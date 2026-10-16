"""Read-only packaging stage projection; no CAD/model/cost computation side effects."""
import copy
import re
from . import packaging_layout, packaging_sheet_cost

TITLES={'3.1':'排版排模','3.2':'组装工艺'}


def load_records(project_id, doc):
    from . import packaging_parts, packaging_conclusions
    try:active=packaging_layout.active_business_document(doc)
    except ValueError:return {}
    records,historical={},{}
    for row in active.get('business_parts') or []:
        code=row.get('business_part_code')
        if not code:continue
        record=dict(packaging_parts.load_part_process(project_id,code) or {})
        if record.get('business_parts_hash') and record['business_parts_hash']!=doc.get('business_parts_hash'):
            version=record.get('business_parts_id')
            if version not in historical:historical[version]=packaging_parts.load_business_parts(project_id,version) if version else {}
            before=next((r for r in (historical[version] or {}).get('business_parts') or [] if r.get('business_part_code')==code),None)
            record['stale']=not before or packaging_conclusions._input(before)!=packaging_conclusions._input(row)
        records[code]=record
    return records


def cut_data(data, plan):
    return {**(data or {}), **(getattr(plan,'packaging_cut_settings',None) or {})}


def validate_cut_settings(value):
    if not isinstance(value,dict) or set(value)-{'cut_margin_length_mm','cut_margin_width_mm','cut_allowances'}:
        raise ValueError('cut_allowance_invalid')
    packaging_sheet_cost.individual_cut({'length_mm':1,'width_mm':1},value)
    for material in (value.get('cut_allowances') or {}):
        packaging_sheet_cost.individual_cut({'length_mm':1,'width_mm':1,'material_code':material},value)
    return copy.deepcopy(value)


def prepare_cut_inputs(project_id, actor):
    """Explicit stage transition: only missing size inputs; no parameter/process model."""
    from . import packaging_parts, packaging_demo_inputs
    doc=packaging_parts.load_business_parts(project_id) or {}
    if not doc.get('derived_from_drawing') or doc.get('auto_geometry_inputs') is False:
        return False
    try:active=packaging_layout.active_business_document(doc)
    except ValueError as exc:raise ValueError(str(exc)) from exc
    active_codes={r.get('business_part_code') for r in active.get('business_parts') or []}
    changed=False
    updated=copy.deepcopy(doc)
    sizes=None
    for row in updated.get('business_parts') or []:
        if row.get('business_part_code') not in active_codes:continue
        for unit in [row]+list(row.get('sections') or []):
            if all(_size(unit)[0]):continue
            if sizes is None:sizes=packaging_demo_inputs.geometry_sizes(packaging_parts.load_parts(project_id) or {})
            size=packaging_demo_inputs.demo_size(unit,sizes)
            unit['poc_inputs']={**(unit.get('poc_inputs') or {}),
                'length_mm':size['length_mm'],'width_mm':size['width_mm'],
                'size_source':size['size_source'],'mock':True}
            changed=True
    if changed:
        packaging_parts.save_business_parts(project_id,updated)
        from ..storage import store
        store.audit(project_id,'packaging_cut_inputs_prepared',{'actor':actor,'human_confirmed':False})
    return changed


def _size(row):
    confirmed=row.get('confirmed_size') or {}
    binding=row.get('geometry_binding') or {}
    selected={}
    if binding.get('status')=='bound' and binding.get('entity_ids'):
        from . import packaging_parts
        selected=packaging_parts._automatic_selected_size(binding,binding['entity_ids']) or {}
    for value, source in ((confirmed,'图纸核对'),(selected,'CAD候选'),
            (row.get('automatic_size') or {},'CAD估算'),(row.get('reference') or {},'图纸/参考信息'),
            (row.get('poc_inputs') or {},'推荐估算'),(row.get('estimated_size') or {},'几何估算')):
        size=[packaging_sheet_cost.positive(value.get(k)) for k in ('length_mm','width_mm')]
        if all(size):return size,source
    return [None,None],'未取得尺寸'


def build(project_id, doc, data, plan, records):
    labels=doc.get('scheme_labels') or []
    try: active=packaging_layout.active_business_document(doc)
    except ValueError: active={'business_parts':[],'layout_rows':[]}
    parts,cuts,processes=[],[],[]
    settings=cut_data(data,plan)
    layouts=packaging_layout.auto_assign_layout_rows(active.get('layout_rows') or [],active.get('business_parts') or [])
    usable_layouts=[r for r in layouts if r.get('kind')!='drawing_layout_candidate'
                    or r.get('size_source')=='cad_cut_sheet_dimension']
    assigned={code for r in usable_layouts for code in r.get('part_codes') or []}
    for row in active.get('business_parts') or []:
        from .packaging_part_route_match import is_external_part
        code=row.get('business_part_code');ref=row.get('reference') or {}
        external=is_external_part(row)
        size,source=_size(row)
        quantity=row.get('usage_qty',ref.get('quantity',row.get('quantity')))
        if quantity is None and doc.get('poc_demo') is True:quantity=(row.get('poc_inputs') or {}).get('quantity')
        item={'code':code,'display_code':row.get('display_code') or code,'name':row.get('name'),
              'material':ref.get('material_text') or row.get('material_text') or row.get('material') or '',
              'size_mm':size,'size_source':source,'quantity':quantity,
              'scheme_id':row.get('scheme_id'),'sections':[],
              'candidate_count':len((row.get('geometry_binding') or {}).get('candidates') or [])}
        item.update(external=external,material_code=(row.get('poc_inputs') or {}).get('material_code') or ref.get('material_code'),
                    gsm=(row.get('poc_inputs') or {}).get('gsm') or ref.get('gsm'),
                    thickness_mm=(row.get('poc_inputs') or {}).get('thickness_mm') or ref.get('thickness_mm'))
        sections=row.get('sections') or []
        item['section_cost_units']=len(sections)>1 or row.get('sections_source')=='manual'
        for section in sections:
            child_size,child_source=_size(section)
            item['sections'].append({'code':code+'::'+str(section.get('section_id') or ''),
                'name':section.get('name'),'material':section.get('material_text') or item['material'],
                'material_code':(section.get('poc_inputs') or {}).get('material_code'),
                'scheme_id':row.get('scheme_id'),
                'size_mm':child_size,'size_source':child_source,'per_parent_quantity':section.get('quantity'),
                'quantity':float(quantity)*float(section['quantity']) if packaging_sheet_cost.positive(quantity) and packaging_sheet_cost.positive(section.get('quantity')) else None})
        parts.append(item)
        if not external:
            for unit in item['sections'] if item['section_cost_units'] else [item]:
                if unit['code'] in assigned:continue
                variables={**(row.get('poc_inputs') or {}),'length_mm':unit['size_mm'][0],'width_mm':unit['size_mm'][1],
                           'material_code':unit.get('material_code')}
                try: cut=packaging_sheet_cost.individual_cut(variables,settings)
                except ValueError: cut={'sheet_mm':[None,None],'allowance_mm':[],'size_source':'invalid_allowance'}
                cuts.append({**unit,**cut})
        record=records.get(code) or {}
        if external:continue
        from . import packaging_process_instances
        scope_changed=not packaging_process_instances.is_whole_part_process(record)
        processes.append({'code':code,'name':item['name'],'section_name':'',
             'plan':{} if scope_changed else record.get('plan') or {},'coverage':record.get('coverage') or {},
             'process_scope_changed':scope_changed,
             'stale': scope_changed or bool(record.get('stale')) or (record.get('plan') or {}).get('part_class') in ('machining','sheet_metal','welded')})
    layout_parts=[]
    for parent in parts:
        units=parent['sections'] if parent['section_cost_units'] else [parent]
        layout_parts.extend([{**unit,'name':parent['name']+' · '+str(unit['name']) if parent['section_cost_units'] else unit['name']} for unit in units])
    index={p['code']:p for p in layout_parts}
    layout_gaps=[]
    owners={}
    for layout in usable_layouts:
        codes=layout.get('part_codes') or []
        counts=layout.get('part_counts') or ({codes[0]:layout.get('n_up')} if len(codes)==1 else {})
        for code in codes:owners.setdefault(code,[]).append(layout.get('entity_id'))
        size=layout.get('sheet_mm') or []
        if (len(size)!=2 or not all(packaging_sheet_cost.positive(v) for v in size)
                or not codes or len(codes)!=len(set(codes)) or layout.get('dimension_role') in ('machine_sheet','unclassified')
                or any(c not in index or not packaging_sheet_cost.positive(counts.get(c))
                       or not float(counts[c]).is_integer() for c in codes)):
            layout_gaps.append('排模 '+str(layout.get('entity_id') or '')+' 缺开料尺寸、关联件或排出数量')
    if any(len(v)>1 for v in owners.values()):layout_gaps.append('同一零件关联多个实际排模，请调整关联')
    projected_layouts=[]
    for layout in layouts:
        projected={k:v for k,v in layout.items() if k not in ('raw_entities','geometry')}
        size=layout.get('sheet_mm') or [];area=0;known=True
        codes=layout.get('part_codes') or [];counts=layout.get('part_counts') or ({codes[0]:layout.get('n_up')} if len(codes)==1 else {})
        for code in codes:
            dims=(index.get(code) or {}).get('size_mm') or []
            count=packaging_sheet_cost.positive(counts.get(code))
            if len(dims)!=2 or not all(dims) or not count:known=False;break
            area+=dims[0]*dims[1]*count
        utilization=area/(size[0]*size[1]) if known and codes and len(size)==2 and all(packaging_sheet_cost.positive(v) for v in size) else None
        projected['utilization']=utilization if utilization is not None and 0<=utilization<=1 else None
        projected_layouts.append(projected)
    selected=bool(parts) and (len(labels)<2 or doc.get('selected_scheme_id') in labels)
    missing_cuts=[c['code'] for c in cuts if not all(packaging_sheet_cost.positive(v) for v in c['sheet_mm'])]
    layout_ready=selected and not layout_gaps and not missing_cuts
    process=getattr(plan,'process',None)
    process_steps=process.model_dump().get('steps',[]) if process else []
    from . import packaging_assembly
    assembly_state=packaging_assembly.recommendation_state(plan)
    usable_assembly=bool(process_steps) or assembly_state=='succeeded'
    process_valid=usable_assembly and packaging_assembly.valid(plan,active,records) and all(not p['stale'] and bool(p['plan'].get('steps')) for p in processes)
    assembly_steps=[]
    if process:
        from ..models.process import ProcessPlan
        for step in process_steps:
            try:packaging_assembly.validate(ProcessPlan(steps=[step]),active)
            except ValueError:continue
            step=dict(step,display_no=len(assembly_steps)+1)
            if re.fullmatch(r'(?:工艺|工序)\s*\d+',step.get('name') or ''):
                linked=[r for r in (process.assembly_connections or []) if r.get('id') in (step.get('assembly_connection_ids') or [])]
                if linked:
                    step['name']='；'.join(r.get('name') or r.get('method') or '连接名称待补' for r in linked)
                elif step.get('description') and not re.fullmatch(r'(?:工艺|工序)\s*\d+',step['description']):
                    step['name']=step['description']
                else:step['name']='零件间连接（工序名称待补）'
            assembly_steps.append(step)
    route_scheme=getattr(plan,'packaging_route_scheme_id',None)
    selected_scheme=doc.get('selected_scheme_id')
    if len(labels)>1 and route_scheme != selected_scheme:
        process_valid=False
    elif route_scheme and selected_scheme and route_scheme != selected_scheme:
        process_valid=False
    return {'project_id':project_id,'business_parts_hash':doc.get('business_parts_hash'),
        'schemes':doc.get('schemes') or [{'scheme_id':label,'name':'方案 '+str(label)} for label in labels],
        'selected_scheme_id':doc.get('selected_scheme_id'),'parts':parts,'layouts':projected_layouts,
        'individual_cuts':cuts,'part_processes':processes,'layout_parts':layout_parts,
        'assembly_steps':assembly_steps,'postprocess_steps':assembly_steps,
        'assembly_result_state':assembly_state,
        'assembly_attempt':copy.deepcopy(getattr(plan,'packaging_assembly_attempt',{}) or {}),
        'assembly_scope_changed':process is not None and not packaging_assembly.valid(plan,active,records),
        'assembly_summary':getattr(process,'summary','') if process else '',
        'assembly_questions':[q.model_dump() for q in process.open_questions] if process else [],
        'cut_settings':{k:settings[k] for k in ('cut_margin_length_mm','cut_margin_width_mm','cut_allowances') if k in settings},
        'scheme_ready':selected,
        'status':{'3.1':{'completed':layout_ready,'missing':([] if selected else ['未取得业务部件或尚未选择方案'])+layout_gaps+(['开料尺寸缺失：'+','.join(missing_cuts)] if missing_cuts else [])},
                  '3.2':{'completed':process_valid and bool(getattr(plan,'process_confirmed',False)),
                         'missing':[] if process_valid else ['请补齐当前零件工艺并生成绑定零件的新增组装工序']}},
        'process_ready':sum(bool(p['plan'].get('steps')) and not p['stale'] for p in processes),
        'process_missing':sum(not p['plan'].get('steps') or p['stale'] for p in processes)}
