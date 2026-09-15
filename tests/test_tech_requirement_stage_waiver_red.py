"""需求阶段（1.1 → 1.2 → 1.3）依赖分级与「带缺口继续」的缺口记录（waiver）。

背景（用户反馈 + 本次实测）：
  · 1.1 `requirement-create.js` 的 `rcPersist(true)` 在星号字段没填全时直接
    `rcToast(...) + return` —— 真硬闸门，用户没有任何「带着缺口继续」的出口；
  · 1.2 `requirement-confirm-page.js` 的 `cfAct('confirm')` 根本不看 `cfPrecheck`，
    缺口既不放行也不留痕；
  · 1.3 `requirement-review-page.js` 同样没有缺口留痕，业务批准与「能不能解析」混在一起；
  · 后端 `services/requirement_service.py` 三个流转函数都没有 `waiver` 入口，
    `RequirementDoc` 也没有任何缺口字段 —— 前端即便想签也无处可落。

新契约（见 docs/specs/tech-requirement-stage-dependency-tiers.md）：
  L1/L4（保存前置、状态机前置、人工点击、角色权限）不可豁免；
  L2（星号字段 / 完整性检查缺口、待澄清问题）允许「仍要继续」，但必须落库：
  缺口编码 + 中文名 + 签字人 + 签字时间 + 原因；同一批缺口签过一次后复用、不再重复索要。

验证方式：
  · 后端行为用带 pydantic 的解释器（本机为 open-claude/.venv/bin/python）在子进程里真跑
    requirement_service 的缺口计算 / 签字 / 三个流转函数，临时 DATA_DIR、假项目，
    不联网、不碰真实运行数据；
  · 前端「提示 + 带缺口继续」做源码契约断言（没有可独立执行的纯函数）。
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


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8", errors="replace").replace("\x00", "")


CREATE = read("tech_app/frontend/requirement-create.js")
CONFIRM = read("tech_app/frontend/requirement-confirm-page.js")
REVIEW = read("tech_app/frontend/requirement-review-page.js")
MAIN = read("tech_app/backend/main.py")
SERVICE = read("tech_app/backend/services/requirement_service.py")
MODELS = read("tech_app/backend/models/workflow.py")
AGENT = read("tech_app/backend/services/oc_agent.py")

# 既有需求相关路由集合：本批只加可选 body 字段，绝不允许新增 / 删除路由。
REQUIREMENT_ROUTES = {
    'get("/api/projects/{project_id}/requirement"',
    'get("/api/projects/{project_id}/requirement/pdf"',
    'get("/api/projects/{project_id}/requirement/precheck"',
    'get("/api/requirements"',
    'post("/api/projects/{project_id}/requirement/ai-check"',
    'post("/api/projects/{project_id}/requirement/confirm"',
    'post("/api/projects/{project_id}/requirement/extract-documents"',
    'post("/api/projects/{project_id}/requirement/return-to-draft"',
    'post("/api/projects/{project_id}/requirement/review"',
    'post("/api/projects/{project_id}/requirement/submit-confirmation"',
    'put("/api/projects/{project_id}/requirement"',
    'put("/api/projects/{project_id}/requirement/customer-credit"',
}

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

from tech_app.backend.models.workflow import RequirementDoc
from tech_app.backend.services import requirement_service
from tech_app.backend.storage import store

ACTOR = {"username": "wangwu", "display_name": "王五", "role": "process_manager"}
REASON = "客户催得急，先提交确认，缺口在 1.2 补齐"

PID = store.create_project(source_filename="req.dxf", source_bytes=b"x",
                           note="requirement waiver probe", owner="tester")


def new_doc():
    doc = RequirementDoc(project_id=PID, requirement_no="REQ-T", title="",
                         status="draft", data={})
    store.save_requirement(PID, doc.model_dump(), author="tester")
    return doc


def load_doc():
    return RequirementDoc(**store.load_requirement(PID))


def gaps_of(doc):
    try:
        return requirement_service.requirement_gaps(PID, doc)
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def waiver_rows(doc):
    rows = []
    for item in (getattr(doc, "waivers", None) or []):
        rows.append(item.model_dump() if hasattr(item, "model_dump") else dict(item))
    return rows


def audit_actions():
    return [str(row.get("action") or "") for row in store.list_audit(PID)]


def call(fn_name, *args, **kwargs):
    fn = getattr(requirement_service, fn_name, None)
    if fn is None:
        return {"ok": False, "error_type": "AttributeError", "error": "缺少 %s" % fn_name}
    try:
        out = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
    return {"ok": True, "doc": out}


out = {"case": case}
doc = new_doc()
out["baseline_gaps"] = gaps_of(doc)
out["missing_keys"] = list(out["baseline_gaps"].get("keys") or [])
out["missing_labels"] = list(out["baseline_gaps"].get("labels") or [])

if case == "submit_no_waiver":
    out["result"] = call("submit_requirement_confirmation", PID, ACTOR, "先提交")
    out["waivers"] = waiver_rows(load_doc())
    out["status"] = load_doc().status
elif case == "submit_with_waiver":
    out["result"] = call("submit_requirement_confirmation", PID, ACTOR, "带缺口提交",
                         waiver={"reason": REASON})
    out["waivers"] = waiver_rows(load_doc())
    out["status"] = load_doc().status
    out["audit"] = audit_actions()
elif case == "confirm_reuses_submit_waiver":
    out["submit"] = call("submit_requirement_confirmation", PID, ACTOR, "带缺口提交",
                         waiver={"reason": REASON})
    out["result"] = call("confirm_requirement", PID, ACTOR, "带缺口确认",
                         waiver={"reason": REASON})
    out["waivers"] = waiver_rows(load_doc())
    out["status"] = load_doc().status
    out["audit"] = audit_actions()
elif case == "confirm_fresh_waiver":
    out["submit"] = call("submit_requirement_confirmation", PID, ACTOR, "先提交")
    out["result"] = call("confirm_requirement", PID, ACTOR, "带缺口确认",
                         waiver={"reason": REASON})
    out["waivers"] = waiver_rows(load_doc())
    out["status"] = load_doc().status
    out["audit"] = audit_actions()
elif case == "review_with_waiver":
    out["submit"] = call("submit_requirement_confirmation", PID, ACTOR, "先提交")
    out["confirm"] = call("confirm_requirement", PID, ACTOR, "确认")
    out["result"] = call("review_requirement", PID, ACTOR, "approve", "带缺口批准",
                         waiver={"reason": REASON})
    out["waivers"] = waiver_rows(load_doc())
    out["status"] = load_doc().status
    out["audit"] = audit_actions()
elif case == "waiver_covers_partial":
    keys = list(out["missing_keys"])
    try:
        waiver = requirement_service.record_requirement_waiver(
            PID, doc, "submit_confirmation", gaps={"keys": keys[:1], "labels": out["missing_labels"][:1]},
            reason=REASON, actor=ACTOR)
        store.save_requirement(PID, doc.model_dump(), author="tester")
        saved = load_doc()
        out["recorded_keys"] = list(waiver.missing_keys)
        out["covers_all"] = bool(requirement_service.requirement_waiver_covers(saved, keys))
        out["covers_subset"] = bool(requirement_service.requirement_waiver_covers(saved, keys[:1]))
    except Exception as exc:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
elif case == "save_draft_keeps_waivers":
    # 草稿阶段先留一条签字，再模拟前端整份表单 PUT：签字记录必须还在。
    try:
        requirement_service.record_requirement_waiver(
            PID, doc, "submit_confirmation", reason=REASON, actor=ACTOR)
        store.save_requirement(PID, doc.model_dump(), author="tester")
        out["record_error"] = ""
    except Exception as exc:  # noqa: BLE001
        out["record_error"] = "%s: %s" % (type(exc).__name__, exc)
    fresh = {"project_id": PID, "requirement_no": "REQ-T", "title": "改过的标题",
             "status": "draft", "data": {"title": "改过的标题"}}
    try:
        requirement_service.save_requirement_draft(PID, RequirementDoc(**fresh), ACTOR)
        out["save_error"] = ""
    except Exception as exc:  # noqa: BLE001
        out["save_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["waivers"] = waiver_rows(load_doc())
elif case == "precheck_shape":
    pre = requirement_service.requirement_precheck(PID, doc)
    out["precheck_keys"] = sorted(pre.keys())
    out["gaps"] = pre.get("gaps")
    out["items"] = len(pre.get("items") or [])
    out["ok"] = pre.get("ok")
    out["note"] = pre.get("generated_note")
    out["engine"] = pre.get("engine")

print(json.dumps(out, ensure_ascii=False, default=str))
'''


@functools.lru_cache(maxsize=1)
def pydantic_python() -> str:
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
    data_dir = tempfile.mkdtemp(prefix="cpq-req-waiver-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-req-waiver-script-")
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


class BackendRequirementWaiver(unittest.TestCase):
    _cache: dict = {}

    @classmethod
    def data(cls, case: str) -> dict:
        if case not in cls._cache:
            cls._cache[case] = run_backend_case(case)
        return cls._cache[case]

    # ------------------------------------------------ 缺口口径
    def test_gap_helper_reports_codes_and_labels(self):
        data = self.data("submit_no_waiver")
        self.assertNotIn("error", data["baseline_gaps"], data["baseline_gaps"].get("error"))
        self.assertTrue(data["missing_keys"], "空需求单必须能算出缺口编码")
        self.assertEqual(len(data["missing_keys"]), len(data["missing_labels"]),
                         "缺口编码与中文名必须一一对应")
        self.assertTrue(all(str(x).strip() for x in data["missing_labels"]),
                        "缺口中文名不能是空串")

    def test_precheck_shape_is_additive(self):
        data = self.data("precheck_shape")
        for key in ("items", "ok", "generated_note", "engine"):
            with self.subTest(key=key):
                self.assertIn(key, data["precheck_keys"], "预检既有字段不得删")
        self.assertTrue(data["items"], "预检条目不得变空")
        self.assertEqual("deterministic_rules", data["engine"])
        gaps = data["gaps"]
        self.assertIsInstance(gaps, dict, "预检必须新增结构化 gaps")
        self.assertEqual(sorted(gaps.keys()), ["count", "keys", "labels"])
        self.assertEqual(len(gaps["keys"]), gaps["count"])
        self.assertTrue(gaps["keys"], "空需求单的预检缺口不能为空")

    # ------------------------------------------------ 1.1
    def test_submit_without_signature_behaves_as_before(self):
        data = self.data("submit_no_waiver")
        self.assertIs(True, data["result"]["ok"], data["result"].get("error"))
        self.assertEqual("pending_confirmation", data["status"])
        self.assertEqual([], data["waivers"], "没有签字时不得凭空写入豁免记录")

    def test_submit_with_signature_records_gaps_and_actor(self):
        data = self.data("submit_with_waiver")
        self.assertIs(True, data["result"]["ok"], data["result"].get("error"))
        self.assertEqual("pending_confirmation", data["status"])
        self.assertTrue(data["waivers"], "带缺口提交必须留下豁免记录")
        waiver = data["waivers"][-1]
        self.assertEqual("submit_confirmation", waiver["stage"])
        self.assertEqual(sorted(data["missing_keys"]), sorted(waiver["missing_keys"]),
                         "记录里的缺口必须与服务端算出的缺口一致")
        self.assertEqual(data["missing_labels"], waiver["missing_fields"],
                         "缺口中文名要按同一顺序落库")
        self.assertEqual("王五", waiver["waived_by"], "签字人要写真实姓名")
        self.assertTrue(str(waiver["waived_at"] or "").strip(), "签字时间不能为空")
        self.assertIn("客户催得急", waiver["reason"], "人工填的原因要原样落库")
        self.assertIn("workflow:requirement_submitted_waived", data["audit"])

    def test_saving_draft_does_not_wipe_waivers(self):
        data = self.data("save_draft_keeps_waivers")
        self.assertEqual("", data["save_error"], data["save_error"])
        self.assertTrue(data["waivers"], "整份表单 PUT 之后签字记录必须还在")

    # ------------------------------------------------ 1.2
    def test_confirm_with_signature_records_gap_note(self):
        data = self.data("confirm_fresh_waiver")
        self.assertIs(True, data["result"]["ok"], data["result"].get("error"))
        self.assertEqual("pending_review", data["status"])
        self.assertTrue(data["waivers"], "带缺口确认必须留下豁免记录")
        waiver = data["waivers"][-1]
        self.assertEqual("confirm", waiver["stage"])
        self.assertEqual(sorted(data["missing_keys"]), sorted(waiver["missing_keys"]))
        self.assertEqual("王五", waiver["waived_by"])
        self.assertIs(False, bool(waiver["reused"]), "首次签字不能标成复用")
        self.assertIn("workflow:requirement_confirmed_waived", data["audit"])

    def test_same_gap_is_reused_instead_of_asked_twice(self):
        data = self.data("confirm_reuses_submit_waiver")
        self.assertIs(True, data["result"]["ok"], data["result"].get("error"))
        self.assertEqual("pending_review", data["status"])
        self.assertGreaterEqual(len(data["waivers"]), 2)
        self.assertEqual("submit_confirmation", data["waivers"][-2]["stage"])
        waiver = data["waivers"][-1]
        self.assertEqual("confirm", waiver["stage"])
        self.assertIs(True, bool(waiver["reused"]), "同一批缺口已签过字要复用，不再重复索要")

    def test_partial_signature_does_not_cover_new_gaps(self):
        data = self.data("waiver_covers_partial")
        self.assertNotIn("error", data, data.get("error"))
        self.assertTrue(data["covers_subset"], "子集缺口应被同一批签字覆盖")
        self.assertIs(False, data["covers_all"], "出现新缺口必须重新签字，不能复用旧签字")

    # ------------------------------------------------ 1.3
    def test_review_with_signature_records_business_approval(self):
        data = self.data("review_with_waiver")
        self.assertIs(True, data["result"]["ok"], data["result"].get("error"))
        self.assertEqual("approved", data["status"])
        self.assertTrue(data["waivers"], "带缺口批准必须留下豁免记录")
        waiver = data["waivers"][-1]
        self.assertEqual("review", waiver["stage"])
        self.assertEqual(sorted(data["missing_keys"]), sorted(waiver["missing_keys"]))
        self.assertEqual("王五", waiver["waived_by"])
        self.assertIn("workflow:requirement_reviewed_waived", data["audit"])


class BackendModelAndRouteContract(unittest.TestCase):
    def test_waiver_model_and_doc_field_exist(self):
        self.assertRegex(MODELS, r"class\s+RequirementWaiver\s*\(BaseModel\)")
        self.assertRegex(MODELS, r"class\s+RequirementWaiver[\s\S]{0,600}?stage:\s*str")
        for field in ("missing_keys", "missing_fields", "waived_by", "waived_at",
                      "reason", "reused"):
            with self.subTest(field=field):
                self.assertRegex(MODELS, r"class\s+RequirementWaiver[\s\S]{0,800}?%s\s*:" % field)
        self.assertRegex(MODELS, r"class\s+RequirementDoc[\s\S]{0,900}?waivers\s*:\s*List\[RequirementWaiver\]")

    def test_waiver_helpers_have_a_single_implementation(self):
        for name in ("requirement_gaps", "record_requirement_waiver", "requirement_waiver_covers"):
            with self.subTest(name=name):
                self.assertIn("def %s(" % name, SERVICE, "缺口/豁免唯一实现必须在 requirement_service")
                self.assertNotIn("def %s(" % name, AGENT,
                                 "Agent 不得另写一套缺口/豁免实现，必须复用 service")

    def test_three_flows_accept_optional_waiver(self):
        for name in ("submit_requirement_confirmation", "confirm_requirement", "review_requirement"):
            with self.subTest(name=name):
                match = re.search(r"def\s+%s\(([^)]*)\)" % name, SERVICE, re.S)
                self.assertTrue(match, "找不到 %s" % name)
                self.assertIn("waiver", match.group(1), "%s 必须接受可选 waiver" % name)

    def test_requirement_routes_are_unchanged(self):
        found = set(re.findall(r'@app\.((?:get|post|put|delete)\("[^"]*requirement[^"]*")', MAIN))
        self.assertEqual(REQUIREMENT_ROUTES, found, "本批不允许新增 / 删除需求相关路由")

    def test_role_gates_are_kept(self):
        confirm_route = MAIN[MAIN.find("def confirm_requirement("):]
        confirm_route = confirm_route[:confirm_route.find("@app.")]
        self.assertIn("MANAGER_ROLES", confirm_route, "1.2 确认仍要工艺技术经理权限")
        review_route = MAIN[MAIN.find("def review_requirement("):]
        review_route = review_route[:review_route.find("@app.")]
        self.assertIn("DIRECTOR_ROLES", review_route, "1.3 审核仍要工艺技术总监权限")

    def test_agent_tools_are_kept(self):
        for tool in ("SubmitRequirementConfirmation", "ConfirmRequirement",
                     "ReturnRequirementToDraft", "ApproveRequirementReview",
                     "RejectRequirementReview", "GetRequirementPrecheck"):
            with self.subTest(tool=tool):
                self.assertIn('"name": "%s"' % tool, AGENT)


class FrontendGapContinueContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.create_persist = block_from(CREATE, "const rcPersistWithRequiredValidation=rcPersist;")
        cls.cf_act = block_from(CONFIRM, "async function cfAct(")
        cls.rr_submit = block_from(REVIEW, "async function rrSubmit(")
        cls.rr_start = block_from(REVIEW, "async function rrStart(")

    def test_blocks_are_found(self):
        for name, block in (("rcPersist", self.create_persist), ("cfAct", self.cf_act),
                            ("rrSubmit", self.rr_submit), ("rrStart", self.rr_start)):
            with self.subTest(name=name):
                self.assertTrue(block, "找不到 %s 的实现块" % name)

    # ------------------------------------------------ 1.1
    def test_create_page_no_longer_hard_blocks_missing_star_fields(self):
        self.assertIn("仍要继续", self.create_persist, "1.1 缺口必须给「仍要继续」出口")
        self.assertNotRegex(
            self.create_persist,
            r"if\s*\(\s*missing\.length\s*\)\s*\{\s*rcToast\([^)]*\)\s*;\s*rcFocusFirstRequiredField\([^)]*\)\s*;\s*return\s*;\s*\}",
            "缺星号字段不得再被直接 return 掉",
        )

    def test_create_page_still_focuses_and_keeps_gap_helpers(self):
        for helper in ("rcRequiredFieldEntries", "rcRequiredFieldErrors", "rcFocusFirstRequiredField"):
            with self.subTest(helper=helper):
                self.assertIn("function %s(" % helper, CREATE)
        self.assertIn("rcFocusFirstRequiredField", self.create_persist,
                      "取消带缺口继续时要定位到首个缺口")

    def test_create_page_sends_waiver_on_submit(self):
        self.assertIn("waiver", CREATE, "1.1 必须把签字交给既有提交接口")
        self.assertIn("/requirement/submit-confirmation", CREATE)
        self.assertRegex(CREATE, r"waiver\s*:", "提交体里要出现 waiver 字段")

    # ------------------------------------------------ 1.2
    def test_confirm_page_asks_before_confirming_with_gaps(self):
        self.assertIn("仍要继续", self.cf_act, "1.2 有缺口时必须先问「仍要继续」")
        self.assertIn("/requirement/confirm", self.cf_act)
        self.assertIn("return-to-draft", self.cf_act)
        self.assertIn("pending_confirmation", self.cf_act, "状态机前置不得丢")
        self.assertIn("cfPublishTaskEvent", self.cf_act)
        self.assertRegex(self.cf_act, r"waiver\s*=\s*\{", "确认前要构造签字体")
        self.assertRegex(self.cf_act, r"JSON\.stringify\([^)]*waiver",
                         "确认请求体必须带上签字")
        self.assertIn("gaps", CONFIRM, "确认页缺口口径必须来自预检的 gaps")

    def test_confirm_page_cancel_stops_before_sending(self):
        self.assertNotIn("missing-comment", self.cf_act)

    # ------------------------------------------------ 1.3
    def test_review_page_asks_before_approving_with_gaps(self):
        self.assertIn("仍要继续", self.rr_submit, "1.3 审核通过前有缺口要先问")
        self.assertIn("/requirement/review", self.rr_submit)
        self.assertIn("pending_review", self.rr_submit, "状态机前置不得丢")
        self.assertRegex(self.rr_submit, r"waiver\s*=\s*\{", "审核通过前要构造签字体")
        self.assertRegex(self.rr_submit, r"JSON\.stringify\([^)]*waiver",
                         "审核请求体必须带上签字")

    def test_review_page_reads_precheck(self):
        self.assertIn("/requirement/precheck", self.rr_start,
                      "审核页要复用既有预检读取缺口，不能自写一套")

    def test_review_reject_does_not_ask(self):
        self.assertRegex(self.rr_submit, r"approve",
                         "缺口提示只针对审核通过，驳回原样放行")


if __name__ == "__main__":
    unittest.main()
