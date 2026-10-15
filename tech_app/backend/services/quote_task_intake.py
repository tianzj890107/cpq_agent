"""已鉴权报价任务→需求确认，同任务重开不重新建单或覆盖已有审批。"""
import copy
import threading
from pathlib import Path
from ..models.workflow import RequirementDoc
from ..storage import store
from . import requirement_service,industry_templates

_LOCK=threading.RLock()
from cpq_file_policy import extensions,max_bytes
DRAWINGS=set(extensions('source'))-{'.pdf'}  # 普通需求PDF不自动认作权威工程图。


def requirement_data(task):
    payload=task.get('payload') or {}
    quote=payload.get('quote') or {}
    industry=industry_templates.normalize(payload.get('industry') or quote.get('industry') or 'battery')
    data=dict(payload.get('requirement') or {})
    snapshot=payload.get('requirement_form') or {}
    aliases={'客户':'customer_name','客户名称':'customer_name',
        '项目名称':'project_name','项目规模':'quote_quantity','数量':'quote_quantity',
        'customer':'customer_name','project_scale':'quote_quantity','quantity':'quote_quantity'}
    for section in snapshot.values():
        values=section.get('数据',{}) if isinstance(section,dict) else {}
        if isinstance(values,list):values=values[0] if len(values)==1 else {}
        if isinstance(values,dict):
            for key,value in values.items():
                if value is not None and value!='':data.setdefault(aliases.get(key,key),value)
    if data.get('quantity') is not None:data.setdefault('quote_quantity',data['quantity'])
    data.update(industry=industry,industry_selection=industry,title=task.get('title') or payload.get('project_name') or '报价工艺需求',
        description=payload.get('requirement_text') or data.get('description') or '',
        source='CPQ 报价任务',source_task_id=str(task['task_id']),source_task_no=task.get('task_no') or '',
        source_session_id=task.get('session_id') or quote.get('session_id') or '',
        business_case_id=payload.get('business_case_id') or quote.get('business_case_id') or '',
        customer_name=data.get('customer_name') or payload.get('customer') or task.get('customer') or '',
        quote_original_snapshot=copy.deepcopy(payload),requirement_type='工艺评估')
    return data


def accept_task(task,user,*,attachments):
    if task.get('task_kind')!='tech_new_product' or not task.get('task_id'):
        raise ValueError('不是可接入的报价工艺任务')
    if len(attachments)>20:raise ValueError('任务附件超过20份')
    for file in attachments:
        name=file.get('name') or '';raw=file.get('data')
        if not name or len(name)>240 or name in ('.','..') or '/' in name or '\\' in name or not isinstance(raw,bytes) or not raw or len(raw)>max_bytes():
            raise ValueError('任务附件无效')
    key=str(task['task_id'])
    with _LOCK:
        metas=store.list_projects(lightweight=True)
        existing=next((m for m in metas if str(m.get('quote_task_id') or '')==key),None)
        if not existing:
            existing=next((m for m in metas if
                str((m.get('business_case') or {}).get('source_task_id') or '')==key and
                str((m.get('business_case') or {}).get('source_session_id') or
                    (m.get('business_case') or {}).get('quote_session_id') or '')==str(task.get('session_id') or '')),None)
        if existing:
            pid=existing['project_id'];doc=store.load_requirement(pid)
            if doc:
                page={'pending_review':'requirement-review','approved':'drawing',
                      'draft':'requirement-create','rejected':'requirement-create'}.get(doc['status'],'requirement-confirm')
                return {'project_id':pid,'page':page,'reused':True,'has_drawing':bool(store.source_path(pid))}
        data=requirement_data(task)
        # CAD先于截图/辅助图片，避免先上传的需求截图成为解析原图。
        candidates=[a for a in attachments if Path(a['name']).suffix.lower() in DRAWINGS]
        primary=next((a for a in candidates if Path(a['name']).suffix.lower() in {'.dwg','.dxf'}),
                     candidates[0] if candidates else None)
        if existing:pid=existing['project_id']
        elif primary:pid=store.create_project(primary['name'],primary['data'],note='报价任务承接',owner=user.get('username','system'))
        else:pid=store.create_requirement_project(note='报价任务承接，图纸可在2.1上传',owner=user.get('username','system'))
        meta=store.load_meta(pid);meta.update(quote_task_id=key,project_name=data['title'])
        store._meta().put_meta(pid,meta)
        store.save_business_case(pid,{'entry_origin':'quote','internal_test':False,
            'business_case_id':data['business_case_id'],'source_session_id':data['source_session_id'],
            'quote_session_id':data['source_session_id'],
            'source_task_id':key},author=user.get('username','system'))
        for file in attachments:
            if file is not primary:store.add_attachment(pid,file['name'],file['data'],user.get('username','system'))
        doc=RequirementDoc(project_id=pid,requirement_no='',data=data,title=data['title'])
        requirement_service.save_requirement_draft(pid,doc,user)
        requirement_service.submit_requirement_confirmation(pid,user,comment='承接报价需求快照，进入确认；未代确认或审核')
        return {'project_id':pid,'page':'requirement-confirm','reused':False,'has_drawing':bool(primary)}
