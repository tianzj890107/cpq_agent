"""快速报价入口之后的全流程红测。

这些测试故意针对当前真实断点，不用“函数名出现过”冒充链路已打通。
"""
from pathlib import Path
import inspect
import unittest
from unittest import mock

import cpq_agent_server as server


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "报价首页.html").read_text(encoding="utf-8")
PANEL = (ROOT / "tech_app/frontend/quick-quote-panel.js").read_text(encoding="utf-8")
SERVER = (ROOT / "cpq_agent_server.py").read_text(encoding="utf-8")


class QuickQuoteWorkspaceUiRed(unittest.TestCase):
    def test_diff_is_remembered_from_top_level_payload(self):
        """后端把行放在 payload.diff；不能继续读取 workspace.rows。"""
        remember = PANEL[PANEL.index("function rememberCommandResult"):PANEL.index("function openQuickQuoteSession")]
        self.assertRegex(remember, r"workspaceState\.diff\s*=\s*data\.diff")
        self.assertNotIn("(workspaceState.workspace || {}).rows", PANEL)
        self.assertNotIn("(state.workspace || {}).rows", HOME)

    def test_business_fields_are_not_edited_with_browser_prompt(self):
        start = HOME.index("async function runQuickQuoteWorkspaceCommand")
        flow = HOME[start:HOME.index("(function initQuickQuoteWorkspace", start)]
        self.assertNotRegex(flow, r"\bprompt\s*\(")
        self.assertIn("renderQuickQuoteEditor", HOME + PANEL)

    def test_candidate_click_is_the_only_baseline_selector(self):
        self.assertIn("data-qq-baseline", PANEL)
        self.assertNotIn("把哪个案例选为基准", HOME)

    def test_buttons_are_driven_by_server_workflow_state(self):
        combined = HOME + PANEL
        self.assertIn("workflow_state", combined)
        self.assertIn("can_confirm", combined)
        self.assertIn("syncQuickQuoteWorkflowState", combined)
        self.assertNotIn("const blocked = quickQuoteEligibleTotal() === 0", HOME)

    def test_confirm_is_disabled_before_price_even_when_library_has_cases(self):
        """eligible_total=2 只说明库里有案例，不等于当前实例已有可确认报价。"""
        self.assertRegex(HOME + PANEL, r"priced[\s\S]{0,500}qqWorkspaceConfirm")

    def test_quick_mode_does_not_open_a_blocking_library_modal(self):
        init = HOME[HOME.index("function initQuoteModeEntries") - 800:
                    HOME.index("function initQuoteModeEntries") + 1600]
        self.assertNotRegex(init, r"mode\s*===\s*['\"]quick['\"][\s\S]{0,200}openQuickQuotePanel\s*\(")


class QuickQuoteBackendStateRed(unittest.TestCase):
    def setUp(self):
        server.QUICK_QUOTE_SESSIONS.clear()
        server.quick_quote_idempotency.clear()

    def test_read_unknown_session_is_404_and_does_not_create_ghost(self):
        with mock.patch.object(server.cpq_quick_quote_price, "find_quote", return_value={}):
            out = server._handle_quick_quote_read("does-not-exist")
        self.assertFalse(out["ok"])
        self.assertEqual(404, out["status"])
        self.assertEqual("session_not_found", out["code"])
        self.assertNotIn("does-not-exist", server.QUICK_QUOTE_SESSIONS)

    def test_write_unknown_session_is_404_and_does_not_create_ghost(self):
        out = server._handle_quick_quote_session_write(
            "ghost", "match", {"inputs": {"box_type": "YT-DWG-WINE-700ML"}},
            user={"user_id": "SM1"}, idempotency_key="op-1")
        self.assertFalse(out["ok"])
        self.assertEqual("session_not_found", out["code"])
        self.assertNotIn("ghost", server.QUICK_QUOTE_SESSIONS)

    def test_responses_expose_server_workflow_state_and_allowed_actions(self):
        server.QUICK_QUOTE_SESSIONS["s1"] = {
            "inputs": {}, "baseline": {}, "workspace": {}, "quote": {},
            "versions": 0, "quote_mode": "quick", "industry": "packaging"}
        with mock.patch.object(server.cpq_quick_quote_price, "find_quote", return_value={}):
            out = server._handle_quick_quote_read("s1")
        self.assertIn("workflow_state", out)
        self.assertEqual("created", out["workflow_state"])
        self.assertIn("match", out["allowed_actions"])
        self.assertNotIn("confirm", out["allowed_actions"])

    def test_out_of_order_command_reports_required_and_current_state(self):
        server.QUICK_QUOTE_SESSIONS["s1"] = {
            "inputs": {}, "baseline": {}, "workspace": {}, "quote": {},
            "versions": 0, "quote_mode": "quick", "industry": "packaging"}
        out = server._handle_quick_quote_session_write(
            "s1", "confirm", {}, user={"user_id": "SM1"}, idempotency_key="confirm-1")
        self.assertEqual("invalid_workflow_state", out["code"])
        self.assertEqual("priced", out["required_state"])
        self.assertEqual("created", out["current_state"])

    def test_session_state_uses_persistent_repository_not_process_dictionary(self):
        self.assertIsNone(__import__("re").search(
            r"QUICK_QUOTE_SESSIONS\s*:\s*dict\s*=\s*\{\}", SERVER),
            "快速报价实例仍然只存在进程字典，服务重启会丢工作区")
        self.assertIn("quick_quote_session_repository", SERVER)

    def test_idempotency_store_is_persistent_not_process_dictionary(self):
        self.assertIsNone(__import__("re").search(
            r"quick_quote_idempotency\s*:\s*dict\s*=\s*\{\}", SERVER),
            "幂等记录仍然只存在进程字典，重启后重复确认会再落版本")
        self.assertIn("quick_quote_idempotency_repository", SERVER)


class QuickQuoteIdempotencyAndAclRed(unittest.TestCase):
    def test_session_create_accepts_and_enforces_idempotency_key(self):
        signature = inspect.signature(server._handle_quick_quote_session_create)
        self.assertIn("idempotency_key", signature.parameters)

    def test_frontend_reuses_operation_key_for_retry(self):
        """每次 postCommand 临时 new key 会令双击/超时重试失去幂等。"""
        post = PANEL[PANEL.index("function postCommand"):PANEL.index("function rememberCommandResult")]
        self.assertIn("operationId", post)
        self.assertNotIn("options.idempotencyKey || newIdempotencyKey(command)", post)

    def test_session_persists_owner_and_participants(self):
        create = inspect.getsource(server._handle_quick_quote_session_create)
        self.assertIn("owner_user_id", create)
        self.assertIn("participants", create)

    def test_each_read_and_write_checks_instance_acl(self):
        read_sig = inspect.signature(server._handle_quick_quote_read)
        self.assertIn("user", read_sig.parameters)
        dispatch = inspect.getsource(server._handle_quick_quote_session_write)
        self.assertIn("require_quick_quote_access", dispatch)


class QuickQuoteCardAndRecoveryRed(unittest.TestCase):
    def test_confirm_response_contains_card_summary_for_immediate_home_update(self):
        source = inspect.getsource(server._handle_quick_quote_session_confirm)
        for token in ("card", "workflow_state", "version_no"):
            self.assertIn(token, source)

    def test_frontend_confirm_updates_or_refreshes_same_home_card(self):
        confirm = HOME[HOME.index("async function runQuickQuoteConfirm"):
                       HOME.index("async function runQuickQuoteTransfer")]
        self.assertRegex(confirm, r"(upsert|refresh|reload)[A-Za-z]*Quote[A-Za-z]*Card")

    def test_read_contract_includes_full_recoverable_state(self):
        source = inspect.getsource(server._handle_quick_quote_read)
        for token in ("owner", "participants", "diff", "workflow_state", "revision",
                      "baseline", "workspace", "version_no", "quote_mode", "industry"):
            self.assertIn('"%s"' % token, source)


if __name__ == "__main__":
    unittest.main()
