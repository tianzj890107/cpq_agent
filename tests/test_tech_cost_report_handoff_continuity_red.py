import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COST_HTML = (ROOT / "tech_app/frontend/cost-review.html").read_text(encoding="utf-8")
COST_JS = (ROOT / "tech_app/frontend/cost-review.js").read_text(encoding="utf-8")
REPORT_JS = (ROOT / "tech_app/frontend/report-publish-result.js").read_text(encoding="utf-8")
PARENT = (ROOT / "tech_app/frontend/tech-workbench.js").read_text(encoding="utf-8")
INBOX = (ROOT / "tech_app/frontend/cpq-tech-inbox.js").read_text(encoding="utf-8")
MAIN = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
COST_FLOW = (ROOT / "tech_app/backend/services/cost_flow.py").read_text(encoding="utf-8")
REPORT_FLOW = (ROOT / "tech_app/backend/services/report_workflow.py").read_text(encoding="utf-8")
BRIDGE = (ROOT / "cpq_tech_bridge.py").read_text(encoding="utf-8")
WF = (ROOT / "cpq_wf.py").read_text(encoding="utf-8")
QUOTE = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")


def py_body(source: str, name: str) -> str:
    start = source.find(f"def {name}(")
    if start < 0:
        raise AssertionError(f"找不到函数 {name}")
    rest = source[start:]
    match = re.search(r"\n(?:async\s+)?def\s+\w+\s*\(", rest[1:])
    return rest if not match else rest[: match.start() + 1]


class CostDestinationContract(unittest.TestCase):
    def test_cost_page_exposes_process_confirmation_and_sales_continuation(self):
        surface = COST_HTML + COST_JS
        self.assertIn("提交工艺经理确认", surface)
        self.assertIn("回传销售经理继续报价", surface)
        self.assertNotIn("退回工艺经理复核", surface)

    def test_cost_page_propagates_current_task_identity(self):
        self.assertRegex(COST_JS, r"(?:task_id|tech_task)")
        self.assertRegex(COST_JS, r"URLSearchParams[\s\S]{0,300}(?:task_id|tech_task)")
        operation = py_body(COST_FLOW, "return_to_process")
        self.assertRegex(operation, r"source_task_id|task_id")

    def test_process_manager_handoff_contains_complete_cost_and_process_package(self):
        body = py_body(COST_FLOW, "return_to_process")
        for token in ("project_id", "params", "process", "parts", "assembly", "final", "confirmed"):
            with self.subTest(token=token):
                self.assertIn(token, body)
        self.assertRegex(body, r"quote_session_id|source_session_id")

    def test_process_manager_task_opens_major_step_five(self):
        branch = re.search(
            r"kind\s*===\s*['\"]tech_cost_return['\"]([\s\S]*?)(?=\n\s*\}|\n\s*if\s*\()",
            INBOX,
        )
        self.assertIsNotNone(branch)
        self.assertRegex(branch.group(1), r"stage=summary|['\"]summary['\"]")
        self.assertNotRegex(branch.group(1), r"stage=process|['\"]process['\"]")

    def test_finance_claimed_task_is_closed_when_destination_is_completed(self):
        combined = COST_FLOW + "\n" + BRIDGE + "\n" + WF
        self.assertRegex(combined, r"complete_(?:claimed_)?task|close_(?:claimed_)?task")
        self.assertRegex(combined, r"source_task_id")


class SalesQuoteContinuationContract(unittest.TestCase):
    def test_cost_to_sales_builds_full_quote_handoff_payload(self):
        body = py_body(COST_FLOW, "integration_quote_result")
        for token in ("params", "cost", "process", "project_id", "quantity", "params_final"):
            with self.subTest(token=token):
                self.assertIn(token, body)
        self.assertRegex(body, r"parts|part_cost")
        self.assertRegex(body, r"assembly|assembly_cost")

    def test_bridge_never_uses_tech_project_id_as_fake_quote_session(self):
        body = py_body(BRIDGE, "send_to_quote")
        self.assertNotIn("cpq_wf.sync_card(session_id", body)
        self.assertRegex(body, r"create.*(?:quote_)?session|ensure.*(?:quote_)?session", re.I)
        self.assertIn("source_session_id", body)

    def test_quote_step_two_snapshot_and_task_payload_keep_technical_result(self):
        body = py_body(BRIDGE, "send_to_quote")
        self.assertIn("_step2_snapshot", body)
        self.assertIn('"tech_result"', body)
        for token in ("s2_products", "s2_techparams"):
            self.assertIn(token, BRIDGE)
        self.assertRegex(QUOTE, r"wfRestoreStepData\(2\)")
        self.assertIn("第 3 步", body)


class PublishedReportHandoffContract(unittest.TestCase):
    def test_published_report_has_visible_sales_handoff_button(self):
        self.assertRegex(REPORT_JS, r"回传销售经理继续报价")
        self.assertRegex(REPORT_JS, r"(?:id|data-[\w-]+)=['\"][^'\"]*(?:Quote|quote|Sales|sales)[^'\"]*['\"]")

    def test_report_uses_dedicated_handoff_route_not_integration_route(self):
        self.assertNotIn("/integration/send-to-quote", REPORT_JS)
        self.assertRegex(
            REPORT_JS,
            r"/process-report/(?:send-to-quote|handoff-to-sales|return-to-quote)",
        )
        self.assertRegex(
            MAIN,
            r"@app\.post\(\s*['\"]/api/projects/\{project_id\}/process-report/"
            r"(?:send-to-quote|handoff-to-sales|return-to-quote)['\"]",
        )

    def test_report_handoff_requires_published_and_carries_full_report(self):
        combined = MAIN + "\n" + REPORT_FLOW
        self.assertRegex(combined, r"def\s+(?:send|handoff|return)\w*(?:quote|sales)\w*\s*\(")
        for token in (
            "published", "report_no", "version", "title", "reviewed_by",
            "published_by", "distribution_scope", "attachments",
        ):
            with self.subTest(token=token):
                self.assertIn(token, combined)

    def test_report_handoff_is_idempotent_and_does_not_regress_quote_step(self):
        combined = REPORT_FLOW + "\n" + BRIDGE + "\n" + WF
        self.assertRegex(combined, r"idempot|dedup|already_sent|handoff_key|result_version", re.I)
        self.assertRegex(combined, r"current_step")
        self.assertRegex(combined, r"(?:>=|max\()[^\n]{0,100}(?:TECH_CONFIRM_STEP|step_no|current_step)")

    def test_report_publish_parent_exposes_sales_handoff_action(self):
        block = re.search(r"['\"]report-publish['\"]\s*:\s*\{([^\n]+)", PARENT)
        self.assertIsNotNone(block)
        self.assertIn("sendReportToQuote", block.group(1))


class ExistingWorkflowPreservation(unittest.TestCase):
    def test_existing_cost_report_and_workflow_capabilities_remain(self):
        for token in (
            "/api/projects/{project_id}/cost-review/confirm",
            "/api/projects/{project_id}/cost-review/send-to-quote",
            "/api/projects/{project_id}/cost-review/return-to-process",
            "/api/projects/{project_id}/process-report/publish",
        ):
            with self.subTest(token=token):
                self.assertIn(token, MAIN)
        for token in ("TASK_KIND_TECH_COST", "TASK_KIND_TECH_COST_RETURN", "complete_step", "send_task"):
            with self.subTest(token=token):
                self.assertIn(token, WF)


if __name__ == "__main__":
    unittest.main()
