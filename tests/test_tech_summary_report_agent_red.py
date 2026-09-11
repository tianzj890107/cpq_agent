"""第 13 步红测：3.1 汇总报告的 Agent 动作接通。

覆盖：
1. 新增 6 个 3.1 平台工具（获取汇总数据 / 准备报告 / 生成报告草稿 / 更新报告字段 /
   保存发布范围 / 提交审核）并复用既有 summary / process-report 实现；
2. 只读工具复用既有汇总与报告数据源，oc_agent 不自造汇总或报告生成逻辑；
3. 提交审核有显式确认门，且不改写单据号 / 编制人 / 审核发布留痕；
4. UI 动作映射统一为 refresh-report，送审不接成自动执行；
5. 3.1 看板注册 refresh / draft / fields / distribution 动作并经 TechBoardRuntime 上报进度；
6. 报告字段与调用都落在右侧看板，左侧不直接调 /process-report 或 /summary；
7. 既有平台工具与 summary / process-report 路由不减少。

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
    "GetSummaryData", "GetProcessReport", "GenerateProcessReportDraft",
    "UpdateProcessReportFields", "SaveProcessReportDistribution",
    "SubmitProcessReportReview",
)
REFRESH_TOOLS = ("GenerateProcessReportDraft", "UpdateProcessReportFields",
                 "SaveProcessReportDistribution", "SubmitProcessReportReview")
GATED_TOOLS = ("SubmitProcessReportReview",)
FORBIDDEN_UI_ACTIONS = ("submit-report", "approve-report", "publish-report")
KEPT_UI_ACTIONS = ("parse", "refresh-ir", "refresh-integration", "integration-step",
                   "extract-requirement", "refresh-requirement", "fill-review-note")
KEPT_TOOLS = (
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "UpdatePartParameters", "RequestParse", "GetIntegrationState",
    "RequestIntegrationStep", "GetRequirementDraft", "ExtractRequirement",
    "ConfirmRequirement", "ApproveRequirementReview", "RejectRequirementReview",
)
KEPT_ROUTES = (
    "/api/projects/{project_id}/summary",
    "/api/projects/{project_id}/summary/recommend",
    "/api/projects/{project_id}/summary/confirm",
    "/api/projects/{project_id}/summary.html",
    "/api/projects/{project_id}/summary.md",
    "/api/projects/{project_id}/process-report",
    "/api/projects/{project_id}/process-report/prepare",
    "/api/projects/{project_id}/process-report/submit-review",
    "/api/projects/{project_id}/process-report/distribution",
    "/api/projects/{project_id}/process-report/versions",
)
# 状态流转 / 审计实现唯一性哨兵：各只存在于一个后端文件，且不是 oc_agent.py。
SINGLE_IMPL_SENTINELS = ("workflow:report_prepared", "workflow:report_saved",
                         "workflow:report_submitted")
# oc_agent.py 里绝不允许出现的“第二套实现 / 越权直写”。
FORBIDDEN_AGENT_SNIPPETS = (
    "store.save_process_report(", 'doc.status = "in_review"',
    "summary_svc.recommend(", "workflow:report_",
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


class TechSummaryReportAgentRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _read(MAIN)
        cls.agent = _read(AGENT_SERVICE)
        cls.board = _read(FRONTEND / "summary-result.js")
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.parent_html = _read(FRONTEND / "tech-workbench.html")

    # ---------------------------------------------------------------- 后端工具
    def test_backend_declares_summary_report_tools(self):
        for tool in NEW_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent,
                          f"3.1 平台工具 {tool} 没有声明")

    def test_backend_dispatches_summary_report_tools(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertTrue(body, "找不到 _run_platform_tool 分派入口")
        for tool in NEW_TOOLS:
            self.assertIn(f'"{tool}"', body,
                          f"平台工具 {tool} 没有接进 _run_platform_tool")

    def test_read_tools_reuse_existing_summary_and_report_sources(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertRegex(body, r"(summary_svc\.aggregate|summary\.aggregate)",
                         "获取汇总数据必须复用 services/summary 的 aggregate")
        self.assertIn("store.load_process_report", body,
                      "准备报告必须复用既有报告数据源")
        for snippet in ("summary_svc.recommend(",):
            self.assertNotIn(snippet, self.agent,
                             f"3.1 不得触发模型汇总（那是既有 async 路由的职责）：{snippet}")

    def test_submit_review_requires_explicit_confirmation(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        for tool in GATED_TOOLS:
            branch = _branch(body, tool)
            self.assertTrue(branch, f"{tool} 未分派")
            self.assertIn("requires_confirmation", branch,
                          f"{tool} 必须返回 requires_confirmation 回执")
            self.assertIn("confirmed", branch,
                          f"{tool} 必须依据 confirmed 参数才执行")

    def test_report_workflow_reuses_single_implementation(self):
        for sentinel in SINGLE_IMPL_SENTINELS:
            hits = _backend_files_with(sentinel)
            self.assertEqual(len(hits), 1,
                             f"实现 {sentinel} 必须只存在于一个后端文件，实际：{hits}")
            self.assertNotIn("oc_agent.py", hits,
                             f"oc_agent.py 不得自带第二套实现（{sentinel}）")
        for snippet in FORBIDDEN_AGENT_SNIPPETS:
            self.assertNotIn(snippet, self.agent,
                             f"oc_agent.py 不得越权直写 / 重写 3.1 实现：{snippet}")

    def test_ui_action_mapping_for_summary_report(self):
        match = re.search(r"UI_ACTION_TOOLS\s*=\s*\{([\s\S]*?)\n\}", self.agent)
        self.assertIsNotNone(match, "UI_ACTION_TOOLS 映射不存在")
        body = match.group(1)
        for tool in REFRESH_TOOLS:
            self.assertRegex(
                body, rf'"{tool}"\s*:\s*"refresh-report"',
                f"UI 动作映射缺少 {tool} → refresh-report")
        for forbidden in FORBIDDEN_UI_ACTIONS:
            self.assertNotIn(f'"{forbidden}"', body,
                             f"送审不能接成自动执行的动作：{forbidden}")
        for action in KEPT_UI_ACTIONS:
            self.assertIn(f'"{action}"', body, f"既有 UI 动作 {action} 被改动")

    def test_existing_tools_and_routes_not_reduced(self):
        for tool in KEPT_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent, f"既有平台工具 {tool} 被删除")
        for route in KEPT_ROUTES:
            self.assertIn(f'"{route}"', self.main, f"既有 summary 路由 {route} 被删除")

    # ---------------------------------------------------------------- 看板
    def test_board_registers_summary_report_actions(self):
        self.assertIn("registerActions", self.board,
                      "summary-result.js 没有 registerActions 注册")
        for name in ("refreshProcessReport", "generateProcessReportDraft",
                     "updateProcessReportFields", "saveProcessReportDistribution"):
            self.assertRegex(self.board, rf"{name}\s*:",
                             f"看板没有注册 {name} 动作")
        for kept in ("saveProcessReport", "submitProcessReportReview"):
            self.assertRegex(self.board, rf"{kept}\s*:", f"既有动作 {kept} 被删除")

    def test_board_reports_report_progress_through_runtime(self):
        self.assertIn("TechBoardRuntime", self.board,
                      "3.1 进度必须经 TechBoardRuntime 上报给父壳")
        self.assertRegex(
            self.board, r"[\"'](?:task-progress|task-completed|task-failed)[\"']",
            "看板没有上报报告任务的进度事件")

    # ---------------------------------------------------------------- 左侧
    def test_left_handles_refresh_report_ui_action(self):
        self.assertRegex(self.chat, r'["\']refresh-report["\']',
                         "左侧没有处理 refresh-report UI 动作")
        self.assertRegex(self.chat, r'executeAction\(\s*["\']refreshProcessReport["\']',
                         "左侧没有经桥触发看板的 refreshProcessReport")

    def test_report_results_refresh_board_not_chat_only(self):
        self.assertRegex(self.board, r"/process-report",
                         "看板必须仍是 process-report 接口的调用方")
        for literal in ("/process-report", "/summary"):
            self.assertNotIn(literal, self.chat,
                             f"左侧不得直接调用 {literal}，必须经看板桥")

    # ---------------------------------------------------------------- 边界
    def test_parent_shell_has_no_summary_form_dom(self):
        for marker in ("srSave", "srSubmit", "srReportNo", "srDistributionScope"):
            self.assertNotIn(marker, self.parent_html,
                             f"3.1 表单必须留在右侧看板 iframe 内（父壳出现 {marker}）")


if __name__ == "__main__":
    unittest.main()
