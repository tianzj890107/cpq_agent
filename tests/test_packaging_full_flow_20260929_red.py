"""2026-09-29 真流程缺口：报价文本、财务回传 ACL、包装真实枚举回显。"""
from __future__ import annotations

import pathlib
import importlib
import subprocess
import unittest
from unittest import mock
import base64

import cpq_agent_server

from tech_app.backend.services import project_access, requirement_service
from tech_app.backend.models.workflow import RequirementDoc
from tech_app.backend.services.packaging_semantics import provenance as cad_provenance
drawing_steps = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.steps")


ROOT = pathlib.Path(__file__).resolve().parents[1]


class QuoteTextAutofill(unittest.TestCase):
    TEXT = ("700ML 双开门酒盒；盒型编码 YT-DWG-WINE-700ML；盒族 书型盒/双开门礼盒；"
            "闭合方式 双开门/对开；内尺寸 220.5×90×90 mm；面纸 225g；"
            "EVA 内托；V 槽；数量 1000 只。")

    def test_explicit_fields_from_quote_are_recovered_without_model(self):
        fields = requirement_service.extract_explicit_packaging_quote_fields(self.TEXT)
        self.assertEqual(fields["box_type"], "YT-DWG-WINE-700ML")
        self.assertEqual(fields["box_family"], "书型盒/双开门礼盒")
        self.assertEqual(fields["closure_type"], "双开门/对开")
        self.assertEqual((fields["inner_length"], fields["inner_width"], fields["inner_height"]),
                         (220.5, 90.0, 90.0))
        self.assertEqual(fields["face_paper_gsm"], 225.0)
        self.assertEqual(fields["insert_type"], "EVA内托")
        self.assertEqual(fields["v_groove"], "yes")
        self.assertEqual(fields["quote_quantity"], "1000")

    def test_ambiguous_and_unlabelled_dimensions_do_not_become_inner_size(self):
        fields = requirement_service.extract_explicit_packaging_quote_fields(
            "纸张开料 307.07×528.89 mm；内尺寸 220×90×90 mm；内尺寸 225×90×90 mm")
        for key in ("inner_length", "inner_width", "inner_height"):
            self.assertNotIn(key, fields)

    def test_existing_values_and_sources_are_not_overwritten(self):
        existing = {"industry": "packaging", "inner_length": 300,
                    "field_sources": {"inner_length": "manual"}}
        merged = requirement_service.merge_explicit_packaging_quote_fields(existing, self.TEXT)
        self.assertEqual(merged["inner_length"], 300)
        self.assertEqual(merged["field_sources"]["inner_length"], "manual")
        self.assertEqual(merged["inner_width"], 90)
        self.assertEqual(merged["field_sources"]["inner_width"], "user_text")
        self.assertEqual(merged["quote_text_field_evidence"]["inner_width"], self.TEXT)

    def test_save_draft_applies_text_fields_on_real_write_path(self):
        doc = RequirementDoc(project_id="ab587d1e45b6", requirement_no="REQ-TEST",
                             data={"industry": "packaging", "quote_requirement_text": self.TEXT})
        with mock.patch.object(requirement_service.store, "load_requirement", return_value=None), \
             mock.patch.object(requirement_service.store, "save_requirement") as save, \
             mock.patch.object(requirement_service.store, "audit"):
            result = requirement_service.save_requirement_draft(
                "ab587d1e45b6", doc, user={"username": "PE1", "role": "process_manager"})
        self.assertEqual(result["data"]["inner_length"], 220.5)
        self.assertEqual(result["data"]["field_sources"]["box_type"], "user_text")
        self.assertEqual(result["data"]["field_provenance"]["inner_length"]["status"],
                         "needs_confirmation")
        self.assertEqual(save.call_args.args[1]["data"]["inner_length"], 220.5)

    def test_quote_original_has_priority_over_longer_generic_description(self):
        doc = RequirementDoc(project_id="ab587d1e45b6", requirement_no="REQ-TEST",
                             data={"industry": "packaging", "quote_requirement_text": self.TEXT,
                                   "description": "客户备注：请注意交期。" * 50})
        with mock.patch.object(requirement_service.store, "load_requirement", return_value=None), \
             mock.patch.object(requirement_service.store, "save_requirement"), \
             mock.patch.object(requirement_service.store, "audit"):
            result = requirement_service.save_requirement_draft(
                "ab587d1e45b6", doc, user={"username": "PE1", "role": "process_manager"},
                current=None)
        self.assertEqual(result["data"]["box_type"], "YT-DWG-WINE-700ML")

    def test_later_full_form_save_keeps_quote_text_and_evidence(self):
        old = RequirementDoc(project_id="ab587d1e45b6", requirement_no="REQ-TEST",
                             data={"industry": "packaging", "quote_requirement_text": self.TEXT,
                                   "inner_length": 220.5,
                                   "field_sources": {"inner_length": "user_text"},
                                   "quote_text_field_evidence": {"inner_length": self.TEXT}}).model_dump()
        incoming = RequirementDoc(project_id="ab587d1e45b6", requirement_no="REQ-TEST",
                                  data={"industry": "packaging", "inner_length": 220.5})
        with mock.patch.object(requirement_service.store, "save_requirement"), \
             mock.patch.object(requirement_service.store, "audit"):
            result = requirement_service.save_requirement_draft(
                "ab587d1e45b6", incoming, user={"username": "PE1", "role": "process_manager"},
                current=old)
        self.assertEqual(result["data"]["quote_requirement_text"], self.TEXT)
        self.assertEqual(result["data"]["quote_text_field_evidence"]["inner_length"], self.TEXT)

    def test_cad_conflict_does_not_silently_replace_sales_text(self):
        current = {"project_id": "ab587d1e45b6", "requirement_no": "REQ-TEST",
                   "data": {"industry": "packaging", "face_paper_gsm": 225.0,
                            "field_sources": {"face_paper_gsm": "user_text"},
                            "quote_text_field_evidence": {"face_paper_gsm": self.TEXT}}}
        semantics = {"semantics_id": "CAD-TEST", "fields": {
            "face_paper_gsm": {"value": 235.0, "status": "confirmed",
                               "origin": "cad_annotation", "confidence": 0.9}}}
        with mock.patch.object(cad_provenance.requirement_service.store,
                               "load_requirement", return_value=current), \
             mock.patch.object(cad_provenance.requirement_service,
                               "save_requirement_draft", side_effect=lambda _p, doc, **_kw: doc.model_dump()), \
             mock.patch.object(cad_provenance.requirement_service.store, "audit"):
            result = cad_provenance.apply_to_requirement("ab587d1e45b6", semantics)
        data = result["data"]
        self.assertEqual(data["face_paper_gsm"], 225.0)
        self.assertEqual(data["field_sources"]["face_paper_gsm"], "user_text")
        self.assertEqual(data["field_provenance"]["face_paper_gsm"]["status"], "conflict")

    def test_equivalent_numeric_text_and_cad_values_are_not_false_conflicts(self):
        current = {"project_id": "ab587d1e45b6", "requirement_no": "REQ-TEST",
                   "data": {"industry": "packaging", "face_paper_gsm": "225",
                            "field_sources": {"face_paper_gsm": "user_text"}}}
        semantics = {"semantics_id": "CAD-TEST", "fields": {
            "face_paper_gsm": {"value": 225.0, "status": "confirmed",
                               "origin": "confirmed_from_cad"}}}
        with mock.patch.object(cad_provenance.requirement_service.store,
                               "load_requirement", return_value=current), \
             mock.patch.object(cad_provenance.requirement_service,
                               "save_requirement_draft", side_effect=lambda _p, doc, **_kw: doc.model_dump()), \
             mock.patch.object(cad_provenance.requirement_service.store, "audit"):
            result = cad_provenance.apply_to_requirement("ab587d1e45b6", semantics)
        self.assertEqual(result["data"]["field_provenance"]["face_paper_gsm"]["status"],
                         "confirmed")

    def test_drawing_board_calls_quote_text_pending_not_missing(self):
        previous = {"inner_length": {"origin": "user_text", "status": "needs_confirmation",
                                     "value": 220.5}}
        board = drawing_steps._board_of(
            "inner_length", {"status": "missing", "value": None},
            previous, {"inner_length": "user_text"}, False)
        self.assertEqual(board, "pending")


class FinanceHandoffAcl(unittest.TestCase):
    def test_only_packaging_send_is_action_scoped(self):
        pid = "ab587d1e45b6"
        self.assertTrue(project_access.is_contribute_route(
            "POST", f"/api/projects/{pid}/requirement/packaging-quote/send"))
        self.assertFalse(project_access.is_contribute_route(
            "PUT", f"/api/projects/{pid}/requirement"))
        self.assertFalse(project_access.is_contribute_route(
            "POST", f"/api/projects/{pid}/requirement/packaging-bom"))


class QuickQuoteDrawingConflict(unittest.TestCase):
    def test_sales_value_is_compared_with_dwg_value(self):
        parsed = {"kind": "drawing", "fields": {"units": "mm",
                  "material_notes": ["235g面纸底PET光银"]}, "capability": {}}
        body = {"name": "wine.dwg", "data": base64.b64encode(b"drawing").decode(),
                "match": False, "fallback": {"face_paper_gsm": 225}}
        with mock.patch.object(cpq_agent_server.cpq_quick_quote_file,
                               "parse_file", return_value=parsed):
            result = cpq_agent_server._handle_quick_quote_parse(body)
        self.assertEqual(result["inputs"]["face_paper_gsm"], 235.0)
        self.assertTrue(any("225" in text and "235" in text
                            for text in result["warnings"]))


class PackagingEnumRoundTrip(unittest.TestCase):
    def test_unknown_but_real_saved_value_remains_selectable(self):
        source = (ROOT / "tech_app/frontend/requirement-create.js").read_text()
        body = source.split("function rcSelect(", 1)[1].split("function rcSection(", 1)[0]
        js = ("const rcFieldValue=(name)=>name==='box_type'?'YT-DWG-WINE-700ML':'双开门/对开';"
              "const rcAiBadge=()=>'';const esc=x=>String(x);"
              "function rcSelect(" + body + ";"
              "console.log(rcSelect('盒型','box_type', [['','请选择'],['book','书型盒']]));"
              "console.log(rcSelect('闭合方式','closure_type', [['','请选择'],['other','其他']]));")
        run = subprocess.run(["node", "-e", js], text=True, capture_output=True, check=True)
        self.assertIn('value="YT-DWG-WINE-700ML" selected', run.stdout)
        self.assertIn('value="双开门/对开" selected', run.stdout)


if __name__ == "__main__":
    unittest.main()
