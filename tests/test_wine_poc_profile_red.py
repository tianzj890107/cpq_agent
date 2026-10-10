import unittest
from tech_app.backend.services import wine_poc

class WinePocTests(unittest.TestCase):
    def test_no_demo_opt_in_is_rejected(self):
        with self.assertRaises(ValueError):
            wine_poc.prepare_rows({'business_parts':[]},[],enabled=False)

    def test_reference_inputs_never_confirm_cad(self):
        doc={'business_parts':[{'business_part_code':'A','name':'底板','geometry_binding':{'status':'ambiguous'},'reference':{}}]}
        got=wine_poc.prepare_rows(doc,[{'name':'底板','code':'K','length_mm':200,'width_mm':80,'material_text':'2mm灰板'}],enabled=True)
        row=got['business_parts'][0]
        self.assertEqual('ambiguous',row['geometry_binding']['status'])
        self.assertEqual(200,row['poc_inputs']['length_mm'])
        self.assertEqual('poc_library_reference',row['poc_inputs']['source'])
        self.assertNotIn('poc_inputs',doc['business_parts'][0])

    def test_unknown_geometry_is_not_silently_defaulted(self):
        with self.assertRaisesRegex(ValueError,'poc_size_missing'):
            wine_poc.prepare_rows({'business_parts':[{'business_part_code':'A','name':'未知件','reference':{}}]},[],enabled=True)
