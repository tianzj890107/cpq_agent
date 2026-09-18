# -*- coding: utf-8 -*-
"""执行器声明一致性门禁：禁止靠改 layer / executor 字段虚增生产覆盖率。

守护的是**声明与执行是否一致**，不是源码字符串：

  · 声明 production_* / recorded_provider / postgres_integration 的案例必须有真实入口、
    必须没有 Sim `actions`，声明 PG 的必须真的声明 `pg` 场景；
  · `layer=recorded_provider` / `layer=integration` 不再能把案例算进生产层 ——
    executor 由 `executor_of()` 唯一决定，且**只认显式 executor / actions**；
  · llm_contract 里确实无法真实回放的 fixture 必须显式声明 `provider_replay=simulated`
    与原因，否则一律判不合法；
  · 本模块自带反例：把上述任一条件改坏，门禁必须报出来。
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import checks, dataset, executor_of  # noqa: E402

GATE = ("production_unit", "production_http", "recorded_provider", "postgres_integration")


class DeclaredExecutorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = dataset.load_cases()

    def test_declared_executor_is_within_closed_set(self):
        allowed = {"specification_only", "simulation", "production_unit", "production_http",
                   "recorded_provider", "postgres_integration"}
        bogus = [c.case_id for c in self.cases
                 if c.raw.get("executor") and c.raw["executor"] not in allowed]
        self.assertEqual(bogus, [], f"出现闭集之外的 executor：{bogus[:5]}")

    def test_production_cases_never_declare_sim_actions(self):
        bad = [c.case_id for c in self.cases
               if executor_of(c) in GATE and c.raw.get("actions")]
        self.assertEqual(bad, [], f"生产层案例声明了 Sim actions：{bad[:5]}")

    def test_layer_field_cannot_inflate_production_coverage(self):
        """改 layer 不得改变执行器；非生产层案例更不能被 layer 抬进生产层。"""
        for case in self.cases:
            base = executor_of(case)
            raw = copy.deepcopy(case.raw)
            for probe in ("recorded_provider", "integration", "deterministic"):
                raw["layer"] = probe
                self.assertEqual(
                    executor_of(raw), base,
                    f"{case.case_id} 只改 layer={probe} 就改变了执行器（{base} → "
                    f"{executor_of(raw)}）：executor 必须只看 executor/actions 字段")
                if base not in GATE:
                    self.assertNotIn(executor_of(raw), GATE,
                                     f"{case.case_id} 只改 layer={probe} 就被算成生产层")

    def test_missing_executor_falls_back_to_specification_only(self):
        """既没有 executor 也没有 actions 的案例只能归为 specification_only。"""
        offenders = []
        for case in self.cases:
            raw = case.raw
            if raw.get("executor") or raw.get("actions"):
                continue
            if executor_of(case) != "specification_only":
                offenders.append(case.case_id)
        self.assertEqual(offenders, [], f"缺少执行器却被算成执行过：{offenders[:5]}")

    def test_layer_reaches_its_declared_executor(self):
        """layer=recorded_provider / integration 必须真的用对应执行器，不许退回 Sim。"""
        offenders = []
        for case in self.cases:
            layer = case.get("layer")
            executor = executor_of(case)
            if layer == "recorded_provider" and executor != "recorded_provider":
                offenders.append((case.case_id, layer, executor))
            if layer == "integration" and executor != "postgres_integration":
                offenders.append((case.case_id, layer, executor))
        self.assertEqual(offenders, [], f"layer 声明的执行器不一致：{offenders[:5]}")

    def test_recorded_provider_cases_feed_fixture_into_real_entry(self):
        offenders = []
        for case in self.cases:
            if executor_of(case) != "recorded_provider":
                continue
            provider = (case.raw.get("input") or {}).get("provider")
            blob = json.dumps(case.raw.get("input") or {}, ensure_ascii=False)
            if not provider or "$fixture" not in blob or str(provider) not in blob:
                offenders.append(case.case_id)
        self.assertEqual(offenders, [],
                         f"recorded_provider 案例没有把 fixture 喂进真实入口：{offenders}")

    def test_postgres_cases_declare_pg_scenario(self):
        offenders = []
        for case in self.cases:
            if executor_of(case) != "postgres_integration":
                continue
            steps = (case.raw.get("input") or {}).get("steps") or []
            if not any(isinstance(s, dict) and s.get("pg") for s in steps):
                offenders.append(case.case_id)
        self.assertEqual(offenders, [], f"PG 案例没有声明 pg 场景：{offenders}")
        self.assertGreaterEqual(len([c for c in self.cases
                                     if executor_of(c) == "postgres_integration"]), 9,
                                "PG 层案例少于 9 条（并发领取 / 回传幂等 / 事务回滚 / 角色范围）")

    def test_llm_contract_unreplayable_fixtures_are_explicit(self):
        for case in self.cases:
            if case.get("domain") != "llm_contract":
                continue
            if executor_of(case) == "recorded_provider":
                continue
            inp = case.raw.get("input") or {}
            self.assertEqual(inp.get("provider_replay"), "simulated",
                             f"{case.case_id} 不是 recorded_provider，也没有声明 provider_replay")
            self.assertTrue(str(inp.get("provider_replay_reason") or "").strip(),
                            f"{case.case_id} 缺少 provider_replay_reason")

    # ---------------- 反例：门禁必须能报出问题 ----------------
    def _fake(self, **over):
        base = {"id": "fake.case", "version": "1.0", "title": "反例", "domain": "concurrency",
                "priority": "P0", "layer": "deterministic", "roles": ["sales_mgr"],
                "preconditions": {}, "input": {}, "actions": [{"op": "login", "actor": "a"}],
                "expected": {"state": {}}, "invariants": [], "tags": [], "source_specs": []}
        base.update(over)
        return base

    def _errors(self, raw):
        case = dataset.Case(raw, Path("cases/fake.json"), "fake")
        return checks.check_case_contract(case)

    def test_counterexample_production_declared_but_sim_executed(self):
        errs = self._errors(self._fake(executor="production_http",
                                       entry={"http": {"method": "GET", "path": "/api/projects"}}))
        self.assertTrue(any("不得声明 actions" in e for e in errs), errs)

    def test_counterexample_recorded_provider_without_fixture_wiring(self):
        errs = self._errors(self._fake(
            executor="recorded_provider", domain="llm_contract",
            entry={"module": "backend.services.llm_output", "function": "is_truncated"},
            input={"provider": "provider/invalid_json.json",
                   "steps": [{"id": "s", "call": {"args": {"reason": "length"}}}]},
            actions=[]))
        self.assertTrue(any("真的把 fixture" in e for e in errs), errs)

    def test_counterexample_layer_integration_without_pg_executor(self):
        errs = self._errors(self._fake(layer="integration", tags=["integration"]))
        self.assertTrue(any("layer=integration" in e for e in errs), errs)

    def test_counterexample_unknown_executor_is_rejected(self):
        errs = self._errors(self._fake(executor="production_super"))
        self.assertTrue(any("未知的 executor" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
