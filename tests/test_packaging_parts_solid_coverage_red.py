"""红测：图纸零件 3D 挤出的覆盖率（凹多边形 + 批量结论 + 真值指标）。

Spec：`docs/specs/packaging-parts-solid-coverage.md`（取代 `packaging-parts-3d-extrusion.md` 的凹多边形条款）
前置：假设第 4 层（单件挤出 / 落库 / 路由）已实现。

现状缺口（34 上真跑 + 本机同代码实测，项目 f1417060ae9d 酒盒.dwg）：
  · `summarize().solid_ok_ratio` 恒 0.0，64 行零件没有一行带 `solid_status` —— 覆盖率根本没人算；
  · 单件实测：DWG-P35 `ok`(12 面)、DWG-P07 `unsupported:concave_polygon`、DWG-P01 `unsupported:outline_open`；
  · 51 件闭合轮廓里 34 件凹、17 件凸，而三角化是扇形 + 凸性门槛 → 覆盖率天花板 17/64 = 0.266；
  · 没有"整份零件文档一次算完"的入口。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
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

PKG = "tech_app.backend.services.packaging_part_solids"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"

TRIANGULATION = "ear_clipping"
FORBIDDEN_REASON = "concave_polygon"
NEW_REASONS = ("self_intersecting", "degenerate_polygon")
KEPT_REASONS = ("outline_open", "outline_unavailable", "thickness_unknown",
                "too_few_points", "too_many_points")
STATS_KEYS = ("part_total", "ok_total", "unsupported_total", "solid_ok_ratio",
              "unsupported_reason_mix")
SOLIDS_PATH = "/api/projects/{pid}/requirement/packaging-parts/solids"
REQUIREMENT = {"grey_board": "灰板", "grey_board_thickness": 2.5,
               "face_paper": "粉灰", "face_paper_gsm": 350}


def module():
    try:
        return importlib.import_module(PKG)
    except ModuleNotFoundError:
        return None


def row(points, thickness=2.0, code="DWG-P01", status="closed", **over):
    base = {"part_code": code, "part_id": code, "name": "图纸零件 " + code,
            "unfolded_length_mm": 100.0, "unfolded_width_mm": 50.0, "area_mm2": 5000.0,
            "thickness_mm": thickness, "outline_status": status, "outline_reason": "",
            "size_source": "closed_outline", "material": {"spec": "灰板 2.0mm"},
            "outline": {"points": points, "closed": True, "area_mm2": 5000.0,
                        "entity_ids": ["ent:model:R-0"]}}
    base.update(over)
    return base


RECT = [[0.0, 0.0], [100.0, 0.0], [100.0, 50.0], [0.0, 50.0]]
L_SHAPE = [[0.0, 0.0], [100.0, 0.0], [100.0, 20.0], [20.0, 20.0], [20.0, 60.0], [0.0, 60.0]]
U_SHAPE = [[0.0, 0.0], [120.0, 0.0], [120.0, 80.0], [90.0, 80.0], [90.0, 30.0],
           [30.0, 30.0], [30.0, 80.0], [0.0, 80.0]]
BOWTIE = [[0.0, 0.0], [100.0, 100.0], [100.0, 0.0], [0.0, 100.0]]
COLLINEAR = [[0.0, 0.0], [50.0, 0.0], [100.0, 0.0]]


def area_of(points):
    total = 0.0
    for index in range(len(points)):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


class SolidCase(unittest.TestCase):
    def module(self):
        module_ = module()
        if module_ is None:
            self.fail("缺少 %s（Spec §4.1）" % PKG)
        return module_

    def extrude_all(self, rows, **over):
        module_ = self.module()
        self.assertTrue(callable(getattr(module_, "extrude_all", None)),
                        "必须提供 extrude_all()（Spec §2.2）")
        return module_.extrude_all(rows, **over) if over else module_.extrude_all(rows)


# --------------------------------------------------------------------------- #
# A 组：常量
# --------------------------------------------------------------------------- #
class AConstants(SolidCase):
    def test_a1_triangulation_name(self):
        self.assertEqual(self.module().TRIANGULATION, TRIANGULATION,
                         "三角化方式必须逐字等于 Spec §2")

    def test_a2_concave_is_not_a_reason_anymore(self):
        reasons = tuple(self.module().UNSUPPORTED_REASONS)
        self.assertNotIn(FORBIDDEN_REASON, reasons,
                         "凹多边形不许再是拒绝理由（Spec §2.1）")

    def test_a3_new_and_kept_reasons(self):
        reasons = tuple(self.module().UNSUPPORTED_REASONS)
        for name in NEW_REASONS:
            self.assertIn(name, reasons, "新增拒绝理由 %s（Spec §2）" % name)
        for name in KEPT_REASONS:
            self.assertIn(name, reasons, "既有拒绝理由 %s 不许删（Spec §5）" % name)

    def test_a4_extrude_all_is_callable(self):
        self.assertTrue(callable(getattr(self.module(), "extrude_all", None)),
                        "必须提供 extrude_all()（Spec §2.2）")


# --------------------------------------------------------------------------- #
# B 组：耳切三角化
# --------------------------------------------------------------------------- #
class BEarClipping(SolidCase):
    def test_b1_convex_rectangle_is_unchanged(self):
        out = self.module().extrude(row(RECT))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["triangles"], 12, "凸矩形仍是底 1 + 顶 1 + 侧 8（Spec §2.1）")

    def test_b2_l_shape_is_extruded(self):
        out = self.module().extrude(row(L_SHAPE))
        self.assertEqual(out["status"], "ok", "凹件必须能挤（Spec §2.1）")
        self.assertEqual(out.get("triangulation"), TRIANGULATION)
        self.assertEqual(out["triangles"], 2 * len(L_SHAPE) + 2 * (len(L_SHAPE) - 2))
        self.assertAlmostEqual(float(out["volume_mm3"]), area_of(L_SHAPE) * 2.0, places=3,
                               msg="体积必须是轮廓面积 × 料厚")

    def test_b3_u_shape_is_extruded(self):
        out = self.module().extrude(row(U_SHAPE))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["triangles"], 2 * len(U_SHAPE) + 2 * (len(U_SHAPE) - 2))
        self.assertAlmostEqual(float(out["volume_mm3"]), area_of(U_SHAPE) * 2.0, places=3)

    def test_b4_self_intersecting_is_rejected(self):
        out = self.module().extrude(row(BOWTIE))
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["reason"], "self_intersecting",
                         "自交轮廓不许硬挤（Spec §2.1）")
        self.assertFalse(out.get("stl"), "拒绝时不许给半成品 STL")

    def test_b5_degenerate_is_rejected(self):
        out = self.module().extrude(row(COLLINEAR))
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["reason"], "degenerate_polygon", "零面积轮廓不许硬挤（Spec §2.1）")

    def test_b6_missing_thickness_is_still_rejected(self):
        out = self.module().extrude(row(L_SHAPE, thickness=None))
        self.assertEqual(out["reason"], "thickness_unknown",
                         "缺料厚仍是拒绝，绝不许默认（Spec §5）")


# --------------------------------------------------------------------------- #
# C 组：批量产出
# --------------------------------------------------------------------------- #
class CBatch(SolidCase):
    def test_c1_shape_and_counts(self):
        rows = [row(RECT, code="DWG-P01"), row(L_SHAPE, code="DWG-P02"),
                row(BOWTIE, code="DWG-P03"), row(RECT, thickness=None, code="DWG-P04")]
        out = self.extrude_all(rows)
        self.assertIsInstance(out, dict)
        self.assertEqual(len(out.get("parts") or []), 4)
        stats = out.get("stats") or {}
        for key in STATS_KEYS:
            self.assertIn(key, stats, "stats 缺 %s（Spec §2.2）" % key)
        self.assertEqual(int(stats["part_total"]), 4)
        self.assertEqual(int(stats["ok_total"]), 2)
        self.assertEqual(int(stats["unsupported_total"]), 2)
        self.assertAlmostEqual(float(stats["solid_ok_ratio"]), 0.5, places=3)
        self.assertEqual(int((stats["unsupported_reason_mix"] or {}).get("self_intersecting", 0)), 1)
        self.assertEqual(int((stats["unsupported_reason_mix"] or {}).get("thickness_unknown", 0)), 1)

    def test_c2_rows_get_status_written_back(self):
        rows = [row(RECT, code="DWG-P01"), row(L_SHAPE, code="DWG-P02")]
        out = self.extrude_all(rows)
        statuses = {item["part_code"]: item.get("solid_status") for item in out["parts"]}
        self.assertEqual(statuses, {"DWG-P01": "ok", "DWG-P02": "ok"},
                         "逐件结论要回写 solid_status（Spec §2.2）")
        for item in out["parts"]:
            self.assertIn(item.get("solid_reason"), ("", None) + tuple(self.module().UNSUPPORTED_REASONS))

    def test_c3_input_rows_are_not_mutated(self):
        rows = [row(RECT, code="DWG-P01")]
        self.extrude_all(rows)
        self.assertNotIn("solid_status", rows[0], "extrude_all 不许改入参（Spec §2.2）")

    def test_c4_empty_input_is_zero_not_error(self):
        out = self.extrude_all([])
        stats = out.get("stats") or {}
        self.assertEqual(int(stats.get("part_total", -1)), 0)
        self.assertEqual(float(stats.get("solid_ok_ratio")), 0.0)


# --------------------------------------------------------------------------- #
# D 组：覆盖率是真值
# --------------------------------------------------------------------------- #
class DMetrics(SolidCase):
    def test_d1_summarize_reads_solid_status(self):
        doc = {"parts": [{"part_code": "DWG-P01", "outline_status": "closed",
                          "size_source": "closed_outline", "solid_status": "ok"},
                         {"part_code": "DWG-P02", "outline_status": "closed",
                          "size_source": "closed_outline",
                          "solid_status": "unsupported", "solid_reason": "thickness_unknown"}],
               "stats": {"part_total": 2}}
        summary = packaging_parts.summarize(doc)
        self.assertAlmostEqual(float(summary["solid_ok_ratio"]), 0.5, places=3,
                               msg="solid_ok_ratio 必须反映行上的真值（Spec §2.3）")

    def test_d2_batch_then_summarize_is_consistent(self):
        rows = [row(RECT, code="DWG-P01"), row(BOWTIE, code="DWG-P02")]
        out = self.extrude_all(rows)
        doc = {"parts": out["parts"], "stats": {"part_total": len(rows)}}
        summary = packaging_parts.summarize(doc)
        self.assertAlmostEqual(float(summary["solid_ok_ratio"]), 0.5, places=3)


# --------------------------------------------------------------------------- #
# E 组：路由与前端
# --------------------------------------------------------------------------- #
class ERoutes(SolidCase):
    def test_e1_batch_route_registered(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn(SOLIDS_PATH, source, "main.py 缺批量挤出路由（Spec §2.4）")
        self.assertIn("BOX_MATCH_DECIDE_ROLES", source, "写权限必须引用既有角色集")

    def test_e2_unsupported_copy_updated(self):
        source = APP_JS.read_text(encoding="utf-8", errors="replace")
        for phrase in ("自交", "退化"):
            self.assertIn(phrase, source, "unsupported 文案要有：%s（Spec §2.5）" % phrase)
        self.assertNotIn("凹多边形", source, "凹多边形不再是拒绝理由（Spec §2.5）")

    def test_e3_missing_error_code_preserved(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("PACKAGING_PART_SOLID_MISSING", source, "既有稳定错误码不许删")


# --------------------------------------------------------------------------- #
# F 组：真实样本门槛（Spec §3）
# --------------------------------------------------------------------------- #
class FRealSample(SolidCase):
    @classmethod
    def setUpClass(cls):
        cls._cache = {}

    def _result(self, filename):
        cached = self._cache.get(filename)
        if cached is not None:
            return cached
        sample = SAMPLES_DIR / filename
        if not sample.exists():
            self.skipTest("真实样本不在本机：%s" % sample)
        tool = shutil.which("dwg2dxf")
        if not tool:
            self.skipTest("本机没有 libredwg 的 dwg2dxf")
        from tech_app.backend.services import cad_ir
        cache = pathlib.Path(tempfile.mkdtemp()) / "real.dxf"
        subprocess.run([tool, "-y", "-o", str(cache), str(sample)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ir = cad_ir.parse_dxf(cache.read_bytes(), filename=cache.name,
                              source={"kind": "dxf_2d", "attachment_name": filename})
        doc = packaging_parts.extract(ir, options={"requirement": REQUIREMENT})
        result = self.extrude_all(doc["parts"])
        self._cache[filename] = (doc, result)
        return doc, result

    def test_f1_solid_ok_ratio(self):
        _doc, result = self._result("酒盒.dwg")
        ratio = float((result.get("stats") or {}).get("solid_ok_ratio") or 0.0)
        self.assertGreaterEqual(ratio, 0.70,
                                "酒盒 solid_ok_ratio 门槛 0.70（今天 0.0，Spec §3）")

    def test_f2_concave_parts_are_extruded(self):
        _doc, result = self._result("酒盒.dwg")
        ok = [item for item in result["parts"] if item.get("status") == "ok"
              and item.get("triangulation") == TRIANGULATION]
        self.assertGreaterEqual(len(ok), 30, "真图上至少 30 件要耳切挤出（Spec §3）")

    def test_f3_convex_parts_do_not_regress(self):
        _doc, result = self._result("酒盒.dwg")
        for item in result["parts"]:
            if item.get("status") != "ok":
                continue
            points = item.get("points")
            self.assertIsInstance(points, int, "逐件结论要带点数")
            self.assertEqual(int(item["triangles"]), 2 * points + 2 * (points - 2),
                             "%s 面数必须等于 2n + 2(n-2)" % item.get("part_code"))

    def test_f4_every_part_has_a_conclusion(self):
        _doc, result = self._result("酒盒.dwg")
        reasons = tuple(self.module().UNSUPPORTED_REASONS)
        for item in result["parts"]:
            self.assertIn(item.get("status"), ("ok", "unsupported"),
                          "%s 必须有结论" % item.get("part_code"))
            if item.get("status") == "unsupported":
                self.assertIn(item.get("reason"), reasons,
                              "%s 的拒绝理由必须在闭集里" % item.get("part_code"))
                self.assertNotEqual(item.get("reason"), FORBIDDEN_REASON)


if __name__ == "__main__":
    unittest.main(verbosity=2)
