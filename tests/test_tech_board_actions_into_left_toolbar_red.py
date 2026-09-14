"""红测：技术工艺右侧看板的业务按钮统一搬到左侧会话操作栏（唯一入口、唯一主按钮、带 tooltip）。

现状缺口（Red 基线）：
  · 左侧 `#techChatActions` 里写死了一批入口（`techChatAttach` / `techChatAiRun` /
    `techChatSecondary` / `techChatBulk`），父壳只认识 `STAGE_ACTIONS` / `STAGE_CHAT_ACTIONS`
    里少数动作名，看板实际注册的 35 个动作大部分在左侧没有入口；
  · 同一动作被渲染三次：左侧 `techChatPrimary` + `techChatBulk`（都是 `.primary`）、
    右侧业务卡底栏 `#techPrimary` / `#techSecondary`、阶段页页内按钮；
  · 2.2 参数推荐 / 组装工艺的生成、保存、确认、智能补全、保存补填、确认参数已齐只在
    `aiRenderActions()` 里生成，根本没有注册成看板动作，左侧无从渲染；
  · 看板动作快照没有 role / order / hint，父壳无法选出唯一主按钮，也无法生成 tooltip；
  · 嵌入态仍会显示 `#btnParse` / `#aiStart` / `#aiActions` / `#aiOpsCard .ai-ops` /
    `#crRunAll` / `#crOpsCard .ai-ops` / `#btnAiExtract` 这些第二份入口。

本批只做「入口归一 + 去重 + tooltip」；不改任何业务实现、算法、后端路由与既有 state 事件。
不联网、不起服务、不读真实业务数据；全部为静态契约校验。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
WB_HTML = F / "tech-workbench.html"
WB_JS = F / "tech-workbench.js"
EMBED = F / "tech-embed.js"
RUNTIME = F / "tech-board-runtime.js"
BRIDGE = F / "tech-board-bridge.js"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

STAGE_PAGES = {
    "requirement-create": F / "requirement-create.js",
    "requirement-confirm": F / "requirement-confirm-page.js",
    "requirement-review": F / "requirement-review-page.js",
    "drawing": F / "app.js",
    "process": F / "assembly-integration.js",
    "cost": F / "cost-review.js",
    "summary": F / "summary-result.js",
    "report-review": F / "report-review-result.js",
    "report-publish": F / "report-publish-result.js",
}
ACTION_PAGES = {
    "requirement-create": [F / "requirement-create.js"],
    "requirement-confirm": [F / "requirement-confirm-page.js"],
    "requirement-review": [F / "requirement-review-page.js"],
    "drawing": [F / "app.js"],
    "process": [F / "assembly-integration.js"],
    "cost": [F / "cost-review.js"],
    "summary": [F / "summary-result.js"],
    "report-review": [F / "report-review-result.js"],
    "report-publish": [F / "report-publish-result.js"],
}

# 嵌入态必须隐藏的第二份入口（按钮行），独立打开阶段页时照旧显示。
EMBED_HIDDEN = (
    "#btnParse",
    "#aiStart",
    "#aiActions",
    "#aiOpsCard .ai-ops",
    "#crRunAll",
    "#crOpsCard .ai-ops",
    "#btnAiExtract",
)
# 只藏按钮、不能藏掉输入与说明。
MUST_STAY_VISIBLE = ("#aiProductName", "#crProductName", "#crQuantity", "#aiOpsHint", "#crHint",
                     "#aiExtractStatus")

# 契约更新（「左侧操作栏只留当前步骤业务动作、去掉通用刷新与导航按钮」批次）：用户明确要求
# 去掉每页重复的上一步 / 下一步 / 转交任务 / 失败重试；父壳左侧只保留唯一主按钮槽位，
# 其余业务动作全部由看板快照动态渲染。
REQUIRED_LEFT_TOOLBAR_IDS = ("techChatPrimary",)
RETIRED_LEFT_TOOLBAR_IDS = ("techChatPrev", "techChatNext", "techChatTransfer", "techChatRetry")
REMOVED_LEFT_TOOLBAR_IDS = ("techChatAttach", "techChatAiRun", "techChatSecondary", "techChatBulk")
KEPT_KEEP_IDS = ("ocFilesAction", "ocResultActions", "ocTaskProgressHost",
                 "ocChatAttachBtn", "ocChatFileInput", "techPrev", "techNext", "techNowLabel")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
    """返回 marker 之后第一对成对花括号（含花括号）的源码，跳过字符串与注释。"""
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
                return text[brace:i + 1]
        i += 1
    return ""


def action_names_of(js: str) -> list[str]:
    """抓出 registerActions({...}) 顶层动作名（缩进 4 空格的 `name: {` 或 helper 调用）。"""
    names: list[str] = []
    for match in re.finditer(r"registerActions\(\{", js):
        block = block_from(js, "registerActions(")
        idx = js.find("registerActions({", match.start())
        brace = js.find("{", idx)
        depth = 0
        end = brace
        while end < len(js):
            if js[end] == "{":
                depth += 1
            elif js[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        body = js[brace + 1:end]
        for key in re.finditer(r"(?m)^\s{4}([A-Za-z_$][\w$]*)\s*:", body):
            if key.group(1) not in names:
                names.append(key.group(1))
        del block
    return names


class TechBoardActionsIntoLeftToolbarRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read(WB_HTML)
        cls.js = read(WB_JS)
        cls.embed = read(EMBED)
        cls.runtime = read(RUNTIME)
        cls.bridge = read(BRIDGE)
        cls.main = read(MAIN)
        cls.pages = {stage: [read(p) for p in paths] for stage, paths in ACTION_PAGES.items()}
        cls.all_actions = sorted({name for texts in cls.pages.values() for text in texts
                                  for name in action_names_of(text)})

    # ------------------------------------------------------------ 左侧操作栏：删除与保留
    def test_left_toolbar_drops_attachment_button(self):
        for bad in ("techChatAttach", "openChatFilePicker"):
            with self.subTest(token=bad):
                self.assertNotIn(bad, self.html, f"左侧工具栏仍保留附件按钮 {bad}")
                self.assertNotIn(bad, self.js, f"父壳仍保留附件按钮逻辑 {bad}")
        # 上传入口只保留输入区圆形 ＋
        self.assertIn('id="ocChatAttachBtn"', self.html)
        self.assertIn('id="ocChatFileInput"', self.html)

    def test_static_per_action_buttons_are_replaced_by_dynamic_rendering(self):
        for bad in REMOVED_LEFT_TOOLBAR_IDS[1:]:
            with self.subTest(token=bad):
                self.assertNotIn(f'id="{bad}"', self.html,
                                 f"左侧仍写死业务按钮 {bad}，应改为按看板快照动态渲染")
        for gone in RETIRED_LEFT_TOOLBAR_IDS:
            with self.subTest(retired=gone):
                self.assertNotIn(f'id="{gone}"', self.html, f"已退役的通用按钮 {gone} 不得回到父壳")
                self.assertNotIn(gone, self.js, f"父壳不得再驱动已退役按钮 {gone}")
        for needed in REQUIRED_LEFT_TOOLBAR_IDS + KEPT_KEEP_IDS:
            with self.subTest(token=needed):
                self.assertIn(f'id="{needed}"', self.html, f"既有节点 {needed} 被删除")

    def test_left_toolbar_renders_board_snapshot_actions_dynamically(self):
        for token in ("data-tech-action", "createElement('button')",
                      "insertAdjacentElement('afterend')",
                      "[data-tech-action]"):
            with self.subTest(token=token):
                self.assertIn(token, self.js, f"父壳缺少动态动作按钮契约：{token}")

    def test_left_toolbar_helpers_exist(self):
        for fn in ("boardActionEntries", "primaryActionName", "actionTooltip", "syncChatActionList"):
            with self.subTest(fn=fn):
                self.assertRegex(self.js, rf"function\s+{fn}\s*\(",
                                 f"父壳缺少 {fn}()：左侧工具栏必须只有这一条渲染路径")

    # ------------------------------------------------------------ 唯一主按钮 + tooltip
    def test_exactly_one_primary_variant_assignment(self):
        sites = re.findall(r"variant\s*:\s*['\"]primary['\"]", self.js)
        self.assertEqual(len(sites), 1,
                         "父壳只允许一处 variant: 'primary'（唯一主按钮槽位），当前 %d 处" % len(sites))
        self.assertRegex(self.js, r"id=\"techChatPrimary\"|'techChatPrimary'",
                         "唯一主按钮必须落在既有 #techChatPrimary 槽位")

    def test_primary_selection_is_driven_by_board_role(self):
        # 第 33 批反转：父壳不再「在多个 primary 里确定性地取第一个」—— 可见 primary 恰好一个
        # 才认；0 个或多个都返回空并交给 primaryDiagnostic() 出确定性诊断（见新 Spec）。
        role_block = block_from(self.js, "function primaryEntries(")
        self.assertTrue(role_block, "缺少 primaryEntries()")
        self.assertRegex(role_block, r"role\s*===\s*['\"]primary['\"]",
                         "主按钮必须来自看板声明的 role === 'primary'")
        body = block_from(self.js, "function primaryActionName(")
        self.assertTrue(body, "缺少 primaryActionName()")
        self.assertRegex(body, r"length\s*!==\s*1",
                         "可见 primary 必须恰好一个才认，不得取第一个")
        guard = body.find("length !== 1")
        pick = body.find("[0]")
        self.assertGreaterEqual(guard, 0, "缺少唯一性守卫")
        if pick >= 0:
            self.assertLess(guard, pick, "唯一性守卫必须先于取值，多个 primary 时不得静默取第一个")
        self.assertGreaterEqual(len(re.findall(r"primaryName", self.js)), 2,
                                "primaryName 必须同时用于选中与排除，避免同一动作渲染两次")
        self.assertRegex(self.js, r"(?:===|!==)\s*primaryName",
                         "动态按钮列表必须排除已渲染为主按钮的那个动作")

    def test_every_button_has_tooltip_and_aria_label(self):
        body = block_from(self.js, "function actionTooltip(")
        self.assertTrue(body, "缺少 actionTooltip()")
        # 契约更新（「业务动作一律可点、点了再给真实原因」批次）：按钮不再有「当前不可用」这种
        # 预先置灰的文案，tooltip 只区分执行中 / 未打开项目 / 当前步骤主操作。
        for token in ("hint", "执行中", "请先打开项目", "当前步骤主操作"):
            with self.subTest(token=token):
                self.assertIn(token, body, f"tooltip 规则缺少 {token}")
        self.assertIn("setAttribute('aria-label'", self.js, "动态按钮必须写 aria-label")
        self.assertRegex(self.js, r"\.title\s*=", "按钮必须写 title 才有悬浮提示")

    # ------------------------------------------------------------ 父壳不再写死业务动作
    def test_parent_stops_hardcoding_business_action_names(self):
        allowlist = {"refreshData"}
        leaked = sorted(name for name in self.all_actions
                        if name not in allowlist and name in self.js)
        self.assertEqual(leaked, [],
                         "父壳仍在写死业务动作名（左侧入口必须完全由看板快照驱动）：%s" % leaked)
        for gone in ("STAGE_ACTIONS", "STAGE_CHAT_ACTIONS"):
            with self.subTest(token=gone):
                self.assertNotIn(gone, self.js, f"{gone} 应被 STAGES 描述表 + 看板快照取代")

    def test_stage_flow_table_covers_nine_stages_without_action_names(self):
        # 契约更新（第 19 步「九阶段上下文」批次）：壳导航表定名 STAGES，只描述九阶段的编号 /
        # 名称 / 页面文件，不再携带任何业务动作名（动作由看板快照决定）。
        start = self.js.find("const STAGES = [")
        self.assertGreater(start, -1, "缺少九阶段描述表 STAGES")
        end = self.js.find("];", start)
        self.assertGreater(end, start, "STAGES 描述表没有正常结束")
        block = self.js[start:end]
        for stage in STAGE_PAGES:
            with self.subTest(stage=stage):
                self.assertIn(stage, block, f"STAGES 缺少 {stage}")
        for token in ("primary:", "secondary:"):
            with self.subTest(token=token):
                self.assertNotIn(token, block, "壳导航表不得再写死业务动作名")

    def test_bottom_bar_business_buttons_are_retired(self):
        for bad in ("techPrimary", "techSecondary"):
            with self.subTest(token=bad):
                self.assertNotIn(f'id="{bad}"', self.html, f"右侧底栏业务按钮 {bad} 与左侧重复")
                self.assertNotIn(f"$('{bad}')", self.js, f"父壳仍在驱动底栏业务按钮 {bad}")
        self.assertNotIn("syncActionBar", self.js, "底栏业务代理 syncActionBar() 应退役")

    def test_existing_left_toolbar_channels_are_kept(self):
        self.assertIn("TechBoardBridge", self.js)
        self.assertRegex(self.js, r"executeAction\(", "业务动作仍必须经 TechBoardBridge.executeAction")
        self.assertRegex(self.js, r"applyStage\(", "上一步 / 下一步仍复用既有 applyStage")
        # 契约更新：常驻「失败重试」按钮与其「最近一次动作」缓存已随通用导航按钮一起退役，
        # 失败恢复改由「业务动作始终可点 + 真实错误落到标题行」承担。
        self.assertNotRegex(self.js, r"last[A-Za-z]*Action",
                            "最近一次动作缓存应随失败重试按钮一起退役")
        for token in ("setBoardNotice", "task-failed"):
            with self.subTest(token=token):
                self.assertIn(token, self.js, f"失败恢复仍须有真实错误出口：{token}")
        for bad in ("contentDocument", "contentWindow.document"):
            self.assertNotIn(bad, self.js, f"父壳不得查询 iframe DOM：{bad}")
        for bad in ("techChatAction", "tech-board-action", "@app.post(\"/api/tech-board"):
            self.assertNotIn(bad, self.main, "不得为左侧入口新增后端路由")

    # ------------------------------------------------------------ 看板动作元数据
    def test_runtime_passes_role_order_hint_to_parent(self):
        body = block_from(self.runtime, "function entryState(")
        self.assertTrue(body, "缺少 entryState()")
        for token in ("role:", "order:", "hint:"):
            with self.subTest(token=token):
                self.assertIn(token, body, f"entryState() 未透传 {token}")
        self.assertIn("'primary'", body)
        self.assertIn("'aux'", body)
        self.assertRegex(body, r"'primary'\s*:\s*'aux'|'primary'[^\n]*\?\s*'primary'",
                         "非白名单 role 必须降级为 'aux'")
        self.assertIn("ACTION_STATE", self.runtime)
        for event in ("ready", "action-state", "task-progress", "task-completed", "task-failed",
                      "selection-changed"):
            with self.subTest(event=event):
                self.assertIn(event, self.runtime, f"既有 state 事件 {event} 被改动")
        self.assertIn("state.actions", self.bridge, "父壳桥仍必须把动作快照存进 state.actions")

    def test_each_stage_declares_roles_and_single_primary(self):
        for stage, texts in self.pages.items():
            combined = "\n".join(texts)
            with self.subTest(stage=stage):
                self.assertIn("role:", combined, f"{stage} 的看板动作没有声明 role")
                self.assertIn("order:", combined, f"{stage} 的看板动作没有声明 order")
                static_primary = len(re.findall(r"role\s*:\s*['\"]primary['\"]", combined))
                self.assertLessEqual(static_primary, 1,
                                     f"{stage} 静态声明了 {static_primary} 个 primary，只允许 1 个")
                dynamic = re.search(r"role\s*:\s*[^\n]*\?\s*['\"]primary['\"][^\n]*:\s*['\"]aux['\"]",
                                    combined)
                self.assertTrue(static_primary or dynamic,
                                f"{stage} 没有任何主按钮（静态或动态）声明")

    def test_assemble_step_registers_its_page_only_actions(self):
        js = "\n".join(self.pages["process"])
        expected = {
            "generateIntegrationParams": "aiGenerate('params')",
            "generateIntegrationProcess": "aiGenerate('process')",
            "saveIntegrationParams": "aiSaveEdits('params')",
            "confirmIntegrationParams": "aiConfirmStep('params')",
            "confirmIntegrationProcess": "aiConfirmStep('process')",
            "autofillIntegrationParams": "aiParamsAutofill()",
            "saveIntegrationParamsFinal": "aiParamsFinalize(false)",
            "confirmIntegrationParamsFinal": "aiParamsFinalize(true)",
        }
        for name, call in expected.items():
            with self.subTest(action=name):
                self.assertIn(f"{name}:", js, f"2.2 未注册动作 {name}")
                idx = js.find(f"{name}:")
                self.assertIn(call, js[idx:idx + 900],
                              f"{name} 必须复用既有实现 {call}，不得另写一套")
        # 参数推荐 / 组装工艺专属按钮按当前看板页签决定可见性
        self.assertRegex(js, r"aiTab\s*===\s*'params'[\s\S]{0,200}visible",
                         "参数推荐专属动作必须按 aiTab === 'params' 决定 visible")
        self.assertRegex(js, r"aiTab\s*===\s*'process'[\s\S]{0,200}visible",
                         "组装工艺专属动作必须按 aiTab === 'process' 决定 visible")

    # ------------------------------------------------------------ 右侧去重与能力保留
    def test_duplicate_buttons_are_hidden_in_embed_scope_only(self):
        for selector in EMBED_HIDDEN:
            with self.subTest(selector=selector):
                hits = [line for line in self.embed.splitlines() if selector in line]
                self.assertTrue(hits, f"tech-embed.js 未隐藏嵌入态的重复入口 {selector}")
                for line in hits:
                    self.assertIn(".tech-embed", line,
                                  f"{selector} 的隐藏规则必须限定在 .tech-embed 作用域：{line.strip()}")
        for selector in MUST_STAY_VISIBLE:
            with self.subTest(selector=selector):
                for line in self.embed.splitlines():
                    if selector in line:
                        self.fail(f"{selector} 是被保留的输入 / 说明，不得隐藏：{line.strip()}")

    def test_board_keeps_every_underlying_implementation(self):
        drawing = "\n".join(self.pages["drawing"])
        process = "\n".join(self.pages["process"])
        cost = "\n".join(self.pages["cost"])
        req = "\n".join(self.pages["requirement-create"])
        for token in ("startAllPartProcesses", "parseDrawing("):
            with self.subTest(token=token):
                self.assertIn(token, drawing, f"2.1 底层实现 {token} 被删除")
        for token in ("aiRunIntegration", "aiConfirmStep(", "aiParamsAutofill(", "aiParamsFinalize(",
                      "aiSaveEdits(", "aiGenerate("):
            with self.subTest(token=token):
                self.assertIn(token, process, f"2.2 底层实现 {token} 被删除")
        for token in ("crRunAll(", "crConfirmCost(", "crRunOp("):
            with self.subTest(token=token):
                self.assertIn(token, cost, f"2.3 底层实现 {token} 被删除")
        for token in ("rcExtractRequirementFields", "/requirement/extract-documents"):
            with self.subTest(token=token):
                self.assertIn(token, req, f"1.1 需求解析链路 {token} 被删除")

    def test_upload_entries_stay_inside_the_board(self):
        html = read(F / "assembly-integration.html")
        js = "\n".join(self.pages["process"])
        for token in ("aiUploadBtn", "aiDrawingInput"):
            with self.subTest(token=token):
                self.assertIn(token, html + js, f"上传入口 {token} 必须留在 2.2 看板内（跨文档点击会丢 user activation）")
        idx = js.find("openIntegrationDrawings:")
        self.assertGreater(idx, -1, "缺少 openIntegrationDrawings 动作")
        self.assertIn("aiSetTab('drawings')", js[idx:idx + 700],
                      "openIntegrationDrawings 仍应只切页签 + 聚焦上传入口")


if __name__ == "__main__":
    unittest.main()
