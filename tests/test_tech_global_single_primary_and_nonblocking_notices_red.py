from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_bytes().replace(b"\x00", b"").decode("utf-8")


WB_HTML = read("tech_app/frontend/tech-workbench.html")
WB_CSS = read("tech_app/frontend/tech-workbench.css")
WB_JS = read("tech_app/frontend/tech-workbench.js")
RUNTIME = read("tech_app/frontend/tech-board-runtime.js")
CHAT_CSS = read("tech_app/frontend/agent-chat.css")
QUOTE = read("确认需求解析结果.html")
SOURCES = {
    "requirement-create": read("tech_app/frontend/requirement-create.js"),
    "requirement-confirm": read("tech_app/frontend/requirement-confirm-page.js"),
    "requirement-review": read("tech_app/frontend/requirement-review-page.js"),
    "drawing": read("tech_app/frontend/app.js"),
    "process": read("tech_app/frontend/assembly-integration.js"),
    "cost": read("tech_app/frontend/cost-review.js"),
    "summary": read("tech_app/frontend/summary-result.js"),
    "report-review": read("tech_app/frontend/report-review-result.js"),
    "report-publish": read("tech_app/frontend/report-publish-result.js"),
}


class TechGlobalSinglePrimaryAndNonblockingNoticesContract(unittest.TestCase):
    def test_all_nine_stages_register_actions(self):
        self.assertEqual(9, len(SOURCES))
        for stage, source in SOURCES.items():
            with self.subTest(stage=stage):
                self.assertIn("TechBoardRuntime.registerActions", source)

    def test_runtime_exposes_exactly_one_visible_primary_guard(self):
        for token in ("primary", "visible", "primary_count", "stage", "view"):
            with self.subTest(token=token):
                self.assertIn(token, RUNTIME)
        self.assertRegex(RUNTIME, r"primary_count\s*!==\s*1|primaryCount\s*!==\s*1")

    def test_required_before_after_labels_exist(self):
        expected = {
            "requirement-create": ("一键解析需求", "提交确认"),
            "requirement-confirm": ("通过确认",),
            "requirement-review": ("提交审核意见",),
            "drawing": ("一键解析图纸", "确认解析结果并进入下一步"),
            "process": ("一键分析整合图纸", "确认图纸并进入参数推荐", "一键生成参数推荐",
                        "确认并进入下一页签", "一键生成组装工艺", "确认并进入下一步"),
            "cost": ("一键测算全部成本", "确认成本"),
            "summary": ("一键生成报告草稿", "提交审核"),
            "report-review": ("审核通过并进入下一步",),
            "report-publish": ("发布报告", "回传销售经理继续报价"),
        }
        for stage, labels in expected.items():
            for label in labels:
                with self.subTest(stage=stage, label=label):
                    self.assertIn(label, SOURCES[stage])

    def test_dynamic_stages_switch_primary_role_from_real_state(self):
        checks = {
            "requirement-create": r"role:\s*extracted\s*\?\s*['\"]primary['\"]\s*:\s*['\"]aux",
            "cost": r"role:\s*!?crCostsComplete\(\)\s*\?\s*['\"]primary",
            "report-publish": r"status[\s\S]{0,1200}role:\s*[^,}\n]*primary",
        }
        for stage, pattern in checks.items():
            with self.subTest(stage=stage):
                self.assertRegex(SOURCES[stage], pattern)

    def test_process_declares_three_tab_state_transitions(self):
        source = SOURCES["process"]
        for token in ("aiTab === 'drawings'", "aiTab === 'params'", "aiTab === 'process'",
                      "aiHasParams()", "aiHasProcess()"):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertRegex(source, r"TechEmbed[\s\S]{0,300}requestNavigate")

    def test_primary_button_keeps_start_integration_visual_language(self):
        block = re.search(r"\.tech-chat-actions\s*>\s*button\.primary\s*\{([^}]*)\}", WB_CSS, re.S)
        self.assertIsNotNone(block)
        self.assertIn("var(--gradient-primary)", block.group(1))
        self.assertRegex(block.group(1), r"color:\s*#fff")

    def test_binding_caption_returns_below_the_input(self):
        # 最新决策（取代第 33 批）：说明位于输入框下方、走普通文档流，不得绝对定位。
        self.assertIn("会话绑定当前项目；右侧工作台只承载业务步骤，不重复会话。", WB_HTML)
        composer = re.search(r"#techChatPane \.oc-composer\s*\{([^}]*)\}", WB_CSS, re.S)
        self.assertIsNotNone(composer)
        self.assertRegex(composer.group(1), r"padding:\s*10px\s+16px\s+0")
        caption = re.search(r"#techChatPane \.oc-disc\s*\{([^}]*)\}", WB_CSS, re.S)
        self.assertIsNotNone(caption)
        self.assertNotRegex(caption.group(1), r"position:\s*(absolute|fixed)")
        self.assertRegex(caption.group(1), r"margin-top:\s*9px")

    def test_compact_autogrow_input_contract_remains(self):
        for token in ("width: 34px", "height: 34px", "width: 36px", "height: 36px",
                      "font-size: 12px", "line-height: 20px"):
            with self.subTest(token=token):
                self.assertIn(token, CHAT_CSS)
        self.assertIn("Math.min(contentHeight, 120)", read("tech_app/frontend/agent-chat.js"))

    def test_tech_permission_notice_stays_inside_existing_title_row(self):
        header = re.search(r'<div class="tech-workspace-context"[^>]*>(.*?)</div>\s*<div id="techWorkspaceOutlet"', WB_HTML, re.S)
        self.assertIsNotNone(header)
        self.assertIn('id="techContextNotice"', header.group(1))
        notice = re.search(r"\.tech-context-notice\s*\{([^}]*)\}", WB_CSS, re.S)
        self.assertIsNotNone(notice)
        self.assertRegex(notice.group(1), r"white-space:\s*nowrap")
        self.assertRegex(notice.group(1), r"overflow:\s*hidden")

    def test_quote_permission_notice_no_longer_inserts_normal_flow_bar(self):
        self.assertNotRegex(QUOTE, r"insertBefore\(bar,\s*host\)")
        self.assertNotRegex(QUOTE, r"bar\.style\.cssText\s*=\s*['\"][^'\"]*margin:")
        self.assertIn("只能查看", QUOTE)

    def test_footer_remains_fixed_flex_child_of_results_card(self):
        self.assertIn('class="tech-workbench-bottom"', WB_HTML)
        self.assertIn('id="techPrev"', WB_HTML)
        self.assertIn('id="techNext"', WB_HTML)
        footer = re.search(r"\.tech-workbench-bottom\s*\{([^}]*)\}", WB_CSS, re.S)
        outlet = re.search(r"#techWorkspaceOutlet\s*\{([^}]*)\}", WB_CSS, re.S)
        self.assertIsNotNone(footer)
        self.assertIsNotNone(outlet)
        self.assertRegex(footer.group(1), r"flex:\s*0\s+0\s+auto")
        self.assertRegex(outlet.group(1), r"flex:\s*1")
        self.assertRegex(outlet.group(1), r"min-height:\s*0")


if __name__ == "__main__":
    unittest.main()
