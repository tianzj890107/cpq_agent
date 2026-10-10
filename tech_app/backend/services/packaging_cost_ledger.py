"""Read-only views of the canonical packaging formula ledger; no second cost store."""
import math

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
    result['others'] = sum(result.get(key, 0) for key in KEYS[2:])
    result['total'] = sum(result.get(key, 0) for key in KEYS)
    # Backwards-compatible API keys, not the packaging UI's expense names.
    result['machining'] = result.get('processing', 0)
    result['overhead'] = sum(result.get(key, 0) for key in KEYS[3:])
    return result


def project(doc, ledger):
    from . import packaging_cost
    parts = (doc or {}).get('business_parts') or []
    ledger = ledger or {}
    built, stale = bool(ledger.get('built')), bool(ledger.get('stale'))
    totals = {key: _number(ledger.get(field, 0)) for key, field in TOTAL_KEYS.items()}
    errors = []
    if any(value is None for value in totals.values()):
        errors.append('invalid_category_total')
    totals = {key: value if value is not None else 0 for key, value in totals.items()}
    final = _breakdown(totals)
    expected = _number(ledger.get('total_cost', 0))
    if expected is None or abs(final['total'] - expected) > .00001 * max(1, expected):
        errors.append('category_total_mismatch')
    final['total'] = expected if expected is not None else 0
    grouped = {}
    codes = {str(row.get('business_part_code')) for row in parts}
    for item in ledger.get('items') or []:
        code = str(item.get('part_code') or '')
        if _number(item.get('amount')) is None:
            errors.append('invalid_amount:' + (code or 'project'))
        if code and code not in codes:
            errors.append('unknown_part:' + code)
        if code in codes:
            grouped.setdefault(code, []).append(item)
    rows = []
    allocated = dict.fromkeys(KEYS, 0.0)
    for part in parts:
        code = str(part.get('business_part_code') or '')
        ref = part.get('reference') or part.get('authority') or {}
        quantity = _number(ref.get('quantity', part.get('quantity', 1)))
        valid_quantity = quantity is not None and quantity > 0
        quantity = quantity if valid_quantity else 1
        values = dict.fromkeys(KEYS, 0.0)
        items = grouped.get(code, [])
        valid = bool(items) and valid_quantity
        for item in items:
            amount = _number(item.get('amount'))
            if amount is None:
                errors.append('invalid_amount:' + code)
                valid = False
                continue
            category = item.get('cost_category')
            amount = packaging_cost.apply_loss(amount, item.get('loss_rate'), category=category,
                loss_base_scope=ledger.get('loss_base_scope') or packaging_cost.DEFAULT_LOSS_BASE_SCOPE)
            if _number(amount) is None:
                errors.append('invalid_loss:' + code)
                valid = False
                continue
            key = ('tooling' if item.get('tooling_code') else
                   category if category in ('material', 'labor', 'packaging', 'freight', 'other')
                   else 'processing')
            values[key] += amount
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
            'unit_cost': subtotal / quantity, 'subtotal': subtotal, 'items': items,
            'summary': '单件与整单来自同一份公式成本明细', 'open_questions': int(not valid)})
    project_values = {key: totals[key] - allocated[key] for key in KEYS}
    if any(value < -.00001 for value in project_values.values()):
        errors.append('part_amount_exceeds_category_total')
    expenses = _breakdown(project_values)
    missing = [row['id'] for row in rows if not row['has_cost']]
    zero = [row['id'] for row in rows if row['has_cost'] and row['subtotal'] <= 0]
    reconciled = not errors
    usable = built and not stale and reconciled
    return {'parts': rows, 'parts_total': _breakdown(allocated), 'final': final,
        'assembly': {'id': '__assembly__', 'name': '包装整单成本', 'kind': 'assembly',
            'has_cost': usable, 'quantity': 1, 'unit_cost': final['total'], 'subtotal': final['total'],
            'breakdown': final if built else {}, 'cost_stale': built and stale,
            'summary': '同一份公式成本账汇总，不叠加单件成本', 'open_questions': len(ledger.get('gaps') or [])},
        'counts': {'parts': len(rows), 'parts_costed': len(rows) - len(missing),
            'missing': missing, 'zero': zero, 'assembly_costed': usable},
        'ready': bool(rows) and usable and not missing and not zero,
        'reconciled': reconciled, 'reconciliation_errors': sorted(set(errors)),
        'project_expenses': expenses, 'packaging_cost': ledger,
        'cost_source': 'packaging_formula_ledger', 'estimate_id': ledger.get('estimate_id')}


def load(project_id):
    from . import packaging_parts, packaging_cost
    return project(packaging_parts.load_business_parts(project_id) or {},
                   packaging_cost.with_current_quote_quantity(
                       packaging_cost.load_cost(project_id) or {}, packaging_cost._requirement_data(project_id)))


def part_view(project_id, code, view=None):
    view = view or load(project_id)
    row = next((row for row in view['parts'] if row['id'] == code), None)
    if row is None:
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
    from . import packaging_cost
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
