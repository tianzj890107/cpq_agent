import copy
import unittest
import json
import subprocess
from pathlib import Path
from unittest.mock import patch
from tech_app.backend.services import workflow_stages, requirement_service, packaging_assembly
from tests.test_packaging_quote_close_loop_red import package_fixture


class Streamlined(unittest.TestCase):
    def test_packaging_has_no_process_or_separate_requirement_review(self):
        rows=workflow_stages.for_industry('packaging')
        self.assertNotIn('process',{r['stage_id'] for r in rows})
        self.assertNotIn('requirement-review',{r['stage_id'] for r in rows})
        self.assertEqual('确认及审核',next(r for r in rows if r['stage_id']=='requirement-confirm')['title'])
        self.assertEqual({'1','2','3','4'},{r['phase'] for r in rows})

    def test_other_industry_keeps_original_workflow(self):
        self.assertEqual(workflow_stages.STAGES,workflow_stages.for_industry('battery'))

    def test_one_approval_saves_once_and_is_idempotent(self):
        saved={'project_id':'P','requirement_no':'R','title':'产品','status':'pending_confirmation','data':{'industry':'packaging'},'history':[]}
        with patch.object(requirement_service.store,'load_requirement',side_effect=lambda pid:copy.deepcopy(saved)),\
             patch.object(requirement_service.store,'save_requirement',side_effect=lambda pid,doc,**kw:saved.update(copy.deepcopy(doc))) as write,\
             patch.object(requirement_service.store,'audit'):
            out=requirement_service.confirm_requirement('P',{'username':'PE1','role':'engineer'},'通过')
            self.assertEqual('approved',out['status'])
            self.assertEqual('PE1',out['confirmed_by']);self.assertEqual('PE1',out['reviewed_by'])
            self.assertEqual(1,write.call_count)
            requirement_service.confirm_requirement('P',{'username':'PE1','role':'engineer'},'通过')
            self.assertEqual(1,write.call_count)

    def test_old_pending_review_can_complete_merged_action(self):
        saved={'project_id':'P','requirement_no':'R','status':'pending_review','data':{'industry':'packaging'},'history':[]}
        with patch.object(requirement_service.store,'load_requirement',return_value=saved),\
             patch.object(requirement_service.store,'save_requirement'),patch.object(requirement_service.store,'audit'):
            self.assertEqual('approved',requirement_service.confirm_requirement('P',{'username':'PE1','role':'engineer'})['status'])

    def test_finance_display_only_no_role_code_change(self):
        import cpq_auth
        from tech_app.backend.services import auth
        self.assertEqual('报价人员',cpq_auth.ROLES['finance_mgr'])
        self.assertEqual('报价人员',auth.ROLE_LABEL['finance_manager'])
        self.assertNotIn('sales_manager',auth.COST_ROLES)

    def test_existing_assembly_is_not_appended_or_read(self):
        with patch('tech_app.backend.services.integration.load_plan',side_effect=AssertionError('不读取旧组装')):
            rows=[{'step_name':'包盒','part_code':'A'}]
            self.assertEqual(rows,packaging_assembly.append_operations('P',rows))

    def test_packaging_final_document_is_commercial_not_internal(self):
        import cpq_packaging_quote as quote
        package=package_fixture()
        priced=quote.price(package,gross_margin_rate=.25)
        doc=quote.document(priced)
        self.assertIn('规格',doc['markdown']);self.assertIn('数量',doc['markdown'])
        self.assertIn('单价',doc['markdown']);self.assertIn('总价',doc['markdown'])
        for word in ['工艺路线','部件与材料','成本总额','毛利率','定价引擎','亿纬锂能']:
            self.assertNotIn(word,doc['markdown'])
        self.assertTrue(any(s.get('kind')=='table' for s in doc['sections']))

    def test_route_preparation_never_runs_name_or_assembly_models(self):
        from tech_app.backend.services import packaging_demo_flow,packaging_process_instances,packaging_bom,packaging_route,integration
        with patch.object(packaging_demo_flow,'ensure_inputs'),patch.object(packaging_demo_flow,'prepare_names',side_effect=AssertionError('不做排模名称补全')),\
             patch.object(packaging_process_instances,'collect',return_value={'missing':[],'active':True}),\
             patch.object(packaging_assembly,'prepare',side_effect=AssertionError('不生成组装')),\
             patch.object(integration,'load_plan'),\
             patch.object(packaging_demo_flow.store,'load_requirement',return_value={'requirement_no':'R'}),\
             patch.object(packaging_bom,'build_bom'),patch.object(packaging_route,'build_route'),patch.object(packaging_route,'confirm_route',return_value={}),\
             patch.object(packaging_demo_flow,'sync_integration',return_value='READY'):
            self.assertEqual('READY',packaging_demo_flow.prepare_route('P',{'username':'PE1'}))

    def test_model_prepare_legacy_entry_is_noop_and_preserves_history(self):
        from tech_app.backend.services import integration,llm_client
        with patch.object(integration,'load_plan',return_value='HISTORY'),patch.object(llm_client,'run',side_effect=AssertionError('不调模型')):
            self.assertEqual('HISTORY',packaging_assembly.prepare('P',{},force=True))

    def test_actual_cost_ignores_layout_but_preserves_part_assembly_work(self):
        from tests.test_packaging_demo_flow_end_to_end_red import DemoFlow,PID,REQ_NO
        from tech_app.backend.services import packaging_cost,packaging_sheet_cost,packaging_parts,packaging_bom,packaging_route
        fixture=DemoFlow('test_a5_native_per_part_processes_reach_material_and_labor_ledger')
        fixture.setUp()
        try:
            fixture.test_a5_native_per_part_processes_reach_material_and_labor_ledger()
            doc=packaging_parts.load_business_parts(PID)
            doc['layout_rows']=[{'entity_id':'UNUSED-SHEET','kind':'layout','part_codes':['DWG-BP01'],
                'sheet_mm':[1000,800],'n_up':8,'part_counts':{'DWG-BP01':8}}]
            packaging_parts.save_business_parts(PID,doc)
            packaging_bom.build_bom(PID,REQ_NO)
            packaging_route.build_route(PID,REQ_NO)
            packaging_route.confirm_route(PID,REQ_NO,actor={'username':'tester'})
            original=packaging_sheet_cost.apply_layout_costs
            def check(layouts,*args,**kwargs):
                self.assertEqual([],layouts)
                return original(layouts,*args,**kwargs)
            with patch.object(packaging_sheet_cost,'apply_layout_costs',side_effect=check) as apply:
                result=packaging_cost.build_cost(PID,REQ_NO,actor={'username':'tester'})
            apply.assert_called_once()
            self.assertGreater(result['material_total'],0)
            self.assertGreater(result['process_total'],0)
            self.assertEqual(0,result['labor_total'])
        finally:fixture.tearDown()

    def test_commercial_context_keeps_actual_spec_no_legacy_issuer(self):
        from cpq_commercial_quote import with_context,document
        q={'net_unit_price':10,'untaxed_total':10000,'taxed_unit_price':11.3,'taxed_total':11300,'tax_rate':.13,'quote_quantity':1000}
        saved=copy.deepcopy(q)
        enriched=with_context(q,{'requirement':{'inner_length':200,'inner_width':150,'inner_height':80,'packaging_product_name':'礼盒'}},
            {'客户':'真实客户','签约主体':'亿纬锂能股份有限公司','签约主体地址':'广东省惠州市仲恺高新区惠风七路38号','报价联系人':'实际联系人'})
        view=document(enriched)
        self.assertIn('200 × 150 × 80 mm',view['markdown'])
        self.assertIn('真实客户',view['markdown']);self.assertIn('实际联系人',view['markdown'])
        self.assertNotIn('亿纬',view['markdown']);self.assertNotIn('惠风七路',view['markdown'])
        self.assertEqual(saved,q)

    def test_projection_uses_merged_approval_and_four_phases(self):
        from tech_app.backend.services import workflow_projection
        facts={'requirement':{'status':'pending_review','data':{'industry':'packaging'}},'plan':None}
        self.assertFalse(workflow_projection._judge('1.2','P',facts)['completed'])
        facts['requirement']['status']='approved'
        self.assertTrue(workflow_projection._judge('1.2','P',facts)['completed'])
        with patch.object(workflow_projection,'_judge',return_value={'status':'confirmed','completed':True}):
            rows=workflow_projection._rows_for('P',facts,'engineer')
        phases=workflow_projection._phases(rows)
        self.assertEqual([1,2,3,4],[p['no'] for p in phases])
        self.assertNotIn('process',{r['stage_id'] for r in rows})
        confirm=next(r for r in rows if r['key']=='1.2')
        self.assertTrue(confirm['actionable'])

    def test_frontend_resume_packaging_skips_old_process_stage(self):
        path=Path(__file__).resolve().parents[1]/'tech_app/frontend/tech-stage-restore.js'
        script='const window={};'+path.read_text()+''';
const restore=window.TechStageRestore.fromSignals;
if(restore({requirement:{status:'approved',data:{industry:'packaging'}}},{has_ir:true},{})!=='cost')throw Error('packaging requires removed stage');
if(restore({requirement:{status:'approved',data:{industry:'battery'}}},{has_ir:true},{})!=='process')throw Error('other industry changed');
if(restore({requirement:{status:'pending_review',data:{industry:'packaging'}}},{},{})!=='requirement-confirm')throw Error('old review not merged');
'''
        result=subprocess.run(['node','-e',script],text=True,capture_output=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_technical_pdf_omits_business_fields(self):
        from tech_app.backend.services.requirement_pdf import build_requirement_pdf
        doc={'project_id':'P','title':'技术要求','data':{'industry':'packaging','customer_credit':'SECRET-CREDIT',
            'final_customer_name':'SECRET-CUSTOMER','project_code':'SECRET-PROJECT','packaging_product_name':'礼盒','inner_length':200}}
        pdf=build_requirement_pdf(doc)
        for value in ['SECRET-CREDIT','SECRET-CUSTOMER','SECRET-PROJECT']:
            self.assertNotIn(value.encode('utf-16-be').hex().upper().encode(),pdf)
        self.assertIn('产品技术规格'.encode('utf-16-be').hex().upper().encode(),pdf)

    def test_actual_word_contains_commercial_tables_not_route_dump(self):
        import io,zipfile
        import cpq_packaging_quote as quote,cpq_agent_server as server
        pkg=package_fixture();priced=quote.price(pkg,gross_margin_rate=.25)
        priced['product_name']='实际包装产品';priced['bom_items']=[{'item_name':'SECRET-PART'}]
        priced['route_steps']=[{'step_name':'SECRET-PROCESS'}]
        with patch.object(server,'_packaging_demo_export_quote',return_value=priced),\
             patch('cpq_wf.step_snapshot',side_effect=lambda sid,step:{'packaging_package':pkg} if step==2 else {}):
            data=server._build_quote_docx({'session_id':'Q'})
        with zipfile.ZipFile(io.BytesIO(data)) as z:xml=z.read('word/document.xml').decode()
        for word in ['产品名称','型号','规格','数量','单价','总价','含税总额','实际包装产品']:
            self.assertIn(word,xml)
        self.assertIn('<w:tbl>',xml)
        self.assertNotIn('SECRET-PART',xml);self.assertNotIn('SECRET-PROCESS',xml)

    def test_engineer_can_dispatch_pricing_without_assembly_or_parameter_confirmation(self):
        from tech_app.backend.services import integration,packaging_demo_flow,packaging_parts,packaging_route,cost_review
        from tech_app.backend.models.integration import IntegrationPlan
        plan=IntegrationPlan(project_id='P')
        req={'title':'包装产品','data':{'industry':'packaging','quote_quantity':1000}}
        doc={'business_parts_hash':'H','business_parts':[{'business_part_code':'A','name':'纸面'}]}
        with patch.object(integration.store,'load_requirement',return_value=req),\
             patch.object(integration,'load_plan',return_value=plan),patch.object(integration,'save_plan'),patch.object(integration.store,'audit'),\
             patch('tech_app.backend.services.packaging_process_confirmation.status',return_value={'confirmed':True}),\
             patch.object(packaging_demo_flow,'prepare_route') as prepare,patch.object(packaging_parts,'load_business_parts',return_value=doc),\
             patch.object(packaging_route,'load_route',return_value={'built':True,'steps':[{'step_name':'包盒'}]}),\
             patch.object(cost_review,'load_review'),patch.object(cost_review,'save_review'),\
             patch.object(integration.cpq_bridge,'send_to_finance',return_value={'task_id':'T','target_role_name':'报价人员'}) as send:
            result=integration.send_to_finance('P',{'username':'PE1','role':'engineer'},token='TEST',target_type='user',target_user_id='FINANCE-ID')
            prepare.assert_called_once();send.assert_called_once()
            self.assertEqual('T',result.finance_handoff.task_id)
            self.assertFalse(result.finance_handoff.draft)
            self.assertTrue(result.confirmed)
            self.assertFalse(result.process_confirmed)
            self.assertIsNone(result.process)

    def test_actual_chrome_confirmation_shows_only_technical_fields(self):
        import tempfile
        from tests._chrome_page_probe import page_probe
        from tests.test_packaging_whole_part_process_unit_red import js_function
        chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        page=Path(__file__).resolve().parents[1]/'tech_app/frontend/requirement-confirm-page.js'
        source=js_function(page,'cfTechnicalFields')+'\n'+js_function(page,'cfRender')
        context="""
const cfRequirement={title:'包装要求',status:'pending_review',data:{industry:'packaging',packaging_product_name:'礼盒',
inner_length:200,inner_width:150,inner_height:80,quote_quantity:1000,final_customer_name:'SECRET-CUSTOMER',customer_credit:'SECRET-CREDIT'}};
const cfPid='P',cfEsc=v=>String(v??''),statusLabel=v=>v,cfLabel=(k,v)=>v,cfSubLabel=v=>v,
cfIcon=()=>'',cfAiNote=()=>'',cfPrecheckRows=()=>'',cfSourceUrl=()=>'',cfBind=()=>{},cfPublishStatus=()=>{};
"""
        checks="""
cfRender();
const info=document.querySelector('.info-list').textContent;
if(!info.includes('200 × 150 × 80')||!info.includes('礼盒'))throw Error('technical fields missing');
if(info.includes('SECRET')||info.includes('客户信用')||info.includes('项目编码'))throw Error('business fields exposed');
if(!document.querySelector('#confirmPass').textContent.includes('确认及审核'))throw Error('old action');
document.querySelector('#probe').textContent='PASS';
"""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'view.html'
            path.write_text('<!doctype html><meta charset="utf-8"><div id="app"></div><pre id="probe"></pre><script>'+context+source+checks+'</script>')
            result=page_probe(chrome,path,Path(directory)/'profile',"document.querySelector('#probe')?.textContent")
            self.assertEqual('PASS',result)

    def test_frontend_visible_stages_and_continuous_major_numbers(self):
        source=(Path(__file__).resolve().parents[1]/'tech_app/frontend/tech-workbench.js').read_text()
        def declaration(name):
            begin=source.index('  const '+name+' = [');end=source.index('\n  ];',begin)+6
            return source[begin:end]
        begin=source.index('  const PACKAGING_MAJOR_STEPS=');end=source.index('\n  function currentMajorStep',begin)
        script="const state={industry:'packaging'};"+declaration('STAGES')+declaration('MAJOR_STEPS')+source[begin:end]+'''
if(visibleMajors().map(m=>m.no).join()!=='1,2,3,4')throw Error('numbering');
if(visibleStages().some(s=>s.id==='process'||s.id==='requirement-review'))throw Error('removed stage');
state.industry='battery';if(visibleMajors().length!==5||visibleStages().length!==9)throw Error('other industries changed');
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_actual_drawing_send_action_opens_person_picker_without_navigation(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        path=Path(__file__).resolve().parents[1]/'tech_app/frontend/app.js'
        script="""
let currentProject='P';const API='',calls=[],window={TechEmbed:{requestNavigate:s=>calls.push(s)},
PackagingHandoff:{open:options=>{calls.push(options.projectId);return {ok:true}}}};
"""+js_function(path,'sendPackagingPartsToPricing')+"""
sendPackagingPartsToPricing().then(result=>{
if(!result.ok||calls.length!==1||calls[0]!=='P')throw Error('person picker not opened or page navigated');
}).catch(e=>{console.error(e);process.exit(1)});
"""
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
