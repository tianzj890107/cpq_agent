"""One manufacturing operation instance -> one controlled billing decision."""
import hashlib
import json

from .packaging_part_route_match import is_external_part

ALIASES = {
    'UV印刷':'PKG-C-PRINT-UV',
    '覆膜':'PKG-C-LAMINATION','覆哑膜':'PKG-C-LAMINATION',
    '烫金':'PKG-C-HOT-STAMP-FLAT', '机裱':'PKG-C-MOUNTING',
    '面纸模切':'PKG-C-DIE-CUT','模切':'PKG-C-DIE-CUT',
    'V槽':'PKG-C-V-GROOVE','V 槽开槽':'PKG-C-V-GROOVE','V槽开槽':'PKG-C-V-GROOVE',
    '胶水':'PKG-C-GLUE', '手裱':'PKG-C-LABOR', '组装':'PKG-C-LABOR',
    '包盒':'PKG-C-LABOR','检验':'PKG-C-LABOR','清洁包装':'PKG-C-LABOR',
}

def billing(step):
    from . import packaging_cost as cost
    name = str(step.get('step_name') or step.get('name') or '').strip()
    code = str(step.get('formula_code') or ALIASES.get(name) or '')
    entry = cost.FORMULA_CATALOG.get(code)
    if not code.startswith('PKG-C-') or code == 'PKG-C-MATERIAL':
        entry = None
    return {'formula_code':code, 'cost_category':entry['cost_category'] if entry else cost.STEP_CATEGORY_MAP.get(name,''),
            'includes_labor':bool(entry and code not in ('PKG-C-LABOR','PKG-C-MATERIAL','PKG-C-GLUE')),
            'gap':'' if entry else 'process_formula_missing'}

def fingerprint(records):
    return hashlib.sha256(json.dumps(records,sort_keys=True,ensure_ascii=False,
                                    default=str).encode()).hexdigest()

def expand(records):
    steps=[]
    for record in records:
        sections=record.get('sections') or []
        groups=sections or [{'plan':record.get('plan') or {},'section_id':'',
                             'section_name':'','quantity':record.get('usage_qty',1)}]
        for group in groups:
            for source in (group.get('plan') or {}).get('steps') or []:
                name=str(source.get('name') or source.get('step_name') or '')
                prefix=str(group.get('section_name') or '')+' · '
                if group.get('section_name') and name.startswith(prefix):
                    name=name[len(prefix):]
                parameters=dict(record.get('cost_parameters') or {})
                parameters.update(group.get('cost_parameters') or {})
                parameters.update(source.get('cost_parameters') or {})
                step={'step_no':10*(len(steps)+1),'step_name':name,'rank':len(steps),
                      'part_code':record['part_code'],'section_id':group.get('section_id') or '',
                      'operation_code':source.get('operation_code') or '',
                      'original_step_no':source.get('step_no'),
                      'formula_code':source.get('formula_code') or '',
                      'usage_qty':group.get('quantity',record.get('usage_qty',1)),
                      'usage_assumed':bool(record.get('usage_assumed')),
                      'cost_parameters':parameters,'standard_seconds':source.get('standard_seconds'),
                      'time_source':'provided_standard' if source.get('standard_seconds') is not None else 'unknown',
                      'needs_standard_time':False,'workstation':source.get('workstation') or '',
                      'work_content':source.get('description') or name,'parallel_ok':0,
                      'source':record.get('process_origin') or 'model_recommendation',
                      'source_ref':record.get('record_hash') or fingerprint(record),
                      'depends_on':steps[-1]['step_no'] if steps else None}
                step.update(billing(step))
                step['needs_standard_time']=(step['formula_code']=='PKG-C-LABOR' and step['standard_seconds'] is None)
                steps.append(step)
    return steps

def collect(project_id):
    from . import packaging_parts
    doc=packaging_parts.load_business_parts(project_id) or {}
    rows=doc.get('business_parts') or []
    records=[]
    missing=[]
    has_saved_result=False
    for row in rows:
        reference=packaging_parts.business_part_reference_block(row)
        # 外购口径只允许有一处（Spec §2.6）：引用 `packaging_part_route_match.is_external_part`。
        if is_external_part(dict(row, reference=reference)):
            continue
        code=str(row.get('business_part_code') or '')
        if not code:
            continue
        record=packaging_parts.load_part_process(project_id,code) or {}
        has_saved_result |= bool(record.get('plan'))
        if not (record.get('plan') or {}).get('steps'):
            missing.append(code)
            continue
        record=dict(record)
        if (record.get('business_parts_hash') and doc.get('business_parts_hash')
                and record['business_parts_hash'] != doc['business_parts_hash']):
            missing.append(code)
            continue
        record['part_code']=code
        # Do not guess manufacturing dimensions from CAD bbox.
        parameters=dict(record.get('cost_parameters') or {})
        parameters.update(row.get('cost_parameters') or {})
        record['cost_parameters']=parameters
        usage = row.get('usage_qty')
        if usage is None:
            usage = reference.get('quantity')
        if usage is None:
            usage = row.get('quantity')
        record['usage_qty'] = 1 if usage is None else usage
        record['usage_assumed'] = usage is None
        record['business_parts_version'] = doc.get('business_parts_hash') or doc.get('business_parts_id') or ''
        records.append(record)
    return {'records':records,'missing':missing,'hash':fingerprint(records),
            'active':bool(records) or has_saved_result}

def drift(project_id, source_versions):
    if not source_versions.get('part_process_hash'):
        return ''
    try:
        current=collect(project_id)
        return 'part_process_changed' if current['hash'] != source_versions['part_process_hash'] else ''
    except Exception:
        return 'part_process_unavailable'
