import copy
import importlib
import json
import subprocess
import unittest
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class SavedFirst(unittest.TestCase):
    def service(self):
        return importlib.import_module('tech_app.backend.services.cost_review_loading')

    def test_saved_skips_live_dependencies_and_is_not_authority(self):
        svc = self.service()
        from tech_app.backend.services import packaging_cost, packaging_parts, cost_review
        from tech_app.backend.storage import da_repo, store
        row = {'project_id':'p','estimate_id':'e','requirement_no':'r','total_cost':2,
               'material_total':2,'quote_quantity':1000,'source_versions_json':'{}'}
        items = [{'part_code':'a','part_name':'面纸','amount':2,'cost_category':'material','inputs_json':'{}'}]
        doc = {'business_parts':[{'business_part_code':'a','name':'面纸','reference':{'quantity':1}}]}
        with patch.object(store,'load_meta',return_value={'project_id':'p'}), \
             patch.object(store,'load_requirement',return_value={'requirement_no':'r','data':{'industry':'packaging','quote_quantity':1000}}), \
             patch.object(packaging_parts,'load_business_parts',return_value=doc), \
             patch.object(da_repo,'load_packaging_cost',return_value=row), \
             patch.object(da_repo,'load_packaging_cost_items',return_value=items), \
             patch.object(cost_review,'load_review') as review, \
             patch.object(packaging_cost,'load_cost',side_effect=AssertionError('不得读取现行路线/BOM/规则')), \
             patch.object(packaging_cost,'compute_project',side_effect=AssertionError('不得重算')):
            review.return_value.model_dump.return_value={'confirmed':True}
            result=svc.saved('p')
        self.assertTrue(result['verification_pending'])
        self.assertFalse(result['ready'])
        self.assertTrue(result['review']['confirmed'])
        self.assertEqual(2,result['final']['total'])
        self.assertNotIn('calculation_details',result['parts'][0])
        self.assertNotIn('items',result['packaging_cost'])

    def test_compact_projection_same_money_without_detail_generation(self):
        from tech_app.backend.services import packaging_cost_ledger as ledger
        doc={'business_parts':[{'business_part_code':'a','reference':{'quantity':2}}]}
        cost={'built':True,'estimate_id':'e','material_total':4,'total_cost':4,
              'items':[{'part_code':'a','cost_category':'material','amount':4,'inputs_json':'{}'}]}
        before=copy.deepcopy(cost)
        full=ledger.project(doc,cost)
        with patch.object(ledger,'_calculation_evidence',side_effect=AssertionError('详情不得提前生成')):
            compact=ledger.project(doc,cost,include_details=False)
        for key in ('final','parts_total','counts','reconciled','ready'):
            self.assertEqual(full[key],compact[key])
        self.assertEqual(full['parts'][0]['unit_cost'],compact['parts'][0]['unit_cost'])
        self.assertNotIn('items',compact['parts'][0])
        self.assertNotIn('calculation_details',compact['parts'][0])
        self.assertNotIn('items',compact['packaging_cost'])
        self.assertEqual(before,cost)

    def test_loader_saved_is_visible_while_verification_waits_and_late_response_ignored(self):
        path=ROOT/'tech_app/frontend/cost-review-loader.js'
        self.assertTrue(path.exists(),'缺少先展示后核验的加载协调器')
        script=path.read_text()+r'''
        (async()=>{
          const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
          const first=deferred(),second=deferred();let n=0;const shown=[],errors=[];
          const loader=CPQCostReviewLoader.create({readSaved:async()=>({id:++n}),
            readVerified:()=>n===1?first.promise:second.promise,
            render:d=>shown.push(d),failed:e=>errors.push(e.message)});
          await loader.load();
          if(shown.length!==1||shown[0].id!==1)throw Error('saved被核验阻塞');
          await loader.load();second.resolve({id:20});await new Promise(r=>setTimeout(r,0));
          first.resolve({id:10});await new Promise(r=>setTimeout(r,0));
          if(shown.at(-1).id!==20)throw Error('旧核验倒灌');
          const failure=CPQCostReviewLoader.create({readSaved:async()=>({id:30}),
            readVerified:async()=>{throw Error('核验超时')},render:d=>shown.push(d),failed:e=>errors.push(e.message)});
          await failure.load();await new Promise(r=>setTimeout(r,0));
          if(shown.at(-1).id!==30||!errors.includes('核验超时'))throw Error('核验失败丢失旧表格');
        })().catch(e=>{console.error(e);process.exitCode=1});
        '''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_detail_version_mismatch_is_rejected(self):
        svc=self.service()
        from tech_app.backend.services import packaging_cost_ledger
        from fastapi import HTTPException
        with patch.object(packaging_cost_ledger,'load',return_value={'estimate_id':'new','parts':[]}):
            with self.assertRaises(HTTPException) as caught:svc.detail('p','a','old')
        self.assertEqual(409,caught.exception.status_code)

    def test_same_estimate_id_changed_rows_also_conflict(self):
        svc=self.service()
        from tech_app.backend.services import packaging_cost_ledger
        with patch.object(packaging_cost_ledger,'load',return_value={'estimate_id':'same','display_version':'new','parts':[]}):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:svc.detail('p','a','same','old')
        self.assertEqual(409,caught.exception.status_code)

    def test_visibility_must_not_run_full_cost_validation_before_fast_view(self):
        from tech_app.backend.services import packaging_cost, project_access
        from tech_app.backend.storage import da_repo, store
        with patch.object(store,'load_requirement',return_value={'requirement_no':'r'}), \
             patch.object(da_repo,'load_packaging_cost',return_value={'estimate_id':'saved'}), \
             patch.object(packaging_cost,'load_cost',side_effect=AssertionError('ACL不得完整核验成本')):
            self.assertTrue(project_access._packaging_cost_visible('p'))

    def test_real_browser_saved_table_and_buttons_before_verification_and_lazy_details(self):
        chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        from tech_app.backend.services import packaging_cost_ledger
        full=packaging_cost_ledger.project({'business_parts':[{'business_part_code':'a','name':'面纸','reference':{'quantity':1}}]},
            {'built':True,'estimate_id':'e','material_total':2,'total_cost':2,'quote_quantity':1000,
             'items':[{'part_code':'a','cost_category':'material','amount':2,'inputs_json':'{}'}]})
        full.update(review={'confirmed':True},verification_pending=False,quote_quantity=1000)
        saved=copy.deepcopy(full);saved.update(verification_pending=True,ready=False)
        saved['parts'][0].pop('items');saved['parts'][0].pop('calculation_details');saved['packaging_cost'].pop('items')
        script='const saved='+json.dumps(saved)+';const verified='+json.dumps(full)+';'
        script+=r'''
        const calls=[];let finishVerify,finishDetail;
        window.TechProjectContext={bind:()=>({project:'p'})};
        window.esc=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
        window.cpqAuth={user:()=>({role:'finance_mgr'})};
        window.actions={};window.TechBoardRuntime={registerActions:a=>Object.assign(actions,a),registerViews:()=>{},refreshState:()=>{},publishStatus:()=>{}};
        window.api=async url=>{calls.push(url);if(url.includes('?view=saved'))return structuredClone(saved);
          if(url.includes('?view=summary'))return new Promise(r=>finishVerify=r);
          if(url.includes('/details/'))return new Promise(r=>finishDetail=r);
          if(url.includes('/agent/events'))return {events:[]};throw Error('unexpected request '+url)};
        '''
        script+=(ROOT/'tech_app/frontend/cost-review-loader.js').read_text()+'\n'+(ROOT/'tech_app/frontend/cost-review.js').read_text()
        script+=r'''
        const delay=()=>new Promise(r=>setTimeout(r,80));
        (async()=>{await delay();
          if(!document.getElementById('crBody').textContent.includes('面纸') && !document.getElementById('crBody').textContent.includes('2'))throw Error('已存成本没有上屏');
          if(!document.getElementById('crConfirm').disabled || !actions.confirmCostReview.getState().busy)throw Error('核验前允许确认');
          if(calls.some(x=>x.includes('/details/')))throw Error('未点击先读取详情');
          crTab='parts';crRender();void crOpenCostDetail('a');await delay();
          if(calls.filter(x=>x.includes('/details/')).length!==1)throw Error('详情未按需加载');
          document.querySelector('.cr-cost-details').close();finishDetail(verified.parts[0]);await delay();
          if(document.querySelector('.cr-cost-details')?.open)throw Error('关闭后迟到响应重开弹窗');
          finishVerify(verified);await delay();
          if(document.getElementById('crConfirm').disabled || actions.confirmCostReview.getState().busy)throw Error('核验后没有解锁');
          if(crTab!=='parts')throw Error('后台响应重置页签');
          if(calls.filter(x=>x.includes('?view=summary')).length!==1)throw Error('重复核验请求');
          document.getElementById('probe').textContent='PASS';
        })().catch(e=>document.getElementById('probe').textContent=e.stack);
        '''
        html=(ROOT/'tech_app/frontend/cost-review.html').read_text()
        html=re.sub(r'<script\b[^>]*>.*?</script>','',html,flags=re.S)
        html=re.sub(r'<link\b[^>]*>','',html)
        html=html.replace('</body>','<pre id="probe"></pre><script>'+script+'</script></body>')
        with tempfile.TemporaryDirectory() as folder:
            page=Path(folder)/'cost.html';page.write_text(html)
            result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking',
                '--user-data-dir='+str(Path(folder)/'profile'),'--virtual-time-budget=2500','--dump-dom',page.as_uri()],capture_output=True,text=True,timeout=30)
        found=re.search(r'<pre id="probe">(.*?)</pre>',result.stdout,re.S)
        self.assertIsNotNone(found,result.stderr[-500:])
        self.assertEqual('PASS',found.group(1))

    def test_frontend_uses_split_views_and_no_full_cost_history_restore(self):
        js=(ROOT/'tech_app/frontend/cost-review.js').read_text()
        self.assertIn('CPQCostReviewLoader.create',js)
        self.assertIn("'?view=saved'",js)
        self.assertIn("'?view=summary'",js)
        self.assertIn('/details/',js)
        self.assertIn('verification_pending',js)
        workbench=(ROOT/'tech_app/frontend/tech-workbench.js').read_text()
        self.assertIn('/cost-review?view=saved',workbench)


class SavedStorage(unittest.TestCase):
    def test_real_saved_ledger_read_only_and_no_current_dependencies(self):
        from tests.test_packaging_cost_engine_red import CostCase
        from tech_app.backend.storage import da_repo, meta_backend
        from tech_app.backend.services import cost_review_loading, packaging_cost, packaging_parts
        fixture=CostCase();fixture.setUp()
        try:
            backend=meta_backend.get_backend()
            backend.put_meta('saved-p',{'project_id':'saved-p'})
            backend.put_doc('saved-p','requirement',{'requirement_no':'saved-r','data':{'industry':'packaging','quote_quantity':1000}})
            backend.put_doc('saved-p',packaging_parts.BUSINESS_DOC_KEY,{'items':[{'business_parts':[{'business_part_code':'a','name':'面纸','reference':{'quantity':1}}]}]})
            cost={'estimate_id':'same','total_cost':2,'material_total':2,'labor_total':0,'process_total':0,
                  'tooling_total':0,'packaging_total':0,'freight_total':0,'other_total':0,'quote_quantity':1000}
            items=[{'part_code':'a','part_name':'面纸','cost_category':'material','amount':2,'loss_rate':0,'inputs_json':'{}'}]
            da_repo.save_packaging_cost('saved-p','saved-r','default',cost,items)
            before={str(p):p.read_bytes() for p in fixture.data_dir.rglob('*.json')}
            with patch.object(packaging_cost,'load_cost',side_effect=AssertionError('不应核验上游')):
                result=cost_review_loading.saved('saved-p')
            self.assertEqual(2,result['parts'][0]['unit_cost'])
            self.assertEqual(2,result['final']['total'])
            self.assertTrue(result['verification_pending'])
            self.assertEqual(before,{str(p):p.read_bytes() for p in fixture.data_dir.rglob('*.json')})
            first_version=result['display_version']
            items[0]['amount']=3;cost.update(total_cost=3,material_total=3)
            da_repo.save_packaging_cost('saved-p','saved-r','default',cost,items)
            changed=cost_review_loading.saved('saved-p')
            self.assertNotEqual(first_version,changed['display_version'])
            self.assertEqual('same',changed['estimate_id'])
            self.assertEqual(3,changed['final']['total'])
        finally:fixture.tearDown()

    def test_storage_errors_are_not_empty_costs(self):
        from tech_app.backend.services import cost_review_loading, packaging_parts
        from tech_app.backend.storage import store
        with patch.object(store,'load_meta',return_value={'project_id':'p'}), \
             patch.object(store,'load_requirement',return_value={'data':{'industry':'packaging'}}), \
             patch.object(packaging_parts,'load_business_parts',side_effect=RuntimeError('目录读取失败')):
            with self.assertRaisesRegex(RuntimeError,'目录读取失败'):cost_review_loading.saved('p')
