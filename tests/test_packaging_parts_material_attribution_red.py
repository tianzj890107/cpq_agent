"""红测：图纸零件的材料与厚度归属（覆盖率）。

Spec：`docs/specs/packaging-parts-material-attribution.md`
前置：假设第 1～5 层（真实轮廓 / 面板 / 工艺成本门槛 / 3D / 指标门禁）已实现。

现状缺口（34 上真跑，不是推断；项目 f1417060ae9d 酒盒.dwg）：
  · 64 件里 `material` 只有 12 件、`thickness_mm` 只有 8 件 → `processable_ratio=0.062`；
  · 两档取法（件级最近标注 + 图级"全图唯一值"兜底）在真图上必然取不到：一张图 6 种材料文本；
  · 件级半径 `0.25 × 对角线` 在整版大件上放大到 166mm，把图级材料说明误归给 4 件 open 大件；
  · 需求 3.3（grey_board_thickness / face_paper_gsm …）完全没参与归属。

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
STEPS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "steps.py"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"

RULE_ID = "part_material_attribution_v1"
KINDS = ("part_note", "group_note", "layer_name", "requirement_default")
NOTE_DISTANCE_MAX_MM = 150.0
GROUP_NOTE_RADIUS_MM = 300.0
AMBIGUOUS_DISTANCE_MM = 5.0
PARTITION_KEYWORDS = ("左盒", "右盒", "内盒", "外盒", "盒盖", "底盒", "底板", "围条", "内托",
                      "面纸", "衬纸")
REQUIREMENT_MATERIAL_FIELDS = ("grey_board", "grey_board_thickness", "face_paper",
                               "face_paper_gsm", "lining_paper")
NEW_METRICS = ("material_known_ratio", "thickness_known_ratio", "material_default_ratio",
               "attribution_kind_mix")

# 真样本验收用的需求口径（1.1 需求单 3.3 的典型填法）。
REQUIREMENT = {"grey_board": "灰板", "grey_board_thickness": 2.5,
               "face_paper": "粉灰", "face_paper_gsm": 350}


# --------------------------------------------------------------------------- #
# 合成 IR / 零件文档
# --------------------------------------------------------------------------- #
def text_entry(eid, text, xy):
    return {"entity_id": eid, "type": "MTEXT", "layer": "TEXT",
            "normalized_text": text, "raw_text": text, "position": [float(xy[0]), float(xy[1])],
            "height": 2.5, "evidence_ref": "ev:E:" + eid.split(":")[-1]}


def rect(tag, x0, y0, w, h, layer="DESIGN"):
    """一个由 4 条 LINE 组成的闭合矩形分量（可被环搜索拼成闭环）。"""
    corners = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    eids, entities = [], []
    for index in range(4):
        start, end = corners[index], corners[(index + 1) % 4]
        eid = "ent:model:%s-%d" % (tag, index)
        eids.append(eid)
        entities.append({"entity_id": eid, "type": "LINE", "layer": layer,
                         "attributes": {"start": [float(start[0]), float(start[1])],
                                        "end": [float(end[0]), float(end[1])]}})
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids,
                 "bbox": [float(x0), float(y0), float(x0 + w), float(y0 + h)]}
    return component, entities


def open_rect(tag, x0, y0, w, h, layer="DESIGN"):
    """一个缺一条边的分量（端点度数为奇数 → 真开线，必须判 open）。"""
    corners = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    eids, entities = [], []
    for index in range(3):
        start, end = corners[index], corners[(index + 1) % 4]
        eid = "ent:model:%s-%d" % (tag, index)
        eids.append(eid)
        entities.append({"entity_id": eid, "type": "LINE", "layer": layer,
                         "attributes": {"start": [float(start[0]), float(start[1])],
                                        "end": [float(end[0]), float(end[1])]}})
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids,
                 "bbox": [float(x0), float(y0), float(x0 + w), float(y0 + h)]}
    return component, entities


def make_ir(pieces, texts=()):
    components, entities = [], []
    for component, rows in pieces:
        components.append(component)
        entities.extend(rows)
    return {"ir_version": "cad-ir/1", "ir_id": "ir:test", "ir_hash": "hash",
            "parser": {"name": "test"}, "source": {"kind": "dxf_2d", "attachment_name": "t.dxf"},
            "units": {"drawing_units": "mm", "scale_to_mm": 1.0, "unit_status": "confirmed",
                      "unit_confidence": 1.0, "candidates": []},
            "layers": [], "entities": entities, "texts": list(texts),
            "dimensions": [], "geometry": {"components": components},
            "evidence": {}, "unsupported": [], "warnings": [], "stats": {}}


def row_of(doc, component_id):
    for row in doc.get("parts") or []:
        if row.get("component_id") == component_id:
            return row
    raise AssertionError("零件文档里没有分量 %s（parts=%d）"
                         % (component_id, len(doc.get("parts") or [])))


class AttributionCase(unittest.TestCase):
    def module(self):
        for name in ("extract", "attribute_materials", "summarize"):
            self.assertTrue(callable(getattr(packaging_parts, name, None)),
                            "packaging_parts 缺 %s()（Spec §5.1）" % name)
        return packaging_parts

    def extract(self, pieces, texts=(), requirement=None):
        module = self.module()
        options = {"requirement": requirement} if requirement is not None else None
        return module.extract(make_ir(pieces, texts), options=options)


# --------------------------------------------------------------------------- #
# A 组：常量与闭集
# --------------------------------------------------------------------------- #
class AConstants(AttributionCase):
    def test_a1_rule_id_and_kinds_are_frozen(self):
        self.assertEqual(packaging_parts.MATERIAL_ATTRIBUTION_RULE_ID, RULE_ID,
                         "规则号必须逐字等于 Spec §2")
        self.assertEqual(tuple(packaging_parts.ATTRIBUTION_KINDS), KINDS,
                         "来源闭集必须逐字等于 Spec §2（顺序即优先级）")

    def test_a2_thresholds_are_frozen(self):
        self.assertEqual(float(packaging_parts.NOTE_DISTANCE_MAX_MM), NOTE_DISTANCE_MAX_MM,
                         "件级半径硬上限必须等于 Spec §2")
        self.assertEqual(float(packaging_parts.GROUP_NOTE_RADIUS_MM), GROUP_NOTE_RADIUS_MM,
                         "成组注记覆盖半径必须等于 Spec §2")
        self.assertEqual(float(packaging_parts.AMBIGUOUS_DISTANCE_MM), AMBIGUOUS_DISTANCE_MM,
                         "歧义距离阈值必须等于 Spec §2.1 第 3 条")

    def test_a3_partition_keywords_and_requirement_fields(self):
        for word in PARTITION_KEYWORDS:
            self.assertIn(word, tuple(packaging_parts.PARTITION_KEYWORDS),
                          "分区关键词 %s 必须在闭集里（Spec §2）" % word)
        self.assertEqual(tuple(packaging_parts.REQUIREMENT_MATERIAL_FIELDS),
                         REQUIREMENT_MATERIAL_FIELDS,
                         "需求取料字段必须逐字等于 Spec §2（键名取自 industry_templates 3.3）")

    def test_a4_attribute_materials_is_callable(self):
        self.assertTrue(callable(getattr(packaging_parts, "attribute_materials", None)),
                        "必须提供纯函数 attribute_materials()（Spec §5.1）")


# --------------------------------------------------------------------------- #
# B 组：四层归属
# --------------------------------------------------------------------------- #
class BLayers(AttributionCase):
    def test_b1_part_note_inside_the_part(self):
        piece = rect("A", 0, 0, 200, 100)
        doc = self.extract([piece], [text_entry("ent:model:T1", "2.5mm灰板", (100, 50))])
        row = row_of(doc, "cmp:A")
        self.assertEqual(row.get("material"), "灰板", "件内标注要能定材料（Spec §3）")
        self.assertEqual(row.get("thickness_mm"), 2.5)
        self.assertEqual((row.get("material_source") or {}).get("kind"), "part_note")
        self.assertEqual((row.get("thickness_source") or {}).get("kind"), "part_note")
        self.assertEqual((row.get("material_source") or {}).get("evidence_ref"), "ev:E:T1",
                         "件级归属必须带出处（Spec §2.1 第 1 条）")

    def test_b2_part_note_radius_has_an_absolute_cap(self):
        # 500×500 → 0.25×对角线 ≈ 177mm > 硬上限 150mm；标注放在 160mm 处必须**不**归属。
        piece = rect("B", 0, 0, 500, 500)
        doc = self.extract([piece], [text_entry("ent:model:T2", "2.5mm灰板", (250, 660))])
        row = row_of(doc, "cmp:B")
        self.assertIsNone(row.get("thickness_mm"),
                          "超过 NOTE_DISTANCE_MAX_MM(150) 的标注不许归属（Spec §2 层 1）")
        self.assertIsNone(row.get("material"))

    def test_b3_note_near_two_parts_is_not_a_part_note(self):
        # 两个 200×100 的件相距 100mm，标注落在中间：同时贴两件 → 不是件级标注。
        pieces = [rect("C1", 0, 0, 200, 100), rect("C2", 300, 0, 200, 100)]
        doc = self.extract(pieces, [text_entry("ent:model:T3", "2.5mm灰板", (250, 50))])
        for cid in ("cmp:C1", "cmp:C2"):
            row = row_of(doc, cid)
            self.assertNotEqual((row.get("material_source") or {}).get("kind"), "part_note",
                                "同一条注记同时贴多件时不许当件级标注（Spec §2 层 1）")

    def test_b4_group_note_covers_several_parts_with_one_evidence(self):
        pieces = [rect("D1", 0, 0, 200, 100), rect("D2", 300, 0, 200, 100),
                  rect("D3", 600, 0, 200, 100)]
        note = text_entry("ent:model:T4", "名称：内盒2灰板 材料：2mm灰板", (300, -150))
        doc = self.extract(pieces, [note])
        rows = [row_of(doc, cid) for cid in ("cmp:D1", "cmp:D2", "cmp:D3")]
        for row in rows:
            self.assertEqual((row.get("material_source") or {}).get("kind"), "group_note",
                             "成组注记要覆盖多件（Spec §2 层 2）")
            self.assertEqual(row.get("material"), "灰板",
                             "KV 文本必须切成材料标签，不许塞整句（Spec §3）")
            self.assertEqual(row.get("thickness_mm"), 2.0)
        covers = [sorted((row.get("attribution") or {}).get("covers") or []) for row in rows]
        self.assertEqual(len({tuple(item) for item in covers}), 1, "三件的 covers 必须一致")
        self.assertEqual(len(covers[0]), 3, "covers 必须逐件列全（Spec §2 层 2）")
        self.assertEqual((rows[0].get("attribution") or {}).get("partition"), "内盒2",
                         "分区词要摘进 attribution.partition（Spec §3）")

    def test_b5_layer_name_gives_material_only(self):
        piece = rect("E", 0, 0, 200, 100, layer="灰板层")
        doc = self.extract([piece])
        row = row_of(doc, "cmp:E")
        self.assertEqual((row.get("material_source") or {}).get("kind"), "layer_name")
        self.assertEqual(row.get("material"), "灰板层")
        self.assertIsNone(row.get("thickness_mm"), "图层名只给材质，不许给厚度（Spec §2 层 3）")

    def test_b6_requirement_default_is_visible_and_confirmed(self):
        piece = rect("F", 0, 0, 200, 100)
        doc = self.extract([piece], requirement=REQUIREMENT)
        row = row_of(doc, "cmp:F")
        self.assertEqual((row.get("material_source") or {}).get("kind"), "requirement_default")
        self.assertEqual(row.get("thickness_mm"), 2.5, "兜底厚度取需求灰板厚度")
        self.assertIn("粉灰", str(row.get("material") or ""), "兜底材质取需求面纸口径")
        self.assertTrue(row.get("needs_confirmation"), "兜底必须逐件标 needs_confirmation")
        refs = row.get("assumption_refs") or []
        self.assertTrue(refs, "兜底必须写 assumption_refs（Spec §2.1 第 5 条）")
        self.assertTrue(any("requirement" in str(item) for item in refs),
                        "assumption_refs 必须指向需求字段")

    def test_b7_part_note_wins_over_requirement_default(self):
        piece = rect("G", 0, 0, 200, 100)
        doc = self.extract([piece], [text_entry("ent:model:T5", "1.8mm 灰板裱光银纸", (100, 50))],
                           requirement=REQUIREMENT)
        row = row_of(doc, "cmp:G")
        self.assertEqual(row.get("thickness_mm"), 1.8, "层 1 不许被层 4 覆盖（Spec §2.1 第 4 条）")
        self.assertEqual((row.get("thickness_source") or {}).get("kind"), "part_note")
        self.assertFalse(row.get("needs_confirmation"))

    def test_b8_open_part_never_gets_material(self):
        piece = open_rect("H", 0, 0, 200, 100)
        doc = self.extract([piece], [text_entry("ent:model:T6", "2.5mm灰板", (100, 50))],
                           requirement=REQUIREMENT)
        row = row_of(doc, "cmp:H")
        self.assertNotEqual(row.get("outline_status"), "closed",
                            "缺一条边的分量必须判 open（本组前提）")
        self.assertIsNone(row.get("material"), "open 件不许取得材料（Spec §2.1 第 2 条）")
        self.assertIsNone(row.get("thickness_mm"), "open 件不许取得厚度（Spec §2.1 第 2 条）")

    def test_b9_conflicting_close_notes_abstain(self):
        piece = rect("I", 0, 0, 200, 100)
        notes = [text_entry("ent:model:T7", "2.0mm灰板", (100, -40)),
                 text_entry("ent:model:T8", "1.8mm白卡", (100, -44))]
        doc = self.extract(pieces=[piece], texts=notes, requirement=REQUIREMENT)
        row = row_of(doc, "cmp:I")
        self.assertIsNone(row.get("material"), "距离相近的冲突候选必须弃权（Spec §2.1 第 3 条）")
        notes_text = " ".join(str(item) for item in ((row.get("attribution") or {}).get("notes") or []))
        self.assertIn("ambiguous", notes_text, "弃权必须留痕 ambiguous:<候选数>")


# --------------------------------------------------------------------------- #
# C 组：指标
# --------------------------------------------------------------------------- #
class CMetrics(AttributionCase):
    def _doc(self, rows):
        return {"parts": rows, "stats": {"part_total": len(rows)}}

    def test_c1_summary_carries_new_metrics(self):
        got = packaging_parts.summarize(self._doc([]))
        for key in NEW_METRICS:
            self.assertIn(key, got, "summarize() 缺指标 %s（Spec §4）" % key)

    def test_c2_zero_total_is_zero_not_error(self):
        got = packaging_parts.summarize(self._doc([]))
        for key in ("material_known_ratio", "thickness_known_ratio", "material_default_ratio"):
            self.assertEqual(float(got[key]), 0.0, "%s 在 part_total=0 时必须是 0.0" % key)
        self.assertEqual(sum(int(value) for value in got["attribution_kind_mix"].values()), 0)

    def test_c3_ratios_match_their_definitions(self):
        rows = [{"part_code": "DWG-P01", "outline_status": "closed", "role": "cut",
                 "size_source": "closed_outline", "material": {"spec": "灰板"},
                 "thickness_mm": 2.0, "material_source": {"kind": "part_note"},
                 "thickness_source": {"kind": "part_note"}},
                {"part_code": "DWG-P02", "outline_status": "closed", "role": "unknown",
                 "size_source": "closed_outline", "material": {"spec": "灰板"},
                 "thickness_mm": 2.5, "material_source": {"kind": "requirement_default"},
                 "thickness_source": {"kind": "requirement_default"}},
                {"part_code": "DWG-P03", "outline_status": "open", "role": "unknown",
                 "size_source": "component_bbox", "material": None, "thickness_mm": None}]
        got = packaging_parts.summarize(self._doc(rows))
        self.assertAlmostEqual(float(got["material_known_ratio"]), 2 / 3, places=3)
        self.assertAlmostEqual(float(got["thickness_known_ratio"]), 2 / 3, places=3)
        self.assertAlmostEqual(float(got["material_default_ratio"]), 1 / 3, places=3)

    def test_c4_attribution_kind_mix_counts_every_kind(self):
        rows = [{"part_code": "DWG-P01", "material_source": {"kind": "part_note"}},
                {"part_code": "DWG-P02", "material_source": {"kind": "group_note"}},
                {"part_code": "DWG-P03", "material_source": {"kind": "layer_name"}},
                {"part_code": "DWG-P04", "material_source": {"kind": "requirement_default"}},
                {"part_code": "DWG-P05", "material_source": None}]
        mix = packaging_parts.summarize(self._doc(rows))["attribution_kind_mix"]
        for kind in KINDS:
            self.assertEqual(int(mix.get(kind, 0)), 1, "attribution_kind_mix 缺 %s" % kind)
        self.assertEqual(int(mix.get("none", 0)), 1, "取不到要计入 none")


# --------------------------------------------------------------------------- #
# D 组：护栏（既有口径不许被本批放宽）
# --------------------------------------------------------------------------- #
class DGuards(unittest.TestCase):
    def test_d1_reject_codes_unchanged(self):
        self.assertEqual(tuple(packaging_parts.PROCESS_REJECT_CODES),
                         ("PACKAGING_PART_NOT_CLOSED", "PACKAGING_PART_MATERIAL_UNKNOWN",
                          "PACKAGING_PART_NOT_FOUND"),
                         "三道拒绝码不许改（Spec §6）")

    def test_d2_outline_and_size_vocabulary_unchanged(self):
        self.assertEqual(float(packaging_parts.LOOP_TOLERANCE_MM), 1.0)
        self.assertEqual(tuple(packaging_parts.OUTLINE_STATUSES), ("closed", "open", "unavailable"))
        self.assertEqual(tuple(packaging_parts.SIZE_SOURCES),
                         ("closed_outline", "component_bbox", "dwg_outline"))
        self.assertEqual(packaging_parts.PART_CODE_FORMAT, "DWG-P%02d")

    def test_d3_missing_material_still_blocks(self):
        verdict = packaging_parts.processability({"part_code": "DWG-P01", "outline_status": "closed",
                                                  "material": None, "thickness_mm": None})
        self.assertFalse(verdict["ok"], "缺料仍然必须被拒（Spec §6）")
        self.assertEqual(verdict["code"], "PACKAGING_PART_MATERIAL_UNKNOWN")

    def test_d4_extract_is_deterministic(self):
        pieces = [rect("J1", 0, 0, 200, 100), rect("J2", 300, 0, 200, 100)]
        texts = [text_entry("ent:model:T9", "2.5mm灰板", (250, 50))]
        ir = make_ir(pieces, texts)
        first = packaging_parts.extract(ir, options={"requirement": REQUIREMENT})
        second = packaging_parts.extract(ir, options={"requirement": REQUIREMENT})
        self.assertEqual(first.get("parts"), second.get("parts"), "同一份 IR 两次跑必须逐字相同")


# --------------------------------------------------------------------------- #
# E 组：真实样本门槛（Spec §4）
# --------------------------------------------------------------------------- #
class ERealSample(AttributionCase):
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
        doc = packaging_parts.extract(ir, options={"requirement": REQUIREMENT})
        self._cache[filename] = doc
        return doc

    def test_e1_material_coverage(self):
        summary = packaging_parts.summarize(self._doc("酒盒.dwg"))
        self.assertGreaterEqual(float(summary["material_known_ratio"]), 0.75,
                                "酒盒 material_known_ratio 门槛 0.75（Spec §4）")

    def test_e2_thickness_coverage(self):
        summary = packaging_parts.summarize(self._doc("酒盒.dwg"))
        self.assertGreaterEqual(float(summary["thickness_known_ratio"]), 0.75,
                                "酒盒 thickness_known_ratio 门槛 0.75（Spec §4）")

    def test_e3_processable_coverage(self):
        summary = packaging_parts.summarize(self._doc("酒盒.dwg"))
        self.assertGreaterEqual(float(summary["processable_ratio"]), 0.70,
                                "酒盒 processable_ratio 门槛 0.70（今天 0.062，Spec §4）")

    def test_e4_open_parts_have_no_attribution(self):
        doc = self._doc("酒盒.dwg")
        bad = [row["part_code"] for row in doc["parts"]
               if row.get("outline_status") != "closed"
               and (row.get("material") or row.get("thickness_mm"))]
        self.assertEqual(bad, [], "open 件不许带材料/厚度（今天 9 件误归属，Spec §4）")

    def test_e5_requirement_default_rows_are_flagged(self):
        doc = self._doc("酒盒.dwg")
        for row in doc["parts"]:
            if (row.get("material_source") or {}).get("kind") == "requirement_default":
                self.assertTrue(row.get("needs_confirmation"),
                                "%s 走兜底必须标 needs_confirmation" % row.get("part_code"))

    def test_e6_flow_passes_the_requirement_into_extraction(self):
        source = STEPS_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("requirement", source,
                      "零件提取步骤必须把需求 3.3 材料口径传进 extract()（Spec §5.2）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
