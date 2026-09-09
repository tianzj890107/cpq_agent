import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "tech_app/frontend/tech-workbench.html"
JS_PATH = ROOT / "tech_app/frontend/tech-workbench.js"
CSS_PATH = ROOT / "tech_app/frontend/tech-workbench.css"


class TechMajorFlowNavigationContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.js = JS_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.compact_css = re.sub(r"\s+", "", cls.css).lower()

    def test_all_nine_internal_stages_remain_registered(self):
        for stage in (
            "requirement-create", "requirement-confirm", "requirement-review",
            "drawing", "process", "cost", "summary", "report-review", "report-publish",
        ):
            self.assertRegex(self.js, rf"id:\s*['\"]{re.escape(stage)}['\"]")

    def test_visible_navigation_defines_exactly_five_major_steps(self):
        match = re.search(r"const\s+MAJOR_STEPS\s*=\s*\[([\s\S]*?)\n\s*\];", self.js)
        self.assertIsNotNone(match, "缺少五大流程 MAJOR_STEPS 定义")
        block = match.group(1)
        labels = re.findall(r"label:\s*['\"]([^'\"]+)['\"]", block)
        self.assertEqual(labels, [
            "创建工艺评估需求", "图纸解析", "工艺方案/组装整合", "成本测算", "输出工艺评估结果",
        ])
        numbers = re.findall(r"no:\s*['\"]([1-5])['\"]", block)
        self.assertEqual(numbers, ["1", "2", "3", "4", "5"])

    def test_hidden_stages_are_grouped_under_major_steps(self):
        compact_js = re.sub(r"\s+", "", self.js)
        expected_groups = (
            "stages:['requirement-create','requirement-confirm','requirement-review']",
            "stages:['drawing']",
            "stages:['process']",
            "stages:['cost']",
            "stages:['summary','report-review','report-publish']",
        )
        for group in expected_groups:
            self.assertIn(group, compact_js)
        self.assertRegex(self.js, r"MAJOR_STEPS\.find\([^)]*\.stages\.includes\(state\.stage\)")

    def test_top_bar_renders_major_steps_not_internal_stages(self):
        self.assertIn("MAJOR_STEPS.forEach", self.js)
        render = re.search(
            r"function\s+renderTop\s*\(\)\s*\{([\s\S]*?)\n\s*function\s+updateProjectLabel",
            self.js,
        )
        self.assertIsNotNone(render)
        self.assertNotIn("STAGES.forEach", render.group(1))
        self.assertIn("data-major-step", render.group(1))
        self.assertNotIn("tech-phase-title", render.group(1))
        self.assertNotIn("tech-phase-arrow", render.group(1))

    def test_major_step_clicks_use_entry_stages(self):
        compact_js = re.sub(r"\s+", "", self.js)
        for entry in ("requirement-create", "drawing", "process", "cost", "summary"):
            self.assertIn(f"entry:'{entry}'", compact_js)
        self.assertRegex(self.js, r"applyStage\([^,]*(?:major|step)[^,]*\.entry")

    def test_redundant_current_phase_label_is_removed(self):
        self.assertNotIn('id="techPhaseLabel"', self.html)
        self.assertNotIn("techPhaseLabel", self.js)
        self.assertNotIn("当前：", self.js)

    def test_flow_label_is_immediately_followed_by_runtime_model(self):
        self.assertRegex(
            self.html,
            r"<span>技术工艺流程</span>\s*<span\s+id=['\"]techModelInfo['\"]",
        )
        combined = self.js + (ROOT / "tech_app/frontend/agent-chat.js").read_text(encoding="utf-8")
        self.assertIn("techModelInfo", combined)
        self.assertRegex(combined, r"data\.model|agent_model|text_model")
        self.assertNotRegex(self.html, r'id="techModelInfo"[^>]*>\s*·\s*[^<]+')

    def test_next_button_matches_quote_resting_and_hover_states(self):
        self.assertRegex(
            self.compact_css,
            r"#technext\{[^}]*background:var\(--gradient-primary-soft\)[^}]*"
            r"color:var\(--twb-primary\)[^}]*border:1pxsolidvar\(--color-primary-border\)",
        )
        self.assertRegex(
            self.compact_css,
            r"#technext:hover:not\(:disabled\)\{[^}]*background:var\(--gradient-primary-hover\)"
            r"[^}]*color:#fff[^}]*border-color:var\(--twb-primary-active\)",
        )
        self.assertRegex(self.compact_css, r"button:disabled\{[^}]*cursor:not-allowed")


if __name__ == "__main__":
    unittest.main()
