"""红测：过程行「点标题行展开」恢复 + 图标同行 + 完成后全部变 ✓。

用户口径（本批，针对 ## 133 之后的现状）：
  1. **每行标题可以点击展开**的交互要恢复（## 133 把它去掉了）；
  2. 圆圈后面不该换行才是内容 —— 图标与标题必须在同一行；
  3. **查询条件 / 命中 / 差异这些结果行必须折进它们所属的标题行**里（默认收起）；
  4. 卡片做完之后，**这些标题行也要变成 ✓**（现状仍是 ○）。
  其余（没有「详情」字样、没有输入输出 JSON、图标 ✓/○/⚠、命中绿 / 未命中橙、
  报价与技术工艺同一套）保持 ## 133 的口径不变。

当前缺口（实现前天然失败）：
  · `agent-chat.js::pushTaskStep` 在 ## 133 里把折叠区整块删掉，缩进行改成
    `.oc-process-subs` 直接可见，标题行也不再有 `tool-toggle` / `aria-expanded`；
  · `setAssistantState()` 收尾翻转只找 `ctx.steps || ctx.tools`，而任务卡是把
    `oc-task-steps` 挂到 `turn.body`（`ensureTaskCard`），于是解析那类卡片里
    仍是「进行中」的行不会翻成 ✓；
  · 缩进行在父行里直接可见，没有折进父行。

Spec：docs/specs/quote-tech-process-row-fold-restore.md
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

from tests.test_quote_tech_process_row_product_contract_red import (  # noqa: F401
    DOTS,
    CHECK,
    WARN,
    rule_body,
)
from tests.test_quote_tech_unified_tool_list_conversation_red import (
    BOARD_PREAMBLE,
    DOM_STUB,
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

TECH_PREAMBLE = r'''
var doc = makeDoc();
var document = doc;
var T = doc.createElement("div");
function scrollDown() {}
function boardStage() { return "process"; }
'''

PROBE = r'''
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
function isCollapsed(node) {
  if (!node) return null;
  if (node.tagName === "DETAILS") return !node.open;
  return !!(node.hidden || node.hasAttribute("hidden")
            || node.getAttribute("aria-hidden") === "true");
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
        found.push({depth: depth, cls: String(child.className),
                    state: child.getAttribute("data-state"),
                    icon: icon ? String(icon.textContent).trim() : null,
                    text: String(child.textContent)});
        walk(child, depth + 1);
      } else { walk(child, depth); }
    }
  }
  walk(root, 0);
  return found;
}
function probe(root, out) {
  var rows = rowsOf(root);
  out.rows = rows;
  out.icons = rows.map(function (r) { return r.icon; });
  out.states = rows.map(function (r) { return r.state; });
  out.top_level = root.children.length;
  out.detail_nodes = root.querySelectorAll('[data-agent-role="tool-detail"]').length;
  out.toggle_nodes = root.querySelectorAll('[data-agent-role="tool-toggle"]').length;
  out.detail_word_nodes = countOwnText(root, "\u8be6\u60c5");
  out.detail_collapsed = (function () {
    var box = root.querySelector('[data-agent-role="tool-detail"]');
    return box ? isCollapsed(box) : null;
  })();
  out.subs_inside_detail = (function () {
    var box = root.querySelector('[data-agent-role="tool-detail"]');
    if (!box) return false;
    return box.querySelectorAll('[data-agent-role="tool-item"]').length > 0;
  })();
  out.toggle_has_title = (function () {
    var n = root.querySelector('[data-agent-role="tool-toggle"]');
    return !!(n && n.querySelector
              && (n.querySelector('[data-agent-role="tool-title"]') || ownText(n)));
  })();
  out.toggle_aria = (function () {
    var n = root.querySelector('[data-agent-role="tool-toggle"]');
    return n ? n.getAttribute("aria-expanded") : null;
  })();
  out.toggle_keyboard = (function () {
    var n = root.querySelector('[data-agent-role="tool-toggle"]');
    if (!n) return null;
    if (n.tagName === "BUTTON" || n.tagName === "SUMMARY") return true;
    return n.getAttribute("role") === "button" && n.getAttribute("tabindex") === "0";
  })();
  out.click_expands = (function () {
    var n = root.querySelector('[data-agent-role="tool-toggle"]');
    if (!n) return null;
    var before = isCollapsed(root.querySelector('[data-agent-role="tool-detail"]'));
    n.dispatch("click");
    var after = isCollapsed(root.querySelector('[data-agent-role="tool-detail"]'));
    return {before: before, after: after,
            aria: n.getAttribute("aria-expanded")};
  })();
  out.text = String(root.textContent);
  out.json_like = /\{\s*\}|"[a-z_]+"\s*:/.test(out.text);
  out.raw_tool_id = /component_match|vision_parse|LookupComponentLibrary/.test(out.text);
  out.hit_class = (function () {
    for (var i = 0; i < rows.length; i += 1) {
      if (rows[i].text.indexOf("\u547d\u4e2d") >= 0) return rows[i].cls;
    }
    return null;
  })();
  out.miss_class = (function () {
    for (var i = 0; i < rows.length; i += 1) {
      if (rows[i].text.indexOf("\u65e0\u540c\u7c7b\u4ef6") >= 0) return rows[i].cls;
    }
    return null;
  })();
  return out;
}
function titleText(row) {
  var n = row.querySelector ? row.querySelector('[data-agent-role="tool-title"]') : null;
  return n ? String(n.textContent) : "";
}
'''

TECH_TAIL = PROBE + r'''
var out = {};
out.missing = MISSING;

// 复刻真实的「解析」卡：任务卡把过程行挂在 turn.body 上的 .oc-task-steps 里。
var wrap = doc.createElement("div");
var body = doc.createElement("div");
var chip = doc.createElement("span");
wrap.append(body);
var steps = doc.createElement("div");
steps.className = "oc-task-steps";
body.append(steps);
var card = { steps: steps, status: "running", box: doc.createElement("div"),
             state: chip, wrap: wrap, body: body };
var ctx = { wrap: wrap, body: body, state: chip };   // 注意：没有 steps / tools

pushTaskStep(card, "检索零部件库开始", "", "tool");
pushTaskStep(card, "检索零部件库（1/4）：P-001 上壳", "", "tool",
  { tool: "component_match", title: "零部件库检索", status: "running",
    input: { part_id: "P-001" }, output: {} });
pushTaskStep(card, "  查询条件：length=108、width=56、height=13.25、hole_diameter=4", "", "tool");
pushTaskStep(card, "  命中 CMP-SEMI-EE-BLOCK-0001 搬运吸嘴主体安装块（可改制，匹配度 65%）", "", "tool");
pushTaskStep(card, "  差异：length: 库内 120.0mm / 图纸 108.0", "", "tool");
pushTaskStep(card, "检索零部件库（2/4）：P-002 下壳", "", "tool",
  { tool: "component_match", title: "零部件库检索", status: "running" });
pushTaskStep(card, "  库内无同类件，按新制评估", "", "tool");
pushTaskStep(card, "零部件库检索完成：可复用 0、可改制 2、未匹配 2", "", "");

out.parent_title = (function () {
  var rows = rowsOf(steps);
  for (var i = 0; i < rows.length; i += 1) {
    if (rows[i].text.indexOf("检索零部件库（1/4）") >= 0) {
      return rows[i].text;
    }
  }
  return null;
})();
out.sub_title_raw = (function () {
  var all = steps.querySelectorAll('[data-agent-role="tool-title"]');
  for (var i = 0; i < all.length; i += 1) {
    if (String(all[i].textContent).indexOf("查询条件") >= 0) return String(all[i].textContent);
  }
  return null;
})();
out.icon_title_siblings = (function () {
  var all = steps.querySelectorAll('[data-agent-role="tool-item"]');
  for (var i = 0; i < all.length; i += 1) {
    var node = all[i];
    if (node.children.length < 2) continue;
    if (node.children[0].getAttribute("data-agent-role") !== "tool-state-icon") continue;
    if (ownText(node.children[0]).trim() === "") continue;
    return node.children[1].getAttribute("data-agent-role") === "tool-title";
  }
  return null;
})();

probe(steps, out);

setAssistantState(ctx, "succeeded");
out.after = {};
probe(steps, out.after);

var failSteps = doc.createElement("div");
failSteps.className = "oc-task-steps";
var failCard = { steps: failSteps, status: "running", box: doc.createElement("div"),
                 state: doc.createElement("span") };
pushTaskStep(failCard, "检索工艺库：P-003 电芯组支架", "", "err",
             { tool: "lookup_process", status: "failed" });
setAssistantState({ state: doc.createElement("span"), wrap: doc.createElement("div"),
                    body: doc.createElement("div") }, "succeeded");
out.failed_icons = rowsOf(failSteps).map(function (r) { return r.icon; });

console.log(JSON.stringify(out));
'''

BOARD_TAIL = PROBE + r'''
var out = {};
out.missing = MISSING;
var card = aiProcessCard("参数推荐 · 智能重算");
card.log(["查询同类件", "查询条件：length=108、width=56",
          "  命中 CMP-SEMI-EE-BLOCK-0001（可改制，匹配度 65%）",
          "  库内无同类件，按新制评估"]);
var thread = $ai("aiTinner");
var root = thread.children[thread.children.length - 1];
var steps = root.querySelector('[data-agent-role="tools"]') || root.querySelector(".oc-process-steps");
var asmOut = {};
probe(steps, asmOut);
out.asm = asmOut;

var cr = crCard("零件成本 · P-001");
cr.log(["查询成本库", "  命中 CMP-X（可改制，匹配度 65%）"]);
var crThread = $cr("crTinner");
var crRoot = crThread.children[crThread.children.length - 1];
var crSteps = crRoot.querySelector('[data-agent-role="tools"]')
           || crRoot.querySelector(".oc-process-steps");
out.cr = {};
probe(crSteps, out.cr);

console.log(JSON.stringify(out));
'''


def _pick(text: str, kind: str, name: str, missing: list) -> str:
    src = function_source(text, name) if kind == "fn" else arrow_source(text, f"const {name} = ")
    if not src:
        missing.append(name)
    return src


def tech_driver() -> str:
    js = read(CHAT_JS)
    missing: list = []
    parts = [
        DOM_STUB, TECH_PREAMBLE,
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


def board_driver() -> str:
    asm, cost = read(ASSEMBLY), read(COST)
    missing: list = []
    parts = [
        DOM_STUB, BOARD_PREAMBLE,
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
    parts[2] = "var MISSING = %s" % json.dumps(missing)
    return "\n".join(p for p in parts if p)


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tech = run_node(tech_driver())
        cls.board = run_node(board_driver())
        cls.chat_js = read(CHAT_JS)
        cls.chat_css = read(CHAT_CSS)
        cls.quote = read(QUOTE)


class AFoldRestored(Harness):
    def test_a1_result_rows_are_folded_into_the_title_row(self):
        out = self.tech
        self.assertGreaterEqual(out["detail_nodes"], 1,
                                "结果行没有折进所属标题行（没有任何折叠区）：%s"
                                % str(out["parent_title"])[:120])
        self.assertTrue(out["subs_inside_detail"],
                        "查询条件 / 命中 / 差异没有成为标题行折叠区里的子项")
        self.assertEqual(2, out["top_level"],
                         "结果行仍然是与父行平级的顶层行（顶层行数 %s）" % out["top_level"])

    def test_a2_fold_defaults_to_collapsed_and_the_title_row_toggles(self):
        out = self.tech
        self.assertIs(True, out["detail_collapsed"],
                      "折叠区不是默认收起：%r" % out["detail_collapsed"])
        self.assertTrue(out["toggle_has_title"],
                        "标题行本身不是展开开关（找不到 tool-toggle + tool-title）")
        self.assertIn(out["toggle_aria"], ("false", "true"),
                      "标题行没有 aria-expanded：%r" % out["toggle_aria"])
        self.assertTrue(out["toggle_keyboard"],
                        "标题行不能用键盘展开：%r" % out["toggle_keyboard"])
        click = out["click_expands"] or {}
        self.assertIs(True, click.get("before"), "点击前折叠区不是收起的：%r" % click)
        self.assertIs(False, click.get("after"), "点击标题行没有展开：%r" % click)
        self.assertEqual("true", click.get("aria"), "展开后 aria-expanded 没同步：%r" % click)

    def test_a3_no_detail_label_and_no_technical_payload(self):
        out = self.tech
        self.assertEqual(0, out["detail_word_nodes"], "界面上又出现了「详情」字样")
        self.assertFalse(out["json_like"], "过程行里又出现了输入 / 输出 JSON")
        self.assertFalse(out["raw_tool_id"], "过程行里又出现了原始工具名")

    def test_a4_board_side_folds_the_same_way(self):
        for label, out in (("3 阶段页", self.board.get("asm") or {}),
                           ("4 阶段页", self.board.get("cr") or {})):
            with self.subTest(where=label):
                self.assertGreaterEqual(out.get("detail_nodes") or 0, 1,
                                        "%s 结果行没有折进标题行" % label)
                self.assertIs(True, out.get("detail_collapsed"),
                              "%s 折叠区不是默认收起：%r" % (label, out.get("detail_collapsed")))
                self.assertTrue(out.get("toggle_has_title"), "%s 标题行不是开关" % label)
                self.assertEqual(0, out.get("detail_word_nodes") or 0,
                                 "%s 出现了「详情」字样" % label)

    def test_a5_quote_page_title_row_is_the_toggle(self):
        fn = function_source(self.quote, "addToolActivity")
        self.assertTrue(fn, "报价页找不到 addToolActivity")
        self.assertNotIn("详情", fn, "报价页过程行又挂上了「详情」字样")
        for token in ("tool-toggle", "aria-expanded"):
            with self.subTest(token=token):
                self.assertIn(token, fn, "报价页标题行不是展开开关：缺 %r" % token)


class BSameLine(Harness):
    def test_b1_icon_and_title_share_one_line(self):
        out = self.tech
        self.assertIs(True, out["icon_title_siblings"],
                      "图标与标题不是同一行的兄弟节点（图标被挤到单独一行）")
        raw = str(out["sub_title_raw"] or "")
        self.assertTrue(raw, "没有采到结果行的标题")
        self.assertEqual(raw.strip(), raw,
                         "结果行标题带着多余的前导空白 / 换行：%r" % raw)

    def test_b2_css_does_not_push_the_icon_onto_its_own_line(self):
        row = rule_body(self.chat_css, ".oc-process-step")
        self.assertRegex(row, r"display\s*:\s*flex", "过程行不是横向 flex 布局：%s" % row[:200])
        icon = rule_body(self.chat_css, ".oc-process-dot")
        self.assertTrue(icon, "找不到 .oc-process-dot 规则")
        for bad in ("flex-basis: 100%", "width: 100%", "display: block"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, icon.replace("  ", " "),
                                 "图标被设成占满整行，内容必然换行：%s" % icon)


class CDoneState(Harness):
    def test_c1_every_row_becomes_a_check_when_the_card_succeeds(self):
        after = self.tech["after"] or {}
        rows = after.get("rows") or []
        self.assertTrue(rows, "完成后没有采到任何过程行")
        for row in rows:
            with self.subTest(state=row.get("state"), icon=row.get("icon")):
                self.assertEqual("completed", row.get("state"),
                                 "卡片已完成，行仍是 %r：%s"
                                 % (row.get("state"), str(row.get("text"))[:80]))
                self.assertEqual(CHECK, row.get("icon"),
                                 "卡片已完成，行图标仍是 %r" % row.get("icon"))

    def test_c2_results_rows_flip_together_with_their_parent(self):
        after = self.tech["after"] or {}
        icons = after.get("icons") or []
        self.assertNotIn("\u25cb", icons, "完成后仍留着圆圈：%s" % icons)
        self.assertNotIn("\u25cc", icons, "完成后仍留着圆圈：%s" % icons)

    def test_c3_failed_rows_keep_the_warning_icon(self):
        icons = [i for i in (self.tech["failed_icons"] or []) if i]
        self.assertTrue(icons, "失败行没有图标")
        for icon in icons:
            with self.subTest(icon=icon):
                self.assertEqual(WARN, icon, "失败行丢了 ⚠")

    def test_c4_no_bullet_dots_regression(self):
        for label, icons in (("技术左栏", self.tech["icons"]),
                             ("技术左栏-完成后", (self.tech["after"] or {}).get("icons")),
                             ("3 阶段页", (self.board.get("asm") or {}).get("icons")),
                             ("4 阶段页", (self.board.get("cr") or {}).get("icons"))):
            for icon in (icons or []):
                with self.subTest(where=label, icon=icon):
                    self.assertNotIn(icon, DOTS, "%s 又出现「点」图标：%r" % (label, icon))


class DToneKept(Harness):
    def test_d1_hit_and_miss_tones_survive_the_fold(self):
        for label, cls, tone in (("命中", self.tech["hit_class"], "hit"),
                                 ("未命中", self.tech["miss_class"], "miss")):
            body = rule_body(self.chat_css, "." + tone)
            with self.subTest(tone=tone):
                self.assertIn(tone, str(cls or ""), "%s 行丢了色调类：%r" % (label, cls))
                self.assertTrue(body, "CSS 里没有 .%s 的色调规则" % tone)
