import copy
import json
import subprocess
import unittest
from pathlib import Path

from cpq_cost_contract import cost_fields
from tech_app.backend.services import packaging_cost, packaging_cost_ledger
from tests.test_packaging_whole_part_process_unit_red import js_function

ROOT = Path(__file__).resolve().parents[1]


class VisibleCostTests(unittest.TestCase):
    def test_round_unit_then_multiply_without_rewriting_ledger(self):
        for raw, quantity, unit, order in [(47.0069,1000,47.01,47010), (1.005,1000,1.01,1010), (47.004,1000,47,47000), (0,1000,0,0)]:
            with self.subTest(raw=raw):
                doc={'business_parts':[{'business_part_code':'A','reference':{'quantity':1}}]}
                ledger={'built':True,'quote_quantity':quantity,'total_cost':raw,'material_total':raw,
                        'items':[{'part_code':'A','cost_category':'material','amount':raw}],
                        'source_versions':{'expense_policy':packaging_cost.EXPENSE_POLICY}}
                before=copy.deepcopy(ledger)
                view=packaging_cost_ledger.project(doc,ledger)
                self.assertEqual(unit,view['unit_cost'])
                self.assertEqual(unit,view['final']['total'])
                self.assertEqual(unit,view['assembly']['unit_cost'])
                self.assertEqual(order,view['order_total_cost'])
                self.assertEqual(raw,view['raw_unit_cost'])
                self.assertEqual(raw,view['parts'][0]['unit_cost'])
                self.assertTrue(view['reconciled'])
                returned=cost_fields({'industry':'packaging','requirement':{},'cost':ledger})
                self.assertEqual(returned['order_total_cost'],view['order_total_cost'])
                self.assertEqual(before,ledger)

    def test_other_expenses_open_and_no_assembly_or_historical_copy(self):
        file=ROOT/'tech_app/frontend/cost-review.js'
        script="const crMoney=v=>Number(v||0).toFixed(2);const esc=String;"
        script+="const crData={packaging_cost:{built:true,quote_quantity:1000},expense_policy:'legacy',assembly:{name:'包装整单成本',has_cost:true,unit_cost:47.01,breakdown:{material:45,processing:1,labor:.5,packaging:.51,others:1.01},summary:'材料 + 加工 + 其他费用'},final:{material:45,processing:1,labor:.5,packaging:.51,others:1.01,total:47.01},unit_cost:47.01,order_total_cost:47010};"
        script+='\n'.join(js_function(file,n) for n in ('crPackagingBreakdown','crBreakdownRow','crRenderAssembly','crRenderTotal'))
        script+="const html=crRenderAssembly()+crRenderTotal();console.log(JSON.stringify({html}));"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,check=True)
        html=json.loads(result.stdout)['html']
        self.assertRegex(html,r'<details[^>]*\bopen\b[^>]*><summary>其他费用 1\.01')
        self.assertNotIn('历史人工',html)
        self.assertNotIn('组装人工',html)
        self.assertIn('47010.00',html)
        self.assertIn('包装费',html)
