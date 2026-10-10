"""Generalized drawing fixtures; no BOM, network, or production storage."""
import copy
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tech_app.backend.services import cad_ir, packaging_parts as parts
from tech_app.backend.services import packaging_business_part_resolver as resolver
from tech_app.backend.services import packaging_part_visual_review as review


def ownership_doc():
    candidate = {'id': 'whole', 'candidate_id': 'whole', 'component_ids': ['c'],
                 'entity_ids': ['circle'], 'bbox': [-30, -30, 30, 30]}
    return {'derived_from_drawing': True, 'geometry_evidence': {'components': [
        {'component_id': 'c', 'entity_ids': ['circle'], 'bbox': [-30, -30, 30, 30]}]},
        'business_parts': [
            {'business_part_code': 'first', 'name': '甲', 'sequence_no': 1,
             'geometry_binding': {'status': 'unbound', 'candidates': [candidate]}},
            {'business_part_code': 'last', 'name': '乙', 'sequence_no': 2,
             'confirmed_size': {'length_mm': 60, 'width_mm': 60},
             'geometry_binding': {'status': 'bound', 'bound_by': 'manual',
                                  'component_ids': ['c'], 'entity_ids': ['circle'],
                                  'bbox': [-30, -30, 30, 30], 'candidates': [candidate]},
             'sections': [{'section_id': 'old', 'confirmed_size': {'length_mm': 60}}]}]}


class DrawingOrderCircleSceneTest(unittest.TestCase):
    def test_real_circle_is_a_part_component_not_only_a_hole_record(self):
        import ezdxf
        drawing = ezdxf.new()
        drawing.units = 4
        circle = drawing.modelspace().add_circle((700,300),37)
        stream = io.StringIO()
        drawing.write(stream)
        ir = cad_ir.parse_dxf(stream.getvalue().encode())
        circle_id = next(row['entity_id'] for row in ir['entities'] if row.get('handle') == circle.dxf.handle)
        self.assertTrue(any(circle_id in c['entity_ids'] for c in ir['geometry']['components']))
        extracted = parts.extract(ir)
        self.assertEqual(1,len(extracted['parts']))
        self.assertEqual('closed',extracted['parts'][0]['outline_status'])
        self.assertAlmostEqual(74,extracted['parts'][0]['unfolded_length_mm'])

    def test_spatial_order_not_handle_order_and_translation_invariant(self):
        texts = [{'entity_id': 'z', 'raw_text': '名称：部件甲', 'height': 5, 'position': [0, 100]},
                 {'entity_id': 'a', 'raw_text': '名称：部件乙', 'height': 5, 'position': [200, 99]},
                 {'entity_id': 'b', 'raw_text': '名称：部件丙', 'height': 5, 'position': [0, 0]}]
        for dx, dy, reverse in [(0, 0, False), (1300, -800, True)]:
            changed = copy.deepcopy(texts)
            for row in changed:
                row['position'] = [row['position'][0]+dx, row['position'][1]+dy]
            if reverse:
                changed.reverse()
            rows = resolver.extract_text_anchors({'texts': changed})
            self.assertEqual(['z', 'a', 'b'], [row['entity_id'] for row in rows])

    def test_earlier_can_take_later_candidate_and_invalidates_later_size(self):
        before = ownership_doc()
        before['business_parts'][1]['geometry_binding']['bound_by'] = 'auto'
        changed = parts.set_geometry_binding(before, 'first', [], candidate_id='whole', bound_by='auto')
        first, last = changed['business_parts']
        self.assertEqual(['circle'], first['geometry_binding']['entity_ids'])
        self.assertEqual('unbound', last['geometry_binding']['status'])
        self.assertFalse(last.get('sections'))
        self.assertNotIn('confirmed_size', last)
        self.assertEqual(60, last['confirmed_size_history'][-1]['length_mm'])
        self.assertEqual('bound', before['business_parts'][1]['geometry_binding']['status'])

    def test_later_cannot_reuse_earlier_even_using_component_selection(self):
        document = ownership_doc()
        document['business_parts'].reverse()
        # sequence_no is the persisted drawing order, not the array's accidental order.
        document['business_parts'][0]['sequence_no'] = 1
        document['business_parts'][1]['sequence_no'] = 2
        with self.assertRaisesRegex(ValueError, 'candidate_entities_already_assigned'):
            parts.set_geometry_binding(document, 'first', ['c'], bound_by='auto')

    def test_section_editor_uses_same_earlier_owner_priority(self):
        from tech_app.backend.services import packaging_sections
        before = ownership_doc()
        changed = packaging_sections.assign_sections(before,'first',
            [{'component_ids':['c'],'name':'部分'}],actor='PE1')
        self.assertEqual('unbound',changed['business_parts'][1]['geometry_binding']['status'])

    def test_one_verified_section_can_feed_single_part_without_second_confirmation(self):
        row = {'business_part_code':'one','name':'甲','geometry_binding':{
            'status':'bound','bound_by':'auto','component_ids':['c'],'entity_ids':['e']},
            'sections':[{'section_id':'s','status':'bound','entity_ids':['e'],
                         'component_ids':['c'],'confirmed_size':{'length_mm':74,'width_mm':74,
                         'source':'verified_cad_dimension'}}], 'sections_complete':True}
        verified = parts.verified_business_part_input_row(row,{'derived_from_drawing':True})
        self.assertEqual(74,verified['reference']['length_mm'])

    def test_complete_round_candidate_contains_internal_line_not_bbox_corner(self):
        from math import cos, sin, pi
        polygon = [[30*cos(i*pi/32),30*sin(i*pi/32)] for i in range(65)]
        regions = [
            {'region_id': 'outer', 'component_ids': ['c1'], 'entity_ids': ['e1'],
             'bbox': [-30,-30,30,30], 'outline_status': 'closed', 'outline_points': polygon,
             'entity_total': 1, 'substantial': True},
            {'region_id': 'inside', 'component_ids': ['c2'], 'entity_ids': ['e2'],
             'bbox': [-5,-5,5,5], 'entity_total': 1, 'substantial': True},
            {'region_id': 'corner', 'component_ids': ['c3'], 'entity_ids': ['e3'],
             'bbox': [25,25,29,29], 'entity_total': 1, 'substantial': True}]
        ir = {'entities': [
            {'entity_id':'e1','type':'CIRCLE','attributes':{'center':[0,0],'radius':30}},
            {'entity_id':'e2','type':'LINE','attributes':{'start':[-5,0],'end':[5,0]}},
            {'entity_id':'e3','type':'LINE','attributes':{'start':[25,25],'end':[29,29]}}]}
        groups = review._combined_groups(ir, regions, {}, [])
        complete = [g for g in groups if 'closed_outline_enclosure' in g.get('evidence_reasons', [])]
        self.assertTrue(complete)
        self.assertEqual({'e1','e2'}, set(complete[0]['entity_ids']))

    def test_native_diameter_annotations_survive_ir_and_scene(self):
        import ezdxf
        from tech_app.backend import main
        drawing = ezdxf.new('R2010')
        drawing.units = 4
        ms = drawing.modelspace()
        ms.add_circle((0,0),30)
        dim = ms.add_diameter_dim(center=(0,0),radius=30,angle=30,
                                  override={'dimtxt':3,'dimasz':2})
        dim.render()
        stream = io.StringIO()
        drawing.write(stream)
        ir = cad_ir.parse_dxf(stream.getvalue().encode())
        with patch.object(main.cad_ir,'load_ir',return_value=ir), \
             patch.object(main,'_packaging_cad_layer_roles',return_value={}):
            scene = main._packaging_cad_scene('isolated')
        annotations = [row for row in scene['entities'] if row.get('dimension_id')]
        self.assertTrue(annotations, 'DIMENSION must not vanish from the drawing scene')
        self.assertTrue(any(row.get('kind') == 'text' for row in annotations))
        self.assertTrue(any(len(row.get('points') or []) >= 2 for row in annotations))
        self.assertTrue(all(row.get('dimension_targets') for row in annotations))

    def test_preview_selects_dimension_by_witness_not_component_membership(self):
        source = (Path(__file__).resolve().parents[1]/'tech_app/frontend/app.js').read_text()
        start = source.index('function packagingPartAnnotationEntities(')
        end = source.index('\nfunction ',start+10)
        binding = {'entity_ids':['circle'],'bbox':[-30,-30,30,30]}
        doc = {'cad_scene':{'entities':[
            {'cad_entity_id':'dim:text','dimension_id':'dim','dimension_targets':[[-30,0],[30,0]]},
            {'cad_entity_id':'other:text','dimension_id':'other','dimension_targets':[[100,0],[160,0]]}]}}
        script = source[start:end]+'\nconsole.log(JSON.stringify(packagingPartAnnotationEntities('+json.dumps(binding)+','+json.dumps(doc)+')));'
        result = subprocess.run(['node','-e',script],capture_output=True,text=True,check=True)
        self.assertEqual(['dim:text'],[r['cad_entity_id'] for r in json.loads(result.stdout)])

    def test_candidate_menu_discloses_earlier_block_and_later_takeover(self):
        source = (Path(__file__).resolve().parents[1]/'tech_app/frontend/app.js').read_text()
        self.assertTrue('function packagingCandidateOwnership(' in source,'候选下拉缺少顺序占用提示')
        start = source.index('function packagingCandidateOwnership(')
        end = source.index('\nfunction ',start+10)
        document = ownership_doc()
        script = source[start:end]+'\nconst doc='+json.dumps(document)+';\n' + '''
const candidate={entity_ids:['circle'],component_ids:['c']};
const first=packagingCandidateOwnership(doc.business_parts[0],candidate,doc);
const last=packagingCandidateOwnership(doc.business_parts[1],candidate,doc);
doc.business_parts[0].geometry_binding={status:'bound',entity_ids:['circle'],component_ids:['c']};
console.log(JSON.stringify([first,last,packagingCandidateOwnership(doc.business_parts[1],candidate,doc)]));
'''
        result = subprocess.run(['node','-e',script],capture_output=True,text=True,check=True)
        first,last,blocked = json.loads(result.stdout)
        self.assertFalse(first['blocked'])
        self.assertEqual(['乙'],first['displaces'])
        self.assertTrue(blocked['blocked'])
        self.assertEqual(['甲'],blocked['owners'])
