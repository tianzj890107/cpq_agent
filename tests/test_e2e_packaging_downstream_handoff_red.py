"""Red tests for docs/specs/e2e-packaging-downstream-handoff-report.md."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "tech_app/backend/services"
MAIN = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
COST_FLOW = (SERVICES / "cost_flow.py").read_text(encoding="utf-8")
ACCESS = (SERVICES / "project_access.py").read_text(encoding="utf-8")
REPORT = (SERVICES / "report_workflow.py").read_text(encoding="utf-8")
PACKAGING_COST = (SERVICES / "packaging_cost.py").read_text(encoding="utf-8")


class UnifiedManufacturingSnapshotRed(unittest.TestCase):
    def test_adapter_module_exists(self):
        path = SERVICES / "manufacturing_snapshot.py"
        self.assertTrue(path.exists(),
                        "缺少 legacy IR / packaging parts 的统一制造快照适配层")

    def test_integration_uses_unified_snapshot(self):
        self.assertTrue("manufacturing_snapshot" in MAIN,
                      "2.2 仍未接入包装零件/BOM/route")

    def test_cost_review_uses_unified_snapshot(self):
        block = MAIN[MAIN.find("def _cost_review_ctx"):MAIN.find("def _cost_review_payload")]
        self.assertTrue("manufacturing_snapshot" in block,
                      "2.3 仍只读取 legacy DesignIR")

    def test_report_uses_unified_snapshot(self):
        self.assertTrue("manufacturing_snapshot" in REPORT,
                      "报告草稿仍会把包装项目生成为 0 零件/0 成本")


class HandoffAclAndAtomicityRed(unittest.TestCase):
    def test_task_creation_grants_project_participation(self):
        self.assertTrue("grant_task_project_access" in COST_FLOW,
                      "创建财务任务时必须同步授予项目参与权")

    def test_claimed_finance_task_is_readable(self):
        self.assertTrue("claimed_task_project_access" in ACCESS,
                      "FI 领取正确任务后必须能打开项目")

    def test_explicit_business_case_is_authoritative(self):
        self.assertTrue("explicit_business_case_is_authoritative" in COST_FLOW,
                      "显式 business_case_id 不得再与 source task 候选混合消歧")

    def test_quote_handoff_and_source_close_share_operation(self):
        for token in ("handoff_operation_id", "resume_incomplete_handoff",
                      "source_task_closed"):
            self.assertTrue(token in COST_FLOW, f"回报价原子状态机缺少 {token}")

    def test_project_id_and_quote_session_are_distinct_fields(self):
        self.assertTrue("assert_distinct_project_and_quote_session" in COST_FLOW,
                      "任务 session/project 仍可能误用 quote_session_id")


class PackagingCostAuthorityRed(unittest.TestCase):
    def test_cost_result_has_formal_or_provisional_readiness(self):
        self.assertTrue("packaging_cost_readiness_gate" in PACKAGING_COST,
                        "包装成本必须明确 formal/provisional，不能只有 has_gaps")

    def test_missing_formula_variables_are_structured(self):
        for token in ("missing_variable", "affected_amount", "resolution_action"):
            self.assertTrue(token in PACKAGING_COST,
                            f"成本缺口缺少结构化字段 {token}")

    def test_missing_values_are_not_silently_zeroed(self):
        self.assertTrue("reject_silent_zero_fallback" in PACKAGING_COST,
                        "无价格/换算/公式时不得静默按 0 进入正式成本")


if __name__ == "__main__":
    unittest.main()
