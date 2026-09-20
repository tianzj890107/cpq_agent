from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TECH = ROOT / "tech_app" / "frontend"


class QuoteTechHeaderModelAndNamesRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quote = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")
        cls.tech_html = (TECH / "tech-workbench.html").read_text(encoding="utf-8")
        cls.tech_js = (TECH / "tech-workbench.js").read_text(encoding="utf-8")
        cls.agent_js = (TECH / "agent-chat.js").read_text(encoding="utf-8", errors="replace")
        cls.tech_css = (TECH / "tech-workbench.css").read_text(encoding="utf-8")

    def test_tech_workbench_renders_shared_settings_panel_css(self):
        self.assertRegex(self.tech_html, r'<link[^>]+href="llm-settings-panel\.css(?:\?[^" ]*)?"')
        self.assertRegex(self.tech_html, r'<script[^>]+src="llm-settings-panel\.js(?:\?[^" ]*)?"')

    def test_both_header_model_labels_are_real_buttons(self):
        for source, node_id in ((self.quote, "modelInfo"), (self.tech_html, "techModelInfo")):
            tag = re.search(rf'<button[^>]+id="{node_id}"[^>]*>', source)
            self.assertIsNotNone(tag)
            self.assertIn('type="button"', tag.group(0))
            self.assertIn("aria-label", tag.group(0))

    def test_quote_header_model_opens_existing_settings_modal(self):
        self.assertRegex(self.quote, r'<button[^>]+id="modelInfo"[^>]+onclick="openSettings\(\)"')
        self.assertIn("function openSettings()", self.quote)
        self.assertIn("settingsModal", self.quote)

    def test_tech_header_and_nav_share_one_settings_opener(self):
        self.assertIn("techSettings", self.tech_js)
        self.assertIn("techModelInfo", self.tech_js)
        self.assertRegex(self.tech_js, r'(?:openTechModelSettings|openModelSettings)[\s\S]+Ll[mM]SettingsPanel\.mount\(')
        self.assertGreaterEqual(len(re.findall(r'addEventListener\([\'\"]click[\'\"]', self.tech_js)), 2)

    def test_agent_failure_does_not_replace_configured_model_with_status_text(self):
        unavailable = re.search(r'if \(data\.available === false\) \{([\s\S]*?)\n\s*\}', self.agent_js)
        self.assertIsNotNone(unavailable)
        self.assertNotRegex(unavailable.group(1), r'techShellModel\([^)]*(?:未连接|未就绪|Agent)')
        self.assertNotRegex(unavailable.group(1), r'setModelLabel\([^)]*(?:未连接|未就绪|Agent)')
        self.assertRegex(self.agent_js, r'/api/llm/settings|LlmSettingsPanel\.load\(')

    def test_tech_model_trigger_has_feedback_and_focus_visible(self):
        self.assertRegex(self.tech_css, r'#techModelInfo[^\{]*\{[^}]*cursor:\s*pointer')
        self.assertRegex(self.tech_css, r'#techModelInfo:(?:focus-visible|hover)[^\{]*\{')

    def test_project_name_reads_nested_meta_and_requirement_title(self):
        self.assertRegex(self.tech_js, r'(?:data|projectData)\.meta\.(?:project_name|name)')
        self.assertIn("/requirement", self.tech_js)
        self.assertRegex(self.tech_js, r'requirement(?:Data)?(?:\?\.)?\.requirement(?:\?\.)?\.title|requirement\.title')

    def test_task_id_is_resolved_to_business_label(self):
        self.assertRegex(self.tech_js, r'/wf/task\?task_id=|URLSearchParams[^\n]*task_id')
        for field in ("task.title", "task.source_label", "task.task_kind_label", "task.task_no"):
            self.assertIn(field, self.tech_js)
        self.assertNotIn("` · 任务 ${state.taskId}`", self.tech_js)
        self.assertNotIn("`项目 ${project}${suffix}`", self.tech_js)


if __name__ == "__main__":
    unittest.main()

