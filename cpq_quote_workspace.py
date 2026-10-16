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
        result['editable']=False
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


def save_pricing(session,step,inputs,user,*,expected_cost_fingerprint,rule_result=None):
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
            previous=cpq_wf._snapshot_dict(cpq_wf.step_snapshot(session,current_step,conn=conn))
            stored=previous.get('packaging_quote') or {}
            rules=copy.deepcopy(stored.get('rule_pricing'))
            # 首次进入下一步时，本步快照尚无定价；继承最近的已存规则而非退回毛利率模式。
            if rules is None and rule_result is None and step>=4:
                for prior_step in range(current_step-1,2,-1):
                    prior=cpq_wf._snapshot_dict(cpq_wf.step_snapshot(session,prior_step,conn=conn))
                    candidate=prior.get('packaging_quote') or {}
                    if candidate.get('rule_pricing') is not None:
                        stored=candidate
                        rules=copy.deepcopy(candidate['rule_pricing'])
                        break
            if rules and rule_result is None and stored.get('source_cost_fingerprint')!=expected_cost_fingerprint:
                raise cpq_wf.WfError('成本版本已变化，请重新读取系统定价规则')
            if rule_result is not None:
                if step not in (3,4) or not rule_result.get('source',{}).get('table')=='md_clm_material_price_rule':
                    raise cpq_wf.WfError('系统规则来源无效')
                if step==4 and (not rules or stored.get('source_cost_fingerprint')!=expected_cost_fingerprint):
                    source3=cpq_wf._snapshot_dict(cpq_wf.step_snapshot(session,3,conn=conn))
                    stored=source3.get('packaging_quote') or {}
                    rules=copy.deepcopy(stored.get('rule_pricing'))
                    if not rules or stored.get('source_cost_fingerprint')!=expected_cost_fingerprint:
                        raise cpq_wf.WfError('请先按当前成本重新计算系统定价规则')
                rules=rules or {'profit_amount':0,'addon_amount':0,'stages':{}}
                values=list(rule_result.get('product_markup',{}).values())
                from cpq_money import money
                from cpq_cost_contract import number
                if len(values)!=1 or number(values[0]) is None:
                    raise cpq_wf.WfError('包装规则结果必须准确对应当前产品')
                rules['profit_amount' if step==3 else 'addon_amount']=money(values[0])
                if step==3:rules['addon_amount']=0;rules['stages'].pop('4',None)
                rules['stages'][str(step)]=copy.deepcopy(rule_result)
            if rules is not None:
                # 规则金额只来自本服务取库计算或已存快照，客户端不得提交。
                inputs.pop('gross_margin_rate',None);inputs.pop('addons',None)
                inputs.update(markup_rate=0,addons={'other_addon':rules['addon_amount']},rule_pricing=rules)
                if stored.get('source_cost_fingerprint')==expected_cost_fingerprint:
                    for key in ('quote_quantity','tax_rate','discount'):
                        if key in stored:inputs.setdefault(key,copy.deepcopy(stored[key]))
                pricing_mode='markup'
            else:
                if 'gross_margin_rate' not in inputs:
                    raise cpq_wf.WfError('尚未取得已保存的系统定价规则，请在当前报价重新读取系统规则后继续，无需重新回传成本')
                pricing_mode='gross_margin'
            try:result=price(package,pricing_mode=pricing_mode,publish=False,actor=user,**inputs)
            except PricingError as exc:raise cpq_wf.WfError(str(exc)) from exc
            quote=result.get('quote') or result
            quote['source_cost_fingerprint']=cost_fingerprint(package)
            snapshot={'packaging_package':package,'packaging_quote':quote,
                'packaging_cost_fingerprint':cost_fingerprint(package)}
            if rules:
                for stage,value in rules['stages'].items():
                    snapshot['s'+stage+'_markup']={'数据':copy.deepcopy(value.get('markup') or [])}
            if basic is not None:snapshot['s5_basic']={'数据':basic}
            # 回看第3步修改参数时，也保存到当前在办步骤，刷新不会被更晚的旧快照覆盖。
            cpq_wf.merge_step_snapshot(session,current_step,snapshot,conn=conn)
            return {'ok':True,'quote':quote,'data':snapshot}
    finally:conn.close()
