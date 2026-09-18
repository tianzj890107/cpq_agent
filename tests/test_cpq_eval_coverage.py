# -*- coding: utf-8 -*-
"""CPQ 业务回归数据集覆盖矩阵测试。

验证第一批数据集满足：五阶段 13 子步骤每步都有正常 + 前置失败、报价六步每步都有
正常 / 拒绝 / 恢复、角色与失败类型全覆盖、跨 Agent 与并发幂等数量达标。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import PHASES, ROLES, SUB_STEPS  # noqa: E402
from scripts.cpq_eval import coverage, dataset, runner  # noqa: E402


class CpqEvalCoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = dataset.load_cases()
        cls.axes = coverage.matrix(cls.cases)

    # ---------------- 覆盖矩阵 ----------------
    def test_matrix_has_all_axes(self):
        for axis in ("domain", "priority", "layer", "stage", "sub_step", "quote_step",
                     "role", "failure_type", "kind"):
            self.assertIn(axis, self.axes)
            self.assertTrue(self.axes[axis], f"覆盖矩阵缺少 {axis} 轴")

    def test_matrix_layers_and_priorities(self):
        # layer 是数据的离线/联网属性（历史字段）：deterministic / recorded_provider / integration。
        self.assertGreaterEqual(self.axes["layer"].get("deterministic", 0), 200)
        self.assertGreaterEqual(self.axes["layer"].get("recorded_provider", 0), 5)
        self.assertGreaterEqual(self.axes["layer"].get("integration", 0), 9)
        self.assertGreaterEqual(self.axes["priority"].get("P0", 0), 60)

    def test_matrix_executor_layers_are_real_and_ordered(self):
        """执行层才是发布门禁口径：simulation 不能冒充 production-backed。"""
        exec_axis = self.axes["executor"]
        self.assertEqual(exec_axis.get("specification_only", 0), 0,
                         "第一批不允许纯规范案例（必须可执行或显式说明）")
        self.assertGreaterEqual(exec_axis.get("simulation", 0), 200)
        self.assertGreaterEqual(exec_axis.get("production_unit", 0), 30)
        self.assertGreaterEqual(exec_axis.get("production_http", 0), 30)
        self.assertGreaterEqual(exec_axis.get("recorded_provider", 0), 5)
        self.assertGreaterEqual(exec_axis.get("postgres_integration", 0), 9)
        # 六层互斥且合计等于总数。
        self.assertEqual(sum(exec_axis.values()), len(self.cases))

    def test_roles_cover_the_five_business_roles(self):
        table = coverage.role_table(self.cases)
        for role in ROLES:
            self.assertGreater(table.get(role, 0), 0, f"角色 {role} 没有案例覆盖")
        for role in ("sales_mgr", "process_mgr", "process_engineer", "finance_mgr",
                     "reviewer", "admin"):
            self.assertGreaterEqual(table.get(role, 0), 1, f"缺少角色 {role} 的案例")

    def test_failure_types_cover_key_paths(self):
        table = self.axes["failure_type"]
        for name in ("permission_denied", "missing_prerequisite", "duplicate_request",
                     "concurrency_conflict", "transaction_rollback", "invalid_output",
                     "not_found", "timeout", "refresh_failure", "stale_upstream",
                     "auth_failure", "data_leak_attempt", "ui_protocol_violation",
                     "provider_error"):
            self.assertGreater(table.get(name, 0), 0, f"失败类型 {name} 没有案例覆盖")

    def test_kinds_cover_concurrency_idempotent_recovery(self):
        kinds = self.axes["kind"]
        for name in ("normal", "negative", "concurrency", "idempotent", "recovery",
                     "refresh", "stale", "legacy"):
            self.assertGreater(kinds.get(name, 0), 0, f"案例种类 {name} 没有覆盖")

    # ---------------- 五阶段 13 子步骤 ----------------
    def test_substep_table_has_13_rows(self):
        table = coverage.substep_table(self.cases)
        self.assertEqual(len(table), 13)
        self.assertEqual([row["sub"] for row in table], list(SUB_STEPS))

    def test_every_substep_has_normal_and_precondition_failure(self):
        table = coverage.substep_table(self.cases)
        missing = [row["key"] for row in table if not row["ok"]]
        self.assertEqual(missing, [], f"子步骤缺少正常 / 前置失败案例：{missing}")
        for row in table:
            self.assertGreaterEqual(row["normal"], 1, f"{row['key']} 缺正常案例")
            self.assertGreaterEqual(row["precondition"], 1, f"{row['key']} 缺前置失败案例")

    def test_substep_rows_match_five_phase_table(self):
        table = {row["sub"]: row for row in coverage.substep_table(self.cases)}
        flat = [sub for phase in PHASES for sub in phase["subs"]]
        self.assertEqual(sorted(table), sorted(flat))
        self.assertEqual(len({row["phase"] for row in table.values()}), 5,
                         "13 个子步骤必须落在 5 个阶段里")
        for phase in PHASES:
            for sub in phase["subs"]:
                self.assertEqual(table[sub]["phase"], phase["no"])
                self.assertEqual(table[sub]["phase_title"], phase["title"])

    # ---------------- 报价六步 ----------------
    def test_quote_step_table_covers_six_steps(self):
        table = coverage.quote_step_table(self.cases)
        self.assertEqual([row["step"] for row in table], [1, 2, 3, 4, 5, 6])

    def test_every_quote_step_has_normal_deny_recovery(self):
        table = coverage.quote_step_table(self.cases)
        for row in table:
            self.assertTrue(row["ok"], f"报价第 {row['step']} 步覆盖不足：{row}")
            self.assertTrue(row["normal"], f"第 {row['step']} 步缺正常案例")
            self.assertTrue(row["deny"], f"第 {row['step']} 步缺拒绝案例")
            self.assertTrue(row["recovery"], f"第 {row['step']} 步缺恢复案例")

    # ---------------- 必修清单 ----------------
    def test_required_coverage_all_ok(self):
        items = coverage.required_coverage(self.cases)
        self.assertTrue(items)
        failures = [item for item in items if not item["ok"]]
        self.assertEqual(failures, [], f"必修覆盖清单未满足：{failures}")

    def test_domain_minimums(self):
        counts = self.axes["domain"]
        expected = {"quote": 45, "tech": 65, "cross_agent": 30, "auth_acl": 15,
                    "session_history": 15, "concurrency": 20, "llm_contract": 10,
                    "failure_recovery": 10, "ui_protocol": 10}
        for domain, minimum in expected.items():
            self.assertGreaterEqual(counts.get(domain, 0), minimum, f"{domain} 数量不足")

    def test_cross_agent_and_concurrency_volume(self):
        counts = self.axes["domain"]
        self.assertGreaterEqual(counts.get("cross_agent", 0), 30, "跨 Agent / 任务流至少 30 条")
        self.assertGreaterEqual(counts.get("concurrency", 0), 20, "并发 / 幂等至少 20 条")

    # ---------------- 渲染与一致性 ----------------
    def test_renderers_produce_text(self):
        for text in (coverage.render_matrix(self.axes),
                     coverage.render_substeps(self.cases),
                     coverage.render_quote_steps(self.cases),
                     coverage.render_required(self.cases)):
            self.assertIsInstance(text, str)
            self.assertGreater(len(text), 10)

    def test_report_matrix_matches_dataset(self):
        report = runner.build_report([{"case": case, "status": "passed", "reason": ""}
                                      for case in self.cases], {"layer": ""})
        self.assertEqual(report["matrix"]["domain"], self.axes["domain"])
        self.assertEqual(len(report["substeps"]), 13)
        self.assertEqual(len(report["quote_steps"]), 6)

    def test_every_case_belongs_to_a_known_domain_and_kind(self):
        for case in self.cases:
            self.assertTrue(coverage.kinds_of(case), f"{case.case_id} 没有任何覆盖种类")
            self.assertIn(case.get("domain"), self.axes["domain"])


def tearDownModule():
    """把 prodkit 钉死的隔离环境放回原值，不把 CPQ_SSO 等开关泄漏给同进程其他测试。"""
    try:
        from scripts.cpq_eval import prodkit

        prodkit.restore_process_env()
    except Exception:  # pragma: no cover - 没装载过就无需还原
        pass


if __name__ == "__main__":
    unittest.main()
