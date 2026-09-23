r"""红测：包装图纸项目一进 2.1 右栏就是 2D（不许先给一个 3D 的框）。

Spec：`docs/specs/packaging-2-1-first-paint-must-be-2d-not-3d.md`

用户原话（2026-09-23）：

> 为什么现在我重新刷新进来又变成上面是3D的空间了 应该直接就是这个2D的图

现状缺口（HEAD `872898b` 工作副本只读）：
  · 右栏的 HTML 默认态是 **3D**：`index.html` 里 `#viewer`（`.view-3d-content`，写着
    「从「零件清单」中选择一项，查看模型…」）默认可见，而 `#packagingCadPlanViewer` /
    `#packagingPartPanel` / `#packagingShapeIdle` 都带 `hidden`；
  · 把右栏切成 2D 的只有 `enterDrawingFlowPanes()`（`app.js:1346-1356`），而全文件**只有一处**
    调用它：`parseDrawing()` 里 `if (currentDrawingEntry === "drawing_flow")`（`app.js:936-937`）——
    即"用户点了开始解析之后"；
  · `openProject()`（`app.js:4457-4585`）在 `entry === "drawing_flow"` 时只读回链路状态与零件文档
    （`loadDrawingFlowPanel()` / `refreshPackagingParts()` / `renderTree()`），**完全不碰右栏** ⇒
    刷新或重进 2.1 时右栏留在默认态，用户第一眼看到的就是那个 3D 空框。

纪律：只读 `app.js` / `index.html`；不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
INDEX_HTML = ROOT / "tech_app" / "frontend" / "index.html"

ENTRY_FN = "enterDrawingFlowPanes"
OPEN_FN = "openProject"
APPLY_FN = "applyPackagingShapeOnlyPanes"
GUARD = 'currentDrawingEntry === "drawing_flow"'
PLAN_READ = "loadPackagingCadPlan()"
IDLE_COPY = "从左栏选一件零件，这里直接看它的形状。"


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def function_body(source: str, name: str) -> str:
    at = source.find("function " + name + "(")
    if at < 0:
        at = source.find("async function " + name + "(")
    if at < 0:
        return ""
    brace = source.find("{", at)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[at:index + 1]
    return ""


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = read_text(APP_JS)
        cls.html = read_text(INDEX_HTML)


# --------------------------------------------------------------------------- #
# A 组：进项目就切右栏（不许等用户点解析）
# --------------------------------------------------------------------------- #
class AFirstPaint(Base):
    def test_a1_open_project_switches_the_right_pane(self):
        body = function_body(self.app, OPEN_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % OPEN_FN)
        self.assertIn(ENTRY_FN + "(", body,
                      "项目打开、判定是图纸链路后必须当场把右栏切成 2D（Spec §2.1）")

    def test_a2_the_switch_is_inside_the_drawing_flow_branch(self):
        body = function_body(self.app, OPEN_FN)
        guard = body.find(GUARD)
        call = body.find(ENTRY_FN + "(")
        self.assertGreaterEqual(guard, 0, "openProject 里必须有 %s 判定" % GUARD)
        self.assertGreaterEqual(call, 0, "openProject 里要调用 %s()" % ENTRY_FN)
        self.assertLess(guard, call,
                        "切右栏必须落在图纸链路那条分支里（Spec §2.1/§2.3）")

    def test_a3_every_call_site_is_guarded(self):
        pattern = r"(?<!function )" + re.escape(ENTRY_FN) + r"\(\);"
        for match in re.finditer(pattern, self.app):
            head = self.app[max(0, match.start() - 400):match.start()]
            self.assertIn(GUARD, head,
                          "每一处 %s() 调用都必须在图纸链路守卫里，不许误伤视觉链路（Spec §2.3）"
                          % ENTRY_FN)

    def test_a4_the_switch_still_hides_3d_and_fills_the_pane(self):
        body = function_body(self.app, ENTRY_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % ENTRY_FN)
        self.assertIn(APPLY_FN + "(", body,
                      "切 2D 仍要打 data-qq-no-3d / data-qq-fill 两个钩子（Spec §2.3）")
        self.assertIn('$("viewer")', body,
                      "切 2D 仍要显式撤掉 3D 画布（Spec §2.1）")
        self.assertIn("hidden", body, "3D 画布必须被隐藏（Spec §2.1）")
        self.assertIn("packagingCadPlanViewer()", body,
                      "2D 容器（CAD 平面图）要跟着显隐（Spec §2.1）")


# --------------------------------------------------------------------------- #
# B 组：顺序与健壮（先切面板、不依赖选中件、失败不回头）
# --------------------------------------------------------------------------- #
class BOrderAndRobustness(Base):
    def test_b1_panes_switch_before_reading_coordinates(self):
        body = function_body(self.app, ENTRY_FN)
        apply_at = body.find(APPLY_FN + "(")
        read_at = body.find(PLAN_READ)
        self.assertGreaterEqual(apply_at, 0, "切面板必须走 %s()（Spec §2.1）" % APPLY_FN)
        self.assertGreaterEqual(read_at, 0, "仍要读一次坐标：%s" % PLAN_READ)
        self.assertLess(apply_at, read_at,
                        "先切面板、再读坐标，首屏才不闪 3D 框（Spec §2.1）")

    def test_b2_switch_does_not_require_a_selected_part(self):
        body = function_body(self.app, ENTRY_FN)
        for token in ("currentSelectedPanelPart", "currentPackagingBusinessPartCode"):
            self.assertNotIn(token, body,
                             "未选中零件时也必须切到 2D：%s 不许做前置判定（Spec §2.2）" % token)
        self.assertNotIn("return;\n  }", body.replace("\r", ""),
                         "函数不许在开头直接返回（Spec §2.2）")

    def test_b3_switch_is_idempotent_and_safe(self):
        body = function_body(self.app, ENTRY_FN)
        self.assertNotIn("throw", body, "重复调用只重复同一批状态，不许抛错（Spec §2.2）")
        self.assertIn(IDLE_COPY, self.html,
                      "未选中零件时那句引导逐字保留（Spec §2.2）")


# --------------------------------------------------------------------------- #
# C 组：护栏（别的入口与 3D 口径不许被动）
# --------------------------------------------------------------------------- #
class CGuardrails(Base):
    def test_c1_3d_is_still_removed_by_the_two_hooks(self):
        body = function_body(self.app, APPLY_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % APPLY_FN)
        self.assertIn('data-qq-no-3d', body, "3D 口径仍靠 data-qq-no-3d 撤（Spec §2.3）")
        self.assertIn("qqFill", body, "占满仍靠 data-qq-fill（Spec §2.3）")

    def test_c2_non_packaging_first_paint_is_untouched(self):
        for node in ('id="packagingCadPlanViewer"', 'id="packagingShapeIdle"'):
            at = self.html.find(node)
            self.assertGreaterEqual(at, 0, "index.html 里应有 %s" % node)
            line = self.html[self.html.rfind("<", 0, at):self.html.find(">", at) + 1]
            self.assertIn("hidden", line,
                          "非包装项目的右栏默认态不许被改：%s 仍要带 hidden（Spec §2.3）" % node)


if __name__ == "__main__":
    unittest.main()
