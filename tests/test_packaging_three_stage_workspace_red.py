import copy
import ast
import importlib
import json
import pathlib
import subprocess
import unittest
from unittest.mock import patch
from tech_app.backend.models.integration import IntegrationPlan
from tech_app.backend.services import workflow_projection
from tech_app.backend.services import packaging_parts, integration, packaging_cost, packaging_sheet_cost
from tech_app.backend.storage import store

ROOT=pathlib.Path(__file__).resolve().parents[1]


class PackagingWorkspaceTests(unittest.TestCase):
    def service(self):
        try: return importlib.import_module('tech_app.backend.services.packaging_workspace')
        except ModuleNotFoundError: self.fail('缺少包装三步骤纯读投影')

    def doc(self):
        return {'business_parts_hash':'H','business_parts_id':'V','scheme_labels':['1','2'],
            'selected_scheme_id':'1','business_parts':[
            {'business_part_code':'B2','display_code':'02','name':'面纸','scheme_id':'1',
             'reference':{'material_text':'225g面纸','quantity':2,'length_mm':100,'width_mm':60},
             'poc_inputs':{'length_mm':100,'width_mm':60,'material_code':'M'}},
            {'business_part_code':'B1','display_code':'01','name':'另一方案','scheme_id':'2'}],
            'layout_rows':[]}

    def build(self, doc=None, plan=None, records=None):
        return self.service().build('P',doc or self.doc(),{'industry':'packaging'},
                                    plan or IntegrationPlan(),records or {})

    def test_scope_order_dimensions_usage_and_no_cost_reads(self):
        doc=self.doc();original=copy.deepcopy(doc)
        board=self.build(doc)
        self.assertEqual(['B2'],[r['code'] for r in board['parts']])
        self.assertEqual([100,60],board['parts'][0]['size_mm'])
        self.assertEqual(2,board['parts'][0]['quantity'])
        self.assertEqual([100,65],board['individual_cuts'][0]['sheet_mm'])
        self.assertEqual(doc,original)
        self.assertTrue(board['status']['3.1']['completed'])

    def test_unselected_alternatives_never_merged_or_marked_complete(self):
        doc=self.doc();doc.pop('selected_scheme_id')
        board=self.build(doc)
        self.assertFalse(board['status']['3.1']['completed'])
        self.assertEqual([],board['parts'])

    def test_layout_counts_and_source_required_before_ready(self):
        doc=self.doc();doc['layout_rows']=[{'scheme_id':'1','entity_id':'L','kind':'layout_instruction',
            'part_codes':['B2'],'sheet_mm':[300,200],'dimension_role':'cut_sheet','size_source':'cad_cut_sheet_dimension'}]
        board=self.build(doc)
        self.assertFalse(board['status']['3.1']['completed'])
        doc['layout_rows'][0]['part_counts']={'B2':4}
        board=self.build(doc)
        self.assertTrue(board['status']['3.1']['completed'])
        self.assertAlmostEqual(.4,board['layouts'][0]['utilization'])

    def test_allowance_configuration_uses_same_cost_rule(self):
        plan=IntegrationPlan(packaging_cut_settings={'cut_margin_length_mm':3,'cut_margin_width_mm':7})
        board=self.build(plan=plan)
        self.assertEqual([103,67],board['individual_cuts'][0]['sheet_mm'])

    def test_backend_step_status_not_legacy_params(self):
        facts={'requirement':{'data':{'industry':'packaging'}},'packaging_business_parts':self.doc(),
               'plan':IntegrationPlan()}
        verdict=workflow_projection._judge('3.1','P',facts)
        self.assertTrue(verdict['completed'])

    def render(self, tab, board):
        path=ROOT/'tech_app/frontend/packaging-stage-workspace.js'
        self.assertTrue(path.exists(),'缺少包装专属页签渲染')
        script='const window={};'+path.read_text()+'\nconsole.log(window.PackagingStageWorkspace.render('+json.dumps(tab)+','+json.dumps(board)+'));'
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        return result.stdout

    def test_three_tabs_are_separate_and_escape_names(self):
        board={'project_id':'P','parts':[{'code':'B','name':'<script>','material':'225g',
            'size_mm':[100,60],'quantity':2,'sections':[]}], 'layouts':[],
            'individual_cuts':[{'code':'B','name':'<script>','size_mm':[100,60],'sheet_mm':[100,65],'allowance_mm':[0,5]}],
            'part_processes':[{'code':'B','name':'面纸','plan':{'steps':[{'step_no':10,'name':'模切','recommendation':{'kind':'ai'},'cost_parameters':{'labor_seconds':30}}]}}],
            'assembly_steps':[{'step_no':10,'name':'裱合','type':'assembly','description':'组合零件',
                              'part_codes':['B','C'],'cost_parameters':{'labor_seconds':30}}],
            'postprocess_steps':[],'status':{},'schemes':[],'cut_settings':{}}
        one,two,three=[self.render(tab,board) for tab in ('drawings','params','process')]
        self.assertIn('&lt;script&gt;',one)
        self.assertIn('100.00 × 60.00 mm',one)
        self.assertNotIn('整合图纸',one)
        self.assertIn('开料',two)
        self.assertNotIn('整机概览',two)
        self.assertNotIn('零件间连接',two)
        self.assertIn('裱合',three)
        self.assertNotIn('模切',three)
        self.assertIn('计费参数',three)
        self.assertNotIn('成本金额',three)

    def test_packaging_stage_one_never_runs_params_or_process(self):
        src=(ROOT/'tech_app/frontend/assembly-integration.js').read_text()
        body=src[src.index('async function aiRunAll()'):src.index('/* ------------------------------------------------------- 模型参数设置')]
        script='''const calls=[];const aiIndustry='packaging';const aiPid='P';
const $ai=()=>({value:''});const aiPlan=()=>({quantity:1});let aiData={status:{has_params:true,has_process:true}},aiTab='drawings';
const aiUrl=()=>'';const api=async()=>({});const aiSay=()=>{};const aiRender=()=>{};
const aiGenerate=async step=>{calls.push(step);aiData={status:{has_params:true,has_process:true}}};
const aiAutoFillParams=async()=>{};const AI_TABS={};const aiRefreshPackagingWorkspace=async()=>({status:{'3.1':{completed:true}}});
'''+body+"\naiRunAll().then(()=>{if(calls.length)process.exit(1)}).catch(e=>{console.error(e);process.exit(2)});"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr or '包装3.1仍生成后续参数/工艺')

    def test_settings_reach_real_cost_data_and_invalidate_fingerprint(self):
        doc={'data':{'industry':'packaging','cut_margin_width_mm':5,'inner_length':220}}
        plan=IntegrationPlan(packaging_cut_settings={'cut_margin_width_mm':9})
        with patch.object(store,'load_requirement',return_value=doc),patch.object(integration,'load_plan',return_value=plan):
            effective=packaging_cost._requirement_data('P')
        self.assertEqual(9,effective['cut_margin_width_mm'])
        self.assertEqual(220,effective['inner_length'])
        self.assertEqual(5,doc['data']['cut_margin_width_mm'])
        self.assertNotEqual(packaging_sheet_cost.allowance_fingerprint(doc['data']),packaging_sheet_cost.allowance_fingerprint(effective))

    def test_invalid_allowance_rejected(self):
        service=self.service()
        for settings in ({'cut_margin_width_mm':-1},{'cut_margin_width_mm':float('nan')},
                         {'cut_allowances':{'M':{'length_mm':-1}}},{'other':12}):
            with self.assertRaises(ValueError):service.validate_cut_settings(settings)

    def test_external_parts_do_not_block_cut_or_require_manufacturing(self):
        doc=self.doc();doc['business_parts'].append({'business_part_code':'MAG','name':'磁铁',
             'scheme_id':'1','reference':{'material_text':'磁铁','quantity':2,'process_text':'采购'}})
        board=self.build(doc)
        self.assertEqual(['B2'],[r['code'] for r in board['individual_cuts']])
        self.assertNotIn('MAG',[r['code'] for r in board['part_processes']])
        self.assertTrue(board['status']['3.1']['completed'])

    def test_draft_layout_not_promoted_to_actual_and_duplicate_actual_owner_blocks(self):
        doc=self.doc();doc['layout_rows']=[{'kind':'drawing_layout_candidate','part_codes':['B2'],'entity_id':'D'}]
        board=self.build(doc)
        self.assertTrue(board['status']['3.1']['completed'])
        self.assertEqual(1,len(board['individual_cuts']))
        real={'kind':'layout_instruction','scheme_id':'1','part_codes':['B2'],'entity_id':'L','sheet_mm':[300,200],
              'dimension_role':'cut_sheet','part_counts':{'B2':4}}
        doc['layout_rows']=[real,{**real,'entity_id':'L2'}]
        self.assertFalse(self.build(doc)['status']['3.1']['completed'])

    def test_actual_workspace_handler_reads_only_active_part_records(self):
        tree=ast.parse((ROOT/'tech_app/backend/main.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='read_packaging_business_parts')
        fn.decorator_list=[]
        from fastapi import Depends
        namespace={'__name__':'tech_app.backend.main','__package__':'tech_app.backend','Depends':Depends,'current_user':lambda:None,'_workflow_project':lambda p:None,
                   'packaging_parts':packaging_parts,'store':store,'integration':integration}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'workspace-handler','exec'),namespace)
        calls=[]
        with patch.object(packaging_parts,'load_business_parts',return_value=self.doc()), \
             patch.object(packaging_parts,'load_part_process',side_effect=lambda p,c:calls.append(c) or {}), \
             patch.object(store,'load_requirement',return_value={'data':{'industry':'packaging'}}), \
             patch.object(integration,'load_plan',return_value=IntegrationPlan()), \
             patch.object(store,'load_cost_lookup',side_effect=AssertionError('workspace must not load cost')):
            result=namespace['read_packaging_business_parts']('P',user={'role':'engineer'},workspace=True)
        self.assertEqual(['B2'],calls)
        self.assertIn('workspace',result)
        self.assertNotIn('geometry_evidence',result)

    def test_project_postprocess_does_not_double_count_per_part_glue(self):
        from tech_app.backend.models.process import ProcessPlan
        plan=IntegrationPlan(process=ProcessPlan(part_class='packaging',steps=[
            {'step_no':10,'name':'零件粘合','type':'other','description':'来自零件路线'},
            {'step_no':20,'name':'装盒','type':'assembly','description':'项目组装','part_codes':['B2','B3']}]))
        doc=self.doc();doc['business_parts'].append({'business_part_code':'B3','name':'灰板','scheme_id':'1'})
        board=self.build(doc=doc,plan=plan)
        self.assertEqual(['装盒'],[s['name'] for s in board['postprocess_steps']])

    def test_prepare_cut_only_fills_missing_dimensions_preserves_known_and_no_model(self):
        service=self.service();doc=self.doc();doc['derived_from_drawing']=True
        doc['business_parts'].append({'business_part_code':'G','scheme_id':'1','name':'未知衬纸'})
        saved=[]
        with patch.object(packaging_parts,'load_business_parts',return_value=doc), \
             patch.object(packaging_parts,'load_parts',return_value={}), \
             patch.object(packaging_parts,'save_business_parts',side_effect=lambda p,d:saved.append(d)), \
             patch.object(store,'audit'), \
             patch('tech_app.backend.services.llm_client.run',side_effect=AssertionError('cut preparation must not call model')):
            self.assertTrue(service.prepare_cut_inputs('P','PE1'))
        self.assertEqual(doc['business_parts'][0],saved[0]['business_parts'][0])
        self.assertEqual('demo_placeholder_not_cad',saved[0]['business_parts'][2]['poc_inputs']['size_source'])
        self.assertNotIn('confirmed_size',saved[0]['business_parts'][2])
        self.assertNotIn('poc_inputs',doc['business_parts'][2])

    def test_strict_cut_mode_does_not_fill_estimates(self):
        service=self.service();doc={**self.doc(),'derived_from_drawing':True,'auto_geometry_inputs':False}
        with patch.object(packaging_parts,'load_business_parts',return_value=doc), \
             patch.object(packaging_parts,'save_business_parts',side_effect=AssertionError('must not write')):
            self.assertFalse(service.prepare_cut_inputs('P','PE1'))

    def test_packaging_toolbar_has_only_current_step_primary_and_legacy_unchanged(self):
        from tests.test_integration_params_tab_single_primary_and_auto_fill_red import block_from
        src=(ROOT/'tech_app/frontend/assembly-integration.js').read_text()
        blocks={k:block_from(src,k+':') for k in ('runIntegration','confirmDrawingsAndNext',
                  'generateIntegrationParams','confirmParamsAndNext','generateIntegrationProcess','sendIntegrationToFinance')}
        script='let aiIndustry="packaging",aiTab="params",aiBusy=false,aiDeferredBusy=false;const aiHasParams=()=>false,aiHasProcess=()=>false,aiAnalyzed=()=>false;\n'
        for name,block in blocks.items():script+='const '+name+'='+block+';\n'
        script+='''
if(generateIntegrationParams.getState().visible)process.exit(1);
if(!confirmParamsAndNext.getState().visible||confirmParamsAndNext.getState().label!=='进入组装工艺')process.exit(2);
aiTab='drawings';
if(runIntegration.getState().visible||confirmDrawingsAndNext.getState().visible)process.exit(3);
aiTab='process';
if(generateIntegrationProcess.getState().label!=='生成新增组装工艺')process.exit(4);
aiIndustry='battery';aiTab='params';
if(!generateIntegrationParams.getState().visible||confirmParamsAndNext.getState().visible)process.exit(5);
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_package_scripts_loaded_before_renderer(self):
        html=(ROOT/'tech_app/frontend/assembly-integration.html').read_text()
        self.assertLess(html.index('src="packaging-stage-workspace.js'),html.index('src="assembly-integration.js'))
        self.assertLess(html.index('src="packaging-process-presentation.js'),html.index('src="assembly-integration.js'))

    def test_different_section_materials_use_own_allowances(self):
        doc=self.doc();doc['business_parts'][0]['sections']=[
            {'section_id':'A','name':'灰板','estimated_size':{'length_mm':100,'width_mm':50},'poc_inputs':{'material_code':'GREY'}},
            {'section_id':'B','name':'面纸','estimated_size':{'length_mm':100,'width_mm':50},'poc_inputs':{'material_code':'PAPER'}}]
        plan=IntegrationPlan(packaging_cut_settings={'cut_allowances':{'GREY':{'width_mm':3},'PAPER':{'width_mm':8}}})
        self.assertEqual([[100,53],[100,58]],[r['sheet_mm'] for r in self.build(doc,plan)['individual_cuts']])

    def test_layout_can_bind_specific_section_and_other_section_keeps_individual_cut(self):
        from tech_app.backend.services import packaging_layout
        doc=self.doc();doc['business_parts'][0]['sections']=[
            {'section_id':'A','name':'第一组成','quantity':1,'estimated_size':{'length_mm':100,'width_mm':50}},
            {'section_id':'B','name':'第二组成','quantity':1,'estimated_size':{'length_mm':80,'width_mm':40}}]
        doc['layout_rows']=[{'entity_id':'L','scheme_id':'1','kind':'layout_instruction',
                             'sheet_mm':[300,200],'dimension_role':'cut_sheet','part_counts':{'B2::A':4}}]
        updated=packaging_layout.set_layout_assignment(doc,'L',['B2::A'],actor='PE1')
        board=self.build(updated)
        self.assertTrue(board['status']['3.1']['completed'],board['status'])
        self.assertEqual(['B2::B'],[r['code'] for r in board['individual_cuts']])
        self.assertIn('B2::A',[r['code'] for r in board['layout_parts']])

    def test_layout_section_respects_parent_scheme_boundary(self):
        from tech_app.backend.services import packaging_layout
        doc=self.doc();doc['business_parts'][1]['sections']=[{'section_id':'OTHER','name':'别案组成'}]
        doc['business_parts'][1]['sections_source']='manual'
        doc['layout_rows']=[{'entity_id':'L','scheme_id':'1'}]
        with self.assertRaisesRegex(ValueError,'scheme_conflict'):
            packaging_layout.set_layout_assignment(doc,'L',['B1::OTHER'],actor='PE1')

    def test_route_from_other_scheme_is_not_shown_as_adopted(self):
        from tech_app.backend.models.process import ProcessPlan
        plan=IntegrationPlan(process_confirmed=True,packaging_route_scheme_id='2',
            process=ProcessPlan(part_class='packaging',steps=[{'step_no':10,'name':'开料','type':'blank','description':'开料'}]))
        self.assertFalse(self.build(plan=plan)['status']['3.2']['completed'])

    def test_legacy_unscoped_route_is_not_adopted_for_multiple_schemes(self):
        from tech_app.backend.models.process import ProcessPlan
        plan=IntegrationPlan(process_confirmed=True,
            process=ProcessPlan(part_class='packaging',steps=[{'step_no':10,'name':'开料','type':'blank','description':'开料'}]))
        self.assertFalse(self.build(plan=plan)['status']['3.2']['completed'])
