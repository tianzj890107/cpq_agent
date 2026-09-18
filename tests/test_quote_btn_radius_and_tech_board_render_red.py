"""红测：报价按钮倒角 + 技术工艺三处渲染修正（零件库结论 / 详情换行 / 零件清单缩进）。

用户口径（四条）：

1. 「正在查看第 1 步…（可编辑）回到当前步骤 / 保存修改并重算」这排按钮**没做倒角**，
   并要求顺带把全局同类问题扫一遍；
2. 右侧零件清单里的「零部件库检索」结论**像一段没渲染的纯文字**，而且
   `（未匹配（按新制评估） · 51%）` 这种嵌套括号 + 给未匹配行挂匹配度是错的；
3. Agent 过程事件行里的「详情」应该换到下一行（现在被横向 flex 挤在文字右边）；
4. 零件清单缩进不对：零件要同一缩进、总成要同一缩进、工艺推荐要跟零件而不是跟总成。

现状（实测，非推断）：

· `确认需求解析结果.html` 全页**没有** `.btn` 基础规则，只有 `.bottom-bar .btn`（`:556`）与
  `.modal-footer .btn`（`:700`）两条作用域受限的规则 → 底部栏之外的 4 颗 `.btn`
  （view-bar 两颗 `:831`/`:832`、设置弹窗两颗 `:908`/`:909`）没有倒角；view-bar 那两颗
  靠行内 `style="padding:7px 14px;min-width:0"` 撑尺寸。全仓 9 个用裸 `btn` 的页面里，
  只有这一页缺基础规则。
· `workbench.css` 只有 `.component-match-unavailable*` / `-stale`（`:247`–`:255`），
  `.component-match-summary` / `-list` / `-item` **一条样式都没有**；`app.js:1015`
  `renderComponentMatchResult()` 把每行拼成单段 `textContent`，于是
  `（未匹配（按新制评估） · 51%）`。
· `agent-chat.css:326` 的 `.oc-process-step` 是横向 flex，`:345` 的 `.oc-process-detail`
  只有 `flex:1;min-width:0` → 「详情」与过程文字同一行。
· `app.js:1520` `renderNode(..., depth, ...)` 用 `6 + depth * 14` 当缩进，实测总成 `["6px","20px"]`、
  零件 `["34px","20px","6px"]`、工艺推荐行 `marginLeft` 全为 `null`（只吃 CSS 的 `padding-left:10px`）。

Spec：docs/specs/quote-btn-radius-and-tech-board-render-fixes.md
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
QUOTE_HTML = ROOT / "确认需求解析结果.html"
APP_JS = FRONTEND / "app.js"
WB_CSS = FRONTEND / "workbench.css"
CHAT_CSS = FRONTEND / "agent-chat.css"
CHAT_JS = FRONTEND / "agent-chat.js"
INDEX_HTML = FRONTEND / "index.html"
SPEC = ROOT / "docs" / "specs" / "quote-btn-radius-and-tech-board-render-fixes.md"

NODE = shutil.which("node")

SKIP_DIRS = {"node_modules", "vendor", ".git", "open-claude", ".venv", "tests", "docs"}

# 全仓用裸 `btn` 类名、且各自已有基础倒角规则的页面（A6 回归护栏的基线）。
PAGES_WITH_BASE_BTN = (
    "BOM层级结构.html",
    "XBOM智能体-配置BOM生成.html",
    "报价规则.html",
    "规则助手-规则配置.html",
    "tech_app/frontend/assembly-integration.html",
    "tech_app/frontend/cost-review.html",
    "tech_app/frontend/index.html",
    "tech_app/frontend/report.html",
)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


# --------------------------------------------------------------------------- #
# CSS / JS 解析小工具
# --------------------------------------------------------------------------- #
def declarations(css: str):
    """按出现顺序返回 (选择器, 声明串)，两侧空白已规整。

    先去掉注释再按花括号深度切分：`@media` 这类 at-rule 只当包装层丢弃，内部真正的
    规则照常产出。选择器可能是逗号分组，调用方按逗号拆开比对。
    """
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    pairs = []
    stack = []
    buf_start = 0
    for index, char in enumerate(text):
        if char == "{":
            stack.append((text[buf_start:index].strip(), index + 1))
            buf_start = index + 1
        elif char == "}":
            if stack:
                selector, body_start = stack.pop()
                if not selector.startswith("@"):
                    pairs.append((selector, text[body_start:index]))
            buf_start = index + 1
    return [(re.sub(r"\s+", " ", selector).strip(), re.sub(r"\s+", "", body))
            for selector, body in pairs]


def rules_for(css: str, selector: str):
    wanted = re.sub(r"\s+", " ", selector).strip()
    return [body for sel, body in declarations(css)
            if any(part.strip() == wanted for part in sel.split(","))]


def rule(css: str, selector: str) -> str:
    found = rules_for(css, selector)
    if not found:
        raise AssertionError("缺少 CSS 规则：%s" % selector)
    return found[0]


def joined(css: str, selector: str) -> str:
    """同一选择器的多条规则拼起来看（分组、重复声明都能覆盖）。"""
    return "".join(rules_for(css, selector))


def joined_matching(css: str, *tokens: str) -> str:
    """选择器里同时含这几段的规则拼起来（不要求逐字相等，容得下等价写法）。"""
    out = []
    for selector, body in declarations(css):
        if all(token in selector for token in tokens):
            out.append(body)
    return "".join(out)


def has_declaration(body: str, prop: str, value: str = None) -> bool:
    if value is None:
        return re.search(re.escape(prop) + r"\s*:", body) is not None
    return re.search(re.escape(prop) + r"\s*:\s*" + re.escape(value), body) is not None


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
    marker = f"function {name}("
    block = block_from(text, marker)
    if not block:
        return ""
    start = text.find(marker)
    at = text.find(block, start)
    return text[start:at + len(block)]


def run_node(driver: str) -> dict:
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端结构走查")
    with tempfile.TemporaryDirectory(prefix="cpq-render-fix-js-") as tmp:
        script = pathlib.Path(tmp) / "driver.js"
        script.write_text(driver, encoding="utf-8")
        completed = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise AssertionError("JS 结构走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2500:],
                                    completed.stderr[-2500:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# 页面样式来源清点（A6 全局护栏）
# --------------------------------------------------------------------------- #
def style_sources(page: pathlib.Path):
    """页面自己的样式来源：内联 `<style>` + 同目录相对路径的本地 css。"""
    out = []
    html = read(page)
    for m in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.S):
        out.append((str(page) + "#inline", m.group(1)))
    for m in re.finditer(r"<link[^>]+rel=[\"']stylesheet[\"'][^>]*>", html):
        href = re.search(r"href=[\"']([^\"']+)[\"']", m.group(0))
        if not href:
            continue
        url = href.group(1).split("?")[0]
        if url.startswith(("/", "http", "//")):
            continue
        target = (page.parent / url).resolve()
        if target.exists() and target.suffix == ".css":
            out.append((str(target), read(target)))
    return out


def base_btn_radius(sources) -> bool:
    """样式来源里有没有一条选择器含 `.btn`、且声明含 `border-radius` 的基础规则。"""
    for _name, text in sources:
        for selector, body in declarations(text):
            parts = [p.strip() for p in selector.split(",")]
            if ".btn" in parts and "border-radius" in body:
                return True
    return False


def pages_using_bare_btn_token():
    found = []
    for page in sorted(ROOT.rglob("*.html")):
        if any(part in SKIP_DIRS for part in page.parts):
            continue
        html = read(page)
        count = 0
        for m in re.finditer(r"class\s*=\s*\"([^\"]*)\"", html):
            count += 1 if "btn" in m.group(1).split() else 0
        for m in re.finditer(r"class\s*=\s*'([^']*)'", html):
            count += 1 if "btn" in m.group(1).split() else 0
        if count:
            found.append((page, count))
    return found


# --------------------------------------------------------------------------- #
# 最小 DOM 桩：只记父子关系与文本，真实布局由 CSS 契约保证。
# --------------------------------------------------------------------------- #
DOM_STUB = r'''
class El {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.className = ""; this.own = ""; this.children = []; this.parentNode = null;
    this.dataset = {}; this.style = {}; this.hidden = false; this.innerHTML = "";
    this.id = ""; this.type = "";
  }
  append() { var self = this; [].slice.call(arguments).forEach(function (n) { n.parentNode = self; self.children.push(n); }); }
  appendChild(node) { node.parentNode = this; this.children.push(node); return node; }
  prepend(node) { node.parentNode = this; this.children.unshift(node); return node; }
  remove() { if (this.parentNode) { var i = this.parentNode.children.indexOf(this); if (i >= 0) { this.parentNode.children.splice(i, 1); } } }
  replaceChildren() { var list = [].slice.call(arguments); list.forEach(function (n) { n.parentNode = this; }); this.children = list; this.own = ""; }
  closest() { return null; }
  matches() { return false; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener() {}
  setAttribute(name, value) { if (name === "id") { this.id = String(value); } if (name === "class") { this.className = String(value); } }
  getAttribute(name) { return name === "id" ? (this.id || null) : null; }
  get textContent() { return this.own + this.children.map(function (c) { return c.textContent; }).join(""); }
  set textContent(value) { this.own = (value === null || value === undefined) ? "" : String(value); this.children = []; }
}
var BY_ID = {};
globalThis.document = {
  createElement: function (tag) { return new El(tag); },
  getElementById: function (id) { return BY_ID[String(id)] || null; },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {}
};
function dumpTree(el) {
  return { cls: String(el.className || ""), own: String(el.own || ""),
           kids: el.children.map(dumpTree) };
}
function collect(node, cls, out) {
  var tokens = String(node.className || "").split(/\s+/);
  if (tokens.indexOf(cls) >= 0) { out.push(node); }
  node.children.forEach(function (c) { collect(c, cls, out); });
}
'''


def component_match_driver(js: str) -> str:
    return "\n".join([
        DOM_STUB,
        textwrap.dedent(
            """
            var sec = new El("section"); sec.id = "secParts"; BY_ID["secParts"] = sec;
            """),
        function_source(js, "renderComponentMatchResult"),
        textwrap.dedent(
            r'''
            renderComponentMatchResult({
              generated_at: "2026-09-16 18:14:37",
              library_size: 20,
              summary: { total: 4, reuse: 0, modify: 2, new: 2, matched: 2 },
              items: [
                { part_id: "P-001", part_name: "上壳", decision: "modify", decision_label: "可改制",
                  matched: true, component_code: "CMP-SEMI-EE-BLOCK-0001",
                  component_name: "搬运吸嘴主体安装块", score: 0.6483 },
                { part_id: "P-003", part_name: "保护板/BMS", decision: "new",
                  decision_label: "未匹配（按新制评估）", matched: false,
                  component_code: null, component_name: null, score: 0.5124 },
                { part_id: "P-004", part_name: "电芯组支架", decision: "new",
                  decision_label: "未匹配（按新制评估）", matched: false,
                  component_code: null, component_name: null, score: 0.0 }
              ]
            });
            var slot = sec.children[sec.children.length - 1];
            var rows = []; collect(slot, "component-match-item", rows);
            console.log(JSON.stringify({
              tree: dumpTree(slot),
              summary_text: (function () {
                var hit = []; collect(slot, "component-match-summary", hit);
                return hit.length ? hit[0].textContent : "";
              })(),
              rows: rows.map(function (r) {
                var part = [], hit = [], tag = [];
                collect(r, "component-match-part", part);
                collect(r, "component-match-hit", hit);
                collect(r, "component-match-tag", tag);
                return {
                  cls: String(r.className),
                  text: r.textContent,
                  part: part.length ? part[0].textContent : "",
                  hit: hit.length ? hit[0].textContent : "",
                  tag: tag.length ? tag[0].textContent : "",
                  children: r.children.map(function (c) { return String(c.className); })
                };
              })
            }));
            '''),
    ])


def parts_tree_driver(js: str) -> str:
    return "\n".join([
        DOM_STUB,
        textwrap.dedent(
            """
            function esc(s) { return String(s === null || s === undefined ? "" : s); }
            function geomFor() { return null; }
            function drawingsFor() { return null; }
            function partStaleMark() { return ""; }
            function partThumbnail() { return ""; }
            function selectPart() {}
            function openPartAnalysis() {}
            var processWrenchNavIcon = "";
            """),
        function_source(js, "buildClientTree"),
        function_source(js, "renderNode"),
        function_source(js, "buildPartSubActions"),
        textwrap.dedent(
            r'''
            var ir = {
              device_name: "电池",
              assemblies: [
                { assembly_id: "A-001", name: "整机", role: null, quantity: 1, parent_id: null },
                { assembly_id: "A-002", name: "电池模组", role: null, quantity: 1, parent_id: "A-001" }
              ],
              parts: [
                { part_id: "P-001", name: "上壳", parent_id: "A-002", quantity: 1, confidence: 0.5,
                  features: [], material: null, recommendation: "" },
                { part_id: "P-002", name: "下壳", parent_id: "A-001", quantity: 1, confidence: 0.5,
                  features: [], material: null, recommendation: "" },
                { part_id: "P-003", name: "支架", parent_id: null, quantity: 1, confidence: 0.5,
                  features: [], material: null, recommendation: "" }
              ]
            };
            var tree = new El("div"); tree.id = "tree";
            var byId = {}; ir.parts.forEach(function (p) { byId[p.part_id] = p; });
            var root = buildClientTree(ir);
            var arity = renderNode.length;
            root.children.forEach(function (c) { renderNode(c, tree, 0, byId); });
            function walk(node, out) {
              var cls = String(node.className || "");
              if (cls === "asm") { out.asm.push(node.style.paddingLeft); }
              if (cls.indexOf("part-item") >= 0) { out.part.push(node.style.marginLeft); }
              if (cls === "part-subactions") { out.sub.push(node.style.marginLeft); }
              node.children.forEach(function (c) { walk(c, out); });
            }
            var out = { asm: [], part: [], sub: [] };
            walk(tree, out);
            var asmA = [], partA = [];
            collect(tree, "asm", asmA);
            var rowsA = []; collect(tree, "part-item", rowsA);
            var subsA = []; collect(tree, "part-subactions", subsA);
            console.log(JSON.stringify({
              asm: out.asm, part: out.part, sub: out.sub,
              asm_count: asmA.length, part_count: rowsA.length, sub_count: subsA.length,
              render_node_arity: arity,
              part_parent_index: rowsA.map(function (r) {
                return r.parentNode.children.indexOf(r);
              })
            }));
            '''),
    ])


# --------------------------------------------------------------------------- #
# Spec
# --------------------------------------------------------------------------- #
class SpecPinsTheContractTest(unittest.TestCase):
    def test_01_spec_pins_the_contract(self):
        spec = read(SPEC)
        self.assertTrue(spec, "缺少 docs/specs/quote-btn-radius-and-tech-board-render-fixes.md")
        for token in ("border-radius", "component-match-item", "component-match-tag",
                      "oc-process-detail", "flex-basis", "renderNode", "part-subactions",
                      "workbench.css", "agent-chat.css", "确认需求解析结果.html"):
            with self.subTest(token=token):
                self.assertIn(token, spec, "spec 未钉住 %s" % token)


# --------------------------------------------------------------------------- #
# A. 报价页 `.btn` 统一倒角 + 全局同类问题护栏
# --------------------------------------------------------------------------- #
class QuoteButtonRadiusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read(QUOTE_HTML)
        cls.css = "\n".join(text for _name, text in style_sources(QUOTE_HTML))

    def test_10_quote_page_defines_a_base_btn_rule(self):
        found = [body for selector, body in declarations(self.css)
                 if ".btn" in [part.strip() for part in selector.split(",")]]
        self.assertTrue(found, "确认需求解析结果.html 没有任何 `.btn` 基础规则："
                               "底部栏之外的按钮只能各自裸奔（倒角缺失的来源）")
        base = "".join(found)
        for prop in ("border-radius", "display", "align-items", "padding", "cursor"):
            with self.subTest(prop=prop):
                self.assertTrue(has_declaration(base, prop),
                                "`.btn` 基础规则缺 %s：%s" % (prop, base[:400]))

    def test_11_base_radius_matches_the_bottom_bar(self):
        bottom = joined(self.css, ".bottom-bar .btn")
        self.assertTrue(has_declaration(bottom, "border-radius", "10px"),
                        "底部栏倒角被改动：%s" % bottom[:300])
        base = "".join(body for selector, body in declarations(self.css)
                       if ".btn" in [part.strip() for part in selector.split(",")])
        self.assertTrue(has_declaration(base, "border-radius", "10px"),
                        "`.btn` 基础规则必须用与底部栏一致的 10px 倒角，不引入第二套视觉：%s" % base[:300])

    def test_12_view_bar_buttons_get_their_size_from_css(self):
        bar = joined(self.css, ".view-bar .vb-actions .btn")
        self.assertTrue(has_declaration(bar, "padding", "7px14px"),
                        "`.view-bar .vb-actions .btn` 没有接管小尺寸内边距：%s" % bar[:300])
        self.assertTrue(has_declaration(bar, "min-width", "0"),
                        "`.view-bar .vb-actions .btn` 没有接管 min-width:0：%s" % bar[:300])

    def test_13_view_bar_buttons_have_no_inline_padding_style(self):
        bar = self.html[self.html.find('class="view-bar"'):]
        bar = bar[:bar.find("</div>\n        </div>") if "</div>\n        </div>" in bar else 1200]
        buttons = re.findall(r"<button[^>]*>", bar)
        self.assertGreaterEqual(len(buttons), 2, "view-bar 里找不到两颗按钮：%s" % bar[:400])
        for tag in buttons:
            with self.subTest(tag=tag[:120]):
                self.assertNotIn("padding", tag,
                                 "按钮仍在用行内 padding 撑尺寸（行内样式不参与统一倒角）：%s" % tag)
                self.assertNotIn("min-width", tag,
                                 "按钮仍在用行内 min-width：%s" % tag)

    def test_14_bottom_bar_and_modal_footer_keep_their_min_widths(self):
        self.assertTrue(has_declaration(joined(self.css, ".bottom-bar .btn"), "min-width", "110px"),
                        "底部栏 min-width:110px 被改动")
        self.assertTrue(has_declaration(joined(self.css, ".modal-footer .btn"), "min-width", "92px"),
                        "设置弹窗 min-width:92px 被改动")

    def test_15_modal_footer_buttons_are_covered_by_the_base_rule_too(self):
        buttons = re.findall(r'<button class="btn btn-(?:secondary|primary)"[^>]*>(?:(?!</button>)[\s\S])*?</button>',
                             self.html)
        self.assertGreaterEqual(len(buttons), 4,
                                "该页 `.btn` 数量对不上，护栏可能失效（底部 2 + view-bar 2 + 弹窗 2）")
        base = "".join(body for selector, body in declarations(self.css)
                       if ".btn" in [part.strip() for part in selector.split(",")])
        self.assertTrue(has_declaration(base, "border-radius"),
                        "设置弹窗的取消 / 保存也要吃基础规则（今天连内边距都没有）")

    def test_16_every_page_using_the_bare_btn_token_has_a_base_rule(self):
        pages = pages_using_bare_btn_token()
        names = sorted(str(page.relative_to(ROOT)) for page, _count in pages)
        for rel in PAGES_WITH_BASE_BTN:
            self.assertIn(rel, names, "基线页面 %s 不再使用 `.btn`，护栏清单需要复核" % rel)
        self.assertIn("确认需求解析结果.html", names)
        for page, count in pages:
            rel = str(page.relative_to(ROOT))
            with self.subTest(page=rel, buttons=count):
                self.assertTrue(base_btn_radius(style_sources(page)),
                                "%s 用了 %d 颗裸 `.btn`，但自己的样式来源里没有带 border-radius 的 "
                                "`.btn` 基础规则（这就是「按钮没做倒角」的同一类问题）" % (rel, count))


# --------------------------------------------------------------------------- #
# B. 零部件库检索结论：真的渲染出来
# --------------------------------------------------------------------------- #
class ComponentMatchStyleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = read(WB_CSS)

    def test_20_result_block_has_styles(self):
        for selector in (".component-match-summary", ".component-match-list", ".component-match-item"):
            with self.subTest(selector=selector):
                body = joined(self.css, selector)
                self.assertTrue(body,
                                "%s 一条样式都没有：结论区会退化成没有任何层级的纯文字" % selector)

    def test_21_list_is_a_vertical_column(self):
        body = joined(self.css, ".component-match-list")
        self.assertTrue(has_declaration(body, "flex-direction", "column")
                        or has_declaration(body, "display", "grid"),
                        "列表必须是纵向排列：%s" % body[:300])

    def test_22_item_rows_are_distinguishable_records(self):
        body = joined(self.css, ".component-match-item")
        self.assertTrue(has_declaration(body, "display") or has_declaration(body, "padding")
                        or has_declaration(body, "border"),
                        "每一行要是一条可分辨的记录（display / padding / border 至少一项）：%s" % body[:300])

    def test_23_decision_tag_has_three_colored_states(self):
        for selector in (".component-match-tag", ".component-match-tag.reuse",
                         ".component-match-tag.modify", ".component-match-tag.new"):
            with self.subTest(selector=selector):
                body = joined(self.css, selector)
                self.assertTrue(body, "判定胶囊缺样式：%s" % selector)
        self.assertTrue(has_declaration(joined(self.css, ".component-match-tag"), "border-radius"),
                        "判定胶囊要有圆角：%s" % joined(self.css, ".component-match-tag")[:300])

    def test_24_part_and_hit_segments_have_styles(self):
        for selector in (".component-match-part", ".component-match-hit"):
            with self.subTest(selector=selector):
                self.assertTrue(joined(self.css, selector), "缺样式：%s" % selector)

    def test_25_kb_unavailable_branch_is_untouched(self):
        for selector in (".component-match-unavailable", ".component-match-unavailable-note",
                         ".component-match-stale"):
            with self.subTest(selector=selector):
                self.assertTrue(joined(self.css, selector),
                                "「库连不上」分支的样式被删了：%s" % selector)


@unittest.skipUnless(NODE, "需要 node 才能真跑渲染走查")
class ComponentMatchDomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(component_match_driver(read(APP_JS)))

    def test_30_summary_line_keeps_its_wording(self):
        text = self.out["summary_text"]
        for token in ("零部件库检索", "可复用 0", "可改制 2", "未匹配 2", "库内 20 条",
                      "2026-09-16 18:14:37"):
            with self.subTest(token=token):
                self.assertIn(token, text, "小结行口径被改动：%s" % text)

    def test_31_every_row_is_three_structured_segments(self):
        rows = self.out["rows"]
        self.assertEqual(3, len(rows), "行数对不上：%s" % json.dumps(rows, ensure_ascii=False))
        for row in rows:
            with self.subTest(row=row.get("text")):
                self.assertTrue(row["part"],
                                "行缺 `.component-match-part`（件号 + 名称要独立成段）：%s" % row)
                self.assertTrue(row["hit"],
                                "行缺 `.component-match-hit`（命中件号/名称 或「库内无同类件」要独立成段）：%s" % row)
                self.assertTrue(row["tag"],
                                "行缺 `.component-match-tag`（判定要独立成胶囊）：%s" % row)

    def test_32_no_nested_parentheses_in_row_text(self):
        for row in self.out["rows"]:
            with self.subTest(row=row.get("text")):
                self.assertNotIn("（未匹配（", row["text"],
                                 "判定文案被塞进了外层括号，出现嵌套括号：%s" % row["text"])

    def test_33_matched_row_shows_code_and_score(self):
        row = self.out["rows"][0]
        self.assertIn("CMP-SEMI-EE-BLOCK-0001", row["hit"], "命中行丢了库内件号：%s" % row)
        self.assertIn("65%", row["hit"] + row["tag"] + row["text"], "命中行丢了匹配度：%s" % row)
        self.assertIn("可改制", row["tag"], "判定必须进胶囊：%s" % row)

    def test_34_unmatched_rows_show_no_score(self):
        for row in self.out["rows"][1:]:
            with self.subTest(row=row.get("text")):
                self.assertIn("库内无同类件", row["hit"] or row["text"],
                              "未命中行的命中文案被改动：%s" % row)
                self.assertNotIn("%", row["text"],
                                 "未匹配的行不该挂匹配度（分数只说明最像的候选也不太像）：%s" % row["text"])
                self.assertIn("未匹配", row["tag"], "判定必须进胶囊：%s" % row)

    def test_35_row_classes_keep_the_decision(self):
        for row, decision in zip(self.out["rows"], ("modify", "new", "new")):
            with self.subTest(decision=decision):
                self.assertIn(decision, row["cls"], "行的判定类丢了：%s" % row["cls"])


# --------------------------------------------------------------------------- #
# C. 过程事件行的「详情」换行
# --------------------------------------------------------------------------- #
class ProcessDetailWrapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = read(CHAT_CSS)
        cls.js = read(CHAT_JS)

    def test_40_step_row_can_wrap(self):
        body = joined(self.css, ".oc-process-step")
        self.assertTrue(has_declaration(body, "flex-wrap", "wrap"),
                        "过程行必须允许换行，否则「详情」永远被挤在文字右边：%s" % body[:400])

    # ## 133 起：过程行不再有「详情」折叠块，原 test_41–test_45 整体退役。
    # 过程行的新合同见 tests/test_quote_tech_process_row_product_contract_red.py。


# --------------------------------------------------------------------------- #
# D. 零件清单缩进
# --------------------------------------------------------------------------- #
class PartsTreeIndentStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read(APP_JS)
        cls.css = read(WB_CSS)

    def test_50_render_node_no_longer_scales_the_indent_by_depth(self):
        body = function_body(self.js, "renderNode")
        self.assertTrue(body, "找不到 renderNode()")
        self.assertNotRegex(body, r"depth\s*\*",
                            "缩进仍在按层级深度缩放（总成与零件会各自出现好几档缩进）：%s" % body[:600])
        self.assertRegex(body, r"renderNode\s*\(\s*node\s*,\s*container\s*,\s*partById\s*\)",
                         "renderNode 的签名里仍带着 depth：%s" % body[:200])

    def test_51_indents_are_named_constants(self):
        # 具名常量放模块级还是放函数里都可以，只要求"6px / 20px 有名字、且渲染时真的用了它"。
        pairs = re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(\d+)\s*;", self.js)
        indents = {name: int(value) for name, value in pairs if "indent" in name.lower()}
        self.assertTrue(indents, "缩进没有用具名常量表达（例如 `const PART_INDENT = 20;`）")
        self.assertIn(6, indents.values(), "总成缩进常量没找到（期望 6）：%s" % indents)
        self.assertIn(20, indents.values(), "零件缩进常量没找到（期望 20）：%s" % indents)
        body = function_body(self.js, "renderNode")
        self.assertTrue(any(name in body for name in indents),
                        "renderNode 没有用这两个具名常量：%s" % body[:400])

    def test_52_part_subactions_have_an_indent_source(self):
        body = function_body(self.js, "buildPartSubActions")
        self.assertTrue(body, "找不到 buildPartSubActions()")
        self.assertRegex(body, r"marginLeft|paddingLeft",
                         "「工艺推荐」行没有任何缩进来源，只能吃 CSS 的 padding-left:10px"
                         "（比总成还靠外，看起来挂在总成上）：%s" % body[:600])
        sub = joined(self.css, ".part-subactions")
        self.assertTrue(has_declaration(sub, "padding-left") or has_declaration(sub, "border-left"),
                        "「工艺推荐」行的左侧引导线被删了：%s" % sub[:300])

    def test_53_parent_child_and_label_contracts_are_untouched(self):
        for token in ("buildClientTree", "asm-meta", "part-confidence", "partStaleMark",
                      "part-subaction", "partAnalysis", "几何✓", "2D✓", "待补参数"):
            with self.subTest(token=token):
                self.assertIn(token, self.js, "零件清单既有契约被删除：%s" % token)


@unittest.skipUnless(NODE, "需要 node 才能真跑零件清单缩进走查")
class PartsTreeIndentDomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(parts_tree_driver(read(APP_JS)))

    def test_60_one_indent_for_every_assembly(self):
        asm = self.out["asm"]
        self.assertEqual(2, len(asm), "总成行数对不上：%s" % asm)
        self.assertEqual(1, len(set(asm)),
                         "总成必须同一缩进，实测 %s（仍按层级各走各的档）" % asm)

    def test_61_one_indent_for_every_part(self):
        part = self.out["part"]
        self.assertEqual(3, len(part), "零件行数对不上：%s" % part)
        self.assertEqual(1, len(set(part)),
                         "零件必须同一缩进，实测 %s（三个零件三档缩进）" % part)

    def test_62_part_is_one_level_inside_the_assembly(self):
        asm = self.out["asm"][0]
        part = self.out["part"][0]
        self.assertEqual(20, int(part.replace("px", "")),
                         "零件缩进应固定为 20px（总成 6px + 一个子级），实测 %s" % part)
        self.assertEqual(6, int(asm.replace("px", "")),
                         "总成缩进应固定为 6px，实测 %s" % asm)

    def test_63_process_button_row_follows_its_part(self):
        sub = self.out["sub"]
        self.assertEqual(3, len(sub), "「工艺推荐」行数对不上：%s" % sub)
        for value in sub:
            with self.subTest(value=value):
                self.assertEqual(self.out["part"][0], value,
                                 "「工艺推荐」必须与所属零件同缩进，实测 %s（零件 %s）"
                                 % (sub, self.out["part"]))

    def test_64_every_part_row_carries_its_subactions(self):
        self.assertEqual(self.out["part_count"], self.out["sub_count"],
                         "有零件行没有配套的子操作行")
        self.assertEqual(2, self.out["asm_count"])

    def test_65_render_node_signature_dropped_depth(self):
        self.assertEqual(3, self.out["render_node_arity"],
                         "renderNode 仍接收 depth 形参（缩进会再次跟层级绑定）")
