"""红测：成本单的输入版本要"算时记下"，不是"读时现取"。

Spec：`docs/specs/packaging-cost-input-version-pinning.md`

现状缺口（代码级，三处，都可指到行）：
  · `packaging_cost.py:2297 load_cost()` **无条件**用 `_upstream_route_version()`（`:2301`，
    读接口那一刻现取）重写 `source_versions` —— 路线重确认一次，旧成本单的
    `route_version` 就跟着变，这个字段名不副实；
  · 成本表与读回体里都没有来源字段（`da_repo.py:903 _PACKAGING_COST_COLUMNS` 无来源列、
    `packaging_cost.py:2235 _rehydrate()` 不返回来源、`da_db.py:30 _ADDED_COLUMNS` 无补列）
    —— 落库都存不下，读回自然无从比对；
  · 成本逐行吃 BOM（`packaging_cost.py:1866`），但**没有 BOM 指纹、也从不比对**：
    BOM 重建后旧成本照旧 `built=true`，没有任何"输入变了"的标记
    （对照 `packaging_match.py:652` 已有的 `stale` / `stale_reasons`）。

纪律：只读源码 + 假仓库；不连 PG / SQLite 生产库、不发 HTTP、不写任何文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_cost as cost           # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-COSTPIN-001"

OLD_BOM_HASH = "bom-hash-v1-old"
STORED_ROUTE_VERSION = "route:v1"
LIVE_ROUTE_VERSION = "route:v2"

BOM_ROW_A = {"item_key": "RB02001-P02", "bom_category": "box_part", "length_mm": 440.123,
             "width_mm": 482.92, "material": "300g双铜哑胶", "seq": 1}
BOM_ROW_B = {"item_key": "RB02001-P08", "bom_category": "box_part", "length_mm": 120.0,
             "width_mm": 80.0, "material": "灰板", "seq": 2}


def _stored_row(source_versions=None, sentinel=True):
    """落库回来的成本估算行（`source_versions_json` 是本批要新增的列）。"""
    row = {"estimate_id": 7, "project_id": PID, "requirement_no": REQ_NO,
           "scenario_code": "default", "industry": "packaging",
           "engine_version": cost.ENGINE_VERSION, "cost_profile": cost.COST_PROFILE,
           "currency": "CNY", "quote_quantity": 1000, "tax_rate": 0.13,
           "loss_base_scope": cost.DEFAULT_LOSS_BASE_SCOPE,
           "material_total": 100.0, "process_total": 50.0, "labor_total": 20.0,
           "tooling_total": 0.0, "packaging_total": 5.0, "freight_total": 3.0,
           "other_total": 2.0, "subtotal": 180.0, "loss_amount": 1.8,
           "total_cost": 1234.5, "has_gaps": 0, "gaps_json": "[]",
           "assumptions_json": "[]", "computed_at": "2026-09-22 10:00:00",
           "computed_by": "PE1", "computed_by_role": "process_engineer"}
    if source_versions is not None:
        row["source_versions_json"] = json.dumps(source_versions, ensure_ascii=False)
    return row


STORED_VERSIONS = {"route_version": STORED_ROUTE_VERSION,
                   "engine_version": cost.ENGINE_VERSION,
                   "bom_hash": OLD_BOM_HASH, "bom_item_total": 2}


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


def _load_cost(row, bom_rows=(), route_version=LIVE_ROUTE_VERSION, bom_error=None):
    def bom_loader(*args, **kwargs):
        if bom_error:
            raise bom_error
        return list(bom_rows)

    with _Patch((cost, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                (cost.da_repo, "load_packaging_cost", lambda *a, **k: row),
                (cost.da_repo, "load_packaging_cost_items", lambda *a, **k: []),
                (cost, "_upstream_route_version", lambda *a, **k: route_version),
                (cost.da_repo, "load_packaging_bom", bom_loader)):
        return cost.load_cost(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# H 组：输入版本的记录与比对
# --------------------------------------------------------------------------- #
class HInputVersionPinning(unittest.TestCase):
    def test_h1_rebuilt_bom_is_reported(self):
        result = _load_cost(_stored_row(STORED_VERSIONS), bom_rows=[BOM_ROW_A, BOM_ROW_B])
        self.assertIs(True, result.get("stale"),
                      "BOM 变过之后旧成本必须被标成过期（Spec §2.2）：现在连读都不读 BOM，"
                      "一份输入已经换掉的报价底稿看起来完全有效")
        self.assertIn("bom_rebuilt", result.get("stale_reasons") or [],
                      "必须说清是哪一项变了（`bom_rebuilt`），不许只给一个布尔")
        versions = result.get("source_versions") or {}
        self.assertEqual(OLD_BOM_HASH, versions.get("bom_hash"),
                         "`source_versions` 必须是**存的**那一份：算的时候记下哪一版 BOM，"
                         "读的时候不许现取")

    def test_h2_stored_route_version_is_not_rewritten_at_read_time(self):
        result = _load_cost(_stored_row(STORED_VERSIONS), bom_rows=[BOM_ROW_A, BOM_ROW_A])
        versions = result.get("source_versions") or {}
        self.assertEqual(STORED_ROUTE_VERSION, versions.get("route_version"),
                         "`source_versions.route_version` 是**算的时候**那一版（Spec §2.2）："
                         "现在被 `_upstream_route_version()` 的现值覆盖 —— 路线重确认一次，"
                         "旧成本单的追溯字段就跟着变，等于说谎")
        self.assertIn("route_reconfirmed", result.get("stale_reasons") or [],
                      "路线版本对不上要单独报 `route_reconfirmed`")

    def test_h3_legacy_estimate_without_provenance_is_disclosed(self):
        result = _load_cost(_stored_row(None), bom_rows=[BOM_ROW_A])
        self.assertEqual({}, result.get("source_versions") or {},
                         "历史成本单没有来源时给 `{}`（Spec §2.2）：不许用现取的路线版本兜上去")
        self.assertIn("provenance_missing", result.get("stale_reasons") or [],
                      "没有来源本身就要披露（`provenance_missing`），不许当成'没过期'")

    def test_h4_unreadable_bom_is_unknown_not_a_verdict(self):
        result = _load_cost(_stored_row(STORED_VERSIONS), bom_error=RuntimeError("bom store down"))
        flag = result.get("bom_unavailable") or {}
        self.assertEqual("bom_unavailable", flag.get("code"),
                         "当前 BOM 读不到必须显式披露（Spec §2.2）")
        self.assertNotIn("bom_rebuilt", result.get("stale_reasons") or [],
                         "读不到 BOM 时'比较不了'，不许断言 BOM 变过")

    def test_h5_bom_fingerprint_is_stable_and_order_insensitive(self):
        first = cost.bom_input_hash([BOM_ROW_A, BOM_ROW_B])
        second = cost.bom_input_hash([BOM_ROW_B, BOM_ROW_A])
        self.assertTrue(str(first or "").strip(),
                        "成本要能说'算的是哪一版 BOM'，就必须有 BOM 指纹（Spec §2.1）")
        self.assertEqual(first, second,
                         "同一批 BOM 行换个顺序必须是同一个指纹（行序不是内容）")
        self.assertNotEqual(first, cost.bom_input_hash([BOM_ROW_A]),
                            "内容不同必须指纹不同")
        self.assertEqual("", cost.bom_input_hash([]),
                         "没有 BOM 行时给空串（'没有 BOM' 不是一版内容）")


# --------------------------------------------------------------------------- #
# H 组（护栏）：既有口径不许被本批改掉
# --------------------------------------------------------------------------- #
class HExistingContractUnchanged(unittest.TestCase):
    def test_h6_existing_keys_are_verbatim(self):
        result = _load_cost(_stored_row(STORED_VERSIONS), bom_rows=[BOM_ROW_A])
        self.assertIs(True, result.get("built"), "既有键逐字不变")
        self.assertEqual(1234.5, result.get("total_cost"), "既有键逐字不变")
        self.assertEqual([], result.get("items"), "既有键逐字不变")
        self.assertEqual("PE1", result.get("computed_by"), "既有键逐字不变")
        self.assertEqual(cost.ENGINE_VERSION, (result.get("source_versions") or {})
                         .get("engine_version"), "引擎版本口径逐字不变")
        self.assertEqual(cost.READINESS_FORMAL,
                         (result.get("readiness") or {}).get("verdict"),
                         "readiness 裁决口径逐字不变")

    def test_h7_never_computed_is_not_a_stale_estimate(self):
        result = _load_cost(None, bom_rows=[BOM_ROW_A])
        self.assertIs(False, result.get("built"), "没算过的路径逐字不变")
        self.assertIs(False, bool(result.get("stale")), "'还没算'不是'过期'，不许标 stale")
        self.assertEqual([], result.get("stale_reasons") or [],
                         "'还没算'不许报 `provenance_missing`（那是历史成本单才有的事）")
        self.assertEqual(LIVE_ROUTE_VERSION, (result.get("source_versions") or {})
                         .get("route_version"),
                         "没算过时没有'算的那一刻'，既有口径（现取上游版本）保持不变")


if __name__ == "__main__":
    unittest.main()
