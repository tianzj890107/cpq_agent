import copy
import pathlib
import unittest
import subprocess
import json
from unittest.mock import patch
from tech_app.backend.services import packaging_cost_ledger
import cpq_packaging_quote as quote

ROOT=pathlib.Path(__file__).resolve().parents[1]

class OwnershipTests(unittest.TestCase):
    def test_actual_addon_ui_retains_values_and_explicit_zero(self):
        from tests.test_packaging_quote_card_sales_view_parity_red import fn_src
        script="const document={querySelectorAll:()=>[{dataset:{packagingAddon:'freight'},value:'0'},{dataset:{packagingAddon:'insurance'},value:'0.25'}]};\n"+fn_src('packagingAddonValues')+"\nconsole.log(JSON.stringify(packagingAddonValues({packaging_quote:{addons:[{code:'freight',amount:2},{code:'commission',amount:1}]}})));"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual({'freight':0,'commission':1,'insurance':.25},json.loads(result.stdout))

    def test_cost_reader_and_confirmation_share_resolved_order_quantity(self):
        from tech_app.backend.services import packaging_cost,cost_review,integration
        from tech_app.backend.models.cost_review import CostReview
        from tech_app.backend.models.integration import IntegrationPlan
        req={'data':{'industry':'packaging','quote_quantity':1,'description':'预计首批定1500个'}}
        with patch.object(packaging_cost.store,'load_requirement',return_value=req),patch.object(integration,'load_plan',return_value=IntegrationPlan()):
            self.assertEqual(1500,packaging_cost._requirement_data('P')['quote_quantity'])
        with patch.object(cost_review.store,'load_requirement',return_value=req),patch.object(cost_review,'summarize',return_value={'packaging_cost':{'built':True,'quote_quantity':1500}}),patch.object(cost_review,'confirm_gaps',return_value={'codes':[]}):
            result=cost_review.payload('P',None,IntegrationPlan(),CostReview())
        self.assertEqual(1500,result['quote_quantity'])
        self.assertFalse(result['quantity_mismatch'])

    def test_quantity_replay_preserves_setup_and_minimum_charge_units(self):
        from tech_app.backend.services.packaging_cost_audit import replay_quantity
        cost={'quote_quantity':1,'items':[
            {'part_code':'A','cost_category':'lamination','formula_code':'PKG-C-LAMINATION',
             'expression':'100/quote_quantity+1/2','inputs_json':'{"quote_quantity":1,"usage_qty":2}',
             'amount':400,'min_charge_applied':True}]}
        result=replay_quantity(cost,1000)
        self.assertTrue(result['complete_excluding_freight'])
        self.assertAlmostEqual(1.2,result['unit_cost_excluding_freight'])
        self.assertAlmostEqual(1200,result['order_cost_excluding_freight'])

    def test_first_batch_text_and_delimited_labels_are_read_without_guessing(self):
        from tech_app.backend.services import requirement_service
        fields=requirement_service.extract_explicit_packaging_quote_fields('预计首批定1500个\n预计首批定1500个')
        self.assertEqual(1500,float(fields.get('quote_quantity') or 0))
        fields=requirement_service.extract_explicit_packaging_quote_fields('包装品类：酒盒、报价数量：1500、闭合方式：天地盖、面纸克重：180克')
        self.assertEqual('酒盒',fields['packaging_category'])
        self.assertEqual('天地盖',fields['closure_type'])
        self.assertEqual(1500,float(fields['quote_quantity']))
        fields=requirement_service.extract_explicit_packaging_quote_fields('零件数量：2个；报价数量：1500只')
        self.assertEqual(1500,float(fields.get('quote_quantity') or 0),'零件用量不能混进报价批量')
        fields=requirement_service.extract_explicit_packaging_quote_fields('报价数量：1500只；预计首批定1000个')
        self.assertNotIn('quote_quantity',fields,'矛盾的订单数量不能猜')

    def test_unsourced_one_is_not_preserved_over_explicit_order_text(self):
        from tech_app.backend.services import requirement_service
        data={'quote_quantity':1,'description':'预计首批定1500个'}
        value=requirement_service.merge_explicit_packaging_quote_fields(data,data['description'])
        self.assertEqual(1500,float(value['quote_quantity']))
        self.assertEqual('user_text',value['field_sources']['quote_quantity'])
        explicit=dict(data,field_sources={'quote_quantity':'user_input'})
        self.assertEqual(1,requirement_service.merge_explicit_packaging_quote_fields(explicit,data['description'])['quote_quantity'])

    def fixture(self):
        doc={'business_parts':[{'business_part_code':'A','name':'面纸','reference':{'quantity':2}}]}
        cost={'built':True,'quote_quantity':1000,'total_cost':20,'material_total':8,
              'process_total':4,'labor_total':6,'packaging_total':2,'items':[
              {'part_code':'A','cost_category':'material','amount':8},
              {'part_code':'A','cost_category':'die_cutting','amount':4},
              {'part_code':'A','cost_category':'labor','amount':6},
              {'part_code':'A','cost_category':'packaging','amount':2}]}
        return doc,cost

    def test_only_material_and_processing_in_part_and_total_unchanged(self):
        doc,cost=self.fixture();before=copy.deepcopy(cost)
        view=packaging_cost_ledger.project(doc,cost)
        self.assertEqual(6,view['parts'][0]['unit_cost'])
        self.assertEqual(0,view['parts'][0]['breakdown']['labor'])
        self.assertEqual(0,view['parts'][0]['breakdown']['packaging'])
        self.assertEqual(6,view['project_expenses']['labor'])
        self.assertEqual(2,view['project_expenses']['packaging'])
        self.assertEqual(20,view['final']['total'])
        self.assertTrue(view['reconciled'])
        self.assertEqual(before,cost)

    def test_unit_and_order_total_explicit(self):
        doc,cost=self.fixture();view=packaging_cost_ledger.project(doc,cost)
        self.assertEqual(20,view['unit_cost'])
        self.assertEqual(20000,view['order_total_cost'])
        self.assertEqual('元/只',view['cost_unit'])

    def test_quote_addons_support_real_business_costs(self):
        items,total=quote._addons_of({'freight':2,'financing':1,'insurance':.2,
                                     'commission':.3,'business_service':.4,'rush':.5,'special_testing':.6})
        self.assertAlmostEqual(5,total)
        self.assertEqual(7,len(items))

    def test_new_quote_moves_freight_out_of_margin_base_and_charges_once(self):
        from tests.test_packaging_quote_close_loop_red import package_fixture
        pkg=package_fixture()
        pkg['cost'].update(total_cost=22,freight_total=2,source_versions={
            'expense_policy':'part_material_processing_project_labor_packaging/1'})
        result=quote.price(pkg,gross_margin_rate=.2)
        self.assertEqual(20,result['cost_total'])
        self.assertEqual(25,result['margin_price'])
        self.assertEqual(2,result['addon_total'])
        self.assertEqual(27,result['net_unit_price'])
        self.assertEqual(result['net_unit_price'],quote.recompute(result)['net_unit_price'])
        result=quote.price(pkg,gross_margin_rate=.2,addons={'freight':3})
        self.assertEqual(3,result['addon_total'])
        pkg['cost'].pop('source_versions')
        result=quote.price(pkg,gross_margin_rate=.2,addons={'freight':3})
        self.assertEqual(20,result['cost_total'],'旧包显式指定运输也不能再收原运费')
        self.assertEqual(3,result['addon_total'])

    def test_estimated_default_quantity_replaced_but_true_user_value_retained(self):
        from tech_app.backend.services import requirement_service
        data={'industry':'packaging','quote_quantity':1000,'description':'报价数量：100、盒型：天地盒',
              'demo_assumptions':{'quote_quantity':{'mock':True,'value':1000}}}
        before=copy.deepcopy(data)
        resolved=requirement_service.resolved_packaging_order_data(data)
        self.assertEqual(100,resolved['quote_quantity'])
        self.assertNotIn('quote_quantity',resolved['demo_assumptions'])
        self.assertEqual(before,data)


    def test_parts_ui_uses_part_only_breakdown_and_unit_order_labels(self):
        source=(ROOT/'tech_app/frontend/cost-review.js').read_text()
        self.assertIn('crPackagingPartBreakdown(row.breakdown)',source)
        self.assertIn('order_total_cost',source)
        self.assertIn('元/只',source)

    def test_part_labor_alone_cannot_mask_missing_manufacturing_cost(self):
        doc,cost=self.fixture();cost['items']=cost['items'][2:]
        view=packaging_cost_ledger.project(doc,cost)
        self.assertFalse(view['parts'][0]['has_cost'])
