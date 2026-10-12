# -*- coding: utf-8 -*-
"""端到端守卫：图纸零件 → 演示输入 → BOM → 路线 → 成本（材料费必须真的算出来）。

用合成几何与合成材料（不是任何一份真实图纸），断言通用链路本身：
尺寸缺 → 材料行不出金额；补上演示输入后 → 材料行出金额且逐条披露 mock。
"""
from __future__ import annotations

import pathlib
import tempfile
import sys
import unittest
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

    def test_a4_demo_never_writes_a_confirmed_size(self):
        self._prepare_project()
        packaging_demo_inputs.prepare(PID, actor="tester", enable_demo=True)
        doc = packaging_parts.load_business_parts(PID) or {}
        for row in doc.get("business_parts") or []:
            self.assertNotIn("confirmed_size", row,
                             "演示输入不是人工确认尺寸，不许写 confirmed_size")


if __name__ == "__main__":
    unittest.main()
