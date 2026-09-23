"""红测：转换"跑完了但结果不合格"的 retryable 也不许说反（非异常失败路径绕过了码表）。

Spec：`docs/specs/packaging-flow-non-exception-retryable.md`

现状缺口（代码级，都可指到行）：
  · `packaging_drawing_flow/steps.py:173-174`（manifest 不成功）与 `:177-179`（质量门槛未过）
    两条**非异常**失败路径都调 `_failed(code, message, detail)`，吃 `_failed()` 的默认
    `retryable=True`（`:75-79`）—— manifest 里的 `error_code` 可能是
    `DWG_CONVERTER_UNSAFE_PATH`(500, False) / `FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION`(500, False)
    / `DWG_CONVERTER_BINARY_UNUSABLE`(500, False)；
  · `model.py:287-288 error_meta()` 只认流层 `ERROR_CODES`，权威闭集
    `file_preflight.STABLE_ERROR_CODES` 一个都不认（`DWG_CONVERSION_TIMEOUT`(504, True) 会被
    兜成默认的 `(500, False)`，把可重试的说成最终失败）。

纪律：假 `cad_converter` 模块 + 直接调 `model.error_meta()`；不起服务、不发 HTTP、
不连 PG / SQLite、不写业务数据、不建项目、不跑真 DWG。
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
steps = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.steps")

STEPS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "steps.py"
PID = "testpid00001"
DETAIL_KEYS = ("conversion_id", "status", "quality", "warning_count", "error_count",
               "converter_name", "converter_version", "drawing_version")


class _Converter:
    """假 `cad_converter`：`convert_drawing()` 直接回一份 manifest（不抛异常）。"""

    def __init__(self, manifest):
        self.manifest = manifest

    def convert_drawing(self, project_id, filename, content, drawing_version=1):
        return self.manifest


def _convert(manifest):
    ctx = {"project_id": PID, "run_id": "flow-x", "filename": "酒盒.dwg",
           "resolve": lambda name: _Converter(manifest) if name == "cad_converter" else None}
    return steps.dwg_convert(ctx)


def _section(step_name):
    source = STEPS_PY.read_text(encoding="utf-8")
    at = source.find("def %s(" % step_name)
    if at < 0:
        return ""
    end = source.find("\ndef ", at + 1)
    return source[at:end if end > 0 else len(source)]


# --------------------------------------------------------------------------- #
# S 组：取值口要认全权威闭集
# --------------------------------------------------------------------------- #
class SErrorMeta(unittest.TestCase):
    def test_s1_authoritative_table_is_consulted(self):
        self.assertEqual((504, True), model.error_meta("DWG_CONVERSION_TIMEOUT"),
                         "权威闭集里的码不许被兜成默认 (500, False)（Spec §2.1）")
        self.assertEqual((500, False), model.error_meta("DWG_CONVERTER_UNSAFE_PATH"))
        self.assertEqual((500, False),
                         model.error_meta("FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION"))

    def test_s2_flow_table_still_wins_and_default_unchanged(self):
        for code, expected in (("REQUIREMENT_NOT_EDITABLE", (409, False)),
                               ("DRAWING_SOURCE_UNAVAILABLE", (503, True)),
                               ("PACKAGING_PARTS_NO_IR", (409, False)),
                               ("PACKAGING_FLOW_DEPENDENCY_MISSING", (500, False)),
                               ("NO_SUCH_CODE_ANYWHERE", (500, False))):
            self.assertEqual(expected, model.error_meta(code),
                             "流层表优先 / 未知码仍是 (500, False)（护栏）：%s" % code)


# --------------------------------------------------------------------------- #
# S 组：两条非异常路径要按码报 retryable
# --------------------------------------------------------------------------- #
class SConvertManifest(unittest.TestCase):
    def test_s3_unretryable_code_is_reported_unretryable(self):
        result = _convert({"status": "failed", "error_code": "DWG_CONVERTER_UNSAFE_PATH",
                           "conversion_id": "c1"})
        self.assertEqual("DWG_CONVERTER_UNSAFE_PATH", result.get("error_code"))
        self.assertIs(False, result.get("retryable"),
                      "「转换器返回了不安全的输出路径」不是重试能解决的（Spec §1）")

    def test_s4_retryable_code_stays_retryable(self):
        result = _convert({"status": "failed", "error_code": "DWG_CONVERSION_TIMEOUT"})
        self.assertEqual("DWG_CONVERSION_TIMEOUT", result.get("error_code"))
        self.assertIs(True, result.get("retryable"),
                      "「转换超时，请稍后重试」必须仍可说可重试（护栏）")

    def test_s5_missing_error_code_falls_back_unchanged(self):
        result = _convert({"status": "failed"})
        self.assertEqual("DWG_CONVERSION_FAILED", result.get("error_code"),
                         "不给 error_code 仍回落既有码（护栏）")
        self.assertIs(True, result.get("retryable"),
                      "DWG_CONVERSION_FAILED 在表里是可重试（502, True）（护栏）")

    def test_s6_quality_gate_uses_the_table_value(self):
        result = _convert({"status": "ok", "quality": {"verified": False},
                           "conversion_id": "c2"})
        self.assertEqual("DWG_CONVERTER_OUTPUT_INVALID", result.get("error_code"),
                         "质量门槛未过仍是既有码（护栏）")
        self.assertIs(True, result.get("retryable"),
                      "该码在表里是 (502, True)（护栏）")


# --------------------------------------------------------------------------- #
# S 组：源码守卫与既有形状
# --------------------------------------------------------------------------- #
class SWiring(unittest.TestCase):
    def test_s7_both_non_exception_failures_pass_retryable(self):
        section = _section("dwg_convert")
        self.assertTrue(section, "找不到 dwg_convert()")
        anchors = ('if status not in ("ok", "success_with_warnings"):',
                   'if quality and not quality.get("verified"):')
        for anchor in anchors:
            at = section.find(anchor)
            self.assertGreaterEqual(at, 0, "既有判据不许被删：%s" % anchor)
            seg = section[at:at + 500]
            self.assertIn("_failed(", seg, "这一条仍是失败分支：%s" % anchor)
            self.assertIn("retryable=", seg,
                          "非异常失败也要按码表报 retryable（Spec §2.2）：%s" % anchor)

    def test_s8_code_taking_and_detail_keys_unchanged(self):
        section = _section("dwg_convert")
        self.assertIn('str(manifest.get("error_code") or "DWG_CONVERSION_FAILED")', section,
                      "code 取法逐字不变（护栏）")
        for key in DETAIL_KEYS:
            self.assertIn('"%s":' % key, section,
                          "detail 键逐字不变（护栏）：%s" % key)


if __name__ == "__main__":
    unittest.main()
