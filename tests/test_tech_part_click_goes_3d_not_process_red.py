"""红测：零件清单点零件进 3D 视图，「工艺推荐」按钮才进工艺推荐（实现前应失败）。

现状缺口（实测）：
  · `app.js` 的 `selectPart()` 末尾无条件调用 `autoOpenGeneratedProcess(part)`；
    该函数对「库里已有工艺」的零件会 `openPartAnalysis(part, "process")`，把右栏
    从 3D 切到工艺推荐。
  · 于是所有选中零件的入口（零件行 `renderTree`、2D 缩略图 bbox、生成结果展示）
    都被劫持成「进工艺推荐」，零件行下面独立的「工艺推荐」子按钮反而没有区别。

要求：
  · 点零件 = 右侧 3D 视图（part-detail）；零件行下的「工艺推荐」子按钮才进
    part-process。
  · 「已生成即自动展开」只在解析 / 生成完成的时机保留（用户此前的要求），
    不再绑在「选中零件」上。
  · 判定与渲染全部复用既有 `partHasExistingProcess()` / `openPartAnalysis()` /
    `renderPartAnalysis()`，不新增接口、不触发生成、不新建父级面板。
"""
from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
APP_JS = F / "app.js"
PARENT_HTML = F / "tech-workbench.html"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
        i += 1
    return ""


class PartClickGoesTo3D(unittest.TestCase):
    """R1：选中零件只进 3D，不替用户切到工艺推荐。"""

    @classmethod
    def setUpClass(cls):
        cls.app = read(APP_JS)

    def test_select_part_never_hijacks_to_process_analysis(self):
        block = block_from(self.app, "function selectPart(part)")
        self.assertTrue(block, "找不到 selectPart(part)")
        for token in ("autoOpenGeneratedProcess(", "openPartAnalysis(", "renderPartAnalysis("):
            with self.subTest(token=token):
                self.assertNotIn(
                    token, block,
                    "点零件必须留在 3D 视图：selectPart() 里不得出现 " + token)

    def test_select_part_still_renders_3d_and_part_detail(self):
        block = block_from(self.app, "function selectPart(part)")
        for token in ('setRightPane("model")', "exitBoardViewHost()", "markSelection(",
                      "togglePartSubActions(", "updateChatContext(", 'notePartView("part-detail")'):
            with self.subTest(token=token):
                self.assertIn(token, block, "选中零件的既有 3D / 零件详情渲染被删：" + token)

    def test_part_row_and_bbox_clicks_only_select(self):
        row = block_from(self.app, "function renderNode(")
        self.assertTrue(row, "找不到零件行渲染 renderNode()")
        self.assertIn("selectPart(", row, "零件行点击必须走 selectPart()")
        self.assertNotIn("openPartAnalysis(", row, "零件行点击不得直接打开工艺推荐")
        bbox = block_from(self.app, "function renderBboxes(")
        self.assertTrue(bbox, "找不到缩略图 bbox 渲染 renderBboxes()")
        self.assertIn("selectPart(", bbox, "缩略图 bbox 点击必须走 selectPart()")
        self.assertNotIn("openPartAnalysis(", bbox, "缩略图点击不得直接打开工艺推荐")

    def test_process_subaction_still_opens_part_process(self):
        block = block_from(self.app, "function buildPartSubActions(")
        self.assertTrue(block, "找不到 buildPartSubActions()")
        self.assertIn("openPartAnalysis(part, mode)", block,
                      "零件行下的子按钮必须继续是工艺推荐的唯一入口")
        self.assertIn("event.stopPropagation()", block,
                      "子按钮必须继续阻止冒泡到零件行")
        self.assertRegex(block, r'\[\["process",\s*"工艺推荐"',
                         "子按钮的 mode/label 映射被改动")


class AutoExpandOnlyOnGenerationCompletion(unittest.TestCase):
    """R3：自动展开留在解析 / 生成完成，不再绑在选中零件上。"""

    @classmethod
    def setUpClass(cls):
        cls.app = read(APP_JS)

    def test_generated_result_selects_then_auto_opens(self):
        block = block_from(self.app, "function showGeneratedResult()")
        self.assertTrue(block, "找不到 showGeneratedResult()")
        self.assertIn("selectPart(target)", block, "生成完成仍要自动选中零件")
        self.assertIn("autoOpenGeneratedProcess(target)", block,
                      "生成完成后要按「已生成即展开」补一次自动展开")

    def test_batch_completion_still_auto_opens(self):
        block = block_from(self.app, "async function runAllPartProcessesInBackground(")
        self.assertTrue(block, "找不到 runAllPartProcessesInBackground()")
        self.assertIn("autoOpenGeneratedProcess(", block,
                      "批量生成完成后要对当前零件补一次自动展开")

    def test_auto_open_still_reuses_probe_and_entry_with_dedupe(self):
        block = block_from(self.app, "function autoOpenGeneratedProcess(")
        self.assertTrue(block, "找不到 autoOpenGeneratedProcess()")
        for token in ("partHasExistingProcess(", "openPartAnalysis(",
                      "autoOpenedProcessParts.has(", "autoOpenedProcessParts.add("):
            with self.subTest(token=token):
                self.assertIn(token, block, "自动展开的既有实现被改动：" + token)
        for token in ("fetch(", "/generate", "document.createElement"):
            with self.subTest(token=token):
                self.assertNotIn(token, block, "自动展开不得新增请求 / 生成 / 面板：" + token)

    def test_existing_process_probe_stays_read_only(self):
        block = block_from(self.app, "async function partHasExistingProcess(")
        self.assertTrue(block, "找不到 partHasExistingProcess()")
        self.assertIn("/process", block)
        self.assertNotRegex(block, r'method:\s*"POST"', "判定必须继续是只读 GET")


class CapabilitiesNotReduced(unittest.TestCase):
    """R4：视图注册、父子关系、后端路由与父壳边界都不缩水。"""

    @classmethod
    def setUpClass(cls):
        cls.app = read(APP_JS)
        cls.parent_html = read(PARENT_HTML)
        cls.main = read(MAIN_PY)

    def test_part_flow_views_keep_registration_and_parents(self):
        for token in ('const PART_VIEW_PARENT = {', "const PART_FLOW_VIEWS = [",
                      "window.TechBoardPartViews = {", '"part-process"', '"part-cost"',
                      '"part-detail": "parts-list"', '"part-process": "part-detail"'):
            with self.subTest(token=token):
                self.assertIn(token, self.app, "零件层级视图契约被删：" + token)

    def test_process_and_cost_views_still_render_through_analysis(self):
        self.assertIn('const PART_VIEW_FOR = {', self.app)
        self.assertIn('renderPartAnalysis(part, mode)', self.app,
                      "part-process / part-cost 必须继续复用既有分析渲染")
        self.assertIn("/parts/${encodeURIComponent(part.part_id)}/process", self.app,
                      "单件工艺推荐接口被删")

    def test_parent_shell_still_hosts_no_part_detail_modal(self):
        for token in ("part-detail", "part-process", "part-cost"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.parent_html,
                                 "父壳不得承载零件层级视图：" + token)


if __name__ == "__main__":
    unittest.main()
