import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WB_HTML = (ROOT / "tech_app/frontend/tech-workbench.html").read_text(encoding="utf-8")
WB_CSS = (ROOT / "tech_app/frontend/tech-workbench.css").read_text(encoding="utf-8")
WB_JS = (ROOT / "tech_app/frontend/tech-workbench.js").read_text(encoding="utf-8")
DRAWING_JS = (ROOT / "tech_app/frontend/app.js").read_text(encoding="utf-8")
COST_HTML = (ROOT / "tech_app/frontend/cost-review.html").read_text(encoding="utf-8")
COST_JS = (ROOT / "tech_app/frontend/cost-review.js").read_text(encoding="utf-8")
BACKEND = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")


class TechPrimaryQuickActionsAndBulkAnalysisContract(unittest.TestCase):
    def test_chat_primary_quick_action_uses_quote_blue_primary_style(self):
        self.assertRegex(WB_CSS, r'\.tech-chat-actions\s*>\s*button\.primary\s*\{[^}]*background\s*:\s*var\(--gradient-(?:primary|ai)\)[^}]*color\s*:\s*(?:white|#fff)', re.S)
        self.assertRegex(WB_CSS, r'button\.primary:hover:not\(:disabled\)')
        self.assertRegex(WB_CSS, r'button\.primary:focus-visible')
        self.assertRegex(WB_JS, r'classList\.(?:toggle|add)\(["\']primary["\']')

    def test_drawing_chat_has_primary_bulk_process_action(self):
        # 契约更新（「右侧看板按钮统一到左侧会话操作栏」批次）：批量入口不再由父壳写死，
        # 父壳只按看板动作快照渲染；2.1 唯一主按钮是「开始解析」，批量入口是描边动作。
        self.assertIn("runAllPartProcesses", DRAWING_JS)
        self.assertIn("一键生成全部工艺推荐", DRAWING_JS + WB_JS)
        self.assertRegex(DRAWING_JS, r"runAllPartProcesses\s*:\s*\{[\s\S]{0,600}role\s*:")
        self.assertRegex(WB_JS, r"data-tech-action", "批量入口必须由看板快照动态渲染到左侧操作栏")

    def test_parts_board_registers_deferred_bulk_process_action(self):
        self.assertRegex(
            DRAWING_JS,
            r'runAllPartProcesses\s*:\s*\{[\s\S]{0,800}label\s*:\s*["\']一键生成全部工艺推荐["\'][\s\S]{0,800}deferred\s*:\s*true',
        )
        self.assertRegex(DRAWING_JS, r'runAllPartProcesses[\s\S]{0,2500}return\s*\{\s*ok\s*:\s*true')

    def test_bulk_process_reuses_single_part_route_and_controlled_serial_iteration(self):
        self.assertRegex(DRAWING_JS, r'async\s+function\s+\w*(?:All|Bulk)\w*Process')
        self.assertRegex(DRAWING_JS, r'for\s*\([^)]*(?:part|target)[^)]*\)\s*\{[\s\S]{0,2000}await')
        self.assertRegex(DRAWING_JS, r'/parts/\$\{[^}]+\}/process')
        self.assertNotRegex(BACKEND, r'@app\.post\(["\'][^"\']*(?:process-all|process/bulk|bulk-process)')

    def test_bulk_process_skips_successes_tracks_partial_failures_and_refreshes(self):
        self.assertRegex(DRAWING_JS, r'(?:missing|has_process|existing|skip)', re.I)
        self.assertRegex(DRAWING_JS, r'(?:failed|failures)[\s\S]{0,1800}(?:continue|for\s*\()')
        for token in ("task-progress", "task-completed", "task-failed"):
            with self.subTest(event=token):
                self.assertIn(token, DRAWING_JS)
        self.assertRegex(DRAWING_JS, r'(?:refreshData|requestBoardSummary|renderTree|loadProject)\s*\(')

    def test_parts_board_exposes_bulk_button_without_removing_single_part_action(self):
        self.assertIn("一键生成全部工艺推荐", DRAWING_JS)
        self.assertIn("工艺推荐", DRAWING_JS)
        self.assertRegex(DRAWING_JS, r'data\.partAnalysis\s*=\s*mode')

    def test_cost_bulk_action_uses_one_click_all_copy_and_existing_pipeline(self):
        # 契约更新（「右侧看板业务按钮统一到左侧会话操作栏」批次）：父壳不再写死按钮文案，
        # 文案来自看板注册的动作 label（cost-review.js），左侧只按动作快照渲染。
        self.assertIn("一键测算全部成本", COST_HTML)
        self.assertIn("一键测算全部成本", COST_JS)
        self.assertIn("data-tech-action", WB_JS,
                      "批量入口必须由看板快照动态渲染到左侧操作栏")
        self.assertRegex(COST_JS, r'runCostReview\s*:\s*\{[\s\S]{0,500}deferred\s*:\s*true')
        self.assertRegex(COST_JS, r'async\s+function\s+crRunAll\s*\(')
        self.assertRegex(COST_JS, r'crRunAll[\s\S]{0,900}crRunParts\(false\)[\s\S]{0,900}crRunAssembly\(')

    def test_primary_slot_stays_blue_and_navigation_stays_outline(self):
        self.assertRegex(WB_HTML, r'id="techChatPrimary"[^>]*')
        self.assertRegex(WB_JS, r'setChatButton\([^)]*techChatPrimary[\s\S]{0,500}(?:primary|variant)')
        self.assertNotRegex(WB_HTML, r'id="techChatPrev"[^>]*class="[^"]*primary')
        self.assertNotRegex(WB_HTML, r'id="techChatTransfer"[^>]*class="[^"]*primary')


if __name__ == "__main__":
    unittest.main()
