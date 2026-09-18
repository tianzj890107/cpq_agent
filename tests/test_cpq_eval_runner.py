# -*- coding: utf-8 -*-
"""CPQ 业务回归 runner 测试：CLI、分层、退出码、报告落点、离线与密钥安全。

全部通过 ``python3 -m scripts.cpq_eval.runner`` 或进程内调用执行，不访问真实数据库、
真实模型、PDT 与线上服务。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import DATASET_ROOT, REPORTS_DIR  # noqa: E402
from scripts.cpq_eval import dataset, runner, scoring  # noqa: E402

CLI = [sys.executable, "-m", "scripts.cpq_eval.runner"]


class CpqEvalRunnerTest(unittest.TestCase):
    def cli(self, *args, env=None, timeout=300):
        environment = dict(os.environ)
        # 默认把 integration / 隔离 PG 相关变量都清掉：测试显式模拟「没有 PG 环境」，
        # 不依赖跑测试时 shell 里恰好有没有 CPQ_EVAL_PG_*。需要 PG 的用例用 env= 显式加回。
        for key in list(environment):
            if key == "CPQ_EVAL_INTEGRATION" or key.startswith("CPQ_EVAL_PG_"):
                environment.pop(key)
        if env:
            environment.update(env)
        return subprocess.run(CLI + list(args), cwd=str(ROOT), capture_output=True,
                              text=True, timeout=timeout, env=environment)

    # ---------------- CLI 基本命令 ----------------
    def test_list_prints_cases_and_exits_zero(self):
        result = self.cli("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("quote.step1", result.stdout)
        self.assertIn("# 共", result.stdout)
        self.assertGreaterEqual(result.stdout.count("\n"), 200)

    def test_validate_passes(self):
        result = self.cli("--validate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("结果：通过", result.stdout)
        self.assertNotIn("错误：", result.stdout)

    def test_layer_deterministic_green(self):
        result = self.cli("--layer", "deterministic", "--no-report", "--quiet")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("失败 0", result.stdout)
        self.assertIn("非法 0", result.stdout)

    def test_layer_recorded_provider_green(self):
        result = self.cli("--layer", "recorded_provider", "--no-report", "--quiet")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("失败 0", result.stdout)

    def test_domain_and_priority_and_case_filters(self):
        for args in (("--domain", "quote"), ("--domain", "tech"), ("--priority", "P0")):
            result = self.cli(*args, "--no-report", "--quiet")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("失败 0", result.stdout)
        result = self.cli("--case", "quote.step1.match.ok", "--no-report", "--quiet")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("（1 条）", result.stdout)

    def test_unknown_case_exits_non_zero(self):
        result = self.cli("--case", "does.not.exist", "--no-report", "--quiet")
        self.assertEqual(result.returncode, 2)
        self.assertIn("没有匹配的案例", result.stderr)

    def test_failure_returns_non_zero_exit_code(self):
        summary = scoring.summarize([
            {"case": {"priority": "P0", "domain": "quote", "layer": "deterministic"},
             "status": "failed", "reason": "boom"},
            {"case": {"priority": "P1", "domain": "tech", "layer": "deterministic"},
             "status": "passed", "reason": ""},
        ])
        self.assertEqual(summary["exit_code"], 1)
        self.assertEqual(summary["totals"]["failed"], 1)
        self.assertEqual(len(summary["failures"]), 1)

    # ---------------- 分层与离线 ----------------
    def test_integration_layer_is_skipped_by_default(self):
        """integration 层（postgres_integration）默认全部 skip，绝不冒充 passed。"""
        expected = len([c for c in dataset.load_cases() if c.get("layer") == "integration"])
        self.assertGreaterEqual(expected, 9)
        result = self.cli("--layer", "integration", "--no-report", "--quiet")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"跳过 {expected}", result.stdout)
        self.assertIn("通过 0", result.stdout)
        self.assertIn("integration 跳过：%d" % expected, result.stdout)

    def test_integration_without_pg_environment_is_skipped_not_passed(self):
        """显式开启 integration 但没有隔离 PostgreSQL 环境时必须 skip，不得算通过。"""
        expected = len([c for c in dataset.load_cases() if c.get("layer") == "integration"])
        result = self.cli("--layer", "integration", "--no-report", "--quiet",
                          env={"CPQ_EVAL_INTEGRATION": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("通过 0", result.stdout)
        self.assertIn(f"跳过 {expected}", result.stdout)
        self.assertNotIn("postgres_integration 真实通过", result.stdout)

    def test_integration_rejects_production_hosts(self):
        for url in ("http://172.16.10.34:8010/", "http://pdt.internal/", "https://prod.example.com/"):
            with self.assertRaises(ValueError, msg=url):
                runner.guard_target_url(url)
        with self.assertRaises(ValueError):
            runner.guard_target_url("")
        with self.assertRaises(ValueError):
            runner.guard_target_url("ftp://127.0.0.1/")
        self.assertEqual(runner.guard_target_url("http://127.0.0.1:18010"), "http://127.0.0.1:18010")

    def test_production_target_url_marks_case_invalid(self):
        result = self.cli("--layer", "integration", "--no-report", "--quiet",
                          "--target-url", "http://172.16.10.34:8010/",
                          env={"CPQ_EVAL_INTEGRATION": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("拒绝生产", result.stdout + result.stderr)

    def test_integration_skips_are_reported_not_counted_as_production_pass(self):
        """integration 未启用/环境缺失时，报告必须把 skipped 与 production-backed 分开。"""
        result = self.cli("--layer", "integration", "--no-report", "--quiet")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("数据库集成案例数", result.stdout)
        self.assertIn("production-backed 通过率", result.stdout)

    def test_offline_layers_never_open_network(self):
        options = runner.Options(quiet=True, no_report=True)
        cases = [case for case in dataset.load_cases()
                 if case.get("layer") in ("deterministic", "recorded_provider")]
        fixtures = dataset.load_fixtures()
        original = socket.socket.connect

        def _blocked(*_args, **_kwargs):
            raise AssertionError("deterministic / recorded_provider 层不得发起网络连接")

        socket.socket.connect = _blocked
        try:
            results = runner.run_cases(cases, fixtures, options)
        finally:
            socket.socket.connect = original
        bad = [item for item in results if item["status"] in ("failed", "invalid")]
        self.assertEqual(bad, [], f"离线层出现失败：{bad[:3]}")

    # ---------------- 报告与安全 ----------------
    def test_report_written_to_given_temp_directory(self):
        with tempfile.TemporaryDirectory(prefix="cpq_eval_test_") as tmp:
            before = sorted(p.name for p in REPORTS_DIR.iterdir())
            result = self.cli("--domain", "quote", "--report", tmp, "--quiet")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            json_path = Path(tmp) / "cpq_eval_report.json"
            md_path = Path(tmp) / "cpq_eval_report.md"
            self.assertTrue(json_path.is_file())
            self.assertTrue(md_path.is_file())
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["totals"]["failed"], 0)
            self.assertIn("matrix", report)
            self.assertIn("domain", report["matrix"])
            self.assertEqual(sorted(p.name for p in REPORTS_DIR.iterdir()), before,
                             "runner 不得把报告写进仓库 reports/ 目录")

    def test_default_report_does_not_pollute_repository(self):
        before = sorted(p.name for p in REPORTS_DIR.iterdir())
        result = self.cli("--case", "quote.step1.match.ok", "--quiet")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("报告：", result.stdout)
        for line in result.stdout.splitlines():
            if line.startswith("报告："):
                path = Path(line.split("报告：", 1)[1].strip())
                self.assertFalse(str(path).startswith(str(DATASET_ROOT)),
                                 "报告默认必须写临时目录，不得落进数据集目录")
        self.assertEqual(sorted(p.name for p in REPORTS_DIR.iterdir()), before)

    def test_output_and_report_contain_no_secrets(self):
        with tempfile.TemporaryDirectory(prefix="cpq_eval_sec_") as tmp:
            result = self.cli("--layer", "recorded_provider", "--report", tmp, "--quiet")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(scoring.scan_secrets(result.stdout), [])
            blob = (Path(tmp) / "cpq_eval_report.json").read_text(encoding="utf-8")
            self.assertEqual(scoring.scan_secrets(blob), [])
            self.assertNotIn("postgres://", result.stdout)

    def test_redaction_masks_credentials(self):
        text = "api_key=abcdefghijklmnop password=zzzzzzzzzzzz postgres://u:p@h/db"
        self.assertTrue(scoring.scan_secrets(text), "原始文本应能识别出敏感模式")
        redacted = scoring.redact(text)
        for secret in ("abcdefghijklmnop", "zzzzzzzzzzzz", "postgres://u:p@h/db"):
            self.assertNotIn(secret, redacted)
        self.assertIn("***", redacted)

    def test_baseline_log_classification(self):
        log = ("Ran 1970 tests in 127.5s\n\n"
               "FAILED (failures=1, errors=2, skipped=7)\n"
               "FAIL: test_acl_scope (test_tech_project_acl_scope_red.TechAclTest.test_acl_scope)\n"
               "ERROR: test_store (tests.test_store.StoreTest.test_store)\n"
               "ModuleNotFoundError: No module named 'psycopg'\n"
               "ERROR: test_api (tests.test_api.ApiTest.test_api)\n"
               "ModuleNotFoundError: No module named 'fastapi'\n")
        report = scoring.classify_baseline_log(log)
        self.assertEqual(report["ran"], 1970)
        self.assertEqual(report["summary"]["expected_red"], 1)
        self.assertEqual(report["summary"]["dependency_error"], 2)
        self.assertEqual(report["summary"]["dataset_failure"], 0)


if __name__ == "__main__":
    unittest.main()
