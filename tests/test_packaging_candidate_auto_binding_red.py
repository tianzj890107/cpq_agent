"""默认最高候选自动归属；仅 CAD 完整证据可确权，尺寸仍人工核对。"""
from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import unittest
from unittest.mock import patch

from tech_app.backend.services import packaging_parts, packaging_bom

ROOT = pathlib.Path(__file__).resolve().parents[1]


def document(*, conflict=False, partial=False):
    components = [
        {"component_id": "c1", "entity_ids": ["e1"], "bbox": [0, 0, 100, 80], "status": "closed"},
        {"component_id": "c2", "entity_ids": ["e2"], "bbox": [200, 0, 300, 80], "status": "closed"},
    ]
    candidates = [
        {"id": "low", "component_ids": ["c1"], "entity_ids": ["e1"],
         "bbox": [0, 0, 100, 80], "distance_mm": 80, "anchor_in_region": False,
         "dimension_spatial": False, "geometry_status": "supported"},
        {"id": "high", "component_ids": ["c2"],
         "entity_ids": ["e2", "extra"] if partial else ["e2"],
         "bbox": [200, 0, 300, 80], "distance_mm": 1, "anchor_in_region": True,
         "dimension_spatial": True, "geometry_status": "supported"},
    ]
    rows = [{"business_part_code": "BP1", "name": "左盖面纸",
             "reference": {"length_mm": 307.07, "width_mm": 528.89},
             "geometry_binding": {"status": "ambiguous", "candidates": candidates}}]
    if conflict:
        rows.append({"business_part_code": "BP2", "name": "其他件",
                     "geometry_binding": {"status": "bound", "bound_by": "manual",
                                          "component_ids": ["c2"], "entity_ids": ["e2"]}})
    return {"derived_from_drawing": True, "business_parts": rows,
            "geometry_evidence": {"components": components}}


class AutoBindingTest(unittest.TestCase):
    def test_highest_complete_candidate_is_persisted_as_auto_not_human(self):
        before = document()
        result = packaging_parts.auto_bind_business_candidates(before)
        row = result["business_parts"][0]
        binding = row["geometry_binding"]
        self.assertEqual("bound", binding["status"])
        self.assertEqual("auto", binding["bound_by"])
        self.assertEqual("high", binding["candidate_id"])
        self.assertEqual("auto_selected", binding["attribution"]["status"])
        self.assertFalse(binding["size_confirmed"])
        self.assertEqual(2, len(binding["candidates"]))
        self.assertEqual("ambiguous", before["business_parts"][0]["geometry_binding"]["status"])
        self.assertEqual(result, packaging_parts.auto_bind_business_candidates(result))
        bom = packaging_bom.business_part_rows(result)[0]
        self.assertEqual("needs_input", bom["status"])
        self.assertIsNone(bom["length_mm"])

    def test_top_conflict_or_partial_geometry_does_not_fall_through(self):
        for source in (document(conflict=True), document(partial=True)):
            with self.subTest(source=source):
                result = packaging_parts.auto_bind_business_candidates(source)
                self.assertEqual("ambiguous", result["business_parts"][0]["geometry_binding"]["status"])
                self.assertNotEqual("low", result["business_parts"][0]["geometry_binding"].get("candidate_id"))

    def test_manual_binding_and_workbook_doc_are_not_overwritten(self):
        source = document()
        source["business_parts"][0]["geometry_binding"] = {
            "status": "bound", "bound_by": "manual", "component_ids": ["c1"],
            "entity_ids": ["e1"], "size_confirmed": True}
        source["business_parts"][0]["confirmed_size"] = {"length_mm": 100, "width_mm": 80}
        self.assertEqual(source, packaging_parts.auto_bind_business_candidates(source))
        workbook = copy.deepcopy(document())
        workbook["derived_from_drawing"] = False
        self.assertEqual(workbook, packaging_parts.auto_bind_business_candidates(workbook))

    def test_global_conflict_is_awarded_to_stronger_candidate_not_first_row(self):
        source = document()
        first = source["business_parts"][0]
        first["geometry_binding"]["candidates"] = [{
            "id": "shared-low", "component_ids": ["c2"], "entity_ids": ["e2"],
            "bbox": [200, 0, 300, 80], "distance_mm": 60,
            "anchor_in_region": False, "dimension_spatial": False,
            "geometry_status": "supported"}]
        source["business_parts"].append({"business_part_code": "BP2", "name": "右盖面纸",
            "geometry_binding": {"status": "ambiguous", "candidates": [{
                "id": "shared-high", "component_ids": ["c2"], "entity_ids": ["e2"],
                "bbox": [200, 0, 300, 80], "distance_mm": 1,
                "anchor_in_region": True, "dimension_spatial": True,
                "geometry_status": "supported"}]}})
        result = packaging_parts.auto_bind_business_candidates(source)
        self.assertEqual("ambiguous", result["business_parts"][0]["geometry_binding"]["status"])
        self.assertEqual("auto", result["business_parts"][1]["geometry_binding"]["bound_by"])

    def test_auto_owner_can_later_confirm_size_without_human_rebinding(self):
        result = packaging_parts.auto_bind_business_candidates(document())
        updated = packaging_parts.confirm_business_part_size(
            result, "BP1", 307.07, 528.89, actor="PE1", note="按图纸标注核对")
        self.assertEqual("computed", packaging_bom.business_part_rows(updated)[0]["status"])

    def test_user_reselect_clears_old_size_but_keeps_its_audit_history(self):
        bound = packaging_parts.auto_bind_business_candidates(document())
        sized = packaging_parts.confirm_business_part_size(
            bound, "BP1", 307.07, 528.89, actor="PE1", note="按标注核对")
        changed = packaging_parts.set_geometry_binding(
            sized, "BP1", ["c1"], bound_by="manual", reason="用户改选 low", candidate_id="low")
        row = changed["business_parts"][0]
        self.assertNotIn("confirmed_size", row)
        self.assertEqual(307.07, row["confirmed_size_history"][-1]["length_mm"])
        self.assertEqual("needs_input", packaging_bom.business_part_rows(changed)[0]["status"])

    def test_new_document_auto_binds_before_first_save(self):
        source = document()
        reference = {"derived_from_drawing": True, "parts": [{
            "business_part_code": "BP1", "name": "左盖面纸"}]}
        geometry = {"parts_id": "g1", "parts": [{
            "part_code": "P1", "component_id": "c1", "entity_ids": ["e1"],
            "bbox": [0, 0, 100, 80]}, {
            "part_code": "P2", "component_id": "c2", "entity_ids": ["e2"],
            "bbox": [200, 0, 300, 80]}]}
        match = {"bindings": [dict(source["business_parts"][0]["geometry_binding"],
                                   business_part_code="BP1")]}
        built = packaging_parts.business_parts_document(reference, geometry, bindings=match)
        self.assertEqual("auto", built["business_parts"][0]["geometry_binding"]["bound_by"])
        repeated = packaging_parts.business_parts_document(reference, geometry, bindings=match)
        self.assertEqual(built["business_parts_id"], repeated["business_parts_id"])

    def test_python_and_browser_use_same_confidence(self):
        candidate = document()["business_parts"][0]["geometry_binding"]["candidates"][1]
        source = (ROOT / "tech_app/frontend/app.js").read_text()
        start = source.index("function packagingCandidateConfidence(")
        end = source.index("\n}", start) + 2
        script = source[start:end] + "\nprocess.stdout.write(String(packagingCandidateConfidence("
        script += json.dumps(candidate) + ")));"
        result = subprocess.run(["node", "-e", script], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertEqual(packaging_parts.candidate_confidence_percent(candidate), int(result.stdout))

    def test_existing_project_bulk_action_writes_once_and_second_call_is_read_only(self):
        from tech_app.backend import main
        state = {"doc": document()}
        def save(_pid, payload):
            state["doc"] = payload
            return payload
        with patch.object(main, "_require"), patch.object(main, "_workflow_project"), \
             patch.object(main.packaging_parts, "load_business_parts",
                          side_effect=lambda _pid: state["doc"]), \
             patch.object(main.packaging_parts, "save_business_parts", side_effect=save) as saved, \
             patch.object(main.store, "audit") as audited, \
             patch.object(main, "_business_parts_body", side_effect=lambda _pid, doc: doc):
            first = main.auto_bind_packaging_business_candidates("p1", user={"username": "PE1"})
            second = main.auto_bind_packaging_business_candidates("p1", user={"username": "PE1"})
        self.assertEqual("auto", first["business_parts"][0]["geometry_binding"]["bound_by"])
        self.assertEqual(first, second)
        self.assertEqual(1, saved.call_count)
        self.assertEqual(1, audited.call_count)

    def test_new_parse_and_existing_project_have_auto_entry_and_no_confirm_button(self):
        steps = (ROOT / "tech_app/backend/services/packaging_drawing_flow/steps.py").read_text()
        main = (ROOT / "tech_app/backend/main.py").read_text()
        frontend = (ROOT / "tech_app/frontend/app.js").read_text()
        self.assertIn("business_parts_document", steps)
        self.assertIn("auto_bind_business_candidates(doc)",
                      (ROOT / "tech_app/backend/services/packaging_parts.py").read_text())
        self.assertIn("auto_bind_business_candidates", main)
        self.assertIn("packagingAutoBindCandidatesUrl", frontend)
        self.assertNotIn('id="packagingConfirmCandidate"', frontend)
        self.assertIn('selector.addEventListener("change", async', frontend)
        self.assertIn("fetch(packagingGeometryBindingUrl(wanted)", frontend)


if __name__ == "__main__":
    unittest.main()
