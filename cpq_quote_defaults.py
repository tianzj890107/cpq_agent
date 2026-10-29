"""新报价可编辑推荐；不代表客户需求、选用产品或定价依据。"""
from copy import deepcopy

from cpq_industries import normalize


_PROFILES = {
    'semiconductor': ('半导体封装产品（推荐，待确认）', '电子器件封装（推荐，待确认）', '防静电包装运输（推荐）'),
    'battery': ('电池组（推荐，待确认）', '设备配套供电（推荐，待确认）', '电池合规专运（推荐，待确认运输条件）'),
    'appliance': ('电器配套产品（推荐，待确认）', '家用电器配套（推荐，待确认）', '公路运输（推荐）'),
    'packaging': ('定制包装盒（推荐，待确认）', '纸质包装（推荐，待确认）', '公路运输／防潮防压（推荐）'),
}


def with_recommendations(entry, industry):
    """返回独立副本，字段严格投影到该行业已有目录。"""
    result = deepcopy(entry)
    profile = _PROFILES.get(normalize(industry))
    if not profile or result.get('step') != 1:
        return result
    description, application, transport = profile
    defaults = {
        's1_basic': {'测算类型': '新产品报价（推荐）', '币种': 'CNY', '测算状态': '草稿'},
        's1_dest': [{'发运地类型': '工厂（推荐）', '发运国家': '中国（推荐，待确认）',
                     '国家/地区': '中国（推荐，待确认）'}],
        's1_products': [{'成品描述': description, '方案描述': application}],
        's1_techparams': [{'material': application, '应用范围': application}],
        's1_payment': [
            {'阶段名称': '预付款（推荐）', '资金占用天数': 0, '计划收付款比例': 30, '回款维度': '订单', '货前款': '是'},
            {'阶段名称': '发货前尾款（推荐）', '资金占用天数': 0, '计划收付款比例': 70, '回款维度': '订单', '货前款': '是'},
        ],
        's1_logistics': [{'物流计算类型': '按实际询价（推荐）', '物流类型': transport}],
    }
    value = defaults.get(result['section_id'])
    if value is None:
        return result
    columns = result.get('techparams_columns') or result.get('columns') or []
    keys = {f['key'] for f in (result.get('fields') if isinstance(value, dict) else columns)}
    project = lambda row: {k: v for k, v in row.items() if k in keys}
    if isinstance(value, dict):
        result['recommended_values'] = project(value)
    else:
        result['recommended_rows'] = [project(row) for row in value]
    return result
