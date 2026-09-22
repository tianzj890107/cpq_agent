"""红测：成本单读回来还要说得出「这一单绑了哪几项包材」（Spec
`packaging-cost-content-binding-replay.md`）。

现状缺口（实测，不是推断）：
  · `compute_project()` 算完的结果体带 `content_binding`（`{source, bound_total, unbound_total,
    bound_codes, unbound_codes}`），**但这一份从来没落库** —— `da_repo.save_packaging_cost()`
    的 `_PACKAGING_COST_COLUMNS` 与显式键里都没有它；
  · `_rehydrate()`（`packaging_cost.py:2647`）读回时改成**现算**：`bound_content_codes_detail({})`
    （喂空 payload，等于去问**当前**的数据源）+ `row.get("gaps_unbound_to_order")` —— 而后者
    **不是** `wip_packaging_cost_estimate` 的列（`SELECT *` 读不回）；
  · 真引擎真库实测（`tests/test_packaging_cost_engine_red.py` 的夹具，同一条成本单）：
    算完 `unbound_total = 7` / `unbound_codes` 7 项 → 读回 `0` / `[]`；
    就绪门 `readiness.unbound_total` 7 → 0，reasons 里那句
    「包材绑定数据源缺失：7 条包材缺口只披露不阻断」也一起消失；
  · 把 `bound_content_codes_detail` 换成回答 `authoritative` 的桩，读一条算时是 `none` 的成本单
    会读到 `authoritative` + 桩给的那个包材项 —— 与 `packaging-cost-input-version-pinning.md`
    §2.2（读侧只读存的那一份）在绑定这一轴上完全相反。

纪律：真引擎真库（临时 SQLite / 临时 meta 目录）用于 A 组，其余组用假仓库行 + 打桩；
不连 PG / 34、不发 HTTP、不写生产数据。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_cost as cost           # noqa: E402
from tech_app.backend.storage import da_db, da_repo                    # noqa: E402
from tests.test_packaging_cost_engine_red import CostCase               # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-COSTBIND-001"

BINDING_KEYS = ("source", "bound_total", "unbound_total", "bound_codes", "unbound_codes")
STORED_BINDING = {"source": "none", "bound_total": 0, "unbound_total": 7,
                  "bound_codes": [],
                  "unbound_codes": ["PKG-CT-BAG", "PKG-CT-DIVIDER", "PKG-CT-LABEL"]}
HEADLINE_FRAGMENT = "包材绑定数据源缺失"

UNBOUND_GAP = {"code": "content_formula_error:PKG-CT-BAG", "content_code": "PKG-CT-BAG"}


def _stored_row(content_binding="__absent__", with_unbound_rows=False):
    """落库回来的成本估算行（`content_binding_json` 是本批要新增的列）。"""
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
    if content_binding != "__absent__":
        row["content_binding_json"] = json.dumps(content_binding, ensure_ascii=False)
    if with_unbound_rows:
        row["gaps_unbound_to_order"] = [dict(UNBOUND_GAP)]
    return row


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


def _load_cost(row):
    with _Patch((cost, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                (cost.da_repo, "load_packaging_cost", lambda *a, **k: row),
                (cost.da_repo, "load_packaging_cost_items", lambda *a, **k: []),
                (cost, "_upstream_route_version", lambda *a, **k: ""),
                (cost.da_repo, "load_packaging_bom", lambda *a, **k: [])):
        return cost.load_cost(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# A 组：真引擎真库往返（算完那一份 == 读回来那一份）
# --------------------------------------------------------------------------- #
class ARoundTrip(CostCase):
    def binding_of(self, result):
        return {key: result["content_binding"][key] for key in BINDING_KEYS}

    def test_a1_read_back_is_verbatim(self):
        self.prepare()
        built = self.build_cost()
        loaded = self.load_cost()
        self.assertEqual(built["content_binding"]["unbound_total"], 7,
                         "夹具前提：这一版有 7 项包材缺口（Spec §3）")
        self.assertEqual(self.binding_of(built), self.binding_of(loaded),
                         "算完那一份绑定账读回来必须逐字相同（Spec §C2）："
                         "现在 `unbound_total` / `unbound_codes` 重开就归零，"
                         "「被降级披露的包材项」那一行也就没了")

    def test_a2_estimate_row_carries_the_binding(self):
        self.prepare()
        built = self.build_cost()
        rows = self.db_rows("SELECT * FROM wip_packaging_cost_estimate")
        self.assertTrue(rows, "夹具前提：成本单已落库")
        row = rows[0]
        self.assertIn("content_binding_json", row,
                      "落库那一行必须有 `content_binding_json` 这一列（Spec §C1）")
        stored = json.loads(row["content_binding_json"] or "{}")
        self.assertEqual(self.binding_of(built), {key: stored.get(key) for key in BINDING_KEYS},
                         "存的就是算出来的那一份，不许换个形状（Spec §C1）")

    def test_a3_readiness_keeps_the_why_sentence(self):
        self.prepare()
        built = self.build_cost()
        loaded = self.load_cost()
        written = [row for row in ((built.get("readiness") or {}).get("reasons") or [])
                   if HEADLINE_FRAGMENT in row]
        self.assertTrue(written, "夹具前提：算完那一趟 reasons 里有「%s…」（Spec §3）"
                                 % HEADLINE_FRAGMENT)
        self.assertEqual((built.get("readiness") or {}).get("unbound_total"),
                         (loaded.get("readiness") or {}).get("unbound_total"),
                         "就绪门的 `unbound_total` 读回来必须与算完一致（Spec §C4）")
        read_back = [row for row in ((loaded.get("readiness") or {}).get("reasons") or [])
                     if HEADLINE_FRAGMENT in row]
        self.assertTrue(read_back,
                        "「为什么这些包材缺口没进阻断」这句读回来还得在（Spec §C4）："
                        "现在重开成本单它就不见了")


# --------------------------------------------------------------------------- #
# B 组：读侧回放与老成本单（假仓库行）
# --------------------------------------------------------------------------- #
class BReplay(unittest.TestCase):
    def test_b1_five_keys_are_replayed(self):
        out = _load_cost(_stored_row(STORED_BINDING))["content_binding"]
        for key in BINDING_KEYS:
            self.assertIn(key, out, "五个键必须都在（Spec §C2）：%s" % key)
        self.assertEqual(STORED_BINDING, out, "回放的就是存的那一份（Spec §C2）")

    def test_b2_legacy_estimate_reports_no_source_not_none(self):
        out = _load_cost(_stored_row())["content_binding"]
        for key in BINDING_KEYS:
            self.assertIn(key, out, "老成本单五个键也必须在（Spec §C3）：%s" % key)
        self.assertEqual("", out["source"],
                         "老成本单没记过来源 → 报空（Spec §C3）：「没有权威数据源」是算的那一刻的结论，"
                         "老成本单不知道这件事")
        self.assertNotIn(out["source"], ("none", "authoritative"),
                         "不许拿现数据源的答案顶替（Spec §C3）")
        self.assertEqual(0, out["unbound_total"], "取不到就是 0，不许编（Spec §C3）")
        self.assertEqual([], out["unbound_codes"], "取不到就是空清单（Spec §C3）")

    def test_b3_unreadable_column_is_not_none_either(self):
        for bad in ("{}", "not json", "[]", "7"):
            row = _stored_row()
            row["content_binding_json"] = bad
            out = _load_cost(row)["content_binding"]
            self.assertEqual("", out["source"], "解不出 / 不是对象 → 同样报空（Spec §C3）：%r" % bad)
            self.assertEqual(0, out["unbound_total"], "取不到就是 0（Spec §C3）：%r" % bad)

    def test_b4_read_side_does_not_ask_today_source(self):
        stub = mock.Mock(return_value={"codes": {"PKG-CT-DIVIDER"}, "source": "authoritative"})
        with _Patch((cost, "bound_content_codes_detail", stub)):
            out = _load_cost(_stored_row(STORED_BINDING))["content_binding"]
        self.assertEqual(0, stub.call_count,
                         "读侧不许再问当前的数据源（Spec §C2）：读的就是算时那一份，"
                         "与 `source_versions` 同一条纪律")
        self.assertEqual(STORED_BINDING, out, "回放不受当前数据源影响（Spec §C2）")


# --------------------------------------------------------------------------- #
# C 组：就绪门
# --------------------------------------------------------------------------- #
class CReadiness(unittest.TestCase):
    def test_c1_replayed_count_shows_up(self):
        out = _load_cost(_stored_row(STORED_BINDING))["readiness"]
        self.assertEqual(7, out.get("unbound_total"),
                         "读回来的成本单也要报出「几条包材缺口只披露不阻断」（Spec §C4）")
        self.assertTrue([row for row in (out.get("reasons") or [])
                         if HEADLINE_FRAGMENT in row],
                        "那句解释必须回来（Spec §C4）")

    def test_c2_stored_gap_rows_still_win(self):
        out = cost.packaging_cost_readiness_gate(
            {"built": True, "gaps": [],
             "gaps_unbound_to_order": [dict(UNBOUND_GAP), dict(UNBOUND_GAP), dict(UNBOUND_GAP)],
             "content_binding": {"source": "none", "unbound_total": 9}})
        self.assertEqual(3, out.get("unbound_total"),
                         "`gaps_unbound_to_order` 在位时以它的条数为准（既有口径，Spec §C4）")

    def test_c3_legacy_estimate_does_not_claim_a_missing_source(self):
        out = _load_cost(_stored_row())["readiness"]
        self.assertEqual(0, out.get("unbound_total"), "取不到就是 0（Spec §C4）")
        self.assertFalse([row for row in (out.get("reasons") or [])
                          if HEADLINE_FRAGMENT in row and "0 条" not in row],
                         "老成本单不知道有几条，不许凭空说「N 条只披露不阻断」（Spec §C3 / §C4）")


# --------------------------------------------------------------------------- #
# D 组：冻结面
# --------------------------------------------------------------------------- #
class DFreeze(unittest.TestCase):
    def test_d1_shape_is_unchanged(self):
        for row in (_stored_row(STORED_BINDING), _stored_row()):
            out = _load_cost(row)["content_binding"]
            self.assertEqual(set(BINDING_KEYS), set(out),
                             "键集合逐字不变（Spec §C5）")
        stored = _load_cost(_stored_row(STORED_BINDING))["content_binding"]
        self.assertIsInstance(stored["bound_total"], int)
        self.assertIsInstance(stored["unbound_total"], int)
        self.assertIsInstance(stored["bound_codes"], list)
        self.assertIsInstance(stored["unbound_codes"], list)

    def test_d2_write_side_closed_set_is_unchanged(self):
        detail = cost.bound_content_codes_detail({})
        self.assertEqual("none", detail.get("source"),
                         "写侧今天仍老实报 none（Spec §C5）")
        self.assertEqual(set(), set(detail.get("codes") or ()), "今天仍没有权威数据源（Spec §C5）")
        self.assertEqual(set(), cost.bound_content_codes({}), "兼容包装行为不变（Spec §C5）")

    def test_d3_schema_and_migration_agree(self):
        self.assertIn(("wip_packaging_cost_estimate", "content_binding_json", "TEXT"),
                      da_db._ADDED_COLUMNS,
                      "老库要靠 ALTER TABLE 补这一列（Spec §C1）")
        schema = (ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql").read_text(
            encoding="utf-8")
        at = schema.index("CREATE TABLE IF NOT EXISTS wip_packaging_cost_estimate")
        block = schema[at:schema.index(");", at)]
        self.assertIn("content_binding_json", block,
                      "新库的建表语句也要有这一列（Spec §C1）")
        self.assertIn("content_binding_json", inspect.getsource(da_repo.save_packaging_cost),
                      "写侧要走同一处显式键（Spec §C1）")

    def test_d4_frontend_is_not_touched(self):
        js = (ROOT / "tech_app" / "frontend" / "requirement-confirm.js").read_text(encoding="utf-8")
        self.assertNotIn("content_binding_json", js, "前端不该看见这一列（Spec §C3）")
        self.assertIn("'none' || raw === 'authoritative'", js,
                      "既有闭集（none / authoritative）不动（Spec §C3）")

    def test_d5_other_keys_are_verbatim(self):
        out = _load_cost(_stored_row(STORED_BINDING))
        self.assertIs(True, out.get("built"), "既有键逐字不变")
        self.assertEqual(1234.5, out.get("total_cost"), "既有键逐字不变")
        self.assertEqual("PE1", out.get("computed_by"), "既有键逐字不变")


if __name__ == "__main__":
    unittest.main()
