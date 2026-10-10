"""User-authorized wine demonstration: create linked cards and run actual DWG pipeline.

Never print authentication tokens; never delete projects or mutate existing projects.
"""
import json
import time
import uuid
import argparse
import urllib.request
import urllib.error
from pathlib import Path

BASE = 'http://172.16.10.34:8010'


def request(path, data=None, *, token='', method=None, timeout=60):
    headers = {'Content-Type':'application/json','Authorization':'Bearer '+token}
    payload = json.dumps(data,ensure_ascii=False).encode() if data is not None else None
    req = urllib.request.Request(BASE+path,data=payload,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(str(error.code)+' '+error.read().decode()[:1000]) from None


def login(username):
    return request('/auth/login',{'username':username,'password':'123456'})['token']


def create(resume=None):
    sm,pe=login('SM1'),login('PE1')
    if resume:
        sid,pid,taskid,title,business_case_id=resume
        fields={'business_case_id':business_case_id}
        note='700ML双开门酒盒，内尺寸220.5×90×90mm，面纸225g，EVA内托，V槽是，1000只。仅POC演示，缺失价格/工时/费率允许mock，非生产报价。'
        prepare(pid,sid,taskid,title,fields,note,pe)
        return
    sid=request('/agents/quote/api/new',{},token=sm)['id']
    title='酒盒全流程POC-'+time.strftime('%m%d-%H%M%S')
    card=request('/wf/card/sync',{'session_id':sid,'title':title,'customer':'裕同包装POC演示',
        'project_name':title,'current_step':1,'industry':'packaging'},token=sm)['card']
    note='700ML双开门酒盒，内尺寸220.5×90×90mm，面纸225g，EVA内托，V槽是，1000只。仅POC演示，缺失价格/工时/费率允许mock，非生产报价。'
    task=request('/wf/task/send',{'session_id':sid,'target_type':'role','target_role_code':'process_mgr',
        'task_kind':'tech_new_product','note':note,'payload':{'industry':'packaging','title':title,'description':note}},token=sm)
    taskrow=task.get('task') or task
    taskid=str(taskrow.get('id') or taskrow.get('task_id') or '')
    if not taskid:
        raise RuntimeError('task id absent: '+str(task))
    request('/wf/task/claim',{'task_id':taskid},token=pe)
    root=Path(__file__).resolve().parents[1]
    drawing=root/'裕同包装项目-待开发/酒盒.dwg'
    boundary='cpq-'+uuid.uuid4().hex
    fields={'note':note,'source_session_id':sid,'source_task_id':taskid,
            'business_case_id':str(card.get('business_case_id') or '')}
    body=b''
    for key,value in fields.items():
        body+=('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"\r\n\r\n'+value+'\r\n').encode()
    body+=('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="酒盒.dwg"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+drawing.read_bytes()+b'\r\n'+('--'+boundary+'--\r\n').encode()
    req=urllib.request.Request(BASE+'/api/projects',data=body,headers={'Authorization':'Bearer '+pe,
        'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.urlopen(req,timeout=60) as response: project=json.load(response)
    pid=project['project_id']
    print(json.dumps({'quote_session':sid,'project_id':pid,'task_id':taskid,'title':title},ensure_ascii=False),flush=True)
    prepare(pid,sid,taskid,title,fields,note,pe)


def prepare(pid,sid,taskid,title,fields,note,pe):
    data={'industry':'packaging','title':title,'description':note,'quote_quantity':1000,
          'customer_name':'裕同包装POC演示','source_task_id':taskid,'source_session_id':sid,
          'business_case_id':fields['business_case_id'],'box_type':'YT-DWG-WINE-700ML','box_family':'书型盒/双开门礼盒',
          'closure_type':'双开门/对开','inner_length':220.5,'inner_width':90,'inner_height':90,'face_paper_gsm':225,
          'insert_type':'EVA内托','v_groove':'是','poc_demo':True,'tax_rate':.13,'currency':'CNY'}
    request('/api/projects/'+pid+'/requirement',{'project_id':pid,'requirement_no':'REQ-'+pid.upper(),'title':title,'data':data},token=pe,method='PUT')
    print('DWG pipeline starting '+pid,flush=True)
    result=request('/api/projects/'+pid+'/drawing-flow/run',{'prompt':note},token=pe,timeout=1800)
    print('DWG pipeline returned '+str(result.get('flow',{}).get('status')),flush=True)
    doc=request('/api/projects/'+pid+'/requirement/packaging-business-parts',token=pe)
    print('business parts '+str(len(doc.get('business_parts') or [])),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--resume',nargs=5)
    create(parser.parse_args().resume)
