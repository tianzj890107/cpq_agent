"""红测：任务框架的「过程事件」通道（模型调用 + 工具摘要按序进会话）。

用户反馈：右侧看板按钮跑出来的任务卡（例：需求资料解析）只有一个状态 chip 和一句进度，
看不到模型在干什么、用了哪个工具。

现状（已实测，非推断）：
  · 任务卡的正文只有 progress_log —— 后端任务的进度就是一句文字
    （services/tasks.py:201-212 → storage/store.py:236-257）；
  · 「需求资料解析」的 job 在后台线程里直接调模型（main.py:5894-5896），没有经过
    Agent 会话循环；thinking / tool_use 只在 /agent/send 的 SSE 里产生
    （services/oc_agent.py:3036-3040），任务线程永远不会产生这类事件；
  · 任务卡落库只存文字步骤（tasks.py:251-257 的 task.steps）。

本批给任务框架加一条一等的「过程事件」通道（phase = model / tool / progress），
一条序列、可回放；**不**播模型推理过程（后台任务没有推理流）。

Spec：docs/specs/tech-task-card-body-layout-and-process-stream.md（契约 B1–B11）
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
BACKEND = ROOT / "tech_app" / "backend"
SPEC = ROOT / "docs" / "specs" / "tech-task-card-body-layout-and-process-stream.md"
TASKS_PY = BACKEND / "services" / "tasks.py"
LLM_CLIENT_PY = BACKEND / "services" / "llm_client.py"
CLAUDE_CLIENT_PY = BACKEND / "services" / "claude_client.py"
QWEN_CLIENT_PY = BACKEND / "services" / "qwen_client.py"
MAIN_PY = BACKEND / "main.py"
STORE_PY = BACKEND / "storage" / "store.py"
TIMELINE_JS = FRONTEND / "tech-session-timeline.js"
CHAT_JS = FRONTEND / "agent-chat.js"
CHAT_CSS = FRONTEND / "agent-chat.css"

NODE = shutil.which("node")
PHASES = ("model", "tool", "progress")
# 载荷必须透传 process 的轮询点（文件 → 该文件里 progress_log 的锚点）。
PAYLOAD_SITES = {
    FRONTEND / "app.js": "progress_log",
    FRONTEND / "assembly-integration.js": "progress_log",
    FRONTEND / "cost-review.js": "progress_log",
    FRONTEND / "inline-analysis.js": "progress_log",
    FRONTEND / "requirement-create.js": "progress_log",
    FRONTEND / "agent-chat.js": "progress_log",
}


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def def_block(text: str, marker: str) -> str:
    """Python：从 marker 截到下一个顶层 def/class/装饰器。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    match = re.search(r"\n(?=(?:def|class|@|async def)\s)", text[idx + len(marker):])
    end = idx + len(marker) + match.start() if match else len(text)
    return text[idx:end]


def js_function_body(text: str, name: str) -> str:
    marker = f"function {name}("
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


def child_python() -> str:
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
    with tempfile.TemporaryDirectory(prefix="cpq-process-stream-") as tmp:
        path = pathlib.Path(tmp) / "probe.py"
        path.write_text(script, encoding="utf-8")
        completed = subprocess.run([child_python(), str(path), *args],
                                   capture_output=True, text=True, timeout=300)
        if completed.returncode != 0:
            raise AssertionError("子进程探针失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2500:],
                                    completed.stderr[-2500:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


def run_node(driver: str, *args: str) -> dict:
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端走查")
    with tempfile.TemporaryDirectory(prefix="cpq-process-js-") as tmp:
        script = pathlib.Path(tmp) / "driver.js"
        script.write_text(driver, encoding="utf-8")
        completed = subprocess.run([NODE, str(script), *args], capture_output=True,
                                   text=True, timeout=60)
        if completed.returncode != 0:
            raise AssertionError("JS 走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2500:],
                                    completed.stderr[-2500:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# 子进程：真跑任务框架 + 真打任务端点（打桩模型客户端，绝不联网、绝不真花钱）
# --------------------------------------------------------------------------- #
CHILD = r'''
import json
import os
import sys
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

from pydantic import BaseModel
from starlette.testclient import TestClient

import tech_app.backend.main as main
from tech_app.backend.services import claude_client, tasks
from tech_app.backend.storage import store

client = TestClient(main.app, raise_server_exceptions=False)
PID = store.create_project(source_filename="process-stream.png", source_bytes=b"png",
                           note="process stream probe " + case, owner="tester")
TERMINAL = {"succeeded", "failed", "partial", "interrupted", "cancelled", "stale"}
SYSTEM_PROMPT = "系统提示-绝不允许出现在过程流里"
USER_BODY = "用户输入原文-也绝不允许出现在过程流里"
API_KEY = "sk-probe-must-not-leak"


def dump(payload):
    print(json.dumps(payload, ensure_ascii=False, default=str))


def poll(task_id, timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get("/api/projects/%s/tasks/%s" % (PID, task_id))
        task = response.json()
        if task.get("status") in TERMINAL:
            return task
        time.sleep(0.2)
    return {"status": "timeout", "error": "轮询超时"}


class ProbeOut(BaseModel):
    value: str


class _Block:
    def __init__(self, data):
        self.type = "tool_use"
        self.name = claude_client._TOOL_NAME
        self.input = data


class _Response:
    def __init__(self, data):
        self.stop_reason = "end_turn"
        self.content = [_Block(data)]


class _Messages:
    def create(self, **kwargs):
        return _Response({"value": "ok"})


class _Client:
    def __init__(self):
        self.messages = _Messages()


def stub_model():
    """打桩：不发网络请求，但走完 claude_client.run() 的真实控制流。"""
    claude_client._route = lambda vision=False: {
        "model": "probe-model-9", "provider": "anthropic", "provider_label": "探针",
        "base_url": "http://127.0.0.1:1", "api_key": API_KEY}
    claude_client.get_client = lambda vision=False: _Client()
    claude_client._tuning = lambda: {"thinking": False, "max_tokens": 256}


def text_of(entries):
    return [str((entry or {}).get("text") or "") for entry in (entries or [])]


def stream_shape(task):
    entries = task.get("process_log") or []
    return {"count": len(entries),
            "phases": [(entry or {}).get("phase") for entry in entries],
            "seqs": [(entry or {}).get("seq") for entry in entries],
            "texts": text_of(entries)}


try:
    if case == "stream":
        stub_model()

        def job():
            tasks.report_progress("准备用料与费率")
            tasks.process_event("tool", "检索企业工艺库（P-001）")
            result = claude_client.run(SYSTEM_PROMPT,
                                       [claude_client.text_block(USER_BODY)], ProbeOut)
            tasks.report_progress("  ↳ 命中 3 条路线模板")
            return {"value": result.value}

        task_id = tasks.submit(PID, "process", job)
        task = poll(task_id)
        dump({"case": case, "task": task, "shape": stream_shape(task),
              "progress_log": task.get("progress_log"), "status": task.get("status"),
              "leak": [needle for needle in (SYSTEM_PROMPT, USER_BODY, API_KEY)
                       if any(needle in text for text in text_of(task.get("process_log")))]})

    elif case == "progress_only":
        def job():
            tasks.report_progress("准备用料与费率")
            tasks.report_progress("  ↳ 命中 3 条路线模板")
            return {}

        task = poll(tasks.submit(PID, "cost", job))
        dump({"case": case, "progress_log": task.get("progress_log"),
              "progress": task.get("progress"), "status": task.get("status")})

    elif case == "api":
        # 1) 空文本 no-op；2) 有任务上下文时非法 phase 必须报错。
        def job_ok():
            tasks.process_event("model", "")
            tasks.process_event("tool", "检索零部件库")
            tasks.report_progress("完成")
            return {}

        def job_bad():
            tasks.process_event("bogus", "非法阶段")
            return {}

        ok_task = poll(tasks.submit(PID, "library_lookup", job_ok))
        bad_task = poll(tasks.submit(PID, "cost", job_bad))
        # 3) 没有任务上下文时静默 no-op：不抛错、不新增任务。
        before = len(store.list_tasks(PID))
        outside_error = ""
        try:
            tasks.process_event("model", "不在任务线程里")
            tasks.process_event("tool", "")
        except Exception as exc:            # noqa: BLE001
            outside_error = "%s: %s" % (type(exc).__name__, exc)
        dump({"case": case, "ok": stream_shape(ok_task), "ok_status": ok_task.get("status"),
              "bad_status": bad_task.get("status"), "bad_error": str(bad_task.get("error") or ""),
              "outside_error": outside_error, "tasks_before": before,
              "tasks_after": len(store.list_tasks(PID))})

    elif case == "route":
        def job():
            tasks.process_event("tool", "检索企业成本库（P-002）")
            tasks.report_progress("按库内依据测算")
            return {}

        task_id = tasks.submit(PID, "cost", job)
        poll(task_id)
        body = client.get("/api/projects/%s/tasks/%s" % (PID, task_id)).json()
        dump({"case": case, "status": client.get(
            "/api/projects/%s/tasks/%s" % (PID, task_id)).status_code,
            "has_process_log": "process_log" in body,
            "has_progress_log": "progress_log" in body,
            "has_fields": all(key in body for key in
                              ("task_id", "kind", "status", "progress", "error", "result")),
            "shape": stream_shape(body)})

    elif case == "store_merge":
        base = {"kind": "task", "source": "shell", "stage": "cost", "text": "成本测算",
                "key": "task:T9"}
        first = dict(base)
        first["task"] = {"id": "T9", "label": "成本测算", "status": "running",
                         "steps": ["读取零件"],
                         "process": [{"seq": 1, "phase": "progress", "text": "读取零件"}]}
        second = dict(base)
        second["task"] = {"id": "T9", "label": "成本测算", "status": "succeeded",
                          "steps": ["读取零件", "模型返回"],
                          "process": [{"seq": 2, "phase": "model", "text": "调用模型（opus5）"}]}
        third = dict(base)
        third["task"] = {"id": "T9", "label": "成本测算", "status": "succeeded",
                         "steps": ["读取零件", "模型返回", "库内无同类件"],
                         "process": [{"seq": 3, "phase": "tool", "text": "库内无同类件"},
                                      {"seq": 4, "phase": "tool", "text": "库内无同类件"}]}
        store.append_session_event(PID, first)
        store.append_session_event(PID, second)
        store.append_session_event(PID, third)
        rows = store.load_session_events(PID)
        card = rows[0] if rows else {}
        dump({"case": case, "card_count": len(rows),
              "task": card.get("task"), "seq": card.get("seq")})

    else:
        dump({"_error": "unknown case: " + case})
except Exception as exc:  # noqa: BLE001 — 红测要把真实缺口回给断言
    dump({"_error": "%s: %s" % (type(exc).__name__, exc), "_error_type": type(exc).__name__})
'''


class ProcessStreamStaticContractTest(unittest.TestCase):
    """B1–B11 的源码契约。"""

    @classmethod
    def setUpClass(cls):
        cls.spec = read(SPEC)
        cls.tasks_py = read(TASKS_PY)
        cls.llm_client = read(LLM_CLIENT_PY)
        cls.claude_client = read(CLAUDE_CLIENT_PY)
        cls.qwen_client = read(QWEN_CLIENT_PY)
        cls.main_py = read(MAIN_PY)
        cls.store_py = read(STORE_PY)
        cls.timeline_js = read(TIMELINE_JS)
        cls.chat_js = read(CHAT_JS)
        cls.chat_css = read(CHAT_CSS)

    # ---------------------------------------------------------- 任务框架
    def test_01_spec_pins_the_contract(self):
        self.assertTrue(self.spec, "缺少过程事件通道的 spec")
        for token in ("process_event", "process_log", "model", "tool", "seq",
                      "claude_client", "qwen_client"):
            self.assertIn(token, self.spec, "spec 未钉住 %s" % token)

    def test_02_tasks_exposes_process_event_with_a_phase_whitelist(self):
        block = def_block(self.tasks_py, "def process_event(")
        self.assertTrue(block, "services/tasks.py 没有 process_event()")
        for phase in PHASES:
            self.assertIn('"%s"' % phase, block, "phase 白名单缺 %s：%s" % (phase, block[:300]))
        self.assertIn("ValueError", block, "非法 phase 必须明确报错，不能静默吞掉")
        self.assertIn("_CURRENT_TASK", block, "没有任务上下文时必须静默 no-op（与 report_progress 同口径）")

    def test_03_process_log_exists_and_is_bounded(self):
        self.assertIn("process_log", self.tasks_py, "任务文档没有 process_log")
        self.assertRegex(self.tasks_py, r"PROCESS_LOG_LIMIT\s*=", "缺少过程日志上限")
        submit = def_block(self.tasks_py, "def submit(")
        self.assertIn("process_log", submit, "建任务时没有初始化 process_log")

    def test_04_report_progress_feeds_the_same_stream(self):
        block = def_block(self.tasks_py, "def report_progress(")
        self.assertTrue(block, "找不到 report_progress()")
        self.assertIn("append_task_progress", block, "report_progress 的既有写入被删除")
        self.assertIn("process_log", block + self.tasks_py[:0] + def_block(self.tasks_py, "def process_event("),
                      "进度行没有进同一条过程序列")

    # ---------------------------------------------------------- 事件发出点
    def test_05_dispatch_layer_does_not_emit_its_own_pair(self):
        block = def_block(self.llm_client, "def run(")
        self.assertTrue(block, "找不到 llm_client.run()")
        self.assertNotIn("process_event", block,
                         "llm_client 只做分派，再发一对会造成同一调用两份模型事件")

    def test_06_model_events_are_emitted_at_the_last_mile(self):
        for path, text in ((CLAUDE_CLIENT_PY, self.claude_client), (QWEN_CLIENT_PY, self.qwen_client)):
            with self.subTest(module=path.name):
                block = def_block(text, "def run(")
                self.assertTrue(block, "找不到 %s 的 run()" % path.name)
                self.assertIn("process_event", block,
                              "%s 真正发起调用的那一层没有发模型事件" % path.name)
                self.assertIn("last_used_model", text,
                              "%s 的模型留痕口径被改动" % path.name)

    def test_07_tool_events_cover_the_four_lookups(self):
        sites = (
            "def _process_lookup_for(",
            "def _cost_lookup_for(",
            "def _refresh_component_match(",
            "def model_lookup_search(",
        )
        for marker in sites:
            with self.subTest(site=marker):
                block = def_block(self.main_py, marker)
                self.assertTrue(block, "找不到 %s" % marker)
                self.assertIn("process_event", block,
                              "%s 没有发工具事件：%s" % (marker, block[:200]))

    # ---------------------------------------------------------- 载荷与渲染
    def test_08_every_poller_passes_process_through(self):
        for path in PAYLOAD_SITES:
            with self.subTest(file=path.name):
                text = read(path)
                self.assertRegex(text, r"process\s*:\s*Array\.isArray\([^)]*process_log",
                                 "%s 没有把 process_log 透传成 process（同一字段名、同一形状）"
                                 % path.name)

    def test_09_left_pane_renders_ordered_phases(self):
        render = js_function_body(self.chat_js, "renderTaskProgress")
        self.assertTrue(render, "找不到 renderTaskProgress()")
        self.assertIn("process", render, "任务卡不认 process 字段")
        self.assertIn("log", render, "缺 process 时必须能退回 progress_log（旧任务兼容）")
        step = js_function_body(self.chat_js, "pushTaskStep")
        self.assertIn("phase", step, "每一步没有携带 phase，顺序之外的语义全丢了")
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.assertIn('"%s"' % phase, self.chat_js, "缺少 phase=%s 的判定" % phase)

    def test_10_phase_styles_exist(self):
        for phase in ("model", "tool"):
            with self.subTest(phase=phase):
                self.assertRegex(self.chat_css, r"\.oc-process-step\.%s\b" % phase,
                                 "缺少 .oc-process-step.%s 配色" % phase)

    def test_11_card_persists_and_replays_the_stream(self):
        persist = js_function_body(self.chat_js, "persistTaskCard")
        self.assertIn("process", persist, "落库没有带上过程序列")
        replay = js_function_body(self.chat_js, "replayTimelineTask")
        self.assertIn("process", replay, "回放没有透传过程序列")

    def test_12_mergers_take_the_union_by_seq(self):
        store_block = def_block(self.store_py, "def _merge_task_entry(")
        self.assertTrue(store_block, "找不到 _merge_task_entry()")
        self.assertIn("process", store_block, "store 的任务卡合并没有 process 口径")
        self.assertIn("seq", store_block, "store 的 process 合并没有按 seq 取并集")
        self.assertIn("mergeTask", self.timeline_js, "时间线合并实现不见了")
        self.assertIn("process", self.timeline_js, "前端 mergeTask 没有 process 口径")


# --------------------------------------------------------------------------- #
# 运行时：真跑任务框架
# --------------------------------------------------------------------------- #
class ProcessStreamRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = pathlib.Path(tempfile.mkdtemp(prefix="cpq-stream-data-"))
        cls.stream = run_child(CHILD, str(cls.work), str(ROOT), "stream")
        cls.progress_only = run_child(CHILD, str(cls.work), str(ROOT), "progress_only")
        cls.api = run_child(CHILD, str(cls.work), str(ROOT), "api")
        cls.route = run_child(CHILD, str(cls.work), str(ROOT), "route")
        cls.merge = run_child(CHILD, str(cls.work), str(ROOT), "store_merge")

    def _no_error(self, payload):
        self.assertNotIn("_error", payload, "子进程探针报错：%s" % payload)

    def test_20_report_progress_and_process_event_share_one_stream(self):
        self._no_error(self.stream)
        shape = self.stream["shape"]
        self.assertEqual(["progress", "tool", "model", "model", "progress"], shape["phases"],
                         "过程序列不是一条有序流：%s" % shape)
        self.assertEqual([1, 2, 3, 4, 5], shape["seqs"], "seq 不是本任务内单调递增：%s" % shape)

    def test_21_model_call_emits_exactly_one_pair_with_the_real_model(self):
        shape = self.stream["shape"]
        models = [text for text, phase in zip(shape["texts"], shape["phases"]) if phase == "model"]
        self.assertEqual(2, len(models), "一次逻辑模型调用必须恰好一对事件：%s" % shape)
        self.assertIn("probe-model-9", models[0], "开始事件没有点名实际使用的模型：%s" % models)
        self.assertIn("probe-model-9", models[1], "返回事件没有点名实际使用的模型：%s" % models)

    def test_22_progress_log_backward_compatibility_is_untouched(self):
        # 守护用例：只跑 report_progress 的任务，今天与改完后都必须是这个样子。
        out = self.progress_only
        self._no_error(out)
        self.assertEqual("succeeded", out.get("status"), "只跑进度的任务不该失败：%s" % out)
        self.assertEqual(["准备用料与费率", "  ↳ 命中 3 条路线模板"], out.get("progress_log"),
                         "progress_log 的既有形状/内容被改动（旧客户端会坏）")
        # progress 单值字段的既有收尾（成功即「完成」）在此不做断言 —— 那是 tasks._run
        # 的既有行为，本批只保证 process_log 这条新通道不影响它。
        self.assertEqual("完成", out.get("progress"),
                         "progress 单值字段的收尾被改动（状态条会显示错）")

    def test_23_no_prompt_or_key_leaks_into_the_stream(self):
        self.assertEqual([], self.stream["leak"],
                         "过程事件里出现了 prompt 原文或密钥：%s" % self.stream["leak"])

    def test_24_empty_text_is_a_noop_and_unknown_phase_is_loud(self):
        self._no_error(self.api)
        self.assertEqual(["tool", "progress"], self.api["ok"]["phases"],
                         "空文本必须 no-op（不该多出一条），其余照常入流：%s" % self.api["ok"])
        self.assertEqual("failed", self.api["bad_status"],
                         "非法 phase 必须让任务明确失败：%s" % self.api)
        self.assertIn("phase", self.api["bad_error"], "失败原因没说清是 phase 不合法：%s" % self.api)

    def test_25_process_event_outside_a_task_is_silent(self):
        self.assertEqual("", self.api["outside_error"],
                         "没有任务上下文时必须静默 no-op，不能抛错：%s" % self.api)
        self.assertEqual(self.api["tasks_before"], self.api["tasks_after"],
                         "没有任务上下文时不得凭空写盘：%s" % self.api)

    def test_26_task_route_exposes_process_log(self):
        self._no_error(self.route)
        self.assertEqual(200, self.route["status"], "任务端点没回 200：%s" % self.route)
        self.assertTrue(self.route["has_process_log"], "任务端点没有带 process_log")
        self.assertTrue(self.route["has_progress_log"], "任务端点丢了 process_log 之外的既有字段")
        self.assertTrue(self.route["has_fields"], "任务端点丢了既有字段：%s" % self.route)
        self.assertEqual(["tool", "progress"], self.route["shape"]["phases"],
                         "端点返回的顺序与写入顺序不一致：%s" % self.route["shape"])

    def test_27_session_card_merges_process_by_seq(self):
        self._no_error(self.merge)
        self.assertEqual(1, self.merge["card_count"], "同一 task.id 只该有一张卡")
        task = self.merge["task"]
        process = task.get("process") or []
        self.assertEqual([1, 2, 3, 4], [row.get("seq") for row in process],
                         "落库的 process 没有按 seq 取并集：%s" % process)
        texts = [row.get("text") for row in process]
        self.assertEqual(2, texts.count("库内无同类件"),
                         "重复文本被吃掉了（不同零件说同一句话是合法的）：%s" % texts)
        self.assertEqual(["读取零件", "模型返回", "库内无同类件"], task.get("steps"),
                         "steps 的既有合并口径被改动：%s" % task.get("steps"))


# --------------------------------------------------------------------------- #
# 运行时：前端时间线合并
# --------------------------------------------------------------------------- #
TIMELINE_DRIVER = r'''
const fs = require('fs');
const vm = require('vm');

const code = fs.readFileSync(process.argv[2], 'utf8');
const env = { console: console, Date: Date, JSON: JSON, Math: Math, Object: Object,
              Array: Array, Map: Map, Set: Set, String: String, Number: Number, Boolean: Boolean };
env.window = env; env.globalThis = env;
vm.runInContext(code, vm.createContext(env), { filename: 'tech-session-timeline.js' });
const mod = env.TechSessionTimeline || (env.window && env.window.TechSessionTimeline);
if (!mod) throw new Error('tech-session-timeline.js 没有导出 window.TechSessionTimeline');
const out = {};

// A. mergeTask：process 按 seq 取并集、升序，重复文本保留；steps 口径不动。
{
  const previous = { id: 'T1', status: 'running', steps: ['读取零件'],
                     process: [{ seq: 1, phase: 'progress', text: '读取零件' }] };
  const incoming = { id: 'T1', status: 'succeeded', steps: ['读取零件', '模型返回'],
                     process: [{ seq: 3, phase: 'tool', text: '库内无同类件' },
                               { seq: 2, phase: 'model', text: '调用模型（opus5）' },
                               { seq: 4, phase: 'tool', text: '库内无同类件' }] };
  const merged = mod.mergeTask(previous, incoming);
  out.union_seqs = (merged.process || []).map(row => row.seq);
  out.union_phases = (merged.process || []).map(row => row.phase);
  out.duplicate_text_kept = (merged.process || []).filter(row => row.text === '库内无同类件').length;
  out.steps = merged.steps;
  out.status = merged.status;
}

// B. applyTaskProgress：看板载荷里的 process 必须落进同一张卡，且顺序不变。
{
  const first = mod.applyTaskProgress([], {
    id: 'T2', label: '需求资料解析', status: 'running', steps: ['开始'],
    process: [{ seq: 1, phase: 'progress', text: '开始' }] });
  const second = mod.applyTaskProgress(first, {
    id: 'T2', label: '需求资料解析', status: 'running', steps: ['开始', '调用模型'],
    process: [{ seq: 1, phase: 'progress', text: '开始' },
              { seq: 2, phase: 'model', text: '调用模型（probe）' }] });
  const card = second.find(entry => entry.task && entry.task.id === 'T2') || {};
  out.apply_seqs = ((card.task || {}).process || []).map(row => row.seq);
  out.apply_texts = ((card.task || {}).process || []).map(row => row.text);
  out.card_count = second.length;
}

// C. 旧卡片（没有 process）照旧合并，不报错、不造空数组。
{
  const merged = mod.mergeTask({ id: 'T3', steps: ['a'] }, { id: 'T3', steps: ['a', 'b'] });
  out.legacy_steps = merged.steps;
  out.legacy_has_process = Object.prototype.hasOwnProperty.call(merged, 'process');
}

console.log(JSON.stringify(out));
'''


@unittest.skipUnless(NODE, "需要 node 才能真跑时间线合并")
class TimelineMergeRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(TIMELINE_DRIVER, str(TIMELINE_JS))

    def test_30_process_is_union_sorted_by_seq(self):
        self.assertEqual([1, 2, 3, 4], self.out["union_seqs"],
                         "process 没有按 seq 升序取并集：%s" % self.out)
        self.assertEqual(["progress", "model", "tool", "tool"], self.out["union_phases"],
                         "phase 没有跟着 seq 走：%s" % self.out)

    def test_31_identical_text_with_different_seq_survives(self):
        self.assertEqual(2, self.out["duplicate_text_kept"],
                         "重复文本被吃掉了：%s" % self.out)

    def test_32_steps_merge_is_untouched(self):
        self.assertEqual(["读取零件", "模型返回"], self.out["steps"],
                         "steps 的既有合并口径被改动：%s" % self.out)
        self.assertEqual("succeeded", self.out["status"], "状态就地更新被破坏：%s" % self.out)

    def test_33_board_payload_carries_process_into_the_card(self):
        self.assertEqual([1, 2], self.out["apply_seqs"],
                         "看板载荷的 process 没有落进任务卡：%s" % self.out)
        self.assertEqual(1, self.out["card_count"], "同一 taskId 只能有一张卡：%s" % self.out)

    def test_34_legacy_cards_still_merge(self):
        self.assertEqual(["a", "b"], self.out["legacy_steps"],
                         "旧卡片（没有 process）的合并被破坏：%s" % self.out)
        self.assertIs(False, self.out["legacy_has_process"],
                      "旧卡片不得被凭空补出一个空 process：%s" % self.out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
