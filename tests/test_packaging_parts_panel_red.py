"""红测：图纸零件可选中的行 + 右栏零件面板（第 2 层）。

Spec：`docs/specs/packaging-parts-selectable-panel.md`

**现状缺口（代码事实）**：

  · `tech_app/frontend/app.js` 的 `renderTree()` drawing_flow 分支（约 2010–2023 行）只 appendChild，
    该分支内 `dataset.partId` / `addEventListener` / `selectPart` **出现 0 次** → 点零件毫无反应；
  · 右栏 `#modelPanes` 只有 `#viewer`（THREE）/`#partDetail`/`#parameterEditor`；图纸项目下
    `currentIR` / `currentGeometry` / `currentDrawings` 全为 null，右栏永远停在占位与空白画布；
  · 没有任何"按零件取几何"的只读接口：`GET /drawing-flow` 不含实体，
    `GET /requirement/packaging-parts` 不含坐标。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
INDEX_HTML = ROOT / "tech_app" / "frontend" / "index.html"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
STYLE_FILES = [ROOT / "tech_app" / "frontend" / "drawing-flow.css",
               ROOT / "tech_app" / "frontend" / "style.css"]

READ_PATH = "/api/projects/{pid}/requirement/packaging-parts/{part_code}"
NOT_FOUND_CODE = "PACKAGING_PART_NOT_FOUND"
PANEL_ID = "packagingPartPanel"


def _app_js() -> str:
    return APP_JS.read_text(encoding="utf-8", errors="replace")


def _drawing_flow_branch() -> str:
    """renderTree() 里 `currentDrawingEntry === "drawing_flow"` 那段。"""
    source = _app_js()
    start = source.index("function renderTree(ir)")
    end = source.index("function renderIR(ir)")
    block = source[start:end]
    marker = 'currentDrawingEntry === "drawing_flow"'
    begin = block.index(marker)
    return block[begin:]


# --------------------------------------------------------------------------- #
# A 组：后端单件只读面
# --------------------------------------------------------------------------- #
class ABackendRoute(unittest.TestCase):
    def test_a1_read_path_constant_and_route_registered(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("PACKAGING_PART_READ_PATH", source,
                      "main.py 必须有 PACKAGING_PART_READ_PATH 常量（Spec §2）")
        self.assertIn("/requirement/packaging-parts/{part_code}", source,
                      "main.py 缺单件详情路由")
        import tech_app.backend.main as main
        paths = {route.path for route in main.app.routes}
        self.assertIn(READ_PATH, paths, "单件详情路由未注册：%s" % READ_PATH)

    def test_a2_not_found_code_and_200_shape(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn(NOT_FOUND_CODE, source, "零件不存在必须给 %s" % NOT_FOUND_CODE)
        for key in ('"found"', '"built"', '"outline"', '"component_bbox"',
                    '"evidence"', '"size_source"', '"summary"'):
            self.assertIn(key, source, "单件详情响应缺键 %s" % key)

    def test_a3_route_is_read_only(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("PACKAGING_PART_READ_PATH", source, "先要有常量（Spec §2）")
        start = source.index("PACKAGING_PART_READ_PATH")
        block = source[start:start + 4000]
        self.assertNotIn("_require(", block,
                         "单件详情是纯读，不得判写权限（与列表口径一致）")


# --------------------------------------------------------------------------- #
# B 组：行可点
# --------------------------------------------------------------------------- #
class BClickableRows(unittest.TestCase):
    def test_b1_rows_carry_part_id_and_click(self):
        block = _drawing_flow_branch()
        self.assertIn("dataset.partId", block,
                      "零件行必须带 dataset.partId（今天 0 次）")
        self.assertTrue("addEventListener" in block or "onclick" in block,
                        "零件行必须绑 click")

    def test_b2_click_calls_the_packaging_selector(self):
        block = _drawing_flow_branch()
        self.assertIn("selectPackagingPart", block,
                      "零件行点击必须走 selectPackagingPart(part_code)")
        self.assertNotIn("selectPart(", block,
                         "图纸零件行不得调用视觉链路的 selectPart（它会清空右栏）")

    def test_b3_selector_is_a_new_function(self):
        source = _app_js()
        self.assertIn("function selectPackagingPart", source,
                      "必须新增 selectPackagingPart()，不复用 selectPart()")

    def test_b4_selection_state_variable_exists(self):
        source = _app_js()
        self.assertRegex(source, r"currentSelectedPanelPart",
                         "需要独立的选中件变量，避免污染视觉链路的 currentSelectedId")


# --------------------------------------------------------------------------- #
# C 组：右栏面板
# --------------------------------------------------------------------------- #
class CRightPanel(unittest.TestCase):
    def test_c1_container_exists_inside_model_panes_and_hidden(self):
        html = INDEX_HTML.read_text(encoding="utf-8", errors="replace")
        self.assertIn('id="%s"' % PANEL_ID, "index.html 缺 #%s" % PANEL_ID)
        panes_start = html.index('id="modelPanes"')
        panes_end = html.index("analysisPanel", panes_start)
        panel_at = html.index('id="%s"' % PANEL_ID)
        self.assertTrue(panes_start < panel_at < panes_end,
                        "#%s 必须放在 #modelPanes 内（右栏），不是左栏或抽屉" % PANEL_ID)
        self.assertRegex(html[panel_at - 200:panel_at + 200], r"\bhidden\b",
                         "面板默认必须 hidden，未选中零件时不留白")

    def test_c2_panel_pieces_exist(self):
        html = INDEX_HTML.read_text(encoding="utf-8", errors="replace")
        for node in ("packagingPartTitle", "packagingPartOutline",
                     "packagingPartFacts", "packagingPartEvidence"):
            self.assertIn('id="%s"' % node, "面板缺 %s" % node)

    def test_c3_renderer_switches_the_panel(self):
        source = _app_js()
        self.assertIn(PANEL_ID, source, "app.js 必须操作 #%s" % PANEL_ID)
        self.assertRegex(source, r"function\s+\w*[Pp]ackagingPart\w*\s*\(",
                         "需要独立的零件面板渲染函数")

    def test_c4_3d_panel_is_not_shown_for_drawing_projects(self):
        source = _app_js()
        marker = source.find('currentDrawingEntry === "drawing_flow"')
        self.assertGreaterEqual(marker, 0)
        window = source[max(0, marker - 3000):marker + 3000]
        self.assertIn("viewer", window,
                      "图纸项目下必须显式处理 #viewer（今天是隐藏它，不是留着空白画布）")


# --------------------------------------------------------------------------- #
# D 组：三态文案
# --------------------------------------------------------------------------- #
class DStatusCopy(unittest.TestCase):
    def _copy_table_body(self) -> str:
        source = _app_js()
        marker = "PACKAGING_OUTLINE_COPY"
        self.assertIn(marker, source,
                      "三态文案必须收在一张表里（PACKAGING_OUTLINE_COPY），不许散落在模板串里")
        start = source.index(marker)
        return source[start:start + 900]

    def test_d1_table_has_three_states(self):
        body = self._copy_table_body()
        for status in ("closed", "open", "unavailable"):
            self.assertIn(status, body, "文案表缺 %s" % status)

    def test_d2_closed_copy(self):
        self.assertIn("轮廓已闭合", self._copy_table_body(), "closed 必须有自己的文案")

    def test_d3_open_copy_mentions_bbox_and_estimate_only(self):
        body = self._copy_table_body()
        self.assertIn("包围盒", body, "open 的文案必须点明尺寸来自包围盒")
        self.assertIn("仅供估算", body, "open 的文案必须说明仅供参考")

    def test_d4_unavailable_copy_mentions_unit(self):
        self.assertIn("单位未确认", self._copy_table_body(),
                      "unavailable 必须点明单位未确认")

    def test_d5_reasons_are_named_in_the_copy_table(self):
        body = self._copy_table_body()
        for reason in ("no_closed_loop", "loop_too_small"):
            self.assertIn(reason, body, "必须能显示降级原因 %s" % reason)


# --------------------------------------------------------------------------- #
# E 组：前端不许做几何
# --------------------------------------------------------------------------- #
class ENoFrontendGeometry(unittest.TestCase):
    def test_e1_no_geometry_solving_in_frontend(self):
        source = _app_js()
        for banned in ("polygon_area", "shoelace", "computeLoop", "findLoop", "loopOf"):
            self.assertNotIn(banned, source,
                             "前端不许做几何求解（出现 %s）——坐标只从后端来" % banned)

    def test_e2_outline_rendered_with_viewbox(self):
        source = _app_js()
        match = re.search(r"function\s+\w*[Pp]ackagingPart\w*\s*\(", source)
        self.assertIsNotNone(match, "先要有零件面板渲染函数（Spec §3）")
        body = source[match.start():]
        nxt = body.find("\nfunction ", 10)
        body = body[:nxt if nxt > 0 else 4000]
        self.assertIn("viewBox", body,
                      "轮廓必须用 SVG viewBox 直接吃后端坐标，不在 JS 里逐点缩放")


# --------------------------------------------------------------------------- #
# F 组：不再承诺 3D
# --------------------------------------------------------------------------- #
class FNoFalse3DPromise(unittest.TestCase):
    def test_f1_viewer_title_is_rewritten_for_drawing_projects(self):
        source = _app_js()
        self.assertIn("viewerPartName", source)
        match = re.search(r"currentDrawingEntry === \"drawing_flow\"", source)
        self.assertIsNotNone(match)
        # 图纸项目专用的标题文案必须存在，且不承诺 3D
        self.assertIn("图纸零件", source, "图纸项目下 #viewerPartName 要换成图纸零件口径的文案")
        window = source[max(0, match.start() - 1500):match.start() + 6000]
        self.assertNotIn("3D 视图 · 选择零件后查看", window,
                         "图纸项目分支里不得保留 3D 承诺文案")


if __name__ == "__main__":
    unittest.main(verbosity=2)
