"""红测：统一解析服务端点（8010 上的 /api/file/parse）—— 逆向快速报价第 7 批。

Spec：`docs/specs/quick-quote-7-unified-parse-service.md`
依赖：批 5（报价侧客户端 `cpq_quick_quote_file.py` 已实现）。

现状缺口（2026-09-21 实测，不是推断）：
  · `tech_app/backend/main.py` 没有 /api/file/parse、没有 /api/file/parse/capability；
  · `tech_app/backend/services/unified_parse.py` 不存在；
  · 34 上实测：GET /api/file/parse/capability → 404，POST /api/file/parse → 405，
    所以销售拿 酒盒.dwg 走快速报价只能拿到 kind=service_unavailable。

纪律：
  · 全部离线：转换与解析一律走注入的假 `deps`，**不真跑 ODA、不真读盘、不连库、不联网**；
  · 真实样本（`裕同包装项目-待开发/酒盒.dwg`）只读，只在有转换器的本机跑；
  · 34 端到端只在 `CPQ_PARSE_SERVICE_E2E=1` 时跑，缺服务/缺样本一律 skipTest 并点名缺什么；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import base64
import importlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_NAME = "tech_app.backend.services.unified_parse"
MODULE_PATH = ROOT / "tech_app" / "backend" / "services" / "unified_parse.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
REAL_SAMPLE = SAMPLES_DIR / "酒盒.dwg"
E2E_BASE = os.environ.get("CPQ_PARSE_SERVICE_URL") or "http://172.16.10.34:8010"
E2E = os.environ.get("CPQ_PARSE_SERVICE_E2E") == "1"

import cpq_quick_quote_file as qq_file  # noqa: E402

SAMPLE_IR = {
    "units": {"drawing_units": "mm", "unit_status": "confirmed", "scale_to_mm": 1.0},
    "document": {"extents": [0.0, 0.0, 100.0, 50.0]},
    "layers": [{"name": "DESIGN"}, {"name": "0"}, {"name": "CUTTER"}],
    "blocks": [{"name": "B-1"}, {"name": "B-1"}, {"name": "A-2"}],
    "dimensions": [{"measured_value": 219.6435}, {"measured_value": 90.0}],
    "texts": [{"normalized_text": "235g白卡底PET光银裱A9 E坑", "raw_text": "235g{...}"},
              {"normalized_text": "V slot(V槽）", "raw_text": "V slot(V槽）"},
              {"normalized_text": "Flute/Grain", "raw_text": "Flute/Grain"}],
}


def load_module():
    try:
        return importlib.import_module(MODULE_NAME)
    except ImportError:
        return None


class FakeDeps:
    """注入用的假依赖：能力 / 转换 / DXF 解析三段都能按需失败。"""

    def __init__(self, *, available=True, dxf=b"DXF-CONTENT", ir=None,
                 cap_error=None, convert_error=None, parse_error=None):
        self.available = available
        self.dxf = dxf
        self.ir = SAMPLE_IR if ir is None else ir
        self.cap_error = cap_error
        self.convert_error = convert_error
        self.parse_error = parse_error
        self.convert_calls = []
        self.parse_calls = []

    def capability(self):
        if self.cap_error:
            raise self.cap_error
        return {"available": self.available, "provider": "oda" if self.available else "",
                "converter_version": "27.1" if self.available else "",
                "preview_available": self.available, "adapter_name": "oda"}

    def convert(self, project_id, filename, content):
        self.convert_calls.append({"project_id": project_id, "filename": filename,
                                   "size": len(content)})
        if self.convert_error:
            raise self.convert_error
        return {"dxf": self.dxf, "provider": "oda", "provider_version": "27.1",
                "status": "ok", "warnings": []}

    def parse_dxf(self, content, filename, source):
        self.parse_calls.append({"filename": filename, "size": len(content),
                                 "source": dict(source or {})})
        if self.parse_error:
            raise self.parse_error
        return self.ir


def payload(name="酒盒.dwg", raw=b"AC1027-fake", fields=None):
    body = {"name": name, "data": base64.b64encode(raw).decode("ascii")}
    if fields is not None:
        body["fields"] = fields
    return body


class Base(unittest.TestCase):
    def module(self):
        module = load_module()
        if module is None:
            self.fail("%s 不存在（Spec 批 7 §2.1）" % MODULE_NAME)
        return module

    def parse(self, body, deps):
        module = self.module()
        return module.parse_payload(body, deps=deps)

    def error_of(self, body, deps):
        module = self.module()
        with self.assertRaises(module.ParseError) as ctx:
            module.parse_payload(body, deps=deps)
        return ctx.exception


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANaming(Base):
    def test_a1_module_and_constants(self):
        module = self.module()
        self.assertEqual("cpq-unified-parse", module.SERVICE_NAME)
        self.assertEqual(("service", "provider", "provider_version", "dwg", "dxf", "preview"),
                         tuple(module.CAPABILITY_KEYS))
        self.assertEqual(("capability", "convert", "parse_dxf"),
                         tuple(module.PARSE_DEPS_METHODS))
        self.assertEqual("cpq-unified-parse", module.PARSE_PROJECT_ID,
                         "转换产物必须落到隔离解析项目目录（Spec §2.1）")
        self.assertEqual(("v_groove", "magnet", "window"), tuple(module.KEYWORD_FIELDS))

    def test_a2_fields_match_quote_side_verbatim(self):
        module = self.module()
        self.assertEqual(tuple(qq_file.QUICK_FIELDS), tuple(module.PARSE_FIELDS),
                         "两侧字段清单必须逐字一致（Spec §2.1）")

    def test_a3_parse_error_shape(self):
        module = self.module()
        err = module.ParseError("empty_file", "空文件", http_status=400, advice="换一个文件")
        self.assertEqual("empty_file", err.code)
        self.assertEqual(400, err.http_status)
        self.assertEqual("换一个文件", err.advice)
        for code in ("empty_file", "bad_payload", "unsupported_format", "file_too_large",
                     "converter_unavailable", "parse_failed"):
            self.assertIn(code, module.PARSE_ERROR_CODES)


# --------------------------------------------------------------------------- #
# B 组：能力预检
# --------------------------------------------------------------------------- #
class TestBCapability(Base):
    def test_b1_keys_are_exactly_the_closed_set(self):
        module = self.module()
        out = module.capability(deps=FakeDeps())
        self.assertEqual(set(module.CAPABILITY_KEYS), set(out.keys()))
        self.assertTrue(out["dwg"])
        self.assertEqual("oda", out["provider"])
        self.assertEqual("27.1", out["provider_version"])

    def test_b2_unavailable_converter_reports_false_not_raise(self):
        module = self.module()
        out = module.capability(deps=FakeDeps(available=False))
        self.assertFalse(out["dwg"], "能力必须来自真探测，不许硬编码 True（Spec §2.2）")
        self.assertEqual("", out["provider"])

    def test_b3_probe_error_does_not_raise(self):
        module = self.module()
        out = module.capability(deps=FakeDeps(cap_error=RuntimeError("converter probe failed")))
        self.assertFalse(out["dwg"])
        self.assertIn("detail", out)
        self.assertTrue(str(out["detail"]).strip(), "探测失败要给原因，不能只回 false")


# --------------------------------------------------------------------------- #
# C 组：正确失败
# --------------------------------------------------------------------------- #
class TestCFailures(Base):
    def test_c1_empty_file(self):
        err = self.error_of(payload(raw=b""), FakeDeps())
        self.assertEqual("empty_file", err.code)
        self.assertEqual(400, err.http_status)

    def test_c2_empty_file_does_not_call_converter(self):
        deps = FakeDeps()
        self.error_of(payload(raw=b""), deps)
        self.assertEqual([], deps.convert_calls, "空文件不该发转换请求（Spec §2.3）")

    def test_c3_bad_base64(self):
        err = self.error_of({"name": "酒盒.dwg", "data": "!!!not-base64!!!"}, FakeDeps())
        self.assertEqual("bad_payload", err.code)
        self.assertEqual(400, err.http_status)

    def test_c4_missing_name(self):
        err = self.error_of({"data": base64.b64encode(b"x").decode("ascii")}, FakeDeps())
        self.assertEqual("bad_payload", err.code)

    def test_c5_file_too_large(self):
        module = self.module()
        raw = b"x" * (module.MAX_PARSE_BYTES + 1)
        err = self.error_of(payload(raw=raw), FakeDeps())
        self.assertEqual("file_too_large", err.code)
        self.assertEqual(413, err.http_status)

    def test_c6_unsupported_format(self):
        err = self.error_of(payload(name="需求.docx"), FakeDeps())
        self.assertEqual("unsupported_format", err.code)
        self.assertEqual(400, err.http_status)
        self.assertIn("extract", str(err.advice), "文档类要指路既有 /api/extract（Spec §2.3）")

    def test_c7_converter_unavailable_is_503(self):
        deps = FakeDeps(available=False)
        err = self.error_of(payload(), deps)
        self.assertEqual("converter_unavailable", err.code)
        self.assertEqual(503, err.http_status)
        self.assertEqual([], deps.convert_calls, "能力为假时不许再试转换（Spec §2.3）")
        self.assertTrue(str(err.advice).strip(), "要给「转人工 / 转精准报价」建议")

    def test_c8_convert_exception_is_parse_failed_502(self):
        err = self.error_of(payload(), FakeDeps(convert_error=RuntimeError("oda blew up")))
        self.assertEqual("parse_failed", err.code)
        self.assertEqual(502, err.http_status)


# --------------------------------------------------------------------------- #
# D 组：字段裁剪
# --------------------------------------------------------------------------- #
class TestDFieldSelection(Base):
    def test_d1_only_requested_fields_come_back(self):
        out = self.parse(payload(fields=["layers", "units"]), FakeDeps())
        self.assertTrue(out["ok"])
        self.assertEqual({"layers", "units"}, set(out["fields"].keys()))

    def test_d2_default_is_the_whole_quick_set(self):
        module = self.module()
        out = self.parse(payload(), FakeDeps())
        self.assertTrue(set(out["fields"].keys()) <= set(module.PARSE_FIELDS))
        self.assertEqual(sorted(module.PARSE_FIELDS),
                         sorted(set(out["fields"]) | set(out["missing_fields"])),
                         "每个被请求的字段要么有值、要么进 missing，不许消失（Spec §2.3）")

    def test_d3_unknown_field_key_is_bad_payload(self):
        err = self.error_of(payload(fields=["geometry", "layers"]), FakeDeps())
        self.assertEqual("bad_payload", err.code)
        self.assertIn("geometry", str(err))

    def test_d4_success_payload_shape(self):
        module = self.module()
        out = self.parse(payload(), FakeDeps())
        for key in ("ok", "kind", "service", "provider", "provider_version",
                    "fields", "missing_fields", "warnings", "elapsed_ms"):
            self.assertIn(key, out, "出参缺 %s（Spec §2.3）" % key)
        self.assertEqual("drawing", out["kind"])
        self.assertEqual(module.SERVICE_NAME, out["service"])

    def test_d5_convert_gets_isolated_project_id(self):
        module = self.module()
        deps = FakeDeps()
        self.parse(payload(), deps)
        self.assertEqual([module.PARSE_PROJECT_ID],
                         [call["project_id"] for call in deps.convert_calls],
                         "转换产物只能落隔离解析项目（Spec §2.1）")


# --------------------------------------------------------------------------- #
# E 组：IR → 字段
# --------------------------------------------------------------------------- #
class TestEFieldsFromIr(Base):
    def fields(self, ir, wanted=None):
        module = self.module()
        return module.fields_from_ir(ir, wanted or module.PARSE_FIELDS)

    def test_e1_layers_sorted_and_blocks_deduped(self):
        out = self.fields(SAMPLE_IR)
        self.assertEqual(["0", "CUTTER", "DESIGN"], out["layers"])
        self.assertEqual(["A-2", "B-1"], out["blocks"])

    def test_e2_dimensions_and_texts_use_the_right_ir_keys(self):
        out = self.fields(SAMPLE_IR)
        self.assertEqual([219.6435, 90.0], out["annotated_dimensions"])
        self.assertIn("V slot(V槽）", out["text_annotations"])
        self.assertIn("Flute/Grain", out["text_annotations"])

    def test_e3_outline_from_extents_is_labelled(self):
        out = self.fields(SAMPLE_IR)
        size = out["outline_size"]
        self.assertAlmostEqual(100.0, float(size["width"]))
        self.assertAlmostEqual(50.0, float(size["height"]))
        self.assertEqual("document_extents", size["source"],
                         "图纸范围必须标明来源，不能冒充成品内尺寸（Spec §2.4）")

    def test_e4_keyword_hit_is_true_never_false(self):
        out = self.fields(SAMPLE_IR)
        self.assertIs(True, out["v_groove"], "命中 V槽 必须给 True")
        for key in ("magnet", "window"):
            self.assertNotIn(key, out, "未命中不许给 False（Spec §2.4）")

    def test_e5_unconfirmed_units_go_missing(self):
        ir = dict(SAMPLE_IR, units={"drawing_units": "mm", "unit_status": "assumed"})
        self.assertNotIn("units", self.fields(ir))

    def test_e6_material_notes_only_keeps_material_lines(self):
        out = self.fields(SAMPLE_IR)
        self.assertEqual(["235g白卡底PET光银裱A9 E坑"], out["material_notes"])

    def test_e7_cap_lists(self):
        module = self.module()
        ir = dict(SAMPLE_IR, texts=[{"normalized_text": "t%d" % i}
                                    for i in range(module.MAX_LIST_ITEMS + 20)])
        out = self.fields(ir)
        self.assertEqual(module.MAX_LIST_ITEMS, len(out["text_annotations"]))

    def test_e8_not_produced_fields_are_missing_not_empty(self):
        module = self.module()
        out = self.parse(payload(), FakeDeps())
        for key in ("box_features", "unfolded_size"):
            self.assertIn(key, out["missing_fields"],
                          "%s 本批不产，必须进 missing（Spec §2.4）" % key)


# --------------------------------------------------------------------------- #
# F 组：不落库
# --------------------------------------------------------------------------- #
class TestFNoPersistence(Base):
    def test_f1_module_does_not_touch_business_storage(self):
        self.assertTrue(MODULE_PATH.exists(), "%s 不存在" % MODULE_PATH)
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("import store", "from ..storage import", "da_db", "da_repo",
                          "meta_backend", "save_ir", "parse_conversion"):
            self.assertNotIn(forbidden, source,
                             "统一解析服务不许写业务存储：出现 %r（Spec §2.1）" % forbidden)


# --------------------------------------------------------------------------- #
# G 组：路由
# --------------------------------------------------------------------------- #
class TestGRoutes(Base):
    def test_g1_routes_registered(self):
        text = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn('"/api/file/parse"', text)
        self.assertIn('"/api/file/parse/capability"', text)
        self.assertIn("unified_parse", text, "路由必须复用 unified_parse，不许另写一套（Spec §2.5）")

    def test_g2_parse_error_status_is_passed_through(self):
        text = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        marker = text.find("/api/file/parse")
        window = text[max(0, marker - 4000):marker + 4000]
        self.assertIn("http_status", window, "ParseError 的 http_status 必须原样回（Spec §2.5）")
        self.assertIn("code", window, "错误必须带稳定 code")


# --------------------------------------------------------------------------- #
# H 组：真实样本（本机）
# --------------------------------------------------------------------------- #
class TestHRealSample(Base):
    def _converter(self):
        try:
            from tech_app.backend.services import cad_converter
        except ImportError:                                     # pragma: no cover
            self.skipTest("技术工艺侧 cad_converter 不在本机")
        cap = cad_converter.capability() or {}
        if not cap.get("available"):
            self.skipTest("本机没有可用 DWG 转换器（capability().available=false）")
        return True

    def test_h1_real_sample_fields_match_golden(self):
        module = self.module()
        self._converter()
        if not REAL_SAMPLE.exists():
            self.skipTest("真实样本不在本机：%s" % REAL_SAMPLE)
        raw = REAL_SAMPLE.read_bytes()
        out = module.parse_payload(payload(name=REAL_SAMPLE.name, raw=raw))
        fields = out["fields"]
        self.assertEqual(8, len(fields["layers"]), "酒盒.dwg 图层数金标 = 8（## 240）")
        for name in ("0", "CUTTER", "DESIGN"):
            self.assertIn(name, fields["layers"])
        self.assertEqual(316, len(fields["annotated_dimensions"]), "酒盒.dwg 尺寸标注金标 = 316")
        self.assertEqual(127, len(fields["text_annotations"]), "酒盒.dwg 文字金标 = 127")
        self.assertIs(True, fields.get("v_groove"), "图纸文字有 V slot(V槽）→ v_groove 为真")


# --------------------------------------------------------------------------- #
# I 组：34 端到端（要显式开）
# --------------------------------------------------------------------------- #
class TestE2EService(Base):
    def _get(self, path):
        if not E2E:
            self.skipTest("未开 CPQ_PARSE_SERVICE_E2E=1：本轮不跑线上解析服务")
        req = urllib.request.Request(E2E_BASE + path)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except Exception as exc:                                # noqa: BLE001
            self.skipTest("解析服务不可达（%s）：%s" % (E2E_BASE, str(exc)[:120]))

    def test_i1_capability_is_served(self):
        status, body = self._get("/api/file/parse/capability")
        self.assertEqual(200, status, "34 上 /api/file/parse/capability 必须是 200（Spec §0）")
        for key in ("service", "provider", "provider_version", "dwg", "dxf", "preview"):
            self.assertIn(key, body or {})

    def test_i2_real_dwg_round_trip(self):
        if not REAL_SAMPLE.exists():
            self.skipTest("真实样本不在本机：%s" % REAL_SAMPLE)
        status, body = self._get("/api/file/parse/capability")
        if status != 200 or not (body or {}).get("dwg"):
            self.skipTest("线上解析服务未就绪（HTTP %s / dwg=%s）"
                          % (status, (body or {}).get("dwg")))
        data = base64.b64encode(REAL_SAMPLE.read_bytes()).decode("ascii")
        req = urllib.request.Request(
            E2E_BASE + "/api/file/parse",
            data=json.dumps({"name": REAL_SAMPLE.name, "data": data,
                             "fields": ["layers", "annotated_dimensions"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=180) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(8, len(out["fields"]["layers"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
