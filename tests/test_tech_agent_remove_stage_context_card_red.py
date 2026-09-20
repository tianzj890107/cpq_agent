import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WB_JS = (ROOT / "tech_app/frontend/tech-workbench.js").read_text(encoding="utf-8")
WB_HTML = (ROOT / "tech_app/frontend/tech-workbench.html").read_text(encoding="utf-8")
CHAT_JS = (ROOT / "tech_app/frontend/agent-chat.js").read_bytes().replace(b"\x00", b"").decode("utf-8")
CHAT_CSS = (ROOT / "tech_app/frontend/agent-chat.css").read_text(encoding="utf-8")
REQ_JS = (ROOT / "tech_app/frontend/requirement-create.js").read_text(encoding="utf-8")


class TechAgentRemoveStageContextCardContract(unittest.TestCase):
    def test_agent_chat_no_longer_creates_stage_context_card(self):
        for token in ("ocStageContext", "oc-stage-context", "contextHost", "renderStageContext"):
            with self.subTest(token=token):
                self.assertIsNone(re.search(re.escape(token), CHAT_JS), token)

    def test_stage_card_specific_styles_are_removed(self):
        self.assertNotRegex(CHAT_CSS, r"\.oc-stage-context(?:[-\s:{])")

    def test_stage_card_action_event_channel_is_removed(self):
        self.assertNotIn("cpq:tech-agent:stage-action", CHAT_JS)
        self.assertNotIn("cpq:tech-agent:stage-action", WB_JS)

    def test_parent_context_payload_no_longer_builds_visible_card_actions(self):
        body = re.search(
            r"function\s+stageAgentContext\s*\([^)]*\)\s*\{([\s\S]*?)(?=\n\s*function\s+syncAgentStageContext)",
            WB_JS,
        )
        self.assertIsNotNone(body)
        self.assertNotIn("context.actions", body.group(1))
        self.assertNotRegex(body.group(1), r"\bactions\s*[,}:]")

    def test_nine_stage_page_context_remains_available_to_agent_requests(self):
        block = re.search(r"STAGE_AGENT_CONTEXT\s*=\s*\{([\s\S]*?)\n\s*\};", WB_JS)
        self.assertIsNotNone(block)
        values = re.findall(r"pageContext\s*:\s*['\"]([^'\"]+)", block.group(1))
        self.assertEqual(9, len(values))
        self.assertEqual(9, len(set(values)))
        self.assertRegex(CHAT_JS, r"function\s+currentPageContext\s*\(")
        self.assertIn("currentPageContext()", CHAT_JS)

    def test_unified_chat_action_bar_and_board_action_routing_remain(self):
        self.assertIn('id="techChatActions"', WB_HTML)
        # 契约更新（「去掉通用刷新与导航按钮」批次）：父壳不再持有 STAGE_ACTIONS 动作表 ——
        # 阶段只由 STAGES 描述表登记，动作全部来自看板注册表与动作快照。
        self.assertIn("const STAGES", WB_JS)
        self.assertIn("boardActionEntries", WB_JS)
        self.assertIn("techChatPrimary", WB_JS)
        self.assertIn("TechBoardBridge", WB_JS)
        self.assertIn("executeAction", WB_JS)

    def test_requirement_board_keeps_real_save_and_submit_controls(self):
        self.assertIn('id="saveDraft"', REQ_JS)
        self.assertIn('id="submitRequirement"', REQ_JS)
        self.assertIn("submit-confirmation", REQ_JS)


if __name__ == "__main__":
    unittest.main()
