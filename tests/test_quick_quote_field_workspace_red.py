"""红测：字段工作区修改与差异价格计算 —— 逆向快速报价第 3 批。

Spec：`docs/specs/quick-quote-3-field-workspace-and-delta-price.md`
依赖：批 1（案例模型）、批 2（候选检索与 `build_baseline()` 基准快照）。
本批红测**假设前两批已实现**：前两批未落地时，相关用例会以「批 1/批 2 未实现」的原因失败。

现状缺口（实测，不是推断）：
  · `cpq_quick_quote_workspace.py` 不存在；
  · `cpq_kb.py:68` 没有 `kb_quick_quote_delta_rule`：面纸 +50g 贵多少、烫金加一项贵多少，
    这些口径全仓无处可查；
  · `确认需求解析结果.html` 的右侧工作台只有既有 6 步的 `render_form` / `render_table` 分区，
    **没有「参数 / 基准案例 / 当前报价 / 差异价格」四列对比表**（`grep -c 差异价格` → 0）；
  · Agent 侧没有确定性写字段的入口，没有「未确认修改不得落库」的约束，也没有逐项修改审计。

数值口径（Spec §2.2，与业务示例逐字对齐）：
  数量 5000 → 3000 = +0.27；面纸 200 → 250g = +0.31；烫金 无 → 有 = +0.18；内长 200 → 210mm = +0.12。
本文件用注入的规则行复现这四个数，**不读工作簿、不连库**。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写业务数据；
  · 规则一律注入（`rules=`）；库读不到只断言抛错；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import importlib
import json
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WORKSPACE_PY = ROOT / "cpq_quick_quote_workspace.py"
PANEL_JS = ROOT / "tech_app" / "frontend" / "quick-quote-panel.js"
WORKBENCH_HTML = ROOT / "确认需求解析结果.html"

import cpq_kb  # noqa: E402

WS_MODULE = "cpq_quick_quote_workspace"
WS_MISSING = "cpq_quick_quote_workspace.py 不存在（Spec 批 3 §2.1）"
CASE_MISSING = "cpq_quick_quote_case.py 不存在（Spec 批 1，批 3 依赖它）"
SALES = {"user_id": "100", "username": "sales1", "role_code": "sales_mgr"}
VIEWER = {"user_id": "300", "username": "viewer1", "role_code": "viewer"}

FIELD_KEYS = ("box_type", "box_family", "closure_type", "insert_type",
              "inner_length", "inner_width", "inner_height", "fit_clearance",
              "grey_board_gsm", "face_paper_gsm", "material_code",
              "print_colors", "lamination", "hot_stamping", "v_groove", "magnet",
              "window", "ribbon", "quantity", "tooling_fee_amount", "freight_amount")

RULES = (
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
    "case_snapshot": dict(BASE_VALUES, case_code="QQ-2026-0001", standard_price=9.0),
    "selected_by": dict(SALES), "selected_at": "2026-09-21T10:00:00",
    "base_price": 9.0, "base_currency": "CNY", "base_tax_included": False,
    "base_quantity": 5000.0, "base_unit_price": 9.0,
    "source_type": "workbook", "review_status": "reviewed", "valid_until": "2027-03-01",
}

USER_EDITS = {"quantity": 3000, "face_paper_gsm": 250, "hot_stamping": True,
              "inner_length": 210}
USER_DELTAS = {"quantity": 0.27, "face_paper_gsm": 0.31, "hot_stamping": 0.18,
               "inner_length": 0.12}


def load_module(name):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


class Base(unittest.TestCase):
    maxDiff = None

    def module(self):
        module = load_module(WS_MODULE)
        if module is None:
            self.fail(WS_MISSING)
        return module

    def case_module(self):
        module = load_module("cpq_quick_quote_case")
        if module is None:
            self.fail(CASE_MISSING)
        return module

    def workspace(self, edits=None, *, source="workspace"):
        module = self.module()
        ws = module.new_workspace(copy.deepcopy(BASELINE), user=SALES)
        if edits:
            ws = module.apply_edits(ws, edits, source=source, user=SALES, rules=RULES)
        return ws


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANamingContract(Base):
    def test_a1_module_constants(self):
        module = self.module()
        self.assertEqual("quick_quote_workspace_v1", module.ENGINE_VERSION)
        self.assertEqual("packaging", module.INDUSTRY)
        self.assertEqual(FIELD_KEYS, tuple(module.FIELD_KEYS))
        self.assertEqual("kb_quick_quote_delta_rule", module.DELTA_RULE_TABLE)
        self.assertEqual(("rate", "step", "band", "direct"), tuple(module.RULE_KINDS))
        self.assertEqual(("workspace", "agent"), tuple(module.EDIT_SOURCES))

    def test_a2_field_specs_shape(self):
        module = self.module()
        specs = module.FIELD_SPECS
        self.assertEqual(set(FIELD_KEYS), set(specs))
        for key in FIELD_KEYS:
            spec = specs[key]
            self.assertTrue(spec.get("label"), "%s 缺中文标签" % key)
            self.assertIn(spec.get("group"), tuple(module.FIELD_GROUPS), "%s 分组非法" % key)
            self.assertIn(spec.get("value_type"), ("num", "bool", "text", "enum"),
                          "%s 取值类型非法" % key)
            if spec["value_type"] == "num":
                self.assertIn("unit", spec, "%s 必须带单位（Spec §2.1）" % key)

    def test_a3_write_roles_reuse_existing_closed_set(self):
        module = self.module()
        import cpq_packaging_quote
        self.assertEqual(cpq_packaging_quote.WRITE_ROLES, module.WRITE_ROLES,
                         "落库角色必须复用既有 WRITE_ROLES，不得新造闭集（Spec §2.5）")

    def test_a4_reuses_batch1_exception_class(self):
        module = self.module()
        case_module = self.case_module()
        self.assertIs(case_module.CaseLibraryUnavailable,
                      getattr(module, "CaseLibraryUnavailable", None))
        self.assertTrue(issubclass(module.WorkspaceError, Exception))

    def test_a5_public_callables(self):
        module = self.module()
        for name in ("load_rules", "normalize_value", "validate_edits", "delta_price",
                     "new_workspace", "apply_edits", "pending_fields", "confirm",
                     "diff_table", "diff_total", "agent_patch", "save", "load"):
            self.assertTrue(callable(getattr(module, name, None)),
                            "%s.%s() 缺失（Spec §2.1）" % (WS_MODULE, name))

    def test_a6_workspace_does_not_import_tech_pipeline(self):
        self.assertTrue(WORKSPACE_PY.exists(), WS_MISSING)
        source = WORKSPACE_PY.read_text(encoding="utf-8")
        for banned in ("tech_app", "packaging_handoff", "cad_converter"):
            self.assertNotIn(banned, source, "快速报价工作区不得进入技术工艺：%s" % banned)


# --------------------------------------------------------------------------- #
# B 组：差异价规则（读表 + 四种口径）
# --------------------------------------------------------------------------- #
class TestBDeltaRules(Base):
    def test_b1_rate_kind_exact_value(self):
        module = self.module()
        got = module.delta_price("face_paper_gsm", 200.0, 250.0, rules=RULES)
        self.assertTrue(got["priced"])
        self.assertAlmostEqual(0.31, float(got["delta"]), places=9)
        self.assertEqual("QQQ-PAPER-RATE", got["rule_code"])
        self.assertEqual(4, int(got["rule_version"]))
        self.assertTrue(got["formula"], "每项差异必须留下依据（公式）")

    def test_b2_step_kind_both_directions(self):
        module = self.module()
        up = module.delta_price("hot_stamping", False, True, rules=RULES)
        self.assertAlmostEqual(0.18, float(up["delta"]), places=9)
        down = module.delta_price("hot_stamping", True, False, rules=RULES)
        self.assertAlmostEqual(-0.18, float(down["delta"]), places=9)

    def test_b3_step_kind_uses_step_size(self):
        module = self.module()
        rules = tuple(dict(row, step_size=5.0, amount=1.0)
                      for row in RULES if row["rule_code"] == "QQQ-HOTSTEP")
        got = module.delta_price("hot_stamping", 0.0, 12.0, rules=rules)
        self.assertAlmostEqual(3.0, float(got["delta"]), places=9,
                               msg="ceil(12/5)=3 步 × 1.0 元")

    def test_b4_band_kind_uses_breakpoints_and_base_unit_price(self):
        module = self.module()
        got = module.delta_price("quantity", 5000.0, 3000.0, rules=RULES,
                                 base_unit_price=9.0)
        self.assertAlmostEqual(0.27, float(got["delta"]), places=9,
                               msg="0.95 - 0.92 = +0.03 × 9.0 元 = +0.27（Spec §2.2）")

    def test_b5_direct_kind_is_absolute_difference(self):
        module = self.module()
        got = module.delta_price("tooling_fee_amount", 800.0, 1200.0, rules=RULES)
        self.assertAlmostEqual(400.0, float(got["delta"]), places=9)

    def test_b6_field_without_rule_is_not_priced(self):
        module = self.module()
        got = module.delta_price("box_type", "A", "B", rules=RULES)
        self.assertFalse(got["priced"], "没有规则的字段不得编价格（Spec §2.2 第 2 条）")
        self.assertAlmostEqual(0.0, float(got["delta"]), places=9)
        self.assertTrue(got["note"], "不计价要给说明")
        self.assertIsNone(got["rule_code"] or None)

    def test_b7_rule_conflict_raises(self):
        module = self.module()
        rules = tuple(RULES) + (dict(RULES[1], rule_code="QQQ-PAPER-RATE-2", rate=0.01),)
        with self.assertRaises(module.WorkspaceError):
            module.delta_price("face_paper_gsm", 200.0, 250.0, rules=rules)

    def test_b8_load_rules_unavailable_or_empty_raises(self):
        module = self.module()
        case_module = self.case_module()
        with mock.patch.object(cpq_kb, "snapshot", side_effect=cpq_kb.KbUnavailable("probe")):
            with self.assertRaises(case_module.CaseLibraryUnavailable):
                module.load_rules(None)
        with mock.patch.object(cpq_kb, "snapshot",
                               return_value={"tables": {"kb_quick_quote_delta_rule": []}}):
            with self.assertRaises(case_module.CaseLibraryUnavailable):
                module.load_rules(None)

    def test_b9_expired_rule_does_not_price_silently(self):
        module = self.module()
        rules = tuple(dict(row, effective_to="2025-12-31")
                      for row in RULES if row["rule_code"] == "QQQ-PAPER-RATE")
        got = module.delta_price("face_paper_gsm", 200.0, 250.0, rules=rules,
                                 today=__import__("datetime").date(2026, 9, 21))
        self.assertFalse(got["priced"], "过期规则不得静默按老口径算钱（Spec §2.2 第 3 条）")
        self.assertTrue(got["note"])

    def test_b10_rules_come_from_injected_rows_only(self):
        module = self.module()
        with mock.patch.object(cpq_kb, "snapshot", side_effect=AssertionError("不得读库")):
            got = module.delta_price("face_paper_gsm", 200.0, 250.0, rules=RULES)
        self.assertAlmostEqual(0.31, float(got["delta"]), places=9)


# --------------------------------------------------------------------------- #
# C 组：工作区状态机
# --------------------------------------------------------------------------- #
class TestCWorkspaceState(Base):
    def test_c1_new_workspace_builds_base_and_current(self):
        module = self.module()
        ws = module.new_workspace(copy.deepcopy(BASELINE), user=SALES)
        self.assertEqual("quick_quote_workspace_v1", ws["engine_version"])
        self.assertEqual("QQ-2026-0001", ws["baseline"]["case_code"])
        self.assertEqual(5000.0, float(ws["base_values"]["quantity"]))
        self.assertEqual(ws["base_values"], ws["current"],
                         "初始 current 必须等于基准案例值")
        self.assertEqual({}, dict(ws["pending"]))
        self.assertEqual([], list(ws["edits"]))
        self.assertEqual([], list(ws.get("missing_base_fields", [])))

    def test_c2_base_values_can_come_explicitly(self):
        module = self.module()
        baseline = dict(copy.deepcopy(BASELINE), base_values={"quantity": 800.0},
                        case_snapshot=None)
        ws = module.new_workspace(baseline, user=SALES)
        self.assertEqual(800.0, float(ws["base_values"]["quantity"]),
                         "显式 base_values 优先（Spec §2.3）")

    def test_c3_agent_edits_go_to_pending_not_current(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000}, source="agent")
        self.assertEqual(3000.0, float(ws["pending"]["quantity"]))
        self.assertEqual(5000.0, float(ws["current"]["quantity"]),
                         "Agent 的修改未确认前不得进 current（Spec §2.3）")
        self.assertFalse(ws["confirmed"])
        edits = [row for row in ws["edits"] if row["field"] == "quantity"]
        self.assertTrue(edits)
        self.assertEqual("agent", edits[-1]["source"])
        self.assertFalse(edits[-1]["confirmed"])

    def test_c4_workspace_edits_are_confirmed_immediately(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000}, source="workspace")
        self.assertEqual(3000.0, float(ws["current"]["quantity"]))
        self.assertEqual({}, dict(ws["pending"]))
        self.assertTrue(ws["confirmed"])

    def test_c5_confirm_merges_pending(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000, "hot_stamping": True}, source="agent")
        confirmed = module.confirm(ws, user=SALES)
        self.assertEqual(3000.0, float(confirmed["current"]["quantity"]))
        self.assertIs(True, confirmed["current"]["hot_stamping"])
        self.assertEqual({}, dict(confirmed["pending"]))
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual({"quantity": 3000.0, "hot_stamping": True}, dict(ws["pending"]),
                         "confirm() 不得改入参（Spec §2.3）：入参应原样保留**两条**待确认项")

    def test_c6_confirm_requires_user(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000}, source="agent")
        with self.assertRaises(module.WorkspaceError):
            module.confirm(ws, user=None)

    def test_c7_pending_fields_reported(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000, "face_paper_gsm": 250}, source="agent")
        pending = module.pending_fields(ws)
        self.assertEqual({"quantity", "face_paper_gsm"}, set(pending))
        self.assertEqual(3000.0, float(pending["quantity"]))

    def test_c8_apply_edits_is_pure(self):
        module = self.module()
        ws = self.workspace()
        snapshot = copy.deepcopy(ws)
        module.apply_edits(ws, {"quantity": 3000}, source="workspace", user=SALES, rules=RULES)
        self.assertEqual(snapshot, ws, "apply_edits() 不得改入参（Spec §2.3）")

    def test_c9_reverting_to_base_clears_pending(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000}, source="agent")
        back = module.apply_edits(ws, {"quantity": 5000}, source="agent",
                                  user=SALES, rules=RULES)
        self.assertNotIn("quantity", dict(back["pending"]),
                         "改回基准值等于还原，pending 必须清掉")


# --------------------------------------------------------------------------- #
# D 组：校验、展示与业务示例数值
# --------------------------------------------------------------------------- #
class TestDValidationAndDiffTable(Base):
    def test_d1_range_violation_raises_with_field_errors(self):
        module = self.module()
        ws = self.workspace()
        with self.assertRaises(module.WorkspaceError) as ctx:
            module.apply_edits(ws, {"quantity": 0}, source="workspace", user=SALES, rules=RULES)
        errors = getattr(ctx.exception, "field_errors", None)
        self.assertTrue(errors, "校验失败必须带字段级错误（Spec §2.3）")
        self.assertEqual("quantity", errors[0]["field"])

    def test_d2_partial_write_forbidden(self):
        module = self.module()
        ws = self.workspace()
        with self.assertRaises(module.WorkspaceError):
            module.apply_edits(ws, {"quantity": 3000, "face_paper_gsm": 9999},
                               source="workspace", user=SALES, rules=RULES)
        self.assertEqual(5000.0, float(ws["current"]["quantity"]),
                         "有一条不合法就整批不写入")

    def test_d3_unknown_field_rejected(self):
        module = self.module()
        ws = self.workspace()
        with self.assertRaises(module.WorkspaceError):
            module.apply_edits(ws, {"unknown_field": 1}, source="workspace",
                               user=SALES, rules=RULES)

    def test_d4_enum_field_choices_enforced(self):
        module = self.module()
        ws = self.workspace()
        with self.assertRaises(module.WorkspaceError):
            module.apply_edits(ws, {"print_colors": "八色金葱"},
                               source="workspace", user=SALES, rules=RULES)

    def test_d5_dirty_values_are_normalized(self):
        module = self.module()
        self.assertEqual(250.0, module.normalize_value("face_paper_gsm", "250g"))
        self.assertEqual(200.0, module.normalize_value("inner_length", "200 mm"))
        self.assertEqual(3000.0, module.normalize_value("quantity", "3,000 个"))
        self.assertIs(True, module.normalize_value("hot_stamping", "有"))
        self.assertIs(False, module.normalize_value("hot_stamping", "无"))

    def test_d6_diff_table_only_lists_changed_fields(self):
        module = self.module()
        ws = self.workspace(USER_EDITS)
        rows = module.diff_table(ws, rules=RULES)
        fields = [row["field_key"] for row in rows]
        self.assertEqual({"quantity", "face_paper_gsm", "hot_stamping", "inner_length"},
                         set(fields))
        self.assertEqual(len(fields), len(set(fields)), "同一字段不得出现两行")
        self.assertEqual(sorted(fields, key=FIELD_KEYS.index), fields,
                         "行顺序必须按 FIELD_KEYS 顺序（Spec §2.3）")

    def test_d7_diff_table_reproduces_business_example(self):
        module = self.module()
        ws = self.workspace(USER_EDITS)
        rows = {row["field_key"]: row for row in module.diff_table(ws, rules=RULES)}
        for field, expected in USER_DELTAS.items():
            self.assertAlmostEqual(expected, float(rows[field]["delta"]), places=9,
                                   msg="%s 差异价必须与业务示例一致" % field)
        self.assertEqual("5000 个", rows["quantity"]["display_base"])
        self.assertEqual("3000 个", rows["quantity"]["display_current"])
        self.assertEqual("200 g/m²", rows["face_paper_gsm"]["display_base"])
        self.assertEqual("无", rows["hot_stamping"]["display_base"])
        self.assertEqual("有", rows["hot_stamping"]["display_current"])
        self.assertEqual("+0.27 元", rows["quantity"]["delta_text"])
        self.assertEqual("+0.18 元", rows["hot_stamping"]["delta_text"])

    def test_d8_diff_table_marks_pending_rows(self):
        module = self.module()
        ws = self.workspace(USER_EDITS, source="agent")
        rows = {row["field_key"]: row for row in module.diff_table(ws, rules=RULES)}
        self.assertTrue(all(row["pending"] for row in rows.values()),
                        "未确认的修改必须标 pending（Spec §2.6）")
        mixed = module.confirm(ws, user=SALES)
        after = {row["field_key"]: row for row in module.diff_table(mixed, rules=RULES)}
        self.assertFalse(any(row["pending"] for row in after.values()))

    def test_d9_diff_total_splits_confirmed_and_preview(self):
        module = self.module()
        ws = self.workspace(USER_EDITS, source="workspace")
        total = module.diff_total(ws, rules=RULES)
        self.assertAlmostEqual(9.0, float(total["base_unit_price"]), places=9)
        self.assertAlmostEqual(0.88, float(total["confirmed_delta_total"]), places=9)
        self.assertAlmostEqual(9.88, float(total["confirmed_unit_price"]), places=9)
        self.assertAlmostEqual(0.88, float(total["preview_delta_total"]), places=9)
        self.assertEqual("CNY", total["currency"])
        self.assertEqual(4, len(total["items"]))
        self.assertTrue(total["rule_versions"], "必须回显用到的规则版本（Spec §2.3）")

    def test_d10_pending_not_counted_as_confirmed(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000}, source="agent")
        total = module.diff_total(ws, rules=RULES)
        self.assertAlmostEqual(0.0, float(total["confirmed_delta_total"]), places=9)
        self.assertAlmostEqual(0.27, float(total["preview_delta_total"]), places=9)
        self.assertAlmostEqual(9.27, float(total["preview_unit_price"]), places=9)

    def test_d11_validate_edits_reports_all_problems(self):
        module = self.module()
        problems = module.validate_edits({"quantity": 0, "print_colors": "八色金葱",
                                          "nope": 1})
        fields = {row[0] for row in problems}
        self.assertEqual({"quantity", "print_colors", "nope"}, fields)
        for row in problems:
            self.assertTrue(row[1], "每条校验失败都要有中文原因")

    def test_d12_decimal_delta_text_format(self):
        module = self.module()
        ws = self.workspace({"inner_length": 190}, source="workspace")
        row = module.diff_table(ws, rules=RULES)[0]
        self.assertRegex(row["delta_text"], r"^-0\.12 元$")


# --------------------------------------------------------------------------- #
# E 组：自然语言入口
# --------------------------------------------------------------------------- #
class TestEAgentPatch(Base):
    def test_e1_multi_edit_sentence(self):
        module = self.module()
        ws = self.workspace()
        result = module.agent_patch(ws, "把数量改成 3000，面纸改成 250g，再增加烫金")
        edits = result["proposed_edits"]
        self.assertEqual(3000.0, float(edits["quantity"]))
        self.assertEqual(250.0, float(edits["face_paper_gsm"]))
        self.assertIs(True, edits["hot_stamping"])
        self.assertEqual([], list(result["unresolved"]))
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(3000.0, float(result["workspace"]["pending"]["quantity"]),
                         "Agent 建议必须写进工作区 pending（Spec §2.4）")

    def test_e2_ambiguous_change_is_not_guessed(self):
        module = self.module()
        ws = self.workspace()
        result = module.agent_patch(ws, "改成 250")
        self.assertEqual({}, dict(result["proposed_edits"]),
                         "没有字段名时不得猜（Spec §2.4 第 2 条）")
        self.assertTrue(result["unresolved"])
        candidates = result["unresolved"][0]["candidates"]
        self.assertIn("face_paper_gsm", candidates, "歧义要给出候选字段清单")

    def test_e3_agent_patch_never_outputs_price(self):
        module = self.module()
        ws = self.workspace()
        result = module.agent_patch(ws, "把数量改成 3000")
        blob = json.dumps(result, ensure_ascii=False)
        for banned in ("delta_total", "unit_price", "quote_amount", "价格区间", "price_range"):
            self.assertNotIn(banned, blob,
                             "Agent 入口不得直接出报价（Spec §2.4 第 3 条）")
        self.assertTrue(result["requires_confirmation"])

    def test_e4_propose_injection_is_used(self):
        module = self.module()
        ws = self.workspace()
        calls = []

        def fake_propose(text, workspace):
            calls.append(text)
            return {"face_paper_gsm": 300}

        result = module.agent_patch(ws, "特殊口径", propose=fake_propose)
        self.assertEqual(["特殊口径"], calls)
        self.assertEqual(300.0, float(result["proposed_edits"]["face_paper_gsm"]))

    def test_e5_unknown_field_in_sentence_is_unresolved(self):
        module = self.module()
        ws = self.workspace()
        result = module.agent_patch(ws, "把盒型改成超级礼盒")
        self.assertEqual({}, dict(result["proposed_edits"]))
        self.assertTrue(result["unresolved"])

    def test_e6_agent_patch_requires_user_for_pending_write(self):
        module = self.module()
        ws = self.workspace()
        result = module.agent_patch(ws, "把数量改成 3000", user=None)
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual("100", str(result["workspace"]["user"]["user_id"]),
                         "未显式传 user 时沿用工作区创建者（不得静默变成匿名）")


# --------------------------------------------------------------------------- #
# F 组：落库与恢复（复用卡片快照）
# --------------------------------------------------------------------------- #
class TestFPersist(Base):
    def test_f1_save_requires_confirmation(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000}, source="agent")
        with self.assertRaises(module.WorkspaceError):
            module.save(ws, user=SALES, session_id="sess-1")

    def test_f2_save_requires_role(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000}, source="workspace")
        for user in (None, VIEWER):
            with self.assertRaises(module.WorkspaceError):
                module.save(ws, user=user, session_id="sess-1")

    def test_f3_save_writes_card_step_snapshot(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000}, source="workspace")
        calls = []

        def fake_merge(session_id, step_no, snapshot, conn=None):
            calls.append((session_id, step_no, copy.deepcopy(snapshot)))
            return dict(snapshot)

        with mock.patch("cpq_wf.merge_step_snapshot", side_effect=fake_merge):
            module.save(ws, user=SALES, session_id="sess-1")
        self.assertEqual(1, len(calls))
        session_id, step_no, snapshot = calls[0]
        self.assertEqual("sess-1", session_id)
        self.assertEqual(2, step_no, "快速报价工作区挂在第 2 步快照（Spec §2.5）")
        self.assertIn("quick_quote", snapshot)
        self.assertEqual(3000.0, float(snapshot["quick_quote"]["current"]["quantity"]))

    def test_f4_load_reads_back_workspace(self):
        module = self.module()
        ws = self.workspace({"quantity": 3000}, source="workspace")
        with mock.patch("cpq_wf.step_snapshot", return_value={"quick_quote": ws}) as mocked:
            loaded = module.load("sess-1")
        self.assertEqual(2, mocked.call_args[0][1], "工作区读第 2 步快照（Spec §2.5）")
        self.assertEqual("quick_quote_workspace_v1", loaded["engine_version"])
        self.assertEqual(3000.0, float(loaded["current"]["quantity"]))

    def test_f5_no_new_table_created(self):
        self.assertTrue(WORKSPACE_PY.exists(), WS_MISSING)
        source = WORKSPACE_PY.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE", source,
                         "本批复用卡片快照，不新建表（Spec §2.5）")


# --------------------------------------------------------------------------- #
# G 组：前端对比表
# --------------------------------------------------------------------------- #
class TestFronendDiffTable(Base):
    def test_g1_workbench_has_workspace_container(self):
        html = WORKBENCH_HTML.read_text(encoding="utf-8")
        self.assertIn('id="quickQuoteWorkspace"', html,
                      "工作台缺快速报价工作区容器（Spec §2.6）")
        self.assertIn("quick-quote-panel.js", html,
                      "工作台必须加载 quick-quote-panel.js（Spec §2.6）")

    def test_g2_panel_renders_four_column_diff_table(self):
        self.assertTrue(PANEL_JS.exists(),
                        "tech_app/frontend/quick-quote-panel.js 不存在（批 1 §2.6）")
        source = PANEL_JS.read_text(encoding="utf-8")
        self.assertIn("renderDiffTable", source)
        for header in ("参数", "基准案例", "当前报价", "差异价格"):
            self.assertIn(header, source, "对比表缺列头：%s（Spec §2.6）" % header)

    def test_g3_panel_marks_pending_rows(self):
        self.assertTrue(PANEL_JS.exists(),
                        "tech_app/frontend/quick-quote-panel.js 不存在（批 1 §2.6）")
        source = PANEL_JS.read_text(encoding="utf-8")
        self.assertIn("pending", source, "未确认行必须有可见标记（Spec §2.6）")

    def test_g4_panel_does_not_recompute_prices(self):
        self.assertTrue(PANEL_JS.exists(),
                        "tech_app/frontend/quick-quote-panel.js 不存在（批 1 §2.6）")
        source = PANEL_JS.read_text(encoding="utf-8")
        for banned in ("delta_price", "rule_kind", "breakpoints", "0.0062"):
            self.assertNotIn(banned, source,
                             "前端不得重算价格，只能渲染后端结构（Spec §2.6）")

    def test_g5_panel_uses_backend_row_keys(self):
        self.assertTrue(PANEL_JS.exists(),
                        "tech_app/frontend/quick-quote-panel.js 不存在（批 1 §2.6）")
        source = PANEL_JS.read_text(encoding="utf-8")
        for key in ("field_key", "display_base", "display_current", "delta_text"):
            self.assertIn(key, source, "前端必须消费 diff_table() 的字段名：%s" % key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
