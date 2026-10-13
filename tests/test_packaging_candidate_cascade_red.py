import unittest
from tech_app.backend.services import packaging_parts as p
from tech_app.backend.services import packaging_part_visual_review as v


class CandidateCascadeTests(unittest.TestCase):
    def test_browser_and_backend_keep_uncapped_ranking_but_bounded_display(self):
        import json
        from tests.test_packaging_candidate_confidence_default_red import run_helpers
        candidates=[
            {'id':'group:a','entity_ids':['a'],'component_ids':['a'],
             'geometry_status':'ambiguous','anchor_in_region':True,'dimension_spatial':True,
             'evidence_reasons':['compound_name_anchor']},
            {'id':'layout:z','entity_ids':['b'],'component_ids':['b'],
             'geometry_status':'supported','dimension_spatial':True,
             'evidence_reasons':['layout_frame','raw_cad_entities','caption_column_owner','complete_closed_outline']}]
        output=json.loads(run_helpers('const items='+json.dumps(candidates)+';'
            'process.stdout.write(JSON.stringify(packagingRankedCandidates(items).map(x=>'
            '({id:x.candidate.id,display:x.confidence,raw:packagingCandidateConfidence(x.candidate,{uncapped:true})}))));'))
        self.assertEqual('layout:z',output[0]['id'])
        for item in output:
            candidate=next(c for c in candidates if c['id']==item['id'])
            self.assertEqual(p.candidate_confidence_percent(candidate),item['display'])
            self.assertEqual(p.candidate_confidence_percent(candidate,capped=False),item['raw'])

    def test_single_name_does_not_merge_closed_shapes_from_different_views(self):
        ir={'texts':[{'entity_id':'caption','raw_text':'名称：部件甲','position':[0,90],'height':5}],
            'entities':[]}
        regions=[dict(region_id=code,component_ids=[code],entity_ids=[code],
                      bbox=box,substantial=True,outline_status='closed',
                      name_anchor_entity_id='caption') for code,box in
                 [('one',[0,100,40,150]),('other',[0,0,40,30])]]
        groups=v._compound_named_groups(ir,regions,{'one':'top','other':'bottom'},[])
        self.assertFalse(any(set(g['component_ids'])=={'one','other'} for g in groups))

    def test_display_score_cap_does_not_turn_evidence_into_id_tie(self):
        whole={'id':'layout:z','entity_ids':['e'],'component_ids':['a'],'bbox':[0,0,10,20],
               'geometry_status':'supported','dimension_spatial':True,'name_anchor_entity_id':'caption',
               'evidence_reasons':['layout_frame','raw_cad_entities','caption_column_owner','complete_closed_outline']}
        compound={'id':'group:a','entity_ids':['e','foreign'],'component_ids':['a','b'],'bbox':[0,0,10,80],
                  'geometry_status':'ambiguous','anchor_in_region':True,'dimension_spatial':True,
                  'name_anchor_entity_id':'caption','evidence_reasons':['compound_name_anchor']}
        self.assertEqual(85,p.candidate_confidence_percent(whole))
        self.assertEqual(85,p.candidate_confidence_percent(compound))
        doc={'derived_from_drawing':True,'geometry_evidence':{'components':[
             {'component_id':'a','entity_ids':['e'],'bbox':[0,0,10,20]},
             {'component_id':'b','entity_ids':['foreign'],'bbox':[0,60,10,80]}]},
             'business_parts':[{'business_part_code':'target','name':'甲','sequence_no':1,
                                'geometry_binding':{'candidates':[compound,whole]}}]}
        result=p.auto_bind_business_candidates(doc)
        self.assertEqual('layout:z',result['business_parts'][0]['geometry_binding']['candidate_id'])

    def test_claim_conflict_cannot_fall_back_to_fragment_of_full_candidate(self):
        whole={'id':'layout:whole','entity_ids':['owned','free'],'component_ids':['a','b'],
               'bbox':[0,0,30,20],'geometry_status':'supported','dimension_spatial':True,
               'name_anchor_entity_id':'caption','evidence_reasons':['layout_frame','raw_cad_entities','caption_column_owner']}
        fragment={'id':'region:fragment','entity_ids':['free'],'component_ids':['b'],
                  'bbox':[20,0,30,20],'geometry_status':'supported'}
        doc={'derived_from_drawing':True,'geometry_evidence':{'components':[
             {'component_id':'a','entity_ids':['owned'],'bbox':[0,0,10,20]},
             {'component_id':'b','entity_ids':['free'],'bbox':[20,0,30,20]}]},'business_parts':[
             {'business_part_code':'manual','sequence_no':1,'geometry_binding':{'status':'bound',
              'bound_by':'manual','entity_ids':['owned'],'component_ids':['a']}},
             {'business_part_code':'target','name':'甲','sequence_no':2,
              'geometry_binding':{'candidates':[fragment,whole]}}]}
        result=p.auto_bind_business_candidates(doc)
        binding=result['business_parts'][1]['geometry_binding']
        self.assertNotEqual('bound',binding['status'])
        self.assertEqual(doc['business_parts'][0],result['business_parts'][0])
