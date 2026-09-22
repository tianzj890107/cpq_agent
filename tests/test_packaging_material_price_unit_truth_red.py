"""红测：材料价格的「计价单位」必须被核对、留痕、并在单位不对时拦住这一行
（Spec `packaging-material-price-unit-truth.md`）。

现状缺口（代码级，可指到行）：
  · `tech_app/backend/services/packaging_cost.py:1706` 的 `_material_price()` 取回整条价格行，
    调用点（`:2113-2119`）只读 `price` —— `kb_material_price.unit` 一个字都没被看过；
  · `:2119` 的 `ton_price = price * 1000` 把「单价必须是 元/kg」写死进算式
    （`packaging-cost-engine.md` §2.3 的冻结合同），单位写成「吨」也照旧 ×1000；
  · 材料行的 `inputs_json` 里没有 `price_unit` / `price_unit_status`，事后回查不了口径；
  · `GAP_RESOLUTIONS` 里 `material_price_missing` 的动作**没写单位要求**（与 §5 表不一致）。

夹具复用既有冻结测试模块 `tests.test_packaging_cost_gaps_red`（`CostGapCase` + 真实种子快照），
不复制常量、不连线上库。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.storage import da_seed_packaging as seed  # noqa: E402
from tests.test_packaging_cost_engine_red import PID, REQ_NO  # noqa: E402
from tests.test_packaging_cost_gaps_red import (  # noqa: E402
    CostGapCase, prices_with_valid_from)

GREYBOARD = "MAT-PKG-GREYBOARD"
GREYBOARD_PART = "RB01001-P01"
MISMATCH_CODE = "material_price_unit_mismatch"
MISSING_CODE = "material_price_unit_missing"
STATUSES = ("ok", "unit_missing", "unit_mismatch")

#: 本批**不许**动的既有登记（逐字对照，改一个字都算回退）。
FROZEN_RESOLUTIONS = {
    "material_price_missing": {
        "missing_variable": ["price"],
        "resolution_action": "在物料主数据里补该材料的权威单价",
        "entry": "kb_material_price", "severity": "blocking"},
    "material_gsm_missing": {
        "missing_variable": ["gsm"],
        "resolution_action": "补材料克重/厚度换算（灰板必须给 GSM）",
        "entry": "kb_material", "severity": "blocking"},
    "loss_rate_missing": {
        "missing_variable": ["loss_rate"],
        "resolution_action": "补该材料的损耗率（或确认按 0 计并签字）",
        "entry": "kb_cost_factor", "severity": "advisory"},
    "part_size_missing": {
        "missing_variable": ["length_mm", "width_mm"],
        "resolution_action": "补零件展开尺寸（2.1 零件提取或盒型尺寸确认）",
        "entry": "packaging-parts", "severity": "blocking"},
    "no_formula": {
        "missing_variable": [], "resolution_action": "该类别还没有公式：先抽规则再算",
        "entry": "packaging_cost_rules.json", "severity": "blocking"},
}
#: `ton_price` 的算式（`packaging-cost-engine.md` §2.3 的冻结合同）。
FROZEN_TON_PRICE_LINE = '"ton_price": (price * 1000) if price is not None else None,'


def prices_with_unit(code, unit, *, drop=False):
    """种子价格（带 `valid_from`）里把某条材料的价格单位改掉 / 删掉，其余逐字不变。"""
    rows = []
    for row in prices_with_valid_from():
        if str(row.get("material_code") or "") == code:
            row = dict(row)
            if drop:
                row.pop("unit", None)
            else:
                row["unit"] = unit
        rows.append(row)
    return rows


class UnitTruthCase(CostGapCase):
    def module(self):
        module = self.cost_mod()
        if not hasattr(module, "material_price_unit_status"):
            raise AssertionError(
                "缺少 packaging_cost.material_price_unit_status()（Spec §C1）")
        return module

    def project_with_unit(self, unit=None, *, drop=False):
        over = {}
        if drop or unit is not None:
            over["kb_material_price"] = prices_with_unit(GREYBOARD, unit, drop=drop)
        self.prepare()
        if over:
            self.snapshot(**over)
        return self.cost_mod().compute_project(PID, REQ_NO)

    def gaps_for(self, cost, code, where=None):
        out = []
        for item in cost.get("gaps") or []:
            if str(item.get("code")) != code:
                continue
            if where and str(item.get("where")) != where:
                continue
            out.append(item)
        return out


# --------------------------------------------------------------------------- #
# A 组：`material_price_unit_status()`（闭集三值、不给默认单位、不抛错）
# --------------------------------------------------------------------------- #
class AUnitStatus(unittest.TestCase):
    def setUp(self):
        self.module = self._load()

    @staticmethod
    def _load():
        import importlib
        try:
            return importlib.import_module("tech_app.backend.services.packaging_cost")
        except Exception as exc:                                   # pragma: no cover
            raise AssertionError("缺少 tech_app/backend/services/packaging_cost.py：%s" % exc)

    def status(self, row):
        fn = getattr(self.module, "material_price_unit_status", None)
        if fn is None:
            raise AssertionError("缺少 packaging_cost.material_price_unit_status()（Spec §C1）")
        return fn(row)

    def test_a1_kg_writings_are_ok(self):
        for row in ({"price": 6.5, "unit": "kg"}, {"price": 6.5, "unit": "KG"},
                    {"price": 6.5, "unit": "  Kg  "}, {"price": 6.5, "unit": "千克"},
                    {"price": 6.5, "unit": "公斤"}):
            self.assertEqual(("ok", "kg"), self.status(row),
                             "kg / 千克 / 公斤（大小写与空白归一）都算核对通过（Spec §C1）：%r" % row)

    def test_a2_missing_unit_is_not_ok(self):
        for row in ({"price": 6.5}, {"price": 6.5, "unit": ""}, {"price": 6.5, "unit": "   "},
                    {"price": 6.5, "unit": None}, {"price": 6.5, "unit": 123}):
            self.assertEqual(("unit_missing", ""), self.status(row),
                             "没写单位就是没核对过，不许当 kg（Spec §C1 / §4）：%r" % row)

    def test_a3_non_dict_input_never_raises(self):
        for row in (None, "kg", [], 123, True):
            self.assertEqual(("unit_missing", ""), self.status(row),
                             "不是价格行 → 没核对过（Spec §C1）：%r" % row)

    def test_a4_other_units_are_a_mismatch_with_the_raw_text(self):
        for unit in ("吨", "ton", "t", "元/米", "元/张", "美元/kg"):
            status, text = self.status({"price": 6.5, "unit": unit})
            self.assertEqual("unit_mismatch", status,
                             "只有 kg 与它的中文写法算通过，其余一律 mismatch（Spec §C1）：%s" % unit)
            self.assertEqual(unit, text, "原文逐字回给用户（Spec §C1）：%s" % unit)

    def test_a5_statuses_and_expected_unit_are_a_closed_set(self):
        self.assertEqual("kg", getattr(self.module, "MATERIAL_PRICE_UNIT_EXPECTED", None),
                         "公式要求的规范单位是 kg（Spec §C1）")
        self.assertEqual(STATUSES, tuple(getattr(self.module, "MATERIAL_PRICE_UNIT_STATUSES", ())),
                         "状态是闭集三值（Spec §C1）")

    def test_a6_ton_is_not_an_alias(self):
        aliases = getattr(self.module, "MATERIAL_PRICE_UNIT_ALIASES", {}) or {}
        for bad in ("吨", "ton", "t", "mt"):
            self.assertNotIn(bad, aliases, "吨/ton 不许当别名（Spec §C1 / §4）：%s" % bad)
        self.assertEqual("kg", aliases.get("kg"), "kg 必须在别名表里（Spec §C1）")

    def test_a7_function_stays_pure(self):
        source = inspect.getsource(self.module.material_price_unit_status)
        for forbidden in ("kb_repo", "get_backend(", "open(", "requests", "urllib", "print("):
            self.assertNotIn(forbidden, source,
                             "核对单位是纯函数：不许出现 %s（Spec §C1）" % forbidden)

    def test_a8_expected_unit_is_not_overridable(self):
        parameters = list(inspect.signature(self.module.material_price_unit_status).parameters)
        self.assertEqual(["price_row"], parameters,
                         "只吃一条价格行；期望单位不许由入参覆盖（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：行级行为（kg 逐字不变 / 缺单位披露 / 单位不对拦住）
# --------------------------------------------------------------------------- #
class BLineLevel(UnitTruthCase):
    def test_b1_kg_rows_are_unchanged(self):
        cost = self.project()
        codes = self.gap_codes(cost)
        self.assertNotIn(MISMATCH_CODE, codes, "种子里全是 kg：不许平白多出单位缺口（Spec §C4）")
        self.assertNotIn(MISSING_CODE, codes, "种子里全是 kg：不许平白多出单位缺口（Spec §C4）")
        line = self.material_line(cost, GREYBOARD_PART)
        inputs = self.inputs_of(line)
        self.assertEqual("kg", inputs.get("price_unit"), "材料行要说得出这一版按什么单位算（Spec §C2）")
        self.assertEqual("ok", inputs.get("price_unit_status"), "单位核对结果要留痕（Spec §C2）")
        self.assertIsNotNone(line.get("amount"), "kg 的行金额一字不变（Spec §C4）")

    def test_b2_missing_unit_is_disclosed_but_still_computed(self):
        cost = self.project_with_unit(drop=True)
        gaps = self.gaps_for(cost, MISSING_CODE, GREYBOARD_PART)
        self.assertEqual(1, len(gaps), "单位没核对过必须说出来（Spec §C2）：%s" % self.gap_codes(cost))
        line = self.material_line(cost, GREYBOARD_PART)
        self.assertEqual("unit_missing", self.inputs_of(line).get("price_unit_status"),
                         "行上要留痕（Spec §C2）")
        self.assertIsNotNone(line.get("amount"),
                             "缺单位是 advisory：今天不拦算，本批不悄悄改行为（Spec §C2 / §6.3）")

    def test_b3_mismatched_unit_blocks_the_row(self):
        cost = self.project_with_unit("吨")
        gaps = self.gaps_for(cost, MISMATCH_CODE, GREYBOARD_PART)
        self.assertEqual(1, len(gaps), "单位不是 kg 必须拦住这一行（Spec §C2）：%s"
                                      % self.gap_codes(cost))
        line = self.material_line(cost, GREYBOARD_PART)
        self.assertIsNone(line.get("amount"), "单位不对就不许 ×1000 出金额（Spec §C2）")
        self.assertEqual("unit_mismatch", self.inputs_of(line).get("price_unit_status"),
                         "行上要留痕（Spec §C2）")

    def test_b4_blocked_detail_names_the_unit_and_the_expected_one(self):
        cost = self.project_with_unit("吨")
        gap = self.gaps_for(cost, MISMATCH_CODE, GREYBOARD_PART)[0]
        detail = str(gap.get("detail") or "")
        self.assertIn("吨", detail, "要说清这条价格写的是什么单位（Spec §C2）：%s" % detail)
        self.assertIn("kg", detail, "要说清公式要求什么单位（Spec §C2）：%s" % detail)

    def test_b5_mismatch_and_price_missing_are_exclusive(self):
        cost = self.project_with_unit("吨")
        self.assertNotIn("material_price_missing", self.gap_codes(cost),
                         "价格在、只是单位不对：不许再报一次「没有价格」（Spec §C2）")

    def test_b6_no_unit_gap_when_there_is_no_price_at_all(self):
        self.prepare()
        self.snapshot(kb_material_price=[row for row in prices_with_valid_from()
                                         if str(row.get("material_code")) != GREYBOARD])
        cost = self.cost_mod().compute_project(PID, REQ_NO)
        codes = self.gap_codes(cost)
        self.assertIn("material_price_missing", codes, "没有价格照旧报缺价格（Spec §C2）")
        self.assertNotIn(MISSING_CODE, codes,
                         "没有价格行就无从核对单位，不许再报一条单位缺口（Spec §C2）")
        self.assertNotIn(MISMATCH_CODE, codes,
                         "没有价格行就无从核对单位（Spec §C2）")


# --------------------------------------------------------------------------- #
# C 组：登记与冻结面
# --------------------------------------------------------------------------- #
class CRegistryAndFreeze(UnitTruthCase):
    def test_c1_new_codes_are_registered_with_four_keys(self):
        module = self.module()
        for code, severity in ((MISMATCH_CODE, "blocking"), (MISSING_CODE, "advisory")):
            entry = module.GAP_RESOLUTIONS.get(code)
            self.assertIsInstance(entry, dict, "缺口登记必须有两处落点（Spec §C3）：%s" % code)
            self.assertEqual(severity, entry.get("severity"), "严重度按 §C3：%s" % code)
            self.assertEqual("kb_material_price", entry.get("entry"),
                             "补数入口指向价格表（Spec §C3）：%s" % code)
            self.assertTrue(str(entry.get("resolution_action") or "").strip(),
                            "必须有可执行动作（Spec §C3）：%s" % code)
            self.assertIn("missing_variable", entry, "四键齐备（Spec §C3）：%s" % code)

    def test_c2_mismatch_action_names_the_expected_unit(self):
        action = str(self.module().GAP_RESOLUTIONS[MISMATCH_CODE].get("resolution_action") or "")
        self.assertIn("kg", action, "动作要点名改成 元/kg（Spec §C3）：%s" % action)

    def test_c3_existing_registrations_are_untouched(self):
        module = self.module()
        for code, frozen in FROZEN_RESOLUTIONS.items():
            self.assertEqual(frozen, module.GAP_RESOLUTIONS.get(code),
                             "既有登记一字不动（Spec §C3）：%s" % code)

    def test_c4_ton_price_expression_is_frozen(self):
        source = pathlib.Path(module_path(self)).read_text(encoding="utf-8")
        self.assertIn(FROZEN_TON_PRICE_LINE, source,
                      "`ton_price = 单价 × 1000` 是冻结合同，一个字不许改（Spec §C4）")

    def test_c5_price_lookup_is_unchanged(self):
        source = inspect.getsource(self.module()._material_price)
        self.assertIn("kb_repo.current_price", source, "取价仍走既有入口（Spec §C4）")
        self.assertIn("industry=PACKAGING_INDUSTRY", source, "行业继承口径未变（Spec §C4）")

    def test_c6_seed_units_are_not_rewritten(self):
        units = []
        for item in seed.MATERIALS:
            units.append(str((item.get("price") or {}).get("unit") or ""))
        self.assertEqual(["kg"] * len(units), units,
                         "不许为了转绿改种子数据的单位（Spec §C4）")

    def test_c7_no_new_dependency(self):
        source = pathlib.Path(module_path(self)).read_text(encoding="utf-8")
        for forbidden in ("import requests", "import urllib", "import numpy", "openpyxl"):
            self.assertNotIn(forbidden, source, "不加依赖（Spec §C4）：%s" % forbidden)


def module_path(case):
    module = case.module()
    return pathlib.Path(inspect.getsourcefile(module))


if __name__ == "__main__":
    unittest.main()
