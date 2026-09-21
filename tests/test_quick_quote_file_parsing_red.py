"""红测：文件解析接入与真实案例验收 —— 逆向快速报价第 5 批。

Spec：`docs/specs/quick-quote-5-file-parsing.md`
依赖：批 1–4。本批红测**假设前四批已实现**。

现状缺口（实测，不是推断）：
  · `cpq_quick_quote_file.py` 不存在；
  · `cpq_agent_server.py:3500` 的 `/api/extract` 只覆盖 `_extract_text()` 支持的文字类文档，
    DWG/DXF 一律"无法解析"，报价侧没有统一解析服务客户端；
  · 报价侧目前没有 DWG 转换器直连（`grep -l "dwg2dxf|ODAFileConverter|libredwg" cpq_*.py` 为空）——
    这是必须保持的现状；
  · `DEPLOYMENT.md` 没有 `CPQ_UNIFIED_PARSE_URL`；
  · 没有 `/api/quick-quote/parse` 路由，能力（provider / 版本 / 是否支持 DWG）没有出口，
    上一次 DWG 事故（健康检查声称能预览、实际不产预览）的教训就是能力必须显式可读。

纪律：
  · 全部离线：不真发 HTTP（`transport` 一律注入）、不调模型、不起服务、不写业务数据；
  · 真实 DWG 样本**只读**（`裕同包装项目-待开发/酒盒.dwg`、`圆盘盒.dwg`）；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import importlib
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FILE_PY = ROOT / "cpq_quick_quote_file.py"
SERVER_PY = ROOT / "cpq_agent_server.py"
DEPLOY_MD = ROOT / "DEPLOYMENT.md"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = SAMPLES_DIR / "酒盒.dwg"
ROUND_BOX = SAMPLES_DIR / "圆盘盒.dwg"

MODULE = "cpq_quick_quote_file"
MISSING = "cpq_quick_quote_file.py 不存在（Spec 批 5 §2.1）"
DEP2 = "cpq_quick_quote_match.py 不存在（Spec 批 2，批 5 端到端依赖它）"
TODAY = dt.date(2026, 9, 21)

QUICK_FIELDS = ("units", "annotated_dimensions", "outline_size", "box_features",
                "closure_type", "v_groove", "magnet", "window",
                "material_notes", "text_annotations", "layers", "blocks",
                "unfolded_size")
MATCH_INPUT_KEYS = (
    "box_type", "box_family", "closure_type",
    "inner_length", "inner_width", "inner_height",
    "grey_board_gsm", "face_paper_gsm",
    "insert_type", "print_colors", "lamination", "hot_stamping", "v_groove", "magnet",
    "quantity",
)
BANNED_CONVERTER_TOKENS = ("dwg2dxf", "dwg2SVG", "AppRun", "ODAFileConverter", "libredwg")
DWG_MAGIC = b"AC1027"

CAP_OK = {"service": "unified-dwg-parse", "provider": "oda", "provider_version": "27.1",
          "dwg": True, "dxf": True, "preview": True}
CAP_NO_DWG = dict(CAP_OK, dwg=False, provider="", provider_version="",
                  detail="本机未配置 DWG 转换器")


def load_module(name):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def record_transport(responses):
    """按 URL 命中最近一次响应；记录每次调用，方便断言"调没调服务"。"""
    calls = []

    def transport(url, payload=None):
        calls.append({"url": url, "payload": copy.deepcopy(payload)})
        for key, value in responses.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return copy.deepcopy(value)
        raise AssertionError("测试未登记的 URL：%s" % url)

    transport.calls = calls
    return transport


DRAWING_PAYLOAD = {
    "units": "mm",
    "outline_size": {"length": 210.0, "width": 160.0, "height": 85.0},
    "annotated_dimensions": [{"value": 200.0, "axis": "inner_length"},
                             {"value": 150.0, "axis": "inner_width"},
                             {"value": 80.0, "axis": "inner_height"}],
    "box_features": {"box_type": "YT-RB-01001-A", "box_family": "01天地盖"},
    "closure_type": "磁吸",
    "v_groove": True, "magnet": True, "window": False,
    "material_notes": ["面纸 250g 铜版纸", "灰板 1200gsm"],
    "text_annotations": ["礼盒 天地盖"], "layers": ["DIM", "CUT"],
    "blocks": ["TITLE"], "unfolded_size": {"length": 420.0, "width": 300.0},
}


class Base(unittest.TestCase):
    maxDiff = None

    def module(self):
        module = load_module(MODULE)
        if module is None:
            self.fail(MISSING)
        return module

    def match_module(self):
        module = load_module("cpq_quick_quote_match")
        if module is None:
            self.fail(DEP2)
        return module


# --------------------------------------------------------------------------- #
# A 组：命名契约与"不装第二套 ODA"
# --------------------------------------------------------------------------- #
class TestANamingContract(Base):
    def test_a1_module_constants(self):
        module = self.module()
        self.assertEqual("quick_quote_file_v1", module.ENGINE_VERSION)
        self.assertEqual("packaging", module.INDUSTRY)
        self.assertEqual("CPQ_UNIFIED_PARSE_URL", module.PARSE_URL_ENV)
        self.assertEqual("/api/file/parse", module.PARSE_PATH)
        self.assertEqual("/api/file/parse/capability", module.CAPABILITY_PATH)
        self.assertEqual("/api/quick-quote/parse", module.QUICK_QUOTE_PARSE_PATH)
        self.assertEqual((".dwg", ".dxf"), tuple(module.DWG_EXTS))
        self.assertEqual(QUICK_FIELDS, tuple(module.QUICK_FIELDS))

    def test_a2_default_url_is_single_place(self):
        module = self.module()
        self.assertTrue(module.DEFAULT_PARSE_URL.startswith("http://127.0.0.1:8010"),
                        "统一解析服务挂在 8010（Spec §2.1）")
        self.assertTrue(module.DEFAULT_PARSE_URL.endswith(module.PARSE_PATH))

    def test_a3_exceptions(self):
        module = self.module()
        self.assertTrue(issubclass(module.ParseServiceUnavailable, Exception))
        self.assertTrue(issubclass(module.ParseUnsupported, module.QuickQuoteFileError))
        self.assertTrue(issubclass(module.ParseServiceUnavailable, RuntimeError))

    def test_a4_public_callables(self):
        module = self.module()
        for name in ("parse_url", "capability", "parse_file", "to_match_inputs",
                     "parse_and_match"):
            self.assertTrue(callable(getattr(module, name, None)),
                            "%s.%s() 缺失（Spec §2.1）" % (MODULE, name))

    def test_a5_module_client_only_no_converter(self):
        self.assertTrue(FILE_PY.exists(), MISSING)
        source = FILE_PY.read_text(encoding="utf-8")
        for banned in BANNED_CONVERTER_TOKENS:
            self.assertNotIn(banned, source,
                             "报价侧不得直连转换器（共享统一解析服务）：%s" % banned)
        for banned in ("tech_app", "cad_converter", "subprocess"):
            self.assertNotIn(banned, source,
                             "报价侧只当客户端，不得 import 技术工艺解析实现：%s" % banned)

    def test_a6_no_second_oda_in_quote_side(self):
        offenders = []
        for path in sorted(ROOT.glob("cpq_*.py")):
            source = path.read_text(encoding="utf-8", errors="ignore")
            for token in BANNED_CONVERTER_TOKENS:
                if token in source:
                    offenders.append("%s:%s" % (path.name, token))
        self.assertEqual([], offenders,
                         "报价侧不得出现第二套 ODA/LibreDWG：%s" % offenders)

    def test_a7_document_exts_cover_blueprint_added_formats(self):
        module = self.module()
        for ext in (".txt", ".xlsx", ".pdf", ".png", ".jpg"):
            self.assertIn(ext, tuple(module.DOC_EXTS))


# --------------------------------------------------------------------------- #
# B 组：能力预检
# --------------------------------------------------------------------------- #
class TestBCapability(Base):
    def test_b1_capability_shape(self):
        module = self.module()
        transport = record_transport({"capability": CAP_OK})
        cap = module.capability(transport=transport)
        for key in ("service", "provider", "provider_version", "dwg", "dxf", "preview"):
            self.assertIn(key, cap, "能力表缺字段：%s（Spec §2.2）" % key)
        self.assertTrue(cap["dwg"])
        self.assertEqual(1, len(transport.calls), "能力预检只发一次请求")

    def test_b2_service_down_raises(self):
        module = self.module()
        transport = record_transport({"capability": AssertionError("connection refused")})
        with self.assertRaises(module.ParseServiceUnavailable):
            module.capability(transport=transport)

    def test_b3_non_dict_response_raises(self):
        module = self.module()
        transport = record_transport({"capability": ["not", "a", "dict"]})
        with self.assertRaises(module.ParseServiceUnavailable,
                               msg="返回体不是 dict 时必须报错，不得当成空能力表"):
            module.capability(transport=transport)

    def test_b4_dwg_unsupported_raises_with_advice(self):
        module = self.module()
        transport = record_transport({"capability": CAP_NO_DWG})
        with self.assertRaises(module.ParseUnsupported) as ctx:
            module.parse_file("酒盒.dwg", b"AC1027" + b"\x00" * 64, transport=transport)
        advice = getattr(ctx.exception, "advice", "") or str(ctx.exception)
        self.assertTrue(advice, "能力不足必须给用户可见建议")
        self.assertTrue(("精准报价" in advice) or ("人工" in advice),
                        "建议要指向转精准报价或人工处理（Spec §2.3）")

    def test_b5_capability_env_override_is_honored(self):
        module = self.module()
        with mock.patch.dict("os.environ", {"CPQ_UNIFIED_PARSE_URL":
                                            "http://127.0.0.1:9999/api/file/parse"}):
            reloaded = importlib.reload(module)
            self.assertIn("9999", reloaded.parse_url())
        importlib.reload(module)


# --------------------------------------------------------------------------- #
# C 组：正确失败
# --------------------------------------------------------------------------- #
class TestCFailLoud(Base):
    def test_c1_empty_file_rejected_without_request(self):
        module = self.module()
        transport = record_transport({})
        with self.assertRaises(module.QuickQuoteFileError):
            module.parse_file("需求.docx", b"", transport=transport)
        self.assertEqual([], transport.calls, "空文件不得发请求（Spec §2.3）")

    def test_c2_unknown_ext_lists_supported_formats(self):
        module = self.module()
        transport = record_transport({})
        with self.assertRaises(module.QuickQuoteFileError) as ctx:
            module.parse_file("方案.psd", b"PSD-bytes", transport=transport)
        message = str(ctx.exception)
        self.assertIn(".dwg", message)
        self.assertIn(".pdf", message)
        self.assertEqual([], transport.calls)

    def test_c3_service_5xx_raises_unavailable(self):
        module = self.module()
        transport = record_transport({"capability": CAP_OK,
                                      "parse": RuntimeError("500 server error")})
        with self.assertRaises(module.ParseServiceUnavailable):
            module.parse_file("酒盒.dwg", DWG_MAGIC + b"\x00" * 128, transport=transport)

    def test_c4_never_returns_empty_parse_on_failure(self):
        module = self.module()
        transport = record_transport({"capability": CAP_OK,
                                      "parse": RuntimeError("boom")})
        try:
            result = module.parse_file("酒盒.dwg", DWG_MAGIC + b"\x00" * 32,
                                       transport=transport)
        except (module.ParseServiceUnavailable, module.ParseUnsupported,
                module.QuickQuoteFileError):
            return
        self.fail("解析失败必须抛错，不得返回 %r（Spec §2.3）" % result)


# --------------------------------------------------------------------------- #
# D 组：文档路径不依赖 DWG 服务
# --------------------------------------------------------------------------- #
class TestDDocumentPath(Base):
    def test_d1_document_does_not_call_parse_service(self):
        module = self.module()
        transport = record_transport({})
        with mock.patch("cpq_agent_server._extract_text",
                        return_value=("礼盒 天地盖 200*150*80 面纸250g", "")) as extractor:
            result = module.parse_file("需求.txt", "礼盒".encode("utf-8"),
                                       transport=transport)
        self.assertEqual("document", result["kind"])
        self.assertIn("天地盖", result["text"])
        self.assertEqual([], transport.calls,
                         "文档类不得依赖统一解析服务（Spec §2.3）")
        self.assertTrue(extractor.called, "文档类必须复用既有 _extract_text() 口径")

    def test_d2_xlsx_and_pdf_use_extract_path(self):
        module = self.module()
        for name, text in (("报价.xlsx", "数量 3000"), ("需求.pdf", "内长 200mm")):
            transport = record_transport({})
            with mock.patch("cpq_agent_server._extract_text", return_value=(text, "")):
                result = module.parse_file(name, b"bytes", transport=transport)
            self.assertEqual("document", result["kind"], name)
            self.assertEqual([], transport.calls, name)

    def test_d3_image_uses_extract_path_not_dwg_service(self):
        module = self.module()
        transport = record_transport({})
        with mock.patch("cpq_agent_server._extract_text", return_value=("", "图片转文字")):
            try:
                module.parse_file("需求.png", b"PNG", transport=transport)
            except module.QuickQuoteFileError:
                pass
        self.assertEqual([], transport.calls, "图片不得被送去 DWG 解析服务")


# --------------------------------------------------------------------------- #
# E 组：结构化映射
# --------------------------------------------------------------------------- #
class TestEMapping(Base):
    def parse_drawing(self, module, payload=None):
        transport = record_transport({"capability": CAP_OK,
                                      "parse": dict(DRAWING_PAYLOAD, **(payload or {}))})
        return module.parse_file("酒盒.dwg", DWG_MAGIC + b"\x00" * 128, transport=transport)

    def test_e1_parse_requests_only_quick_fields(self):
        module = self.module()
        transport = record_transport({"capability": CAP_OK, "parse": dict(DRAWING_PAYLOAD)})
        module.parse_file("酒盒.dwg", DWG_MAGIC + b"\x00" * 64, transport=transport)
        parse_calls = [call for call in transport.calls if "capability" not in call["url"]]
        self.assertEqual(1, len(parse_calls))
        payload = parse_calls[0]["payload"]
        self.assertEqual(set(QUICK_FIELDS), set(payload["fields"]),
                         "报价快速通道只索取匹配所需字段（Spec §2.3）")
        self.assertEqual("酒盒.dwg", payload["name"])
        self.assertTrue(base64.b64decode(payload["data"]), "文件必须 base64 传原始字节")

    def test_e2_parse_result_shape(self):
        module = self.module()
        result = self.parse_drawing(module)
        self.assertEqual("drawing", result["kind"])
        self.assertEqual("oda", result["provider"])
        self.assertEqual("27.1", result["provider_version"])
        self.assertIn("fields", result)
        self.assertIn("missing_fields", result)
        for key in QUICK_FIELDS:
            self.assertIn(key, result["fields"], "解析结果缺快字段：%s" % key)

    def test_e3_units_converted_to_mm(self):
        module = self.module()
        parsed = self.parse_drawing(module, {"units": "cm",
                                            "outline_size": {"length": 21.0, "width": 16.0,
                                                             "height": 8.5},
                                            "annotated_dimensions": []})
        mapped = module.to_match_inputs(parsed)
        inputs = mapped["inputs"]
        self.assertAlmostEqual(210.0, float(inputs["inner_length"]), places=6)
        self.assertAlmostEqual(160.0, float(inputs["inner_width"]), places=6)
        self.assertAlmostEqual(85.0, float(inputs["inner_height"]), places=6)

    def test_e4_inch_units_converted(self):
        module = self.module()
        parsed = self.parse_drawing(module, {"units": "inch",
                                            "outline_size": {"length": 8.0, "width": 6.0,
                                                             "height": 3.0},
                                            "annotated_dimensions": []})
        inputs = module.to_match_inputs(parsed)["inputs"]
        self.assertAlmostEqual(203.2, float(inputs["inner_length"]), places=6)

    def test_e5_annotated_inner_size_wins_over_outline(self):
        module = self.module()
        parsed = self.parse_drawing(module)
        inputs = module.to_match_inputs(parsed)["inputs"]
        self.assertAlmostEqual(200.0, float(inputs["inner_length"]), places=6,
                               msg="标注内尺寸优先于外形尺寸（Spec §2.4 第 2 条）")

    def test_e6_features_mapped_to_match_keys(self):
        module = self.module()
        parsed = self.parse_drawing(module)
        inputs = module.to_match_inputs(parsed)["inputs"]
        self.assertEqual("YT-RB-01001-A", inputs["box_type"])
        self.assertEqual("01天地盖", inputs["box_family"])
        self.assertEqual("磁吸", inputs["closure_type"])
        self.assertIs(True, inputs["v_groove"])
        self.assertIs(True, inputs["magnet"])
        self.assertIs(False, inputs["window"])

    def test_e7_material_notes_parsed_to_gsm(self):
        module = self.module()
        parsed = self.parse_drawing(module)
        inputs = module.to_match_inputs(parsed)["inputs"]
        self.assertAlmostEqual(250.0, float(inputs["face_paper_gsm"]), places=6)
        self.assertAlmostEqual(1200.0, float(inputs["grey_board_gsm"]), places=6)

    def test_e8_unparsed_keys_go_to_missing_not_guessed(self):
        module = self.module()
        parsed = self.parse_drawing(module)
        mapped = module.to_match_inputs(parsed)
        self.assertIn("quantity", mapped["missing"], "解析不出数量必须如实列入 missing，不得猜")
        self.assertNotIn("quantity", mapped["inputs"])
        for key in mapped["missing"]:
            self.assertNotIn(key, mapped["inputs"], "missing 的键不得出现在 inputs 里")

    def test_e9_fallback_fills_gaps_and_is_labelled(self):
        module = self.module()
        parsed = self.parse_drawing(module)
        mapped = module.to_match_inputs(parsed, fallback={"quantity": 3000,
                                                         "inner_length": 999})
        self.assertAlmostEqual(3000.0, float(mapped["inputs"]["quantity"]), places=6)
        self.assertEqual("fallback", mapped["sources"]["quantity"])
        self.assertEqual("parse", mapped["sources"]["inner_length"])
        self.assertAlmostEqual(200.0, float(mapped["inputs"]["inner_length"]), places=6,
                               msg="解析值与 fallback 冲突时以解析值为准（Spec §2.4 第 4 条）")

    def test_e10_missing_units_defaults_to_mm_with_warning(self):
        module = self.module()
        parsed = self.parse_drawing(module, {"units": ""})
        mapped = module.to_match_inputs(parsed)
        self.assertAlmostEqual(200.0, float(mapped["inputs"]["inner_length"]), places=6)
        self.assertTrue(mapped.get("warnings"), "单位缺失要记 warning，不得静默")


# --------------------------------------------------------------------------- #
# F 组：端到端串起来
# --------------------------------------------------------------------------- #
class TestFEndToEnd(Base):
    def cases(self):
        return [{
            "case_code": "QQ-2026-0001", "case_version": 1, "industry": "packaging",
            "customer_masked": "华东酒类客户A", "box_type_code": "YT-RB-01001-A",
            "box_family": "01天地盖", "closure_type": "磁吸", "fit_clearance": 1.5,
            "inner_length": 200.0, "inner_width": 150.0, "inner_height": 80.0,
            "material_code": "MAT-FACE-157", "grey_board_gsm": 1200.0,
            "face_paper_gsm": 250.0, "print_colors": "CMYK", "lamination": True,
            "hot_stamping": True, "v_groove": True, "window": False,
            "insert_type": "EVA内托", "magnet": True, "ribbon": False,
            "quantity_tiers": [{"qty": 3000, "unit_price": 10.8}],
            "bom_summary": "面纸+灰板+内托", "process_summary": "印刷/覆膜/烫金/V槽",
            "standard_cost": 60.0, "standard_price": 80.0, "deal_price": 78.0,
            "currency": "CNY", "tax_included": False, "quote_date": "2026-06-01",
            "valid_from": "2026-06-01", "valid_until": "2027-03-01",
            "source_type": "workbook", "source_ref": "报价逻辑-0903.xlsx!报价表",
            "review_status": "reviewed", "version": 1,
        }]

    def test_f1_parse_and_match_returns_candidates(self):
        module = self.module()
        match_module = self.match_module()
        transport = record_transport({"capability": CAP_OK, "parse": dict(DRAWING_PAYLOAD)})
        result = module.parse_and_match("酒盒.dwg", DWG_MAGIC + b"\x00" * 64,
                                        cases=self.cases(), transport=transport,
                                        weights=match_module.DEFAULT_WEIGHTS, today=TODAY)
        self.assertEqual("drawing", result["parse"]["kind"])
        self.assertIn("inputs", result)
        self.assertTrue(result["match"]["candidates"], "解析结果必须能直接进候选检索")
        top = result["match"]["candidates"][0]
        self.assertEqual("QQ-2026-0001", top["case_code"])
        self.assertTrue(result["match"]["requires_manual_selection"])

    def test_f2_zero_candidate_advises_precise_quote(self):
        module = self.module()
        match_module = self.match_module()
        transport = record_transport({"capability": CAP_OK,
                                      "parse": dict(DRAWING_PAYLOAD,
                                                    box_features={"box_type": "NOPE"})})
        result = module.parse_and_match("酒盒.dwg", DWG_MAGIC + b"\x00" * 64,
                                        cases=self.cases(), transport=transport,
                                        weights=match_module.DEFAULT_WEIGHTS, today=TODAY)
        self.assertEqual([], result["match"]["candidates"])
        self.assertIn("精准报价", result["match"]["no_candidate_reason"])


# --------------------------------------------------------------------------- #
# G 组：路由与部署登记
# --------------------------------------------------------------------------- #
class TestGRouteAndDeployment(Base):
    def test_g1_server_route_registered(self):
        module = self.module()
        path = module.QUICK_QUOTE_PARSE_PATH
        source = SERVER_PY.read_text(encoding="utf-8")
        self.assertIn(path, source, "cpq_agent_server.py 必须注册 %s" % path)
        self.assertIn("_handle_quick_quote_parse", source)
        handler = source.split("def _handle_quick_quote_parse", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("capability", handler, "路由必须回传能力段（Spec §2.5）")
        for banned in ("/api/projects/", "cpq_tech_bridge", "dwg2dxf"):
            self.assertNotIn(banned, handler,
                             "报价解析路由不得转调技术工艺或自行转换：%s" % banned)

    def test_g2_deployment_documents_env(self):
        module = self.module()
        text = DEPLOY_MD.read_text(encoding="utf-8")
        self.assertIn(module.PARSE_URL_ENV, text,
                      "DEPLOYMENT.md 必须登记 %s（Spec §2.5）" % module.PARSE_URL_ENV)

    def test_g3_capability_exposed_to_frontend(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        self.assertIn("provider_version", source,
                      "能力段（provider / 版本）必须能到前端，避免又一次"
                      "「页面宣称支持、实际不支持」")


# --------------------------------------------------------------------------- #
# H 组：真实样本验收
# --------------------------------------------------------------------------- #
class TestHRealSamples(Base):
    def require_sample(self, path):
        if not path.exists():
            self.fail("真实 DWG 样本不在本机（不入库）：%s" % path.name)

    def test_h1_samples_present_and_readable(self):
        for path in (WINE_BOX, ROUND_BOX):
            self.require_sample(path)
            with path.open("rb") as handle:
                header = handle.read(6)
            self.assertEqual(DWG_MAGIC, header, "%s 文件头应为 AC1027" % path.name)

    def test_h2_real_parse_when_service_available(self):
        module = self.module()
        self.require_sample(WINE_BOX)
        try:
            cap = module.capability()
        except module.ParseServiceUnavailable as exc:
            self.skipTest("统一解析服务不在线（CPQ_UNIFIED_PARSE_URL 未部署/未启动）：%s" % exc)
        if not cap.get("dwg"):
            self.skipTest("统一解析服务报告 dwg=false（provider=%s）："
                          "能力未就绪，真实解析未验收" % cap.get("provider"))
        result = module.parse_file(WINE_BOX.name, WINE_BOX.read_bytes())
        self.assertEqual("drawing", result["kind"])
        self.assertTrue(result["fields"], "真实样本必须解出字段，不得空结果")

    def test_h3_quote_side_has_no_converter_direct_call(self):
        for name in ("cpq_quick_quote_file.py", "cpq_agent_server.py"):
            path = ROOT / name
            self.assertTrue(path.exists(), "%s 不存在" % name)
            source = path.read_text(encoding="utf-8", errors="ignore")
            for token in BANNED_CONVERTER_TOKENS:
                self.assertNotIn(token, source,
                                 "%s 不得直连转换器（共享统一解析服务）：%s" % (name, token))


if __name__ == "__main__":
    unittest.main(verbosity=2)
