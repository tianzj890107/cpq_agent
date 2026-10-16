"""CAD-derived packaging layout evidence; never a BOM or cost authority."""
from __future__ import annotations

import re
import copy
from typing import Any


_N_UP = re.compile(r"(?:各\s*)?排\s*([1-9]\d*)\s*模")
_SHEET = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[*×xX]\s*(\d+(?:\.\d+)?)(?!\s*[*×xX]\s*\d)(?:\s*mm)?", re.I)
_SCHEME = re.compile(r"(?:内托)?方案\s*([1-9]\d*)")
_PAIRED_NAME = re.compile(r"^(.*?)(内|左|上|前)、(外|右|下|后)(.+)$")


def sheet_matches(text):
    # 三维成品尺寸不是二维纸张；不能从 200×100×80 中截出 100×80。
    triples=[m.span() for m in re.finditer(r'\d+(?:\.\d+)?\s*[*×xX]\s*\d+(?:\.\d+)?\s*[*×xX]\s*\d+(?:\.\d+)?',text)]
    return [m for m in _SHEET.finditer(text) if not any(m.start()<b and m.end()>a for a,b in triples)]


def clean_cad_text(raw: Any) -> str:
    text = str(raw or "").replace(r"\P", "\n").replace(r"\~", " ")
    text = re.sub(r"\\[CcFfHhWwQqTtAa][^;]*;", "", text)
    return re.sub(r"[{}]|\\[LlOoKk]", "", text).strip()


def layout_record(entity_id: str, raw: str) -> dict:
    text = clean_cad_text(raw)
    n_up, sheet = _N_UP.search(text), next(iter(sheet_matches(text)),None)
    return {"entity_id": entity_id, "raw_text": raw, "text": text,
            "n_up": int(n_up.group(1)) if n_up else None,
            "sheet_mm": [float(sheet.group(1)), float(sheet.group(2))] if sheet else None,
            "confirmed": False, "part_codes": [], "rule_id": "cad_layout_instruction_v1"}


def is_layout_instruction(value: Any) -> bool:
    """The manufacturing instruction, not its location, decides the text role."""
    return bool(_N_UP.search(clean_cad_text(value)))


def extract_layout_rows(cad_ir: Any) -> list[dict]:
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    rows = []
    for text in ir.get("texts") or []:
        if not isinstance(text, dict):
            continue
        raw = str(text.get("raw_text") or text.get("normalized_text") or "")
        clean = clean_cad_text(raw)
        n_up = _N_UP.search(clean)
        if not n_up:
            continue
        sheet = next(iter(sheet_matches(clean)),None)
        cut_size=machine_size=None
        for match in sheet_matches(clean):
            prefix=clean[:match.start()]
            machine_pos=max((m.start() for m in re.finditer(r'上机|机台',prefix)),default=-1)
            cut_pos=max((m.start() for m in re.finditer(r'开料|纸张|纸料',prefix)),default=-1)
            size=[float(match.group(1)),float(match.group(2))]
            if machine_pos>cut_pos:machine_size=size
            elif cut_size is None:cut_size=size
        rows.append({
            **layout_record(str(text.get("entity_id") or ""), raw),
            "entity_id": str(text.get("entity_id") or ""),
            "raw_text": raw,
            "position": list(text.get("position") or []),
            "layer": str(text.get("layer") or ""),
            "n_up": int(n_up.group(1)),
            "sheet_mm": cut_size,
            "machine_mm": machine_size,
            "confirmed": False,
            "part_codes": [],  # compound text alone cannot prove each part's allocation
            "rule_id": "cad_layout_instruction_v1",
            "kind": "layout_instruction",
            "size_source": "cad_layout_text" if sheet else "missing",
            "dimension_role": "cut_sheet" if cut_size else "machine_sheet" if machine_size else "unclassified",
        })
    rows=bind_layout_regions(ir,rows)
    return sorted(rows, key=lambda row: (-((row.get('position') or [])+[0,0])[1],((row.get('position') or [])+[0,0])[0],row['entity_id']))


def bind_layout_regions(ir, rows):
    """只有独占的矩形框才作局部证据；不以最近的任意线段冒充排模。"""
    result=[dict(r) for r in rows]
    frames=[]
    for entity in ir.get('entities') or []:
        box=entity.get('bbox') or []
        points=(entity.get('attributes') or {}).get('points') or entity.get('points') or []
        if entity.get('kind')!='polyline' or not entity.get('closed') or len(box)!=4 or len(points) not in (4,5):continue
        corners={(round(p[0],3),round(p[1],3)) for p in points}
        if corners!={(round(x,3),round(y,3)) for x in (box[0],box[2]) for y in (box[1],box[3])}:continue
        frames.append(entity)
    def distance(pos,box):
        return max(box[0]-pos[0],0,pos[0]-box[2])**2+max(box[1]-pos[1],0,pos[1]-box[3])**2
    claims={}
    for row in result:
        pos=row.get('position') or []
        if len(pos)<2:continue
        eligible=[]
        for frame in frames:
            b=frame['bbox'];span=max(b[2]-b[0],b[3]-b[1])
            if span<=0 or distance(pos,b)>(span*.25)**2:continue
            nearer=[r for r in rows if r is not row and r.get('entity_id')!=row.get('entity_id') and len(r.get('position') or [])>=2
                    and distance(r['position'],b)<=distance(pos,b)]
            if nearer:continue
            eligible.append(frame)
        eligible.sort(key=lambda f:((f['bbox'][2]-f['bbox'][0])*(f['bbox'][3]-f['bbox'][1]),distance(pos,f['bbox'])))
        if eligible:
            frame=eligible[0];row['preview_bbox']=list(frame['bbox']);row['preview_region_source']='cad_rectangular_frame'
            row['preview_frame_entity_id']=frame.get('entity_id');claims.setdefault(frame.get('entity_id'),[]).append(row)
    for group in claims.values():
        if len(group)>1:
            for row in group:
                for key in ('preview_bbox','preview_region_source','preview_frame_entity_id'):row.pop(key,None)
    return result


def attach_cut_dimension_candidates(ir, rows):
    """保存原标注见证点与位置；近邻不是开料尺寸的角色证据。"""
    import math
    from .packaging_business_part_resolver import dimension_rects
    rects=dimension_rects(ir)
    dimension_text={d.get('entity_id'):clean_cad_text(d.get('raw_text') or d.get('normalized_text'))
                    for d in ir.get('dimensions') or []}
    result=bind_layout_regions(ir,rows)
    for row in result:
        pos=row.get('position') or []
        if len(pos)<2: continue
        found=[]
        for rect in sorted(rects,key=lambda r:math.hypot(r['center'][0]-pos[0],r['center'][1]-pos[1])):
            region=row.get('preview_bbox')
            if region and not (region[0]<=rect['center'][0]<=region[2] and region[1]<=rect['center'][1]<=region[3]):continue
            if len(rect.get('witness_points') or [])<3: continue
            if any(abs(c['length_mm']-rect['length_mm'])<.01 and abs(c['width_mm']-rect['width_mm'])<.01 for c in found): continue
            ids=rect['dimension_ref'].removeprefix('dim:').split('|')
            role='cut_sheet' if all(re.search(r'开料|纸张|纸料',dimension_text.get(e,'')) for e in ids) else 'unclassified'
            found.append(dict(rect,dimension_role=role,size_source='cad_dimension',entity_ids=ids))
            if len(found)==3: break
        row['cut_dimension_candidates']=found
        explicit=[c for c in found if c['dimension_role']=='cut_sheet' and math.hypot(
            c['center'][0]-pos[0],c['center'][1]-pos[1])<=math.hypot(c['length_mm'],c['width_mm'])]
        if not row.get('sheet_mm') and len(explicit)==1:
            row.update(sheet_mm=[explicit[0]['length_mm'],explicit[0]['width_mm']],
                       size_source='cad_cut_sheet_dimension',dimension_role='cut_sheet',
                       cut_dimension_entity_ids=explicit[0]['entity_ids'])
    return result


def set_cut_inputs(doc, entity_id, inputs, actor):
    from .packaging_sheet_cost import positive
    result=copy.deepcopy(doc)
    row=next((r for r in result.get('layout_rows') or [] if r['entity_id']==entity_id),None)
    if not row: raise ValueError('layout_entity_not_found')
    if not isinstance(inputs,dict): raise ValueError('layout_cost_input_invalid')
    size=inputs.get('sheet_mm') or []
    counts=inputs.get('part_counts') or {}
    if not isinstance(size,(list,tuple)) or not isinstance(counts,dict): raise ValueError('layout_cost_input_invalid')
    if not actor or len(size)!=2 or not all(positive(v) for v in size): raise ValueError('layout_sheet_size_missing')
    if not row.get('part_codes') or set(counts)!=set(row['part_codes']) or any(
            not positive(v) or not positive(v).is_integer() for v in counts.values()):
        raise ValueError('layout_part_count_missing')
    row.update(sheet_mm=[float(v) for v in size],part_counts={k:int(v) for k,v in counts.items()},
               size_source='user_verified_cut_sheet',dimension_role='cut_sheet',kind='layout_instruction',
               cut_input_actor=actor,association_gaps=[],assignment_source='user_selection',assignment_actor=actor)
    matched=[c for c in row.get('cut_dimension_candidates') or [] if abs(c['length_mm']-row['sheet_mm'][0])<.01 and abs(c['width_mm']-row['sheet_mm'][1])<.01]
    row['cut_dimension_entity_ids']=matched[0]['entity_ids'] if len(matched)==1 else []
    return result


def cut_dimension_svg(ir, candidate):
    """真实CAD局部及DIMENSION显示图元，不生成假纸张边界。"""
    from html import escape
    from . import packaging_parts
    ids=set(candidate.get('entity_ids') or [])
    displays=[e for d in ir.get('dimensions') or [] if d.get('entity_id') in ids for e in d.get('display_entities') or []]
    points=candidate.get('witness_points') or []
    boxes=[e['bbox'] for e in displays if e.get('bbox')]
    xs=[p[0] for p in points]+[b[i] for b in boxes for i in (0,2)]
    ys=[p[1] for p in points]+[b[i] for b in boxes for i in (1,3)]
    if not xs or not ys: raise ValueError('layout_dimension_location_missing')
    original=[min(xs),min(ys),max(xs),max(ys)]
    reach=max(original[2]-original[0],original[3]-original[1],1)*1.5
    for component in (ir.get('geometry') or {}).get('components') or []:
        b=component.get('bbox') or []
        if len(b)!=4 or max(b[2]-b[0],b[3]-b[1])>reach: continue
        if b[2]>=original[0] and b[0]<=original[2] and b[3]>=original[1] and b[1]<=original[3]:
            xs.extend([b[0],b[2]]);ys.extend([b[1],b[3]])
    pad=max(max(xs)-min(xs),max(ys)-min(ys),1)*.12
    box=[min(xs)-pad,min(ys)-pad,max(xs)+pad,max(ys)+pad]
    width,height=box[2]-box[0],box[3]-box[1]
    def number(v): return format(float(v),'.6f')
    elements=[]
    for entity in ir.get('entities') or []:
        b=entity.get('bbox') or []
        if len(b)!=4 or b[2]<box[0] or b[0]>box[2] or b[3]<box[1] or b[1]>box[3]: continue
        chains,_,_=packaging_parts._entity_chains(entity)
        for chain in chains or []:
            elements.append('<polyline points="'+' '.join(number(x)+','+number(-y) for x,y in chain)+'" fill="none" stroke="#64748b" vector-effect="non-scaling-stroke"/>')
    for entity in displays:
        pts=entity.get('points') or []
        if entity.get('kind')=='text':
            elements.append('<text x="'+number(entity.get('x',0))+'" y="'+number(-entity.get('y',0))+'" font-size="'+number(entity.get('height') or height*.03)+'" fill="#2563eb">'+escape(str(entity.get('text') or ''))+'</text>')
        elif pts:
            elements.append('<polyline points="'+' '.join(number(p[0])+','+number(-p[1]) for p in pts)+'" fill="none" stroke="#2563eb" vector-effect="non-scaling-stroke"/>')
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="'+' '.join(map(number,(box[0],-box[3],width,height)))+'">'+''.join(elements)+'</svg>'


def scheme_labels(cad_ir: Any) -> list[str]:
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    labels = set()
    for text in ir.get("texts") or []:
        if isinstance(text, dict):
            match = _SCHEME.search(str(text.get("raw_text") or text.get("normalized_text") or ""))
            if match:
                labels.add(match.group(1))
    return sorted(labels)


def scheme_scopes(ir: Any, regions: Any = None, anchors: Any = None) -> list[dict]:
    """标题和局部空白带给出候选作用域；没有可信边界时不伪称确认。"""
    headers = []
    for row in (ir or {}).get('texts') or []:
        text = clean_cad_text(row.get('raw_text') or row.get('normalized_text'))
        match = _SCHEME.search(text)
        position = row.get('position') or []
        if match and len(position) >= 2:
            headers.append({'scheme_id': match.group(1), 'title': text,
                            'entity_id': row.get('entity_id'), 'position': position,
                            'kind': 'layout' if re.search(r'排模|排版|拼版', text) else 'component_variant'})
    unique = {row['scheme_id']: row for row in headers}
    if len(unique) > 1 and max(r['position'][0] for r in unique.values())-min(r['position'][0] for r in unique.values()) \
            > max(r['position'][1] for r in unique.values())-min(r['position'][1] for r in unique.values()):
        # 左右排布旋转到同一阅读坐标系复用空白带算法，不写行业样图方向。
        def rotate(row):
            result = dict(row)
            pos,box = row.get('position') or [],row.get('bbox') or []
            if len(pos)>=2: result['position']=[pos[1],-pos[0]]
            if len(box)==4: result['bbox']=[box[1],-box[2],box[3],-box[0]]
            return result
        rotated = scheme_scopes(dict(ir,texts=[rotate(r) for r in ir.get('texts') or []]),
                                [rotate(r) for r in regions or []],[rotate(a) for a in anchors or []])
        for scope in rotated:
            scope['position'] = unique[scope['scheme_id']]['position']
            scope.update(coordinate_axis=0,coordinate_sign=-1)
        return rotated
    headers = sorted(unique.values(), key=lambda row: -float(row['position'][1]))
    if len(headers) < 2:
        return headers
    boundaries = []
    for top, bottom in zip(headers, headers[1:]):
        high, low = float(top['position'][1]), float(bottom['position'][1])
        if high <= low:
            raise ValueError('packaging_scheme_scope_ambiguous: 方案标题未形成可区分的空间区域')
        # 件名行之间的最大空白是候选分隔线，不使用样图坐标。
        ys = sorted({low, high} | {float(a['position'][1]) for a in (anchors or [])
                    if not a.get('excluded') and len(a.get('position') or []) >= 2
                    and low < float(a['position'][1]) < high})
        a, b = max(zip(ys, ys[1:]), key=lambda pair: pair[1] - pair[0])
        boundary = (a + b) / 2
        # 优先把分隔线放到实际几何空白带；大图框不是业务件边界证据。
        intervals = []
        for region in regions or []:
            box = region.get('bbox') or []
            if len(box) == 4 and 0 < box[3] - box[1] < high - low:
                intervals.append((max(low, box[1]), min(high, box[3])))
        merged = []
        for start, end in sorted((x,y) for x,y in intervals if x < y):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start,end])
        gaps = [(left[1], right[0]) for left,right in zip(merged,merged[1:])
                if right[0] > left[1] and a <= (left[1]+right[0])/2 <= b]
        if gaps:
            start,end = min(gaps,key=lambda pair: abs((pair[0]+pair[1])/2-boundary))
            boundary = (start+end)/2
        boundaries.append(boundary)
    spacing = max(abs(float(a['position'][1])-float(b['position'][1])) for a,b in zip(headers,headers[1:]))
    for index, header in enumerate(headers):
        header.update(y_max=boundaries[index-1] if index else float(header['position'][1])+spacing,
                      y_min=boundaries[index] if index < len(boundaries) else float(header['position'][1])-spacing,
                      scope_status='inferred_from_drawing', rule_id='scheme_title_and_spatial_gap/1',
                      coordinate_axis=1,coordinate_sign=1)
    return headers


def select_scheme(doc: Any, scheme_id: str) -> dict:
    result = copy.deepcopy(doc)
    if str(scheme_id) not in (result.get('scheme_labels') or []):
        raise ValueError('packaging_scheme_not_found')
    result['selected_scheme_id'] = str(scheme_id)
    return result


def active_business_document(doc: Any) -> dict:
    # 只读投影不复制几十 MB 的 CAD 图元；只替换列表，不修改输入行。
    result = dict(doc or {})
    labels = result.get('scheme_labels') or []
    if len(labels) < 2:
        return result
    selected = result.get('selected_scheme_id')
    if selected not in labels:
        raise ValueError('packaging_scheme_selection_required: 请先选择采用的方案，不能混算')
    result['business_parts'] = [r for r in result.get('business_parts') or []
                                if r.get('scheme_id') in (selected,'common')]
    result['layout_rows'] = [r for r in result.get('layout_rows') or []
                             if r.get('scheme_id') in (selected,'common')]
    return result


def extract_drawing_layout_candidates(ir, anchors, regions=None):
    """重复件名在主图框外是候选证据，不是已经确认的排模。"""
    usable=[a for a in anchors or [] if a.get('name') and not a.get('excluded') and len(a.get('position') or [])>=2]
    frames=[]
    def inside(a,box):
        x,y=a['position'][:2]
        return box[0]<=x<=box[2] and box[1]<=y<=box[3]
    for entity in (ir or {}).get('entities') or []:
        box=entity.get('bbox') or []
        points=(entity.get('attributes') or {}).get('points') or []
        if entity.get('kind')!='polyline' or not entity.get('closed') or len(box)!=4 or len(points) not in (4,5):
            continue
        corners={(round(p[0],4),round(p[1],4)) for p in points}
        if corners != {(round(x,4),round(y,4)) for x in (box[0],box[2]) for y in (box[1],box[3])}:
            continue
        names={a['name'] for a in usable if inside(a,box)}
        if len(names)>=2: frames.append((len(names),entity,names))
    if regions:
        from . import packaging_part_visual_review
        for frame in packaging_part_visual_review._layout_frames(ir,regions):
            names={a['name'] for a in usable if inside(a,frame['bbox'])}
            if len(names)>=2:
                frames.append((len(names),dict(frame,entity_id=frame['frame_id']),names))
    if not frames: return []
    _,frame,names=max(frames,key=lambda f:(f[0],-(f[1]['bbox'][2]-f[1]['bbox'][0])*(f[1]['bbox'][3]-f[1]['bbox'][1])))
    result=[]
    for anchor in usable:
        if anchor['name'] not in names or inside(anchor,frame['bbox']): continue
        result.append({'entity_id':anchor['entity_id'],'raw_text':anchor.get('raw_text') or anchor['name'],
            'text':anchor['name'],'position':list(anchor['position']),'kind':'drawing_layout_candidate',
            'classification':'repeated_unfolding_or_layout','confirmed':False,'n_up':None,'sheet_mm':None,
            'part_codes':[],'rule_id':'repeated_name_outside_primary_frame/1',
            'evidence_refs':[anchor['entity_id']] + list(frame.get('source_entity_ids') or [frame['entity_id']]),
            'classification_notice':'同名展开图位于主图框外，可能是排模或草稿；模数、纸张尺寸未知。'})
    return result


def _layout_name_variants(text):
    """只展开明写的并列方位，不凭关键词猜零件。"""
    value=clean_cad_text(text).split('：',1)[0].split(':',1)[0]
    value=re.sub(r'^.*?圆盒\s*','',value)
    value=re.sub(r'1/2$', '',value)
    items=[value]
    for pattern,options in ((r'盖/地',('盖','地')),(r'内、?外圈',('内圈','外圈'))):
        items=[re.sub(pattern,option,item) for item in items for option in options] if any(re.search(pattern,i) for i in items) else items
    return list(dict.fromkeys(p.strip() for item in items for p in re.split(r'[+＋]',item) if p.strip()))


def _layout_name_key(name):
    value=str(name or '').replace('盒','').replace('忖','衬')
    value=value.replace('内圈围边','内围').replace('外圈围边','外围')
    value=value.replace('内圈衬纸','内围衬纸').replace('外圈衬纸','外围衬纸')
    return {'内托面卡':'面卡','地外底贴':'地底贴面纸'}.get(value,value)


def refresh_text_sizes(row):
    """旧清单只重读本条已存文字；不重跑CAD、不覆盖人工/明确标注的开料。"""
    if row.get('kind')=='drawing_layout_candidate':return {}
    if row.get('sheet_mm') and row.get('size_source') not in ('missing','demo_horizontal_sheet_estimate'):
        return {}
    parsed=extract_layout_rows({'texts':[{'entity_id':row.get('entity_id'),
        'raw_text':row.get('raw_text') or row.get('text'),'position':row.get('position') or []}]})
    if not parsed or not parsed[0].get('sheet_mm'):return {}
    return {key:parsed[0].get(key) for key in ('sheet_mm','machine_mm','size_source','dimension_role')}


def auto_assign_layout_rows(rows, parts):
    """Full CAD names only. Association never supplies yield or approval."""
    result = copy.deepcopy(rows or [])
    names = sorted([(str(part.get('name') or '').strip(),part.get('business_part_code'))
                    for part in parts or [] if part.get('business_part_code')],
                   key=lambda item: -len(item[0]))
    for row in result:
        row.update(refresh_text_sizes(row))
        if row.get('assignment_source') == 'ai_semantic':
            from .packaging_name_assist import valid_layout_selection
            if valid_layout_selection(row, parts):
                continue
            for key in ('part_codes','part_counts','assignment_source','assignment_evidence','semantic_context'):
                row.pop(key, None)
        if row.get('assignment_source') == 'user_selection' or row.get('assignment_actor'):
            continue
        if row.get('part_codes') and row.get('assignment_source') not in ('cad_layout_full_name','cad_layout_declared_name'):
            continue  # 不改写来源不明的历史关联。
        row['part_codes']=[]
        row.pop('assignment_source',None)
        row.pop('assignment_evidence',None)
        text = clean_cad_text(row.get('text') or row.get('raw_text'))
        occupied, codes, evidence = set(), [], []
        eligible=[p for p in parts or [] if not row.get('scheme_id') or p.get('scheme_id') in (row['scheme_id'],'common')]
        gaps=[]
        for name, code in names:
            part = next((p for p in parts or [] if p.get('business_part_code') == code), {})
            if part not in eligible:
                continue
            if not name:
                continue
            for match in re.finditer(re.escape(name),text):
                positions = set(range(match.start(),match.end()))
                if positions & occupied:
                    continue
                same=[p for p in eligible if p.get('name')==name]
                if len(same)!=1:
                    gaps.append({'name':name,'code':'ambiguous_part_name'})
                    continue
                occupied.update(positions)
                if code not in codes:
                    codes.append(code)
                    evidence.append({'part_code':code,'name':name,'entity_id':row.get('entity_id'),
                                     'text':text,'source':'cad_layout_full_name'})
        # 复合简称按声明的组成名称匹配；未命中的名称保留，不创造不存在的零件。
        for mention in _layout_name_variants(text):
            hits=[]
            for part in eligible:
                declared=[part.get('name','')] + [s['name'] for s in declared_sections(part.get('name'))]
                if any(_layout_name_key(mention)==_layout_name_key(name) for name in declared): hits.append(part)
            if len(hits)==1:
                code=hits[0]['business_part_code']
                if code not in codes:
                    codes.append(code)
                    evidence.append({'part_code':code,'name':hits[0]['name'],'entity_id':row.get('entity_id'),
                                     'text':mention,'source':'cad_layout_declared_name'})
            elif not codes or len(_layout_name_variants(text))>1:
                gaps.append({'name':mention,'code':'ambiguous_part_name' if hits else 'layout_part_name_unmatched'})
        if codes:
            row.update(part_codes=codes,assignment_source='cad_layout_full_name',
                       assignment_evidence=evidence)
            if row.get('n_up') and (len(codes)==1 or re.search(r'各\s*排',text)):
                row.setdefault('part_counts',{code:row['n_up'] for code in codes})
        row['association_gaps']=gaps
    return result


def declared_sections(name: Any) -> list[dict]:
    """A text label can name two portions of one business item.

    This is textual structure only; neither portion is assigned a CAD shape.
    """
    label = str(name or "").strip()
    match = _PAIRED_NAME.match(label)
    names = ([match.group(1) + match.group(2) + match.group(4),
              match.group(1) + match.group(3) + match.group(4)] if match else [label])
    return [{"name": item, "geometry_status": "pending_attribution",
             "source": "drawing_text"} for item in names if item]


def part_fragments(component_ids: Any, components: Any) -> list[dict]:
    """Keep per-piece CAD evidence; a union bbox is not a part drawing."""
    by_id = {str(row.get("component_id") or ""): row for row in (components or [])
             if isinstance(row, dict)}
    result = []
    for component_id in dict.fromkeys(str(cid) for cid in (component_ids or []) if cid):
        row = by_id.get(component_id)
        if row is None:
            continue
        bbox = row.get("bbox")
        if not isinstance(bbox, (list, tuple)):
            outline = row.get("outline") if isinstance(row.get("outline"), dict) else {}
            bbox = outline.get("bbox")
        result.append({
            "component_id": component_id,
            "entity_ids": list(row.get("entity_ids") or []),
            "bbox": list(bbox[:4]) if isinstance(bbox, (list, tuple)) and len(bbox) >= 4 else None,
            "layers": list(row.get("layers") or []),
        })
    return result


def set_layout_confirmation(doc: Any, entity_id: str, confirmed: bool) -> dict:
    """Confirm an observed instruction, not an inferred yield or cost."""
    record = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    rows = record.get("layout_rows") or []
    target = next((row for row in rows if row.get("entity_id") == entity_id), None)
    if target is None:
        raise ValueError("layout_entity_not_found")
    if confirmed and (not (target.get("n_up") or target.get('part_counts')) or not target.get("sheet_mm")):
        raise ValueError("layout_evidence_incomplete")
    target["confirmed"] = bool(confirmed)
    return record


def set_layout_assignment(doc: Any, entity_id: str, part_codes: list, *, actor: str) -> dict:
    """Attach an observed layout to real business parts, without deriving consumption."""
    record = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    known = {row.get("business_part_code") for row in record.get("business_parts") or []
             if isinstance(row, dict)}
    child_schemes={}
    for parent in record.get('business_parts') or []:
        sections=parent.get('sections') or []
        if len(sections)>1 or parent.get('sections_source')=='manual':
            for section in sections:
                if section.get('section_id'):
                    child=str(parent.get('business_part_code'))+'::'+str(section['section_id'])
                    known.add(child);child_schemes[child]=parent.get('scheme_id')
    codes = list(dict.fromkeys(part_codes))
    if not actor or any(code not in known for code in codes):
        raise ValueError("layout_assignment_invalid")
    target = next((row for row in record.get("layout_rows") or []
                   if row.get("entity_id") == entity_id), None)
    if target is None:
        raise ValueError("layout_entity_not_found")
    if target.get('scheme_id') and any(
            row.get('business_part_code') in codes and row.get('scheme_id') not in (target['scheme_id'],'common')
            for row in record.get('business_parts') or []):
        raise ValueError('layout_assignment_scheme_conflict')
    if target.get('scheme_id') and any(code in child_schemes and child_schemes[code] not in (target['scheme_id'],'common') for code in codes):
        raise ValueError('layout_assignment_scheme_conflict')
    target["part_codes"] = codes
    target["assignment_actor"] = actor
    target["assignment_source"] = "user_selection"
    # Association is not approval of sheet size, yield, usage or price.
    target["confirmed"] = False
    return record
