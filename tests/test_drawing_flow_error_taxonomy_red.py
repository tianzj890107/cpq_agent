"""红测：drawing-flow 错误分类、前置条件、不得判死整条链路。

Spec：`docs/specs/drawing-flow-error-taxonomy.md`

现状缺口（实测，本文件可直接复现）：

  · `packaging_semantics/provenance.py:55` 在缺需求单时抛
    `ValueError("需求单不存在，请先创建需求草稿")`；
  · `packaging_drawing_flow/steps.py:304-309` 用 `except Exception` 把它收敛成
    `REQUIREMENT_SAVE_FAILED`，并用 `getattr(exc, "message", ...)` 取文案 —— `ValueError`
    没有 `.message`，于是对外只剩「需求字段写入失败，请重试」，真因只在日志里。
    实测输出：`status=failed | code=REQUIREMENT_SAVE_FAILED |
    msg=需求字段写入失败，请重试 | retryable=True`；
  · `model.py:28` `STEP_TERMINAL_FAILURES = ("failed", "unavailable")`，
    `__init__.py:431-432` 的 run_flow 遇终态失败即 break —— field_write 失败后
    `pending_confirm` / `downstream_prepare` 永不执行，`flow.status="failed"`；
    而 `retryable=True` 却永远重试不成功（缺的是前置数据）。

纪律：全部离线；用 stub 模块提供 `apply_to_requirement`，不导入 packaging_semantics
（它会连带 import anthropic），不连库、不调模型、不写业务数据。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

steps_mod = importlib.import_module(
    "tech_app.backend.services.packaging_drawing_flow.steps")
flow_pkg = importlib.import_module("tech_app.backend.services.packaging_drawing_flow")
model = importlib.import_module(
    "tech_app.backend.services.packaging_drawing_flow.model")

STEPS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "steps.py"
PROVENANCE_PY = (ROOT / "tech_app" / "backend" / "services" / "packaging_semantics"
                 / "provenance.py")

PROJECT_ID = "nope-no-requirement-draft"


class DraftMissing(Exception):
    """模拟修好之后的专用异常：自带稳定码。"""
    stable_error_code = "REQUIREMENT_DRAFT_MISSING"
    message = "需求单不存在，请先创建需求草稿"


def _ctx(apply_fn):
    class Stub:
        apply_to_requirement = staticmethod(apply_fn)
    return {"project_id": PROJECT_ID, "run_id": "r1", "actor": "tester",
            "resolve": lambda name: Stub,
            "semantics": {"fields": {"inner_length": {"value": 200, "unit": "mm"}}},
            "anchor": {"unit_status": "confirmed", "ir_id": "ir1", "ir_hash": "h1"}}


class AErrorClassification(unittest.TestCase):
    """A 组：码要对得上因，文案要留住因。"""

    def test_a1_provenance_carries_stable_code(self):
        src = PROVENANCE_PY.read_text(encoding="utf-8")
        self.assertTrue("REQUIREMENT_DRAFT_MISSING" in src,
                        "provenance 缺需求单时必须抛出带 REQUIREMENT_DRAFT_MISSING 稳定码的异常")

    def test_a2_draft_missing_is_not_save_failed(self):
        def raises(project_id, semantics, accept=(), author="system"):
            raise DraftMissing()
        out = steps_mod.field_write(_ctx(raises))
        self.assertEqual(out.get("error_code"), "REQUIREMENT_DRAFT_MISSING",
                         "缺需求单必须是 REQUIREMENT_DRAFT_MISSING，不能收敛成写入失败")

    def test_a3_draft_missing_is_blocked_and_not_retryable(self):
        def raises(project_id, semantics, accept=(), author="system"):
            raise DraftMissing()
        out = steps_mod.field_write(_ctx(raises))
        self.assertEqual(out.get("status"), "blocked",
                         "缺前置条件不是执行失败，必须是 blocked（否则整条链路被 break 掉）")
        self.assertFalse(out.get("retryable"),
                         "缺前置条件不可重试：重试同一入口必然再失败")

    def test_a4_message_keeps_real_reason(self):
        def raises(project_id, semantics, accept=(), author="system"):
            raise DraftMissing()
        out = steps_mod.field_write(_ctx(raises))
        message = str(out.get("error_message") or "")
        self.assertIn("需求单", message,
                      "对外文案必须保留真因（现在是通用的『需求字段写入失败，请重试』）")

    def test_a5_no_message_attribute_sniffing(self):
        src = STEPS_PY.read_text(encoding="utf-8")
        self.assertFalse('getattr(exc, "message"' in src,
                         "不得用 getattr(exc, 'message') 取文案：普通异常没有这个属性，真因会被吞")


class BPreconditions(unittest.TestCase):
    """B 组：跑之前就能知道缺什么。"""

    def test_b1_preconditions_api_exists(self):
        self.assertTrue(hasattr(flow_pkg, "preconditions"),
                        "缺少 packaging_drawing_flow.preconditions()：前端无法提前提示缺需求单")

    def test_b2_missing_draft_is_reported_as_blocking(self):
        items = flow_pkg.preconditions(PROJECT_ID)
        self.assertIsInstance(items, list, "preconditions() 必须返回列表")
        codes = {item.get("code") for item in items}
        self.assertIn("REQUIREMENT_DRAFT_MISSING", codes,
                      "无需求单的项目必须被 preconditions() 报出来")
        entry = [item for item in items
                 if item.get("code") == "REQUIREMENT_DRAFT_MISSING"][0]
        self.assertEqual(entry.get("severity"), "blocking")
        self.assertTrue(str(entry.get("action") or ""),
                        "必须给出下一步动作（去哪建需求草稿），不能只报错")


class CFlowMustNotDie(unittest.TestCase):
    """C 组：blocked 不得让整条链路判死。"""

    def test_c1_blocked_is_not_terminal_failure(self):
        self.assertNotIn("blocked", model.STEP_TERMINAL_FAILURES,
                         "blocked 不是终态失败（否则 run_flow 会 break 掉后续步骤）")

    def test_c2_flow_status_with_blocked_is_completed(self):
        flow = {"steps": [{"step_id": step, "status": "completed"}
                          for step in model.STEP_IDS]}
        for row in flow["steps"]:
            if row["step_id"] == "field_write":
                row["status"] = "blocked"
        self.assertEqual(flow_pkg._flow_status(flow), "completed",
                         "只有 blocked、没有 failed 时链路状态必须是 completed")

    def test_c3_flow_status_with_failed_still_failed(self):
        flow = {"steps": [{"step_id": step, "status": "completed"}
                          for step in model.STEP_IDS]}
        for row in flow["steps"]:
            if row["step_id"] == "cad_ir_parse":
                row["status"] = "failed"
        self.assertEqual(flow_pkg._flow_status(flow), "failed",
                         "真失败仍必须判 failed（这条不得被放宽）")


class DRegressionGuards(unittest.TestCase):
    """D 组（绿护栏）：真失败、既有步骤与路由契约不得被动。"""

    def test_d1_real_write_failure_stays_retryable(self):
        def raises(project_id, semantics, accept=(), author="system"):
            raise OSError("disk full")
        out = steps_mod.field_write(_ctx(raises))
        self.assertEqual(out.get("error_code"), "REQUIREMENT_SAVE_FAILED",
                         "真写库/落盘失败仍必须是 REQUIREMENT_SAVE_FAILED")
        self.assertEqual(out.get("status"), "failed")
        self.assertTrue(out.get("retryable"), "真失败仍可重试")

    def test_d2_step_contract_unchanged(self):
        # 2026-09-21：零件提取批按 docs/specs/packaging-dwg-parts-extraction.md §4 在
        # packaging_semantics 之后、field_write 之前插入 parts_extract —— 除新增这一步，
        # 其余步骤的名称与顺序逐字未变。
        self.assertEqual(model.STEP_IDS,
                         ("file_preflight", "dwg_convert", "cad_ir_parse",
                          "packaging_semantics", "parts_extract", "field_write",
                          "pending_confirm", "downstream_prepare"),
                         "步骤闭集不得变动")
        self.assertEqual(set(model.STEP_TITLES), set(model.STEP_IDS),
                         "每步都必须有标题")

    def test_d3_other_steps_error_codes_unchanged(self):
        out = steps_mod.run("file_preflight", {})
        self.assertIn(str(out.get("status")), ("failed", "completed", "blocked", "unavailable"),
                      "未知/空上下文的预检必须返回结构化结果，不得抛裸异常")

    def test_d4_terminal_failures_still_contains_failed(self):
        for status in ("failed", "unavailable"):
            self.assertIn(status, model.STEP_TERMINAL_FAILURES,
                          "%s 仍必须是终态失败" % status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
