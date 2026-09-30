"""先红后绿：圆盘盒方案、排模文字与多片部件边界。"""
from pathlib import Path
import unittest

from tech_app.backend.services import packaging_business_part_resolver as resolver
from tech_app.backend.services import packaging_layout
from tech_app.tools.split_round_box_schemes import scheme_for_bounds


ROOT = Path(__file__).resolve().parents[1]


class RoundBoxLayoutRed(unittest.TestCase):
    def test_split_keeps_scheme_bands_but_not_shared_border_or_shipping(self):
        self.assertEqual(scheme_for_bounds(7543, 10529), "upper")
        self.assertEqual(scheme_for_bounds(4491, 7347), "lower")
        self.assertEqual(scheme_for_bounds(3900, 4300), "excluded")
        self.assertEqual(scheme_for_bounds(7400, 7600), "excluded")

    def setUp(self):
        self.ir = {"texts": [
            {"entity_id": "left-a", "raw_text": "盖内圈面纸", "position": [100, 100], "layer": "TEXT"},
            {"entity_id": "left-b", "raw_text": "地盒底座面纸", "position": [200, 100], "layer": "TEXT"},
            {"entity_id": "right", "raw_text": "10PC圆盒 盖内圈面纸+地盒底座面纸：157g双铜 443*595mm 各排1模", "position": [1000, 100], "layer": "TEXT"},
            {"entity_id": "right-label", "raw_text": "盖内圈面纸", "position": [1100, 80], "layer": "TEXT"},
            {"entity_id": "right-2", "raw_text": "底盒底板灰板内衬裱卡：250g白卡 840*435mm 排2模", "position": [1200, 110], "layer": "TEXT"},
            {"entity_id": "scheme", "raw_text": "内托方案2", "position": [500, 300], "layer": "TEXT"},
        ]}

    def test_layout_text_is_not_a_business_part(self):
        anchors = resolver.extract_text_anchors(self.ir, layout_aware=True)
        self.assertEqual([a["name"] for a in anchors if not a.get("excluded")],
                         ["盖内圈面纸", "地盒底座面纸"])
        self.assertEqual(next(a for a in anchors if a["entity_id"] == "right")["excluded"], "layout_instruction")

    def test_layout_retains_raw_evidence_and_does_not_guess(self):
        rows = packaging_layout.extract_layout_rows(self.ir)
        self.assertEqual(len(rows), 2)
        first = next(row for row in rows if row["entity_id"] == "right")
        self.assertEqual(first["sheet_mm"], [443.0, 595.0])
        self.assertEqual(first["n_up"], 1)
        self.assertFalse(rows[0]["confirmed"])
        self.assertIn("盖内圈面纸+地盒底座面纸", first["raw_text"])

    def test_no_layout_instruction_is_not_invented(self):
        self.assertEqual(packaging_layout.extract_layout_rows({"texts": [self.ir["texts"][0]]}), [])

    def test_mixed_schemes_must_not_become_one_bom(self):
        mixed = {"texts": self.ir["texts"] + [
            {"entity_id": "scheme1", "raw_text": "内托方案1", "position": [500, 800], "layer": "TEXT"}]}
        with self.assertRaisesRegex(ValueError, "multiple_packaging_schemes"):
            resolver.resolve_business_parts("demo", mixed, {"parts": []},
                                            use_model=False, strict_schemes=True)

    def test_multi_fragment_representation_keeps_each_piece(self):
        fragments = packaging_layout.part_fragments(["c1", "c2"], [
            {"component_id": "c1", "bbox": [0, 0, 10, 10], "layers": ["CUT"], "entity_ids": ["e1"]},
            {"component_id": "c2", "bbox": [20, 0, 30, 10], "layers": ["CREASE"], "entity_ids": ["e2"]},
        ])
        self.assertEqual([f["component_id"] for f in fragments], ["c1", "c2"])
        self.assertEqual([f["bbox"] for f in fragments], [[0, 0, 10, 10], [20, 0, 30, 10]])

    def test_text_declares_two_portions_without_inventing_geometry(self):
        sections = packaging_layout.declared_sections("地盒内、外圈围边面纸")
        self.assertEqual([section["name"] for section in sections],
                         ["地盒内圈围边面纸", "地盒外圈围边面纸"])
        self.assertTrue(all(section["geometry_status"] == "pending_attribution" for section in sections))

    def test_confirm_requires_observed_sheet_and_n_up(self):
        row = packaging_layout.extract_layout_rows(self.ir)[0]
        doc = {"layout_rows": [row]}
        updated = packaging_layout.set_layout_confirmation(doc, row["entity_id"], True)
        self.assertTrue(updated["layout_rows"][0]["confirmed"])
        self.assertFalse(doc["layout_rows"][0]["confirmed"])
        missing_sheet = {"layout_rows": [dict(row, sheet_mm=None)]}
        with self.assertRaisesRegex(ValueError, "layout_evidence_incomplete"):
            packaging_layout.set_layout_confirmation(missing_sheet, row["entity_id"], True)

    def test_packaging_stage_has_distinct_layout_view(self):
        parent = (ROOT / "tech_app/frontend/tech-workbench.js").read_text()
        board = (ROOT / "tech_app/frontend/assembly-integration.js").read_text()
        self.assertIn("3.2 排版排模", parent)
        self.assertIn("packaging-layout", board)
        self.assertIn("3.3 后道加工与组装工艺", parent)


if __name__ == "__main__":
    unittest.main()
