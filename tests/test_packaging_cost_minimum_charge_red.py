"""红测：包装成本最低收费口径裁决 —— 包装修复第 3 批。

Spec：`docs/specs/packaging-cost-minimum-charge.md`。依赖修复第 2 批（规则快照
`tech_app/agent_knowledge/rules/packaging_cost_rules.json` + 离线工具
`tech_app/tools/extract_packaging_rules.py`）已实现。

现状缺口（首次运行时**必须失败**，是实测不是推断）：
  · `FORMULA_CATALOG` 的复膜/热烫-平压/啤切/V槽 四条的**表达式取自「报价-工费率」**，
    而 **`minimum_charge` 200/150/100/120 取自「报价-行业标准」**（V槽的 120 两张表里都没有）；
    于是实际算的是 `MAX(①门限/数量, ②表达式)` —— **两张表里都不存在的第三条公式**；
  · `source_ref` 只写了 ②，看不出这一点；`compute_line()` 结果也不标注用的是哪套口径；
  · 没有任何审计码能挡下「门限来自另一张表」「门限数值在该单元格里根本不存在」这两件事。

实测证据（工作簿 `报价逻辑-0903.xlsx`，两表缓存值）：
  · 复膜 V2：① 1.2934294398230088 / ② 1.376611258004827
  · 热烫-平压 X2：① 0.5549999999999999 / ② 1.343266666666667
  · 啤/切 AI2：① 0.15 / ② 0.8347876923076923
  · V槽 AK5：① 0.15（门限 150）/ ② 0.816（无门限）
  · 裱纸 AH13：① 0.1 / ② 0.17132857142857144
  · ② 全表只有 4 处 MAX：`AU2` 与 `AI9`/`AI14`/`AI15`。

**本批不裁决哪个口径对**：裁决权在业务/用户。红测只要求「申报字段 + 单来源 + 未裁决可观测」。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RULES_JSON = ROOT / "tech_app" / "agent_knowledge" / "rules" / "packaging_cost_rules.json"
TOOL_PY = ROOT / "tech_app" / "tools" / "extract_packaging_rules.py"
WORKBOOK = ROOT / "裕同包装项目-待开发" / "报价逻辑-0903.xlsx"
WORKBOOK_SHA256 = "974d9484414824d0bbfd2c83fb0c6044e4348c7a407e1ae0f58699bb60d2acc0"

VISIBLE_SHEETS = ("问题点", "报价-行业标准", "包装运输", "报价表", "成本细分", "报价-工费率",
                  "包装运输 (2)", "报价表 (2)", "成本细分（2）")
HIDDEN_SHEETS = ("大货最终定价", "大货价核算1", "首批试产毛利核算")
POLICY_IDS = ("sheet_industry_standard", "sheet_labor_rate", "declared_hybrid")

#: 五个有争议的类别 → ① ② 两表同列（列字母相同，行号见 CELLS）
CATEGORIES = ("lamination", "hot_stamp_flat", "die_cutting", "v_groove", "mounting")
CATEGORY_CODES = {
    "lamination": "PKG-C-LAMINATION",
    "hot_stamp_flat": "PKG-C-HOT-STAMP-FLAT",
    "die_cutting": "PKG-C-DIE-CUT",
    "v_groove": "PKG-C-V-GROOVE",
    "mounting": "PKG-C-MOUNTING",
}
CATEGORY_COLUMNS = {"lamination": "V", "hot_stamp_flat": "X", "die_cutting": "AI",
                    "v_groove": "AK", "mounting": "AH"}
#: 取哪一行做黄金样例（对应 报价-工费率 第 2/5/13 行）
CATEGORY_ROWS = {"lamination": 2, "hot_stamp_flat": 2, "die_cutting": 2, "v_groove": 5,
                 "mounting": 13}
CATEGORY_CELLS = {name: "%s%d" % (CATEGORY_COLUMNS[name], CATEGORY_ROWS[name])
                  for name in CATEGORIES}
#: ① 报价-行业标准 原文里的门限（V槽是 150；现状写 120，两表都没有）
SHEET_A_THRESHOLD = {"lamination": 200, "hot_stamp_flat": 150, "die_cutting": 100,
                     "v_groove": 150, "mounting": 100}
#: ① / ② 缓存值（工作簿实测，q=1000）
GOLDEN_A = {"lamination": 1.2934294398230088, "hot_stamp_flat": 0.5549999999999999,
            "die_cutting": 0.15, "v_groove": 0.15, "mounting": 0.1}
GOLDEN_B = {"lamination": 1.376611258004827, "hot_stamp_flat": 1.343266666666667,
            "die_cutting": 0.8347876923076923, "v_groove": 0.816,
            "mounting": 0.17132857142857144}
GOLDEN_QUANTITY = 1000
#: 允许出现在 `minimum_charge` 里的数值（0 = 不采用门限）
ALLOWED_MINIMUM_CHARGE = {
    "PKG-C-LAMINATION": {0, 200},
    "PKG-C-HOT-STAMP-FLAT": {0, 150},
    "PKG-C-DIE-CUT": {0, 100},
    "PKG-C-V-GROOVE": {0, 150},
    "PKG-C-MOUNTING": {0, 100},
}
#: ② 报价-工费率 全表含 MAX 的单元格
SHEET_B_MAX_CELLS = {"AU2", "AI9", "AI14", "AI15"}

#: c1/c2/c4 的输入（第 7 批 CMinimumCharge 用的同一组）
LAM_20 = {"machine_length": 20, "machine_width": 20, "imposition_count": 1, "setup_minutes": 30,
          "capacity_per_hour": 5500, "equipment_rate": 197, "labor_rate": 145, "film_price": 1.7,
          "film_thickness_um": 18, "film_kg_price": 18.5, "tax_factor": 1.13}
DIE_BASE = {"imposition_count": 1, "setup_minutes": 120, "capacity_per_hour": 6500,
            "equipment_rate": 197.52, "labor_rate": 190.06}
#: 复算 ② 表达式（与 FORMAT 里的写法同源，仅用于红测期望值）
VAR_MAP_LAM = {"machine_length": "H", "machine_width": "I", "imposition_count": "J",
               "quote_quantity": "R", "capacity_per_hour": "CAP", "setup_minutes": "SET"}


def sheet_a_expression(category, variables):
    """① 报价-行业标准 的**可变项**（不含外层 MAX、不含 IFERROR）。"""
    q = variables["quote_quantity"]
    j = variables.get("imposition_count", 1)
    if category == "lamination":
        hl, hw = variables["machine_length"], variables["machine_width"]
        return (hl * hw / 1000000 * variables["film_price"] / variables["tax_factor"] / j
                + hl * hw / 1000000 * variables["film_thickness_um"] / 1000
                * variables["film_kg_price"] / j + 0.15 / j)
    if category == "hot_stamp_flat":
        return (variables["hot_area_mm2"] / 1000000 * variables["foil_price"] + 0.3 / j)
    if category == "die_cutting":
        return 0.15 / j
    if category == "v_groove":
        return 0.15
    if category == "mounting":
        raise NotImplementedError("裱纸 ① 的 H/I 尺寸未纳入本红测（用工作簿缓存值核对）")
    raise KeyError(category)


def sheet_b_expression(category, variables):
    """② 报价-工费率 的表达式（主行，无 MAX）。"""
    q = variables["quote_quantity"]
    j = variables.get("imposition_count", 1)
    rate = variables["equipment_rate"] + variables["labor_rate"]
    if category == "lamination":
        hl, hw = variables["machine_length"], variables["machine_width"]
        return (hl * hw / 1000000 * variables["film_price"] / variables["tax_factor"] / j
                + hl * hw / 1000000 * variables["film_thickness_um"] / 1000
                * variables["film_kg_price"] / j
                + (variables["setup_minutes"] / 60
                   + q / j / variables["capacity_per_hour"]) * rate / q)
    if category == "hot_stamp_flat":
        return (variables["hot_area_mm2"] / 1000000 * variables["foil_price"]
                + (variables["setup_minutes"] / 60
                   + q / j / variables["capacity_per_hour"]) * rate / q)
    if category == "die_cutting":
        return ((variables["setup_minutes"] / 60
                 + q / j / variables["capacity_per_hour"]) * rate / q)
    if category == "v_groove":
        return ((variables["setup_minutes"] / 60 + q / variables["capacity_per_hour"])
                * rate / q * variables["times"])
    if category == "mounting":
        raise NotImplementedError("裱纸 ② 的公式见工作簿 AH13")
    raise KeyError(category)


def expected_golden(policy_id, category, variables):
    """Spec §4.1 的 golden 期望值（裱纸 ① 的 H/I 尺寸未纳入红测 → 用工作簿缓存值）。"""
    if category == "mounting":
        threshold = SHEET_A_THRESHOLD["mounting"] / GOLDEN_QUANTITY
        return {"sheet_industry_standard": GOLDEN_A["mounting"],
                "sheet_labor_rate": GOLDEN_B["mounting"],
                "declared_hybrid": max(threshold, GOLDEN_B["mounting"])}[policy_id]
    return expected_policy_value(policy_id, category, variables)[0]


def expected_policy_value(policy_id, category, variables):
    """按候选口径复算 `(单件金额, 是否由门限决定)`（Spec §3）。"""
    q = variables["quote_quantity"]
    threshold = SHEET_A_THRESHOLD[category] / q
    if policy_id == "sheet_industry_standard":
        base = sheet_a_expression(category, variables)
        return (max(threshold, base), threshold > base)
    if policy_id == "sheet_labor_rate":
        return (sheet_b_expression(category, variables), False)
    if policy_id == "declared_hybrid":
        base = sheet_b_expression(category, variables)
        return (max(threshold, base), threshold > base)
    raise KeyError(policy_id)


#: Spec §3.1 的 6 行矩阵：用例 → 输入
RED_TEST_IMPACT = (
    ("c1", "lamination", dict(LAM_20, quote_quantity=1000)),
    ("c2", "lamination", dict(LAM_20, quote_quantity=100)),
    ("c2", "lamination", dict(LAM_20, quote_quantity=1000)),
    ("c2", "lamination", dict(LAM_20, quote_quantity=10000)),
    ("c4", "die_cutting", dict(DIE_BASE, quote_quantity=100)),
    ("c4", "die_cutting", dict(DIE_BASE, quote_quantity=1000)),
)


def load_cost_module():
    return importlib.import_module("tech_app.backend.services.packaging_cost")


def load_tool():
    if not TOOL_PY.exists():
        return None
    spec = importlib.util.spec_from_file_location("pkg_minimum_charge_rules", TOOL_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_rules():
    return json.loads(RULES_JSON.read_text(encoding="utf-8"))


def synthetic_cells(**sheets):
    out = {}
    for name, payload in sheets.items():
        state, formulas, cached = payload
        out[name] = {"state": state, "formulas": dict(formulas), "cached": dict(cached)}
    return out


GLUE_SOURCE_FORMULA = "=H2*I2/1000000*0.74/J2"
GLUE_EXPRESSION = "machine_length*machine_width/1000000*glue_unit_price/imposition_count"
GLUE_VARIABLE_MAP = {"machine_length": "H", "machine_width": "I", "imposition_count": "J"}


def synthetic_rule(**over):
    rule = {
        "formula_code": "PKG-C-GLUE", "cost_category": "glue",
        "expression": GLUE_EXPRESSION,
        "minimum_charge": 0, "rounding": 4, "rate_code": "", "loss_scope": "胶水",
        "source_sheet": "报价-工费率", "source_cell": "AN2",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/AN2",
        "minimum_charge_source_ref": "",
        "variable_map": dict(GLUE_VARIABLE_MAP),
        "verify_inputs": {"machine_length": 889, "machine_width": 700, "imposition_count": 1,
                          "glue_unit_price": 0.74},
        "expected_result": 0.46050199999999997, "formula_version": "packaging_cost_v1",
    }
    rule.update(over)
    cells = {"AN2": GLUE_SOURCE_FORMULA}
    if rule.get("source_cell"):
        cells = {rule["source_cell"]: rule.get("source_formula", GLUE_SOURCE_FORMULA)}
    rule.setdefault("source_formula", GLUE_SOURCE_FORMULA)
    return rule, cells


def synthetic_rules(rule=None, cells=None, **top):
    rule = rule or synthetic_rule()[0]
    cells = cells or {rule["source_cell"]: rule["source_formula"]}
    rules = {"rule_set": "packaging_cost_v1", "source_file": "synthetic.xlsx",
             "source_sha256": "synthetic-sha", "source_sheets": ["报价-工费率"],
             "review_status": "reviewed", "generated_at": "2026-09-20",
             "formulas": [rule],
             "minimum_charge_policy": {
                 "status": "pending", "chosen": "", "decided_by": "", "decided_at": "",
                 "candidates": [
                     {"policy_id": pid, "authoritative_sheet":
                      "" if pid == "declared_hybrid" else (
                          "报价-行业标准" if pid == "sheet_industry_standard" else "报价-工费率"),
                      "is_declared_hybrid": pid == "declared_hybrid",
                      "rationale": "synthetic", "golden": {}, "red_test_impact": []}
                     for pid in POLICY_IDS]}}
    rules.update(top)
    return rules, cells


def synthetic_workbook_cells(cells):
    return synthetic_cells(**{"报价-工费率": ("visible", dict(cells), {k: 0.4605 for k in cells})})


# --------------------------------------------------------------------------- #
# F. 工作簿证据（现在就该绿：锁住「口径冲突是真实的」）
# --------------------------------------------------------------------------- #
class FWorkbookEvidence(unittest.TestCase):
    """不依赖任何实现；只用工作簿证明 ① 与 ② 是两套不同的公式。"""

    def setUp(self):
        if not WORKBOOK.exists():
            self.skipTest("客户工作簿不在本机（不入库），跳过工作簿证据")

    def workbook(self, data_only=False):
        import openpyxl
        return openpyxl.load_workbook(WORKBOOK, data_only=data_only)

    def test_f1_sha256_matches_spec(self):
        import hashlib
        digest = hashlib.sha256(WORKBOOK.read_bytes()).hexdigest()
        self.assertEqual(digest, WORKBOOK_SHA256, "工作簿与 Spec §1 记录的不是同一份")

    def test_f2_two_sheets_disagree_on_all_five_categories(self):
        wb = self.workbook(data_only=True)
        for name in CATEGORIES:
            cell = CATEGORY_CELLS[name]
            a = wb["报价-行业标准"][cell].value
            b = wb["报价-工费率"][cell].value
            self.assertNotAlmostEqual(
                a, b, places=6,
                msg="%s 在 ① ② 两表必须给出不同值（否则本批无争议）：①=%r ②=%r" % (name, a, b))

    def test_f3_golden_constants_match_workbook(self):
        wb = self.workbook(data_only=True)
        for name in CATEGORIES:
            cell = CATEGORY_CELLS[name]
            self.assertAlmostEqual(wb["报价-行业标准"][cell].value, GOLDEN_A[name], places=9,
                                   msg="① %s!%s 黄金值与工作簿不符" % (name, cell))
            self.assertAlmostEqual(wb["报价-工费率"][cell].value, GOLDEN_B[name], places=9,
                                   msg="② %s!%s 黄金值与工作簿不符" % (name, cell))

    def test_f4_threshold_lives_only_in_industry_standard(self):
        wb = self.workbook()
        for name in CATEGORIES:
            cell = CATEGORY_CELLS[name]
            a = wb["报价-行业标准"][cell].value
            b = wb["报价-工费率"][cell].value
            self.assertIn("MAX(", a.upper(),
                          "① %s!%s 必须带门限 MAX（Spec §1）" % (name, cell))
            self.assertIn("%d/" % SHEET_A_THRESHOLD[name], a,
                          "① %s!%s 的门限必须是 %d" % (name, cell, SHEET_A_THRESHOLD[name]))
            if cell in SHEET_B_MAX_CELLS:
                continue
            self.assertNotIn("MAX(", b.upper(),
                             "② %s!%s 主行不得有门限 MAX（Spec §1）" % (name, cell))

    def test_f5_v_groove_threshold_is_150_not_120(self):
        wb = self.workbook()
        text_a = wb["报价-行业标准"]["AK5"].value
        text_b = wb["报价-工费率"]["AK5"].value
        self.assertIn("150/R5", text_a)
        self.assertNotIn("120", text_a + text_b,
                         "120 在两张表里都不存在 —— 现状的 minimum_charge=120 是凭空数字")

    def test_f6_max_only_in_four_rate_sheet_cells(self):
        wb = self.workbook()
        found = set()
        for row in wb["报价-工费率"].iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and "MAX(" in cell.value.upper():
                    found.add(cell.coordinate)
        self.assertEqual(found, SHEET_B_MAX_CELLS,
                         "② 报价-工费率 含 MAX 的单元格必须正好是 %s" % sorted(SHEET_B_MAX_CELLS))

    def test_f7_hidden_sheets_are_hidden(self):
        wb = self.workbook()
        for name in HIDDEN_SHEETS:
            self.assertEqual(wb[name].sheet_state, "hidden",
                             "%s 必须是隐藏 Sheet（不得作为规则来源）" % name)
        for name in VISIBLE_SHEETS:
            self.assertEqual(wb[name].sheet_state, "visible")


# --------------------------------------------------------------------------- #
# B. 单来源申报（直接读 FORMULA_CATALOG，不依赖快照）
# --------------------------------------------------------------------------- #
class BSingleSourceDeclaration(unittest.TestCase):
    def catalog(self):
        return load_cost_module().FORMULA_CATALOG

    def test_b1_thresholded_entries_declare_source_sheet_and_cell(self):
        for name in CATEGORIES:
            entry = self.catalog()[CATEGORY_CODES[name]]
            self.assertTrue(entry.get("source_sheet"),
                            "%s 必须声明 source_sheet（Spec §5.1）" % CATEGORY_CODES[name])
            self.assertTrue(entry.get("source_cell"),
                            "%s 必须声明 source_cell（Spec §5.1）" % CATEGORY_CODES[name])
            self.assertIn(entry["source_sheet"], VISIBLE_SHEETS,
                          "%s 的 source_sheet 必须是可见 Sheet" % CATEGORY_CODES[name])

    def test_b2_source_ref_matches_sheet_and_cell(self):
        for name in CATEGORIES:
            code = CATEGORY_CODES[name]
            entry = self.catalog()[code]
            expected = "报价逻辑-0903.xlsx/%s/%s" % (entry.get("source_sheet"),
                                                     entry.get("source_cell"))
            self.assertEqual(entry.get("source_ref"), expected,
                             "%s 的 source_ref 必须与 source_sheet/source_cell 一致" % code)

    def test_b3_thresholded_entries_declare_minimum_charge_source(self):
        for name in CATEGORIES:
            code = CATEGORY_CODES[name]
            entry = self.catalog()[code]
            if not entry.get("minimum_charge"):
                continue
            self.assertTrue(entry.get("minimum_charge_source_ref"),
                            "%s 的 minimum_charge=%r 必须声明 minimum_charge_source_ref（Spec §5.2）"
                            % (code, entry.get("minimum_charge")))

    def test_b4_zero_threshold_entries_have_empty_source(self):
        for code, entry in self.catalog().items():
            if entry.get("minimum_charge"):
                continue
            self.assertFalse(entry.get("minimum_charge_source_ref"),
                             "%s 的 minimum_charge=0 时 minimum_charge_source_ref 必须为空" % code)

    def test_b5_minimum_charge_value_exists_in_one_of_the_two_sheets(self):
        for code, allowed in ALLOWED_MINIMUM_CHARGE.items():
            entry = self.catalog()[code]
            self.assertIn(
                entry.get("minimum_charge"), allowed,
                "%s 的 minimum_charge=%r 不在许可集合 %s 里：120 这种数字两张表都没有（Spec §5.2）"
                % (code, entry.get("minimum_charge"), sorted(allowed)))

    def test_b6_expressions_are_verbatim_from_their_source_cell(self):
        module = load_cost_module()
        helper = getattr(module, "verbatim_equivalent", None)
        self.assertTrue(callable(helper),
                        "packaging_cost 必须提供 verbatim_equivalent(...)（Spec §5.3）")
        for name in CATEGORIES:
            code = CATEGORY_CODES[name]
            entry = self.catalog()[code]
            literals = {k: v for k, v in (entry.get("verify_inputs") or {}).items()
                        if k not in (entry.get("variable_map") or {})}
            self.assertTrue(
                helper(entry["expression"], entry.get("source_formula", ""),
                       entry.get("variable_map") or {}, entry.get("source_cell", ""), literals),
                "%s 的 expression 与 %s 原文不是逐字等价 —— 说明门限与表达式来自不同表（Spec §5.3）"
                % (code, entry.get("source_cell")))

    def test_b7_thresholded_entries_declare_variable_map(self):
        for name in CATEGORIES:
            code = CATEGORY_CODES[name]
            entry = self.catalog()[code]
            vmap = entry.get("variable_map") or {}
            self.assertTrue(vmap, "%s 必须声明 variable_map（Spec §5.3）" % code)
            for var, column in vmap.items():
                self.assertIn(var, entry["expression"],
                              "%s 的 variable_map 里的 %s 必须出现在表达式里" % (code, var))
                self.assertTrue(column and column.isalpha(),
                                "%s 的 variable_map[%s]=%r 必须是列字母" % (code, var, column))

    def test_b8_source_sheet_is_never_hidden(self):
        for code, entry in self.catalog().items():
            for field in ("source_sheet",):
                if entry.get(field):
                    self.assertNotIn(entry[field], HIDDEN_SHEETS,
                                     "%s 的 %s 不得指向隐藏 Sheet（Spec §5.1）" % (code, field))
            ref = entry.get("minimum_charge_source_ref") or ""
            for hidden in HIDDEN_SHEETS:
                self.assertNotIn(hidden, ref,
                                 "%s 的 minimum_charge_source_ref 不得指向隐藏 Sheet" % code)


# --------------------------------------------------------------------------- #
# C. verbatim_equivalent（红：这个判定函数目前不存在）
# --------------------------------------------------------------------------- #
class CVerbatimHelper(unittest.TestCase):
    def helper(self):
        module = load_cost_module()
        fn = getattr(module, "verbatim_equivalent", None)
        self.assertTrue(callable(fn),
                        "packaging_cost 必须提供 verbatim_equivalent(expression, "
                        "source_formula, variable_map, source_cell, literals)（Spec §5.3）")
        return fn

    def test_c1_redundant_parentheses_and_whitespace_are_equivalent(self):
        fn = self.helper()
        self.assertTrue(fn("H2*I2/1000000*1.7/1.13/J2",
                           "=(H2*I2/1000000*1.7/1.13/J2)", {}, "V2", {}))
        self.assertTrue(fn("machine_length*machine_width/1000000*1.7/1.13/imposition_count",
                           "=(H2*I2/1000000*1.7/1.13/J2)",
                           {"machine_length": "H", "machine_width": "I",
                            "imposition_count": "J"}, "V2", {}))

    def test_c2_scientific_and_plain_numbers_are_equivalent(self):
        fn = self.helper()
        self.assertTrue(fn("H2*I2/1e6*1.7", "=(H2*I2/1000000*1.7)", {}, "V2", {}))

    def test_c3_single_number_change_is_not_equivalent(self):
        fn = self.helper()
        self.assertFalse(fn("H2*I2/1000000*1.8/1.13/J2",
                            "=(H2*I2/1000000*1.7/1.13/J2)", {}, "V2", {}),
                         "1.7 改成 1.8 必须判不等（否则「抄错一个系数」挡不住）")

    def test_c4_iferror_wrapper_is_stripped(self):
        fn = self.helper()
        self.assertTrue(fn("MAX(200/R2,H2*I2/1000000)",
                           '=IFERROR(MAX(200/R2,H2*I2/1000000),"")', {}, "V2", {}))

    def test_c5_wrong_source_cell_row_is_not_equivalent(self):
        fn = self.helper()
        self.assertFalse(fn("H3*I3/1000000*1.7/1.13/J3",
                            "=(H2*I2/1000000*1.7/1.13/J2)", {}, "V2", {}),
                         "声明 V2 却抄了第 3 行必须判不等")

    def test_c6_unknown_identifier_is_not_equivalent(self):
        fn = self.helper()
        self.assertFalse(fn("H2*I2/1000000*glue_unit_price/J2",
                            "=(H2*I2/1000000*0.74/J2)", {}, "AN2", {}),
                         "既不在 variable_map 也不在 literals 里的变量必须判不等")

    def test_c7_literals_are_resolved_from_verify_inputs(self):
        fn = self.helper()
        self.assertTrue(fn("machine_length*machine_width/1000000*glue_unit_price/imposition_count",
                           "=H2*I2/1000000*0.74/J2", dict(GLUE_VARIABLE_MAP), "AN2",
                           {"glue_unit_price": 0.74}))


# --------------------------------------------------------------------------- #
# A. 快照里的 minimum_charge_policy 块（依赖修复第 2 批）
# --------------------------------------------------------------------------- #
class APolicyBlock(unittest.TestCase):
    def rules(self):
        if not RULES_JSON.exists():
            self.fail("缺少 tech_app/agent_knowledge/rules/packaging_cost_rules.json（修复第 2 批）")
        return read_rules()

    def block(self):
        rules = self.rules()
        self.assertIn("minimum_charge_policy", rules,
                      "快照必须新增 minimum_charge_policy 块（Spec §4）")
        return rules["minimum_charge_policy"]

    def candidates(self):
        return {item["policy_id"]: item for item in self.block()["candidates"]}

    def test_a1_policy_block_exists_with_three_candidates(self):
        block = self.block()
        self.assertIn("status", block)
        self.assertEqual(len(block.get("candidates") or []), 3)
        self.assertEqual({item["policy_id"] for item in block["candidates"]},
                         set(POLICY_IDS), "候选必须正好是 ① ② ③ 三条")

    def test_a2_status_closed_set_and_decision_fields(self):
        block = self.block()
        self.assertIn(block.get("status"), {"pending", "chosen"})
        if block["status"] == "pending":
            self.assertEqual(block.get("chosen"), "")
            self.assertEqual(block.get("decided_by"), "")
        else:
            self.assertIn(block.get("chosen"), POLICY_IDS)
            self.assertTrue(block.get("decided_by"),
                            "status=chosen 必须有 decided_by（谁拍的板）")
            self.assertTrue(block.get("decided_at"))

    def test_a3_every_candidate_has_required_fields(self):
        for policy_id, item in self.candidates().items():
            for field in ("authoritative_sheet", "is_declared_hybrid", "rationale", "golden",
                          "red_test_impact"):
                self.assertIn(field, item, "%s 必须带 %s（Spec §4.1）" % (policy_id, field))
            self.assertEqual(set(item["golden"]), set(CATEGORIES),
                             "%s 的 golden 必须正好覆盖 5 个类别" % policy_id)
            for name, row in item["golden"].items():
                self.assertEqual(row.get("cell"), CATEGORY_CELLS[name])
                self.assertEqual(row.get("quantity"), GOLDEN_QUANTITY)

    def test_a4_golden_values_match_workbook(self):
        for policy_id, item in self.candidates().items():
            for name, row in item["golden"].items():
                expected = expected_golden(policy_id, name, self.policy_inputs(name))
                self.assertAlmostEqual(
                    row.get("unit_amount"), expected, places=9,
                    msg="%s 的 %s 黄金值与工作簿口径不符（Spec §4.1）" % (policy_id, name))

    def policy_inputs(self, name):
        if name == "lamination":
            return {"machine_length": 889, "machine_width": 700, "imposition_count": 1,
                    "setup_minutes": 30, "capacity_per_hour": 5500, "equipment_rate": 197,
                    "labor_rate": 145, "film_price": 1.7, "film_thickness_um": 18,
                    "film_kg_price": 18.5, "tax_factor": 1.13, "quote_quantity": 1000}
        if name == "hot_stamp_flat":
            return {"hot_area_mm2": 30000, "imposition_count": 1, "setup_minutes": 200,
                    "capacity_per_hour": 5000, "equipment_rate": 193, "labor_rate": 115,
                    "foil_price": 8.5, "quote_quantity": 1000}
        if name == "die_cutting":
            return dict(DIE_BASE, quote_quantity=1000)
        if name == "v_groove":
            return {"imposition_count": 1, "setup_minutes": 60, "capacity_per_hour": 3000,
                    "equipment_rate": 195, "labor_rate": 111, "times": 2, "quote_quantity": 1000}
        return {}  # 裱纸 ① 的 H/I 尺寸未纳入红测 → golden 用工作簿缓存值（expected_golden）

    def test_a5_industry_standard_golden_equals_industry_standard_expression(self):
        item = self.candidates()["sheet_industry_standard"]
        for name in ("lamination", "hot_stamp_flat", "die_cutting", "v_groove"):
            inputs = self.policy_inputs(name)
            expected = max(SHEET_A_THRESHOLD[name] / GOLDEN_QUANTITY,
                           sheet_a_expression(name, inputs))
            self.assertAlmostEqual(item["golden"][name]["unit_amount"], expected, places=9,
                                   msg="① 的 %s 必须等于 MAX(门限/q, ①可变项)" % name)
        self.assertAlmostEqual(item["golden"]["mounting"]["unit_amount"], GOLDEN_A["mounting"],
                               places=9)

    def test_a6_rate_sheet_golden_equals_rate_sheet_expression(self):
        item = self.candidates()["sheet_labor_rate"]
        for name in ("lamination", "hot_stamp_flat", "die_cutting", "v_groove"):
            expected = sheet_b_expression(name, self.policy_inputs(name))
            self.assertAlmostEqual(item["golden"][name]["unit_amount"], expected, places=9,
                                   msg="② 的 %s 必须等于该表原文（无 MAX）" % name)
        self.assertAlmostEqual(item["golden"]["mounting"]["unit_amount"], GOLDEN_B["mounting"],
                               places=9)
        self.assertTrue(item["is_declared_hybrid"] is False)
        self.assertEqual(item["authoritative_sheet"], "报价-工费率")

    def test_a7_red_test_impact_matrix_recomputes(self):
        for policy_id, item in self.candidates().items():
            rows = item.get("red_test_impact") or []
            self.assertEqual(len(rows), len(RED_TEST_IMPACT),
                             "%s 的 red_test_impact 必须正好 %d 行（Spec §4.1）"
                             % (policy_id, len(RED_TEST_IMPACT)))
            for row, (case, category, inputs) in zip(rows, RED_TEST_IMPACT):
                amount, applied = expected_policy_value(policy_id, category, inputs)
                self.assertEqual(row.get("case"), case)
                self.assertEqual(row.get("quantity"), inputs["quote_quantity"])
                self.assertEqual(row.get("applied"), applied,
                                 "%s/%s q=%s 的 applied 复算不符" % (policy_id, case,
                                                                     inputs["quote_quantity"]))
                self.assertAlmostEqual(row.get("amount"), amount, places=9,
                                       msg="%s/%s q=%s 的 amount 复算不符"
                                           % (policy_id, case, inputs["quote_quantity"]))

    def test_a8_no_candidate_points_at_hidden_sheet(self):
        for policy_id, item in self.candidates().items():
            for field in ("authoritative_sheet", "minimum_charge_source", "expression_source"):
                self.assertNotIn(item.get(field), HIDDEN_SHEETS,
                                 "%s 的 %s 不得指向隐藏 Sheet" % (policy_id, field))
        self.assertEqual(self.candidates()["declared_hybrid"]["authoritative_sheet"], "")


# --------------------------------------------------------------------------- #
# D. 运行时必须暴露「用哪套口径」
# --------------------------------------------------------------------------- #
class DRuntimeSurface(unittest.TestCase):
    def module(self):
        return load_cost_module()

    def policy_info(self):
        module = self.module()
        fn = getattr(module, "minimum_charge_policy", None)
        self.assertTrue(callable(fn),
                        "packaging_cost 必须提供 minimum_charge_policy()（Spec §6）")
        return fn()

    def test_d1_module_exposes_minimum_charge_policy(self):
        module = self.module()
        fn = getattr(module, "minimum_charge_policy", None)
        self.assertTrue(callable(fn),
                        "packaging_cost 必须提供 minimum_charge_policy()（Spec §6）")
        self.assertIsInstance(getattr(module, "MINIMUM_CHARGE_POLICY", None), dict,
                              "必须有模块级 MINIMUM_CHARGE_POLICY（Spec §6）")

    def test_d2_pending_policy_is_unresolved_with_declared_fallback(self):
        module = self.module()
        block = getattr(module, "MINIMUM_CHARGE_POLICY", None)
        self.assertIsInstance(block, dict, "必须有模块级 MINIMUM_CHARGE_POLICY（Spec §6）")
        self.assertIn(block.get("status"), {"pending", "chosen"})
        info = self.policy_info()
        for field in ("status", "chosen", "policy", "fallback", "decided_by"):
            self.assertIn(field, info, "minimum_charge_policy() 必须返回 %s" % field)

    def test_d3_compute_line_carries_policy(self):
        module = self.module()
        info = self.policy_info()
        lam = {"machine_length": 889, "machine_width": 700, "imposition_count": 1,
               "setup_minutes": 30, "capacity_per_hour": 5500, "equipment_rate": 197,
               "labor_rate": 145, "film_price": 1.7, "film_thickness_um": 18,
               "film_kg_price": 18.5, "tax_factor": 1.13, "quote_quantity": 1000}
        line = module.compute_line("lamination", lam)
        self.assertIn("policy", line, "compute_line 结果必须带 policy 字段（Spec §6）")
        self.assertEqual(line["policy"], info["policy"])

    def test_d4_policy_function_reads_module_constant_live(self):
        module = self.module()
        self.policy_info()
        self.assertIsInstance(getattr(module, "MINIMUM_CHARGE_POLICY", None), dict,
                              "必须有模块级 MINIMUM_CHARGE_POLICY（Spec §6）")
        original = module.MINIMUM_CHARGE_POLICY
        try:
            module.MINIMUM_CHARGE_POLICY = {"status": "chosen", "chosen": "sheet_labor_rate",
                                           "decided_by": "synthetic", "decided_at": "2026-09-20"}
            info = module.minimum_charge_policy()
            self.assertEqual(info["policy"], "sheet_labor_rate",
                             "chosen 时必须返回所选口径（Spec §6）")
            self.assertEqual(info["fallback"], "")
            module.MINIMUM_CHARGE_POLICY = {"status": "pending", "chosen": "", "decided_by": "",
                                            "decided_at": ""}
            info = module.minimum_charge_policy()
            self.assertEqual(info["policy"], "unresolved")
            self.assertEqual(info["fallback"], "sheet_labor_rate",
                             "未裁决时必须标注回退口径，不许静默装作已裁决")
        finally:
            module.MINIMUM_CHARGE_POLICY = original

    def test_d5_chosen_policy_reproduces_its_golden_values(self):
        module = self.module()
        info = self.policy_info()
        self.assertEqual(info["status"], "chosen",
                         "本批必须先由业务/用户裁决；未裁决时这条必然失败（Spec §3/§6）")
        golden = {"sheet_industry_standard": GOLDEN_A,
                  "sheet_labor_rate": GOLDEN_B,
                  "declared_hybrid": {name: max(SHEET_A_THRESHOLD[name] / GOLDEN_QUANTITY,
                                                GOLDEN_B[name]) for name in CATEGORIES}}[info["policy"]]
        inputs = {
            "lamination": {"machine_length": 889, "machine_width": 700, "imposition_count": 1,
                           "setup_minutes": 30, "capacity_per_hour": 5500, "equipment_rate": 197,
                           "labor_rate": 145, "film_price": 1.7, "film_thickness_um": 18,
                           "film_kg_price": 18.5, "tax_factor": 1.13, "quote_quantity": 1000},
            "hot_stamp_flat": {"hot_area_mm2": 30000, "imposition_count": 1, "setup_minutes": 200,
                               "capacity_per_hour": 5000, "equipment_rate": 193,
                               "labor_rate": 115, "foil_price": 8.5, "quote_quantity": 1000},
            "die_cutting": dict(DIE_BASE, quote_quantity=1000),
            "v_groove": {"imposition_count": 1, "setup_minutes": 60, "capacity_per_hour": 3000,
                         "equipment_rate": 195, "labor_rate": 111, "times": 2,
                         "quote_quantity": 1000},
        }
        for name, variables in inputs.items():
            line = module.compute_line(name, variables)
            self.assertAlmostEqual(line["amount"], golden[name], places=6,
                                   msg="%s 在 %s 口径下必须复现黄金值（Spec §3）"
                                       % (name, info["policy"]))
            self.assertEqual(line["policy"], info["policy"])

    def test_d6_unresolved_never_claims_min_charge_applied(self):
        module = self.module()
        info = self.policy_info()
        if info["status"] == "chosen":
            self.skipTest("已裁决：{0} 口径下由 test_d5 复算黄金值".format(info["chosen"]))
        line = module.compute_line("lamination", dict(LAM_20, quote_quantity=1000))
        self.assertEqual(line["policy"], "unresolved")
        self.assertFalse(line["min_charge_applied"],
                         "未裁决期间不许先按 ③ 的拼接公式产出 min_charge_applied=true（Spec §6）")


# --------------------------------------------------------------------------- #
# E. 审计码（工具来自修复第 2 批）
# --------------------------------------------------------------------------- #
class EAuditCodes(unittest.TestCase):
    def tool(self):
        module = load_tool()
        self.assertIsNotNone(module,
                             "缺少 tech_app/tools/extract_packaging_rules.py（修复第 2 批）")
        self.assertTrue(callable(getattr(module, "audit_rules", None)))
        return module

    def audit(self, rules, cells, **over):
        module = self.tool()
        kwargs = {"workbook_sha256": rules["source_sha256"], "workbook_name": "synthetic.xlsx",
                  "catalog_codes": {rules["formulas"][0]["formula_code"]}}
        kwargs.update(over)
        return module.audit_rules(synthetic_workbook_cells(cells), rules, **kwargs)

    def codes(self, report):
        return {item["code"] for item in report.get("problems") or []}

    def clean(self):
        rules, cells = synthetic_rules()
        return rules, cells

    def test_e1_clean_snapshot_passes(self):
        rules, cells = self.clean()
        report = self.audit(rules, cells)
        self.assertTrue(report["ok"], report.get("problems"))
        self.assertEqual(report["exit_code"], 0)

    def test_e2_policy_block_missing(self):
        rules, cells = self.clean()
        rules.pop("minimum_charge_policy")
        report = self.audit(rules, cells)
        self.assertIn("minimum_charge_policy_missing", self.codes(report))
        self.assertEqual(report["exit_code"], 1)

    def test_e3_policy_status_or_candidates_invalid(self):
        rules, cells = self.clean()
        rules["minimum_charge_policy"]["status"] = "maybe"
        self.assertEqual(self.audit(rules, cells)["exit_code"], 1)
        self.assertIn("minimum_charge_policy_unknown",
                      self.codes(self.audit(rules, cells)))
        rules, cells = self.clean()
        rules["minimum_charge_policy"]["status"] = "chosen"
        rules["minimum_charge_policy"]["chosen"] = "whatever"
        rules["minimum_charge_policy"]["decided_by"] = "someone"
        self.assertIn("minimum_charge_policy_unknown",
                      self.codes(self.audit(rules, cells)))

    def test_e4_mixed_source_formula_is_rejected(self):
        """门限来自 报价-行业标准、表达式来自 报价-工费率，且未申报 ③ → 必须报错。"""
        rule, _ = synthetic_rule(minimum_charge=200,
                                 minimum_charge_source_ref="报价逻辑-0903.xlsx/报价-行业标准/V2")
        rules, cells = synthetic_rules(rule=rule)
        report = self.audit(rules, cells)
        self.assertIn("mixed_source_formula", self.codes(report))
        self.assertEqual(report["exit_code"], 1)

    def test_e5_declared_hybrid_allows_two_sources(self):
        rule, _ = synthetic_rule(minimum_charge=200,
                                 minimum_charge_source_ref="报价逻辑-0903.xlsx/报价-行业标准/V2")
        rules, cells = synthetic_rules(rule=rule)
        rules["minimum_charge_policy"].update({"status": "chosen", "chosen": "declared_hybrid",
                                               "decided_by": "业务"})
        report = self.audit(rules, cells)
        self.assertNotIn("mixed_source_formula", self.codes(report))

    def test_e6_threshold_without_source_ref(self):
        rule, _ = synthetic_rule(minimum_charge=200)
        rules, cells = synthetic_rules(rule=rule)
        report = self.audit(rules, cells)
        self.assertIn("minimum_charge_source_missing", self.codes(report))

    def test_e7_threshold_value_not_present_in_source_cell(self):
        """复现 V槽 120 的缺陷：门限数值在来源单元格里根本不存在。"""
        rule, _ = synthetic_rule(minimum_charge=120,
                                 minimum_charge_source_ref="报价逻辑-0903.xlsx/报价-工费率/AK5")
        rules, cells = synthetic_rules(rule=rule)
        report = self.audit(rules, cells)
        self.assertIn("minimum_charge_not_in_source", self.codes(report))

    def test_e8_source_cell_mismatch(self):
        rule, _ = synthetic_rule(source_formula="=H2*I2/1000000*0.75/J2")
        rules, cells = synthetic_rules(rule=rule)
        report = self.audit(rules, cells)
        self.assertIn("source_cell_mismatch", self.codes(report))
        self.assertEqual(report["exit_code"], 2)

    def test_e9_unmapped_variable(self):
        rule, _ = synthetic_rule(expression="machine_length*machine_width/1000000*nope/imposition_count")
        rules, cells = synthetic_rules(rule=rule)
        report = self.audit(rules, cells)
        self.assertIn("unmapped_variable", self.codes(report))

    def test_e10_hidden_sheet_source(self):
        rule, _ = synthetic_rule(minimum_charge=200,
                                 minimum_charge_source_ref="报价逻辑-0903.xlsx/大货价核算1/AI2")
        rules, cells = synthetic_rules(rule=rule)
        report = self.audit(rules, cells)
        self.assertIn("hidden_sheet_source", self.codes(report))

    def test_e11_verbatim_equivalence_accepts_redundant_parentheses(self):
        rule, _ = synthetic_rule(source_formula="=((H2*I2/1000000)*0.74)/J2")
        rules, cells = synthetic_rules(rule=rule)
        report = self.audit(rules, cells)
        self.assertNotIn("source_cell_mismatch", self.codes(report),
                         "多余括号不算改写（Spec §5.3）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
