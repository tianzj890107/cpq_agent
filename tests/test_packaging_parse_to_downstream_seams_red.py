"""红测：解析到下游三处未闭合的缝 —— 人工字段被降级 / 配对复核读不到 / 放行留痕门禁不认。

Spec：`docs/specs/packaging-parse-to-downstream-seams.md`
依赖：`packaging-dwg-parts-extraction`（C7 回填）、`packaging-parametric-bom`（BOM 读接口）、
`packaging-downstream-blockers-close-loop`（§1.3 人工来源判定、§1.4 配对披露、§3.1 放行留痕过桥）。

现状缺口（34 线上实测，不是推断；细节见 Spec §1）：

  · 项目 `559892f033b9` 的 `data` 里 7 个字段值都在、`field_sources` 全是 `manual`，跑完一键解析后
    `field_provenance` 被写成 `status=missing / origin=missing`，于是 `gates` 的
    `box_match / bom / route / cost / quote_publish` 全部 `blocked`（`field_unconfirmed`）——
    值明明在，下游一步都做不了；根因在 `provenance.apply_to_requirement()` 人工分支用
    `_entry_snapshot(candidate)` + `setdefault` 改一份**已经带 status 的候选快照**。
  · 同一项目的 BOM 33 行、4 行走 `source: dwg_parts`，其中 `RB02001-P08`（磁铁）被绑到
    440.1×482.9 的纸面板上（`material_match=false`）；`bind_rows()` 已经返回 `pairing_review`，
    但 `packaging_bom._bind_parts()` 只取 `items` 把它丢掉，
    `GET .../requirement/packaging-bom` 顶层键里没有 `pairing_review` ——
    "在报告里单列不一致项"落成了一次函数返回值，没有任何读接口能看到。
  · 同一项目：`packaging-quote/send` 已 200、报价卡片已推进到 `handoff_pending`、
    交接记录里 `gap_waiver_json` 带着 PE1 写的放行原因；而
    `GET .../drawing-flow?stage=quote_publish` 仍然是 `blocked` + `cost_gaps_unresolved`
    （"成本仍存在缺口，缺口清零后才能生成正式报价"）—— 门禁完全不看 `packaging_handoff`。

本批只写 Spec + 红测（AGENTS.md：Codex 不直接编写业务实现）。
夹具复用既有冻结测试模块，**不复制**它们的常量与假库：
  · `tests.test_packaging_parametric_bom_red.BomCase` —— BOM 真链路（种子 KB + 临时 SQLite + meta 沙盘）；
  · `tests.test_packaging_semantics_red.SemanticsCase` —— 需求单内存看板（A 组）。

A 组与 `packaging-manual-field-confirmation.md`（门禁判据 + 人工确认通道）同一根因、互补口径；
`packaging-quote-draft-and-card-visibility.md` §3.3 把"门禁转绿"的断言委托给本文件 **A5**，不许删。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import copy
import importlib
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_packaging_parametric_bom_red import (  # noqa: E402
    BOX_MAIN, PID, REQ_NO, BomCase)
from tests.test_packaging_semantics_red import SemanticsCase  # noqa: E402

FLOW_PKG = "tech_app.backend.services.packaging_drawing_flow"
PARTS_PKG = "tech_app.backend.services.packaging_parts"

PAIRING_KEYS = ("item_key", "part_code", "row_material", "part_material", "material_match")
BOM_STATS_KEYS = {"total", "by_category", "computed", "needs_input", "locked",
                  "material_unresolved"}
WAIVER = {"by": "PE1", "at": "2026-09-22 00:26:16",
          "reason": "全流程演示：已知缺口按演示口径放行，待语义层实现后收敛",
          "codes": ["material_gsm_missing", "material_price_missing", "no_formula:print"]}
CONFIRMED_FIELDS = ("inner_length", "inner_width", "inner_height", "closure_type",
                    "v_groove", "face_paper_gsm", "quote_quantity")


def load_parts_module():
    try:
        return importlib.import_module(PARTS_PKG)
    except ModuleNotFoundError as exc:
        raise AssertionError("缺少 %s（Spec §3.2）：%s" % (PARTS_PKG, exc))


def panel(part_code, length, width, material, component_id="cmp:probe"):
    """一件图纸零件：只要绑定只认的那几个键。"""
    return {"part_code": part_code, "part_id": part_code, "component_id": component_id,
            "unfolded_length_mm": length, "unfolded_width_mm": width,
            "area_mm2": float(length) * float(width), "material": material,
            "role": "cut", "outline_status": "closed"}


def parts_doc(parts):
    return {"engine_version": "packaging-parts/1", "parts": list(parts),
            "filtered": [], "unavailable": [], "stats": {}, "source": {}, "reviewable": True}


def paper_parts():
    """四件纸件：面积降序，配到 YT-RB-01001-A 的 4 个待绑行上，材料一致。"""
    return [panel("DWG-P01", 211.6, 161.6, "灰板 2.0mm", "cmp:1"),
            panel("DWG-P02", 211.6, 100.0, "灰板 2.0mm", "cmp:2"),
            panel("DWG-P03", 161.6, 100.0, "灰板 2.0mm", "cmp:3"),
            panel("DWG-P04", 200.0, 50.0, "灰板 2.0mm", "cmp:4")]


def magnet_first_parts():
    """同四件，但面积最大的那件是磁铁 → 第 1 个待绑行（纸）必然材料不同类。"""
    parts = paper_parts()
    parts[0]["material"] = "钕铁硼 Ø10×2mm"
    return parts


# --------------------------------------------------------------------------- #
# 共享门禁夹具：在内存需求单上跑**真** gates.build()，只把上游引擎按 name 换掉
# --------------------------------------------------------------------------- #
def stub_module(**functions):
    return types.SimpleNamespace(**functions)


def requirement_doc():
    data = {"industry": "packaging", "packaging_product_name": "礼盒",
            "quote_quantity": 5000}
    values = {"inner_length": "219.6", "inner_width": "89.2", "inner_height": "86.4",
              "closure_type": "磁吸", "v_groove": "yes", "face_paper_gsm": "225",
              "quote_quantity": "5000"}
    for key in CONFIRMED_FIELDS:
        data[key] = values[key]
    data["field_sources"] = {key: "manual" for key in CONFIRMED_FIELDS}
    data["field_provenance"] = {
        key: {"origin": "user_confirmed", "status": "confirmed", "value": data[key],
              "confidence": 1, "evidence_level": "human_review", "evidence_refs": [],
              "conflicts": [], "alternatives": []}
        for key in CONFIRMED_FIELDS}
    return {"project_id": PID, "requirement_no": REQ_NO, "status": "draft",
            "title": "包装需求", "data": data, "history": [], "waivers": []}


def run_gate(case, *, has_gaps=True, handoff=None, requirement=None, stage=""):
    """跑真门禁。`requirement=None` 时不额外打桩 `store.load_requirement`
    （A 组用 `SemanticsCase.memory_requirement` 已经打好的那一份）。"""
    gates = importlib.import_module(FLOW_PKG + ".gates")
    persistence = importlib.import_module(FLOW_PKG + ".persistence")
    anchor = importlib.import_module(FLOW_PKG + ".anchor")
    store = importlib.import_module("tech_app.backend.storage.store")

    engines = {
        "packaging_match": stub_module(
            load_box_match=lambda pid: {"decision": "confirmed",
                                        "confirmed_box_type": BOX_MAIN}),
        "packaging_bom": stub_module(load_bom=lambda pid: {"built": True}),
        "packaging_route": stub_module(load_route=lambda pid: {"status": "confirmed"}),
        "packaging_cost": stub_module(
            load_cost=lambda pid: {"built": True, "has_gaps": has_gaps},
            minimum_charge_policy=lambda: {"status": "chosen"}),
    }
    if handoff is not None:
        engines["packaging_handoff"] = stub_module(
            load_handoff=lambda pid, req="": copy.deepcopy(handoff))

    patches = [
        mock.patch.object(persistence, "load_flow", lambda pid: {}),
        mock.patch.object(anchor, "current_anchor",
                          lambda pid: {"unit_status": "confirmed"}),
        mock.patch.object(anchor, "requirement_snapshot_version",
                          lambda pid: "reqsnap/1:test"),
    ]
    if requirement is not None:
        patches.append(mock.patch.object(store, "load_requirement",
                                         lambda pid: copy.deepcopy(requirement)))
    for patch in patches:
        patch.start()
        case.addCleanup(patch.stop)
    return gates.build(PID, resolve=engines.get, stage=stage)


# --------------------------------------------------------------------------- #
# A 组：人工字段不许被解析降级（Spec §3.1）
# --------------------------------------------------------------------------- #
SEAM_FIELDS = ("inner_length", "inner_width", "inner_height", "closure_type",
               "v_groove", "face_paper_gsm", "quote_quantity")
SEAM_VALUES = {"inner_length": "219.6", "inner_width": "89.2", "inner_height": "86.4",
               "closure_type": "磁吸", "v_groove": "yes", "face_paper_gsm": "225",
               "quote_quantity": "5000"}


def candidate(status="missing", origin="missing", value=None):
    return {"origin": origin, "status": status, "value": value, "confidence": 0.0,
            "evidence_level": "NONE", "evidence_refs": [], "conflicts": []}


def semantics_doc(**fields):
    return {"semantics_id": "sem-seams", "fields": dict(fields)}


class AManualFieldNotDemoted(SemanticsCase):
    """34 实测形状：值在 + `field_sources=manual` + 候选读不到 → 看板被写成 missing。"""

    def manual_data(self, fields=SEAM_FIELDS):
        data = {key: SEAM_VALUES[key] for key in fields}
        data["field_sources"] = {key: "manual" for key in fields}
        return data

    def entry(self, data):
        return (data.get("field_provenance") or {}).get("inner_length") or {}

    def run_case(self, *, seed, semantics):
        service, req, _state, project_id = self.memory_requirement(seed)
        result = self.apply(semantics, service, project_id)
        data = (result or {}).get("data") or req.get("data") or {}
        return data, project_id

    def test_a1_missing_candidate_does_not_demote_a_manual_value(self):
        data, _pid = self.run_case(seed=self.manual_data(("inner_length",)),
                                   semantics=semantics_doc(inner_length=candidate()))
        row = self.entry(data)
        self.assertEqual(row.get("status"), "confirmed",
                         "人工已填的值必须留在 confirmed（Spec §3.1 A1）："
                         "34 上这里被写成 missing，下游 5 段全 blocked")
        self.assertEqual(row.get("origin"), "user_confirmed")
        self.assertEqual(str(row.get("value")), "219.6")
        self.assertEqual(data.get("inner_length"), "219.6")
        self.assertEqual((data.get("field_sources") or {}).get("inner_length"), "manual")

    def test_a2_needs_confirmation_candidate_does_not_demote_either(self):
        data, _pid = self.run_case(
            seed=self.manual_data(("inner_length",)),
            semantics=semantics_doc(inner_length=candidate("needs_confirmation",
                                                          "inferred_from_geometry", "210")))
        row = self.entry(data)
        self.assertEqual(row.get("status"), "confirmed", "Spec §3.1 A2")
        self.assertEqual(data.get("inner_length"), "219.6", "人工值不许被候选顶掉")

    def test_a3_evidence_still_lands_in_alternatives(self):
        data, _pid = self.run_case(
            seed=self.manual_data(("inner_length",)),
            semantics=semantics_doc(inner_length=candidate("needs_confirmation",
                                                          "inferred_from_geometry", "210")))
        row = self.entry(data)
        self.assertTrue(row.get("alternatives"),
                        "候选证据必须仍然进 alternatives（Spec §3.1 A3）：披露不许丢")

    def test_a4_without_a_manual_source_missing_stays_missing(self):
        service, req, _state, project_id = self.memory_requirement({"inner_length": "219.6"})
        result = self.apply(semantics_doc(inner_length=candidate()), service, project_id)
        data = (result or {}).get("data") or req.get("data") or {}
        row = self.entry(data)
        self.assertEqual(row.get("status"), "missing",
                         "没人填过的字段不许被标成 confirmed（Spec §3.1 A4 护栏）")

    def test_a5_gates_open_for_manual_fields_after_parsing(self):
        # 必须用同一个 project_id：门禁按 pid 去 store 读需求单，
        # 换成别的号会读到空需求、把 field_missing 当成"通过"（空断言）。
        service, _req, _state, project_id = self.memory_requirement(self.manual_data(),
                                                                    project_id=PID)
        self.apply(semantics_doc(**{key: candidate() for key in SEAM_FIELDS}),
                   service, project_id)
        for stage in ("box_match", "bom", "route", "cost"):
            entry = run_gate(self, stage=stage)
            codes = [row.get("code") for row in entry.get("blocking") or []]
            self.assertNotIn("field_unconfirmed", codes,
                             "%s 不许再报 field_unconfirmed（Spec §3.1 A5）：实际 %s"
                             % (stage, codes))


# --------------------------------------------------------------------------- #
# B 组：配对复核必须能从 BOM 读接口读到（Spec §3.2）
# --------------------------------------------------------------------------- #
class BPairingReviewExposure(BomCase):
    def build(self, parts=None, requirement_no=REQ_NO):
        self.save_requirement()
        self.confirm_box()
        if parts is not None:
            load_parts_module().save_parts(PID, parts_doc(parts))
        return self.bom().build_bom(PID, requirement_no)

    def test_b1_bom_document_carries_pairing_review(self):
        doc = self.build(magnet_first_parts())
        self.assertIn("pairing_review", doc,
                      "BOM 文档必须带 pairing_review（Spec §3.2 B1）："
                      "bind_rows() 算出来了，_bind_parts() 不能把它丢掉")
        review = doc["pairing_review"]
        self.assertIsInstance(review, list)
        self.assertTrue(review, "磁铁绑到纸面板上时必须有一条不一致项")
        for row in review:
            for key in PAIRING_KEYS:
                self.assertIn(key, row, "不一致项缺键 %s" % key)
            self.assertEqual(row["material_match"], False)

    def test_b2_no_mismatch_means_empty_list_not_missing_key(self):
        doc = self.build(paper_parts())
        self.assertIn("pairing_review", doc, "没有不一致时也要给 []，不许省略键（Spec §3.2 B2）")
        self.assertEqual(list(doc["pairing_review"]), [])

    def test_b3_load_bom_also_carries_it(self):
        self.build(magnet_first_parts())
        doc = self.bom().load_bom(PID, REQ_NO)
        self.assertIn("pairing_review", doc,
                      "读回路径也必须带（Spec §3.2 B4）：不是只在 build_bom() 的返回值里")
        self.assertTrue(list(doc["pairing_review"]), "读回的那一份同样要有不一致项")

    def test_b4_stats_and_binding_are_unchanged(self):
        """护栏：本批只加披露，不改既有口径（Spec §3.2 B5）。"""
        self.save_requirement()
        self.confirm_box()
        before = self.bom().build_bom(PID, REQ_NO)
        self.assertEqual(set(before["stats"]), BOM_STATS_KEYS,
                         "stats 键集不许变")
        after = self.build(magnet_first_parts())
        self.assertEqual(set(after["stats"]), BOM_STATS_KEYS)
        bound_rows = [row for row in after["items"] if row.get("source") == "dwg_parts"]
        self.assertEqual(len(bound_rows), 4,
                         "4 个待绑行照旧全部绑定（材料不一致仍然是披露，不是拒绝）")
        self.assertEqual(set(after["gaps"]), {"needs_input", "missing_variables",
                                              "material_unresolved"})


# --------------------------------------------------------------------------- #
# C 组：放行留痕必须能在门禁上认出来（Spec §3.3）
# --------------------------------------------------------------------------- #
class QuotePublishWaiverCase(unittest.TestCase):
    """放行留痕必须能在门禁上被认出来。"""

    def gate(self, *, has_gaps=True, handoff=None):
        return run_gate(self, has_gaps=has_gaps, handoff=handoff,
                        requirement=requirement_doc(), stage="quote_publish")

    @staticmethod
    def gap_entry(entry):
        for row in entry.get("blocking") or []:
            if row.get("code") == "cost_gaps_unresolved":
                return row
        return {}

    def test_c1_a_valid_waiver_is_disclosed_on_the_gate(self):
        handoff = {"handoff_no": "pkghandoff:%s:%s:default:1" % (PID, REQ_NO),
                   "has_gaps": True, "gap_waiver_json": json.dumps(WAIVER, ensure_ascii=False)}
        entry = self.gate(has_gaps=True, handoff=handoff)
        row = self.gap_entry(entry)
        self.assertTrue(row, "缺口未清时 cost_gaps_unresolved 必须还在 blocking 里（Spec §3.3 C4）")
        self.assertEqual(row.get("waived"), True,
                         "已按留痕放行的缺口必须标出来（Spec §3.3 C1）："
                         "34 上放行成功了，门禁却只给一句『缺口清零后才能生成正式报价』")
        self.assertEqual((row.get("waiver") or {}).get("by"), WAIVER["by"])
        self.assertEqual((row.get("waiver") or {}).get("reason"), WAIVER["reason"])
        self.assertTrue(entry.get("waiver"), "entry 顶层也要给同一份放行摘要")

    def test_c2_no_gaps_means_no_waiver_key(self):
        entry = self.gate(has_gaps=False, handoff=None)
        self.assertFalse(self.gap_entry(entry), "没有缺口就不该有 cost_gaps_unresolved")
        self.assertFalse(entry.get("waiver"), "没有缺口时不许编一份放行摘要（Spec §3.3 C2）")

    def test_c3_handoff_without_a_valid_waiver_is_not_treated_as_waived(self):
        bad = [
            {"gap_waiver_json": ""},
            {"gap_waiver_json": json.dumps({"by": "", "at": WAIVER["at"],
                                            "reason": WAIVER["reason"]})},
            {"gap_waiver_json": json.dumps({"by": WAIVER["by"], "at": "",
                                            "reason": WAIVER["reason"]})},
            {"gap_waiver_json": json.dumps({"by": WAIVER["by"], "at": WAIVER["at"],
                                            "reason": ""})},
            {"gap_waiver_json": json.dumps({"by": WAIVER["by"], "at": WAIVER["at"],
                                            "reason": WAIVER["reason"], "codes": []})},
            {"gap_waiver_json": json.dumps({"by": WAIVER["by"], "at": WAIVER["at"],
                                            "reason": WAIVER["reason"],
                                            "codes": ["loss_rate_missing"]})},
        ]
        for handoff in bad:
            with self.subTest(handoff=handoff):
                entry = self.gate(has_gaps=True, handoff=handoff)
                row = self.gap_entry(entry)
                self.assertTrue(row, "缺口未清时 blocking 不许少（Spec §3.3 C4）")
                self.assertNotEqual(row.get("waived"), True,
                                    "留痕不合法时不许当放行（Spec §3.3 C3）：%s" % handoff)
                self.assertFalse(entry.get("waiver"))

    def test_c4_gate_never_drops_the_blocking_entry(self):
        handoff = {"has_gaps": True, "gap_waiver_json": json.dumps(WAIVER, ensure_ascii=False)}
        entry = self.gate(has_gaps=True, handoff=handoff)
        self.assertEqual(entry.get("status"), "blocked",
                         "披露不是放宽：有缺口就还是 blocked（Spec §3.3 C4）")
        self.assertIn("cost_gaps_unresolved",
                      [row.get("code") for row in entry.get("blocking") or []])


if __name__ == "__main__":
    unittest.main()
