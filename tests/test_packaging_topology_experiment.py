"""Safety checks for an OFFLINE experiment; these are not production accuracy claims."""
import copy
import unittest
from scripts.experiment_packaging_topology import experiment


def circle(eid,radius):
    return {'entity_id':eid,'type':'CIRCLE','closed':True,'bbox':[-radius,-radius,radius,radius],
            'attributes':{'center':[0,0],'radius':radius}}


def rectangle(eid,x,y,w,h):
    return {'entity_id':eid,'type':'LWPOLYLINE','closed':True,'bbox':[x,y,x+w,y+h],
            'attributes':{'points':[[x,y],[x+w,y],[x+w,y+h],[x,y+h]],'closed':True}}


def fixture():
    entities=[circle('outer',10),circle('hole',3),rectangle('slot',-1,5,2,2)]
    row={'business_part_code':'arbitrary','name':'任意名称','geometry_binding':{
        'entity_ids':['slot'],'bbox':[-1,5,1,7],'length_mm':2,'width_mm':2,
        'candidates':[{'id':'whole','entity_ids':['outer','hole','slot'],'component_ids':['c'],
                       'bbox':[-10,-10,10,10]}]},'sections':[{'entity_ids':['slot'],'bbox':[-1,5,1,7]}]}
    return {'ir':{'entities':entities},'doc':{'business_parts':[row]}}


class ExperimentSafety(unittest.TestCase):
    def test_circle_topology_keeps_one_material_with_internal_holes(self):
        data=fixture();result=experiment(data,'topology')['business_parts'][0]
        self.assertEqual(1,len(result['sections']))
        self.assertEqual({'outer','hole','slot'},set(result['sections'][0]['entity_ids']))
        self.assertEqual(20,result['geometry_binding']['length_mm'])

    def test_existing_input_is_unchanged_and_no_invented_entity(self):
        data=fixture();before=copy.deepcopy(data);result=experiment(data,'combined')
        self.assertEqual(before,data)
        self.assertTrue(set(result['business_parts'][0]['geometry_binding']['entity_ids'])<=
                        {e['entity_id'] for e in data['ir']['entities']})

    def test_renaming_does_not_change_selection(self):
        data=fixture();other=copy.deepcopy(data);other['doc']['business_parts'][0]['name']='改名纸板xyz'
        a=experiment(data,'combined')['business_parts'][0];b=experiment(other,'combined')['business_parts'][0]
        self.assertEqual(a['geometry_binding'],b['geometry_binding']);self.assertEqual(a['sections'],b['sections'])

    def test_circle_inside_non_circular_outer_is_not_a_new_material(self):
        data=fixture();outer=rectangle('paper',-12,-12,24,28)
        data['ir']['entities']=[outer,circle('hole',3)]
        row=data['doc']['business_parts'][0]
        row['geometry_binding']={'entity_ids':['paper','hole'],'bbox':outer['bbox'],'candidates':[]}
        row['sections']=[{'entity_ids':['paper','hole'],'bbox':outer['bbox']}]
        self.assertEqual(data['doc'],experiment(data,'topology'))

    def test_non_circle_single_piece_stays_identical(self):
        data=fixture();data['ir']['entities']=[rectangle('plain',0,0,20,15)]
        row=data['doc']['business_parts'][0];row['geometry_binding']={'entity_ids':['plain'],'bbox':[0,0,20,15],'candidates':[]}
        row['sections']=[{'entity_ids':['plain'],'bbox':[0,0,20,15]}]
        self.assertEqual(data['doc'],experiment(data,'combined'))

    def test_wrong_name_owner_candidate_cannot_replace_selection(self):
        data=fixture();data['doc']['business_parts'][0]['geometry_binding']['candidates'][0]['evidence_reasons']=['other_name_anchor']
        self.assertEqual(data['doc'],experiment(data,'combined'))

    def test_manual_choice_remains_unchanged(self):
        data=fixture();data['doc']['business_parts'][0]['geometry_binding']['bound_by']='manual'
        self.assertEqual(data['doc'],experiment(data,'combined'))

    def test_entity_order_and_translation_preserve_topological_membership(self):
        data=fixture();shifted=copy.deepcopy(data)
        def shift_box(box):return [box[0]+123,box[1]-456,box[2]+123,box[3]-456]
        for entity in shifted['ir']['entities']:
            entity['bbox']=shift_box(entity['bbox']);a=entity['attributes']
            if 'center' in a:a['center']=[a['center'][0]+123,a['center'][1]-456]
            if 'points' in a:a['points']=[[p[0]+123,p[1]-456] for p in a['points']]
        shifted['ir']['entities'].reverse()
        row=shifted['doc']['business_parts'][0];row['geometry_binding']['bbox']=shift_box(row['geometry_binding']['bbox'])
        for c in row['geometry_binding']['candidates']:c['bbox']=shift_box(c['bbox'])
        for s in row['sections']:s['bbox']=shift_box(s['bbox'])
        a=experiment(data,'combined')['business_parts'][0];b=experiment(shifted,'combined')['business_parts'][0]
        self.assertEqual(a['geometry_binding']['entity_ids'],b['geometry_binding']['entity_ids'])
        self.assertEqual(a['sections'][0]['entity_ids'],b['sections'][0]['entity_ids'])
        self.assertEqual(a['geometry_binding']['length_mm'],b['geometry_binding']['length_mm'])

    def test_same_caption_parallel_portions_but_foreign_claim_is_protected(self):
        data=fixture();a=rectangle('a',0,0,20,4);b=rectangle('b',0,7,20,4)
        data['ir']['entities']=[a,b]
        row=data['doc']['business_parts'][0]
        row['geometry_binding']={'entity_ids':['a'],'bbox':a['bbox'],'candidates':[
            {'id':e['entity_id'],'entity_ids':[e['entity_id']],'bbox':e['bbox'],'member_total':1,
             'evidence_reasons':['complete_closed_outline'],'name_anchor_entity_id':'one-caption'} for e in [a,b]]}
        row['sections']=[{'entity_ids':['a'],'bbox':a['bbox']}]
        self.assertEqual(2,len(experiment(data,'combined')['business_parts'][0]['sections']))
        data['doc']['business_parts'].append({'business_part_code':'other','name':'另件','geometry_binding':{'entity_ids':['b']}})
        self.assertEqual(1,len(experiment(data,'combined')['business_parts'][0]['sections']))
