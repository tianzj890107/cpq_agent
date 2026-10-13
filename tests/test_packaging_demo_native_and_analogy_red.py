import unittest
import json
from datetime import datetime
from unittest.mock import Mock
from tech_app.backend.services import packaging_demo_inputs as demo
from tech_app.backend.models.ir import Part
from tech_app.backend.models.process import ProcessOutline, OutlineStep, ProcessType

class DemoNativeAndAnalogy(unittest.TestCase):
    def test_unbound_packaging_gap_does_not_break_part_reconciliation(self):
        from tech_app.backend.services.packaging_cost_ledger import project
        doc={'business_parts':[{'business_part_code':'p','reference':{'quantity':1}}]}
        ledger={'built':True,'material_total':4,'total_cost':4,'items':[
            {'part_code':'p','cost_category':'material','amount':4,'loss_rate':0},
            {'content_code':'unused','cost_category':'packaging','amount':None}],
            'gaps_unbound_to_order':[{'content_code':'unused','binding_status':'unbound','code':'content_formula_error:unused'}]}
        value=project(doc,ledger)
        self.assertTrue(value['ready'],value['reconciliation_errors'])
        ledger['gaps_unbound_to_order']=[]
        self.assertFalse(project(doc,ledger)['ready'])
    def test_demo_usage_projects_from_same_prepared_input(self):
        from tech_app.backend.services.packaging_cost_ledger import project
        doc={'poc_demo':True,'business_parts':[{'business_part_code':'p','reference':{'quantity':None},
            'usage_qty':2,'poc_inputs':{'mock':True,'quantity':2}}]}
        ledger={'built':True,'material_total':4,'total_cost':4,'items':[
            {'part_code':'p','cost_category':'material','amount':4,'loss_rate':0}]}
        value=project(doc,ledger)
        self.assertTrue(value['ready'],value['reconciliation_errors'])
        self.assertEqual(2,value['parts'][0]['quantity'])
    def test_pg_datetime_evidence_can_be_saved_as_json(self):
        part=Part(part_id='p',name='面纸',industry='packaging')
        lookup={'status':'matched','match_method':'exact_name','routes':[{'header':{'created_at':datetime(2026,10,12)},
            'steps':[{'line_number':10,'operation_name':'组装'}]}]}
        _,coverage=demo.demo_process_plan(part,lookup,{'length_mm':100,'width_mm':100})
        json.dumps(coverage,ensure_ascii=False)
    def test_native_route_no_model_and_mock_time_disclosed(self):
        run=Mock(side_effect=AssertionError('native route must not call model'))
        part=Part(part_id='p',name='新面纸',industry='packaging')
        lookup={'status':'matched','match_method':'exact_name','routes':[{'steps':[
            {'line_number':10,'operation_name':'组装'}]}]}
        plan,coverage=demo.demo_process_plan(part,lookup,{'length_mm':210,'width_mm':120},run=run)
        self.assertEqual(30,plan['steps'][0]['standard_seconds'])
        self.assertEqual('poc_mock',plan['steps'][0]['time_source'])
        self.assertTrue(plan['steps'][0]['cost_parameters']['mock'])
        self.assertFalse(coverage['approved'])
        run.assert_not_called()

    def test_analogy_uses_model_and_not_reference_time(self):
        part=Part(part_id='new',name='新内衬',industry='packaging')
        run=Mock(return_value=ProcessOutline(part_id='new',part_name='新内衬',steps=[
            OutlineStep(step_no=10,name='组装',type=ProcessType.assembly)]))
        plan,coverage=demo.demo_process_plan(part,{'status':'not_found','routes':[],
            'reference_routes':[{'reference_name':'库内面纸','steps':[{'operation_name':'组装','standard_seconds':999}]}]},
            {'length_mm':100,'width_mm':80},run=run)
        run.assert_called_once()
        self.assertEqual(30,plan['steps'][0]['standard_seconds'])
        self.assertEqual('packaging_analogical_recommendation',coverage['process_origin'])

    def test_size_preference_and_explicit_demo_placeholder(self):
        value=demo.demo_size({'confirmed_size':{'length_mm':300,'width_mm':150}}, {})
        self.assertEqual(300,value['length_mm'])
        value=demo.demo_size({'name':'未知配件'}, {})
        self.assertEqual('demo_placeholder_not_cad',value['size_source'])
        self.assertEqual(100,value['length_mm'])

    def test_unknown_material_demo_still_has_grammage(self):
        value=demo.demo_inputs_for({'name':'未命名部件'}, {},materials=[],allow_estimates=True)
        self.assertTrue(value['ok'])
        self.assertGreater(value['grammage'],0)
        self.assertIn('演示',value['note'])

    def test_native_approved_time_preserved(self):
        part=Part(part_id='p',name='新面纸',industry='packaging')
        lookup={'status':'matched','match_method':'exact_code','routes':[{
            'approved':True,'match_method':'product_item_code','steps':[
                {'line_number':10,'operation_name':'组装','standard_seconds':72}]}]}
        plan,_=demo.demo_process_plan(part,lookup,{'length_mm':210,'width_mm':120},run=Mock())
        self.assertEqual(72,plan['steps'][0]['standard_seconds'])
        self.assertEqual('approved_standard',plan['steps'][0]['time_source'])

    def test_known_layout_missing_cut_inputs_gets_disclosed_estimate(self):
        doc={'business_parts':[{'business_part_code':'a','poc_inputs':{'length_mm':200,'width_mm':100}},
            {'business_part_code':'b','poc_inputs':{'length_mm':150,'width_mm':80}}],
            'layout_rows':[{'kind':'layout_instruction','part_codes':['a','b']}]}
        value=demo.prepare_demo_layouts(doc)
        row=value['layout_rows'][0]
        self.assertEqual({'a':1,'b':1},row['part_counts'])
        self.assertEqual([350,105],row['sheet_mm'])
        self.assertEqual('demo_horizontal_sheet_estimate',row['size_source'])
        self.assertTrue(row['mock'])
        self.assertNotIn('sheet_mm',doc['layout_rows'][0])
