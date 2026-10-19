"""包装六项演示交互红测；只读本地源代码，业务存储与外部调用全部隔离。"""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import zipfile

from tests.test_packaging_whole_part_process_unit_red import js_function

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / 'tech_app/frontend'
HEADINGS = ['一.项目名称', '二.需求', '三.产品技术规格', '3.1 产品与订单',
            '3.2 成品尺寸与盒型', '3.3 材料', '3.4 印刷与表面工艺', '3.5 包装与物流']
PDF_SHA = '91081be68614406f62de0fb18ccc35303da91ea66e6b05c0484f2ac489dac7dc'


class PackagingDemoHandoff(unittest.TestCase):
    def node(self, script):
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def rule_check(self, industry='packaging'):
        from tech_app.backend.services import requirement_service as service
        doc = service.RequirementDoc(project_id='P', requirement_no='R', title='茅台酒盒', data={'industry': industry})
        with patch.object(service.store, 'load_meta', return_value={}):
            return service.requirement_precheck('P', doc)

    def test_packaging_rule_check_has_exact_eight_requested_rows(self):
        self.assertEqual(HEADINGS, [row['item'] for row in self.rule_check()['items']])

    def test_other_industry_checks_are_not_replaced(self):
        names = [row['item'] for row in self.rule_check('battery')['items']]
        self.assertNotEqual(HEADINGS, names)
        self.assertTrue(any('客户与项目' in name for name in names))

    def test_ai_cannot_turn_missing_saved_dimensions_into_confirmed_values(self):
        from tech_app.backend import main
        rules = self.rule_check()
        result = main.RequirementAiCheckResult(summary='模型错误地认为完整', items=[
            {'item': item['item'], 'status': 'ok', 'detail': '已齐全'} for item in rules['items']])
        with patch.object(main.llm_client, 'last_used_model', return_value='test-model'):
            check = main._normalize_requirement_ai_check(rules, result)
        self.assertEqual('need_info', next(row for row in check['items'] if row['item'] == '3.2 成品尺寸与盒型')['status'])
        self.assertIn('成品内长', json.dumps(check, ensure_ascii=False))

    def test_ai_request_uses_packaging_eight_rows_for_engineer(self):
        from tech_app.backend import main
        saved = {'project_id': 'P', 'requirement_no': 'R', 'title': '酒盒',
                 'data': {'industry': 'packaging'}, 'status': 'pending_confirmation'}
        model_result = main.RequirementAiCheckResult(summary='待补充', items=[])
        with patch.object(main, '_workflow_project', return_value={}), \
             patch.object(main.store, 'load_requirement', return_value=saved), \
             patch.object(main.store, 'load_meta', return_value={}), \
             patch.object(main.store, 'source_path', return_value=None), \
             patch.object(main.store, 'save_requirement'), patch.object(main.store, 'audit'), \
             patch.object(main.llm_client, 'run', return_value=model_result) as run, \
             patch.object(main.llm_client, 'last_used_model', return_value='test-model'):
            result = main.ai_check_requirement('P', user={'role': 'engineer', 'username': 'PE1'})
        prompt = run.call_args.args[0]
        for name in HEADINGS: self.assertIn(name, prompt)
        self.assertNotIn('半导体', prompt)
        self.assertEqual(HEADINGS, [row['item'] for row in result['check']['items']])

    def test_old_ai_check_display_is_projected_without_rewriting_history(self):
        code = js_function(FRONT / 'requirement-confirm-page.js', 'cfCurrentCheck')
        result = self.node('const cfRequirement={data:{industry:"packaging"}};'+code+"""
const rules={items:[{item:'一.项目名称',status:'ok',detail:'已提供'},
{item:'3.2 成品尺寸与盒型',status:'need_info',detail:'成品内长未填'}]};
const old={engine:'qwen',items:[{item:'客户与项目信息',status:'ok'},
{item:'3.2 成品尺寸与盒型',status:'ok',detail:'旧检查声称齐全'}]};
const before=JSON.stringify(old);const result=cfCurrentCheck(rules,old);
console.log(JSON.stringify({result,unchanged:before===JSON.stringify(old)}));
""")
        self.assertTrue(result['unchanged'])
        self.assertEqual(['一.项目名称', '3.2 成品尺寸与盒型'], [r['item'] for r in result['result']['items']])
        self.assertEqual('need_info', result['result']['items'][1]['status'])

    def test_ai_mapping_uses_current_packaging_catalog_not_old_eleven_rows(self):
        from tech_app.backend import main
        result = main.RequirementAiCheckResult(summary='尚缺尺寸', items=[
            {'item': '一、需求基本信息（Section A）', 'status': 'ok', 'detail': '旧行业项'}])
        with patch.object(main.llm_client, 'last_used_model', return_value='test-model'):
            check = main._normalize_requirement_ai_check(self.rule_check(), result)
        self.assertEqual(HEADINGS, [row['item'] for row in check['items']])
        self.assertTrue(any(row['status'] == 'need_info' for row in check['items']))

    def test_marked_maotai_original_preview_and_download_return_original_attachment(self):
        from tech_app.backend import main
        saved = {'title': '茅台酒盒', 'data': {'industry': 'packaging',
                 'requirement_pdf_template': 'maotai_wine_box'}}
        with patch.object(main, '_workflow_project', return_value={}), \
             patch.object(main.store, 'load_requirement', return_value=saved), \
             patch.object(main.store, 'load_meta', return_value={}):
            responses = [main.get_original_requirement_pdf('P', download=value, user={'role': 'engineer'})
                         for value in (False, True)]
        for response in responses:
            self.assertEqual(PDF_SHA, hashlib.sha256(response.body).hexdigest())
            self.assertEqual('application/pdf', response.media_type)
        self.assertIn('inline', responses[0].headers['content-disposition'])
        self.assertIn('attachment', responses[1].headers['content-disposition'])

    def test_unmarked_packaging_project_uses_fixed_pdf_per_latest_decision(self):
        from tech_app.backend import main
        from tech_app.backend.services.packaging_requirement_document import build_snapshot
        with patch.object(main, '_workflow_project', return_value={}), \
             patch('tech_app.backend.services.packaging_requirement_document.load_snapshot', return_value=build_snapshot({'data': {'industry': 'packaging'}}, {})), \
             patch.object(main.store, 'load_requirement', return_value={'data': {'industry': 'packaging'}}), \
             patch.object(main.store, 'load_meta', return_value={}):
            response = main.get_requirement_pdf('P', user={'role': 'engineer'})
        self.assertEqual(PDF_SHA, hashlib.sha256(response.body).hexdigest())
        self.assertTrue(response.body.startswith(b'%PDF'))

    def test_packaging_merged_approval_does_not_ask_missing_field_confirmation(self):
        code = js_function(FRONT / 'requirement-confirm-page.js', 'cfAct')
        result = self.node("""
const calls=[],prompts=[],nav=[],cfPid='P',CF_RETURNABLE_STATUSES=['pending_confirmation'];
const cfRequirement={status:'pending_confirmation',data:{industry:'packaging'}};
const document={querySelector:()=>({value:''})};
const window={confirm:message=>{prompts.push(message);return false},
TechEmbed:{embedded:true,requestNavigate:(...v)=>nav.push(v)}};
const cfGaps=()=>['成品内长','成品内宽','期望首样交付时间'];
const cfToast=()=>{},cfPublishTaskEvent=()=>{},api=async(url,body)=>{calls.push({url,body});return {}};
""" + code + """
(async()=>{const result=await cfAct('confirm');console.log(JSON.stringify({result,prompts,calls,nav}))})()
.catch(e=>{console.error(e);process.exit(1)});
""")
        self.assertEqual([], result['prompts'])
        self.assertTrue(result['result']['ok'])
        self.assertEqual(1, len(result['calls']))
        self.assertEqual('drawing', result['nav'][0][0])

    def test_send_action_hidden_before_explicit_process_confirmation(self):
        text = (FRONT / 'app.js').read_text()
        start = text.index('    sendPartsToPricing: {')
        end = text.index('    runAllPartProcesses:', start)
        action = text[start:end]
        out = self.node("""
const currentProject='P', currentPackagingBusinessParts={business_parts:[{business_part_code:'A'}]};
const window={PackagingHandoff:{current:()=>({confirmed:false})}}, allPartsProcessBusy=false;
const partsProcessComplete=()=>true,sendPackagingPartsToPricing=()=>{};
const actions=({""" + action + """});console.log(JSON.stringify(actions.sendPartsToPricing.getState()));
""")
        self.assertFalse(out['visible'], '推荐齐全不等于工程师已经确认当前工艺')

    def test_actual_confirm_action_only_confirms_then_reveals_send_on_same_page(self):
        text = (FRONT / 'app.js').read_text()
        actions = text[text.index('    confirmDrawingResult: {'):text.index('    runAllPartProcesses:', text.index('    confirmDrawingResult: {'))]
        helper = (FRONT / 'packaging-handoff.js').read_text()
        out = self.node("""
const requests=[],nav=[],window={TechBoardRuntime:{refreshState(){}},TechEmbed:{requestNavigate:value=>nav.push(value)}};
const currentProject='P',API='',allPartsProcessBusy=false;
const currentPackagingBusinessParts={business_parts:[{business_part_code:'A'}]};let currentIR={};let confirmed=false;
const drawingParsed=()=>true,renderIR=()=>{},status=()=>{},packagingBusinessPartRows=doc=>doc.business_parts,
partsProcessComplete=()=>true,sendPackagingPartsToPricing=()=>{throw Error('confirm must not open picker')};
const fetch=async(url,opts={})=>{
requests.push({url,method:opts.method||'GET'});
if(url.endsWith('/confirm'))confirmed=true;
return {ok:true,json:async()=>url.endsWith('/confirmation')?{ready:true,confirmed}:
url.endsWith('/requirement')?{requirement:{data:{industry:'packaging'}}}:{}};
};
""" + helper + 'const actions=({' + actions + """});
(async()=>{
await window.PackagingHandoff.refresh('P');const before=actions.sendPartsToPricing.getState();
const result=await actions.confirmDrawingResult.run();
console.log(JSON.stringify({result,before,send:actions.sendPartsToPricing.getState(),
confirm:actions.confirmDrawingResult.getState(),requests,nav}));
})().catch(error=>{console.error(error);process.exit(1)});
""")
        self.assertTrue(out['result']['ok'])
        self.assertFalse(out['before']['visible'])
        self.assertTrue(out['send']['visible'])
        self.assertFalse(out['confirm']['visible'])
        self.assertEqual([], out['nav'])
        self.assertEqual(['/api/projects/P/integration/process/confirm'],
                         [r['url'] for r in out['requests'] if r['method'] == 'POST'])

    def test_click_send_opens_selection_before_any_post_and_never_navigates(self):
        code = js_function(FRONT / 'app.js', 'sendPackagingPartsToPricing')
        result = self.node("""
const requests=[],nav=[],currentProject='P',API='';
let opened=false;
const window={TechEmbed:{requestNavigate:(...v)=>nav.push(v)},PackagingHandoff:{open:options=>{opened=options.projectId==='P';return {ok:true}}}};
const fetch=async(url,opts)=>{requests.push({url,body:opts?.body});return {ok:true,json:async()=>({finance:{task_id:'T'}})}};
""" + code + """
(async()=>{await sendPackagingPartsToPricing();console.log(JSON.stringify({requests,nav,opened}))})()
.catch(e=>{console.error(e);process.exit(1)});
""")
        self.assertEqual([], result['requests'], '打开选人窗时不得直接创建交接任务')
        self.assertEqual([], result['nav'], '发送不应跳到成本页')
        self.assertTrue(result['opened'])

    def test_backend_rejects_unconfirmed_before_route_preparation_or_dispatch(self):
        from tech_app.backend.services import integration, packaging_demo_flow
        from tech_app.backend.models.integration import IntegrationPlan
        plan = IntegrationPlan(project_id='P')
        with patch.object(integration.store, 'load_requirement', return_value={'data': {'industry': 'packaging'}}), \
             patch.object(integration, 'load_plan', return_value=plan), \
             patch.object(packaging_demo_flow, 'prepare_route') as prepare, \
             patch.object(integration, '_send_packaging_finance_draft', return_value=plan) as dispatch:
            with self.assertRaises(integration.IntegrationFlowError):
                integration.send_to_finance('P', {'username': 'PE1', 'role': 'engineer'},
                                            target_type='user', target_user_id='FI1')
            prepare.assert_not_called()
            dispatch.assert_not_called()

    def test_commercial_result_has_no_demo_disclaimer_without_altering_saved_quote(self):
        from cpq_commercial_quote import document
        q = {'demo_only': True, 'customer': '茅台', 'quote_quantity': 1000,
             'net_unit_price': 47.74, 'untaxed_total': 47740, 'taxed_unit_price': 53.95,
             'taxed_total': 53946.2, 'tax_rate': .13}
        original = copy.deepcopy(q)
        result = document(q)
        self.assertNotIn('演示报价单，不得用于正式交易', result['markdown'])
        self.assertIn('47740.00', result['markdown'])
        self.assertIn('茅台', result['markdown'])
        self.assertEqual(original, q)

    def word_content(self):
        import cpq_agent_server as server
        quote = {'demo_only': True, 'customer': '茅台', 'product_name': '茅台酒盒',
                 'quote_quantity': 1000, 'net_unit_price': 47.74, 'untaxed_total': 47740,
                 'taxed_unit_price': 53.95, 'taxed_total': 53946.2, 'tax_rate': .13}
        with patch.object(server, '_packaging_demo_export_quote', return_value=quote), \
             patch('cpq_wf.step_snapshot', return_value={}):
            data = server._build_quote_docx({'session_id': 'Q'})
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read('word/document.xml')
        from xml.etree import ElementTree as ET
        root = ET.fromstring(xml)
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        text = ''.join(root.itertext())
        paragraphs = [''.join(p.itertext()) for p in root.findall('w:body/w:p', ns)]
        return text, paragraphs

    def test_actual_word_has_no_demo_disclaimer(self):
        text, _ = self.word_content()
        self.assertNotIn('演示报价单，不得用于正式交易', text)

    def test_actual_word_has_no_duplicate_customer_paragraph_but_retains_table_customer(self):
        text, paragraphs = self.word_content()
        self.assertFalse(any(p.startswith('客户：') for p in paragraphs), paragraphs)
        self.assertIn('茅台', text)
        self.assertIn('47740.00', text)
