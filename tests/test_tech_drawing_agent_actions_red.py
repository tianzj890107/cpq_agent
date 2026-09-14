"""第 7 步红测：2.1 图纸解析的 Agent 动作接通。

覆盖三件事：
1. 看板（app.js）把 2.1 的业务动作注册进 TechBoardRuntime，并与页面上既有按钮共用同一份实现；
2. 左侧会话（agent-chat.js）在统一父壳里只经 TechBoardBridge 触发这些动作，不直接调业务接口；
3. 后端平台工具与 UI 动作名一个都不能少（本轮不改后端）。

不联网、不起服务、不读真实业务数据；不涉及第 6 步的零件视图层级。
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
AGENT_SERVICE = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"

BOARD_ACTIONS = ("parseDrawing", "modelLookup", "verify", "searchComponents", "refreshData")
BOARD_NAMED_IMPL = ("runModelLookup", "runVerification", "runComponentMatch")
LEFT_BOARD_ACTIONS = ("parseDrawing", "modelLookup", "verify", "searchComponents")
PLATFORM_TOOLS = (
    "GetProjectState", "ListParts", "GetPartDetail", "GetOpenQuestions",
    "LookupComponentLibrary", "LookupProcessLibrary", "LookupCostLibrary",
    "UpdatePartParameters", "RequestParse",
)
UI_ACTIONS = ("parse", "refresh-ir")


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


class TechDrawingAgentActionsRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = _read(FRONTEND / "app.js")
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.parent_html = _read(FRONTEND / "tech-workbench.html")
        cls.agent_service = _read(AGENT_SERVICE)

    # ---------------------------------------------------------------- helpers
    def _action_blocks(self):
        blocks = re.findall(r"registerActions\(\s*\{([\s\S]*?)\n\s*\}\)", self.board)
        self.assertTrue(blocks, "app.js 没有 TechBoardRuntime.registerActions 注册块")
        return "\n".join(blocks)

    def _function_body(self, text, name):
        match = re.search(rf"function\s+{name}\s*\([\s\S]*?\n\s*\}}", text)
        self.assertIsNotNone(match, f"{name}() 不存在")
        return match.group(0)

    # ---------------------------------------------------------------- 看板动作
    def test_board_registers_drawing_business_actions(self):
        block = self._action_blocks()
        for action in BOARD_ACTIONS:
            self.assertRegex(
                block, rf"(?:^|[\s{{,])['\"]?{re.escape(action)}['\"]?\s*:",
                f"看板未注册 2.1 业务动作 {action}")
        self.assertGreaterEqual(
            len(re.findall(r"\brun\s*:", block)), len(BOARD_ACTIONS),
            "每个 2.1 业务动作都必须带真实 run 实现")

    def test_board_actions_reuse_existing_named_implementations(self):
        for name in BOARD_NAMED_IMPL:
            self.assertIn(f"function {name}(", self.board,
                          f"app.js 应把既有实现抽成具名函数 {name}()")
        self.assertRegex(
            self.board, r'btnModelLookup"\)\.onclick\s*=\s*runModelLookup',
            "型号核验按钮必须与注册动作共用 runModelLookup()")
        self.assertRegex(
            self.board, r'btnVerify"\)\.onclick\s*=\s*runVerification',
            "校验修正按钮必须与注册动作共用 runVerification()")
        block = self._action_blocks()
        for name in BOARD_NAMED_IMPL:
            self.assertIn(name, block,
                          f"注册动作没有复用 {name}()，看起来另写了一份实现")

    # ---------------------------------------------------------------- 左侧接通
    def test_left_maps_agent_events_to_board_actions(self):
        for action in LEFT_BOARD_ACTIONS:
            self.assertRegex(
                self.chat, rf'executeAction\(\s*["\']{re.escape(action)}["\']',
                f"左侧没有把 Agent 事件接到看板动作 {action}")

    def test_plus_menu_routes_actions_and_views_separately(self):
        # 契约更新（2.1「能力入口归位更多功能」批次）：联网核验 / 校验修正在统一工作台里从
        # 左侧会话栏移到 2.1 页内的「更多功能 ▾」菜单（节点 id 不变，左侧不再重复一份），
        # 业务动作分派与视图分派仍然分开。
        drawing_html = _read(FRONTEND / "index.html")
        for node_id, label in (("btnModelLookup", "联网核验"), ("btnVerify", "校验修正")):
            with self.subTest(node=node_id):
                self.assertRegex(drawing_html, rf'id="{node_id}"[^>]*>{label}',
                                 f"2.1「更多功能 ▾」缺少 {label}")
                self.assertNotRegex(self.parent_html, rf'id="{node_id}"',
                                    f"{label} 不得再占左侧会话栏一颗按钮")
        # 动作名只能发给 execute-action；视图名继续发给 navigate-view。
        self.assertRegex(
            self.chat, r'(?:executeAction|DRAWING_ACTION)[\s\S]{0,400}modelLookup',
            "联网核验/校验修正没有走 executeAction 分派")
        self.assertRegex(
            self.chat, r'navigateView\(',
            "视图入口仍必须走 navigateView")

    def test_part_parameter_edits_refresh_board_through_bridge(self):
        body = self._function_body(self.chat, "flushPartEdits")
        self.assertIn("inUnifiedWorkbench", body,
                      "零件参数改完的刷新必须区分统一父壳与独立页")
        self.assertRegex(
            body, r"(refreshData|executeAction)\s*\(",
            "统一父壳里必须经桥刷新看板，不能只发同窗口事件")

    def test_unified_mode_does_not_own_business_data_endpoints(self):
        for name in ("renderComponentMatch", "rematchButton"):
            body = self._function_body(self.chat, name)
            self.assertIn(
                "inUnifiedWorkbench", body,
                f"{name}() 必须在统一父壳里走看板动作，而不是父壳自己拉业务数据")
        for endpoint in ("/model-lookup", "/verify"):
            self.assertNotIn(
                endpoint, self.chat,
                f"左侧会话不得直接调用业务接口 {endpoint}")

    # ---------------------------------------------------------------- 后端不改
    def test_agent_platform_tools_and_ui_actions_unchanged(self):
        for tool in PLATFORM_TOOLS:
            self.assertIn(f'"name": "{tool}"', self.agent_service,
                          f"平台工具 {tool} 被删除或改名")
        match = re.search(r"UI_ACTION_TOOLS\s*=\s*\{([\s\S]*?)\n\}", self.agent_service)
        self.assertIsNotNone(match, "UI_ACTION_TOOLS 映射不存在")
        for action in UI_ACTIONS:
            self.assertIn(f'"{action}"', match.group(1),
                          f"UI 动作 {action} 被删除或改名")

    # ---------------------------------------------------------------- 边界
    def test_parent_shell_has_no_new_business_dom(self):
        for node_id in ("modelLookupDetails", "verificationDetails", "componentMatch",
                        "partDetail", "analysisPanel", "tree"):
            self.assertNotRegex(
                self.parent_html, rf'id=["\']{re.escape(node_id)}["\']',
                f"父壳出现业务 DOM #{node_id} —— 业务内容必须留在右侧看板内")


if __name__ == "__main__":
    unittest.main()
