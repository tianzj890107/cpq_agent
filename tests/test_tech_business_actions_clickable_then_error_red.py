"""红测：技术工艺业务动作按钮一律可点，点了再给真实原因。

现状缺口（实测）：
  · `assembly-integration.js:1513` 的 `sendIntegrationToFinance.getState()` 直接读页内按钮
    `enabled: Boolean(button) && !button.disabled`，而 `:847` 的 `financeBtn.disabled = !ready`
    要求四项前置条件同时成立 —— 少了任何一项，左侧「确认工艺并发送财务」永久灰掉，
    `run()` 里已经写好的结构化错误（`:1485` 的 not-ready）永远跑不到。
  · `cost-review.js:779` 的 `confirmCostReview.getState()` 同样读 `#crConfirm` 的 disabled，
    而 `:385` 的 `disabled = !ready` 一旦为真，左侧「确认成本」永久灰掉；
    `:387-389` 的写入数据库 / 回传销售经理继续报价 / 提交工艺经理确认又都要求 confirmed，
    于是整条「回传销售经理继续报价」链路走不下去 —— 这就是用户遇到的硬 bug。
  · 看板收尾不重新发布快照：`crConfirmCost()`（`:602`）与 `crRunOp()`（`:482`）的 finally
    只做 `crBusy = false; crRender();`，父壳缓存里的 enabled 一直停在上一次的值。
  · `cost-review.js:34` 的 `CR_COST_ROLES = ['finance_mgr','admin']` 与后端权威白名单
    `auth.py:55` 的 `COST_ROLES = {"finance_manager","admin"}` 不一致，财务负责人被误判只读。
  · 静默返回：`aiOpenFinanceDialog()`（`:907`）的 `if (aiBusy) return;`、
    父壳 `runBoardAction` 的 `if (!state.project) return;` —— 点了没反应。

本批只改「不让点的判定」与「点了以后怎么报错」：不改协议信封、不改后端权限与路由、
不改页内按钮自身的 disabled 语义。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
WB_JS = F / "tech-workbench.js"
RUNTIME = F / "tech-board-runtime.js"
ASSEMBLY = F / "assembly-integration.js"
COST = F / "cost-review.js"
CHAT = F / "agent-chat.js"
BRIDGE = F / "tech-board-bridge.js"
BACKEND_MAIN = ROOT / "tech_app" / "backend" / "main.py"
BACKEND_AUTH = ROOT / "tech_app" / "backend" / "services" / "auth.py"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
    """返回 marker 之后第一个配对大括号块（含大括号）。配对不上返回空串。"""
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


def arrow_bodies(text: str, marker: str) -> list[str]:
    """收集每个 marker（如 'getState:'）后面第一个配对大括号块。"""
    out = []
    for match in re.finditer(re.escape(marker), text):
        brace = text.find("{", match.end() - 1)
        if brace < 0:
            continue
        depth = 0
        i = brace
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append(text[brace:i + 1])
                    break
            i += 1
    return out


class TechBusinessActionsClickableRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read(WB_JS)
        cls.runtime = read(RUNTIME)
        cls.assembly = read(ASSEMBLY)
        cls.cost = read(COST)
        cls.chat = read(CHAT)
        cls.bridge = read(BRIDGE)
        cls.main = read(BACKEND_MAIN)
        cls.auth = read(BACKEND_AUTH)

    # ------------------------------------------------ 契约 A：父壳不再按 enabled === false 禁用
    def test_parent_toolbar_never_disables_on_enabled_false(self):
        body = block_from(self.js, "function syncChatActionList(")
        self.assertTrue(body, "找不到 syncChatActionList()")
        self.assertNotIn("entry.enabled === false", body,
                         "前置条件没满足也要能点：不能再按 enabled === false 灰掉按钮")
        self.assertRegex(body, r"entry\.busy\s*===\s*true",
                         "只有同一动作正在执行（busy）才禁用")

    def test_parent_primary_slot_never_disables_on_enabled_false(self):
        body = block_from(self.js, "function syncChatActions(")
        self.assertTrue(body, "找不到 syncChatActions()")
        self.assertNotIn("primaryEntry.enabled !== false", body,
                         "主按钮槽位同样不能再按 enabled 禁用")
        self.assertRegex(body, r"primaryEntry\.busy\s*!==\s*true",
                         "主按钮只在执行中禁用")

    def test_tooltip_no_longer_claims_unavailable(self):
        body = block_from(self.js, "function actionTooltip(")
        self.assertTrue(body, "找不到 actionTooltip()")
        self.assertNotIn("当前不可用", body,
                         "按钮还能点，tooltip 就不能宣称「当前不可用」")
        self.assertIn("执行中", body, "执行中这个真实状态要保留")

    def test_board_action_never_returns_silently(self):
        body = block_from(self.js, "function runBoardAction(")
        self.assertTrue(body, "找不到 runBoardAction()")
        self.assertNotRegex(body, r"if\s*\(\s*!state\.project\s*\)\s*return\s*;",
                            "没有项目时不得静默 return —— 点了必须给出原因")
        self.assertIn("请先打开项目", body, "没有项目要给出「请先打开项目」的可见提示")

    def test_board_action_failure_is_red_and_reaches_chat(self):
        body = block_from(self.js, "function runBoardAction(")
        self.assertRegex(body, r"setBoardNotice\([^)]*['\"]error['\"]",
                         "失败原因必须按 error 等级写进标题行提示位")
        self.assertIn("ocTechAgent", body,
                      "失败原因还必须同步一条左侧会话提示，不能只闪在标题行")

    def test_chat_exposes_notice_for_parent(self):
        self.assertRegex(self.chat, r"notice\s*:\s*pushSystem",
                         "agent-chat.js 的 window.ocTechAgent 必须导出会话提示入口")

    # ------------------------------------------------ 契约 B：运行时重新发布快照
    def test_runtime_exposes_refresh_state(self):
        api = block_from(self.runtime, "var api = {")
        self.assertTrue(api, "找不到运行时 api 导出块")
        self.assertIn("refreshState", api, "必须导出 refreshState() 供看板刷新快照")

    def test_refresh_state_republishes_without_overrides(self):
        body = block_from(self.runtime, "function refreshState(")
        self.assertTrue(body, "找不到 refreshState()")
        self.assertIn("ACTION_STATE", body, "refreshState 必须重新发布 action-state")
        self.assertNotIn("overrides", body,
                         "refreshState 只重发快照，不能写 overrides（那是覆盖语义）")

    def test_entry_state_field_contract_unchanged(self):
        body = block_from(self.runtime, "function entryState(")
        for field in ("label", "visible", "enabled", "busy", "role", "order", "hint"):
            with self.subTest(field=field):
                self.assertIn(field + ":", body, f"entryState 字段契约不能动：{field}")

    # ------------------------------------------------ 契约 C：2.2 组装与整合
    def test_assembly_getstate_never_reads_page_button_disabled(self):
        blocks = arrow_bodies(self.assembly, "getState:")
        self.assertTrue(blocks, "2.2 找不到任何 getState()")
        for body in blocks:
            with self.subTest(body=body[:60]):
                self.assertNotIn(".disabled", body,
                                 "看板动作可用性不能再直通页内按钮 disabled")

    def test_assembly_actions_declare_enabled_true(self):
        blocks = arrow_bodies(self.assembly, "getState:")
        for body in blocks:
            if "enabled:" not in body:
                continue
            with self.subTest(body=body[:60]):
                self.assertRegex(body, r"enabled:\s*true",
                                 "enabled 只表达「这一步有这个动作」，忙闲交给 busy")

    def test_send_finance_action_checks_blocker_and_returns_error(self):
        body = block_from(self.assembly, "sendIntegrationToFinance:")
        self.assertTrue(body, "找不到 sendIntegrationToFinance 动作")
        self.assertNotIn(".disabled", body, "发送财务不能再读页内按钮 disabled")
        self.assertIn("aiFinanceBlocker(", body, "点了要先算前置条件，再决定放行或报错")
        self.assertRegex(body, r"ok:\s*false", "前置不满足要返回结构化失败")
        # 契约更新（组装工艺收口批次）：弹窗改由后台链路调用（它要先确认工艺再开弹窗），
        # 实现本体与调用点一字未少。
        chain = block_from(self.assembly, "async function aiConfirmProcessAndSendToFinance(")
        self.assertIn("aiOpenFinanceDialog(", chain, "满足条件时仍走既有发送对话框")
        self.assertIn("code:", body, "结构化失败必须带可识别的 code")

    def test_finance_blocker_is_single_source(self):
        blocker = block_from(self.assembly, "function aiFinanceBlocker(")
        self.assertTrue(blocker, "必须抽出 aiFinanceBlocker() 作为唯一判定")
        ops = block_from(self.assembly, "function aiRenderOps(")
        self.assertTrue(ops, "找不到 aiRenderOps()")
        self.assertIn("aiFinanceBlocker(", ops,
                      "页内 why 与动作判定必须共用同一份，否则两处会漂移")

    def test_open_finance_dialog_reports_busy(self):
        body = block_from(self.assembly, "async function aiOpenFinanceDialog(")
        self.assertTrue(body, "找不到 aiOpenFinanceDialog()")
        self.assertNotRegex(body, r"if\s*\(\s*aiBusy\s*\)\s*return\s*;",
                            "忙的时候不得静默 return")
        self.assertRegex(body, r"ok:\s*false", "忙的时候要返回结构化失败")

    def test_assembly_render_republishes_action_state(self):
        render = block_from(self.assembly, "function aiRender(")
        self.assertTrue(render, "找不到 aiRender()")
        self.assertIn("aiPublishState(", render, "渲染完要重新发布动作快照")
        publish = block_from(self.assembly, "function aiPublishState(")
        self.assertTrue(publish, "找不到 aiPublishState()")
        self.assertIn("refreshState", publish, "aiPublishState 必须走运行的 refreshState()")

    # ------------------------------------------------ 契约 D：2.3 成本测算
    def test_cost_role_codes_match_backend_authority(self):
        # 契约更新（2.3 角色判定批次）：登录态 window.cpqAuth.user() 给的是 **CPQ 口径**
        # 角色码（cpq_auth.ROLES：finance_mgr / process_mgr / sales_mgr），后端权威是技术工艺
        # 口径 finance_manager（cpq_sso.ROLE_MAP 映射）。前一版只抄了后端那一份，于是真财务
        # 经理被前端判成只读、写请求发不出去。现在判定改成「服务端能力位 can_cost 优先，
        # 退回角色码白名单时两套口径都认」。
        backend_match = re.search(r"COST_ROLES\s*=\s*\{([^}]*)\}", self.auth)
        self.assertTrue(backend_match, "找不到后端 auth.COST_ROLES")
        backend = set(re.findall(r"['\"]([A-Za-z_]+)['\"]", backend_match.group(1)))
        frontend_match = re.search(r"CR_COST_ROLES\s*=\s*\[([^\]]*)\]", self.cost)
        self.assertTrue(frontend_match, "找不到前端 CR_COST_ROLES")
        frontend = set(re.findall(r"['\"]([A-Za-z_]+)['\"]", frontend_match.group(1)))
        self.assertTrue(backend <= frontend,
                        "后端权威角色码必须都在前端白名单里（前端只是提前提示）")
        self.assertIn("finance_mgr", frontend,
                      "CPQ 登录态给的是 finance_mgr，前端不认就会把财务经理误判成只读")
        self.assertIn("CpqSso", self.cost, "要优先读服务端算好的能力位")
        self.assertIn("canCost", self.cost, "能力位字段是 canCost")

    def test_cost_getstate_never_reads_page_button_disabled(self):
        blocks = arrow_bodies(self.cost, "getState:")
        self.assertTrue(blocks, "2.3 找不到任何 getState()")
        for body in blocks:
            with self.subTest(body=body[:60]):
                self.assertNotIn(".disabled", body,
                                 "看板动作可用性不能再直通页内按钮 disabled")

    def test_cost_actions_declare_enabled_true(self):
        blocks = arrow_bodies(self.cost, "getState:")
        for body in blocks:
            if "enabled:" not in body:
                continue
            with self.subTest(body=body[:60]):
                self.assertRegex(body, r"enabled:\s*true",
                                 "enabled 只表达「这一步有这个动作」，忙闲交给 busy")

    def test_confirm_cost_action_checks_blocker_and_returns_error(self):
        body = block_from(self.cost, "confirmCostReview:")
        self.assertTrue(body, "找不到 confirmCostReview 动作")
        self.assertNotIn(".disabled", body, "确认成本不能再读页内按钮 disabled")
        self.assertIn("crConfirmBlocker(", body, "点了要先算前置条件")
        self.assertIn("crConfirmCost(", body, "满足条件时仍走既有确认实现")
        self.assertRegex(body, r"ok:\s*false", "前置不满足 / 确认失败都要返回结构化失败")

    def test_cost_confirm_blocker_is_single_source(self):
        blocker = block_from(self.cost, "function crConfirmBlocker(")
        self.assertTrue(blocker, "必须抽出 crConfirmBlocker() 作为唯一判定")
        actions = block_from(self.cost, "function crRenderActions(")
        self.assertTrue(actions, "找不到 crRenderActions()")
        self.assertIn("crConfirmBlocker(", actions,
                      "页内 why 与动作判定必须共用同一份")

    def test_cost_render_republishes_action_state(self):
        render = block_from(self.cost, "function crRender(")
        self.assertTrue(render, "找不到 crRender()")
        self.assertIn("crPublishState(", render, "渲染完要重新发布动作快照")
        publish = block_from(self.cost, "function crPublishState(")
        self.assertTrue(publish, "找不到 crPublishState()")
        self.assertIn("refreshState", publish, "crPublishState 必须走运行的 refreshState()")

    def test_readonly_does_not_block_left_toolbar(self):
        body = block_from(self.cost, "confirmCostReview:")
        self.assertNotIn("crReadOnly(", body,
                         "只读身份不再拦在按钮前面：点了给原因，后端照旧 403 兜底")

    # ------------------------------------------------ 保护边界
    def test_protocol_bridge_and_backend_untouched(self):
        for name in ("READY", "ACTION_STATE", "TASK_PROGRESS", "TASK_COMPLETED",
                     "TASK_FAILED", "SELECTION_CHANGED", "BOARD_STATUS"):
            with self.subTest(name=name):
                self.assertIn(name + ":", self.bridge, f"桥事件白名单被改动：{name}")
        for route in ('/api/projects/{project_id}/cost-review/parts/{part_id}',
                      '/api/projects/{project_id}/cost-review/confirm',
                      '/api/projects/{project_id}/cost-review/send-to-quote',
                      '/api/projects/{project_id}/integration/process/confirm'):
            with self.subTest(route=route):
                self.assertIn(route, self.main, f"后端路由不得减少：{route}")
        self.assertGreaterEqual(len(re.findall(r"_require\(user,\s*auth\.COST_ROLES", self.main)), 3,
                                "后端仍是权限权威，_require 不得被移除")

    def test_page_internal_button_semantics_kept(self):
        params = re.search(r"<button[^>]*id=\"aiParamsConfirm\"[^>]*>", self.assembly)
        self.assertIsNotNone(params, "页内 #aiParamsConfirm 被删除")
        self.assertIn("!has || aiBusy", params.group(0),
                      "页内按钮自身的 disabled 语义本批不改")
        self.assertRegex(self.cost, r"\$cr\('crConfirm'\)\.disabled\s*=",
                         "页内 #crConfirm 的 disabled 语义本批不改")


if __name__ == "__main__":
    unittest.main()
