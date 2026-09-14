"""红测：标题行步骤状态恢复蓝底蓝字胶囊，红色只留给失败提示。

现状缺口：
  · `tech-workbench.css:531-539` 的 `.tech-context-notice` 只有 `color: var(--color-red, #d92d20)`，
    没有背景与圆角 —— 于是 `已打开项目 …（补充说明✓，佐证文件 0 个）`、`就绪`、`本步已确认`
    这些正常步骤状态在父壳里全部变成了红字；
  · `tech-workbench.js` 的 `board-status` 分支只搬 `payload.text`，**丢掉 `payload.level`**，
    `setBoardNotice(message)` 也没有等级参数，失败提示与正常状态共用一个红色样式；
  · 页内独立打开时的状态是 `workbench.css:17` 的蓝底蓝字 `.status-badge`（`--color-primary-light`
    + `--color-primary` + 全圆角），两者视觉不一致。

本批只改父壳提示位的样式与 level 接线；不改协议、不改阶段页页内状态行、不改文案。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
WB_CSS = F / "tech-workbench.css"
WB_JS = F / "tech-workbench.js"
WB_HTML = F / "tech-workbench.html"
RUNTIME = F / "tech-board-runtime.js"
BRIDGE = F / "tech-board-bridge.js"
EMBED = F / "tech-embed.js"
WORKBENCH_CSS = F / "workbench.css"

RED_TOKENS = ("var(--color-red", "#d92d20", "#b91c1c", "#ef4444")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def rule(text: str, selector: str) -> str:
    """返回单个 CSS 选择器的规则体（选择器后必须紧跟 `{`，避免前缀误命中）。"""
    match = re.search(re.escape(selector) + r"\s*\{", text)
    if not match:
        return ""
    start = text.find("{", match.start())
    end = text.find("}", start)
    return text[start:end + 1] if end > 0 else ""


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


class TechStepStatusBluePillRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = read(WB_CSS)
        cls.js = read(WB_JS)
        cls.html = read(WB_HTML)
        cls.runtime = read(RUNTIME)
        cls.bridge = read(BRIDGE)
        cls.embed = read(EMBED)
        cls.workbench_css = read(WORKBENCH_CSS)

    # ------------------------------------------------------------ 样式：蓝底蓝字胶囊
    def test_notice_base_rule_is_blue_on_light_blue(self):
        body = rule(self.css, ".tech-context-notice")
        self.assertTrue(body, "找不到 .tech-context-notice 规则")
        self.assertRegex(body, r"background\s*:\s*[^;]*(--color-primary-light|#E6F0FD)",
                         "步骤状态必须是浅蓝底（与页内 .status-badge 同款）")
        self.assertRegex(body, r"color\s*:\s*[^;]*(--color-primary,\s*#0060E6|#0060E6)",
                         "步骤状态必须是主色蓝字")
        self.assertRegex(body, r"var\(--color-primary-light\s*,\s*#E6F0FD\)",
                         "父壳没有定义 --color-primary-light，必须带 hex 兜底")

    def test_notice_base_rule_has_no_red(self):
        body = rule(self.css, ".tech-context-notice")
        self.assertTrue(body, "找不到 .tech-context-notice 规则")
        for token in RED_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, body, "基础态不得再用红色 —— 红色只属于失败提示")

    def test_notice_is_capsule_with_ellipsis(self):
        body = rule(self.css, ".tech-context-notice")
        self.assertRegex(body, r"border-radius\s*:\s*(999px|var\(--radius-full\))",
                         "胶囊圆角缺失")
        self.assertRegex(body, r"padding\s*:", "胶囊需要内边距")
        self.assertIn("overflow: hidden", body, "长文案仍要能省略号收尾")
        self.assertIn("text-overflow: ellipsis", body, "长文案仍要能省略号收尾")

    def test_notice_does_not_stretch_across_the_row(self):
        body = rule(self.css, ".tech-context-notice")
        self.assertNotRegex(body, r"flex\s*:\s*1\s+1",
                            "胶囊不能拉满整条标题行，否则就是一条蓝色横幅而不是气泡")
        if "flex:" in body:
            self.assertRegex(body, r"flex\s*:\s*(0\s+1\s+auto|0\s+0\s+auto|none)",
                             "flex 只能是按内容收窄的取值")

    def test_error_is_a_separate_modifier(self):
        body = rule(self.css, ".tech-context-notice.is-error")
        self.assertTrue(body, "缺少 .tech-context-notice.is-error 失败态样式")
        self.assertRegex(body, r"color\s*:\s*[^;]*(--color-red|#d92d20|#b91c1c)",
                         "失败态必须是红字")
        self.assertRegex(body, r"background\s*:\s*[^;]*(--color-red-light|#fee2e2)",
                         "失败态需要浅红底，与蓝色胶囊区分开")

    def test_hidden_rule_is_kept(self):
        body = rule(self.css, ".tech-context-notice[hidden]")
        self.assertTrue(body, "缺少 .tech-context-notice[hidden] 规则")
        self.assertIn("display: none", body, "没有内容时必须整块隐藏")

    # ------------------------------------------------------------ 父壳：按 level 渲染
    def test_board_status_handler_reads_level(self):
        handler = block_from(self.js, "function bindBoardBridge(")
        self.assertTrue(handler, "找不到 bindBoardBridge()")
        self.assertIn("board-status", handler, "board-status 分支被改动")
        self.assertRegex(handler, r"payload[\s\S]{0,80}level|\.level\b",
                         "必须读取 payload.level，不能再把它丢掉")
        self.assertIn("payload.text", handler, "状态文案仍须原样搬运")

    def test_failure_notice_marks_error_level(self):
        handler = block_from(self.js, "function bindBoardBridge(")
        setter = block_from(self.js, "function setBoardNotice(")
        self.assertTrue(setter, "找不到 setBoardNotice()")
        self.assertRegex(handler, r"setBoardNotice\([^)]*['\"]error['\"]",
                         "桥的失败 / task-failed 提示必须显式标为 error")
        self.assertIn("is-error", setter, "setBoardNotice 必须切换失败态样式")
        self.assertRegex(setter, r"classList[\s\S]{0,60}is-error|is-error[\s\S]{0,60}classList",
                         "失败态用 class 切换，不要写死颜色")

    def test_set_board_notice_defaults_to_info(self):
        setter = block_from(self.js, "function setBoardNotice(")
        self.assertIn("state.boardNotice", setter, "失败提示优先显示的语义不能丢")
        self.assertIn("state.boardStatus", setter, "清除失败提示后必须回落到步骤状态")
        self.assertRegex(setter, r"level|Level", "必须按等级决定颜色")

    def test_apply_stage_resets_status_text_and_level(self):
        stage = block_from(self.js, "function applyStage(")
        self.assertTrue(stage, "找不到 applyStage()")
        self.assertRegex(stage, r"boardStatus\s*=\s*['\"]{2}",
                         "切步必须清掉上一步的状态文本")
        self.assertIn("Level", stage, "切步必须连等级一起清，别把 is-error 带到下一步")

    # ------------------------------------------------------------ 保护边界
    def test_parent_does_not_rewrite_business_copy(self):
        # 注释里提到文案是允许的，这里只查**字符串字面量**与拼接。
        for literal in ("已打开项目", "佐证文件"):
            with self.subTest(literal=literal):
                self.assertNotRegex(self.js, rf"['\"`]{literal}",
                                    "父壳不得把业务文案写成字面量（文案必须来自看板原文）")
        setter = block_from(self.js, "function setBoardNotice(")
        self.assertNotRegex(setter, r"textContent\s*=[^;]*\+",
                            "父壳不得给状态文案拼前缀或后缀")
        self.assertIn("techContextNotice", self.html, "提示位节点仍在标题行")

    def test_protocol_and_pages_are_untouched(self):
        status_fn = block_from(self.runtime, "function publishStatus(")
        self.assertTrue(status_fn, "publishStatus() 被删除")
        self.assertIn("'error'", status_fn, "level 白名单不变")
        self.assertIn("'info'", status_fn, "level 白名单不变")
        self.assertIn("BOARD_STATUS", self.bridge, "桥仍须登记 board-status")
        badge = rule(self.workbench_css, ".status-badge")
        self.assertTrue(badge, "页内 .status-badge 被删除")
        self.assertRegex(badge, r"background\s*:\s*var\(--color-primary-light\)",
                         "页内状态行必须保持蓝底")
        self.assertRegex(badge, r"color\s*:\s*var\(--color-primary\)",
                         "页内状态行必须保持蓝字")
        for token in (".tech-embed .title-row .status-badge", ".tech-embed #status",
                      ".tech-embed .ai-status"):
            with self.subTest(token=token):
                self.assertIn(token, self.embed, f"嵌入态隐藏规则 {token} 被改动")


if __name__ == "__main__":
    unittest.main()
