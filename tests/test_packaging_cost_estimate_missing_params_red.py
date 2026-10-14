"""Spec `packaging-cost-estimate-missing-params.md` 红测。

口径：公式与费率一行不改；只有**库内对不上**的件（相似命中 / 没命中）才把缺的
`standard_seconds` 按相似件估出来，标 `similarity_estimate`，不需要 approved，但不冒充标准工时。
"""
import unittest
from pathlib import Path

from tech_app.backend.services import packaging_process_estimate as estimate
from tech_app.backend.services import packaging_process_instances as instances

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = [
    {"box_type_code": "YT-RB-01001-A", "step_name": "组装", "standard_seconds": 22.0,
     "part_code": "RB01001-P03"},
    {"box_type_code": "YT-RB-01001-A", "step_name": "V 槽开槽", "standard_seconds": 15.0,
     "part_code": "RB01001-P01"},
    {"box_type_code": "YT-RB-02001-A", "step_name": "组装", "standard_seconds": 25.0,
     "part_code": "RB02001-P05"},
    {"box_type_code": "YT-DWG-ROUND-10PC", "step_name": "V槽围边", "standard_seconds": None,
     "part_code": "ASSY"},
]


def similar_lookup(**overrides):
    lookup = {"status": "not_found", "match_method": "unbound", "reference_routes": []}
    lookup.update(overrides)
    return lookup


def record(lookup, *, part_code="DWG-BP99", steps=None):
    return {"part_code": part_code, "record_hash": "h-" + part_code,
            "process_scope": "business_part/1", "usage_qty": 1, "lookup": lookup,
            "plan": {"part_class": "packaging",
                     "steps": steps or [{"step_no": 10, "name": "组装", "type": "assembly"}]}}


class ConstTests(unittest.TestCase):
    def test_a1_constants_verbatim(self):
        self.assertEqual("packaging-process-estimate/1", estimate.ENGINE_VERSION)
        self.assertEqual(("similarity_estimate",), estimate.ESTIMATE_TIME_SOURCES)
        self.assertEqual(("standard_seconds",), estimate.ESTIMATE_KINDS)
        self.assertEqual(("reference_route", "similar_step_template",
                          "operations_estimate_policy"), estimate.ESTIMATE_ORIGINS)
        self.assertEqual(30.0, estimate.DEFAULT_OPERATION_SECONDS)

    def test_a2_similarity_route_gate(self):
        self.assertFalse(estimate.similarity_route(None))
        self.assertFalse(estimate.similarity_route({}))
        self.assertFalse(estimate.similarity_route({"status": "matched", "match_method": "exact_name"}))
        self.assertFalse(estimate.similarity_route({"status": "matched", "match_method": "normalized_name"}))
        self.assertTrue(estimate.similarity_route({"match_method": "feature_similarity"}))
        self.assertTrue(estimate.similarity_route(similar_lookup()))
        self.assertFalse(estimate.similarity_route({"status": "unavailable", "match_method": "unbound"}))


class EstimateSecondsTests(unittest.TestCase):
    def test_b1_reference_route_wins(self):
        lookup = similar_lookup(reference_routes=[
            {"reference_code": "A", "steps": [{"operation_name": "组装", "standard_seconds": 45.0}]}])
        found = estimate.estimate_seconds("组装", lookup=lookup, templates=TEMPLATES,
                                          box_type_code="YT-DWG-ROUND-10PC")
        self.assertEqual("reference_route", found["origin"])
        self.assertEqual(45.0, found["seconds"])

    def test_b2_template_same_box_type_first(self):
        found = estimate.estimate_seconds("组装", lookup=similar_lookup(), templates=TEMPLATES,
                                          box_type_code="YT-RB-01001-A")
        self.assertEqual("similar_step_template", found["origin"])
        self.assertEqual(22.0, found["seconds"])
        # 盒型对不上时按 (step_name, part_code) 稳定取第一条（不按分数瞎挑）。
        other = estimate.estimate_seconds("组装", lookup=similar_lookup(), templates=TEMPLATES,
                                          box_type_code="YT-DWG-ROUND-10PC")
        self.assertEqual("similar_step_template", other["origin"])
        self.assertEqual("YT-RB-01001-A", other["reference"]["box_type_code"])
        self.assertEqual(22.0, other["seconds"])

    def test_b2b_alias_same_step_matches(self):
        found = estimate.estimate_seconds("V槽围边", lookup=similar_lookup(), templates=TEMPLATES,
                                          box_type_code="YT-DWG-ROUND-10PC")
        self.assertEqual(15.0, found["seconds"])

    def test_b3_policy_fallback(self):
        found = estimate.estimate_seconds("贴牌", lookup=similar_lookup(), templates=TEMPLATES,
                                          box_type_code="YT-DWG-ROUND-10PC")
        self.assertEqual("operations_estimate_policy", found["origin"])
        self.assertEqual(30.0, found["seconds"])

    def test_b4_exact_hit_never_estimated(self):
        self.assertIsNone(estimate.estimate_seconds(
            "组装", lookup={"status": "matched", "match_method": "exact_name"}, templates=TEMPLATES))


class FillStepTests(unittest.TestCase):
    def test_c1_fills_trace(self):
        step = {"step_name": "组装", "standard_seconds": None, "cost_parameters": {}}
        trace = estimate.fill_step(step, name="组装", lookup=similar_lookup(),
                                   templates=TEMPLATES, box_type_code="YT-RB-01001-A", part_code="P1")
        self.assertEqual(22.0, step["standard_seconds"])
        self.assertAlmostEqual(22.0 / 60.0, step["duration_min"])
        self.assertEqual("similarity_estimate", step["time_source"])
        self.assertTrue(step["estimated"])
        self.assertEqual("standard_seconds", trace["kind"])
        self.assertEqual("P1", trace["part_code"])
        self.assertEqual(22.0, step["cost_parameters"]["labor_seconds"])
        self.assertIs(True, step["cost_parameters"]["estimated"])
        self.assertEqual("similar_step_template", step["cost_parameters"]["estimate_origin"])

    def test_c2_existing_seconds_untouched(self):
        step = {"step_name": "组装", "standard_seconds": 8.0, "cost_parameters": {}, "time_source": "provided_standard"}
        self.assertIsNone(estimate.fill_step(step, name="组装", lookup=similar_lookup(), templates=TEMPLATES))
        self.assertEqual(8.0, step["standard_seconds"])
        self.assertNotIn("estimated", step)

    def test_c3_exact_hit_untouched(self):
        step = {"step_name": "组装", "standard_seconds": None, "cost_parameters": {}}
        self.assertIsNone(estimate.fill_step(step, name="组装",
            lookup={"status": "matched", "match_method": "normalized_name"}, templates=TEMPLATES))
        self.assertIsNone(step["standard_seconds"])

    def test_c4_never_changes_formula_or_other_params(self):
        step = {"step_name": "组装", "standard_seconds": None, "formula_code": "PKG-C-LABOR",
                "cost_parameters": {"machine_length": 100.0}}
        estimate.fill_step(step, name="组装", lookup=similar_lookup(), templates=TEMPLATES)
        self.assertEqual("PKG-C-LABOR", step["formula_code"])
        self.assertEqual(100.0, step["cost_parameters"]["machine_length"])


class ExpandWiringTests(unittest.TestCase):
    def test_d1_default_expand_unchanged(self):
        steps = instances.expand([record(similar_lookup())])
        self.assertIsNone(steps[0]["standard_seconds"])
        self.assertEqual("unknown", steps[0]["time_source"])
        self.assertTrue(steps[0]["needs_standard_time"])

    def test_d2_expand_with_templates_estimates(self):
        steps = instances.expand([record(similar_lookup())], templates=TEMPLATES,
                                 box_type_code="YT-RB-01001-A")
        step = steps[0]
        self.assertEqual(22.0, step["standard_seconds"])
        self.assertEqual("similarity_estimate", step["time_source"])
        self.assertTrue(step["estimated"])
        self.assertFalse(step["needs_standard_time"])
        self.assertEqual("PKG-C-LABOR", step["formula_code"])

    def test_d3_exact_hit_untouched_by_expand(self):
        steps = instances.expand([record({"status": "matched", "match_method": "exact_name"})],
                                 templates=TEMPLATES, box_type_code="YT-RB-01001-A")
        self.assertIsNone(steps[0]["standard_seconds"])
        self.assertTrue(steps[0]["needs_standard_time"])

    def test_d4_known_seconds_kept(self):
        steps = instances.expand([record(similar_lookup(), steps=[
            {"step_no": 10, "name": "组装", "type": "assembly", "standard_seconds": 7.0}])],
            templates=TEMPLATES, box_type_code="YT-RB-01001-A")
        self.assertEqual(7.0, steps[0]["standard_seconds"])
        self.assertNotIn("estimated", steps[0])


class RealTemplateAliasTests(unittest.TestCase):
    """真实模板行只读实测（2026-10-14）：圆盘盒模板工序名按闭集别名对到 RB 模板工时。"""

    ROWS = [
        {"box_type_code": "YT-RB-01001-A", "step_name": "V 槽开槽", "standard_seconds": 15.0,
         "part_code": "RB01001-P01"},
        {"box_type_code": "YT-RB-01001-A", "step_name": "机裱", "standard_seconds": 18.0,
         "part_code": "RB01001-P02"},
        {"box_type_code": "YT-RB-01001-A", "step_name": "检验", "standard_seconds": 10.0,
         "part_code": "RB01001-P03"},
        {"box_type_code": "YT-RB-02001-A", "step_name": "面纸印刷", "standard_seconds": 18.0,
         "part_code": "RB02001-P05"},
        {"box_type_code": "YT-RB-01001-A", "step_name": "面纸模切", "standard_seconds": 8.0,
         "part_code": "RB01001-P02"},
        {"box_type_code": "YT-DWG-ROUND-10PC", "step_name": "V槽围边", "standard_seconds": None,
         "part_code": "ASSY"},
    ]

    def _seconds(self, name):
        found = estimate.template_seconds(name, self.ROWS, box_type_code="YT-DWG-ROUND-10PC")
        self.assertIsNotNone(found, name)
        return found

    def test_g1_round_box_names_resolve_to_rb_hours(self):
        self.assertEqual(15.0, self._seconds("V槽围边")["seconds"])
        self.assertEqual(18.0, self._seconds("围边裱贴")["seconds"])
        self.assertEqual(10.0, self._seconds("装配检验包装")["seconds"])
        self.assertEqual(8.0, self._seconds("灰板与面纸模切")["seconds"])
        self.assertEqual(18.0, self._seconds("纸张印刷覆膜")["seconds"])

    def test_g2_same_box_type_row_without_hours_is_skipped(self):
        # 圆盘盒自己的模板行没有工时 → 不许拿它当"0 秒"，必须跳到有工时的那条。
        found = self._seconds("V槽围边")
        self.assertEqual("similar_step_template", found["origin"])
        self.assertEqual("YT-RB-01001-A", found["reference"]["box_type_code"])
        self.assertEqual(15.0, found["seconds"])


class GuardTests(unittest.TestCase):
    def test_e1_manufacturing_model_steps_stay_empty(self):
        from tech_app.backend.models.ir import Part
        from tech_app.backend.models.process import ProcessOutline
        from tech_app.backend.services import packaging_manufacturing as manufacturing
        lookup = similar_lookup(reference_routes=[{"reference_code": "A",
            "steps": [{"operation_name": "组装", "standard_seconds": 45.0}]}])
        plan, _coverage = manufacturing.outline(
            Part(part_id="R", name="圆件", industry="packaging"), lookup=lookup,
            run=lambda *args: ProcessOutline(steps=[{"step_no": 10, "name": "组装", "type": "assembly"}]))
        self.assertTrue(all(s.standard_seconds is None for s in plan.steps))

    def test_e2_cost_formula_and_rates_untouched(self):
        from tech_app.backend.services import packaging_cost as cost
        self.assertEqual("RATE-PKG-LABOR-ASSEMBLY", cost.STEP_RATE_MAP["组装"])
        self.assertIn("PKG-C-LABOR", cost.FORMULA_CATALOG)

    def test_f1_cost_module_never_imports_estimate(self):
        source = (ROOT / "tech_app/backend/services/packaging_cost.py").read_text(encoding="utf-8")
        self.assertNotIn("packaging_process_estimate", source)

    def test_f2_manufacturing_never_sets_similarity_estimate(self):
        source = (ROOT / "tech_app/backend/services/packaging_manufacturing.py").read_text(encoding="utf-8")
        self.assertNotIn("similarity_estimate", source)

    def test_f3_estimate_module_is_offline(self):
        source = (ROOT / "tech_app/backend/services/packaging_process_estimate.py").read_text(encoding="utf-8")
        for banned in ("requests", "urllib", "open(", "llm_client"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
