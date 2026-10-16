"""当前业务零件的连接证据与覆盖；不读CAD、不猜几何、不依赖样例零件编号。"""
import copy
import hashlib
import re

POLICY='assembly_connections/1'


def candidate_pairs(doc):
    """Weak lexical role hints for the model, never accepted connections or chargeable steps."""
    rows=doc.get('business_parts') or []
    result=[]
    for face in rows:
        name=re.sub(r'\s+','',str(face.get('name') or ''))
        match=re.fullmatch(r'(.+?)(?:面纸|衬纸|包纸|贴纸)\d*',name)
        if not match:continue
        stem=match.group(1)
        supports=[]
        for backing in rows:
            other=re.sub(r'\s+','',str(backing.get('name') or ''))
            support=re.fullmatch(r'(.+?)(?:灰板|衬板|底板|纸板)\d*',other)
            if backing is face:continue
            if (support and support.group(1)==stem) or other==stem:
                supports.append(backing)
        # Several plausible layers are ambiguous. Do not turn pairwise combinations into glue facts.
        if len(supports)!=1:continue
        backing=supports[0]
        result.append({'part_codes':[face['business_part_code'],backing['business_part_code']],
            'name':str(face.get('name'))+' / '+str(backing.get('name')),
            'evidence':'名称属于同一构件，分别为纸面和承托；只提示检查可能的裱合关系',
            'source':'name_role_candidate','confidence':.4,'needs_model_judgement':True})
    return result


def generic(name):
    return bool(re.fullmatch(r'(?:整机|整盒|整体|全部零件)?(?:总装|组装|组装成盒)(?:[（(].*[）)])?',str(name).strip()))


def method_key(text):
    for words,key in ((('裱','包覆'),'mount'),(('胶','粘'),'glue'),(('焊',),'weld'),(('螺','铆'),'fasten'),(('卡扣',),'snap')):
        if any(w in str(text) for w in words):return key
    return str(text or '').strip()


def relation_id(name,codes,method):
    return 'JOIN-'+hashlib.sha256(('|'.join(sorted(codes))+'|'+name+'|'+method).encode()).hexdigest()[:12]


def existing_coverage(connection,doc,records):
    names={r['business_part_code']:r.get('name') or '' for r in doc.get('business_parts') or []}
    wanted=set(connection['part_codes']);method=method_key(connection['method'])
    for code,record in records.items():
        for step in (record.get('plan') or {}).get('steps') or []:
            text=str(step.get('name') or '')+' '+str(step.get('description') or '')
            if generic(step.get('name')):continue
            bound=set(step.get('part_codes') or [])
            # 无显式编码时，只有不同的完整名称都在实际工序文本中出现才认领；不只靠同一个“裱合”词。
            labels=[names[c] for c in wanted]
            nested=any(a in b for a in labels for b in labels if a!=b)
            named=all(labels) and len(set(labels))==len(labels) and not nested and all(n in text for n in labels)
            if (bound==wanted or named) and (not method or method==method_key(text)):
                return {'part_code':code,'step_no':step.get('step_no'),'name':step.get('name')}
    return None


def known(doc,interfaces,records):
    rows=doc.get('business_parts') or [];allowed={r['business_part_code'] for r in rows}
    aliases={}
    for row in rows:
        if row.get('name'):aliases.setdefault(row['name'],[]).append(row['business_part_code'])
    result=[];unresolved=[];seen=set()
    for interface in interfaces or []:
        value=interface.model_dump() if hasattr(interface,'model_dump') else interface
        codes=[p if p in allowed else (aliases.get(p) or [None])[0] if len(aliases.get(p) or [])==1 else None for p in value.get('parts') or []]
        if len(codes)<2 or None in codes or len(set(codes))!=len(codes):
            unresolved.append('接口无法唯一绑定当前零件：'+str(value.get('name') or '未命名接口'));continue
        name=value.get('name') or '零件连接';method=value.get('method') or ''
        if not method:unresolved.append('接口“'+name+'”未说明连接方式，仅作连接估算')
        key=(tuple(sorted(codes)),method_key(method))
        if key in seen:continue
        seen.add(key)
        relation={'id':relation_id(name,codes,method),'name':name,'method':method,'part_codes':codes,
            'evidence':value.get('control') or value.get('note') or '已有组装接口记录',
            'source':'existing_interface','confidence':.8}
        covered=existing_coverage(relation,doc,records)
        if covered:relation.update(status='manufactured',manufacturing_evidence=covered)
        else:relation['status']='required'
        result.append(relation)
    return result,unresolved


def enrich(plan,doc,records,known_relations):
    """一次模型输出的关系与工序归一；缺失的有依据连接逐项补齐，不造全局总装。"""
    from ..models.process import ProcessStep,ProcessType
    allowed={r['business_part_code'] for r in doc.get('business_parts') or []}
    connections=copy.deepcopy(known_relations);by_id={r['id']:r for r in connections}
    for raw in plan.assembly_connections:
        if raw.get('id') in by_id:
            if set(raw.get('part_codes') or [])!=set(by_id[raw['id']]['part_codes']):raise ValueError('assembly_connection_binding_invalid')
            if raw.get('method') and by_id[raw['id']]['method'] and method_key(raw['method'])!=method_key(by_id[raw['id']]['method']):
                raise ValueError('assembly_connection_method_conflict')
            continue
        codes=raw.get('part_codes') or []
        if not isinstance(codes,list) or len(codes)!=2 or len(set(codes))!=2 or not set(codes)<=allowed or not str(raw.get('evidence') or '').strip():
            raise ValueError('assembly_connection_evidence_invalid')
        name=str(raw.get('name') or '');method=str(raw.get('method') or '')
        if not name or generic(name):raise ValueError('assembly_connection_too_generic')
        # 同一连接不得因模型另起ID再计费；随后把旧ID映射到同一关系。
        duplicate=next((r for r in connections if set(r['part_codes'])==set(codes) and method_key(r['method'])==method_key(method)),None)
        identifier=str(raw.get('id') or relation_id(name,codes,method))
        if duplicate:
            by_id[identifier]=duplicate;continue
        relation={'id':identifier,'name':name,'method':method,'part_codes':codes,'evidence':str(raw['evidence']),
            'source':'ai_inferred','confidence':.6,'status':'required'}
        covered=existing_coverage(relation,doc,records)
        if covered:relation.update(status='manufactured',manufacturing_evidence=covered)
        connections.append(relation);by_id[identifier]=relation
    steps=[];covered=set();actions=set()
    for step in plan.steps:
        if not set(step.part_codes)<=allowed:raise ValueError('assembly_part_binding_invalid')
        if re.fullmatch(r'(?:工艺|工序)\s*\d+',step.name or ''):
            linked=[by_id[i] for i in step.assembly_connection_ids if i in by_id]
            if not linked:
                linked=[r for r in connections if set(r['part_codes'])==set(step.part_codes)]
            if len(linked)==1:step.name=linked[0]['name'] or linked[0]['method']
        if step.type==ProcessType.inspection:steps.append(step);continue
        if generic(step.name):continue
        ids=step.assembly_connection_ids
        matching=[by_id[i] for i in ids if i in by_id]
        if ids and len(matching)!=len(ids):raise ValueError('assembly_connection_unknown')
        if not matching:
            matching=[r for r in connections if set(r['part_codes'])==set(step.part_codes)]
            specific=[r for r in matching if method_key(r['method'])==method_key(step.name)]
            if specific:matching=specific
            elif len(matching)>1:raise ValueError('assembly_connection_method_ambiguous')
        if not matching and len(step.part_codes)==2:
            name=step.name;method=step.name
            relation={'id':relation_id(name,step.part_codes,method),'name':name,'method':method,
                'part_codes':list(step.part_codes),'evidence':step.description or name,
                'source':'ai_inferred','confidence':min(step.confidence,.6),'status':'required'}
            manufactured=existing_coverage(relation,doc,records)
            if manufactured:relation.update(status='manufactured',manufacturing_evidence=manufactured)
            connections.append(relation);matching=[relation];by_id[relation['id']]=relation
        matching=[r for r in matching if r['status']!='manufactured']
        if not matching:continue
        if any(set(r['part_codes'])!=set(step.part_codes) for r in matching):
            raise ValueError('assembly_connection_binding_invalid')
        keys={(r['id'],method_key(step.name)) for r in matching}
        if keys & actions:continue
        step.assembly_connection_ids=list(dict.fromkeys(r['id'] for r in matching))
        step.recommendation={'kind':'ai','reason':'根据当前零件与连接关系推荐；推断不冒充CAD证据或标准工艺'}
        steps.append(step);covered.update(step.assembly_connection_ids);actions.update(keys)
    for relation in connections:
        if relation['status']=='manufactured' or relation['id'] in covered:continue
        names={r['business_part_code']:r.get('name') or r['business_part_code'] for r in doc.get('business_parts') or []}
        action=relation['method'] or '连接'
        title='与'.join(names[c] for c in relation['part_codes'])+'：'+action
        steps.append(ProcessStep(step_no=max((s.step_no for s in steps),default=0)+10,name=title,type=ProcessType.assembly,
            description=relation['name']+'；'+relation['evidence'],part_codes=relation['part_codes'],
            assembly_connection_ids=[relation['id']],confidence=min(relation['confidence'],.6),
            recommendation={'kind':'rule' if relation['source']=='existing_interface' else 'ai',
                'reason':'按已知连接逐项编制估算' if relation['source']=='existing_interface' else '按AI推断连接编制估算，非标准工艺'}))
    original=[step.step_no for step in steps]
    mapping={old:(index+1)*10 for index,old in enumerate(original) if original.count(old)==1}
    for index,step in enumerate(steps):
        step.step_no=(index+1)*10
        step.depends_on=[mapping[n] for n in step.depends_on if n in mapping and mapping[n]<step.step_no]
    plan.steps=steps;plan.assembly_connections=connections
    return plan


def validate_coverage(plan):
    from ..models.process import ProcessType
    by_id={r.get('id'):r for r in plan.assembly_connections}
    if len(by_id)!=len(plan.assembly_connections):raise ValueError('assembly_connection_duplicated')
    covered=set();actions=set()
    for step in plan.steps:
        if step.type==ProcessType.inspection:continue
        if generic(step.name):raise ValueError('assembly_result_too_generic')
        if not step.assembly_connection_ids:raise ValueError('assembly_connection_coverage_missing')
        for identifier in step.assembly_connection_ids:
            relation=by_id.get(identifier)
            if not relation or set(relation['part_codes'])!=set(step.part_codes):raise ValueError('assembly_connection_binding_invalid')
            action=(identifier,method_key(step.name))
            if relation.get('status')=='manufactured' or action in actions:raise ValueError('assembly_connection_repeated')
            actions.add(action)
            covered.add(identifier)
    if any(r.get('status')!='manufactured' and r.get('id') not in covered for r in plan.assembly_connections):
        raise ValueError('assembly_connection_coverage_missing')
