"""技术工艺会话：去掉红色报错卡片，失败信息按普通输出继续。

背景（用户反馈 + 本次实测）：
  · 左侧会话里凡是失败/系统提示都渲染成红字卡片 —— `pushSystem()`（agent-chat.js:458）用
    `el("div", "oc-err-line", `⚠ ${text}`)` 配一个「!」头像，`agent-chat.css:255` 的
    `.oc-err-line { color: #dc2626 }` 把它染红；
  · 流式失败（agent-chat.js:668 的 `event.type === "error"`）与 SSE 读取失败（:720 的 catch）
    也各自往同一条回复里塞一行 `.oc-err-line`；
  · 用户口径：**报错要和普通输出一样继续输出**，不要这些红字卡片
    （此前的问题是「报错持续在最下面一直看到」，既占位又不是一套样式）。

新契约见 docs/specs/tech-chat-drop-red-error-cards.md：
  `pushSystem` 改用与普通助手输出同款的结构（`oc-amsg` + `oc-aav` ✦ + `oc-abody` + `oc-atxt`）；
  流式失败写进同一条回复的正文并保留失败状态位；`.oc-err-line` 从 JS 与 CSS 一起删掉；
  **但失败文本必须仍然可见**，状态 chip（`⚠ 失败`）、工具结果错误边框（`.oc-tool-result.err`）、
  看板侧白色气泡里的 `⚠` 文本都要保留。

验证方式：agent-chat.js 是 CRLF + NUL 的混合体，读盘前先清 NUL；函数体按括号配平截取，
断言落在真实函数体上，不是全文件 grep。另用 `node --check` 保证改完仍能解析。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
CHAT_PATH = FRONTEND / "agent-chat.js"
CSS_PATH = FRONTEND / "agent-chat.css"
COST_REVIEW_PATH = FRONTEND / "cost-review.js"
ASSEMBLY_PATH = FRONTEND / "assembly-integration.js"


def read(path: Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", "replace")


CHAT = read(CHAT_PATH)
CSS = read(CSS_PATH)


def balanced_block(source: str, open_index: int) -> str:
    pairs = {"{": "}", "(": ")", "[": "]"}
    open_ch = source[open_index]
    close_ch = pairs[open_ch]
    depth = 0
    quote = ""
    escaped = False
    index = open_index
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        else:
            if char in "\"'`":
                quote = char
            elif char == open_ch:
                depth += 1
            elif char == close_ch:
                depth -= 1
                if depth == 0:
                    return source[open_index:index + 1]
        index += 1
    raise AssertionError("括号不配平：起始位置 %d" % open_index)


def function_body(source: str, signature: str) -> str:
    """按签名截取函数体（含外层大括号），签名匹配不到返回空串。"""
    at = source.find(signature)
    if at < 0:
        return ""
    brace = source.find("{", at)
    if brace < 0:
        return ""
    return balanced_block(source, brace)


def branch_block(source: str, marker: str, span: int = 500) -> str:
    """截取某个分支标记之后的一小段，用于断言分支内部实现。"""
    at = source.find(marker)
    if at < 0:
        return ""
    return source[at:at + span]


class RedErrorCardGone(unittest.TestCase):
    """红字卡片整体退役：JS 与 CSS 都不再持有这个类。"""

    def test_chat_js_no_longer_renders_the_red_error_line(self):
        self.assertNotIn("oc-err-line", CHAT,
                         "agent-chat.js 仍在用 .oc-err-line 渲染错误 —— 那就是用户要去掉的红字卡片")

    def test_css_drops_the_red_error_line_rule(self):
        self.assertNotIn(".oc-err-line", CSS, "agent-chat.css 仍保留红字报错样式")

    def test_conversation_errors_are_not_red_text(self):
        blocks = [self._rule(".oc-err-line")]
        for block in blocks:
            self.assertEqual("", block)
        self.assertIn(".oc-tool-result.err", CSS,
                      "工具结果错误边框是工具卡内部的状态，不属于本批要删的红字报错卡片")

    def _rule(self, selector: str) -> str:
        at = CSS.find(selector)
        if at < 0:
            return ""
        brace = CSS.find("{", at)
        return CSS[at:brace] if brace > at else ""


class SystemNoticesUseOrdinaryOutput(unittest.TestCase):
    """pushSystem 是唯一的系统提示出口：改成普通输出，但仍要真的写进线程。"""

    def setUp(self):
        body = function_body(CHAT, "function pushSystem(")
        self.assertTrue(body, "agent-chat.js 缺少 pushSystem()")
        self.body = body

    def test_push_system_uses_the_ordinary_output_structure(self):
        for token in ("oc-amsg", "oc-aav", "oc-abody", "oc-atxt"):
            with self.subTest(token=token):
                self.assertIn(token, self.body,
                              "pushSystem 必须与普通助手输出同款渲染（缺少 %s）" % token)

    def test_push_system_uses_the_assistant_identity_avatar(self):
        self.assertIn("✦", self.body,
                      "系统提示要和普通输出一样，用助手同一身份（✦），不再是「!」头像")
        self.assertNotIn('"!"', self.body, "pushSystem 仍在用「!」头像")

    def test_push_system_no_longer_prepends_the_warning_glyph(self):
        self.assertNotRegex(self.body, r"⚠\s*\$\{",
                            "仍以 `⚠ ${...}` 拼红字前缀；文本应作为普通正文输出")

    def test_push_system_still_appends_to_the_thread(self):
        self.assertIn("tinner.append(", self.body, "pushSystem 必须仍然把提示写进会话线程")
        self.assertIn("scrollDown(", self.body, "写入后仍要跟随滚动")

    def test_notice_outlet_is_still_push_system(self):
        self.assertRegex(CHAT, r"notice\s*:\s*pushSystem",
                         "window.ocTechAgent.notice 必须仍是 pushSystem（父壳唯一提示入口）")

    def test_board_failure_notice_still_routes_through_push_system(self):
        body = function_body(CHAT, "function boardFailureNotice(")
        self.assertTrue(body, "agent-chat.js 缺少 boardFailureNotice()")
        self.assertIn("pushSystem(", body, "看板失败仍要经唯一出口进会话，不得静默吞掉")
        self.assertIn("isQuietBoardCode(", body, "预期内失败码仍要提前返回、不刷噪音")


class StreamingFailuresStayInTheReply(unittest.TestCase):
    """流式失败并入同一条回复正文，状态位照旧置失败。"""

    def test_error_event_writes_plain_text_and_marks_failed(self):
        block = branch_block(CHAT, 'if (event.type === "error")')
        self.assertTrue(block, '找不到 event.type === "error" 分支')
        self.assertIn('setAssistantState(ctx, "failed")', block,
                      "失败状态位不能丢：回复仍要显示「⚠ 失败」")
        self.assertNotIn("oc-err-line", block, "该分支仍在插红字行")
        self.assertRegex(block, r"ctx\.(text|full)",
                         "失败原因要写进同一条回复的正文，而不是另一张红色卡片")

    def test_sse_read_failure_marks_failed_without_red_line(self):
        blocks = []
        at = CHAT.find("} catch (error) {")
        while at >= 0:
            block = CHAT[at:at + 600]
            if "ctx.failed = true" in block:
                blocks.append(block)
            at = CHAT.find("} catch (error) {", at + 1)
        self.assertEqual(1, len(blocks), "找不到 SSE 读取失败的那个 catch")
        block = blocks[0]
        self.assertIn('setAssistantState(ctx, "failed")', block,
                      "连接失败也要保留失败状态位")
        self.assertNotIn("oc-err-line", block, "连接失败仍在插红字行")
        self.assertRegex(block, r"ctx\.(text|full)",
                         "连接失败的原因要写进同一条回复的正文，而不是另一张红色卡片")

    def test_status_chip_still_carries_the_failed_glyph(self):
        body = function_body(CHAT, "function setAssistantState(")
        self.assertTrue(body, "agent-chat.js 缺少 setAssistantState()")
        self.assertIn("⚠", body, "失败状态位（⚠ 失败）被一起删掉了")
        self.assertIn("is-failed", body, "失败状态位样式类被删掉了")


class NothingElseWasStripedOut(unittest.TestCase):
    """不缩水：看板侧提示、失败文本与状态位都要还在。"""

    def test_board_side_plain_bubbles_keep_their_warning_text(self):
        for path in (COST_REVIEW_PATH, ASSEMBLY_PATH):
            source = read(path)
            with self.subTest(path=path.name):
                self.assertIn("⚠", source,
                              "看板侧白色气泡里的 ⚠ 文本不是红色卡片，不得一起删")

    def test_failure_text_is_never_swallowed(self):
        self.assertIn("pushSystem(`${prefix}${reason}。`)", CHAT,
                      "看板失败仍要把真实原因写进会话，不能改成静默 return")

    def test_agent_chat_still_parses(self):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("未安装 node，跳过语法检查")
        for path in (CHAT_PATH, CSS_PATH):
            if path.suffix == ".css":
                continue
            completed = subprocess.run([node, "--check", str(path)],
                                       capture_output=True, text=True, timeout=60)
            self.assertEqual(0, completed.returncode,
                             "%s 语法错误：\n%s" % (path.name, completed.stderr))


if __name__ == "__main__":
    unittest.main()
