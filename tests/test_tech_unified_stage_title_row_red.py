from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


class TechUnifiedStageTitleRowRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (FRONTEND / "tech-workbench.html").read_text(encoding="utf-8")
        cls.js = (FRONTEND / "tech-workbench.js").read_text(encoding="utf-8")
        cls.css = (FRONTEND / "tech-workbench.css").read_text(encoding="utf-8")
        cls.embed = (FRONTEND / "tech-embed.js").read_text(encoding="utf-8")

    def test_all_five_major_steps_define_fixed_titles(self):
        expected = {"1": "工艺评估需求", "2": "图纸解析", "3": "组装与整合", "4": "成本测算", "5": "工艺评估报告"}
        for no, title in expected.items():
            self.assertRegex(self.js, rf"(?:no:\s*['\"]{no}['\"][^\n]{{0,220}}title:\s*['\"]{title}['\"]|['\"]{no}['\"]\s*:\s*['\"]{title}['\"])")

    def test_context_title_header_is_visible_for_every_major_step(self):
        self.assertIn('id="techContextHeader"', self.html)
        self.assertIn('id="techContextTitle"', self.html)
        self.assertRegex(self.js, r'techContextHeader[\s\S]{0,1200}(?:hidden\s*=\s*false|removeAttribute\(["\']hidden)')
        self.assertNotRegex(self.js, r'header\.hidden\s*=\s*!substeps\.length')

    def test_title_row_has_right_aligned_tabs_slot(self):
        self.assertRegex(self.html, r'id="techContextTitle"[\s\S]{0,400}id="techSubstepsBar"')
        context = re.search(r'\.tech-workspace-context\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(context)
        self.assertIn("display: flex", context.group(1))
        self.assertRegex(context.group(1), r'justify-content:\s*space-between')
        self.assertRegex(self.css, r'\.tech-substeps-(?:slot|bar)\s*\{[^}]*(?:margin-left:\s*auto|justify-content:\s*flex-end)')

    def test_process_and_cost_tabs_are_exposed_in_unified_header(self):
        for stage in ("process", "cost"):
            self.assertRegex(self.js, rf"['\"]{stage}['\"][\s\S]{{0,1200}}(?:proxy|data-child-tab|querySelector)")
        for label in ("整合图纸", "参数推荐", "组装工艺"):
            self.assertIn(label, self.js)

    def test_embedded_pages_hide_duplicate_title_and_tab_rows(self):
        self.assertRegex(self.embed, r'(?:title-section|title-row)')
        self.assertRegex(self.embed, r'(?:ai-tabs|inline-analysis-tabs)')
        self.assertRegex(self.embed, r'(?:display\s*:\s*none|hidden)')

    def test_substep_active_style_remains_light(self):
        active = re.search(r'\.tech-substep-btn\.active\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(active)
        self.assertNotRegex(active.group(1), r'background:[^;}]*(?:gradient-primary|#0067D1)')
        self.assertNotRegex(active.group(1), r'color:\s*#fff')


if __name__ == "__main__":
    unittest.main()
