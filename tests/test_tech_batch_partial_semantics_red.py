"""批量动作统一容错语义（第五批）：逐件跑完 + 部分完成 + 仅重试失败项。

背景（用户反馈 + 只读代码检查）：
  · 2.3 成本「一键测算全部成本」还是一票否决：crRunParts() 第一件没算出结果就 return false，
    后面的零件一个都不再尝试；
  · 2.1 工艺批量虽然逐件跑完，但只要有 1 个失败就上报 task-failed，把「4 成功 + 1 失败」
    说成整批失败；
  · 会话卡与运行时都不认识 task-partial（agent-chat.js 的桥、tech-board-runtime.js 的 EVENT）；
  · 全仓没有「仅重试失败项」入口，失败后只能整批重跑（重复计费、重写已成功结果）；
  · 整机成本没有服务端门禁：run_cost_review_assembly() 不检查零件是否都算出成本，
    非界面路径能在零件成本残缺时把整机成本算出来（并花一次模型钱）。

契约见 docs/specs/tech-batch-action-partial-and-retry-failed.md：
  C1 事件与运行时常量（task-partial / 统一 payload）；
  C2 成本逐件全部尝试 + crRetryFailed；
  C3 工艺 partial 语义 + retryFailedPartProcesses；
  C4 会话卡把 partial 当完成、不当失败，并提供「仅重试失败项」；
  C5 整机成本的服务端同步 409 门禁（不提交任务、不调模型）；
  C6 其它批量动作核对结论（不改代码）；C7 既有契约不放松。

验证方式：
  · 后端用带 fastapi/pydantic 的解释器（本机为 open-claude/.venv/bin/python）在子进程里真跑
    TestClient + 临时 DATA_DIR，并把 tasks.submit 打桩 —— 绝不发模型请求；
  · 前端用 Node 的 vm 加载从真实文件里抽出的函数块，用最小桩驱动控制流；
  · 运行时 / 会话卡 / 静态资源版本号做源码契约断言。
"""
from __future__ import annotations

import functools
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


COST_JS = read(FRONTEND / "cost-review.js")
APP_JS = read(FRONTEND / "app.js")
RUNTIME_JS = read(FRONTEND / "tech-board-runtime.js")
AGENT_CHAT = read(FRONTEND / "agent-chat.js")
INDEX_HTML = read(FRONTEND / "index.html")
COST_REVIEW_HTML = read(FRONTEND / "cost-review.html")
WORKBENCH_HTML = read(FRONTEND / "tech-workbench.html")
MAIN = read(ROOT / "tech_app/backend/main.py")

NODE = shutil.which("node")


def block_from(text: str, marker: str) -> str:
    """返回 marker 之后第一个配对大括号块（含大括号）；配对不上返回空串。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    # 函数体的开括号：默认参数里可能先出现 `{}`（如 options = {}），所以取
    # 「后面直接换行」的那个 `{` 作为块起点。
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
    """跑一段自包含的 JS 驱动（内含从真实文件抽出的函数块 + 最小桩）。"""
    if not NODE:
        raise unittest.SkipTest("本机没有 node，跳过前端控制流走查")
    with tempfile.TemporaryDirectory(prefix="cpq-batch-partial-js-") as tmp:
        script = pathlib.Path(tmp) / "driver.js"
        script.write_text(driver, encoding="utf-8")
        completed = subprocess.run([NODE, str(script)], capture_output=True,
                                   text=True, timeout=60)
        if completed.returncode != 0:
            raise AssertionError("JS 走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2000:],
                                    completed.stderr[-2000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


COST_PARTS_STUB = r"""
var calls = [];
var said = [];
var crBusy = false;
var crTab = "parts";
var crDeferredBusy = false;
var crLastSettle = null;
// 契约（见 spec C2）：上一轮失败清单记在模块级 crLastFailures 里，供 crRetryFailed() 复用。
var crLastFailures = null;
var crData = { parts: [
  { id: "P-001", name: "上盖", quantity: 1, has_cost: false },
  { id: "P-002", name: "支架", quantity: 1, has_cost: false },
  { id: "P-003", name: "绝缘板", quantity: 1, has_cost: false },
  { id: "P-004", name: "线缆", quantity: 1, has_cost: false }
] };
function crSay(text) { said.push(String(text)); }
function crStatus() {}
function crToast() {}
function crRender() {}
function crCard() { return { done: function () {}, log: function () {} }; }
function crPublishTask() {}
async function crSaveNote() {}
function markCost(id) {
  var row = crData.parts.filter(function (item) { return item.id === id; })[0];
  if (row) row.has_cost = true;
}
var failP002 = true;
async function crRunPart(id) {
  calls.push(id);
  if (id === "P-002" && failP002) return false;
  markCost(id);
  return true;
}
async function crRunAssembly() { return true; }
"""


def cost_driver(body: str) -> str:
    return (body + COST_PARTS_STUB + r"""
(async () => {
  var result = await crRunParts(false);
  console.log(JSON.stringify({ calls: calls, result: result, said: said }));
})().catch(function (error) {
  console.log(JSON.stringify({ error: String((error && error.message) || error),
                               calls: calls }));
});
""")


def cost_retry_driver(body: str) -> str:
    return (body + COST_PARTS_STUB + r"""
(async () => {
  await crRunParts(false);
  var firstRound = calls.slice();
  calls.length = 0;
  failP002 = false;
  await crRetryFailed();
  console.log(JSON.stringify({ firstRound: firstRound, retryRound: calls,
                               crRetryFailed: typeof crRetryFailed }));
})().catch(function (error) {
  console.log(JSON.stringify({ error: String((error && error.message) || error),
                               calls: calls }));
});
""")


COST_RUN_ALL_STUB = r"""
var crTab = "parts";
var crData = { counts: {}, final: {} };
var assemblyCalls = 0;
var partsCalls = [];
var published = [];
var said = [];
function crSay(text) { said.push(String(text)); }
function crStatus() {}
function crToast() {}
function crRender() {}
function crCard() { return { done: function () {}, log: function () {} }; }
function crPublishTask(name, extra) { published.push({ name: String(name), extra: extra || {} }); }
async function crSaveNote() {}
function crMoney(value) { return String(value); }
async function crRunPart() { return true; }
async function crRunParts(onlyMissing, onlyIds) {
  partsCalls.push({ onlyMissing: onlyMissing, onlyIds: onlyIds || null });
  return FAILURE_SUMMARY;
}
async function crRunAssembly() { assemblyCalls += 1; return true; }
"""


def cost_run_all_driver(failure_summary: str) -> str:
    body = block_from(COST_JS, "async function crRunAll(")
    return (body + COST_RUN_ALL_STUB.replace("FAILURE_SUMMARY", failure_summary) + r"""
(async () => {
  await crRunAll();
  console.log(JSON.stringify({ assemblyCalls: assemblyCalls, published: published,
                               said: said, partsCalls: partsCalls }));
})().catch(function (error) {
  console.log(JSON.stringify({ error: String((error && error.message) || error) }));
});
""")


PROCESS_STUB = r"""
var allPartsProcessBusy = false;
var currentProject = "PROJ";
var currentSelectedId = "P-001";
var currentIR = { parts: [
  { part_id: "P-001", name: "上盖" }, { part_id: "P-002", name: "支架" },
  { part_id: "P-003", name: "绝缘板" }, { part_id: "P-004", name: "线缆" },
  { part_id: "P-005", name: "输出线缆组件" }
] };
var processReadyParts = new Set();
var partCalls = [];
var progressCalls = [];
var settleCalls = [];
var statusLines = [];
var summaryCalls = 0;
var autoOpenCalls = 0;
var refreshCalls = 0;
var failParts = { "P-002": "工艺推荐失败：模型超时" };
// 契约（见 spec C3）：上一轮失败清单记在模块级 lastPartProcessFailures 里。
var lastPartProcessFailures = null;
var window = { TechBoardRuntime: { updateActionState: function () {}, publish: function () {} } };
function partProcessKey(projectId, partId) { return String(projectId) + ":" + String(partId); }
function markPartProcessReady() {}
function refreshBoardActionState() { refreshCalls += 1; }
function status(text) { statusLines.push(String(text)); }
function requestBoardSummary() { summaryCalls += 1; return { ok: true }; }
function autoOpenGeneratedProcess() { autoOpenCalls += 1; }
function allPartsProcessPublish(state) { progressCalls.push(JSON.parse(JSON.stringify(state || {}))); }
function allPartsProcessSettle(event, payload) {
  settleCalls.push({ event: String(event), payload: payload || {} });
}
async function partHasExistingProcess() { return false; }
async function runOnePartProcess(projectId, part) {
  partCalls.push(part.part_id);
  if (failParts[part.part_id]) throw new Error(failParts[part.part_id]);
  markPartProcessReady(projectId, part.part_id);
  return { status: "succeeded" };
}
"""


def process_blocks(*markers) -> str:
    parts = []
    for marker in markers:
        block = block_from(APP_JS, marker)
        if not block:
            return ""
        parts.append(block)
    return "\n".join(parts)


def process_background_driver(prelude: str) -> str:
    """真跑 app.js 的 runAllPartProcesses + runAllPartProcessesInBackground。"""
    body = process_blocks("async function runAllPartProcesses(",
                          "async function runAllPartProcessesInBackground(")
    return (body + PROCESS_STUB + prelude + r"""
(async () => {
  await runAllPartProcessesInBackground({});
  console.log(JSON.stringify({ settleCalls: settleCalls, partCalls: partCalls,
                               statusLines: statusLines, summaryCalls: summaryCalls }));
})().catch(function (error) {
  console.log(JSON.stringify({ error: String((error && error.message) || error),
                               settleCalls: settleCalls, partCalls: partCalls }));
});
""")


def process_retry_driver() -> str:
    """先整批跑一轮（P-002 失败），修好之后再点「仅重试失败项」。"""
    body = process_blocks("async function runAllPartProcesses(",
                          "async function retryFailedPartProcesses(")
    return (body + PROCESS_STUB + r"""
(async () => {
  await runAllPartProcesses({});
  var firstRound = partCalls.slice();
  failParts = {};
  partCalls.length = 0;
  await retryFailedPartProcesses();
  console.log(JSON.stringify({ firstRound: firstRound, retryRound: partCalls,
                               retryFn: typeof retryFailedPartProcesses }));
})().catch(function (error) {
  console.log(JSON.stringify({ error: String((error && error.message) || error),
                               partCalls: partCalls }));
});
""")


CHILD = r'''
import json
import os
import sys
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

from starlette.testclient import TestClient

import tech_app.backend.main as main
from tech_app.backend.models.integration import IntegrationParamPlan, IntegrationPlan
from tech_app.backend.models.ir import DesignIR
from tech_app.backend.models.process import ProcessPlan, ProcessStep
from tech_app.backend.services import integration
from tech_app.backend.storage import store

client = TestClient(main.app, raise_server_exceptions=False)

# tasks.submit 打桩：本批验的是「门禁在提交之前」，绝不真跑后台任务 / 不调模型。
submitted = []


def fake_submit(project_id, kind, job=None, *args, **kwargs):
    submitted.append({"project": project_id, "kind": kind})
    return "task-stub-%d" % len(submitted)


main.tasks.submit = fake_submit

PARTS = [
    {"part_id": "P-001", "name": "上盖", "quantity": 1,
     "features": [{"type": "box", "length": 108, "width": 56, "height": 13.25}]},
    {"part_id": "P-002", "name": "输出线缆组件", "model_no": "XT30", "quantity": 1,
     "features": [{"type": "box", "length": 60, "width": 40, "height": 20}]},
]


def new_project(tag, with_process=True):
    pid = store.create_project(source_filename="电池图纸案例1.png", source_bytes=b"png",
                               note="batch partial probe " + tag, owner="tester")
    store.save_ir(pid, DesignIR(device_name="电池箱", design_intent="批量容错探针",
                                parts=PARTS).model_dump(), stage="parsed")
    plan = IntegrationPlan(project_id=pid)
    plan.params = IntegrationParamPlan(product_family="other", params=[])
    if with_process:
        plan.process = ProcessPlan(steps=[ProcessStep(no=1, name="整机总装")])
    integration.save_plan(pid, plan, "tester")
    return pid


def amount(value):
    return {"part_id": "P-001", "items": [{"name": "材料费", "category": "material",
                                           "quantity": 1, "unit_price": value,
                                           "amount": value}]}


def assembly(pid):
    response = client.post("/api/projects/%s/cost-review/assembly" % pid)
    body = {}
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - 非 JSON 响应也要如实回给测试
        body = {"raw": response.text[:200]}
    return {"status": response.status_code, "body": body,
            "submitted": len(submitted)}


out = {}

# 场景一：零件成本残缺（P-002 还没算）→ 必须就地 409，且不提交任务。
pid = new_project("gate")
store.save_cost(pid, "P-001", amount(10.0))
out["gated"] = assembly(pid)
out["gated_assembly_written"] = bool(getattr(integration.load_plan(pid), "cost", None))

# 场景二：零件成本补齐 → 允许提交。
store.save_cost(pid, "P-002", amount(20.0))
out["allowed"] = assembly(pid)
out["submitted_after"] = len(submitted)

# 场景三：既有门禁不变（没有 2.2 组装工艺仍是 400）。
pid2 = new_project("no-process", with_process=False)
store.save_cost(pid2, "P-001", amount(10.0))
store.save_cost(pid2, "P-002", amount(20.0))
out["no_process"] = assembly(pid2)

print(json.dumps(out, ensure_ascii=False, default=str))
'''


def backend_python() -> str:
    candidates = [sys.executable, str(ROOT / "open-claude/.venv/bin/python"),
                  shutil.which("python3"), shutil.which("python")]
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", "import fastapi, pydantic"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return ""


@functools.lru_cache(maxsize=None)
def backend_probe() -> dict:
    python = backend_python()
    if not python:
        raise unittest.SkipTest("没有可 import fastapi/pydantic 的解释器，跳过整机成本门禁走查")
    data_dir = tempfile.mkdtemp(prefix="cpq-batch-partial-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-batch-partial-script-")
    try:
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run([python, str(script), data_dir, str(ROOT)],
                                   capture_output=True, text=True, timeout=600,
                                   cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError("整机成本门禁走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2000:],
                                    completed.stderr[-4000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(script_dir, ignore_errors=True)


class AssemblyGateBackendRedTest(unittest.TestCase):
    """C5：整机成本的同步门禁 —— 零件成本残缺时不许提交任务。"""

    @classmethod
    def setUpClass(cls):
        cls.data = backend_probe()

    def test_missing_part_cost_blocks_assembly(self):
        gated = self.data.get("gated") or {}
        self.assertEqual(gated.get("status"), 409,
                         f"零件成本还没算全就必须就地挡住整机成本：{gated}")
        detail = json.dumps(gated.get("body") or {}, ensure_ascii=False)
        self.assertIn("P-002", detail, f"必须点名缺失零件：{detail}")

    def test_blocked_assembly_submits_no_task(self):
        gated = self.data.get("gated") or {}
        self.assertEqual(gated.get("submitted"), 0,
                         f"被门禁挡下时不得提交任务（否则等于花钱调模型）：{self.data}")
        self.assertFalse(self.data.get("gated_assembly_written"),
                         "被门禁挡下时不得写入整机成本")

    def test_complete_part_costs_allow_assembly(self):
        allowed = self.data.get("allowed") or {}
        self.assertIn(allowed.get("status"), (200, 202),
                      f"零件成本齐了就必须放行（既有链路不破）：{allowed}")
        self.assertTrue((allowed.get("body") or {}).get("task_id"),
                        f"放行时要照旧返回 task_id：{allowed}")
        self.assertEqual(self.data.get("submitted_after"), 1,
                         "放行时必须提交一次任务")

    def test_existing_process_gate_is_unchanged(self):
        blocked = self.data.get("no_process") or {}
        self.assertEqual(blocked.get("status"), 400,
                         f"没有 2.2 组装工艺仍是 400（既有门禁不得放松）：{blocked}")


class CostBatchRedTest(unittest.TestCase):
    """C2：成本逐件全部尝试 + 仅重试失败项。"""

    def test_cost_batch_tries_every_part(self):
        body = block_from(COST_JS, "async function crRunParts(")
        self.assertTrue(body, "找不到 cost-review.js 的 crRunParts()")
        outcome = run_node(cost_driver(body))
        self.assertNotIn("error", outcome, f"crRunParts 抛错：{outcome}")
        self.assertEqual(outcome.get("calls"), ["P-001", "P-002", "P-003", "P-004"],
                         f"一件失败也必须继续把后面的零件跑完：{outcome}")

    def test_cost_batch_reports_failures(self):
        body = block_from(COST_JS, "async function crRunParts(")
        outcome = run_node(cost_driver(body))
        result = outcome.get("result") or {}
        self.assertIsInstance(result, dict,
                              f"crRunParts 必须回结构化结果（不是 false）：{outcome}")
        self.assertEqual(result.get("failed"), 1, f"失败件数要如实汇总：{result}")
        ids = [str(item.get("id") or item.get("part_id") or "")
               for item in (result.get("failures") or [])]
        self.assertEqual(ids, ["P-002"], f"失败清单要点名 P-002：{result}")

    def test_subset_run_only_touches_given_parts(self):
        body = block_from(COST_JS, "async function crRunParts(")
        outcome = run_node(body + COST_PARTS_STUB + r"""
(async () => {
  await crRunParts(false, ["P-003"]);
  console.log(JSON.stringify({ calls: calls }));
})().catch(function (error) {
  console.log(JSON.stringify({ error: String((error && error.message) || error),
                               calls: calls }));
});
""")
        self.assertNotIn("error", outcome, f"只跑指定零件时抛错：{outcome}")
        self.assertEqual(outcome.get("calls"), ["P-003"],
                         f"给了 onlyIds 就只能跑这些零件（供仅重试失败项复用）：{outcome}")

    def test_run_all_with_failures_publishes_partial(self):
        outcome = run_node(cost_run_all_driver(
            '{ attempted: 4, succeeded: 3, failed: 1, skipped: 0,'
            ' failures: [{ id: "P-002", name: "支架", message: "没算出结果" }] }'))
        self.assertNotIn("error", outcome, f"crRunAll 抛错：{outcome}")
        self.assertEqual(outcome.get("assemblyCalls"), 0,
                         f"还有零件没算出来时不得跑整机成本：{outcome}")
        names = [item.get("name") for item in (outcome.get("published") or [])]
        self.assertIn("task-partial", names,
                      f"有成功也有失败时必须上报部分完成：{outcome}")

    def test_run_all_without_failures_keeps_going(self):
        outcome = run_node(cost_run_all_driver(
            '{ attempted: 4, succeeded: 4, failed: 0, skipped: 0, failures: [] }'))
        self.assertEqual(outcome.get("assemblyCalls"), 1,
                         f"零失败时照旧跑整机成本（既有链路不破）：{outcome}")

    def test_retry_failed_only_reruns_failures(self):
        body = (block_from(COST_JS, "async function crRunParts(")
                + block_from(COST_JS, "async function crRetryFailed("))
        self.assertRegex(COST_JS, r"async function crRetryFailed\(",
                         "缺少「仅重试失败项」实现 crRetryFailed()")
        self.assertTrue("仅重试失败项" in COST_JS, "2.3 看板必须提供「仅重试失败项」入口")
        outcome = run_node(cost_retry_driver(body))
        self.assertNotIn("error", outcome, f"crRetryFailed 抛错：{outcome}")
        self.assertEqual(outcome.get("retryRound"), ["P-002"],
                         f"重试只能跑上一轮失败的零件（不重复计费）：{outcome}")


class ProcessBatchRedTest(unittest.TestCase):
    """C3：工艺批量 partial 语义 + 仅重试失败项。"""

    def test_process_batch_with_failures_settles_partial(self):
        outcome = run_node(process_background_driver(""))
        self.assertNotIn("error", outcome, f"批量工艺抛错：{outcome}")
        self.assertEqual(outcome.get("partCalls"),
                         ["P-001", "P-002", "P-003", "P-004", "P-005"],
                         f"一件失败也必须把后面的零件跑完（既有行为不得回退）：{outcome}")
        events = [item.get("event") for item in (outcome.get("settleCalls") or [])]
        self.assertEqual(events, ["task-partial"],
                         f"4 成功 + 1 失败必须是部分完成，不是整批失败：{outcome}")
        payload = ((outcome.get("settleCalls") or [{}])[0] or {}).get("payload") or {}
        self.assertEqual(payload.get("succeeded"), 4, f"payload 要带成功数：{payload}")
        self.assertEqual(payload.get("failed"), 1, f"payload 要带失败数：{payload}")
        self.assertEqual(len(payload.get("failures") or []), 1,
                         f"payload 要带失败清单：{payload}")

    def test_process_batch_with_no_success_still_fails(self):
        prelude = ('failParts = { "P-001": "模型超时", "P-002": "模型超时", "P-003": "模型超时",'
                   ' "P-004": "模型超时", "P-005": "模型超时" };' + "\n")
        outcome = run_node(process_background_driver(prelude))
        events = [item.get("event") for item in (outcome.get("settleCalls") or [])]
        self.assertEqual(events, ["task-failed"],
                         f"一件都没成功仍是整批失败：{outcome}")

    def test_process_retry_runs_only_failed_parts(self):
        self.assertRegex(APP_JS, r"async function retryFailedPartProcesses\(",
                         "缺少「仅重试失败项」实现 retryFailedPartProcesses()")
        self.assertTrue("仅重试失败项" in APP_JS, "2.1 看板必须提供「仅重试失败项」入口")
        outcome = run_node(process_retry_driver())
        self.assertNotIn("error", outcome, f"retryFailedPartProcesses 抛错：{outcome}")
        self.assertEqual(outcome.get("retryFn"), "function",
                         "retryFailedPartProcesses 必须是可调用的函数")
        self.assertEqual(outcome.get("firstRound"),
                         ["P-001", "P-002", "P-003", "P-004", "P-005"],
                         f"第一轮必须逐件跑完：{outcome}")
        self.assertEqual(outcome.get("retryRound"), ["P-002"],
                         f"「仅重试失败项」只跑上一轮失败的零件（不重复计费）：{outcome}")


class BatchSemanticsSourceRedTest(unittest.TestCase):
    """C1 / C4 / C6：运行时事件、会话卡口径、静态资源版本号。"""

    def test_runtime_exposes_partial_event(self):
        self.assertRegex(RUNTIME_JS, r"TASK_PARTIAL:\s*'task-partial'",
                         "tech-board-runtime.js 必须暴露 task-partial 事件常量")
        body = block_from(RUNTIME_JS, "function publishTaskCard(")
        self.assertTrue(body, "找不到 publishTaskCard()")
        self.assertTrue("EVENT.TASK_PARTIAL" in body,
                        "task-partial 必须在 publishTaskCard 的白名单里，否则会被运行时吞掉")

    def test_agent_chat_renders_partial_as_completion(self):
        self.assertTrue('"task-partial"' in AGENT_CHAT,
                        "会话壳必须处理 task-partial 事件")
        self.assertRegex(AGENT_CHAT, r'name === "task-partial"\)\s*\{[^}]*status:\s*"partial"',
                         "task-partial 必须按 partial 状态渲染（部分完成，不是失败）")
        branch = block_from(AGENT_CHAT, 'if (name === "task-partial")')
        self.assertNotIn("oc-task-error", branch,
                         "部分完成卡不得挂红色错误行（与去红字卡片的口径一致）")

    def test_agent_chat_offers_retry_failed_entry(self):
        self.assertTrue("仅重试失败项" in AGENT_CHAT,
                        "部分完成卡必须提供「仅重试失败项」入口")

    def test_asset_versions_are_bumped(self):
        cases = [
            ("app.js?v=20260916-pp1", INDEX_HTML, "index.html"),
            ("agent-chat.js?v=20260916-cad1", INDEX_HTML, "index.html"),
            ("agent-chat.js?v=20260916-cad1", WORKBENCH_HTML, "tech-workbench.html"),
            ("cost-review.js?v=cr9", COST_REVIEW_HTML, "cost-review.html"),
            ("tech-board-runtime.js?v=tbr1", INDEX_HTML, "index.html"),
            ("tech-board-runtime.js?v=tbr1", COST_REVIEW_HTML, "cost-review.html"),
        ]
        for old, text, where in cases:
            with self.subTest(asset=old, in_html=where):
                self.assertNotIn(old, text, f"本批改过该脚本，{where} 的 ?v= 必须 bump：{old}")


if __name__ == "__main__":
    unittest.main()
