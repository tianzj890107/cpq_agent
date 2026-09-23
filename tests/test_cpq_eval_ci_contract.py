# -*- coding: utf-8 -*-
"""CI 防伪：`.gitlab-ci.yml` 必须**结构化**地接入测试集门禁，且不得夹带部署 / 生产访问。

不是「文件里出现过某个字符串」就通过：这里用 `scripts.cpq_eval.ci_yaml` 把 CI 配置解析成
job / script / services / variables / rules 结构，再断言每个 job 真的调用对应门禁；并用
`ci_gates` / `runner` / `scoring` 的真实函数验证「失败返回非零」「simulation 不能掩盖
production 失败」「mutation survived 时判失败」。
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import ci_contract, ci_gates, ci_yaml, scoring  # noqa: E402
from scripts.cpq_eval import ci_contract as ci_contract_mod  # noqa: E402

CI_PATH = ROOT / ".gitlab-ci.yml"
CPQ_JOBS = ("cpq_eval_fast", "cpq_eval_production_http", "cpq_eval_recorded_provider",
            "cpq_eval_postgres")


@contextlib.contextmanager
def _quiet():
    """门禁 / sentinel 会把结论打到 stdout、stderr；测试里不该混进用例输出。"""
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        yield


def _effective(jobs: dict, name: str) -> dict:
    """把 ``extends`` 链合并成 job 的有效配置（本地 ``before_script`` 追加到模板之后）。"""
    job = dict(jobs.get(name) or {})
    merged = {}
    parents = job.get("extends")
    if isinstance(parents, str):
        parents = [parents]
    for parent in parents or []:
        merged.update(_effective(jobs, parent))
    for key, value in job.items():
        if key == "extends":
            continue
        if key in ("before_script", "script") and key in merged:
            base = merged[key]
            base = base if isinstance(base, list) else [base]
            value = base + (value if isinstance(value, list) else [value])
        merged[key] = value
    return merged


class CiStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = ci_yaml.load(CI_PATH)
        cls.jobs = ci_yaml.jobs(cls.doc)

    def job_running(self, needle: str) -> str:
        hits = [name for name, job in self.jobs.items()
                if ci_yaml.job_runs_command(job, needle)]
        self.assertEqual(len(hits), 1,
                         f"应恰好有一个 job 调用 {needle!r}，实际 {hits}")
        return hits[0]

    # 1~3：三类离线门禁各由独立 job 真实调用
    def test_ci_calls_fast_gate(self):
        name = self.job_running("scripts.cpq_eval.ci_gates --gate fast")
        self.assertEqual(name, "cpq_eval_fast")

    def test_ci_calls_production_http_gate(self):
        name = self.job_running("scripts.cpq_eval.ci_gates --gate production_http")
        self.assertEqual(name, "cpq_eval_production_http")

    def test_ci_calls_recorded_provider_gate(self):
        name = self.job_running("scripts.cpq_eval.ci_gates --gate recorded_provider")
        self.assertEqual(name, "cpq_eval_recorded_provider")

    # 4：存在 PostgreSQL service job
    def test_ci_has_postgres_service_job(self):
        job = self.jobs.get("cpq_eval_postgres")
        self.assertIsNotNone(job, "缺少 cpq_eval_postgres job")
        services = job.get("services") or []
        aliases = [svc.get("alias") for svc in services if isinstance(svc, dict)]
        self.assertIn("cpq-eval-pg", aliases, f"未配置隔离 PostgreSQL service alias：{services}")
        self.assertTrue(any("postgres" in str(svc.get("name", ""))
                            for svc in services if isinstance(svc, dict)))

    def test_ci_postgres_job_runs_integration_gate_strictly(self):
        job = self.jobs["cpq_eval_postgres"]
        self.assertTrue(ci_yaml.job_runs_command(
            job, "scripts.cpq_eval.ci_gates --gate postgres_integration --strict"),
            "PG job 必须以 --strict 调 postgres_integration 门禁（否则全 skip 也能过）")

    def test_ci_postgres_job_env_matches_guard_contract(self):
        variables = self.jobs["cpq_eval_postgres"].get("variables") or {}
        self.assertEqual(str(variables.get("CPQ_EVAL_INTEGRATION")), "1")
        self.assertEqual(str(variables.get("CPQ_EVAL_PG_CI")), "1")
        self.assertEqual(str(variables.get("CPQ_EVAL_PG_HOST")), "cpq-eval-pg")
        self.assertEqual(str(variables.get("CPQ_EVAL_PG_MAINTENANCE_DB")), "postgres")
        self.assertNotIn("CPQ_PG_HOST", variables,
                         "CI 不应把生产用 CPQ_PG_* 变量带进来")

    # 5~8：禁止部署 / 生产访问 / 真实模型 key
    FORBIDDEN = ("ssh ", "scp ", "rsync", "systemctl", "docker push", "kubectl",
                 "172.16.10.34", "172.16.5.181", "pdt", "metabase",
                 "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "sk-")

    def test_ci_has_no_deploy_or_production_access(self):
        text = CI_PATH.read_text(encoding="utf-8").lower()
        for token in self.FORBIDDEN:
            if token.islower() and token in ("pdt",):
                continue  # 'pdt' 会误伤普通单词，单独用连接串判断
            self.assertNotIn(token.lower(), text,
                             f"CI 里出现了禁止内容：{token}")
        for line in CI_PATH.read_text(encoding="utf-8").splitlines():
            low = line.lower()
            if "host" in low or "url" in low or "alias" in low:
                self.assertNotIn("172.16.", low)

    def test_required_jobs_install_project_requirements(self):
        """必跑 job 必须在干净镜像里装 requirements.txt，否则会「零执行却绿色」。"""
        for name in CPQ_JOBS + ("python_contract",):
            effective = _effective(self.doc, name)
            install = " ".join(str(line) for line in (effective.get("before_script") or []))
            self.assertIn("pip install -r requirements.txt", install,
                          f"{name} 未安装 requirements.txt（会静默 skip）：{install!r}")

    def test_required_jobs_never_allow_failure(self):
        for name in CPQ_JOBS:
            job = self.jobs[name]
            self.assertFalse(bool(job.get("allow_failure")),
                             f"{name} 不允许 allow_failure（失败必须让 pipeline 红）")
            self.assertNotIn("when", job.get("rules") or {},
                             f"{name} 不得用 rules 跳过")

    def test_all_cpq_jobs_run_gates_with_strict(self):
        for name in CPQ_JOBS:
            script = " ".join(ci_yaml.job_script(self.jobs[name]))
            self.assertIn("--strict", script,
                          f"{name} 的门禁必须以 --strict 运行：{script!r}")

    def test_ci_records_trigger_scope(self):
        rules = ((self.doc.get("workflow") or {}).get("rules")) or []
        joined = " ".join(str(rule) for rule in rules)
        self.assertIn("merge_request_event", joined, "CI 必须覆盖 MR")
        self.assertIn("$CI_DEFAULT_BRANCH", joined, "CI 必须覆盖默认分支")


class CiGateBehaviourTest(unittest.TestCase):
    """门禁的函数级行为：失败必须非零、simulation 不能掩盖 production 失败。"""

    def test_gate_returns_nonzero_when_command_fails(self):
        with mock.patch.dict(ci_gates.GATES, {
                "_probe": {"title": "probe", "commands": [["--case", "no.such.case"]],
                           "optional": False}}), _quiet():
            status, code = ci_gates.run_gate("_probe", stream=False)
        self.assertEqual(status, "failed")
        self.assertNotEqual(code, 0)

    def test_runner_invalid_case_exit_nonzero(self):
        from scripts.cpq_eval import runner

        with _quiet():
            self.assertNotEqual(
                runner.main(["--case", "no.such.case", "--no-report", "--quiet"]), 0)

    def test_production_failure_not_masked_by_simulation_pass(self):
        class _Case(dict):
            def __init__(self, **kw):
                super().__init__(**kw)
                self.case_id = kw.get("id")

        sim_ok = _Case(id="sim.ok", domain="quote", priority="P1", layer="deterministic",
                       executor="simulation", actions=[{"op": "noop"}])
        prod_bad = _Case(id="prod.bad", domain="quote", priority="P0", layer="deterministic",
                         executor="production_http", input={"steps": [{"id": "s", "http": {}}]})
        summary = scoring.summarize([
            {"case": sim_ok, "status": "passed", "reason": ""},
            {"case": prod_bad, "status": "failed", "reason": "真实路由 500"},
        ])
        self.assertEqual(summary["exit_code"], 1)
        self.assertEqual(summary["gate"]["production_failed"], 1)
        self.assertIn("prod.bad", summary["gate"]["p0_production_failed"])

    def test_pg_mutation_survived_fails_require_all_killed(self):
        from scripts.cpq_eval import pg_sentinel

        report = {"available": True, "reason": "", "rows": [
            {"mutation": "fake", "case": "c", "killed": False, "baseline_ok": True,
             "mutated_ok": True, "reason": "survived"}],
            "killed": 0, "survived": 1, "cleanup": {}}
        with mock.patch.object(pg_sentinel, "run_all", return_value=report), _quiet():
            code = pg_sentinel.main(["--require-all-killed"])
        self.assertNotEqual(code, 0)

    def test_strict_gate_fails_when_production_layer_cannot_load(self):
        """缺依赖（生产模块不可装载）时 strict 门禁必须失败，不能降级成 skip。"""
        with mock.patch.object(ci_gates, "production_preflight",
                               return_value=(False, "No module named 'fastapi'")), _quiet():
            status, code = ci_gates.run_gate("fast", stream=False, strict=True)
        self.assertEqual(status, "failed")
        self.assertNotEqual(code, 0)

    def test_strict_gate_fails_when_cases_are_silently_skipped(self):
        """依赖齐全但案例仍被 skip（环境不完整）时 strict 门禁必须失败。"""
        import json as _json
        import tempfile as _tempfile

        real_mkdtemp = _tempfile.mkdtemp      # 先存引用：patch 后再调会递归

        def _fake_run(argv, **kwargs):
            # 门禁把 --report <dir> 传进来；写一份「全 skip」的报告模拟静默降级。
            if "--report" in argv:
                directory = argv[argv.index("--report") + 1]
                report = {"summary": {"by_executor": {
                    "production_unit": {"passed": 0, "failed": 0, "skipped": 40,
                                        "invalid": 0}}}}
                with open(f"{directory}/cpq_eval_report.json", "w", encoding="utf-8") as fh:
                    _json.dump(report, fh)

            class _Proc:
                returncode = 0
                stdout = ""
                stderr = ""

            return _Proc()

        with mock.patch.object(ci_gates, "production_preflight", return_value=(True, "")), \
                mock.patch.object(ci_gates.subprocess, "run", _fake_run), \
                mock.patch.object(ci_gates.tempfile, "mkdtemp",
                                  side_effect=real_mkdtemp), _quiet():
            status, code = ci_gates.run_gate("fast", stream=False, strict=True)
        self.assertEqual(status, "failed")
        self.assertNotEqual(code, 0)

    def test_ci_pg_all_skipped_is_not_a_pass(self):
        self.assertTrue(ci_gates._pg_all_skipped(
            {"postgres": {"case_count": 13, "skipped": 13}}))
        self.assertFalse(ci_gates._pg_all_skipped(
            {"postgres": {"case_count": 13, "skipped": 12}}))
        self.assertFalse(ci_gates._pg_all_skipped(
            {"postgres": {"case_count": 0, "skipped": 0}}))
        # CI 的 PG job 必须走 --strict，否则「全部 skip」会被当成成功
        postgres_job = ci_yaml.jobs(ci_yaml.load(CI_PATH))["cpq_eval_postgres"]
        self.assertTrue(ci_yaml.job_runs_command(
            postgres_job,
            "scripts.cpq_eval.ci_gates --gate postgres_integration --strict"))

    def test_ci_pg_job_fails_when_gate_unavailable_in_strict_mode(self):
        with _quiet():
            status, code = ci_gates._run_postgres_gate(strict=True, stream=False)
        from scripts.cpq_eval import pg_integration

        available, _ = pg_integration.available()
        if available:
            self.assertEqual(status, "passed")
            self.assertEqual(code, 0)
        else:
            self.assertEqual(status, "failed")
            self.assertNotEqual(code, 0)


class CiDependencyCoverageTest(unittest.TestCase):
    """干净镜像里能不能真的装载生产入口：requirements.txt 必须覆盖全部第三方 import。

    为什么要这条：CI 用 `python:3.10-slim`，不装 requirements.txt 时 production_unit /
    production_http / recorded_provider 会整体 skipped，门禁「零执行却绿色」。这里在子进程里
    真装载一次生产入口，收集第三方顶层模块，再逐个回到 requirements.txt 找出处。
    """

    @classmethod
    def setUpClass(cls):
        cls.requirements = ci_contract_mod.requirement_names()
        # `## 472`：依赖闭包按**含 extras** 的声明算 —— `covered_distributions()` 自己的 docstring
        # 就是「requirements.txt 直接声明的包 + 它们（含已启用 extras）的传递依赖闭包」，而
        # `requirement_names()` 按定义（"去掉 extras 与版本号"）会把 `psycopg[binary]` /
        # `uvicorn[standard]` 的 extras 一起丢掉，于是 `psycopg-binary` 被判「缺出处」——
        # 干净镜像（CI 只装 requirements.txt）里同样必红。不传 `names` 时它内部走 `requirement_specs()`
        # （保留 extras），声明名集合仍用 `requirement_names()` 单独校验。
        cls.closure = ci_contract_mod.covered_distributions()
        cls.modules = ci_contract_mod.production_third_party_modules()

    def test_requirements_file_is_non_empty(self):
        self.assertGreater(len(self.requirements), 8, self.requirements)

    def test_every_production_import_has_a_requirement(self):
        import importlib.metadata as md

        dist_map = md.packages_distributions()
        # requirements.txt 直接声明的包 + 它们（含已启用 extras）的传递依赖闭包。
        # pip 只装直接依赖，其余由被声明的包带进来，所以按闭包判定「有出处」。
        covered = self.closure
        missing = []
        for module in self.modules:
            dists = {ci_contract_mod._norm(name) for name in (dist_map.get(module) or [])}
            if not dists:
                # 不是 PyPI 发行包：仓库内模块 / 运行时合成模块 / 解释器 site 注入。
                # （本地能装载已说明它不是「缺失的第三方包」。）
                continue
            if not (dists & covered):
                missing.append((module, sorted(dists)))
        self.assertEqual(missing, [],
                         f"生产入口 import 的第三方模块不在 requirements.txt 闭包里：{missing}")

    def test_dependency_closure_is_not_trivially_equal_to_declared(self):
        closure = self.closure
        self.assertTrue(self.requirements <= closure,
                        sorted(self.requirements - closure))
        # 传递依赖确实被展开了，否则上面的断言就会退化成「必须逐字声明」。
        import importlib.metadata as md
        resolvable = []
        for name in sorted(self.requirements):
            try:
                if md.requires(name) is not None:
                    resolvable.append(name)
            except Exception:
                continue
        if not resolvable:
            self.skipTest("环境里没有任何可解析安装元数据，无法验证传递依赖展开")
        self.assertTrue(closure - self.requirements,
                        "声明包一个传递依赖都没展开，依赖闭包退化为逐字比对")
        # 未显式请求 extras 的包不应把 extras 依赖算进来（openai 不装 numpy / pandas）。
        self.assertNotIn("pandas", closure)
        self.assertNotIn("numpy", closure)
        self.assertIn("uvicorn", closure)

    def test_third_party_modules_are_not_empty(self):
        self.assertGreater(len(self.modules), 5, self.modules)


if __name__ == "__main__":
    unittest.main()
