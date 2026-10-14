import pathlib
import subprocess
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
HTML=(ROOT/'确认需求解析结果.html').read_text()


class RefreshRestorationTests(unittest.TestCase):
    def test_boot_restores_before_new_without_creating_or_running(self):
        boot='async function boot()'+HTML.split('async function boot()',1)[1].split('\n    // 登录态就绪',1)[0]
        js=r'''
const calls=[];const sessionStorage={getItem:k=>k==='cpq:openSession'?'LEGACY':null,setItem:()=>{},removeItem:k=>calls.push(['remove',k])};
const localStorage={getItem:()=>null,setItem:()=>{}};
const location={search:'?session_id=ORIGINAL',protocol:'http:'};
const $=()=>({textContent:'',innerHTML:''});
let FORMS=[],INDUSTRIES=[],CURRENT_INDUSTRY='',manualMode=false,currentSessionId=null,BOOT_PENDING=true;
const AGENT_URL='/agents/quote';
const renderProgressBar=()=>{},waitForAuth=async()=>{},initModelInfoFallback=()=>{},setConn=()=>{},resolveInitialIndustry=()=> 'packaging',urlIndustry=()=>'',storedIndustry=()=>'',renderIndustrySelect=()=>{},refreshFixedFormsForIndustry=async()=>{},addErrorBubble=()=>{};
const cpqAuthFetch=async(url)=>{calls.push(['fetch',url]);return {ok:true,json:async()=>url.endsWith('/api/new')?{id:'NEW'}:{forms:[],industries:[]}}};
const openSession=async(id)=>{calls.push(['open',id]);return true};
''' +boot+r'''
boot().then(()=>{if(calls.some(x=>x[1]?.endsWith('/api/new')))throw Error('restore created a new session');if(!calls.some(x=>x[0]==='open'&&x[1]==='ORIGINAL'))throw Error('did not restore URL identity');console.log('ok')});
'''
        p=subprocess.run(['node','-e',js],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr[-1800:])

    def test_successful_open_pins_identity_and_awaits_recovery(self):
        fn=HTML.split('async function openSession(id)',1)[1].split('async function deleteSession',1)[0]
        self.assertTrue('pinQuoteSession(data.id)' in fn,'History open must pin actual identity')
        self.assertTrue('await wfRefresh(true)' in fn,'Restoration must await step snapshots')
        self.assertTrue('return false' in fn,'Failed open must not report success')

    def test_explicit_new_unbinds_old_identity(self):
        fn=HTML.split('async function startNewQuote()',1)[1].split('// 兼容旧入口',1)[0]
        self.assertTrue("searchParams.delete('session_id')" in fn)
        self.assertTrue("'cpq:openSession'" in fn)

    def test_replay_does_not_autorun_agent(self):
        fn=HTML.split('function wfMaybePromptStart()',1)[1].split('async function wfStartStep',1)[0]
        self.assertTrue('QUOTE_REPLAYING' in fn)
        self.assertTrue('QUOTE_RESUME_KEY' in fn)

    def test_tech_tab_selection_is_url_restorable(self):
        js=(ROOT/'tech_app/frontend/tech-workbench.js').read_text()
        fn=js.split('function navigateBoardView(view, label)',1)[1].split('/* 统一标题行',1)[0]
        self.assertTrue("searchParams.set('view', view)" in fn)
        cost=(ROOT/'tech_app/frontend/cost-review.js').read_text()
        self.assertTrue("get('view')" in cost)
        self.assertTrue('crRequestedView ||' in cost)

    def test_pin_preserves_industry_and_other_navigation_parameters(self):
        fn='function pinQuoteSession(id)'+HTML.split('function pinQuoteSession(id)',1)[1].split('async function openSession',1)[0]
        js="const location={href:'http://host/quote.html?industry=packaging&task_id=T'};let pinned='';const history={state:{keep:1},replaceState:(state,title,url)=>{pinned=url;if(state.keep!==1)throw Error('lost history state')}};"+fn+"pinQuoteSession('ORIGINAL');const q=new URL(pinned).searchParams;if(q.get('session_id')!=='ORIGINAL'||q.get('industry')!=='packaging'||q.get('task_id')!=='T')throw Error('lost navigation parameters');"
        p=subprocess.run(['node','-e',js],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_auth_ready_does_not_register_an_empty_replacement_card(self):
        fn=HTML.split('const onAuthChanged =',1)[1].split("document.addEventListener('cpq-auth-ready'",1)[0]
        self.assertTrue('QUOTE_REPLAYING' in fn)
        self.assertTrue('BOOT_PENDING' in fn)
        self.assertFalse('wfSyncCard' in fn)

    def test_restore_failure_does_not_crash_when_placeholder_was_removed(self):
        boot='async function boot()'+HTML.split('async function boot()',1)[1].split('\n    // 登录态就绪',1)[0]
        self.assertTrue("const placeholder = $('panelPlaceholder')" in boot)
        self.assertTrue('if (placeholder)' in boot)

    def test_real_summary_helper_preserves_saved_values_and_ignores_metadata(self):
        self.assertTrue('function summarySectionRows(payload)' in HTML, 'Snapshot restore calls an undefined helper')
        fn='function summarySectionRows(payload)'+HTML.split('function summarySectionRows(payload)',1)[1].split('async function wfRestoreStepData',1)[0]
        js=fn+"const a=summarySectionRows({kind:'summary',fields:[{label:'加价',value:0},{label:'需要',value:false}]});if(a.rows.length!==2||a.rows[0]['值']!==0||a.rows[1]['值']!==false)throw Error('lost zero/false');const b=summarySectionRows({kind:'summary',rows:[{'金额':10,'数量':2}]});if(b.columns.join(',')!=='金额,数量')throw Error('lost table columns');if(summarySectionRows({cost:{total_cost:10}}).rows.length)throw Error('rendered technical metadata');"
        p=subprocess.run(['node','-e',js],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_tech_view_action_pins_only_after_success(self):
        source=(ROOT/'tech_app/frontend/tech-workbench.js').read_text()
        fn='function navigateBoardView(view, label)'+source.split('function navigateBoardView(view, label)',1)[1].split('/* 统一标题行',1)[0]
        js="const location={href:'http://host/tech-workbench.html?project=P&stage=cost&task_id=T'};let pinned='';const history={state:null,replaceState:(s,t,url)=>pinned=url};const boardBridge=()=>({navigateView:async()=>{}}),setBoardNotice=()=>{},syncChildTabActive=()=>{};const state={stage:'cost'},CHILD_TAB_PROXY={cost:{}};"+fn+"navigateBoardView('assembly','整单成本');setImmediate(()=>{const q=new URL(pinned).searchParams;if(q.get('project')!=='P'||q.get('stage')!=='cost'||q.get('task_id')!=='T'||q.get('view')!=='assembly')throw Error('lost tech navigation')});"
        p=subprocess.run(['node','-e',js],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
