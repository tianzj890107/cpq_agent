import copy
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from tech_app.backend import main
from tech_app.backend.services import (packaging_demo_flow, packaging_demo_inputs,
                                           packaging_name_assist, packaging_parts, tasks)
from tech_app.backend.storage import kb_repo, store


PROJECT = "fixture-process-preflight"
ACTOR = {"username": "fixture-manager", "role": "process_manager"}
CONFLICT = "demo_box_match_missing_or_conflicting_inputs"


def requirement(status="approved"):
    return {
        "project_id": PROJECT,
        "requirement_no": "FIXTURE-WINE",
        "status": status,
        "data": {"industry": "packaging", "packaging_category": "酒盒"},
    }


def business_part():
    return {
        "business_part_code": "FIXTURE-P01",
        "name": "面纸",
        "reference": {"material_text": "225G面纸"},
        "geometry_binding": {
            "status": "bound",
            "bound_by": "auto",
            "component_ids": ["component-1"],
            "entity_ids": ["entity-1"],
            "bbox": [10, 20, 310, 170],
        },
    }


class ProcessSubmission(unittest.IsolatedAsyncioTestCase):
    async def test_ready_part_submits_without_rewriting_approved_requirement(self):
        row = business_part()
        original = requirement()
        before = copy.deepcopy(original)
        loaded = {"row": row, "doc": {"derived_from_drawing": True}}
        with (
            patch.object(main, "_workflow_project", return_value={"project_id": PROJECT}),
            patch.object(main, "_packaging_business_part_row", return_value=loaded),
            patch.object(store, "load_requirement", return_value=original),
            patch.object(store, "save_requirement") as save,
            patch.object(packaging_demo_flow, "ensure_inputs", side_effect=ValueError(CONFLICT)),
            patch.object(tasks, "submit", return_value="fixture-task") as submit,
        ):
            result = await main.packaging_business_part_process(
                PROJECT, row["business_part_code"], note="", attachments=[], user=ACTOR,
            )
        self.assertEqual({"task_id": "fixture-task"}, result)
        self.assertEqual((PROJECT, "packaging_business_part_process"), submit.call_args.args[:2])
        self.assertEqual(ACTOR["username"], submit.call_args.kwargs["actor"])
        save.assert_not_called()
        self.assertEqual(before, original)

    async def test_approved_requirement_runs_demo_prepare_without_rewriting(self):
        """已审核需求缺证据时走演示兜底：AI 推荐 + 演示输入，但不改写需求单。"""
        row = business_part()
        row["geometry_binding"]["entity_ids"] = []
        failing = {"row": row, "doc": {"derived_from_drawing": True}}
        prepared = business_part()
        prepared["poc_inputs"] = {"mock": True, "length_mm": 100.0, "width_mm": 100.0,
                                  "material_text": "2.0mm灰板", "gsm": 1400.0}
        prepared["reference"].update({"length_mm": 100.0, "width_mm": 100.0,
                                      "size_quality": "poc_reference",
                                      "material_text": "2.0mm灰板"})
        ok = {"row": prepared, "doc": {"derived_from_drawing": True, "poc_demo": True}}
        with (
            patch.object(main, "_workflow_project", return_value={"project_id": PROJECT}),
            patch.object(main, "_packaging_business_part_row", side_effect=[failing, ok]),
            patch.object(store, "load_requirement", return_value=requirement()),
            patch.object(store, "save_requirement") as save,
            patch.object(packaging_name_assist, "prepare") as assist,
            patch.object(packaging_demo_inputs, "prepare") as demo,
            patch.object(tasks, "submit", return_value="fixture-task") as submit,
        ):
            result = await main.packaging_business_part_process(
                PROJECT, "FIXTURE-P01", note="", attachments=[], user=ACTOR,
            )
        self.assertEqual({"task_id": "fixture-task"}, result)
        assist.assert_called_once()
        demo.assert_called_once_with(PROJECT, actor=ACTOR["username"], enable_demo=False)
        save.assert_not_called()
        submit.assert_called_once()

    async def test_missing_evidence_reports_conflict_without_enqueueing(self):
        row = business_part()
        row["geometry_binding"]["entity_ids"] = []
        loaded = {"row": row, "doc": {"derived_from_drawing": True}}
        with (
            patch.object(main, "_workflow_project", return_value={"project_id": PROJECT}),
            patch.object(main, "_packaging_business_part_row", return_value=loaded),
            patch.object(store, "load_requirement", return_value=requirement("draft")),
            patch.object(packaging_demo_flow, "ensure_inputs", side_effect=ValueError(CONFLICT)),
            patch.object(tasks, "submit") as submit,
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.packaging_business_part_process(
                    PROJECT, row["business_part_code"], note="", attachments=[], user=ACTOR,
                )
        self.assertEqual(409, raised.exception.status_code)
        self.assertEqual(CONFLICT, raised.exception.detail["code"])
        submit.assert_not_called()


class DemoPreparation(unittest.TestCase):
    def test_rejected_box_does_not_supply_dimensions_or_code(self):
        round_box = {
            "box_type_code": "FIXTURE-ROUND",
            "name": "圆盘盒",
            "size_l_min": 400,
            "size_l_max": 402,
        }
        rejected = {
            "box_type_code": round_box["box_type_code"],
            "status": "rejected",
            "out_of_range": False,
            "can_confirm": False,
            "total_score": 90,
        }
        with (
            patch.object(kb_repo, "packaging_box_types", return_value=[round_box]),
            patch.object(packaging_demo_flow.packaging_match, "match_box_types",
                         return_value={"candidates": [rejected]}),
        ):
            with self.assertRaisesRegex(ValueError, CONFLICT):
                packaging_demo_flow.requirement_defaults(requirement("draft"))

    def test_no_confirmable_box_leaves_requirement_and_parts_unchanged(self):
        original = requirement("draft")
        proposed = copy.deepcopy(original)
        proposed["data"].update(box_type="FIXTURE-ROUND", inner_length=401)
        before = copy.deepcopy(original)
        matching = packaging_demo_flow.packaging_match
        with (
            patch.object(store, "load_requirement", return_value=original),
            patch.object(store, "save_requirement") as save,
            patch.object(packaging_parts, "load_business_parts", return_value={"derived_from_drawing": True}),
            patch.object(packaging_parts, "save_business_parts") as save_parts,
            patch.object(packaging_demo_flow, "requirement_defaults", return_value=proposed),
            patch.object(matching, "load_box_match", return_value={"decision": "none"}),
            patch.object(matching, "run_box_match", return_value={"candidates": []}),
            patch.object(matching, "match_box_types", return_value={"candidates": []}),
        ):
            with self.assertRaisesRegex(ValueError, CONFLICT):
                packaging_demo_flow.ensure_inputs(PROJECT, ACTOR)
        save.assert_not_called()
        save_parts.assert_not_called()
        self.assertEqual(before, original)
