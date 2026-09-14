from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TECH_HTML = (ROOT / "tech_app/frontend/tech-workbench.html").read_text(encoding="utf-8")
TECH_CSS = (ROOT / "tech_app/frontend/tech-workbench.css").read_text(encoding="utf-8")
CHAT_JS = (ROOT / "tech_app/frontend/agent-chat.js").read_bytes().replace(b"\x00", b"").decode("utf-8")
QUOTE_HTML = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")


def css_rule(source: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", source, re.S)
    if not match:
        raise AssertionError(f"缺少 CSS 规则：{selector}")
    return re.sub(r"\s+", "", match.group(1))


class TechChatComposerFlushBottomContract(unittest.TestCase):
    def test_tech_composer_has_no_caption_below_input(self):
        composer = re.search(r'<div class="oc-composer">(.*?)</div>\s*</section>', TECH_HTML, re.S)
        self.assertIsNotNone(composer)
        self.assertNotIn('class="oc-disc"', composer.group(1))
        self.assertNotIn("会话绑定当前项目", composer.group(1))

    def test_tech_composer_bottom_padding_is_zero(self):
        rule = css_rule(TECH_CSS, "#techChatPane .oc-composer")
        self.assertIn("padding:10px16px0", rule)
        self.assertIn("flex:00auto", rule)

    def test_input_box_is_last_visible_child_and_controls_remain(self):
        composer = re.search(r'<div class="oc-composer">(.*?)</div>\s*</section>', TECH_HTML, re.S)
        self.assertIsNotNone(composer)
        body = composer.group(1)
        for control_id in ("ocChatAttachBtn", "ocChatFileInput", "ocInput", "ocSend"):
            with self.subTest(control=control_id):
                self.assertIn(f'id="{control_id}"', body)
        closing_box = body.rfind("</div>")
        self.assertFalse(re.sub(r"<!--[\s\S]*?-->", "", body[closing_box + 6:]).strip())

    def test_attachment_and_send_wiring_remain(self):
        self.assertRegex(CHAT_JS, r"ocChatAttachBtn[\s\S]{0,1000}ocChatFileInput[\s\S]{0,300}\.click\(\)")
        self.assertIn('$("ocSend")', CHAT_JS)

    def test_quote_caption_is_not_removed(self):
        area_start = QUOTE_HTML.find('<div class="chat-input-area">')
        caption = QUOTE_HTML.find('<div class="oc-disc">会话绑定当前项目；右侧工作台只承载业务步骤，不重复会话。</div>')
        self.assertGreaterEqual(area_start, 0)
        self.assertGreater(caption, area_start)


if __name__ == "__main__":
    unittest.main()
