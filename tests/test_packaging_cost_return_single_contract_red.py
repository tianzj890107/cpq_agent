import copy
import pathlib
import unittest
from unittest.mock import patch, MagicMock
from tests.test_packaging_cost_engine_red import CostCase, PID
from tech_app.backend.models.integration import IntegrationPlan
from tech_app.backend.models.cost import CostAnalysis
from tech_app.backend.services import cost_flow, integration, packaging_handoff, report_workflow
from tech_app.backend.storage import store
import cpq_agent_server as quote_server
import cpq_tech_bridge as bridge
import importlib
import json

ROOT=pathlib.Path(__file__).resolve().parents[1]

def package(version='v1'):
    return {'industry':'packaging','result_version':version,'source':{'project_id':PID,'source_session_id':'QUOTE1','result_version':version},
        'requirement':{'packaging_product_name':'酒盒','quote_quantity':1000,'currency':'CNY'},
        'box_type':{'confirmed_box_type':'BOX1'},'params':{},
        'cost':{'built':True,'cost_profile':'detail_sheet/1','estimate_id':version,'quote_quantity':1000,
            'material_total':2,'process_total':6,'labor_total':.3,'packaging_total':1,'other_total':0,
            'tooling_total':0,'freight_total':0,'total_cost':9.3,'currency':'CNY','gaps':[],'has_gaps':False}}

def projection():
    return {'part_id':'ASSEMBLY','quantity':1000,'items':[{'category':'material','name':'材料','amount':2}],
        'cost_model':{'material':2,'processing':6,'others':1.3,'total':9.3,'source':'packaging_ledger','estimate_id':'v1'}}

class CanonicalSource(CostCase):
    def old_plan(self):
        p=IntegrationPlan(project_id=PID,quantity=1)
        p.cost=CostAnalysis(part_id='ASSEMBLY',items=[{'category':'material','name':'旧成本','amount':99}])
        return p

    def test_current_ledger_overrides_old_projection_and_keeps_audit(self):
        p=self.old_plan()
        with patch.object(integration,'load_plan',return_value=p),patch.object(store,'load_requirement',return_value={'data':{'industry':'packaging'}}), \
             patch.object(cost_flow,'packaging_cost_projection',return_value=projection()),patch.object(integration,'save_plan'),patch.object(store,'audit') as audit:
            result=cost_flow.integration_ready_cost(PID)
        self.assertEqual(9.3,result.cost.cost_model['total'])
        self.assertEqual(1000,result.quantity)
        self.assertTrue(audit.called,'旧成本投影必须留痕')

    def test_stale_ledger_cannot_escape_via_existing_old_plan(self):
        with patch.object(integration,'load_plan',return_value=self.old_plan()),patch.object(store,'load_requirement',return_value={'data':{'industry':'packaging'}}),patch.object(cost_flow,'packaging_cost_projection',return_value=None):
            with self.assertRaises(cost_flow.CostFlowError):cost_flow.integration_ready_cost(PID)

    def test_report_uses_current_packaging_contract_even_without_plan_cost(self):
        with patch.object(integration,'load_plan',return_value=IntegrationPlan(project_id=PID)), \
             patch.object(store,'load_requirement',return_value={'data':{'industry':'packaging'}}), \
             patch.object(cost_flow,'_packaging_package_of',return_value=package()):
            result=report_workflow._technical_result(PID,'酒盒')
        self.assertEqual(9.3,result.get('unit_cost'))
        self.assertEqual(1000,result.get('quantity'))
        self.assertEqual(package(),result.get('packaging_package'))

    def test_cost_return_body_cannot_mix_old_total_with_new_package(self):
        from types import SimpleNamespace
        review=SimpleNamespace(confirmed=True,confirmed_by='FI1',confirmed_at=None,note='')
        with patch.object(cost_flow.cost_review,'load_review',return_value=review),patch.object(cost_flow,'_cost_snapshot',return_value={}),patch.object(cost_flow,'_packaging_package_of',return_value=package()):
            result=cost_flow.integration_quote_result(PID,self.old_plan(),'酒盒',{'data':{'industry':'packaging'}})
        self.assertEqual(9.3,result['unit_cost']);self.assertEqual(9.3,result['cost']['total'])
        self.assertEqual(1000,result['quantity'])

class ReturnFields(unittest.TestCase):
    def test_legacy_nested_cost_normalizes_without_zero_or_price_guess(self):
        data={'tech_project_id':PID,'packaging_package':package()}
        result=quote_server.normalize_finance_handoff({'tech_result':data},session_id='QUOTE1')
        self.assertEqual(9.3,result['unit_cost'])
        self.assertEqual(1000,result['quantity'])
        self.assertEqual(9300,result['order_total_cost'])
        self.assertFalse(result['provisional'])

    def test_direct_handoff_contract_and_same_price_new_version(self):
        first=packaging_handoff.bridge_result(package())
        second=packaging_handoff.bridge_result(package('v2'))
        self.assertEqual(9.3,first.get('unit_cost'))
        self.assertNotEqual(first.get('cost_fingerprint'),second.get('cost_fingerprint'))
        original=package()['cost'];changed=copy.deepcopy(original);changed['estimate_id']='v2'
        self.assertNotEqual(packaging_handoff.result_version_of(original),packaging_handoff.result_version_of(changed))

    def test_real_gaps_survive_legacy_normalization(self):
        p=package();p['cost'].update(has_gaps=True,gaps=[{'code':'missing_material'}])
        result=quote_server.normalize_finance_handoff({'tech_result':{'tech_project_id':PID,'packaging_package':p}})
        self.assertEqual(1,result['gap_count']);self.assertTrue(result['provisional'])

    def test_snapshot_has_costed_product_row_not_sale_price(self):
        with patch.object(bridge,'_forms',return_value=({},lambda x:x)):
            result=bridge._step2_snapshot({'packaging_package':package()})
        row=result['s2_products']['数据'][0]
        self.assertEqual(9.3,row['基础成本']);self.assertEqual(1000,row['数量'])
        self.assertNotIn('价格',row)
        detail=result['s2_packaging_cost']['数据'][0]
        self.assertEqual('6.00',detail['加工']);self.assertEqual('1.30',detail['其他费用'])
        self.assertNotIn('工装',detail)

    def test_identity_reads_workflow_connection_not_master_data(self):
        conn=MagicMock();cursor=conn.cursor.return_value.__enter__.return_value
        cursor.description=[];cursor.fetchall.return_value=[]
        with patch('cpq_auth._connect',return_value=conn) as wf,patch('cpq_db.connect') as md:
            quote_server._identity_query('SELECT card_id FROM cpq_wf_card')
        wf.assert_called_once();md.assert_not_called()

    def test_refresh_entry_checks_return_and_confirm_saves_snapshot(self):
        html=(ROOT/'确认需求解析结果.html').read_text()
        opened=html.split('async function openSession(id)',1)[1].split('async function deleteSession',1)[0]
        applied=html.split('async function applyTechResult(result, task)',1)[1].split('async function checkTechResultHandoff',1)[0]
        self.assertTrue('checkTechResultHandoff()' in opened,'刷新恢复必须读回成本回传待办')
        self.assertTrue('/wf/card/cost-apply' in applied,'销售确认必须经受权成本接收接口落盘，不止改DOM')

    def test_receive_preserves_sale_price_and_rejects_other_session(self):
        service=importlib.import_module('cpq_cost_receive')
        row=service.merge_product_rows([{'成品编码':'BOX1','价格':20,'利润加成':10}],
            {'成品编码':'BOX1','基础成本':9.3,'数量':1000})[0]
        self.assertEqual(20,row['价格']);self.assertEqual(9.3,row['基础成本']);self.assertEqual(10,row['利润加成'])
        task={'session_id':'OTHER','payload':{'tech_result':{'packaging_package':package()}}}
        with patch('cpq_wf.task_detail',return_value=task):
            with self.assertRaises(bridge.cpq_wf.WfError):service.apply('QUOTE1','T1',{'user_id':1,'role_code':'sales_mgr'})

    def test_actual_transaction_receives_original_quote_and_is_idempotent(self):
        from tests.fixtures import wf_handoff_harness as H
        import cpq_cost_receive,cpq_wf
        with H.Workbench() as wb:
            p=package();p['source']['source_session_id']=H.QUOTE_SESSION
            result=packaging_handoff.bridge_result(p)
            sent=wb.handoff(result=result)
            target=str((sent.get('handoff') or {}).get('task_id') or sent.get('task_id') or '')
            if not target:target=str([t for t in wb.tasks() if str(t['task_id'])!='9001'][0]['task_id'])
            cpq_wf.merge_step_snapshot(H.QUOTE_SESSION,1,{'s1_products':{'数据':[{'成品编码':'BOX1','价格':20,'利润加成':10}]}})
            # 假库把jsonb保留成字符串；只补psycopg解码边界，成本接收/写快照/事务仍跑真代码。
            task=copy.deepcopy([t for t in wb.tasks() if str(t['task_id'])==target][0]);task['session_id']=H.QUOTE_SESSION
            if isinstance(task.get('payload'),str):task['payload']=json.loads(task['payload'])
            with patch('cpq_wf.task_detail',return_value=task):
                received=cpq_cost_receive.apply(H.QUOTE_SESSION,target,wb.user(H.SALES_UID))
                self.assertEqual(9.3,received['products'][0]['基础成本']);self.assertEqual(20,received['products'][0]['价格'])
                self.assertEqual(9300,received['cost']['order_total_cost'])
                self.assertTrue(cpq_cost_receive.apply(H.QUOTE_SESSION,target,wb.user(H.SALES_UID))['already_received'])
