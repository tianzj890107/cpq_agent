import copy
import unittest
from unittest.mock import patch
from tech_app.backend.services import process,packaging_parts as parts,packaging_sections as sections,packaging_cost as cost
from tech_app.backend.models.ir import Part
from tech_app.backend.models.process import ProcessOutline


def lookup(method='exact_name',approved=False):
    return {'status':'matched','match_method':method,'routes':[{
        'header':{'md_clm_process_routing_base_info_id':'H','name':'某包装件工艺路线'},'approved':approved,
        'steps':[{'line_number':30,'operation_name':'包盒','operation_code':'OP3','standard_seconds':70},
                 {'line_number':10,'operation_name':'开料','operation_code':'OP1'},
                 {'line_number':20,'operation_name':'模切','operation_code':'OP2'}]}]}


class ClosureTests(unittest.TestCase):
    def part(self):
        return Part(part_id='P',name='陌生包装件',role='packaging',material={'spec':'225g铜版纸'})

    def row(self):
        return {'business_part_code':'P','name':'陌生包装件','reference':{
            'length_mm':150,'width_mm':80,'material_text':'225g铜版纸','size_quality':'unfolded'}}

    def test_adapter_preserves_packaging_identity(self):
        self.assertEqual('packaging',parts.business_as_ir_part(self.row()).industry)
        self.assertIsNone(parts.business_as_ir_part(self.row()).role)

    def test_geometry_adapter_also_preserves_packaging_industry(self):
        self.assertEqual('packaging',parts.as_ir_part({'part_code':'G','name':'平面纸件'}).industry)

    def test_other_industry_uses_existing_mechanical_path(self):
        part=Part(part_id='metal',name='支架',material={'spec':'Q235'})
        with patch.object(process.claude_client,'run',return_value=ProcessOutline()):
            plan,_=process.outline_process(part)
        self.assertNotEqual('packaging',plan.part_class)
        self.assertTrue(plan.steps)

    def test_packaging_decompose_does_not_use_mechanical_fallback(self):
        part=self.part()
        with patch.object(process.claude_client,'run',return_value=ProcessOutline()), self.assertRaisesRegex(ValueError,'packaging_process_empty'):
            process.decompose_process(part)

    def test_lookup_is_structured_and_route_not_rewritten(self):
        with patch.object(process.claude_client,'run',side_effect=AssertionError('database steps must not be rewritten')):
            plan,coverage=process.outline_process(self.part(),lookup=lookup())
        self.assertEqual(['开料','模切','包盒'],[s.name for s in plan.steps])
        self.assertEqual('packaging',plan.part_class)
        self.assertEqual(3,coverage['summary']['total'])

    def test_reference_time_not_inherited(self):
        with patch.object(process.claude_client,'run',return_value=ProcessOutline()):
            plan,_=process.outline_process(self.part(),lookup=lookup())
        self.assertTrue(all(s.duration_min is None for s in plan.steps))
        self.assertTrue(all(s.model_dump().get('standard_seconds') is None for s in plan.steps))

    def test_reference_operation_trace_is_not_lost(self):
        with patch.object(process.claude_client,'run',return_value=ProcessOutline()):
            plan,_=process.outline_process(self.part(),lookup=lookup())
        self.assertEqual('OP2',plan.steps[1].model_dump().get('operation_code'))

    def test_duplicate_operations_kept(self):
        data=lookup();data['routes'][0]['steps'][1]['operation_name']='模切'
        with patch.object(process.claude_client,'run',return_value=ProcessOutline()):
            plan,_=process.outline_process(self.part(),lookup=data)
        self.assertEqual(2,[s.name for s in plan.steps].count('模切'))

    def test_no_route_empty_model_is_failure_not_generic_machining(self):
        with patch.object(process.claude_client,'run',return_value=ProcessOutline()),self.assertRaisesRegex(ValueError,'packaging_process_empty'):
            process.outline_process(self.part(),lookup={'status':'not_found','routes':[]})

    def test_packaging_model_prompt_not_mechanical(self):
        with patch.object(process.claude_client,'run',return_value=ProcessOutline(steps=[{'step_no':10,'name':'模切','type':'other'}])) as run:
            plan,_=process.outline_process(self.part(),lookup={'status':'not_found','routes':[]})
        self.assertNotIn('机械加工',run.call_args.args[0])
        self.assertNotIn('去毛刺',[s.name for s in plan.steps])

    def test_mechanical_model_output_rejected_for_packaging(self):
        output=ProcessOutline(steps=[{'step_no':10,'name':'粗铣基准面','type':'milling'}])
        with patch.object(process.claude_client,'run',return_value=output),self.assertRaisesRegex(ValueError,'packaging_process_incompatible'):
            process.outline_process(self.part(),lookup={'status':'not_found','routes':[]})

    def test_cost_receives_existing_row_values_and_tax_factor(self):
        row=self.row();row['cost_parameters']={'ton_price':7600,'proof_base':50,'imposition_count':2}
        result=parts.business_cost_inputs(row,requirement={'data':{'tax_factor':1.13}},quantity=1200)
        self.assertEqual(7600,result['variables'].get('ton_price'))
        self.assertEqual(1.13,result['variables'].get('tax_factor'))
        line=cost.compute_line('material',result['variables'])
        self.assertIsNotNone(line['amount'])

    def test_global_other_material_price_not_inherited(self):
        row=self.row();row['product_item_code']='A'
        result=parts.business_cost_inputs(row,requirement={'data':{'material_code':'B','ton_price':9999}},quantity=10)
        self.assertNotIn('ton_price',result['variables'])

    def test_missing_price_remains_unknown(self):
        result=parts.business_cost_inputs(self.row(),quantity=100)
        self.assertIsNone(cost.compute_line('material',result['variables'])['amount'])

    def test_shared_route_and_section_paths_pass_lookup(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        main=(root/'tech_app/backend/main.py').read_text()
        block=main[main.index('async def packaging_business_part_process('):main.index('def get_packaging_business_part_process(')]
        self.assertIn('lookup=da_lookup',block)
        self.assertIn('lookup=da_lookup',(root/'tech_app/backend/services/packaging_sections.py').read_text())

    def test_section_uses_own_cost_parameters_not_parent_price(self):
        row=self.row()
        row.update(sections_complete=True,geometry_binding={'status':'bound','entity_ids':['e']},
            sections=[{'section_id':'S','name':'面纸部分','entity_ids':['e'],'component_ids':['c'],
                'status':'bound','quantity':1,'material_text':'225g铜版纸',
                'cost_parameters':{'ton_price':7600},'confirmed_size':{
                    'length_mm':150,'width_mm':80,'source':'verified_cad_dimension','entity_ids':['e']}}])
        row['reference']['cost_parameters']={'ton_price':9999}
        child=sections.section_input_rows(row)[0]
        result=parts.business_cost_inputs(child,quantity=1000)
        self.assertEqual(7600,result['variables'].get('ton_price'))

    def test_ambiguous_routes_not_arbitrarily_selected(self):
        payload=lookup();payload['status']='ambiguous';payload['routes']*=2
        with self.assertRaisesRegex(ValueError,'packaging_route_ambiguous'):
            process.outline_process(self.part(),lookup=payload)

    def test_approved_code_route_preserves_known_seconds_only(self):
        payload=lookup(method='exact_code',approved=True)
        payload['routes'][0]['match_method']='product_item_code'
        plan,_=process.outline_process(self.part(),lookup=payload)
        self.assertEqual(70,plan.steps[-1].standard_seconds)
        self.assertIsNone(plan.steps[0].standard_seconds)

    def test_old_mechanical_business_plan_cannot_feed_project_cost(self):
        from tech_app.backend.services import packaging_process_instances as instances
        with patch.object(parts,'load_business_parts',return_value={'business_parts':[{'business_part_code':'P'}]}), \
             patch.object(parts,'load_part_process',return_value={'plan':{'part_class':'machining','steps':[{'name':'粗铣'}]}}):
            result=instances.collect('isolated')
        self.assertEqual(['P'],result['missing'])
        self.assertEqual([],result['records'])

    def test_reference_input_not_mutated(self):
        payload=lookup();before=copy.deepcopy(payload)
        process.outline_process(self.part(),lookup=payload)
        self.assertEqual(before,payload)

    def test_old_mechanical_section_also_cannot_feed_cost(self):
        from tech_app.backend.services import packaging_process_instances as instances
        with patch.object(parts,'load_business_parts',return_value={'business_parts':[{'business_part_code':'P'}]}), \
             patch.object(parts,'load_part_process',return_value={'plan':{'steps':[{'name':'合并'}]},
                'sections':[{'plan':{'part_class':'machining','steps':[{'name':'粗铣'}]}}]}):
            self.assertEqual(['P'],instances.collect('isolated')['missing'])

    def test_external_part_does_not_generate_manufacturing_steps(self):
        with patch.object(process.claude_client,'run',side_effect=AssertionError('external item must not be manufactured')), self.assertRaisesRegex(ValueError,'packaging_external_no_manufacturing'):
            process.outline_process(self.part(),lookup={'status':'external','routes':[]})

    def test_grayboard_cannot_borrow_face_paper_gsm(self):
        row=self.row();row['name']='底盒灰板';row['reference']['material_text']='2mm双灰板'
        result=parts.business_cost_inputs(row,requirement={'data':{'face_paper_gsm':225}},quantity=1000)
        self.assertFalse(result['ok'])
        self.assertEqual(['gsm'],result['missing_variables'])

    def test_grayboard_can_use_its_own_explicit_gsm(self):
        row=self.row();row['name']='底盒灰板';row['reference']['material_text']='2mm双灰板'
        row['cost_parameters']={'gsm':1250,'ton_price':3800}
        result=parts.business_cost_inputs(row,requirement={'data':{'face_paper_gsm':225}},quantity=1000)
        self.assertTrue(result['ok'])
        self.assertEqual(1250,result['variables']['gsm'])
