"""红测：思考过程（思维链）默认折叠、点击展开（技术工艺 + 报价）。

现状缺口：
  · 供应商流本来就有 ``thinking_delta``（``open-claude/open_claude`` 的 ``content_block_delta``），
    但技术 ``oc_agent.py::_stream_once()`` 与报价 ``cpq_agent_server.py::_stream_once()``
    只转发 ``text_delta / tool_use_* / message_end``，思维链被静默丢弃，前端拿不到；
  · 技术会话（``agent-chat.js``）与报价会话（``确认需求解析结果.html``）都没有
    「默认隐藏、点击展开」的思考过程折叠块；
  · 报价系统提示强制模型按「🤔 思考 / 📋 规划 / ⚙️ 执行 / ✅ 结果」输出，
    前三段现在落在正文气泡里，与真实思维链重复占版面。

本批只新增后端 ``thinking`` 帧与前端折叠块；不改既有事件语义、不落库、不新增路由。
不联网、不起服务、不读真实业务数据；除 ``splitReasoningSections`` 的行为校验外均为静态契约。
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
TECH_BACKEND = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"
QUOTE_BACKEND = ROOT / "cpq_agent_server.py"
TECH_JS = F / "agent-chat.js"
TECH_CSS = F / "agent-chat.css"
QUOTE = ROOT / "确认需求解析结果.html"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

WHITE = re.compile(r"background\s*:\s*(#fff(?:fff)?|white)\b")
BORDER = re.compile(r"border\s*:\s*1px solid")
GRAY = ("var(--bg-secondary)", "var(--oc-bg-2)", "var(--oc-bg-3)")

# 报价系统提示里强制的四段标记：前三段收进折叠块，✅ 结果留在正文。
REASONING_MARKS = ("🤔", "📋", "⚙️", "✅")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
    """返回 marker 之后第一对花括号包住的块（跳过字符串与 // 注释）。"""
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


def py_block(text: str, name: str) -> str:
    """按缩进切出 Python 函数体；花括号匹配不适用于 Python 字典字面量。"""
    match = re.search(rf"^([ \t]*)def {re.escape(name)}\(", text, re.M)
    if not match:
        return ""
    indent = match.group(1)
    rest = text[match.start():]
    nxt = re.search(rf"\n{re.escape(indent)}(?:def |class |@)", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


class ChatCollapsibleThinkingTraceRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tech_backend = read(TECH_BACKEND)
        cls.quote_backend = read(QUOTE_BACKEND)
        cls.tech_js = read(TECH_JS)
        cls.tech_css = read(TECH_CSS)
        cls.quote = read(QUOTE)
        cls.main = read(MAIN)

    # ------------------------------------------------------------ 后端：技术工艺
    def test_tech_backend_forwards_thinking_delta(self):
        body = py_block(self.tech_backend, "_stream_once")
        self.assertTrue(body, "找不到 oc_agent._stream_once()")
        self.assertIn("thinking_delta", body, "供应商的 thinking_delta 仍被丢弃")
        self.assertRegex(body, r'["\']type["\']\s*:\s*["\']thinking["\']',
                         "必须下发新的 thinking 帧")
        self.assertRegex(body, r"thinking_delta[\s\S]{0,400}emit\(",
                         "thinking_delta 分支必须紧随 emit 下发")

    def test_tech_backend_keeps_existing_stream_events(self):
        body = py_block(self.tech_backend, "_stream_once")
        for token in ("text_delta", "tool_use_end", "message_end"):
            with self.subTest(token=token):
                self.assertIn(token, body, f"既有流式分支 {token} 被改动")
        self.assertIn("max_tokens", self.tech_backend, "截断处理不得被删除")

    def test_tech_thinking_is_not_persisted(self):
        body = py_block(self.tech_backend, "_serialize_history")
        self.assertTrue(body, "找不到 _serialize_history()")
        self.assertNotIn("thinking", body, "思维链不得进入会话历史回放")
        self.assertIn('kind == "text"', body, "历史回放既有分支被改动")
        self.assertNotRegex(self.tech_backend, r'events\.append\(\{[^}]*["\']thinking["\']',
                            "思维链不得写入会话事件")

    # ------------------------------------------------------------ 后端：报价
    def test_quote_backend_forwards_thinking_delta(self):
        body = py_block(self.quote_backend, "_stream_once")
        self.assertTrue(body, "找不到 cpq_agent_server._stream_once()")
        self.assertIn("thinking_delta", body, "供应商的 thinking_delta 仍被丢弃")
        self.assertRegex(body, r'["\']type["\']\s*:\s*["\']thinking["\']',
                         "必须下发新的 thinking 帧")
        self.assertRegex(body, r'elif t == "thinking_delta"[\s\S]{0,400}emit\(',
                         "thinking_delta 分支必须紧随 emit 下发")

    def test_quote_backend_keeps_existing_stream_events(self):
        body = py_block(self.quote_backend, "_stream_once")
        for token in ("text_delta", "tool_use_start", "tool_use_end", "message_end"):
            with self.subTest(token=token):
                self.assertIn(token, body, f"既有流式分支 {token} 被改动")

    def test_quote_thinking_is_not_persisted(self):
        body = py_block(self.quote_backend, "_stream_once")
        self.assertNotRegex(body, r'self\.events\.append\(\{[^}]*["\']thinking["\']',
                            "思维链不得写入会话事件")
        self.assertIn('self.events.append({"type": "tool_use"', body,
                      "既有工具事件写入被改动")

    def test_no_new_thinking_route(self):
        targets = [(self.main, "tech_app/backend/main.py"), (self.quote_backend, "cpq_agent_server.py")]
        for text, where in targets:
            with self.subTest(where=where):
                self.assertNotIn("/thinking", text, "不得为思维链新增后端路由")

    # ------------------------------------------------------------ 技术工艺前端
    def test_tech_chat_handles_thinking_event(self):
        body = block_from(self.tech_js, "function handleEvent(")
        self.assertTrue(body, "找不到 handleEvent()")
        self.assertRegex(body, r'event\.type === "thinking"',
                         "handleEvent 必须处理 thinking 帧")
        self.assertRegex(body, r'event\.type === "thinking"[\s\S]{0,160}appendThinking\(',
                         "thinking 帧必须交给 appendThinking()")
        self.assertRegex(body, r'event\.type === "thinking"[\s\S]{0,200}return;',
                         "thinking 帧不得落进正文文本累积")

    def test_tech_append_thinking_builds_collapsed_details(self):
        body = block_from(self.tech_js, "function appendThinking(")
        self.assertTrue(body, "缺少 appendThinking()")
        self.assertIn("details", body, "折叠容器必须用原生 details")
        self.assertIn("oc-thinking", body, "缺少折叠块 class")
        self.assertIn("思考过程", body, "折叠行文案缺失")
        self.assertIn("oc-thinking-body", body, "缺少折叠正文容器")
        self.assertIn("insertBefore", body, "折叠块必须插在正文文本之前")
        self.assertRegex(body, r"if\s*\(\s*![\w.]*\s*\)\s*return",
                         "文本为空时必须直接 return，不建空块")

    def test_tech_side_note_does_not_replay_thinking(self):
        body = block_from(self.tech_js, "function renderHistory(")
        self.assertTrue(body, "找不到 renderHistory()")
        self.assertNotIn("thinking", body, "历史回放不得渲染思维链")
        for token in ('event.type === "user"', 'event.type === "assistant"',
                      'event.type === "tool_use"', 'event.type === "tool_result"'):
            with self.subTest(token=token):
                self.assertIn(token, body, f"历史回放既有分支 {token} 被改动")

    def test_tech_thinking_css_is_white_with_border(self):
        blocks = re.findall(r"\.oc-thinking\s*\{[^}]*\}", self.tech_css)
        self.assertTrue(blocks, "缺少 .oc-thinking 样式")
        for block in blocks:
            with self.subTest(block=block[:50]):
                self.assertRegex(block, WHITE, ".oc-thinking 必须是白底")
                self.assertRegex(block, BORDER, ".oc-thinking 必须有 1px 边框")
                for token in GRAY:
                    self.assertNotIn(token, block, f".oc-thinking 不得用灰底 {token}")
        self.assertIn(".oc-thinking-body", self.tech_css, "缺少 .oc-thinking-body 样式")
        self.assertIn(".oc-thinking summary", self.tech_css, "折叠行需要可点击样式")

    # ------------------------------------------------------------ 报价前端
    def test_quote_chat_handles_thinking_event(self):
        body = block_from(self.quote, "function handleAgentEvent(")
        self.assertTrue(body, "找不到 handleAgentEvent()")
        self.assertIn("case 'thinking':", body, "SSE switch 必须处理 thinking 帧")
        self.assertRegex(body, r"case 'thinking':[\s\S]{0,160}appendThinkingText\(",
                         "thinking 帧必须交给 appendThinkingText()")

    def test_quote_append_thinking_builds_collapsed_details(self):
        body = block_from(self.quote, "function appendThinkingText(")
        self.assertTrue(body, "缺少 appendThinkingText()")
        self.assertIn("details", body, "折叠容器必须用原生 details")
        self.assertIn("thinking-block", body, "缺少折叠块 class")
        self.assertIn("思考过程", body, "折叠行文案缺失")
        self.assertIn("thinking-body", body, "缺少折叠正文容器")
        self.assertIn("insertBefore", body, "折叠块必须插在 .message-text 之前")
        self.assertRegex(body, r"if\s*\(\s*![\w.]*\s*\)\s*return",
                         "文本为空时必须直接 return，不建空块")

    def test_quote_thinking_css_is_white_with_border(self):
        blocks = re.findall(r"\.thinking-block\s*\{[^}]*\}", self.quote)
        self.assertTrue(blocks, "缺少 .thinking-block 样式")
        for block in blocks:
            with self.subTest(block=block[:50]):
                self.assertRegex(block, WHITE, ".thinking-block 必须是白底")
                self.assertRegex(block, BORDER, ".thinking-block 必须有 1px 边框")
                for token in GRAY:
                    self.assertNotIn(token, block, f".thinking-block 不得用灰底 {token}")
        self.assertIn(".thinking-body", self.quote, "缺少 .thinking-body 样式")

    def test_quote_finish_bubble_splits_reasoning_sections(self):
        body = block_from(self.quote, "function finishStreamBubble(")
        self.assertTrue(body, "找不到 finishStreamBubble()")
        self.assertIn("splitReasoningSections", body, "正文渲染前必须收拢思考/规划/执行段")
        self.assertIn("renderMarkdown", body, "正文仍走既有 Markdown 渲染")
        self.assertLess(body.find("splitReasoningSections"), body.find("renderMarkdown"),
                        "分段必须发生在 renderMarkdown 之前")
        for mark in REASONING_MARKS:
            with self.subTest(mark=mark):
                self.assertIn(mark, self.quote, f"系统提示的 {mark} 段落必须被识别")
        self.assertIn("思考-规划-执行-结果", self.quote_backend,
                      "系统提示词不得为本次改动被改写")

    def test_quote_split_reasoning_sections_behaviour(self):
        body = block_from(self.quote, "function splitReasoningSections(")
        self.assertTrue(body, "缺少 splitReasoningSections()")
        node = shutil.which("node")
        if not node:
            # 无 node 时退回静态契约：四段标记齐全，且必须同时返回正文与思维链。
            for mark in REASONING_MARKS:
                with self.subTest(mark=mark):
                    self.assertIn(mark, body, f"{mark} 段落未被识别")
            self.assertIn("body", body, "必须返回正文")
            self.assertIn("thinking", body, "必须返回思维链文本")
            return
        cases = [
            "🤔 **思考**：判断口径\n\n📋 **规划**：查三张表\n\n⚙️ **执行**：填入 s1_dest\n\n✅ **结果**：已填好，请核对",
            "这是一段普通答复，没有任何分段标记。",
            "",
        ]
        script = (
            body
            + "\nconst out = " + json.dumps(cases, ensure_ascii=False)
            + ".map(v => splitReasoningSections(v));\n"
            + "console.log(JSON.stringify(out));"
        )
        proc = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"splitReasoningSections 无法执行：{proc.stderr}")
        results = json.loads(proc.stdout)
        self.assertEqual(len(results), len(cases), "返回结果数量不匹配")
        structured = results[0]
        self.assertIsInstance(structured, dict, "必须返回 { body, thinking } 结构")
        self.assertIn("body", structured, "缺少 body 字段")
        self.assertIn("thinking", structured, "缺少 thinking 字段")
        self.assertIn("✅", structured["body"], "✅ 结果段必须留在正文")
        self.assertIn("请核对", structured["body"], "结果段之后的正文不得被吞掉")
        for mark in ("🤔", "📋", "⚙️"):
            with self.subTest(mark=mark):
                self.assertNotIn(mark, structured["body"], f"{mark} 段落不得留在正文")
                self.assertIn(mark, structured["thinking"], f"{mark} 段落必须收进折叠块")
        for index, raw in enumerate(cases[1:], start=1):
            with self.subTest(case=index):
                self.assertEqual(results[index]["body"], raw, "无标记时必须一字不改地返回原文")


if __name__ == "__main__":
    unittest.main()
