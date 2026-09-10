from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TECH = ROOT / "tech_app"


class UnifiedModelSettingsAndApiKeysRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quote = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")
        cls.tech_html = (TECH / "frontend" / "tech-workbench.html").read_text(encoding="utf-8")
        cls.tech_js = (TECH / "frontend" / "tech-workbench.js").read_text(encoding="utf-8")
        cls.panel_js = (TECH / "frontend" / "llm-settings-panel.js").read_text(encoding="utf-8")
        cls.tech_settings = (TECH / "backend" / "services" / "llm_settings.py").read_text(encoding="utf-8")

    def test_shared_panel_uses_quote_settings_endpoint_only(self):
        self.assertIn('fetch("/api/settings"', self.panel_js)
        self.assertNotIn("/api/llm/settings", self.panel_js)

    def test_quote_modal_has_no_separate_tech_settings_tab_or_form(self):
        for legacy in (
            'id="setTabTech"', 'id="techVisionBox"', 'id="setTechKey"',
            "switchSettingsTab", "saveTechLlm", "/api/llm/settings",
        ):
            self.assertNotIn(legacy, self.quote)

    def test_tech_workbench_uses_centered_modal_not_anchor_popover(self):
        self.assertRegex(self.tech_html, r'id="techModelSettingsMask"[^>]+role="(?:presentation|dialog)"')
        self.assertRegex(self.tech_html, r'id="techModelSettings"[^>]+role="dialog"')
        self.assertRegex(self.tech_js, r'openTechModelSettings[\s\S]+techModelSettingsMask')
        opener = re.search(r'function openTechModelSettings\([^)]*\)\s*\{([\s\S]*?)\n\s*\}', self.tech_js)
        self.assertIsNotNone(opener)
        self.assertNotIn("getBoundingClientRect", opener.group(1))
        self.assertNotRegex(opener.group(1), r'\.style\.(?:left|top)\s*=')

    def test_tech_frontend_has_no_independent_settings_api(self):
        combined = self.tech_html + "\n" + self.tech_js + "\n" + self.panel_js
        self.assertNotIn("/api/llm/settings", combined)
        self.assertIn("/api/settings", combined)

    def test_tech_backend_does_not_persist_a_second_settings_file(self):
        self.assertNotRegex(self.tech_settings, r'_PATH\s*=.*llm_settings\.json')
        self.assertNotRegex(self.tech_settings, r'data\["keys"\]|_state\["keys"\]')
        self.assertRegex(self.tech_settings, r'(?:cpq_settings|shared_settings|quote_settings)')

    def test_tech_uses_one_quote_model_without_vision_text_split(self):
        self.assertNotIn("vision_model", self.panel_js)
        self.assertNotIn("text_model", self.panel_js)
        self.assertNotRegex(self.tech_settings, r'_state\["vision_model"\]|_state\["text_model"\]')
        self.assertRegex(self.tech_settings, r'(?:current|selected|resolve).*model')

    def test_api_key_is_submitted_only_to_global_quote_settings(self):
        self.assertRegex(self.panel_js, r'fetch\("/api/settings"[\s\S]+api_key')
        self.assertRegex(self.panel_js, r'api_key_provider')
        self.assertRegex(self.panel_js, r'input\.value\s*=\s*""')


if __name__ == "__main__":
    unittest.main()
