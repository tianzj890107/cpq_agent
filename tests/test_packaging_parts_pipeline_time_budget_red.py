"""红测：包装零件链路的**时间预算**（逐件诊断不许超线性、部署自检不许无限等）。

Spec：`docs/specs/packaging-parts-pipeline-time-budget.md`
前置：假设 `packaging-parts-outline-chaining.md` 的重复边折叠/外轮廓重判已实现。

现状缺口（34 实测 + 本机同代码）：
  · 34 部署 `9f4fcfe` 后，`scripts/deploy_34_bare.sh` 第 6b 步的 `$PY -` 进程 100% CPU 连跑
    22 分 54 秒无任何输出（`ps -o pid,etime,time,pcpu`），而 8010 服务侧同提交跑同一份
    `酒盒.dwg` 是正常的（八步 19.2s）—— 两条路不同口径，且慢的那条**没有任何东西会停下来**；
  · 热点：`_outline_evidence()` 对**每一个**分量都算 `_nearest_gap_mm()`（`extract()` 里无条件调用），
    而 `9f4fcfe` 的实现是"每轮取最近的一对、移除后重扫全部点对" ⇒ `O(N^3)`。

A 组用**距离计算次数**（不是钟表）钉复杂度，本机秒级可复现；
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import re
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_part_solids, packaging_parts  # noqa: E402
from tech_app.backend.services.cad_ir import geometry as cad_geometry  # noqa: E402

DEPLOY_SH = ROOT / "scripts" / "deploy_34_bare.sh"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
FIXTURE_IR = ROOT / "tests" / "fixtures" / "cad_ir" / "parts_panels.json"

TIME_BUDGET_MS = 2000
EXTRACT_BUDGET_S = 20.0
EXTRUDE_BUDGET_MS = 500.0
DEPLOY_TIMEOUT_SECONDS = 900
SPEC_BLOCK = "6b."

#: D 组要用"能过过滤门槛"的合成件（默认门槛会把这些小件整件滤掉）。
PERMISSIVE = {"min_area_mm2": 0.0, "max_edge_mm": 100000.0,
              "max_area_mm2": 1e12, "max_parts": 512}


# --------------------------------------------------------------------------- #
# 合成 IR
# --------------------------------------------------------------------------- #
def make_ir(pieces):
    components, entities = [], []
    for component, rows in pieces:
        components.append(component)
        entities.extend(rows)
    return {"ir_version": "cad-ir/1", "ir_id": "ir:budget", "ir_hash": "hash",
            "parser": {"name": "test"}, "source": {"kind": "dxf_2d", "attachment_name": "t.dxf"},
            "units": {"drawing_units": "mm", "scale_to_mm": 1.0, "unit_status": "confirmed",
                      "unit_confidence": 1.0, "candidates": []},
            "layers": [], "entities": entities, "texts": [], "dimensions": [],
            "geometry": {"components": components}, "evidence": {}, "unsupported": [],
            "warnings": [], "stats": {}}


def segment(tag, index, start, end, layer="DESIGN"):
    return {"entity_id": "ent:model:%s-%04d" % (tag, index), "type": "LINE", "layer": layer,
            "attributes": {"start": [float(start[0]), float(start[1])],
                           "end": [float(end[0]), float(end[1])]}}


def bbox_of(points):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def open_chain(tag, count, spacing=50.0):
    """count 条互不相连的短线段 → 2*count 个奇度顶点（开放刀口的合成版）。"""
    rows, eids, points = [], [], []
    for index in range(count):
        start = (index * spacing, 0.0)
        end = (index * spacing + 10.0, 0.0)
        entity = segment(tag, index, start, end)
        rows.append(entity)
        eids.append(entity["entity_id"])
        points.extend([start, end])
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids, "bbox": bbox_of(points)}
    return component, rows


def closed_ring(tag, count, radius=500.0):
    """count 条首尾相接的线段 → 0 个奇度顶点（对照，复用同一顶点数/边数量级）。"""
    import math
    vertices = [(radius * math.cos(2 * math.pi * index / count),
                 radius * math.sin(2 * math.pi * index / count)) for index in range(count)]
    rows, eids, points = [], [], []
    for index in range(count):
        start, end = vertices[index], vertices[(index + 1) % count]
        entity = segment(tag, index, start, end)
        rows.append(entity)
        eids.append(entity["entity_id"])
        points.extend([start, end])
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids, "bbox": bbox_of(points)}
    return component, rows


def members_of(ir, component_id):
    entities = {row.get("entity_id"): row for row in ir.get("entities") or []}
    for component in ir["geometry"]["components"]:
        if component.get("component_id") == component_id:
            return [entities[key] for key in component.get("entity_ids") or [] if key in entities]
    raise AssertionError("合成 IR 里没有分量 %s" % component_id)


def count_distance_calls(callable_):
    """跑一次并把 `cad_geometry.distance()` 的调用次数数出来（纯计数，不看钟表）。"""
    real = cad_geometry.distance
    counter = {"calls": 0}

    def wrapper(first, second):
        counter["calls"] += 1
        return real(first, second)

    cad_geometry.distance = wrapper
    try:
        result = callable_()
    finally:
        cad_geometry.distance = real
    return result, counter["calls"]


def deploy_block():
    text = DEPLOY_SH.read_text(encoding="utf-8")
    start = text.index(SPEC_BLOCK)
    rest = text[start + len(SPEC_BLOCK):]
    stops = [rest.index(marker) for marker in ("step \"7.", "step \"7b", "\n# ----")
             if marker in rest]
    end = min(stops) if stops else len(rest)
    return rest[:end]


class ComplexityBudget(unittest.TestCase):
    """A 组：奇度顶点配对的距离计算次数必须是线性的。"""

    def pairing_calls(self, count):
        open_piece = open_chain("open%d" % count, count)
        ring_piece = closed_ring("ring%d" % count, count)
        open_component = open_piece[0]
        ir = make_ir([open_piece, ring_piece])
        _, open_calls = count_distance_calls(
            lambda: packaging_parts.outline_diagnosis(members_of(ir, open_component["component_id"])))
        _, ring_calls = count_distance_calls(
            lambda: packaging_parts.outline_diagnosis(members_of(ir, ring_piece[0]["component_id"])))
        return open_calls - ring_calls

    def test_a1_pairing_calls_are_linear(self):
        count = 50
        calls = self.pairing_calls(count)
        self.assertLessEqual(calls, 4 * 2 * count,
                             "N=%d 个奇度顶点时距离计算 %d 次（上界 %d）：配对不许是 O(N^2) 及以上"
                             % (2 * count, calls, 4 * 2 * count))

    def test_a2_doubling_input_does_not_more_than_double_calls(self):
        small = self.pairing_calls(50)
        large = self.pairing_calls(100)
        self.assertGreater(small, 0, "两次配对之间不可能一次距离计算都不做（对照没取到）")
        self.assertLessEqual(large, 2.2 * small,
                             "规模翻倍调用次数涨了 %.2f 倍（%d → %d）：禁止 O(N^2) 及以上"
                             % (float(large) / float(small or 1), small, large))

    def test_a3_no_odd_vertices_means_no_pairing_work(self):
        component, rows = closed_ring("ring0", 24)
        ir = make_ir([(component, rows)])
        diagnosis, calls = count_distance_calls(
            lambda: packaging_parts.outline_diagnosis(members_of(ir, component["component_id"])))
        self.assertEqual(int(diagnosis.get("odd_degree_vertices") or 0), 0)
        self.assertEqual(float(diagnosis.get("nearest_gap_mm") or 0.0), 0.0)
        self.assertEqual(calls, 0, "没有奇度顶点时不该做任何配对距离计算（实际 %d 次）" % calls)

    def test_a4_single_odd_vertex_is_ignored(self):
        component, rows = open_chain("open1", 1)
        ir = make_ir([(component, rows)])
        diagnosis, calls = count_distance_calls(
            lambda: packaging_parts.outline_diagnosis(members_of(ir, component["component_id"])))
        self.assertEqual(int(diagnosis.get("odd_degree_vertices") or 0), 2)
        self.assertLessEqual(calls, 1, "两个奇度顶点最多配对一次（实际 %d 次）" % calls)
        self.assertGreaterEqual(float(diagnosis.get("nearest_gap_mm") or 0.0), 0.0)


class WallClockBudget(unittest.TestCase):
    """B 组：预算要看得见、夹具链路要有硬上界。"""

    def test_b1_module_declares_time_budget(self):
        budget = getattr(packaging_parts, "TIME_BUDGET_MS", None)
        self.assertIsNotNone(budget, "packaging_parts 缺 TIME_BUDGET_MS（Spec §3.5）")
        self.assertEqual(int(budget), TIME_BUDGET_MS, "TIME_BUDGET_MS 必须是 %d" % TIME_BUDGET_MS)

    def test_b2_diagnosis_reports_elapsed_ms(self):
        component, rows = open_chain("openms", 40)
        ir = make_ir([(component, rows)])
        diagnosis = packaging_parts.outline_diagnosis(members_of(ir, component["component_id"]))
        self.assertIn("elapsed_ms", diagnosis, "诊断必须带 elapsed_ms（Spec §3.4）")
        self.assertGreaterEqual(float(diagnosis["elapsed_ms"]), 0.0)
        self.assertIn("budget_exceeded", diagnosis, "诊断必须带 budget_exceeded（Spec §3.5）")
        self.assertFalse(bool(diagnosis["budget_exceeded"]),
                         "40 条线段的合成件不该超预算；超了说明配对还是在重扫")

    def test_b3_fixture_extract_within_budget(self):
        import json
        ir = json.loads(FIXTURE_IR.read_text(encoding="utf-8"))
        started = time.monotonic()
        doc = packaging_parts.extract(ir)
        spent = time.monotonic() - started
        self.assertTrue((doc.get("parts") or []), "夹具 IR 必须能提出零件")
        self.assertLessEqual(spent, EXTRACT_BUDGET_S,
                             "夹具 IR 的 extract 用了 %.1fs（上界 %.0fs）" % (spent, EXTRACT_BUDGET_S))

    def test_b4_worst_shape_extrude_within_budget(self):
        import math
        points = [[600.0 * math.cos(2 * math.pi * index / 2000),
                   600.0 * math.sin(2 * math.pi * index / 2000)] for index in range(2000)]
        row = {"part_code": "DWG-P99", "outline_status": "closed", "thickness_mm": 2.0,
               "outline": {"points": points}}
        started = time.monotonic()
        out = packaging_part_solids.extrude(row)
        spent_ms = (time.monotonic() - started) * 1000.0
        self.assertEqual(out.get("status"), "ok", "最坏形状（2000 点凸）必须能挤出：%r" % out.get("reason"))
        self.assertLessEqual(spent_ms, EXTRUDE_BUDGET_MS,
                             "单件挤出用了 %.0fms（上界 %.0fms）" % (spent_ms, EXTRUDE_BUDGET_MS))


class DeploySelfCheckBudget(unittest.TestCase):
    """C 组：部署自检第 6b 步必须有超时、有进度（静态钉住，防"无限等"回归）。"""

    def setUp(self):
        self.block = deploy_block()

    def test_c1_selfcheck_is_wrapped_in_timeout(self):
        matches = re.findall(r"timeout\s+(\d+)", self.block)
        self.assertTrue(matches, "第 6b 步没有被 timeout 包裹（Spec §4.8）")
        seconds = min(int(value) for value in matches)
        self.assertLessEqual(seconds, DEPLOY_TIMEOUT_SECONDS,
                             "第 6b 步超时预算 %ds 大于 %ds" % (seconds, DEPLOY_TIMEOUT_SECONDS))

    def test_c2_timeout_exits_nonzero_and_names_the_sample(self):
        self.assertIn("超时", self.block, "超时必须出现在第 6b 步的报错文案里（Spec §4.9）")
        self.assertIn("fail", self.block, "超时必须非零退出（Spec §4.9）")

    def test_c3_each_sample_prints_immediately(self):
        self.assertIn("flush=True", self.block,
                      "第 6b 步必须逐样本 flush 输出（Spec §4.10）")


class OneVerdictTwoPaths(unittest.TestCase):
    """D 组：extract() 与 outline_diagnosis() 不许各有一套诊断。"""

    def test_d1_extract_and_diagnosis_agree(self):
        component, rows = open_chain("agree", 30)
        ir = make_ir([(component, rows)])
        diagnosis = packaging_parts.outline_diagnosis(members_of(ir, component["component_id"]))
        doc = packaging_parts.extract(ir, options=dict(PERMISSIVE))
        row = next((item for item in doc.get("parts") or []
                    if item.get("component_id") == component["component_id"]), None)
        self.assertIsNotNone(row, "extract 没产出该分量")
        inline = row.get("outline_diagnosis") or {}
        self.assertEqual(inline.get("nearest_gap_mm"), diagnosis.get("nearest_gap_mm"),
                         "同一条诊断在两处不一致（Spec §5.11）")
        self.assertEqual(inline.get("odd_degree_vertices"), diagnosis.get("odd_degree_vertices"))

    def test_d2_summary_matches_rows(self):
        component, rows = open_chain("sum", 20)
        ir = make_ir([(component, rows)])
        doc = packaging_parts.extract(ir, options=dict(PERMISSIVE))
        summary = packaging_parts.summarize(doc)
        stats = summary.get("stats") if isinstance(summary.get("stats"), dict) else summary
        parts = doc.get("parts") or []
        self.assertEqual(stats.get("part_total"), len(parts))
        closed = [row for row in parts if row.get("outline_status") == "closed"]
        expected = round(float(len(closed)) / float(len(parts)), 3) if parts else 0.0
        self.assertEqual(round(float(summary.get("closed_ratio") or 0.0), 3), expected)


if __name__ == "__main__":
    unittest.main()
