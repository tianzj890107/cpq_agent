"""红测：报错按普通输出走会话流，refresh 类动作不再产生任务进度卡。

现状缺口（实测）：
  · `agent-chat.js:1559-1580` 的 `showBoardNavFailure()` 除了写一条流内提示，还额外 append 了
    `div.oc-retry-row`；`agent-chat.css:624` 的 `.oc-retry-row { display:flex; order:1; ... }`
    把这一行永久钉在 `.oc-tinner`（flex column）最底部，而且从不移除 ——
    一次失败之后，底部那行提示会一直挂着，后面再聊多少轮也不动。
  · `tech-board-runtime.js` 的 `runEntry()` 对**每个**动作都发 `TASK_PROGRESS(phase:'start')` /
    `TASK_COMPLETED`（失败再发 `TASK_FAILED`），父壳把这些渲染成 `.oc-task-card`（按 taskId 去重、
    永不删除），于是刷新动作每跑一次就在会话里留一张「刷新…看板 已完成」。
  · `agent-chat.js:1504` 的 `.catch(() => {})` 把 refresh 失败静默吞掉。

本批只改「失败提示的呈现位置」与「刷新动作要不要出卡」；任务卡机制、结果入口常驻、
后端路由与协议字段一律不动。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
CHAT_JS = F / "agent-chat.js"
CHAT_CSS = F / "agent-chat.css"
RUNTIME = F / "tech-board-runtime.js"
BRIDGE = F / "tech-board-bridge.js"
WB_HTML = F / "tech-workbench.html"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 每页那颗「刷新…」动作：本批起一律 silent（不出任务卡）。
REFRESH_ACTIONS = (
    (F / "app.js", "refreshData:"),
    (F / "requirement-create.js", "refreshData:"),
    (F / "requirement-confirm-page.js", "refreshData:"),
    (F / "requirement-review-page.js", "refreshData:"),
    (F / "assembly-integration.js", "refreshIntegration:"),
    (F / "cost-review.js", "refreshCostReview:"),
    (F / "summary-result.js", "refreshProcessReport:"),
    (F / "report-review-result.js", "refreshProcessReport:"),
    (F / "report-publish-result.js", "refreshProcessReport:"),
)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


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


def order_one_selectors(css: str) -> list[str]:
    """返回所有带 `order: 1` 的规则选择器（取规则体前的最后一行文本）。"""
    out = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"(?:^|[;\s])order:\s*1\b", body):
            out.append(selector.strip().splitlines()[-1].strip())
    return out


class ChatErrorsInflowAndDropRefreshTaskCardsRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = read(CHAT_JS)
        cls.css = read(CHAT_CSS)
        cls.runtime = read(RUNTIME)
        cls.bridge = read(BRIDGE)
        cls.wb_html = read(WB_HTML)
        cls.main = read(MAIN)

    # ------------------------------------------------ 契约 A：失败提示回到会话流
    def test_no_pinned_notice_row_in_chat_css(self):
        pinned = order_one_selectors(self.css)
        self.assertEqual(pinned, [".oc-result-actions"],
                         "只有「结果入口常驻底部」可以有 order:1；提示行不得钉在会话最下面")

    def test_nav_failure_message_is_in_flow(self):
        body = block_from(self.chat, "function showBoardNavFailure(")
        self.assertTrue(body, "找不到 showBoardNavFailure()")
        self.assertIn("noteInThread(", body, "失败原因必须继续用会话流内的普通消息输出")
        self.assertNotIn("oc-retry-row", body, "不得再新建独立提示行")
        self.assertNotIn("tinner.append(", body,
                         "失败提示不得直接 append 到会话末尾（那就是钉在底部）")

    def test_retry_control_is_kept_with_the_message(self):
        body = block_from(self.chat, "function showBoardNavFailure(")
        self.assertIn("oc-chip-retry", body, "重试能力不得被一起删掉")
        self.assertIn("boardNavigateView(", body, "重试必须复用唯一导航出口")

    def test_chat_css_drops_the_retry_row_rule(self):
        self.assertNotIn(".oc-retry-row", self.css,
                         "常驻底部行已退役，对应 CSS 规则必须一起删除")

    def test_existing_error_channels_are_kept(self):
        self.assertIn("function pushSystem(", self.chat, "pushSystem 仍是流内错误输出通道")
        self.assertIn("oc-err-line", self.chat)
        self.assertIn(".oc-err-line", self.css, "流内错误行样式保留")

    # ------------------------------------------------ 契约 B：silent 动作不出卡
    def test_runtime_gates_card_events_by_silent(self):
        body = block_from(self.runtime, "function runEntry(")
        self.assertTrue(body, "找不到 runEntry()")
        self.assertIn("entry.silent === true", body, "必须支持条目级 silent 标记")
        self.assertRegex(body, r"function publishTaskCard\s*\(",
                         "卡片事件必须收口到一个可开关的本地出口")
        self.assertGreaterEqual(body.count("publishTaskCard("), 4,
                                "1 处定义 + 3 处调用（progress / completed / failed）")
        for event in ("TASK_PROGRESS", "TASK_COMPLETED", "TASK_FAILED"):
            with self.subTest(event=event):
                self.assertNotRegex(body, rf"publish\(EVENT\.{event}",
                                    f"卡片事件 {event} 不得再被直接发布")

    def test_card_gate_still_emits_three_events(self):
        body = block_from(self.runtime, "function publishTaskCard(")
        self.assertTrue(body, "找不到 publishTaskCard()")
        self.assertIn("entry.silent === true", body, "silent 判定必须在出口里")
        for event in ("EVENT.TASK_PROGRESS", "EVENT.TASK_COMPLETED", "EVENT.TASK_FAILED"):
            with self.subTest(event=event):
                self.assertIn(event, body, f"{event} 仍须由该出口发布")

    def test_refresh_actions_are_silent(self):
        for path, marker in REFRESH_ACTIONS:
            with self.subTest(file=path.name, action=marker):
                block = block_from(read(path), marker)
                self.assertTrue(block, f"{path.name} 找不到 {marker}")
                self.assertRegex(block, r"silent:\s*true",
                                 "刷新动作是看板内部同步，不该在会话里留「已完成」卡")
                self.assertRegex(block, r"visible:\s*false",
                                 "既有的 visible:false 不能回退成可见")

    def test_task_card_pipeline_is_untouched(self):
        self.assertIn("function renderTaskProgress(", self.chat,
                      "任务卡渲染不得删除：解析等长任务仍要出卡")
        self.assertIn(".oc-task-card", self.css, "任务卡样式保留")
        self.assertIn("ocTaskProgressHost", self.chat + self.wb_html, "进度宿主保留")

    def test_entry_state_contract_unchanged(self):
        body = block_from(self.runtime, "function entryState(")
        for field in ("label", "visible", "enabled", "busy", "role", "order", "hint"):
            with self.subTest(field=field):
                self.assertIn(field + ":", body, f"entryState 字段契约不得改动：{field}")

    # ------------------------------------------------ 契约 C：refresh 失败不静默
    def test_refresh_caller_does_not_swallow_errors(self):
        body = block_from(self.chat, "function refreshBoardAfterUpload(")
        self.assertTrue(body, "找不到 refreshBoardAfterUpload()")
        self.assertNotRegex(body, r"\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)",
                            "refresh 失败不得再被空 catch 吞掉")
        # 契约更新（会话卡片降噪批次）：看板失败统一走 boardFailureNotice 出口（内部仍是
        # pushSystem 的普通输出，预期内失败才提前返回），刷新失败不得被静默吞掉。
        self.assertRegex(body, r"boardFailureNotice\(|pushSystem\(|noteInThread\(",
                         "refresh 失败必须以普通输出写进会话")

    # ------------------------------------------------ 保护边界
    def test_result_chips_stay_pinned(self):
        result = re.search(r"\.oc-result-actions\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(result, "结果入口规则被删除")
        self.assertRegex(result.group(1), r"order:\s*1",
                         "结果入口常驻底部是有意设计，本批不动")

    def test_protocol_and_bridge_untouched(self):
        for name in ("READY", "ACTION_STATE", "TASK_PROGRESS", "TASK_COMPLETED",
                     "TASK_FAILED", "SELECTION_CHANGED", "BOARD_STATUS"):
            with self.subTest(name=name):
                self.assertIn(name + ":", self.bridge, f"桥事件白名单被改动：{name}")
        self.assertNotIn("setInterval", self.runtime,
                         "运行时仍不得新增轮询")
        for route in ('/api/projects/{project_id}/cost-review/confirm',
                      '/api/projects/{project_id}/integration/process/confirm'):
            with self.subTest(route=route):
                self.assertIn(route, self.main, f"后端路由不得减少：{route}")


if __name__ == "__main__":
    unittest.main()
