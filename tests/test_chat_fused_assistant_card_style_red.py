"""红测：助手卡片融合风格（技术白卡 + 报价蓝色身份行 + 状态 chip + 只折叠思维链）。

已确认口径：保留技术工艺卡片、内容默认展开；只有「思考过程」与工具「详情」折叠；
保留卡片右上角的运行中 / 已完成状态图标；同时保留报价那行蓝色身份字。

现状缺口：
  · 技术 `.oc-amsg` 只有 `display:flex; gap:11px`，没有白底卡片、边框，也没有身份行；
    `.oc-intent-card` 仍是 `var(--oc-bg-2)` 灰底，`.oc-art` / `.oc-task-card` 是 `.5px` 细边；
  · 报价 `.message-ai` 是 `var(--bg-page)`、`.tool-activity.trace` 是 `var(--bg-secondary)` 灰底；
  · 技术没有身份行与其右侧状态 chip；报价身份行也没有状态 chip；
  · 看板 `task-progress` / `task-completed` / `task-failed` payload 只有 `{action, phase}`，
    `renderTaskProgress()` 把 label 兜底成「处理中」，所有事件落进同一张空卡反复翻转。

本批只改样式与状态 chip，不改任务轮询 / 后端任务接口 / 桥协议；不引入任何把运行过程
默认收起的折叠结构（上一稿的 `oc-task-fold` 作废）。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
TECH_CSS = F / "agent-chat.css"
TECH_JS = F / "agent-chat.js"
QUOTE = ROOT / "确认需求解析结果.html"
RUNTIME = F / "tech-board-runtime.js"
BRIDGE = F / "tech-board-bridge.js"
APP_JS = F / "app.js"
INLINE_JS = F / "inline-analysis.js"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

WHITE = re.compile(r"background\s*:\s*(#fff(?:fff)?|white)\b")
BORDER_1PX = re.compile(r"border\s*:\s*1px solid")
GRAY = ("var(--bg-secondary)", "var(--oc-bg-2)", "var(--oc-bg-3)")

BLUE = ("#0060E6", "#0050C4")
STATE_COLORS = {
    "is-running": ("#e0edff", "#0050C4"),
    "is-succeeded": ("#dcfce7", "#15803d"),
    "is-failed": ("#fee2e2", "#b91c1c"),
}
STATUS_GLYPHS = ("◌", "✓", "⚠")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def rule(text: str, selector: str) -> str:
    """返回单个 CSS 选择器的规则体（选择器后必须紧跟 `{`，避免前缀误命中）。"""
    match = re.search(re.escape(selector) + r"\s*\{", text)
    if not match:
        return ""
    start = text.find("{", match.start())
    end = text.find("}", start)
    return text[start:end + 1] if end > 0 else ""


def block_from(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(marker))
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
                return text[brace:i + 1]
        i += 1
    return ""


class ChatFusedAssistantCardStyleRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tech_css = read(TECH_CSS)
        cls.tech_js = read(TECH_JS)
        cls.quote = read(QUOTE)
        cls.runtime = read(RUNTIME)
        cls.bridge = read(BRIDGE)
        cls.app_js = read(APP_JS)
        cls.inline_js = read(INLINE_JS)
        cls.main = read(MAIN)

    def _assert_card(self, text, selector, where):
        body = rule(text, selector)
        self.assertTrue(body, f"{where} 找不到 {selector} 规则")
        self.assertRegex(body, WHITE, f"{selector} 必须是白底（当前：{body.strip()}）")
        self.assertRegex(body, BORDER_1PX, f"{selector} 必须有 1px 边框（当前：{body.strip()}）")
        for token in GRAY:
            self.assertNotIn(token, body, f"{selector} 不得再用灰底 {token}")

    # ------------------------------------------------------------ 技术：白卡
    def test_tech_assistant_card_is_white_with_border(self):
        self._assert_card(self.tech_css, ".oc-amsg", "agent-chat.css")
        self.assertIn("border-radius", rule(self.tech_css, ".oc-amsg"), "白卡需要圆角")
        self.assertIn("padding", rule(self.tech_css, ".oc-amsg"), "白卡需要内边距")

    def test_tech_inner_cards_are_white_with_border(self):
        for selector in (".oc-art", ".oc-task-card", ".oc-intent-card"):
            with self.subTest(selector=selector):
                self._assert_card(self.tech_css, selector, "agent-chat.css")
        task_body = rule(self.tech_css, ".oc-task-steps")
        self.assertTrue(task_body, "步骤区 .oc-task-steps 必须保留（默认展开）")

    # ------------------------------------------------------------ 技术：蓝色身份行
    def test_tech_assistant_has_blue_identity_row(self):
        body = rule(self.tech_css, ".oc-alabel")
        self.assertTrue(body, "缺少 .oc-alabel 身份行样式")
        self.assertRegex(body, r"color\s*:\s*(#0060E6|var\(--color-secondary\))",
                         "身份行必须是报价同款蓝色字")
        self.assertIn("display: flex", body, "身份行是 flex 行")
        dot = rule(self.tech_css, ".oc-alabel::before")
        self.assertTrue(dot, "身份行需要左侧圆点")
        self.assertIn("6px", dot, "圆点必须是 6px")
        for hex_value in BLUE:
            with self.subTest(hex=hex_value):
                self.assertIn(hex_value, dot, "圆点渐变必须与报价 --gradient-ai 同值")
        creator = block_from(self.tech_js, "function addAssistant(")
        self.assertTrue(creator, "找不到 addAssistant()")
        self.assertIn("oc-alabel", creator, "身份行必须由 addAssistant() 插入")
        self.assertIn("技术工艺智能体", creator, "身份行文案缺失")
        self.assertNotIn("oc-alabel-state", block_from(self.tech_js, "function pushSystem("),
                         "系统告警卡不带状态 chip")

    # ------------------------------------------------------------ 技术：状态 chip
    def test_tech_identity_row_has_right_aligned_status_chip(self):
        body = rule(self.tech_css, ".oc-alabel-state")
        self.assertTrue(body, "缺少 .oc-alabel-state 状态 chip 样式")
        self.assertRegex(body, r"margin-left\s*:\s*auto", "chip 必须靠右，不做卡片外悬浮角标")
        self.assertIn("border-radius: 999px", body, "chip 用胶囊圆角")
        for state, (bg, fg) in STATE_COLORS.items():
            with self.subTest(state=state):
                state_body = rule(self.tech_css, f".oc-alabel-state.{state}")
                self.assertTrue(state_body, f"缺少 {state} 状态样式")
                self.assertIn(bg, state_body, f"{state} 背景色必须与任务卡状态一致")
                self.assertIn(fg, state_body, f"{state} 文字色必须与任务卡状态一致")

    def test_tech_status_flips_in_place_with_same_glyphs(self):
        updater = block_from(self.tech_js, "function setAssistantState(")
        self.assertTrue(updater, "缺少 setAssistantState(ctx, state)")
        for token in ("running", "succeeded", "failed"):
            with self.subTest(token=token):
                self.assertIn(token, updater, f"setAssistantState 必须支持 {token}")
        for glyph in STATUS_GLYPHS:
            with self.subTest(glyph=glyph):
                self.assertIn(glyph, updater, f"缺少状态图标 {glyph}")
        creator = block_from(self.tech_js, "function addAssistant(")
        self.assertIn("is-running", creator, "新建助手卡默认是运行中")
        handler = block_from(self.tech_js, "function handleEvent(")
        self.assertRegex(handler, r'setAssistantState\(\s*ctx\s*,\s*["\']succeeded["\']',
                         "done 必须把同一张 chip 翻成已完成")
        self.assertRegex(handler, r'setAssistantState\(\s*ctx\s*,\s*["\']failed["\']',
                         "error 必须把同一张 chip 翻成失败")

    # ------------------------------------------------------------ 技术：默认展开
    def test_tech_content_is_expanded_by_default(self):
        for bad in ("oc-task-fold", "过程详情"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, self.tech_js, "不得把运行过程默认收起")
                self.assertNotIn(bad, self.tech_css, "不得把运行过程默认收起")
        task_body = block_from(self.tech_js, "function renderTaskProgress(")
        self.assertTrue(task_body, "找不到 renderTaskProgress()")
        self.assertNotIn("details", task_body, "任务卡步骤必须直接可见，不包 details")
        self.assertIn("oc-task-steps", self.tech_js, "步骤区仍用 .oc-task-steps 直接渲染")

    def test_tech_only_thinking_and_tool_payload_are_collapsible(self):
        tool_card = block_from(self.tech_js, "function addToolCard(")
        self.assertTrue(tool_card, "找不到 addToolCard()")
        self.assertIn("oc-art-detail", tool_card, "工具详情折叠块必须保留")
        self.assertIn("详情", tool_card, "工具详情折叠行文案缺失")
        self.assertIn("oc-thinking", self.tech_js, "思考过程折叠块缺失")
        self.assertNotIn("oc-thinking", block_from(self.tech_js, "function renderTaskProgress("),
                         "思考过程折叠块不属于任务卡")

    # ------------------------------------------------------------ 任务事件去噪
    def test_board_runtime_task_events_carry_label_and_task_id(self):
        for event in ("TASK_PROGRESS", "TASK_COMPLETED", "TASK_FAILED"):
            with self.subTest(event=event):
                body = block_from(self.runtime, f"publish(EVENT.{event},")
                self.assertTrue(body, f"找不到 {event} 的 publish 调用")
                self.assertIn("label", body, f"{event} payload 必须带 label")
                self.assertIn("taskId", body, f"{event} payload 必须带 taskId")

    def test_board_pages_forward_real_task_detail_to_parent(self):
        self.assertRegex(self.app_js, r"TechBoardRuntime\s*&&[\s\S]{0,200}publish\(",
                         "2.1 必须把 pollTask 已有的 detail 经 TechBoardRuntime.publish 转给父壳")
        idx = self.app_js.find("agent:task-progress")
        self.assertGreater(idx, -1, "pollTask 的 agent:task-progress 派发被删除")
        window = self.app_js[idx:idx + 900]
        for token in ("label", "taskId", "log"):
            with self.subTest(token=token):
                self.assertIn(token, window, f"2.1 任务 detail 必须保留 {token}")
        self.assertRegex(self.inline_js, r"TechBoardRuntime\s*&&[\s\S]{0,200}publish\(",
                         "2.2 / 2.3 的 inline-analysis 必须同样把 detail 转给父壳")

    def test_empty_task_card_is_not_rendered(self):
        self.assertNotIn('detail.label || "处理中"', self.tech_js, "不得再把 label 兜底成「处理中」")
        self.assertNotIn("detail.label || '处理中'", self.tech_js, "不得再把 label 兜底成「处理中」")
        body = block_from(self.tech_js, "function renderTaskProgress(")
        self.assertRegex(body, r"if\s*\(\s*!label\s*&&\s*!taskId",
                         "label / taskId / log / progress 全空时必须直接 return，不建卡")
        self.assertIn("progress", body, "空判据要包含 progress")

    # ------------------------------------------------------------ 报价：白卡 + chip
    def test_quote_assistant_and_trace_are_white_with_border(self):
        for selector in (".message-ai", ".tool-activity.trace"):
            with self.subTest(selector=selector):
                self._assert_card(self.quote, selector, "确认需求解析结果.html")

    def test_quote_identity_row_has_right_aligned_status_chip(self):
        body = rule(self.quote, ".message-label-state")
        self.assertTrue(body, "缺少 .message-label-state 状态 chip 样式")
        self.assertRegex(body, r"margin-left\s*:\s*auto", "chip 必须靠右")
        for state, (bg, fg) in STATE_COLORS.items():
            with self.subTest(state=state):
                state_body = rule(self.quote, f".message-label-state.{state}")
                self.assertTrue(state_body, f"缺少 {state} 状态样式")
                self.assertIn(bg, state_body, f"{state} 背景色必须与技术侧一致")
                self.assertIn(fg, state_body, f"{state} 文字色必须与技术侧一致")
        self.assertIn("报价单智能体", self.quote, "报价身份行文案被删除")
        streaming = block_from(self.quote, "function ensureStreamBubble(")
        self.assertTrue(streaming, "找不到 ensureStreamBubble()")
        self.assertIn("message-label-state", streaming, "报价助手卡必须带状态 chip")
        self.assertIn("is-running", streaming, "流式开始时是运行中")
        self.assertIn("is-succeeded", block_from(self.quote, "function finishStreamBubble("),
                      "流式结束必须翻成已完成")
        self.assertIn("is-failed", block_from(self.quote, "function addErrorBubble("),
                      "出错必须翻成失败")

    def test_user_bubbles_keep_primary_fill(self):
        tech_user = rule(self.tech_css, ".oc-ubub")
        self.assertIn("var(--oc-accent)", tech_user, "技术用户气泡必须保持主色实心")
        self.assertNotRegex(tech_user, WHITE, "技术用户气泡不得改成白底")
        quote_user = rule(self.quote, ".message-user")
        self.assertIn("var(--color-primary)", quote_user, "报价用户气泡必须保持主色实心")
        self.assertNotRegex(quote_user, WHITE, "报价用户气泡不得改成白底")

    # ------------------------------------------------------------ 保护边界
    def test_existing_pipeline_and_bridge_are_kept(self):
        for token in ("pushTaskStep", "toneOf", "sanitizeTaskDetail", "setTaskStatus",
                      "taskProgressHost"):
            with self.subTest(token=token):
                self.assertIn(token, self.tech_js, f"既有实现 {token} 被删除")
        self.assertIn("oc-task-card", self.tech_css, "任务卡结构被改动")
        for event in ("ready", "action-state", "task-progress", "task-completed", "task-failed",
                      "selection-changed"):
            with self.subTest(event=event):
                self.assertIn(event, self.bridge, f"桥事件 {event} 被改动")
        for token in ("function addToolActivity(", "function showStage(", "function showTyping(",
                      "function describeTool("):
            with self.subTest(token=token):
                self.assertIn(token, self.quote, f"报价轨迹实现 {token} 被删除")
        for bad in ("oc-task-fold", "oc-alabel-state", "message-label-state"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, self.main, "不得为样式改动新增后端路由")


if __name__ == "__main__":
    unittest.main()
