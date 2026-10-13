"""Read-only replay of saved expressions at a different order quantity, not a new ledger."""
import json
import math
from . import packaging_formula, packaging_cost


def replay_quantity(cost, quantity):
    quantity=float(quantity)
    if not math.isfinite(quantity) or quantity <= 0:raise ValueError('invalid_quantity')
    old_quantity=float(cost.get('quote_quantity') or 0)
    if old_quantity <= 0:raise ValueError('original_quantity_missing')
    totals={'material':0.,'processing':0.,'labor':0.,'packaging':0.,'tooling':0.,'other':0.}
    rows=[];unavailable=[]
    unbound={str(r.get('content_code') or '') for r in cost.get('gaps_unbound_to_order') or []
             if r.get('binding_status')=='unbound'}
    for item in cost.get('items') or []:
        expression=item.get('expression') or ''
        inputs=json.loads(item.get('inputs_json') or '{}')
        amount=item.get('amount')
        if str(item.get('content_code') or '') in unbound and not item.get('part_code') and amount in (None,0):
            continue
        code=item.get('formula_code') or ''
        category=item.get('cost_category') or ('packaging' if code.startswith('PKG-P-') else '')
        kind=('tooling' if item.get('tooling_code') else
              category if category in totals else 'processing')
        if amount is None:
            unavailable.append({'id':item.get('item_id'),'reason':'amount_missing'});continue
        amount=float(amount)
        new_amount=amount
        if expression:
            try:
                original=packaging_cost._formula_value(expression,inputs)
                usage=float(inputs.get('usage_qty') or 1)
                scale=usage if item.get('part_code') and code.startswith('PKG-C-') else 1.
                if not item.get('min_charge_applied') and not math.isclose(original*scale,amount,rel_tol=1e-7,abs_tol=1e-5):
                    raise ValueError('original_expression_does_not_reconcile')
                changed=dict(inputs)
                if 'quote_quantity' in changed:
                    changed['quote_quantity']=float(changed['quote_quantity']) / old_quantity * quantity
                value=packaging_cost._formula_value(expression,changed)*scale
                # When the saved row hit minimum billing, its original amount freezes that charge.
                if item.get('min_charge_applied'):
                    value=max(value,amount*old_quantity/quantity)
                new_amount=value
            except (ValueError,TypeError,packaging_formula.FormulaError) as exc:
                unavailable.append({'id':item.get('item_id'),'reason':str(exc)});continue
        elif item.get('tooling_code') and amount != 0:
            # Tooling refund/amortisation has separate contracts; cannot guess them from amount.
            unavailable.append({'id':item.get('item_id'),'reason':'tooling_recompute_required'});continue
        elif kind != 'packaging' and amount != 0:
            unavailable.append({'id':item.get('item_id'),'reason':'expression_missing'});continue
        with_loss=packaging_cost.apply_loss(new_amount,item.get('loss_rate'),category=category,
            loss_base_scope=cost.get('loss_base_scope') or packaging_cost.DEFAULT_LOSS_BASE_SCOPE)
        totals[kind]+=with_loss
        rows.append({'part_code':item.get('part_code'),'name':item.get('part_name') or inputs.get('name'),
            'category':kind,'original_amount':amount,'amount':with_loss,'formula_code':code})
    return {'original_quantity':old_quantity,'quote_quantity':quantity,'totals':totals,
        'unit_cost_excluding_freight':sum(totals.values()),
        'order_cost_excluding_freight':sum(totals.values())*quantity,
        'items':rows,'unavailable':unavailable,'complete_excluding_freight':not unavailable,
        'note':'按原明细公式和费率回放，仅调整批量；不含运输，不重选尺寸/工序，不代替正式重算'}
