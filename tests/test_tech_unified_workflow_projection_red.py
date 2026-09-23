"""批次 5B 红测：技术工艺统一流程投影、阶段完成条件与准入门禁。

背景（本次代码走查 + 用户反馈）：
  · `tech-workbench.js:refreshProgress()` 用启发式在前端拼完成态：
    `if (reqStatus) done.add('requirement-create')`（草稿也算完成）、
    `if (integration.process && integration.process.steps.length) done.add('process')`
    （只生成工序就算完成）、`if (costReview.confirmed) done.add('cost')` …；
  · 顶部只按「有没有 project」决定未来步骤能否进入，分不清「能看」与「能执行」；
  · `catch (e) { state.progress = { done: new Set() } }` 读取失败把完成态清空成未完成；
  · 每个页面自己再算一套可用性，同一件事三份实现。

目标契约见 `docs/specs/tech-unified-workflow-projection.md`：
后端出唯一投影（5 阶段 × 13 子步骤，见批次 5A 口径），前端只渲染。

验证方式：在子进程里用临时 DATA_DIR + TestClient 真跑后端（假项目、不联网、不调模型），
投影按模块 `tech_app.backend.services.workflow_projection.build_projection` 计算；
前端只做可执行性检查（纯函数在 node 里真跑 + 接线契约）。
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
PROJECTION_JS = FRONTEND / "tech-workflow-projection.js"
WORKBENCH_JS = FRONTEND / "tech-workbench.js"
PROJECTION_PY = ROOT / "tech_app" / "backend" / "services" / "workflow_projection.py"

SUBS = ("1.1", "1.2", "1.3", "2.1", "3.1", "3.2", "3.3", "4.1", "4.2", "4.3",
        "5.1", "5.2", "5.3")
STATUS_ENUM = ("not_started", "in_progress", "generated", "edited",
               "awaiting_confirmation", "confirmed", "blocked", "stale",
               "in_review", "approved", "published")
STAGE_FIELDS = ("key", "phase_no", "phase_title", "sub", "sub_title", "stage_id", "view",
                "status", "viewable", "actionable", "completed", "stale",
                "blocked_reasons", "missing_requirements", "required_role",
                "primary_action", "next_stage")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


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
from tech_app.backend.services import cost_flow, integration
from tech_app.backend.storage import store

client = TestClient(main.app, raise_server_exceptions=False)

USER = {"username": "zhaowu", "display_name": "赵五", "role": "process_manager"}
FIN = {"username": "qianliu", "display_name": "钱六", "role": "finance_manager"}

MODULE_ERROR = ""
try:
    from tech_app.backend.services import workflow_projection as wfp
except Exception as exc:  # noqa: BLE001
    wfp = None
    MODULE_ERROR = "%s: %s" % (type(exc).__name__, exc)

PARTS = [
    {"part_id": "P-001", "name": "上壳", "quantity": 1,
     "features": [{"type": "box", "length": 108, "width": 56, "height": 13.25}]},
    {"part_id": "P-002", "name": "下壳", "quantity": 1,
     "features": [{"type": "box", "length": 108, "width": 56, "height": 13.25}]},
]


def new_project(tag, parts=PARTS, ir=True, requirement_status=None):
    pid = store.create_project(source_filename="电池图纸案例1.png", source_bytes=b"png",
                               note="projection probe " + tag, owner="tester")
    if requirement_status:
        store.save_requirement(pid, {"project_id": pid, "requirement_no": "REQ-1",
                                     "title": "探针需求", "status": requirement_status})
    if ir:
        store.save_ir(pid, DesignIR(device_name="电池箱", design_intent="投影探针",
                                    parts=parts).model_dump(), stage="parsed")
    return pid


def plan_for(pid, *, steps=None, params=True):
    plan = IntegrationPlan(project_id=pid)
    plan.params = IntegrationParamPlan(product_family="other", params=[])
    if steps is not None:
        plan.process = ProcessPlan(steps=[ProcessStep(no=no, name=name) for no, name in steps])
    integration.save_plan(pid, plan, "tester")
    return plan


def amount(value):
    return {"part_id": "P-001", "items": [{"name": "材料费", "category": "material",
                                           "quantity": 1, "unit_price": value,
                                           "amount": value}]}


def projection(pid, user=None):
    if wfp is None:
        return {"module_error": MODULE_ERROR}
    try:
        proj = wfp.build_projection(pid, user or USER)
    except Exception as exc:  # noqa: BLE001
        return {"module_error": "%s: %s" % (type(exc).__name__, exc)}
    rows = {}
    for row in proj.get("stages") or []:
        rows[str(row.get("key") or "")] = row
    return {"module_error": "", "phases": proj.get("phases") or [], "stages": rows,
            "raw": proj}


def stage(pid, key, user=None):
    data = projection(pid, user)
    if data.get("module_error"):
        return {"module_error": data["module_error"]}
    return data["stages"].get(key) or {"missing": key}


out = {}
out["module_error"] = MODULE_ERROR

# 1. 只有草稿的需求单：1.1 不算完成，1.2 不可执行。
draft = new_project("draft", ir=False, requirement_status="draft")
out["draft_1_1"] = stage(draft, "1.1")
out["draft_1_2"] = stage(draft, "1.2")

# 2. 提交确认 / 确认完成 / 审核通过三个状态。
submitted = new_project("submitted", ir=False, requirement_status="pending_confirmation")
out["submitted_1_1"] = stage(submitted, "1.1")
# `## 471`：`confirmed` 要给一个**可达**的状态 —— 依赖顺序是「1.1 存草稿 → 2.1 图纸解析 →
# 1.1 提交确认 → 1.2 通过确认 → 1.3 审核」（`packaging-stage-order-equals-dependency.md` §2.1，
# `## 313` 的 `assert_requirement_drawing_parsed()` 会把没有解析过图纸的提交确认 409 挡下），
# 所以"已确认"必然意味着图纸已经解析过。以前这里 `ir=False` 造的是一个真实点不出来的合成态，
# 于是 `confirmed_1_3` 的前置里含着「请先完成 2.1 图纸解析」，那条 `actionable` 断言断言的对象
# 变成了旧行序。断言与期望值一字未改，只把夹具补成可达状态。
confirmed = new_project("confirmed", ir=True, requirement_status="pending_review")
out["confirmed_1_2"] = stage(confirmed, "1.2")
out["confirmed_1_3"] = stage(confirmed, "1.3")
approved = new_project("approved", ir=False, requirement_status="approved")
out["approved_1_3"] = stage(approved, "1.3")

# 3. 图纸解析：有零件 / 0 零件 / 0 零件已人工确认。
normal = new_project("drawing")
out["drawing_normal"] = stage(normal, "2.1")
zero = new_project("drawing-zero", parts=[])
out["drawing_zero"] = stage(zero, "2.1")
store.audit(zero, "parse_no_parts_confirmed", {"by": "赵五", "parts": 0})
out["drawing_zero_confirmed"] = stage(zero, "2.1")

# 4. 组装与整合：只生成工序 ≠ 完成；参数确认 / 工艺确认逐级解锁。
proc = new_project("process")
plan_for(proc, steps=[(10, "整机总装")])
out["steps_only_3_1"] = stage(proc, "3.1")
out["steps_only_3_2"] = stage(proc, "3.2")
out["steps_only_3_3"] = stage(proc, "3.3")
integration.confirm_params(proc, USER)
out["params_confirmed_3_2"] = stage(proc, "3.2")
integration.confirm_process(proc, USER)
out["process_confirmed_3_3"] = stage(proc, "3.3")

# 5. 成本：逐件算完 ≠ 完成；财务确认才算。
cost = new_project("cost")
plan_for(cost, steps=[(10, "整机总装")])
store.save_cost(cost, "P-001", amount(10.0))
store.save_cost(cost, "P-002", amount(20.0))
# 整机（组装）成本也要有，否则 confirm_review 会把「整机没算」当成缺口拒掉。
from tech_app.backend.models.cost import CostAnalysis
_cost_plan = integration.load_plan(cost)
_cost_plan.cost = CostAnalysis(part_id="A-001", part_name="整机电池箱",
                               items=[{"name": "材料费", "category": "material",
                                       "quantity": 1, "unit_price": 30.0, "amount": 30.0}])
integration.save_plan(cost, _cost_plan, "tester")
out["cost_unconfirmed_4_1"] = stage(cost, "4.1")
out["cost_unconfirmed_4_3"] = stage(cost, "4.3", FIN)
try:
    cost_flow.confirm_review(cost, FIN)
    out["cost_confirm_error"] = ""
except Exception as exc:  # noqa: BLE001
    out["cost_confirm_error"] = "%s: %s" % (type(exc).__name__, exc)
out["cost_confirmed_4_3"] = stage(cost, "4.3", FIN)

# 6. 报告已发布但没回传报价：5.3 不是完成态。
pub = new_project("publish")
store.save_process_report(pub, {"project_id": pub, "report_no": "RPT-1",
                                "status": "published", "prepared_at": "2026-09-17 10:00:00",
                                "version": 1, "published_at": "2026-09-17 11:00:00"})
out["published_5_1"] = stage(pub, "5.1")
out["published_5_3"] = stage(pub, "5.3")

# 7. 全新项目：都能看，后面不能执行；老项目（没有 IR）也能取到投影。
fresh = new_project("fresh", ir=False)
out["fresh_viewable"] = [str((stage(fresh, sub) or {}).get("viewable"))
                         for sub in ("1.1", "2.1", "3.3", "4.3", "5.3")]
out["fresh_actionable_5_3"] = stage(fresh, "5.3")

# 8. 权限：阶段 4 是财务的步骤，工艺经理不可执行且原因可读。
out["cost_role_for_process_manager"] = stage(cost, "4.3", USER)

# 9. 幂等：连续两次的 stages 必须一致。
first = projection(normal)
second = projection(normal)
out["idempotent_computable"] = not (first.get("module_error") or second.get("module_error"))
out["idempotent"] = (json.dumps(first.get("stages"), sort_keys=True, default=str)
                     == json.dumps(second.get("stages"), sort_keys=True, default=str))

# 10. HTTP 契约 + 既有 /workflow 不动。
response = client.get("/api/projects/%s/workflow/projection" % normal)
out["http_status"] = response.status_code
try:
    body = response.json()
except Exception:  # noqa: BLE001
    body = {"raw": response.text[:200]}
out["http_keys"] = sorted(body.keys()) if isinstance(body, dict) else []
out["http_stage_count"] = len(body.get("stages") or []) if isinstance(body, dict) else 0
out["http_phase_count"] = len(body.get("phases") or []) if isinstance(body, dict) else 0
legacy = client.get("/api/projects/%s/workflow" % normal)
out["legacy_status"] = legacy.status_code
try:
    out["legacy_keys"] = sorted((legacy.json() or {}).keys())
except Exception:  # noqa: BLE001
    out["legacy_keys"] = []
missing = client.get("/api/projects/does-not-exist/workflow/projection")
out["missing_status"] = missing.status_code

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
        raise unittest.SkipTest("没有可 import fastapi/pydantic 的解释器，跳过流程投影走查")
    data_dir = tempfile.mkdtemp(prefix="cpq-projection-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-projection-script-")
    try:
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run([python, str(script), data_dir, str(ROOT)],
                                   capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError("流程投影走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-3000:],
                                    completed.stderr[-4000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(script_dir, ignore_errors=True)


class ProjectionCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = backend_probe()

    def stage(self, name: str) -> dict:
        row = self.data.get(name) or {}
        self.assertFalse(row.get("module_error"),
                         f"投影模块必须可用（{row.get('module_error')}）")
        self.assertNotIn("missing", row, f"投影里没有这一条：{name}")
        return row


class ProjectionContractTest(ProjectionCase):
    def test_module_exists(self):
        self.assertTrue(PROJECTION_PY.exists(),
                        "后端必须有唯一投影模块 tech_app/backend/services/"
                        "workflow_projection.py（导出 build_projection）")
        self.assertFalse(self.data.get("module_error"),
                         f"投影模块必须能导入并计算：{self.data.get('module_error')}")

    def test_http_route_returns_five_phases_and_thirteen_subs(self):
        self.assertEqual(200, self.data.get("http_status"),
                         f"GET /workflow/projection 必须 200：{self.data.get('http_status')}")
        self.assertEqual(5, self.data.get("http_phase_count"),
                         f"投影必须有 5 个阶段：{self.data.get('http_phase_count')}")
        self.assertEqual(13, self.data.get("http_stage_count"),
                         f"投影必须有 13 个子步骤：{self.data.get('http_stage_count')}")
        for key in ("phases", "stages", "project_id", "generated_at", "refresh_ok"):
            self.assertIn(key, self.data.get("http_keys") or [],
                          f"投影响应必须带 {key}：{self.data.get('http_keys')}")

    def test_every_stage_carries_the_contract_fields(self):
        rows = self.stage("drawing_normal")
        missing = [field for field in STAGE_FIELDS if field not in rows]
        self.assertFalse(missing, f"子步骤必须带全部契约字段，缺：{missing}")
        self.assertIn(rows.get("status"), STATUS_ENUM,
                      f"status 必须是统一枚举之一：{rows.get('status')}")

    def test_unknown_project_is_404(self):
        # 先证明路由真的存在（否则"404"只是路由不存在时的假通过）。
        self.assertEqual(200, self.data.get("http_status"), "路由必须先存在")
        self.assertEqual(404, self.data.get("missing_status"),
                         "不存在的项目必须 404，不得返回空投影")

    def test_legacy_workflow_route_is_unchanged(self):
        self.assertEqual(200, self.data.get("legacy_status"), "既有 /workflow 必须保留")
        for key in ("project", "requirement", "report", "summary", "audit"):
            self.assertIn(key, self.data.get("legacy_keys") or [],
                          f"既有 /workflow 的字段不得删：{key}")

    def test_projection_is_idempotent(self):
        self.assertTrue(self.data.get("idempotent_computable"),
                        "两次投影都必须真的算出来，否则一致性比较没有意义")
        self.assertTrue(self.data.get("idempotent"),
                        "同一项目连续两次投影必须一致（除时间戳）")


class RequirementCompletionTest(ProjectionCase):
    def test_draft_is_not_completed(self):
        row = self.stage("draft_1_1")
        self.assertFalse(row.get("completed"),
                         f"草稿不能算 1.1 创建完成：{row}")

    def test_draft_blocks_the_confirm_step(self):
        row = self.stage("draft_1_2")
        self.assertTrue(row.get("viewable"), "1.2 必须能看")
        self.assertFalse(row.get("actionable"), f"草稿时 1.2 不可执行：{row}")
        self.assertTrue(row.get("blocked_reasons"),
                        f"不可执行必须给出原因：{row}")

    def test_submitted_confirmation_completes_create(self):
        row = self.stage("submitted_1_1")
        self.assertTrue(row.get("completed"), f"提交确认后 1.1 才算完成：{row}")

    def test_confirmed_requirement_completes_confirm_and_opens_review(self):
        self.assertTrue(self.stage("confirmed_1_2").get("completed"))
        review = self.stage("confirmed_1_3")
        self.assertFalse(review.get("completed"), "还没审核通过，1.3 不算完成")
        self.assertTrue(review.get("actionable"), f"确认后 1.3 应可执行：{review}")

    def test_approved_requirement_completes_review(self):
        self.assertTrue(self.stage("approved_1_3").get("completed"))


class DrawingCompletionTest(ProjectionCase):
    def test_parsed_ir_with_parts_is_completed(self):
        self.assertTrue(self.stage("drawing_normal").get("completed"))

    def test_zero_parts_is_not_completed_and_says_why(self):
        row = self.stage("drawing_zero")
        self.assertTrue(row.get("viewable"), "2.1 必须能看")
        self.assertFalse(row.get("completed"), f"0 个零件不能算 2.1 完成：{row}")
        text = json.dumps(row.get("missing_requirements"), ensure_ascii=False)
        self.assertIn("确认", text, f"必须提示需要人工确认无零件结果：{text}")

    def test_zero_parts_with_manual_confirmation_is_completed(self):
        row = self.stage("drawing_zero_confirmed")
        self.assertTrue(row.get("completed"),
                        f"人工确认「确实没有识别出零件」后 2.1 必须完成：{row}")


class IntegrationCompletionTest(ProjectionCase):
    def test_steps_without_confirmation_is_not_completed(self):
        """只生成了工序 ≠ 3.3 组装工艺完成。"""
        row = self.stage("steps_only_3_3")
        self.assertFalse(row.get("completed"),
                         f"只生成工序不能算组装工艺完成：{row}")
        self.assertIn(row.get("status"), ("generated", "edited", "in_progress",
                                          "awaiting_confirmation"),
                      f"只生成工序的状态应是已生成/待确认：{row.get('status')}")

    def test_steps_without_confirmation_do_not_open_cost(self):
        row = self.stage("steps_only_3_3")
        self.assertTrue(row.get("viewable"))
        self.assertFalse(row.get("actionable"), f"未确认的工艺不可执行：{row}")

    def test_params_confirmation_completes_3_2(self):
        self.assertTrue(self.stage("params_confirmed_3_2").get("completed"),
                        "参数推荐人工确认后 3.2 必须完成")

    def test_process_confirmation_completes_3_3(self):
        row = self.stage("process_confirmed_3_3")
        self.assertTrue(row.get("completed"), f"组装工艺确认后 3.3 必须完成：{row}")


class CostCompletionTest(ProjectionCase):
    def test_part_costs_complete_but_summary_waits_for_confirmation(self):
        self.assertTrue(self.stage("cost_unconfirmed_4_1").get("completed"),
                        "逐件成本算完后 4.1 必须完成")
        row = self.stage("cost_unconfirmed_4_3")
        self.assertFalse(row.get("completed"),
                         f"财务没确认成本就不算 4.3 完成：{row}")
        self.assertIn(row.get("status"), ("generated", "awaiting_confirmation",
                                          "in_progress"),
                      f"未确认的成本汇总状态不对：{row.get('status')}")

    def test_confirmed_cost_completes_4_3(self):
        self.assertTrue(self.stage("cost_confirmed_4_3").get("completed"))

    def test_phase_four_is_not_actionable_for_the_process_manager(self):
        row = self.stage("cost_role_for_process_manager")
        self.assertTrue(row.get("viewable"), "阶段 4 仍然能看")
        self.assertFalse(row.get("actionable"), f"工艺经理不该能执行成本确认：{row}")
        text = json.dumps({"blocked": row.get("blocked_reasons"),
                           "role": row.get("required_role")}, ensure_ascii=False)
        self.assertTrue(text.strip("{} "), f"必须说明为什么不能执行：{row}")


class ReportCompletionTest(ProjectionCase):
    def test_published_report_completes_5_1(self):
        self.assertTrue(self.stage("published_5_1").get("completed"))

    def test_published_is_distinct_from_returned_to_quote(self):
        row = self.stage("published_5_3")
        self.assertEqual("published", row.get("status"),
                         f"已发布必须是可区分的状态 published：{row.get('status')}")
        self.assertFalse(row.get("completed"),
                         f"已发布但未回传报价不算 5.3 完成：{row}")


class GateTest(ProjectionCase):
    def test_every_stage_is_viewable_even_when_not_actionable(self):
        viewable = self.data.get("fresh_viewable") or []
        self.assertEqual(["True"] * 5, viewable,
                         f"任何阶段都必须能看（含未来步骤）：{viewable}")

    def test_future_step_is_not_actionable_on_a_fresh_project(self):
        row = self.stage("fresh_actionable_5_3")
        self.assertFalse(row.get("actionable"), f"全新项目 5.3 不可执行：{row}")
        self.assertTrue(row.get("blocked_reasons"), f"必须给出阻塞原因：{row}")


# --------------------------------------------------------------------------- #
# 前端：唯一投影消费 + 刷新失败保留旧状态
# --------------------------------------------------------------------------- #
NODE_SCRIPT = r'''
const fs = require('fs');
const path = require('path');
const root = process.argv[process.argv.length - 1];
const file = path.join(root, 'tech_app', 'frontend', 'tech-workflow-projection.js');
let out = { exists: fs.existsSync(file) };
if (out.exists) {
  const window = { };
  const document = { addEventListener() {} };
  try {
    new Function('window', 'document', fs.readFileSync(file, 'utf8'))(window, document);
  } catch (error) {
    out.error = String((error && error.message) || error);
  }
  const api = window.TechWorkflowProjection;
  out.api = typeof api;
  out.methods = api ? Object.keys(api) : [];
  if (api && typeof api.progress === 'function') {
    const projection = {
      project_id: 'p1',
      generated_at: '2026-09-17T10:00:00',
      refresh_ok: true,
      phases: [],
      stages: [
        { key: '1.1', stage_id: 'requirement-create', status: 'confirmed', completed: true,
          viewable: true, actionable: true, blocked_reasons: [], missing_requirements: [] },
        { key: '3.3', stage_id: 'process', status: 'generated', completed: false,
          viewable: true, actionable: false, blocked_reasons: ['请先确认参数推荐'],
          missing_requirements: ['参数最终确认'] },
        { key: '5.3', stage_id: 'report-publish', status: 'published', completed: false,
          viewable: true, actionable: false, blocked_reasons: ['尚未回传报价'],
          missing_requirements: [] },
      ],
    };
    const mapped = api.progress(projection) || {};
    const done = mapped.done instanceof Set ? Array.from(mapped.done) : mapped.done;
    out.done = done;
    out.status = mapped.status ? (mapped.status instanceof Map
      ? Object.fromEntries(mapped.status) : mapped.status) : null;
    out.blocked = mapped.blocked ? (mapped.blocked instanceof Map
      ? Object.fromEntries(mapped.blocked) : mapped.blocked) : null;
  }
}
console.log(JSON.stringify(out));
'''


@functools.lru_cache(maxsize=None)
def node_probe() -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("没有 node，跳过前端纯函数走查")
    proc = subprocess.run([node, "-e", NODE_SCRIPT, "--", str(ROOT)],
                          capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    if proc.returncode != 0:
        raise AssertionError("node 走查失败：%s\n%s" % (proc.stdout[-1000:], proc.stderr[-2000:]))
    return json.loads(proc.stdout.strip().splitlines()[-1])


class FrontendProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = node_probe()
        cls.workbench = read(WORKBENCH_JS)

    def test_pure_progress_mapper_exists(self):
        self.assertTrue(self.probe.get("exists"),
                        "前端必须有 tech_app/frontend/tech-workflow-projection.js")
        self.assertEqual("object", self.probe.get("api"),
                         f"必须挂 window.TechWorkflowProjection：{self.probe}")
        self.assertIn("progress", self.probe.get("methods") or [],
                      f"必须导出 progress(projection)：{self.probe.get('methods')}")

    def test_progress_marks_only_completed_steps_as_done(self):
        done = self.probe.get("done") or []
        self.assertIn("1.1", done, "完成的子步骤必须进 done")
        self.assertNotIn("3.3", done, "只生成工序的子步骤不能进 done")
        self.assertNotIn("5.3", done, "已发布未回传不能进 done")

    def test_progress_carries_status_and_blocked_reasons(self):
        status = self.probe.get("status") or {}
        self.assertEqual("published", status.get("5.3"), f"状态要透传：{status}")
        blocked = self.probe.get("blocked") or {}
        self.assertTrue(blocked.get("3.3"), f"阻塞原因要透传：{blocked}")

    def test_workbench_no_longer_builds_completion_by_itself(self):
        heuristics = re.findall(r"done\.add\(([^)]*)\)", self.workbench)
        self.assertFalse(heuristics,
                         f"前端不得再自己拼完成态（refreshProgress 里还在 done.add）：{heuristics}")
        self.assertIn("TechWorkflowProjection", self.workbench,
                      "refreshProgress 必须消费后端投影")

    def test_refresh_failure_keeps_the_previous_state(self):
        block = self.workbench[self.workbench.find("async function refreshProgress()"):]
        block = block[:block.find("\n  }") + 4] if "\n  }" in block else block
        self.assertTrue(block, "找不到 refreshProgress()")
        self.assertNotIn("new Set()", block,
                         "刷新失败不得把完成集合清空（必须保留上一次状态）")


if __name__ == "__main__":
    unittest.main()
