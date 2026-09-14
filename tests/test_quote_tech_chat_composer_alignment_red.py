"""Red contracts for quote/tech composer model removal and bottom alignment."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_bytes().replace(b"\x00", b"").decode("utf-8")


def block(source: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", source, re.S)
    if not match:
        raise AssertionError(f"missing CSS selector: {selector}")
    return match.group(1)


class QuoteTechComposerAlignmentRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quote = read_text("确认需求解析结果.html")
        cls.tech_html = read_text("tech_app/frontend/tech-workbench.html")
        cls.chat_css = read_text("tech_app/frontend/agent-chat.css")
        cls.workbench_css = read_text("tech_app/frontend/tech-workbench.css")
        cls.chat_js = read_text("tech_app/frontend/agent-chat.js")
        cls.workbench_js = read_text("tech_app/frontend/tech-workbench.js")

    def test_tech_composer_contains_attachment_input_and_send_but_no_model_control(self):
        composer = re.search(
            r'<div class="oc-inputbox oc-inputbox-single">(.*?)</div>',
            self.tech_html,
            re.S,
        )
        self.assertIsNotNone(composer, "统一技术工艺输入框必须保留")
        markup = composer.group(1)
        for control_id in ("ocChatAttachBtn", "ocChatFileInput", "ocInput", "ocSend"):
            with self.subTest(control=control_id):
                self.assertIn(f'id="{control_id}"', markup)
        self.assertNotIn("ocModelSelect", markup)
        self.assertNotIn("ocModelSelectLabel", markup)
        self.assertNotRegex(markup, r"大语言模型|切换.*模型")

    def test_removed_model_control_is_not_kept_as_dead_css_or_javascript(self):
        self.assertNotIn(".oc-model-select", self.chat_css)
        self.assertNotIn("ocModelSelect", self.chat_js)
        self.assertNotIn("ocModelSelectLabel", self.chat_js)
        self.assertNotIn("ocModelSelect", self.workbench_js)
        self.assertNotIn("ocModelSelectLabel", self.workbench_js)

    def test_tech_composer_keeps_binding_caption_without_moving_input(self):
        # 第 33 批反转：技术工艺说明行回归，但改为绝对定位的不占布局高度提示；
        # 输入框底边坐标不变（#techChatPane .oc-composer 仍是 padding: 10px 16px 0）。
        composer = re.search(
            r'<div class="oc-composer">(.*?)</div>\s*</section>',
            self.tech_html,
            re.S,
        )
        self.assertIsNotNone(composer)
        self.assertIn("oc-disc", composer.group(1))
        self.assertIn("会话绑定当前项目", composer.group(1))
        caption = block(self.workbench_css, "#techChatPane .oc-disc")
        self.assertRegex(caption, r"position:\s*absolute")
        self.assertNotRegex(caption, r"margin-bottom:\s*[1-9]")

    def test_quote_and_tech_composer_use_the_same_outer_padding(self):
        quote_padding = re.search(r"\.chat-input-area\s*\{[^}]*padding:\s*([^;]+);", self.quote, re.S)
        self.assertIsNotNone(quote_padding)
        self.assertEqual("10px 16px", " ".join(quote_padding.group(1).split()))

        tech_padding = re.search(r"\.oc-composer\s*\{[^}]*padding:\s*([^;]+);", self.chat_css, re.S)
        self.assertIsNotNone(tech_padding)
        self.assertEqual(
            "10px 16px",
            " ".join(tech_padding.group(1).split()),
            "技术工艺不得用额外 bottom padding 把输入框向上顶",
        )

    def test_both_composers_remain_bottom_anchored_by_flex_layout(self):
        quote_panel = block(self.quote, ".chat-panel")
        quote_composer = block(self.quote, ".chat-input-area")
        tech_panel = block(self.workbench_css, "#techChatPane.oc-agent-pane,\n.tech-chat-pane")
        tech_thread = block(self.workbench_css, "#techChatPane .oc-thread")
        tech_composer = block(self.workbench_css, "#techChatPane .oc-composer")

        self.assertRegex(quote_panel, r"display:\s*flex")
        self.assertRegex(quote_panel, r"flex-direction:\s*column")
        self.assertRegex(quote_panel, r"height:\s*100vh")
        self.assertRegex(quote_composer, r"flex-shrink:\s*0")
        self.assertRegex(tech_panel, r"display:\s*flex")
        self.assertRegex(tech_panel, r"flex-direction:\s*column")
        self.assertRegex(tech_panel, r"height:\s*100%")
        self.assertRegex(tech_thread, r"flex:\s*1\s+1\s+auto")
        self.assertRegex(tech_thread, r"min-height:\s*0")
        self.assertRegex(tech_composer, r"flex:\s*0\s+0\s+auto")
        self.assertRegex(tech_composer, r"padding:\s*10px\s+16px\s+0")

    def test_global_model_settings_entries_remain_available(self):
        self.assertIn('id="techModelInfo"', self.tech_html)
        self.assertRegex(self.tech_html, r"模型与参数设置")
        self.assertRegex(self.tech_html, r'id="techModelSettingsMask"|id="techModelSettings"')
        self.assertIn('id="modelInfo"', self.quote)
        self.assertRegex(self.quote, r'onclick="openSettings\(\)"')
        self.assertRegex(self.quote, r'id="settingsModal"|id="settingsOverlay"')


if __name__ == "__main__":
    unittest.main()
