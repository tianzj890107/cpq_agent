import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "tech_app/frontend/tech-workbench.html").read_text(encoding="utf-8")
CSS = (ROOT / "tech_app/frontend/tech-workbench.css").read_text(encoding="utf-8")
JS = (ROOT / "tech_app/frontend/tech-workbench.js").read_text(encoding="utf-8")


def rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", CSS, re.S)
    if not match:
        raise AssertionError(f"缺少 CSS 规则：{selector}")
    return re.sub(r"\s+", "", match.group(1))


class TechRightWorkspaceQuoteRoundedCardContract(unittest.TestCase):
    def test_single_results_card_wraps_context_outlet_and_footer_in_order(self):
        card = re.search(r'<(?:section|div)[^>]+id="techResultsArea"[^>]*>', HTML)
        self.assertIsNotNone(card)
        opening = card.group(0)
        self.assertRegex(opening, r'class="[^"]*tech-results-area')
        positions = [HTML.find(token, card.end()) for token in ("techContextHeader", "techWorkspaceOutlet", "tech-workbench-bottom")]
        self.assertTrue(all(position >= 0 for position in positions), positions)
        self.assertEqual(positions, sorted(positions))

    def test_results_card_matches_quote_geometry_and_surface(self):
        card = rule(".tech-results-area")
        for declaration in (
            "margin:16px", "border-radius:12px", "overflow:hidden",
            "display:flex", "flex-direction:column", "flex:1", "min-height:0",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, card)
        self.assertRegex(card, r"border:\.?5pxsolidvar\(--(?:twb-border|border-color)\)")
        self.assertRegex(card, r"background:var\(--(?:twb-card|bg-page)\)")
        self.assertNotRegex(card, r"box-shadow:(?!none)")

    def test_outer_workspace_column_does_not_become_a_second_card(self):
        outer = rule(".tech-workspace-pane")
        self.assertIn("border-radius:0", outer)
        self.assertIn("box-shadow:none", outer)
        self.assertNotRegex(outer, r"margin:(?!0(?:;|$))")

    def test_context_and_bottom_bar_are_card_internal_separators(self):
        context = rule(".tech-workspace-context")
        footer = rule(".tech-workbench-bottom")
        self.assertRegex(context, r"border-bottom:\.?5pxsolidvar\(--(?:twb-border|border-color)\)")
        self.assertRegex(footer, r"border-top:\.?5pxsolidvar\(--(?:twb-border|border-color)\)")
        self.assertNotRegex(context + footer, r"box-shadow:(?!none)")

    def test_workspace_outlet_and_stage_frame_fill_card_without_own_rounding(self):
        outlet = rule("#techWorkspaceOutlet")
        self.assertIn("flex:1", outlet)
        self.assertIn("min-height:0", outlet)
        frame = rule(".tech-stage-frame")
        self.assertIn("width:100%", frame)
        self.assertIn("height:100%", frame)
        self.assertRegex(frame, r"border:0(?:;|$)")
        self.assertNotRegex(frame, r"border-radius:(?!0)")

    def test_responsive_rules_keep_spacing_instead_of_returning_flush(self):
        compact = re.sub(r"\s+", "", CSS)
        responsive_margins = re.findall(
            r'@media[^{}]+\{[\s\S]*?\.tech-results-area\{[^{}]*margin:([0-9]+)px',
            compact,
        )
        self.assertTrue(responsive_margins, "缺少技术结果卡的响应式外间距")
        self.assertTrue(all(int(value) > 0 for value in responsive_margins), responsive_margins)

    def test_existing_stage_and_bridge_contract_is_preserved(self):
        for token in ("techWorkspaceOutlet", "techContextHeader", "techPrev", "techNext"):
            with self.subTest(token=token):
                self.assertIn(token, HTML)
        # 契约更新（「业务按钮统一到左侧会话操作栏」批次）：底栏代理 syncActionBar() 退役，
        # 左侧操作栏改由 syncChatActions() 按看板动作快照渲染。
        for token in ("mountStageFrame", "TechBoardBridge", "applyStage", "syncChatActions"):
            with self.subTest(token=token):
                self.assertIn(token, JS)


if __name__ == "__main__":
    unittest.main()
