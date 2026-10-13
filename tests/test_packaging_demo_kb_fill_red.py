import unittest
from scripts.packaging_demo_kb_fill import plan,SOURCE

class DemoKnowledge(unittest.TestCase):
    def test_every_demo_material_has_price_and_computable_grammage(self):
        from tech_app.backend.services.packaging_cost import _material_gsm_detail
        rows=plan()
        prices={r['material_code']:r for r in rows['kb_material_price']}
        for material in rows['kb_material']:
            self.assertIn('mock',material['note'])
            self.assertEqual('packaging',material['industry'])
            self.assertGreater(_material_gsm_detail(material)[0],0)
            self.assertGreater(prices[material['material_code']]['price'],0)
            self.assertEqual('kg',prices[material['material_code']]['unit'])
            self.assertEqual(SOURCE,prices[material['material_code']]['source_name'])

    def test_rates_have_explicit_time_unit(self):
        for row in plan()['kb_cost_rate']:
            self.assertEqual('元/小时',row['unit'])
            self.assertGreater(row['value'],0)
