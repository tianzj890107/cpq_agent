"""一键生成工艺推荐的并发与缓存（Spec `packaging-process-batch-concurrency-and-cache.md`）。

A 组（前端并发/轮询）用 node 真跑新接线；C/D 组真跑后端纯函数与真实 HTTP 读取（urlopen 打桩），
不是 grep。B 组是语义不变护栏（已有有效工艺仍跳过、终态仍 partial）。
"""
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / 'tech_app' / 'frontend' / 'app.js'

BATCH_START = 'async function runPackagingBusinessProcessBatch('
BATCH_END = '/* ---------------- 2.1 3D'


def batch_body():
    source = APP_JS.read_text()
    start = source.index(BATCH_START)
    return source[start:source.index(BATCH_END, start)]


class FrontendConcurrencyTest(unittest.TestCase):
    def test_concurrency_override_of_one_is_serial(self):
        script = '''
const assert=require('assert'); let currentProject='p', allPartsProcessBusy=true;
const emitted=[]; const window={dispatchEvent:e=>emitted.push(e.detail)}; window.PACKAGING_PROCESS_BATCH_CONCURRENCY=1;
class CustomEvent {constructor(name,init){this.detail=init.detail}}
const API='', sleep=async()=>{}, forwardTaskDetail=()=>{}, refreshBoardActionState=()=>{};
class FormData {}
let inFlight=0, maxInFlight=0, posts=0;
const fetch=async(url,options)=>{
  inFlight++; if(inFlight>maxInFlight) maxInFlight=inFlight;
  await new Promise(r=>setTimeout(r,5));
  let data;
  if(options&&options.method==='POST'){posts++;data={task_id:'t'};}
  else if(String(url).includes('/tasks/')){data={status:'succeeded',progress_log:[],result:{plan:{steps:[{name:'模切'}]}}};}
  else {data={plan:{steps:[]}};}
  inFlight--;
  return {ok:true,json:async()=>data};
};
''' + batch_body() + '''
(async()=>{
 const ready=[1,2,3,4].map(i=>({code:'P'+i}));
 await runPackagingBusinessProcessBatch('p',ready,[]);
 assert.equal(posts,4);
 assert.equal(maxInFlight,1,'覆盖为 1 时严格串行，实测峰值并发 '+maxInFlight);
})().catch(error=>{console.error(error);process.exit(1)});
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)

    def test_batch_submits_with_bounded_concurrency(self):
        script = '''
const assert=require('assert'); let currentProject='p', allPartsProcessBusy=true;
const emitted=[]; const window={dispatchEvent:e=>emitted.push(e.detail)};
class CustomEvent {constructor(name,init){this.detail=init.detail}}
const API='', sleep=async()=>{}, forwardTaskDetail=()=>{}, refreshBoardActionState=()=>{};
class FormData {}
let inFlight=0, maxInFlight=0, posts=0;
const fetch=async(url,options)=>{
  inFlight++; if(inFlight>maxInFlight) maxInFlight=inFlight;
  await new Promise(r=>setTimeout(r,5));
  let data;
  if(options&&options.method==='POST'){posts++;data={task_id:'t'};}
  else if(String(url).includes('/tasks/')){data={status:'succeeded',progress_log:[],result:{plan:{steps:[{name:'模切'}]}}};}
  else {data={plan:{steps:[]}};}
  inFlight--;
  return {ok:true,json:async()=>data};
};
''' + batch_body() + '''
(async()=>{
 const ready=[1,2,3,4,5,6].map(i=>({code:'P'+i}));
 await runPackagingBusinessProcessBatch('p',ready,[]);
 assert.equal(posts,6,'每一件都要真提交');
 assert.ok(maxInFlight>=2,'必须并发提交，实测峰值并发 '+maxInFlight);
 assert.ok(maxInFlight<=3,'并发要有界（默认 3），实测峰值并发 '+maxInFlight);
 assert.equal(allPartsProcessBusy,false);
 assert.equal(emitted.at(-1).status,'succeeded');
})().catch(error=>{console.error(error);process.exit(1)});
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)

    def test_polling_backs_off_from_sixty_percent(self):
        script = '''
const assert=require('assert'); let currentProject='p', allPartsProcessBusy=true;
const emitted=[]; const window={dispatchEvent:e=>emitted.push(e.detail)};
class CustomEvent {constructor(name,init){this.detail=init.detail}}
const API='', forwardTaskDetail=()=>{}, refreshBoardActionState=()=>{};
class FormData {}
const sleeps=[]; const sleep=async(ms)=>{sleeps.push(ms);};
let polls=0;
const fetch=async(url,options)=>{
 let data;
 if(options&&options.method==='POST'){data={task_id:'t'};}
 else if(String(url).includes('/tasks/')){polls++;data=polls<2?{status:'running',progress_log:[]}:{status:'succeeded',progress_log:[],result:{plan:{steps:[{name:'模切'}]}}};}
 else {data={plan:{steps:[]}};}
 return {ok:true,json:async()=>data};
};
''' + batch_body() + '''
(async()=>{
 await runPackagingBusinessProcessBatch('p',[{code:'A'}],[]);
 assert.ok(sleeps.length>=2,'至少两次轮询间隔');
 assert.equal(sleeps[0],600,'首次轮询间隔 600ms，实测 '+sleeps[0]);
 assert.ok(sleeps[1]>sleeps[0],'间隔要退避，实测 '+JSON.stringify(sleeps));
 assert.ok(sleeps.every(v=>v<=1200),'退避封顶 1200ms，实测 '+JSON.stringify(sleeps));
})().catch(error=>{console.error(error);process.exit(1)});
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)

    def test_existing_valid_process_is_still_skipped_and_terminal_partial(self):
        script = '''
const assert=require('assert'); let currentProject='p', allPartsProcessBusy=true;
const emitted=[]; const window={dispatchEvent:e=>emitted.push(e.detail)};
class CustomEvent {constructor(name,init){this.detail=init.detail}}
const API='', sleep=async()=>{}, forwardTaskDetail=()=>{}, refreshBoardActionState=()=>{};
class FormData {}
let posts=0, polls=0;
const fetch=async(url,options)=>{
 let data;
 if(options&&options.method==='POST'){posts++;data={task_id:'t'};}
 else if(String(url).includes('/tasks/')){polls++;data={status:'succeeded',progress_log:[],result:{plan:{steps:[{name:'模切'}]}}};}
 else if(String(url).includes('/A/process')){data={plan:{steps:[{name:'开料'}]}};}
 else {data={plan:{steps:[]}};}
 return {ok:true,json:async()=>data};
};
''' + batch_body() + '''
(async()=>{
 await runPackagingBusinessProcessBatch('p',[{code:'A'},{code:'B'}],[{part_code:'Z',reason:'缺尺寸'}]);
 assert.equal(posts,1,'A 已有有效工艺必须跳过');
 assert.equal(polls,1);
 assert.equal(emitted.at(-1).status,'partial','有跳过项终态仍是 partial');
 assert.equal(allPartsProcessBusy,false);
})().catch(error=>{console.error(error);process.exit(1)});
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)


class SectionParallelTest(unittest.TestCase):
    def _inputs(self, count):
        return {'part_code': 'P', 'name': 'N', 'sections': [
            {'row': {'business_part_code': 'P::s%d' % i}, 'section_id': 's%d' % i,
             'name': '部分%d' % (i + 1), 'quantity': 1, 'entity_ids': ['e%d' % i],
             'grounding': 'g'} for i in range(count)]}

    def test_sections_run_in_parallel_and_keep_serial_order(self):
        from tech_app.backend.services import packaging_sections as sections
        from tech_app.backend.services import packaging_parts, process, da_process_routing

        lock = threading.Lock()
        live = {'now': 0, 'max': 0}

        def fake_recommend(part, **kwargs):
            with lock:
                live['now'] += 1
                live['max'] = max(live['max'], live['now'])
            time.sleep(0.05)
            with lock:
                live['now'] -= 1
            return ({'part_id': 'p', 'steps': [{'step_no': 10, 'name': '开料', 'depends_on': []}],
                     'open_questions': []}, {'summary': {'reused': 0, 'missing': 1}})

        with patch.object(packaging_parts, 'business_as_ir_part', return_value=object()), \
             patch.object(da_process_routing, 'for_row', return_value={'status': 'unbound'}), \
             patch.object(process, 'compute', return_value={'warnings': []}):
            merged, coverage, results = sections.recommend_process(
                self._inputs(4), fake_recommend)
        self.assertGreaterEqual(live['max'], 2, '各组成必须并行，实测峰值并发 %s' % live['max'])
        self.assertLessEqual(live['max'], 4)
        self.assertEqual([s['section_name'] for s in results],
                         ['部分1', '部分2', '部分3', '部分4'], '结果必须按输入组成顺序重排')
        self.assertEqual([s['name'] for s in merged['steps']],
                         ['部分1 · 开料', '部分2 · 开料', '部分3 · 开料', '部分4 · 开料'])


class DaRoutingCacheTest(unittest.TestCase):
    def _response(self):
        response = MagicMock()
        response.read.return_value = b'{"ok": true, "status": "matched", "routes": []}'
        return response

    def test_repeated_bridge_read_hits_cache(self):
        from tech_app.backend.services import da_process_routing as routing
        response = self._response()
        with patch.object(routing.cpq_kb_client, '_base', return_value='http://x'), \
             patch.object(routing.cpq_kb_client, '_internal_token', return_value='t'), \
             patch.object(routing.urllib.request, 'urlopen') as op, \
             patch.object(routing, 'CACHE_TTL_SECONDS', 300):
            op.return_value.__enter__.return_value = response
            routing.clear_cache()
            with routing.cache_scope():
                routing._fetch('C', 'N')
                routing._fetch('C', 'N')
            self.assertEqual(op.call_count, 1, '同一入参第二次必须命中缓存')

    def test_bridge_read_not_memoized_outside_scope(self):
        from tech_app.backend.services import da_process_routing as routing
        response = self._response()
        with patch.object(routing.cpq_kb_client, '_base', return_value='http://x'), \
             patch.object(routing.cpq_kb_client, '_internal_token', return_value='t'), \
             patch.object(routing.urllib.request, 'urlopen') as op, \
             patch.object(routing, 'CACHE_TTL_SECONDS', 300):
            op.return_value.__enter__.return_value = response
            routing.clear_cache()
            routing._fetch('C', 'N')
            routing._fetch('C', 'N')
            self.assertEqual(op.call_count, 2, '作用域外不许记忆；直接调用仍各自读桥')

    def test_cache_ttl_zero_disables_hits(self):
        from tech_app.backend.services import da_process_routing as routing
        response = self._response()
        with patch.object(routing.cpq_kb_client, '_base', return_value='http://x'), \
             patch.object(routing.cpq_kb_client, '_internal_token', return_value='t'), \
             patch.object(routing.urllib.request, 'urlopen') as op, \
             patch.object(routing, 'CACHE_TTL_SECONDS', 0):
            op.return_value.__enter__.return_value = response
            routing.clear_cache()
            with routing.cache_scope():
                routing._fetch('C', 'N')
                routing._fetch('C', 'N')
            self.assertEqual(op.call_count, 2, 'TTL=0 时不使用缓存')

    def test_candidates_are_cached(self):
        from tech_app.backend.services import da_process_routing as routing
        response = MagicMock()
        response.read.return_value = b'{"ok": true, "status": "ok", "candidates": []}'
        with patch.object(routing.cpq_kb_client, '_base', return_value='http://x'), \
             patch.object(routing.cpq_kb_client, '_internal_token', return_value='t'), \
             patch.object(routing.urllib.request, 'urlopen') as op, \
             patch.object(routing, 'CACHE_TTL_SECONDS', 300):
            op.return_value.__enter__.return_value = response
            routing.clear_cache()
            with routing.cache_scope():
                routing._fetch_candidates()
                routing._fetch_candidates()
            self.assertEqual(op.call_count, 1)


if __name__ == '__main__':
    unittest.main()
