"""新明细规则必须独立于0903，并贯通实际项目成本。"""
import json
import unittest
from unittest.mock import patch
from tests.test_packaging_cost_engine_red import CostCase, PID, REQ_NO
from tech_app.backend.services import packaging_cost as cost


class DetailRules(unittest.TestCase):
    def test_unknown_assembly_time_has_one_whole_box_budget(self):
        from tech_app.backend.services import packaging_assembly
        rows=[{'step_no':i,'scope':'project','standard_seconds':30,'time_source':'poc_mock'} for i in range(12)]
        self.assertTrue(hasattr(packaging_assembly,'estimated_labor_seconds'))
        allocated=packaging_assembly.estimated_labor_seconds(rows)
        self.assertAlmostEqual(30,sum(allocated.values()))
        rows.append({'step_no':99,'scope':'project','standard_seconds':120,'time_source':'provided_standard'})
        self.assertNotIn(99,packaging_assembly.estimated_labor_seconds(rows))

    def test_quote_document_preserves_three_cost_groups_and_same_price(self):
        import cpq_packaging_quote as quote
        from tests.test_packaging_quote_close_loop_red import package_fixture
        package=package_fixture()
        package['cost'].update(cost_profile='detail_sheet/1',material_total=2,process_total=6,
            labor_total=.3,packaging_total=1,other_total=0,tooling_total=0,freight_total=0,
            subtotal=8.3,total_cost=9.3)
        result=quote.price(package,gross_margin_rate=.1)
        self.assertAlmostEqual(9.3/.9,result['margin_price'])
        section=quote._doc_sections(result)[4]
        labels=[r['项目'] for r in section['rows']]
        self.assertIn('加工',labels);self.assertIn('其他费用',labels)
        self.assertNotIn('工装',labels);self.assertNotIn('人工',labels)

    def test_quantity_imposition_matrix_and_soft_variant(self):
        for quantity in (1000,5000,10000,20000):
            for n_up in (1,2):
                inputs={'quote_quantity':quantity,'imposition_count':n_up,'times':2,
                        'machine_length':889,'machine_width':700,'tax_factor':1.13,'hot_area_mm2':30000}
                expected={'die_cutting':max(100/quantity,.15/n_up),
                    'hot_stamp_flat':max(150/quantity,.255+.3/n_up),
                    'v_groove':max(150/quantity,.15)*2,
                    'lamination':max(200/quantity,(889*700/1e6*1.7/1.13+889*700/1e6*18/1000*18.5+.15)/n_up)}
                for category,value in expected.items():
                    r=cost.compute_line(category,inputs,rows=[],cost_profile='detail_sheet/1')
                    self.assertAlmostEqual(value,r['amount'])
        r=cost.compute_line('die_cutting',{'quote_quantity':10000,'imposition_count':2,'die_cut_variant':'soft'},rows=[],cost_profile='detail_sheet/1')
        self.assertAlmostEqual(.04,r['amount']);self.assertEqual('PKG-D-DIE-CUT-SOFT',r['formula_code'])

    def test_invalid_reviewed_detail_rule_does_not_fall_back(self):
        with self.assertRaises(cost.CostError):
            cost.compute_line('die_cutting',{'quote_quantity':1000},cost_profile='detail_sheet/1',
                rows=[{'formula_code':'PKG-D-DIE-CUT','review_status':'reviewed','expression':'__import__(1)'}])

    def test_old_calculation_profile_keeps_total_and_requires_recalculation(self):
        old={'built':True,'total_cost':123,'source_versions':{'expense_policy':cost.EXPENSE_POLICY}}
        result=cost.with_current_expense_policy(old)
        self.assertEqual(123,result['total_cost'])
        self.assertIn('calculation_profile_changed',result['stale_reasons'])

    def test_source_workbook_and_each_formula_are_audited(self):
        from scripts.packaging_detail_cost_rules import audit,ROOT
        workbook=ROOT/'裕同包装项目-待开发/成本测算明细.xlsx'
        if not workbook.exists():self.skipTest('客户原工作簿不在此环境')
        self.assertEqual(36,len(audit(workbook)))

    def test_multi_part_layout_bills_shared_die_cut_once(self):
        from tech_app.backend.services import packaging_sheet_cost
        layout={'entity_id':'LAY1','sheet_mm':[600,400],'part_codes':['A','B'],
                'part_counts':{'A':2,'B':2}}
        parts=[{'part_code':c,'material_code':'M','quantity':1,'length_mm':100,'width_mm':100} for c in ('A','B')]
        lines=[{'part_code':c,'cost_category':'die_cutting','formula_code':'PKG-D-DIE-CUT',
                'inputs':{'quote_quantity':1000,'imposition_count':2,'die_cut_unit_fee':.15},
                'amount':.1,'loss_rate':0} for c in ('A','B')]
        def evaluate(category,variables,**kwargs):
            return cost.compute_line(category,variables,cost_profile='detail_sheet/1',**kwargs)
        self.assertTrue(hasattr(packaging_sheet_cost,'apply_layout_process_costs'))
        gaps=packaging_sheet_cost.apply_layout_process_costs([layout],parts,lines,1000,evaluate,[])
        self.assertEqual([],gaps)
        self.assertAlmostEqual(.1,sum(r['amount'] for r in lines))
        self.assertEqual(500,lines[0]['inputs']['sheet_quantity'])

    def test_decimal_constants_are_supported_but_attribute_access_is_not(self):
        from tech_app.backend.services import packaging_formula
        self.assertEqual(2,packaging_formula.evaluate('50.8/25.4',{},precision=8))
        with self.assertRaises(packaging_formula.FormulaError):
            packaging_formula.evaluate('a.__class__',{'a':1})

    def detail(self,category,**variables):
        return cost.compute_line(category,dict(machine_length=889,machine_width=700,
            imposition_count=1,quote_quantity=1000,tax_factor=1.13,**variables),
            rows=[],cost_profile='detail_sheet/1')

    def test_five_processes_match_visible_workbook(self):
        inputs={'machine_length':889,'machine_width':700,'imposition_count':1,
                'quote_quantity':1000,'tax_factor':1.13,'hot_area_mm2':30000}
        expected={'lamination':1.2934294398230088,'hot_stamp_flat':.555,
                  'die_cutting':.15,'v_groove':.15,
                  'mounting':889/25.4*700/25.4*.56/1000}
        for category,value in expected.items():
            with self.subTest(category=category):
                r=cost.compute_line(category,inputs,rows=[],cost_profile='detail_sheet/1')
                self.assertAlmostEqual(value,r['amount'])
                self.assertTrue(r['formula_code'].startswith('PKG-D-'))
                self.assertIn('成本测算明细.xlsx',r['source_ref'])

    def test_minimum_charge_and_multiple_passes(self):
        r=cost.compute_line('v_groove',{'quote_quantity':1000,'times':2},rows=[],cost_profile='detail_sheet/1')
        self.assertAlmostEqual(.3,r['amount'])
        r=cost.compute_line('die_cutting',{'quote_quantity':1000,'imposition_count':2},rows=[],cost_profile='detail_sheet/1')
        self.assertAlmostEqual(.1,r['amount']);self.assertTrue(r['min_charge_applied'])

    def test_setup_parameters_cannot_leak_into_detail(self):
        r=self.detail('die_cutting',setup_minutes=99999,labor_rate=99999,equipment_rate=99999)
        self.assertAlmostEqual(.15,r['amount'])
        self.assertNotIn('setup_minutes',cost.expression_variables(r['expression']))

    def test_original_0903_goldens_still_available(self):
        r=cost.compute_line('die_cutting',{'quote_quantity':1000,'imposition_count':1},rows=[])
        self.assertAlmostEqual(.8347876923076923,r['amount'])

    def test_glue_patch_area_is_not_divided_by_imposition_again(self):
        r=self.detail('glue',glue_area_mm2=4920)
        self.assertAlmostEqual(4920/1000000*.74,r['amount'])

    def test_old_kb_code_does_not_override_new_profile(self):
        rows=[{'formula_code':'PKG-C-DIE-CUT','review_status':'reviewed','expression':'999'}]
        r=cost.compute_line('die_cutting',{'quote_quantity':1000,'imposition_count':1},rows=rows,cost_profile='detail_sheet/1')
        self.assertAlmostEqual(.15,r['amount'])


class DetailProject(CostCase):
    def test_manufacturing_process_uses_its_part_loss_rate(self):
        from tech_app.backend.services import packaging_assembly
        self.prepare()
        part=next(r for r in cost.da_repo.load_packaging_bom(PID,REQ_NO) if r['bom_category']=='box_part')
        code=part.get('part_code') or part['item_key']
        step={'step_no':1,'part_code':code,'step_name':'模切','formula_code':'PKG-C-DIE-CUT',
              'usage_qty':1,'cost_parameters':{'imposition_count':1}}
        with patch.object(cost.da_repo,'load_packaging_route_steps',return_value=[step]), \
             patch.object(packaging_assembly,'route_drift',return_value=''):
            result=cost.compute_project(PID,REQ_NO)
        material=next(r for r in result['items'] if r['cost_category']=='material' and r['part_code']==code)
        process=next(r for r in result['items'] if r['formula_code']=='PKG-D-DIE-CUT')
        self.assertIsNotNone(material['loss_rate'])
        self.assertEqual(material['loss_rate'],process['loss_rate'])

    def test_handoff_profile_and_minimum_policy_follow_the_saved_cost(self):
        from tech_app.backend.services import packaging_handoff
        self.prepare();self.build_cost()
        package=packaging_handoff.handoff_package(PID,REQ_NO)
        self.assertEqual('detail_sheet/1',package['cost_profile'])
        self.assertEqual('detail_sheet/1',package['minimum_charge_policy']['chosen'])

    def test_actual_assembly_cost_does_not_grow_with_mock_step_count(self):
        from tech_app.backend.services import packaging_assembly
        self.prepare()
        steps=[{'step_no':i,'scope':'project','part_code':'ASSY','part_codes':['A','B'],
                'step_name':'整盒组装','formula_code':'PKG-C-LABOR','standard_seconds':30,
                'time_source':'poc_mock','usage_qty':1} for i in range(12)]
        with patch.object(cost.da_repo,'load_packaging_route_steps',return_value=steps), \
             patch.object(packaging_assembly,'route_drift',return_value=''), \
             patch.object(cost,'_rate_row',return_value={'value':36,'minimum_charge':0}):
            result=cost.compute_project(PID,REQ_NO)
        self.assertAlmostEqual(.3,result['labor_total'])

    def test_cost_handoff_projection_has_three_groups(self):
        from tech_app.backend.services import cost_flow
        self.prepare();self.build_cost()
        projected=cost_flow.packaging_cost_projection(PID)
        self.assertTrue(projected)
        self.assertTrue(all(row['category'] in ('material','machining','other') for row in projected['items']))
        self.assertFalse(any(row['name']=='人工' for row in projected['items']))

    def test_real_project_and_database_readback_use_detail_profile(self):
        self.prepare()
        result=self.build_cost()
        self.assertEqual('detail_sheet/1',result['cost_profile'])
        self.assertTrue(any(row['formula_code']=='PKG-D-DIE-CUT' for row in result['items']))
        loaded=self.load_cost()
        self.assertEqual(result['process_total'],loaded['process_total'])
        self.assertEqual('detail_sheet/1',loaded['source_versions']['calculation_profile'])
