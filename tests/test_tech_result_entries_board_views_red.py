"""第 5 步红测：父会话栏入口 → 右侧看板视图，以及看板侧的六个视图。

对齐现状实现（第 4 步已把入口收敛到 dispatchDrawingCapability → TechBoardBridge.navigateView）：
  · 入口名就是看板视图名：parts / questions / report / evidence / review / files；
  · 本文件只断言“入口确实把视图名交给看板”与“看板确实注册了这六个视图”，
    不涉及第 6 步的零件详情层级，也不涉及 Agent 工具接线。
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
ENTRY_VIEWS = ("parts", "questions", "report", "evidence", "review", "files")


class TechResultEntriesBoardViewsRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = (FRONTEND / "agent-chat.js").read_text(
            encoding="utf-8", errors="replace").replace("\x00", "")
        cls.html = (FRONTEND / "tech-workbench.html").read_text(encoding="utf-8")
        cls.board = (FRONTEND / "app.js").read_text(encoding="utf-8", errors="replace")

    def test_left_entries_forward_view_names_to_board(self):
        # 三颗结果按钮：控件 id → 视图名，成对登记。
        for node_id, view in (("ocPartsAction", "parts"), ("ocQuestionsAction", "questions"),
                              ("ocReportAction", "report")):
            self.assertRegex(
                self.chat,
                rf'["\']{node_id}["\']\s*,\s*["\']{view}["\']',
                f"{node_id} 未与视图 {view} 成对登记")
        # 任务文件入口。
        self.assertRegex(self.chat, r'dispatchDrawingCapability\(\s*["\']files["\']')
        # 契约更新（2.1「能力入口归位更多功能」批次）：左侧会话栏只保留「解析视图」这一个
        # 视图入口；导入已有 3D 模型 / 版本与校核审查 / 联网核验 / 校验修正四项已归位到 2.1
        # 页内的「更多功能 ▾」菜单（仍复用同一批看板视图与业务动作，能力一个不少）。
        self.assertRegex(self.html, r'data-tech-capability="evidence"',
                         "左侧会话栏缺少「解析视图」入口")
        for moved in ("import3d", "review", "modelLookup", "verify"):
            with self.subTest(capability=moved):
                self.assertNotRegex(
                    self.html, rf'data-tech-capability="{moved}"',
                    f"{moved} 已归位 2.1「更多功能 ▾」，不得留在左侧会话栏")
                self.assertIn(moved, self.board,
                              f"{moved} 的看板视图 / 业务动作不得被删除")

    def test_dispatch_goes_through_board_bridge_navigate_view(self):
        match = re.search(
            r"function\s+dispatchDrawingCapability\s*\([\s\S]{0,900}?\n  \}", self.chat)
        self.assertIsNotNone(match, "缺少 dispatchDrawingCapability")
        body = match.group(0)
        self.assertIn("boardBridge()", body)
        self.assertIn("navigateView", body)

    def test_board_registers_all_six_views_with_real_runners(self):
        match = re.search(r"registerViews\(\s*\{([\s\S]*?)\n\s*\}\)", self.board)
        self.assertIsNotNone(match, "2.1 看板必须通过 TechBoardRuntime.registerViews 注册内部视图")
        body = match.group(1)
        for view in ENTRY_VIEWS:
            self.assertRegex(
                body, rf"(?:^|[\s{{,])['\"]?{view}['\"]?\s*:", f"看板未注册视图 {view}")
        self.assertGreaterEqual(
            len(re.findall(r"\brun\s*:", body)), len(ENTRY_VIEWS),
            "每个视图都必须带真实的 run 实现")

    def test_board_views_reuse_existing_sections_instead_of_new_copy(self):
        anchors = ("secParts", "secQuestions", "secEvidence", "secVersions")
        hit = [name for name in anchors if name in self.board]
        self.assertGreaterEqual(
            len(hit), 4,
            f"看板视图必须复用既有面板（app.js 只引用了 {hit}）")


if __name__ == "__main__":
    unittest.main()
