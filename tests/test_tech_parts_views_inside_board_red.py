"""第 6 步红测：2.1 看板内部的零件层级视图状态机。

视图链：drawing-overview → parts-list → part-detail → part-process / part-cost。
本文件只断言“视图状态机建在看板内部、返回只切视图、父壳不承载零件详情”，
不涉及第 7 步的 Agent 工具接线，也不新增后端契约。
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"

PART_VIEWS = ("drawing-overview", "parts-list", "part-detail", "part-process", "part-cost")
STEP5_VIEWS = ("parts", "questions", "report", "evidence", "review", "files", "upload", "import3d")
# 父壳绝不允许出现这些业务 DOM（第 4 步已断言过一轮，这里是第 6 步的回归锁）。
PARENT_FORBIDDEN_IDS = (
    "partDetail", "viewer", "modelPanes", "analysisPanel", "analysisHost",
    "parameterEditor", "secParts", "tree", "secEvidence", "secQuestions",
    "boardViewHost", "boardViewBody",
)


def _read(name):
    return (FRONTEND / name).read_text(encoding="utf-8", errors="replace").replace("\x00", "")


class TechPartsViewsInsideBoardRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = _read("app.js")
        cls.parent_html = _read("tech-workbench.html")
        cls.parent_js = _read("tech-workbench.js")
        cls.parent_combined = cls.parent_html + "\n" + cls.parent_js

    # ---------------------------------------------------------------- helpers
    def _register_views_block(self):
        match = re.search(r"registerViews\(\s*\{([\s\S]*?)\n\s*\}\)", self.board)
        self.assertIsNotNone(match, "2.1 看板没有 TechBoardRuntime.registerViews 注册块")
        return match.group(1)

    def _assert_in(self, haystack, needle, message):
        # 不用 assertIn：它会把整个 app.js 倒进失败信息里，红测报告读不了。
        self.assertTrue(needle in haystack, message)

    def _function_body(self, name):
        match = re.search(rf"function\s+{name}\s*\([\s\S]*?\n\}}", self.board)
        self.assertIsNotNone(match, f"app.js 缺少 {name}()")
        return match.group(0)

    # ---------------------------------------------------------------- 注册
    def test_board_registers_part_flow_views_with_real_runners(self):
        block = self._register_views_block()
        for view in PART_VIEWS:
            self.assertRegex(
                block, rf"(?:^|[\s{{,])['\"]?{re.escape(view)}['\"]?\s*:",
                f"看板未注册零件层级视图 {view}")
        self.assertGreaterEqual(
            len(re.findall(r"\brun\s*:", block)), len(PART_VIEWS),
            "每个零件层级视图都必须带真实的 run 实现")
        for view in STEP5_VIEWS:
            self.assertRegex(
                block, rf"(?:^|[\s{{,])['\"]?{re.escape(view)}['\"]?\s*:",
                f"第 5 步的入口视图 {view} 被这轮改动顶掉了")

    # ---------------------------------------------------------------- 状态机
    def test_part_view_state_machine_is_exposed_and_reports_active_view(self):
        self._assert_in(self.board, "TechBoardPartViews",
                        "看板必须暴露 window.TechBoardPartViews 内部视图状态机")
        for method in ("show", "back", "current"):
            self.assertRegex(
                self.board, rf"(?:window\.)?TechBoardPartViews[\s\S]{{0,600}}?\b{method}\b",
                f"TechBoardPartViews 缺少 {method}()")
        self._assert_in(self.board, "TechBoardRuntime.setView(",
                        "每次切换视图都要用 TechBoardRuntime.setView 上报当前视图")
        self.assertRegex(
            self.board, r"PART_VIEW_PARENT[\s\S]{0,400}part-detail",
            "缺少父/子视图映射 PART_VIEW_PARENT")

    # ---------------------------------------------------------------- 进入详情
    def test_selecting_a_part_enters_part_detail_inside_board(self):
        select_body = self._function_body("selectPart")
        self._assert_in(select_body, "part-detail",
                        "selectPart 只切了右侧面板，没有进入 part-detail 视图")
        detail = self._function_body("showPartDetail")
        self._assert_in(detail, "selectPart(",
                        "part-detail 必须复用既有 selectPart 渲染，不能另写一份零件详情")
        self._assert_in(detail, "loadVersions(",
                        "零件详情必须带出该零件的版本（复用既有 /versions）")

    # ---------------------------------------------------------------- 工艺 / 成本
    def test_process_and_cost_subactions_enter_part_views(self):
        self.assertRegex(
            self.board, r"PART_VIEW_FOR[\s\S]{0,300}part-process[\s\S]{0,300}part-cost"
                        r"|PART_VIEW_FOR[\s\S]{0,300}part-cost[\s\S]{0,300}part-process",
            "缺少零件子动作 → 看板视图映射 PART_VIEW_FOR")
        body = self._function_body("openPartAnalysis")
        self._assert_in(body, "PART_VIEW_FOR",
                        "工艺推荐 / 成本测算仍只切 setRightPane，没有切到 part-process / part-cost")
        self.assertRegex(body, r"TechBoardPartViews[\s\S]{0,120}\.show\(",
                         "openPartAnalysis 必须走看板内部视图状态机，不跳过它")

    # ---------------------------------------------------------------- 返回
    def test_back_controls_only_change_board_view(self):
        for label in ("返回零件清单", "返回零件详情"):
            self._assert_in(self.board, label, f"看板内部缺少「{label}」返回控件")
        self.assertRegex(
            self.board, r"PART_VIEW_PARENT[\s\S]{0,400}parts-list",
            "返回必须按看板内部父子视图回到上一层")
        for forbidden in ("window.parent.postMessage", "TechBoardBridge.navigateView",
                          "parent.postMessage("):
            self.assertFalse(
                forbidden in self.board,
                f"看板内部视图切换不得走父壳通道（发现 {forbidden}）")

    # ---------------------------------------------------------------- 父壳回归
    def test_parent_shell_still_hosts_no_part_views_or_modals(self):
        for node_id in PARENT_FORBIDDEN_IDS:
            self.assertNotRegex(
                self.parent_html, rf'id=["\']{re.escape(node_id)}["\']',
                f"父壳出现了业务详情 DOM #{node_id}，业务详情必须留在右侧看板内")
        for view in ("part-detail", "part-process", "part-cost", "parts-list", "drawing-overview"):
            self.assertFalse(
                view in self.parent_combined,
                f"父壳持有看板内部视图名 {view} —— 零件层级必须由看板自己维护")
        self.assertFalse("contentDocument" in self.parent_combined,
                         "父壳不得再触及 iframe 内部 DOM")


if __name__ == "__main__":
    unittest.main()
