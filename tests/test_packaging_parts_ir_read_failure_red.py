"""红测：零件提取"读不到 CAD 解析结果"被说成"还没有解析结果，先跑一键解析图纸"。

Spec：`docs/specs/packaging-parts-ir-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `packaging_drawing_flow/steps.py:273-282 _previous_ir()` 把 `cad_ir.load_ir()` 的异常
    吞成 `None`（`except Exception: return None`）——"读不到"与"确实没有"同形；
  · `:325-333 parts_extract()` 于是给既有的 `PACKAGING_PARTS_NO_IR`
    （"还没有可用的 CAD 图纸解析结果…（缺前置条件，重试不会成功）" + action "先跑一键解析图纸"），
    而 IR 本来就在、只是这一趟读不到；`retryable=False` 还明确告诉前端"重试没用"。

纪律：假 `cad_ir` / 假 `packaging_parts` 模块 + 打桩 `steps._emit` / `steps.store.load_requirement`；
不起服务、不发 HTTP、不连 PG / SQLite、不建项目、不写任何文件、不跑真解析。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services.packaging_drawing_flow import model   # noqa: E402
# 包 `__init__` 里有一个同名函数 `steps()`，会把子模块属性遮掉 —— 走 importlib 拿真模块。
steps = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.steps")

PID = "testpid00001"
IR_DOC = {"ir_id": "ir-1", "ir_hash": "h1", "ir_version": "cad-ir/1", "stats": {}}


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, old in reversed(self.saved):
            setattr(owner, name, old)
        return False


class _CadIr:
    """假 `cad_ir`：`load_ir()` 要么给文档、要么给 None、要么按 `error` 抛。"""

    def __init__(self, doc=None, error=None):
        self.doc = doc
        self.error = error
        self.calls = []

    def load_ir(self, project_id, ir_id=None):
        self.calls.append((project_id, ir_id))
        if self.error is not None:
            raise self.error
        return self.doc


class _Parts:
    """假 `packaging_parts`：`extract` / `save_parts` 只回一份最小文档。"""

    REQUIREMENT_MATERIAL_FIELDS = ()

    def __init__(self):
        self.extract_calls = []

    def extract(self, ir, semantics=None, options=None):
        self.extract_calls.append({"ir": ir, "semantics": semantics, "options": options})
        return {"parts_id": "parts-1", "parts_hash": "ph1",
                "stats": {"part_total": 4, "filtered_total": 1, "truncated": 0,
                          "by_role": {}},
                "unavailable": []}

    def save_parts(self, project_id, doc):
        return doc


def _resolver(cad_ir, parts):
    def resolve(name):
        return {"cad_ir": cad_ir, "packaging_parts": parts}.get(str(name))
    return resolve


def _ctx(cad_ir, parts, **extra):
    ctx = {"project_id": PID, "run_id": "flow-x", "resolve": _resolver(cad_ir, parts)}
    ctx.update(extra)
    return ctx


def ir_and_problem(raw):
    """兼容两种形状：新形状给 `{"ir": …, "read_problem": …}`，旧形状直接给 IR 本身。"""
    if isinstance(raw, dict) and ("read_problem" in raw or "ir" in raw):
        return raw.get("ir"), raw.get("read_problem")
    return None, None


def _previous_ir(cad_ir):
    return steps._previous_ir({"project_id": PID, "resolve": _resolver(cad_ir, _Parts())})


def _parts_extract(cad_ir, parts=None):
    parts = parts if parts is not None else _Parts()
    emitted = []
    with _Patch((steps, "_emit", lambda *a, **k: emitted.append(a) or {}),
                (steps.store, "load_requirement",
                 lambda project_id: (_ for _ in ()).throw(RuntimeError("no store in test")))):
        result = steps.parts_extract(_ctx(cad_ir, parts))
    return result, parts, emitted


# --------------------------------------------------------------------------- #
# P 组：上一版 IR 的三态（读到 / 确实没有 / 读不到）
# --------------------------------------------------------------------------- #
class PPreviousIr(unittest.TestCase):
    def test_p1_read_failure_is_not_folded_into_none(self):
        raw = _previous_ir(_CadIr(error=RuntimeError("meta channel down")))
        ir, problem = ir_and_problem(raw)
        self.assertIsNone(ir, "读不到时 ir 必须是 None")
        self.assertIsInstance(problem, dict,
                              "读不到必须留下 read_problem（Spec §2.1）")
        self.assertEqual("ir_unavailable", problem.get("code"))
        self.assertEqual("RuntimeError", problem.get("reason"),
                         "异常类名要说出来")
        self.assertIn("meta channel down", str(problem.get("message")),
                      "原文前 200 字要带上")

    def test_p2_read_returns_the_document(self):
        raw = _previous_ir(_CadIr(doc=IR_DOC))
        ir, problem = ir_and_problem(raw)
        self.assertIs(IR_DOC, ir, "读到时原样给那份 IR（Spec §2.1）")
        self.assertIsNone(problem, "读到时没有读问题")

    def test_p3_absent_ir_is_not_a_read_problem(self):
        raw = _previous_ir(_CadIr(doc=None))
        ir, problem = ir_and_problem(raw)
        self.assertIsNone(ir)
        self.assertIsNone(problem,
                          "确实没有 IR 不是读问题（由既有 PACKAGING_PARTS_NO_IR 负责）")


# --------------------------------------------------------------------------- #
# P 组：零件提取这一步的判定
# --------------------------------------------------------------------------- #
class PPartsExtractBranch(unittest.TestCase):
    def test_p4_read_failure_has_its_own_code_and_is_retryable(self):
        result, parts, _ = _parts_extract(_CadIr(error=RuntimeError("meta channel down")))
        self.assertEqual("PACKAGING_PARTS_IR_UNAVAILABLE", result.get("error_code"),
                         "读不到要有自己的码（Spec §2.2）")
        self.assertEqual("blocked", result.get("status"),
                         "零件提不出来不许判死后续步骤（不是 failed / unavailable）")
        self.assertIs(True, result.get("retryable"),
                      "读取故障是可重试的（Spec §2.2）")
        message = str(result.get("error_message") or "")
        self.assertIn("RuntimeError", message, "文案要说得出异常类名")
        self.assertIn("重试", message)
        for forbidden in ("还没有", "缺前置条件", "先跑一键解析"):
            self.assertNotIn(forbidden, message,
                             "读不到不许说成缺前置条件：%s" % forbidden)
        action = str((result.get("detail") or {}).get("action") or "")
        self.assertNotIn("一键解析", action,
                         "重跑一键解析不会有帮助（Spec §2.2）")
        self.assertEqual([], parts.extract_calls,
                         "读不到 IR 就不该去提零件")

    def test_p5_absent_ir_keeps_the_existing_code(self):
        result, _, _ = _parts_extract(_CadIr(doc=None))
        self.assertEqual("PACKAGING_PARTS_NO_IR", result.get("error_code"),
                         "确实没有 IR 仍是既有码（护栏）")
        self.assertEqual("blocked", result.get("status"))
        self.assertIs(False, result.get("retryable"),
                      "缺前置条件仍旧 retryable=False（护栏）")
        action = str((result.get("detail") or {}).get("action") or "")
        self.assertIn("一键解析", action, "确实没有 IR 才该让人去跑一键解析（护栏）")

    def test_p6_with_ir_the_extraction_still_runs(self):
        result, parts, emitted = _parts_extract(_CadIr(doc=dict(IR_DOC)))
        self.assertEqual("completed", result.get("status"), "拿到 IR 照旧提取（护栏）")
        self.assertIsNone(result.get("error_code"))
        detail = result.get("detail") or {}
        self.assertEqual(4, detail.get("parts_total"))
        self.assertEqual(1, detail.get("filtered_total"))
        self.assertEqual("parts-1", detail.get("parts_id"))
        self.assertEqual(1, len(parts.extract_calls), "读到时必须真的去提零件")
        self.assertTrue(emitted, "照旧发一条进度（护栏）")


# --------------------------------------------------------------------------- #
# P 组：码表与既有 blocked 口径
# --------------------------------------------------------------------------- #
class PErrorCodes(unittest.TestCase):
    def test_p7_new_code_is_registered_as_retryable(self):
        self.assertIn("PACKAGING_PARTS_IR_UNAVAILABLE", model.ERROR_CODES,
                      "新码要进 ERROR_CODES（Spec §2.4）")
        self.assertEqual((503, True),
                         model.ERROR_CODES.get("PACKAGING_PARTS_IR_UNAVAILABLE"))
        self.assertEqual((409, False), model.ERROR_CODES.get("PACKAGING_PARTS_NO_IR"),
                         "既有码逐字不变（护栏）")

    def test_p8_blocked_helper_defaults_to_not_retryable(self):
        plain = steps._blocked("X_CODE", "message")
        self.assertEqual("blocked", plain.get("status"))
        self.assertIs(False, plain.get("retryable"),
                      "既有 _blocked() 的 retryable 必须仍是 False（护栏）")
        self.assertNotIn("action", plain.get("detail") or {})
        with_action = steps._blocked("X_CODE", "message", {"dependency": "cad_ir"},
                                    action="go")
        self.assertIs(False, with_action.get("retryable"))
        self.assertEqual("go", (with_action.get("detail") or {}).get("action"))


if __name__ == "__main__":
    unittest.main()
