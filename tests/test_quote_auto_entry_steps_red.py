import subprocess
import unittest
from pathlib import Path
from tests.test_packaging_whole_part_process_unit_red import js_function

PAGE = Path(__file__).resolve().parents[1] / '确认需求解析结果.html'


class AutoEntry(unittest.TestCase):
    def run_js(self, case):
        script = '''
let currentSessionId='Q',currentStep=3,busy=false,manualMode=false;
let WF={loaded:true,canEdit:true,awaiting:false,pendingTask:null,card:{overall_status:'in_progress'}};
let LAST_PACKAGING_QUOTE=null,PACKAGING_PRICING_DIRTY=false;
const QUOTE_STEP_JOBS=new Map(),sections=new Map(),STEPS=['需求','工艺','定价','加价','方案','文档'];
let calls=[];let industry='packaging';
const currentIndustry=()=>industry,ensureStep1Context=async()=>{},ensureCarryData=async()=>{},
wfRestoreStepData=async()=>{},extractSectionRows=()=>[],collectStepData=()=>({}),
runMarkupStep=async n=>{calls.push('rule'+n);await Promise.resolve();return true;},
fillStep5=()=>{calls.push('fill5');return true;},repricePackagingWorkspace=async()=>{calls.push('save5');return true;},
beginUserTurn=()=>{},sendToAgent=async()=>{calls.push('document');return true;},addErrorBubble=()=>{};
'''
        script += js_function(PAGE, 'runQuoteStepOnEntry') + '\n'
        script += '(async()=>{' + case + '})().catch(e=>{console.error(e);process.exit(1)});'
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_missing_started_step_runs_and_concurrent_entry_coalesces(self):
        self.run_js("await Promise.all([runQuoteStepOnEntry(3),runQuoteStepOnEntry(3)]);if(calls.join()!=='rule3')throw Error(calls);")

    def test_saved_rules_refresh_does_not_recalculate(self):
        self.run_js("LAST_PACKAGING_QUOTE={rule_pricing:{stages:{'3':{}}}};await runQuoteStepOnEntry(3);if(calls.length)throw Error(calls);")

    def test_readonly_past_and_completed_do_not_execute(self):
        self.run_js("WF.canEdit=false;await runQuoteStepOnEntry(3);WF.canEdit=true;await runQuoteStepOnEntry(4);WF.card.overall_status='completed';await runQuoteStepOnEntry(3);if(calls.length)throw Error(calls);")

    def test_scheme_fills_and_saves_and_document_generates(self):
        self.run_js("currentStep=5;await runQuoteStepOnEntry(5);currentStep=6;await runQuoteStepOnEntry(6);if(calls.join()!=='fill5,save5,document')throw Error(calls);")

    def test_generic_industry_keeps_rule_calculation(self):
        self.run_js("industry='battery';currentStep=4;await runQuoteStepOnEntry(4);if(calls.join()!=='rule4')throw Error(calls);")

    def test_saved_document_is_restored_without_generation(self):
        self.run_js("currentStep=6;sections.set('s6_doc',{markdown:'已存报价单'});await runQuoteStepOnEntry(6);if(calls.length)throw Error(calls);")

    def test_stale_rules_recalculate(self):
        self.run_js("LAST_PACKAGING_QUOTE={rule_pricing:{stages:{'3':{}}}};PACKAGING_PRICING_DIRTY=true;await runQuoteStepOnEntry(3);if(calls.join()!=='rule3')throw Error(calls);")

    def test_force_retries_existing_rule(self):
        self.run_js("LAST_PACKAGING_QUOTE={rule_pricing:{stages:{'3':{}}}};await runQuoteStepOnEntry(3,true);if(calls.join()!=='rule3')throw Error(calls);")

    def test_switch_during_context_restore_never_runs_old_step(self):
        self.run_js("const job=runQuoteStepOnEntry(3);currentSessionId='OTHER';await job;if(calls.length)throw Error(calls);")

    def test_awaiting_handoff_never_runs(self):
        self.run_js("WF.awaiting=true;await runQuoteStepOnEntry(3);if(calls.length)throw Error(calls);")

    def test_entry_hooks_and_start_order(self):
        confirm=js_function(PAGE,'confirmStep')
        self.assertIn('await wfStartStep(next',confirm)
        self.assertLess(confirm.index('await wfStartStep(next'),confirm.index('await runQuoteStepOnEntry(next)'))
        self.assertIn('runQuoteStepOnEntry',js_function(PAGE,'wfStartStep'))
        self.assertIn('runQuoteStepOnEntry',js_function(PAGE,'wfMaybePromptStart'))
        self.assertIn('wfMaybePromptStart()',js_function(PAGE,'openSession'))

    def test_started_card_resume_runs_once_without_starting_over(self):
        script='''
const $=()=>null,wfUser=()=>({user_id:1});
let currentSessionId='Q',currentStep=3,BOOT_PENDING=false,busy=false,QUOTE_REPLAYING=false,QUOTE_RESUME_KEY='';
const WF={loaded:true,canEdit:true,stepStarted:true,awaiting:false,pendingTask:null};
const WF_AUTO={inflight:false,failedFor:''};
let calls=0;const runQuoteStepOnEntry=()=>{calls++;return Promise.resolve(true);};
const wfStartStep=()=>{throw Error('must not restart workflow');};
'''+js_function(PAGE,'wfMaybePromptStart')+'''
wfMaybePromptStart();wfMaybePromptStart();
if(calls!==1)throw Error('expected single resume, got '+calls);
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_retry_belongs_to_plain_table_not_bom(self):
        plain=js_function(PAGE,'renderTableSection')
        self.assertIn('systemRuleStep',plain)
        self.assertNotIn('systemRuleStep',js_function(PAGE,'renderBomTreeSection'))


if __name__ == '__main__':
    unittest.main()
