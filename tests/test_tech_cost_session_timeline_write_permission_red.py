"""红测：2.3 成本测算的会话时间线写入权限（财务经理不再被前端伪 403 拦下）。

用户反馈：
    我在成本测算为什么会显示这一步归工艺经理办理；财务经理没有这一步的操作权限
    但是执行是可以正常执行的

根因（已实测）：
  · `tech_app/frontend/cpq-sso.js:167` 的写请求拦截只在
    `state.canWrite || (state.canCost && isCostUrl(url))` 为真时放行；财务经理
    `can_write=false` / `can_cost=true`（`main.py:597-598`、`auth.COST_ROLES`）。
  · `COST_URL_PATTERNS`（cpq-sso.js:34-41）只列了 `/cost-review/*`、`/parts/{id}/cost`、
    `/integration/cost` —— 2.3 的真业务动作都在里面，所以「执行可以正常执行」。
  · 漏掉的是同一批动作伴随写的会话时间线：`cost-review.js:92` 的过程文字要
    `POST /api/projects/{id}/agent/event`（## 69 引入）。它不在白名单里 → 前端伪造 403
    且**不调用 nativeFetch** → 后端没有日志，用户只看到「这一步归工艺经理办理」，
    而成本动作其实已经成功、过程文字却没落库。
  · 另一半缺口在后端：`main.py` 的 `/agent/event` 用 `auth.WRITE_ROLES`，
    `finance_manager` 不在其中 —— 前端放行也仍会真 403。

红线：拦截表达式的形状（`state.canWrite || (state.canCost && isCostUrl(url))`、伪 403、toast）、
Agent 对话 `/agent/send` 归工艺侧、`COST_ROLES` / `WRITE_ROLES` / `ROLE_MAP` 的值、
会话时间线的读写结构与排序口径，全部不变。
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
SSO_JS = FRONTEND / "cpq-sso.js"
COST_JS = FRONTEND / "cost-review.js"
MAIN = (ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8")
AUTH = (ROOT / "tech_app" / "backend" / "services" / "auth.py").read_text(encoding="utf-8")
SSO_SOURCE = SSO_JS.read_text(encoding="utf-8", errors="replace")
VENV_PY = ROOT / "open-claude" / ".venv" / "bin" / "python"

# 2.3 页面在财务经理手里真正会发的写请求（真业务动作 + 伴随写的会话时间线）。
MUST_PASS_FOR_FINANCE = [
    ("session_timeline_note", "/api/projects/P-abc123/agent/event", "POST"),
    ("session_timeline_note_query", "/api/projects/P-abc123/agent/event?stage=cost", "POST"),
    ("cost_review_confirm", "/api/projects/P-abc123/cost-review/confirm", "POST"),
    ("cost_review_part", "/api/projects/P-abc123/cost-review/parts/P-1?quantity=1", "POST"),
    ("part_cost", "/api/projects/P-abc123/parts/P-1/cost", "POST"),
    ("integration_cost", "/api/projects/P-abc123/integration/cost", "POST"),
]

# 仍必须被拦下来的：Agent 对话与工艺侧的写接口不归财务经理。
MUST_BLOCK_FOR_FINANCE = [
    ("agent_send", "/api/projects/P-abc123/agent/send", "POST"),
    ("agent_new", "/api/projects/P-abc123/agent/new", "POST"),
    ("params_finalize", "/api/projects/P-abc123/integration/params/finalize", "POST"),
    ("params_autofill", "/api/projects/P-abc123/integration/params/autofill", "POST"),
]

# isCostUrl() 的纯函数判定（URL 白名单不论能力位）。
URL_TRUE = [url for _, url, _ in MUST_PASS_FOR_FINANCE]
URL_FALSE = [url for _, url, _ in MUST_BLOCK_FOR_FINANCE] + [
    "/api/projects/P-abc123/agent/meta",
    "/api/projects/P-abc123/requirements/REQ-1/confirm",
    "/api/projects/P-abc123/parse",
]

GATE_NODE = r'''
const fs = require('fs');
const vm = require('vm');

const ssoPath = process.argv[2];
const snippetPath = process.argv[3];
const config = JSON.parse(process.argv[4]);
const out = {};

// A. 纯函数：直接执行源码里的 COST_URL_PATTERNS / isCostUrl（不是文本推断）。
{
  const snippet = fs.readFileSync(snippetPath, 'utf8');
  const env = {};
  vm.runInNewContext(snippet + '\nthis.__isCostUrl = isCostUrl;', env,
                     { filename: 'cpq-sso-url-gate.js' });
  if (typeof env.__isCostUrl !== 'function') throw new Error('源码里没有 isCostUrl 实现');
  out.url_gate = {};
  for (const url of config.urlTrue.concat(config.urlFalse)) {
    out.url_gate[url] = !!env.__isCostUrl(url);
  }
}

// B. 真跑整份 cpq-sso.js：打桩 /api/me 给出能力位，再看写请求到底有没有发给原生 fetch。
async function loadGate(ssoBody) {
  const nativeCalls = [];
  const sandbox = {};
  const store = {};
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.console = console;
  sandbox.addEventListener = function () {};
  sandbox.removeEventListener = function () {};
  sandbox.setTimeout = (fn, ms) => (ms > 100 ? null : setTimeout(fn, ms));
  sandbox.clearTimeout = clearTimeout;
  sandbox.localStorage = {
    getItem: key => (Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null),
    setItem: (key, value) => { store[key] = String(value); },
    removeItem: key => { delete store[key]; },
  };
  sandbox.location = { pathname: '/tech-workbench.html' };
  sandbox.document = {
    readyState: 'complete',
    addEventListener() {}, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getElementById() { return null; },
    createElement() {
      return { style: {}, className: '', innerHTML: '', textContent: '',
               setAttribute() {}, addEventListener() {}, appendChild() {},
               remove() {}, querySelector() { return null; } };
    },
    head: { appendChild() {} },
    body: { appendChild() {} },
    dispatchEvent() {},
  };
  sandbox.CustomEvent = class CustomEvent {
    constructor(type, init) { this.type = type; Object.assign(this, init || {}); }
  };
  sandbox.Response = class Response {
    constructor(body, init) {
      this.body = body;
      this.status = (init && init.status) || 200;
      this.ok = this.status >= 200 && this.status < 300;
      this.headers = { get: () => 'application/json' };
    }
    json() { return Promise.resolve(JSON.parse(this.body || '{}')); }
  };
  sandbox.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : ((input && input.url) || '');
    nativeCalls.push({ url: url, method: ((init && init.method) || 'GET').toUpperCase() });
    if (url.indexOf('/api/me') !== -1) {
      return Promise.resolve(new sandbox.Response(
        JSON.stringify({ user: { username: 'probe' }, sso: ssoBody }), { status: 200 }));
    }
    return Promise.resolve(new sandbox.Response('{"ok":true}', { status: 200 }));
  };
  const code = fs.readFileSync(ssoPath, 'utf8');
  vm.runInContext(code, vm.createContext(sandbox), { filename: 'cpq-sso.js' });
  await new Promise(resolve => setTimeout(resolve, 30));  // 等 check() 的 promise 链跑完
  return { sandbox: sandbox, calls: nativeCalls };
}

async function probe(ssoBody) {
  const loaded = await loadGate(ssoBody);
  const state = loaded.sandbox.CpqSso && loaded.sandbox.CpqSso.state
    ? loaded.sandbox.CpqSso.state() : null;
  const result = { state: state ? { enabled: state.enabled, checked: state.checked,
                                    canWrite: state.canWrite, canCost: state.canCost } : null,
                   cases: {} };
  for (const item of config.pass.concat(config.block)) {
    const [label, url, method] = item;
    loaded.calls.length = 0;
    const response = await loaded.sandbox.fetch(url, { method: method });
    result.cases[label] = { status: response.status, native: loaded.calls.length > 0,
                            url: url, method: method };
  }
  return result;
}

(async () => {
  out.finance = await probe({ enabled: true, can_write: false, can_cost: true,
                              role_name: '财务经理' });
  out.process = await probe({ enabled: true, can_write: true, can_cost: false,
                              role_name: '工艺经理' });
  out.viewer = await probe({ enabled: true, can_write: false, can_cost: false,
                             role_name: '销售经理' });
  console.log(JSON.stringify(out));
})().catch(err => { console.log(JSON.stringify({ _error: String(err && err.stack || err) })); process.exit(0); });
'''


CHILD = r'''
import json
import os
import sys
import types

data_dir, root, case = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _stub

from tech_app.backend.storage import store


def dump(payload):
    print(json.dumps(payload, ensure_ascii=False, default=str))


try:
    if case == "session_timeline_write_roles":
        from starlette.testclient import TestClient
        import tech_app.backend.main as main

        pid = store.create_project(source_filename="cost.dxf", source_bytes=b"x",
                                   note="cost timeline permission probe", owner="probe")
        client = TestClient(main.app, raise_server_exceptions=False)

        def as_role(role, cpq_name=""):
            user = {"username": role + "-probe", "role": role, "display_name": role}
            if cpq_name:
                user["cpq_role_name"] = cpq_name
            main.app.dependency_overrides[main.current_user] = lambda _user=user: _user

        def post(path, body):
            response = client.post("/api/projects/%s%s" % (pid, path), json=body)
            detail = ""
            try:
                detail = (response.json() or {}).get("detail") or ""
            except Exception:
                detail = response.text
            return {"status": response.status_code, "detail": str(detail)}

        results = {}
        main.app.dependency_overrides.clear()
        results["anonymous_admin_event"] = post("/agent/event", {
            "kind": "session-note", "source": "board", "stage": "cost",
            "text": "重启后仍在", "key": "cost:anon"})
        for role, cpq_name in (("finance_manager", "财务经理"),
                               ("process_manager", "工艺经理"),
                               ("sales_manager", "销售经理")):
            as_role(role, cpq_name)
            results["event_" + role] = post("/agent/event", {
                "kind": "session-note", "source": "board", "stage": "cost",
                "text": "成本测算中（%s）" % role, "key": "cost:" + role})
        as_role("finance_manager", "财务经理")
        # 会话时间线是项目数据、不是业务产出：前端只能看到 URL、看不到阶段，
        # 后端也就不按阶段限制，避免「前端放行 / 后端 403」的伪权限提示复发。
        results["event_finance_manager_other_stage"] = post("/agent/event", {
            "kind": "session-note", "source": "shell", "stage": "drawing",
            "text": "财务侧留痕", "key": "shell:finance"})
        # Agent 对话仍归工艺侧：财务经理不得越界。
        results["send_finance_manager"] = post("/agent/send", {"message": "你好"})
        results["new_finance_manager"] = post("/agent/new", {})
        as_role("process_manager", "工艺经理")
        results["event_process_manager_no_key"] = post("/agent/event", {
            "kind": "session-note", "source": "board", "stage": "process",
            "text": "工艺侧仍可写"})
        main.app.dependency_overrides.clear()
        dump({"pid": pid, "results": results, "stored": store.load_session_events(pid)})
    else:
        dump({"_error": "unknown case: " + case})
except Exception as exc:  # noqa: BLE001 - 红测要把真实缺口类型回给断言
    dump({"_error": "%s: %s" % (type(exc).__name__, exc), "_error_type": type(exc).__name__})
'''


def py_function(source: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?def\s+{re.escape(name)}\s*\([^)]*\)[^:]*:\s*([\s\S]*?)"
        rf"(?=\n(?:async\s+)?def\s+|\n@app\.|\Z)",
        source,
    )
    if not match:
        raise AssertionError(f"找不到 Python 函数 {name}")
    return match.group(1)


def cost_url_snippet() -> str:
    """源码里 COST_URL_PATTERNS 到 isCostUrl 结束的那一段（本批允许在中间加常量）。"""
    start = SSO_SOURCE.find("var COST_URL_PATTERNS")
    assert start > 0, "找不到 cpq-sso.js 的成本接口白名单"
    is_cost_url = SSO_SOURCE.find("function isCostUrl", start)
    assert is_cost_url > start, "找不到 isCostUrl()"
    end = SSO_SOURCE.find("\n  }", is_cost_url)
    assert end > is_cost_url, "isCostUrl() 的实现不完整"
    return SSO_SOURCE[start:end + 4]


def run_gate_node() -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("未安装 node，无法真跑 cpq-sso.js 的写请求拦截")
    config = {
        "urlTrue": URL_TRUE,
        "urlFalse": URL_FALSE,
        "pass": MUST_PASS_FOR_FINANCE,
        "block": MUST_BLOCK_FOR_FINANCE,
    }
    workdir = tempfile.mkdtemp(prefix="tech-cost-sso-node-")
    try:
        snippet = Path(workdir) / "cost-url.js"
        snippet.write_text(cost_url_snippet(), encoding="utf-8")
        script = Path(workdir) / "gate.js"
        script.write_text(GATE_NODE, encoding="utf-8")
        completed = subprocess.run(
            [node, str(script), str(SSO_JS), str(snippet), json.dumps(config)],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "拦截器 harness 执行失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout, completed.stderr))
        lines = [line for line in completed.stdout.strip().splitlines() if line.strip()]
        if not lines:
            raise AssertionError("拦截器 harness 没有输出\nstderr:\n%s" % completed.stderr)
        return json.loads(lines[-1])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _interpreter() -> str:
    for candidate in (VENV_PY, Path(sys.executable)):
        if candidate and Path(candidate).exists():
            probe = subprocess.run(
                [str(candidate), "-c", "import fastapi, pydantic, starlette"],
                capture_output=True, text=True)
            if probe.returncode == 0:
                return str(candidate)
    raise unittest.SkipTest(
        "没有带 FastAPI/Starlette 的解释器（本机为 open-claude/.venv/bin/python），"
        "跳过后端会话时间线权限测试")


def run_child(case: str, data_dir: str, timeout: int = 300) -> dict:
    python = _interpreter()
    workdir = tempfile.mkdtemp(prefix="tech-cost-sso-child-")
    try:
        script = Path(workdir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run(
            [python, str(script), data_dir, str(ROOT), case],
            capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "子进程执行失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout, completed.stderr))
        lines = [line for line in completed.stdout.strip().splitlines() if line.strip()]
        if not lines:
            raise AssertionError("子进程没有输出 JSON\nstderr:\n%s" % completed.stderr)
        return json.loads(lines[-1])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class FrontendCostGateAllowsSessionTimeline(unittest.TestCase):
    """前端拦截器：财务经理的 2.3 写请求不能再被伪 403 挡在浏览器里。"""

    @classmethod
    def setUpClass(cls):
        cls.gate = run_gate_node()

    def _error(self):
        self.assertNotIn("_error", self.gate, "拦截器 harness 跑不起来：%s" % self.gate.get("_error"))

    def test_cost_url_helper_recognises_the_session_timeline_write(self):
        self._error()
        table = self.gate["url_gate"]
        for url in URL_TRUE:
            with self.subTest(url=url):
                self.assertTrue(table.get(url), "2.3 要写的会话时间线必须算成本白名单：%s" % url)
        for url in URL_FALSE:
            with self.subTest(url=url):
                self.assertFalse(table.get(url), "这一步不归财务经理，别放行：%s" % url)

    def test_finance_manager_write_requests_reach_the_native_fetch(self):
        self._error()
        cases = self.gate["finance"]["cases"]
        for label, url, method in MUST_PASS_FOR_FINANCE:
            with self.subTest(case=label):
                got = cases.get(label) or {}
                self.assertTrue(got.get("native"),
                                "财务经理的 %s %s 被前端伪 403 拦下了（请求没发出去）"
                                % (method, url))
                self.assertEqual(got.get("status"), 200, "%s 应放行给后端判定" % label)
        for label, url, method in MUST_BLOCK_FOR_FINANCE:
            with self.subTest(case=label):
                got = cases.get(label) or {}
                self.assertFalse(got.get("native"),
                                 "%s 归工艺侧，前端仍要拦下（别把 Agent 对话也放给财务）" % label)
                self.assertEqual(got.get("status"), 403, "%s 仍应返回结构化 403" % label)

    def test_process_manager_and_viewer_behaviour_unchanged(self):
        self._error()
        process = self.gate["process"]["cases"]
        for label, url, method in MUST_PASS_FOR_FINANCE + MUST_BLOCK_FOR_FINANCE:
            with self.subTest(case=label):
                self.assertTrue((process.get(label) or {}).get("native"),
                                "工艺经理有 can_write，任何写请求都不该被拦：%s" % label)
        viewer = self.gate["viewer"]["cases"]
        for label, url, method in MUST_PASS_FOR_FINANCE + MUST_BLOCK_FOR_FINANCE:
            with self.subTest(case=label):
                got = viewer.get(label) or {}
                self.assertFalse(got.get("native"),
                                 "既没有工艺侧写权限也没有成本权限的账号仍要拦住：%s" % label)
                self.assertEqual(got.get("status"), 403)

    def test_gate_expression_and_single_source_of_urls_stay(self):
        self.assertIn("state.canWrite || (state.canCost && isCostUrl(url))", SSO_SOURCE,
                      "能力分流不能改形状；本批只修误拦")
        self.assertIn("status: 403", SSO_SOURCE)
        self.assertIn("toast(detail)", SSO_SOURCE)
        self.assertNotIn("function isCostUrl(url) {\n    return COST_URL_PATTERNS.some",
                         SSO_SOURCE,
                         "会话时间线的放行要并进 isCostUrl 的同一份判定，别另写一条分支")
        self.assertIn("/agent/event", SSO_SOURCE, "会话时间线写入要显式列出来")

    def test_cost_page_really_writes_the_timeline(self):
        body = COST_JS.read_text(encoding="utf-8", errors="replace")
        start = body.find("function crPersistNote")
        self.assertGreater(start, 0, "cost-review.js 仍要写本阶段的过程文字")
        self.assertIn("/agent/event", body[start:start + 700],
                      "2.3 的过程文字就是通过 /agent/event 落库的（本批放行的正是它）")


class BackendSessionTimelineWriteRoles(unittest.TestCase):
    """后端：会话时间线是项目数据，2.3 的操作者（财务经理）同样要能写。"""

    @classmethod
    def setUpClass(cls):
        cls.data_dir = tempfile.mkdtemp(prefix="tech-cost-sso-store-")
        cls.data = run_child("session_timeline_write_roles", cls.data_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def _error(self):
        self.assertNotIn("_error", self.data, "后端能力尚不存在：%s" % self.data.get("_error"))

    def _case(self, name):
        self._error()
        return self.data["results"][name]

    def test_finance_manager_can_write_the_session_timeline(self):
        got = self._case("event_finance_manager")
        self.assertEqual(got["status"], 200,
                         "财务经理在 2.3 写过程文字被后端拦下了：%s" % got["detail"])

    def test_process_manager_keeps_its_ability(self):
        for name in ("event_process_manager", "event_process_manager_no_key"):
            with self.subTest(case=name):
                self.assertEqual(self._case(name)["status"], 200, "工艺侧既有能力不得回退")

    def test_anonymous_admin_path_untouched(self):
        self.assertEqual(self._case("anonymous_admin_event")["status"], 200)

    def test_roles_without_any_capability_are_still_blocked(self):
        got = self._case("event_sales_manager")
        self.assertEqual(got["status"], 403, "销售经理在技术工艺里仍是只读")

    def test_stage_is_not_restricted(self):
        got = self._case("event_finance_manager_other_stage")
        self.assertEqual(got["status"], 200,
                         "URL 级白名单与阶段级后端规则不一致会重现伪权限提示：%s" % got["detail"])

    def test_agent_chat_stays_on_the_process_side(self):
        for name in ("send_finance_manager", "new_finance_manager"):
            with self.subTest(case=name):
                self.assertEqual(self._case(name)["status"], 403,
                                 "Agent 对话仍归工艺侧，财务经理不能越界")

    def test_written_notes_are_persisted(self):
        self._error()
        texts = [str(row.get("text") or "") for row in self.data["stored"]]
        self.assertIn("成本测算中（finance_manager）", texts,
                      "放行的写请求必须真的落库，否则重进项目还是少几条")
        self.assertIn("工艺侧仍可写", texts)


class SourceContract(unittest.TestCase):
    """权限集合与路由的角色集合：只增一处，其余一个不动。"""

    def test_session_write_roles_is_the_union_of_both_capabilities(self):
        match = re.search(r"^SESSION_WRITE_ROLES\s*=\s*(.+)$", AUTH, re.M)
        self.assertIsNotNone(match, "auth.py 缺少会话时间线的写权限集合")
        expr = match.group(1)
        self.assertIn("WRITE_ROLES", expr)
        self.assertIn("COST_ROLES", expr)

    def test_agent_event_uses_the_session_roles(self):
        body = py_function(MAIN, "agent_event")
        self.assertIn("auth.SESSION_WRITE_ROLES", body,
                      "/agent/event 要按会话时间线权限判定，而不是工艺侧写权限")
        self.assertIn("会话内容属于项目数据", body, "路由自己的口径说明要保留")

    def test_agent_chat_and_params_routes_are_unchanged(self):
        self.assertIn("auth.WRITE_ROLES", py_function(MAIN, "agent_send"),
                      "Agent 对话仍归工艺侧写权限")
        self.assertIn("auth.WRITE_ROLES", py_function(MAIN, "agent_restart_task"))
        self.assertIn("auth.WRITE_ROLES", py_function(MAIN, "finalize_integration_params"))
        self.assertNotIn("auth.COST_ROLES", py_function(MAIN, "finalize_integration_params"))

    def test_role_sets_keep_their_values(self):
        self.assertIn('WRITE_ROLES = {"engineer", "process_manager", "admin"}', AUTH)
        self.assertIn('COST_ROLES = {"finance_manager", "admin"}', AUTH)
        self.assertIn('"finance_mgr": "finance_manager"',
                      (ROOT / "tech_app/backend/services/cpq_sso.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
