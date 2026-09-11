import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
QUOTE = ROOT / "确认需求解析结果.html"
TECH_CHAT = ROOT / "tech_app" / "frontend" / "agent-chat.css"


def css_block(text: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", text, re.S)
    if not match:
        raise AssertionError(f"找不到 CSS 选择器：{selector}")
    return re.sub(r"\s+", " ", match.group(1)).strip()


class QuoteTechAiMessageWhiteSurfaceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quote = QUOTE.read_text(encoding="utf-8")
        cls.tech = TECH_CHAT.read_text(encoding="utf-8")
        cls.quote_ai = css_block(cls.quote, ".message-ai")

    def test_quote_ai_message_uses_system_white_surface(self):
        self.assertRegex(self.quote_ai, r"background(?:-color)?\s*:\s*var\(--bg-page\)")
        self.assertNotRegex(self.quote_ai, r"var\(--bg-secondary\)|gradient|#[0-9a-fA-F]{3,8}|rgba?\(")

    def test_quote_ai_message_is_not_a_bordered_tail_bubble(self):
        self.assertNotRegex(self.quote_ai, r"border\s*:\s*(?!0(?:\D|$)|none\b)")
        self.assertNotRegex(self.quote_ai, r"border-bottom-left-radius\s*:")

    def test_tech_plain_ai_message_remains_without_colored_container_background(self):
        for selector in (".oc-amsg", ".oc-abody", ".oc-atxt"):
            block = css_block(self.tech, selector)
            with self.subTest(selector=selector):
                self.assertNotRegex(
                    block,
                    r"background(?:-color)?\s*:\s*(?:var\(--oc-bg-[23]\)|var\(--oc-accent\)|gradient|#[0-9a-fA-F]{3,8}|rgba?\()",
                )

    def test_ai_messages_remain_left_aligned_with_dark_text(self):
        self.assertRegex(self.quote_ai, r"align-self\s*:\s*flex-start")
        self.assertRegex(css_block(self.tech, ".oc-amsg"), r"display\s*:\s*flex")
        self.assertRegex(css_block(self.tech, ".oc-atxt"), r"color\s*:\s*var\(--oc-text-1\)")

    def test_user_message_rules_are_not_conflated_with_ai_surface(self):
        quote_user = css_block(self.quote, ".message-user")
        tech_user = css_block(self.tech, ".oc-ubub")
        self.assertRegex(quote_user, r"background(?:-color)?\s*:\s*var\(--color-primary\)")
        self.assertRegex(tech_user, r"background(?:-color)?\s*:\s*var\(--oc-accent\)")


if __name__ == "__main__":
    unittest.main()
