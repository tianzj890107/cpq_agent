"""红测：零件清单里不再有「一键生成全部工艺推荐」按钮，但批量能力一个都不能少。

缺陷：`app.js` 的 `renderPartsBoardToolbar()` 往 2.1 零件清单（`.drawing-parts-column` /
`#secParts`）里再塞一颗 `#partsBulkBar` 蓝色批量按钮，和左侧会话操作栏里的
`runAllPartProcesses` 动作重复；用户要求零件清单里不要这颗按钮。

红线：删的只是看板里的重复按钮 —— 左侧唯一入口、批量实现、单零件入口、后端接口都不许动。
"""
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
APP = (FRONTEND / "app.js").read_text(encoding="utf-8")
CSS = (FRONTEND / "agent-chat.css").read_text(encoding="utf-8")
HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
WB_HTML = (FRONTEND / "tech-workbench.html").read_text(encoding="utf-8")
MAIN = (ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8")

LABEL = "一键生成全部工艺推荐"


class PartsListDropsBulkButtonContract(unittest.TestCase):
    def test_parts_board_toolbar_is_gone(self):
        for token in ("partsBulkBar", "renderPartsBoardToolbar", "partsBoardToolbarHost",
                      "board-parts-bulk", "board-parts-toolbar"):
            with self.subTest(token=token):
                self.assertNotIn(token, APP, f"零件清单里的批量按钮残留：{token}")

    def test_parts_toolbar_styles_are_gone(self):
        self.assertNotRegex(CSS, r"\.board-parts-toolbar\s*\{")
        self.assertNotRegex(CSS, r"\.board-parts-bulk\b")

    def test_static_pages_do_not_reintroduce_the_node(self):
        for name, html in (("index.html", HTML), ("tech-workbench.html", WB_HTML)):
            with self.subTest(page=name):
                self.assertNotIn("partsBulkBar", html)

    def test_left_toolbar_action_is_the_single_entry(self):
        self.assertRegex(
            APP,
            r'runAllPartProcesses\s*:\s*\{[\s\S]{0,400}label\s*:\s*["\']'
            + re.escape(LABEL) + r'["\']',
            "看板动作注册必须保留：左侧操作栏与 Agent 都靠它分派",
        )
        self.assertRegex(APP, r"runAllPartProcesses\s*:\s*\{[\s\S]{0,400}role\s*:")
        self.assertRegex(APP, r"runAllPartProcesses\s*:\s*\{[\s\S]{0,600}deferred\s*:\s*true")
        self.assertRegex(APP, r"runAllPartProcesses[\s\S]{0,900}getState\s*:\s*\(\)\s*=>")

    def test_label_only_lives_on_the_registered_action(self):
        # 文案只允许挂在动作注册的 label 上；不得再作为按钮 textContent / innerHTML 出现
        # （注释里提到这句文案是允许的，注释不构成第二个入口）。
        self.assertEqual(APP.count(f'label: "{LABEL}"'), 1)
        self.assertNotRegex(APP, r'textContent\s*=\s*["\']' + re.escape(LABEL))
        self.assertNotRegex(APP, r'innerHTML\s*=[^;\n]*' + re.escape(LABEL))

    def test_bulk_implementation_is_untouched(self):
        self.assertRegex(APP, r"function\s+startAllPartProcesses\s*\(")
        self.assertRegex(APP, r"/parts/\$\{[^}]+\}/process")
        self.assertRegex(APP, r"for\s*\([^)]*(?:part|target)[^)]*\)\s*\{[\s\S]{0,2000}await")
        for event in ("task-progress", "task-completed", "task-failed"):
            with self.subTest(event=event):
                self.assertIn(event, APP)

    def test_single_part_process_entry_is_untouched(self):
        self.assertRegex(APP, r"data\.partAnalysis\s*=\s*mode")
        self.assertRegex(APP, r'openPartAnalysis\(\s*part\s*,\s*["\']process["\']\s*\)')

    def test_backend_still_exposes_only_the_single_part_route(self):
        self.assertIn("/api/projects/{project_id}/parts/{part_id}/process", MAIN)
        self.assertNotRegex(MAIN, r'@app\.post\(["\'][^"\']*(?:process-all|process/bulk|bulk-process)')


if __name__ == "__main__":
    unittest.main()
