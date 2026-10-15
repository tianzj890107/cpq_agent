"""Read-only cost display: saved ledger first, authority checked separately."""
from fastapi import HTTPException
import math

from ..storage import da_repo, store
from . import cost_review, packaging_cost, packaging_cost_ledger, packaging_parts, requirement_service


def saved(project_id):
    if not store.load_meta(project_id):
        raise HTTPException(404, '项目不存在')
    requirement = store.load_requirement(project_id) or {}
    data = requirement.get('data') or {}
    if data.get('industry') != 'packaging':
        return None  # Other industries keep their existing source and rules.
    data = requirement_service.resolved_packaging_order_data(data)
    doc = packaging_parts.load_business_parts(project_id) or {}
    row = da_repo.load_packaging_cost(project_id, requirement.get('requirement_no') or '')
    ledger = (packaging_cost._rehydrate(row, da_repo.load_packaging_cost_items(row['estimate_id']))
              if row else {'built': False, 'items': []})
    if row:
        ledger['source_versions'] = packaging_cost._loads(row.get('source_versions_json'), {})
    view = packaging_cost_ledger.project(doc, ledger, include_details=False)
    def quantity(value):
        try:
            number = float(value)
            return number if math.isfinite(number) and number > 0 else None
        except (TypeError, ValueError):
            return None
    quote_quantity = quantity(data.get('quote_quantity'))
    cost_quantity = quantity(ledger.get('quote_quantity'))
    return dict(view, review=cost_review.load_review(project_id).model_dump(),
                verification_pending=True, ready=False, quantity=quote_quantity,
                quote_quantity=quote_quantity, cost_quote_quantity=cost_quantity,
                quantity_mismatch=bool(quote_quantity and cost_quantity and
                                       quote_quantity != cost_quantity),
                assembly_usage=1, done_kinds=[])


def detail(project_id, part_id, estimate_id, display_version=''):
    view = packaging_cost_ledger.load(project_id, detail_part_id=part_id)
    if (not estimate_id or str(view.get('estimate_id') or '') != estimate_id
            or not display_version or view.get('display_version') != display_version):
        raise HTTPException(409, '成本版本已变化，请刷新成本表后重试')
    row = next((r for r in view.get('parts') or [] if str(r.get('id')) == part_id), None)
    if row is None:
        raise HTTPException(404, '该成本版本没有此零件')
    return dict(row, estimate_id=estimate_id, display_version=display_version)
