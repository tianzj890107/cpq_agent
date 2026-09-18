# -*- coding: utf-8 -*-
"""CPQ 业务回归数据集契约测试。

只读 ``dataset/evals/cpq/**`` 与 ``scripts/cpq_eval/**``，不访问网络、数据库或真实模型。
覆盖：schema、id 稳定性与唯一性、fixture / spec 引用、五阶段 13 子步骤与报价六步口径、
金额 decimal、相对时间、P0 反例、写操作幂等声明、权限 allow/deny、回传关联字段、
模型结构化工具调用、资产卫生与产物不落仓。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import (  # noqa: E402
    CASES_DIR, DATASET_ROOT, DOMAINS, FAILURE_TYPES, LAYERS, PRIORITIES, PRODUCTION_EXECUTORS,
    ROLES, SCHEMAS_DIR, SUB_STEPS, executor_of, find_legacy_wording,
)
from scripts.cpq_eval import checks, coverage, dataset, runner  # noqa: E402

DOMAIN_MINIMUM = {
    "quote": 45, "tech": 65, "cross_agent": 30, "auth_acl": 15, "session_history": 15,
    "concurrency": 20, "llm_contract": 10, "failure_recovery": 10, "ui_protocol": 10,
}
_MONEY_KEY = ("amount", "price", "subtotal", "cost", "total", "margin", "charge",
              "discount", "rate", "excise", "tax")
_COUNT_TAIL = ("total", "count", "parts", "rows", "runs", "versions", "n", "size", "length")


def _leaf(node, key=""):
    if isinstance(node, dict):
        for sub_key, value in node.items():
            yield from _leaf(value, sub_key)
    elif isinstance(node, list):
        for item in node:
            yield from _leaf(item, key)
    else:
        yield key, node


def _is_count_key(key: str) -> bool:
    for text in (key, key.rsplit(".", 1)[-1]):
        if text.rsplit("_", 1)[-1] in _COUNT_TAIL and not text.endswith("_amount"):
            return True
    return False


class CpqEvalDatasetContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suites = dataset.load_suites()
        cls.cases = dataset.load_cases()
        cls.fixtures = dataset.load_fixtures()
        cls.raws = [case.raw for case in cls.cases]

    # ---------------- schema 与结构 ----------------
    def test_schema_files_exist_and_parse(self):
        for name in ("case.schema.json", "suite.schema.json"):
            path = SCHEMAS_DIR / name
            self.assertTrue(path.is_file(), f"缺少 schema：{path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("$schema"), "https://json-schema.org/draft/2020-12/schema")

    def test_every_suite_conforms_to_suite_schema(self):
        schema = dataset.load_schema("suite.schema.json")
        for suite in self.suites:
            problems = dataset.validate(suite.raw, schema)
            self.assertEqual(problems, [],
                             f"{suite.path.name} 不符合 suite schema：{problems}")

    def test_case_ids_unique_and_stable(self):
        seen = {}
        for case in self.cases:
            self.assertNotIn(case.case_id, seen, f"id 重复：{case.case_id}")
            seen[case.case_id] = case.rel_path
        self.assertEqual(len(seen), len(self.cases))

    def test_domain_layer_priority_are_closed_sets(self):
        for case in self.cases:
            self.assertIn(case.get("domain"), DOMAINS)
            self.assertIn(case.get("layer"), LAYERS)
            self.assertIn(case.get("priority"), PRIORITIES)
            self.assertTrue(set(case.get("roles") or []) <= set(ROLES))
            failure = case.get("failure_type")
            if failure:
                self.assertIn(failure, FAILURE_TYPES)

    def test_first_batch_size_and_domain_minimums(self):
        self.assertGreaterEqual(len(self.cases), 200, "第一批至少 200 条案例")
        counts = coverage.matrix(self.cases)["domain"]
        for domain, minimum in DOMAIN_MINIMUM.items():
            self.assertGreaterEqual(counts.get(domain, 0), minimum, f"domain={domain} 数量不足")

    def test_source_specs_exist(self):
        """source_specs 要么指向仓库里真实存在的文件，要么是真实模块 / 真实路由引用。"""
        for case in self.cases:
            specs = case.get("source_specs") or []
            self.assertTrue(specs, f"{case.case_id} 必须列明对应 spec")
            for ref in specs:
                text = str(ref)
                if text.startswith("module:") or text.startswith("route:"):
                    continue                    # 由 checks.real_source_ref_errors 逐条校验真实性
                self.assertTrue((ROOT / text).exists(),
                                f"{case.case_id} 的 spec 不存在：{text}")

    def test_real_source_refs_point_at_live_code_or_routes(self):
        """production-backed 的 P0 必须同时指向真实模块 / 真实路由，且引用必须真实存在。"""
        offenders = []
        for case in self.cases:
            errors = checks.real_source_ref_errors(case)
            if errors:
                offenders.append((case.case_id, errors))
            if case.get("priority") == "P0" and executor_of(case) in PRODUCTION_EXECUTORS \
                    and not checks.real_source_refs(case):
                offenders.append((case.case_id, "缺少 module: / route: 真实入口引用"))
        self.assertEqual(offenders, [], f"source_specs 真实入口引用有问题：{offenders[:5]}")

    # ---------------- 业务口径 ----------------
    def test_no_legacy_wording_in_new_dataset(self):
        offenders = []
        for case in self.cases:
            raw = case.raw
            scanned = [raw.get("title") or "", raw.get("notes") or ""]
            scanned.extend(raw.get("tags") or [])
            scanned.extend(raw.get("invariants") or [])
            scanned.extend([str(a.get("label") or "") for a in raw.get("actions") or []
                            if isinstance(a, dict)])
            for _key, text in _leaf(raw.get("expected") or {}):
                if isinstance(text, str):
                    scanned.append(text)
            for text in scanned:
                if find_legacy_wording(text):
                    offenders.append((case.case_id, text[:40]))
        self.assertEqual(offenders, [], f"新数据集出现旧口径文案：{offenders[:5]}")

    def test_tech_cases_declare_five_phase_substeps(self):
        tech = [case for case in self.cases if case.get("domain") == "tech"]
        self.assertGreater(len(tech), 0)
        covered = set()
        for case in tech:
            self.assertIn(case.get("sub_step"), SUB_STEPS, f"{case.case_id} 子步骤非法")
            self.assertTrue(case.get("stage"), f"{case.case_id} 缺 stage")
            covered.add(case.sub_step)
        self.assertEqual(covered, set(SUB_STEPS), "13 个子步骤必须全部有案例")

    def test_quote_cases_declare_six_steps(self):
        quote = [case for case in self.cases if case.get("domain") == "quote"]
        steps = {case.get("quote_step") for case in quote}
        self.assertTrue(steps <= {1, 2, 3, 4, 5, 6}, f"报价步骤非法：{steps}")
        self.assertTrue({1, 2, 3, 4, 5, 6} <= steps, "报价六步必须全部有案例")

    # ---------------- 金额 / 时间 ----------------
    def test_money_assertions_are_decimal_strings(self):
        offenders = []
        for case in self.cases:
            for key, value in _leaf(case.get("expected") or {}):
                leaf = str(key).rsplit(".", 1)[-1]
                if leaf.rsplit("_", 1)[-1] not in _MONEY_KEY or _is_count_key(str(key)):
                    continue
                if isinstance(value, bool) or value is None:
                    continue
                if isinstance(value, (int, float)):
                    offenders.append((case.case_id, key, value))
                elif isinstance(value, str) and not value.replace(".", "", 1).replace("-", "", 1).isdigit():
                    offenders.append((case.case_id, key, value))
        self.assertEqual(offenders, [], f"金额必须用 decimal 字符串：{offenders[:5]}")

    def test_time_assertions_are_relative_not_frozen(self):
        offenders = []
        for case in self.cases:
            for key, value in _leaf(case.get("expected") or {}):
                if isinstance(value, str) and len(value) >= 10 and value[:4].isdigit() \
                        and value[4] == "-" and value[7] == "-" and not str(key).startswith("$"):
                    offenders.append((case.case_id, key, value))
        self.assertEqual(offenders, [],
                         f"expected 里不得写死绝对时间，只能校验格式 / 顺序 / 窗口：{offenders[:5]}")

    # ---------------- 反例 / 幂等 / 权限 / 回传 / 模型 ----------------
    def test_p0_cases_contain_negative_or_failure_path(self):
        offenders = []
        for case in self.cases:
            if case.get("priority") != "P0":
                continue
            expected = case.get("expected") or {}
            tags = set(case.get("tags") or [])
            http_status = (expected.get("http") or {}).get("status")
            has_4xx = isinstance(http_status, int) and http_status >= 400
            has_action_expect = any(isinstance(a, dict) and a.get("expect")
                                    for a in case.get("actions") or [])
            negative_tags = {"negative", "deny", "permission", "rollback", "conflict",
                             "concurrency", "duplicate", "idempotent", "timeout", "error",
                             "failure", "stale", "recovery", "refresh"}
            if not (expected.get("forbidden") or has_action_expect or has_4xx or tags & negative_tags):
                offenders.append(case.case_id)
        self.assertEqual(offenders, [], f"P0 案例必须含反例或失败路径：{offenders[:5]}")

    def test_write_operations_declare_idempotency(self):
        offenders = []
        for case in self.cases:
            ops = [str(a.get("op")) for a in case.get("actions") or [] if isinstance(a, dict)]
            if not set(ops) & checks.WRITE_OPS:
                continue
            tags = set(case.get("tags") or [])
            declared = (
                any(isinstance(a, dict) and (a.get("idempotency_key") or a.get("repeat_expect"))
                    for a in case.get("actions") or [])
                or bool((case.get("input") or {}).get("idempotency_key"))
                or bool(tags & {"idempotent", "duplicate", "recovery", "concurrency"})
            )
            if not declared:
                offenders.append(case.case_id)
        self.assertEqual(offenders, [], f"写操作未声明幂等键 / 重复执行期望：{offenders[:5]}")

    def test_auth_acl_cases_cover_allow_and_deny(self):
        acl = [case for case in self.cases if case.get("domain") == "auth_acl"]
        self.assertGreaterEqual(len(acl), 15)
        for case in acl:
            matrix = (case.get("input") or {}).get("access_matrix") or {}
            values = set(matrix.values()) if isinstance(matrix, dict) else set()
            self.assertTrue({"allow", "deny"} <= values,
                            f"{case.case_id} 必须同时覆盖允许角色与拒绝角色")

    def test_cross_agent_handoff_validates_linkage(self):
        tokens = ("source_project_id", "source_task_id", "business_case_id",
                  "source_quote_card", "source_session_id", "target_quote_session_id")
        cross = [case for case in self.cases if case.get("domain") == "cross_agent"]
        self.assertGreaterEqual(len(cross), 30, "跨 Agent / 任务流至少 30 条")
        handoffs = [case for case in cross
                    if any("handoff" in str(a.get("op")) for a in case.get("actions") or [])]
        self.assertGreaterEqual(len(handoffs), 20, "回传类案例数量不足")
        for case in handoffs:
            blob = json.dumps(case.get("expected") or {}, ensure_ascii=False) + \
                json.dumps(case.get("actions") or [], ensure_ascii=False)
            self.assertTrue(any(token in blob for token in tokens),
                            f"{case.case_id} 未校验关联字段，存在串项目风险")

    def test_llm_contract_asserts_structured_tool_calls(self):  # noqa: D401
        cases = [case for case in self.cases if case.get("domain") == "llm_contract"]
        self.assertGreaterEqual(len(cases), 10)
        for case in cases:
            if executor_of(case) == "recorded_provider":
                providers = [a.get("provider") for a in case.get("actions") or []
                             if isinstance(a, dict) and a.get("provider")]
                if not providers and (case.get("input") or {}).get("provider"):
                    providers = [(case.get("input") or {})["provider"]]
                self.assertTrue(providers, f"{case.case_id} 必须引用 recorded provider fixture")
            else:
                # 只有「确实无法真实回放的旧 fixture」才允许退化成 Sim，且必须显式声明原因。
                inp = case.get("input") or {}
                self.assertEqual(inp.get("provider_replay"), "simulated",
                                 f"{case.case_id} 不是 recorded_provider，也没有声明 "
                                 f"provider_replay=simulated")
                self.assertTrue(str(inp.get("provider_replay_reason") or "").strip(),
                                f"{case.case_id} 缺少 provider_replay_reason")
            blob = json.dumps(case.get("expected") or {}, ensure_ascii=False)
            self.assertIn("tool", blob, f"{case.case_id} 必须断言结构化工具调用")
            for _key, text in _leaf(case.get("expected", {}).get("messages") or []):
                if isinstance(text, str):
                    self.assertLessEqual(len(text), 120,
                                         f"{case.case_id} 出现了模型逐字文本断言")

    def test_recorded_provider_uses_test_key_only(self):
        for case in self.cases:
            for action in case.get("actions") or []:
                if not isinstance(action, dict) or not action.get("provider"):
                    continue
                self.assertNotIn("api_key", action)
                fixture = self.fixtures[action["provider"]]
                if isinstance(fixture, dict):
                    request = fixture.get("request") or {}
                    self.assertEqual(str(request.get("api_key") or "test-key"), "test-key",
                                     f"{action['provider']} 出现非 test-key 密钥")

    # ---------------- fixture / 资产 ----------------
    def test_fixture_references_resolve(self):
        for case in self.cases:
            for ref in dataset.fixture_refs(case):
                self.assertTrue(dataset.fixture_exists(ref),
                                f"{case.case_id} 引用的 fixture 不存在：{ref}")

    def test_every_fixture_is_referenced(self):
        used = set()
        for case in self.cases:
            used.update(dataset.fixture_refs(case))
        declared = set(self.fixtures)
        self.assertEqual(declared - used, set(), "存在从未被任何案例引用的 fixture")

    def test_dataset_assets_clean(self):
        self.assertEqual(checks.check_dataset_assets(), [],
                         "数据集内不得出现缓存 / 运行产物 / 报告文件")
        for path in DATASET_ROOT.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                self.assertNotIn(path.suffix, (".pyc", ".log", ".db", ".sqlite3", ".bak"))

    def test_reports_dir_holds_only_gitkeep(self):
        reports = DATASET_ROOT / "reports"
        entries = sorted(p.name for p in reports.iterdir() if p.is_file())
        self.assertEqual(entries, [".gitkeep"], f"reports/ 下只能有 .gitkeep：{entries}")

    def test_op_registry_has_no_gap(self):
        registry = runner.op_registry_report()
        self.assertEqual(registry["missing"], [], "有已注册但未实现的动作")
        self.assertEqual(registry["extra"], [], "实现了未注册的动作")

    def test_full_dataset_contract_check_is_green(self):
        errors = checks.check_dataset(self.cases)
        errors.extend(checks.check_fixtures(self.cases, self.fixtures, dataset.fixture_exists))
        self.assertEqual(errors, [], f"数据集契约不通过：{errors[:5]}")

    # ---------------- 旧口径审计：历史文本必须分类，且不得被改写 ----------------
    def test_historical_legacy_wording_is_classified_not_rewritten(self):
        audit = checks.legacy_wording_audit()
        self.assertGreaterEqual(len(audit), 7, "旧口径审计没有覆盖到历史 E2E 清单")
        self.assertEqual(checks.unclassified_legacy_hits(), [],
                         "出现未登记为历史文本的旧口径（真正过期必须处理）")
        for row in audit:
            self.assertEqual(row.get("classification"), "historical_provenance",
                             f"{row['file']}:{row['line']} 未分类")
        # 历史原文必须原样保留：文件里仍然能找到这些旧口径字符串。
        blob = (ROOT / "docs/specs/tech-agent-recovery-22-e2e-scenarios.json").read_text(
            encoding="utf-8")
        for name in ("2.2 组装与整合", "2.3 成本测算", "九阶段独立上下文与步进"):
            self.assertIn(name, blob, f"历史文本被改写了：{name}")
        # 历史文本已分类后，`--validate` 不再有旧口径警告（真正过期的旧口径仍是错误）。
        report = runner.validate_dataset()
        self.assertEqual(report["warnings"], [], f"仍有警告未清理：{report['warnings']}")
        self.assertEqual(report["legacy_audit_classified"], len(audit))
        self.assertEqual(report["errors"], [], f"校验不应有错误：{report['errors'][:3]}")

    def test_new_dataset_cases_have_no_legacy_wording(self):
        offenders = []
        for case in self.cases:
            scanned = [case.get("title") or "", str(case.get("tags") or "")]
            scanned.extend(case.get("invariants") or [])
            for text in scanned:
                for name in find_legacy_wording(text):
                    offenders.append((case.case_id, name))
        self.assertEqual(offenders, [], f"新增案例出现旧口径：{offenders[:5]}")


def tearDownModule():
    """把 prodkit 钉死的隔离环境放回原值，不把 CPQ_SSO 等开关泄漏给同进程其他测试。"""
    try:
        from scripts.cpq_eval import prodkit

        prodkit.restore_process_env()
    except Exception:  # pragma: no cover - 没装载过就无需还原
        pass


if __name__ == "__main__":
    unittest.main()
