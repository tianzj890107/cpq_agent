"""Red tests for docs/specs/e2e-quote-session-and-completion-closure.md.

Offline only: no PG, model, network or production data.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "报价首页.html").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")
SERVER = (ROOT / "cpq_agent_server.py").read_text(encoding="utf-8")


class QuoteSessionIdentityRed(unittest.TestCase):
    def test_card_navigation_carries_session_id_in_url(self):
        """刷新可恢复，不能只在打开前写临时状态。"""
        self.assertTrue(
            re.search(r"确认需求解析结果\.html[^\n]{0,240}(?:session_id|session)=", HOME),
            "报价卡进入工作台的 URL 必须显式携带 session_id",
        )

    def test_workbench_reads_session_id_from_url(self):
        self.assertTrue(
            re.search(r"URLSearchParams[\s\S]{0,400}(?:get\(['\"]session_id|get\(['\"]session)",
                      WORKBENCH),
            "工作台必须优先从 URL 恢复报价会话",
        )

    def test_persisted_identity_guard_exists(self):
        self.assertTrue("validate_business_identity" in SERVER,
                      "服务端缺少 session/business-case/project/task 单实例校验")


class QuoteCompletionGateRed(unittest.TestCase):
    def test_empty_product_rows_are_a_hard_gate(self):
        self.assertTrue("quote_step_completion_gate" in SERVER,
                      "缺少服务端步骤完成门禁，前端 POC 提示不能代替业务约束")

    def test_force_fill_cannot_bypass_empty_quote(self):
        fn = re.search(r"async function fillStepRecommend\([^)]*\)\s*\{([\s\S]*?)\n\s*\}",
                      WORKBENCH)
        self.assertIsNotNone(fn, "找不到强行填满实现")
        self.assertRegex(fn.group(1), r"completionGate|quoteCompletionGate|canCompleteQuoteStep",
                         "强行填满必须调用同一份完成门禁")

    def test_export_and_import_require_non_empty_detail(self):
        self.assertTrue("assertQuoteExportable" in WORKBENCH,
                      "生成 Word/导入数据库前必须校验报价明细非空且金额有效")

    def test_finance_handoff_has_structured_cost_payload(self):
        required = ("tech_project_id", "unit_cost", "cost_fingerprint", "gap_count",
                    "provisional")
        for name in required:
            self.assertTrue(name in SERVER, f"报价服务缺少财务回传字段 {name}")


if __name__ == "__main__":
    unittest.main()
