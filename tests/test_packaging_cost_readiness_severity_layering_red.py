"""红测：成本就绪结论必须按缺口严重度分层（blocking 决定正式，advisory 只披露）。

Spec：docs/specs/packaging-cost-readiness-severity-layering.md

现状缺口（代码事实，`tech_app/backend/services/packaging_cost.py:1737-1788`）：
  · `verdict = PROVISIONAL if (gaps or silent) else FORMAL` —— 结论只看"有没有任何缺口"，
    `severity=blocking/advisory` 之分算出来了却不参与结论（`blocking_total` 是死字段）；
  · 一条纯提示缺口（`loss_rate_missing` / `freight_rule_missing` / `below_moq`）就能把成本打成
    "暂定"，`formal_cost_or_raise()` 于是要求 POC 豁免签字；
  · `affected_amount_total` 把 advisory 的金额混进同一个数（34 那次 9 条 advisory 影响
    5.4831 元/件，约占成本 31%），读的人分不出阻断与提示。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_cost as cost_mod  # noqa: E402

ADVISORY = "loss_rate_missing"
BLOCKING = "content_formula_error:PKG-P-03"


def _cost(gaps, items=None):
    return {"built": True, "gaps": list(gaps), "items": list(items or []),
            "rule_snapshot_version": "pkginst-seed-1"}


class AConclusionFollowsSeverity(unittest.TestCase):
    """A 组：结论只由阻断（与静默按 0）决定。"""

    def gate(self, cost):
        return cost_mod.packaging_cost_readiness_gate(cost)

    def test_a1_advisory_only_is_formal(self):
        gate = self.gate(_cost([{"code": ADVISORY, "where": "EVA 片材"}]))
        self.assertEqual(0, gate["blocking_total"], "夹具前提：这条是 advisory")
        self.assertEqual("formal", gate["verdict"],
                         "只带提示性缺口的成本必须是 formal（现在 verdict 只看缺口条数）")
        self.assertTrue(gate["formal_ready"], "formal_ready 必须跟着 verdict")

    def test_a2_advisory_is_disclosed_not_silent(self):
        gate = self.gate(_cost([{"code": ADVISORY, "where": "EVA 片材"}]))
        self.assertEqual(1, gate.get("advisory_total"),
                         "gate 必须新增 advisory_total（提示缺口条数）")
        advisories = gate.get("advisories")
        self.assertIsInstance(advisories, list, "gate 必须新增 advisories（结构化提示缺口）")
        self.assertEqual([ADVISORY], [row.get("code") for row in advisories],
                         "提示缺口必须原样带出，只是不再决定 verdict")
        self.assertEqual(1, len(gate.get("gaps") or []), "gaps 仍保留全部缺口（读端不破）")
        self.assertTrue(any("提示" in str(reason) for reason in gate.get("reasons") or []),
                        "formal 也要有一句「N 项提示缺口」，不许静默：%r" % gate.get("reasons"))

    def test_a3_blocking_still_provisional(self):
        gate = self.gate(_cost([{"code": BLOCKING, "where": "PKG-P-03"}]))
        self.assertEqual(1, gate["blocking_total"])
        self.assertEqual("provisional", gate["verdict"], "阻断缺口必须仍是 provisional")
        self.assertFalse(gate["formal_ready"])

    def test_a4_silent_zero_fallback_still_provisional(self):
        gap = {"code": ADVISORY, "where": "EVA 片材"}
        item = {"part_name": "EVA 片材", "amount": 3.0, "expression": "Σ(面积 × 单价)",
                "inputs": {"loss_rate": None}}
        gate = self.gate(_cost([gap], [item]))
        self.assertEqual(1, gate["silent_zero_total"], "夹具前提：这条命中静默按 0")
        self.assertEqual("provisional", gate["verdict"],
                         "静默按 0 一律仍是 provisional —— 不许因为 severity=advisory 就放行")

    def test_a5_amounts_are_split_by_severity(self):
        gaps = [{"code": ADVISORY, "where": "EVA 片材"},
                {"code": BLOCKING, "where": "PKG-P-03"}]
        items = [{"part_name": "EVA 片材", "amount": 5.0, "inputs": {}},
                 {"part_code": "PKG-P-03", "amount": 7.5, "inputs": {}}]
        gate = self.gate(_cost(gaps, items))
        self.assertEqual(5.0, gate.get("advisory_amount_total"),
                         "提示缺口的影响金额要单独成数")
        self.assertEqual(7.5, gate.get("blocking_amount_total"),
                         "阻断缺口的影响金额要单独成数")
        self.assertEqual(12.5, gate.get("affected_amount_total"),
                         "affected_amount_total 保持兼容 = 两者之和")


class BReleaseGateUsesTheSameRuler(unittest.TestCase):
    """B 组：出口按同一把尺子 —— 提示缺口不要求签字，阻断缺口照旧要。"""

    def test_b1_advisory_only_does_not_require_a_waiver(self):
        cost = _cost([{"code": ADVISORY, "where": "EVA 片材"}])
        try:
            gate = cost_mod.formal_cost_or_raise(cost)
        except cost_mod.CostError as exc:                     # noqa: PERF203
            self.fail("只带提示性缺口的成本不该要 POC 签字（现在抛 %s/%s）"
                      % (exc.status_code, exc.code))
        self.assertEqual("formal", gate["verdict"])
        self.assertEqual(1, gate.get("advisory_total"), "放行时提示必须原样带出")

    def test_b2_blocking_requires_a_waiver(self):
        cost = _cost([{"code": BLOCKING, "where": "PKG-P-03"}])
        with self.assertRaises(cost_mod.CostError) as ctx:
            cost_mod.formal_cost_or_raise(cost)
        self.assertEqual(409, ctx.exception.status_code)
        self.assertEqual("packaging_cost_not_formal", ctx.exception.code)
        waived = cost_mod.formal_cost_or_raise(cost, {"signed_by": "zhangzhen", "reason": "赶样"})
        self.assertTrue(waived.get("waived"), "带签字的阻断缺口照旧可放行")

    def test_b3_version_literal_unchanged(self):
        self.assertEqual("packaging-cost-readiness/1", cost_mod.READINESS_VERSION,
                         "本批不许改就绪口径的版本字面")


if __name__ == "__main__":
    unittest.main()
