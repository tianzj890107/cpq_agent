"""红测：模型调用一行一次（返回补写回原行）+ 明细改成短摘要。

用户口径（两条）：

1. 「调用模型（qwen3.5-plus）详情 输入{} 输出{} · 模型返回（qwen3.5-plus）详情 输入{} 输出{}
   这个东西没必要 只需要调用模型（qwen3.5-plus）不需要模型返回（qwen3.5-plus）这个题目」
2. 「输入输出都是空的，只要一个问的是什么 返回的是什么只要有就行了」；
   随后补充：「我不想有很长文本截断成只有开头那点，我想总结性的比较简短的就像现在报价和
   提问 agent 的时候怎么做的 然后返回时把结果补写进原来那条」。

现状（实测，非推断）：

· `qwen_client.py:710`/`:720` 与 `claude_client.py:185`/`:227` 各播一条 model 事件，两条的
  明细都只有 `_model_detail()` 返回的 `{model, provider, vision}`（`qwen_client.py:725`），
  没有 `input` / `output`；前端 `agent-chat.js:1421-1433` 固定按 `detail.input || {}` /
  `detail.output || {}` 渲染 → 两行都显示 `{}`。
· 参考实现：报价侧 `确认需求解析结果.html:1131` 的 `showStage()` 是**一条原地更新**的轨迹行；
  提问 Agent 侧 `agent-chat.js:639` 的 `addToolCard()` 主行是中文业务文案 + 一句话入参摘要
  （`toolSubtitle()` `:379`），结果由 `setToolResult()` `:668` **写回同一张卡**。
· 当时刻意不留正文：`docs/specs/effective-model-for-vision-and-task-process-detail.md:189-193`（B8）；
  守卫在 `tests/test_task_process_detail_red.py:523`（test_10）与
  `tests/test_tech_task_process_stream_red.py:516`（test_23），**本批不反转它们**。

Spec：docs/specs/tech-model-call-row-merged-and-summary-detail.md
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
CHAT_JS = FRONTEND / "agent-chat.js"
QWEN_PY = ROOT / "tech_app/backend/services/qwen_client.py"
CLAUDE_PY = ROOT / "tech_app/backend/services/claude_client.py"
TASKS_PY = ROOT / "tech_app/backend/services/tasks.py"
SPEC = ROOT / "docs" / "specs" / "tech-model-call-row-merged-and-summary-detail.md"

NODE = shutil.which("node")
DEFINITION_MARKERS = ("\ndef ", "\nasync def ", "\n@app.", "\nclass ")

# 一次调用明细里短摘要允许出现的键（契约 A）。
INPUT_KEYS = ("任务", "模型", "服务商", "带图", "文本段", "提示字数", "消息字数", "附件")
OUTPUT_OK_KEYS = ("状态", "结果", "规模")
OUTPUT_FAIL_KEYS = ("状态", "原因")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
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


def py_body(text: str, name: str) -> str:
    marker = f"def {name}("
    idx = text.find(marker)
    if idx < 0:
        return ""
    brace = text.find("\n", idx)
    rest = text[brace:] if brace >= 0 else text[idx:]
    end = len(rest)
    for marker_ in DEFINITION_MARKERS:
        at = rest.find(marker_, 1)
        if at != -1:
            end = min(end, at)
    return rest[:end]


def function_source(text: str, name: str) -> str:
    marker = f"function {name}("
    block = block_from(text, marker)
    if not block:
        return ""
    start = text.find(marker)
    at = text.find(block, start)
    return text[start:at + len(block)]


def arrow_source(text: str, marker: str) -> str:
    block = block_from(text, marker)
    if not block:
        return ""
    start = text.find(marker)
    at = text.find(block, start)
    return text[start:at + len(block)] + ";"


def js_const_source(text: str, name: str) -> str:
    match = re.search(r"^\s*const %s = .*?;\s*$" % re.escape(name), text, re.M)
    return match.group(0).strip() if match else ""


def require_dict(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise AssertionError("%s 没有生成明细：%r" % (label, value))
    return value


def child_python() -> str:
    candidates = [str(ROOT / "open-claude" / ".venv" / "bin" / "python"),
                  sys.executable, shutil.which("python3"), shutil.which("python")]
    probe_code = ("import sys; sys.path.insert(0, %r); "
                  "import tech_app.backend.services.tasks" % str(ROOT))
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", probe_code], cwd=str(ROOT),
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return sys.executable


def run_child(script: str, *args: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="cpq-model-row-") as tmp:
        path = pathlib.Path(tmp) / "probe.py"
        path.write_text(script, encoding="utf-8")
        completed = subprocess.run([child_python(), str(path), *args],
                                   capture_output=True, text=True, timeout=300)
        if completed.returncode != 0:
            raise AssertionError("子进程探针失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2500:],
                                    completed.stderr[-2500:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


def run_node(driver: str) -> dict:
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端走查")
    with tempfile.TemporaryDirectory(prefix="cpq-model-row-js-") as tmp:
        script = pathlib.Path(tmp) / "driver.js"
        script.write_text(driver, encoding="utf-8")
        completed = subprocess.run([NODE, str(script)], capture_output=True,
                                   text=True, timeout=60)
        if completed.returncode != 0:
            raise AssertionError("JS 走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2500:],
                                    completed.stderr[-2500:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# 子进程：用打桩客户端真跑 qwen_client.run()，看两条事件的明细
# --------------------------------------------------------------------------- #
CHILD = r'''
import json
import os
import sys
import time
import types

data_dir, root = sys.argv[1], sys.argv[2]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

import cpq_shared_settings

cpq_shared_settings.load = lambda legacy=(): {
    "model": "qwen3.5-plus", "temperature": 0.2, "max_tokens": 4096, "thinking": False,
    "api_keys": {"qwen": "GLOBAL-QWEN-KEY"}}

from pydantic import BaseModel

from tech_app.backend.services import qwen_client, tasks
from tech_app.backend.storage import store

SYSTEM_PROMPT = "系统提示-绝不允许出现在过程流里"
USER_BODY = "用户输入原文-也绝不允许出现在过程流里"
API_KEY = "sk-probe-must-not-leak"
CANARY = "LEAK-CANARY-候选原件不得整包进明细"
IMAGE = "data:image/png;base64,aGVsbG8="

BODY = "x" * 58


class ProbeOut(BaseModel):
    parts: int = 4
    questions: int = 3
    summary: str = BODY
    note: str = "n" * 12


CALLED = {}


class _Completions:
    def create(self, **kwargs):
        CALLED["model"] = kwargs.get("model")
        msg = types.SimpleNamespace(content=json.dumps(
            {"parts": 4, "questions": 3, "summary": BODY, "note": "n" * 12}))
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg, finish_reason="stop")])


class _Chat:
    completions = _Completions()


class _FakeClient:
    chat = _Chat()


qwen_client.get_client = lambda vision=False: _FakeClient()

PID = store.create_project(source_filename="model-row.png", source_bytes=b"png",
                           note="model row probe", owner="tester")


def job():
    tasks.report_progress("准备调用模型")
    qwen_client.run(
        SYSTEM_PROMPT,
        [{"type": "text", "text": "【设备需求原图】"},
         {"type": "image_url", "image_url": {"url": IMAGE}},
         {"type": "text", "text": USER_BODY},
         {"type": "text", "text": "【佐证图片: source.png】"},
         {"type": "text", "text": "【本地输入清单】\n{\"filename\": \"source.png\"}"}],
        ProbeOut)
    return {}


TERMINAL = {"succeeded", "failed", "partial", "interrupted", "cancelled", "stale"}
task_id = tasks.submit(PID, "parse", job)
deadline = time.time() + 120
task = {}
while time.time() < deadline:
    found = [t for t in store.list_tasks(PID) if t.get("task_id") == task_id]
    task = found[0] if found else {}
    if task.get("status") in TERMINAL:
        break
    time.sleep(0.05)

rows = [row for row in (task.get("process_log") or []) if row.get("phase") == "model"]
blob = json.dumps(task.get("process_log") or [], ensure_ascii=False, default=str)
print(json.dumps({
    "status": task.get("status"),
    "error": str(task.get("error") or ""),
    "texts": [str(row.get("text") or "") for row in rows],
    "details": [row.get("detail") for row in rows],
    "called_model": CALLED.get("model"),
    "sizes": [len(json.dumps(row.get("detail"), ensure_ascii=False, default=str)) for row in rows],
    "leak": [needle for needle in (SYSTEM_PROMPT, USER_BODY, API_KEY, CANARY, IMAGE, "base64")
             if needle in blob],
}, ensure_ascii=False, default=str))
'''


# --------------------------------------------------------------------------- #
# 前端：最小 DOM 桩，真跑 pushTaskStep
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
  prepend(node) { node.parentNode = this; this.children.unshift(node); }
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

MERGE_TAIL = r'''
function classesOf(node) { return String((node && node.className) || "").split(/\s+/).filter(Boolean); }
function find(node, cls) {
  if (!node || !node.children) return null;
  for (var i = 0; i < node.children.length; i += 1) {
    var child = node.children[i];
    if (classesOf(child).indexOf(cls) >= 0) return child;
    var deeper = find(child, cls);
    if (deeper) return deeper;
  }
  return null;
}
function rowInfo(row) {
  var box = find(row, "oc-process-detail");
  return {
    text: String(row.textContent).replace(/\s+/g, " ").trim(),
    klass: String(row.className),
    has_details: !!box,
    input: box ? (function () { var p = find(box, "oc-process-detail-input"); return p ? p.textContent : ""; })() : "",
    output: box ? (function () { var p = find(box, "oc-process-detail-output"); return p ? p.textContent : ""; })() : ""
  };
}
var out = {};

/* ① 同一次调用：开始 + 成功返回 */
var card = ensureTaskCard("T-merge-1", "图纸解析");
pushTaskStep(card, "调用模型（probe-model）", "", "model",
  {model: "probe-model", provider: "qwen", vision: true, call: "c1", status: "running",
   input: {任务: "图纸解析 SOP", 模型: "probe-model", 服务商: "qwen", 带图: 1, 文本段: 2,
           提示字数: 3300, 消息字数: 120, 附件: ["source.png"]}});
out.rows_after_start = card.steps.children.length;
pushTaskStep(card, "模型返回（probe-model）", "", "model",
  {model: "probe-model", provider: "qwen", vision: true, call: "c1", status: "ok",
   output: {状态: "ok", 结果: {parts: 4, questions: 3, summary: "58 字"}, 规模: 128}});
out.rows_after_return = card.steps.children.length;
out.merged = rowInfo(card.steps.children[0]);

/* ② 两次不同的调用 → 两行 */
var card2 = ensureTaskCard("T-merge-2", "参数推荐");
pushTaskStep(card2, "调用模型（probe-model）", "", "model",
  {model: "probe-model", provider: "qwen", vision: false, call: "d1", status: "running",
   input: {模型: "probe-model"}});
pushTaskStep(card2, "模型返回（probe-model）", "", "model",
  {model: "probe-model", provider: "qwen", vision: false, call: "d1", status: "ok",
   output: {状态: "ok"}});
pushTaskStep(card2, "调用模型（probe-model）", "", "model",
  {model: "probe-model", provider: "qwen", vision: false, call: "d2", status: "running",
   input: {模型: "probe-model"}});
pushTaskStep(card2, "模型返回（probe-model）", "", "model",
  {model: "probe-model", provider: "qwen", vision: false, call: "d2", status: "ok",
   output: {状态: "ok"}});
out.two_calls_rows = card2.steps.children.length;

/* ③ 旧数据：没有 call → 逐字保持今天的行为（两行） */
var card3 = ensureTaskCard("T-merge-3", "旧任务");
pushTaskStep(card3, "调用模型（probe-model）", "", "model",
  {model: "probe-model", provider: "qwen", vision: false});
pushTaskStep(card3, "模型返回（probe-model）", "", "model",
  {model: "probe-model", provider: "qwen", vision: false});
out.legacy_rows = card3.steps.children.length;
out.legacy_first = rowInfo(card3.steps.children[0]);

/* ④ 失败：仍然一行，且原因不展开就能看见 */
var card4 = ensureTaskCard("T-merge-4", "图纸解析");
pushTaskStep(card4, "调用模型（probe-model）", "", "model",
  {model: "probe-model", provider: "qwen", vision: true, call: "e1", status: "running",
   input: {模型: "probe-model"}});
pushTaskStep(card4, "模型调用失败（连接超时）", "", "model",
  {model: "probe-model", provider: "qwen", vision: true, call: "e1", status: "failed",
   output: {状态: "failed", 原因: "连接超时"}});
out.failed_rows = card4.steps.children.length;
out.failed = rowInfo(card4.steps.children[0]);

console.log(JSON.stringify(out));
'''


def merge_driver(js: str) -> str:
    optional_helpers = ("modelCallKey", "modelRowKey", "mergeModelRow", "applyModelOutput",
                        "modelRowMap", "findModelRow")
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
            function echoTaskPrompt() {}
            function refreshResultChips() {}
            function loadFiles() {}
            function renderTaskRetry() {}
            function boardStage() { return "parse"; }
            """),
        js_const_source(js, "SENSITIVE_TASK_KEYS"),
        arrow_source(js, "const el = (tag, cls, text)"),
        function_source(js, "taskStatusWord"),
        function_source(js, "ensureTaskCard"),
    ]
    parts.extend(function_source(js, name) for name in optional_helpers)
    parts.extend([function_source(js, "pushTaskStep"), MERGE_TAIL])
    return "\n".join(part for part in parts if part)


# --------------------------------------------------------------------------- #
# 契约
# --------------------------------------------------------------------------- #
class SpecPinnedTest(unittest.TestCase):
    def test_spec_pins_both_contracts(self):
        text = read(SPEC)
        for token in ("`call`", "`input`", "`output`", "current_task_name",
                      "`pushTaskStep`", "`模型返回", "PROCESS_DETAIL_LIMIT",
                      "_model_input_summary", "_model_output_summary"):
            self.assertIn(token, text, "Spec 缺少契约锚点：%s" % token)
        self.assertIn("不反转", text, "Spec 必须写明既有守卫测试不反转")


class ModelDetailSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qwen = read(QWEN_PY)
        cls.claude = read(CLAUDE_PY)
        cls.tasks = read(TASKS_PY)

    def test_current_task_name_exists_and_reads_sop_names(self):
        body = py_body(self.tasks, "current_task_name")
        self.assertTrue(body, "tasks.py 缺少 current_task_name()")
        self.assertIn("_CURRENT_TASK", body, "current_task_name() 必须看当前任务上下文")
        self.assertIn("_SOP_NAMES", body, "current_task_name() 必须取 _SOP_NAMES 里的中文名")
        self.assertRegex(body, r"return\s+['\"]{2}|\breturn\b", "current_task_name() 要有返回值")

    def test_both_clients_pass_a_pairing_call_id(self):
        for name, src in (("qwen_client", self.qwen), ("claude_client", self.claude)):
            calls = re.findall(r"_model_detail\((?:[^()]|\([^()]*\))*\)", src)
            with self.subTest(service=name):
                self.assertGreaterEqual(len(calls), 2,
                                        "%s 的 _model_detail 调用点少于两处：%s" % (name, calls))
                for call in calls:
                    self.assertIn("call", call,
                                  "%s 的 _model_detail 调用没带配对 id：%s" % (name, call))

    def test_detail_carries_call_and_status_and_summaries(self):
        for key in ('"call"', "'call'", '"status"', "'status'"):
            pass
        for name, src in (("qwen_client", self.qwen), ("claude_client", self.claude)):
            with self.subTest(service=name):
                self.assertTrue(re.search(r"[\"']call[\"']\s*:", src),
                                "%s 的模型明细没有 call 键" % name)
                self.assertTrue(re.search(r"[\"']status[\"']\s*:", src),
                                "%s 的模型明细没有 status 键" % name)
                for field in ("带图", "文本段", "提示字数", "消息字数", "附件"):
                    self.assertIn(field, src, "%s 的输入摘要缺「%s」" % (name, field))
                for field in ("结果", "规模"):
                    self.assertIn(field, src, "%s 的输出摘要缺「%s」" % (name, field))
                self.assertIn("current_task_name", src,
                              "%s 的输入摘要没有任务名（哪一步的调用）" % name)

    def test_summary_never_embeds_raw_text(self):
        """"短摘要"必须只算长度/规模：不得把 prompt / 用户消息 / data URL 直接塞进明细。"""
        for name, src in (("qwen_client", self.qwen), ("claude_client", self.claude)):
            with self.subTest(service=name):
                detail = py_body(src, "_model_detail")
                self.assertTrue(detail, "%s 缺 _model_detail()" % name)
                self.assertNotIn("data:", detail, "%s 的明细里出现了 data URL 字面量" % name)
                self.assertNotIn("base64", detail, "%s 的明细里出现了 base64" % name)
                for helper in ("_model_input_summary", "_model_output_summary"):
                    body = py_body(src, helper)
                    self.assertTrue(body, "%s 缺摘要辅助函数 %s()" % (name, helper))
                    self.assertNotIn("data:", body, "%s 的 %s 里出现了 data URL" % (name, helper))
                    self.assertNotIn("base64", body, "%s 的 %s 里出现了 base64" % (name, helper))
                    self.assertIn("len(", body,
                                  "%s 的 %s 没有用 len() 算长度/规模" % (name, helper))

    def test_event_texts_unchanged(self):
        """合并只发生在渲染层：后端两条事件的文本一个字都不改。"""
        for name, src in (("qwen_client", self.qwen), ("claude_client", self.claude)):
            with self.subTest(service=name):
                self.assertIn("调用模型（", src)
                self.assertIn("模型返回（", src)
                self.assertIn("模型调用失败（", src)


class ModelDetailRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="cpq-model-row-work-")
        try:
            cls.out = run_child(CHILD, str(cls.work), str(ROOT))
        finally:
            shutil.rmtree(cls.work, ignore_errors=True)

    def test_model_call_emits_exactly_one_pair(self):
        self.assertEqual("succeeded", self.out["status"],
                         "模型调用任务失败：%s" % self.out)
        self.assertEqual(2, len(self.out["details"]),
                         "一次逻辑调用必须恰好两条模型事件：%s" % self.out["texts"])
        self.assertIn("调用模型（", self.out["texts"][0])
        self.assertIn("模型返回（", self.out["texts"][1])

    def test_pair_shares_one_call_id(self):
        first = require_dict(self.out["details"][0], "开始事件明细")
        second = require_dict(self.out["details"][1], "返回事件明细")
        self.assertTrue(first.get("call"), "开始事件的明细没有配对 id：%s" % first)
        self.assertEqual(first.get("call"), second.get("call"),
                         "同一次调用的两条事件配对 id 不一致：%s / %s" % (first, second))
        self.assertEqual("running", first.get("status"))
        self.assertEqual("ok", second.get("status"))

    def test_input_summary_is_short_and_about_the_ask(self):
        detail = require_dict(self.out["details"][0], "开始事件明细")
        payload = require_dict(detail.get("input"), "开始事件的 input 摘要")
        for key in INPUT_KEYS:
            if key == "附件":
                continue
            self.assertIn(key, payload, "输入摘要缺 %s：%s" % (key, payload))
        self.assertEqual("图纸解析 SOP", payload["任务"], "输入摘要没有说清是哪一步的调用")
        self.assertEqual(self.out["called_model"], payload["模型"])
        self.assertEqual(1, payload["带图"], "带图数量不对：%s" % payload)
        self.assertGreaterEqual(payload["文本段"], 2)
        self.assertGreater(payload["提示字数"], 0)
        self.assertGreater(payload["消息字数"], 0)
        self.assertEqual(["source.png"], payload.get("附件"),
                         "附件名没有抓出来：%s" % payload)
        # 短摘要：整块不许出现被问的原文
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("绝不允许出现在过程流里", blob)
        self.assertNotIn("aGVsbG8", blob, "base64 图片进了输入摘要")

    def test_output_summary_is_short_and_about_the_result(self):
        detail = require_dict(self.out["details"][1], "返回事件明细")
        payload = require_dict(detail.get("output"), "返回事件的 output 摘要")
        for key in OUTPUT_OK_KEYS:
            self.assertIn(key, payload, "输出摘要缺 %s：%s" % (key, payload))
        self.assertEqual("ok", payload["状态"])
        result = require_dict(payload.get("结果"), "输出摘要里的「结果」")
        self.assertEqual("4", str(result.get("parts")), "标量字段应给原值：%s" % result)
        self.assertEqual("58 字", result.get("summary"), "字符串字段只给字数：%s" % result)
        for key, value in result.items():
            self.assertLessEqual(len(str(value)), 16, "「结果」里的 %s 太长：%s" % (key, value))
        self.assertIsInstance(payload.get("规模"), int)
        self.assertGreater(payload["规模"], 0)

    def test_summary_does_not_leak_and_stays_small(self):
        self.assertEqual([], self.out["leak"],
                         "模型明细里出现了不该出现的内容：%s" % self.out["leak"])
        for size in self.out["sizes"]:
            self.assertLessEqual(size, 4096, "单条明细超过 4096 字节：%s" % size)


@unittest.skipUnless(NODE, "需要 node 才能真跑模型行合并走查")
class ModelRowMergeFrontendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read(CHAT_JS)
        cls.out = run_node(merge_driver(cls.js))

    def test_one_visible_row_per_model_call(self):
        self.assertEqual(1, self.out["rows_after_start"],
                         "开始事件没有建行：%s" % self.out)
        self.assertEqual(1, self.out["rows_after_return"],
                         "返回事件另起了一行（应补写回原来那条）：%s" % self.out)

    def test_row_text_keeps_the_call_and_drops_the_return_title(self):
        text = self.out["merged"]["text"]
        self.assertIn("调用模型", text, "行文字被换掉了：%s" % text)
        self.assertNotIn("模型返回", text, "「模型返回」这个题目还在：%s" % text)

    def test_details_show_input_and_output(self):
        merged = self.out["merged"]
        self.assertTrue(merged["has_details"], "模型行没有「详情」：%s" % merged)
        self.assertIn("图纸解析 SOP", merged["input"], "详情里看不到问的是什么：%s" % merged)
        self.assertIn("parts", merged["output"], "详情里看不到返回的是什么：%s" % merged)
        self.assertNotEqual("{}", merged["input"].strip(), "输入还是空的：%s" % merged)
        self.assertNotEqual("{}", merged["output"].strip(), "输出还是空的：%s" % merged)

    def test_two_different_calls_stay_two_rows(self):
        self.assertEqual(2, self.out["two_calls_rows"],
                         "两次不同的调用应各自一行（合并不得跨 call）：%s" % self.out)

    def test_legacy_rows_without_call_keep_todays_shape(self):
        self.assertEqual(2, self.out["legacy_rows"],
                         "旧数据（没有 call）必须逐字保持今天的行为：%s" % self.out)
        # 旧数据没有 call，逐字沿用今天的行为：仍会长出详情块，但输入/输出都是空 {}，
        # 既不能被合并逻辑改写，也不能凭空补出内容。
        self.assertTrue(self.out["legacy_first"]["has_details"],
                        "旧数据的结构被本批改动了：%s" % self.out["legacy_first"])
        self.assertEqual("{}", self.out["legacy_first"]["input"].strip(),
                         "旧数据的输入被改写了：%s" % self.out["legacy_first"])
        self.assertEqual("{}", self.out["legacy_first"]["output"].strip(),
                         "旧数据的输出被改写了：%s" % self.out["legacy_first"])

    def test_failure_stays_one_row_and_stays_visible(self):
        self.assertEqual(1, self.out["failed_rows"],
                         "失败事件另起了一行：%s" % self.out)
        text = self.out["failed"]["text"]
        self.assertIn("调用模型", text)
        self.assertIn("模型调用失败", text, "失败原因不展开就要能看见：%s" % text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
