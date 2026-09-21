"""红测：快速报价生成、风险提示与转精准报价 —— 逆向快速报价第 4 批。

Spec：`docs/specs/quick-quote-4-quick-quote-and-handoff.md`
依赖：批 1（案例模型）、批 2（候选 + 基准快照）、批 3（字段工作区 + 差异价）。
本批红测**假设前三批已实现**：前三批未落地时，相关用例会以「批 N 未实现」的原因失败。

现状缺口（实测，不是推断）：
  · `cpq_quick_quote_price.py` 不存在 —— 快速报价没有「基准价 + 差异项」入口；
  · `cpq_packaging_quote.py:214` 的 `price()` 只吃技术工艺成本包（精准链路），
    全仓没有「偏差范围 / 适用门槛 / 转精准出口」；
  · `cpq_wf.py:51` 已有 `TASK_KIND_TECH_NEW="tech_new_product"`（`确认需求解析结果.html:799`
    的「转技术工艺」按钮），但没有任何接口把快速报价已填数据整包带过去；
  · `cpq_tech_bridge.py:620` 的 `HANDOFF_KINDS` 是既有闭集，本批不得新增 kind。

数值口径（注入规则，Spec §2.4）：
  基准单价 9.00 元；数量 5000→3000 +0.27；面纸 200→250g +0.31；烫金 无→有 +0.18；
  内长 200→210mm +0.12 → 差异合计 0.88 → 快速报价 9.88 元，偏差 ±5% → 区间 [9.386, 10.374]。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写业务数据、不派发任务；
  · 规则/配置一律注入；`cpq_wf` 一律 mock；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRICE_PY = ROOT / "cpq_quick_quote_price.py"

import cpq_packaging_quote  # noqa: E402
import cpq_tech_bridge  # noqa: E402
import cpq_wf  # noqa: E402

MODULE = "cpq_quick_quote_price"
MISSING = "cpq_quick_quote_price.py 不存在（Spec 批 4 §2.1）"
DEP1 = "cpq_quick_quote_case.py 不存在（Spec 批 1，批 4 依赖它）"
DEP3 = "cpq_quick_quote_workspace.py 不存在（Spec 批 3，批 4 依赖它）"
SALES = {"user_id": "100", "username": "sales1", "role_code": "sales_mgr"}
VIEWER = {"user_id": "300", "username": "viewer1", "role_code": "viewer"}
TODAY = dt.date(2026, 9, 21)

GATE_KEYS = ("case_reviewed", "case_not_expired", "box_compatible",
             "size_within_threshold", "quantity_in_range", "no_unknown_process")
TRANSFER_REQUIRED_FIELDS = ("box_type", "closure_type", "inner_length", "inner_width",
                            "inner_height", "face_paper_gsm", "grey_board_gsm",
                            "print_colors", "quantity")
HANDOFF_KINDS_BEFORE = ("cost_to_quote", "cost_to_process", "process_to_quote",
                        "report_to_quote", "packaging_cost_to_quote")

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


def load_module(name):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


class Base(unittest.TestCase):
    maxDiff = None

    def module(self):
        module = load_module(MODULE)
        if module is None:
            self.fail(MISSING)
        return module

    def case_module(self):
        module = load_module("cpq_quick_quote_case")
        if module is None:
            self.fail(DEP1)
        return module

    def workspace_module(self):
        module = load_module("cpq_quick_quote_workspace")
        if module is None:
            self.fail(DEP3)
        return module

    def workspace(self, edits=None, *, baseline=None, source="workspace"):
        ws_module = self.workspace_module()
        ws = ws_module.new_workspace(copy.deepcopy(baseline or BASELINE), user=SALES)
        if edits:
            ws = ws_module.apply_edits(ws, edits, source=source, user=SALES, rules=RULES)
        return ws


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANamingContract(Base):
    def test_a1_module_constants(self):
        module = self.module()
        self.assertEqual("quick_quote_v1", module.ENGINE_VERSION)
        self.assertEqual("packaging", module.INDUSTRY)
        self.assertEqual(GATE_KEYS, tuple(module.GATE_KEYS))
        self.assertEqual(TRANSFER_REQUIRED_FIELDS, tuple(module.TRANSFER_REQUIRED_FIELDS))
        self.assertEqual("quick_quote_price", module.QUOTE_KEY)
        self.assertEqual(2, module.QUOTE_STEP)
        self.assertEqual("precise_quote", module.TARGET_PRECISE)

    def test_a2_gate_labels_cover_all_keys(self):
        module = self.module()
        labels = module.GATE_LABELS
        self.assertEqual(set(GATE_KEYS), set(labels))
        for key, label in labels.items():
            self.assertTrue(label, "%s 缺中文标签" % key)

    def test_a3_reuses_existing_role_and_tax_closed_sets(self):
        module = self.module()
        self.assertEqual(cpq_packaging_quote.WRITE_ROLES, module.WRITE_ROLES)
        self.assertEqual(cpq_packaging_quote.DEFAULT_TAX_RATE, module.DEFAULT_TAX_RATE)

    def test_a4_advice_text_and_exception(self):
        module = self.module()
        self.assertIn("建议转精准报价", module.ADVICE_TRANSFER)
        self.assertTrue(issubclass(module.QuickQuoteBlocked, module.QuickQuoteError))

    def test_a5_public_callables(self):
        module = self.module()
        for name in ("gate_config", "gate_check", "price", "quote_fingerprint",
                     "transfer_to_precise", "save", "find_quote"):
            self.assertTrue(callable(getattr(module, name, None)),
                            "%s.%s() 缺失（Spec §2.1）" % (MODULE, name))

    def test_a6_no_new_handoff_kind_defined(self):
        self.assertTrue(PRICE_PY.exists(), MISSING)
        source = PRICE_PY.read_text(encoding="utf-8")
        self.assertNotIn("HANDOFF_KINDS =", source,
                         "本批不得新增交接口径闭集（Spec §2.6）")
        self.assertIn("TASK_KIND_TECH_NEW", source,
                      "转精准报价必须复用既有「转技术工艺」任务口径")

    def test_a7_no_tech_pipeline_import(self):
        self.assertTrue(PRICE_PY.exists(), MISSING)
        source = PRICE_PY.read_text(encoding="utf-8")
        for banned in ("tech_app", "packaging_handoff", "cad_converter"):
            self.assertNotIn(banned, source, "快速报价模块不得进入技术工艺：%s" % banned)


# --------------------------------------------------------------------------- #
# B 组：配置
# --------------------------------------------------------------------------- #
class TestBConfig(Base):
    def test_b1_defaults_added_to_case_config(self):
        module = self.module()
        case_module = self.case_module()
        defaults = case_module.default_config()
        for key, value in (("size_diff_threshold", 0.15), ("quantity_min", 100),
                           ("quantity_max", 100000), ("base_deviation_pct", 0.05),
                           ("per_miss_deviation_pct", 0.02), ("max_deviation_pct", 0.20),
                           ("tax_rate", 0.13)):
            self.assertIn(key, defaults, "批 4 必须给 default_config() 补键：%s" % key)
            self.assertEqual(value, defaults[key])

    def test_b2_gate_config_shares_same_source(self):
        module = self.module()
        case_module = self.case_module()
        gate_cfg = module.gate_config()
        for key in ("size_diff_threshold", "quantity_min", "quantity_max",
                    "base_deviation_pct", "per_miss_deviation_pct", "max_deviation_pct",
                    "tax_rate"):
            self.assertEqual(case_module.default_config()[key], gate_cfg[key],
                             "%s 必须与 default_config() 同源（Spec §2.2）" % key)

    def test_b3_gate_config_override_merges(self):
        module = self.module()
        merged = module.gate_config({"size_diff_threshold": 0.5})
        self.assertEqual(0.5, merged["size_diff_threshold"])
        self.assertEqual(100, merged["quantity_min"], "未覆盖的键必须用默认值补齐")


# --------------------------------------------------------------------------- #
# C 组：适用门槛
# --------------------------------------------------------------------------- #
class TestCGates(Base):
    def test_c1_all_gates_pass_for_normal_quick_quote(self):
        module = self.module()
        result = module.gate_check(BASELINE, self.workspace(USER_EDITS),
                                   today=TODAY, rules=RULES)
        self.assertTrue(result["passed"])
        self.assertEqual([], list(result["blocking"]))
        self.assertEqual([], list(result["unknown_process_fields"]))
        self.assertEqual(list(GATE_KEYS), [item["key"] for item in result["items"]])

    def test_c2_unreviewed_case_blocks(self):
        module = self.module()
        baseline = copy.deepcopy(BASELINE)
        baseline["case_snapshot"]["review_status"] = "draft"
        result = module.gate_check(baseline, self.workspace(USER_EDITS),
                                   today=TODAY, rules=RULES)
        self.assertFalse(result["passed"])
        self.assertIn("case_reviewed", result["blocking"])
        self.assertEqual(module.ADVICE_TRANSFER, result["advice"])

    def test_c3_expired_case_blocks(self):
        module = self.module()
        baseline = copy.deepcopy(BASELINE)
        baseline["valid_until"] = "2026-01-01"
        result = module.gate_check(baseline, self.workspace(USER_EDITS),
                                   today=TODAY, rules=RULES)
        self.assertFalse(result["passed"])
        self.assertIn("case_not_expired", result["blocking"])
        self.assertIn("过期", json.dumps(result, ensure_ascii=False))

    def test_c4_changed_box_type_blocks(self):
        module = self.module()
        ws = self.workspace({"box_type": "YT-XX-99999-Z"})
        result = module.gate_check(BASELINE, ws, today=TODAY, rules=RULES)
        self.assertFalse(result["passed"])
        self.assertIn("box_compatible", result["blocking"])
        detail = [item for item in result["items"] if item["key"] == "box_compatible"][0]
        self.assertIn("重新检索", detail["detail"])

    def test_c5_size_over_threshold_blocks_and_names_the_side(self):
        module = self.module()
        ws = self.workspace({"inner_length": 260})
        result = module.gate_check(BASELINE, ws, today=TODAY, rules=RULES)
        self.assertFalse(result["passed"])
        self.assertIn("size_within_threshold", result["blocking"])
        self.assertAlmostEqual(0.30, float(result["size_diff"]["max_rel"]), places=6)
        self.assertEqual(0.15, float(result["size_diff"]["threshold"]))
        detail = [item for item in result["items"] if item["key"] == "size_within_threshold"][0]
        self.assertIn("内长", detail["detail"])

    def test_c6_size_within_threshold_passes(self):
        module = self.module()
        result = module.gate_check(BASELINE, self.workspace({"inner_length": 225}),
                                   today=TODAY, rules=RULES)
        self.assertTrue(result["passed"])
        self.assertAlmostEqual(0.125, float(result["size_diff"]["max_rel"]), places=6)

    def test_c7_quantity_out_of_range_blocks(self):
        module = self.module()
        for qty in (50, 200000):
            ws = self.workspace({"quantity": qty})
            result = module.gate_check(BASELINE, ws, today=TODAY, rules=RULES)
            self.assertFalse(result["passed"], "数量 %s 应被拦" % qty)
            self.assertIn("quantity_in_range", result["blocking"])
            detail = [item for item in result["items"] if item["key"] == "quantity_in_range"][0]
            self.assertIn("100", detail["detail"])
            self.assertIn("100000", detail["detail"])

    def test_c8_unknown_process_without_rule_blocks(self):
        module = self.module()
        ws = self.workspace({"ribbon": True})
        result = module.gate_check(BASELINE, ws, today=TODAY, rules=RULES)
        self.assertFalse(result["passed"])
        self.assertIn("no_unknown_process", result["blocking"])
        self.assertEqual(["ribbon"], list(result["unknown_process_fields"]))
        detail = [item for item in result["items"] if item["key"] == "no_unknown_process"][0]
        self.assertIn("丝带", detail["detail"])

    def test_c9_new_process_with_rule_passes(self):
        module = self.module()
        result = module.gate_check(BASELINE, self.workspace({"hot_stamping": True}),
                                   today=TODAY, rules=RULES)
        self.assertTrue(result["passed"], "烫金有差异价规则，属于有依据的新增工艺")


# --------------------------------------------------------------------------- #
# D 组：定价与依据
# --------------------------------------------------------------------------- #
class TestDPricing(Base):
    def test_d1_unit_price_is_base_plus_delta(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        self.assertAlmostEqual(9.0, float(quote["base_unit_price"]), places=9)
        self.assertAlmostEqual(0.88, float(quote["delta_total"]), places=9)
        self.assertAlmostEqual(9.88, float(quote["unit_price"]), places=9)

    def test_d2_delta_items_match_workspace_diff(self):
        module = self.module()
        ws_module = self.workspace_module()
        ws = self.workspace(USER_EDITS)
        quote = module.price(BASELINE, ws, today=TODAY, rules=RULES)
        expected = {row["field_key"]: row["delta"] for row in ws_module.diff_table(ws, rules=RULES)}
        got = {row["field_key"]: row["delta"] for row in quote["delta_items"]}
        self.assertEqual(set(expected), set(got), "快速报价必须逐项复用批 3 的差异口径")
        for field, delta in expected.items():
            self.assertAlmostEqual(float(delta), float(got[field]), places=9)

    def test_d3_basis_record_is_complete(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        blob = json.dumps(quote["basis"], ensure_ascii=False)
        self.assertIn("QQ-2026-0001", blob, "依据必须写明基准案例编号")
        self.assertIn("9.00", blob.replace("9.0元", "9.00"), "依据必须写明基准价格")
        for field in ("数量", "面纸克重", "烫金", "内长"):
            self.assertIn(field, blob, "依据缺修改项：%s" % field)
        self.assertTrue(quote["rule_versions"], "依据必须带规则版本")

    def test_d4_tax_handling_follows_case(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        self.assertFalse(quote["tax_included"])
        self.assertAlmostEqual(9.88 * 1.13, float(quote["unit_price_taxed"]), places=6)
        taxed = copy.deepcopy(BASELINE)
        taxed["tax_included"] = True
        taxed_quote = module.price(taxed, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        self.assertTrue(taxed_quote["tax_included"])
        self.assertAlmostEqual(float(taxed_quote["unit_price"]),
                               float(taxed_quote["unit_price_taxed"]), places=9)

    def test_d5_case_and_currency_recorded(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        self.assertEqual("QQ-2026-0001", quote["case_code"])
        self.assertEqual(3, int(quote["case_version"]))
        self.assertEqual("CNY", quote["base_currency"])
        self.assertEqual("2027-03-01", quote["valid_until"])
        self.assertTrue(quote["transfer_available"])

    def test_d6_blocked_quote_raises_instead_of_pricing(self):
        module = self.module()
        ws = self.workspace({"inner_length": 300})
        with self.assertRaises(module.QuickQuoteBlocked) as ctx:
            module.price(BASELINE, ws, today=TODAY, rules=RULES)
        self.assertIn("建议转精准报价", str(ctx.exception))
        result = ctx.exception.result
        self.assertFalse(result["passed"])
        self.assertIn("size_within_threshold", result["blocking"])

    def test_d7_no_quote_id_before_gate_passes(self):
        module = self.module()
        ws = self.workspace({"quantity": 50})
        with self.assertRaises(module.QuickQuoteBlocked):
            module.price(BASELINE, ws, today=TODAY, rules=RULES)


# --------------------------------------------------------------------------- #
# E 组：偏差、区间与风险提示
# --------------------------------------------------------------------------- #
class TestEDeviationAndRange(Base):
    def test_e1_deviation_and_range_follow_config(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        self.assertAlmostEqual(0.05, float(quote["deviation"]["est_pct"]), places=9)
        self.assertAlmostEqual(9.88 * 0.05, float(quote["deviation"]["est_amount"]), places=6)
        self.assertAlmostEqual(9.88 * 0.95, float(quote["price_range"]["low"]), places=6)
        self.assertAlmostEqual(9.88 * 1.05, float(quote["price_range"]["high"]), places=6)

    def test_e2_unpriced_diff_widens_deviation_and_warns(self):
        module = self.module()
        ws = self.workspace(dict(USER_EDITS, freight_amount=300))
        quote = module.price(BASELINE, ws, today=TODAY, rules=RULES)
        self.assertAlmostEqual(0.07, float(quote["deviation"]["est_pct"]), places=9,
                               msg="无规则差异项每项 +2%，且其金额不得被当成 0 差异（Spec §2.4）")
        warnings = json.dumps(quote["warnings"], ensure_ascii=False)
        self.assertIn("运输", warnings, "无价格依据的差异项必须在 warnings 里点名")
        self.assertIn("0.07", json.dumps(quote["deviation"]))

    def test_e3_deviation_capped(self):
        # 实现期回写（见 Spec §5）：原写法的 window=True 属于「相对基准新增 + 没有差异价
        # 规则」的工艺项，按 Spec §2.3 第 6 条必须先被门槛拦下，price() 不该出价；
        # 这里改用「删项 + 无规则的费用项」堆偏差，并把上限压到 10% 真正验证封顶（断言只增不减）。
        module = self.module()
        ws = self.workspace(dict(USER_EDITS, freight_amount=300,
                                 magnet=False, v_groove=False))
        quote = module.price(BASELINE, ws, today=TODAY, rules=RULES,
                             config={"max_deviation_pct": 0.10})
        self.assertLessEqual(float(quote["deviation"]["est_pct"]), 0.20)
        self.assertAlmostEqual(0.10, float(quote["deviation"]["est_pct"]), places=9,
                               msg="0.05 + 3×0.02 = 0.11，封顶 0.10")

    def test_e4_risk_notice_contains_key_points(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        notice = quote["risk_notice"]
        self.assertIn("QQ-2026-0001", notice)
        self.assertIn("2027-03-01", notice, "风险提示要写清有效期")
        self.assertIn("5", notice, "风险提示要写清偏差百分比")
        self.assertIn("依据", notice, "风险提示要说明按依据报价")

    def test_e5_expiring_case_warns_but_prices(self):
        module = self.module()
        baseline = copy.deepcopy(BASELINE)
        baseline["valid_until"] = "2026-10-05"
        quote = module.price(baseline, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        self.assertAlmostEqual(9.88, float(quote["unit_price"]), places=9)
        self.assertTrue(quote["warnings"], "临近过期要提醒")


# --------------------------------------------------------------------------- #
# F 组：落库与版本
# --------------------------------------------------------------------------- #
class TestFPersist(Base):
    def test_f1_save_requires_role_and_session(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        for user in (None, VIEWER):
            with self.assertRaises(module.QuickQuoteError):
                module.save(quote, user=user, session_id="sess-1")
        with self.assertRaises(module.QuickQuoteError):
            module.save(quote, user=SALES, session_id="")

    def test_f2_save_writes_quick_quote_price_segment(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        calls = []

        def fake_merge(session_id, step_no, snapshot, conn=None):
            calls.append((session_id, step_no, copy.deepcopy(snapshot)))
            return dict(snapshot)

        with mock.patch("cpq_wf.merge_step_snapshot", side_effect=fake_merge):
            module.save(quote, user=SALES, session_id="sess-1")
        self.assertEqual(1, len(calls))
        session_id, step_no, snapshot = calls[0]
        self.assertEqual("sess-1", session_id)
        self.assertEqual(2, step_no)
        self.assertIn("quick_quote_price", snapshot)
        self.assertAlmostEqual(9.88, float(snapshot["quick_quote_price"]["unit_price"]),
                               places=9)

    def test_f3_fingerprint_is_idempotent_for_same_inputs(self):
        module = self.module()
        ws = self.workspace(USER_EDITS)
        first = module.price(BASELINE, ws, today=TODAY, rules=RULES)
        second = module.price(BASELINE, ws, today=TODAY, rules=RULES)
        self.assertEqual(first["quick_quote_id"], second["quick_quote_id"])
        self.assertEqual(module.quote_fingerprint(first), module.quote_fingerprint(second))
        changed = module.price(BASELINE, self.workspace(dict(USER_EDITS, quantity=2000)),
                               today=TODAY, rules=RULES)
        self.assertNotEqual(first["quick_quote_id"], changed["quick_quote_id"],
                            "字段变了必须换指纹，不能覆盖上一版")

    def test_f4_find_quote_reads_back_segment(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        with mock.patch("cpq_wf.step_snapshot",
                        return_value={"quick_quote_price": quote}) as mocked:
            loaded = module.find_quote(quote["quick_quote_id"], session_id="sess-1")
        self.assertEqual(quote["quick_quote_id"], loaded["quick_quote_id"])
        self.assertEqual(2, mocked.call_args[0][1])
        with mock.patch("cpq_wf.step_snapshot", return_value={}):
            self.assertEqual({}, module.find_quote("", session_id="sess-1"))

    def test_f5_no_new_table(self):
        self.assertTrue(PRICE_PY.exists(), MISSING)
        source = PRICE_PY.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE", source, "本批复用卡片快照，不新建表（Spec §2.5）")


# --------------------------------------------------------------------------- #
# G 组：转精准报价
# --------------------------------------------------------------------------- #
class TestGTransferToPrecise(Base):
    def test_g1_requires_role_and_session(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        for user in (None, VIEWER):
            with self.assertRaises(module.QuickQuoteError):
                module.transfer_to_precise(quote, user=user, session_id="sess-1")
        with self.assertRaises(module.QuickQuoteError):
            module.transfer_to_precise(quote, user=SALES, session_id="")

    def test_g2_blocked_quote_cannot_transfer(self):
        module = self.module()
        with self.assertRaises(module.QuickQuoteBlocked):
            module.price(BASELINE, self.workspace({"quantity": 50}), today=TODAY, rules=RULES)

    def test_g3_reuses_tech_new_product_task_kind(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        calls = []

        def fake_send(session_id, user, target_type="", target_role_code="",
                      target_user_id="", note="", task_kind="", payload=None, conn=None):
            calls.append({"session_id": session_id, "user": user, "task_kind": task_kind,
                          "payload": copy.deepcopy(payload or {}), "note": note})
            return {"task_id": "T-1"}

        with mock.patch("cpq_wf.send_task", side_effect=fake_send):
            result = module.transfer_to_precise(quote, user=SALES, session_id="sess-1")
        self.assertEqual(1, len(calls))
        self.assertEqual("sess-1", calls[0]["session_id"])
        self.assertEqual(cpq_wf.TASK_KIND_TECH_NEW, calls[0]["task_kind"],
                         "必须复用既有「转技术工艺」任务口径（Spec §2.6）")
        self.assertEqual("tech_new_product", result["task_kind"])
        self.assertEqual("precise_quote", result["target"])

    def test_g4_payload_carries_filled_data(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        calls = []

        with mock.patch("cpq_wf.send_task",
                        side_effect=lambda *a, **kw: calls.append(kw) or {"task_id": "T-1"}):
            result = module.transfer_to_precise(quote, user=SALES, session_id="sess-1")
        payload = calls[0]["payload"]
        for key in ("quick_quote", "requirement", "baseline_case", "delta_items"):
            self.assertIn(key, payload, "交接包缺键：%s（Spec §2.6）" % key)
        requirement = payload["requirement"]
        for field in TRANSFER_REQUIRED_FIELDS:
            self.assertIn(field, requirement, "已填数据必须整包带过去：%s" % field)
        self.assertEqual(3000.0, float(requirement["quantity"]))
        self.assertEqual(250.0, float(requirement["face_paper_gsm"]))
        self.assertIs(True, requirement["hot_stamping"])
        self.assertTrue(result["input_fields_complete"])
        self.assertEqual([], list(result["missing_inputs"]))
        self.assertNotIn("handoff_kind", json.dumps(payload, ensure_ascii=False),
                         "不得另造交接口径（Spec §2.6）")

    def test_g5_missing_required_field_refuses_transfer(self):
        module = self.module()
        module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        ws = self.workspace(USER_EDITS)
        quote = module.price(BASELINE, ws, today=TODAY, rules=RULES)
        broken = copy.deepcopy(quote)
        broken["requirement"] = {k: v for k, v in dict(BASELINE["case_snapshot"]).items()
                                 if k != "box_type"}
        with mock.patch("cpq_wf.send_task", return_value={"task_id": "T-1"}) as mocked:
            with self.assertRaises(module.QuickQuoteError) as ctx:
                module.transfer_to_precise(broken, user=SALES, session_id="sess-1")
        self.assertIn("box_type", str(ctx.exception), "缺字段必须点名")
        mocked.assert_not_called()

    def test_g6_transfer_does_not_call_tech_services(self):
        module = self.module()
        quote = module.price(BASELINE, self.workspace(USER_EDITS), today=TODAY, rules=RULES)
        with mock.patch("cpq_wf.send_task", return_value={"task_id": "T-1"}), \
                mock.patch.object(cpq_tech_bridge, "send_to_quote",
                                  side_effect=AssertionError("不得走回传链路")):
            module.transfer_to_precise(quote, user=SALES, session_id="sess-1")


# --------------------------------------------------------------------------- #
# H 组：非回归
# --------------------------------------------------------------------------- #
class TestHNonRegression(Base):
    def test_h1_handoff_kinds_unchanged(self):
        self.assertEqual(HANDOFF_KINDS_BEFORE, tuple(cpq_tech_bridge.HANDOFF_KINDS),
                         "既有交接口径闭集不得被本批改动")

    def test_h2_packaging_quote_pricing_untouched(self):
        source = (ROOT / "cpq_packaging_quote.py").read_text(encoding="utf-8")
        self.assertIn('ENGINE_VERSION = "packaging_quote_v1"', source)
        self.assertIn("DEFAULT_TAX_RATE = 0.13", source)
        self.assertNotIn("quick_quote", source,
                         "快速报价不得塞进精准定价模块（Spec §4）")

    def test_h3_workflow_task_kinds_unchanged(self):
        self.assertEqual("tech_new_product", cpq_wf.TASK_KIND_TECH_NEW)
        self.assertEqual("handoff", cpq_wf.TASK_KIND_HANDOFF)

    def test_h4_price_module_is_pure_on_inputs(self):
        module = self.module()
        baseline = copy.deepcopy(BASELINE)
        ws = self.workspace(USER_EDITS)
        baseline_snapshot, ws_snapshot = copy.deepcopy(baseline), copy.deepcopy(ws)
        module.price(baseline, ws, today=TODAY, rules=RULES)
        self.assertEqual(baseline_snapshot, baseline, "price() 不得改基准快照")
        self.assertEqual(ws_snapshot, ws, "price() 不得改工作区")


if __name__ == "__main__":
    unittest.main(verbosity=2)
