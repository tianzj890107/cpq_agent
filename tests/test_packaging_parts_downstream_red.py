"""红测：图纸零件跑通工艺推荐与成本测算（第 3 层）。

Spec：`docs/specs/packaging-parts-downstream-process-and-cost.md`
前置：本层**假设第 1 层（真实轮廓）与第 2 层（可选中 + 右栏面板）已实现**。

**现状缺口（代码事实）**：`POST /parts/{part_id}/process` 第一句 `store.load_ir()`，
图纸项目为空 → 404「请先解析(parse)得到 IR」；即便有 IR，也找不到 `DWG-Pxx`。
前端工艺推荐判的是 `currentIR.parts` → 图纸项目恒为「还没有零件，请先完成图纸解析。」，
与左栏 64 行自相矛盾。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_parts  # noqa: E402

MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

PART_ID_NAMESPACE = "packaging_parts/1"
REJECT_CODES = ("PACKAGING_PART_NOT_CLOSED", "PACKAGING_PART_MATERIAL_UNKNOWN",
                "PACKAGING_PART_NOT_FOUND")
PROCESS_PATH = "/api/projects/{pid}/requirement/packaging-parts/{part_code}/process"
COST_PATH = "/api/projects/{pid}/requirement/packaging-parts/{part_code}/cost"


def row(**over):
    base = {"part_code": "DWG-P01", "part_id": "DWG-P01", "name": "图纸零件 P01",
            "unfolded_length_mm": 100.0, "unfolded_width_mm": 50.0, "area_mm2": 5000.0,
            "layers": ["CUT"], "role": "cut", "component_id": "cmp:1",
            "entity_ids": ["ent:model:R-0"], "evidence_refs": ["ev:E:R-0"],
            "repeat_of": "", "size_source": "closed_outline", "outline_status": "closed",
            "outline_reason": "", "outline": {"points": [[0, 0], [100, 0], [100, 50], [0, 50]],
                                              "closed": True, "area_mm2": 5000.0,
                                              "entity_ids": ["ent:model:R-0"]},
            "thickness_mm": 2.0, "material": {"spec": "灰板 2.0mm"}}
    base.update(over)
    return base


class DownstreamCase(unittest.TestCase):
    def module(self):
        for name in ("as_ir_part", "processability"):
            self.assertTrue(callable(getattr(packaging_parts, name, None)),
                            "packaging_parts 缺 %s()（Spec §3）" % name)
        return packaging_parts


# --------------------------------------------------------------------------- #
# A 组：id 映射
# --------------------------------------------------------------------------- #
class AIdMapping(DownstreamCase):
    def test_a1_namespace_constant(self):
        self.assertEqual(packaging_parts.PART_ID_NAMESPACE, PART_ID_NAMESPACE,
                         "必须逐字等于 Spec §2 的 part_id_namespace")

    def test_a2_extract_stamps_part_id(self):
        import json
        fixture = json.loads((ROOT / "tests" / "fixtures" / "cad_ir" / "parts_panels.json")
                             .read_text(encoding="utf-8"))
        doc = packaging_parts.extract(fixture)
        self.assertEqual(doc.get("part_id_namespace"), PART_ID_NAMESPACE,
                         "零件文档顶层必须给 part_id_namespace")
        for entry in doc["parts"]:
            self.assertEqual(entry.get("part_id"), entry["part_code"],
                             "part_id 必须等于 part_code（本版唯一口径）")

    def test_a3_part_code_is_untouched(self):
        self.assertEqual(packaging_parts.PART_CODE_FORMAT, "DWG-P%02d",
                         "part_code 形状不许改")


# --------------------------------------------------------------------------- #
# B 组：as_ir_part
# --------------------------------------------------------------------------- #
class BAsIrPart(DownstreamCase):
    def test_b1_closed_with_material_makes_a_plate(self):
        part = self.module().as_ir_part(row())
        self.assertEqual(part.part_id, "DWG-P01")
        self.assertEqual(part.name, "图纸零件 P01")
        self.assertEqual(part.quantity, 1)
        self.assertEqual(len(part.features), 1, "闭合 + 厚度已知 → 应给一个板件基体")
        self.assertEqual(part.features[0].type, "plate")
        self.assertGreaterEqual(part.confidence, 0.6)

    def test_b2_no_thickness_means_no_feature_and_low_confidence(self):
        part = self.module().as_ir_part(row(thickness_mm=None, material=None))
        self.assertEqual(part.features, [], "厚度未知不许造特征（不许猜）")
        self.assertLessEqual(part.confidence, 0.4)

    def test_b3_open_part_has_no_feature(self):
        part = self.module().as_ir_part(row(outline_status="open", outline_reason="no_closed_loop"))
        self.assertEqual(part.features, [])
        self.assertLessEqual(part.confidence, 0.4)

    def test_b4_provenance_carries_size_source(self):
        part = self.module().as_ir_part(row())
        blob = part.model_dump_json()
        self.assertIn("closed_outline", blob, "留痕里必须带上 size_source")
        self.assertIn("closed", blob, "留痕里必须带上 outline_status")


# --------------------------------------------------------------------------- #
# C 组：processability
# --------------------------------------------------------------------------- #
class CProcessability(DownstreamCase):
    def test_c1_open_part_is_rejected(self):
        verdict = self.module().processability(row(outline_status="open"))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["code"], "PACKAGING_PART_NOT_CLOSED")
        self.assertIn("outline", verdict["missing_variables"])

    def test_c2_missing_material_is_rejected_with_field_list(self):
        verdict = self.module().processability(row(thickness_mm=None, material=None))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["code"], "PACKAGING_PART_MATERIAL_UNKNOWN")
        self.assertIn("thickness_mm", verdict["missing_variables"])
        self.assertIn("material", verdict["missing_variables"])

    def test_c3_ready_part_passes(self):
        verdict = self.module().processability(row())
        self.assertTrue(verdict["ok"], "闭合 + 材料齐 → 必须可算")
        self.assertEqual(verdict["code"], "")
        self.assertTrue(verdict.get("part"), "通过时必须给出可直接交给 outline_process 的 Part")

    def test_c4_reject_codes_are_frozen(self):
        self.assertEqual(tuple(packaging_parts.PROCESS_REJECT_CODES), REJECT_CODES,
                         "PROCESS_REJECT_CODES 必须逐字等于 Spec §3")


# --------------------------------------------------------------------------- #
# D 组：路由
# --------------------------------------------------------------------------- #
class DRoutes(DownstreamCase):
    def test_d1_routes_are_registered(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for path in (PROCESS_PATH, COST_PATH):
            self.assertIn(path, source, "main.py 缺路由 %s" % path)
        import tech_app.backend.main as main
        paths = {route.path for route in main.app.routes}
        for path in (PROCESS_PATH, COST_PATH):
            self.assertIn(path, paths, "路由未注册：%s" % path)

    def test_d2_write_roles_are_reused(self):
        import importlib
        match = importlib.import_module("tech_app.backend.services.packaging_match")
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        start = source.index(PROCESS_PATH)
        block = source[start:start + 5000]
        self.assertIn("BOX_MATCH_DECIDE_ROLES", block,
                      "写权限必须引用 packaging_match.BOX_MATCH_DECIDE_ROLES，不另抄一份")
        self.assertIs(packaging_parts.PROCESS_REJECT_CODES[0],
                      match.BOX_MATCH_DECIDE_ROLES and packaging_parts.PROCESS_REJECT_CODES[0])

    def test_d3_route_asks_processability_first(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        start = source.index(PROCESS_PATH)
        block = source[start:start + 5000]
        self.assertIn("processability", block,
                      "路由必须先调 processability 再谈跑工艺（缺料不许硬算）")


# --------------------------------------------------------------------------- #
# E 组：既有技术路由不许受影响
# --------------------------------------------------------------------------- #
class EOldRouteUntouched(DownstreamCase):
    def test_e1_old_process_route_still_registered(self):
        import tech_app.backend.main as main
        paths = {route.path for route in main.app.routes}
        self.assertIn("/api/projects/{project_id}/parts/{part_id}/process", paths,
                      "既有技术路由必须还在")

    def test_e2_old_route_does_not_touch_packaging_parts(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        start = source.index('@app.post("/api/projects/{project_id}/parts/{part_id}/process")')
        end = source.index('@app.get("/api/projects/{project_id}/parts/{part_id}/process")', start)
        block = source[start:end]
        self.assertNotIn("packaging_parts", block,
                         "既有技术路由不得被改成走图纸零件文档")


# --------------------------------------------------------------------------- #
# F 组：前端
# --------------------------------------------------------------------------- #
class FFrontend(DownstreamCase):
    def _js(self):
        return APP_JS.read_text(encoding="utf-8", errors="replace")

    def test_f1_panel_has_two_buttons(self):
        source = self._js()
        self.assertIn("生成工艺推荐", source)
        self.assertIn("成本测算", source)
        self.assertIn("packagingPartProcess", source,
                      "面板需要一个稳定的按钮/容器 id（packagingPartProcess）")

    def test_f2_grey_reason_is_written(self):
        source = self._js()
        self.assertIn("闭合轮廓", source, "灰按钮必须写明『未找到闭合轮廓』")
        self.assertIn("缺材料", source, "灰按钮必须写明缺材料/厚度")

    def test_f3_reuses_inline_analysis(self):
        source = self._js()
        marker = source.find("packagingPartProcess")
        self.assertGreater(marker, 0)
        window = source[max(0, marker - 2000):marker + 4000]
        self.assertIn("CadInlineAnalysis", window,
                      "必须复用既有 CadInlineAnalysis 渲染，不新建第二套")

    def test_f4_batch_entry_counts_packaging_parts(self):
        source = self._js()
        marker = source.find("function startAllPartProcesses")
        self.assertGreater(marker, 0, "板级批量入口必须还在")
        window = source[marker:marker + 1500]
        self.assertIn("currentPackagingParts", window,
                      "图纸项目下批量入口必须按零件文档计数，而不是 currentIR.parts")


if __name__ == "__main__":
    unittest.main(verbosity=2)
