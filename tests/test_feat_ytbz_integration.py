"""全量整合必须同时保留现网行为和feat入口。"""
import subprocess
import unittest
from pathlib import Path

import cpq_wf
from tests import test_quote_direct_six_steps_red as direct_tests


class IntegrationTests(unittest.TestCase):
    def test_archived_direct_card_is_not_editable(self):
        card={'creator_user_id':'100','overall_status':'archived'}
        user={'user_id':'100','role_code':'sales_mgr'}
        self.assertFalse(cpq_wf._can_card_step(card,user,2,True))

    def test_direct_mode_does_not_run_legacy_auto_entry(self):
        root=Path(__file__).resolve().parents[1]
        page=(root/'确认需求解析结果.html').read_text()
        start=page.index('    async function runQuoteStepOnEntry(')
        end=page.index('    function wfMaybePromptStart()',start)
        script="""
const WF={directQuote:true,loaded:true,canEdit:true,awaiting:false,pendingTask:null,card:{overall_status:'in_progress'}};
const currentStep=3,manualMode=false,busy=false,currentSessionId='S',QUOTE_STEP_JOBS=new Map();
let reads=0;const ensureStep1Context=async()=>{reads++;throw Error('legacy auto-entry invoked');};
const addErrorBubble=()=>{};
"""+page[start:end]+"""
runQuoteStepOnEntry(3).then(result=>{if(reads!==0||result!==false)throw Error('direct mode invoked legacy pricing');}).catch(e=>{console.error(e);process.exitCode=1;});
"""
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_archived_direct_prepare_cannot_change_saved_inputs(self):
        fixture=direct_tests.WorkflowTests('test_real_workflow_six_steps_no_handoff_and_persistent_read')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        q=fixture.q;q.command('S',fixture.user,'enable')
        fixture.card['overall_status']='archived'
        with self.assertRaises((ValueError,cpq_wf.WfError)):
            q.command('S',fixture.user,'prepare',data={'prompt':fixture.natural_prompt()})

    def test_customer_packaging_prompt_updates_card_industry_from_server_facts(self):
        fixture=direct_tests.WorkflowTests('test_plain_prompt_prepares_and_completes_the_same_card')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.card['industry']='battery'
        result=fixture.q.command('S',fixture.user,'prepare',data={'prompt':fixture.natural_prompt()})
        self.assertEqual('packaging',result['card']['industry'])
