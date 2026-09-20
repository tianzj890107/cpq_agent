"""第 8 步红测：1.1 创建需求的 Agent 动作接通。

覆盖：
1. 后端新增 7 个需求相关平台工具，并复用既有 store / requirement_extract / 任务表；
2. UI 动作映射新增 extract-requirement / refresh-requirement，旧动作名不变；
3. 看板（requirement-create.js）把一键解析注册成 extractRequirement 并上报解析进度；
4. 左侧（agent-chat.js）经桥触发解析并渲染需求解析摘要；
5. 不新增第二套提取实现、不新增 HTTP 路由、既有工具与路由不减少。

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
    "GetRequirementDraft", "UpdateRequirementFields", "AttachRequirementFiles",
    "ExtractRequirement", "GetRequirementTask", "GetRequirementAiFill",
    "SubmitRequirementConfirmation",
)
NEW_UI_ACTIONS = {
    "ExtractRequirement": "extract-requirement",
    "UpdateRequirementFields": "refresh-requirement",
    "AttachRequirementFiles": "refresh-requirement",
}
KEPT_UI_ACTIONS = ("parse", "refresh-ir", "refresh-integration", "integration-step")
KEPT_TOOLS = (
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "LookupComponentLibrary", "LookupProcessLibrary", "LookupCostLibrary",
    "UpdatePartParameters", "RequestParse", "GetIntegrationState",
    "ListIntegrationParams", "UpdateIntegrationParams", "UpdateIntegrationProcess",
    "RequestIntegrationStep",
)
KEPT_REQUIREMENT_ROUTES = (
    "/api/projects/{project_id}/requirement",
    "/api/projects/{project_id}/requirement/extract-documents",
    "/api/projects/{project_id}/requirement/submit-confirmation",
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


class TechRequirementAgentRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _read(MAIN)
        cls.agent = _read(AGENT_SERVICE)
        cls.board = _read(FRONTEND / "requirement-create.js")
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.parent_html = _read(FRONTEND / "tech-workbench.html")

    # ---------------------------------------------------------------- 后端工具
    def test_backend_declares_requirement_agent_tools(self):
        for tool in NEW_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent,
                          f"1.1 平台工具 {tool} 没有声明")

    def test_backend_dispatches_requirement_tools(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertTrue(body, "找不到 _run_platform_tool 分派入口")
        for tool in NEW_TOOLS:
            self.assertIn(f'"{tool}"', body,
                          f"平台工具 {tool} 没有接进 _run_platform_tool")

    def test_extract_requirement_does_not_reimplement_extraction(self):
        """一键解析必须复用既有 /requirement/extract-documents，而不是另起一套提取。"""
        definitions = []
        for path in BACKEND.rglob("*.py"):
            text = _read(path)
            if "def extract_requirement_fields(" in text:
                definitions.append(path.name)
        self.assertEqual(
            definitions, ["requirement_extract.py"],
            f"提取实现只允许存在于 requirement_extract.py，实际：{definitions}")
        self.assertNotIn(
            "def extract_requirement_fields(", self.agent,
            "oc_agent.py 不得自带第二份字段提取实现")
        route_at = self.main.find('"/api/projects/{project_id}/requirement/extract-documents"')
        self.assertNotEqual(route_at, -1, "既有 extract-documents 路由被删除")
        self.assertIn(
            "extract_requirement_fields", self.main[route_at:route_at + 6000],
            "既有 extract-documents 路由必须仍是真正执行提取的地方")

    def test_ui_action_mapping_for_requirement(self):
        match = re.search(r"UI_ACTION_TOOLS\s*=\s*\{([\s\S]*?)\n\}", self.agent)
        self.assertIsNotNone(match, "UI_ACTION_TOOLS 映射不存在")
        body = match.group(1)
        for tool, action in NEW_UI_ACTIONS.items():
            self.assertRegex(
                body, rf'"{tool}"\s*:\s*"{re.escape(action)}"',
                f"UI 动作映射缺少 {tool} → {action}")
        for action in KEPT_UI_ACTIONS:
            self.assertIn(f'"{action}"', body, f"既有 UI 动作 {action} 被改动")

    def test_existing_tools_and_routes_not_reduced(self):
        for tool in KEPT_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent, f"既有平台工具 {tool} 被删除")
        for route in KEPT_REQUIREMENT_ROUTES:
            self.assertIn(f'"{route}"', self.main, f"既有需求路由 {route} 被删除")

    # ---------------------------------------------------------------- 看板
    def test_board_registers_extract_requirement_action(self):
        blocks = re.findall(r"registerActions\(\s*\{([\s\S]*?)\n\s*\}\)", self.board)
        self.assertTrue(blocks, "requirement-create.js 没有 registerActions 注册块")
        block = "\n".join(blocks)
        self.assertRegex(block, r"extractRequirement\s*:",
                         "看板没有注册 extractRequirement 动作")
        self.assertIn("rcExtractRequirementFields", block,
                      "extractRequirement 必须复用既有 rcExtractRequirementFields()")
        for kept in ("saveRequirementDraft", "submitRequirement"):
            self.assertRegex(block, rf"{kept}\s*:", f"既有动作 {kept} 被删除")

    def test_board_reports_extract_progress_through_runtime(self):
        self.assertIn("TechBoardRuntime", self.board,
                      "解析进度必须经 TechBoardRuntime 上报给父壳")
        self.assertRegex(
            self.board, r"[\"'](?:task-progress|task-completed|task-failed)[\"']",
            "看板没有上报解析任务的进度事件")

    # ---------------------------------------------------------------- 左侧
    def test_left_triggers_extract_and_renders_requirement_summary(self):
        self.assertRegex(
            self.chat, r'["\']extract-requirement["\']',
            "左侧没有处理 extract-requirement UI 动作")
        self.assertRegex(
            self.chat, r'executeAction\(\s*["\']extractRequirement["\']',
            "左侧没有经桥触发看板的 extractRequirement")
        for token in ("document_extraction", "filled_fields", "recommended_fields",
                      "recommendation_confidence"):
            self.assertIn(token, self.chat,
                          f"左侧需求解析摘要缺少 {token}")

    def test_no_duplicate_extraction_entry(self):
        self.assertEqual(
            self.board.count("requirement/extract-documents"), 1,
            "一键解析必须只复用既有 extract-documents 接口（正好一处引用）")
        self.assertEqual(
            self.chat.count("requirement/extract-documents"), 0,
            "左侧不得直接调用需求提取接口，必须经桥交给看板")

    # ---------------------------------------------------------------- 边界
    def test_parent_shell_has_no_requirement_form_dom(self):
        for marker in ("requirementForm", "rcIndustry", "btnAiExtract", "aiExtractStatus"):
            self.assertNotIn(marker, self.parent_html,
                             f"需求表单必须留在右侧看板 iframe 内（父壳出现 {marker}）")


if __name__ == "__main__":
    unittest.main()
