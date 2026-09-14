import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKBENCH = (ROOT / "tech_app/frontend/tech-workbench.js").read_text(encoding="utf-8")
ASSEMBLY_HTML = (ROOT / "tech_app/frontend/assembly-integration.html").read_text(encoding="utf-8")
ASSEMBLY_JS = (ROOT / "tech_app/frontend/assembly-integration.js").read_text(encoding="utf-8")
COST_HTML = (ROOT / "tech_app/frontend/cost-review.html").read_text(encoding="utf-8")
COST_JS = (ROOT / "tech_app/frontend/cost-review.js").read_text(encoding="utf-8")
MAIN = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
INTEGRATION = (ROOT / "tech_app/backend/services/integration.py").read_text(encoding="utf-8")


def py_function(source: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?def\s+{re.escape(name)}\s*\([^)]*\)[^:]*:\s*([\s\S]*?)"
        rf"(?=\n(?:async\s+)?def\s+|\n@app\.|\Z)",
        source,
    )
    if not match:
        raise AssertionError(f"找不到 Python 函数 {name}")
    return match.group(1)


class CostStepHasOnlyCostTabs(unittest.TestCase):
    def test_parent_cost_proxy_has_exactly_three_cost_views(self):
        block = re.search(
            r"['\"]cost['\"]\s*:\s*\{\s*tabs\s*:\s*\[([\s\S]*?)\]\s*,?\s*\}",
            WORKBENCH,
        )
        self.assertIsNotNone(block)
        keys = re.findall(r"key\s*:\s*['\"]([^'\"]+)", block.group(1))
        self.assertEqual(["parts", "assembly", "total"], keys)
        self.assertNotIn("整合参数", block.group(1))

    def test_cost_page_has_no_integration_params_ui(self):
        for token in ('整合参数', 'crGoParams', 'data-cr-tab="params"'):
            with self.subTest(token=token):
                self.assertNotIn(token, COST_HTML)

    def test_cost_script_has_no_integration_params_business(self):
        for token in (
            "crRenderParams", "crAutofill", "crFinalize",
            "/integration/params/autofill", "/integration/params/finalize",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, COST_JS)
        self.assertNotRegex(COST_JS, r"params\s*:\s*\{\s*run\s*:\s*\(\)\s*=>\s*crSetTab")
        self.assertRegex(COST_JS, r"(?:const\s+CR_TABS|renderers)\s*=\s*\{[^}]*parts[^}]*assembly[^}]*total")


class IntegrationStepOwnsCompleteParameterRecommendation(unittest.TestCase):
    def test_integration_keeps_parameter_recommendation_tab_and_required_checklist(self):
        self.assertIn('data-ai-tab="params"', ASSEMBLY_HTML)
        self.assertIn("QuoteParams.card", ASSEMBLY_JS)
        self.assertIn("param_checklist", ASSEMBLY_JS)
        self.assertRegex(ASSEMBLY_JS, r"required_missing|required_total|missing_required")

    def test_integration_page_owns_autofill_save_and_final_confirmation(self):
        self.assertRegex(ASSEMBLY_JS, r"async\s+function\s+ai\w*Autofill\s*\(")
        self.assertRegex(ASSEMBLY_JS, r"async\s+function\s+ai\w*(?:Finalize|FinalConfirm)\s*\(")
        self.assertIn("/integration/params/autofill", ASSEMBLY_JS)
        self.assertIn("/integration/params/finalize", ASSEMBLY_JS)
        for label in ("智能补全", "保存补填", "确认参数已齐"):
            with self.subTest(label=label):
                self.assertIn(label, ASSEMBLY_JS)

    def test_integration_parameter_messages_no_longer_defer_missing_fields_to_cost(self):
        self.assertNotRegex(ASSEMBLY_JS, r"2\.3[^\n]{0,100}(?:整合参数|补齐|补填)")
        self.assertNotRegex(ASSEMBLY_JS, r"财务经理[^\n]{0,100}(?:整合参数|参数)[^\n]{0,100}(?:补齐|补填)")


class ParameterFinalizationAndFlowGate(unittest.TestCase):
    def test_existing_autofill_and_finalize_routes_remain(self):
        self.assertIn('/api/projects/{project_id}/integration/params/autofill', MAIN)
        self.assertIn('/api/projects/{project_id}/integration/params/finalize', MAIN)

    def test_parameter_completion_routes_use_process_write_roles(self):
        autofill = py_function(MAIN, "autofill_integration_params")
        finalize = py_function(MAIN, "finalize_integration_params")
        for body in (autofill, finalize):
            self.assertIn("auth.WRITE_ROLES", body)
            self.assertNotIn("auth.COST_ROLES", body)
            self.assertNotRegex(body, r"2\.3|财务经理负责")

    def test_send_to_finance_requires_final_complete_parameters(self):
        body = py_function(INTEGRATION, "send_to_finance")
        self.assertIn("params_final", body)
        self.assertRegex(body, r"missing_required\s*\(")
        self.assertRegex(body, r"raise\s+IntegrationFlowError")
        self.assertNotRegex(body, r"财务[^\n]{0,100}(?:补齐|补填)")

    def test_cost_permissions_and_core_actions_remain(self):
        self.assertIn("auth.COST_ROLES", py_function(MAIN, "generate_integration_cost"))
        for token in ("runCostReview", "confirmCostReview", "零件成本", "组装成本", "汇总"):
            with self.subTest(token=token):
                self.assertIn(token, WORKBENCH + COST_HTML + COST_JS)


if __name__ == "__main__":
    unittest.main()
