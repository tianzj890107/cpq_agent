"""红测：DWG 能力事实（能力矩阵 / 错误码 / 审计）不得写死。

Spec：`docs/specs/dwg-capability-truth-and-audit.md`

现状缺口（实测）：

  · `file_preflight.py:204-225` 能力矩阵逐行写死 `"converter_available": False`
    （注释仍写「第 1 批不装转换器」），与 34 的事实相反；
  · `file_preflight.py:244` `_GATE_ERROR_CODE["dwg"] = "DWG_CONVERTER_NOT_INSTALLED"` 硬编码；
  · `file_preflight.py:46-48` 该码 message 写死「当前环境尚未安装 CAD 转换服务」；
  · 34 实测：ODA 27.1 主转换器已装，真转 `酒盒.dwg` 出 DXF，`fallback_used=false`。

业务后果：用户/客服从 2.1 传 DWG 得到"环境没装转换器"，排查方向指向运维，
真因是入口错（该走 drawing-flow）；`audit_entry()` 的 `converter_available` 也恒 False，
审计跟着说假话，事后无法区分"环境故障"与"入口错误"。

纪律：全部离线；不连 Postgres、不调模型、不起服务、不真转 DWG；禁止改红测转绿。
"""
from __future__ import annotations

import inspect
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import file_preflight as fp  # noqa: E402

MODULE_PATH = ROOT / "tech_app" / "backend" / "services" / "file_preflight.py"

DWG_AVAILABLE = {"available": True, "role": "primary", "version": "27.1",
                 "source": "oda", "checked_at": "2026-09-21T00:00:00"}
DWG_MISSING = {"available": False, "role": "none", "version": "",
               "source": "none", "checked_at": "2026-09-21T00:00:00"}

LEGACY_CODES = ("FILE_EMPTY", "FILE_CORRUPTED", "FILE_TOO_LARGE",
                "FILE_FORMAT_UNSUPPORTED", "FILE_EXTENSION_CONTENT_MISMATCH",
                "DWG_CONVERTER_NOT_INSTALLED", "DWG_NOT_A_3D_MODEL",
                "DWG_CONVERTER_BINARY_UNUSABLE", "DWG_CONVERSION_FAILED")


def detected_dwg():
    return {"detected_format": "dwg", "dwg_version": "AC1027",
            "original_filename": "酒盒.dwg", "extension": "dwg",
            "file_size": 1024, "is_empty": False, "is_truncated": False,
            "extension_content_mismatch": False}


def detected_dxf():
    return {"detected_format": "dxf", "dwg_version": "",
            "original_filename": "平面图.dxf", "extension": "dxf",
            "file_size": 1024, "is_empty": False, "is_truncated": False,
            "extension_content_mismatch": False}


class AConverterTruth(unittest.TestCase):
    """A 组：能力事实必须来自运行时探测，且被错误码/审计共用。"""

    def test_a1_detect_converter_availability_exists(self):
        self.assertTrue(hasattr(fp, "detect_converter_availability"),
                        "缺少 detect_converter_availability()：能力事实没有单一来源")
        fn = getattr(fp, "detect_converter_availability")
        self.assertTrue(callable(fn), "detect_converter_availability 必须可调用")
        sig = inspect.signature(fn)
        self.assertEqual(len(sig.parameters), 0, "探测不得要求调用方传参（单一来源）")

    def test_a2_detect_converter_availability_shape(self):
        result = fp.detect_converter_availability()
        self.assertIsInstance(result, dict, "探测结果必须是 dict")
        for key in ("available", "role", "version", "checked_at"):
            self.assertIn(key, result, "探测结果必须含 %s" % key)
        self.assertIn(result["role"], ("primary", "fallback", "none"),
                      "role 只能是 primary / fallback / none")
        self.assertIsInstance(result["available"], bool, "available 必须是 bool")

    def test_a3_capability_matrix_is_not_hardcoded(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse('"converter_available": False' in src,
                         '能力矩阵不得继续写死 "converter_available": False（34 上转换器已可用）')
        sig = inspect.signature(fp.capabilities_of)
        self.assertIn("converter", sig.parameters,
                      "capabilities_of() 必须接受注入的 converter 结果（否则无法与事实对齐）")

    def test_a4_capability_matrix_reflects_injection(self):
        available = fp.capabilities_of(detected_dwg(), converter=DWG_AVAILABLE)
        self.assertTrue(available["converter_available"],
                        "转换器可用时 capabilities_of() 必须报 converter_available=True")
        missing = fp.capabilities_of(detected_dwg(), converter=DWG_MISSING)
        self.assertFalse(missing["converter_available"],
                         "转换器不可用时必须报 False（同一函数两种输入两种结论）")


class BVisionGateRouting(unittest.TestCase):
    """B 组：DWG 在视觉入口的拒答理由必须与事实一致。"""

    def test_b1_new_stable_code_registered(self):
        self.assertIn("DWG_USE_DRAWING_FLOW", fp.STABLE_ERROR_CODES,
                      "必须新增稳定码 DWG_USE_DRAWING_FLOW（DWG 该走 drawing-flow）")

    def test_b2_dwg_with_converter_points_to_drawing_flow(self):
        error = fp.vision_gate_error(detected_dwg(), converter=DWG_AVAILABLE)
        self.assertIsNotNone(error, "DWG 仍必须被视觉入口拒答（不能真的送模型）")
        self.assertEqual(error.stable_error_code, "DWG_USE_DRAWING_FLOW",
                         "转换器可用时 DWG 的拒答理由必须是『走 drawing-flow』，不是『没装转换器』")
        self.assertFalse(error.retryable, "入口选错不可重试（重试同一入口必然再失败）")
        self.assertNotIn("尚未安装", str(getattr(error, "message", "") or ""),
                         "文案不得再说『尚未安装』")

    def test_b3_dwg_without_converter_keeps_legacy_code(self):
        error = fp.vision_gate_error(detected_dwg(), converter=DWG_MISSING)
        self.assertEqual(error.stable_error_code, "DWG_CONVERTER_NOT_INSTALLED",
                         "探测不到转换器时保持既有码（此时语义为真）")

    def test_b4_legacy_message_no_longer_asserts_absence(self):
        info = fp.STABLE_ERROR_CODES["DWG_CONVERTER_NOT_INSTALLED"]
        message = str(info.get("message") or "")
        self.assertNotIn("尚未安装", message,
                         "默认文案不得断言『尚未安装』（该判定只在探测为真时才成立）")


class CAuditTruth(unittest.TestCase):
    """C 组：审计字段必须与探测结果一致，并带上角色/版本。"""

    def test_c1_audit_reports_converter_role(self):
        record = fp.audit_entry(detected_dwg(), converter=DWG_AVAILABLE)
        self.assertTrue(record["converter_available"],
                        "审计的 converter_available 必须反映探测结果（不得恒 False）")
        for key in ("converter_role", "converter_version"):
            self.assertIn(key, record, "审计必须新增 %s（事后区分环境故障与入口错误）" % key)
        self.assertEqual(record["converter_role"], "primary")
        self.assertEqual(record["converter_version"], "27.1")


class DRegressionGuards(unittest.TestCase):
    """D 组（绿护栏）：既有闭集与其它格式的语义不得被顺手改掉。"""

    def test_d1_legacy_codes_survive(self):
        for code in LEGACY_CODES:
            self.assertIn(code, fp.STABLE_ERROR_CODES, "既有稳定码 %s 不得消失" % code)

    def test_d2_dxf_and_unknown_unchanged(self):
        dxf = fp.vision_gate_error(detected_dxf(), converter=DWG_AVAILABLE)
        self.assertEqual(dxf.stable_error_code, "FILE_FORMAT_UNSUPPORTED",
                         "DXF 的拒答码本批不变（矢量解析不走视觉入口）")
        unknown = fp.vision_gate_error({"detected_format": "unsupported",
                                        "is_empty": False}, converter=DWG_AVAILABLE)
        self.assertEqual(unknown.stable_error_code, "FILE_FORMAT_UNSUPPORTED",
                         "未知格式仍走 FILE_FORMAT_UNSUPPORTED")

    def test_d3_bitmap_still_passes_gate(self):
        png = {"detected_format": "png", "original_filename": "a.png",
               "is_empty": False, "extension_content_mismatch": False}
        self.assertIsNone(fp.vision_gate_error(png, converter=DWG_AVAILABLE),
                          "位图必须仍能通过门禁（视觉路径不变）")

    def test_d4_dwg_is_never_direct_vision(self):
        caps = fp.capabilities_of(detected_dwg(), converter=DWG_AVAILABLE)
        self.assertFalse(caps["direct_vision"],
                         "DWG 永远不能直接送视觉模型（本批不放宽）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
