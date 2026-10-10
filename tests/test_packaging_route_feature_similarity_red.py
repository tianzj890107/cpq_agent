import unittest
from unittest.mock import patch, MagicMock
from tech_app.backend.services import packaging_part_route_match as m
from tech_app.backend.services import da_process_routing as routing
import cpq_process_routing as bridge


class FeatureSimilarityTests(unittest.TestCase):
    def part(self, **extra):
        return dict(name='底盒底板', material_text='2mm灰板', length_mm=210,
                    width_mm=130, size_verified=True, **extra)

    def pool(self):
        return [{'name':'底板工艺路线','code':'P1','material_text':'2mm灰板',
                 'length_mm':130,'width_mm':210}]

    def test_real_feature_ranking_rotated_size(self):
        result=m.rank_route_features(self.part(),self.pool())
        self.assertEqual('matched',result['status'])
        self.assertEqual(1,result['candidates'][0]['scores']['size'])
        self.assertFalse(result['approved'])

    def test_known_material_conflict_rejects(self):
        pool=self.pool(); pool[0]['material_text']='EVA'
        self.assertEqual('unbound',m.rank_route_features(self.part(),pool)['status'])

    def test_explicit_missing_code_does_not_switch_identity(self):
        with patch.object(routing.cpq_kb_client,'_base',return_value='http://x'), \
             patch.object(routing.cpq_kb_client,'_internal_token',return_value='test'), \
             patch.object(routing,'_fetch',return_value={'ok':True,'status':'not_found','routes':[]}), \
             patch.object(routing,'_fetch_candidates') as candidates:
            result=routing.for_row({'name':'底板','product_item_code':'NO-SUCH-CODE'})
        candidates.assert_not_called()
        self.assertEqual('not_found',result['status'])

    def test_missing_feature_scores_are_not_renormalized(self):
        part=self.part();part['material_text']='';part['size_verified']=False
        result=m.rank_route_features(part,self.pool())
        self.assertEqual('unbound',result['status'])
        self.assertEqual(0,result['candidates'][0]['scores']['material'])

    def test_no_input_mutation(self):
        import copy
        pool=self.pool();original=copy.deepcopy(pool)
        m.rank_route_features(self.part(),pool)
        self.assertEqual(original,pool)

    def test_direction_conflict_rejects(self):
        part=self.part();part['name']='左盖面纸'
        pool=self.pool();pool[0]['name']='右盖面纸工艺路线'
        self.assertEqual('unbound',m.rank_route_features(part,pool)['status'])

    def test_ties_remain_ambiguous(self):
        pool=self.pool();pool.append(dict(pool[0],code='P2'))
        self.assertEqual('ambiguous',m.rank_route_features(self.part(),pool)['status'])

    def test_weak_size_never_gets_score(self):
        part=self.part();part['size_verified']=False
        item=m.rank_route_features(part,self.pool())['candidates'][0]
        self.assertEqual(0,item['scores']['size'])
        self.assertIn('verified_size',item['missing_features'])

    def test_material_description_not_imposition_or_process(self):
        f=bridge.material_features({'spec':'210x130mm','desc':'材料：2mm灰板；报价排版：900x600mm；工艺：模切'})
        self.assertEqual('2mm灰板',f['material_text'])
        self.assertEqual((210,130),(f['length_mm'],f['width_mm']))

    def test_for_row_really_calls_candidate_bridge_and_ranker(self):
        row={'name':'底盒底板','reference':{'material_text':'2mm灰板'},
             'confirmed_size':{'length_mm':210,'width_mm':130,'source':'drawing_dimension_manual'}}
        candidate={**self.pool()[0],'route':{'header':{'name':'底板工艺路线'},
                       'steps':[{'line_number':1,'operation_name':'模切'}],'approved':True}}
        with patch.object(routing.cpq_kb_client,'_base',return_value='http://x'), \
             patch.object(routing.cpq_kb_client,'_internal_token',return_value='test'), \
             patch.object(routing,'_fetch',return_value={'ok':True,'status':'not_found','routes':[]}), \
             patch.object(routing,'_fetch_candidates',return_value={'ok':True,'status':'ok','candidates':[candidate]}) as fetch:
            result=routing.for_row(row)
        fetch.assert_called_once()
        self.assertEqual('feature_similarity',result['match_method'])
        self.assertEqual('matched',result['status'])
        self.assertFalse(result['routes'][0]['approved'])

    def test_candidate_reader_uses_readonly_and_source_scope(self):
        cursor=MagicMock();cursor.fetchall.return_value=[]
        conn=MagicMock();conn.__enter__.return_value=conn
        conn.cursor.return_value.__enter__.return_value=cursor
        with patch.object(bridge.cpq_db,'connect',return_value=conn) as connect:
            result=bridge.packaging_candidates()
        connect.assert_called_once_with(readonly=True)
        self.assertEqual([],result['candidates'])
        query=cursor.execute.call_args_list[1]
        self.assertIn('data_source',repr(query.args[0]))
        self.assertTrue(query.args[1])

    def test_candidate_overflow_is_not_silent_truncation(self):
        cursor=MagicMock();cursor.fetchall.return_value=[{}]*201
        conn=MagicMock();conn.__enter__.return_value=conn
        conn.cursor.return_value.__enter__.return_value=cursor
        with patch.object(bridge.cpq_db,'connect',return_value=conn):
            result=bridge.packaging_candidates()
        self.assertEqual('candidate_limit_exceeded',result['status'])
        self.assertEqual([],result['candidates'])

    def test_imposition_dimensions_are_not_part_size(self):
        features=bridge.material_features({'spec':'按图纸','desc':'报价排版：900x600mm；材料：灰板'})
        self.assertIsNone(features['length_mm'])
        self.assertEqual('灰板',features['material_text'])

    def test_candidate_outage_does_not_look_like_empty_library(self):
        with patch.object(routing,'_fetch_candidates',side_effect=TimeoutError):
            result=routing._similar_route({'name':'底板'}, {'status':'not_found','routes':[]})
        self.assertEqual('unavailable',result['similarity_status'])

    def test_candidates_use_authenticated_bridge(self):
        import json
        response=MagicMock();response.read.return_value=json.dumps({'ok':True,'status':'ok','candidates':[]}).encode()
        with patch.object(routing.cpq_kb_client,'_base',return_value='http://x'), \
             patch.object(routing.cpq_kb_client,'_internal_token',return_value='test'), \
             patch.object(routing.urllib.request,'urlopen') as request:
            request.return_value.__enter__.return_value=response
            routing._fetch_candidates()
        req=request.call_args.args[0]
        self.assertIn('mode=packaging_candidates',req.full_url)
        self.assertIn('test',list(req.headers.values()))
