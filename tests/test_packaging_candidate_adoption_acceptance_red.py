"""验收补充：复用离线内存夹具，不连接线上、不修改实现方的原红测。"""
from tests.test_packaging_candidate_fields_adoption_red import (
    AdoptCase, candidate, empty_requirement_data,
)


class CandidateAdoptionAcceptance(AdoptCase):
    def test_conflicted_candidate_is_not_eligible_for_simple_adoption(self):
        _, req, _, pid = self.case()
        semantics = {'fields':{'box_type':candidate('round_tube',status='conflict')}}
        result = self.adopt(pid,['box_type'],semantics=semantics)
        self.assertNotIn('box_type',result['adopted'],
                         'Spec C1 只允许 needs_confirmation/confirmed，冲突不能被直接采纳')
        self.assertNotIn('box_type',self.data(req))

    def test_already_confirmed_cad_candidate_adoption_still_records_manual_source(self):
        _, req, _, pid = self.case()
        semantics = {'fields':{'box_type':candidate('round_tube',status='confirmed',
                                                   origin='confirmed_from_cad')}}
        result = self.adopt(pid,['box_type'],semantics=semantics)
        self.assertEqual(['box_type'],result['adopted'])
        self.assertEqual('manual',self.data(req)['field_sources']['box_type'])

    def test_mixed_batch_preserves_manual_conflict_and_adopts_other_key(self):
        data = empty_requirement_data()
        data['inner_length'] = 500
        data['field_sources']['inner_length'] = 'manual'
        _, req, _, pid = self.case(data)
        result = self.adopt(pid,['inner_length','inner_width'])
        self.assertEqual(500,self.data(req)['inner_length'])
        self.assertEqual(['inner_width'],result['adopted'])
        self.assertEqual('inner_length',result['conflicts'][0]['field'])
        self.assertTrue(self.data(req)['field_provenance']['inner_length']['alternatives'])
