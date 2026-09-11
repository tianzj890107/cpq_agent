"""首页卡片等高红测基线：待办卡与会话卡高度统一、待办卡不再留空占位行。

现状缺口（`报价首页.html`）：

  · `.card-grid` 只写了 `repeat(3, 1fr)`，没有显式等高策略，`.request-card` 也没有吃满格高；
  · 待办卡 `taskCardHtml()` 比会话卡 `cardHtml()` 多一行「来自 … 第 N 步」，
    备注为空时还用 `&nbsp;` 硬占一整行，导致同一行卡片高度不一致、空白把页脚顶开；
  · 描述这种占位做法的注释（「备注那一行没内容时也占位，四张卡的页脚才对得齐」）仍然留在文件里。

本批只改 `报价首页.html` 的卡片网格/卡片样式与待办卡备注行；不碰会话卡信息行、不碰数据接口。
不联网、不起服务、不读真实业务数据；只做静态契约校验。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME = ROOT / "报价首页.html"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _brced(text: str, signature: str) -> str:
    """返回 signature 之后第一对成对花括号（含花括号）的源码，跳过字符串与注释。"""
    idx = text.find(signature)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(signature))
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            i = len(text) if j < 0 else j + 2
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace : i + 1]
        i += 1
    return ""


def _js_function(text: str, name: str) -> str:
    match = re.search(r"function\s+%s\s*\(" % re.escape(name), text)
    return _brced(text, match.group(0)) if match else ""


def _css_rule(css: str, selector: str) -> str:
    # 选择器后面只允许空白或 `{`（排除 `.request-card.wf-task` 这类复合选择器和 `:hover`）。
    match = re.search(re.escape(selector) + r"(?![.\w:-])[^{}]*\{([^}]*)\}", css)
    return match.group(1) if match else ""


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


class HomeCardsEqualHeightRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = _read(HOME)
        cls.grid = _css_rule(cls.home, ".card-grid")
        cls.card = _css_rule(cls.home, ".request-card")
        cls.info = _css_rule(cls.home, ".request-info")
        cls.footer = _css_rule(cls.home, ".request-footer")
        cls.task_card = _js_function(cls.home, "taskCardHtml")
        cls.session_card = _js_function(cls.home, "cardHtml")

    # ------------------------------------------------------------ 等高契约
    def test_card_grid_stretches_row_items(self):
        self.assertTrue(self.grid, "找不到 .card-grid 规则")
        self.assertIn(
            "align-items:stretch",
            _compact(self.grid),
            ".card-grid 必须显式 align-items: stretch，同一行卡片才会等高",
        )

    def test_request_card_fills_grid_cell(self):
        self.assertTrue(self.card, "找不到 .request-card 规则")
        self.assertIn(
            "height:100%",
            _compact(self.card),
            ".request-card 必须 height: 100% 吃满网格单元，才会与同行最高的卡片等高",
        )

    def test_no_start_alignment_on_cards(self):
        for name, body in ((".card-grid", self.grid), (".request-card", self.card)):
            self.assertNotIn(
                "align-items:start",
                _compact(body),
                "%s 不能退回 align-items: start（会让卡片各自自然高度）" % name,
            )
        self.assertNotIn(
            "align-self:start",
            _compact(self.grid + self.card),
            "卡片网格/卡片上不能出现 align-self: start",
        )

    # ------------------------------------------------------------ 待办卡空占位
    def test_pending_card_has_no_empty_nbsp_placeholder(self):
        self.assertTrue(self.task_card, "找不到 taskCardHtml() 函数体")
        self.assertNotIn(
            "&nbsp;",
            self.task_card,
            "待办卡不能再拿 &nbsp; 占一整行空备注；备注为空时应当不渲染该行",
        )

    def test_pending_card_note_line_is_conditional(self):
        self.assertIn("request-note", self.task_card, "待办卡缺少备注行渲染")
        self.assertRegex(
            self.task_card,
            r"t\.note\s*\?",
            "待办卡备注行必须由 t.note 条件渲染（有备注才占一行）",
        )

    def test_pending_card_keeps_source_and_step_line(self):
        for token in ("来自 ", "第 ", "步"):
            self.assertIn(token, self.task_card, "待办卡「来自 … 第 N 步」这一行不能删：缺 %r" % token)
        self.assertIn(
            "border-left: 3px solid var(--color-primary)",
            _css_rule(self.home, ".request-card.wf-task"),
            "待办卡左侧蓝边不能丢",
        )

    # ------------------------------------------------------------ 保留守卫
    def test_session_card_does_not_add_fake_info_lines(self):
        self.assertTrue(self.session_card, "找不到 cardHtml() 函数体")
        info_lines = self.session_card.count('<div class="request-info')
        self.assertLessEqual(
            info_lines,
            2,
            "会话卡不许靠补占位信息行去凑高度，实际出现 %d 行" % info_lines,
        )

    def test_footer_still_pins_to_bottom(self):
        self.assertIn("margin-top:auto", _compact(self.footer), "页脚贴底规则被改动")

    def test_grid_columns_and_single_line_truncation_kept(self):
        self.assertIn("repeat(3,1fr)", _compact(self.grid), "三列网格被改动")
        info = _compact(self.info)
        self.assertIn("white-space:nowrap", info)
        self.assertIn("text-overflow:ellipsis", info)
        card = _compact(self.card)
        self.assertIn("display:flex", card)
        self.assertIn("flex-direction:column", card)

    def test_stale_placeholder_comment_is_gone(self):
        for stale in ("备注那一行没内容时也占位", "四张卡的页脚才对得齐"):
            self.assertNotIn(
                stale,
                self.home,
                "描述空占位做法的过时注释必须删掉或改写：%s" % stale,
            )


if __name__ == "__main__":
    unittest.main()
