"""红测：2.1 零件详情/工艺推荐面板收口 + 「更多功能 / 任务文件」弹卡片。

用户口径（九条原话见 Spec §「用户口径」）：

· U1/U2 工艺说明输入框与「选择补充文件」整行都不要（要说什么走左侧 Agent 输入框）；
· U3 最上面的「返回零件详情」重复，删；
· U4 「重新生成工艺推荐」「编辑」移到右上角「返回零件详情」左边，且生成按钮改成同款样式；
· U5 「返回零件清单」不需要（零件清单一直显示）；
· U6 「更多功能 ▾」做成和「返回零件详情 / 选择补充文件 / 编辑」一样样式；
· U7 「导入已有 3D 模型」「版本校核审签」置灰点不了；
· U8 这些内容不要在零件详情里显示，改成「更多功能」点了弹卡片；
· U9 左侧「任务文件」也不要占工作区，像设置模型一样弹卡片。

现状（实测，非推断）：

· `inline-analysis.js:76` 的 `.inline-analysis-inputs` 里就是说明 textarea + 「选择补充文件」；
· 两颗「返回零件详情」= `index.html:201` 的 `#btnBackToModel`（上）与 `inline-analysis.js:73` 的
  `data-inline-close`（右上）；`#btnBoardBackList`（`index.html:188`）由 `app.js:2974-2977` 显示；
· `#btnMoreActions` 用 `workbench.css:161` 的大号 `.report-btn`，而「编辑」是 `.inline-action`（11px）；
· `#btnMoreImport3d` / `#btnMoreReview` 只在 `app.js:885-892`（解析成功那一刻）启用，
  `openProject()`（`app.js:1435-1440`）从不启用 → 打开已有项目后永远置灰（用户看到的现象）；
· `#secVersions` 被 `selectPart()`（`app.js:2090-2106`）搬进零件详情；`import3d` / `review` / `files`
  三个视图（`app.js:2763-2768`）渲染进工作区宿主 `#boardViewHost`。

Spec：docs/specs/tech-part-detail-chrome-and-action-cards.md
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
INDEX = FRONTEND / "index.html"
APP_JS = FRONTEND / "app.js"
INLINE_JS = FRONTEND / "inline-analysis.js"
INLINE_CSS = FRONTEND / "inline-analysis.css"
WORKBENCH_CSS = FRONTEND / "workbench.css"
SPEC = ROOT / "docs" / "specs" / "tech-part-detail-chrome-and-action-cards.md"

# 本批必须被提升的缓存号（改前实测值）——不刷号线上会命中旧缓存。
OLD_VERSIONS = {
    "workbench.css": "20260916-renderfix1",
    "inline-analysis.css": "20260917-font1",
    "inline-analysis.js": "20260916-cad1",
    "app.js": "20260916-renderfix1",
}


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def js_function(text: str, name: str) -> str:
    """按大括号配对取出 `function name(...) {...}` 的完整块（含函数名）。"""
    marker = "function %s(" % name
    start = text.find(marker)
    if start < 0:
        return ""
    brace = text.find("{", start)
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
        if ch == "/" and nxt == "*":
            j = text.find("*/", i)
            i = len(text) if j < 0 else j + 2
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return ""


def button_tag(src: str, hook: str) -> str:
    match = re.search(r"<button[^>]*%s[^>]*>" % re.escape(hook), src)
    return match.group(0) if match else ""


def button_classes(src: str, hook: str) -> set:
    tag = button_tag(src, hook)
    if not tag:
        return set()
    found = re.search(r'class="([^"]*)"', tag)
    return set((found.group(1) if found else "").split())


def css_version(html: str, asset: str) -> str:
    match = re.search(re.escape(asset) + r"\?v=([^\"'&]+)", html)
    return match.group(1) if match else ""


# --------------------------------------------------------------------------- #
# 契约钉死
# --------------------------------------------------------------------------- #
class SpecPinnedTest(unittest.TestCase):
    def test_spec_pins_every_contract(self):
        text = read(SPEC)
        for anchor in ("inline-action", "boardCardMask", "boardCardBody", "board-card-mask",
                       "syncActionSheet", "openBoardCard", "closeBoardCard", "card: true",
                       "partDetailVersions", "一律不再出现", "data-inline-note",
                       "inline-analysis-actions", "board-card-mask[hidden]"):
            self.assertIn(anchor, text, "Spec 缺少契约锚点：%s" % anchor)


# --------------------------------------------------------------------------- #
# 契约 A/B：面板头部与输入区、2.1 头部按钮
# --------------------------------------------------------------------------- #
class PanelChromeRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read(INLINE_JS)

    def test_note_and_attachment_inputs_are_gone(self):
        for token in ("data-inline-note", "data-inline-files", "inline-file-picker", "notePlaceholder"):
            self.assertNotIn(token, self.js,
                             "工艺说明/附件输入区还在（%s）：说明要说什么走左侧 Agent 输入框" % token)

    def test_quantity_stays_for_cost_mode(self):
        self.assertIn("data-inline-quantity", self.js,
                      "成本测算的「批量」不是说明输入，必须保留")

    def test_generate_and_edit_move_left_of_the_close_button(self):
        gen = self.js.find("data-inline-generate")
        edit = self.js.find("data-inline-edit")
        save = self.js.find("data-inline-save")
        close = self.js.find("data-inline-close")
        self.assertGreater(gen, 0, "找不到生成按钮")
        self.assertGreater(edit, 0, "找不到编辑按钮")
        self.assertGreater(save, 0, "找不到保存按钮")
        self.assertGreater(close, 0, "找不到返回零件详情按钮")
        self.assertLess(gen, edit, "生成按钮必须排在编辑左边")
        self.assertLess(edit, save, "保存必须跟在编辑后面（同一组）")
        self.assertLess(save, close, "生成/编辑/保存必须整体排在「返回零件详情」左边")

    def test_all_four_buttons_share_one_style(self):
        for hook in ("data-inline-generate", "data-inline-edit", "data-inline-save", "data-inline-close"):
            classes = button_classes(self.js, hook)
            self.assertIn("inline-action", classes,
                          "%s 没有用统一的小号描边按钮样式：%s" % (hook, sorted(classes)))
        generate = button_classes(self.js, "data-inline-generate")
        for heavy in ("primary", "start-parse-btn"):
            self.assertNotIn(heavy, generate, "生成按钮还在用重样式：%s" % heavy)

    def test_no_second_action_row(self):
        # 只判「独立容器类名」，包一层 .inline-head-actions 是允许的。
        found = re.search(r'class="[^"]*\binline-analysis-actions\b[^"]*"', self.js)
        self.assertIsNone(found,
                          "按钮已合并进头部一行，不该再有第二个按钮行：%s"
                          % (found.group(0) if found else ""))

    def test_extra_form_gone_with_the_inputs(self):
        self.assertNotIn("function extraForm", self.js, "extraForm() 随说明/附件输入一起删除")


class CssRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inline_css = read(INLINE_CSS)
        cls.workbench_css = read(WORKBENCH_CSS)
        cls.index = read(INDEX)

    def test_file_picker_rules_removed(self):
        self.assertNotIn(".inline-file-picker", self.inline_css,
                         "「选择补充文件」整行都不要了，它的样式规则也该删")

    def test_inputs_row_is_single_column_for_cost_only(self):
        found = re.search(r"\.inline-analysis-inputs\s*\{([^}]*)\}", self.inline_css)
        self.assertTrue(found, "找不到 .inline-analysis-inputs 规则")
        body = found.group(1)
        self.assertNotIn("auto auto", body,
                         "说明与附件删了，输入行不再需要三列网格：%s" % body)

    def test_card_shell_styles_added(self):
        for selector in (".board-card-mask", ".board-card-body", ".board-card-close"):
            self.assertIn(selector, self.workbench_css, "缺少弹卡片样式：%s" % selector)
        self.assertIn(".board-card-mask[hidden]", self.workbench_css,
                      "遮罩要靠 [hidden] 隐藏，必须显式写 display:none")

    def test_report_btn_global_rule_untouched(self):
        self.assertIn("600 13px", self.workbench_css,
                      ".report-btn 的全局规则不能为了本批被改（其它页在用）")

    def test_assets_are_cachebusted(self):
        for asset, old in OLD_VERSIONS.items():
            self.assertNotEqual(old, css_version(self.index, asset),
                                "%s 的 ?v= 没有提升，线上会命中旧缓存" % asset)


class HeaderButtonRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = read(INDEX)
        cls.app = read(APP_JS)

    def test_duplicate_back_button_and_its_bar_are_gone(self):
        self.assertNotIn("btnBackToModel", self.index, "重复的「返回零件详情」还在")
        self.assertNotIn("analysis-panel-bar", self.index, "那一整条按钮栏都该删")

    def test_parts_list_button_is_gone(self):
        self.assertNotIn("btnBoardBackList", self.index,
                         "零件清单一直显示，「返回零件清单」不再需要")
        self.assertNotIn("btnBoardBackList", self.app,
                         "节点删了，绑定与显示同步也必须删")

    def test_more_actions_uses_the_small_outline_style(self):
        classes = button_classes(self.index, 'id="btnMoreActions"')
        self.assertIn("inline-action", classes,
                      "更多功能 ▾ 要和「编辑」「返回零件详情」同款：%s" % sorted(classes))
        self.assertNotIn("report-btn", classes,
                         "更多功能 ▾ 还在用更重的大号按钮样式：%s" % sorted(classes))

    def test_back_to_model_binding_removed_from_app(self):
        self.assertNotIn("BACK_TO_PART_DETAIL", self.app, "常量应随按钮一起删除")
        self.assertNotIn("BACK_TO_PARTS_LIST", self.app, "常量应随按钮一起删除")


# --------------------------------------------------------------------------- #
# 契约 C：更多功能里两颗按钮的置灰是缺陷
# --------------------------------------------------------------------------- #
class ActionSheetGateRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = read(APP_JS)

    def test_single_shared_gate_function_exists(self):
        body = js_function(self.app, "syncActionSheet")
        self.assertTrue(body, "缺少统一判定函数 syncActionSheet()")
        for button in ("btnMoreImport3d", "btnMoreReview"):
            self.assertIn(button, body, "syncActionSheet() 没有接管 %s" % button)
        self.assertIn("currentProject", body,
                      "这两颗只依赖「有项目」，不该再绑在解析成功那一刻")

    def test_both_entry_points_share_the_gate(self):
        parse_body = js_function(self.app, "parseDrawing")
        open_body = js_function(self.app, "openProject")
        self.assertTrue(parse_body, "找不到 parseDrawing()")
        self.assertTrue(open_body, "找不到 openProject()")
        self.assertIn("syncActionSheet", parse_body, "解析成功后没有走统一判定")
        self.assertIn("syncActionSheet", open_body,
                      "打开已有项目后没有走统一判定 —— 这正是「置灰点不了」的根因")

    def test_old_duplicated_enable_lines_are_gone(self):
        parse_body = js_function(self.app, "parseDrawing")
        self.assertNotIn('$("btnMoreImport3d").disabled = false', parse_body,
                         "旧的散落启用语句应被 syncActionSheet() 取代")


# --------------------------------------------------------------------------- #
# 契约 D/E：三处改弹卡片、零件详情不再内嵌版本面板
# --------------------------------------------------------------------------- #
class ActionCardsRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = read(INDEX)
        cls.app = read(APP_JS)
        cls.workbench_css = read(WORKBENCH_CSS)

    def test_card_shell_dom_exists(self):
        for node in ('id="boardCardMask"', 'id="boardCard"', 'id="boardCardTitle"',
                     'id="boardCardBody"', 'id="boardCardClose"'):
            self.assertIn(node, self.index, "缺少卡片节点：%s" % node)
        card = re.search(r"<[^>]*id=\"boardCard\"[^>]*>", self.index)
        self.assertTrue(card, "找不到 #boardCard 节点")
        tag = card.group(0)
        self.assertIn('role="dialog"', tag, "卡片要有 dialog 语义：%s" % tag)
        self.assertIn('aria-modal="true"', tag, "卡片要有 aria-modal：%s" % tag)

    def test_three_views_become_cards(self):
        specs = self.app
        for view in ("import3d", "review", "files"):
            match = re.search(r"\b%s\s*:\s*\{[^}]*\}" % re.escape(view), specs)
            self.assertTrue(match, "BOARD_VIEW_SPECS 里找不到 %s" % view)
            self.assertIn("card: true", match.group(0),
                          "%s 还没改成卡片呈现：%s" % (view, match.group(0)))

    def test_card_branch_keeps_the_workspace_alone(self):
        open_card = js_function(self.app, "openBoardCard")
        close_card = js_function(self.app, "closeBoardCard")
        self.assertTrue(open_card, "缺少 openBoardCard()")
        self.assertTrue(close_card, "缺少 closeBoardCard()")
        self.assertIn("boardCardBody", open_card, "卡片没把内容渲染进 #boardCardBody")
        self.assertIn("boardCardMask", close_card, "关闭时没有收起遮罩")
        self.assertNotIn("boardViewHost", open_card,
                         "卡片视图不该再占用工作区宿主")

    def test_every_close_path_goes_through_one_function(self):
        close_view = js_function(self.app, "closeBoardView")
        self.assertTrue(close_view, "找不到 closeBoardView()")
        self.assertIn("closeBoardCard", close_view, "closeBoardView() 也要能关掉卡片")
        self.assertIn("Escape", self.app, "卡片要支持 Esc 关闭")

    def test_part_detail_no_longer_embeds_versions(self):
        select = js_function(self.app, "selectPart")
        self.assertTrue(select, "找不到 selectPart()")
        self.assertNotIn("partDetailVersions", self.app, "零件详情里的版本槽位应删除")
        self.assertNotIn("secVersions", select, "零件详情不再搬运版本面板")

    def test_task_files_renderer_is_reused_not_copied(self):
        self.assertEqual(1, self.app.count("function renderBoardFiles"),
                         "任务文件渲染只保留一份 renderBoardFiles()")
        self.assertIn("renderBoardFiles", js_function(self.app, "openBoardCard"),
                      "卡片视图要复用既有任务文件渲染，不另写一份")


if __name__ == "__main__":
    unittest.main(verbosity=2)
