from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_bytes().replace(b"\x00", b"").decode("utf-8")


HTML = read("tech_app/frontend/index.html")
CSS = read("tech_app/frontend/workbench.css")
JS = read("tech_app/frontend/app.js")


class DrawingBoardTwoColumnPartsAnd3DContract(unittest.TestCase):
    def test_parts_and_tree_are_unique(self):
        self.assertEqual(1, len(re.findall(r'id=["\']secParts["\']', HTML)))
        self.assertEqual(1, len(re.findall(r'id=["\']tree["\']', HTML)))

    def test_split_contains_parts_then_model_columns(self):
        split = re.search(r'class=["\'][^"\']*drawing-board-split[^"\']*["\'][^>]*>([\s\S]*?)</div>\s*</div>\s*</div>', HTML)
        self.assertIsNotNone(split, "2.1 右侧看板尚无 drawing-board-split")
        body = split.group(1)
        self.assertIn("drawing-parts-column", body)
        self.assertIn('id="secParts"', body)
        self.assertIn("drawing-model-column", body)
        self.assertIn('id="viewer"', body)
        self.assertLess(body.index("drawing-parts-column"), body.index("drawing-model-column"))

    def test_parts_are_no_longer_a_drawer_section(self):
        drawer = re.search(r'id=["\']ocDrawerBody["\'][^>]*>([\s\S]*?)</section>\s*<div id=["\']loginOverlay', HTML)
        self.assertIsNotNone(drawer)
        self.assertNotIn('id="secParts"', drawer.group(1))
        sec = re.search(r'<[^>]+id=["\']secParts["\'][^>]*>', HTML)
        self.assertIsNotNone(sec)
        self.assertNotIn("data-drawer-section", sec.group(0))

    def test_desktop_grid_and_independent_parts_scroll(self):
        split = re.search(r'\.drawing-board-split\s*\{([^}]*)\}', CSS, re.S)
        parts = re.search(r'\.drawing-parts-column\s*\{([^}]*)\}', CSS, re.S)
        model = re.search(r'\.drawing-model-column\s*\{([^}]*)\}', CSS, re.S)
        self.assertIsNotNone(split)
        self.assertRegex(split.group(1), r'display:\s*grid')
        self.assertRegex(split.group(1), r'grid-template-columns:\s*minmax\(')
        self.assertIsNotNone(parts)
        self.assertRegex(parts.group(1), r'overflow-y:\s*auto')
        self.assertIsNotNone(model)
        self.assertRegex(model.group(1), r'min-width:\s*0')

    def test_mobile_stacks_parts_above_model(self):
        media = re.search(r'@media\s*\(max-width:\s*900px\)\s*\{([\s\S]*?)\n\}', CSS)
        self.assertIsNotNone(media)
        self.assertRegex(media.group(1), r'\.drawing-board-split\s*\{[^}]*grid-template-columns:\s*1fr')

    def test_parts_view_does_not_move_section_into_overlay_host(self):
        branch = re.search(r'if\s*\(view\s*===\s*["\']parts-list["\'][\s\S]{0,650}?\n\s*\}', JS)
        self.assertIsNotNone(branch)
        self.assertNotIn('openBoardView("parts")', branch.group(0))
        self.assertRegex(branch.group(0), r'focus|scrollIntoView')
        self.assertIn('view: "parts-list"', branch.group(0))

    def test_drawing_overview_keeps_split_visible(self):
        branch = re.search(r'if\s*\(view\s*===\s*["\']drawing-overview["\'][\s\S]{0,500}?\n\s*\}', JS)
        self.assertIsNotNone(branch)
        self.assertNotRegex(branch.group(0), r'hide|hidden\s*=\s*true[^\n]*(?:secParts|drawing-board-split)')
        self.assertIn("setRightPane", branch.group(0))

    def test_existing_selection_and_3d_pipeline_remain(self):
        for token in ("function renderTree(", "function selectPart(", "togglePartSubActions(",
                      '$("viewer")', "initViewer", "viewerBroken"):
            with self.subTest(token=token):
                self.assertIn(token, JS)

    def test_analysis_only_replaces_right_column(self):
        setter = re.search(r'function setRightPane\([^)]*\)\s*\{([\s\S]*?)\n\}', JS)
        self.assertIsNotNone(setter)
        self.assertIn('$("analysisPanel")', setter.group(1))
        self.assertIn('$("modelPanes")', setter.group(1))
        self.assertNotIn("secParts", setter.group(1))

    def test_accessible_column_names_exist(self):
        self.assertRegex(HTML, r'class=["\'][^"\']*drawing-parts-column[^"\']*["\'][^>]*(?:aria-label=["\']零件清单["\']|aria-labelledby=)')
        self.assertRegex(HTML, r'class=["\'][^"\']*drawing-model-column[^"\']*["\'][^>]*(?:aria-label=["\']3D 视图["\']|aria-labelledby=)')


if __name__ == "__main__":
    unittest.main()
