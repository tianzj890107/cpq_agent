"""红测：包装最低收费口径裁决（② sheet_labor_rate）的落地 —— 裁决记录 + 口径变更。

Spec：`docs/specs/packaging-cost-minimum-charge-decision.md`
上游：`docs/specs/packaging-cost-minimum-charge.md`（三套候选与实测证据）

**现状缺口（首次运行时必须失败，实测不是推断）**：

  · `packaging_cost_rules.json` 的 `minimum_charge_policy.status` 仍是 `pending`，
    `chosen` / `decided_by` / `decided_at` 全空 —— 运行时只能标 `unresolved` 并回退，
    对外没有任何口径结论；
  · `FORMULA_CATALOG` 里四个码仍带着混合口径的门限（复膜 200 / 烫金 150 / 啤切 100 /
    V 槽 150），门限出处其实是 `报价-行业标准`，而表达式出处是 `报价-工费率`；
  · `_MIN_CHARGE_DECISIONS` 只登记了 `PKG-C-V-GROOVE` 一条，且 `owner` / `decided_at` 为空。

裁决已由业务在 2026-09-21 给出（见 Spec §1：选 ②，真实成本口径），本文件把裁决的
**每一条后果**变成断言。裁决前这些断言是红的；裁决落地后必须全绿，且**断言文本不再改**。
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

#: 裁决（Spec §1/§2）—— 逐字取自业务裁决，实现方不许改。
DECIDED_BY = "张真"
DECIDED_AT = "2026-09-21"
CHOSEN = "sheet_labor_rate"
THRESHOLDED = ("PKG-C-LAMINATION", "PKG-C-HOT-STAMP-FLAT", "PKG-C-DIE-CUT", "PKG-C-V-GROOVE")
#: 第 1 批冻结值：裁决不改它，只作为"曾经冻结过什么"的证据保留。
FROZEN = {"PKG-C-LAMINATION": 200, "PKG-C-HOT-STAMP-FLAT": 150,
          "PKG-C-DIE-CUT": 100, "PKG-C-V-GROOVE": 120}

#: ② 口径下必须复现的值（实测：把四个码的 minimum_charge 归零后逐条跑出来的）。
LABOR_RATE_GOLDEN = {"lamination": 1.3766112580048271,
                     "hot_stamp_flat": 1.3432666666666671,
                     "die_cutting": 0.8347876923076923,
                     "v_groove": 0.816}
LABOR_RATE_LOW_EXPRESSION = {"q100": 1.772916788093, "q1000": 0.233916788093,
                             "q10000": 0.080016788093}
DIE_CUT_100 = 7.811227692308
V_GROOVE_FROZEN = 120

LAMINATION = {"machine_length": 889, "machine_width": 700, "imposition_count": 1,
              "setup_minutes": 30, "capacity_per_hour": 5500, "equipment_rate": 197,
              "labor_rate": 145, "film_price": 1.7, "film_thickness_um": 18,
              "film_kg_price": 18.5, "tax_factor": 1.13}
DIE_BASE = {"imposition_count": 1, "setup_minutes": 120, "capacity_per_hour": 6500,
            "equipment_rate": 197.52, "labor_rate": 190.06}


def cost_module():
    try:
        return importlib.import_module("tech_app.backend.services.packaging_cost")
    except Exception as exc:                                   # noqa: BLE001
        raise unittest.SkipTest("成本引擎不可导入：%s" % exc)


def rules_json():
    return json.loads(RULES_JSON.read_text(encoding="utf-8"))


def rule_by_code(code):
    for item in rules_json().get("formulas") or []:
        if str(item.get("formula_code") or "") == code:
            return item
    return {}


# --------------------------------------------------------------------------- #
# A. 运行时：门限归零，冻结值留证
# --------------------------------------------------------------------------- #
class ARuntimeDecision(unittest.TestCase):
    def test_a1_policy_is_chosen_as_labor_rate(self):
        info = cost_module().minimum_charge_policy()
        self.assertEqual(info["status"], "chosen", "裁决必须落档（Spec §2）")
        self.assertEqual(info["policy"], CHOSEN, "所选口径必须是 ② 报价-工费率")
        self.assertEqual(info["decided_by"], DECIDED_BY)
        self.assertEqual(info["decided_at"], DECIDED_AT)
        self.assertEqual(info["fallback"], "", "已裁决就不许再标回退口径（Spec C4）")

    def test_a2_runtime_minimum_charges_are_zero(self):
        catalog = cost_module().FORMULA_CATALOG
        for code in THRESHOLDED:
            self.assertEqual(float(catalog[code]["minimum_charge"]), 0.0,
                             "② 主行无门限：%s 的 minimum_charge 必须归零（Spec C1）" % code)

    def test_a3_frozen_values_are_preserved(self):
        catalog = cost_module().FORMULA_CATALOG
        for code, expected in FROZEN.items():
            self.assertEqual(float(catalog[code].get("frozen_minimum_charge")), float(expected),
                             "%s 的第 1 批冻结值必须保留为证据，不得因裁决而抹掉（Spec C1）" % code)

    def test_a4_decision_registry_records_all_four(self):
        module = cost_module()
        registry = getattr(module, "_MIN_CHARGE_DECISIONS", None)
        self.assertIsInstance(registry, dict, "必须有冲突登记表（Spec C3）")
        for code in THRESHOLDED:
            self.assertIn(code, registry,
                          "%s 的 minimum_charge 已与冻结值不同，必须登记（Spec C3）" % code)
            entry = registry[code]
            for field in ("frozen", "current", "owner", "decided_at", "reason"):
                self.assertIn(field, entry, "%s 的登记缺 %s" % (code, field))
            self.assertEqual(float(entry["frozen"]), float(FROZEN[code]), code)
            self.assertEqual(float(entry["current"]), 0.0, code)
            self.assertEqual(str(entry["owner"]), DECIDED_BY,
                             "%s 的登记必须写上裁决人（不许留空）" % code)
            self.assertEqual(str(entry["decided_at"]), DECIDED_AT,
                             "%s 的登记必须写上裁决日期（不许留空）" % code)
            self.assertTrue(str(entry["reason"]).strip(), "%s 的登记必须写清依据" % code)

    def test_a5_only_the_four_codes_carry_a_threshold_history(self):
        catalog = cost_module().FORMULA_CATALOG
        others = [code for code, entry in catalog.items()
                  if float(entry.get("minimum_charge") or 0) > 0]
        self.assertEqual(others, [], "② 落地后不该还有任何码带正门限：%s" % others)


# --------------------------------------------------------------------------- #
# B. 规则快照：裁决落档，来源格声明清空
# --------------------------------------------------------------------------- #
class BDecisionRecord(unittest.TestCase):
    def test_b1_rules_json_records_the_decision(self):
        block = rules_json().get("minimum_charge_policy") or {}
        self.assertEqual(str(block.get("status") or ""), "chosen")
        self.assertEqual(str(block.get("chosen") or ""), CHOSEN)
        self.assertEqual(str(block.get("decided_by") or ""), DECIDED_BY)
        self.assertEqual(str(block.get("decided_at") or ""), DECIDED_AT)
        decisions = block.get("decisions") or []
        codes = {str(item.get("formula_code") or "") for item in decisions}
        self.assertEqual(codes, set(THRESHOLDED),
                         "裁决必须逐条列出这四个码（Spec C2）：%s" % sorted(codes))
        for item in decisions:
            self.assertTrue(str(item.get("reason") or "").strip(),
                            "%s 的裁决必须写依据" % item.get("formula_code"))

    def test_b2_rules_threshold_matches_runtime_and_source_ref_is_cleared(self):
        for code in THRESHOLDED:
            item = rule_by_code(code)
            self.assertTrue(item, "快照缺 %s" % code)
            self.assertEqual(float(item.get("minimum_charge") or 0), 0.0, code)
            self.assertEqual(str(item.get("minimum_charge_source_ref") or ""), "",
                             "%s 的门限已归零，就不许再声明门限来源格（Spec C1）" % code)

    def test_b3_batch3_candidates_are_kept(self):
        block = rules_json().get("minimum_charge_policy") or {}
        ids = sorted(str(item.get("policy_id") or "") for item in block.get("candidates") or [])
        self.assertEqual(ids, ["declared_hybrid", "sheet_industry_standard", "sheet_labor_rate"],
                         "三套候选是裁决依据，裁决后也不许删（Spec C2）")

    def test_b4_row_level_thresholds_are_preserved(self):
        variants = rule_by_code("PKG-C-DIE-CUT").get("row_variants") or []
        cells = sorted(str(item.get("source_cell") or "") for item in variants)
        self.assertEqual(cells, ["AI14", "AI15", "AI9"],
                         "报价-工费率 的行级门限是'主行无门限'的对照证据，不许删（Spec C2）")


# --------------------------------------------------------------------------- #
# C. ② 口径的数值后果：表达式说了算
# --------------------------------------------------------------------------- #
class CLaborRateNumbers(unittest.TestCase):
    def test_c1_low_expression_no_longer_hits_a_threshold(self):
        module = cost_module()
        line = module.compute_line("lamination", dict(LAMINATION, quote_quantity=1000,
                                                     machine_length=20, machine_width=20))
        self.assertFalse(line["min_charge_applied"],
                         "② 主行无门限：表达式再低也不许命中最低收费（Spec §2）")
        self.assertAlmostEqual(line["amount"], LABOR_RATE_LOW_EXPRESSION["q1000"], places=6,
                               msg="必须复现 报价-工费率 原文的表达式值")

    def test_c2_amounts_scale_by_quantity_without_a_floor(self):
        module = cost_module()
        for quantity, key in ((100, "q100"), (1000, "q1000"), (10000, "q10000")):
            line = module.compute_line("lamination", dict(LAMINATION, quote_quantity=quantity,
                                                         machine_length=20, machine_width=20))
            self.assertFalse(line["min_charge_applied"], "q=%d 不许命中门限" % quantity)
            self.assertAlmostEqual(line["amount"], LABOR_RATE_LOW_EXPRESSION[key], places=6,
                                   msg="q=%d 的表达式值必须与 ② 口径一致" % quantity)

    def test_c3_die_cut_has_no_main_row_threshold(self):
        module = cost_module()
        low = module.compute_line("die_cutting", dict(DIE_BASE, quote_quantity=100))
        self.assertFalse(low["min_charge_applied"], "啤/切主行也不许再有 100 件门限")
        self.assertAlmostEqual(low["amount"], DIE_CUT_100, places=6)
        high = module.compute_line("die_cutting", dict(DIE_BASE, quote_quantity=1000))
        self.assertFalse(high["min_charge_applied"])
        self.assertAlmostEqual(high["amount"], LABOR_RATE_GOLDEN["die_cutting"], places=6)


# --------------------------------------------------------------------------- #
# D. 不许顺手改的东西
# --------------------------------------------------------------------------- #
class DUnchanged(unittest.TestCase):
    def test_d1_batch7_golden_totals_still_hold(self):
        module = cost_module()
        inputs = {
            "lamination": dict(LAMINATION, quote_quantity=1000),
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
            self.assertAlmostEqual(line["amount"], LABOR_RATE_GOLDEN[name], places=6,
                                   msg="%s 在第 7 批冻结黄金值上必须逐字不变（Spec C5）" % name)

    def test_d2_expressions_are_untouched(self):
        module = cost_module()
        for code in THRESHOLDED:
            item = rule_by_code(code)
            self.assertEqual(item.get("expression"),
                             module.FORMULA_CATALOG[code]["expression"],
                             "%s 的表达式本批不许改（Spec C5）" % code)

    def test_d3_decision_does_not_shrink_the_catalog(self):
        module = cost_module()
        self.assertGreaterEqual(len(module.FORMULA_CATALOG), 20,
                                "公式目录不得因裁决而缩水")
        self.assertEqual(float(module.FORMULA_CATALOG["PKG-C-MOUNTING"]["minimum_charge"]), 0.0,
                         "裱纸本来就是 0，裁决不许把它改成别的")


if __name__ == "__main__":
    unittest.main(verbosity=2)
