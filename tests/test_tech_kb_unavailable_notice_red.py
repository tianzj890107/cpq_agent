"""零件库连不上必须"当场看见"：看板顶部红色警示 + 结论区「未检索（库连不上）」：Spec / Red。

契约见 docs/specs/tech-kb-unavailable-loud-notice.md。今天的事实是反的：

  · 自动路（解析 / 拆解 / 导入 3D 后顺手检索）吞掉异常，只写一条进度提示 + 一条审计，
    不落任何状态（tech_app/backend/main.py:1334-1335）；
  · GET 出口查不到报告就回 {"items": [], "summary": {}}（main.py:1393）；
  · 前端拿到空报告渲染成「还没有零部件库检索结果（解析完成后自动生成）。」
    （tech_app/frontend/app.js:1028），只要还留着旧报告，顶行还会写「库内 0 条」
    （app.js:1036 的 report.library_size || 0）。

于是刷新页面后故障现场就没了：「库连不上」与「库里没有可复用零件」在界面上长得一样。

验证方式：
  · 后端：带 fastapi 的解释器在子进程里真跑 TestClient + 临时 DATA_DIR；"知识库不可达"用
    「内部令牌有、基址指到没人监听的端口」造出来，绝不真连 PG；"重试成功"用夹具快照打桩；
  · 前端：Node 直接加载从 app.js 抽出的 renderComponentMatchResult()，用最小 DOM 桩驱动；
  · 静态契约：store / main / app.js / workbench.css / index.html 的源码断言。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
SPEC = ROOT / "docs" / "specs" / "tech-kb-unavailable-loud-notice.md"
STORE_PY = ROOT / "tech_app" / "backend" / "storage" / "store.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
APP_JS_PATH = FRONTEND / "app.js"
WORKBENCH_CSS_PATH = FRONTEND / "workbench.css"
INDEX_HTML_PATH = FRONTEND / "index.html"
SNAPSHOT_FIXTURE = ROOT / "tests" / "fixtures" / "cpq_kb_snapshot_20260916.json"

NODE = shutil.which("node")

# 本批开工前 index.html 的资源版本号：改了 app.js / workbench.css 就必须换掉它们。
BEFORE_APP_VERSION = "20260916-echo1"
BEFORE_CSS_VERSION = "20260914-split2"

BANNER_ID = "componentMatchBanner"
BANNER_CLASS = "component-match-unavailable"
BANNER_TEXT = "零件库连不上，本次未检索"
CONCLUSION_TEXT = "未检索（库连不上）"
ZERO_COUNT_TEXTS = ("库内 0 条", "可复用 0", "可改制 0", "未匹配 0")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
    """返回 marker 之后第一个配对大括号块（含大括号）；配对不上返回空串。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    match = re.compile(r"\{\s*\n").search(text, idx + len(marker))
    brace = match.start() if match else text.find("{", idx + len(marker))
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
                return text[idx:index + 1]
        index += 1
    return ""


def def_block(text: str, marker: str) -> str:
    """Python：从 marker 截到下一个顶层 def/class（花括号无关）。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    match = re.search(r"\n(?=(?:def|class|@)\s)", text[idx + len(marker):])
    end = idx + len(marker) + match.start() if match else len(text)
    return text[idx:end]


def run_node(driver: str) -> dict:
    """跑一段自包含的 JS 驱动（内含从真实文件抽出的函数块 + 最小 DOM 桩）。"""
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端控制流走查")
    with tempfile.TemporaryDirectory(prefix="cpq-kb-notice-js-") as tmp:
        script = pathlib.Path(tmp) / "driver.js"
        script.write_text(driver, encoding="utf-8")
        completed = subprocess.run([NODE, str(script)], capture_output=True,
                                   text=True, timeout=60)
        if completed.returncode != 0:
            raise AssertionError("JS 走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2000:],
                                    completed.stderr[-2000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


def child_python() -> str:
    """能同时 import fastapi 与本仓库后端的解释器（本机为 open-claude/.venv）。"""
    candidates = [str(ROOT / "open-claude" / ".venv" / "bin" / "python"),
                  sys.executable, shutil.which("python3"), shutil.which("python")]
    probe_code = ("import sys; sys.path.insert(0, %r); "
                  "import fastapi, tech_app.backend.main" % str(ROOT))
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", probe_code], cwd=str(ROOT),
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return sys.executable


def run_child(script: str, *args: str) -> dict:
    """用能 import fastapi 的解释器跑子进程脚本，回最后一行 JSON。"""
    with tempfile.TemporaryDirectory(prefix="cpq-kb-notice-") as tmp:
        path = pathlib.Path(tmp) / "probe.py"
        path.write_text(script, encoding="utf-8")
        completed = subprocess.run([child_python(), str(path), *args],
                                   capture_output=True, text=True, timeout=240)
        if completed.returncode != 0:
            raise AssertionError("子进程探针失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2500:],
                                    completed.stderr[-2500:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# 前端：最小 DOM 桩
# --------------------------------------------------------------------------- #
FRONTEND_DOM_STUB = r'''
class El {
  constructor(tag, doc) {
    this.tagName = String(tag || "div").toUpperCase();
    this._doc = doc; this._id = ""; this.className = ""; this._text = "";
    this.children = []; this.parentNode = null; this.hidden = false;
    this.style = {}; this.dataset = {}; this.attrs = {};
  }
  get id() { return this._id; }
  set id(value) { this._id = String(value); if (this._doc) this._doc.register(this._id, this); }
  get childNodes() { return this.children.slice(); }
  get firstChild() { return this.children.length ? this.children[0] : null; }
  get lastChild() { return this.children.length ? this.children[this.children.length - 1] : null; }
  get firstElementChild() { return this.children.length ? this.children[0] : null; }
  get parentElement() { return this.parentNode; }
  classes() { return String(this.className || "").split(/\s+/).filter(Boolean); }
  get classList() {
    var self = this;
    return {
      add: function () { [].slice.call(arguments).forEach(function (c) {
        if (self.classes().indexOf(c) < 0) { self.className = self.classes().concat([c]).join(" "); } }); },
      remove: function () { [].slice.call(arguments).forEach(function (c) {
        self.className = self.classes().filter(function (x) { return x !== c; }).join(" "); }); },
      contains: function (c) { return self.classes().indexOf(c) >= 0; },
      toggle: function (c) { if (self.classes().indexOf(c) >= 0) { this.remove(c); } else { this.add(c); } }
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
  _attach(node, index) {
    node.parentNode = this;
    if (index === undefined || index === null || index < 0 || index >= this.children.length) {
      this.children.push(node);
    } else {
      this.children.splice(index, 0, node);
    }
    return node;
  }
  append() { var self = this; [].slice.call(arguments).forEach(function (n) { self._attach(n); }); }
  appendChild(node) { return this._attach(node); }
  prepend(node) { return this._attach(node, 0); }
  insertBefore(node, ref) {
    var index = ref ? this.children.indexOf(ref) : -1;
    return this._attach(node, index < 0 ? 0 : index);
  }
  before(node) {
    if (!this.parentNode) { return; }
    var index = this.parentNode.children.indexOf(this);
    this.parentNode._attach(node, index < 0 ? 0 : index);
  }
  after(node) {
    if (!this.parentNode) { return; }
    var index = this.parentNode.children.indexOf(this);
    this.parentNode._attach(node, index < 0 ? 0 : index + 1);
  }
  replaceChildren() {
    this.children.forEach(function (c) { c.parentNode = null; });
    this.children = []; this._text = "";
  }
  remove() {
    if (this.parentNode) {
      var index = this.parentNode.children.indexOf(this);
      if (index >= 0) { this.parentNode.children.splice(index, 1); }
    }
    this.parentNode = null;
    if (this._doc && this._id) { this._doc.unregister(this._id, this); }
  }
  setAttribute(name, value) {
    this.attrs[String(name)] = String(value);
    if (name === "id") { this.id = value; }
    if (name === "class") { this.className = String(value); }
  }
  getAttribute(name) {
    if (name === "id") { return this._id || null; }
    if (name === "class") { return this.className || null; }
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }
  matches(selector) {
    var self = this;
    return String(selector || "").trim().split(/(?=[.#])/).filter(Boolean).every(function (part) {
      if (part[0] === ".") { return self.classes().indexOf(part.slice(1)) >= 0; }
      if (part[0] === "#") { return self._id === part.slice(1); }
      return self.tagName === part.toUpperCase();
    });
  }
  closest(selector) {
    var node = this;
    while (node) {
      if (node.matches && node.matches(selector)) { return node; }
      node = node.parentNode;
    }
    return null;
  }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener() {}
  removeEventListener() {}
}

function flatten(node, out) {
  out = out || [];
  out.push(node);
  (node.children || []).forEach(function (child) { flatten(child, out); });
  return out;
}

var doc = {
  _byId: {}, _board: null,
  register: function (id, el) { this._byId[String(id)] = el; },
  unregister: function (id, el) { if (this._byId[String(id)] === el) { delete this._byId[String(id)]; } },
  getElementById: function (id) { return this._byId[String(id)] || null; },
  createElement: function (tag) { return new El(tag, this); },
  querySelector: function (selector) {
    return String(selector || "").indexOf("center-panel") >= 0 ? this._board : null;
  },
  querySelectorAll: function () { return []; },
  addEventListener: function () {}, dispatchEvent: function () {}
};
var board = doc.createElement("div");
board.className = "center-panel";
doc._board = board;
var split = doc.createElement("div");
split.className = "drawing-board-split";
board.append(split);
var column = doc.createElement("section");
column.className = "drawing-parts-column";
split.append(column);
var secParts = doc.createElement("div");
secParts.className = "panel-section";
secParts.id = "secParts";
column.append(secParts);
globalThis.document = doc;
globalThis.window = { addEventListener: function () {}, dispatchEvent: function () {} };
function status() {}
function toast() {}
function publishResultSummary() {}
function agentSay() {}
var currentProject = "proj-notice-1";
'''

FRONTEND_TAIL = (r'''
  var order = flatten(board);
  var banner = doc.getElementById("''' + BANNER_ID + r'''");
  var result = doc.getElementById("componentMatchResult");
  console.log(JSON.stringify({
    banner: banner ? {id: banner.id, className: banner.className,
                      text: banner.textContent, hidden: !!banner.hidden} : null,
    bannerAttached: banner ? order.indexOf(banner) >= 0 : false,
    bannerBeforeParts: banner ? (order.indexOf(banner) >= 0 && order.indexOf(secParts) >= 0 &&
                                 order.indexOf(banner) < order.indexOf(secParts)) : false,
    resultText: result ? result.textContent : "",
    pageText: board.textContent,
    probe: (typeof globalThis.__probe === "undefined" ? null : globalThis.__probe)
  }));
''')


def render_body() -> str:
    body = block_from(read(APP_JS_PATH), "function renderComponentMatchResult(")
    if body:
        return body
    return block_from(read(APP_JS_PATH), "renderComponentMatchResult =")


def frontend_driver(payload, extra: str = "") -> str:
    return (FRONTEND_DOM_STUB + render_body() + r"""
(async function () {
  try {
    await renderComponentMatchResult(""" + json.dumps(payload, ensure_ascii=False) + r""");
""" + extra + FRONTEND_TAIL + r"""
  } catch (error) {
    console.log(JSON.stringify({error: String((error && error.stack) || error)}));
  }
})();
""")


UNAVAILABLE = {
    "reason": "kb_unavailable",
    "message": "零件库连不上，本次未检索",
    "error": "连接 Postgres 失败：connection refused",
    "at": "2026-09-16 16:20:00",
}
PREVIOUS_REPORT = {
    "items": [{"part_id": "P-001", "part_name": "顶盖板", "decision": "reuse",
               "component_code": "CMP-SEMI-EE-COVER-0001", "score": 0.94,
               "decision_label": "可复用"}],
    "summary": {"reuse": 1, "modify": 0, "new": 0},
    "library_size": 20,
    "generated_at": "2026-09-15 10:00:00",
}
OK_REPORT = {
    "items": [{"part_id": "P-001", "part_name": "顶盖板", "decision": "reuse",
               "component_code": "CMP-SEMI-EE-COVER-0001", "score": 0.94,
               "decision_label": "可复用"}],
    "summary": {"reuse": 1, "modify": 0, "new": 0},
    "library_size": 20,
    "generated_at": "2026-09-16 16:30:00",
}


# --------------------------------------------------------------------------- #
# 后端：子进程探针（TestClient + 临时 DATA_DIR，绝不真连 PG）
# --------------------------------------------------------------------------- #
CHILD_BACKEND = r'''
import json
import os
import socket
import sys
import types

data_dir, root, fixture = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

# 知识库不可达：内部令牌给足，基址指到一个没人监听的端口（连接被拒）。
_probe = socket.socket()
_probe.bind(("127.0.0.1", 0))
_dead_port = _probe.getsockname()[1]
_probe.close()
os.environ["CPQ_INTERNAL_TOKEN"] = "kb-internal-token-1"
os.environ["CPQ_AUTH_BASE_URL"] = "http://127.0.0.1:%d" % _dead_port
os.environ["CPQ_KB_BASE_URL"] = "http://127.0.0.1:%d" % _dead_port

from starlette.testclient import TestClient

import tech_app.backend.main as main
from tech_app.backend.models.ir import DesignIR
from tech_app.backend.services import cpq_kb_client
from tech_app.backend.storage import kb_repo, store

client = TestClient(main.app, raise_server_exceptions=False)

PARTS = [
    {"part_id": "P-001", "name": "顶盖板", "quantity": 1,
     "features": [{"type": "box", "length": 108, "width": 56, "height": 13.25}]},
    {"part_id": "P-002", "name": "主体安装块", "quantity": 1,
     "features": [{"type": "box", "length": 108, "width": 56, "height": 13.25}]},
]

out = {"data_dir": data_dir}
out["store_has_api"] = bool(hasattr(store, "save_component_match_unavailable")
                            and hasattr(store, "load_component_match_unavailable"))

pid = store.create_project(source_filename="电池图纸案例1.png", source_bytes=b"png",
                           note="kb unavailable notice probe", owner="tester")
store.save_ir(pid, DesignIR(device_name="电池箱", design_intent="零件库不可用探针",
                            parts=PARTS).model_dump(), stage="parsed")
out["project"] = pid


def body_of(response):
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - 非 JSON 也要如实回报
        return {"raw": response.text[:300]}


# 1. 自动路：解析完成后的"顺手检索"，知识库连不上。
try:
    main._refresh_component_match(pid, {"parts": PARTS}, kept="解析结果")
    out["auto_raised"] = None
except BaseException as exc:  # noqa: BLE001
    out["auto_raised"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])

if out["store_has_api"]:
    info = store.load_component_match_unavailable(pid)
    out["auto_unavailable"] = info if isinstance(info, dict) else (
        None if info is None else str(info))
else:
    out["auto_unavailable"] = None
out["auto_report"] = store.load_component_match(pid)

# 2. 读取出口必须把"未检索"交出来。
response = client.get("/api/projects/%s/component-match" % pid)
out["get"] = {"status": response.status_code, "body": body_of(response)}

# 3. 主动路：任务失败，但状态要留下（打桩 submit 同步跑 job，绝不真起后台线程）。
def fake_submit(project_id, kind, job=None, *args, **kwargs):
    out["post_job_error"] = None
    if job:
        try:
            job()
        except BaseException as exc:  # noqa: BLE001
            out["post_job_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    return "task-stub"


main.tasks.submit = fake_submit
response = client.post("/api/projects/%s/component-match" % pid)
out["post"] = {"status": response.status_code, "body": body_of(response)}
response = client.get("/api/projects/%s/component-match" % pid)
out["get_after_post"] = {"status": response.status_code, "body": body_of(response)}

# 4. 重试成功：把快照打桩成可用（夹具），警示必须被清掉。
SNAP = json.loads(open(fixture, encoding="utf-8").read())
cpq_kb_client.fetch_snapshot = lambda since=None: {
    "ok": True, "kb_version": 7, "unchanged": False, "tables": SNAP}
kb_repo._CACHE["version"] = None
kb_repo._CACHE["tables"] = {}
try:
    main._refresh_component_match(pid, {"parts": PARTS}, kept="解析结果")
    out["retry_raised"] = None
except BaseException as exc:  # noqa: BLE001
    out["retry_raised"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
report = store.load_component_match(pid)
out["retry_report_items"] = len((report or {}).get("items") or [])
out["retry_library_size"] = (report or {}).get("library_size")
out["retry_unavailable"] = (store.load_component_match_unavailable(pid)
                            if out["store_has_api"] else "api-missing")
response = client.get("/api/projects/%s/component-match" % pid)
out["get_after_retry"] = {"status": response.status_code, "body": body_of(response)}

print(json.dumps(out, ensure_ascii=False, default=str))
'''


def zero_library_reports(data_dir: str) -> list:
    """磁盘上是否存在把故障当结论的 library_size=0 报告。"""
    offenders = []
    for path in pathlib.Path(data_dir).rglob("component_match*.json"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r'"library_size"\s*:\s*0\b', text):
            offenders.append(str(path))
    return offenders


# --------------------------------------------------------------------------- #
# A. 源码契约
# --------------------------------------------------------------------------- #
class NoticeSourceContractTest(unittest.TestCase):
    def test_01_spec_pins_the_contract(self):
        text = read(SPEC)
        self.assertTrue(text, "缺少 docs/specs/tech-kb-unavailable-loud-notice.md")
        for token in ("component_match_unavailable", BANNER_ID, BANNER_CLASS,
                      BANNER_TEXT, CONCLUSION_TEXT):
            self.assertIn(token, text, "spec 未钉住 %s" % token)

    def test_02_store_persists_unavailable_state(self):
        text = read(STORE_PY)
        self.assertIn("def save_component_match_unavailable", text,
                      "store 缺 save_component_match_unavailable")
        self.assertIn("def load_component_match_unavailable", text,
                      "store 缺 load_component_match_unavailable")
        body = def_block(text, "def save_component_match(")
        self.assertIn("component_match_unavailable", body,
                      "save_component_match 必须在成功落报告时清掉「未检索」状态，否则重试成功后警示不消失")

    def test_03_get_route_exposes_unavailable(self):
        body = def_block(read(MAIN_PY), "def get_component_match(")
        self.assertTrue(body, "找不到 main.get_component_match")
        self.assertIn("unavailable", body,
                      "读取出口必须把「未检索」交给前端；查不到报告时不能再只回空报告")

    def test_04_auto_path_persists_and_keeps_left_notice(self):
        body = def_block(read(MAIN_PY), "def _refresh_component_match(")
        self.assertTrue(body, "找不到 main._refresh_component_match")
        self.assertIn("save_component_match_unavailable", body,
                      "自动路失败时必须落「未检索」状态，不能只写一条审计")
        self.assertIn("report_progress", body,
                      "左边那条提示要保留（既有行为不得为了少一行而删掉）")

    def test_05_post_task_persists_unavailable(self):
        body = def_block(read(MAIN_PY), "def run_component_match(")
        self.assertTrue(body, "找不到 main.run_component_match")
        self.assertIn("save_component_match_unavailable", body,
                      "主动路的任务失败也要留下「未检索」状态，刷新后还能看见")

    def test_06_frontend_renders_banner_and_conclusion(self):
        body = block_from(read(APP_JS_PATH), "function renderComponentMatchResult(")
        self.assertTrue(body, "找不到 app.js 的 renderComponentMatchResult()")
        self.assertIn("unavailable", body, "渲染没有区分「未检索」")
        self.assertIn(BANNER_ID, body, "缺少看板顶部警示节点 %s" % BANNER_ID)
        self.assertIn(CONCLUSION_TEXT, body, "结论区没有写「%s」" % CONCLUSION_TEXT)

    def test_07_css_has_red_warning_style(self):
        text = read(WORKBENCH_CSS_PATH)
        match = re.search(r"\.%s\s*\{([^}]*)\}" % re.escape(BANNER_CLASS), text)
        self.assertIsNotNone(match, "workbench.css 缺 .%s 样式" % BANNER_CLASS)
        rule = (match.group(1) or "").lower()
        self.assertTrue("red" in rule or re.search(r"#[a-f0-9]{6}", rule),
                        "警示必须是红色（用 var(--color-red)），现在是：%s" % rule[:200])

    def test_08_asset_versions_bumped(self):
        html = read(INDEX_HTML_PATH)
        self.assertNotIn("app.js?v=%s" % BEFORE_APP_VERSION, html,
                         "改了 app.js 必须换掉 index.html 里的版本号（否则老缓存挡住新代码）")
        self.assertNotIn("workbench.css?v=%s" % BEFORE_CSS_VERSION, html,
                         "改了 workbench.css 必须换掉 index.html 里的版本号")


# --------------------------------------------------------------------------- #
# B. 后端动态：知识库连不上 → 状态必须留下；重试成功 → 状态清掉
# --------------------------------------------------------------------------- #
class KbUnavailableNoticeBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = tempfile.mkdtemp(prefix="cpq-kb-notice-data-")
        cls.out = run_child(CHILD_BACKEND, cls.data_dir, str(ROOT), str(SNAPSHOT_FIXTURE))

    def test_09_store_api_exists(self):
        self.assertTrue(self.out.get("store_has_api"),
                        "store 还没有 save/load_component_match_unavailable")

    def test_10_auto_path_records_state_without_report(self):
        self.assertIsNone(self.out.get("auto_raised"),
                          "自动路不该把异常抛给调用方（已拿到的解析结果不能作废）：%s"
                          % self.out.get("auto_raised"))
        info = self.out.get("auto_unavailable")
        self.assertIsInstance(info, dict,
                              "自动路失败后必须留下可恢复的状态，现在：%r" % (info,))
        for key in ("reason", "message", "at"):
            self.assertTrue(str(info.get(key) or "").strip(),
                            "未检索状态缺 %s：%s" % (key, info))
        self.assertEqual("kb_unavailable", info.get("reason"),
                         "知识库不可用必须标成 kb_unavailable：%s" % info)
        self.assertIsNone(self.out.get("auto_report"),
                          "知识库不可用时不得落一份空报告：%r" % (self.out.get("auto_report"),))

    def test_11_get_route_exposes_unavailable_without_zero_counts(self):
        got = self.out.get("get") or {}
        self.assertEqual(200, got.get("status"), "读取出口应 200：%s" % got)
        body = got.get("body") or {}
        self.assertTrue(body.get("unavailable"),
                        "读取出口必须把「未检索」交出来：%s" % body)
        self.assertNotEqual(0, body.get("library_size"),
                            "不得把故障说成「库内 0 条」：%s" % body)
        self.assertEqual([], body.get("items") or [], "本次没有检索结果：%s" % body)

    def test_12_post_path_fails_and_records(self):
        post = self.out.get("post") or {}
        self.assertEqual(200, post.get("status"), "提交任务应 200：%s" % post)
        self.assertTrue(str(self.out.get("post_job_error") or "").strip(),
                        "知识库不可用时主动检索必须失败：%s" % post)
        after = (self.out.get("get_after_post") or {}).get("body") or {}
        self.assertTrue(after.get("unavailable"),
                        "主动检索失败后刷新页面仍要看到「未检索」：%s" % after)

    def test_13_retry_success_clears_state(self):
        self.assertIsNone(self.out.get("retry_raised"),
                          "重试成功不应抛错：%s" % self.out.get("retry_raised"))
        self.assertEqual(20, self.out.get("retry_library_size"),
                         "重试成功后 library_size 应为库内真实条数：%s"
                         % self.out.get("retry_library_size"))
        self.assertIsNone(self.out.get("retry_unavailable"),
                          "重试成功后必须清掉「未检索」状态，警示才会消失：%r"
                          % (self.out.get("retry_unavailable"),))
        after = (self.out.get("get_after_retry") or {}).get("body") or {}
        self.assertIsNone(after.get("unavailable"),
                          "读取出口也不该再报「未检索」：%s" % after)

    def test_14_never_persists_zero_library_report(self):
        offenders = zero_library_reports(self.data_dir)
        self.assertEqual([], offenders,
                         "磁盘上出现了把故障当结论的 library_size=0 报告：%s" % offenders)


# --------------------------------------------------------------------------- #
# C. 前端动态：看板顶部红色警示 + 结论区文案
# --------------------------------------------------------------------------- #
class KbUnavailableNoticeFrontendTest(unittest.TestCase):
    def test_20_unavailable_shows_board_top_banner(self):
        outcome = run_node(frontend_driver({
            "items": [], "summary": {}, "library_size": None, "unavailable": UNAVAILABLE}))
        self.assertNotIn("error", outcome, "渲染抛错：%s" % outcome)
        banner = outcome.get("banner")
        self.assertIsInstance(banner, dict, "看板顶部没有出现警示节点：%s" % outcome)
        self.assertIn(BANNER_CLASS, banner.get("className") or "",
                      "警示节点缺红色类名 %s：%s" % (BANNER_CLASS, banner))
        self.assertIn(BANNER_TEXT, banner.get("text") or "",
                      "警示文案必须是「%s」：%s" % (BANNER_TEXT, banner))
        self.assertTrue(outcome.get("bannerAttached"), "警示没挂进看板：%s" % outcome)
        self.assertTrue(outcome.get("bannerBeforeParts"),
                        "警示必须排在零件清单之前（看板顶部）：%s" % outcome)

    def test_21_unavailable_never_reports_zero_counts(self):
        outcome = run_node(frontend_driver({
            "items": [], "summary": {}, "library_size": None, "unavailable": UNAVAILABLE}))
        self.assertNotIn("error", outcome, "渲染抛错：%s" % outcome)
        self.assertIn(CONCLUSION_TEXT, outcome.get("resultText") or "",
                      "结论区必须写「%s」：%s" % (CONCLUSION_TEXT, outcome.get("resultText")))
        page = outcome.get("pageText") or ""
        for text in ZERO_COUNT_TEXTS:
            self.assertNotIn(text, page,
                             "知识库连不上时不得出现「%s」这类把故障当结论的文案：%s" % (text, page))

    def test_22_previous_report_is_labeled_stale(self):
        payload = dict(PREVIOUS_REPORT)
        payload["unavailable"] = UNAVAILABLE
        outcome = run_node(frontend_driver(payload))
        self.assertNotIn("error", outcome, "渲染抛错：%s" % outcome)
        page = outcome.get("pageText") or ""
        self.assertIn(CONCLUSION_TEXT, outcome.get("resultText") or "",
                      "本次结论仍是「未检索」：%s" % outcome.get("resultText"))
        self.assertIn("上一次", page,
                      "上次的成功结论必须标注为「上一次的结论（可能已过期）」：%s" % page)

    def test_23_success_render_removes_banner(self):
        extra = (
            "    globalThis.__probe = {afterUnavailable: !!doc.getElementById(%s)};\n"
            "    await renderComponentMatchResult(%s);\n"
            "    var afterSuccess = doc.getElementById(%s);\n"
            "    globalThis.__probe.afterSuccess = afterSuccess ? "
            "(afterSuccess.hidden || flatten(board).indexOf(afterSuccess) < 0) : true;\n"
            % (json.dumps(BANNER_ID), json.dumps(OK_REPORT, ensure_ascii=False),
               json.dumps(BANNER_ID)))
        outcome = run_node(frontend_driver(
            {"items": [], "summary": {}, "library_size": None, "unavailable": UNAVAILABLE},
            extra=extra))
        self.assertNotIn("error", outcome, "渲染抛错：%s" % outcome)
        probe = outcome.get("probe") or {}
        self.assertTrue(probe.get("afterUnavailable"),
                        "知识库连不上时必须先出现警示（否则这条测试是空转）：%s" % outcome)
        self.assertIn("库内 20 条", outcome.get("pageText") or "",
                      "检索成功后应照常渲染结论：%s" % outcome.get("pageText"))
        self.assertTrue(probe.get("afterSuccess"),
                        "检索成功后警示必须消失：%s" % outcome)


if __name__ == "__main__":
    unittest.main(verbosity=2)
