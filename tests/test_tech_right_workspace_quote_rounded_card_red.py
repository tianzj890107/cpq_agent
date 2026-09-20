"""技术右侧工作区结果卡契约。

契约更新（「报价 / 工艺工作区去卡片 + 工艺标题行收窄」批次）：
原「报价式圆角卡片」几何（margin:16px / border:.5px / border-radius:12px）已被用户最新决策
覆盖为「铺满、无外边距、无边框、无圆角」，见
docs/specs/tech-quote-workspace-flush-and-compact-stage-title-row.md。
本文件保留同层包装、外层非卡片、分隔线、iframe 满高与桥接等结构断言。"""
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
        # 契约反转（「报价 / 工艺工作区去卡片」批次，Spec
        # docs/specs/tech-quote-workspace-flush-and-compact-stage-title-row.md）：
        # 用户明确要求这层圆角卡片不再包住内容 —— 外边距 / 边框 / 圆角全部归零，
        # 结果区铺满右侧工作区；表面色与 flex 排版保持不变。精确高度与内外间距
        # 由 tests/test_tech_quote_workspace_flush_red.py 逐条钉住。
        card = rule(".tech-results-area")
        for declaration in (
            "margin:0", "border:0", "border-radius:0", "overflow:hidden",
            "display:flex", "flex-direction:column", "flex:1", "min-height:0",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, card)
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

    def test_responsive_rules_do_not_bring_the_margin_back(self):
        # 同上契约反转：窄屏也不许把外边距加回来（原来是 12px / 8px）。
        compact = re.sub(r"\s+", "", CSS)
        offenders = re.findall(
            r'@media[^{}]+\{[\s\S]*?\.tech-results-area\{[^{}]*margin:([1-9][0-9]*)px',
            compact,
        )
        self.assertEqual([], offenders, "响应式里又把结果卡外边距加回来了：%s" % offenders)

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
