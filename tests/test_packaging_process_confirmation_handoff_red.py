"""真实确认保存/失效与真实浏览器选人交接，隔离全部线上依赖。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tech_app.backend.services import integration, packaging_parts, packaging_process_instances
from tech_app.backend.services import packaging_process_confirmation as confirmation

ROOT = Path(__file__).resolve().parents[1]


class Confirmation(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        self.doc = {'business_parts': [{'business_part_code': 'A', 'name': '灰板',
                    'reference': {'material_text': '2mm灰板'}, 'quantity': 1,
                    'geometry_binding': {'entity_ids': ['E1'], 'status': 'bound'},
                    'sections': [{'section_id': 'S', 'entity_ids': ['E1'], 'quantity': 1}]}]}
        self.collected = {'missing': [], 'records': [{'part_code': 'A', 'usage_qty': 1,
                          'plan': {'steps': [{'step_no': 10, 'name': '模切'}]}}]}
        patches = [patch.object(integration.store, 'load_integration', side_effect=lambda pid: copy.deepcopy(self.saved)),
                   patch.object(integration.store, 'save_integration', side_effect=lambda pid, data, **kw: self.saved.update(json.loads(json.dumps(data)))),
                   patch.object(integration.store, 'load_requirement', return_value={'data': {'industry': 'packaging'}}),
                   patch.object(integration.store, 'audit'),
                   patch.object(packaging_parts, 'load_business_parts', side_effect=lambda *a: copy.deepcopy(self.doc)),
                   patch.object(packaging_process_instances, 'collect', side_effect=lambda *a, **kw: copy.deepcopy(self.collected))]
        for item in patches:
            item.start(); self.addCleanup(item.stop)

    def confirm(self):
        return integration.confirm_process('P', {'username': 'PE1', 'role': 'engineer'})

    def test_confirmation_saves_reloads_and_is_idempotent_without_assembly(self):
        self.assertFalse(confirmation.status('P')['confirmed'])
        with patch.object(integration, 'save_plan', wraps=integration.save_plan) as save:
            plan = self.confirm()
            self.assertIsNone(plan.process)
            self.assertFalse(plan.process_confirmed)
            self.assertEqual('PE1', confirmation.status('P')['confirmed_by'])
            self.assertTrue(confirmation.status('P')['confirmed'])
            self.confirm(); self.assertEqual(1, save.call_count)

    def test_geometry_material_quantity_size_sections_and_process_changes_expire_confirmation(self):
        for change in ('geometry', 'material', 'quantity', 'size', 'section', 'process'):
            with self.subTest(change=change):
                before_doc, before_records = copy.deepcopy(self.doc), copy.deepcopy(self.collected)
                self.confirm()
                row = self.doc['business_parts'][0]
                if change == 'geometry': row['geometry_binding']['entity_ids'] = ['E2']
                if change == 'material': row['reference']['material_text'] = '225g面纸'
                if change == 'quantity': row['quantity'] = 2
                if change == 'size': row['confirmed_size'] = {'length_mm': 200, 'width_mm': 80}
                if change == 'section': row['sections'][0]['entity_ids'] = ['E2']
                if change == 'process': self.collected['records'][0]['plan']['steps'][0]['name'] = '覆膜'
                self.assertFalse(confirmation.status('P')['confirmed'])
                self.doc, self.collected = before_doc, before_records

    def test_thumbnail_display_order_and_record_timestamp_do_not_expire_confirmation(self):
        self.confirm()
        self.doc['business_parts'][0].update(thumbnail='NEW', display_code='02')
        self.collected['records'][0]['updated_at'] = 'NEW'
        self.assertTrue(confirmation.status('P')['confirmed'])

    def test_missing_or_partial_results_cannot_confirm(self):
        self.collected['missing'] = ['B']
        with self.assertRaises(integration.IntegrationFlowError): self.confirm()
        self.assertFalse(confirmation.status('P')['confirmed'])
        self.assertEqual({}, self.saved)

    def test_sales_role_cannot_confirm(self):
        with self.assertRaises(integration.IntegrationFlowError):
            integration.confirm_process('P', {'username': 'SM1', 'role': 'sales_manager'})
        self.assertEqual({}, self.saved)

    def test_stale_or_draft_send_cannot_bypass_confirmation(self):
        self.confirm()
        self.doc['business_parts'][0]['quantity'] = 2
        with patch('tech_app.backend.services.packaging_demo_flow.prepare_route') as prepare, \
             patch.object(integration, '_send_packaging_finance_draft') as send:
            for draft in (False, True):
                with self.assertRaises(integration.IntegrationFlowError):
                    integration.send_to_finance('P', {'role': 'engineer'}, target_type='user', target_user_id='9', draft=draft)
            prepare.assert_not_called(); send.assert_not_called()

    def test_confirmed_send_requires_person_and_does_not_change_confirmation(self):
        self.confirm()
        with self.assertRaises(integration.IntegrationFlowError):
            integration.send_to_finance('P', {'role': 'engineer'}, target_type='role', target_role_code='finance_mgr')
        with patch('tech_app.backend.services.packaging_demo_flow.prepare_route') as prepare, \
             patch.object(integration, '_send_packaging_finance_draft', return_value='SENT') as send:
            self.assertEqual('SENT', integration.send_to_finance('P', {'role': 'engineer'}, target_type='user', target_user_id='9'))
            prepare.assert_called_once_with('P', {'role': 'engineer'}, confirmed_only=True)
            self.assertEqual('9', send.call_args.kwargs['target_user_id'])
        self.assertTrue(confirmation.status('P')['confirmed'])


class PickerChrome(unittest.TestCase):
    def test_actual_picker_search_cancel_retry_send_and_project_isolation(self):
        from tests._chrome_page_probe import page_probe
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists(): self.skipTest('Chrome unavailable')
        code = (ROOT / 'tech_app/frontend/packaging-handoff.js').read_text()
        script = """
const posts=[], events=[]; let confirmed=false, failList=false, failSend=false, releaseSend=null;
window.TechBoardRuntime={refreshState(){},publish:(...args)=>events.push(args)};
window.cpqAuth={api:async()=>{
 if(failList)throw Error('读取失败');
 return {users:[{user_id:'90071992547409991',username:'FI1',display_name:'报价甲',role_code:'finance_mgr'},
 {user_id:'90071992547409992',username:'FI2',display_name:'报价乙',role_code:'finance_mgr'},
 {user_id:'3',username:'SM1',role_code:'sales_mgr'}]};}};
window.fetch=async(url,options={})=>{
 if(options.method==='POST'&&url.endsWith('/confirm')){confirmed=true;return {ok:true,json:async()=>({})};}
 if(options.method==='POST'){
   posts.push({url,body:JSON.parse(options.body)});
   if(releaseSend!==null)await new Promise(resolve=>releaseSend=resolve);
   return {ok:!failSend,json:async()=>failSend?{detail:'发送失败'}:{finance:{task_id:'T'}}};
 }
 return {ok:true,json:async()=>({ready:true,confirmed})};
};
const tick=()=>new Promise(resolve=>setTimeout(resolve,15));
const check=(value,message)=>{if(!value)throw Error(message)};
""" + code + """
(async()=>{
 const h=window.PackagingHandoff;
 await h.refresh('P');check(!h.current('P').confirmed,'unconfirmed exposed');
 await h.confirm('P');check(h.current('P').confirmed&&posts.length===0,'confirm dispatched a task');
 h.open({projectId:'P'});await tick();
 check(posts.length===0,'opened picker sent task');
 check(document.querySelector('#pricingUser').options.length===3,'wrong roles included');
 check(document.querySelector('.send').disabled,'person not selected');
 document.querySelector('#pricingSearch').value='FI2';document.querySelector('#pricingSearch').oninput();
 check(document.querySelector('#pricingUser').options.length===2,'search failed');
 document.querySelector('.cancel').click();check(!document.querySelector('#packagingPricingDialog')&&posts.length===0,'cancel sent');
 failList=true;h.open({projectId:'P'});await tick();
 check(document.querySelector('.send').disabled&&!document.querySelector('.retry').hidden,'list error hidden');
 failList=false;document.querySelector('.retry').click();await tick();
 const user=document.querySelector('#pricingUser');user.value='90071992547409991';user.onchange();
 failSend=true;document.querySelector('.send').click();await tick();
 check(document.querySelector('.ai-send-msg').textContent==='发送失败','send failure invisible');
 failSend=false;releaseSend=()=>{};
 document.querySelector('.send').click();document.querySelector('.send').click();await tick();
 check(posts.length===2,'double click duplicated dispatch');
 check(posts[1].body.target_type==='user'&&posts[1].body.target_user_id==='90071992547409991','recipient lost precision');
 releaseSend();await tick();releaseSend=null;
 check(!document.querySelector('#packagingPricingDialog'),'success did not close picker');
 check(location.pathname.endsWith('/view.html'),'unexpected navigation');
 h.open({projectId:'P'});await tick();h.activate('OTHER');
 check(!document.querySelector('#packagingPricingDialog'),'old project dialog remained');
 h.open({projectId:'OTHER'});await tick();await h.refresh('P');
 check(document.querySelector('#packagingPricingDialog')?.dataset.projectId==='OTHER','old project refresh dismissed current dialog');
 document.querySelector('.cancel').click();
 // 无关的较早状态响应不能覆盖改件后的新确认状态。
 let releaseOld;window.fetch=()=>new Promise(resolve=>releaseOld=resolve);
 const old=h.refresh('R');h.invalidate('R');
 window.fetch=async()=>({ok:true,json:async()=>({confirmed:false,ready:true})});
 await h.refresh('R');releaseOld({ok:true,json:async()=>({confirmed:true})});await old;
 check(!h.current('R').confirmed,'late response restored stale confirmation');
 document.querySelector('#probe').textContent='PASS';
})().catch(error=>document.querySelector('#probe').textContent='FAIL: '+error.stack);
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'view.html'
            path.write_text('<!doctype html><meta charset="utf-8"><pre id="probe"></pre><script>' + script + '</script>')
            self.assertEqual('PASS', page_probe(chrome, path, Path(directory)/'profile',
                "document.querySelector('#probe')?.textContent"))


class TargetedBridge(unittest.TestCase):
    def test_selected_pricing_identity_is_validated_and_returned_for_access(self):
        import cpq_tech_bridge as bridge
        user = {'role_code': 'process_mgr'}
        payload = {'tech_cost': {'requirement': {'industry': 'packaging'}}}
        with patch.object(bridge.cpq_auth, 'list_users', return_value=[
                {'user_id': '123', 'username': 'FI1', 'role_code': 'finance_mgr'}]), \
             patch.object(bridge, '_side_task', return_value={'task_id': 'T'}) as send:
            result = bridge.send_to_finance(user, 'Q', '酒盒', payload=payload, target_type='user', target_user_id='123')
            self.assertEqual('FI1', result['target_username'])
            self.assertEqual('123', result['target_user_id'])
            self.assertEqual('123', send.call_args.kwargs['target_user_id'])

    def test_unknown_or_wrong_role_recipient_does_not_create_task(self):
        import cpq_tech_bridge as bridge
        with patch.object(bridge.cpq_auth, 'list_users', return_value=[
                {'user_id': '123', 'username': 'SM1', 'role_code': 'sales_mgr'},
                {'user_id': '456', 'username': 'FI2', 'role_code': 'finance_mgr', 'status': 'disabled'}]), \
             patch.object(bridge, '_side_task') as send:
            for target in ('123', '456', 'MISSING'):
                with self.assertRaises(bridge.BridgeError):
                    bridge.send_to_finance({'role_code': 'process_mgr'}, 'Q', '酒盒',
                        payload={'tech_cost': {'requirement': {'industry': 'packaging'}}},
                        target_type='user', target_user_id=target)
            send.assert_not_called()

    def test_person_id_is_not_mistaken_for_project_participant_username(self):
        from tech_app.backend import main
        from tech_app.backend.models.integration import FinanceHandoff, IntegrationPlan
        plan = IntegrationPlan(project_id='P', finance_handoff=FinanceHandoff(
            task_id='T', target_type='user', target_user_id='123', target_username='FI1'))
        request = type('Request', (), {'headers': {}, 'query_params': {}})()
        with patch.object(main.store, 'load_requirement', return_value={'data': {'industry': 'packaging'}}), \
             patch.object(main, '_bridge_call', return_value=plan), \
             patch.object(main, '_integration_payload', return_value={}), \
             patch.object(main.cost_flow, 'grant_task_project_access', return_value={'granted': True}) as grant:
            main.integration_send_to_finance('P', main.IntegrationPublishBody(
                target_type='user', target_user_id='123'), request,
                user={'role': 'engineer', 'username': 'PE1'})
        self.assertEqual('FI1', grant.call_args.kwargs['username'])
