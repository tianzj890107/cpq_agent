"""技术工艺工具轨迹：业务行 + 可展开详情 红测基线。

现状缺口（技术侧 `tech_app/frontend/agent-chat.js`）：

  · `addToolCard()` 把原始平台工具名（`ListParts`、`tech_ui`、`mcp__…`）直接当主标题；
  · `toolSubtitle()` 把入参 JSON 铺在副标题，业务用户直接看到实现细节；
  · `setToolResult()` 把整份工具返回常驻渲染成 `<pre class="oc-tool-result">`，无法收起。

报价侧 `确认需求解析结果.html` 的 `addToolActivity()` + `describeTool()` 只给一行中文业务描述，
不展示结果。本批把技术侧改成混合形态：主行对齐报价「业务行」，原始工具名 / 入参 / 结果收进
默认折叠的原生 `<details>`，既不把术语抛给业务用户，也不丢可审计性。

不联网、不起服务、不读真实业务数据；只做静态契约与 `node --check` 语法校验。
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
CHAT_JS = FRONTEND / "agent-chat.js"
CHAT_CSS = FRONTEND / "agent-chat.css"
AGENT_SERVICE = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"

# 第 17 步固定的 tech_ui action 枚举，一个都不能漏。
UI_ACTIONS = (
    "focus_view",
    "refresh_view",
    "fill_fields",
    "select_part",
    "show_result_actions",
    "show_progress",
    "set_stage",
    "request_confirmation",
)

# 代表性工具 → 中文文案里必须出现的关键词（防止英文工具名直接透传）。
REPRESENTATIVE_LABELS = {
    "GetProjectState": "状态",
    "ListParts": "零件",
    "GetPartDetail": "零件",
    "GetOpenQuestions": "问题",
    "UpdatePartParameters": "参数",
    "LookupComponentLibrary": "零部件",
    "LookupProcessLibrary": "工艺",
    "LookupCostLibrary": "成本",
    "RequestParse": "解析",
    "ExtractRequirement": "需求",
    "ConfirmRequirement": "确认",
    "ApproveRequirementReview": "审核",
    "RunCostReviewAll": "成本",
    "PublishProcessReport": "发布",
    "SendReportToQuote": "报价",
}

CJK = re.compile(r"[\u4e00-\u9fff]")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _js_braced(text: str, signature: str) -> str:
    """返回 signature 之后第一对成对花括号（含花括号）的源码，跳过字符串与注释。"""
    idx = text.find(signature)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(signature))
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


def _js_function(text: str, name: str) -> str:
    match = re.search(r"function\s+%s\s*\(" % re.escape(name), text)
    if not match:
        return ""
    return _js_braced(text, match.group(0))


def _js_object(text: str, name: str) -> str:
    return _js_braced(text, "const %s =" % name)


def _entries(body: str) -> dict:
    return dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"([^"]*)"', body))


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _css_rule(css: str, selector: str, suffix: str = "") -> str:
    pattern = re.escape(selector) + r"(?![\w-])" + re.escape(suffix) + r"\s*\{([^}]*)\}"
    match = re.search(pattern, css)
    return match.group(1) if match else ""


class TechToolTraceBusinessLineRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = _read(CHAT_JS)
        cls.css = _read(CHAT_CSS)
        cls.backend = _read(AGENT_SERVICE)
        cls.labels = _entries(_js_object(cls.chat, "TOOL_TRACE_LABELS"))
        cls.ui_labels = _entries(_js_object(cls.chat, "TOOL_TRACE_UI_ACTIONS"))
        cls.label_fn = _js_function(cls.chat, "toolTraceLabel")
        cls.card_fn = _js_function(cls.chat, "addToolCard")
        cls.result_fn = _js_function(cls.chat, "setToolResult")

    # ------------------------------------------------------------- 业务文案映射
    def test_every_platform_tool_has_a_chinese_business_label(self):
        tools = [n for n in re.findall(r'"name":\s*"(\w+)"', self.backend)]
        self.assertTrue(tools, "读不到 oc_agent.py 的平台工具清单")
        self.assertTrue(
            self.labels,
            "agent-chat.js 里找不到 `const TOOL_TRACE_LABELS` 工具→中文映射表",
        )
        missing = sorted(t for t in tools if t != "tech_ui" and t not in self.labels)
        self.assertFalse(
            missing,
            "这些平台工具没有中文业务标签，主行会直接暴露原始工具名：" + ", ".join(missing),
        )

    def test_labels_are_chinese_and_use_business_wording(self):
        offenders = [
            (name, label)
            for name, label in self.labels.items()
            if not label.strip() or not CJK.search(label) or label.strip() == name
        ]
        self.assertFalse(offenders, "中文标签缺失或直接复用了工具名：%s" % (offenders,))
        for name, keyword in REPRESENTATIVE_LABELS.items():
            self.assertIn(name, self.labels, "%s 缺少业务标签" % name)
            self.assertIn(
                keyword,
                self.labels[name],
                "%s 的文案 %r 没有命中业务关键词 %r" % (name, self.labels[name], keyword),
            )

    def test_tech_ui_actions_all_have_chinese_labels(self):
        self.assertTrue(
            self.ui_labels,
            "agent-chat.js 里找不到 `const TOOL_TRACE_UI_ACTIONS` action→中文映射表",
        )
        missing = [a for a in UI_ACTIONS if a not in self.ui_labels]
        self.assertFalse(missing, "这些 tech_ui action 没有中文文案：%s" % (missing,))
        bad = [a for a in UI_ACTIONS if not CJK.search(self.ui_labels.get(a, ""))]
        self.assertFalse(bad, "这些 tech_ui action 文案不是中文：%s" % (bad,))
        self.assertIn("TOOL_TRACE_UI_ACTIONS", self.label_fn, "toolTraceLabel 必须走 action 表处理 tech_ui")
        self.assertIn("tech_ui", self.label_fn)

    def test_unknown_tool_falls_back_to_generic_chinese_line(self):
        self.assertTrue(self.label_fn, "agent-chat.js 里找不到 `function toolTraceLabel(`")
        self.assertIn("调用", self.label_fn, "未登记工具必须回退成含「调用」的中文行")
        self.assertIn("name", self.label_fn)

    def test_label_mapping_is_presentation_only(self):
        labels_obj = _js_object(self.chat, "TOOL_TRACE_LABELS")
        ui_obj = _js_object(self.chat, "TOOL_TRACE_UI_ACTIONS")
        self.assertTrue(labels_obj, "agent-chat.js 里找不到 `const TOOL_TRACE_LABELS`")
        self.assertTrue(ui_obj, "agent-chat.js 里找不到 `const TOOL_TRACE_UI_ACTIONS`")
        self.assertTrue(self.label_fn, "agent-chat.js 里找不到 `function toolTraceLabel(`")
        region = labels_obj + ui_obj + self.label_fn
        for token in ("fetch(", "/api/", "XMLHttpRequest", "executeAction", "TechBoardBridge"):
            self.assertNotIn(token, region, "工具文案映射只能是纯展示，不能出现 %s" % token)

    # ------------------------------------------------------------- 工具卡结构
    def test_tool_card_primary_line_uses_business_label(self):
        self.assertTrue(self.card_fn, "agent-chat.js 里找不到 `function addToolCard(`")
        self.assertIn("toolTraceLabel", self.card_fn, "工具卡主行必须用工具→中文映射生成文案")
        compact = _compact(self.card_fn)
        self.assertIn('"oc-art-name"', compact)
        self.assertNotIn(
            '"oc-art-name",event.name',
            compact,
            "主行不得再把原始工具名当作标题",
        )
        self.assertIn("oc-art-state", compact, "主行需要显式状态位（◌ / ✓ / ⚠）")
        self.assertIn("◌", compact)

    def test_tool_card_has_collapsed_detail_block(self):
        self.assertTrue(self.card_fn, "agent-chat.js 里找不到 `function addToolCard(`")
        compact = _compact(self.card_fn)
        self.assertIn('"details"', compact, "详情必须是原生 <details>（可折叠）")
        self.assertIn('"summary"', compact)
        self.assertIn('"oc-art-detail"', compact)
        self.assertIn('"oc-art-raw"', compact, "详情里要能看到原始工具名")
        self.assertIn('"oc-art-input"', compact, "详情里要能看到入参 JSON")
        self.assertIn('"oc-tool-result"', compact, "详情里要保留工具结果 <pre>")
        self.assertIn("event.name", self.card_fn)
        self.assertNotRegex(self.card_fn, r"\.open\s*=\s*true", "详情必须默认折叠")
        self.assertNotIn('"open"', compact, "详情必须默认折叠（不能带 open 属性）")

    def test_tool_result_keeps_truncation_error_style_and_state_semantics(self):
        self.assertTrue(self.result_fn, "agent-chat.js 里找不到 `function setToolResult(`")
        self.assertIn("4000", self.result_fn, "工具结果 4000 字截断不能丢")
        self.assertIn("result", self.result_fn, "setToolResult 仍要写回详情里的结果节点")
        self.assertIn("is_error", self.result_fn)
        self.assertIn("#dc2626", self.result_fn)
        self.assertIn("✓", self.result_fn)
        self.assertIn("⚠", self.result_fn)
        self.assertIn("#16a34a", self.result_fn)

    def test_detail_styles_exist_and_card_can_wrap(self):
        for selector in (".oc-art-detail", ".oc-art-raw", ".oc-art-input"):
            self.assertTrue(_css_rule(self.css, selector), "agent-chat.css 缺少 `%s` 规则" % selector)
        self.assertRegex(
            self.css,
            r"\.oc-art-detail[^{}]*summary[^{}]*\{",
            "agent-chat.css 缺少 `.oc-art-detail summary` 规则",
        )
        art = _css_rule(self.css, ".oc-art")
        self.assertIn("flex-wrap", art, ".oc-art 需要允许换行以容纳详情块")
        result = _compact(_css_rule(self.css, ".oc-tool-result"))
        self.assertIn("max-height:240px", result, ".oc-tool-result 的 240px 滚动不能丢")
        self.assertIn("overflow:auto", result)
        self.assertIn(".oc-tool-result.err", self.css)

    # ------------------------------------------------------------- 资源与语法
    def test_static_asset_cache_keys_are_bumped(self):
        js_pages = ("index.html", "tech-workbench.html")
        css_pages = ("index.html", "tech-workbench.html", "assembly-integration.html", "cost-review.html")
        js_versions = set()
        for page in js_pages:
            found = re.findall(r"agent-chat\.js\?v=([\w.\-]+)", _read(FRONTEND / page))
            self.assertTrue(found, "%s 没有引用 agent-chat.js?v=" % page)
            js_versions.update(found)
        css_versions = set()
        for page in css_pages:
            found = re.findall(r"agent-chat\.css\?v=([\w.\-]+)", _read(FRONTEND / page))
            self.assertTrue(found, "%s 没有引用 agent-chat.css?v=" % page)
            css_versions.update(found)
        self.assertEqual(len(js_versions), 1, "agent-chat.js 的 ?v= 必须全部一致：%s" % (js_versions,))
        self.assertEqual(len(css_versions), 1, "agent-chat.css 的 ?v= 必须全部一致：%s" % (css_versions,))
        self.assertNotEqual(
            next(iter(js_versions)), "20260911-board13",
            "改了 agent-chat.js 必须同时更新两处缓存版本号",
        )
        self.assertNotEqual(
            next(iter(css_versions)), "20260911-bubble1",
            "改了 agent-chat.css 必须同时更新四处缓存版本号",
        )

    def test_agent_chat_js_passes_node_syntax_check(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用")
        proc = subprocess.run([node, "--check", str(CHAT_JS)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_only_tool_trace_rendering_is_touched_in_agent_chat_js(self):
        for name in ("toolTraceLabel", "addToolCard", "setToolResult"):
            self.assertTrue(_js_function(self.chat, name), "缺少 %s 定义" % name)


if __name__ == "__main__":
    unittest.main()
