"""销售报价单按业务父件组织，不把材料组成当成新零件。"""
def parent_code(row):
    origin=row.get('size_source_json') or {}
    if isinstance(origin,str):
        import json
        try:origin=json.loads(origin)
        except (ValueError,TypeError):origin={}
    if not isinstance(origin,dict):origin={}
    return str(row.get('parent_part_code') or origin.get('parent_part_code') or row.get('part_code')
               or row.get('business_part_code') or row.get('item_key') or '').split('::')[0]


def part_rows(items, business_parts=None):
    groups={parent_code(p):{'name':p.get('name') or p.get('part_name') or parent_code(p),'materials':[]}
            for p in business_parts or [] if parent_code(p)}
    for item in items:
        category=str(item.get('bom_category') or '')
        if category in ('packaging','freight','tooling','consumable','process'):continue
        code=parent_code(item)
        if not code:continue
        if business_parts is not None and code not in groups:continue
        group=groups.setdefault(code,{'name':item.get('parent_part_name') or item.get('item_name') or item.get('part_name') or code,'materials':[]})
        material=str(item.get('material') or item.get('material_text') or item.get('component') or '')
        value=material+(' × '+str(item['quantity']) if item.get('quantity') is not None else '')
        if '::' in str(item.get('item_key') or item.get('part_code') or ''):
            value=str(item.get('item_name') or item.get('part_name') or item.get('item_key'))+'：'+value
        if value and value not in group['materials']:group['materials'].append(value)
    return [{'项目':f"{code} · {g['name']}",'值':'；'.join(g['materials']) or '材料未提供','来源':'业务部件与材料组成'} for code,g in groups.items()]


def process_rows(steps, parts):
    names={parent_code(p):p.get('name') or p.get('parent_part_name') or p.get('item_name') or p.get('part_name') or parent_code(p) for p in parts}
    groups={}
    for step in steps:
        codes=step.get('part_codes') or []
        code=parent_code(step)
        group='跨件组装' if len(codes)>1 else (code or (str(codes[0]).split('::')[0] if codes else '整件工艺'))
        groups.setdefault(group,[]).append(step)
    return [{'项目':f"{code} · {names.get(code,'')}".strip(' ·'),'值':'\n'.join(
        f"{i}. {s.get('step_name') or s.get('name') or '工序名称待补'}"
        +('（关联：'+ '、'.join(names.get(c,c) for c in s['part_codes'])+'）' if s.get('part_codes') else '')
        +f" · 设备 {s.get('equipment') or '—'} · 标准工时 {s.get('standard_seconds') if s.get('standard_seconds') is not None else '—'} 秒"
        for i,s in enumerate(rows,1)),'来源':'工艺路线（按业务部件分组）'} for code,rows in groups.items()]
