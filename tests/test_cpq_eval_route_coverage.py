# -*- coding: utf-8 -*-
"""真实路由覆盖守护：新增项目级路由却没有对应案例 / 策略时必须失败并列出漏测路由。

守护对象是**真实 FastAPI 路由表**（从 `tech_app/backend/main.py` 的 AST 现算），
不是手抄的接口清单：

  · `dataset/evals/cpq/routes/project_routes.json`    —— 读路由覆盖来源 + 快照；
  · `dataset/evals/cpq/routes/write_route_policy.json` —— 每条写路由的分类与案例。

两份文件都由 `python3 -m scripts.cpq_eval.runner --snapshot-routes` 现算重写；
本模块只负责「现算 vs 快照」的差异检查 —— 差异一出，测试失败并打印差在哪条路由。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import checks, coverage, dataset, prodkit  # noqa: E402

ROUTES_DIR = ROOT / "dataset" / "evals" / "cpq" / "routes"


class RouteSnapshotGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = dataset.load_cases()
        cls.case_ids = {case.case_id for case in cls.cases}
        cls.routes = prodkit.routes()
        cls.reads = coverage.read_routes()
        cls.writes = coverage.write_routes()
        cls.whitelist = coverage.contribute_whitelist()
        cls.snapshot = json.loads((ROUTES_DIR / "project_routes.json").read_text(encoding="utf-8"))
        cls.policy = json.loads((ROUTES_DIR / "write_route_policy.json").read_text(encoding="utf-8"))

    # ---------------- 快照 vs 现算 ----------------
    def test_project_route_snapshot_matches_live_table(self):
        gaps = coverage.snapshot_gaps(self.snapshot)
        self.assertEqual(gaps, {"added": [], "removed": []},
                         f"真实项目级路由与快照不一致（新增 {gaps['added'][:5]} / "
                         f"删除 {gaps['removed'][:5]}）；重跑 "
                         f"`python3 -m scripts.cpq_eval.runner --snapshot-routes`")

    def test_snapshot_records_every_read_route(self):
        recorded = set(self.snapshot.get("project_routes") or [])
        missing = [row["path"] for row in self.reads if f"GET {row['path']}" not in recorded]
        self.assertEqual(missing, [], f"读路由没进快照：{missing[:5]}")

    def test_every_read_route_has_a_coverage_source(self):
        gaps = coverage.read_gaps(self.cases, self.snapshot)
        self.assertEqual(gaps["missing_from_report"], [],
                         f"读路由没有覆盖记录：{gaps['missing_from_report'][:5]}")
        self.assertEqual(gaps["unexplained"], [],
                         f"读路由没有覆盖来源也没有理由：{gaps['unexplained'][:5]}")

    # ---------------- 写路由策略 ----------------
    def test_every_write_route_has_a_policy_entry(self):
        gaps = coverage.policy_gaps(self.cases, self.policy)
        self.assertEqual(gaps["missing_routes"], [],
                         "新增项目级写路由没有对应策略 / 案例，漏测路由："
                         f"{gaps['missing_routes']}")
        self.assertEqual(gaps["stale_routes"], [],
                         f"策略表里有已不存在的写路由：{gaps['stale_routes'][:5]}")

    def test_write_route_classification_matches_real_whitelist(self):
        gaps = coverage.policy_gaps(self.cases, self.policy)
        self.assertEqual(gaps["wrong_class"], [],
                         f"专属动作白名单被归错类：{gaps['wrong_class']}")
        self.assertEqual(self.policy.get("contribute_whitelist_size"), len(self.whitelist),
                         "策略表里的白名单条数与真实 CONTRIBUTE_ROUTES 不一致")

    def test_contribute_whitelist_routes_all_have_cases(self):
        gaps = coverage.policy_gaps(self.cases, self.policy)
        self.assertEqual(gaps["uncovered_whitelist"], [],
                         "专属业务写路由没有 ACL / 角色案例，漏测路由："
                         f"{gaps['uncovered_whitelist']}")

    def test_policy_cases_exist_in_dataset(self):
        gaps = coverage.policy_gaps(self.cases, self.policy)
        self.assertEqual(gaps["missing_policy_cases"], [],
                         f"策略表引用了不存在的案例：{gaps['missing_policy_cases']}")

    def test_case_declared_routes_exist_in_real_table(self):
        gaps = coverage.policy_gaps(self.cases, self.policy)
        self.assertEqual(gaps["declared_unknown"], [],
                         f"案例声明了不存在的路由：{gaps['declared_unknown']}")

    # ---------------- 守护本身有效：新增写路由必须被报出来 ----------------
    def test_new_write_route_without_case_is_reported(self):
        fake = {"method": "POST", "path": "/api/projects/{project_id}/brand-new-write",
                "handler": "brand_new_write", "line": 10 ** 6,
                "runtime_params": ("project_id",)}
        rows = list(self.routes.project()) + [fake]
        gaps = coverage.policy_gaps(self.cases, self.policy, route_rows=rows)
        self.assertIn("POST /api/projects/{project_id}/brand-new-write", gaps["missing_routes"],
                      "新增写路由没有被覆盖守护报出来 —— 守护失效")

    def test_new_whitelisted_write_route_without_case_is_reported(self):
        fake_route = {"method": "POST", "path": "/api/projects/{project_id}/cost-review/unknown",
                      "handler": "unknown_cost_write", "line": 10 ** 6,
                      "runtime_params": ("project_id",)}
        rows = list(self.routes.project()) + [fake_route]
        policy = json.loads(json.dumps(self.policy))
        policy["writes"].append({"route": "POST /api/projects/{project_id}/cost-review/unknown",
                                 "policy": "contribute_whitelist", "covered_by": [],
                                 "policy_cases": []})
        real_whitelist = coverage.contribute_whitelist

        def with_extra():
            return set(real_whitelist()) | {("POST", "/api/projects/{project_id}/cost-review/unknown")}
        coverage.contribute_whitelist = with_extra
        try:
            gaps = coverage.policy_gaps(self.cases, policy, route_rows=rows)
        finally:
            coverage.contribute_whitelist = real_whitelist
        self.assertIn("POST /api/projects/{project_id}/cost-review/unknown",
                      gaps["uncovered_whitelist"],
                      "新增专属写路由没有案例时没有被报出来 —— 守护失效")

    def test_route_snapshot_diff_detects_new_route(self):
        fake = {"method": "GET", "path": "/api/projects/{project_id}/brand-new-read",
                "handler": "brand_new_read", "line": 10 ** 6,
                "runtime_params": ("project_id",)}
        rows = list(self.routes.project()) + [fake]
        gaps = coverage.snapshot_gaps(self.snapshot, route_rows=rows)
        self.assertIn("GET /api/projects/{project_id}/brand-new-read", gaps["added"])

    # ---------------- 读路由整体覆盖 + UI 协议静态守护 ----------------
    def test_route_sweep_cases_cover_all_read_routes(self):
        sweeps = coverage.sweep_case_ids(self.cases)
        self.assertGreaterEqual(len(sweeps), 2, "缺少动态遍历读路由的 route_sweep 案例")
        report = {row["route"]: row for row in self.snapshot.get("reads") or []}
        unexplained = [row for row in coverage.read_routes()
                       if not (report.get(f"GET {row['path']}") or {}).get("covered_by")
                       and not (report.get(f"GET {row['path']}") or {}).get("reason")]
        self.assertEqual(unexplained, [], "有读路由既没有指名案例也没有整体覆盖理由")

    def test_ui_protocol_static_guards_do_not_shrink(self):
        self.assertGreaterEqual(len(checks.UI_PROTOCOL), 11,
                                "UI 协议静态守护项减少了（原 11 条不得下降）")
        used = {}
        for case in self.cases:
            for name in (case.get("expected") or {}).get("ui_protocol") or {}:
                used.setdefault(name, []).append(case.case_id)
        missing = [name for name in checks.UI_PROTOCOL if not used.get(name)]
        self.assertEqual(missing, [], f"UI 协议项没有案例覆盖：{missing}")

    def test_reported_route_coverage_counts_are_exposed(self):
        info = coverage.route_coverage(self.cases, self.routes.all)
        self.assertGreaterEqual(info["reads_total"], 40)
        self.assertGreaterEqual(info["writes_total"], 100)
        self.assertGreaterEqual(info["writes_covered"], 19)
        self.assertIsInstance(coverage.render_route_coverage(info), str)


def tearDownModule():
    """把 prodkit 钉死的隔离环境放回原值，不把 CPQ_SSO 等开关泄漏给同进程其他测试。"""
    try:
        from scripts.cpq_eval import prodkit

        prodkit.restore_process_env()
    except Exception:  # pragma: no cover - 没装载过就无需还原
        pass


if __name__ == "__main__":
    unittest.main()
