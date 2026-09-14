"""红测：2.1 左侧按钮清理 + 「已生成工艺推荐」自动展开（实现前应失败）。

现状缺口（实测）：
  · 左侧操作栏渲染所有 `visible !== false` 的动作，而 `app.js` 的 `modelLookup`
    （联网核验）与 `verify`（校验修正）都返回 `visible: true`，于是左侧栏里和 2.1
    右侧「更多功能 ▾」（`index.html` 的 `#btnModelLookup` / `#btnVerify`）重复了两颗
    按钮。
  · 左侧还有一颗静态「解析视图」按钮（`tech-workbench.html`
    `data-tech-capability="evidence"`），同样是重复入口。
  · 2.1 收口主按钮叫「确认解析结果并进入下一步」，用户要求只叫「确认解析结果」。
  · 某个零件的工艺推荐已经生成时，选中它仍然只显示 3D，用户必须再点一次「工艺推荐」
    才能看到结果；要求已生成即自动展开。

本批只改 2.1：隐藏的两颗动作、`evidence` 视图、Agent 工具分派、后端路由一律保留；
自动展开复用既有 `partHasExistingProcess()` 读取判定与既有 `openPartAnalysis()` 渲染，
不新增接口、不触发生成、不新建父级 Drawer/Modal。
"""
from __future__ import annotations

import re
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
APP_JS = F / "app.js"
PARENT_HTML = F / "tech-workbench.html"
INDEX_HTML = F / "index.html"
CHAT_JS = F / "agent-chat.js"
ASSEMBLY_JS = F / "assembly-integration.js"
RUNTIME_JS = F / "tech-board-runtime.js"
BRIDGE_JS = F / "tech-board-bridge.js"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

LEFT_HIDDEN_ACTIONS = ("modelLookup", "verify")


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


class TechDrawingToolbarCleanupAndProcessAutoExpandRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = read(APP_JS)
        cls.parent_html = read(PARENT_HTML)
        cls.index_html = read(INDEX_HTML)
        cls.chat = read(CHAT_JS)
        cls.assembly = read(ASSEMBLY_JS)
        cls.runtime = read(RUNTIME_JS)
        cls.bridge = read(BRIDGE_JS)
        cls.main = read(MAIN_PY)

    def nav_body(self) -> str:
        nav = re.search(
            r'<nav class="tech-chat-actions"[^>]*id="techChatActions"[^>]*>([\s\S]*?)</nav>',
            self.parent_html,
        )
        self.assertIsNotNone(nav, "父壳左侧操作栏 #techChatActions 不存在")
        return nav.group(1)

    # ------------------------------------------------ 契约 A：左侧按钮清理
    def test_evidence_button_is_gone_from_parent_toolbar(self):
        nav = self.nav_body()
        self.assertNotIn("解析视图", nav, "「解析视图」不该再占左侧操作栏一颗按钮")
        self.assertNotIn("data-tech-capability", nav,
                         "左侧操作栏不该再有静态能力按钮")
        self.assertNotIn('data-tech-capability="evidence"', self.parent_html,
                         "父壳不得再以 evidence 能力入口渲染解析视图")

    def test_evidence_view_capability_is_preserved(self):
        self.assertIn('runBoardView("evidence")', self.app,
                      "解析视图看板视图本体被删除")
        self.assertIn("secEvidence", self.app, "解析视图面板被删除")
        self.assertIn("dispatchDrawingCapability", self.chat,
                      "会话快捷能力分派被删除")
        self.assertIn("'capability:evidence': 'evidence'", self.chat,
                      "capability→视图映射被删除（Agent / 看板内部仍要走它）")

    def test_model_lookup_and_verify_leave_left_toolbar(self):
        for name in LEFT_HIDDEN_ACTIONS:
            with self.subTest(action=name):
                block = block_from(self.app, f"{name}:")
                self.assertTrue(block, f"找不到 {name} 动作条目")
                self.assertRegex(block, r"visible:\s*false",
                                 f"{name} 不该再占左侧操作栏一颗按钮")
                self.assertNotRegex(block, r"visible:\s*true",
                                    f"{name} 的左侧可见性必须关掉")

    def test_hidden_actions_keep_their_runnable_body(self):
        for name, runner, label in (("modelLookup", "runModelLookup()", "联网核验"),
                                    ("verify", "runVerification()", "校验修正")):
            with self.subTest(action=name):
                block = block_from(self.app, f"{name}:")
                self.assertTrue(block, f"找不到 {name} 动作条目")
                self.assertIn(runner, block, f"{name} 的执行链路被改动")
                self.assertIn(label, block, f"{name} 的文案被改动")
                self.assertRegex(block, r"enabled:", f"{name} 的可用性判定被删除")

    def test_more_actions_menu_still_owns_both_entries(self):
        sheet = re.search(r'<div id="actionSheet"[\s\S]*?</div>', self.index_html)
        self.assertIsNotNone(sheet, "2.1「更多功能」菜单不存在")
        for token in ('id="btnModelLookup"', 'id="btnVerify"', "联网核验", "校验修正"):
            with self.subTest(token=token):
                self.assertIn(token, sheet.group(0),
                              f"「更多功能」里的 2.1 能力入口被改动：{token}")

    def test_agent_action_dispatch_unchanged(self):
        for token in ('DRAWING_ACTION_CAPABILITIES = ["modelLookup", "verify"]',
                      'bridge.executeAction("modelLookup"',
                      'bridge.executeAction("verify"',
                      "CAPABILITY_LABELS"):
            with self.subTest(token=token):
                self.assertIn(token, self.chat, f"Agent 业务动作分派被改动：{token}")

    # ------------------------------------------------ 契约 B：收口按钮文案
    def test_confirm_button_label_is_shortened(self):
        block = block_from(self.app, "confirmDrawingResult:")
        self.assertTrue(block, "找不到 confirmDrawingResult 动作条目")
        self.assertRegex(block, r'label:\s*"确认解析结果"',
                         "2.1 收口主按钮应叫「确认解析结果」")
        self.assertNotIn("并进入下一步", block,
                         "2.1 收口主按钮不应再带「并进入下一步」")

    def test_confirm_keeps_its_real_state_gate(self):
        block = block_from(self.app, "confirmDrawingResult:")
        # 契约更新（主按钮三段式批次）：确认动作从 order 15 让位到 25，
        # 前面插「一键生成全部工艺推荐」(order 15)；闸门本体与标签不变。
        for token in ('requestNavigate("process"', "drawingParsed()", "no-navigation",
                      "/api/projects/", "order: 25", "role:"):
            with self.subTest(token=token):
                self.assertIn(token, block, f"确认动作既有实现被删除：{token}")

    def test_other_stages_keep_their_next_step_labels(self):
        self.assertIn("并进入下一步", self.assembly,
                      "其它阶段的「…并进入下一步」文案被误改")

    # ------------------------------------------------ 契约 C：已生成工艺推荐自动展开
    def test_existing_process_probe_stays_read_only(self):
        block = block_from(self.app, "async function partHasExistingProcess(")
        self.assertTrue(block, "既有的「库里是否已有工艺」判定被删除")
        self.assertIn("/process", block)
        self.assertNotRegex(block, r'method:\s*"POST"',
                            "自动展开依赖的判定必须继续是只读 GET")

    def test_auto_open_reuses_existing_probe_and_entry(self):
        block = block_from(self.app, "function autoOpenGeneratedProcess(")
        self.assertTrue(block, "尚未实现 autoOpenGeneratedProcess(part)")
        self.assertIn("partHasExistingProcess(", block,
                      "自动展开必须复用既有读取判定，不得另写第二份")
        self.assertIn("openPartAnalysis(", block,
                      "自动展开必须复用既有工艺推荐入口")
        self.assertIn("currentProject", block, "缺少项目守卫")
        self.assertNotIn("fetch(", block, "自动展开不得自己发请求")
        self.assertNotIn("/generate", block, "自动展开不得触发生成")
        self.assertNotIn("document.createElement", block,
                         "自动展开不得新建面板节点")

    def test_auto_open_is_deduplicated_per_part(self):
        self.assertRegex(self.app, r"autoOpenedProcessParts\s*=\s*new\s+Set\(\)",
                         "缺少按零件去重的自动展开标记")
        block = block_from(self.app, "function autoOpenGeneratedProcess(")
        self.assertIn("autoOpenedProcessParts.has(", block,
                      "自动展开前必须查重，否则会反复抢走 3D 视图")
        self.assertIn("autoOpenedProcessParts.add(", block,
                      "展开成功后必须记标记")

    def test_auto_open_failure_is_silent_and_local(self):
        block = block_from(self.app, "function autoOpenGeneratedProcess(")
        self.assertIn(".catch(", block, "读取失败必须静默回落，不得抛出")
        for token in ("status(", "noteInThread(", "alert(", "Modal", "Drawer"):
            with self.subTest(token=token):
                self.assertNotIn(token, block,
                                 f"自动展开不得出现这些用户可见副作用：{token}")

    def test_select_part_triggers_auto_open(self):
        block = block_from(self.app, "function selectPart(part)")
        self.assertTrue(block, "找不到 selectPart(part)")
        self.assertIn("autoOpenGeneratedProcess(", block,
                      "选中零件后要按「已生成即展开」处理")

    def test_batch_process_completion_triggers_auto_open(self):
        block = block_from(self.app, "async function runAllPartProcessesInBackground(")
        self.assertTrue(block, "找不到 runAllPartProcessesInBackground()")
        self.assertIn("autoOpenGeneratedProcess(", block,
                      "批量生成完成后要对当前零件补一次自动展开")

    def test_select_part_keeps_its_existing_behavior(self):
        block = block_from(self.app, "function selectPart(part)")
        for token in ("window.CadInlineAnalysis?.reset()", 'setRightPane("model")',
                      "exitBoardViewHost()", "markSelection(", "togglePartSubActions(",
                      "updateChatContext("):
            with self.subTest(token=token):
                self.assertIn(token, block, f"selectPart 既有行为被删除：{token}")

    def test_no_new_backend_route_or_protocol_change(self):
        self.assertIn("/api/projects/{project_id}/parts/{part_id}/process", self.main,
                      "后端工艺读取路由被删除")
        self.assertIn("/api/projects/{project_id}/parts/{part_id}/process", self.main)
        self.assertRegex(self.main, r'@app\.post\(\s*"/api/projects/\{project_id\}/parts/\{part_id\}/process"',
                         "后端工艺生成路由被删除")
        for token in ("envelope('command', name, payload, requestId)",
                      "if (type !== 'state') return;",
                      "STATE_EVENTS", "'board-status'", "'selection-changed'"):
            with self.subTest(token=token):
                self.assertIn(token, self.bridge + self.runtime,
                              f"桥协议常量被改动：{token}")
        for view in ('"parts-list"', '"part-detail"', '"part-process"', '"part-cost"',
                     '"drawing-overview"'):
            with self.subTest(view=view):
                self.assertIn(view, self.app, f"看板视图名被改动：{view}")
        self.assertIn("一键生成全部工艺推荐", self.app, "批量工艺推荐入口被删除")


if __name__ == "__main__":
    unittest.main()
