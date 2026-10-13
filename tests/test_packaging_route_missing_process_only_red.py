import copy
import unittest
from unittest.mock import patch
from tech_app.backend.services import packaging_demo_inputs as service
from tech_app.backend.services import packaging_parts, packaging_layout, packaging_process_instances, da_process_routing, tasks


class MissingOnlyTests(unittest.TestCase):
    def row(self, code, sections=None):
        return {'business_part_code':code,'name':'面纸'+code,'reference':{'material_text':'225g面纸'},
                'poc_inputs':{'length_mm':100,'width_mm':60},'sections':sections or []}

    def section(self, sid):
        return {'section_id':sid,'name':'组成'+sid,'entity_ids':[sid],
                'quantity':1,'poc_inputs':{'length_mm':100,'width_mm':60}}

    def record(self, row, children=None):
        return {'part_code':row['business_part_code'],'business_parts_hash':'H',
                'plan':{'part_class':'packaging','steps':[{'step_no':10,'name':'开料','depends_on':[]}]},
                'coverage':{'summary':{'reused':1,'missing':0}},'sections':children or []}

    def run_prepare(self, rows, records, missing):
        doc={'poc_demo':True,'business_parts_hash':'H','business_parts_id':'V','business_parts':rows}
        calls, saved, progress = [], [], []
        def recommend(part, lookup, inputs, **kw):
            calls.append(part.name)
            return ({'part_class':'packaging','steps':[{'step_no':10,'name':'开料','depends_on':[],
                     'duration_min':.5,'standard_seconds':30}], 'overall_note':''},
                    {'summary':{'reused':0,'missing':1},'process_origin':'packaging_model_recommendation'})
        with patch.object(packaging_parts,'load_business_parts',return_value=doc), \
             patch.object(packaging_layout,'active_business_document',side_effect=lambda d:d), \
             patch.object(packaging_process_instances,'collect',return_value={'records':[r for c,r in records.items() if c not in missing], 'missing':missing,'active':bool(records)}), \
             patch.object(packaging_parts,'verified_business_part_input_row',side_effect=lambda r,d:r), \
             patch.object(packaging_parts,'load_part_process',side_effect=lambda p,c:copy.deepcopy(records.get(c,{}))), \
             patch.object(packaging_parts,'save_part_process',side_effect=lambda p,r:saved.append(copy.deepcopy(r))), \
             patch.object(da_process_routing,'for_row',return_value={}), \
             patch.object(service,'demo_process_plan',side_effect=recommend), \
             patch.object(tasks,'report_progress',side_effect=lambda t,*a,**kw:progress.append(t)):
            result=service.prepare_processes('P',actor='PE1')
        return result,calls,saved,progress

    def test_one_missing_does_not_regenerate_or_overwrite_valid_part(self):
        a,b=self.row('A'),self.row('B')
        result,calls,saved,progress=self.run_prepare([a,b],{'A':self.record(a)},['B'])
        self.assertEqual(['面纸B'],calls)
        self.assertEqual(['B'],[r['part_code'] for r in saved])
        self.assertTrue(result['ready'])
        self.assertTrue(any('B' in p for p in progress))

    def test_multisection_has_one_whole_part_model_call(self):
        row=self.row('A',[self.section('S1'),self.section('S2')])
        _,calls,saved,_=self.run_prepare([row],{},['A'])
        # Whole-part Spec supersedes per-portion model calls.
        self.assertEqual(['面纸A'],calls)
        self.assertEqual([],saved[0]['sections'])
        self.assertEqual(1,len(saved[0]['plan']['steps']))
        self.assertEqual('business_part/1',saved[0]['process_scope'])

    def test_legacy_child_result_requires_whole_part_regeneration(self):
        s1,s2=self.section('S1'),self.section('S2')
        row=self.row('A',[s1,s2])
        child={'section_id':'S1','section_name':'组成S1','quantity':1,
               'plan':self.record(row)['plan'],'coverage':{'summary':{'reused':1,'missing':0}}}
        old=self.record(row,[child])
        _,calls,saved,_=self.run_prepare([row],{'A':old},['A'])
        self.assertEqual(['面纸A'],calls)
        self.assertEqual([],saved[0]['sections'])
        self.assertEqual([child],old['sections'], '历史结果保留，不改旧组成方案')

    def test_complete_collection_does_not_call_or_save(self):
        row=self.row('A')
        _,calls,saved,_=self.run_prepare([row],{'A':self.record(row)},[])
        self.assertEqual([],calls)
        self.assertEqual([],saved)

    def test_changed_composition_regenerates_whole_part_not_each_section(self):
        s1,s2=self.section('S1'),self.section('S2')
        row=self.row('A',[s1,s2])
        old_signature=copy.deepcopy(service._section_process_input(row,s1))
        old_signature['section']['poc_inputs']['length_mm']=200
        child={'section_id':'S1','section_name':'组成S1','quantity':1,
               'process_input':old_signature,'plan':self.record(row)['plan']}
        _,calls,_,_=self.run_prepare([row],{'A':self.record(row,[child])},['A'])
        self.assertEqual(['面纸A'],calls)

    def test_collect_detects_incomplete_section_result(self):
        row=self.row('A',[self.section('S1'),self.section('S2')])
        child={'section_id':'S1','plan':self.record(row)['plan']}
        doc={'business_parts_hash':'H','business_parts':[row]}
        with patch.object(packaging_parts,'load_business_parts',return_value=doc), \
             patch.object(packaging_layout,'active_business_document',side_effect=lambda d:d), \
             patch.object(packaging_parts,'load_part_process',return_value=self.record(row,[child])):
            result=packaging_process_instances.collect('P')
        self.assertEqual(['A'],result['missing'])
        self.assertEqual([],result['records'])

    def test_merged_sections_preserve_local_dependencies_time_and_source(self):
        from tech_app.backend.models.ir import Part
        plan={'steps':[{'step_no':10,'name':'开料','duration_min':1,'depends_on':[],
                         'recommendation':{'kind':'reference'}},
                       {'step_no':20,'name':'检验','duration_min':2,'depends_on':[10]}]}
        children=[{'section_id':'S1','section_name':'组成1','quantity':2,'plan':plan,'coverage':{}},
                  {'section_id':'S2','section_name':'组成2','quantity':1,'plan':plan,'coverage':{}}]
        merged,_=service._merge_demo_sections(Part(part_id='A',name='面纸'),children)
        self.assertEqual([10,20,30,40],[s['step_no'] for s in merged['steps']])
        self.assertEqual([30],merged['steps'][3]['depends_on'])
        self.assertEqual(2,merged['steps'][0]['duration_min'])
        self.assertEqual('reference',merged['steps'][0]['recommendation']['kind'])
        self.assertEqual(1,plan['steps'][0]['duration_min'])
