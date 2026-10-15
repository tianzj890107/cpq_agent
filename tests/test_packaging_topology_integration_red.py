import copy
import unittest
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from tech_app.backend.services import packaging_parts as parts, packaging_sections as sections
from tests.test_packaging_topology_experiment import fixture, rectangle, circle


class TopologyIntegration(unittest.TestCase):
    def refine(self, data):
        from tech_app.backend.services.packaging_part_topology import refine_document
        return refine_document(data['doc'], data['ir'])

    def data(self):
        data = fixture()
        doc = data['doc']; doc['derived_from_drawing'] = True
        doc['geometry_evidence'] = {'components': [{'component_id':'c','entity_ids':['slot'],
                                                  'bbox':[-1,5,1,7]}]}
        row = doc['business_parts'][0]
        row['reference'] = {}; row['geometry_binding'].update(status='bound',bound_by='auto',component_ids=['c'])
        return data

    def test_complete_circle_uses_real_sections_and_consistent_candidate(self):
        data = self.data(); result = self.refine(data); row = result['business_parts'][0]
        self.assertEqual(1,len(row['sections']))
        section = row['sections'][0]
        self.assertTrue(section['section_id']); self.assertEqual('bound', section['status'])
        self.assertEqual(1,section['quantity']); self.assertEqual(20,section['estimated_size']['length_mm'])
        binding = row['geometry_binding']
        selected = next(c for c in binding['candidates'] if c['id']==binding['candidate_id'])
        self.assertEqual(set(binding['entity_ids']),set(selected['entity_ids']))
        self.assertEqual(set(binding['entity_ids']),set(section['entity_ids']))
        self.assertFalse(binding.get('size_confirmed')); self.assertFalse(section['confirmed_size'])

    def test_hydrate_and_downstream_do_not_split_holes(self):
        result = self.refine(self.data()); row = result['business_parts'][0]
        self.assertEqual(row,sections.hydrate_part(row,result['geometry_evidence']['components']))
        prepared = parts.verified_business_part_input_row(row,result)
        self.assertEqual(20,prepared['reference']['length_mm'])
        self.assertEqual(1,len(prepared['sections']))

    def test_input_unchanged_and_idempotent(self):
        data=self.data(); before=copy.deepcopy(data); result=self.refine(data)
        self.assertEqual(before,data)
        self.assertEqual(result,self.refine({'doc':result,'ir':data['ir']}))

    def test_manual_binding_and_sections_unchanged(self):
        for key in ('binding','sections'):
            data=self.data(); row=data['doc']['business_parts'][0]
            if key=='binding': row['geometry_binding']['bound_by']='manual'
            else: row['sections_source']='manual'
            self.assertEqual(data['doc'],self.refine(data))

    def test_non_circle_and_non_drawing_stay_identical(self):
        data=self.data(); data['doc']['derived_from_drawing']=False
        self.assertEqual(data['doc'],self.refine(data))
        data['doc']['derived_from_drawing']=True; data['ir']['entities']=[rectangle('slot',-1,5,2,2)]
        data['doc']['business_parts'][0]['geometry_binding']['candidates']=[]
        self.assertEqual(data['doc'],self.refine(data))

    def test_foreign_claim_blocks_enclosing_candidate(self):
        data=self.data(); data['doc']['business_parts'].append({'business_part_code':'other',
            'geometry_binding':{'entity_ids':['outer'],'bound_by':'manual'}})
        self.assertEqual(data['doc'],self.refine(data))

    def test_annotation_and_foreign_contours_not_added(self):
        data=self.data(); data['ir']['entities'] += [circle('annotation',2),circle('foreign',1)]
        data['doc']['geometry_evidence']['raw_entities']={'annotation':{'annotation':True}}
        data['doc']['business_parts'].append({'business_part_code':'other','geometry_binding':{'entity_ids':['foreign']}})
        result=self.refine(data)['business_parts'][0]
        self.assertNotIn('annotation',result['geometry_binding']['entity_ids'])
        self.assertNotIn('foreign',result['geometry_binding']['entity_ids'])

    def test_same_label_parallel_materials_keep_separate_cutting_sizes(self):
        data=self.data(); entities=[rectangle('a',0,0,20,4),rectangle('b',0,7,20,4)]
        data['ir']['entities']=entities; row=data['doc']['business_parts'][0]
        row['geometry_binding'].update(entity_ids=['a'],bbox=[0,0,20,4],candidates=[
            {'id':e['entity_id'],'entity_ids':[e['entity_id']],'component_ids':['c'], 'bbox':e['bbox'],
             'member_total':1,'name_anchor_entity_id':'same','evidence_reasons':['complete_closed_outline']}
            for e in entities])
        row['sections']=[{'entity_ids':['a'],'bbox':[0,0,20,4]}]
        result=self.refine(data); row=result['business_parts'][0]
        self.assertEqual(2,len(row['sections']))
        prepared=parts.verified_business_part_input_row(row,result)
        children=sections.section_input_rows(prepared)
        self.assertEqual([4,4],[c['reference']['width_mm'] for c in children])
        self.assertNotIn('width_mm',prepared['reference'])
        self.assertEqual(2,len({s['section_id'] for s in row['sections']}))

    def test_document_builder_accepts_ir_and_runs_topology(self):
        data=self.data(); row=data['doc']['business_parts'][0]
        geometry={'parts':[{'component_id':'c','entity_ids':['slot'],'bbox':[-1,5,1,7]}]}
        binding=dict(row['geometry_binding'],business_part_code='arbitrary')
        result=parts.business_parts_document({'derived_from_drawing':True,'parts':[
            {'business_part_code':'arbitrary','name':'任意名称'}]},geometry,
            bindings={'bindings':[binding]},cad_ir=data['ir'])
        self.assertEqual(20,result['business_parts'][0]['geometry_binding']['length_mm'])

    def test_selecting_generated_candidate_keeps_topology_sections(self):
        data=self.data(); data['doc']['geometry_evidence']['raw_entities']={
            e['entity_id']:{'bbox':e['bbox'],'kind':e['type'].lower()} for e in data['ir']['entities']}
        result=self.refine(data); row=result['business_parts'][0]; binding=row['geometry_binding']
        selected=parts.set_geometry_binding(result,'arbitrary',binding['component_ids'],
            candidate_id=binding['candidate_id'],bound_by='manual')
        actual=selected['business_parts'][0]
        self.assertEqual('cad_topology',actual['sections_source'])
        self.assertEqual(1,len(actual['sections']))
        self.assertEqual(set(binding['entity_ids']),set(actual['sections'][0]['entity_ids']))

    def test_scope_prevents_cross_scheme_candidate(self):
        data=self.data(); data['doc']['schemes']=[{'scheme_id':'1','y_min':0,'y_max':20}]
        data['doc']['business_parts'][0]['scheme_id']='1'
        self.assertEqual(data['doc'],self.refine(data))

    def test_existing_circle_in_non_circle_outer_unchanged(self):
        data=self.data(); outer=rectangle('paper',-12,-12,24,28)
        data['ir']['entities']=[outer,circle('hole',3)]
        row=data['doc']['business_parts'][0]
        row['geometry_binding'].update(entity_ids=['paper','hole'],candidates=[])
        row['sections']=[{'entity_ids':['paper','hole'],'bbox':outer['bbox']}]
        self.assertEqual(data['doc'],self.refine(data))

    def test_preview_draws_proven_outline_not_false_annotation_or_neighbors(self):
        text=(Path(__file__).resolve().parents[1]/'tech_app/frontend/app.js').read_text()
        start=text.index('function packagingPartSceneEntities('); end=text.index('\n}',start)+2
        script=text[start:end]+'''
const binding={topology_rule:'cad-conservative-topology-v1',exact_entities:true,
 entity_ids:['edge'],component_ids:['c'],bbox:[0,0,20,20]};
const doc={geometry_evidence:{components:[{component_id:'c',entity_ids:['edge'],
 annotation_filtered:[{entity_id:'edge'}]}]},cad_scene:{entities:[
 {cad_entity_id:'edge',annotation:true,bbox:[0,0,10,10]},
 {cad_entity_id:'nearby',bbox:[1,1,2,2]}]}};
console.log(JSON.stringify(packagingPartSceneEntities(binding,doc)));
'''
        result=json.loads(subprocess.run(['node','-e',script],capture_output=True,text=True,check=True).stdout)
        self.assertEqual(['edge'],[r['cad_entity_id'] for r in result])
        self.assertFalse(result[0].get('annotation'))

    def test_valid_raw_circle_without_retained_component_feeds_downstream(self):
        data=self.data(); data['doc']['geometry_evidence'].update(components=[],raw_entities={
            e['entity_id']:{'bbox':e['bbox']} for e in data['ir']['entities']})
        row=data['doc']['business_parts'][0]; row['geometry_binding']['component_ids']=[]
        row['geometry_binding']['candidates'][0]['component_ids']=[]
        result=self.refine(data)
        ready=parts.verified_business_part_input_row(result['business_parts'][0],result)
        self.assertEqual(20,ready['reference']['length_mm'])

    def test_two_disconnected_outer_circles_are_two_materials_not_empty_section(self):
        data=self.data(); a=circle('a',10); b=circle('b',10)
        b['attributes']['center']=[30,0]; b['bbox']=[20,-10,40,10]
        data['ir']['entities']=[a,b]; row=data['doc']['business_parts'][0]
        row['geometry_binding'].update(entity_ids=['a','b'],candidates=[],bbox=[-10,-10,40,10])
        row['sections']=[{'entity_ids':['a','b'],'bbox':[-10,-10,40,10]}]
        result=self.refine(data)['business_parts'][0]
        self.assertEqual([['a'],['b']],[s['entity_ids'] for s in result['sections']])
        self.assertTrue(all(s['status']=='bound' for s in result['sections']))

    def test_storage_roundtrip_and_version_invalidation(self):
        class Backend:
            def __init__(self): self.docs={}
            def get_doc(self,pid,key): return copy.deepcopy(self.docs.get((pid,key)))
            def put_doc(self,pid,key,value): self.docs[(pid,key)]=copy.deepcopy(value)
        data=self.data(); result=self.refine(data)
        with patch.object(parts,'get_backend',return_value=Backend()):
            old=parts.save_business_parts('local-test',data['doc'])
            saved=parts.save_business_parts('local-test',result)
            loaded=parts.load_business_parts('local-test')
            self.assertEqual(saved,loaded)
            self.assertNotEqual(old['business_parts_id'],saved['business_parts_id'])
            self.assertEqual('business_parts_reimported',parts.business_binding_stale_reason(
                old['business_parts_id'],saved['business_parts_id']))
        self.assertEqual(1,len(loaded['business_parts'][0]['sections']))

    def test_parallel_parts_feed_whole_part_process_and_per_portion_material(self):
        data=self.data(); entities=[rectangle('a',0,0,20,4),rectangle('b',0,7,20,4)]
        data['ir']['entities']=entities; row=data['doc']['business_parts'][0]
        row['reference']={'material_text':'157g双铜纸','ton_price':5000}
        row['geometry_binding'].update(entity_ids=['a'],bbox=[0,0,20,4],candidates=[
            {'id':e['entity_id'],'entity_ids':[e['entity_id']],'component_ids':['c'],'bbox':e['bbox'],
             'member_total':1,'name_anchor_entity_id':'same','evidence_reasons':['complete_closed_outline']}
            for e in entities])
        row['sections']=[{'entity_ids':['a'],'bbox':[0,0,20,4]}]
        result=self.refine(data); row=parts.verified_business_part_input_row(result['business_parts'][0],result)
        process=parts.business_process_inputs(row); cost=parts.business_cost_inputs(row,quantity=1000)
        self.assertTrue(process['ok']); self.assertEqual('business_part/1',process['process_scope'])
        self.assertTrue(cost['ok']); self.assertEqual(2,len(cost['sections']))
        self.assertEqual([4,4],[s['variables']['cut_width'] for s in cost['sections']])

    def test_document_orders_before_refinement_not_by_enlarged_material_union(self):
        data=self.data(); row=data['doc']['business_parts'][0]
        def order(rows):
            self.assertFalse(any((r.get('geometry_binding') or {}).get('topology_rule') for r in rows))
            return rows
        with patch.object(parts,'order_business_parts_by_regions',side_effect=order):
            parts.business_parts_document({'derived_from_drawing':True,'parts':[
                {'business_part_code':'arbitrary','name':'任意名称'}]},
                {'parts':[{'component_id':'c','entity_ids':['slot'],'bbox':[-1,5,1,7]}]},
                bindings={'bindings':[dict(row['geometry_binding'],business_part_code='arbitrary')]},
                cad_ir=data['ir'])


if __name__=='__main__': unittest.main()
