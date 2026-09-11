import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
QUOTE = ROOT / "确认需求解析结果.html"
TECH_CHAT = ROOT / "tech_app" / "frontend" / "agent-chat.css"
TECH_THEME = ROOT / "tech_app" / "frontend" / "tech-workbench.css"


def css_block(text: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", text, re.S)
    if not match:
        raise AssertionError(f"找不到 CSS 选择器：{selector}")
    return re.sub(r"\s+", " ", match.group(1)).strip()


class QuoteTechUserMessagePrimaryBubbleContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quote = QUOTE.read_text(encoding="utf-8")
        cls.tech_chat = TECH_CHAT.read_text(encoding="utf-8")
        cls.tech_theme = TECH_THEME.read_text(encoding="utf-8")
        cls.quote_user = css_block(cls.quote, ".message-user")
        cls.tech_user = css_block(cls.tech_chat, ".oc-ubub")

    def test_quote_user_bubble_uses_solid_system_primary_token(self):
        self.assertRegex(self.quote_user, r"background(?:-color)?\s*:\s*var\(--color-primary\)")
        self.assertNotRegex(self.quote_user, r"gradient|#[0-9a-fA-F]{3,8}|rgba?\(")

    def test_tech_user_bubble_uses_system_primary_semantics_not_gray(self):
        self.assertRegex(
            self.tech_user,
            r"background(?:-color)?\s*:\s*var\(--(?:color-primary|oc-accent)\)",
        )
        self.assertNotRegex(self.tech_user, r"gradient|var\(--oc-bg-[123]\)|#[0-9a-fA-F]{3,8}|rgba?\(")

    def test_tech_accent_is_only_an_alias_of_system_primary(self):
        root = css_block(self.tech_chat, ":root")
        self.assertRegex(root, r"--oc-accent\s*:\s*var\(--color-primary\)")
        self.assertRegex(self.tech_theme, r"--color-primary\s*:\s*#0060E6\b", re.I)

    def test_both_user_bubbles_have_white_text_and_right_tail(self):
        for name, block in (("quote", self.quote_user), ("tech", self.tech_user)):
            with self.subTest(surface=name):
                self.assertRegex(block, r"color\s*:\s*(?:white|#fff(?:fff)?)\b", f"{name} 用户消息不是白字")
                self.assertRegex(block, r"border-radius\s*:\s*(?!0(?:\D|$))")
                self.assertRegex(block, r"border-bottom-right-radius\s*:\s*(?!0(?:\D|$))")

    def test_user_message_alignment_remains_on_the_right(self):
        self.assertRegex(self.quote_user, r"align-self\s*:\s*flex-end")
        self.assertRegex(self.tech_user, r"align-self\s*:\s*flex-end")


if __name__ == "__main__":
    unittest.main()
