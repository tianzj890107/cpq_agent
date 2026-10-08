import unittest
from tech_app.backend.services import packaging_business_part_resolver as resolver
from tech_app.backend.services import packaging_parts, packaging_bom


class RoundDimensionTest(unittest.TestCase):
    def test_spatial_cad_dimension_can_feed_downstream_but_bbox_cannot(self):
        row = {'business_part_code': 'BP1', 'name': '圆底板', 'reference': {}, 'geometry_binding': {
            'status': 'bound', 'component_ids': ['c1'], 'entity_ids': ['circle1'],
            'size_confirmed': True, 'size_source': 'size_dimension',
            'evidence_kinds': ['geometry_region', 'size_dimension'],
            'length_mm': 94, 'width_mm': 94}}
        doc = {'derived_from_drawing': True}
        result = packaging_parts.verified_business_part_input_row(row, doc)
        self.assertEqual(94, result['reference']['length_mm'])
        self.assertEqual('verified_cad_dimension', result['reference']['size_source'])
        bom = packaging_bom.business_part_rows({**doc, 'business_parts': [row]})[0]
        self.assertEqual(94, bom['length_mm'])
        row['geometry_binding']['size_source'] = 'geometry_region'
        with self.assertRaisesRegex(ValueError, 'drawing_part_size_not_confirmed'):
            packaging_parts.verified_business_part_input_row(row, doc)
    def test_diameter_confirms_only_its_real_circle_not_an_equal_square(self):
        ir = {'entities': [{'entity_id': 'circle1', 'type': 'CIRCLE',
                           'attributes': {'center': [0, 0], 'radius': 47}}],
              'dimensions': [{'entity_id': 'diameter1', 'dim_type': 'diameter',
                              'measured_value': 94, 'declared_value': 94,
                              'target_entity_ids': ['point:-47,0', 'point:47,0']}]}
        rects = resolver.dimension_rects(ir)
        region = {'bbox': [-47, -47, 47, 47], 'entity_ids': ['circle1']}
        self.assertTrue(resolver._region_is_size_confirmed(region, rects))
        self.assertFalse(resolver._region_is_size_confirmed(
            {'bbox': [-47, -47, 47, 47], 'entity_ids': ['square1']}, rects))
        self.assertFalse(resolver._region_is_size_confirmed(
            {'bbox': [153, -47, 247, 47], 'entity_ids': ['circle2']}, rects))

    def test_radius_produces_diameter_but_conflicting_annotation_does_not(self):
        ir = {'entities': [{'entity_id': 'circle1', 'type': 'CIRCLE',
                           'attributes': {'center': [20, 30], 'radius': 36}}],
              'dimensions': [{'entity_id': 'radius1', 'dim_type': 'radius',
                              'measured_value': 36, 'declared_value': 36,
                              'target_entity_ids': ['point:20,30', 'point:56,30']}]}
        rects = resolver.dimension_rects(ir)
        self.assertEqual(72, rects[0]['length_mm'])
        ir['dimensions'][0]['declared_value'] = 50
        self.assertEqual([], resolver.dimension_rects(ir))
