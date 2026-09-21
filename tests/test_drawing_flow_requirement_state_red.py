"""红测：需求不可编辑（已提交）时的前置条件与稳定码 —— drawing-flow 第 5 批补充。

Spec：`docs/specs/drawing-flow-non-editable-requirement.md`
（前置：`docs/specs/drawing-flow-error-taxonomy.md`）

现状缺口（实测，9-21，34 上 `bf99bec0d274` / `ce9d5aae9631` 两条 requirement 都是 `approved`）：

  · `requirement_service.save_requirement_draft` 抛 `RequirementSaveError("需求已提交，不能直接修改…", 409)`，
    该异常**没有** `stable_error_code`；
  · `steps.field_write` 因此把它归成 `PACKAGING_FLOW_STEP_FAILED`（"其它未识别异常"）+ `retryable=True`
    —— 文案通用、重试永远不会成功；
  · `packaging_drawing_flow.preconditions(project_id)` 返回 `[]`，跑之前完全看不出缺什么；
  · 结果：2.1 字段看板空白、报价拿不到候选，用户只看到一句「重试」。

本批只补「分类 + 可预见性」，**不放宽**「已提交的需求不可被静默改写」。

纪律：全部离线（不连库、不调模型、不写业务数据）；
需要"需求已提交"的场景一律用 `mock.patch.object(store, ...)` 注入，
`save_requirement` 被调用即视为失败（证明守卫没有放行写库）。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

flow_pkg = importlib.import_module("tech_app.backend.services.packaging_drawing_flow")
steps_mod = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.steps")
model = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.model")
requirements = importlib.import_module("tech_app.backend.services.requirement_service")
workflow_models = importlib.import_module("tech_app.backend.models.workflow")

REQUIREMENT_SERVICE_PY = (ROOT / "tech_app" / "backend" / "services" / "requirement_service.py")
STEPS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "steps.py"

NOT_EDITABLE = "REQUIREMENT_NOT_EDITABLE"
SAVE_REJECTED = "REQUIREMENT_SAVE_REJECTED"
DRAFT_MISSING = "REQUIREMENT_DRAFT_MISSING"
STEP_FAILED = "PACKAGING_FLOW_STEP_FAILED"

#: 契约 C4：可编辑状态下限不许被放宽。
EDITABLE_STATUSES = ("draft", "rejected")
#: 需求已进入流程、不可再被图纸解析改写的状态（Spec §0）。
NOT_EDITABLE_STATUSES = ("pending_confirmation", "pending_review", "approved")

PROJECT_ID = "flow-non-editable-probe"
FIELDS = {"inner_length": {"value": 200, "unit": "mm"},
          "inner_width": {"value": 120, "unit": "mm"}}


class NotEditable(Exception):
    """修好之后 `save_requirement_draft` 该抛的形状：自带稳定码。"""

    stable_error_code = NOT_EDITABLE
    message = "需求已提交，不能直接修改；请先退回后再编辑"


class SaveRejected(Exception):
    stable_error_code = SAVE_REJECTED
    message = "客户信用等级只能为 A、B、C 或 D"


class UnknownBoom(Exception):
    """没有稳定码的未识别异常：现状口径必须保留（可重试）。"""


def requirement_doc(status="draft", *, data=None, credit=None):
    payload = dict(data or {})
    if credit is not None:
        payload["customer_credit"] = credit
    return workflow_models.RequirementDoc(project_id=PROJECT_ID, requirement_no="REQ-1",
                                          status=status, data=payload)


def requirement_row(status):
    return {"project_id": PROJECT_ID, "requirement_no": "REQ-1", "status": status, "data": {}}


def _ctx(apply_fn):
    class Stub:
        apply_to_requirement = staticmethod(apply_fn)

    return {"project_id": PROJECT_ID, "run_id": "r1", "actor": "tester",
            "resolve": lambda name: Stub,
            "semantics": {"fields": dict(FIELDS)},
            "anchor": {"unit_status": "confirmed", "ir_id": "ir1", "ir_hash": "h1"}}


def raising(exc):
    def apply_fn(project_id, semantics, accept=(), author="system"):
        raise exc
    return apply_fn


class AStableCodes(unittest.TestCase):
    """A 组：业务拒绝必须自带稳定码，且码要对得上因。"""

    def test_a1_approved_requirement_rejection_carries_its_own_code(self):
        with mock.patch.object(requirements.store, "save_requirement",
                               side_effect=AssertionError("需求已提交时绝不许写库")):
            with self.assertRaises(Exception) as caught:
                requirements.save_requirement_draft(
                    PROJECT_ID, requirement_doc("draft"), user={"username": "t", "role": "admin"},
                    current={"status": "approved", "data": {}})
        exc = caught.exception
        self.assertEqual(str(getattr(exc, "stable_error_code", "") or ""), NOT_EDITABLE,
                         "需求不可编辑必须自带 %s（Spec §1）" % NOT_EDITABLE)
        self.assertEqual(int(getattr(exc, "status_code", 0) or 0), 409)
        self.assertIn("提交", str(exc), "文案必须留住真因（Spec §1.3）")

    def test_a2_other_business_rejections_carry_a_default_code(self):
        with mock.patch.object(requirements.store, "load_requirement", return_value=None):
            with self.assertRaises(Exception) as caught:
                requirements.save_requirement_draft(
                    PROJECT_ID, requirement_doc("draft", credit="X"),
                    user={"username": "t", "role": "admin"})
        code = str(getattr(caught.exception, "stable_error_code", "") or "")
        self.assertEqual(code, SAVE_REJECTED,
                         "其它业务拒绝必须有默认稳定码 %s，不许留空（Spec §1.2）" % SAVE_REJECTED)

    def test_a3_every_raise_declares_a_code(self):
        tree = ast.parse(REQUIREMENT_SERVICE_PY.read_text(encoding="utf-8"))
        missing = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            func = node.exc.func
            name = getattr(func, "id", "") or getattr(func, "attr", "")
            if name != "RequirementSaveError":
                continue
            if not any(keyword.arg == "code" for keyword in node.exc.keywords):
                missing.append(node.lineno)
        self.assertEqual(missing, [],
                         "`raise RequirementSaveError(...)` 必须逐处显式给码（Spec §1.2），第 %s 行没给"
                         % missing)

    def test_a4_field_write_blocks_on_non_editable_requirement(self):
        out = steps_mod.field_write(_ctx(raising(NotEditable())))
        self.assertEqual(out.get("error_code"), NOT_EDITABLE)
        self.assertEqual(out.get("status"), "blocked",
                         "不可编辑是前置条件，不是执行失败（Spec §3.2）")
        self.assertFalse(out.get("retryable"), "重试同一入口必然再失败（Spec §3.2）")
        self.assertIn("提交", str(out.get("error_message") or ""), "文案必须留住真因（Spec §3.2）")
        self.assertTrue(str((out.get("detail") or {}).get("action") or ""),
                        "必须给出下一步动作（退回/新建草稿），不能只报错（Spec §3.2）")
        self.assertTrue(out.get("fields"), "前置条件缺失不等于这次识别没结果，看板不许被清空（Spec §3.2）")

    def test_a5_business_rejection_is_not_an_unknown_failure(self):
        out = steps_mod.field_write(_ctx(raising(SaveRejected())))
        self.assertEqual(out.get("error_code"), SAVE_REJECTED,
                         "带稳定码的业务拒绝不许落 %s（Spec §3.3）" % STEP_FAILED)
        self.assertFalse(out.get("retryable"), "业务拒绝不可重试（Spec §3.3）")

    def test_a6_no_message_sniffing_decides_the_code(self):
        src = STEPS_PY.read_text(encoding="utf-8")
        self.assertNotIn("in str(exc)", src,
                         "不得用 str(exc) 关键字匹配决定码（Spec §1.3）")

    def test_a7_unknown_errors_stay_retryable(self):
        out = steps_mod.field_write(_ctx(raising(UnknownBoom("boom"))))
        self.assertEqual(out.get("error_code"), STEP_FAILED,
                         "没有稳定码的未识别异常保持既有口径（Spec §3.4）")
        self.assertTrue(out.get("retryable"), "偶发故障必须仍可重试（Spec §3.4）")


class BPreconditions(unittest.TestCase):
    """B 组：跑链路之前就要能看见「需求已提交」。"""

    def items(self, requirement):
        with mock.patch.object(flow_pkg.store, "load_requirement", return_value=requirement):
            return flow_pkg.preconditions(PROJECT_ID)

    def codes(self, requirement):
        return {item.get("code") for item in self.items(requirement)}

    def test_b1_approved_requirement_is_reported_as_blocking(self):
        items = self.items(requirement_row("approved"))
        rows = [item for item in items if item.get("code") == NOT_EDITABLE]
        self.assertTrue(rows, "已提交的需求必须在 preconditions() 里被报出来（Spec §2）")
        entry = rows[0]
        self.assertEqual(entry.get("severity"), "blocking")
        self.assertIn("approved", str(entry.get("message") or ""),
                      "message 必须带上当前状态，否则用户不知道卡在哪（Spec §2）")
        self.assertTrue(str(entry.get("action") or ""), "必须给出下一步动作（Spec §2）")

    def test_b2_every_non_editable_status_is_reported(self):
        for status in NOT_EDITABLE_STATUSES:
            self.assertIn(NOT_EDITABLE, self.codes(requirement_row(status)),
                          "status=%s 也是不可编辑状态（Spec §2）" % status)

    def test_b3_editable_statuses_report_nothing(self):
        for status in EDITABLE_STATUSES:
            self.assertEqual(self.items(requirement_row(status)), [],
                             "status=%s 可编辑，不该有前置条件缺口（Spec §2）" % status)

    def test_b4_missing_draft_still_reported_alone(self):
        self.assertEqual(self.codes(None), {DRAFT_MISSING},
                         "没有需求单时仍只报 %s（回归锚点，Spec §2）" % DRAFT_MISSING)

    def test_b5_preconditions_are_read_only(self):
        with mock.patch.object(flow_pkg.store, "load_requirement",
                               return_value=requirement_row("approved")) as loader:
            with mock.patch.object(flow_pkg.store, "save_requirement",
                                   side_effect=AssertionError("preconditions() 不许写库")):
                first = flow_pkg.preconditions(PROJECT_ID)
                second = flow_pkg.preconditions(PROJECT_ID)
        self.assertEqual(first, second, "preconditions() 必须幂等（Spec §2）")
        self.assertGreaterEqual(loader.call_count, 2)


class CRegistry(unittest.TestCase):
    """C 组：码要登记，blocked 不是终态。"""

    def test_c1_codes_are_registered(self):
        self.assertIn(NOT_EDITABLE, model.PRECONDITION_BLOCKERS,
                      "必须登记进 PRECONDITION_BLOCKERS（Spec §3.1）")
        spec = model.PRECONDITION_BLOCKERS[NOT_EDITABLE]
        self.assertTrue(str(spec.get("action") or ""), "登记项必须带 action（Spec §3.1）")
        self.assertEqual(model.error_meta(NOT_EDITABLE), (409, False))
        self.assertEqual(model.error_meta(SAVE_REJECTED), (409, False))

    def test_c2_blocked_is_not_a_terminal_failure(self):
        self.assertNotIn("blocked", model.STEP_TERMINAL_FAILURES,
                         "blocked 不许被当成终态失败（Spec §5）")


class DNoBypass(unittest.TestCase):
    """D 组：不许为了让链路跑通而放宽或绕过。"""

    def test_d1_editable_statuses_are_unchanged(self):
        self.assertEqual(tuple(requirements.EDITABLE_STATUSES), EDITABLE_STATUSES,
                         "不许把 approved 放进可编辑集合（Spec §4）")

    def test_d2_steps_never_writes_the_requirement_directly(self):
        src = STEPS_PY.read_text(encoding="utf-8")
        self.assertNotIn("store.save_requirement(", src,
                         "图纸解析不许绕过 save_requirement_draft 直接写需求（Spec §4）")

    def test_d3_the_guard_message_is_still_there(self):
        src = REQUIREMENT_SERVICE_PY.read_text(encoding="utf-8")
        self.assertIn("不能直接修改", src, "「已提交不可静默改写」的守卫不许被删（Spec §4）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
