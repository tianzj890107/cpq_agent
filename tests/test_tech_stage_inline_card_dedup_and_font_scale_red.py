"""红测：2.2 / 2.3 六个页签去重复卡片层 + 内容卡片字号分级。

用户口径（两条）：

1. 「整合图纸 / 参数推荐 / 组装工艺 / 零件成本 / 组装成本 / 汇总 这六个页签，每个页里面
   内容里面包了多余的一层，标题和流程标题完全一样，完全是重复的。直接把这个多余的圆角
   卡片连同标题一起去掉，直接显示『已上传的整合图纸』『整机概览』『工序明细』
   『零件成本（2.1 拆出来的每个零件）』『组装成本 · …』『汇总』等等这个级别的内容卡片。」
2. 「这六页的内容是不是字体相比于别的页来说有点小，而且不是很统一。比如『材料 0.71 /
   人工 0.06 / 制造费用 0.03 / 加工费用 0.02』这里就很合适，但『0.82 0.82 单件 小计 操作』
   这里的字体就太小，『来料检验与配组 / 设备: 检验台、量具 / 工时: 4 分』这也太小。
   你只看哪些需要大一点，不要全都直接变大。」

现状（实测，非推断）：

· 「多余的一层圆角卡片」= 这两个阶段页自己的 `.center-panel`
  （`workbench.css:48`：白底 + 1px 边框 + `var(--radius-lg)` 圆角），把整页内容包住；
· 「标题和流程标题完全一样」= 卡片头里的 `.center-title`：`assembly-integration.html:181`
  的 `#aiPanelTitle` 被 `assembly-integration.js:1058` 写成 `AI_TABS[aiTab]`（`:19` =
  整合图纸 / 参数推荐 / 组装工艺），`cost-review.html:148` 的 `#crPanelTitle` 被
  `cost-review.js:515` 写成 `CR_TABS[crTab]`（`:21` = 零件成本 / 组装成本 / 汇总）——
  正好是用户列出的那六个页签名；
· 嵌入态 `tech-embed.js:160`/`:161` 已经隐藏了 `.title-section` 与 `.ai-tabs`，
  只剩这一层卡片与这行重复标题没被处理；
· 字号：`inline-analysis.css` 里内容卡片大量使用 `9px` / `10px`
  （`.inline-cost-table{font-size:9px}`、`.inline-step-grid{font-size:9px}` 等），
  而用户点名「很合适」的 `.cr-part` 是 `12px`（`cost-review.css:9`）。

Spec：docs/specs/tech-stage-inline-card-dedup-and-font-scale.md
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
SPEC = ROOT / "docs" / "specs" / "tech-stage-inline-card-dedup-and-font-scale.md"

WB_CSS = FRONTEND / "workbench.css"
INLINE_CSS = FRONTEND / "inline-analysis.css"
AI_CSS = FRONTEND / "assembly-integration.css"
CR_CSS = FRONTEND / "cost-review.css"
AI_HTML = FRONTEND / "assembly-integration.html"
CR_HTML = FRONTEND / "cost-review.html"
AI_JS = FRONTEND / "assembly-integration.js"
CR_JS = FRONTEND / "cost-review.js"
EMBED_JS = FRONTEND / "tech-embed.js"
INDEX_HTML = FRONTEND / "index.html"

# 「面板自身的头部 / 控件」——本批明确不放大，允许继续用 9px / 10px。
PANEL_CHROME_SMALL = {
    ".inline-analysis-title small",
    ".inline-analysis-close",
    ".inline-analysis-tabs button",
    ".inline-analysis-inputs textarea",
    ".inline-analysis-qty",
    ".inline-analysis-qty input",
    ".inline-file-picker span",
    ".inline-file-picker em",
    ".inline-action",
    ".inline-analysis-status",
}


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def declarations(css: str):
    """按出现顺序返回 (选择器, 去空白声明串)；`@media` 这类 at-rule 只当包装层丢弃。"""
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


def selectors(css: str):
    return [sel for sel, _ in declarations(css)]


def bodies_for(css: str, selector: str):
    """选择器可能是单项，也可能是逗号列表（`.a,.b`）。

    单项：命中逗号选择器里的任一项（原判据，保持不变）；
    逗号列表：要求整条选择器列表逐项相等 —— 这一步原实现恒不匹配
    （`sel.split(",")` 的每一项都不含逗号，永远不等于含逗号的 wanted），
    属脚手架缺陷，就地修正，断言未放松。
    """
    wanted = re.sub(r"\s+", " ", selector).strip()
    wanted_list = [part.strip() for part in wanted.split(",") if part.strip()]
    out = []
    for sel, body in declarations(css):
        parts = [part.strip() for part in sel.split(",") if part.strip()]
        if wanted in parts or parts == wanted_list:
            out.append(body)
    return out


def joined(css: str, selector: str) -> str:
    return "".join(bodies_for(css, selector))


def joined_matching(css: str, *tokens: str) -> str:
    """选择器里同时含这几段的规则拼起来（不要求逐字相等，容得下等价写法）。"""
    out = []
    for selector, body in declarations(css):
        if all(token in selector for token in tokens):
            out.append(body)
    return "".join(out)


def matching_selectors(css: str, *tokens: str):
    return [sel for sel in selectors(css) if all(token in sel for token in tokens)]


def has_declaration(body: str, prop: str, value: str = None) -> bool:
    if value is None:
        return re.search(re.escape(prop) + r"\s*:", body) is not None
    return re.search(re.escape(prop) + r"\s*:\s*" + re.escape(value), body) is not None


PX = r"([0-9.]+)px"


def font_sizes(css: str, selector: str):
    """返回该选择器声明过的所有字号（按出现顺序，含 `font:` 简写）。"""
    found = []
    for body in bodies_for(css, selector):
        for match in re.finditer(r"(?:^|;)font-size:\s*" + PX, body):
            found.append(float(match.group(1)))
        for match in re.finditer(r"(?:^|;)font:\s*" + PX + r"/", body):
            found.append(float(match.group(1)))
    return found


def effective_font_size(css: str, selector: str) -> float:
    """该选择器最后一次生效的字号（后写覆盖先写）。"""
    found = font_sizes(css, selector)
    if not found:
        raise AssertionError("选择器没有任何字号声明：%s" % selector)
    return found[-1]


class SpecPinnedTest(unittest.TestCase):
    def test_spec_exists_and_pins_both_contracts(self):
        text = read(SPEC)
        for token in ("`.oc-work .center-panel`", "`.tech-embed`", "`.center-header`",
                      "`#aiPanelTitle`", "`#crPanelTitle`", "`.inline-cost-table`",
                      "`.inline-step-grid`", "`.cr-part`"):
            self.assertIn(token, text, "Spec 缺少契约锚点：%s" % token)


class StageCardDedupRedTest(unittest.TestCase):
    """契约一：去掉页内这一层圆角卡片与重复标题。"""

    @classmethod
    def setUpClass(cls):
        cls.ai_css = read(AI_CSS)
        cls.cr_css = read(CR_CSS)
        cls.ai_html = read(AI_HTML)
        cls.cr_html = read(CR_HTML)
        cls.ai_js = read(AI_JS)
        cls.cr_js = read(CR_JS)
        cls.wb_css = read(WB_CSS)
        cls.embed_js = read(EMBED_JS)
        cls.index_html = read(INDEX_HTML)

    # --- 2.2 / 2.3 的 .center-panel 不再是卡片 ---

    def test_stage_page_css_declares_flush_center_panel(self):
        matched = matching_selectors(self.ai_css, ".oc-work", ".center-panel")
        self.assertTrue(matched,
                        "assembly-integration.css 缺少 `.oc-work .center-panel` 去卡片规则")
        body = joined_matching(self.ai_css, ".oc-work", ".center-panel")
        self.assertTrue(has_declaration(body, "background", "transparent"),
                        "去卡片后背景必须透明，现在：%s" % body)
        self.assertTrue(has_declaration(body, "border", "0"), "去卡片后边框必须归零")
        self.assertTrue(has_declaration(body, "border-radius", "0"), "去卡片后圆角必须归零")
        self.assertTrue(has_declaration(body, "box-shadow", "none"),
                        "去卡片后不得留阴影（否则仍像一张卡）")

    def test_flush_rule_does_not_break_layout(self):
        body = joined_matching(self.ai_css, ".oc-work", ".center-panel")
        for forbidden in ("display:block", "position:", "overflow", "max-height", "margin"):
            self.assertNotIn(forbidden, body,
                             "去卡片只改外观，不得动 `%s`（会破坏滚动与满高）" % forbidden)

    def test_flush_rule_is_page_scoped_not_shared(self):
        panel_body = joined(self.wb_css, ".center-panel")
        self.assertTrue(has_declaration(panel_body, "background", "var(--bg-card)"),
                        "workbench.css 的 .center-panel 基线不许被本批改动")
        self.assertTrue(has_declaration(panel_body, "border-radius", "var(--radius-lg)"),
                        "workbench.css 的 .center-panel 圆角基线不许被本批改动")

    # --- 嵌入态这一行整行退出布局 ---

    def test_embed_header_row_collapses(self):
        matched = matching_selectors(self.ai_css, ".tech-embed", ".center-header")
        self.assertTrue(matched,
                        "assembly-integration.css 缺少 `.tech-embed … .center-header` 收起规则")
        body = joined_matching(self.ai_css, ".tech-embed", ".center-header")
        self.assertTrue(has_declaration(body, "display", "none"),
                        "嵌入态这一行没有可显示的内容，必须整行退出布局")

    def test_center_header_rules_stay_scoped(self):
        for sel in matching_selectors(self.ai_css, ".center-header"):
            self.assertIn(".tech-embed", sel,
                          "`.center-header` 规则必须限定在 .tech-embed 作用域：%s" % sel)
        self.assertNotRegex(self.ai_css, r"(?:^|[},])\s*body\s*\{",
                            "assembly-integration.css 不许新增裸 body 规则")

    def test_embed_shared_hide_list_unchanged(self):
        self.assertIn('.tech-embed .title-section { display: none !important; }', self.embed_js)
        self.assertIn('.tech-embed .ai-tabs { display:none !important; }', self.embed_js)
        self.assertNotIn('.tech-embed .center-header',
                         self.embed_js,
                         "tech-embed.js 的隐藏清单被 11 个阶段页共用，不能在这里加 center-header")

    # --- 重复标题节点删除 ---

    def test_duplicate_title_nodes_removed(self):
        self.assertNotIn("aiPanelTitle", self.ai_html)
        self.assertNotIn("crPanelTitle", self.cr_html)
        self.assertNotIn("center-title", self.ai_html)
        self.assertNotIn("center-title", self.cr_html)

    def test_tab_rows_and_card_container_kept(self):
        for html, tabs_id in ((self.ai_html, 'id="aiTabs"'), (self.cr_html, 'id="crTabs"')):
            self.assertIn('class="center-header"', html, "卡片头这一行要保留（页签还挂在它里面）")
            self.assertIn(tabs_id, html, "页签行必须保留")
            self.assertIn('class="ai-body"', html, "内容容器必须保留")
        for label in ("整合图纸", "参数推荐", "组装工艺"):
            self.assertIn(label, self.ai_html)
        for label in ("零件成本", "组装成本", "汇总"):
            self.assertIn(label, self.cr_html)

    def test_js_no_longer_writes_panel_title(self):
        self.assertNotIn("aiPanelTitle", self.ai_js)
        self.assertNotIn("crPanelTitle", self.cr_js)
        self.assertNotRegex(self.ai_js, r"\$ai\(\s*['\"]aiPanelTitle['\"]\s*\)")
        self.assertNotRegex(self.cr_js, r"\$cr\(\s*['\"]crPanelTitle['\"]\s*\)")

    def test_ai_tabs_constant_still_used_elsewhere(self):
        self.assertGreaterEqual(len(re.findall(r"AI_TABS", self.ai_js)), 4,
                                "AI_TABS 还有 4 处用途（页签提示 / 生成文案 / 步骤文案 / 缺产出提示），"
                                "删标题那一行后必须保留")
        self.assertRegex(self.ai_js, r"#aiTabs \[data-ai-tab\][\s\S]{0,220}classList\.toggle\('active'")
        self.assertRegex(self.cr_js, r"#crTabs \[data-cr-tab\][\s\S]{0,220}classList\.toggle\('active'")

    # --- 内容卡片本身必须保留 ---

    def test_content_cards_preserved(self):
        for title in ("已上传的整合图纸", "整机概览", "工序明细", "装配方案", "工艺库覆盖",
                      "库内依据 · 工艺库", "假设与待澄清", "报价必填参数完成度",
                      "零件间连接", "整机 BOM（单台用量）"):
            self.assertIn(title, self.ai_js, "2.2 的内容卡片被误删：%s" % title)
        for title in ("零件成本（2.1 拆出来的每个零件）", "组装成本 · ", "汇总", "本步状态"):
            self.assertIn(title, self.cr_js, "2.3 的内容卡片被误删：%s" % title)
        for js in (self.ai_js, self.cr_js):
            self.assertIn('class="inline-card"', js)
            self.assertIn('class="inline-card-title"', js)

    # --- 其它页的 .center-panel 不受影响 ---

    def test_other_pages_keep_their_card(self):
        self.assertIn('class="center-panel"', self.index_html)
        self.assertIn('id="viewerPartName" class="center-title"', self.index_html)
        self.assertRegex(self.wb_css, r"\.center-title\s*\{[^}]*font-size:\s*13px")

    def test_cache_busters_bumped(self):
        for html in (self.ai_html, self.cr_html, self.index_html):
            self.assertNotIn("inline-analysis.css?v=20260819-flat6", html,
                             "inline-analysis.css 改了，三页引用版本号都要提升")
        for html in (self.ai_html, self.cr_html):
            self.assertNotIn("assembly-integration.css?v=ai8", html,
                             "assembly-integration.css 改了，两页引用版本号都要提升")
            self.assertIn("assembly-integration.css?v=ai9", html)


class InlineFontScaleRedTest(unittest.TestCase):
    """契约二：内容卡片字号三档，不再出现 9px / 10px。"""

    @classmethod
    def setUpClass(cls):
        cls.css = read(INLINE_CSS)
        cls.cr_css = read(CR_CSS)

    # --- 用户点名的两处必须到 12px ---

    def test_complained_table_and_step_grid_reach_body_size(self):
        self.assertEqual(effective_font_size(self.css, ".inline-cost-table"), 12.0,
                         "「0.82 0.82 单件 小计 操作」所在表格太小")
        self.assertEqual(
            effective_font_size(self.css,
                                ".inline-cost-table input,.inline-cost-table select"), 12.0,
            "表格里的可编辑单元格必须跟表格同字号")
        self.assertEqual(effective_font_size(self.css, ".inline-step-grid"), 12.0,
                         "「设备: 检验台、量具 / 工时: 4 分」太小")
        self.assertEqual(effective_font_size(self.css, ".inline-step-title"), 12.5,
                         "「来料检验与配组」这类工序名要略高于正文")

    # --- 标题档 ---

    def test_card_title_tier(self):
        self.assertEqual(effective_font_size(self.css, ".inline-card-title"), 13.0,
                         "内容卡片标题是删掉重复页标题后页内最高一级，应与 .center-title 同级")

    # --- 正文档 ---

    def test_body_tier(self):
        expected = {
            ".inline-row": 12.0,
            ".inline-cov-row": 12.0,
            ".inline-description": 12.0,
            ".inline-edit-grid label": 12.0,
            ".inline-edit-grid input,.inline-edit-grid select,.inline-edit-grid textarea": 12.0,
        }
        for selector, size in expected.items():
            self.assertEqual(effective_font_size(self.css, selector), size,
                             "%s 应为正文档 %spx" % (selector, size))

    # --- 注解档 ---

    def test_annotation_tier(self):
        expected = {
            ".inline-hint": 11.0,
            ".inline-warn,.inline-question": 11.0,
            ".inline-source,.inline-assumption": 11.0,
            ".inline-reference": 11.0,
            ".inline-reference small": 11.0,
            ".inline-lib-step": 11.0,
            ".inline-lib-step code": 11.0,
            ".inline-lib-step small": 11.0,
            ".inline-cat-bar": 11.0,
            ".inline-cat-tag": 11.0,
            ".inline-cov-code": 11.0,
            ".inline-cov-split": 11.0,
            ".inline-totals span": 11.0,
            ".inline-cost-total span": 11.0,
            ".inline-cost-total em": 11.0,
            ".inline-type": 11.0,
            ".inline-confidence": 11.0,
            ".inline-dep": 11.0,
            ".inline-sno": 11.0,
        }
        for selector, size in expected.items():
            self.assertEqual(effective_font_size(self.css, selector), size,
                             "%s 应为注解档 %spx" % (selector, size))

    def test_no_content_text_below_11px(self):
        offenders = {}
        for selector, body in declarations(self.css):
            for part in (p.strip() for p in selector.split(",")):
                if not part.startswith(".inline-"):
                    continue
                if part in PANEL_CHROME_SMALL:
                    continue
                sizes = font_sizes(self.css, part)
                if sizes and min(sizes) < 11:
                    offenders[part] = sizes
        self.assertEqual(offenders, {},
                         "内容卡片里还有低于 11px 的字号（面板头部/控件除外）：%s" % offenders)

    # --- 明确不放大 ---

    def test_panel_chrome_unchanged(self):
        self.assertEqual(effective_font_size(self.css, ".inline-analysis"), 12.0)
        self.assertEqual(effective_font_size(self.css, ".inline-analysis-title strong"), 14.0)
        self.assertEqual(effective_font_size(self.css, ".inline-analysis-title small"), 10.0)
        self.assertEqual(effective_font_size(self.css, ".inline-analysis-tabs button"), 11.0)
        self.assertEqual(effective_font_size(self.css, ".inline-action"), 11.0)
        self.assertEqual(effective_font_size(self.css, ".inline-analysis-status"), 10.0)
        self.assertEqual(effective_font_size(self.css, ".inline-file-picker span"), 10.0)

    def test_big_numbers_unchanged(self):
        self.assertEqual(effective_font_size(self.css, ".inline-totals strong"), 15.0)
        self.assertEqual(effective_font_size(self.css, ".inline-cost-total strong"), 23.0)

    def test_card_geometry_and_table_padding_unchanged(self):
        card = joined(self.css, ".inline-card")
        self.assertTrue(has_declaration(card, "padding", "9px"), "内容卡片内边距不许动")
        self.assertTrue(has_declaration(card, "border-radius", "var(--radius-md)"),
                        "内容卡片圆角不许动")
        header = joined(self.css, ".inline-cost-table th")
        self.assertTrue(has_declaration(header, "padding", "5px4px"),
                        "表格内边距不属于字号问题，不许顺手放大")
        table = joined(self.css, ".inline-cost-table")
        self.assertTrue(has_declaration(table, "min-width", "650px"), "表格最小宽度不许缩小")

    def test_cost_review_baseline_untouched(self):
        self.assertEqual(effective_font_size(self.cr_css, ".cr-part"), 12.0,
                         "用户点名「很合适」的样本必须保持 12px")
        self.assertEqual(effective_font_size(self.cr_css, ".cr-part i"), 11.0)
        self.assertIn("tr.cr-final", self.cr_css)


if __name__ == "__main__":
    unittest.main()
