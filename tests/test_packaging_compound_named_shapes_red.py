"""一个复合件名可以对应多个独立圆形；不用酒盒/圆盘盒答案。"""
import unittest
from tech_app.backend.services import packaging_part_visual_review as review


class CompoundNamedShapesTest(unittest.TestCase):
    def test_compound_name_proposes_two_circles_without_neighbour_or_layout_copies(self):
        for dx, dy in ((0, 0), (700, -900)):
            def box(x, y, r):
                return [x-r+dx, y-r+dy, x+r+dx, y+r+dy]
            regions = [
                {'region_id': 'a', 'component_ids': ['c1'], 'entity_ids': ['e1'],
                 'bbox': box(0, 0, 15), 'substantial': True},
                {'region_id': 'b', 'component_ids': ['c2'], 'entity_ids': ['e2'],
                 'bbox': box(60, 0, 20), 'substantial': True},
                {'region_id': 'other', 'component_ids': ['c3'], 'entity_ids': ['e3'],
                 'bbox': box(170, 0, 25), 'substantial': True}]
            ir = {'texts': [
                {'entity_id': 't1', 'raw_text': '名称：内、外圈面纸', 'position': [30+dx, 35+dy]},
                {'entity_id': 't2', 'raw_text': '名称：底板', 'position': [170+dx, 35+dy]}],
                  'entities': []}
            self.assertTrue(hasattr(review, '_compound_named_groups'), '缺少复合名称的多图形候选')
            groups = review._compound_named_groups(ir, regions, {}, [])
            self.assertEqual([['c1', 'c2']], [row['component_ids'] for row in groups])
            self.assertEqual('ambiguous', groups[0]['geometry_status'])
            self.assertNotIn('length_mm', groups[0], '模型候选不能自证展开尺寸')

    def test_single_name_does_not_merge_all_nearby_shapes(self):
        self.assertTrue(hasattr(review, '_compound_named_groups'))
        self.assertEqual([], review._compound_named_groups({'texts': [
            {'entity_id': 't', 'raw_text': '名称：底板', 'position': [0, 0]}]}, [], {}, []))
