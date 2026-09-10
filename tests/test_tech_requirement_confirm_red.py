"""第 9 步红测：1.2 确认需求的 Agent 动作接通。

覆盖：
1. 后端新增 5 个确认相关平台工具，并复用既有预检 / confirm / return 实现；
2. 通过 / 退回必须有显式确认门，未确认不改状态；
3. UI 动作映射新增 fill-confirmation-note，且不把审批动作接成自动执行；
4. 看板（requirement-confirm-page.js）注册 applyConfirmationNote 并上报确认进度；
5. 左侧（agent-chat.js）经桥带入确认意见并渲染确认摘要 / 需确认提示；
6. 不新增第二套预检或流转实现、不新增 HTTP 路由、既有工具与路由不减少。

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
    "GetRequirementPrecheck", "GetRequirementClarifications",
    "SaveRequirementConfirmationNote", "ConfirmRequirement",
    "ReturnRequirementToDraft",
)
NEW_UI_ACTIONS = {
    "SaveRequirementConfirmationNote": "fill-confirmation-note",
    "ConfirmRequirement": "refresh-requirement",
    "ReturnRequirementToDraft": "refresh-requirement",
}
# 审批类动作绝不能映射成 tool_use 阶段就自动执行的动作。
FORBIDDEN_UI_ACTIONS = ("confirm-requirement", "return-requirement")
KEPT_UI_ACTIONS = (
    "parse", "refresh-ir", "refresh-integration", "integration-step",
)
KEPT_TOOLS = (
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "LookupComponentLibrary", "LookupProcessLibrary", "LookupCostLibrary",
    "UpdatePartParameters", "RequestParse", "GetIntegrationState",
    "ListIntegrationParams", "UpdateIntegrationParams", "UpdateIntegrationProcess",
    "RequestIntegrationStep",
)
KEPT_ROUTES = (
    "/api/projects/{project_id}/requirement",
    "/api/projects/{project_id}/requirement/precheck",
    "/api/projects/{project_id}/requirement/confirm",
    "/api/projects/{project_id}/requirement/return-to-draft",
    "/api/projects/{project_id}/requirement/submit-confirmation",
)
# 预检 / 流转实现唯一性哨兵：必须各只存在于一个后端文件，且不是 oc_agent.py。
SINGLE_IMPL_SENTINELS = (
    "基于已保存需求字段的确定性完整性检查",
    "workflow:requirement_confirmed",
    "workflow:requirement_returned",
)


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


class TechRequirementConfirmRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _read(MAIN)
        cls.agent = _read(AGENT_SERVICE)
        cls.board = _read(FRONTEND / "requirement-confirm-page.js")
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.parent_html = _read(FRONTEND / "tech-workbench.html")

    # ---------------------------------------------------------------- 后端工具
    def test_backend_declares_confirmation_agent_tools(self):
        for tool in NEW_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent,
                          f"1.2 平台工具 {tool} 没有声明")

    def test_backend_dispatches_confirmation_tools(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertTrue(body, "找不到 _run_platform_tool 分派入口")
        for tool in NEW_TOOLS:
            self.assertIn(f'"{tool}"', body,
                          f"平台工具 {tool} 没有接进 _run_platform_tool")

    def test_confirmation_actions_require_explicit_confirmation(self):
        """通过 / 退回必须显式确认：未确认只回执，绝不改状态。"""
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertIn("requires_confirmation", body,
                      "确认类工具必须返回 requires_confirmation 回执")
        self.assertIn("confirmed", body,
                      "确认类工具必须依据 confirmed 参数才执行流转")
        for tool in ("ConfirmRequirement", "ReturnRequirementToDraft"):
            self.assertIn(f'"{tool}"', body, f"确认工具 {tool} 未分派")

    def test_ui_action_mapping_for_confirmation(self):
        match = re.search(r"UI_ACTION_TOOLS\s*=\s*\{([\s\S]*?)\n\}", self.agent)
        self.assertIsNotNone(match, "UI_ACTION_TOOLS 映射不存在")
        body = match.group(1)
        for tool, action in NEW_UI_ACTIONS.items():
            self.assertRegex(
                body, rf'"{tool}"\s*:\s*"{re.escape(action)}"',
                f"UI 动作映射缺少 {tool} → {action}")
        for forbidden in FORBIDDEN_UI_ACTIONS:
            self.assertNotIn(f'"{forbidden}"', body,
                             f"审批动作 {forbidden} 不能接成自动执行，否则绕过确认门")
        for action in KEPT_UI_ACTIONS:
            self.assertIn(f'"{action}"', body, f"既有 UI 动作 {action} 被改动")

    def test_confirmation_reuses_single_implementation(self):
        """预检与流转必须各只有一份实现，且不能在 oc_agent.py 里重写。"""
        for sentinel in SINGLE_IMPL_SENTINELS:
            hits = _backend_files_with(sentinel)
            self.assertEqual(len(hits), 1,
                             f"实现 {sentinel} 必须只存在于一个后端文件，实际：{hits}")
            self.assertNotIn("oc_agent.py", hits,
                             f"oc_agent.py 不得自带第二套实现（{sentinel}）")
        self.assertNotIn("def _requirement_precheck(", self.agent,
                         "oc_agent.py 不得自带第二份预检实现")

    def test_existing_tools_and_routes_not_reduced(self):
        for tool in KEPT_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent, f"既有平台工具 {tool} 被删除")
        for route in KEPT_ROUTES:
            self.assertIn(f'"{route}"', self.main, f"既有需求路由 {route} 被删除")

    # ---------------------------------------------------------------- 看板
    def test_board_registers_confirmation_note_action(self):
        blocks = re.findall(r"registerActions\(\s*\{([\s\S]*?)\n\s*\}\)", self.board)
        self.assertTrue(blocks, "requirement-confirm-page.js 没有 registerActions 注册块")
        block = "\n".join(blocks)
        self.assertRegex(block, r"applyConfirmationNote\s*:",
                         "看板没有注册 applyConfirmationNote 动作")
        self.assertIn("confirmationNote", block,
                      "applyConfirmationNote 必须写入既有 #confirmationNote")
        for kept in ("confirmRequirement", "returnRequirementDraft"):
            self.assertRegex(block, rf"{kept}\s*:", f"既有动作 {kept} 被删除")

    def test_board_reports_confirmation_progress_through_runtime(self):
        self.assertIn("TechBoardRuntime", self.board,
                      "确认进度必须经 TechBoardRuntime 上报给父壳")
        self.assertRegex(
            self.board, r"[\"'](?:task-progress|task-completed|task-failed)[\"']",
            "看板没有上报确认任务的进度事件")

    # ---------------------------------------------------------------- 左侧
    def test_left_applies_confirmation_note_and_renders_summary(self):
        self.assertRegex(
            self.chat, r'["\']fill-confirmation-note["\']',
            "左侧没有处理 fill-confirmation-note UI 动作")
        self.assertRegex(
            self.chat, r'executeAction\(\s*["\']applyConfirmationNote["\']',
            "左侧没有经桥把确认意见带入看板")
        for token in ("generated_note", "need_info", "requires_confirmation"):
            self.assertIn(token, self.chat,
                          f"左侧确认摘要缺少 {token}")

    def test_no_duplicate_confirmation_entry(self):
        self.assertIn("requirement/confirm", self.board,
                      "看板必须仍调用既有需求确认接口")
        self.assertIn("requirement/return-to-draft", self.board,
                      "看板必须仍调用既有需求退回接口")
        self.assertNotIn("requirement/confirm", self.chat,
                         "左侧不得直接调用需求确认接口，必须经确认门 / 看板")
        self.assertNotIn("requirement/return-to-draft", self.chat,
                         "左侧不得直接调用需求退回接口，必须经确认门 / 看板")

    # ---------------------------------------------------------------- 边界
    def test_parent_shell_has_no_confirmation_form_dom(self):
        for marker in ("confirmationNote", "confirmPass", "returnDraft"):
            self.assertNotIn(marker, self.parent_html,
                             f"确认表单必须留在右侧看板 iframe 内（父壳出现 {marker}）")


if __name__ == "__main__":
    unittest.main()
