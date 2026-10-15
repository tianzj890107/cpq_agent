"""线上验收回归：长版本、乱序终态及嵌入成本会话。无线上连接。"""
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / 'tech_app/frontend'


def function(source, name):
    start = source.index('function ' + name + '(')
    # 这些函数的结束括号独占一行；避免正则误读模板字符串内的大括号。
    line_start = source.rfind('\n', 0, start) + 1
    indent = re.match(r' *', source[line_start:start]).group()
    end = source.index('\n'+indent+'}', start) + len(indent) + 2
    return source[start:end]


def node(script):
    p = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise AssertionError(p.stderr)


class HandoffChatRepair(unittest.TestCase):
    def test_new_and_existing_schema_support_full_versions(self):
        import cpq_wf
        ddl = cpq_wf._ddl_pg('isolated_test')
        create = next(s for s in ddl if 'CREATE TABLE IF NOT EXISTS isolated_test.cpq_wf_handoff' in s)
        self.assertRegex(create, r'source_result_version\s+text')
        self.assertTrue(any(re.search(r'ALTER TABLE isolated_test.cpq_wf_handoff\s+ALTER COLUMN source_result_version TYPE text', s) for s in ddl))

    def test_long_version_is_preserved_and_retry_reuses_handoff(self):
        from tests.fixtures import wf_handoff_harness as H
        version = 'pkgcost-v2:1000:47.352645:' + 'a' * 64 + ':' + 'b' * 64
        with H.Workbench() as wb:
            first = wb.handoff(result_version=version)
            again = wb.handoff(result_version=version)
            self.assertEqual(first['handoff_id'], again['handoff_id'])
            self.assertEqual(version, wb.handoffs()[0]['source_result_version'])
            changed = wb.handoff(result_version=version[:-1]+'c')
            self.assertNotEqual(first['handoff_id'], changed['handoff_id'])

    def test_persisted_task_retains_original_sequence_and_timestamp(self):
        from unittest.mock import patch
        from tech_app.backend.storage import store
        class Backend:
            data = {}
            def get_doc(self, pid, kind): return self.data.get((pid, kind))
            def put_doc(self, pid, kind, value): self.data[pid, kind] = value
        with patch.object(store, '_meta', return_value=Backend()):
            first=store.append_session_event('p', {'kind':'task','ts':'2026-10-15T01:00:01',
                'task':{'id':'a','status':'succeeded'},'key':'task:a'})
            store.append_session_event('p', {'kind':'user','text':'下一次','ts':'2026-10-15T01:00:02'})
            late=store.append_session_event('p', {'kind':'task','ts':'2026-10-15T01:00:03',
                'task':{'id':'a','status':'running'},'key':'task:a'})
            self.assertEqual((first['seq'],first['ts'],'succeeded'),(late['seq'],late['ts'],late['task']['status']))

    def test_shell_event_writes_keep_user_before_task_even_with_slow_response(self):
        source=(FRONT/'agent-chat.js').read_text()
        node('let sessionWriteQueue=Promise.resolve();'+function(source,'persistSessionEvent')+r'''
        let projectId='p',replayingHistory=false;
        const boardStage=()=> 'cost',api=()=>'/p/event',authHeaders=()=>({});
        const stored=[];const fetch=async(url,o)=>{
          const x=JSON.parse(o.body);
          if(x.kind==='user')await new Promise(r=>setTimeout(r,20));
          stored.push(x.kind);return {ok:true,json:async()=>({})};
        };
        (async()=>{
          persistSessionEvent({kind:'user'});persistSessionEvent({kind:'task'});
          await sessionWriteQueue;
          if(stored.join(',')!=='user,task')throw Error('用户消息落在卡片后面');
        })().catch(e=>{console.error(e);process.exitCode=1});
        ''')

    def test_all_failed_process_batch_is_failed(self):
        source=(FRONT/'app.js').read_text()
        node(r'''
        let currentProject='p',allPartsProcessBusy=true;
        const API='',events=[];
        const window={dispatchEvent:()=>{},PACKAGING_PROCESS_BATCH_CONCURRENCY:2};
        const CustomEvent=function(n,o){};
        function forwardTaskDetail(d){events.push(structuredClone(d))}
        function refreshBoardActionState(){}
        const sleep=async()=>{};
        let fetch=async(url,opts)=>({ok:!opts.method,status:500,
          json:async()=>opts.method?{detail:'实际失败'}:{plan:{steps:[]}}});
        '''+'async '+function(source,'runPackagingBusinessProcessBatch')+r'''
        (async()=>{
          await runPackagingBusinessProcessBatch('p',[{code:'a'},{code:'b'}],[]);
          if(events.at(-1).status!=='failed')throw Error('全部失败仍报完成');
          if(allPartsProcessBusy)throw Error('任务未释放busy');
          fetch=async(url,opts)=>({ok:true,json:async()=>url.includes('/tasks/')
            ?{status:'partial',result:{plan:{steps:[{name:'已有部分工序'}]}}}
            :opts.method?{task_id:'t'}:{plan:{steps:[]}}});
          await runPackagingBusinessProcessBatch('p',[{code:'a'},{code:'b'}],[]);
          if(events.at(-1).status!=='partial')throw Error('有部分工序被错误报为全失败');
        })().catch(e=>{console.error(e);process.exitCode=1});
        ''')

    def test_backend_late_running_does_not_undo_terminal(self):
        from tech_app.backend.storage import store
        for status in ('succeeded', 'partial', 'failed', 'blocked', 'interrupted'):
            merged = store._merge_task_entry({'status': status, 'error': '原始原因', 'steps': ['完成']},
                                              {'status': 'running', 'steps': ['迟到进度']})
            self.assertEqual(status, merged['status'])
            self.assertEqual(['完成', '迟到进度'], merged['steps'])

    def test_frontend_terminal_and_first_position_survive_late_events(self):
        node((FRONT/'tech-session-timeline.js').read_text() + r'''
        const t=TechSessionTimeline;
        for(const status of ['succeeded','partial','failed','blocked','interrupted']){
          const rows=t.merge({events:[
            {kind:'user',ts:'2026-10-15T01:00:00',seq:1,text:'开始'},
            {kind:'task',key:'task:a',ts:'2026-10-15T01:00:01',seq:2,task:{id:'a',status,steps:['完成']}},
            {kind:'user',ts:'2026-10-15T01:00:02',seq:3,text:'下一次'},
            {kind:'task',key:'task:a',ts:'2026-10-15T01:00:03',task:{id:'a',status:'running',steps:['迟到']}}
          ]});
          if(rows[1].task?.status!==status)throw Error('状态倒退/位置漂移 '+status);
          if(rows[1].task.steps.length!==2)throw Error('丢失进度');
        }
        ''')

    def test_partial_forwarding_does_not_report_success(self):
        source=(FRONT/'app.js').read_text()
        node('const seen=[];const window={TechBoardRuntime:{publish:(e,a,d)=>seen.push(e)}};'
             'function persistDrawingTask(){};'+function(source,'forwardTaskDetail')+r'''
        forwardTaskDetail({status:'partial'});
        if(seen[0]!=='task-partial')throw Error('部分失败被上报成成功');
        ''')

    def test_embedded_cost_bubble_and_failure_are_forwarded(self):
        chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists(): self.skipTest('Chrome unavailable')
        source=(FRONT/'cost-review.js').read_text()
        names=['crAppend','crSay','crUserSay','crPersistNote','crCard','crPublishTask','crRememberSettle']
        # 使用真实 DOM 和真实成本卡实现；桥只收集消息，不运行外部服务。
        script=r'''
        const sent=[],saved=[];
        const windowRuntime={publish:(name,action,payload)=>sent.push({name,action,payload:structuredClone(payload)})};
        window.TechBoardRuntime=windowRuntime;
        const $cr=id=>document.getElementById(id);
        const esc=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
        let costTurn=null,crReplaying=false,crPid='test',crLastSettle=null;
        const api=(url,options)=>{saved.push(options);return Promise.reject(new Error('回传失败：版本字段超长'))};
        let crData={},crBusy=false,crTaskId='t';
        const crRender=()=>{},crStatus=()=>{},crToast=()=>{},crShowFailure=()=>{},crUrl=s=>s;
        '''
        # 后续新增的桥状态与纯辅助函数也加载，避免测试另写一份实现。
        begin=source.find('let crBridgeTurn')
        if begin>=0: script+=source[begin:source.index('function crAppend',begin)]
        script+='\n'.join(function(source,n) for n in names)
        script+='\nasync '+function(source,'crRunOp')
        script+='\nasync '+function(source,'crConfirmCost')
        script+=r'''
        (async()=>{try{
          crUserSay('回传销售经理继续报价。');
          const c=crCard('回传销售经理继续报价');c.log(['核对完整版本']);
          c.done(false,'回传失败：版本字段超长');
          const p=sent.map(x=>x.payload);
          if(!p.some(x=>x.prompt==='回传销售经理继续报价。'))throw Error('用户消息未到父壳');
          if(!p.some(x=>x.status==='failed'&&String(x.error).includes('版本字段超长')))throw Error('错误未到父壳');
          if(new Set(p.map(x=>x.taskId)).size!==1)throw Error('一轮生成多张卡');
          const previousId=p[0].taskId;
          sent.length=0;
          const result=await crRunOp('send-to-quote');
          if(result!==false||crBusy)throw Error('真实回传失败没有释放busy');
          if(!sent.some(x=>x.name==='task-failed'&&x.payload.error.includes('版本字段超长')))throw Error('按钮失败被隐藏');
          if(sent[0].payload.taskId===previousId)throw Error('重试复用了旧任务ID');
          if(new Set(sent.map(x=>x.payload.taskId)).size!==1)throw Error('按钮重复建卡');
          sent.length=0;
          await crConfirmCost();
          if(!sent.some(x=>x.payload.prompt==='确认当前成本。'&&x.payload.status==='failed'))throw Error('确认成本未同步失败与用户气泡');
          window.TechBoardRuntime={publish:()=>{throw Error('桥暂时断开')}};
          await crRunOp('send-to-quote');
          if(crBusy)throw Error('桥断开阻止业务收尾');
          window.TechBoardRuntime=null;
          const local=crUserSay('独立页仍显示');
          if(local?.textContent!=='独立页仍显示')throw Error('独立页用户气泡丢失');
          const standalone=crCard('独立成本');standalone.log(['原有明细']);standalone.done(true);
          document.getElementById('probe').textContent='PASS';
        }catch(e){document.getElementById('probe').textContent=e.stack}})();
        '''
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'probe.html'
            # 真 iframe，使 parent !== window；模拟实际工作台隐藏子线程。
            child='<div id="crThread"><div id="crTinner"></div></div><pre id="probe"></pre><script>'+script+'</script>'
            path.write_text('<iframe srcdoc="'+child.replace('&','&amp;').replace('"','&quot;')+'"></iframe>')
            # dump-dom 不含 iframe DOM：让 child 把结果复制到父壳可见输出。
            child=child.replace("document.getElementById('probe').textContent", "parent.document.getElementById('probe').textContent")
            path.write_text('<pre id="probe"></pre><iframe srcdoc="'+child.replace('&','&amp;').replace('"','&quot;')+'"></iframe>')
            result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking',
                '--user-data-dir='+str(Path(folder)/'profile'),'--virtual-time-budget=1000','--dump-dom',path.as_uri()],capture_output=True,text=True,timeout=30)
        found=re.search(r'<pre id="probe">(.*?)</pre>',result.stdout,re.S)
        self.assertIsNotNone(found,result.stderr[-300:])
        self.assertEqual('PASS',found.group(1))
