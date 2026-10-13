"""Read-only online cost snapshot and offline quantity replay; never writes projects."""
import argparse
import getpass
import json
import os
import pathlib
import sys
import urllib.request
import urllib.parse

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from tech_app.backend.services.packaging_cost_audit import replay_quantity
from tech_app.backend.services.requirement_service import resolved_packaging_order_data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_id')
    parser.add_argument('--base',default='http://172.16.10.34:8010')
    parser.add_argument('--user',default='PE1')
    args=parser.parse_args()
    def request(path,data=None,token=''):
        payload=json.dumps(data).encode() if data is not None else None
        req=urllib.request.Request(args.base.rstrip('/')+path,data=payload,
            headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)
    token=request('/auth/login',{'username':args.user,
        'password':os.environ.get('CPQ_AUDIT_PASSWORD') or getpass.getpass('Password: ')})['token']
    root='/api/projects/'+urllib.parse.quote(args.project_id,safe='')
    requirement=request(root+'/requirement',token=token)['requirement']
    data=resolved_packaging_order_data(requirement.get('data') or {})
    cost=request(root+'/requirement/packaging-cost',token=token)['cost']
    result=replay_quantity(cost,data.get('quote_quantity'))
    result['project_id']=args.project_id
    result['original_total_cost']=cost.get('total_cost')
    result['quantity_resolution']=data.get('quantity_resolution')
    result['largest_items']=sorted(result.pop('items'),key=lambda r:r['amount'],reverse=True)[:10]
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
