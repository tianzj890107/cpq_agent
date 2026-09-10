from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


class TechFullWidthBoardAndSingleAgentPaneRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell_html = (FRONTEND / "tech-workbench.html").read_text(encoding="utf-8")
        cls.shell_js = (FRONTEND / "tech-workbench.js").read_text(encoding="utf-8")
        cls.embed_js = (FRONTEND / "tech-embed.js").read_text(encoding="utf-8")
        cls.frontend_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in FRONTEND.glob("*")
            if path.suffix in {".html", ".js", ".css"}
        )

    def test_child_panel_toggle_is_removed(self):
        self.assertNotIn('id="techPanelToggle"', self.shell_html)
        self.assertNotIn("techPanelToggle", self.shell_js)
        self.assertNotIn("子页面板", self.shell_html + self.shell_js)

    def test_embedded_child_agent_pane_can_never_be_reopened(self):
        self.assertFalse(
            "show-child-chat" in self.frontend_text,
            "前端仍包含允许重新展开子页面会话栏的 show-child-chat 状态",
        )
        self.assertRegex(
            self.embed_js,
            r"\.tech-embed\s+\.oc-agent-pane\s*\{[^}]*display\s*:\s*none\s*!important",
        )

    def test_embedded_board_layout_is_explicitly_single_column_and_full_width(self):
        for selector in (".oc-shell .page-container", ".oc-work", ".oc-work .center-panel"):
            rule = re.search(
                re.escape(".tech-embed " + selector) + r"\s*\{([^}]*)\}",
                self.embed_js,
            )
            self.assertIsNotNone(rule, f"嵌入模式缺少 {selector} 的布局覆盖")
            self.assertRegex(rule.group(1), r"width\s*:\s*100%")

        work_rule = re.search(r"\.tech-embed \.oc-work\s*\{([^}]*)\}", self.embed_js)
        self.assertRegex(
            work_rule.group(1),
            r"(?:display\s*:\s*block|grid-template-columns\s*:\s*(?:minmax\(0,\s*1fr\)|1fr))",
        )
        container_rule = re.search(
            r"\.tech-embed \.oc-shell \.page-container\s*\{([^}]*)\}", self.embed_js
        )
        self.assertIn("max-width:none", re.sub(r"\s+", "", container_rule.group(1)))

    def test_parent_agent_has_stage_context_for_only_major_flows_two_three_four(self):
        mapping = re.search(
            r"(?:const|let|var)\s+STAGE_AGENT_CONTEXT\s*=\s*\{([\s\S]*?)\n\s*\};",
            self.shell_js,
        )
        self.assertIsNotNone(mapping, "应显式定义父 Agent 栏的阶段上下文映射")
        body = mapping.group(1)
        for stage in ("drawing", "process", "cost"):
            self.assertRegex(body, rf"(?:['\"]{stage}['\"]|\b{stage}\b)\s*:")
        for stage in ("requirement-create", "report"):
            self.assertNotRegex(body, rf"['\"]{stage}['\"]\s*:")

    def test_stage_context_sync_targets_the_single_parent_agent_pane(self):
        self.assertEqual(self.shell_html.count('id="techChatPane"'), 1)
        self.assertRegex(self.shell_js, r"function\s+syncAgentStageContext\s*\(")
        sync = re.search(
            r"function\s+syncAgentStageContext\s*\([^)]*\)\s*\{([\s\S]*?)(?=\n\s*function |\n\s*/\*)",
            self.shell_js,
        )
        self.assertIsNotNone(sync)
        self.assertIn("techChatPane", sync.group(1))
        self.assertRegex(
            self.shell_js,
            r"function\s+applyStage\s*\([^)]*\)\s*\{[\s\S]{0,1200}syncAgentStageContext\s*\(",
        )


if __name__ == "__main__":
    unittest.main()
