"""跨报价/技术工艺的只读成本投影；不计算工序、不猜售价、不读写数据库。"""
import hashlib
import json
import math
from cpq_money import money, total


def number(value):
    if isinstance(value,bool):return None
    try:result=float(value)
    except (TypeError,ValueError):return None
    return result if math.isfinite(result) and result>=0 else None


def cost_fields(result):
    result=result or {}
    package=result.get('packaging_package') or (result if result.get('industry')=='packaging' and 'requirement' in result else {})
    ledger=package.get('cost') if package else result.get('cost') or result.get('cost_breakdown') or {}
    ledger=ledger if isinstance(ledger,dict) else {}
    requirement=package.get('requirement') or {}
    source=package.get('source') or {}
    unit=number(ledger.get('total_cost',ledger.get('total',result.get('unit_cost'))))
    quantity=number(ledger.get('quote_quantity',requirement.get('quote_quantity',result.get('quantity'))))
    if quantity==0:quantity=None
    gaps=ledger.get('gaps') or package.get('gaps') or []
    gap_count=max(int(number(result.get('gap_count')) or 0),len(gaps))
    version=source.get('result_version') or package.get('result_version') or ledger.get('estimate_id') or result.get('cost_result_version') or result.get('result_version') or ''
    fingerprint=hashlib.sha256(json.dumps({'ledger':ledger,'version':version},sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    material=number(ledger.get('material_total',ledger.get('material')))
    processing=number(ledger.get('process_total',ledger.get('processing')))
    others=number(ledger.get('others'))
    if others is None and all(k in ledger for k in ('labor_total','packaging_total')):
        values=[number(ledger.get(k,0)) for k in ('labor_total','packaging_total','other_total')]
        if all(v is not None for v in values):others=sum(values)
    provisional=bool(result.get('provisional') or ledger.get('has_gaps') or ledger.get('stale') or gap_count or unit is None or quantity is None or
        result.get('cost_status') in ('provisional','draft'))
    raw_unit=unit
    if unit is not None:unit=money(unit)
    return {'industry':package.get('industry') or result.get('industry') or '',
        'tech_project_id':result.get('tech_project_id') or result.get('project_id') or source.get('project_id') or '',
        'quote_session_id':result.get('quote_session_id') or source.get('source_session_id') or result.get('source_session_id') or '',
        'business_case_id':result.get('business_case_id') or source.get('business_case_id') or '',
        'source_task_id':result.get('source_task_id') or source.get('source_task_id') or '',
        'unit_cost':unit,'raw_unit_cost':raw_unit,'quantity':quantity,'order_total_cost':total(unit,quantity) if unit is not None and quantity is not None else None,
        'material_cost':money(material) if material is not None else None,'processing_cost':money(processing) if processing is not None else None,'other_cost':money(others) if others is not None else None,
        'currency':ledger.get('currency') or requirement.get('currency') or result.get('currency') or '',
        'cost_tax_included':ledger.get('cost_tax_included',ledger.get('tax_included')),
        'cost_profile':ledger.get('cost_profile') or package.get('cost_profile') or result.get('cost_profile') or '',
        'cost_result_version':str(version),'cost_fingerprint':fingerprint,'gap_count':gap_count,'provisional':provisional,
        'cost_status':'provisional' if provisional else 'formal'}
