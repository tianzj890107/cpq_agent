"""销售接收已鉴权任务的成本，原子落入原报价；不接受浏览器自报金额。"""
import copy
import cpq_auth
import cpq_wf
from cpq_cost_contract import cost_fields


def merge_product_rows(existing,incoming):
    rows=copy.deepcopy(existing or [])
    matches=[i for i,row in enumerate(rows) if incoming.get('成品编码') and str(row.get('成品编码'))==str(incoming['成品编码'])]
    if not rows:return [copy.deepcopy(incoming)]
    if len(matches)==1:index=matches[0]
    elif len(rows)==1:index=0
    else:raise cpq_wf.WfError('有多个产品行且无法唯一确定成本目标，请核对成品编码')
    # 成本交接绝不覆盖售价、销售利润与加价。
    values={k:v for k,v in incoming.items() if k not in ('价格','利润加成','其他加价') and v is not None and v!=''}
    rows[index].update(values)
    return rows


def apply(session,task_id,user):
    if (user or {}).get('role_code') not in {'sales_mgr'} | set(cpq_wf.ADMIN_ROLES):
        raise cpq_wf.WfError('只有销售经理或管理员能接收报价成本')
    task=cpq_wf.task_detail(task_id,user)
    payload=task.get('payload') or {}
    if task.get('session_id')!=session or payload.get('quote_session_id',session)!=session:
        raise cpq_wf.WfError('回传任务不属于当前报价')
    if task.get('status') not in ('open','claimed','completed'):
        raise cpq_wf.WfError('该成本回传任务已撤销或不可接收')
    result=payload.get('tech_result') or {}
    if not result:raise cpq_wf.WfError('任务没有技术成本结果')
    fields=cost_fields(result)
    if fields['quote_session_id'] and fields['quote_session_id']!=session:
        raise cpq_wf.WfError('成本包来源报价与接收报价不一致')
    fields['quote_session_id']=session
    fields['received_task_id']=str(task_id)
    if fields['unit_cost'] is None or fields['quantity'] is None:
        raise cpq_wf.WfError('回传缺少有效单位成本或报价数量，不能以零代替')
    package=result.get('packaging_package') or (result if result.get('industry')=='packaging' else {})
    req=package.get('requirement') or {};box=package.get('box_type') or {};material=result.get('material') or {}
    row={'成品编码':material.get('number') or box.get('confirmed_box_type') or req.get('box_type') or '',
        '成品描述':material.get('name') or req.get('packaging_product_name') or result.get('product_name') or '',
        '数量':fields['quantity'],'基础成本':fields['unit_cost'],'币种':fields['currency'],
        '成本指纹':fields['cost_fingerprint'],'成本状态':'暂估' if fields['provisional'] else '正式'}
    conn=cpq_auth._connect()
    try:
        with conn.transaction():
            card=cpq_wf._fetch_card(conn,session)
            if not card:raise cpq_wf.WfError('原报价卡片不存在')
            if user.get('role_code') not in cpq_wf.ADMIN_ROLES and str(user.get('user_id')) not in {
                str(card.get('creator_user_id')),str(card.get('current_owner')),str(task.get('target_user_id')),str(task.get('claimed_by_user_id'))}:
                raise cpq_wf.WfError('当前用户不是该报价的销售接收人')
            second=cpq_wf._snapshot_dict(cpq_wf.step_snapshot(session,2,conn=conn))
            latest=second.get('packaging_package') or {}
            if latest and package and cost_fields({'packaging_package':latest})['cost_fingerprint']!=fields['cost_fingerprint']:
                raise cpq_wf.WfError('已有更新的成本回传，请接收最新任务，不覆盖为旧版本')
            saved=cpq_wf._snapshot_dict(cpq_wf.step_snapshot(session,1,conn=conn))
            section=copy.deepcopy(saved.get('s1_products') or {})
            current=section.get('数据') or (second.get('s2_products') or {}).get('数据') or []
            section['数据']=merge_product_rows(current,row)
            previous=saved.get('finance_cost') or {}
            if previous.get('cost_fingerprint')==fields['cost_fingerprint'] and previous.get('received_task_id')==str(task_id) and section==saved.get('s1_products'):
                return {'ok':True,'already_received':True,'cost':fields,'products':section['数据']}
            cpq_wf.merge_step_snapshot(session,1,{'s1_products':section,'finance_cost':fields},conn=conn)
            cpq_wf.merge_step_snapshot(session,2,{'s2_products':section,'finance_cost':fields},conn=conn)
            params=result.get('techparams_row') or result.get('techparams')
            if isinstance(params,dict) and params:
                cpq_wf.merge_step_snapshot(session,1,{'s1_techparams':{'数据':[params]}},conn=conn)
                cpq_wf.merge_step_snapshot(session,2,{'s2_techparams':{'数据':[params]}},conn=conn)
            cpq_wf._log(conn,int(card['card_id']),None,int(user['user_id']),'cost_receive',None,None,
                '接收成本任务 '+str(task_id)+' / '+fields['cost_fingerprint'])
        return {'ok':True,'cost':fields,'products':section['数据']}
    finally:conn.close()
