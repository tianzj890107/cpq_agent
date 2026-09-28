"""红测：先用 CAD 构造整件候选，再让模型给弱语义建议。

Spec: docs/specs/packaging-whole-part-candidate-geometry-first.md
样例名称、坐标和尺寸与酒盒金标无关；不调用网络、不读取 BOM/Excel。
"""
from __future__ import annotations

import copy
import unittest

from tech_app.backend.services import packaging_business_part_resolver as resolver
from tech_app.backend.services import packaging_part_visual_review as review


def _line(entity_id, start, end, layer="CUTTER"):
    x0, y0 = start
    x1, y1 = end
    return {"entity_id": entity_id, "kind": "line", "type": "LINE", "layer": layer,
            "aci_color": 1 if layer == "CUTTER" else 3,
            "bbox": [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
            "attributes": {"start": list(start), "end": list(end)}}


def _fixture(dx=0.0, dy=0.0, name="部件甲", reverse=False):
    """两片互补刀线属于甲；旁边另有乙。每片本身都不是整件外形。"""
    def p(x, y):
        return (x + dx, y + dy)

    entities = [
        _line("e_a1", p(0, 0), p(0, 80)),
        _line("e_a2", p(0, 80), p(60, 80)),
        _line("e_b1", p(60.5, 80), p(120, 80)),
        _line("e_b2", p(120, 80), p(120, 0)),
        _line("e_b3", p(120, 0), p(60.5, 0)),
        _line("e_c1", p(150, 0), p(150, 80)),
        _line("e_c2", p(150, 80), p(200, 80)),
        _line("e_c3", p(200, 80), p(200, 0)),
        _line("e_c4", p(200, 0), p(150, 0)),
    ]
    parts = [
        {"component_id": "cmp:a", "part_code": "G-A", "entity_ids": ["e_a1", "e_a2"],
         "outline": {"bbox": [dx, dy, 60 + dx, 80 + dy]}, "layers": ["CUTTER"],
         "outline_status": "open", "area_mm2": 4800},
        {"component_id": "cmp:b", "part_code": "G-B",
         "entity_ids": ["e_b1", "e_b2", "e_b3"],
         "outline": {"bbox": [60.5 + dx, dy, 120 + dx, 80 + dy]}, "layers": ["CUTTER"],
         "outline_status": "open", "area_mm2": 4760},
        {"component_id": "cmp:c", "part_code": "G-C",
         "entity_ids": ["e_c1", "e_c2", "e_c3", "e_c4"],
         "outline": {"bbox": [150 + dx, dy, 200 + dx, 80 + dy]}, "layers": ["CUTTER"],
         "outline_status": "closed", "area_mm2": 4000},
    ]
    def dim(entity_id, a, b):
        return {"entity_id": entity_id, "dim_type": "linear", "measured_value": (
            abs(a[0] - b[0]) or abs(a[1] - b[1])),
            "target_entity_ids": ["point:%.3f,%.3f" % p(*a),
                                  "point:%.3f,%.3f" % p(*b)]}

    ir = {"entities": entities[::-1] if reverse else entities,
          "texts": [
              {"entity_id": "t_name", "raw_text": "名称：" + name,
               "position": list(p(45, 95)), "layer": "0"},
              {"entity_id": "t_material", "raw_text": "1.8mm灰板裱光银纸",
               "position": list(p(45, 106)), "layer": "0"},
              {"entity_id": "t_legend", "raw_text": "Generic Rule Legend Cut",
               "position": list(p(350, 200)), "layer": "TITLE"},
          ],
          "dimensions": [dim("d_x", (0, 0), (120, 0)),
                         dim("d_y", (120, 0), (120, 80)),
                         # Another view has the same numbers; it must not confirm this one.
                         dim("d_far_x", (400, 400), (520, 400)),
                         dim("d_far_y", (520, 400), (520, 480))],
          "layers": [{"name": "CUTTER", "role": "cut"}],
          "geometry": {"components": []},
          "source": {"original_filename": "independent-synthetic.dwg"}}
    return ir, {"parts": parts[::-1] if reverse else parts}


def _member_sets(candidates):
    return {frozenset(row.get("component_ids") or []) for row in candidates}


class PackagingWholePartCandidateGeometryFirstRed(unittest.TestCase):
    def test_two_disconnected_fragments_can_form_one_candidate_without_swallowing_neighbour(self):
        ir, geometry = _fixture()
        candidates = review.build_part_view_candidates(ir, geometry)
        members = _member_sets(candidates)
        self.assertIn(frozenset({"cmp:a", "cmp:b"}), members,
                      "整件候选不能永远等于单个连通分量")
        combined = next(row for row in candidates
                        if frozenset(row.get("component_ids") or []) == {"cmp:a", "cmp:b"})
        self.assertEqual([0, 0, 120, 80], combined["bbox"])
        self.assertTrue(set(combined["entity_ids"]) <= {row["entity_id"] for row in ir["entities"]})
        self.assertNotIn("cmp:c", combined["component_ids"])
        self.assertTrue(combined.get("evidence_reasons"), "必须交代为什么两片可合成一件")

    def test_candidate_members_do_not_depend_on_absolute_coordinates_order_or_gold_name(self):
        baseline = _member_sets(review.build_part_view_candidates(*_fixture()))
        translated = _member_sets(review.build_part_view_candidates(
            *_fixture(dx=317.25, dy=-51.5, name="另外一种称呼", reverse=True)))
        self.assertIn(frozenset({"cmp:a", "cmp:b"}), baseline)
        self.assertEqual(baseline, translated)

    def test_equal_numbers_elsewhere_are_not_this_parts_dimension_evidence(self):
        region = {"bbox": [0, 0, 120, 80], "length_mm": 120, "width_mm": 80,
                  "entity_ids": ["e_a1", "e_a2", "e_b1", "e_b2", "e_b3"]}
        far = {"length_mm": 120, "width_mm": 80, "center": [460, 440],
               "view_id": "other-view",
               "horizontal_targets": [[400, 400], [520, 400]],
               "vertical_targets": [[520, 400], [520, 480]]}
        self.assertFalse(resolver._region_is_size_confirmed(region, [far]),
                         "不能全图找到相同 120×80 就确认当前零件")
        wrong_witness = dict(far, center=[60, 40], view_id="this-view")
        self.assertFalse(resolver._region_is_size_confirmed(region, [wrong_witness]),
                         "标注中心靠近也不够，扩展点指向别件仍不得确认")

    def test_model_may_select_existing_whole_candidate_but_only_as_weak_advice(self):
        request = {"candidates": [{"candidate_id": "group:ab", "view_id": "v1",
                                   "component_ids": ["cmp:a", "cmp:b"],
                                   "entity_ids": ["e_a1", "e_b1"],
                                   "geometry_status": "ambiguous"}],
                   "confirmed_fields": {"name": "图纸原文"}}
        original = copy.deepcopy(request)
        answer = review.validate_model_suggestion(
            request, {"candidate_id": "group:ab", "text_class": "name",
                      "suggested_name": "名称规范化建议", "confidence": 0.99,
                      "reason": "视图和文字相近"})
        self.assertEqual("needs_confirmation", answer["status"])
        self.assertEqual("WEAK", answer["evidence_level"])
        self.assertEqual("group:ab", answer["candidate_id"])
        self.assertEqual(original, request, "建议不能原地覆盖 CAD 或已确认名称")
        for forbidden in ("size_confirmed", "length_mm", "width_mm", "geometry_binding"):
            self.assertNotIn(forbidden, answer)

    def test_real_ids_are_not_enough_to_authorise_arbitrary_cross_view_merge(self):
        request = {"candidates": [
            {"candidate_id": "v1:a", "view_id": "v1", "component_ids": ["cmp:a"],
             "entity_ids": ["e_a1"], "geometry_status": "supported"},
            {"candidate_id": "v2:c", "view_id": "v2", "component_ids": ["cmp:c"],
             "entity_ids": ["e_c1"], "geometry_status": "supported"},
        ]}
        invented = review.validate_model_suggestion(
            request, {"candidate_id": "group:a+c", "text_class": "name",
                      "suggested_name": "合成件", "reason": "看起来相近"})
        self.assertEqual("rejected", invented["status"])
        cross_view = review.validate_model_suggestion(
            request, {"candidate_ids": ["v1:a", "v2:c"], "action": "merge",
                      "text_class": "name", "suggested_name": "合成件"})
        self.assertEqual("rejected", cross_view["status"])

    def test_model_cannot_smuggle_geometry_size_or_legend_into_business_fact(self):
        request = {"candidates": [{"candidate_id": "group:ab", "view_id": "v1",
                                   "component_ids": ["cmp:a", "cmp:b"],
                                   "entity_ids": ["e_a1", "e_b1"],
                                   "geometry_status": "ambiguous"}]}
        forbidden = review.validate_model_suggestion(
            request, {"candidate_id": "group:ab", "text_class": "name",
                      "entity_ids": ["e_a1", "fake"], "bbox": [0, 0, 999, 999],
                      "length_mm": 999, "width_mm": 999, "size_confirmed": True,
                      "status": "bound"})
        self.assertEqual("rejected", forbidden["status"])
        legend = review.validate_model_suggestion(
            request, {"candidate_id": "group:ab", "text_class": "legend",
                      "suggested_name": "Generic Rule Legend Cut"})
        self.assertEqual("rejected", legend["status"])

    def test_resolver_sends_whole_candidate_not_only_nearest_single_fragment(self):
        ir, geometry = _fixture()
        requests = []

        def reviewer(request):
            requests.append(request)
            return {"selected_candidate_id": "none", "insufficient_evidence": True,
                    "reason": "证据不足"}

        outcome = resolver.resolve_business_parts(
            "whole-part-red", ir, geometry, use_model=True, model_reviewer=reviewer)
        self.assertTrue(requests, "模型复核必须接到实际解析链路")
        self.assertTrue(any({"cmp:a", "cmp:b"} <= set(candidate.get("component_ids") or [])
                            for request in requests for candidate in request.get("candidates") or []),
                        "只给最近 8 个单片，模型就不可能选择真正的整件")
        self.assertFalse(outcome["detail"]["gold_standard_used"])
        self.assertTrue(all(row.get("status") != "bound"
                            for row in outcome["match"]["bindings"]),
                        "模型拒答不得恢复旧的距离绑定")

    def test_model_timeout_keeps_whole_candidate_without_restoring_distance_binding(self):
        ir, geometry = _fixture()

        def reviewer(_request):
            raise TimeoutError("synthetic model timeout")

        outcome = resolver.resolve_business_parts(
            "whole-part-timeout-red", ir, geometry,
            use_model=True, model_reviewer=reviewer)
        self.assertTrue(all(row.get("status") != "bound"
                            for row in outcome["match"]["bindings"]),
                        "模型超时不能退回单片距离绑定")
        self.assertTrue(any(
            {"cmp:a", "cmp:b"} <= set(candidate.get("component_ids") or [])
            for row in outcome["match"]["bindings"]
            for candidate in row.get("candidates") or []),
            "超时后仍须可查看 CAD 整件候选并人工确认")


if __name__ == "__main__":
    unittest.main()
