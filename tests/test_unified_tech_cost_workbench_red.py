import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


class UnifiedTechCostWorkbenchContract(unittest.TestCase):
    def read(self, relative):
        path = ROOT / relative
        self.assertTrue(path.is_file(), f"缺少统一工作台文件：{relative}")
        return path.read_text(encoding="utf-8")

    def test_unified_shell_has_single_chat_and_workspace_outlet(self):
        html = self.read("tech_app/frontend/tech-workbench.html")
        self.assertEqual(len(re.findall(r'id=["\']techChatPane["\']', html)), 1)
        self.assertEqual(len(re.findall(r'id=["\']techWorkspaceOutlet["\']', html)), 1)
        self.assertIn("tech-workbench.js", html)
        self.assertIn("tech-workbench.css", html)

    def test_stage_registry_covers_technical_cost_and_output_flow(self):
        js = self.read("tech_app/frontend/tech-workbench.js")
        stages = {
            "requirement-create", "requirement-confirm", "requirement-review",
            "drawing", "process", "cost", "summary", "report-review", "report-publish",
        }
        for stage in stages:
            self.assertRegex(js, rf"[\"']{re.escape(stage)}[\"']", f"stage 未注册：{stage}")
        self.assertIn("embed=1", js)

    def test_navigation_messages_are_same_origin_and_whitelisted(self):
        js = self.read("tech_app/frontend/tech-workbench.js")
        self.assertIn("cpq:tech-workbench:navigate", js)
        self.assertRegex(js, r"event\.origin\s*!==\s*location\.origin")
        self.assertRegex(js, r"(?:STAGES|stages)\.(?:has|includes)|(?:STAGES|stages)\[")
        self.assertIn("popstate", js)
        self.assertIn("URLSearchParams", js)

    def test_quote_home_uses_unified_tech_entry(self):
        home = self.read("报价首页.html")
        self.assertRegex(home, r"tech\s*:\s*['\"]tech-workbench\.html")
        self.assertNotRegex(home, r"tech\s*:\s*['\"]requirement-create\.html")

    def test_legacy_pages_support_embed_mode(self):
        pages = [
            "requirement-create.js", "requirement-confirm-page.js", "requirement-review-page.js",
            "app.js", "assembly-integration.js", "cost.js", "summary-result.js",
            "report-review-result.js", "report-publish-result.js",
        ]
        for name in pages:
            text = self.read(f"tech_app/frontend/{name}")
            self.assertIn("embed", text, f"{name} 尚未支持统一工作台嵌入模式")

    def test_workbench_styles_define_two_panes_and_mobile_fallback(self):
        css = self.read("tech_app/frontend/tech-workbench.css")
        self.assertIn("tech-workbench-layout", css)
        self.assertIn("tech-chat-pane", css)
        self.assertIn("tech-workspace-pane", css)
        self.assertRegex(css, r"@media\s*\([^)]*max-width\s*:\s*900px")

    def test_progress_is_inside_right_workspace_card_not_global_top(self):
        html = self.read("tech_app/frontend/tech-workbench.html")
        workspace = html.find('class="tech-workspace-pane')
        progress = html.find('id="techStepsBar"')
        outlet = html.find('id="techWorkspaceOutlet"')
        self.assertGreater(workspace, -1)
        self.assertGreater(progress, workspace, "进度栏必须放进右侧工作台卡片")
        self.assertGreater(outlet, progress, "进度栏应位于右侧业务内容上方")
        before_body = html[:html.find('class="tech-workbench-body')]
        self.assertNotIn('id="techStepsBar"', before_body, "进度栏不得横跨左右两栏顶部")
        css = self.read("tech_app/frontend/tech-workbench.css")
        self.assertIn("tech-workspace-progress", css)

    def assert_unified_entry_file(self, relative, forbidden):
        text = self.read(relative)
        self.assertIn("tech-workbench.html", text, f"{relative} 尚未接入统一入口")
        for target in forbidden:
            direct = rf"(?:location|window\.location)\.href\s*=\s*[^;\n]*{re.escape(target)}|const\s+page\s*=\s*[^;\n]*{re.escape(target)}"
            self.assertNotRegex(text, direct, f"{relative} 仍会顶层直达 {target}")

    def test_quote_home_cards_and_tasks_always_use_unified_shell(self):
        self.assert_unified_entry_file("报价首页.html", [
            "tech-task.html", "requirement-detail.html", "cost-review.html", "assembly-integration.html",
        ])

    def test_tech_home_project_list_always_uses_unified_shell(self):
        self.assert_unified_entry_file("tech_app/frontend/home.js", [
            "requirement-create.html", "requirement-detail.html", "tech-task.html",
        ])

    def test_tech_inbox_tasks_always_use_unified_shell(self):
        self.assert_unified_entry_file("tech_app/frontend/cpq-tech-inbox.js", [
            "tech-task.html", "cost-review.html", "assembly-integration.html",
        ])

    def test_task_routes_preserve_task_and_select_expected_stage(self):
        sources = self.read("报价首页.html") + self.read("tech_app/frontend/cpq-tech-inbox.js")
        self.assertRegex(sources, r"stage=requirement-create[^\n]*(?:task_id|tech_task)")
        self.assertRegex(sources, r"stage=cost[^\n]*project")
        self.assertRegex(sources, r"stage=process[^\n]*project")


if __name__ == "__main__":
    unittest.main()
