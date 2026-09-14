"""红测：2.2「组装工艺」页只留一颗主按钮，确认后进入下一步（2.3 成本测算）。

现状缺口（实测）：
  · 站在组装工艺页（`aiTab === 'process'`）时，`assembly-integration.js` 会把
    `generateIntegrationProcess`（生成组装工艺）渲染成次按钮（role: aux），却把
    `confirmIntegrationProcess`（确认组装工艺）单独列一颗（order 70）—— 本页第一步反而
    不是主按钮。
  · 更关键的是：确认组装工艺之后没有任何通往下一步（2.3 成本测算）的入口，
    用户只能自己去找顶部流程条切步骤。

本批只改组装工艺页的两颗按钮：生成组装工艺的 role 随 `aiHasProcess()` 反转、
「确认组装工艺」并进新的「确认并进入下一步」（确认 → 走既有嵌入通道切到 cost）。
被隐藏的动作继续注册、继续可执行；「确认工艺并发送财务」保持原样（交接要人工选接收人）；
后端路由、桥协议、右侧看板一律不动。
"""
from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
BOARD = F / "assembly-integration.js"
CHAT = F / "agent-chat.js"
PARENT = F / "tech-workbench.js"
BRIDGE = F / "tech-board-bridge.js"
EMBED = F / "tech-embed.js"
AGENT = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 组装工艺页必须消失的动作（注册与实现都要保留，只是 visible: false）。
PROCESS_HIDDEN = ("confirmIntegrationProcess",)
# 上一批已隐藏的 Agent 专用入口。
AGENT_ONLY = ("integrationStep", "openIntegrationDrawings")


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


class IntegrationProcessTabSinglePrimaryRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = read(BOARD)
        cls.chat = read(CHAT)
        cls.parent = read(PARENT)
        cls.bridge = read(BRIDGE)
        cls.embed = read(EMBED)
        cls.agent = read(AGENT)
        cls.main = read(MAIN)

    # ------------------------------------------- 契约 A：组装工艺页可见动作
    def test_generate_process_is_visible_on_process_tab(self):
        block = block_from(self.board, "generateIntegrationProcess:")
        self.assertTrue(block, "找不到 generateIntegrationProcess 动作条目")
        self.assertRegex(block, r"aiTab === 'process'[\s\S]{0,120}visible",
                         "生成组装工艺必须绑定到组装工艺页签")
        self.assertNotRegex(block, r"visible:\s*false",
                            "生成组装工艺是组装工艺页的入口，不得隐藏")
        self.assertIn("aiGenerate('process')", block,
                      "生成组装工艺必须继续复用既有实现 aiGenerate('process')")

    def test_confirm_process_and_next_action_is_registered(self):
        block = block_from(self.board, "confirmProcessAndNext:")
        self.assertTrue(block, "缺少 confirmProcessAndNext 动作（确认并进入下一步）")
        self.assertIn("确认并进入下一步", block, "动作 label 必须是「确认并进入下一步」")
        self.assertRegex(block, r"visible:\s*aiTab === 'process' && aiHasProcess\(\)",
                         "确认并进入下一步只在组装工艺页且已生成工艺时出现")
        self.assertRegex(block, r"role:\s*aiHasProcess\(\)\s*\?\s*'primary'\s*:\s*'aux'",
                         "确认并进入下一步必须是本页主按钮")
        self.assertRegex(block, r"order:\s*45", "确认并进入下一步的 order 应为 45")
        self.assertIn("aiConfirmProcessAndNext()", block,
                      "动作必须走新增的确认链路函数")

    def test_generate_process_is_primary_until_process_exists(self):
        block = block_from(self.board, "generateIntegrationProcess:")
        self.assertRegex(block, r"role:\s*aiHasProcess\(\)\s*\?\s*'aux'\s*:\s*'primary'",
                         "未生成工艺时生成组装工艺必须是主按钮，生成后让位给确认并进入下一步")

    def test_confirm_integration_process_is_hidden(self):
        for name in PROCESS_HIDDEN:
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertTrue(block, f"找不到 {name} 动作条目")
                self.assertRegex(block, r"visible:\s*false",
                                 f"{name} 不该再出现在组装工艺页左侧操作栏")
                self.assertNotRegex(block, r"visible:\s*(?:show|true)",
                                    f"{name} 的可见性必须关掉")

    def test_confirm_hidden_action_keeps_its_implementation(self):
        block = block_from(self.board, "confirmIntegrationProcess:")
        self.assertIn("aiConfirmStep('process')", block,
                      "确认组装工艺的既有实现不得被删（Agent / 内部链路仍在用）")

    def test_agent_only_actions_stay_hidden(self):
        for name in AGENT_ONLY:
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertRegex(block, r"visible:\s*false", f"{name} 仍须对用户隐藏")

    def test_start_integration_analysis_is_not_on_process_tab(self):
        block = block_from(self.board, "runIntegration:")
        self.assertRegex(block, r"aiTab === 'drawings'[\s\S]{0,160}visible",
                         "开始整合分析只属于「整合图纸」页，组装工艺页不得出现")
        self.assertNotRegex(block, r"visible:\s*true",
                            "开始整合分析不得再在组装工艺页出现")

    def test_finance_handoff_stays_on_process_tab(self):
        block = block_from(self.board, "sendIntegrationToFinance:")
        self.assertRegex(block, r"aiTab === 'process'[\s\S]{0,160}visible",
                         "确认工艺并发送财务仍属于组装工艺页（人工交接入口）")
        self.assertIn("aiFinanceBlocker()", block, "财务闸门不得被改动或删除")
        self.assertIn("aiOpenFinanceDialog()", block, "发送财务弹窗不得被删除")

    def test_process_helper_is_shared(self):
        self.assertRegex(self.board, r"const aiHasProcess = \(\)",
                         "缺少共享判定 aiHasProcess()")

    # ------------------------------------------- 契约 B：确认并进入下一步
    def test_confirm_and_next_confirms_then_navigates(self):
        body = block_from(self.board, "async function aiConfirmProcessAndNext()")
        self.assertTrue(body, "找不到 aiConfirmProcessAndNext()")
        self.assertIn("aiConfirmStep('process')", body, "必须先走既有「确认组装工艺」")
        self.assertIn("techGoNextStage('cost')", body,
                      "确认通过后必须切到下一步 2.3 成本测算")

    def test_next_stage_helper_reuses_the_embed_channel(self):
        body = block_from(self.board, "function techGoNextStage(")
        self.assertTrue(body, "找不到 techGoNextStage()")
        self.assertIn("TechEmbed", body, "切步骤必须复用既有嵌入通道")
        self.assertIn("TechEmbed.requestNavigate(", body,
                      "必须经既有 requestNavigate 请求父壳切换 stage")
        self.assertIn("techGoNextStage('cost')", self.board,
                      "2.2 组装工艺页的下一步是 cost（2.3 成本测算）")
        self.assertNotIn("postMessage", body,
                         "不得自己拼 postMessage，导航只有既有那一条通道")

    def test_confirm_and_next_invents_no_new_endpoint(self):
        body = block_from(self.board, "async function aiConfirmProcessAndNext()")
        for token in ("fetch(", "api(", "/integration/", "/api/projects/"):
            with self.subTest(token=token):
                self.assertNotIn(token, body,
                                 f"确认并进入下一步不得自己发请求（{token}），只能复用既有函数")

    def test_confirm_and_next_fails_loudly(self):
        body = block_from(self.board, "async function aiConfirmProcessAndNext()")
        self.assertIn("process_confirmed", body, "必须检查后端是否真的确认了组装工艺")
        self.assertIn("confirm-failed", body, "确认没过时必须返回结构化失败")
        self.assertIn("ok: false", body, "失败要如实回结构化失败，不伪装成功")

    def test_confirm_and_next_switches_stage_only_after_confirm(self):
        body = block_from(self.board, "async function aiConfirmProcessAndNext()")
        idx_confirm = body.find("process_confirmed")
        idx_switch = body.find("techGoNextStage('cost')")
        self.assertGreater(idx_confirm, -1, "必须检查 process_confirmed")
        self.assertGreater(idx_switch, idx_confirm,
                           "只有确认通过之后才允许切到 2.3")

    def test_confirm_and_next_guards_busy_and_missing_process(self):
        body = block_from(self.board, "async function aiConfirmProcessAndNext()")
        self.assertIn("busy", body, "并发时要返回结构化 busy")
        self.assertIn("aiHasProcess()", body, "未生成工艺时要挡住并给出真实原因")
        self.assertIn("no-process", body, "未生成工艺时要有结构化失败码")

    def test_confirm_and_next_tells_user_about_the_handoff(self):
        body = block_from(self.board, "async function aiConfirmProcessAndNext()")
        self.assertIn("aiSay(", body, "进入下一步要在会话里说明发生了什么")
        self.assertIn("确认工艺并发送财务", body,
                      "还没交给财务时要提示去点「确认工艺并发送财务」")

    # ------------------------------------------- 契约 C：能力不缩水
    def test_agent_actions_and_views_unchanged(self):
        self.assertIn('"integration-step"', self.chat, "Agent ui_action 分发被删除")
        self.assertIn('"open-integration-drawings"', self.chat, "Agent ui_action 分发被删除")
        self.assertRegex(self.chat, r'executeAction\(\s*"integrationStep"',
                         "Agent 仍须经桥触发运行整合环节")
        views = block_from(self.board, "registerViews(")
        for view in ("drawings", "params", "process"):
            with self.subTest(view=view):
                self.assertRegex(views, rf"{view}\s*:", f"内部视图 {view} 被删除")
        for mapping in ('"RequestIntegrationStep": "integration-step"',
                        '"UploadIntegrationDrawing": "open-integration-drawings"'):
            with self.subTest(mapping=mapping):
                self.assertIn(mapping, self.agent, f"后端工具映射被改动：{mapping}")

    def test_parent_filter_and_primary_rule_unchanged(self):
        self.assertIn("entry.visible === false", self.parent,
                      "父壳仍以 visible === false 为唯一跳过条件")
        self.assertRegex(self.parent, r"role === 'primary'",
                         "主按钮仍只认看板声明的 role === 'primary'")
        self.assertNotIn("confirmProcessAndNext", self.parent,
                         "父壳不得为某个动作名写分支")

    def test_right_board_buttons_stay_hidden_in_embed(self):
        for selector in ("#aiActions", "#aiStart"):
            with self.subTest(selector=selector):
                hits = [line for line in self.embed.splitlines() if selector in line]
                self.assertTrue(hits, f"嵌入态未隐藏右侧看板按钮 {selector}")
                for line in hits:
                    self.assertIn(".tech-embed", line,
                                  f"{selector} 的隐藏规则必须限定在 .tech-embed 作用域")

    def test_backend_routes_untouched(self):
        for route in ("/integration/process", "/integration/process/confirm",
                      "/integration/send-to-finance"):
            with self.subTest(route=route):
                self.assertIn(route, self.main, f"后端 integration 工艺路由被删除：{route}")

    def test_left_pane_does_not_call_business_endpoints(self):
        self.assertNotIn("/integration", self.chat,
                         "左侧会话仍不得直接调用 integration 接口")

    def test_protocol_whitelist_untouched(self):
        for name in ("READY", "ACTION_STATE", "TASK_PROGRESS", "TASK_COMPLETED",
                     "TASK_FAILED", "SELECTION_CHANGED", "BOARD_STATUS"):
            with self.subTest(name=name):
                self.assertIn(name + ":", self.bridge, f"桥事件白名单被改动：{name}")


if __name__ == "__main__":
    unittest.main()
