import json
import unittest
from unittest.mock import patch
from pathlib import Path
from tech_app.backend.models.ir import Part
from tech_app.backend.models.process import ProcessOutline
from tech_app.backend.services import packaging_manufacturing as service
from tech_app.backend.services import da_process_routing as bridge
from tech_app.backend.services.packaging_part_route_match import rank_route_features


class AnalogicalProcessTests(unittest.TestCase):
    def test_inner_outer_allowed_only_for_analogy(self):
        part={'name':'内盒面纸','material_text':'225g铜版纸'}
        candidates=[{'name':'外盒面纸','material_text':'225g铜版纸','code':'A'}]
        self.assertEqual([],rank_route_features(part,candidates)['candidates'])
        self.assertEqual(1,len(rank_route_features(part,candidates,analogy=True)['candidates']))

    def test_material_conflict_rejected_for_analogy(self):
        result=rank_route_features({'name':'底板','material_text':'灰板'},
            [{'name':'底板','material_text':'EVA','code':'A'}],analogy=True)
        self.assertEqual([],result['candidates'])

    def test_unmatched_bridge_loads_real_operation_rows(self):
        candidate={'name':'外盒面纸','material_text':'225g铜版纸','code':'A'}
        with patch.object(bridge,'_fetch_candidates',return_value={'ok':True,'status':'ok','candidates':[candidate]}),patch.object(bridge,'_fetch',return_value=self.lookup()) as fetch:
            result=bridge._similar_route({'name':'内盒面纸','material':'225g铜版纸'},{'status':'not_found'})
        self.assertTrue(fetch.called)
        self.assertEqual('A',result['reference_routes'][0]['reference_code'])
        self.assertEqual(2,len(result['reference_routes'][0]['steps']))

    def test_similarity_empty_model_fails(self):
        with self.assertRaisesRegex(ValueError,'packaging_process_empty'):
            service.outline(Part(part_id='R',name='圆件',industry='packaging'),lookup=self.lookup(),run=lambda *args:ProcessOutline())

    def test_exact_route_never_calls_model(self):
        data=self.lookup();data['match_method']='exact_name'
        plan,_=service.outline(Part(part_id='W',name='面纸',industry='packaging'),lookup=data,
            run=lambda *args:self.fail('精确命中不得重新生成'))
        self.assertEqual(['开料','浮雕击凸'],[s.name for s in plan.steps])

    def lookup(self):
        return {'status':'matched','match_method':'feature_similarity','routes':[{
            'approved':False,'steps':[{'line_number':10,'operation_name':'开料'},
                                     {'line_number':20,'operation_name':'浮雕击凸'}]}]}

    def test_similarity_adapts_instead_of_copying(self):
        with patch.object(service,'SYSTEM',service.SYSTEM):
            calls=[]
            def run(system,content,schema):
                calls.append(content)
                return ProcessOutline(steps=[{'step_no':10,'name':'模切','type':'other'}])
            plan,coverage=service.outline(Part(part_id='R',name='圆形内衬',industry='packaging'),lookup=self.lookup(),run=run)
        self.assertTrue(calls,'相似件必须让模型按本件调整')
        self.assertEqual(['模切'],[s.name for s in plan.steps])
        self.assertFalse(coverage['approved'])
        self.assertTrue(all(s.duration_min is None for s in plan.steps))

    def test_unmatched_receives_reference_operations(self):
        received=[]
        def run(system,content,schema):
            received.append(json.loads(content[0]['text']))
            return ProcessOutline(steps=[{'step_no':10,'name':'模切','type':'other'}])
        service.outline(Part(part_id='R',name='圆形内衬',industry='packaging'),
            lookup={'status':'not_found','reference_routes':self.lookup()['routes']},run=run)
        self.assertEqual('浮雕击凸',received[0]['reference_routes'][0]['steps'][1]['operation_name'])

    def test_frontend_renders_references(self):
        root=Path(__file__).resolve().parents[1]
        source=(root/'tech_app/frontend/inline-analysis.js').read_text()
        self.assertIn('reference_routes',source)
        self.assertIn('相似件工艺借鉴',source)
