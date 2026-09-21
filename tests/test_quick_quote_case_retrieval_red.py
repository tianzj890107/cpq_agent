"""红测：相似案例检索与候选选择 —— 逆向快速报价第 2 批。

Spec：`docs/specs/quick-quote-2-case-retrieval.md`
依赖：批 1（`cpq_quick_quote_case.py`：案例模型 + 来源/审核/有效期准入）。
本批红测**假设批 1 已实现**：因此批 1 未落地时，本文件里依赖案例模型的用例会以
「批 1 未实现」的原因失败，这是预期的（批 2 建立在批 1 之上，不能靠重写准入判定绕过）。

现状缺口（实测，不是推断）：
  · `cpq_quick_quote_match.py` 不存在；
  · `cpq_packaging_match.py:25` 的五维匹配打的是**盒型库**（`kb_packaging_box_type`），
    输入键只有 7 个 `MATCH_INPUT_KEYS`，没有数量档 / 材料克重 / 内托 / 磁铁，
    也没有「候选为什么排第一」的相同项 / 差异项输出；
  · `cpq_kb.py:68` 没有 `kb_quick_quote_match_weight`，相似度权重口径无处可查；
  · 全仓没有「人工选基准案例」这一步的接口：既没有 `requires_manual_selection`，
    也没有 `build_baseline()` 的角色与可选性校验。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写业务数据；
  · 案例与权重一律注入（`cases=` / `weights=`）；库读不到只断言**抛错**；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib
import inspect
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MATCH_PY = ROOT / "cpq_quick_quote_match.py"

import cpq_kb  # noqa: E402

MATCH_MODULE = "cpq_quick_quote_match"
CASE_MODULE = "cpq_quick_quote_case"
MATCH_MISSING = "cpq_quick_quote_match.py 不存在（Spec 批 2 §2.1）"
CASE_MISSING = "cpq_quick_quote_case.py 不存在（Spec 批 1 §2.1，批 2 依赖它）"
TODAY = dt.date(2026, 9, 21)
SALES = {"user_id": "100", "username": "sales1", "role_code": "sales_mgr"}

QUICK_MATCH_INPUT_KEYS = (
    "box_type", "box_family", "closure_type",
    "inner_length", "inner_width", "inner_height",
    "grey_board_gsm", "face_paper_gsm",
    "insert_type", "print_colors", "lamination", "hot_stamping", "v_groove", "magnet",
    "quantity",
)
DIMENSIONS = ("size_range", "grey_board_gsm", "face_paper_gsm",
              "print_colors", "lamination", "hot_stamping",
              "v_groove", "magnet", "quantity")
HARD_GATE_DIMENSIONS = ("box_type", "box_family", "closure_type", "insert_type")
DEFAULT_WEIGHTS = (
    {"dimension": "size_range", "weight": 0.30, "hard_gate": 0, "tolerance": 0.20},
    {"dimension": "grey_board_gsm", "weight": 0.10, "hard_gate": 0, "tolerance": 0.30},
    {"dimension": "face_paper_gsm", "weight": 0.10, "hard_gate": 0, "tolerance": 0.30},
    {"dimension": "print_colors", "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "lamination", "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "hot_stamping", "weight": 0.08, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "v_groove", "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "magnet", "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "quantity", "weight": 0.12, "hard_gate": 0, "tolerance": 0.0},
)


def load_module(name):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def case_row(code="QQ-0001", **over):
    row = {
        "case_code": code, "case_version": 1, "industry": "packaging",
        "customer_masked": "华东酒类客户A",
        "box_type_code": "YT-RB-01001-A", "box_family": "01天地盖",
        "closure_type": "磁吸", "fit_clearance": 1.5,
        "inner_length": 200.0, "inner_width": 150.0, "inner_height": 80.0,
        "material_code": "MAT-FACE-157", "grey_board_gsm": 1200.0, "face_paper_gsm": 200.0,
        "print_colors": "CMYK", "lamination": True, "hot_stamping": True, "v_groove": True,
        "window": False, "insert_type": "EVA内托", "magnet": True, "ribbon": False,
        "quantity_tiers": [{"qty": 1000, "unit_price": 12.5},
                           {"qty": 3000, "unit_price": 10.8},
                           {"qty": 10000, "unit_price": 9.6}],
        "bom_summary": "面纸+灰板+内托", "process_summary": "印刷/覆膜/烫金/V槽",
        "standard_cost": 60.049408129741252, "standard_price": 80.06587750632167,
        "deal_price": 78.0, "currency": "CNY", "tax_included": False,
        "quote_date": "2026-06-01", "valid_from": "2026-06-01", "valid_until": "",
        "source_type": "workbook", "source_ref": "报价逻辑-0903.xlsx!报价表",
        "review_status": "reviewed", "version": 1,
    }
    row.update(over)
    return row


def inputs(**over):
    data = {
        "box_type": "YT-RB-01001-A", "box_family": "01天地盖", "closure_type": "磁吸",
        "inner_length": 200.0, "inner_width": 150.0, "inner_height": 80.0,
        "grey_board_gsm": 1200.0, "face_paper_gsm": 200.0,
        "insert_type": "EVA内托", "print_colors": "CMYK",
        "lamination": True, "hot_stamping": True, "v_groove": True, "magnet": True,
        "quantity": 3000,
    }
    data.update(over)
    return data


class Base(unittest.TestCase):
    maxDiff = None

    def module(self):
        module = load_module(MATCH_MODULE)
        if module is None:
            self.fail(MATCH_MISSING)
        return module

    def case_module(self):
        module = load_module(CASE_MODULE)
        if module is None:
            self.fail(CASE_MISSING)
        return module


# --------------------------------------------------------------------------- #
# A 组：命名契约与单一事实源
# --------------------------------------------------------------------------- #
class TestANamingContract(Base):
    def test_a1_module_idempotent_helpers_exist(self):
        module = self.module()
        self.assertEqual("quick_quote_case_match_v1", module.ENGINE_VERSION)
        self.assertEqual("packaging", module.INDUSTRY)
        self.assertEqual(QUICK_MATCH_INPUT_KEYS, tuple(module.QUICK_MATCH_INPUT_KEYS))
        self.assertEqual(HARD_GATE_DIMENSIONS, tuple(module.HARD_GATE_DIMENSIONS))
        self.assertEqual(DIMENSIONS, tuple(module.DIMENSIONS))
        self.assertEqual("kb_quick_quote_match_weight", module.WEIGHT_TABLE)
        self.assertEqual(5, module.DEFAULT_TOP_N)
        self.assertEqual(3, module.MIN_CANDIDATES)

    def test_a2_public_callables(self):
        module = self.module()
        for name, params in (("load_weights", None), ("match_cases", None),
                             ("explain", None), ("build_baseline", None)):
            func = getattr(module, name, None)
            self.assertTrue(callable(func), "%s.%s() 缺失（Spec §2.1/§2.4）" % (MATCH_MODULE, name))
        self.assertTrue(inspect.isclass(getattr(module, "QuickQuoteMatchError", None)))

    def test_a3_reuses_batch1_eligibility_and_exception(self):
        module = self.module()
        case_module = self.case_module()
        self.assertIs(case_module.quote_eligibility, getattr(module, "quote_eligibility", None),
                      "批 2 必须复用批 1 的 quote_eligibility（不得重写准入判定，Spec §2.1）")
        self.assertIs(case_module.CaseLibraryUnavailable,
                      getattr(module, "CaseLibraryUnavailable", None),
                      "读库异常类必须复用批 1 的 CaseLibraryUnavailable")

    def test_a4_match_does_not_import_tech_pipeline(self):
        self.assertTrue(MATCH_PY.exists(), MATCH_MISSING)
        source = MATCH_PY.read_text(encoding="utf-8")
        for banned in ("tech_app", "packaging_handoff", "cad_converter", "cpq_tech_bridge"):
            self.assertNotIn(banned, source,
                             "快速报价检索不得进入技术工艺链路：%s" % banned)

    def test_a5_default_weights_shape_matches_table_contract(self):
        module = self.module()
        rows = list(module.DEFAULT_WEIGHTS)
        self.assertEqual(DIMENSIONS, tuple(row["dimension"] for row in rows))
        total = sum(float(row["weight"]) for row in rows)
        # 九条种子按 Spec §2.1 逐字给出，加总本来就是 0.9：相似度是**按权重和归一**的
        # （`similarity = 加权和 / 权重和`，d1 用"完全一致 → 1.0"钉住这条），
        # 所以这里断言的是"种子没被改过"，不是"九条必须凑成 1"。
        self.assertAlmostEqual(0.9, total, places=9, msg="权重种子之和（Spec §2.1）不得被改动")
        self.assertEqual(DEFAULT_WEIGHTS, tuple(dict(row) for row in rows),
                         "权重表种子必须与 Spec §2.1 一致")


# --------------------------------------------------------------------------- #
# B 组：权重只从表来
# --------------------------------------------------------------------------- #
class TestBWeightsFromTable(Base):
    def test_b1_weights_change_reorders_candidates(self):
        module = self.module()
        cases = [
            case_row("QQ-PROCESS", face_paper_gsm=400.0, grey_board_gsm=800.0,
                     hot_stamping=True, v_groove=True, lamination=True, magnet=True,
                     quantity_tiers=[{"qty": 3000, "unit_price": 10.0}]),
            case_row("QQ-MATERIAL", face_paper_gsm=200.0, grey_board_gsm=1200.0,
                     hot_stamping=False, v_groove=False, lamination=False, magnet=False,
                     quantity_tiers=[{"qty": 3000, "unit_price": 11.0}]),
        ]
        process_only = tuple(
            dict(row, weight=(1.0 if row["dimension"] in
                              ("hot_stamping", "v_groove", "lamination", "magnet") else 0.0))
            for row in DEFAULT_WEIGHTS)
        paper_only = tuple(
            dict(row, weight=(1.0 if row["dimension"] == "face_paper_gsm" else 0.0))
            for row in DEFAULT_WEIGHTS)
        by_process = module.match_cases(inputs(), cases=cases, weights=process_only, today=TODAY)
        by_paper = module.match_cases(inputs(), cases=cases, weights=paper_only, today=TODAY)
        self.assertEqual("QQ-PROCESS", by_process["candidates"][0]["case_code"])
        self.assertEqual("QQ-MATERIAL", by_paper["candidates"][0]["case_code"],
                         "同一批案例换权重表必须换出不同排序（证明权重来自表，不是写死）")

    def test_b2_weights_come_from_injected_table_only(self):
        module = self.module()
        cases = [case_row("QQ-A"), case_row("QQ-B", face_paper_gsm=250.0)]
        only_paper = tuple(dict(row, weight=(1.0 if row["dimension"] == "face_paper_gsm" else 0.0))
                           for row in DEFAULT_WEIGHTS)
        result = module.match_cases(inputs(), cases=cases, weights=only_paper, today=TODAY)
        top = result["candidates"][0]
        self.assertEqual("QQ-A", top["case_code"])
        self.assertEqual(1.0, round(top["similarity"], 6),
                         "只按面纸克重打分时，完全一致的案例必须是 100%")

    def test_b3_load_weights_unavailable_or_empty_raises(self):
        module = self.module()
        case_module = self.case_module()
        with mock.patch.object(cpq_kb, "snapshot", side_effect=cpq_kb.KbUnavailable("probe")):
            with self.assertRaises(case_module.CaseLibraryUnavailable):
                module.load_weights(None)
        with mock.patch.object(cpq_kb, "snapshot", return_value={"tables": {
                "kb_quick_quote_match_weight": []}}):
            with self.assertRaises(case_module.CaseLibraryUnavailable,
                                   msg="权重表为空必须抛错，不得回落代码里的默认权重"):
                module.load_weights(None)

    def test_b4_no_numeric_weight_constants_in_code(self):
        self.assertTrue(MATCH_PY.exists(), MATCH_MISSING)
        source = MATCH_PY.read_text(encoding="utf-8")
        self.assertIn("kb_quick_quote_match_weight", source)
        start = source.index("DEFAULT_WEIGHTS")
        end = source.index("\n)\n", start)
        outside_seed = source[:start] + source[end:]
        for value in ("0.30", "0.20", "0.15", "0.12"):
            self.assertNotIn(value, outside_seed,
                             "权重/容差不得写死在打分逻辑里，只能来自权重表（Spec §2.1）")


# --------------------------------------------------------------------------- #
# C 组：硬筛选
# --------------------------------------------------------------------------- #
class TestCHardGates(Base):
    def test_c1_box_type_conflict_rejected(self):
        module = self.module()
        cases = [case_row("QQ-MINE"), case_row("QQ-OTHER", box_type_code="YT-RB-09999-Z")]
        result = module.match_cases(inputs(), cases=cases, weights=DEFAULT_WEIGHTS, today=TODAY)
        codes = [row["case_code"] for row in result["candidates"]]
        self.assertEqual(["QQ-MINE"], codes)
        rejected = {row["case_code"]: row for row in result["rejected"]}
        self.assertIn("QQ-OTHER", rejected)
        self.assertEqual("box_type_conflict", rejected["QQ-OTHER"]["reason_code"])
        self.assertTrue(rejected["QQ-OTHER"]["reason"], "淘汰要给中文原因")

    def test_c2_family_and_closure_and_insert_conflicts(self):
        module = self.module()
        cases = [
            case_row("QQ-FAMILY", box_family="03抽屉盒"),
            case_row("QQ-CLOSURE", closure_type="抽屉+拉带"),
            case_row("QQ-INSERT", insert_type="植绒内托"),
        ]
        result = module.match_cases(inputs(), cases=cases, weights=DEFAULT_WEIGHTS, today=TODAY)
        rejected = {row["case_code"]: row["reason_code"] for row in result["rejected"]}
        self.assertEqual("box_family_conflict", rejected.get("QQ-FAMILY"))
        self.assertEqual("closure_conflict", rejected.get("QQ-CLOSURE"))
        self.assertEqual("insert_conflict", rejected.get("QQ-INSERT"))
        self.assertEqual([], result["candidates"])

    def test_c3_closure_separator_intersection_is_compatible(self):
        module = self.module()
        cases = [case_row("QQ-MULTI", closure_type="磁吸/卡扣")]
        result = module.match_cases(inputs(closure_type="磁吸"), cases=cases,
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertEqual(["QQ-MULTI"], [row["case_code"] for row in result["candidates"]],
                         "闭合方式按分隔符拆开后交集非空即兼容（Spec §2.5）")

    def test_c4_missing_hard_gate_input_is_needs_input_not_conflict(self):
        module = self.module()
        cases = [case_row("QQ-FULL"), case_row("QQ-NOCLOSURE", closure_type="")]
        result = module.match_cases(inputs(closure_type=""), cases=cases,
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        status = {row["case_code"]: row for row in result["candidates"]}
        self.assertEqual("needs_input", status["QQ-FULL"]["status"],
                         "输入缺闭合方式时，案例只能算缺输入，不能判命中")
        self.assertEqual("needs_input", status["QQ-NOCLOSURE"]["status"])
        for row in result["candidates"]:
            self.assertEqual("missing_input", row["reason_code"],
                             "缺输入候选必须带 reason_code=missing_input（Spec §2.3 第 6 条）")

    def test_c5_inputs_complete_and_missing_inputs_reported(self):
        module = self.module()
        full = module.match_cases(inputs(), cases=[case_row("QQ-A")],
                                  weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertTrue(full["inputs_complete"])
        self.assertEqual([], list(full["missing_inputs"]))
        partial = module.match_cases(inputs(box_type="", quantity=None),
                                     cases=[case_row("QQ-A")],
                                     weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertFalse(partial["inputs_complete"])
        self.assertEqual({"box_type", "quantity"}, set(partial["missing_inputs"]))


# --------------------------------------------------------------------------- #
# D 组：相似度与排序
# --------------------------------------------------------------------------- #
class TestDSimilarityAndRanking(Base):
    def test_d1_identical_case_scores_100(self):
        module = self.module()
        result = module.match_cases(inputs(), cases=[case_row("QQ-SAME")],
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        top = result["candidates"][0]
        self.assertAlmostEqual(1.0, top["similarity"], places=9)
        self.assertAlmostEqual(100.0, top["similarity_pct"], places=6)

    def test_d2_candidates_capped_at_top_n(self):
        module = self.module()
        cases = [case_row("QQ-%04d" % index) for index in range(9)]
        result = module.match_cases(inputs(), cases=cases, weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertEqual(5, len(result["candidates"]), "默认最多 5 个候选（Spec §2.3 第 1 条）")
        self.assertFalse(result["few_candidates"])
        more = module.match_cases(inputs(), cases=cases, weights=DEFAULT_WEIGHTS,
                                  top_n=3, today=TODAY)
        self.assertEqual(3, len(more["candidates"]))

    def test_d3_sorted_by_similarity_then_case_code(self):
        module = self.module()
        cases = [
            # 面纸比需求(200)高 5g：与需求**不相等**才算"近"（原写 200.0 与需求逐字相同，
            # 于是它与 QQ-SAME 同为 1.0，同分按 case_code 升序时 QQ-CLOSE 必然排在前面）。
            case_row("QQ-CLOSE", face_paper_gsm=205.0),
            case_row("QQ-FAR", face_paper_gsm=260.0),
            case_row("QQ-SAME"),
        ]
        result = module.match_cases(inputs(), cases=cases, weights=DEFAULT_WEIGHTS, today=TODAY)
        scores = [row["similarity"] for row in result["candidates"]]
        self.assertEqual(sorted(scores, reverse=True), scores, "相似度必须降序")
        codes = [row["case_code"] for row in result["candidates"]]
        self.assertEqual("QQ-SAME", codes[0])
        self.assertLess(codes.index("QQ-CLOSE"), codes.index("QQ-FAR"))

    def test_d4_ineligible_cases_rank_after_eligible_ones(self):
        module = self.module()
        cases = [
            case_row("QQ-DRAFT", review_status="draft"),
            case_row("QQ-OK"),
        ]
        result = module.match_cases(inputs(), cases=cases, weights=DEFAULT_WEIGHTS, today=TODAY)
        codes = [row["case_code"] for row in result["candidates"]]
        self.assertEqual(["QQ-OK", "QQ-DRAFT"], codes,
                         "未审核案例可以展示，但必须排在可用案例之后（Spec §2.3 第 3 条）")
        draft = result["candidates"][1]
        self.assertFalse(draft["eligible"])
        self.assertIn("未审核", draft["rank_reason"])
        self.assertEqual("QQ-OK", result["suggested_case_code"],
                         "建议只能来自 eligible 候选")

    def test_d5_score_breakdown_covers_all_dimensions(self):
        module = self.module()
        result = module.match_cases(inputs(), cases=[case_row("QQ-A")],
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        breakdown = result["candidates"][0]["score_breakdown"]
        self.assertEqual(set(DIMENSIONS), set(breakdown))
        for key, value in breakdown.items():
            self.assertGreaterEqual(float(value), 0.0, "%s 分数不得为负" % key)
            self.assertLessEqual(float(value), 1.0, "%s 分数不得 >1" % key)


# --------------------------------------------------------------------------- #
# E 组：候选可解释性
# --------------------------------------------------------------------------- #
class TestECandidateExplanation(Base):
    def test_e1_candidate_shows_required_fields(self):
        module = self.module()
        result = module.match_cases(inputs(face_paper_gsm=250.0, quantity=1000),
                                    cases=[case_row("QQ-A")],
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        row = result["candidates"][0]
        for key in ("case_code", "similarity", "similarity_pct", "standard_price", "currency",
                    "tax_included", "quote_date", "valid_until", "expired", "expiring_soon",
                    "source_type", "review_status", "eligible", "same_items", "diff_items",
                    "rank_reason"):
            self.assertIn(key, row, "候选缺展示字段：%s（Spec §2.3）" % key)
        self.assertEqual("workbook", row["source_type"])
        self.assertEqual("reviewed", row["review_status"])
        self.assertEqual("2026-06-01", row["quote_date"])

    def test_e2_same_and_diff_items_explain_the_match(self):
        module = self.module()
        result = module.match_cases(inputs(face_paper_gsm=250.0, quantity=1000),
                                    cases=[case_row("QQ-A")],
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        row = result["candidates"][0]
        same = {item["field"] for item in row["same_items"]}
        diff = {item["field"] for item in row["diff_items"]}
        self.assertIn("box_type", same)
        self.assertIn("face_paper_gsm", diff)
        paper = [item for item in row["diff_items"] if item["field"] == "face_paper_gsm"][0]
        self.assertEqual(200.0, float(paper["base"]))
        self.assertEqual(250.0, float(paper["current"]))
        self.assertTrue(paper["label"], "差异项必须有中文标签")
        self.assertTrue(paper["delta_text"], "差异项必须有可读差异描述")

    def test_e3_deal_price_hidden_by_default(self):
        module = self.module()
        result = module.match_cases(inputs(), cases=[case_row("QQ-A")],
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertNotIn("deal_price", result["candidates"][0],
                         "成交价默认不得返回（Spec §2.3 第 4 条）")
        with_price = module.match_cases(inputs(), cases=[case_row("QQ-A")],
                                       weights=DEFAULT_WEIGHTS, today=TODAY,
                                       include_deal_price=True)
        self.assertIn("deal_price", with_price["candidates"][0])

    def test_e4_expiring_case_is_flagged(self):
        module = self.module()
        cases = [case_row("QQ-EXPIRING", valid_until="2026-10-05")]
        result = module.match_cases(inputs(), cases=cases, weights=DEFAULT_WEIGHTS, today=TODAY)
        row = result["candidates"][0]
        self.assertTrue(row["expiring_soon"], "距过期 ≤30 天必须提示（Spec 批 1 §2.3）")
        self.assertFalse(row["expired"])
        self.assertIn("过期", row["rank_reason"])

    def test_e5_explain_returns_single_case_breakdown(self):
        module = self.module()
        detail = module.explain(inputs(quantity=1000), case_row("QQ-A"), weights=DEFAULT_WEIGHTS)
        self.assertEqual("QQ-A", detail["case_code"])
        self.assertEqual(set(DIMENSIONS), set(detail["score_breakdown"]))
        self.assertTrue(detail["rank_reason"])
        self.assertIn("same_items", detail)
        self.assertIn("diff_items", detail)


# --------------------------------------------------------------------------- #
# F 组：人工选择
# --------------------------------------------------------------------------- #
class TestFManualSelection(Base):
    def test_f1_never_autoselects(self):
        module = self.module()
        result = module.match_cases(inputs(), cases=[case_row("QQ-A")],
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertTrue(result["requires_manual_selection"])
        self.assertEqual("", result["confirmed_case_code"],
                         "不得替用户决定基准案例（Spec §2.3）")
        self.assertEqual("QQ-A", result["suggested_case_code"])

    def test_f2_build_baseline_requires_user_and_role(self):
        module = self.module()
        cases = [case_row("QQ-A")]
        with self.assertRaises(module.QuickQuoteMatchError):
            module.build_baseline(inputs(), "QQ-A", cases=cases, user=None,
                                  weights=DEFAULT_WEIGHTS, today=TODAY)
        with self.assertRaises(module.QuickQuoteMatchError):
            module.build_baseline(inputs(), "QQ-A", cases=cases,
                                  user={"user_id": "300", "username": "v", "role_code": "viewer"},
                                  weights=DEFAULT_WEIGHTS, today=TODAY)

    def test_f3_baseline_payload_is_ready_for_workspace(self):
        module = self.module()
        cases = [case_row("QQ-A", quantity_tiers=[{"qty": 1000, "unit_price": 12.5},
                                                  {"qty": 3000, "unit_price": 10.8}])]
        baseline = module.build_baseline(inputs(quantity=3000), "QQ-A", cases=cases,
                                         user=SALES, weights=DEFAULT_WEIGHTS, today=TODAY)
        for key in ("engine_version", "case_code", "case_version", "case_snapshot",
                    "selected_by", "selected_at", "base_price", "base_currency",
                    "base_tax_included", "base_quantity", "base_unit_price",
                    "source_type", "review_status", "valid_until"):
            self.assertIn(key, baseline, "基准案例快照缺字段：%s（Spec §2.4）" % key)
        self.assertEqual("sales_mgr", baseline["selected_by"]["role_code"])
        self.assertEqual(3000.0, float(baseline["base_quantity"]))
        self.assertAlmostEqual(10.8, float(baseline["base_unit_price"]), places=6)
        self.assertAlmostEqual(80.06587750632167, float(baseline["base_price"]), places=9)

    def test_f4_ineligible_case_cannot_be_selected(self):
        module = self.module()
        for code, case, reason in (
                ("QQ-DRAFT", case_row("QQ-DRAFT", review_status="draft"), "not_reviewed"),
                ("QQ-DEMO", case_row("QQ-DEMO", source_type="demo"), "source_not_authoritative"),
                ("QQ-OLD", case_row("QQ-OLD", valid_until="2025-01-01"), "expired"),
        ):
            with self.assertRaises(module.QuickQuoteMatchError) as ctx:
                module.build_baseline(inputs(), code, cases=[case], user=SALES,
                                      weights=DEFAULT_WEIGHTS, today=TODAY)
            message = str(ctx.exception)
            self.assertTrue(message, "不可选案例必须给用户可见原因")
            self.assertIn(reason, getattr(ctx.exception, "reason_code", "") or message,
                          "不可选原因要对上准入口径：%s" % reason)

    def test_f5_unknown_case_code_raises(self):
        module = self.module()
        with self.assertRaises(module.QuickQuoteMatchError):
            module.build_baseline(inputs(), "QQ-NOPE", cases=[case_row("QQ-A")],
                                  user=SALES, weights=DEFAULT_WEIGHTS, today=TODAY)


# --------------------------------------------------------------------------- #
# G 组：边界与非回归
# --------------------------------------------------------------------------- #
class TestGBoundaries(Base):
    def test_g1_no_candidate_gives_reason_and_precise_advice(self):
        module = self.module()
        result = module.match_cases(inputs(), cases=[case_row("QQ-OTHER", box_type_code="X")],
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertEqual([], result["candidates"])
        self.assertTrue(result["no_candidate_reason"], "0 候选必须给中文原因")
        self.assertIn("精准报价", result["no_candidate_reason"],
                      "0 候选必须建议转精准报价，不得硬造候选（Spec §2.3 第 5 条）")
        self.assertEqual("", result["suggested_case_code"])

    def test_g2_few_candidates_flag(self):
        module = self.module()
        cases = [case_row("QQ-A"), case_row("QQ-B"), case_row("QQ-C")]
        result = module.match_cases(inputs(), cases=cases, weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertEqual(3, len(result["candidates"]))
        self.assertFalse(result["few_candidates"], "3 个候选刚好达到下限，不算少")
        two = module.match_cases(inputs(), cases=cases[:2], weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertTrue(two["few_candidates"], "不足 3 个候选要如实标记")
        self.assertEqual("", two["no_candidate_reason"],
                         "有候选时 no_candidate_reason 必须是空串（Spec §2.3 第 5 条）")

    def test_g3_match_is_pure(self):
        module = self.module()
        cases = [case_row("QQ-A")]
        weights = [dict(row) for row in DEFAULT_WEIGHTS]
        data = inputs()
        cases_snapshot, weights_snapshot, inputs_snapshot = (copy.deepcopy(cases),
                                                            copy.deepcopy(weights),
                                                            copy.deepcopy(data))
        module.match_cases(data, cases=cases, weights=weights, today=TODAY)
        self.assertEqual(cases_snapshot, cases, "match_cases 不得改案例行")
        self.assertEqual(weights_snapshot, weights, "match_cases 不得改权重行")
        self.assertEqual(inputs_snapshot, data, "match_cases 不得改输入")

    def test_g4_weights_version_is_reported(self):
        module = self.module()
        weights = [dict(row, version=7, review_status="reviewed",
                        source_type="workbook") for row in DEFAULT_WEIGHTS]
        result = module.match_cases(inputs(), cases=[case_row("QQ-A")],
                                    weights=weights, today=TODAY)
        self.assertTrue(result["weights_version"], "必须回显权重口径版本（Spec §2.3）")
        self.assertIn("7", result["weights_version"])

    def test_g5_match_does_not_write_anything(self):
        module = self.module()
        with mock.patch.object(cpq_kb, "snapshot", side_effect=AssertionError("不得读库")):
            result = module.match_cases(inputs(), cases=[case_row("QQ-A")],
                                        weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertEqual(1, len(result["candidates"]),
                         "显式注入 cases/weights 时不得再读库")

    def test_g6_print_colors_normalized_before_compare(self):
        module = self.module()
        cases = [case_row("QQ-4C", print_colors="4C")]
        result = module.match_cases(inputs(print_colors="CMYK"), cases=cases,
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        row = result["candidates"][0]
        self.assertEqual(1.0, float(row["score_breakdown"]["print_colors"]),
                         "4C 与 CMYK 必须归一后视为同色数（Spec §2.5）")

    def test_g7_quantity_string_normalized(self):
        module = self.module()
        cases = [case_row("QQ-A", quantity_tiers=[{"qty": 3000, "unit_price": 10.8}])]
        result = module.match_cases(inputs(quantity="3,000 个"), cases=cases,
                                    weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertEqual(1.0, float(result["candidates"][0]["score_breakdown"]["quantity"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
