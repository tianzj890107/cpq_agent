"""Explicit new-project demo preparation; no historical project or master-data write."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tech_app.backend.storage import store
from tech_app.backend.services import wine_poc,packaging_parts,packaging_manufacturing,packaging_process_instances
import cpq_process_routing


def run(pid):
    requirement=store.load_requirement(pid) or {}
    data=requirement.get('data') or {}
    if data.get('poc_demo') is not True or '酒盒' not in str(requirement.get('title')):
        raise ValueError('explicit_wine_demo_required')
    doc=packaging_parts.load_business_parts(pid)
    if not doc:
        raise ValueError('drawing_not_parsed')
    result=cpq_process_routing.packaging_candidates()
    if result.get('status')!='ok':
        raise ValueError('reference_database_unavailable')
    prepared=wine_poc.prepare_rows(doc,result['candidates'],enabled=True)
    saved=packaging_parts.save_business_parts(pid,prepared)
    for row in saved['business_parts']:
        inputs=row['poc_inputs']
        print(json.dumps({'part':row['name'],'length_mm':inputs.get('length_mm'),
            'width_mm':inputs.get('width_mm'),'material':inputs.get('material_text'),'source':inputs.get('source'),'mock':True},ensure_ascii=False),flush=True)
        if wine_poc.is_external_part(row):
            continue
        code=inputs.get('reference_code')
        lookup=cpq_process_routing.lookup(code,'') if code else {'status':'not_found','routes':[]}
        lookup['match_method']='exact_name'
        adapted=dict(row,reference={**row.get('reference',{}),'material_text':inputs['material_text'],
                                  'length_mm':inputs['length_mm'],'width_mm':inputs['width_mm']})
        part=packaging_parts.business_as_ir_part(adapted)
        def fallback(*args):
            from tech_app.backend.models.process import ProcessOutline
            names=['开料','模切']+(['包盒'] if '灰板' not in inputs['material_text'] else [])
            return ProcessOutline(steps=[{'step_no':(index+1)*10,'name':name,'type':'other'} for index,name in enumerate(names)])
        plan,coverage=packaging_manufacturing.outline(part,lookup=lookup,run=fallback,note=inputs['note'])
        plan.overall_note+='；POC mock工时30秒/道，非生产标准；'+inputs['note']
        body=plan.model_dump()
        for step in body['steps']:
            step['standard_seconds']=30
            step['duration_min']=.5
            step['time_source']='poc_mock'
            decision=packaging_process_instances.billing(step)
            if decision['gap']:
                step['formula_code']='PKG-C-LABOR'
                step['note']='POC暂按人工30秒计费；'+str(step.get('note') or '')
        packaging_parts.save_part_process(pid,{'part_code':row['business_part_code'],'parts_id':'',
            'business_parts_id':saved['business_parts_id'],'business_parts_hash':saved['business_parts_hash'],
            'plan':body,'sections':[],'coverage':coverage,'lookup':lookup,'poc_demo':True,
            'process_origin':'poc_reference_with_mock_time','cost_parameters':row['cost_parameters'],
            'assumptions':[inputs['note'],'POC各工序30秒/道；36元/小时，不是标准工时']})
    store.audit(pid,'poc:wine_automatic_inputs',{'by':'PE1','mock':True,'part_total':len(saved['business_parts'])})
    print('POC inputs and process records ready',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('project_id');run(parser.parse_args().project_id)
