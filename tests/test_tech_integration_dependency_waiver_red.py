"""2.2 出口的依赖分级与「带缺口继续」的缺口豁免（waiver）。

背景（用户反馈 + 本次实测）：
  · 参数推荐页「仍要继续」把 params_final 置回 false（main.py:2480 的 confirm=false 分支），
    发送财务时前端 aiFinanceBlocker()（assembly-integration.js:1050）与后端
    integration.send_to_finance()（services/integration.py:853）又把
    required_missing / params_final / params_confirmed / process_confirmed 四项全部当硬门禁；
  · 前端那次「仍要继续」没有留下任何可读的豁免记录，所以同一个缺口会被反复拦回来，
    用户只能逐页倒查 —— 表现为「允许进下一页，但最终仍然不能继续流程」。

新契约（见 docs/specs/tech-dependency-tiers-and-step-waivers.md）：
  L1 生成依赖（无参数推荐 / 无组装工艺）不可豁免；
  L2 质量依赖（报价必填缺口、内部阶段未确认）可由本人签字豁免，且签字要落库；
  已签过字的同一批缺口不得再拦第二次；出现新缺口要重新签字；
  L4（权限、写库、回传报价、审核、发布）一律不可豁免。

验证方式：
  · 后端行为用带 pydantic 的解释器（本机为 open-claude/.venv/bin/python）在子进程里
    真跑 integration.send_to_finance / record_waiver / status 与 cost_review.payload，
    临时 DATA_DIR、假项目、打桩的 cpq_bridge，不联网、不碰真实运行数据；
  · 前端分级与不缩水做源码契约断言（这一段没有可独立执行的纯函数）。
"""
from __future__ import annotations

import functools
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
ASSEMBLY = (FRONTEND / "assembly-integration.js").read_text(encoding="utf-8", errors="replace")
MAIN = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
INTEGRATION = (ROOT / "tech_app/backend/services/integration.py").read_text(encoding="utf-8")
COST_REVIEW = (ROOT / "tech_app/backend/services/cost_review.py").read_text(encoding="utf-8")
COST_FLOW = (ROOT / "tech_app/backend/services/cost_flow.py").read_text(encoding="utf-8")
AGENT = (ROOT / "tech_app/backend/services/oc_agent.py").read_text(encoding="utf-8")
PARAM_SPEC_PATH = ROOT / "tech_app/agent_knowledge/rules/quote_product_params.json"

# 报价必填字段快照（16 项）：本批要的是「允许带缺口继续」，绝不允许把必填偷偷降级成选填
# 来让流程通过。这份清单被动过就说明有人绕过了豁免机制。
REQUIRED_PARAM_CODES = [
    "product_series", "product_model", "product_item_code", "product_item_name",
    "cell_model", "rated_voltage", "rated_capacity", "max_continuous_current",
    "max_dimension", "weight", "operating_temperature", "capacity",
    "cooling_type", "frame_size", "film_scheme", "connector",
]

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
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

from tech_app.backend.models.integration import IntegrationParamPlan, IntegrationPlan
from tech_app.backend.models.process import ProcessPlan, ProcessStep
from tech_app.backend.services import cost_review, integration, product_params
from tech_app.backend.storage import store

ACTOR = {"username": "wangwu", "display_name": "王五", "role": "process_manager"}
REASON = "客户先要工艺方案，报价前补齐参数"


def _bridge_ok(*args, **kwargs):
    return {"task_id": "T-1", "task_no": "HT-0001", "target_role_name": "财务经理",
            "target_type": "role", "target_name": ""}


integration.cpq_bridge.send_to_finance = _bridge_ok

PID = store.create_project(source_filename="waiver.dxf", source_bytes=b"x",
                           note="waiver probe", owner="tester")


def build(with_params=True, with_process=True):
    plan = IntegrationPlan(project_id=PID)
    if with_params:
        plan.params = IntegrationParamPlan(product_family="other", params=[])
    if with_process:
        plan.process = ProcessPlan(steps=[ProcessStep(no=1, name="整机总装")])
    integration.save_plan(PID, plan, "tester")
    return plan


def missing(plan):
    return product_params.missing_required(plan.params) if plan.params else []


def waiver_rows(plan):
    rows = []
    for item in (getattr(plan, "waivers", None) or []):
        rows.append(item.model_dump() if hasattr(item, "model_dump") else dict(item))
    return rows


def send(**kwargs):
    try:
        plan = integration.send_to_finance(PID, ACTOR, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 要把真实异常类型回给测试
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
    return {"ok": True, "waivers": waiver_rows(plan)}


def record(plan, codes, names, confirmations, stage="params"):
    try:
        waiver = integration.record_waiver(
            plan, stage, missing_codes=codes, missing_fields=names,
            confirmations=confirmations, reason=REASON, actor=ACTOR)
    except Exception as exc:  # noqa: BLE001
        return {"recorded": False, "error_type": type(exc).__name__, "error": str(exc)}
    integration.save_plan(PID, plan, "tester")
    return {"recorded": True,
            "waiver": waiver.model_dump() if hasattr(waiver, "model_dump") else dict(waiver)}


def audit_actions():
    return [str(row.get("action") or "") for row in store.list_audit(PID)]


CONFIRMATIONS = ["params_final", "params_confirmed", "process_confirmed"]
out = {"case": case}

if case == "block_without_waiver":
    plan = build()
    out["missing_codes"] = sorted(str(f.get("code") or "") for f in missing(plan))
    out["missing_names"] = [str(f.get("name") or "") for f in missing(plan)]
    out["result"] = send()
elif case == "waived_send":
    plan = build()
    out["missing_codes"] = sorted(str(f.get("code") or "") for f in missing(plan))
    out["result"] = send(waiver={"reason": REASON})
    out["audit"] = audit_actions()
    out["status"] = integration.status(integration.load_plan(PID))
elif case == "params_waiver_then_send":
    plan = build()
    rows = missing(plan)
    codes = sorted(str(f.get("code") or "") for f in rows)
    names = [str(f.get("name") or "") for f in rows]
    out["missing_codes"] = codes
    out["record"] = record(plan, codes, names, CONFIRMATIONS)
    out["result"] = send()
    out["audit"] = audit_actions()
elif case == "partial_waiver_then_send":
    plan = build()
    rows = missing(plan)
    codes = sorted(str(f.get("code") or "") for f in rows)
    names = [str(f.get("name") or "") for f in rows]
    out["missing_codes"] = codes
    out["uncovered_name"] = names[-1] if names else ""
    out["record"] = record(plan, codes[:1], names[:1], CONFIRMATIONS)
    out["result"] = send()
elif case == "l1_no_params":
    build(with_params=False)
    out["result"] = send(waiver={"reason": REASON})
elif case == "l1_no_process":
    build(with_process=False)
    out["result"] = send(waiver={"reason": REASON})
elif case == "downstream_visibility":
    build()
    out["result"] = send(waiver={"reason": REASON})
    plan = integration.load_plan(PID)
    out["status"] = integration.status(plan)
    review = cost_review.load_review(PID)
    payload = cost_review.payload(PID, None, plan, review)
    out["cost_payload"] = {"waiver": payload.get("waiver"),
                           "params_complete": payload.get("params_complete")}
    out["audit"] = audit_actions()

print(json.dumps(out, ensure_ascii=False, default=str))
'''


@functools.lru_cache(maxsize=1)
def pydantic_python() -> str:
    """找一个能 import pydantic 的解释器（后端服务依赖它）。"""
    candidates = [sys.executable, str(ROOT / "open-claude/.venv/bin/python"),
                  shutil.which("python3"), shutil.which("python")]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", "import pydantic"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return ""


def run_backend_case(case: str) -> dict:
    python = pydantic_python()
    if not python:
        raise unittest.SkipTest("没有可 import pydantic 的解释器，跳过后端行为走查")
    data_dir = tempfile.mkdtemp(prefix="cpq-waiver-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-waiver-script-")
    try:
        script = Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run(
            [python, str(script), data_dir, str(ROOT), case],
            capture_output=True, text=True, timeout=240, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "后端走查 %s 失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (case, completed.returncode, completed.stdout, completed.stderr))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(script_dir, ignore_errors=True)


def py_def_region(text: str, name: str) -> str:
    """返回一个顶层 Python 函数（或路由处理函数）的整段源码，用于「函数整体没被改坏」类断言。"""
    match = re.search(r"(?:async\s+)?def\s+%s\s*\(" % re.escape(name), text)
    if not match:
        return ""
    tail = text[match.end():]
    end = re.search(r"\n(?:@app\.|(?:async\s+)?def\s+\w+\s*\()", tail)
    return text[match.start(): match.end() + (end.start() if end else len(tail))]


def block_from(text: str, marker: str) -> str:
    """返回 marker 之后第一个配对大括号块（含大括号）；配对不上返回空串。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(marker))
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
                return text[brace:index + 1]
        index += 1
    return ""


class BackendWaiverContract(unittest.TestCase):
    """L1 不可豁免、L2 签字可放行、同一缺口不重复拦。"""

    _cache: dict = {}

    @classmethod
    def data(cls, case: str) -> dict:
        if case not in cls._cache:
            cls._cache[case] = run_backend_case(case)
        return cls._cache[case]

    def test_without_a_signature_the_gaps_still_block(self):
        data = self.data("block_without_waiver")
        result = data["result"]
        self.assertIs(False, result["ok"], "未签字时发送财务必须被拦住")
        self.assertEqual("IntegrationFlowError", result["error_type"])
        self.assertTrue(data["missing_codes"], "构造场景应至少缺一项报价必填")
        self.assertIn("报价必填", result["error"], result["error"])

    def test_signature_lets_the_handoff_through(self):
        result = self.data("waived_send")["result"]
        self.assertIs(True, result["ok"], result.get("error") or "带豁免仍被拦住")
        self.assertTrue(result["waivers"], "放行后必须留下豁免记录")

    def test_signature_records_actor_time_and_audit(self):
        data = self.data("waived_send")
        result = data["result"]
        self.assertIs(True, result["ok"], result.get("error") or "带豁免仍被拦住")
        self.assertTrue(result["waivers"], "放行后必须留下豁免记录")
        waiver = result["waivers"][-1]
        self.assertEqual("finance_handoff", waiver["stage"])
        self.assertEqual("王五", waiver["waived_by"])
        self.assertTrue(str(waiver["waived_at"] or "").strip())
        self.assertTrue(str(waiver["reason"] or "").strip(), "服务端要补默认原因，不能是空")
        self.assertEqual(sorted(data["missing_codes"]), sorted(waiver["missing_codes"]),
                         "豁免记录的缺口必须与服务端算出的缺口一致")
        self.assertIn("integration_send_to_finance_waived", data["audit"])

    def test_signature_carries_the_internal_confirmations(self):
        result = self.data("waived_send")["result"]
        self.assertIs(True, result["ok"], result.get("error") or "带豁免仍被拦住")
        self.assertTrue(result["waivers"], "放行后必须留下豁免记录")
        waiver = result["waivers"][-1]
        self.assertEqual(["params_confirmed", "params_final", "process_confirmed"],
                         sorted(waiver["waived_confirmations"]),
                         "带缺口放行时内部确认应被顺带补掉，而不是继续当门禁")
        self.assertEqual(sorted(self.data("waived_send")["missing_codes"]),
                         sorted(waiver["missing_codes"]))

    def test_params_waiver_is_reused_instead_of_asked_twice(self):
        data = self.data("params_waiver_then_send")
        self.assertIs(True, data["record"]["recorded"],
                      "record_waiver 必须存在且可落库：%s" % data["record"].get("error"))
        self.assertIs(True, data["result"]["ok"],
                      "同一批缺口在参数推荐签过字后，发送财务不得再拦一次：%s"
                      % data["result"].get("error"))
        reuse = data["result"]["waivers"][-1]
        self.assertEqual("finance_handoff", reuse["stage"])
        self.assertIs(True, reuse["reused"], "复用已有签字时要标记 reused")
        self.assertEqual(sorted(data["missing_codes"]), sorted(reuse["missing_codes"]))

    def test_a_new_gap_needs_a_new_signature(self):
        data = self.data("partial_waiver_then_send")
        self.assertIs(True, data["record"]["recorded"], data["record"].get("error"))
        self.assertIs(False, data["result"]["ok"], "未覆盖的新缺口必须重新签字")
        self.assertIn(data["uncovered_name"], data["result"]["error"],
                      "错误消息要说清是哪个字段没被覆盖")

    def test_generation_dependencies_cannot_be_waived(self):
        for case, label in (("l1_no_params", "没有参数推荐"),
                            ("l1_no_process", "没有组装工艺")):
            with self.subTest(case=case):
                result = self.data(case)["result"]
                self.assertIs(False, result["ok"], "%s 属于 L1，不能被豁免" % label)
                self.assertEqual("IntegrationFlowError", result["error_type"])

    def test_downstream_sees_the_waiver(self):
        data = self.data("downstream_visibility")
        self.assertIs(True, data["result"]["ok"], data["result"].get("error"))
        status = data["status"]
        self.assertIs(False, status["params_complete"], "缺口还在，params_complete 必须是 false")
        self.assertTrue(status["waiver"], "status() 要把豁免摘要交给前端")
        self.assertIs(False, data["cost_payload"]["params_complete"])
        self.assertTrue(data["cost_payload"]["waiver"],
                        "2.3 财务看到的 payload 也要带上豁免，不能再按同一缺口拦人")


class FrontendDependencyTiering(unittest.TestCase):
    """前端只把 L1 当门禁，L2 缺口走「仍要继续」并留下签字。"""

    def test_finance_blocker_only_guards_generation_dependencies(self):
        body = block_from(ASSEMBLY, "function aiFinanceBlocker(")
        self.assertTrue(body, "找不到 aiFinanceBlocker()")
        self.assertIn("has_params", body)
        self.assertIn("has_process", body)
        for token in ("required_missing", "params_final", "params_confirmed", "process_confirmed"):
            with self.subTest(token=token):
                self.assertNotIn(token, body,
                                 "%s 属于 L2，应由「仍要继续」签字放行，不再当硬门禁" % token)

    def test_level2_gaps_are_described_separately(self):
        body = block_from(ASSEMBLY, "function aiFinanceGaps(")
        self.assertTrue(body, "缺少 aiFinanceGaps()：L2 缺口要说清楚，不能静默放过")
        for token in ("required_missing", "params_final", "params_confirmed", "process_confirmed"):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_send_chain_asks_for_a_signature_and_stops_on_cancel(self):
        body = block_from(ASSEMBLY, "async function aiConfirmProcessAndSendToFinance(")
        self.assertTrue(body, "找不到 aiConfirmProcessAndSendToFinance()")
        self.assertIn("aiAskProceed(", body, "L2 缺口必须复用既有「仍要继续」弹窗取得签字")
        self.assertIn("aiFinanceGaps(", body, "弹窗正文用统一的缺口描述")
        self.assertIn("waiver", body, "签字要随发送财务请求带上去")
        self.assertRegex(body, r"ok:\s*false", "取消时返回结构化失败，不发送不落库")
        self.assertIn("aiOpenFinanceDialog(", body, "接收人弹窗仍在既有通道里")

    def test_params_continue_records_the_waiver_through_existing_endpoint(self):
        body = block_from(ASSEMBLY, "async function aiConfirmParamsAndNext(")
        self.assertTrue(body, "找不到 aiConfirmParamsAndNext()")
        self.assertIn("aiParamsFinalize(", body, "仍要继续还要走既有「保存补填」落库")
        self.assertIn("waiver", body, "仍要继续必须把签字交给既有 finalize 调用")
        finalize = block_from(ASSEMBLY, "async function aiParamsFinalize(")
        self.assertIn("waiver", finalize, "finalize 请求体要带上可选 waiver（不新开路由）")

    def test_send_to_finance_payload_carries_the_waiver(self):
        body = block_from(ASSEMBLY, "async function aiRunOp(")
        self.assertTrue(body, "找不到 aiRunOp()")
        self.assertIn("waiver", body, "send-to-finance 的请求体要带上签字")


class ExistingEndpointsCarryTheWaiver(unittest.TestCase):
    """签字走既有接口的可选字段：不新增路由、不新建第二套实现。"""

    def test_finalize_endpoint_accepts_the_waiver(self):
        cls_body = re.search(r"class IntegrationFinalizeBody\(BaseModel\):[\s\S]{0,800}", MAIN)
        self.assertIsNotNone(cls_body, "找不到 IntegrationFinalizeBody")
        self.assertIn("waiver", cls_body.group(0),
                      "参数推荐页的「仍要继续」要把签字交给既有 finalize 接口")
        route = py_def_region(MAIN, "finalize_integration_params")
        self.assertTrue(route, "找不到 finalize_integration_params 路由")
        self.assertIn("waiver", route, "路由必须把签字落库（confirm=false 分支）")

    def test_send_to_finance_endpoint_accepts_the_waiver(self):
        cls_body = re.search(r"class IntegrationPublishBody\(BaseModel\):[\s\S]{0,800}", MAIN)
        self.assertIsNotNone(cls_body, "找不到 IntegrationPublishBody")
        self.assertIn("waiver", cls_body.group(0), "发送财务的请求体要能带上签字")
        route = py_def_region(MAIN, "integration_send_to_finance")
        self.assertTrue(route, "找不到 integration_send_to_finance 路由")
        self.assertIn("waiver", route, "路由必须把签字透传给 services.integration")


class LevelsOfDependencyAreNotReduced(unittest.TestCase):
    """L4 与既有能力一律不动：权限、写库、回传报价、审核、发布、Agent 自动豁免。"""

    def test_routes_and_action_names_are_intact(self):
        for token in ("/api/projects/{project_id}/integration/params/finalize",
                      "/api/projects/{project_id}/integration/params/autofill",
                      "/api/projects/{project_id}/integration/send-to-finance",
                      "/api/projects/{project_id}/integration/material-write",
                      "/api/projects/{project_id}/integration/send-to-quote"):
            with self.subTest(token=token):
                self.assertIn(token, MAIN)
        for token in ("aiAskProceed(", "aiOpenFinanceDialog(", "aiRunOp(", "aiFinanceBlocker(",
                      "aiConfirmStep('process')", "aiRequiredGaps("):
            with self.subTest(token=token):
                self.assertIn(token, ASSEMBLY)

    def test_permissions_stay_hard(self):
        route = py_def_region(MAIN, "integration_send_to_finance")
        self.assertTrue(route, "找不到发送财务的路由处理函数")
        self.assertIn("auth.MANAGER_ROLES", route, "发送财务的权限门禁不得放宽")
        self.assertRegex(route, r"_require\(", "路由必须保留权限校验调用")

    def test_external_handoff_and_publishing_keep_their_hard_gates(self):
        self.assertRegex(COST_FLOW, r"def integration_send_to_quote_body[\s\S]{0,4000}missing_required",
                         "对外回传报价的最终完整性检查不得被豁免机制绕过")
        for token in ("approve", "publish", "review"):
            with self.subTest(token=token):
                self.assertIn(token, MAIN)

    def test_agent_cannot_waive_on_its_own(self):
        body = block_from(AGENT, "def _send_integration_to_finance(")
        self.assertTrue(body, "找不到 _send_integration_to_finance()")
        self.assertNotIn("waiver", body, "豁免是人的签字，Agent 工具不得自动豁免")
        self.assertIn("requires_confirmation", AGENT)

    def test_quote_required_param_codes_are_not_relaxed(self):
        spec = json.loads(PARAM_SPEC_PATH.read_text(encoding="utf-8"))
        required = [field["code"] for field in spec["fields"] if field.get("required")]
        self.assertEqual(REQUIRED_PARAM_CODES, required,
                         "报价必填字段清单被动过：必填项要靠豁免机制解决，不许降级成选填")


if __name__ == "__main__":
    unittest.main()
