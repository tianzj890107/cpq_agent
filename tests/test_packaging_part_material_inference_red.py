# -*- coding: utf-8 -*-
"""业务件材料按件名推定（Spec `packaging-part-material-inference.md`）。先红后绿。"""
import unittest

from tech_app.backend.services import packaging_demo_inputs as demo
from tech_app.backend.services import packaging_parts as parts

INFERRED = "inferred_from_part_name"


def row(name="左盒盒背灰板", material="", **reference_extra):
    reference = {"length_mm": 218.2, "width_mm": 100.5, "size_quality": "confirmed",
                 "material_text": material}
    reference.update(reference_extra)
    return {"business_part_code": "DWG-BP01", "name": name, "reference": reference,
            "authority": dict(reference), "sections": []}


class MaterialInference(unittest.TestCase):
    def test_reference_block_infers_material_from_name(self):
        block = parts.business_part_reference_block(row())
        self.assertEqual("2.0mm灰板", block.get("material_text"))
        self.assertEqual(INFERRED, block.get("material_source"))

    def test_explicit_material_is_never_rewritten(self):
        block = parts.business_part_reference_block(row(material="225G铜版底PET光银"))
        self.assertEqual("225G铜版底PET光银", block.get("material_text"))
        self.assertFalse(block.get("material_source"))

    def test_process_inputs_accepts_inferred_material(self):
        inputs = parts.business_process_inputs(row())
        self.assertTrue(inputs.get("ok"), inputs.get("message"))
        self.assertEqual("2.0mm灰板", inputs.get("material_text"))
        self.assertEqual(INFERRED, inputs.get("material_source"))

    def test_unknown_name_still_rejects_with_material_unknown(self):
        inputs = parts.business_process_inputs(row(name="神秘件X"))
        self.assertFalse(inputs.get("ok"))
        self.assertEqual("PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN", inputs.get("code"))
        self.assertEqual(["material"], inputs.get("missing_variables"))

    def test_process_assumption_discloses_inference(self):
        text = parts.business_process_assumption(parts.business_process_inputs(row()))
        self.assertIn("推定", text)
        self.assertIn("2.0mm灰板", text)

    def test_cost_inputs_reads_inferred_material(self):
        # 材料费另有克重门禁：推定只负责"材料原文"，不替它补克重。
        inputs = parts.business_cost_inputs(row())
        self.assertEqual("2.0mm灰板", inputs.get("material_text"))
        self.assertEqual(INFERRED, inputs.get("material_source"))

    def test_cost_assumption_discloses_inference(self):
        payload = {"ok": True, "material_source": INFERRED, "material_text": "2.0mm灰板",
                   "variables": {"cut_length": 218.2, "cut_width": 100.5}}
        text = parts.business_cost_assumption(payload)
        self.assertIn("推定", text)
        self.assertIn("2.0mm灰板", text)

    def test_grammage_inferred_from_thickness_family_or_text(self):
        from tech_app.backend.services import packaging_material_inference as inference
        self.assertEqual((1400.0, "inferred_thickness_density"), inference.infer_grammage("2.0mm灰板"))
        self.assertEqual((350.0, "material_text"), inference.infer_grammage("350G玖龙粉灰"))
        self.assertEqual((80.0, "inferred_family_default"), inference.infer_grammage("衬纸"))
        self.assertEqual((None, ""), inference.infer_grammage("神秘材料"))

    def test_cost_inputs_accepts_inferred_grammage(self):
        inputs = parts.business_cost_inputs(row())
        self.assertTrue(inputs.get("ok"), inputs.get("message"))
        self.assertEqual(1400.0, inputs.get("gsm"))
        self.assertEqual("inferred_thickness_density",
                         (inputs.get("variables") or {}).get("grammage_source"))
        self.assertIn("推定", parts.business_cost_assumption(inputs))

    def test_demo_inputs_do_not_change_business_identity(self):
        base = {"business_parts": [{"business_part_code": "X", "name": "件"}]}
        with_demo = {"poc_demo": True, "demo_inputs_version": "v",
                     "business_parts": [{**base["business_parts"][0],
                                         "poc_inputs": {"mock": True, "length_mm": 100}}]}
        self.assertEqual(parts._business_identity(base), parts._business_identity(with_demo))

    def test_single_implementation_shared_with_demo_path(self):
        from tech_app.backend.services import packaging_material_inference as inference
        self.assertIs(demo.infer_material_text, inference.infer_material_text)
        self.assertEqual("2.0mm灰板", inference.infer_material_text("左盒盒背灰板"))
        self.assertEqual("", inference.infer_material_text("神秘件X"))


if __name__ == "__main__":
    unittest.main()
