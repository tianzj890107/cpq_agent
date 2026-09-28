"""红测：图框空间候选可复用，模型仍不能确权；不读样例答案或联网。"""
from __future__ import annotations

import copy
from unittest import TestCase, mock
from pathlib import Path

from tech_app.backend.services import packaging_part_visual_review as review
from tech_app.backend.services import packaging_business_part_resolver as resolver
from tech_app.backend.services import packaging_parts


def line(eid, x0, y0, x1, y1):
    return {"entity_id": eid, "kind": "line", "type": "LINE", "layer": "CUTTER",
            "bbox": [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
            "attributes": {"start": [x0, y0], "end": [x1, y1]}}


def part(cid, ids, box):
    return {"component_id": cid, "part_code": cid, "entity_ids": ids,
            "outline": {"bbox": box}, "layers": ["CUTTER"],
            "outline_status": "open", "area_mm2": (box[2]-box[0])*(box[3]-box[1])}


def fixture(dx=0, reverse=False):
    def shift(box):
        return [box[0]+dx, box[1], box[2]+dx, box[3]]
    entities = [
        line("a", dx, 0, dx+50, 0),
        line("a2", dx, 0, dx, 70),
        line("b", dx+52.5, 0, dx+100, 0),
        line("b2", dx+100, 0, dx+100, 70),
        line("c", dx+112, 0, dx+145, 0),
        line("c2", dx+145, 0, dx+145, 70),
        {"entity_id": "frame:left", "kind": "polyline", "type": "LWPOLYLINE",
         "layer": "FRAME", "closed": True, "bbox": shift([-10, -10, 111, 90]),
         "attributes": {"points": [[dx-10,-10],[dx+111,-10],[dx+111,90],[dx-10,90]]}},
        {"entity_id": "frame:right", "kind": "polyline", "type": "LWPOLYLINE",
         "layer": "FRAME", "closed": True, "bbox": shift([111.5, -10, 240, 90]),
         "attributes": {"points": [[dx+111.5,-10],[dx+240,-10],[dx+240,90],[dx+111.5,90]]}},
    ]
    parts = [part("cmp:a", ["a", "a2"], shift([0, 0, 50, 70])),
             part("cmp:b", ["b", "b2"], shift([52.5, 0, 100, 70])),
             part("cmp:c", ["c", "c2"], shift([112, 0, 145, 70]))]
    ir = {"entities": entities[::-1] if reverse else entities,
          "texts": [{"entity_id": "name", "raw_text": "名称：部件甲",
                     "position": [dx+48, 75], "layer": "0"}],
          "dimensions": [], "layers": [{"name": "CUTTER", "role": "cut"}]}
    return ir, {"parts": parts[::-1] if reverse else parts}


class PackagingLayoutSpatialModelCombinationRed(TestCase):
    def test_unassigned_candidates_have_a_separate_read_only_panel(self):
        source = (Path(__file__).resolve().parents[1] / "tech_app/frontend/app.js").read_text(
            encoding="utf-8")
        self.assertIn('data-qq-unassigned-candidates', source)
        self.assertIn('function openPackagingUnassignedCandidate(', source)
        start = source.index('function openPackagingUnassignedCandidate(')
        end = source.index('\nfunction ', start + 1)
        body = source[start:end]
        self.assertIn('packagingPartSceneSvg(exact', body)
        self.assertIn('packagingConfirmUnassignedCandidate', body)
        self.assertIn('candidate_id: String(candidate.candidate_id', body)
        self.assertNotIn('packagingBusinessPartProcess', body)
        self.assertNotIn('packagingBusinessPartCost', body)
        self.assertIn('binding.candidate_id ? Object.assign({}, binding, {bbox: null, component_ids: []})',
                      source)

    def test_raw_candidate_association_does_not_unlock_geometry_downstream(self):
        from tests.test_packaging_business_part_plan_click_and_bound_outline_red import value_of
        row = {"business_part_code": "BP-01", "geometry_binding": {
            "status": "partial", "candidate_id": "layout:real", "component_ids": ["cmp:a"],
            "entity_ids": ["a", "unkept-arc"], "size_confirmed": False}}
        geometry = {"parts": [{"part_code": "P-01", "component_id": "cmp:a",
                               "outline_status": "closed"}]}
        decision = value_of("packagingBusinessPartDownstreamTarget", row, geometry)
        self.assertFalse(decision["ok"])
        self.assertEqual("raw_candidate_size_unconfirmed", decision["code"])

    def test_close_non_touching_lines_form_ambiguous_group_but_not_cross_frame(self):
        ir, geometry = fixture()
        candidates = review.build_part_view_candidates(ir, geometry)
        groups = [row for row in candidates if set(row.get("component_ids") or []) == {"cmp:a", "cmp:b"}]
        self.assertTrue(groups, "端点不接时仍要有空间聚类候选")
        self.assertEqual("ambiguous", groups[0]["geometry_status"])
        self.assertIn("spatial", " ".join(groups[0]["evidence_reasons"]))
        self.assertTrue(groups[0].get("frame_id"))
        self.assertFalse(any({"cmp:b", "cmp:c"} <= set(row.get("component_ids") or [])
                             for row in candidates), "不同图框不能因为相距 2 mm 被并件")

    def test_translation_and_order_do_not_change_group_members(self):
        base = {frozenset(row.get("component_ids") or [])
                for row in review.build_part_view_candidates(*fixture())}
        moved = {frozenset(row.get("component_ids") or [])
                 for row in review.build_part_view_candidates(*fixture(dx=913.5, reverse=True))}
        self.assertEqual(base, moved)

    def test_candidate_carries_nearby_drawing_text_for_model_semantics(self):
        ir, geometry = fixture()
        ir["texts"].extend([
            {"entity_id": "material", "raw_text": "材料：2mm灰板",
             "position": [42, 80], "layer": "0"},
            {"entity_id": "legend", "raw_text": "Generic Rule Legend Cut",
             "position": [132, 78], "layer": "0"},
        ])
        group = next(row for row in review.build_part_view_candidates(ir, geometry)
                     if set(row.get("component_ids") or []) == {"cmp:a", "cmp:b"})
        ids = {row.get("entity_id") for row in group.get("nearby_texts") or []}
        self.assertIn("name", ids)
        self.assertIn("material", ids)
        self.assertNotIn("legend", ids, "异框图例不得作为本件的附近文字")

    def test_raw_cad_geometry_missing_from_kept_components_is_still_a_candidate(self):
        ir, geometry = fixture()
        ir["entities"].append({
            "entity_id": "unkept-arc", "kind": "arc", "type": "ARC", "layer": "CUTTER",
            "bbox": [20, 1, 40, 20],
            "attributes": {"center": [30, 10], "radius": 10,
                           "start_angle": 0, "end_angle": 180}})
        candidates = review.build_part_view_candidates(ir, geometry)
        raw = [row for row in candidates
               if "raw_cad_entities" in (row.get("evidence_reasons") or [])
               and "unkept-arc" in (row.get("entity_ids") or [])]
        self.assertTrue(raw, "候选不能只看过滤后几何件，否则原始弧/样条永远丢失")
        self.assertEqual("ambiguous", raw[0]["geometry_status"])
        self.assertFalse(any("c" in (row.get("entity_ids") or []) for row in raw),
                         "原始图元聚类也不能跨图框吞并邻件")
        outcome = resolver.resolve_business_parts(
            "raw-candidate-review", ir, geometry, use_model=True,
            model_reviewer=lambda _request: {"selected_candidate_id": "none",
                                             "insufficient_evidence": True, "reason": "audit"})
        document = packaging_parts.business_parts_document(
            outcome["reference"], geometry, bindings=outcome["match"])
        code = document["business_parts"][0]["business_part_code"]
        confirmed = packaging_parts.set_geometry_binding(
            document, code, ["cmp:a", "cmp:b"], bound_by="manual")
        self.assertTrue(any("unkept-arc" in (row.get("entity_ids") or [])
                            for row in confirmed.get("unassigned_candidates") or []),
                        "只确认已保留分量，不能假装遗漏的原始弧也已经绑定")
        raw_candidate = next(row for row in document["unassigned_candidates"]
                             if "unkept-arc" in (row.get("entity_ids") or []))
        other_row = copy.deepcopy(document["business_parts"][0])
        other_row["business_part_code"] = "OTHER-PART"
        other_row["geometry_binding"]["candidates"] = [dict(raw_candidate, id=raw_candidate["candidate_id"])]
        document["business_parts"].append(other_row)
        selected = packaging_parts.set_geometry_binding(
            document, code, [], bound_by="manual",
            candidate_id=raw_candidate["candidate_id"], reason="图纸人工核对")
        binding = selected["business_parts"][0]["geometry_binding"]
        self.assertEqual("partial", binding["status"])
        self.assertIn("unkept-arc", binding["entity_ids"])
        self.assertFalse(binding.get("size_confirmed"))
        self.assertEqual(raw_candidate["candidate_id"], binding["candidate_id"])
        self.assertFalse(any(row.get("candidate_id") == raw_candidate["candidate_id"]
                             for row in selected["unassigned_candidates"]))
        other_code = "OTHER-PART"
        with self.assertRaisesRegex(ValueError, "already_assigned"):
            packaging_parts.set_geometry_binding(selected, other_code, [], bound_by="manual",
                                                 candidate_id=raw_candidate["candidate_id"])
        with self.assertRaises(ValueError):
            packaging_parts.set_geometry_binding(document, code, [], bound_by="manual",
                                                 candidate_id="layout:invented")
        with self.assertRaises(ValueError):
            packaging_parts.set_geometry_binding(document, "invented-part", [], bound_by="manual",
                                                 candidate_id=raw_candidate["candidate_id"])

    def test_missing_layout_frame_keeps_unfiltered_raw_geometry_as_single_weak_candidate(self):
        ir, geometry = fixture()
        ir["entities"] = [entity for entity in ir["entities"]
                          if not str(entity.get("entity_id") or "").startswith("frame:")]
        ir["entities"].append({"entity_id": "orphan-arc", "kind": "arc", "type": "ARC",
                               "layer": "CUTTER", "bbox": [20, 10, 30, 25]})
        rows = review.build_part_view_candidates(ir, geometry)
        orphan = next(row for row in rows if row.get("entity_ids") == ["orphan-arc"])
        self.assertEqual("ambiguous", orphan["geometry_status"])
        self.assertEqual([], orphan["component_ids"])
        self.assertIn("layout_frame_unavailable", orphan["evidence_reasons"])

    def test_human_candidate_with_only_part_of_known_component_stays_partial(self):
        candidate = {"candidate_id": "layout:subset", "component_ids": ["cmp:a"],
                     "entity_ids": ["a"], "bbox": [0, 0, 50, 70],
                     "evidence_reasons": ["raw_cad_entities"]}
        document = {"business_parts": [{"business_part_code": "BP-01", "geometry_binding": {}}],
                    "geometry_evidence": {"components": [{"component_id": "cmp:a",
                                                          "entity_ids": ["a", "a2"],
                                                          "outline": {"bbox": [0, 0, 50, 70]}}]},
                    "unassigned_candidates": [candidate]}
        updated = packaging_parts.set_geometry_binding(
            document, "BP-01", [], candidate_id="layout:subset")
        binding = updated["business_parts"][0]["geometry_binding"]
        self.assertEqual("partial", binding["status"])
        self.assertEqual("none", binding["size_source"])
        self.assertFalse(binding["size_confirmed"])

    def test_model_selected_spatial_group_stays_weak_and_can_be_manually_confirmed(self):
        ir, geometry = fixture()
        requests = []

        def reviewer(request):
            requests.append(request)
            group = next(row for row in request["candidates"]
                         if set(row.get("component_ids") or []) == {"cmp:a", "cmp:b"})
            return {"selected_candidate_id": group["id"], "insufficient_evidence": False,
                    "reason": "同一框内看起来是同一件", "text_class": "name"}

        outcome = resolver.resolve_business_parts(
            "independent-layout", ir, geometry, use_model=True, model_reviewer=reviewer)
        self.assertTrue(requests)
        binding = outcome["match"]["bindings"][0]
        self.assertNotEqual("bound", binding["status"])
        self.assertFalse(binding["size_confirmed"])
        self.assertEqual("model_suggested", binding["attribution"]["status"])
        self.assertTrue(any(set(item.get("component_ids") or []) == {"cmp:a", "cmp:b"}
                            for item in binding.get("candidates") or []))

    def test_model_classifies_material_without_promoting_it_to_part_name(self):
        ir, geometry = fixture()

        def reviewer(request):
            group = next(row for row in request["candidates"]
                         if set(row.get("component_ids") or []) == {"cmp:a", "cmp:b"})
            return {"selected_candidate_id": group["id"], "insufficient_evidence": False,
                    "text_class": "material", "suggested_material": "灰板",
                    "reason": "这条是材料描述"}

        outcome = resolver.resolve_business_parts(
            "independent-material", ir, geometry, use_model=True, model_reviewer=reviewer)
        binding = outcome["match"]["bindings"][0]
        self.assertNotEqual("bound", binding["status"])
        self.assertEqual("review_needed", binding["attribution"]["status"])
        self.assertEqual("material", binding["attribution"]["text_class"])
        self.assertEqual("灰板", binding["attribution"]["suggested_material"])

    def test_valid_candidate_id_does_not_launder_model_fabricated_size(self):
        ir, geometry = fixture()

        def reviewer(request):
            group = next(row for row in request["candidates"]
                         if set(row.get("component_ids") or []) == {"cmp:a", "cmp:b"})
            return {"selected_candidate_id": group["id"], "insufficient_evidence": False,
                    "text_class": "name", "length_mm": 999, "size_confirmed": True,
                    "reason": "模型编了尺寸"}

        outcome = resolver.resolve_business_parts(
            "independent-overreach", ir, geometry, use_model=True, model_reviewer=reviewer)
        binding = outcome["match"]["bindings"][0]
        self.assertEqual("review_needed", binding["attribution"]["status"])
        self.assertFalse(binding["size_confirmed"])
        self.assertNotEqual(999, binding["length_mm"])
        with self.assertRaises(ValueError):
            review._Decision.model_validate({"selected_candidate_id": "layout:real",
                                             "insufficient_evidence": False,
                                             "reason": "编造了尺寸", "length_mm": 999})

    def test_distant_fragments_do_not_recompute_endpoints_for_all_pairs(self):
        ir = {"entities": [], "texts": [], "dimensions": [], "layers": [{"name": "CUTTER", "role": "cut"}]}
        rows = []
        for index in range(90):
            x = index * 1000
            eid = "e%d" % index
            ir["entities"].extend([line(eid, x, 0, x+40, 0),
                                   line(eid+"b", x, 0, x, 70)])
            rows.append(part("cmp:%d" % index, [eid, eid+"b"], [x, 0, x+40, 70]))
        with mock.patch.object(review, "_dangling_points", wraps=review._dangling_points) as spy:
            review.build_part_view_candidates(ir, {"parts": rows})
        self.assertLess(spy.call_count, 500, "不能对每一对远隔分量反复统计端点")

    def test_unassigned_groups_survive_document_without_becoming_business_parts(self):
        ir, geometry = fixture()
        outcome = resolver.resolve_business_parts(
            "independent-unassigned", ir, geometry, use_model=True,
            model_reviewer=lambda _request: {"selected_candidate_id": "none",
                                             "insufficient_evidence": True, "reason": "待核对"})
        document = packaging_parts.business_parts_document(
            outcome["reference"], geometry, bindings=outcome["match"])
        self.assertTrue(any(set(row.get("component_ids") or []) == {"cmp:a", "cmp:b"}
                            for row in document.get("unassigned_candidates") or []))
        self.assertEqual(len(outcome["reference"]["parts"]), len(document["business_parts"]))
        self.assertTrue(all(row.get("status") == "needs_confirmation"
                            for row in document["unassigned_candidates"]))
        from tech_app.backend import main
        body = main._business_parts_body("independent-unassigned", document)
        self.assertEqual(document["unassigned_candidates"], body["unassigned_candidates"])
        code = document["business_parts"][0]["business_part_code"]
        confirmed = packaging_parts.set_geometry_binding(
            document, code, ["cmp:a", "cmp:b"], bound_by="manual", reason="checked CAD")
        self.assertFalse(any(set(row.get("component_ids") or []) == {"cmp:a", "cmp:b"}
                             for row in confirmed.get("unassigned_candidates") or []),
                         "人工确认后，这组图形不能仍显示为未归属")
