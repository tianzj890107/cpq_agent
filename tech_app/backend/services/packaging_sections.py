"""Physical portions of one business part; CAD IDs are evidence, union bounds are not sizes."""
from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Callable

MAX_SECTIONS = 64


def positive(value: Any):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def bbox(row):
    for raw in (row.get('drawing_bbox'), row.get('bbox'), (row.get('outline') or {}).get('bbox')):
        if isinstance(raw, (list, tuple)) and len(raw) == 4:
            try:
                box = [float(v) for v in raw]
                if all(math.isfinite(v) for v in box) and box[2] > box[0] and box[3] > box[1]:
                    return box
            except (TypeError, ValueError):
                pass
    return None


def union_bounds(rows):
    boxes = [box for row in rows if (box := bbox(row)) is not None]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None


def _inside(point, polygon):
    """Boundary-inclusive polygon test, not bounding-rectangle containment."""
    x, y = point[:2]
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        ax, ay = a[:2]; bx, by = b[:2]
        cross = (x-ax)*(by-ay)-(y-ay)*(bx-ax)
        if abs(cross) < 1e-7 and min(ax,bx)-1e-7 <= x <= max(ax,bx)+1e-7 and min(ay,by)-1e-7 <= y <= max(ay,by)+1e-7:
            return True
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside


def physical_groups(components):
    """Preserve disconnected portions; only attach proven internal non-material strokes."""
    rows = sorted([r for r in components if bbox(r)], key=lambda r: str(r.get('component_id')))
    parent = {}
    for child in rows:
        # Two nested cut outlines can also be two usable pieces. Never call them
        # a hole from bbox containment alone; require a known internal role.
        if child.get('role') not in ('hole', 'crease', 'v_groove', 'partial_cut', 'reference'):
            continue
        points = child.get('outline_points') or [p for seg in child.get('segments') or [] for p in seg]
        if not points:
            continue
        candidates = []
        for outer in rows:
            polygon = outer.get('outline_points') or []
            if outer is child or outer.get('outline_status') != 'closed' or len(polygon) < 3:
                continue
            ob, cb = bbox(outer), bbox(child)
            area = (ob[2]-ob[0])*(ob[3]-ob[1])
            if area <= (cb[2]-cb[0])*(cb[3]-cb[1]):
                continue
            samples = list(points) + [[(a[0]+b[0])/2,(a[1]+b[1])/2] for a,b in zip(points,points[1:])]
            if all(_inside(p, polygon) for p in samples):
                candidates.append((area, str(outer.get('component_id'))))
        if candidates:
            parent[str(child.get('component_id'))] = min(candidates)[1]
    groups = {}
    for row in rows:
        key = str(row.get('component_id'))
        while key in parent:
            key = parent[key]
        groups.setdefault(key, []).append(row)
    return list(groups.values())


def _section(name, members, *, section_id='', quantity=1, source='cad_components'):
    ids = sorted({str(r.get('component_id')) for r in members})
    entities = sorted({str(e) for r in members for e in r.get('entity_ids') or [] if e})
    box = union_bounds(members)
    sid = section_id or 'section:' + hashlib.sha256('\n'.join(entities or ids or [name]).encode()).hexdigest()[:12]
    return {'section_id': sid, 'name': name, 'component_ids': ids, 'entity_ids': entities,
            'bbox': box, 'estimated_size': {'length_mm': box[2]-box[0], 'width_mm': box[3]-box[1]} if box else {},
            'confirmed_size': {}, 'quantity': quantity, 'source': source,
            'status': 'bound' if entities else 'pending_attribution', 'exact_entities': True}


def _composition_gaps(groups):
    for i, outer_group in enumerate(groups):
        for outer in outer_group:
            polygon = outer.get('outline_points') or []
            if outer.get('outline_status') != 'closed' or len(polygon) < 3:
                continue
            for j, inner_group in enumerate(groups):
                if i == j:
                    continue
                for inner in inner_group:
                    points = inner.get('outline_points') or []
                    if inner.get('outline_status') == 'closed' and points and all(_inside(p,polygon) for p in points):
                        return ['nested_cut_outline_needs_composition_decision']
    return []


def hydrate_part(row, components):
    result = copy.deepcopy(row)
    if result.get('sections_source') == 'manual' and result.get('sections'):
        return result
    binding = result.get('geometry_binding') or {}
    selected = set(binding.get('component_ids') or [])
    members = [r for r in components if r.get('component_id') in selected]
    declarations = [r.get('name') for r in result.get('declared_sections') or [] if r.get('name')]
    groups = physical_groups(members)
    proven_groups = binding.get('portion_component_groups') or []
    if proven_groups:
        partition = [cid for group in proven_groups for cid in group]
        by_id = {r['component_id']:r for r in members}
        if len(partition) == len(set(partition)) and set(partition) == set(by_id):
            groups = [[by_id[cid] for cid in group] for group in proven_groups]
    sections = []
    for index in range(max(len(groups), len(declarations) if len(declarations) > 1 else 0)):
        name = str(result.get('name') or '部分') + (f' · 部分 {index+1}' if max(len(groups),len(declarations)) > 1 else '')
        section = _section(name, groups[index] if index < len(groups) else [])
        if len(declarations) > 1:
            section['declared_name_candidates'] = list(declarations)
            section['name_source'] = 'portion_name_unresolved'  # CID 排序不证明“内/外/左/右”。
        if len(groups) == 1 and binding.get('size_confirmed') and binding.get('size_source') == 'size_dimension':
            section['confirmed_size'] = {'length_mm': binding.get('length_mm'), 'width_mm': binding.get('width_mm'),
                'source': 'verified_cad_dimension', 'entity_ids': list(section['entity_ids'])}
        # Per-region dimension evidence, never a dimension of the union rectangle.
        for proof in binding.get('section_dimension_evidence') or []:
            if set(proof.get('entity_ids') or []) == set(section['entity_ids']) and positive(proof.get('length_mm')) and positive(proof.get('width_mm')):
                section['confirmed_size'] = copy.deepcopy(proof)
        sections.append(section)
    result['sections'] = sections
    result['sections_source'] = 'cad_components'
    result['section_gaps'] = _composition_gaps(groups)
    result['sections_complete'] = bool(sections) and all(s['status'] == 'bound' for s in sections) and not result['section_gaps']
    return result


def _target(doc, code):
    row = next((r for r in doc.get('business_parts') or [] if r.get('business_part_code') == code), None)
    if row is None:
        raise ValueError('business_part_code_not_found')
    return row


def _version(doc):
    from . import packaging_parts
    doc['stats'] = packaging_parts.business_parts_stats(doc.get('business_parts') or [])
    doc['business_parts_id'], doc['business_parts_hash'] = packaging_parts._business_identity(doc)
    return doc


def assign_sections(doc, code, selections, *, actor):
    if not str(actor or '').strip() or not isinstance(selections, list) or not 1 <= len(selections) <= MAX_SECTIONS:
        raise ValueError('sections_require_actor_and_nonempty_list')
    record = copy.deepcopy(doc)
    row = _target(record, code)
    components = {r['component_id']: r for r in (record.get('geometry_evidence') or {}).get('components') or []}
    foreign = {str(e) for part in record.get('business_parts') or [] if part.get('business_part_code') != code
               and (part.get('geometry_binding') or {}).get('status') in ('bound','partial')
               for e in (part.get('geometry_binding') or {}).get('entity_ids') or []}
    old = {r['section_id']: r for r in row.get('sections') or []}
    used, section_ids, sections = set(), set(), []
    for index, selection in enumerate(selections):
        if not isinstance(selection,dict):
            raise ValueError('section_must_be_an_object')
        ids = selection.get('component_ids') or []
        if not isinstance(ids, list) or not ids or any(not isinstance(cid,str) or cid not in components for cid in ids):
            raise ValueError('section_component_not_in_project')
        members = [components[cid] for cid in sorted(set(ids))]
        available = {str(e) for r in members for e in r.get('entity_ids') or []}
        requested = selection.get('entity_ids')
        if requested is not None and (not isinstance(requested,list)
                or any(not isinstance(e,str) for e in requested) or set(requested) != available):
            raise ValueError('section_entity_set_differs_from_components')
        if not available:
            raise ValueError('section_has_no_cad_entities')
        if used & available:
            raise ValueError('section_duplicate_entities')
        if foreign & available:
            raise ValueError('section_entities_already_assigned')
        quantity = positive(selection.get('quantity',1))
        if quantity is None:
            raise ValueError('section_quantity_must_be_positive')
        name = str(selection.get('name') or f'部分 {index+1}').strip()[:120]
        section = _section(name,members,section_id=str(selection.get('section_id') or '')[:100],quantity=quantity,source='manual')
        section['material_text'] = str(selection.get('material_text') or '').strip()[:300]
        if section['section_id'] in section_ids:
            raise ValueError('section_id_must_be_unique')
        previous = old.get(section['section_id']) or {}
        if set(previous.get('entity_ids') or []) == available and set(previous.get('component_ids') or []) == set(ids):
            section['confirmed_size'] = copy.deepcopy(previous.get('confirmed_size') or {})
        section_ids.add(section['section_id']); used.update(available); sections.append(section)
    row['sections'], row['sections_source'], row['sections_complete'] = sections, 'manual', True
    row['section_gaps'] = []
    row['geometry_binding'] = {**(row.get('geometry_binding') or {}), 'status':'bound','bound_by':'manual',
        'component_ids': sorted({c for s in sections for c in s['component_ids']}), 'entity_ids':sorted(used),
        'bbox':union_bounds(sections), 'size_confirmed':False,'size_source':'sections','exact_entities':True,
        'portion_component_groups':[s['component_ids'] for s in sections]}
    row.pop('confirmed_size',None)
    row['sections_actor'] = str(actor)
    return _version(record)


def confirm_section_size(doc, code, section_id, length_mm, width_mm, *, actor, note):
    if not actor or not str(note or '').strip() or not positive(length_mm) or not positive(width_mm):
        raise ValueError('section_size_requires_positive_dimensions_actor_and_note')
    record = copy.deepcopy(doc)
    row = _target(record,code)
    section = next((s for s in row.get('sections') or [] if s.get('section_id') == section_id),None)
    if section is None or not section.get('entity_ids') or section.get('status') != 'bound':
        raise ValueError('section_geometry_required')
    if section.get('confirmed_size'):
        section.setdefault('size_history',[]).append(copy.deepcopy(section['confirmed_size']))
    from . import packaging_parts
    section['confirmed_size'] = {'length_mm':positive(length_mm),'width_mm':positive(width_mm),
        'source':'manual_confirmed_drawing','confirmed_by':str(actor),'confirmed_at':packaging_parts._stamp(),
        'note':str(note).strip(),'entity_ids':list(section['entity_ids'])}
    row['sections_source'] = 'manual'
    row['sections_complete'] = bool(row.get('sections')) and all(s.get('status') == 'bound' for s in row['sections']) and not row.get('section_gaps')
    return _version(record)


def verified_sections(row):
    sections = row.get('sections') or []
    if not row.get('sections_complete') or not sections:
        raise ValueError('section_composition_incomplete')
    seen = set()
    for section in sections:
        sid = str(section.get('section_id') or '')
        size = section.get('confirmed_size') or {}
        ids = set(section.get('entity_ids') or [])
        if (section.get('status') != 'bound' or not ids or seen & ids
                or not positive(section.get('quantity')) or not positive(size.get('length_mm'))
                or not positive(size.get('width_mm')) or size.get('source') not in ('manual_confirmed_drawing','verified_cad_dimension')
                or set(size.get('entity_ids') or []) != ids):
            raise ValueError('section_size_not_confirmed:' + sid)
        if size.get('source') == 'manual_confirmed_drawing' and not (size.get('confirmed_by') and size.get('note')):
            raise ValueError('section_size_not_confirmed:' + sid)
        seen.update(ids)
    binding = row.get('geometry_binding') or {}
    if binding.get('status') != 'bound' or seen != set(binding.get('entity_ids') or []):
        raise ValueError('section_binding_entities_mismatch')
    result = copy.deepcopy(sections)
    for section in result:
        section['quantity'] = positive(section['quantity'])
    return result


def section_input_rows(row):
    from . import packaging_parts
    out = []
    for section in verified_sections(row):
        reference = dict(packaging_parts.business_part_reference_block(row))
        reference.update(section['confirmed_size'],size_quality='unfolded')
        if section.get('material_text'):
            reference['material_text'] = section['material_text']
        child = {'business_part_code':row['business_part_code']+'::'+section['section_id'],
                 'parent_part_code':row['business_part_code'],'section_id':section['section_id'],
                 'name':section['name'],'reference':reference,'material':row.get('material'),
                 'quantity':section['quantity'],'entity_ids':section['entity_ids']}
        out.append(child)
    return out


def compute_material(inputs, calculate: Callable):
    if not inputs.get('sections'):
        return calculate('material',dict(inputs['variables']))
    results = []
    for section in inputs['sections']:
        line = calculate('material',dict(section['variables']))
        amount = line.get('amount')
        multiplier = positive(section.get('quantity'))
        if multiplier is not None and isinstance(amount,(int,float)) and math.isfinite(amount):
            amount *= multiplier
        else:
            amount = None
        results.append({**line,'amount':amount,'section_id':section['section_id'],
                        'section_name':section['name'],'quantity':multiplier,'inputs':section['variables'],
                        'unit_amount':line.get('amount')})
    missing = [r for r in results if r['amount'] is None]
    return {'amount':None if missing else sum(r['amount'] for r in results), 'sections':results,
            'expression':'逐部分材料费 × 每套用量之和','inputs':{'sections':[r['inputs'] for r in results]},
            'gap':{'code':'section_cost_incomplete','section_ids':[r['section_id'] for r in missing]} if missing else None}


def recommend_process(inputs, recommend, *, note='', attachments=None):
    from . import packaging_parts, process, da_process_routing
    results, steps, questions, warnings, reused, missing = [], [], [], [], 0, 0
    for section in inputs['sections']:
        part = packaging_parts.business_as_ir_part(section['row'])
        grounding = '\n'.join([section['grounding'],
            '组成：'+section['name']+'；用量：'+str(section['quantity'])+'；CAD 图元：'+','.join(section['entity_ids']),note])
        da_lookup = da_process_routing.for_row(section['row'])
        grounding += '\n' + da_process_routing.grounding(da_lookup)
        plan, coverage = recommend(part, overall=None, geom=None, note=grounding, attachments=attachments or [])
        payload = plan.model_dump() if hasattr(plan,'model_dump') else dict(plan)
        warnings.extend(section['name']+'：'+w for w in process.compute(payload)['warnings'])
        results.append({'section_id':section['section_id'],'section_name':section['name'],'lookup':da_lookup,
            'entity_ids':section['entity_ids'],'quantity':section['quantity'],'plan':payload,'coverage':coverage})
        original_steps = payload.get('steps') or []
        numbers = {s.get('step_no'):(len(steps)+i+1)*10 for i,s in enumerate(original_steps)}
        for step in original_steps:
            step = dict(step)
            step['original_step_no'] = step.get('step_no')
            step['step_no'] = numbers[step.get('step_no')]
            step['depends_on'] = [numbers.get(n,n) for n in step.get('depends_on') or []]
            if isinstance(step.get('duration_min'),(int,float)):
                step['original_duration_min'] = step['duration_min']
                step['duration_min'] *= section['quantity']
            step['section_id'], step['section_name'] = section['section_id'], section['name']
            step['op_id'] = section['section_id']+'::'+str(step.get('op_id') or len(steps)+1)
            step['name'] = section['name']+' · '+str(step.get('name') or step.get('operation') or '')
            steps.append(step)
        questions.extend(payload.get('open_questions') or [])
        summary = (coverage or {}).get('summary') or {}
        reused += summary.get('reused') or 0
        missing += summary.get('missing') or 0
    merged = {'part_id':inputs['part_code'],'part_name':inputs.get('name') or '', 'steps':steps,
              'overall_note':'按全部组成分别推荐工艺，保留各部分来源与用量','open_questions':questions,'rule_warnings':warnings}
    return merged, {'summary':{'reused':reused,'missing':missing},'sections':[r['coverage'] for r in results]}, results
