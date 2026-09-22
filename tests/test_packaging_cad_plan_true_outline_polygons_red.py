"""红测：平面图按真实闭合轮廓画多边形（开口件仍画包络矩形）。

Spec：`docs/specs/packaging-cad-plan-true-outline-polygons.md`
依赖口径：`docs/specs/packaging-parts-true-outline.md`（闭合件的环与 `points`）、
          `docs/specs/packaging-cad-plan-drawing-coordinates.md`（`drawing_bbox` 只给画图）

现状缺口（2026-09-22 实测，真样本 `裕同包装项目-待开发/酒盒.dwg`）：

  · 闭合件 134 件**每一件**都带着 `outline.points`（合计 1777 点，单件 4…32 点），
    开口件 129 件一份点都没有；
  · `geometry_evidence_of()` 不带 `outline_points` → 平面图只能把每件画成一个包络矩形，
    闭合件看不出真实形状（形状信息在服务端白白丢掉了）。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
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
MAX_TOTAL_POINTS = 5000


def fixture_ir() -> dict:
    return json.loads((FIXTURES / "parts_panels.json").read_text(encoding="utf-8"))


def js_body(source: str, signature: str) -> str:
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


def closed_doc() -> dict:
    """一份最小的几何零件文档：一件闭合（带环点）+ 一件开口（只有包络）。"""
    return {
        "parts": [
            {"component_id": "cmp:closed", "entity_ids": ["ent:1"], "layers": ["CUT"],
             "role": "cut", "geometry_component_ref": "cmp:closed",
             "outline_status": "closed", "size_source": "closed_outline",
             "unfolded_length_mm": 100.0, "unfolded_width_mm": 50.0, "area_mm2": 5000.0,
             "outline": {"points": [[0.0, 0.0], [100.0, 0.0], [100.0, 50.0], [0.0, 50.0]],
                         "bbox": [0.0, 0.0, 100.0, 50.0], "closed": True, "area_mm2": 5000.0}},
            {"component_id": "cmp:open", "entity_ids": ["ent:2"], "layers": ["CREASE"],
             "role": "crease", "geometry_component_ref": "cmp:open",
             "outline_status": "open", "size_source": "component_bbox",
             "unfolded_length_mm": 40.0, "unfolded_width_mm": 20.0, "area_mm2": 800.0,
             "outline": {"points": None, "bbox": [200.0, 0.0, 240.0, 20.0],
                         "closed": False, "area_mm2": None}},
        ],
        "stats": {"component_total": 2, "kept_total": 2},
    }


# --------------------------------------------------------------------------- #
# A 组：证据层只给闭合件带环点
# --------------------------------------------------------------------------- #
class AEvidenceCarriesOutlinePoints(unittest.TestCase):
    def test_a1_fixture_components_declare_the_key(self):
        doc = packaging_parts.extract(fixture_ir())
        evidence = packaging_parts.geometry_evidence_of(doc)
        by_id = {row.get("component_id"): row for row in doc["parts"]}
        self.assertTrue(evidence.get("components"), "证据层一个分量都没有")
        for row in evidence.get("components"):
            part = by_id.get(row.get("component_id")) or {}
            self.assertIn("outline_points", row,
                          "证据层没有 outline_points：平面图画不出闭合件的真实轮廓（Spec §C1）")
            self.assertEqual((part.get("outline") or {}).get("points"), row.get("outline_points"),
                             "%s 的 outline_points 必须逐字等于零件行的 outline.points"
                             % row.get("component_id"))

    def test_a2_closed_parts_pass_their_loop_open_parts_stay_empty(self):
        evidence = packaging_parts.geometry_evidence_of(closed_doc())
        rows = {row["component_id"]: row for row in evidence["components"]}
        self.assertEqual([[0.0, 0.0], [100.0, 0.0], [100.0, 50.0], [0.0, 50.0]],
                         rows["cmp:closed"].get("outline_points"),
                         "闭合件的环点必须原样带出来，不许改写/补点")
        self.assertIsNone(rows["cmp:open"].get("outline_points"),
                          "开口件没有环：不许拿包络编 4 个点冒充轮廓（Spec §4）")

    def test_a3_existing_keys_and_totals_survive(self):
        evidence = packaging_parts.geometry_evidence_of(closed_doc())
        for row in evidence["components"]:
            for key in ("component_id", "entity_ids", "bbox", "drawing_bbox", "layers", "role",
                        "geometry_component_ref", "unfolded_length_mm", "unfolded_width_mm",
                        "outline_status", "size_source"):
                self.assertIn(key, row, "证据层丢了既有键 %s" % key)
        self.assertEqual(2, evidence.get("kept_component_total"))


# --------------------------------------------------------------------------- #
# B 组：前端画多边形（闭合）/矩形（其余）
# --------------------------------------------------------------------------- #
class BPlanDrawsPolygons(unittest.TestCase):
    def test_b1_closed_parts_are_polygons(self):
        body = js_body(APP_JS, "function packagingCadPlanComponentSvg(")
        self.assertTrue(body, "packagingCadPlanComponentSvg() 找不到（签名变了？）")
        self.assertIn("polygon", body, "闭合件必须画成多边形，不是包络矩形（Spec §C2）")
        self.assertIn("packagingCadPlanOutlinePoints", body,
                      "环点串必须走唯一入口，不许在渲染函数里各写一份")

    def test_b2_the_point_entry_only_accepts_closed_loops(self):
        body = js_body(APP_JS, "function packagingCadPlanOutlinePoints(")
        self.assertTrue(body, "缺少 packagingCadPlanOutlinePoints()（Spec §C2）")
        self.assertIn("outline_points", body)
        self.assertIn("closed", body, "只有闭合件才画多边形")
        self.assertIn("-y", body.replace(" ", ""), "平面图的 y 轴要按 viewBox 的约定翻一次")

    def test_b3_rect_branch_survives_for_open_parts(self):
        body = js_body(APP_JS, "function packagingCadPlanComponentSvg(")
        self.assertIn("<rect", body, "开口件仍要画包络矩形（第 1 层口径不许回退）")
        for token in ("data-component-id", "data-bbox", "data-business-part", "data-role"):
            self.assertIn(token, body, "多边形/矩形必须带同一套数据属性：%s" % token)

    def test_b4_app_js_still_parses(self):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("未安装 node，跳过语法检查")
        completed = subprocess.run([node, "--check", str(APP_JS_PATH)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(0, completed.returncode, "app.js 语法错误：\n%s" % completed.stderr)


# --------------------------------------------------------------------------- #
# C 组：护栏（现状即绿）
# --------------------------------------------------------------------------- #
class CGuards(unittest.TestCase):
    def test_c1_binding_ignores_outline_points(self):
        plan = packaging_parts.bind_geometry(
            [{"business_part_code": "P01", "length_mm": 100.0, "width_mm": 50.0}],
            [{"component_id": "cmp:a", "entity_ids": [], "outline_points": [[0, 0], [100, 0]]}])
        row = (plan.get("bindings") or [{}])[0]
        self.assertEqual("unbound", row.get("status"),
                         "环点只是绘图数据，不许被当成件尺寸（Spec §C3）")
        self.assertIn("size_unknown", row.get("reasons") or [])

    def test_c2_interaction_and_colors_are_untouched(self):
        for token in ("highlightPackagingBusinessPart", "packagingCadPlanComponentBox",
                      "PACKAGING_CAD_LAYER_COLORS", "fitPackagingCadPlan"):
            self.assertIn(token, APP_JS, "平面图既有能力丢了：%s" % token)

    def test_c3_panel_outline_rendering_is_not_replaced(self):
        body = js_body(APP_JS, "function renderPackagingPartPanel(")
        self.assertIn("polygon", body, "右栏零件面板的轮廓渲染不许被本批改掉")


# --------------------------------------------------------------------------- #
# D 组：真样本（本机有样本 + dwg2dxf 才跑）
# --------------------------------------------------------------------------- #
class DRealSample(unittest.TestCase):
    def setUp(self):
        if not DWG.exists():
            self.skipTest("真实样本不在本机：%s" % DWG)
        if not shutil.which("dwg2dxf"):
            self.skipTest("本机没有 libredwg 的 dwg2dxf")

    def real_evidence(self) -> dict:
        cache = pathlib.Path(tempfile.mkdtemp()) / "real.dxf"
        subprocess.run([shutil.which("dwg2dxf"), "-y", "-o", str(cache), str(DWG)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from tech_app.backend.services import cad_ir
        ir = cad_ir.parse_dxf(cache.read_bytes(), filename=cache.name,
                              source={"kind": "dxf_2d", "attachment_name": "酒盒.dwg"})
        return packaging_parts.geometry_evidence_of(packaging_parts.extract(ir))

    def test_d1_real_closed_parts_all_have_a_loop(self):
        components = self.real_evidence().get("components") or []
        closed = [row for row in components if row.get("outline_status") == "closed"]
        without = [row["component_id"] for row in closed
                   if len(row.get("outline_points") or []) < 3]
        self.assertGreaterEqual(len(closed), 100, "真样本闭合件数变了：%s" % len(closed))
        self.assertEqual([], without, "闭合件必须都带环点：%s" % without[:5])

    def test_d2_open_parts_carry_no_points_and_payload_stays_small(self):
        components = self.real_evidence().get("components") or []
        open_rows = [row for row in components if row.get("outline_status") != "closed"]
        self.assertTrue(open_rows, "真样本应当既有闭合件也有开口件")
        self.assertEqual([], [row["component_id"] for row in open_rows if row.get("outline_points")],
                         "开口件不许带点（Spec §C1/§4）")
        total = sum(len(row.get("outline_points") or []) for row in components)
        self.assertLessEqual(total, MAX_TOTAL_POINTS,
                             "证据层载荷上限：只带闭合件的环点（实测 1777），"
                             "不许把开放轮廓折线也带上；实测 %s" % total)

    def test_d3_drawing_box_and_loop_agree_with_the_part_size(self):
        evidence = self.real_evidence()
        checked = 0
        for row in evidence.get("components") or []:
            points = row.get("outline_points") or []
            box = row.get("drawing_bbox")
            if len(points) < 3 or not box:
                continue
            checked += 1
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            self.assertAlmostEqual(min(xs), box[0], delta=0.01)
            self.assertAlmostEqual(min(ys), box[1], delta=0.01)
            self.assertAlmostEqual(max(xs), box[2], delta=0.01)
            self.assertAlmostEqual(max(ys), box[3], delta=0.01)
        self.assertGreaterEqual(checked, 100, "该核对到的闭合件数变了：%s" % checked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
