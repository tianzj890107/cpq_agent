"""第 11 步红测：2.2 组装与整合的 Agent 动作接通。

覆盖：
1. 修复既有缺口：统一左侧会话处理 refresh-integration / integration-step；
2. 新增 4 个 2.2 平台工具（整合图纸上传 / 参数确认 / 工序确认 / 发送财务）并复用既有实现；
3. 发送财务必须有显式确认门；
4. UI 动作映射新增 open-integration-drawings，发送财务不接成自动执行；
5. 2.2 看板注册 refresh / step / open-drawings 动作并经 TechBoardRuntime 上报进度；
6. 参数、工序、成本必须刷新右侧看板（走看板桥），左侧不直接调 /integration/*。

不联网、不起服务、不读真实业务数据。
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
BACKEND = ROOT / "tech_app" / "backend"
MAIN = BACKEND / "main.py"
AGENT_SERVICE = BACKEND / "services" / "oc_agent.py"

NEW_TOOLS = (
    "UploadIntegrationDrawing", "ConfirmIntegrationParams",
    "ConfirmIntegrationProcess", "SendIntegrationToFinance",
)
NEW_UI_ACTIONS = {
    "UploadIntegrationDrawing": "open-integration-drawings",
    "ConfirmIntegrationParams": "refresh-integration",
    "ConfirmIntegrationProcess": "refresh-integration",
    "SendIntegrationToFinance": "refresh-integration",
}
KEPT_UI_ACTIONS = ("parse", "refresh-ir", "refresh-integration", "integration-step")
FORBIDDEN_UI_ACTIONS = ("send-to-finance", "auto-send-finance")
# 2.2 既有工具：一个都不能少。
KEPT_TOOLS = (
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "LookupComponentLibrary", "LookupProcessLibrary", "LookupCostLibrary",
    "UpdatePartParameters", "RequestParse", "GetIntegrationState",
    "ListIntegrationParams", "UpdateIntegrationParams", "UpdateIntegrationProcess",
    "RequestIntegrationStep",
)
KEPT_ROUTES = (
    "/api/projects/{project_id}/integration",
    "/api/projects/{project_id}/integration/drawings",
    "/api/projects/{project_id}/integration/params",
    "/api/projects/{project_id}/integration/params/confirm",
    "/api/projects/{project_id}/integration/process",
    "/api/projects/{project_id}/integration/process/confirm",
    "/api/projects/{project_id}/integration/cost",
    "/api/projects/{project_id}/integration/confirm",
    "/api/projects/{project_id}/integration/send-to-finance",
)
# 确认 / 发送流转实现唯一性哨兵：各只存在于一个后端文件，且不是 oc_agent.py。
SINGLE_IMPL_SENTINELS = ("integration_params_confirm", "integration_process_confirm")
FORBIDDEN_AGENT_SNIPPETS = ("plan.params_confirmed = True", "plan.process_confirmed = True")


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _py_def_body(text, name):
    idx = text.find(f"def {name}(")
    if idx < 0:
        return ""
    rest = text[idx:]
    nxt = rest.find("\ndef ", 1)
    return rest[:nxt] if nxt != -1 else rest


def _backend_files_with(needle):
    hits = []
    for path in BACKEND.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if needle in _read(path):
            hits.append(path.name)
    return hits


class TechIntegrationAgentRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _read(MAIN)
        cls.agent = _read(AGENT_SERVICE)
        cls.board = _read(FRONTEND / "assembly-integration.js")
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.parent_html = _read(FRONTEND / "tech-workbench.html")

    # ---------------------------------------------------------------- 后端工具
    def test_backend_declares_integration_action_tools(self):
        for tool in NEW_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent,
                          f"2.2 平台工具 {tool} 没有声明")

    def test_backend_dispatches_integration_action_tools(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertTrue(body, "找不到 _run_platform_tool 分派入口")
        for tool in NEW_TOOLS:
            self.assertIn(f'"{tool}"', body,
                          f"平台工具 {tool} 没有接进 _run_platform_tool")

    def test_send_to_finance_requires_explicit_confirmation(self):
        """发送财务是对外动作：未 confirmed 只回执，绝不触发外呼。"""
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertIn('"SendIntegrationToFinance"', body, "发送财务工具未分派")
        self.assertIn("requires_confirmation", body,
                      "发送财务必须返回 requires_confirmation 回执")
        self.assertIn("confirmed", body,
                      "发送财务必须依据 confirmed 参数才执行")

    def test_ui_action_mapping_for_integration(self):
        match = re.search(r"UI_ACTION_TOOLS\s*=\s*\{([\s\S]*?)\n\}", self.agent)
        self.assertIsNotNone(match, "UI_ACTION_TOOLS 映射不存在")
        body = match.group(1)
        for tool, action in NEW_UI_ACTIONS.items():
            self.assertRegex(
                body, rf'"{tool}"\s*:\s*"{re.escape(action)}"',
                f"UI 动作映射缺少 {tool} → {action}")
        for forbidden in FORBIDDEN_UI_ACTIONS:
            self.assertNotIn(f'"{forbidden}"', body,
                             f"发送财务不能接成自动执行的动作：{forbidden}")
        for action in KEPT_UI_ACTIONS:
            self.assertIn(f'"{action}"', body, f"既有 UI 动作 {action} 被改动")

    def test_integration_reuses_single_implementation(self):
        """确认 / 发送流转必须只有一份实现，且不能在 oc_agent.py 里重写。"""
        for sentinel in SINGLE_IMPL_SENTINELS:
            hits = _backend_files_with(sentinel)
            self.assertEqual(len(hits), 1,
                             f"实现 {sentinel} 必须只存在于一个后端文件，实际：{hits}")
            self.assertNotIn("oc_agent.py", hits,
                             f"oc_agent.py 不得自带第二套实现（{sentinel}）")
        for snippet in FORBIDDEN_AGENT_SNIPPETS:
            self.assertNotIn(snippet, self.agent,
                             f"oc_agent.py 不得直写 2.2 确认状态：{snippet}")

    def test_existing_tools_and_routes_not_reduced(self):
        for tool in KEPT_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent, f"既有平台工具 {tool} 被删除")
        for route in KEPT_ROUTES:
            self.assertIn(f'"{route}"', self.main, f"既有 integration 路由 {route} 被删除")

    # ---------------------------------------------------------------- 看板
    def test_board_registers_integration_refresh_and_step_actions(self):
        blocks = re.findall(r"registerActions\(\s*\{([\s\S]*?)\n\s*\}\)", self.board)
        self.assertTrue(blocks, "assembly-integration.js 没有 registerActions 注册块")
        block = "\n".join(blocks)
        for name in ("refreshIntegration", "integrationStep", "openIntegrationDrawings"):
            self.assertRegex(block, rf"{name}\s*:",
                             f"看板没有注册 {name} 动作")
        for kept in ("runIntegration", "sendIntegrationToFinance"):
            self.assertRegex(block, rf"{kept}\s*:", f"既有动作 {kept} 被删除")

    def test_board_reports_integration_progress_through_runtime(self):
        self.assertIn("TechBoardRuntime", self.board,
                      "2.2 进度必须经 TechBoardRuntime 上报给父壳")
        self.assertRegex(
            self.board, r"[\"'](?:task-progress|task-completed|task-failed)[\"']",
            "看板没有上报整合任务的进度事件")

    # ---------------------------------------------------------------- 左侧
    def test_left_handles_integration_ui_actions(self):
        pairs = (
            ("refresh-integration", "refreshIntegration"),
            ("integration-step", "integrationStep"),
            ("open-integration-drawings", "openIntegrationDrawings"),
        )
        for action, board_action in pairs:
            self.assertRegex(
                self.chat, rf'["\']{re.escape(action)}["\']',
                f"左侧没有处理 {action} UI 动作")
            self.assertRegex(
                self.chat, rf'executeAction\(\s*["\']{board_action}["\']',
                f"左侧没有经桥触发看板的 {board_action}")

    def test_integration_results_refresh_board_not_chat_only(self):
        """参数 / 工序 / 成本必须落到右侧看板：左侧只发动作，不自己调接口。"""
        self.assertIn("aiUrl(", self.board, "看板必须仍是 integration 接口的调用方")
        self.assertNotIn("/integration", self.chat,
                         "左侧不得直接调用 /integration/*，必须经看板桥")

    # ---------------------------------------------------------------- 边界
    def test_parent_shell_has_no_integration_form_dom(self):
        for marker in ("aiBody", "aiStart", "aiParams"):
            self.assertNotIn(marker, self.parent_html,
                             f"2.2 表单必须留在右侧看板 iframe 内（父壳出现 {marker}）")


if __name__ == "__main__":
    unittest.main()
