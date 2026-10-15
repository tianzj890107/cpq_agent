import importlib
import pathlib
import unittest
import base64
import sqlite3
import tempfile
import asyncio
import io
import subprocess
from unittest.mock import patch
from tests.test_packaging_cost_engine_red import CostCase
from tech_app.backend.storage import store

ROOT=pathlib.Path(__file__).resolve().parents[1]

class TechContinuity(CostCase):
    def test_quote_task_without_drawing_starts_confirmation_and_reopens_same_project(self):
        service=importlib.import_module('tech_app.backend.services.quote_task_intake')
        task={'task_id':'123','task_kind':'tech_new_product','session_id':'QUOTE1','title':'酒盒',
              'customer':'客户甲','payload':{'industry':'packaging','business_case_id':'bc_123',
              'requirement_text':'酒盒 1000只','requirement':{'inner_length':220,'quote_quantity':1000}}}
        first=service.accept_task(task,self.actor(),attachments=[])
        pid=first['project_id'];doc=store.load_requirement(pid)
        self.assertEqual('pending_confirmation',doc['status'])
        self.assertEqual('requirement-confirm',first['page'])
        self.assertEqual(220,doc['data']['inner_length'])
        self.assertIsNone(store.source_path(pid))
        again=service.accept_task(task,self.actor(),attachments=[])
        self.assertEqual(pid,again['project_id'])
        self.assertEqual('QUOTE1',doc['data']['source_session_id'])

    def test_original_drawing_bytes_arrive_without_reparsing(self):
        service=importlib.import_module('tech_app.backend.services.quote_task_intake')
        task={'task_id':'124','task_kind':'tech_new_product','session_id':'Q2','payload':{'industry':'packaging','requirement_text':'礼盒'}}
        result=service.accept_task(task,self.actor(),attachments=[{'name':'酒盒.dwg','data':b'CAD-original'}])
        self.assertEqual(b'CAD-original',store.source_path(result['project_id']).read_bytes())

    def test_reopen_preserves_approved_edited_requirements(self):
        from tech_app.backend.services import quote_task_intake
        task={'task_id':'126','task_kind':'tech_new_product','session_id':'Q2','payload':{'industry':'packaging'}}
        result=quote_task_intake.accept_task(task,self.actor(),attachments=[])
        pid=result['project_id'];doc=store.load_requirement(pid)
        doc['status']='approved';doc['data']['inner_length']=999;store.save_requirement(pid,doc)
        again=quote_task_intake.accept_task(task,self.actor(),attachments=[])
        self.assertEqual(pid,again['project_id']);self.assertEqual('drawing',again['page'])
        self.assertEqual(doc,store.load_requirement(pid))

    def test_legacy_task_link_is_reused_not_duplicated(self):
        from tech_app.backend.services import quote_task_intake
        pid=store.create_requirement_project(owner='PE1')
        store.save_business_case(pid,{'source_task_id':'127','source_session_id':'Qold','entry_origin':'quote'})
        original={'project_id':pid,'requirement_no':'Rold','status':'approved','data':{'source_task_id':'127','source_session_id':'Qold'}}
        store.save_requirement(pid,original)
        task={'task_id':'127','task_kind':'tech_new_product','session_id':'Qold','payload':{'industry':'packaging'}}
        result=quote_task_intake.accept_task(task,self.actor(),attachments=[])
        self.assertEqual(pid,result['project_id'])
        self.assertEqual(original,store.load_requirement(pid))

    def test_first_drawing_keeps_approval_and_replacement_remains_guarded(self):
        from tech_app.backend import main
        from starlette.datastructures import UploadFile
        pid=store.create_requirement_project(owner='PE1')
        meta=store.load_meta(pid);meta['quote_task_id']='125';store._meta().put_meta(pid,meta)
        original={'project_id':pid,'requirement_no':'R1','status':'approved','data':{'industry':'packaging'}}
        store.save_requirement(pid,original)
        upload=UploadFile(filename='酒盒.dwg',file=io.BytesIO(b'cad-original'))
        asyncio.run(main.replace_project_source(pid,upload,{'username':'PE1','role':'engineer'}))
        self.assertEqual(original,store.load_requirement(pid))
        self.assertEqual(b'cad-original',store.source_path(pid).read_bytes())
        with self.assertRaises(main.HTTPException) as raised:
            main._check_workflow_input_change(pid,{'username':'PE1','role':'engineer'})
        self.assertEqual(403,raised.exception.status_code)

    def test_quote_requirements_do_not_demand_drawing_before_stage_two(self):
        from tech_app.backend.services import requirement_service
        from tech_app.backend.models.workflow import RequirementDoc
        pid=store.create_requirement_project(owner='PE1')
        meta=store.load_meta(pid);meta['quote_task_id']='125';store._meta().put_meta(pid,meta)
        result=requirement_service.requirement_precheck(pid,RequirementDoc(project_id=pid,requirement_no='',data={'industry':'packaging'}))
        self.assertFalse(any('source' in row.get('missing',[]) for row in result['items']))

    def test_stage_two_upload_preserves_project_instead_of_creating_another(self):
        js=(ROOT/'tech_app/frontend/app.js').read_text()
        upload=js.split('$("btnUpload").onclick = async () => {',1)[1].split('currentProject = res.project_id;',1)[0]
        self.assertTrue('/source' in upload,'2.1上传必须补入当前项目而不是新建另一张卡片')

class QuoteFiles(unittest.TestCase):
    def setUp(self):
        import cpq_quote_files
        self.files=cpq_quote_files
        self.tmp=tempfile.TemporaryDirectory();self.db=pathlib.Path(self.tmp.name)/'files.db'
        self.user={'user_id':1,'role_code':'sales_manager'}
        self.patches=[patch('cpq_auth._connect',side_effect=lambda:sqlite3.connect(self.db,isolation_level=None)),
          patch('cpq_auth._exec',side_effect=lambda conn,sql,args=():conn.execute(sql.replace('%s','?'),args)),
          patch('cpq_wf.get_card',return_value={'creator_user_id':'1','current_owner':'1'})]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_save_bind_reopen_and_task_bytes_are_identical(self):
        raw=b'original\x00DWG';ref=self.files.save('酒盒.dwg',base64.b64encode(raw),self.user)
        self.files.bind('QUOTE1',[ref],self.user)
        self.assertEqual([ref],self.files.list_session('QUOTE1',self.user))
        content=self.files.task_files({'session_id':'QUOTE1','payload':{'attachment_refs':[ref]}},self.user)
        self.assertEqual(raw,base64.b64decode(content[0]['data']))
        self.assertEqual(ref,self.files.save('酒盒.dwg',base64.b64encode(raw),self.user,'QUOTE1'))
    def test_other_owner_cannot_bind_or_read(self):
        ref=self.files.save('图纸.dwg',base64.b64encode(b'cad'),self.user)
        with self.assertRaises(Exception):self.files.bind('QUOTE1',[ref],{'user_id':2,'role_code':'sales_manager'})
        with self.assertRaises(Exception):self.files.list_session('QUOTE1',{'user_id':2,'role_code':'sales_manager'})
    def test_invalid_path_and_corrupt_base64_rejected(self):
        for name,data in [('../图纸.dwg','Y2Fk'),('图纸.dwg','???')]:
            with self.assertRaises(Exception):self.files.save(name,data,self.user)
    def test_task_cannot_fetch_file_from_another_session(self):
        ref=self.files.save('图纸.dwg','Y2Fk',self.user,'QUOTE1')
        with self.assertRaises(Exception):self.files.task_files({'session_id':'QUOTE2','payload':{'attachment_refs':[ref]}},self.user)

    def test_integrity_corruption_is_not_silently_forwarded(self):
        ref=self.files.save('图纸.dwg','Y2Fk',self.user,'QUOTE1')
        with sqlite3.connect(self.db) as conn:conn.execute('UPDATE cpq_quote_attachment SET content=?',(b'bad',))
        with self.assertRaises(Exception):self.files.read_file('QUOTE1',ref['file_id'],self.user)

    def test_existing_unlisted_format_remains_readable_but_new_upload_denied(self):
        import hashlib
        raw=b'existing-original-presentation'
        conn=sqlite3.connect(self.db,isolation_level=None)
        self.files.ensure_table(conn)
        conn.execute('INSERT INTO cpq_quote_attachment VALUES (?,?,?,?,?,?,?)',
            ('legacy',1,'QUOTE1','历史.pptx',hashlib.sha256(raw).hexdigest(),len(raw),raw))
        conn.close()
        result=self.files.read_file('QUOTE1','legacy',self.user)
        self.assertEqual(raw,base64.b64decode(result['data']))
        with self.assertRaises(Exception):self.files.save('新资料.pptx',base64.b64encode(raw),self.user,'QUOTE1')

class FrontendContinuity(unittest.TestCase):
    def test_attachment_denial_does_not_break_existing_quote_restore(self):
        source=(ROOT/'确认需求解析结果.html').read_text()
        function=source.split('async function restoreQuoteFiles(bindPending)',1)[1].split('const TEXT_EXT',1)[0]
        function='async function restoreQuoteFiles(bindPending)'+function
        script="let chatFiles=[];let reported=false;const currentSessionId='Q1';const wfApi=async()=>{throw Error('附件无权读取')};const renderAttachChips=()=>{};const addErrorBubble=()=>{reported=true};"+function+"\nrestoreQuoteFiles(false).then(()=>{if(!reported)throw Error('读取失败必须披露')}).catch(error=>{console.error(error);process.exitCode=1});"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_task_entry_accepts_existing_quote_and_goes_to_confirmation(self):
        js=(ROOT/'tech_app/frontend/tech-task.js').read_text()
        self.assertIn('/api/quote-tasks/',js)
        self.assertIn('/accept',js)
        self.assertNotIn("if (!file) { toast('请先上传至少一份模型图纸",js)

    def test_quote_home_can_upload_dwg_and_persist_refs(self):
        source=(ROOT/'报价首页.html').read_text()
        self.assertIn('/wf/quote-files',source)
        self.assertIn('cpq:quoteFileRefs',source)
        a=source.index('<input id="fileInput"');b=source.index('>',a)
        self.assertIn('.dwg',source[a:b])

    def test_quick_quote_and_precise_keep_original_files(self):
        source=(ROOT/'tech_app/frontend/quick-quote-panel.js').read_text()
        quote=(ROOT/'确认需求解析结果.html').read_text()
        self.assertIn('/wf/quote-files',source)
        self.assertIn('/wf/quote-files',quote)
        self.assertIn('attachment_refs',quote)
