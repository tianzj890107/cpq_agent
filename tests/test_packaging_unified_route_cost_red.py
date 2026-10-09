import unittest
from unittest.mock import patch
from tech_app.backend.services import packaging_route as route, packaging_cost as cost
from tests.test_packaging_cost_engine_red import CostCase, PID, REQ_NO

class UnifiedTests(unittest.TestCase):
    def test_reference_usage_preserves_zero_not_fallback(self):
        from tech_app.backend.services import packaging_process_instances as m, packaging_parts as parts
        for usage in (0,3):
            with patch.object(parts,'load_business_parts',return_value={'business_parts':[
                {'business_part_code':'A','reference':{'quantity':usage}}]}), \
                 patch.object(parts,'load_part_process',return_value={'plan':{'steps':[{'name':'模切'}]}}):
                self.assertEqual(m.collect('isolated')['records'][0]['usage_qty'],usage)
    def test_all_saved_results_stale_cannot_fallback_to_templates(self):
        from tech_app.backend.services import packaging_process_instances as m, packaging_parts as parts
        with patch.object(parts,'load_business_parts',return_value={'business_parts_hash':'new',
             'business_parts':[{'business_part_code':'A'}]}), \
             patch.object(parts,'load_part_process',return_value={'business_parts_hash':'old',
             'plan':{'steps':[{'name':'模切'}]}}):
            result=m.collect('isolated')
        self.assertTrue(result['active'])
        self.assertEqual(result['missing'],['A'])
    def test_sections_keep_each_quantity_and_billing_identity(self):
        from tech_app.backend.services import packaging_process_instances as m
        steps=m.expand([{'part_code':'A','usage_qty':99,'sections':[
            {'section_id':'outer','quantity':2,'plan':{'steps':[{'name':'模切'}]}},
            {'section_id':'inner','quantity':3,'plan':{'steps':[{'name':'模切'}]}}]}])
        self.assertEqual([s['section_id'] for s in steps],['outer','inner'])
        self.assertEqual([s['usage_qty'] for s in steps],[2,3])
    def test_same_name_instances_retained(self):
        from tech_app.backend.services import packaging_process_instances as m
        rows=[{'part_code':'A','usage_qty':2,'plan':{'steps':[{'step_no':1,'name':'模切'}, {'step_no':2,'name':'模切'}]}},
              {'part_code':'B','usage_qty':1,'plan':{'steps':[{'step_no':1,'name':'模切'}]}}]
        steps=m.expand(rows)
        self.assertEqual(len(steps),3)
        self.assertEqual([s['part_code'] for s in steps],['A','A','B'])
        self.assertEqual([s['formula_code'] for s in steps],['PKG-C-DIE-CUT']*3)
    def test_model_duration_not_standard_time(self):
        from tech_app.backend.services import packaging_process_instances as m
        s=m.expand([{'part_code':'A','plan':{'steps':[{'name':'组装','duration_min':5}]}}])[0]
        self.assertIsNone(s['standard_seconds'])
        self.assertTrue(s['needs_standard_time'])
    def test_aliases_and_unknown(self):
        from tech_app.backend.services import packaging_process_instances as m
        self.assertEqual(m.billing({'step_name':'覆哑膜'})['formula_code'],'PKG-C-LAMINATION')
        self.assertEqual(m.billing({'step_name':'机裱'})['formula_code'],'PKG-C-MOUNTING')
        self.assertEqual(m.billing({'step_name':'丝印UV'})['gap'],'process_formula_missing')
    def test_explicit_formula_priority(self):
        from tech_app.backend.services import packaging_process_instances as m
        b=m.billing({'step_name':'印刷','formula_code':'PKG-C-PRINT-UV'})
        self.assertEqual(b['cost_category'],'print_uv')
        self.assertTrue(b['includes_labor'])
    def test_route_consumes_part_results(self):
        from pathlib import Path
        s=Path(route.__file__).read_text()
        self.assertIn('packaging_process_instances.collect',s)
        self.assertIn('part_process_hash',s)
    def test_cost_consumes_instances_not_deduped_names(self):
        from pathlib import Path
        s=Path(cost.__file__).read_text()
        self.assertNotIn('for name in process_names:',s)
        self.assertIn('packaging_process_instances.billing',s)

class UnifiedIntegration(CostCase):
    def records(self):
        return [{'part_code':'A','usage_qty':2,'cost_parameters':{'imposition_count':2},
                 'plan':{'steps':[{'step_no':1,'name':'模切'}, {'step_no':2,'name':'模切'}]}},
                {'part_code':'B','usage_qty':1,'cost_parameters':{'imposition_count':1},
                 'plan':{'steps':[{'step_no':1,'name':'模切'}]}}]

    def source(self, records=None):
        from tech_app.backend.services import packaging_process_instances as m
        records = self.records() if records is None else records
        return {'records':records,'missing':[],'active':True,'hash':m.fingerprint(records)}

    def test_roundtrip_confirm_cost_three_operations(self):
        from tech_app.backend.services import packaging_process_instances as m
        from tech_app.backend.storage import da_repo
        import json
        self.prepare()
        with patch.object(m,'collect',return_value=self.source()):
            built=route.build_route(PID,REQ_NO)
            self.assertEqual(len(built['steps']),3)
            self.assertEqual(route.validate_order(built['steps']),[])
            self.assertEqual([s['part_code'] for s in da_repo.load_packaging_route_steps(PID,REQ_NO)],['A','A','B'])
            confirmed=route.confirm_route(PID,REQ_NO,actor=self.actor())
            self.assertFalse(confirmed['stale'])
            result=cost.compute_project(PID,REQ_NO)
            items=[i for i in result['items'] if i['formula_code']=='PKG-C-DIE-CUT']
            self.assertEqual(len(items),3)
            self.assertEqual([i['part_code'] for i in items],['A','A','B'])
            self.assertTrue(all(i['amount'] is not None for i in items))
            self.assertEqual(json.loads(items[0]['inputs_json'])['usage_qty'],2)
            self.assertEqual(len({i['source_ref'] for i in items}),3)
            snapshot=route.route_versions(PID,REQ_NO)[-1]
            self.assertEqual(len(json.loads(snapshot['steps_json'])),3)

    def test_changed_part_process_blocks_cost_and_reconfirm(self):
        from tech_app.backend.services import packaging_process_instances as m
        self.prepare()
        with patch.object(m,'collect',return_value=self.source()):
            route.build_route(PID,REQ_NO)
            route.confirm_route(PID,REQ_NO,actor=self.actor())
        changed=self.records()
        changed[0]['usage_qty']=3
        with patch.object(m,'collect',return_value=self.source(changed)):
            self.assertTrue(route.load_route(PID,REQ_NO)['stale'])
            with self.assertRaises(route.RouteError):
                route.confirm_route(PID,REQ_NO,actor=self.actor())
            with self.assertRaises(cost.CostError):
                cost.compute_project(PID,REQ_NO)

    def test_missing_part_rejects_complete_route(self):
        from tech_app.backend.services import packaging_process_instances as m
        self.prepare()
        source=self.source()
        source['missing']=['C']
        with patch.object(m,'collect',return_value=source), self.assertRaises(route.RouteError) as got:
            route.build_route(PID,REQ_NO)
        self.assertEqual(got.exception.code,'part_process_incomplete')

    def test_missing_part_imposition_not_defaulted(self):
        from tech_app.backend.services import packaging_process_instances as m
        self.prepare()
        records=self.records()
        records[0]['cost_parameters']={}
        with patch.object(m,'collect',return_value=self.source(records)):
            route.build_route(PID,REQ_NO)
            route.confirm_route(PID,REQ_NO,actor=self.actor())
            result=cost.compute_project(PID,REQ_NO)
        items=[i for i in result['items'] if i.get('part_code')=='A']
        self.assertTrue(all(i['amount'] is None for i in items))
        self.assertIn('process_parameters_missing',[g['code'] for g in result['gaps']])

    def test_all_28_part_results_kept(self):
        from tech_app.backend.services import packaging_parts as parts
        for n in range(28):
            parts.save_part_process(PID,{'part_code':str(n),'parts_id':'v1','plan':{'steps':[{'name':'模切'}]}})
        for n in range(28):
            self.assertTrue(parts.load_part_process(PID,str(n)).get('plan'))

    def test_machine_mounting_not_double_charged_labor(self):
        from tech_app.backend.services import packaging_process_instances as m
        self.prepare()
        records=[{'part_code':'A','cost_parameters':{'imposition_count':1},'plan':{'steps':[{'name':'机裱','standard_seconds':100}]}}]
        with patch.object(m,'collect',return_value=self.source(records)):
            route.build_route(PID,REQ_NO)
            route.confirm_route(PID,REQ_NO,actor=self.actor())
            result=cost.compute_project(PID,REQ_NO)
        self.assertEqual(len([i for i in result['items'] if i.get('part_code')=='A' and i['cost_category']=='mounting']),1)
        self.assertFalse([i for i in result['items'] if i.get('part_code')=='A' and i['cost_category']=='labor'])
