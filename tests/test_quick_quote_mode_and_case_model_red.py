"""红测：快速报价模式与标准报价案例数据模型 —— 逆向快速报价第 1 批。

Spec：`docs/specs/quick-quote-1-mode-and-case-model.md`
依赖：无（第 1 批；批 2–5 建立在它之上）。

现状缺口（实测，不是推断）：
  · `报价首页.html:1283-1287` 只有「报价助手 / 配置助手 / 规则助手 / 技术工艺」四个入口，
    全仓 `grep -c 快速报价` → 0，精准/快速两条报价路径没有分开；
  · `cpq_quick_quote_case.py` 不存在 —— 没有标准报价案例的数据结构、来源分层、审核状态、
    有效期与准入判定，也没有快速报价五步的定义；
  · `cpq_kb.py:63` 的 `SOURCE_TYPES` 只被成本公式使用，案例侧没有同一套口径（口径一散
    就会出现「演示数据被当成权威案例去报价」）；
  · 报价侧没有案例表（`cpq_wf.py` 的 `cpq_wf_card_step.data_snapshot` 是同名覆盖快照，
    不能当案例库），也没有 `kb_quick_quote_config`；
  · `cpq_agent_server.py` 没有 `/api/quick-quote/cases` 路由，处理函数也不存在。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写业务数据；
  · 案例与配置一律用注入参数（`cases=` / `config=`）；库读不到只断言**抛错**，不要求真连库；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib
import inspect
import json
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASE_PY = ROOT / "cpq_quick_quote_case.py"
SERVER_PY = ROOT / "cpq_agent_server.py"
HOME_HTML = ROOT / "报价首页.html"
PANEL_JS = ROOT / "tech_app" / "frontend" / "quick-quote-panel.js"

import cpq_kb  # noqa: E402

MODULE_NAME = "cpq_quick_quote_case"
MODULE_MISSING = "cpq_quick_quote_case.py 不存在（Spec 批 1 §2.1）"

CASE_FIELDS_REQUIRED = (
    "case_code", "case_version", "customer_masked",
    "box_type_code", "box_family", "closure_type", "fit_clearance",
    "inner_length", "inner_width", "inner_height",
    "material_code", "grey_board_gsm", "face_paper_gsm",
    "print_colors", "lamination", "hot_stamping", "v_groove",
    "insert_type", "magnet", "ribbon", "window",
    "quantity_tiers", "bom_summary", "process_summary",
    "standard_cost", "standard_price", "deal_price",
    "currency", "tax_included",
    "quote_date", "valid_from", "valid_until",
    "source_type", "source_ref", "review_status", "version", "industry",
)
REASON_CODES = ("ok", "missing_fields", "retired", "industry_mismatch",
                "source_not_authoritative", "not_reviewed", "expired")
TODAY = dt.date(2026, 9, 21)


def load_case_module():
    """模块不存在时返回 None（每个用例给明确断言，不抛 ImportError）。"""
    try:
        return importlib.import_module(MODULE_NAME)
    except Exception:
        return None


def base_case(**over):
    case = {
        "case_code": "QQ-2026-0001", "case_version": 3, "industry": "packaging",
        "customer_masked": "华东酒类客户A",
        "box_type_code": "YT-RB-01001-A", "box_family": "01天地盖",
        "closure_type": "磁吸", "fit_clearance": 1.5,
        "inner_length": 200.0, "inner_width": 150.0, "inner_height": 80.0,
        "material_code": "MAT-FACE-157", "grey_board_gsm": 1200, "face_paper_gsm": 200,
        "print_colors": "CMYK", "lamination": True, "hot_stamping": True, "v_groove": True,
        "window": False, "insert_type": "EVA内托", "magnet": True, "ribbon": False,
        "quantity_tiers": [{"qty": 1000, "unit_price": 12.5},
                           {"qty": 3000, "unit_price": 10.8}],
        "bom_summary": "面纸+灰板+内托（5 部件）",
        "process_summary": "印刷/覆膜/烫金/V槽/裱贴",
        "standard_cost": 60.049408129741252,
        "standard_price": 80.06587750632167,
        "deal_price": 78.0,
        "currency": "CNY", "tax_included": False,
        "quote_date": "2026-03-01", "valid_from": "2026-03-01", "valid_until": "",
        "source_type": "workbook", "source_ref": "报价逻辑-0903.xlsx!报价表",
        "review_status": "reviewed", "version": 3,
    }
    case.update(over)
    return case


class Base(unittest.TestCase):
    maxDiff = None

    def module(self):
        module = load_case_module()
        if module is None:
            self.fail(MODULE_MISSING)
        return module

    def meta(self, module, name):
        """命名契约：缺常量/函数直接给点名断言。"""
        if not hasattr(module, name):
            self.fail("%s 缺少命名契约 %s（Spec %%s）" % (MODULE_NAME, name))
        return getattr(module, name)


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANamingContract(Base):
    def test_a1_engine_and_mode_constants(self):
        module = self.module()
        self.assertEqual("quick_quote_case_v1", self.meta(module, "ENGINE_VERSION"))
        self.assertEqual("packaging", self.meta(module, "INDUSTRY"))
        self.assertEqual("precise", self.meta(module, "MODE_PRECISE"))
        self.assertEqual("quick", self.meta(module, "MODE_QUICK"))
        self.assertEqual(("precise", "quick"), tuple(self.meta(module, "QUOTE_MODES")))

    def test_a2_quick_quote_steps_stay_in_quote_side(self):
        module = self.module()
        steps = tuple(self.meta(module, "QUICK_QUOTE_STEPS"))
        self.assertEqual(("requirement", "match_cases", "baseline", "adjust", "quote"), steps)
        for banned in ("tech", "process", "bom", "cost", "publish"):
            self.assertNotIn(banned, steps,
                             "快速报价五步里不得出现技术工艺步骤：%s" % banned)

    def test_a3_source_types_single_source_of_truth(self):
        module = self.module()
        self.assertEqual(tuple(cpq_kb.SOURCE_TYPES), tuple(self.meta(module, "CASE_SOURCES")),
                         "案例来源分层必须复用 cpq_kb.SOURCE_TYPES，不得各写一份")
        self.assertEqual(("draft", "reviewed", "retired"),
                         tuple(self.meta(module, "CASE_REVIEW_STATUSES")))
        self.assertEqual(("workbook", "dwg_confirmed"),
                         tuple(self.meta(module, "QUICK_QUOTE_ALLOWED_SOURCES")))
        self.assertEqual(("reviewed",), tuple(self.meta(module, "QUICK_QUOTE_ALLOWED_REVIEW")))

    def test_a4_table_names_and_route_constant(self):
        module = self.module()
        self.assertEqual("cpq_qq_standard_case", self.meta(module, "CASE_TABLE"))
        self.assertEqual("kb_quick_quote_config", self.meta(module, "CONFIG_TABLE"))
        self.assertEqual("/api/quick-quote/cases", self.meta(module, "QUICK_QUOTE_CASES_PATH"))
        for exc in ("QuickQuoteCaseError", "CaseLibraryUnavailable"):
            self.assertTrue(inspect.isclass(getattr(module, exc, None)),
                            "%s.%s 必须是异常类（Spec §2.1）" % (MODULE_NAME, exc))
            self.assertTrue(issubclass(getattr(module, exc), Exception))

    def test_a5_public_callables_exist(self):
        module = self.module()
        for name in ("default_config", "load_config", "normalize_case", "case_missing_fields",
                     "quote_eligibility", "load_cases", "quick_quote_cases", "find_case",
                     "build_case_from_quote", "save_case", "init"):
            self.assertTrue(callable(getattr(module, name, None)),
                            "%s.%s() 缺失（Spec §2.3/§2.4）" % (MODULE_NAME, name))

    def test_a6_init_creates_case_table_idempotently(self):
        module = self.module()
        source = CASE_PY.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS", source)
        self.assertIn("cpq_qq_standard_case", source)
        self.assertIn("ADD COLUMN IF NOT EXISTS", source,
                      "init() 必须用增量列补齐老库（Spec §2.4）")


# --------------------------------------------------------------------------- #
# B 组：案例字段与归一化
# --------------------------------------------------------------------------- #
class TestBCaseFields(Base):
    def test_b1_case_fields_cover_business_list(self):
        module = self.module()
        fields = set(self.meta(module, "CASE_FIELDS"))
        missing = [key for key in CASE_FIELDS_REQUIRED if key not in fields]
        self.assertEqual([], missing, "案例字段缺这些业务必需项：%s" % missing)

    def test_b2_normalize_case_cleans_dirty_values(self):
        module = self.module()
        row = module.normalize_case({
            "case_code": " QQ-2026-0001 ", "face_paper_gsm": "250g",
            "grey_board_gsm": "1200 克", "lamination": "是", "hot_stamping": "有",
            "v_groove": "需要", "window": "否", "magnet": "", "ribbon": None,
            "inner_length": "200 mm", "tax_included": "含税",
            "standard_price": "80.06587750632167", "currency": "cny",
        })
        self.assertEqual("QQ-2026-0001", row["case_code"])
        self.assertEqual(250.0, row["face_paper_gsm"])
        self.assertEqual(1200.0, row["grey_board_gsm"])
        self.assertEqual(200.0, row["inner_length"])
        self.assertIs(True, row["lamination"])
        self.assertIs(True, row["hot_stamping"])
        self.assertIs(True, row["v_groove"])
        self.assertIs(False, row["window"])
        self.assertIsNone(row["magnet"])
        self.assertIsNone(row["ribbon"])
        self.assertIs(True, row["tax_included"])
        self.assertEqual("CNY", row["currency"])
        self.assertAlmostEqual(80.06587750632167, row["standard_price"], places=9)

    def test_b3_normalize_case_does_not_mutate_input(self):
        module = self.module()
        raw = {"case_code": " X ", "face_paper_gsm": "250g"}
        snapshot = copy.deepcopy(raw)
        module.normalize_case(raw)
        self.assertEqual(snapshot, raw, "normalize_case() 必须是纯函数（不改入参）")

    def test_b4_case_missing_fields_names_keys(self):
        module = self.module()
        row = base_case(box_type_code="", closure_type="", quantity_tiers=[], standard_price=None)
        missing = list(module.case_missing_fields(row))
        for key in ("box_type_code", "closure_type", "quantity_tiers", "standard_price"):
            self.assertIn(key, missing, "缺字段必须点名：%s" % key)

    def test_b5_quantity_tiers_are_bands(self):
        module = self.module()
        row = module.normalize_case(base_case())
        tiers = row["quantity_tiers"]
        self.assertTrue(tiers, "数量必须是档位（Spec §2.2 第 2 条）")
        self.assertEqual([1000, 3000], [int(tier["qty"]) for tier in tiers])
        self.assertAlmostEqual(12.5, float(tiers[0]["unit_price"]), places=6)

    def test_b6_customer_must_be_masked(self):
        module = self.module()
        for bad in ("张三", "13800138000", "zhang@example.com", "13812345678"):
            with self.assertRaises(module.QuickQuoteCaseError,
                                   msg="疑似未脱敏客户信息不得入库：%s" % bad):
                module.save_case(base_case(customer_masked=bad),
                                 user={"user_id": "100", "role_code": "sales_mgr"})


# --------------------------------------------------------------------------- #
# C 组：有效期与准入
# --------------------------------------------------------------------------- #
class TestCEligibility(Base):
    def test_c1_default_config_shape(self):
        module = self.module()
        config = module.default_config()
        self.assertEqual({"workbook": 365, "dwg_confirmed": 180, "demo": 0, "unknown": 0},
                         dict(config["case_valid_days_by_source"]))
        self.assertEqual(30, config["expiry_warn_days"])
        self.assertEqual(["workbook", "dwg_confirmed"], list(config["quick_quote_allowed_sources"]))
        self.assertEqual(["reviewed"], list(config["quick_quote_allowed_review"]))
        self.assertEqual(3, config["min_candidates"])
        self.assertEqual(5, config["top_n_candidates"])

    def test_c2_load_config_merges_over_defaults(self):
        module = self.module()
        merged = module.load_config({"expiry_warn_days": 7})
        self.assertEqual(7, merged["expiry_warn_days"])
        self.assertEqual({"workbook": 365, "dwg_confirmed": 180, "demo": 0, "unknown": 0},
                         dict(merged["case_valid_days_by_source"]),
                         "缺失键必须用默认值补齐（Spec §2.3）")

    def test_c3_load_config_unavailable_raises(self):
        module = self.module()
        with mock.patch.object(cpq_kb, "snapshot",
                               side_effect=cpq_kb.KbUnavailable("probe")):
            with self.assertRaises(module.CaseLibraryUnavailable):
                module.load_config(None)

    def test_c4_reviewed_authoritative_case_is_eligible(self):
        module = self.module()
        result = module.quote_eligibility(base_case(), today=TODAY)
        self.assertTrue(result["eligible"])
        self.assertEqual("ok", result["reason_code"])
        self.assertFalse(result["expired"])
        self.assertIsInstance(result["reason"], str)
        self.assertTrue(result["reason"])

    def test_c5_reason_code_priority_is_deterministic(self):
        module = self.module()
        cases = [
            ("missing_fields", base_case(source_type="demo", review_status="draft",
                                        box_type_code="")),
            ("retired", base_case(source_type="demo", review_status="retired",
                                  box_type_code="")),
            ("industry_mismatch", base_case(industry="battery", source_type="demo")),
            ("source_not_authoritative", base_case(source_type="demo", review_status="draft")),
            ("not_reviewed", base_case(review_status="draft")),
            ("expired", base_case(quote_date="2020-01-01", valid_until="2020-06-01")),
        ]
        for expected, case in cases:
            got = module.quote_eligibility(case, today=TODAY)
            self.assertEqual(expected, got["reason_code"],
                             "reason_code 优先级错误：%s" % json.dumps(case, ensure_ascii=False))
            self.assertFalse(got["eligible"])
            self.assertTrue(got["reason"], "每个不通过都要有中文文案")

    def test_c6_explicit_valid_until_wins(self):
        module = self.module()
        row = base_case(valid_until="2027-01-01", quote_date="2020-01-01")
        got = module.quote_eligibility(row, today=TODAY)
        self.assertTrue(got["eligible"], "显式 valid_until 优先于按来源推算")
        self.assertGreater(got["expires_in_days"], 0)

    def test_c7_valid_days_derived_from_source_and_demo_expires_same_day(self):
        module = self.module()
        wb = module.quote_eligibility(base_case(source_type="workbook",
                                                quote_date="2026-09-01", valid_until=""),
                                      today=TODAY)
        self.assertEqual(345, wb["expires_in_days"],
                         "workbook 案例有效期 = quote_date + 365 天（Spec §2.3）")
        self.assertTrue(wb["expiring_soon"] is False)
        demo = module.quote_eligibility(base_case(source_type="demo", quote_date="2026-09-21",
                                                  valid_until=""), today=TODAY)
        self.assertEqual("source_not_authoritative", demo["reason_code"])

    def test_c8_expiring_soon_window(self):
        module = self.module()
        got = module.quote_eligibility(base_case(valid_until="2026-10-10"), today=TODAY)
        self.assertTrue(got["eligible"])
        self.assertTrue(got["expiring_soon"], "距过期 ≤ expiry_warn_days 必须给「即将过期」提示")
        self.assertEqual(19, got["expires_in_days"])

    def test_c9_quick_quote_cases_filters_ineligible(self):
        module = self.module()
        cases = [base_case(case_code="QQ-OK"),
                 base_case(case_code="QQ-DRAFT", review_status="draft"),
                 base_case(case_code="QQ-DEMO", source_type="demo"),
                 base_case(case_code="QQ-OLD", valid_until="2025-01-01")]
        eligible = [row["case_code"] for row in
                    module.quick_quote_cases(cases=cases, today=TODAY)]
        self.assertEqual(["QQ-OK"], eligible)
        all_codes = [row["case_code"] for row in
                     module.load_cases(cases=cases, today=TODAY)]
        self.assertEqual({"QQ-OK", "QQ-DRAFT", "QQ-DEMO", "QQ-OLD"}, set(all_codes),
                         "load_cases() 默认含过期/未审核（详情页要能打开）")

    def test_c10_load_cases_injected_rows_are_not_mutated(self):
        module = self.module()
        cases = [base_case()]
        snapshot = copy.deepcopy(cases)
        module.load_cases(cases=cases, today=TODAY)
        self.assertEqual(snapshot, cases, "load_cases() 不得改注入的原始行")

    def test_c11_find_case_returns_empty_for_unknown_code(self):
        module = self.module()
        self.assertEqual({}, module.find_case("NOPE", cases=[base_case()]))
        found = module.find_case("QQ-2026-0001", cases=[base_case()])
        self.assertEqual("YT-RB-01001-A", found["box_type_code"])

    def test_c12_load_cases_unavailable_raises_instead_of_empty(self):
        module = self.module()
        with mock.patch.object(cpq_kb, "snapshot",
                               side_effect=cpq_kb.KbUnavailable("probe")):
            with self.assertRaises(module.CaseLibraryUnavailable):
                module.load_cases(None)

    def test_c13_build_case_from_quote_defaults_to_draft(self):
        module = self.module()
        quote = {
            "engine_version": "packaging_quote_v1", "industry": "packaging",
            "quote_quantity": 1000, "cost_total": 60.049408129741252,
            "untaxed_unit_price": 80.06587750632167, "currency": "CNY",
            "tax_included": False, "created_at": "2026-09-01 10:00:00",
            "inputs": {"box_type": "YT-RB-01001-A", "inner_length": 200,
                       "inner_width": 150, "inner_height": 80, "closure_type": "磁吸",
                       "face_paper_gsm": 200, "grey_board_gsm": 1200, "v_groove": "是",
                       "magnet": "有", "lamination": "是", "hot_stamping": "有",
                       "insert_type": "EVA内托", "print_colors": "CMYK"},
        }
        case = module.build_case_from_quote(quote, case_code="QQ-NEW-0001")
        self.assertEqual("draft", case["review_status"],
                         "从报价沉淀的案例默认必须是 draft（Spec §2.4）")
        self.assertEqual(1, int(case["version"]))
        self.assertEqual("YT-RB-01001-A", case["box_type_code"])
        self.assertIn("unmapped", case, "映射不到的字段要进 unmapped，不猜")
        got = module.quote_eligibility(case, today=dt.date(2026, 9, 21))
        self.assertEqual("not_reviewed", got["reason_code"])

    def test_c14_save_case_requires_user(self):
        module = self.module()
        with self.assertRaises(module.QuickQuoteCaseError):
            module.save_case(base_case(), user=None)


# --------------------------------------------------------------------------- #
# D 组：快速报价不进入技术工艺
# --------------------------------------------------------------------------- #
class TestDStaysInQuoteSide(Base):
    def test_d1_module_does_not_import_tech_pipeline(self):
        module = self.module()
        source = CASE_PY.read_text(encoding="utf-8")
        self.assertNotIn("tech_app", source,
                         "报价侧快速报价模块不得 import 技术工艺（Spec §2.1）")
        for banned in tuple(self.meta(module, "TECH_PIPELINE_MODULES")):
            self.assertNotIn(banned, source,
                             "快速报价链路不得触及技术工艺模块：%s" % banned)

    def test_d2_quote_side_files_have_no_dwg_converter_direct_call(self):
        targets = [CASE_PY, SERVER_PY]
        for path in targets:
            self.assertTrue(path.exists(), "%s 不存在（Spec 批 1 §2.1）" % path.name)
        for path in targets:
            source = path.read_text(encoding="utf-8")
            for banned in ("dwg2dxf", "dwg2SVG", "AppRun", "ODAFileConverter", "libredwg"):
                self.assertNotIn(banned, source,
                                 "%s 不得直连 DWG 转换器（转换只走统一解析服务）" % path.name)

    def test_d3_server_route_is_isolated_from_tech_handlers(self):
        module = self.module()
        path = self.meta(module, "QUICK_QUOTE_CASES_PATH")
        source = SERVER_PY.read_text(encoding="utf-8")
        self.assertIn(path, source, "cpq_agent_server.py 必须注册 %s 路由" % path)
        self.assertIn("_handle_quick_quote_cases", source,
                      "路由要有独立处理函数 _handle_quick_quote_cases()")
        handler = source.split("def _handle_quick_quote_cases", 1)[1].split("\ndef ", 1)[0]
        for banned in ("_handle_step1_match", "cpq_tech_bridge", "_handle_markup_fill"):
            self.assertNotIn(banned, handler,
                             "快速报价案例路由不得转调技术工艺/既有加价链路：%s" % banned)

    def test_d4_quick_steps_do_not_include_tech_handoff(self):
        module = self.module()
        self.assertNotIn("handoff", " ".join(self.meta(module, "QUICK_QUOTE_STEPS")))
        source = CASE_PY.read_text(encoding="utf-8")
        self.assertNotIn("send_to_quote", source)
        self.assertNotIn("handoff_kind", source)


# --------------------------------------------------------------------------- #
# E 组：首页入口与前端模块
# --------------------------------------------------------------------------- #
class TestEHomeEntryAndPanel(Base):
    def test_e1_home_has_both_quote_mode_entries(self):
        html = HOME_HTML.read_text(encoding="utf-8")
        self.assertIn('data-quote-mode="precise"', html, "首页缺「精准报价」入口（Spec §2.6）")
        self.assertIn('data-quote-mode="quick"', html, "首页缺「快速报价」入口（Spec §2.6）")
        self.assertIn("快速报价", html)
        self.assertIn("精准报价", html)

    def test_e2_quick_entry_is_packaging_only(self):
        html = HOME_HTML.read_text(encoding="utf-8")
        window = re.search(r'data-quote-mode="quick".{0,900}', html, re.S)
        self.assertIsNotNone(window, "找不到快速报价入口")
        snippet = window.group(0)
        self.assertTrue(("packaging" in snippet) or ("包装" in snippet),
                        "快速报价入口必须挂在包装行业下（Spec §2.6）")

    def test_e3_quick_quote_panel_module_exists(self):
        self.assertTrue(PANEL_JS.exists(),
                        "tech_app/frontend/quick-quote-panel.js 不存在（Spec §2.6）")
        source = PANEL_JS.read_text(encoding="utf-8")
        self.assertIn("window.QuickQuotePanel", source)
        self.assertIn('"precise"', source)
        self.assertIn('"quick"', source)

    def test_e4_panel_never_calls_tech_pipeline(self):
        self.assertTrue(PANEL_JS.exists(),
                        "tech_app/frontend/quick-quote-panel.js 不存在（Spec §2.6）")
        source = PANEL_JS.read_text(encoding="utf-8")
        for banned in ("/api/projects/", "/api/tech/", "packaging_handoff", "cad_converter"):
            self.assertNotIn(banned, source,
                             "快速报价前端不得引用技术工艺链路：%s" % banned)

    def test_e5_panel_is_loaded_by_home(self):
        html = HOME_HTML.read_text(encoding="utf-8")
        self.assertIn("quick-quote-panel.js", html,
                      "首页必须加载 quick-quote-panel.js（Spec §2.6）")


# --------------------------------------------------------------------------- #
# F 组：非回归
# --------------------------------------------------------------------------- #
class TestFNonRegression(Base):
    def test_f1_existing_quote_steps_unchanged(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        block = re.search(r"^STEPS\s*=\s*\[(.*?)^\]", source, re.S | re.M)
        self.assertIsNotNone(block, "cpq_agent_server.STEPS 结构变了，需人工核对")
        names = re.findall(r'^\s*"([^"]+)"', block.group(1), re.M)
        self.assertEqual(["确认需求配置", "工艺确认", "定价-利润加成", "报价-其他加价项",
                          "报价方案", "输出报价单"], names,
                         "既有 6 步报价流程不得被快速报价改动（Spec §4）")

    def test_f2_kb_source_types_untouched(self):
        self.assertEqual(("demo", "workbook", "dwg_confirmed", "unknown"),
                         tuple(cpq_kb.SOURCE_TYPES))

    def test_f3_packaging_quote_engine_untouched(self):
        source = (ROOT / "cpq_packaging_quote.py").read_text(encoding="utf-8")
        self.assertIn('ENGINE_VERSION = "packaging_quote_v1"', source)
        self.assertNotIn("quick_quote", source,
                         "本批不得把快速报价塞进既有包装定价模块（Spec §4）")

    def test_f4_case_model_helpers_stay_in_this_module(self):
        module = self.module()
        for helper in ("quote_eligibility", "case_missing_fields", "CASE_FIELDS",
                       "QUICK_QUOTE_ALLOWED_SOURCES"):
            self.assertTrue(hasattr(module, helper),
                            "案例模型与准入判定只住在 %s（批 2 起复用，不得重写）" % MODULE_NAME)


if __name__ == "__main__":
    unittest.main(verbosity=2)
