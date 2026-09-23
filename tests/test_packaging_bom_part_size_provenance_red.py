"""红测：BOM 回填的零件尺寸必须带来源 —— 展开轮廓 ≠ 包围盒。

Spec：`docs/specs/packaging-bom-part-size-provenance.md`
依赖：`packaging-parts-extraction`（C7 回填）、`packaging-parametric-bom`（BOM 读接口）、
`packaging-parse-to-downstream-seams`（§3.2 配对复核披露）。

现状缺口（34 线上实测，不是推断；细节见 Spec §1）：

  · 项目 `5416443be409`（`酒盒.dwg`，`flow-576b9d05d67be56e`，BOM 33 行）里 4 行走 `source=dwg_parts`；
  · 零件侧 4 件里有 3 件是 `outline_status=open` + `size_source=component_bbox` ——
    写进 `unfolded_length_mm/width_mm` 的数字其实是**未闭合零件的包围盒**，不是展开尺寸；
  · 回填到 BOM 行后，`size_source_json.dwg_binding` 只有
    component_id / part_code / rule_id / fallback_paired / original_missing_variables /
    pairing_basis / material_match —— **来源一个字都没有**；
  · 于是成本按 `cut_length × cut_width` 算出来的材料费、界面上显示的"展开尺寸"，
    都无法区分"真展开"与"包围盒"；同一份 BOM 里磁铁（钕铁硼 Ø10×2mm）那行拿到 440.123 × 482.92。

本批只写 Spec + 红测（AGENTS.md：Codex 不直接编写业务实现）。
夹具**复用**冻结测试模块，不复制它的常量与假库：
`tests.test_packaging_parametric_bom_red.BomCase`（BOM 真链路：种子 KB + 临时 SQLite + meta 沙盘）。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_packaging_parametric_bom_red import (  # noqa: E402
    PID, REQ_NO, BomCase, load_bom)

PARTS_PKG = "tech_app.backend.services.packaging_parts"

#: 零件文档侧既有的来源闭集（Spec §2 不许新增取值）。
SIZE_SOURCES = ("closed_outline", "component_bbox", "dwg_outline")
#: 回填行上必须能读到的三个新键。
BINDING_PROVENANCE_KEYS = ("size_source", "outline_status", "size_quality")
QUALITY_VALUES = {"unfolded", "bbox_only"}
#: 既有七个键，一个都不许少（Spec §3 A4 / D5）。
LEGACY_BINDING_KEYS = ("component_id", "part_code", "rule_id", "fallback_paired",
                       "original_missing_variables", "pairing_basis", "material_match")


def parts_module():
    import importlib
    return importlib.import_module(PARTS_PKG)


def part(part_code, length, width, material, *, size_source, outline_status,
         component_id="cmp:probe"):
    """一件图纸零件：含来源键（这正是本批要求一路带下去的东西）。"""
    return {"part_code": part_code, "part_id": part_code, "component_id": component_id,
            "unfolded_length_mm": length, "unfolded_width_mm": width,
            "area_mm2": float(length) * float(width), "material": material,
            "role": "cut", "size_source": size_source, "outline_status": outline_status}


def parts_doc(parts):
    return {"engine_version": "packaging-parts/1", "parts": list(parts),
            "filtered": [], "unavailable": [], "stats": {}, "source": {},
            "reviewable": True}


def bbox_parts():
    """34 实测形状：4 件里 3 件未闭合（包围盒），1 件闭合（真展开）。

    面积降序，配到 `YT-RB-01001-A` 的 4 个待绑行上；材料一致，避免混入 material_match 噪声。
    """
    return [part("DWG-P01", 211.6, 161.6, "灰板 2.0mm", component_id="cmp:1",
                 size_source="component_bbox", outline_status="open"),
            part("DWG-P02", 211.6, 100.0, "灰板 2.0mm", component_id="cmp:2",
                 size_source="component_bbox", outline_status="open"),
            part("DWG-P03", 161.6, 100.0, "灰板 2.0mm", component_id="cmp:3",
                 size_source="component_bbox", outline_status="open"),
            part("DWG-P04", 200.0, 50.0, "灰板 2.0mm", component_id="cmp:4",
                 size_source="closed_outline", outline_status="closed")]


def unfolded_parts():
    """四件全是闭合轮廓（护栏：不许把展开来源标成 bbox_only）。"""
    return [part("DWG-P01", 211.6, 161.6, "灰板 2.0mm", component_id="cmp:1",
                 size_source="closed_outline", outline_status="closed"),
            part("DWG-P02", 211.6, 100.0, "灰板 2.0mm", component_id="cmp:2",
                 size_source="closed_outline", outline_status="closed"),
            part("DWG-P03", 161.6, 100.0, "灰板 2.0mm", component_id="cmp:3",
                 size_source="closed_outline", outline_status="closed"),
            part("DWG-P04", 200.0, 50.0, "灰板 2.0mm", component_id="cmp:4",
                 size_source="dwg_outline", outline_status="closed")]


def sourceless_parts():
    """来源键缺失（老版本文档）：不许因此被算成 `unfolded`（Spec §3 D3）。"""
    parts = bbox_parts()
    for row in parts:
        row.pop("size_source")
        row.pop("outline_status")
    return parts


class SizeProvenanceCase(BomCase):
    """在真 BOM 链路上拿回填结果；需要时先把零件文档落进 meta 沙盘。"""

    def build(self, parts, requirement_no=REQ_NO):
        self.save_requirement()
        self.confirm_box()
        if parts is not None:
            parts_module().save_parts(PID, parts_doc(parts))
        return self.bom().build_bom(PID, requirement_no)

    def template_items(self):
        """先建一次 BOM，拿到待绑行（尚未回填）。"""
        self.save_requirement()
        self.confirm_box()
        return list((self.bom().build_bom(PID, REQ_NO).get("items") or []))

    def bind(self, parts):
        return parts_module().bind_rows(self.template_items(), parts_doc(parts))

    @staticmethod
    def bound_rows(doc):
        return [row for row in (doc.get("items") or [])
                if row.get("source") == "dwg_parts"]

    @staticmethod
    def binding_of(row):
        raw = row.get("size_source_json")
        if isinstance(raw, str):
            raw = json.loads(raw)
        return ((raw or {}).get("dwg_binding") or {}) if isinstance(raw, dict) else {}


# --------------------------------------------------------------------------- #
# A 组：回填时必须带来源（Spec §3 A1–A4）
# --------------------------------------------------------------------------- #
class ABindingCarriesProvenance(SizeProvenanceCase):
    def test_a1_every_binding_carries_size_source_and_outline_status(self):
        parts = bbox_parts()
        result = self.bind(parts)
        rows = [row for row in result["items"] if row.get("source") == "dwg_parts"]
        self.assertEqual(len(rows), 4, "4 个待绑行照旧全部绑定")
        by_code = {row["part_code"]: row for row in parts}
        for row in rows:
            binding = self.binding_of(row)
            code = binding.get("part_code")
            self.assertIn(code, by_code, "配对结果应是夹具里那 4 件")
            for key in ("size_source", "outline_status"):
                self.assertIn(key, binding,
                              "Spec §3 A1：回填必须带 %s（现在只有 component_id/part_code/"
                              "rule_id/fallback_paired/original_missing_variables/"
                              "pairing_basis/material_match）" % key)
            self.assertEqual(binding["size_source"], by_code[code]["size_source"],
                             "Spec §3 A1：size_source 必须与零件文档那一行逐字相等")
            self.assertEqual(binding["outline_status"], by_code[code]["outline_status"],
                             "Spec §3 A1：outline_status 必须与零件文档那一行逐字相等")

    def test_a2_size_quality_is_the_closed_set_mapping(self):
        result = self.bind(bbox_parts())
        rows = [row for row in result["items"] if row.get("source") == "dwg_parts"]
        seen = set()
        for row in rows:
            binding = self.binding_of(row)
            self.assertIn("size_quality", binding, "Spec §3 A2：必须给出 size_quality")
            quality = binding["size_quality"]
            self.assertIn(quality, QUALITY_VALUES,
                          "Spec §3 A2：size_quality 闭集 %s" % sorted(QUALITY_VALUES))
            seen.add(quality)
            expected = "bbox_only" if binding["size_source"] == "component_bbox" else "unfolded"
            self.assertEqual(quality, expected,
                             "Spec §3 A2：component_bbox → bbox_only，其余 → unfolded")
        self.assertEqual(seen, {"bbox_only", "unfolded"},
                         "3 件包围盒 + 1 件真展开，两种来源都要出现")

    def test_a3_pairing_rule_is_unchanged(self):
        """护栏：配对仍是位置配对（行顺序 ↔ 面积降序），本批不动（Spec §3 A3）。"""
        parts = bbox_parts()
        rows = [row for row in self.bind(parts)["items"] if row.get("source") == "dwg_parts"]
        codes = [self.binding_of(row).get("part_code") for row in rows]
        self.assertEqual(codes, ["DWG-P01", "DWG-P02", "DWG-P03", "DWG-P04"],
                         "面积降序 ↔ 行顺序，逐字不变")

    def test_a4_legacy_binding_keys_and_values_are_unchanged(self):
        """护栏：既有七个键一个不少，取值口径逐字不变（Spec §3 A4 / D5）。"""
        rows = [row for row in self.bind(bbox_parts())["items"]
                if row.get("source") == "dwg_parts"]
        for index, row in enumerate(rows, start=1):
            binding = self.binding_of(row)
            for key in LEGACY_BINDING_KEYS:
                self.assertIn(key, binding, "Spec §3 D5：既有键 %s 不许少" % key)
            self.assertEqual(binding["rule_id"], "dwg_parts_row_pairing_v1")
            self.assertFalse(binding["fallback_paired"], "4 件零件配 4 行，不触发循环取件")
            self.assertIn("位置配对：第 %d 个待绑行" % index, binding["pairing_basis"],
                          "配对依据逐字不变")
        result = self.bind(bbox_parts())
        self.assertEqual(list(result["pairing_review"]), [],
                         "同类材料不产生不一致项（披露口径照旧）")
        for row in rows:
            self.assertIsNot(self.binding_of(row)["material_match"], False,
                             "同类材料不许被判成不一致")


# --------------------------------------------------------------------------- #
# B 组：BOM 文档与读回路径都要带（Spec §3 B1–B3）
# --------------------------------------------------------------------------- #
class BBomDocumentCarriesProvenance(SizeProvenanceCase):
    def test_b1_bom_document_bindings_carry_provenance(self):
        doc = self.build(bbox_parts())
        rows = self.bound_rows(doc)
        self.assertEqual(len(rows), 4, "4 行 dwg_parts")
        for row in rows:
            binding = self.binding_of(row)
            for key in BINDING_PROVENANCE_KEYS:
                self.assertIn(key, binding,
                              "Spec §3 B1：%s 的 size_source_json.dwg_binding 缺 %s"
                              % (row.get("item_key"), key))

    def test_b2_load_bom_also_carries_it(self):
        self.build(bbox_parts())
        doc = self.bom().load_bom(PID, REQ_NO)
        rows = self.bound_rows(doc)
        self.assertTrue(rows, "读回的那一份同样要有回填行")
        for row in rows:
            binding = self.binding_of(row)
            for key in BINDING_PROVENANCE_KEYS:
                self.assertIn(key, binding,
                              "Spec §3 B2：读回路径也要带 %s（不是只在 build_bom() 的返回值里）" % key)

    def test_b3_numbers_and_totals_are_unchanged(self):
        """护栏：本批只加来源，不改数字与统计（Spec §3 B3）。"""
        before = self.build(None)
        before_rows = self.bound_rows(before)
        self.assertEqual(before_rows, [], "没有零件文档时 BOM 不绑任何行（照旧）")
        # `## 468`：键集断言由「逐字相等」改为「必须**包含**本批那六个键」。
        # `packaging-bom-size-quality-accounting.md`（`## 342`）给 `stats` 加了必存在的
        # `size_quality`、给 `gaps` 加了 `bbox_only`，两个键集不可能再逐字相等；该 Spec 的
        # 「已记录的偏差（不改测试）」写明修法就是 `assertLessEqual(冻结集, 实际集)`。
        # 护栏意图（"既有键一个都不许消失"）不减反增：包含式比相等式更能抓到"删键"。
        self.assertLessEqual({"total", "by_category", "computed", "needs_input", "locked",
                              "material_unresolved"},
                             set(before["stats"]),
                             "stats 必须仍带这六个既有键（只允许新增账户，不允许消失）")
        self.assertLessEqual({"needs_input", "missing_variables", "material_unresolved"},
                             set(before["gaps"]),
                             "gaps 必须仍带这三个既有键（只允许新增账户，不允许消失）")
        after = self.build(bbox_parts())
        self.assertEqual(set(after["stats"]), set(before["stats"]), "stats 键集不许变")
        self.assertEqual(set(after["gaps"]), set(before["gaps"]), "gaps 键集不许变")
        got = {(row["length_mm"], row["width_mm"]) for row in self.bound_rows(after)}
        self.assertEqual(got, {(211.6, 161.6), (211.6, 100.0),
                              (161.6, 100.0), (200.0, 50.0)},
                         "Spec §3 B3：length_mm/width_mm 逐字不变")


# --------------------------------------------------------------------------- #
# C 组：两类来源在同一份文档里必须可区分（Spec §3 C1–C3）
# --------------------------------------------------------------------------- #
class CTwoSourcesAreDistinguishable(SizeProvenanceCase):
    def test_c1_bbox_rows_are_identifiable(self):
        doc = self.build(bbox_parts())
        bbox = [row for row in self.bound_rows(doc)
                if self.binding_of(row).get("size_source") == "component_bbox"]
        self.assertEqual(len(bbox), 3, "夹具里 3 件是包围盒来源")
        for row in bbox:
            self.assertEqual(self.binding_of(row).get("size_quality"), "bbox_only",
                             "Spec §3 C1：包围盒来源必须能一眼认出来")

    def test_c2_unfolded_rows_are_not_marked_as_bbox(self):
        doc = self.build(unfolded_parts())
        for row in self.bound_rows(doc):
            self.assertEqual(self.binding_of(row).get("size_quality"), "unfolded",
                             "Spec §3 C2：闭合轮廓来源不许被标成 bbox_only")

    def test_c3_the_two_sources_are_distinguishable_in_one_document(self):
        """Spec §3 C3：同一份 BOM 里 3 个 bbox_only + 1 个 unfolded 必须能区分出来。"""
        doc = self.build(bbox_parts())
        qualities = [self.binding_of(row).get("size_quality")
                     for row in self.bound_rows(doc)]
        self.assertEqual(sorted(qualities, key=str),
                         ["bbox_only", "bbox_only", "bbox_only", "unfolded"],
                         "真样本形状：4 行里 3 行包围盒、1 行真展开，读接口上必须分得开")


# --------------------------------------------------------------------------- #
# D 组：护栏（Spec §3 D1–D5）
# --------------------------------------------------------------------------- #
class DGuards(SizeProvenanceCase):
    def test_d1_parts_document_round_trips_both_keys(self):
        """零件文档侧的 size_source / outline_status 原样存取（闭集不变）。"""
        module = parts_module()
        module.save_parts(PID, parts_doc(bbox_parts()))
        record = module.load_parts(PID) or {}
        got = {row["part_code"]: (row.get("size_source"), row.get("outline_status"))
               for row in (record.get("parts") or [])}
        self.assertEqual(got, {"DWG-P01": ("component_bbox", "open"),
                               "DWG-P02": ("component_bbox", "open"),
                               "DWG-P03": ("component_bbox", "open"),
                               "DWG-P04": ("closed_outline", "closed")})
        for size_source, outline_status in got.values():
            self.assertIn(size_source, SIZE_SOURCES, "size_source 闭集不许被扩")
            self.assertIn(outline_status, ("closed", "open"), "outline_status 闭集不许被扩")

    def test_d2_open_parts_must_bind_as_bbox_only(self):
        """Spec §3 D2：未闭合（open）的零件必须能在回填行上被认出来，且不许标成展开来源。"""
        doc = self.build(bbox_parts())
        rows = self.bound_rows(doc)
        opened = [row for row in rows
                  if self.binding_of(row).get("outline_status") == "open"]
        self.assertEqual(len(opened), 3,
                         "Spec §3 D2：夹具里 3 件是未闭合零件，回填行上必须能认出它们"
                         "（现在 outline_status 一个都没落地）")
        for row in opened:
            binding = self.binding_of(row)
            self.assertEqual(binding.get("size_source"), "component_bbox",
                             "open 的零件不许被写成 closed_outline")
            self.assertEqual(binding.get("size_quality"), "bbox_only",
                             "未闭合零件的数字就是包围盒，不许与展开来源长得一样")

    def test_d3_sourceless_parts_are_never_assumed_unfolded(self):
        """Spec §3 D3：来源键缺失时不许猜成 unfolded（宁可按包围盒或如实留空）。"""
        doc = self.build(sourceless_parts())
        rows = self.bound_rows(doc)
        self.assertEqual(len(rows), 4, "老版本文档照旧能绑（本批不改绑定门槛）")
        for row in rows:
            quality = self.binding_of(row).get("size_quality")
            self.assertNotEqual(quality, "unfolded",
                                "Spec §3 D3：没有来源证据时不许算成 unfolded")

    def test_d4_binding_count_is_unchanged(self):
        """护栏：绑定行数照旧 4 行（披露不是拒绝）。"""
        doc = self.build(bbox_parts())
        self.assertEqual(len(self.bound_rows(doc)), 4)
        self.assertEqual(doc["stats"]["computed"], 10, "与冻结红测一致：10 行 computed")

    def test_d5_binding_rule_id_and_locked_behaviour_are_untouched(self):
        """护栏：rule_id 与"锁定行绝不碰"照旧。"""
        module = parts_module()
        rows = self.template_items()
        for row in rows:
            if row.get("bom_category") in ("box_part", "optional_part"):
                row["locked"] = 1
        result = module.bind_rows(rows, parts_doc(bbox_parts()))
        self.assertEqual(result["bound"], 0, "锁定行一律不绑")
        self.assertEqual(result["skipped_locked"], 4)
        self.assertEqual(result["rule_id"], "dwg_parts_row_pairing_v1")


if __name__ == "__main__":
    unittest.main()
