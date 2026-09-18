# -*- coding: utf-8 -*-
"""production-backed 案例的「真能杀死回归」证明（mutation / sentinel）。

为什么需要这一层：`simulation` 的案例、规则、执行实现是同一套新增代码，Sim 全绿
不能证明真实 FastAPI 路由 / ACL / 事务 / Agent 工具链还在工作。本模块做两件事：

1. **mutation 证明**：主动把真实生产函数改坏（monkeypatch 真实模块），再跑某条
   production-backed 案例，断言它必须变红，并给出「哪条真实路由 / 哪层门禁」坏了。
   若改坏了仍然绿，说明这条案例没有真的打在production代码上 —— 测试直接失败。
2. **历史兼容 fixture 证明**：三组完整 fixture（报价第 4 步 / 旧 page_context 项目 /
   中断的跨 Agent 任务）由真实 `cpq_wf` / `workflow_projection` / `store` / `tasks`
   执行，断言历史原文不被改写、步骤不回退、恢复不新建重复项目 / 任务 / 会话。

允许 monkeypatch **真实生产函数**来制造故障；不允许把断言对象换成 Sim。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import dataset, prodkit, production, runner  # noqa: E402

OPTIONS = runner.Options(quiet=True, no_report=True)


class ProductionBackedCaseTest(unittest.TestCase):
    """所有 mutation / 历史兼容测试的公共脚手架。"""

    @classmethod
    def setUpClass(cls):
        cls.fixtures = dataset.load_fixtures()
        cls.cases = {case.case_id: case for case in dataset.load_cases()}
        ok, reason = prodkit.available()
        if not ok:                                     # pragma: no cover - 依赖缺失才走到
            raise unittest.SkipTest(f"生产模块不可装载：{reason}")
        cls.mods = prodkit.modules()

    # ---------------- 工具 ----------------
    def case(self, case_id):
        case = self.cases.get(case_id)
        self.assertIsNotNone(case, f"数据集里没有案例 {case_id}")
        return case

    def run_case(self, case_id) -> dict:
        return runner.run_case(self.case(case_id), self.fixtures, OPTIONS)

    def assert_green(self, case_id):
        result = self.run_case(case_id)
        self.assertEqual(result["status"], "passed",
                         f"{case_id} 基线应为通过：{result.get('reason')}")
        self.assertTrue(result.get("trace"), f"{case_id} 必须命中真实生产入口")
        return result

    def assert_killed(self, case_id, patcher, must_mention="") -> dict:
        """先证明未变异时绿；变异后必须红，且失败原因指向真实入口。"""
        self.assert_green(case_id)
        with patcher:
            result = self.run_case(case_id)
        self.assertEqual(result["status"], "failed",
                         f"生产回归未被捕获：{case_id} 在变异后仍然 "
                         f"{result['status']}（结论：该案例没有真正覆盖这段生产逻辑）")
        if must_mention:
            self.assertIn(must_mention, result.get("reason") or "",
                          f"{case_id} 的失败原因没有指出真实问题：{result.get('reason')}")
        return result


class MutationSentinelTest(ProductionBackedCaseTest):
    """7 项必证的生产回归（用户第八节）+ 1 项「退化成 Sim」守护。"""

    # 1. 删除 finance_handoff 的财务角色池可读逻辑
    def test_mutation_finance_role_pool_read_is_killed(self):
        project_access = self.mods["project_access"]
        patcher = mock.patch.object(project_access, "_finance_handoff_visible",
                                   staticmethod(lambda project_id: False))
        self.assert_killed("acl.real.finance.open_handoff_project", patcher, must_mention="status")

    def test_mutation_finance_role_pool_read_is_killed_across_route_sweep(self):
        project_access = self.mods["project_access"]
        patcher = mock.patch.object(project_access, "_finance_handoff_visible",
                                   staticmethod(lambda project_id: False))
        result = self.assert_killed("acl.real.finance.read_route_sweep_not_blocked", patcher,
                                    must_mention="acl_blocked")
        # 报告要能指出是哪条真实读路由被通用 ACL 判成「项目不存在」
        self.assertIn("/api/projects/{project_id}/", result.get("reason") or "")

    # 2. 删除来源报价的销售角色池可读逻辑
    def test_mutation_sales_role_pool_read_is_killed(self):
        project_access = self.mods["project_access"]
        patcher = mock.patch.object(project_access, "_quote_link_visible",
                                   staticmethod(lambda project_id: False))
        self.assert_killed("acl.real.sales.open_quote_linked_project", patcher, must_mention="status")

    # 3. 把全部写接口强制套用通用 project write ACL（专属动作白名单失效）
    def test_mutation_generic_write_acl_on_professional_route_is_killed(self):
        project_access = self.mods["project_access"]
        patcher = mock.patch.object(project_access, "is_contribute_route",
                                   staticmethod(lambda method, path: False))
        result = self.assert_killed("acl.real.finance.save_cost_note_changes_state", patcher,
                                    must_mention="acl_blocked_professional_write")
        # 报告要能指出是哪条真实写路由 / 哪一步被通用 ACL 提前截断
        self.assertIn("save:", result.get("reason") or "")
        self.assertIn("PUT /api/projects/{project_id}/cost-review",
                      str((self.case("acl.real.finance.save_cost_note_changes_state")
                           .get("covers_routes")) or ""))

    def test_mutation_generic_write_acl_on_cost_confirm_is_killed(self):
        project_access = self.mods["project_access"]
        patcher = mock.patch.object(project_access, "is_contribute_route",
                                   staticmethod(lambda method, path: False))
        result = self.assert_killed("acl.real.finance.confirm_cost_reaches_business_gate", patcher,
                                    must_mention="acl_blocked_professional_write")
        self.assertIn("confirm:", result.get("reason") or "")
        self.assertIn("POST /api/projects/{project_id}/cost-review/confirm",
                      str((self.case("acl.real.finance.confirm_cost_reaches_business_gate")
                           .get("covers_routes")) or ""))

    def test_mutation_generic_write_acl_on_reviewer_route_is_killed(self):
        project_access = self.mods["project_access"]
        patcher = mock.patch.object(project_access, "is_contribute_route",
                                   staticmethod(lambda method, path: False))
        result = self.assert_killed("acl.real.director.report_review_reaches_gate", patcher,
                                    must_mention="acl_blocked_professional_write")
        self.assertIn("review:", result.get("reason") or "")
        self.assertIn("POST /api/projects/{project_id}/process-report/review",
                      str((self.case("acl.real.director.report_review_reaches_gate")
                           .get("covers_routes")) or ""))

    # 4. 绕过 route 自己的 _require（越权者被放行）
    def test_mutation_bypassing_route_require_is_killed(self):
        main = self.mods["main"]
        patcher = mock.patch.object(main, "_require", lambda user, roles, message="": None)
        result = self.assert_killed("acl.real.finance_related_but_wrong_duty_gets_route_403",
                                    patcher, must_mention="steps.publish.status")
        self.assertIn("403", result.get("reason") or "")

    # 5. 报价回传推进错业务实例
    def test_mutation_handoff_to_wrong_business_instance_is_killed(self):
        case_link = prodkit.module("cpq_case_link")
        real_decide = case_link.decide

        def wrong_instance(*args, **kwargs):
            got = dict(real_decide(*args, **kwargs) or {})
            got["quote_session_id"] = "QS-WRONG"
            return got

        patcher = mock.patch.object(case_link, "decide", wrong_instance)
        self.assert_killed("xagent.real.case_link_single_candidate_linked", patcher,
                           must_mention="quote_session_id")

    # 6. 报价 current_step 倒退
    def test_mutation_quote_step_rollback_is_killed(self):
        cpq_wf = prodkit.module("cpq_wf")
        patcher = mock.patch.object(cpq_wf, "advance_step_no",
                                    lambda card, step_no: int(step_no or 1))
        self.assert_killed("xagent.real.advance_step_never_regresses", patcher, must_mention="result")

    def test_mutation_quote_step_rollback_is_killed_for_legacy_card(self):
        cpq_wf = prodkit.module("cpq_wf")
        patcher = mock.patch.object(cpq_wf, "advance_step_no",
                                    lambda card, step_no: int(step_no or 1))
        self.assert_killed("history.real.legacy_quote_late_handoff_no_step_rollback", patcher,
                           must_mention="result")

    # 7. 重复回传产生两条副作用（幂等键失效）
    def test_mutation_duplicate_handoff_side_effect_is_killed(self):
        store = self.mods["store"]
        real_append = store.append_session_event

        def no_idempotency(project_id, event):
            stripped = dict(event or {})
            stripped.pop("key", None)
            stripped.pop("task", None)
            return real_append(project_id, stripped)

        patcher = mock.patch.object(store, "append_session_event", no_idempotency)
        self.assert_killed("history.real.session_event_replay_idempotent_and_ordered", patcher,
                           must_mention="steps.load.result")

    def test_mutation_duplicate_handoff_side_effect_is_killed_under_concurrency(self):
        store = self.mods["store"]
        real_append = store.append_session_event

        def no_idempotency(project_id, event):
            stripped = dict(event or {})
            stripped.pop("key", None)
            return real_append(project_id, stripped)

        patcher = mock.patch.object(store, "append_session_event", no_idempotency)
        self.assert_killed("conc.real.barrier_session_event_single_row_per_key", patcher,
                           must_mention="steps.events.result")

    # 守护：声明 production-backed 却退化成 Sim —— 必须是 failed 而不是 passed
    def test_declared_production_backed_but_sim_only_is_rejected(self):
        case_id = "acl.real.finance.open_handoff_project"
        self.assert_green(case_id)
        real_run = production.run_case

        def sim_only(case, case_dir=""):
            outcome = real_run(case, case_dir)
            if outcome.get("status") == "executed":
                outcome["observation"]["trace"] = []
            return outcome

        with mock.patch.object(production, "run_case", sim_only):
            result = self.run_case(case_id)
        self.assertEqual(result["status"], "failed", "退化成 Sim 的案例必须被判失败")
        self.assertIn("没有任何生产入口被命中", result.get("reason") or "")


class ExtendedMutationSentinelTest(ProductionBackedCaseTest):
    """本批新增的 5 项生产回归守卫（角色池 / 幂等 / 事务 / 关联 / 历史 / 404 语义）。"""

    # 8. 把专业审核接口套用 engineer-only 权限：总监/审核角色必须立刻变红
    def test_mutation_engineer_only_review_route_is_killed(self):
        auth = self.mods["auth"]
        patcher = mock.patch.object(auth, "DIRECTOR_ROLES", {"process_engineer"})
        result = self.assert_killed("acl.real.director.report_review_reaches_gate", patcher,
                                    must_mention="403")
        self.assertIn("/api/projects/{project_id}/process-report/review",
                      str((self.case("acl.real.director.report_review_reaches_gate")
                           .get("covers_routes")) or ""))

    # 9. 忽略来源报价关联字段（source_quote_id / quote_session_id）：销售经理可读必须变红
    def test_mutation_ignoring_source_quote_link_is_killed(self):
        project_access = self.mods["project_access"]
        store = self.mods["store"]

        def ignore_source_quote(project_id):
            case = store.load_business_case(project_id) or {}
            return bool(str(case.get("source_quote_id") or ""))

        patcher = mock.patch.object(project_access, "_quote_link_visible", ignore_source_quote)
        self.assert_killed("acl.real.sales.open_quote_linked_project", patcher, must_mention="status")

    # 10. 历史会话查询只返回最后一条消息：时间线回放必须变红
    def test_mutation_history_returns_only_last_message_is_killed(self):
        store = self.mods["store"]
        real_load = store.load_session_events

        def only_last(project_id, *args, **kwargs):
            rows = list(real_load(project_id, *args, **kwargs) or [])
            return rows[-1:] if rows else []

        patcher = mock.patch.object(store, "load_session_events", only_last)
        self.assert_killed("history.real.agent_events_http_replays_persisted_timeline", patcher)

    # 11. 把「不存在项目」的 404 与「存在但无权限」响应混淆：必须变红
    def test_mutation_confusing_not_found_with_forbidden_is_killed(self):
        project_access = self.mods["project_access"]
        store = self.mods["store"]
        real_require = project_access.require_project_access

        def confused(project_id, user, mode="read"):
            meta = store.load_meta(project_id)
            if meta and not project_access.can_read(user, meta):
                # 变异：把「存在但没权限」暴露成 403，用户就能反推项目是否存在
                raise project_access.ProjectAccessError("forbidden", "无权查看该项目")
            return real_require(project_id, user, mode)

        with mock.patch.object(project_access, "require_project_access", confused):
            result = self.run_case("fail.real.outsider_cannot_probe_project_existence")
        self.assertEqual(result["status"], "failed",
                         "404 与 403 被混淆后测试仍然通过：不可枚举性没有被真正守护")


class PostgresMutationSentinelTest(unittest.TestCase):
    """真库事务 / 幂等守卫：用 MUTATIONS 把约束或回滚去掉，案例必须变红。

    没有隔离 PostgreSQL 环境时整类 skip（不算通过）；报告里会单列 integration skipped。
    """

    @classmethod
    def setUpClass(cls):
        from scripts.cpq_eval import pg_integration
        cls.pg = pg_integration
        ok, reason = pg_integration.available()
        if not ok:
            raise unittest.SkipTest(f"隔离 PostgreSQL 不可用，PG sentinel 跳过：{reason}")
        cls.cases = {case.case_id: case for case in dataset.load_cases()}

    def _run(self, case_id, mutate):
        from scripts.cpq_eval import production as production_mod
        case = self.cases.get(case_id)
        self.assertIsNotNone(case, f"数据集里没有 PG 案例 {case_id}")
        baseline = self.pg.run_case(case)
        self.assertEqual(baseline.get("status"), "executed", baseline.get("reason"))
        errors = production_mod.compare_production(case, baseline["observation"],
                                                   runner.compare, runner.resolve_path)
        self.assertEqual(errors, [], f"{case_id} 基线应为通过：{errors[:3]}")
        broken = self.pg.run_case(case, mutate=mutate)
        self.assertEqual(broken.get("status"), "executed", broken.get("reason"))
        errors = production_mod.compare_production(case, broken["observation"],
                                                   runner.compare, runner.resolve_path)
        self.assertNotEqual(errors, [], f"注入 {mutate} 后 {case_id} 仍然通过：守卫无效")
        return errors

    def test_mutation_handoff_unique_index_removed_is_killed(self):
        errors = self._run("pg.handoff.concurrent_same_key_single_row", "drop_handoff_index")
        self.assertTrue(any("steps.pg.result.rows" in e for e in errors), errors)

    def test_mutation_task_open_unique_index_removed_is_killed(self):
        errors = self._run("pg.task.db_rejects_duplicate_open", "drop_task_open_index")
        self.assertTrue(any("steps.pg.result" in e for e in errors), errors)

    def test_mutation_reuse_lookup_disabled_is_killed(self):
        errors = self._run("pg.task.supersede_on_signature_change", "blind_task_lookup")
        self.assertTrue(any("steps.pg.result" in e for e in errors), errors)

    def test_mutation_handoff_conflict_clause_removed_is_killed(self):
        errors = self._run("pg.handoff.concurrent_same_key_single_row", "handoff_no_conflict")
        self.assertTrue(any("steps.pg.result" in e for e in errors), errors)

    def test_mutation_transaction_rollback_removed_is_killed(self):
        errors = self._run("pg.tx.midway_failure_rolls_back_all", "no_rollback_tx")
        self.assertTrue(any("steps.pg.result.rolled_back" in e for e in errors), errors)

    def test_mutation_autocommit_connection_is_killed(self):
        errors = self._run("pg.tx.midway_failure_rolls_back_all", "autocommit_tx")
        self.assertTrue(any("steps.pg.result" in e for e in errors), errors)

    def test_mutation_atomic_claim_guard_removed_is_killed(self):
        errors = self._run("pg.claim.concurrent_two_claimers_one_winner",
                           "claim_without_open_guard")
        self.assertTrue(any("steps.pg.result.successes" in e for e in errors), errors)

    def test_mutation_claim_eligibility_bypassed_is_killed(self):
        errors = self._run("pg.claim.role_denied_no_side_effect", "claim_ignore_eligibility")
        self.assertTrue(any("steps.pg.result" in e for e in errors), errors)


class LegacyFixtureCompatTest(ProductionBackedCaseTest):
    """三组完整 fixture 由真实生产代码执行的历史兼容证明。"""

    def setUp(self):
        self.store = self.mods["store"]
        self.projection = prodkit.module("backend.services.workflow_projection")
        self.tasks = self.mods["tasks"]
        self.cpq_wf = prodkit.module("cpq_wf")
        self.fresh_dir = root = Path(prodkit.fresh_dir_for("legacy"))
        prodkit.fresh_store(str(root / "meta"))

    def identity(self, spec):
        return prodkit.make_identity(spec)

    def seed(self, spec):
        ids = prodkit.seed_projects(spec)
        return next(iter(ids.values()))

    # ---------------- 1. legacy_quote_case ----------------
    def test_legacy_quote_card_at_step_four_never_rolls_back(self):
        fixture = self.fixtures["quote/legacy_quote_case.json"]
        card = fixture["card"]
        self.assertEqual(card["current_step"], 4)
        for late_step in (1, 2, 3):
            self.assertEqual(self.cpq_wf.advance_step_no(card, late_step), 4,
                             f"历史回传到第 {late_step} 步把报价卡片写回退了")
        self.assertEqual(self.cpq_wf.advance_step_no(card, 5), 5)
        self.assertEqual(card["current_step"], 4, "真实推进函数不得就地改写原卡片对象")

    # ---------------- 2. legacy_tech_project ----------------
    def test_legacy_project_history_text_is_not_rewritten(self):
        fixture = self.fixtures["tech/legacy_tech_project.json"]["project"]
        pid = self.seed({"legacy": {"owner": "carol", "filename": "legacy.dxf",
                                    "business_case": fixture["business_case"],
                                    "participants": []}})
        for row in fixture["timeline"]:
            self.store.append_session_event(pid, {**row, "key": f"legacy:{row['seq']}"})
        rows = self.store.load_session_events(pid)
        self.assertEqual([row["seq"] for row in rows], [1, 2, 3])
        self.assertEqual(rows[0]["text"], fixture["timeline"][0]["text"],
                         "历史消息原文被改写了")
        self.assertEqual(rows[0]["stage"] if "stage" in rows[0] else rows[0].get("page_context"),
                         fixture["timeline"][0]["page_context"],
                         "历史 page_context / stage 原文被改写了")

    def test_legacy_project_projection_maps_to_five_phases_thirteen_stages(self):
        fixture = self.fixtures["tech/legacy_tech_project.json"]["project"]
        pid = self.seed({"legacy": {"owner": "carol", "filename": "legacy.dxf",
                                    "business_case": fixture["business_case"],
                                    "participants": []}})
        stages_table = self.mods["workflow_stages"]
        self.assertEqual(len(stages_table.PHASES), 5, "五阶段口径被改坏了")
        self.assertEqual(len(stages_table.STAGES), 13, "13 子步骤口径被改坏了")
        data = self.projection.build_projection(pid, self.identity(
            {"username": "carol", "cpq_role_code": "process_mgr", "tech_role": "process_engineer"}))
        self.assertEqual(len(data["phases"]), 5)
        self.assertEqual(len(data["stages"]), 13)
        self.assertEqual([row["sub"] for row in data["stages"]],
                         [row["sub"] for row in stages_table.STAGES])
        self.assertEqual(data["stages"][12]["stage_id"], "report-publish")
        self.assertTrue(all(row["viewable"] for row in data["stages"]))
        self.assertEqual(data["project_id"], pid, "投影返回了别的项目")

    # ---------------- 3. interrupted_cross_agent_case ----------------
    def test_interrupted_cross_agent_task_recovers_without_duplicates(self):
        fixture = self.fixtures["handoff/interrupted_cross_agent_case.json"]
        pid = self.seed({"interrupted": {"owner": "carol", "filename": "interrupted.dxf",
                                         "business_case": fixture["project"]["business_case"],
                                         "participants": []}})
        self.store.save_task(pid, {"task_id": "t_int1", "kind": "tech_new_product",
                                   "status": "running", "progress": "", "error": "",
                                   "detail": {}, "steps": [], "process": []})
        projects_before = len(self.store.list_projects())
        self.assertEqual(self.tasks.recover_interrupted_tasks(), 1)
        self.assertEqual(self.tasks.recover_interrupted_tasks(), 0, "重复恢复又改了一次")
        tasks = self.store.list_tasks(pid)
        self.assertEqual(len(tasks), 1, "恢复过程新建了重复任务")
        self.assertEqual(tasks[0]["status"], "interrupted")
        self.assertNotEqual(tasks[0]["status"], "succeeded", "中断任务被伪装成成功")
        events = self.store.load_session_events(pid)
        self.assertEqual(len(events), 1, "恢复过程新建了重复会话卡")
        self.assertEqual(len(self.store.list_projects()), projects_before,
                         "恢复过程新建了重复项目")


def tearDownModule():
    """把 prodkit 钉死的隔离环境放回原值，不把 CPQ_SSO 等开关泄漏给同进程其他测试。"""
    try:
        from scripts.cpq_eval import prodkit

        prodkit.restore_process_env()
    except Exception:  # pragma: no cover - 没装载过就无需还原
        pass


if __name__ == "__main__":
    unittest.main()
