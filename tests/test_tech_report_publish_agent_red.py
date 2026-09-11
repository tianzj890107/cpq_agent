"""第 15 步红测：3.3 发布并回传报价的 Agent 动作接通。

覆盖：
1. 新增 7 个 3.3 平台工具（发布状态 / 发布对象 / 更新发布范围 / 正式发布 /
   回传报价 / 新建报告版本 / 发布结果）并复用既有 process-report 与回传实现；
2. 只读工具复用既有报告 / 版本 / 整机回传数据源；
3. 正式发布 / 回传报价 / 新建版本都必须有显式确认门；
4. Agent 不能绕过审核状态：发布要求 approved、回传报价要求 published；
5. UI 动作映射统一为 refresh-report，发布 / 回传 / 新版本不接成自动执行；
6. 3.3 看板注册 refresh / send-to-quote / new-version 并经 TechBoardRuntime 上报进度；
7. 左侧经看板桥触发，不直接调 /process-report 或 /integration；
8. 既有平台工具与全部 process-report 路由不减少。

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
    "GetReportPublishState", "ListReportPublishRecipients", "UpdateReportDistribution",
    "PublishProcessReport", "SendReportToQuote", "CreateReportNewVersion",
    "GetReportPublishResult",
)
GATED_TOOLS = ("PublishProcessReport", "SendReportToQuote", "CreateReportNewVersion")
REFRESH_TOOLS = ("UpdateReportDistribution",) + GATED_TOOLS
FORBIDDEN_UI_ACTIONS = ("publish-report", "send-report-to-quote", "new-report-version",
                        "report-publish")
KEPT_UI_ACTIONS = ("parse", "refresh-ir", "extract-requirement",
                   "fill-confirmation-note", "fill-review-note")
KEPT_TOOLS = (
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "UpdatePartParameters", "RequestParse", "GetIntegrationState",
    "RequestIntegrationStep", "GetRequirementDraft", "ExtractRequirement",
    "ConfirmRequirement", "ApproveRequirementReview", "RejectRequirementReview",
)
KEPT_ROUTES = (
    "/api/projects/{project_id}/process-report",
    "/api/projects/{project_id}/process-report/distribution",
    "/api/projects/{project_id}/process-report/publish",
    "/api/projects/{project_id}/process-report/versions",
    "/api/projects/{project_id}/process-report/versions/{version}",
    "/api/projects/{project_id}/process-report/new-version",
)
# 发布 / 新版本 / 发布范围实现唯一性哨兵：各只存在于一个后端文件，且不是 oc_agent.py。
SINGLE_IMPL_SENTINELS = (
    "workflow:report_published", "workflow:report_new_version",
    "workflow:report_distribution_updated",
    "报告须审核通过后才能发布", "仅已发布报告可创建新版本",
)
FORBIDDEN_AGENT_SNIPPETS = (
    'doc.status = "published"', "store.save_process_report(",
    "cpq_bridge.send_to_quote(", "workflow:report_",
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


def _branch(body, tool):
    marker = f'if name == "{tool}"'
    idx = body.find(marker)
    if idx < 0:
        return ""
    rest = body[idx + len(marker):]
    nxt = rest.find('\n    if name == ')
    return rest[:nxt] if nxt != -1 else rest


def _backend_files_with(needle):
    hits = []
    for path in BACKEND.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if needle in _read(path):
            hits.append(path.name)
    return hits


class TechReportPublishAgentRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _read(MAIN)
        cls.agent = _read(AGENT_SERVICE)
        cls.board = _read(FRONTEND / "report-publish-result.js")
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.parent_html = _read(FRONTEND / "tech-workbench.html")

    # ---------------------------------------------------------------- 后端工具
    def test_backend_declares_report_publish_tools(self):
        for tool in NEW_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent,
                          f"3.3 平台工具 {tool} 没有声明")

    def test_backend_dispatches_report_publish_tools(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertTrue(body, "找不到 _run_platform_tool 分派入口")
        for tool in NEW_TOOLS:
            self.assertIn(f'"{tool}"', body,
                          f"平台工具 {tool} 没有接进 _run_platform_tool")

    def test_read_tools_reuse_existing_report_version_handoff_sources(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertIn("store.load_process_report", body,
                      "发布状态 / 发布对象必须复用既有报告数据源")
        self.assertIn("list_process_report_versions", body,
                      "发布结果必须复用既有版本数据源")

    def test_publish_tools_require_explicit_confirmation(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        for tool in GATED_TOOLS:
            branch = _branch(body, tool)
            self.assertTrue(branch, f"{tool} 未分派")
            self.assertIn("requires_confirmation", branch,
                          f"{tool} 必须返回 requires_confirmation 回执")
            self.assertIn("confirmed", branch,
                          f"{tool} 必须依据 confirmed 参数才执行")

    def test_agent_cannot_bypass_review_status(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        publish = _branch(body, "PublishProcessReport")
        self.assertIn("approved", publish,
                      "正式发布必须保留「审核通过(approved)」状态闸门")
        to_quote = _branch(body, "SendReportToQuote")
        self.assertIn("published", to_quote,
                      "回传报价必须要求报告已发布(published)")

    def test_publish_flow_reuses_single_implementation(self):
        for sentinel in SINGLE_IMPL_SENTINELS:
            hits = _backend_files_with(sentinel)
            self.assertEqual(len(hits), 1,
                             f"实现 {sentinel} 必须只存在于一个后端文件，实际：{hits}")
            self.assertNotIn("oc_agent.py", hits,
                             f"oc_agent.py 不得自带第二套实现（{sentinel}）")
        for snippet in FORBIDDEN_AGENT_SNIPPETS:
            self.assertNotIn(snippet, self.agent,
                             f"oc_agent.py 不得越权直写 / 重写 3.3 实现：{snippet}")

    def test_publish_route_keeps_director_permission_and_audit(self):
        body = _py_def_body(self.main, "publish_process_report")
        self.assertTrue(body, "既有发布路由 publish_process_report 被删除")
        self.assertIn("DIRECTOR_ROLES", body, "发布必须保留工艺技术总监权限校验")
        self.assertIn("store.audit(", body, "发布必须保留审计记录")

    def test_ui_action_mapping_for_report_publish(self):
        match = re.search(r"UI_ACTION_TOOLS\s*=\s*\{([\s\S]*?)\n\}", self.agent)
        self.assertIsNotNone(match, "UI_ACTION_TOOLS 映射不存在")
        body = match.group(1)
        for tool in REFRESH_TOOLS:
            self.assertRegex(body, rf'"{tool}"\s*:\s*"refresh-report"',
                             f"UI 动作映射缺少 {tool} → refresh-report")
        for forbidden in FORBIDDEN_UI_ACTIONS:
            self.assertNotIn(f'"{forbidden}"', body,
                             f"发布 / 回传不能接成自动执行的动作：{forbidden}")
        for action in KEPT_UI_ACTIONS:
            self.assertIn(f'"{action}"', body, f"既有 UI 动作 {action} 被改动")

    def test_existing_tools_and_routes_not_reduced(self):
        for tool in KEPT_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent, f"既有平台工具 {tool} 被删除")
        for route in KEPT_ROUTES:
            self.assertIn(f'"{route}"', self.main, f"既有发布路由 {route} 被删除")

    # ---------------------------------------------------------------- 看板
    def test_board_registers_report_publish_actions(self):
        self.assertIn("registerActions", self.board,
                      "report-publish-result.js 没有 registerActions 注册")
        for name in ("refreshProcessReport", "sendReportToQuote", "createReportNewVersion"):
            self.assertRegex(self.board, rf"{name}\s*:",
                             f"看板没有注册 {name} 动作")
        self.assertRegex(self.board, r"publishProcessReport\s*:",
                         "既有动作 publishProcessReport 被删除")

    def test_board_reports_publish_progress_through_runtime(self):
        self.assertIn("TechBoardRuntime", self.board,
                      "3.3 进度必须经 TechBoardRuntime 上报给父壳")
        self.assertRegex(
            self.board, r"[\"'](?:task-progress|task-completed|task-failed)[\"']",
            "看板没有上报发布任务的进度事件")

    # ---------------------------------------------------------------- 左侧
    def test_left_handles_report_publish_ui_action(self):
        self.assertRegex(self.chat, r'["\']refresh-report["\']',
                         "左侧没有处理 refresh-report UI 动作")
        self.assertRegex(self.chat, r'executeAction\(\s*["\']refreshProcessReport["\']',
                         "左侧没有经桥触发看板的 refreshProcessReport")

    def test_publish_results_refresh_board_not_chat_only(self):
        self.assertIn("process-report", self.board,
                      "看板必须仍是 process-report 接口的调用方")
        for literal in ("/process-report", "/integration"):
            self.assertNotIn(literal, self.chat,
                             f"左侧不得直接调用 {literal}，必须经看板桥")

    # ---------------------------------------------------------------- 边界
    def test_parent_shell_has_no_publish_form_dom(self):
        for marker in ("rpPrimary", "rpPrimaryAction", "rpPublish"):
            self.assertNotIn(marker, self.parent_html,
                             f"3.3 表单必须留在右侧看板 iframe 内（父壳出现 {marker}）")


if __name__ == "__main__":
    unittest.main()
