import unittest
from unittest.mock import patch
from tech_app.backend.models.integration import IntegrationPlan
from tech_app.backend.models.cost_review import CostReview
from tech_app.backend.services import cost_review


class PackagingCostQuantityScopeTest(unittest.TestCase):
    def test_changed_batch_is_a_cost_confirmation_gap(self):
        data = {'counts': {'missing': [], 'zero': [], 'assembly_costed': True, 'parts': 1},
                'packaging_cost': {'built': True, 'quote_quantity': 500}}
        with patch.object(cost_review.store, 'load_requirement', return_value={
                'data': {'industry': 'packaging', 'quote_quantity': 1000}}):
            gaps = cost_review.confirm_gaps('p1', None, IntegrationPlan(), data)
        self.assertIn('packaging:quantity:500:1000', gaps['codes'])

    def test_quote_batch_is_not_single_box_usage_or_legacy_default(self):
        req = {'data': {'industry': 'packaging', 'quote_quantity': 1000}}
        with patch.object(cost_review.store, 'load_requirement', return_value=req), \
             patch.object(cost_review, 'summarize', return_value={
                 'packaging_cost': {'built': True, 'quote_quantity': 1000}}), \
             patch.object(cost_review, 'confirm_gaps', return_value=[]):
            result = cost_review.payload('p1', None, IntegrationPlan(quantity=1), CostReview())
        self.assertEqual(1000, result['quote_quantity'])
        self.assertEqual(1000, result['quantity'])
        self.assertEqual(1, result['assembly_usage'])
        self.assertEqual(1, result['legacy_batch_quantity'])

    def test_cost_batch_difference_is_disclosed_not_overwritten(self):
        req = {'data': {'industry': 'packaging', 'quote_quantity': 1000}}
        with patch.object(cost_review.store, 'load_requirement', return_value=req), \
             patch.object(cost_review, 'summarize', return_value={
                 'packaging_cost': {'built': True, 'quote_quantity': 500}}), \
             patch.object(cost_review, 'confirm_gaps', return_value=[]):
            result = cost_review.payload('p1', None, IntegrationPlan(), CostReview())
        self.assertTrue(result['quantity_mismatch'])
        self.assertEqual(500, result['cost_quote_quantity'])
