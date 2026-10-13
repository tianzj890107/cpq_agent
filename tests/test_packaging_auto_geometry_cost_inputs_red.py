import copy
import unittest
from tech_app.backend.services import packaging_parts as p

class AutoGeometryInputs(unittest.TestCase):
    def test_frontend_cost_and_process_allow_selected_geometry(self):
        from tests.test_packaging_business_part_size_cost_entry_red import run_cases
        row=self.row()
        for name in ('packagingBusinessPartSizeCostTarget','packagingBusinessPartProcessTarget'):
            result=run_cases(name,[[row,{'derived_from_drawing':True}]])['results'][0]
            self.assertTrue(result['ok'],result)
            self.assertTrue(result['value']['ok'],result)

    def test_strict_mode_still_requires_size_confirmation(self):
        with self.assertRaises(ValueError):
            p.verified_business_part_input_row(self.row(),{'derived_from_drawing':True,'auto_geometry_inputs':False})
    def row(self):
        return {'business_part_code':'DWG-P','name':'面纸','reference':{'material_text':'225G面纸'},
            'geometry_binding':{'status':'bound','bound_by':'auto','component_ids':['c'],
                'entity_ids':['e'],'size_confirmed':False,'bbox':[10,20,310,170]}}

    def test_selected_candidate_is_usable_without_manual_confirmation(self):
        row=self.row(); before=copy.deepcopy(row)
        value=p.verified_business_part_input_row(row,{'derived_from_drawing':True})
        ref=p.business_part_reference_block(value)
        self.assertEqual((300,150),(ref['length_mm'],ref['width_mm']))
        self.assertEqual('auto_selected_cad_geometry',ref['size_source'])
        self.assertEqual('',p.business_size_unconfirmed_reason(value))
        self.assertNotIn('confirmed_size',value)
        self.assertEqual(before,row)

    def test_missing_entities_still_rejected(self):
        row=self.row();row['geometry_binding']['entity_ids']=[]
        with self.assertRaises(ValueError): p.verified_business_part_input_row(row,{'derived_from_drawing':True})

    def test_each_section_uses_own_size(self):
        row=self.row();row['geometry_binding'].update(entity_ids=['e1','e2'],component_ids=['c1','c2'])
        row.update(sections_complete=True,sections=[
            {'section_id':'s1','name':'片1','quantity':1,'status':'bound','entity_ids':['e1'],'component_ids':['c1'],
             'estimated_size':{'length_mm':120,'width_mm':60}},
            {'section_id':'s2','name':'片2','quantity':1,'status':'bound','entity_ids':['e2'],'component_ids':['c2'],
             'estimated_size':{'length_mm':80,'width_mm':40}}])
        value=p.verified_business_part_input_row(row,{'derived_from_drawing':True})
        from tech_app.backend.services.packaging_sections import section_input_rows
        children=section_input_rows(value)
        self.assertEqual([120,80],[p.business_part_reference_block(c)['length_mm'] for c in children])
        self.assertTrue(all(not s.get('confirmed_size') for s in value['sections']))
