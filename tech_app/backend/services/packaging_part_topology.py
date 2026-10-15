"""Conservative CAD topology refinement; no model, answer table or storage access.

Connectivity supplies candidates, not physical material count. Internal contours
remain drawing evidence: this module does not claim they are proven voids and
does not subtract their area from material costs.
"""
from __future__ import annotations

import copy
import hashlib
import math

from . import packaging_sections as sections
from . import packaging_part_visual_review as visual

RULE = 'cad-conservative-topology-v1'
GEOMETRY_TYPES = {'LINE', 'ARC', 'CIRCLE', 'ELLIPSE', 'SPLINE', 'LWPOLYLINE', 'POLYLINE'}


def _box(values):
    boxes = [b for b in values if isinstance(b, (list, tuple)) and len(b) == 4
             and all(isinstance(n, (int, float)) and math.isfinite(n) for n in b)]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None


def _circle(entity):
    attr = entity.get('attributes') or {}
    if entity.get('type') != 'CIRCLE':
        return None
    center, radius = attr.get('center'), attr.get('radius')
    if (not isinstance(center, (list, tuple)) or len(center) < 2
            or not all(isinstance(n, (int, float)) and math.isfinite(n) for n in (*center[:2], radius))
            or radius <= 0):
        return None
    return center[0], center[1], radius


def _inside(entity, outer):
    x, y, radius = _circle(outer)
    own = _circle(entity)
    if own:
        return math.hypot(own[0]-x, own[1]-y)+own[2] <= radius+1e-4
    box = entity.get('bbox')
    if not _box([box]) or box[0] < x-radius-1e-4 or box[2] > x+radius+1e-4 or box[1] < y-radius-1e-4 or box[3] > y+radius+1e-4:
        return False
    points = visual._entity_points(entity)
    return bool(points) and all(math.hypot(p[0]-x, p[1]-y) <= radius+1e-4 for p in points)


def _safe(candidate, entities, foreign):
    ids = set(candidate.get('entity_ids') or [])
    return bool(ids) and ids <= entities.keys() and not ids.intersection(foreign) and not set(
        candidate.get('evidence_reasons') or []).intersection(('other_name_anchor', 'mixed_name_owners'))


def _enclosing(row, entities, foreign):
    old = set(row['geometry_binding'].get('entity_ids') or [])
    found = []
    for candidate in row['geometry_binding'].get('candidates') or []:
        ids = set(candidate.get('entity_ids') or [])
        if not old or not old <= ids or not _safe(candidate, entities, foreign):
            continue
        for eid in sorted(ids):
            outer = entities[eid]
            if _circle(outer) and all(_inside(entities[i], outer) for i in ids):
                found.append((_circle(outer)[2], len(ids), str(candidate.get('id') or ''), candidate))
    return max(found, key=lambda item: item[:3])[3] if found else None


def _circle_groups(row, entities, foreign):
    ids = set(row['geometry_binding'].get('entity_ids') or [])
    circles = [entities[eid] for eid in sorted(ids) if eid in entities and _circle(entities[eid])]
    polygons = [entities[eid] for eid in sorted(ids) if eid in entities and entities[eid].get('closed')
                and not _circle(entities[eid]) and len(visual._entity_points(entities[eid])) >= 3]
    circles = [e for e in circles if not any(all(sections._inside(p, visual._entity_points(poly))
                for p in visual._entity_points(e)) for poly in polygons)]
    roots = [e for e in circles if not any(e is not p and _circle(p)[2] > _circle(e)[2]+1e-5
                                          and _inside(e, p) for p in circles)]
    if not roots:
        return None
    root_ids = {e['entity_id'] for e in roots}
    groups, owned = [], set()
    for outer in sorted(roots, key=lambda e: (-_circle(e)[2], _circle(e)[0], _circle(e)[1], e['entity_id'])):
        members = {eid for eid, e in entities.items() if (eid in ids or eid not in foreign) and _inside(e, outer)}
        # Preserve the old material's teeth/crease geometry outside its nominal circle.
        for section in row.get('sections') or []:
            if (outer['entity_id'] in (section.get('entity_ids') or [])
                    and len(root_ids.intersection(section.get('entity_ids') or [])) == 1):
                members.update(section.get('entity_ids') or [])
        members.add(outer['entity_id']); members -= owned
        owned.update(members)
        groups.append({'entity_ids': sorted(members),
                       'outer_entity_id': outer['entity_id'],
                       'internal_contour_ids': sorted(eid for eid in members if eid != outer['entity_id']
                                                      and entities.get(eid, {}).get('closed'))})
    for section in row.get('sections') or []:
        members = set(section.get('entity_ids') or [])-owned
        if members:
            groups.append({'entity_ids': sorted(members)})
            owned.update(members)
    if ids-owned:
        groups.append({'entity_ids': sorted(ids-owned)})
    return groups


def _parallel(row, entities, foreign):
    binding = row['geometry_binding']; old = set(binding.get('entity_ids') or [])
    candidates = []
    for c in binding.get('candidates') or []:
        b = c.get('bbox')
        if (not _safe(c, entities, foreign) or not _box([b]) or c.get('member_total', 1) != 1
                or not c.get('name_anchor_entity_id') or 'complete_closed_outline' not in (c.get('evidence_reasons') or [])):
            continue
        w, h = b[2]-b[0], b[3]-b[1]
        if min(w, h) <= 0 or max(w, h)/min(w, h) < 3 or any(_circle(entities[eid]) for eid in c['entity_ids']):
            continue
        candidates.append(c)
    owners = {c['name_anchor_entity_id'] for c in candidates if old and old <= set(c['entity_ids'])}
    if len(owners) != 1:
        return None
    unique = {tuple(sorted(c['entity_ids'])): c for c in candidates if c['name_anchor_entity_id'] in owners}
    candidates = [unique[key] for key in sorted(unique)]
    if not 2 <= len(candidates) <= 4 or len({c['frame_id'] for c in candidates if c.get('frame_id')}) > 1:
        return None
    sizes = [(c['bbox'][2]-c['bbox'][0], c['bbox'][3]-c['bbox'][1]) for c in candidates]
    longs, shorts = [max(s) for s in sizes], [min(s) for s in sizes]
    if len({w >= h for w, h in sizes}) != 1 or max(longs)/min(longs) > 1.2 or max(shorts)/min(shorts) > 2:
        return None
    merged = _box([c['bbox'] for c in candidates])
    span = merged[3]-merged[1] if sizes[0][0] >= sizes[0][1] else merged[2]-merged[0]
    if span > sum(shorts)+3*max(shorts)*(len(candidates)-1):
        return None
    if not old <= set().union(*(set(c['entity_ids']) for c in candidates)):
        return None
    return [{'entity_ids': sorted(c['entity_ids'])} for c in candidates]


def _install(row, groups, entities, components, candidate, reasons):
    binding = row['geometry_binding']
    ids = sorted({eid for group in groups for eid in group['entity_ids']})
    if not ids or any(eid not in entities for eid in ids):
        return
    box = _box([entities[eid].get('bbox') for eid in ids])
    if not box or box[2] <= box[0] or box[3] <= box[1]:
        return
    selected = set(ids)
    cids = sorted(c['component_id'] for c in components if set(c.get('entity_ids') or [])
                  and set(c['entity_ids']) <= selected)
    if not cids:
        cids = list(binding.get('component_ids') or [])
    token = hashlib.sha256((RULE+'\n'+'\n'.join(ids)+'\n'+str([g['entity_ids'] for g in groups])).encode()).hexdigest()[:16]
    cid = 'topology:'+token
    source = candidate or next((c for c in binding.get('candidates') or []
                               if (c.get('id') or c.get('candidate_id')) == binding.get('candidate_id')), {})
    proposal = copy.deepcopy(source)
    proposal.update(id=cid, candidate_id=cid, entity_ids=ids, component_ids=cids, bbox=box,
                    exact_entities=True,
                    length_mm=box[2]-box[0], width_mm=box[3]-box[1],
                    topology_rule=RULE, source_candidate_id=source.get('id') or source.get('candidate_id'),
                    evidence_reasons=sorted(set(source.get('evidence_reasons') or []) | set(reasons)))
    binding['candidates'] = [c for c in binding.get('candidates') or [] if c.get('id') != cid]+[proposal]
    binding.update(candidate_id=cid, entity_ids=ids, component_ids=cids, geometry_component_ref=cids,
                   bbox=box, length_mm=box[2]-box[0], width_mm=box[3]-box[1],
                   size_confirmed=False, size_source='geometry_binding', section_dimension_evidence=[],
                   topology_rule=RULE, exact_entities=True, portion_component_groups=[])
    out = []
    for i, group in enumerate(groups):
        members = group['entity_ids']; group_ids = set(members)
        group_box = _box([entities[eid].get('bbox') for eid in members])
        section = sections._section(str(row.get('name') or '部分')+(f' · 部分 {i+1}' if len(groups)>1 else ''),
            [{'component_id':cid, 'entity_ids':members, 'bbox':group_box}], source='cad_topology')
        section['component_ids'] = sorted(c['component_id'] for c in components if set(c.get('entity_ids') or [])
                                         and set(c['entity_ids']) <= group_ids)
        section['topology_rule'] = RULE
        if group.get('outer_entity_id'):
            section.update(outer_entity_id=group['outer_entity_id'],internal_contour_ids=group['internal_contour_ids'])
        out.append(section)
    row.update(sections=out, sections_source='cad_topology', sections_complete=True, section_gaps=[])
    proposal.update(topology_sections=copy.deepcopy(out), entity_total=len(ids),
                    section_dimension_evidence=[], portion_components=[],
                    topology_entity_ids=ids)
    row.pop('confirmed_size', None)
    row.pop('automatic_size', None)
    from . import packaging_layout
    row['cad_fragments'] = packaging_layout.part_fragments(cids, components)


def refine_document(doc, cad_ir):
    """Pure explicit-parse stage. No changes when evidence does not support refinement."""
    if not doc.get('derived_from_drawing') or not isinstance(cad_ir, dict):
        return doc
    result = copy.deepcopy(doc)
    registry = (doc.get('geometry_evidence') or {}).get('raw_entities') or {}
    all_entities = {e['entity_id']: e for e in cad_ir.get('entities') or [] if e.get('entity_id')
                    and e.get('type') in GEOMETRY_TYPES}
    entities = {eid:e for eid,e in all_entities.items() if not registry.get(eid, {}).get('annotation')}
    components = (doc.get('geometry_evidence') or {}).get('components') or []
    claims = {}
    for row in result.get('business_parts') or []:
        for eid in (row.get('geometry_binding') or {}).get('entity_ids') or []:
            claims.setdefault(eid, set()).add(row.get('business_part_code'))
    scopes = {str(s['scheme_id']): s for s in doc.get('schemes') or [] if s.get('scheme_id') is not None}
    for row in result.get('business_parts') or []:
        binding = row.get('geometry_binding') or {}
        if (binding.get('bound_by') == 'manual' or row.get('sections_source') in ('manual', 'cad_topology')
                or binding.get('status') != 'bound' or not binding.get('entity_ids')):
            continue
        foreign = {eid for eid, owners in claims.items() if owners-{row.get('business_part_code')}}
        # The annotation heuristic can mark real four-edge closed strips as dimension
        # lines. Only existing complete-outline candidates are counterevidence; never
        # re-admit arbitrary nearby annotation lines to a circle.
        admitted = set(binding['entity_ids'])
        for c in binding.get('candidates') or []:
            if ('complete_closed_outline' in (c.get('evidence_reasons') or [])
                    and c.get('name_anchor_entity_id') and _safe(c, all_entities, foreign)):
                admitted.update(c['entity_ids'])
        scoped = dict(entities)
        scoped.update({eid:all_entities[eid] for eid in admitted if eid in all_entities})
        scope = scopes.get(str(row.get('scheme_id')))
        if scope:
            axis, sign = scope.get('coordinate_axis', 1), scope.get('coordinate_sign', 1)
            scoped = {eid:e for eid,e in scoped.items() if _box([e.get('bbox')]) and
                      scope['y_min'] <= min(sign*e['bbox'][axis], sign*e['bbox'][axis+2])
                      and max(sign*e['bbox'][axis], sign*e['bbox'][axis+2]) < scope['y_max']}
        old = set(binding['entity_ids'])
        if not old <= scoped.keys():
            continue
        candidate = _enclosing(row, scoped, foreign)
        trial = copy.deepcopy(row)
        if candidate:
            trial['geometry_binding']['entity_ids'] = list(candidate['entity_ids'])
        groups = _circle_groups(trial, scoped, foreign)
        parallel = _parallel(trial, scoped, foreign)
        if parallel:
            groups = parallel
        if not groups:
            continue
        # Do not perturb already correct non-circle selections or established material boundaries.
        if not old <= set().union(*(set(g['entity_ids']) for g in groups)):
            continue
        _install(row, groups, scoped, components, candidate,
                 ['same_caption_parallel_portions'] if parallel else ['enclosing_circle_internal_contours'])
        for eid in row['geometry_binding']['entity_ids']:
            claims.setdefault(eid, set()).add(row.get('business_part_code'))
    return result


def restore_candidate_sections(row, candidate, evidence):
    """A subsequent explicit candidate click keeps the same physical partition."""
    binding = row.get('geometry_binding') or {}
    saved = candidate.get('topology_sections') or []
    ids = set(binding.get('entity_ids') or [])
    registry = evidence.get('raw_entities') or {}
    if candidate.get('topology_rule') != RULE or not saved or not ids <= registry.keys():
        return
    partition = [eid for s in saved for eid in s.get('entity_ids') or []]
    if len(partition) != len(set(partition)) or set(partition) != ids:
        return
    for section in saved:
        if section.get('topology_rule') != RULE or not section.get('section_id') or section.get('quantity') != 1:
            return
        box = _box([registry[eid].get('bbox') for eid in section['entity_ids']])
        saved_box = section.get('bbox')
        if not box or not _box([saved_box]) or any(abs(a-b) > 1e-5 for a,b in zip(box, saved_box)):
            return
    binding.update(status='bound',topology_rule=RULE,size_confirmed=False,size_source='geometry_binding',exact_entities=True)
    row.update(sections=copy.deepcopy(saved),sections_source='cad_topology',sections_complete=True,section_gaps=[])


def has_exact_raw_geometry(row, doc):
    binding = row.get('geometry_binding') or {}
    ids = set(binding.get('entity_ids') or [])
    registry = (doc.get('geometry_evidence') or {}).get('raw_entities') or {}
    saved = row.get('sections') or []
    partition = [eid for s in saved for eid in s.get('entity_ids') or []]
    return bool(ids) and binding.get('topology_rule') == RULE and binding.get('exact_entities') is True and (
        row.get('sections_source') == 'cad_topology' and ids <= registry.keys()
        and len(partition) == len(set(partition)) and set(partition) == ids
        and all(s.get('status') == 'bound' and s.get('topology_rule') == RULE for s in saved))
