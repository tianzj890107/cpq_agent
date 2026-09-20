"""红测：技术工艺业务页面「项目身份唯一来源」与防串项目（批次 1）。

用户口径（批次 1 原文要点）：

  · 消除技术工艺业务页面在 URL 缺失 project 时，静默使用 localStorage 中上一次项目的
    行为；统一工作台内，项目身份必须来自当前 URL、父壳绑定状态或明确任务关联，不能猜。
  · localStorage 只能承担「最近访问导航」，不能决定业务数据归属。
  · 缺少项目时不得请求项目接口、不得写入、不得自动打开上一次项目。
  · 两个浏览器标签同时打开不同项目不能互相影响；iframe 与父壳不一致必须拒绝。
  · task_id 能唯一关联时允许恢复；关联不唯一时必须停止并提示。

现状缺口（实测，`grep` 定位，均为读取点，不是推断）：

  · `assembly-integration.js:14`、`cost-review.js:15`、`requirement-create.js:2`、
    `requirement-confirm-page.js:2`、`requirement-review-page.js:2`、`requirement-detail.js:2`、
    `summary-result.js:2`、`report-review-result.js:2`、`report-publish-result.js:2` 都是
    `?project || localStorage.getItem('cad_engine_project_id') || ''`；
  · `tech-embed.js:49`、`workflow-navigation.js:28`（还多读 `currentProject`）、
    `workflow.js:17` 是同一表达式的第二、三、四份拷贝；
  · 2.1 的 `app.js:525` 是 `q.get("project") || localStorage.getItem("lastProject")`，会
    「自动打开上一次项目」；
  · 父壳 `tech-workbench.js:862-877` 的 message 处理器只校验 origin / source / stage
    白名单，`data.project` 直接进 `applyStage` —— iframe 能把父壳切到另一个项目。

本批的验收核心是**行为**：用 node 真跑（不是静态文本搜索）

  ① 新增共享解析模块 `tech_app/frontend/tech-project-context.js`（`window.TechProjectContext`）的
     全部判定；② 12 个页面/访问点在四种环境下的真实解析值；③ `fromTask` 的唯一性、去重与并发。

结构断言只用于 DOM / 脚本加载顺序 / 调用点接线（用户允许的静态契约范围）。

Spec：docs/specs/tech-project-identity-single-source.md
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONT = ROOT / "tech_app" / "frontend"
SHARED = FRONT / "tech-project-context.js"
SPEC = ROOT / "docs" / "specs" / "tech-project-identity-single-source.md"
NODE = shutil.which("node")

STORAGE_KEYS = ("cad_engine_project_id", "currentProject", "lastProject")
STALE = {"cad_engine_project_id": "B", "currentProject": "B", "lastProject": "B"}

# 页面 → (访问点常量名, 额外需要一并纳入的语句标记)
CONST_PAGES = (
    ("requirement-create.js", "rcPid", ()),
    ("requirement-confirm-page.js", "cfPid", ()),
    ("requirement-review-page.js", "rrPid", ()),
    ("requirement-detail.js", "rdPid", ()),
    ("summary-result.js", "srPid", ()),
    ("report-review-result.js", "rrPid", ()),
    ("report-publish-result.js", "rpPid", ()),
    ("assembly-integration.js", "aiPid", ()),
    ("cost-review.js", "crPid", ()),
    ("app.js", "pid", ("new URLSearchParams(location.search)",)),
)

ACCESSOR_PAGES = ("tech-embed.js", "workflow-navigation.js", "workflow.js")

BUSINESS_HTML = {
    "index.html": "app.js",
    "assembly-integration.html": "assembly-integration.js",
    "cost-review.html": "cost-review.js",
    "requirement-create.html": "requirement-create.js",
    "requirement-confirm.html": "requirement-confirm-page.js",
    "requirement-review.html": "requirement-review-page.js",
    "requirement-detail.html": "requirement-detail.js",
    "summary.html": "summary-result.js",
    "report-review.html": "report-review-result.js",
    "report-publish.html": "report-publish-result.js",
    "tech-workbench.html": "tech-workbench.js",
}


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


# --------------------------------------------------------------------------- #
# 语句 / 块提取（字符串与注释感知；不依赖固定行号）
# --------------------------------------------------------------------------- #
_TERMINATORS = ";{}"


def _skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return len(text)


def stmt_start(text: str, idx: int) -> int:
    i = idx
    while i > 0 and text[i - 1] not in _TERMINATORS:
        i -= 1
    while i < idx and text[i] in " \t\r\n":
        i += 1
    return i


def stmt_end(text: str, idx: int) -> int:
    depth = 0
    i = idx
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
        if ch in "\"'":
            i = _skip_string(text, i)
            continue
        if ch == "`":
            i = _skip_string(text, i)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth == 0:
            return i + 1
        i += 1
    return len(text)


def block_end(text: str, idx: int) -> int:
    """从 idx 起找到第一个 `{` 的配对 `}`，返回其后的位置。"""
    depth = 0
    i = text.find("{", idx)
    if i < 0:
        return len(text)
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
            i = _skip_string(text, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def const_snippet(text: str, name: str, extra: tuple = ()) -> str:
    """提取 `const/let/var <name> = …;` 语句（连同前置的 TechProjectContext 相关语句）。"""
    match = re.search(r"(?:const|let|var)\s+%s\s*=" % re.escape(name), text)
    if not match:
        return ""
    at = match.start()
    start, end = stmt_start(text, at), stmt_end(text, at)
    for marker in extra:
        pos = text.rfind(marker, 0, at)
        if pos >= 0:
            start = min(start, stmt_start(text, pos))
    ctx = text.rfind("TechProjectContext", 0, at)
    if ctx >= 0:
        start = min(start, stmt_start(text, ctx))
    return text[start:end].strip()


def function_snippet(text: str, name: str) -> str:
    """提取 `function <name>(…) { … }`（允许 `const <name> = (…) => {}`）。"""
    match = re.search(r"function\s+%s\s*\(" % re.escape(name), text)
    if match:
        return text[stmt_start(text, match.start()):block_end(text, match.start())].strip()
    match = re.search(r"(?:const|let|var)\s+%s\s*=\s*(?:function|\([^)]*\)\s*=>)" % re.escape(name), text)
    if match:
        return text[stmt_start(text, match.start()):stmt_end(text, match.start())].strip()
    return ""


def range_snippet(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = text.find(end_marker, start)
    if end < 0:
        return ""
    return text[stmt_start(text, start):block_end(text, end)].strip()


# --------------------------------------------------------------------------- #
# node 驱动：真跑模块与页面片段
# --------------------------------------------------------------------------- #
DRIVER = r'''
const vm = require('vm');
const fs = require('fs');
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const moduleSrc = (spec.module && fs.existsSync(spec.module)) ? fs.readFileSync(spec.module, 'utf8') : '';
const stores = {};

function makeEl() {
  const holder = {
    children: [], childNodes: [], dataset: {}, style: {}, innerHTML: '', textContent: '',
    hidden: false, value: '', checked: false, disabled: false, tagName: 'DIV', className: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  };
  const fn = function () { return makeEl(); };
  return new Proxy(holder, {
    get(t, k) { if (k in t) return t[k]; if (typeof k === 'symbol') return undefined; return fn; },
    set(t, k, v) { t[k] = v; return true; },
    has() { return true; },
  });
}

function makeDoc() {
  return {
    getElementById: () => makeEl(), querySelector: () => makeEl(), querySelectorAll: () => [],
    createElement: () => makeEl(), addEventListener() {}, head: makeEl(), body: makeEl(),
    documentElement: makeEl(), readyState: 'complete',
  };
}

function splitStatements(src) {
  const out = [];
  let depth = 0, start = 0, i = 0;
  while (i < src.length) {
    const ch = src[i], nxt = src[i + 1] || '';
    if (ch === '/' && nxt === '/') { const j = src.indexOf('\n', i); i = j < 0 ? src.length : j; continue; }
    if (ch === '/' && nxt === '*') { const j = src.indexOf('*/', i); i = j < 0 ? src.length : j + 2; continue; }
    if (ch === '"' || ch === "'" || ch === '`') {
      const quote = ch; i += 1;
      while (i < src.length) { if (src[i] === '\\') { i += 2; continue; } if (src[i] === quote) { i += 1; break; } i += 1; }
      continue;
    }
    if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    else if (ch === ')' || ch === ']') depth -= 1;
    else if (ch === '}') { depth -= 1; if (depth <= 0) { out.push(src.slice(start, i + 1)); start = i + 1; depth = 0; } }
    else if (ch === ';' && depth === 0) { out.push(src.slice(start, i + 1)); start = i + 1; }
    i += 1;
  }
  if (src.slice(start).trim()) out.push(src.slice(start));
  return out.filter((part) => part.trim());
}

function makeSandbox(job, store, reads, fetchLog) {
  const sandbox = {};
  sandbox.console = { log() {}, warn() {}, error() {} };
  sandbox.setTimeout = setTimeout; sandbox.clearTimeout = clearTimeout;
  sandbox.Promise = Promise; sandbox.JSON = JSON; sandbox.Math = Math; sandbox.Date = Date;
  sandbox.URLSearchParams = URLSearchParams; sandbox.URL = URL;
  sandbox.encodeURIComponent = encodeURIComponent; sandbox.decodeURIComponent = decodeURIComponent;
  sandbox.document = makeDoc();
  sandbox.localStorage = {
    getItem: (k) => { reads.push(String(k)); return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  sandbox.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  const pathname = job.pathname || '/index.html';
  const search = job.search || '';
  sandbox.location = { search, pathname, href: 'http://localhost' + pathname + search,
                       origin: 'http://localhost', host: 'localhost', hash: '' };
  sandbox.history = { pushState() {}, replaceState() {} };
  sandbox.navigator = { userAgent: 'probe' };
  sandbox.addEventListener = function () {};
  sandbox.postMessage = function () {};
  sandbox.frameElement = job.frameProject ? { dataset: { project: job.frameProject } } : null;
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.top = sandbox;
  sandbox.parent = sandbox;
  const headers = job.token ? { Authorization: 'Bearer ' + job.token } : {};
  sandbox.fetch = function (url, opts) {
    fetchLog.push({ url: String(url), headers: (opts && opts.headers) || {} });
    const body = job.fetchResponse || {};
    const status = job.fetchStatus === undefined ? 200 : job.fetchStatus;
    return new Promise((resolve, reject) => setTimeout(() => {
      if (job.fetchFailure) { reject(new Error(job.fetchFailure)); return; }
      resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
    }, 1));
  };
  return sandbox;
}

async function runJob(job) {
  const group = job.storageGroup || job.name;
  if (!stores[group]) stores[group] = Object.assign({}, job.storage || {});
  const store = stores[group];
  const reads = [];
  const fetchLog = [];
  const sandbox = makeSandbox(job, store, reads, fetchLog);
  vm.createContext(sandbox);
  if (moduleSrc) {
    try { vm.runInContext(moduleSrc, sandbox, { filename: 'tech-project-context.js' }); }
    catch (error) { return { name: job.name, ok: false, error: '模块加载失败: ' + error.message, reads, calls: fetchLog }; }
  }
  const skipped = [];
  try {
    if (job.accumulate) {
      for (const part of splitStatements(job.snippet)) {
        try { vm.runInContext(part, sandbox, { filename: job.file || job.name }); }
        catch (error) { skipped.push(part.slice(0, 60)); }
      }
    } else {
      vm.runInContext(job.snippet, sandbox, { filename: job.file || job.name });
    }
    let value;
    vm.runInContext('__value = (function(){ try { return (' + job.read + '); } catch (e) { return { __error: String(e && e.message || e) }; } })();', sandbox, { filename: 'read' });
    value = sandbox.__value;
    if (job.await) { value = await value; }
    let encoded;
    try { encoded = JSON.parse(JSON.stringify(value === undefined ? null : value)); }
    catch (error) { encoded = String(value); }
    return { name: job.name, ok: true, value: encoded, reads, calls: fetchLog, skipped };
  } catch (error) {
    return { name: job.name, ok: false, error: error.constructor.name + ': ' + error.message, reads, calls: fetchLog, skipped };
  }
}

(async function main() {
  const out = [];
  for (const job of spec.jobs) out.push(await runJob(job));
  process.stdout.write(JSON.stringify(out));
})();
'''


def run_jobs(jobs: list, module: pathlib.Path = SHARED) -> dict:
    """真跑 node。模块缺失时不提前报错：让每个用例自己暴露真实行为（现状=退回旧项目）。"""
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端行为走查")
    with tempfile.TemporaryDirectory(prefix="cpq-pid-spec-") as tmp:
        spec_path = pathlib.Path(tmp) / "spec.json"
        driver_path = pathlib.Path(tmp) / "driver.js"
        target = str(module) if module.exists() else ""
        spec_path.write_text(json.dumps({"module": target, "jobs": jobs}), encoding="utf-8")
        driver_path.write_text(DRIVER, encoding="utf-8")
        completed = subprocess.run([NODE, str(driver_path), str(spec_path)],
                                   capture_output=True, text=True, timeout=120)
        if completed.returncode != 0:
            raise AssertionError("node 走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2000:],
                                    completed.stderr[-2000:]))
        rows = json.loads(completed.stdout.strip().splitlines()[-1])
        return {row["name"]: row for row in rows}


def module_jobs(pairs: list) -> list:
    """pairs: (name, 表达式, 附加配置字典)。表达式结果写入 __api。"""
    jobs = []
    for name, expression, extra in pairs:
        job = {"name": name, "snippet": "__api = (%s);" % expression, "read": "__api"}
        job.update(extra or {})
        jobs.append(job)
    return jobs


def value_of(rows: dict, name: str):
    """取真实解析结果；任务本身执行失败时给出可读的缺口说明。"""
    row = rows.get(name)
    if not row or not row.get("ok"):
        raise AssertionError("%s 未能执行（缺口：%r）" % (name, row))
    return row["value"]


def stmt_jobs(pairs: list) -> list:
    """pairs: (name, 语句片段（须写 __api）, 附加配置)。"""
    jobs = []
    for name, statements, extra in pairs:
        job = {"name": name, "snippet": statements, "read": "__api"}
        job.update(extra or {})
        jobs.append(job)
    return jobs


def fetch_doc(payload: dict, ok: bool = True) -> dict:
    return {"ok": ok, "task": {"task_id": "T1", "payload": payload}}


# --------------------------------------------------------------------------- #
# 1. Spec 锚点
# --------------------------------------------------------------------------- #
class SpecPinnedTest(unittest.TestCase):
    def test_spec_pins_contract(self):
        self.assertTrue(SPEC.exists(), "缺少 Spec：%s" % SPEC)
        text = read(SPEC)
        for token in ("TechProjectContext", "tech-project-context.js", "resolve", "bind",
                      "current", "assertSame", "fromTask", "projectFromTaskPayload",
                      "missing_project", "project_mismatch", "task_no_project", "task_ambiguous",
                      "cad_engine_project_id", "lastProject", "data-project", "frameElement",
                      "非目标", "权限边界", "不允许减少的既有能力", "历史数据兼容"):
            self.assertIn(token, text, "Spec 缺少契约锚点：%s" % token)


# --------------------------------------------------------------------------- #
# 2. 走查脚手架自检（证明 harness 不是永远失败）
# --------------------------------------------------------------------------- #
COMPLIANT_MODULE = """
(function (root) {
  'use strict';
  function readSearch(search) {
    var text = String(search || '');
    if (text.charAt(0) === '?') text = text.slice(1);
    return new URLSearchParams(text).get('project') || '';
  }
  function parentProject() {
    try {
      if (typeof window !== 'undefined' && window.frameElement && window.frameElement.dataset) {
        return String(window.frameElement.dataset.project || '');
      }
    } catch (error) { return ''; }
    return '';
  }
  function resolve(options) {
    var opts = options || {};
    var search = opts.search === undefined ? (typeof location !== 'undefined' ? location.search : '') : opts.search;
    var parent = opts.parentProject === undefined ? parentProject() : opts.parentProject;
    var project = readSearch(search);
    if (project && parent && project !== parent) {
      return { project: '', source: '', error: 'project_mismatch', message: '项目不一致，已停止' };
    }
    if (project) return { project: project, source: 'url', error: '', message: '' };
    if (parent) return { project: parent, source: 'parent', error: '', message: '' };
    return { project: '', source: '', error: 'missing_project', message: '缺少项目' };
  }
  var api = { resolve: resolve, bind: function (o) { return resolve(o); }, current: function () { return resolve({}); } };
  root.TechProjectContext = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
"""


class HarnessSelfTest(unittest.TestCase):
    def test_harness_reads_compliant_page_correctly(self):
        with tempfile.TemporaryDirectory(prefix="cpq-pid-self-") as tmp:
            module = pathlib.Path(tmp) / "tech-project-context.js"
            module.write_text(COMPLIANT_MODULE, encoding="utf-8")
            jobs = [
                {"name": "no_url", "snippet": "const rcPid = TechProjectContext.bind().project;",
                 "read": "rcPid", "search": "", "storage": dict(STALE)},
                {"name": "url_a", "snippet": "const rcPid = TechProjectContext.bind().project;",
                 "read": "rcPid", "search": "?project=A", "storage": dict(STALE)},
                {"name": "mismatch", "snippet": "const rcPid = TechProjectContext.bind().project;",
                 "read": "rcPid", "search": "?project=A&embed=1", "frameProject": "B",
                 "storage": dict(STALE)},
                {"name": "parent", "snippet": "const rcPid = TechProjectContext.bind().project;",
                 "read": "rcPid", "search": "?embed=1", "frameProject": "P1", "storage": dict(STALE)},
                {"name": "multi_statement",
                 "snippet": "const ctx = TechProjectContext.bind();\nconst rcPid = ctx.project;",
                 "read": "rcPid", "search": "", "storage": dict(STALE)},
                {"name": "two_statement_accumulate",
                 "snippet": "const nothing = document.getElementById('x');\nconst rcPid = TechProjectContext.bind().project;",
                 "read": "rcPid", "search": "", "storage": dict(STALE), "accumulate": True},
            ]
            rows = run_jobs(jobs, module=module)
            self.assertEqual(rows["no_url"]["value"], "", "自检：无 project 时必须解析成空")
            self.assertEqual(rows["url_a"]["value"], "A", "自检：URL 项目必须优先")
            self.assertEqual(rows["mismatch"]["value"], "", "自检：父壳不一致必须拒绝")
            self.assertEqual(rows["parent"]["value"], "P1", "自检：嵌入时可用父壳项目")
            self.assertEqual(rows["multi_statement"]["value"], "", "自检：多语句形态要能求值")
            self.assertEqual(rows["two_statement_accumulate"]["value"], "",
                             "自检：累加求值要能跳过不能独立求值的语句：%r"
                             % (rows["two_statement_accumulate"],))


# --------------------------------------------------------------------------- #
# 3. 共享模块行为
# --------------------------------------------------------------------------- #
class SharedContextModuleTest(unittest.TestCase):
    def test_shared_module_exists(self):
        self.assertTrue(SHARED.exists(),
                        "缺少项目身份唯一来源模块：tech_app/frontend/tech-project-context.js")

    @classmethod
    def setUpClass(cls):
        cls.rows = run_jobs(module_jobs([
            ("url_only", "TechProjectContext.resolve({search:'?project=A'})", {}),
            ("no_url_with_stale_storage", "TechProjectContext.resolve({search:''})",
             {"storage": dict(STALE)}),
            ("url_a_storage_b", "TechProjectContext.resolve({search:'?project=A'})",
             {"storage": dict(STALE)}),
            ("url_a_parent_b", "TechProjectContext.resolve({search:'?project=A', parentProject:'B'})", {}),
            ("url_a_parent_a", "TechProjectContext.resolve({search:'?project=A', parentProject:'A'})", {}),
            ("no_url_parent_b", "TechProjectContext.resolve({search:'', parentProject:'B'})", {}),
            ("env_no_url", "TechProjectContext.bind().project", {"search": "", "storage": dict(STALE)}),
            ("env_url_a", "TechProjectContext.bind().project", {"search": "?project=A", "storage": dict(STALE)}),
            ("env_mismatch", "TechProjectContext.bind().project",
             {"search": "?project=A&embed=1", "frameProject": "B"}),
            ("env_parent", "TechProjectContext.bind().project", {"search": "?embed=1", "frameProject": "P1"}),
            ("env_frame_cross_domain", "TechProjectContext.bind({search:''}).project", {}),
            ("idempotent_1", "JSON.stringify([TechProjectContext.resolve({search:''}), TechProjectContext.resolve({search:''})])",
             {"storage": dict(STALE)}),
        ]))
        # assertSame / current 以「本页 bind() 读环境得到的身份」为准（页面真实用法）。
        cls.rows.update(run_jobs(stmt_jobs([
            ("repeat_bind", "TechProjectContext.bind();\n"
                            "__api = JSON.stringify([TechProjectContext.current().project,"
                            " TechProjectContext.bind().project]);", {"search": "?project=A"}),
            ("same_ok", "TechProjectContext.bind();\n__api = TechProjectContext.assertSame('A');",
             {"search": "?project=A"}),
            ("same_wrong", "TechProjectContext.bind();\n__api = TechProjectContext.assertSame('B');",
             {"search": "?project=A"}),
            ("same_missing", "TechProjectContext.bind();\n__api = TechProjectContext.assertSame('A');",
             {"search": "", "storage": dict(STALE)}),
            ("same_missing_storage_b", "TechProjectContext.bind();\n"
                                       "__api = TechProjectContext.assertSame('B');",
             {"search": "", "storage": dict(STALE)}),
        ])))

    def value(self, name):
        row = self.rows[name]
        self.assertTrue(row.get("ok"), "模块执行失败 %s：%r" % (name, row))
        return row["value"]

    def test_url_project_wins(self):
        self.assertEqual(self.value("url_only")["project"], "A")
        self.assertEqual(self.value("url_only")["source"], "url")
        self.assertEqual(self.value("url_a_storage_b")["project"], "A")

    def test_missing_project_is_explicit_and_storage_is_never_read(self):
        row = self.value("no_url_with_stale_storage")
        self.assertEqual(row["project"], "", "无 project 时必须为空，不得退回 localStorage")
        self.assertEqual(row["error"], "missing_project")
        self.assertTrue(row["message"], "缺少项目必须带可展示文案")
        self.assertEqual(self.rows["no_url_with_stale_storage"]["reads"], [],
                         "项目身份解析不得读取 localStorage：%r"
                         % (self.rows["no_url_with_stale_storage"]["reads"],))
        self.assertEqual(self.rows["url_a_storage_b"]["reads"], [],
                         "项目身份解析不得读取 localStorage：%r"
                         % (self.rows["url_a_storage_b"]["reads"],))

    def test_parent_and_mismatch(self):
        self.assertEqual(self.value("no_url_parent_b")["project"], "B")
        self.assertEqual(self.value("no_url_parent_b")["source"], "parent")
        self.assertEqual(self.value("url_a_parent_a")["project"], "A")
        mismatch = self.value("url_a_parent_b")
        self.assertEqual(mismatch["project"], "", "父壳与 URL 不一致必须拒绝")
        self.assertEqual(mismatch["error"], "project_mismatch")
        self.assertTrue(mismatch["message"])

    def test_environment_binding(self):
        self.assertEqual(self.value("env_no_url"), "",
                         "bind() 读环境时：无 URL project 且 localStorage 有旧项目 → 必须为空")
        self.assertEqual(self.value("env_url_a"), "A")
        self.assertEqual(self.value("env_mismatch"), "", "iframe 项目 ≠ 父壳项目必须拒绝")
        self.assertEqual(self.value("env_parent"), "P1", "嵌入且 URL 无 project 时用父壳项目")
        self.assertEqual(self.rows["env_no_url"]["reads"], [], "bind() 不得读 localStorage")

    def test_idempotent_and_repeatable(self):
        pair = json.loads(self.value("idempotent_1"))
        self.assertEqual(pair[0], pair[1], "resolve() 必须幂等")
        self.assertEqual(pair[0]["project"], "")
        self.assertEqual(json.loads(self.value("repeat_bind")), ["A", "A"],
                         "bind()/current() 必须一致（同一环境重复调用结果全等）")

    def test_assert_same_guard(self):
        self.assertTrue(self.value("same_ok")["ok"])
        wrong = self.value("same_wrong")
        self.assertFalse(wrong["ok"], "写前一致性校验必须拒绝不同项目")
        self.assertEqual(wrong["error"], "project_mismatch")
        missing = self.value("same_missing")
        self.assertFalse(missing["ok"], "没有项目时不允许任何写入")
        self.assertEqual(missing["error"], "missing_project")

    def test_task_payload_unique(self):
        rows = run_jobs(module_jobs([
            ("tech_cost", "TechProjectContext.projectFromTaskPayload({tech_cost:{project_id:'P1'}})", {}),
            ("tech_project_id", "TechProjectContext.projectFromTaskPayload({tech_project_id:'P2'})", {}),
            ("flat_project_id", "TechProjectContext.projectFromTaskPayload({project_id:'P3'})", {}),
            ("tech_result", "TechProjectContext.projectFromTaskPayload({tech_result:{project_id:'P4'}})", {}),
            ("same_twice", "TechProjectContext.projectFromTaskPayload({tech_cost:{project_id:'P5'}, tech_project_id:'P5'})", {}),
            ("conflict", "TechProjectContext.projectFromTaskPayload({tech_cost:{project_id:'P1'}, tech_project_id:'P2'})", {}),
            ("empty", "TechProjectContext.projectFromTaskPayload({})", {}),
            ("null_payload", "TechProjectContext.projectFromTaskPayload(null)", {}),
        ]))
        self.assertEqual(value_of(rows, "tech_cost")["project"], "P1")
        self.assertEqual(value_of(rows, "tech_project_id")["project"], "P2")
        self.assertEqual(value_of(rows, "flat_project_id")["project"], "P3")
        self.assertEqual(value_of(rows, "tech_result")["project"], "P4")
        self.assertEqual(value_of(rows, "same_twice")["project"], "P5", "相同值重复不算冲突")
        conflict = value_of(rows, "conflict")
        self.assertEqual(conflict["project"], "", "关联多个不同项目必须停止")
        self.assertEqual(conflict["error"], "task_ambiguous")
        self.assertTrue(conflict["message"])
        empty = value_of(rows, "empty")
        self.assertEqual(empty["project"], "")
        self.assertEqual(empty["error"], "task_no_project")
        self.assertEqual(value_of(rows, "null_payload")["project"], "")

    def test_from_task_unique_memoized_and_concurrent(self):
        rejected = ("function (e) { return { error: 'rejected', message: String(e && e.message || e) }; }")
        rows = run_jobs([
            {"name": "unique", "read": "__api", "await": True,
             "fetchResponse": fetch_doc({"tech_cost": {"project_id": "P1"}}),
             "snippet": "__api = TechProjectContext.fromTask('T1');"},
            {"name": "sequential_twice", "read": "__api", "await": True,
             "fetchResponse": fetch_doc({"tech_cost": {"project_id": "P1"}}),
             "snippet": "__api = TechProjectContext.fromTask('T1')"
                        ".then(function () { return TechProjectContext.fromTask('T1'); });"},
            {"name": "concurrent_twice", "read": "__api", "await": True,
             "fetchResponse": fetch_doc({"tech_cost": {"project_id": "P1"}}),
             "snippet": "__api = Promise.all([TechProjectContext.fromTask('T1'),"
                        " TechProjectContext.fromTask('T1')]).then(function (r) { return r[1]; });"},
            {"name": "ambiguous", "read": "__api", "await": True,
             "fetchResponse": fetch_doc({"tech_cost": {"project_id": "P1"}, "tech_project_id": "P2"}),
             "snippet": "__api = TechProjectContext.fromTask('T1').then(function (v) { return v; }, " + rejected + ");"},
            {"name": "no_project", "read": "__api", "await": True,
             "fetchResponse": fetch_doc({}),
             "snippet": "__api = TechProjectContext.fromTask('T1').then(function (v) { return v; }, " + rejected + ");"},
            {"name": "http_error", "read": "__api", "await": True, "fetchStatus": 500,
             "fetchResponse": {"detail": "boom"},
             "snippet": "__api = TechProjectContext.fromTask('T1').then(function (v) { return v; }, " + rejected + ");"},
            {"name": "network_error", "read": "__api", "await": True, "fetchFailure": "offline",
             "snippet": "__api = TechProjectContext.fromTask('T1').then(function (v) { return v; }, " + rejected + ");"},
            {"name": "with_token", "read": "__api", "await": True, "token": "TKN",
             "fetchResponse": fetch_doc({"tech_cost": {"project_id": "P1"}}),
             "snippet": "__api = TechProjectContext.fromTask('T1', {token: 'TKN'});"},
        ])
        self.assertEqual(value_of(rows, "unique")["project"], "P1", "唯一关联必须允许恢复")
        self.assertEqual(rows["unique"]["calls"][0]["url"], "/wf/task?task_id=T1",
                         "必须用既有任务接口解析项目：%r" % (rows["unique"]["calls"],))
        self.assertEqual(len(rows["sequential_twice"]["calls"]), 1,
                         "同一 task 连续调用两次只能发一次请求：%r" % (rows["sequential_twice"]["calls"],))
        self.assertEqual(value_of(rows, "sequential_twice")["project"], "P1")
        self.assertEqual(len(rows["concurrent_twice"]["calls"]), 1,
                         "同一 task 并发两次只能发一次请求：%r" % (rows["concurrent_twice"]["calls"],))
        self.assertEqual(value_of(rows, "concurrent_twice")["project"], "P1")
        ambiguous = value_of(rows, "ambiguous")
        self.assertEqual(ambiguous["error"], "rejected",
                         "关联不唯一必须停止（拒绝）而不是猜：%r" % (ambiguous,))
        self.assertIn("多个", ambiguous["message"])
        self.assertEqual(value_of(rows, "no_project")["error"], "rejected")
        self.assertEqual(value_of(rows, "http_error")["error"], "rejected", "接口失败必须可见地失败")
        self.assertEqual(value_of(rows, "network_error")["error"], "rejected", "离线必须可见地失败")
        self.assertTrue(rows["with_token"]["calls"][0]["headers"].get("Authorization"),
                        "任务解析必须带当前账号令牌（归属由服务端按账号判定）：%r"
                        % (rows["with_token"]["calls"],))


# --------------------------------------------------------------------------- #
# 4. 页面级：真实页面片段的行为
# --------------------------------------------------------------------------- #
class PageIdentityRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        jobs = []
        for file, name, extra in CONST_PAGES:
            snippet = const_snippet(read(FRONT / file), name, extra)
            if not snippet:
                snippet = "const %s = '';" % name
            base = {"file": file, "snippet": snippet, "read": name}
            jobs.append(dict(base, name="%s:no_url" % file, search="", storage=dict(STALE)))
            jobs.append(dict(base, name="%s:url_a" % file, search="?project=A", storage=dict(STALE)))
            jobs.append(dict(base, name="%s:mismatch" % file, search="?project=A&embed=1",
                             frameProject="B", storage=dict(STALE)))
        cls.rows = run_jobs(jobs)

    def snippet_rows(self):
        for file, _name, _extra in CONST_PAGES:
            self.assertIn("%s:no_url" % file, self.rows)
            yield file

    def test_no_silent_fallback_to_last_project(self):
        for file in self.snippet_rows():
            row = self.rows["%s:no_url" % file]
            self.assertTrue(row.get("ok"), "%s 的项目解析无法求值：%r" % (file, row))
            self.assertEqual(row["value"], "",
                             "%s：URL 无 project 时不得使用 localStorage 里的旧项目，实际=%r"
                             % (file, row["value"]))
            self.assertEqual(row["reads"], [],
                             "%s：项目解析不得读 localStorage，实际读取=%r" % (file, row["reads"]))

    def test_url_project_is_authoritative(self):
        for file in self.snippet_rows():
            row = self.rows["%s:url_a" % file]
            self.assertTrue(row.get("ok"), "%s 的项目解析无法求值：%r" % (file, row))
            self.assertEqual(row["value"], "A",
                             "%s：URL 项目必须优先于 localStorage，实际=%r" % (file, row["value"]))

    def test_iframe_parent_mismatch_is_refused(self):
        for file in self.snippet_rows():
            row = self.rows["%s:mismatch" % file]
            self.assertTrue(row.get("ok"), "%s 的项目解析无法求值：%r" % (file, row))
            self.assertEqual(row["value"], "",
                             "%s：iframe 项目与父壳不一致必须拒绝执行，实际=%r"
                             % (file, row["value"]))

    def test_two_tabs_do_not_cross_projects(self):
        jobs = []
        for file, name, extra in CONST_PAGES:
            snippet = const_snippet(read(FRONT / file), name, extra) or "const %s = '';" % name
            for tab, search in (("A", "?project=A"), ("B", "?project=B"), ("none", "")):
                jobs.append({"name": "%s:tab_%s" % (file, tab), "file": file, "snippet": snippet,
                             "read": name, "search": search, "storage": dict(STALE),
                             "storageGroup": "shared-browser"})
        rows = run_jobs(jobs)
        for file, _name, _extra in CONST_PAGES:
            self.assertEqual(rows["%s:tab_A" % file]["value"], "A",
                             "%s：标签页 A 必须只认自己的项目" % file)
            self.assertEqual(rows["%s:tab_B" % file]["value"], "B",
                             "%s：标签页 B 必须只认自己的项目（不得被 A 覆写的 localStorage 影响）" % file)
            self.assertEqual(rows["%s:tab_none" % file]["value"], "",
                             "%s：没有 project 的标签页不得继承别的标签页项目" % file)


def accessor_snippet(file: str):
    """提取 `projectId` 访问点，连它在文件里依赖的前置语句（如 `qs` / `qp`）一起。

    返回 `(片段, 是否函数)`：函数形态取 `projectId()`，常量形态直接取 `projectId`。
    """
    source = read(FRONT / file)
    match = re.search(r"function\s+projectId\s*\(", source)
    if match:
        start, end, callable_ = match.start(), block_end(source, match.start()), True
    else:
        match = re.search(r"(?:const|let|var)\s+projectId\s*=", source)
        if not match:
            return "", False
        start, end, callable_ = match.start(), stmt_end(source, match.start()), False
    pre = start
    pos = source.rfind("new URLSearchParams(location.search)", 0, start)
    if pos >= 0:
        pre = min(pre, stmt_start(source, pos))
    return source[pre:end].strip(), callable_


class AccessorRuntimeTest(unittest.TestCase):
    """`projectId()` 访问点（tech-embed / workflow / workflow-navigation）的行为。"""

    @classmethod
    def setUpClass(cls):
        jobs = []
        for file in ACCESSOR_PAGES:
            source, callable_ = accessor_snippet(file)
            if source:
                snippet = source + ("\nconst __p = projectId();" if callable_ else "\nconst __p = projectId;")
            else:
                snippet = "const __p = '';"
            for scenario, search in (("no_url", ""), ("url_a", "?project=A")):
                jobs.append({"name": "%s:%s" % (file, scenario), "file": file, "snippet": snippet,
                             "read": "__p", "search": search, "storage": dict(STALE)})
        cls.rows = run_jobs(jobs)

    def test_accessor_never_uses_stale_storage(self):
        for file in ACCESSOR_PAGES:
            row = self.rows["%s:no_url" % file]
            self.assertTrue(row.get("ok"), "%s 的 projectId() 无法求值：%r" % (file, row))
            self.assertEqual(row["value"], "",
                             "%s：projectId() 无 URL project 时不得退回 localStorage，实际=%r"
                             % (file, row["value"]))
            self.assertEqual(self.rows["%s:url_a" % file]["value"], "A",
                             "%s：projectId() 必须以 URL 为准" % file)


class WorkbenchParentProjectTest(unittest.TestCase):
    """父壳：URL 解析保持合规（回归守卫），并把项目下发给 iframe。"""

    @classmethod
    def setUpClass(cls):
        source = read(FRONT / "tech-workbench.js")
        snippet = range_snippet(source, "const STAGES = [", "function readFromUrl()")
        if not snippet:
            snippet = "const state = {};"
        jobs = [
            {"name": "no_url", "file": "tech-workbench.js",
             "snippet": snippet + "\nreadFromUrl();", "read": "state.project",
             "search": "?stage=drawing", "storage": dict(STALE), "accumulate": True},
            {"name": "url_a", "file": "tech-workbench.js",
             "snippet": snippet + "\nreadFromUrl();", "read": "state.project",
             "search": "?project=A&stage=drawing", "storage": dict(STALE), "accumulate": True},
            {"name": "url_a_task", "file": "tech-workbench.js",
             "snippet": snippet + "\nreadFromUrl();", "read": "state.taskId",
             "search": "?project=A&stage=cost&task_id=T9", "storage": dict(STALE), "accumulate": True},
        ]
        cls.rows = run_jobs(jobs)

    def test_state_project_from_url_only(self):
        self.assertEqual(self.rows["no_url"]["value"], "",
                         "父壳 state.project 必须只来自 URL（守卫既有合规行为）：%r"
                         % (self.rows["no_url"],))
        self.assertEqual(self.rows["url_a"]["value"], "A")
        self.assertEqual(self.rows["url_a_task"]["value"], "T9")


class UrlBuilderRuntimeTest(unittest.TestCase):
    """2.2 / 2.3 的项目级 URL 只能指向本页解析出的项目。"""

    @classmethod
    def setUpClass(cls):
        rows = {}
        for file, const_name, fn_name, suffix in (
                ("assembly-integration.js", "aiPid", "aiUrl", "/integration/params"),
                ("cost-review.js", "crPid", "crUrl", "/cost-review/parts")):
            source = read(FRONT / file)
            const_src = const_snippet(source, const_name)
            fn_src = function_snippet(source, fn_name) or ""
            if not fn_src:
                match = re.search(r"const\s+%s\s*=" % re.escape(fn_name), source)
                fn_src = const_snippet(source, fn_name)
            jobs = [
                {"name": "%s:resolved" % file, "file": file,
                 "snippet": "%s\n%s\nconst __p = %s('%s');" % (const_src, fn_src, fn_name, suffix),
                 "read": "__p", "search": "?project=A", "storage": dict(STALE)},
                {"name": "%s:empty" % file, "file": file,
                 "snippet": "%s\n%s\nconst __v = %s;" % (const_src, fn_src, const_name),
                 "read": "__v", "search": "", "storage": dict(STALE)},
            ]
            rows.update(run_jobs(jobs))
        cls.rows = rows

    def test_url_targets_resolved_project_only(self):
        for file, _c, _f, _s in (("assembly-integration.js", "aiPid", "aiUrl", ""),
                                 ("cost-review.js", "crPid", "crUrl", "")):
            row = self.rows["%s:resolved" % file]
            self.assertTrue(row.get("ok"), "%s 的 URL 构造函数无法求值：%r" % (file, row))
            self.assertIn("/api/projects/A/", row["value"],
                          "%s：URL 必须指向本页项目 A，实际=%r" % (file, row["value"]))
            self.assertNotIn("B", row["value"].split("/api/projects/")[-1].split("/")[0],
                             "%s：URL 不得指向 localStorage 里的旧项目，实际=%r" % (file, row["value"]))
            self.assertEqual(self.rows["%s:empty" % file]["value"], "",
                             "%s：无项目时项目变量必须为空（页面据此退出，不得猜项目）" % file)


# --------------------------------------------------------------------------- #
# 5. 调用点接线（静态契约：DOM / 加载顺序 / 路由）
# --------------------------------------------------------------------------- #
BAD_READ = re.compile(r"localStorage\s*(?:\.\s*getItem\s*\(\s*|\[\s*)['\"]"
                      r"(cad_engine_project_id|currentProject|lastProject)['\"]")


class CallSiteWiringTest(unittest.TestCase):
    def test_no_page_reads_identity_from_localstorage(self):
        offenders = {}
        for path in sorted(FRONT.glob("*.js")):
            text = read(path)
            found = BAD_READ.findall(text)
            if found:
                offenders[path.name] = sorted({key for key, _ in
                                               [(m, None) for m in found]} or set(found))
        self.assertEqual(offenders, {},
                         "项目身份不得再从 localStorage 读取（只允许写「最近访问」）：%r" % offenders)

    def test_shared_module_loaded_before_page_scripts(self):
        missing = []
        for html, script in BUSINESS_HTML.items():
            text = read(FRONT / html)
            if "tech-project-context.js" not in text:
                missing.append("%s 未加载 tech-project-context.js" % html)
                continue
            if text.find("tech-project-context.js") > text.find(script):
                missing.append("%s 的 tech-project-context.js 必须早于 %s" % (html, script))
            if "tech-embed.js" in text and text.find("tech-project-context.js") > text.find("tech-embed.js"):
                missing.append("%s 的 tech-project-context.js 必须早于 tech-embed.js" % html)
        self.assertEqual(missing, [], "脚本加载顺序不符合契约：%r" % missing)

    def test_pages_delegate_to_shared_context(self):
        missing = []
        for file, name, _extra in CONST_PAGES:
            text = read(FRONT / file)
            if "TechProjectContext" not in text:
                missing.append("%s 未使用共享项目解析" % file)
                continue
            snippet = const_snippet(text, name)
            if "TechProjectContext" not in snippet:
                missing.append("%s 的项目变量 %s 未委托共享解析" % (file, name))
        for file in ACCESSOR_PAGES:
            if "TechProjectContext" not in read(FRONT / file):
                missing.append("%s 未使用共享项目解析" % file)
        self.assertEqual(missing, [], "调用点没有收敛到唯一来源：%r" % missing)

    def test_pages_keep_missing_project_guard(self):
        """无项目时的既有退出/错误处理必须保留（不允许减少既有能力）。"""
        expected = {
            "assembly-integration.js", "cost-review.js", "requirement-confirm-page.js",
            "requirement-review-page.js", "requirement-detail.js", "summary-result.js",
            "report-review-result.js", "report-publish-result.js",
        }
        missing = []
        for file in sorted(expected):
            text = read(FRONT / file)
            if not re.search(r"if\s*\(\s*!\s*\w*Pid\s*\)", text):
                missing.append(file)
        self.assertEqual(missing, [], "这些页面缺少「无项目 → 明确退出」的处理：%r" % missing)

    def test_workbench_hands_project_to_iframe_and_refuses_switch(self):
        text = read(FRONT / "tech-workbench.js")
        self.assertRegex(text, r"dataset\.project|data-project|setAttribute\(\s*['\"]data-project",
                         "父壳必须把本壳 project 标在 iframe 上，供子页比对")
        handler = text[text.find("window.addEventListener('message'"):]
        handler = handler[:handler.find("\n  });") + 5] if handler else ""
        self.assertIn("TechProjectContext", handler,
                      "父壳 message 处理器必须用共享模块判定 iframe 传来的项目")
        self.assertRegex(handler, r"project_mismatch|不一致",
                         "父壳必须拒绝 iframe 传来的不同项目")

    def test_cost_review_resolves_project_from_task_when_missing(self):
        text = read(FRONT / "cost-review.js")
        self.assertIn("fromTask", text,
                      "2.3 在 URL 无 project 但有 task_id 时必须用共享模块唯一恢复项目")


if __name__ == "__main__":
    unittest.main()
