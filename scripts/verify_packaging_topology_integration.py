"""Reparse local DXFs through production builders; never access project storage.

Frozen baseline is an evaluation output, never an input to recognition.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tech_app.backend.services import packaging_parts as parts, packaging_sections as sections
from tech_app.backend.services import packaging_business_part_resolver as resolver
from tech_app.backend.services.cad_ir.parser import parse_dxf
from tech_app.backend.services.packaging_part_topology import refine_document


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def verify(path, frozen):
    started=time.monotonic()
    ir=parse_dxf(path.read_bytes(),filename=path.name)
    geometry=parts.extract(ir)
    resolved=resolver.resolve_business_parts('local-topology-validation',ir,geometry,use_model=True,strict_schemes=True)
    doc=parts.business_parts_document(resolved['reference'],geometry,bindings=resolved['match'],cad_ir=ir)
    baseline=json.loads(frozen.read_text())['doc']
    original={r['business_part_code']:r for r in baseline['business_parts']}
    assert [r['business_part_code'] for r in baseline['business_parts']] == [r['business_part_code'] for r in doc['business_parts']]
    assert all((r.get('sequence_no'),r.get('display_code'),r.get('name')) ==
               (original[r['business_part_code']].get('sequence_no'),original[r['business_part_code']].get('display_code'),
                original[r['business_part_code']].get('name')) for r in doc['business_parts'])
    changes=[]; checks=[]
    for row in doc['business_parts']:
        old=original[row['business_part_code']]
        if old==row: continue
        binding=row['geometry_binding']; selected=next(c for c in binding['candidates'] if c['id']==binding['candidate_id'])
        assert set(binding['entity_ids'])==set(selected['entity_ids'])
        assert row==sections.hydrate_part(row,doc['geometry_evidence']['components'])
        assert set(binding['entity_ids'])=={eid for s in row['sections'] for eid in s['entity_ids']}
        ready=parts.verified_business_part_input_row(row,doc)
        process=parts.business_process_inputs(ready)
        cost=parts.business_cost_inputs(ready,requirement={'face_paper_gsm':157},quantity=1000)
        checks.append({'id':row['business_part_code'],'process_input_ok':process['ok'],
                       'cost_input_ok':cost['ok'],'cost_gap':cost.get('missing_variables')})
        changes.append({'id':row['business_part_code'],'name':row['name'],
            'entity_total':len(binding['entity_ids']),'section_total':len(row['sections']),
            'dimensions':[binding['length_mm'],binding['width_mm']],
            'section_sizes':[s['estimated_size'] for s in row['sections']],
            'changed_fields':[k for k in set(old)|set(row) if old.get(k)!=row.get(k)]})
    assert refine_document(doc,ir)==doc
    summary={'file':path.name,'seconds':round(time.monotonic()-started,2),'part_total':len(doc['business_parts']),
        'frozen_part_hash':digest(baseline['business_parts']),'current_part_hash':digest(doc['business_parts']),
        'all_part_fields_identical':baseline['business_parts']==doc['business_parts'],
        'whole_document_identical':baseline==doc,'changed_total':len(changes),'changes':changes,
        'downstream_input_checks':checks,'idempotent':True,'candidate_and_hydrate_checks':True,
        'order_and_identity_preserved':True}
    print(path.name,'parts',summary['part_total'],'changed',len(changes),'seconds',summary['seconds'],flush=True)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ('wine-dxf','round-dxf','frozen-dir','out'):
        parser.add_argument('--'+arg,type=Path,required=True)
    args=parser.parse_args();os.environ['PACKAGING_PART_MODEL_REVIEW']='off'
    result={name:verify(path,args.frozen_dir/(name+'-baseline.json'))
            for name,path in [('wine',args.wine_dxf),('round',args.round_dxf)]}
    assert result['wine']['whole_document_identical'],'wine freeze failed'
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
