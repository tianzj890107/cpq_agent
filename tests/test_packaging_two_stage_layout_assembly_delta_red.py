import copy
import importlib
import json
import pathlib
import subprocess
import tempfile
import re
import os
import shutil
import unittest
from unittest.mock import patch
from tech_app.backend.models.integration import IntegrationPlan
from tech_app.backend.models.process import ProcessPlan, ProcessStep
from tech_app.backend.services import packaging_workspace, workflow_stages, workflow_projection
from tech_app.backend.services import packaging_demo_flow, packaging_parts, integration
from tech_app.backend.storage import store

ROOT=pathlib.Path(__file__).resolve().parents[1]


def doc():
    return {'business_parts_hash':'H','business_parts_id':'V','business_parts':[
        {'business_part_code':'A','name':'灰板','reference':{'length_mm':100,'width_mm':60,'material_text':'灰板','quantity':1}},
        {'business_part_code':'B','name':'面纸','reference':{'length_mm':120,'width_mm':70,'material_text':'225g面纸','quantity':1}}],
        'layout_rows':[]}


def records():
    return {code:{'part_code':code,'process_scope':'business_part/1','plan':{'part_class':'packaging',
        'steps':[{'step_no':10,'name':'模切','standard_seconds':30}]}} for code in ('A','B')}


def assembly():
    return ProcessPlan(part_class='packaging',steps=[{'step_no':10,'name':'面纸与灰板裱合',
        'type':'assembly','description':'将面纸与灰板组合','part_codes':['A','B'],
        'standard_seconds':None,'duration_min':None}])


class TwoStageTests(unittest.TestCase):
    def test_actual_frontend_binding_save_preserves_other_operations_and_messages_order(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        script=r'''
const events=[];
const original={steps:[{step_no:10,name:'裱合',part_codes:['A','B']},{step_no:20,name:'装盒',part_codes:['A','C']}]};
const aiPlan=()=>({process:original});
const aiBusinessParts={workspace:{assembly_steps:original.steps}};
const group={querySelectorAll:()=>['B','C'].map(code=>({dataset:{pkgAssemblyPart:code}}))};
const button={dataset:{pkgSaveAssembly:'0'},closest:()=>group};
const document={querySelectorAll:selector=>selector==='[data-pkg-save-assembly]'?[button]:[]};
const $ai=()=>null;
let aiData;
const aiUserSay=()=>events.push('user');
const aiSay=()=>events.push('agent');
const aiToast=message=>{throw Error(message)};
const aiUrl=path=>'/integration'+path;
const api=async(url,options)=>{events.push('save');globalThis.sent=JSON.parse(options.body);return {}};
const aiRefreshPackagingWorkspace=async()=>events.push('refresh');
const aiRender=()=>events.push('render');
'''+js_function(ROOT/'tech_app/frontend/assembly-integration.js','aiBindBody')+r'''
aiBindBody();
button.onclick().then(()=>console.log(JSON.stringify({events,sent:globalThis.sent,original})));
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        value=json.loads(result.stdout)
        self.assertEqual(['user','save','refresh','render','agent'],value['events'])
        self.assertEqual(['B','C'],value['sent']['steps'][0]['part_codes'])
        self.assertEqual(['A','C'],value['sent']['steps'][1]['part_codes'])
        self.assertEqual(['A','B'],value['original']['steps'][0]['part_codes'])

    def test_saved_delta_bindings_reach_route_and_formula_cost_and_edit_invalidates_cost(self):
        from tests.test_packaging_demo_flow_end_to_end_red import DemoFlow,PID,REQ_NO,REQ,GEOMETRY,BUSINESS
        from tech_app.backend.services import packaging_demo_flow, packaging_route, packaging_cost, packaging_cost_ledger
        from tech_app.backend.services import da_process_routing
        from tech_app.backend.storage import da_repo
        fixture=DemoFlow('test_a5_native_per_part_processes_reach_material_and_labor_ledger')
        fixture.setUp()
        try:
            store.save_requirement(PID,{'project_id':PID,'requirement_no':REQ_NO,'data':dict(REQ)})
            packaging_parts.save_parts(PID,copy.deepcopy(GEOMETRY))
            packaging_parts.save_business_parts(PID,copy.deepcopy(BUSINESS))
            ids=[r['business_part_code'] for r in BUSINESS['business_parts']]
            lookup={'status':'matched','match_method':'exact_name','routes':[{'steps':[
                {'line_number':10,'operation_name':'模切'},{'line_number':20,'operation_name':'检验'}]}]}
            calls=[]
            def model(*args):
                calls.append(args)
                return ProcessPlan(part_class='packaging',steps=[
                    {'step_no':10,'name':'面纸与灰板裱合','type':'assembly','description':'新增跨件组合','part_codes':ids},
                    {'step_no':20,'name':'组合成盒检查','type':'inspection','description':'检查组合结果','part_codes':ids}])
            actor={'username':'PE1','role':'process_manager'}
            with patch.object(da_process_routing,'for_row',return_value=lookup):
                result=packaging_demo_flow.run(PID,actor=actor,run_model=model)
            self.assertTrue(result['ready'])
            self.assertEqual(1,len(calls))
            saved=integration.load_plan(PID)
            self.assertEqual(2,len(saved.process.steps),'组装页不复制4道零件制造工序')
            steps=da_repo.load_packaging_route_steps(PID,REQ_NO)
            self.assertEqual(6,len(steps),'完整路线保留4道制造+2道新增组装')
            additional=[s for s in steps if s.get('scope')=='project']
            self.assertEqual([ids,ids],[s['part_codes'] for s in additional])
            lines=[r for r in result['cost_projection']['packaging_cost']['items'] if r.get('part_code')=='ASSY']
            self.assertEqual(2,len(lines),'新增组装不乘绑定零件个数')
            for line in lines:
                inputs=json.loads(line['inputs_json'])
                self.assertEqual(ids,inputs['part_codes'])
                self.assertEqual(1,inputs['usage_qty'])
            changed=saved.process.model_copy(deep=True)
            changed.steps[0].name='灰板与面纸粘合'
            self.service().save(PID,changed,actor)
            self.assertTrue(packaging_cost.load_cost(PID,REQ_NO)['stale'])
            with self.assertRaises(packaging_cost.CostError):packaging_cost.compute_project(PID,REQ_NO)
            with self.assertRaises(packaging_route.RouteError):packaging_route.confirm_route(PID,REQ_NO,actor=actor)
        finally:
            fixture.tearDown();fixture.doCleanups()

    def test_card_stage_and_projection_use_two_stage_packaging_numbering(self):
        from tech_app.backend.services import home_card
        stage=home_card._stage_block('3.2','packaging')
        self.assertEqual('组装工艺',stage['title'])
        self.assertEqual(12,stage['total'])
        action=home_card._primary_action('P','3.2',{},'packaging')
        self.assertIn('view=process',action['target'])
        facts={'requirement':{'data':{'industry':'packaging'}},'errors':{}}
        with patch.object(workflow_projection,'_judge',return_value={'completed':True,'status':'confirmed'}):
            rows=workflow_projection._rows_for('P',facts,'process_manager')
        self.assertEqual(['3.1','3.2'],[r['key'] for r in rows if r['phase_no']==3])
    def test_compact_selector_in_real_chrome_at_narrow_workbench_width(self):
        chrome=pathlib.Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('本机Chrome不可用，其他渲染测试仍执行')
        from tests.test_packaging_whole_part_process_unit_red import js_function
        names=[{'business_part_code':'P'+str(i),'name':'内外圈围边面纸及底板组成'+str(i),'scheme_id':'1'} for i in range(26)]
        payload={'business_parts':names,'layout_rows':[{'entity_id':'L','scheme_id':'1','text':'围边面纸共排',
            'part_codes':['P0','P1'],'sheet_mm':[680,460],'part_counts':{'P0':2,'P1':2},'n_up':2}]}
        script='const esc=v=>String(v??"").replace(/[&<>\"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;"}[c]));\n'
        script+='const aiBusinessParts='+json.dumps(payload,ensure_ascii=False)+';\n'
        source=(ROOT/'tech_app/frontend/assembly-integration.js').read_text()
        script+=re.search(r'const aiAttr = [^\n]+',source).group(0)+'\n'
        script+=js_function(ROOT/'tech_app/frontend/assembly-integration.js','aiRenderPackagingLayout')+'\n'
        script+=r'''
document.getElementById('fixture').innerHTML=aiRenderPackagingLayout();
document.querySelector('.ai-layout-association-bar details').open=true;
setTimeout(()=>{
 const checkboxes=[...document.querySelectorAll('input[type=checkbox]')].map(n=>n.getBoundingClientRect().width);
 const numbers=[...document.querySelectorAll('input[type=number]')].map(n=>n.getBoundingClientRect().width);
 const fixture=document.getElementById('fixture');
 const overflow=fixture.scrollWidth>fixture.clientWidth;
 document.getElementById('probe').textContent=JSON.stringify({checkboxes,numbers,overflow,fixtureWidth:fixture.getBoundingClientRect().width});
},100);
'''
        css='\n'.join((ROOT/'tech_app/frontend'/f).read_text() for f in ('workbench.css','inline-analysis.css','assembly-integration.css'))
        with tempfile.TemporaryDirectory() as directory:
            page=pathlib.Path(directory)/'layout.html'
            for width in (360,640):
                page.write_text('<!doctype html><meta charset="utf-8"><style>'+css+
                    '\nbody{margin:0;padding:12px;box-sizing:border-box;height:auto;overflow:auto}#fixture{width:'+str(width-24)+'px;min-width:0}</style>'+
                    '<body data-industry="packaging"><main id="fixture"></main><pre id="probe" hidden></pre><script>'+script+'</script></body>')
                image_path=pathlib.Path(directory)/('layout-'+str(width)+'.png')
                result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run',
                    '--disable-background-networking','--hide-scrollbars','--user-data-dir='+str(pathlib.Path(directory)/('profile-'+str(width))),
                    '--window-size='+str(width)+',1000','--virtual-time-budget=1000','--dump-dom',
                    '--screenshot='+str(image_path),page.as_uri()],capture_output=True,text=True,timeout=30)
                self.assertEqual(0,result.returncode,result.stderr[-600:])
                match=re.search(r'<pre id="probe" hidden="">(.*?)</pre>',result.stdout,re.S)
                self.assertIsNotNone(match,result.stdout[-1000:])
                metrics=json.loads(match.group(1))
                self.assertEqual(width-24,metrics['fixtureWidth'],metrics)
                self.assertFalse(metrics['overflow'],metrics)
                self.assertTrue(metrics['checkboxes'])
                self.assertTrue(all(12<=w<=16 for w in metrics['checkboxes']),metrics)
                self.assertTrue(all(60<=w<=120 for w in metrics['numbers']),metrics)
                if os.environ.get('CPQ_UI_PROBE_DIR'):
                    target=pathlib.Path(os.environ['CPQ_UI_PROBE_DIR'])/image_path.name
                    shutil.copyfile(image_path,target)
                    print('排模UI截图：',target,flush=True)
                print('排模UI浏览器验证：宽度',width,'复选框',metrics['checkboxes'][0],'数字输入',metrics['numbers'][0],flush=True)
    def service(self):
        try:return importlib.import_module('tech_app.backend.services.packaging_assembly')
        except ModuleNotFoundError:self.fail('缺少新增组装方案服务')

    def test_packaging_stage_table_has_two_steps_and_stable_views(self):
        self.assertTrue(hasattr(workflow_stages,'for_industry'))
        rows=workflow_stages.for_industry('packaging')
        self.assertEqual([('3.1','排版排模','params'),('3.2','组装工艺','process')],
            [(r['sub'],r['sub_title'],r['view']) for r in rows if r['phase']=='3'])
        self.assertEqual(3,len([r for r in workflow_stages.for_industry('battery') if r['phase']=='3']))

    def test_models_preserve_bound_parts_and_billing_parameters(self):
        value=assembly().model_dump()['steps'][0]
        self.assertEqual(['A','B'],value['part_codes'])
        step=ProcessStep(step_no=10,name='裱合',type='assembly',description='新增装配',
            part_codes=['A','B'],scope='project',cost_parameters={'labor_seconds':30})
        self.assertEqual(30,step.model_dump()['cost_parameters']['labor_seconds'])

    def test_workspace_renders_only_added_assembly_not_manufacturing(self):
        board=packaging_workspace.build('P',doc(),{},IntegrationPlan(process=assembly()),records())
        path=ROOT/'tech_app/frontend/packaging-stage-workspace.js'
        script='const window={};'+path.read_text()+'\nconsole.log(window.PackagingStageWorkspace.render("process",'+json.dumps(board)+'));'
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=15)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertNotIn('模切',result.stdout)
        self.assertIn('面纸与灰板裱合',result.stdout)
        self.assertIn('灰板',result.stdout)
        self.assertIn('面纸',result.stdout)
        self.assertIn('data-pkg-assembly-part',result.stdout)

    def test_sync_integration_never_copies_whole_route(self):
        plan=IntegrationPlan(process=assembly())
        with patch.object(integration,'load_plan',return_value=plan),patch.object(integration,'save_plan'), \
             patch.object(store,'load_requirement',return_value={'data':{'industry':'packaging'}}), \
             patch.object(store,'audit'),patch.object(packaging_parts,'load_business_parts',return_value=doc()):
            result=packaging_demo_flow.sync_integration('P',{'username':'PE1'},
                route={'steps':[{'step_no':i*10,'step_name':'模切','standard_seconds':30} for i in range(1,101)]})
        self.assertEqual(['面纸与灰板裱合'],[s.name for s in result.process.steps])

    def test_generation_one_call_parent_bindings_reuse_and_invalid_ids_rejected(self):
        service=self.service();plan=IntegrationPlan();calls=[]
        def run(*args):calls.append(args);return assembly()
        with patch.object(integration,'load_plan',return_value=plan),patch.object(integration,'save_plan'), \
             patch.object(packaging_parts,'load_business_parts',return_value=doc()), \
             patch.object(packaging_parts,'load_part_process',side_effect=lambda p,c:records()[c]):
            service.prepare('P',{'username':'PE1'},run=run)
            service.prepare('P',{'username':'PE1'},run=run)
        self.assertEqual(1,len(calls))
        self.assertEqual('assembly_delta/1',plan.packaging_assembly_scope)
        self.assertEqual(['A','B'],plan.process.steps[0].part_codes)
        for ids in (['A'],['A','missing'],['A','A'],['A','A::portion']):
            bad=assembly();bad.steps[0].part_codes=ids
            with self.assertRaises(ValueError):service.validate(bad,doc())
        bad=assembly();bad.steps[0].name='模切'
        with self.assertRaises(ValueError):service.validate(bad,doc())

    def test_project_route_appends_delta_once_with_usage_one_and_persistent_bindings(self):
        service=self.service();plan=IntegrationPlan()
        with patch.object(integration,'load_plan',return_value=plan),patch.object(integration,'save_plan'), \
             patch.object(packaging_parts,'load_business_parts',return_value=doc()), \
             patch.object(packaging_parts,'load_part_process',side_effect=lambda p,c:records()[c]):
            service.prepare('P',{'username':'PE1'},run=lambda *a:assembly())
            manufacturing=[{'step_no':10,'step_name':'模切','part_code':'A'},
                           {'step_no':20,'step_name':'模切','part_code':'B'}]
            steps=service.append_operations('P',manufacturing)
        self.assertEqual(3,len(steps))
        self.assertEqual('ASSY',steps[-1]['part_code'])
        self.assertEqual(['A','B'],steps[-1]['part_codes'])
        self.assertEqual(1,steps[-1]['usage_qty'])
        self.assertEqual(2,len(manufacturing),'不改写输入制造工艺')

    def test_workspace_and_projection_read_real_part_records_for_completion(self):
        service=self.service();plan=IntegrationPlan()
        with patch.object(integration,'load_plan',return_value=plan),patch.object(integration,'save_plan'), \
             patch.object(packaging_parts,'load_business_parts',return_value=doc()), \
             patch.object(packaging_parts,'load_part_process',side_effect=lambda p,c:records()[c]):
            service.prepare('P',{'username':'PE1'},run=lambda *a:assembly())
            plan.process_confirmed=True
            facts={'plan':plan,'requirement':{'data':{'industry':'packaging'}},'packaging_business_parts':doc()}
            state=workflow_projection._judge('3.2','P',facts)
        self.assertTrue(state['completed'],state)

    def test_frontend_two_tabs_and_compact_layout_controls(self):
        shell=(ROOT/'tech_app/frontend/tech-workbench.js').read_text()
        start=shell.index('const PACKAGING_PROCESS_TABS');end=shell.index('const PACKAGING_COST_TABS',start)
        self.assertEqual(2,shell[start:end].count("stage: 'process'"))
        self.assertNotIn('方案与部件',shell[start:end])
        css=(ROOT/'tech_app/frontend/assembly-integration.css').read_text()
        self.assertIn('.ai-layout-size-grid',css)
        self.assertIn('input[type="checkbox"]',css)
        source=(ROOT/'tech_app/frontend/assembly-integration.js').read_text()
        self.assertIn('data-pkg-save-assembly',source)
