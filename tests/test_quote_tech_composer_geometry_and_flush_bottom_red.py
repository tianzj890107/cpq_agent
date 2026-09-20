"""红测：报价输入框对齐工艺规格 + 工艺输入区贴住会话列底部（实现前应失败）。

现状缺口（headless Chrome 1440×900 实测）：
  · 工艺 `#techChatPane` 只有 862px，而它所在的 grid 行是 900px —— `agent-chat.css`
    的 `.oc-agent-pane { height/max-height: calc(100vh - 38px) }` 被工艺侧的
    `#techChatPane.oc-agent-pane` 规则只覆盖了 `height`，`max-height` 仍生效，于是整列
    被压短，输入区底边停在 862px，视口底部留 38px 空白。临时把 `max-height` 改成
    `none` 后列高与输入区底边都变成 900px。
  · 报价 `.chat-input-wrapper` 是 76px / `padding:10px 12px 10px 20px` / `gap:12px` /
    textarea `15px/24px` / 附件钮 50px / 发送钮 54px；工艺 `.oc-inputbox-single` 是
    52px / `7px 8px 7px 12px` / `gap:8px` / textarea `12px/20px` / + 34px / 发送 36px。

本批只对齐几何与字号：报价按工艺的尺寸与字号改，工艺会话列撑满并把输入区贴住列底；
配色各页仍用自己的 token，功能与自增高契约不动。

静态契约按源文件逐项比对：工艺侧取值来自 `agent-chat.css` 的
`.oc-inputbox` / `.oc-inputbox-single`（后者覆盖前者），报价侧取值来自
`确认需求解析结果.html` 内联 <style>。
"""
from __future__ import annotations

import re
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_CSS = ROOT / "tech_app" / "frontend" / "agent-chat.css"
TECH_CSS = ROOT / "tech_app" / "frontend" / "tech-workbench.css"
QUOTE_HTML = ROOT / "确认需求解析结果.html"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def strip_media(css: str) -> str:
    """去掉 @media 块，只保留基础规则（响应式覆盖不算桌面基线）。"""
    out = []
    i = 0
    while True:
        m = re.search(r"@media[^{]*\{", css[i:])
        if not m:
            out.append(css[i:])
            break
        start = i + m.start()
        brace = i + m.end() - 1
        depth = 0
        j = brace
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(css[i:start])
        i = j + 1
    return "".join(out)


def rule_body(css: str, selector: str) -> str:
    """取该 selector 最后一个声明的块体（同文件后者覆盖前者）。"""
    bodies = re.findall(re.escape(selector) + r"\s*\{([^{}]*)\}", css)
    return bodies[-1] if bodies else ""


def decl(text: str, prop: str) -> str:
    if not text:
        return ""
    pattern = re.compile(r"(?:^|[;{\s])" + re.escape(prop) + r"\s*:\s*([^;}]+)")
    values = pattern.findall(text)
    return norm(values[-1]) if values else ""


def layered(first: str, second: str, prop: str) -> str:
    """second（更靠后的规则）优先，回退 first。"""
    return decl(second, prop) or decl(first, prop)


def padding_bottom(block: str) -> str:
    explicit = decl(block, "padding-bottom")
    if explicit:
        return explicit
    raw = decl(block, "padding")
    parts = raw.split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[1]
    if len(parts) == 3:
        return parts[2]
    return parts[3]


def flex_basis(block: str) -> str:
    raw = decl(block, "flex")
    parts = raw.split()
    if len(parts) == 3:
        return parts[2]
    if len(parts) == 1:
        return parts[0]
    return ""


class ComposerGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = strip_media(read(AGENT_CSS))
        cls.tech = strip_media(read(TECH_CSS))
        cls.quote_raw = read(QUOTE_HTML)
        cls.quote = strip_media(cls.quote_raw)
        # 工艺侧有效值：.oc-inputbox-single 在 .oc-inputbox 之后 → 后者优先。
        cls.tech_box_base = rule_body(cls.agent, ".oc-inputbox")
        cls.tech_box = rule_body(cls.agent, ".oc-inputbox-single")
        cls.tech_ta_base = rule_body(cls.agent, ".oc-inputbox textarea")
        cls.tech_ta = rule_body(cls.agent, ".oc-inputbox-single textarea")
        cls.tech_add = rule_body(cls.agent, ".oc-add")
        cls.tech_send = rule_body(cls.agent, ".oc-inputbox-single .oc-send")
        # 报价侧
        cls.quote_area = rule_body(cls.quote, ".chat-input-area")
        cls.quote_box = rule_body(cls.quote, ".chat-input-wrapper")
        cls.quote_ta = rule_body(cls.quote, ".chat-input")
        cls.quote_attach = rule_body(cls.quote, ".chat-attach-btn")
        cls.quote_send = rule_body(cls.quote, ".chat-send")

    def tech_box_value(self, prop: str) -> str:
        return layered(self.tech_box_base, self.tech_box, prop)

    def tech_ta_value(self, prop: str) -> str:
        return layered(self.tech_ta_base, self.tech_ta, prop)

    # ------------------------------------------------ R1 工艺会话列贴底
    def test_tech_pane_override_removes_inherited_max_height_cap(self):
        block = rule_body(self.tech, "#techChatPane.oc-agent-pane, .tech-chat-pane")
        if not block:
            block = rule_body(self.tech, ".tech-chat-pane")
        self.assertTrue(block, "找不到工艺会话列的覆盖规则")
        self.assertEqual("none", decl(block, "max-height"),
                         "工艺会话列必须覆盖 .oc-agent-pane 继承的 calc(100vh - 38px) 上限")
        self.assertEqual("100%", decl(block, "height"),
                         "工艺会话列仍要按 grid 行高撑满")
        self.assertEqual("static", decl(block, "position"),
                         "工艺会话列不该退回 sticky 小窗定位")

    def test_standalone_agent_pane_keeps_its_sticky_viewport_pane(self):
        block = rule_body(self.agent, ".oc-agent-pane")
        self.assertTrue(block, "agent-chat.css 的 .oc-agent-pane 被删除")
        self.assertEqual("calc(100vh - 38px)", decl(block, "max-height"),
                         "独立 2.1 页的 sticky 会话窗不能被改高")
        self.assertEqual("calc(100vh - 38px)", decl(block, "height"))
        self.assertEqual("sticky", decl(block, "position"))
        self.assertEqual("start", decl(block, "align-self"))

    def test_tech_grid_row_still_gives_100_percent_basis(self):
        body = rule_body(self.tech, ".tech-workbench-body")
        self.assertTrue(body, "找不到 .tech-workbench-body")
        self.assertEqual("100vh", decl(body, "height"))
        self.assertEqual("100%", decl(body, "grid-template-rows"))

    def test_input_area_sits_flush_at_the_bottom_on_both_sides(self):
        tech_composer = rule_body(self.tech, "#techChatPane .oc-composer")
        self.assertTrue(tech_composer, "找不到工艺输入区规则")
        self.assertTrue(self.quote_area, "找不到报价输入区规则")
        self.assertEqual("0 0 auto", decl(tech_composer, "flex"),
                         "工艺输入区必须是列尾固定块，才能贴住列底")
        self.assertEqual(
            padding_bottom(self.quote_area), padding_bottom(tech_composer),
            "两侧输入区底部内衬必须一致（报价 {} / 工艺 {}）".format(
                padding_bottom(self.quote_area), padding_bottom(tech_composer)))

    # ------------------------------------------------ R3 报价输入框 = 工艺规格
    def test_quote_box_padding_matches_tech(self):
        self.assertTrue(self.quote_box, "找不到报价 .chat-input-wrapper")
        self.assertEqual(self.tech_box_value("padding"), norm(decl(self.quote_box, "padding")),
                         "报价输入框内衬必须等于工艺 .oc-inputbox-single")

    def test_quote_box_gap_and_min_height_match_tech(self):
        self.assertEqual(self.tech_box_value("gap"), norm(decl(self.quote_box, "gap")),
                         "报价输入框 gap 必须等于工艺")
        self.assertEqual(self.tech_box_value("min-height"), norm(decl(self.quote_box, "min-height")),
                         "报价输入框 min-height 必须等于工艺（工艺是 0，由内容与圆钮决定高度）")

    def test_quote_box_radius_matches_tech(self):
        self.assertEqual(self.tech_box_value("border-radius"), norm(decl(self.quote_box, "border-radius")),
                         "报价输入框圆角必须等于工艺")

    def test_quote_box_keeps_flex_center_row(self):
        self.assertEqual("flex", norm(decl(self.quote_box, "display")))
        self.assertEqual("center", norm(decl(self.quote_box, "align-items")))

    def test_quote_textarea_font_matches_tech(self):
        self.assertTrue(self.quote_ta, "找不到报价 .chat-input")
        for prop in ("font-size", "line-height", "max-height", "flex", "min-width"):
            with self.subTest(prop=prop):
                self.assertEqual(
                    self.tech_ta_value(prop), norm(decl(self.quote_ta, prop)),
                    "报价输入框 %s 必须等于工艺 .oc-inputbox-single textarea" % prop)

    def test_quote_attach_button_matches_tech_circle(self):
        self.assertTrue(self.quote_attach, "找不到报价 .chat-attach-btn")
        for prop in ("width", "height", "border-radius"):
            with self.subTest(prop=prop):
                self.assertEqual(decl(self.tech_add, prop), norm(decl(self.quote_attach, prop)),
                                 "报价附件圆钮 %s 必须等于工艺 .oc-add" % prop)
        self.assertEqual(flex_basis(self.tech_add), flex_basis(self.quote_attach),
                         "报价附件圆钮 flex 基准必须等于工艺（不允许被压扁）")

    def test_quote_send_button_matches_tech_circle(self):
        self.assertTrue(self.quote_send, "找不到报价 .chat-send")
        for prop in ("width", "height", "border-radius"):
            with self.subTest(prop=prop):
                self.assertEqual(decl(self.tech_send, prop), norm(decl(self.quote_send, prop)),
                                 "报价发送圆钮 %s 必须等于工艺 .oc-inputbox-single .oc-send" % prop)
        self.assertEqual(flex_basis(self.tech_send), flex_basis(self.quote_send),
                         "报价发送圆钮 flex 基准必须等于工艺")

    def test_icon_sizes_match(self):
        tech_add_icon = rule_body(self.agent, ".oc-add")  # 图标字号在 .oc-add / .chat-attach-btn 自身
        quote_attach_icon = rule_body(self.quote, ".chat-attach-btn .ti")
        tech_send_icon = rule_body(self.agent, ".oc-inputbox-single .oc-send .ti")
        quote_send_icon = rule_body(self.quote, ".chat-send .ti")
        self.assertEqual("18px", norm(decl(tech_add_icon, "font-size")))
        self.assertEqual(norm(decl(tech_add_icon, "font-size")), norm(decl(quote_attach_icon, "font-size")),
                         "报价附件图标字号必须等于工艺")
        self.assertEqual("22px", norm(decl(tech_send_icon, "font-size")))
        self.assertEqual(norm(decl(tech_send_icon, "font-size")), norm(decl(quote_send_icon, "font-size")),
                         "报价发送图标字号必须等于工艺")

    # ------------------------------------------------ 其余契约不缩水
    def test_quote_keeps_its_own_color_tokens(self):
        self.assertIn("var(--bg-page)", self.quote_box,
                      "报价输入框要继续用本页 token，不能照抄工艺的 --oc-* 变量")
        self.assertNotIn("--oc-", self.quote_box)
        self.assertNotIn("--oc-", self.quote_ta)

    def test_both_sides_keep_the_binding_note_line(self):
        self.assertIn("#techChatPane .oc-disc", self.tech,
                      "工艺侧的会话绑定说明行被删除")
        self.assertIn('class="oc-disc"', self.quote_raw,
                      "报价侧的会话绑定说明行被删除")

    def test_no_third_geometry_variant_added(self):
        # 报价页只允许存在一份 .chat-input-wrapper / .chat-input / 两个按钮的尺寸定义
        for selector in (".chat-input-wrapper {", ".chat-input {", ".chat-attach-btn {"):
            with self.subTest(selector=selector):
                self.assertEqual(1, self.quote_raw.count(selector),
                                 "报价输入区不得出现第二套尺寸定义：%s" % selector)


if __name__ == "__main__":
    unittest.main()
