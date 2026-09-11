"""第 12 步红测：2.3 成本测算的 Agent 动作接通。

覆盖：
1. 新增 10 个 2.3 平台工具（读取状态 / 零件成本 / 逐件测算 / 组装测算 / 汇总 /
   编辑说明 / 确认成本 / 写入物料 / 返回工艺 / 发送报价）并复用既有 cost-review 实现；
2. 只读工具复用 services.cost_review，oc_agent 不自带第二套成本算法；
3. 确认成本与三个对外动作必须有显式确认门；
4. 对外动作需要 SSO token：agent_send → stream_sse → _SSO_TOKEN 注入链完整；
5. UI 动作映射新增 cost-step / refresh-cost-review，对外动作不接成自动执行；
6. 2.3 看板注册 refresh / step / 三个去向动作并经 TechBoardRuntime 上报进度；
7. 成本数字与调用都落在右侧看板，左侧不直接调 /cost-review/*；
8. 既有平台工具与 cost-review 路由不减少。

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
    "GetCostReviewState", "ListCostReviewParts", "RunCostReviewPart",
    "RunCostReviewAssembly", "RunCostReviewAll", "UpdateCostReviewNote",
    "ConfirmCostReview", "WriteCostReviewMaterial", "ReturnCostReviewToProcess",
    "SendCostReviewToQuote",
)
RUN_TOOLS = ("RunCostReviewPart", "RunCostReviewAssembly", "RunCostReviewAll")
GATED_TOOLS = ("ConfirmCostReview", "WriteCostReviewMaterial",
               "ReturnCostReviewToProcess", "SendCostReviewToQuote")
REFRESH_TOOLS = ("UpdateCostReviewNote",) + GATED_TOOLS
NEW_UI_ACTIONS = {tool: "cost-step" for tool in RUN_TOOLS}
NEW_UI_ACTIONS.update({tool: "refresh-cost-review" for tool in REFRESH_TOOLS})
FORBIDDEN_UI_ACTIONS = ("send-cost-to-quote", "write-cost-material",
                        "return-cost-to-process", "cost-review-op")
KEPT_UI_ACTIONS = ("parse", "refresh-ir", "refresh-integration", "integration-step",
                   "extract-requirement", "refresh-requirement", "fill-review-note")
# 既有平台工具：一个都不能少。
KEPT_TOOLS = (
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "LookupComponentLibrary", "LookupProcessLibrary", "LookupCostLibrary",
    "UpdatePartParameters", "RequestParse", "GetIntegrationState",
    "ListIntegrationParams", "UpdateIntegrationParams", "UpdateIntegrationProcess",
    "RequestIntegrationStep", "GetRequirementDraft", "ExtractRequirement",
    "SubmitRequirementConfirmation", "GetRequirementPrecheck",
    "ConfirmRequirement", "ApproveRequirementReview", "RejectRequirementReview",
)
KEPT_ROUTES = (
    "/api/projects/{project_id}/cost-review",
    "/api/projects/{project_id}/cost-review/parts/{part_id}",
    "/api/projects/{project_id}/cost-review/assembly",
    "/api/projects/{project_id}/cost-review/confirm",
    "/api/projects/{project_id}/cost-review/material-write",
    "/api/projects/{project_id}/cost-review/send-to-quote",
    "/api/projects/{project_id}/cost-review/return-to-process",
)
# 状态流转 / 审计实现唯一性哨兵：各只存在于一个后端文件，且不是 oc_agent.py。
SINGLE_IMPL_SENTINELS = ("cost_review_confirm", "cost_review_material_write",
                         "cost_review_send_to_quote")
# oc_agent.py 里绝不允许出现的“第二套实现 / 越权直写”。
FORBIDDEN_AGENT_SNIPPETS = (
    "review.confirmed = True", "CostAnalysis(", "cost_model.",
    "cpq_bridge.write_material(", "cpq_bridge.send_to_quote(",
    "cpq_bridge.return_to_process(",
    "cost_review.run_part(", "cost_review.run_assembly(",
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
    """取 _run_platform_tool 里某个工具 if 分支的正文。"""
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


class TechCostReviewAgentRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _read(MAIN)
        cls.agent = _read(AGENT_SERVICE)
        cls.board = _read(FRONTEND / "cost-review.js")
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.parent_html = _read(FRONTEND / "tech-workbench.html")

    # ---------------------------------------------------------------- 后端工具
    def test_backend_declares_cost_review_action_tools(self):
        for tool in NEW_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent,
                          f"2.3 平台工具 {tool} 没有声明")

    def test_backend_dispatches_cost_review_action_tools(self):
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertTrue(body, "找不到 _run_platform_tool 分派入口")
        for tool in NEW_TOOLS:
            self.assertIn(f'"{tool}"', body,
                          f"平台工具 {tool} 没有接进 _run_platform_tool")

    def test_read_tools_reuse_existing_cost_review_service(self):
        """只读必须有真数据来源：复用 services.cost_review，不自造一套。"""
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertRegex(body, r"cost_review\.(load_review|summarize|payload)",
                         "只读工具必须复用 services.cost_review")
        for snippet in ("cost_model.", "CostAnalysis("):
            self.assertNotIn(snippet, self.agent,
                             f"oc_agent.py 不得自带第二套成本算法：{snippet}")

    def test_run_tools_request_board_execution_without_recomputing(self):
        """测算类工具只发请求：Agent 不自己跑 run_part / run_assembly。"""
        body = _py_def_body(self.agent, "_run_platform_tool")
        for tool in RUN_TOOLS:
            branch = _branch(body, tool)
            self.assertTrue(branch, f"{tool} 未分派")
            self.assertRegex(branch, r'"requested"\s*:\s*True',
                             f"{tool} 必须返回 requested 回执，由看板执行")
        for snippet in ("cost_review.run_part(", "cost_review.run_assembly("):
            self.assertNotIn(snippet, self.agent,
                             f"测算必须交给看板既有接口，oc_agent 不得直接算：{snippet}")

    def test_gated_tools_require_explicit_confirmation(self):
        """确认成本与三个对外动作：未 confirmed 只回执，绝不触发写入或外呼。"""
        body = _py_def_body(self.agent, "_run_platform_tool")
        for tool in GATED_TOOLS:
            branch = _branch(body, tool)
            self.assertTrue(branch, f"{tool} 未分派")
            self.assertIn("requires_confirmation", branch,
                          f"{tool} 必须返回 requires_confirmation 回执")
            self.assertIn("confirmed", branch,
                          f"{tool} 必须依据 confirmed 参数才执行")

    def test_outward_tools_use_shared_implementation_not_agent_rewrite(self):
        for sentinel in SINGLE_IMPL_SENTINELS:
            hits = _backend_files_with(sentinel)
            self.assertEqual(len(hits), 1,
                             f"实现 {sentinel} 必须只存在于一个后端文件，实际：{hits}")
            self.assertNotIn("oc_agent.py", hits,
                             f"oc_agent.py 不得自带第二套实现（{sentinel}）")
        for snippet in FORBIDDEN_AGENT_SNIPPETS:
            self.assertNotIn(snippet, self.agent,
                             f"oc_agent.py 不得越权直写 / 重写 2.3 实现：{snippet}")

    def test_agent_send_plumbs_sso_token_for_outward_tools(self):
        """对外动作要带用户 token：agent_send → stream_sse → _SSO_TOKEN。"""
        send_body = _py_def_body(self.main, "agent_send")
        self.assertIn("_sso_token(request)", send_body,
                      "agent_send 必须把 _sso_token(request) 传给 Agent")
        self.assertRegex(send_body, r"stream_sse\([\s\S]{0,400}?token=",
                         "agent_send 调 stream_sse 时必须带上 token")
        stream_body = _py_def_body(self.agent, "stream_sse")
        self.assertRegex(stream_body, r"token",
                         "stream_sse 必须接收 token 参数")
        self.assertIn("current_token", self.agent,
                      "对外工具必须能取到本轮对话的 SSO token（current_token）")
        self.assertTrue(
            re.search(r'contextvars\.ContextVar\(\s*["\'][^"\']*token', self.agent, re.I),
            "oc_agent.py 必须用上下文变量（如 _SSO_TOKEN）把 token 带进工具线程")

    def test_ui_action_mapping_for_cost_review(self):
        match = re.search(r"UI_ACTION_TOOLS\s*=\s*\{([\s\S]*?)\n\}", self.agent)
        self.assertIsNotNone(match, "UI_ACTION_TOOLS 映射不存在")
        body = match.group(1)
        for tool, action in NEW_UI_ACTIONS.items():
            self.assertRegex(
                body, rf'"{tool}"\s*:\s*"{re.escape(action)}"',
                f"UI 动作映射缺少 {tool} → {action}")
        for forbidden in FORBIDDEN_UI_ACTIONS:
            self.assertNotIn(f'"{forbidden}"', body,
                             f"对外动作不能接成自动执行的动作：{forbidden}")
        for action in KEPT_UI_ACTIONS:
            self.assertIn(f'"{action}"', body, f"既有 UI 动作 {action} 被改动")

    def test_existing_tools_and_routes_not_reduced(self):
        for tool in KEPT_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent, f"既有平台工具 {tool} 被删除")
        for route in KEPT_ROUTES:
            self.assertIn(f'"{route}"', self.main, f"既有 cost-review 路由 {route} 被删除")

    # ---------------------------------------------------------------- 看板
    def test_board_registers_cost_review_actions(self):
        blocks = re.findall(r"registerActions\(\s*\{([\s\S]*?)\n\s*\}\)", self.board)
        self.assertTrue(blocks, "cost-review.js 没有 registerActions 注册块")
        block = "\n".join(blocks)
        for name in ("refreshCostReview", "costStep", "writeCostReviewMaterial",
                     "sendCostReviewToQuote", "returnCostReviewToProcess"):
            self.assertRegex(block, rf"{name}\s*:",
                             f"看板没有注册 {name} 动作")
        for kept in ("runCostReview", "confirmCostReview"):
            self.assertRegex(block, rf"{kept}\s*:", f"既有动作 {kept} 被删除")
        for view in ("parts", "assembly", "total", "params"):
            self.assertRegex(self.board, rf"{view}\s*:\s*\{{\s*run\s*:",
                             f"既有视图 {view} 被删除")

    def test_board_reports_cost_progress_through_runtime(self):
        self.assertIn("TechBoardRuntime", self.board,
                      "2.3 进度必须经 TechBoardRuntime 上报给父壳")
        self.assertRegex(
            self.board, r"[\"'](?:task-progress|task-completed|task-failed)[\"']",
            "看板没有上报成本任务的进度事件")

    # ---------------------------------------------------------------- 左侧
    def test_left_handles_cost_review_ui_actions(self):
        pairs = (("cost-step", "costStep"),
                 ("refresh-cost-review", "refreshCostReview"))
        for action, board_action in pairs:
            self.assertRegex(
                self.chat, rf'["\']{re.escape(action)}["\']',
                f"左侧没有处理 {action} UI 动作")
            self.assertRegex(
                self.chat, rf'executeAction\(\s*["\']{board_action}["\']',
                f"左侧没有经桥触发看板的 {board_action}")

    def test_cost_results_refresh_board_not_chat_only(self):
        """成本必须落到右侧看板：左侧只发动作，不自己调接口。"""
        self.assertIn("crUrl(", self.board, "看板必须仍是 cost-review 接口的调用方")
        self.assertNotIn("/cost-review", self.chat,
                         "左侧不得直接调用 /cost-review/*，必须经看板桥")

    # ---------------------------------------------------------------- 边界
    def test_parent_shell_has_no_cost_form_dom(self):
        for marker in ("crBody", "crRunAll", "crConfirm", "crPanelTitle"):
            self.assertNotIn(marker, self.parent_html,
                             f"2.3 表单必须留在右侧看板 iframe 内（父壳出现 {marker}）")


if __name__ == "__main__":
    unittest.main()
