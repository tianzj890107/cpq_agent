"""2.3「确认成本」：算出来是 0 元的行不再硬拦，点了「仍要继续」就带着缺口确认。

背景（用户反馈 + 本次实测）：
  · 财务在 2.3 点「确认成本」，只要有一行是 0 元，看板上的按钮就是灰的
    （cost-review.js 用 crData.ready 禁用，而 ready 把 0 元行也算作"没算全"）；
  · 从左侧操作栏点进去时前端确实弹了「成本还有没算完的地方 … 确定要继续吗？」，
    用户点了「仍要继续」，请求发出去之后被后端 cost_flow.confirm_review() 用同一批缺口
    **再抛一次**：`这些行算出来是 0 元：…`。于是界面回到「确认失败」——
    用户看到的现象就是「现在确认成本是 0 元仍要继续就会拦着」。
  · 同一批缺口的签字在 2.2（参数推荐 / 确认工艺并发送财务）已经能落库并复用
    （见 docs/specs/tech-waiver-reuse-and-outbound-quote-handoff-with-gaps.md），
    2.3 是唯一一处「前端承诺可带缺口继续、后端没有豁免机制」的环节。

契约见 docs/specs/tech-cost-confirm-gaps-and-waiver.md：
  C1 confirm_review() 两级判定 + 签字落库（L1「没有零件」仍硬拦）；
  C2 payload().review 暴露 gaps / cost_waiver；
  C3 前端纯函数 crWaiverCoversGaps() 与后端同一条规则，0 元行不再禁用「确认成本」，
     已签字覆盖时不弹第二次「仍要继续」；
  C4 路由加可选请求体，权限与三个去向的既有校验一律不动。

验证方式：
  · 后端用带 pydantic 的解释器（本机为 open-claude/.venv/bin/python）在子进程里真跑
    cost_flow / cost_review，临时 DATA_DIR、假项目、不联网、不碰运行数据；
  · 前端纯函数用 Node 的 vm 加载从 cost-review.js 里抽出的真实函数驱动；
  · 接线（按钮禁用条件、请求体里带 waiver、covered 时不弹窗）做源码契约断言。
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
COST_JS = (FRONTEND / "cost-review.js").read_text(encoding="utf-8", errors="replace")
COST_FLOW = (ROOT / "tech_app/backend/services/cost_flow.py").read_text(encoding="utf-8")
COST_REVIEW_SVC = (ROOT / "tech_app/backend/services/cost_review.py").read_text(encoding="utf-8")
COST_REVIEW_MODEL = (ROOT / "tech_app/backend/models/cost_review.py").read_text(encoding="utf-8")
MAIN = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")

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
from tech_app.backend.models.ir import DesignIR
from tech_app.backend.models.integration import IntegrationParamPlan, IntegrationPlan
from tech_app.backend.models.process import ProcessPlan, ProcessStep
from tech_app.backend.services import cost_flow, cost_review, integration
from tech_app.backend.storage import store

ACTOR = {"username": "wangwu", "display_name": "王五", "role": "finance_manager"}
REASON = "客户先看成本，材料明细后补"

PID = store.create_project(source_filename="zero.dxf", source_bytes=b"x",
                           note="cost zero waiver probe", owner="tester")


def _amount(value):
    return {"name": "材料费", "category": "material", "quantity": 1,
            "unit_price": value, "amount": value}


def build(parts=1, part_amounts=None, assembly_amount=0.0, save_ir=True):
    """建一个 2.3 的项目：IR（零件）+ 2.2 的整机方案（含组装成本）。"""
    if save_ir:
        store.save_ir(PID, DesignIR(
            device_name="试验整机", design_intent="成本签字探针",
            parts=[{"part_id": f"P{i}", "name": f"零件{i}", "quantity": 1}
                   for i in range(1, parts + 1)]).model_dump(), stage="parsed")
        for index, value in enumerate(part_amounts or [0.0] * parts, start=1):
            store.save_cost(PID, f"P{index}", {"part_id": f"P{index}",
                                               "items": [_amount(value)]})
    plan = IntegrationPlan(project_id=PID)
    plan.params = IntegrationParamPlan(product_family="other", params=[])
    plan.process = ProcessPlan(steps=[ProcessStep(no=1, name="整机总装")])
    plan.cost = CostAnalysis(items=[CostItem(**_amount(assembly_amount))])
    integration.save_plan(PID, plan, "tester")
    return plan


def confirm(waiver=None):
    try:
        data = cost_flow.confirm_review(PID, ACTOR, waiver=waiver)
    except Exception as exc:  # noqa: BLE001 - 真实异常类型要回给测试
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
    review = data.get("review") or {}
    return {"ok": True,
            "confirmed": review.get("confirmed"),
            "gaps": review.get("gaps"),
            "cost_waiver": review.get("cost_waiver")}


def review_state():
    ir, plan, review = cost_flow.cost_review_ctx(PID)
    return {"confirmed": bool(review.confirmed),
            "waiver_count": len(review.waivers or []),
            "last": (review.waivers[-1].model_dump() if review.waivers else None),
            "gaps": cost_review.confirm_gaps(PID, ir, plan),
            "audit": [str(row.get("action") or "") for row in store.list_audit(PID)]}


out = {"case": case}

if case == "zero_no_signature":
    build()
    out["before"] = review_state()["gaps"]
    out["result"] = confirm()
    out["after"] = review_state()
elif case == "zero_with_signature":
    build()
    out["before"] = review_state()["gaps"]
    out["result"] = confirm(waiver={"reason": REASON})
    out["after"] = review_state()
elif case == "zero_with_blank_reason":
    build()
    out["result"] = confirm(waiver={})
    out["after"] = review_state()
elif case == "same_batch_twice":
    build()
    out["before"] = review_state()["gaps"]
    out["first"] = confirm(waiver={"reason": REASON})
    out["second"] = confirm()
    out["after"] = review_state()
elif case == "new_zero_after_signature":
    build(parts=2, part_amounts=[0.0, 10.0])
    out["first"] = confirm(waiver={"reason": REASON})
    store.save_cost(PID, "P2", {"part_id": "P2", "items": [_amount(0.0)]})
    out["second"] = confirm()
    out["third"] = confirm(waiver={"reason": "新冒出来的 0 元行，本人再签一次"})
    out["after"] = review_state()
elif case == "missing_parts_and_assembly":
    plan = build(parts=2, part_amounts=[10.0, 10.0])
    # 「没算成本」= 有分析但没有明细；整机成本直接留空（2.2 还没算）。
    store.save_cost(PID, "P2", {"part_id": "P2", "items": []})
    plan.cost = None
    integration.save_plan(PID, plan, "tester")
    out["before"] = review_state()["gaps"]
    out["result"] = confirm()
elif case == "no_parts_with_signature":
    build(parts=0, save_ir=False)
    out["result"] = confirm(waiver={"reason": REASON})
    out["after"] = review_state()
elif case == "zero_rows_are_named":
    build(parts=2, part_amounts=[0.0, 0.0], assembly_amount=0.0)
    out["before"] = review_state()["gaps"]

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
vm.runInContext(src + "\n;__fn = typeof crWaiverCoversGaps === 'function' ? crWaiverCoversGaps : null;",
                sandbox, { filename: 'crWaiverCoversGaps.js' });
const fn = sandbox.__fn;
const out = cases.map((item) => {
  if (typeof fn !== 'function') return { ok: false, error: 'not-a-function' };
  try {
    return { ok: true, value: !!fn(item.waiver, item.codes) };
  } catch (error) {
    return { ok: false, error: String((error && error.message) || error) };
  }
});
console.log(JSON.stringify(out));
'''

COVERAGE_CASES = [
    {"name": "no-waiver", "waiver": None, "codes": ["P1:zero"], "expected": False},
    {"name": "covers-same-batch",
     "waiver": {"missing_codes": ["P1:zero", "assembly:zero"]},
     "codes": ["P1:zero"], "expected": True},
    {"name": "new-gap-not-covered",
     "waiver": {"missing_codes": ["P1:zero"]},
     "codes": ["P1:zero", "P2:zero"], "expected": False},
    {"name": "no-gaps-left",
     "waiver": {"missing_codes": []}, "codes": [], "expected": True},
]


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


@functools.lru_cache(maxsize=None)
def run_backend_case(case: str) -> dict:
    python = pydantic_python()
    if not python:
        raise unittest.SkipTest("没有可 import pydantic 的解释器，跳过后端行为走查")
    data_dir = tempfile.mkdtemp(prefix="cpq-cost-waiver-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-cost-waiver-script-")
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


def extract_helper(name: str, text: str) -> str:
    """从源文件里抽出名为 name 的真实函数源码（函数头 + 配对块）。"""
    marker = "function %s(" % name
    body = block_from(text, marker)
    head = text.find(marker)
    if not body or head < 0 or text.count(marker) != 1:
        return ""
    return text[head:text.find(body, head) + len(body)]


def py_def_region(text: str, name: str) -> str:
    match = re.search(r"(?:async\s+)?def\s+%s\s*\(" % re.escape(name), text)
    if not match:
        return ""
    tail = text[match.end():]
    end = re.search(r"\n(?:@app\.|(?:async\s+)?def\s+\w+\s*\()", tail)
    return text[match.start(): match.end() + (end.start() if end else len(tail))]


@functools.lru_cache(maxsize=1)
def helper_results() -> list:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("未安装 node，无法执行前端纯函数测试")
    source = extract_helper("crWaiverCoversGaps", COST_JS)
    if not source:
        raise AssertionError(
            "cost-review.js 里找不到唯一一份 function crWaiverCoversGaps(...) —— "
            "「签过的同一批缺口不再问第二次」必须有唯一的纯函数判定（见 spec C3）")
    workdir = tempfile.mkdtemp(prefix="cpq-cost-waiver-node-")
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
                "crWaiverCoversGaps harness 执行失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout, completed.stderr))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class ZeroRowsCanBeWaived(unittest.TestCase):
    """0 元行只是缺口，签过字就能带着它确认 —— 这正是用户报的那一步。"""

    def test_zero_rows_without_a_signature_are_refused(self):
        data = run_backend_case("zero_no_signature")
        result = data["result"]
        self.assertIs(False, result["ok"],
                      "没签字时该如实拒绝，而不是假装确认成功")
        self.assertEqual("CostFlowError", result["error_type"])
        self.assertIn("0 元", result["error"])
        self.assertIs(False, data["after"]["confirmed"])
        self.assertFalse(data["after"]["waiver_count"])

    def test_the_refusal_tells_the_user_they_may_continue(self):
        data = run_backend_case("zero_no_signature")
        self.assertIn("仍要继续", data["result"]["error"],
                      "拒绝时要说清「可以点仍要继续带缺口确认」，不能只报错误")

    def test_continuing_with_a_signature_confirms_the_step(self):
        data = run_backend_case("zero_with_signature")
        result = data["result"]
        self.assertIs(True, result["ok"],
                      "点了「仍要继续」就该带着缺口确认成功：%s" % result.get("error"))
        self.assertIs(True, data["after"]["confirmed"])
        last = data["after"]["last"] or {}
        self.assertEqual("cost_review", last.get("stage"))
        self.assertEqual(data["before"]["codes"] if data.get("before") else last["missing_codes"],
                         last.get("missing_codes"))
        self.assertEqual("王五", last.get("waived_by"))
        self.assertTrue(last.get("waived_at"))

    def test_the_signature_keeps_the_reason_and_the_audit(self):
        data = run_backend_case("zero_with_signature")
        last = data["after"]["last"] or {}
        self.assertEqual("客户先看成本，材料明细后补", last.get("reason"))
        self.assertIn("cost_review_confirm_waived", data["after"]["audit"],
                      "带缺口确认要留审计，事后能查出是谁放的行")
        blank = run_backend_case("zero_with_blank_reason")["after"]["last"] or {}
        self.assertIn("带缺口继续", blank.get("reason") or "",
                      "没填原因时由服务端补一条默认原因")

    def test_the_gap_is_reported_with_the_confirmation(self):
        data = run_backend_case("zero_with_signature")
        gaps = data["result"]["gaps"] or {}
        self.assertTrue(gaps.get("codes"), "返回体要带上这批缺口的编码")
        self.assertTrue(gaps.get("fields") or gaps.get("names"),
                        "返回体要带上给人看的缺口名")
        self.assertTrue(data["after"]["gaps"]["codes"],
                        "确认之后缺口仍在（签字只放行，不抹掉）")

    def test_the_same_batch_is_not_blocked_twice(self):
        data = run_backend_case("same_batch_twice")
        self.assertIs(True, data["first"]["ok"], data["first"].get("error"))
        self.assertIs(True, data["second"]["ok"],
                      "同一批缺口已经签过字，第二次确认不该再拦：%s"
                      % data["second"].get("error"))
        self.assertTrue(data["result"] if False else data["after"]["confirmed"])

    def test_a_new_zero_row_needs_a_new_signature(self):
        data = run_backend_case("new_zero_after_signature")
        self.assertIs(True, data["first"]["ok"], data["first"].get("error"))
        self.assertIs(False, data["second"]["ok"],
                      "签字之后新冒出来的 0 元行必须重新签")
        self.assertIn("P2", data["second"]["error"])
        self.assertIs(True, data["third"]["ok"], data["third"].get("error"))
        self.assertEqual(2, data["after"]["waiver_count"])


class GapsAreListedAndHardGatesStayHard(unittest.TestCase):
    """缺口一次列全；L1、权限与三个去向的既有校验一律不放宽。"""

    def test_missing_parts_and_assembly_are_named_together(self):
        data = run_backend_case("missing_parts_and_assembly")
        result = data["result"]
        self.assertIs(False, result["ok"])
        self.assertIn("P2", result["error"], "没算成本的零件要点名")
        self.assertIn("整机", result["error"], "整机成本没算也要点名")

    def test_no_parts_is_still_a_hard_gate(self):
        data = run_backend_case("no_parts_with_signature")
        result = data["result"]
        self.assertIs(False, result["ok"],
                      "没有零件就没有可确认的对象（L1），签字也不放行")
        self.assertIn("零件", result["error"])
        self.assertIs(False, data["after"]["confirmed"])

    def test_zero_rows_are_listed_by_id(self):
        data = run_backend_case("zero_rows_are_named")
        before = data["before"] or {}
        codes = before.get("codes") or []
        self.assertIn("P1:zero", codes)
        self.assertIn("P2:zero", codes)
        self.assertIn("ASSY:zero", codes, "整机那一行也要单独点名")
        fields = before.get("fields") or []
        self.assertTrue(any("整机" in row for row in fields), "整机缺口要有人看的中文名")
        self.assertFalse(any("整机 " in row for row in fields),
                         "中文之间不要留空格（「整机（组装）算出来是 0 元」）")

    def test_route_permission_and_outbound_gates_are_untouched(self):
        route = py_def_region(MAIN, "confirm_cost_review")
        self.assertTrue(route, "找不到 /cost-review/confirm 路由")
        self.assertRegex(route, r"_require\(user, auth\.COST_ROLES",
                         "确认成本仍是财务权限，不得放宽")
        for name in ("write_material", "send_to_quote"):
            body = py_def_region(COST_FLOW, name)
            self.assertTrue(body, "找不到 cost_flow.%s()" % name)
            self.assertIn("_ready(", body,
                          "%s 的前置（先确认成本）不得放宽" % name)

    def test_the_confirm_body_is_optional_and_the_agent_path_is_untouched(self):
        self.assertIn("class CostConfirmBody", COST_REVIEW_MODEL,
                      "确认成本的可选请求体要落在模型文件里")
        self.assertIn("waiver", py_def_region(MAIN, "confirm_cost_review"))
        agent = (ROOT / "tech_app/backend/services/oc_agent.py").read_text(encoding="utf-8")
        call = re.search(r"cost_flow\.confirm_review\([^)]*\)", agent)
        self.assertTrue(call, "Agent 侧仍要调用同一份 confirm_review()")
        self.assertNotIn("waiver", call.group(0),
                         "Agent 不得自动豁免：缺口该报错就报错")


class FrontendUsesTheSameRule(unittest.TestCase):
    """前端认得落库的签字，0 元行不再禁用按钮，也不再弹第二次。"""

    def test_coverage_helper_matches_the_backend_rule(self):
        results = helper_results()
        for case, row in zip(COVERAGE_CASES, results):
            with self.subTest(case=case["name"]):
                self.assertIs(True, row["ok"], row.get("error"))
                self.assertEqual(case["expected"], row["value"],
                                 "覆盖判定必须与后端 waiver_covers() 一致")

    def test_the_backend_exposes_gaps_and_the_signature(self):
        body = py_def_region(COST_REVIEW_SVC, "payload")
        self.assertTrue(body, "找不到 cost_review.payload()")
        self.assertIn("confirm_gaps", COST_REVIEW_SVC)
        self.assertIn('"gaps"', body)
        self.assertIn('"cost_waiver"', body)
        self.assertIn("waivers", COST_REVIEW_MODEL)

    def test_the_confirm_button_is_not_greyed_out_by_zero_rows(self):
        block = block_from(COST_JS, "function crRenderActions(") or COST_JS
        self.assertIn("crConfirm", COST_JS)
        self.assertNotRegex(
            COST_JS, r"crConfirm'\)\.disabled\s*=\s*!ready",
            "「确认成本」不得再按 ready（把 0 元行也算成没算全）置灰 —— "
            "缺口交给签字闸门处理")

    def test_the_confirm_request_carries_the_signature(self):
        block = block_from(COST_JS, "async function crConfirmCost(")
        self.assertTrue(block, "找不到 crConfirmCost()")
        self.assertIn("waiver", block, "确认请求要把签字带给后端")
        self.assertIn("JSON.stringify", block)

    def test_the_second_prompt_is_guarded_by_the_signature(self):
        block = block_from(COST_JS, "confirmCostReview:")
        self.assertTrue(block, "找不到 confirmCostReview 动作")
        self.assertIn("crConfirmGaps()", block,
                      "缺口闸门要用同一份缺口判定，不许另写一套")
        self.assertIn("crWaiverCoversGaps(", COST_JS,
                      "覆盖判定必须与后端 waiver_covers() 同一条规则")
        self.assertRegex(block, r"if\s*\(\s*why\s*&&\s*gaps\.covered\s*\)",
                         "已签字覆盖时不得再弹「仍要继续」，只把风险说清楚")
        self.assertRegex(block, r"crConfirmCost\(waiver\)",
                         "点了「仍要继续」要带着签字走既有确认实现")

    def test_the_board_button_goes_through_the_same_gate(self):
        self.assertIn("crConfirmGate = gate", COST_JS,
                      "闸门只有一份：注册时把它交给右看板那颗按钮")
        self.assertRegex(COST_JS, r"crConfirm'\)\.onclick\s*=\s*\(\)\s*=>\s*\(crConfirmGate",
                         "右看板「确认成本」必须走同一份闸门，不能绕过签字")


if __name__ == "__main__":
    unittest.main()
