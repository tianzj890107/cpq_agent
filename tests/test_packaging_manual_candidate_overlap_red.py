import copy
import unittest
from tech_app.backend.services import packaging_parts as p


class ManualOverlapTest(unittest.TestCase):
    def fixture(self):
        binding = {'status': 'bound', 'component_ids': ['c'], 'entity_ids': ['e'], 'bound_by': 'manual'}
        return {'business_parts': [
            {'business_part_code': 'BP05', 'sequence_no': 5, 'geometry_binding': binding,
             'confirmed_size': {'length_mm': 10}, 'sections': [{'section_id': 's'}]},
            {'business_part_code': 'BP13', 'sequence_no': 13, 'geometry_binding': {
                'candidates': [{'id': 'candidate2', 'entity_ids': ['e'], 'component_ids': ['c'], 'bbox': [0,0,10,20]}]}}],
            'geometry_evidence': {'components': [{'component_id': 'c', 'entity_ids': ['e'], 'bbox': [0,0,10,20]}]}}

    def test_manual_overlap_is_saved_without_erasing_other_choice(self):
        doc = self.fixture(); before = copy.deepcopy(doc)
        result = p.set_geometry_binding(doc, 'BP13', [], candidate_id='candidate2', bound_by='manual')
        self.assertEqual(before, doc)
        self.assertEqual(before['business_parts'][0], result['business_parts'][0])
        binding = result['business_parts'][1]['geometry_binding']
        self.assertEqual('candidate2', binding['candidate_id'])
        self.assertEqual(['BP05'], binding['overlap_part_codes'])

    def test_auto_selection_still_respects_earlier_claim(self):
        with self.assertRaisesRegex(ValueError, 'already_assigned'):
            p.set_geometry_binding(self.fixture(), 'BP13', [], candidate_id='candidate2', bound_by='auto')
