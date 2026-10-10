"""Explicit demonstration overlay. Never assert CAD/manual confirmation."""
import copy
import re
from .packaging_part_route_match import normalize_part_name, is_external_part


def prepare_rows(document, candidates, *, enabled=False):
    if enabled is not True:
        raise ValueError('poc_not_enabled')
    doc=copy.deepcopy(document)
    doc['poc_demo']=True
    for row in doc.get('business_parts') or []:
        name=normalize_part_name(row.get('name'))
        matches=[item for item in candidates if normalize_part_name(item.get('name'))==name]
        reference=row.get('reference') or {}
        inputs={}
        if len(matches)==1:
            match=matches[0]
            inputs={'length_mm':match.get('length_mm'),'width_mm':match.get('width_mm'),
                    'material_text':reference.get('material_text') or match.get('material_text'),
                    'source':'poc_library_reference','reference_code':match.get('code')}
        else:
            size=row.get('confirmed_size') or reference
            if size.get('length_mm') and size.get('width_mm'):
                inputs={'length_mm':size['length_mm'],'width_mm':size['width_mm'],
                        'material_text':reference.get('material_text'),'source':'poc_existing_size'}
            else:
                binding=row.get('geometry_binding') or {}
                box=binding.get('bbox')
                if not box:
                    options=binding.get('candidates') or []
                    box=next((candidate.get('bbox') for candidate in options if candidate.get('bbox')),None)
                if box and len(box)>=4 and box[2]>box[0] and box[3]>box[1]:
                    inputs={'length_mm':box[2]-box[0],'width_mm':box[3]-box[1],
                            'material_text':reference.get('material_text') or ('2mm灰板' if '板' in str(row.get('name')) else '225g面纸'),
                            'source':'poc_candidate_estimate'}
        external=is_external_part(row) or any(word in str(row.get('name') or '') for word in ('磁铁','EVA'))
        if external and not is_external_part(row):
            row['reference']={**reference,'process_text':str(reference.get('process_text') or '')+'；POC外购件'}
        if not external and not (inputs.get('length_mm') and inputs.get('width_mm')):
            raise ValueError('poc_size_missing:'+str(row.get('name')))
        material=inputs.get('material_text') or ('POC外购件' if external else '')
        gsm=re.search(r'(\d+(?:\.\d+)?)\s*[gG]',material)
        thickness=re.search(r'(\d+(?:\.\d+)?)\s*[mM][mM]',material)
        inputs.update(material_text=material,quantity=1,ton_price=8000,tax_factor=1.13,
            gsm=float(gsm.group(1)) if gsm else float(thickness.group(1))*700 if thickness else 350,
            imposition_count=1,proof_base=50,machine_length=inputs.get('length_mm'),
            machine_width=inputs.get('width_mm'),mock=True,
            note='POC演示：参照尺寸非本图确认；吨价8000元/吨、灰板体积密度700kg/m³、用量1、单模及校版50张为mock；不用于生产报价')
        row['poc_inputs']=inputs
        row['usage_qty']=1
        row['cost_parameters']={**inputs,'labor_rate':36,'labor_seconds':30,
                                'standard_seconds':30,'overhead_seconds':0,'overhead_rate':0}
    return doc


def part_bom_row(row, doc):
    value=row.get('poc_inputs') or {}
    if doc.get('poc_demo') is not True or not value.get('mock'):
        return None
    return {'bom_category':'optional_part' if is_external_part(row) else 'box_part',
            'item_key':row['business_part_code'],'part_code':row['business_part_code'],
            'item_name':row['name'],'material':value.get('material_text'),'material_code':'',
            'quantity':1,'unit':'件','length_mm':value.get('length_mm'),'width_mm':value.get('width_mm'),
            'size_source_json':__import__('json').dumps({'kind':value.get('source'),'poc_demo':True},ensure_ascii=False),
            'status':'computed','missing_variables':[],'is_optional':int(is_external_part(row)),
            'source':'poc_demo_reference'}
