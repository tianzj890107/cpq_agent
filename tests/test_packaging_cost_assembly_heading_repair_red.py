import copy
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
FRONT=ROOT/'tech_app/frontend'

def function(source,name):
    start=source.index('function '+name+'(')
    indent=re.match(' *',source[source.rfind('\n',0,start)+1:start]).group()
    return source[start:source.index('\n'+indent+'}',start)+len(indent)+2]

def node(script):
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=30)
    if result.returncode:raise AssertionError(result.stderr)

class LoadingAndAssembly(unittest.TestCase):
    def test_saved_cost_does_not_copy_cad_candidates(self):
        from tech_app.backend.storage import meta_backend,store,da_repo
        from tech_app.backend.services import cost_review_loading,packaging_parts,cost_review
        with tempfile.TemporaryDirectory() as folder:
            backend=meta_backend.JsonMetaBackend(Path(folder))
            doc={'scheme_labels':['1','2'],'selected_scheme_id':'1','business_parts':[
                {'business_part_code':'a','name':'面纸','scheme_id':'1','reference':{'quantity':2},
                 'geometry_binding':{'candidates':[{'entities':['large']*1000}]}},
                {'business_part_code':'b','name':'另一方案','scheme_id':'2'}]}
            backend.put_doc('p',packaging_parts.BUSINESS_DOC_KEY,{'items':[doc]})
            row={'estimate_id':'e','material_total':4,'total_cost':4,'quote_quantity':1000}
            items=[{'part_code':'a','part_name':'面纸','cost_category':'material','amount':4,'inputs_json':'{}'}]
            original=copy.deepcopy
            def no_cad_copy(value,*args,**kwargs):
                if isinstance(value,dict) and ('geometry_binding' in str(value.keys()) or '"geometry_binding"' in json.dumps(value,ensure_ascii=False)):
                    raise AssertionError('首屏不能深复制完整CAD候选')
                return original(value,*args,**kwargs)
            with patch.object(meta_backend,'_backend',backend),patch.object(store,'load_meta',return_value={'project_id':'p'}), \
                 patch.object(store,'load_requirement',return_value={'requirement_no':'r','data':{'industry':'packaging','quote_quantity':1000}}), \
                 patch.object(da_repo,'load_packaging_cost',return_value=row),patch.object(da_repo,'load_packaging_cost_items',return_value=items), \
                 patch.object(cost_review,'load_review') as review,patch.object(copy,'deepcopy',side_effect=no_cad_copy):
                review.return_value.model_dump.return_value={}
                result=cost_review_loading.saved('p')
            self.assertEqual(['a'],[r['id'] for r in result['parts']])
            self.assertEqual(2,result['parts'][0]['quantity'])
            self.assertEqual(2,result['parts'][0]['unit_cost'])
            self.assertTrue(result['verification_pending'])
            self.assertFalse(result['ready'])

    def test_saved_failure_can_recover_from_verified_exit(self):
        node((FRONT/'cost-review-loader.js').read_text()+r'''
        (async()=>{
          const shown=[];
          const loader=CPQCostReviewLoader.create({readSaved:async()=>{throw Error('首屏暂时超时')},
            readVerified:async()=>({verification_pending:false,final:{total:7}}),render:d=>shown.push(d),failed:()=>{}});
          const result=await loader.load();await new Promise(r=>setTimeout(r,0));
          if(!result.ok||shown.at(-1)?.final.total!==7)throw Error('首屏失败直接放弃其他读取出口');
        })().catch(e=>{console.error(e);process.exitCode=1});
        ''')

    def test_empty_multi_part_assembly_is_not_reused_forever(self):
        from tests.test_assembly_connections_compact_conversation_red import DOC
        from tech_app.backend.services import packaging_assembly as svc,integration
        from tech_app.backend.models.integration import IntegrationPlan
        from tech_app.backend.models.process import ProcessPlan
        plan=IntegrationPlan(process=ProcessPlan(summary='尚未识别连接，待补'),packaging_assembly_scope=svc.SCOPE,
                             packaging_assembly_input_hash=svc.input_hash(DOC,{}))
        calls=[]
        def run(*args):calls.append(args);return ProcessPlan(summary='缺少连接依据')
        with patch.object(svc,'active_inputs',return_value=(copy.deepcopy(DOC),{})),patch.object(integration,'load_plan',return_value=plan),patch.object(integration,'save_plan'):
            result=svc.prepare('p',{'username':'PE1'},run=run,automatic=True)
        self.assertEqual(1,len(calls),'空缓存使重新推荐不再调用模型')
        self.assertFalse(result.process_confirmed)

    def test_model_receives_semantic_pair_candidates_not_auto_claimed_connections(self):
        from tests.test_assembly_connections_compact_conversation_red import AssemblyConnections
        seen=[]
        def run(system,content,schema):
            seen.append(json.loads(content[0]['text']))
            return {'summary':'无可靠连接结论','steps':[]}
        result=AssemblyConnections().prepare(run,interfaces=[])
        self.assertTrue(seen[0].get('connection_candidates'),'缺少供模型判断的零件角色候选')
        self.assertEqual([],result.process.steps,'未被模型采用的候选不可当真实工序')
        self.assertFalse(result.process_confirmed)

    def test_layout_gaps_do_not_block_independent_assembly_recommendation(self):
        source=(FRONT/'assembly-integration.js').read_text()
        node(function(source,'aiBlocker')+r'''
        const aiIndustry='packaging',aiData={};
        let aiBusinessParts={workspace:{scheme_ready:true,parts:[{code:'a'},{code:'b'}],status:{'3.1':{missing:['排模未关联']}}}};
        if(aiBlocker('process'))throw Error('排模缺口阻断独立组装推荐');
        aiBusinessParts.workspace.scheme_ready=false;
        if(!aiBlocker('process'))throw Error('未选择方案不应生成');
        ''')

    def test_empty_result_state_is_partial_not_completed(self):
        from tech_app.backend.services import packaging_assembly as svc
        from tech_app.backend.models.integration import IntegrationPlan
        from tech_app.backend.models.process import ProcessPlan
        from tech_app.backend.models.ir import OpenQuestion
        plan=IntegrationPlan(process=ProcessPlan(summary='未找到连接',open_questions=[OpenQuestion(field='连接',reason='依据缺失')]))
        self.assertEqual('partial',svc.recommendation_state(plan))

    def test_partial_task_keeps_business_readiness_dictionary(self):
        from tech_app.backend.services import tasks
        result={'status':{'has_process':True},'task_status':'partial','plan':{'process':{'steps':[]}}}
        with patch.object(tasks,'_finish_active',return_value=True) as finish:
            tasks._run('p','t','isolated_probe',lambda:result,False)
        final=finish.call_args.kwargs
        self.assertEqual('partial',final['status'])
        self.assertEqual({'has_process':True},final['result']['status'])

    def test_real_assembly_handler_marks_empty_result_partial(self):
        import ast,asyncio,types
        from typing import List
        from fastapi import Form,File,Depends,UploadFile
        from tech_app.backend.services import packaging_demo_flow,packaging_assembly,integration,tasks
        from tech_app.backend.storage import store
        from tech_app.backend.models.integration import IntegrationPlan
        from tech_app.backend.models.process import ProcessPlan
        from tech_app.backend.models.ir import OpenQuestion
        plan=IntegrationPlan(packaging_assembly_part_count=2,
            process=ProcessPlan(summary='连接未齐',open_questions=[OpenQuestion(field='a',reason='未匹配连接')]))
        tree=ast.parse((ROOT/'tech_app/backend/main.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='generate_integration_process')
        fn.decorator_list=[]
        async def attachments(*args):return []
        outputs=[]
        namespace={'__name__':'tech_app.backend.main','__package__':'tech_app.backend',
            'Form':Form,'File':File,'Depends':Depends,'UploadFile':UploadFile,'List':List,
            'current_user':lambda:None,'_require':lambda *a:None,'_workflow_project':lambda *a:None,
            'auth':types.SimpleNamespace(WRITE_ROLES=()),'store':store,'integration':integration,'tasks':tasks,
            '_read_attachments':attachments,'_integration_payload':lambda *a:{'status':{'has_process':True}}}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'real-assembly-handler','exec'),namespace)
        def submit(pid,kind,job):outputs.append(job());return 't'
        with patch.object(packaging_demo_flow,'ensure_inputs',return_value=False),patch.object(packaging_demo_flow,'sync_integration'), \
             patch.object(store,'load_requirement',return_value={'data':{'industry':'packaging'}}), \
             patch.object(packaging_assembly,'prepare',return_value=plan),patch.object(tasks,'submit',side_effect=submit),patch.object(tasks,'report_progress'):
            asyncio.run(namespace['generate_integration_process']('p',note='',attachments=[],user={'username':'PE1'}))
        self.assertEqual('partial',outputs[0]['task_status'])
        self.assertEqual({'has_process':True},outputs[0]['status'])

    def test_partial_frontend_keeps_regeneration_primary_and_reports_partial(self):
        source=(FRONT/'assembly-integration.js').read_text()
        has=source[source.index('const aiHasProcess ='):source.index(';',source.index('const aiHasProcess ='))+1]
        node(r'''
        let aiBusy=false,aiTab='process',aiIndustry='packaging',aiData={status:{has_process:true}};
        const aiBusinessParts={workspace:{assembly_result_state:'partial'}},states=[],events=[],aiEditing={};
        const $ai=()=>null,aiBlocker=()=>'',aiRenderActions=()=>{},aiStatus=()=>{},aiUserSay=()=>{},aiUrl=x=>x;
        const AI_TAB_NAMES={process:'组装工艺'},AI_TABS={},api=async()=>({task_id:'t'});
        const aiProcessCard=()=>({done:s=>states.push(s),log:()=>{}});
        const aiPublishTask=(name,d)=>events.push(name),aiRefreshPackagingWorkspace=async()=>{},aiRender=()=>{};
        const aiPollTask=async()=>({task_status:'partial'});
        '''+has+'async '+function(source,'aiPost')+r'''
        (async()=>{
          if(aiHasProcess())throw Error('空结果仍把发送财务当主操作');
          const result=await aiPost('process',{keepTab:true});
          if(!result.partial||states[0]!=='partial'||events.at(-1)!=='task-partial'||aiBusy)throw Error('部分完成被覆盖或busy未释放');
        })().catch(e=>{console.error(e);process.exitCode=1});
        ''')

    def test_pair_hints_are_side_sensitive_and_ambiguous_layers_are_not_guessed(self):
        from tech_app.backend.services import packaging_assembly_connections as svc
        def doc(names):return {'business_parts':[{'business_part_code':str(i),'name':name} for i,name in enumerate(names)]}
        self.assertEqual([],svc.candidate_pairs(doc(['左盖面纸','右盖灰板'])))
        self.assertEqual([],svc.candidate_pairs(doc(['盖面纸','盖灰板1','盖灰板2'])))
        pairs=svc.candidate_pairs(doc(['另一产品底板','另一产品底板面纸']))
        self.assertEqual(['1','0'],pairs[0]['part_codes'])
        self.assertTrue(pairs[0]['needs_model_judgement'])

    def test_real_dom_failed_verification_keeps_money_and_retry(self):
        chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        source=(FRONT/'cost-review.js').read_text()
        coordinator=source[source.index('const crLoadCoordinator ='):source.index('async function crStart()')]
        script=r'''
        let opts,retries=0,crData={final:{total:12.34}};
        const $cr=id=>document.getElementById(id),esc=s=>String(s),crStatus=()=>{},crStart=()=>retries++;
        const CPQCostReviewLoader={create:o=>{opts=o;return {}}};
        '''+coordinator+r'''
        try{
          opts.failed(Error('核验超时'));
          if(!document.getElementById('crBody').textContent.includes('12.34'))throw Error('已有成本被清空');
          const button=document.querySelector('#crVerificationRetry button');
          if(!button||button.textContent!=='重试核验')throw Error('无重试入口');
          button.click();if(retries!==1)throw Error('重试未接线');
          document.getElementById('probe').textContent='PASS';
        }catch(e){document.getElementById('probe').textContent=e.stack}
        '''
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'retry.html'
            path.write_text('<div id="crBody">12.34</div><pre id="probe"></pre><script>'+script+'</script>')
            p=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking',
                '--user-data-dir='+str(Path(folder)/'profile'),'--virtual-time-budget=1000','--dump-dom',path.as_uri()],capture_output=True,text=True,timeout=30)
        found=re.search(r'<pre id="probe">(.*?)</pre>',p.stdout,re.S)
        self.assertIsNotNone(found,p.stderr[-200:]);self.assertEqual('PASS',found.group(1))

    def test_real_dom_heading_has_no_duplicate_numbers(self):
        chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        source=(ROOT/'确认需求解析结果.html').read_text()
        script='const sections=new Map(),$=id=>document.getElementById(id),shownStep=1;const escapeHtml=s=>String(s);'
        script+="const FORMS=['basic','dest','products','techparams','payment','logistics'].map(id=>({section_id:'s1_'+id,step:1}));"
        if 'function syncSectionNumbers(' in source:script+=function(source,'syncSectionNumbers')
        script+="const currentIndustry=()=> 'packaging';"+function(source,'statusChipHtml')+function(source,'sectionShell')
        script+=r'''
        try{
          const sec=sectionShell({section_id:'s1_products',step:1,title:'③ 产品信息列表'},'');
          if(!sec.el.querySelector('.num'))throw Error('用户要求保留蓝色数字块');
          if(sec.title!=='产品信息列表')throw Error('标题仍有旧圈号');
          // 模拟先填付款/物流，再填产品与技术参数，不能沿模型回包顺序标5/6。
          for(const id of ['payment','logistics','techparams','basic','dest'])
            sectionShell({section_id:'s1_'+id,step:1,title:'① '+id},'');
          const order=()=>[...document.querySelectorAll('#resultsContent > .collapse-block')].map(e=>{
            const item=[...sections.entries()].find(([id,s])=>s.el===e);
            return [item[0],e.querySelector('.num')?.textContent];
          });
          const expected=FORMS.map((f,i)=>[f.section_id,String(i+1)]);
          if(JSON.stringify(order())!==JSON.stringify(expected))throw Error('顺序或编号不符合模板 '+JSON.stringify(order()));
          sectionShell({section_id:'s1_products',step:6,title:'③ 产品信息列表'},'');
          if(sec.step!==1||sec.el.querySelector('.num').textContent!=='3')throw Error('历史回看/错误step破坏产品编号');
          sectionShell({section_id:'s3_markup',step:3,title:'定价规则'},'');
          if(sections.get('s3_markup').el.querySelector('.num').textContent!=='1')throw Error('编号没有按步骤重置');
          sectionShell({section_id:'s1_techparams',step:1,title:'④ 产品技术参数'},'');
          if(sections.get('s1_techparams').el.querySelector('.num').textContent!=='4')throw Error('重复渲染改变编号');
          document.getElementById('probe').textContent='PASS';
        }catch(e){document.getElementById('probe').textContent=e.stack}
        '''
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'heading.html';path.write_text('<div id="resultsContent"></div><pre id="probe"></pre><script>'+script+'</script>')
            p=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking',
                '--user-data-dir='+str(Path(folder)/'profile'),'--virtual-time-budget=1000','--dump-dom',path.as_uri()],capture_output=True,text=True,timeout=30)
        found=re.search(r'<pre id="probe">(.*?)</pre>',p.stdout,re.S)
        self.assertIsNotNone(found,p.stderr[-200:]);self.assertEqual('PASS',found.group(1))
