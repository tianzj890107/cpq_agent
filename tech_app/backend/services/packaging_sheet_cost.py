"""开料投影与整张材料分摊。复用主成本公式，不创建第二份成本账。"""
import math
import hashlib
import json

RULE_VERSION='sheet_material_allocation/1'


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
