"""部署后真PG成本接收验证：只使用事务内临时表，强制回滚，不写历史项目。"""
import copy
import json
import pathlib
import sys
from unittest.mock import patch

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import cpq_auth
import cpq_cost_receive
import cpq_wf
from psycopg import sql


def main():
    conn=cpq_auth._connect()
    class Borrowed:
        def __getattr__(self,name):return getattr(conn,name)
        def close(self):pass
    wrapped=Borrowed()
    try:
        with conn.transaction(force_rollback=True):
            for table in ('cpq_wf_card','cpq_wf_card_step','cpq_wf_task','cpq_wf_user','cpq_wf_task_event'):
                conn.execute(sql.SQL('CREATE TEMP TABLE {} AS SELECT * FROM {}.{} WITH NO DATA').format(
                    sql.Identifier(table),sql.Identifier(cpq_auth.WF_SCHEMA),sql.Identifier(table)))
            conn.execute("SELECT set_config('search_path',%s,true)",('pg_temp,'+cpq_auth.WF_SCHEMA+',public',))
            def seed(table,data):
                conn.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),
                    sql.SQL(',').join(map(sql.Identifier,data)),sql.SQL(',').join(sql.Placeholder() for _ in data)),tuple(data.values()))
            sid='isolated-cost-receive';uid=-100
            seed('cpq_wf_user',{'user_id':uid,'username':'isolated-sales','display_name':'验收销售','role_code':'sales_mgr','status':'active'})
            seed('cpq_wf_user',{'user_id':-200,'username':'isolated-finance','display_name':'验收财务','role_code':'finance_mgr','status':'active'})
            seed('cpq_wf_card',{'card_id':-1,'session_id':sid,'assistant_type':'quote','title':'隔离验收','current_step':3,
                'creator_user_id':uid,'current_owner':uid,'overall_status':'in_progress','business_case_id':'isolated-bc','industry':'packaging'})
            for step in (1,2):seed('cpq_wf_card_step',{'card_step_id':-step,'card_id':-1,'step_no':step,'status':'done','data_snapshot':'{}'})
            package={'industry':'packaging','result_version':'v1','source':{'project_id':'isolated-tech','source_session_id':sid,'result_version':'v1'},
                'requirement':{'packaging_product_name':'隔离酒盒','quote_quantity':1000,'currency':'CNY'},'box_type':{'confirmed_box_type':'BOX1'},
                'cost':{'total_cost':9.3,'quote_quantity':1000,'material_total':2,'process_total':6,'labor_total':.3,
                    'packaging_total':1,'other_total':0,'currency':'CNY','cost_profile':'detail_sheet/1','estimate_id':'v1','gaps':[]}}
            seed('cpq_wf_task',{'task_id':-101,'card_id':-1,'from_user_id':-200,'from_step_no':3,'target_type':'user',
                'target_user_id':uid,'status':'open','task_kind':'handoff','payload':json.dumps({'quote_session_id':sid,'tech_result':{'packaging_package':package}})})
            with patch.object(cpq_auth,'_connect',return_value=wrapped),patch.object(cpq_wf,'_new_id',return_value=-900):
                cpq_wf.merge_step_snapshot(sid,1,{'s1_products':{'数据':[{'成品编码':'BOX1','价格':20,'利润加成':10}]}})
                cpq_wf.merge_step_snapshot(sid,2,{'packaging_package':package})
                actor={'user_id':uid,'role_code':'sales_mgr'}
                first=cpq_cost_receive.apply(sid,'-101',actor)
                assert first['products'][0]['基础成本']==9.3 and first['products'][0]['价格']==20
                assert first['cost']['order_total_cost']==9300
                assert cpq_cost_receive.apply(sid,'-101',actor)['already_received']
                restored=cpq_wf.step_snapshot(sid,1)
                assert restored['finance_cost']['unit_cost']==9.3 and restored['s1_products']['数据'][0]['价格']==20
                newer=copy.deepcopy(package);newer['cost']['estimate_id']='v2';newer['source']['result_version']='v2'
                cpq_wf.merge_step_snapshot(sid,2,{'packaging_package':newer})
                try:cpq_cost_receive.apply(sid,'-101',actor)
                except cpq_wf.WfError:pass
                else:raise AssertionError('旧成本覆盖新版本未被拒绝')
                print(json.dumps({'real_pg':'pass','unit_cost':9.3,'quantity':1000,'order_total_cost':9300,
                    'sale_price_preserved':20,'refresh':'pass','idempotency':'pass','old_version_rejected':'pass','production_writes':False},ensure_ascii=False))
        print('临时表事务已强制回滚，未保留业务记录')
    finally:conn.close()


if __name__=='__main__':main()
