r"""红测：尺寸标注线不许进零件形状（也不许进轮廓环 / 件内折线 / 件尺寸）。

Spec：`docs/specs/packaging-dimension-annotation-must-not-enter-part-shape.md`

用户原话（2026-09-23）：

> 现在还有一个问题 那些白色的线是用来标注尺寸用的 不能也显示在这个零件里

现状缺口（本机只读实测，用仓库已缓存的真样本 converted.dxf，不重跑 DWG 转换）：
  · 酒盒.dwg 的 CAD IR 里 `DIMENSION 316` 自己**不带线几何**（`ir["dimensions"][0]` 只有
    `declared_value` 与 `target_entity_ids`）—— 尺寸线/尺寸界线本来就是**普通 LINE**；
  · 分组按**端点相接**连通，尺寸界线的一端正好落在零件角点上 ⇒ 标注线与零件永久同组：
    实测 `DWG-P02` 成员 27 条里**只有 19 条**是轮廓边，另外 8 条是尺寸线（`54CA` 长 **219.64mm**，
    等于该图一条标注的 `declared_value` 219.6435）、尺寸界线（88.70 / 88.84 / 267.94 / 214.49）
    与箭头（**5.02mm**）；`DWG-P54` 更极端：84 条成员里只有 6 条是轮廓边；
  · `_component_segments(members)` 把**件内全部实体**折线化 ⇒ 那 219.64 的尺寸线就画进了零件形状
    （前端 `packagingCadPlanComponentSvg()` 逐段原样画，角色 `unknown` ⇒ `#8b949e` 浅灰细线）；
  · 更严重的是**件尺寸被改**：合成夹具最小复现（200×300 的闭合矩形 + 3 条接在角点上的标注线）
    现在跑出来 `outline.points` 是 **6 点、多绕到 y=-50**、`unfolded = 200×350`、`area = 70000`；
  · 判据不能图省事：5598 条 LINE 里 **1716 条**长度等于某个 `declared_value`（±0.5mm），
    只按"等长"删必然误伤真几何。

纪律：只读仓库 + `python` 真跑 `extract()`（合成夹具在测试内构造，不联网、不连库、不重跑 DWG 转换）；
不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import copy
import importlib
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"

PREDICATE = "annotation_entity_ids"
REASONS_NAME = "ANNOTATION_REASONS"
LAYERS_NAME = "ANNOTATION_LAYERS"
ARROW_NAME = "ANNOTATION_ARROW_MAX_MM"
FILTERED_KEY = "annotation_filtered"
STATS_KEY = "annotation_filtered_total"
FRONT_DISCLOSURE = "data-qq-annotation-filtered"
FRONT_COPY = "已剔除标注线"

REASONS = ("dimension_extension", "dimension_line", "dimension_arrow", "annotation_layer")

#: 合成夹具的坐标（mm）：200×300 的闭合矩形 + 3 条接在角点上的标注线。
PANEL = [[0, 0], [200, 0], [200, 300], [0, 300]]
EXT_LEFT = [[0, -50], [0, 0]]          # 尺寸界线（端点落在标注目标点 (0,0)）
EXT_RIGHT = [[200, 0], [200, -50]]     # 尺寸界线（端点落在标注目标点 (200,0)）
DIM_LINE = [[200, -50], [0, -50]]      # 尺寸线（长 200 = declared_value）
DIM_VALUE = 200.0
INNER_REAL = [[50, 50], [150, 50]]     # 真件内几何：不许被误删
INNER_EQUAL = [[0, 150], [200, 150]]   # 长 200 = declared_value，但两端都在轮廓上：不许被误删


def _entity(eid, kind, points=None, closed=None, start=None, end=None, layer="CUT"):
    row = {
        "entity_id": eid, "handle": eid.split(":")[-1], "type": kind.upper(), "kind": kind,
        "layer": layer, "space": "model", "block_path": [], "closed": closed,
        "length": None, "area": None, "length_mm": None, "area_mm2": None,
        "attributes": {}, "evidence_ref": "ev:E:%s" % eid.split(":")[-1],
        "canonical_index": 1, "bbox": None,
    }
    xs, ys = [], []
    if points is not None:
        row["attributes"]["points"] = points
        xs = [p[0] for p in points]; ys = [p[1] for p in points]
    if start is not None:
        row["attributes"]["start"] = start
    if end is not None:
        row["attributes"]["end"] = end
        xs = [start[0], end[0]]; ys = [start[1], end[1]]
    if xs:
        row["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
    return row


def dimension_ir(*, with_real_geometry=False, with_equal_length_decoy=False):
    """合成一份最小 CAD IR：四个实体同属一个 component（尺寸界线接在零件角点上）。"""
    entities = [
        _entity("ent:model:P1", "polyline", points=PANEL, closed=True),
        _entity("ent:model:D1", "line", start=EXT_LEFT[1], end=EXT_LEFT[0], layer="0"),
        _entity("ent:model:D2", "line", start=DIM_LINE[0], end=DIM_LINE[1], layer="0"),
        _entity("ent:model:D3", "line", start=EXT_RIGHT[0], end=EXT_RIGHT[1], layer="0"),
    ]
    if with_real_geometry:
        entities.append(_entity("ent:model:I1", "line", start=INNER_REAL[0], end=INNER_REAL[1]))
    if with_equal_length_decoy:
        entities.append(_entity("ent:model:I2", "line", start=INNER_EQUAL[0], end=INNER_EQUAL[1]))
    ir = {
        "ir_version": "cad-ir/1", "ir_id": "ir:test-annotation", "ir_hash": "hash",
        "source": {"kind": "dxf_2d"},
        "units": {"unit_status": "confirmed", "unit": "mm", "scale_to_mm": 1.0},
        "document": {"extents": [0, -50, 200, 300], "dxf_version": "AC1027"},
        "layers": [{"name": "CUT", "visible": True, "color": 7, "entity_count": 1},
                   {"name": "0", "visible": True, "color": 7, "entity_count": 3},
                   {"name": "Defpoints", "visible": True, "color": 7, "entity_count": 0}],
        "entities": entities,
        "texts": [],
        "dimensions": [{
            "entity_id": "ent:model:DIM1", "handle": "DIM1", "layer": "0", "dim_type": "linear",
            "raw_text": "", "normalized_text": "",
            "declared_value": DIM_VALUE, "measured_value": DIM_VALUE, "unit": "mm",
            "delta": 0.0, "tolerance": 0.0,
            "target_entity_ids": ["point:0.000000,0.000000", "point:200.000000,0.000000"],
            "confidence": 0.5, "evidence_ref": "ev:E:DIM1",
        }],
        "geometry": {
            "closed_outlines": [], "open_outlines": [],
            "components": [{"component_id": "cmp:1",
                            "entity_ids": [row["entity_id"] for row in entities],
                            "bbox": [0, -50, 200, 300], "closed_cycles": 1}],
            "holes": [], "repeated_groups": [], "overlaps": [], "tolerance": 1.0,
        },
        "stats": {"entity_total": len(entities), "layer_total": 3, "closed_outline_total": 1,
                  "open_outline_total": 3, "dimension_total": 1},
        "evidence": {}, "unsupported": [], "warnings": [], "parser": {},
    }
    return ir


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module("tech_app.backend.services.packaging_parts")

    def extract(self, ir):
        return self.module.extract(copy.deepcopy(ir), None)

    def parts_of(self, doc):
        return list(doc.get("parts") or [])

    def segments_of(self, part):
        return [tuple((round(float(point[0]), 3), round(float(point[1]), 3)) for point in seg)
                for seg in (part.get("segments") or [])]


# --------------------------------------------------------------------------- #
# A 组：合成夹具真跑 extract()（标注线不许进形状，且不许改件尺寸）
# --------------------------------------------------------------------------- #
class AAnnotationStaysOut(Base):
    def test_a1_annotation_lines_stay_out_of_the_segments(self):
        part = self.parts_of(self.extract(dimension_ir()))[0]
        drawn = self.segments_of(part)
        banned = [tuple((round(float(p[0]), 3), round(float(p[1]), 3)) for p in seg)
                  for seg in (EXT_LEFT, EXT_RIGHT, DIM_LINE)]
        for seg in banned:
            self.assertNotIn(seg, drawn,
                             "标注线（尺寸线/尺寸界线）不许画进件内折线（Spec §2.2）：%r" % (seg,))

    def test_a2_outline_does_not_walk_through_the_annotation(self):
        part = self.parts_of(self.extract(dimension_ir()))[0]
        points = (part.get("outline") or {}).get("points") or []
        got = [(round(float(p[0]), 3), round(float(p[1]), 3)) for p in points]
        self.assertEqual(4, len(got),
                         "轮廓环只能是那 4 个角点；绕着尺寸界线多走一圈就是把标注当几何（Spec §2.2）：%r"
                         % (got,))
        self.assertNotIn((0.0, -50.0), got, "轮廓环不许走到尺寸线的端点 y=-50（Spec §2.2）")
        self.assertEqual({(0.0, 0.0), (200.0, 0.0), (200.0, 300.0), (0.0, 300.0)}, set(got),
                         "轮廓环就是那把 200×300 的矩形（Spec §2.2）")

    def test_a3_part_size_is_not_inflated_by_the_annotation(self):
        part = self.parts_of(self.extract(dimension_ir()))[0]
        self.assertEqual(200.0, float(part.get("unfolded_length_mm") or 0),
                         "件尺寸不许被标注线撑大（Spec §2.2）")
        self.assertEqual(300.0, float(part.get("unfolded_width_mm") or 0),
                         "件尺寸不许被标注线撑大（现测被撑成 350，Spec §2.2）")
        self.assertEqual(60000.0, float(part.get("area_mm2") or 0),
                         "件面积不许被标注线撑大（现测 70000，Spec §2.2）")

    def test_a4_stats_declare_how_many_were_filtered(self):
        doc = self.extract(dimension_ir())
        stats = doc.get("stats") or {}
        self.assertIn(STATS_KEY, stats,
                      "文档级必须报出剔除了多少条标注实体（Spec §2.3）：%r" % (sorted(stats),))
        self.assertEqual(3, int(stats[STATS_KEY]),
                         "三条标注线必须逐条计入（Spec §2.3）")

    def test_a5_each_part_keeps_the_trace(self):
        part = self.parts_of(self.extract(dimension_ir()))[0]
        rows = part.get(FILTERED_KEY)
        self.assertIsInstance(rows, list,
                              "逐件要留痕（Spec §2.3）：%r" % (rows,))
        self.assertEqual(3, len(rows), "三条标注线都要留痕（Spec §2.3）")
        ids = [str((row or {}).get("entity_id") or "") for row in rows]
        self.assertEqual(sorted(ids), ids, "留痕按 entity_id 升序、确定性（Spec §2.3）：%r" % (ids,))
        for row in rows:
            self.assertIn(str((row or {}).get("reason") or ""), REASONS,
                          "reason 必须落在闭集里（Spec §2.1）：%r" % (row,))

    def test_a6_real_inner_geometry_is_not_deleted(self):
        ir = dimension_ir(with_real_geometry=True)
        part = self.parts_of(self.extract(ir))[0]
        drawn = self.segments_of(part)
        self.assertIn(((50.0, 50.0), (150.0, 50.0)), drawn,
                      "真件内几何一条都不许少（Spec §2.2 反向判据）")

    def test_a7_equal_length_alone_is_not_evidence(self):
        ir = dimension_ir(with_equal_length_decoy=True)
        doc = self.extract(ir)
        part = self.parts_of(doc)[0]
        drawn = self.segments_of(part)
        self.assertIn(((0.0, 150.0), (200.0, 150.0)), drawn,
                      "长度等于某个 declared_value 的**真件内线**不许被当成标注删掉（Spec §2.1 第 4 条）")
        self.assertEqual(3, int((doc.get("stats") or {}).get(STATS_KEY) or 0),
                         "多一条等长真几何，剔除数仍然只能是 3（Spec §2.1）")


# --------------------------------------------------------------------------- #
# B 组：判据函数的直接单测 + 前端守卫
# --------------------------------------------------------------------------- #
class BPredicateAndFrontend(Base):
    def predicate(self, ir):
        fn = getattr(self.module, PREDICATE, None)
        self.assertTrue(callable(fn),
                        "packaging_parts 必须提供纯函数 %s(ir, entities)（Spec §2.1）" % PREDICATE)
        return fn

    def test_b1_reason_closed_set_is_declared(self):
        self.assertTrue(hasattr(self.module, REASONS_NAME),
                        "reason 闭集必须以常量声明（Spec §2.1）：%s" % REASONS_NAME)
        self.assertEqual(set(REASONS), set(getattr(self.module, REASONS_NAME)),
                         "reason 闭集就是那四个码（Spec §2.1）")
        self.assertIn("DEFPOINTS", tuple(getattr(self.module, LAYERS_NAME, ())),
                      "尺寸定义点图层必须在闭集里（Spec §2.1 第 3 条）")
        self.assertTrue(float(getattr(self.module, ARROW_NAME, 0)) >= 1.0,
                        "箭头长度阈值要声明出来（Spec §2.1）")

    def test_b2_predicate_catches_exactly_the_annotation(self):
        ir = dimension_ir()
        got = self.predicate(ir)(ir, ir["entities"])
        self.assertEqual({"ent:model:D1", "ent:model:D2", "ent:model:D3"}, set(got),
                         "三条标注线必须全被抓到、且一个不多（Spec §2.1）：%r" % (got,))
        for reason in got.values():
            self.assertIn(str(reason), REASONS, "reason 落在闭集（Spec §2.1）：%r" % (reason,))

    def test_b3_predicate_is_deterministic(self):
        ir = dimension_ir()
        fn = self.predicate(ir)
        first = fn(ir, ir["entities"])
        second = fn(ir, ir["entities"])
        self.assertEqual(first, second, "同一份 IR 两次跑逐字相同（Spec §2.3）")
        shuffled = list(reversed(ir["entities"]))
        self.assertEqual(first, fn(ir, shuffled), "输入顺序不影响判据（Spec §2.3）")

    def test_b4_pure_geometry_has_no_annotations(self):
        ir = dimension_ir(with_real_geometry=True)
        ir["dimensions"] = []
        ir["geometry"]["components"][0]["entity_ids"] = ["ent:model:P1", "ent:model:I1"]
        ir["entities"] = [row for row in ir["entities"]
                          if row["entity_id"] in ("ent:model:P1", "ent:model:I1")]
        got = self.predicate(ir)(ir, ir["entities"])
        self.assertEqual({}, got, "没有标注的图一个实体都不许被判成标注（Spec §2.1）")
        doc = self.extract(ir)
        self.assertEqual(0, int((doc.get("stats") or {}).get(STATS_KEY) or 0),
                         "没有标注时剔除数必须是 0（Spec §2.3）")

    def test_b5_frontend_still_draws_backend_geometry(self):
        src = read_text(APP_JS)
        self.assertIn("outline_points", src, "零件形状仍画后端给的轮廓环点（Spec §2.4）")
        self.assertIn("segments", src, "零件形状仍画后端给的折线段（Spec §2.4）")

    def test_b6_frontend_discloses_the_removal(self):
        src = read_text(APP_JS)
        self.assertIn(FRONT_DISCLOSURE, src,
                      "剔除了标注线必须说得出（Spec §2.3）")
        self.assertIn(FRONT_COPY, src,
                      "披露文案逐字：%s（Spec §2.3）" % FRONT_COPY)


if __name__ == "__main__":
    unittest.main()
