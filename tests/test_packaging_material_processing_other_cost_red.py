import json
import pathlib
import subprocess
import unittest
from unittest.mock import patch
from tech_app.backend.services import packaging_cost,packaging_cost_ledger
from tests.test_packaging_cost_engine_red import CostCase,PID,REQ_NO

ROOT=pathlib.Path(__file__).resolve().parents[1]

class NewPolicyCompute(CostCase):
    def test_actual_engine_bills_manufacturing_and_final_assembly_once(self):
        self.prepare()
        steps=[{'step_no':10,'step_name':'包盒','formula_code':'PKG-C-LABOR',
                'part_code':'P','usage_qty':2,'standard_seconds':30},
               {'step_no':20,'step_name':'最终合盒','formula_code':'PKG-C-LABOR',
                'part_code':'ASSY','scope':'project','part_codes':['P','Q','R'],
                'usage_qty':3,'standard_seconds':30}]
        from tech_app.backend.services import packaging_assembly
        with patch.object(packaging_cost.da_repo,'load_packaging_route_steps',return_value=steps), \
             patch.object(packaging_assembly,'route_drift',return_value=None), \
             patch.object(packaging_cost,'_rate_row',return_value={'value':36,'minimum_charge':0}):
            result=packaging_cost.compute_project(PID,REQ_NO)
            packaging_cost.da_repo.save_packaging_cost(PID,REQ_NO,result['scenario_code'],result,result['items'])
            replay=packaging_cost.load_cost(PID,REQ_NO)
        self.assertFalse(replay['stale'],replay['stale_reasons'])
        self.assertEqual(packaging_cost.EXPENSE_POLICY,replay['source_versions']['expense_policy'])
        self.assertEqual(result['process_total'],replay['process_total'])
        self.assertEqual(result['labor_total'],replay['labor_total'])
        self.assertAlmostEqual(.3,result['labor_total'])
        manual=[r for r in result['items'] if r['cost_category']=='labor']
        assembly=next(r for r in manual if r['part_code']=='ASSY')
        self.assertAlmostEqual(.3,assembly['amount'])
        self.assertEqual(1,json.loads(assembly['inputs_json'])['usage_qty'])
        self.assertTrue(any(r['part_code']=='P' and r['amount']>=.6 for r in manual))
        self.assertAlmostEqual(result['total_cost'],sum(result[k] for k in
            ('material_total','process_total','labor_total','packaging_total','other_total')))

    def test_new_cost_does_not_query_or_bill_cancelled_expenses(self):
        self.prepare()
        with patch.object(packaging_cost.kb_repo,'packaging_tooling_rules',side_effect=AssertionError('tooling queried')),patch.object(packaging_cost.kb_repo,'packaging_logistics_rules',side_effect=AssertionError('freight queried')):
            result=packaging_cost.compute_project(PID,REQ_NO)
        self.assertEqual(0,result['tooling_total']);self.assertEqual(0,result['freight_total'])
        self.assertEqual(packaging_cost.EXPENSE_POLICY,result['source_versions']['expense_policy'])

class NewPresentation(unittest.TestCase):
    def test_manufacturing_labor_is_processing_not_assembly(self):
        doc={'business_parts':[{'business_part_code':'P','reference':{'quantity':2}}]}
        ledger={'built':True,'material_total':2,'process_total':6,'labor_total':.3,'packaging_total':1,'total_cost':9.3,
          'source_versions':{'expense_policy':getattr(packaging_cost,'EXPENSE_POLICY','new')},'items':[
          {'part_code':'P','cost_category':'material','amount':2},
          {'part_code':'P','cost_category':'labor','amount':6},
          {'part_code':'ASSY','cost_category':'labor','amount':.3,'inputs_json':'{"scope":"project","part_codes":["P","Q"]}'}]}
        doc['business_parts'].append({'business_part_code':'Q','reference':{'quantity':1}})
        view=packaging_cost_ledger.project(doc,ledger)
        self.assertEqual(3,view['parts'][0]['breakdown']['processing'])
        self.assertEqual(.3,view['final']['labor'])
        self.assertAlmostEqual(1.3,view['final']['others'])
        self.assertTrue(view['reconciled'],view['reconciliation_errors'])

    def test_assembly_rate_example_is_not_multiplied_by_part_count(self):
        self.assertTrue(hasattr(packaging_cost,'assembly_labor_total'))
        rows=[{'cost_category':'labor','part_code':'P','amount':9},
              {'cost_category':'labor','part_code':'ASSY','scope':'project','part_codes':['A','B','C'],'amount':.3}]
        self.assertAlmostEqual(.3,packaging_cost.assembly_labor_total(rows))

    def test_three_visible_groups_and_nested_assembly_labor(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        fn=js_function(ROOT/'tech_app/frontend/cost-review.js','crPackagingBreakdown')
        p=subprocess.run(['node','-e',"const crMoney=String;"+fn+"console.log(crPackagingBreakdown({material:1,processing:2,labor:.3,packaging:.2,other:0,tooling:0,freight:0,others:.5}));"],capture_output=True,text=True)
        self.assertEqual(0,p.returncode,p.stderr)
        html=p.stdout
        self.assertTrue('加工' in html.split('<details')[0])
        self.assertTrue('组装人工' in html.split('<details')[1])
        self.assertNotIn('模具摊销',html);self.assertNotIn('运输费',html)
        js=(ROOT/'tech_app/frontend/cost-review.js').read_text()
        self.assertIn('<th>材料</th><th>加工</th><th>其他费用</th>',js)

    def test_summary_renders_processing_and_other_in_the_correct_columns(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        path=ROOT/'tech_app/frontend/cost-review.js'
        script="const assert=require('assert');const esc=String;const crMoney=v=>Number(v||0).toFixed(2);"
        script+="const crData={packaging_cost:{built:true,quote_quantity:1000},expense_policy:"+json.dumps(packaging_cost.EXPENSE_POLICY)+",final:{material:2,processing:6,labor:.3,packaging:1,other:0,others:1.3,total:9.3},parts_total:{material:2,processing:6,others:0,total:8},counts:{parts:2,parts_costed:2},unit_cost:9.3,order_total_cost:9300};"
        script+=js_function(path,'crPackagingBreakdown')+js_function(path,'crRenderTotal')
        script+="const html=crRenderTotal();assert(html.includes('<th>材料</th><th>加工</th><th>其他费用</th>'));assert(html.includes('6.00</span></td><td><span class=\"number\">1.30'));assert(html.includes('组装人工'));assert(!html.includes('运输费'));assert(!html.includes('模具摊销'));assert(html.includes('9300.00'));"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_old_policy_is_stale_without_changing_amount(self):
        old={'built':True,'total_cost':20,'source_versions':{'expense_policy':'part_material_processing_project_labor_packaging/1'},'stale_reasons':[]}
        self.assertTrue(hasattr(packaging_cost,'with_current_expense_policy'))
        result=packaging_cost.with_current_expense_policy(old)
        self.assertTrue(result['stale']);self.assertEqual(20,result['total_cost'])
        self.assertNotIn('stale',old)
