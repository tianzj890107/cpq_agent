"""合成 CAD 证据红测，不读取用户答案或访问线上数据库。"""
import copy
import pathlib
import subprocess
import unittest

from tech_app.backend.services import packaging_parts as parts
from tech_app.backend.services import packaging_part_visual_review as review

ROOT = pathlib.Path(__file__).resolve().parents[1]


class LayoutFirstSelectionTests(unittest.TestCase):
    def test_ui_and_backend_candidate_scores_agree(self):
        import json
        js = (ROOT / 'tech_app/frontend/app.js').read_text()
        start = js.index('function packagingCandidateConfidence(')
        end = js.find('\nfunction ',start+1)
        candidates = [dict(entity_ids=['e'],component_ids=['c'],geometry_status=status,
                           dimension_spatial=dimension,anchor_in_region=False,
                           evidence_reasons=['layout_frame','raw_cad_entities','caption_column_owner',
                                             'complete_closed_outline'])
                      for status in ['supported','ambiguous'] for dimension in [True,False]]
        script = js[start:end] + '\nconsole.log(JSON.stringify('+json.dumps(candidates)+'.map(packagingCandidateConfidence)));'
        result = subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual([parts.candidate_confidence_percent(c) for c in candidates],json.loads(result.stdout))

    def test_external_caption_aligns_with_its_column_not_neighbour_edge(self):
        regions = [dict(region_id='left',component_ids=['l'],bbox=[0,0,100,400]),
                   dict(region_id='right',component_ids=['r'],bbox=[120,0,220,400])]
        ir = {'texts':[{'entity_id':'t-left','raw_text':'名称：衬板甲','position':[50,-25],'height':5},
                       {'entity_id':'t-right','raw_text':'名称：衬板乙','position':[170,-25],'height':5}]}
        review._assign_local_name_owners(ir,regions,[])
        self.assertEqual(['t-left','t-right'],[x['name_anchor_entity_id'] for x in regions])

    def test_caption_at_neighbour_edge_does_not_steal_column(self):
        region = dict(region_id='a',component_ids=['a'],bbox=[200,100,300,500])
        ir = {'texts':[{'entity_id':'own','raw_text':'名称：内层灰板','position':[200,70],'height':5},
                       {'entity_id':'next','raw_text':'名称：盒背灰板','position':[302,110],'height':5}]}
        review._assign_local_name_owners(ir,[region],[])
        self.assertEqual('own',region['name_anchor_entity_id'])

    def test_labels_above_are_not_forced_to_wine_below_convention(self):
        regions = [dict(region_id='a',bbox=[0,0,100,100]),dict(region_id='b',bbox=[150,0,250,100])]
        ir = {'texts':[{'entity_id':'a','raw_text':'名称：面纸甲','position':[20,120],'height':5},
                       {'entity_id':'b','raw_text':'名称：面纸乙','position':[170,120],'height':5}]}
        review._assign_local_name_owners(ir,regions,[])
        self.assertEqual(['a','b'],[r['name_anchor_entity_id'] for r in regions])

    def test_reselection_invalidates_unversioned_reference_size(self):
        from tests.test_packaging_drawing_order_circle_scene_red import ownership_doc
        document = ownership_doc()
        document['business_parts'][0]['reference'] = {'length_mm':999,'width_mm':888,
                                                     'material_text':'灰板'}
        changed = parts.set_geometry_binding(document,'first',[],candidate_id='whole',bound_by='manual')
        row = changed['business_parts'][0]
        self.assertIsNone(row['reference'].get('length_mm'))
        self.assertEqual('灰板',row['reference']['material_text'])
        self.assertEqual(999,row['reference_size_history'][-1]['length_mm'])

    def test_raw_layout_members_can_bind_only_when_present_in_cad_registry(self):
        from tests.test_packaging_drawing_order_circle_scene_red import ownership_doc
        document = ownership_doc()
        candidate = document['business_parts'][0]['geometry_binding']['candidates'][0]
        candidate.update(entity_ids=['circle','short-arc'],bbox=[-30,-30,40,30],
                         frame_id='frame',name_anchor_entity_id='label',
                         evidence_reasons=['layout_frame','raw_cad_entities','caption_column_owner'])
        document['geometry_evidence']['raw_entities'] = {'circle':{'bbox':[-30,-30,30,30]},
                                                       'short-arc':{'bbox':[30,0,40,10]}}
        document['unassigned_candidates'] = [{**candidate,'name_anchor_entity_id':'',
                                              'evidence_reasons':['raw_cad_entities']}]
        document['business_parts'] = document['business_parts'][:1]
        changed = parts.set_geometry_binding(document,'first',[],candidate_id='whole',bound_by='auto')
        row = changed['business_parts'][0]
        self.assertEqual('bound',row['geometry_binding']['status'])
        self.assertEqual({'circle','short-arc'},set(row['sections'][0]['entity_ids']))
        self.assertEqual(70,row['sections'][0]['estimated_size']['length_mm'])
        document['geometry_evidence']['raw_entities'].pop('short-arc')
        incomplete = parts.set_geometry_binding(document,'first',[],candidate_id='whole',bound_by='auto')
        self.assertEqual('partial',incomplete['business_parts'][0]['geometry_binding']['status'])

    def test_distinct_round_portions_remain_separate_not_one_union_size(self):
        from tech_app.backend.services import packaging_sections
        components = [dict(component_id='a',entity_ids=['circle-a'],drawing_bbox=[0,0,20,20],
                           role='cut',outline_status='closed',outline_points=[[0,0],[20,0],[20,20],[0,20]]),
                      dict(component_id='b',entity_ids=['circle-b'],drawing_bbox=[40,0,70,30],
                           role='cut',outline_status='closed',outline_points=[[40,0],[70,0],[70,30],[40,30]])]
        row = {'name':'内外圈面纸','geometry_binding':{'component_ids':['a','b'],
                                                       'entity_ids':['circle-a','circle-b']}}
        result = packaging_sections.hydrate_part(row,components)
        self.assertEqual(2,len(result['sections']))
        self.assertEqual([20,30],[s['estimated_size']['length_mm'] for s in result['sections']])
        candidate = {'id':'both','component_ids':['a','b'],'entity_ids':['circle-a','circle-b'],
                     'bbox':[0,0,70,30],'name_anchor_entity_id':'label',
                     'evidence_reasons':['layout_frame','raw_cad_entities','caption_column_owner']}
        document = {'derived_from_drawing':True,'geometry_evidence':{'components':components,
                    'raw_entities':{eid:{'bbox':c['drawing_bbox']} for c in components for eid in c['entity_ids']}},
                    'business_parts':[{'business_part_code':'one','name':'内外圈面纸',
                                      'geometry_binding':{'candidates':[candidate]}}]}
        bound = parts.set_geometry_binding(document,'one',[],candidate_id='both',bound_by='auto')['business_parts'][0]
        refreshed = packaging_sections.hydrate_part(bound,components)
        self.assertEqual(2,len(refreshed['sections']))
        self.assertEqual([20,30],[s['estimated_size']['length_mm'] for s in refreshed['sections']])

    def test_compound_part_uses_resolved_owner_not_second_nearest_name_pass(self):
        ir = {'entities':[], 'texts':[
            {'entity_id':'compound','raw_text':'地盒内、外圈围边面纸','position':[0,-5],'height':5},
            {'entity_id':'neighbour','raw_text':'名称：底板','position':[100,200],'height':5}]}
        regions = [dict(region_id='r'+str(i),component_ids=['c'+str(i)],entity_ids=['e'+str(i)],
                        bbox=[i*35,0,i*35+25,25],substantial=True,
                        name_anchor_entity_id='compound' if i<2 else 'neighbour') for i in range(3)]
        groups = review._compound_named_groups(ir,regions,{},[])
        self.assertTrue(groups)
        self.assertEqual({'c0','c1'},set(groups[0]['component_ids']))

    def test_row_band_cannot_bridge_different_rows_via_tall_component(self):
        rows = [dict(business_part_code=code,geometry_binding={'bbox':box}) for code,box in
                [('tall',[100,0,120,140]),('upper',[0,110,20,130]),('lower',[0,0,20,20])]]
        result = parts.order_business_parts_by_regions(rows)
        self.assertLess(next(r['sequence_no'] for r in result if r['business_part_code']=='upper'),
                        next(r['sequence_no'] for r in result if r['business_part_code']=='lower'))

    def test_layout_zone_survives_missing_scheme_heading(self):
        from tech_app.backend.services import packaging_business_part_resolver as resolver
        ir = {'texts':[{'entity_id':'heading','raw_text':'外盒','position':[0,200]},
                       {'entity_id':'own','raw_text':'名称：底板','position':[0,100]},
                       {'entity_id':'layout1','raw_text':'底板：250g白卡 400*300mm 排2模','position':[1000,100]},
                       {'entity_id':'layout2','raw_text':'面纸：157g双铜 500*400mm 排1模','position':[1000,0]},
                       {'entity_id':'repeat','raw_text':'名称：底板','position':[1100,100]}]}
        anchors = resolver.extract_text_anchors(ir,layout_aware=True)
        self.assertEqual('layout_zone_label',next(a['excluded'] for a in anchors if a['entity_id']=='repeat'))
        self.assertEqual(['own'],[a['entity_id'] for a in anchors if not a.get('excluded')])

    def test_preview_does_not_refit_to_persisted_old_binding(self):
        js = (ROOT / 'tech_app/frontend/app.js').read_text()
        start = js.index('function openPackagingBusinessPart(')
        end = js.find('\nfunction ',start+1)
        self.assertNotIn('highlightPackagingBusinessPart(wanted, (binding.component_ids',js[start:end])

    def test_highlight_can_preserve_annotation_inclusive_preview_viewbox(self):
        js = (ROOT / 'tech_app/frontend/app.js').read_text()
        start = js.index('function highlightPackagingBusinessPart(')
        end = js.find('\nfunction ',start+1)
        script = '''
const assert=require('assert');let currentPackagingBusinessPartCode='';let fits=0;
const host={querySelectorAll:()=>[{getAttribute:k=>({'data-cad-entity-id':'e','data-bbox':'0,0,10,20'})[k]||'',
classList:{toggle(){}}}]};
function packagingCadPlanViewer(){return host;}
function packagingCadPlanRange(){return {x:0,y:0,width:10,height:20};}
function fitPackagingCadPlan(){fits++;}
''' + js[start:end] + '''
assert.strictEqual(highlightPackagingBusinessPart('part',['e'],true).targets,1);
assert.strictEqual(fits,0);
highlightPackagingBusinessPart('part',['e']);assert.strictEqual(fits,1);
'''
        result = subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_identical_entities_from_region_and_layout_use_one_slot(self):
        regions = [{'region_id':'region:a','substantial':True,'component_ids':['ca'],
                    'entity_ids':['a','b'],'bbox':[0,0,30,40],'outline_status':'closed'}]
        groups = [{'candidate_id':'layout:a','component_ids':['ca'],'entity_ids':['b','a'],
                   'bbox':[0,0,30,40],'region_ids':['region:a'],
                   'evidence_reasons':['layout_frame','raw_cad_entities'],
                   'geometry_status':'ambiguous'}]
        candidates = review._candidates((15,-5),regions,'region:a',[],groups)
        self.assertEqual(1,len(candidates))
        self.assertEqual({'region:a','layout:a'},set(candidates[0]['source_candidate_ids']))

    def test_verified_whole_region_beats_its_internal_fragment(self):
        fragment = {'id': 'piece', 'entity_ids': ['a'], 'component_ids': ['ca'],
                    'bbox': [0, 0, 12, 70], 'geometry_status': 'supported',
                    'anchor_in_region': True, 'distance_mm': 0}
        whole = {'id': 'layout:synthetic', 'entity_ids': ['a', 'b', 'c'],
                 'component_ids': ['ca', 'cb'], 'bbox': [0, 0, 130, 70],
                 'geometry_status': 'supported', 'dimension_spatial': True,
                 'name_anchor_entity_id': 'caption-outside', 'anchor_in_region': False,
                 'evidence_reasons': ['layout_frame', 'raw_cad_entities',
                                      'spatial_bbox_cluster', 'complete_closed_outline']}
        self.assertGreater(parts.candidate_confidence_percent(whole),
                           parts.candidate_confidence_percent(fragment),
                           '完整且有标注证据的区域不能输给靠近文字的内部碎片')

    def test_unknown_layers_do_not_prove_same_boundary_role(self):
        members = [dict(region_id='r'+str(i), component_ids=['c'+str(i)],
                        entity_ids=['e'+str(i)], bbox=[i*10, 0, i*10+8, 20])
                   for i in range(2)]
        ir = {'entities': [dict(entity_id='e'+str(i), layer='unresolved', kind='line')
                           for i in range(2)]}
        group = review._group_record(members, ir, {'r0': 'v', 'r1': 'v'})
        self.assertNotIn('same_boundary_role', group['evidence_reasons'])

    def test_order_uses_final_regions_with_row_tolerance_and_preserves_ids(self):
        order = getattr(parts, 'order_business_parts_by_regions', None)
        self.assertTrue(callable(order), '需要区域阅读顺序纯函数，不能只排序件名文字')
        rows = [dict(business_part_code=code, geometry_binding={'bbox': box})
                for code, box in [('bottom', [0, 0, 40, 30]),
                                  ('right', [120, 100, 160, 130]),
                                  ('left', [0, 101, 40, 131])]]
        before = copy.deepcopy(rows)
        for reverse, dx, dy in [(False, 0, 0), (True, 711, -241)]:
            shifted = copy.deepcopy(rows[::-1] if reverse else rows)
            for row in shifted:
                b = row['geometry_binding']['bbox']
                row['geometry_binding']['bbox'] = [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy]
            result = order(shifted)
            self.assertEqual(['left', 'right', 'bottom'], [r['business_part_code'] for r in result])
            self.assertEqual([1, 2, 3], [r['sequence_no'] for r in result])
        self.assertEqual(before, rows)

    def test_card_does_not_use_size_from_previous_candidate(self):
        js = (ROOT / 'tech_app/frontend/app.js').read_text()
        start = js.index('function packagingBusinessPartSizeText(')
        end = js.find('\nfunction ', start+1)
        body = js[start:end]
        script = body + '''
const assert = require('assert');
const row = {
  confirmed_size: {length_mm: 200, width_mm: 400, candidate_id: 'old'},
  reference: {length_mm: 200, width_mm: 400, candidate_id: 'old'},
  geometry_binding: {candidate_id: 'new', length_mm: 42, width_mm: 93,
    candidates: [{id: 'new', bbox: [0,0,42,93]}]},
  sections: [{confirmed_size: {length_mm:42,width_mm:93,candidate_id:'new',
    source:'verified_cad_dimension'},estimated_size:{length_mm:42,width_mm:93}}]
};
assert.strictEqual(packagingBusinessPartSizeText(row), '42.00 × 93.00 mm');
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_unversioned_legacy_size_cannot_override_current_cad_section(self):
        js = (ROOT / 'tech_app/frontend/app.js').read_text()
        start = js.index('function packagingBusinessPartSizeText(')
        end = js.find('\nfunction ',start+1)
        script = js[start:end] + '''
const assert=require('assert');
const row={confirmed_size:{length_mm:200,width_mm:400},
geometry_binding:{candidate_id:'new',component_ids:['new-component'],length_mm:42,width_mm:93},
sections:[{component_ids:['new-component'],confirmed_size:{length_mm:42,width_mm:93,source:'verified_cad_dimension'}}]};
assert.strictEqual(packagingBusinessPartSizeText(row),'42.00 × 93.00 mm');
'''
        result = subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
