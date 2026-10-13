import json
import pathlib
import subprocess
import unittest
from tests.fixtures import wf_handoff_harness as H
import cpq_tech_bridge
import cpq_wf

class FinanceCompletionIdentity(unittest.TestCase):
    def seed(self,wb,project=None,owner=None):
        wb.source_task()['from_user_id']=H.SALES_UID
        wb.db.seed('cpq_wf_task',task_id=9002,card_id=H.CARD_ID,task_kind='tech_cost',
            from_user_id=H.PROC_UID,status='claimed',claimed_by_user_id=owner or H.FIN_UID,
            payload=json.dumps({'tech_cost':{'project_id':project or H.TECH_PROJECT}}))
        return {**H.cost_result(),'completion_task_id':'9002'}

    def test_routes_to_original_quote_but_only_completes_own_finance_task_and_retries(self):
        with H.Workbench(source_task_kind='tech_new_product',source_claimed_by=H.PROC_UID) as wb:
            result=self.seed(wb)
            out=wb.handoff(result=result)
            self.assertEqual(H.QUOTE_SESSION,out['quote_session_id'])
            self.assertEqual('completed',wb.rows('cpq_wf_task',task_id=9002)[0]['status'])
            self.assertEqual('claimed',wb.source_task()['status'])
            self.assertEqual('9002',out['source_task']['task_id'])
            repeated=wb.handoff(result=result)
            self.assertEqual(out['handoff_id'],repeated['handoff_id'])
            self.assertEqual(1,len(wb.handoffs()))

    def test_cross_project_completion_is_rejected_before_writes(self):
        with H.Workbench(source_task_kind='tech_new_product',source_claimed_by=H.PROC_UID) as wb:
            result=self.seed(wb,project='unrelated')
            with self.assertRaisesRegex(cpq_tech_bridge.BridgeError,'项目'):
                wb.handoff(result=result)
            self.assertEqual([],wb.handoffs())
            self.assertEqual('claimed',wb.rows('cpq_wf_task',task_id=9002)[0]['status'])

    def test_finance_task_on_distinct_carrier_card_can_complete_without_retargeting_quote(self):
        with H.Workbench(source_task_kind='tech_new_product',source_claimed_by=H.PROC_UID) as wb:
            result=self.seed(wb)
            wb.db.seed('cpq_wf_card',card_id=2002,session_id=H.TECH_PROJECT,assistant_type='quote',
                current_step=1,overall_status='in_progress',creator_user_id=H.PROC_UID)
            wb.rows('cpq_wf_task',task_id=9002)[0]['card_id']=2002
            out=wb.handoff(result=result)
            self.assertEqual(H.QUOTE_SESSION,out['quote_session_id'])
            self.assertEqual('completed',wb.rows('cpq_wf_task',task_id=9002)[0]['status'])
            self.assertEqual('claimed',wb.source_task()['status'])

    def test_unclaimed_finance_task_cannot_be_closed_as_current_task(self):
        with H.Workbench(source_task_kind='tech_new_product',source_claimed_by=H.PROC_UID) as wb:
            result=self.seed(wb)
            row=wb.rows('cpq_wf_task',task_id=9002)[0]
            row.update(status='open',claimed_by_user_id=None)
            with self.assertRaisesRegex(cpq_tech_bridge.BridgeError,'领取'):
                wb.handoff(result=result)
            self.assertEqual([],wb.handoffs())

    def test_other_finance_claim_is_still_protected(self):
        with H.Workbench(source_task_kind='tech_new_product',source_claimed_by=H.PROC_UID) as wb:
            result=self.seed(wb,owner=H.OTHER_FIN_UID)
            with self.assertRaisesRegex((cpq_wf.WfError,cpq_tech_bridge.BridgeError),'FI2'):
                wb.handoff(result=result)
            self.assertEqual([],wb.handoffs())

    def test_process_manager_cannot_use_finance_completion_identity(self):
        with H.Workbench(source_task_kind='tech_new_product',source_claimed_by=H.PROC_UID) as wb:
            result=self.seed(wb)
            with self.assertRaisesRegex(cpq_tech_bridge.BridgeError,'财务'):
                wb.handoff(uid=H.PROC_UID,result=result)
            self.assertEqual([],wb.handoffs())

    def test_cost_banner_distinguishes_ready_estimate_from_incomplete_cost(self):
        path=pathlib.Path(__file__).resolve().parents[1]/'tech_app/frontend/cost-review.js'
        src=path.read_text()
        start=src.find('function crPackagingCostNotice(')
        self.assertGreaterEqual(start,0)
        end=src.find('\nfunction ',start+1)
        function=src[start:end]
        script=function+'''
const cost={has_gaps:true,gaps:[{code:'estimated_time'}]};
const ready=crPackagingCostNotice({packaging_cost:cost,ready:true,reconciled:true});
if(ready.includes('部分成本') || !ready.includes('估算') || !ready.includes('正式报价'))process.exit(1);
if(!crPackagingCostNotice({packaging_cost:cost,ready:false,reconciled:false}).includes('部分成本'))process.exit(2);
if(!crPackagingCostNotice({packaging_cost:{...cost,stale:true},ready:true,reconciled:true}).includes('重新测算'))process.exit(3);
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
