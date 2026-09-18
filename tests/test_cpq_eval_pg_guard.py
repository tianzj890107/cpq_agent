# -*- coding: utf-8 -*-
"""隔离 PostgreSQL 的安全连接合同 + 临时库兜底清理（父 / 子共用一份白名单）。

覆盖用户第四节 8 条 + 第八节 4 条：回环放行、CI service alias 只在 CI 标志下放行、
生产 / PDT 特征永远拒绝、未显式开启一律拒绝、不回落 CPQ_PG_*、兜底清理只删本轮
`cpq_eval_it_<10 hex>` 且绝不模糊匹配。纯离线，不连任何数据库。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import pg_guard, pg_integration, pg_scenarios  # noqa: E402


def _cfg(**kw):
    base = {"host": "127.0.0.1", "port": "5432", "user": "postgres", "database": "",
            "maintenance_db": "postgres", "child_db": ""}
    base.update(kw)
    return base


CLEAN_ENV = {"CPQ_EVAL_INTEGRATION": "1"}


class PgGuardHostTest(unittest.TestCase):
    def test_loopback_hosts_allowed(self):
        with mock.patch.dict(os.environ, CLEAN_ENV, clear=True):
            for host in ("127.0.0.1", "localhost", "::1"):
                info = pg_guard.guard(_cfg(host=host))
                self.assertEqual(info["mode"], "loopback", host)

    def test_ci_service_alias_allowed_only_with_ci_flag(self):
        cfg = _cfg(host="cpq-eval-pg")
        with mock.patch.dict(os.environ, CLEAN_ENV, clear=True):
            with self.assertRaises(pg_guard.PgGuardError):
                pg_guard.guard(cfg)                        # 非 CI：拒绝
        env = dict(CLEAN_ENV, CPQ_EVAL_PG_CI="1")
        with mock.patch.dict(os.environ, env, clear=True):
            info = pg_guard.guard(cfg)                     # CI + 白名单：放行
            self.assertEqual(info["mode"], "ci_service")

    def test_ci_alias_not_in_whitelist_rejected(self):
        cfg = _cfg(host="some-other-pg")
        with mock.patch.dict(os.environ, dict(CLEAN_ENV, CPQ_EVAL_PG_CI="1"), clear=True):
            with self.assertRaises(pg_guard.PgGuardError):
                pg_guard.guard(cfg)

    def test_ci_alias_ip_rejected(self):
        cfg = _cfg(host="10.0.0.5")
        with mock.patch.dict(os.environ, dict(CLEAN_ENV, CPQ_EVAL_PG_CI="1",
                                              CPQ_EVAL_PG_SERVICE_HOSTS="10.0.0.5"), clear=True):
            with self.assertRaises(pg_guard.PgGuardError):
                pg_guard.guard(cfg)

    def test_production_ip_always_rejected(self):
        for host in ("172.16.10.34", "172.16.5.181"):
            with mock.patch.dict(os.environ, dict(CLEAN_ENV, CPQ_EVAL_PG_CI="1",
                                                  CPQ_EVAL_PG_SERVICE_HOSTS=host), clear=True):
                with self.assertRaises(pg_guard.PgGuardError):
                    pg_guard.guard(_cfg(host=host))

    def test_production_markers_in_host_or_database_rejected(self):
        with mock.patch.dict(os.environ, CLEAN_ENV, clear=True):
            with self.assertRaises(pg_guard.PgGuardError):
                pg_guard.guard(_cfg(host="pdt-db.internal"))
            with self.assertRaises(pg_guard.PgGuardError):
                pg_guard.guard(_cfg(maintenance_db="production"))
            with self.assertRaises(pg_guard.PgGuardError):
                pg_guard.guard(_cfg(child_db="cpq_eval_it_notprod0"))

    def test_requires_explicit_integration_switch(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(pg_guard.PgGuardError):
                pg_guard.guard(_cfg())

    def test_no_fallback_to_production_env(self):
        env = {"CPQ_PG_HOST": "127.0.0.1", "CPQ_PG_DATABASE": "cpq",
               "CPQ_PG_USER": "cpq", "CPQ_PG_PASSWORD": "secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = pg_integration.config()
            self.assertFalse(cfg["configured"], "不得回落读取 CPQ_PG_*")
            self.assertEqual(cfg["host"], "")
            with self.assertRaises(pg_guard.PgGuardError):
                pg_integration.guard_config(cfg)

    def test_parent_and_child_share_same_whitelist(self):
        self.assertIs(pg_integration.pg_guard, pg_scenarios.pg_guard)
        with mock.patch.dict(os.environ, dict(CLEAN_ENV, CPQ_EVAL_PG_CI="1"), clear=True):
            cfg = _cfg(host="cpq-eval-pg")
            self.assertEqual(pg_guard.guard(cfg)["mode"], "ci_service")
            self.assertEqual(pg_scenarios.guard(cfg)["mode"], "ci_service")


class TempDbCleanupTest(unittest.TestCase):
    """兜底清理：只删本轮严格格式的临时库，绝不模糊匹配。"""

    def setUp(self):
        self.cfg = _cfg(CPQ_EVAL_INTEGRATION="1")
        pg_integration.reset_stats()

    def test_pattern_is_strict(self):
        self.assertTrue(re.match(pg_guard.TEMP_DB_RE.pattern, "cpq_eval_it_0123456789"))
        for bad in ("cpq_eval_it_012345678", "cpq_eval_it_0123456789a",
                    "cpq_eval_it_ZZZZZZZZZZ", "cpq_eval_other_0123456789", "postgres"):
            self.assertIsNone(pg_guard.TEMP_DB_RE.match(bad), bad)

    def test_cleanup_refuses_non_matching_names_without_connecting(self):
        calls = []
        for bad in ("postgres", "cpq_eval_it_short", "cpq_eval_it_ZZZZZZZZZZ", "cpq"):
            with mock.patch.dict(os.environ, CLEAN_ENV, clear=True):
                result = pg_integration.cleanup_orphan(self.cfg, bad,
                                                       connect=lambda cfg: calls.append(cfg))
            self.assertFalse(result["attempted"], bad)
            self.assertFalse(result["dropped"], bad)
            self.assertRegex(result["reason"], "不匹配")
        self.assertEqual(calls, [], "对非本轮数据库不得发起任何 DROP")

    def test_cleanup_drops_matching_temp_db_only(self):
        executed = []

        class _FakeAdmin:
            def execute(self, sql):
                executed.append(sql)

            def close(self):
                pass

        with mock.patch.dict(os.environ, CLEAN_ENV, clear=True):
            result = pg_integration.cleanup_orphan(
                self.cfg, "cpq_eval_it_0123456789", connect=lambda cfg: _FakeAdmin())
        self.assertTrue(result["attempted"])
        self.assertTrue(result["dropped"])
        self.assertEqual(len(executed), 1)
        self.assertIn('DROP DATABASE IF EXISTS "cpq_eval_it_0123456789"', executed[0])
        self.assertIn("WITH (FORCE)", executed[0])
        stats = pg_integration.stats()
        self.assertEqual(stats["parent_cleanup_dropped"], 1)
        self.assertEqual(stats["parent_cleanup_failed"], 0)

    def test_cleanup_refuses_unsafe_host(self):
        with mock.patch.dict(os.environ, CLEAN_ENV, clear=True):
            result = pg_integration.cleanup_orphan(
                _cfg(host="172.16.10.34"), "cpq_eval_it_0123456789",
                connect=lambda cfg: self.fail("不安全 host 不得连接"))
        self.assertFalse(result["attempted"])
        self.assertIn("不安全", result["reason"])

    def test_timeout_fallback_cleans_exact_child_db(self):
        captured = {}

        def fake_cleanup(cfg, child_db, connect=None):
            captured["child_db"] = child_db
            return {"attempted": True, "dropped": True, "reason": "", "database": child_db}

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 300)

        with mock.patch.dict(os.environ, CLEAN_ENV, clear=True), \
                mock.patch.object(pg_integration, "cleanup_orphan", fake_cleanup), \
                mock.patch.object(pg_integration.subprocess, "run", fake_run):
            payload, err = pg_integration._run_child("claim.role_denied_no_side_effect", {})
        self.assertIsNone(payload)
        self.assertIn("超时", err)
        self.assertRegex(captured["child_db"], pg_guard.TEMP_DB_RE.pattern)

    def test_cleanup_failure_is_reported_not_swallowed(self):
        class _Boom:
            def execute(self, sql):
                raise RuntimeError("permission denied")

            def close(self):
                pass

        with mock.patch.dict(os.environ, CLEAN_ENV, clear=True):
            result = pg_integration.cleanup_orphan(
                self.cfg, "cpq_eval_it_0123456789", connect=lambda cfg: _Boom())
        self.assertTrue(result["attempted"])
        self.assertFalse(result["dropped"])
        self.assertIn("permission denied", result["reason"])
        self.assertEqual(pg_integration.stats()["parent_cleanup_failed"], 1)


if __name__ == "__main__":
    unittest.main()
