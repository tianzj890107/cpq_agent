import unittest
from unittest.mock import patch

from tech_app.backend.services import cost_review


class PackagingCostReviewLossProjection(unittest.TestCase):
    def test_loss_is_already_in_categories_and_not_added_twice(self):
        result = dict(built=True, total_cost=62, material_total=15,
                      labor_total=18, process_total=8, tooling_total=18,
                      packaging_total=2, freight_total=1, other_total=0,
                      loss_amount=1.2)
        with patch.object(cost_review, 'parts_source', return_value=[]), \
             patch.object(cost_review, '_part_rows', return_value=[]), \
             patch.object(cost_review, '_assembly_row', return_value={
                 'breakdown': {}, 'has_cost': False, 'id': 'assembly', 'unit_cost': 0}), \
             patch.object(cost_review.store, 'load_requirement', return_value={
                 'data': {'industry': 'packaging'}}), \
             patch('tech_app.backend.services.packaging_cost.load_cost', return_value=result), \
             patch('tech_app.backend.services.packaging_cost.readiness_verdict',
                   return_value={'formal_ready': True}):
            final = cost_review.summarize('test', None, None)['final']
        self.assertAlmostEqual(sum(final[key] for key in
                                   ('material', 'labor', 'machining', 'overhead')),
                               final['total'])

