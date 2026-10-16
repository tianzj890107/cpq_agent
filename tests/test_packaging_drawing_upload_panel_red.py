import pathlib
import subprocess
import tempfile
import json
import re
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]


class UploadPanel(unittest.TestCase):
    def test_browser_empty_upload_drag_drop_and_compact_replace(self):
        chrome=pathlib.Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        js=(ROOT/'tech_app/frontend/drawing-upload.js').read_text()
        css='\n'.join((ROOT/'tech_app/frontend'/name).read_text() for name in ('inline-analysis.css','drawing-upload.css'))
        script=r'''
let saves=0,requests=0;
const root=document.getElementById('upload');
const options={projectId:'P',getProjectId:()=> 'P',filename:'',request:async()=>{requests++;return {ok:true,json:async()=>({})};},onSaved:()=>saves++};
DrawingUploadPanel.update(root,options);
const emptyVisible=!root.hidden&&root.getBoundingClientRect().height>100;
const event=new Event('drop',{cancelable:true});Object.defineProperty(event,'dataTransfer',{value:{files:[new File(['cad'],'新图.dwg')]}});
root.dispatchEvent(event);
setTimeout(()=>{
 const uploadWorked=saves===1&&requests===1&&root.querySelector('[role=status]').textContent.includes('已保存');
 DrawingUploadPanel.update(root,{...options,filename:'已有图.dwg'});
 const button=root.querySelector('button');const compact=button.textContent==='更换图纸'&&button.getBoundingClientRect().width<200&&root.getBoundingClientRect().height<120;
 const noOverflow=document.documentElement.scrollWidth<=innerWidth;
 document.getElementById('probe').textContent=JSON.stringify({emptyVisible,uploadWorked,compact,noOverflow});
},60);
'''
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'upload.html'
            path.write_text('<!doctype html><meta charset="utf-8"><style>'+css+'</style><section id="upload" class="drawing-upload-panel" hidden></section><pre hidden id="probe"></pre><script>'+js+script+'</script>')
            result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking',
                '--user-data-dir='+str(pathlib.Path(directory)/'profile'),'--window-size=640,900',
                '--virtual-time-budget=1000','--dump-dom',path.as_uri()],capture_output=True,text=True,timeout=30)
            self.assertEqual(0,result.returncode,result.stderr[-500:])
            match=re.search(r'<pre hidden="" id="probe">(.*?)</pre>',result.stdout,re.S)
            self.assertIsNotNone(match,result.stdout[-500:])
            metrics=json.loads(match[1]);self.assertTrue(all(metrics.values()),metrics)

    def test_upload_panel_is_in_main_workspace_not_hidden_drawer(self):
        text=(ROOT/'tech_app/frontend/index.html').read_text()
        self.assertIn('id="drawingUploadPanel"',text)
        self.assertLess(text.index('id="drawingUploadPanel"'),text.index('id="ocDrawer"'))
        self.assertIn('drawing-upload.js',text)

    def test_upload_controller_saves_current_project_and_handles_race_and_failure(self):
        path=ROOT/'tech_app/frontend/drawing-upload.js'
        self.assertTrue(path.exists(),'缺少主工作区上传控件')
        script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const window={};class Data{append(k,v){this[k]=v;}}
vm.runInNewContext(fs.readFileSync(PATH,'utf8'),{window,FormData:Data,console,Promise});
let pid='P1',calls=[],saved=[],events=[],deferred,fail=false;
const controller=window.DrawingUploadPanel.createUploader({projectId:'P1',getProjectId:()=>pid,
request:async(url,options)=>{calls.push({url,options});if(fail)throw Error('offline');return await new Promise(r=>deferred=r);},
onSaved:p=>saved.push(p),onProgress:e=>events.push(e),hasSource:false});
(async()=>{
 const file={name:'盒.dwg',size:100};const task=controller.upload(file);
 await controller.upload(file);assert.equal(calls.length,1,'不得重复上传');
 assert.equal(calls[0].url,'/api/projects/P1/source');assert.equal(calls[0].options.body.file,file);
 pid='P2';deferred({ok:true,json:async()=>({})});await task;assert.deepEqual(saved,[],'迟到响应不得跳回旧项目');
 pid='P1';fail=true;await controller.upload(file);assert.equal(events.at(-1).status,'failed');
 fail=false;const retry=controller.upload(file);deferred({ok:true,json:async()=>({})});await retry;
 assert.deepEqual(saved,['P1']);assert.equal(events.at(-1).status,'succeeded');
 assert(events[0].prompt.includes('上传'));assert(calls.every(c=>c.url.endsWith('/P1/source')));
 const before=calls.length;await controller.upload({name:'bad.exe',size:10});assert.equal(calls.length,before);
 await controller.upload({name:'big.dwg',size:17*1024*1024});assert.equal(calls.length,before);
 assert(!calls.some(c=>c.url==='/api/projects'));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace('PATH',repr(str(path)))
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=15)
        self.assertEqual(0,result.returncode,result.stderr)
