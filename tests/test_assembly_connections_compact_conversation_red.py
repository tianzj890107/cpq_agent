import copy,json,pathlib,re,subprocess,tempfile,unittest
from unittest.mock import patch
from tech_app.backend.services import packaging_assembly as service,integration
from tech_app.backend.models.integration import IntegrationPlan,IntegrationParamPlan
from tech_app.backend.models.process import ProcessPlan

ROOT=pathlib.Path(__file__).resolve().parents[1]
DOC={'business_parts_hash':'sample','business_parts':[
    {'business_part_code':'X1','name':'盖面纸','material_text':'纸'},
    {'business_part_code':'X2','name':'盖灰板','material_text':'灰板'},
    {'business_part_code':'X3','name':'底盒','material_text':'灰板'}]}
INTERFACES=[{'name':'盖纸裱合盖板','parts':['X1','X2'],'method':'裱合'},
            {'name':'盖板与底盒连接','parts':['X2','X3'],'method':'粘合'}]

class AssemblyConnections(unittest.TestCase):
    def prepare(self,run,*,interfaces=INTERFACES,records=None,automatic=True):
        plan=IntegrationPlan(params=IntegrationParamPlan(interfaces=interfaces))
        with patch.object(service,'active_inputs',return_value=(copy.deepcopy(DOC),records or {})),patch.object(integration,'load_plan',return_value=plan),patch.object(integration,'save_plan'):
            return service.prepare('P',{'username':'PE1'},run=run,automatic=automatic)

    def test_generic_total_assembly_is_not_accepted_as_connection_coverage(self):
        def run(*args):return ProcessPlan(steps=[{'step_no':10,'name':'整机组装','type':'assembly','description':'将全部零件组装','part_codes':['X1','X2','X3']}])
        plan=self.prepare(run)
        self.assertGreaterEqual(len(plan.process.steps),2)
        self.assertEqual({('X1','X2'),('X2','X3')},{tuple(sorted(s.part_codes)) for s in plan.process.steps})
        self.assertTrue(all(s.name not in ('整机组装','整盒组装') for s in plan.process.steps))

    def test_model_failure_uses_real_connections_not_fake_global_operation(self):
        plan=self.prepare(lambda *a:(_ for _ in ()).throw(RuntimeError('offline')))
        self.assertEqual(2,len(plan.process.steps))
        self.assertAlmostEqual(30,sum(s.standard_seconds or 0 for s in plan.process.steps))
        self.assertTrue(all(s.recommendation['kind']!='ai' for s in plan.process.steps))

    def test_no_connection_evidence_cannot_claim_successful_global_assembly(self):
        plan=self.prepare(lambda *a:(_ for _ in ()).throw(RuntimeError('offline')),interfaces=[])
        self.assertEqual([],plan.process.steps)
        self.assertTrue(plan.process.open_questions)

    def test_manufacturing_already_covers_one_connection(self):
        records={'X1':{'plan':{'steps':[{'name':'裱合','description':'已完成盖纸与灰板裱合','part_codes':['X1','X2']}]}}}
        plan=self.prepare(lambda *a:(_ for _ in ()).throw(RuntimeError('offline')),records=records)
        self.assertEqual([['X2','X3']],[s.part_codes for s in plan.process.steps])

    def test_one_model_call_receives_connections_and_preserves_inferred_source(self):
        calls=[]
        def run(system,content,schema):
            calls.append(json.loads(content[0]['text']))
            return {'summary':'连接推荐','assembly_connections':[{'id':'J1','part_codes':['X1','X2'],'name':'盖纸裱合盖板','method':'裱合','evidence':'名称与材料对应'}],
                'steps':[{'step_no':10,'name':'盖纸与盖板裱合','type':'assembly','description':'将盖面纸裱在盖灰板上','part_codes':['X1','X2'],'assembly_connection_ids':['J1']}]}
        plan=self.prepare(run,interfaces=[])
        self.assertEqual(1,len(calls))
        self.assertIn('connections',calls[0])
        self.assertEqual('ai_inferred',plan.process.assembly_connections[0]['source'])
        self.assertEqual(['J1'],plan.process.steps[0].assembly_connection_ids)

    def test_old_generic_cache_cannot_be_reused(self):
        legacy=IntegrationPlan(process=ProcessPlan(steps=[{'step_no':10,'name':'整盒组装','type':'assembly','description':'整盒','part_codes':['X1','X2','X3']}]),packaging_assembly_scope=service.SCOPE,packaging_assembly_input_hash=service.input_hash(DOC,{}))
        self.assertFalse(service.valid(legacy,DOC,{}))

    def test_renamed_reordered_parts_work_without_sample_specific_rules(self):
        renamed=copy.deepcopy(DOC);aliases={'X1':'random-C','X2':'random-A','X3':'random-B'}
        renamed['business_parts'].reverse()
        for i,row in enumerate(renamed['business_parts']):row.update(business_part_code=aliases[row['business_part_code']],name='不同产品的部件'+str(i))
        interfaces=copy.deepcopy(INTERFACES)
        for row in interfaces:row['parts']=[aliases[c] for c in row['parts']]
        with patch(__name__+'.DOC',renamed):
            plan=self.prepare(lambda *a:(_ for _ in ()).throw(RuntimeError('offline')),interfaces=interfaces)
        self.assertEqual({('random-A','random-C'),('random-A','random-B')},{tuple(sorted(s.part_codes)) for s in plan.process.steps})

    def test_unknown_model_ids_are_rejected_not_saved(self):
        def run(*a):return {'assembly_connections':[{'id':'J','name':'粘合','method':'粘合','part_codes':['X1','invented'],'evidence':'模型猜测'}], 'summary':'错误候选','steps':[]}
        # 新合同：坏关系被隔离，已有的有效接口仍保留，而不是整份丢掉。
        result=self.prepare(run,automatic=False)
        self.assertTrue(result.packaging_assembly_attempt['issues'])
        self.assertEqual(2,len(result.process.steps))
        self.assertNotIn('invented',str([s.part_codes for s in result.process.steps]))
        self.assertNotIn('J',[r['id'] for r in result.process.assembly_connections])

    def test_substring_names_do_not_fake_an_already_covered_connection(self):
        renamed=copy.deepcopy(DOC);renamed['business_parts'][0]['name']='面纸';renamed['business_parts'][1]['name']='盖面纸'
        records={'X2':{'plan':{'steps':[{'name':'裱合','description':'盖面纸裱合'}]}}}
        with patch(__name__+'.DOC',renamed):
            plan=self.prepare(lambda *a:(_ for _ in ()).throw(RuntimeError('offline')),records=records)
        self.assertEqual(2,len(plan.process.steps))

    def test_two_distinct_actions_on_one_connection_are_kept(self):
        def run(*a):return {'assembly_connections':[{'id':'J','name':'盖纸与盖板粘合','method':'粘合','part_codes':['X1','X2'],'evidence':'零件名称与材料对应'}],
            'steps':[{'step_no':20,'name':'涂胶定位','type':'assembly','description':'涂胶定位','part_codes':['X1','X2'],'assembly_connection_ids':['J']},
                     {'step_no':40,'name':'压合固化','type':'assembly','description':'压合','part_codes':['X1','X2'],'assembly_connection_ids':['J'],'depends_on':[20]}]}
        plan=self.prepare(run,interfaces=[])
        self.assertEqual(['涂胶定位','压合固化'],[s.name for s in plan.process.steps])
        self.assertEqual([10],plan.process.steps[1].depends_on)
        self.assertAlmostEqual(30,sum(s.standard_seconds for s in plan.process.steps))
        self.assertIn('X3',[q.field for q in plan.process.open_questions])

    def test_real_standard_time_is_not_replaced_by_unknown_budget(self):
        def run(*a):return {'summary':'标准与估算分开','steps':[{'step_no':10,'name':'面纸与灰板裱合','type':'assembly','description':'裱合','part_codes':['X1','X2'],'standard_seconds':120,'time_source':'provided_standard'}]}
        plan=self.prepare(run)
        self.assertEqual(120,plan.process.steps[0].standard_seconds)
        self.assertEqual(30,plan.process.steps[1].standard_seconds)

    def test_changed_interfaces_invalidate_cached_result(self):
        from tech_app.backend.models.integration import IntegrationInterface
        plan=self.prepare(lambda *a:ProcessPlan(summary='由接口编制',steps=[]))
        self.assertTrue(service.valid(plan,DOC,{}))
        plan.params.interfaces.append(IntegrationInterface(name='新的连接',parts=['X1','X3'],method='卡扣'))
        self.assertFalse(service.valid(plan,DOC,{}))

    def test_real_json_save_reload_and_route_keep_connection_bindings(self):
        from tech_app.backend.storage import store,meta_backend
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'_meta',return_value=meta_backend.JsonMetaBackend(pathlib.Path(folder))),patch.object(store,'_touch_stage'),patch.object(store,'audit'),patch.object(service,'active_inputs',return_value=(copy.deepcopy(DOC),{})):
            integration.save_plan('isolated',IntegrationPlan(params=IntegrationParamPlan(interfaces=INTERFACES)))
            result=service.prepare('isolated',{'username':'PE1'},run=lambda *a:ProcessPlan(summary='已有接口',steps=[]),automatic=True)
            restored=integration.load_plan('isolated')
            self.assertEqual(result.process.model_dump(),restored.process.model_dump())
            manufacture=[{'step_no':10,'step_name':'模切','part_code':'X1'}]
            route=service.append_operations('isolated',manufacture)
            self.assertEqual(3,len(route))
            self.assertTrue(all(s['assembly_connection_ids'] and s['usage_qty']==1 for s in route[1:]))
            self.assertEqual(1,len(manufacture))

class CompactConversation(unittest.TestCase):
    def test_real_chrome_details_hidden_and_long_cards_expand_without_summary(self):
        chrome=pathlib.Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        css=(ROOT/'tech_app/frontend/agent-chat.css').read_text()
        helper=ROOT/'tech_app/frontend/process-card-fold.js'
        script=helper.read_text() if helper.exists() else ''
        script+="""
window.addEventListener('load',()=>setTimeout(()=>{
 try{
 const host=document.querySelector('.oc-task-steps');
 const hidden=document.querySelector('[data-agent-role="tool-detail"]');
 if(getComputedStyle(hidden).display!=='none')throw Error('hidden detail is displayed');
 if(!window.CPQProcessCardFold)throw Error('shared fold behavior missing');
 CPQProcessCardFold.sync(host);
 const card=document.querySelector('.oc-amsg'), short=card.getBoundingClientRect().height;
 if(short>350)throw Error('collapsed card still too tall');
 const toggle=card.querySelector('[data-process-overflow-toggle]');
 if(!toggle||toggle.getAttribute('aria-expanded')!=='false')throw Error('long process not folded');
 if(card.querySelector('[data-result-summary]'))throw Error('unwanted result summary');
 toggle.click();
 if(card.getBoundingClientRect().height<=short+100)throw Error('expand did not reveal logs');
 host.appendChild(host.children[0].cloneNode(true));CPQProcessCardFold.sync(host);
 if(toggle.getAttribute('aria-expanded')!=='true')throw Error('new progress collapsed user selection');
 toggle.click();if(card.getBoundingClientRect().height>short+40)throw Error('collapse failed');
 const small=document.createElement('div');small.className='oc-process-steps';
 for(let i=0;i<3;i++)small.appendChild(host.children[0].cloneNode(true));
 document.body.append(small);CPQProcessCardFold.sync(small);
 if(small.__cpqOverflowToggle)throw Error('short bubble changed');
 document.getElementById('probe').textContent='PASS';
 }catch(e){document.getElementById('probe').textContent='FAIL:'+e.message}
},100));
"""
        rows=''.join('<div class="oc-process-step"><span class="oc-process-dot">✓</span><span class="oc-process-text">工艺步骤 '+str(i)+'</span></div>' for i in range(40))
        with tempfile.TemporaryDirectory() as folder:
            page=pathlib.Path(folder)/'probe.html'
            page.write_text('<!doctype html><meta charset="utf-8"><style>'+css+'</style><div class="oc-amsg oc-task-card"><div class="oc-abody"><div class="oc-alabel">技术工艺智能体 <span class="oc-task-state">已完成</span></div><div class="oc-task-steps">'+rows+'<div class="oc-process-body" data-agent-role="tool-detail" hidden>完整查询条件</div></div></div></div><pre id="probe"></pre><script>'+script+'</script>')
            result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking','--user-data-dir='+str(pathlib.Path(folder)/'profile'),'--virtual-time-budget=1500','--dump-dom',page.as_uri()],capture_output=True,text=True,timeout=30)
            match=re.search(r'<pre id="probe">([^<]+)</pre>',result.stdout)
            self.assertIsNotNone(match,result.stderr[-300:]);self.assertEqual('PASS',match.group(1))
