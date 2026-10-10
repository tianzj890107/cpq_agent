from pathlib import Path
import unittest
import subprocess

ROOT = Path(__file__).resolve().parents[1]


class BatchExecutionTest(unittest.TestCase):
    def test_real_batch_runs_post_poll_and_skips_valid_result(self):
        source = (ROOT / 'tech_app/frontend/app.js').read_text()
        start = source.index('async function runPackagingBusinessProcessBatch(')
        body = source[start:source.index('/* ---------------- 2.1 3D', start)]
        script = '''
const assert=require('assert'); let currentProject='p', allPartsProcessBusy=true;
const emitted=[], posts=[]; let polls=0;
const window={dispatchEvent:e=>emitted.push(e.detail)};
class CustomEvent {constructor(name,init){this.detail=init.detail}}
const API='',sleep=async()=>{},forwardTaskDetail=()=>{},refreshBoardActionState=()=>{};
class FormData {}
const fetch=async(url,options)=>{
 let data;
 if(options?.method==='POST'){posts.push(url);data={task_id:'real-task'};}
 else if(url.includes('/tasks/')){polls++;data={status:'succeeded',progress_log:['取到企业工艺'],result:{plan:{steps:[{name:'模切'}]}}};}
 else data=url.includes('/B/process') ? {plan:{steps:[{name:'开料'}]}} : {plan:{steps:[{name:'旧路线'}]},business_stale:true};
 return {ok:true,json:async()=>data};
};
''' + body + '''
(async()=>{
 await runPackagingBusinessProcessBatch('p',[{code:'A'},{code:'B'}],[]);
 assert.equal(posts.length,1);assert(posts[0].includes('/packaging-business-parts/A/process'));
 assert.equal(polls,1);assert(emitted[0].prompt);assert.equal(emitted[0].status,'queued');
 assert.equal(emitted.at(-1).status,'succeeded');assert.equal(allPartsProcessBusy,false);
})().catch(error=>{console.error(error);process.exit(1)});
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
    def test_batch_submits_business_tasks_not_only_opens_panels(self):
        source = (ROOT / 'tech_app/frontend/app.js').read_text()
        start = source.index('function startAllPackagingPartProcesses(')
        body = source[start:source.index('/* ---------------- 2.1 3D', start)]
        self.assertIn('runPackagingBusinessProcessBatch', body)
        self.assertNotIn('Promise.resolve(packagingBusinessPartProcessByAuthority', body)
        self.assertIn('async function runPackagingBusinessProcessBatch(', source)

    def test_single_part_generation_supplies_user_prompt_before_submit(self):
        source = (ROOT / 'tech_app/frontend/inline-analysis.js').read_text()
        body = source[source.index('async function generate(state)'):source.index('async function poll(state')]
        self.assertIn('prompt:', body)
        self.assertIn('agent:task-progress', body)
        self.assertLess(body.index('agent:task-progress'), body.index('const submitted'))
