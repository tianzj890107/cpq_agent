"""红测：业务部件的 CAD 平面图必须真的画得出来（证据层要带绘图坐标）。

Spec：`docs/specs/packaging-cad-plan-drawing-coordinates.md`
依赖口径：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§6.2 平面图交互）、
          `docs/specs/packaging-business-parts-binding-size-source.md`（`bbox` 的绑定语义）

现状缺口（2026-09-22 实测，真样本 `裕同包装项目-待开发/酒盒.dwg`）：

  · `extract()` 的 263 件**每一件**都在 `outline.bbox` 里带着图纸坐标包络；
  · `geometry_evidence_of()` 只透传 `row.get("bbox")` —— 而 `parts` 行**没有**这个键 →
    证据层 `bbox` 恒为 `null` → `packagingCadPlanComponentSvg()` 每件都返回空串 →
    2.1 右栏「CAD 平面图」画 0 个矩形（一块空白画布，也不是空态文案）；
  · 263 件的 `outline.bbox` 边长与 `unfolded_length_mm/width_mm` **逐件相等**（误差 > 0.01mm 的 0 件）。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_parts  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "cad_ir"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
DWG = SAMPLES_DIR / "酒盒.dwg"
APP_JS_PATH = ROOT / "tech_app" / "frontend" / "app.js"
APP_JS = APP_JS_PATH.read_text(encoding="utf-8")


def fixture_ir() -> dict:
    return json.loads((FIXTURES / "parts_panels.json").read_text(encoding="utf-8"))


def js_body(source: str, signature: str) -> str:
    """按大括号配平截一个 JS 函数体（找不到就返回空串，让断言自己报错）。"""
    start = source.find(signature)
    if start < 0:
        return ""
    index = source.find("{", start)
    if index < 0:
        return ""
    depth = 0
    for pos in range(index, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[index:pos + 1]
    return ""


# --------------------------------------------------------------------------- #
# A 组：证据层必须带绘图坐标，且不许污染 `bbox`
# --------------------------------------------------------------------------- #
class AEvidenceCarriesDrawingBox(unittest.TestCase):
    def setUp(self):
        self.doc = packaging_parts.extract(fixture_ir())
        self.evidence = packaging_parts.geometry_evidence_of(self.doc)
        self.by_id = {row.get("component_id"): row for row in self.doc["parts"]}

    def test_a1_every_component_carries_its_drawing_box(self):
        components = self.evidence.get("components") or []
        self.assertTrue(components, "证据层一个分量都没有")
        for row in components:
            part = self.by_id.get(row.get("component_id"))
            self.assertIsNotNone(part, "证据层出现了零件表里没有的分量：%s" % row.get("component_id"))
            self.assertIn("drawing_bbox", row,
                          "证据层分量没有 drawing_bbox：平面图只能画空白（Spec §C1）")
            expected = (part.get("outline") or {}).get("bbox")
            self.assertEqual(expected, row.get("drawing_bbox"),
                             "%s 的 drawing_bbox 必须逐字等于零件行的 outline.bbox"
                             % row.get("component_id"))

    def test_a2_binding_bbox_is_not_polluted_by_drawing_coordinates(self):
        for row in self.evidence.get("components") or []:
            part = self.by_id.get(row.get("component_id"))
            self.assertEqual(part.get("bbox"), row.get("bbox"),
                             "`bbox` 是绑定判据的兜底来源：不许把绘图坐标写进去（Spec §C2）")

    def test_a3_existing_keys_and_totals_survive(self):
        for row in self.evidence.get("components") or []:
            for key in ("component_id", "entity_ids", "bbox", "layers", "role",
                        "geometry_component_ref", "unfolded_length_mm", "unfolded_width_mm",
                        "outline_status", "size_source"):
                self.assertIn(key, row, "证据层丢了既有键 %s" % key)
        self.assertEqual(4, self.evidence.get("kept_component_total"))


# --------------------------------------------------------------------------- #
# B 组：前端只能有一个"取画图框"的入口
# --------------------------------------------------------------------------- #
class BPlanViewerUsesDrawingBox(unittest.TestCase):
    def test_b1_single_entry_prefers_drawing_bbox(self):
        body = js_body(APP_JS, "function packagingCadPlanComponentBox(")
        self.assertTrue(body, "缺少 packagingCadPlanComponentBox()：取画图框必须只有一个入口")
        self.assertIn("drawing_bbox", body, "取画图框必须先看 drawing_bbox")
        self.assertIn("bbox", body, "还要保留 bbox 兜底（老文档/技术链路）")

    def test_b2_component_svg_draws_from_that_entry(self):
        body = js_body(APP_JS, "function packagingCadPlanComponentSvg(")
        self.assertTrue(body, "packagingCadPlanComponentSvg() 找不到（签名变了？）")
        self.assertIn("packagingCadPlanComponentBox", body,
                      "画矩形必须走统一起点，不许再直接读 component.bbox")
        self.assertNotIn("component && component.bbox", body)

    def test_b3_view_range_uses_the_same_entry(self):
        body = js_body(APP_JS, "function renderPackagingCadPlan(")
        self.assertTrue(body, "renderPackagingCadPlan() 找不到（签名变了？）")
        self.assertIn("packagingCadPlanComponentBox", body,
                      "整张图的 viewBox 范围也要按同一入口算，否则缩放与图元对不上")

    def test_b4_no_coordinates_is_a_stated_empty_state(self):
        self.assertIn("PACKAGING_CAD_PLAN_NO_COORDS", APP_JS,
                      "有图元但一个坐标都没有时必须给空态文案（Spec §C4）")
        body = js_body(APP_JS, "function renderPackagingCadPlan(")
        self.assertIn("PACKAGING_CAD_PLAN_NO_COORDS", body,
                      "空态文案要在 renderPackagingCadPlan() 里真的用上，不是只声明常量")

    def test_b5_app_js_still_parses(self):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("未安装 node，跳过语法检查")
        completed = subprocess.run([node, "--check", str(APP_JS_PATH)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(0, completed.returncode, "app.js 语法错误：\n%s" % completed.stderr)


# --------------------------------------------------------------------------- #
# C 组：护栏（现状即绿，改完必须仍绿）
# --------------------------------------------------------------------------- #
class CGuards(unittest.TestCase):
    def test_c1_binding_does_not_read_the_drawing_box(self):
        plan = packaging_parts.bind_geometry(
            [{"business_part_code": "P01", "length_mm": 100.0, "width_mm": 50.0}],
            [{"component_id": "cmp:a", "entity_ids": [], "drawing_bbox": [0, 0, 100.0, 50.0],
              "outline_status": "unavailable", "size_source": "dwg_outline"}])
        row = (plan.get("bindings") or [{}])[0]
        self.assertEqual("unbound", row.get("status"),
                         "绘图坐标不是件尺寸：单位没确认时不许拿它去对业务尺寸（Spec §C2）")
        self.assertIn("size_unknown", row.get("reasons") or [])

    def test_c2_plan_viewer_interaction_stays(self):
        for token in ("packagingCadPlanViewBox", "fitPackagingCadPlan",
                      "highlightPackagingBusinessPart", "data-component-id"):
            self.assertIn(token, APP_JS, "平面图的既有交互丢了：%s" % token)


# --------------------------------------------------------------------------- #
# D 组：真样本（本机有样本 + dwg2dxf 才跑）
# --------------------------------------------------------------------------- #
class DRealSample(unittest.TestCase):
    def setUp(self):
        if not DWG.exists():
            self.skipTest("真实样本不在本机：%s" % DWG)
        if not shutil.which("dwg2dxf"):
            self.skipTest("本机没有 libredwg 的 dwg2dxf")

    def real_geometry(self) -> dict:
        cache = pathlib.Path(tempfile.mkdtemp()) / "real.dxf"
        subprocess.run([shutil.which("dwg2dxf"), "-y", "-o", str(cache), str(DWG)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from tech_app.backend.services import cad_ir
        ir = cad_ir.parse_dxf(cache.read_bytes(), filename=cache.name,
                              source={"kind": "dxf_2d", "attachment_name": "酒盒.dwg"})
        return packaging_parts.extract(ir)

    def test_d1_real_sample_has_drawing_boxes_for_every_part(self):
        doc = self.real_geometry()
        evidence = packaging_parts.geometry_evidence_of(doc)
        total = len(evidence.get("components") or [])
        with_box = [c for c in evidence.get("components") or [] if c.get("drawing_bbox")]
        self.assertGreaterEqual(total, 200, "真样本零件数变了：%s" % total)
        self.assertEqual(total, len(with_box),
                         "真样本上每一件都该有图元矩形（实测 263/263）；"
                         "没有坐标的件有 %s 个" % (total - len(with_box)))

    def test_d2_drawing_box_agrees_with_the_part_size(self):
        doc = self.real_geometry()
        evidence = packaging_parts.geometry_evidence_of(doc)
        by_id = {row.get("component_id"): row for row in doc["parts"]}
        checked = 0
        for row in evidence.get("components") or []:
            box = row.get("drawing_bbox")
            part = by_id.get(row.get("component_id")) or {}
            if not box or part.get("unfolded_length_mm") is None:
                continue
            checked += 1
            self.assertAlmostEqual(abs(box[2] - box[0]), part["unfolded_length_mm"], delta=0.01,
                                   msg="%s 的绘图包络长边与件尺寸不同源" % row.get("component_id"))
            self.assertAlmostEqual(abs(box[3] - box[1]), part["unfolded_width_mm"], delta=0.01,
                                   msg="%s 的绘图包络短边与件尺寸不同源" % row.get("component_id"))
        self.assertGreaterEqual(checked, 200, "真样本上该核对到的件数变了：%s" % checked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
