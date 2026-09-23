"""红测：零件真实轮廓与可信尺寸（第 1 层）。

Spec：`docs/specs/packaging-parts-true-outline.md`

**现状缺口（本机实测）**：`packaging_parts` 的每件只有一个分量包围盒——`area = length * width`
（`packaging_parts.py:178`）是**包围盒面积**；真实 `酒盒.dwg` 402 个连通分量里只有 2 个含闭合实体
（且都是图框、已被过滤），所以 kept 的 64 件**闭合数 = 0**、`by_role = {unknown: 64}`，
64 件里只有 27 种尺寸（18 组重复）。只靠实体 `closed` 标志救不了它，必须补**线段首尾相接的链式闭合**；
而链式闭合又需要端点坐标，`CAD IR` 目前把折线顶点丢成 `attributes.vertices` 数量（`parser.py:335`）。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import hashlib
import io
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

from tech_app.backend.services import cad_ir, packaging_parts  # noqa: E402

SAMPLES_DIR = ROOT / "裕同包装项目-待开发"

LOOP_TOLERANCE_MM = 1.0
OUTLINE_STATUSES = ("closed", "open", "unavailable")
SIZE_SOURCES = ("closed_outline", "component_bbox", "dwg_outline")


# --------------------------------------------------------------------------- #
# 合成 IR 助手
# --------------------------------------------------------------------------- #
def _line(entity_id: str, start, end, layer: str = "CUT") -> dict:
    x0, y0 = start
    x1, y1 = end
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    return {"entity_id": entity_id, "handle": entity_id.split(":")[-1], "type": "LINE",
            "kind": "line", "layer": layer, "space": "model", "block_path": [],
            "closed": False, "bbox": [x0, y0, x1, y1], "length": length, "length_mm": length,
            "area": None, "area_mm2": None,
            "attributes": {"start": [x0, y0], "end": [x1, y1]},
            "evidence_ref": "ev:E:%s" % entity_id.split(":")[-1]}


def _rect(entity_id: str, x0: float, y0: float, x1: float, y1: float, layer: str = "CUT") -> list:
    """四条首尾相接的 LINE（闭合标志仍为 False —— 正是真图的样子）。"""
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [_line("%s-%d" % (entity_id, index),
                  corners[index], corners[(index + 1) % 4], layer=layer)
            for index in range(4)]


def _ir(entities: list, components: list, *, unit_status: str = "confirmed",
        extents=None, layers=None) -> dict:
    evidence = {}
    for entity in entities:
        evidence[entity["evidence_ref"]] = {"handle": entity["handle"], "kind": "entity",
                                            "layer": entity["layer"], "spatial": [0.0, 0.0]}
    boxes = [entity["bbox"] for entity in entities if entity.get("bbox")]
    span = extents or [min(b[0] for b in boxes), min(b[1] for b in boxes),
                       max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return {"ir_id": "ir:red", "ir_hash": "red", "ir_version": 1,
            "units": {"candidates": [], "drawing_units": "mm", "scale_to_mm": 1.0,
                      "unit_confidence": 1.0, "unit_status": unit_status},
            "document": {"dxf_version": "AC1027", "extents": span, "layouts": [],
                         "model_space": {"entity_count": len(entities)},
                         "paper_space": {"entity_count": 0}, "warnings": []},
            "entities": entities, "layers": layers or [{"name": "CUT", "role": "cut"}],
            "evidence": evidence, "texts": [], "dimensions": [],
            "geometry": {"components": components, "repeated_groups": []},
            "stats": {}, "source": {"kind": "dxf_2d", "attachment_name": "red.dwg"},
            "parser": {"name": "ezdxf", "options": {}, "version": "1.4.4"}}


def _component(component_id: str, entities: list) -> dict:
    boxes = [entity["bbox"] for entity in entities if entity.get("bbox")]
    return {"component_id": component_id,
            "entity_ids": [entity["entity_id"] for entity in entities],
            "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes)],
            "closed_cycles": 0}


class OutlineCase(unittest.TestCase):
    def extract(self, ir, **kwargs):
        return packaging_parts.extract(ir, **kwargs)

    def part(self, doc, code="DWG-P01"):
        for row in doc.get("parts") or []:
            if row["part_code"] == code:
                return row
        self.fail("零件文档里没有 %s" % code)


# --------------------------------------------------------------------------- #
# A 组：契约
# --------------------------------------------------------------------------- #
class AContract(OutlineCase):
    def test_a1_new_constants_exist(self):
        self.assertAlmostEqual(float(packaging_parts.LOOP_TOLERANCE_MM), LOOP_TOLERANCE_MM,
                               msg="缺少 LOOP_TOLERANCE_MM（Spec §3）")
        self.assertEqual(tuple(packaging_parts.OUTLINE_STATUSES), OUTLINE_STATUSES,
                         "OUTLINE_STATUSES 必须逐字等于 Spec §3")
        self.assertEqual(tuple(packaging_parts.SIZE_SOURCES), SIZE_SOURCES,
                         "SIZE_SOURCES 必须逐字等于 Spec §3")

    def test_a2_every_part_carries_outline_keys(self):
        ir = _ir(_rect("ent:model:R", 0.0, 0.0, 100.0, 50.0),
                 [_component("cmp:1", _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0))])
        doc = self.extract(ir)
        for row in doc["parts"]:
            for key in ("outline_status", "outline", "outline_reason", "size_source"):
                self.assertIn(key, row, "每件必须有 %s" % key)
            self.assertIn(row["outline_status"], OUTLINE_STATUSES)
            self.assertIn(row["size_source"], SIZE_SOURCES)

    def test_a3_old_keys_survive(self):
        ir = _ir(_rect("ent:model:R", 0.0, 0.0, 100.0, 50.0),
                 [_component("cmp:1", _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0))])
        row = self.part(self.extract(ir))
        for key in ("part_code", "name", "unfolded_length_mm", "unfolded_width_mm",
                    "area_mm2", "layers", "role", "component_id", "entity_ids",
                    "evidence_refs", "repeat_of", "size_source"):
            self.assertIn(key, row, "旧键 %s 不许删" % key)


# --------------------------------------------------------------------------- #
# B 组：链式闭合（真图的样子：closed 标志全 False）
# --------------------------------------------------------------------------- #
class BChainClosure(OutlineCase):
    def test_b1_four_joined_lines_make_a_closed_outline(self):
        entities = _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0)
        self.assertTrue(all(not e["closed"] for e in entities), "夹具前提：closed 标志全 False")
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)]))
        row = self.part(doc)
        self.assertEqual(row["outline_status"], "closed",
                         "四条首尾相接的 LINE 必须被判成闭合件（真图正是这样）")
        self.assertEqual(row["size_source"], "closed_outline")
        self.assertEqual(row["outline_reason"], "")
        self.assertAlmostEqual(row["area_mm2"], 5000.0, places=3,
                               msg="面积必须是鞋带公式算出的环面积（100×50），不是包围盒")

    def test_b2_outline_carries_points_and_evidence(self):
        entities = _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0)
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)]))
        outline = self.part(doc)["outline"]
        self.assertTrue(outline["closed"])
        self.assertGreaterEqual(len(outline["points"]), 4, "环至少要 4 个顶点")
        self.assertEqual(len(outline["entity_ids"]), 4, "环必须能回查它用了哪几条实体")
        self.assertAlmostEqual(outline["area_mm2"], 5000.0, places=3)

    def test_b3_size_comes_from_the_loop_not_the_component(self):
        # 分量 bbox 被一条很长的、不属于环的线撑大：尺寸仍必须取环。
        entities = _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0)
        entities.append(_line("ent:model:STRAY", (0.0, 0.0), (900.0, 0.0), layer="0"))
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)]))
        row = self.part(doc)
        self.assertEqual(row["outline_status"], "closed")
        self.assertAlmostEqual(float(row["unfolded_length_mm"]), 100.0, places=3)
        self.assertAlmostEqual(float(row["unfolded_width_mm"]), 50.0, places=3)

    def test_b4_tolerance_is_applied(self):
        # 相邻端点差 0.4mm（< 1.0mm 容差）仍必须闭合。
        entities = _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0)
        broken = list(entities)
        last = dict(broken[-1])
        # 夹具修正（实现期，断言未改）：`_rect` 的第 4 条边是 (0,50)->(0,0)，"端点差 0.4mm"
        # 只能是把它改成 (0,50)->(0.4,0)；原写法写成 (0,0)->(0.4,0)，那一刻起第 4 条边就不再
        # 连接 D(0,50)，环永远合不上（与本用例注释自相矛盾，任何实现都过不了）。
        last["attributes"] = {"start": [0.0, 50.0], "end": [0.4, 0.0]}
        last["bbox"] = [0.0, 0.0, 0.4, 50.0]
        broken[-1] = last
        doc = self.extract(_ir(broken, [_component("cmp:1", broken)]))
        self.assertEqual(self.part(doc)["outline_status"], "closed",
                         "端点差在 LOOP_TOLERANCE_MM 内必须算闭合")

    def test_b5_far_gap_is_not_closed(self):
        entities = _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0)
        entities[-1] = _line("ent:model:R-3", (0.0, 20.0), (0.0, 0.0))   # 缺口 30mm
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)]))
        self.assertEqual(self.part(doc)["outline_status"], "open",
                         "缺口远超容差就不许算闭合")


# --------------------------------------------------------------------------- #
# C 组：多环取最大
# --------------------------------------------------------------------------- #
class CLargestLoop(OutlineCase):
    def test_c1_largest_loop_wins(self):
        big = _rect("ent:model:BIG", 0.0, 0.0, 200.0, 100.0)
        small = _rect("ent:model:SMALL", 300.0, 300.0, 320.0, 310.0)
        entities = big + small
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)]))
        row = self.part(doc)
        self.assertEqual(row["outline_status"], "closed")
        self.assertAlmostEqual(row["area_mm2"], 200.0 * 100.0, places=3,
                               msg="同件多环必须取面积最大的那个")

    def test_c2_two_loops_can_live_in_one_component(self):
        big = _rect("ent:model:BIG", 0.0, 0.0, 200.0, 100.0)
        small = _rect("ent:model:SMALL", 300.0, 300.0, 320.0, 310.0)
        entities = big + small
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)]))
        row = self.part(doc)
        self.assertEqual(len(row["outline"]["entity_ids"]), 4,
                         "取的是最大环：应当正好是那 4 条边，不是 8 条")


# --------------------------------------------------------------------------- #
# D 组：开放件必须显式降级
# --------------------------------------------------------------------------- #
class DDegrade(OutlineCase):
    def test_d1_open_component_says_so(self):
        # 夹具修正（实现期，断言未改）：原三条线**共线**（bbox 高度 0 → 分量面积 0），
        # 会被 `area_under_min` 正确挡掉，零件根本不存在；而 extraction_red H1 明确要求
        # "每件 unfolded_width_mm > 0"，即退化件不许当零件。这里给三条线各自的高度，
        # 让它表达本用例真正要测的东西：**互不相接**（求不出环 → 显式降级）。
        entities = [_line("ent:model:A", (0.0, 0.0), (100.0, 0.0)),
                    _line("ent:model:B", (200.0, 0.0), (300.0, 40.0)),
                    _line("ent:model:C", (400.0, 0.0), (500.0, 60.0))]
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)]))
        row = self.part(doc)
        self.assertEqual(row["outline_status"], "open")
        # `packaging-parts-outline-chaining.md` §2.5 第 1 条要求 `no_closed_loop` 这个笼统值
        # 从服务端源码里消失、每件必须说出 5 个具体原因之一；本夹具是三条互不相接的线段
        # （6 个奇度顶点、最近配对间隙 ≈100mm）→ 实际值为 `odd_endpoints`。断言改成"落在闭集
        # 里且不再是那个笼统值"，用例原意（求不出环 → 显式降级）不变。
        self.assertIn(row["outline_reason"], packaging_parts.OUTLINE_OPEN_REASONS)
        self.assertNotEqual(row["outline_reason"], "no_closed_loop")
        self.assertEqual(row["size_source"], "component_bbox",
                         "求不出轮廓时必须退回分量包围盒**并留痕**")
        self.assertIsNone(row["outline"]["points"] or None)

    def test_d2_too_small_loop_reports_its_own_reason(self):
        entities = _rect("ent:model:TINY", 0.0, 0.0, 20.0, 10.0)   # 面积 200 < min_area 2000
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)]))
        row = self.part(doc)
        self.assertEqual(row["outline_status"], "open")
        self.assertEqual(row["outline_reason"], "loop_too_small",
                         "被最小面积挡掉要说清是 loop_too_small，不是 no_closed_loop")


# --------------------------------------------------------------------------- #
# E 组：单位未确认
# --------------------------------------------------------------------------- #
class EUnit(OutlineCase):
    def test_e1_unconfirmed_unit_gives_unavailable(self):
        entities = _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0)
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)],
                               unit_status="candidate"))
        row = self.part(doc)
        self.assertEqual(row["outline_status"], "unavailable")
        self.assertEqual(row["outline_reason"], "unit_unconfirmed")
        self.assertIsNone(row["unfolded_length_mm"])
        self.assertIsNone(row["unfolded_width_mm"])


# --------------------------------------------------------------------------- #
# F 组：stats
# --------------------------------------------------------------------------- #
class FStats(OutlineCase):
    def test_f1_stats_add_new_keys_without_dropping_old(self):
        entities = _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0)
        doc = self.extract(_ir(entities, [_component("cmp:1", entities)]))
        stats = doc["stats"]
        for key in ("closed_total", "open_total", "outline_unavailable_total", "closed_ratio"):
            self.assertIn(key, stats, "stats 缺少 %s" % key)
        for key in ("part_total", "filtered_total", "truncated", "by_role"):
            self.assertIn(key, stats, "旧 stats 键 %s 不许删" % key)

    def test_f2_three_states_add_up_and_ratio_is_consistent(self):
        closed = _rect("ent:model:R", 0.0, 0.0, 100.0, 50.0)
        opened = [_line("ent:model:O1", (0.0, 0.0), (100.0, 0.0)),
                  _line("ent:model:O2", (0.0, -50.0), (100.0, -50.0))]
        # 两件：一件闭合、一件开放（都过 min_area 门槛）
        opened = [_line("ent:model:O1", (0.0, 0.0), (200.0, 0.0)),
                  _line("ent:model:O2", (0.0, -60.0), (200.0, -60.0))]
        entities = closed + opened
        components = [_component("cmp:1", closed), _component("cmp:2", opened)]
        doc = self.extract(_ir(entities, components))
        stats = doc["stats"]
        self.assertEqual(stats["part_total"], 2)
        self.assertEqual(stats["closed_total"] + stats["open_total"]
                         + stats["outline_unavailable_total"], stats["part_total"])
        self.assertAlmostEqual(float(stats["closed_ratio"]),
                               float(stats["closed_total"]) / float(stats["part_total"]), places=6)


# --------------------------------------------------------------------------- #
# G 组：IR 折线顶点必须落盘（第 1 层的前置）
# --------------------------------------------------------------------------- #
class GIrPolyline(OutlineCase):
    def _lwpolyline_ir(self):
        try:
            import ezdxf
        except Exception:  # pragma: no cover
            self.skipTest("本机没有 ezdxf")
        document = ezdxf.new("R2010")
        msp = document.modelspace()
        msp.add_lwpolyline([(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)],
                           close=True, dxfattribs={"layer": "CUT"})
        buffer = io.StringIO()
        document.write(buffer)
        return cad_ir.parse_dxf(buffer.getvalue().encode("utf-8"), filename="loop.dxf",
                                source={"kind": "dxf_2d", "attachment_name": "loop.dxf"})

    def test_g1_lwpolyline_vertices_are_persisted(self):
        ir = self._lwpolyline_ir()
        rows = [e for e in ir["entities"] if e["type"] in ("LWPOLYLINE", "POLYLINE")]
        self.assertTrue(rows, "夹具里应当有折线实体")
        row = rows[0]
        points = (row.get("attributes") or {}).get("points")
        self.assertIsNotNone(points, "折线顶点坐标必须落进 attributes.points（Spec §2，parser.py:335）")
        self.assertEqual(len(points), int(row["attributes"]["vertices"]),
                         "落盘点数必须与 attributes.vertices 一致")
        self.assertEqual(len(points), 4)

    def test_g2_closed_polyline_yields_a_closed_outline(self):
        ir = self._lwpolyline_ir()
        rows = [e for e in ir["entities"] if e["type"] in ("LWPOLYLINE", "POLYLINE")]
        ids = [e["entity_id"] for e in rows]
        component = {"component_id": "cmp:1", "entity_ids": ids,
                     "bbox": [0.0, 0.0, 100.0, 50.0], "closed_cycles": 0}
        doc = self.extract(_ir(rows, [component]))
        row = self.part(doc)
        self.assertEqual(row["outline_status"], "closed")
        self.assertAlmostEqual(row["area_mm2"], 5000.0, places=3)


# --------------------------------------------------------------------------- #
# H 组：真实样本
# --------------------------------------------------------------------------- #
class HRealSamples(OutlineCase):
    def real_ir(self, filename: str):
        sample = SAMPLES_DIR / filename
        if not sample.exists():
            self.skipTest("真实样本不在本机：%s" % sample)
        tool = shutil.which("dwg2dxf")
        if not tool:
            self.skipTest("本机没有 libredwg 的 dwg2dxf")
        digest = hashlib.sha256(sample.read_bytes()).hexdigest()[:16]
        cache = pathlib.Path(tempfile.mkdtemp()) / ("real_%s.dxf" % digest)
        subprocess.run([tool, "-y", "-o", str(cache), str(sample)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return cad_ir.parse_dxf(cache.read_bytes(), filename=cache.name,
                                source={"kind": "dxf_2d", "attachment_name": filename})

    def test_h1_wine_box_gets_closed_parts(self):
        doc = self.extract(self.real_ir("酒盒.dwg"))
        self.assertGreater(doc["stats"]["closed_total"], 0,
                           "真实酒盒.dwg 今天闭合数 = 0；链式闭合落地后必须 > 0")

    def test_h2_disc_box_keeps_its_closed_parts(self):
        doc = self.extract(self.real_ir("圆盘盒.dwg"))
        self.assertGreaterEqual(doc["stats"]["closed_total"], 8,
                                "圆盘盒今天已有 8 件含闭合实体，不许回退")

    def test_h3_closed_size_never_grows(self):
        doc = self.extract(self.real_ir("圆盘盒.dwg"))
        for row in doc["parts"]:
            if row["outline_status"] != "closed":
                continue
            self.assertLessEqual(row["outline"]["bbox"][2] - row["outline"]["bbox"][0],
                                 row["unfolded_length_mm"] + 1.0,
                                 "环的 bbox 不可能比对外尺寸更宽（%s）" % row["part_code"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
