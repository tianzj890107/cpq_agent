"""红测：技术工艺任务卡的「卡片体」必须包进 .oc-abody（不再挤成一行）。

用户反馈：右侧看板点按钮跑出来的任务卡（例：需求资料解析 / 进行中 /
「正在读取技术资料并调用模型提取需求字段」）所有文字挤在同一行。

根因（已实测，非推断）：agent-chat.js:1387 把 `oc-amsg oc-task-card is-queued`
三个类放在同一个 div 上，紧接着把「身份行」和「步骤列表」作为两个兄弟节点挂上去；
而 `.oc-amsg` 是横向 flex（agent-chat.css:168-172），于是两者成了同一 flex 行的两个
item。正确的一支（合并进当前轮助手卡）把 steps 塞进 `.oc-abody`，所以只有
「没有实时轮」这条分支是坏的。回归由 `## 89`（a4bd13c）引入：改之前是
`el("div", "oc-task-card is-queued")`，默认 block、上下排列。

Spec：docs/specs/tech-task-card-body-layout-and-process-stream.md（契约 A1–A6）
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
CHAT_JS = FRONTEND / "agent-chat.js"
CHAT_CSS = FRONTEND / "agent-chat.css"
ASSEMBLY = FRONTEND / "assembly-integration.js"
COST = FRONTEND / "cost-review.js"
SPEC = ROOT / "docs" / "specs" / "tech-task-card-body-layout-and-process-stream.md"

NODE = shutil.which("node")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
    """从 marker 之后第一个 `{` 起做括号配对，返回该块（含首尾花括号）。"""
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
            j = text.find("*/", i)
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
                return text[brace:i + 1]
        i += 1
    return ""


def function_body(text: str, name: str) -> str:
    return block_from(text, f"function {name}(")


def function_source(text: str, name: str) -> str:
    """要真跑的 function 声明必须连 `function name(...)` 一起抽出。"""
    marker = f"function {name}("
    block = block_from(text, marker)
    if not block:
        return ""
    start = text.find(marker)
    at = text.find(block, start)
    return text[start:at + len(block)]


def arrow_source(text: str, marker: str) -> str:
    """箭头函数要连 `const xxx = (...) =>` 一起抽出，只抽 `{...}` 会丢掉形参。"""
    block = block_from(text, marker)
    if not block:
        return ""
    start = text.find(marker)
    at = text.find(block, start)
    return text[start:at + len(block)] + ";"


def rule(text: str, selector: str) -> str:
    """取 `selector { ... }` 的规则体（不匹配时返回空串）。"""
    for match in re.finditer(re.escape(selector) + r"\s*\{([^}]*)\}", text):
        return match.group(1)
    return ""


def run_node(driver: str) -> dict:
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端结构走查")
    with tempfile.TemporaryDirectory(prefix="cpq-task-card-js-") as tmp:
        script = Path(tmp) / "driver.js"
        script.write_text(driver, encoding="utf-8")
        completed = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise AssertionError("JS 结构走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2500:],
                                    completed.stderr[-2500:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# 最小 DOM 桩：只要能记录父子关系与文本即可，真实布局由 CSS 契约保证。
# --------------------------------------------------------------------------- #
DOM_STUB = r'''
class El {
  constructor(tag, doc) {
    this.tagName = String(tag || "div").toUpperCase();
    this._doc = doc; this._id = ""; this.className = ""; this._text = "";
    this.children = []; this.parentNode = null;
  }
  get id() { return this._id; }
  set id(value) { this._id = String(value); if (this._doc) this._doc.register(this._id, this); }
  get childNodes() { return this.children.slice(); }
  get firstChild() { return this.children.length ? this.children[0] : null; }
  get firstElementChild() { return this.children.length ? this.children[0] : null; }
  classes() { return String(this.className || "").split(/\s+/).filter(Boolean); }
  get classList() {
    var self = this;
    return {
      add: function () { [].slice.call(arguments).forEach(function (c) {
        if (self.classes().indexOf(c) < 0) { self.className = self.classes().concat([c]).join(" "); } }); },
      remove: function () { [].slice.call(arguments).forEach(function (c) {
        self.className = self.classes().filter(function (x) { return x !== c; }).join(" "); }); },
      contains: function (c) { return self.classes().indexOf(c) >= 0; }
    };
  }
  get textContent() {
    return this._text + this.children.map(function (c) { return c.textContent; }).join("");
  }
  set textContent(value) {
    this._text = (value === null || value === undefined) ? "" : String(value);
    this.children.forEach(function (c) { c.parentNode = null; });
    this.children = [];
  }
  append() { var self = this; [].slice.call(arguments).forEach(function (n) { n.parentNode = self; self.children.push(n); }); }
  appendChild(node) { node.parentNode = this; this.children.push(node); return node; }
  prepend(node) { node.parentNode = this; this.children.unshift(node); return node; }
  remove() {
    if (this.parentNode) {
      var index = this.parentNode.children.indexOf(this);
      if (index >= 0) { this.parentNode.children.splice(index, 1); }
    }
    this.parentNode = null;
  }
  setAttribute(name, value) { if (name === "id") { this.id = value; } if (name === "class") { this.className = String(value); } }
  getAttribute(name) { return name === "id" ? (this._id || null) : null; }
  matches() { return false; }
  closest() { return null; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener() {}
}
var doc = {
  _byId: {},
  register: function (id, el) { this._byId[String(id)] = el; },
  getElementById: function (id) { return this._byId[String(id)] || null; },
  createElement: function (tag) { return new El(tag, this); },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {}
};
globalThis.document = doc;
globalThis.window = { addEventListener: function () {}, dispatchEvent: function () {} };
'''

CARD_TAIL = r'''
var out = {};
var card = ensureTaskCard("T-probe-1", "需求资料解析");
pushTaskStep(card, "正在读取技术资料并调用模型提取需求字段", "");
var box = card.box;
out.box_class = String(box.className);
out.box_children = box.children.map(function (c) { return String(c.className); });
var body = box.children.length ? box.children[0] : null;
out.body_class = body ? String(body.className) : "";
out.body_children = body ? body.children.map(function (c) { return String(c.className); }) : [];
var label = body && body.children.length > 0 ? body.children[0] : null;
var stepsEl = body && body.children.length > 1 ? body.children[1] : null;
out.label_text = label ? label.textContent : "";
out.steps_text = stepsEl ? stepsEl.textContent : "";
out.step_row_class = stepsEl && stepsEl.children.length ? String(stepsEl.children[0].className) : "";
out.host_cards = tinner.children.length;
out.steps_parent_class = card.steps && card.steps.parentNode ? String(card.steps.parentNode.className) : "";

// 合并进当前轮助手卡的那一支：步骤必须落在本轮助手卡的 .oc-abody 里（不回归）。
var turnBody = doc.createElement("div"); turnBody.className = "oc-abody";
var turnWrap = doc.createElement("div"); turnWrap.className = "oc-amsg"; turnWrap.append(turnBody);
var turnState = doc.createElement("span"); turnState.className = "oc-alabel-state";
activeTurnCtx = { wrap: turnWrap, body: turnBody, state: turnState };
var merged = ensureTaskCard("T-probe-2", "组装工艺");
pushTaskStep(merged, "模型编制工序明细", "");
out.merged_box_is_turn_wrap = merged.box === turnWrap;
out.merged_body_children = turnBody.children.map(function (c) { return String(c.className); });
console.log(JSON.stringify(out));
'''


def card_driver(js: str) -> str:
    parts = [
        DOM_STUB,
        textwrap.dedent(
            """
            var tinner = doc.createElement("div"); tinner.id = "ocTinner";
            var taskProgressCards = new Map();
            var activeTurnCtx = null;
            function clearEmpty() {}
            function scrollDown() {}
            function taskProgressHost() { return tinner; }
            """),
        arrow_source(js, "const el = (tag, cls, text)"),
        function_source(js, "taskStatusWord"),
        function_source(js, "ensureTaskCard"),
        function_source(js, "pushTaskStep"),
        function_source(js, "setTaskStatus"),
        CARD_TAIL,
    ]
    return "\n".join(part for part in parts if part)


# --------------------------------------------------------------------------- #
# A. 源码与样式契约
# --------------------------------------------------------------------------- #
class CardBodyStaticContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read(CHAT_JS)
        cls.css = read(CHAT_CSS)
        cls.spec = read(SPEC)

    def test_01_spec_pins_the_contract(self):
        self.assertTrue(self.spec, "缺少 docs/specs/tech-task-card-body-layout-and-process-stream.md")
        for token in ("oc-abody", "oc-task-card", "ensureTaskCard", "process_event", "process_log"):
            self.assertIn(token, self.spec, "spec 未钉住 %s" % token)

    def test_02_task_card_branch_builds_a_body_container(self):
        body = function_body(self.js, "ensureTaskCard")
        self.assertTrue(body, "找不到 ensureTaskCard()")
        self.assertIn("oc-abody", body,
                      "任务卡分支没有建 .oc-abody 内容容器：头行与步骤会重新变成同一 flex 行的两个兄弟节点")

    def test_03_head_and_steps_are_not_attached_to_the_card_element(self):
        body = function_body(self.js, "ensureTaskCard")
        self.assertNotRegex(body, r"box\.append\(\s*head",
                            "身份行不得直接挂在卡片元素上（.oc-amsg 是横向 flex）：%s" % body[:400])
        self.assertNotRegex(body, r"box\.append\(\s*steps",
                            "步骤区不得直接挂在卡片元素上：%s" % body[:400])
        self.assertRegex(body, r"\.append\(\s*head\s*,\s*steps\s*\)|\.append\(\s*steps\s*\)",
                         "身份行与步骤区必须挂到同一个内容容器里：%s" % body[:400])

    def test_04_every_assistant_card_has_a_body(self):
        cards = re.findall(r'el\(\s*"div"\s*,\s*"oc-amsg[^"]*"', self.js)
        bodies = re.findall(r'el\(\s*"div"\s*,\s*"oc-abody[^"]*"', self.js)
        self.assertEqual(len(cards), len(bodies),
                         "有 %d 个 .oc-amsg 却只有 %d 个 .oc-abody：有卡没有内容容器（就会挤成一行）"
                         % (len(cards), len(bodies)))

    def test_05_css_comment_no_longer_contradicts_the_implementation(self):
        self.assertNotIn("不在 .oc-amsg", self.css,
                         "agent-chat.css 仍写着「任务卡不在 .oc-amsg 里」，与实现相反")
        comments = re.findall(r"/\*([\s\S]*?)\*/", self.css)
        self.assertTrue(any("oc-task-card" in text and "oc-abody" in text for text in comments),
                        "缺少说明「任务卡与助手卡同款、内容在 .oc-abody 里」的注释")

    def test_06_oc_abody_keeps_its_flex_contract(self):
        body = rule(self.css, ".oc-abody")
        self.assertTrue(body, "找不到 .oc-abody 规则")
        self.assertRegex(body, r"flex\s*:\s*1", ".oc-abody 必须是 flex 行里唯一的正文列")
        self.assertIn("min-width", body, ".oc-abody 缺 min-width: 0，flex 行里会挤压溢出")

    def test_07_task_steps_stay_a_vertical_list(self):
        body = rule(self.css, ".oc-task-steps")
        self.assertTrue(body, "找不到 .oc-task-steps 规则")
        self.assertRegex(body, r"flex-direction\s*:\s*column", "步骤区必须逐行排列")

    def test_08_chip_and_state_colors_are_untouched(self):
        self.assertRegex(self.css, r"\.oc-task-card[^{]*\.oc-task-state\s*\{[^}]*margin-left",
                         "让任务卡 chip 靠右的规则被删除")
        for status in ("running", "interrupted", "partial", "succeeded", "failed"):
            with self.subTest(status=status):
                self.assertRegex(self.css, rf"\.oc-task-card\.is-{status}\b[^{{]*\.oc-task-state\s*\{{",
                                 "任务卡 %s 态配色规则被删除" % status)

    def test_09_the_two_good_templates_keep_the_same_shape(self):
        for path, name in ((ASSEMBLY, "aiProcessCard"), (COST, "crCard")):
            with self.subTest(function=name):
                body = function_body(read(path), name)
                self.assertTrue(body, "找不到 %s()" % name)
                self.assertIn("oc-abody", body, "%s 的内容容器被改动" % name)
                self.assertIn("oc-process-steps", body, "%s 的步骤区被改动" % name)

    def test_10_existing_card_pipeline_is_kept(self):
        for token in ("pushTaskStep", "setTaskStatus", "toneOf", "sanitizeTaskDetail",
                      "taskProgressHost", "oc-task-steps", "oc-alabel-state", "oc-task-state"):
            with self.subTest(token=token):
                self.assertIn(token, self.js, "任务卡管线 %s 被删除" % token)
        self.assertIn('window.addEventListener("agent:task-progress"', self.js,
                      "后端任务进度 → 会话卡的既有入口被删了")


# --------------------------------------------------------------------------- #
# B. 真跑：任务卡 DOM 结构（无实时轮 / 合并进当前轮）
# --------------------------------------------------------------------------- #
@unittest.skipUnless(NODE, "需要 node 才能真跑任务卡结构走查")
class TaskCardDomShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(card_driver(read(CHAT_JS)))

    def test_20_card_keeps_the_assistant_card_classnames(self):
        self.assertIn("oc-amsg", self.out["box_class"], "任务卡必须仍是助手卡同款：%s" % self.out)
        self.assertIn("oc-task-card", self.out["box_class"], "任务卡身份类丢了：%s" % self.out)

    def test_21_card_has_exactly_one_direct_child(self):
        children = self.out["box_children"]
        self.assertEqual(1, len(children),
                         "卡片直接子元素必须恰好 1 个（.oc-abody），现在是 %s —— 身份行与步骤区"
                         "会变成同一 flex 行的两个 item（就是「挤成一行」）" % children)

    def test_22_the_single_child_is_the_body_container(self):
        self.assertIn("oc-abody", self.out["body_class"], "内容容器不是 .oc-abody：%s" % self.out)

    def test_23_body_holds_the_label_then_the_steps(self):
        children = self.out["body_children"]
        self.assertEqual(2, len(children), ".oc-abody 里应当是身份行 + 步骤区：%s" % children)
        self.assertIn("oc-alabel", children[0], "第一个子节点必须是蓝色身份行：%s" % children)
        self.assertIn("oc-task-steps", children[1], "第二个子节点必须是步骤区：%s" % children)

    def test_24_step_text_is_not_inside_the_label_row(self):
        text = "正在读取技术资料并调用模型提取需求字段"
        self.assertIn(text, self.out["steps_text"], "步骤文字没有落在步骤区里：%s" % self.out)
        self.assertNotIn(text, self.out["label_text"],
                         "步骤文字出现在身份行里 —— 头和步骤挤在同一行了：%s" % self.out)

    def test_25_steps_are_rendered_as_rows(self):
        self.assertIn("oc-process-step", self.out["step_row_class"],
                      "每一步必须是一条 .oc-process-step：%s" % self.out)

    def test_26_only_one_card_on_the_host(self):
        self.assertEqual(1, self.out["host_cards"],
                         "同一 taskId 只能有一张卡：%s" % self.out)

    def test_27_merging_into_the_current_turn_still_works(self):
        self.assertTrue(self.out["merged_box_is_turn_wrap"],
                        "有实时轮时必须复用本轮助手卡：%s" % self.out)
        self.assertEqual(1, len(self.out["merged_body_children"]),
                         "被合并的步骤没有落进本轮助手卡的 .oc-abody：%s" % self.out)
        self.assertIn("oc-task-steps", self.out["merged_body_children"][0],
                      "合并进本轮助手卡的不是步骤区：%s" % self.out)

    def test_28_steps_node_lives_under_the_body(self):
        self.assertIn("oc-abody", self.out["steps_parent_class"],
                      "步骤区的父节点不是 .oc-abody：%s" % self.out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
