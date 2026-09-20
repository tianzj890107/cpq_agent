"""第 17 步红测：统一结构化 UI 事件 tech_ui（Agent 表达意图，看板固定渲染）。

覆盖：
1. tech_ui 平台工具 + 固定八项 action 枚举 + 无自由 HTML / 字段结构；
2. 服务端校验九阶段白名单与视图白名单，越界不入队；
3. 线程本地队列 + {"type": "tech_ui"} SSE 帧下发；
4. UI_ACTION_TOOLS 不映射 tech_ui；
5. 前端固定 TECH_UI_ACTIONS 映射、handleEvent 处理 tech_ui；
6. focus_view / refresh_view / select_part 经看板桥，结果入口与进度复用既有宿主；
7. set_stage 只接受九阶段白名单；request_confirmation 必须用户点击才执行；
8. 不做模型字符串 innerHTML 注入；既有工具与路由不减少。

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

TECH_UI_ACTIONS = (
    "focus_view", "refresh_view", "fill_fields", "select_part",
    "show_result_actions", "show_progress", "set_stage", "request_confirmation",
)
NINE_STAGES = (
    "requirement-create", "requirement-confirm", "requirement-review",
    "drawing", "process", "cost", "summary", "report-review", "report-publish",
)
FORBIDDEN_SCHEMA_KEYS = ('"html"', '"raw"', '"script"', '"columns"')
KEPT_TOOLS = (
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "UpdatePartParameters", "RequestParse", "GetIntegrationState",
    "RequestIntegrationStep", "ExtractRequirement", "ConfirmRequirement",
    "ApproveRequirementReview",
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


def _schema_block(text):
    match = re.search(r'"name":\s*"tech_ui",[\s\S]*?input_schema[\s\S]*?\n    \}', text)
    if match:
        return match.group(0)
    idx = text.find('"name": "tech_ui"')
    return text[idx:idx + 3000] if idx >= 0 else ""


class TechUiProtocolRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _read(MAIN)
        cls.agent = _read(AGENT_SERVICE)
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.workbench = _read(FRONTEND / "tech-workbench.js")

    # ---------------------------------------------------------------- 后端 schema
    def test_backend_declares_tech_ui_tool(self):
        self.assertIn('"name": "tech_ui"', self.agent,
                      "oc_agent.py 没有声明 tech_ui 平台工具")

    def test_tech_ui_action_enum_is_fixed_and_complete(self):
        block = _schema_block(self.agent)
        self.assertTrue(block, "找不到 tech_ui 的 schema 定义")
        for action in TECH_UI_ACTIONS:
            self.assertIn(f'"{action}"', block,
                          f"tech_ui action 枚举缺少 {action}")

    def test_tech_ui_schema_has_no_freeform_html_or_fields(self):
        block = _schema_block(self.agent)
        self.assertTrue(block, "找不到 tech_ui 的 schema 定义")
        for key in FORBIDDEN_SCHEMA_KEYS:
            self.assertNotIn(key, block,
                             f"tech_ui 不得接受自由结构：{key}")

    def test_tech_ui_handler_exists_and_is_dispatched(self):
        self.assertIn("def _handle_tech_ui(", self.agent,
                      "缺少 _handle_tech_ui 校验 / 入队入口")
        body = _py_def_body(self.agent, "_run_platform_tool")
        self.assertIn("_handle_tech_ui", body,
                      "tech_ui 没有接进 _run_platform_tool 分派")

    def test_stage_and_view_whitelists_exist(self):
        stages = re.search(r"TECH_UI_STAGES\s*=\s*[\(\[\{]([\s\S]*?)[\)\]\}]", self.agent)
        self.assertIsNotNone(stages, "缺少九阶段白名单 TECH_UI_STAGES")
        for stage in NINE_STAGES:
            self.assertIn(stage, stages.group(1),
                          f"TECH_UI_STAGES 缺少 {stage}")
        self.assertRegex(self.agent, r"TECH_UI_VIEWS\s*=",
                         "缺少视图白名单 TECH_UI_VIEWS")

    def test_handler_rejects_and_does_not_render_html(self):
        body = _py_def_body(self.agent, "_handle_tech_ui")
        self.assertTrue(body, "找不到 _handle_tech_ui")
        self.assertNotIn("innerHTML", body,
                         "tech_ui 服务端不得拼接 HTML")
        for tag in ("<div", "<span", "<table", "<script"):
            self.assertNotIn(tag, body, f"tech_ui 服务端不得生成 HTML：{tag}")
        self.assertRegex(body, r"(TECH_UI_ACTIONS|unknown|未知|不在白名单)",
                         "越界 action 必须被拒绝并返回可读错误")

    def test_tech_ui_events_are_queued_and_streamed(self):
        self.assertRegex(self.agent, r"_tech_ui_events\(",
                         "tech_ui 事件必须先入线程本地队列")
        self.assertRegex(self.agent, r'"type":\s*"tech_ui"',
                         "tech_ui 事件必须以 tech_ui SSE 帧下发")

    def test_ui_action_tools_does_not_map_tech_ui(self):
        match = re.search(r"UI_ACTION_TOOLS\s*=\s*\{([\s\S]*?)\n\}", self.agent)
        self.assertIsNotNone(match, "UI_ACTION_TOOLS 映射不存在")
        self.assertNotIn('"tech_ui"', match.group(1),
                         "tech_ui 不得走 ui_action 自动分派通道")

    # ---------------------------------------------------------------- 前端
    def test_frontend_has_fixed_action_map(self):
        block = _read(FRONTEND / "agent-chat.js")
        self.assertRegex(block, r"TECH_UI_ACTIONS\s*=",
                         "agent-chat.js 缺少固定 TECH_UI_ACTIONS 映射")
        idx = block.find("TECH_UI_ACTIONS")
        window = block[idx:idx + 2500]
        for action in TECH_UI_ACTIONS:
            self.assertIn(action, window, f"前端映射缺少 {action}")

    def test_frontend_handles_tech_ui_events(self):
        self.assertRegex(self.chat, r'tech_ui',
                         "handleEvent 没有处理 tech_ui 事件")

    def test_board_driven_actions_go_through_bridge(self):
        self.assertIn("TechBoardBridge", self.chat,
                      "focus_view / refresh_view / select_part 必须经看板桥")
        self.assertRegex(self.chat, r"navigateView\(",
                         "focus_view 必须经 TechBoardBridge.navigateView")
        self.assertRegex(self.chat, r"executeAction\(",
                         "refresh_view / select_part 必须经 TechBoardBridge.executeAction")

    def test_result_and_progress_reuse_existing_hosts(self):
        # 契约更新（2.1 结果入口迁到左侧操作栏批次）：会话结果条退役，结果入口复用左侧操作栏的两颗按钮。
        for host in ("ocQuestionsAction", "ocReportAction", "ocTaskProgressHost"):
            self.assertIn(host, self.chat, f"必须复用既有宿主 {host}")
        self.assertIn("renderTaskProgress", self.chat,
                      "show_progress 必须复用既有任务进度渲染")

    def test_set_stage_is_whitelisted_and_routed_to_shell(self):
        self.assertRegex(self.chat, r"set-stage",
                         "set_stage 必须走父壳既有切步通道")
        self.assertIn("set-stage", self.workbench,
                      "父壳没有接收 set_stage 的切步处理")

    def test_request_confirmation_is_user_gated(self):
        self.assertIn("request_confirmation", self.chat,
                      "缺少 request_confirmation 处理")
        self.assertRegex(self.chat, r"(确认|confirm)[A-Za-z]*",
                         "request_confirmation 必须渲染固定确认交互")
        self.assertNotRegex(
            self.chat,
            r"request_confirmation[\s\S]{0,200}executeAction\([^)]*\)\s*;?\s*//\s*auto",
            "request_confirmation 不得自动执行")

    def test_no_model_html_injection(self):
        idx = self.chat.find("TECH_UI_ACTIONS")
        self.assertGreaterEqual(idx, 0, "agent-chat.js 缺少 TECH_UI_ACTIONS 映射")
        window = self.chat[idx:idx + 3000]
        self.assertNotIn("innerHTML", window,
                         "tech_ui 渲染不得把模型字符串当 HTML 注入")

    # ---------------------------------------------------------------- 边界
    def test_existing_tools_and_routes_not_reduced(self):
        for tool in KEPT_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent, f"既有平台工具 {tool} 被删除")
        for route in ("/api/projects/{project_id}/summary",
                      "/api/projects/{project_id}/process-report"):
            self.assertIn(f'"{route}"', self.main, f"既有路由 {route} 被删除")


if __name__ == "__main__":
    unittest.main()
