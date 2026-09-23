"""红测：案例库的维护写路径（补字段 / 审核 / 停用）与面板动作接线 —— 逆向快速报价第 12 批。

Spec：`docs/specs/quick-quote-12-case-maintenance.md`
依赖：批 1（案例模型与准入）、批 6（`library_readiness()` / `case_fix_plan()`）、
批 8（`cpq_quick_quote_price.WRITE_ROLES`）。

现状缺口（2026-09-21 实测，不是推断）：
  · `cpq_quick_quote_case.py` 没有 `case_edit_patch()` / `case_review_patch()` /
    `case_write_allowed()`，也没有 `CASE_MAINTENANCE_ERRORS` / `CASE_TRANSITIONS`；
  · `cpq_agent_server.py` 只有 `GET /api/quick-quote/cases`，没有任何写路由；
  · `tech_app/frontend/quick-quote-panel.js` 的 `renderReadiness()` 画了
    `fill_case_fields` / `review_case` 两个按钮，但 `open()` 没有 `onAction`，
    两个动作没有处理分支 → 点下去不发任何请求；
  · `报价首页.html` 的 `openQuickQuotePanel()` 只传 `onPrecise`，没有 `onAction`；
  · 案例库里 2 条 `draft` 案例（`QQ-YT-DWG-WINE-700ML` / `QQ-YT-DWG-ROUND-10PC`）
    缺 `standard_price` → `eligible_total = 0`，而产品里没有把这两个数字补上的路径。

纪律：
  · 全部离线：不连 PG、不真发 HTTP、不调模型、不写业务数据；
  · 库访问一律 `mock`，纯函数用内存字典驱动；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import datetime as dt
import importlib
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASE_PY = ROOT / "cpq_quick_quote_case.py"
SERVER_PY = ROOT / "cpq_agent_server.py"
PANEL_JS = ROOT / "tech_app" / "frontend" / "quick-quote-panel.js"
HOME_HTML = ROOT / "报价首页.html"
MODULE_NAME = "cpq_quick_quote_case"
TODAY = dt.date(2026, 9, 21)

SALES = {"user_id": "100", "username": "sales1", "role_code": "sales_mgr"}
VIEWER = {"user_id": "101", "username": "guest", "role_code": "viewer"}

EXPECTED_ERRORS = ("unknown_field", "immutable_field", "invalid_value",
                   "illegal_transition", "reason_required", "case_not_found")
EXPECTED_IMMUTABLE = ("case_code", "case_version", "version")
EXPECTED_FIELDS_PATH = "/api/quick-quote/cases/{case_code}/fields"
EXPECTED_REVIEW_PATH = "/api/quick-quote/cases/{case_code}/review"


def load_case_module():
    return importlib.import_module(MODULE_NAME)


def draft_case(**over):
    """两份 DWG 实样沉淀出来的案例形状（`draft` + 没价 → 现在不可用）。"""
    case = {
        "case_code": "QQ-YT-DWG-WINE-700ML", "case_version": 1, "customer_masked": "",
        "box_type_code": "YT-RB-01001-A", "box_family": "02书型盒",
        "closure_type": "磁吸", "insert_type": "EVA内托", "fit_clearance": 1.5,
        "magnet": True, "ribbon": True, "window": False, "v_groove": True,
        "inner_length": 200.0, "inner_width": 150.0, "inner_height": 80.0,
        "grey_board_gsm": 1200.0, "face_paper_gsm": 235.0,
        "print_colors": "CMYK", "lamination": True, "hot_stamping": True,
        "quantity_tiers": "3000/5000/10000",
        "standard_cost": 0.0, "standard_price": 0.0, "deal_price": 0.0,
        "currency": "CNY", "tax_included": False,
        "quote_date": "2026-08-01", "valid_from": "2026-08-01",
        "source_type": "dwg_confirmed", "source_ref": "酒盒.dwg",
        "review_status": "draft", "version": 1, "industry": "packaging",
    }
    case.update(over)
    return case


def second_case(**over):
    case = draft_case(case_code="QQ-YT-DWG-ROUND-10PC", box_family="01天地盖",
                      closure_type="天地盖", inner_length=210.0, inner_width=210.0,
                      inner_height=60.0, source_ref="圆盘盒.dwg")
    case.update(over)
    return case


class Base(unittest.TestCase):
    def module(self):
        try:
            return load_case_module()
        except ImportError:                                       # pragma: no cover
            self.fail("%s.py 不存在（Spec 批 12）" % MODULE_NAME)

    def attr(self, name, hint):
        module = self.module()
        self.assertTrue(hasattr(module, name),
                        "%s.%s 缺失（%s）" % (MODULE_NAME, name, hint))
        return getattr(module, name)

    def fn(self, name, hint):
        value = self.attr(name, hint)
        self.assertTrue(callable(value), "%s.%s 必须可调用（%s）" % (MODULE_NAME, name, hint))
        return value

    def edit_patch(self, case, values, **kw):
        return self.fn("case_edit_patch", "Spec 批 12 §2.2")(case, values, **kw)

    def review_patch(self, case, status, **kw):
        kw.setdefault("reviewer", "PE1")
        kw.setdefault("today", TODAY)
        return self.fn("case_review_patch", "Spec 批 12 §2.3")(case, status, **kw)

    def server_source(self):
        self.assertTrue(SERVER_PY.exists(), "cpq_agent_server.py 不存在")
        return SERVER_PY.read_text(encoding="utf-8")

    def panel_source(self):
        self.assertTrue(PANEL_JS.exists(), "quick-quote-panel.js 不存在")
        return PANEL_JS.read_text(encoding="utf-8")

    def codes(self, result):
        return [row.get("reason_code") for row in result.get("blocked") or []]


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANaming(Base):
    def test_a1_error_codes_closed_set(self):
        self.assertEqual(EXPECTED_ERRORS, tuple(self.attr("CASE_MAINTENANCE_ERRORS",
                                                         "Spec 批 12 §2.1")))

    def test_a2_editable_fields_is_case_fields_minus_identity(self):
        module = self.module()
        self.assertEqual(EXPECTED_IMMUTABLE, tuple(self.attr("CASE_IMMUTABLE_FIELDS",
                                                             "Spec 批 12 §2.1")))
        expected = tuple(k for k in module.CASE_FIELDS if k not in EXPECTED_IMMUTABLE)
        self.assertEqual(expected, tuple(self.attr("CASE_EDITABLE_FIELDS", "Spec 批 12 §2.1")),
                         "可改字段必须由 CASE_FIELDS 推导，不许另抄一份清单")

    def test_a3_transitions_are_a_closed_set_over_review_statuses(self):
        module = self.module()
        transitions = self.attr("CASE_TRANSITIONS", "Spec 批 12 §2.1")
        self.assertEqual(set(module.CASE_REVIEW_STATUSES), set(transitions),
                         "CASE_TRANSITIONS 的键集必须等于 CASE_REVIEW_STATUSES")
        for source, targets in transitions.items():
            for target in targets:
                self.assertIn(target, module.CASE_REVIEW_STATUSES,
                              "状态机里出现了闭集外的状态：%s → %s" % (source, target))
        self.assertEqual((), tuple(transitions["retired"]),
                         "已停用是终态，不许有任何出边（Spec §2.3 第 2 条）")
        self.assertEqual(("draft", "retired"), tuple(self.attr("CASE_REASON_REQUIRED",
                                                               "Spec 批 12 §2.1")))

    def test_a4_two_write_route_templates(self):
        self.assertEqual(EXPECTED_FIELDS_PATH,
                         self.attr("QUICK_QUOTE_CASE_FIELDS_PATH", "Spec 批 12 §2.1"))
        self.assertEqual(EXPECTED_REVIEW_PATH,
                         self.attr("QUICK_QUOTE_CASE_REVIEW_PATH", "Spec 批 12 §2.1"))


# --------------------------------------------------------------------------- #
# B 组：局部补字段
# --------------------------------------------------------------------------- #
class TestBEditPatch(Base):
    def test_b1_price_patch_carries_label_and_before_after(self):
        out = self.edit_patch(draft_case(), {"standard_price": 23.4}, today=TODAY)
        self.assertEqual({"standard_price": 23.4}, out.get("patch"),
                         "补价必须落进 patch（Spec §2.2）")
        self.assertEqual([], out.get("blocked"))
        changed = out.get("changed") or []
        self.assertEqual(1, len(changed))
        self.assertEqual("standard_price", changed[0].get("field"))
        self.assertEqual(0.0, changed[0].get("before"))
        self.assertEqual(23.4, changed[0].get("after"))
        self.assertEqual("标准单价", changed[0].get("label"))

    def test_b2_unknown_field_is_blocked_but_valid_fields_still_patch(self):
        out = self.edit_patch(draft_case(), {"standard_price": 18.0, "no_such_field": 1},
                              today=TODAY)
        self.assertIn("unknown_field", self.codes(out))
        self.assertEqual({"standard_price": 18.0}, out.get("patch"),
                         "单个非法键不许拖掉整批合法键（Spec §2.2 第 7 条）")

    def test_b3_identity_columns_are_immutable_not_unknown(self):
        values = {"case_code": "QQ-OTHER", "case_version": 9, "version": 7}
        out = self.edit_patch(draft_case(), values, today=TODAY)
        self.assertEqual(["immutable_field"] * 3, self.codes(out),
                         "身份列必须报 immutable_field（Spec §2.2 第 2 条）")
        self.assertEqual({}, out.get("patch"))

    def test_b4_value_domain_each_branch(self):
        bad = {"standard_price": -1.0, "inner_length": 0.0, "grey_board_gsm": -5,
               "currency": "rmb", "quote_date": "2026/08/01", "magnet": "yes"}
        out = self.edit_patch(draft_case(), bad, today=TODAY)
        self.assertEqual(sorted(bad), sorted(row.get("field") for row in out.get("blocked") or []))
        for code in self.codes(out):
            self.assertEqual("invalid_value", code)
        self.assertEqual({}, out.get("patch"))

    def test_b5_valid_from_after_valid_until_is_rejected(self):
        out = self.edit_patch(draft_case(), {"valid_from": "2026-09-01",
                                             "valid_until": "2026-08-01"}, today=TODAY)
        self.assertIn("invalid_value", self.codes(out),
                      "生效日晚于截止日必须拒（Spec §2.2 第 3 条）")

    def test_b6_same_value_is_idempotent(self):
        out = self.edit_patch(draft_case(), {"standard_price": 0.0, "currency": "CNY"},
                              today=TODAY)
        self.assertEqual({}, out.get("patch"))
        self.assertEqual([], out.get("changed"))
        self.assertEqual([], out.get("blocked"))

    def test_b7_patch_key_order_follows_case_fields(self):
        module = self.module()
        out = self.edit_patch(draft_case(), {"review_status": "draft", "industry": "packaging",
                                             "standard_price": 9.0, "inner_length": 210.0},
                              today=TODAY)
        keys = list(out.get("patch") or {})
        self.assertEqual([k for k in module.CASE_FIELDS if k in keys], keys,
                         "patch 键序必须按 CASE_FIELDS（确定性，Spec §2.2 第 5 条）")

    def test_b8_empty_values_do_not_raise(self):
        for empty in (None, {}):
            out = self.edit_patch(draft_case(), empty, today=TODAY)
            self.assertEqual({}, out.get("patch"))
            self.assertEqual([], out.get("changed"))
            self.assertEqual([], out.get("blocked"))


# --------------------------------------------------------------------------- #
# C 组：审核状态流转
# --------------------------------------------------------------------------- #
class TestCReviewPatch(Base):
    def test_c1_draft_to_reviewed_records_reviewer_and_date(self):
        out = self.review_patch(draft_case(), "reviewed", reviewer="PE1")
        self.assertEqual([], out.get("blocked"))
        patch = out.get("patch") or {}
        self.assertEqual("reviewed", patch.get("review_status"))
        self.assertEqual("PE1", patch.get("reviewed_by"))
        self.assertEqual("2026-09-21", patch.get("reviewed_at"),
                         "reviewed_at 必须取注入的 today（Spec §2.3 第 6 条）")
        self.assertEqual("review_status", (out.get("changed") or [{}])[0].get("field"))

    def test_c2_reviewed_to_draft_requires_reason(self):
        case = draft_case(review_status="reviewed")
        blocked = self.review_patch(case, "draft", reason="   ")
        self.assertEqual(["reason_required"], self.codes(blocked))
        ok = self.review_patch(case, "draft", reason="客户改口径，重新核价")
        self.assertEqual([], ok.get("blocked"))
        self.assertEqual("客户改口径，重新核价", (ok.get("patch") or {}).get("review_reason"))

    def test_c3_retired_is_terminal(self):
        case = draft_case(review_status="retired")
        for target in ("reviewed", "draft"):
            out = self.review_patch(case, target, reason="试试")
            self.assertEqual(["illegal_transition"], self.codes(out),
                             "已停用是终态：%s 必须非法（Spec §2.3 第 2 条）" % target)

    def test_c4_retire_requires_reason(self):
        blocked = self.review_patch(draft_case(), "retired", reason="")
        self.assertEqual(["reason_required"], self.codes(blocked))
        ok = self.review_patch(draft_case(), "retired", reason="盒型停产")
        self.assertEqual([], ok.get("blocked"))
        self.assertEqual("retired", (ok.get("patch") or {}).get("review_status"))

    def test_c5_same_status_is_idempotent(self):
        out = self.review_patch(draft_case(), "draft")
        self.assertEqual({}, out.get("patch"))
        self.assertEqual([], out.get("changed"))
        self.assertEqual([], out.get("blocked"))

    def test_c6_reviewer_is_required(self):
        for blank in ("", "   ", None):
            out = self.review_patch(draft_case(), "reviewed", reviewer=blank)
            self.assertEqual(["invalid_value"], self.codes(out))
            self.assertIn("审核人必填", (out.get("blocked") or [{}])[0].get("detail") or "")

    def test_c7_unknown_status_is_invalid(self):
        out = self.review_patch(draft_case(), "approved")
        self.assertEqual(["invalid_value"], self.codes(out))


# --------------------------------------------------------------------------- #
# D 组：资格回流（本批唯一验收口径）
# --------------------------------------------------------------------------- #
class TestDReadinessRecycle(Base):
    def test_d1_fill_price_and_review_makes_both_cases_eligible(self):
        module = self.module()
        readiness = self.fn("library_readiness", "Spec 批 6 §2.1")
        cases = [draft_case(), second_case()]
        before = readiness(cases, today=TODAY)
        self.assertEqual("no_eligible", before.get("verdict"))
        self.assertEqual(0, before.get("eligible_total"))

        updated = []
        for case in cases:
            fill = self.edit_patch(case, {"standard_price": 23.4}, today=TODAY)
            review = self.review_patch(case, "reviewed", reviewer="PE1")
            self.assertEqual([], list(fill.get("blocked") or []))
            self.assertEqual([], list(review.get("blocked") or []))
            fields = set(fill.get("patch") or {}) | set(review.get("patch") or {})
            self.assertIn("standard_price", fields)
            self.assertIn("review_status", fields)
            row = dict(case)
            row.update(fill.get("patch") or {})
            row.update(review.get("patch") or {})
            updated.append(row)

        after = readiness(updated, today=TODAY)
        self.assertEqual("ready", after.get("verdict"),
                         "补价 + 审核后必须判 ready（Spec §2.6）")
        self.assertEqual(2, after.get("eligible_total"))
        self.assertEqual([], list(after.get("blocked_by") or []))


# --------------------------------------------------------------------------- #
# E 组：服务端两条写路由
# --------------------------------------------------------------------------- #
class TestEServerRoutes(Base):
    def test_e1_server_registers_both_route_templates(self):
        module = self.module()
        server = importlib.import_module("cpq_agent_server")
        for name in ("QUICK_QUOTE_CASE_FIELDS_PATH", "QUICK_QUOTE_CASE_REVIEW_PATH"):
            self.assertTrue(hasattr(server, name) or name in self.server_source(),
                            "服务端没有引用 %s（Spec §2.4）" % name)
        self.assertEqual(module.QUICK_QUOTE_CASE_FIELDS_PATH, EXPECTED_FIELDS_PATH)
        self.assertEqual(module.QUICK_QUOTE_CASE_REVIEW_PATH, EXPECTED_REVIEW_PATH)

    def test_e2_action_regex_parses_case_code_and_action(self):
        server = importlib.import_module("cpq_agent_server")
        pattern = getattr(server, "QUICK_QUOTE_CASE_ACTION_RE", None)
        self.assertIsNotNone(pattern, "服务端缺 QUICK_QUOTE_CASE_ACTION_RE（Spec §2.4）")
        fields = pattern.match("/api/quick-quote/cases/QQ-YT-DWG-WINE-700ML/fields")
        review = pattern.match("/api/quick-quote/cases/QQ-YT-DWG-WINE-700ML/review")
        self.assertIsNotNone(fields, "fields 路由没匹配上")
        self.assertIsNotNone(review, "review 路由没匹配上")
        self.assertEqual("QQ-YT-DWG-WINE-700ML", (fields.groupdict() or {}).get("case_code"))

    def test_e3_write_permission_reuses_existing_closed_set(self):
        module = self.module()
        allowed = self.fn("case_write_allowed", "Spec §2.4")
        price = importlib.import_module("cpq_quick_quote_price")
        self.assertTrue(allowed(SALES), "sales_mgr 必须能维护案例")
        self.assertFalse(allowed(VIEWER), "viewer 不许改案例")
        self.assertFalse(allowed(None), "未登录不许改案例")
        src = CASE_PY.read_text(encoding="utf-8")
        self.assertIn("WRITE_ROLES", src,
                      "写权限必须复用既有闭集，不许新造一份（Spec §2.4）")

    def test_e4_handler_validates_before_writing(self):
        src = self.server_source()
        self.assertIn("_handle_quick_quote_case_write", src,
                      "服务端缺案例维护处理函数（Spec §2.4）")
        start = src.index("_handle_quick_quote_case_write")
        window = src[start:start + 4000]
        self.assertIn("blocked", window)
        self.assertIn("readiness", window, "写路由出参必须带 readiness（Spec §2.4）")
        self.assertIn("case_not_found", window, "找不到案例必须回 case_not_found")
        write_at = window.find("save_case(")
        blocked_at = window.find("blocked")
        self.assertTrue(write_at == -1 or blocked_at < write_at,
                        "先校验后写：blocked 判定必须出现在落库之前（Spec §2.4）")

    def test_e5_client_helper_posts_to_both_templates(self):
        src = self.panel_source()
        for name in ("CASE_FIELDS_PATH", "CASE_REVIEW_PATH"):
            self.assertIn(name, src, "面板缺 %s（Spec §2.5）" % name)
        self.assertNotIn('"/api/quick-quote/cases/', src.replace(
            'var CASES_PATH = "/api/quick-quote/cases";', ""),
            "面板不许再手写第二份路由字面量（Spec §2.5）")


# --------------------------------------------------------------------------- #
# F 组：前端动作接线
# --------------------------------------------------------------------------- #
class TestFPanelWiring(Base):
    def panel_const_path(self, src, name):
        """取面板常量 `var NAME = <expr>;` 的**求值结果**（只认字面量与 `CASES_PATH + 后缀`）。

        本用例原来要求 `var NAME = "<模板>";` 是**一份字面量赋值**，而同模块 E5 明令
        「面板不许再手写第二份 `/api/quick-quote/cases/...` 字面量」——两条结构上不可能同时成立
        （changelog `## 256` 已记录）。这里按 Spec §2.5 的**本意**（"值与后端模板同值"）
        改成对求值结果断言：`CASES_PATH + "/{case_code}/fields"` 这种拼接是允许的写法。
        """
        match = re.search(r"var\s+%s\s*=\s*([^;]+);" % name, src)
        if not match:
            return None
        literal = re.search(r'var\s+CASES_PATH\s*=\s*"([^"]+)"', src)
        cases_path = literal.group(1) if literal else None
        out = []
        for part in [item.strip() for item in match.group(1).split("+")]:
            if part == "CASES_PATH":
                if cases_path is None:
                    return None
                out.append(cases_path)
            elif len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
                out.append(part[1:-1])
            else:
                return None
        return "".join(out)

    def test_f1_panel_action_constants_match_backend(self):
        expected_by_name = {
            "CASE_FIELDS_PATH": self.attr("QUICK_QUOTE_CASE_FIELDS_PATH", "Spec 批 12 §2.1"),
            "CASE_REVIEW_PATH": self.attr("QUICK_QUOTE_CASE_REVIEW_PATH", "Spec 批 12 §2.1"),
        }
        src = self.panel_source()
        for name, expected in expected_by_name.items():
            value = self.panel_const_path(src, name)
            self.assertIsNotNone(value, "面板缺 %s 常量（Spec §2.5）" % name)
            self.assertEqual(expected, value, "%s 的求值结果必须与后端模板同值" % name)

    def test_f2_open_accepts_on_action(self):
        src = self.panel_source()
        self.assertIn("onAction", src, "面板 open() 必须支持 onAction（Spec §2.5）")
        self.assertIn("onCaseFill", src, "fill_case_fields 必须有处理分支")
        self.assertIn("onCaseReview", src, "review_case 必须有处理分支")

    def test_f3_pending_marker_when_no_callback(self):
        src = self.panel_source()
        self.assertIn("data-qq-action-pending", src,
                      "没有回调时必须标 data-qq-action-pending（Spec §2.5）")

    def test_f4_home_page_passes_on_action(self):
        self.assertTrue(HOME_HTML.exists(), "报价首页.html 不存在")
        html = HOME_HTML.read_text(encoding="utf-8")
        start = html.find("function openQuickQuotePanel")
        self.assertNotEqual(-1, start, "报价首页.html 缺 openQuickQuotePanel()")
        window = html[start:start + 900]
        self.assertIn("onAction", window,
                      "首页必须传 onAction（或 onCaseFill / onCaseReview），否则动作按钮是死按钮"
                      "（Spec §2.5）")


if __name__ == "__main__":                                        # pragma: no cover
    unittest.main()
