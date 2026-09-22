"""红测：语义识别"读不到 CAD 解析结果"被说成"项目里没有解析结果，请先重跑图纸解析"。

Spec：`docs/specs/packaging-semantics-ir-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `packaging_semantics/__init__.py:276-279 analyze_conversion()` 里
    `cad_ir.load_ir(...)` 的异常**原样往上抛** —— 越过 `:280-286`，`audit` 一条不写，
    上游 `steps.packaging_semantics()` 只能兜成通用的 `PACKAGING_SEMANTICS_FAILED`
    （"这一步坏了"），而不是"读通道抖了一下"；
  · `:280 if not isinstance(ir, dict):` 把"确实没有"与"读到了坏形状"一起收进
    `PACKAGING_SEMANTICS_SOURCE_MISSING`（"项目里没有可用的 CAD 图纸解析结果，
    请先重跑图纸解析" + `audit.reason="source_missing"`）。

纪律：`mock.patch.object(cad_ir, "load_ir", …)` + 打桩 `persistence.audit` /
`save_semantics` / `list_semantics`；不起服务、不发 HTTP、不连 PG / SQLite、不建项目、不写任何文件。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEMANTICS_PKG = "tech_app.backend.services.packaging_semantics"
CAD_IR_PKG = "tech_app.backend.services.cad_ir"
PREFLIGHT = "tech_app.backend.services.file_preflight"

PID = "testpid00001"
NEW_CODE = "PACKAGING_SEMANTICS_SOURCE_UNREADABLE"
OLD_CODE = "PACKAGING_SEMANTICS_SOURCE_MISSING"
OLD_MESSAGE = "项目里没有可用的 CAD 图纸解析结果，请先重跑图纸解析"


def _package():
    return importlib.import_module(SEMANTICS_PKG)


def _cad_ir():
    return importlib.import_module(CAD_IR_PKG)


def _persistence():
    return importlib.import_module(SEMANTICS_PKG + ".persistence")


def _preflight():
    return importlib.import_module(PREFLIGHT)


class _Stubs:
    """打桩审计与落库：本批红测不许碰任何存储。"""

    def __init__(self):
        self.audits = []
        self.saved = []

    def __enter__(self):
        persistence = _persistence()
        self._patches = [
            mock.patch.object(persistence, "audit",
                              lambda *a, **k: self.audits.append((a, k))),
            mock.patch.object(persistence, "save_semantics",
                              lambda project_id, doc: self.saved.append(doc) or doc),
            mock.patch.object(persistence, "list_semantics", lambda project_id: []),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()
        return False


def _run(load_ir):
    """跑一次 `analyze_conversion(project_id)`（不给 ir），只打桩存储与 load_ir。"""
    package = _package()
    cad_ir = _cad_ir()
    with _Stubs() as stubs:
        with mock.patch.object(cad_ir, "load_ir", load_ir):
            try:
                package.analyze_conversion(PID)
            except BaseException as exc:            # noqa: BLE001 - 红测只看这一条出口
                return exc, stubs
    return None, stubs


# --------------------------------------------------------------------------- #
# Q 组：读不到 CAD IR 必须有自己的码、自己的文案、自己的审计
# --------------------------------------------------------------------------- #
class QIrReadFailure(unittest.TestCase):
    def test_q1_read_failure_has_its_own_code(self):
        def boom(*args, **kwargs):
            raise RuntimeError("meta channel down")

        exc, _ = _run(boom)
        self.assertIsNotNone(exc, "读不到 CAD IR 时不许静默继续（Spec §2.2）")
        self.assertEqual(NEW_CODE, getattr(exc, "stable_error_code", None),
                         "读不到要有自己的码（Spec §2.2）")
        self.assertEqual(503, getattr(exc, "http_status", None))
        self.assertIs(True, getattr(exc, "retryable", None),
                      "读通道故障是可重试的")
        message = str(getattr(exc, "message", "") or exc)
        self.assertIn("RuntimeError", message, "文案要说得出异常类名")
        for forbidden in ("没有可用的 CAD", "重跑图纸解析"):
            self.assertNotIn(forbidden, message,
                             "读不到不许说成'没有解析结果'：%s" % forbidden)

    def test_q2_read_failure_is_audited(self):
        def boom(*args, **kwargs):
            raise RuntimeError("meta channel down")

        _, stubs = _run(boom)
        self.assertTrue(stubs.audits, "读失败必须留下审计（Spec §2.2）")
        payload = dict((stubs.audits[0][0][2] if len(stubs.audits[0][0]) > 2 else {}) or {})
        self.assertEqual("source_unreadable", payload.get("reason"),
                         "审计原因要与'确实没有'分开")
        self.assertEqual("testpid00001", stubs.audits[0][0][0])

    def test_q8_file_capability_error_reads_retryable_from_the_table(self):
        exc, _ = _run(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertIs(True, getattr(exc, "retryable", None),
                      "未注册的码会被兜成 500 + 不可重试（Spec §2.1）")
        detected = getattr(exc, "detected", {}) or {}
        self.assertEqual("RuntimeError", detected.get("reason"),
                         "detected 要带异常类名（排障）")


# --------------------------------------------------------------------------- #
# Q 组：确实没有 CAD IR 的两条既有路径一个字不改
# --------------------------------------------------------------------------- #
class QExistingMissingPath(unittest.TestCase):
    def test_q3_none_is_still_source_missing(self):
        exc, stubs = _run(lambda *a, **k: None)
        self.assertEqual(OLD_CODE, getattr(exc, "stable_error_code", None),
                         "确实没有仍是既有码（护栏）")
        self.assertEqual(422, getattr(exc, "http_status", None))
        self.assertEqual(OLD_MESSAGE, getattr(exc, "message", None),
                         "既有文案逐字不变（护栏）")
        payload = (stubs.audits[0][0][2] if stubs.audits and len(stubs.audits[0][0]) > 2
                   else {})
        self.assertEqual("source_missing", (payload or {}).get("reason"))

    def test_q4_wrong_shape_is_still_source_missing(self):
        exc, _ = _run(lambda *a, **k: ["not", "a", "dict"])
        self.assertEqual(OLD_CODE, getattr(exc, "stable_error_code", None),
                         "坏形状仍归既有码（护栏）")

    def test_q7_explicit_ir_never_touches_cad_ir(self):
        package = _package()
        calls = []
        stub_doc = {"semantics_id": "sem-1", "source": {}}
        with _Stubs():
            with mock.patch.object(_cad_ir(), "load_ir",
                                   lambda *a, **k: calls.append(a) or None):
                with mock.patch.object(package, "analyze",
                                       lambda ir, **kwargs: dict(stub_doc)):
                    doc = package.analyze_conversion(PID, ir={"ir_id": "ir-1"})
        self.assertEqual([], calls, "给了 ir 就不许再去读项目里的 IR（护栏）")
        self.assertEqual("sem-1", (doc or {}).get("semantics_id"))


# --------------------------------------------------------------------------- #
# Q 组：权威码表与源码守卫
# --------------------------------------------------------------------------- #
class QErrorCodeTable(unittest.TestCase):
    def test_q5_new_code_joins_the_authoritative_table(self):
        table = getattr(_preflight(), "STABLE_ERROR_CODES", None)
        self.assertIsInstance(table, dict, "必须导出 STABLE_ERROR_CODES")
        self.assertIn(NEW_CODE, table, "新码必须并入权威闭集（Spec §2.1）")
        spec = table[NEW_CODE]
        self.assertEqual(503, spec.get("http_status"), NEW_CODE)
        self.assertIs(True, spec.get("retryable"), NEW_CODE)
        self.assertTrue(spec.get("message"), "%s 必须有中文文案" % NEW_CODE)
        for code, status in ((OLD_CODE, 422), ("PACKAGING_LAYER_RULES_INVALID", 500)):
            self.assertEqual(status, (table.get(code) or {}).get("http_status"),
                             "既有语义码逐字不变：%s" % code)
        self.assertEqual(OLD_MESSAGE, (table.get(OLD_CODE) or {}).get("message"))

    def test_q6_analyze_conversion_wraps_the_read(self):
        source = (ROOT / "tech_app" / "backend" / "services" / "packaging_semantics"
                  / "__init__.py").read_text(encoding="utf-8")
        at = source.find("def analyze_conversion(")
        self.assertGreaterEqual(at, 0, "找不到 analyze_conversion()")
        body = source[at:at + 2000]
        self.assertIn(NEW_CODE, body, "读失败要用新码（Spec §2.2）")
        at_try = body.find("try:")
        at_load = body.find("cad_ir.load_ir(")
        self.assertGreaterEqual(at_load, 0, "既有 load_ir 调用不许被删")
        self.assertGreaterEqual(at_try, 0, "load_ir 必须被 try/except 包住（Spec §2.2）")
        self.assertLess(at_try, at_load)
        self.assertIn("source_unreadable", body, "审计原因要落 source_unreadable")
        self.assertIn(OLD_CODE, body, "既有 SOURCE_MISSING 分支不许被删")


if __name__ == "__main__":
    unittest.main()
