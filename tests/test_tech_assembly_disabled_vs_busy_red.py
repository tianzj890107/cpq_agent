from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


class TechAssemblyDisabledVsBusyRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (FRONTEND / "assembly-integration.css").read_text(encoding="utf-8")
        cls.js = (FRONTEND / "assembly-integration.js").read_text(encoding="utf-8")

    def test_generic_disabled_action_is_not_allowed_not_waiting(self):
        rule = re.search(r'\.ai-actions\s+\.inline-action:disabled\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(rule)
        self.assertRegex(rule.group(1), r'cursor:\s*not-allowed')
        self.assertNotRegex(rule.group(1), r'cursor:\s*wait')

    def test_wait_cursor_requires_explicit_busy_state(self):
        self.assertRegex(
            self.css,
            r'\.ai-actions\s+\.inline-action(?:\[aria-busy=["\']?true["\']?\]|\.is-busy|\[data-busy=["\']?true["\']?\])[^\{]*\{[^}]*cursor:\s*wait',
        )

    def test_generate_button_exposes_real_busy_semantics(self):
        generate = re.search(r'<button[^>]*id="aiGenerate"[^>]*>', self.js)
        self.assertIsNotNone(generate)
        self.assertRegex(generate.group(0), r'aria-busy=')
        self.assertIn("aiBusy", generate.group(0))
        self.assertRegex(self.js, r'aiBusy\s*\?[^:]*parse-spinner')

    def test_missing_result_confirm_buttons_stay_plain_disabled(self):
        params = re.search(r'<button[^>]*id="aiParamsConfirm"[^>]*>', self.js)
        process = re.search(r'<button[^>]*id="aiProcessConfirm"[^>]*>', self.js)
        self.assertIsNotNone(params)
        self.assertIsNotNone(process)
        self.assertIn("!has || aiBusy", params.group(0))
        self.assertIn("!has || aiBusy", process.group(0))
        self.assertNotIn("aria-busy", params.group(0))
        self.assertNotIn("aria-busy", process.group(0))

    def test_finance_disabled_reference_style_remains_not_allowed(self):
        self.assertRegex(self.css, r'\.ai-op-btn:disabled\s*\{[^}]*cursor:\s*not-allowed')


if __name__ == "__main__":
    unittest.main()

