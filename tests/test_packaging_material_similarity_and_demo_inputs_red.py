# -*- coding: utf-8 -*-
"""通用性守卫：材料相似度推荐 + 演示输入装配。

这两件事都**不许**针对某一张图纸：断言里只出现合成材料与合成件名，换成别的 DWG 走同一套。
"""
import unittest

from tech_app.backend.services import packaging_demo_inputs as demo
from tech_app.backend.services import packaging_material_match as match

KB_MATERIALS = [
    {"material_code": "M-GREY", "name": "灰板（双灰纸板）", "grade": "2.0mm", "spec": "t2.0 双灰",
     "properties": [{"prop_key": "density", "value_num": 0.75}]},
    {"material_code": "M-FACE", "name": "铜版纸面纸", "grade": "157g", "spec": "157g/㎡ 单面涂布",
     "properties": [{"prop_key": "gsm", "value_num": 157}]},
    {"material_code": "M-EVA", "name": "EVA 片材", "grade": "38°", "spec": ""},
    {"material_code": "M-LINER", "name": "内衬纸（牛皮衬纸）", "grade": "80g", "spec": ""},
]


class MaterialFeaturesTest(unittest.TestCase):
    def test_a1_grammage_and_thickness_only_from_text(self):
        self.assertEqual(225, match.material_features("225G铜版底PET光银")["grammage"])
        self.assertEqual(2.0, match.material_features("2mm灰板")["thickness_mm"])
        self.assertIsNone(match.material_features("磁铁")["grammage"])

    def test_a2_family_is_keyword_based_not_drawing_specific(self):
        self.assertEqual("face_paper", match.family_of("350G玖龙粉灰面纸"))
        self.assertEqual("eva", match.family_of("EVA 内托"))
        self.assertEqual("", match.family_of("神秘材料X"))


class RecommendTest(unittest.TestCase):
    def test_b1_similarity_matches_family_and_reports_reasons(self):
        result = match.recommend_materials("225G铜版底PET光银", KB_MATERIALS)
        self.assertEqual("M-FACE", result["candidate"]["material_code"])
        self.assertEqual("similarity", result["status"])
        self.assertFalse(result["approved"], "相似度只是建议，不许当成已确认")
        self.assertTrue(any("材质族" in reason for reason in result["candidate"]["reasons"]))

    def test_b2_exact_name_is_its_own_status(self):
        result = match.recommend_materials("灰板（双灰纸板）", KB_MATERIALS)
        self.assertEqual("exact_name", result["status"])

    def test_b3_unknown_text_never_silently_takes_top_score(self):
        result = match.recommend_materials("神秘材料X", KB_MATERIALS)
        self.assertIn(result["status"], ("below_threshold", "no_candidate", "ambiguous"))
        self.assertIsNone(result["candidate"] if result["status"] != "ambiguous" else None)

    def test_b4_empty_library_is_no_candidate_not_a_guess(self):
        result = match.recommend_materials("225G铜版底PET光银", [])
        self.assertEqual("no_candidate", result["status"])
        self.assertIsNone(result["candidate"])

    def test_b5_demo_price_is_family_based_and_disclosed(self):
        price = match.demo_unit_price("225G铜版底PET光银")
        self.assertEqual("face_paper", price["family"])
        self.assertEqual("demo_family_price", price["source"])
        self.assertTrue(price["unit_price"] > 0)

    def test_b6_grammage_from_text_then_thickness_times_density(self):
        self.assertEqual(225, match.derive_grammage("225G铜版底PET光银")["grammage"])
        derived = match.derive_grammage("2mm灰板", material=KB_MATERIALS[0])
        self.assertEqual(1500.0, derived["grammage"])
        self.assertEqual("derived_from_density", derived["source"])
        self.assertIsNone(match.derive_grammage("神秘材料X")["grammage"])


class DemoInputsTest(unittest.TestCase):
    def _sizes(self):
        return {"cmp:1": {"length_mm": 300.0, "width_mm": 500.0, "size_source": "unfolded_size"},
                "cmp:2": {"length_mm": 120.0, "width_mm": 260.0, "size_source": "unfolded_size"}}

    def test_c1_multi_component_part_takes_per_axis_envelope(self):
        row = {"business_part_code": "P1", "name": "封面",
               "geometry_binding": {"component_ids": ["cmp:1", "cmp:2"]}}
        size = demo.part_size(row, self._sizes())
        self.assertEqual(300.0, size["length_mm"])
        self.assertEqual(500.0, size["width_mm"])

    def test_c2_no_geometry_no_size_no_guess(self):
        row = {"business_part_code": "P2", "name": "封面", "geometry_binding": {}}
        self.assertIsNone(demo.part_size(row, self._sizes())["length_mm"])
        value = demo.demo_inputs_for(row, self._sizes(), materials=KB_MATERIALS)
        self.assertFalse(value["ok"])
        self.assertEqual("no_geometry_size", value["reason"])

    def test_c3_drawing_material_is_recommended_and_price_disclosed(self):
        row = {"business_part_code": "P3", "name": "左盖面纸",
               "reference": {"material_text": "225G铜版底PET光银"},
               "geometry_binding": {"component_ids": ["cmp:1"]}}
        value = demo.demo_inputs_for(row, self._sizes(), materials=KB_MATERIALS)
        self.assertTrue(value["ok"])
        self.assertEqual("M-FACE", value["recommended_material"]["material_code"])
        self.assertEqual(225.0, value["grammage"])
        self.assertTrue(value["note"], "演示口径必须随结论披露")

    def test_c4_missing_material_is_inferred_from_name_and_disclosed(self):
        row = {"business_part_code": "P4", "name": "左盒盒背灰板",
               "geometry_binding": {"component_ids": ["cmp:2"]}}
        value = demo.demo_inputs_for(row, self._sizes(), materials=KB_MATERIALS)
        self.assertEqual("inferred_from_part_name", value["material_source"])
        self.assertIn("灰板", value["material_text"])
        self.assertIn("推定", value["note"])

    def test_c5_unknown_part_name_invents_nothing(self):
        self.assertEqual("", demo.infer_material_text("神秘件X"))

    def test_c6_poc_inputs_reach_the_bom_rows_with_sizes(self):
        from tech_app.backend.services import packaging_bom
        doc = {"derived_from_drawing": True, "business_parts_id": "id-1",
               "business_parts_hash": "hash-1", "poc_demo": True,
               "business_parts": [{"business_part_code": "P5", "name": "底板",
                                   "reference": {},
                                   "poc_inputs": {"length_mm": 200.0, "width_mm": 80.0,
                                                  "material_text": "2mm灰板", "mock": True,
                                                  "source": "demo_similarity"}}]}
        rows = packaging_bom.business_part_rows(doc)
        self.assertEqual(1, len(rows))
        self.assertEqual(200.0, rows[0]["length_mm"])
        self.assertEqual(80.0, rows[0]["width_mm"])

    def test_c7_module_has_no_single_drawing_hardcoding(self):
        source = open("tech_app/backend/services/packaging_demo_inputs.py",
                      encoding="utf-8").read()
        for banned in ("酒盒", "圆盘盒", "DWG-BP"):
            self.assertNotIn(banned, source, "演示装配不许写死某一份图纸的标识")


if __name__ == "__main__":
    unittest.main()
