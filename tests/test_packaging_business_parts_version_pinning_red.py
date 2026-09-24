"""红测：权威清单的版本必须像零件文档一样「建时固定、读时比对」。

Spec：`docs/specs/packaging-business-parts-version-pinning.md`
依赖：`packaging-business-parts-and-cad-plan-view.md` §7（下游按 `business_parts_id/hash` 判 stale）、
`packaging-bom-parts-version-binding.md`（零件轴的做法）、`packaging-cost-input-version-pinning.md`。

现状缺口（代码级，见 Spec §1）：
  · `packaging_bom.business_part_rows()` 的行只带 `{"kind": "authority_workbook", sheet, row}`，
    **没有清单版本**（零件轴在 `size_source_json.dwg_binding` 里写了 `parts_id`/`parts_hash`）；
  · `packaging_bom.load_bom()` 的 `source_versions.business_parts_id/hash` 是**读时现取**的，
    没有 `business_parts_stale`（零件轴有 `parts_binding_stale`）；
  · `packaging_cost._input_drift()` 只比 `route_version` 与 `bom_hash`，业务部件那条轴无人比。

纪律：只读源码 + 临时 SQLite / 临时 meta 沙盘，不连 PG / 34、不发 HTTP、不写业务数据。
禁止为了让红测转绿而修改本文件；口径变化改 Spec。
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_cost as cost  # noqa: E402
from tests.test_packaging_parametric_bom_red import (  # noqa: E402
    PID, REQ_NO, BomCase, load_bom)

PARTS_PKG = "tech_app.backend.services.packaging_parts"

#: 新原因（Spec §2.2 / §2.3 的闭集取值之一）。
REASON_REIMPORTED = "business_parts_reimported"
#: `business_parts_stale` 每一条必须有的键（Spec §2.2）。
STALE_ROW_KEYS = ("item_key", "bound_business_parts_hash",
                  "current_business_parts_hash", "reason")
V1_HASH = "hash-aaaa"
V2_HASH = "hash-bbbb"


def parts_module():
    return importlib.import_module(PARTS_PKG)


def authority_row(code, name, *, length=307.07, width=528.89,
                  material="225G太阳铜版底PET光银", process="开料-模切",
                  sheet="零部件排版工艺", row=4):
    """一件业务部件（形状照真样本：`authority` 就是权威原文那一块）。"""
    return {"business_part_code": code, "name": name,
            "authority": {"sequence_no": 1, "length_mm": length, "width_mm": width,
                          "material_text": material, "layout_text": "787x560mm=1M",
                          "process_text": process, "note": "", "quantity": "",
                          "source": {"sheet": sheet, "row": row}},
            "geometry_binding": {"status": "unbound", "component_ids": []}}


def biz_doc(rows, *, pid="business-parts:aaaa", phash=V1_HASH):
    return {"engine_version": "packaging-business-parts/1",
            "business_parts_id": pid, "business_parts_hash": phash,
            "business_parts": list(rows), "geometry_evidence": {},
            "unavailable": [], "stats": {}}


def payload_of(row):
    """`size_source_json` 可能是 JSON 串（纯函数产物）或 dict（读回来的行）。"""
    raw = row.get("size_source_json")
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- #
# A. 纯函数：行上必须固定它建的时候照的那一版清单
# --------------------------------------------------------------------------- #
class ARowsPinTheListVersion(unittest.TestCase):
    def module(self):
        module = load_bom()
        self.assertIsNotNone(module, "缺少 tech_app/backend/services/packaging_bom.py")
        return module

    def rows(self, doc):
        return self.module().business_part_rows(doc)

    def test_a1_every_row_carries_the_version_it_was_built_from(self):
        doc = biz_doc([authority_row("JWXR21-P01", "左盖面纸"),
                       authority_row("JWXR21-P28", "磁铁", length=15.0, width=5.0)])
        rows = self.rows(doc)
        self.assertEqual(2, len(rows), "行数口径逐字不变")
        for row in rows:
            payload = payload_of(row)
            self.assertEqual("business-parts:aaaa", payload.get("business_parts_id"),
                             "行上必须固定它建的时候照的那一版清单（Spec §2.1）：%r" % (payload,))
            self.assertEqual(V1_HASH, payload.get("business_parts_hash"),
                             "行上必须固定清单内容哈希（Spec §2.1）：%r" % (payload,))
            # `## 494` §2.4：新写入用 `reference_workbook`；老行的 `authority_workbook` 仍被识别。
            self.assertEqual("reference_workbook", payload.get("kind"), "出处 kind 按新口径")
            self.assertEqual("零部件排版工艺", payload.get("sheet"), "既有出处键逐字不变")
            self.assertEqual(4, payload.get("row"), "既有出处键逐字不变")

    def test_a2_document_without_a_version_never_invents_one(self):
        row = self.rows(biz_doc([authority_row("JWXR21-P01", "左盖面纸")], pid="", phash=""))[0]
        payload = payload_of(row)
        self.assertIn("business_parts_id", payload, "键必须存在（缺了就给空串）")
        self.assertIn("business_parts_hash", payload, "键必须存在（缺了就给空串）")
        self.assertEqual("", payload.get("business_parts_id"), "文档没给版本时不许编一个")
        self.assertEqual("", payload.get("business_parts_hash"), "文档没给版本时不许编一个")

    def test_a3_row_shape_is_unchanged(self):
        row = self.rows(biz_doc([authority_row("JWXR21-P01", "左盖面纸")]))[0]
        self.assertEqual("box_part", row.get("bom_category"), "类别口径逐字不变")
        self.assertEqual("JWXR21-P01", row.get("item_key"), "行主键口径逐字不变")
        self.assertEqual("JWXR21-P01", row.get("part_code"), "part_code 口径逐字不变")
        self.assertEqual("", row.get("material_code"), "材料码口径逐字不变（本批不碰映射）")
        self.assertIn(row.get("status"), ("computed", "needs_input"), "状态口径逐字不变")


# --------------------------------------------------------------------------- #
# B. BOM 读侧：比对「建的」与「当前的」，三态两两可分
# --------------------------------------------------------------------------- #
class BReadSideCompares(BomCase):
    def build_with(self, rows):
        self.save_requirement()
        self.confirm_box()
        parts_module().save_business_parts(PID, biz_doc(rows))
        return self.bom().build_bom(PID, REQ_NO)

    def gap_code(self, doc):
        block = doc.get("business_parts") if isinstance(doc.get("business_parts"), dict) else {}
        gap = block.get("gap") if isinstance(block.get("gap"), dict) else {}
        return gap.get("code")

    def test_b1_same_version_is_not_reported(self):
        self.build_with([authority_row("JWXR21-P01", "左盖面纸")])
        doc = self.bom().load_bom(PID, REQ_NO)
        self.assertIsInstance(doc.get("business_parts_stale"), list,
                              "load_bom() 必须给 business_parts_stale，且键必须存在（Spec §2.2）")
        self.assertEqual([], doc.get("business_parts_stale"),
                         "版本对得上时不许报过期（Spec §2.2）")

    def test_b2_reimport_makes_the_rows_stale(self):
        self.save_requirement()
        self.confirm_box()
        first = parts_module().save_business_parts(
            PID, biz_doc([authority_row("JWXR21-P01", "左盖面纸")]))
        self.bom().build_bom(PID, REQ_NO)
        second = parts_module().save_business_parts(
            PID, biz_doc([authority_row("JWXR21-P01", "左盖面纸", length=320.0)]))
        doc = self.bom().load_bom(PID, REQ_NO)
        stale = doc.get("business_parts_stale")
        self.assertIsInstance(stale, list, "load_bom() 必须给 business_parts_stale（Spec §2.2）")
        self.assertEqual(1, len(stale),
                         "重新导入清单后，那一行必须被报成「按旧清单建的」（Spec §2.2）：%r" % (stale,))
        row = stale[0]
        for key in STALE_ROW_KEYS:
            self.assertIn(key, row, "business_parts_stale 每一条缺键 %s（Spec §2.2）" % key)
        self.assertEqual("JWXR21-P01", row["item_key"])
        self.assertEqual(first["business_parts_hash"], row["bound_business_parts_hash"],
                         "报的必须是**建的**那一版，不是当前值")
        self.assertEqual(second["business_parts_hash"], row["current_business_parts_hash"],
                         "报的必须是**当前**那一版")
        self.assertEqual(REASON_REIMPORTED, row["reason"], "原因取值必须在闭集里（Spec §2.2）")

    def test_b3_unreadable_list_is_not_a_verdict(self):
        self.build_with([authority_row("JWXR21-P01", "左盖面纸")])
        with mock.patch.object(parts_module(), "load_business_parts",
                               side_effect=RuntimeError("meta down")):
            doc = self.bom().load_bom(PID, REQ_NO)
        self.assertIsInstance(doc.get("business_parts_stale"), list,
                              "load_bom() 必须给 business_parts_stale（Spec §2.2）")
        self.assertEqual([], doc.get("business_parts_stale") or [],
                         "当前清单读不到时是「比较不了」，不许断言过期（Spec §2.2）")
        self.assertEqual("business_parts_document_unavailable", self.gap_code(doc),
                         "既有披露口径逐字不变：读不到 ≠ 没有清单")

    def test_b4_without_a_list_there_is_nothing_to_compare(self):
        self.save_requirement()
        self.confirm_box()
        self.bom().build_bom(PID, REQ_NO)
        doc = self.bom().load_bom(PID, REQ_NO)
        self.assertIsInstance(doc.get("business_parts_stale"), list,
                              "没有清单时键也必须存在（Spec §2.2）")
        self.assertEqual([], doc.get("business_parts_stale"))
        self.assertEqual("business_parts_missing", self.gap_code(doc),
                         "既有披露口径逐字不变：没有清单 ≠ 版本对不上")

    def test_b5_existing_keys_are_untouched(self):
        self.build_with([authority_row("JWXR21-P01", "左盖面纸")])
        doc = self.bom().load_bom(PID, REQ_NO)
        for key in ("parts_binding_stale", "parts_document_unavailable", "business_rows",
                    "business_material_rows", "role_unbound", "source_versions", "stats"):
            self.assertIn(key, doc, "既有键逐字不变：%s" % key)
        versions = doc.get("source_versions") or {}
        for key in ("box_type_code", "engine_version", "parts_id", "parts_hash",
                    "business_parts_id", "business_parts_hash", "box_type_codes"):
            self.assertIn(key, versions, "既有 source_versions 键逐字不变：%s" % key)
        self.assertEqual(set(("unfolded", "bbox_only", "unknown")),
                         set((doc.get("stats") or {}).get("size_quality") or {}),
                         "尺寸质量三档口径逐字不变（不许加第四档）")


# --------------------------------------------------------------------------- #
# C. 成本读侧：存了版本就要比
# --------------------------------------------------------------------------- #
STORED_VERSIONS = {"route_version": "route:v1", "engine_version": cost.ENGINE_VERSION,
                   "bom_hash": "", "business_parts_id": "business-parts:old",
                   "business_parts_hash": "hash-old"}


def _stored_row(source_versions):
    return {"estimate_id": 7, "project_id": PID, "requirement_no": REQ_NO,
            "scenario_code": "default", "industry": "packaging",
            "engine_version": cost.ENGINE_VERSION, "cost_profile": cost.COST_PROFILE,
            "currency": "CNY", "quote_quantity": 1000, "tax_rate": 0.13,
            "loss_base_scope": cost.DEFAULT_LOSS_BASE_SCOPE,
            "material_total": 100.0, "process_total": 50.0, "labor_total": 20.0,
            "tooling_total": 0.0, "packaging_total": 5.0, "freight_total": 3.0,
            "other_total": 2.0, "subtotal": 180.0, "loss_amount": 1.8,
            "total_cost": 1234.5, "has_gaps": 0, "gaps_json": "[]",
            "assumptions_json": "[]", "computed_at": "2026-09-22 10:00:00",
            "computed_by": "PE1", "computed_by_role": "process_engineer",
            "source_versions_json": json.dumps(source_versions, ensure_ascii=False)}


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, value in reversed(self.saved):
            setattr(owner, name, value)
        return False


def _load_cost(current, stored=None):
    """`current` = 当前清单文档 / None / 一个异常实例（读挂）。"""
    stored = STORED_VERSIONS if stored is None else stored
    loader = (mock.Mock(side_effect=current) if isinstance(current, BaseException)
              else (lambda *a, **k: current))
    with _Patch((cost, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                (cost.da_repo, "load_packaging_cost", lambda *a, **k: _stored_row(stored)),
                (cost.da_repo, "load_packaging_cost_items", lambda *a, **k: []),
                (cost, "_upstream_route_version", lambda *a, **k: stored.get("route_version") or ""),
                (cost.da_repo, "load_packaging_bom", lambda *a, **k: []),
                (parts_module(), "load_business_parts", loader)):
        return cost.load_cost(PID, REQ_NO)


class CCostStaleCompares(unittest.TestCase):
    def reasons(self, result):
        return result.get("stale_reasons") or []

    def test_c1_reimport_is_reported(self):
        result = _load_cost(biz_doc([authority_row("JWXR21-P01", "左盖面纸")],
                                    pid="business-parts:new", phash="hash-new"))
        self.assertIn(REASON_REIMPORTED, self.reasons(result),
                      "成本单里存了 business_parts_hash 却没人比（Spec §2.3）："
                      "重新导入清单后旧成本必须被标出来")
        self.assertIs(True, result.get("stale"), "有原因就必须 stale=true")

    def test_c2_same_version_is_not_reported(self):
        result = _load_cost(biz_doc([authority_row("JWXR21-P01", "左盖面纸")],
                                    pid="business-parts:old", phash="hash-old"))
        self.assertNotIn(REASON_REIMPORTED, self.reasons(result),
                         "清单没换就不许报过期（Spec §2.3）")

    def test_c3_unreadable_list_is_not_a_verdict(self):
        result = _load_cost(RuntimeError("meta down"))
        self.assertNotIn(REASON_REIMPORTED, self.reasons(result),
                         "清单读不到时是「比较不了」，不许断言换过（Spec §2.3）")
        flag = result.get("business_parts_unavailable")
        self.assertIsInstance(flag, dict, "必须给 business_parts_unavailable（键必须存在）")
        self.assertEqual("business_parts_unavailable", (flag or {}).get("code"),
                         "读挂必须披露出来（Spec §2.3）")
        self.assertTrue(str((flag or {}).get("reason") or ""), "要给出异常类名（Spec §2.3）")

    def test_c4_history_without_a_version_is_disclosed_not_guessed(self):
        stored = {"route_version": "route:v1", "engine_version": cost.ENGINE_VERSION,
                  "bom_hash": ""}
        result = _load_cost(biz_doc([authority_row("JWXR21-P01", "左盖面纸")],
                                    pid="business-parts:new", phash="hash-new"),
                            stored=stored)
        self.assertNotIn(REASON_REIMPORTED, self.reasons(result),
                         "历史成本单当时没记版本：「没记」≠「变了」（Spec §2.3）")
        flag = result.get("business_parts_unavailable") or {}
        self.assertEqual("business_parts_version_missing", flag.get("code"),
                         "当时没记版本要披露（Spec §2.3），不许当成「没过期」")

    def test_c5_existing_reasons_and_keys_are_untouched(self):
        result = _load_cost(biz_doc([authority_row("JWXR21-P01", "左盖面纸")],
                                    pid="business-parts:old", phash="hash-old"))
        for key in ("stale", "stale_reasons", "bom_unavailable", "route_unavailable",
                    "source_versions", "items", "total_cost", "gaps"):
            self.assertIn(key, result, "既有键逐字不变：%s" % key)
        versions = result.get("source_versions") or {}
        self.assertEqual("route:v1", versions.get("route_version"),
                         "既有埋点逐字不变（不许被现值覆盖）")
        self.assertEqual("", versions.get("bom_hash"), "既有埋点逐字不变")


if __name__ == "__main__":
    unittest.main()
