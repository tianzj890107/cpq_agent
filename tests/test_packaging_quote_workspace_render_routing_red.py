import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackagingWorkspaceTests(unittest.TestCase):
    def test_executable_workspace_refresh_reads_without_creating(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('报价首页.html','utf8');
const fn=src.slice(src.indexOf('async function initQuickQuoteWorkspacePage()'),src.indexOf("document.addEventListener('DOMContentLoaded', initQuickQuoteWorkspacePage)"));
class Node {constructor(){this.children=[];this.events={};}prepend(n){this.children.unshift(n);}insertBefore(n){this.children.push(n);}appendChild(n){this.children.push(n);}setAttribute(){}addEventListener(k,fn){this.events[k]=fn;}querySelectorAll(){return this.children;}}
const nodes={},body={classList:{add:()=>{}}},document={body,createElement:()=>new Node(),getElementById:id=>nodes[id]||(nodes[id]=new Node())};
const reads=[],statuses=[],panel={renderParseEntry:()=>new Node(),openQuickQuoteWorkspace:async id=>{reads.push(id);return {ok:true,quote_mode:'quick',inputs:{inner_length:20},match:{candidates:[]}};},
openQuickQuoteSession:()=>{throw Error('refresh must not create');},matchQuickQuoteCases:()=>{throw Error('refresh must not rematch');}};
const ctx={URLSearchParams,document,window:{location:{search:'?workspace=quick&session=existing',pathname:'/报价首页.html'}},
quickQuoteWorkspaceBox:()=>document.getElementById('quickQuoteWorkspace'),setActiveQuoteMode:()=>{},quickQuotePanelApi:()=>panel,
textarea:new Node(),runQuickQuoteWorkspaceCommand:()=>{},renderQuickQuoteWorkspace:()=>{},quickQuoteStatus:t=>statuses.push(t),rememberQuickQuoteSession:()=>{},
sessionStorage:{getItem:()=>null},syncQuickQuoteWorkflowState:()=>{},quickQuoteExtractView:null};
vm.createContext(ctx);vm.runInContext(fn,ctx);
(async()=>{await ctx.initQuickQuoteWorkspacePage();assert.deepStrictEqual(reads,['existing']);assert.strictEqual(ctx.quickQuoteExtractView.inputs.inner_length,20);
panel.openQuickQuoteWorkspace=async()=>({ok:false,error:'读取不可用'});await ctx.initQuickQuoteWorkspacePage();assert(statuses.at(-1).includes('读取不可用'));assert(statuses.at(-1).includes('不会创建新实例'));})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_executable_submit_and_card_route(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('报价首页.html','utf8');
const between=(a,b)=>src.slice(src.indexOf(a),src.indexOf(b,src.indexOf(a)+a.length));
const store=new Map(),calls=[],panel={workspaceState:{quick_quote_session_id:'old'},
openQuickQuoteSession:async opts=>{calls.push(['create',opts]);return {ok:true,quick_quote_session_id:'new'};},
quickQuoteSessionId:()=>panel.workspaceState.quick_quote_session_id,
openQuickQuoteWorkspace:async id=>({ok:true,quote_mode:'quick',quick_quote_session_id:id})};
const ctx={quickQuoteCreating:false,quickQuotePanelApi:()=>panel,setActiveQuoteMode:()=>{},
quickQuoteStatus:()=>{},techIndustry:()=> 'packaging',rememberQuickQuoteSession:()=>{},
sessionStorage:{setItem:(k,v)=>store.set(k,v)},alert:msg=>{throw Error(msg);},
navigateQuickQuoteWorkspace:id=>calls.push(['navigate',id]),quickQuoteKnownIds:()=>[],OPEN_KEYS:{quote:'q'},
PAGES:{quote:'确认需求解析结果.html'},window:{location:{href:''}}};
vm.createContext(ctx);
vm.runInContext(between('async function submitQuickQuoteRequirement(text)','async function openQuickQuoteCard(sessionId)'),ctx);
vm.runInContext(between('async function openQuickQuoteCard(sessionId)','function navigateQuickQuoteWorkspace(id)'),ctx);
(async()=>{await ctx.submitQuickQuoteRequirement('酒盒需求');
assert.deepStrictEqual(calls.map(x=>x[0]),['create','navigate']);assert.strictEqual(calls[1][1],'new');
assert.strictEqual(store.get('cpq:quickRequirement:new'),'酒盒需求');assert.strictEqual(panel.workspaceState.quick_quote_session_id,'');
await ctx.openQuickQuoteCard('existing');assert.strictEqual(calls.at(-1)[1],'existing');
const location={href:'https://example.test/报价首页.html?old=1'};
const urlCtx={URL,window:{location}};vm.createContext(urlCtx);
vm.runInContext(between('function navigateQuickQuoteWorkspace(id)','async function initQuickQuoteWorkspacePage()'),urlCtx);
urlCtx.navigateQuickQuoteWorkspace('a/b &中文');const out=new URL(location.href);
assert.strictEqual(out.searchParams.get('session'),'a/b &中文');assert.strictEqual(out.searchParams.get('workspace'),'quick');assert(!out.searchParams.has('old'));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mode_click_only_selects(self):
        source = (ROOT / '报价首页.html').read_text()
        block = source.split('(function initQuoteModeEntries()', 1)[1].split('})();', 1)[0]
        self.assertNotIn('openQuickQuoteHomeWorkspace();', block)
        self.assertNotIn("requestNavigate('quote'", block)

    def test_card_and_submit_navigate_instance_workspace(self):
        source = (ROOT / '报价首页.html').read_text()
        submit = source.split('async function submitQuickQuoteRequirement(text)', 1)[1].split('async function openQuickQuoteCard', 1)[0]
        card = source.split('async function openQuickQuoteCard(sessionId)', 1)[1].split('async function submitRequirement', 1)[0]
        self.assertIn('navigateQuickQuoteWorkspace(sid)', submit)
        self.assertNotIn('matchQuickQuoteCases(', submit)
        self.assertIn('navigateQuickQuoteWorkspace(id)', card)
        self.assertIn('initQuickQuoteWorkspacePage', source)

    def test_selected_style_not_permanent_quick(self):
        source = (ROOT / '报价首页.html').read_text()
        self.assertIn('.quote-mode-entry--active', source)
        self.assertNotIn('.quote-mode-entry--quick { color: #FFFFFF', source)

    def test_real_renderer_preserves_all_columns_and_nested_package(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Node {constructor(tag){this.tag=tag;this.childNodes=[];this.attributes={};this._text='';}
 appendChild(n){this.childNodes.push(n);return n;} setAttribute(k,v){this.attributes[k]=v;}
 set textContent(v){this._text=String(v);} get textContent(){return this._text+this.childNodes.map(n=>n.textContent).join('|');}
 set innerHTML(v){this.childNodes=[];this._text='';} addEventListener(){} }
const document={createElement:t=>new Node(t)};const window={document};
vm.runInNewContext(fs.readFileSync('tech_app/frontend/packaging-quote-panel.js','utf8'),{window,document});
const target=new Node('div');window.PackagingQuotePanel.render(target,{sections:{s3_markup:{rows:[{'定价模式':'gross_margin','费率':0,'成本总额':12,'未税单价':16}]}},quote:{}});
assert(target.textContent.includes('gross_margin'));assert(target.textContent.includes('未税单价'));
assert.strictEqual(typeof window.PackagingQuotePanel.renderPackage,'function');
const pkg=new Node('div');window.PackagingQuotePanel.renderPackage(pkg,{requirement:{v_groove:false,new_da:0},bom:{items:[{name:'<内托>',sections:[{name:'圆片',confirmed_size:{length_mm:20,width_mm:20},quantity:2}]}]},route:{steps:[{name:'模切'}]},cost:{total_cost:0,gaps:['缺费率']}});
for(const text of ['false','new_da','0','<内托>','圆片','20','模切','缺费率']) assert(pkg.textContent.includes(text),text);
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
