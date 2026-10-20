from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
from contextlib import ExitStack

from tech_app.backend.services import cost_flow as flow


class CostTaskContext(unittest.TestCase):
    def plan(self, task='COST'):
        return SimpleNamespace(finance_handoff=SimpleNamespace(task_id=task), params=None)

    def context(self):
        stack=ExitStack()
        stack.enter_context(patch.object(flow.store,'load_requirement',return_value={'data':{'source_task_id':'PROCESS'}}))
        stack.enter_context(patch.object(flow.store,'load_meta',return_value={'quote_task_id':'PROCESS'}))
        return stack

    def test_known_process_task_is_not_the_completion_task(self):
        with self.context():
            self.assertEqual('COST',flow.cost_completion_task_id('P','PROCESS',self.plan()))

    def test_missing_task_uses_saved_finance_task(self):
        with self.context():
            self.assertEqual('COST',flow.cost_completion_task_id('P','',self.plan()))

    def test_explicit_cost_or_unknown_id_is_not_silently_replaced(self):
        with self.context():
            for task in ('COST','OLDER-COST','OTHER-PROJECT'):
                self.assertEqual(task,flow.cost_completion_task_id('P',task,self.plan()))

    def test_process_task_without_finance_handoff_fails_closed(self):
        with self.context(), self.assertRaises(flow.CostFlowError) as error:
            flow.cost_completion_task_id('P','PROCESS',self.plan(''))
        self.assertEqual(409,error.exception.status_code)
        self.assertIn('成本',str(error.exception))

    def test_legacy_context_without_any_task_stays_compatible(self):
        with self.context():
            self.assertEqual('',flow.cost_completion_task_id('P','',self.plan('')))

    def test_both_cost_exits_use_cost_task_not_process_task(self):
        for action in ('send_to_quote','return_to_process'):
            with self.subTest(action=action), self.context(), ExitStack() as stack:
                plan=self.plan();review=SimpleNamespace()
                stack.enter_context(patch.object(flow,'_ready',return_value=(None,plan,review)))
                stack.enter_context(patch.object(flow,'cost_review_ctx',return_value=(None,plan,review)))
                stack.enter_context(patch.object(flow,'_record_action'))
                stack.enter_context(patch.object(flow.cost_review,'payload',return_value={}))
                stack.enter_context(patch.object(flow.cost_review,'summarize',return_value={}))
                stack.enter_context(patch.object(flow,'process_handoff_package',return_value={'confirmed':True}))
                call=stack.enter_context(patch.object(flow,'bridge_call',return_value={}))
                direct=stack.enter_context(patch.object(flow,'integration_send_to_quote_body',return_value={}))
                getattr(flow,action)('P',{'username':'FI','role':'finance_manager'},source_task_id='PROCESS')
                if action=='send_to_quote':
                    self.assertEqual('COST',direct.call_args.kwargs['completion_task_id'])
                else:
                    self.assertEqual('COST',call.call_args.args[9])

    def test_actual_transaction_keeps_original_engineer_task(self):
        from tests.fixtures import wf_handoff_harness as H
        from tests.test_finance_quote_completion_identity_red import FinanceCompletionIdentity
        with H.Workbench(source_task_kind='tech_new_product',source_claimed_by=H.PROC_UID) as wb:
            result=FinanceCompletionIdentity().seed(wb)
            with patch.object(flow.store,'load_requirement',return_value={'data':{'source_task_id':'9001'}}), \
                 patch.object(flow.store,'load_meta',return_value={'quote_task_id':'9001'}):
                result['completion_task_id']=flow.cost_completion_task_id(H.TECH_PROJECT,'9001',self.plan('9002'))
            sent=wb.handoff(result=result)
            self.assertEqual('9002',sent['source_task']['task_id'])
            self.assertEqual('claimed',wb.source_task()['status'])
            self.assertEqual(H.PROC_UID,wb.source_task()['claimed_by_user_id'])
            self.assertEqual('completed',wb.rows('cpq_wf_task',task_id=9002)[0]['status'])
