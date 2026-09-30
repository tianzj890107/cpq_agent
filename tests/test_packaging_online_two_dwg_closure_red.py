"""线上两张 DWG 验收回归；无网络、无业务库写入。"""
from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cpq_agent_server import quote_step_completion_gate, _assert_formal_packaging_quote
import cpq_wf
import cpq_packaging_quote
from tech_app.backend.services import packaging_parts
from tech_app.backend.services import packaging_business_part_resolver as resolver
from tech_app.backend.services import home_card
from tech_app.backend.services import packaging_bom


def package(*, gaps=True):
    return {"industry": "packaging", "source": {"project_id": "p1"},
            "cost": {"total_cost": 4.4, "material_total": 0 if gaps else 2.4,
                     "labor_total": 0,
                     "has_gaps": gaps},
            "gaps": [{"code": "material_price_missing"}] if gaps else [],
            "requirement": {"quote_quantity": 1000}}


def quote(*, draft=True):
    return {"industry": "packaging", "cost_total": 4.4,
            "quote_quantity": 1000, "net_unit_price": 5.9,
            "draft": draft, "publish_blocked": draft,
            "gap_count": 1 if draft else 0}


class PackagingQuoteGateTest(unittest.TestCase):
    def test_step3_accepts_real_packaging_package_without_battery_rows(self):
        self.assertTrue(quote_step_completion_gate(
            3, {"packaging_package": package(), "packaging_quote": quote()})["ok"])

    def test_step4_accepts_real_packaging_quote_without_battery_rows(self):
        self.assertTrue(quote_step_completion_gate(
            4, {"packaging_package": package(), "packaging_quote": quote()})["ok"])

    def test_step5_accepts_packaging_quote_with_traceable_draft(self):
        self.assertTrue(quote_step_completion_gate(
            5, {"packaging_package": package(), "packaging_quote": quote()})["ok"])

    def test_step6_rejects_draft_even_if_detail_looks_complete(self):
        data = {"packaging_package": package(), "packaging_quote": quote(),
                "s5_detail": {"kind": "summary", "rows": [{"项目": "未税单价", "值": "5.9"}]}}
        result = quote_step_completion_gate(6, data)
        self.assertFalse(result["ok"])
        self.assertEqual("packaging_draft_not_publishable", result["code"])

    def test_override_cannot_publish_draft(self):
        data = {"packaging_package": package(), "packaging_quote": quote(),
                "_e2e_override": True,
                "s5_detail": {"kind": "summary", "rows": [{"项目": "未税单价", "值": "5.9"}]}}
        self.assertFalse(quote_step_completion_gate(6, data)["ok"])

    def test_positive_partial_total_does_not_make_zero_material_formal(self):
        pkg = package(gaps=False)
        pkg["cost"]["material_total"] = 0
        result = quote_step_completion_gate(
            6, {"packaging_package": pkg, "packaging_quote": quote(draft=False)})
        self.assertEqual("packaging_cost_evidence_missing", result["code"])

    def test_price_engine_discloses_zero_material_as_draft(self):
        pkg = package(gaps=False)
        pkg["cost"]["material_total"] = 0
        result = cpq_packaging_quote.price(pkg, gross_margin_rate=0.25)
        self.assertTrue(result["draft"])
        self.assertTrue(result["publish_blocked"])

    def test_non_packaging_gate_unchanged(self):
        self.assertEqual("no_product_rows", quote_step_completion_gate(3, {})["code"])


class PackagingPartBindingTest(unittest.TestCase):
    def test_unconfirmed_part_is_not_position_bound_or_computed(self):
        items = [{"bom_category": "box_part", "item_key": "P01", "status": "needs_input",
                  "length_mm": None, "width_mm": None,
                  "missing_variables": ["length_mm", "width_mm"]}]
        parts = {"parts_id": "g1", "parts_hash": "h1", "parts": [
            {"part_code": "DWG-P01", "component_id": "c1", "unfolded_length_mm": 307.07,
             "unfolded_width_mm": 528.89, "size_source": "closed_outline", "role": "unknown"}]}
        before = copy.deepcopy(items)
        result = packaging_parts.bind_rows(
            items, parts, business_parts={"business_parts_id": "bp1",
                                    "business_parts": [{"business_part_code": "P01",
                                                        "geometry_binding": {"status": "ambiguous"}}]})
        row = result["items"][0]
        self.assertEqual(before, items, "绑定函数不能修改输入")
        self.assertEqual("needs_input", row["status"])
        self.assertIsNone(row["length_mm"])
        self.assertIsNone(row["width_mm"])
        self.assertNotEqual("auto_position_area", row.get("binding_method"))

    def test_material_processing_sentence_is_not_a_part_name(self):
        phrase = "透明0.5MM厚30%RPET胶片单面防刮花/单面覆膜"
        self.assertEqual("material_spec_not_part",
                         resolver._exclusion_reason(phrase, phrase))

    def test_drawing_derived_size_stays_pending_until_human_confirms_it(self):
        doc = {"derived_from_drawing": True, "business_parts": [{
            "business_part_code": "DWG-BP01", "name": "左盖面纸",
            "reference": {"length_mm": 307.07, "width_mm": 528.89},
            "confirmed_size": {"length_mm": 307.07, "width_mm": 528.89},
            "geometry_binding": {"status": "ambiguous", "bound_by": "deterministic"}}]}
        row = packaging_bom.business_part_rows(doc)[0]
        self.assertEqual("needs_input", row["status"])
        self.assertIsNone(row["length_mm"])

    def test_confirmed_size_enters_bom_with_human_audit(self):
        doc = {"derived_from_drawing": True, "business_parts": [{
            "business_part_code": "DWG-BP01", "name": "左盖面纸",
            "geometry_binding": {"status": "bound", "bound_by": "manual",
                                 "component_ids": ["c1"]}}]}
        confirmed = packaging_parts.confirm_business_part_size(
            doc, "DWG-BP01", 307.07, 528.89, actor="PE1",
            note="按图纸标注人工核对")
        row = packaging_bom.business_part_rows(confirmed)[0]
        self.assertEqual("computed", row["status"])
        self.assertEqual((307.07, 528.89), (row["length_mm"], row["width_mm"]))
        self.assertEqual("manual_confirmed_drawing", json.loads(row["size_source_json"])["kind"])

    def test_size_confirmation_rejects_unbound_or_unexplained_numbers(self):
        doc = {"business_parts": [{"business_part_code": "DWG-BP01",
                                   "geometry_binding": {"status": "ambiguous",
                                                        "bound_by": "deterministic"}}]}
        with self.assertRaisesRegex(ValueError, "confirmed_geometry_binding_required"):
            packaging_parts.confirm_business_part_size(
                doc, "DWG-BP01", 307.07, 528.89, actor="PE1", note="图纸标注")
        doc["business_parts"][0]["geometry_binding"] = {
            "status": "bound", "bound_by": "manual", "component_ids": ["c1"]}
        with self.assertRaisesRegex(ValueError, "actor_and_evidence_note"):
            packaging_parts.confirm_business_part_size(
                doc, "DWG-BP01", 307.07, 528.89, actor="PE1", note="")

    def test_size_confirmation_has_real_api_and_board_entry(self):
        backend = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
        frontend = (ROOT / "tech_app/frontend/app.js").read_text(encoding="utf-8")
        self.assertIn("@app.put(PACKAGING_SIZE_CONFIRM_PATH)", backend)
        self.assertIn("packagingBusinessPartSizeConfirmUrl", frontend)
        self.assertIn("packagingConfirmSize", frontend)

    def test_single_part_downstream_refuses_unconfirmed_drawing_size(self):
        row = {"business_part_code": "DWG-BP01", "name": "左盖面纸",
               "material": "纸", "reference": {"length_mm": 307.07, "width_mm": 528.89},
               "geometry_binding": {"status": "bound", "bound_by": "manual",
                                    "component_ids": ["c1"], "size_confirmed": False}}
        with self.assertRaisesRegex(ValueError, "drawing_part_size_not_confirmed"):
            packaging_parts.verified_business_part_input_row(
                row, {"derived_from_drawing": True})
        row["geometry_binding"]["size_confirmed"] = True
        row["confirmed_size"] = {"length_mm": 300, "width_mm": 500,
                                 "confirmed_by": "PE1"}
        verified = packaging_parts.verified_business_part_input_row(
            row, {"derived_from_drawing": True})
        self.assertEqual(300, verified["reference"]["length_mm"])
        self.assertEqual(500, verified["reference"]["width_mm"])
        self.assertEqual(307.07, row["reference"]["length_mm"])

    def test_frontend_does_not_offer_single_part_shortcut_before_confirmation(self):
        source = (ROOT / "tech_app/frontend/app.js").read_text(encoding="utf-8")
        self.assertIn("packagingBusinessPartSizeCostTarget(row, currentPackagingBusinessParts)", source)
        self.assertIn("packagingBusinessPartProcessTarget(row, currentPackagingBusinessParts)", source)
        self.assertIn("target.ok && drawingSizeReady", source)
        script = """
const fs = require('fs'), vm = require('vm');
const s = fs.readFileSync('tech_app/frontend/app.js','utf8');
for (const name of ['packagingBusinessPartSizeCostTarget','packagingBusinessPartProcessTarget']) {
  const begin=s.indexOf('function '+name+'('), end=s.indexOf('\\n}',begin)+2;
  vm.runInThisContext(s.slice(begin,end));
}
const row={business_part_code:'DWG-BP01',material:'纸',reference:{length_mm:307.07,width_mm:528.89},
  geometry_binding:{status:'bound',bound_by:'manual',component_ids:['c1'],size_confirmed:false}};
const doc={derived_from_drawing:true};
if (packagingBusinessPartSizeCostTarget(row,doc).ok || packagingBusinessPartProcessTarget(row,doc).ok) process.exit(2);
row.geometry_binding.size_confirmed=true;
row.confirmed_size={length_mm:300,width_mm:500};
if (!packagingBusinessPartSizeCostTarget(row,doc).ok || !packagingBusinessPartProcessTarget(row,doc).ok) process.exit(3);
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)


class ServerSideGuardTest(unittest.TestCase):
    def test_direct_step_done_has_server_gate(self):
        source = (ROOT / "cpq_wf.py").read_text(encoding="utf-8")
        body = source.split("def complete_step(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("quote_step_completion_gate", body)

    def test_server_export_and_import_reject_draft(self):
        source = (ROOT / "cpq_agent_server.py").read_text(encoding="utf-8")
        self.assertIn("_assert_formal_packaging_quote", source)
        self.assertIn("_assert_formal_packaging_quote(data)", source)

    def test_packaging_margin_has_explicit_ui_entry_not_hidden_default(self):
        source = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")
        self.assertIn("packagingMarginInput", source)
        self.assertIn("packagingRepriceBtn", source)
        self.assertIn("系统不会默认填入费率", source)

    def test_formal_export_requires_session_and_rejects_draft_from_stored_card(self):
        with self.assertRaises(ValueError):
            _assert_formal_packaging_quote({"industry": "packaging"})
        def snapshot(_sid, step):
            return ({"packaging_package": package()} if step == 2 else
                    {"packaging_quote": quote()})
        with patch.object(cpq_wf, "step_snapshot", side_effect=snapshot):
            with self.assertRaisesRegex(ValueError, "草稿"):
                _assert_formal_packaging_quote({"session_id": "q1", "industry": "packaging"})

    def test_formal_export_accepts_gap_free_stored_packaging_quote(self):
        def snapshot(_sid, step):
            return ({"packaging_package": package(gaps=False)} if step == 2 else
                    {"packaging_quote": quote(draft=False)})
        with patch.object(cpq_wf, "step_snapshot", side_effect=snapshot):
            self.assertIsNone(_assert_formal_packaging_quote(
                {"session_id": "q1", "industry": "packaging"}))


class FinanceTaskDelegationTest(unittest.TestCase):
    class Cursor:
        def __init__(self, row):
            self.row = row
        def fetchone(self):
            return self.row

    def _run(self, *, kind, delegate, expected_card_id=11):
        queries = []
        def fake_exec(_conn, sql, args=()):
            queries.append(sql)
            if "SELECT card_id" in sql:
                return self.Cursor((11, "claimed", 7, kind))
            return self.Cursor(None)
        with patch.object(cpq_wf.cpq_auth, "_exec", side_effect=fake_exec), \
             patch.object(cpq_wf, "_log"):
            result = cpq_wf.close_source_task(
                object(), 123, {"user_id": 8, "role_code": "finance_mgr"},
                allow_packaging_delegate=delegate, expected_card_id=expected_card_id)
        return result, queries

    def test_finance_can_close_pe_held_packaging_source_task_only(self):
        result, queries = self._run(kind=cpq_wf.TASK_KIND_TECH_NEW, delegate=True)
        self.assertTrue(result["closed"])
        self.assertTrue(any("UPDATE cpq_wf_task" in sql for sql in queries))

    def test_finance_cannot_close_unrelated_held_task(self):
        with self.assertRaises(cpq_wf.WfError):
            self._run(kind=cpq_wf.TASK_KIND_HANDOFF, delegate=True)

    def test_finance_cannot_close_other_quote_cards_source_task(self):
        with self.assertRaises(cpq_wf.WfError):
            self._run(kind=cpq_wf.TASK_KIND_TECH_NEW, delegate=True,
                      expected_card_id=12)


class HomeProgressTest(unittest.TestCase):
    def test_requirement_draft_and_completed_downstream_are_separate_facts(self):
        from tech_app.backend.services.packaging_drawing_flow import persistence
        from tech_app.backend.storage import da_repo
        from tech_app.backend.services import packaging_bom, packaging_route, packaging_cost
        with patch.object(home_card.store, "load_requirement", return_value={
                    "status": "draft", "requirement_no": "REQ1", "data": {"industry": "packaging"}}), \
             patch.object(persistence, "load_flow", return_value={"steps": [
                    {"status": "completed"} for _ in range(8)]}), \
             patch.object(da_repo, "load_box_match", return_value={"decision": "confirmed"}), \
             patch.object(packaging_bom, "load_bom", return_value={"built": True}), \
             patch.object(packaging_route, "load_route", return_value={"status": "confirmed"}), \
             patch.object(packaging_cost, "load_cost", return_value={
                    "built": True, "gaps": [{"code": "material_price_missing"}]}):
            progress = home_card.packaging_progress("p1")
        self.assertEqual(5, len(progress["completed"]))
        self.assertEqual(1, progress["cost_gap_count"])


if __name__ == "__main__":
    unittest.main()
