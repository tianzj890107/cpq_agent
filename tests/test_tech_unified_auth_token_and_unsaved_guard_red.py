"""批次 8 红测：统一认证客户端、Token 状态传播与未保存修改保护。

Spec: ``docs/specs/tech-unified-auth-token-and-unsaved-guard.md``

本批两件事：

* **8A** 登录态只有一份事实源（``cpq_auth_token``），兼容键 ``authToken`` /
  ``cad_engine_token`` 只读一次后迁移；身份变化**就地**广播、**不再整页重载**；
  登录态变化不丢当前 project / stage。
* **8B** 看板把「有未保存修改」通过 ``TechBoardBridge`` 报给父壳，
  父壳用一个 ``guardLeave`` 闸门拦住五个导航出口；取消是 quiet 的、
  未知（失败 / 超时）宁可拦下也不能静默丢改动。

这些用例在**实现前必须失败**，且失败必须落在真实缺口上（不是导入 / 语法 / 环境错误）。
前端部分用 node 真跑模块与协议（不是静态文本搜索）；只有脚本路由存在性用静态契约。
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
FRONTEND = ROOT / "tech_app" / "frontend"

AUTH_MODULE = FRONTEND / "tech-auth-session.js"          # 8A 新增（本批要求）
SSO_MODULE = FRONTEND / "cpq-sso.js"                     # 8A 改造
BRIDGE_MODULE = FRONTEND / "tech-board-bridge.js"        # 8B 扩协议
WORKBENCH_MODULE = FRONTEND / "tech-workbench.js"        # 8B 导航闸门
LEGACY_CLIENTS = [FRONTEND / "auth.js", FRONTEND / "account.js", FRONTEND / "session-guard.js"]
SPEC_FILE = ROOT / "docs" / "specs" / "tech-unified-auth-token-and-unsaved-guard.md"

NODE = shutil.which("node")


# --------------------------------------------------------------------------- #
# node 驱动：真跑前端模块 + 真跑 postMessage 协议
# --------------------------------------------------------------------------- #
DRIVER = r'''
const vm = require('vm');
const fs = require('fs');
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

function fire(handlers, ev) {
  const type = String((ev && ev.type) || '');
  (handlers[type] || []).slice().forEach(function (fn) { try { fn(ev); } catch (error) {} });
}

function makeTarget() {
  const handlers = {};
  return {
    _handlers: handlers,
    addEventListener: function (type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    removeEventListener: function (type, fn) {
      const list = handlers[type] || []; const i = list.indexOf(fn); if (i >= 0) list.splice(i, 1);
    },
    dispatchEvent: function (ev) { fire(handlers, ev); return true; },
  };
}

function safe(value) {
  try { return JSON.parse(JSON.stringify(value === undefined ? null : value)); }
  catch (error) { return String(value); }
}

async function runJob(job) {
  const out = { name: job.name, loaded: [] };
  const store = Object.assign({}, job.storage || {});
  const session = Object.assign({}, job.session || {});
  const sessionWrites = [];
  const reloads = [];
  const fetches = [];
  const parentPosts = [];
  const posted = [];

  const doc = makeTarget();
  doc.readyState = 'complete';
  doc.getElementById = function () { return null; };
  doc.querySelector = function () { return null; };
  doc.querySelectorAll = function () { return []; };
  doc.createElement = function () {
    return { style: {}, dataset: {}, className: '', id: '', textContent: '', innerHTML: '',
             appendChild: function () {}, remove: function () {}, querySelector: function () { return null; },
             addEventListener: function () {} };
  };
  doc.head = { appendChild: function () {} };
  doc.body = { appendChild: function () {} };

  function MiniEvent(type, init) { this.type = String(type); this.detail = init && init.detail; }

  const sandbox = {
    console: { log: function () {}, warn: function () {}, error: function () {} },
    setTimeout: setTimeout, clearTimeout: clearTimeout,
    setInterval: function () { return 0; }, clearInterval: function () {},
    document: doc,
    CustomEvent: MiniEvent, Event: MiniEvent,
    localStorage: {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
      setItem: function (k, v) { store[k] = String(v); },
      removeItem: function (k) { delete store[k]; },
      key: function (i) { return Object.keys(store)[i] || null; },
    },
    sessionStorage: {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(session, k) ? session[k] : null; },
      setItem: function (k, v) { sessionWrites.push({ key: String(k) }); session[k] = String(v); },
      removeItem: function (k) { sessionWrites.push({ key: String(k) }); delete session[k]; },
    },
    location: {
      href: 'http://localhost' + (job.pathname || '/tech-workbench.html'),
      origin: 'http://localhost',
      pathname: job.pathname || '/tech-workbench.html',
      search: job.search || '', hash: '',
      reload: function () { reloads.push(1); },
      replace: function () {}, assign: function () {},
    },
    history: { pushState: function () {}, replaceState: function () {}, back: function () {}, forward: function () {} },
    navigator: { userAgent: 'probe' },
    URLSearchParams: URLSearchParams, URL: URL, Response: Response, Headers: Headers,
    fetch: function (url, opts) {
      fetches.push({ url: String(url), method: String((opts && opts.method) || 'GET'),
                     headers: Object.assign({}, (opts && opts.headers) || {}) });
      const status = job.fetchStatus === undefined ? 200 : job.fetchStatus;
      const body = job.fetchResponse === undefined ? {} : job.fetchResponse;
      return Promise.resolve({ ok: status < 400, status: status,
                               json: function () { return Promise.resolve(body); },
                               text: function () { return Promise.resolve(''); } });
    },
  };
  const win = makeTarget();
  Object.assign(sandbox, win);
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.top = job.embedded ? {} : sandbox;
  sandbox.parent = job.embedded
    ? { postMessage: function (msg, origin) { parentPosts.push({ msg: safe(msg), origin: origin }); } }
    : sandbox;
  sandbox.frameElement = job.embedded ? { dataset: { project: job.frameProject || '' } } : null;
  sandbox.__replies = job.replies || {};
  sandbox.__noReply = job.noReply || [];

  let frameWin = null;
  let lastEnvelope = null;

  sandbox.__harness = {
    posted: posted, parentPosts: parentPosts, sessionWrites: sessionWrites, reloads: reloads,
    fetches: fetches,
    sleep: function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); },
    installFrame: function (opts) {
      const options = opts || {};
      const replies = Object.assign({}, sandbox.__replies, options.replies || {});
      const noReply = (options.noReply || sandbox.__noReply || []);
      frameWin = {
        postMessage: function (msg, origin) {
          posted.push({ type: msg.type, name: msg.name, payload: safe(msg.payload),
                        requestId: msg.requestId, projectId: msg.projectId, stage: msg.stage,
                        origin: origin });
          lastEnvelope = msg;
          if (msg.type !== 'command') return;
          if (noReply.indexOf(msg.name) >= 0) return;
          const payload = Object.prototype.hasOwnProperty.call(replies, msg.name)
            ? replies[msg.name] : { ok: true, result: {} };
          const reply = Object.assign({}, msg, { type: 'result', payload: payload });
          setTimeout(function () {
            // 真实浏览器派发的一定是 type='message' 的事件；驱动必须照实派发，
            // 否则实现方只能靠「注册一个空类型监听器」迁就走查缺陷（生产死代码）。
            fire(win._handlers, { type: 'message', origin: sandbox.location.origin,
                                  source: frameWin, data: reply });
          }, 0);
        },
      };
      return frameWin;
    },
    frameWindow: function () { return frameWin; },
    deliver: function (data, source) {
      fire(win._handlers, { type: 'message', origin: sandbox.location.origin,
                            source: source || frameWin, data: data });
    },
    deliverState: function (name, payload, projectId, stage) {
      const base = lastEnvelope || {};
      sandbox.__harness.deliver({
        namespace: 'cpq:tech-board', version: 1, type: 'state', requestId: '', name: name,
        payload: payload || {},
        projectId: projectId === undefined ? (base.projectId || '') : projectId,
        stage: stage === undefined ? (base.stage || '') : stage,
      });
    },
    commandNames: function () {
      return posted.filter(function (m) { return m.name !== 'sync-state'; }).map(function (m) { return m.name; });
    },
  };

  vm.createContext(sandbox);

  (job.modules || []).forEach(function (file) {
    if (!file || !fs.existsSync(file)) { out.loaded.push({ file: file, ok: false, missing: true }); return; }
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
  out.reloads = reloads.length;
  out.fetches = fetches;
  out.posted = posted;
  out.parentPosts = parentPosts;
  out.sessionWrites = sessionWrites;
  out.storage = Object.assign({}, store);
  return out;
}

(async function main() {
  const results = [];
  for (const job of spec.jobs) results.push(await runJob(job));
  process.stdout.write(JSON.stringify(results));
})();
'''


def run_jobs(jobs: list) -> dict:
    """真跑 node。模块缺失不提前报错：让每个用例自己暴露真实行为。"""
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端行为走查")
    with tempfile.TemporaryDirectory(prefix="cpq-b8-spec-") as tmp:
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
    base = {"name": name, "script": script}
    base.update(kw)
    return base


class NodeCase(unittest.TestCase):
    """公共断言：node 跑出 ok=false 时，必须给出可读的失败原因（不是 ERROR）。"""

    def ok_row(self, rows, name):
        row = rows.get(name)
        self.assertIsNotNone(row, f"node 没有返回 {name} 的结果")
        self.assertTrue(row.get("ok"), f"{name} 脚本执行失败：{row.get('error')}")
        return row

    def value(self, rows, name):
        return self.ok_row(rows, name)["value"]


# --------------------------------------------------------------------------- #
# 8A 登录态：唯一事实源、迁移、广播、就地刷新
# --------------------------------------------------------------------------- #
class TokenSingleSourceTest(NodeCase):

    def test_auth_module_exists_with_pinned_contract(self):
        rows = run_jobs([job("contract", """
            const T = window.TechAuth;
            if (!T) { return { present: false }; }
            const fns = ['token','setToken','clear','migrate','subscribe',
                         'bindContext','context','embedded','broadcast','isReady','ready'];
            return {
              present: true,
              token_key: T.TOKEN_KEY,
              legacy: T.LEGACY_KEYS,
              missing_fns: fns.filter(function (k) { return typeof T[k] !== 'function'; }),
            };
        """, modules=[str(AUTH_MODULE)])])
        value = self.value(rows, "contract")
        self.assertTrue(value["present"],
                        "缺少 tech_app/frontend/tech-auth-session.js（批次 8 Spec §6.1）："
                        "window.TechAuth 是登录态的唯一事实源入口")
        self.assertEqual("cpq_auth_token", value["token_key"],
                         "唯一事实源键名必须仍是 cpq_auth_token")
        self.assertEqual(["authToken", "cad_engine_token"], value["legacy"],
                         "兼容键清单必须钉死为 authToken / cad_engine_token")
        self.assertEqual([], value["missing_fns"],
                         f"TechAuth 缺少这些方法：{value['missing_fns']}")

    def test_legacy_key_migrates_into_single_source(self):
        rows = run_jobs([job("migrate", """
            const T = window.TechAuth;
            const before = T.token();
            const after = T.migrate();
            const again = T.migrate();
            return { before: before, after: after, again: again };
        """, modules=[str(AUTH_MODULE)], storage={"authToken": "L1"})])
        row = self.ok_row(rows, "migrate")
        value = row["value"]
        self.assertEqual("L1", value["before"],
                         "只有兼容键 authToken 有值时，token() 必须能读出来（历史会话兼容）")
        self.assertEqual("L1", value["after"], "migrate() 必须返回迁移后的生效值")
        self.assertEqual("L1", row["storage"].get("cpq_auth_token"),
                         "migrate() 必须把兼容键的值写进唯一事实源")
        self.assertIsNone(row["storage"].get("authToken"),
                          "迁移后必须删除兼容键 authToken（不能长期留两份）")
        self.assertIsNone(row["storage"].get("cad_engine_token"),
                          "迁移后不得再新写 cad_engine_token")
        self.assertEqual("L1", value["again"], "migrate() 必须幂等")

    def test_canonical_token_wins_over_legacy(self):
        rows = run_jobs([job("canonical", """
            const T = window.TechAuth;
            return { token: T.token(), migrated: T.migrate() };
        """, modules=[str(AUTH_MODULE)],
            storage={"cpq_auth_token": "C", "authToken": "L", "cad_engine_token": "X"})])
        row = self.ok_row(rows, "canonical")
        self.assertEqual("C", row["value"]["token"],
                         "唯一事实源有值时不得被兼容键覆盖")
        self.assertEqual("C", row["storage"].get("cpq_auth_token"))
        self.assertIsNone(row["storage"].get("authToken"),
                          "迁移后兼容键必须被清掉，而不是留着旧值")

    def test_set_token_mirrors_all_keys_and_notifies(self):
        rows = run_jobs([job("set", """
            const T = window.TechAuth;
            const seen = [];
            const off = T.subscribe(function (e) { seen.push({ token: e && e.token, previous: e && e.previous }); });
            T.setToken('T9');
            off();
            T.setToken('T10');
            return { seen: seen, token: T.token() };
        """, modules=[str(AUTH_MODULE)])])
        row = self.ok_row(rows, "set")
        value = row["value"]
        # 脚本是 setToken('T9') → 退订 → setToken('T10')：
        # 退订只停止**通知**，不停止**写入**（Spec §6.1「唯一写入口」）。
        # 因此三份键与当前 token 都必须是最后写入的 'T10'，seen 只收到退订前那一次。
        self.assertEqual("T10", row["storage"].get("cpq_auth_token"))
        self.assertEqual("T10", row["storage"].get("authToken"),
                         "setToken 必须同时镜像到兼容键（否则页面仍读到空）")
        self.assertEqual("T10", row["storage"].get("cad_engine_token"),
                         "setToken 必须同时镜像到兼容键")
        self.assertEqual("T10", value["token"], "退订后写入的票才是当前票")
        self.assertEqual([{"token": "T9", "previous": ""}], value["seen"],
                         "订阅者只在退订前收到一次 {token, previous}；退订后写入不得再通知")

    def test_clear_removes_every_key(self):
        rows = run_jobs([job("clear", """
            const T = window.TechAuth;
            const seen = [];
            T.subscribe(function (e) { seen.push(e && e.token); });
            T.clear();
            return { seen: seen, token: T.token() };
        """, modules=[str(AUTH_MODULE)],
            storage={"cpq_auth_token": "C", "authToken": "C", "cad_engine_token": "C"})])
        row = self.ok_row(rows, "clear")
        for key in ("cpq_auth_token", "authToken", "cad_engine_token"):
            self.assertIsNone(row["storage"].get(key), f"clear() 必须清掉 {key}")
        self.assertEqual("", row["value"]["token"])
        self.assertEqual([""], row["value"]["seen"], "clear() 必须广播一次空票")

    def test_token_changes_never_reload_or_touch_session_storage(self):
        rows = run_jobs([job("reload", """
            const T = window.TechAuth;
            T.setToken('A'); T.clear(); T.setToken('B');
            return { token: T.token(), reloads: __harness.reloads.length };
        """, modules=[str(AUTH_MODULE)])])
        row = self.ok_row(rows, "reload")
        self.assertEqual(0, row["value"]["reloads"], "写登录态绝不允许整页重载")
        self.assertEqual(0, len(row["sessionWrites"]),
                         "不得在 sessionStorage 另存一份登录态（iframe 会各存一份）")

    def test_context_survives_identity_change(self):
        rows = run_jobs([job("context", """
            const T = window.TechAuth;
            T.bindContext({ project: 'p1', stage: 'cost' });
            T.setToken('A');
            const afterSet = T.context();
            T.clear();
            const afterClear = T.context();
            return { afterSet: afterSet, afterClear: afterClear };
        """, modules=[str(AUTH_MODULE)])])
        value = self.value(rows, "context")
        self.assertEqual({"project": "p1", "stage": "cost"}, value["afterSet"],
                         "登录态变化不得丢掉当前 project / stage（批次 1 的口径）")
        self.assertEqual({"project": "p1", "stage": "cost"}, value["afterClear"],
                         "登出也不得把 project / stage 清掉")

    def test_embedded_frame_does_not_keep_a_second_login_state(self):
        rows = run_jobs([job("embedded", """
            const T = window.TechAuth;
            T.setToken('E1');
            T.broadcast('logout', { reason: 'probe' });
            return { embedded: T.embedded(), token: T.token() };
        """, modules=[str(AUTH_MODULE)], embedded=True)])
        row = self.ok_row(rows, "embedded")
        self.assertTrue(row["value"]["embedded"], "iframe 内 embedded() 必须为 true")
        posts = row["parentPosts"]
        self.assertEqual(1, len(posts), f"广播必须发给父壳一次，实际 {len(posts)}")
        self.assertEqual("cpq:tech-auth", posts[0]["msg"].get("namespace"),
                         f"广播必须带 namespace=cpq:tech-auth：{posts[0]['msg']}")
        self.assertEqual("http://localhost", posts[0]["origin"],
                         "广播不能用 '*' 作为 targetOrigin")
        self.assertEqual(0, len(row["sessionWrites"]),
                         "iframe 不得另存一份登录态（必须与父壳共用 localStorage）")

    def test_ready_resolves_even_without_token(self):
        rows = run_jobs([job("ready", """
            const T = window.TechAuth;
            const beforeReady = T.isReady();
            const payload = await Promise.race([
              T.ready().then(function (v) { return { value: v }; }),
              __harness.sleep(1200).then(function () { return { timeout: true }; }),
            ]);
            return { beforeReady: beforeReady, payload: payload, afterReady: T.isReady(),
                     token: T.token() };
        """, modules=[str(AUTH_MODULE)], fetchStatus=401)])
        value = self.value(rows, "ready")
        self.assertFalse(value["payload"].get("timeout"),
                         "无票（401）时 ready() 也必须 resolve，不能挂住页面")
        self.assertIs(value["beforeReady"], False,
                      "就绪标记必须在首次登录态查询完成后才为 true")
        self.assertIs(value["afterReady"], True, "ready() 完成后 isReady() 必须为 true")

    def test_sso_reads_through_single_source_and_refreshes_in_place(self):
        rows = run_jobs([job("sso", """
            const T = window.TechAuth;
            const S = window.CpqSso;
            if (!S) { return { sso: false }; }
            const before = __harness.reloads.length;
            T.setToken('T2');
            await __harness.sleep(60);
            const me = __harness.fetches.filter(function (f) { return f.url.indexOf('/api/me') >= 0; });
            return {
              sso: true,
              same_source: T.token() === S.token(),
              token: T.token(),
              reloads: __harness.reloads.length - before,
              me_count: me.length,
              me_auth: me.map(function (f) { return (f.headers.Authorization || f.headers.authorization || ''); }),
            };
        """, modules=[str(AUTH_MODULE), str(SSO_MODULE)],
            fetchResponse={"sso": {"enabled": True, "can_write": True},
                           "user": {"username": "alice", "role": "process_manager"}})])
        value = self.value(rows, "sso")
        self.assertTrue(value["sso"], "cpq-sso.js 必须仍然加载并暴露 CpqSso")
        self.assertTrue(value["same_source"],
                        "CpqSso.token() 必须等于 TechAuth.token()（不允许第二份事实源）")
        self.assertEqual(0, value["reloads"],
                         "身份变化后必须就地刷新（Spec §6.2），不允许 location.reload()")
        self.assertGreaterEqual(value["me_count"], 2,
                                "身份变化后必须重新用新票请求 /api/me")
        self.assertEqual("Bearer T2", value["me_auth"][-1],
                         f"重新请求 /api/me 必须带新票，实际 {value['me_auth']}")

    def test_legacy_token_keys_have_one_writer(self):
        offenders = []
        for path in LEGACY_CLIENTS:
            text = path.read_text(encoding="utf-8")
            if re.search(r"""setItem\(\s*['"](authToken|cad_engine_token)['"]""", text):
                offenders.append(f"{path.relative_to(ROOT)} 仍自己写兼容键")
            if "TechAuth" not in text:
                offenders.append(f"{path.relative_to(ROOT)} 没有改用 TechAuth 读写登录态")
        self.assertEqual([], offenders, "登录态写入必须只有一个入口（批次 8 Spec §6.3）")


# --------------------------------------------------------------------------- #
# 8B 未保存修改：桥协议与导航闸门
# --------------------------------------------------------------------------- #
BRIDGE_PRELUDE = """
            const bridge = window.TechBoardBridge;
            const H = __harness;
            const events = [];
            bridge.subscribe(function (e) { events.push({ type: e.type, name: e.name }); });
            const frame = H.installFrame({});
            const errorNames = function () {
              return events.filter(function (e) { return e.type === 'error'; })
                           .map(function (e) { return e.name; });
            };
            const settle = function (promise) {
              return promise.then(function (v) { return { v: v }; },
                                  function (e) { return { err: String(e && e.message),
                                                          code: String((e && e.code) || '') }; });
            };
"""

ATTACHED = BRIDGE_PRELUDE + """
            await bridge.attach(frame, { projectId: 'p1', stage: 'cost' });
"""


class BridgeProtocolTest(NodeCase):

    def test_bridge_declares_the_leave_protocol(self):
        rows = run_jobs([job("proto", """
            const b = window.TechBoardBridge;
            const P = b.PROTOCOL || {};
            const events = b.STATE_EVENTS
              ? Object.keys(b.STATE_EVENTS).map(function (k) { return b.STATE_EVENTS[k]; }) : [];
            return {
              namespace: b.namespace, version: b.version,
              events: events, commands: P.commands || null,
              has_guard: typeof b.guardLeave === 'function',
              has_mark: typeof b.markDirty === 'function',
              has_warn: typeof b.shouldWarnOnUnload === 'function',
              snapshot_dirty_type: typeof ((b.snapshot() || {}).dirty),
            };
        """, modules=[str(BRIDGE_MODULE)])])
        value = self.value(rows, "proto")
        self.assertEqual("cpq:tech-board", value["namespace"], "namespace 不得改名（批次 9 依赖）")
        self.assertEqual(1, value["version"], "协议版本必须保持 1（向后兼容）")
        for name in ("dirty-state", "leave-approved"):
            self.assertIn(name, value["events"], f"STATE_EVENTS 缺 {name}")
        for name in ("ready", "action-state", "task-progress", "task-completed",
                     "task-failed", "selection-changed", "board-status"):
            self.assertIn(name, value["events"], f"既有状态事件 {name} 不得丢")
        self.assertIsNotNone(value["commands"], "必须导出 TechBoardBridge.PROTOCOL.commands")
        for name in ("request-leave", "save-draft", "discard"):
            self.assertIn(name, value["commands"], f"PROTOCOL.commands 缺 {name}")
        for name in ("execute-action", "navigate-view", "refresh-data", "select-part", "sync-state"):
            self.assertIn(name, value["commands"], f"既有命令 {name} 不得丢")
        self.assertTrue(value["has_guard"], "缺少 TechBoardBridge.guardLeave（Spec §6.4）")
        self.assertTrue(value["has_mark"], "缺少 TechBoardBridge.markDirty")
        self.assertTrue(value["has_warn"], "缺少 TechBoardBridge.shouldWarnOnUnload")
        self.assertEqual("boolean", value["snapshot_dirty_type"],
                         "snapshot().dirty 必须是一个布尔字段")

    def test_detached_board_fails_open(self):
        rows = run_jobs([job("open", BRIDGE_PRELUDE + """
            const allowed = await bridge.guardLeave('next', { target: 'summary' });
            return { allowed: allowed, warn: bridge.shouldWarnOnUnload(),
                     dirty: bridge.snapshot().dirty };
        """, modules=[str(BRIDGE_MODULE)])])
        value = self.value(rows, "open")
        self.assertTrue(value["allowed"],
                        "看板未 attach（纯查看页面）时 guardLeave 必须直接放行，不能误拦")
        self.assertFalse(value["warn"], "未 attach 时不应触发 beforeunload 警告")
        self.assertFalse(value["dirty"], "拿不到看板状态时不得默认 dirty")

    def test_clean_board_skips_the_handshake(self):
        rows = run_jobs([job("clean", ATTACHED + """
            const allowed = await bridge.guardLeave('next', { target: 'summary' });
            return { allowed: allowed, commands: H.commandNames(),
                     dirty: bridge.snapshot().dirty };
        """, modules=[str(BRIDGE_MODULE)])])
        value = self.value(rows, "clean")
        self.assertTrue(value["allowed"], "干净状态下必须直接放行")
        self.assertEqual([], value["commands"],
                         f"干净状态不得发 request-leave，实际发了 {value['commands']}")

    def test_dirty_waits_for_board_confirmation_after_save(self):
        rows = run_jobs([job("save", ATTACHED + """
            await H.deliverState('dirty-state', { dirty: true, reason: 'edit', source: 'board' });
            const settled = settle(bridge.guardLeave('next', { target: 'summary' }));
            await H.sleep(40);
            const beforeConfirm = await Promise.race([
              settled, H.sleep(1).then(function () { return { pending: true }; })]);
            await H.deliverState('dirty-state', { dirty: false, reason: 'saved', source: 'server' });
            const result = await Promise.race([
              settled, H.sleep(1500).then(function () { return { timeout: true }; })]);
            return { beforeConfirm: beforeConfirm, result: result, commands: H.commandNames(),
                     dirty: bridge.snapshot().dirty, errors: errorNames() };
        """, modules=[str(BRIDGE_MODULE)],
            replies={"request-leave": {"ok": True, "result": {"decision": "save"}},
                     "save-draft": {"ok": True, "result": {"saved": True}}})])
        value = self.value(rows, "save")
        self.assertTrue(value["beforeConfirm"].get("pending"),
                        "看板还没报 dirty=false 之前不得放行（save-draft 的 ok 回复不算确认）")
        self.assertFalse(value["result"].get("timeout"), "看板确认保存后必须 resolve")
        self.assertTrue(value["result"].get("v"), f"必须放行，实际 {value['result']}")
        self.assertEqual("request-leave", value["commands"][0],
                         f"必须先问再看板才可能保存：{value['commands']}")
        self.assertIn("save-draft", value["commands"], f"必须先落草稿：{value['commands']}")
        self.assertFalse(value["dirty"], "看板报 dirty=false 之后父壳必须清掉 dirty")
        self.assertEqual([], value["errors"], f"正常保存路径不该有失败事件：{value['errors']}")

    def test_mark_dirty_during_handshake_voids_the_approval(self):
        rows = run_jobs([job("unsaved", ATTACHED + """
            await H.deliverState('dirty-state', { dirty: true, reason: 'edit', source: 'board' });
            const settled = settle(bridge.guardLeave('next', { target: 'summary' }));
            await H.sleep(60);
            bridge.markDirty('agent-autofill');
            const result = await Promise.race([
              settled, H.sleep(400).then(function () { return { timeout: true }; })]);
            return { result: result, dirty: bridge.snapshot().dirty, commands: H.commandNames() };
        """, modules=[str(BRIDGE_MODULE)],
            replies={"request-leave": {"ok": True, "result": {"decision": "save"}},
                     "save-draft": {"ok": True, "result": {"saved": True}}})])
        value = self.value(rows, "unsaved")
        self.assertTrue(value["dirty"], "markDirty 之后 dirty 必须为 true")
        if value["result"].get("timeout"):
            self.fail("放行条件被判过脏之后必须立刻收敛，不能一直挂着")
        self.assertFalse(value["result"].get("v"),
                         "握手期间又被标脏（Agent 回填）时，本次放行必须作废（Spec §6.4）")

    def test_cancel_is_quiet_and_posts_nothing_else(self):
        rows = run_jobs([job("cancel", ATTACHED + """
            await H.deliverState('dirty-state', { dirty: true, reason: 'edit', source: 'board' });
            const result = await Promise.race([
              settle(bridge.guardLeave('next', { target: 'summary' })),
              H.sleep(800).then(function () { return { timeout: true }; })]);
            return { result: result, commands: H.commandNames(), errors: errorNames(),
                     state_error: String(bridge.snapshot().error || '') };
        """, modules=[str(BRIDGE_MODULE)],
            replies={"request-leave": {"ok": True, "result": {"decision": "cancel"}}})])
        value = self.value(rows, "cancel")
        self.assertFalse(value["result"].get("timeout"), "用户取消必须立刻有结果")
        self.assertFalse(value["result"].get("v"), "取消时不得放行")
        self.assertEqual(["request-leave"], value["commands"],
                         f"取消后不得再发任何命令，实际 {value['commands']}")
        self.assertEqual([], value["errors"],
                         f"用户主动取消是 quiet 的，不该产生失败事件：{value['errors']}")
        self.assertEqual("", value["state_error"], "取消不得污染 snapshot().error")

    def test_discard_then_leave(self):
        rows = run_jobs([job("discard", ATTACHED + """
            await H.deliverState('dirty-state', { dirty: true, reason: 'edit', source: 'board' });
            const settled = settle(bridge.guardLeave('prev', { target: 'drawing' }));
            await H.sleep(40);
            await H.deliverState('dirty-state', { dirty: false, reason: 'discarded', source: 'board' });
            const result = await Promise.race([
              settled, H.sleep(1500).then(function () { return { timeout: true }; })]);
            return { result: result, commands: H.commandNames() };
        """, modules=[str(BRIDGE_MODULE)],
            replies={"request-leave": {"ok": True, "result": {"decision": "discard"}},
                     "discard": {"ok": True, "result": {"discarded": True}}})])
        value = self.value(rows, "discard")
        self.assertTrue(value["result"].get("v"), f"放弃修改后必须放行，实际 {value['result']}")
        self.assertEqual(["request-leave", "discard"], value["commands"],
                         f"必须先问再丢，实际 {value['commands']}")

    def test_leave_approved_is_one_shot(self):
        rows = run_jobs([job("approved", ATTACHED + """
            await H.deliverState('dirty-state', { dirty: true, reason: 'edit', source: 'board' });
            const settled = settle(bridge.guardLeave('next', { target: 'summary' }));
            H.deliverState('leave-approved', { reason: 'next', target: 'summary', decision: 'save' });
            const first = await Promise.race([
              settled, H.sleep(1500).then(function () { return { timeout: true }; })]);
            const second = await Promise.race([
              settle(bridge.guardLeave('next', { target: 'summary' })),
              H.sleep(250).then(function () { return { pending: true }; })]);
            return { first: first, second: second, dirty: bridge.snapshot().dirty };
        """, modules=[str(BRIDGE_MODULE)],
            replies={"request-leave": {"ok": True, "result": {"decision": "cancel"}}})])
        value = self.value(rows, "approved")
        self.assertTrue(value["first"].get("v"),
                        "看板自己发来的 leave-approved 必须放行（否则用户确认过一次还被拦）")
        self.assertFalse(value["dirty"], "leave-approved 之后 dirty 必须被清掉")
        blocked_again = value["second"].get("pending") is True or value["second"].get("v") is False
        self.assertTrue(blocked_again,
                        "leave-approved 是一次性的：第二次导航必须重新被拦（不能无审放行）")

    def test_concurrent_guard_leaves_are_deduplicated(self):
        rows = run_jobs([job("dedupe", ATTACHED + """
            await H.deliverState('dirty-state', { dirty: true, reason: 'edit', source: 'board' });
            const a = settle(bridge.guardLeave('next', { target: 'summary' }));
            const b = settle(bridge.guardLeave('next', { target: 'summary' }));
            await H.sleep(40);
            const midway = H.commandNames();
            await H.deliverState('dirty-state', { dirty: false, reason: 'saved', source: 'server' });
            const both = await Promise.race([
              Promise.all([a, b]), H.sleep(1500).then(function () { return [{ timeout: true }, { timeout: true }]; })]);
            return { both: both, midway: midway,
                     leave_count: H.posted.filter(function (m) { return m.name === 'request-leave'; }).length };
        """, modules=[str(BRIDGE_MODULE)],
            replies={"request-leave": {"ok": True, "result": {"decision": "save"}},
                     "save-draft": {"ok": True, "result": {"saved": True}}})])
        value = self.value(rows, "dedupe")
        self.assertEqual(1, value["leave_count"],
                         f"连点下一步只能发一条 request-leave，实际 {value['leave_count']}")
        self.assertEqual("request-leave", value["midway"][0], f"中途命令：{value['midway']}")
        self.assertEqual([True, True], [bool(item.get("v")) for item in value["both"]],
                         f"两次调用必须共用同一结果，实际 {value['both']}")

    def test_detach_mid_handshake_blocks_and_stops(self):
        rows = run_jobs([job("detach", ATTACHED + """
            await H.deliverState('dirty-state', { dirty: true, reason: 'edit', source: 'board' });
            const settled = settle(bridge.guardLeave('next', { target: 'summary' }));
            await H.sleep(20);
            bridge.detach('frame-replaced');
            const result = await Promise.race([
              settled, H.sleep(1200).then(function () { return { timeout: true }; })]);
            await H.sleep(30);
            return { result: result, commands: H.commandNames() };
        """, modules=[str(BRIDGE_MODULE)], noReply=["request-leave"])])
        value = self.value(rows, "detach")
        self.assertFalse(value["result"].get("timeout"), "看板被切走时必须立刻结束握手")
        self.assertFalse(value["result"].get("v"), "看板被切走时不得放行")
        if "err" in value["result"]:
            self.assertEqual("detached", value["result"]["code"],
                             "看板切走的失败码必须是 detached（quiet，不能刷成故障）")
        self.assertEqual(["request-leave"], value["commands"],
                         f"切走后不得补发 save-draft / discard：{value['commands']}")

    def test_board_refusal_and_timeout_are_loud(self):
        rows = run_jobs([job("refuse", ATTACHED + """
            await H.deliverState('dirty-state', { dirty: true, reason: 'edit', source: 'board' });
            const result = await Promise.race([
              settle(bridge.guardLeave('next', { target: 'summary' })),
              H.sleep(1500).then(function () { return { timeout: true }; })]);
            return { result: result, errors: errorNames() };
        """, modules=[str(BRIDGE_MODULE)],
            replies={"request-leave": {"ok": False, "error": {"code": "leave-refused",
                                                              "message": "看板保存失败，请先处理"}}})])
        value = self.value(rows, "refuse")
        self.assertFalse(value["result"].get("v"), "看板拒绝时不得放行")
        self.assertTrue(value["errors"], "看板拒绝是真实失败，必须留下非 quiet 的失败事件")

        rows = run_jobs([job("timeout", ATTACHED + """
            await H.deliverState('dirty-state', { dirty: true, reason: 'edit', source: 'board' });
            const result = await Promise.race([
              settle(bridge.guardLeave('next', { target: 'summary', timeout: 120 })),
              H.sleep(1500).then(function () { return { timeout: true }; })]);
            return { result: result, errors: errorNames() };
        """, modules=[str(BRIDGE_MODULE)], noReply=["request-leave"])])
        value = self.value(rows, "timeout")
        self.assertFalse(value["result"].get("timeout"), "超时必须自己收敛，不能一直挂着")
        self.assertFalse(value["result"].get("v"), "未确认是否已保存时不得放行（宁可多问一句）")
        self.assertTrue(value["errors"], "超时必须留一条非 quiet 的失败")

    def test_dirty_state_sources_and_missing_field(self):
        rows = run_jobs([job("sources", ATTACHED + """
            const out = {};
            bridge.markDirty('agent-autofill');
            out.after_mark = bridge.snapshot().dirty;
            out.warn = bridge.shouldWarnOnUnload();
            await H.deliverState('dirty-state', { dirty: false, reason: 'saved', source: 'server' });
            out.after_clean = bridge.snapshot().dirty;
            await H.deliverState('dirty-state', { reason: 'edit', source: 'agent' });
            out.after_missing = bridge.snapshot().dirty;
            return out;
        """, modules=[str(BRIDGE_MODULE)])])
        value = self.value(rows, "sources")
        self.assertTrue(value["after_mark"], "Agent 自动回填必须能把看板标脏")
        self.assertTrue(value["warn"], "标脏后 shouldWarnOnUnload 必须为 true")
        self.assertFalse(value["after_clean"], "看板报 dirty=false 时必须清掉")
        self.assertTrue(value["after_missing"],
                        "dirty-state 缺 dirty 字段时必须按 true 处理（宁可多问一句）")

    def test_existing_bridge_behaviour_does_not_regress(self):
        rows = run_jobs([job("legacy", ATTACHED + """
            const before = bridge.snapshot();
            bridge.markDirty('probe');
            const guard = await bridge.executeAction('noop');
            const unsub = bridge.subscribe(function () {});
            unsub();
            return { snapshot_keys: Object.keys(bridge.snapshot()).sort(),
                     had_attached: before.attached, had_ready: before.ready,
                     guard_ok: Boolean(guard && guard.ok),
                     quiet: (bridge.QUIET_FAILURE_CODES || []).slice(),
                     namespace: bridge.namespace, version: bridge.version };
        """, modules=[str(BRIDGE_MODULE)])])
        value = self.value(rows, "legacy")
        self.assertTrue(value["had_attached"], "attach 之后 attached 必须为 true")
        self.assertTrue(value["had_ready"], "sync-state 之后 ready 必须为 true")
        self.assertTrue(value["guard_ok"], "executeAction 的既有行为不得回退")
        self.assertEqual("cpq:tech-board", value["namespace"])
        self.assertEqual(1, value["version"])
        for name in ("attached", "ready", "stage", "projectId", "actions", "view", "busy",
                     "error", "lastEvent"):
            self.assertIn(name, value["snapshot_keys"], f"snapshot() 不得丢字段 {name}")
        for code in ("detached", "note-target-missing", "missing-comment", "no-selection"):
            self.assertIn(code, value["quiet"], f"QUIET_FAILURE_CODES 不得丢 {code}")


class WorkbenchNavigationGuardContractTest(unittest.TestCase):
    """脚本路由存在性（静态契约）：五个出口必须共用同一个导航闸门。"""

    def setUp(self):
        self.text = WORKBENCH_MODULE.read_text(encoding="utf-8")

    def test_single_guard_funnel(self):
        text = self.text
        defs = len(re.findall(r"function\s+guardedStage\s*\(", text))
        self.assertEqual(1, defs,
                         "tech-workbench.js 必须有且只有一个导航闸门函数 guardedStage（Spec §6.5）")
        guard_calls = len(re.findall(r"guardLeave\s*\(", text))
        self.assertEqual(1, guard_calls,
                         "guardLeave 只允许在 guardedStage 内调用一次（不允许各出口各写一套）")
        calls = len(re.findall(r"guardedStage\s*\(", text))
        self.assertGreaterEqual(calls, 6,
                                f"一个定义 + 五个出口调用，实际只有 {calls} 处")
        self.assertIn("beforeunload", text, "必须有 beforeunload 兜底")
        self.assertIn("shouldWarnOnUnload", text, "beforeunload 必须用 shouldWarnOnUnload 判定")

    def test_each_exit_routes_through_the_guard(self):
        text = self.text
        anchors = [
            ("[data-major-step]", "\u9876\u90e8\u5927\u6b65\u9aa4"),
            ("prevBtn.addEventListener", "\u4e0a\u4e00\u6b65"),
            ("nextBtn.addEventListener", "\u4e0b\u4e00\u6b65"),
            ("addEventListener('popstate'", "\u6d4f\u89c8\u5668\u524d\u8fdb/\u540e\u9000"),
            ("'cpq:tech-workbench:exit'", "\u9000\u51fa\u9879\u76ee"),
        ]
        missing = []
        for anchor, label in anchors:
            index = text.find(anchor)
            if index < 0:
                missing.append(f"{label}：找不到出口锚点 {anchor}")
                continue
            if "guardedStage(" not in text[index:index + 900]:
                missing.append(f"{label}：出口没有走 guardedStage")
        self.assertEqual([], missing, "；".join(missing) or "全部出口都已走同一闸门")


if __name__ == "__main__":
    unittest.main()
