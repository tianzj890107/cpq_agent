"""DA routing evidence via CPQ's authenticated read-only bridge."""
import json
import urllib.parse
import urllib.request
from . import cpq_kb_client

def for_row(row):
    reference = row.get('reference') or {}
    authority = row.get('authority') or {}
    code = row.get('product_item_code') or reference.get('product_item_code') or authority.get('product_item_code') or ''
    name = row.get('name') or row.get('business_part_name') or ''
    token = cpq_kb_client._internal_token()
    if not token or not cpq_kb_client._base():
        return {'status':'unavailable','routes':[], 'notice':'DA 路线桥接未配置，以下为模型建议而非库内标准'}
    try:
        query=urllib.parse.urlencode({'product_item_code':code,'name':name})
        req=urllib.request.Request(cpq_kb_client._base()+'/wf/tech/process-routing?'+query,
            headers={cpq_kb_client.INTERNAL_TOKEN_HEADER:token,'Accept':'application/json'})
        with urllib.request.urlopen(req,timeout=cpq_kb_client.CPQ_KB_TIMEOUT_SECONDS) as response:
            payload=json.load(response)
        if not isinstance(payload,dict) or not payload.get('ok'):
            raise ValueError('invalid bridge response')
        return payload
    except Exception:
        return {'status':'unavailable','routes':[], 'notice':'DA 路线读取失败，以下为模型建议而非库内标准'}

def grounding(lookup):
    return ('DA 数据库路线依据（保留原序号与原文；草稿/名称命中仅供参考，不得声称已批准。'
            'NULL 工时是缺口，不是零；生成工时仍属模型估计。多条候选不得自行选定）：\n'
            +json.dumps(lookup,ensure_ascii=False,default=str))
