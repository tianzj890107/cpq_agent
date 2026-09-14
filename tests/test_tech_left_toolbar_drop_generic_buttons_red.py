"""红测：左侧会话操作栏只留当前步骤业务动作，去掉通用刷新与导航按钮。

现状缺口（实测）：
  · `tech-workbench.html:121-124` 静态放了 `#techChatPrev` / `#techChatNext` /
    `#techChatTransfer` / `#techChatRetry` 四个通用按钮，与顶部流程条的
    `#techPrev` / `#techNext` / 大步骤按钮 / 子页签重复。
  · 每个 stage 页的刷新动作 `getState()` 都是 `visible: true`，于是左侧多出一颗
    「刷新需求看板 / 刷新整合看板 / 刷新成本看板 / 刷新汇总报告 / …」。这些是看板内部
    刷新通道（`refresh-data`、Agent 工具在用），不是给人点的业务按钮。
  · `tech-workbench.js` 还用 `STAGE_CHAT_FLOW` + `chatFlow()` 决定操作栏显隐并驱动那四个按钮。

本批只做「减法」：四个通用按钮与刷新入口退出左侧栏；动作本体、`refresh-data` 命令、
顶部流程条、输入区 ＋、结果入口与任务进度卡一律保留。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
WB_HTML = F / "tech-workbench.html"
WB_JS = F / "tech-workbench.js"
RUNTIME = F / "tech-board-runtime.js"
BRIDGE = F / "tech-board-bridge.js"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

GENERIC_IDS = ("techChatPrev", "techChatNext", "techChatTransfer", "techChatRetry")

# 每页那颗「刷新…看板」动作：只退出左侧栏（visible: false），动作本体必须保留。
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


class TechLeftToolbarDropGenericButtonsRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read(WB_HTML)
        cls.js = read(WB_JS)
        cls.runtime = read(RUNTIME)
        cls.bridge = read(BRIDGE)
        cls.main = read(MAIN)

    # ------------------------------------------------ 契约 A：四个通用按钮删除
    def test_generic_buttons_removed_from_html(self):
        for token in GENERIC_IDS:
            with self.subTest(token=token):
                self.assertNotIn(token, self.html,
                                 "左侧会话操作栏不再保留通用导航 / 转交 / 重试按钮")

    def test_primary_slot_and_other_hosts_are_kept(self):
        for token in ("techChatPrimary", "ocChatAttachBtn", "ocChatFileInput",
                      "ocResultActions", "ocTaskProgressHost",
                      "techPrev", "techNext", "techNowLabel"):
            with self.subTest(token=token):
                self.assertIn(token, self.html, f"既有入口不得被这次减法删掉：{token}")

    # ------------------------------------------------ 契约 B：父壳删除对应接线
    def test_parent_js_no_longer_drives_generic_buttons(self):
        for token in GENERIC_IDS:
            with self.subTest(token=token):
                self.assertNotIn(token, self.js, "父壳不得再渲染 / 绑定这四个按钮")

    def test_transfer_and_retry_helpers_are_retired(self):
        for token in ("transferCurrentTask", "replayLastBoardAction",
                      "lastBoardAction", "lastActionFailed"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.js, f"{token} 只服务已退役按钮，必须清掉")

    def test_stage_flow_table_retired(self):
        for token in ("STAGE_CHAT_FLOW", "chatFlow("):
            with self.subTest(token=token):
                self.assertNotIn(token, self.js,
                                 "壳里的阶段导航表已退役：显隐由看板动作快照决定")

    def test_action_bar_visibility_driven_by_board_actions(self):
        body = block_from(self.js, "function syncChatActions(")
        self.assertTrue(body, "找不到 syncChatActions()")
        self.assertIn("boardActionEntries(", body, "显隐与渲染都必须来自看板动作快照")
        self.assertIn("entries.length", body, "没有可见业务动作时才隐藏操作栏")
        self.assertIn("syncChatActionList(", body, "其余业务动作仍紧随主按钮渲染")
        self.assertNotIn("chatFlow(", body, "显隐不再依赖壳里的阶段导航表")

    def test_business_action_exit_is_unchanged(self):
        body = block_from(self.js, "function runBoardAction(")
        self.assertTrue(body, "找不到 runBoardAction()")
        self.assertRegex(body, r"executeAction\(", "业务动作仍只经 TechBoardBridge.executeAction")
        for bad in ("contentDocument", "contentWindow.document"):
            self.assertNotIn(bad, self.js, f"父壳不得查询 iframe DOM：{bad}")

    # ------------------------------------------------ 契约 C：刷新动作退出左侧栏
    def test_refresh_actions_are_hidden_from_toolbar(self):
        for path, marker in REFRESH_ACTIONS:
            with self.subTest(file=path.name, action=marker):
                source = read(path)
                self.assertIn(marker, source, f"{path.name} 的刷新动作被删除")
                body = block_from(source, marker)
                self.assertTrue(body, f"{path.name} 找不到 {marker}")
                self.assertRegex(body, r"visible:\s*false",
                                 "刷新动作只退出左侧栏：getState 必须返回 visible: false")

    def test_refresh_actions_still_registered(self):
        for path, marker in REFRESH_ACTIONS:
            with self.subTest(file=path.name, action=marker):
                source = read(path)
                self.assertIn("registerActions", source,
                              f"{path.name} 的动作注册表不得被清空")
                self.assertRegex(source, re.escape(marker) + r"\s*\{[\s\S]{0,400}?run:",
                                 "刷新动作必须仍然带 run()：Agent 与看板内部还要用")

    def test_refresh_channel_is_untouched(self):
        self.assertIn("refresh-data", self.runtime, "运行时 refresh-data 命令不得删除")
        self.assertIn("refreshData", self.bridge, "桥的 refreshData 入口不得删除")
        self.assertIn("refreshName", self.runtime, "refresh-data 的 name 解析不得删除")

    # ------------------------------------------------ 保护边界
    def test_no_backend_or_protocol_change(self):
        for token in ("techChatActions", "tech-board-action", "techChatPrev"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.main, "不得为左侧入口新增后端路由")
        for name in ("READY", "ACTION_STATE", "TASK_PROGRESS", "TASK_COMPLETED",
                     "TASK_FAILED", "SELECTION_CHANGED", "BOARD_STATUS"):
            with self.subTest(name=name):
                self.assertIn(name + ":", self.bridge, f"桥事件白名单被改动：{name}")


if __name__ == "__main__":
    unittest.main()
