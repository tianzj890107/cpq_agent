import copy
import importlib
import json
import pathlib
import subprocess
import unittest
from unittest.mock import patch,MagicMock
from tech_app.backend.services import requirement_service as rs, cost_review, cost_flow, integration
from tech_app.backend.models.integration import IntegrationPlan
from tech_app.backend.models.cost_review import CostReview

ROOT=pathlib.Path(__file__).resolve().parents[1]
class SequentialTests(unittest.TestCase):
    def test_slow_cost_read_aborts_with_retryable_timeout_not_empty_data(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        script="const crUrl=()=>'/cost-review';const api=(url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'}))));\n"+js_function(ROOT/'tech_app/frontend/cost-review.js','crReadCostData')+"\ncrReadCostData(5).catch(e=>console.log(JSON.stringify({code:e.code,message:e.message})));"
        out=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,out.returncode,out.stderr)
        value=json.loads(out.stdout)
        self.assertEqual('cost-read-timeout',value['code'])
        self.assertIn('重试',value['message'])

    def test_three_cost_stage_projections_reuse_one_request_snapshot(self):
        from tech_app.backend.services import workflow_projection
        facts={'plan':IntegrationPlan()}
        with patch.object(cost_review,'summarize',return_value={'counts':{}}) as summarize:
            for _ in range(3):workflow_projection._cost_data('P',facts)
        self.assertEqual(1,summarize.call_count)

    def test_cost_start_finishes_without_waiting_for_timeline(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        script=r'''
let crPid='P',crTaskId='',crUser,crData,crTab='parts';
const crRequestedView='';
const events=[],nodes={};
const $cr=id=>nodes[id]||(nodes[id]={});
const crBind=()=>{},crLoadModelLabel=()=>{},crUrl=()=>'/cost-review';
const crReadOnly=()=>false,crReadOnlyWhy=()=>'';
const window={cpqAuth:{user:()=>({username:'FI1'})}};
const api=async()=>({review:{confirmed:false},quantity:1000,ready:true,counts:{missing:[]},parts:[{id:'A'}]});
const crRender=()=>events.push('render'),crStatus=()=>events.push('ready'),crSay=()=>{};
const crReplayTimeline=()=>new Promise(()=>{});
'''+js_function(ROOT/'tech_app/frontend/cost-review.js','crReadCostData')+'\n'+js_function(ROOT/'tech_app/frontend/cost-review.js','crStart')+r'''
Promise.race([crStart().then(()=>true),new Promise(resolve=>setTimeout(()=>resolve(false),100))]).then(done=>console.log(JSON.stringify({done,events})));
'''
        out=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,out.returncode,out.stderr)
        value=json.loads(out.stdout)
        self.assertTrue(value['done'])
        self.assertEqual(['render','ready'],value['events'])

    def test_confirmation_real_payload_reuses_one_ledger_and_discloses_signature(self):
        from tech_app.backend.services import packaging_cost_ledger
        req={'data':{'industry':'packaging','quote_quantity':1000}}
        view={'parts':[{'id':'A'}],'reconciled':False,'reconciliation_errors':['amount_mismatch'],
            'packaging_cost':{'built':True,'stale':True,'quote_quantity':1000,'gaps':[]},
            'counts':{'parts':1,'parts_costed':1,'missing':[],'zero':[],'assembly_costed':False},'final':{'total':50}}
        review=CostReview();plan=IntegrationPlan()
        with patch.object(cost_flow,'cost_review_ctx',return_value=(None,plan,review)),patch.object(cost_flow.store,'load_requirement',return_value=req),patch.object(packaging_cost_ledger,'load',return_value=view) as load,patch.object(cost_review,'save_review'),patch.object(cost_flow.store,'audit'),patch.object(integration,'project_family',return_value='packaging'),patch.object(integration,'missing_required',return_value=[]):
            result=cost_flow.confirm_review('P',{'username':'FI1'},waiver={})
            self.assertTrue(result['review']['confirmed'])
            self.assertEqual('internal_estimate',result['review']['confirmation_mode'])
            self.assertTrue(result['review']['cost_waiver']['missing_codes'])
            self.assertEqual(1,load.call_count)

    def test_no_signature_and_no_objects_are_not_falsely_confirmed(self):
        from tech_app.backend.services import packaging_cost_ledger
        view={'reconciled':False,'packaging_cost':{'stale':True},'counts':{'parts':1,'missing':[],'zero':[],'assembly_costed':False},'final':{'total':50}}
        review=CostReview()
        with patch.object(cost_flow,'cost_review_ctx',return_value=(None,IntegrationPlan(),review)),patch.object(cost_flow.store,'load_requirement',return_value={'data':{'industry':'packaging'}}),patch.object(packaging_cost_ledger,'load',return_value=view):
            with self.assertRaises(cost_flow.CostFlowError):cost_flow.confirm_review('P',{'username':'FI1'})
            view['counts']['parts']=0
            with self.assertRaises(cost_flow.CostFlowError):cost_flow.confirm_review('P',{'username':'FI1'},waiver={})
        self.assertFalse(review.confirmed)

    def test_frozen_engineering_values_feed_missing_inputs_but_never_replace_customer(self):
        from tech_app.backend.services.packaging_semantics import provenance
        req={'industry':'packaging','revision':2,'inner_length':90}
        evidence={'requirement_revision':2,'ir_hash':'H','fields':{
            'inner_length':{'value':100,'status':'confirmed'},'inner_width':{'value':40,'status':'confirmed'},
            'inner_height':{'value':50,'status':'needs_confirmation'}}}
        backend=MagicMock()
        backend.get_doc.side_effect=lambda pid,key:evidence if key=='packaging_engineering_fields' else {'ir_hash':'H'}
        self.assertTrue(hasattr(provenance,'engineering_inputs'),'缺少只读工程输入投影')
        with patch.object(provenance.requirement_service.store,'_meta',return_value=backend):
            out=provenance.engineering_inputs('P',req)
        self.assertEqual(90,out['inner_length'])
        self.assertEqual(40,out['inner_width'])
        self.assertNotIn('inner_height',out)
        self.assertNotIn('inner_width',req)

    def test_real_frontend_gate_sends_empty_waiver_after_proceed(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        source=(ROOT/'tech_app/frontend/cost-review.js').read_text()
        register=source[source.index('(function crRegisterTechBoardActions()'):]
        script=r'''
let actions,crConfirmGate,crTab='parts',crBusy=false,crDeferredBusy=false;
let crData={packaging_cost:{built:true},counts:{parts:1,missing:[],zero:[],assembly_costed:true},review:{gaps:{codes:['tooling:missing'],fields:['模具摊销缺失'],text:'模具摊销缺失'}}};
const events=[];
const window={TechBoardRuntime:{registerActions:a=>actions=a,registerViews:()=>{}}};
const crReadOnly=()=>false,crReadOnlyWhy=()=>'';
const crStatus=()=>{},crSay=()=>{};
const crAskProceed=async()=>{events.push('ask');return true};
const crConfirmCost=async waiver=>{events.push(waiver);return true};
'''+''.join(js_function(ROOT/'tech_app/frontend/cost-review.js',f)+'\n' for f in ('crConfirmBlocker','crWaiverCoversGaps','crConfirmGaps'))+register+r'''
actions.confirmCostReview.gate().then(result=>console.log(JSON.stringify({result,events})));
'''
        out=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,out.returncode,out.stderr)
        value=json.loads(out.stdout)
        self.assertEqual(['ask',{}],value['events'])
        self.assertTrue(value['result']['ok'])

    def test_signed_quality_confirmation_is_projected_as_internal_completion(self):
        from tech_app.backend.services import workflow_projection
        review=CostReview(confirmed=True)
        cost_review.record_waiver(review,gaps={'codes':['tooling:missing'],'fields':['模具缺参数']},actor={'username':'FI1'})
        data={'parts':[{'id':'A','has_cost':False}],'packaging_cost':{'built':True},'counts':{'missing':['A']}}
        with patch.object(workflow_projection,'_cost_data',return_value=data),patch.object(cost_review,'confirm_gaps',return_value={'codes':['tooling:missing'],'fields':['模具缺参数']}):
            result=workflow_projection._judge_cost('4.1','P',{},IntegrationPlan(),review)
        self.assertTrue(result['completed'])
        self.assertEqual('internal_estimate',result['confirmation_mode'])

    def test_requirement_stages_do_not_require_future_drawing(self):
        from tests.test_packaging_requirement_confirm_order_guard_red import FakeStore,DRAFT,blocked_prerequisite
        fake=FakeStore(DRAFT)
        with patch.object(rs,'store',fake),patch.object(rs,'drawing_parse_prerequisite',return_value=blocked_prerequisite()),patch.object(rs,'requirement_gaps',return_value={'keys':[],'labels':[]}):
            a=rs.submit_requirement_confirmation('P',{'username':'PE1'})
            b=rs.confirm_requirement('P',{'username':'PE1'})
            c=rs.review_requirement('P',{'username':'PE1'},'approve')
        self.assertEqual(['pending_confirmation','pending_review','approved'],[a['status'],b['status'],c['status']])

    def test_approved_packaging_can_parse_without_reopening_customer_requirement(self):
        flow=importlib.import_module('tech_app.backend.services.packaging_drawing_flow')
        with patch.object(flow.store,'load_requirement',return_value={'status':'approved','data':{'industry':'packaging'}}):
            self.assertEqual([],flow.preconditions('P'))

    def test_frozen_customer_fields_are_separate_engineering_evidence(self):
        steps=importlib.import_module('tech_app.backend.services.packaging_drawing_flow.steps')
        req={'status':'approved','requirement_no':'R','data':{'industry':'packaging','inner_length':90}}
        backend=MagicMock();module=MagicMock()
        ctx={'project_id':'P','run_id':'run','actor':'PE1','semantics':{'fields':{'inner_length':{'value':100,'status':'confirmed','origin':'cad_dimension'}}},'anchor':{'ir_id':'IR','ir_hash':'HASH','unit_status':'confirmed'}}
        with patch.object(steps.store,'load_requirement',return_value=req),patch.object(steps.store,'_meta',return_value=backend),patch.object(steps,'_resolve',return_value=module),patch.object(steps,'_emit'):
            result=steps.field_write(ctx)
        self.assertEqual('completed',result['status'])
        module.apply_to_requirement.assert_not_called()
        backend.put_doc.assert_called_once()
        self.assertEqual('packaging_engineering_fields',backend.put_doc.call_args.args[1])
        self.assertEqual(90,req['data']['inner_length'])

    def test_cost_page_does_not_load_full_manufacturing_snapshot(self):
        from tech_app.backend import main
        req={'data':{'industry':'packaging'}}
        with patch.object(main.store,'load_meta',return_value={ 'project_id':'P'}),patch.object(main.store,'load_requirement',return_value=req),patch.object(main.manufacturing_snapshot,'load_snapshot') as heavy,patch.object(integration,'load_plan',return_value=IntegrationPlan()),patch.object(cost_review,'load_review',return_value=CostReview()):
            main._cost_review_ctx('P')
        heavy.assert_not_called()

    def test_explicit_empty_waiver_confirms_internal_draft_despite_quality_gaps(self):
        from tech_app.backend.services import packaging_cost_ledger
        review=CostReview();plan=IntegrationPlan()
        view={'reconciled':False,'packaging_cost':{'built':True,'stale':True},'counts':{'parts':1,'missing':[],'zero':[],'assembly_costed':False},'final':{'total':50}}
        gaps={'parts':1,'codes':['packaging:stale','ledger:mismatch','tooling:missing'],'fields':['成本过期','对账缺口','模具缺参数'],'text':'成本有质量缺口'}
        with patch.object(cost_flow,'cost_review_ctx',return_value=(None,plan,review)),patch.object(cost_flow.store,'load_requirement',return_value={'data':{'industry':'packaging'}}),patch.object(packaging_cost_ledger,'load',return_value=view) as load,patch.object(cost_review,'confirm_gaps',return_value=gaps),patch.object(cost_review,'save_review'),patch.object(cost_flow.store,'audit'),patch.object(cost_review,'payload',return_value={'review':{'confirmed':True}}):
            result=cost_flow.confirm_review('P',{'username':'FI1'},waiver={})
        self.assertTrue(result['review']['confirmed'])
        self.assertEqual(1,load.call_count)
        self.assertTrue(review.waivers)
        self.assertEqual(set(gaps['codes']),set(review.waivers[-1].missing_codes))

    def test_frontend_formula_gap_is_not_hidden_by_complete_part_counts(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        source=js_function(ROOT/'tech_app/frontend/cost-review.js','crConfirmBlocker')
        script="const crReadOnly=()=>false;const crReadOnlyWhy=()=>'';const crData={packaging_cost:{built:true},counts:{parts:1,missing:[],zero:[],assembly_costed:true},review:{gaps:{codes:['tooling:missing'],text:'模具摊销依据缺失'}}};\n"+source+"\nconsole.log(JSON.stringify(crConfirmBlocker()));"
        out=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,out.returncode,out.stderr)
        self.assertIn('模具',json.loads(out.stdout))
