"""红测：任务「过程事件」必须带结构化明细，并且能像工具卡那样展开。

用户反馈：直接执行（右侧按钮 / 一键动作）时看不到"库检索的查询条件、命中件、差异"，
只有一行行拼好的中文；而 Agent 会话里的工具卡是能展开看输入输出的。

现状（已实测，非推断）：`## 94` 已经把过程事件通道打通（`tasks.process_event(phase, text,
detail=None)` → `process_log` → 左侧 `.oc-process-step`），但：

  · 四处工具事件只发文本（main.py:1345 / :1473 / :1492 / :1692），`detail` 一个都没用；
  · 逐件零部件库检索的"查询条件 / 命中 / 差异"是拼好的中文句子
    （component_match.py:156-169），`query_params` / `component_code` / `score` /
    `gap_notes` 这些结构化事实只存在于落盘报告里；
  · `tasks.report_progress()`（tasks.py:208）只收一个参数，明细没有入口；
  · 前端 `pushTaskStep()`（agent-chat.js:1407）只画 dot + text，没有「详情」；
  · `persistTaskCard()`（agent-chat.js:1585）落库只挑 `seq/phase/text`，`detail` 会被丢掉。

Spec：docs/specs/effective-model-for-vision-and-task-process-detail.md（契约 B1–B8）
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
CHAT_CSS = FRONTEND / "agent-chat.css"
TASKS_PY = ROOT / "tech_app/backend/services/tasks.py"
MAIN_PY = ROOT / "tech_app/backend/main.py"
COMPONENT_PY = ROOT / "tech_app/backend/services/component_match.py"
PROCESS_PY = ROOT / "tech_app/backend/services/process_lookup.py"
COST_PY = ROOT / "tech_app/backend/services/cost_lookup.py"
QWEN_PY = ROOT / "tech_app/backend/services/qwen_client.py"
CLAUDE_PY = ROOT / "tech_app/backend/services/claude_client.py"
SPEC = ROOT / "docs" / "specs" / "effective-model-for-vision-and-task-process-detail.md"

NODE = shutil.which("node")
DEFINITION_MARKERS = ("\ndef ", "\nasync def ", "\n@app.", "\nclass ")


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


def py_body(src: str, name: str) -> str:
    idx = src.find("def %s(" % name)
    if idx < 0:
        return ""
    rest = src[idx:]
    end = len(rest)
    for marker in DEFINITION_MARKERS:
        at = rest.find(marker, 1)
        if at != -1:
            end = min(end, at)
    return rest[:end]


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


def arrow_source(text: str, marker: str) -> str:
    block = block_from(text, marker)
    if not block:
        return ""
    start = text.find(marker)
    at = text.find(block, start)
    return text[start:at + len(block)] + ";"


def js_const_source(text: str, name: str) -> str:
    """取单行 `const NAME = ...;`（正则字面量与数组常量都用它抽）。"""
    match = re.search(r"^\s*const %s = .*?;\s*$" % re.escape(name), text, re.M)
    return match.group(0).strip() if match else ""


def require_dict(value, label: str) -> dict:
    """明细缺失时给一条清爽的断言失败，而不是 AttributeError。"""
    if not isinstance(value, dict):
        raise AssertionError("%s 没有生成明细：%r" % (label, value))
    return value


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
    with tempfile.TemporaryDirectory(prefix="cpq-process-detail-") as tmp:
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
    with tempfile.TemporaryDirectory(prefix="cpq-process-detail-js-") as tmp:
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
# 子进程：真跑任务框架与检索服务（打桩知识库与模型客户端，绝不联网）
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

import cpq_shared_settings

cpq_shared_settings.load = lambda legacy=(): {
    "model": "qwen3.5-plus", "temperature": 0.2, "max_tokens": 4096, "thinking": False,
    "api_keys": {"qwen": "GLOBAL-QWEN-KEY"}}

from pydantic import BaseModel
from starlette.testclient import TestClient

import tech_app.backend.main as main
from tech_app.backend.services import component_match, cost_lookup, process_lookup, tasks
from tech_app.backend.storage import store

client = TestClient(main.app, raise_server_exceptions=False)
PID = store.create_project(source_filename="process-detail.png", source_bytes=b"png",
                           note="process detail probe " + case, owner="tester")
TERMINAL = {"succeeded", "failed", "partial", "interrupted", "cancelled", "stale"}
CANARY = "LEAK-CANARY-候选原件不得整包进明细"
SYSTEM_PROMPT = "系统提示-绝不允许出现在过程流里"
USER_BODY = "用户输入原文-也绝不允许出现在过程流里"
API_KEY = "sk-probe-must-not-leak"

IR = {"parts": [
    {"part_id": "P-001", "name": "上壳", "quantity": 1, "material": {"spec": "ABS"},
     "features": [{"type": "box", "length": 108.0, "width": 56.0, "height": 13.25}]},
    {"part_id": "P-002", "name": "下壳", "quantity": 1, "material": {"spec": "ABS"},
     "features": [{"type": "box", "length": 108.0, "width": 56.0, "height": 13.25}]},
]}

HIT_ROW = {"component_id": 7, "component_code": "CMP-SEMI-EE-BLOCK-0001",
           "name": "搬运吸嘴主体安装块", "score": 0.648, "match_type": "fuzzy",
           "gap_notes": "length: 库内 120.0mm / 图纸 108.0", "internal_note": CANARY}


def dump(payload):
    print(json.dumps(payload, ensure_ascii=False, default=str))


def poll(task_id, timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get("/api/projects/%s/tasks/%s" % (PID, task_id)).json()
        if task.get("status") in TERMINAL:
            return task
        time.sleep(0.2)
    return {"status": "timeout", "error": "轮询超时", "process_log": []}


def rows(task):
    return [(entry or {}) for entry in (task.get("process_log") or [])]


def texts(task):
    return [str(entry.get("text") or "") for entry in rows(task)]


def find(task, needle):
    for entry in rows(task):
        if needle in str(entry.get("text") or ""):
            return entry
    return None


def leak_report(task):
    blob = json.dumps(rows(task), ensure_ascii=False, default=str)
    return [needle for needle in (SYSTEM_PROMPT, USER_BODY, API_KEY, CANARY) if needle in blob]


def sizes(task):
    return [len(json.dumps(entry.get("detail"), ensure_ascii=False, default=str))
            for entry in rows(task) if entry.get("detail") is not None]


class ProbeOut(BaseModel):
    ok: bool = True


CALLED = {}


class _Completions:
    def create(self, **kwargs):
        CALLED["model"] = kwargs.get("model")
        msg = types.SimpleNamespace(content='{"ok": true}')
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg, finish_reason="stop")])


class _Chat:
    completions = _Completions()


class _FakeClient:
    chat = _Chat()


def stub_kb():
    component_match.kb_repo.refresh_kb = lambda: None
    component_match.kb_repo.list_components = lambda limit=1000: [{"component_code": "CMP-X"}] * 20
    component_match.kb_repo.recommend_components = lambda query, limit=3: [dict(HIT_ROW)]


if case == "progress_detail":
    def job():
        # 旧口径：只有文本，没有明细 —— 必须照旧写进 progress_log 且不带 detail。
        tasks.report_progress("读取零件清单")
        # 新口径：同一行文字附带结构化明细。
        tasks.report_progress("查询条件：length=108", {
            "tool": "component_match", "title": "零部件库检索",
            "input": {"part_id": "P-001", "params": {"length": 108.0}}, "status": "running",
            "output": {}})
        return {}

    task = poll(tasks.submit(PID, "library_lookup", job))
    plain = find(task, "读取零件清单")
    rich = find(task, "查询条件：length=108")
    dump({"case": case, "status": task.get("status"), "error": str(task.get("error") or ""),
          "progress_log": task.get("progress_log"),
          "plain_detail": (plain or {}).get("detail"),
          "plain_phase": (plain or {}).get("phase"),
          "rich_phase": (rich or {}).get("phase"),
          "rich_text": (rich or {}).get("text"),
          "rich_detail": (rich or {}).get("detail"),
          "leak": leak_report(task), "sizes": sizes(task)})

elif case == "component_detail":
    stub_kb()

    def job():
        main._refresh_component_match(PID, json.loads(json.dumps(IR)), kept="解析结果")
        return {}

    task = poll(tasks.submit(PID, "component_match", job))
    dump({"case": case, "status": task.get("status"), "error": str(task.get("error") or ""),
          "texts": texts(task),
          "start_detail": (find(task, "检索零部件库开始") or {}).get("detail"),
          "target_detail": (find(task, "检索零部件库（1/2）") or {}).get("detail"),
          "query_detail": (find(task, "查询条件：") or {}).get("detail"),
          "hit_detail": (find(task, "命中 CMP-SEMI-EE-BLOCK-0001") or {}).get("detail"),
          "gap_detail": (find(task, "差异：") or {}).get("detail"),
          "summary_detail": (find(task, "零部件库检索完成") or {}).get("detail"),
          "hit_text": (find(task, "命中 CMP-SEMI-EE-BLOCK-0001") or {}).get("text"),
          "leak": leak_report(task), "sizes": sizes(task)})

elif case == "tool_detail":
    chunk = {"parts": IR["parts"]}

    def job():
        process_lookup.lookup_part = lambda part, match=None, progress=None: {
            "route": {"route_code": "RT-PLATE-MACHINED"},
            "steps": [1, 2, 3], "extra_steps": [4], "feature_gaps": [5], "notes": [],
            "summary": {"route_steps": 3, "extra_steps": 1, "covered_features": 2,
                        "uncovered_features": 1, "library_steps": 9}}
        process_lookup.save_report = lambda project_id, part_id, report: None
        cost_lookup.lookup_part = lambda part, quantity=1, match=None, process_report=None, progress=None: {
            "materials": [1, 2], "rates": [3], "factors": [4], "summary": {
                "materials": 2, "rates": 1, "factors": 1}}
        cost_lookup.save_report = lambda project_id, part_id, report: None
        main._process_lookup_for(PID, "P-001", chunk["parts"][0])
        main._cost_lookup_for(PID, "P-001", chunk["parts"][0], 2)
        return {}

    task = poll(tasks.submit(PID, "library_lookup", job))
    dump({"case": case, "status": task.get("status"), "error": str(task.get("error") or ""),
          "texts": texts(task),
          "process_start": (find(task, "检索企业工艺库（P-001）") or {}).get("detail"),
          "process_end": (find(task, "命中 3 道工序") or {}).get("detail"),
          "cost_start": (find(task, "检索企业成本库（P-001）") or {}).get("detail"),
          "cost_end": (find(task, "命中物料价") or {}).get("detail"),
          "leak": leak_report(task), "sizes": sizes(task)})

elif case == "model_detail":
    from tech_app.backend.services import qwen_client

    qwen_client.get_client = lambda vision=False: _FakeClient()

    def job():
        tasks.report_progress("准备调用模型")
        qwen_client.run(SYSTEM_PROMPT, [{"type": "image_url",
                                         "image_url": {"url": "data:image/png;base64,aGVsbG8="}}],
                        ProbeOut)
        return {}

    task = poll(tasks.submit(PID, "library_lookup", job))
    dump({"case": case, "status": task.get("status"), "error": str(task.get("error") or ""),
          "texts": texts(task), "called_model": CALLED.get("model"),
          "model_details": [entry.get("detail") for entry in rows(task)
                            if entry.get("phase") == "model"],
          "leak": leak_report(task), "sizes": sizes(task)})

else:
    dump({"case": case, "error": "未知用例"})
'''


class TaskProcessDetailRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        python = child_python()
        if not python:
            raise unittest.SkipTest("没有可 import fastapi 的解释器，跳过过程明细走查")
        cls.work = tempfile.mkdtemp(prefix="cpq-process-detail-work-")
        try:
            cls.progress = run_child(CHILD, str(cls.work), str(ROOT), "progress_detail")
            cls.component = run_child(CHILD, str(cls.work), str(ROOT), "component_detail")
            cls.tools = run_child(CHILD, str(cls.work), str(ROOT), "tool_detail")
            cls.model = run_child(CHILD, str(cls.work), str(ROOT), "model_detail")
        finally:
            shutil.rmtree(cls.work, ignore_errors=True)


class ProcessDetailPayloadTest(TaskProcessDetailRuntimeTest):
    def test_01_spec_pins_the_contract(self):
        spec = read(SPEC)
        self.assertTrue(spec, "缺少 docs/specs/effective-model-for-vision-and-task-process-detail.md")
        for token in ("detail", "oc-process-detail", "component_match", "gap_notes",
                      "report_progress", "process_event", "4096"):
            self.assertIn(token, spec, "spec 未钉住 %s" % token)

    def test_02_report_progress_accepts_detail(self):
        self.assertEqual("succeeded", self.progress["status"],
                         "带明细的 report_progress 直接把任务打挂了：%s" % self.progress)
        detail = self.progress["rich_detail"]
        self.assertIsInstance(detail, dict, "report_progress 没有把 detail 落到过程行：%s"
                             % self.progress)
        self.assertEqual("component_match", detail.get("tool"))
        self.assertEqual("零部件库检索", detail.get("title"))
        self.assertEqual(108.0, (detail.get("input") or {}).get("params", {}).get("length"))
        self.assertEqual("running", detail.get("status"))
        self.assertEqual("progress", self.progress["rich_phase"],
                         "明细必须附着在同一行进度上，不许另起一条：%s" % self.progress)
        self.assertEqual("查询条件：length=108", self.progress["rich_text"],
                         "进度文本被改写了：%s" % self.progress)

    def test_03_plain_progress_keeps_its_old_shape(self):
        self.assertIsNone(self.progress["plain_detail"],
                          "没有明细的进度行不得凭空长出一个 detail：%s" % self.progress)
        self.assertIn("读取零件清单", self.progress["progress_log"] or [],
                      "progress_log 的既有文本口径被破坏：%s" % self.progress)

    def test_04_component_lookup_rows_carry_query_and_hit_details(self):
        self.assertEqual("succeeded", self.component["status"],
                         "零部件库检索任务失败：%s" % self.component)
        for key in ("start_detail", "target_detail", "query_detail", "hit_detail",
                    "gap_detail", "summary_detail"):
            detail = self.component[key]
            self.assertIsInstance(detail, dict, "%s 没有带明细：%s" % (key, self.component))

    def test_05_query_conditions_are_structured(self):
        detail = require_dict(self.component["query_detail"], "查询条件行的 detail")
        self.assertEqual("component_match", detail.get("tool"))
        self.assertEqual("ABS", (detail.get("input") or {}).get("material_spec"))
        self.assertEqual({"length": 108.0, "width": 56.0, "height": 13.25},
                         (detail.get("input") or {}).get("params"),
                         "查询条件仍只是拼好的中文句子：%s" % detail)

    def test_06_hit_row_has_code_decision_score_and_gap(self):
        hit = require_dict(self.component["hit_detail"], "命中行的 detail")
        output = hit.get("output") or {}
        self.assertEqual("CMP-SEMI-EE-BLOCK-0001", output.get("component_code"))
        self.assertEqual("搬运吸嘴主体安装块", output.get("component_name"))
        self.assertEqual("modify", output.get("decision"))
        self.assertAlmostEqual(0.648, float(output.get("score")), places=3,
                               msg="命中行的匹配度没有结构化：%s" % hit)
        self.assertEqual("running", hit.get("status"))
        gap = require_dict(self.component["gap_detail"], "差异行的 detail")
        self.assertEqual("length: 库内 120.0mm / 图纸 108.0",
                         (gap.get("output") or {}).get("gap_notes"),
                         "差异全文没有进明细：%s" % gap)
        summary_row = require_dict(self.component["summary_detail"], "检索结束行的 detail")
        self.assertEqual("ok", summary_row.get("status"),
                         "检索结束行没有 ok 状态：%s" % self.component["summary_detail"])
        summary = summary_row.get("output") or {}
        for key in ("total", "reuse", "modify", "new", "library_size"):
            self.assertIn(key, summary, "结束行的计数缺少 %s：%s" % (key, summary))

    def test_07_per_part_rows_keep_their_text(self):
        self.assertIn("检索零部件库（1/2）：P-001 上壳", self.component["texts"],
                      "逐件检索的目标行文本被改写了：%s" % self.component["texts"])
        self.assertIn("  ↳ 命中 CMP-SEMI-EE-BLOCK-0001 搬运吸嘴主体安装块（可改制，匹配度 65%）",
                      str(self.component.get("hit_text") or ""),
                      "命中行的文本被改写了：%s" % self.component["hit_text"])

    def test_08_other_tool_rows_carry_details(self):
        self.assertEqual("succeeded", self.tools["status"],
                         "工艺 / 成本库检索任务失败：%s" % self.tools)
        start = self.tools["process_start"] or {}
        self.assertEqual("process_lookup", start.get("tool"))
        self.assertEqual("P-001", (start.get("input") or {}).get("part_id"))
        self.assertEqual("running", start.get("status"))
        end = self.tools["process_end"] or {}
        self.assertEqual("ok", end.get("status"))
        for key in ("route_code", "route_steps", "extra_steps"):
            self.assertIn(key, end.get("output") or {}, "工艺库返回明细缺 %s：%s" % (key, end))
        cost = self.tools["cost_start"] or {}
        self.assertEqual("cost_lookup", cost.get("tool"))
        self.assertEqual(2, (cost.get("input") or {}).get("quantity"))
        for key in ("materials", "rates", "factors"):
            self.assertIn(key, (self.tools["cost_end"] or {}).get("output") or {},
                          "成本库返回明细缺 %s：%s" % (key, self.tools["cost_end"]))

    def test_09_model_events_carry_model_provider_and_capability(self):
        self.assertEqual("succeeded", self.model["status"],
                         "模型调用任务失败：%s" % self.model)
        details = self.model["model_details"] or []
        self.assertGreaterEqual(len(details), 2, "模型事件没有带明细：%s" % self.model)
        called = self.model["called_model"]
        for detail in details:
            self.assertIsInstance(detail, dict, "模型事件缺 detail：%s" % detail)
            self.assertEqual(called, detail.get("model"), "模型明细里的模型名不是实跑的那个：%s"
                             % detail)
            self.assertTrue(detail.get("provider"), "模型明细缺 provider：%s" % detail)
            self.assertIn("vision", detail, "模型明细缺 vision 能力位：%s" % detail)
        self.assertEqual("qwen", details[0].get("provider"))
        self.assertIs(True, details[0].get("vision"))

    def test_10_details_never_leak_prompts_keys_or_raw_candidates(self):
        for name, payload in (("progress", self.progress), ("component", self.component),
                              ("tools", self.tools), ("model", self.model)):
            with self.subTest(case=name):
                self.assertEqual([], payload.get("leak"),
                                 "过程流里出现了不该出现的内容：%s" % payload.get("leak"))

    def test_11_detail_size_is_capped(self):
        for name, payload in (("progress", self.progress), ("component", self.component),
                              ("tools", self.tools), ("model", self.model)):
            with self.subTest(case=name):
                for size in payload.get("sizes") or []:
                    self.assertLessEqual(size, 4096,
                                         "%s 的单条明细超过 4096 字节：%s" % (name, size))


# --------------------------------------------------------------------------- #
# 源码契约：四处工具事件与三个服务 helper 都必须带明细
# --------------------------------------------------------------------------- #
class ProcessDetailSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = read(TASKS_PY)
        cls.main = read(MAIN_PY)
        cls.component = read(COMPONENT_PY)
        cls.process = read(PROCESS_PY)
        cls.cost = read(COST_PY)
        cls.qwen = read(QWEN_PY)
        cls.claude = read(CLAUDE_PY)

    def test_20_report_progress_takes_an_optional_detail(self):
        body = py_body(self.tasks, "report_progress")
        self.assertTrue(body, "找不到 report_progress()")
        self.assertTrue(re.search(r"def report_progress\([^)]*detail", body),
                        "report_progress() 还没有 detail 入口：%s" % body[:200])
        self.assertIn("detail", body.split("store.append_task_process")[-1][:400],
                      "report_progress() 没把 detail 带进过程行：%s" % body[:400])

    def test_21_tool_events_in_main_pass_details(self):
        self.assertGreaterEqual(self.main.count("process_event("), 8,
                                "main.py 的工具事件被删了：%s" % self.main.count("process_event("))
        with_detail = len(re.findall(r"process_event\([^)]*detail\s*=", self.main, re.S))
        self.assertGreaterEqual(with_detail, 8,
                                "main.py 里仍有工具事件不带 detail（现 %d 处）：%s"
                                % (with_detail, re.findall(r"process_event\((.{0,60})", self.main)[:6]))

    def test_22_services_forward_details_to_progress(self):
        for path, name in ((COMPONENT_PY, "component_match"), (PROCESS_PY, "process_lookup"),
                           (COST_PY, "cost_lookup")):
            src = read(path)
            body = py_body(src, "_report")
            with self.subTest(service=name):
                self.assertTrue(re.search(r"def _report\([^)]*detail", body),
                                "%s._report() 不接受 detail：%s" % (name, body[:200]))
                self.assertIn("progress(message,detail)", body.replace(" ", ""),
                              "%s._report() 没有把 detail 交给回调：%s" % (name, body[:300]))

    def test_23_component_lookup_attaches_structured_rows(self):
        for token in ("query_params", "component_code", "score", "gap_notes", "candidate"):
            with self.subTest(token=token):
                self.assertIn(token, self.component, "逐件明细缺少 %s" % token)

    def test_24_model_events_include_provider_and_vision(self):
        for path, name in ((QWEN_PY, "qwen_client"), (CLAUDE_PY, "claude_client")):
            src = read(path)
            self.assertTrue(re.search(r'process_event\(\s*"model"[^)]*detail', src),
                            "%s 的模型事件没有带明细（qwen_client.py:%d 字符）"
                            % (name, len(src)))
            for key in ("provider", "vision"):
                with self.subTest(service=name, key=key):
                    self.assertIn(key, src, "%s 的模型明细缺 %s" % (name, key))


# --------------------------------------------------------------------------- #
# 前端：详情折叠 + 落库带明细
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
  prepend(node) { node.parentNode = this; this.children.unshift(node); return node; }
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

DETAIL_TAIL = r'''
function names(node) { return node && node.children ? node.children.map(function (c) { return String(c.className); }) : []; }
function find(node, cls) {
  if (!node || !node.children) return null;
  for (var i = 0; i < node.children.length; i += 1) {
    var child = node.children[i];
    if (String(child.className).split(/\s+/).indexOf(cls) >= 0) return child;
    var deeper = find(child, cls);
    if (deeper) return deeper;
  }
  return null;
}

var out = {};
var detail = {tool: "component_match", title: "零部件库检索",
              input: {part_id: "P-001", params: {length: 108}},
              status: "running", output: {decision: "modify", score: 0.648}};

var card = ensureTaskCard("T-detail-1", "零部件库检索");
pushTaskStep(card, "查询条件：length=108", "", "tool", detail);
var rows = card.steps.children;
var richRow = rows[rows.length - 1];
out.rich_row_class = String(richRow.className);
out.rich_row_children = names(richRow);
var box = find(richRow, "oc-process-detail");
out.has_details = !!box;
out.summary_text = box && box.children.length ? box.children[0].textContent : "";
out.details_text = box ? box.textContent : "";
out.input_json = box ? (function () {
  var pre = find(box, "oc-process-detail-input");
  return pre ? pre.textContent : "";
})() : "";
out.output_json = box ? (function () {
  var pre = find(box, "oc-process-detail-output");
  return pre ? pre.textContent : "";
})() : "";

pushTaskStep(card, "读取零件清单", "");
var plainRow = card.steps.children[card.steps.children.length - 1];
out.plain_row_children = names(plainRow);
out.plain_has_details = !!find(plainRow, "oc-process-detail");

// 落库必须带上明细
var persisted = null;
persistSessionEvent = function (event) { persisted = event; };
persistTaskCard("T-detail-2", "零部件库检索", "succeeded", [], "", "",
  [{seq: 1, phase: "tool", text: "查询条件：length=108", detail: detail}]);
out.persisted_detail = persisted && persisted.task && persisted.task.process
  ? (persisted.task.process[0].detail || null) : null;
out.persisted_keys = persisted && persisted.task && persisted.task.process
  ? Object.keys(persisted.task.process[0]) : [];

// 看板载荷里的明细要真的画出来
renderTaskProgress({taskId: "T-detail-3", label: "零部件库检索", status: "running",
                    process: [{seq: 1, phase: "tool", text: "命中 CMP-X（可改制，匹配度 65%）",
                               detail: detail}]});
var rendered = taskProgressCards.get("T-detail-3");
out.board_card_has_details = !!(rendered && rendered.steps.children.length &&
                                find(rendered.steps.children[0], "oc-process-detail"));
out.board_row_class = rendered && rendered.steps.children.length
  ? String(rendered.steps.children[0].className) : "";

console.log(JSON.stringify(out));
'''


def detail_driver(js: str) -> str:
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
            function boardStage() { return "cost"; }
            """),
        js_const_source(js, "SENSITIVE_TASK_KEYS"),
        js_const_source(js, "QUIET_BOARD_CODES"),
        js_const_source(js, "INTERRUPTED_CODES"),
        arrow_source(js, "const el = (tag, cls, text)"),
        function_source(js, "taskStatusWord"),
        function_source(js, "ensureTaskCard"),
        function_source(js, "pushTaskStep"),
        function_source(js, "setTaskStatus"),
        function_source(js, "toneOf"),
        function_source(js, "sanitizeTaskDetail"),
        function_source(js, "isInterruptedCode"),
        function_source(js, "isQuietBoardCode"),
        function_source(js, "renderTaskProgress"),
        function_source(js, "persistTaskCard"),
        DETAIL_TAIL,
    ]
    return "\n".join(part for part in parts if part)


@unittest.skipUnless(NODE, "需要 node 才能真跑过程卡渲染走查")
class ProcessDetailFrontendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read(CHAT_JS)
        cls.css = read(CHAT_CSS)
        cls.out = run_node(detail_driver(cls.js))

    def test_30_detail_row_keeps_dot_and_text(self):
        self.assertIn("oc-process-step", self.out["rich_row_class"])
        self.assertIn("tool", self.out["rich_row_class"], "工具行的相位类名丢了：%s" % self.out)
        self.assertIn("oc-process-dot", self.out["rich_row_children"])
        self.assertIn("oc-process-text", self.out["rich_row_children"])

    # ## 133 起：过程行不再有「详情」折叠区，也不再显示输入 / 输出 JSON。
    # 原 test_31 / test_32 退役，改由
    # tests/test_quote_tech_process_row_product_contract_red.py 的 A / D 组接管
    # （无折叠区、无按钮、无 JSON、产品侧结果直接可见）。
    def test_33_plain_row_has_no_details_block(self):
        self.assertIs(False, self.out["plain_has_details"],
                      "没有明细的行不该长出「详情」：%s" % self.out)
        self.assertEqual(["oc-process-dot", "oc-process-text"], self.out["plain_row_children"],
                         "没有明细的行结构必须与今天逐字一致：%s" % self.out["plain_row_children"])

    def test_34_persisted_card_keeps_the_detail(self):
        detail = require_dict(self.out["persisted_detail"], "落库载荷里的 task.process[0].detail")
        self.assertEqual("component_match", detail.get("tool"))
        self.assertEqual(108, (detail.get("input") or {})
                         .get("params", {}).get("length"))

    # 原 test_35 / test_36 退役（看板不再渲染折叠明细、CSS 不再需要 .oc-process-detail）。


if __name__ == "__main__":
    unittest.main(verbosity=2)
