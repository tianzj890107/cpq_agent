"""CAD 批量生成改成「逐件容错 + 部分成功」（第 1 批）：一票否决必须消失。

背景（用户反馈 + 线上实证）：
  · 线上项目 `7393f6a00ccc`（电池图纸案例 1.png，5 个零件）里 `P-005 输出线缆组件`
    （型号 XT30）的 `features=[]`；
  · `main.py` 的 `/generate` 与 `/drawings` 在**预检**阶段就 `raise RuntimeError`，
    于是 `P-001 ~ P-004`（几何完全齐备）一张 3D / 2D 都拿不到，同一条错误连产生 6 次
    `failed` 任务；
  · 底层本来就是逐件容错：`geometry.generate_part()` / `drawing2d.generate_drawings()`
    返回 `ok=True/False + error`，`generate_all()` 只是逐件调一遍 —— 被上层封死了。

契约见 docs/specs/tech-cad-batch-partial-generation.md：
  C1 3D 逐件预检 + 能生成的先全部生成（结果 status=partial，带 total/succeeded/failed/skipped）；
  C2 2D 同一套逐件模型，且不要求 3D 全成功；
  C3 整批失败只剩 5 种（内核不可用 / 项目或 IR 缺失 / IR 并发变更 / 全部零件不可生成 / 基础设施异常）；
  C4 任务终态支持 partial，且是终态；
  C5 前端用结构化摘要（infrastructure_ok / processable）替代布尔门禁；
  C6 成功件立即可看，待补件就地可辨；
  C7 确定性预检问题不再反复产生异步失败任务；
  C8 权限、IR 并发保护、单零件 409、审计等既有门禁一律不放松。

验证方式：
  · 后端用带 fastapi/pydantic 的解释器（本机为 open-claude/.venv/bin/python）在子进程里
    真跑 TestClient + **真实 CadQuery**（临时 DATA_DIR、假项目、不联网、不碰运行数据）；
  · 逐件 CAD 入口的模拟只打在 `geometry.generate_part`（既有逐件入口）上，用来复现
    「单件运行期失败 / 内核不可用 / IR 并发变更」三种场景；
  · 前端与任务层做源码契约断言（轮询终态、结构化摘要、门禁、进度卡 partial）。
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


APP = read(FRONTEND / "app.js")
CHAT = read(FRONTEND / "agent-chat.js")
CHAT_CSS = read(FRONTEND / "agent-chat.css")
COST_JS = read(FRONTEND / "cost.js")
PROCESS_JS = read(FRONTEND / "process.js")
INLINE_JS = read(FRONTEND / "inline-analysis.js")
MAIN = read(ROOT / "tech_app/backend/main.py")
TASKS = read(ROOT / "tech_app/backend/services/tasks.py")
GEOMETRY = read(ROOT / "tech_app/backend/services/geometry.py")


def block_from(text: str, marker: str) -> str:
    """从 marker 起截出一个配对完整的 {...} 代码块（与其它红测同一套口径）。"""
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


# --------------------------------------------------------------------------- #
# 后端走查：真实 CadQuery + TestClient，一个子进程跑完全部场景
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

out = {}
try:
    import cadquery  # noqa: F401
    out["cadquery"] = True
except Exception:
    out["cadquery"] = False

if not out["cadquery"]:
    print(json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)

from starlette.testclient import TestClient

import tech_app.backend.main as main
from tech_app.backend.models.ir import DesignIR
from tech_app.backend.services import drawing2d, geometry
from tech_app.backend.storage import store

client = TestClient(main.app, raise_server_exceptions=False)

# 线上项目 7393f6a00ccc 的形状：4 个有基体 + 1 个 features 为空的线缆组件。
GOOD = [
    ("P-001", "上盖", "box", {"length": 108, "width": 56, "height": 13.25}),
    ("P-002", "下壳", "box", {"length": 108, "width": 56, "height": 13.25}),
    ("P-003", "绝缘板", "plate", {"length": 90, "width": 40, "thickness": 1.6}),
    ("P-004", "支架", "box", {"length": 70, "width": 40, "height": 10}),
]
CABLE = ("P-005", "输出线缆组件", "XT30")

TERMINAL = {"succeeded", "failed", "partial", "interrupted", "cancelled", "stale"}


def parts_fixture(kind="mixed"):
    """kind: mixed(4 好 + 1 空特征) / all_blank(全部空特征) / no_height(P-002 缺高度) /
    all_good(5 件全部可生成，只给「单件运行期失败」场景用)。"""
    parts = []
    for pid, name, base, dims in GOOD:
        dims = dict(dims)
        if kind == "no_height" and pid == "P-002":
            dims.pop("height", None)
        feature = {"type": base}
        feature.update({k: v for k, v in dims.items() if v is not None})
        parts.append({"part_id": pid, "name": name, "quantity": 1, "features": [feature]})
    if kind == "all_blank":
        for part in parts:
            part["features"] = []
    # 红测数据修正（CAD 逐件容错批次）：线缆件在 mixed / no_height / all_blank 下仍是
    # features=[]（那是「缺基体」场景本身），只有 all_good 场景给它一个可生成的基体 ——
    # 「单件运行期失败」这一例的断言口径是「5 件全部可生成，只有 P-002 运行期报错」，
    # 若沿用空特征，被预检挡下的 P-005 必然计入 skipped，与断言的 skipped==0 矛盾。
    cable_features = ([{"type": "box", "length": 40, "width": 20, "height": 8}]
                      if kind == "all_good" else [])
    parts.append({"part_id": CABLE[0], "name": CABLE[1], "model_no": CABLE[2],
                  "quantity": 1, "features": cable_features})
    return parts


def new_project(tag, kind="mixed"):
    pid = store.create_project(source_filename="电池图纸案例1.png", source_bytes=b"png",
                               note="cad batch probe " + tag, owner="tester")
    store.save_ir(pid, DesignIR(device_name="电池箱", design_intent="批量容错探针",
                                parts=parts_fixture(kind)).model_dump(), stage="parsed")
    return pid


def task_snapshot(pid):
    return [{"kind": t.get("kind"), "status": t.get("status"),
             "error": str(t.get("error") or "")[:200]} for t in store.list_tasks(pid)]


def poll(pid, task_id, timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get("/api/projects/%s/tasks/%s" % (pid, task_id)).json()
        if task.get("status") in TERMINAL:
            return task
        time.sleep(0.2)
    return {"status": "timeout", "error": "轮询超时"}


def submit(pid, path, body=None):
    response = client.post("/api/projects/%s%s" % (pid, path), json=body if body else None)
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text[:400]}
    return response.status_code, data


def run(pid, path):
    status, data = submit(pid, path)
    task_id = data.get("task_id") if isinstance(data, dict) else None
    return {"http": status, "submit": data, "task": poll(pid, task_id) if task_id else None}


def case_mixed():
    pid = new_project("mixed")
    three_d = run(pid, "/generate")
    two_d = run(pid, "/drawings")
    return {"project": pid, "three_d": three_d, "two_d": two_d,
            "store_3d": store.load_geometry_result(pid),
            "store_2d": store.load_drawings_result(pid),
            "tasks": task_snapshot(pid)}


def case_no_height():
    pid = new_project("no-height", "no_height")
    return {"project": pid, "three_d": run(pid, "/generate"), "tasks": task_snapshot(pid)}


def case_all_blank():
    pid = new_project("all-blank", "all_blank")
    status, data = submit(pid, "/generate")
    time.sleep(0.5)
    return {"project": pid, "http": status, "submit": data, "tasks": task_snapshot(pid)}


def case_dedup():
    pid = new_project("dedup")
    submits = []
    for _ in range(3):
        status, data = submit(pid, "/generate")
        task_id = data.get("task_id") if isinstance(data, dict) else None
        if task_id:
            poll(pid, task_id)
        submits.append({"http": status, "submit": data})
    return {"project": pid, "submits": submits, "tasks": task_snapshot(pid),
            "store_3d": store.load_geometry_result(pid)}


def _patched(fake):
    real = geometry.generate_part

    def wrapped(part, out_dir):
        return fake(part, out_dir, real)

    geometry.generate_part = wrapped
    return real


def case_kernel_unavailable():
    pid = new_project("kernel-down")

    def fake(part, out_dir, real):
        raise geometry.GeometryUnavailable("CadQuery 未安装，无法生成几何。")

    real = _patched(fake)
    try:
        return {"project": pid, "three_d": run(pid, "/generate"), "tasks": task_snapshot(pid)}
    finally:
        geometry.generate_part = real


def case_single_part_runtime_failure():
    pid = new_project("runtime-fail", "all_good")

    def fake(part, out_dir, real):
        if part.part_id == "P-002":
            return geometry.PartGeometryResult(part.part_id, part.name, ok=False,
                                               error="模拟 CAD 运行期失败")
        return real(part, out_dir)

    real = _patched(fake)
    try:
        return {"project": pid, "three_d": run(pid, "/generate"), "tasks": task_snapshot(pid)}
    finally:
        geometry.generate_part = real


def case_ir_changed_midway():
    pid = new_project("ir-changed")

    def fake(part, out_dir, real):
        if part.part_id == "P-001":
            current = store.load_ir(pid)
            current["device_name"] = "并发修改后的整机"
            store.save_ir(pid, current, stage="parsed", author="tester")
        return real(part, out_dir)

    real = _patched(fake)
    try:
        return {"project": pid, "three_d": run(pid, "/generate"), "tasks": task_snapshot(pid)}
    finally:
        geometry.generate_part = real


def case_regenerate_guard():
    pid = new_project("regenerate")
    status, data = submit(pid, "/parts/P-005/regenerate")
    return {"project": pid, "http": status, "body": data}


CASES = {
    "mixed": case_mixed,
    "no_height": case_no_height,
    "all_blank": case_all_blank,
    "dedup": case_dedup,
    "kernel_unavailable": case_kernel_unavailable,
    "runtime_failure": case_single_part_runtime_failure,
    "ir_changed": case_ir_changed_midway,
    "regenerate": case_regenerate_guard,
}

for name, fn in CASES.items():
    try:
        out[name] = fn()
    except Exception as exc:  # noqa: BLE001 — 单个场景失败不影响其它场景
        out[name] = {"probe_error": "%s: %s" % (type(exc).__name__, exc)}

print(json.dumps(out, ensure_ascii=False, default=str))
'''


def backend_python() -> str:
    """找一个能 import fastapi / pydantic 的解释器（后端服务依赖它们）。"""
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
def probe() -> dict:
    python = backend_python()
    if not python:
        raise unittest.SkipTest("没有可 import fastapi/pydantic 的解释器，跳过 CAD 批量走查")
    data_dir = tempfile.mkdtemp(prefix="cpq-cad-batch-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-cad-batch-script-")
    try:
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run(
            [python, str(script), data_dir, str(ROOT)],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "CAD 批量走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout[-2000:], completed.stderr[-4000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(script_dir, ignore_errors=True)


class CadBatchPartialBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = probe()
        if not cls.data.get("cadquery"):
            raise unittest.SkipTest("本机缺少 CadQuery，跳过真实 CAD 批量走查")

    def case(self, name: str) -> dict:
        value = self.data.get(name)
        self.assertIsInstance(value, dict, f"走查场景 {name} 缺失：{self.data.get(name)}")
        self.assertNotIn("probe_error", value, f"走查场景 {name} 抛错：{value.get('probe_error')}")
        return value

    def parts_of(self, payload) -> dict:
        self.assertIsInstance(payload, dict, f"结果不是对象：{payload!r}")
        parts = payload.get("parts")
        self.assertIsInstance(parts, list, f"结果必须带 parts 列表：{payload!r}")
        return {p.get("part_id"): p for p in parts}

    def assertPartialIsTerminal(self, body: str, where: str):
        """partial 必须与 succeeded 并列成为终态（同一行，或同一个集合字面量）。"""
        for line in body.splitlines():
            if "partial" in line and "succeeded" in line:
                return
        blob = re.sub(r"\s+", " ", body)
        for first, second in (("succeeded", "partial"), ("partial", "succeeded")):
            if re.search(r"""\[[^\]]{0,200}['"]%s['"][^\]]{0,200}['"]%s['"]""" % (first, second), blob):
                return
        self.fail(f"{where} 必须把 partial 当终态：既不继续等待，也不当失败")


class CadBatchPartialBackendRedTest(CadBatchPartialBase):
    """一票否决必须消失：一个坏零件不能再拖垮其余零件。"""

    # -------------------------------------------------- C1 逐件预检
    def test_three_d_generates_the_other_four_parts(self):
        case = self.case("mixed")
        task = case["three_d"]["task"]
        self.assertIsNotNone(task, "5 个零件里 4 个可生成，必须真的提交任务")
        self.assertEqual(task.get("status"), "partial",
                         "有零件被跳过但仍有成功件，任务终态必须是 partial：%s"
                         % task.get("error"))
        parts = self.parts_of(task.get("result"))
        for part_id in ("P-001", "P-002", "P-003", "P-004"):
            with self.subTest(part_id=part_id):
                self.assertTrue(parts.get(part_id, {}).get("ok"),
                                f"{part_id} 几何齐备，必须生成成功：{parts.get(part_id)}")
        self.assertFalse(parts.get("P-005", {}).get("ok"),
                         "P-005 features 为空，必须是未生成而不是整批失败")

    def test_three_d_result_counts_and_all_parts_present(self):
        case = self.case("mixed")
        result = self.case("mixed")["three_d"]["task"].get("result") or {}
        self.assertEqual(result.get("status"), "partial", "结果必须自带 status=partial")
        self.assertEqual(result.get("total"), 5, "total 必须是 IR 里的零件总数")
        self.assertEqual(result.get("succeeded"), 4, "4 个零件的 CAD 必须成功")
        self.assertEqual(result.get("failed"), 0, "预检跳过的零件不是运行期失败")
        self.assertEqual(result.get("skipped"), 1, "P-005 必须计入 skipped")
        self.assertEqual(result.get("skipped_parts"), ["P-005"], "跳过零件要能指名道姓")
        self.assertEqual(
            (result.get("succeeded") or 0) + (result.get("failed") or 0) + (result.get("skipped") or 0),
            result.get("total"),
            "计数必须自洽：succeeded + failed + skipped == total")
        self.assertEqual([p.get("part_id") for p in result.get("parts") or []],
                         ["P-001", "P-002", "P-003", "P-004", "P-005"],
                         "结果必须覆盖 IR 全部零件且保持 IR 顺序（UI 要显示 4 成功 + 1 待补）")

    def test_three_d_ok_parts_keep_their_existing_fields(self):
        case = self.case("mixed")
        parts = self.parts_of(case["three_d"]["task"].get("result"))
        for part_id in ("P-001", "P-002", "P-003", "P-004"):
            with self.subTest(part_id=part_id):
                entry = parts[part_id]
                self.assertTrue(entry.get("step_url"), "既有 step_url 字段不得缩水")
                self.assertTrue(entry.get("stl_url"), "既有 stl_url 字段不得缩水")
                self.assertIsNotNone(entry.get("volume_mm3"), "既有质量属性字段不得缩水")
        self.assertIn("source_ir_hash", case["three_d"]["task"].get("result") or {},
                      "既有 source_ir_hash 不得被本批删掉")

    def test_blocked_part_carries_structured_issue(self):
        case = self.case("mixed")
        parts = self.parts_of(case["three_d"]["task"].get("result"))
        entry = parts.get("P-005") or {}
        issues = entry.get("issues")
        self.assertIsInstance(issues, list, "被挡零件必须带结构化 issues：%s" % entry)
        self.assertTrue(issues, "P-005 必须至少有一条结构化问题")
        issue = issues[0]
        for key in ("part_id", "scope", "code", "field", "severity", "repair", "message"):
            with self.subTest(key=key):
                self.assertIn(key, issue, f"结构化问题缺少 {key}：{issue}")
        self.assertEqual(issue.get("part_id"), "P-005", "问题必须指名零件")
        self.assertEqual(issue.get("code"), "BASE_FEATURE_MISSING",
                         "features 为空必须报 BASE_FEATURE_MISSING")
        self.assertEqual(issue.get("repair"), "initialize_base_feature",
                         "修复动作必须是「建立简化几何」而不是「再次生成」")
        self.assertEqual(issue.get("severity"), "blocking_for_part",
                         "缺尺寸只挡这个零件，不能挡整批")
        self.assertTrue(str(issue.get("message") or "").strip(), "问题必须带可读原因")

    def test_submit_response_reports_processable_and_blocked(self):
        case = self.case("mixed")
        submit = case["three_d"]["submit"]
        self.assertTrue(submit.get("task_id"), "可生成零件仍然必须起异步任务")
        self.assertEqual(submit.get("processable"), 4, "提交响应必须给出可生成件数")
        self.assertEqual(submit.get("total"), 5, "提交响应必须给出零件总数")
        blocked = submit.get("blocked")
        self.assertIsInstance(blocked, list, "提交响应必须带 blocked 结构化清单：%s" % submit)
        self.assertEqual([b.get("part_id") for b in blocked], ["P-005"],
                         "blocked 必须列出被挡零件：%s" % blocked)
        self.assertEqual(blocked[0].get("code"), "BASE_FEATURE_MISSING",
                         "blocked 必须带 code，前端要据此显示「补充参数」")

    def test_successful_parts_are_persisted(self):
        case = self.case("mixed")
        for doc_key, label in (("store_3d", "3D"), ("store_2d", "2D")):
            with self.subTest(doc=label):
                doc = case.get(doc_key)
                self.assertIsInstance(doc, dict, f"{label} 结果必须落库：{doc!r}")
                parts = self.parts_of(doc)
                ok_ids = [pid for pid, p in parts.items() if p.get("ok")]
                self.assertEqual(sorted(ok_ids), ["P-001", "P-002", "P-003", "P-004"],
                                 f"{label} 成功项必须落库，不能被跳过项带没：{ok_ids}")
                self.assertIn("P-005", parts,
                              f"{label} 落库结果必须保留被挡零件，重进项目才看得到「待补」")

    # -------------------------------------------------- C2 2D 同一套逐件模型
    def test_two_d_generates_the_other_four_parts(self):
        case = self.case("mixed")
        task = case["two_d"]["task"]
        self.assertIsNotNone(task, "2D 与 3D 走同一套逐件模型，也必须起任务")
        self.assertEqual(task.get("status"), "partial", "2D 同样是部分成功：%s" % task.get("error"))
        parts = self.parts_of(task.get("result"))
        for part_id in ("P-001", "P-002", "P-003", "P-004"):
            with self.subTest(part_id=part_id):
                entry = parts.get(part_id, {})
                self.assertTrue(entry.get("ok"), f"{part_id} 的 2D 必须生成成功：{entry}")
                self.assertTrue(entry.get("views"), "既有 views 字段不得缩水")
        self.assertFalse(parts.get("P-005", {}).get("ok"),
                         "P-005 无基体，2D 也是跳过而不是整批失败")

    def test_two_d_does_not_depend_on_three_d_success(self):
        """2D 从 IR 重建实体，不读已保存的 3D 文件 —— 3D 里有跳过件不构成阻断。"""
        case = self.case("mixed")
        two_d = case["two_d"]["task"].get("result") or {}
        self.assertEqual(two_d.get("succeeded"), 4,
                         "3D 有一个跳过件时，2D 仍必须产出其余 4 张")
        blocked = case["two_d"]["submit"].get("blocked")
        self.assertEqual([b.get("part_id") for b in blocked or []], ["P-005"],
                         "2D 的提交响应同样要带 blocked：%s" % case["two_d"]["submit"])

    # -------------------------------------------------- 逐件语义的其它形状
    def test_missing_base_dimension_blocks_only_that_part(self):
        case = self.case("no_height")
        task = case["three_d"]["task"]
        self.assertEqual(task.get("status"), "partial",
                         "P-002 缺高度只能挡 P-002：%s" % task.get("error"))
        parts = self.parts_of(task.get("result"))
        for part_id in ("P-001", "P-003", "P-004"):
            with self.subTest(part_id=part_id):
                self.assertTrue(parts.get(part_id, {}).get("ok"),
                                f"{part_id} 不受 P-002 影响：{parts.get(part_id)}")
        self.assertEqual((task.get("result") or {}).get("succeeded"), 3,
                         "缺尺寸的 P-002 与空特征的 P-005 之外，其余 3 件必须成功")
        issue = (parts.get("P-002") or {}).get("issues") or [{}]
        self.assertEqual(issue[0].get("code"), "BASE_FEATURE_DIMENSION_MISSING",
                         "基体尺寸缺失必须报 BASE_FEATURE_DIMENSION_MISSING：%s" % issue[0])
        self.assertEqual(issue[0].get("repair"), "fill_base_feature_dimensions",
                         "修复动作是「补尺寸」而不是「重新生成」：%s" % issue[0])

    # -------------------------------------------------- C3 整批失败的五种情况
    def test_batch_fails_only_when_no_part_is_generatable(self):
        case = self.case("all_blank")
        submit = case["submit"]
        self.assertEqual(submit.get("status"), "failed",
                         "全部零件都不可生成，整批才失败：%s" % submit)
        self.assertIsNone(submit.get("task_id"),
                          "确定性失败不得再提交异步任务：%s" % submit)
        self.assertEqual(submit.get("processable"), 0, "可生成件数必须是 0")
        self.assertEqual(len(submit.get("blocked") or []), 5, "5 个零件都要给出原因")
        self.assertEqual(case["tasks"], [],
                         "整批不可执行时不应留下任何任务记录：%s" % case["tasks"])

    def test_cad_kernel_unavailable_is_a_whole_batch_failure(self):
        case = self.case("kernel_unavailable")
        task = case["three_d"]["task"] or {}
        self.assertEqual(task.get("status"), "failed",
                         "内核整体不可用属于整批失败，不得误报 partial：%s" % task)
        self.assertIn("cadquery", str(task.get("error") or "").lower(),
                      "必须保留可读的内核不可用原因：%s" % task.get("error"))

    def test_ir_changed_during_batch_still_fails(self):
        case = self.case("ir_changed")
        task = case["three_d"]["task"] or {}
        self.assertEqual(task.get("status"), "failed",
                         "IR 被并发修改仍必须整批失败（并发保护不得被本批破坏）：%s" % task)

    # -------------------------------------------------- 单件运行期失败 vs 整批
    def test_single_part_cad_failure_is_partial_not_batch_failure(self):
        case = self.case("runtime_failure")
        task = case["three_d"]["task"] or {}
        self.assertEqual(task.get("status"), "partial",
                         "单件 CAD 运行期失败其余成功，是部分成功：%s" % task.get("error"))
        result = task.get("result") or {}
        self.assertEqual(result.get("succeeded"), 4, "其余 4 件仍必须成功")
        self.assertEqual(result.get("failed"), 1, "运行期失败计入 failed")
        self.assertEqual(result.get("skipped"), 0, "P-005 几何齐备，不该被计入 skipped")
        parts = self.parts_of(result)
        issue = (parts.get("P-002") or {}).get("issues") or [{}]
        self.assertEqual(issue[0].get("code"), "CAD_GENERATION_FAILED",
                         "运行期失败要有自己的 code：%s" % issue[0])
        self.assertEqual(issue[0].get("repair"), "regenerate",
                         "运行期失败才可以「再次生成」：%s" % issue[0])

    # -------------------------------------------------- C7 不再堆失败任务
    def test_deterministic_blocked_part_creates_no_failed_task_records(self):
        case = self.case("dedup")
        failed = [t for t in case["tasks"] if t.get("kind") == "generate"
                  and t.get("status") == "failed"]
        self.assertEqual(failed, [],
                         "同一个确定性缺字段不得反复产生失败任务：%s" % case["tasks"])
        self.assertLessEqual(len([t for t in case["tasks"] if t.get("kind") == "generate"]), 3,
                             "最多与请求次数相当，不得额外膨胀：%s" % case["tasks"])
        for index, item in enumerate(case["submits"], start=1):
            submit = item["submit"]
            blocked = submit.get("blocked") if isinstance(submit, dict) else None
            self.assertEqual([b.get("part_id") for b in blocked or []], ["P-005"],
                             "第 %d 次请求仍必须就地指出缺参数零件：%s" % (index, submit))
            self.assertTrue(submit.get("task_id"),
                            "第 %d 次请求仍要为可生成零件起任务：%s" % (index, submit))
        ok_ids = sorted(pid for pid, p in self.parts_of(case.get("store_3d")).items() if p.get("ok"))
        self.assertEqual(ok_ids, ["P-001", "P-002", "P-003", "P-004"],
                         "重复请求后成功件仍必须落库：%s" % ok_ids)

    # -------------------------------------------------- C8 保护边界
    def test_regenerate_route_keeps_its_preflight_refusal(self):
        case = self.case("regenerate")
        self.assertEqual(case["http"], 409,
                         "单零件重生成的预检 409 语义本批不得改变：%s" % case["body"])

    def test_single_part_preflight_helper_is_preserved(self):
        self.assertIn("def preflight_parts(", GEOMETRY,
                      "既有 preflight_parts() 是 vision.py 与单零件重生成的依赖，不得删除")
        self.assertIn("def generate_part(", GEOMETRY,
                      "逐件 CAD 入口 generate_part() 不得改名（批量必须复用它）")
        self.assertIn('"/api/projects/{project_id}/generate"', MAIN, "3D 路由不得减少")
        self.assertIn('"/api/projects/{project_id}/drawings"', MAIN, "2D 路由不得减少")
        self.assertIn("preflight_parts", MAIN, "单零件重生成仍要用既有预检")


class CadBatchPartialFrontendRedTest(unittest.TestCase):
    """前端必须把 partial 当终态、按结构化摘要决定要不要跑 2D。"""

    @classmethod
    def setUpClass(cls):
        cls.app = APP
        cls.chat = CHAT
        cls.main = MAIN
        cls.tasks = TASKS

    def assertPartialIsTerminal(self, body: str, where: str):
        for line in body.splitlines():
            if "partial" in line and "succeeded" in line:
                return
        blob = re.sub(r"\s+", " ", body)
        for first, second in (("succeeded", "partial"), ("partial", "succeeded")):
            if re.search(r"""\[[^\]]{0,200}['"]%s['"][^\]]{0,200}['"]%s['"]""" % (first, second), blob):
                return
        self.fail(f"{where} 必须把 partial 当终态：既不继续等待，也不当失败")

    # -------------------------------------------------- C4 任务层 / 轮询
    def test_task_runner_supports_partial_terminal_status(self):
        runner = block_from(self.tasks, "def _run(")
        self.assertTrue(runner, "找不到 tasks._run()")
        self.assertIn("partial", runner,
                      "任务函数返回 status=partial 时，任务终态必须写 partial（C4）")

    def test_app_poll_task_treats_partial_as_terminal(self):
        body = block_from(self.app, "async function pollTask(")
        self.assertTrue(body, "找不到 app.js 的 pollTask()")
        self.assertPartialIsTerminal(body, "app.js pollTask()")

    def test_other_pollers_treat_partial_as_terminal(self):
        for name, body in (("cost.js", block_from(COST_JS, "async function pollTask(")),
                           ("process.js", block_from(PROCESS_JS, "async function pollTask(")),
                           ("inline-analysis.js", block_from(INLINE_JS, "async function poll("))):
            with self.subTest(file=name):
                self.assertTrue(body, f"找不到 {name} 的轮询函数")
                self.assertPartialIsTerminal(body, f"{name} 轮询")

    # -------------------------------------------------- C5 结构化门禁
    def test_generate_helpers_return_structured_summary(self):
        for name in ("generateGeometry", "generateDrawings"):
            with self.subTest(name=name):
                body = block_from(self.app, f"async function {name}(")
                self.assertTrue(body, f"找不到 {name}()")
                self.assertIn("infrastructure_ok", body,
                              f"{name}() 必须回执基础设施是否可用（调用方不再靠布尔猜）")
                for token in ("processable", "succeeded", "blocked"):
                    self.assertIn(token, body, f"{name}() 的回执必须带 {token}")

    def test_auto_generate_gate_is_not_a_bare_boolean(self):
        helper = block_from(self.app, "async function autoGenerateAfterParse(")
        self.assertTrue(helper, "找不到 autoGenerateAfterParse()")
        self.assertRegex(helper, r"infrastructure_ok",
                         "自动流程只能被基础设施 / IR 级失败挡住")
        self.assertRegex(helper, r"processable",
                         "没有可生成零件时不必空跑 2D")
        self.assertFalse(re.search(r"!\s*geometryOk\b", helper),
                         "不得再用单一布尔 gate：单件失败不能阻断其余零件的 2D")
        geo = helper.find("generateGeometry(")
        drw = helper.find("generateDrawings(")
        self.assertGreaterEqual(geo, 0, "自动流程必须先跑几何生成")
        self.assertGreater(drw, geo, "自动流程必须接着跑 2D 工程图")
        self.assertNotRegex(helper, r"if\s*\(\s*!\s*\w+\s*\)\s*return\s+false",
                            "门禁必须按 infrastructure_ok / processable 判定，而不是真假值")

    def test_run_task_handles_submit_without_task_id(self):
        body = block_from(self.app, "async function runTask(")
        self.assertTrue(body, "找不到 runTask()")
        self.assertRegex(body, r"!\s*\w*\.?task_id\b",
                         "提交响应没有 task_id（同步判定整批不可执行）时不得去轮询 null")

    def test_generate_routes_do_not_raise_on_per_part_preflight(self):
        for marker in ("def generate(project_id: str", "def drawings(project_id: str"):
            with self.subTest(route=marker):
                body = block_from(self.main, marker)
                self.assertTrue(body, f"找不到路由 {marker}")
                self.assertNotRegex(body, r"[Pp]reflight[\s\S]{0,400}?raise\s+RuntimeError",
                                    "逐件预检问题不得再让整批任务抛异常")
                self.assertIn("partial", body,
                              "批量结果必须能表达 partial（有跳过件但仍有成功件）")

    # -------------------------------------------------- C6 UI 可读性
    def test_show_generated_result_still_called_after_partial(self):
        for name in ("generateGeometry", "generateDrawings"):
            with self.subTest(name=name):
                body = block_from(self.app, f"async function {name}(")
                self.assertIn("showGeneratedResult()", body,
                              f"{name}() 完成后必须直接显示已有结果（成功的零件立即可看）")

    def test_status_line_reports_success_and_pending_counts(self):
        for name in ("generateGeometry", "generateDrawings"):
            with self.subTest(name=name):
                body = re.sub(r"\s+", " ", block_from(self.app, f"async function {name}("))
                self.assertRegex(body, r"`[^`]{0,200}成功[^`]{0,200}待补",
                                 f"{name}() 的状态行必须同时给出成功数与待补数")

    def test_part_row_marks_blocked_parts_as_pending(self):
        body = re.sub(r"\s+", " ", block_from(self.app, "function renderNode("))
        self.assertIn("待补", body,
                      "零件清单必须把「有结构化问题」的零件标成待补，而不是笼统的几何✗")
        self.assertIn("issues", body, "待补标记要读逐件 issues 字段")

    # -------------------------------------------------- 进度卡 / 桥
    def test_task_card_supports_partial_status(self):
        word = block_from(self.chat, "function taskStatusWord(")
        self.assertRegex(word, r"partial\s*:\s*['\"][^'\"]+",
                         "进度卡状态词必须补 partial（沿用蓝色，不新增红色）")
        terminal = block_from(self.chat, "function renderTaskProgress(")
        self.assertPartialIsTerminal(terminal, "agent-chat.js renderTaskProgress()")
        interrupt = block_from(self.chat, "function interruptRunningCards(")
        self.assertIn("partial", interrupt,
                      "partial 是终态，切看板时不得被就地改成「中断」")

    def test_task_card_css_has_partial_rule(self):
        self.assertRegex(CHAT_CSS, r"\.oc-task-card\.is-partial",
                         "进度卡的 .is-partial 必须有样式规则，否则状态词没有底色")

    def test_forward_task_detail_maps_partial_to_completed(self):
        body = block_from(self.app, "function forwardTaskDetail(")
        self.assertTrue(body, "找不到 forwardTaskDetail()")
        idx = body.find("partial")
        self.assertGreaterEqual(idx, 0, "桥/看板事件必须认识 partial")
        window = body[max(0, idx - 240): idx + 240]
        self.assertIn("task-completed", window,
                      "partial 必须按完成事件播给看板与进度卡，不能一直挂在处理中")


if __name__ == "__main__":
    unittest.main()
