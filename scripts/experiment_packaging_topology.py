"""Isolated experiments, NOT imported by the application or suitable for business writes.

No name, file hash, expected part count or workbook answer participates in selection.
The wine input is only a regression gate AFTER the same variants run on both inputs.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tech_app.backend.services import packaging_parts as parts
from tech_app.backend.services import packaging_business_part_resolver as resolver
from tech_app.backend.services import packaging_part_visual_review as visual
from tech_app.backend.services.cad_ir.parser import parse_dxf


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def union(boxes):
    boxes=[b for b in boxes if b and len(b)==4]
    return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)] if boxes else None


def circle(entity):
    a=entity.get('attributes') or {}
    if entity.get('type')!='CIRCLE' or not a.get('center') or not a.get('radius'):return None
    return (*a['center'][:2],float(a['radius']))


def inside(entity,outer):
    x,y,r=circle(outer)
    own=circle(entity)
    if own:return math.hypot(own[0]-x,own[1]-y)+own[2]<=r+1e-4
    points=visual._entity_points(entity)
    return bool(points) and all(math.hypot(p[0]-x,p[1]-y)<=r+1e-4 for p in points)


def safe(candidate):
    return not set(candidate.get('evidence_reasons') or []).intersection(('other_name_anchor','mixed_name_owners'))


def enclosing_candidate(row,entities):
    binding=row.get('geometry_binding') or {};old=set(binding.get('entity_ids') or [])
    if not old:return None
    found=[]
    for c in binding.get('candidates') or []:
        if not safe(c):continue
        ids=set(c.get('entity_ids') or [])
        if not old <= ids:continue  # Never drop an existing physical portion to take one circle.
        for eid in ids:
            e=entities.get(eid) or {}
            if not circle(e):continue
            if all(inside(entities[i],e) for i in ids if i in entities):
                found.append((circle(e)[2],len(ids),c))
    return max(found,key=lambda item:item[:2])[2] if found else None


def apply_candidate(row,candidate):
    binding=row['geometry_binding'];ids=sorted(set(candidate.get('entity_ids') or []));box=candidate.get('bbox')
    if ids==sorted(binding.get('entity_ids') or []):return
    binding.update(entity_ids=ids,component_ids=list(candidate.get('component_ids') or []),bbox=box,
        length_mm=box[2]-box[0],width_mm=box[3]-box[1],
        candidate_id=candidate.get('id') or candidate.get('candidate_id'),
        experiment_reason='existing_enclosing_contour_not_internal_holes')


def topology(row,entities,claimed):
    binding=row.get('geometry_binding') or {};ids=set(binding.get('entity_ids') or [])
    circles=[entities[eid] for eid in ids if eid in entities and circle(entities[eid])]
    if not circles:return
    # A circle inside a noncircular closed outer contour is a hole, NOT another material portion.
    from tech_app.backend.services.packaging_sections import _inside
    closed_polygons=[e for eid in ids if (e:=entities.get(eid)) and e.get('closed') and not circle(e)
                     and len(visual._entity_points(e))>=3]
    def inside_polygon(outer,polygon):
        points=visual._entity_points(outer);boundary=visual._entity_points(polygon)
        return bool(points) and all(_inside(point,boundary) for point in points)
    circles=[e for e in circles if not any(inside_polygon(e,poly) for poly in closed_polygons)]
    if not circles:return
    roots=[e for e in circles if not any(e is not p and circle(p)[2]>circle(e)[2]+1e-5 and inside(e,p) for p in circles)]
    groups=[];owned=set();holes=[]
    for outer in sorted(roots,key=lambda e:(-circle(e)[2],circle(e)[0],circle(e)[1],e['entity_id'])):
        members={eid for eid,e in entities.items() if e.get('type') in parts.LOOP_TYPES
                 and (eid in ids or not claimed.get(eid)) and inside(e,outer)}
        for section in row.get('sections') or []:
            if outer['entity_id'] in (section.get('entity_ids') or []):
                members.update(section.get('entity_ids') or [])
        members.add(outer['entity_id']);members-=owned;owned|=members;ids|=members
        children=[eid for eid in members if eid!=outer['entity_id'] and entities[eid].get('closed')]
        holes.extend(children)
        box=outer['bbox']
        groups.append({'entity_ids':sorted(members),'bbox':box,
            'estimated_size':{'length_mm':box[2]-box[0],'width_mm':box[3]-box[1]},
            'outer_entity_id':outer['entity_id'],'hole_or_internal_contour_ids':sorted(children),
            'experiment_role':'one_material_portion_with_internal_contours'})
    # Preserve existing disconnected portions, never promote an internal hole to a material section.
    for section in row.get('sections') or []:
        members=set(section.get('entity_ids') or [])-owned
        if members:
            box=union([entities[eid].get('bbox') for eid in members if eid in entities])
            groups.append(dict(section,entity_ids=sorted(members),bbox=box))
            owned|=members
    remaining=ids-owned
    if remaining:
        groups.append({'entity_ids':sorted(remaining),'bbox':union([entities[eid].get('bbox') for eid in remaining if eid in entities]),
                       'experiment_role':'unclassified_existing_geometry'})
    binding['entity_ids']=sorted(ids)
    row['sections']=groups
    row['experiment_topology']={'circle_roots':[e['entity_id'] for e in roots],
                                'hole_or_internal_contour_ids':sorted(holes)}


def parallel_portions(row,entities,claimed=None):
    binding=row.get('geometry_binding') or {};old=set(binding.get('entity_ids') or [])
    # Repeated elongated closed shapes under the SAME label, not nearest unrelated shapes.
    candidates=[]
    for c in binding.get('candidates') or []:
        reasons=c.get('evidence_reasons') or []
        b=c.get('bbox')
        if not safe(c) or not b or 'complete_closed_outline' not in reasons:continue
        if any((claimed or {}).get(eid) for eid in c.get('entity_ids') or []):continue
        if c.get('member_total',1)!=1 or not c.get('name_anchor_entity_id'):continue
        w,h=b[2]-b[0],b[3]-b[1]
        if min(w,h)<=0 or max(w,h)/min(w,h)<3:continue
        if any(circle(entities[eid]) for eid in c.get('entity_ids') or [] if eid in entities):continue
        candidates.append(c)
    owners={c['name_anchor_entity_id'] for c in candidates if old <= set(c.get('entity_ids') or [])}
    if len(owners)!=1:return
    candidates=[c for c in candidates if c['name_anchor_entity_id'] in owners]
    unique={tuple(sorted(c.get('entity_ids') or [])):c for c in candidates}
    candidates=list(unique.values())
    if not 2<=len(candidates)<=4:return
    if len({c.get('frame_id') for c in candidates if c.get('frame_id')})>1:return
    sizes=[(c['bbox'][2]-c['bbox'][0],c['bbox'][3]-c['bbox'][1]) for c in candidates]
    if len({w>=h for w,h in sizes})!=1:return
    longs=[max(w,h) for w,h in sizes];shorts=[min(w,h) for w,h in sizes]
    if max(longs)/min(longs)>1.2 or max(shorts)/min(shorts)>2:return
    merged=union([c['bbox'] for c in candidates])
    cross_span=merged[3]-merged[1] if sizes[0][0]>=sizes[0][1] else merged[2]-merged[0]
    if cross_span>sum(shorts)+3*max(shorts)*(len(candidates)-1):return
    ids=set().union(*(set(c.get('entity_ids') or []) for c in candidates))
    if not old <= ids:return
    binding.update(entity_ids=sorted(ids),bbox=merged,length_mm=merged[2]-merged[0],width_mm=merged[3]-merged[1],
                   experiment_reason='same_caption_parallel_closed_material_portions')
    row['sections']=[{'entity_ids':sorted(c.get('entity_ids') or []),'bbox':c['bbox'],
                     'estimated_size':{'length_mm':c['bbox'][2]-c['bbox'][0],'width_mm':c['bbox'][3]-c['bbox'][1]}}
                     for c in candidates]


def caption_union(row,entities,claimed):
    """A deliberately broader fourth experiment: only candidates sharing an exact CAD caption ID."""
    binding=row.get('geometry_binding') or {};old=set(binding.get('entity_ids') or [])
    if not old:return
    candidates=[c for c in binding.get('candidates') or [] if safe(c)]
    selected=next((c for c in candidates if (c.get('id') or c.get('candidate_id'))==binding.get('candidate_id')),None)
    owner=(selected or {}).get('name_anchor_entity_id')
    if not owner:return
    candidates=[c for c in candidates if c.get('name_anchor_entity_id')==owner
                and not any(claimed.get(eid) for eid in c.get('entity_ids') or [])]
    if len({c.get('frame_id') for c in candidates if c.get('frame_id')})>1:return
    additions=[set(c.get('entity_ids') or []) for c in candidates if not set(c.get('entity_ids') or [])<=old]
    if not additions:return
    groups=[set(s.get('entity_ids') or []) for s in row.get('sections') or []]
    if not groups:groups=[set(old)]
    for new in additions:
        touched=[g for g in groups if g & new]
        groups=[g for g in groups if not g & new]+[set().union(new,*touched)]
    ids=set().union(old,*groups);box=union([entities[eid].get('bbox') for eid in ids if eid in entities])
    binding.update(entity_ids=sorted(ids),bbox=box,length_mm=box[2]-box[0],width_mm=box[3]-box[1],
                   experiment_reason='same_exact_caption_candidate_union')
    row['sections']=[{'entity_ids':sorted(g),'bbox':union([entities[eid].get('bbox') for eid in g if eid in entities])} for g in groups]


def experiment(data,variant):
    result=copy.deepcopy(data['doc']);entities={e['entity_id']:e for e in data['ir']['entities']}
    claimed={eid:row['business_part_code'] for row in result['business_parts'] for eid in (row.get('geometry_binding') or {}).get('entity_ids') or []}
    for row in result['business_parts']:
        if (row.get('geometry_binding') or {}).get('bound_by')=='manual':continue
        candidate=enclosing_candidate(row,entities)
        if candidate:apply_candidate(row,candidate)
        if variant in ('topology','combined','caption_union'):
            foreign={eid:owner for eid,owner in claimed.items() if owner!=row['business_part_code']}
            topology(row,entities,foreign)
        if variant in ('combined','caption_union'):
            foreign={eid:owner for eid,owner in claimed.items() if owner!=row['business_part_code']}
            parallel_portions(row,entities,foreign)
            if variant=='caption_union':
                caption_union(row,entities,foreign)
                topology(row,entities,foreign)
    return result


def differences(before,after):
    changes=[]
    for a,b in zip(before['business_parts'],after['business_parts']):
        if a!=b:
            def state(row):
                binding=row.get('geometry_binding') or {}
                return {'candidate_id':binding.get('candidate_id'),'dimensions':[binding.get('length_mm'),binding.get('width_mm')],
                        'entity_total':len(binding.get('entity_ids') or []),'section_total':len(row.get('sections') or [])}
            changes.append({'id':a['business_part_code'],'name':a['name'],'before':state(a),'after':state(b),
                            'changed_fields':[k for k in set(a)|set(b) if a.get(k)!=b.get(k)]})
    return changes


def parse(path):
    ir=parse_dxf(path.read_bytes(),filename=path.name);geometry=parts.extract(ir)
    resolved=resolver.resolve_business_parts('offline-experiment',ir,geometry,use_model=True,strict_schemes=True)
    doc=parts.business_parts_document(resolved['reference'],geometry,bindings=resolved['match'])
    return {'ir':ir,'geometry':geometry,'resolved':resolved,'doc':doc}


def render_comparison(data,result,out):
    from PIL import Image,ImageDraw,ImageFont
    from scripts.audit_round_box_parts import draw_view,FONT
    rows=[(a,b) for a,b in zip(data['doc']['business_parts'],result['business_parts']) if a!=b]
    font=ImageFont.truetype(FONT,17)
    for offset in range(0,len(rows),4):
        image=Image.new('RGB',(1800,1600),'white');draw=ImageDraw.Draw(image)
        for j,(a,b) in enumerate(rows[offset:offset+4]):
            candidates=[c for c in a['geometry_binding'].get('candidates') or [] if safe(c)]
            boxes=[a['geometry_binding'].get('bbox'),b['geometry_binding'].get('bbox')]
            boxes.extend(c.get('bbox') for c in candidates if c.get('name_anchor_entity_id'))
            box=union(boxes)
            if box:box=[box[0]-70,box[1]-70,box[2]+70,box[3]+70]
            for col,row in enumerate((a,b)):
                x=col*900;y=j*400;binding=row['geometry_binding']
                draw.text((x+8,y+5),('原结果 ' if col==0 else '离线实验 ')+row['business_part_code']+' '+row['name'],font=font,fill='black')
                draw.text((x+8,y+29),'图元 '+str(len(binding.get('entity_ids') or []))+' / 材料组成 '+str(len(row.get('sections') or [])),font=font,fill='black')
                tile=Image.new('RGB',(898,344),'white')
                draw_view(ImageDraw.Draw(tile),(0,0,898,344),box,data['ir']['entities'],data['ir']['texts'],set(binding.get('entity_ids') or []))
                image.paste(tile,(x+1,y+55))
        image.save(out/f'comparison-{offset+1:02d}.png')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wine-dxf',type=Path,required=True);parser.add_argument('--round-dxf',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True);os.environ['PACKAGING_PART_MODEL_REVIEW']='off'
    inputs={}
    for name,path in [('wine',args.wine_dxf),('round',args.round_dxf)]:
        print('BASELINE',name,flush=True);inputs[name]=parse(path)
        (args.out/f'{name}-baseline.json').write_text(json.dumps(inputs[name],ensure_ascii=False))
    summary={}
    for variant in ('enclosure','topology','combined','caption_union'):
        summary[variant]={}
        for name,data in inputs.items():
            result=experiment(data,variant);diff=differences(data['doc'],result)
            summary[variant][name]={'baseline_part_total':len(data['doc']['business_parts']),
                                   'changed_parts':len(diff),'changes':diff,
                                   'all_part_fields_identical':data['doc']['business_parts']==result['business_parts'],
                                   'baseline_parts_hash':digest(data['doc']['business_parts']),
                                   'experimental_parts_hash':digest(result['business_parts'])}
            (args.out/f'{name}-{variant}.json').write_text(json.dumps({'experiment_only':True,'variant':variant,'doc':result},ensure_ascii=False))
        summary[variant]['wine_freeze_pass']=summary[variant]['wine']['all_part_fields_identical']
        print(variant,'wine changes',summary[variant]['wine']['changed_parts'],'round changes',summary[variant]['round']['changed_parts'],flush=True)
        if variant=='combined':render_comparison(inputs['round'],result,args.out)
        if variant=='caption_union':
            broader=args.out/'broad-comparison';broader.mkdir(exist_ok=True)
            render_comparison(inputs['round'],result,broader)
    (args.out/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
