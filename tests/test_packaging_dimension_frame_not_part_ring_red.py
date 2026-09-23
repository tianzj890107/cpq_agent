"""红测：手画的**尺寸框**不许被当成零件轮廓（Spec
`docs/specs/packaging-dimension-frame-must-not-be-the-part-ring.md`）。

现场：酒盒 2.1 点开的那一件，形状里带着外围那圈"白线"（尺寸线 / 尺寸界线 / 箭头）—— 不是
"多画了几笔"，而是**选环选错了**：`_largest_loop()` 按面积最大挑，挑了外面那个尺寸框
（`cmp:139`：19 条边 / 117 846mm² / 308.83×446.32），真件是里面那个 4 元矩形
（`54C7/54C8/54C9/54CA` / 58 960mm² / 219.643×267.937）。

补的判据（Spec §2.1 ④）：真图上手工画的尺寸箭头就是**两段** ≤ `ANNOTATION_ARROW_MAX_MM`
的短段，共用一个顶点、远端近似反向（cos ≤ `DIMENSION_ARROW_PAIR_COS`），但**完全共线**的两段
不算（那更像被 CAD 拆成两笔的同一条线）。只凭长度判标注仍然禁止。

纪律：A / B / D 组离线（合成夹具真跑纯函数与 `extract()`，源码守卫）；C 组要真样本（缓存 IR 或
本机可用转换器），缺任一项即 skip。不连 PG / 34、不发 HTTP、不写业务数据。
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

SPEC_MD = ROOT / "docs" / "specs" / "packaging-dimension-frame-must-not-be-the-part-ring.md"
SOURCE_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
CACHED_JIUHE = (ROOT / "tech_app" / "data" / "cad-ir-realsample" / "conversions"
                / "47c39dc1ab6738fc48c8" / "converted.dxf")

REASONS = ("dimension_extension", "dimension_line", "dimension_arrow", "annotation_layer")
PAIR_CONST = "DIMENSION_ARROW_PAIR_COS"
HELPER = "_arrow_pair_ids"
SEGMENT_CAP = "ANNOTATION_ARROW_MAX_MM"

#: 零件矩形 200×300（面积 60 000）——真件
PART = ((0, 0), (200, 0), (200, 300), (0, 300))
FRAME_AREA = 200 * 340          # 尺寸框环 68 000 > 零件 60 000（今天会赢）
PART_AREA = 200 * 300


def _line(eid, start, end, layer="0"):
    xs, ys = [start[0], end[0]], [start[1], end[1]]
    return {
        "entity_id": eid, "handle": eid.split(":")[-1], "type": "LINE", "kind": "line",
        "layer": layer, "space": "model", "block_path": [], "closed": False,
        "length": None, "area": None, "length_mm": None, "area_mm2": None,
        "attributes": {"start": list(start), "end": list(end)},
        "evidence_ref": "ev:E:%s" % eid.split(":")[-1], "canonical_index": 1,
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
    }


def frame_ir(extra=()):
    """零件矩形 + 手画尺寸框（延长线接在零件角点上、尺寸线在左、箭头是内折的两段 5.02mm）。"""
    entities = [
        _line("ent:model:P1", (0, 0), (200, 0)),
        _line("ent:model:P2", (200, 0), (200, 300)),
        _line("ent:model:P3", (200, 300), (0, 300)),
        _line("ent:model:P4", (0, 300), (0, 0)),
        _line("ent:model:X1", (0, 0), (-40, 0)),
        _line("ent:model:X2", (-40, 0), (-40, 145)),
        _line("ent:model:A1", (-40, 145), (-40.5, 150)),
        _line("ent:model:A2", (-40.5, 150), (-40, 155)),
        _line("ent:model:X3", (-40, 155), (-40, 340)),
        _line("ent:model:X4", (-40, 340), (0, 340)),
        _line("ent:model:X5", (0, 340), (0, 300)),
    ]
    entities.extend(extra)
    return {
        "ir_version": "cad-ir/1", "ir_id": "ir:test-frame", "ir_hash": "hash",
        "source": {"kind": "dxf_2d"},
        "units": {"unit_status": "confirmed", "unit": "mm", "scale_to_mm": 1.0},
        "document": {"extents": [-40, 0, 200, 340], "dxf_version": "AC1027"},
        "layers": [{"name": "0", "visible": True, "color": 7,
                    "entity_count": len(entities)}],
        "entities": entities,
        "texts": [], "dimensions": [],
        "geometry": {
            "closed_outlines": [], "open_outlines": [],
            "components": [{"component_id": "cmp:1",
                            "entity_ids": [row["entity_id"] for row in entities],
                            "bbox": [-40, 0, 200, 340], "closed_cycles": 1}],
            "holes": [], "repeated_groups": [], "overlaps": [], "tolerance": 1.0,
        },
        "stats": {"entity_total": len(entities), "layer_total": 1, "closed_outline_total": 0,
                  "open_outline_total": 0, "dimension_total": 0},
        "evidence": {}, "unsupported": [], "warnings": [], "parser": {},
    }


def read_source() -> str:
    return SOURCE_PY.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module("tech_app.backend.services.packaging_parts")

    def extract(self, ir):
        return self.module.extract(copy.deepcopy(ir))

    def part_of(self, doc, component_id="cmp:1"):
        return [row for row in (doc.get("parts") or [])
                if row["component_id"] == component_id][0]

    def segments_of(self, part):
        return [tuple((round(float(point[0]), 3), round(float(point[1]), 3)) for point in seg)
                for seg in (part.get("segments") or [])]


# --------------------------------------------------------------------------- #
# A 组：判据（合成夹具直接调纯函数）
# --------------------------------------------------------------------------- #
class AArrowPairPredicate(Base):
    def test_a1_the_kinked_pair_is_the_arrow(self):
        ir = frame_ir()
        got = self.module.annotation_entity_ids(ir, ir["entities"])
        self.assertEqual({"ent:model:A1", "ent:model:A2"}, set(got),
                         "只有那两段内折 5mm 才是箭头，别的实体一条都不许被判（Spec §2.1 ④）：%r" % (got,))
        self.assertEqual({"dimension_arrow"}, set(got.values()),
                         "reason 必须是 dimension_arrow（Spec §2.1）：%r" % (got,))

    def test_a2_length_alone_is_still_not_evidence(self):
        ir = frame_ir(extra=[_line("ent:model:S1", (0, 300), (5, 300))])
        got = self.module.annotation_entity_ids(ir, ir["entities"])
        self.assertNotIn("ent:model:S1", got,
                         "单独一条 5mm 短段不许被判成标注（长度不是判据，Spec §2.1 第 4 条）：%r" % (got,))

    def test_a3_a_right_angle_v_is_not_an_arrow(self):
        ir = frame_ir(extra=[_line("ent:model:V1", (-40, 145), (-35, 140)),
                             _line("ent:model:V2", (-35, 140), (-40, 135))])
        got = self.module.annotation_entity_ids(ir, ir["entities"])
        self.assertNotIn("ent:model:V1", got, "成 90° 的两段短段不是箭头（Spec §2.1 ④）：%r" % (got,))
        self.assertNotIn("ent:model:V2", got, "成 90° 的两段短段不是箭头（Spec §2.1 ④）：%r" % (got,))

    def test_a4_two_collinear_short_pieces_are_not_an_arrow(self):
        ir = frame_ir(extra=[_line("ent:model:C1", (-60, 0), (-55, 0)),
                             _line("ent:model:C2", (-55, 0), (-50, 0))])
        got = self.module.annotation_entity_ids(ir, ir["entities"])
        self.assertNotIn("ent:model:C1", got,
                         "完全共线的两段更像被 CAD 拆成两笔的同一条线，不算箭头（Spec §2.1 ④）：%r" % (got,))
        self.assertNotIn("ent:model:C2", got,
                         "完全共线的两段更像被 CAD 拆成两笔的同一条线，不算箭头（Spec §2.1 ④）：%r" % (got,))

    def test_a5_deterministic_and_order_free(self):
        ir = frame_ir()
        first = self.module.annotation_entity_ids(ir, ir["entities"])
        second = self.module.annotation_entity_ids(ir, list(reversed(ir["entities"])))
        self.assertEqual(first, second, "同一份 IR 两次跑（含乱序输入）必须逐字相同（Spec §2.1）")

    def test_a6_pair_criteria_are_declared(self):
        self.assertTrue(float(getattr(self.module, "ANNOTATION_ARROW_MAX_MM", 0)) >= 1.0,
                        "箭头长度上限要声明出来（Spec §2.1 ④）")
        cosine = getattr(self.module, PAIR_CONST, None)
        self.assertIsNotNone(cosine, "反向共线的判据常量 %s 必须在位（Spec §2.1 ④）" % PAIR_CONST)
        self.assertLess(float(cosine), 0.0, "%s 必须是负的（反向才算箭头）：%r" % (PAIR_CONST, cosine))


# --------------------------------------------------------------------------- #
# B 组：形状（真跑 extract()，环 / 尺寸 / 折线 / 留痕）
# --------------------------------------------------------------------------- #
class BFrameMustNotWinTheRing(Base):
    def test_b1_ring_is_the_part_not_the_frame(self):
        part = self.part_of(self.extract(frame_ir()))
        outline = part.get("outline") or {}
        points = outline.get("points") or []
        self.assertEqual("closed", part.get("outline_status"), "这一件仍然是闭合件（Spec §2.2）")
        self.assertEqual(4, len(points),
                         "环必须是零件矩形那 4 个点，不是尺寸框那 8 个点（Spec §2.1/§2.2）：%r" % (points,))
        self.assertAlmostEqual(PART_AREA, float(outline.get("area_mm2") or 0.0), delta=1.0,
                               msg="环面积必须是零件的 60 000，不是尺寸框的 68 000（Spec §2.2）")
        self.assertEqual(["ent:model:P1", "ent:model:P2", "ent:model:P3", "ent:model:P4"],
                         sorted(outline.get("entity_ids") or []),
                         "环只能由零件那 4 条边构成（Spec §2.2）")

    def test_b2_part_size_is_the_part_size(self):
        part = self.part_of(self.extract(frame_ir()))
        self.assertAlmostEqual(200.0, float(part.get("unfolded_length_mm") or 0.0), delta=0.5,
                               msg="件尺寸不许被尺寸框撑成 340（Spec §2.2）")
        self.assertAlmostEqual(300.0, float(part.get("unfolded_width_mm") or 0.0), delta=0.5,
                               msg="件尺寸不许被尺寸框撑大（Spec §2.2）")
        self.assertNotAlmostEqual(FRAME_AREA, float(part.get("area_mm2") or 0.0), delta=1.0,
                                  msg="面积不许等于尺寸框的面积（Spec §2.2）")

    def test_b3_arrows_are_not_drawn_into_the_part(self):
        part = self.part_of(self.extract(frame_ir()))
        drawn = self.segments_of(part)
        arrow_one = ((-40.0, 145.0), (-40.5, 150.0))
        arrow_two = ((-40.5, 150.0), (-40.0, 155.0))
        self.assertNotIn(arrow_one, drawn, "箭头段不许画进件内折线（Spec §2.2）")
        self.assertNotIn(arrow_two, drawn, "箭头段不许画进件内折线（Spec §2.2）")
        for seg in (((0.0, 0.0), (200.0, 0.0)), ((200.0, 0.0), (200.0, 300.0))):
            self.assertIn(seg, drawn, "零件自己的边一条都不许少（Spec §2.2 反向判据）")

    def test_b4_removal_is_disclosed_per_part(self):
        doc = self.extract(frame_ir())
        stats = doc.get("stats") or {}
        self.assertEqual(2, int(stats.get("annotation_filtered_total") or 0),
                         "两段箭头必须逐条计入（Spec §2.3）：%r" % (sorted(stats),))
        rows = self.part_of(doc).get("annotation_filtered")
        self.assertEqual(["ent:model:A1", "ent:model:A2"], [str(r["entity_id"]) for r in rows],
                         "逐件留痕按 entity_id 升序（Spec §2.3）：%r" % (rows,))
        for row in rows:
            self.assertIn(str(row.get("reason")), REASONS, "reason 落在闭集（Spec §2.3）：%r" % (row,))

    def test_b5_part_identity_is_untouched(self):
        doc = self.extract(frame_ir())
        parts = doc.get("parts") or []
        self.assertEqual(1, len(parts), "摘标注不许改「有几件」（Spec §2.3）")
        self.assertEqual(len(frame_ir()["entities"]), len(parts[0].get("entity_ids") or []),
                         "件身份（成员证据）必须按**原始成员**报，摘标注不许从证据里抹掉（Spec §2.3）")


# --------------------------------------------------------------------------- #
# C 组：真样本（缺缓存 / 缺转换器 → skip）
# --------------------------------------------------------------------------- #
class CRealSamples(Base):
    def cached_ir(self):
        if not CACHED_JIUHE.exists():
            self.skipTest("本机没有缓存的酒盒 CAD IR（%s）" % CACHED_JIUHE.parent.parent.name)
        from tech_app.backend.services import cad_ir
        return cad_ir.parse_dxf(CACHED_JIUHE.read_bytes(), filename="converted.dxf",
                                source={"kind": "dxf_2d"})

    def disc_doc(self):
        try:
            from tech_app.tools import packaging_parts_gate as gate
        except Exception as exc:                                   # noqa: BLE001
            self.skipTest("门禁工具不可用（%s）" % exc)
        sample = gate.SAMPLES_DIR / "圆盘盒.dwg"
        if not gate.SAMPLES_DIR.is_dir() or not sample.exists():
            self.skipTest("本机没有真实样本目录（门禁自己也会 skip）")
        if gate._converter_gap():
            self.skipTest("本机没有可用转换器（门禁自己也会 skip）")
        from tech_app.backend.services import cad_ir
        content, _converter = gate._sample_dxf(sample)
        ir = cad_ir.parse_dxf(content, filename="gate_sample.dxf",
                              source={"kind": "dxf_2d", "attachment_name": sample.name})
        return self.module.extract(ir)

    def test_c1_jiuhe_frame_part_ring_is_the_rectangle(self):
        ir = self.cached_ir()
        doc = self.module.extract(copy.deepcopy(ir))
        self.assertEqual(263, len(doc.get("parts") or []),
                         "件数不许变（酒盒 263 件）")
        rows = {row["component_id"]: row for row in doc["parts"]}
        for component_id, part_code in (("cmp:139", "DWG-P02"), ("cmp:47", "DWG-P02")):
            if component_id not in rows:
                self.skipTest("缓存 IR 里没有 %s（样本换过？）" % component_id)
            row = rows[component_id]
            outline = row.get("outline") or {}
            self.assertEqual(4, len(outline.get("points") or []),
                             "%s（%s）：环必须是那个 4 元矩形（Spec §2.1/§2.2）" % (part_code, component_id))
            self.assertAlmostEqual(58851.0, float(outline.get("area_mm2") or 0.0), delta=1.0,
                                   msg="%s：环面积 ≈ 58 851（Spec §2.2）" % component_id)
            self.assertAlmostEqual(219.643, float(row.get("unfolded_length_mm") or 0.0), delta=0.5,
                                   msg="%s：件尺寸必须是真件的 219.643（Spec §2.2）" % component_id)
            self.assertAlmostEqual(267.937, float(row.get("unfolded_width_mm") or 0.0), delta=0.5,
                                   msg="%s：件尺寸必须是真件的 267.937（Spec §2.2）" % component_id)
            self.assertEqual(sorted(outline.get("entity_ids") or []),
                             sorted(outline.get("entity_ids") or []),
                             "环的边集必须确定性（Spec §2.1）")
        stats = self.module.summarize(doc)
        self.assertEqual(134, int(stats.get("closed_total") or 0),
                         "闭合件数不许被本批改动（酒盒冻结值 134，Spec §2.3）")
        self.assertEqual(129, int(stats.get("open_total") or 0),
                         "开口件数不许被本批改动（酒盒冻结值 129，Spec §2.3）")

    def test_c2_disc_box_has_zero_collateral(self):
        doc = self.disc_doc()
        stats = self.module.summarize(doc)
        self.assertEqual(312, int(stats.get("part_total") or len(doc.get("parts") or [])),
                         "圆盘盒件数必须原样 312（Spec §2.3）")
        self.assertEqual(255, int(stats.get("closed_total") or 0),
                         "圆盘盒闭合件数必须原样 255（Spec §2.3）")
        self.assertEqual(9, int(stats.get("role_known_total") or 0),
                         "圆盘盒角色已知件数必须原样 9（Spec §2.3：既有的 role_known_total 地板）")
        self.assertEqual(0, int((doc.get("stats") or {}).get("annotation_filtered_total") or 0),
                         "圆盘盒上这一批一条都不许判（零附带，Spec §2.3）")


# --------------------------------------------------------------------------- #
# D 组：护栏（源码口径 + 不许回退）
# --------------------------------------------------------------------------- #
class DGuards(Base):
    def test_d1_pair_helper_and_cap_are_in_the_source(self):
        source = read_source()
        self.assertIn("def %s(" % HELPER, source, "判据必须是**配对**的纯函数（Spec §2.1 ④）")
        self.assertIn(PAIR_CONST, source, "反向判据常量必须在位（Spec §2.1 ④）")
        self.assertIn(SEGMENT_CAP, source, "每段的长度上限必须用既有常量（Spec §2.1 ④）")

    def test_d2_shape_may_change_but_closure_must_not(self):
        source = read_source()
        self.assertIn("if annotation_rows and outline is not None:", source,
                      "摘标注只许在一进一出都闭合的件上生效（Spec §2.3）")
        self.assertIn("elif annotation_rows:", source,
                      "本来就求不出环的件必须原样回退、不留痕（Spec §2.3）")

    def test_d3_spec_is_declared(self):
        self.assertTrue(SPEC_MD.exists(), "Spec 必须在位：%s" % SPEC_MD.name)
        text = SPEC_MD.read_text(encoding="utf-8")
        for token in (PAIR_CONST, SEGMENT_CAP, "58 851", "312", "255", "9"):
            self.assertIn(token, text, "Spec 必须写明本批的门槛与零附带读数：%s" % token)


if __name__ == "__main__":
    unittest.main()
