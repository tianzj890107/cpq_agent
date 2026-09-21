"""红测：包装专属参数族与技术侧包装闭环收口。

Spec：`docs/specs/packaging-tech-param-bom-route-cost-closure.md`（新五批 · 第 4 批）。

现场缺口（服务器实测 + 本地只读实测）：

  · 服务器实测：酒盒项目在技术侧 **3.2 参数推荐**被判成「产品族：其他成品」，随后要求填写
    产品系列 / 产品型号 / 重量 / 工作温度 / 机械号 —— 那是锂电产品的字段，不是包装的。
  · 本地取证（实跑）：`product_params.family_keys()` 只有
    `['li_primary','li_ion_pack','ess','pv_module','other']`，**没有包装族**；
    `fields_for("other")` 的 14 项与 7 个必填（product_series / product_model /
    product_item_code / product_item_name / max_dimension / weight / operating_temperature）
    与现场看到的字段逐项对应。
  · `as_prompt/align/checklist/missing_required` 都已支持 `family=`，但四个调用点全部按默认走：
    `integration.py:228/240`、`main.py:3083/3175`、`cost_review.py:252` —— 族由模型自由决定，
    写「包装盒」还会被 `resolve_family` 静默退回 `other`。
  · 包装字段（`industry_templates.PACKAGING_SPEC`，64 键 / 10 必填）目前只被 1.1 表单、
    需求抽取、需求单 PDF、完整性预检使用，3.2 与 4.3 都不用它。
  · 包装链路（盒型匹配 → 参数化 BOM → 工艺路线 → 成本）已经建好且既有红测全绿，
    本批只为它对齐 **参数来源**：`packaging_bom.INNER_DIM_KEYS` 要的
    `inner_length/inner_width/inner_height` 正是包装字段，而 3.2 现在问的是 `weight`。

验证方式：

  · 纯函数行为：`family_for_industry` / `packaging_family_fields` / `resolve_family`。
  · AST / 源码契约：四个调用点是否真的把族传下去、包装字段清单是否由
    `industry_templates.PACKAGING_SPEC` 派生（不许抄一份）。
  · 跨模块一致性：BOM 内尺寸键 ⊆ 包装族字段、包装族必填 == 盒型匹配必填、行业键单一来源。
  · 基线护栏：`other` 族 14 项、`industry_templates` 的 64/10、DA 五族的相对顺序都不许动。
  · 全部离线：不连 Postgres、不调模型、不起服务、不读写生产数据。
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import re
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC = ROOT / "docs" / "specs" / "packaging-tech-param-bom-route-cost-closure.md"
PP_PY = ROOT / "tech_app" / "backend" / "services" / "product_params.py"
INTEGRATION_PY = ROOT / "tech_app" / "backend" / "services" / "integration.py"
COST_REVIEW_PY = ROOT / "tech_app" / "backend" / "services" / "cost_review.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
DA_JSON = ROOT / "tech_app" / "agent_knowledge" / "rules" / "quote_product_params.json"

#: DA 快照里的五个产品族（相对顺序不许变，只允许在后面追加包装族）。
DA_FAMILIES = ("li_primary", "li_ion_pack", "ess", "pv_module", "other")

#: `other`（其他成品）族基线：本批一个字都不许改（实测 14 项）。
OTHER_BASELINE = ("product_series", "product_model", "product_item_code",
                  "product_item_name", "scheme_desc", "version_ext", "machine_model",
                  "max_dimension", "weight", "operating_temperature",
                  "storage_temperature", "application_scope", "transport_scheme",
                  "other_nonstd_markup")

#: 包装需求字段的规模（`industry_templates.PACKAGING_SPEC`，实测 64 键 / 10 必填）。
PACKAGING_FIELD_COUNT = 64
PACKAGING_REQUIRED = ("box_type", "closure_type", "face_paper_gsm", "inner_height",
                      "inner_length", "inner_width", "packaging_category",
                      "packaging_product_name", "quote_quantity", "v_groove")

#: 包装族里绝不允许出现的电池/通用成品字段（闭集断言）。
FOREIGN_CODES = ("cell_code", "cell_model", "reference_size", "rated_voltage",
                 "rated_capacity", "max_continuous_current", "max_pulse_current",
                 "plug_wire_model", "plug_direction", "wire_length", "is_wire_wound",
                 "machine_model", "weight", "operating_temperature",
                 "storage_temperature", "product_series", "product_model",
                 "product_item_code", "product_item_name")

PACKAGING_INDUSTRY = "packaging"


def setUpModule():
    if not SPEC.exists():
        raise AssertionError(f"缺少本批 Spec：{SPEC.relative_to(ROOT)}")
    for path in (PP_PY, INTEGRATION_PY, COST_REVIEW_PY, MAIN_PY, DA_JSON):
        if not path.exists():
            raise AssertionError(f"缺少文件：{path.relative_to(ROOT)}")


def must(condition, message: str):
    if not condition:
        raise AssertionError(message)


def read(path) -> str:
    p = pathlib.Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


_TREES: dict = {}


def tree(path):
    key = str(path)
    if key not in _TREES:
        _TREES[key] = ast.parse(read(path))
    return _TREES[key]


def func_node(path, name):
    for item in tree(path).body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
            return item
    return None


def func_source(path, name) -> str:
    node = func_node(path, name)
    if node is None or not getattr(node, "end_lineno", None):
        return ""
    return "\n".join(read(path).splitlines()[node.lineno - 1:node.end_lineno])


def param_names(node) -> set:
    if node is None:
        return set()
    args = node.args
    names = {a.arg for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def calls(node, func_name: str):
    out = []
    if node is None:
        return out
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            if name == func_name:
                out.append(sub)
    return out


def positional_count(call) -> int:
    return len([arg for arg in call.args if not isinstance(arg, ast.Starred)])


def has_family(call) -> bool:
    return any(k.arg == "family" for k in call.keywords)


def passes_family(call, *, minimum: int) -> bool:
    """这个调用是否把"族"当第 minimum 个位置参数（或 family= 关键字）传了进去。"""
    return positional_count(call) + (1 if has_family(call) else 0) >= minimum


def family_calls(path, func_name, callee, *, minimum):
    node = func_node(path, func_name)
    must(node is not None, f"{pathlib.Path(path).name} 里没有函数 {func_name}")
    return [call for call in calls(node, callee) if passes_family(call, minimum=minimum)]


def product_params():
    try:
        return importlib.import_module("tech_app.backend.services.product_params")
    except ImportError as exc:                              # pragma: no cover - 环境问题
        raise AssertionError(f"product_params 不能导入：{exc}") from exc


def industry_templates():
    return importlib.import_module("tech_app.backend.services.industry_templates")


def packaging_family() -> str:
    module = product_params()
    key = getattr(module, "PACKAGING_FAMILY", "")
    must(isinstance(key, str) and key.strip(),
         "product_params 必须定义 PACKAGING_FAMILY（包装族的 key，Spec §2）——"
         "现在只有 li_primary/li_ion_pack/ess/pv_module/other 五个族，包装项目无处可去，"
         "只能落进「其他成品」")
    return key.strip()


def family_for_industry(industry):
    module = product_params()
    fn = getattr(module, "family_for_industry", None)
    must(callable(fn),
         "product_params 必须定义 family_for_industry(industry)："
         "把项目行业映射到锁定的产品族，返回 None 表示沿用既有口径（Spec §2）")
    return fn(industry)


def packaging_codes() -> set:
    module = product_params()
    key = packaging_family()
    fields = module.fields_for(key)
    must(fields, f"fields_for({key!r}) 是空的：包装族字段清单必须由 "
                 "industry_templates.PACKAGING_SPEC 派生后注册进字典（Spec §2.4）")
    return {str(item.get("code") or "") for item in fields}


# ===========================================================================
# A. 包装族
# ===========================================================================
class PackagingFamilyTest(unittest.TestCase):
    def test_a1_family_and_mapper_exist(self):
        key = packaging_family()
        node = func_node(PP_PY, "family_for_industry")
        must(node is not None,
             "product_params.py 里没有 family_for_industry —— 行业到族的映射必须只有一个入口")
        self.assertTrue(param_names(node), "family_for_industry 必须接受行业参数")
        self.assertEqual(key, family_for_industry(PACKAGING_INDUSTRY))

    def test_a2_only_packaging_is_pinned(self):
        key = packaging_family()
        self.assertEqual(key, family_for_industry(PACKAGING_INDUSTRY))
        for other in ("semiconductor", "appliance", "battery", "", None, "unknown-industry"):
            self.assertIsNone(family_for_industry(other),
                              f"{other!r} 不该被锁族：本批只锁包装，其它行业行为必须逐字不变")
        self.assertEqual(key, family_for_industry(PACKAGING_INDUSTRY),
                         "同输入必须同输出（纯函数）")
        source = read(PP_PY)
        self.assertEqual([], re.findall(r"^\s*(?:from|import)\s+\S*\b(?:psycopg|store|sqlite3)\b",
                                        source, re.M),
                         "族解析必须是纯函数：不读库")

    def test_a3_family_is_registered(self):
        key = packaging_family()
        module = product_params()
        self.assertIn(key, module.family_keys(),
                      f"包装族 {key!r} 没有注册进字典（families()/family_keys()）")
        names = {item["key"]: item.get("name") for item in module.families()}
        self.assertTrue(str(names.get(key) or "").strip(), "包装族必须有展示名")

    def test_a4_field_codes_match_the_requirement_template(self):
        expected = set(industry_templates().field_keys(PACKAGING_INDUSTRY))
        self.assertEqual(PACKAGING_FIELD_COUNT, len(expected),
                         "护栏：industry_templates 的包装字段数变了，先确认是不是误改需求模板")
        self.assertEqual(expected, packaging_codes(),
                         "包装族的字段清单必须与 industry_templates.PACKAGING_SPEC 完全一致"
                         "（64 键），不许在参数模块里另立一份")

    def test_a5_name_and_required_come_from_the_template(self):
        templates = industry_templates()
        expected = {}
        for block in templates.PACKAGING_SPEC:
            for field in block.fields:
                expected[field.key] = (field.label, bool(field.required))
        got = {item["code"]: (str(item.get("name") or ""), bool(item.get("required")))
               for item in product_params().fields_for(packaging_family())}
        self.assertEqual(expected, got,
                         "包装族的 name/required 必须逐条取自 SpecField.label / SpecField.required")

    def test_a6_no_battery_fields_in_the_packaging_family(self):
        leaked = sorted(set(FOREIGN_CODES) & packaging_codes())
        self.assertEqual([], leaked,
                         f"包装族里出现了电池/通用成品字段 {leaked} —— 现场就是这样把酒盒"
                         "当成「其他成品」的（工作温度 / 机械号 / 产品系列）")


# ===========================================================================
# B. 单一来源
# ===========================================================================
class SingleSourceTest(unittest.TestCase):
    def test_b1_field_list_is_derived_not_copied(self):
        source = read(PP_PY)
        self.assertIn("industry_templates", source,
                      "product_params 必须从 industry_templates 派生包装字段（Spec §2.3），"
                      "不许抄一份字段清单")
        leaked = sorted(code for code in industry_templates().field_keys(PACKAGING_INDUSTRY)
                        if re.search(r"[\"']" + re.escape(code) + r"[\"']", source))
        self.assertEqual([], leaked,
                         f"包装字段清单不许在 product_params.py 里字面量抄写：{leaked}")

    def test_b2_prompt_really_carries_the_packaging_fields(self):
        module = product_params()
        key = packaging_family()
        prompt = module.as_prompt(key)
        must(prompt, f"as_prompt({key!r}) 返回空 —— 3.2 拿不到任何字典")
        self.assertIn(key, prompt, "提示词里必须写明族的 key，模型才知道 product_family 填什么")
        missing = sorted(code for code in industry_templates().field_keys(PACKAGING_INDUSTRY)
                         if code not in prompt)
        self.assertEqual([], missing, f"提示词里缺这些包装字段：{missing[:8]}")

    def test_b3_checklist_reports_the_packaging_required_set(self):
        module = product_params()
        report = module.checklist(types.SimpleNamespace(params=[]), packaging_family())
        summary = report["summary"]
        self.assertEqual(PACKAGING_FIELD_COUNT, summary["total"])
        self.assertEqual(len(industry_templates().required_keys(PACKAGING_INDUSTRY)),
                         summary["required_total"],
                         "必填数必须等于 industry_templates.required_keys('packaging')（10 项）")
        self.assertEqual(0, summary["required_filled"], "没有参数时不该有任何已填项")


# ===========================================================================
# C. 按行业锁族
# ===========================================================================
class PinFamilyByIndustryTest(unittest.TestCase):
    def test_c1_recommend_params_pins_the_family(self):
        node = func_node(INTEGRATION_PY, "recommend_params")
        must(node is not None, "integration.recommend_params 不存在")
        self.assertIn("family_for_industry", func_source(INTEGRATION_PY, "recommend_params"),
                      "recommend_params 必须按项目行业取锁定的族（Spec §3.1）")
        self.assertTrue(family_calls(INTEGRATION_PY, "recommend_params", "as_prompt", minimum=1),
                        "as_prompt 必须带上锁定的族，否则包装项目还是拿到全部 DA 族")
        self.assertTrue(family_calls(INTEGRATION_PY, "recommend_params", "align", minimum=2),
                        "align 必须带上锁定的族，否则模型写 other 就落 other")

    def test_c2_saving_params_pins_the_family(self):
        self.assertTrue(family_calls(MAIN_PY, "update_integration_params", "align", minimum=2),
                        "保存人工编辑的参数时必须按同一份锁定的族 align（main.py:3083）")

    def test_c3_required_gate_pins_the_family(self):
        self.assertTrue(family_calls(MAIN_PY, "finalize_integration_params", "missing_required",
                                     minimum=2),
                        "必填补齐门禁必须按同一份锁定的族校验（main.py:3175），"
                        "否则包装项目会被要求补「工作温度」")

    def test_c4_resolve_family_no_longer_falls_back_to_other(self):
        module = product_params()
        key = packaging_family()
        self.assertEqual(key, module.resolve_family(key))
        self.assertEqual(key, module.resolve_family("包装盒"),
                         "模型/人写中文族名「包装盒」时必须解析到包装族，不再静默退回 other")


# ===========================================================================
# D. 成本与包装链路对齐
# ===========================================================================
class ClosureAlignmentTest(unittest.TestCase):
    def test_d1_cost_checklist_pins_the_family(self):
        self.assertTrue(family_calls(COST_REVIEW_PY, "payload", "checklist", minimum=2),
                        "4.3 财务看板的参数清单必须按锁定的族（cost_review.py:252）")
        self.assertTrue(family_calls(COST_REVIEW_PY, "payload", "missing_required", minimum=2),
                        "4.3 的「还缺几项」必须按同一份族校验")

    def test_d2_bom_inner_dim_keys_are_packaging_fields(self):
        bom = importlib.import_module("tech_app.backend.services.packaging_bom")
        missing = sorted(set(bom.INNER_DIM_KEYS) - packaging_codes())
        self.assertEqual([], missing,
                         f"参数化 BOM 要的内尺寸键 {missing} 不在包装族字段里 —— "
                         "3.2 填的字段和 BOM 要的字段必须是同一套")

    def test_d3_required_set_matches_box_matching(self):
        templates = industry_templates()
        self.assertEqual(set(PACKAGING_REQUIRED), set(templates.required_keys(PACKAGING_INDUSTRY)),
                         "护栏：需求模板的包装必填集合变了，先确认是不是误改")
        got = {item["code"] for item in product_params().fields_for(packaging_family())
               if item.get("required")}
        self.assertEqual(set(PACKAGING_REQUIRED), got,
                         "包装族的必填集合必须与盒型匹配读的 required_keys('packaging') 同源")

    def test_d4_industry_key_has_one_source(self):
        match = importlib.import_module("tech_app.backend.services.packaging_match")
        self.assertEqual(PACKAGING_INDUSTRY, match.PACKAGING_INDUSTRY)
        self.assertEqual(packaging_family(), family_for_industry(match.PACKAGING_INDUSTRY),
                         "包装链路的行业键必须能映射到包装族（行业字面量只有一份）")


# ===========================================================================
# E. 护栏：本批不动既有口径
# ===========================================================================
class BaselineGuardTest(unittest.TestCase):
    def test_e1_other_family_is_untouched(self):
        codes = {item["code"] for item in product_params().fields_for("other")}
        self.assertEqual(set(OTHER_BASELINE), codes,
                         "「其他成品」族的 14 项是本批的基线：改了它等于改了非包装行业的"
                         "3.2 参数表")

    def test_e2_requirement_template_is_untouched(self):
        templates = industry_templates()
        self.assertEqual(PACKAGING_FIELD_COUNT, len(templates.field_keys(PACKAGING_INDUSTRY)))
        self.assertEqual(set(PACKAGING_REQUIRED),
                         set(templates.required_keys(PACKAGING_INDUSTRY)),
                         "1.1 表单 / 抽取 / PDF / 预检 / 前端 RC_PACKAGING_SPECS 都按这 64/10 对齐")

    def test_e3_da_families_are_append_only(self):
        module = product_params()
        key = str(getattr(module, "PACKAGING_FAMILY", "") or "")
        kept = [item for item in module.family_keys() if not key or item != key]
        self.assertEqual(list(DA_FAMILIES), kept,
                         "DA 五个族的相对顺序必须保持不变，包装族只允许追加")


if __name__ == "__main__":
    unittest.main()
