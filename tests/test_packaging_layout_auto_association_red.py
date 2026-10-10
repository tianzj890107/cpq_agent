import unittest
from pathlib import Path
from tech_app.backend.services import packaging_layout as layout

class AssociationTests(unittest.TestCase):
    def test_explicit_cad_names_assign_multiple_without_yield(self):
        rows=[{'entity_id':'E','text':'地盒底板、面纸各排4模','part_codes':[]}]
        parts=[{'business_part_code':'A','name':'地盒底板'},{'business_part_code':'B','name':'面纸'},{'business_part_code':'C','name':'底板'}]
        got=layout.auto_assign_layout_rows(rows,parts)
        self.assertEqual(['A','B'],got[0]['part_codes'])
        self.assertFalse(got[0].get('confirmed',False))
        self.assertEqual([],rows[0]['part_codes'])

    def test_no_names_no_guess(self):
        got=layout.auto_assign_layout_rows([{'text':'排4模','part_codes':[]}],[{'name':'底板','business_part_code':'A'}])
        self.assertEqual([],got[0]['part_codes'])

    def test_manual_empty_preserved(self):
        got=layout.auto_assign_layout_rows([{'text':'底板排4模','part_codes':[],'assignment_source':'user_selection'}],[{'name':'底板','business_part_code':'A'}])
        self.assertEqual([],got[0]['part_codes'])

    def test_header_contains_save_outside_details(self):
        root=Path(__file__).resolve().parents[1]
        js=(root/'tech_app/frontend/assembly-integration.js').read_text()
        self.assertIn('ai-layout-association-bar',js)
        self.assertIn('ai-layout-association-bar', (root/'tech_app/frontend/assembly-integration.css').read_text())
