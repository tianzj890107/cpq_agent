"""红测：2.2 左侧操作栏去掉 Agent 专用单步动作与重复的「整合图纸」入口。

现状缺口（实测）：
  · `assembly-integration.js:1623` 的 `integrationStep`（运行整合环节）`getState()` 返回
    `visible: true`，被父壳 `boardActionEntries()` 渲染成左侧按钮；但它的 `run()` 必须拿到
    `payload.step`，而父壳 `runBoardAction()` 只发 `{ label, role }`（`tech-workbench.js:602`），
    用户点它必然落到 `bad-step`「step 只能是 params / process」—— 一个对用户永远失败的按钮。
    它的真实调用方是 Agent 工具 RequestIntegrationStep → tech_ui "integration-step"。
  · `assembly-integration.js:1649` 的 `openIntegrationDrawings`（整合图纸）同样 `visible: true`，
    而标题行右侧子页签（`tech-workbench.js:61-66` 的 CHILD_TAB_PROXY.process.tabs）已经是
    同一目标的可见入口 → 一个界面里「整合图纸」出现两次。
    它的真实调用方是 Agent 工具 UploadIntegrationDrawing → tech_ui "open-integration-drawings"。

本批只改这两个条目的可见性（改 `visible: false`，与既有 refreshIntegration 同一通道）；
Agent 两条工具链路、run() 实现、其余动作与会话/看板协议一律不动。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
BOARD = F / "assembly-integration.js"
CHAT = F / "agent-chat.js"
PARENT = F / "tech-workbench.js"
BRIDGE = F / "tech-board-bridge.js"
RUNTIME = F / "tech-board-runtime.js"
AGENT = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 这两个条目只给 Agent 工具链用，不该出现在左侧操作栏。
AGENT_ONLY_ACTIONS = ("integrationStep", "openIntegrationDrawings")
# 2.2 真正的用户动作：动作本身保留，但可见性由所属页签决定（本批起不做全页签常驻）。
USER_ACTIONS = ("runIntegration", "sendIntegrationToFinance")
# 页签专属动作：visible 仍由当前页签决定。
# 第 30 批（参数推荐页只留一颗主按钮）之后，参数页的 save / confirm / autofill /
# finalize 五颗已并入「生成参数推荐」与「确认并进入下一步」两条链路，改为 visible: false；
# 第 31 批（组装工艺页只留一颗主按钮）又把「确认组装工艺」并进「确认并进入下一步」，
# 同样改为 visible: false。这里只保留仍然按页签显示的两个生成动作，其余由
# tests/test_integration_params_tab_single_primary_and_auto_fill_red.py 与
# tests/test_integration_process_tab_single_primary_and_next_step_red.py 负责守住。
TAB_SCOPED_ACTIONS = ("generateIntegrationParams", "generateIntegrationProcess")


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
                    break
                i += 1
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


class IntegrationLeftToolbarDropAgentOnlyRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = read(BOARD)
        cls.chat = read(CHAT)
        cls.parent = read(PARENT)
        cls.bridge = read(BRIDGE)
        cls.runtime = read(RUNTIME)
        cls.agent = read(AGENT)
        cls.main = read(MAIN)

    # ------------------------------------------------- 契约 A：左侧栏不再露出
    def test_integration_step_is_hidden_from_left_bar(self):
        block = block_from(self.board, "integrationStep:")
        self.assertTrue(block, "找不到 integrationStep 动作条目")
        self.assertRegex(block, r"visible:\s*false",
                         "运行整合环节是 Agent 专用单步动作，必须对用户隐藏（visible: false）")
        self.assertNotRegex(block, r"visible:\s*true",
                            "运行整合环节不得再对左侧操作栏可见")

    def test_open_integration_drawings_is_hidden_from_left_bar(self):
        block = block_from(self.board, "openIntegrationDrawings:")
        self.assertTrue(block, "找不到 openIntegrationDrawings 动作条目")
        self.assertRegex(block, r"visible:\s*false",
                         "整合图纸在标题行已有子页签入口，左侧栏必须隐藏（visible: false）")
        self.assertNotRegex(block, r"visible:\s*true",
                            "整合图纸不得在左侧操作栏重复出现")

    def test_hidden_entries_use_the_same_channel_as_refresh(self):
        for name in AGENT_ONLY_ACTIONS + ("refreshIntegration",):
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertTrue(block, f"找不到 {name} 动作条目")
                self.assertRegex(block, r"visible:\s*false",
                                 f"{name} 必须与刷新动作走同一条 visible: false 通道")

    def test_parent_left_bar_filters_invisible_actions(self):
        self.assertIn("entry.visible === false", self.parent,
                      "父壳仍须以 visible === false 为唯一跳过条件")
        self.assertNotIn("integrationStep", self.parent,
                         "父壳不得靠动作名黑名单来隐藏条目")
        self.assertNotIn("openIntegrationDrawings", self.parent,
                         "父壳不得靠动作名黑名单来隐藏条目")

    # ------------------------------------------------- 契约 B：Agent 能力不缩水
    def test_agent_tool_chain_still_reaches_the_board(self):
        for ui_action, board_action in (("integration-step", "integrationStep"),
                                        ("open-integration-drawings", "openIntegrationDrawings")):
            with self.subTest(ui_action=ui_action):
                self.assertIn(f'"{ui_action}"', self.chat,
                              f"左侧 ui_action 分发被删除：{ui_action}")
                self.assertRegex(
                    self.chat, rf'executeAction\(\s*"{board_action}"',
                    f"左侧不再经桥触发看板动作：{board_action}")

    def test_backend_tool_mapping_unchanged(self):
        self.assertIn('"RequestIntegrationStep": "integration-step"', self.agent,
                      "后端工具 RequestIntegrationStep 的映射被改动")
        self.assertIn('"UploadIntegrationDrawing": "open-integration-drawings"', self.agent,
                      "后端工具 UploadIntegrationDrawing 的映射被改动")

    def test_run_implementations_are_kept(self):
        step_block = block_from(self.board, "integrationStep:")
        self.assertIn("aiIntegrationStepInBackground(", step_block,
                      "运行整合环节的既有实现不得被删")
        self.assertIn("deferred: true", step_block,
                      "运行整合环节仍是长任务（deferred）")
        drawings_block = block_from(self.board, "openIntegrationDrawings:")
        self.assertIn("aiSetTab('drawings')", drawings_block,
                      "整合图纸动作仍须切到 drawings 页签")
        self.assertIn("focus()", drawings_block,
                      "整合图纸动作仍须聚焦上传入口")
        self.assertRegex(drawings_block, r"ok:\s*true",
                         "整合图纸动作仍须返回结构化成功")

    def test_labels_and_orders_unchanged(self):
        for name, label, order in (("integrationStep", "运行整合环节", "120"),
                                   ("openIntegrationDrawings", "整合图纸", "130")):
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertIn(label, block, f"{name} 的 label 被改动")
                self.assertIn(f"order: {order}", block, f"{name} 的 order 被改动")

    # ------------------------------------------------- 契约 C：用户动作与可见入口
    def test_user_facing_integration_actions_stay_visible(self):
        # 契约更新（第 30 批）：这两个动作仍注册、仍可执行，但不再全页签常驻 ——
        # 「开始整合分析」只属于整合图纸页，「确认工艺并发送财务」只属于组装工艺页。
        for name, tab in (("runIntegration", "drawings"),
                          ("sendIntegrationToFinance", "process")):
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertTrue(block, f"找不到 {name} 动作条目")
                self.assertRegex(block, rf"aiTab === '{tab}'[\s\S]{{0,160}}visible",
                                 f"{name} 的可见性必须绑定到它所属的页签 {tab}")
                self.assertNotRegex(block, r"visible:\s*true",
                                    f"{name} 不得再在其它页签常驻")
                self.assertIn("run:", block, f"{name} 的实现不得被删")

    def test_tab_scoped_actions_stay_visible_by_tab(self):
        for name in TAB_SCOPED_ACTIONS:
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertTrue(block, f"找不到 {name} 动作条目")
                self.assertRegex(block, r"visible:\s*show",
                                 f"{name} 仍须按当前页签决定可见性")

    def test_child_tab_proxy_still_owns_the_drawings_entry(self):
        block = block_from(self.parent, "CHILD_TAB_PROXY")
        self.assertIn("'process'", block, "2.2 的子页签代理被删除")
        for key, label in (("drawings", "整合图纸"), ("params", "参数推荐"), ("process", "组装工艺")):
            with self.subTest(tab=key):
                self.assertRegex(block, rf"key:\s*'{key}',\s*label:\s*'{label}'",
                                 f"标题行子页签 {label} 被删除或改名")

    def test_views_registration_unchanged(self):
        block = block_from(self.board, "registerViews(")
        self.assertTrue(block, "找不到 registerViews 注册块")
        for view in ("drawings", "params", "process"):
            with self.subTest(view=view):
                self.assertRegex(block, rf"{view}\s*:", f"内部视图 {view} 被删除")

    # ------------------------------------------------- 保护边界
    def test_protocol_and_backend_routes_untouched(self):
        for name in ("READY", "ACTION_STATE", "TASK_PROGRESS", "TASK_COMPLETED",
                     "TASK_FAILED", "SELECTION_CHANGED", "BOARD_STATUS"):
            with self.subTest(name=name):
                self.assertIn(name + ":", self.bridge, f"桥事件白名单被改动：{name}")
        for route in ("/integration", "/integration/drawings", "/integration/params",
                      "/integration/process", "/integration/confirm",
                      "/integration/send-to-finance"):
            with self.subTest(route=route):
                self.assertIn(route, self.main, f"后端 integration 路由被删除：{route}")

    def test_runtime_and_silent_contract_untouched(self):
        self.assertIn("entry.silent === true", self.runtime, "silent 出口被改动")
        self.assertIn("entry.deferred === true", self.runtime, "deferred 语义被改动")


if __name__ == "__main__":
    unittest.main()
