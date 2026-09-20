"""「带缺口继续」签字的复用边界：同一批缺口不得在后续环节被重复拦。

背景（用户反馈 + 本次实测）：
  · 2.2「参数推荐」的「仍要继续」弹窗明确承诺「平台会记下是你签的字，之后不再按同一批
    缺口拦你」，后端 services/integration.py::send_to_finance() 也已经用 waiver_covers()
    复用了那一次签字；
  · 但前端 assembly-integration.js 的 aiFinanceGaps() 只读
    status.params_final / params_confirmed / process_confirmed，完全不读 status.waiver，
    于是到了「确认工艺并发送财务」又把同一批缺口摆一遍、再要一次签字 ——
    用户看到的就是「已经仍要继续了还是不能继续 / 为什么这一步还有没完成的项」；
  · 再往后的 2.3 对外回传，cost_flow.py::integration_send_to_quote_body() 在
    missing_required() 非空时无条件抛 CostFlowError，既不查 waiver_covers()，也不区分
    「本人签过字的这批缺口」和「签字之后新冒出来的缺口」，等于同一批缺口被拦第三次。

新契约见 docs/specs/tech-waiver-reuse-and-outbound-quote-handoff-with-gaps.md：
  C1 前端新增纯函数 aiWaiverCoversGaps(waiver, missingCodes, pending)，aiFinanceGaps()
     调用它并给出 covered；
  C2 gaps.covered 为真时不得再弹「仍要继续」，直接进接收人弹窗，缺口只作为风险持续展示；
  C3 对外回传保留最终完整性检查（继续引用 missing_required），但已被签字覆盖的缺口按签字
     放行并把缺口随返回体带出去；未被覆盖（新缺口）时仍然抛错、逐个点名；
  C4 L1 生成依赖、权限、写库、审核、发布、回传幂等、报价必填字段清单一律不放宽。

验证方式：
  · 后端行为用带 pydantic 的解释器（本机为 open-claude/.venv/bin/python）在子进程里真跑
    integration / cost_flow，临时 DATA_DIR、假项目、打桩的 cpq_bridge，不联网、不碰真实数据；
  · 前端纯函数用 Node 的 vm 加载从 assembly-integration.js 里抽出的真实函数驱动；
  · 接线（aiFinanceGaps 用 covered、弹窗被 covered 挡住）做源码契约断言。
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
COST_FLOW = (ROOT / "tech_app/backend/services/cost_flow.py").read_text(encoding="utf-8")
INTEGRATION = (ROOT / "tech_app/backend/services/integration.py").read_text(encoding="utf-8")
MAIN = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
PARAM_SPEC_PATH = ROOT / "tech_app/agent_knowledge/rules/quote_product_params.json"

# 报价必填字段快照（16 项）：必填项要靠豁免机制解决，不许顺手降级成选填。
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

from tech_app.backend.models.cost import CostAnalysis, CostItem
from tech_app.backend.models.integration import IntegrationParamPlan, IntegrationPlan
from tech_app.backend.models.process import ProcessPlan, ProcessStep
from tech_app.backend.services import cost_flow, integration, product_params
from tech_app.backend.storage import store

ACTOR = {"username": "wangwu", "display_name": "王五", "role": "process_manager"}
REASON = "客户先要工艺方案，报价前补齐参数"

integration.cpq_bridge.send_to_finance = lambda *args, **kwargs: {
    "task_id": "T-1", "task_no": "HT-0001", "target_role_name": "财务经理",
    "target_type": "role", "target_name": ""}
integration.cpq_bridge.send_to_quote = lambda *args, **kwargs: {
    "task_id": "T-2", "next_step_no": 3, "next_step_name": "定价-利润加成",
    "target_role_name": "销售经理", "quote_session_id": "S-1", "new_card": False}
integration.cpq_bridge.write_material = lambda *args, **kwargs: {
    "material_id": "M1", "number": "CP-0001", "name": "x",
    "material_unit_price": 1.0, "breakdown": {}, "tables": []}

PID = store.create_project(source_filename="waiver.dxf", source_bytes=b"x",
                           note="waiver handoff probe", owner="tester")


def build():
    plan = IntegrationPlan(project_id=PID)
    plan.params = IntegrationParamPlan(product_family="other", params=[])
    plan.process = ProcessPlan(steps=[ProcessStep(no=1, name="整机总装")])
    integration.save_plan(PID, plan, "tester")
    return plan


def missing(plan):
    return product_params.missing_required(plan.params) if plan.params else []


def add_cost():
    """2.3 财务已经算完成本：回传报价的前提。就地落盘，返回最新 plan。"""
    plan = integration.load_plan(PID)
    plan.cost = CostAnalysis(items=[CostItem(
        name="材料费", category="material", quantity=1, unit_price=10.0, amount=10.0)])
    integration.save_plan(PID, plan, "tester")
    return plan


def sign(codes=None, names=None, confirmations=None):
    """在 2.2 上签一次字（正式链路：send_to_finance 带 waiver）。"""
    plan = integration.load_plan(PID)
    rows = missing(plan)
    if codes is None:
        return integration.send_to_finance(PID, ACTOR, waiver={"reason": REASON})
    integration.record_waiver(
        plan, "finance_handoff",
        missing_codes=codes, missing_fields=names or [],
        confirmations=confirmations or ["params_final", "params_confirmed",
                                        "process_confirmed"],
        reason=REASON, actor=ACTOR)
    integration.save_plan(PID, plan, "tester")
    return plan


def quote():
    """走 2.3 对外回传的正文（回传销售经理继续报价用的是同一条）。"""
    try:
        body = cost_flow.integration_send_to_quote_body(
            PID, product_name="整机", spec="", note="", token="", user=ACTOR)
    except Exception as exc:  # noqa: BLE001 - 真实异常类型要回给测试
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
    status = body.get("status") or {}
    return {"ok": True,
            "status": {"required_missing": status.get("required_missing"),
                       "params_complete": status.get("params_complete"),
                       "waiver": status.get("waiver")},
            "handoff": body.get("handoff")}


def audit_actions():
    return [str(row.get("action") or "") for row in store.list_audit(PID)]


out = {"case": case}

if case == "quote_without_signature":
    plan = build()
    add_cost()
    out["missing_names"] = [str(f.get("name") or "") for f in missing(plan)]
    out["result"] = quote()
elif case == "quote_after_signed_handoff":
    plan = build()
    out["missing_names"] = [str(f.get("name") or "") for f in missing(plan)]
    sign()
    add_cost()
    out["result"] = quote()
    out["audit"] = audit_actions()
    # 契约更新（签字复用批次）：回传本身会用业务主数据回填「成品描述」等字段（既有行为），
    # 所以「如实带出去」的基准是回传后的真实缺口，而不是签字那一刻的快照。
    out["missing_after"] = [str(f.get("name") or "")
                            for f in missing(integration.load_plan(PID))]
elif case == "quote_after_new_gap":
    plan = build()
    rows = missing(plan)
    codes = sorted(str(f.get("code") or "") for f in rows)
    names = [str(f.get("name") or f.get("code") or "") for f in rows]
    out["uncovered_name"] = names[-1] if names else ""
    sign(codes=codes[:1], names=names[:1])
    add_cost()
    out["result"] = quote()

print(json.dumps(out, ensure_ascii=False, default=str))
'''

NODE_HARNESS = r'''
const fs = require('fs');
const vm = require('vm');
const [fnFile, casesFile] = process.argv.slice(2);
const src = fs.readFileSync(fnFile, 'utf8').trim();
const cases = JSON.parse(fs.readFileSync(casesFile, 'utf8'));
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(src + "\n;__fn = typeof aiWaiverCoversGaps === 'function' ? aiWaiverCoversGaps : null;",
                sandbox, { filename: 'aiWaiverCoversGaps.js' });
const fn = sandbox.__fn;
const out = cases.map((item) => {
  if (typeof fn !== 'function') return { ok: false, error: 'not-a-function' };
  try {
    return { ok: true, value: !!fn(item.waiver, item.missing, item.pending) };
  } catch (error) {
    return { ok: false, error: String((error && error.message) || error) };
  }
});
console.log(JSON.stringify(out));
'''

COVERAGE_CASES = [
    {"name": "no-waiver", "waiver": None, "missing": ["weight"], "pending": ["params_final"],
     "expected": False},
    {"name": "covers-same-batch",
     "waiver": {"missing_codes": ["weight", "product_model"],
                "waived_confirmations": ["params_final", "params_confirmed",
                                         "process_confirmed"]},
     "missing": ["weight", "product_model"], "pending": ["params_final"], "expected": True},
    {"name": "new-gap-not-covered",
     "waiver": {"missing_codes": ["weight"], "waived_confirmations": ["params_final"]},
     "missing": ["weight", "product_model"], "pending": ["params_final"], "expected": False},
    {"name": "new-confirmation-not-covered",
     "waiver": {"missing_codes": ["weight"], "waived_confirmations": ["params_final"]},
     "missing": ["weight"], "pending": ["process_confirmed"], "expected": False},
    {"name": "no-gaps-left",
     "waiver": {"missing_codes": [], "waived_confirmations": []},
     "missing": [], "pending": [], "expected": True},
]


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
    data_dir = tempfile.mkdtemp(prefix="cpq-waiver-handoff-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-waiver-handoff-script-")
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


def extract_waiver_helper() -> str:
    """从 assembly-integration.js 里抽出 aiWaiverCoversGaps 的真实函数源码。

    契约更新（签字复用批次）：原来的 `rfind(pattern, 0, head + 1)` 窗口只到函数名首字符，
    任何实现都取不到（实测恒返回 -1），这里改成按 marker 出现次数判唯一 + 从 head 起
    取到配对块末尾 —— 判据不变（仍是「唯一实现」），只是能取到源码。
    """
    marker = "function aiWaiverCoversGaps("
    body = block_from(ASSEMBLY, marker)
    head = ASSEMBLY.find(marker)
    if not body or head < 0 or ASSEMBLY.count(marker) != 1:
        return ""
    # block_from() 给的是「{...}」块，函数头要自己带上：从 head 一直取到块尾。
    return ASSEMBLY[head:ASSEMBLY.find(body, head) + len(body)]


@functools.lru_cache(maxsize=1)
def waiver_helper_results() -> list:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("未安装 node，无法执行前端纯函数测试")
    source = extract_waiver_helper()
    if not source:
        raise AssertionError(
            "assembly-integration.js 里找不到 function aiWaiverCoversGaps(...) —— "
            "「签过的同一批缺口不再问第二次」必须有唯一的纯函数判定（见 spec C1）")
    workdir = tempfile.mkdtemp(prefix="cpq-waiver-node-")
    try:
        fn_file = Path(workdir) / "helper.js"
        cases_file = Path(workdir) / "cases.json"
        script = Path(workdir) / "harness.js"
        fn_file.write_text(source, encoding="utf-8")
        cases_file.write_text(json.dumps(COVERAGE_CASES), encoding="utf-8")
        script.write_text(NODE_HARNESS, encoding="utf-8")
        completed = subprocess.run(
            [node, str(script), str(fn_file), str(cases_file)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "aiWaiverCoversGaps harness 执行失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout, completed.stderr))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class WaiverIsReusedAtTheFinanceHandoff(unittest.TestCase):
    """同一批缺口在 2.2 签过字之后，2.3 对外回传不得再拦一次。"""

    @classmethod
    def setUpClass(cls):
        cls.signed = run_backend_case("quote_after_signed_handoff")

    def test_signed_gaps_do_not_block_the_outbound_handoff(self):
        result = self.signed["result"]
        self.assertIs(True, result["ok"],
                      "2.2 已经签字放行的同一批缺口，回传销售经理时必须复用那次签字，"
                      "不得再抛错：%s" % result.get("error"))

    def test_the_gap_travels_with_the_handoff_instead_of_being_hidden(self):
        result = self.signed["result"]
        self.assertIs(True, result["ok"], result.get("error"))
        status = result["status"]
        # 契约更新（签字复用批次）：回传这一步会用业务主数据回填「成品描述」
        # （product_item_name）等字段 —— 既有行为，签字只负责放行、不负责隐藏缺口。
        # 于是断言改成「报告的数量 == 回传后的真实缺口」，既不放松也不假装缺口没变。
        self.assertEqual(status["required_missing"], len(self.signed["missing_after"]),
                         "缺口只是被签字放行，数量必须如实带出去")
        self.assertGreater(status["required_missing"], 0,
                           "签字不能把缺口抹掉（只有空集合才算被覆盖）")
        self.assertLessEqual(status["required_missing"],
                             len(self.signed["missing_names"]),
                             "回传不该凭空多出签字时没有的缺口")
        self.assertIs(False, status["params_complete"],
                      "带缺口回传时 params_complete 必须是 false，不能假装参数齐了")
        self.assertTrue(status["waiver"], "返回体要带上那条签字，便于追溯是谁放的行")

    def test_the_signed_handoff_is_audited(self):
        actions = self.signed["audit"]
        self.assertIn("integration_send_to_finance_waived", actions,
                      "2.2 的签字必须留下审计")
        self.assertIn("integration_send_to_quote", actions,
                      "回传报价本身照旧要留审计")


class UnsignedGapsStillBlock(unittest.TestCase):
    """没签过字的缺口照旧拦，且 L4 与既有能力一律不放宽。"""

    @classmethod
    def setUpClass(cls):
        cls.plain = run_backend_case("quote_without_signature")
        cls.new_gap = run_backend_case("quote_after_new_gap")

    def test_no_signature_means_no_handoff(self):
        result = self.plain["result"]
        self.assertIs(False, result["ok"], "没签过字就不该放行")
        self.assertEqual("CostFlowError", result["error_type"])
        for name in self.plain["missing_names"][:3]:
            self.assertIn(name, result["error"], "错误消息要逐个点名缺哪些字段")

    def test_a_new_gap_needs_a_new_signature(self):
        result = self.new_gap["result"]
        self.assertIs(False, result["ok"], "签字之后新冒出来的缺口必须重新签字")
        self.assertEqual("CostFlowError", result["error_type"])
        self.assertIn(self.new_gap["uncovered_name"], result["error"])

    def test_the_final_completeness_check_stays_in_place(self):
        body = py_def_region(COST_FLOW, "integration_send_to_quote_body")
        self.assertTrue(body, "找不到 integration_send_to_quote_body()")
        self.assertIn("missing_required", body,
                      "最终完整性检查不得被整段删掉，只是判定要接进豁免机制")

    def test_hard_gates_and_required_codes_are_untouched(self):
        for token in ("approve", "publish", "review"):
            with self.subTest(token=token):
                self.assertIn(token, MAIN)
        route = py_def_region(MAIN, "integration_send_to_quote")
        self.assertTrue(route, "找不到 /integration/send-to-quote 路由")
        self.assertRegex(route, r"_require\(", "回传报价的权限门禁不得放宽")
        spec = json.loads(PARAM_SPEC_PATH.read_text(encoding="utf-8"))
        required = [field["code"] for field in spec["fields"] if field.get("required")]
        self.assertEqual(REQUIRED_PARAM_CODES, required,
                         "报价必填字段清单被动过：缺口要靠豁免机制解决，不许降级成选填")


class FrontendDoesNotAskTwice(unittest.TestCase):
    """前端要认得已经落库的签字，不再对同一批缺口弹第二次「仍要继续」。"""

    def test_coverage_helper_matches_the_backend_rule(self):
        results = waiver_helper_results()
        for case, row in zip(COVERAGE_CASES, results):
            with self.subTest(case=case["name"]):
                self.assertIs(True, row["ok"], row.get("error"))
                self.assertEqual(case["expected"], row["value"],
                                 "覆盖判定与后端 waiver_covers() 的规则必须一致")

    def test_finance_gaps_expose_whether_the_batch_is_already_signed(self):
        body = block_from(ASSEMBLY, "function aiFinanceGaps(")
        self.assertTrue(body, "找不到 aiFinanceGaps()")
        self.assertIn("aiWaiverCoversGaps(", body,
                      "缺口描述必须用同一个覆盖判定，不许前端另写一套")
        self.assertRegex(body, r"covered\s*:", "aiFinanceGaps() 要把 covered 交给调用方")
        self.assertIn("state.waiver", body,
                      "覆盖判定要读 status.waiver —— 那是后端已经落库的签字")

    def test_the_second_signature_prompt_is_guarded_by_covered(self):
        body = block_from(ASSEMBLY, "async function aiConfirmProcessAndSendToFinance(")
        self.assertTrue(body, "找不到 aiConfirmProcessAndSendToFinance()")
        index = body.find("aiAskProceed(")
        self.assertGreater(index, 0, "发送财务链路上应仍有「仍要继续」弹窗")
        window = body[max(0, index - 300):index]
        self.assertIn("covered", window,
                      "「仍要继续」弹窗必须被 covered 判定挡住：签过字的不再问第二次")

    def test_the_recipient_dialog_is_still_the_only_path(self):
        body = block_from(ASSEMBLY, "async function aiConfirmProcessAndSendToFinance(")
        self.assertIn("aiOpenFinanceDialog(", body,
                      "放行之后仍然走既有的接收人弹窗，不另开一条发送通道")


if __name__ == "__main__":
    unittest.main()
