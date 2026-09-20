"""红测：技术工艺步骤状态行去掉，文本原样并入统一工作台标题行。

现状缺口：
  · 阶段页各自渲染一行步骤状态（2.1 的 `#status`、2.2 的 `#status` + `#aiPanelStatus`、
    2.3 的 `#status` + `#crStatus`、1.1/1.2/1.3 模板里的 `.status-badge`），
    嵌入态下 `.form-title` 被隐藏后它变成标题行下方单独的一行；
  · 父壳标题行 `#techContextHeader` 只有 `#techContextTitle` / `#techContextNotice` /
    `#techSubstepsBar`，没有任何通道把阶段页的状态文字送进来；
  · 看板 → 父壳的标准状态事件白名单里没有承载步骤状态的事件。

本批只改看板状态通道、阶段页上报与嵌入态隐藏；不改状态文案、九阶段流程与业务数据。
不联网、不起服务、不读真实业务数据；只做静态契约校验。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = "tech_app/frontend/"
RUNTIME = ROOT / (F + "tech-board-runtime.js")
BRIDGE = ROOT / (F + "tech-board-bridge.js")
WB_JS = ROOT / (F + "tech-workbench.js")
WB_HTML = ROOT / (F + "tech-workbench.html")
EMBED = ROOT / (F + "tech-embed.js")
APP_JS = ROOT / (F + "app.js")
ASSEMBLY_JS = ROOT / (F + "assembly-integration.js")
COST_JS = ROOT / (F + "cost-review.js")
INDEX_HTML = ROOT / (F + "index.html")
ASSEMBLY_HTML = ROOT / (F + "assembly-integration.html")
COST_HTML = ROOT / (F + "cost-review.html")
REQ_SCRIPTS = [
    ROOT / (F + "requirement-create.js"),
    ROOT / (F + "requirement-confirm-page.js"),
    ROOT / (F + "requirement-review-page.js"),
]

EXISTING_EVENTS = (
    "ready",
    "action-state",
    "task-progress",
    "task-completed",
    "task-failed",
    "selection-changed",
)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
    """返回 marker 之后第一对成对花括号（含花括号）的源码，跳过字符串与注释。"""
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
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
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
                return text[brace : i + 1]
        i += 1
    return ""


class TechStepStatusInContextRowRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = read(RUNTIME)
        cls.bridge = read(BRIDGE)
        cls.wb_js = read(WB_JS)
        cls.wb_html = read(WB_HTML)
        cls.embed = read(EMBED)
        cls.app_js = read(APP_JS)
        cls.assembly_js = read(ASSEMBLY_JS)
        cls.cost_js = read(COST_JS)

    # ------------------------------------------------------------ 协议
    def test_runtime_defines_board_status_event_and_publish_helper(self):
        self.assertIn(
            "BOARD_STATUS: 'board-status'",
            self.runtime,
            "看板运行时必须登记 board-status 事件名",
        )
        self.assertIn("board-status", self.runtime.split("window.TechBoardRuntime")[0])
        self.assertIn("function publishStatus", self.runtime, "运行时必须导出 publishStatus 助手")
        api = block_from(self.runtime, "var api =")
        self.assertIn("publishStatus", api, "publishStatus 必须在 TechBoardRuntime 上暴露")

    def test_parent_bridge_whitelists_board_status(self):
        self.assertIn(
            "BOARD_STATUS: 'board-status'",
            self.bridge,
            "父壳桥必须把 board-status 加进状态事件白名单，否则消息会被丢弃",
        )

    # ------------------------------------------------------------ 父壳标题行
    def test_parent_renders_board_status_in_context_title_row(self):
        self.assertRegex(
            self.wb_js,
            r"board-status",
            "父壳必须订阅 board-status 事件",
        )
        handler = block_from(self.wb_js, "function setBoardNotice")
        self.assertIn("techContextNotice", self.wb_html)
        self.assertIn("techContextNotice", handler, "提示位仍必须写在标题行的 #techContextNotice")
        self.assertRegex(
            self.wb_js,
            r"payload[\s\S]{0,80}\.text|\.text[\s\S]{0,80}payload",
            "父壳必须原样取 payload.text 渲染，不做二次改写",
        )

    def test_parent_keeps_error_precedence_and_resets_on_stage_switch(self):
        self.assertIn("boardStatus", self.wb_js, "父壳必须保存最近一次看板状态，供提示清除后回落")
        self.assertRegex(
            self.wb_js,
            r"function setBoardNotice[\s\S]{0,600}boardStatus",
            "setBoardNotice 清除提示时必须回落到保存的看板状态，而不是留空",
        )
        self.assertRegex(
            self.wb_js,
            r"function mountStageFrame[\s\S]{0,2500}boardStatus",
            "重新挂载 iframe（切换 stage）时必须丢弃上一步的状态文本",
        )

    # ------------------------------------------------------------ 阶段页上报
    def test_stage_pages_publish_step_status_verbatim(self):
        cases = (
            ("2.1 status()", self.app_js, "const status = (msg, busy = false) =>"),
            ("2.2 aiStatus()", self.assembly_js, "function aiStatus(message, error = false)"),
            ("2.3 crStatus()", self.cost_js, "function crStatus(message, error = false)"),
        )
        for label, source, marker in cases:
            body = block_from(source, marker)
            with self.subTest(case=label):
                self.assertTrue(body, "找不到 %s 的实现" % label)
                self.assertIn(
                    "publishStatus",
                    body,
                    "%s 必须把同一段状态文字原样上报给父壳" % label,
                )

    def test_requirement_pages_publish_rendered_status_badge(self):
        for path in REQ_SCRIPTS:
            source = read(path)
            with self.subTest(script=path.name):
                self.assertIn(
                    "publishStatus",
                    source,
                    "%s 必须上报模板里渲染出的状态徽标文本" % path.name,
                )

    # ------------------------------------------------------------ 嵌入态隐藏
    def test_child_status_rows_hidden_only_in_embed_mode(self):
        self.assertIn(".title-row .status-badge", self.embed, "嵌入态必须隐藏阶段页的状态徽标")
        self.assertIn(".ai-status", self.embed, "嵌入态必须隐藏 2.2/2.3 的重复 ai-status 行")
        for rule in re.findall(r"[^\n;{}]*\.(?:title-row \.status-badge|ai-status)\s*\{[^}]*\}", self.embed):
            with self.subTest(rule=rule.strip()):
                self.assertRegex(
                    rule,
                    r"\.tech-embed\s+",
                    "隐藏规则必须限定在 .tech-embed 作用域，不能影响独立打开阶段页",
                )
        global_hide = re.search(
            r"(?<!tech-embed )(?<!tech-embed )\.(?:ai-status|title-row \.status-badge|status)\s*\{[^}]*display\s*:\s*none",
            self.embed,
        )
        self.assertIsNone(global_hide, "不允许写不带 .tech-embed 前缀的全局隐藏规则")

    # ------------------------------------------------------------ 保留守卫
    def test_context_title_row_keeps_title_notice_and_substeps(self):
        header = re.search(r'id="techContextHeader"[^>]*>(.*?)</div>\s*<div id="techWorkspaceOutlet"',
                           self.wb_html, re.S)
        self.assertIsNotNone(header, "找不到 #techContextHeader")
        body = header.group(1)
        for node in ("techContextTitle", "techContextNotice", "techSubstepsBar"):
            with self.subTest(node=node):
                self.assertIn('id="%s"' % node, body)

    def test_existing_state_events_unchanged(self):
        for name in EXISTING_EVENTS:
            with self.subTest(event=name):
                self.assertIn("'%s'" % name, self.bridge)
                self.assertIn("'%s'" % name, self.runtime)

    def test_status_texts_unchanged(self):
        self.assertIn(
            '`已打开项目 ${pid}（补充说明${note ? "✓" : "—"}，佐证文件 ${atts} 个）`',
            self.app_js,
            "2.1 的状态文案不得改动，必须原样搬到标题行",
        )
        self.assertRegex(
            read(INDEX_HTML),
            r'id="status"[^>]*>等待上传图纸<',
            "2.1 状态徽标默认文案不得改动",
        )
        for path in (ASSEMBLY_HTML, COST_HTML):
            with self.subTest(page=path.name):
                self.assertRegex(read(path), r'id="status"[^>]*>读取中…<')


if __name__ == "__main__":
    unittest.main()
