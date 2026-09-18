"""红测：过程行「运行中必须是圆圈 / 有内容必须折叠 / 状态展示行用 ·」（批次 ## 142）。

用户口径（原文要点，针对 ## 137 落地后的现状）：

  1. 「正在的时候应该是圆圈，但是实际上正在的时候就已经是 ✓ 了」；
  2. 「里面还是有多余的很奇怪的缩进和换行」；
  3. 「这些并没有折叠展开而是都还是显示出来」；
  4. 「对于里面的详情，如果是一个任务步骤就 ✓，展示状态的不需要，展示状态的就用 · 就行了」，
     例如「产品族 …：报价成品参数 14/25 已给出（必填 4/11）」「报价必填项仍缺：产品系列、…」。

现状缺口（node 实跑真渲染器，不是推断）：`tests/test_process_row_running_info_and_fold_red.py`
的场景 S1/S4 复现如下 ——

  · **一上来就 ✓**：`agent-chat.js::pushTaskStep` 的
    `itemState = … : (detail && status === 'running') ? 'running' : 'completed'`
    —— 没有显式状态的行一律按「完成」渲染。于是卡片还在跑时，
    「汇总 2.1 零件…」「正在调用模型推荐整机参数与整合方案」「参数 25 条、连接 3 处、BOM 5 行」
    全是 ✓（实测 `icons = ['○','✓','✓','✓','✓','✓','✓']`）。
  · **裸子节点 = 既不能折叠、又继承父行 flex**：`opensCall` 分支把先到的普通行
    `item.append(row)` 直接挂成调用行的裸子节点（没有 `tool-detail`、没有开关、不是收起态）。
    实测该行 `tool-detail` 只有 1 个（属于另一行），裸子节点 2 个、可见行 5 行 ——
    用户看到的就是「多余缩进 / 换行 + 没有折叠、全部都显示出来」。
  · **状态展示行也是 ✓**：「产品族 …已给出（必填 4/11）」「报价必填项仍缺：…」
    「共 N 道组装工序：…」「参数 N 条、连接 N 处、BOM N 行」这些陈述统计行没有独立的行类型，
    只能跟着步骤行翻 ✓。

目标合同（Spec：`docs/specs/process-row-running-info-and-fold.md`）：

  A. 卡片未到终态时，未标注终态的过程行一律 `○`（`data-state="running"`）；
     卡片成功收尾后才翻 `✓`；卡片一开始就是成功终态（历史回放）时直接 `✓`。
  B. 有子内容的行必须有折叠区：子行落在 `[data-agent-role="tool-detail"]` 内、默认收起、
     标题行即开关；**不得**把子行裸挂成父行的直接子节点。
  C. 行分两类：**任务步骤行** `○/✓/⚠`；**状态展示行**（统计 / 覆盖率 / 汇总陈述）
     一律 `·`（`data-state="info"`），不参与收尾翻转。显式标注 `detail.kind='info'` 优先，
     历史文本按封闭句式兜底。
  D. 文本不得带前导空白 / `↳`；其余 ## 133 / ## 136 合同（无「详情」、无输入输出 JSON、
     图标与标题同行、命中绿 / 未命中橙）不变。

用户明确要求「不只是图纸解析的输出气泡，而是报价和技术工艺所有的都应该是统一的」，
所以红测分两组档位：

  · A/B/C/D 组：技术工艺左栏 `agent-chat.js::pushTaskStep`（用户现场 3.2 / 3.3 的入口）；
  · E 组：另外三个过程行入口必须同一合同 —— 3 阶段页 `assembly-integration.js::aiProcessCard`、
    4 阶段页 `cost-review.js::crCard`、报价页 `确认需求解析结果.html::addToolActivity`。
    实测缺口：`aiProcessCard` / `crCard` 的 `rowFor()` 把每一行硬编码成
    `data-state="completed"` + ✓（卡片还在跑就已经是 ✓），且三个入口都没有 `·` 状态展示行；
    报价页 `makeItem()` 运行中是 ○（正确），但 `markTraceDone()` 会把状态展示行也翻成 ✓。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import unittest

from tests.test_quote_tech_process_row_product_contract_red import (  # noqa: F401
    CIRCLES,
    DOTS,
    CHECK,
    WARN,
)
from tests.test_quote_tech_process_row_fold_and_done_red import _pick  # noqa: F401
from tests.test_quote_tech_unified_tool_list_conversation_red import (  # noqa: F401
    DOM_STUB,
    board_driver as _unified_board_driver,
    function_source,
    read,
    run_node,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
CHAT_JS = FRONTEND / "agent-chat.js"
ASSEMBLY = FRONTEND / "assembly-integration.js"
COST = FRONTEND / "cost-review.js"
QUOTE = ROOT / "确认需求解析结果.html"

DOT = "\u00b7"          # · 状态展示行专用
ARROW = "\u21b3"        # ↳

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
function roleOf(node) {
  return (node && node.getAttribute) ? node.getAttribute("data-agent-role") : null;
}
function ancestors(node) {
  var out = [];
  var n = node ? node.parentNode : null;
  while (n) { out.push(n); n = n.parentNode; }
  return out;
}
function inDetailBox(node) {
  var chain = ancestors(node);
  for (var i = 0; i < chain.length; i += 1) { if (roleOf(chain[i]) === "tool-detail") return true; }
  return false;
}
function bareUnderItem(node) {
  // 直接挂在别的 tool-item 上（中间没有 tool-detail）=> 裸子节点
  var chain = ancestors(node);
  for (var i = 0; i < chain.length; i += 1) {
    if (roleOf(chain[i]) === "tool-detail") return false;
    if (roleOf(chain[i]) === "tool-item") return true;
  }
  return false;
}
function hiddenByAncestor(node) {
  var chain = ancestors(node);
  for (var i = 0; i < chain.length; i += 1) {
    var n = chain[i];
    if (n.hidden) return true;
    if (n.hasAttribute && (n.hasAttribute("hidden")
        || n.getAttribute("aria-hidden") === "true")) return true;
  }
  return false;
}
function titleOf(node) {
  // 标题行即开关时，原句被搬进 tool-toggle 子节点里，必须用 textContent 取全；
  // 没有开关的普通行，原句就在 tool-title 自己的 _text 上。
  var t = node.querySelector ? node.querySelector('[data-agent-role="tool-title"]') : null;
  return t ? String(t.textContent) : null;
}
function roster(root) {
  var rows = [];
  var all = root.querySelectorAll('[data-agent-role="tool-item"]');
  for (var i = 0; i < all.length; i += 1) {
    var node = all[i];
    var icon = node.querySelector('[data-agent-role="tool-state-icon"]');
    rows.push({
      idx: i,
      state: node.getAttribute("data-state"),
      icon: icon ? String(icon.textContent).trim() : null,
      title: titleOf(node),
      folded: inDetailBox(node),
      bare: bareUnderItem(node),
      visible: !hiddenByAncestor(node),
      detail: node.querySelectorAll('[data-agent-role="tool-detail"]').length,
      toggle: node.querySelectorAll('[data-agent-role="tool-toggle"]').length
    });
  }
  return rows;
}
function summarise(root) {
  var rows = roster(root);
  var details = root.querySelectorAll('[data-agent-role="tool-detail"]');
  var collapsed = 0;
  for (var i = 0; i < details.length; i += 1) {
    var d = details[i];
    if (d.hidden || (d.hasAttribute && (d.hasAttribute("hidden")
        || d.getAttribute("aria-hidden") === "true"))) collapsed += 1;
  }
  return {
    rows: rows,
    icons: rows.map(function (r) { return r.icon; }),
    states: rows.map(function (r) { return r.state; }),
    titles: rows.map(function (r) { return r.title; }),
    bare: rows.filter(function (r) { return r.bare; }).length,
    visible: rows.filter(function (r) { return r.visible; }).length,
    folded: rows.filter(function (r) { return r.folded; }).length,
    details: details.length,
    toggles: root.querySelectorAll('[data-agent-role="tool-toggle"]').length,
    detail_collapsed: details.length > 0 && collapsed === details.length,
    top: root.children.length,
    text: String(root.textContent)
  };
}
function byTitle(sum, needle) {
  for (var i = 0; i < sum.rows.length; i += 1) {
    if (String(sum.rows[i].title || "").indexOf(needle) >= 0) return sum.rows[i];
  }
  return null;
}
function newCard(status) {
  var wrap = doc.createElement("div");
  var body = doc.createElement("div");
  var steps = doc.createElement("div");
  steps.className = "oc-task-steps";
  wrap.append(body);
  body.append(steps);
  return { wrap: wrap, body: body, steps: steps,
           card: { steps: steps, status: status || "running", box: doc.createElement("div"),
                   state: doc.createElement("span"), wrap: wrap, body: body } };
}
function finish(holder) {
  setAssistantState({ state: doc.createElement("span"), wrap: holder.wrap, body: holder.body },
                    "succeeded");
}
'''

TAIL = PROBE + r'''
var out = {};

// ---- S1：用户现场「3.2 参数推荐」的实时序列（模型行带 detail，进度行不带） ----
var S1 = newCard("running");
pushTaskStep(S1.card, "汇总 2.1 零件、1.x 需求与本步整合图纸", "", "progress");
pushTaskStep(S1.card, "载入报价成品参数字典（46 个字段 / 5 个产品族）", "", "progress");
pushTaskStep(S1.card, "调用模型（qwen3.5-plus）", "", "model",
             { call: "c1", status: "running", title: "调用模型" });
pushTaskStep(S1.card, "正在调用模型推荐整机参数与整合方案", "", "progress");
pushTaskStep(S1.card, "参数 25 条、连接 3 处、BOM 5 行", "", "progress");
pushTaskStep(S1.card, "  ↳ 产品族 锂离子电池包 / 模组：报价成品参数 14/25 已给出（必填 4/11）",
             "", "progress");
pushTaskStep(S1.card, "  ↳ 报价必填项仍缺：产品系列、产品型号、成品编码、标称电压、标称容量",
             "", "progress");
out.s1_running = summarise(S1.steps);
finish(S1);
out.s1_done = summarise(S1.steps);

// ---- S2：历史回放（卡片一开始就是成功终态） ----
var S2 = newCard("succeeded");
pushTaskStep(S2.card, "汇总 2.1 零件、1.x 需求与本步整合图纸", "", "progress");
pushTaskStep(S2.card, "参数 25 条、连接 3 处、BOM 5 行", "", "progress");
out.s2_replay = summarise(S2.steps);

// ---- S3：用户现场「3.3 组装工艺」的老口径文本行（progress_log，无 detail） ----
var S3 = newCard("running");
[
  "检索工艺库：ASSY 便携设备锂电池 PACK（类别 电池包／材料 未定）",
  "  ↳ 命中路线 RT-PLATE-MACHINED 板类机加工零件典型路线（按类别/材料/批量召回，7 道工序）",
  "正在调用模型编制组装工序明细",
  "  ↳ 共 5 道组装工序：沿用库内 0 道、缺失需新建 5 道"
].forEach(function (line) { pushTaskStep(S3.card, line, "", "progress"); });
out.s3_text = summarise(S3.steps);
finish(S3);
out.s3_done = summarise(S3.steps);

// ---- S4：用户点名的两条「状态展示行」+ 同族统计行 + 反例 ----
var S4 = newCard("running");
pushTaskStep(S4.card, "查询成本库", "", "tool",
             { tool: "cost_lookup", status: "running" });
pushTaskStep(S4.card, "  ↳ 产品族 锂离子电池包 / 模组：报价成品参数 14/25 已给出（必填 4/11）", "", "");
pushTaskStep(S4.card, "  ↳ 报价必填项仍缺：产品系列、产品型号、成品编码", "", "");
pushTaskStep(S4.card, "  ↳ 共 5 道组装工序：沿用库内 0 道、缺失需新建 5 道", "", "");
pushTaskStep(S4.card, "参数 25 条、连接 3 处、BOM 5 行", "", "");
pushTaskStep(S4.card, "  ↳ 查询条件：length=108、width=56", "", "");
pushTaskStep(S4.card, "  ↳ 命中 CMP-SEMI-EE-BLOCK-0001（可改制，匹配度 65%）", "", "");
pushTaskStep(S4.card, "  ↳ 库内无同类件，按新制评估", "", "");
pushTaskStep(S4.card, "检索工艺库：ASSY 便携设备锂电池 PACK", "", "");
pushTaskStep(S4.card, "正在调用模型编制组装工序明细", "", "");
out.s4 = summarise(S4.steps);
finish(S4);
out.s4_done = summarise(S4.steps);

// ---- S5：显式标注 detail.kind = 'info' ----
var S5 = newCard("running");
pushTaskStep(S5.card, "报价成品参数覆盖率：14/25", "", "progress", { kind: "info" });
pushTaskStep(S5.card, "检索工艺库：ASSY", "", "progress", { kind: "step" });
out.s5 = summarise(S5.steps);
finish(S5);
out.s5_done = summarise(S5.steps);

console.log(JSON.stringify(out));
'''


def driver() -> str:
    js = read(CHAT_JS)
    missing: list = []
    parts = [
        DOM_STUB, TECH_PREAMBLE,
        "var MISSING = [];",
        _pick(js, "arrow", "el", missing),
        _pick(js, "fn", "modelRowKey", missing),
        _pick(js, "fn", "modelRowMap", missing),
        _pick(js, "fn", "findModelRow", missing),
        _pick(js, "fn", "applyModelOutput", missing),
        _pick(js, "fn", "mergeModelRow", missing),
        _pick(js, "fn", "pushTaskStep", missing),
        _pick(js, "fn", "setAssistantState", missing),
        TAIL,
    ]
    parts[2] = "var MISSING = %s;" % json.dumps(missing)
    return "\n".join(p for p in parts if p)


DATA = None


def out() -> dict:
    global DATA
    if DATA is None:
        DATA = run_node(driver())
    return DATA


def row(summary: dict, needle: str) -> dict:
    for item in summary.get("rows") or []:
        if needle in str(item.get("title") or ""):
            return item
    raise AssertionError("找不到过程行：%r（现有：%r）"
                         % (needle, [r.get("title") for r in summary.get("rows") or []]))


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = out()
        cls.board = run_node(board_driver())
        cls.quote = run_node(quote_driver())
        cls.missing = cls.data.get("missing") if isinstance(cls.data, dict) else None
        cls.js = read(CHAT_JS)

    def test_harness_is_complete(self):
        """脚手架自检：抽不到函数时失败必须落在脚手架，不是需求。"""
        self.assertEqual([], self.data.get("missing") or [],
                         "agent-chat.js 里抽不到渲染函数：%s" % self.data.get("missing"))


class ARunningUsesCircle(Harness):
    def test_a1_rows_stay_circles_while_the_card_is_running(self):
        summary = self.data["s1_running"]
        for needle in ("汇总 2.1 零件", "载入报价成品参数字典", "调用模型（qwen3.5-plus）",
                       "正在调用模型推荐整机参数与整合方案"):
            with self.subTest(row=needle):
                item = row(summary, needle)
                self.assertEqual("running", item.get("state"),
                                 "卡片还在跑，行已经是终态 %r：%s"
                                 % (item.get("state"), item.get("title")))
                self.assertIn(item.get("icon"), CIRCLES,
                              "运行中的行必须是圆圈，实际 %r" % item.get("icon"))
        self.assertNotIn(CHECK, summary.get("icons") or [],
                         "卡片还在跑，已经有行显示 ✓：%s" % summary.get("icons"))

    def test_a2_rows_turn_into_checks_when_the_card_succeeds(self):
        summary = self.data["s1_done"]
        for needle in ("汇总 2.1 零件", "载入报价成品参数字典", "调用模型（qwen3.5-plus）",
                       "正在调用模型推荐整机参数与整合方案"):
            with self.subTest(row=needle):
                item = row(summary, needle)
                self.assertEqual("completed", item.get("state"),
                                 "卡片成功后行没有收成完成：%r" % item.get("state"))
                self.assertEqual(CHECK, item.get("icon"), "卡片成功后行没有变 ✓")

    def test_a3_replayed_finished_card_shows_checks_immediately(self):
        summary = self.data["s2_replay"]
        self.assertTrue(summary.get("rows"), "历史回放没有渲染出任何过程行")
        for item in summary.get("rows") or []:
            with self.subTest(row=item.get("title")):
                self.assertEqual("completed", item.get("state"),
                                 "历史回放的已成功卡片里还留着非完成态 %r" % item.get("state"))
                self.assertEqual(CHECK, item.get("icon"),
                                 "历史回放的已成功卡片里行图标是 %r" % item.get("icon"))


class BFoldIsARealFold(Harness):
    def test_b1_no_row_is_a_bare_child_of_another_row(self):
        for key in ("s1_running", "s3_text", "s4"):
            with self.subTest(scene=key):
                self.assertEqual(0, self.data[key].get("bare") or 0,
                                 "有过程行被裸挂成父行的直接子节点（既不能折叠、"
                                 "又会继承父行 flex 布局）：%s"
                                 % [r.get("title") for r in self.data[key]["rows"]
                                    if r.get("bare")])

    def test_b2_every_nested_row_lives_in_a_collapsed_detail_box(self):
        for key in ("s1_running", "s4"):
            with self.subTest(scene=key):
                summary = self.data[key]
                self.assertGreaterEqual(summary.get("folded") or 0, 1,
                                        "没有任何子行落在折叠区里：%s" % summary.get("rows"))
                self.assertTrue(summary.get("detail_collapsed"),
                                "折叠区不是默认收起（用户看到的就是「全都显示出来」）：%r"
                                % summary.get("detail_collapsed"))
                self.assertEqual(summary.get("details"), summary.get("toggles"),
                                 "折叠区与标题行开关没有成对出现：details=%r toggles=%r"
                                 % (summary.get("details"), summary.get("toggles")))

    def test_b3_folded_rows_are_hidden_until_expanded(self):
        summary = self.data["s1_running"]
        nested = [r for r in summary["rows"] if r.get("folded")]
        self.assertTrue(nested, "没有采到折叠区里的行")
        for item in nested:
            with self.subTest(row=item.get("title")):
                self.assertFalse(item.get("visible"),
                                 "折叠区里的行仍然可见（没有折叠）：%r" % item.get("title"))

    def test_b4_row_titles_have_no_indent_or_arrow(self):
        for key in ("s1_running", "s3_text", "s4"):
            for item in self.data[key]["rows"]:
                with self.subTest(scene=key, row=item.get("title")):
                    title = str(item.get("title") or "")
                    self.assertTrue(title, "过程行没有标题：%r" % item)
                    self.assertEqual(title.strip(), title,
                                     "标题带着多余的前导 / 尾随空白：%r" % title)
                    self.assertNotIn(ARROW, title, "标题里还留着「↳」：%r" % title)


class CInfoRowsUseDot(Harness):
    def test_c1_explicit_info_kind_renders_a_dot(self):
        before = row(self.data["s5"], "报价成品参数覆盖率")
        self.assertEqual(DOT, before.get("icon"),
                         "detail.kind='info' 的行必须用 ·（实际 %r）" % before.get("icon"))
        after = row(self.data["s5_done"], "报价成品参数覆盖率")
        self.assertEqual(DOT, after.get("icon"),
                         "状态展示行在卡片完成后仍然是 ·（不得翻成 ✓）")
        step = row(self.data["s5_done"], "检索工艺库")
        self.assertEqual(CHECK, step.get("icon"), "显式标注的步骤行必须正常翻 ✓")

    def test_c2_statistical_lines_fall_back_to_dot(self):
        s3 = self.data["s3_text"]
        item = row(s3, "共 5 道组装工序")
        self.assertEqual(DOT, item.get("icon"),
                         "老口径的统计陈述行必须用 ·：%r" % item.get("title"))
        s4 = self.data["s4"]
        for needle in ("已给出（必填 4/11）", "报价必填项仍缺", "共 5 道组装工序",
                       "参数 25 条、连接 3 处、BOM 5 行"):
            with self.subTest(row=needle):
                item = row(s4, needle)
                self.assertEqual(DOT, item.get("icon"),
                                 "状态展示行必须用 ·：%r（实际 %r）"
                                 % (item.get("title"), item.get("icon")))

    def test_c3_action_and_result_rows_keep_step_icons(self):
        s4 = self.data["s4"]
        for needle in ("查询成本库", "查询条件：length=108", "命中 CMP-SEMI-EE-BLOCK-0001",
                       "库内无同类件，按新制评估", "检索工艺库：ASSY",
                       "正在调用模型编制组装工序明细"):
            with self.subTest(row=needle):
                item = row(s4, needle)
                self.assertNotEqual(DOT, item.get("icon"),
                                    "任务步骤 / 结果行不得用 ·：%r" % item.get("title"))
                self.assertIn(item.get("icon"), (CHECK, WARN) + CIRCLES)

    def test_c4_dot_only_ever_marks_an_info_row(self):
        for key in ("s1_running", "s3_text", "s4", "s5"):
            for item in self.data[key]["rows"]:
                if str(item.get("icon")) != DOT:
                    continue
                with self.subTest(scene=key, row=item.get("title")):
                    self.assertEqual("info", item.get("state"),
                                     "用 · 的行必须把行类型标成 info（data-state）：%r"
                                     % item.get("state"))
                    self.assertNotIn(DOT, DOTS,
                                     "· 仍是产品侧允许的状态图标之一")


class DContractKept(Harness):
    def test_d1_no_detail_word_and_no_json(self):
        for key in ("s1_running", "s3_text", "s4", "s5"):
            text = str(self.data[key].get("text") or "")
            with self.subTest(scene=key):
                self.assertNotIn("详情", text, "界面上又出现了「详情」字样")
                self.assertFalse(bool(__import__("re").search(r"\{\s*\}|[a-z_]+\"\s*:", text)),
                                 "过程行里又出现了输入 / 输出 JSON：%s" % text[:160])

    def test_d2_every_row_has_an_icon_and_a_title(self):
        for key in ("s1_running", "s1_done", "s2_replay", "s3_text", "s4", "s5"):
            for item in self.data[key]["rows"]:
                with self.subTest(scene=key, row=item.get("title")):
                    self.assertIsNotNone(item.get("icon"), "过程行没有状态图标")
                    self.assertTrue(str(item.get("title") or "").strip(), "过程行没有标题")

    def test_d3_no_stale_circle_after_success(self):
        for key in ("s1_done", "s3_done"):
            for item in self.data[key]["rows"]:
                if item.get("state") == "info":
                    continue
                with self.subTest(scene=key, row=item.get("title")):
                    self.assertNotIn(item.get("icon"), CIRCLES,
                                     "卡片完成后仍留着圆圈：%r" % item.get("title"))



# --------------------------------------------------------------------------- #
# E 组：报价侧与阶段页（aiProcessCard / crCard / addToolActivity）必须同一合同
# --------------------------------------------------------------------------- #
BOARD_PREAMBLE = r"""
var doc = makeDoc();
var document = doc;
var AI = {};
function $ai(id) {
  if (!AI[id]) { var node = doc.createElement("div"); node.id = id; AI[id] = node; }
  return AI[id];
}
function esc(value) { return String(value == null ? "" : value); }
var aiReplaying = false;
var aiPid = "";
var boardTurn = null;
function api() { return Promise.resolve({}); }
var TIMELINE = [];
function aiTimelineNote(text) { TIMELINE.push(String(text)); }
var CR = {};
function $cr(id) {
  if (!CR[id]) { var node = doc.createElement("div"); node.id = id; CR[id] = node; }
  return CR[id];
}
var crReplaying = false;
var crPid = "";
var costTurn = null;
function crPersistNote(text) { TIMELINE.push(String(text)); }
"""

BOARD_TAIL = PROBE + r"""
var out = {};
out.missing = MISSING;

var asm = aiProcessCard("3.3 组装工艺");
asm.log([
  "调用模型（qwen3.5-plus）",
  "检索工艺库：ASSY 便携设备锂电池 PACK（类别 电池包／材料 未定）",
  "正在调用模型编制组装工序明细",
  "  共 5 道组装工序：沿用库内 0 道、缺失需新建 5 道"
]);
var asmRoot = $ai("aiTinner").children[$ai("aiTinner").children.length - 1];
var asmSteps = asmRoot.querySelector('[data-agent-role="tools"]')
            || asmRoot.querySelector(".oc-process-steps");
out.asm_running = summarise(asmSteps);
try { asm.done(true); } catch (error) { out.asm_done_error = String(error && error.message || error); }
out.asm_done = summarise(asmSteps);

var cr = crCard("4.1 零件成本");
cr.log([
  "查询成本库",
  "  参数 25 条、连接 3 处、BOM 5 行"
]);
var crRoot = $cr("crTinner").children[$cr("crTinner").children.length - 1];
var crSteps = crRoot.querySelector('[data-agent-role="tools"]')
           || crRoot.querySelector(".oc-process-steps");
out.cr_running = summarise(crSteps);
try { cr.done(true); } catch (error) { out.cr_done_error = String(error && error.message || error); }
out.cr_done = summarise(crSteps);

console.log(JSON.stringify(out));
"""

QUOTE_PREAMBLE = r"""
var doc = makeDoc();
var document = doc;
var chatEl = doc.createElement("div");
chatEl.className = "chat-messages";
var streamBubble = null;
var streamBuf = "";
function scrollChat() {}
function finishStreamBubble() { streamBubble = null; streamBuf = ""; }
"""

QUOTE_TAIL = PROBE + r"""
var out = {};
out.missing = MISSING;

var trace = addToolActivity("调用模型（qwen3.5-plus）", "");
addToolActivity("  共 5 道组装工序：沿用库内 0 道、缺失需新建 5 道", "");
var steps = trace ? (trace.querySelector('[data-agent-role="tools"]') || trace) : null;
out.quote_running = summarise(steps);
if (typeof markTraceDone === "function") markTraceDone();
out.quote_done = summarise(steps);

console.log(JSON.stringify(out));
"""


def board_driver() -> str:
    asm, cost = read(ASSEMBLY), read(COST)
    missing: list = []
    parts = [
        DOM_STUB, BOARD_PREAMBLE,
        "var MISSING = [];",
        _pick(asm, "fn", "aiThreadAppend", missing),
        _pick(asm, "fn", "aiUserSay", missing),
        _pick(asm, "fn", "aiProcessCard", missing),
        _pick(cost, "fn", "crAppend", missing),
        _pick(cost, "fn", "crCard", missing),
        BOARD_TAIL,
    ]
    parts[2] = "var MISSING = %s;" % json.dumps(missing)
    return "\n".join(parts)


def quote_driver() -> str:
    quote = read(QUOTE)
    missing: list = []
    parts = [
        DOM_STUB, QUOTE_PREAMBLE,
        "var MISSING = [];",
        function_source(quote, "addToolActivity"),
        function_source(quote, "markTraceDone"),
        QUOTE_TAIL,
    ]
    parts[2] = "var MISSING = %s;" % json.dumps([m for m in missing if m])
    return "\n".join(parts)


INFO_ASM = "共 5 道组装工序"
INFO_CR = "参数 25 条、连接 3 处、BOM 5 行"
INFO_QUOTE = "共 5 道组装工序"


class EQuoteAndBoardParity(Harness):
    def test_e0_scaffold_is_complete(self):
        """脚手架自检：阶段页 / 报价页抽不到函数时失败必须落在脚手架。"""
        for label, data in (("阶段页", self.board), ("报价页", self.quote)):
            with self.subTest(where=label):
                self.assertEqual([], (data or {}).get("missing") or [],
                                 "%s 抽不到渲染函数：%s" % (label, (data or {}).get("missing")))

    def test_e1_board_rows_stay_circles_while_running(self):
        for label, key, step_title in (("3 阶段页-组装整合", "asm_running", "调用模型（qwen3.5-plus）"),
                                       ("4 阶段页-成本", "cr_running", "查询成本库")):
            summary = self.board[key]
            with self.subTest(where=label):
                step = row(summary, step_title)
                self.assertEqual("running", step.get("state"),
                                 "%s 卡片还在跑，过程行已是终态 %r：%s"
                                 % (label, step.get("state"), step.get("title")))
                self.assertIn(step.get("icon"), CIRCLES,
                              "%s 运行中的行必须是圆圈，实际 %r" % (label, step.get("icon")))
                self.assertNotIn(CHECK, summary.get("icons") or [],
                                 "%s 卡片还在跑，已经有行显示 ✓：%s"
                                 % (label, summary.get("icons")))

    def test_e2_board_rows_settle_on_success(self):
        for label, key, step_title in (("3 阶段页-组装整合", "asm_done", "调用模型（qwen3.5-plus）"),
                                       ("4 阶段页-成本", "cr_done", "查询成本库")):
            summary = self.board[key]
            with self.subTest(where=label):
                step = row(summary, step_title)
                self.assertEqual("completed", step.get("state"),
                                 "%s 卡片成功后行没有收成完成：%r" % (label, step.get("state")))
                self.assertEqual(CHECK, step.get("icon"), "%s 成功后行没有变 ✓" % label)

    def test_e3_board_status_lines_use_a_dot(self):
        for label, key, needle in (("3 阶段页-组装整合", "asm_running", INFO_ASM),
                                   ("3 阶段页-组装整合-完成后", "asm_done", INFO_ASM),
                                   ("4 阶段页-成本", "cr_running", INFO_CR),
                                   ("4 阶段页-成本-完成后", "cr_done", INFO_CR)):
            summary = self.board[key]
            with self.subTest(where=label):
                info = row(summary, needle)
                self.assertEqual(DOT, info.get("icon"),
                                 "%s 状态展示行必须用 ·（实际 %r）" % (label, info.get("icon")))
                self.assertEqual("info", info.get("state"),
                                 "%s 状态展示行必须把行类型标成 info：%r" % (label, info.get("state")))

    def test_e4_quote_page_matches_the_same_contract(self):
        running = self.quote["quote_running"]
        step = row(running, "调用模型（qwen3.5-plus）")
        self.assertEqual("running", step.get("state"),
                         "报价页卡片还在跑，过程行已是终态 %r" % step.get("state"))
        self.assertIn(step.get("icon"), CIRCLES, "报价页运行中的行必须是圆圈")
        info = row(running, INFO_QUOTE)
        self.assertEqual(DOT, info.get("icon"), "报价页状态展示行必须用 ·（实际 %r）" % info.get("icon"))
        self.assertEqual("info", info.get("state"), "报价页状态展示行必须标成 info")

        done = self.quote["quote_done"]
        step_done = row(done, "调用模型（qwen3.5-plus）")
        self.assertEqual(CHECK, step_done.get("icon"), "报价页收尾后步骤行没有变 ✓")
        info_done = row(done, INFO_QUOTE)
        self.assertEqual(DOT, info_done.get("icon"),
                         "报价页收尾后状态展示行不得翻成 ✓（实际 %r）" % info_done.get("icon"))
        self.assertEqual("info", info_done.get("state"),
                         "报价页收尾后状态展示行的行类型丢了")

    def test_e5_board_fold_is_real_not_bare(self):
        for label, key in (("3 阶段页-组装整合", "asm_running"), ("4 阶段页-成本", "cr_running"),
                           ("报价页", "quote_running")):
            summary = self.board[key] if key.startswith(("asm", "cr")) else self.quote[key]
            with self.subTest(where=label):
                self.assertEqual(0, summary.get("bare") or 0,
                                 "%s 有过程行被裸挂成父行直接子节点：%s"
                                 % (label, [r.get("title") for r in summary["rows"] if r.get("bare")]))
                self.assertGreaterEqual(summary.get("folded") or 0, 1,
                                        "%s 没有任何子行落在折叠区里：%s" % (label, summary["rows"]))
                self.assertEqual(summary.get("details"), summary.get("toggles"),
                                 "%s 折叠区与标题行开关没有成对：details=%r toggles=%r"
                                 % (label, summary.get("details"), summary.get("toggles")))



if __name__ == "__main__":
    unittest.main()
