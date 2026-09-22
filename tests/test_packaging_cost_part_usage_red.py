"""红测：BOM 行上的「用量」必须进成本，或缺用量必须披露。

Spec：`docs/specs/packaging-cost-part-usage-not-applied.md`
依赖：`packaging-cost-engine.md`（材料行公式与输入）、
`packaging-bom-business-parts-rows.md`（权威清单的用量落进 `quantity` 列）。

现状缺口（代码级，见 Spec §1）：
  · `packaging_cost.py` 的「部件 × 材料」循环的 `variables` 里没有这一行的 `quantity`
    （只有 `cut_length`/`cut_width`/`gsm`/`ton_price`/`imposition_count`/`quote_quantity`…）——
    同一件用量 1 与用量 8 的材料金额完全相同；
  · `quantity` 列确实存在、2.1 面板也确实显示成「数量 8」；
  · 取不到用量时没有任何缺口。

夹具**复用**冻结模块 `tests.test_packaging_cost_engine_red.CostCase`（真链路：种子 KB +
临时 SQLite + meta 沙盘 + 真 BOM / 真路线），材料取种子里的灰板（有克重推导与单价）。
纪律：不连 PG / 34、不发 HTTP、不写业务数据；禁止为了让红测转绿而改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.storage import da_seed_packaging as seed  # noqa: E402
from tests.test_packaging_cost_engine_red import CostCase, PID, REQ_NO  # noqa: E402

#: 种子材料：MAT-PKG-GREYBOARD「灰板（双灰纸板）」t2.0 / 密度 0.75 / 6.5 元每 kg
MATERIAL = seed.MATERIALS[0]["material"]
MATERIAL_NAME = MATERIAL["name"]
MATERIAL_CODE = MATERIAL["material_code"]


def price_rows():
    """种子价格的形状 + `valid_from`（`kb_repo.current_price()` 会跳过没有生效日的行）。"""
    return [dict(seed.MATERIALS[0]["price"], material_code=MATERIAL_CODE, price_id=1,
                 industry="packaging", valid_from="2026-01-01", valid_to=None)]


def bom_row(item_key, quantity, *, length=500.0, width=400.0):
    """一行部件：材料能在种子里解析出克重与单价，尺寸齐备。"""
    return {"industry": "packaging", "engine_version": "packaging_bom_v1",
            "bom_category": "box_part", "item_key": item_key,
            "item_name": "灰板件 %s" % item_key, "part_code": item_key,
            "material": MATERIAL_NAME, "material_code": MATERIAL_CODE,
            "quantity": quantity, "unit": "件",
            "length_mm": length, "width_mm": width, "height_mm": 0.0,
            "size_source_json": "{\"kind\": \"kb_template\"}",
            "status": "computed", "missing_variables": "[]",
            "is_optional": 0, "locked": 0, "source": "kb_packaging_part_template"}


class PartUsageCase(CostCase):
    """真链路把成本算出来，只把 BOM 行换成受控的两行。"""

    def build_with(self, rows):
        self.snapshot(kb_material_price=price_rows())
        self.prepare()
        module = self.cost_mod()
        rows = [dict(row) for row in rows]
        with mock.patch.object(module.da_repo, "load_packaging_bom",
                               lambda *a, **k: [dict(row) for row in rows]):
            return module.build_cost(PID, requirement_no=REQ_NO)

    @staticmethod
    def material_lines(result):
        return [row for row in (result.get("items") or [])
                if row.get("cost_category") == "material"]

    def by_part(self, result):
        return {line.get("part_code"): line for line in self.material_lines(result)}

    @staticmethod
    def inputs_of(line):
        """行上的输入留痕：落库是 `inputs_json`（JSON 串），内存/读回体里可能是 `inputs`。"""
        import json as _json
        raw = line.get("inputs_json")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = _json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and parsed:
                return parsed
        value = line.get("inputs")
        return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- #
# A. 用量要进金额
# --------------------------------------------------------------------------- #
class AUsageReachesTheAmount(PartUsageCase):
    def test_a1_every_material_line_carries_the_rows_usage(self):
        result = self.build_with([bom_row("P-Q1", 1), bom_row("P-Q8", 8)])
        lines = self.by_part(result)
        self.assertEqual({"P-Q1", "P-Q8"}, set(lines), "两行部件应当出两条材料行")
        self.assertEqual(1.0, self.inputs_of(lines["P-Q1"]).get("usage_qty"),
                         "用量为 1 的行也要有 usage_qty（键必须存在）")
        self.assertEqual(8.0, self.inputs_of(lines["P-Q8"]).get("usage_qty"),
                         "这一行的用量必须在成本输入里留痕（Spec §2.1）："
                         "现在输入里根本没有 quantity 这个量")

    def test_a2_amount_scales_with_the_rows_usage(self):
        result = self.build_with([bom_row("P-Q1", 1), bom_row("P-Q8", 8)])
        lines = self.by_part(result)
        single = lines["P-Q1"].get("amount")
        many = lines["P-Q8"].get("amount")
        self.assertIsNotNone(single, "灰板能取到克重与单价，材料行必须出金额")
        self.assertGreater(float(single), 0.0,
                           "单件金额必须 > 0，否则比例断言会假通过")
        self.assertAlmostEqual(8 * float(single), float(many), places=6,
                               msg="同一件用量 8 的材料费必须是用量 1 的 8 倍（Spec §2.1）："
                                   "现在两边完全相同")

    def test_a3_missing_usage_is_disclosed(self):
        result = self.build_with([bom_row("P-QNONE", None)])
        line = self.by_part(result)["P-QNONE"]
        self.assertEqual(1.0, self.inputs_of(line).get("usage_qty"),
                         "取不到用量按 1 计（Spec §2.1）")
        codes = [gap.get("code") for gap in (result.get("gaps") or [])
                 if isinstance(gap, dict)]
        self.assertIn("usage_qty_missing", codes,
                      "取不到用量必须披露（Spec §2.2）：不许悄悄按 1 件算")


# --------------------------------------------------------------------------- #
# B. 护栏：用量 1 / 没用量的行逐字不变
# --------------------------------------------------------------------------- #
class BGuards(PartUsageCase):
    def test_b1_usage_one_amount_and_expression_are_unchanged(self):
        with_qty = self.by_part(self.build_with([bom_row("P-Q1", 1)]))["P-Q1"]
        without_qty = self.by_part(self.build_with([bom_row("P-Q0", None)]))["P-Q0"]
        self.assertAlmostEqual(float(without_qty.get("amount")), float(with_qty.get("amount")),
                               places=9,
                               msg="用量为 1 与没有用量的行金额必须一致（Spec §2.3）")
        for key in ("cut_length", "cut_width", "gsm", "ton_price",
                    "quote_quantity", "imposition_count"):
            self.assertIn(key, self.inputs_of(with_qty), "既有 inputs 键逐字不变：%s" % key)

    def test_b2_formula_text_keeps_the_workbook_wording(self):
        line = self.by_part(self.build_with([bom_row("P-Q8", 8)]))["P-Q8"]
        expression = str(line.get("expression") or "")
        self.assertIn("cut_length*cut_width", expression,
                      "表达式仍是工作簿原文（Spec §2.1）")
        self.assertNotIn("usage_qty", expression,
                         "用量不许被塞进表达式字符串（Spec §2.1）")

    def test_b3_other_categories_are_untouched(self):
        result = self.build_with([bom_row("P-Q8", 8)])
        # 包材行（`content_code` 非空）本来就走内容表的 `usage_qty`，不属本批范围（Spec §6）。
        other = [row for row in (result.get("items") or [])
                 if row.get("cost_category") != "material"
                 and not str(row.get("content_code") or "")]
        self.assertTrue(other, "工序 / 人工等其它类别照旧出账")
        for line in other:
            self.assertNotIn("usage_qty", self.inputs_of(line),
                             "只有材料行带这一件的用量（Spec §2.3 / §6）：%s"
                             % line.get("cost_category"))


if __name__ == "__main__":
    unittest.main()
