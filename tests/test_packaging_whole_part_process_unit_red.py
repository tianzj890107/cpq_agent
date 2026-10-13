"""Whole business part, not CAD portion, is the manufacturing analysis unit."""
import ast
import asyncio
import copy
import pathlib
import json
import subprocess
import unittest
from unittest.mock import patch

from tech_app.backend.models.integration import IntegrationPlan
from tech_app.backend.services import packaging_parts as parts, packaging_demo_inputs as demo
from tech_app.backend.services import packaging_layout, packaging_process_instances as instances
from tech_app.backend.services import packaging_workspace, da_process_routing, tasks

ROOT = pathlib.Path(__file__).resolve().parents[1]


def js_function(path, name):
    source=path.read_text();start=source.index('function '+name+'(')
    if source[max(0,start-6):start]=='async ':start-=6
    brace=source.index('{',start);depth=0
    for index in range(brace,len(source)):
        if source[index]=='{':depth+=1
        elif source[index]=='}':
            depth-=1
            if not depth:return source[start:index+1]
    raise AssertionError('函数未闭合 '+name)


def row():
    return {'business_part_code':'P','name':'地盒内外圈围边面纸',
            'reference':{'material_text':'225g面纸','quantity':2},
            'poc_inputs':{'length_mm':100,'width_mm':60},'sections_source':'manual',
            'sections_complete':True,'geometry_binding':{'status':'bound','entity_ids':['e1','e2']},'sections':[
                {'section_id':'IN','name':'内圈','entity_ids':['e1'],'component_ids':['c1'],
                 'quantity':1,'status':'bound','material_text':'225g面纸','confirmed_size':{'length_mm':100,'width_mm':60,'source':'verified_cad_dimension','entity_ids':['e1']}},
                {'section_id':'OUT','name':'外圈','entity_ids':['e2'],'component_ids':['c2'],
                 'quantity':1,'status':'bound','material_text':'150g衬纸','confirmed_size':{'length_mm':120,'width_mm':65,'source':'verified_cad_dimension','entity_ids':['e2']}}]}


def plan():
    return {'part_class':'packaging','steps':[{'step_no':10,'name':'模切','depends_on':[],
            'standard_seconds':30,'duration_min':.5,'cost_parameters':{'imposition_count':1}},
            {'step_no':20,'name':'模切','depends_on':[10],'standard_seconds':20,'duration_min':1/3}]}


class WholePartTests(unittest.TestCase):
    def test_inline_panel_renders_scope_notice_when_current_plan_is_empty(self):
        source='const esc=v=>String(v??""),attr=esc;const body={innerHTML:""};\n'
        source+=js_function(ROOT/'tech_app/frontend/app.js','packagingPartBusinessIdentityNote')+'\n'
        for name in ('businessIdentityRow','renderProcess'):
            source+=js_function(ROOT/'tech_app/frontend/inline-analysis.js',name)+'\n'
        source+='renderProcess({root:{querySelector:()=>body},plan:null,businessNote:packagingPartBusinessIdentityNote({process_scope_changed:true})});console.log(body.innerHTML);'
        result=subprocess.run(['node','-e',source],capture_output=True,text=True,timeout=15)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertIn('重新生成整件工艺',result.stdout)
        self.assertIn('data-inline-business-note="stale"',result.stdout)

    def test_regeneration_clears_old_scope_notice_in_same_panel(self):
        source=r'''
const state={mode:'process',busy:false,context:{projectId:'P',part:{part_id:'P',name:'面纸'}},
  businessNote:{text:'旧工艺按组成分别生成',level:'stale'}};
const active=state,renderControls=()=>{},render=()=>{},setStatus=()=>{};
const endpointBase=()=>'/process';const jsonFetch=async()=>({task_id:'task'});
const poll=async()=>({process_scope:'business_part/1',plan:{steps:[{name:'模切'}]},sections:[]});
const FormData=class{},CustomEvent=class{constructor(name,args){}};
const window={dispatchEvent:()=>{},PackagingConclusionNotes:{basis:()=>({}),
  businessIdentity:payload=>packagingPartBusinessIdentityNote(payload)}};
'''
        source+=js_function(ROOT/'tech_app/frontend/app.js','packagingPartBusinessIdentityNote')+'\n'
        source+=js_function(ROOT/'tech_app/frontend/inline-analysis.js','generate')+'\n'
        source+='generate(state).then(()=>console.log(JSON.stringify(state.businessNote)));'
        result=subprocess.run(['node','-e',source],capture_output=True,text=True,timeout=15)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertNotEqual('stale',json.loads(result.stdout)['level'], '生成整件工艺后不能留着旧组成工艺警告')

    def test_legacy_read_hides_split_current_plan_but_preserves_history(self):
        from fastapi import Depends
        tree=ast.parse((ROOT/'tech_app/backend/main.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='get_packaging_business_part_process')
        fn.decorator_list=[]
        legacy={'part_code':'P','plan':plan(),'sections':[{'section_id':'IN','plan':plan()}]}
        namespace={'__name__':'tech_app.backend.main','__package__':'tech_app.backend',
                   'Depends':Depends,'current_user':lambda:None,'_workflow_project':lambda p:None,
                   '_packaging_business_part_row':lambda *a:{'doc':{},'row':row()},
                   'packaging_parts':parts,'_packaging_part_conclusion_version':lambda *a:{'business_stale':False}}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'whole-part-read','exec'),namespace)
        before=copy.deepcopy(legacy)
        with patch.object(parts,'load_part_process',return_value=legacy):
            value=namespace['get_packaging_business_part_process']('isolated','P',user={})
        self.assertIsNone(value['plan'])
        self.assertEqual([],value['sections'])
        self.assertTrue(value['process_scope_changed'])
        self.assertTrue(value['business_stale'])
        self.assertEqual(before['plan'],value['historical_plan'])
        self.assertEqual(before['sections'],value['historical_sections'])
        self.assertEqual(before,legacy)

    def test_inputs_preserve_parent_identity_and_all_portion_evidence(self):
        source=row();before=copy.deepcopy(source)
        value=parts.business_process_inputs(source)
        self.assertTrue(value['ok'])
        self.assertFalse(value.get('sections'), '组成不能成为独立工艺任务列表')
        self.assertEqual('P',value['part_code'])
        self.assertEqual(source['name'],value['name'])
        self.assertEqual(['IN','OUT'],[s['section_id'] for s in value['composition']])
        for token in ('内圈','外圈','100','120','225g面纸','150g衬纸'):
            self.assertIn(token,value['grounding'])
        self.assertIsNone(value['size_length'], '不能把多部分并集当整件展开尺寸')
        self.assertEqual(before,source)

    def test_batch_generates_one_parent_lookup_and_one_plan(self):
        source=row();doc={'poc_demo':True,'business_parts_hash':'H','business_parts_id':'V','business_parts':[source]}
        calls=[];saved=[]
        def generate(part,lookup,inputs,**kw):
            calls.append((part.part_id,part.name,inputs,kw))
            return plan(),{'summary':{'missing':2,'reused':0}}
        with patch.object(parts,'load_business_parts',return_value=doc), \
             patch.object(parts,'verified_business_part_input_row',side_effect=lambda r,d:r), \
             patch.object(parts,'load_part_process',return_value={}), \
             patch.object(parts,'save_part_process',side_effect=lambda p,r:saved.append(r)), \
             patch.object(instances,'collect',return_value={'records':[],'missing':['P'],'active':False}), \
             patch.object(da_process_routing,'for_row',return_value={}) as lookup, \
             patch.object(demo,'demo_process_plan',side_effect=generate),patch.object(tasks,'report_progress'):
            result=demo.prepare_processes('isolated')
        self.assertTrue(result['ready'])
        self.assertEqual([('P',source['name'])],[(c[0],c[1]) for c in calls])
        self.assertEqual(source['name'],lookup.call_args.args[0]['name'])
        self.assertEqual(1,lookup.call_count)
        self.assertEqual('business_part/1',saved[0]['process_scope'])
        self.assertFalse(saved[0]['sections'])
        self.assertEqual(2,len(saved[0]['plan']['steps']), '同名真实工序不去重')

    def test_parent_result_does_not_require_child_process_records(self):
        source=row();record={'part_code':'P','process_scope':'business_part/1','plan':plan(),'sections':[]}
        with patch.object(parts,'load_business_parts',return_value={'business_parts':[source]}), \
             patch.object(parts,'load_part_process',return_value=record):
            result=instances.collect('isolated')
        self.assertEqual([],result['missing'])
        self.assertEqual(['P'],[r['part_code'] for r in result['records']])
        self.assertEqual(2,result['records'][0]['usage_qty'])

    def test_legacy_section_plans_are_not_current_even_if_all_complete(self):
        source=row();legacy={'part_code':'P','plan':plan(),'sections':[
            {'section_id':s['section_id'],'plan':plan()} for s in source['sections']]}
        with patch.object(parts,'load_business_parts',return_value={'business_parts':[source]}), \
             patch.object(parts,'load_part_process',return_value=legacy):
            result=instances.collect('isolated')
        self.assertTrue(result['active'], '保留旧结果，不能悄悄降级为模板路线')
        self.assertEqual(['P'],result['missing'])
        self.assertEqual([],result['records'])
        with self.assertRaisesRegex(ValueError,'process_scope_changed'):
            instances.expand([legacy])

    def test_workspace_shows_one_whole_part_plan_and_keeps_material_portions(self):
        source=row();source['reference'].update(length_mm=100,width_mm=60)
        record={'process_scope':'business_part/1','plan':plan(),'sections':[]}
        board=packaging_workspace.build('isolated',{'business_parts':[source]}, {},IntegrationPlan(),{'P':record})
        self.assertEqual(1,len(board['part_processes']))
        self.assertFalse(board['part_processes'][0].get('section_name'))
        self.assertEqual(2,len(board['individual_cuts']))
        self.assertEqual(['P::IN','P::OUT'],[p['code'] for p in board['layout_parts']])
        self.assertEqual(1,board['process_ready'])
        legacy={**record,'sections':[{'section_id':'IN','section_name':'内圈','plan':plan()},
                                   {'section_id':'OUT','section_name':'外圈','plan':plan()}]}
        board=packaging_workspace.build('isolated',{'business_parts':[source]}, {},IntegrationPlan(),{'P':legacy})
        self.assertEqual(1,len(board['part_processes']))
        self.assertEqual(1,board['process_missing'])
        self.assertTrue(board['part_processes'][0]['stale'])
        self.assertFalse(board['part_processes'][0]['plan'].get('steps'))
        script='const window={};'+(ROOT/'tech_app/frontend/packaging-stage-workspace.js').read_text()
        script+='\nconsole.log(window.PackagingStageWorkspace.render("process",'+json.dumps(board)+'));'
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=15)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual(0,result.stdout.count('<summary>'), '组装页不重复展示零件制造工艺')
        self.assertIn('零件制造工艺待补齐',result.stdout)
        self.assertNotIn('内圈</summary>',result.stdout)

    def test_route_and_formula_cost_charge_whole_part_once_not_each_portion(self):
        from tests.test_packaging_unified_route_cost_red import UnifiedIntegration
        from tests.test_packaging_cost_engine_red import PID,REQ_NO
        from tech_app.backend.services import packaging_route, packaging_cost
        fixture=UnifiedIntegration('test_roundtrip_confirm_cost_three_operations')
        fixture.setUp()
        try:
            fixture.prepare()
            record={'part_code':'P','process_scope':'business_part/1','sections':[],
                    'usage_qty':2,'plan':plan(),
                    'cost_parameters':{'machine_length':300,'machine_width':200,'imposition_count':1}}
            collection={'records':[record],'missing':[],'active':True,'hash':instances.fingerprint([record])}
            with patch.object(instances,'collect',return_value=collection):
                built=packaging_route.build_route(PID,REQ_NO)
                self.assertEqual(2,len(built['steps']), '一套整件方案的两道实际工序，不按组成复制')
                self.assertEqual(['P','P'],[s['part_code'] for s in built['steps']])
                self.assertEqual(['',''],[s['section_id'] for s in built['steps']])
                packaging_route.confirm_route(PID,REQ_NO,actor=fixture.actor())
                cost=packaging_cost.compute_project(PID,REQ_NO)
            lines=[line for line in cost['items'] if line['formula_code']=='PKG-C-DIE-CUT']
            self.assertEqual(2,len(lines))
            self.assertTrue(all(line['amount'] is not None for line in lines))
            self.assertEqual([2,2],[json.loads(line['inputs_json'])['usage_qty'] for line in lines])
        finally:
            fixture.tearDown()
            fixture.doCleanups()

    def test_real_single_part_handler_uses_one_parent_recommendation(self):
        from fastapi import Depends, Form, File, UploadFile, HTTPException
        from tech_app.backend.services import packaging_demo_flow, process, packaging_match
        from tech_app.backend.models.process import ProcessPlan
        tree=ast.parse((ROOT/'tech_app/backend/main.py').read_text())
        names={'_packaging_business_part_process_note','packaging_business_part_process'}
        functions=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        for fn in functions:fn.decorator_list=[]
        source=row();doc={'business_parts':[source],'business_parts_id':'V','business_parts_hash':'H'}
        namespace={'__name__':'tech_app.backend.main','__package__':'tech_app.backend','Dict':dict,'Any':object,
            'List':list,'UploadFile':UploadFile,'Form':Form,'File':File,'Depends':Depends,
            'current_user':lambda:None,'HTTPException':HTTPException,'packaging_parts':parts,
            'packaging_match':packaging_match,'process':process,'tasks':tasks,
            '_require':lambda *a:None,'_workflow_project':lambda p:None,
            '_packaging_business_part_row':lambda *a:{'row':source,'doc':doc},
            '_packaging_business_geometry_label':lambda r:'business','_digest_value':lambda r:'hash',
            '_task_key':lambda *a:'key','now_cst_str':lambda:'2026-10-13',
            '_packaging_business_part_reject':lambda value:AssertionError(value)}
        async def attachments(items):return []
        namespace['_read_attachments']=attachments
        exec(compile(ast.Module(body=functions,type_ignores=[]),'whole-part-handler','exec'),namespace)
        captured=[];jobs=[]
        def recommend(part,**kw):
            captured.append((part.part_id,part.name,kw))
            return ProcessPlan(part_id='P',part_class='packaging',steps=plan()['steps']),{'summary':{}}
        with patch.object(packaging_demo_flow,'ensure_inputs',return_value=False), \
             patch.object(parts,'verified_business_part_input_row',side_effect=lambda r,d:r), \
             patch.object(tasks,'submit',side_effect=lambda p,k,job,**kw:jobs.append(job) or 'task'), \
             patch.object(tasks,'report_progress'),patch.object(tasks,'current_task_id',return_value='task'), \
             patch.object(da_process_routing,'for_row',return_value={}) as lookup, \
             patch.object(process,'outline_process',side_effect=recommend), \
             patch.object(parts,'save_part_process') as saved:
            asyncio.run(namespace['packaging_business_part_process']('isolated','P',note='',attachments=[],user={'username':'PE1'}))
            result=jobs[0]()
        self.assertEqual([('P',source['name'])],[(p,n) for p,n,k in captured])
        self.assertEqual(1,lookup.call_count)
        self.assertFalse(result.get('sections'))
        self.assertEqual('business_part/1',saved.call_args.args[1]['process_scope'])
        for token in ('内圈','外圈','150g衬纸'):
            self.assertIn(token,captured[0][2]['note'])
