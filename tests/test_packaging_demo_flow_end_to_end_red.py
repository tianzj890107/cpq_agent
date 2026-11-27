# -*- coding: utf-8 -*-
"""端到端守卫：图纸零件 → 演示输入 → BOM → 路线 → 成本（材料费必须真的算出来）。

用合成几何与合成材料（不是任何一份真实图纸），断言通用链路本身：
尺寸缺 → 材料行不出金额；补上演示输入后 → 材料行出金额且逐条披露 mock。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import tempfile
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import da_db, da_repo, da_seed_packaging as seed, kb_repo  # noqa: E402
from tech_app.backend.storage import meta_backend, store  # noqa: E402
from tech_app.backend.services import packaging_bom, packaging_cost  # noqa: E402
from tech_app.backend.services import packaging_demo_inputs, packaging_parts, packaging_route  # noqa: E402

PID = "pkgdemo00001"
REQ_NO = "REQ-PKG-D0001"
BOX = "YT-RB-01001-A"
REQ = {"industry": "packaging", "packaging_product_name": "礼盒", "packaging_category": "礼盒",
       "quote_quantity": 1000, "inner_length": 200, "inner_width": 150, "inner_height": 80,
       "fit_clearance": 1.8, "box_type": BOX, "closure_type": "天地盖", "v_groove": "是",
       "face_paper_gsm": 200, "lamination": "是", "hot_stamping": "是", "print_colors": "CMYK"}

GEOMETRY = {"parts": [
    {"component_id": "cmp:1", "unfolded_length_mm": 300.0, "unfolded_width_mm": 500.0,
     "size_source": "outline_loop", "entity_ids": ["ent:1"]},
    {"component_id": "cmp:2", "unfolded_length_mm": 120.0, "unfolded_width_mm": 260.0,
     "size_source": "outline_loop", "entity_ids": ["ent:2"]},
]}

BUSINESS = {"derived_from_drawing": True, "business_parts": [
    {"business_part_code": "DWG-BP01", "name": "左盖面纸",
     "reference": {"material_text": "225G铜版底PET光银", "quantity": 1},
     "geometry_binding": {"status": "bound", "bound_by": "auto", "size_confirmed": False,
                          "component_ids": ["cmp:1"], "entity_ids": ["ent:1"]}},
    {"business_part_code": "DWG-BP02", "name": "左盒盒背灰板",
     "reference": {"quantity": 1},
     "geometry_binding": {"status": "bound", "bound_by": "auto", "size_confirmed": False,
                          "component_ids": ["cmp:2"], "entity_ids": ["ent:2"]}},
]}


def _tables():
    materials = [dict(item["material"], industry="packaging", status="active")
                 for item in seed.MATERIALS]
    properties = [dict(prop, industry="packaging")
                  for item in seed.MATERIALS for prop in item.get("properties") or []]
    prices = [dict(item["price"], material_code=item["material"]["material_code"], price_id=index + 1)
              for index, item in enumerate(seed.MATERIALS)]
    tables = {
        "kb_packaging_box_type": [dict(r) for r in seed.BOX_TYPES],
        "kb_packaging_part_template": [dict(r) for r in seed.PART_TEMPLATES],
        "kb_packaging_process_template": [dict(r) for r in seed.PROCESS_TEMPLATES],
        "kb_packaging_insert_accessory": [dict(r) for r in seed.ACCESSORIES],
        "kb_packaging_logistics_rule": [dict(r) for r in seed.LOGISTICS_RULES],
        "kb_packaging_match_weight": [dict(r) for r in seed.MATCH_WEIGHTS],
        "kb_packaging_cost_formula": [dict(r) for r in seed.COST_FORMULAS],
        "kb_material": materials, "kb_material_property": properties, "kb_material_price": prices,
        "kb_cost_rate": [dict(r, industry="packaging") for r in seed.COST_RATES],
        "kb_cost_factor": [dict(r, industry="packaging") for r in seed.COST_FACTORS],
    }
    for name, attr in (("kb_packaging_cost_content", "COST_CONTENTS"),
                       ("kb_packaging_tooling_rule", "TOOLING_RULES")):
        extra = getattr(seed, attr, None)
        if extra:
            tables[name] = [dict(r) for r in extra]
    return tables


class DemoFlow(unittest.TestCase):
    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "demo.sqlite3"
        self._patch_da = mock.patch.object(config, "DA_DB_PATH", self.db_file)
        self._patch_da.start()
        da_db.init_db(self.db_file)
        self._backend = meta_backend._backend
        meta_backend._backend = meta_backend.JsonMetaBackend(pathlib.Path(tempfile.mkdtemp()))
        kb_repo._CACHE["version"] = "probe-version"
        kb_repo._CACHE["tables"] = _tables()

    def tearDown(self):
        meta_backend._backend = self._backend
        self._patch_da.stop()
        da_db.close_conn()
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._cache)

    def _prepare_project(self):
        store.save_requirement(PID, {"project_id": PID, "requirement_no": REQ_NO,
                                     "title": "演示装配用例", "status": "pending_confirmation",
                                     "data": dict(REQ)})
        da_repo.save_box_match({"project_id": PID, "requirement_no": REQ_NO,
                                "industry": "packaging", "engine_version": "packaging_match_v1",
                                "inputs": {}, "candidates": [], "missing_inputs": [],
                                "suggested_box_type": BOX})
        da_repo.update_box_match_decision(PID, REQ_NO, decision="confirmed",
                                         confirmed_box_type=BOX, confirmed_by="tester",
                                         confirmed_at=da_db.now())
        packaging_parts.save_parts(PID, dict(GEOMETRY))
        packaging_parts.save_business_parts(PID, dict(BUSINESS))
        packaging_bom.build_bom(PID, REQ_NO)
        packaging_route.build_route(PID, REQ_NO)
        packaging_route.confirm_route(PID, REQ_NO, actor={"username": "tester"})
        return packaging_cost.build_cost(PID, REQ_NO, actor={"username": "tester"})

    def test_a1_without_sizes_material_lines_have_no_amount(self):
        cost = self._prepare_project()
        codes = [str(gap.get("code")) for gap in cost["gaps"]]
        self.assertIn("part_size_missing", codes)
        self.assertEqual(0.0, round(cost["material_total"], 6))

    def test_a2_demo_inputs_make_material_lines_payable_and_disclosed(self):
        self._prepare_project()
        prepared = packaging_demo_inputs.prepare(PID, actor="tester", enable_demo=True)
        self.assertEqual(2, prepared["written"])
        packaging_bom.build_bom(PID, REQ_NO)
        packaging_route.build_route(PID, REQ_NO)
        packaging_route.confirm_route(PID, REQ_NO, actor={"username": "tester"})
        cost = packaging_cost.build_cost(PID, REQ_NO, actor={"username": "tester"})
        material_rows = [item for item in cost["items"]
                         if item.get("cost_category") == "material"
                         and str(item.get("part_code")) == "DWG-BP01"]
        self.assertTrue(material_rows, "第一件应当有材料行")
        self.assertTrue(any((item.get("amount") or 0) > 0 for item in material_rows),
                        "演示输入补上后材料行必须出金额")
        self.assertGreater(cost["material_total"], 0.0)
        assumptions = " ".join(cost.get("assumptions") or [])
        self.assertIn("演示", assumptions)

    def test_a3_requirement_flag_is_turned_on_for_the_demo_channel(self):
        self._prepare_project()
        packaging_demo_inputs.prepare(PID, actor="tester", enable_demo=True)
        data = (store.load_requirement(PID) or {}).get("data") or {}
        self.assertTrue(data.get("poc_demo") is True)
        doc = packaging_parts.load_business_parts(PID) or {}
        self.assertTrue(doc.get("poc_demo") is True)
        self.assertTrue(all(row.get("poc_inputs", {}).get("mock") is True
                            for row in doc.get("business_parts") or []))

    def test_first_batch_remains_confirmable_after_a_later_part_triggers_demo_preparation(self):
        from cpq_packaging_quote import PricingError, demo_completion_eligible, price
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from tech_app.backend import main
        from tech_app.backend.services import (integration, packaging_demo_flow, packaging_match, packaging_name_assist,
                                               packaging_process_confirmation)
        from tests.test_packaging_process_preflight_recovery_red import ACTOR, business_part

        self._prepare_project()
        requirement = store.load_requirement(PID)
        requirement["status"] = "approved"
        store.save_requirement(PID, requirement)
        approved = store.load_requirement(PID)
        first = business_part()
        latter = {**business_part(), "business_part_code": "FIXTURE-P02", "geometry_binding": {}}
        packaging_parts.save_business_parts(PID, {
            "derived_from_drawing": True,
            "business_parts": [first, latter],
        })
        plan = {"part_class": "packaging", "steps": [{"step_no": 10, "name": "模切", "standard_seconds": 30}]}
        recommendation = (plan, {"summary": {}})
        queued = []

        def finish_task(project, kind, job, **options):
            queued.append(job())
            return f"task-{len(queued)}"

        async def submit_both():
            for row in (first, latter):
                await main.packaging_business_part_process(
                    PID, row["business_part_code"], note="", attachments=[], user=ACTOR,
                )

        with (
            mock.patch.object(main, "_workflow_project", return_value={"project_id": PID}),
            mock.patch.object(main.tasks, "submit", side_effect=finish_task),
            mock.patch.object(main.process, "outline_process", return_value=recommendation),
            mock.patch.object(main.process, "compute", return_value={}),
            mock.patch.object(packaging_demo_inputs, "demo_process_plan", return_value=recommendation),
            mock.patch.object(packaging_name_assist, "prepare"),
        ):
            asyncio.run(submit_both())
            self.assertEqual(2, len(queued))
            self.assertTrue(all(item["plan"]["steps"] for item in queued))
            state = packaging_process_confirmation.snapshot(PID)
            current = packaging_parts.load_business_parts(PID)
            self.assertEqual(first, current["business_parts"][0])
            self.assertTrue(current["business_parts"][1]["poc_inputs"]["mock"])
            first_process = main.get_packaging_business_part_process(
                PID, first['business_part_code'], user=ACTOR,
            )
            self.assertFalse(first_process['business_stale'])

        self.assertTrue(state["ready"], f"本批已生成但确认缺少有效结论: {state['missing']}")
        self.assertEqual(approved, store.load_requirement(PID))
        integration.confirm_process(PID, ACTOR)
        # The original action can encounter a correct box on the approved requirement,
        # but no match decision and a missing internal measurement.
        pending_box_requirement = store.load_requirement(PID)
        pending_box_requirement['data'].pop('inner_width')
        store.save_requirement(PID, pending_box_requirement)
        approved = store.load_requirement(PID)
        da_db.execute('DELETE FROM wip_packaging_box_match WHERE project_id=? AND requirement_no=?', (PID, REQ_NO))
        app = FastAPI()
        endpoint = '/api/projects/{project_id}/integration/send-to-finance'
        app.add_api_route(endpoint, main.integration_send_to_finance, methods=['POST'])
        app.dependency_overrides[main.current_user] = lambda: ACTOR
        with (TestClient(app) as client,
              mock.patch.object(main.cost_flow, 'grant_task_project_access', return_value={}),
              mock.patch.object(integration.cpq_bridge, 'send_to_finance', return_value={
                  'task_id': 'fixture-finance', 'target_user_id': '9', 'target_username': 'fixture-fi',
              }) as send):
            request = {'target_type': 'user', 'target_user_id': '9'}
            for _ in range(2):
                response = client.post(endpoint.format(project_id=PID), json=request)
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual('fixture-finance', response.json()['finance']['task_id'])
            send.assert_called_once()
            payload = send.call_args.args[6]['tech_cost']['requirement']
            self.assertGreater(payload['inner_width'], 0)
        self.assertEqual(BOX, packaging_match.load_box_match(PID, REQ_NO)['confirmed_box_type'])
        packaging_match.run_box_match(PID, REQ_NO)
        self.assertFalse(packaging_match.load_box_match(PID, REQ_NO)['stale'])
        with mock.patch.object(packaging_demo_inputs, 'prepare_processes', side_effect=AssertionError('工艺已确认')):
            result = packaging_demo_flow.run(PID, actor={'username': 'fixture-fi', 'role': 'finance_manager'})
        self.assertEqual(2, result['cost_projection']['counts']['parts_costed'])
        package = result['handoff_package']
        self.assertTrue(demo_completion_eligible(package))
        self.assertFalse(package['cost']['readiness']['formal_ready'])
        self.assertFalse(package['publishable'])
        self.assertEqual(payload['inner_width'], package['requirement']['inner_width'])
        with self.assertRaises(PricingError):
            price(package, gross_margin_rate=.25, publish=True)
        self.assertTrue(packaging_process_confirmation.status(PID)['confirmed'])
        self.assertEqual(approved, store.load_requirement(PID))
        self.assertEqual(current, packaging_parts.load_business_parts(PID))

        for revision in range(packaging_parts.MAX_VERSIONS):
            current['business_parts'][1]['name'] = f'其他零件修改 {revision}'
            packaging_parts.save_business_parts(PID, current)
        unchanged = packaging_process_confirmation.snapshot(PID)
        self.assertNotIn(first['business_part_code'], unchanged['missing'])
        current['business_parts'][0]['geometry_binding']['bbox'][2] += 10
        packaging_parts.save_business_parts(PID, current)
        changed = packaging_process_confirmation.snapshot(PID)
        self.assertIn(first['business_part_code'], changed['missing'])

    def test_handoff_never_overrides_manual_rejection_or_relies_on_old_demo_assumptions(self):
        from tech_app.backend.services import packaging_match
        from tests.test_packaging_process_preflight_recovery_red import ACTOR

        self._prepare_project()
        for scenario in ('manual_return', 'incompatible', 'old_mock_only'):
            with self.subTest(scenario=scenario):
                decision = 'returned' if scenario == 'manual_return' else 'pending'
                da_repo.update_box_match_decision(PID, REQ_NO, decision=decision, confirmed_box_type='')
                before = da_repo.load_box_match(PID, REQ_NO)
                requirement = {'requirement_no': REQ_NO, 'status': 'approved', 'data': dict(REQ)}
                if scenario == 'incompatible':
                    requirement['data']['closure_type'] = '磁吸滑盖'
                if scenario == 'old_mock_only':
                    keys = ('box_type', 'inner_length', 'inner_width', 'inner_height')
                    requirement['data']['demo_assumptions'] = {
                        key: {'value': REQ[key], 'original_value': None, 'mock': True} for key in keys
                    }
                store.save_requirement(PID, requirement)
                saved = store.load_requirement(PID)
                with self.assertRaises(packaging_match.BoxMatchError):
                    packaging_match.confirm_box_for_finance_handoff(PID, saved, ACTOR)
                self.assertEqual(before, da_repo.load_box_match(PID, REQ_NO))
                self.assertEqual(saved, store.load_requirement(PID))

    def test_batch_workers_can_save_distinct_part_results_at_the_same_time(self):
        codes = [f'FIXTURE-P{i:02d}' for i in range(1, 29)]
        read_documents = packaging_parts._part_doc_items

        def slow_read(*args, **kwargs):
            items = read_documents(*args, **kwargs)
            time.sleep(0.002)
            return items

        def save_result(code):
            return packaging_parts.save_part_process(PID, {'part_code': code, 'plan': {'steps': [{'name': '模切'}]}})

        with mock.patch.object(packaging_parts, '_part_doc_items', side_effect=slow_read):
            with ThreadPoolExecutor(max_workers=4) as workers:
                list(workers.map(save_result, codes))
        self.assertTrue(all(packaging_parts.load_part_process(PID, code) for code in codes))

    def test_a4_demo_never_writes_a_confirmed_size(self):
        self._prepare_project()
        packaging_demo_inputs.prepare(PID, actor="tester", enable_demo=True)
        doc = packaging_parts.load_business_parts(PID) or {}
        for row in doc.get("business_parts") or []:
            self.assertNotIn("confirmed_size", row,
                             "演示输入不是人工确认尺寸，不许写 confirmed_size")

    def test_a5_native_per_part_processes_reach_material_and_labor_ledger(self):
        self._prepare_project()
        packaging_demo_inputs.prepare(PID, actor='tester', enable_demo=True)
        lookup={'status':'matched','match_method':'exact_name','routes':[{'steps':[
            {'line_number':10,'operation_name':'组装'}]}]}
        with mock.patch('tech_app.backend.services.da_process_routing.for_row',return_value=lookup):
            result=packaging_demo_inputs.prepare_processes(PID,actor='tester',
                run=mock.Mock(side_effect=AssertionError('native route must not call model')))
        self.assertTrue(result['ready'],result['failures'])
        self.assertEqual(2,len(result['results']))
        packaging_bom.build_bom(PID, REQ_NO)
        packaging_route.build_route(PID, REQ_NO)
        packaging_route.confirm_route(PID, REQ_NO, actor={'username':'tester'})
        cost=packaging_cost.build_cost(PID,REQ_NO,actor={'username':'tester'})
        self.assertGreater(cost['material_total'],0)
        # 现行费用口径：单件制造人工属于加工，不是整盒组装人工。
        self.assertGreater(cost['process_total'],0)
        manufacturing = [item for item in cost['items'] if item['cost_category']=='labor'
                         and json.loads(item.get('inputs_json') or '{}').get('scope') != 'project']
        self.assertTrue(manufacturing)
        self.assertGreater(sum(item['amount_with_loss'] for item in manufacturing),0)
        self.assertEqual(0,cost['labor_total'], '本夹具未生成跨零件组装，不应收组装人工')


if __name__ == "__main__":
    unittest.main()
