"""项目会话时间线：所有会话条目统一持久化 + 按同一顺序拼接。

背景（用户反馈 + 本次实测）：
  · 重新进入项目后左侧只剩 Agent 对话；通过右侧看板按钮或左侧业务快捷按钮跑出来的
    过程卡与完成提示全部消失 —— `renderTaskProgress()`（agent-chat.js:1220 起）与各
    阶段页的 `*Say()`（cost-review.js:77、assembly-integration.js:82）只写 DOM，
    既不调用任何会话持久化接口，也不写项目数据；
  · 同一条会话线程里同时存在两套顺序规则：Agent 消息按时间追加进 `#ocTinner`，任务卡
    却永远追加进 `#ocTaskProgressHost`（tech-workbench.html:78，位于 `#ocTinner` 末尾），
    文件里自己的注释就写着「建出来就永远钉在底部，聊多少轮都不动」；
  · `renderHistory()`（agent-chat.js:236）回放时还主动跳过 `tech_ui`
    （agent-chat.js:254 `if (event.name === "tech_ui") return;`），所以即使事件已经写进
    OpenClaude JSONL，重进项目也不会恢复。

新契约见 docs/specs/tech-session-timeline-persistence-and-order.md：
  后端 `store.append_session_event()` / `load_session_events()`（project 级 append-only、
  `seq` 等于追加顺序、`key` 幂等且就地更新、同一 `task.id` 只留一张卡）；
  路由 `POST /agent/event` / `GET /agent/events`，并把 `timeline` 扩展进既有
  `GET /agent/history`（Agent 层不可用时仍要返回本地条目）；
  前端唯一顺序实现 `tech_app/frontend/tech-session-timeline.js`（`merge` / `forShell` /
  `forStage` / `applyTaskProgress`，按 `(ts, seq)` 稳定升序）；
  父壳不再把任务卡钉进常驻宿主、改为按顺序进 `#ocTinner` 并落库（节点本身保留，避免破坏
  其它批次的防缩水守卫）。

验证方式：
  · 后端行为与路由用带 fastapi/pydantic 的解释器（本机为 open-claude/.venv/bin/python）
    在子进程里真跑 store 与 TestClient（临时 DATA_DIR、假项目、打桩的 Agent 历史层，
    不联网、不碰真实运行数据）；
  · 前端纯函数用 Node 的 vm 加载真实模块驱动（不是源码文本推断）；
  · 接线（不再钉底、不再跳过 tech_ui、阶段页落库）做源码契约断言。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
WORKBENCH_HTML = FRONTEND / "tech-workbench.html"
AGENT_CHAT = FRONTEND / "agent-chat.js"
TIMELINE_MODULE = FRONTEND / "tech-session-timeline.js"
VENV_PY = ROOT / "open-claude" / ".venv" / "bin" / "python"

STAGE_THREAD_FILES = {
    "cost": "cost-review.js",
    "process": "assembly-integration.js",
    "drawing": "app.js",
}

# 既有 agent 会话路由 / 既有 history 字段：本批只允许「扩展」，不允许消失。
EXISTING_AGENT_ROUTES = [
    '/api/projects/{project_id}/agent/meta',
    '/api/projects/{project_id}/agent/history',
    '/api/projects/{project_id}/agent/send',
    '/api/projects/{project_id}/agent/new',
]
HISTORY_FIELDS = ["project_id", "session_id", "messages", "message_count"]


def read(path) -> str:
    """agent-chat.js 是 CRLF + NUL 的混合体，先清 NUL 再按 utf-8 容错解码。"""
    return (ROOT / path).read_bytes().replace(b"\x00", b"").decode("utf-8", "replace")


CHILD = r'''
import json
import os
import sys
import types

data_dir, root, case = sys.argv[1], sys.argv[2], sys.argv[3]
pid_arg = sys.argv[4] if len(sys.argv) > 4 else ""
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


def new_project(tag):
    return store.create_project(source_filename="timeline.dxf", source_bytes=b"x",
                                note="timeline probe " + tag, owner="tester")


def task_event(task_id, status, steps, key, text="一键测算全部成本", stage="cost"):
    return {"kind": "task", "source": "board", "stage": stage, "text": text,
            "task": {"id": task_id, "label": "成本测算", "status": status,
                     "steps": list(steps)},
            "key": key}


def note_event(text, stage, key=""):
    return {"kind": "session-note", "source": "board", "stage": stage,
            "text": text, "key": key}


try:
    if case == "store_append":
        pid = new_project("append")
        first = store.append_session_event(pid, {
            "kind": "user", "source": "shell", "stage": "cost", "text": "一键测算全部成本"})
        second = store.append_session_event(pid, note_event("测算完成。整机成本 12.3 万", "cost"))
        dump({"pid": pid, "first": first, "second": second,
              "loaded": store.load_session_events(pid)})

    elif case == "store_load":
        dump({"pid": pid_arg, "loaded": store.load_session_events(pid_arg)})

    elif case == "store_upsert":
        pid = new_project("upsert")
        first = store.append_session_event(pid, task_event("T1", "running", ["读取零件"], "task:T1"))
        second = store.append_session_event(pid, task_event("T1", "running", ["读取零件", "匹配工艺"], "task:T1"))
        third = store.append_session_event(pid, task_event("T1", "succeeded", ["读取零件", "匹配工艺"], "task:T1"))
        dump({"pid": pid, "first": first, "second": second, "third": third,
              "loaded": store.load_session_events(pid)})

    elif case == "store_no_key_appends":
        pid = new_project("nokey")
        probes = [note_event("第一行", "cost"), note_event("第二行", "cost")]
        wrote = [store.append_session_event(pid, item) for item in probes]
        dump({"pid": pid, "wrote": wrote, "loaded": store.load_session_events(pid)})

    elif case == "store_idempotent_key":
        pid = new_project("idem")
        body = note_event("测算完成。整机成本 12.3 万", "cost", key="cost:done:1")
        first = store.append_session_event(pid, dict(body))
        second = store.append_session_event(pid, dict(body))
        dump({"pid": pid, "first": first, "second": second,
              "loaded": store.load_session_events(pid)})

    elif case in ("route_event", "route_events", "route_history"):
        from starlette.testclient import TestClient
        from tech_app.backend.services import oc_agent
        import tech_app.backend.main as main

        pid = new_project("route")
        store.append_session_event(pid, task_event("T1", "running", ["读取零件"], "task:T1"))
        store.append_session_event(pid, note_event("成本已确认", "summary", key="summary:1"))
        client = TestClient(main.app, raise_server_exceptions=False)

        def boom(_pid):
            raise oc_agent.AgentUnavailable("no agent layer in probe")

        if case == "route_event":
            response = client.post("/api/projects/%s/agent/event" % pid, json={
                "kind": "session-note", "source": "board", "stage": "cost",
                "text": "刚刚写进去的一行", "key": "cost:route:1"})
            dump({"pid": pid, "status": response.status_code,
                  "body": response.json() if response.content else None,
                  "loaded": store.load_session_events(pid)})

        elif case == "route_events":
            all_resp = client.get("/api/projects/%s/agent/events" % pid)
            cost_resp = client.get("/api/projects/%s/agent/events?stage=cost" % pid)
            board_resp = client.get("/api/projects/%s/agent/events?source=board" % pid)
            dump({"pid": pid, "all": all_resp.json(), "cost": cost_resp.json(),
                  "board": board_resp.json(), "status": all_resp.status_code,
                  "cost_status": cost_resp.status_code, "board_status": board_resp.status_code})

        else:
            main.oc_agent.load_history = boom
            response = client.get("/api/projects/%s/agent/history" % pid)
            dump({"pid": pid, "status": response.status_code, "body": response.json()})

    else:
        dump({"_error": "unknown case: " + case})

except Exception as exc:  # noqa: BLE001 - 红测要把真实缺口类型回给断言
    dump({"_error": "%s: %s" % (type(exc).__name__, exc), "_error_type": type(exc).__name__})
'''


TIMELINE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const code = fs.readFileSync(process.argv[2], 'utf8');
const env = { console: console, setTimeout: setTimeout, clearTimeout: clearTimeout,
              Date: Date, JSON: JSON, Math: Math, Object: Object, Array: Array,
              Map: Map, Set: Set, String: String, Number: Number, Boolean: Boolean };
env.window = env;
env.globalThis = env;
vm.runInContext(code, vm.createContext(env), { filename: 'tech-session-timeline.js' });

const mod = env.TechSessionTimeline || (env.window && env.window.TechSessionTimeline);
if (!mod) throw new Error('tech-session-timeline.js 没有导出 window.TechSessionTimeline');

const tag = e => `${e.kind || e.type || '?'}` + ((e.task && e.task.id) ? `:${e.task.id}` : '');
const text = e => String(e.text || (e.task && e.task.label) || '');
const steps = e => ((e.task && e.task.steps) || []).slice();
const out = {};

// A. merge：Agent 回放消息与本地事件必须交错成一条时间线，而不是两段拼接。
{
  const messages = [
    { type: 'user', ts: '2026-09-15T10:00:00+08:00', text: '开始解析' },
    { type: 'assistant', ts: '2026-09-15T10:00:04+08:00', text: '好的' },
  ];
  const events = [
    { seq: 1, ts: '2026-09-15T10:00:02+08:00', kind: 'task', source: 'board', stage: 'cost',
      task: { id: 'T1', label: '成本测算', status: 'running', steps: ['读取零件'] } },
    { seq: 2, ts: '2026-09-15T10:00:06+08:00', kind: 'session-note', source: 'board',
      stage: 'cost', text: '测算完成' },
  ];
  out.merge_order = mod.merge({ events: events, messages: messages }).map(tag);
}

// B. 同一 ts 用 seq 兜底；seq 也缺失时保持原顺序（稳定）。
{
  const tied = [
    { seq: 3, ts: '2026-09-15T10:00:00+08:00', kind: 'session-note', text: 'third' },
    { seq: 1, ts: '2026-09-15T10:00:00+08:00', kind: 'session-note', text: 'first' },
  ];
  out.tie_seq_order = mod.merge({ events: tied, messages: [] }).map(text);
  const unstable = [
    { kind: 'session-note', text: 'A' },
    { kind: 'session-note', text: 'B' },
  ];
  out.stable_no_ts = mod.merge({ events: unstable, messages: [] }).map(text);
}

// C. forShell：排除 source:board 的过程文字，保留 Agent 消息 / task / tech_ui / shell note。
{
  const events = [
    { seq: 1, ts: '2026-09-15T10:00:01+08:00', kind: 'session-note', source: 'board',
      stage: 'cost', text: '一键测算全部成本…' },
    { seq: 2, ts: '2026-09-15T10:00:02+08:00', kind: 'session-note', source: 'shell',
      text: '已打开项目' },
    { seq: 3, ts: '2026-09-15T10:00:03+08:00', kind: 'task', source: 'board', stage: 'cost',
      task: { id: 'T1', label: '成本测算', status: 'succeeded', steps: ['读取零件'] } },
    { seq: 4, ts: '2026-09-15T10:00:04+08:00', kind: 'tech_ui', source: 'agent',
      stage: 'cost', ui: { action: 'focus_view' } },
  ];
  const messages = [{ type: 'assistant', ts: '2026-09-15T10:00:00+08:00', text: '好的' }];
  out.shell = mod.forShell({ events: events, messages: messages }).map(tag);
}

// D. forStage：只返回本阶段 source:board 的条目。
{
  const events = [
    { seq: 1, ts: '2026-09-15T10:00:01+08:00', kind: 'session-note', source: 'board',
      stage: 'process', text: '组装工艺已确认' },
    { seq: 2, ts: '2026-09-15T10:00:02+08:00', kind: 'session-note', source: 'board',
      stage: 'cost', text: '成本已确认' },
    { seq: 3, ts: '2026-09-15T10:00:03+08:00', kind: 'session-note', source: 'shell',
      stage: 'cost', text: '这是父壳的' },
    { seq: 4, ts: '2026-09-15T10:00:04+08:00', kind: 'user', source: 'agent',
      stage: 'cost', text: '用户消息' },
  ];
  out.stage_cost = mod.forStage({ events: events, stage: 'cost' }).map(text);
  out.stage_process = mod.forStage({ events: events, stage: 'process' }).map(text);
}

// E. applyTaskProgress：同一 task.id 只留一张卡，进度行按行去重追加，终态就地更新。
{
  const base = [
    { seq: 1, ts: '2026-09-15T10:00:00+08:00', kind: 'session-note', source: 'shell', text: '开始' },
    { seq: 2, ts: '2026-09-15T10:00:01+08:00', kind: 'task', source: 'board', stage: 'cost',
      task: { id: 'T1', label: '成本测算', status: 'running', steps: ['读取零件'] } },
    { seq: 3, ts: '2026-09-15T10:00:02+08:00', kind: 'session-note', source: 'shell', text: '结束' },
  ];
  const progressed = mod.applyTaskProgress(base, {
    id: 'T1', label: '成本测算', status: 'running', steps: ['读取零件', '匹配工艺'] });
  out.task_after_progress = progressed.map(tag);
  out.task_steps_after_progress = steps(progressed.find(e => tag(e) === 'task:T1'));
  const done = mod.applyTaskProgress(progressed, {
    id: 'T1', label: '成本测算', status: 'succeeded', steps: ['读取零件', '匹配工艺'] });
  out.task_after_done = done.map(tag);
  out.task_status_after_done = (done.find(e => tag(e) === 'task:T1') || {}).task.status;
  out.task_count_after_done = done.filter(e => tag(e) === 'task:T1').length;
}

// F. dedupe：同 key 只留一条，位置取先出现的那条。
{
  const list = [
    { seq: 1, ts: '2026-09-15T10:00:00+08:00', kind: 'session-note', key: 'k', text: '旧' },
    { seq: 2, ts: '2026-09-15T10:00:01+08:00', kind: 'session-note', key: 'other', text: '别的' },
    { seq: 3, ts: '2026-09-15T10:00:02+08:00', kind: 'session-note', key: 'k', text: '新' },
  ];
  out.dedupe = mod.dedupe(list).map(text);
}

// G. append：追加一条后仍按 (ts, seq) 稳定升序。
{
  const list = [{ seq: 1, ts: '2026-09-15T10:00:00+08:00', kind: 'session-note', text: 'A' }];
  const next = mod.append(list, { seq: 2, ts: '2026-09-15T09:59:59+08:00',
                                  kind: 'session-note', text: 'B' });
  out.append_order = next.map(text);
  out.append_input_untouched = list.map(text);
}

// H. normalize：统一补齐 kind（Agent 回放消息用 type，事件用 kind），不改原对象。
{
  const raw = { seq: 7, ts: '2026-09-15T10:00:00+08:00', kind: 'session-note', text: 'x' };
  const entry = mod.normalize(raw);
  out.normalize_kind = entry.kind;
  out.normalize_seq = entry.seq;
  out.normalize_input_untouched = raw.kind === 'session-note' && Object.keys(raw).length === 4
    && raw.seq === 7 && raw.text === 'x';
  const fromType = mod.normalize({ type: 'assistant', text: 'y' });
  out.normalize_kind_from_type = fromType.kind;
}

console.log(JSON.stringify(out));
"""


def _interpreter() -> str:
    for candidate in (VENV_PY, Path(sys.executable)):
        if candidate and Path(candidate).exists():
            probe = subprocess.run(
                [str(candidate), "-c", "import fastapi, pydantic, starlette"],
                capture_output=True, text=True)
            if probe.returncode == 0:
                return str(candidate)
    raise unittest.SkipTest(
        "没有带 FastAPI/Starlette 的解释器（本机为 open-claude/.venv/bin/python），跳过后端时间线测试")


def run_child(case: str, data_dir: str, pid: str = "", timeout: int = 240) -> dict:
    python = _interpreter()
    workdir = tempfile.mkdtemp(prefix="tech-timeline-child-")
    try:
        script = Path(workdir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run(
            [python, str(script), data_dir, str(ROOT), case, pid],
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


def run_timeline_harness() -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("未安装 node，无法执行会话时间线纯函数测试")
    if not TIMELINE_MODULE.exists():
        raise AssertionError(
            "缺少前端纯函数模块 tech_app/frontend/tech-session-timeline.js —— "
            "时间线的唯一顺序 / 去重实现必须住在这里（父壳与阶段页共用）")
    workdir = tempfile.mkdtemp(prefix="tech-timeline-node-")
    try:
        script = Path(workdir) / "harness.js"
        script.write_text(TIMELINE_HARNESS, encoding="utf-8")
        completed = subprocess.run(
            [node, str(script), str(TIMELINE_MODULE)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "时间线 harness 执行失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout, completed.stderr))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class TimelineStoreBackend(unittest.TestCase):
    """项目级会话时间线存储：追加有序、可重启恢复、key 幂等、同一 task 只留一张卡。"""

    @classmethod
    def setUpClass(cls):
        cls.data_dir = tempfile.mkdtemp(prefix="tech-timeline-store-")
        cls.append_data = run_child("store_append", cls.data_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def _error(self, data: dict):
        self.assertNotIn("_error", data, "会话时间线的后端能力尚不存在：%s" % data.get("_error"))

    def test_append_returns_stored_entry_with_server_seq(self):
        data = self.append_data
        self._error(data)
        first, second = data["first"], data["second"]
        self.assertEqual(1, first["seq"], first)
        self.assertEqual(2, second["seq"], second)
        self.assertEqual("user", first["kind"])
        self.assertEqual("session-note", second["kind"])
        self.assertIn("ts", first, "条目必须带服务端时间戳")

    def test_load_returns_events_in_append_order(self):
        data = self.append_data
        self._error(data)
        loaded = data["loaded"]
        self.assertEqual([1, 2], [row["seq"] for row in loaded], loaded)
        self.assertEqual("一键测算全部成本", loaded[0]["text"])

    def test_timeline_survives_process_restart(self):
        data = self.append_data
        self._error(data)
        again = run_child("store_load", self.data_dir, data["pid"])
        self._error(again)
        loaded = again["loaded"]
        self.assertEqual([1, 2], [row["seq"] for row in loaded],
                         "重启（新进程、同一 DATA_DIR）后时间线必须还在：%s" % loaded)
        self.assertEqual("测算完成。整机成本 12.3 万", loaded[1]["text"])

    def test_same_task_id_keeps_single_entry_with_merged_steps(self):
        data = run_child("store_upsert", self.data_dir)
        self._error(data)
        loaded = data["loaded"]
        self.assertEqual(1, len(loaded), "同一 task.id 在时间线里只能有一条条目：%s" % loaded)
        entry = loaded[0]
        self.assertEqual(1, entry["seq"], "就地更新必须保留原 seq（原地不动）：%s" % entry)
        self.assertEqual(["读取零件", "匹配工艺"], entry["task"]["steps"])
        self.assertEqual("succeeded", entry["task"]["status"])
        self.assertEqual(data["first"]["seq"], data["third"]["seq"])

    def test_same_key_is_idempotent_and_keeps_seq(self):
        data = run_child("store_idempotent_key", self.data_dir)
        self._error(data)
        self.assertEqual(1, len(data["loaded"]), "同一 key 重复提交只能留一条：%s" % data["loaded"])
        self.assertEqual(data["first"]["seq"], data["second"]["seq"])

    def test_entries_without_key_always_append_new_rows(self):
        data = run_child("store_no_key_appends", self.data_dir)
        self._error(data)
        self.assertEqual(2, len(data["loaded"]), data["loaded"])
        self.assertNotEqual(data["wrote"][0]["seq"], data["wrote"][1]["seq"])

    def test_store_keeps_existing_business_storage_api(self):
        source = read("tech_app/backend/storage/store.py")
        for name in ("create_project", "load_meta", "save_requirement", "list_audit",
                     "append_session_event", "load_session_events"):
            with self.subTest(symbol=name):
                self.assertRegex(source, r"\ndef %s\(" % name)


class TimelineRoutes(unittest.TestCase):
    """时间线读写路由：新增两条 + 既有 /agent/history 扩展，且一个既有字段都不删。"""

    @classmethod
    def setUpClass(cls):
        cls.data_dir = tempfile.mkdtemp(prefix="tech-timeline-route-")
        cls.event_post = run_child("route_event", cls.data_dir)
        cls.events_get = run_child("route_events", cls.data_dir)
        cls.history_get = run_child("route_history", cls.data_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def _error(self, data: dict):
        self.assertNotIn("_error", data, "时间线路由尚不存在：%s" % data.get("_error"))

    def test_existing_agent_routes_are_all_still_registered(self):
        source = read("tech_app/backend/main.py")
        for route in EXISTING_AGENT_ROUTES:
            with self.subTest(route=route):
                self.assertIn('"%s"' % route, source,
                              "既有会话路由被删或改名（任何一批改造都不允许缩减路由）")

    def test_post_agent_event_persists_and_returns_seq(self):
        data = self.event_post
        self._error(data)
        self.assertEqual(200, data["status"], data)
        body = data["body"] or {}
        self.assertIn("seq", body, "POST /agent/event 必须返回服务端分配的 seq：%s" % body)
        self.assertIn("event", body, body)
        self.assertEqual(body["seq"], body["event"]["seq"])
        self.assertEqual(1, len([row for row in data["loaded"] if row.get("key") == "cost:route:1"]),
                         "路由写入的条目必须真的落库：%s" % data["loaded"])

    def test_get_agent_events_filters_and_orders_ascending(self):
        data = self.events_get
        self._error(data)
        self.assertEqual(200, data["status"], data)
        body = data["all"]
        self.assertEqual([1, 2], [row["seq"] for row in body["events"]], body)
        self.assertEqual(2, body["count"], body)
        self.assertEqual(200, data["cost_status"], data)
        self.assertEqual(["cost"], [row["stage"] for row in data["cost"]["events"]],
                         "?stage= 必须只返回该阶段条目：%s" % data["cost"])
        self.assertEqual(200, data["board_status"], data)
        self.assertTrue(all(row["source"] == "board" for row in data["board"]["events"]),
                        "?source= 必须只返回该来源条目：%s" % data["board"])

    def test_history_keeps_fields_and_adds_timeline(self):
        data = self.history_get
        self._error(data)
        self.assertEqual(200, data["status"], data)
        body = data["body"]
        for field in HISTORY_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, body, "/agent/history 不允许删除既有字段 %s：%s" % (field, body))
        self.assertIn("timeline", body, "/agent/history 必须扩展返回 timeline：%s" % body)
        self.assertIs(False, body.get("available"))
        self.assertEqual([1, 2], [row["seq"] for row in body["timeline"]],
                         "Agent 层不可用时仍要返回持久化的本地条目：%s" % body["timeline"])


class TimelinePureModule(unittest.TestCase):
    """前端唯一顺序实现：合并 / 归属 / 同一张任务卡 / 去重 / 追加，全部按 (ts, seq) 稳定。"""

    @classmethod
    def setUpClass(cls):
        cls.data = {}
        cls.error = ""
        try:
            cls.data = run_timeline_harness()
        except unittest.SkipTest:
            raise
        except AssertionError as exc:
            # 模块缺失 / 跑不起来时让每个用例各自失败并带上原因，不要吞成一处 setUpClass 错误。
            cls.error = str(exc)

    def setUp(self):
        if self.error:
            self.fail(self.error)

    def test_merge_interleaves_messages_and_local_events(self):
        self.assertEqual(
            ["user", "task:T1", "assistant", "session-note"], self.data["merge_order"],
            "merge 必须按 (ts, seq) 交错，而不是「消息全在前、事件全在后」")

    def test_merge_falls_back_to_seq_then_keeps_original_order(self):
        self.assertEqual(["first", "third"], self.data["tie_seq_order"],
                         "同一 ts 必须用 seq 兜底")
        self.assertEqual(["A", "B"], self.data["stable_no_ts"],
                         "ts 与 seq 都缺失时必须保持原顺序（稳定排序）")

    def test_for_shell_excludes_board_notes_only(self):
        self.assertEqual(["assistant", "session-note", "task:T1", "tech_ui"], self.data["shell"],
                         "forShell 只排除 source:board 的过程文字，Agent 消息 / task / tech_ui / shell note 都要留")

    def test_for_stage_returns_only_that_stage_board_entries(self):
        self.assertEqual(["成本已确认"], self.data["stage_cost"], self.data)
        self.assertEqual(["组装工艺已确认"], self.data["stage_process"], self.data)

    def test_apply_task_progress_keeps_one_card_in_place(self):
        self.assertEqual(["session-note", "task:T1", "session-note"], self.data["task_after_progress"],
                         "同一 task.id 只留一张卡，且不能挪动它的位置")
        self.assertEqual(["读取零件", "匹配工艺"], self.data["task_steps_after_progress"],
                         "进度行按行去重追加")
        self.assertEqual(1, self.data["task_count_after_done"])
        self.assertEqual("succeeded", self.data["task_status_after_done"],
                         "task-completed 就地更新同一张卡的状态")

    def test_dedupe_keeps_first_position_with_latest_content(self):
        self.assertEqual(["新", "别的"], self.data["dedupe"],
                         "同 key 去重后位置取先出现的那条，内容取最后写入的那条")

    def test_append_returns_sorted_copy_without_mutating_input(self):
        self.assertEqual(["B", "A"], self.data["append_order"])
        self.assertEqual(["A"], self.data["append_input_untouched"],
                         "append 必须返回新数组，不改调用方传进来的列表")

    def test_normalize_fills_kind_without_mutating_input(self):
        self.assertEqual("session-note", self.data["normalize_kind"], self.data)
        self.assertEqual(7, self.data["normalize_seq"])
        self.assertEqual("assistant", self.data["normalize_kind_from_type"],
                         "Agent 回放消息只有 type，normalize 必须补齐 kind")
        self.assertIs(True, self.data["normalize_input_untouched"],
                      "normalize 必须返回新对象，不改调用方传进来的条目")


class TimelineParentShellWiring(unittest.TestCase):
    """父壳：任务卡不再钉在常驻宿主、按顺序进 #ocTinner、回放 timeline 且不再跳过 tech_ui。"""

    def test_pinned_host_is_no_longer_the_task_card_insertion_point(self):
        source = read("tech_app/frontend/agent-chat.js")
        match = re.search(r"function taskProgressHost\(\)\s*\{([^}]*)\}", source)
        self.assertIsNotNone(match, "agent-chat.js 缺少 taskProgressHost()")
        body = match.group(1)
        self.assertNotIn("ocTaskProgressHost", body,
                         "任务卡插入点仍会回落到常驻宿主，卡片又会钉在会话底部、不按时间顺序")
        self.assertRegex(body, r"return\s+tinner",
                         "taskProgressHost() 必须直接返回 #ocTinner（唯一且按顺序的插入点）")

    def test_dom_host_node_is_kept_so_other_guards_do_not_shrink(self):
        html = read("tech_app/frontend/tech-workbench.html")
        self.assertIn('id="ocTaskProgressHost"', html,
                      "本批只改「卡片插到哪里」，不删节点 —— "
                      "多个批次的防缩水守卫（零件清单入口、空态区间、换项目清理）都引用它")
        self.assertIn("ocTaskProgressHost", read("tech_app/frontend/agent-chat.js"),
                      "换项目时仍要清掉宿主里的残留节点（既有可见状态清理契约）")

    def test_render_history_renders_timeline_and_no_longer_skips_tech_ui(self):
        source = read("tech_app/frontend/agent-chat.js")
        start = source.find("function renderHistory(")
        self.assertGreater(start, -1, "renderHistory() 不见了")
        block = source[start:start + 2600]
        self.assertNotRegex(block, r'event\.name\s*===\s*"tech_ui"\)\s*return',
                            "renderHistory() 不允许再跳过 tech_ui —— 重进项目必须恢复结构化卡片")
        self.assertRegex(source, r"TechSessionTimeline",
                         "父壳必须走统一的 tech-session-timeline 实现，而不是自己再造一套顺序")

    def test_timeline_module_loads_before_agent_chat(self):
        html = read("tech_app/frontend/tech-workbench.html")
        module_at = html.find("tech-session-timeline.js")
        chat_at = html.find("agent-chat.js")
        self.assertGreater(module_at, -1, "tech-workbench.html 没有加载 tech-session-timeline.js")
        self.assertGreater(chat_at, -1)
        self.assertLess(module_at, chat_at,
                        "tech-session-timeline.js 必须先于 agent-chat.js 加载")

    def test_shell_persists_entries_through_the_agent_event_route(self):
        source = read("tech_app/frontend/agent-chat.js")
        self.assertRegex(source, r'api\("/event"\)|/agent/event',
                         "父壳的会话条目必须调用 POST /agent/event 落库")
        self.assertNotRegex(source, r'if\s*\(name\s*===\s*"detached"\)\s*\{[^}]*clear\(\)',
                            "detached 不允许清空已落库的历史")


class TimelineStagePageWiring(unittest.TestCase):
    """阶段页：本地线程照旧可见，同时把同一条内容以 session-note 落库，并在加载后回放本阶段。"""

    def test_stage_thread_pages_use_the_shared_timeline_module(self):
        for stage, filename in STAGE_THREAD_FILES.items():
            with self.subTest(stage=stage, file=filename):
                source = read("tech_app/frontend/%s" % filename)
                self.assertRegex(source, r"TechSessionTimeline",
                                 "%s 必须复用统一的会话时间线实现" % filename)

    def test_stage_thread_pages_post_their_notes_to_the_timeline(self):
        for stage, filename in STAGE_THREAD_FILES.items():
            with self.subTest(stage=stage, file=filename):
                source = read("tech_app/frontend/%s" % filename)
                self.assertRegex(source, r"/agent/event",
                                 "%s 的会话文字必须经 POST /agent/event 落库" % filename)

    def test_stage_thread_pages_replay_their_own_stage_on_load(self):
        for stage, filename in STAGE_THREAD_FILES.items():
            with self.subTest(stage=stage, file=filename):
                source = read("tech_app/frontend/%s" % filename)
                self.assertRegex(source, r"/agent/events",
                                 "%s 加载后必须用 GET /agent/events 回放本阶段条目" % filename)

    def test_existing_local_thread_outlets_are_not_removed(self):
        outlets = {
            "cost-review.js": ["crAppend", "crSay", "crThread", "crTinner"],
            "assembly-integration.js": ["aiThreadAppend", "aiSay", "aiUserSay", "aiThread", "aiTinner"],
        }
        for filename, names in outlets.items():
            source = read("tech_app/frontend/%s" % filename)
            for name in names:
                with self.subTest(file=filename, symbol=name):
                    self.assertIn(name, source, "阶段页原有会话出口被删，本地可见性会缩水")


class TimelineNoShrinkage(unittest.TestCase):
    """不缩水：既有 Agent 协议、既有动作名、结果入口都不能因为本批被删。"""

    def test_agent_history_fields_are_still_returned_by_the_service(self):
        source = read("tech_app/backend/services/oc_agent.py")
        start = source.find("def load_history(")
        self.assertGreater(start, -1)
        block = source[start:start + 2200]
        for field in HISTORY_FIELDS:
            with self.subTest(field=field):
                self.assertIn('"%s"' % field, block,
                              "load_history() 不再返回 %s，/agent/history 会缩水" % field)

    def test_agent_unavailable_branch_keeps_its_shape(self):
        source = read("tech_app/backend/main.py")
        start = source.find("def agent_history(")
        self.assertGreater(start, -1)
        block = source[start:start + 1600]
        self.assertIn("AgentUnavailable", block)
        for field in ("messages", "message_count"):
            with self.subTest(field=field):
                self.assertIn(field, block)

    def test_result_actions_entry_stays_pinned(self):
        source = read("tech_app/frontend/agent-chat.js")
        self.assertIn("oc-result-actions", source,
                      "结果入口条是本批明确保留的常驻 affordance，不能被顺手删掉")


if __name__ == "__main__":
    unittest.main()
