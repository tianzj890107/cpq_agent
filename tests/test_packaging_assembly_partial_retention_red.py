import copy
import json
import subprocess
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
from tech_app.backend.services import packaging_assembly as svc, integration
from tech_app.backend.models.integration import IntegrationPlan, IntegrationParamPlan
from tech_app.backend.models.process import ProcessPlan
from tests.test_assembly_connections_compact_conversation_red import DOC


GOOD={'step_no':10,'name':'盖纸与盖板裱合','description':'面纸裱在灰板上','type':'assembly','part_codes':['X1','X2']}


class PartialRetention(unittest.TestCase):
    def generate(self, raw, previous=None, doc=None):
        previous=previous or IntegrationPlan(params=IntegrationParamPlan(interfaces=[]))
        seen=[]
        def run(system,content,schema):
            seen.append(schema)
            if isinstance(raw,Exception):raise raw
            return schema.model_validate(raw)
        with patch.object(svc,'active_inputs',return_value=(copy.deepcopy(doc or DOC),{})),\
             patch.object(integration,'load_plan',return_value=previous),patch.object(integration,'save_plan') as save:
            result=svc.prepare('isolated',{'username':'PE1'},run=run,force=True,automatic=True)
            self.assertEqual(1,len(seen))
            self.assertTrue(save.called)
            return result

    def test_invalid_binding_does_not_discard_valid_operation(self):
        result=self.generate({'steps':[GOOD,dict(GOOD,step_no=20,part_codes=['X2','unknown'])]})
        self.assertEqual([GOOD['name']],[s.name for s in result.process.steps])
        self.assertEqual('partial',svc.recommendation_state(result))
        self.assertTrue(result.packaging_assembly_attempt['issues'])
        svc.validate(result.process,DOC)

    def test_one_malformed_step_does_not_destroy_the_schema_result(self):
        result=self.generate({'steps':[GOOD,{'step_no':20,'name':'坏格式','part_codes':{'not':'list'}}]})
        self.assertEqual(1,len(result.process.steps))
        self.assertTrue(result.packaging_assembly_attempt['issues'])

    def test_invalid_connection_does_not_drop_other_valid_connection(self):
        good={'id':'J1','name':'面纸灰板裱合','method':'裱合','part_codes':['X1','X2'],'evidence':'名称材料对应'}
        result=self.generate({'assembly_connections':[good,dict(good,id='J2',part_codes=['X1','unknown'])],
            'steps':[dict(GOOD,assembly_connection_ids=['J1'])]})
        self.assertEqual(['J1'],[r['id'] for r in result.process.assembly_connections])
        self.assertEqual(1,len(result.process.steps))

    def test_empty_retry_keeps_previous_valid_process_and_explains(self):
        old=self.generate({'steps':[GOOD]})
        before=old.process.model_dump();confirmed=old.process_confirmed
        result=self.generate(RuntimeError('upstream unavailable'),previous=old)
        self.assertEqual(before,result.process.model_dump())
        self.assertEqual(confirmed,result.process_confirmed)
        self.assertTrue(svc.valid(result,DOC,{}))
        attempt=result.packaging_assembly_attempt
        self.assertTrue(attempt['retained_previous'])
        self.assertEqual(0,attempt['generated_step_count'])
        self.assertEqual('partial',svc.recommendation_state(result))
        self.assertIn('模型',json.dumps(attempt,ensure_ascii=False))
        restored=IntegrationPlan.model_validate(result.model_dump())
        self.assertEqual(attempt,restored.packaging_assembly_attempt)

    def test_stale_previous_result_is_not_confirmed_or_relabelled_current(self):
        old=self.generate({'steps':[GOOD]});fingerprint=old.packaging_assembly_input_hash
        changed=copy.deepcopy(DOC);changed['business_parts_hash']='new revision'
        result=self.generate({'summary':'没有可靠连接','steps':[]},previous=old,doc=changed)
        self.assertEqual(1,len(result.process.steps),'历史内容不清空')
        self.assertEqual(fingerprint,result.packaging_assembly_input_hash)
        self.assertFalse(svc.valid(result,changed,{}))
        self.assertFalse(result.process_confirmed)

    def test_first_failure_reports_model_failure_instead_of_success(self):
        result=self.generate(TimeoutError('secret diagnostic not for display'))
        self.assertEqual([],result.process.steps)
        self.assertFalse(result.process_confirmed)
        self.assertEqual('partial',svc.recommendation_state(result))
        text=json.dumps(result.model_dump(),ensure_ascii=False)
        self.assertIn('模型',text);self.assertNotIn('secret diagnostic',text)
        self.assertTrue(result.packaging_assembly_attempt['issues'])

    def test_connection_only_result_is_not_rescued_into_fake_manufacturing(self):
        result=self.generate({'steps':[],'assembly_connections':[{'id':'J','name':'盖纸裱合盖板','method':'裱合',
            'part_codes':['X1','X2'],'evidence':'已有名称与材料支持'}]})
        self.assertEqual(1,len(result.process.steps))
        self.assertEqual('assembly',result.process.steps[0].type.value)

    def test_rejected_manufacturing_row_does_not_change_dependency_identity(self):
        result=self.generate({'steps':[GOOD,dict(GOOD,step_no=20,name='模切'),
            dict(GOOD,step_no=40,name='盖板连接底盒',part_codes=['X2','X3'],depends_on=[10,20])]})
        self.assertEqual([10,20],[s.step_no for s in result.process.steps])
        self.assertEqual([10],result.process.steps[1].depends_on)
        self.assertEqual(30,sum(s.standard_seconds for s in result.process.steps))

    def test_saved_attempt_and_previous_process_survive_real_json_reload(self):
        from tech_app.backend.storage import store,meta_backend
        old=self.generate({'steps':[GOOD]})
        result=self.generate(TimeoutError(),previous=old)
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'_meta',return_value=meta_backend.JsonMetaBackend(Path(folder))),\
             patch.object(store,'_touch_stage'),patch.object(store,'audit'):
            integration.save_plan('isolated',result)
            restored=integration.load_plan('isolated')
        self.assertEqual(result.process.model_dump(),restored.process.model_dump())
        self.assertEqual(result.packaging_assembly_attempt,restored.packaging_assembly_attempt)

    def test_single_part_can_legitimately_need_zero_new_operations(self):
        old=self.generate({'steps':[GOOD]})
        doc=copy.deepcopy(DOC);doc['business_parts']=doc['business_parts'][:1]
        with patch.object(svc,'active_inputs',return_value=(doc,{})),patch.object(integration,'load_plan',return_value=old),patch.object(integration,'save_plan'):
            result=svc.prepare('isolated',{'username':'PE1'},force=True,automatic=True)
        self.assertEqual([],result.process.steps)
        self.assertEqual('succeeded',svc.recommendation_state(result))
        self.assertEqual(GOOD['name'],result.packaging_assembly_history[-1]['process']['steps'][0]['name'])

    def test_manual_save_remains_strict(self):
        invalid=ProcessPlan(steps=[dict(GOOD,part_codes=['X1','unknown'])])
        with patch.object(svc,'active_inputs',return_value=(DOC,{})),patch.object(integration,'load_plan',return_value=IntegrationPlan()),patch.object(integration,'save_plan') as save:
            with self.assertRaises(ValueError):svc.save('isolated',invalid,{'username':'PE1'})
            save.assert_not_called()

    def test_downstream_route_failure_keeps_visible_saved_assembly(self):
        from tech_app.backend.services import packaging_demo_flow as flow, packaging_process_instances, packaging_bom, packaging_route
        from tech_app.backend.storage import store
        plan=self.generate({'steps':[GOOD]})
        with ExitStack() as stack:
            names=stack.enter_context(patch.object(flow,'prepare_names'))
            stack.enter_context(patch.object(flow,'ensure_inputs'))
            stack.enter_context(patch.object(packaging_process_instances,'collect',return_value={'active':[{}],'missing':[]}))
            stack.enter_context(patch.object(svc,'prepare',return_value=plan))
            stack.enter_context(patch.object(svc,'active_inputs',return_value=(DOC,{})))
            stack.enter_context(patch.object(store,'load_requirement',return_value={'requirement_no':'REQ'}))
            stack.enter_context(patch.object(packaging_bom,'build_bom'))
            stack.enter_context(patch.object(packaging_route,'build_route',side_effect=ValueError('route unavailable')))
            confirm=stack.enter_context(patch.object(packaging_route,'confirm_route'))
            saved=stack.enter_context(patch.object(integration,'save_plan'))
            result=flow.prepare_route('isolated',{'username':'PE1'},prepare_associations=False)
        self.assertEqual(1,len(result.process.steps))
        self.assertEqual('partial',svc.recommendation_state(result))
        self.assertEqual('项目路线',result.packaging_assembly_attempt['issues'][-1]['stage'])
        self.assertTrue(saved.called);names.assert_not_called();confirm.assert_not_called()

    def test_failed_task_rereads_saved_results_without_claiming_success(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        path=Path(__file__).resolve().parents[1]/'tech_app/frontend/assembly-integration.js'
        script="""let aiBusy=false,aiPid='P',aiIndustry='packaging',reads=0,renders=0;
          const AI_TAB_NAMES={},AI_TABS={},aiBlocker=()=>'',aiRenderActions=()=>{},aiStatus=()=>{},
          aiUserSay=()=>{},aiProcessCard=()=>({done:()=>{},log:()=>{}}),aiPublishTask=()=>{},aiUrl=x=>x,
          api=async()=>({task_id:'T'}),aiPollTask=async()=>{throw Error('route failed')},
          aiRefreshPackagingWorkspace=async()=>{reads++;return {assembly_steps:[{}]}},aiRender=()=>renders++,
          aiTaskInterrupted=()=>false,aiToast=()=>{},aiShowFailure=()=>{};"""
        script+=js_function(path,'aiPost')
        script+="(async()=>{const result=await aiPost('process');if(result.ok||reads!==1||renders!==1||aiBusy)throw Error('saved result lost after task failure')})().catch(e=>{console.error(e);process.exitCode=1});"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_ui_displays_attempt_and_escapes_issues(self):
        source=(Path(__file__).resolve().parents[1]/'tech_app/frontend/packaging-stage-workspace.js').read_text()
        script="global.window=global;"+source+"\nconst html=PackagingStageWorkspace.render('process',{parts:[],assembly_steps:[],status:{},assembly_attempt:{status:'partial',retained_previous:true,generated_step_count:0,issues:[{reason:'<script>bad</script>'}]}});if(!html.includes('上次')||!html.includes('&lt;script&gt;')||html.includes('<script>bad'))throw Error('missing or unsafe failure disclosure');"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_browser_shows_previous_steps_and_latest_zero_attempt_together(self):
        chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        from tests._chrome_page_probe import page_probe
        root=Path(__file__).resolve().parents[1]/'tech_app/frontend'
        board={'parts':[{'code':'X1','name':'盖面纸'},{'code':'X2','name':'盖灰板'}],
            'assembly_steps':[GOOD],'status':{},'assembly_result_state':'partial',
            'assembly_attempt':{'status':'partial','retained_previous':True,'generated_step_count':0,
                'issues':[{'stage':'模型','index':1,'reason':'<img src=x>模型超时'}]}}
        script=(root/'packaging-stage-workspace.js').read_text()+"\nconst board="+json.dumps(board)+";document.getElementById('stage').innerHTML=PackagingStageWorkspace.render('process',board);document.getElementById('probe').textContent=JSON.stringify({oldVisible:document.body.textContent.includes('盖纸与盖板裱合'),retention:document.body.textContent.includes('上次'),zero:document.body.textContent.includes('本次有效工序 0'),safe:!document.querySelector('img'),sharedButton:!!document.querySelector('button.inline-action.save')});"
        with tempfile.TemporaryDirectory() as folder:
            page=Path(folder)/'assembly.html'
            css=(root/'workbench.css').read_text()+(root/'inline-analysis.css').read_text()
            page.write_text('<!doctype html><meta charset="utf-8"><style>'+css+'</style><main id="stage"></main><pre id="probe" hidden></pre><script>'+script+'</script>')
            result=json.loads(page_probe(chrome,page,Path(folder)/'profile',"document.getElementById('probe')?.textContent"))
        self.assertTrue(all(result.values()),result)
