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

    def test_single_implementation_shared_with_demo_path(self):
        from tech_app.backend.services import packaging_material_inference as inference
        self.assertIs(demo.infer_material_text, inference.infer_material_text)
        self.assertEqual("2.0mm灰板", inference.infer_material_text("左盒盒背灰板"))
        self.assertEqual("", inference.infer_material_text("神秘件X"))


if __name__ == "__main__":
    unittest.main()
