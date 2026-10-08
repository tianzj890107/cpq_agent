import unittest
from unittest.mock import patch
from tech_app.backend.models.integration import IntegrationPlan
from tech_app.backend.services import integration, packaging_route, packaging_parts


class InternalFinanceDraftTest(unittest.TestCase):
    def test_draft_handoff_carries_facts_without_signing_approvals(self):
        plan = IntegrationPlan()
        requirement = {'data': {'industry': 'packaging', 'quote_quantity': 1000,
                                'title': '圆盘方案A', 'customer_name': '测试客户'}}
        parts = {'business_parts_id': 'b1', 'business_parts_hash': 'h1',
                 'business_parts': [{'business_part_code': 'BP1', 'name': '圆底板'}]}
        with patch.object(integration, 'load_plan', return_value=plan), \
             patch.object(integration.store, 'load_requirement', return_value=requirement), \
             patch.object(packaging_route, 'load_route', return_value={'built': True, 'steps': [{'name': '模切'}]}), \
             patch.object(packaging_parts, 'load_business_parts', return_value=parts), \
             patch.object(integration.cpq_bridge, 'send_to_finance', return_value={
                 'task_id': 'task1', 'task_no': 'COST-1'}) as bridge, \
             patch.object(integration, 'save_plan'), patch.object(integration.store, 'audit'):
            result = integration.send_to_finance('p1', {'username': 'PE1'}, draft=True,
                                                target_type='role', target_role_code='finance_mgr')
        self.assertFalse(result.confirmed)
        self.assertFalse(result.params_confirmed)
        self.assertFalse(result.process_confirmed)
        self.assertEqual([], result.waivers)
        self.assertTrue(result.finance_handoff.draft)
        payload = bridge.call_args.args[6]['tech_cost']
        self.assertEqual(1000, payload['quote_quantity'])
        self.assertEqual('p1', payload['project_id'])
        self.assertEqual('h1', payload['business_parts_hash'])
        self.assertEqual('BP1', payload['business_parts'][0]['business_part_code'])

    def test_draft_is_packaging_only(self):
        with patch.object(integration.store, 'load_requirement', return_value={'data': {'industry': 'battery'}}):
            with self.assertRaisesRegex(integration.IntegrationFlowError, '包装'):
                integration.send_to_finance('p1', draft=True)
