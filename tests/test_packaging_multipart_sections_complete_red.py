"""多组成闭环行为回归，不读金标，不连接生产库。"""
import copy
import asyncio
import importlib
import json
import pathlib
import subprocess
import unittest
from unittest.mock import patch

from tech_app.backend.services import packaging_parts as parts, packaging_bom as bom

ROOT = pathlib.Path(__file__).resolve().parents[1]


def component(cid, box):
    x0, y0, x1, y1 = box
    return {'component_id': cid, 'entity_ids': [cid + '-e'], 'drawing_bbox': box,
            'outline': {'bbox':box,'points':[[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]},
            'outline_status': 'closed', 'outline_points': [[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]],
            'unfolded_length_mm': x1-x0, 'unfolded_width_mm': y1-y0}


def document():
    return {'derived_from_drawing': True, 'business_parts_id': 'old', 'business_parts_hash': 'old',
            'business_parts': [{'business_part_code': 'BP-A', 'name': '组合衬片',
                'reference': {'material_text': '225g面纸', 'quantity': 2},
                'geometry_binding': {'status': 'bound', 'bound_by': 'auto',
                    'component_ids': ['a','b'], 'entity_ids': ['a-e','b-e'], 'bbox': [0,0,280,60]}}],
            'geometry_evidence': {'components': [component('a',[0,0,100,40]),
                component('b',[200,0,280,60]), component('nearby',[300,0,350,50])]}}


class MultipartSectionsTest(unittest.TestCase):
    def test_nested_cut_outlines_do_not_automatically_become_two_material_portions(self):
        s=self.service();doc=document()
        doc['geometry_evidence']['components']=[component('a',[0,0,100,100]),component('b',[20,20,40,40])]
        row=s.hydrate_part(doc['business_parts'][0],doc['geometry_evidence']['components'])
        self.assertFalse(row['sections_complete'],'内圈可能是孔，不能自动算两份材料')
        self.assertIn('nested_cut_outline_needs_composition_decision',row['section_gaps'])

    def test_each_portion_can_have_its_own_material_without_overwriting_parent(self):
        s=self.service();doc=self.confirmed()
        selections=[dict(r,material_text='150g内衬纸' if r['section_id']=='outside' else '225g面纸')
                    for r in doc['business_parts'][0]['sections']]
        doc=s.assign_sections(doc,'BP-A',selections,actor='PE1')
        row=parts.verified_business_part_input_row(doc['business_parts'][0],doc)
        cost=parts.business_cost_inputs(row)
        self.assertEqual([225,150],[r['gsm'] for r in cost['sections']])
        self.assertEqual(['225g面纸','150g内衬纸'],[r['material'] for r in bom.business_part_rows(doc)])
        self.assertEqual('225g面纸',doc['business_parts'][0]['reference']['material_text'])
        self.assertIn('150g内衬纸',parts.business_process_inputs(row)['grounding'])

    def test_complementary_open_fragments_are_one_portion_not_extra_material(self):
        doc=document()
        for component_row in doc['geometry_evidence']['components']:
            component_row['outline_status']='open';component_row['outline_points']=None
        candidate={'id':'whole','candidate_id':'whole','component_ids':['a','b'],
                   'entity_ids':['a-e','b-e'],'bbox':[0,0,280,60],
                   'evidence_reasons':['cut_lines_complementary'],'geometry_status':'supported'}
        doc['business_parts'][0]['geometry_binding']['candidates']=[candidate]
        result=parts.set_geometry_binding(doc,'BP-A',['a','b'],bound_by='manual',candidate_id='whole')
        self.assertEqual(1,len(result['business_parts'][0]['sections']))
        self.assertEqual(['a-e','b-e'],result['business_parts'][0]['sections'][0]['entity_ids'])
        self.assertFalse(result['business_parts'][0]['sections'][0]['confirmed_size'])

    def test_existing_dwg_caches_have_real_section_references_not_gold_counts(self):
        from tech_app.backend.services.cad_ir.parser import parse_dxf
        from tech_app.backend.services import packaging_business_part_resolver as resolver
        directory=ROOT/'tech_app/data/cpq-unified-parse/conversions'
        if not (directory/'manifests.json').exists():
            self.skipTest('本地真实 DWG 历史转换缓存不可用；合成闭环测试仍执行')
        manifest=json.loads((directory/'manifests.json').read_text())
        for source in manifest.get('manifests') or []:
            path=directory/source['conversion_id']/'converted.dxf'
            if not path.exists():continue
            ir=parse_dxf(path.read_bytes());geometry=parts.extract(ir)
            resolved=resolver.resolve_business_parts('isolated-readonly',ir,geometry,use_model=False)
            doc=parts.business_parts_document(resolved['reference'],geometry,bindings=resolved['match'])
            known={e['entity_id'] for e in ir['entities']}
            components={r['component_id']:r for r in doc['geometry_evidence']['components']}
            multipart=0;section_total=0
            for row in doc['business_parts']:
                self.assertIn('sections',row)
                multipart+=int(len(row['sections'])>1);section_total+=len(row['sections'])
                for section in row['sections']:
                    self.assertTrue(set(section['entity_ids'])<=known)
                    self.assertTrue(all(cid in components for cid in section['component_ids']))
            print(json.dumps({'source':source['attachment_name'],'diagnostic_only':True,
                'business_parts':len(doc['business_parts']),'multipart_business_parts':multipart,
                'physical_and_pending_sections':section_total,'model_calls':0},ensure_ascii=False),flush=True)

    def test_plain_name_group_excludes_layout_copies_and_is_translation_invariant(self):
        from tech_app.backend.services import packaging_part_visual_review as review
        for dx in (0,-400):
            regions=[{'region_id':cid,'component_ids':[cid],'entity_ids':[cid+'-e'],
                'bbox':[x0+dx,y0,x1+dx,y1],'substantial':True,'outline_status':'closed'}
                for cid,(x0,y0,x1,y1) in [('a',(0,0,30,30)),('b',(50,0,80,40)),('layout',(210,-40,330,80))]]
            ir={'texts':[{'entity_id':'name','raw_text':'名称：组合衬片','position':[40+dx,50]},
                {'entity_id':'scheme','raw_text':'方案1','position':[0+dx,200]},
                {'entity_id':'layout1','raw_text':'各排2模','position':[200+dx,150]},
                {'entity_id':'layout2','raw_text':'排3模','position':[202+dx,200]}],'entities':[]}
            groups=review._compound_named_groups(ir,regions,{},[])
            self.assertEqual([['a','b']],[g['component_ids'] for g in groups])

    def test_ui_section_gate_and_markup_keep_every_portion_without_untrusted_html(self):
        doc=self.confirmed();row=doc['business_parts'][0]
        src=(ROOT/'tech_app/frontend/app.js').read_text()
        snippets=[]
        for name in ('packagingSectionsReady','packagingSectionsMarkup'):
            start=src.index('function '+name+'(');end=src.index('\n}',start)+2;snippets.append(src[start:end])
        script='const esc=v=>String(v??" ").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");\n'+'\n'.join(snippets)
        script+='\nconst row='+json.dumps(row)+';const doc='+json.dumps(doc)+';'
        script+='const valid=packagingSectionsReady(row); row.sections[1].name="<img onerror=alert(1)>";'
        script+='const html=packagingSectionsMarkup(row,doc,true); row.sections[1].confirmed_size={};'
        script+='console.log(JSON.stringify({valid,invalid:packagingSectionsReady(row),html}));'
        result=json.loads(subprocess.run(['node','-e',script],capture_output=True,text=True,check=True).stdout)
        self.assertTrue(result['valid']);self.assertFalse(result['invalid'])
        self.assertIn('100.00 × 40.00 mm',result['html']);self.assertIn('80.00 × 60.00 mm',result['html'])
        self.assertNotIn('<img',result['html']);self.assertEqual(2,result['html'].count('<fieldset'))

    def test_confirming_single_section_uses_new_dimension_not_old_parent_size(self):
        s=self.service();doc=document()
        row=doc['business_parts'][0]
        row['geometry_binding'].update(component_ids=['a'],entity_ids=['a-e'],size_confirmed=True)
        row['confirmed_size']={'length_mm':120,'width_mm':40}
        row=s.hydrate_part(row,doc['geometry_evidence']['components'])
        doc['business_parts']=[row]
        doc=s.confirm_section_size(doc,'BP-A',row['sections'][0]['section_id'],100,40,actor='PE1',note='新标注')
        verified=parts.verified_business_part_input_row(doc['business_parts'][0],doc)
        inputs=parts.business_cost_inputs(verified)
        self.assertEqual(100,inputs['sections'][0]['variables']['cut_length'])

    def test_process_summary_remaps_local_numbers_and_dependencies(self):
        from tech_app.backend.models.process import ProcessPlan,ProcessStep
        from tech_app.backend.services import process
        s=self.service();doc=self.confirmed()
        row=parts.verified_business_part_input_row(doc['business_parts'][0],doc)
        inputs=parts.business_process_inputs(row)
        def recommend(part,**kw):
            return ProcessPlan(part_id=part.part_id,steps=[
                ProcessStep(step_no=10,name='下料',type='blank',description='原工序',duration_min=1),
                ProcessStep(step_no=20,name='模切',type='other',description='原工序',duration_min=1,depends_on=[10])]),{}
        merged,coverage,sections=s.recommend_process(inputs,recommend)
        self.assertEqual([10,20,30,40],[r['step_no'] for r in merged['steps']])
        self.assertEqual([[],[10],[],[30]],[r.get('depends_on') or [] for r in merged['steps']])
        self.assertEqual([],process.compute(merged)['warnings'])
        self.assertEqual(12,process.compute(merged)['total_duration_min'])
        self.assertEqual([10,20],[r['step_no'] for r in sections[1]['plan']['steps']])

    def test_storage_roundtrip_keeps_all_sections_and_stales_old_result(self):
        doc=self.confirmed()
        records={}
        class Backend:
            def get_doc(self,pid,key):return copy.deepcopy(records.get((pid,key)))
            def put_doc(self,pid,key,value):records[(pid,key)]=copy.deepcopy(value)
        with patch.object(parts,'get_backend',return_value=Backend()):
            saved=parts.save_business_parts('p',doc)
            loaded=parts.load_business_parts('p')
        self.assertEqual(saved['business_parts_hash'],loaded['business_parts_hash'])
        self.assertEqual([100,80],[s['confirmed_size']['length_mm'] for s in loaded['business_parts'][0]['sections']])
        new=self.service().confirm_section_size(loaded,'BP-A','outside',85,60,actor='PE1',note='复核')
        self.assertEqual('business_parts_reimported',parts.business_binding_stale_reason(saved['business_parts_id'],new['business_parts_id']))

    def test_stale_editor_and_wrong_role_cannot_write_sections(self):
        from tech_app.backend import main
        from fastapi import HTTPException
        with patch.object(main,'_workflow_project'),patch.object(parts,'load_business_parts',return_value=document()), \
             patch.object(parts,'save_business_parts') as save:
            with self.assertRaises(HTTPException) as caught:
                main.update_packaging_sections('p','BP-A',main.PackagingSectionsAction(business_parts_hash='wrong'),
                    {'username':'PE1','role':'process_manager'})
            self.assertEqual(409,caught.exception.status_code)
            with self.assertRaises(HTTPException) as caught:
                main.update_packaging_sections('p','BP-A',main.PackagingSectionsAction(),{'username':'SM1','role':'sales_manager'})
            self.assertEqual(403,caught.exception.status_code)
            save.assert_not_called()

    def test_api_saves_composition_and_individual_size(self):
        from tech_app.backend import main
        self.assertTrue(hasattr(main,'update_packaging_sections'),'缺少组成写入 API')
        doc = document()
        with patch.object(main,'_workflow_project'), patch.object(main.packaging_parts,'load_business_parts',return_value=doc), \
             patch.object(main.packaging_parts,'save_business_parts',side_effect=lambda pid,d:d), \
             patch.object(main,'_business_parts_body',side_effect=lambda pid,d:d), patch.object(main.store,'audit'):
            body = main.PackagingSectionsAction(sections=[{'name':'内衬','section_id':'s1','component_ids':['a']},
                {'name':'外衬','section_id':'s2','component_ids':['b']}])
            result = main.update_packaging_sections('p','BP-A',body,{'username':'PE1','role':'process_manager'})
        self.assertEqual(2,len(result['business_parts'][0]['sections']))
        self.assertTrue(hasattr(main,'confirm_packaging_section_size'))

    def test_actual_cost_task_calculates_all_sections_and_persists_them(self):
        from tech_app.backend import main
        doc = self.confirmed()
        seen=[]; saved=[]
        def submit(pid, kind, job, **kw):
            self.job=job
            return 'task'
        def calculate(category, variables):
            seen.append(variables['cut_length'])
            return {'amount':variables['cut_length']*variables['cut_width']/1000}
        with patch.object(main,'_workflow_project'), \
             patch.object(main,'_packaging_business_part_row',return_value={'row':doc['business_parts'][0],'doc':doc}), \
             patch.object(main.store,'load_requirement',return_value={}), \
             patch.object(main.tasks,'submit',side_effect=submit),patch.object(main.tasks,'report_progress'), \
             patch.object(main.tasks,'current_task_id',return_value='task'), \
             patch.object(main.packaging_cost,'compute_line',side_effect=calculate), \
             patch.object(main.packaging_parts,'save_part_cost',side_effect=lambda pid,value:saved.append(value)):
            asyncio.run(main.packaging_business_part_cost('p','BP-A',1000,'',[],
                {'role':'process_manager','username':'PE1'}))
            result=self.job()
        self.assertEqual([100,80],seen)
        self.assertEqual(27.2,result['line']['amount'])
        self.assertEqual(2,len(saved[0]['sections']))

    def test_actual_process_task_calls_each_section_and_keeps_identity(self):
        from tech_app.backend import main
        from tech_app.backend.models.process import ProcessPlan
        doc=self.confirmed();seen=[];saved=[]
        def submit(pid,kind,job,**kw): self.job=job;return 'task'
        def recommend(part,**kw):
            seen.append((part.part_id,kw['note']))
            return ProcessPlan(part_id=part.part_id,steps=[]),{'summary':{'reused':0,'missing':0}}
        with patch.object(main,'_workflow_project'), \
             patch.object(main,'_packaging_business_part_row',return_value={'row':doc['business_parts'][0],'doc':doc}), \
             patch.object(main.tasks,'submit',side_effect=submit),patch.object(main.tasks,'report_progress'), \
             patch.object(main.tasks,'current_task_id',return_value='task'), \
             patch.object(main.process,'outline_process',side_effect=recommend), \
             patch.object(main.packaging_parts,'save_part_process',side_effect=lambda pid,value:saved.append(value)):
            asyncio.run(main.packaging_business_part_process('p','BP-A','',[],{'role':'process_manager','username':'PE1'}))
            result=self.job()
        self.assertEqual(['BP-A::inside','BP-A::outside'],[r[0] for r in seen])
        self.assertEqual(2,len(result['sections']))
        self.assertEqual(2,len(saved[0]['sections']))

    def test_single_business_name_can_propose_multiple_owned_shapes(self):
        from tech_app.backend.services import packaging_part_visual_review as review
        regions=[{'region_id':cid,'component_ids':[cid],'entity_ids':[cid+'-e'],
                  'bbox':box,'substantial':True,'outline_status':'closed'} for cid,box in
                 [('a',[0,0,30,30]),('b',[50,0,80,40]),('other',[160,0,190,40])]]
        ir={'texts':[{'entity_id':'name','raw_text':'名称：组合衬片','position':[40,50]},
                     {'entity_id':'other-name','raw_text':'名称：底板','position':[175,50]}],'entities':[]}
        groups=review._compound_named_groups(ir,regions,{},[])
        self.assertEqual([['a','b']],[r['component_ids'] for r in groups])

    def test_frontend_can_choose_all_or_individual_portion_and_edit(self):
        src=(ROOT/'tech_app/frontend/app.js').read_text()
        for marker in ('function packagingSectionsMarkup(', 'packagingSectionViewSelect',
                       'packagingSaveSections','packagingConfirmSectionSize','/sections'):
            self.assertTrue(marker in src, '界面未接入 '+marker)

    def test_real_resolver_default_groups_one_label_two_circles_and_dimensions(self):
        from tech_app.backend.services import packaging_business_part_resolver as resolver
        ir={'source':{},'entities':[
            {'entity_id':'a-e','type':'CIRCLE','bbox':[-20,-20,20,20],'attributes':{'center':[0,0],'radius':20}},
            {'entity_id':'b-e','type':'CIRCLE','bbox':[70,-30,130,30],'attributes':{'center':[100,0],'radius':30}}],
            'texts':[{'entity_id':'name','raw_text':'名称：组合衬片','position':[50,40]}],
            'dimensions':[{'entity_id':'d1','dim_type':'diameter','measured_value':40,'declared_value':40,
                          'target_entity_ids':['point:-20,0','point:20,0']},
                         {'entity_id':'d2','dim_type':'diameter','measured_value':60,'declared_value':60,
                          'target_entity_ids':['point:70,0','point:130,0']}]}
        geometry={'parts':[dict(component('a',[-20,-20,20,20]),part_code='P1'),
                           dict(component('b',[70,-30,130,30]),part_code='P2')]}
        result=resolver.resolve_business_parts('p',ir,geometry,use_model=False)
        doc=parts.business_parts_document(result['reference'],geometry,bindings=result['match'])
        row=next(r for r in doc['business_parts'] if r['name']=='组合衬片')
        self.assertEqual(2,len(row['sections']), '默认归属仍然只选了单个圆：'+str([
            (c.get('id'),c.get('name_anchor_entity_id'),parts.candidate_confidence_percent(c))
            for c in row['geometry_binding'].get('candidates') or []]))
        self.assertEqual(['a-e','b-e'],row['geometry_binding']['entity_ids'])
        self.assertEqual([40,60],[s['confirmed_size']['length_mm'] for s in row['sections']])

    def test_internal_hole_not_extra_material_and_bbox_inside_not_polygon_inside(self):
        s=self.service()
        outer=component('outer',[0,0,100,100])
        hole=component('hole',[10,10,20,20]);hole['role']='hole'
        self.assertEqual(1,len(s.physical_groups([outer,hole])))
        outer['outline_points']=[[0,0],[100,0],[100,20],[20,20],[20,100],[0,100],[0,0]]
        outside=component('in-notch',[40,40,60,60]);outside['role']='hole'
        self.assertEqual(2,len(s.physical_groups([outer,outside])))

    def test_pending_section_bom_never_falls_back_to_confirmed_parent_union(self):
        doc=self.assigned()
        doc['business_parts'][0]['confirmed_size']={'length_mm':280,'width_mm':60}
        rows=bom.business_part_rows(doc)
        self.assertTrue(all(r['length_mm'] is None and r['status']=='needs_input' for r in rows))


    def service(self):
        self.assertIsNotNone(importlib.util.find_spec('tech_app.backend.services.packaging_sections'),
                             '缺少真实组成数据与校验层')
        return importlib.import_module('tech_app.backend.services.packaging_sections')

    def assigned(self):
        return self.service().assign_sections(document(), 'BP-A', [
            {'section_id': 'inside', 'name': '内衬', 'component_ids': ['a'], 'quantity': 1},
            {'section_id': 'outside', 'name': '外衬', 'component_ids': ['b'], 'quantity': 2}], actor='PE1')

    def confirmed(self):
        s = self.service()
        doc = self.assigned()
        for sid, length, width in [('inside',100,40),('outside',80,60)]:
            doc = s.confirm_section_size(doc, 'BP-A', sid, length, width, actor='PE1', note='核对 CAD 标注')
        return doc

    def test_components_keep_independent_size_not_union_bbox(self):
        s = self.service()
        doc = document()
        row = s.hydrate_part(doc['business_parts'][0], doc['geometry_evidence']['components'])
        self.assertEqual(2, len(row['sections']))
        self.assertEqual([100,80], [r['estimated_size']['length_mm'] for r in row['sections']])
        self.assertFalse(any(r.get('confirmed_size') for r in row['sections']))
        self.assertEqual([0,0,280,60], doc['business_parts'][0]['geometry_binding']['bbox'])

    def test_missing_declared_portion_is_pending_not_complete(self):
        s = self.service()
        doc = document()
        row = doc['business_parts'][0]
        row['declared_sections'] = [{'name': '内片'},{'name':'外片'},{'name':'底片'}]
        row['geometry_binding']['component_ids'] = ['a','b']
        row = s.hydrate_part(row, doc['geometry_evidence']['components'])
        self.assertEqual(3, len(row['sections']))
        self.assertEqual('pending_attribution', row['sections'][-1]['status'])
        self.assertFalse(row['sections_complete'])

    def test_assignment_is_atomic_and_changes_version(self):
        source = document()
        before = copy.deepcopy(source)
        doc = self.assigned()
        self.assertEqual(before, source)
        self.assertNotEqual('old', doc['business_parts_hash'])
        self.assertEqual('manual', doc['business_parts'][0]['sections_source'])
        self.assertEqual(['a-e','b-e'], doc['business_parts'][0]['geometry_binding']['entity_ids'])

    def test_rejects_unknown_duplicate_and_foreign_entities(self):
        s = self.service()
        for selections in ([{'component_ids':['missing']}],
                           [{'component_ids':['a']},{'component_ids':['a']}],
                           [{'component_ids':['a'],'entity_ids':['b-e']}],
                           [{'component_ids':['a'],'quantity':0}]):
            with self.subTest(selections=selections), self.assertRaises(ValueError):
                s.assign_sections(document(),'BP-A',selections,actor='PE1')
        doc = document()
        doc['business_parts'].append({'business_part_code':'BP-B','geometry_binding':{
            'status':'bound','entity_ids':['nearby-e']}})
        with self.assertRaisesRegex(ValueError, 'already_assigned'):
            s.assign_sections(doc,'BP-A',[{'component_ids':['nearby']}],actor='PE1')

    def test_whole_part_confirmation_cannot_confirm_multiple_portions(self):
        doc = self.assigned()
        with self.assertRaisesRegex(ValueError,'section_size_confirmation_required'):
            parts.confirm_business_part_size(doc,'BP-A',280,60,actor='PE1',note='union')

    def test_each_size_is_independent_and_all_are_required(self):
        s = self.service()
        doc = self.assigned()
        doc = s.confirm_section_size(doc,'BP-A','inside',100,40,actor='PE1',note='标注')
        row = doc['business_parts'][0]
        with self.assertRaisesRegex(ValueError, 'outside'):
            parts.verified_business_part_input_row(row,doc)
        doc = s.confirm_section_size(doc,'BP-A','outside',80,60,actor='PE1',note='标注')
        verified = parts.verified_business_part_input_row(doc['business_parts'][0],doc)
        self.assertEqual(2, len(verified['sections']))
        self.assertNotIn('length_mm', verified['reference'], '不能向模型传 union 尺寸')

    def test_reassignment_revokes_only_changed_portion_size(self):
        s = self.service()
        doc = self.confirmed()
        updated = s.assign_sections(doc,'BP-A',[
            {'section_id':'inside','name':'内衬','component_ids':['a'],'quantity':1},
            {'section_id':'outside','name':'外衬','component_ids':['nearby'],'quantity':2}],actor='PE1')
        rows = updated['business_parts'][0]['sections']
        self.assertTrue(rows[0]['confirmed_size'])
        self.assertFalse(rows[1]['confirmed_size'])
        self.assertNotEqual(doc['business_parts_hash'],updated['business_parts_hash'])

    def test_bom_expands_portions_with_parent_and_usage_not_parent_union(self):
        doc = self.confirmed()
        rows = bom.business_part_rows(doc)
        self.assertEqual(2,len(rows))
        self.assertEqual([100,80],[r['length_mm'] for r in rows])
        self.assertEqual([2,4],[r['quantity'] for r in rows])
        self.assertEqual(2,len({r['item_key'] for r in rows}))
        self.assertEqual(['BP-A','BP-A'],[json.loads(r['size_source_json'])['parent_part_code'] for r in rows])

    def test_cost_sums_portion_areas_times_usage_and_propagates_gaps(self):
        s = self.service()
        doc = self.confirmed()
        row = parts.verified_business_part_input_row(doc['business_parts'][0],doc)
        inputs = parts.business_cost_inputs(row,quantity=1000)
        self.assertTrue(inputs['ok'])
        self.assertEqual(2,len(inputs['sections']))
        calls=[]
        def calculate(category, variables):
            calls.append(dict(variables))
            return {'amount':variables['cut_length']*variables['cut_width']/1000,'expression':'area'}
        line = s.compute_material(inputs,calculate)
        self.assertEqual(27.2,line['amount'])  # 每盒用量 2 × (4 + 2×4.8)，不计 100mm 空白
        self.assertEqual([100,80],[r['cut_length'] for r in calls])
        self.assertEqual([1000,1000],[r['quote_quantity'] for r in calls])
        bad = s.compute_material(inputs,lambda category,v: {'amount':None,'gap':{'code':'price_missing'}})
        self.assertIsNone(bad['amount'])
        self.assertEqual(2,len(bad['sections']))

    def test_process_inputs_include_every_portion_and_are_not_first_geometry(self):
        doc = self.confirmed()
        row = parts.verified_business_part_input_row(doc['business_parts'][0],doc)
        inputs = parts.business_process_inputs(row)
        self.assertTrue(inputs['ok'])
        self.assertEqual(['inside','outside'],[r['section_id'] for r in inputs['sections']])
        self.assertIn('100',inputs['grounding'])
        self.assertIn('80',inputs['grounding'])

    def test_preview_is_entity_exact_without_neighbour_in_union_gap(self):
        src=(ROOT/'tech_app/frontend/app.js').read_text()
        start=src.index('function packagingPartSceneEntities(')
        end=src.index('\n}',start)+2
        script=src[start:end]+'''\nconsole.log(JSON.stringify(packagingPartSceneEntities(
          {component_ids:['a','b'],entity_ids:['a-e','b-e'],bbox:[0,0,280,60],exact_entities:true},
          {cad_scene:{entities:[{cad_entity_id:'a-e',bbox:[0,0,100,40]},
            {cad_entity_id:'gap-e',bbox:[120,0,140,40]},{cad_entity_id:'b-e',bbox:[200,0,280,60]}]}}
          ).map(x=>x.cad_entity_id)));'''
        result=subprocess.run(['node','-e',script],text=True,capture_output=True,check=True)
        self.assertEqual(['a-e','b-e'],json.loads(result.stdout))

    def test_multi_part_downstream_must_not_pick_first_closed_shape(self):
        src=(ROOT/'tech_app/frontend/app.js').read_text()
        start=src.index('function packagingBusinessPartDownstreamTarget(')
        end=src.index('\n}',start)+2
        script=src[start:end]+'''\nconsole.log(JSON.stringify(packagingBusinessPartDownstreamTarget(
          {business_part_code:'BP-A',sections:[{section_id:'s1'},{section_id:'s2'}],
           geometry_binding:{component_ids:['a','b']}},
          {parts:[{component_id:'a',part_code:'P1',outline_status:'closed'},
                  {component_id:'b',part_code:'P2',outline_status:'closed'}]})));'''
        result=json.loads(subprocess.run(['node','-e',script],text=True,capture_output=True,check=True).stdout)
        self.assertFalse(result['ok'])
        self.assertEqual('multipart_business_route_required',result['code'])
