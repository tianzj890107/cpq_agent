"""红测：成本账按「组成（section）」发号时，明细与单件/整单必须仍然对得上账。

Spec：`docs/specs/packaging-cost-ledger-section-reconciliation.md`

现状缺口（34 线上实测 `成本账`，不是推断）：

  · 项目 `6beeeebb19a1`（酒盒）跑到 2.3 之后，`cost-review` 返回
    `reconciled=false`、`reconciliation_errors` 8 条全是
    `unknown_part:DWG-BP10::section:5b917c0cdfad` / `...DWG-BP12::section:...`；
  · `counts = {parts: 28, parts_costed: 26, missing: [DWG-BP10, DWG-BP12]}`，
    而这 26 件的 `subtotal` 全是 0，`parts_total` 材料/人工全 0，只有
    `final` 的包装/运输/加工有数 —— 明细与总额因此对不上；
  · 于是 `POST /cost-review/confirm`、`/send-to-quote`、`/return-to-process`
    一律 400「包装成本明细与总额未对齐，不能确认或发送，请重新测算并核对」，
    连带卡死 3.x 报告：需求单 3 步已经审批通过、技术工艺总结也确认了，报告仍送不出去。

根因（`tech_app/backend/services/packaging_cost_ledger.py:45-52`）：
`project()` 用 `ledger['items'][i]['part_code']` 与业务部件清单做**字面**比对，
而多组成件的物料/工艺明细由 `packaging_sections.section_input_rows()` 以
`<父件号>::<section_id>`（`packaging_sections.py:283`）发号 —— 子件号不在
业务部件清单里，就被判成"未知零件"，同时父件因为查不到明细而被判"没算成本"。

本批只改对账口径：子件号归回父件、金额仍按组成逐行累加、总额与明细仍必须
逐分对齐；不得放宽"真未知零件"（`unknown_part:`）这条守卫，不得改成本公式、
费率、单位或 `packaging_sections` 的发号规则。

纪律：全部离线（不连 PG、不调模型、不起服务），只喂 `project(doc, ledger)` 纯函数。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LEDGER = "tech_app.backend.services.packaging_cost_ledger"
SECTION_MARK = "::section:"
CATEGORIES = ("material", "labor", "processing", "tooling", "packaging", "freight", "other")


def part(code, *, name="", quantity=1):
    return {"business_part_code": code, "name": name or code,
            "reference": {"quantity": quantity}}


def item(code, amount, category="material", *, parent=None, section=None, name=""):
    row = {"part_code": code, "amount": amount, "cost_category": category,
           "loss_rate": 0, "part_name": name or code}
    if parent:
        row["parent_part_code"] = parent
    if section:
        row["section_id"] = section
    return row


def ledger(items, *, built=True, stale=False, extra=None):
    """一份最小但字段齐全的公式账（category_* 与 total_cost 与明细一致）。"""
    totals = {key: 0.0 for key in CATEGORIES}
    for row in items:
        category = row.get("cost_category")
        key = category if category in CATEGORIES else "processing"
        totals[key] += float(row.get("amount") or 0)
    doc = {
        "built": built, "stale": stale,
        "items": list(items),
        "estimate_id": "pkgcost:test:REQ-1:default",
        "loss_base_scope": "",
        "gaps": [],
        "material_total": totals["material"], "labor_total": totals["labor"],
        "process_total": totals["processing"], "tooling_total": totals["tooling"],
        "packaging_total": totals["packaging"], "freight_total": totals["freight"],
        "other_total": totals["other"],
        "total_cost": sum(totals.values()),
    }
    doc.update(extra or {})
    return doc


def section_items(parent, sections, *, category="material", name=""):
    return [item(f"{parent}{SECTION_MARK}{sid}", amount, category,
                 parent=parent, section=SECTION_MARK + sid, name=name)
            for sid, amount in sections]


class LedgerCase(unittest.TestCase):
    maxDiff = None

    def module(self):
        try:
            return importlib.import_module(LEDGER)
        except ModuleNotFoundError as exc:                       # pragma: no cover
            self.fail("缺少 %s（Spec §2）：%s" % (LEDGER, exc))

    def project(self, doc, led):
        fn = getattr(self.module(), "project", None)
        self.assertTrue(callable(fn), "packaging_cost_ledger 必须导出 project(doc, ledger)（Spec §2）")
        return fn(doc, led)

    def errors(self, view):
        return list(view.get("reconciliation_errors") or [])

    def unknown(self, view):
        return [row for row in self.errors(view) if str(row).startswith("unknown_part:")]


class SectionChildCodesReconcile(LedgerCase):
    """A：多组成件的子件号必须归回父件。"""

    def test_a1_section_children_do_not_look_like_unknown_parts(self):
        doc = {"business_parts": [part("DWG-BP10", name="左盖面纸")]}
        led = ledger(section_items("DWG-BP10", [("aaa", 100.0), ("bbb", 50.0)]))
        view = self.project(doc, led)
        self.assertEqual([], self.unknown(view),
                         "子件号是同一父件的组成，不是未知零件：%s" % self.errors(view))
        self.assertTrue(view["reconciled"], "组成齐全就必须对得上账：%s" % self.errors(view))

    def test_a2_section_amounts_land_on_the_parent_part_row(self):
        doc = {"business_parts": [part("DWG-BP10", name="左盖面纸")]}
        led = ledger(section_items("DWG-BP10", [("aaa", 100.0), ("bbb", 50.0)]))
        view = self.project(doc, led)
        rows = {row["id"]: row for row in view["parts"]}
        self.assertIn("DWG-BP10", rows)
        row = rows["DWG-BP10"]
        self.assertTrue(row["has_cost"], "组成有明细，父件就算算过：%r" % row)
        self.assertAlmostEqual(row["subtotal"], 150.0, places=6,
                               msg="父件小计必须等于各组成之和：%r" % row)
        self.assertEqual([], list(view["counts"]["missing"]),
                         "组成齐全时不许再有 missing 件：%r" % view["counts"])
        self.assertEqual([], list(view["counts"]["zero"]),
                         "金额非零时不许被判成 0 元件：%r" % view["counts"])

    def test_a3_parts_total_and_category_totals_still_balance(self):
        doc = {"business_parts": [part("DWG-BP10", quantity=2), part("DWG-BP12", name="内盒2面纸")]}
        led = ledger(section_items("DWG-BP10", [("aaa", 100.0), ("bbb", 50.0)])
                     + section_items("DWG-BP12", [("ccc", 30.0)], name="内盒2面纸"))
        view = self.project(doc, led)
        self.assertTrue(view["reconciled"], self.errors(view))
        allocated = view["parts_total"]
        self.assertAlmostEqual(allocated["material"], 180.0, places=6)
        self.assertAlmostEqual(view["final"]["material"], 180.0, places=6)
        self.assertAlmostEqual(view["final"]["total"], 180.0, places=6)
        for key in CATEGORIES:
            self.assertAlmostEqual(view["project_expenses"][key], 0.0, places=6,
                                   msg="归属齐全时项目级费用必须归零：%r" % view["project_expenses"])

    def test_a4_quantity_still_divides_on_the_parent_row(self):
        doc = {"business_parts": [part("DWG-BP10", quantity=4)]}
        led = ledger(section_items("DWG-BP10", [("aaa", 100.0)]))
        view = self.project(doc, led)
        row = [r for r in view["parts"] if r["id"] == "DWG-BP10"][0]
        self.assertAlmostEqual(row["subtotal"], 100.0, places=6)
        self.assertAlmostEqual(row["unit_cost"], 25.0, places=6)


class UnknownPartsAreStillRejected(LedgerCase):
    """B：守卫不许被放宽 —— 真未知零件仍要拦住确认。"""

    def test_b1_truly_unknown_part_is_still_an_error(self):
        doc = {"business_parts": [part("DWG-BP10")]}
        led = ledger(section_items("DWG-BP10", [("aaa", 10.0)])
                     + [item("DWG-BP99", 5.0)])
        view = self.project(doc, led)
        self.assertFalse(view["reconciled"], "未知零件必须仍然对不上账")
        self.assertIn("unknown_part:DWG-BP99", self.errors(view))

    def test_b2_only_the_unknown_row_is_reported(self):
        doc = {"business_parts": [part("DWG-BP10"), part("DWG-BP12")]}
        led = ledger(section_items("DWG-BP10", [("aaa", 10.0)])
                     + section_items("DWG-BP12", [("bbb", 20.0)])
                     + [item("DWG-BP99", 5.0)])
        view = self.project(doc, led)
        self.assertEqual(["unknown_part:DWG-BP99"], self.unknown(view))

    def test_b3_section_of_an_unknown_parent_is_still_an_error(self):
        doc = {"business_parts": [part("DWG-BP10")]}
        led = ledger(section_items("DWG-BP99", [("aaa", 10.0)]))
        view = self.project(doc, led)
        self.assertFalse(view["reconciled"],
                         "父件都不在清单里时，子件号不许凭空归位")
        self.assertTrue(self.unknown(view), self.errors(view))

    def test_b4_project_level_rows_are_not_parts(self):
        doc = {"business_parts": [part("DWG-BP10")]}
        led = ledger(section_items("DWG-BP10", [("aaa", 10.0)])
                     + [item("", 7.0, "freight", name="运输"), {"part_code": None, "amount": 3.0,
                                                               "cost_category": "packaging",
                                                               "loss_rate": 0, "part_name": "包装"}])
        view = self.project(doc, led)
        self.assertEqual([], self.unknown(view), self.errors(view))
        self.assertTrue(view["reconciled"], self.errors(view))
        self.assertAlmostEqual(view["project_expenses"]["freight"], 7.0, places=6)
        self.assertAlmostEqual(view["project_expenses"]["packaging"], 3.0, places=6)


class ConfirmationGateStaysConsistent(LedgerCase):
    """C：`ready` 仍要求每件都算过、且没有 0 元件。"""

    def test_c1_unpriced_part_keeps_ready_false(self):
        doc = {"business_parts": [part("DWG-BP10"), part("DWG-BP12")]}
        led = ledger(section_items("DWG-BP10", [("aaa", 10.0)]))
        view = self.project(doc, led)
        self.assertTrue(view["reconciled"], self.errors(view))
        self.assertFalse(view["ready"], "还有零件没算成本时不许 ready")
        self.assertEqual(["DWG-BP12"], list(view["counts"]["missing"]))

    def test_c2_zero_amount_part_keeps_ready_false(self):
        doc = {"business_parts": [part("DWG-BP10")]}
        led = ledger(section_items("DWG-BP10", [("aaa", 0.0)]))
        view = self.project(doc, led)
        self.assertTrue(view["reconciled"], self.errors(view))
        self.assertFalse(view["ready"], "算出来是 0 元的件不许 ready")
        self.assertEqual(["DWG-BP10"], list(view["counts"]["zero"]))


if __name__ == "__main__":
    unittest.main()
