import copy
import importlib
import json
import io
import zipfile
import unittest
from unittest.mock import Mock, patch

import cpq_packaging_quote as quote
from cpq_quote_gate import quote_step_completion_gate
from cpq_quote_workspace import cost_fingerprint
from tests.test_packaging_quote_close_loop_red import package_fixture


def demo_package():
    pkg = package_fixture()
    pkg['requirement'].update(poc_demo=True, poc_auto_no_manual=True)
    pkg['cost'].update(material_total=10, built=True, stale=False,
        items=[{'part_code':'P','cost_category':'material','amount':10}], has_gaps=True)
    pkg['gaps'] = [{'code':'loss_rate_missing','severity':'advisory'}]
    return pkg


class DemoCompletion(unittest.TestCase):
    def test_browser_export_validation_distinguishes_demo_download_and_formal_import(self):
        import pathlib, subprocess
        from tests.test_packaging_whole_part_process_unit_red import js_function
        path=pathlib.Path(__file__).resolve().parents[1]/'确认需求解析结果.html'
        script="const LAST_PACKAGING_PACKAGE={industry:'packaging'};const LAST_PACKAGING_QUOTE={demo_only:true,draft:true};const quoteDetailRows=()=>[];const quoteCompletionGate=()=>({ok:true,demo_only:true});"
        script+=js_function(path,'assertQuoteExportable')
        script+="if(!assertQuoteExportable('document').ok)throw Error('演示下载被前端拦住');if(assertQuoteExportable().ok)throw Error('正式导入被错误放行');"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_export_uses_saved_quote_and_formal_import_stays_blocked(self):
        import cpq_agent_server as server
        pkg=demo_package();priced=quote.price(pkg,gross_margin_rate=.25)
        priced['source_cost_fingerprint']=cost_fingerprint(pkg)
        def snapshot(sid, step):
            self.assertEqual('original-session',sid)
            return {'packaging_package':pkg} if step==2 else {'packaging_quote':priced}
        payload={'session_id':'original-session','industry':'packaging','steps':[
            {'step':6,'sections':[{'title':'CLIENT_FAKE_PRICE','kind':'document','markdown':'CLIENT_FAKE_PRICE'}]}]}
        with patch('cpq_wf.step_snapshot',side_effect=snapshot):
            with self.assertRaises(ValueError):server._assert_formal_packaging_quote(payload)
            raw=server._build_quote_docx(payload)
        if raw is None:self.skipTest('python-docx unavailable')
        with zipfile.ZipFile(io.BytesIO(raw)) as doc:
            text=doc.read('word/document.xml').decode()
        self.assertIn('演示报价单',text)
        self.assertIn('不得用于正式交易',text)
        self.assertNotIn('CLIENT_FAKE_PRICE',text)

    def test_clean_demo_still_is_not_a_formal_quote(self):
        pkg=demo_package();pkg['gaps']=[];pkg['cost']['has_gaps']=False
        priced=quote.price(pkg,gross_margin_rate=.25)
        self.assertTrue(priced['demo_only'])
        self.assertTrue(priced['publish_blocked'])
        with self.assertRaises(quote.PricingError):quote.document(priced,publish=True)
        pkg['cost']['items']=[]
        priced=quote.price(pkg,gross_margin_rate=.25)
        self.assertTrue(priced['demo_only'])
        self.assertTrue(priced['publish_blocked'])
        self.assertFalse(quote.demo_completion_eligible(pkg))
    def test_demo_can_complete_with_disclosed_estimates_but_not_publish(self):
        pkg = demo_package()
        priced = quote.price(pkg, gross_margin_rate=.25)
        priced['source_cost_fingerprint'] = cost_fingerprint(pkg)
        result = quote_step_completion_gate(6, {'packaging_package':pkg, 'packaging_quote':priced})
        self.assertTrue(result['ok'], result)
        self.assertTrue(priced['publish_blocked'])
        self.assertIn('演示报价单', quote.document(priced)['markdown'])
        with self.assertRaises(quote.PricingError):quote.document(priced, publish=True)
        with self.assertRaises(quote.PricingError):quote.price(pkg, gross_margin_rate=.25, publish=True)

    def test_client_only_demo_flag_or_unpriced_rows_cannot_bypass(self):
        for change in ('not_demo','stale','uncomputed','zero_material','nan'):
            pkg = demo_package()
            if change=='not_demo':pkg['requirement'].clear()
            if change=='stale':pkg['cost']['stale']=True
            if change=='uncomputed':pkg['cost']['items'][0]['amount']=None
            if change=='zero_material':pkg['cost']['material_total']=0
            if change=='nan':pkg['cost']['total_cost']=float('nan')
            priced={'industry':'packaging','net_unit_price':20,'quote_quantity':1000,
                    'draft':True,'gap_count':1,'demo_only':True}
            self.assertFalse(quote_step_completion_gate(6,{'packaging_package':pkg,'packaging_quote':priced})['ok'],change)

    def test_changed_cost_still_blocks_demo_completion(self):
        pkg=demo_package();priced=quote.price(pkg,gross_margin_rate=.25)
        priced['source_cost_fingerprint']='old-cost'
        result=quote_step_completion_gate(6,{'packaging_package':pkg,'packaging_quote':priced})
        self.assertFalse(result['ok'])
        self.assertIn('packaging_quote_stale',[g['code'] for g in result['blocking']])


class SemanticCandidates(unittest.TestCase):
    def test_existing_next_button_waits_for_background_association_then_changes_tab(self):
        import pathlib, subprocess
        from tests.test_packaging_whole_part_process_unit_red import js_function
        path=pathlib.Path(__file__).resolve().parents[1]/'tech_app/frontend/assembly-integration.js'
        script="""
let aiBusy=false,aiDeferredBusy=false,aiData={},actions=[],tab='params';
const aiPlan=()=>({quantity:1000}),aiUrl=x=>'/integration'+x;
const aiPost=async(path,options)=>{actions.push(path);if(!options.keepTab)throw Error('不应自动跳页');return {ok:true};};
const api=async(path,options)=>{if(path!=='/integration')throw Error(path);actions.push('save');return {};};
const aiRefreshPackagingWorkspace=async()=>({status:{'3.1':{completed:true}}});
const aiSetTab=value=>tab=value;const aiSay=()=>{};
"""+js_function(path,'aiPackagingNext')+"""
(async()=>{const r=await aiPackagingNext('params');if(!r.ok||tab!=='process'||actions.join(',')!=='name-associations,save')throw Error('原按钮链路未接异步语义任务');})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_background_prepare_persists_material_suggestion_into_real_bom_and_cost(self):
        from tests.test_packaging_demo_flow_end_to_end_red import DemoFlow, PID, REQ_NO
        from tech_app.backend.services import packaging_parts, packaging_demo_flow, packaging_bom, packaging_cost, llm_client
        fixture=DemoFlow('test_a1_without_sizes_material_lines_have_no_amount')
        fixture.setUp();self.addCleanup(fixture.tearDown)
        fixture._prepare_project()
        doc=packaging_parts.load_business_parts(PID)
        doc['business_parts'][0]['name']='新内围忖片'
        doc['business_parts'][0]['reference'].pop('material_text',None)
        packaging_parts.save_business_parts(PID,doc)
        run=self.runner([{'key':'material:DWG-BP01','candidate_codes':['MAT-PKG-LINER'],
                          'confidence':.9,'reason':'内围保护用纸与库内衬纸对应'}])
        with patch.object(llm_client,'run',run):
            packaging_demo_flow.prepare_names(PID,{'username':'PE1','role':'process_manager'})
        prepared=packaging_parts.load_business_parts(PID)
        inputs=prepared['business_parts'][0]['poc_inputs']
        self.assertEqual('MAT-PKG-LINER',inputs['material_code'])
        self.assertEqual('ai_semantic',inputs['recommendation']['material_source'])
        packaging_bom.build_bom(PID,REQ_NO)
        cost=packaging_cost.compute_project(PID,REQ_NO)
        material=next(r for r in cost['items'] if r['part_code']=='DWG-BP01' and r['cost_category']=='material')
        self.assertGreater(material['amount'],0)
        self.assertEqual(.05,material['loss_rate'])

    def test_name_model_runs_inside_background_job_not_http_submission(self):
        import ast, pathlib, types
        path=pathlib.Path(__file__).resolve().parents[1]/'tech_app/backend/main.py'
        tree=ast.parse(path.read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='prepare_packaging_name_associations')
        fn.decorator_list=[];fn.args.defaults=[]
        for arg in fn.args.args:arg.annotation=None
        module=ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[]))
        submit=Mock(return_value='background-task')
        namespace={'_require':Mock(),'auth':types.SimpleNamespace(WRITE_ROLES={'engineer'}),
                   '_workflow_project':Mock(),'store':types.SimpleNamespace(load_requirement=lambda p:{'data':{'industry':'packaging'}}),
                   'tasks':types.SimpleNamespace(submit=submit)}
        exec(compile(module,str(path),'exec'),namespace)
        result=namespace[fn.name]('P',{'username':'PE1','role':'engineer'})
        self.assertEqual({'task_id':'background-task'},result)
        self.assertTrue(callable(submit.call_args.args[2]))
        self.assertEqual('packaging_name_associations',submit.call_args.args[1])

    def service(self):
        try:return importlib.import_module('tech_app.backend.services.packaging_name_assist')
        except ModuleNotFoundError:self.fail('缺少受约束名称语义服务')

    def doc(self):
        return {'selected_scheme_id':'S1','business_parts':[
            {'business_part_code':'P1','name':'新内围忖片','scheme_id':'S1'},
            {'business_part_code':'P2','name':'外层倒扣面纸','scheme_id':'S1'},
            {'business_part_code':'P3','name':'右盖面纸','scheme_id':'S2'}],
            'layout_rows':[{'entity_id':'L1','scheme_id':'S1','text':'内围衬纸+倒扣面纸 各排2模',
                            'association_gaps':[{'name':'倒扣面纸'}],'n_up':2}]}

    def runner(self, entries):
        return Mock(side_effect=lambda system, blocks, schema:schema.model_validate({'suggestions':entries}))

    def test_batched_model_can_associate_multiple_existing_parts(self):
        svc=self.service();doc=self.doc();before=copy.deepcopy(doc)
        run=self.runner([{'key':'layout:L1','candidate_codes':['P1','P2'],'confidence':.9,'reason':'名称及同方案用途对应'}])
        result=svc.resolve_document(doc,[],run=run)
        row=result['layout_rows'][0]
        self.assertEqual(['P1','P2'],row['part_codes'])
        self.assertEqual({'P1':2,'P2':2},row['part_counts'])
        self.assertEqual('ai_semantic',row['assignment_source'])
        self.assertEqual(before,doc)
        self.assertEqual(1,run.call_count)
        svc.resolve_document(result,[],run=Mock(side_effect=AssertionError('同版本不能重复调用')))

    def test_bad_ids_other_schemes_and_low_confidence_are_rejected(self):
        svc=self.service()
        for codes,confidence in ((['UNKNOWN'],.9),(['P3'],.9),(['P2'],.2)):
            result=svc.resolve_document(self.doc(),[],run=self.runner([
                {'key':'layout:L1','candidate_codes':codes,'confidence':confidence,'reason':'候选'}]))
            self.assertFalse(result['layout_rows'][0].get('part_codes'))

    def test_manual_selection_is_preserved_and_model_failure_degrades(self):
        svc=self.service();doc=self.doc()
        doc['layout_rows'][0].update(part_codes=['P2'],assignment_source='user_selection')
        result=svc.resolve_document(doc,[],run=self.runner([]))
        self.assertEqual(['P2'],result['layout_rows'][0]['part_codes'])
        result=svc.resolve_document(self.doc(),[],run=Mock(side_effect=RuntimeError('offline')))
        self.assertFalse(result['layout_rows'][0].get('part_codes'))
        self.assertEqual('unavailable',result['name_assist']['status'])

    def test_material_suggestion_does_not_overwrite_explicit_material(self):
        svc=self.service();doc=self.doc();materials=[{'material_code':'M1','name':'内衬纸'}]
        entries=[{'key':'material:P1','candidate_codes':['M1'],'confidence':.9,'reason':'内围衬纸语义'}]
        result=svc.resolve_document(doc,materials,run=self.runner(entries))
        self.assertEqual('M1',result['business_parts'][0]['semantic_material']['material_code'])
        self.assertNotIn('reference',result['business_parts'][0])
        doc['business_parts'][0]['reference']={'material_text':'EVA'}
        result=svc.resolve_document(doc,materials,run=self.runner(entries))
        self.assertNotIn('semantic_material',result['business_parts'][0])

    def test_saved_model_layout_survives_read_without_model_and_expires_on_change(self):
        svc=self.service();from tech_app.backend.services import packaging_layout
        run=self.runner([{'key':'layout:L1','candidate_codes':['P1','P2'],'confidence':.9,'reason':'同方案语义'}])
        result=svc.resolve_document(self.doc(),[],run=run)
        rows=packaging_layout.auto_assign_layout_rows(result['layout_rows'],result['business_parts'])
        self.assertEqual(['P1','P2'],rows[0]['part_codes'])
        result['business_parts'][1]['name']='完全不同的新部件'
        rows=packaging_layout.auto_assign_layout_rows(rows,result['business_parts'])
        self.assertNotEqual('ai_semantic',rows[0].get('assignment_source'))

    def test_directions_material_conflicts_and_duplicate_answers_are_rejected(self):
        svc=self.service();doc=self.doc()
        doc['layout_rows'][0].update(text='左盖面纸 排2模')
        doc['business_parts'][1]['name']='右盖面纸'
        result=svc.resolve_document(doc,[],run=self.runner([
            {'key':'layout:L1','candidate_codes':['P2'],'confidence':.95,'reason':'猜测'}]))
        self.assertFalse(result['layout_rows'][0].get('part_codes'))
        entry={'key':'layout:L1','candidate_codes':['P2'],'confidence':.95,'reason':'猜测'}
        result=svc.resolve_document(self.doc(),[],run=self.runner([entry,entry]))
        self.assertFalse(result['layout_rows'][0].get('part_codes'))
        doc=self.doc();doc['business_parts'][0]['name']='不明磁性附件'
        result=svc.resolve_document(doc,[{'material_code':'PAPER','name':'面纸'}],run=self.runner([
            {'key':'material:P1','candidate_codes':['PAPER'],'confidence':.99,'reason':'模型猜测'}]))
        # “磁性”未进入既有材质族，不作为硬证据；明确磁铁必须拒绝跨族。
        doc['business_parts'][0]['name']='磁铁附件'
        result=svc.resolve_document(doc,[{'material_code':'PAPER','name':'面纸'}],run=self.runner([
            {'key':'material:P1','candidate_codes':['PAPER'],'confidence':.99,'reason':'模型猜测'}]))
        self.assertNotIn('semantic_material',result['business_parts'][0])

    def test_semantic_material_is_consumed_as_demo_input_not_authority(self):
        from tech_app.backend.services import packaging_demo_inputs as demo
        svc=self.service();materials=[{'material_code':'M1','name':'内衬纸','grade':'80g'}]
        updated=svc.resolve_document(self.doc(),materials,run=self.runner([
            {'key':'material:P1','candidate_codes':['M1'],'confidence':.9,'reason':'内围保护用途'}]))
        row=updated['business_parts'][0]
        with patch.object(demo,'_kb_price',return_value={'unit_price':9,'source':'kb_material_price'}):
            result=demo.demo_inputs_for(row,{},materials=materials,allow_estimates=True)
        self.assertEqual('ai_semantic',result['material_source'])
        self.assertEqual('M1',result['recommended_material']['material_code'])
        self.assertEqual(80,result['grammage'])
        self.assertNotIn('reference',row)

    def test_page_projection_does_not_call_model_or_write(self):
        svc=self.service();doc=self.doc()
        result=svc.resolve_document(doc,[],run=self.runner([
            {'key':'layout:L1','candidate_codes':['P1','P2'],'confidence':.9,'reason':'同方案用途'}]))
        from tech_app.backend.services import packaging_workspace, llm_client, packaging_parts
        from tech_app.backend.models.integration import IntegrationPlan
        with patch.object(llm_client,'run',side_effect=AssertionError('GET不能调模型')), \
             patch.object(packaging_parts,'save_business_parts',side_effect=AssertionError('GET不能写')):
            board=packaging_workspace.build('P',result,{},IntegrationPlan(),{})
        self.assertEqual(['P1','P2'],board['layouts'][0]['part_codes'])

    def test_association_reasons_render_with_existing_small_controls_and_escaping(self):
        import pathlib, subprocess
        from tests.test_packaging_whole_part_process_unit_red import js_function
        path=pathlib.Path(__file__).resolve().parents[1]/'tech_app/frontend/assembly-integration.js'
        script="const esc=v=>String(v??'').replaceAll('<','&lt;');const aiAttr=esc;const aiBusinessParts="+json.dumps({
            'business_parts':[{'business_part_code':'P','name':'面纸'}],
            'layout_rows':[{'entity_id':'L','text':'面纸排模','part_codes':['P'],
              'assignment_source':'ai_semantic','assignment_evidence':[{'part_code':'P','confidence':.9,'reason':'<script>unsafe'}]}]},ensure_ascii=False)+";"
        script+=js_function(path,'aiRenderPackagingLayout')
        script+="const h=aiRenderPackagingLayout();if(!h.includes('AI关联依据')||!h.includes('90%')||h.includes('<script>'))throw Error('语义依据未安全渲染');"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
