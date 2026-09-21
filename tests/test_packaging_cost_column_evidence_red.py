"""红测：包装成本逐列证据登记 —— 包装修复第 4 批。

Spec：`docs/specs/packaging-cost-column-evidence.md`。依赖修复第 2 批（规则快照
`tech_app/agent_knowledge/rules/packaging_cost_rules.json`）已实现。

现状缺口（首次运行时**必须失败**，是实测不是推断）：
  · 快照里没有 `categories` / `column_evidence`，`报价-工费率` 里「哪 9 列真有公式、哪 15 列
    一行公式都没有」这条事实只存在于人工阅读工作簿的过程里；
  · 对账工具不会检查「有没有给没有证据的列造公式」；
  · 因此谁都可以给 丝印 / 压纹 / 贴双面胶 补一条公式，而没有任何机器可校验的痕迹挡下来。

实测证据（`报价-工费率` 第 2–15 行）：有公式的部件级列只有 S/U/V/X/AH/AI/AK/AN/AO 九列；
T/W/Y/Z/AA/AB/AC/AD/AE/AF/AG/AJ/AL/AM/AP 十五列 **0 公式 0 手填**。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RULES_JSON = ROOT / "tech_app" / "agent_knowledge" / "rules" / "packaging_cost_rules.json"
TOOL_PY = ROOT / "tech_app" / "tools" / "extract_packaging_rules.py"

#: 采用口径里真有公式的部件级列（Spec §1）
FORMULA_COLUMNS = {
    "S": ("material", "材料价", 13, 1),
    "U": ("print_uv", "UV印刷", 6, 8),
    "V": ("lamination", "复膜", 6, 8),
    "X": ("hot_stamp_flat", "热烫-平压", 3, 11),
    "AH": ("mounting", "裱纸", 1, 13),
    "AI": ("die_cutting", "啤/切", 13, 1),
    "AK": ("v_groove", "V槽", 2, 12),
    "AN": ("glue", "胶水", 6, 8),
    "AO": ("labor", "人工/全检/包装", 1, 13),
}
#: 采用口径里 0 公式 0 手填的列（Spec §1）
BLANK_COLUMNS = {
    "T": "print", "W": "transfer_film", "Y": "hot_stamp_round", "Z": "cold_stamp",
    "AA": "silk_screen", "AB": "varnish", "AC": "anti_scratch", "AD": "pet_oil",
    "AE": "visidi_uv", "AF": "texture", "AG": "emboss_deboss", "AJ": "folding",
    "AL": "auto_mount", "AM": "double_tape", "AP": "other",
}
SAMPLE_FORMULAS = {
    "S": "=K2*L2/1000000*M2/1000000*N2/1.13/J2+K2*L2/1000000*M2/1000000*N2/1.13*Q2/R2",
    "U": "=((30/60+R2/J2/12000)*(591+666))/R2+H2*I2/1000000*4/1000*115/1.13/J2",
    "V": ("=(H2*I2/1000000*1.7/1.13/J2+H2*I2/1000000*18/1000*18.5/J2)"
          "+((30/60+R2/J2/5500)*(197+145))/R2"),
    "X": "=((100*75*4)/1000000*8.5)+((200/60+R2/J2/5000)*(193+115))/R2",
    "AH": "=((30/60+R13/J13/3500)*(209+126))/R13",
    "AI": "=((120/60+R2/J2/6500)*(197.52+190.06))/R2",
    "AK": "=(60/60+R5/3000)*(195+111)/R5*2",
    "AN": "=H2*I2/1000000*0.74/J2",
    "AO": "=(36+2)*40/180",
}
#: 各公式列在 0903 里取样用的真实公式单元格（Spec §1）
EVIDENCE_CELLS = {"S": "S2", "U": "U2", "V": "V2", "X": "X2", "AH": "AH13",
                  "AI": "AI2", "AK": "AK5", "AN": "AN2", "AO": "AO15"}
HIDDEN_SHEETS = ("大货最终定价", "大货价核算1", "首批试产毛利核算")
RULES_EXIT = {"category_evidence_missing", "evidence_kind_unknown",
              "invented_formula_for_blank_column", "column_evidence_incomplete",
              "formula_without_evidence"}
EVIDENCE_KINDS = {"formula", "hand_filled", "no_formula_in_workbook"}


def load_cost_module():
    return importlib.import_module("tech_app.backend.services.packaging_cost")


def load_tool():
    if not TOOL_PY.exists() or not RULES_JSON.exists():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("pkg_extract_packaging_rules", TOOL_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_cells(**sheets):
    out = {}
    for name, payload in sheets.items():
        state, formulas, cached = payload
        out[name] = {"state": state, "formulas": dict(formulas), "cached": dict(cached)}
    return out


class EvidenceCase(unittest.TestCase):
    def rules(self):
        if not RULES_JSON.exists():
            self.fail("缺少 tech_app/agent_knowledge/rules/packaging_cost_rules.json（修复第 2 批）")
        return json.loads(RULES_JSON.read_text(encoding="utf-8"))

    def categories(self):
        rules = self.rules()
        self.assertIn("categories", rules, "快照必须新增 categories（Spec §2）")
        return {item["cost_category"]: item for item in rules["categories"]}

    def column_evidence(self):
        rules = self.rules()
        self.assertIn("column_evidence", rules, "快照必须新增 column_evidence（Spec §2）")
        return {item["source_column"]: item for item in rules["column_evidence"]}


# --------------------------------------------------------------------------- #
# A. categories 证据
# --------------------------------------------------------------------------- #
class ACategoryEvidence(EvidenceCase):
    def test_a1_categories_cover_runtime_categories_exactly(self):
        module = load_cost_module()
        expected = {code for code, _ in module.COST_CATEGORIES}
        expected |= {code for code, _ in module.PROJECT_COST_CATEGORIES}
        got = set(self.categories())
        self.assertEqual(len(got), 26)
        self.assertEqual(got, expected, "categories 必须恰好覆盖 24 + 2 个运行时类别")

    def test_a2_required_fields(self):
        for code, item in self.categories().items():
            for field in ("label", "level", "evidence_kind"):
                self.assertIn(field, item, "%s 缺字段 %s" % (code, field))

    def test_a3_labels_match_runtime(self):
        module = load_cost_module()
        labels = dict(module.COST_CATEGORIES)
        labels.update(dict(module.PROJECT_COST_CATEGORIES))
        for code, item in self.categories().items():
            self.assertEqual(item["label"], labels[code], "%s 的中文名必须与运行时逐字一致" % code)

    def test_a4_levels_match_runtime(self):
        module = load_cost_module()
        part = {code for code, _ in module.COST_CATEGORIES}
        for code, item in self.categories().items():
            self.assertEqual(item["level"], "part" if code in part else "project",
                             "%s 的 level 必须与运行时一致" % code)

    def test_a5_evidence_kind_is_a_closed_set(self):
        for code, item in self.categories().items():
            self.assertIn(item["evidence_kind"], EVIDENCE_KINDS,
                          "%s 的 evidence_kind 不在闭集里" % code)

    def test_a6_formula_columns_are_marked_as_formula(self):
        items = self.categories()
        for column, (code, _label, _rows, _blank) in FORMULA_COLUMNS.items():
            item = items[code]
            self.assertEqual(item["evidence_kind"], "formula",
                             "%s 在 0903 里有公式，必须标 formula" % code)
            self.assertEqual(item["source_sheet"], "报价-工费率")
            self.assertEqual(item["source_column"], column)
            cell = str(item.get("source_cell") or "")
            self.assertEqual(cell, EVIDENCE_CELLS[column],
                             "%s 的证据单元格必须取工作簿里真有公式的那一格" % code)
            self.assertTrue(cell.startswith(column), "%s 的证据单元格必须落在该列" % code)

    def test_a7_blank_columns_have_no_formula_code(self):
        module = load_cost_module()
        items = self.categories()
        for column, code in BLANK_COLUMNS.items():
            item = items[code]
            self.assertEqual(item["evidence_kind"], "no_formula_in_workbook",
                             "%s 在 0903 里 0 公式 0 手填，必须如实登记" % code)
            self.assertEqual(item.get("formula_code") or "", "",
                             "%s 没有证据，不许挂公式码" % code)
            self.assertNotIn(code, {entry["cost_category"]
                                    for entry in module.FORMULA_CATALOG.values()},
                             "%s 不许凭空造公式（Spec §2.1）" % code)

    def test_a8_part_level_formula_evidence_has_a_formula(self):
        module = load_cost_module()
        catalog = {entry["cost_category"] for entry in module.FORMULA_CATALOG.values()}
        for code, item in self.categories().items():
            if item["level"] != "part" or item["evidence_kind"] != "formula":
                continue
            self.assertIn(code, catalog,
                          "%s 标了 formula 就必须在 FORMULA_CATALOG 里有实现" % code)

    def test_a9_hidden_sheets_are_never_a_source(self):
        text = RULES_JSON.read_text(encoding="utf-8")
        for name in HIDDEN_SHEETS:
            self.assertNotIn(name, text, "隐藏 Sheet %s 不得作为证据来源" % name)

    def test_a10_no_hand_filled_evidence_in_this_workbook(self):
        for code, item in self.categories().items():
            self.assertNotEqual(item["evidence_kind"], "hand_filled",
                                "%s：采用口径第 2–15 行没有手填数字，不该标 hand_filled" % code)


# --------------------------------------------------------------------------- #
# B. column_evidence
# --------------------------------------------------------------------------- #
class BColumnEvidence(EvidenceCase):
    def test_b1_covers_all_24_columns(self):
        module = load_cost_module()
        self.assertEqual(set(self.column_evidence()),
                         {entry["source_column"] for entry in self.column_evidence().values()})
        self.assertEqual(len(self.column_evidence()), 24, "必须覆盖 24 个成本列")
        self.assertEqual(list(self.column_evidence()),
                         [item["source_column"] for item in self.rules()["column_evidence"]])
        self.assertEqual([item["cost_category"] for item in self.rules()["column_evidence"]],
                         [code for code, _ in module.COST_CATEGORIES],
                         "column_evidence 必须按 S→AP 列序与 COST_CATEGORIES 对齐")

    def test_b2_row_counts_add_up(self):
        rules = self.rules()
        rows = rules.get("source_rows")
        self.assertEqual(rows, 14, "快照必须写明 source_rows = 14（Spec §2）")
        for item in rules["column_evidence"]:
            total = item["formula_rows"] + item["blank_rows"] + item["hand_filled_rows"]
            self.assertEqual(total, rows, "%s 列三项行数之和必须等于 source_rows"
                             % item["source_column"])

    def test_b3_formula_row_counts_match_the_workbook(self):
        evidence = self.column_evidence()
        for column, (_code, _label, formula_rows, _blank) in FORMULA_COLUMNS.items():
            self.assertEqual(evidence[column]["formula_rows"], formula_rows,
                             "%s 列公式行数必须与工作簿一致" % column)

    def test_b4_blank_row_counts_match_the_workbook(self):
        evidence = self.column_evidence()
        for column, (_code, _label, _formula_rows, blank_rows) in FORMULA_COLUMNS.items():
            self.assertEqual(evidence[column]["blank_rows"], blank_rows,
                             "%s 列空行数必须与工作簿一致" % column)

    def test_b5_sample_formulas_are_transcribed(self):
        evidence = self.column_evidence()
        for column, formula in SAMPLE_FORMULAS.items():
            self.assertEqual(evidence[column]["sample_formula"], formula,
                             "%s 列的样例公式必须与应用口径逐字一致" % column)
            self.assertTrue(str(evidence[column]["sample_cell"]).startswith(column))

    def test_b6_blank_columns_have_no_sample(self):
        evidence = self.column_evidence()
        for column in BLANK_COLUMNS:
            item = evidence[column]
            self.assertEqual(item["formula_rows"], 0, "%s 列确实 0 公式" % column)
            self.assertEqual(item["blank_rows"], 14)
            self.assertEqual(item["sample_formula"], "")
            self.assertEqual(item["sample_cell"], "")

    def test_b7_headers_match_runtime_labels(self):
        module = load_cost_module()
        labels = dict(module.COST_CATEGORIES)
        for item in self.rules()["column_evidence"]:
            self.assertEqual(str(item["header"]).replace("\n", "").replace("/", "/"),
                             labels[item["cost_category"]].replace("\n", ""),
                             "%s 列的列头必须与运行时中文名一致" % item["source_column"])

    def test_b8_source_sheet_is_the_adopted_one(self):
        for item in self.rules()["column_evidence"]:
            self.assertEqual(item["source_sheet"], "报价-工费率",
                             "逐列证据只能取采用口径的 Sheet")


# --------------------------------------------------------------------------- #
# C. 对账工具的证据校验
# --------------------------------------------------------------------------- #
class CToolEvidenceChecks(EvidenceCase):
    def tool(self):
        module = load_tool()
        self.assertIsNotNone(module, "缺少 extract_packaging_rules.py 或规则快照（Spec §3）")
        return module

    def synthetic_rules(self, **over):
        rules = {
            "rule_set": "packaging_cost_evidence_test", "source_file": "synthetic.xlsx",
            "source_sha256": "sha", "source_sheets": ["报价-工费率"],
            "review_status": "reviewed", "generated_at": "2026-09-20", "source_rows": 1,
            "formulas": [{"formula_code": "PKG-C-GLUE", "cost_category": "glue",
                          "expression": "machine_length*machine_width/1000000*glue_unit_price/"
                                        "imposition_count",
                          "minimum_charge": 0, "rounding": 4, "rate_code": "", "loss_scope": "胶水",
                          "source_sheet": "报价-工费率", "source_cell": "AN2",
                          "verify_inputs": {"machine_length": 889, "machine_width": 700,
                                            "imposition_count": 1, "glue_unit_price": 0.74},
                          "expected_result": 0.46050199999999997,
                          "formula_version": "packaging_cost_evidence_test"}],
            "categories": [{"cost_category": "glue", "label": "胶水", "level": "part",
                            "evidence_kind": "formula", "source_sheet": "报价-工费率",
                            "source_cell": "AN2", "source_column": "AN",
                            "formula_code": "PKG-C-GLUE"}],
            "column_evidence": [{"source_sheet": "报价-工费率", "source_column": "AN",
                                 "header": "胶水", "formula_rows": 1, "blank_rows": 0,
                                 "hand_filled_rows": 0, "sample_cell": "AN2",
                                 "sample_formula": "=H2*I2/1000000*0.74/J2",
                                 "cost_category": "glue"}],
        }
        rules.update(over)
        return rules

    def cells(self):
        return synthetic_cells(**{"报价-工费率": (
            "visible", {"AN2": "=H2*I2/1000000*0.74/J2"}, {"AN2": 0.46050199999999997})})

    def audit(self, rules=None, cells=None, **over):
        module = self.tool()
        rules = rules or self.synthetic_rules()
        kwargs = {"workbook_sha256": rules["source_sha256"], "workbook_name": "synthetic.xlsx",
                  "catalog_codes": {"PKG-C-GLUE"},
                  "runtime_categories": {"glue": ("胶水", "part")}}
        kwargs.update(over)
        return module.audit_rules(cells or self.cells(), rules, **kwargs)

    def codes(self, report):
        return {item["code"] for item in report.get("problems") or []}

    def test_c1_consistent_evidence_passes(self):
        report = self.audit()
        self.assertTrue(report["ok"], report.get("problems"))
        self.assertEqual(report["exit_code"], 0)

    def test_c2_missing_category_evidence_is_reported(self):
        rules = self.synthetic_rules(categories=[])
        self.assertIn("category_evidence_missing", self.codes(self.audit(rules)))

    def test_c3_unknown_evidence_kind_is_reported(self):
        rules = self.synthetic_rules()
        rules["categories"][0]["evidence_kind"] = "guessed"
        self.assertIn("evidence_kind_unknown", self.codes(self.audit(rules)))

    def test_c4_formula_evidence_without_formula_cell_fails(self):
        rules = self.synthetic_rules()
        cells = synthetic_cells(**{"报价-工费率": ("visible", {}, {"AN2": 0.4605})})
        self.assertIn("evidence_cell_has_no_formula", self.codes(self.audit(rules, cells)))

    def test_c5_runtime_formula_without_evidence_is_reported(self):
        report = self.audit(catalog_codes={"PKG-C-GLUE", "PKG-C-LAMINATION"},
                            runtime_categories={"glue": ("胶水", "part"),
                                                "lamination": ("复膜", "part")})
        self.assertIn("formula_without_evidence", self.codes(report))

    def test_c6_invented_formula_for_blank_column_is_reported(self):
        rules = self.synthetic_rules()
        rules["categories"].append({"cost_category": "silk_screen", "label": "丝印", "level": "part",
                                    "evidence_kind": "no_formula_in_workbook",
                                    "source_sheet": "报价-工费率", "source_column": "AA",
                                    "formula_code": "PKG-C-SILK"})
        rules["column_evidence"].append({"source_sheet": "报价-工费率", "source_column": "AA",
                                         "header": "丝印", "formula_rows": 1, "blank_rows": 0,
                                         "hand_filled_rows": 0, "sample_cell": "AA2",
                                         "sample_formula": "=1", "cost_category": "silk_screen"})
        report = self.audit(rules, catalog_codes={"PKG-C-GLUE"},
                            runtime_categories={"glue": ("胶水", "part"),
                                                "silk_screen": ("丝印", "part")})
        self.assertIn("invented_formula_for_blank_column", self.codes(report))

    def test_c7_incomplete_column_evidence_is_reported(self):
        rules = self.synthetic_rules()
        rules["column_evidence"][0]["blank_rows"] = 5
        self.assertIn("column_evidence_incomplete", self.codes(self.audit(rules)))

    def test_c8_evidence_codes_use_the_documented_exit_codes(self):
        rules = self.synthetic_rules(categories=[])
        report = self.audit(rules)
        self.assertEqual(report["exit_code"], 1)
        self.assertTrue(self.codes(report) & RULES_EXIT)


# --------------------------------------------------------------------------- #
# D. 回归守卫
# --------------------------------------------------------------------------- #
class DRegressionGuards(EvidenceCase):
    def test_d1_blank_categories_still_return_a_gap_not_zero(self):
        module = load_cost_module()
        for code in ("silk_screen", "texture", "auto_mount", "varnish"):
            line = module.compute_line(code, {"quote_quantity": 1000})
            self.assertIsNone(line["amount"], "%s 没有公式证据，不许给金额" % code)
            self.assertEqual(line["gap"]["code"], "no_formula:%s" % code)

    def test_d2_batch1_frozen_numbers_still_hold(self):
        module = load_cost_module()
        line = module.compute_line("lamination", {
            "machine_length": 889, "machine_width": 700, "imposition_count": 1,
            "setup_minutes": 30, "capacity_per_hour": 5500, "equipment_rate": 197,
            "labor_rate": 145, "film_price": 1.7, "film_thickness_um": 18,
            "film_kg_price": 18.5, "tax_factor": 1.13, "quote_quantity": 1000})
        self.assertAlmostEqual(line["amount"], 1.3766112580048271, places=6)
        # 2026-09-21 口径裁决取 ② 报价-工费率（主行无门限），门限归零
        # （见 docs/specs/packaging-cost-minimum-charge-decision.md §4）。
        for code, expected in (("PKG-C-LAMINATION", 0), ("PKG-C-DIE-CUT", 0)):
            self.assertEqual(module.FORMULA_CATALOG[code]["minimum_charge"], expected)

    def test_d3_snapshot_formulas_are_unchanged_by_this_batch(self):
        module = load_cost_module()
        for item in self.rules()["formulas"]:
            entry = module.FORMULA_CATALOG[item["formula_code"]]
            self.assertEqual(item["expression"], entry["expression"],
                             "%s 的表达式本批不许改" % item["formula_code"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
