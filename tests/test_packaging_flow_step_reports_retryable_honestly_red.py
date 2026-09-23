"""红测：三步 catch 把"重试没用"的失败一律报成可重试（界面上写着"（可重试）"）。

Spec：`docs/specs/packaging-flow-step-reports-retryable-honestly.md`

现状缺口（代码级，都可指到行）：
  · `packaging_drawing_flow/steps.py:75-79 _failed()` 的 `retryable` 默认值是 `True`；
  · `dwg_convert()`（`:157-161`）、`cad_ir_parse()`（`:207-210`）、`packaging_semantics()`（`:259-262`）
    三处 catch 都只取了 `stable_error_code`，**没有取异常自带的 `retryable`** ——
    `FileCapabilityError.__init__`（`file_preflight.py:266-272`）明明把它从
    `STABLE_ERROR_CODES` 读进了实例；
  · 于是 `PACKAGING_LAYER_RULES_INVALID`(500, False) / `CAD_IR_ENTITY_LIMIT_EXCEEDED`(413, False) /
    `DWG_CONVERTER_BINARY_UNUSABLE`(500, False) 这三条"重试没用"的码，在终态信号里
    （`app.js:5248`、`quick-quote-panel.js:902-903` 的「（可重试）/（最终失败）」）都被说成可重试。

纪律：假 `cad_converter` / `cad_ir` / `packaging_semantics` 模块 + 真 `FileCapabilityError`；
不起服务、不发 HTTP、不连 PG / SQLite、不写业务数据、不建项目、不跑真 DWG。
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

from tech_app.backend.services.file_preflight import FileCapabilityError   # noqa: E402
steps = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.steps")

STEPS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "steps.py"
PID = "testpid00001"

NOT_RETRYABLE = ("PACKAGING_LAYER_RULES_INVALID", "CAD_IR_ENTITY_LIMIT_EXCEEDED",
                 "DWG_CONVERTER_BINARY_UNUSABLE")
RETRYABLE = "DRAWING_SOURCE_UNAVAILABLE"


class _Converter:
    def __init__(self, error):
        self.error = error

    def convert_drawing(self, project_id, filename, content, drawing_version=1):
        raise self.error


class _CadIr:
    def __init__(self, error):
        self.error = error

    def parse_conversion(self, project_id, drawing_version=1):
        raise self.error


class _Semantics:
    def __init__(self, error):
        self.error = error

    def analyze_conversion(self, project_id, ir=None, **kwargs):
        raise self.error


def _ctx(name, module):
    return {"project_id": PID, "run_id": "flow-x",
            "resolve": (lambda wanted: module if str(wanted) == name else None)}


STEP_NAMES = ("dwg_convert", "cad_ir_parse", "packaging_semantics")


def _run(step_name, error):
    """跑一步，返回它的结果字典（三步都只依赖注入的模块，不碰存储）。"""
    if step_name == "dwg_convert":
        return steps.dwg_convert(_ctx("cad_converter", _Converter(error)))
    if step_name == "cad_ir_parse":
        return steps.cad_ir_parse(_ctx("cad_ir", _CadIr(error)))
    return steps.packaging_semantics(_ctx("packaging_semantics", _Semantics(error)))


def _section(step_name):
    """粗略取出某个 step 函数的源码段（到下一个顶层 `def `/`# ---` 为止）。"""
    source = STEPS_PY.read_text(encoding="utf-8")
    at = source.find("def %s(" % step_name)
    if at < 0:
        return ""
    end = source.find("\ndef ", at + 1)
    return source[at:end if end > 0 else len(source)]


# --------------------------------------------------------------------------- #
# R 组：异常说"不可重试"，结果就必须是不可重试
# --------------------------------------------------------------------------- #
class RNotRetryable(unittest.TestCase):
    def test_r1_semantics_layer_rules_is_not_retryable(self):
        result = _run("packaging_semantics", FileCapabilityError(NOT_RETRYABLE[0]))
        self.assertEqual(NOT_RETRYABLE[0], result.get("error_code"))
        self.assertIs(False, result.get("retryable"),
                      "码表写不可重试，结果就不许说可重试（Spec §1）")

    def test_r2_converter_binary_unusable_is_not_retryable(self):
        result = _run("dwg_convert", FileCapabilityError(NOT_RETRYABLE[2]))
        self.assertEqual(NOT_RETRYABLE[2], result.get("error_code"))
        self.assertIs(False, result.get("retryable"),
                      "「请检查转换器安装与配置」不是重试能解决的（Spec §1）")

    def test_r3_entity_limit_exceeded_is_not_retryable(self):
        result = _run("cad_ir_parse", FileCapabilityError(NOT_RETRYABLE[1]))
        self.assertEqual(NOT_RETRYABLE[1], result.get("error_code"))
        self.assertIs(False, result.get("retryable"),
                      "「请拆分图纸」不是重试能解决的（Spec §1）")


# --------------------------------------------------------------------------- #
# R 组（护栏）：普通异常与可重试的码都不许被改口径
# --------------------------------------------------------------------------- #
class RStillRetryable(unittest.TestCase):
    def test_r4_plain_exception_keeps_the_existing_default(self):
        for step_name in STEP_NAMES:
            result = _run(step_name, RuntimeError("boom"))
            self.assertEqual("failed", result.get("status"), step_name)
            self.assertIs(True, result.get("retryable"),
                          "%s：普通异常仍是既有默认 True（护栏）" % step_name)

    def test_r5_retryable_code_stays_retryable(self):
        for step_name in STEP_NAMES:
            result = _run(step_name, FileCapabilityError(RETRYABLE))
            self.assertEqual(RETRYABLE, result.get("error_code"), step_name)
            self.assertIs(True, result.get("retryable"),
                          "%s：读取故障仍必须说可重试（护栏）" % step_name)


# --------------------------------------------------------------------------- #
# R 组：源码守卫与既有形状
# --------------------------------------------------------------------------- #
class RWiring(unittest.TestCase):
    def test_r6_each_catch_reads_the_exception_retryable(self):
        for step_name in STEP_NAMES:
            section = _section(step_name)
            self.assertTrue(section, "找不到 %s()" % step_name)
            self.assertIn('getattr(exc, "retryable"', section,
                          "%s：catch 必须取异常自带的可重试性（Spec §2）" % step_name)
            self.assertIn("retryable=", section,
                          "%s：取到之后必须交给 _failed()（Spec §2）" % step_name)

    def test_r7_fallback_codes_and_detail_shape_unchanged(self):
        fallbacks = {"dwg_convert": "DWG_CONVERSION_FAILED",
                     "cad_ir_parse": "CAD_IR_SOURCE_MISSING",
                     "packaging_semantics": "PACKAGING_SEMANTICS_FAILED"}
        for step_name, code in fallbacks.items():
            section = _section(step_name)
            self.assertIn('getattr(exc, "stable_error_code", "") or "%s"' % code, section,
                          "%s：兜底码取法逐字不变（护栏）" % step_name)
            self.assertIn('{"reason": type(exc).__name__}', section,
                          "%s：detail 形状逐字不变（护栏）" % step_name)

    def test_r8_failed_helper_defaults_unchanged(self):
        plain = steps._failed("X_CODE", "message")
        self.assertEqual("failed", plain.get("status"))
        self.assertIs(True, plain.get("retryable"),
                      "_failed() 默认仍是 True（不许顺手改成一律 False）")
        forced = steps._failed("X_CODE", "message", {"reason": "RuntimeError"}, False)
        self.assertIs(False, forced.get("retryable"))
        self.assertEqual("RuntimeError", (forced.get("detail") or {}).get("reason"))


if __name__ == "__main__":
    unittest.main()
