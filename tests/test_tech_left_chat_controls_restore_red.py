from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
# 看板（2.1 右侧 iframe）里真正承载业务数据的页面脚本；左侧入口的数据只能来自它。
BOARD_SCRIPTS = ("app.js", "inline-analysis.js", "workflow-navigation.js")


class TechLeftChatControlsRestoreRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (FRONTEND / "tech-workbench.html").read_text(encoding="utf-8")
        cls.chat = (FRONTEND / "agent-chat.js").read_text(encoding="utf-8", errors="replace").replace("\x00", "")
        cls.css = (FRONTEND / "agent-chat.css").read_text(encoding="utf-8")
        cls.combined = cls.html + "\n" + cls.chat

    def _one_id(self, node_id):
        self.assertEqual(
            len(re.findall(rf'id=["\']{re.escape(node_id)}["\']', self.html)),
            1,
            f"父壳中 {node_id} 必须且只能存在一份",
        )

    def test_parent_chat_restores_single_persistent_control_hosts(self):
        for node_id in (
            "ocPlus", "ocCapabilityMenu", "ocResultActions", "ocPartsAction",
            "ocQuestionsAction", "ocReportAction", "ocFilesAction", "ocFilesCount",
            "ocTaskProgressHost",
        ):
            self._one_id(node_id)

    def test_plus_button_and_menu_are_accessible(self):
        self.assertRegex(
            self.html,
            r'<button[^>]+id="ocPlus"[^>]+type="button"[^>]+aria-(?:label|expanded)=',
        )
        self.assertRegex(self.html, r'id="ocCapabilityMenu"[^>]+role="menu"')
        for capability, label in (
            ("upload", "补充需求图纸"),
            ("evidence", "解析视图"),
            ("import3d", "导入已有 3D 模型"),
            ("review", "版本与校核审查"),
        ):
            self.assertRegex(
                self.html,
                rf'<button[^>]+data-tech-capability="{capability}"[^>]+role="menuitem"[^>]*>[\s\S]*?{label}',
            )

    def test_result_actions_are_persistent_accessible_buttons(self):
        self.assertRegex(
            self.html,
            r'id="ocResultActions"[^>]+aria-label="图纸解析结果"[^>]+hidden',
        )
        for node_id, label in (
            ("ocPartsAction", "零件清单"),
            ("ocQuestionsAction", "待澄清问题"),
            ("ocReportAction", "解析报告"),
        ):
            self.assertRegex(
                self.html,
                rf'<button[^>]+id="{node_id}"[^>]+type="button"[^>]*>[\s\S]*?{label}',
            )

    def test_controls_are_driven_by_drawing_stage_and_bridge_state(self):
        self.assertRegex(self.chat, r'(?:stage|context\.stage)[\s\S]{0,300}["\']drawing["\']')
        self.assertRegex(self.chat, r'(?:TechBoardBridge|cpq:tech-board)[\s\S]{0,800}(?:subscribe|snapshot|action-state|result-summary)')
        for node_id in ("ocPartsAction", "ocQuestionsAction", "ocFilesCount"):
            self.assertIn(node_id, self.chat)
        self.assertNotRegex(self.chat, r'contentDocument|#tree\s+\.part|querySelectorAll\(["\']#tree')

    def test_left_counts_derive_from_board_result_summary_groups(self):
        # 左侧按钮的“显示/隐藏”与计数只能来自 result-summary 的 parts/questions/report
        # 分组；只出现分组名不算数，必须真的读 available / count。
        match = re.search(
            r"function\s+applyDrawingResultSummary\s*\([\s\S]*?\n  \}", self.chat)
        self.assertIsNotNone(match, "缺少 applyDrawingResultSummary")
        body = match.group(0)
        for group in ("parts", "questions", "report"):
            self.assertRegex(body, rf"results\.{group}\b", f"未从 result-summary 读取 results.{group}")
        self.assertGreaterEqual(
            len(re.findall(r"\.available\s*===?\s*true|available\s*===\s*true", body)), 3,
            "三个结果入口都必须按 available 决定是否显示")
        self.assertRegex(body, r'setChipCount\(\s*\$\(\s*"ocPartsCount"',
                         "零件数量必须写入 #ocPartsCount")

    def test_board_actually_publishes_result_summary(self):
        # 左侧控件不是“恢复了 DOM”就算完成：看板必须真的发布 result-summary，
        # 否则计数与可用态在真实链路里永远是 0 / 隐藏。
        publishers = {name: (FRONTEND / name).read_text(encoding="utf-8", errors="replace")
                      for name in BOARD_SCRIPTS if (FRONTEND / name).is_file()}
        owner = [name for name, text in publishers.items() if "result-summary" in text]
        self.assertTrue(
            owner,
            f"右侧看板没有任何脚本发布 result-summary，左侧入口无数据来源（已查 {sorted(publishers)}）")

    def test_menu_closes_on_escape_and_restores_focus(self):
        self.assertRegex(self.chat, r'(?:event|e)\.key\s*===?\s*["\']Escape["\']')
        self.assertRegex(self.chat, r'ocPlus[\s\S]{0,1200}\.focus\(')
        self.assertRegex(self.chat, r'aria-expanded')

    def test_task_progress_is_keyed_by_task_id_and_has_failure_state(self):
        self.assertIn("ocTaskProgressHost", self.combined)
        for token in ("taskId", "running", "succeeded", "failed"):
            self.assertIn(token, self.chat)
        self.assertRegex(self.html, r'id="ocTaskProgressHost"[^>]+aria-live=')

    def test_parent_does_not_absorb_business_detail_dom(self):
        for forbidden_id in (
            "secUpload", "secEvidence", "secImport3d", "secParts", "secQuestions",
            "tree", "partDetail", "viewer", "analysisPanel", "analysisHost",
        ):
            self.assertNotRegex(self.html, rf'id=["\']{forbidden_id}["\']')
        self.assertNotRegex(
            self.html,
            r'(?:part|parts|零件|3D)[^>]{0,80}(?:drawer|modal)|(?:drawer|modal)[^>]{0,80}(?:part|parts|零件|3D)',
        )

    def test_controls_have_focus_and_disabled_styles(self):
        for selector in ("oc-ibtn", "oc-capability-item", "oc-chip", "oc-files-action"):
            self.assertRegex(self.css, rf'\.{selector}[^{{]*:(?:focus-visible|disabled)')


if __name__ == "__main__":
    unittest.main()

