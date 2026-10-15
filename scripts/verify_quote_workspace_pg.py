"""部署后验证共用报价字段的草稿保存：只写事务临时表，结束强制回滚。"""
import json
import pathlib
import sys
from unittest.mock import patch

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import cpq_auth
import cpq_wf
import cpq_quote_workspace as workspace
from psycopg import sql


def main():
    conn=cpq_auth._connect()
    class Borrowed:
        def __getattr__(self,name):return getattr(conn,name)
        def close(self):pass
    try:
        with conn.transaction(force_rollback=True):
            for table in ('cpq_wf_card','cpq_wf_card_step','cpq_wf_quote_version'):
                conn.execute(sql.SQL('CREATE TEMP TABLE {} AS SELECT * FROM {}.{} WITH NO DATA').format(
                    sql.Identifier(table),sql.Identifier(cpq_auth.WF_SCHEMA),sql.Identifier(table)))
            conn.execute("SELECT set_config('search_path',%s,true)",('pg_temp,'+cpq_auth.WF_SCHEMA+',public',))
            session='isolated-industry-fields'
            conn.execute("INSERT INTO cpq_wf_card(card_id,session_id,industry,current_step,creator_user_id,current_owner,overall_status) VALUES (-1,%s,'packaging',3,-101,-101,'in_progress')",(session,))
            for step in (2,3,4,5):
                conn.execute("INSERT INTO cpq_wf_card_step(card_step_id,card_id,step_no,status,data_snapshot) VALUES (%s,-1,%s,'pending','{}')",(-step,step))
            package={'industry':'packaging','source':{'source_session_id':session,'result_version':'v1'},
                'requirement':{'quote_quantity':1000,'packaging_product_name':'隔离验收样例','inner_length':220.5,'inner_width':90,'inner_height':90},
                'box_type':{'confirmed_box_type':'ISOLATED'},'cost':{'total_cost':10,'quote_quantity':1000,'currency':'CNY',
                    'material_total':6,'process_total':3,'labor_total':.3,'packaging_total':.7,'other_total':0,'cost_profile':'detail_sheet/1','gaps':[]}}
            user={'user_id':-101,'role_code':'sales_mgr'}
            with patch.object(cpq_auth,'_connect',return_value=Borrowed()):
                cpq_wf.merge_step_snapshot(session,2,{'packaging_package':package})
                cpq_wf.merge_step_snapshot(session,3,{'preserved_field':'保留原快照'})
                fingerprint=workspace.cost_fingerprint(package)
                result=workspace.save_pricing(session,3,{'gross_margin_rate':.25,'addons':{'freight':0}},user,expected_cost_fingerprint=fingerprint)
                assert abs(result['quote']['margin_price']-10/.75)<1e-9
                restored=cpq_wf.step_snapshot(session,3)
                assert restored['preserved_field']=='保留原快照' and restored['packaging_quote']==result['quote']
                assert cpq_wf.get_card(session)['current_step']==3
                assert conn.execute('SELECT count(*) FROM cpq_wf_card_step WHERE status!=%s',('pending',)).fetchone()[0]==0
                for actor,fp in (({'user_id':-202,'role_code':'sales_mgr'},fingerprint),(user,'old-cost')):
                    try:workspace.save_pricing(session,3,{'gross_margin_rate':.3},actor,expected_cost_fingerprint=fp)
                    except cpq_wf.WfError:pass
                    else:raise AssertionError('foreign owner / stale cost accepted')
                assert cpq_wf.step_snapshot(session,3)==restored
                conn.execute('UPDATE cpq_wf_card SET current_step=5 WHERE card_id=-1')
                workspace.save_pricing(session,3,{'gross_margin_rate':.3},user,expected_cost_fingerprint=fingerprint)
                assert cpq_wf.step_snapshot(session,5)['packaging_quote']['gross_margin_rate']==.3
                last=workspace.save_pricing(session,5,{'gross_margin_rate':0,'addons':{'freight':0},'tax_rate':0,
                    'discount':{'code':'discount','rate':.1},'basic_fields':{'币种':'CNY'}},user,expected_cost_fingerprint=fingerprint)
                assert last['quote']['untaxed_total']==9000 and last['quote']['taxed_total']==9000
                assert cpq_wf.step_snapshot(session,5)['s5_basic']['数据']['币种']=='CNY'
                assert conn.execute('SELECT count(*) FROM cpq_wf_quote_version').fetchone()[0]==0
                changed=json.loads(json.dumps(package));changed['cost']['total_cost']=12;changed['source']['result_version']='v2'
                cpq_wf.merge_step_snapshot(session,2,{'packaging_package':changed})
                view=workspace.read_snapshot(session,5)
                assert view['packaging_package']['cost']['total_cost']==12 and view['packaging_quote']['cost_total']==10
                assert cpq_wf.step_snapshot(session,5)['packaging_package']['cost']['total_cost']==10
                conn.execute("UPDATE cpq_wf_card SET current_step=6,overall_status='completed' WHERE card_id=-1")
                try:workspace.save_pricing(session,5,{'gross_margin_rate':.3},user,expected_cost_fingerprint=fingerprint)
                except cpq_wf.WfError:pass
                else:raise AssertionError('completed quote overwritten')
                print(json.dumps({'real_pg':'PASS','gross_margin':'PASS','refresh_readback':'PASS','zero_fee_and_tax':'PASS',
                    'basic_fields':'PASS','latest_cost_read_projection':'PASS','stale_cost_rejected':'PASS','foreign_owner_rejected':'PASS',
                    'save_current_draft':'PASS','no_step_progression':True,'no_formal_version_created':True,'production_writes':False},ensure_ascii=False))
        print('临时表事务已强制回滚，未保留业务记录')
    finally:conn.close()


if __name__=='__main__':main()
