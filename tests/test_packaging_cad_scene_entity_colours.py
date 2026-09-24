"""包装 DWG 件图：CAD 实体色必须穿过 IR 和场景，不能用包围盒伪造轮廓。"""
from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from tech_app.backend import main
from tech_app.backend.services import cad_ir


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tech_app/data/cad-ir-realsample/conversions/47c39dc1ab6738fc48c8/converted.dxf"


class PackagingCadSceneColours(unittest.TestCase):
    def test_scene_exposes_entity_colour_and_keeps_real_line(self):
        ir = {"entities": [{"entity_id": "e:1", "kind": "line", "layer": "DESIGN",
                            "aci_color": 1, "bbox": [0, 0, 10, 0],
                            "attributes": {"start": [0, 0], "end": [10, 0]}}],
              "layers": [{"name": "DESIGN", "color": 7, "visible": True}],
              "texts": [], "source": {}, "document": {"extents": [0, 0, 10, 0]}}
        with patch.object(main.cad_ir, "load_ir", return_value=ir), \
             patch.object(main, "_packaging_cad_layer_roles", return_value={}):
            scene = main._packaging_cad_scene("test-project")
        self.assertEqual(1, scene["entities"][0]["aci_color"])
        self.assertEqual(7, scene["entities"][0]["layer_aci_color"])
        self.assertEqual([[0.0, 0.0], [10.0, 0.0]], scene["entities"][0]["points"])

    def test_no_geometry_does_not_turn_bbox_into_fake_rectangle(self):
        self.assertEqual([], main._cad_scene_points({"kind": "dimension", "bbox": [0, 0, 20, 10]}))
        self.assertEqual([], main._cad_scene_points({"kind": "hatch", "bbox": [0, 0, 20, 10]}))

    def test_real_dwg_entity_aci_survives_parse_and_legacy_lookup(self):
        ir = cad_ir.parse_dxf(SAMPLE.read_bytes(), filename=SAMPLE.name)
        colours = {row.get("aci_color") for row in ir["entities"]}
        self.assertTrue({1, 2, 3, 5}.issubset(colours), colours)
        legacy = main._packaging_cad_legacy_aci(str(SAMPLE), SAMPLE.stat().st_mtime_ns)
        red = next(row for row in ir["entities"] if row.get("aci_color") == 1
                   and row.get("handle") in legacy)
        self.assertEqual(1, legacy[red["handle"]])


if __name__ == "__main__":
    unittest.main()
