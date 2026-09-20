"""技术工艺 / 报价 助手卡片统一：一层边框 + 去头像 + token 与几何对齐。

背景（用户反馈 + 本次实测）：
  · 一次助手回复在技术侧出现**两层边框**：外层 `.oc-amsg` 一张带框白卡，里面再套一张带框卡
    （`index.html:141` / `assembly-integration.html:123` / `cost-review.html:97` 的 `.oc-intent-card`，
    以及 `agent-chat.css:233` 的 `.oc-art`、`:329` 的 `.oc-process-card`、`:357` 的 `.oc-match-card`）；
  · 技术侧每张助手卡左侧还挂一个 28px 渐变头像（`agent-chat.css:170` 的 `.oc-aav`，`✦` / `¥`），
    报价侧没有；两边卡的边框色也不是同一个值 —— 技术 `--oc-border-2: #d9d9e3`
    （`agent-chat.css:12`），报价 `--border-color: #e7e7ea`（`确认需求解析结果.html:26`）；
  · 消息间距、用户气泡圆角/内边距/宽度上限、行内代码圆角也都各写一套。

新契约见 docs/specs/tech-quote-assistant-card-unification.md：
  C1 技术侧删掉头像与 `.oc-aav` 规则，身份统一靠报价同款蓝色身份行 `.oc-alabel`；
  C2 `.oc-amsg` 是助手回复里唯一带 `background` + `border` 的容器，卡内区块一律无框；
     同级卡 `.oc-task-card` 保留一张框，但与 `.oc-amsg` 完全同款；
  C3 技术卡框改用 `var(--oc-border-3)`（= `#e7e7ea`，与报价 `--border-color` 同值）；
  C4 `.oc-tinner` gap 14px、`.oc-ubub` 14px/4px/`11px 14px`/92%、行内代码圆角 4px；
  C5 阶段页 `aiProcessCard()` / `crCard()` 改成 `.oc-amsg` + `.oc-alabel` + `.oc-alabel-state`，
     不再各写一套 `.oc-process-state`；
  C6 思考过程与工具「详情」仍默认折叠，任务卡管线、用户主色气泡、桥协议与路由一律不动。

验证方式：CSS 用选择器取真实规则块比对两侧同值；JS 按函数体/整文件断言节点与类名，
agent-chat.js 读盘前先清 NUL 哨兵；不改任何业务实现。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
TECH_CSS = F / "agent-chat.css"
TECH_JS = F / "agent-chat.js"
QUOTE = ROOT / "确认需求解析结果.html"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 技术侧所有会渲染助手卡的前端资源：头像必须从这里全部消失。
AVATAR_FILES = (
    F / "agent-chat.js",
    F / "assembly-integration.js",
    F / "cost-review.js",
    F / "assembly-integration.html",
    F / "cost-review.html",
    F / "index.html",
)

# `.oc-amsg` 里面不允许再出现第二层框的区块（`.oc-art` / `.oc-thinking` 必须保留规则本身）。
INNER_SELECTORS = (".oc-art", ".oc-thinking", ".oc-intent-card", ".oc-process-card", ".oc-match-card")


def read(path: Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", "replace")


def rule(text: str, selector: str) -> str:
    """取一个选择器的规则体；找不到返回空串。"""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text, re.S)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def block_from(text: str, marker: str) -> str:
    """marker 之后第一个配对大括号块（含大括号）；配对不上返回空串。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    index = brace
    while index < len(text):
        char = text[index]
        if char in "\"'`":
            quote = char
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    break
                index += 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace:index + 1]
        index += 1
    return ""


def function_body(text: str, name: str) -> str:
    """按括号配平截取一个 JS 函数体（含函数签名）。"""
    match = re.search(r"function\s+%s\s*\(" % re.escape(name), text)
    if not match:
        return ""
    brace = text.find("{", match.end())
    if brace < 0:
        return ""
    depth = 0
    index = brace
    while index < len(text):
        char = text[index]
        if char in "\"'`":
            quote = char
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    break
                index += 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[match.start():index + 1]
        index += 1
    return ""


def declarations(body: str) -> dict:
    """把 `a: b;` 规则体拆成 {属性: 值}，值里的多余空格归一（先去掉规则里的注释）。"""
    out = {}
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    for item in body.split(";"):
        if ":" not in item:
            continue
        prop, _, value = item.partition(":")
        out[prop.strip().lower()] = re.sub(r"\s+", " ", value).strip()
    return out


class TechCss(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tech = read(TECH_CSS)
        cls.quote = read(QUOTE)
        cls.tech_js = read(TECH_JS)


class AvatarIsGoneAndIdentityRowIsShared(TechCss):
    """C1：技术侧去掉头像，身份统一靠报价同款蓝色身份行。"""

    def test_avatar_rule_is_removed(self):
        self.assertEqual("", rule(self.tech, ".oc-aav"), "`.oc-aav` 规则必须删除，技术侧不再有头像")

    def test_no_avatar_node_left_in_any_tech_entry(self):
        for path in AVATAR_FILES:
            with self.subTest(path=path.name):
                text = read(path)
                self.assertNotIn("oc-aav", text, f"{path.name} 里还有头像节点")

    def test_assistant_and_system_cards_carry_the_identity_row(self):
        for name in ("addAssistant", "pushSystem"):
            with self.subTest(function=name):
                body = function_body(self.tech_js, name)
                self.assertTrue(body, f"找不到 {name}()")
                for token in ("oc-amsg", "oc-abody", "oc-atxt", "oc-alabel"):
                    self.assertIn(token, body, f"{name}() 缺少 {token}")

    def test_system_notice_keeps_the_shared_identity_label(self):
        body = function_body(self.tech_js, "pushSystem")
        self.assertIn("技术工艺智能体", body, "系统提示要和普通输出同一身份行")

    def test_stage_page_cards_use_the_same_identity_row(self):
        for path, label in ((F / "assembly-integration.js", "技术工艺智能体"),
                            (F / "cost-review.js", "成本测算")):
            with self.subTest(path=path.name):
                text = read(path)
                self.assertIn("oc-alabel", text, f"{path.name} 的助手卡必须带身份行")
                self.assertIn(label, text, f"{path.name} 的身份行文案缺失")

    def test_assistant_card_keeps_flex_contract(self):
        body = rule(self.tech, ".oc-amsg")
        self.assertRegex(body, r"display\s*:\s*flex",
                         "`.oc-amsg` 的 display:flex 是既有契约，去头像后仍要保留")

    def test_quote_identity_row_is_untouched(self):
        self.assertIn("报价单智能体", self.quote, "报价身份行文案不得被改动")
        self.assertIn("message-label-state", self.quote, "报价状态 chip 不得被改动")


class OnlyOneBorderPerAssistantReply(TechCss):
    """C2：`.oc-amsg` 是唯一一层框，卡内区块不再各自成卡。"""

    def test_inner_blocks_have_no_box(self):
        for selector in INNER_SELECTORS:
            with self.subTest(selector=selector):
                body = rule(self.tech, selector)
                if not body:
                    # `.oc-intent-card` / `.oc-process-card` / `.oc-match-card` 允许随结构消失。
                    self.assertIn(selector, (".oc-intent-card", ".oc-process-card", ".oc-match-card"),
                                  f"{selector} 规则必须保留（本批只去掉它的框）")
                    continue
                self.assertNotRegex(body, r"border\s*:", f"{selector} 不得再自带边框")
                self.assertNotRegex(body, r"background\s*:", f"{selector} 不得再自带底色")

    def test_quote_thinking_block_has_no_box_either(self):
        body = rule(self.quote, ".thinking-block")
        self.assertTrue(body, "报价 `.thinking-block` 规则不得删除")
        self.assertNotRegex(body, r"border\s*:", "报价思考块也要并进那一层卡，不再自带边框")
        self.assertNotRegex(body, r"background\s*:", "报价思考块不再自带底色")

    def test_assistant_card_is_the_only_box(self):
        body = rule(self.tech, ".oc-amsg")
        self.assertRegex(body, r"border\s*:\s*1px solid", "`.oc-amsg` 必须保留那一层边框")
        self.assertRegex(body, r"background\s*:\s*(#fff(?:fff)?|white)\b", "`.oc-amsg` 保持白底")

    def test_sibling_task_card_matches_the_assistant_card_box(self):
        card = rule(self.tech, ".oc-task-card")
        assistant = rule(self.tech, ".oc-amsg")
        self.assertTrue(card, "`.oc-task-card` 必须保留")
        for prop in ("border-radius", "padding"):
            with self.subTest(prop=prop):
                self.assertEqual(declarations(assistant).get(prop), declarations(card).get(prop),
                                 f"同级任务卡的 {prop} 必须与助手卡同值")

    def test_tool_trace_keeps_its_contract_without_the_box(self):
        art = rule(self.tech, ".oc-art")
        self.assertRegex(art, r"display\s*:\s*flex", "工具轨迹仍是 flex 行")
        self.assertIn("flex-wrap", art, "工具轨迹仍需换行容纳详情块")
        for token in (".oc-atile", ".oc-art-name", ".oc-art-detail", ".oc-art-raw", ".oc-art-input"):
            with self.subTest(token=token):
                self.assertIn(token, self.tech, f"工具轨迹结构 {token} 被删除")
        self.assertIn("border-top", rule(self.tech, ".oc-art-detail"),
                      "详情块与主行之间的细分隔线要保留（这是分隔线，不是卡框）")


class BorderTokenMatchesOnBothSides(TechCss):
    """C3：技术卡框与报价卡框必须是同一个颜色。"""

    def test_tech_card_border_uses_the_same_value_as_quote(self):
        tech_border3 = re.search(r"--oc-border-3\s*:\s*(#[0-9a-fA-F]{6})", self.tech)
        quote_border = re.search(r"--border-color\s*:\s*(#[0-9a-fA-F]{6})", self.quote)
        self.assertTrue(tech_border3, "agent-chat.css 缺少 --oc-border-3")
        self.assertTrue(quote_border, "报价缺少 --border-color")
        self.assertEqual(quote_border.group(1).lower(), tech_border3.group(1).lower(),
                         "技术卡框与报价卡框必须同色")

    def test_assistant_and_task_card_use_that_token(self):
        for selector in (".oc-amsg", ".oc-task-card"):
            with self.subTest(selector=selector):
                body = rule(self.tech, selector)
                self.assertIn("var(--oc-border-3)", body,
                              f"{selector} 的边框必须用与报价同色的 --oc-border-3")
                self.assertNotIn("var(--oc-border-2)", body,
                                 f"{selector} 不得再用更深的 --oc-border-2")

    def test_quote_side_keeps_its_own_token(self):
        self.assertIn("var(--border-color)", rule(self.quote, ".message-ai"),
                      "报价助手卡边框 token 不得被改")


class GeometryIsAligned(TechCss):
    """C4：间距、内边距、圆角、宽度上限两边同值。"""

    def test_message_gap_matches_quote(self):
        tech_gap = declarations(rule(self.tech, ".oc-tinner")).get("gap")
        quote_gap = declarations(rule(self.quote, ".chat-messages")).get("gap")
        self.assertEqual(quote_gap, tech_gap, "会话消息间距两边必须一致")

    def test_assistant_card_box_matches_quote(self):
        tech = declarations(rule(self.tech, ".oc-amsg"))
        quote = declarations(rule(self.quote, ".message-ai"))
        for prop in ("border-radius", "padding"):
            with self.subTest(prop=prop):
                self.assertEqual(quote.get(prop), tech.get(prop),
                                 f"助手卡的 {prop} 必须与报价同值")
        self.assertRegex(tech.get("background", ""), r"^(#fff(?:fff)?|white)$",
                         "助手卡必须是纯白底")

    def test_user_bubble_matches_quote(self):
        tech = declarations(rule(self.tech, ".oc-ubub"))
        quote = declarations(rule(self.quote, ".message-user"))
        for prop in ("border-radius", "border-bottom-right-radius", "padding"):
            with self.subTest(prop=prop):
                self.assertEqual(quote.get(prop), tech.get(prop),
                                 f"用户气泡的 {prop} 必须与报价同值")
        # 报价的宽度上限写在通用的 `.message` 上（用户气泡与助手气泡共用）。
        self.assertEqual(declarations(rule(self.quote, ".message")).get("max-width"),
                         tech.get("max-width"), "用户气泡的宽度上限必须与报价同值")
        self.assertIn("var(--oc-accent)", tech.get("background", ""),
                      "用户气泡仍是主色实心")

    def test_inline_code_radius_matches_quote(self):
        tech = declarations(rule(self.tech, ".oc-atxt.rendered :not(pre) > code")).get("border-radius")
        quote = declarations(rule(self.quote, ".message-text code")).get("border-radius")
        self.assertTrue(tech and quote, "两侧行内代码规则必须存在")
        self.assertEqual(quote, tech, "行内代码圆角两边必须一致")


class ProgressChipsCollapseToOneImplementation(TechCss):
    """C5：阶段页的过程卡改成与 Agent 同一张卡、同一个状态 chip。"""

    def test_stage_pages_build_the_agent_card(self):
        for path, builder in ((F / "assembly-integration.js", "aiProcessCard"),
                              (F / "cost-review.js", "crCard")):
            with self.subTest(path=path.name):
                text = read(path)
                body = function_body(text, builder)
                self.assertTrue(body, f"{path.name} 找不到 {builder}()")
                for token in ("oc-amsg", "oc-abody", "oc-alabel", "oc-alabel-state"):
                    self.assertIn(token, body, f"{builder}() 缺少 {token}")
                self.assertNotIn("oc-aav", body, f"{builder}() 仍在建头像节点")
                self.assertNotIn("oc-process-state", body,
                                 f"{builder}() 仍在用第二套状态 chip 类名")

    def test_process_steps_stay_borderless_and_expanded(self):
        for selector in (".oc-process-steps", ".oc-process-step", ".oc-process-dot"):
            with self.subTest(selector=selector):
                body = rule(self.tech, selector)
                self.assertTrue(body, f"步骤区 {selector} 规则必须保留")
                self.assertNotRegex(body, r"border\s*:", f"{selector} 不得自带边框")
        self.assertEqual("", rule(self.tech, ".oc-process-state"),
                         "`.oc-process-state` 已被 `.oc-alabel-state` 取代，规则应删除")


class NothingIsRelaxed(TechCss):
    """C6：折叠口径、任务卡管线、用户气泡与路由边界一律不动。"""

    def test_thinking_block_stays_a_collapsed_details(self):
        body = function_body(self.tech_js, "appendThinking")
        self.assertTrue(body, "找不到 appendThinking()")
        self.assertIn("oc-thinking", body, "思考过程折叠块必须保留")
        self.assertIn("details", body, "思考过程仍是原生 details")
        self.assertNotRegex(body, r'\bopen\b\s*[:=]\s*true', "思考过程仍默认折叠")

    def test_tool_details_stay_collapsed(self):
        body = function_body(self.tech_js, "addToolCard")
        self.assertTrue(body, "找不到 addToolCard()")
        self.assertIn("oc-art-detail", body, "工具「详情」折叠块必须保留")
        self.assertIn("details", body, "工具详情仍是原生 details")
        self.assertNotRegex(body, r'\bopen\b\s*[:=]\s*true', "工具详情仍默认折叠")

    def test_task_card_pipeline_is_kept(self):
        for token in ("oc-task-card", "oc-task-steps", "renderTaskProgress",
                      "sanitizeTaskDetail", "taskProgressHost"):
            with self.subTest(token=token):
                self.assertIn(token, self.tech + self.tech_js, f"任务卡管线 {token} 被删除")

    def test_user_bubble_stays_primary_filled(self):
        body = rule(self.tech, ".oc-ubub")
        self.assertIn("var(--oc-accent)", body, "用户气泡仍是主色实心")
        self.assertNotRegex(body, r"background\s*:\s*(#fff(?:fff)?|white)\b", "用户气泡不得变白底")

    def test_no_backend_route_or_style_token_leaks_into_backend(self):
        main = read(MAIN)
        for token in ("oc-aav", "oc-alabel", "oc-task-card", "chat-fused"):
            with self.subTest(token=token):
                self.assertNotIn(token, main, "不得为样式改动新增后端路由或后端样式分支")


if __name__ == "__main__":
    unittest.main()
