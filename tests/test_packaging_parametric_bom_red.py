"""红测：参数化部件展开与包装 BOM —— 包装第 5 批。

Spec：docs/specs/packaging-parametric-bom.md
依赖：第 1 批（四行业注册表）、第 2 批（包装需求模板）、第 3 批（包装知识库扩展表 +
12 盒型 / 31 部件模板 / 23 工艺模板 / 12 内托配件 / 3 物流规则 / 5 包装物料）、第 4 批
（盒型五维匹配与工艺经理确认）已完成。

现状缺口（实测，不是推断）：
  · `tech_app/backend/services/packaging_formula.py` 不存在 —— 全仓没有任何安全表达式求值器
    （`ast.parse` / `safe_eval` / `evaluate_expression` 零命中），31 条部件模板里的
    `L+4t+2c`、`（L-4）×（W-4）× 8`、`长度 = W/3 + 40` 目前无处可算；
  · `tech_app/backend/services/packaging_bom.py` 不存在 —— 确认盒型之后没有任何参数化展开
    与 BOM 组装能力；
  · `da_schema.sql` 没有 `wip_packaging_bom_item` 表，展开结果无处落库；
  · `main.py` 没有 packaging-bom 三个路由，`requirement-confirm.js` 也没有对应面板。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import inspect
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FORMULA_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_formula.py"
BOM_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_bom.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
SCHEMA_SQL = ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql"
CONFIRM_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import (da_db, da_repo, da_seed_packaging as seed,  # noqa: E402
                                      kb_repo, meta_backend, store)
from tech_app.backend.services import industry_templates  # noqa: E402

PID = "pkgbom000001"
REQ_NO = "REQ-PKG-0001"

BOX_MAIN = "YT-RB-01001-A"     # 天地盖全盖：t=2.0，c=1.8，10 个部件模板
BOX_BOOK = "YT-RB-02001-A"     # 书型盒铰链翻盖：t=2.0，c=1.5，10 个部件模板
BOX_DRAWER = "YT-RB-03001-A"   # 抽屉盒内滑式：t=2.0，c=1.2，11 个部件模板
BOX_NO_PARTS = "YT-RB-01002-A"  # 12 个盒型里 9 个没有部件模板，这是其中之一

CATEGORIES = ("finished", "box_part", "material", "process",
              "packaging", "tooling", "optional_part")

REQ = {"industry": "packaging", "packaging_product_name": "礼盒", "quote_quantity": 1000,
       "inner_length": 200, "inner_width": 150, "inner_height": 80, "fit_clearance": 1.8}

# 让 YT-RB-01001-A 的 10 个部件全部算出来所需的最小覆盖（这些量不许实现自行推断）
OVERRIDES_FULL_MAIN = {"H盖": 30, "盖展开尺寸": 220, "盒身展开尺寸": 216, "包边": 15}

# YT-RB-01001-A 无覆盖时的精确展开结果：(status, length, width, height, missing_variables)
MAIN_EXPECT = {
    "RB01001-P01": ("computed", 211.6, 161.6, None, []),
    "RB01001-P02": ("needs_input", 211.6, None, None, ["H盖"]),
    "RB01001-P03": ("needs_input", 161.6, None, None, ["H盖"]),
    "RB01001-P04": ("computed", 200.0, 150.0, None, []),
    "RB01001-P05": ("computed", 204.0, 80.0, None, []),
    "RB01001-P06": ("computed", 150.0, 80.0, None, []),
    "RB01001-P07": ("needs_input", None, None, None, ["盖展开尺寸", "包边"]),
    "RB01001-P08": ("needs_input", None, None, None, ["盒身展开尺寸", "包边"]),
    "RB01001-P09": ("computed", 196.0, 146.0, 8.0, []),
    "RB01001-P10": ("computed", 90.0, None, None, []),
}

MAIN_PROCESS_STEPS = {"灰板开料", "V 槽开槽", "灰板成型", "面纸印刷", "表面处理",
                      "面纸模切", "机裱", "手裱", "组装", "检验", "清洁包装"}
MAIN_MATERIALS = {"灰板 2.0mm", "特种纸 200g", "EVA 植绒 5mm", "涤纶丝带 10mm"}
MAIN_NEEDS_INPUT = {"RB01001-P02", "RB01001-P03", "RB01001-P07", "RB01001-P08"}


def load_formula():
    """表达式引擎；不存在时返回 None（用例给明确断言，不抛 ImportError）。"""
    try:
        return importlib.import_module("tech_app.backend.services.packaging_formula")
    except Exception:
        return None


def load_bom():
    """BOM 引擎；不存在时返回 None。"""
    try:
        return importlib.import_module("tech_app.backend.services.packaging_bom")
    except Exception:
        return None


def box_row(code):
    for row in seed.BOX_TYPES:
        if row["box_type_code"] == code:
            return dict(row)
    raise AssertionError("种子里没有盒型 %s" % code)


def packaging_tables():
    """按 da_seed_packaging 的真实演示数据造快照（不合成假数据）。"""
    materials = [dict(item["material"], industry="packaging", status="active")
                 for item in seed.MATERIALS]
    properties = [dict(prop, industry="packaging")
                  for item in seed.MATERIALS for prop in item.get("properties") or []]
    return {
        "kb_packaging_box_type": [dict(r) for r in seed.BOX_TYPES],
        "kb_packaging_part_template": [dict(r) for r in seed.PART_TEMPLATES],
        "kb_packaging_process_template": [dict(r) for r in seed.PROCESS_TEMPLATES],
        "kb_packaging_insert_accessory": [dict(r) for r in seed.ACCESSORIES],
        "kb_packaging_logistics_rule": [dict(r) for r in seed.LOGISTICS_RULES],
        "kb_packaging_match_weight": [dict(r) for r in seed.MATCH_WEIGHTS],
        "kb_material": materials,
        "kb_material_property": properties,
    }


class BomCase(unittest.TestCase):
    """共用：快照可替换 + 独立临时 SQLite + 独立 meta 目录。"""

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "bom.sqlite3"
        self._patch_da = mock.patch.object(config, "DA_DB_PATH", self.db_file)
        self._patch_da.start()
        da_db.init_db(self.db_file)
        self._backend = meta_backend._backend
        self.data_dir = pathlib.Path(tempfile.mkdtemp())
        meta_backend._backend = meta_backend.JsonMetaBackend(self.data_dir)
        self.snapshot()

    def tearDown(self):
        meta_backend._backend = self._backend
        self._patch_da.stop()
        da_db.close_conn()
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._cache)

    # ---- 知识库快照 -------------------------------------------------------- #
    def snapshot(self, **overrides):
        tables = packaging_tables()
        tables.update(overrides)
        kb_repo._CACHE["version"] = "probe-version"
        kb_repo._CACHE["tables"] = tables
        return tables

    # ---- 模块加载 ---------------------------------------------------------- #
    def formula(self):
        module = load_formula()
        self.assertIsNotNone(
            module, "缺少 tech_app/backend/services/packaging_formula.py（Spec §4.5）")
        return module

    def bom(self):
        module = load_bom()
        self.assertIsNotNone(
            module, "缺少 tech_app/backend/services/packaging_bom.py（Spec §4.5）")
        return module

    # ---- 需求单与确认盒型 --------------------------------------------------- #
    def save_requirement(self, project_id=PID, requirement_no=REQ_NO, **over):
        data = dict(REQ)
        data.update(over)
        store.save_requirement(project_id, {
            "project_id": project_id, "requirement_no": requirement_no,
            "title": "包装 BOM 用例", "status": "pending_confirmation", "data": data})

    def confirm_box(self, code=BOX_MAIN, project_id=PID, requirement_no=REQ_NO):
        da_repo.save_box_match({
            "project_id": project_id, "requirement_no": requirement_no,
            "industry": "packaging", "engine_version": "packaging_match_v1",
            "inputs": {}, "candidates": [], "missing_inputs": [],
            "suggested_box_type": code})
        da_repo.update_box_match_decision(
            project_id, requirement_no, decision="confirmed", confirmed_box_type=code,
            confirmed_by="wangjingli", confirmed_at=da_db.now())

    # ---- 常用断言 ---------------------------------------------------------- #
    @staticmethod
    def part(result, part_code):
        for item in result["parts"]:
            if item["part_code"] == part_code:
                return item
        raise AssertionError("展开结果里没有 %s：%s" % (
            part_code, [p["part_code"] for p in result["parts"]]))

    @staticmethod
    def by_category(result, category):
        return [row for row in result["items"] if row["bom_category"] == category]

    @staticmethod
    def db_rows(sql, params=()):
        return da_db.query_all(sql, params)


# --------------------------------------------------------------------------- #
# A. 表达式引擎：白名单、精度与"绝不执行"
# --------------------------------------------------------------------------- #
class AFormulaEngine(BomCase):
    def test_a1_module_and_function_closure(self):
        module = self.formula()
        self.assertEqual(set(module.ALLOWED_FUNCTIONS),
                         {"MIN", "MAX", "IF", "IFERROR", "ROUND"},
                         "只允许这 5 个函数（Spec §2.3）")
        self.assertAlmostEqual(float(module.evaluate("1 + 1", {})), 2.0, places=6)

    def test_a2_arithmetic_matches_the_spec_table(self):
        module = self.formula()
        self.assertAlmostEqual(float(module.evaluate("L+4t+2c", {"L": 200.0, "t": 2.0, "c": 1.8})),
                               211.6, places=1)
        self.assertAlmostEqual(float(module.evaluate("W+4t+2c", {"W": 150.0, "t": 2.0, "c": 1.8})),
                               161.6, places=1)
        self.assertAlmostEqual(float(module.evaluate("L+2t", {"L": 200.0, "t": 2.0})), 204.0, places=1)
        self.assertAlmostEqual(float(module.evaluate("W/3 + 40", {"W": 150.0})), 90.0, places=1)
        self.assertAlmostEqual(float(module.evaluate("（L-4）", {"L": 200.0})), 196.0, places=1)

    def test_a3_precedence_and_parentheses(self):
        module = self.formula()
        self.assertAlmostEqual(float(module.evaluate("2+3*4", {})), 14.0, places=1)
        self.assertAlmostEqual(float(module.evaluate("(2+3)*4", {})), 20.0, places=1)
        self.assertAlmostEqual(float(module.evaluate("W内-2t", {"W内": 122.0, "t": 2.0})), 118.0, places=1)

    def test_a4_normalize_expression(self):
        module = self.formula()
        cases = {
            "（L-4）×（W-4）× 8": "(L-4)*(W-4)*8",
            "长度 = W/3 + 40": "W/3+40",
            "盖展开尺寸 + 包边 + 出血3mm": "盖展开尺寸+包边+出血3",
            "6 ÷ 2": "6/2",
            "L+4t+2c  ×  W+4t+2c": "L+4t+2c*W+4t+2c",
        }
        for raw, expected in cases.items():
            got = module.normalize_expression(raw)
            self.assertEqual(str(got).replace(" ", ""), expected, "归一化 %r" % raw)

    def test_a5_result_is_rounded_half_up_to_one_decimal(self):
        module = self.formula()
        self.assertEqual(float(module.evaluate("10/3", {})), 3.3)
        self.assertEqual(float(module.evaluate("1/4", {})), 0.3,
                         "Spec §2.3 要求 1 位小数、半上进位（0.25 → 0.3）")

    def test_a6_whitelisted_functions_work(self):
        module = self.formula()
        self.assertAlmostEqual(float(module.evaluate("MAX(120/1000, 800/480)", {})), 1.7, places=1)
        self.assertAlmostEqual(float(module.evaluate("MIN(3, 5)", {})), 3.0, places=1)
        self.assertAlmostEqual(float(module.evaluate("IF(1 > 0, 7, 9)", {})), 7.0, places=1)
        self.assertAlmostEqual(float(module.evaluate("IF(1 >= 2, 7, 9)", {})), 9.0, places=1)
        self.assertAlmostEqual(float(module.evaluate("ROUND(10/3, 2)", {}, precision=2)), 3.33, places=2)
        self.assertAlmostEqual(float(module.evaluate("IFERROR(1/0, 5)", {})), 5.0, places=1)

    def test_a7_division_by_zero_raises(self):
        module = self.formula()
        with self.assertRaises(module.FormulaError):
            module.evaluate("1/0", {})
        with self.assertRaises(module.FormulaError):
            module.evaluate("W/0", {"W": 10.0})

    def test_a8_missing_variable_is_reported(self):
        module = self.formula()
        with self.assertRaises(module.FormulaError) as ctx:
            module.evaluate("L + 4t + 2c + H盖", {"L": 200.0, "t": 2.0, "c": 1.8})
        self.assertIn("H盖", list(getattr(ctx.exception, "missing_variables", [])))

    def test_a9_forbidden_constructs_are_refused(self):
        module = self.formula()
        payloads = [
            "__import__('os').system('true')",
            "L.__class__",
            "L[0]",
            "'abc'",
            "2 ** 8",
            "L; L",
            "L\n+1",
            "import os",
            "eval('1')",
            "open('/etc/passwd')",
            "SUM(L, 1)",
            "L + lambda: 1",
        ]
        for text in payloads:
            with self.assertRaises(module.FormulaError, msg="非法表达式必须抛错：%r" % text):
                module.evaluate(text, {"L": 1.0})

    def test_a10_engine_never_executes_dynamic_code(self):
        module = self.formula()
        source = inspect.getsource(module)
        for forbidden in ("eval(", "exec(", "compile(", "__import__", "subprocess",
                          "os.system", "pickle.loads"):
            self.assertFalse(forbidden in source,
                             "表达式引擎不得出现 %s：禁止执行任意代码（Spec §2.3）" % forbidden)


# --------------------------------------------------------------------------- #
# B. 变量绑定：只按 Spec §2.2 那张表，不做"看起来合理"的推断
# --------------------------------------------------------------------------- #
class BVariableBinding(BomCase):
    def test_b1_dims_come_from_the_requirement(self):
        module = self.bom()
        variables = module.bind_variables(box_row(BOX_MAIN), dict(REQ), {})
        for name, value in (("L", 200.0), ("W", 150.0), ("H", 80.0)):
            self.assertIn(name, variables, "缺 %s 绑定" % name)
            self.assertAlmostEqual(float(variables[name]["value"]), value, places=1)
            self.assertEqual(variables[name]["source"], "requirement")

    def test_b2_thickness_is_the_first_number_of_the_box(self):
        module = self.bom()
        t = module.bind_variables(box_row(BOX_MAIN), dict(REQ), {})["t"]
        self.assertAlmostEqual(float(t["value"]), 2.0, places=3)
        self.assertEqual(t["source"], "box_type")
        t25 = module.bind_variables(box_row("YT-RB-01003-A"), dict(REQ), {})["t"]
        self.assertAlmostEqual(float(t25["value"]), 2.5, places=3,
                               msg="`2.5（2.0/3.0可选）` 取首个数值 2.5")

    def test_b3_clearance_prefers_requirement_then_box(self):
        module = self.bom()
        from_req = module.bind_variables(box_row(BOX_MAIN), dict(REQ, fit_clearance=2.4), {})["c"]
        self.assertAlmostEqual(float(from_req["value"]), 2.4, places=3)
        no_c = {k: v for k, v in REQ.items() if k != "fit_clearance"}
        from_box = module.bind_variables(box_row(BOX_BOOK), no_c, {})["c"]
        self.assertAlmostEqual(float(from_box["value"]), 1.5, places=3,
                               msg="需求没填配合间隙时取盒型的 1.5")
        self.assertEqual(from_box["source"], "box_type")

    def test_b4_override_wins_and_is_traced(self):
        module = self.bom()
        variables = module.bind_variables(box_row(BOX_MAIN), dict(REQ), {"L": 300, "H盖": 30})
        self.assertAlmostEqual(float(variables["L"]["value"]), 300.0, places=1)
        self.assertEqual(variables["L"]["source"], "override")
        self.assertAlmostEqual(float(variables["H盖"]["value"]), 30.0, places=1)
        self.assertEqual(variables["H盖"]["source"], "override")

    def test_b5_override_only_variables_are_never_inferred(self):
        module = self.bom()
        self.assertEqual(set(module.AUTO_VARIABLES), {"L", "W", "H", "t", "c"})
        self.assertIn("H盖", module.OVERRIDE_ONLY_VARIABLES)
        self.assertIn("包边", module.OVERRIDE_ONLY_VARIABLES)
        variables = module.bind_variables(box_row(BOX_MAIN), dict(REQ), {})
        for name in module.OVERRIDE_ONLY_VARIABLES:
            self.assertNotIn(name, variables,
                             "%s 只能来自 overrides，不能从 L/W/H/t/c 推断" % name)

    def test_b6_inline_default_variables_are_bound(self):
        module = self.bom()
        result = module.expand_parts(BOX_BOOK, dict(REQ))
        variables = result["variables"]
        self.assertAlmostEqual(float(variables["铰链宽"]["value"]), 40.0, places=1,
                               msg="`铰链宽40` 的数字是内联默认值（Spec §2.2）")
        self.assertAlmostEqual(float(variables["出血"]["value"]), 3.0, places=1,
                               msg="`出血3mm` 去掉单位后缀后数字是内联默认值")
        self.assertEqual(variables["铰链宽"]["source"], "literal_default")

    def test_b7_missing_inner_dims_are_a_gap(self):
        module = self.bom()
        req = {k: v for k, v in REQ.items() if k not in ("inner_length", "inner_width")}
        with self.assertRaises(module.BomError) as ctx:
            module.expand_parts(BOX_MAIN, req)
        self.assertEqual(getattr(ctx.exception, "code", ""), "missing_requirement_input",
                         "缺内尺寸必须报明确缺口，不得拿默认值凑（Spec §2.6）")
        self.assertEqual(getattr(ctx.exception, "status_code", 409), 409)


# --------------------------------------------------------------------------- #
# C. 部件展开：31 条模板的真实公式必须算得出、算得准、算不了就报缺口
# --------------------------------------------------------------------------- #
class CPartExpansion(BomCase):
    def expand(self, code=BOX_MAIN, req=None, overrides=None):
        return self.bom().expand_parts(code, dict(req or REQ),
                                       overrides=dict(overrides or {}))

    def test_c1_real_templates_are_expanded(self):
        module = self.bom()
        for code, count in ((BOX_MAIN, 10), (BOX_BOOK, 10), (BOX_DRAWER, 11)):
            result = module.expand_parts(code, dict(REQ))
            self.assertEqual(len(result["parts"]), count, "%s 的部件数" % code)
            self.assertEqual(result["engine_version"], "packaging_bom_v1")
            self.assertEqual(result["box_type_code"], code)

    def test_c2_exact_sizes_of_the_reference_box(self):
        result = self.expand()
        for part_code, (status, length, width, height, missing) in MAIN_EXPECT.items():
            item = self.part(result, part_code)
            self.assertEqual(item["status"], status, "%s 的 status" % part_code)
            self.assertEqual(list(item["missing_variables"]), missing, "%s 的缺失变量" % part_code)
            for key, expected in (("length", length), ("width", width), ("height", height)):
                value = item[key]
                if expected is None:
                    self.assertIsNone(value, "%s.%s 应为 null（不许填 0 或估算）" % (part_code, key))
                else:
                    self.assertAlmostEqual(float(value), expected, places=1,
                                           msg="%s.%s" % (part_code, key))
        self.assertEqual(result["expanded_count"], 6)
        self.assertEqual(result["needs_input_count"], 4)

    def test_c3_optional_part_is_separated_and_keeps_its_source_expression(self):
        item = self.part(self.expand(), "RB01001-P10")
        self.assertEqual(int(item.get("is_optional") or 0), 1)
        self.assertEqual(item["size_mode"], "expression")
        self.assertEqual(item["size_length_expr"], "长度 = W/3 + 40",
                         "溯源要留原始表达式，不许把原文改写掉")
        self.assertAlmostEqual(float(item["length"]), 90.0, places=1)

    def test_c4_standard_part_has_no_size_and_no_missing(self):
        item = self.part(self.expand(BOX_BOOK), "RB02001-P08")     # 磁铁：size_expr = 标准件
        self.assertEqual(item["size_mode"], "standard_part")
        self.assertEqual(item["status"], "computed")
        self.assertEqual(list(item["missing_variables"]), [])
        for key in ("length", "width", "height"):
            self.assertIsNone(item[key], "标准件没有公式尺寸，必须为空")

    def test_c5_inline_default_expression_computes_without_overrides(self):
        module = self.bom()
        result = module.expand_parts(BOX_BOOK, dict(REQ))
        item = self.part(result, "RB02001-P07")                   # 铰链布：204 × 铰链宽40
        self.assertEqual(item["status"], "computed")
        self.assertAlmostEqual(float(item["length"]), 204.0, places=1)
        self.assertAlmostEqual(float(item["width"]), 40.0, places=1)

    def test_c6_overrides_complete_the_whole_box(self):
        result = self.expand(overrides=OVERRIDES_FULL_MAIN)
        self.assertEqual(result["needs_input_count"], 0)
        self.assertEqual(result["expanded_count"], 10)
        self.assertEqual(self.part(result, "RB01001-P02")["status"], "computed")
        self.assertAlmostEqual(float(self.part(result, "RB01001-P02")["width"]), 30.0, places=1)
        self.assertAlmostEqual(float(self.part(result, "RB01001-P07")["length"]), 238.0, places=1)
        self.assertAlmostEqual(float(self.part(result, "RB01001-P08")["length"]), 234.0, places=1)

    def test_c7_box_without_part_template_is_an_explicit_gap(self):
        module = self.bom()
        with self.assertRaises(module.BomError) as ctx:
            module.expand_parts(BOX_NO_PARTS, dict(REQ))
        self.assertEqual(getattr(ctx.exception, "code", ""), "no_part_template",
                         "9 个盒型没有部件模板，必须是明确缺口，不许返回空 BOM 冒充成功")
        self.assertIn(BOX_NO_PARTS, str(ctx.exception))
        self.assertEqual(getattr(ctx.exception, "status_code", 409), 409)

    def test_c8_illegal_expression_turns_into_a_gap_not_a_number(self):
        tables = self.snapshot()
        patched = []
        for row in seed.PART_TEMPLATES:
            row = dict(row)
            if row["part_code"] == "RB01001-P04":
                row["size_length_expr"] = "__import__('os').system('true')"
            patched.append(row)
        tables["kb_packaging_part_template"] = patched
        result = self.expand()
        item = self.part(result, "RB01001-P04")
        self.assertEqual(item["status"], "needs_input",
                         "非法表达式只能转成缺口，绝不能执行、绝不能给数字")
        self.assertIsNone(item["length"])


# --------------------------------------------------------------------------- #
# D. BOM 七类：分类口径按 0903 收紧，缺数据不出空行
# --------------------------------------------------------------------------- #
class DBomCategories(BomCase):
    def build(self, code=BOX_MAIN, overrides=None, requirement_no=REQ_NO, **req_over):
        self.save_requirement(requirement_no=requirement_no, **req_over)
        self.confirm_box(code, requirement_no=requirement_no)
        return self.bom().build_bom(PID, requirement_no, overrides=dict(overrides or {}))

    def test_d1_categories_are_the_seven(self):
        module = self.bom()
        self.assertEqual(set(module.BOM_CATEGORIES), set(CATEGORIES))
        self.assertEqual(len(module.BOM_CATEGORIES), 7)

    def test_d2_finished_row(self):
        rows = self.by_category(self.build(), "finished")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_key"], BOX_MAIN)
        self.assertEqual(rows[0]["item_name"], box_row(BOX_MAIN)["name"])
        self.assertAlmostEqual(float(rows[0]["quantity"]), 1000.0, places=1)

    def test_d3_box_part_and_optional_part_are_split(self):
        result = self.build()
        parts = {row["item_key"] for row in self.by_category(result, "box_part")}
        optional = {row["item_key"] for row in self.by_category(result, "optional_part")}
        self.assertEqual(len(parts), 9)
        self.assertNotIn("RB01001-P10", parts, "选配部件不能混在 box_part 里")
        self.assertEqual(optional, {"RB01001-P10"})
        p05 = [row for row in self.by_category(result, "box_part")
               if row["item_key"] == "RB01001-P05"][0]
        self.assertAlmostEqual(float(p05["quantity"]), 2.0, places=1)
        self.assertAlmostEqual(float(p05["length_mm"]), 204.0, places=1)
        self.assertAlmostEqual(float(p05["width_mm"]), 80.0, places=1)

    def test_d4_process_rows_are_deduped_by_step_name(self):
        rows = self.by_category(self.build(), "process")
        names = [row["item_key"] for row in rows]
        self.assertEqual(len(names), len(set(names)), "process 必须按 step_name 去重")
        self.assertEqual(set(names), MAIN_PROCESS_STEPS)

    def test_d5_tooling_rows_come_from_hard_tooling_steps(self):
        rows = self.by_category(self.build(), "tooling")
        self.assertEqual(len(rows), 2,
                         "YT-RB-01001-A 只有「表面处理（烫金）」与「面纸模切」两道含刀模制程")
        self.assertEqual({row["component"] for row in rows}, {"烫金", "模切"})
        for row in rows:
            self.assertEqual(row["industry"], "packaging")
            blob = json.dumps(row, ensure_ascii=False)
            for forbidden in ("unit_cost", "tooling_cost", "amortiz", "price", "amount"):
                self.assertNotIn(forbidden, blob,
                                 "本批只标「涉及工装 + 待分摊」，不算钱、不定寿命（Spec §2.5）")

    def test_d6_packaging_rows_are_the_logistics_rules(self):
        rows = self.by_category(self.build(), "packaging")
        self.assertEqual({row["item_key"] for row in rows},
                         {rule["rule_code"] for rule in seed.LOGISTICS_RULES})

    def test_d7_material_rows_are_deduped_and_codes_resolved(self):
        result = self.build()
        rows = self.by_category(result, "material")
        self.assertEqual({row["item_key"] for row in rows}, MAIN_MATERIALS)
        by_key = {row["item_key"]: row for row in rows}
        self.assertEqual(by_key["灰板 2.0mm"]["material_code"], "MAT-PKG-GREYBOARD")
        self.assertEqual(by_key["EVA 植绒 5mm"]["material_code"], "MAT-PKG-EVA")
        self.assertIsNone(by_key["涤纶丝带 10mm"]["material_code"],
                          "知识库里解析不到的材料码必须留空，不许编一个")
        self.assertEqual(result["stats"]["material_unresolved"], 1)
        self.assertEqual(result["gaps"]["material_unresolved"], ["涤纶丝带 10mm"])

    def test_d8_every_row_is_traceable_and_stats_add_up(self):
        result = self.build()
        self.assertEqual(result["engine_version"], "packaging_bom_v1")
        self.assertEqual(result["stats"]["by_category"], {
            "finished": 1, "box_part": 9, "optional_part": 1, "material": 4,
            "process": 11, "tooling": 2, "packaging": 3})
        self.assertEqual(result["stats"]["total"], 31)
        self.assertEqual(result["stats"]["computed"], 6)
        self.assertEqual(result["stats"]["needs_input"], 4)
        for row in result["items"]:
            self.assertEqual(row["industry"], "packaging")
            self.assertEqual(row["engine_version"], "packaging_bom_v1")
            self.assertTrue(row["source"], "%s 缺 source 溯源" % row["item_key"])
        self.assertEqual(set(result["gaps"]["needs_input"]), MAIN_NEEDS_INPUT)
        self.assertEqual(list(result["gaps"]["missing_variables"]["RB01001-P02"]), ["H盖"])
        self.assertEqual(list(result["gaps"]["missing_variables"]["RB01001-P07"]),
                         ["盖展开尺寸", "包边"])

    def test_d9_empty_categories_produce_no_rows(self):
        tables = self.snapshot()
        tables["kb_packaging_logistics_rule"] = []
        tables["kb_packaging_process_template"] = [
            dict(row, step_name="灰板开料", work_content="按部件尺寸裁切灰板")
            for row in seed.PROCESS_TEMPLATES if row["box_type_code"] == BOX_MAIN]
        result = self.build()
        self.assertEqual(self.by_category(result, "packaging"), [], "没有物流规则就不出该类行")
        self.assertEqual(self.by_category(result, "tooling"), [], "没有刀模制程就不出工装行")
        self.assertEqual(len(self.by_category(result, "process")), 1, "工序去重后只剩 1 道")


# --------------------------------------------------------------------------- #
# E. 人工锁定与重算：锁定的行不被覆盖，未锁定的行必须重建
# --------------------------------------------------------------------------- #
class ELockAndRecalc(BomCase):
    def build(self, overrides=None):
        self.save_requirement()
        self.confirm_box()
        return self.bom().build_bom(PID, REQ_NO, overrides=dict(overrides or {}))

    def test_e1_lock_marks_the_row(self):
        self.build()
        record = self.bom().lock_bom_item(PID, REQ_NO, "RB01001-P01",
                                          actor={"username": "wangjingli"})
        item = [row for row in record["items"] if row["item_key"] == "RB01001-P01"][0]
        self.assertEqual(item["status"], "locked")
        self.assertEqual(item["locked_by"], "wangjingli")
        self.assertTrue(item["locked_at"])
        rows = self.db_rows("SELECT locked, status FROM wip_packaging_bom_item "
                            "WHERE project_id = ? AND item_key = ?", (PID, "RB01001-P01"))
        self.assertEqual(int(rows[0]["locked"]), 1)

    def test_e2_rebuild_keeps_locked_rows_untouched(self):
        self.build()
        module = self.bom()
        module.lock_bom_item(PID, REQ_NO, "RB01001-P09", actor={"username": "wangjingli"})
        before = self.db_rows("SELECT * FROM wip_packaging_bom_item WHERE project_id = ? "
                              "AND item_key = ?", (PID, "RB01001-P09"))[0]
        self.build(overrides=OVERRIDES_FULL_MAIN)
        after = self.db_rows("SELECT * FROM wip_packaging_bom_item WHERE project_id = ? "
                             "AND item_key = ?", (PID, "RB01001-P09"))[0]
        self.assertEqual(after["length_mm"], before["length_mm"], "锁定行的尺寸不得被重算覆盖")
        self.assertEqual(float(after["length_mm"]), 196.0)
        self.assertEqual(int(after["locked"]), 1)
        self.assertEqual(after["locked_by"], "wangjingli")
        self.assertEqual(after["locked_at"], before["locked_at"])
        rebuilt = self.db_rows("SELECT status FROM wip_packaging_bom_item WHERE project_id = ? "
                               "AND item_key = ?", (PID, "RB01001-P02"))[0]
        self.assertEqual(rebuilt["status"], "computed",
                         "未锁定行必须按新的覆盖变量重建（H盖 给了 30，不再是 needs_input）")

    def test_e3_rebuild_replaces_unlocked_rows(self):
        self.build()
        total = self.db_rows("SELECT COUNT(*) AS n FROM wip_packaging_bom_item "
                             "WHERE project_id = ?", (PID,))[0]["n"]
        self.build(overrides=OVERRIDES_FULL_MAIN)
        again = self.db_rows("SELECT COUNT(*) AS n FROM wip_packaging_bom_item "
                             "WHERE project_id = ?", (PID,))[0]["n"]
        self.assertEqual(total, again, "同一 (project_id, requirement_no) 整体替换，不留旧行")
        left = self.db_rows("SELECT COUNT(*) AS n FROM wip_packaging_bom_item "
                            "WHERE project_id = ? AND status = 'needs_input'", (PID,))[0]["n"]
        self.assertEqual(left, 0)

    def test_e4_lock_and_unlock_write_the_project_audit(self):
        self.build()
        module = self.bom()
        with mock.patch.object(store, "audit") as spy:
            module.lock_bom_item(PID, REQ_NO, "RB01001-P01", actor={"username": "wangjingli"})
            module.lock_bom_item(PID, REQ_NO, "RB01001-P01", actor={"username": "wangjingli"},
                                 locked=False)
        actions = [str(call.args[1]) for call in spy.call_args_list]
        self.assertTrue(any("packaging_bom_item_locked" in a for a in actions), actions)
        self.assertTrue(any("packaging_bom_item_unlocked" in a for a in actions), actions)

    def test_e5_unlock_restores_the_row_status(self):
        self.build()
        module = self.bom()
        module.lock_bom_item(PID, REQ_NO, "RB01001-P02", actor={"username": "wangjingli"})
        record = module.lock_bom_item(PID, REQ_NO, "RB01001-P02",
                                      actor={"username": "wangjingli"}, locked=False)
        item = [row for row in record["items"] if row["item_key"] == "RB01001-P02"][0]
        self.assertEqual(item["status"], "needs_input", "解锁后按当前展开结果重新判定")
        self.assertFalse(item["locked_at"])

    def test_e6_repeat_lock_is_idempotent(self):
        self.build()
        module = self.bom()
        first = module.lock_bom_item(PID, REQ_NO, "RB01001-P01", actor={"username": "wangjingli"})
        locked_at = [row for row in first["items"] if row["item_key"] == "RB01001-P01"][0]["locked_at"]
        with mock.patch.object(store, "audit") as spy:
            second = module.lock_bom_item(PID, REQ_NO, "RB01001-P01",
                                          actor={"username": "wangjingli"})
        again = [row for row in second["items"] if row["item_key"] == "RB01001-P01"][0]["locked_at"]
        self.assertEqual(locked_at, again, "重复锁定同一状态必须幂等")
        self.assertEqual(spy.call_count, 0, "重复锁定不得重复写审计")

    def test_e7_lock_unknown_item_is_refused(self):
        self.build()
        with self.assertRaises(self.bom().BomError) as ctx:
            self.bom().lock_bom_item(PID, REQ_NO, "RB01001-P99")
        self.assertEqual(getattr(ctx.exception, "code", ""), "item_not_found")
        self.assertEqual(getattr(ctx.exception, "status_code", 404), 404)


# --------------------------------------------------------------------------- #
# F. 落库、缺口与读回
# --------------------------------------------------------------------------- #
class FPersistAndGaps(BomCase):
    def test_f1_repo_functions_and_schema_exist(self):
        for name in ("save_packaging_bom", "load_packaging_bom"):
            self.assertTrue(hasattr(da_repo, name), "da_repo 缺 %s()（Spec §4.5）" % name)
        self.assertTrue("wip_packaging_bom_item" in SCHEMA_SQL.read_text(encoding="utf-8"),
                        "da_schema.sql 必须建 wip_packaging_bom_item 表（Spec §3.1）")
        with sqlite3.connect(str(self.db_file)) as conn:
            names = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("wip_packaging_bom_item", names)
            info = list(conn.execute("PRAGMA table_info(wip_packaging_bom_item)"))
            columns = {row[1] for row in info}
            for column in ("project_id", "requirement_no", "industry", "engine_version",
                           "generated_at", "bom_category", "item_key", "item_name",
                           "material_code", "quantity", "size_source_json", "status",
                           "missing_variables", "is_optional", "locked", "locked_by",
                           "locked_at"):
                self.assertIn(column, columns, "缺列 %s" % column)
            primary = {row[1] for row in info if row[5]}
            self.assertEqual(primary, {"project_id", "requirement_no", "bom_category", "item_key"},
                             "主键必须是 (project_id, requirement_no, bom_category, item_key)")

    def test_f2_engine_version_constant(self):
        self.assertEqual(self.bom().ENGINE_VERSION, "packaging_bom_v1")

    def test_f3_no_confirmed_box_is_a_gap(self):
        self.save_requirement()
        with self.assertRaises(self.bom().BomError) as ctx:
            self.bom().build_bom(PID, REQ_NO)
        self.assertEqual(getattr(ctx.exception, "code", ""), "box_type_not_confirmed",
                         "没有确认盒型时必须报缺口，不得自己挑一个候选（Spec §2.1）")

    def test_f4_non_packaging_industry_is_refused(self):
        store.save_requirement(PID, {
            "project_id": PID, "requirement_no": REQ_NO, "title": "半导体用例",
            "status": "pending_confirmation",
            "data": {"industry": "semiconductor", "product_name": "某芯片"}})
        with self.assertRaises(self.bom().BomError) as ctx:
            self.bom().build_bom(PID, REQ_NO)
        self.assertEqual(getattr(ctx.exception, "status_code", 400), 400,
                         "包装 BOM 只对 packaging 行业生效")

    def test_f5_missing_inner_dims_is_a_gap(self):
        self.save_requirement(inner_length="", inner_width="")
        self.confirm_box()
        with self.assertRaises(self.bom().BomError) as ctx:
            self.bom().build_bom(PID, REQ_NO)
        self.assertEqual(getattr(ctx.exception, "code", ""), "missing_requirement_input")

    def test_f6_read_back_matches_what_was_built(self):
        self.save_requirement()
        self.confirm_box()
        module = self.bom()
        built = module.build_bom(PID, REQ_NO)
        loaded = module.load_bom(PID, REQ_NO)
        self.assertTrue(loaded["built"])
        self.assertEqual(len(loaded["items"]), len(built["items"]))
        self.assertEqual(loaded["stats"]["total"], 31)
        repo_rows = da_repo.load_packaging_bom(PID, REQ_NO)
        self.assertEqual(len(repo_rows), 31)
        self.assertEqual({row["bom_category"] for row in repo_rows}, set(CATEGORIES))
        row = [r for r in repo_rows if r["item_key"] == "RB01001-P01"][0]
        self.assertEqual(row["engine_version"], "packaging_bom_v1")
        self.assertTrue(row["generated_at"])
        source = json.loads(row["size_source_json"])
        self.assertEqual(source["length"]["expr"], "L+4t+2c", "落库要留下尺寸的公式溯源")
        self.assertEqual(source["length"]["variables"]["t"], "box_type")
        self.assertIsNone(source["height"], "P01 没有高度表达式，该维溯源为 null")

    def test_f7_empty_read_does_not_raise(self):
        record = self.bom().load_bom("nobom00000001", "REQ-NONE")
        self.assertFalse(record["built"])
        self.assertEqual(list(record["items"]), [])
        self.assertEqual(record["stats"]["total"], 0)


# --------------------------------------------------------------------------- #
# G. 接口与角色门禁
# --------------------------------------------------------------------------- #
class GApiAndRoles(BomCase):
    def test_g1_routes_are_registered(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for suffix in ("/requirement/packaging-bom", "/requirement/packaging-bom/lock"):
            self.assertTrue(suffix in source, "main.py 缺路由 %s" % suffix)
        import tech_app.backend.main as main  # noqa: F401  (导入较慢，只在需要时)
        paths = {route.path for route in main.app.routes}
        self.assertIn("/api/projects/{project_id}/requirement/packaging-bom", paths)
        self.assertIn("/api/projects/{project_id}/requirement/packaging-bom/lock", paths)

    def test_g2_write_roles_are_reused_from_batch4(self):
        match = importlib.import_module("tech_app.backend.services.packaging_match")
        self.assertIs(self.bom().BOM_WRITE_ROLES, match.BOX_MATCH_DECIDE_ROLES,
                      "展开/锁定必须复用第 4 批的角色常量对象，不得另抄一份")
        self.assertEqual(set(self.bom().BOM_WRITE_ROLES),
                         {"process_manager", "process_director", "admin"})
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertTrue("BOX_MATCH_DECIDE_ROLES" in source,
                        "main.py 的包装 BOM 路由必须引用第 4 批的角色常量")

    def test_g3_frontend_panel_is_wired(self):
        source = CONFIRM_JS.read_text(encoding="utf-8", errors="replace")
        self.assertTrue("packaging-bom" in source, "1.2 需求确认页要接包装 BOM 接口")


# --------------------------------------------------------------------------- #
# H. 非回归护栏：前四批与三个原行业不许被这套改动碰坏
# --------------------------------------------------------------------------- #
class HNonRegression(BomCase):
    def test_h1_packaging_requirement_fields_unchanged(self):
        self.assertEqual(len(industry_templates.field_keys("packaging")), 64)
        self.assertEqual(industry_templates.required_keys("packaging"), {
            "box_type", "closure_type", "face_paper_gsm", "inner_height",
            "inner_length", "inner_width", "packaging_category",
            "packaging_product_name", "quote_quantity", "v_groove"})

    def test_h2_new_engines_are_offline(self):
        for module in (self.formula(), self.bom()):
            source = inspect.getsource(module)
            for forbidden in ("requests", "urllib", "httpx", "psycopg", "llm_client",
                              "subprocess", "socket"):
                self.assertFalse(forbidden in source,
                                 "%s 不得出现 %s：不联网、不起进程、不调模型（Spec §5）"
                                 % (module.__name__, forbidden))

    def test_h3_batch3_seed_data_is_untouched(self):
        self.assertEqual(len(seed.BOX_TYPES), 12)
        self.assertEqual(len(seed.PART_TEMPLATES), 31)
        self.assertEqual(len(seed.PROCESS_TEMPLATES), 23)
        self.assertEqual(len(seed.ACCESSORIES), 12)
        self.assertEqual(len(seed.LOGISTICS_RULES), 3)
        self.assertEqual(len(seed.MATERIALS), 5)
        exprs = {row["part_code"]: row for row in seed.PART_TEMPLATES}
        self.assertEqual(exprs["RB01001-P01"]["size_length_expr"], "L+4t+2c")
        self.assertEqual(exprs["RB01001-P01"]["size_width_expr"], "W+4t+2c")
        self.assertEqual(exprs["RB01001-P10"]["size_length_expr"], "长度 = W/3 + 40")
        self.assertEqual(exprs["RB02001-P08"]["size_expr"], "标准件")

    def test_h4_legacy_bom_tables_are_not_touched(self):
        self.save_requirement()
        self.confirm_box()
        self.bom().build_bom(PID, REQ_NO)
        for table in ("wip_bom_item", "wip_part", "wip_process_plan"):
            count = self.db_rows("SELECT COUNT(*) AS n FROM %s" % table)[0]["n"]
            self.assertEqual(count, 0, "包装 BOM 不得写 %s（Spec §6）" % table)

    def test_h5_batch4_contract_still_available(self):
        match = importlib.import_module("tech_app.backend.services.packaging_match")
        self.assertEqual(match.ENGINE_VERSION, "packaging_match_v1")
        self.assertEqual(set(match.BOX_MATCH_DECIDE_ROLES),
                         {"process_manager", "process_director", "admin"})
        self.assertTrue(hasattr(da_repo, "box_match_audit"))
        self.assertTrue(hasattr(kb_repo, "packaging_match_weights"))

    def test_h6_four_industries_intact(self):
        self.assertEqual(set(industry_templates.INDUSTRIES),
                         {"semiconductor", "battery", "appliance", "packaging"})
        import cpq_industries
        for key in ("semiconductor", "battery", "appliance"):
            profile = cpq_industries.INDUSTRIES[key]
            self.assertEqual(profile["cost_profile"], "generic_v1")
            self.assertEqual(profile["pricing_profile"], "generic_margin_v1")
        packaging = cpq_industries.INDUSTRIES["packaging"]
        self.assertEqual(packaging["cost_profile"], "packaging_v1")
        self.assertEqual(packaging["pricing_profile"], "packaging_margin_v1")


if __name__ == "__main__":
    unittest.main()
