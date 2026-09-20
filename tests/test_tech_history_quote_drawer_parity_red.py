import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
QUOTE = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")
HTML = (ROOT / "tech_app/frontend/tech-workbench.html").read_text(encoding="utf-8")
CSS = (ROOT / "tech_app/frontend/tech-workbench.css").read_text(encoding="utf-8")
JS = (ROOT / "tech_app/frontend/tech-workbench.js").read_text(encoding="utf-8")


def block(text: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", text, re.S)
    if not match:
        raise AssertionError(f"缺少样式：{selector}")
    return re.sub(r"\s+", "", match.group(1))


class TechHistoryQuoteDrawerParityContract(unittest.TestCase):
    def test_static_drawer_uses_quote_structure_and_accessibility(self):
        self.assertRegex(HTML, r'id="techHistoryOverlay"[^>]*class="[^"]*overlay')
        drawer = re.search(r'<aside[^>]+id="techHistoryDrawer"[^>]*>([\s\S]*?)</aside>', HTML)
        self.assertIsNotNone(drawer)
        opening = re.search(r'<aside[^>]+id="techHistoryDrawer"[^>]*>', HTML).group(0)
        self.assertRegex(opening, r'class="[^"]*drawer[^"]*tech-history-drawer')
        self.assertIn('role="dialog"', opening)
        self.assertIn('aria-modal="true"', opening)
        for token in ("drawer-header", "drawer-actions", "drawer-body", "techHistoryList"):
            with self.subTest(token=token):
                self.assertIn(token, drawer.group(1))

    def test_drawer_has_close_new_and_refresh_controls(self):
        self.assertRegex(HTML, r'id="techHistoryClose"[^>]+aria-label="关闭历史记录"')
        self.assertRegex(HTML, r'id="techHistoryNew"[^>]*>[\s\S]{0,150}新建技术项目')
        self.assertRegex(HTML, r'id="techHistoryRefresh"[^>]+aria-label="刷新历史记录"')
        self.assertRegex(HTML, r'id="techHistoryNew"[^>]+class="[^"]*(?:btn-mini primary|primary btn-mini)')

    def test_desktop_geometry_and_motion_match_quote_drawer(self):
        drawer = block(CSS, ".tech-history-drawer")
        self.assertIn("position:fixed", drawer)
        self.assertIn("left:0", drawer)
        self.assertIn("width:340px", drawer)
        self.assertIn("height:100vh", drawer)
        self.assertIn("transform:translateX(-104%)", drawer)
        self.assertRegex(drawer, r"box-shadow:4px0(?:px)?24pxrgba\(15,16,25,?\.12\)")
        shown = block(CSS, ".tech-history-drawer.show")
        self.assertIn("transform:translateX(0)", shown)

    def test_overlay_matches_quote_and_uses_show_state(self):
        overlay = block(CSS, ".tech-history-mask")
        self.assertRegex(overlay, r"background:rgba\(15,16,25,?\.35\)")
        self.assertIn("opacity:0", overlay)
        self.assertIn("visibility:hidden", overlay)
        shown = block(CSS, ".tech-history-mask.show")
        self.assertIn("opacity:1", shown)
        self.assertIn("visibility:visible", shown)

    def test_open_close_escape_and_focus_return_are_wired(self):
        self.assertRegex(JS, r'openTechHistory[\s\S]{0,900}classList\.add\(["\']show["\']\)')
        self.assertRegex(JS, r'closeTechHistory[\s\S]{0,900}classList\.remove\(["\']show["\']\)')
        self.assertRegex(JS, r'techHistoryOverlay[\s\S]{0,500}addEventListener\(["\']click["\'][^;]*closeTechHistory')
        self.assertRegex(JS, r'techHistoryClose[\s\S]{0,500}addEventListener\(["\']click["\'][^;]*closeTechHistory')
        self.assertRegex(JS, r'(?:event|e)\.key\s*===?\s*["\']Escape["\'][\s\S]{0,300}closeTechHistory')
        self.assertRegex(JS, r'techHistory["\']?\)?\.focus\(\)|\$\(["\']techHistory["\']\)\?*\.focus\(\)')

    def test_refresh_and_new_project_actions_are_explicit(self):
        self.assertRegex(JS, r'techHistoryRefresh[\s\S]{0,500}addEventListener\(["\']click["\'][\s\S]{0,250}loadTechHistory')
        self.assertRegex(JS, r'techHistoryNew[\s\S]{0,700}/报价首页\.html\?assistant=tech')

    def test_history_data_and_stage_restore_contract_remain(self):
        for token in ("fetch('/api/projects'", "techStageFromProject", "techHistoryRestore", "applyStage"):
            with self.subTest(token=token):
                self.assertIn(token, JS)
        self.assertRegex(JS, r'/workflow')
        self.assertNotRegex(JS, r'fetch\([^\n]*(?:delete|DELETE)')

    def test_history_cards_show_current_and_stage_metadata(self):
        self.assertRegex(JS, r'(?:stage|stageLabel|stageName)')
        self.assertRegex(JS, r'(?:classList\.add\(["\']current["\']\)|className[^;]*current)')
        self.assertRegex(JS, r'aria-current')

    def test_dynamic_drawer_creation_is_removed(self):
        self.assertNotRegex(JS, r'function\s+ensureHistoryUi\s*\(')
        self.assertNotRegex(JS, r'createElement\(["\']aside["\']\)[\s\S]{0,500}techHistoryDrawer')
        self.assertNotRegex(JS, r'techHistoryDrawer[\s\S]{0,500}\.hidden\s*=')


if __name__ == "__main__":
    unittest.main()
