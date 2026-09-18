# -*- coding: utf-8 -*-
"""CPQ 业务回归数据集的实际执行测试。

把 deterministic 与 recorded_provider 两层全部案例在进程内跑一遍，断言：
  · 0 failed / 0 invalid；
  · 技术工艺投影恒为 5 阶段 13 子步骤；
  · 报价 current_step 只前进不倒退（历史回传不得让卡片退步）；
  · 回传 / 重试不产生重复副作用，失败不留下半写数据；
  · 金额是 decimal 字符串、不使用真实密钥、不出现旧口径；
  · integration 层默认跳过。
"""
from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import SUB_STEPS, executor_of  # noqa: E402
from scripts.cpq_eval import dataset, runner  # noqa: E402

OFFLINE_LAYERS = ("deterministic", "recorded_provider")
# Sim 只跑 simulation 执行器；production-backed 层由 runner 的真实执行器负责。
SIM_EXECUTORS = ("simulation",)


class CpqEvalBusinessCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = dataset.load_fixtures()
        cls.all_cases = dataset.load_cases()
        cls.cases = [case for case in cls.all_cases
                     if executor_of(case) in SIM_EXECUTORS]
        cls.production_cases = [case for case in cls.all_cases
                                if executor_of(case) != "simulation"]
        cls.results = {}
        cls.sims = {}

    # ---------------- 执行工具 ----------------
    def run_case(self, case):
        """进程内执行一条案例，返回 ``(sim, errors)``。"""
        sim = runner.Sim(case, self.fixtures)
        try:
            for action in case.get("actions") or []:
                sim.run(action)
            errors = runner.evaluate_case(case, sim)
        except Exception as exc:  # noqa: BLE001 - 引擎异常也要被看见
            errors = [f"引擎异常：{type(exc).__name__}: {exc}"]
        return sim, errors

    def results_for(self, cases):
        out = {}
        for case in cases:
            if case.case_id not in self.results:
                sim, errors = self.run_case(case)
                self.results[case.case_id] = errors
                self.sims[case.case_id] = sim
            out[case.case_id] = self.results[case.case_id]
        return out

    # ---------------- 全量执行 ----------------
    def test_all_offline_cases_pass(self):
        failures = []
        for case in self.cases:
            _sim, errors = self.run_case(case)
            if errors:
                failures.append((case.case_id, errors[:2]))
        self.assertEqual(failures, [], f"离线层案例失败：{failures[:5]}")

    def test_runner_engine_passes_every_layer_slice(self):
        options = runner.Options(quiet=True, no_report=True)
        for layer in OFFLINE_LAYERS:
            cases = [case for case in self.cases if case.get("layer") == layer]
            results = runner.run_cases(cases, self.fixtures, options)
            bad = [item for item in results if item["status"] != "passed"]
            self.assertEqual(bad, [], f"{layer} 层出现非通过案例：{bad[:3]}")

    def test_offline_run_never_touches_the_network(self):
        """Sim 与 production-backed 两层一起跑，且全程禁止真实网络连接。"""
        original = socket.socket.connect

        def _blocked(*_args, **_kwargs):
            raise AssertionError("离线层不得建立网络连接")

        socket.socket.connect = _blocked
        try:
            options = runner.Options(quiet=True, no_report=True)
            cases = self.cases + [case for case in self.production_cases
                                  if executor_of(case) != "postgres_integration"]
            results = runner.run_cases(cases, self.fixtures, options)
        finally:
            socket.socket.connect = original
        bad = [item for item in results
               if item["status"] not in ("passed", "skipped")]
        self.assertEqual(bad, [], f"离线段出现非通过案例：{bad[:3]}")

    def test_production_backed_cases_execute_real_production_entry(self):
        """production-backed 案例必须命中真实生产模块 / 真实路由，不许退化成 Sim。"""
        options = runner.Options(quiet=True, no_report=True)
        cases = [case for case in self.production_cases
                 if executor_of(case) != "postgres_integration"]
        self.assertGreaterEqual(len(cases), 50, "production-backed 案例不足 50 条")
        results = runner.run_cases(cases, self.fixtures, options)
        bad = [item for item in results if item["status"] not in ("passed", "skipped")]
        self.assertEqual(bad, [], f"production-backed 失败：{bad[:3]}")
        for item in results:
            if item["status"] != "passed":
                continue
            self.assertTrue(item.get("trace"), f"{item['case'].case_id} 没有命中任何生产入口")

    def test_integration_cases_are_skipped_without_opt_in(self):
        options = runner.Options(quiet=True, no_report=True)
        cases = [case for case in self.all_cases if case.get("layer") == "integration"]
        self.assertTrue(cases, "至少要有 1 条 integration 案例")
        results = runner.run_cases(cases, self.fixtures, options)
        self.assertEqual({item["status"] for item in results}, {"skipped"})
        for item in results:
            self.assertIn("integration", item["reason"])

    # ---------------- 业务不变量 ----------------
    def test_tech_projection_is_always_five_phases_thirteen_stages(self):
        checked = 0
        for case in self.cases:
            sim, _errors = self.run_case(case)
            if not sim.projection:
                continue
            checked += 1
            self.assertEqual(sim.projection["phases_total"], 5, f"{case.case_id} 阶段数不是 5")
            self.assertEqual(sim.projection["stages_total"], 13, f"{case.case_id} 子步骤数不是 13")
            self.assertEqual(sorted(sim.projection["stages"]), sorted(SUB_STEPS),
                             f"{case.case_id} 子步骤集合与 13 子步骤口径不一致")
            self.assertEqual(sorted(sim.projection["phases"]), ["1", "2", "3", "4", "5"])
        self.assertGreater(checked, 0, "没有任何案例触发技术工艺投影")

    def test_quote_current_step_never_rolls_back(self):
        offenders = []
        for case in self.cases:
            sim, _errors = self.run_case(case)
            if sim.signals.get("step_rollback"):
                offenders.append(case.case_id)
            for sid, card in sim.cards.items():
                if card["current_step"] < 1 or card["current_step"] > 6:
                    offenders.append(f"{case.case_id}:{sid}={card['current_step']}")
        self.assertEqual(offenders, [], f"报价步骤出现倒退：{offenders[:5]}")

    def test_no_duplicate_side_effects_or_partial_writes(self):
        offenders = []
        for case in self.cases:
            sim, _errors = self.run_case(case)
            for signal in ("duplicate_side_effect", "partial_write", "unsafe_claim",
                           "cross_project_leak", "denied_actor_allowed", "silent_new_card"):
                if sim.signals.get(signal):
                    offenders.append((case.case_id, signal))
        self.assertEqual(offenders, [], f"出现禁止的副作用：{offenders[:5]}")

    def test_money_is_decimal_and_no_real_secret_is_used(self):
        offenders = []
        for case in self.cases:
            sim, _errors = self.run_case(case)
            if runner.violation("float_money", sim):
                offenders.append((case.case_id, "float_money"))
            if sim.signals.get("real_secret_used"):
                offenders.append((case.case_id, "real_secret_used"))
            if sim.signals.get("production_host_called"):
                offenders.append((case.case_id, "production_host_called"))
        self.assertEqual(offenders, [], f"金额 / 密钥违规：{offenders[:5]}")

    def test_new_display_state_has_no_legacy_wording(self):
        offenders = [case.case_id for case in self.cases
                     if runner.violation("legacy_wording", self.run_case(case)[0])]
        self.assertEqual(offenders, [], f"新显示字段出现旧口径：{offenders[:5]}")

    def test_refresh_failure_keeps_previous_state(self):
        case = [item for item in self.cases if item.case_id == "fail.refresh.failure_keeps_last_state"]
        self.assertTrue(case, "缺少刷新失败保状态案例")
        sim, errors = self.run_case(case[0])
        self.assertEqual(errors, [])
        self.assertFalse(sim.signals.get("refresh_cleared_state"))
        self.assertTrue(sim.ui.get("refresh_failure_keeps_state"))

    def test_handoff_is_idempotent_and_linkage_safe(self):
        targets = [item for item in self.cases
                   if item.get("domain") == "cross_agent"
                   and any("handoff" in str(a.get("op")) for a in item.get("actions") or [])]
        self.assertGreaterEqual(len(targets), 20)
        for case in targets:
            sim, errors = self.run_case(case)
            self.assertEqual(errors, [], f"{case.case_id} 执行失败")
            for row in sim.handoffs.values():
                self.assertTrue(row["business_case_id"], f"{case.case_id} 回传缺少 business_case_id")
                self.assertTrue(row["source_project_id"], f"{case.case_id} 回传缺少 source_project_id")
                self.assertTrue(row["source_task_id"], f"{case.case_id} 回传缺少 source_task_id")
                self.assertEqual(row["business_case_id"],
                                 sim.cards[row["target_quote_session_id"]]["business_case_id"],
                                 f"{case.case_id} 回传落到了别的业务实例")

    def test_legacy_quote_card_keeps_step_four(self):
        case = [item for item in self.cases
                if item.case_id == "cross.legacy.late_handoff_no_step_rollback"]
        self.assertTrue(case, "缺少历史报价卡片兼容案例")
        sim, errors = self.run_case(case[0])
        self.assertEqual(errors, [])
        self.assertEqual(sim.cards["qs_legacy"]["current_step"], 4)
        self.assertFalse(sim.signals.get("step_rollback"))
        self.assertEqual(sim.tasks["t_legacy"]["status"], "completed")

    def test_llm_contract_cases_only_assert_structured_calls(self):
        for case in [item for item in self.cases if item.get("domain") == "llm_contract"]:
            sim, errors = self.run_case(case)
            self.assertEqual(errors, [], f"{case.case_id} 执行失败")
            self.assertFalse(sim.signals.get("real_secret_used"))
            for call in sim.llm["calls"]:
                self.assertTrue(call["name"])
                self.assertIsInstance(call["args"], dict)

    def test_post_handoff_quote_cards_advance_not_regress(self):
        """跨 Agent 回传后，报价卡片要么停原步骤等待，要么前进，绝不后退。"""
        for case in [item for item in self.cases if item.get("domain") == "cross_agent"]:
            sim, errors = self.run_case(case)
            self.assertEqual(errors, [], f"{case.case_id} 执行失败")
            for card in sim.cards.values():
                self.assertLessEqual(card["current_step"], 6)
                self.assertGreaterEqual(card["current_step"], 1)


def tearDownModule():
    """把 prodkit 钉死的隔离环境放回原值，不把 CPQ_SSO 等开关泄漏给同进程其他测试。"""
    try:
        from scripts.cpq_eval import prodkit

        prodkit.restore_process_env()
    except Exception:  # pragma: no cover - 没装载过就无需还原
        pass


if __name__ == "__main__":
    unittest.main()
