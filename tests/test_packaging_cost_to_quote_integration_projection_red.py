"""红测：包装账算完并签字之后，"整机成本"不能还是空的 —— 否则回传销售永远走不通。

Spec：`docs/specs/packaging-cost-to-quote-integration-projection.md`

现状缺口（34 线上实测，项目 `6beeeebb19a1`，2026-10-12）：

  · 包装账已经算齐：`packaging_cost_ledger.load()` → `reconciled=true`、
    `material_total=11.46`、`total_cost=15.60`，`POST /cost-review/confirm` 通过；
  · 但同一项目的 `integration.load_plan().cost` 是 `None` —— 包装 2.3 的
    「整机成本测算」只调 `packaging_cost.build_cost()`，从不回填集成计划成本；
  · 于是 `cost_flow.send_to_quote()` 在 `integration_ready_cost()` 上被
    「请先完成整机成本测算，再写入数据库或发送至报价」拦死，报价侧收不到这次技术结果。

本批只补这一条投影：包装项目的整机成本 = 同一份包装账（不重算、不复制算法、
不覆盖已存在的人工/模型测算）。

夹具复用既有冻结测试模块 `tests.test_packaging_cost_engine_red`（`CostCase`），
不复制常量、不连线上库、不调模型。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.models.cost import CostAnalysis  # noqa: E402
from tech_app.backend.services import cost_flow, cost_model, integration  # noqa: E402
from tech_app.backend.services import packaging_cost_ledger  # noqa: E402
from tests.test_packaging_cost_engine_red import CostCase, PID  # noqa: E402


class ProjectionCase(CostCase):
    """包装账已算齐（材料费 > 0）的现场；集成计划里从来没有整机成本。"""

    def built_ledger(self):
        self.prepare()
        self.build_cost()
        self.assertIsNone(integration.load_plan(PID).cost,
                          "夹具要求这个项目的集成计划里本来没有整机成本")
        view = packaging_cost_ledger.load(PID)
        ledger = view.get("packaging_cost") or {}
        self.assertTrue(ledger.get("built"), "夹具要求包装账已经算过")
        self.assertFalse(ledger.get("stale"), "夹具要求包装账不是过期版本")
        self.assertGreater(float((view.get("final") or {}).get("total") or 0), 0,
                           "夹具必须先算出一份合计不为 0 的包装账")
        return view


class A包装账投影(ProjectionCase):

    def test_a1_built_packaging_ledger_satisfies_integration_ready_cost(self):
        self.built_ledger()
        try:
            plan = cost_flow.integration_ready_cost(PID)
        except Exception as exc:                                    # noqa: BLE001
            self.fail("包装账已算齐并签字，却被「请先完成整机成本测算」拦住：%s" % exc)
        self.assertIsNotNone(plan.cost, "包装账必须投影成整机成本")
        self.assertTrue(list(plan.cost.items or []), "整机成本必须有明细行")

    def test_a2_projection_is_the_same_ledger_not_a_recompute(self):
        view = self.built_ledger()
        plan = cost_flow.integration_ready_cost(PID)
        result = cost_model.breakdown(plan.cost.model_dump())
        self.assertAlmostEqual(float(view["final"]["total"]), float(result["total"]), places=2,
                               msg="整机成本合计必须逐字等于包装账，不许重算")
        self.assertAlmostEqual(float(view["final"]["material"]), float(result["material"]), places=2,
                               msg="整机成本材料必须逐字等于包装账材料")
        self.assertEqual("packaging_ledger", str((result.get("source") or "")),
                         "投影必须写明自己是包装账的投影，别让人以为是另一次测算")

    def test_a3_existing_integration_cost_is_never_overwritten(self):
        self.built_ledger()
        plan = integration.load_plan(PID)
        plan.cost = CostAnalysis(part_id=integration.ASSEMBLY_PART_ID, part_name="人工测算",
                                 quantity=1, summary="已有人工测算",
                                 items=[{"category": "material", "name": "人工填的材料",
                                         "amount": 99.0}])
        integration.save_plan(PID, plan, "FI1")
        again = cost_flow.integration_ready_cost(PID)
        self.assertEqual(99.0, cost_model.breakdown(again.cost.model_dump())["material"],
                         "已有整机成本不许被包装账投影覆盖")

    def test_a4_stale_or_missing_packaging_ledger_is_still_refused(self):
        self.prepare()
        try:
            cost_flow.integration_ready_cost(PID)
        except Exception:                                           # noqa: BLE001
            return
        self.fail("没有包装账、也没有整机成本时，不许凭空造一份整机成本")


if __name__ == "__main__":
    unittest.main()
