"""Offline visual audit; never opens a project store, workbook, model or database.

Example: python scripts/audit_round_box_parts.py --dxf /tmp/圆盘盒.dxf --out /tmp/audit
Writes audit artifacts only beneath --out. Production recognition is not modified.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont
from tech_app.backend.services import packaging_parts, packaging_business_part_resolver as resolver
from tech_app.backend.services import packaging_part_visual_review as visual
from tech_app.backend.services.cad_ir.parser import parse_dxf

FONT = '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'


def box_union(boxes):
    boxes = [b for b in boxes if b and len(b) == 4]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None


def draw_view(draw, rectangle, box, entities, texts, selected=None, labels=True):
    if not box:
        return
    x0,y0,x1,y1=rectangle
    pad=16
    scale=min((x1-x0-2*pad)/max(1,box[2]-box[0]),(y1-y0-2*pad)/max(1,box[3]-box[1]))
    cx=(x0+x1)/2;cy=(y0+y1)/2;bx=(box[0]+box[2])/2;by=(box[1]+box[3])/2
    def point(p):return (cx+(p[0]-bx)*scale,cy-(p[1]-by)*scale)
    for entity in entities:
        bounds=entity.get('bbox')
        if not bounds or bounds[2]<box[0] or bounds[0]>box[2] or bounds[3]<box[1] or bounds[1]>box[3]:continue
        points=visual._entity_points(entity)
        if len(points)<2:continue
        active=selected is None or entity['entity_id'] in selected
        color=visual._color(entity) if active else '#d4d9df'
        draw.line([point(p) for p in points],fill=color,width=2 if selected and active else 1)
    if labels:
        font=ImageFont.truetype(FONT,12)
        for text in texts:
            pos=text.get('position') or []
            if len(pos)>1 and box[0]<=pos[0]<=box[2] and box[1]<=pos[1]<=box[3]:
                content=str(text.get('normalized_text') or text.get('raw_text') or '')
                draw.text(point(pos),content[:65],font=font,fill='#0f172a')


def main():
    args=argparse.ArgumentParser();args.add_argument('--dxf',type=Path,required=True);args.add_argument('--out',type=Path,required=True)
    options=args.parse_args();options.out.mkdir(parents=True,exist_ok=True)
    os.environ['PACKAGING_PART_MODEL_REVIEW']='off'
    start=time.monotonic()
    ir=parse_dxf(options.dxf.read_bytes(),filename=options.dxf.name)
    print('CAD',len(ir['entities']),'entities;',len(ir['texts']),'texts',flush=True)
    entities=ir['entities'];texts=ir['texts']
    (options.out/'raw-texts.json').write_text(json.dumps(texts,ensure_ascii=False,indent=2))
    geometry=packaging_parts.extract(ir)
    print('GEOMETRY',len(geometry.get('parts') or []),flush=True)
    resolved=resolver.resolve_business_parts('offline-round-audit',ir,geometry,use_model=True,strict_schemes=True)
    doc=packaging_parts.business_parts_document(resolved['reference'],geometry,bindings=resolved['match'])
    (options.out/'fresh-result.json').write_text(json.dumps({'ir':ir,'geometry':geometry,'resolved':resolved,'doc':doc},ensure_ascii=False))
    anchor_by_id={t['entity_id']:t for t in texts}
    records=[]
    rows=doc.get('business_parts') or []
    for index,row in enumerate(rows):
        binding=row.get('geometry_binding') or {};ref=row.get('reference') or {};sections=row.get('sections') or []
        evidence=ref.get('evidence') or {}
        ids=evidence.get('anchor_entity_ids') or []
        if not ids:ids=(row.get('authority') or {}).get('evidence',{}).get('anchor_entity_ids') or []
        anchors=[anchor_by_id[eid] for eid in ids if eid in anchor_by_id]
        exact=[t for t in texts if row.get('name') and str(t.get('normalized_text') or '').startswith(row['name'])]
        anchor_rows = anchors or exact
        scope = next((s for s in doc.get('schemes') or [] if s.get('scheme_id') == row.get('scheme_id')), {})
        anchor_rows = [t for t in anchor_rows if not scope or scope['y_min'] <= t['position'][1] < scope['y_max']]
        box = binding.get('bbox')
        if box and anchor_rows:
            anchor_rows = [min(anchor_rows,key=lambda t:math.hypot(t['position'][0]-(box[0]+box[2])/2,
                                                                   t['position'][1]-(box[1]+box[3])/2))]
        record={'index':index+1,'id':row.get('business_part_code'),'scheme':row.get('scheme_id'),'name':row.get('name'),
                'status':binding.get('status'),'bbox':binding.get('bbox'),
                'dimensions':[binding.get('length_mm'),binding.get('width_mm')],
                'reference_dimensions':[ref.get('length_mm'),ref.get('width_mm')],
                'entity_total':len(binding.get('entity_ids') or []),'candidate_id':binding.get('candidate_id'),
                'sections':[{'name':s.get('name'),'bbox':s.get('bbox'),'estimated_size':s.get('estimated_size'),
                             'size':s.get('size'),'entity_total':len(s.get('entity_ids') or [])} for s in sections],
                'anchor_texts':[{k:t.get(k) for k in ('entity_id','normalized_text','position')} for t in anchor_rows],
                'section_gaps':row.get('section_gaps'),'size_confirmed':binding.get('size_confirmed')}
        records.append(record)
        print(json.dumps(record,ensure_ascii=False),flush=True)
    (options.out/'part-summary.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
    # Raw full drawing first; no predicted boundaries in this reference image.
    font=ImageFont.truetype(FONT,18)
    extents=box_union([e.get('bbox') for e in entities])
    overview=Image.new('RGB',(2600,1500),'white');draw=ImageDraw.Draw(overview)
    draw_view(draw,(0,40,2600,1500),extents,entities,texts)
    draw.text((20,10),'圆盘盒原始图：未叠加预测边界',font=font,fill='black');overview.save(options.out/'raw-overview.png')
    schemes=doc.get('schemes') or []
    for scheme in schemes:
        print('SCHEME',json.dumps(scheme,ensure_ascii=False),flush=True)
    # Audit tiles retain original positions and surrounding labels; no normalized-only previews.
    for offset in range(0,len(rows),6):
        image=Image.new('RGB',(1800,1500),'white');draw=ImageDraw.Draw(image)
        for j,row in enumerate(rows[offset:offset+6]):
            record=records[offset+j];binding=row.get('geometry_binding') or {};box=binding.get('bbox')
            anchors=record['anchor_texts'];positions=[t.get('position') for t in anchors if len(t.get('position') or [])>1]
            anchor_box=box_union([[p[0],p[1],p[0],p[1]] for p in positions])
            context=box_union([box,anchor_box])
            if context:
                margin=max(100,min(300,max(context[2]-context[0],context[3]-context[1])*.18))
                context=[context[0]-margin,context[1]-margin,context[2]+margin,context[3]+margin]
            x=(j%2)*900;y=(j//2)*500
            draw.rectangle((x,y,x+899,y+499),outline='#94a3b8')
            draw.text((x+10,y+5),f"{offset+j+1:02d} {row.get('scheme_id')} {row.get('name')}",font=font,fill='black')
            draw.text((x+10,y+30),f"{binding.get('status')} / {record['dimensions']} / {len(record['sections'])}组成",font=font,fill='black')
            tile=Image.new('RGB',(898,438),'white')
            draw_view(ImageDraw.Draw(tile),(0,0,898,438),context,entities,texts,set(binding.get('entity_ids') or []))
            image.paste(tile,(x+1,y+61))
        image.save(options.out/f'parts-{offset+1:02d}-{min(offset+6,len(rows)):02d}.png')
    print('DONE',round(time.monotonic()-start,2),'seconds;',options.out,flush=True)


if __name__=='__main__':main()
