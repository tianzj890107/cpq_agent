"""红测：包材绑定数据源缺失必须被说出来（不许把"我没数据"显示成"没有缺口"）。

Spec：docs/specs/packaging-cost-content-binding-source-disclosure.md

现状缺口（实现自查，逐条可指到行）：
  · `bound_content_codes()` 这一版**故意**只返回空集（仓库里还没有"这一单用哪几项包材"的权威数据源，
    这是上一份 Spec §2.3 的退路）；
  · 于是 `compute_project()` 里每一行都是 `unbound` → `bound_gaps = []`，而"真的没有缺口"也是
    `bound_gaps = []` —— **两者在读接口上完全同形**；
  · 就绪门有 `unbound_total` 但只给条数，不回答"为什么这些包材缺口没进阻断"。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import inspect
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_cost as cost_mod  # noqa: E402

NONE_HINT = "绑定数据源缺失"


class ABindingSource(unittest.TestCase):
    """A 组：绑定集合必须带来源，且不许假装有数据。"""

    def test_a1_detail_reports_source(self):
        detail = getattr(cost_mod, "bound_content_codes_detail", None)
        self.assertTrue(callable(detail), "缺少 bound_content_codes_detail()（Spec §2.1）")
        got = detail({})
        self.assertIsInstance(got, dict, "返回值必须是 dict：%r" % (got,))
        self.assertIn(got.get("source"), ("none", "authoritative"),
                      "source 是闭集 none/authoritative：%r" % got.get("source"))
        self.assertEqual(set(), set(got.get("codes") or ()), "这一版 codes 仍是空集")
        self.assertEqual("none", got.get("source"),
                         "今天没有权威数据源 → 必须老实报 none，不许假装 authoritative")

    def test_a2_compat_wrapper_keeps_working(self):
        self.assertEqual(set(), cost_mod.bound_content_codes({}),
                         "bound_content_codes() 仍是只回集合的兼容包装")

    def test_a3_compute_project_carries_content_binding(self):
        body = inspect.getsource(cost_mod.compute_project)
        self.assertIn("content_binding", body,
                      "compute_project 结果体必须带 content_binding（Spec §2.2），"
                      "否则读接口分不出「没有缺口」与「没有数据」")


class BReadinessTellsWhy(unittest.TestCase):
    """B 组：就绪门必须回答"为什么这些包材缺口不进阻断"。"""

    def gate(self, payload):
        return cost_mod.packaging_cost_readiness_gate(payload)

    def test_b1_no_data_source_is_spelled_out(self):
        gate = self.gate({"built": True, "gaps": [], "content_binding": {"source": "none"},
                          "gaps_unbound_to_order": [
                              {"code": "content_formula_error:PKG-P-DIVIDER",
                               "content_code": "PKG-CT-DIVIDER", "binding_status": "unbound"},
                              {"code": "content_formula_error:PKG-P-BAG",
                               "content_code": "PKG-CT-BAG", "binding_status": "unbound"}]})
        self.assertEqual("none", gate.get("content_binding_source"),
                         "gate 必须带 content_binding_source（键必须存在）：%r" % gate.keys())
        self.assertEqual(2, gate.get("unbound_total"))
        joined = "；".join(str(reason) for reason in gate.get("reasons") or [])
        self.assertIn(NONE_HINT, joined,
                      "数据源缺失时必须有一句说清楚，否则读的人只看到「没有阻断缺口」：%r" % joined)
        self.assertIn("2", joined, "那句话要给出条数：%r" % joined)
        self.assertTrue(gate["formal_ready"], "verdict 口径不改：数据源缺失不把成本打成暂定")

    def test_b2_authoritative_source_does_not_add_the_hint(self):
        gate = self.gate({"built": True, "gaps": [], "content_binding": {"source": "authoritative"},
                          "gaps_unbound_to_order": []})
        joined = "；".join(str(reason) for reason in gate.get("reasons") or [])
        self.assertEqual("authoritative", gate.get("content_binding_source"))
        self.assertNotIn(NONE_HINT, joined, "有权威数据源时不许再说这句话：%r" % joined)

    def test_b3_key_exists_even_without_any_payload(self):
        gate = self.gate({})
        self.assertIn("content_binding_source", gate,
                      "键必须**总是**存在（空 payload 也要有，取不到给空串）：%r" % sorted(gate))

    def test_b4_unbound_codes_are_listed_by_name(self):
        body = inspect.getsource(cost_mod.compute_project)
        self.assertIn("unbound_codes", body,
                      "content_binding 必须逐条列出被降级披露的包材项（Spec §2.4），"
                      "只给总数的话报告没法指名道姓")


if __name__ == "__main__":
    unittest.main()
