"""DA routing evidence via CPQ's authenticated read-only bridge."""
import json
import urllib.parse
import urllib.request
from . import cpq_kb_client
from .packaging_part_route_match import is_external_part, normalize_part_name, rank_route_features

_EXTERNAL_NOTICE = '外购/采购件：无工艺路线，不参与路线推荐'


def _fetch(product_item_code, name):
    query=urllib.parse.urlencode({'product_item_code':product_item_code,'name':name})
    req=urllib.request.Request(cpq_kb_client._base()+'/wf/tech/process-routing?'+query,
        headers={cpq_kb_client.INTERNAL_TOKEN_HEADER:cpq_kb_client._internal_token(),
                 'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=cpq_kb_client.CPQ_KB_TIMEOUT_SECONDS) as response:
        payload=json.load(response)
    if not isinstance(payload,dict) or not payload.get('ok'):
        raise ValueError('invalid bridge response')
    return payload

def _fetch_candidates():
    req=urllib.request.Request(cpq_kb_client._base()+'/wf/tech/process-routing?mode=packaging_candidates',
        headers={cpq_kb_client.INTERNAL_TOKEN_HEADER:cpq_kb_client._internal_token(),'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=cpq_kb_client.CPQ_KB_TIMEOUT_SECONDS) as response:
        payload=json.load(response)
    if not isinstance(payload,dict) or not payload.get('ok'):
        raise ValueError('invalid candidate bridge response')
    return payload


def _similar_route(row, payload):
    try:
        response = _fetch_candidates()
        if response.get('status') != 'ok':
            return {**payload,'similarity_status':response.get('status'),'notice':'包装候选读取未完成，不能声称库内无相似路线'}
        reference = row.get('reference') or {}
        size = row.get('confirmed_size') or {}
        # Validated callers also carry a CAD/manual-derived reference; never use binding bbox.
        verified = size.get('source') in ('drawing_dimension_manual','verified_cad_dimension')
        if not verified and reference.get('size_quality') in ('unfolded','verified_cad_dimension'):
            size = reference
            verified = True
        feature = {'name':row.get('name') or row.get('business_part_name'),
                   'material_text':reference.get('material_text') or row.get('material') or '',
                   'length_mm':size.get('length_mm'),'width_mm':size.get('width_mm'),'size_verified':verified}
        ranking = rank_route_features(feature,response.get('candidates') or [])
        result = {**payload,'status':ranking['status'] if ranking['status']!='unbound' else 'not_found',
                  'match_method':'feature_similarity','similarity':ranking,
                  'notice':'相似零件工艺参考；不继承标准尺寸、工时或费率，需按本件编制与审核','routes':[]}
        if ranking['status']=='matched':
            selected = ranking['candidates'][0]
            if selected.get('route'):
                routes = [dict(selected['route'])]
            elif selected.get('code'):
                loaded = _fetch(selected['code'],'')
                if loaded.get('status') != 'matched':
                    return {**result,'status':loaded.get('status','unavailable'),'reason':'selected_route_not_unique_or_unavailable'}
                routes = [dict(route) for route in loaded.get('routes') or []]
            else:
                return {**result,'status':'not_found','reason':'candidate_missing_route_code'}
            for route in routes:
                route.update(approved=False,match_method='feature_similarity',notice=result['notice'])
            result['routes']=routes
        # 工艺借鉴不改变身份匹配：方向/编号不同仍可作为同材料参照。
        analogy = rank_route_features(feature,response.get('candidates') or [],analogy=True)
        references = []
        for candidate in analogy['candidates'][:3]:
            if candidate.get('score',0) < .25:
                continue
            if candidate.get('route'):
                found = [candidate['route']]
            elif candidate.get('code'):
                loaded = _fetch(candidate['code'],'')
                found = loaded.get('routes',[]) if loaded.get('status') == 'matched' else []
            else:
                found = []
            for route in found[:1]:
                if route.get('steps'):
                    references.append({**route,'approved':False,'reference_name':candidate.get('name'),
                        'reference_code':candidate.get('code'),'similarity_score':candidate['score'],
                        'scores':candidate['scores'],'missing_features':candidate['missing_features']})
        result['reference_routes'] = references
        return result
    except Exception:
        return {**payload,'similarity_status':'unavailable','notice':'相似路线读取失败，以下仅为模型建议，不是空库结论'}


def for_row(row):
    row = row if isinstance(row, dict) else {}
    reference = row.get('reference') or {}
    authority = row.get('authority') or {}
    # 外购/采购件没有工艺路线：直接短路，不调桥接、不猜。
    if is_external_part(row):
        return {'status':'external','routes':[],'notice':_EXTERNAL_NOTICE,
                'match_method':'unbound'}
    code = row.get('product_item_code') or reference.get('product_item_code') or authority.get('product_item_code') or ''
    name = row.get('name') or row.get('business_part_name') or ''
    token = cpq_kb_client._internal_token()
    if not token or not cpq_kb_client._base():
        return {'status':'unavailable','routes':[], 'notice':'DA 路线桥接未配置，以下为模型建议而非库内标准',
                'match_method':'unbound'}
    try:
        payload=dict(_fetch(code, name))
    except Exception:
        return {'status':'unavailable','routes':[], 'notice':'DA 路线读取失败，以下为模型建议而非库内标准',
                'match_method':'unbound'}
    payload.setdefault('routes', [])
    # 逐字名未中且归一化名不同时，只重试一次；命中记 normalized_name，两次都不中记 unbound。
    if payload.get('status') in ('not_found', 'missing_identity'):
        normalized = normalize_part_name(name)
        if normalized and normalized != name:
            try:
                retry = _fetch(code, normalized)
            except Exception:
                retry = None
            if isinstance(retry, dict) and retry.get('status') in ('matched', 'ambiguous'):
                retry = dict(retry)
                retry.setdefault('routes', [])
                retry['match_method'] = 'normalized_name'
                retry['normalized_from'] = name
                retry['matched_name'] = normalized
                return retry
        payload['match_method'] = 'unbound'
        payload['reason'] = payload.get('status')
        # Explicit business code is an identity, not a hint: never silently replace it.
        return payload if code else _similar_route(row, payload)
    payload['match_method'] = 'exact_name'
    return payload

def grounding(lookup):
    return ('DA 数据库路线依据（保留原序号与原文；草稿/名称命中仅供参考，不得声称已批准。'
            'NULL 工时是缺口，不是零；生成工时仍属模型估计。多条候选不得自行选定）：\n'
            +json.dumps(lookup,ensure_ascii=False,default=str))
