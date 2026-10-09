"""红测：可用性与服务边界（批次 5）—— 能力隔离 + 统一解析服务。

Spec：docs/specs/capability-isolation-and-shared-parse-batch5.md

现状（2026-10-09 实测）：

  · T8 运行侧**已是能力隔离**（`cad_converter.capability()` 探测不抛、`unified_parse.capability()`
    绝不许 500、DWG 缺转换器是单文件 415 业务错误）—— 本批只验收，不重做；
  · T8 缺的是：`capability_isolation.py`（把能力翻成「哪些可用 / 哪些被禁用 / 为什么」）
    与「发布 / 运行」分开的判据；`scripts/create_release.py` 不消费生产门禁报告；
  · T17 **已是共用同一服务**（`unified_parse.SERVICE_PATH` 是唯一事实源，报价侧不 import
    `ezdxf`/ODA）—— 本批加守卫钉住地址与「不自己解析 DWG」。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CREATE_RELEASE = ROOT / "scripts" / "create_release.py"
QUICK_FILE = ROOT / "cpq_quick_quote_file.py"


def _module(dotted: str):
    try:
        return importlib.import_module(dotted)
    except Exception as exc:                                   # noqa: BLE001 - 交回用例判定
        return exc


def _fn(self, mod, name):
    fn = getattr(mod, name, None)
    if not callable(fn):
        self.fail("缺 `%s`（Spec §2）：模块 %r 里没有这个可调用" % (name, mod.__name__))
    return fn


def _iso(self):
    mod = _module("tech_app.backend.services.capability_isolation")
    if isinstance(mod, Exception):
        self.fail("缺 `tech_app/backend/services/capability_isolation.py`（Spec §2.1）：%r" % mod)
    return mod


class T8IsolationView(unittest.TestCase):
    """T8 运行侧：转换器禁用不牵连文档解析。"""

    def test_t8_a_unavailable_disables_only_dwg(self):
        fn = _fn(self, _iso(self), "isolation_view")
        out = fn({"available": False, "stable_error_code": "DWG_CONVERTER_NOT_INSTALLED",
                  "preview_available": False, "provider": "", "converter_version": ""})
        self.assertEqual("disabled", out["dwg_parse"])
        self.assertEqual("enabled", out["documents"], "文字/Excel/PDF/图片不受转换器影响")
        self.assertIn("DWG_CONVERTER_NOT_INSTALLED", out["disabled_reason"])
        self.assertTrue(str(out["warning"]).strip(), "禁用必须明确告警")

    def test_t8_b_unified_shape_available_is_enabled(self):
        fn = _fn(self, _iso(self), "isolation_view")
        out = fn({"dwg": True, "dxf": True, "preview": True, "provider": "oda",
                  "provider_version": "27.1"})
        self.assertEqual("enabled", out["dwg_parse"])
        self.assertEqual("enabled", out["documents"])
        self.assertEqual("", out["disabled_reason"])
        self.assertEqual("", out["warning"])

    def test_t8_c_bad_input_is_disabled_not_raised(self):
        fn = _fn(self, _iso(self), "isolation_view")
        for bad in (None, "boom", {}, {"available": None}):
            out = fn(bad)
            self.assertEqual("disabled", out["dwg_parse"], "脏入参按禁用处理：%r" % (bad,))
            self.assertEqual("enabled", out["documents"])
            self.assertTrue(str(out["disabled_reason"]).strip())


class T8ReleaseVerdict(unittest.TestCase):
    """T8 发布侧：门禁失败只阻止发布，不阻止运行。"""

    def _report(self, items, *, env="production", verdict="no_go"):
        return {"env": env, "verdict": verdict, "items": items}

    def test_t8_d_fail_blocks_release(self):
        fn = _fn(self, _iso(self), "release_verdict")
        out = fn(self._report([
            {"id": "converter_license", "kind": "manual", "status": "acknowledged"},
            {"id": "converter_version_pinned", "kind": "auto", "status": "fail"}]))
        self.assertFalse(out["release_ok"])
        self.assertIn("converter_version_pinned", out["blocking"])
        self.assertEqual([], out["manual"])

    def test_t8_e_all_ok_releases(self):
        fn = _fn(self, _iso(self), "release_verdict")
        out = fn(self._report([
            {"id": "converter_version_pinned", "kind": "auto", "status": "ok"},
            {"id": "converter_license", "kind": "manual", "status": "acknowledged"}],
            verdict="go"))
        self.assertTrue(out["release_ok"])
        self.assertEqual([], out["blocking"])

    def test_t8_f_production_skip_and_manual_are_blocking(self):
        fn = _fn(self, _iso(self), "release_verdict")
        out = fn(self._report([
            {"id": "some_skip", "kind": "auto", "status": "skip"},
            {"id": "converter_license", "kind": "manual", "status": "manual_unacknowledged"}]))
        self.assertFalse(out["release_ok"])
        self.assertIn("some_skip", out["blocking"], "生产环境不允许 skip")
        self.assertIn("converter_license", out["manual"])
        self.assertIn("converter_license", out["blocking"])

    def test_t8_g_invalid_report_is_no_go(self):
        fn = _fn(self, _iso(self), "release_verdict")
        for bad in (None, "boom", {}, {"env": "production"}):
            out = fn(bad)
            self.assertFalse(out["release_ok"], "缺报告的发布一律不放行：%r" % (bad,))
            self.assertEqual("no_go", out["verdict"])
            self.assertEqual(["gate_report_invalid"], out["blocking"])


class T8ReleaseTooling(unittest.TestCase):
    """T8 发布工具接线：创建 Release 前必须过生产门禁。"""

    def test_t8_h_create_release_consumes_gate_report(self):
        self.assertTrue(CREATE_RELEASE.exists(), "缺 scripts/create_release.py")
        text = CREATE_RELEASE.read_text(encoding="utf-8")
        self.assertTrue("release_verdict" in text,
                        "create_release.py 必须消费 release_verdict（Spec §2.3）")
        self.assertTrue("--gate-report" in text,
                        "create_release.py 必须有 --gate-report（Spec §2.3）")
        # 既有口径不许被改掉（Spec §2.3 / test_repository_workflow_contract）
        self.assertIn("repository/tags", text)
        self.assertIn("不得隐式创建 tag", text)


class T17SharedParseService(unittest.TestCase):
    """T17 守卫（今天应为绿）：报价与技术工艺共用同一个统一解析服务。"""

    def test_t17_a_paths_are_single_source_of_truth(self):
        up = _module("tech_app.backend.services.unified_parse")
        qf = _module("cpq_quick_quote_file")
        if isinstance(up, Exception) or isinstance(qf, Exception):
            self.fail("缺模块：%r / %r" % (up, qf))
        self.assertEqual(up.SERVICE_PATH, qf.PARSE_PATH)
        self.assertEqual(up.CAPABILITY_PATH, qf.CAPABILITY_PATH)

    def test_t17_b_quick_quote_does_not_parse_dwg_itself(self):
        text = QUICK_FILE.read_text(encoding="utf-8")
        self.assertNotIn("ezdxf", text, "报价侧不得自己解析 DXF（Spec §2.4）")
        self.assertNotIn("ODAFileConverter", text, "报价侧不得自己起 ODA（Spec §2.4）")

    def test_t17_c_capability_probe_never_raises(self):
        up = _module("tech_app.backend.services.unified_parse")
        if isinstance(up, Exception):
            self.fail("缺 unified_parse：%r" % up)

        class Boom:
            def capability(self):
                raise RuntimeError("转换器炸了")

        out = up.capability(deps=Boom())
        self.assertIsInstance(out, dict)
        self.assertFalse(out["dwg"], "探测失败回 dwg=False，绝不抛异常")
        self.assertTrue(str(out.get("detail")).strip(), "必须给出失败原因")

    def test_t17_d_capability_surfaces_provider(self):
        up = _module("tech_app.backend.services.unified_parse")
        if isinstance(up, Exception):
            self.fail("缺 unified_parse：%r" % up)

        class Ok:
            def capability(self):
                return {"available": True, "provider": "oda",
                        "converter_version": "27.1", "preview_available": True}

        out = up.capability(deps=Ok())
        self.assertTrue(out["dwg"])
        self.assertEqual("oda", out["provider"])
        self.assertEqual("27.1", out["provider_version"])


if __name__ == "__main__":
    unittest.main()
