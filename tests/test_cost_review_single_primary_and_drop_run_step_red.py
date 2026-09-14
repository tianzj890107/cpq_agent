"""红测：2.3「成本测算」去掉「运行成本测算」，主按钮随测算完成度反转。

现状缺口（实测）：
  · 站在 2.3 成本测算页时，`cost-review.js` 会把 `costStep`（运行成本测算）渲染进左侧
    操作栏（`visible: true`，order 70）。它要求 `payload.step ∈ part / assembly / all`，
    左侧栏不会给任何 step，用户点了只能拿到 `bad-step`；旁边还有一颗同义的
    「一键测算全部成本」。它真正的调用方是 Agent（`RunCostReviewPart/Assembly/All`
    → `cost-step` → `executeAction("costStep")`）。
  · `tech-board-runtime.js` 的 `entryState()` 只从 `getState()` 读 role，动作条目上的
    静态 `role: 'primary'` 是死元数据，因此 2.3 左侧栏**根本没有主按钮**：测算前没有
    起点高亮，测算完成后也没有收口，「一键测算全部成本」与「确认成本」都只是描边按钮。

本批只改 2.3 的这两件事：`costStep` 退出左侧操作栏（注册 / 校验 / 后台链路一律保留），
两颗业务按钮的 role 由同一份「成本是否算全」判定（复用 `crConfirmBlocker()`）反转。
被隐藏的动作继续注册、继续可执行；三个去向、内部视图、后端路由、桥协议、右侧看板一律不动。
"""
from __future__ import annotations

import re
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
BOARD = F / "cost-review.js"
HTML = F / "cost-review.html"
CHAT = F / "agent-chat.js"
PARENT = F / "tech-workbench.js"
RUNTIME = F / "tech-board-runtime.js"
BRIDGE = F / "tech-board-bridge.js"
EMBED = F / "tech-embed.js"
AGENT = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 2.3 里必须继续注册（Agent / 内部链路仍在用）但不再出现在左侧操作栏的动作。
LEFT_HIDDEN = ("costStep",)

COST_ROUTES = (
    "/api/projects/{project_id}/cost-review",
    "/api/projects/{project_id}/cost-review/parts/{part_id}",
    "/api/projects/{project_id}/cost-review/assembly",
    "/api/projects/{project_id}/cost-review/confirm",
    "/api/projects/{project_id}/cost-review/material-write",
    "/api/projects/{project_id}/cost-review/send-to-quote",
    "/api/projects/{project_id}/cost-review/return-to-process",
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


class CostReviewSinglePrimaryRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = read(BOARD)
        cls.html = read(HTML)
        cls.chat = read(CHAT)
        cls.parent = read(PARENT)
        cls.runtime = read(RUNTIME)
        cls.bridge = read(BRIDGE)
        cls.embed = read(EMBED)
        cls.agent = read(AGENT)
        cls.main = read(MAIN)

    # ------------------------------------------- 契约 A：运行成本测算退出左侧栏
    def test_run_cost_step_action_still_registered(self):
        block = block_from(self.board, "costStep:")
        self.assertTrue(block, "costStep 动作注册被删除（Agent 仍在调用）")
        self.assertIn("运行成本测算", block, "动作 label 被改动")

    def test_run_cost_step_is_hidden_from_left_toolbar(self):
        for name in LEFT_HIDDEN:
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertTrue(block, f"找不到 {name} 动作条目")
                self.assertRegex(block, r"visible:\s*false",
                                 f"{name} 不该再占左侧操作栏一颗按钮")
                self.assertNotRegex(block, r"visible:\s*true",
                                    f"{name} 的可见性必须关掉")

    def test_run_cost_step_keeps_its_validator_and_pipeline(self):
        block = block_from(self.board, "costStep:")
        for token in ("'part'", "'assembly'", "'all'", "missing-part", "bad-step",
                      "crRunPart(", "crRunAssembly", "crRunAll()",
                      "crCostStepInBackground("):
            with self.subTest(token=token):
                self.assertIn(token, block,
                              f"被隐藏的动作仍须保留既有实现：{token}")
        self.assertRegex(block, r"deferred:\s*true",
                         "costStep 仍是长任务，deferred 不得改动")
        self.assertRegex(block, r"order:\s*70", "costStep 的 order 不需要改动")

    def test_agent_cost_step_chain_unchanged(self):
        for mapping in ('"RunCostReviewPart": "cost-step"',
                        '"RunCostReviewAssembly": "cost-step"',
                        '"RunCostReviewAll": "cost-step"'):
            with self.subTest(mapping=mapping):
                self.assertIn(mapping, self.agent, f"后端工具映射被改动：{mapping}")
        self.assertIn("cost-step", self.chat, "左侧会话的 cost-step 分发被删除")
        self.assertRegex(self.chat, r"executeAction\(\s*\"costStep\"",
                         "Agent 仍须经桥触发 costStep")

    def test_left_pane_does_not_call_cost_review_endpoints(self):
        self.assertNotIn("/cost-review", self.chat,
                         "左侧会话仍不得直接调用 cost-review 接口")

    def test_backend_cost_routes_untouched(self):
        for route in COST_ROUTES:
            with self.subTest(route=route):
                self.assertIn(route, self.main, f"后端 cost-review 路由被删除：{route}")
        self.assertGreaterEqual(len(re.findall(r"_require\(user,\s*auth\.COST_ROLES", self.main)), 3,
                                "后端仍是权限权威，_require 不得被移除")

    # ------------------------------------------- 契约 B：唯一完成度判定
    def test_cost_completion_helper_reuses_the_single_source(self):
        block = block_from(self.board, "function crCostsComplete(")
        self.assertTrue(block, "缺少共享判定 crCostsComplete()")
        self.assertIn("crConfirmBlocker()", block,
                      "「成本是否算全」必须复用 crConfirmBlocker()，不得另写一份判定")
        for token in ("counts", "crData", "ready"):
            with self.subTest(token=token):
                self.assertNotIn(token, block,
                                 f"共享判定里出现了第二份条件 {token}，会与 crConfirmBlocker() 漂移")

    def test_cost_completion_helper_is_declared_once(self):
        self.assertEqual(self.board.count("function crCostsComplete("), 1,
                         "crCostsComplete() 只能声明一次")
        self.assertGreaterEqual(self.board.count("crCostsComplete()"), 3,
                                "共享判定必须被两颗按钮的 getState() 共同引用（1 处声明 + 2 处使用）")

    # ------------------------------------------- 契约 C：主按钮反转
    def test_run_cost_review_is_primary_until_costs_complete(self):
        block = block_from(self.board, "runCostReview:")
        self.assertTrue(block, "找不到 runCostReview 动作条目")
        self.assertRegex(block, r"role:\s*!crCostsComplete\(\)\s*\?\s*'primary'\s*:\s*'aux'",
                         "没算全时「一键测算全部成本」必须是主按钮，算全后让位给「确认成本」")

    def test_confirm_cost_review_is_primary_once_costs_complete(self):
        block = block_from(self.board, "confirmCostReview:")
        self.assertTrue(block, "找不到 confirmCostReview 动作条目")
        self.assertRegex(block, r"role:\s*crCostsComplete\(\)\s*\?\s*'primary'\s*:\s*'aux'",
                         "算全后「确认成本」必须是主按钮")

    def test_no_dead_static_primary_on_the_action_entry(self):
        self.assertNotRegex(
            self.board, r"(?m)^\s*role:\s*'primary',?\s*$",
            "动作条目上的静态 role 是死元数据：真实 role 只能由 getState() 返回")

    def test_exactly_one_primary_role_is_declared(self):
        found = re.findall(r"role\s*:\s*[^\n]*'primary'", self.board)
        self.assertEqual(len(found), 2,
                         f"2.3 只允许两颗按钮声明动态 role（各一处），实际 {len(found)} 处：{found}")

    def test_roles_reverse_from_the_same_judgement(self):
        for name in ("runCostReview", "confirmCostReview"):
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertIn("crCostsComplete()", block,
                              f"{name} 的 role 必须来自共享判定，不得各写一份")

    def test_run_cost_review_keeps_label_order_and_deferred_start(self):
        block = block_from(self.board, "runCostReview:")
        self.assertIn("一键测算全部成本", block, "动作 label 被改动")
        self.assertRegex(block, r"order:\s*10", "runCostReview 的 order 不需要改动")
        self.assertRegex(block, r"deferred:\s*true", "start 型长任务语义不得改动")
        self.assertIn("crRunAllInBackground()", block,
                      "一键测算全部成本必须继续复用既有后台链路")
        self.assertRegex(block, r"enabled:\s*true",
                         "enabled 只表达「这一步有这个动作」，忙闲交给 busy")

    def test_confirm_cost_review_keeps_blocker_and_error(self):
        block = block_from(self.board, "confirmCostReview:")
        self.assertIn("确认成本", block, "动作 label 被改动")
        self.assertRegex(block, r"order:\s*20", "confirmCostReview 的 order 不需要改动")
        self.assertIn("crConfirmBlocker(", block, "点了要先算前置条件")
        self.assertIn("crConfirmCost(", block, "满足条件时仍走既有确认实现")
        self.assertIn("not-ready", block, "前置不满足要返回结构化失败码")
        self.assertRegex(block, r"ok:\s*false", "前置不满足 / 确认失败都要返回结构化失败")
        self.assertRegex(block, r"enabled:\s*true", "enabled 只表达「这一步有这个动作」")
        self.assertNotIn(".disabled", block, "可用性不能再直通页内按钮 disabled")

    def test_confirm_cost_review_stays_visible_at_all_times(self):
        block = block_from(self.board, "confirmCostReview:")
        self.assertRegex(block, r"visible:\s*true",
                         "确认成本必须一直可见：没算全也要让用户点出真实原因")
        self.assertNotIn("crReadOnly(", block,
                         "只读身份不再拦在按钮前面：点了给原因，后端照旧 403 兜底")

    def test_other_cost_actions_keep_visibility_and_role(self):
        for name, order in (("writeCostReviewMaterial", 30),
                            ("sendCostReviewToQuote", 40),
                            ("returnCostReviewToProcess", 50)):
            with self.subTest(action=name):
                self.assertRegex(self.board, rf"{name}\s*:",
                                 f"既有去向动作 {name} 被删除")
                self.assertRegex(self.board, rf"{name}\s*:\s*crOpAction\([^)]*,\s*{order}\)",
                                 f"{name} 仍须复用 crOpAction 且 order 不变")
        for kind in ("material-write", "send-to-quote", "return-to-process"):
            with self.subTest(kind=kind):
                self.assertIn(f"crOpAction('{kind}'", self.board,
                              f"去向 {kind} 必须继续复用既有 crRunOp(kind)")
        refresh = block_from(self.board, "refreshCostReview:")
        self.assertRegex(refresh, r"visible:\s*false",
                         "刷新动作仍只退出左侧栏，由 refresh-data 与 Agent 工具触发")

    def test_visible_actions_declare_aux_role(self):
        ops = block_from(self.board, "const crOpAction =")
        self.assertTrue(ops, "找不到 crOpAction 工厂")
        self.assertRegex(ops, r"role:\s*role\s*\|\|\s*'aux'",
                         "去向动作的 role 由工厂统一给 aux，不得被抢成主按钮")
        step = block_from(self.board, "costStep:")
        self.assertRegex(step, r"role:\s*'aux'",
                         "被隐藏的运行成本测算继续声明 aux")

    # ------------------------------------------- 契约 D：能力不缩水
    def test_views_unchanged(self):
        views = block_from(self.board, "registerViews(")
        self.assertTrue(views, "找不到 registerViews()")
        for view in ("parts", "assembly", "total"):
            with self.subTest(view=view):
                self.assertRegex(views, rf"{view}\s*:", f"内部视图 {view} 被删除")
        self.assertRegex(views, r"params\s*:", "显式拒绝的 params 兼容别名被删除")
        self.assertIn("moved-to-integration", views,
                      "params 别名仍须返回可识别的失败，不得静默什么都不做")

    def test_right_board_run_all_and_ops_card_stay_hidden_in_embed(self):
        for selector in ("#crRunAll", "#crOpsCard .ai-ops"):
            with self.subTest(selector=selector):
                hits = [line for line in self.embed.splitlines() if selector in line]
                self.assertTrue(hits, f"嵌入态未隐藏右侧看板重复入口 {selector}")
                for line in hits:
                    self.assertIn(".tech-embed", line,
                                  f"{selector} 的隐藏规则必须限定在 .tech-embed 作用域")

    def test_right_board_inline_part_and_assembly_buttons_are_kept(self):
        self.assertIn('id="crRunAll"', self.html, "右侧「一键测算全部成本」被删除")
        self.assertIn('id="crActions"', self.html, "右侧页内动作容器被删除")
        self.assertIn('id="crConfirm"', self.html, "右侧「确认成本」被删除")
        actions = block_from(self.board, "function crRenderActions(")
        self.assertTrue(actions, "找不到 crRenderActions()")
        for token in ("crRunParts", "crRunAssembly", "data-cr-part"):
            with self.subTest(token=token):
                self.assertIn(token, self.board + actions,
                              f"页内逐件 / 单环节重跑入口 {token} 不得被本批删掉")

    def test_parent_filter_and_primary_rule_unchanged(self):
        self.assertIn("entry.visible === false", self.parent,
                      "父壳仍以 visible === false 为唯一跳过条件")
        self.assertRegex(self.parent, r"role === 'primary'",
                         "主按钮仍只认看板声明的 role === 'primary'")
        for name in ("costStep", "runCostReview", "confirmCostReview"):
            with self.subTest(name=name):
                self.assertNotIn(name, self.parent,
                                 f"父壳不得为某个动作名写分支：{name}")

    def test_protocol_whitelist_untouched(self):
        for name in ("READY", "ACTION_STATE", "TASK_PROGRESS", "TASK_COMPLETED",
                     "TASK_FAILED", "SELECTION_CHANGED", "BOARD_STATUS"):
            with self.subTest(name=name):
                self.assertIn(name + ":", self.bridge, f"桥事件白名单被改动：{name}")
        self.assertIn("role: raw.role === 'primary' ? 'primary' : 'aux'", self.runtime,
                      "运行时 role 仍只认白名单 primary，其它一律降级 aux")


if __name__ == "__main__":
    unittest.main()
