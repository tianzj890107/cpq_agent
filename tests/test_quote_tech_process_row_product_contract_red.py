"""红测：过程行（Tool List）产品侧收口，报价与技术工艺两侧统一。

用户口径（本批）：
  1) 状态图标统一：**做完的步骤一律 ✓**，其余一律圆圈；不再出现「点」。
  2) 之前不同颜色的文字（命中 / 未命中 / 模型 / 工具）必须保留，
     而且颜色要真的落在文字上（不是只给图标上色，也不是规则写了但类名对不上）。
  3) **「详情」这个东西和按钮完全不要**；输入 / 输出 JSON 也完全不要；
     用户不需要感知技术实现。
  4) 只保留产品侧的执行结果：查询条件、命中件与匹配度、差异、库内条数、
     费率 / 回退 / 系数 / 待补等。
  5) 报价侧与技术工艺侧**所有** Agent 输出气泡都遵守同一套过程行合同。

当前缺口（实现前天然失败，非推断）：
  · 技术左栏 `agent-chat.js::pushTaskStep` 仍建 `<details class="oc-process-detail">`
    + `<summary>详情</summary>`，并把 `输入 / 输出` JSON（空对象时就是 `{} {}`）画进去；
    标题行还挂着 `role="button"` / `tabindex` / `aria-expanded`。
  · 阶段页 `assembly-integration.js::aiProcessCard` / `cost-review.js::crCard`
    也建 `data-agent-role="tool-detail"` 折叠区，且图标写死 `●`。
  · 报价页 `确认需求解析结果.html` 的过程行同样挂着 `role="button"` /
    `tabindex` / `aria-expanded`，并且没有统一的状态图标。
  · 图标：`pushTaskStep` 里完成行是 `•`、命中是 `●`、未命中是 `○`、进行中是 `◌`。
  · 颜色：缩进行渲染成 `.oc-process-sub hit/miss`，而 CSS 只给
    `.oc-process-step.sub.hit/miss` 上色 → 实际渲染出来的类名拿不到颜色。

Spec：docs/specs/quote-tech-process-row-product-contract.md
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

from tests.test_quote_tech_unified_tool_list_conversation_red import (  # noqa: F401
    BOARD_PREAMBLE,
    DOM_STUB,
    NODE,
    arrow_source,
    function_source,
    read,
    run_node,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
CHAT_JS = FRONTEND / "agent-chat.js"
CHAT_CSS = FRONTEND / "agent-chat.css"
ASSEMBLY = FRONTEND / "assembly-integration.js"
COST = FRONTEND / "cost-review.js"
QUOTE = ROOT / "确认需求解析结果.html"

CIRCLES = ("\u25cb", "\u25cc")          # ○ / ◌
DOTS = ("\u2022", "\u25cf", "\u23fa")   # • / ● / ⏺
CHECK = "\u2713"                        # ✓
WARN = "\u26a0"                         # ⚠

# 过程行里出现过的原始工具名：用户不该看到这些技术标识。
RAW_TOOL_IDS = ("component_match", "vision_parse", "lookup_process", "cost_lookup",
                "LookupComponentLibrary", "GetPartDetail", "ListParts")

TECH_PREAMBLE = r'''
var doc = makeDoc();
var document = doc;
var T = doc.createElement("div");
function scrollDown() {}
function boardStage() { return "process"; }
'''

ROWS_JS = r'''
function ownText(node) {
  if (!node) return "";
  return (node._text === undefined) ? String(node.textContent) : String(node._text);
}
function countOwnText(root, needle) {
  if (!root) return 0;
  var total = 0;
  var all = root.querySelectorAll("*");
  for (var i = 0; i < all.length; i += 1) {
    if (ownText(all[i]).indexOf(needle) >= 0) total += 1;
  }
  return total;
}
function rowsOf(root) {
  var found = [];
  function walk(node, depth) {
    var kids = (node && node.children) ? node.children : [];
    for (var i = 0; i < kids.length; i += 1) {
      var child = kids[i];
      var role = child.getAttribute ? child.getAttribute("data-agent-role") : null;
      if (role === "tool-item") {
        var icon = child.querySelector('[data-agent-role="tool-state-icon"]');
        found.push({
          depth: depth,
          cls: String(child.className),
          state: child.getAttribute("data-state"),
          text: String(child.textContent),
          icon: icon ? String(icon.textContent).trim() : null,
          toggle: child.querySelectorAll('[data-agent-role="tool-toggle"]').length,
          detail: child.querySelectorAll('[data-agent-role="tool-detail"]').length,
          buttons: child.querySelectorAll('summary, button, [role="button"]').length,
          hidden: child.querySelectorAll("[hidden]").length,
          ariaHidden: child.querySelectorAll('[aria-hidden="true"]').length,
          nestedItems: child.querySelectorAll('[data-agent-role="tool-item"]').length
        });
        walk(child, depth + 1);
      } else {
        walk(child, depth);
      }
    }
  }
  walk(root, 0);
  return found;
}
function rosterOut(root, prefix, out) {
  var rows = rowsOf(root);
  out[prefix + "_rows"] = rows;
  out[prefix + "_icons"] = rows.map(function (r) { return r.icon; });
  out[prefix + "_states"] = rows.map(function (r) { return r.state; });
  out[prefix + "_classes"] = rows.map(function (r) { return r.cls; });
  out[prefix + "_detail_nodes"] = root.querySelectorAll('[data-agent-role="tool-detail"]').length;
  out[prefix + "_toggle_nodes"] = root.querySelectorAll('[data-agent-role="tool-toggle"]').length;
  out[prefix + "_button_nodes"] =
    root.querySelectorAll('summary, button, [role="button"]').length;
  out[prefix + "_aria_expanded"] = root.querySelectorAll("[aria-expanded]").length;
  out[prefix + "_tabindex"] = root.querySelectorAll("[tabindex]").length;
  out[prefix + "_hidden_nodes"] = root.querySelectorAll("[hidden]").length;
  out[prefix + "_aria_hidden_nodes"] = root.querySelectorAll('[aria-hidden="true"]').length;
  out[prefix + "_closed_details"] = (function () {
    var all = root.querySelectorAll("details");
    var closed = 0;
    for (var i = 0; i < all.length; i += 1) { if (!all[i].open) closed += 1; }
    return closed;
  })();
  out[prefix + "_detail_word_nodes"] = countOwnText(root, "\u8be6\u60c5");
  out[prefix + "_top_level"] = root.children.length;
  out[prefix + "_text"] = String(root.textContent);
  return rows;
}
'''

TECH_TAIL = ROWS_JS + r'''
var out = {};
out.missing = MISSING;

function newCard() {
  var steps = doc.createElement("div");
  steps.className = "oc-process-steps";
  return { steps: steps, status: "running", box: doc.createElement("div"),
           state: doc.createElement("span"), root: doc.createElement("div") };
}

var card = newCard();
pushTaskStep(card, "读取输入：source.png、补充说明 48 字", "", "");
pushTaskStep(card, "调用多模态模型解析图纸（qwen3.5-plus）", "", "model",
  { tool: "vision_parse", title: "调用模型", status: "running",
    input: { source: "source.png" }, output: {} });
pushTaskStep(card, "检索零部件库（1/4）：P-001 上壳", "", "tool",
  { tool: "component_match", title: "零部件库检索", status: "running",
    input: { part_id: "P-001" }, output: {} });
pushTaskStep(card, "  查询条件：length=108、width=56、height=13.25、hole_diameter=4", "", "tool");
pushTaskStep(card, "  命中 CMP-SEMI-EE-BLOCK-0001 搬运吸嘴主体安装块（可改制，匹配度 65%）", "", "tool");
pushTaskStep(card, "  库内无同类件，按新制评估", "", "tool");
pushTaskStep(card, "检索零部件库（2/4）：P-002 下壳", "", "tool");
pushTaskStep(card, "  差异：length: 库内 120.0mm / 图纸 108.0", "", "tool");
pushTaskStep(card, "费率 0 条 / 回退 global 0 条 / 系数 0 条 / 待补 10 项", "", "progress", null);

rosterOut(card.steps, "before", out);

setAssistantState(card, "succeeded");
rosterOut(card.steps, "after", out);

var failCard = newCard();
pushTaskStep(failCard, "检索工艺库：P-003 电芯组支架", "", "err",
             { tool: "lookup_process", status: "failed" });
rosterOut(failCard.steps, "failed", out);

out.json_like = /\{\s*\}|"[a-z_]+"\s*:/.test(String(card.steps.textContent));
out.raw_tool_id = /component_match|vision_parse|lookup_process|cost_lookup/.test(
  String(card.steps.textContent));
out.hit_class = (function () {
  var rows = rowsOf(card.steps);
  for (var i = 0; i < rows.length; i += 1) {
    if (rows[i].text.indexOf("\u547d\u4e2d") >= 0) return rows[i].cls;
  }
  return null;
})();
out.miss_class = (function () {
  var rows = rowsOf(card.steps);
  for (var i = 0; i < rows.length; i += 1) {
    if (rows[i].text.indexOf("\u65e0\u540c\u7c7b\u4ef6") >= 0) return rows[i].cls;
  }
  return null;
})();

console.log(JSON.stringify(out));
'''

BOARD_TAIL = ROWS_JS + r'''
var out = {};
out.missing = MISSING;

var card = aiProcessCard("参数推荐 · 智能重算");
card.log(["查询同类件", "命中 CMP-SEMI-EE-BLOCK-0001（可改制，匹配度 65%）",
          "  查询条件：length=108、width=56", "  库内无同类件，按新制评估"]);
var thread = $ai("aiTinner");
var root = thread.children[thread.children.length - 1];
var steps = root.querySelector('[data-agent-role="tools"]') || root.querySelector(".oc-process-steps");
rosterOut(steps, "asm", out);
try { card.done(true); } catch (error) { out.asm_done_error = String(error && error.message || error); }
rosterOut(steps, "asm_done", out);

var cr = crCard("零件成本 · P-001");
cr.log(["查询成本库", "费率 0 条 / 回退 global 0 条 / 系数 0 条 / 待补 10 项",
        "  命中 CMP-X（可改制，匹配度 65%）"]);
var crThread = $cr("crTinner");
var crRoot = crThread.children[crThread.children.length - 1];
var crSteps = crRoot.querySelector('[data-agent-role="tools"]')
           || crRoot.querySelector(".oc-process-steps");
rosterOut(crSteps, "cr", out);
out.json_like = /\{\s*\}|"[a-z_]+"\s*:/.test(String(steps.textContent));

console.log(JSON.stringify(out));
'''


def _tech_driver() -> str:
    js = read(CHAT_JS)
    missing: list = []
    parts = [
        DOM_STUB,
        TECH_PREAMBLE,
        "var MISSING = %s;" % json.dumps(missing),
        _pick(js, "arrow", "el", missing),
        _pick(js, "fn", "modelRowKey", missing),
        _pick(js, "fn", "modelRowMap", missing),
        _pick(js, "fn", "findModelRow", missing),
        _pick(js, "fn", "applyModelOutput", missing),
        _pick(js, "fn", "mergeModelRow", missing),
        _pick(js, "fn", "pushTaskStep", missing),
        _pick(js, "fn", "setAssistantState", missing),
        TECH_TAIL,
    ]
    parts[2] = "var MISSING = %s;" % json.dumps(missing)
    return "\n".join(p for p in parts if p)


def _pick(text: str, kind: str, name: str, missing: list) -> str:
    src = function_source(text, name) if kind == "fn" else arrow_source(text, f"const {name} = ")
    if not src:
        missing.append(name)
    return src


def _board_driver() -> str:
    asm = read(ASSEMBLY)
    cost = read(COST)
    missing: list = []
    parts = [
        DOM_STUB,
        BOARD_PREAMBLE,
        "var MISSING = %s;" % json.dumps(missing),
        "var boardTurn = null;",
        _pick(asm, "fn", "aiThreadAppend", missing),
        _pick(asm, "fn", "aiUserSay", missing),
        _pick(asm, "fn", "aiProcessCard", missing),
        _pick(cost, "fn", "crAppend", missing),
        _pick(cost, "fn", "crSay", missing),
        _pick(cost, "fn", "crUserSay", missing),
        _pick(cost, "fn", "crCard", missing),
        BOARD_TAIL,
    ]
    parts[2] = "var MISSING = %s;" % json.dumps(missing)
    return "\n".join(p for p in parts if p)


def rule_body(css: str, selector: str) -> str:
    out = []
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        if selector in match.group(1):
            out.append(match.group(2))
    return "\n".join(out)


def tone_rule(css: str, tokens, tone: str) -> str:
    """返回一条「同时匹配过程行类名与色调名」且声明了 color 的规则体。"""
    best = ""
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        selector, body = match.group(1), match.group(2)
        if tone not in selector:
            continue
        if not any(tok in selector for tok in tokens):
            continue
        if "color" not in body:
            continue
        best = selector + " {" + body + "}"
    return best


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tech = run_node(_tech_driver())
        cls.board = run_node(_board_driver())
        cls.chat_js = read(CHAT_JS)
        cls.chat_css = read(CHAT_CSS)
        cls.asm = read(ASSEMBLY)
        cls.cost = read(COST)
        cls.quote = read(QUOTE)

    def nonempty(self, value, label):
        self.assertTrue(value, "%s 为空，红测脚手架失效（不是需求缺口）" % label)


class ANoDetailNoButton(Harness):
    def test_a1_no_detail_region_on_either_side(self):
        for label, out, prefix in (("技术左栏", self.tech, "after"),
                                   ("阶段页-组装整合", self.board, "asm"),
                                   ("阶段页-成本", self.board, "cr")):
            with self.subTest(where=label):
                self.assertEqual(0, out[prefix + "_detail_nodes"],
                                 "%s 仍然有可展开的「详情」区（tool-detail）：用户要求完全不要" % label)
                self.assertEqual(0, out[prefix + "_toggle_nodes"],
                                 "%s 仍然有折叠开关（tool-toggle）" % label)
                self.assertEqual(0, out[prefix + "_detail_word_nodes"],
                                 "%s 仍然渲染出「详情」字样" % label)

    def test_a2_no_button_or_disclosure_attributes(self):
        for label, out, prefix in (("技术左栏", self.tech, "after"),
                                   ("阶段页-组装整合", self.board, "asm"),
                                   ("阶段页-成本", self.board, "cr")):
            with self.subTest(where=label):
                self.assertEqual(0, out[prefix + "_button_nodes"],
                                 "%s 过程行里仍有可点按钮 / role=button" % label)
                self.assertEqual(0, out[prefix + "_aria_expanded"],
                                 "%s 过程行仍带 aria-expanded 展开语义" % label)
                self.assertEqual(0, out[prefix + "_tabindex"],
                                 "%s 过程行仍可键盘聚焦（tabindex）" % label)

    def test_a3_result_lines_are_visible_without_any_click(self):
        out = self.tech
        for label, prefix in (("技术左栏", "after"),):
            with self.subTest(where=label):
                self.assertEqual(0, out[prefix + "_hidden_nodes"],
                                 "%s 仍有 hidden 节点，结果行必须直接可见" % label)
                self.assertEqual(0, out[prefix + "_aria_hidden_nodes"],
                                 "%s 仍有 aria-hidden 节点，结果行必须直接可见" % label)
                self.assertEqual(0, out[prefix + "_closed_details"],
                                 "%s 把结果藏在收起的折叠块里，必须直接可见" % label)
        text = str(out["after_text"])
        for token in ("查询条件：length=108", "命中 CMP-SEMI-EE-BLOCK-0001",
                      "库内无同类件，按新制评估", "差异：length: 库内 120.0mm",
                      "费率 0 条 / 回退 global 0 条 / 系数 0 条 / 待补 10 项"):
            with self.subTest(token=token):
                self.assertIn(token, text, "%s 的产品侧结果没有直接显示出来" % token)

    def test_a4_sub_lines_stay_inside_their_parent_row(self):
        rows = self.tech["after_rows"] or []
        top = [r for r in rows if r.get("depth") == 0]
        self.assertTrue(top, "没有渲染出任何顶层过程行：%s" % rows)
        parent = top[2] if len(top) > 2 else top[-1]
        self.assertGreaterEqual(parent.get("nestedItems") or 0, 1,
                                "缩进的产品侧结果没有归属父行（父项里没有子项）：%s" % parent)
        self.assertIn("查询条件：length=108", str(parent.get("text")),
                      "父行文本里看不到它自己的子结果：%s" % str(parent.get("text"))[:200])
        self.assertEqual(len(top), self.tech["after_top_level"],
                         "子结果被渲染成了与父行平级的顶层行")

    def test_a5_quote_page_follows_the_same_contract(self):
        fn = function_source(self.quote, "addToolActivity")
        self.assertTrue(fn, "报价页找不到 addToolActivity（过程行渲染入口）")
        for token in ("tool-detail", "\u8be6\u60c5", "aria-expanded", "tabindex", "summary"):
            with self.subTest(token=token):
                self.assertNotIn(token, fn,
                                 "报价页过程行仍然带 %r：两侧必须同一套合同" % token)
        for pattern in ('role="button"', "'role', 'button'", '"role", "button"',
                        "'button', '0'", '"button", "0"'):
            with self.subTest(pattern=pattern):
                self.assertNotIn(pattern, fn, "报价页过程行仍是可点按钮：%r" % pattern)
        self.assertIn("tool-state-icon", fn,
                      "报价页过程行没有统一的状态图标标记")
        for dot in DOTS:
            with self.subTest(dot=dot):
                self.assertNotIn(dot, fn, "报价页过程行还在用「点」当状态图标")


class BStateIcons(Harness):
    def test_b1_done_steps_show_a_check(self):
        rows = self.tech["after_rows"] or []
        self.assertTrue(rows, "没有采到任何过程行")
        for row in rows:
            with self.subTest(state=row.get("state"), icon=row.get("icon")):
                self.assertIsNotNone(row.get("icon"),
                                     "过程行没有状态图标（行内还嵌了没有图标的重复标记？）：%r"
                                     % str(row.get("text"))[:120])
                self.assertEqual(CHECK, row.get("icon"),
                                 "完成的步骤必须是 ✓（实际 %r，data-state=%r）"
                                 % (row.get("icon"), row.get("state")))

    def test_b2_no_bullet_dots_anywhere(self):
        for label, out, prefix in (("技术左栏", self.tech, "after"),
                                   ("阶段页-组装整合", self.board, "asm"),
                                   ("阶段页-成本", self.board, "cr")):
            icons = out[prefix + "_icons"] or []
            with self.subTest(where=label):
                for icon in icons:
                    self.assertTrue(icon is None or (icon not in DOTS),
                                    "%s 仍有「点」当状态图标：%r" % (label, icon))

    def test_b3_running_and_pending_use_a_circle(self):
        for label, icons in (("技术左栏-完成前", self.tech["before_icons"]),
                             ("阶段页-完成前", self.board["asm_icons"]),
                             ("阶段页-成本", self.board["cr_icons"])):
            icons = list(icons or [])
            with self.subTest(where=label):
                self.assertTrue(icons, "%s 没有采到任何图标" % label)
                for icon in icons:
                    self.assertIn(icon, (CHECK, WARN) + CIRCLES,
                                  "%s 出现了既不是 ✓/⚠ 也不是圆圈的图标：%r" % (label, icon))

    def test_b4_failed_keeps_the_warning_icon(self):
        rows = [r for r in (self.tech["failed_rows"] or []) if r.get("state") == "failed"]
        self.assertTrue(rows, "失败行没有渲染出来")
        for row in rows:
            with self.subTest(state=row.get("state")):
                self.assertEqual("failed", row.get("state"))
                self.assertEqual(WARN, row.get("icon"), "失败行必须保留 ⚠")

    def test_b5_no_row_stays_running_after_the_card_succeeds(self):
        leftovers = [r for r in (self.tech["after_rows"] or []) if r.get("state") == "running"]
        self.assertEqual([], leftovers, "卡片已完成后仍留着 running 的行：%s" % leftovers)
        leftovers = [r for r in (self.board["asm_done_rows"] or []) if r.get("state") == "running"]
        self.assertEqual([], leftovers, "阶段页卡片完成后仍留着 running 的行：%s" % leftovers)


class CToneColors(Harness):
    def test_c1_hit_and_miss_rows_keep_their_tone_class(self):
        hit = str(self.tech["hit_class"] or "")
        miss = str(self.tech["miss_class"] or "")
        self.assertIn("hit", hit, "命中行丢了 hit 色调类：%r" % hit)
        self.assertIn("miss", miss, "未命中行丢了 miss 色调类：%r" % miss)

    def test_c2_the_tone_colors_actually_match_the_rendered_classes(self):
        for label, cls, tone in (("命中", self.tech["hit_class"], "hit"),
                                 ("未命中", self.tech["miss_class"], "miss")):
            cls = str(cls or "")
            tokens = [tok for tok in cls.split() if tok]
            body = tone_rule(self.chat_css, tokens, tone)
            with self.subTest(tone=tone):
                self.assertTrue(body,
                                "%s 行的色调规则对不上真实类名 %r：规则写了却不生效" % (label, cls))
                self.assertNotIn("oc-process-dot", body,
                                 "%s 的色调只上给了图标，没有落在文字上：%s" % (label, body))

    def test_c3_distinct_colors_still_exist(self):
        hit = tone_rule(self.chat_css, ["oc-process"], "hit")
        miss = tone_rule(self.chat_css, ["oc-process"], "miss")
        model = tone_rule(self.chat_css, ["oc-process"], "model")
        self.assertTrue(hit and miss and model, "命中 / 未命中 / 模型 的色调规则缺了：%r %r %r"
                        % (hit, miss, model))
        self.assertNotEqual(hit, miss, "命中与未命中不能是同一种颜色")

    def test_c4_board_side_keeps_the_same_tone_contract(self):
        cases = (("阶段页-组装整合", self.asm, ("aiProcessCard", "aiSay")),
                 ("阶段页-成本", self.cost, ("crCard", "crSay")))
        for label, text, names in cases:
            body = "\n".join(function_source(text, name) for name in names)
            with self.subTest(where=label):
                self.assertTrue(body.strip(), "%s 找不到过程行渲染入口" % label)
                self.assertRegex(body, r"hit", "%s 没有命中色调，两侧不一致" % label)
                self.assertRegex(body, r"miss", "%s 没有未命中色调，两侧不一致" % label)
                self.assertRegex(body, r"tool-state-icon", "%s 没有统一状态图标标记" % label)


class DNoTechnicalLeak(Harness):
    def test_d1_no_input_output_json_in_the_rows(self):
        self.assertFalse(self.tech["json_like"],
                         "过程行里仍然显示输入 / 输出 JSON（含空 {}/{}）")
        self.assertFalse(self.board["json_like"],
                         "阶段页过程行里仍然显示 JSON")
        for token in ("oc-process-detail-label", "oc-process-detail-input",
                      "oc-process-detail-output"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.chat_js, "技术左栏仍在渲染 %s" % token)

    def test_d2_no_raw_tool_identifiers(self):
        self.assertFalse(self.tech["raw_tool_id"],
                         "过程行里暴露了原始工具名：%s" % str(self.tech["after_text"])[:300])
        text = str(self.tech["after_text"])
        for token in RAW_TOOL_IDS:
            with self.subTest(token=token):
                self.assertNotIn(token, text, "过程行里出现了技术标识 %r" % token)

    def test_d3_product_side_results_are_kept(self):
        text = str(self.tech["after_text"])
        for token in ("读取输入：source.png", "调用多模态模型解析图纸（qwen3.5-plus）",
                      "命中 CMP-SEMI-EE-BLOCK-0001 搬运吸嘴主体安装块（可改制，匹配度 65%）",
                      "待补 10 项"):
            with self.subTest(token=token):
                self.assertIn(token, text, "产品侧结果 %r 被删掉了" % token)
