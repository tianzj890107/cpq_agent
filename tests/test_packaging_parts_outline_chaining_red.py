"""红测：图纸零件"重复边折叠 + 外轮廓重判"（闭合判定的真问题）。

Spec：`docs/specs/packaging-parts-outline-chaining.md`
前置：假设第 1～5 层（真实轮廓 / 面板 / 工艺成本门槛 / 3D / 指标门禁）已实现。

现状缺口（本机同代码实测 + 34 上真跑，项目 f1417060ae9d 酒盒.dwg）：
  · 64 件里 13 件 `open`，`outline_reason` 全是 `no_closed_loop`；
  · 13 件全部是**环搜索撞预算中止**（`MAX_LOOP_STATES=20000`），不是"图纸真的没环"；
  · 根因是重复边：`DWG-P56` 64 条实体只对应 31 对唯一端点（重复度最高 4）；
  · 折叠重复边后，P01 / P08 / P56～P61 共 8 件的最大环 bbox 与分量 bbox 完全一致
    （即外轮廓），其余 4 件最大环明显小于分量 → 不许当外轮廓。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

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

PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"

CHAIN_RULE_ID = "part_outline_chaining_v1"
EDGE_COLLAPSE_TOLERANCE_MM = 1.0
OUTLINE_BBOX_COVER_RATIO = 0.95
OPEN_REASONS = ("no_curve_entity", "unit_unconfirmed", "loop_budget_exhausted",
                "odd_endpoints", "loop_too_small")
MAX_LOOP_CYCLES = 256
MAX_LOOP_STATES = 20000
DIAGNOSIS_KEYS = ("edges_total", "edges_unique", "collapsed_total", "cycles_found",
                  "budget_exhausted", "odd_degree_vertices", "nearest_gap_mm")


# --------------------------------------------------------------------------- #
# 合成 IR
# --------------------------------------------------------------------------- #
def make_ir(pieces):
    components, entities = [], []
    for component, rows in pieces:
        components.append(component)
        entities.extend(rows)
    return {"ir_version": "cad-ir/1", "ir_id": "ir:chain", "ir_hash": "hash",
            "parser": {"name": "test"}, "source": {"kind": "dxf_2d", "attachment_name": "t.dxf"},
            "units": {"drawing_units": "mm", "scale_to_mm": 1.0, "unit_status": "confirmed",
                      "unit_confidence": 1.0, "candidates": []},
            "layers": [], "entities": entities, "texts": [], "dimensions": [],
            "geometry": {"components": components}, "evidence": {}, "unsupported": [],
            "warnings": [], "stats": {}}


def segment(tag, index, start, end, layer="DESIGN"):
    return {"entity_id": "ent:model:%s-%03d" % (tag, index), "type": "LINE", "layer": layer,
            "attributes": {"start": [float(start[0]), float(start[1])],
                           "end": [float(end[0]), float(end[1])]}}


def bbox_of(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def dup_ring(tag, vertices, copies=4, layer="DESIGN"):
    """顶点顺次成环，每条边重复画 copies 份（真图的重复边常态）。"""
    rows, eids, points = [], [], []
    for index in range(len(vertices)):
        start, end = vertices[index], vertices[(index + 1) % len(vertices)]
        points.extend([start, end])
        for repeat in range(copies):
            entity = segment("%s-%d" % (tag, repeat), index, start, end, layer)
            rows.append(entity)
            eids.append(entity["entity_id"])
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids,
                 "bbox": bbox_of(points)}
    return component, rows


def ring(tag, x0, y0, w, h, layer="DESIGN"):
    return dup_ring(tag, [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)],
                    copies=1, layer=layer)


def open_lines(tag, lines, layer="DESIGN"):
    rows, eids, points = [], [], []
    for index, (start, end) in enumerate(lines):
        entity = segment(tag, index, start, end, layer)
        rows.append(entity)
        eids.append(entity["entity_id"])
        points.extend([start, end])
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids, "bbox": bbox_of(points)}
    return component, rows


def combine(tag, pieces, layer="DESIGN"):
    """把若干分量并成一个分量（bbox 取并集），实体 id 全部保留。"""
    rows, eids, boxes = [], [], []
    index = 0
    for _component, entities in pieces:
        for entity in entities:
            copied = dict(entity)
            copied["entity_id"] = "ent:model:%s-m%03d" % (tag, index)
            copied["layer"] = layer
            index += 1
            rows.append(copied)
            eids.append(copied["entity_id"])
        for entity in entities:
            bbox = entity["attributes"]
            boxes.extend([bbox["start"], bbox["end"]])
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids, "bbox": bbox_of(boxes)}
    return component, rows


def circle_vertices(count, radius=100.0):
    import math
    return [(radius * math.cos(2 * math.pi * i / count), radius * math.sin(2 * math.pi * i / count))
            for i in range(count)]


def row_of(doc, component_id):
    for row in doc.get("parts") or []:
        if row.get("component_id") == component_id:
            return row
    raise AssertionError("零件文档里没有分量 %s" % component_id)


class ChainingCase(unittest.TestCase):
    def module(self):
        for name in ("extract", "outline_diagnosis", "summarize"):
            self.assertTrue(callable(getattr(packaging_parts, name, None)),
                            "packaging_parts 缺 %s()（Spec §5.1）" % name)
        return packaging_parts

    def extract(self, pieces):
        return packaging_parts.extract(make_ir(pieces))


# --------------------------------------------------------------------------- #
# A 组：常量与闭集
# --------------------------------------------------------------------------- #
class AConstants(ChainingCase):
    def test_a1_chain_rule_id(self):
        self.assertEqual(packaging_parts.CHAIN_RULE_ID, CHAIN_RULE_ID,
                         "规则号必须逐字等于 Spec §2")

    def test_a2_thresholds_are_frozen(self):
        self.assertEqual(float(packaging_parts.EDGE_COLLAPSE_TOLERANCE_MM),
                         EDGE_COLLAPSE_TOLERANCE_MM, "折叠容差必须等于 Spec §2")
        self.assertEqual(float(packaging_parts.OUTLINE_BBOX_COVER_RATIO),
                         OUTLINE_BBOX_COVER_RATIO, "外轮廓 bbox 覆盖率门槛必须等于 Spec §2")

    def test_a3_open_reasons_closed_set(self):
        self.assertEqual(tuple(packaging_parts.OUTLINE_OPEN_REASONS), OPEN_REASONS,
                         "开线原因闭集必须逐字等于 Spec §2.4（顺序即判定顺序）")

    def test_a4_diagnosis_is_callable(self):
        self.assertTrue(callable(getattr(packaging_parts, "outline_diagnosis", None)),
                        "必须提供 outline_diagnosis()（Spec §2.3）")


# --------------------------------------------------------------------------- #
# B 组：诊断
# --------------------------------------------------------------------------- #
class BDiagnosis(ChainingCase):
    def test_b1_duplicate_edges_are_reported(self):
        vertices = circle_vertices(24)
        piece = dup_ring("A", vertices, copies=4)
        row = row_of(self.extract([piece]), "cmp:A")
        diagnosis = row.get("outline_diagnosis")
        self.assertIsInstance(diagnosis, dict, "每件必须带 outline_diagnosis（Spec §2.3）")
        for key in DIAGNOSIS_KEYS:
            self.assertIn(key, diagnosis, "诊断缺 %s（Spec §2.3）" % key)
        self.assertEqual(int(diagnosis["edges_total"]), 96)
        self.assertEqual(int(diagnosis["edges_unique"]), 24)
        self.assertEqual(int(diagnosis["collapsed_total"]), 72)

    def test_b2_open_lines_report_odd_vertices(self):
        piece = open_lines("B", [((0, 0), (200, 0)), ((200, 0), (200, 100)), ((200, 100), (0, 100)),
                                 ((0, 100), (0, 40)), ((300, 0), (300, 60))])
        row = row_of(self.extract([piece]), "cmp:B")
        diagnosis = row.get("outline_diagnosis") or {}
        self.assertEqual(int(diagnosis.get("odd_degree_vertices") or 0), 4,
                         "奇度顶点数必须如实统计（Spec §2.3）")
        self.assertFalse(diagnosis.get("budget_exhausted"))
        self.assertFalse(diagnosis.get("cycles_found"))

    def test_b3_closed_part_has_no_budget_abort(self):
        piece = ring("C", 0, 0, 200, 100)
        row = row_of(self.extract([piece]), "cmp:C")
        self.assertEqual(row["outline_status"], "closed")
        self.assertFalse((row.get("outline_diagnosis") or {}).get("budget_exhausted"))


# --------------------------------------------------------------------------- #
# C 组：折叠 + rescue
# --------------------------------------------------------------------------- #
class CCollapseRescue(ChainingCase):
    def test_c1_duplicate_ring_is_closed_and_folding_is_reported(self):
        vertices = circle_vertices(24)
        piece = dup_ring("D", vertices, copies=4)
        row = row_of(self.extract([piece]), "cmp:D")
        self.assertEqual(row["outline_status"], "closed",
                         "重复边环必须判成闭合（Spec §2.1/2.2）")
        self.assertEqual(row.get("size_source"), "closed_outline")
        diagnosis = row.get("outline_diagnosis") or {}
        self.assertEqual(int(diagnosis.get("edges_unique") or 0), 24)
        self.assertEqual(int(diagnosis.get("collapsed_total") or 0), 72)
        self.assertIsNone((row.get("outline") or {}).get("compose"),
                          "没走 rescue 的件不许出现 compose（Spec §2.2 最后一条）")

    def test_c2_inner_ring_inside_a_big_frame_is_not_an_outline(self):
        small = ring("E1", 100, 100, 30, 30)
        frame = open_lines("E2", [((0, 0), (600, 0)), ((600, 0), (600, 400))])
        piece = combine("E", [small, frame])
        row = row_of(self.extract([piece]), "cmp:E")
        self.assertNotEqual(row["outline_status"], "closed",
                            "内圈远小于分量时不许当成外轮廓（Spec §2.2）")
        self.assertIn(row.get("outline_reason"), OPEN_REASONS)
        self.assertNotEqual(row.get("outline_reason"), "no_closed_loop")

    def test_c3_genuinely_open_part_says_odd_endpoints(self):
        piece = open_lines("F", [((0, 0), (200, 0)), ((200, 0), (200, 100)), ((0, 100), (200, 100))])
        row = row_of(self.extract([piece]), "cmp:F")
        self.assertEqual(row["outline_status"], "open")
        self.assertEqual(row.get("outline_reason"), "odd_endpoints",
                         "真开线必须报 odd_endpoints（Spec §2.4 序 4）")
        self.assertNotEqual(row.get("size_source"), "closed_outline",
                            "open 件尺寸口径不许变（Spec §2.5 第 4 条）")

    def test_c4_already_closed_outline_is_not_recomputed(self):
        piece = ring("G", 0, 0, 200, 100)
        row = row_of(self.extract([piece]), "cmp:G")
        self.assertIsNone((row.get("outline") or {}).get("compose"),
                          "没走 rescue 的件不许出现 compose（Spec §2.2）")
        self.assertEqual(len((row.get("outline") or {}).get("points") or []), 4,
                         "已闭合件的轮廓点不许变（本 Spec 只救判错的件）")

    def test_c5_duplicated_rectangle_keeps_the_same_outline(self):
        piece = dup_ring("H", [(0, 0), (200, 0), (200, 100), (0, 100)], copies=3)
        row = row_of(self.extract([piece]), "cmp:H")
        self.assertEqual(row["outline_status"], "closed")
        points = (row.get("outline") or {}).get("points") or []
        self.assertEqual(len(points), 4, "折叠后仍应取同一个外轮廓（4 个角点）")
        self.assertIsNone((row.get("outline") or {}).get("compose"))


# --------------------------------------------------------------------------- #
# D 组：护栏
# --------------------------------------------------------------------------- #
class DGuards(unittest.TestCase):
    def test_d1_no_closed_loop_literal_left(self):
        source = PARTS_PY.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("no_closed_loop", source,
                         "笼统原因必须从代码里消失（Spec §2.5 第 1 条）")

    def test_d2_search_budget_literals_unchanged(self):
        self.assertEqual(int(packaging_parts.MAX_LOOP_CYCLES), MAX_LOOP_CYCLES,
                         "环数上限不许为了转绿被调大（Spec §6）")
        self.assertEqual(int(packaging_parts.MAX_LOOP_STATES), MAX_LOOP_STATES,
                         "状态上限不许为了转绿被调大（Spec §6）")

    def test_d3_reject_codes_unchanged(self):
        self.assertEqual(tuple(packaging_parts.PROCESS_REJECT_CODES),
                         ("PACKAGING_PART_NOT_CLOSED", "PACKAGING_PART_MATERIAL_UNKNOWN",
                          "PACKAGING_PART_NOT_FOUND"))
        self.assertEqual(float(packaging_parts.LOOP_TOLERANCE_MM), 1.0)

    def test_d4_extract_is_deterministic(self):
        pieces = [dup_ring("H", circle_vertices(12), copies=2),
                  open_lines("I", [((0, 0), (200, 0))])]
        ir = make_ir(pieces)
        first = packaging_parts.extract(ir)
        second = packaging_parts.extract(ir)
        self.assertEqual(first.get("parts"), second.get("parts"))
        self.assertEqual(first.get("stats"), second.get("stats"))


# --------------------------------------------------------------------------- #
# E 组：真实样本门槛（Spec §3）
# --------------------------------------------------------------------------- #
class ERealSample(ChainingCase):
    @classmethod
    def setUpClass(cls):
        cls._cache = {}

    def _doc(self, filename):
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
        doc = packaging_parts.extract(ir)
        self._cache[filename] = doc
        return doc

    def test_e1_closed_ratio(self):
        summary = packaging_parts.summarize(self._doc("酒盒.dwg"))
        # 2026-09-22：比值分母随 `## 308`（零件文档保留全量件）由 64 变 263，按比值标定的 0.88 假性失败；
        # 分子反而涨了（closed_total 40 → 134），所以改成**绝对分子地板**。
        # 见 `docs/specs/packaging-parts-list-visibility-and-kinds.md` §6。
        self.assertGreaterEqual(int(summary["closed_total"]), 40,
                                "酒盒 closed_total 地板 40（Spec `packaging-parts-list-visibility-and-kinds.md` §6）")

    def test_e2_rescue_total(self):
        doc = self._doc("酒盒.dwg")
        summary = packaging_parts.summarize(doc)
        self.assertIn("collapsed_rescue_total", summary, "summarize() 缺 collapsed_rescue_total")
        self.assertIn("collapsed_edge_total", summary, "summarize() 缺 collapsed_edge_total")
        self.assertGreaterEqual(int(summary["collapsed_rescue_total"]), 6,
                                "真图上至少 6 件应在折叠后被救回（Spec §3）")
        self.assertGreaterEqual(int(summary["collapsed_edge_total"]), 30,
                                "真图重复边的量级至少 30 条（Spec §3）")
        flagged = [row for row in doc["parts"]
                   if "compose" in (row.get("outline") or {})]
        self.assertGreaterEqual(len(flagged), 6, "rescue 成功的件必须留痕 compose")
        for row in flagged:
            compose = row["outline"]["compose"]
            self.assertEqual(compose.get("kind"), "collapsed_cycle")
            self.assertEqual(compose.get("rule_id"), CHAIN_RULE_ID)
            self.assertGreaterEqual(float(compose.get("bbox_cover") or 0.0), OUTLINE_BBOX_COVER_RATIO)

    def test_e3_no_budget_abort_left(self):
        summary = packaging_parts.summarize(self._doc("酒盒.dwg"))
        self.assertIn("budget_exhausted_total", summary, "summarize() 缺 budget_exhausted_total")
        self.assertEqual(int(summary["budget_exhausted_total"]), 0,
                         "折叠后不应再有预算中止（今天 13，Spec §3）")

    def test_e4_open_reason_mix_has_no_vague_reason(self):
        doc = self._doc("酒盒.dwg")
        summary = packaging_parts.summarize(doc)
        self.assertIn("open_reason_mix", summary, "summarize() 缺 open_reason_mix")
        self.assertNotIn("no_closed_loop", summary["open_reason_mix"])
        opens = [row for row in doc["parts"] if row.get("outline_status") == "open"]
        # 2026-09-22：上限 7 是"前 64 件"时代的绝对值，`## 308` 之后 open 件真实为 129 件
        # （不是能力退步，是原来那 199 件根本没进文档）。改为对分母无关的不变式：
        # **必须存在 open 件，且绝不可能全部都是 open**。
        self.assertTrue(1 <= len(opens) < len(doc["parts"]),
                        "真图上必须存在 open 件、但不许全部都是 open（Spec `packaging-parts-list-visibility-and-kinds.md` §6）")
        for row in opens:
            self.assertIn(row.get("outline_reason"), OPEN_REASONS,
                          "%s 的原因必须落在闭集里" % row.get("part_code"))
            self.assertNotEqual(row.get("size_source"), "closed_outline")


if __name__ == "__main__":
    unittest.main(verbosity=2)
