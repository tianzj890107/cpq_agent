from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
PARENT_HTML = FRONTEND / "tech-workbench.html"
PARENT_JS = FRONTEND / "tech-workbench.js"
BRIDGE_JS = FRONTEND / "tech-board-bridge.js"
RUNTIME_JS = FRONTEND / "tech-board-runtime.js"

STAGE_PAGES = (
    "requirement-create.html", "requirement-confirm.html", "requirement-review.html",
    "index.html", "assembly-integration.html", "cost-review.html", "summary.html",
    "report-review.html", "report-publish.html",
)


class TechBoardBridgeProtocolRedTest(unittest.TestCase):
    def test_parent_and_iframe_protocol_modules_exist(self):
        self.assertTrue(BRIDGE_JS.is_file(), "缺少父壳协议模块 tech-board-bridge.js")
        self.assertTrue(RUNTIME_JS.is_file(), "缺少看板运行时 tech-board-runtime.js")

    def test_parent_loads_bridge_before_workbench(self):
        html = PARENT_HTML.read_text(encoding="utf-8")
        bridge = html.find("tech-board-bridge.js")
        workbench = html.find("tech-workbench.js")
        self.assertGreaterEqual(bridge, 0)
        self.assertGreater(workbench, bridge)

    def test_all_nine_stage_pages_load_one_runtime(self):
        missing = []
        for name in STAGE_PAGES:
            source = (FRONTEND / name).read_text(encoding="utf-8")
            if len(re.findall(r'<script[^>]+tech-board-runtime\.js', source)) != 1:
                missing.append(name)
        self.assertFalse(missing, f"未且仅引用一次看板运行时：{missing}")

    def test_protocol_has_envelope_correlation_and_origin_guards(self):
        self.assertTrue(BRIDGE_JS.is_file() and RUNTIME_JS.is_file())
        source = BRIDGE_JS.read_text(encoding="utf-8") + "\n" + RUNTIME_JS.read_text(encoding="utf-8")
        for token in (
            "cpq:tech-board", "version", "requestId", "projectId", "stage",
            "event.origin", "location.origin", "event.source",
        ):
            self.assertIn(token, source)
        self.assertNotRegex(source, r'postMessage\([^\n]+,[\s\n]*["\']\*["\']')

    def test_protocol_declares_required_commands_and_states(self):
        self.assertTrue(BRIDGE_JS.is_file() and RUNTIME_JS.is_file())
        source = BRIDGE_JS.read_text(encoding="utf-8") + "\n" + RUNTIME_JS.read_text(encoding="utf-8")
        for name in (
            "execute-action", "navigate-view", "refresh-data", "select-part", "ready",
            "action-state", "task-progress", "task-completed", "task-failed",
            "selection-changed",
        ):
            self.assertIn(name, source)

    def test_parent_no_longer_reaches_into_iframe_business_dom(self):
        source = PARENT_JS.read_text(encoding="utf-8")
        self.assertNotIn("contentDocument", source)
        self.assertNotRegex(source, r'frame[^\n]*querySelector|doc\.querySelector')
        self.assertNotRegex(source, r'target\.click\(\)|current\.click\(\)')


if __name__ == "__main__":
    unittest.main()

