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
    def test_binding_caption_returns_below_the_input(self):
        # 最新决策（取代第 33 批）：说明原文回到输入框下方，走普通文档流
        # （与技术工艺其它会话一致）；不得再用绝对 / 固定定位把它抽离布局。
        # composer 仍由 flex 锚定在会话列底部。
        composer = re.search(r'<div class="oc-composer">(.*?)</div>\s*</section>', TECH_HTML, re.S)
        self.assertIsNotNone(composer)
        body = composer.group(1)
        self.assertIn('class="oc-disc"', body)
        self.assertIn("会话绑定当前项目", body)
        self.assertLess(body.find("oc-inputbox"), body.find("oc-disc"), "说明行必须在输入框之后（下方）")
        caption = css_rule(TECH_CSS, "#techChatPane .oc-disc")
        self.assertNotIn("position:absolute", caption)
        self.assertNotIn("position:fixed", caption)
        self.assertIn("margin-top:9px", caption)

    def test_tech_composer_outer_padding_matches_quote(self):
        # 契约更新（报价/工艺输入框几何对齐批次）：工艺 composer 的上下内衬改成与报价
        # `.chat-input-area` 相同的 10px（原来 bottom 是 0），两侧输入区底边视觉基线一致。
        rule = css_rule(TECH_CSS, "#techChatPane .oc-composer")
        self.assertIn("padding:10px16px", rule)
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
