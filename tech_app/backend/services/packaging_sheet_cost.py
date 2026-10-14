"""开料投影与整张材料分摊。复用主成本公式，不创建第二份成本账。"""
import math
import hashlib
import json

RULE_VERSION='sheet_material_process_allocation/2'


def allowance_fingerprint(data):
    return hashlib.sha256(json.dumps({k:data.get(k,default) for k,default in (
        ('cut_margin_length_mm',0),('cut_margin_width_mm',5),('cut_allowances',{}))},
        sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def positive(value):
    try:
        number=float(value)
        return number if math.isfinite(number) and number>0 and not isinstance(value,bool) else None
    except (ValueError,TypeError):
        return None


def individual_cut(part, data):
    length,width=positive(part.get('length_mm')),positive(part.get('width_mm'))
    allowances=data.get('cut_allowances') or {}
    if not isinstance(allowances,dict): raise ValueError('cut_allowance_invalid')
    allowance=allowances.get(part.get('material_code'),{})
    if not isinstance(allowance,dict): raise ValueError('cut_allowance_invalid')
    configured=bool(allowance)
    dl,dw=allowance.get('length_mm',data.get('cut_margin_length_mm',0)),allowance.get('width_mm',data.get('cut_margin_width_mm',5))
    try:
        dl,dw=float(dl),float(dw)
        if not all(math.isfinite(x) and x>=0 for x in (dl,dw)): raise ValueError()
    except (TypeError,ValueError):
        raise ValueError('cut_allowance_invalid') from None
    return {'sheet_mm':[length+dl if length else None,width+dw if width else None],
            'size_source':'configured_allowance' if configured or 'cut_margin_length_mm' in data or 'cut_margin_width_mm' in data else 'legacy_allowance_0_5mm',
            'allowance_mm':[dl,dw]}


def sheet_allocation(layout, parts, quote_quantity, sheet_unit_cost):
    q,cost=positive(quote_quantity),positive(sheet_unit_cost)
    if not q or (cost is None and sheet_unit_cost!=0): raise ValueError('layout_cost_input_invalid')
    size=layout.get('sheet_mm') or []
    if len(size)!=2 or not all(positive(x) for x in size): raise ValueError('layout_sheet_size_missing')
    codes=layout.get('part_codes') or []
    if not codes or len(set(codes))!=len(codes): raise ValueError('layout_part_association_missing')
    indexed={p['part_code']:p for p in parts}
    counts=dict(layout.get('part_counts') or {})
    if len(codes)==1 and not counts: counts={codes[0]:layout.get('n_up')}
    required,areas={},{}
    for code in codes:
        if code not in indexed: raise ValueError('layout_part_association_missing')
        count=positive(counts.get(code))
        if not count or not count.is_integer(): raise ValueError('layout_part_count_missing')
        part=indexed[code]
        usage=positive(part.get('quantity'))
        length,width=positive(part.get('length_mm')),positive(part.get('width_mm'))
        if not usage: raise ValueError('layout_part_usage_missing')
        if not length or not width: raise ValueError('layout_part_area_missing')
        required[code]=q*usage/count
        areas[code]=length*width*usage
    sheets=math.ceil(max(required.values()))
    total=sheets*(cost or 0)/q
    weights={code:area/sum(areas.values()) for code,area in areas.items()}
    allocation={code:total*weights[code] for code in codes}
    allocation[codes[-1]]=total-sum(allocation[c] for c in codes[:-1])
    return {'sheet_quantity':sheets,'total_per_quote_unit':total,'allocations':allocation,
            'allocation_weights':weights,'part_counts':counts,'allocation_rule':'required_unfolded_area/1'}


def apply_layout_costs(layouts, parts, lines, quantity, evaluate, formula_rows):
    """先验证整张参与项，再一次替换材料金额；不再乘用量，损耗由原账链统一处理。"""
    from .packaging_cost import CostError
    material={line['part_code']:line for line in lines if line.get('cost_category')=='material'}
    groups=[row for row in layouts if (row.get('kind')!='drawing_layout_candidate'
             or row.get('size_source')=='cad_cut_sheet_dimension') and row.get('part_codes')]
    owners={}
    for group in groups:
        for code in group['part_codes']: owners.setdefault(code,[]).append(group)
    gaps=[]
    for row in layouts:
        if row.get('kind')!='drawing_layout_candidate' and not row.get('part_codes'):
            gaps.append({'code':'layout_part_association_missing','where':row.get('entity_id'),
                         'detail':'明确排模尚未关联零件，不能用于整张材料账'})
    for group in groups:
        codes=group['part_codes']
        try:
            if any(len(owners[c])>1 for c in codes): raise ValueError('layout_part_multiple_sheets')
            if group.get('association_gaps'): raise ValueError('layout_association_incomplete')
            if group.get('dimension_role') in ('machine_sheet','unclassified'): raise ValueError('layout_cut_size_role_unconfirmed')
            if any(c not in material for c in codes): raise ValueError('layout_part_association_missing')
            if any(sum(line.get('cost_category')=='material' and line.get('part_code')==c for line in lines)!=1
                   for c in codes): raise ValueError('layout_duplicate_material_row')
            entries=[material[c] for c in codes]
            fields=('gsm','ton_price','tax_factor','proof_base','price_unit_status','material_code')
            if any(any(line['inputs'].get(f)!=entries[0]['inputs'].get(f) for f in fields)
                   or line.get('loss_rate')!=entries[0].get('loss_rate') for line in entries[1:]):
                raise ValueError('layout_mixed_material_or_rate')
            bom={p['part_code']:p for p in parts}
            materials={bom[c].get('material_code') or bom[c].get('material') for c in codes}
            if len(materials)>1: raise ValueError('layout_mixed_material_or_rate')
            plan=sheet_allocation(group,parts,quantity,1)
            variables=dict(entries[0]['inputs'],cut_length=group['sheet_mm'][0],cut_width=group['sheet_mm'][1],
                           imposition_count=1,quote_quantity=plan['sheet_quantity'],usage_qty=1)
            if any(not positive(variables.get(f)) for f in ('gsm','ton_price','tax_factor')):
                raise ValueError('layout_material_price_missing')
            if variables.get('price_unit_status')=='unit_mismatch': raise ValueError('layout_material_price_unit_mismatch')
            result=evaluate('material',variables,rows=formula_rows)
            if result.get('amount') is None: raise ValueError('layout_material_formula_missing')
            plan=sheet_allocation(group,parts,quantity,result['amount'])
            for code in codes:
                material[code]['amount']=plan['allocations'][code]
                material[code]['expression']=result['expression']
                material[code]['inputs']=dict(material[code]['inputs'],cut_length=group['sheet_mm'][0],
                    cut_width=group['sheet_mm'][1],layout_id=group['entity_id'],sheet_quantity=plan['sheet_quantity'],
                    sheet_formula_inputs=variables,part_counts=plan['part_counts'],
                    allocation_weight=plan['allocation_weights'][code],allocation_rule=plan['allocation_rule'],
                    sheet_total_per_quote_unit=plan['total_per_quote_unit'],cut_size_source=group.get('size_source') or 'cad_layout_text')
        except (ValueError,KeyError,TypeError,CostError) as exc:
            reason=str(exc) if isinstance(exc,ValueError) else 'layout_material_formula_missing' if isinstance(exc,CostError) else 'layout_cost_input_invalid'
            gaps.append({'code':reason,'where':group.get('entity_id'),'detail':'排模材料成本未计算：'+reason})
            for line in lines:
                if line.get('cost_category')=='material' and line.get('part_code') in codes:
                    line['amount']=None
    return gaps


def apply_layout_process_costs(layouts, parts, lines, quantity, evaluate, formula_rows):
    """同一排模的共同加工按整张计一次，按展开用量面积分摊回整件。

    仅合并每个关联件都存在且参数一致的加工实例；涂胶区域、不同烫金图案
    不凭空认定共版。未成立的共同加工保持逐件计费并披露，绝不漏算。
    """
    supported={'lamination','mounting','die_cutting','v_groove','print_uv'}
    indexed={p['part_code']:p for p in parts}
    gaps=[]
    groups=[g for g in layouts if len(g.get('part_codes') or [])>1 and
            (g.get('kind')!='drawing_layout_candidate' or g.get('size_source')=='cad_cut_sheet_dimension')]
    owners={}
    for group in groups:
        for code in group['part_codes']:owners.setdefault(code,[]).append(group)
    for group in groups:
        codes=group['part_codes']
        if any(len(owners[c])!=1 for c in codes):continue
        for category in supported:
            by_code={c:[r for r in lines if r.get('part_code')==c and r.get('cost_category')==category]
                     for c in codes}
            if not any(by_code.values()):continue
            if not all(by_code.values()):continue  # 本就只有部分件做此工序，不虚构共版。
            try:
                materials={indexed[c].get('material_code') or indexed[c].get('material') for c in codes}
                if None in materials or len(materials)!=1:raise ValueError('layout_mixed_material_or_rate')
                if group.get('association_gaps'):raise ValueError('layout_association_incomplete')
                if group.get('dimension_role') in ('machine_sheet','unclassified'):
                    raise ValueError('layout_cut_size_role_unconfirmed')
                count=len(by_code[codes[0]])
                if any(len(rows)!=count for rows in by_code.values()):raise ValueError('layout_process_instances_differ')
                allocation=sheet_allocation(group,parts,quantity,1)
                staged=[]
                for index in range(count):
                    entries=[by_code[c][index] for c in codes]
                    if any(e.get('amount') is None for e in entries):raise ValueError('layout_process_amount_missing')
                    if len({e.get('formula_code') for e in entries})!=1:raise ValueError('layout_process_formula_differs')
                    from .packaging_cost import expression_variables
                    names=set(expression_variables(entries[0].get('expression') or '')) - {
                        'machine_length','machine_width','imposition_count','quote_quantity'}
                    if any(any(e['inputs'].get(k)!=entries[0]['inputs'].get(k) for k in names)
                           or e.get('loss_rate')!=entries[0].get('loss_rate') for e in entries[1:]):
                        raise ValueError('layout_process_parameters_differ')
                    machine=group.get('machine_mm') or group['sheet_mm']
                    variables=dict(entries[0]['inputs'],machine_length=machine[0],
                        machine_width=machine[1],imposition_count=1,
                        quote_quantity=allocation['sheet_quantity'])
                    result=evaluate(category,variables,formula_code=entries[0]['formula_code'],rows=formula_rows)
                    if result.get('amount') is None:raise ValueError('layout_process_formula_missing')
                    allocated=sheet_allocation(group,parts,quantity,result['amount'])
                    staged.append((entries,result,allocated))
                for entries,result,allocated in staged:
                    for code,line in zip(codes,entries):
                        line['amount']=allocated['allocations'][code]
                        line['expression']=result['expression']
                        line['min_charge_applied']=result.get('min_charge_applied',False)
                        line['inputs']=dict(result['inputs'],layout_id=group['entity_id'],
                            sheet_quantity=allocated['sheet_quantity'],part_counts=allocated['part_counts'],
                            sheet_total_per_quote_unit=allocated['total_per_quote_unit'],
                            allocation_weight=allocated['allocation_weights'][code],
                            allocation_rule='shared_sheet_process_area/1',order_quote_quantity=quantity,
                            machine_size_source='图纸上机尺寸' if group.get('machine_mm') else '开料纸张尺寸作为上机尺寸估算')
            except (ValueError,KeyError,TypeError) as exc:
                gaps.append({'code':'layout_process_allocation_unresolved','where':group.get('entity_id'),
                             'detail':'共同加工未合并，保留逐件金额：'+str(exc)})
    return gaps


def process_sheet_parameters(layouts, code):
    groups=[r for r in layouts if code in (r.get('part_codes') or []) and
            (r.get('kind')!='drawing_layout_candidate' or r.get('size_source')=='cad_cut_sheet_dimension')]
    if len(groups)!=1:return {}
    group=groups[0]
    size=group.get('sheet_mm') or []
    if group.get('association_gaps') or group.get('dimension_role') in ('machine_sheet','unclassified'):
        return {}
    count=(group.get('part_counts') or {}).get(code)
    if count is None and len(group['part_codes'])==1:count=group.get('n_up')
    if len(size)!=2 or not all(positive(x) for x in size) or not positive(count):return {}
    machine=group.get('machine_mm') or size
    if len(machine)!=2 or not all(positive(x) for x in machine):return {}
    return {'machine_length':machine[0],'machine_width':machine[1],'imposition_count':count,
            'machine_size_source':'图纸上机尺寸' if group.get('machine_mm') else '开料纸张尺寸作为上机尺寸估算'}
