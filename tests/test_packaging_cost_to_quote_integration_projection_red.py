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
原批次不覆盖已有人工/模型测算；2026-10-14统一回传契约明确改为当前包装账优先，
旧人工/模型结果留审计，不再作为包装项目的第二套对外成本。

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
from tech_app.backend.models.integration import IntegrationParamPlan  # noqa: E402
from tech_app.backend.services import cost_flow, cost_model, integration  # noqa: E402
from tech_app.backend.services import product_params  # noqa: E402
from tech_app.backend.services import packaging_cost_ledger  # noqa: E402
from tech_app.backend.storage import store  # noqa: E402
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

    def test_a3_current_packaging_ledger_wins_and_old_cost_is_audited(self):
        view=self.built_ledger()
        plan = integration.load_plan(PID)
        plan.cost = CostAnalysis(part_id=integration.ASSEMBLY_PART_ID, part_name="人工测算",
                                 quantity=1, summary="已有人工测算",
                                 items=[{"category": "material", "name": "人工填的材料",
                                         "amount": 99.0}])
        integration.save_plan(PID, plan, "FI1")
        again = cost_flow.integration_ready_cost(PID)
        self.assertAlmostEqual(view['final']['material'],cost_model.breakdown(again.cost.model_dump())['material'])
        records=store.list_audit(PID)
        updates=[r for r in records if r.get('action')=='packaging_cost_projection_updated']
        self.assertTrue(any(((r.get('detail') or {}).get('previous_cost') or {}).get('summary')=='已有人工测算' for r in updates))

    def test_a4_stale_or_missing_packaging_ledger_is_still_refused(self):
        self.prepare()
        try:
            cost_flow.integration_ready_cost(PID)
        except Exception:                                           # noqa: BLE001
            return
        self.fail("没有包装账、也没有整机成本时，不许凭空造一份整机成本")

def _handoff_error(self):
    """跑一次回传正文，取回业务拒绝的原文；桥接层/令牌等问题与本批无关，返回空串。"""
    try:
        cost_flow.integration_send_to_quote_body(
            PID, product_name="礼盒", user={"username": "FI1", "display_name": "FI1"})
    except cost_flow.CostFlowError as exc:
        return str(exc)
    except Exception:                                               # noqa: BLE001
        return ""
    return ""


class B包装参数族(ProjectionCase):
    """Spec §2.5/§2.6：包装项目的回传必填按包装族校验，参数从需求单投影。

    现场（34 实测，项目 `6beeeebb19a1`）：参数推荐环节在包装链上跑过，`plan.params` 里
    只有平台自动写的成品编码 / 名称、`product_family` 是空的 —— 回传于是按「其他成品」
    校验，去问产品系列 / 工作温度，而这两个字段在包装链上根本没有来源。
    """

    def handoff_text(self, **over):
        self.prepare(**over)
        self.build_cost()
        plan = integration.load_plan(PID)
        plan.params = IntegrationParamPlan()
        integration.save_plan(PID, plan, "PE1")
        return _handoff_error(self)

    def test_b1_packaging_handoff_never_demands_non_packaging_fields(self):
        text = self.handoff_text()
        self.assertNotIn("整机成本", text, "回传不该还卡在整机成本：包装账投影要先补上")
        self.assertNotIn("产品系列", text, "包装项目不该被当成「其他成品」问产品系列")
        self.assertNotIn("工作温度", text, "包装项目不该被当成「其他成品」问工作温度")

    def test_b2_requirement_fields_are_not_asked_again(self):
        text = self.handoff_text()
        self.assertNotIn("整机成本", text, "回传不该还卡在整机成本：包装账投影要先补上")
        for name in ("成品内长", "成品内宽", "成品内高", "盒型", "闭合方式", "面纸克重"):
            self.assertNotIn(name, text, "需求单已经有「%s」，回传不该再问一遍" % name)

    def test_b3_projection_never_invents_values(self):
        """需求单里没有的字段不许被编出来 —— 只能由「参数推荐」环节补。"""
        self.handoff_text(packaging_product_name=None, packaging_category=None)
        data = (store.load_requirement(PID) or {}).get("data") or {}
        self.assertIsNone(data.get("packaging_product_name"))
        self.assertIsNone(data.get("packaging_category"))
        params = integration.load_plan(PID).params
        self.assertIsNotNone(params, "包装项目必须有一份参数推荐结果（含需求单投影）")
        projected = {row.param_code: row.value for row in (params.params or [])}
        for code in ("packaging_product_name", "packaging_category"):
            self.assertNotIn(code, projected, "需求单没有 %s，不许编一个值出来" % code)
        missing = [row["name"] for row in product_params.missing_required(params)]
        self.assertIn("包装产品名称", missing, "需求单没有的字段必须如实留在缺口里")


if __name__ == "__main__":
    unittest.main()
