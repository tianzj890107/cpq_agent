from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


class TechContextSubstepsAndFrameStatusRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (FRONTEND / "tech-workbench.html").read_text(encoding="utf-8")
        cls.js = (FRONTEND / "tech-workbench.js").read_text(encoding="utf-8")
        cls.css = (FRONTEND / "tech-workbench.css").read_text(encoding="utf-8")
        cls.assembly_css = (FRONTEND / "assembly-integration.css").read_text(encoding="utf-8")
        cls.inline_css = (FRONTEND / "inline-analysis.css").read_text(encoding="utf-8")

    def test_inline_frame_ready_status_row_is_removed(self):
        self.assertNotIn("techStageMessage", self.html)
        self.assertNotIn("techStageMessage", self.js)
        self.assertNotIn("tech-wb-state has-frame", self.js)
        self.assertNotRegex(self.js, r'\$\{meta\.no\}\s+\$\{meta\.label\}\s+已就绪')

    def test_iframe_load_still_syncs_action_bar(self):
        load = re.search(r"iframe\.addEventListener\(['\"]load['\"],\s*\(\)\s*=>\s*\{([\s\S]*?)\}\);", self.js)
        self.assertIsNotNone(load)
        self.assertIn("syncActionBar()", load.group(1))

    def test_context_substep_host_is_rendered(self):
        self.assertRegex(self.html, r'id="techSubstepsBar"[^>]+aria-label="[^"]*小流程[^"]*"')

    def test_major_one_and_five_define_exact_substeps(self):
        expected = {
            "requirement-create": "创建",
            "requirement-confirm": "确认",
            "requirement-review": "审核",
            "summary": "汇总结果",
            "report-review": "结果审核",
            "report-publish": "发布并回传报价",
        }
        for stage, label in expected.items():
            self.assertRegex(self.js, rf"['\"]{re.escape(stage)}['\"][^\n]{{0,180}}['\"]{re.escape(label)}['\"]|['\"]{re.escape(label)}['\"][^\n]{{0,180}}['\"]{re.escape(stage)}['\"]")
        self.assertNotRegex(self.js, r"label:\s*['\"](?:1\.[123]|5\.[123])")

    def test_context_tabs_share_card_header_position_and_do_not_move_major_steps(self):
        self.assertRegex(self.html, r'(?:tech-workspace-context|tech-card-header)[\s\S]{0,500}id="techSubstepsBar"')
        self.assertNotRegex(self.html, r'</div>\s*<nav class="tech-substeps-bar" id="techSubstepsBar"')

    def test_context_substeps_navigate_through_apply_stage(self):
        self.assertRegex(self.js, r'(?:techSubstepsBar|data-substep)[\s\S]{0,1800}applyStage\(')
        self.assertRegex(self.js, r'(?:state\.stage|currentMajorStep\(\))[\s\S]{0,1000}(?:hidden|replaceChildren|innerHTML)')

    def test_parent_substep_active_style_is_white_blue_outlined(self):
        rule = re.search(r'\.tech-substep-btn\.active\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(rule)
        body = rule.group(1)
        self.assertRegex(body, r'background:[^;}]*(?:#fff|#FFFFFF|twb-card)')
        self.assertRegex(body, r'color:[^;}]*(?:twb-primary|#0067D1)')
        self.assertRegex(body, r'border(?:-color)?:[^;}]*(?:primary|#0067D1|#B8D7F4)')
        self.assertNotRegex(body, r'color:\s*#fff')

    def test_existing_process_tabs_do_not_use_dark_active_fill(self):
        active = re.search(r'\.ai-tabs\s+button\.active\s*\{([^}]*)\}', self.assembly_css)
        self.assertIsNotNone(active)
        self.assertNotRegex(active.group(1), r'background:[^;}]*(?:oc-accent|#0067D1)')
        self.assertNotRegex(active.group(1), r'color:\s*#fff')
        inline = re.search(r'\.inline-analysis-tabs button\.active\s*\{([^}]*)\}', self.inline_css)
        self.assertIsNotNone(inline)
        self.assertNotRegex(inline.group(1), r'background:[^;}]*(?:linear-gradient|#0067D1)')
        self.assertNotRegex(inline.group(1), r'color:\s*#fff')


if __name__ == "__main__":
    unittest.main()
