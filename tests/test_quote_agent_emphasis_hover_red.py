import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "确认需求解析结果.html"


class QuoteAgentEmphasisHoverContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = PAGE.read_text(encoding="utf-8")

    def css_bodies(self, selector_fragment):
        bodies = []
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", self.page):
            if selector_fragment in selectors:
                bodies.append(re.sub(r"\s+", "", body).lower())
        self.assertTrue(bodies, f"缺少目标样式选择器：{selector_fragment}")
        return bodies

    def assert_has_declarations(self, selector, declarations):
        bodies = self.css_bodies(selector)
        for declaration in declarations:
            self.assertTrue(
                any(declaration.lower() in body for body in bodies),
                f"{selector} 缺少声明 {declaration}",
            )

    def test_four_targets_keep_existing_identity_and_behavior_hooks(self):
        for token in (
            '<span class="ai-badge">AI</span>',
            'class="step-node ${state}',
            'id="qaFillStep" onclick="fillStepRecommend()"',
            'id="btnNext" onclick="confirmStep()"',
            'id="btnNextText">确认，进入下一步',
        ):
            self.assertIn(token, self.page)

    def test_four_targets_are_outlined_in_their_resting_state(self):
        for selector in (".ai-badge", ".step-node.active"):
            self.assert_has_declarations(selector, (
                "background:var(--color-primary-page)",
                "color:var(--color-primary)",
                "border:1pxsolidvar(--color-primary-border)",
            ))
        for selector in ("#qaFillStep", "#btnNext"):
            self.assert_has_declarations(selector, (
                "background:var(--gradient-primary-soft)",
                "color:var(--color-primary)",
                "border:1pxsolidvar(--color-primary-border)",
            ))

    def test_four_targets_use_deep_blue_and_white_only_on_hover(self):
        for selector in (".ai-badge:hover", ".step-node.active:hover"):
            self.assert_has_declarations(selector, (
                "background:var(--color-primary-active)",
                "color:white",
                "border-color:var(--color-primary-active)",
            ))
        for selector in ("#qaFillStep:hover:not(:disabled)", "#btnNext:hover:not(:disabled)"):
            self.assert_has_declarations(selector, (
                "background:var(--gradient-primary-hover)",
                "color:white",
                "border-color:var(--color-primary-active)",
            ))

    def test_target_buttons_retain_disabled_protection(self):
        self.assertRegex(self.page, r"\.quick-action-btn:disabled\s*\{[^}]*cursor:\s*not-allowed")
        self.assertRegex(self.page, r"\.bottom-bar\s+\.btn:disabled\s*\{[^}]*cursor:\s*not-allowed")


if __name__ == "__main__":
    unittest.main()
