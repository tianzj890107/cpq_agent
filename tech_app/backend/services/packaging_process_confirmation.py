"""包装零件工艺人工确认：只读计算输入版本，不生成工艺、不签组装确认。"""
from . import packaging_parts, packaging_layout, packaging_process_instances


def snapshot(project_id, document=None):
    doc = document if document is not None else (packaging_parts.load_business_parts(project_id) or {})
    try:
        active = packaging_layout.active_business_document(doc)
        collected = packaging_process_instances.collect(project_id, document=doc)
    except ValueError:
        return {'ready': False, 'input_hash': '', 'missing': ['请先选用当前方案']}
    # 候选池、图片、显示序号、保存时间不参与确认；实体、组成及工艺业务内容参与。
    def part_input(row):
        keys = ('business_part_code', 'scheme_id', 'name', 'material', 'material_text', 'reference',
                'poc_inputs', 'confirmed_size', 'automatic_size', 'estimated_size', 'quantity',
                'usage_qty', 'cost_parameters', 'sections_source', 'section_id', 'entity_ids', 'component_ids')
        result = {key: row[key] for key in keys if key in row}
        binding = row.get('geometry_binding') or {}
        result['geometry_binding'] = {key: binding[key] for key in (
            'status', 'entity_ids', 'component_ids', 'size_mm', 'confirmed_size') if key in binding}
        result['sections'] = [part_input(child) for child in row.get('sections') or []]
        return result
    parts = sorted([part_input(row) for row in active.get('business_parts') or []],
                   key=lambda row: str(row.get('business_part_code') or ''))
    records = [{key: row.get(key) for key in ('part_code', 'plan', 'usage_qty', 'cost_parameters', 'process_scope')}
               for row in collected['records']]
    records.sort(key=lambda row: str(row.get('part_code') or ''))
    ready = bool(parts and records and not collected['missing'])
    return {'ready': ready, 'missing': collected['missing'],
            'input_hash': packaging_process_instances.fingerprint({'parts': parts, 'records': records,
                'scheme': active.get('selected_scheme_id')}) if ready else ''}


def status(project_id, plan=None, document=None):
    if plan is None:
        from . import integration
        plan = integration.load_plan(project_id)
    current = snapshot(project_id, document)
    saved = plan.packaging_process_confirmation
    confirmed = bool(current['ready'] and saved.get('input_hash') == current['input_hash'])
    return {**current, 'confirmed': confirmed,
            'confirmed_by': saved.get('confirmed_by') if confirmed else None,
            'confirmed_at': saved.get('confirmed_at') if confirmed else None}
