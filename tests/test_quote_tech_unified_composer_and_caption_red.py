"""红测：报价输入框统一为技术工艺单行样式、技术 ＋ 去掉回形针角标、恢复输入框下方说明行。

现状缺口：
  · 报价 `确认需求解析结果.html` 的 `.chat-input-wrapper` 仍是旧多行样式（38×38 方形回形针、
    2 行 textarea、38×38 方形发送），没有技术工艺 `.oc-inputbox-single` 的单行圆角造型；
  · 技术 `tech-workbench.html` 的 `#ocChatAttachBtn` 除 `ti-plus` 外还挂了一个
    `ti-paperclip oc-add-mark` 角标，`agent-chat.css` 还留着 `.oc-add .oc-add-mark` 规则与
    「契约要求保留这个字形」注释；
  · 两侧输入框下方的 `.oc-disc`「会话绑定当前项目…」说明行都不存在，底部因此空出一块。

本批只改输入区 DOM/CSS 与说明行；不改消息渲染、接口、Agent 工具、九阶段流程与右侧看板。
不联网、不起服务、不读真实业务数据；只做静态契约校验。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUOTE = ROOT / "确认需求解析结果.html"
TECH_HTML = ROOT / "tech_app/frontend/tech-workbench.html"
CHAT_CSS = ROOT / "tech_app/frontend/agent-chat.css"
CHAT_JS = ROOT / "tech_app/frontend/agent-chat.js"
WORKBENCH_JS = ROOT / "tech_app/frontend/tech-workbench.js"
WORKBENCH_CSS = ROOT / "tech_app/frontend/tech-workbench.css"

CAPTION = "会话绑定当前项目；右侧工作台只承载业务步骤，不重复会话。"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def css_rule(css: str, selector: str) -> str:
    """取 `selector { ... }` 的声明块；要求选择器后紧跟可选空白与 `{`，避免误配同前缀选择器。"""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return match.group(1) if match else ""


def button_markup(html: str, button_id: str) -> str:
    match = re.search(
        r'<button[^>]*id="%s"[^>]*>(.*?)</button>' % re.escape(button_id),
        html,
        re.S,
    )
    return match.group(0) if match else ""


class QuoteTechUnifiedComposerRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quote = _read(QUOTE)
        cls.tech_html = _read(TECH_HTML)
        cls.chat_css = _read(CHAT_CSS)
        cls.chat_js = _read(CHAT_JS)
        cls.workbench_js = _read(WORKBENCH_JS)
        cls.workbench_css = _read(WORKBENCH_CSS)

    # ------------------------------------------------- 报价：单行统一输入框
    def test_quote_input_row_adopts_single_line_rounded_box(self):
        wrapper = compact(css_rule(self.quote, ".chat-input-wrapper"))
        self.assertTrue(wrapper, "找不到 .chat-input-wrapper 规则")
        for token in (
            "display:flex",
            "align-items:center",
            "gap:12px",
            "min-height:76px",
            "border-radius:24px",
        ):
            with self.subTest(token=token):
                self.assertIn(token, wrapper, "报价输入行未采用技术工艺单行圆角造型：缺 %s" % token)

    def test_quote_input_row_matches_tech_geometry(self):
        quote = compact(css_rule(self.quote, ".chat-input-wrapper"))
        tech = compact(css_rule(self.chat_css, ".oc-inputbox-single"))
        self.assertTrue(tech, "找不到技术工艺 .oc-inputbox-single 规则")
        for prop in ("min-height:", "border-radius:", "gap:"):
            with self.subTest(prop=prop):
                q = re.search(re.escape(prop) + r"([^;]+)", quote)
                t = re.search(re.escape(prop) + r"([^;]+)", tech)
                self.assertIsNotNone(q, "报价输入行缺 %s" % prop)
                self.assertIsNotNone(t, "技术工艺输入行缺 %s" % prop)
                self.assertEqual(q.group(1), t.group(1), "两侧输入行 %s 数值不一致" % prop)

    def test_quote_attach_button_is_round_plus_without_paperclip(self):
        markup = button_markup(self.quote, "chatAttachBtn")
        self.assertTrue(markup, "找不到 #chatAttachBtn")
        self.assertRegex(markup, r"ti-plus", "报价附件按钮应改为 ＋ 字形")
        self.assertNotRegex(markup, r"ti-paperclip", "报价输入框内不应再出现回形针角标")
        self.assertNotRegex(markup, r"oc-add-mark", "报价输入框不应引入技术工艺的回形针角标类")

        rule = compact(css_rule(self.quote, ".chat-attach-btn"))
        self.assertIn("width:50px", rule)
        self.assertIn("height:50px", rule)
        self.assertIn("border-radius:50%", rule)

    def test_quote_send_button_is_round_primary(self):
        rule = compact(css_rule(self.quote, ".chat-send"))
        self.assertIn("width:54px", rule)
        self.assertIn("height:54px", rule)
        self.assertIn("border-radius:50%", rule)
        self.assertIn("var(--color-primary)", rule)

    def test_quote_textarea_single_row_and_restyled(self):
        textarea = re.search(r"<textarea[^>]*id=\"chatInput\"[^>]*>", self.quote, re.S)
        self.assertIsNotNone(textarea, "找不到 #chatInput")
        self.assertRegex(textarea.group(0), r'rows="1"', "报价输入框应改为单行")

        rule = compact(css_rule(self.quote, ".chat-input"))
        self.assertIn("font-size:15px", rule)
        self.assertIn("line-height:24px", rule)
        self.assertTrue(
            "border:0;" in rule or "border:none" in rule,
            "单行输入框内部 textarea 必须去边框：%s" % rule,
        )
        self.assertIn("flex:1", rule)

    def test_quote_input_wiring_unchanged(self):
        self.assertRegex(
            self.quote,
            r"chatAttachBtn'\)\.onclick\s*=\s*\(\)\s*=>\s*\$\('chatFileInput'\)\.click\(\)",
            "报价附件按钮仍须直接打开隐藏文件输入框",
        )
        file_input = re.search(r"<input[^>]+id=\"chatFileInput\"[^>]*>", self.quote, re.S)
        self.assertIsNotNone(file_input, "找不到 #chatFileInput")
        for token in ('type="file"', "multiple", "hidden"):
            with self.subTest(token=token):
                self.assertIn(token, file_input.group(0))
        self.assertRegex(self.quote, r'id="chatSend"[^>]*onclick="sendFromInput\(\)"')
        self.assertRegex(
            self.quote,
            r"chatInput'\)\.addEventListener\('keydown'",
            "Enter 发送 / Shift+Enter 换行监听不能丢",
        )

    def test_quote_quick_actions_and_attach_chips_kept(self):
        self.assertIn('id="quickActions"', self.quote)
        for button_id in ("qaFillStep", "qaStep", "qaPrev", "qaSend"):
            with self.subTest(button=button_id):
                self.assertIn('id="%s"' % button_id, self.quote)
        self.assertIn('id="attachChips"', self.quote)

    # ------------------------------------------------- 技术工艺 ＋ 去角标
    def test_tech_add_button_has_no_paperclip_badge(self):
        markup = button_markup(self.tech_html, "ocChatAttachBtn")
        self.assertTrue(markup, "找不到 #ocChatAttachBtn")
        self.assertRegex(markup, r"ti-plus", "＋ 主字形必须保留")
        self.assertNotRegex(markup, r"ti-paperclip", "＋ 右上角的回形针角标必须删除")
        self.assertNotRegex(markup, r"oc-add-mark", "回形针角标节点必须删除，不能只改样式")

    def test_tech_add_mark_css_and_stale_comment_removed(self):
        self.assertNotIn(".oc-add .oc-add-mark", self.chat_css, "回形针角标死 CSS 必须删除")
        self.assertNotIn("oc-add-mark", self.chat_css)
        self.assertNotIn(
            "契约要求保留这个字形",
            self.chat_css,
            "描述必须保留角标的过时注释必须删掉或改写",
        )

    def test_tech_add_button_still_opens_file_input(self):
        rule = compact(css_rule(self.chat_css, ".oc-add"))
        self.assertIn("width:50px", rule)
        self.assertIn("height:50px", rule)
        self.assertIn("border-radius:50%", rule)
        combined = self.chat_js + "\n" + self.workbench_js
        self.assertRegex(
            combined,
            r"ocChatAttachBtn[\s\S]{0,1000}ocChatFileInput[\s\S]{0,200}\.click\(\)",
            "＋ 仍须直接打开隐藏文件输入框",
        )

    # ------------------------------------------------- 恢复说明行
    def test_tech_composer_restores_binding_caption(self):
        composer = re.search(r'<div class="oc-composer">(.*?)</div>\s*</section>', self.tech_html, re.S)
        self.assertIsNotNone(composer, "找不到技术工艺 .oc-composer 区块")
        body = composer.group(1)
        self.assertIn('class="oc-disc"', body, "技术工艺 composer 必须恢复 .oc-disc 说明行")
        self.assertIn(CAPTION, body, "说明行文案必须与既有口径一致")
        self.assertLess(
            body.find("oc-inputbox-single"),
            body.find("oc-disc"),
            "说明行必须在输入框下方（DOM 顺序在外层输入框之后）",
        )

    def test_quote_composer_has_same_binding_caption(self):
        area = re.search(r'<div class="chat-input-area">(.*?)\n      </div>', self.quote, re.S)
        self.assertIsNotNone(area, "找不到报价 .chat-input-area 区块")
        body = area.group(1)
        self.assertIn('class="oc-disc"', body, "报价输入区必须补上同一句说明行")
        self.assertIn(CAPTION, body)
        self.assertLess(
            body.find("chat-input-wrapper"),
            body.find("oc-disc"),
            "报价说明行必须在输入行下方",
        )

    def test_caption_stays_in_normal_flow_with_shared_style(self):
        for name, css in (("技术工艺", self.chat_css), ("报价", self.quote)):
            rule = compact(css_rule(css, ".oc-disc"))
            with self.subTest(side=name):
                self.assertTrue(rule, "%s 缺 .oc-disc 样式" % name)
                self.assertIn("font-size:11px", rule)
                self.assertIn("text-align:center", rule)
                self.assertIn("margin-top:9px", rule)
                self.assertNotIn("position:absolute", rule, "说明行不能脱离文档流，否则撑不出高度")
                self.assertNotIn("position:fixed", rule)

    # ------------------------------------------------- 底部锚定守卫
    def test_both_composers_keep_outer_padding_and_bottom_anchor(self):
        quote_area = compact(css_rule(self.quote, ".chat-input-area"))
        self.assertIn("padding:10px16px", quote_area)
        self.assertIn("flex-shrink:0", quote_area)

        tech_padding = re.search(r"\.oc-composer\s*\{[^}]*padding:\s*([^;]+);", self.chat_css, re.S)
        self.assertIsNotNone(tech_padding, "找不到 .oc-composer padding")
        self.assertEqual("10px 16px", " ".join(tech_padding.group(1).split()))

        tech_composer = re.search(
            r"#techChatPane \.oc-composer\s*\{([^}]*)\}", self.workbench_css, re.S
        )
        self.assertIsNotNone(tech_composer)
        self.assertIn("flex: 0 0 auto", tech_composer.group(1))

    def test_global_model_settings_entries_kept(self):
        self.assertIn('id="techModelInfo"', self.tech_html)
        self.assertIn('id="modelInfo"', self.quote)
        self.assertNotIn("ocModelSelect", self.tech_html)
        self.assertNotIn("ocModelSelect", self.chat_js)
        self.assertNotIn("ocModelSelect", self.workbench_js)


if __name__ == "__main__":
    unittest.main()
