"""红测：差异价费率权威化、出价守卫与工作台触发点 —— 逆向快速报价第 8 批。

Spec：`docs/specs/quick-quote-8-rate-authority.md`
依赖：批 1 / 批 3 / 批 4（`cpq_quick_quote_case.py`、`cpq_quick_quote_workspace.py`、
`cpq_quick_quote_price.py` 都已实现）。

现状缺口（2026-09-21 实测，不是推断）：
  · `cpq_kb.kb_quick_quote_delta_rule` 里 4 条费率全是 `source_type=demo` / `review_status=draft`；
  · `cpq_quick_quote_workspace.py` 只有 `_rule_note()` 的**文本**告警，没有 `rule_authority()` /
    `authority_summary()`，`delta_price()` 行里也没有权威字段；
  · `cpq_quick_quote_price.py` 的 `price()` 出参没有费率权威段，`save()` 会把演示费率的价
    当成正式报价版本落库；
  · `tech_app/frontend/quick-quote-panel.js` 没有 `renderQuote()`，也没有出价 / 转精准按钮。

纪律：
  · 全部离线：规则 / 基准 / 工作区一律注入，`cpq_wf` 一律 mock，不连库、不调模型、不写数据；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WORKSPACE_PY = ROOT / "cpq_quick_quote_workspace.py"
PRICE_PY = ROOT / "cpq_quick_quote_price.py"
DEPLOY_MD = ROOT / "DEPLOYMENT.md"
PANEL_JS = ROOT / "tech_app" / "frontend" / "quick-quote-panel.js"

WS = "cpq_quick_quote_workspace"
PR = "cpq_quick_quote_price"
SALES = {"user_id": "100", "username": "sales1", "role_code": "sales_mgr"}
TODAY = dt.date(2026, 9, 21)

RATE_AUTHORITY_REASONS = ("ok", "demo_rate", "not_reviewed",
                          "source_not_authoritative", "no_source")
QUOTE_ACTIONS = ("save_quote", "transfer_precise")

BASE_VALUES = {
    "box_type": "YT-RB-01001-A", "box_family": "01天地盖", "closure_type": "磁吸",
    "insert_type": "EVA内托",
    "inner_length": 200.0, "inner_width": 150.0, "inner_height": 80.0, "fit_clearance": 1.5,
    "grey_board_gsm": 1200.0, "face_paper_gsm": 200.0, "material_code": "MAT-FACE-157",
    "print_colors": "CMYK", "lamination": True, "hot_stamping": False, "v_groove": True,
    "magnet": True, "window": False, "ribbon": False, "quantity": 5000.0,
    "tooling_fee_amount": 800.0, "freight_amount": 0.0,
}

BASELINE = {
    "engine_version": "quick_quote_case_match_v1",
    "case_code": "QQ-2026-0001", "case_version": 3,
    "case_snapshot": dict(BASE_VALUES, case_code="QQ-2026-0001",
                          review_status="reviewed", source_type="workbook",
                          quote_date="2026-06-01", standard_price=9.0),
    "selected_by": dict(SALES), "selected_at": "2026-09-21T10:00:00",
    "base_price": 9.0, "base_currency": "CNY", "base_tax_included": False,
    "base_quantity": 5000.0, "base_unit_price": 9.0,
    "source_type": "workbook", "review_status": "reviewed", "valid_until": "2027-03-01",
}

USER_EDITS = {"quantity": 3000, "face_paper_gsm": 250, "hot_stamping": True,
              "inner_length": 210}

AUTHORITATIVE_RULES = (
    {"rule_code": "QQQ-QTY-BAND", "field_key": "quantity", "rule_kind": "band", "unit": "元",
     "breakpoints_json": "[[0,1.15],[1000,1.00],[3000,0.95],[6000,0.92],[10000,0.85]]",
     "industry": "packaging", "source_type": "workbook", "review_status": "reviewed",
     "version": 2, "effective_from": "2026-01-01", "effective_to": ""},
    {"rule_code": "QQQ-PAPER-RATE", "field_key": "face_paper_gsm", "rule_kind": "rate",
     "unit": "元/g/m²", "rate": 0.0062, "industry": "packaging", "source_type": "workbook",
     "review_status": "reviewed", "version": 4, "effective_from": "2026-01-01",
     "effective_to": ""},
    {"rule_code": "QQQ-HOTSTEP", "field_key": "hot_stamping", "rule_kind": "step",
     "unit": "元", "amount": 0.18, "step_size": 1.0, "industry": "packaging",
     "source_type": "workbook", "review_status": "reviewed", "version": 1,
     "effective_from": "2026-01-01", "effective_to": ""},
    {"rule_code": "QQQ-LEN-RATE", "field_key": "inner_length", "rule_kind": "rate",
     "unit": "元/mm", "rate": 0.012, "industry": "packaging", "source_type": "workbook",
     "review_status": "reviewed", "version": 3, "effective_from": "2026-01-01",
     "effective_to": ""},
    {"rule_code": "QQQ-TOOLING-DIRECT", "field_key": "tooling_fee_amount",
     "rule_kind": "direct", "unit": "元", "amount": 1.0, "industry": "packaging",
     "source_type": "workbook", "review_status": "reviewed", "version": 1,
     "effective_from": "2026-01-01", "effective_to": ""},
)

#: 线上那份**演示费率**（`source_type=demo` / `review_status=draft`）的等价物。
DEMO_RULES = tuple(dict(row, source_type="demo", review_status="draft")
                   for row in AUTHORITATIVE_RULES)


def load(name):
    try:
        return importlib.import_module(name)
    except Exception:                                          # noqa: BLE001
        return None


class Base(unittest.TestCase):
    maxDiff = None

    def ws(self):
        module = load(WS)
        if module is None:
            self.fail("cpq_quick_quote_workspace.py 不存在（批 3）")
        return module

    def pr(self):
        module = load(PR)
        if module is None:
            self.fail("cpq_quick_quote_price.py 不存在（批 4）")
        return module

    def workspace(self, edits=None, *, rules=None):
        module = self.ws()
        ws = module.new_workspace(copy.deepcopy(BASELINE), user=SALES)
        if edits:
            ws = module.apply_edits(ws, edits, source="workspace", user=SALES,
                                    rules=rules or AUTHORITATIVE_RULES)
        return ws


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANaming(Base):
    def test_a1_closed_sets(self):
        module = self.ws()
        self.assertEqual(RATE_AUTHORITY_REASONS, tuple(module.RATE_AUTHORITY_REASONS))
        self.assertEqual(("workbook",), tuple(module.AUTHORITATIVE_RATE_SOURCES),
                         "只有权威工作簿费率能用于正式报价（Spec 批 8 §2.1）")

    def test_a2_labels_cover_all_codes(self):
        module = self.ws()
        labels = self.meta(module, "RATE_AUTHORITY_LABELS")
        self.assertEqual(set(RATE_AUTHORITY_REASONS), set(labels))
        for code, label in labels.items():
            self.assertTrue(str(label).strip(), "%s 缺中文标签" % code)

    def meta(self, module, name):
        self.assertTrue(hasattr(module, name), "%s.%s 缺失（Spec 批 8 §2.1）" % (WS, name))
        return getattr(module, name)

    def test_a3_public_callables(self):
        module = self.ws()
        for name in ("rule_authority", "authority_summary"):
            self.assertTrue(callable(getattr(module, name, None)),
                            "%s.%s() 缺失（Spec 批 8 §2.1）" % (WS, name))
        self.assertTrue(callable(getattr(self.pr(), "is_formal", None)),
                        "%s.is_formal() 缺失（Spec 批 8 §2.2）" % PR)

    def test_a4_quote_actions_closed_set(self):
        self.assertTrue(PANEL_JS.exists(), "quick-quote-panel.js 不存在")
        text = PANEL_JS.read_text(encoding="utf-8")
        self.assertIn("QUOTE_ACTIONS", text)
        for action in QUOTE_ACTIONS:
            self.assertIn(action, text, "前端缺动作 %s（Spec 批 8 §2.3）" % action)


# --------------------------------------------------------------------------- #
# B 组：rule_authority
# --------------------------------------------------------------------------- #
class TestBRuleAuthority(Base):
    def authority(self, rule):
        return self.ws().rule_authority(rule)

    def test_b1_demo_is_not_authoritative(self):
        out = self.authority(DEMO_RULES[0])
        self.assertFalse(out["authoritative"])
        self.assertEqual("demo_rate", out["reason_code"])
        self.assertIn("演示", out["reason"])

    def test_b2_not_reviewed(self):
        row = dict(AUTHORITATIVE_RULES[0], review_status="draft")
        out = self.authority(row)
        self.assertEqual("not_reviewed", out["reason_code"])
        self.assertFalse(out["authoritative"])

    def test_b3_non_workbook_source(self):
        row = dict(AUTHORITATIVE_RULES[0], source_type="dwg_confirmed")
        out = self.authority(row)
        self.assertEqual("source_not_authoritative", out["reason_code"])

    def test_b4_missing_source(self):
        for bad in ({}, None, {"rule_code": ""}):
            self.assertEqual("no_source", self.authority(bad)["reason_code"])

    def test_b5_authoritative(self):
        out = self.authority(AUTHORITATIVE_RULES[0])
        self.assertTrue(out["authoritative"])
        self.assertEqual("ok", out["reason_code"])

    def test_b6_demo_wins_over_not_reviewed(self):
        out = self.authority(DEMO_RULES[0])
        self.assertEqual("demo_rate", out["reason_code"],
                         "demo 要先于未审核报出来（否则会去补审核、绕过来源）")


# --------------------------------------------------------------------------- #
# C 组：authority_summary
# --------------------------------------------------------------------------- #
class TestCSummary(Base):
    def summary(self, rules):
        return self.ws().authority_summary(rules=rules)

    def test_c1_demo_rules_block_formal(self):
        out = self.summary(DEMO_RULES)
        self.assertEqual(len(DEMO_RULES), out["rule_total"])
        self.assertEqual(0, out["authoritative_total"])
        self.assertFalse(out["authoritative"])
        self.assertIn("演示数据", out["headline"])
        self.assertIn("权威", out["detail"])
        self.assertEqual(["demo_rate"], [row["reason_code"] for row in out["blocked_by"]])
        self.assertEqual(sorted(row["rule_code"] for row in DEMO_RULES),
                         out["blocked_by"][0]["codes"])

    def test_c2_authoritative_rules_are_ready(self):
        out = self.summary(AUTHORITATIVE_RULES)
        self.assertTrue(out["authoritative"])
        self.assertEqual([], out["blocked_by"])

    def test_c3_grouping_is_deterministic(self):
        rules = (dict(DEMO_RULES[0]),
                 dict(AUTHORITATIVE_RULES[0], review_status="draft",
                      rule_code="QQQ-A-DRAFT"),
                 dict(AUTHORITATIVE_RULES[0], review_status="draft",
                      rule_code="QQQ-B-DRAFT"))
        out = self.summary(rules)
        self.assertEqual(["not_reviewed", "demo_rate"],
                         [row["reason_code"] for row in out["blocked_by"]],
                         "count 降序 → reason_code 升序（Spec §2.1）")
        self.assertEqual([2, 1], [row["count"] for row in out["blocked_by"]])

    def test_c4_blocks_carry_label_and_codes(self):
        for row in self.summary(DEMO_RULES)["blocked_by"]:
            for key in ("reason_code", "label", "count", "codes"):
                self.assertIn(key, row)
            self.assertTrue(str(row["label"]).strip())


# --------------------------------------------------------------------------- #
# D 组：delta_price 行
# --------------------------------------------------------------------------- #
class TestDDeltaRows(Base):
    def rows(self, rules):
        module = self.ws()
        ws = self.workspace(USER_EDITS, rules=rules)
        return ws, module.diff_table(ws, rules=rules)

    def test_d1_rows_carry_rate_authority(self):
        _, rows = self.rows(DEMO_RULES)
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("rate_authority", row,
                          "差异项行必须带费率权威段（Spec §2.1）")
        codes = {row["rate_authority"]["reason_code"] for row in rows}
        self.assertIn("demo_rate", codes)

    def test_d2_existing_keys_are_kept(self):
        _, rows = self.rows(DEMO_RULES)
        for row in rows:
            for key in ("field_key", "label", "priced", "delta", "rule_code",
                        "rule_version", "display_base", "display_current"):
                self.assertIn(key, row, "既有键 %s 不许动（Spec §2.1）" % key)


# --------------------------------------------------------------------------- #
# E 组：price() 与 is_formal()
# --------------------------------------------------------------------------- #
class TestEPrice(Base):
    def price(self, rules):
        pr = self.pr()
        ws = self.workspace(USER_EDITS, rules=rules)
        return pr.price(copy.deepcopy(BASELINE), ws, today=TODAY, rules=rules)

    def test_e1_quote_carries_rate_authority(self):
        quote = self.price(DEMO_RULES)
        self.assertIn("rate_authority", quote, "出价段必须有费率权威段（Spec §2.2）")
        self.assertFalse(quote["rate_authority"]["authoritative"])

    def test_e2_demo_rate_warning_is_visible(self):
        quote = self.price(DEMO_RULES)
        joined = " ".join(str(item) for item in quote.get("warnings") or [])
        self.assertIn("演示数据", joined,
                      "按演示费率出的价必须在 warnings 里说明（Spec §2.2）")

    def test_e3_authoritative_rules_have_no_demo_warning(self):
        quote = self.price(AUTHORITATIVE_RULES)
        joined = " ".join(str(item) for item in quote.get("warnings") or [])
        self.assertNotIn("演示数据", joined)
        self.assertTrue(quote["rate_authority"]["authoritative"])

    def test_e4_is_formal_reflects_authority(self):
        pr = self.pr()
        self.assertFalse(pr.is_formal(self.price(DEMO_RULES)))
        self.assertTrue(pr.is_formal(self.price(AUTHORITATIVE_RULES)))

    def test_e5_price_math_unchanged(self):
        quote = self.price(DEMO_RULES)
        self.assertAlmostEqual(9.88, float(quote["unit_price"]), places=6,
                               msg="本批不许动差异价口径（Spec §2.4）")


# --------------------------------------------------------------------------- #
# F 组：save() 守卫
# --------------------------------------------------------------------------- #
class TestFSaveGuard(Base):
    def test_f1_formal_save_is_rejected_for_demo_rates(self):
        pr = self.pr()
        ws = self.workspace(USER_EDITS, rules=DEMO_RULES)
        quote = pr.price(copy.deepcopy(BASELINE), ws, today=TODAY, rules=DEMO_RULES)
        with mock.patch.object(pr.cpq_wf, "merge_step_snapshot") as merge:
            with self.assertRaises(pr.QuickQuoteError) as ctx:
                pr.save(quote, user=SALES, session_id="sess-1", formal=True)
            merge.assert_not_called()
        message = str(ctx.exception)
        self.assertIn("费率", message)
        self.assertIn("权威", message)

    def test_f2_default_save_marks_the_snapshot_as_trial(self):
        pr = self.pr()
        ws = self.workspace(USER_EDITS, rules=DEMO_RULES)
        quote = pr.price(copy.deepcopy(BASELINE), ws, today=TODAY, rules=DEMO_RULES)
        captured = {}

        def fake_merge(session_id, step, payload):
            captured.update(payload)
            return {"session_id": session_id}

        with mock.patch.object(pr.cpq_wf, "merge_step_snapshot", side_effect=fake_merge):
            out = pr.save(quote, user=SALES, session_id="sess-1")
        self.assertTrue(out["ok"])
        seg = captured.get(pr.QUOTE_KEY) or {}
        self.assertIn("rate_authority", seg, "落库快照必须带费率权威段（Spec §2.2）")
        self.assertFalse(seg.get("formal"), "演示费率的价只能落成试算")

    def test_f3_formal_save_succeeds_for_authoritative_rates(self):
        pr = self.pr()
        ws = self.workspace(USER_EDITS, rules=AUTHORITATIVE_RULES)
        quote = pr.price(copy.deepcopy(BASELINE), ws, today=TODAY, rules=AUTHORITATIVE_RULES)
        captured = {}

        def fake_merge(session_id, step, payload):
            captured.update(payload)
            return {"session_id": session_id}

        with mock.patch.object(pr.cpq_wf, "merge_step_snapshot", side_effect=fake_merge):
            out = pr.save(quote, user=SALES, session_id="sess-1", formal=True)
        self.assertTrue(out["ok"])
        self.assertTrue((captured.get(pr.QUOTE_KEY) or {}).get("formal"))


# --------------------------------------------------------------------------- #
# G 组：前端
# --------------------------------------------------------------------------- #
class TestGFrontend(Base):
    def panel(self):
        return PANEL_JS.read_text(encoding="utf-8")

    def test_g1_render_quote_exported(self):
        text = self.panel()
        self.assertIn("function renderQuote", text, "前端必须有 renderQuote(quote)（Spec §2.3）")
        export = text[text.find("global.QuickQuotePanel"):]
        self.assertIn("renderQuote", export)

    def test_g2_nodes_and_actions(self):
        text = self.panel()
        self.assertIn("data-qq-quote", text)
        self.assertIn("data-qq-rate-authority", text)
        self.assertIn("data-qq-warning", text)
        self.assertIn('"trial"', text, "非权威费率必须标成 trial（Spec §2.3）")

    def test_g3_demo_warning_is_not_hidden(self):
        text = self.panel()
        marker = text.find("renderQuote")
        window = text[marker:marker + 4000]
        for token in ("warnings", "data-qq-warning"):
            self.assertIn(token, window, "演示费率的告警必须渲染出来（Spec §2.3）")

    def test_g4_quote_actions_constant(self):
        text = self.panel()
        self.assertIn('"save_quote"', text)
        self.assertIn('"transfer_precise"', text)


# --------------------------------------------------------------------------- #
# H 组：部署文档
# --------------------------------------------------------------------------- #
class TestHDeployment(Base):
    def test_h1_documents_rate_authority_rollout(self):
        text = DEPLOY_MD.read_text(encoding="utf-8", errors="replace")
        self.assertIn("kb_quick_quote_delta_rule", text)
        marker = text.find("kb_quick_quote_delta_rule")
        window = text[max(0, marker - 2000):marker + 3000]
        self.assertIn("demo", window)
        self.assertIn("workbook", window, "要写清权威口径是 workbook + reviewed（Spec §2.4）")
        self.assertIn("reviewed", window)


if __name__ == "__main__":
    unittest.main(verbosity=2)
