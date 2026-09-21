"""红测：包装成本缺口里"引擎自己能算却算不出来"的那一类 —— 灰板克重按「厚度 × 密度」推导。

Spec：docs/specs/packaging-cost-gaps-closure.md
依赖：包装第 7 批（成本引擎）已完成。

现状缺口（34 上真跑 + 本机种子全量复算，两处结果一致）：
  · 34 上 24 条缺口里有 **6 条 `material_gsm_missing`**：`灰板 2.0mm` 的材料行 `grade = "2.0mm"`、
    `kb_material.density = 0.75 g/cm³`，克重 = 2.0 × 0.75 × 1000 = **1500 g/㎡** 完全算得出来，
    但 `_material_gsm()` 只认 `gsm` 属性与 `grade` 里的 `NNNg` → 6 行纸板全部不出金额。
  · `EVA 片材`（`grade = "38°"`）的厚度 `10mm` 只写在 `kb_material_property` 里，而
    `_material_rows()` **不 join** 属性表 → 同样永远报缺口。
  · 顺带更正两处我上一轮的误判（**不是**实现缺口，本批不许动）：
    `content_formula_error:PKG-P-*` 的 7 条是源工作簿 `包装运输!D..G` 本来就空（不是变量绑定 bug）；
    `packaging_route._AGGREGATE_EXPANSION` 比对的 `覆膜/烫金/UV 上光` 正是 `SURFACE_REQUIREMENTS`
    的取值，不是死规则。

夹具复用既有冻结测试模块 `tests.test_packaging_cost_engine_red`（`CostCase` + 真实种子快照），
不复制常量、不连线上库。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.storage import da_seed_packaging as seed  # noqa: E402
from tests.test_packaging_cost_engine_red import CostCase, PID, REQ_NO  # noqa: E402

GREYBOARD = "MAT-PKG-GREYBOARD"          # grade=2.0mm, density=0.75 → 1500 g/㎡
GREYBOARD_GSM = 1500.0
EVA = "MAT-PKG-EVA"                      # grade=38°, 厚度 10mm 在属性表, density=0.94 → 9400 g/㎡
EVA_GSM = 9400.0
SPECIAL = "MAT-PKG-SPECIAL"              # grade=120g（有 gsm 就不许推导）

#: `compute_packaging(seed.COST_CONTENTS)` 今天报出来的 7 条（冻结事实：源工作簿本来就空）
CONTENT_ERROR_CODES = (
    "content_formula_error:PKG-P-BAG",
    "content_formula_error:PKG-P-BOARD",
    "content_formula_error:PKG-P-CORNER-PAPER",
    "content_formula_error:PKG-P-CORNER-TOP",
    "content_formula_error:PKG-P-CRAFT-PAPER",
    "content_formula_error:PKG-P-DIVIDER",
    "content_formula_error:PKG-P-LABEL",
)


def prices_with_valid_from():
    """种子价格 + `valid_from`：真实库里每行都有，冻结夹具漏了这一列（不是实现缺口）。"""
    rows = []
    for index, item in enumerate(seed.MATERIALS):
        row = dict(item["price"], material_code=item["material"]["material_code"],
                   price_id=index + 1)
        row.setdefault("valid_from", "2026-01-01 00:00:00")
        rows.append(row)
    return rows


def materials_without(code, *columns):
    rows = []
    for item in seed.MATERIALS:
        row = dict(item["material"], industry="packaging", status="active")
        if row.get("material_code") == code:
            for column in columns:
                row.pop(column, None)
        rows.append(row)
    return rows


def materials_without_property(code, prop_key):
    """去掉某条材料在属性表里的某个属性（其余材料不动）。"""
    rows = []
    for item in seed.MATERIALS:
        for prop in item.get("properties") or []:
            if (_text(item["material"]["material_code"]) == code
                    and _text(prop.get("prop_key")) == prop_key):
                continue
            rows.append(dict(prop, industry="packaging"))
    return rows


def materials_without_price(code):
    return [row for row in prices_with_valid_from()
            if _text(row.get("material_code")) != code]


def _text(value):
    return "" if value is None else str(value).strip()


class CostGapCase(CostCase):
    """共用：把"价格可见"修好（夹具缺列），余下缺口才是被验的对象。"""

    def snapshot(self, **overrides):
        """每次换快照都要把"价格可见"带上：`CostCase.snapshot()` 会整体重置表，夹具本身缺
        `valid_from`（真实库每行都有），漏了就会把缺价格误当成缺克重。"""
        overrides.setdefault("kb_material_price", prices_with_valid_from())
        return super().snapshot(**overrides)

    # ---- 便捷调用 ---------------------------------------------------------- #
    def project(self, **over):
        self.prepare(**over)
        return self.cost_mod().compute_project(PID, REQ_NO)

    @staticmethod
    def gap_codes(cost):
        return [str(item.get("code")) for item in (cost.get("gaps") or [])]

    @staticmethod
    def material_line(cost, part_code):
        for item in cost.get("items") or []:
            if item.get("cost_category") == "material" and item.get("part_code") == part_code:
                return item
        raise AssertionError("成本明细里没有部件 %s 的材料行" % part_code)

    @staticmethod
    def inputs_of(item):
        raw = item.get("inputs_json")
        if isinstance(raw, dict):
            return raw
        return json.loads(raw or "{}")


# --------------------------------------------------------------------------- #
# G 组：克重按「厚度 × 密度」推导
# --------------------------------------------------------------------------- #
class GGsmDerivation(CostGapCase):
    def test_g1_greyboard_gsm_is_derived_from_thickness_and_density(self):
        cost = self.project()
        line = self.material_line(cost, "RB01001-P01")
        inputs = self.inputs_of(line)
        self.assertAlmostEqual(float(inputs.get("gsm") or 0.0), GREYBOARD_GSM, places=1,
                               msg="灰板 2.0mm × 0.75 g/cm³ × 1000 必须算成 1500 g/㎡（Spec §3.1）")
        self.assertEqual(inputs.get("gsm_source"), "derived_from_thickness_density",
                         "推导值必须留痕 gsm_source（Spec §3.2）")
        self.assertIsNotNone(line.get("amount"),
                             "克重算得出来，这一行的金额就必须出得来")

    def test_g2_derived_gsm_is_reproducible(self):
        line = self.material_line(self.project(), "RB01001-P04")
        inputs = self.inputs_of(line)
        gsm = float(inputs.get("gsm") or 0.0)
        thickness, density = 2.0, 0.75
        self.assertAlmostEqual(gsm, round(thickness * density * 1000, 1), places=1,
                               msg="推导式只认 厚度(mm) × 密度(g/cm³) × 1000（Spec §3.1）")

    def test_g3_no_material_gsm_gap_for_greyboard_rows(self):
        cost = self.project()
        missing = [code for code in self.gap_codes(cost) if code == "material_gsm_missing"]
        self.assertEqual(missing, [],
                         "纸板克重算得出来之后，material_gsm_missing 不许再出现：%s"
                         % self.gap_codes(cost))

    def test_g4_grade_gsm_wins_over_derivation(self):
        cost = self.project()
        line = self.material_line(cost, "RB01001-P07")
        inputs = self.inputs_of(line)
        self.assertAlmostEqual(float(inputs.get("gsm") or 0.0), 120.0, places=1,
                               msg="特种纸 grade=120g：有克重就不许用推导值覆盖（Spec §3.1）")
        self.assertEqual(inputs.get("gsm_source"), "grade")

    def test_g5_eva_thickness_comes_from_the_property_table(self):
        cost = self.project()
        line = self.material_line(cost, "RB01001-P09")
        inputs = self.inputs_of(line)
        self.assertAlmostEqual(float(inputs.get("gsm") or 0.0), EVA_GSM, places=1,
                               msg="EVA 的厚度 10mm 在属性表、密度 0.94 在材料列：必须 join 出来"
                                   "（Spec §1.3、§2.2）")
        self.assertEqual(inputs.get("gsm_source"), "derived_from_thickness_density")


# --------------------------------------------------------------------------- #
# H 组：护栏（本批只让"能算的行"能算，不许顺手补别的数、也不许把缺口藏起来）
# --------------------------------------------------------------------------- #
class HGuardrails(CostGapCase):
    def test_h1_missing_density_still_a_gap(self):
        self.snapshot(kb_material=materials_without(GREYBOARD, "density"),
                      kb_material_property=materials_without_property(GREYBOARD, "thickness"))
        cost = self.project()
        codes = self.gap_codes(cost)
        for part in ("RB01001-P01", "RB01001-P04", "RB01001-P05", "RB01001-P06"):
            self.assertIn("material_gsm_missing", codes,
                          "厚度与密度都不是「明写的数」时必须照旧报缺口，不许猜密度（Spec §3.1）")
        self.assertIsNone(self.material_line(cost, "RB01001-P01").get("amount"),
                          "推导不出来就不许出金额")

    def test_h2_missing_price_is_not_patched_by_this_batch(self):
        self.snapshot(kb_material_price=materials_without_price(GREYBOARD))
        cost = self.project()
        codes = self.gap_codes(cost)
        self.assertIn("material_price_missing", codes,
                      "缺价格的行照旧是缺口：克重算得出来不等于金额出得来（Spec §3.3）")
        self.assertIsNone(self.material_line(cost, "RB01001-P01").get("amount"))

    def test_h3_content_gaps_are_not_hidden(self):
        module = self.cost_mod()
        report = module.compute_packaging(seed.COST_CONTENTS)
        codes = [str((line.get("gap") or {}).get("code")) for line in report["lines"]]
        for code in CONTENT_ERROR_CODES:
            self.assertIn(code, codes,
                          "包材项在源工作簿里就是空值，缺口不许被降级或静默（Spec §1.1/§4）")

    def test_h4_print_and_tooling_gaps_are_not_patched(self):
        cost = self.project()
        codes = self.gap_codes(cost)
        self.assertIn("no_formula:print", codes,
                      "print 是手填列：本批不许给它造默认值（Spec §4）")
        self.assertIn("tooling_basis_missing:T-PKG-DIE-REFUND", codes,
                      "刀模分摊基数要商务给，本批不许拍（Spec §5）")

    def test_h5_material_rows_keep_their_columns(self):
        module = self.cost_mod()
        rows = {str(row.get("material_code")): row for row in module._material_rows()}
        self.assertAlmostEqual(float(rows[GREYBOARD].get("density")), 0.75, places=6,
                               msg="join 属性表不许改材料行的既有列")
        self.assertEqual(_text(rows[GREYBOARD].get("grade")), "2.0mm")
        self.assertTrue(isinstance(rows[GREYBOARD].get("properties"), list),
                        "材料行必须带 properties 列表（Spec §2.2）")


if __name__ == "__main__":
    unittest.main()
