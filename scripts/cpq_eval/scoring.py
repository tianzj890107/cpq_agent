# -*- coding: utf-8 -*-
"""评分与汇总：P0/P1/P2 加权、按 domain / layer 汇总、退出码、敏感信息脱敏。

评分口径：P0 权重 3、P1 权重 2、P2 权重 1；``score = 通过案例权重 / 参与案例权重``。
skipped（例如未开启 integration 层）不计入分母；invalid（schema / 契约不合法）视为失败
并从 ``invalid`` 计数单独列出，避免与真实断言失败混淆。
"""
from __future__ import annotations

import re

from . import GATE_EXECUTORS, executor_of

WEIGHTS = {"P0": 3, "P1": 2, "P2": 1}

STATUSES = ("passed", "failed", "skipped", "invalid")

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)(password[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
    re.compile(r"(?i)(token[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)(postgres(?:ql)?://)[^\s\"',}]+"),
)


def redact(text) -> str:
    """抹掉输出里的 token / api key / 密码（报告与 CLI 输出都过这一层）。"""
    out = str(text)
    for index, pattern in enumerate(_SECRET_PATTERNS):
        if index == 0:
            out = pattern.sub("sk-***", out)
        else:
            out = pattern.sub(lambda m: m.group(1) + "***", out)
    return out


def scan_secrets(text) -> list:
    """返回命中的敏感模式名称（用于测试断言输出里不含密钥）。"""
    hits = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(str(text)):
            hits.append(pattern.pattern[:32])
    return hits


def case_weight(case) -> int:
    return WEIGHTS.get(str(case.get("priority")), 1)


def summarize(results) -> dict:
    """``results`` 是 ``[{case, status, reason, duration_ms}]``。"""
    totals = {status: 0 for status in STATUSES}
    weight_total = 0
    weight_passed = 0
    by_domain = {}
    by_priority = {}
    by_layer = {}
    by_executor = {}
    failures = []
    gate = {
        "specification_total": 0, "specification_passed": 0,
        "specification_failed": 0, "specification_skipped": 0,
        "simulation_total": 0, "simulation_passed": 0, "simulation_failed": 0,
        "simulation_skipped": 0,
        "production_total": 0, "production_passed": 0, "production_failed": 0,
        "production_skipped": 0, "production_invalid": 0,
        "integration_skipped": 0, "p0_production_failed": [],
        "integration_total": 0, "integration_scenarios": [],
        "integration_cleanup_ok": 0, "integration_cleanup_failed": 0,
    }
    pg_scenarios = set()
    for item in results:
        case = item.get("case")
        status = item.get("status") or "invalid"
        priority = str(case.get("priority") if hasattr(case, "get") else "") or "P2"
        domain = str(case.get("domain") if hasattr(case, "get") else "") or "unknown"
        layer = str(case.get("layer") if hasattr(case, "get") else "") or "unknown"
        executor = executor_of(case) if hasattr(case, "get") else "specification_only"
        totals[status] = totals.get(status, 0) + 1
        bucket = by_executor.setdefault(executor, {s: 0 for s in STATUSES})
        bucket[status] += 1
        if executor in GATE_EXECUTORS:
            gate["production_total"] += 1
            key = {"passed": "production_passed", "failed": "production_failed",
                   "skipped": "production_skipped",
                   "invalid": "production_invalid"}.get(status)
            if key:
                gate[key] += 1
            if status in ("failed", "invalid") and priority == "P0":
                gate["p0_production_failed"].append(
                    case.case_id if hasattr(case, "case_id") else str(case))
        elif executor == "postgres_integration":
            gate["integration_total"] += 1
            if status == "skipped":
                gate["integration_skipped"] += 1
            for step in ((item.get("observation") or {}).get("steps") or {}).values():
                scenario = str((step or {}).get("scenario") or "")
                if scenario:
                    pg_scenarios.add(scenario)
                cleanup = str((step or {}).get("cleanup") or "")
                if cleanup == "dropped":
                    gate["integration_cleanup_ok"] += 1
                elif cleanup:
                    gate["integration_cleanup_failed"] += 1
        elif executor == "specification_only":
            gate["specification_total"] += 1
            if status == "passed":
                gate["specification_passed"] += 1
            elif status in ("failed", "invalid"):
                gate["specification_failed"] += 1
            elif status == "skipped":
                gate["specification_skipped"] += 1
        else:
            gate["simulation_total"] += 1
            if status == "passed":
                gate["simulation_passed"] += 1
            elif status in ("failed", "invalid"):
                gate["simulation_failed"] += 1
            elif status == "skipped":
                gate["simulation_skipped"] += 1
        for bucket, key in ((by_domain, domain), (by_priority, priority), (by_layer, layer)):
            entry = bucket.setdefault(key, {s: 0 for s in STATUSES})
            entry[status] += 1
        if status in ("passed", "failed", "invalid"):
            weight_total += case_weight(case)
            if status == "passed":
                weight_passed += case_weight(case)
        if status in ("failed", "invalid"):
            failures.append({
                "id": case.case_id if hasattr(case, "case_id") else str(case),
                "domain": domain, "priority": priority, "layer": layer,
                "status": status, "reason": redact(item.get("reason") or "")[:400],
            })
    score = round(weight_passed / weight_total, 4) if weight_total else 1.0
    gate["integration_scenarios"] = sorted(pg_scenarios)
    gate["integration_unique_scenarios"] = len(pg_scenarios)
    return {
        "totals": totals,
        "score": score,
        "weight_total": weight_total,
        "weight_passed": weight_passed,
        "by_domain": by_domain,
        "by_priority": by_priority,
        "by_layer": by_layer,
        "by_executor": by_executor,
        "gate": gate,
        "failures": failures,
        "exit_code": 1 if (totals["failed"] or totals["invalid"]) else 0,
    }


_RED_MODULE = re.compile(r"^test_[a-z0-9_]*_red$")


def classify_baseline_log(text: str) -> dict:
    """把一次全量 unittest 的结果分类（dependency_error / expected_red /
    existing_regression / dataset_failure / unexpected_error）。

    规则（可复核）：
      · 失败原因里出现 ``No module named 'X'`` → dependency_error（本机缺 psycopg / pydantic / fastapi）；
      · 模块名以 ``_red`` 结尾 → expected_red（这些是等待 DeepSeek 实现的 TDD 红测）；
      · 模块名以 ``test_cpq_eval_`` 开头 → dataset_failure（本任务新增数据集自身的失败）；
      · 其余 → existing_regression（非红测的既有用例失败，需要单独判断）。
    """
    import collections

    log = str(text or "")
    blocks = re.split(r"\n(?=(?:FAIL|ERROR): )", log)
    buckets = collections.defaultdict(list)
    ran = None
    for match in re.finditer(r"^Ran (\d+) tests? in", log, re.M):
        ran = int(match.group(1))
    for block in blocks:
        head = re.match(r"(FAIL|ERROR): (\S+) \((.+)\)", block)
        if not head:
            continue
        kind, name, location = head.groups()
        module = location.split(".")[0]
        # 模块导入失败时 unittest 把用例挂在 `unittest.loader._FailedTest` 上：
        # 真正的模块名在 "Failed to import test module: X" 里。不看这里会把红测/缺依赖
        # 误判成 existing_regression。
        if module == "unittest":
            imported = re.search(r"Failed to import test module: (\S+)", block)
            if imported:
                module = imported.group(1)
        modules = sorted(set(re.findall(r"No module named '([^']+)'", block)))
        if modules:
            bucket = "dependency_error"
            reason = "缺少依赖：" + ", ".join(modules)
        elif module.startswith("test_cpq_eval"):
            bucket = "dataset_failure"
            reason = "新增数据集测试失败"
        elif _RED_MODULE.match(module):
            bucket = "expected_red"
            reason = "未实现批次的 TDD 红测"
        else:
            bucket = "existing_regression"
            reason = "非红测用例失败"
        buckets[bucket].append({"module": module, "test": name, "kind": kind, "reason": reason})
    summary = {key: len(value) for key, value in buckets.items()}
    for key in ("dependency_error", "expected_red", "existing_regression", "dataset_failure"):
        summary.setdefault(key, 0)
    return {"ran": ran, "summary": summary, "items": {key: value for key, value in buckets.items()}}
