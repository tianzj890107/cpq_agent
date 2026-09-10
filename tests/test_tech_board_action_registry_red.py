from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
PARENT = (FRONTEND / "tech-workbench.js").read_text(encoding="utf-8")
RUNTIME = FRONTEND / "tech-board-runtime.js"

PAGE_ACTIONS = {
    "requirement-create.js": ("saveRequirementDraft", "submitRequirement"),
    "requirement-confirm-page.js": ("confirmRequirement", "returnRequirementDraft"),
    "requirement-review-page.js": ("submitRequirementReview",),
    "app.js": ("parseDrawing",),
    "assembly-integration.js": ("runIntegration", "sendIntegrationToFinance"),
    "cost-review.js": ("runCostReview", "confirmCostReview"),
    "summary-result.js": ("saveProcessReport", "submitProcessReportReview"),
    "report-review-result.js": ("approveProcessReport", "rejectProcessReport"),
    "report-publish-result.js": ("publishProcessReport",),
}


class TechBoardActionRegistryRedTest(unittest.TestCase):
    def test_parent_stage_actions_use_business_names_not_selectors(self):
        match = re.search(r'const STAGE_ACTIONS\s*=\s*\{([\s\S]*?)\n\s*\};', PARENT)
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertNotRegex(body, r'["\']#[A-Za-z]')
        for action in (
            "saveRequirementDraft", "submitRequirement", "confirmRequirement",
            "returnRequirementDraft", "submitRequirementReview", "parseDrawing",
            "runIntegration", "sendIntegrationToFinance", "runCostReview",
            "confirmCostReview", "saveProcessReport", "submitProcessReportReview",
            "approveProcessReport", "rejectProcessReport", "publishProcessReport",
        ):
            self.assertIn(action, body)

    def test_every_stage_registers_its_existing_actions(self):
        missing = []
        for filename, actions in PAGE_ACTIONS.items():
            source = (FRONTEND / filename).read_text(encoding="utf-8", errors="replace")
            if "TechBoardRuntime.registerActions" not in source:
                missing.append(f"{filename}:registerActions")
                continue
            for action in actions:
                if action not in source:
                    missing.append(f"{filename}:{action}")
        self.assertFalse(missing, f"未注册业务动作：{missing}")

    def test_registry_exposes_state_and_structured_failures(self):
        self.assertTrue(RUNTIME.is_file())
        source = RUNTIME.read_text(encoding="utf-8")
        for token in ("registerActions", "visible", "enabled", "busy", "label", "success", "error"):
            self.assertIn(token, source)

    def test_cross_layer_execution_does_not_use_click(self):
        self.assertNotRegex(PARENT, r'contentDocument[\s\S]{0,500}\.click\(')


if __name__ == "__main__":
    unittest.main()
