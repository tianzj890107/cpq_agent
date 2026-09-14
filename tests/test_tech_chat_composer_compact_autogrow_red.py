from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "tech_app/frontend/agent-chat.css").read_text(encoding="utf-8")
JS = (ROOT / "tech_app/frontend/agent-chat.js").read_bytes().replace(b"\x00", b"").decode("utf-8")
HTML = (ROOT / "tech_app/frontend/tech-workbench.html").read_text(encoding="utf-8")


def rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS, re.S)
    if not match:
        raise AssertionError(f"缺少 CSS 规则：{selector}")
    return re.sub(r"\s+", "", match.group(1))


class TechChatComposerCompactAutogrowContract(unittest.TestCase):
    def test_attachment_button_is_two_thirds_size_but_icon_unchanged(self):
        button = rule(".oc-add")
        self.assertIn("width:34px", button)
        self.assertIn("height:34px", button)
        self.assertIn("flex:0034px", button)
        self.assertIn("font-size:18px", button)

    def test_send_button_is_two_thirds_size_but_icon_unchanged(self):
        button = rule(".oc-inputbox-single .oc-send")
        icon = rule(".oc-inputbox-single .oc-send .ti")
        self.assertIn("width:36px", button)
        self.assertIn("height:36px", button)
        self.assertIn("flex:0036px", button)
        self.assertIn("font-size:22px", icon)

    def test_text_matches_action_button_typography(self):
        textarea = rule(".oc-inputbox-single textarea")
        self.assertIn("font-size:12px", textarea)
        self.assertIn("line-height:20px", textarea)

    def test_shell_has_content_driven_single_line_initial_height(self):
        shell = rule(".oc-inputbox-single")
        self.assertNotIn("min-height:76px", shell)
        self.assertIn("min-height:0", shell)
        self.assertRegex(HTML, r'<textarea[^>]+id="ocInput"[^>]+rows="1"')

    def test_autosize_grows_to_120_and_switches_overflow(self):
        body = re.search(
            r"function\s+autoSize\s*\([^)]*\)\s*\{([\s\S]*?)(?=\n\s*\}\s*\n\s*\n)",
            JS,
        )
        self.assertIsNotNone(body)
        code = body.group(1)
        self.assertIn('input.style.height = "auto"', code)
        self.assertRegex(code, r"contentHeight\s*=\s*input\.scrollHeight")
        self.assertRegex(code, r"Math\.min\(contentHeight,\s*120\)")
        self.assertRegex(code, r"input\.style\.overflowY\s*=\s*[^;]*120")
        self.assertIn('input.addEventListener("input", autoSize)', JS)
        self.assertRegex(JS, r'input\.value\s*=\s*"";\s*autoSize\(\)')

    def test_interaction_contract_is_preserved(self):
        self.assertIn('id="ocChatAttachBtn"', HTML)
        self.assertIn('id="ocChatFileInput"', HTML)
        self.assertIn('id="ocSend"', HTML)
        self.assertRegex(JS, r'event\.key\s*===\s*"Enter"\s*&&\s*!event\.shiftKey')
        self.assertRegex(JS, r"event\.isComposing\s*\|\|\s*event\.keyCode\s*===\s*229")


if __name__ == "__main__":
    unittest.main()
