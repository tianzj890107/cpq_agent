"""第 14 步红测：3.2 报告审核的 Agent 动作接通。

覆盖：
1. 新增 5 个 3.2 平台工具（读取报告及版本 / 审核摘要 / 保存审核意见 / 审核通过 /
   退回汇总）并复用既有 process-report 审核实现；
2. 只读工具复用既有报告与版本数据源；
3. 审核通过 / 退回必须有显式确认门，Agent 不得代替人工审批；
4. 审核意见只回填看板（fill-report-review-note），不落盘、不改状态；
5. 审核判定 / 状态流转实现唯一，且既有 DIRECTOR_ROLES 权限校验与审计保留；
6. 3.2 看板注册 refresh / apply-note 并经 TechBoardRuntime 上报进度；
7. 左侧经看板桥触发，不直接调 /process-report；
8. 既有平台工具与 process-report 路由不减少。

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
    "GetProcessReportReview", "GetReportReviewSummary", "SaveReportReviewNote",
    "ApproveProcessReport", "RejectProcessReport",
)
GATED_TOOLS = ("ApproveProcessReport", "RejectProcessReport")
REFRESH_TOOLS = GATED_TOOLS
FORBIDDEN_UI_ACTIONS = ("approve-report", "reject-report", "review-report",
                        "publish-report")
KEPT_UI_ACTIONS = ("parse", "refresh-ir", "extract-requirement",
                   "fill-confirmation-note", "fill-review-note")
KEPT_TOOLS = (
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "UpdatePartParameters", "RequestParse", "GetIntegrationState",
    "RequestIntegrationStep", "GetRequirementDraft", "ExtractRequirement",
    "ConfirmRequirement", "ReturnRequirementToDraft", "ApproveRequirementReview",
    "RejectRequirementReview", "SaveRequirementReviewNote",
)
KEPT_ROUTES = (
    "/api/projects/{project_id}/process-report",
    "/api/projects/{project_id}/process-report/submit-review",
    "/api/projects/{project_id}/process-report/review",
    "/api/projects/{project_id}/process-report/distribution",
    "/api/projects/{project_id}/process-report/versions",
    "/api/projects/{project_id}/process-report/versions/{version}",
)
# 审核闸门实现唯一性哨兵：审核判定文案只应存在于一个后端文件，且不是 oc_agent.py。
SINGLE_IMPL_SENTINELS = (
    "报告送审后上游工艺数据已变化，请驳回并重新汇总后送审",
    "报告内容仍不满足通过条件",
)
FORBIDDEN_AGENT_SNIPPETS = (
    'doc.status = "approved"', 'doc.status = "rejected"',
    "store.save_process_report(", "workflow:report_",
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


class TechReportReviewAgentRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _read(MAIN)
        cls.agent = _read(AGENT_SERVICE)
        cls.board = _read(FRONTEND / "report-review-result.js")
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.parent_html = _read(FRONTEND / "tech-workbench.html")

    # ---------------------------------------------------------------- 后端工具
    def test_backend_declares_report_review_tools(self):
        for tool in NEW_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent,
                          f"3.2 平台工具 {tool} 没有声明")

    def test_backend_dispatches_report_review_tools(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertTrue(body, "找不到 _run_platform_tool 分派入口")
        for tool in NEW_TOOLS:
            self.assertIn(f'"{tool}"', body,
                          f"平台工具 {tool} 没有接进 _run_platform_tool")

    def test_read_tools_reuse_existing_report_and_version_sources(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertIn("store.load_process_report", body,
                      "读取报告必须复用既有报告数据源")
        self.assertIn("list_process_report_versions", body,
                      "读取版本必须复用既有版本数据源")

    def test_approve_and_reject_require_explicit_confirmation(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        for tool in GATED_TOOLS:
            branch = _branch(body, tool)
            self.assertTrue(branch, f"{tool} 未分派")
            self.assertIn("requires_confirmation", branch,
                          f"{tool} 必须返回 requires_confirmation 回执")
            self.assertIn("confirmed", branch,
                          f"{tool} 必须依据 confirmed 参数才执行")

    def test_review_note_does_not_change_status(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        branch = _branch(body, "SaveReportReviewNote")
        self.assertTrue(branch, "SaveReportReviewNote 未分派")
        self.assertNotIn("requires_confirmation", branch,
                         "审核意见只回填看板，不应要求确认门")
        self.assertNotIn("store.save_process_report(", branch,
                         "审核意见不得落盘改状态")

    def test_review_decision_reuses_single_implementation(self):
        for sentinel in SINGLE_IMPL_SENTINELS:
            hits = _backend_files_with(sentinel)
            self.assertEqual(len(hits), 1,
                             f"实现 {sentinel} 必须只存在于一个后端文件，实际：{hits}")
            self.assertNotIn("oc_agent.py", hits,
                             f"oc_agent.py 不得自带第二套审核判定（{sentinel}）")
        for snippet in FORBIDDEN_AGENT_SNIPPETS:
            self.assertNotIn(snippet, self.agent,
                             f"oc_agent.py 不得越权直写 / 重写 3.2 实现：{snippet}")

    def test_review_route_keeps_director_permission_and_audit(self):
        body = _py_def_body(self.main, "review_process_report")
        self.assertTrue(body, "既有审核路由 review_process_report 被删除")
        self.assertIn("DIRECTOR_ROLES", body,
                      "审核必须保留工艺技术总监权限校验")
        self.assertIn("store.audit(", body, "审核必须保留审计记录")

    def test_ui_action_mapping_for_report_review(self):
        match = re.search(r"UI_ACTION_TOOLS\s*=\s*\{([\s\S]*?)\n\}", self.agent)
        self.assertIsNotNone(match, "UI_ACTION_TOOLS 映射不存在")
        body = match.group(1)
        self.assertRegex(body, r'"SaveReportReviewNote"\s*:\s*"fill-report-review-note"',
                         "审核意见应映射为 fill-report-review-note")
        for tool in REFRESH_TOOLS:
            self.assertRegex(body, rf'"{tool}"\s*:\s*"refresh-report"',
                             f"UI 动作映射缺少 {tool} → refresh-report")
        for forbidden in FORBIDDEN_UI_ACTIONS:
            self.assertNotIn(f'"{forbidden}"', body,
                             f"审核不能接成自动执行的动作：{forbidden}")
        for action in KEPT_UI_ACTIONS:
            self.assertIn(f'"{action}"', body, f"既有 UI 动作 {action} 被改动")

    def test_existing_tools_and_routes_not_reduced(self):
        for tool in KEPT_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent, f"既有平台工具 {tool} 被删除")
        for route in KEPT_ROUTES:
            self.assertIn(f'"{route}"', self.main, f"既有审核路由 {route} 被删除")

    # ---------------------------------------------------------------- 看板
    def test_board_registers_report_review_actions(self):
        self.assertIn("registerActions", self.board,
                      "report-review-result.js 没有 registerActions 注册")
        for name in ("refreshProcessReport", "applyReportReviewNote"):
            self.assertRegex(self.board, rf"{name}\s*:",
                             f"看板没有注册 {name} 动作")
        for kept in ("approveProcessReport", "rejectProcessReport"):
            self.assertRegex(self.board, rf"{kept}\s*:", f"既有动作 {kept} 被删除")

    def test_board_reports_review_progress_through_runtime(self):
        self.assertIn("TechBoardRuntime", self.board,
                      "3.2 进度必须经 TechBoardRuntime 上报给父壳")
        self.assertRegex(
            self.board, r"[\"'](?:task-progress|task-completed|task-failed)[\"']",
            "看板没有上报审核任务的进度事件")

    # ---------------------------------------------------------------- 左侧
    def test_left_handles_report_review_ui_actions(self):
        pairs = (("fill-report-review-note", "applyReportReviewNote"),
                 ("refresh-report", "refreshProcessReport"))
        for action, board_action in pairs:
            self.assertRegex(self.chat, rf'["\']{re.escape(action)}["\']',
                             f"左侧没有处理 {action} UI 动作")
            self.assertRegex(
                self.chat, rf'executeAction\(\s*["\']{board_action}["\']',
                f"左侧没有经桥触发看板的 {board_action}")

    def test_review_results_refresh_board_not_chat_only(self):
        self.assertIn("process-report", self.board,
                      "看板必须仍是 process-report 接口的调用方")
        self.assertNotIn("/process-report", self.chat,
                         "左侧不得直接调用 /process-report，必须经看板桥")

    # ---------------------------------------------------------------- 边界
    def test_parent_shell_has_no_review_form_dom(self):
        for marker in ("rrPublish", "rrReject", "rrDistributionScope", "rrReviewBody"):
            self.assertNotIn(marker, self.parent_html,
                             f"3.2 表单必须留在右侧看板 iframe 内（父壳出现 {marker}）")


if __name__ == "__main__":
    unittest.main()
