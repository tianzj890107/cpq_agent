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
        # 契约更新（「报价 / 技术工艺助手气泡统一为白底 + 气泡边框」批次）：用户明确要求助手
        # 消息用白色气泡背景并保留气泡边框；仍不得用灰底 / 次级底色 / 渐变 / 半透明容器。
        self.assertRegex(self.quote_ai, r"background(?:-color)?\s*:\s*(?:#fff(?:fff)?|var\(--bg-page\))")
        self.assertNotRegex(self.quote_ai, r"var\(--bg-secondary\)|gradient|rgba?\(")

    def test_quote_ai_message_is_a_bordered_bubble_without_tail(self):
        # 气泡边框是用户明确要求保留的（「要有气泡边框，只是背景都保留是白色背景」）；
        # 这里只禁止「带尾巴」的对话气泡（左下角小尖角）。
        self.assertRegex(self.quote_ai, r"border\s*:\s*1px\s+solid")
        self.assertNotRegex(self.quote_ai, r"border-bottom-left-radius\s*:")

    def test_tech_plain_ai_message_keeps_white_surface_without_colored_container(self):
        # 契约更新：技术侧助手消息同样改成白色气泡背景 + 气泡边框（与报价同款），
        # 但仍不得出现灰底 / 强调色 / 渐变这类「带底色的容器」。
        self.assertRegex(css_block(self.tech, ".oc-amsg"),
                         r"background(?:-color)?\s*:\s*(?:#fff(?:fff)?|var\(--oc-bg-1\))")
        for selector in (".oc-amsg", ".oc-abody", ".oc-atxt"):
            block = css_block(self.tech, selector)
            with self.subTest(selector=selector):
                self.assertNotRegex(
                    block,
                    r"background(?:-color)?\s*:\s*(?:var\(--oc-bg-[23]\)|var\(--oc-accent\)|gradient|rgba?\()",
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
