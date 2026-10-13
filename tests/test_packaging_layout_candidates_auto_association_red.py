import unittest
from pathlib import Path
from tech_app.backend.services import packaging_layout as l

class LayoutCandidateTests(unittest.TestCase):
    def test_candidate_cannot_be_confirmed_without_sheet_or_n_up(self):
        doc={'layout_rows':[{'entity_id':'c','kind':'drawing_layout_candidate','n_up':None,'sheet_mm':None}]}
        with self.assertRaisesRegex(ValueError,'layout_evidence_incomplete'):
            l.set_layout_confirmation(doc,'c',True)

    def test_frontend_reads_candidates_and_renders_associated_names(self):
        source=(Path(__file__).resolve().parents[1]/'tech_app/frontend/assembly-integration.js').read_text()
        body=source.split('function aiRenderPackagingLayout(',1)[1].split('\nfunction ',1)[0]
        self.assertIn("row.kind === 'drawing_layout_candidate'",body)
        self.assertIn('无需再次保存',body)
        self.assertIn('association_gaps',body)
        self.assertIn('排模／草稿候选',body)

    def test_repeated_names_outside_main_frame_are_candidates_not_confirmed_layouts(self):
        ir={'entities':[{'entity_id':'frame','kind':'polyline','closed':True,'bbox':[0,0,300,300],
                        'attributes':{'points':[[0,0],[300,0],[300,300],[0,300]]}}]}
        anchors=[{'entity_id':key,'name':name,'position':pos,'raw_text':name} for key,name,pos in
                 [('a','面纸',[20,200]),('b','底板',[100,200]),('c','面纸',[-100,200])]]
        rows=l.extract_drawing_layout_candidates(ir,anchors)
        self.assertEqual(1,len(rows))
        self.assertEqual('c',rows[0]['entity_id'])
        self.assertEqual('drawing_layout_candidate',rows[0]['kind'])
        self.assertFalse(rows[0]['confirmed'])
        self.assertIsNone(rows[0]['n_up'])

    def test_compound_names_are_associated_to_existing_parts_in_same_scheme(self):
        rows=[{'text':'10PC圆盒 盖/地内外圈围边面纸：200g双铜 排1模','scheme_id':'1','entity_id':'l'}]
        parts=[{'name':'地盒内、外圈围边面纸','business_part_code':'p','scheme_id':'1'},
               {'name':'地盒内、外圈围边面纸','business_part_code':'wrong','scheme_id':'2'}]
        result=l.auto_assign_layout_rows(rows,parts)[0]
        self.assertEqual(['p'],result['part_codes'])
        self.assertFalse(result.get('confirmed',False))

    def test_ambiguous_names_are_not_assigned_to_first_row(self):
        rows=[{'text':'底板 排1模','entity_id':'l'}]
        parts=[{'name':'底板','business_part_code':'a'},{'name':'底板','business_part_code':'b'}]
        result=l.auto_assign_layout_rows(rows,parts)[0]
        self.assertFalse(result.get('part_codes'))
        self.assertTrue(result['association_gaps'])

    def test_manual_selection_is_preserved(self):
        row={'text':'底板 排1模','entity_id':'l','part_codes':['manual'],'assignment_source':'user_selection'}
        self.assertEqual(row,l.auto_assign_layout_rows([row],[{'name':'底板','business_part_code':'a'}])[0])
