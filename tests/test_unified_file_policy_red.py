import importlib
import json
import pathlib
import subprocess
import unittest
import tempfile
import re
from unittest.mock import patch
from tests.test_packaging_cost_engine_red import CostCase

ROOT=pathlib.Path(__file__).resolve().parents[1]

class FilePolicy(unittest.TestCase):
    def policy(self):return importlib.import_module('cpq_file_policy')

    def test_one_registry_and_purpose_specific_capabilities(self):
        p=self.policy()
        self.assertEqual(p.registry(),json.loads((ROOT/'tech_app/frontend/file-policy.json').read_text()))
        for ext in ('.dwg','.dxf','.doc','.xls','.step','.sldprt','.webp','.yaml'):
            self.assertTrue(p.allowed('asset',ext))
        self.assertFalse(p.allowed('model_import','.dwg'))
        self.assertTrue(p.allowed('model_import','.step'))
        self.assertEqual('save_only',p.capability('quote_text','a.png')['mode'])
        self.assertEqual('save_only',p.capability('quick','a.doc')['mode'])
        self.assertEqual('converted',p.capability('drawing','a.dwg')['mode'])
        self.assertEqual('direct',p.capability('drawing','a.dxf')['mode'])

    def test_quote_and_tech_limit_and_server_whitelist(self):
        p=self.policy()
        import cpq_quote_files
        from tech_app.backend import config
        self.assertEqual(p.max_bytes(),cpq_quote_files.MAX_FILE_BYTES)
        self.assertLessEqual(config.MAX_UPLOAD_BYTES,p.max_bytes())
        with self.assertRaises(Exception):cpq_quote_files.validate_file('bad.exe',b'MZ')

    def test_primary_drawing_not_first_document(self):
        p=self.policy()
        self.assertEqual('酒盒.dwg',p.primary_name(['需求.docx','示意.png','酒盒.dwg']))
        self.assertIsNone(p.primary_name(['需求.docx','价格.xlsx']))

    def test_extract_checks_decoded_bytes_not_only_base64_length(self):
        import base64
        from types import SimpleNamespace
        from cpq_agent_server import Handler
        output=[]
        request=SimpleNamespace(path='/api/extract',_quick_quote_write=lambda path:False,_read_body=lambda:{'name':'需求.txt','data':base64.b64encode(b'abcd').decode()},_send_json=lambda data:output.append(data))
        with patch('cpq_file_policy.max_bytes',return_value=3):Handler.do_POST(request)
        self.assertFalse(output[-1]['ok'],'base64余量不能扩大文件大小限制')

    def test_parse_document_sets_share_registry(self):
        p=self.policy()
        import cpq_quick_quote_file
        from tech_app.backend.services import unified_parse
        self.assertEqual(set(p.extensions('quick_parse')),set(cpq_quick_quote_file.DWG_EXTS+cpq_quick_quote_file.DOC_EXTS))
        self.assertEqual(cpq_quick_quote_file.DOC_EXTS,unified_parse.DOC_EXTS)
        self.assertNotIn('.xls',cpq_quick_quote_file.DOC_EXTS)
        self.assertNotIn('.png',cpq_quick_quote_file.DOC_EXTS)

    def test_frontend_shared_policy_and_real_node_filtering(self):
        js=ROOT/'tech_app/frontend/file-policy.js'
        self.assertTrue(js.exists(),'缺统一前端文件策略')
        script="const fs=require('fs');global.window=global;global.document=undefined;global.fetch=async()=>({ok:true,json:async()=>JSON.parse(fs.readFileSync('tech_app/frontend/file-policy.json','utf8'))});"+js.read_text()+"\nCPQFilePolicy.ready.then(()=>{if(CPQFilePolicy.accept('model_import')!=='.step,.stp')throw Error('模型专用入口错误');if(CPQFilePolicy.capability('quick','a.png').mode!=='save_only')throw Error('冒充图片OCR');if(!CPQFilePolicy.validate({name:'bad.exe',size:10},'asset'))throw Error('后缀未验证');});"
        result=subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_frontend_entry_points_use_policy(self):
        for name in ('报价首页.html','确认需求解析结果.html','tech_app/frontend/quick-quote-panel.js','tech_app/frontend/workflow.js','tech_app/frontend/tech-workbench.html','tech_app/frontend/index.html'):
            self.assertTrue('file-policy' in (ROOT/name).read_text() or 'CPQFilePolicy' in (ROOT/name).read_text(),name)

    def test_quick_saved_only_keeps_bytes_without_parser_or_field_reset(self):
        script="const fs=require('fs');global.window=global;global.document=undefined;let saved=0,parsed=0;global.fetch=async(url)=>{if(url==='/file-policy.json')return {ok:true,json:async()=>JSON.parse(fs.readFileSync('tech_app/frontend/file-policy.json','utf8'))};if(url==='/wf/quote-files'){saved++;return {ok:true,json:async()=>({file:{file_id:'F1',name:'图.png'}})}};parsed++;throw Error('不应调用解析器');};global.sessionStorage={getItem:()=>null,setItem:()=>{}};global.FileReader=class{readAsDataURL(){this.result='data:;base64,YQ==';this.onload();}};"
        script+=(ROOT/'tech_app/frontend/file-policy.js').read_text()+'\n'+(ROOT/'tech_app/frontend/quick-quote-panel.js').read_text()
        script+="\n(async()=>{await CPQFilePolicy.ready;QuickQuotePanel.workspaceState.inputs={quote_quantity:1000};const r=await QuickQuotePanel.parseFile({name:'图.png',size:1});if(!r.attachment_only||saved!==1||parsed!==0)throw Error('仅保存路径错误');if(QuickQuotePanel.workspaceState.inputs.quote_quantity!==1000)throw Error('清空了已有字段');})().catch(e=>{console.error(e);process.exitCode=1});"
        result=subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_real_chrome_static_and_dynamic_input_purposes(self):
        chrome=pathlib.Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        script='globalThis.fetch=async()=>({ok:true,json:async()=>('+json.dumps(self.policy().registry())+')});'
        script+=(ROOT/'tech_app/frontend/file-policy.js').read_text()
        script+="\nCPQFilePolicy.ready.then(()=>setTimeout(()=>{const i=document.createElement('input');i.type='file';i.id='file_drawing_2d';document.body.append(i);setTimeout(()=>{document.getElementById('probe').textContent=JSON.stringify({same:document.getElementById('fileInput').accept===document.getElementById('ocChatFileInput').accept,step:document.getElementById('file3d').accept,docx:i.accept.includes('.docx'),dwg:i.accept.includes('.dwg'),workbook:document.getElementById('workbookImport').accept,summary:!!document.getElementById('filePolicySummary')});},80)},80));"
        with tempfile.TemporaryDirectory() as directory:
            page=pathlib.Path(directory)/'policy.html'
            page.write_text('<!doctype html><meta charset="utf-8"><div class="unified-input-card"></div><input id="fileInput" type="file"><input id="ocChatFileInput" type="file"><input id="file3d" type="file"><input id="workbookImport" type="file" accept=".xlsx"><pre id="probe"></pre><script>'+script+'</script>')
            r=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking','--user-data-dir='+str(pathlib.Path(directory)/'profile'),'--virtual-time-budget=1500','--dump-dom',page.as_uri()],capture_output=True,text=True,timeout=30)
            found=re.search(r'<pre id="probe">([^<]+)</pre>',r.stdout)
            self.assertIsNotNone(found,r.stderr[-500:]);data=json.loads(found.group(1))
            self.assertTrue(data['same']);self.assertEqual('.step,.stp',data['step'])
            self.assertFalse(data['docx']);self.assertTrue(data['dwg']);self.assertFalse(data['summary'],'Agent输入区不应展示常驻文件能力说明');self.assertEqual('.xlsx',data['workbook'])

class DocumentOnlyProject(CostCase):
    def setUp(self):
        super().setUp()
        from tech_app.backend.storage.blob_backend import LocalBlobBackend
        self.blob_patch=patch('tech_app.backend.storage.store._blob',return_value=LocalBlobBackend(self.data_dir))
        self.blob_patch.start()
    def tearDown(self):
        self.blob_patch.stop();super().tearDown()
    def test_documents_are_saved_as_attachments_not_fake_source(self):
        import asyncio,io
        from starlette.datastructures import UploadFile
        from tech_app.backend import main
        from tech_app.backend.storage import store
        file=UploadFile(filename='需求.docx',file=io.BytesIO(b'original-docx'))
        result=asyncio.run(main.upload_project(file=file,files=[],attachments=[],note='测试',business_case_id='',source_task_id='',source_session_id='',user=self.actor()))
        self.assertIsNone(store.source_path(result['project_id']))
        self.assertIn('需求.docx',store.load_meta(result['project_id'])['attachments'])

    def test_cad_in_supplemental_files_still_becomes_primary(self):
        import asyncio,io
        from starlette.datastructures import UploadFile
        from tech_app.backend import main
        from tech_app.backend.storage import store
        document=UploadFile(filename='需求.docx',file=io.BytesIO(b'original-docx'))
        drawing=UploadFile(filename='酒盒.dwg',file=io.BytesIO(b'original-dwg'))
        result=asyncio.run(main.upload_project(file=document,files=[],attachments=[drawing],note='测试',business_case_id='',source_task_id='',source_session_id='',user=self.actor()))
        self.assertEqual('酒盒.dwg',result['source_filename'])
        self.assertEqual(b'original-dwg',store.source_path(result['project_id']).read_bytes())
        self.assertIn('需求.docx',store.load_meta(result['project_id'])['attachments'])
