"""红测：报价 / 技术工艺 Agent 会话统一 —— 先用户消息、统一执行卡与 Tool List、
详情与思考可折叠、消息白底。

用户口径（本批）：
  1) 任何「用户点按钮 → 会话区出 Agent 输出」的动作，都必须先出用户气泡，再出 Agent。
  2) 一个业务步骤仍是**一张**卡；报价与技术工艺用同一套卡片结构合同。
  3) 卡里的过程行不能再是裸 bullet，要成为 Tool List：状态 icon + 标题 + 次级信息 + 可展开详情。
  4) 查询条件 / 库内条数 / 回退数量 / 待补项这类明细要默认折叠、整行可点、hover 浅蓝。
  5) 思考过程每个有内容的卡底部一个折叠栏，位于卡内，不另起一张卡。
  6) Agent 消息白底 + 浅灰边框 + 深色正文；**用户气泡保持原来的蓝色实心底 + 白字**。
  7) 不新增 / 不修改 font-family。
  8) 过程明细不再有独立的「详情」小标题：**点标题行本身**展开/收起；
     所有缩进子项（查询条件 / 命中 / 差异）折进上一级父行的折叠区，不得与父行平级。

现状缺口（已实测，非推断）：
  · 技术侧 `agent-chat.js` 有 6 处手写 `.oc-amsg` 字符串（需求摘要 / 流程摘要 / 系统提示 /
    检索结果卡 / 确认卡），阶段页 `assembly-integration.js` / `cost-review.js` 各写一套
    `aiSay` / `aiProcessCard`；报价侧 `.tool-activity.trace` 又是一套过程样式。
  · 过程行是 `pushTaskStep()` 拼好的中文 bullet（`.oc-process-step`），没有 title/subtitle/state
    的角色标记；Tool Item 也没有统一的 `data-agent-role` 合同。
  · 报价侧 kickoff（`PENDING_KICKOFF`）、`runStep1` 表单通道、转交任务成功说明三条路径
    不先出用户气泡。
  · 用户气泡在本批**不应**改动：`## 125` 曾把 `.oc-ubub` / `.message-user` 改成白底，
    用户已明确要求改回原来的蓝色实心底 + 白字。
  · 过程行仍挂着独立的「详情」`<summary>`（`## 125` 后已改为标题行可点，但该标签仍然
    看得见）；缩进子项是与父行平级的兄弟节点，没有折进父行。

Spec：docs/specs/quote-tech-unified-tool-list-and-conversation.md
本文件只测本批合同，不覆盖 50 条之外的其它能力。禁止为了让红测转绿而修改本文件。
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
CHAT_JS = FRONTEND / "agent-chat.js"
CHAT_CSS = FRONTEND / "agent-chat.css"
ASSEMBLY = FRONTEND / "assembly-integration.js"
COST = FRONTEND / "cost-review.js"
QUOTE = ROOT / "确认需求解析结果.html"
SPEC = ROOT / "docs" / "specs" / "quote-tech-unified-tool-list-and-conversation.md"

NODE = shutil.which("node")

# 本批明令不得新增 / 修改 font-family：冻结当前两份文件的声明清单。
FONT_FAMILY_BASELINE = (
    "confirm:43:'PingFang SC', -apple-system, Inter, sans-serif",
    "confirm:169:Consolas, Menlo, monospace",
    "confirm:257:inherit",
    "confirm:476:inherit",
    "confirm:498:inherit",
    "confirm:693:inherit",
    "chatcss:207:\"SFMono-Regular\", Consolas, monospace",
)

# 归一化状态词表（写进 root 的 data-status）。
STATES = ("pending", "running", "completed", "failed", "interrupted")


def read(path: pathlib.Path) -> str:
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


def function_source(text: str, name: str) -> str:
    marker = f"function {name}("
    block = block_from(text, marker)
    if not block:
        return ""
    start = text.find(marker)
    if text[max(0, start - 6):start] == "async ":
        start -= 6
    at = text.find(block, start)
    return text[start:at + len(block)]


def arrow_source(text: str, marker: str) -> str:
    block = block_from(text, marker)
    if not block:
        return ""
    start = text.find(marker)
    at = text.find(block, start)
    return text[start:at + len(block)] + ";"


def js_const_block(text: str, name: str) -> str:
    """取 `const NAME = { ... };` / `const NAME = [ ... ];` 这种多行常量。"""
    match = re.search(r"(?m)^\s*const %s = " % re.escape(name), text)
    if not match:
        return ""
    block = block_from(text, f"const {name} = ")
    if not block:
        return ""
    start = match.start()
    at = text.find(block, start)
    return text[start:at + len(block)] + ";"


def rule(text: str, selector: str) -> str:
    """返回单个 CSS 选择器的规则体（选择器后必须紧跟 `{`，避免前缀误命中）。"""
    match = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{", text)
    if not match:
        return ""
    start = text.find("{", match.start())
    end = text.find("}", start)
    return text[start:end + 1] if end > 0 else ""


def enclosing_functions(text: str, needle: str):
    """返回每个 needle 出现位置所属的函数名集合。"""
    pattern = re.compile(r"(?m)^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
    marks = [(m.start(), m.group(1)) for m in pattern.finditer(text)]
    names = set()
    for match in re.finditer(re.escape(needle), text):
        found = None
        for at, name in marks:
            if at < match.start():
                found = name
            else:
                break
        names.add(found or "<top-level>")
    return names


def font_family_fingerprint() -> tuple:
    out = []
    for label, path in (("confirm", QUOTE), ("chatcss", CHAT_CSS)):
        for line_no, line in enumerate(read(path).splitlines(), start=1):
            for match in re.finditer(r"font-family\s*:\s*([^;}]+)", line):
                out.append("%s:%d:%s" % (label, line_no, match.group(1).strip()))
    return tuple(out)


def run_node(driver: str, *args: str) -> dict:
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端会话走查")
    with tempfile.TemporaryDirectory(prefix="cpq-unified-chat-js-") as tmp:
        script = pathlib.Path(tmp) / "driver.js"
        script.write_text(driver, encoding="utf-8")
        completed = subprocess.run([NODE, str(script), *args], capture_output=True,
                                   text=True, timeout=90)
        if completed.returncode != 0:
            raise AssertionError("JS 走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2500:],
                                    completed.stderr[-2500:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# 最小 DOM / HTML 解析替身：让真实的渲染函数在 node 里跑起来并吐出真实节点树
# --------------------------------------------------------------------------- #
DOM_STUB = r'''
var VOID_TAGS = {br:1, hr:1, img:1, input:1, meta:1, link:1, source:1, area:1, base:1,
                 col:1, embed:1, param:1, track:1, wbr:1};

function decodeEntities(text) {
  return String(text == null ? "" : text)
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&");
}

function parseAttrs(raw) {
  var attrs = {};
  if (!raw) return attrs;
  var re = /([A-Za-z_:][-\w:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')|([A-Za-z_:][-\w:.]*)/g;
  var m;
  while ((m = re.exec(raw)) !== null) {
    if (m[1]) attrs[m[1]] = decodeEntities(m[2] !== undefined ? m[2] : m[3]);
    else if (m[4]) attrs[m[4]] = "";
  }
  return attrs;
}

function El(tag, doc) {
  this.tagName = String(tag || "div").toUpperCase();
  this._doc = doc;
  this._id = "";
  this.className = "";
  this._text = "";
  this.children = [];
  this.parentNode = null;
  this._attrs = {};
  this._listeners = {};
  this.style = {};
  this.scrollTop = 0;
  this.scrollHeight = 0;
  this.disabled = false;
  this.value = "";
  this.hidden = false;
  this.open = false;
  this.dataset = {};
}

El.prototype.classes = function () {
  return String(this.className || "").split(/\s+/).filter(Boolean);
};

Object.defineProperty(El.prototype, "classList", {
  get: function () {
    var self = this;
    return {
      add: function () { [].slice.call(arguments).forEach(function (c) {
        if (self.classes().indexOf(c) < 0) self.className = self.classes().concat([c]).join(" "); }); },
      remove: function () { [].slice.call(arguments).forEach(function (c) {
        self.className = self.classes().filter(function (x) { return x !== c; }).join(" "); }); },
      contains: function (c) { return self.classes().indexOf(c) >= 0; },
      toggle: function (c, on) {
        var has = self.classes().indexOf(c) >= 0;
        var want = (on === undefined) ? !has : !!on;
        if (want) this.add(c); else this.remove(c);
        return want;
      }
    };
  }
});

Object.defineProperty(El.prototype, "id", {
  get: function () { return this._id; },
  set: function (value) { this._id = String(value); if (this._doc) this._doc.register(this._id, this); }
});

Object.defineProperty(El.prototype, "textContent", {
  get: function () {
    return this._text + this.children.map(function (c) { return c.textContent; }).join("");
  },
  set: function (value) {
    this._text = (value === null || value === undefined) ? "" : String(value);
    this.children.forEach(function (c) { c.parentNode = null; });
    this.children = [];
  }
});

Object.defineProperty(El.prototype, "innerHTML", {
  get: function () { return this.textContent; },
  set: function (value) {
    this._text = "";
    this.children.forEach(function (c) { c.parentNode = null; });
    this.children = [];
    parseHtmlInto(this, String(value == null ? "" : value));
  }
});

Object.defineProperty(El.prototype, "childNodes", { get: function () { return this.children.slice(); } });
Object.defineProperty(El.prototype, "firstChild", { get: function () { return this.children.length ? this.children[0] : null; } });
Object.defineProperty(El.prototype, "firstElementChild", { get: function () {
  for (var i = 0; i < this.children.length; i += 1) {
    if (this.children[i].tagName !== "#TEXT") return this.children[i];
  }
  return null;
} });

El.prototype.append = function () {
  var self = this;
  [].slice.call(arguments).forEach(function (n) {
    if (typeof n === "string") { self._text += n; return; }
    n.parentNode = self; self.children.push(n);
  });
};
El.prototype.appendChild = function (node) { node.parentNode = this; this.children.push(node); return node; };
El.prototype.prepend = function (node) { node.parentNode = this; this.children.unshift(node); return node; };
El.prototype.insertBefore = function (node, ref) {
  if (!ref) { return this.appendChild(node); }
  var index = this.children.indexOf(ref);
  if (index < 0) { return this.appendChild(node); }
  node.parentNode = this;
  this.children.splice(index, 0, node);
  return node;
};
El.prototype.insertAdjacentHTML = function (position, html) {
  var holder = new El(position === "afterbegin" || position === "beforeend" ? "div" : "div", this._doc);
  parseHtmlInto(holder, String(html == null ? "" : html));
  if (position === "beforeend") {
    var self = this;
    holder.children.forEach(function (c) { c.parentNode = self; self.children.push(c); });
  } else if (position === "afterbegin") {
    var self2 = this;
    holder.children.slice().reverse().forEach(function (c) {
      c.parentNode = self2; self2.children.unshift(c); });
  } else if (position === "beforebegin" && this.parentNode) {
    var parent = this.parentNode;
    var index = parent.children.indexOf(this);
    holder.children.slice().reverse().forEach(function (c) {
      c.parentNode = parent; parent.children.splice(index, 0, c); });
  } else if (position === "afterend" && this.parentNode) {
    var parent2 = this.parentNode;
    var index2 = parent2.children.indexOf(this) + 1;
    holder.children.slice().reverse().forEach(function (c) {
      c.parentNode = parent2; parent2.children.splice(index2, 0, c); });
  }
};
El.prototype.remove = function () {
  if (this.parentNode) {
    var index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
  }
  this.parentNode = null;
};
El.prototype.setAttribute = function (name, value) {
  if (name === "id") { this.id = value; }
  if (name === "class") { this.className = String(value); }
  this._attrs[name] = String(value === undefined || value === null ? "" : value);
  if (name.indexOf("data-") === 0) {
    var key = name.slice(5).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
    this.dataset[key] = this._attrs[name];
  }
};
El.prototype.getAttribute = function (name) {
  if (name === "id") return this._id || null;
  if (name === "class") return this.className || null;
  return Object.prototype.hasOwnProperty.call(this._attrs, name) ? this._attrs[name] : null;
};
El.prototype.hasAttribute = function (name) { return this.getAttribute(name) !== null; };
El.prototype.removeAttribute = function (name) {
  delete this._attrs[name];
  if (name === "class") this.className = "";
};
El.prototype.addEventListener = function (type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); };
El.prototype.removeEventListener = function (type, fn) {
  var list = this._listeners[type] || [];
  var index = list.indexOf(fn);
  if (index >= 0) list.splice(index, 1);
};
El.prototype.dispatch = function (type, event) {
  var self = this;
  (this._listeners[type] || []).slice().forEach(function (fn) {
    fn.call(self, event || { type: type, preventDefault: function () {}, stopPropagation: function () {} });
  });
};
El.prototype.click = function () { this.dispatch("click"); };
El.prototype.focus = function () { this._doc && (this._doc.activeElement = this); };
El.prototype.blur = function () { if (this._doc && this._doc.activeElement === this) this._doc.activeElement = null; };
El.prototype.matches = function (selector) { return matchesSelector(this, selector); };
El.prototype.closest = function (selector) {
  var node = this;
  while (node) { if (matchesSelector(node, selector)) return node; node = node.parentNode; }
  return null;
};
El.prototype.querySelector = function (selector) { var all = queryAll(this, selector); return all.length ? all[0] : null; };
El.prototype.querySelectorAll = function (selector) { return queryAll(this, selector); };

function walk(node, visit) {
  node.children.forEach(function (child) { visit(child); walk(child, visit); });
}

function splitGroups(selector) {
  return String(selector).split(",").map(function (s) { return s.trim(); }).filter(Boolean);
}

function splitCompounds(group) {
  return group.split(/\s+/).filter(Boolean);
}

function matchesCompound(node, compound) {
  if (!node || !node.tagName) return false;
  var rest = compound;
  var tagMatch = /^[A-Za-z][\w-]*/.exec(rest);
  if (tagMatch) {
    if (node.tagName !== tagMatch[0].toUpperCase()) return false;
    rest = rest.slice(tagMatch[0].length);
  }
  var re = /(?:\.([-\w]+))|#([-\w]+)|\[([-\w:]+)(?:([~^$*|]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\]|(\*)/g;
  var m;
  var matchedAny = !!tagMatch;
  while ((m = re.exec(rest)) !== null) {
    matchedAny = true;
    if (m[1] !== undefined) {
      if (node.classes().indexOf(m[1]) < 0) return false;
    } else if (m[2] !== undefined) {
      if (String(node.id) !== m[2]) return false;
    } else if (m[3] !== undefined) {
      var value = node.getAttribute(m[3]);
      if (value === null) return false;
      var want = m[5] !== undefined ? m[5] : (m[6] !== undefined ? m[6] : m[7]);
      if (want === undefined) continue;
      var op = m[4] || "=";
      if (op === "=" && value !== want) return false;
      if (op === "*=" && value.indexOf(want) < 0) return false;
      if (op === "^=" && value.indexOf(want) !== 0) return false;
      if (op === "$=" && value.slice(-want.length) !== want) return false;
      if (op === "~=" && (" " + value + " ").indexOf(" " + want + " ") < 0) return false;
    }
  }
  return matchedAny;
}

function matchesSelector(node, selector) {
  return splitGroups(selector).some(function (group) { return matchesGroup(node, group); });
}

function matchesGroup(node, group) {
  var compounds = splitCompounds(group);
  return matchCompounds(node, compounds, compounds.length - 1);
}

function matchCompounds(node, compounds, index) {
  if (index < 0) return true;
  if (!node || !matchesCompound(node, compounds[index])) return false;
  if (index === 0) return true;
  var parent = node.parentNode;
  while (parent) {
    if (matchCompounds(parent, compounds, index - 1)) return true;
    parent = parent.parentNode;
  }
  return false;
}

function queryAll(root, selector) {
  var out = [];
  splitGroups(selector).forEach(function (group) {
    walk(root, function (node) {
      if (matchesGroup(node, group) && out.indexOf(node) < 0) out.push(node);
    });
  });
  return out;
}

function parseHtmlInto(parent, html) {
  var stack = [parent];
  var re = /<!--[\s\S]*?-->|<\/([A-Za-z][\w:-]*)\s*>|<([A-Za-z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(\/?)>/g;
  var last = 0;
  var m;
  while ((m = re.exec(html)) !== null) {
    if (m.index > last) {
      var text = decodeEntities(html.slice(last, m.index));
      if (text.trim()) {
        var textNode = new El("#text", parent._doc);
        textNode._text = text;
        textNode.parentNode = stack[stack.length - 1];
        stack[stack.length - 1].children.push(textNode);
      }
    }
    last = re.lastIndex;
    if (m[0].indexOf("<!--") === 0) continue;
    if (m[1]) {
      if (stack.length > 1) stack.pop();
      continue;
    }
    var node = new El(m[2], parent._doc);
    var attrs = parseAttrs(m[3]);
    Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    node.parentNode = stack[stack.length - 1];
    stack[stack.length - 1].children.push(node);
    if (!VOID_TAGS[String(m[2]).toLowerCase()] && !m[4]) stack.push(node);
  }
  if (last < html.length) {
    var tail = decodeEntities(html.slice(last));
    if (tail.trim()) {
      var tailNode = new El("#text", parent._doc);
      tailNode._text = tail;
      tailNode.parentNode = stack[stack.length - 1];
      stack[stack.length - 1].children.push(tailNode);
    }
  }
}

function makeDoc() {
  var doc = {
    _byId: {},
    activeElement: null,
    register: function (id, node) { this._byId[String(id)] = node; },
    getElementById: function (id) { return this._byId[String(id)] || null; },
    createElement: function (tag) { return new El(tag, this); },
    querySelector: function (selector) {
      for (var id in this._byId) {
        var node = this._byId[id];
        if (matchesSelector(node, selector)) return node;
      }
      return null;
    },
    querySelectorAll: function (selector) {
      var out = [];
      for (var id in this._byId) {
        var node = this._byId[id];
        if (matchesSelector(node, selector)) out.push(node);
      }
      return out;
    },
    addEventListener: function () {},
    body: null,
    head: null
  };
  doc.body = new El("body", doc);
  doc.head = new El("head", doc);
  return doc;
}

function roles(node) {
  return (node ? node.getAttribute("data-agent-role") : null) || "";
}
function allRoles(root) {
  return queryAll(root, "[data-agent-role]").map(function (n) {
    return { role: n.getAttribute("data-agent-role"), cls: String(n.className || "") };
  });
}
function names(node) {
  return (node ? node.children : []).filter(function (c) { return c.tagName !== "#TEXT"; })
    .map(function (c) { return String(c.className || c.tagName); });
}
'''
# --------------------------------------------------------------------------- #
# 走查 1：技术工艺左栏（真跑 agent-chat.js 的渲染函数）
# --------------------------------------------------------------------------- #
TECH_PREAMBLE = r'''
var doc = makeDoc();
var document = doc;
var tinner = doc.createElement("div"); tinner.id = "ocTinner";
var thread = doc.createElement("div"); thread.id = "ocThread";
var replayingHistory = false;
var activeTurnCtx = null;
var taskProgressCards = new Map();
var PERSISTED = [];
var NOTED = [];
var REPLAYED = [];
function scrollDown() {}
function clearEmpty() {}
function persistSessionEvent(event) { PERSISTED.push(event); }
function noteInThread(text) { NOTED.push(String(text)); }
function replayTimelineTask(event) { REPLAYED.push(event); }
function modelRowKey(detail) { return ""; }
function findModelRow(card, key) { return null; }
function mergeModelRow(card, row, text, detail) {}
function modelRowMap(card) { return null; }
function renderMarkdown(source) { return String(source); }
function boardStage() { return "process"; }
var echoedTaskPrompts = new Set();
var window = { TechSessionTimeline: { forShell: function (o) {
  return (o && o.messages ? o.messages : []).concat(o && o.events ? o.events : []); } } };
'''

# 过程行里「详情」标签的可见性探针：用户口径是「没有详情这个东西，点标题行展开」，
# 所以只允许两种情况：标签不存在，或标签被藏起来（hidden / aria-hidden / CSS display:none）。
# 允许保留不可见的兼容节点（若干既有合同仍读它），但用户不能看见第二个开关。
DETAIL_LABEL_PROBE = r"""
function ownLabel(node) {
  if (!node) return "";
  var text = (node._text === undefined) ? String(node.textContent) : String(node._text);
  return text.trim();
}
function cssHidesDetailLabel(css) {
  var blocks = String(css || "").split("}");
  for (var i = 0; i < blocks.length; i += 1) {
    var parts = blocks[i].split("{");
    if (parts.length < 2) continue;
    var sel = parts[0];
    var body = parts[1];
    if (sel.indexOf("summary") < 0 || sel.indexOf("oc-process-detail") < 0) continue;
    if (sel.indexOf("::") >= 0) continue;   // 伪元素（::-webkit-details-marker / ::before）不算隐藏标签本身
    if (/:hover|:focus|:active/.test(sel)) continue;
    if (/display\s*:\s*none/.test(body) || /visibility\s*:\s*hidden/.test(body)) return true;
  }
  return false;
}
function detailLabelState(root, css) {
  if (!root) return "absent";
  var nodes = root.querySelectorAll("summary, button");
  for (var i = 0; i < nodes.length; i += 1) {
    var node = nodes[i];
    if (ownLabel(node) !== "\u8be6\u60c5") continue;
    if (node.hidden || node.getAttribute("aria-hidden") === "true") return "hidden";
    if (node.tagName === "SUMMARY" && cssHidesDetailLabel(css)) return "hidden";
    return "visible";
  }
  return "absent";
}
"""

TECH_TAIL = r'''
var out = {};
out.missing = MISSING;

addUser("帮我解析这张图纸。");
var ctx = addAssistant();
var wrap = ctx && ctx.wrap ? ctx.wrap : null;
out.turn_order = names(tinner);
out.root_attr = wrap ? wrap.getAttribute("data-agent-card") : null;
out.root_status = wrap ? wrap.getAttribute("data-status") : null;
out.root_roles = wrap ? allRoles(wrap).map(function (r) { return r.role; }) : null;
out.header_role_cls = wrap ? (function () {
  var n = wrap.querySelector('[data-agent-role="header"]');
  return n ? String(n.className) : null;
})() : null;
out.body_role_cls = wrap ? (function () {
  var n = wrap.querySelector('[data-agent-role="body"]');
  return n ? String(n.className) : null;
})() : null;

if (typeof setAssistantState === "function") { setAssistantState(ctx, "succeeded"); }
out.status_after_done = wrap ? wrap.getAttribute("data-status") : null;

addToolCard(ctx, { id: "t1", name: "LookupComponentLibrary",
                   input: { part_id: "P-001", params: { length: 108 } } });
var toolNode = ctx && ctx.cards ? ctx.cards["t1"] : null;
var stateNode = toolNode ? toolNode.state : null;
var toolItem = stateNode && stateNode.closest ? stateNode.closest('[data-agent-role="tool-item"]') : null;
out.tool_item_found = !!toolItem;
out.tool_item_card_attr = toolItem ? toolItem.getAttribute("data-agent-card") : null;
out.tool_item_state = toolItem ? toolItem.getAttribute("data-state") : null;
out.tool_item_roles = toolItem ? allRoles(toolItem).map(function (r) { return r.role; }) : null;

appendThinking(ctx, "先看图纸，再定参数。");
out.thinking_found = wrap ? !!wrap.querySelector('[data-agent-role="thinking"]') : false;
out.thinking_open = wrap ? (function () {
  var n = wrap.querySelector('[data-agent-role="thinking"]');
  return n ? !!n.open : null;
})() : null;
appendThinking(ctx, "");
out.thinking_count = wrap ? wrap.querySelectorAll('[data-agent-role="thinking"]').length : 0;

var steps = doc.createElement("div");
steps.className = "oc-process-steps";
var fakeCard = { steps: steps, status: "running", box: doc.createElement("div"),
                 state: doc.createElement("span") };
pushTaskStep(fakeCard, "查询条件：length=108、width=56", "", "tool",
  { tool: "component_match", title: "零部件库检索", status: "running",
    input: { part_id: "P-001", params: { length: 108, width: 56 } },
    output: { decision: "modify", score: 0.648 } });
var row = steps.children[0];
out.row_found = !!row;
out.row_role = row ? row.getAttribute("data-agent-role") : null;
out.row_state = row ? row.getAttribute("data-state") : null;
out.row_roles = row ? allRoles(row).map(function (r) { return r.role; }) : null;
out.row_text = row ? row.textContent : "";
out.row_card_attr = row ? row.getAttribute("data-agent-card") : null;
out.row_title = row ? (function () {
  var n = row.querySelector('[data-agent-role="tool-title"]');
  return n ? n.textContent : null;
})() : null;
function isCollapsed(node) {
  if (!node) return null;
  if (node.tagName === "DETAILS") return !node.open;
  return !!(node.hidden || node.hasAttribute("hidden")
            || node.getAttribute("aria-hidden") === "true"
            || node.getAttribute("data-collapsed") === "true");
}
out.row_detail_tag = row ? (function () {
  var n = row.querySelector('[data-agent-role="tool-detail"]');
  return n ? n.tagName : null;
})() : null;
out.row_detail_collapsed = row ? isCollapsed(row.querySelector('[data-agent-role="tool-detail"]')) : null;
out.row_toggle_tag = row ? (function () {
  var n = row.querySelector('[data-agent-role="tool-toggle"]');
  return n ? n.tagName : null;
})() : null;
out.row_toggle_contains_title = row ? (function () {
  var n = row.querySelector('[data-agent-role="tool-toggle"]');
  return !!(n && n.querySelector('[data-agent-role="tool-title"]'));
})() : null;
out.row_toggle_aria = row ? (function () {
  var n = row.querySelector('[data-agent-role="tool-toggle"]');
  return n ? n.getAttribute("aria-expanded") : null;
})() : null;
out.row_toggle_keyboard = row ? (function () {
  var n = row.querySelector('[data-agent-role="tool-toggle"]');
  if (!n) return null;
  if (n.tagName === "BUTTON" || n.tagName === "SUMMARY") return true;
  return n.getAttribute("role") === "button" && n.getAttribute("tabindex") === "0";
})() : null;
out.row_detail_label_state = detailLabelState(row, CSS);
out.row_has_detail_word = out.row_detail_label_state === "visible";

var lossSteps = doc.createElement("div");
var lossCard = { steps: lossSteps, status: "running", box: doc.createElement("div"),
                 state: doc.createElement("span") };
pushTaskStep(lossCard, "费率 0 条 / 回退 global 0 条 / 系数 0 条 / 待补 10 项", "", "progress",
  { tool: "cost_lookup", title: "成本库检索", status: "ok", input: { a: 1 }, output: { b: 2 } });
var lossRow = lossSteps.children[0];
out.lossless_tokens = ["费率 0 条", "回退 global 0 条", "系数 0 条", "待补 10 项"];
out.lossless_text_kept = lossRow
  ? out.lossless_tokens.every(function (t) { return lossRow.textContent.indexOf(t) >= 0; })
  : false;
out.row_detail_keeps_full_input = row ? (row.textContent.indexOf("component_match") >= 0)
                                     : false;

var before = tinner.children.length;
renderHistory([{ type: "user", text: "历史里的用户话" },
               { type: "assistant", text: "历史里的回复" }], []);
out.history_with_user = tinner.children.slice(before).map(function (c) {
  return String(c.className); });
var before2 = tinner.children.length;
renderHistory([{ type: "assistant", text: "只有助手" }], []);
out.history_without_user = tinner.children.slice(before2).map(function (c) {
  return String(c.className); });

var nestSteps = doc.createElement("div");
var nestCard = { steps: nestSteps, status: "running", box: doc.createElement("div"),
                 state: doc.createElement("span") };
pushTaskStep(nestCard, "检索零部件库（1/4）：P-001 上壳", "", "tool");
pushTaskStep(nestCard, "  查询条件：length=108、width=56、height=13.25、hole_diameter=4", "", "tool",
  { tool: "component_match", title: "零部件库检索", status: "ok",
    input: { part_id: "P-001" }, output: { decision: "modify", score: 0.648 } });
pushTaskStep(nestCard, "  命中 CMP-SEMI-EE-BLOCK-0001 搬运吸嘴主体安装块（可改制，匹配度 65%）", "", "tool");
out.nest_top_level_count = nestSteps.children.length;
var nestParent = nestSteps.children[0];
out.nest_parent_role = nestParent ? nestParent.getAttribute("data-agent-role") : null;
out.nest_parent_text = nestParent ? String(nestParent.textContent) : null;
out.nest_detail_text = nestParent ? String(nestParent.textContent) : null;
out.nest_detail_collapsed = nestParent
  ? isCollapsed(nestParent.querySelector('[data-agent-role="tool-detail"]')) : null;
out.nest_detail_label_state = detailLabelState(nestSteps, CSS);
out.nest_has_detail_word = out.nest_detail_label_state === "visible";

var ctx2 = addAssistant();
appendThinking(ctx2, "");
out.empty_thinking_count = ctx2.wrap
  ? ctx2.wrap.querySelectorAll('[data-agent-role="thinking"]').length : -1;

var before3 = tinner.children.length;
renderHistory([{ type: "assistant", text: "带思考的历史回复", thinking: "历史思考" }], []);
var historyCard = tinner.children[before3];
out.history_thinking_found = historyCard
  ? !!historyCard.querySelector('[data-agent-role="thinking"]') : false;

var thinkingNode = wrap ? wrap.querySelector('[data-agent-role="thinking"]') : null;
out.thinking_tag = thinkingNode ? thinkingNode.tagName : null;
out.thinking_summary_tag = thinkingNode && thinkingNode.firstElementChild
  ? thinkingNode.firstElementChild.tagName : null;
out.thinking_inside_card = thinkingNode && wrap
  ? (thinkingNode.closest("[data-agent-card]") === wrap) : false;

if (typeof setAssistantState === "function") { setAssistantState(ctx, "failed"); }
var chip = wrap ? wrap.querySelector('[data-agent-role="status"]') : null;
if (!chip && wrap) { chip = wrap.querySelector(".oc-alabel-state"); }
out.failed_chip_text = chip ? chip.textContent : null;
out.failed_status_attr = wrap ? wrap.getAttribute("data-status") : null;

PERSISTED.length = 0;
var bubblesBefore = tinner.children.filter(function (c) {
  return String(c.className).indexOf("oc-ubub") >= 0; }).length;
out.echo_persisted = [];
if (typeof echoTaskPrompt === "function") {
  echoTaskPrompt({ prompt: "请解析当前需求", runId: "run-1" });
  out.echo_persisted = PERSISTED.slice();
}
out.echo_bubbles_added = tinner.children.filter(function (c) {
  return String(c.className).indexOf("oc-ubub") >= 0; }).length - bubblesBefore;

tinner.textContent = "";
addAssistant(); addAssistant();
out.two_cards = tinner.querySelectorAll("[data-agent-card]").length;

console.log(JSON.stringify(out));
'''


def _js_or_missing(text: str, kind: str, name: str, missing: list) -> str:
    if kind == "fn":
        src = function_source(text, name)
    elif kind == "arrow":
        src = arrow_source(text, f"const {name} = ")
    else:
        src = js_const_block(text, name)
    if not src:
        missing.append(name)
    return src


def tech_chat_driver() -> str:
    js = read(CHAT_JS)
    missing: list = []
    parts = [
        DOM_STUB,
        TECH_PREAMBLE,
        "var MISSING = %s;" % json.dumps(missing),
        "var CSS = %s;" % json.dumps(read(CHAT_CSS)),
        DETAIL_LABEL_PROBE,
        _js_or_missing(js, "arrow", "el", missing),
        _js_or_missing(js, "const", "TOOL_ICONS", missing),
        _js_or_missing(js, "arrow", "toolIcon", missing),
        _js_or_missing(js, "const", "TOOL_TRACE_LABELS", missing),
        _js_or_missing(js, "fn", "toolSubtitle", missing),
        _js_or_missing(js, "fn", "toolTraceLabel", missing),
        _js_or_missing(js, "fn", "echoTaskPrompt", missing),
        _js_or_missing(js, "fn", "addUser", missing),
        _js_or_missing(js, "fn", "identityLabel", missing),
        _js_or_missing(js, "fn", "pushSystem", missing),
        _js_or_missing(js, "fn", "addAssistant", missing),
        _js_or_missing(js, "fn", "setAssistantState", missing),
        _js_or_missing(js, "fn", "appendThinking", missing),
        _js_or_missing(js, "fn", "addToolCard", missing),
        _js_or_missing(js, "fn", "setToolResult", missing),
        _js_or_missing(js, "fn", "pushTaskStep", missing),
        _js_or_missing(js, "fn", "renderHistory", missing),
        TECH_TAIL,
    ]
    parts[2] = "var MISSING = %s;" % json.dumps(missing)
    return "\n".join(p for p in parts if p)


QUOTE_CHAT_PREAMBLE = r'''
var doc = makeDoc();
var document = doc;
var chatEl = doc.createElement("div"); chatEl.className = "chat-messages";
var streamBubble = null;
var streamBuf = "";
var stageEl = null;
function scrollChat() {}
function renderMarkdown(source) { return String(source); }
function finishStreamBubble() { streamBubble = null; streamBuf = ""; }
function clearStage() { if (stageEl) { stageEl.remove(); stageEl = null; } }
function hideTyping() {}
'''

QUOTE_CHAT_TAIL = r'''
var out = {};
out.missing = MISSING;

addUserBubble("请解析当前需求");
var bubble = ensureStreamBubble();
out.turn_order = names(chatEl);
out.ai_root_attr = bubble ? bubble.getAttribute("data-agent-card") : null;
out.ai_status = bubble ? bubble.getAttribute("data-status") : null;
out.ai_roles = bubble ? allRoles(bubble).map(function (r) { return r.role; }) : null;

var childrenBeforeThinking = chatEl.children.length;
appendThinkingText("先想一下");
out.thinking_found = bubble ? !!bubble.querySelector('[data-agent-role="thinking"]') : false;
var thinkingNode = bubble ? bubble.querySelector('[data-agent-role="thinking"]') : null;
out.thinking_open = thinkingNode ? !!thinkingNode.open : null;
out.thinking_tag = thinkingNode ? thinkingNode.tagName : null;
out.thinking_summary_tag = thinkingNode && thinkingNode.firstElementChild
  ? thinkingNode.firstElementChild.tagName : null;
out.thinking_added_no_new_bubble = chatEl.children.length === childrenBeforeThinking;
out.thinking_inside_card = thinkingNode && bubble
  ? (thinkingNode.closest("[data-agent-card]") === bubble) : false;

var trace = addToolActivity("调用工具", "来源");
out.trace_root_attr = trace ? trace.getAttribute("data-agent-card") : null;
out.trace_roles = trace ? allRoles(trace).map(function (r) { return r.role; }) : null;

addErrorBubble("出错了");
var errNode = chatEl.children[chatEl.children.length - 1];
out.error_root_attr = errNode ? errNode.getAttribute("data-agent-card") : null;
out.error_status = errNode ? errNode.getAttribute("data-status") : null;
out.error_turn_order = names(chatEl);

console.log(JSON.stringify(out));
'''


def quote_chat_driver() -> str:
    js = read(QUOTE)
    missing: list = []
    parts = [
        DOM_STUB,
        QUOTE_CHAT_PREAMBLE,
        "var MISSING = %s;" % json.dumps(missing),
        _js_or_missing(js, "fn", "addUserBubble", missing),
        _js_or_missing(js, "fn", "ensureStreamBubble", missing),
        _js_or_missing(js, "fn", "appendThinkingText", missing),
        _js_or_missing(js, "fn", "addToolActivity", missing),
        _js_or_missing(js, "fn", "addErrorBubble", missing),
        QUOTE_CHAT_TAIL,
    ]
    parts[2] = "var MISSING = %s;" % json.dumps(missing)
    return "\n".join(p for p in parts if p)
# --------------------------------------------------------------------------- #
# 走查 2：报价侧按钮入口（真跑 确认需求解析结果.html 里的入口函数，原语打桩捕顺序）
# --------------------------------------------------------------------------- #
QUOTE_TURN_PREAMBLE = r'''
var doc = makeDoc();
var document = doc;
var CALLS = [];
var busy = false;
var manualMode = false;
var currentStep = 1;
var shownStep = 1;
var STEPS = ["测算基本信息", "工艺确认", "定价-利润加成", "报价-其他加价项", "报价方案", "生成报价单"];
var chatFiles = [];
var STEP1_REINPUT = false;
var STEP1_LAST_REQ = "";
var STEP1_LAST_DISPLAY = "";
var STEP1_FORM_TASK = null;
var GATE_BLOCKED = false;
var WF = {};
var INPUTS = {};
function $(id) {
  if (!INPUTS[id]) { var node = doc.createElement("input"); node.id = id; INPUTS[id] = node; }
  return INPUTS[id];
}
function alert(message) { CALLS.push("alert:" + String(message)); }
function buildAttachMessage(text) { return String(text); }
function renderAttachChips() {}
function addUserBubble(text) { CALLS.push("user:" + String(text)); }
function addAiBubble(text) { CALLS.push("ai:" + String(text)); }
function sendToAgent(text, opts) {
  CALLS.push("agent:" + String((opts && opts.display) || ""));
  return Promise.resolve();
}
function step1Intent(text, display) { return Promise.resolve({ requirement: "r", comment: "" }); }
function step1Match(requirement, comment) { CALLS.push("match"); return Promise.resolve(true); }
function runStep1(text, display) { CALLS.push("runStep1"); return Promise.resolve(true); }
'''

QUOTE_TURN_TAIL = r'''
var out = {};
out.missing = MISSING;
var tick = function () { return new Promise(function (r) { setTimeout(r, 0); }); };

(async function () {
  CALLS.length = 0;
  $("chatInput").value = "你好，帮我报价";
  sendFromInput();
  out.send_from_input = CALLS.slice();

  CALLS.length = 0;
  fillStepRecommend();
  out.fill_recommend = CALLS.slice();

  CALLS.length = 0;
  STEP1_FORM_TASK = "填表任务";
  await runStep1("原始需求正文", "用户看到的需求");
  out.run_step1 = CALLS.slice();

  CALLS.length = 0;
  fillStepRecommend();
  fillStepRecommend();
  out.two_quick_actions = CALLS.slice();

  console.log(JSON.stringify(out));
})();
'''


def quote_turn_driver() -> str:
    js = read(QUOTE)
    missing: list = []
    parts = [
        DOM_STUB,
        QUOTE_TURN_PREAMBLE,
        "var MISSING = %s;" % json.dumps(missing),
        _js_or_missing(js, "fn", "sendFromInput", missing),
        _js_or_missing(js, "fn", "fillStepRecommend", missing),
        _js_or_missing(js, "fn", "runStep1", missing),
        QUOTE_TURN_TAIL,
    ]
    parts[2] = "var MISSING = %s;" % json.dumps(missing)
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------- #
# 走查 3：技术工艺阶段页看板会话（真跑 aiProcessCard / crCard）
# --------------------------------------------------------------------------- #
BOARD_PREAMBLE = r'''
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
'''

BOARD_TAIL = r'''
var out = {};
out.missing = MISSING;
out.process_roles = [];
out.process_root_attr = null;
out.process_status = null;
out.tool_rows = [];
out.process_detail_label_state = null;

aiUserSay("请重新生成工艺推荐");
try {
  var card = aiProcessCard("参数推荐 · 智能重算");
  card.log(["查询同类件", "  P-001 主体外壳", "库内无同类件，按新制评估"]);
  var root = $ai("aiTinner").children[0];
  out.turn_order = names($ai("aiTinner"));
  out.process_root_attr = root ? root.getAttribute("data-agent-card") : null;
  out.process_status = root ? root.getAttribute("data-status") : null;
  out.process_roles = root ? allRoles(root).map(function (r) { return r.role; }) : [];
  var steps = root ? root.querySelector('[data-agent-role="tools"]') : null;
  if (!steps) { steps = root ? root.querySelector(".oc-process-steps") : null; }
  out.process_detail_label_state = detailLabelState(steps, "");
  if (steps) {
    out.tool_rows = steps.children.map(function (row) {
      return { role: row.getAttribute("data-agent-role"),
               state: row.getAttribute("data-state"),
               text: row.textContent };
    });
  }
  out.user_bubble_role = $ai("aiTinner").children[0]
    ? String($ai("aiTinner").children[0].className) : null;
} catch (error) {
  out.error = String(error && error.message || error);
}

try {
  out.cr_has_card_fn = typeof crCard === "function";
  if (out.cr_has_card_fn) {
    var cr = crCard("零件成本 · P-001");
    cr.log(["查询成本库", "  P-001 主体外壳"]);
    var crRoot = $cr("crTinner").children[$cr("crTinner").children.length - 1];
    out.cr_root_attr = crRoot ? crRoot.getAttribute("data-agent-card") : null;
    out.cr_status = crRoot ? crRoot.getAttribute("data-status") : null;
    out.cr_roles = crRoot ? allRoles(crRoot).map(function (r) { return r.role; }) : [];
  }
} catch (error) {
  out.cr_error = String(error && error.message || error);
}

out.has_cr_user_say = typeof crUserSay === "function";

console.log(JSON.stringify(out));
'''


def board_driver() -> str:
    asm = read(ASSEMBLY)
    cost = read(COST)
    missing: list = []
    parts = [
        DOM_STUB,
        BOARD_PREAMBLE,
        "var MISSING = %s;" % json.dumps(missing),
        DETAIL_LABEL_PROBE,
        _js_or_missing(asm, "fn", "aiThreadAppend", missing),
        _js_or_missing(asm, "fn", "aiUserSay", missing),
        _js_or_missing(asm, "fn", "aiProcessCard", missing),
        _js_or_missing(cost, "fn", "crAppend", missing),
        _js_or_missing(cost, "fn", "crSay", missing),
        _js_or_missing(cost, "fn", "crUserSay", missing),
        _js_or_missing(cost, "fn", "crCard", missing),
        BOARD_TAIL,
    ]
    parts[2] = "var MISSING = %s;" % json.dumps(missing)
    return "\n".join(p for p in parts if p)
# --------------------------------------------------------------------------- #
# 合同工具
# --------------------------------------------------------------------------- #
READ_ONLY_MARKERS = ("querySelector", ".remove()", "closest(", "matches(", "getAttribute")


def creation_sites(text: str, needle: str):
    """只挑「真的在建节点」的出现位置，跳过纯读取（querySelector / remove / closest）。"""
    sites = []
    for match in re.finditer(re.escape(needle), text):
        window = text[max(0, match.start() - 300):match.end() + 300]
        if any(marker in window for marker in READ_ONLY_MARKERS):
            continue
        sites.append(match.start())
    return sites


def card_creator_sites(text: str, needle: str, root_marker: str):
    """返回没有 root 标记的建卡位置数量。"""
    unmarked = 0
    for start in creation_sites(text, needle):
        window = text[max(0, start - 800):start + 800]
        if root_marker not in window:
            unmarked += 1
    return unmarked


def style_backgrounds(rule_body: str):
    return re.findall(r"background(?:-color)?\s*:\s*([^;}]+)", rule_body)


WHITE_VALUES = ("#fff", "#ffffff", "white", "var(--bg-page)", "var(--surface", "var(--oc-bg-1)")


def is_white(value: str) -> bool:
    lowered = value.strip().lower()
    return any(lowered.startswith(item) for item in WHITE_VALUES)


class ChatHarnessMixin:
    @classmethod
    def setUpClass(cls):
        cls.tech = run_node(tech_chat_driver())
        cls.quote_chat = run_node(quote_chat_driver())
        cls.turn = run_node(quote_turn_driver())
        cls.board = run_node(board_driver())
        cls.chat_js = read(CHAT_JS)
        cls.chat_css = read(CHAT_CSS)
        cls.quote_html = read(QUOTE)
        cls.assembly = read(ASSEMBLY)
        cls.cost = read(COST)

    def require_no_missing(self, report, label):
        missing = report.get("missing") or []
        self.assertEqual(
            [], missing,
            "%s：本批合同依赖的渲染函数找不到（被删除或改名）：%s" % (label, missing),
        )


# --------------------------------------------------------------------------- #
# A. 两侧统一卡片
# --------------------------------------------------------------------------- #
class AUnifiedCardContract(ChatHarnessMixin, unittest.TestCase):
    def test_a1_both_sides_root_card_carries_the_same_markers(self):
        self.require_no_missing(self.tech, "技术工艺左栏")
        self.require_no_missing(self.quote_chat, "报价会话")
        self.require_no_missing(self.board, "技术工艺阶段页看板")
        self.assertIsNotNone(self.tech["root_attr"],
                             "技术侧助手卡没有 data-agent-card 根标记")
        self.assertIsNotNone(self.quote_chat["ai_root_attr"],
                             "报价侧助手卡没有 data-agent-card 根标记")
        self.assertIsNotNone(self.board["process_root_attr"],
                             "阶段页过程卡没有 data-agent-card 根标记")
        for label, status in (("技术侧", self.tech["root_status"]),
                              ("报价侧", self.quote_chat["ai_status"]),
                              ("阶段页", self.board["process_status"])):
            with self.subTest(side=label):
                self.assertIn(status, STATES,
                              "%s 根卡的 data-status 不是归一化状态：%r" % (label, status))

    def test_a2_header_and_body_roles_exist_on_both_sides(self):
        tech_roles = set(self.tech["root_roles"] or [])
        for role in ("header", "body"):
            with self.subTest(side="tech", role=role):
                self.assertIn(role, tech_roles, "技术侧助手卡缺 data-agent-role=%s" % role)
        quote_roles = set(self.quote_chat["ai_roles"] or [])
        for role in ("header", "body"):
            with self.subTest(side="quote", role=role):
                self.assertIn(role, quote_roles, "报价侧助手卡缺 data-agent-role=%s" % role)
        board_roles = set(self.board["process_roles"] or [])
        for role in ("header", "body", "tools"):
            with self.subTest(side="board", role=role):
                self.assertIn(role, board_roles, "阶段页过程卡缺 data-agent-role=%s" % role)

    def test_a3_one_business_step_still_makes_one_card(self):
        self.assertEqual(2, self.tech["two_cards"],
                         "两次 addAssistant() 应产生两张执行卡，实际 %s 张"
                         % self.tech["two_cards"])

    def test_a4_tool_items_are_not_their_own_card(self):
        self.assertEqual("tool-item", self.tech["row_role"],
                         "过程行没有 Tool Item 角色（%r）" % self.tech["row_role"])
        self.assertIsNone(self.tech["row_card_attr"],
                          "Tool Item 不该带 data-agent-card（一步一卡）")
        for row in self.board["tool_rows"] or []:
            with self.subTest(text=str(row.get("text"))[:40]):
                self.assertNotEqual("tools", row.get("role"))
        step_rule = rule(self.chat_css, ".oc-process-step")
        self.assertTrue(step_rule, "agent-chat.css 找不到 .oc-process-step 规则")
        for value in style_backgrounds(step_rule):
            self.assertIn("transparent", value,
                          "工具项涨成了独立卡片：.oc-process-step 不该有底色 %r" % value)
        self.assertNotIn("box-shadow", step_rule, "工具项不该带阴影")

    def test_a5_summary_and_trace_cards_do_not_bypass_the_constructor(self):
        unmarked = card_creator_sites(self.chat_js, "oc-amsg", "data-agent-card")
        self.assertEqual(
            0, unmarked,
            "agent-chat.js 里有 %d 处直接拼 .oc-amsg，没有走统一执行卡构造器（缺 data-agent-card）"
            % unmarked,
        )
        unmarked_quote = card_creator_sites(self.quote_html, "message message-ai", "data-agent-card")
        self.assertEqual(
            0, unmarked_quote,
            "确认需求解析结果.html 里有 %d 处直接拼 .message-ai，没有走统一执行卡构造器"
            % unmarked_quote,
        )

    def test_a6_error_state_is_a_modifier_not_a_second_card(self):
        self.assertIsNotNone(self.quote_chat["error_root_attr"],
                             "报价错误气泡没有统一执行卡根标记")
        self.assertEqual("failed", self.quote_chat["error_status"],
                         "报价错误气泡的 data-status 不是 failed：%r"
                         % self.quote_chat["error_status"])
        error_rule = rule(self.quote_html, ".message-error")
        self.assertTrue(error_rule, "找不到 .message-error 规则")
        self.assertNotIn("background", error_rule,
                         "错误态涨成了第二套卡片：.message-error 不该另起底色")
        self.assertNotIn("border", error_rule,
                         "错误态涨成了第二套卡片：.message-error 不该另起边框")


# --------------------------------------------------------------------------- #
# B. 用户消息必须先出现
# --------------------------------------------------------------------------- #
class BUserBubbleBeforeAgent(ChatHarnessMixin, unittest.TestCase):
    def test_b7_input_send_keeps_user_before_agent(self):
        calls = self.turn["send_from_input"]
        self.assertTrue(calls, "sendFromInput() 没有产生任何会话动作")
        self.assertTrue(calls[0].startswith("user:"),
                        "输入框发送没有先出用户气泡：%s" % calls)
        self.assertLess(calls.index(calls[0]),
                        min(i for i, c in enumerate(calls) if c.startswith("agent:")),
                        "用户气泡必须排在 Agent 输出之前：%s" % calls)

    def test_b8_quote_quick_action_emits_user_bubble_before_agent(self):
        calls = self.turn["fill_recommend"]
        self.assertTrue(calls, "fillStepRecommend() 没有产生任何会话动作")
        self.assertTrue(calls[0].startswith("user:"),
                        "报价快捷动作没有先出用户气泡：%s" % calls)

    def test_b8b_run_step1_emits_user_bubble_before_agent(self):
        calls = self.turn["run_step1"]
        self.assertTrue(calls, "runStep1() 没有产生任何会话动作")
        self.assertTrue(
            calls[0].startswith("user:"),
            "新建报价 kickoff / runStep1 直接出 Agent 输出（用户气泡缺失）：%s" % calls,
        )
        self.assertLess(
            calls.index(calls[0]),
            min(i for i, c in enumerate(calls) if c.startswith("agent:")),
            "用户气泡必须排在 Agent 输出之前：%s" % calls,
        )

    def test_b9_tech_board_every_card_site_emits_user_bubble(self):
        for path, callee, bubble_fn in ((ASSEMBLY, "aiProcessCard(", "aiUserSay("),
                                        (COST, "crCard(", "crUserSay(")):
            text = read(path)
            callers = {name for name in enclosing_functions(text, callee)
                       if name not in ("aiProcessCard", "crCard", "<top-level>")}
            for name in sorted(callers):
                with self.subTest(file=path.name, fn=name):
                    body = block_from(text, f"function {name}(")
                    self.assertIn(
                        bubble_fn, body,
                        "%s.%s() 会在会话区出卡，但函数里没有 %s（用户气泡缺失）"
                        % (path.name, name, bubble_fn),
                    )

    def test_b10_regenerate_class_actions_share_the_same_order(self):
        calls = self.turn["two_quick_actions"]
        self.assertGreaterEqual(len(calls), 4, "连续两次快捷动作没有各出一次用户气泡：%s" % calls)
        self.assertTrue(calls[0].startswith("user:") and calls[1].startswith("agent:"),
                        "第一次动作的顺序不是「用户 → Agent」：%s" % calls)
        self.assertTrue(calls[2].startswith("user:") and calls[3].startswith("agent:"),
                        "第二次动作的顺序不是「用户 → Agent」：%s" % calls)

    def test_b11_failure_response_still_keeps_the_user_bubble(self):
        self.require_no_missing(self.quote_chat, "报价会话")
        self.assertEqual("message message-user", self.quote_chat["error_turn_order"][0],
                         "失败回合里用户气泡丢了：%s" % self.quote_chat["error_turn_order"])
        self.assertIn("message-error", self.quote_chat["error_turn_order"][-1],
                      "失败没有落在会话流末尾：%s" % self.quote_chat["error_turn_order"])

    def test_b12_two_consecutive_triggers_keep_turn_pairs(self):
        calls = self.turn["two_quick_actions"]
        self.assertNotEqual(
            ["user", "user", "agent", "agent"],
            [c.split(":")[0] for c in calls],
            "连续触发串序成了「用户A、用户B、AgentA、AgentB」：%s" % calls,
        )

    def test_b13_recovery_and_notice_paths_do_not_fabricate_user_bubbles(self):
        self.assertEqual(["oc-amsg"], self.tech["history_without_user"],
                         "只有 assistant 的历史回合被凭空补了用户气泡：%s"
                         % self.tech["history_without_user"])
        for path, bubble_fn in ((ASSEMBLY, "aiUserSay("), (COST, "crUserSay(")):
            text = read(path)
            for replay in ("aiReplayTimeline", "crReplayTimeline"):
                body = block_from(text, f"function {replay}(")
                if not body:
                    continue
                with self.subTest(file=path.name, fn=replay):
                    self.assertNotIn(bubble_fn, body,
                                     "%s 是纯后台恢复，不得伪造用户气泡" % replay)

    def test_b14_history_replay_never_invents_user_messages(self):
        self.assertEqual(["oc-amsg"], self.tech["history_without_user"],
                         "历史回放补造了不存在的用户话术：%s" % self.tech["history_without_user"])
        with_user = self.tech["history_with_user"]
        self.assertEqual(["oc-ubub", "oc-amsg"], with_user,
                         "历史里有用户气泡时顺序不对：%s" % with_user)


# --------------------------------------------------------------------------- #
# C. Tool List 与信息完整性
# --------------------------------------------------------------------------- #
class CToolListContract(ChatHarnessMixin, unittest.TestCase):
    def test_c15_execution_card_has_a_tool_list_container(self):
        self.assertIn("tools", set(self.tech["root_roles"] or []),
                      "技术侧执行卡缺 Tool List 容器（data-agent-role=tools）：%s"
                      % self.tech["root_roles"])
        self.assertIn("tools", set(self.board["process_roles"] or []),
                      "阶段页过程卡缺 Tool List 容器：%s" % self.board["process_roles"])
        self.assertTrue(self.tech["tool_item_found"],
                        "addToolCard() 没有产出 data-agent-role=tool-item 的工具项")

    def test_c16_tool_item_states_map_to_the_four_states(self):
        self.assertIn(self.tech["tool_item_state"], STATES,
                      "工具项的 data-state 不在四态词表里：%r" % self.tech["tool_item_state"])
        rows = self.board["tool_rows"] or []
        self.assertTrue(rows, "阶段页过程行没有 Tool Item：%s" % rows)
        for row in rows:
            with self.subTest(text=str(row.get("text"))[:40]):
                self.assertEqual("tool-item", row.get("role"))
                self.assertIn(row.get("state"), STATES)

    def test_c17_complex_detail_is_not_compressed(self):
        rows = self.board["tool_rows"] or []
        text = "".join(str(row.get("text") or "") for row in rows)
        for token in ("查询同类件", "P-001 主体外壳", "库内无同类件，按新制评估"):
            with self.subTest(token=token):
                self.assertIn(token, text, "过程被压缩/删减，丢了 %r：%s" % (token, text))
        self.assertNotIn("生成分析", text, "过程被概括成了「生成分析」这类空话")

    def test_c18_query_conditions_are_kept_in_full(self):
        row_text = str(self.tech["row_text"] or "")
        for token in ("length=108", "width=56"):
            with self.subTest(token=token):
                self.assertIn(token, row_text, "查询条件被删减：%s" % row_text)

    def test_c19_missing_items_fallbacks_and_times_are_kept(self):
        # ## 133：原始入参 / 工具名不再展示（属于技术实现），产品侧事实必须原样保留。
        self.assertTrue(
            self.tech["lossless_text_kept"],
            "「费率 0 条 / 回退 global 0 条 / 系数 0 条 / 待补 10 项」这类事实被压缩或丢弃",
        )
        self.assertNotIn("component_match", str(self.tech["row_text"] or ""),
                         "过程行里又出现了原始工具名（用户不需要感知技术实现）")

    def test_c20_parent_child_hierarchy_is_kept(self):
        # ## 133：缩进的子级信息不再折进折叠区，但必须仍然归属父行、原样保留。
        step_text = str(self.tech["nest_parent_text"] or "")
        for token in ("查询条件：length=108", "命中 CMP-SEMI-EE-BLOCK-0001"):
            with self.subTest(token=token):
                self.assertIn(token, step_text,
                              "缩进的子级信息丢失了 %r：%s" % (token, step_text[:200]))
        self.assertIn(".oc-process-step", self.chat_css, "过程行的既有选择器被删")

    def test_c21_tool_items_have_no_independent_card_style(self):
        # ## 133：折叠区整体退役；这里守住「过程行本体不是卡片」。
        self.assertNotIn('data-agent-role", "tool-detail', self.chat_js,
                         "技术左栏仍在建折叠区（tool-detail）")
        for token in ("oc-process-step", "oc-process-dot", "oc-process-text"):
            with self.subTest(token=token):
                self.assertIn(token, self.chat_css, "过程行的既有选择器被删：%s" % token)


# --------------------------------------------------------------------------- #
# D. 已退役：过程行的「详情 / 展开」合同
# --------------------------------------------------------------------------- #
# 用户口径变更（## 133）：过程行**没有**「详情」、没有折叠开关、没有按钮，
# 产品侧结果直接可见。原 D22–D31 的折叠合同整体退役，
# 改由 tests/test_quote_tech_process_row_product_contract_red.py 接管（A/B/C/D 组）。

# --------------------------------------------------------------------------- #
# F. 白底与字体守卫
# --------------------------------------------------------------------------- #
class FWhiteSurfaceAndFontGuard(ChatHarnessMixin, unittest.TestCase):
    def test_f38_user_message_bubble_keeps_the_system_primary_background(self):
        for label, css, selector in (("技术侧", self.chat_css, ".oc-ubub"),
                                     ("报价侧", self.quote_html, ".message-user")):
            with self.subTest(side=label):
                body = rule(css, selector)
                self.assertTrue(body, "%s 找不到 %s 规则" % (label, selector))
                values = style_backgrounds(body)
                self.assertTrue(values, "%s 的 %s 没有 background" % (label, selector))
                self.assertTrue(
                    any(re.search(r"var\(--(?:color-primary|oc-accent)\)", v) for v in values),
                    "%s 用户气泡必须保持系统主色实心背景：%s" % (label, values),
                )
                for value in values:
                    self.assertFalse(is_white(value),
                                     "%s 用户气泡被改成了白底（用户要求改回蓝色）：%r"
                                     % (label, value))

    def test_f39_agent_card_is_white(self):
        for label, css, selector in (("技术侧", self.chat_css, ".oc-amsg"),
                                     ("报价侧", self.quote_html, ".message-ai")):
            with self.subTest(side=label):
                body = rule(css, selector)
                self.assertTrue(body, "%s 找不到 %s 规则" % (label, selector))
                values = style_backgrounds(body)
                self.assertTrue(any(is_white(v) for v in values),
                                "%s Agent 卡不是白底：%s" % (label, values))

    def test_f40_user_message_text_stays_white_on_blue(self):
        for label, css, selector in (("技术侧", self.chat_css, ".oc-ubub"),
                                     ("报价侧", self.quote_html, ".message-user")):
            with self.subTest(side=label):
                body = rule(css, selector)
                self.assertRegex(body, r"color\s*:\s*(?:white|#fff(?:fff)?)\b",
                                 "%s 用户气泡正文不是白字（蓝底白字是既有口径）" % label)
                self.assertRegex(body, r"border-bottom-right-radius\s*:\s*(?!0(?:\D|$))",
                                 "%s 用户气泡丢了右下角小圆角" % label)

    def test_f41_no_gray_message_background(self):
        for label, css, selector in (("技术侧", self.chat_css, ".oc-ubub"),
                                     ("报价侧", self.quote_html, ".message-user"),
                                     ("报价侧AI", self.quote_html, ".message-ai")):
            with self.subTest(side=label):
                body = rule(css, selector)
                for value in style_backgrounds(body):
                    self.assertNotIn("var(--bg-secondary)", value,
                                     "%s 用了明显灰底：%r" % (label, value))
                    self.assertNotIn("var(--oc-bg-2)", value,
                                     "%s 用了明显灰底：%r" % (label, value))

    def test_f42_no_new_or_changed_font_family(self):
        self.assertEqual(
            FONT_FAMILY_BASELINE, font_family_fingerprint(),
            "本批禁止新增 / 修改 font-family",
        )

    def test_f43_font_inheritance_is_kept(self):
        self.assertEqual(FONT_FAMILY_BASELINE, font_family_fingerprint(),
                         "字体声明被改动，继承关系可能已变")
        for selector in (".oc-process-step", ".oc-process-detail", ".oc-thinking",
                         ".oc-ubub", ".oc-amsg"):
            body = rule(self.chat_css, selector)
            with self.subTest(selector=selector):
                self.assertNotIn("font-family", body,
                                 "%s 新增了 font-family" % selector)


# --------------------------------------------------------------------------- #
# G. 非回归
# --------------------------------------------------------------------------- #
class GNonRegression(ChatHarnessMixin, unittest.TestCase):
    def test_g44_agent_output_text_is_not_truncated(self):
        body = function_source(self.chat_js, "setToolResult")
        self.assertIn("4000", body, "工具结果既有的 4000 字上限被改动")
        self.assertTrue(self.tech["lossless_text_kept"],
                        "过程文字被压缩（费率/回退/系数/待补 四项没有完整保留）")

    def test_g45_backend_protocol_is_unchanged(self):
        for event in ("text", "tool_use", "tool_result", "error", "done", "thinking", "tech_ui"):
            with self.subTest(event=event):
                self.assertIn('"%s"' % event, self.chat_js,
                              "技术侧 handleEvent 的 %r 事件分支被删/改名" % event)

    def test_g46_sse_event_names_are_unchanged(self):
        for event in ("text", "tool_use", "tool_result", "error", "done", "cpq_ui"):
            with self.subTest(event=event):
                self.assertIn(event, self.quote_html,
                              "报价侧 SSE 事件名 %r 被删/改名" % event)
        self.assertNotIn("EventSource", self.quote_html,
                         "报价侧不该引入新的 SSE 通道")

    def test_g47_no_database_or_schema_change_is_introduced(self):
        for label, text in (("agent-chat.js", self.chat_js), ("确认需求解析结果.html", self.quote_html)):
            for token in ("CREATE TABLE", "ALTER TABLE", "INSERT INTO"):
                with self.subTest(file=label, token=token):
                    self.assertNotIn(token.upper(), text.upper(),
                                     "%s 里出现了数据库语句，本批不动数据库" % label)
        self.assertIn("/agent/event", self.chat_js, "会话落库接口被改动")
        self.assertIn("/agent/events", self.chat_js, "会话回放接口被改动")

    def test_g48_persistence_restores_the_full_turn(self):
        self.assertEqual(["oc-ubub", "oc-amsg"], self.tech["history_with_user"],
                         "会话持久化回放丢了完整 turn 的顺序：%s" % self.tech["history_with_user"])
        self.assertEqual(1, self.tech["echo_bubbles_added"],
                         "Agent 主动动作的回声气泡没有落进会话流")
        echoed = self.tech["echo_persisted"] or []
        self.assertTrue(echoed and echoed[0].get("kind") == "user",
                        "回声气泡没有以 kind=user 落库：%s" % echoed)

    def test_g49_failure_states_still_render(self):
        self.assertEqual("⚠ 失败", self.tech["failed_chip_text"],
                         "技术侧失败态 chip 文案被改：%r" % self.tech["failed_chip_text"])
        self.assertTrue(function_source(self.chat_js, "setAssistantState"),
                        "技术侧 setAssistantState 被删除")
        self.assertIn("failed", self.quote_html, "报价侧失败态渲染被删")
        self.assertTrue(function_source(self.chat_js, "boardFailureNotice"),
                        "技术侧看板失败提示被删除")

    def test_g50_existing_chat_contract_tests_are_still_present(self):
        for name in ("test_chat_fused_assistant_card_style_red.py",
                     "test_tech_quote_assistant_card_unification_red.py",
                     "test_chat_collapsible_thinking_trace_red.py",
                     "test_tech_tool_trace_business_line_detail_red.py",
                     "test_tech_agent_echo_bubble_and_single_exec_card_red.py",
                     "test_tech_task_card_body_layout_red.py",
                     "test_quote_tech_ai_message_white_surface_red.py"):
            with self.subTest(file=name):
                self.assertTrue((ROOT / "tests" / name).exists(),
                                "既有聊天合同红测 %s 被删除" % name)


if __name__ == "__main__":
    unittest.main()
