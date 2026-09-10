from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "tech_app" / "backend" / "main.py"
AGENT = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"


# 第 0 步冻结的最小能力集。可以新增，不能删除。
REQUIRED_ROUTES = {
    ("post", "/api/projects"),
    ("post", "/api/projects/3d"),
    ("post", "/api/projects/{project_id}/parse"),
    ("get", "/api/projects/{project_id}/files"),
    ("get", "/api/projects/{project_id}/tree"),
    ("get", "/api/projects/{project_id}/bom"),
    ("get", "/api/projects/{project_id}/bom.csv"),
    ("put", "/api/projects/{project_id}/ir"),
    ("post", "/api/projects/{project_id}/verify"),
    ("post", "/api/projects/{project_id}/model-lookup"),
    ("post", "/api/projects/{project_id}/decompose"),
    ("post", "/api/projects/{project_id}/generate"),
    ("post", "/api/projects/{project_id}/drawings"),
    ("post", "/api/projects/{project_id}/parts/{part_id}/process"),
    ("put", "/api/projects/{project_id}/parts/{part_id}/process"),
    ("post", "/api/projects/{project_id}/parts/{part_id}/cost"),
    ("put", "/api/projects/{project_id}/parts/{part_id}/cost"),
    ("get", "/api/projects/{project_id}/integration"),
    ("post", "/api/projects/{project_id}/integration/params"),
    ("put", "/api/projects/{project_id}/integration/params"),
    ("post", "/api/projects/{project_id}/integration/process"),
    ("put", "/api/projects/{project_id}/integration/process"),
    ("post", "/api/projects/{project_id}/integration/cost"),
    ("get", "/api/projects/{project_id}/cost-review"),
    ("post", "/api/projects/{project_id}/cost-review/parts/{part_id}"),
    ("post", "/api/projects/{project_id}/cost-review/assembly"),
    ("post", "/api/projects/{project_id}/cost-review/confirm"),
    ("post", "/api/projects/{project_id}/cost-review/send-to-quote"),
    ("post", "/api/projects/{project_id}/cost-review/return-to-process"),
    ("get", "/api/projects/{project_id}/requirement"),
    ("put", "/api/projects/{project_id}/requirement"),
    ("post", "/api/projects/{project_id}/requirement/extract-documents"),
    ("post", "/api/projects/{project_id}/requirement/submit-confirmation"),
    ("post", "/api/projects/{project_id}/requirement/confirm"),
    ("post", "/api/projects/{project_id}/requirement/return-to-draft"),
    ("post", "/api/projects/{project_id}/requirement/review"),
    ("get", "/api/projects/{project_id}/process-report"),
    ("post", "/api/projects/{project_id}/process-report/prepare"),
    ("put", "/api/projects/{project_id}/process-report"),
    ("post", "/api/projects/{project_id}/process-report/submit-review"),
    ("post", "/api/projects/{project_id}/process-report/review"),
    ("post", "/api/projects/{project_id}/process-report/publish"),
    ("get", "/api/projects/{project_id}/workflow"),
    ("get", "/api/projects/{project_id}/tasks/{task_id}"),
    ("get", "/api/projects/{project_id}/audit"),
    ("get", "/api/projects/{project_id}/agent/meta"),
    ("post", "/api/projects/{project_id}/agent/send"),
    ("post", "/api/projects/{project_id}/agent/new"),
}

REQUIRED_AGENT_TOOLS = {
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "LookupComponentLibrary", "LookupProcessLibrary", "LookupCostLibrary",
    "UpdatePartParameters", "RequestParse", "GetIntegrationState",
    "ListIntegrationParams", "UpdateIntegrationParams",
    "UpdateIntegrationProcess", "RequestIntegrationStep",
}


class TechBackendCapabilityPreservationRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = MAIN.read_text(encoding="utf-8")
        cls.agent = AGENT.read_text(encoding="utf-8")

    def test_existing_backend_routes_are_preserved(self):
        actual = set(re.findall(
            r'@app\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']', self.main))
        self.assertFalse(REQUIRED_ROUTES - actual, f"缺少既有路由：{sorted(REQUIRED_ROUTES - actual)}")

    def test_existing_agent_platform_tools_are_preserved(self):
        actual = set(re.findall(r'["\']name["\']:\s*["\']([^"\']+)["\']', self.agent))
        self.assertFalse(
            REQUIRED_AGENT_TOOLS - actual,
            f"缺少既有 Agent 工具：{sorted(REQUIRED_AGENT_TOOLS - actual)}",
        )

    def test_core_domains_still_have_real_service_calls(self):
        for marker in (
            "requirement_extract.prepare_documents", "integration.save_plan",
            "cost_lookup.lookup_part", "process_lookup.lookup_part",
            "store.save_ir", "store.save_requirement", "store.save_process_report",
        ):
            self.assertIn(marker, self.main + "\n" + self.agent)

    def test_no_501_placeholder_was_introduced(self):
        self.assertNotRegex(self.main, r'HTTPException\(\s*501|status_code\s*=\s*501')


if __name__ == "__main__":
    unittest.main()

