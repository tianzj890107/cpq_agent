import pathlib
import subprocess
import unittest
from unittest.mock import patch, Mock
from tech_app.backend.services import project_access

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProgressiveTests(unittest.TestCase):
    def test_api_paged_and_legacy_contracts_are_separate(self):
        from tech_app.backend import main
        with patch.object(project_access,'visible_projects_page',return_value={'projects':[],'total':0}) as paged,patch.object(project_access,'visible_projects',return_value=[]) as legacy:
            self.assertEqual(main.projects(user={'username':'PE1'},page=1)['total'],0)
            legacy.assert_not_called()
            paged.assert_called_once()
            self.assertEqual(main.projects(user={'username':'PE1'}),[])
            legacy.assert_called_once()

    def test_api_invalid_page_is_400(self):
        from tech_app.backend import main
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as error:
            main.projects(user={'username':'PE1'},page=0)
        self.assertEqual(error.exception.status_code,400)

    def test_real_page_loader_ignores_late_previous_page(self):
        script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');const src=fs.readFileSync('报价首页.html','utf8');
const code=src.slice(src.indexOf('function techProjectPageKey()'),src.indexOf('function renderTechProjectPage()'));
const pending=[],renders=[];const ctx={JSON,Map,encodeURIComponent,PAGE:1,PAGE_SIZE:6,TECH_INCLUDE_ARCHIVED:false,
currentMode:'tech',searchInput:{value:''},TECH_PAGE_CACHE:new Map(),TECH_PAGE_REQUESTS:new Map(),wfUser:()=>({username:'PE1',role:'engineer'}),
currentTab:()=> '我的项目',waitForHomeAuth:async()=>{},techRowOf:r=>r,homeBoundedRequest:fn=>fn(),
window:{cpqAuth:{api:url=>new Promise(resolve=>pending.push({url,resolve}))}},renderTechProjectPage:()=>renders.push(ctx.PAGE)};
vm.createContext(ctx);vm.runInContext(code,ctx);
(async()=>{const first=ctx.loadTechProjectPage();await new Promise(setImmediate);ctx.PAGE=2;const second=ctx.loadTechProjectPage();await new Promise(setImmediate);
assert(pending[0].url.includes('page=1'));assert(pending[1].url.includes('page=2'));assert(pending[1].url.includes('page_size=6'));
pending[1].resolve({projects:[{project_id:'second'}],total:12,pages:2});await second;
pending[0].resolve({projects:[{project_id:'first'}],total:12,pages:2});await first;
assert.deepStrictEqual(renders,[2]);assert.strictEqual(ctx.TECH_PAGE_REQUESTS.size,0);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        run=subprocess.run(['node','-e',script],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_lightweight_store_does_not_read_ir(self):
        backend=Mock()
        backend.list_metas.return_value=[{'project_id':'p','owner':'PE1'}]
        backend.get_user.return_value={'display_name':'工程师'}
        with patch.object(project_access.store,'_meta',return_value=backend):
            rows=project_access.store.list_projects(lightweight=True)
        backend.get_doc.assert_not_called()
        self.assertEqual(rows[0]['owner_display_name'],'工程师')

    def test_acl_and_archive_filter_precede_pagination(self):
        rows=[{'project_id':'blocked','updated_at':'9'},{'project_id':'archived','updated_at':'8','deleted_at':'yes'},{'project_id':'allowed','project_name':'酒盒','updated_at':'7'}]
        with patch.object(project_access.store,'list_projects',return_value=rows),patch.object(project_access,'can_read',side_effect=lambda user,meta:meta['project_id']!='blocked'),patch.object(project_access,'mine_sources',return_value=['owner']),patch.object(project_access.home_card,'build_card') as card:
            result=project_access.visible_projects_page({'username':'PE1'},page=1,page_size=1)
            self.assertEqual(result['total'],1)
            self.assertEqual(result['projects'][0]['project_id'],'allowed')
            card.assert_not_called()
            empty=project_access.visible_projects_page({'username':'PE1'},page=2,page_size=1)
            self.assertEqual(empty['projects'],[])
            search=project_access.visible_projects_page({'username':'PE1'},query='无匹配')
            self.assertEqual(search['total'],0)

    def test_invalid_page_rejected(self):
        for args in ({'page':0},{'page_size':101},{'scope':'invalid'}):
            with self.assertRaises(ValueError):project_access.visible_projects_page({},**args)

    def test_page_only_hydrates_current_visible_slice(self):
        self.assertTrue(hasattr(project_access,'visible_projects_page'))
        rows=[{'project_id':str(i),'owner':'PE1','updated_at':f'2026-10-{i+1:02d}'} for i in range(20)]
        with patch.object(project_access.store,'list_projects',return_value=rows), patch.object(project_access,'can_read',return_value=True), patch.object(project_access,'mine_sources',return_value=['owner']), patch.object(project_access,'visible_projects',side_effect=lambda user,scope,include_archived=False,_metas=None: _metas) as hydrate:
            result=project_access.visible_projects_page({'username':'PE1'},'mine',page=2,page_size=6)
        self.assertEqual(result['total'],20)
        self.assertEqual(len(result['projects']),6)
        hydrate.assert_not_called()
        self.assertEqual(result['projects'][0]['project_id'],'13')

    def test_entry_does_not_wait_for_details(self):
        src=(ROOT/'报价首页.html').read_text()
        body=src.split('async function openTechProject(projectId)',1)[1].split('// 领取任务',1)[0]
        self.assertNotIn('await Promise.all',body)
        self.assertTrue('loadTechProjectPage' in src)
        self.assertTrue('page_size=' in src)

    def test_workbench_results_render_without_supplemental_reads(self):
        src=(ROOT/'tech_app/frontend/app.js').read_text()
        body=src.split('async function openProject(pid)',1)[1].split('function avgConfidence',1)[0]
        self.assertNotIn('await loadDrawingFlowPanel()',body)
        self.assertNotIn('await refreshPackagingParts()',body)
        ai=(ROOT/'tech_app/frontend/assembly-integration.js').read_text().split('async function aiStart()',1)[1]
        self.assertNotIn("Promise.all([api(aiUrl('')), aiLoadParts()])",ai)
