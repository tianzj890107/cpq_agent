import copy
import unittest
from unittest.mock import patch
from tech_app.backend.services import packaging_parts as p


class CandidatePrecisionTest(unittest.TestCase):
    def test_whole_complementary_shape_outscores_equally_near_fragment(self):
        fragment = {'id': 'fragment', 'entity_ids': ['e1'], 'component_ids': ['c1'],
                    'geometry_status': 'supported', 'anchor_in_region': True}
        whole = {**fragment, 'id': 'whole', 'entity_ids': ['e1', 'e2'],
                 'component_ids': ['c1', 'c2'], 'evidence_reasons': [
                     'cut_lines_complementary', 'same_view', 'same_boundary_role']}
        self.assertGreater(p.candidate_confidence_percent(whole), p.candidate_confidence_percent(fragment))
        whole['evidence_reasons'].append('mixed_name_owners')
        self.assertEqual(0, p.candidate_confidence_percent(whole))

    def test_auto_falls_back_when_best_is_occupied_and_keeps_manual_owner(self):
        candidates = [{'id': 'first', 'entity_ids': ['e1'], 'component_ids': ['c1'], 'anchor_in_region': True},
                      {'id': 'second', 'entity_ids': ['e2'], 'component_ids': ['c2']}]
        doc = {'derived_from_drawing': True, 'business_parts': [
            {'business_part_code': 'A', 'sequence_no': 1, 'geometry_binding': {'bound_by': 'manual', 'status': 'bound'}},
            {'business_part_code': 'B', 'sequence_no': 2, 'geometry_binding': {'candidates': candidates}}]}
        seen = []
        def select(record, code, ids, **kwargs):
            seen.append(kwargs['candidate_id'])
            if kwargs['candidate_id'] == 'first':
                raise ValueError('candidate_entities_already_assigned_to_another_part')
            changed = copy.deepcopy(record)
            changed['business_parts'][1]['geometry_binding'] = {'status': 'bound', 'bound_by': 'auto', 'candidate_id': 'second'}
            return changed
        with patch.object(p, 'set_geometry_binding', side_effect=select):
            result = p.auto_bind_business_candidates(doc)
        self.assertEqual(['first', 'second'], seen)
        self.assertEqual(doc['business_parts'][0], result['business_parts'][0])
        self.assertEqual('second', result['business_parts'][1]['geometry_binding']['candidate_id'])
