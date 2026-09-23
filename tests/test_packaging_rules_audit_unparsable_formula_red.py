"""红测：规则对账"读不懂这条公式"不许说成"你抄错了"（`unparsable_formula`）。

Spec：`docs/specs/packaging-rules-audit-unparsable-formula.md`。
血缘：`packaging-cost-minimum-charge.md` §5.3（`verbatim_compare` 的逐字等价与退出码 2）、§7（码表）。

现状缺口（首次运行**必须失败**，是实测不是推断）：
  · `verbatim_compare()` 在任一侧规范化失败时给 `reason = "unparsable"`，但工具
    `extract_packaging_rules.py` 只判 `not equivalent` → 读不懂被折成 `source_cell_mismatch`
    （退出码 2「来源或引用损坏」，文案「改写不等于等价」）；
  · 于是"解析器读不懂区间 `:`"与"快照漏写 `source_formula`"两种现实，跟"真的改写了公式"
    在报告里**逐字同形**；
  · `verbatim_compare()` 手里有答案（`resolved` / `source` 哪一侧为空），却没有字段暴露出来，
    工具也没看 `reason`。

全部离线：纯函数 + 合成夹具（不读工作簿、不读快照、不连库、不起服务）。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOOL_PY = ROOT / "tech_app" / "tools" / "extract_packaging_rules.py"
SPEC_MD = ROOT / "docs" / "specs" / "packaging-cost-minimum-charge.md"

GLUE_EXPRESSION = "machine_length*machine_width/1000000*glue_unit_price/imposition_count"
GLUE_SOURCE = "=H2*I2/1000000*0.74/J2"
GLUE_EXPECTED = 0.46050199999999997
GLUE_VARIABLE_MAP = {"machine_length": "H", "machine_width": "I", "imposition_count": "J"}
GLUE_VERIFY_INPUTS = {"machine_length": 889, "machine_width": 700, "imposition_count": 1,
                      "glue_unit_price": 0.74}
POLICY_IDS = ("sheet_industry_standard", "sheet_labor_rate", "declared_hybrid")
RANGE_SOURCE = "=SUM(H2:H10)/J2"


def load_tool():
    if not TOOL_PY.exists():
        return None
    spec = importlib.util.spec_from_file_location("pkg_rules_audit_unparsable", TOOL_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rule(**over):
    item = {"formula_code": "PKG-C-GLUE", "cost_category": "glue",
            "expression": GLUE_EXPRESSION,
            "minimum_charge": 0, "rounding": 4, "rate_code": "", "loss_scope": "胶水",
            "source_sheet": "报价-工费率", "source_cell": "AN2",
            "source_ref": "synthetic.xlsx/报价-工费率/AN2",
            "minimum_charge_source_ref": "",
            "variable_map": dict(GLUE_VARIABLE_MAP),
            "verify_inputs": dict(GLUE_VERIFY_INPUTS),
            "expected_result": GLUE_EXPECTED, "formula_version": "packaging_cost_v1",
            "source_formula": GLUE_SOURCE}
    item.update(over)
    return item


def rules_for(item):
    return {"rule_set": "packaging_cost_v1", "source_file": "synthetic.xlsx",
            "source_sha256": "synthetic-sha", "source_sheets": ["报价-工费率"],
            "review_status": "reviewed", "generated_at": "2026-09-20",
            "formulas": [item],
            "minimum_charge_policy": {
                "status": "pending", "chosen": "", "decided_by": "", "decided_at": "",
                "candidates": [
                    {"policy_id": policy_id,
                     "authoritative_sheet": "" if policy_id == "declared_hybrid" else (
                         "报价-行业标准" if policy_id == "sheet_industry_standard"
                         else "报价-工费率"),
                     "is_declared_hybrid": policy_id == "declared_hybrid",
                     "rationale": "synthetic", "golden": {}, "red_test_impact": []}
                    for policy_id in POLICY_IDS]}}


def cells_from(formulas, cached=None):
    return {"报价-工费率": {"state": "visible", "formulas": dict(formulas),
                            "cached": dict(cached or {key: GLUE_EXPECTED for key in formulas})}}


class Base(unittest.TestCase):
    def tool(self):
        module = load_tool()
        self.assertIsNotNone(module,
                             "缺少 tech_app/tools/extract_packaging_rules.py（对账工具）")
        self.assertTrue(callable(getattr(module, "audit_rules", None)))
        return module

    def audit(self, item, formulas, cached=None):
        module = self.tool()
        rules = rules_for(item)
        report = module.audit_rules(cells_from(formulas, cached), rules,
                                    workbook_sha256=rules["source_sha256"],
                                    workbook_name="synthetic.xlsx",
                                    catalog_codes={"PKG-C-GLUE"})
        return report

    def codes(self, report):
        return {entry["code"] for entry in report.get("problems") or []}

    def details(self, report):
        return " / ".join(str(entry.get("detail") or "")
                          for entry in report.get("problems") or [])


# --------------------------------------------------------------------------- #
# U1–U3：读不懂 ≠ 不等价
# --------------------------------------------------------------------------- #
class UUnparsableIsNotAMismatch(Base):
    def test_u1_source_formula_with_a_range_is_not_a_rewrite(self):
        report = self.audit(rule(), {"AN2": RANGE_SOURCE})
        codes = self.codes(report)
        self.assertIn("unparsable_formula", codes,
                      "来源原文解析不出规范串时必须报 unparsable_formula（读不懂）：%r" % (codes,))
        self.assertNotIn("source_cell_mismatch", codes,
                         "读不懂不许说成「改写不等于等价」（Spec §2.2）")
        self.assertEqual(report["exit_code"], 1,
                         "读不懂是「口径/校验不一致」（1），不是「来源或引用损坏」（2）")

    def test_u2_detail_names_the_side_that_cannot_be_parsed(self):
        report = self.audit(rule(), {"AN2": RANGE_SOURCE})
        detail = self.details(report)
        self.assertIn("unparsable_formula", self.codes(report))
        self.assertIn("source", detail,
                      "detail 必须点名是哪一边读不懂（expression / source / expression+source）：%r"
                      % detail)
        self.assertNotIn("改写不等于等价", detail,
                         "读不懂的那条不许再借用不等价的文案：%r" % detail)

    def test_u3_snapshot_without_source_formula_is_not_a_rewrite(self):
        report = self.audit(rule(source_formula=""), {"AN2": GLUE_SOURCE})
        codes = self.codes(report)
        self.assertIn("unparsable_formula", codes,
                      "快照漏写 source_formula 时也是「没能校验」，不许报成抄错：%r" % (codes,))
        self.assertNotIn("source_cell_mismatch", codes,
                         "快照漏字段 ≠ 改写了公式（Spec §1 第三行）")
        self.assertEqual(report["exit_code"], 1)


# --------------------------------------------------------------------------- #
# U4：verbatim_compare 要把"哪一侧读不懂"交出来
# --------------------------------------------------------------------------- #
class UVerbatimCompareExposesTheSide(Base):
    def compare(self):
        from tech_app.backend.services import packaging_cost
        return packaging_cost.verbatim_compare

    def test_u4_unparsable_side_is_reported(self):
        compare = self.compare()
        vm = dict(GLUE_VARIABLE_MAP)
        only_source = compare(GLUE_EXPRESSION, RANGE_SOURCE, vm, "AN2",
                              {"glue_unit_price": 0.74})
        self.assertEqual(only_source.get("reason"), "unparsable")
        self.assertEqual(only_source.get("unparsable"), ["source"],
                         "只来源侧读不懂 → unparsable == ['source']（Spec §2.1）")
        only_expression = compare("machine_length*H2:H10/imposition_count", GLUE_SOURCE, vm, "AN2")
        self.assertEqual(only_expression.get("reason"), "unparsable")
        self.assertEqual(only_expression.get("unparsable"), ["expression"],
                         "只表达式侧读不懂 → unparsable == ['expression']")
        both = compare("machine_length*H2:H10/imposition_count", RANGE_SOURCE, vm, "AN2")
        self.assertEqual(both.get("unparsable"), ["expression", "source"],
                         "两侧都读不懂时顺序固定：先 expression 后 source")
        clean = compare(GLUE_EXPRESSION, GLUE_SOURCE, vm, "AN2", {"glue_unit_price": 0.74})
        self.assertEqual(clean.get("unparsable"), [],
                         "两侧都读得懂 → unparsable == []（含等价与不等价两种）")
        self.assertEqual(clean.get("reason"), "",
                         "既有键语义不变：等价时 reason 仍是空串")


# --------------------------------------------------------------------------- #
# U5：码表要跟着说（源码 + 文档守卫）
# --------------------------------------------------------------------------- #
class UCatalogAndDoc(Base):
    def test_u5_tool_and_spec_declare_the_new_code(self):
        head = "\n".join(TOOL_PY.read_text(encoding="utf-8").splitlines()[:60])
        self.assertIn("unparsable_formula", head,
                      "工具 docstring 头部的判定清单必须列出 unparsable_formula（Spec §2.3）")
        spec = SPEC_MD.read_text(encoding="utf-8")
        rows = [line for line in spec.splitlines()
                if line.strip().startswith("|") and "unparsable_formula" in line]
        self.assertTrue(rows, "docs/specs/packaging-cost-minimum-charge.md §7 码表要补这一行")
        self.assertTrue(any("| 1 |" in line for line in rows),
                        "码表里 unparsable_formula 的退出码列必须是 1：%r" % rows)


# --------------------------------------------------------------------------- #
# U6–U8：既有路径一条都不许变（护栏，今天就该绿）
# --------------------------------------------------------------------------- #
class UExistingPathsUnchanged(Base):
    def test_u6_real_mismatch_keeps_its_code_and_exit(self):
        report = self.audit(rule(source_formula="=H2*I2/1000000*0.75/J2"),
                            {"AN2": "=H2*I2/1000000*0.75/J2"})
        codes = self.codes(report)
        self.assertIn("source_cell_mismatch", codes)
        self.assertNotIn("unparsable_formula", codes,
                         "两边都解析得出、规范串不同 → 仍是不等价，不许报成读不懂")
        self.assertEqual(report["exit_code"], 2)

    def test_u7_unmapped_variable_still_wins(self):
        report = self.audit(rule(expression="machine_length*machine_width/1000000*nope/imposition_count"),
                            {"AN2": GLUE_SOURCE})
        codes = self.codes(report)
        self.assertIn("unmapped_variable", codes)
        self.assertNotIn("unparsable_formula", codes,
                         "未映射变量优先，同一处不叠加读不懂（Spec §2.2）")

    def test_u8_clean_snapshot_and_missing_cell_path_unchanged(self):
        clean = self.audit(rule(), {"AN2": GLUE_SOURCE})
        self.assertTrue(clean["ok"], clean.get("problems"))
        self.assertEqual(clean["exit_code"], 0)
        missing = self.audit(rule(source_cell="ZZ99",
                                  source_ref="synthetic.xlsx/报价-工费率/ZZ99"), {})
        self.assertIn("source_cell_has_no_formula", self.codes(missing),
                      "该格根本没有公式这条路径一字不动")
        self.assertEqual(missing["exit_code"], 2)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main(verbosity=2)
