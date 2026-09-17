"""红测：长任务恢复、关键失败固定展示与统一下一步引导（批次 9）。

用户口径（批次 9 原文要点，本批只做其中三件仍有真实缺口的事）：

  9A 统一任务状态：`queued / running / succeeded / failed / interrupted / cancelled / unknown`；
     「前端轮询超时不等于任务失败」；超时 / 刷新后任务仍在任务中心、按 `task_id` 可恢复；
     服务重启后 running 转 interrupted（已实现，只做护栏）。
  9B 关键失败不得只显示 2–4 秒 Toast，必须有常驻块，含五要素：
     原因 / 影响 / 重试 / 返回正确步骤 / **错误追踪 ID**。
  9C 「安静失败」只适用于刷新类副作用；关键失败码与安静码不相交；没有码的失败默认不安静。

现状缺口（只读实测，均为行为/状态层面的缺口，不是文本搜索）：

  · `tech_app/backend/services/tasks.py` 全仓**没有 `cancelled`**：用户中途不要这个任务时没有任何合法
    收尾路径（队列里只剩排队 / 跑完 / 跑挂 / 被重启误判成「中断」）。也**没有封闭状态词表**与
    `normalize_task_status()`：词表外状态无法归一。
  · **取消之后会复活**：`_run()`（:147）在任务函数返回后无条件写 `succeeded`，任何先写入的终态都会被覆盖。
  · 全仓**没有 trace_id**：`tech_app/frontend/*.js` 搜 `trace_id/traceId/request_id/错误追踪` → 0 命中；
    `tech_app/backend/main.py` 搜 `trace` → 0 命中。任务记录里也没有 `trace_id`。
  · 关键失败只有自动消失的浮层：`workflow.js:74` 2600ms、`tech-task.js:42` 2600/4200ms、
    `assembly-integration.js:176` 3200ms、`home.js:31` 3200ms、`summary-result.js:12` 3200ms、
    `report-review-result.js:7` 3200ms、`report-publish-result.js:8` 3600ms、
    `requirement-create.js:7` / `requirement-confirm-page.js:6` / `requirement-review-page.js:5`
    3600ms、`workflow-navigation.js:46` 3600ms —— **没有任何常驻形态**。
  · 轮询只有 `while(true)` + 直接 `throw`（`assembly-integration.js:317`、`cost-review.js:254`、
    `agent-chat.js:1788`）：一次网络抖动就把长任务判成失败，没有重试、没有上限、
    没有「degraded（连接不稳定，结果仍在处理中）」这个中间态，也没有按 `task_id` 恢复的入口。
  · `QUIET_FAILURE_CODES`（`tech-board-bridge.js:114`）只在桥内部生效，页面侧没有共享口径。

本批验收全部是**行为**：后端在子进程 + 临时 DATA_DIR 里真跑 tasks / store / HTTP；
前端用 node 真跑新模块与真跑轮询。静态契约只用于「模块存在性」与「脚本加载顺序」。

Spec：docs/specs/tech-long-task-recovery-and-fixed-error-guide.md
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
SPEC = ROOT / "docs" / "specs" / "tech-long-task-recovery-and-fixed-error-guide.md"

WATCH_MODULE = FRONTEND / "tech-task-watch.js"
FAILURE_MODULE = FRONTEND / "tech-failure-banner.js"
NODE = shutil.which("node")

# 前端探针用到的追踪 ID / 项目 ID：**只在这里定义一次**，经 spec JSON / argv 传给被测方，
# 绝不在内嵌脚本里再写一份字面量（批次 6 红测踩过「两处常量不一致 → 任何实现都不可能通过」）。
TRACE_A = "0f1e2d3c4b5a6978"
TRACE_B = "aa11bb22cc33dd44"
PROBE_PROJECT = "probe9project"
PROBE_TASK = "probe9task01"
QUIET_CODE = "refresh-failed"
CRITICAL_CODES = ["permission_denied", "handoff_failed", "db_write_failed",
                  "task-failed", "interrupted", "result_stale"]


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


# --------------------------------------------------------------------------- #
# 后端：子进程 + 临时 DATA_DIR（绝不连线上 PG，也不写仓库数据）
# --------------------------------------------------------------------------- #
CHILD = r'''
import json
import os
import re
import sys
import threading
import time
import types

data_dir, root, case = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

from tech_app.backend.services import tasks
from tech_app.backend.storage import store

OUT = {"case": case, "errors": []}


def call(name, fn):
    """记录一次能力探测：缺 API 时记缺口，不让整条探针以 AttributeError 崩掉。"""
    try:
        return fn()
    except AttributeError as exc:
        OUT["errors"].append("%s: %s" % (name, exc))
        return None


def project(owner="alice", note="b9"):
    return store.create_project(source_filename="b9.png", source_bytes=b"b",
                                note=note, owner=owner)


def new_task(pid, kind="probe", **extra):
    """直接落一条 queued 任务（不经过线程池），用于取消 / 终态 / 归一化断言。"""
    rec = {"task_id": "t" + os.urandom(3).hex(), "project_id": pid, "kind": kind,
           "dedup_key": kind, "status": "queued", "progress": "排队中", "progress_log": [],
           "process_log": [], "sop_name": "探针 SOP", "sop_step": 0, "sop_total": 3,
           "created_at": "2026-09-17 10:00:00", "started_at": None, "finished_at": None,
           "result": None, "error": None}
    rec.update(extra)
    store.save_task(pid, rec)
    return rec["task_id"]


def session_rows(pid, task_id):
    rows = store.load_session_events(pid)
    return [r for r in rows if str(((r or {}).get("task") or {}).get("id") or "") == task_id]


if case == "unit":
    statuses = call("TASK_STATUSES", lambda: sorted(getattr(tasks, "TASK_STATUSES")))
    terminal = call("TERMINAL_STATUSES", lambda: sorted(getattr(tasks, "TERMINAL_STATUSES")))
    norm = getattr(tasks, "normalize_task_status", None)
    term = getattr(tasks, "is_terminal_status", None)
    cancel = getattr(tasks, "cancel_task", None)
    OUT["statuses"] = statuses
    OUT["terminal"] = terminal
    OUT["normalize"] = ({k: norm(v) for k, v in
                         (("none", None), ("empty", ""), ("nonsense", "nonsense"),
                          ("running", "running"), ("cancelled", "cancelled"))}
                        if callable(norm) else None)
    OUT["is_terminal"] = ({k: term(v) for k, v in
                           (("cancelled", "cancelled"), ("running", "running"),
                            ("nonsense", "nonsense"))} if callable(term) else None)

    pid = project()
    # 1) 排队中取消
    if callable(cancel):
        tid = new_task(pid)
        first = cancel(pid, tid, actor="alice", reason="不做了")
        second = cancel(pid, tid, actor="alice", reason="不做了")
        rec = store.get_task(pid, tid) or {}
        OUT["cancel_queued"] = {"first": first, "second": second,
                                "record": {k: rec.get(k) for k in
                                           ("status", "finished_at", "result", "dedup_key")},
                                "session": [{"key": r.get("key"),
                                             "status": (r.get("task") or {}).get("status")}
                                            for r in session_rows(pid, tid)]}
        OUT["cancel_missing"] = cancel(pid, "0" * 12, actor="alice")

        # 2) 已终态任务取消（不得改写）
        tid2 = new_task(pid)
        store.update_task(pid, tid2, status="succeeded", finished_at="2026-09-17 10:05:00",
                          result={"ok": True})
        OUT["cancel_terminal"] = {"result": cancel(pid, tid2, actor="alice"),
                                  "status": (store.get_task(pid, tid2) or {}).get("status")}

        # 3) 运行中取消 + 取消后收尾不得复活
        tid3 = new_task(pid)
        store.update_task(pid, tid3, status="running")
        OUT["cancel_running"] = {"result": cancel(pid, tid3, actor="alice"),
                                 "status": (store.get_task(pid, tid3) or {}).get("status")}
    else:
        OUT["cancel_queued"] = None
        OUT["cancel_missing"] = None
        OUT["cancel_terminal"] = None
        OUT["cancel_running"] = None

    # 3b) 真跑一个会阻塞的任务：取消后放行 fn，状态不得被 _run() 的收尾写回
    release = threading.Event()
    pid3 = project()
    tid4 = tasks.submit(pid3, "probe_blocking", lambda: (release.wait(5), {"ok": True})[1],
                        dedup_key="probe_blocking")
    for _ in range(200):
        if (store.get_task(pid3, tid4) or {}).get("status") == "running":
            break
        time.sleep(0.02)
    before = (store.get_task(pid3, tid4) or {}).get("status")
    cancelled = call("cancel_task", lambda: cancel(pid3, tid4, actor="alice", reason="取消")) \
        if callable(cancel) else None
    release.set()
    for _ in range(200):
        if (store.get_task(pid3, tid4) or {}).get("status") != before:
            break
        time.sleep(0.02)
    time.sleep(0.2)
    OUT["cancel_then_finish"] = {
        "before": before, "cancel": cancelled,
        "after": (store.get_task(pid3, tid4) or {}).get("status"),
        "result": (store.get_task(pid3, tid4) or {}).get("result")}

    # 4) 并发取消：只允许一个真正执行
    if callable(cancel):
        pid4 = project()
        tid5 = new_task(pid4)
        results = []
        lock = threading.Lock()

        def worker():
            res = cancel(pid4, tid5, actor="alice", reason="并发")
            with lock:
                results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        OUT["cancel_concurrent"] = {
            "performed": sum(1 for r in results if isinstance(r, dict)
                             and r.get("already_terminal") is False),
            "already": sum(1 for r in results if isinstance(r, dict)
                           and r.get("already_terminal") is True),
            "status": (store.get_task(pid4, tid5) or {}).get("status"),
            "session": len(session_rows(pid4, tid5))}
    else:
        OUT["cancel_concurrent"] = None

    # 5) 服务重启扫尾不得碰 cancelled
    pid5 = project()
    tid6 = new_task(pid5)
    if callable(cancel):
        cancel(pid5, tid6, actor="alice", reason="重启前已取消")
    tid7 = new_task(pid5)
    store.update_task(pid5, tid7, status="running")
    recover = getattr(tasks, "recover_interrupted_tasks", None)
    OUT["recover"] = {"recovered": call("recover", recover) if callable(recover) else None,
                      "cancelled": (store.get_task(pid5, tid6) or {}).get("status"),
                      "running": (store.get_task(pid5, tid7) or {}).get("status")}

    # 6) trace_id：入队即生成，且两个任务不相同
    pid6 = project()
    tA = tasks.submit(pid6, "probe_a", lambda: {"ok": True}, dedup_key="probe_a")
    tB = tasks.submit(pid6, "probe_b", lambda: {"ok": True}, dedup_key="probe_b")
    recA = store.get_task(pid6, tA) or {}
    recB = store.get_task(pid6, tB) or {}
    OUT["trace"] = {"a": str(recA.get("trace_id") or ""), "b": str(recB.get("trace_id") or ""),
                    "a_ok": bool(re.match(r"^[0-9a-f]{16}$", str(recA.get("trace_id") or ""))),
                    "b_ok": bool(re.match(r"^[0-9a-f]{16}$", str(recB.get("trace_id") or "")))}
    # 历史任务没有 trace_id：读取不得报错
    pid7 = project()
    legacy = new_task(pid7)
    OUT["legacy"] = {"status": (store.get_task(pid7, legacy) or {}).get("trace_id", "MISSING")}

    # 7) 重试幂等（既有能力护栏）：同一 dedup_key 在 queued/running 时复用同一 task_id
    pid8 = project()
    release2 = threading.Event()
    k1 = tasks.submit(pid8, "probe_dedup", lambda: (release2.wait(5), {"ok": True})[1],
                      dedup_key="probe_dedup")
    k2 = tasks.submit(pid8, "probe_dedup", lambda: {"ok": True}, dedup_key="probe_dedup")
    release2.set()
    OUT["dedup"] = {"same": k1 == k2, "first": k1, "second": k2,
                    "total": len([row for row in store.list_tasks(pid8)
                                  if row.get("dedup_key") == "probe_dedup"])}

elif case == "http":
    from fastapi.testclient import TestClient
    from tech_app.backend import main
    from tech_app.backend.services import cpq_sso

    ALICE = {"username": "alice", "role": "engineer", "display_name": "Alice", "user_id": "101",
             "cpq_role_code": "process_mgr", "cpq_role_name": "工艺经理", "is_system": False}
    cpq_sso.resolve = lambda token: ALICE if (token or "").strip() == "T-A" else None

    pid = project(owner="alice")
    client = TestClient(main.app)
    A = {"Authorization": "Bearer T-A"}

    def routes():
        found = []
        for route in main.app.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            if "cancel" in path:
                found.append({"path": path, "methods": sorted(methods)})
        return sorted(found, key=lambda row: row["path"])

    OUT["cancel_routes"] = routes()
    cancel_path = "/api/projects/{project_id}/tasks/{task_id}/cancel"

    # 未知任务：必须 404 且错误体带 trace_id
    r404 = client.post(cancel_path.replace("{project_id}", pid).replace("{task_id}", "0" * 12),
                       headers=A)
    OUT["cancel_404"] = {"status": r404.status_code, "body": r404.json(),
                         "header": r404.headers.get("X-Trace-Id", "")}

    # 缺项目：guard 造的 404 同样必须带 trace_id
    rguard = client.get("/api/projects/%s/attachments" % ("0" * 12), headers=A)
    OUT["guard_404"] = {"status": rguard.status_code, "body": rguard.json(),
                        "header": rguard.headers.get("X-Trace-Id", "")}

    # 正常 200 也必须带响应头
    r200 = client.get("/api/projects/%s/tasks" % pid, headers=A)
    OUT["list_200"] = {"status": r200.status_code,
                       "header": r200.headers.get("X-Trace-Id", "")}

    # 取消一条真实 queued 任务：走 HTTP 全链路
    tid = new_task(pid)
    rcancel = client.post(cancel_path.replace("{project_id}", pid).replace("{task_id}", tid),
                          headers=A)
    OUT["cancel_ok"] = {"status": rcancel.status_code, "body": rcancel.json(),
                        "task": (client.get("/api/projects/%s/tasks/%s" % (pid, tid),
                                            headers=A).json() or {})}
    # 无票：401（既有行为护栏）
    OUT["cancel_no_token"] = client.post(
        cancel_path.replace("{project_id}", pid).replace("{task_id}", tid)).status_code

    # 历史形状的任务记录（没有 trace_id）：单任务读取必须稳定返回 trace_id 空串
    tid_legacy = new_task(pid)
    OUT["legacy_task_body"] = client.get(
        "/api/projects/%s/tasks/%s" % (pid, tid_legacy), headers=A).json()

print(json.dumps(OUT, ensure_ascii=False, default=str))
'''


def run_child(case: str) -> dict:
    data_dir = tempfile.mkdtemp(prefix="cpq-b9-%s-" % case)
    script_dir = tempfile.mkdtemp(prefix="cpq-b9-script-")
    script = pathlib.Path(script_dir) / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    env = dict(os.environ)
    env["DATA_DIR"] = data_dir
    env["AUTH_ENABLED"] = "false"
    env["CPQ_SSO"] = "true"
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, str(script), data_dir, str(ROOT), case],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=180)
    if completed.returncode != 0:
        raise AssertionError("后端探针子进程失败（case=%s）：\n%s"
                             % (case, completed.stderr[-1500:]))
    return json.loads(completed.stdout.strip().splitlines()[-1])


def need(module, name):
    """缺 API 时给出明确缺口，而不是 AttributeError。"""
    value = getattr(module, name, None)
    if value is None:
        raise AssertionError(
            "缺少 %s（批次 9 Spec §7.1）：tasks.%s 是本批的统一入口" % (name, name))
    return value


# --------------------------------------------------------------------------- #
# 前端：node 真跑（不是静态文本搜索）
# --------------------------------------------------------------------------- #
DRIVER = r'''
const vm = require('vm');
const fs = require('fs');
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

function makeCtor() { return function () {}; }
const NodeCtor = makeCtor(), ElementCtor = makeCtor(), HTMLElementCtor = makeCtor();
ElementCtor.prototype = Object.create(NodeCtor.prototype);
HTMLElementCtor.prototype = Object.create(ElementCtor.prototype);

function makeNode(tag) {
  const node = {
    tagName: String(tag || 'div').toUpperCase(),
    nodeName: String(tag || 'div').toUpperCase(),
    className: '', id: '', textContent: '', innerHTML: '', value: '',
    dataset: {}, style: {}, hidden: false, children: [], parentNode: null,
    attributes: {}, _handlers: {},
    appendChild: function (child) {
      if (!child) return child;
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    append: function () {
      for (let i = 0; i < arguments.length; i += 1) {
        const kid = arguments[i];
        this.appendChild(typeof kid === 'string' ? makeNode('#text') : kid);
        if (typeof kid === 'string') this.children[this.children.length - 1].textContent = kid;
      }
    },
    insertBefore: function (child) { return this.appendChild(child); },
    removeChild: function (child) {
      const i = this.children.indexOf(child);
      if (i >= 0) { this.children.splice(i, 1); child.parentNode = null; }
      return child;
    },
    remove: function () { if (this.parentNode) this.parentNode.removeChild(this); },
    setAttribute: function (k, v) { this.attributes[String(k)] = String(v); },
    getAttribute: function (k) {
      return Object.prototype.hasOwnProperty.call(this.attributes, String(k))
        ? this.attributes[String(k)] : null;
    },
    removeAttribute: function (k) { delete this.attributes[String(k)]; },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    getElementsByTagName: function () { return []; },
    addEventListener: function (type, fn) {
      this._handlers[type] = (this._handlers[type] || []).concat([fn]);
    },
    removeEventListener: function (type, fn) {
      const list = this._handlers[type] || [];
      const i = list.indexOf(fn);
      if (i >= 0) list.splice(i, 1);
    },
    dispatchEvent: function () { return true; },
    focus: function () {}, blur: function () {}, click: function () {},
    classList: {
      add: function () {}, remove: function () {}, toggle: function () {},
      contains: function () { return false; },
    },
    scrollIntoView: function () {},
  };
  Object.setPrototypeOf(node, HTMLElementCtor.prototype);
  return node;
}

function textOf(node) {
  if (!node) return '';
  let text = '';
  if (typeof node.textContent === 'string') text += node.textContent;
  if (typeof node.innerHTML === 'string') text += ' ' + node.innerHTML;
  (node.children || []).forEach(function (kid) { text += ' ' + textOf(kid); });
  return text;
}

function safe(value) {
  try { return JSON.parse(JSON.stringify(value === undefined ? null : value)); }
  catch (error) { return String(value); }
}

async function runJob(job) {
  const out = { name: job.name, loaded: [] };
  const vtimers = { now: 0, queue: [], seq: 0 };

  function virtualSetTimeout(fn, ms) {
    const delay = Number(ms) || 0;
    const id = ++vtimers.seq;
    vtimers.queue.push({ id: id, fn: fn, at: vtimers.now + delay });
    return id;
  }
  function virtualClearTimeout(id) {
    const i = vtimers.queue.findIndex(function (row) { return row.id === id; });
    if (i >= 0) vtimers.queue.splice(i, 1);
  }
  function advance(ms) {
    const target = vtimers.now + (Number(ms) || 0);
    for (let guard = 0; guard < 10000; guard += 1) {
      const due = vtimers.queue.filter(function (row) { return row.at <= target; });
      if (!due.length) break;
      due.sort(function (a, b) { return a.at - b.at || a.id - b.id; });
      const next = due[0];
      vtimers.queue.splice(vtimers.queue.indexOf(next), 1);
      vtimers.now = next.at;
      try { next.fn(); } catch (error) {}
    }
    vtimers.now = target;
  }

  const doc = makeNode('document');
  doc.body = makeNode('body');
  doc.head = makeNode('head');
  doc.documentElement = makeNode('html');
  doc.readyState = 'complete';
  doc.title = 'probe';
  doc.createElement = function (tag) { return makeNode(tag); };
  doc.createTextNode = function (text) {
    const node = makeNode('#text');
    node.textContent = String(text);
    return node;
  };
  doc.getElementById = function () { return null; };
  doc.querySelector = function () { return null; };
  doc.querySelectorAll = function () { return []; };

  const sends = [];
  const store = Object.assign({}, job.storage || {});

  const sandbox = {
    console: { log: function () {}, warn: function () {}, error: function () {} },
    setTimeout: job.virtualTimers ? virtualSetTimeout : setTimeout,
    clearTimeout: job.virtualTimers ? virtualClearTimeout : clearTimeout,
    setInterval: function () { return 0; },
    clearInterval: function () {},
    requestAnimationFrame: function () { return 0; },
    document: doc,
    CustomEvent: function (type, init) { this.type = type; this.detail = init && init.detail; },
    Event: function (type) { this.type = type; },
    localStorage: {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
      setItem: function (k, v) { store[k] = String(v); },
      removeItem: function (k) { delete store[k]; },
      key: function (i) { return Object.keys(store)[i] || null; },
    },
    sessionStorage: {
      getItem: function () { return null; }, setItem: function () {}, removeItem: function () {},
    },
    location: {
      href: 'http://localhost' + (job.pathname || '/index.html'),
      origin: 'http://localhost',
      pathname: job.pathname || '/index.html',
      search: job.search || '', hash: '',
      reload: function () {}, replace: function () {}, assign: function () {},
    },
    history: {
      pushState: function () {}, replaceState: function () {},
      back: function () {}, forward: function () {},
    },
    navigator: { userAgent: 'probe9' },
    URLSearchParams: URLSearchParams, URL: URL, Response: Response, Headers: Headers,
    Node: NodeCtor, Element: ElementCtor, HTMLElement: HTMLElementCtor,
    fetch: function (url, opts) {
      sends.push({ url: String(url), method: String((opts && opts.method) || 'GET') });
      return Promise.resolve({
        ok: true, status: 200, headers: { get: function () { return null; } },
        json: function () { return Promise.resolve({}); },
        text: function () { return Promise.resolve(''); },
      });
    },
    __textOf: textOf,
    __clock: { advance: advance, now: function () { return vtimers.now; },
               pending: function () { return vtimers.queue.length; } },
    __harness: {
      sends: sends,
      sleep: function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); },
    },
  };
  const win = makeNode('window');
  sandbox.addEventListener = win.addEventListener;
  sandbox.removeEventListener = win.removeEventListener;
  sandbox.dispatchEvent = win.dispatchEvent;
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.top = sandbox;
  sandbox.parent = sandbox;
  Object.keys(job.consts || {}).forEach(function (key) { sandbox[key] = job.consts[key]; });

  vm.createContext(sandbox);

  (job.modules || []).forEach(function (file) {
    if (!file || !fs.existsSync(file)) {
      out.loaded.push({ file: file, ok: false, missing: true });
      return;
    }
    try {
      vm.runInContext(fs.readFileSync(file, 'utf8'), sandbox, { filename: file });
      out.loaded.push({ file: file, ok: true });
    } catch (error) {
      out.loaded.push({ file: file, ok: false, error: String(error && error.message) });
    }
  });

  try {
    const fn = vm.runInContext('(async function(){\n' + job.script + '\n})', sandbox,
                               { filename: job.name });
    out.value = safe(await fn());
    out.ok = true;
  } catch (error) {
    out.ok = false;
    out.error = String((error && error.constructor && error.constructor.name) || 'Error') +
      ': ' + String(error && error.message);
  }
  out.sends = sends;
  out.loadedFiles = out.loaded;
  return out;
}

(async function main() {
  const results = [];
  for (const job of spec.jobs) results.push(await runJob(job));
  process.stdout.write(JSON.stringify(results));
})();
'''

CONSTS = {"TRACE_A": TRACE_A, "TRACE_B": TRACE_B, "PROBE_PROJECT": PROBE_PROJECT,
          "PROBE_TASK": PROBE_TASK, "QUIET_CODE": QUIET_CODE}


def run_jobs(jobs: list) -> dict:
    """真跑 node。模块缺失不提前报错：让每个用例自己暴露真实行为。"""
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端行为走查")
    with tempfile.TemporaryDirectory(prefix="cpq-b9-spec-") as tmp:
        spec_path = pathlib.Path(tmp) / "spec.json"
        driver_path = pathlib.Path(tmp) / "driver.js"
        spec_path.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8")
        driver_path.write_text(DRIVER, encoding="utf-8")
        completed = subprocess.run([NODE, str(driver_path), str(spec_path)],
                                   capture_output=True, text=True, timeout=180)
        if completed.returncode != 0:
            raise AssertionError("node 走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2000:],
                                    completed.stderr[-2000:]))
        rows = json.loads(completed.stdout.strip().splitlines()[-1])
        return {row["name"]: row for row in rows}


def job(name, script, **kw):
    base = {"name": name, "script": script, "consts": dict(CONSTS)}
    base.update(kw)
    return base


class NodeCase(unittest.TestCase):
    """公共断言：node 跑出 ok=false 时给出可读原因（不是 ERROR）。"""

    def ok_row(self, rows, name):
        row = rows.get(name)
        self.assertIsNotNone(row, "node 没有返回 %s 的结果" % name)
        self.assertTrue(row.get("ok"), "%s 脚本执行失败：%s" % (name, row.get("error")))
        return row

    def value(self, rows, name):
        return self.ok_row(rows, name)["value"]

    def require_module(self, rows, name, module, hint):
        value = self.value(rows, name)
        self.assertTrue(value.get("present"), hint)
        return value


# --------------------------------------------------------------------------- #
# Spec 钉死
# --------------------------------------------------------------------------- #
class SpecPinnedTest(unittest.TestCase):
    def test_spec_exists_and_pins_contract(self):
        self.assertTrue(SPEC.exists(), "缺少 Spec：%s" % SPEC)
        text = SPEC.read_text(encoding="utf-8")
        for token in ("TASK_STATUSES", "TERMINAL_STATUSES", "cancel_task",
                      "normalize_task_status", "cancelled", "unknown",
                      "trace_id", "X-Trace-Id", "TechTaskWatch", "TechFailure",
                      "CRITICAL_FAILURE_CODES", "QUIET_FAILURE_CODES",
                      "degraded", "轮询超时", "错误追踪 ID", "返回正确步骤"):
            self.assertIn(token, text, "Spec 必须钉死 %s" % token)


class ProbeConstantSelfCheckTest(unittest.TestCase):
    """护栏：探针常量只允许有一处字面量定义（批次 6 红测曾两处不一致 → 任何实现都不可能通过）。"""

    def test_probe_constants_have_single_literal(self):
        text = read(pathlib.Path(__file__))
        for value in (TRACE_A, TRACE_B):
            count = text.count('"%s"' % value)
            self.assertEqual(1, count,
                             "常量 %s 在红测里出现了 %d 次字面量；必须只在顶部定义一次，"
                             "经 consts / argv 传给被测方" % (value, count))


# --------------------------------------------------------------------------- #
# 后端
# --------------------------------------------------------------------------- #
_CACHE: dict = {}


def child(case: str) -> dict:
    if case not in _CACHE:
        _CACHE[case] = run_child(case)
    return _CACHE[case]


class BackendStatusVocabularyTest(unittest.TestCase):
    """任务的统一状态词表：补 cancelled / unknown，并给出归一化入口。"""

    def test_status_vocabulary_is_closed_and_covers_cancelled(self):
        out = child("unit")
        self.assertIsNotNone(out["statuses"],
                             "缺少 tasks.TASK_STATUSES（批次 9 Spec §7.1）")
        self.assertIsNotNone(out["terminal"],
                             "缺少 tasks.TERMINAL_STATUSES（批次 9 Spec §7.1）")
        for code in ("queued", "running", "succeeded", "partial", "failed",
                     "interrupted", "cancelled", "unknown"):
            self.assertIn(code, out["statuses"], "任务状态词表必须含 %s" % code)
        for code in ("succeeded", "partial", "failed", "interrupted", "cancelled"):
            self.assertIn(code, out["terminal"], "终态词表必须含 %s" % code)
        for code in ("queued", "running"):
            self.assertNotIn(code, out["terminal"], "%s 不是终态" % code)

    def test_normalize_and_is_terminal(self):
        out = child("unit")
        norm = out["normalize"]
        self.assertIsNotNone(norm, "缺少 tasks.normalize_task_status()（Spec §7.1）")
        self.assertEqual("unknown", norm["nonsense"], "词表外状态必须归一成 unknown")
        self.assertEqual("unknown", norm["empty"], "空状态必须归一成 unknown")
        self.assertEqual("unknown", norm["none"], "None 状态必须归一成 unknown")
        self.assertEqual("running", norm["running"], "合法状态不得被改写")
        self.assertEqual("cancelled", norm["cancelled"], "cancelled 必须被认成合法状态")
        term = out["is_terminal"]
        self.assertIsNotNone(term, "缺少 tasks.is_terminal_status()（Spec §7.1）")
        self.assertTrue(term["cancelled"], "cancelled 是终态")
        self.assertFalse(term["running"], "running 不是终态")
        self.assertFalse(term["nonsense"], "词表外状态不得算终态")


class CancelTaskContractTest(unittest.TestCase):
    def test_cancel_queued_task_is_terminal_and_traced(self):
        out = child("unit")
        data = out["cancel_queued"]
        self.assertTrue(data, "缺少 tasks.cancel_task()（批次 9 Spec §7.1）")
        first = data["first"] or {}
        self.assertTrue(first.get("ok"), "取消排队中的任务必须成功：%s" % first)
        self.assertEqual("cancelled", first.get("status"), "取消后状态必须是 cancelled")
        self.assertFalse(first.get("already_terminal"), "第一次取消不是 already_terminal")
        self.assertEqual("cancelled", data["record"]["status"], "落库状态必须是 cancelled")
        self.assertTrue(data["record"]["finished_at"], "取消必须写 finished_at（终态要有收尾时间）")
        self.assertIsNone(data["record"]["result"], "取消不得写结果")
        self.assertEqual([{"key": "task:%s" % first.get("task_id"), "status": "cancelled"}],
                         data["session"],
                         "取消必须写会话时间线（key=task:<id>，status=cancelled）")

    def test_cancel_is_idempotent_for_terminal_tasks(self):
        out = child("unit")
        data = out["cancel_queued"]
        self.assertTrue(data, "缺少 tasks.cancel_task()（批次 9 Spec §7.1）")
        second = data["second"] or {}
        self.assertTrue(second.get("ok"), "重复取消必须是幂等成功：%s" % second)
        self.assertTrue(second.get("already_terminal"), "第二次取消必须报 already_terminal")
        self.assertEqual("cancelled", second.get("status"), "幂等返回原状态")
        self.assertEqual([{"key": "task:%s" % data["first"].get("task_id"), "status": "cancelled"}],
                         data["session"], "重复取消不得新增第二条会话时间线")
        done = out["cancel_terminal"]
        self.assertTrue(done, "缺少 tasks.cancel_task()")
        self.assertTrue((done["result"] or {}).get("already_terminal"),
                        "已完成任务再取消必须报 already_terminal")
        self.assertEqual("succeeded", done["status"], "已完成任务不得被改成 cancelled")

    def test_cancel_running_task_and_missing_task(self):
        out = child("unit")
        running = out["cancel_running"]
        self.assertTrue(running, "缺少 tasks.cancel_task()")
        self.assertEqual("cancelled", (running["result"] or {}).get("status"))
        self.assertEqual("cancelled", running["status"], "运行中的任务取消后状态是 cancelled")
        missing = out["cancel_missing"] or {}
        self.assertFalse(missing.get("ok"), "取消不存在的任务必须失败：%s" % missing)
        self.assertEqual("not_found", missing.get("reason"), "失败原因必须是 not_found")

    def test_cancelled_task_is_not_resurrected_by_task_finish(self):
        """取消之后任务函数才收尾：不得把 cancelled 改回 succeeded。"""
        out = child("unit")
        data = out["cancel_then_finish"]
        self.assertEqual("running", data["before"], "探针没等到任务进入 running")
        self.assertEqual("cancelled", data["after"],
                         "任务函数在取消后收尾，不得覆盖 cancelled 终态（Spec §6.2 第 6 条）")
        self.assertIsNone(data["result"], "取消后到达的结果必须丢弃")

    def test_concurrent_cancel_only_one_performs(self):
        out = child("unit")
        data = out["cancel_concurrent"]
        self.assertTrue(data, "缺少 tasks.cancel_task()（批次 9 Spec §7.1 / §10）")
        self.assertEqual(1, data["performed"], "两个线程并发取消只允许一个真正执行")
        self.assertEqual(1, data["already"], "另一个必须返回 already_terminal")
        self.assertEqual("cancelled", data["status"], "最终状态唯一")
        self.assertEqual(1, data["session"], "并发取消只得出一条会话时间线")


class RecoverKeepsCancelledTest(unittest.TestCase):
    def test_recovery_does_not_touch_cancelled(self):
        out = child("unit")
        data = out["recover"]
        self.assertIsNotNone(data, "缺少 tasks.recover_interrupted_tasks()（既有能力）")
        self.assertEqual("cancelled", data["cancelled"],
                         "服务重启扫尾不得把已取消的任务改成 interrupted")
        self.assertEqual("interrupted", data["running"],
                         "在途任务仍按既有口径转 interrupted（既有能力护栏）")
        self.assertGreaterEqual(int(data["recovered"] or 0), 1,
                                "扫尾必须至少处理那一条 running 任务")


class TraceIdBackendTest(unittest.TestCase):
    def test_task_record_carries_trace_id(self):
        out = child("unit")
        trace = out["trace"]
        self.assertIsNotNone(trace, "tasks.submit() 必须给任务生成 trace_id（Spec §7.2）")
        self.assertTrue(trace["a_ok"], "入队时必须生成 trace_id（16 位小写 hex）：%r" % trace["a"])
        self.assertTrue(trace["b_ok"], "第二个任务同样要有 trace_id：%r" % trace["b"])
        self.assertNotEqual(trace["a"], trace["b"], "不同任务不得共用同一个 trace_id")

    def test_legacy_task_without_trace_id_is_still_readable(self):
        """历史任务没有 trace_id：读取不得报错，且形状稳定（空串）。"""
        out = child("unit")
        self.assertIsNotNone(out["legacy"], "历史任务读取不得报错（Spec §13）")
        http_body = child("http").get("legacy_task_body") or {}
        self.assertTrue("trace_id" in http_body,
                        "GET 单任务必须稳定返回 trace_id 字段（历史任务为空串），实际键：%s"
                        % sorted(http_body.keys()))
        self.assertEqual("", http_body.get("trace_id"),
                         "历史任务没有 trace_id 时必须返回空串，不报错（Spec §7.2 / §13）")


class ExistingCapabilityGuardTest(unittest.TestCase):
    """批次 9 要求「重试沿用业务幂等键」—— 这是既有能力，本批只做护栏，不新增第二套机制。"""

    def test_retry_reuses_business_dedup_key(self):
        out = child("unit")
        data = out["dedup"]
        self.assertIsNotNone(data, "缺少 tasks.submit(dedup_key=...)（既有能力）")
        self.assertTrue(data["same"],
                        "同一 dedup_key 的并发重试必须复用同一个 task_id（不得起第二份）：%s" % data)
        self.assertEqual(1, data["total"], "同一 dedup_key 只允许存在一条任务记录：%s" % data)


class HttpCancelAndTraceTest(unittest.TestCase):
    def test_cancel_route_exists(self):
        out = child("http")
        rows = out["cancel_routes"] or []
        paths = [row["path"] for row in rows]
        self.assertIn("/api/projects/{project_id}/tasks/{task_id}/cancel", paths,
                      "缺少取消任务路由（批次 9 Spec §7.3）：%s" % out["cancel_routes"])
        row = [r for r in rows
               if r["path"] == "/api/projects/{project_id}/tasks/{task_id}/cancel"][0]
        self.assertIn("POST", row["methods"], "取消必须是 POST：%s" % row)

    def test_cancel_over_http_is_terminal(self):
        out = child("http")
        ok = out["cancel_ok"]
        self.assertIsNotNone(ok, "缺少取消任务路由（批次 9 Spec §7.3）")
        self.assertEqual(200, ok["status"], "取消已存在的任务必须 200，实际 %s" % ok["status"])
        self.assertEqual("cancelled", (ok["body"] or {}).get("status"))
        self.assertEqual("cancelled", (ok["task"] or {}).get("status"),
                         "取消后 GET 单任务必须看到 cancelled")
        self.assertEqual(401, out["cancel_no_token"], "没票取消必须 401（既有鉴权护栏）")

    def test_missing_task_cancel_is_404(self):
        out = child("http")
        data = out["cancel_404"]
        self.assertIsNotNone(data, "缺少取消任务路由（批次 9 Spec §7.3）")
        self.assertEqual(404, data["status"], "取消不存在的任务必须 404：%s" % data)

    def test_every_response_carries_trace_header(self):
        out = child("http")
        for key in ("cancel_404", "guard_404", "list_200"):
            self.assertIsNotNone(out[key], "缺少 %s 探针结果" % key)
            header = out[key]["header"]
            self.assertRegex(header, r"^[0-9a-f]{16}$",
                             "%s 的响应必须带 X-Trace-Id（16 位小写 hex），实际 %r"
                             % (key, header))

    def test_error_body_trace_id_matches_header(self):
        out = child("http")
        for key in ("cancel_404", "guard_404"):
            data = out[key]
            self.assertIsNotNone(data, "缺少 %s 探针结果" % key)
            body = data["body"] or {}
            self.assertEqual(data["header"], body.get("trace_id"),
                             "%s 的错误体 trace_id 必须与响应头逐字相同：%s" % (key, data))


# --------------------------------------------------------------------------- #
# 前端：TechTaskWatch
# --------------------------------------------------------------------------- #
SCRIPT_CONTRACT = r"""
const W = window.TechTaskWatch;
if (!W) { return { present: false }; }
function probe(fn) { try { return String(fn()); } catch (error) { return 'ERR:' + String(error && error.message); } }
return {
  present: true,
  fns_missing: ['watch', 'recover', 'list'].filter(function (k) { return typeof W[k] !== 'function'; }),
  statuses: W.STATUSES || null,
  terminal: W.TERMINAL || null,
  normalize: {
    nonsense: probe(function () { return W.normalizeStatus('nonsense'); }),
    empty: probe(function () { return W.normalizeStatus(''); }),
    missing: probe(function () { return W.normalizeStatus(undefined); }),
    cancelled: probe(function () { return W.normalizeStatus('cancelled'); }),
  },
  is_terminal: {
    cancelled: probe(function () { return W.isTerminal('cancelled'); }),
    running: probe(function () { return W.isTerminal('running'); }),
    nonsense: probe(function () { return W.isTerminal('nonsense'); }),
  },
  words: {
    succeeded: probe(function () { return W.statusWord('succeeded'); }),
    partial: probe(function () { return W.statusWord('partial'); }),
    failed: probe(function () { return W.statusWord('failed'); }),
    interrupted: probe(function () { return W.statusWord('interrupted'); }),
    cancelled: probe(function () { return W.statusWord('cancelled'); }),
    unknown: probe(function () { return W.statusWord('unknown'); }),
  },
};
"""

SCRIPT_TRANSIENT_RECOVER = r"""
const W = window.TechTaskWatch;
if (!W) { return { present: false }; }
function ok(body) {
  return Promise.resolve({ ok: true, status: 200, headers: { get: function () { return null; } },
    json: function () { return Promise.resolve(body); },
    text: function () { return Promise.resolve(''); } });
}
let calls = 0, succeed = false;
const impl = function () {
  calls += 1;
  if (!succeed) return Promise.reject(new Error('network down'));
  return ok({ status: 'succeeded', result: { ok: true } });
};
const w = W.watch({ projectId: PROBE_PROJECT, taskId: PROBE_TASK, fetchImpl: impl,
                    intervalMs: 1, degradedIntervalMs: 1, transientLimit: 40 });
let settled = 'pending';
w.promise.then(function () { settled = 'resolved'; }, function () { settled = 'rejected'; });
await new Promise(function (r) { setTimeout(r, 25); });
const mid = { state: w.state(), settled: settled, calls: calls };
succeed = true;
let record = null, failure = null;
try { record = await w.promise; } catch (error) { failure = String((error && error.code) || (error && error.message)); }
await new Promise(function (r) { setTimeout(r, 5); });
return { present: true, mid: mid, settled_after: settled, failure: failure,
         status: record && record.status, ok_flag: record && record.ok,
         final_state: w.state(), calls: calls };
"""

SCRIPT_DEGRADED = r"""
const W = window.TechTaskWatch;
if (!W) { return { present: false }; }
let calls = 0;
const impl = function () { calls += 1; return Promise.reject(new Error('network down')); };
const w = W.watch({ projectId: PROBE_PROJECT, taskId: PROBE_TASK, fetchImpl: impl,
                    intervalMs: 1, degradedIntervalMs: 1, transientLimit: 3 });
let settled = 'pending', rejected = null;
w.promise.then(function () { settled = 'resolved'; },
               function (error) { settled = 'rejected'; rejected = String(error && error.code); });
await new Promise(function (r) { setTimeout(r, 40); });
const mid = { state: w.state(), settled: settled, rejected: rejected, calls: calls };
w.abort();
await new Promise(function (r) { setTimeout(r, 10); });
return { present: true, mid: mid, after_abort: { settled: settled, rejected: rejected } };
"""

SCRIPT_TERMINAL_CODES = r"""
const W = window.TechTaskWatch;
if (!W) { return { present: false }; }
function impl(body) {
  return function () {
    return Promise.resolve({ ok: true, status: 200, headers: { get: function () { return null; } },
      json: function () { return Promise.resolve(body); },
      text: function () { return Promise.resolve(''); } });
  };
}
async function once(body) {
  const w = W.watch({ projectId: PROBE_PROJECT, taskId: PROBE_TASK, fetchImpl: impl(body),
                      intervalMs: 1 });
  try {
    const record = await w.promise;
    return { settled: 'resolved', status: record && record.status, ok_flag: record && record.ok };
  } catch (error) {
    return { settled: 'rejected', code: String(error && error.code),
             status: String(error && error.status),
             message: String(error && error.message),
             trace_id: String((error && error.trace_id) || (error && error.traceId) || '') };
  }
}
return {
  present: true,
  failed: await once({ status: 'failed', error: '模型调用失败', trace_id: TRACE_A }),
  interrupted: await once({ status: 'interrupted', error: '服务重启中断', trace_id: TRACE_A }),
  cancelled: await once({ status: 'cancelled', error: '用户取消', trace_id: TRACE_A }),
  succeeded: await once({ status: 'succeeded', result: { ok: true }, trace_id: TRACE_A }),
  partial: await once({ status: 'partial', result: { ok: true }, trace_id: TRACE_A }),
};
"""

SCRIPT_HTTP_CODES = r"""
const W = window.TechTaskWatch;
if (!W) { return { present: false }; }
function impl(status, body, traceHeader) {
  return function () {
    return Promise.resolve({
      ok: status < 400, status: status,
      headers: { get: function (name) {
        return String(name || '').toLowerCase() === 'x-trace-id' ? (traceHeader || null) : null; } },
      json: function () { return Promise.resolve(body); },
      text: function () { return Promise.resolve(''); } });
  };
}
async function once(call) {
  const w = W.watch({ projectId: PROBE_PROJECT, taskId: PROBE_TASK, fetchImpl: call, intervalMs: 1 });
  try {
    await w.promise;
    return { settled: 'resolved' };
  } catch (error) {
    return { settled: 'rejected', code: String(error && error.code),
             trace_id: String((error && error.trace_id) || (error && error.traceId) || ''),
             status: String(error && error.status) };
  }
}
return {
  present: true,
  permission_body: await once(impl(403, { detail: '无权限', trace_id: TRACE_A })),
  permission_header: await once(impl(403, { detail: '无权限' }, TRACE_B)),
  not_found: await once(impl(404, { detail: '任务不存在' })),
  rejected_4xx: await once(impl(422, { detail: '参数不合法' })),
};
"""

SCRIPT_ABORT = r"""
const W = window.TechTaskWatch;
if (!W) { return { present: false }; }
const calls = [];
const impl = function (url, opts) {
  calls.push({ url: String(url), method: String((opts && opts.method) || 'GET') });
  return new Promise(function () {});
};
const w = W.watch({ projectId: PROBE_PROJECT, taskId: PROBE_TASK, fetchImpl: impl, intervalMs: 5 });
let first = null;
w.promise.catch(function (error) { first = String(error && error.code); });
await new Promise(function (r) { setTimeout(r, 15); });
w.abort();
await new Promise(function (r) { setTimeout(r, 10); });
w.abort();
await new Promise(function (r) { setTimeout(r, 10); });
return { present: true, first: first, calls: calls.length,
         writes: calls.filter(function (c) { return c.method !== 'GET'; }).length };
"""

SCRIPT_RECOVER_LIST = r"""
const W = window.TechTaskWatch;
if (!W) { return { present: false }; }
function ok(body) {
  return Promise.resolve({ ok: true, status: 200, headers: { get: function () { return null; } },
    json: function () { return Promise.resolve(body); },
    text: function () { return Promise.resolve(''); } });
}
function http(status, body) {
  return Promise.resolve({ ok: false, status: status, headers: { get: function () { return null; } },
    json: function () { return Promise.resolve(body); },
    text: function () { return Promise.resolve(''); } });
}
const impl = function (url) {
  const text = String(url);
  if (text.indexOf('/tasks/recover1') >= 0) {
    return ok({ task_id: 'recover1', status: 'running', trace_id: TRACE_A });
  }
  if (text.indexOf('/tasks/') >= 0) return http(404, { detail: '任务不存在' });
  return ok({ tasks: [{ task_id: 'recover1', status: 'running' },
                      { task_id: 'recover2', status: 'cancelled' }] });
};
const listed = await W.list(PROBE_PROJECT, impl);
const one = await W.recover(PROBE_PROJECT, 'recover1', impl);
const missing = await W.recover(PROBE_PROJECT, 'nope', impl);
return { present: true,
         listed_is_array: Array.isArray(listed),
         listed_len: Array.isArray(listed) ? listed.length : -1,
         second_status: Array.isArray(listed) && listed[1] ? String(listed[1].status) : '',
         one_id: one && one.task_id,
         one_trace: one && one.trace_id,
         missing_is_null: missing === null };
"""


class WatchContractTest(NodeCase):
    def test_module_and_contract(self):
        rows = run_jobs([job("contract", SCRIPT_CONTRACT, modules=[str(WATCH_MODULE)])])
        value = self.require_module(
            rows, "contract", WATCH_MODULE,
            "缺少 tech_app/frontend/tech-task-watch.js（批次 9 Spec §7.4）："
            "window.TechTaskWatch 是长任务轮询 / 恢复的唯一入口")
        self.assertEqual([], value["fns_missing"],
                         "TechTaskWatch 缺少这些方法：%s" % value["fns_missing"])
        for code in ("queued", "running", "succeeded", "partial", "failed",
                     "interrupted", "cancelled", "unknown"):
            self.assertIn(code, value["statuses"] or [], "STATUSES 必须含 %s" % code)
        for code in ("succeeded", "partial", "failed", "interrupted", "cancelled"):
            self.assertIn(code, value["terminal"] or [], "TERMINAL 必须含 %s" % code)

    def test_normalize_and_words(self):
        rows = run_jobs([job("contract", SCRIPT_CONTRACT, modules=[str(WATCH_MODULE)])])
        value = self.require_module(
            rows, "contract", WATCH_MODULE,
            "缺少 tech_app/frontend/tech-task-watch.js（批次 9 Spec §7.4）："
            "window.TechTaskWatch 是长任务轮询 / 恢复的唯一入口")
        self.assertEqual("unknown", value["normalize"]["nonsense"])
        self.assertEqual("unknown", value["normalize"]["empty"])
        self.assertEqual("unknown", value["normalize"]["missing"])
        self.assertEqual("cancelled", value["normalize"]["cancelled"])
        self.assertEqual("true", value["is_terminal"]["cancelled"])
        self.assertEqual("false", value["is_terminal"]["running"])
        self.assertEqual("false", value["is_terminal"]["nonsense"])
        self.assertEqual("已完成", value["words"]["succeeded"])
        self.assertEqual("部分完成", value["words"]["partial"])
        self.assertEqual("失败", value["words"]["failed"])
        self.assertEqual("中断", value["words"]["interrupted"])
        self.assertEqual("已取消", value["words"]["cancelled"])
        self.assertEqual("状态未知", value["words"]["unknown"])


class WatchPollBehaviourTest(NodeCase):
    def test_transient_failures_do_not_fail_the_task(self):
        rows = run_jobs([job("recover", SCRIPT_TRANSIENT_RECOVER, modules=[str(WATCH_MODULE)])])
        value = self.require_module(rows, "recover", WATCH_MODULE,
                                    "缺少 tech_app/frontend/tech-task-watch.js（Spec §7.4）")
        mid = value["mid"]
        self.assertEqual("pending", mid["settled"],
                         "瞬时故障期间 promise 不得结算（轮询超时 ≠ 任务失败）：%s" % mid)
        self.assertGreaterEqual(int(mid["state"]["transient_failures"] or 0), 1,
                                "瞬时故障必须被计数：%s" % mid["state"])
        self.assertFalse(mid["state"]["degraded"],
                         "未达到 transientLimit 时不得进入 degraded：%s" % mid["state"])
        self.assertEqual("resolved", value["settled_after"],
                         "连接恢复后必须正常结算，而不是判成失败：%s" % value)
        self.assertEqual("succeeded", value["status"])
        self.assertTrue(value["ok_flag"], "成功结算必须带 ok=true")
        self.assertEqual(0, int(value["final_state"]["transient_failures"] or 0),
                         "连续成功一次后瞬时故障计数必须清零：%s" % value["final_state"])

    def test_repeated_transient_failures_degrade_without_settling(self):
        rows = run_jobs([job("degraded", SCRIPT_DEGRADED, modules=[str(WATCH_MODULE)])])
        value = self.require_module(rows, "degraded", WATCH_MODULE,
                                    "缺少 tech_app/frontend/tech-task-watch.js（Spec §7.4）")
        mid = value["mid"]
        self.assertTrue(mid["state"]["degraded"],
                        "连续瞬时故障达到 transientLimit 必须进入 degraded：%s" % mid["state"])
        self.assertEqual("unknown", mid["state"]["status"],
                         "首轮成功前状态必须是 unknown：%s" % mid["state"])
        self.assertEqual("pending", mid["settled"],
                         "degraded 不是失败：promise 必须仍然悬挂（Spec §9.2）：%s" % value)
        self.assertEqual("aborted", value["after_abort"]["rejected"],
                         "abort() 必须给出 aborted：%s" % value["after_abort"])

    def test_terminal_statuses_reject_with_explicit_codes(self):
        rows = run_jobs([job("terminal", SCRIPT_TERMINAL_CODES, modules=[str(WATCH_MODULE)])])
        value = self.require_module(rows, "terminal", WATCH_MODULE,
                                    "缺少 tech_app/frontend/tech-task-watch.js（Spec §7.4）")
        self.assertEqual("rejected", value["failed"]["settled"], "failed 必须明确失败")
        self.assertEqual("task-failed", value["failed"]["code"])
        self.assertEqual(TRACE_A, value["failed"]["trace_id"], "失败必须带追踪 ID")
        self.assertEqual("interrupted", value["interrupted"]["code"],
                         "interrupted 必须走独立码，不能被吞成 task-failed")
        self.assertEqual("cancelled", value["cancelled"]["code"],
                         "cancelled 必须走独立码")
        self.assertEqual("resolved", value["succeeded"]["settled"])
        self.assertEqual("succeeded", value["succeeded"]["status"])
        self.assertEqual("resolved", value["partial"]["settled"],
                         "partial 是终态的一种，必须结算而不是失败")
        self.assertEqual("partial", value["partial"]["status"])

    def test_http_failures_are_classified(self):
        rows = run_jobs([job("http", SCRIPT_HTTP_CODES, modules=[str(WATCH_MODULE)])])
        value = self.require_module(rows, "http", WATCH_MODULE,
                                    "缺少 tech_app/frontend/tech-task-watch.js（Spec §7.4）")
        self.assertEqual("permission_denied", value["permission_body"]["code"],
                         "403 必须归成 permission_denied")
        self.assertEqual(TRACE_A, value["permission_body"]["trace_id"],
                         "403 必须从错误体拿到追踪 ID")
        self.assertEqual(TRACE_B, value["permission_header"]["trace_id"],
                         "错误体没有追踪 ID 时必须退回读 X-Trace-Id 响应头")
        self.assertEqual("task-not-found", value["not_found"]["code"], "404 归成 task-not-found")
        self.assertEqual("request-rejected", value["rejected_4xx"]["code"],
                         "其它 4xx 归成 request-rejected（不得当成瞬时故障无限轮询）")

    def test_abort_stops_polling_without_writing(self):
        rows = run_jobs([job("abort", SCRIPT_ABORT, modules=[str(WATCH_MODULE)])])
        value = self.require_module(rows, "abort", WATCH_MODULE,
                                    "缺少 tech_app/frontend/tech-task-watch.js（Spec §7.4）")
        self.assertEqual("aborted", value["first"], "abort() 必须以 aborted 结算")
        self.assertEqual(0, value["writes"], "轮询/中止不得发出任何写请求")

    def test_recover_and_list(self):
        rows = run_jobs([job("recover", SCRIPT_RECOVER_LIST, modules=[str(WATCH_MODULE)])])
        value = self.require_module(rows, "recover", WATCH_MODULE,
                                    "缺少 tech_app/frontend/tech-task-watch.js（Spec §7.4）")
        self.assertTrue(value["listed_is_array"], "list() 必须返回数组")
        self.assertEqual(2, value["listed_len"], "刷新后任务中心必须能列出任务：%s" % value)
        self.assertEqual("cancelled", value["second_status"], "任务状态原样透出")
        self.assertEqual("recover1", value["one_id"], "recover() 必须按 task_id 取回记录")
        self.assertEqual(TRACE_A, value["one_trace"], "取回的记录必须带追踪 ID")
        self.assertTrue(value["missing_is_null"], "recover() 对 404 返回 null，不抛异常")


# --------------------------------------------------------------------------- #
# 前端：TechFailure（安静口径 + 常驻失败块 + 追踪 ID）
# --------------------------------------------------------------------------- #
SCRIPT_POLICY = r"""
const F = window.TechFailure;
if (!F) { return { present: false }; }
function probe(fn) { try { return String(fn()); } catch (error) { return 'ERR:' + String(error && error.message); } }
const critical = F.CRITICAL_FAILURE_CODES || [];
const quiet = F.QUIET_FAILURE_CODES || [];
return {
  present: true,
  critical: critical,
  quiet: quiet,
  overlap: critical.filter(function (code) { return quiet.indexOf(code) >= 0; }),
  quiet_for_critical: critical.map(function (code) { return [code, probe(function () { return F.isQuiet({ code: code }); })]; }),
  quiet_member: quiet.length ? probe(function () { return F.isQuiet({ code: quiet[0] }); }) : null,
  quiet_nocode: probe(function () { return F.isQuiet({}); }),
  quiet_null: probe(function () { return F.isQuiet(null); }),
  quiet_explicit: probe(function () { return F.isQuiet({ code: 'nonsense-code', quiet: true }); }),
  quiet_non_refresh: quiet.filter(function (code) {
    return code.indexOf('refresh-') !== 0 &&
      ['detached', 'no-selection', 'note-target-missing', 'missing-comment'].indexOf(code) < 0;
  }),
};
"""

SCRIPT_DESCRIBE = r"""
const F = window.TechFailure;
if (!F) { return { present: false }; }
if (typeof F.describe !== 'function') { return { present: true, describe_missing: true }; }
const full = F.describe({ code: 'handoff_failed', message: '回传报价失败：HTTP 500',
                          stage: '5.3 发布并回传报价', trace_id: TRACE_A });
const camel = F.describe({ code: 'permission_denied', message: '无权限', traceId: TRACE_B });
const bare = F.describe({ code: 'permission_denied', message: '无权限' });
function ids(row) { return (row && row.actions ? row.actions.map(function (a) { return a && a.id; }) : []); }
function labels(row) { return (row && row.actions ? row.actions.map(function (a) { return String((a && a.label) || ''); }) : []); }
return {
  present: true,
  full: full,
  ids: ids(full),
  labels: labels(full),
  camel_trace: camel && camel.trace_id,
  bare_trace: bare && bare.trace_id,
  bare_quiet: bare && bare.quiet,
  full_quiet: full && full.quiet,
};
"""

SCRIPT_SHOW_CRITICAL = r"""
const F = window.TechFailure;
if (!F) { return { present: false }; }
if (typeof F.show !== 'function') { return { present: true, show_missing: true }; }
const node = F.show({ code: 'handoff_failed', message: '回传报价失败：HTTP 500',
                      stage: '5.3 发布并回传报价', trace_id: TRACE_A });
if (!node) { return { present: true, node_missing: true }; }
const body = window.document.body;
const attached = body.children.indexOf(node) >= 0;
window.__clock.advance(60000);
const after60 = body.children.indexOf(node) >= 0;
window.__clock.advance(600000);
const after660 = body.children.indexOf(node) >= 0;
const attr = String(node.getAttribute('data-trace-id') ||
  (node.dataset && (node.dataset.traceId || node.dataset.trace_id)) || '');
return { present: true, attached: attached, after60: after60, after660: after660,
         text: window.__textOf(node), trace_attr: attr };
"""

SCRIPT_SHOW_QUIET = r"""
const F = window.TechFailure;
if (!F) { return { present: false }; }
if (typeof F.show !== 'function') { return { present: true, show_missing: true }; }
const before = window.document.body.children.length;
const node = F.show({ code: QUIET_CODE, message: '刷新看板失败' });
return { present: true, before: before, after: window.document.body.children.length,
         node_falsy: !node };
"""


class FailurePolicyTest(NodeCase):
    def test_critical_and_quiet_sets_are_disjoint(self):
        rows = run_jobs([job("policy", SCRIPT_POLICY, modules=[str(FAILURE_MODULE)])])
        value = self.require_module(
            rows, "policy", FAILURE_MODULE,
            "缺少 tech_app/frontend/tech-failure-banner.js（批次 9 Spec §7.5）："
            "window.TechFailure 是「安静 / 固定展示」的唯一口径")
        for code in CRITICAL_CODES:
            self.assertIn(code, value["critical"],
                          "关键失败码必须登记 %s（Spec §7.5）" % code)
        self.assertEqual([], value["overlap"],
                         "关键失败码与安静码不得相交：%s" % value["overlap"])
        for code, quiet in value["quiet_for_critical"]:
            self.assertEqual("false", quiet, "关键失败 %s 不得被静默放行" % code)
        self.assertEqual([], value["quiet_non_refresh"],
                         "安静码只允许刷新/选择类副作用，实际混入：%s" % value["quiet_non_refresh"])
        self.assertEqual("true", value["quiet_member"], "安静码本身必须是 quiet")
        self.assertEqual("false", value["quiet_nocode"],
                         "没有码的失败默认不安静（Spec §5.4）")
        self.assertEqual("false", value["quiet_null"], "null 失败默认不安静")
        self.assertEqual("true", value["quiet_explicit"],
                         "显式 quiet:true 仍可静默（既有桥语义不反转）")

    def test_describe_has_five_elements(self):
        rows = run_jobs([job("describe", SCRIPT_DESCRIBE, modules=[str(FAILURE_MODULE)])])
        value = self.require_module(rows, "describe", FAILURE_MODULE,
                                    "缺少 tech_app/frontend/tech-failure-banner.js（Spec §7.5）")
        self.assertFalse(value.get("describe_missing"), "TechFailure.describe 必须存在")
        self.assertEqual(TRACE_A, value["full"]["trace_id"], "describe 必须透出追踪 ID")
        self.assertIn(TRACE_B, [value["camel_trace"]], "describe 必须同时接受 camelCase traceId")
        self.assertEqual("", value["bare_trace"], "没有追踪 ID 时给空串，不报错")
        self.assertIn("retry", value["ids"], "五要素里的「重试」必须是一个 action：%s" % value)
        self.assertIn("goto-step", value["ids"], "「返回正确步骤」必须是一个 action：%s" % value)
        goto = [label for label in value["labels"]]
        self.assertTrue(any("回传" in label or "5.3" in label for label in goto),
                        "「返回正确步骤」的文案必须写出目标步骤：%s" % goto)
        self.assertTrue(value["full"]["reason"], "必须给出「原因」")
        self.assertTrue(value["full"]["impact"], "必须给出「影响」")
        self.assertFalse(value["full_quiet"], "关键失败不得标记为安静")


class FailureBannerTest(NodeCase):
    def test_critical_failure_is_persistent_and_carries_trace_id(self):
        rows = run_jobs([job("show", SCRIPT_SHOW_CRITICAL, modules=[str(FAILURE_MODULE)],
                             virtualTimers=True)])
        value = self.require_module(rows, "show", FAILURE_MODULE,
                                    "缺少 tech_app/frontend/tech-failure-banner.js（Spec §7.5）")
        self.assertFalse(value.get("show_missing"), "TechFailure.show 必须存在")
        self.assertFalse(value.get("node_missing"), "关键失败必须产出常驻节点")
        self.assertTrue(value["attached"], "show() 必须把失败块挂到页面上")
        self.assertTrue(value["after60"],
                        "关键失败块 60 秒后仍在（不得像 toast 一样自动消失）")
        self.assertTrue(value["after660"],
                        "关键失败块 11 分钟后仍在（常驻，只能显式关闭）")
        self.assertEqual(TRACE_A, value["trace_attr"], "失败块必须带 data-trace-id")
        text = value["text"] or ""
        for label in ("原因", "影响", "重试", "返回正确步骤", "错误追踪 ID"):
            self.assertTrue(label in text,
                            "失败块必须展示「%s」；现有文案：%s" % (label, text[:300]))

    def test_quiet_failure_renders_nothing(self):
        rows = run_jobs([job("quiet", SCRIPT_SHOW_QUIET, modules=[str(FAILURE_MODULE)],
                             virtualTimers=True)])
        value = self.require_module(rows, "quiet", FAILURE_MODULE,
                                    "缺少 tech_app/frontend/tech-failure-banner.js（Spec §7.5）")
        self.assertTrue(value["node_falsy"], "安静失败不得产出失败块")
        self.assertEqual(value["before"], value["after"],
                         "安静失败不得往页面上挂节点")


# --------------------------------------------------------------------------- #
# 接线（只做「模块存在性 + 脚本加载顺序」这类静态契约）
# --------------------------------------------------------------------------- #
BANNER_PAGES = ("assembly-integration.js", "report-publish-result.js", "cost-review.js")
BANNER_HTML = {"assembly-integration.html": "assembly-integration.js",
               "report-publish.html": "report-publish-result.js",
               "cost-review.html": "cost-review.js"}


class WiringTest(unittest.TestCase):
    def test_new_modules_exist(self):
        self.assertTrue(WATCH_MODULE.exists(),
                        "缺少 tech_app/frontend/tech-task-watch.js（Spec §7.4）")
        self.assertTrue(FAILURE_MODULE.exists(),
                        "缺少 tech_app/frontend/tech-failure-banner.js（Spec §7.5）")

    def test_critical_failure_pages_use_persistent_banner(self):
        for name in BANNER_PAGES:
            text = read(FRONTEND / name)
            self.assertTrue("TechFailure.show(" in text,
                            "%s 的关键失败必须走常驻失败块，而不是只弹 toast"
                            "（Spec §1.3 / §5.3）" % name)

    def test_pages_load_banner_before_use(self):
        for html, page in BANNER_HTML.items():
            text = read(FRONTEND / html)
            banner_at = text.find("tech-failure-banner.js")
            page_at = text.find(page)
            self.assertGreaterEqual(banner_at, 0,
                                    "%s 必须加载 tech-failure-banner.js（脚本加载顺序契约）" % html)
            self.assertGreater(page_at, banner_at,
                               "%s 必须在 %s 之前加载 tech-failure-banner.js" % (html, page))

    def test_watch_module_loaded_by_banner_pages(self):
        for name in BANNER_PAGES:
            text = read(FRONTEND / name)
            self.assertTrue("tech-task-watch.js" in read(FRONTEND / name)
                            or "TechTaskWatch" in text,
                            "%s 必须使用统一的轮询模块 TechTaskWatch（Spec §7.4）" % name)


if __name__ == "__main__":
    unittest.main()
