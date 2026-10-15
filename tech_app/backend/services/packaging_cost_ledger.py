"""Read-only views of the canonical packaging formula ledger; no second cost store."""
import math
import json
import hashlib

KEYS = ('material', 'labor', 'processing', 'tooling', 'packaging', 'freight', 'other')
TOTAL_KEYS = dict(zip(KEYS, ('material_total', 'labor_total', 'process_total',
                           'tooling_total', 'packaging_total', 'freight_total', 'other_total')))


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def _breakdown(values):
    result = dict(values)
    result['others'] = sum(result.get(key, 0) for key in KEYS if key not in ('material', 'processing'))
    result['total'] = sum(result.get(key, 0) for key in KEYS)
    # Backwards-compatible API keys, not the packaging UI's expense names.
    result['machining'] = result.get('processing', 0)
    result['overhead'] = sum(result.get(key, 0) for key in KEYS[3:])
    return result


#: 多组成件的明细行按 `<父件号>::<section_id>` 发号（`packaging_sections.section_input_rows()`）。
#: 对账读侧只认父件号：子件号是同一件的一个物理组成部分，不是"另一件"。
SECTION_SEPARATOR = '::'


def _owner_part_code(code, codes, item):
    """把明细行的 part_code 归回业务部件清单里的那一件；归不回去返回空串。

    优先认行上显式的 `parent_part_code`，其次按分隔符取 `::` 前段；两者都要落在清单里
    才算归属成功 —— 父件都不在清单里时仍然按"未知零件"报出，守卫不放宽。
    """
    if code in codes:
        return code
    parent = str((item or {}).get('parent_part_code') or '')
    if parent and parent in codes:
        return parent
    head = code.split(SECTION_SEPARATOR, 1)[0] if SECTION_SEPARATOR in code else ''
    return head if head and head in codes else ''


def _calculation_evidence(item, amount_with_loss, quantity):
    """只投影同一公式账的依据，不执行公式、不查新的价格。"""
    from .packaging_cost import COST_CATEGORIES, LINE_VARIABLES
    raw = item.get('inputs_json')
    try:
        inputs = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        if not isinstance(inputs, dict):
            raise ValueError('inputs_not_object')
        inputs_error = ''
    except (TypeError, ValueError):
        inputs, inputs_error = {}, '历史输入参数无法读取'
    detail = {key: item.get(key) for key in (
        'seq', 'part_code', 'part_name', 'cost_category', 'formula_code', 'formula_version',
        'expression', 'amount', 'loss_rate', 'source_ref', 'source', 'note',
        'rate_code', 'content_code', 'unit', 'unit_price', 'quantity_basis')}
    detail.update(category='material' if item.get('cost_category') == 'material' else 'processing',
                  category_label=dict(COST_CATEGORIES).get(item.get('cost_category'), item.get('cost_category') or '费用'),
                  display_labels={key: LINE_VARIABLES[key] for key in inputs if key in LINE_VARIABLES},
                  inputs=inputs, inputs_error=inputs_error,
                  amount_with_loss=amount_with_loss, quantity_divisor=quantity,
                  unit_amount=amount_with_loss / quantity if amount_with_loss is not None else None,
                  min_charge_applied=bool(item.get('min_charge_applied')),
                  assumptions=item.get('assumptions') or [])
    return detail


def project(doc, ledger, *, include_details=True, detail_part_id=None):
    from . import packaging_cost
    from . import packaging_layout
    scheme_error = ''
    try:
        doc = packaging_layout.active_business_document(doc)
    except ValueError:
        scheme_error = 'packaging_scheme_selection_required'
        doc = dict(doc or {}, business_parts=[])
    parts = (doc or {}).get('business_parts') or []
    ledger = ledger or {}
    policy = (ledger.get('source_versions') or {}).get('expense_policy') or 'legacy'
    current_policy = policy == packaging_cost.EXPENSE_POLICY
    built, stale = bool(ledger.get('built')), bool(ledger.get('stale'))
    totals = {key: _number(ledger.get(field, 0)) for key, field in TOTAL_KEYS.items()}
    errors = []
    if scheme_error:
        errors.append(scheme_error)
    if any(value is None for value in totals.values()):
        errors.append('invalid_category_total')
    totals = {key: value if value is not None else 0 for key, value in totals.items()}
    final = _breakdown(totals)
    expected = _number(ledger.get('total_cost', 0))
    if expected is None or abs(final['total'] - expected) > .00001 * max(1, expected):
        errors.append('category_total_mismatch')
    final['total'] = expected if expected is not None else 0
    grouped = {}
    unused_contents={str(row.get('content_code') or '') for row in ledger.get('gaps_unbound_to_order') or []
        if row.get('binding_status')=='unbound' and str(row.get('code') or '').startswith('content_formula_error:')}
    codes = {str(row.get('business_part_code')) for row in parts}
    for item in ledger.get('items') or []:
        code = str(item.get('part_code') or '')
        if (not code and item.get('cost_category')=='packaging' and item.get('amount') is None
                and str(item.get('content_code') or '') in unused_contents):
            continue  # 明确未绑定本单的包材缺口只披露，与主成本门禁保持一致。
        if _number(item.get('amount')) is None:
            errors.append('invalid_amount:' + (code or 'project'))
        owner = _owner_part_code(code, codes, item) if code else ''
        try:inputs=json.loads(item.get('inputs_json') or '{}')
        except (TypeError,ValueError):inputs={}
        related=inputs.get('part_codes') or []
        project_operation=(code=='ASSY' and inputs.get('scope')=='project' and isinstance(related,list)
                           and len(related)>=2 and len(related)==len(set(related)) and set(related)<=codes)
        if code and not owner and not project_operation:
            errors.append('unknown_part:' + code)
        # Keep source bindings, but only material/manufacturing belongs to a part subtotal.
        excluded = ('packaging', 'freight', 'other') if current_policy else ('labor', 'packaging', 'freight', 'other')
        if owner and item.get('cost_category') not in excluded and not item.get('tooling_code'):
            grouped.setdefault(owner, []).append(item)
    rows = []
    allocated = dict.fromkeys(KEYS, 0.0)
    for part in parts:
        code = str(part.get('business_part_code') or '')
        want_detail = include_details and (detail_part_id is None or code == detail_part_id)
        ref = part.get('reference') or part.get('authority') or {}
        quantity = _number(ref.get('quantity', part.get('quantity', 1)))
        if quantity is None and doc.get('poc_demo') is True and (part.get('poc_inputs') or {}).get('mock') is True:
            quantity=_number((part.get('poc_inputs') or {}).get('quantity'))
        valid_quantity = quantity is not None and quantity > 0
        quantity = quantity if valid_quantity else 1
        values = dict.fromkeys(KEYS, 0.0)
        items = grouped.get(code, [])
        calculation_details = []
        valid = bool(items) and valid_quantity
        for item in items:
            amount = _number(item.get('amount'))
            if amount is None:
                if want_detail:
                    calculation_details.append(_calculation_evidence(item, None, quantity))
                errors.append('invalid_amount:' + code)
                valid = False
                continue
            category = item.get('cost_category')
            amount = packaging_cost.apply_loss(amount, item.get('loss_rate'), category=category,
                loss_base_scope=ledger.get('loss_base_scope') or packaging_cost.DEFAULT_LOSS_BASE_SCOPE)
            if _number(amount) is None:
                if want_detail:
                    calculation_details.append(_calculation_evidence(item, None, quantity))
                errors.append('invalid_loss:' + code)
                valid = False
                continue
            key = ('tooling' if item.get('tooling_code') else
                   'processing' if current_policy and category == 'labor' else
                   category if category in ('material', 'labor', 'packaging', 'freight', 'other')
                   else 'processing')
            values[key] += amount
            if want_detail:
                calculation_details.append(_calculation_evidence(item, amount, quantity))
        if not valid_quantity:
            errors.append('invalid_usage:' + code)
        for key in KEYS:
            allocated[key] += values[key]
        subtotal = sum(values.values())
        rows.append({'id': code, 'name': part.get('name') or code, 'kind': 'part',
            'quantity': quantity, 'has_cost': built and not stale and valid,
            'cost_stale': built and stale, 'cost_scope': 'canonical_ledger',
            'estimate_id': ledger.get('estimate_id'), 'item_count': len(items),
            'breakdown': _breakdown({key: value / quantity for key, value in values.items()}),
            'unit_cost': subtotal / quantity, 'subtotal': subtotal,
            **({'items': items, 'calculation_details': calculation_details} if want_detail else {}),
            'summary': '单件与整单来自同一份公式成本明细', 'open_questions': int(not valid)})
    project_values = {key: totals[key] - allocated[key] for key in KEYS}
    if any(value < -.00001 for value in project_values.values()):
        errors.append('part_amount_exceeds_category_total')
    expenses = _breakdown(project_values)
    audit_items=[]
    for item in ledger.get('items') or []:
        try:inputs=json.loads(item.get('inputs_json') or '{}')
        except (TypeError,ValueError):inputs={}
        audit_items.append({'part_code':item.get('part_code'),'name':item.get('part_name') or inputs.get('name'),
            'amount':item.get('amount'),'category':item.get('cost_category'),
            'formula_code':item.get('formula_code'),'min_charge_applied':bool(item.get('min_charge_applied')),
            'inputs':{k:inputs.get(k) for k in ('quote_quantity','usage_qty','cut_length','cut_width',
                'machine_length','machine_width','imposition_count','price_unit','gsm','ton_price',
                'labor_seconds','labor_rate') if k in inputs}})
    audit_items.sort(key=lambda r:_number(r.get('amount')) or 0,reverse=True)
    missing = [row['id'] for row in rows if not row['has_cost']]
    zero = [row['id'] for row in rows if row['has_cost'] and row['subtotal'] <= 0]
    reconciled = not errors
    usable = built and not stale and reconciled
    # estimate_id is upserted in place. Pin details to the actual saved rows, not just that ID.
    display_version = hashlib.sha256(json.dumps(
        {'estimate_id':ledger.get('estimate_id'), 'computed_at':ledger.get('computed_at'),
         'total_cost':ledger.get('total_cost'), 'items':ledger.get('items') or [],
         'parts':[{key:row.get(key) for key in ('id','quantity','unit_cost','subtotal')} for row in rows]},
        ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return {'parts': rows, 'parts_total': _breakdown(allocated), 'final': final,
        'display_version': display_version,
        'unit_cost':final['total'], 'quote_quantity':_number(ledger.get('quote_quantity')),
        'order_total_cost':final['total'] * _number(ledger.get('quote_quantity')) if _number(ledger.get('quote_quantity')) else None,
        'cost_unit':'元/只', 'expense_policy':policy,
        'cost_audit':{'largest_items':audit_items[:10],
            'minimum_charge_rows':sum(r['min_charge_applied'] for r in audit_items),
            'quantity_basis':'报价数量只用于订单批量分摊；零件用量只表示每盒数量'},
        'assembly': {'id': '__assembly__', 'name': '包装整单成本', 'kind': 'assembly',
            'has_cost': usable, 'quantity': 1, 'unit_cost': final['total'], 'subtotal': final['total'],
            'breakdown': final if built else {}, 'cost_stale': built and stale,
            'summary': '材料 + 工艺加工费 + 其他费用（组装人工、包装及其它项目费用）；同一份账汇总，不重复计费' if current_policy else '历史成本口径，需重算后使用当前费用分类', 'open_questions': len(ledger.get('gaps') or [])},
        'counts': {'parts': len(rows), 'parts_costed': len(rows) - len(missing),
            'missing': missing, 'zero': zero, 'assembly_costed': usable},
        'ready': bool(rows) and usable and not missing and not zero,
        'reconciled': reconciled, 'reconciliation_errors': sorted(set(errors)),
        'project_expenses': expenses, 'packaging_cost': ledger if include_details and detail_part_id is None
            else {key: value for key, value in ledger.items() if key != 'items'},
        'cost_source': 'packaging_formula_ledger', 'estimate_id': ledger.get('estimate_id')}


def load(project_id, *, include_details=True, detail_part_id=None):
    from . import packaging_parts, packaging_cost
    return project(packaging_parts.load_business_parts(project_id) or {},
                   packaging_cost.with_current_quote_quantity(
                       packaging_cost.load_cost(project_id) or {}, packaging_cost._requirement_data(project_id)),
                   include_details=include_details, detail_part_id=detail_part_id)


def part_view(project_id, code, view=None):
    view = view or load(project_id)
    row = next((row for row in view['parts'] if row['id'] == code), None)
    if row is None:
        from . import packaging_parts, packaging_layout, packaging_cost
        doc = packaging_parts.load_business_parts(project_id) or {}
        try:
            active = packaging_layout.active_business_document(doc)
        except ValueError as exc:
            raise packaging_cost.CostError(str(exc),409,'packaging_scheme_selection_required') from exc
        if any(r.get('business_part_code') == code for r in doc.get('business_parts') or []) \
                and not any(r.get('business_part_code') == code for r in active.get('business_parts') or []):
            raise packaging_cost.CostError('此零件属于未采用方案，不计入当前整单成本',409,'packaging_part_inactive_scheme')
        raise ValueError('packaging_business_part_not_found')
    ledger = view['packaging_cost']
    analysis = None
    if row['has_cost'] and view['reconciled']:
        from . import packaging_cost
        analysis = {'part_id': code, 'part_name': row['name'], 'quantity': ledger.get('quote_quantity'),
            'summary': row['summary'], 'items': [], 'open_questions': []}
        for item in row['items']:
            amount = packaging_cost.apply_loss(item['amount'], item.get('loss_rate'),
                category=item.get('cost_category'),
                loss_base_scope=ledger.get('loss_base_scope') or packaging_cost.DEFAULT_LOSS_BASE_SCOPE) / row['quantity']
            analysis['items'].append({'category': item.get('cost_category'),
                'name': item.get('part_name') or item.get('cost_category'), 'quantity': 1,
                'unit': '件', 'unit_price': amount, 'amount': amount,
                'basis': item.get('expression') or '', 'source': item.get('source_ref') or '',
                'formula_code': item.get('formula_code'), 'formula_version': item.get('formula_version')})
    from . import cost
    return {'part_code': code, 'analysis': analysis,
        'summary': cost.compute(analysis) if analysis else None,
        'parts_id': '', 'stale_reason': ','.join(ledger.get('stale_reasons') or []) if ledger.get('stale') else '',
        'business_stale_reason': 'business_parts_reimported' if 'business_parts_reimported' in (ledger.get('stale_reasons') or []) else '',
        'size_source': 'canonical_ledger', 'size_source_ref': view['estimate_id'] or '',
        'size_text': '', 'geometry': '',
        'stale': bool(ledger.get('stale')), 'business_stale': 'business_parts_reimported' in (ledger.get('stale_reasons') or []),
        'business_parts_id': ledger.get('business_parts_id')
            or (ledger.get('source_versions') or {}).get('business_parts_id') or '',
        'business_parts_hash': ledger.get('business_parts_hash')
            or (ledger.get('source_versions') or {}).get('business_parts_hash') or '',
        'business_part_code': code, 'estimate_id': view['estimate_id'],
        'source_versions': ledger.get('source_versions') or {},
        'quote_quantity': ledger.get('quote_quantity'), 'scenario_code': ledger.get('scenario_code'),
        'source': {'estimate_id': view['estimate_id'], 'computed_at': ledger.get('computed_at'),
                   'cost_scope': 'canonical_ledger'}, 'lookup': {},
        'reconciliation_errors': view['reconciliation_errors']}


def rebuild_part(project_id, code, actor):
    from . import packaging_cost, packaging_parts, packaging_layout
    doc = packaging_parts.load_business_parts(project_id) or {}
    try:
        active = packaging_layout.active_business_document(doc)
    except ValueError as exc:
        raise packaging_cost.CostError(str(exc),409,'packaging_scheme_selection_required') from exc
    if len(doc.get('scheme_labels') or []) > 1 and not any(
            r.get('business_part_code') == code for r in active.get('business_parts') or []):
        raise packaging_cost.CostError('此零件属于未采用方案，不计入当前整单成本',409,'packaging_part_inactive_scheme')
    packaging_cost.build_cost(project_id, actor=actor)
    return part_view(project_id, code)


def business_code_for_geometry(project_id, geometry):
    from . import packaging_parts
    doc = packaging_parts.load_business_parts(project_id) or {}
    refs = set(packaging_parts._component_refs(geometry))
    entity_ids = set(geometry.get('entity_ids') or [])
    hits = []
    for row in doc.get('business_parts') or []:
        binding = row.get('geometry_binding') or {}
        if refs.intersection(packaging_parts._business_row_refs(row)) or (
            entity_ids and entity_ids.issubset(set(binding.get('entity_ids') or []))):
            hits.append(row['business_part_code'])
    if len(hits) != 1:
        raise ValueError('geometry_cost_requires_unique_business_part')
    return hits[0]
