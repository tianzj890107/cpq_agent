"""报价共用工作区：字段投影与显式草稿保存；不在读取时定价。"""
import copy
import cpq_auth
import cpq_wf
from cpq_cost_contract import cost_fields

LEGACY_SECTIONS={'s2_packaging','s2_packaging_cost'}
PRODUCT_FIELDS=('材料费用','加工费用','其他费用','整单成本','币种','成本状态','成本结果版本','定价状态','工艺报告编号','工艺报告版本')
MARGIN_FIELDS=('定价方式','毛利率（%）','基础成本','利润加成','定价后单价')
ADDON_FIELDS=('加价编码','加价项目','金额（元/只）')


def catalog_entry(entry,industry):
    if entry['section_id'] in LEGACY_SECTIONS:return None
    if industry!='packaging':return entry
    result=copy.deepcopy(entry);sid=result['section_id']
    if sid in ('s1_products','s2_products','s3_products','s4_products','s5_detail'):
        keys={c['key'] for c in result['columns']}
        result['columns'] += [{'key':k,'label':k,'readonly':True} for k in PRODUCT_FIELDS if k not in keys]
    elif sid in ('s3_markup','s4_markup'):
        names=MARGIN_FIELDS if sid=='s3_markup' else ADDON_FIELDS
        result.update(editable=True,columns=[{'key':k,'label':k,'readonly':k not in ('毛利率（%）','金额（元/只）')} for k in names])
    return result


def cost_fingerprint(package):return cost_fields({'packaging_package':package})['cost_fingerprint']


def read_snapshot(session,step):
    """历史定价保真，成本按最近回传投影；不计算、不写快照。"""
    conn=cpq_auth._connect()
    try:
        snapshot=copy.deepcopy(cpq_wf.step_snapshot(session,step,conn=conn))
        if 3<=int(step or 0)<=6:
            source=cpq_wf.step_snapshot(session,2,conn=conn) or {}
            if (source.get('packaging_package') or {}).get('industry')=='packaging':
                snapshot=dict(snapshot or {},packaging_package=source['packaging_package'])
        if isinstance(snapshot,dict) and snapshot.get('packaging_package'):
            snapshot['packaging_cost_fingerprint']=cost_fingerprint(snapshot['packaging_package'])
        return snapshot
    finally:conn.close()


def product_cost_fields(package):
    f=cost_fields({'packaging_package':package})
    return {'基础成本':f['unit_cost'],'材料费用':f['material_cost'],'加工费用':f['processing_cost'],
        '其他费用':f['other_cost'],'整单成本':f['order_total_cost'],'币种':f['currency'],
        '成本状态':'暂估' if f['provisional'] else '正式','成本结果版本':f['cost_result_version']}


def technical_fields(package):
    row=copy.deepcopy(package.get('requirement') or {})
    row.update({k:v for k,v in (package.get('params') or {}).items() if v is not None and v!=''})
    box=package.get('box_type') or {}
    row['box_type_code']=box.get('confirmed_box_type') or row.get('box_type') or ''
    row['name']=row.get('packaging_product_name') or row.get('title') or ''
    return row


def save_pricing(session,step,inputs,user,*,expected_cost_fingerprint):
    """仅显式动作写草稿；从已存交接包重算，拒绝浏览器自报成本。"""
    from cpq_packaging_quote import price,PricingError
    try:step=int(step or 0)
    except (ValueError,TypeError) as exc:raise cpq_wf.WfError('步骤号无效') from exc
    if step not in (3,4,5) or not user or (user.get('role_code') not in cpq_wf.ADMIN_ROLES and not cpq_wf.can_do_step(user,step)):
        raise cpq_wf.WfError('只有本步骤销售人员可以保存定价')
    allowed={'gross_margin_rate','addons','discount','tax_rate','quote_quantity','basic_fields'}
    if not isinstance(inputs,dict) or set(inputs)-allowed:
        raise cpq_wf.WfError('定价参数无效，不接受客户端成本或售价')
    inputs=copy.deepcopy(inputs)
    basic=inputs.pop('basic_fields',None)
    if basic is not None:
        from cpq_agent_server import FIXED_FORMS
        keys={f['key'] for f in FIXED_FORMS['s5_basic']['fields']}
        if step!=5 or not isinstance(basic,dict) or set(basic)-keys:
            raise cpq_wf.WfError('报价基本信息字段无效')
    conn=cpq_auth._connect()
    try:
        with conn.transaction():
            cpq_auth._exec(conn,'SELECT card_id FROM cpq_wf_card WHERE session_id=%s FOR UPDATE',(session,))
            card=cpq_wf._fetch_card(conn,session)
            if not card or card.get('industry')!='packaging':raise cpq_wf.WfError('不是可定价的包装报价')
            if (user or {}).get('role_code') not in cpq_wf.ADMIN_ROLES and str(user.get('user_id')) not in {str(card.get('creator_user_id')),str(card.get('current_owner'))}:
                raise cpq_wf.WfError('不能修改其他销售的报价')
            current_step=int(card.get('current_step') or 0)
            if current_step not in (3,4,5) or step>current_step or card.get('overall_status') in ('completed','awaiting_handoff'):
                raise cpq_wf.WfError('当前流程状态不可保存该步骤定价')
            cpq_auth._exec(conn,'SELECT card_step_id FROM cpq_wf_card_step WHERE card_id=%s AND step_no=2 FOR UPDATE',(card['card_id'],))
            source=cpq_wf._snapshot_dict(cpq_wf.step_snapshot(session,2,conn=conn))
            package=source.get('packaging_package') or {}
            if not package or not expected_cost_fingerprint or cost_fingerprint(package)!=expected_cost_fingerprint:
                raise cpq_wf.WfError('成本版本已变化，请重新读取回传成本后重算')
            try:result=price(package,pricing_mode='gross_margin',publish=False,**inputs)
            except PricingError as exc:raise cpq_wf.WfError(str(exc)) from exc
            quote=result.get('quote') or result
            quote['source_cost_fingerprint']=cost_fingerprint(package)
            snapshot={'packaging_package':package,'packaging_quote':quote,
                'packaging_cost_fingerprint':cost_fingerprint(package)}
            if basic is not None:snapshot['s5_basic']={'数据':basic}
            # 回看第3步修改参数时，也保存到当前在办步骤，刷新不会被更晚的旧快照覆盖。
            cpq_wf.merge_step_snapshot(session,current_step,snapshot,conn=conn)
            return {'ok':True,'quote':quote,'data':snapshot}
    finally:conn.close()
