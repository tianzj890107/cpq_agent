import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def compact(text):
    return re.sub(r"\s+", "", text).lower()


class PrimaryButtonBlueGradientContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quote = compact((ROOT / "确认需求解析结果.html").read_text(encoding="utf-8"))
        cls.tech = compact((ROOT / "tech_app/frontend/tech-workbench.css").read_text(encoding="utf-8"))

    def assert_gradient_tokens(self, css, source):
        expected = (
            "--gradient-primary:linear-gradient(135deg,#0067d10%,#0057b8100%)",
            "--gradient-primary-hover:linear-gradient(135deg,#0057b80%,#004a9f100%)",
            "--gradient-primary-soft:linear-gradient(135deg,#ffffff0%,#f4f9fe100%)",
        )
        for token in expected:
            self.assertIn(token, css, f"{source} 缺少同色系渐变 token：{token}")

    def test_quote_and_tech_define_the_same_blue_gradient_scale(self):
        self.assert_gradient_tokens(self.quote, "确认需求解析结果.html")
        self.assert_gradient_tokens(self.tech, "tech-workbench.css")

    def test_quote_outlined_actions_use_soft_gradient_at_rest(self):
        self.assertRegex(
            self.quote,
            r"#qafillstep,#btnnext\{[^}]*background:var\(--gradient-primary-soft\)[^}]*"
            r"color:var\(--color-primary\)[^}]*border:1pxsolidvar\(--color-primary-border\)",
        )

    def test_quote_outlined_actions_use_deep_gradient_on_enabled_hover(self):
        self.assertRegex(
            self.quote,
            r"#qafillstep:hover:not\(:disabled\),#btnnext:hover:not\(:disabled\)\{[^}]*"
            r"background:var\(--gradient-primary-hover\)[^}]*color:white[^}]*"
            r"border-color:var\(--color-primary-active\)",
        )

    def test_tech_primary_actions_use_normal_and_hover_gradients(self):
        self.assertRegex(
            self.tech,
            r"\.tech-wb-btn\.primary\{[^}]*background:var\(--gradient-primary\)[^}]*color:#fff",
        )
        self.assertRegex(
            self.tech,
            r"\.tech-wb-btn\.primary:hover:not\(:disabled\)\{[^}]*"
            r"background:var\(--gradient-primary-hover\)",
        )
        self.assertRegex(
            self.tech,
            r"#technext\{[^}]*background:var\(--gradient-primary-soft\)[^}]*color:var\(--twb-primary\)",
        )
        self.assertRegex(
            self.tech,
            r"#technext:hover:not\(:disabled\)\{[^}]*background:var\(--gradient-primary-hover\)",
        )

    def test_primary_brand_color_stays_0067d1(self):
        self.assertIn("--color-primary:#0067d1", self.quote)
        self.assertIn("--twb-primary:#0067d1", self.tech)


if __name__ == "__main__":
    unittest.main()
