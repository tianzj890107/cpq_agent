import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "tech_app/frontend/tech-workbench.html").read_text(encoding="utf-8")
WORKBENCH_JS = (ROOT / "tech_app/frontend/tech-workbench.js").read_text(encoding="utf-8")
CHAT_JS = (ROOT / "tech_app/frontend/agent-chat.js").read_text(encoding="utf-8").replace("\x00", "")
CHAT_CSS = (ROOT / "tech_app/frontend/agent-chat.css").read_text(encoding="utf-8")

CAPABILITIES = {
    "evidence": "解析视图",
    "import3d": "导入已有 3D 模型",
    "review": "版本与校核审查",
    "modelLookup": "联网核验",
    "verify": "校验修正",
}


class TechDirectAttachmentAndChatCapabilitiesContract(unittest.TestCase):
    def test_composer_has_quote_style_attachment_button_and_hidden_multi_file_input(self):
        attach_button = re.search(r'<button[^>]+id="ocChatAttachBtn"[^>]*>(.*?)</button>', HTML, re.S)
        self.assertIsNotNone(attach_button)
        self.assertIn('aria-label="上传附件"', attach_button.group(0))
        self.assertRegex(attach_button.group(0), r'<i[^>]+ti-paperclip')
        self.assertNotIn("＋", attach_button.group(0))
        self.assertNotRegex(attach_button.group(0), r'aria-(?:haspopup|controls|expanded)')
        file_input = re.search(r'<input[^>]+id="ocChatFileInput"[^>]*>', HTML, re.S)
        self.assertIsNotNone(file_input)
        self.assertRegex(file_input.group(0), r'\btype="file"')
        self.assertRegex(file_input.group(0), r'\bmultiple\b')
        self.assertRegex(file_input.group(0), r'\bhidden\b')

    def test_attachment_click_directly_opens_file_input_without_menu_or_navigation(self):
        self.assertRegex(
            CHAT_JS + "\n" + WORKBENCH_JS,
            r'ocChatAttachBtn[\s\S]{0,1000}addEventListener\(["\']click["\'][\s\S]{0,500}ocChatFileInput[\s\S]{0,200}\.click\(\)',
        )
        direct_handler = re.search(
            r'ocChatAttachBtn[\s\S]{0,1000}addEventListener\(["\']click["\']([\s\S]{0,700})',
            CHAT_JS + "\n" + WORKBENCH_JS,
        )
        self.assertIsNotNone(direct_handler)
        self.assertNotRegex(direct_handler.group(1), r'CapabilityMenu|navigateView|openChatCapabilities')

    def test_selected_files_reuse_a_named_upload_path_and_refresh_existing_files(self):
        combined = CHAT_JS + "\n" + WORKBENCH_JS
        self.assertRegex(combined, r'ocChatFileInput[\s\S]{0,1200}addEventListener\(["\']change["\']')
        self.assertRegex(combined, r'(?:upload|attach)[A-Za-z]*(?:Files|Attachments)|(?:executeAction|dispatchDrawingCapability)\(["\'](?:upload|attachFiles)')
        self.assertRegex(combined, r'(?:loadFiles|refreshData|requestBoardSummary)\s*\(')

    def test_non_upload_capabilities_live_in_chat_action_bar(self):
        bar = re.search(r'<nav[^>]+id="techChatActions"[^>]*>(.*?)</nav>', HTML, re.S)
        self.assertIsNotNone(bar)
        for capability, label in CAPABILITIES.items():
            with self.subTest(capability=capability):
                self.assertRegex(
                    bar.group(1),
                    rf'<button[^>]+data-tech-capability="{re.escape(capability)}"[^>]*>[^<]*{re.escape(label)}',
                )

    def test_capabilities_keep_view_vs_action_dispatch_boundaries(self):
        combined = CHAT_JS + "\n" + WORKBENCH_JS
        self.assertRegex(
            combined,
            r'DRAWING_ACTION_CAPABILITIES\s*=\s*\[[^\]]*["\']modelLookup["\'][^\]]*["\']verify["\'][^\]]*\]',
        )
        self.assertRegex(
            combined,
            r'function\s+dispatchDrawingCapability[\s\S]{0,1800}DRAWING_ACTION_CAPABILITIES\.includes\(name\)'
            r'[\s\S]{0,1000}boardNavigateView\(view',
        )
        for capability in ("modelLookup", "verify"):
            with self.subTest(action=capability):
                self.assertRegex(combined, rf'executeAction\(["\']{capability}["\']')

    def test_unified_workbench_old_plus_menu_is_removed(self):
        self.assertNotIn('id="ocPlus"', HTML)
        self.assertNotIn('id="ocCapabilityMenu"', HTML)
        self.assertNotIn('role="menuitem"', HTML)
        self.assertNotRegex(CHAT_CSS, r'\.oc-capability-(?:menu|item)\b')

    def test_task_files_entry_and_existing_result_entries_remain(self):
        for token in ("ocFilesAction", "ocResultActions", "ocPartsAction", "ocQuestionsAction", "ocReportAction"):
            with self.subTest(token=token):
                self.assertIn(f'id="{token}"', HTML)


if __name__ == "__main__":
    unittest.main()
