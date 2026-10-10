import unittest
from unittest.mock import patch
from pathlib import Path
from tech_app.backend.services import packaging_conclusions as conclusions


class ConclusionsTest(unittest.TestCase):
    def test_same_part_in_new_catalog_remains_usable(self):
        row = {'business_part_code': 'A', 'reference': {'length_mm': 10}, 'name': '纸'}
        old = {'business_parts_hash': 'old', 'business_parts': [row]}
        current = {'business_parts_hash': 'new', 'business_parts': [row, {'business_part_code': 'B'}]}
        record = {'part_code': 'A', 'business_parts_hash': 'old', 'business_parts_id': 'old-id'}
        with patch('tech_app.backend.services.packaging_parts.load_business_parts', return_value=old):
            self.assertTrue(conclusions.usable('p', record, current))
            current['business_parts'][0] = {**row, 'reference': {'length_mm': 11}}
            self.assertFalse(conclusions.usable('p', record, current))

    def test_no_historical_evidence_cannot_relabel_old_result(self):
        with patch('tech_app.backend.services.packaging_parts.load_business_parts', return_value=None):
            self.assertFalse(conclusions.usable('p', {'part_code': 'A', 'business_parts_hash': 'old'},
                                                {'business_parts_hash': 'new'}))

    def test_frontend_distinguishes_missing_cost_and_loads_business_process(self):
        root = Path(__file__).resolve().parents[1]
        cost = (root / 'tech_app/frontend/cost-review.js').read_text()
        app = (root / 'tech_app/frontend/app.js').read_text()
        self.assertIn("row.cost_stale ? '待重算' : '待测算'", cost)
        self.assertIn('loadPackagingBusinessProcessSummary(wanted)', app)

    def test_poc_rebuild_synchronizes_material_only_and_pins_version(self):
        from tech_app.backend.services import wine_poc
        doc = {'poc_demo': True, 'business_parts_id': 'new-id', 'business_parts_hash': 'new',
               'business_parts': [{'business_part_code': 'A', 'name': '纸'}]}
        result = {'source_versions': {'business_parts_hash': 'new'}, 'quote_quantity': 1000,
                  'items': [{'part_code': 'A', 'cost_category': 'material', 'amount': .13},
                            {'part_code': 'A', 'cost_category': 'labor', 'amount': 3}]}
        with patch('tech_app.backend.storage.store.load_requirement', return_value={'data': {'poc_demo': True}}), \
             patch('tech_app.backend.services.packaging_parts.load_business_parts', return_value=doc), \
             patch('tech_app.backend.services.packaging_parts.save_part_cost') as save:
            self.assertEqual(1, wine_poc.sync_material_costs('p', result))
            payload = save.call_args.args[1]
            self.assertEqual('new', payload['business_parts_hash'])
            self.assertAlmostEqual(.13, payload['summary']['computed_total'])
            save.reset_mock()
            result['source_versions']['business_parts_hash'] = 'old'
            self.assertEqual(0, wine_poc.sync_material_costs('p', result))
            save.assert_not_called()

    def test_material_or_binding_change_cannot_reuse_process(self):
        row = {'business_part_code': 'A', 'reference': {'material_text': '纸'},
               'geometry_binding': {'component_ids': ['c1']}}
        old = {'business_parts_hash': 'old', 'business_parts': [row]}
        record = {'part_code': 'A', 'business_parts_hash': 'old', 'business_parts_id': 'old-id'}
        with patch('tech_app.backend.services.packaging_parts.load_business_parts', return_value=old):
            for updated in ({**row, 'reference': {'material_text': '灰板'}},
                            {**row, 'geometry_binding': {'component_ids': ['c2']}}):
                self.assertFalse(conclusions.usable('p', record,
                    {'business_parts_hash': 'new', 'business_parts': [updated]}))
