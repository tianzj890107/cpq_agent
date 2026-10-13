import unittest
from unittest import mock
from tests.test_packaging_demo_flow_end_to_end_red import DemoFlow,PID,REQ_NO,REQ,GEOMETRY,BUSINESS
from tech_app.backend.storage import store
from tech_app.backend.services import packaging_parts,packaging_bom

class AutoCloseLoop(DemoFlow):
    def test_cached_geometry_does_not_skip_new_pending_requirement_values(self):
        from tech_app.backend.services import packaging_demo_flow
        store.save_requirement(PID,{'project_id':PID,'requirement_no':REQ_NO,'data':dict(REQ)})
        packaging_parts.save_parts(PID,dict(GEOMETRY))
        packaging_parts.save_business_parts(PID,dict(BUSINESS))
        actor={'username':'PE1','role':'process_manager'}
        packaging_demo_flow.ensure_inputs(PID,actor)
        req=store.load_requirement(PID)
        req['data']['quote_quantity']='待人工确认'
        store.save_requirement(PID,req)
        packaging_demo_flow.ensure_inputs(PID,actor)
        self.assertEqual(1000,store.load_requirement(PID)['data']['quote_quantity'])

    def test_pending_placeholder_is_missing_input_not_a_numeric_value(self):
        from tech_app.backend.services import packaging_demo_flow
        result=packaging_demo_flow.requirement_defaults({'data':{'industry':'packaging',
            'box_type':REQ['box_type'],'quote_quantity':'待人工确认','inner_width':'待确认'}})
        self.assertEqual(1000,result['data']['quote_quantity'])
        self.assertGreater(result['data']['inner_width'],0)
        self.assertEqual('待人工确认',result['data']['demo_assumptions']['quote_quantity']['original_value'])

    def test_preparation_rule_version_change_rebuilds_saved_inputs(self):
        from tech_app.backend.services import packaging_demo_flow,packaging_demo_inputs
        store.save_requirement(PID,{'project_id':PID,'requirement_no':REQ_NO,'data':dict(REQ)})
        packaging_parts.save_parts(PID,dict(GEOMETRY))
        packaging_parts.save_business_parts(PID,dict(BUSINESS))
        actor={'username':'PE1','role':'process_manager'}
        packaging_demo_flow.ensure_inputs(PID,actor)
        with mock.patch.object(packaging_demo_inputs,'ENGINE_VERSION','new-input-rule'),mock.patch.object(packaging_demo_inputs,'prepare',wraps=packaging_demo_inputs.prepare) as prepare:
            packaging_demo_flow.ensure_inputs(PID,actor)
            prepare.assert_called_once()

    def test_original_click_sequence_reuses_processes_at_finance_cost_step(self):
        from tech_app.backend.services import packaging_demo_flow,packaging_demo_inputs
        store.save_requirement(PID,{'project_id':PID,'requirement_no':REQ_NO,'data':dict(REQ)})
        packaging_parts.save_parts(PID,dict(GEOMETRY))
        packaging_parts.save_business_parts(PID,dict(BUSINESS))
        user={'username':'PE1','role':'process_manager'}
        self.assertTrue(packaging_demo_flow.ensure_inputs(PID,user))
        from tech_app.backend.services import integration,product_params
        plan=integration.load_plan(PID)
        self.assertFalse(product_params.missing_required(plan.params,product_params.family_for_industry('packaging')))
        self.assertTrue(plan.params_final)
        self.assertEqual('自动采用',plan.params_final_by)
        first=packaging_parts.load_business_parts(PID)['business_parts_id']
        packaging_demo_flow.ensure_inputs(PID,user)
        self.assertEqual(first,packaging_parts.load_business_parts(PID)['business_parts_id'])
        lookup={'status':'matched','match_method':'exact_name','routes':[{'steps':[
            {'line_number':10,'operation_name':'组装'}]}]}
        with mock.patch('tech_app.backend.services.da_process_routing.for_row',return_value=lookup):
            packaging_demo_inputs.prepare_processes(PID,actor='PE1')
        with mock.patch.object(packaging_demo_inputs,'prepare_processes',side_effect=AssertionError('must reuse')):
            result=packaging_demo_flow.run(PID,actor={'username':'FI1','role':'finance_manager'})
        self.assertTrue(result['ready'])
        self.assertTrue(result['processes']['reused'])
        plan=integration.load_plan(PID)
        self.assertTrue(plan.process_confirmed)
        self.assertEqual('packaging',plan.process.part_class)

    def test_no_new_button_or_independent_start_api(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        self.assertNotIn('startPackagingDemoAutoFlow',(root/'tech_app/frontend/app.js').read_text())
        self.assertNotIn("@app.post('/api/projects/{pid}/requirement/packaging-demo-flow')",(root/'tech_app/backend/main.py').read_text())
    def test_missing_demo_requirement_fields_are_filled_without_overwriting_known_values(self):
        from tech_app.backend.services import packaging_demo_flow
        value={'data':{'industry':'packaging','box_type':REQ['box_type'],'inner_length':200}}
        result=packaging_demo_flow.requirement_defaults(value)
        self.assertEqual(200,result['data']['inner_length'])
        self.assertGreater(result['data']['inner_width'],0)
        self.assertEqual(1000,result['data']['quote_quantity'])
        self.assertTrue(result['data']['demo_assumptions'])
    def test_no_manual_box_route_or_size_confirmation_needed(self):
        from tech_app.backend.services import packaging_demo_flow
        store.save_requirement(PID,{'project_id':PID,'requirement_no':REQ_NO,'data':dict(REQ)})
        packaging_parts.save_parts(PID,dict(GEOMETRY))
        packaging_parts.save_business_parts(PID,dict(BUSINESS))
        lookup={'status':'matched','match_method':'exact_name','routes':[{'steps':[
            {'line_number':10,'operation_name':'组装'}]}]}
        with mock.patch('tech_app.backend.services.da_process_routing.for_row',return_value=lookup):
            result=packaging_demo_flow.run(PID,actor={'username':'PE1','role':'process_manager'},
                run_model=mock.Mock(side_effect=AssertionError('native must not call model')))
        self.assertTrue(result['ready'],result)
        self.assertEqual(2,result['cost_projection']['counts']['parts_costed'])
        self.assertGreater(result['quote_draft']['untaxed_unit_price'],0)

    def test_bom_consumes_all_automatic_section_dimensions(self):
        row={'business_part_code':'p','name':'面纸','reference':{'quantity':1,'material_text':'225g面纸'},
             'sections_complete':True,'geometry_binding':{'status':'bound','bound_by':'auto',
                'component_ids':['c1','c2'],'entity_ids':['e1','e2']},'sections':[
             {'section_id':'s1','name':'片1','quantity':1,'status':'bound','entity_ids':['e1'],'component_ids':['c1'],
              'estimated_size':{'length_mm':120,'width_mm':60}},
             {'section_id':'s2','name':'片2','quantity':1,'status':'bound','entity_ids':['e2'],'component_ids':['c2'],
              'estimated_size':{'length_mm':80,'width_mm':40}}]}
        rows=packaging_bom.business_part_rows({'derived_from_drawing':True,'business_parts':[row]})
        self.assertEqual([120,80],[r['length_mm'] for r in rows])
