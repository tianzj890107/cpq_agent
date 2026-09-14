"""红测：2.2「参数推荐」页只留一颗主按钮、生成即补全必填、确认后进入下一步。

现状缺口（实测）：
  · 站在参数页（`aiTab === 'params'`）时，`assembly-integration.js` 的 registerActions 会把
    `runIntegration`（开始整合分析）、`sendIntegrationToFinance`（确认工艺并发送财务）、
    `generateIntegrationParams`（生成参数推荐，role 却是 aux）、`saveIntegrationParams`（保存参数）、
    `confirmIntegrationParams`（确认参数推荐）、`autofillIntegrationParams`（智能补全）、
    `saveIntegrationParamsFinal`（保存补填）、`confirmIntegrationParamsFinal`（确认参数已齐）
    一起渲染成左侧按钮 —— 本页真正的起点「生成参数推荐」被挤成次按钮，analysis 完成后主按钮还被
    财务动作抢走（它的闸门 `aiFinanceBlocker()` 在参数页必然是死按钮）。
  · 报价必填缺口要用户自己点三次（智能补全 → 保存补填 → 确认参数已齐），任一步没点，
    发送财务时就被 `aiFinanceBlocker()` 挡住。
  · 没有「确认并进入下一步」这个动作：确认参数已齐、确认参数推荐、切到组装工艺要三步。

本批只改 2.2 左侧可见性与参数页两条链路（全部复用既有实现与既有接口）：
被隐藏的动作继续注册、继续可执行；后端路由、桥协议、右侧看板一律不动。
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
EMBED = F / "tech-embed.js"
AGENT = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 参数页只允许这两颗可见动作。
PARAMS_VISIBLE = ("generateIntegrationParams", "confirmParamsAndNext")
# 参数页必须消失的动作（注册与实现都要保留，只是 visible: false）。
PARAMS_HIDDEN = ("saveIntegrationParams", "confirmIntegrationParams", "autofillIntegrationParams",
                 "saveIntegrationParamsFinal", "confirmIntegrationParamsFinal")
# 上一批已隐藏的 Agent 专用入口。
AGENT_ONLY = ("integrationStep", "openIntegrationDrawings")
# 隐藏动作的既有实现，一个都不能删。
KEPT_IMPLS = {
    "saveIntegrationParams": "aiSaveEdits('params')",
    "confirmIntegrationParams": "aiConfirmStep('params')",
    "autofillIntegrationParams": "aiParamsAutofill()",
    "saveIntegrationParamsFinal": "aiParamsFinalize(false)",
    "confirmIntegrationParamsFinal": "aiParamsFinalize(true)",
}


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


class IntegrationParamsTabSinglePrimaryRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = read(BOARD)
        cls.chat = read(CHAT)
        cls.parent = read(PARENT)
        cls.bridge = read(BRIDGE)
        cls.embed = read(EMBED)
        cls.agent = read(AGENT)
        cls.main = read(MAIN)

    # ------------------------------------------- 契约 A：参数页左侧可见动作
    def test_generate_params_is_visible_on_params_tab(self):
        block = block_from(self.board, "generateIntegrationParams:")
        self.assertTrue(block, "找不到 generateIntegrationParams 动作条目")
        self.assertRegex(block, r"aiTab === 'params'[\s\S]{0,120}visible",
                         "生成参数推荐必须绑定到参数推荐页签")
        self.assertNotRegex(block, r"visible:\s*false",
                            "生成参数推荐是参数页的入口，不得隐藏")

    def test_confirm_and_next_action_is_registered(self):
        block = block_from(self.board, "confirmParamsAndNext:")
        self.assertTrue(block, "缺少 confirmParamsAndNext 动作（确认并进入下一步）")
        # 第 33 批反转：参数页的收口动作文案由「确认并进入下一步」改为「确认并进入下一页签」，
        # 与组装工艺页（仍叫「确认并进入下一步」）区分开；role / order / 实现不变。
        self.assertIn("确认并进入下一页签", block, "动作 label 必须是「确认并进入下一页签」")
        self.assertRegex(block, r"visible:\s*aiTab === 'params' && aiHasParams\(\)",
                         "确认并进入下一步只在参数页且已生成参数时出现")
        self.assertRegex(block, r"role:\s*'primary'", "确认并进入下一步必须是主按钮")
        self.assertRegex(block, r"order:\s*35", "确认并进入下一步的 order 应为 35")

    def test_generate_params_is_primary_until_params_exist(self):
        block = block_from(self.board, "generateIntegrationParams:")
        self.assertRegex(block, r"role:\s*aiHasParams\(\)\s*\?\s*'aux'\s*:\s*'primary'",
                         "未生成参数时生成参数推荐必须是主按钮，生成后让位给确认并进入下一步")

    def test_removed_params_actions_are_hidden(self):
        for name in PARAMS_HIDDEN:
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertTrue(block, f"找不到 {name} 动作条目")
                self.assertRegex(block, r"visible:\s*false",
                                 f"{name} 不该再出现在参数页左侧操作栏")
                self.assertNotRegex(block, r"visible:\s*(?:show|true)",
                                    f"{name} 的可见性必须关掉")

    def test_agent_only_actions_stay_hidden(self):
        for name in AGENT_ONLY:
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertRegex(block, r"visible:\s*false", f"{name} 仍须对用户隐藏")

    def test_run_and_finance_actions_are_tab_scoped(self):
        run_block = block_from(self.board, "runIntegration:")
        self.assertRegex(run_block, r"aiTab === 'drawings'[\s\S]{0,160}visible",
                         "开始整合分析只属于「整合图纸」页")
        self.assertNotRegex(run_block, r"visible:\s*true",
                            "开始整合分析不得再在参数页出现")
        finance_block = block_from(self.board, "sendIntegrationToFinance:")
        self.assertRegex(finance_block, r"aiTab === 'process'[\s\S]{0,160}visible",
                         "确认工艺并发送财务只属于「组装工艺」页")
        self.assertNotRegex(finance_block, r"visible:\s*true",
                            "确认工艺并发送财务不得在参数页占位抢主按钮")

    def test_params_helpers_are_shared(self):
        for helper in ("aiHasParams", "aiHasProcess"):
            with self.subTest(helper=helper):
                self.assertRegex(self.board, rf"const {helper} = \(\)",
                                 f"缺少共享判定 {helper}()")

    # ------------------------------------------- 契约 B：生成参数推荐 = 完整链路
    def test_generate_params_calls_the_full_chain(self):
        block = block_from(self.board, "generateIntegrationParams:")
        self.assertIn("aiGenerateParamsFully()", block,
                      "生成参数推荐必须走完整链路（生成 → 自动补全 → 落库）")
        body = block_from(self.board, "async function aiGenerateParamsFully()")
        self.assertTrue(body, "找不到 aiGenerateParamsFully()")
        self.assertIn("aiGenerate('params')", body, "第一步必须复用既有生成实现")
        self.assertIn("aiParamsAutofill()", body, "第二步必须复用既有智能补全接口")
        self.assertIn("aiParamsFinalize(false)", body, "第三步必须复用既有 finalize 落库")

    def test_auto_fill_only_saves_when_it_filled_something(self):
        body = block_from(self.board, "async function aiGenerateParamsFully()")
        self.assertRegex(body, r"applied\s*>\s*0",
                         "只有真的补进了值才需要落库，空补全不写库")
        self.assertIn("aiRequiredGaps()", body, "必须先看报价必填缺口")

    def test_autofill_reports_what_it_filled(self):
        body = block_from(self.board, "async function aiParamsAutofill()")
        self.assertRegex(body, r"return \{ applied:",
                         "aiParamsAutofill 必须把补了几项返回给调用方")
        self.assertIn("unresolved", body, "推不出来的项仍要如实返回")

    def test_generate_chain_reports_gaps_in_chat(self):
        body = block_from(self.board, "async function aiGenerateParamsFully()")
        self.assertIn("aiSay(", body, "生成完要在会话里总结填了什么、还缺什么")
        self.assertIn("required_missing", body, "总结必须包含剩余必填缺口")

    def test_generate_failure_stops_the_chain(self):
        body = block_from(self.board, "async function aiGenerateParamsFully()")
        self.assertRegex(body, r"ok === false[\s\S]{0,200}return",
                         "生成失败必须直接返回，不继续补全 / 落库")
        self.assertIn("no-params", body, "没有产出参数时要有结构化失败")

    # ------------------------------------------- 契约 C：确认并进入下一步
    def test_confirm_and_next_reuses_existing_pipeline(self):
        body = block_from(self.board, "async function aiConfirmParamsAndNext()")
        self.assertTrue(body, "找不到 aiConfirmParamsAndNext()")
        self.assertIn("aiParamsFinalize(true)", body, "必须先走既有「确认参数已齐」")
        self.assertIn("aiConfirmStep('params')", body, "再走既有「确认参数推荐」")
        self.assertIn("aiSetTab('process')", body, "成功后切到下一步「组装工艺」")

    def test_confirm_and_next_invents_no_new_endpoint(self):
        body = block_from(self.board, "async function aiConfirmParamsAndNext()")
        for token in ("fetch(", "api(", "/integration/", "/api/projects/"):
            with self.subTest(token=token):
                self.assertNotIn(token, body,
                                 f"确认并进入下一步不得自己发请求（{token}），只能复用既有函数")

    def test_confirm_and_next_fails_loudly_when_required_missing(self):
        body = block_from(self.board, "async function aiConfirmParamsAndNext()")
        self.assertIn("params_final", body, "必须检查后端是否真的把参数最终确认了")
        self.assertIn("required-missing", body, "必填不齐时必须返回结构化失败")
        self.assertIn("ok: false", body, "失败要如实回结构化失败，不伪装成功")

    def test_confirm_and_next_switches_tab_only_after_confirm(self):
        body = block_from(self.board, "async function aiConfirmParamsAndNext()")
        idx_confirm = body.find("params_confirmed")
        idx_switch = body.find("aiSetTab('process')")
        self.assertGreater(idx_confirm, -1, "必须检查 params_confirmed")
        self.assertGreater(idx_switch, idx_confirm,
                           "只有确认通过之后才允许切到组装工艺")

    def test_confirm_and_next_guards_busy_and_missing_params(self):
        body = block_from(self.board, "async function aiConfirmParamsAndNext()")
        self.assertIn("busy", body, "并发时要返回结构化 busy")
        self.assertIn("aiHasParams()", body, "未生成参数时要挡住并给出真实原因")

    # ------------------------------------------- 契约 D：能力不缩水
    def test_hidden_actions_keep_their_implementations(self):
        for name, call in KEPT_IMPLS.items():
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertIn(call, block, f"{name} 的既有实现不得被删（{call}）")

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
        self.assertNotIn("confirmParamsAndNext", self.parent,
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
        for route in ("/integration/params", "/integration/params/confirm",
                      "/integration/params/autofill", "/integration/params/finalize"):
            with self.subTest(route=route):
                self.assertIn(route, self.main, f"后端 integration 参数路由被删除：{route}")

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
