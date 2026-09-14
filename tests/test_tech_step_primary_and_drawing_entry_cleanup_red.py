"""红测：1.1 主按钮随解析状态切换、2.1 能力入口归位「更多功能」并自动生成 3D/2D。

现状缺口（实测）：
  · `requirement-create.js:257-268`：`submitRequirement` 写死 `role: 'primary'`、`order: 10`，
    `extractRequirement` 写死 `role: 'aux'`、`order: 30` —— 1.1 第一眼的主按钮是「提交确认」，
    而这一步真正的起点是「一键解析需求」；解析完成后主按钮才该交给「提交确认」。
    对照 2.2 的 `runIntegration` / `sendToFinance`（`assembly-integration.js:1498-1517`）
    是 `getState()` 动态反转 role —— 1.1 缺的正是这一份。
  · `tech-workbench.html:113-117` 左侧会话栏又排了五项 2.1 能力按钮，其中「联网核验」「校验修正」
    与 `index.html:179` 的 `#actionSheet`（更多功能 ▾）完全重复；而「导入已有 3D 模型」
    「版本与校核审查」反倒不在「更多功能」里。
  · `app.js:861-906` 的 `parseDrawing()` 解析成功后只把 `#btnGenerate` / `#btnDrawings`
    解锁，用户还得进「更多功能」点两次才有 3D 与 2D。

本批只做「主按钮归位 + 入口去重 + 解析后自动生成」；后端接口、既有节点与既有视图一律不动。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
RC = F / "requirement-create.js"
APP = F / "app.js"
INDEX = F / "index.html"
WB_HTML = F / "tech-workbench.html"
WB_JS = F / "tech-workbench.js"
WB_CSS = F / "tech-workbench.css"
CHAT = F / "agent-chat.js"
RUNTIME = F / "tech-board-runtime.js"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 「更多功能 ▾」菜单本次必须同时具备的能力文案。
MORE_MENU_ITEMS = (
    "联网核验", "校验修正", "拆解推荐", "生成 CAD 几何", "生成 2D 工程图",
    "导出 BOM（CSV）", "导入已有 3D 模型", "版本与校核审查",
)
# 左侧会话栏本次必须交还给「更多功能」的四项能力。
# 契约更新（2.1 左侧按钮清理批次）：左侧会话栏不再保留任何静态能力按钮，
# 「解析视图（evidence）」与其余四项一起归位到 2.1 页内的「更多功能 ▾」/ 看板内部视图。
RETIRED_CAPABILITIES = ("import3d", "review", "modelLookup", "verify", "evidence")


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
                    i += 1
                    break
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


def html_block(text: str, marker: str) -> str:
    """从 marker 起截到最近的 </div>：菜单这类扁平容器用得上。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    end = text.find("</div>", idx)
    return text[idx:end] if end > 0 else text[idx:]


def rule(text: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{", text)
    if not match:
        return ""
    start = text.find("{", match.start())
    end = text.find("}", start)
    return text[start:end + 1] if end > 0 else ""


class TechStepPrimaryAndDrawingEntryCleanupRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rc = read(RC)
        cls.app = read(APP)
        cls.index = read(INDEX)
        cls.wb_html = read(WB_HTML)
        cls.wb_js = read(WB_JS)
        cls.wb_css = read(WB_CSS)
        cls.chat = read(CHAT)
        cls.runtime = read(RUNTIME)
        cls.main = read(MAIN)

    # ------------------------------------------------ 契约 A：1.1 主按钮动态反转
    def _assert_dynamic_role(self, block, label):
        self.assertTrue(block, f"找不到 {label} 动作")
        self.assertNotRegex(block, r"role:\s*['\"](?:aux|primary)['\"]",
                            f"{label} 的 role 不能再写死")
        self.assertRegex(block, r"role:\s*[\s\S]{0,120}?\?",
                         f"{label} 的 role 必须按当前状态动态给出")
        for value in ("'primary'", "'aux'"):
            self.assertIn(value, block, f"{label} 的 role 必须同时能取到 {value}")

    def test_extract_requirement_role_is_dynamic(self):
        self._assert_dynamic_role(block_from(self.rc, "extractRequirement:"), "一键解析需求")

    def test_submit_requirement_role_is_dynamic(self):
        self._assert_dynamic_role(block_from(self.rc, "submitRequirement:"), "提交确认")

    def test_primary_is_swapped_at_the_parse_boundary(self):
        extract = block_from(self.rc, "extractRequirement:")
        submit = block_from(self.rc, "submitRequirement:")
        both = extract + submit
        self.assertIn("primary", both, "两个动作必须有一个成为主按钮")
        # 解析前：解析是主；解析后：提交是主 —— 用同一个判定，不允许各写一份。
        extract_ids = set(re.findall(r"\brc[A-Za-z]+\(\)", extract))
        submit_ids = set(re.findall(r"\brc[A-Za-z]+\(\)", submit))
        self.assertTrue(extract_ids & submit_ids,
                        "两处必须共用同一个「是否已解析」判定，否则会两处漂移")
        self.assertRegex(block_from(self.rc, "extractRequirement:"), r"order:\s*10",
                         "解析入口在前（order 10）")
        self.assertRegex(block_from(self.rc, "submitRequirement:"), r"order:\s*20",
                         "提交确认紧随其后（order 20）")

    def test_requirement_actions_declare_enabled_true(self):
        for marker in ("extractRequirement:", "submitRequirement:", "saveRequirementDraft:"):
            with self.subTest(action=marker):
                block = block_from(self.rc, marker)
                self.assertTrue(block, f"找不到 {marker}")
                self.assertRegex(block, r"enabled:\s*true",
                                 "前置条件不灰按钮：忙闲只由 busy 表达")
                self.assertIn("busy:", block, "busy 语义必须保留")

    def test_requirement_flows_are_untouched(self):
        for token in ("/requirement/extract-documents", "submit-confirmation",
                      "saveRequirementDraft", "extractRequirement", "submitRequirement"):
            with self.subTest(token=token):
                self.assertIn(token, self.rc, f"1.1 既有流程不得被改动：{token}")

    def test_primary_button_style_is_the_shared_solid_one(self):
        body = block_from(self.wb_js, "function syncChatActions(")
        self.assertRegex(body, r"variant:\s*'primary'",
                         "主按钮槽位必须走既有 primary 变体")
        style = rule(self.wb_css, ".tech-chat-actions > button.primary")
        self.assertTrue(style, "缺少主按钮样式")
        self.assertRegex(style, r"(--color-primary|gradient)",
                         "主按钮必须与「开始整合分析」同为系统主色实心")

    # ------------------------------------------------ 契约 B：2.1 入口归位
    def test_workbench_toolbar_drops_duplicated_capabilities(self):
        for name in RETIRED_CAPABILITIES:
            with self.subTest(capability=name):
                self.assertNotIn(f'data-tech-capability="{name}"', self.wb_html,
                                 "这项能力已归位「更多功能」/ 看板内部，左侧栏不得重复")

    def test_more_menu_has_all_capabilities(self):
        sheet = html_block(self.index, 'id="actionSheet"')
        self.assertTrue(sheet, "找不到「更多功能 ▾」菜单 #actionSheet")
        for label in MORE_MENU_ITEMS:
            with self.subTest(label=label):
                self.assertIn(label, sheet, f"「更多功能」缺少入口：{label}")

    def test_new_more_menu_entries_reuse_existing_views(self):
        self.assertRegex(self.app,
                         r'\$\("btnMoreImport3d"\)\.onclick[\s\S]{0,200}?runBoardView\(\s*["\']import3d["\']',
                         "「导入已有 3D 模型」必须复用既有看板视图 import3d")
        self.assertRegex(self.app,
                         r'\$\("btnMoreReview"\)\.onclick[\s\S]{0,200}?runBoardView\(\s*["\']review["\']',
                         "「版本与校核审查」必须复用既有看板视图 review")

    def test_existing_drawing_entries_are_kept(self):
        for token in ('id="secImport3d"', 'id="file3d"', 'id="btnImport3d"',
                      'id="secVersions"', 'data-open-drawer="import3d"',
                      'data-open-drawer="review"'):
            with self.subTest(token=token):
                self.assertIn(token, self.index, f"2.1 既有入口不得删除：{token}")
        for key in ("import3d:", "review:"):
            with self.subTest(key=key):
                self.assertIn(key, self.app, f"既有看板视图 {key} 不得删除")
        views = block_from(self.app, "window.TechBoardRuntime.registerViews(")
        for key in ("import3d:", "review:"):
            with self.subTest(view=key):
                self.assertIn(key, views, f"既有视图注册 {key} 不得删除")

    # ------------------------------------------------ 契约 C：解析后自动生成 3D / 2D
    def test_generate_handlers_are_named_functions(self):
        for name in ("generateGeometry", "generateDrawings"):
            with self.subTest(name=name):
                self.assertRegex(self.app, rf"async function {name}\s*\(",
                                 f"{name}() 必须抽成具名函数，按钮与自动流程共用")
        self.assertRegex(self.app, r'\$\("btnGenerate"\)\.onclick\s*=\s*[^\n;]*generateGeometry',
                         "#btnGenerate 必须复用同一份实现")
        self.assertRegex(self.app, r'\$\("btnDrawings"\)\.onclick\s*=\s*[^\n;]*generateDrawings',
                         "#btnDrawings 必须复用同一份实现")

    def test_generate_functions_report_success_and_failure(self):
        for name in ("generateGeometry", "generateDrawings"):
            body = block_from(self.app, f"function {name}(")
            self.assertTrue(body, f"找不到 {name}()")
            self.assertRegex(body, r"return\s+(?:true|false|{[\s\S]{0,40}?ok\s*:)",
                             f"{name}() 必须回执成功 / 失败，自动流程才知道该不该继续")
            self.assertIn("status(", body, f"{name}() 必须把结果写进状态行")

    def test_parse_triggers_automatic_generation(self):
        helper = block_from(self.app, "function autoGenerateAfterParse(")
        self.assertTrue(helper, "必须新增 autoGenerateAfterParse() 作为唯一自动入口")
        self.assertIn("currentIsImg", helper, "3D 导入项目跳过自动生成")
        self.assertIn("currentIR", helper, "解析没结果就不该触发")
        self.assertIn("generateGeometry(", helper, "自动流程必须先跑几何生成")
        self.assertIn("generateDrawings(", helper, "自动流程必须接着跑 2D 工程图")
        geo = helper.index("generateGeometry(")
        drw = helper.index("generateDrawings(")
        self.assertLess(geo, drw, "必须先生成 3D 再生成 2D")
        self.assertRegex(helper[geo:drw], r"if\s*\(",
                         "几何失败必须挡住 2D：先判断几何结果再继续")
        self.assertRegex(helper, r"失败|error|ok:\s*false",
                         "失败必须给出真实原因，不得静默")
        parse = block_from(self.app, "async function parseDrawing(")
        self.assertIn("autoGenerateAfterParse(", parse,
                      "解析成功后必须自动生成 3D 与 2D，不必再进「更多功能」点两次")

    def test_drawing_endpoints_are_untouched(self):
        for token in ("/api/projects/3d", "/generate", "/drawings", "/decompose", "/parse"):
            with self.subTest(token=token):
                self.assertIn(token, self.app, f"既有接口调用不得改动：{token}")
        for route in ('/api/projects/3d', '/api/projects/{project_id}/generate',
                      '/api/projects/{project_id}/drawings',
                      '/api/projects/{project_id}/decompose'):
            with self.subTest(route=route):
                self.assertIn(route, self.main, f"后端路由不得减少：{route}")

    # ------------------------------------------------ 保护边界
    def test_standalone_drawing_menu_keeps_its_dispatch(self):
        for token in ("DRAWING_ACTION_CAPABILITIES", "dispatchDrawingCapability",
                      "capability:evidence"):
            with self.subTest(token=token):
                self.assertIn(token, self.chat,
                              f"独立 2.1 页的 ＋ 能力菜单仍在使用这套分派：{token}")

    def test_protocol_is_untouched(self):
        for field in ("label", "visible", "enabled", "busy", "role", "order", "hint"):
            with self.subTest(field=field):
                self.assertIn(field + ":", block_from(self.runtime, "function entryState("),
                              f"entryState 字段契约不得改动：{field}")


if __name__ == "__main__":
    unittest.main()
