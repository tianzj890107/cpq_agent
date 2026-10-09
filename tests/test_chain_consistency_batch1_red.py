"""红测：链路一致性（批次 1）—— 单一缺口裁决源 + 金额/税唯一投影 + 版本失效。

Spec：docs/specs/chain-consistency-batch1.md

现状缺口（2026-10-09 实测，代码事实）：

  · T2「能不能用」有两个源：`packaging_cost.packaging_cost_readiness_gate()` 按 severity 判
    （只有 advisory 缺口 → `formal` / `formal_ready=True`），而
    `cost_review.summarize()["ready"]`（`cost_review.py:241`）只看布尔 `has_gaps`
    → **同一份成本两个结论**（实测 gate=formal / summarize ready=False）；
  · T2b `stale` 只在 `summarize()` 里生效，`packaging_cost_readiness_gate()` 不认
    → 依据（BOM / 路线 / 业务件清单 / 规则快照）已漂移的旧成本仍 `formal_ready=True`；
  · T9 金额没有唯一投影：`cpq_packaging_quote.price()` 用**未取整**浮点算
    `taxed_total = taxed_unit_price × 数量`，展示端 `_money()` 逐字段各自 `%.2f`
    → 实测 `taxed_unit_price=1.13565` 展示 `1.14`、`taxed_total=3.40695` 展示 `3.41`，
    客户按 `1.14 × 3` 复算得 `3.42`；`_money_rows()` 实测就是这一对数字，缺陷直接印在报价单上。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import contextlib
import copy
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cpq_packaging_quote as quote_mod  # noqa: E402
from tech_app.backend.services import cost_review as review_mod  # noqa: E402
from tech_app.backend.services import packaging_cost as cost_mod  # noqa: E402

#: `GAP_RESOLUTIONS` 里的 advisory 码（`loss_rate_missing` → severity=advisory）。
ADVISORY = "loss_rate_missing"
#: 既有 severity 分层红测里用的阻断码前缀（`content_formula_error:*` → blocking）。
BLOCKING = "content_formula_error:PKG-P-03"


def _cost(gaps=(), **over):
    """最小成本结果体：默认无缺口、非 stale。"""
    cost = {
        "built": True, "total_cost": 10.0,
        "material_total": 6.0, "labor_total": 2.0, "process_total": 1.0,
        "tooling_total": 1.0, "packaging_total": 0.0, "freight_total": 0.0,
        "other_total": 0.0, "loss_amount": 0.0,
        "has_gaps": bool(gaps), "stale": False, "stale_reasons": [],
        "gaps": [dict(gap) for gap in gaps],
        "content_binding": {"source": "order_contents", "unbound_total": 0},
    }
    cost.update(over)
    return cost


_PART = {"id": "P1", "name": "P1", "kind": "part", "quantity": 1, "has_cost": True,
         "item_count": 1,
         "breakdown": {"material": 5.0, "labor": 0.0, "machining": 0.0,
                       "overhead": 0.0, "total": 5.0},
         "unit_cost": 5.0, "subtotal": 5.0, "summary": "", "open_questions": 0}
_ASSEMBLY = {"id": review_mod.ASSEMBLY_ID, "name": "包装整单成本", "kind": "assembly",
             "quantity": 1, "has_cost": True, "item_count": 1,
             "breakdown": {"material": 6.0, "labor": 2.0, "machining": 1.0,
                           "overhead": 1.0, "total": 10.0},
             "unit_cost": 10.0, "subtotal": 10.0, "summary": "", "open_questions": 0}


@contextlib.contextmanager
def _harness(cost):
    """把 2.3 的读侧固定成受控输入：零件/整机已有成本、需求是包装、成本就是 `cost`。"""
    with mock.patch.object(review_mod, "_part_rows", lambda pid, ir: [dict(_PART)]), \
            mock.patch.object(review_mod, "_assembly_row", lambda plan: dict(_ASSEMBLY)), \
            mock.patch.object(review_mod, "parts_source", lambda pid, ir: None), \
            mock.patch.object(review_mod.store, "load_requirement",
                              lambda pid: {"data": {"industry": "packaging"}}), \
            mock.patch.object(cost_mod, "load_cost", lambda pid: cost):
        yield


def _quote():
    """复现 T9 尾差的定价用例（10-9 实测：单价 1.14 × 3 = 3.42，总额却是 3.41）。"""
    package = {"industry": "packaging",
               "cost": {"total_cost": 0.7035, "currency": "CNY"},
               "requirement": {"quote_quantity": 3},
               "source": {}, "bom": {"items": []}, "route": {"steps": []}}
    return quote_mod.price(package, gross_margin_rate=0.3, tax_rate=0.13)


class ASingleGapVerdictSource(unittest.TestCase):
    """A 组：缺口「能不能用」只有一个裁决源，并如实给出四态。"""

    def test_a1_precondition_advisory_only_is_formal_at_the_gate(self):
        """前提校验：既有 severity 分层已把 advisory-only 判成 formal（本批不改它）。"""
        gate = cost_mod.packaging_cost_readiness_gate(_cost([{"code": ADVISORY}]))
        self.assertEqual(0, gate["blocking_total"], "夹具前提：这条是 advisory")
        self.assertEqual("formal", gate["verdict"])
        self.assertTrue(gate["formal_ready"])

    def test_a2_readiness_verdict_exposes_the_four_states(self):
        self.assertTrue(
            hasattr(cost_mod, "readiness_verdict"),
            "缺 `packaging_cost.readiness_verdict(cost, waiver=None)`"
            "（阻断缺口 / 提示缺口 / 演示豁免 / 正式报价资格 四态，见 Spec §2.1）")
        verdict = cost_mod.readiness_verdict
        self.assertEqual("formal_eligible", verdict(_cost())["state"])
        self.assertEqual("advisory_only", verdict(_cost([{"code": ADVISORY}]))["state"])
        self.assertEqual("blocking_gaps", verdict(_cost([{"code": BLOCKING}]))["state"])
        waived = verdict(_cost([{"code": BLOCKING}]), {"signed_by": "zhangzhen"})
        self.assertEqual("demo_waived", waived["state"])
        self.assertEqual("zhangzhen", waived["waived_by"])

    def test_a3_readiness_verdict_passes_the_gate_through_unchanged(self):
        verdict = cost_mod.readiness_verdict(_cost([{"code": ADVISORY}]))
        gate = verdict.get("gate") or {}
        self.assertEqual("packaging-cost-readiness/1", gate.get("version"),
                         "gate 结果必须原样透传，不许另起口径")
        self.assertEqual(1, gate.get("advisory_total"))

    def test_a4_stale_cost_is_never_formal_eligible(self):
        """依据漂移（BOM / 路线 / 业务件清单 / 规则快照）的旧成本不得算正式。"""
        stale = _cost([], stale=True, stale_reasons=["路线已重新确认"])
        verdict = cost_mod.readiness_verdict(stale)
        self.assertEqual("blocking_gaps", verdict["state"],
                         "stale 成本不得继续 formal_eligible（Spec §2.1 / §1.2）")
        self.assertIn("路线已重新确认", list(verdict.get("stale_reasons") or []))

    def test_a5_cost_review_ready_follows_the_gate(self):
        cost = _cost([{"code": ADVISORY}])
        gate = cost_mod.packaging_cost_readiness_gate(cost)
        with _harness(cost):
            data = review_mod.summarize("probe", None, None)
        self.assertEqual(
            gate["formal_ready"], data["ready"],
            "2.3 的 ready 必须与 readiness gate 同一结论（advisory 不阻断）")

    def test_a6_advisory_gaps_do_not_force_re_sign(self):
        cost = _cost([{"code": ADVISORY, "message": "缺损耗率"}])
        with _harness(cost):
            data = review_mod.summarize("probe", None, None)
            gaps = review_mod.confirm_gaps("probe", None, None, data)
        blocking = gaps.get("blocking_codes")
        advisory = gaps.get("advisory_codes")
        self.assertIsInstance(blocking, list, "confirm_gaps 必须按 severity 分出阻断集合")
        self.assertIsInstance(advisory, list, "confirm_gaps 必须按 severity 分出提示集合")
        self.assertEqual([], [c for c in blocking if str(c).startswith("packaging:gap:")],
                         "一条提示缺口不得进入「必须重签」的阻断集合")
        self.assertTrue([c for c in advisory if str(c).startswith("packaging:gap:")],
                        "提示缺口仍必须披露，不许静默丢弃")


class BMoneyAndTaxSingleProjection(unittest.TestCase):
    """B 组：金额/税只有一个渲染投影，尾差必须如实披露。"""

    def test_b1_money_view_is_the_single_pure_projection(self):
        self.assertTrue(hasattr(quote_mod, "money_view"),
                        "缺 `cpq_packaging_quote.money_view(quote)`（Spec §2.3）")
        quote = _quote()
        before = copy.deepcopy(quote)
        view = quote_mod.money_view(quote)
        self.assertEqual(before, quote, "money_view 必须是纯函数，不许改入参")
        self.assertEqual("packaging-quote-money/1", view.get("version"))
        self.assertEqual({key for key, _ in quote_mod._MONEY_FIELDS},
                         set((view.get("fields") or {}).keys()),
                         "fields 的键集必须等于既有 `_MONEY_FIELDS` 闭集")
        for key, value in (view.get("fields") or {}).items():
            self.assertRegex(str(value), r"^-?\d+\.\d{2}$",
                             "金额渲染一律两位小数：%s=%r" % (key, value))

    def test_b2_reproduced_rounding_gap_is_disclosed_not_silent(self):
        view = quote_mod.money_view(_quote())
        fields = view.get("fields") or {}
        self.assertEqual("1.14", fields.get("taxed_unit_price"))
        self.assertEqual("3.41", fields.get("taxed_total"),
                         "总额取权威数字（不许用展示单价 × 数量重算）")
        self.assertEqual("total", view.get("authority"))
        self.assertFalse(view.get("reconciles"),
                         "1.14 × 3 = 3.42 ≠ 3.41：必须如实报不自洽")
        self.assertEqual("0.01", view.get("difference"))

    def test_b3_quote_document_discloses_the_reconciliation_rule(self):
        doc = quote_mod.document(_quote())
        text = str(doc.get("markdown") or "")
        self.assertIn("含税总额", text, "前提：报价单里本来就有含税总额")
        self.assertTrue(
            ("尾差" in text) or ("以总额为准" in text) or ("以系统计算为准" in text),
            "单价×数量与总额不自洽时，报价单必须披露口径，不许静默")


if __name__ == "__main__":
    unittest.main()
