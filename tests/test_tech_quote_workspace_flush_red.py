"""红测：报价 / 工艺工作区去卡片、工艺标题行收窄、工艺嵌入态去灰底。

用户口径（三条）：

1. 报价和工艺工作区都用了圆角卡片把内容包起来 —— 不需要，直接铺满整个工作区；
2. 工艺的标题行（`组装与整合` + `就绪` + `整合图纸 / 参数推荐 / 组装工艺`）太厚，要窄很多；
3. 工作区里那层卡片外面的灰底取消、它四周的外边距减半 —— 用户已确认**只改技术工艺**
   （报价侧没有那圈灰）。

现状（实测，非推断）：

  · 报价 `确认需求解析结果.html:344` 的 `.results-area` 是 `margin:var(--space-lg)` +
    `border:.5px` + `border-radius:var(--radius-lg)` 的圆角卡片；
  · 工艺 `tech_app/frontend/tech-workbench.css:337` 的 `.tech-results-area` 是 `margin:16px` +
    `.5px` 边框 + `12px` 圆角的卡片（响应式 `:947` 12px / `:989` 8px）；
  · `tech-workbench.css:546` 的 `.tech-workspace-context` 是 `min-height:52px` + `padding:8px 16px`；
  · 嵌入态阶段页的灰底来自 `tech_app/frontend/workbench.css:12` 的 `body{background:var(--bg-page)}`，
    四周间距来自 `.page-container` 的 `var(--space-xl)`（20px）与 `tech-embed.js:150` 的左右 18px。

Spec：docs/specs/tech-quote-workspace-flush-and-compact-stage-title-row.md
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
QUOTE_HTML = ROOT / "确认需求解析结果.html"
WB_HTML = FRONTEND / "tech-workbench.html"
WB_CSS = FRONTEND / "tech-workbench.css"
WB_BASE_CSS = FRONTEND / "workbench.css"
EMBED_JS = FRONTEND / "tech-embed.js"
SPEC = ROOT / "docs" / "specs" / "tech-quote-workspace-flush-and-compact-stage-title-row.md"

HEAD = 52          # 标题行现在的高度
TAIL = 34          # 标题行应该收到的高度
SIDE_BEFORE = 18   # 嵌入态阶段页现在的左右内边距
SIDE_AFTER = 9     # 减半后
TOP_BEFORE = 20    # 嵌入态阶段页现在的上下内边距（var(--space-xl)）
TOP_AFTER = 10


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def declarations(css: str):
    """按出现顺序返回 (选择器, 声明串)，两侧空白已规整。

    先去掉 CSS 注释，再按花括号深度切分：`@media` 这类 at-rule 只当包装层丢弃，
    它内部真正的规则照常产出 —— 直接写正则会被注释文本和嵌套花括号带偏。
    选择器可能是逗号分组（`.tech-embed, .tech-embed body { … }`），调用方按逗号拆开比对。
    """
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    pairs = []
    stack = []
    buf_start = 0
    for index, char in enumerate(text):
        if char == "{":
            stack.append((text[buf_start:index].strip(), index + 1))
            buf_start = index + 1
        elif char == "}":
            if stack:
                selector, body_start = stack.pop()
                if not selector.startswith("@"):
                    pairs.append((selector, text[body_start:index]))
            buf_start = index + 1
    return [(re.sub(r"\s+", " ", selector).strip(), re.sub(r"\s+", "", body))
            for selector, body in pairs]


def rules_for(css: str, selector: str):
    """选择器完全相等的规则（含逗号分组里的一项）全部返回。"""
    wanted = re.sub(r"\s+", " ", selector).strip()
    return [body for sel, body in declarations(css)
            if any(part.strip() == wanted for part in sel.split(","))]


def rule(css: str, selector: str) -> str:
    found = rules_for(css, selector)
    if not found:
        raise AssertionError("缺少 CSS 规则：%s" % selector)
    return found[0]


def joined(css: str, selector: str) -> str:
    """同一选择器的多条规则拼在一起看（分组选择器、重复声明都能覆盖到）。"""
    return "".join(rules_for(css, selector))


def embed_css() -> str:
    """把 tech-embed.js 注入的那段 style 拼回 CSS 文本。"""
    src = read(EMBED_JS)
    start = src.find("style.textContent = [")
    if start < 0:
        raise AssertionError("tech-embed.js 里找不到注入样式块")
    end = src.find("].join(", start)
    if end < 0:
        raise AssertionError("tech-embed.js 注入样式块结尾找不到 .join(")
    return "\n".join(re.findall(r"'([^']*)'", src[start:end]))


class SpecPinsTheContractTest(unittest.TestCase):
    def test_01_spec_pins_the_contract(self):
        spec = read(SPEC)
        self.assertTrue(spec, "缺少 docs/specs/tech-quote-workspace-flush-and-compact-stage-title-row.md")
        for token in ("results-area", "tech-results-area", "tech-workspace-context",
                      "tech-embed", "34px", "9px", "margin", "border-radius"):
            with self.subTest(token=token):
                self.assertIn(token, spec, "spec 未钉住 %s" % token)


# --------------------------------------------------------------------------- #
# 报价工作区：结果区铺满
# --------------------------------------------------------------------------- #
class QuoteWorkspaceFlushTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read(QUOTE_HTML)

    def test_10_results_area_is_no_longer_a_rounded_card(self):
        area = rule(self.html, ".results-area")
        self.assertIn("margin:0", area,
                      "报价结果区还留着外边距：%s" % area)
        self.assertNotRegex(area, r"border:\.?5px|border:1px",
                            "报价结果区还留着边框：%s" % area)
        self.assertIn("border-radius:0", area,
                      "报价结果区还留着圆角：%s" % area)

    def test_11_results_area_keeps_its_flex_shell(self):
        area = rule(self.html, ".results-area")
        for declaration in ("display:flex", "flex-direction:column", "flex:1",
                            "overflow:hidden", "background:var(--bg-page)"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, area,
                              "去卡片顺手改坏了结果区自身的排版：%s" % area)
        self.assertNotRegex(area, r"box-shadow:(?!none)")

    def test_12_right_panel_and_inner_rows_are_untouched(self):
        panel = rule(self.html, ".right-panel")
        self.assertIn("background:var(--bg-page)", panel,
                      "用户已确认报价侧没有那圈灰，右栏底色不许改：%s" % panel)
        for selector, declaration in ((".results-header", "border-bottom"),
                                      (".results-content", "padding:var(--space-lg)"),
                                      (".bottom-bar", "border-top")):
            with self.subTest(selector=selector):
                self.assertIn(declaration, rule(self.html, selector),
                              "去卡片不应改 %s 的内部排版" % selector)


# --------------------------------------------------------------------------- #
# 工艺工作区：结果区铺满
# --------------------------------------------------------------------------- #
class TechWorkspaceFlushTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = read(WB_CSS)
        cls.html = read(WB_HTML)

    def test_20_results_area_is_no_longer_a_rounded_card(self):
        area = rule(self.css, ".tech-results-area")
        self.assertIn("margin:0", area, "工艺结果卡还留着外边距：%s" % area)
        self.assertNotRegex(area, r"border:\.?5px|border:1px",
                            "工艺结果卡还留着边框：%s" % area)
        self.assertIn("border-radius:0", area, "工艺结果卡还留着圆角：%s" % area)

    def test_21_results_area_keeps_its_surface_and_flex_shell(self):
        area = rule(self.css, ".tech-results-area")
        for declaration in ("display:flex", "flex-direction:column", "flex:11auto",
                            "min-height:0", "overflow:hidden",
                            "background:var(--twb-card)", "box-shadow:none"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, area,
                              "去卡片顺手改坏了结果区自身的排版：%s" % area)

    def test_22_responsive_rules_do_not_bring_the_margin_back(self):
        compact = re.sub(r"\s+", "", self.css)
        offenders = re.findall(
            r"@media[^{}]+\{[\s\S]*?\.tech-results-area\{[^{}]*margin:([1-9][0-9]*)px", compact)
        self.assertEqual([], offenders,
                         "响应式里又把工艺结果卡的外边距加回来了：%s" % offenders)

    def test_23_outer_column_still_is_not_a_second_card(self):
        outer = rule(self.css, ".tech-workspace-pane")
        self.assertIn("border-radius:0", outer)
        self.assertIn("box-shadow:none", outer)
        self.assertNotRegex(outer, r"margin:(?!0(?:;|$))")
        self.assertIn("border-left:.5pxsolidvar(--twb-border)", outer,
                      "右侧列与左栏的分隔线不能被顺手删掉：%s" % outer)

    def test_24_context_and_footer_separators_are_kept(self):
        context = rule(self.css, ".tech-workspace-context")
        footer = rule(self.css, ".tech-workbench-bottom")
        self.assertIn("border-bottom:.5pxsolidvar(--twb-border)", context)
        self.assertIn("border-top:.5pxsolidvar(--twb-border)", footer)
        self.assertNotRegex(context + footer, r"box-shadow:(?!none)")

    def test_25_three_inner_blocks_keep_their_order_and_ownership(self):
        card = re.search(r'<(?:section|div)[^>]+id="techResultsArea"[^>]*>', self.html)
        self.assertIsNotNone(card, "结果区容器被删了")
        self.assertRegex(card.group(0), r'class="[^"]*tech-results-area')
        positions = [self.html.find(token, card.end()) for token in
                     ("techContextHeader", "techWorkspaceOutlet", "tech-workbench-bottom")]
        self.assertTrue(all(position >= 0 for position in positions), positions)
        self.assertEqual(positions, sorted(positions), "结果区内部顺序被改了")


# --------------------------------------------------------------------------- #
# 工艺标题行：窄很多
# --------------------------------------------------------------------------- #
class CompactStageTitleRowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = read(WB_CSS)
        cls.html = read(WB_HTML)

    def test_30_title_row_is_much_shorter_and_still_fixed_height(self):
        row = rule(self.css, ".tech-workspace-context")
        match = re.search(r"min-height:(\d+)px", row)
        self.assertIsNotNone(match, "标题行丢了 min-height，会变成由内容撑高：%s" % row)
        height = int(match.group(1))
        self.assertEqual(TAIL, height,
                         "标题行高度应从 %dpx 收到 %dpx，现在写的是 %dpx" % (HEAD, TAIL, height))
        self.assertLess(height, HEAD)

    def test_31_title_row_padding_is_halved(self):
        row = rule(self.css, ".tech-workspace-context")
        self.assertIn("padding:4px16px", row,
                      "标题行上下内边距应从 8px 收到 4px（左右 16px 不变）：%s" % row)
        for declaration in ("align-items:center", "justify-content:space-between",
                            "border-bottom:.5pxsolidvar(--twb-border)",
                            "background:var(--twb-card)"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, row, "标题行既有排版被改坏：%s" % row)

    def test_32_pills_and_tabs_are_not_shrunk_to_fake_a_thin_row(self):
        self.assertIn("padding:5px12px", rule(self.css, ".tech-substep-btn"),
                      "子页签内边距被缩小了：%s" % rule(self.css, ".tech-substep-btn"))
        self.assertIn("padding:2px10px", rule(self.css, ".tech-context-notice"),
                      "状态胶囊内边距被缩小了：%s" % rule(self.css, ".tech-context-notice"))
        self.assertIn("font-size:13px", rule(self.css, ".tech-context-title"),
                      "标题字号被改小了：%s" % rule(self.css, ".tech-context-title"))
        self.assertIn("margin-left:auto", rule(self.css, ".tech-substeps-slot"),
                      "子页签右对齐被改掉了")

    def test_33_title_row_structure_is_intact(self):
        block = re.search(r'<div class="tech-workspace-context"[^>]*>([\s\S]*?)</div>\s*<div id="techWorkspaceOutlet"',
                          self.html)
        self.assertIsNotNone(block, "标题行容器找不到")
        for token in ("techContextTitle", "techContextNotice", "techSubstepsBar"):
            with self.subTest(token=token):
                self.assertIn(token, block.group(1), "标题行里的 %s 被删了" % token)


# --------------------------------------------------------------------------- #
# 工艺嵌入态：取消灰底 + 四周间距减半（只在 .tech-embed 作用域内）
# --------------------------------------------------------------------------- #
class EmbeddedStageGreyRemovedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.embed = embed_css()
        cls.base = read(WB_BASE_CSS)

    def test_40_embedded_body_has_no_grey_background(self):
        body = joined(self.embed, ".tech-embed body")
        self.assertRegex(body, r"background:(?:#fff(?:fff)?|white)!important",
                         "嵌入态阶段页的灰底没取消：%s" % body)
        self.assertNotIn("var(--bg-page)", body,
                         "嵌入态还在用页面灰底 token：%s" % body)

    def test_41_page_container_padding_is_halved(self):
        container = rule(self.embed, ".tech-embed .oc-shell .page-container")
        for declaration in ("padding-top:%dpx!important" % TOP_AFTER,
                            "padding-bottom:%dpx!important" % TOP_AFTER,
                            "padding-left:%dpx!important" % SIDE_AFTER,
                            "padding-right:%dpx!important" % SIDE_AFTER):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, container,
                              "阶段页四周间距没有减半（现在上下 %dpx / 左右 %dpx）：%s"
                              % (TOP_BEFORE, SIDE_BEFORE, container))
        self.assertNotIn("padding-left:%dpx" % SIDE_BEFORE, container)
        self.assertNotIn("padding-right:%dpx" % SIDE_BEFORE, container)

    def test_42_body_bottom_padding_is_halved(self):
        body = joined(self.embed, ".tech-embed body")
        self.assertIn("padding-bottom:%dpx!important" % SIDE_AFTER,
                      "阶段页底部留白没有减半：%s" % body)

    def test_43_grey_rule_stays_scoped_to_embed(self):
        """不得把取消灰底写成裸 body 规则 —— 独立打开阶段页要保持今天的灰底。"""
        bare = [body for sel, body in declarations(self.embed) if sel == "body"]
        self.assertEqual([], bare, "嵌入样式块里出现了裸 body 规则：%s" % bare)
        for declaration in ("background:var(--bg-page)", "padding:var(--space-xl)"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, joined(self.base, "body") + rule(self.base, ".page-container"),
                              "workbench.css 的独立打开基线被改了：%s" % declaration)

    def test_44_inner_card_itself_is_kept(self):
        """那层卡片本身保留：只取消它外面的灰底、把四周间距减半。"""
        panel = rule(self.base, ".center-panel")
        for declaration in ("background:var(--bg-card)",
                            "border:1pxsolidvar(--border-color)",
                            "border-radius:var(--radius-lg)"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, panel,
                              "内层卡片的表面被改掉了：%s" % panel)


# --------------------------------------------------------------------------- #
# 回归锚点
# --------------------------------------------------------------------------- #
class FlushDoesNotBreakShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = read(WB_CSS)
        cls.embed = embed_css()
        cls.embed_js = read(EMBED_JS)
        cls.html = read(WB_HTML)

    def test_50_iframe_and_outlet_keep_filling_the_stage(self):
        outlet = rule(self.css, "#techWorkspaceOutlet")
        self.assertIn("flex:1", outlet)
        self.assertIn("min-height:0", outlet)
        frame = rule(self.css, ".tech-stage-frame")
        self.assertIn("width:100%", frame)
        self.assertIn("height:100%", frame)
        self.assertRegex(frame, r"border:0(?:;|$)")
        self.assertNotRegex(frame, r"border-radius:(?!0)")

    def test_51_embed_keeps_the_full_width_overrides(self):
        container = rule(self.embed, ".tech-embed .oc-shell .page-container")
        for declaration in ("width:100%!important", "max-width:none!important",
                            "min-width:0!important", "margin-left:0!important",
                            "margin-right:0!important"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, container,
                              "嵌入态的整列满宽声明被覆盖没了：%s" % container)
        center = rule(self.embed, ".tech-embed .oc-work .center-panel")
        self.assertIn("width:100%!important", center)
        self.assertIn("flex:11100%!important", center)

    def test_52_stage_and_bridge_anchors_are_preserved(self):
        for token in ("techWorkspaceOutlet", "techContextHeader", "techPrev", "techNext",
                      "techResultsArea"):
            with self.subTest(token=token):
                self.assertIn(token, self.html)
        for token in ("mountStageFrame", "TechBoardBridge", "applyStage", "syncChatActions"):
            with self.subTest(token=token):
                self.assertIn(token, read(FRONTEND / "tech-workbench.js"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
