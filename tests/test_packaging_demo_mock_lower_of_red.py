"""包装演示 mock 以低值为准（Spec `packaging-demo-mock-lower-of.md`）。

纯离线：只用脚本里的离线快照、酒盒实际 BOM 锚点与两张工作簿的逐格吨价；不连库、不写库。
"""
import unittest
from pathlib import Path

from scripts.packaging_demo_kb_relower import (
    CLOSEST_BOM_MATERIAL, EFFECTIVE_FROM, RULE, SNAPSHOT, SOURCE, WINE_BOX_BOM,
    WORKBOOK_TON_PRICES, anchor_of, decide, plan)
from tech_app.backend.services.packaging_material_match import score_candidate

#: 三族重定后的目标价（元/kg）= min(现值, 同族吨价) —— Spec §2 表格。
EXPECTED_LOWER = {"greyboard": 3.95, "face_paper": 6.3, "white_card": 6.7}

#: 酒盒 BOM 里没有对口、两张工作簿也没有可比吨价 → 保持现值、不进计划。
UNCHANGED_CODES = ("MAT-PKG-LINER", "MAT-PKG-DEMO-LINER", "MAT-PKG-SPECIAL",
                   "MAT-PKG-EVA", "MAT-PKG-DEMO-EVA35", "MAT-PKG-DEMO-TUBE",
                   "MAT-PKG-DEMO-MAGNET")


class DemoMockLowerOf(unittest.TestCase):
    def setUp(self):
        self.planned = plan()
        self.rows = {row["material_code"]: row for row in self.planned["kb_material_price"]}
        self.decisions = {row["material_code"]: row for row in self.planned["decisions"]}

    def test_decisions_cover_every_packaging_mock_material(self):
        self.assertEqual(set(SNAPSHOT), set(self.decisions))
        self.assertGreater(len(SNAPSHOT), 29)

    def test_every_declared_anchor_is_the_closest_wine_box_bom_material(self):
        for code, bom in CLOSEST_BOM_MATERIAL.items():
            if not bom:
                continue
            name = SNAPSHOT[code][0]
            best = max(((score_candidate(name, {"name": b, "material_code": b})[0], b)
                        for b in WINE_BOX_BOM))
            self.assertEqual(best[1], bom, "%s 锚错：应锚 %s" % (code, best[1]))
            self.assertEqual(bom, self.decisions[code]["bom_material"], code)

    def test_anchor_carries_material_family_and_the_bom_process(self):
        for code, bom in CLOSEST_BOM_MATERIAL.items():
            if not bom:
                continue
            self.assertEqual(WINE_BOX_BOM[bom][0], self.decisions[code]["family"], code)
            self.assertEqual(WINE_BOX_BOM[bom][1], self.decisions[code]["bom_process"], code)
            self.assertTrue(self.decisions[code]["anchor_source"], code)

    def test_only_anchored_families_change_and_target_is_the_low(self):
        for code, decision in self.decisions.items():
            if decision["family"] in EXPECTED_LOWER:
                self.assertTrue(decision["changed"], code)
                self.assertEqual(EXPECTED_LOWER[decision["family"]],
                                 decision["target_price_kg"], code)
            else:
                self.assertFalse(decision["changed"], code)
                self.assertEqual(decision["current_price_kg"],
                                 decision["target_price_kg"], code)

    def test_target_never_exceeds_current_price(self):
        for decision in self.decisions.values():
            self.assertLessEqual(decision["target_price_kg"],
                                 decision["current_price_kg"] + 1e-9)

    def test_chosen_reference_is_the_minimum_and_rejected_keeps_the_rest(self):
        for code, decision in self.decisions.items():
            ton_prices = [item["ton_price"]
                          for item in [{"ton_price": decision["current_price_kg"] * 1000}]
                          + list(decision["rejected"])]
            self.assertLessEqual(decision["target_price_kg"] * 1000,
                                 min(ton_prices) + 1e-6, code)
            if decision["family"] in WORKBOOK_TON_PRICES:
                self.assertNotEqual("kb_material_price/%s" % code, decision["chosen_ref"], code)

    def test_unchanged_codes_stay_out_of_the_plan(self):
        for code in UNCHANGED_CODES:
            self.assertNotIn(code, self.rows)

    def test_plan_rows_are_append_only_lower_prices_with_traceable_evidence(self):
        self.assertEqual(23, len(self.rows))
        for code, row in self.rows.items():
            self.assertEqual(SOURCE, row["source_name"])
            self.assertEqual("kg", row["unit"])
            self.assertEqual("CNY", row["currency"])
            self.assertEqual(EFFECTIVE_FROM, row["valid_from"])
            self.assertNotIn("price_id", row)
            self.assertIn(RULE, row["evidence"])
            self.assertIn(self.decisions[code]["chosen_ref"], row["evidence"])
            self.assertLess(row["price"], SNAPSHOT[code][1])
        # 每条有锚点的计划行都要把「锚到哪条 BOM 材料 + 走什么工序」写进留痕。
        for code in self.rows:
            decision = self.decisions[code]
            if decision["bom_material"]:
                self.assertIn(decision["bom_material"], self.rows[code]["evidence"], code)
                self.assertIn(decision["bom_process"], self.rows[code]["evidence"], code)

    def test_source_is_a_new_tag_and_supersedes_the_earlier_batch(self):
        self.assertEqual("packaging_demo_20261014_bom_anchor", SOURCE)
        self.assertNotIn(SOURCE, ("packaging_demo_20261012_mock_not_production",
                                  "packaging_demo_20261014_lower_of"))
        self.assertGreater(EFFECTIVE_FROM, "2026-10-14 00:00:00")

    def test_identity_of_grammage_and_family_mapping_are_not_renamed(self):
        for code, (name, _price) in SNAPSHOT.items():
            self.assertEqual(name, self.decisions[code]["name"])

    def test_decide_is_pure_and_repeatable(self):
        first = decide(code="MAT-PKG-FACEPAPER", name="铜版纸面纸", current_price_kg=12.8)
        second = decide(code="MAT-PKG-FACEPAPER", name="铜版纸面纸", current_price_kg=12.8)
        self.assertEqual(first, second)
        self.assertAlmostEqual(6.3, first["target_price_kg"])
        self.assertEqual("报价逻辑-0903.xlsx/报价-工费率!N2", first["chosen_ref"])
        self.assertEqual("255G太阳铜版底PET光银", first["bom_material"])
        # 白卡底PET光银按承印基材进 white_card，不按名字里的 PET/光银进 face_paper。
        card = decide(code="MAT-PKG-DEMO-WHITE325", name="325G白卡底PET光银", current_price_kg=12.8)
        self.assertEqual("white_card", card["family"])
        self.assertAlmostEqual(6.7, card["target_price_kg"])

    def test_no_anchor_keeps_the_current_price_untouched(self):
        kept = decide(code="MAT-PKG-DEMO-TUBE", name="纸管", current_price_kg=9.0)
        self.assertEqual("", kept["bom_material"])
        self.assertFalse(kept["changed"])
        self.assertEqual(9.0, kept["target_price_kg"])

    def test_anchor_of_is_a_pure_lookup(self):
        self.assertEqual(anchor_of("MAT-PKG-DEMO-GREY350")["bom_material"], "350G玖龙粉灰")
        self.assertEqual(anchor_of("MAT-PKG-DEMO-GREY350")["family"], "greyboard")
        self.assertEqual(anchor_of("MAT-PKG-DEMO-TUBE")["bom_material"], "")

    def test_spec_documents_the_same_numbers(self):
        spec = Path("docs/specs/packaging-demo-mock-lower-of.md").read_text(encoding="utf-8")
        self.assertIn("packaging_demo_20261014_bom_anchor", spec)
        self.assertIn("3.95", spec)
        self.assertIn("6.30", spec)
        self.assertIn("6.70", spec)
        self.assertIn("350G玖龙粉灰", spec)


if __name__ == "__main__":
    unittest.main()
