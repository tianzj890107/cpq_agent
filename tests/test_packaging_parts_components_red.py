"""红测：图纸零件连通分量必须按「端点相接」分组，不许按「包围盒相交」吞并。

Spec：`docs/specs/packaging-parts-component-chaining.md`
前置：`packaging-dwg-parts-extraction.md`（## 226，零件 = 连通分量）已实现。

现状缺口（本机实测，不是推断）：
  · `cad_ir/geometry.py:139 components_of()` 用 `boxes_touch(bbox, bbox)` 分组 —— 一条
    `(0,0)-(1000,1000)` 的斜线 bbox 覆盖整块，落在里面的独立实体全被并成一件（实测 3 条互不相接
    的实体 → 1 个分量）；
  · 真图 `酒盒.dwg` 402 个分量里有 4 个吞并块（4451.8×3117.9 / 3927.8×967.9 / 1706.0×713.3 ×2，
    合计 232 条实体），全被 `area_over_max` 整块丢掉，且丢弃不留痕；
  · `filtered_total=192` 里"真碎线噪声"与"被吞并的真零件"混在一个数字里，前端只有
    `还有 N 件未列出（只显示前 M 件）`（那是 `max_parts` 截断），看不到"被过滤"这一笔。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.packaging_parts"
GEO_PY = ROOT / "tech_app" / "backend" / "services" / "cad_ir" / "geometry.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
SAMPLE_TOOL = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"

REASON_CODES = ("edge_over_max", "area_over_max", "area_under_min", "no_curve_entity",
                "no_components", "all_filtered", "no_unit")
DEFAULT_OPTIONS = {"min_area_mm2": 2000, "max_edge_mm": 1200,
                   "max_area_mm2": 1000000, "max_parts": 64}
NEW_STATS_KEYS = ("filtered_reason_mix", "filtered_edge_over_max_total",
                  "filtered_area_over_max_total", "filtered_area_under_min_total",
                  "filtered_no_curve_entity_total", "truncated", "ungroupable_total")
MAX_EDGE_MM = 1200.0
MAX_AREA_MM2 = 1000000.0


def into_geometric_entities(rows):
    """测试夹具的 `{entity_id, kind, bbox, attributes}` → `components_of` 的入参形状。"""
    return [dict(row) for row in rows]


def line(eid, start, end, layer="DESIGN"):
    x0, y0 = min(start[0], end[0]), min(start[1], end[1])
    x1, y1 = max(start[0], end[0]), max(start[1], end[1])
    return {"entity_id": eid, "type": "LINE", "kind": "line", "layer": layer,
            "bbox": [float(x0), float(y0), float(x1), float(y1)],
            "attributes": {"start": [float(start[0]), float(start[1])],
                           "end": [float(end[0]), float(end[1])]}}


def blob(eid, bbox, layer="DESIGN"):
    """没有坐标的实体（spline 这类 IR 没落端点的情况）。"""
    return {"entity_id": eid, "type": "SPLINE", "kind": "spline", "layer": layer,
            "bbox": [float(value) for value in bbox], "attributes": {}}


def square(tag, x0, y0, size, layer="DESIGN"):
    corners = [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]
    return [line("ent:model:%s-%d" % (tag, index), corners[index],
                 corners[(index + 1) % 4], layer=layer) for index in range(4)]


def make_ir(rows, tolerance=1e-3):
    geometry = importlib.import_module("tech_app.backend.services.cad_ir.geometry")
    return {"ir_version": "cad-ir/1", "ir_id": "ir:test", "ir_hash": "hash",
            "parser": {"name": "test"}, "source": {"kind": "dxf_2d", "attachment_name": "t.dxf"},
            "units": {"drawing_units": "mm", "scale_to_mm": 1.0, "unit_status": "confirmed",
                      "unit_confidence": 1.0, "candidates": []},
            "layers": [], "entities": [dict(row) for row in rows], "texts": [],
            "dimensions": [], "geometry": {"components": geometry.components_of(
                into_geometric_entities(rows), tolerance)},
            "evidence": {}, "unsupported": [], "warnings": [], "stats": {}}


class ComponentsCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geometry = importlib.import_module("tech_app.backend.services.cad_ir.geometry")
        cls.module = importlib.import_module(PKG)

    def groups(self, rows, tolerance=1e-3):
        return self.geometry.components_of(into_geometric_entities(rows), tolerance)


# --------------------------------------------------------------------------- #
# A 组：分组判据（合成夹具）
# --------------------------------------------------------------------------- #
class AGrouping(ComponentsCase):
    def test_a1_long_diagonal_does_not_swallow_unrelated_entities(self):
        rows = [line("ent:model:diag", (0.0, 0.0), (1000.0, 1000.0))]
        rows += square("sq", 900.0, 900.0, 50.0)
        groups = self.groups(rows)
        self.assertEqual(2, len(groups),
                         "斜线 bbox 覆盖整块，不许把落在里面的独立方块并进同一件（Spec §1.1）")

    def test_a2_touching_endpoints_still_merge(self):
        rows = [line("ent:model:a", (0.0, 0.0), (100.0, 0.0)),
                line("ent:model:b", (100.0, 0.0), (100.0, 50.0))]
        self.assertEqual(1, len(self.groups(rows)), "端点重合必须照旧合并（Spec §2.2）")

    def test_a3_closed_rectangle_of_four_lines_is_one_component(self):
        self.assertEqual(1, len(self.groups(square("box", 0.0, 0.0, 100.0))),
                         "首尾相接的 4 条 LINE 属于同一件（Spec §2.2）")

    def test_a4_half_millimetre_gap_stays_apart(self):
        rows = [line("ent:model:a", (0.0, 0.0), (100.0, 0.0)),
                line("ent:model:b", (100.5, 0.0), (200.0, 0.0))]
        self.assertEqual(2, len(self.groups(rows)),
                         "0.5mm 缝隙在 1e-3 容差下不许被吞并（Spec §2.2）")

    def test_a5_entity_without_coordinates_is_never_merged_by_bbox(self):
        rows = [line("ent:model:diag", (0.0, 0.0), (1000.0, 1000.0)),
                blob("ent:model:blob", (200.0, 200.0, 210.0, 210.0))]
        groups = self.groups(rows)
        self.assertEqual(2, len(groups),
                         "没有坐标的实体不许用 bbox 兜底并入他件（Spec §2.1）")
        sizes = sorted(len(group.get("entity_ids") or []) for group in groups)
        self.assertEqual([1, 1], sizes, "没有坐标的实体应当单独成件")

    def test_a6_no_new_grouping_vocabulary(self):
        source = GEO_PY.read_text(encoding="utf-8")
        self.assertIn("def components_of(", source)
        self.assertNotIn("import shapely", source)
        self.assertNotIn("import numpy", source)


# --------------------------------------------------------------------------- #
# B 组：被丢掉的两笔账必须分开、可见（后端）
# --------------------------------------------------------------------------- #
class BMetrics(ComponentsCase):
    def test_b1_stats_carries_reason_mix_and_per_reason_totals(self):
        rows = square("tile", 0.0, 0.0, 2000.0) + square("small", 5000.0, 5000.0, 60.0)
        doc = self.module.extract(make_ir(rows))
        stats = doc.get("stats") or {}
        for key in NEW_STATS_KEYS:
            self.assertIn(key, stats, "stats 必须带 %s（Spec §2.4）" % key)
        mix = stats.get("filtered_reason_mix") or {}
        self.assertEqual(stats.get("filtered_total"), sum(mix.values()),
                         "原因明细之和必须等于 filtered_total（Spec §2.4）")
        self.assertGreaterEqual(mix.get("area_over_max") or 0, 1,
                                "2000mm 方板必须按 area_over_max 记账")

    def test_b2_filtered_rows_carry_entity_total(self):
        rows = square("tile", 0.0, 0.0, 2000.0)
        doc = self.module.extract(make_ir(rows))
        filtered = doc.get("filtered") or []
        self.assertTrue(filtered, "2000mm 方板必须出现在 filtered 里")
        for row in filtered:
            self.assertIn("entity_total", row, "过滤项要能判断是不是吞并块（Spec §2.4）")
            self.assertEqual(4, row.get("entity_total"))

    def test_b3_summary_exposes_the_filtered_account(self):
        rows = square("tile", 0.0, 0.0, 2000.0) + square("small", 5000.0, 5000.0, 60.0)
        doc = self.module.extract(make_ir(rows))
        summary = self.module.summarize(doc)
        self.assertIn("filtered_total", summary, "summary 必须透出 filtered_total（Spec §2.4）")
        self.assertIn("filtered_reason_mix", summary, "summary 必须透出 filtered_reason_mix")
        self.assertEqual(summary.get("filtered_total"),
                         sum((summary.get("filtered_reason_mix") or {}).values()))

    def test_b4_frozen_vocabulary_unchanged(self):
        self.assertEqual(tuple(self.module.REASON_CODES), REASON_CODES)
        for key, value in DEFAULT_OPTIONS.items():
            self.assertEqual(self.module.DEFAULT_OPTIONS.get(key), value,
                             "默认口径 %s 是冻结值（Spec §2.5）" % key)
        self.assertEqual(self.module.PART_CODE_FORMAT, "DWG-P%02d")

    def test_b5_ungroupable_entities_are_counted(self):
        rows = [line("ent:model:diag", (0.0, 0.0), (1000.0, 1000.0)),
                blob("ent:model:blob", (200.0, 200.0, 210.0, 210.0))]
        doc = self.module.extract(make_ir(rows))
        self.assertIn("ungroupable_total", doc.get("stats") or {},
                      "没有坐标、无法参与分组的实体必须计数（Spec §2.1）")


# --------------------------------------------------------------------------- #
# C 组：前端 / 读接口（两笔账分三句说）
# --------------------------------------------------------------------------- #
class CWiring(unittest.TestCase):
    def test_c1_read_route_exposes_reason_mix(self):
        text = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("filtered_reason_mix", text,
                      "读接口必须透出 filtered_reason_mix（Spec §2.4）")

    def test_c2_part_tree_shows_both_accounts(self):
        text = APP_JS.read_text(encoding="utf-8", errors="replace")
        self.assertIn("未列出", text, "max_parts 截断的提示必须保留（Spec §2.4）")
        self.assertIn("未成为零件", text,
                      "被过滤掉的分量必须单独说清（Spec §2.4；今天只有 truncated 那一句）")
        self.assertIn("filtered_reason_mix", text, "过滤原因必须从读接口取，不许前端自己猜")


# --------------------------------------------------------------------------- #
# D 组：真样本（默认不跑）
# --------------------------------------------------------------------------- #
class RealSampleComponents(ComponentsCase):
    irs = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.environ.get("CPQ_DWG_REAL_SAMPLES") != "1":
            raise unittest.SkipTest("未设置 CPQ_DWG_REAL_SAMPLES=1：真实样本组默认不跑（Spec §5）")
        try:
            converter = importlib.import_module("tech_app.backend.services.cad_converter")
            capability = converter.capability()
        except Exception as exc:                                       # noqa: BLE001
            raise unittest.SkipTest("转换器适配层不可用：%s: %s" % (type(exc).__name__, exc))
        if not capability.get("available") or capability.get("simulated"):
            raise unittest.SkipTest("没有可用的真实转换器：%r" % capability.get("message"))
        missing = [name for name in (WINE_BOX, ROUND_BOX) if not (SAMPLES_DIR / name).is_file()]
        if missing:
            raise unittest.SkipTest("样本不在本机：缺少 %s" % "、".join(missing))
        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="cpq-dwg-components-"))
        try:
            cad_ir = importlib.import_module("tech_app.backend.services.cad_ir")
            for name in (WINE_BOX, ROUND_BOX):
                out = cls.workspace / name.replace(".dwg", "")
                completed = subprocess.run(
                    [sys.executable, str(SAMPLE_TOOL), "--sample", str(SAMPLES_DIR / name),
                     "--out", str(out), "--json"],
                    capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
                text = (completed.stdout or "").strip()
                payload = json.loads(text[text.find("{"):]) if text else {}
                dxf = pathlib.Path(str(payload.get("dxf_path") or ""))
                if not dxf.is_file():
                    raise unittest.SkipTest("%s 转换未产出 DXF（rc=%s）"
                                            % (name, completed.returncode))
                cls.irs[name] = cad_ir.parse_dxf(dxf.read_bytes(), filename=dxf.name,
                                                 source={"kind": "dwg_2d", "attachment_name": name})
        except unittest.SkipTest:
            raise
        except Exception as exc:                                       # noqa: BLE001
            raise unittest.SkipTest("真实样本转换/解析失败：%s: %s" % (type(exc).__name__, exc))

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "workspace", None) is not None:
            shutil.rmtree(cls.workspace, ignore_errors=True)

    def components(self, name):
        return (self.irs[name].get("geometry") or {}).get("components") or []

    def frame_layers(self, name):
        semantics = importlib.import_module("tech_app.backend.services.packaging_semantics")
        doc = semantics.analyze(self.irs[name])
        return {str(item.get("name")) for item in (doc.get("layers") or [])
                if str(item.get("role")) == "frame"}

    JUDGEABLE = ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE")

    def chaining_keys(self, entity):
        """测试侧**独立**复算端点键（不调用被测实现的分组函数）。"""
        geometry = self.geometry
        attributes = entity.get("attributes") or {}
        kind = str(entity.get("type") or "").upper()
        points = []
        if kind == "LINE":
            points = [geometry.point_of(attributes.get("start")),
                      geometry.point_of(attributes.get("end"))]
        elif kind == "ARC":
            center = geometry.point_of(attributes.get("center"))
            radius = attributes.get("radius")
            if center and radius:
                for angle in (attributes.get("start_angle") or 0.0,
                              attributes.get("end_angle") or 0.0):
                    points.append((center[0] + float(radius) * math.cos(math.radians(angle)),
                                   center[1] + float(radius) * math.sin(math.radians(angle))))
        elif kind in ("LWPOLYLINE", "POLYLINE"):
            points = geometry.points_of(attributes.get("points") or [])
            points = [points[0], points[-1]] if points else []
        circle = None
        if kind == "CIRCLE":
            center = geometry.point_of(attributes.get("center"))
            radius = attributes.get("radius")
            if center and radius:
                circle = (round(center[0], 6), round(center[1], 6), round(float(radius), 6))
        points = [point for point in points if point]
        return [tuple(round(float(value), 6) for value in point) for point in points], circle

    def assert_endpoint_connected(self, name, tolerance=1e-4):
        """Spec §2.3 结构性不变量：多成员分量内部必须能按端点相接串成一个连通体。"""
        entities = {row["entity_id"]: row for row in (self.irs[name].get("entities") or [])}
        judged = 0
        swallowed = []
        for group in self.components(name):
            ids = [item for item in (group.get("entity_ids") or []) if item in entities]
            if len(ids) < 2:
                continue
            if any(str(entities[item].get("type") or "").upper() not in self.JUDGEABLE
                   for item in ids):
                continue                      # 该件含测试侧无法复算端点的类型，跳过
            keys = {item: self.chaining_keys(entities[item]) for item in ids}
            parent = list(range(len(ids)))

            def find(index):
                while parent[index] != index:
                    parent[index] = parent[parent[index]]
                    index = parent[index]
                return index

            def union(left, right):
                root_left, root_right = find(left), find(right)
                if root_left != root_right:
                    parent[root_right] = root_left

            for left in range(len(ids)):
                for right in range(left + 1, len(ids)):
                    first, first_circle = keys[ids[left]]
                    second, second_circle = keys[ids[right]]
                    touch = any(self.geometry.distance(a, b) <= tolerance
                                for a in first for b in second)
                    if not touch and first_circle is not None and first_circle == second_circle:
                        touch = True
                    if touch:
                        union(left, right)
            judged += 1
            if len({find(index) for index in range(len(ids))}) != 1:
                swallowed.append(group.get("component_id"))
        self.assertGreater(judged, 0, "至少要有一条可复算的多成员分量，否则这条不变量是空转")
        self.assertEqual([], swallowed[:5],
                         "这些分量内部的成员端点并不相接，说明还是被吞并了（Spec §2.3）：%s"
                         % swallowed[:5])

    def test_d1_wine_box_components_are_endpoint_connected(self):
        groups = self.components(WINE_BOX)
        self.assertGreaterEqual(len(groups), 402,
                                "酒盒分量数只许多、不许少（Spec §2.3）")
        self.assert_endpoint_connected(WINE_BOX)

    def test_d2_wine_box_filtered_account_adds_up(self):
        doc = self.module.extract(self.irs[WINE_BOX])
        stats = doc.get("stats") or {}
        self.assertEqual(stats.get("filtered_total"),
                         sum((stats.get("filtered_reason_mix") or {}).values()))
        self.assertGreaterEqual(stats.get("filtered_total") or 0, 1)

    def test_d3_round_box_components_do_not_shrink(self):
        self.assertGreaterEqual(len(self.components(ROUND_BOX)), 14,
                                "圆盘盒 14 分量是对照基线（Spec §2.3）")
