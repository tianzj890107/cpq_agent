"""红测：图纸零件平板挤出与 3D 预览（第 4 层）。

Spec：`docs/specs/packaging-parts-3d-extrusion.md`
前置：假设第 1 层（`outline.points`）与第 3 层（材料/厚度前提）已实现。

**现状缺口（代码事实）**：图纸链路 8 步不产任何几何（`packaging_drawing_flow/model.py` 的
`STEP_IDS` 产物最远到 `parts_id/parts_total`），右栏 3D 走 `loadSTL` → `currentGeometry`
（技术链路），图纸项目下恒为 null，所以右栏只剩一条坐标轴的空白画布。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import math
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.packaging_part_solids"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

ENGINE_VERSION = "packaging-part-solids/1"
DOC_KEY = "packaging_part_solids"
UNSUPPORTED_REASONS = ("outline_open", "outline_unavailable", "thickness_unknown",
                       "concave_polygon", "too_few_points", "too_many_points")
MAX_POINTS = 2000
SOLID_PATH = "/api/projects/{pid}/requirement/packaging-parts/{part_code}/solid"
STL_PATH = "/api/projects/{pid}/requirement/packaging-parts/{part_code}/solid.stl"


def load_module():
    try:
        return importlib.import_module(PKG)
    except ModuleNotFoundError:
        return None


def row(points=None, thickness=2.0, status="closed", **over):
    base = {"part_code": "DWG-P01", "part_id": "DWG-P01", "name": "图纸零件 P01",
            "unfolded_length_mm": 100.0, "unfolded_width_mm": 50.0,
            "area_mm2": 5000.0, "thickness_mm": thickness, "outline_status": status,
            "outline_reason": "", "size_source": "closed_outline",
            "material": {"spec": "灰板 2.0mm"},
            "outline": {"points": points if points is not None
                        else [[0.0, 0.0], [100.0, 0.0], [100.0, 50.0], [0.0, 50.0]],
                        "closed": True, "area_mm2": 5000.0,
                        "entity_ids": ["ent:model:R-0"]}}
    base.update(over)
    return base


def l_shape():
    return [[0.0, 0.0], [100.0, 0.0], [100.0, 20.0], [20.0, 20.0], [20.0, 60.0], [0.0, 60.0]]


def circle_points(count: int):
    return [[50.0 + 40.0 * math.cos(2 * math.pi * i / count),
             50.0 + 40.0 * math.sin(2 * math.pi * i / count)] for i in range(count)]


class SolidCase(unittest.TestCase):
    def module(self):
        module = load_module()
        if module is None:
            self.fail("缺少 tech_app/backend/services/packaging_part_solids.py（Spec §3）")
        return module


# --------------------------------------------------------------------------- #
# A 组：契约
# --------------------------------------------------------------------------- #
class AContract(SolidCase):
    def test_a1_constants(self):
        module = self.module()
        self.assertEqual(module.ENGINE_VERSION, ENGINE_VERSION)
        self.assertEqual(module.DOC_KEY, DOC_KEY)
        self.assertEqual(module.STL_FORMAT, "ascii")
        self.assertEqual(int(module.MAX_POINTS), MAX_POINTS)
        self.assertEqual(tuple(module.UNSUPPORTED_REASONS), UNSUPPORTED_REASONS)

    def test_a2_callables(self):
        module = self.module()
        for name in ("extrude", "save_solids", "load_solids"):
            self.assertTrue(callable(getattr(module, name, None)),
                            "缺少 %s()" % name)


# --------------------------------------------------------------------------- #
# B 组：矩形挤出
# --------------------------------------------------------------------------- #
class BRectangle(SolidCase):
    def test_b1_twelve_triangles_and_volume(self):
        out = self.module().extrude(row())
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["triangles"], 12,
                         "矩形：底 2 + 顶 2 + 侧 8 = 12（Spec §3）")
        self.assertAlmostEqual(float(out["volume_mm3"]), 100.0 * 50.0 * 2.0, places=6)

    def test_b2_stl_is_ascii_and_complete(self):
        out = self.module().extrude(row())
        stl = out["stl"]
        self.assertTrue(stl.startswith("solid "), "ASCII STL 必须以 solid 开头")
        self.assertIn("endsolid", stl)
        self.assertEqual(stl.count("facet normal"), 12, "每个三角形一个 facet")

    def test_b3_bbox_is_reported(self):
        out = self.module().extrude(row())
        self.assertAlmostEqual(float(out["bbox_mm"]["length"]), 100.0, places=6)
        self.assertAlmostEqual(float(out["bbox_mm"]["width"]), 50.0, places=6)
        self.assertAlmostEqual(float(out["bbox_mm"]["thickness"]), 2.0, places=6)


# --------------------------------------------------------------------------- #
# C 组：门槛
# --------------------------------------------------------------------------- #
class CGates(SolidCase):
    def test_c1_open_outline_is_unsupported(self):
        out = self.module().extrude(row(status="open"))
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["reason"], "outline_open")
        self.assertIn("stl", out)
        self.assertFalse(out["stl"], "unsupported 时不许给半成品 STL")

    def test_c2_unavailable_outline_is_unsupported(self):
        out = self.module().extrude(row(status="unavailable"))
        self.assertEqual(out["reason"], "outline_unavailable")

    def test_c3_thickness_is_never_defaulted(self):
        out = self.module().extrude(row(thickness=None))
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["reason"], "thickness_unknown",
                         "缺厚度必须拒绝，绝不许默认 2mm")

    def test_c4_too_few_points(self):
        out = self.module().extrude(row(points=[[0.0, 0.0], [10.0, 0.0]]))
        self.assertEqual(out["reason"], "too_few_points")

    def test_c5_too_many_points(self):
        out = self.module().extrude(row(points=circle_points(MAX_POINTS + 1)))
        self.assertEqual(out["reason"], "too_many_points")


# --------------------------------------------------------------------------- #
# D 组：凹多边形
# --------------------------------------------------------------------------- #
class DConcave(SolidCase):
    def test_d1_concave_is_rejected(self):
        out = self.module().extrude(row(points=l_shape()))
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["reason"], "concave_polygon",
                         "凹多边形本版不挤（不做耳切），必须显式拒绝")

    def test_d2_convex_triangle_still_works(self):
        out = self.module().extrude(row(points=[[0.0, 0.0], [100.0, 0.0], [0.0, 50.0]]))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["triangles"], 2 * 3 + 2 * (3 - 2),
                         "三角柱：底 1 + 顶 1 + 侧 6 = 8")


# --------------------------------------------------------------------------- #
# E 组：落库
# --------------------------------------------------------------------------- #
class EPersistence(SolidCase):
    def test_e1_save_load_roundtrip_and_versioning(self):
        module = self.module()
        doc = {"parts": [module.extrude(row())], "engine_version": ENGINE_VERSION}
        first = module.save_solids("proj-solid", doc)
        second = module.save_solids("proj-solid", doc)
        self.assertEqual(first["version"], 1 if "version" in first else first.get("version", 1),
                         "首次落库版本号必须是 1")
        self.assertGreater(second["version"], first["version"],
                           "同内容重复落库也要长版本号（照 semantics 的版本化范式）")
        loaded = module.load_solids("proj-solid")
        self.assertEqual(len(loaded["parts"]), 1)
        self.assertEqual(loaded["parts"][0]["part_code"], "DWG-P01")


# --------------------------------------------------------------------------- #
# F 组：路由
# --------------------------------------------------------------------------- #
class FRoutes(SolidCase):
    def test_f1_routes_registered(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for path in (SOLID_PATH, STL_PATH):
            self.assertIn(path, source, "main.py 缺路由 %s" % path)
        import tech_app.backend.main as main
        paths = {route.path for route in main.app.routes}
        for path in (SOLID_PATH, STL_PATH):
            self.assertIn(path, paths, "路由未注册：%s" % path)

    def test_f2_roles_and_mime(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        start = source.index(SOLID_PATH)
        block = source[start:start + 6000]
        self.assertIn("BOX_MATCH_DECIDE_ROLES", block, "写权限必须引用既有角色集")
        self.assertIn("application/sla", source, "STL 必须用 application/sla")
        self.assertIn("PACKAGING_PART_SOLID_MISSING", source, "未生成 STL 要有稳定错误码")


# --------------------------------------------------------------------------- #
# G 组：前端
# --------------------------------------------------------------------------- #
class GFrontend(SolidCase):
    def _js(self):
        return APP_JS.read_text(encoding="utf-8", errors="replace")

    def test_g1_panel_has_3d_button_reusing_loadstl(self):
        source = self._js()
        self.assertIn("3D 预览", source, "面板要有 3D 预览入口")
        marker = source.find("packagingPartSolid")
        self.assertGreater(marker, 0, "需要稳定的 3D 入口 id（packagingPartSolid）")
        window = source[max(0, marker - 2000):marker + 4000]
        self.assertIn("loadSTL", window, "必须复用既有 loadSTL，不新建第二套画布")

    def test_g2_unsupported_copy_is_human_readable(self):
        source = self._js()
        for phrase in ("没有闭合轮廓", "缺厚度", "凹多边形"):
            self.assertIn(phrase, source, "unsupported 原因必须翻成人话：%s" % phrase)

    def test_g3_existing_viewer_untouched(self):
        source = self._js()
        start = source.index("function initViewer()")
        end = source.index("function clearViewer()", start)
        self.assertNotIn("packaging", source[start:end],
                         "既有 3D 初始化不许被图纸零件逻辑污染")


if __name__ == "__main__":
    unittest.main(verbosity=2)
