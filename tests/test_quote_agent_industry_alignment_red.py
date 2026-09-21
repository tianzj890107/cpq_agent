"""红测：报价助手行业化（需求门禁 + 产品技术参数）—— 包装对齐第 1 批。

Spec：`docs/specs/quote-agent-industry-alignment.md`

现状缺口（实测，不是推断）：

  · `cpq_agent_server.py:866` 把需求门禁硬编码成半导体口径 ——
    `_STEP1_REQUIRED = (("max_dimension","尺寸"),
                        ("application_scope","应用范围/使用场景"),
                        ("operating_temperature","工作温度"))`，
    `_step1_missing()` 只认这三项、**不接受行业参数**，两处调用点
    （`cpq_agent_server.py:790` 的 `match_products` 工具、`:1041` 的 `/api/step1/match`
    `phase="intent"`）都把「缺工作温度」当成「需求不齐」。
    后果：包装需求（数码天地盒 30*30*20 + 铜版纸/亮膜/哑膜/灰板 2.5mm）信息已经齐了，
    却被判「⚠ 需求信息不齐…缺少：工作温度」，链路停在意图识别。

  · `cpq_agent_server.py:1429` 把 ④产品技术参数（`s1_techparams`）的事实源钉死为
    `clm_calc_product_tech` —— 《亿纬锂能DA梳理.xlsx》里的电池/光伏/储能成品参数表。
    包装选出的盒型落不进这张表。

  · `grep -c industry cpq_agent_server.py` → `0`：报价助手**完全没有行业概念**，
    `cpq_industries` / `industry_templates.PACKAGING_SPEC` 在报价侧从未被读取。

分组（Spec §7）：A 门禁行业化（11）/ B 产品技术参数（11）/ C 前后端同源（6）/ D 不回归（5）。

纪律：
  · 全部离线、确定性、无网络：不连 Postgres、不调模型、不起服务；
  · 只读源码与纯函数，不写 `tech_data`、不改服务器配置；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import inspect
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERVER_PY = ROOT / "cpq_agent_server.py"
QUOTE_HTML = ROOT / "确认需求解析结果.html"
TECH_APP = ROOT / "tech_app" / "apps" / "tech-process" / "index.html"
REQ_JS = ROOT / "tech_app" / "frontend" / "requirement-create.js"
INDUSTRY_PY = ROOT / "cpq_industries.py"

from tech_app.backend.services import industry_templates, product_params  # noqa: E402

SERVER_NAME = "cpq_agent_server"

#: 半导体/电池口径的三项 —— 它们出现在**包装**必填里就是本批要修的病（Spec §1.1）。
NON_PACKAGING_REQUIRED = ("max_dimension", "application_scope", "operating_temperature")

#: 半导体口径必须保持逐字不变（Spec §3.1 第 2 条）。
SEMICONDUCTOR_REQUIRED = (("max_dimension", "尺寸"),
                          ("application_scope", "应用范围/使用场景"),
                          ("operating_temperature", "工作温度"))

#: 电池专有键 —— 包装的技术参数里出现任何一个都说明没换源（Spec §4.1）。
BATTERY_ONLY_KEYS = ("cell_type", "cell_model", "nominal_voltage", "capacity",
                     "energy_density", "cathode_material", "anode_material",
                     "pv_module", "pv_cell", "inverter", "bms", "module_type")

#: 盒型库技术参数（`kb_packaging_box_type`，Spec §4.1 至少覆盖这些键）。
BOX_LIBRARY_KEYS = ("box_type_code", "name", "family", "size_l_min", "size_l_max",
                    "size_w_min", "size_w_max", "size_h_min", "size_h_max",
                    "fit_clearance", "grey_board_thickness", "face_paper_gsm",
                    "closure_type", "part_count", "v_groove", "hand_mount_ratio",
                    "standard_seconds", "automation_level")

#: Spec §1.1 的问题一需求原文（数码天地盒）。
TOWER_BOX_TEXT = ("数码天地盒  尺寸（mm）30*30*20；盖面纸 铜版纸，亮膜；"
                  "底面纸 铜版纸，哑膜；盖板材 灰板，厚度2.5mm")


def _server_source() -> str:
    return SERVER_PY.read_text(encoding="utf-8", errors="replace")


class QuoteAgentIndustryCase(unittest.TestCase):
    """公共装载器：模块缺失一律 fail（不许 ERROR、不许静默 skip）。"""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls.server = importlib.import_module(SERVER_NAME)
        except Exception as exc:                       # noqa: BLE001 - 红测要原文
            raise AssertionError("无法导入 %s：%s: %s" % (SERVER_NAME, type(exc).__name__, exc))

    def api(self, name):
        fn = getattr(self.server, name, None)
        self.assertIsNotNone(
            fn, "cpq_agent_server 缺少 %s（Spec §3.1/§4.1）：报价助手还没有行业化的公开口径"
            % name)
        return fn

    def nonempty(self, value, label):
        if callable(value):
            value = value()
        self.assertTrue(str(value or "").strip(), "缺少 %s（Spec §1.1）" % label)
        return value


# --------------------------------------------------------------------------- #
# A. 需求完整性门禁行业化（Spec §3）
# --------------------------------------------------------------------------- #
class AIndustryGate(QuoteAgentIndustryCase):
    def test_a1_step1_required_is_a_callable_api(self):
        fn = self.api("step1_required")
        self.assertTrue(callable(fn), "step1_required 必须是可调用接口（Spec §3.1）")
        hint = self.nonempty(inspect.getdoc(fn), "step1_required 的说明")
        self.assertIn("行业", str(hint), "step1_required 必须写明「按行业取必填项」：%r" % hint)

    def test_a2_semiconductor_requirement_list_is_unchanged(self):
        got = tuple(tuple(item) for item in self.api("step1_required")("semiconductor"))
        self.assertEqual(got, SEMICONDUCTOR_REQUIRED,
                         "半导体门禁必须逐字不变（Spec §3.1）：%r" % (got,))

    def test_a3_packaging_required_keys_come_from_the_industry_template(self):
        got = {str(key) for key, _label in self.api("step1_required")("packaging")}
        expected = set(industry_templates.required_keys("packaging"))
        self.assertEqual(got, expected,
                         "包装必填项必须恰好等于 industry_templates.required_keys('packaging')"
                         "（Spec §3.1）：多 %r 少 %r"
                         % (sorted(got - expected), sorted(expected - got)))
        self.assertEqual(len(expected), 10, "包装必填项应为 10 个：%r" % sorted(expected))

    def test_a4_packaging_required_never_contains_non_packaging_keys(self):
        got = {str(key) for key, _label in self.api("step1_required")("packaging")}
        bad = sorted(got & set(NON_PACKAGING_REQUIRED))
        self.assertEqual(bad, [],
                         "包装必填项不许出现半导体口径的键（Spec §1.1/§3.1）：%r" % bad)

    def test_a5_labels_match_the_industry_template_verbatim(self):
        # 三项报价门禁键不在需求模板里（semiconductor 的 required_keys 是另外 14 个键），
        # 所以只对"模板里确实有的键"要求标签逐字一致；包装必须全部命中模板标签。
        self.assertTrue(all(key in industry_templates.labels("packaging")
                            for key in industry_templates.required_keys("packaging")),
                        "包装必填项必须都能在行业模板里取到中文标签（Spec §3.1）")
        for industry in ("semiconductor", "packaging", "battery", "appliance"):
            labels = industry_templates.labels(industry)
            for key, label in self.api("step1_required")(industry):
                if str(key) not in labels:
                    continue
                self.assertEqual(str(label), str(labels[str(key)]),
                                 "%s/%s 的标签必须与行业模板逐字一致（Spec §3.1）：%r != %r"
                                 % (industry, key, label, labels[str(key)]))
        for key, label in self.api("step1_required")("packaging"):
            self.assertIn(str(key), industry_templates.labels("packaging"),
                          "包装门禁项 %r 必须来自行业模板（Spec §3.1）" % key)
            self.assertNotEqual(str(label), str(key),
                                "包装门禁项 %r 的标签必须是中文，不许回退成原始 key（Spec §3.1）" % key)

    def test_a6_required_list_is_not_a_single_hardcoded_triple(self):
        source = _server_source()
        self.assertNotRegex(
            source, r"(?m)^_STEP1_REQUIRED\s*(?::[^=]*)?=\s*\(\s*\(",
            "不许再把门禁写成模块级单一三元组 `_STEP1_REQUIRED = ((…), …)`（Spec §3.1）；"
            "必填项必须按行业从 industry_templates 派生")
        names = [name for name in dir(self.server) if "STEP1_REQUIRED" in name.upper()]
        self.assertTrue(names, "应保留 STEP1_REQUIRED_BY_INDUSTRY 这类按行业的常量（Spec §3.1）")

    def test_a7_step1_missing_accepts_an_industry(self):
        signature = inspect.signature(self.api("step1_missing"))
        self.assertIn("industry", signature.parameters,
                      "step1_missing 必须接受 industry（Spec §3.1）：%r" % signature)
        parameter = signature.parameters["industry"]
        self.assertIsNot(parameter.default, inspect.Parameter.empty,
                         "industry 必须有默认值，不许把既有调用点全打断（Spec §3.1）")

    def test_a8_packaging_missing_never_reports_operating_temperature(self):
        got = [str(item) for item in
               self.api("step1_missing")({"max_dimension": "30*30*20"}, industry="packaging")]
        self.assertNotIn("工作温度", got,
                         "包装行业不许再报「工作温度」缺失（Spec §1.1/§3.3）：%r" % got)
        labels = industry_templates.labels("packaging")
        for item in got:
            self.assertIn(item, set(labels.values()),
                          "缺失项必须是包装模板的中文标签（Spec §3.1）：%r" % item)

    def test_a9_tower_box_requirement_is_not_blocked(self):
        req = {"packaging_product_name": "数码天地盒",
               "packaging_category": "数码", "quote_quantity": "1000",
               "inner_length": "30", "inner_width": "30", "inner_height": "20",
               "box_type": "天地盖", "closure_type": "天地盖", "v_groove": "是",
               "face_paper_gsm": "250", "face_paper": "铜版纸",
               "grey_board_thickness": "2.5mm", "lamination": "亮膜/哑膜"}
        got = [str(item) for item in
               self.api("step1_missing")(req, industry="packaging")]
        self.assertEqual(got, [],
                         "信息齐全的数码天地盒需求不许再被判「需求信息不齐」（Spec §3.3）：%r"
                         % got)

    def test_a10_semiconductor_gate_still_blocks(self):
        got = [str(item) for item in
               self.api("step1_missing")({"max_dimension": "13*20"},
                                         industry="semiconductor")]
        self.assertIn("应用范围/使用场景", got,
                      "半导体门禁不许被一起放开（Spec §3.1）：%r" % got)
        self.assertIn("工作温度", got, "半导体门禁不许被一起放开（Spec §3.1）：%r" % got)

    def test_a11_unknown_industry_falls_back_without_raising(self):
        for value in ("", None, "flexible", "不存在行业"):
            try:
                got = self.api("step1_required")(value)
            except Exception as exc:                   # noqa: BLE001
                self.fail("行业 %r 不许让 step1_required 抛异常（Spec §3.1）：%s: %s"
                          % (value, type(exc).__name__, exc))
            self.assertEqual(tuple(tuple(item) for item in got), SEMICONDUCTOR_REQUIRED,
                             "未知/空行业必须落回默认行业（Spec §3.1）：%r → %r" % (value, got))
            missing = self.api("step1_missing")({}, industry=value)
            self.assertTrue(list(missing), "默认行业下空需求必须仍被拦（Spec §3.1）")


# --------------------------------------------------------------------------- #
# B. ④产品技术参数行业化（Spec §4）
# --------------------------------------------------------------------------- #
class BProductTechParams(QuoteAgentIndustryCase):
    def columns(self, industry):
        rows = self.api("tech_param_columns")(industry)
        self.assertIsInstance(rows, list, "tech_param_columns 必须返回列表（Spec §4.1）")
        self.assertTrue(rows, "%s 的技术参数列不许为空（Spec §4.1）" % industry)
        keys = []
        for row in rows:
            self.assertTrue({"key", "label"} <= set(row),
                            "列定义必须含 key/label（Spec §4.2）：%r" % (row,))
            keys.append(str(row["key"]))
        return keys, rows

    def test_b12_semiconductor_source_is_unchanged(self):
        self.assertEqual(self.api("tech_param_source")("semiconductor"),
                         "clm_calc_product_tech",
                         "半导体 ④产品技术参数 的事实源不许改（Spec §4.1）")

    def test_b13_packaging_source_is_the_box_library(self):
        self.assertEqual(self.api("tech_param_source")("packaging"), "kb_packaging_box_type",
                         "包装 ④产品技术参数 必须换成盒型库（Spec §4.1/§1.2）")

    def test_b14_packaging_columns_cover_the_box_library(self):
        keys, _rows = self.columns("packaging")
        missing = [key for key in BOX_LIBRARY_KEYS if key not in keys]
        self.assertEqual(missing, [],
                         "包装技术参数缺少盒型库字段（Spec §4.1）：%r" % missing)

    def test_b15_packaging_columns_have_no_battery_only_keys(self):
        keys, _rows = self.columns("packaging")
        bad = sorted(set(keys) & set(BATTERY_ONLY_KEYS))
        self.assertEqual(bad, [],
                         "包装技术参数不许混进电池/光伏专有键（Spec §4.1/§4.2）：%r" % bad)

    def test_b16_packaging_labels_are_chinese(self):
        _keys, rows = self.columns("packaging")
        for row in rows:
            label = str(row["label"] or "")
            self.assertTrue(re.search(r"[\u4e00-\u9fff]", label),
                            "包装技术参数标签必须中文（Spec §4.2）：%r" % (row,))

    def test_b17_semiconductor_columns_still_come_from_the_da_spec(self):
        keys, _rows = self.columns("semiconductor")
        known = {str(field.get("code")) for field in product_params.spec().get("fields", [])}
        unknown = [key for key in keys if key not in known]
        self.assertEqual(unknown, [],
                         "半导体技术参数列必须仍来自 clm_calc_product_tech 字典（Spec §4.1）：%r"
                         % unknown)

    def test_b18_packaging_row_is_empty_without_a_selected_box(self):
        for product in (None, {}, {"name": "天地盖"}, {"box_type_code": ""}):
            got = self.api("tech_param_row")("packaging", product)
            self.assertEqual(dict(got or {}), {},
                             "没选到盒型时不许编造技术参数（Spec §2.4）：%r → %r"
                             % (product, got))

    def test_b19_packaging_row_follows_the_box_record(self):
        record = {"box_type_code": "YT-RB-01001-A", "name": "天地盖盒（全盖）",
                  "family": "01天地盖", "closure_type": "天地盖", "v_groove": "是",
                  "grey_board_thickness": "2.5", "face_paper_gsm": "250",
                  "part_count": 3, "fit_clearance": "0.5"}
        got = dict(self.api("tech_param_row")("packaging", record) or {})
        self.assertEqual(str(got.get("box_type_code")), "YT-RB-01001-A",
                         "必须原样带出盒型编码（Spec §4.1）：%r" % got)
        for key in ("name", "closure_type", "v_groove", "grey_board_thickness"):
            self.assertEqual(str(got.get(key)), str(record[key]),
                             "盒型库已有值必须原样带出（Spec §4.1）：%r" % {key: got.get(key)})
        declared = {str(row["key"]) for row in self.api("tech_param_columns")("packaging")}
        self.assertLessEqual(set(got), declared,
                             "生成的行不许出现列定义之外的键（Spec §4.2）：%r"
                             % sorted(set(got) - declared))

    def test_b20_product_sources_are_industry_aware(self):
        table = getattr(self.server, "_PRODUCT_SOURCES", None)
        self.assertIsInstance(table, dict,
                              "应保留 _PRODUCT_SOURCES，并让它能按行业取源（Spec §4.1）")
        source = self.api("tech_param_source")("packaging")
        self.assertNotEqual(source, "clm_calc_product_tech",
                            "包装不许再映射到电池表（Spec §1.2/§4.1）")
        self.assertIn(source, str(table),
                      "包装的事实源必须出现在 _PRODUCT_SOURCES 里（Spec §4.1）")

    def test_b21_unknown_industry_columns_do_not_raise(self):
        for value in ("", None, "不存在行业"):
            try:
                keys, _rows = self.columns(value)
            except Exception as exc:                   # noqa: BLE001
                self.fail("行业 %r 不许让 tech_param_columns 抛异常（Spec §4.1）：%s: %s"
                          % (value, type(exc).__name__, exc))
            self.assertTrue(keys, "未知行业必须落回默认行业的列（Spec §4.1）")

    def test_b22_server_no_longer_pins_techparams_to_the_battery_table(self):
        source = _server_source()
        for token in ("clm_calc_product_tech", "kb_packaging_box_type", "techparams_columns"):
            self.assertIn(token, source,
                          "报价助手源码缺少 %r（Spec §4.1/§4.2）：④产品技术参数必须按行业换源，"
                          "并把列定义 via techparams_columns 下发" % token)
        self.assertNotIn("'s1_techparams': 'clm_calc_product_tech'", source,
                         "s1_techparams 不许再被钉死成单一电池表（Spec §4.1）")


# --------------------------------------------------------------------------- #
# C. 报价侧与工艺侧字段同源（Spec §5）
# --------------------------------------------------------------------------- #
class CFrontendParity(QuoteAgentIndustryCase):
    def test_c23_quote_frontend_does_not_hardcode_battery_columns(self):
        source = QUOTE_HTML.read_text(encoding="utf-8", errors="replace")
        for token in ("clm_calc_product_tech", "电芯类型", "能量密度", "标称电压"):
            self.assertNotIn(token, source,
                             "报价助手前端不许硬编码电池口径（Spec §5）：%r" % token)

    def test_c24_quote_frontend_uses_server_delivered_columns(self):
        source = QUOTE_HTML.read_text(encoding="utf-8", errors="replace")
        self.assertIn("techparams_columns", source,
                      "报价助手前端的 ④产品技术参数 表头必须用后端下发的 techparams_columns"
                      "（Spec §4.2/§5）")

    def test_c25_tech_process_frontend_has_no_second_packaging_field_list(self):
        source = TECH_APP.read_text(encoding="utf-8", errors="replace")
        self.assertNotRegex(source, r"inner_length|face_paper_gsm|grey_board_thickness|v_groove",
                            "工艺侧前端不许再抄一份包装字段清单（Spec §5）")

    def test_c26_industry_keys_have_one_source(self):
        registry = INDUSTRY_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("INDUSTRY_KEYS", registry, "行业清单唯一来源仍是 cpq_industries（Spec §5）")
        for path in (QUOTE_HTML, TECH_APP, REQ_JS):
            source = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotRegex(
                source, r"\[\s*['\"]semiconductor['\"]\s*,\s*['\"]battery['\"]",
                "%s 不许另写行业数组（Spec §5）" % path.name)

    def test_c27_requirement_create_packaging_specs_match_backend(self):
        source = REQ_JS.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"RC_PACKAGING_SPECS\s*=\s*(\{.*?\n\})", source, re.S)
        self.assertIsNotNone(match, "requirement-create.js 必须有 RC_PACKAGING_SPECS（Spec §5）")
        keys = set(re.findall(r"\[\s*'[^']*'\s*,\s*'([a-z0-9_]+)'", match.group(1)))
        backend = set(industry_templates.field_keys("packaging"))
        self.assertTrue(keys, "RC_PACKAGING_SPECS 解析不到字段（Spec §5）")
        self.assertFalse(keys - backend,
                         "前端包装字段必须在后端模板里存在（Spec §5）：%r"
                         % sorted(keys - backend)[:10])

    def test_c28_frontend_never_marks_temperature_required_for_packaging(self):
        source = QUOTE_HTML.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn(
            "尺寸、应用范围/使用场景、工作温度", source,
            "报价助手前端不许再把「尺寸、应用范围/使用场景、工作温度」当成通用必填文案"
            "（Spec §1.1/§3.3/§5）：缺失项必须由后端按行业返回")
        self.assertRegex(source, r"missing",
                         "前端仍须用后端返回的 missing 列表拼装提示（Spec §3.3）")


# --------------------------------------------------------------------------- #
# D. 不回归（Spec §9）
# --------------------------------------------------------------------------- #
class DNoRegression(QuoteAgentIndustryCase):
    def test_d29_packaging_template_is_unchanged(self):
        self.assertEqual(len(industry_templates.field_keys("packaging")), 64,
                         "包装模板仍是 64 字段（Spec §9）")
        self.assertEqual(len(industry_templates.required_keys("packaging")), 10,
                         "包装必填仍是 10 项（Spec §9）")

    def test_d30_industry_registry_is_unchanged(self):
        import cpq_industries
        self.assertEqual(tuple(cpq_industries.INDUSTRY_KEYS),
                         ("semiconductor", "battery", "appliance", "packaging"),
                         "行业注册表不变（Spec §9）")

    def test_d31_semiconductor_sections_are_unchanged(self):
        keys = industry_templates.field_keys("semiconductor")
        for key in ("wafer_size", "chuck_type", "temperature_range", "max_voltage"):
            self.assertIn(key, keys, "半导体字段清单不许被本批改动（Spec §9）：%r" % key)

    def test_d32_packaging_quote_engine_still_imports(self):
        module = importlib.import_module("cpq_packaging_quote")
        self.assertTrue(callable(getattr(module, "price", None)),
                        "包装报价引擎仍须可导入且 price() 可用（Spec §6）")

    def test_d33_industry_normalization_is_stable(self):
        for key in ("semiconductor", "packaging", "battery", "appliance"):
            self.assertEqual(industry_templates.normalize(key), key,
                             "合法行业键必须原样返回（Spec §9）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
