"""DWG 多连通片纸张尺寸与选件正方形视口的回归。"""
from __future__ import annotations

from pathlib import Path
import re
import unittest
from unittest.mock import patch

from tech_app.backend import main
from tech_app.backend.services import cad_ir, packaging_parts
from tech_app.backend.services import packaging_business_part_resolver as resolver
from tests.test_packaging_part_figure_fidelity_red import call as frontend_call


ROOT = Path(__file__).resolve().parents[1]
WINE = ROOT / "tech_app/data/cad-ir-realsample/conversions/47c39dc1ab6738fc48c8/converted.dxf"


class CompositeFacePaper(unittest.TestCase):
    def test_rule_generalizes_to_other_dimensions_and_requires_mirrored_geometry(self):
        def region(code, box):
            return {"region_id": code, "component_ids": [code], "entity_ids": ["e:" + code],
                    "bbox": box, "center": [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2],
                    "length_mm": box[2] - box[0], "width_mm": box[3] - box[1],
                    "substantial": True, "layers": ["CUT"]}
        left_boxes = [[0, 110, 100, 690], [100, 110, 300, 690], [300, 110, 400, 690]]
        right_boxes = [[1900, 110, 2000, 690], [1700, 110, 1900, 690],
                       [1600, 110, 1700, 690]]
        regions = [region("L" + str(i), box) for i, box in enumerate(left_boxes)] + [
            region("R" + str(i), box) for i, box in enumerate(right_boxes)]
        anchors = [{"entity_id": "a-left", "position": [-5, 0]},
                   {"entity_id": "a-right", "position": [1620, 0]}]
        parts = [{"name": "左测试面纸", "business_part_code": "p-left",
                  "evidence": {"anchor_entity_ids": ["a-left"]}},
                 {"name": "右测试面纸", "business_part_code": "p-right",
                  "evidence": {"anchor_entity_ids": ["a-right"]}}]
        match = {"bindings": [
            {"business_part_code": "p-left", "bbox": [0, -40, 40, -10],
             "size_confirmed": False},
            {"business_part_code": "p-right", "bbox": [1600, 100, 1700, 680],
             "size_confirmed": False},
        ]}
        rects = [{"center": [200, 400], "length_mm": 400, "width_mm": 600}]
        resolver._upgrade_dimensioned_clusters(parts, anchors, regions, rects, match)
        left, right = match["bindings"]
        self.assertEqual((400, 600), (left["length_mm"], left["width_mm"]))
        self.assertEqual((400, 600), (right["length_mm"], right["width_mm"]))
        self.assertTrue(left["size_confirmed"])
        self.assertFalse(right["size_confirmed"])
        # 去掉一个镜像分量后只剩两处对应，不得凭左右名称复制尺寸。
        broken = {"bindings": [{"business_part_code": "p-left", "bbox": [0, -40, 40, -10]},
                               {"business_part_code": "p-right", "bbox": [1600, 100, 1700, 680]}]}
        resolver._upgrade_dimensioned_clusters(parts, anchors, regions[:-1], rects, broken)
        self.assertNotIn("length_mm", broken["bindings"][1])

    def test_composite_view_keeps_nested_lines_even_if_owned_by_another_part(self):
        doc = {"cad_scene": {"entities": [
            {"cad_entity_id": "outer", "bbox": [0, 0, 10, 20], "points": [[0, 0], [10, 20]]},
            {"cad_entity_id": "nested", "bbox": [2, 2, 8, 8], "points": [[2, 2], [8, 8]]},
        ]}, "business_parts": [{"geometry_binding": {
            "business_part_code": "other", "entity_ids": ["nested"]}}]}
        binding = {"business_part_code": "paper", "entity_ids": ["outer"],
                   "bbox": [0, 0, 10, 20], "complete_box": True}
        rows = frontend_call("packagingPartSceneEntities", binding, doc)
        self.assertEqual({"outer", "nested"}, {row["cad_entity_id"] for row in rows})

    def test_wine_left_and_right_face_paper_use_whole_panel_not_nearest_fragment(self):
        ir = cad_ir.parse_dxf(WINE.read_bytes(), filename=WINE.name)
        geometry = packaging_parts.extract(ir)
        result = resolver.resolve_business_parts("offline-test", ir, geometry)
        rows = {row["name"]: row for row in result["reference"]["parts"]}
        left, right = rows["左盖面纸"], rows["右盖面纸"]
        for row in (left, right):
            self.assertAlmostEqual(307.0675, row["length_mm"], places=2)
            self.assertAlmostEqual(528.894, row["width_mm"], places=2)
            self.assertGreaterEqual(len(row["evidence"]["component_ids"]), 3)
        self.assertTrue(left["evidence"]["size_confirmed"])
        self.assertEqual("multi_region_dimension", left["evidence"]["size_source"])
        self.assertFalse(right["evidence"]["size_confirmed"])
        self.assertEqual("mirrored_multi_region_dimension", right["evidence"]["size_source"])
        self.assertFalse(result["detail"]["gold_standard_used"])
        document = packaging_parts.business_parts_document(
            result["reference"], geometry, bindings=result["match"])
        saved = {row["name"]: row for row in document["business_parts"]}
        self.assertIn(saved["左盖面纸"]["reference"]["size_quality"],
                      packaging_parts.BUSINESS_SIZE_CONFIRMED_QUALITIES)
        self.assertEqual("bbox_only", saved["右盖面纸"]["reference"]["size_quality"])
        self.assertEqual(packaging_parts.BUSINESS_PART_SIZE_UNCONFIRMED,
                         packaging_parts.business_cost_inputs(saved["右盖面纸"])["code"])
        binding = saved["左盖面纸"]["geometry_binding"]
        with patch.object(main.cad_ir, "load_ir", return_value=ir), \
             patch.object(main, "_packaging_cad_layer_roles", return_value={}):
            scene = main._packaging_cad_scene("offline-test")
        box = binding["bbox"]
        scene["entities"] = [entity for entity in scene["entities"]
                             if entity.get("bbox") and entity["bbox"][0] >= box[0] - .01
                             and entity["bbox"][1] >= box[1] - .01
                             and entity["bbox"][2] <= box[2] + .01
                             and entity["bbox"][3] <= box[3] + .01]
        figure = frontend_call("packagingPartSceneSvg", binding, {
            "cad_scene": scene, "geometry_evidence": document["geometry_evidence"]},
            {"includeAnnotations": True})
        self.assertGreaterEqual(figure.count("data-cad-entity-id="), 35)
        viewbox = re.search(r'viewBox="([^"]+)"', figure)
        self.assertIsNotNone(viewbox)
        _, _, width, height = [float(part) for part in viewbox.group(1).split()]
        self.assertGreater(height, width, "完整面纸应是纵向整块，不是误绑的扁碎片")

    def test_selected_part_viewport_cannot_shrink_in_flex_column(self):
        css = (ROOT / "tech_app/frontend/drawing-flow.css").read_text()
        block = css.split(".packaging-part-shape-viewport {", 1)[1].split("}", 1)[0]
        self.assertIn("aspect-ratio: 1 / 1", block)
        self.assertIn("flex: none", block)


if __name__ == "__main__":
    unittest.main()
