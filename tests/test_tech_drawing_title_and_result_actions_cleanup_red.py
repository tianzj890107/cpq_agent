from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_bytes().replace(b"\x00", b"").decode("utf-8")


HTML = read("tech_app/frontend/tech-workbench.html")
CSS = read("tech_app/frontend/tech-workbench.css")
WB_JS = read("tech_app/frontend/tech-workbench.js")
CHAT_JS = read("tech_app/frontend/agent-chat.js")
EMBED_JS = read("tech_app/frontend/tech-embed.js")


class TechDrawingTitleAndResultActionsCleanupContract(unittest.TestCase):
    def test_context_header_has_stage_independent_height(self):
        rule = re.search(r"\.tech-workspace-context\s*\{([^}]*)\}", CSS, re.S)
        self.assertIsNotNone(rule)
        self.assertRegex(rule.group(1), r"min-height:\s*(?:4[4-9]|[5-9]\d)px")
        self.assertRegex(rule.group(1), r"align-items:\s*center")

    def test_drawing_title_and_real_board_status_share_parent_row(self):
        self.assertRegex(WB_JS, r"title:\s*['\"]图纸解析['\"]")
        self.assertIn("techContextTitle", WB_JS)
        self.assertIn("techContextNotice", WB_JS)
        handler = re.search(r"if\s*\(type\s*===\s*['\"]board-status['\"]\)\s*\{([\s\S]*?)\n\s*\}", WB_JS)
        self.assertIsNotNone(handler)
        self.assertIn("payload.text", handler.group(1))
        self.assertNotIn("42058acfab83", WB_JS)

    def test_embedded_child_title_section_is_removed_from_layout(self):
        self.assertRegex(EMBED_JS, r"\.tech-embed \.title-section\s*\{\s*display:\s*none\s*!important;\s*\}")
        self.assertNotIn(".tech-embed .title-section .form-title", EMBED_JS)
        self.assertIn("board-status", EMBED_JS)

    def test_parts_result_button_and_result_strip_are_deleted(self):
        for node_id in ("ocResultActions", "ocPartsAction", "ocPartsCount"):
            with self.subTest(node_id=node_id):
                self.assertNotIn(f'id="{node_id}"', HTML)
        self.assertNotIn("ocPartsAction", CHAT_JS)
        self.assertNotIn("ocResultActions", CHAT_JS)

    def test_questions_and_report_are_in_action_toolbar(self):
        toolbar = re.search(r'<nav class="tech-chat-actions"[^>]*id="techChatActions"[^>]*>([\s\S]*?)</nav>', HTML)
        self.assertIsNotNone(toolbar)
        body = toolbar.group(1)
        self.assertIn('id="ocQuestionsAction"', body)
        self.assertIn('id="ocQuestionsCount"', body)
        self.assertIn("待澄清问题", body)
        self.assertIn('id="ocReportAction"', body)
        self.assertIn("解析报告", body)

    def test_zero_question_count_is_preserved(self):
        self.assertRegex(HTML, r'id="ocQuestionsCount"[^>]*>0</span>')
        self.assertRegex(CHAT_JS, r'ocQuestionsCount[\s\S]{0,900}(?:count|questions)')

    def test_remaining_result_actions_keep_board_view_mapping(self):
        self.assertRegex(CHAT_JS, r"ocQuestionsAction:\s*['\"]questions['\"]")
        self.assertRegex(CHAT_JS, r"ocReportAction:\s*['\"]report['\"]")
        self.assertNotRegex(CHAT_JS, r"ocPartsAction:\s*['\"]parts['\"]")
        self.assertIn("navigateView", CHAT_JS)

    def test_result_summary_parts_contract_is_not_deleted(self):
        drawing = read("tech_app/frontend/app.js")
        self.assertRegex(drawing, r"parts:\s*\{\s*available:")
        self.assertRegex(drawing, r"questions:\s*\{\s*available:")
        self.assertRegex(drawing, r"report:\s*\{\s*available:")

    def test_status_notice_remains_single_line_and_error_capable(self):
        notice = re.search(r"\.tech-context-notice\s*\{([^}]*)\}", CSS, re.S)
        self.assertIsNotNone(notice)
        self.assertRegex(notice.group(1), r"white-space:\s*nowrap")
        self.assertRegex(notice.group(1), r"text-overflow:\s*ellipsis")
        self.assertIn(".tech-context-notice.is-error", CSS)


if __name__ == "__main__":
    unittest.main()
