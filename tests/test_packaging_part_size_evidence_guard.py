"""尺寸来源不能在文档组装时丢失，未确认值不能靠样本修正进入下游。"""
from __future__ import annotations

from pathlib import Path
import unittest

from tech_app.backend.services import cad_ir, packaging_parts
from tech_app.backend.services import packaging_business_part_resolver as resolver


ROOT = Path(__file__).resolve().parents[1]
WINE = ROOT / "tech_app/data/cad-ir-realsample/conversions/47c39dc1ab6738fc48c8/converted.dxf"


class PackagingPartSizeEvidenceGuard(unittest.TestCase):
    def test_unconfirmed_dwg_size_survives_document_and_blocks_cost(self):
        ir = cad_ir.parse_dxf(WINE.read_bytes(), filename=WINE.name)
        geometry = packaging_parts.extract(ir)
        outcome = resolver.resolve_business_parts("offline-test", ir, geometry)
        document = packaging_parts.business_parts_document(
            outcome["reference"], geometry, bindings=outcome["match"])
        by_name = {row["name"]: row for row in document["business_parts"]}
        for name in ("左盖面纸", "右盖面纸"):
            row = by_name[name]
            self.assertEqual("bbox_only", row["reference"]["size_quality"])
            self.assertEqual("geometry_region", row["reference"]["size_source"])
            self.assertEqual(packaging_parts.BUSINESS_PART_SIZE_UNCONFIRMED,
                             packaging_parts.business_cost_inputs(row)["code"])
        self.assertFalse(outcome["detail"]["gold_standard_used"])

    def test_right_pane_square_has_no_flex_shrink(self):
        css = (ROOT / "tech_app/frontend/drawing-flow.css").read_text()
        block = css.split(".packaging-part-shape-viewport {", 1)[1].split("}", 1)[0]
        self.assertIn("aspect-ratio: 1 / 1", block)
        self.assertIn("flex: none", block)


if __name__ == "__main__":
    unittest.main()
