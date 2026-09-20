from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


class TechAssemblyTabSelectedStateRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (FRONTEND / "assembly-integration.html").read_text(encoding="utf-8")
        cls.css = (FRONTEND / "assembly-integration.css").read_text(encoding="utf-8")
        cls.js = (FRONTEND / "assembly-integration.js").read_text(encoding="utf-8")

    def test_three_expected_tabs_remain(self):
        tabs = re.findall(r'<button[^>]+data-ai-tab="([^"]+)"[^>]*>([^<]+)</button>', self.html)
        self.assertEqual(tabs, [
            ("drawings", "整合图纸"),
            ("params", "参数推荐"),
            ("process", "组装工艺"),
        ])

    def test_active_tab_is_white_with_blue_text_and_border(self):
        rule = re.search(r'\.ai-tabs\s+button\.active\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(rule)
        body = rule.group(1)
        self.assertRegex(body, r'background:\s*(?:#fff(?:fff)?|white|var\([^,]+,\s*#fff(?:fff)?\))')
        self.assertRegex(body, r'color:[^;}]*(?:oc-accent|color-primary|#0067D1)')
        self.assertRegex(body, r'border(?:-color)?:[^;}]*(?:oc-accent|color-primary|primary-border|#0067D1|#B8D7F4)')
        self.assertNotRegex(body, r'background:[^;}]*(?:oc-accent|#0067D1)')
        self.assertNotRegex(body, r'color:\s*#fff')

    def test_active_hover_does_not_turn_into_dark_fill(self):
        self.assertRegex(
            self.css,
            r'\.ai-tabs\s+button\.active:hover[^\{]*\{[^}]*(?:background:\s*(?:#fff(?:fff)?|white)|color:[^;}]*(?:oc-accent|color-primary))',
        )

    def test_tabs_have_keyboard_focus_feedback(self):
        self.assertRegex(self.css, r'\.ai-tabs\s+button:focus-visible[^\{]*\{[^}]*(?:outline|box-shadow)')

    def test_existing_active_switching_contract_remains(self):
        self.assertRegex(
            self.js,
            r"classList\.toggle\(['\"]active['\"],\s*button\.dataset\.aiTab\s*===\s*aiTab\)",
        )


if __name__ == "__main__":
    unittest.main()

