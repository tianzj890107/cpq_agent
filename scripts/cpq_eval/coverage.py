# -*- coding: utf-8 -*-
"""覆盖矩阵与「必须覆盖」清单（完成标准的机器可读版本）。

矩阵轴：domain / priority / layer / stage / sub_step / quote_step / role / failure_type / kind。
kind 从 tags 与 failure_type 推导，用于区分**正常 / 异常 / 并发 / 恢复 / 刷新 / 幂等 / stale**，
这也是「第一批 200 条必须是业务状态数据、不是复制标题」的可核对依据。
"""
from __future__ import annotations

from . import (DOMAINS, EXECUTORS, FAILURE_TYPES, GATE_EXECUTORS, LAYERS, PRIORITIES,
               PRODUCTION_EXECUTORS, QUOTE_STEPS, ROLES, STAGES, covers_routes, executor_of)

KIND_TAGS = {
    "normal": ("normal",),
    "negative": ("negative", "deny", "permission", "reject"),
    "concurrency": ("concurrency", "conflict", "race"),
    "recovery": ("recovery", "rollback", "retry", "timeout", "failure"),
    "refresh": ("refresh",),
    "idempotent": ("idempotent", "duplicate"),
    "stale": ("stale",),
    "legacy": ("legacy",),
}

KINDS = tuple(KIND_TAGS)

_PREREQUISITE_FAILURES = frozenset({
    "missing_prerequisite", "permission_denied", "not_found", "stale_upstream", "auth_failure",
})

# 第一批的最低条数（用户任务的第五节 / 第十三节）
DOMAIN_MINIMUMS = {
    "quote": 45, "tech": 65, "cross_agent": 30, "auth_acl": 15, "session_history": 15,
    "concurrency": 20, "llm_contract": 10, "failure_recovery": 10, "ui_protocol": 10,
}
TOTAL_MINIMUM = 200
P0_MINIMUM = 60
CROSS_AGENT_MINIMUM = 30
CONCURRENCY_MINIMUM = 20


def kinds_of(case) -> tuple:
    tags = set(str(tag) for tag in (case.get("tags") or []))
    found = {kind for kind, names in KIND_TAGS.items() if tags & set(names)}
    failure = str(case.get("failure_type") or "")
    if failure and failure != "none":
        if failure in ("concurrency_conflict",):
            found.add("concurrency")
        elif failure in ("transaction_rollback", "timeout", "provider_error",
                         "refresh_failure", "invalid_output"):
            found.add("recovery")
        elif failure == "duplicate_request":
            found.add("idempotent")
        elif failure == "stale_upstream":
            found.add("stale")
        else:
            found.add("negative")
    return tuple(sorted(found)) or ("normal",)


def is_precondition_failure(case) -> bool:
    failure = str(case.get("failure_type") or "")
    tags = set(str(tag) for tag in (case.get("tags") or []))
    return failure in _PREREQUISITE_FAILURES or bool(
        tags & {"prerequisite", "permission", "deny", "negative"})


def matrix(cases) -> dict:
    axes = {
        "domain": {}, "priority": {}, "layer": {}, "executor": {}, "stage": {}, "sub_step": {},
        "quote_step": {}, "role": {}, "failure_type": {}, "kind": {},
    }
    for case in cases:
        axes["domain"][case.get("domain")] = axes["domain"].get(case.get("domain"), 0) + 1
        axes["priority"][case.get("priority")] = axes["priority"].get(case.get("priority"), 0) + 1
        axes["layer"][case.get("layer")] = axes["layer"].get(case.get("layer"), 0) + 1
        executor = executor_of(case)
        axes["executor"][executor] = axes["executor"].get(executor, 0) + 1
        axes["failure_type"][case.get("failure_type") or "none"] = \
            axes["failure_type"].get(case.get("failure_type") or "none", 0) + 1
        if case.get("stage"):
            axes["stage"][case.get("stage")] = axes["stage"].get(case.get("stage"), 0) + 1
        if case.get("sub_step"):
            axes["sub_step"][case.get("sub_step")] = axes["sub_step"].get(case.get("sub_step"), 0) + 1
        if case.get("quote_step"):
            key = f"step{case.get('quote_step')}"
            axes["quote_step"][key] = axes["quote_step"].get(key, 0) + 1
        for role in case.get("roles") or []:
            axes["role"][role] = axes["role"].get(role, 0) + 1
        for kind in kinds_of(case):
            axes["kind"][kind] = axes["kind"].get(kind, 0) + 1
    return axes


def substep_table(cases) -> list:
    """13 子步骤 × （正常 / 前置失败）覆盖表。"""
    rows = []
    for stage in STAGES:
        sub = stage["sub"]
        mine = [case for case in cases if case.get("sub_step") == sub]
        normal = [case for case in mine if "normal" in kinds_of(case)]
        pre = [case for case in mine if is_precondition_failure(case)]
        rows.append({
            "sub": sub, "key": stage["key"], "stage_id": stage["stage_id"],
            "phase": stage["phase"], "phase_title": stage["phase_title"],
            "total": len(mine), "normal": len(normal), "precondition": len(pre),
            "ok": bool(normal) and bool(pre),
        })
    return rows


def quote_step_table(cases) -> list:
    """报价六步 × （正常 / 拒绝 / 恢复）覆盖表。"""
    rows = []
    for no, title, role in QUOTE_STEPS:
        mine = [case for case in cases
                if case.get("domain") == "quote" and case.get("quote_step") == no]
        kinds = set()
        for case in mine:
            kinds.update(kinds_of(case))
        rows.append({
            "step": int(no), "title": title, "role": role, "total": len(mine),
            "normal": "normal" in kinds, "deny": bool(kinds & {"negative"}),
            "recovery": bool(kinds & {"recovery", "refresh", "idempotent"}),
            "ok": bool(mine) and "normal" in kinds and bool(kinds & {"negative"})
                  and bool(kinds & {"recovery", "refresh", "idempotent"}),
        })
    return rows


def role_table(cases) -> dict:
    table = {role: 0 for role in ROLES}
    for case in cases:
        for role in case.get("roles") or []:
            if role in table:
                table[role] += 1
    return table


def required_coverage(cases) -> list:
    """返回 ``[{requirement, ok, detail}]``；任何一条不满足即视为覆盖不足。"""
    checks = []
    total = len(cases)

    def _add(name, ok, detail):
        checks.append({"requirement": name, "ok": bool(ok), "detail": str(detail)})

    _add(f"案例总数 ≥ {TOTAL_MINIMUM}", total >= TOTAL_MINIMUM, f"实际 {total}")
    for domain in DOMAINS:
        minimum = DOMAIN_MINIMUMS.get(domain, 1)
        count = sum(1 for case in cases if case.get("domain") == domain)
        _add(f"domain={domain} ≥ {minimum}", count >= minimum, f"实际 {count}")
    p0 = sum(1 for case in cases if case.get("priority") == "P0")
    _add(f"P0 ≥ {P0_MINIMUM}", p0 >= P0_MINIMUM, f"实际 {p0}")
    cross = sum(1 for case in cases if case.get("domain") == "cross_agent")
    _add(f"跨 Agent ≥ {CROSS_AGENT_MINIMUM}", cross >= CROSS_AGENT_MINIMUM, f"实际 {cross}")
    conc = sum(1 for case in cases if case.get("domain") == "concurrency")
    _add(f"并发 / 幂等 ≥ {CONCURRENCY_MINIMUM}", conc >= CONCURRENCY_MINIMUM, f"实际 {conc}")
    for layer in LAYERS:
        count = sum(1 for case in cases if case.get("layer") == layer)
        _add(f"layer={layer} ≥ 1", count >= 1, f"实际 {count}")
    missing_sub = [row["sub"] for row in substep_table(cases) if not row["ok"]]
    _add("技术工艺 13 子步骤均有正常 + 前置失败案例", not missing_sub,
         "缺：" + ", ".join(missing_sub) if missing_sub else "13/13 行覆盖")
    missing_quote = [str(row["step"]) for row in quote_step_table(cases) if not row["ok"]]
    _add("报价六步均有正常 + 拒绝 + 恢复案例", not missing_quote,
         "缺：" + ", ".join(missing_quote) if missing_quote else "6/6 步覆盖")
    kinds = {kind: 0 for kind in KINDS}
    for case in cases:
        for kind in kinds_of(case):
            kinds[kind] += 1
    for kind in ("concurrency", "recovery", "refresh", "idempotent", "stale", "legacy"):
        _add(f"kind={kind} 有案例", kinds[kind] > 0, f"实际 {kinds[kind]}")
    roles = role_table(cases)
    _add("角色覆盖含销售 / 工艺 / 财务 / 审核 / 管理员",
         all(roles.get(role, 0) > 0 for role in
             ("sales_mgr", "process_mgr", "process_engineer", "finance_mgr", "reviewer", "admin")),
         str(roles))
    types = {item: 0 for item in FAILURE_TYPES}
    for case in cases:
        key = case.get("failure_type") or "none"
        types[key] = types.get(key, 0) + 1
    _add("失败类型覆盖并发冲突 / 事务回滚 / 权限 / 幂等 / stale / 刷新失败 / provider",
         all(types.get(name, 0) > 0 for name in
             ("concurrency_conflict", "transaction_rollback", "permission_denied",
              "duplicate_request", "stale_upstream", "refresh_failure", "provider_error")),
         str(types))
    return checks


def render_matrix(axes: dict) -> str:
    lines = ["覆盖矩阵"]
    order = ("domain", "priority", "layer", "stage", "sub_step", "quote_step",
             "role", "failure_type", "kind")
    for axis in order:
        items = axes.get(axis) or {}
        if not items:
            continue
        body = ", ".join(f"{key}={value}" for key, value in sorted(items.items(),
                                                                  key=lambda kv: str(kv[0])))
        lines.append(f"  {axis:14s} {body}")
    return "\n".join(lines)


def render_substeps(cases) -> str:
    lines = ["五阶段 / 13 子步骤覆盖（正常 / 前置失败）"]
    for row in substep_table(cases):
        flag = "OK " if row["ok"] else "缺 "
        lines.append(f"  {flag}{row['sub']} {row['key']:12s} stage={row['stage_id']:18s} "
                     f"共 {row['total']:2d} 条（正常 {row['normal']}、前置失败 {row['precondition']}）")
    return "\n".join(lines)


def render_quote_steps(cases) -> str:
    lines = ["报价六步覆盖（正常 / 拒绝 / 恢复）"]
    for row in quote_step_table(cases):
        flag = "OK " if row["ok"] else "缺 "
        lines.append(f"  {flag}{row['step']}. {row['title']:14s} 角色={row['role']:11s} "
                     f"共 {row['total']:2d} 条（正常 {row['normal']}、拒绝 {row['deny']}、"
                     f"恢复 {row['recovery']}）")
    return "\n".join(lines)


def render_required(cases) -> str:
    lines = ["必须覆盖清单"]
    for item in required_coverage(cases):
        lines.append(f"  {'OK ' if item['ok'] else '缺 '}{item['requirement']} —— {item['detail']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 执行层（发布门禁口径）
# --------------------------------------------------------------------------- #
def executor_table(cases) -> dict:
    """按执行层统计案例数；production-backed 与 simulation 分开列。"""
    table = {name: 0 for name in EXECUTORS}
    for case in cases:
        name = executor_of(case)
        table[name] = table.get(name, 0) + 1
    return table


def render_executors(by_executor: dict, gate: dict) -> str:
    """runner 输出的门禁摘要：simulation 通过率与 production-backed 通过率分开。"""
    lines = ["-- 执行层 / 发布门禁 --"]

    def _line(key: str, label: str) -> str:
        row = (by_executor or {}).get(key) or {}
        total = sum(row.values())
        return (f"  {label}：{total} 条（通过 {row.get('passed', 0)} / 失败 "
                f"{row.get('failed', 0)} / 跳过 {row.get('skipped', 0)} / 非法 "
                f"{row.get('invalid', 0)}）")

    for key, label in (("specification_only", "specification_only（只固化规范，未执行）"),
                       ("simulation", "simulation（Sim 假库，不计入发布门禁）"),
                       ("production_unit", "production_unit（真实生产函数）"),
                       ("production_http", "production_http（真实路由）"),
                       ("recorded_provider", "recorded_provider（回放 + 真实分发边界）"),
                       ("postgres_integration", "postgres_integration（隔离 PostgreSQL，默认跳过）")):
        lines.append(_line(key, label))
    gate = gate or {}
    lines.extend(_render_gate_counts(by_executor or {}, gate))
    lines.append(f"  simulation 通过率：{gate.get('simulation_passed', 0)}/"
                 f"{gate.get('simulation_total', 0)}")
    lines.append(f"  production-backed 通过率：{gate.get('production_passed', 0)}/"
                 f"{gate.get('production_total', 0)}"
                 f"（失败 {gate.get('production_failed', 0)} / 非法 "
                 f"{gate.get('production_invalid', 0)} / 跳过 {gate.get('production_skipped', 0)}）")
    lines.append(f"  integration 跳过：{gate.get('integration_skipped', 0)}")
    bad = gate.get("p0_production_failed") or []
    lines.append("  P0 production-backed 全部通过" if not bad
                 else f"  P0 production-backed 失败 {len(bad)} 条：" + ", ".join(bad[:10]))
    return "\n".join(lines)


def gate_counts(by_executor: dict, gate: dict) -> dict:
    """发布门禁的六个必读数字（口径：只看 executor，不看 layer）。

    ``executed`` = passed + failed + invalid；**skipped 不算执行过**，所以
    「production 覆盖率高」不可能靠 skip 撑起来。
    """
    gate = gate or {}
    by_executor = by_executor or {}

    def _row(key):
        return by_executor.get(key) or {}

    def _sum(keys, statuses):
        return sum(int(_row(key).get(status, 0)) for key in keys for status in statuses)

    ran = ("passed", "failed", "invalid")
    production = ("production_unit", "production_http", "recorded_provider")
    pure = ("specification_only", "simulation")
    return {
        "cases_total": sum(sum(_row(key).values()) for key in EXECUTORS),
        "executed_total": _sum(EXECUTORS, ran),
        "production_executed": _sum(production, ran),
        "integration_executed": _sum(("postgres_integration",), ran),
        "spec_or_sim_executed": _sum(pure, ran),
        "specification_only_total": sum(_row("specification_only").values()),
        "simulation_total": sum(_row("simulation").values()),
        "production_total": sum(sum(_row(key).values()) for key in production),
        "integration_total": sum(_row("postgres_integration").values()),
        "integration_skipped": int(gate.get("integration_skipped", 0)),
        "integration_passed": int(_row("postgres_integration").get("passed", 0)),
        "integration_failed": int(_row("postgres_integration").get("failed", 0)),
        "integration_invalid": int(_row("postgres_integration").get("invalid", 0)),
        "integration_unique_scenarios": int(gate.get("integration_unique_scenarios", 0)),
        "integration_scenarios": list(gate.get("integration_scenarios") or []),
        "integration_cleanup_ok": int(gate.get("integration_cleanup_ok", 0)),
        "integration_cleanup_failed": int(gate.get("integration_cleanup_failed", 0)),
        "production_failed": int(gate.get("production_failed", 0))
                             + int(gate.get("production_invalid", 0)),
    }


def render_gate_counts(by_executor: dict, gate: dict) -> str:
    """门禁六数：总案例 / 实际执行 / 生产入口 / 数据库集成 / 仅规范或模拟。"""
    info = gate_counts(by_executor, gate)
    return "\n".join([
        f"  · 总案例数：{info['cases_total']}",
        f"  · 实际执行案例数：{info['executed_total']}（skipped 不算执行过）",
        f"  · 生产入口执行案例数：{info['production_executed']}"
        f"（production_unit / production_http / recorded_provider）",
        f"  · 数据库集成案例数：{info['integration_executed']}"
        f"（postgres_integration 实跑；声明共 {info['integration_total']} 条，"
        f"跳过 {info['integration_skipped']} 条）",
        f"  · PostgreSQL 场景：案例 {info['integration_total']} 条，"
        f"唯一场景 {info['integration_unique_scenarios']} 个"
        f"（通过 {info['integration_passed']} / 失败 {info['integration_failed']} / "
        f"非法 {info['integration_invalid']} / 跳过 {info['integration_skipped']}），"
        f"cleanup 成功 {info['integration_cleanup_ok']} / 失败 "
        f"{info['integration_cleanup_failed']}",
        f"  · 仅规范 / 模拟案例数：{info['spec_or_sim_executed']}"
        f"（specification_only {info['specification_only_total']} + "
        f" simulation {info['simulation_total']}）",
    ])


def _render_gate_counts(by_executor: dict, gate: dict) -> list:
    return render_gate_counts(by_executor, gate).splitlines()

def production_backed_cases(cases) -> list:
    return [case for case in cases if executor_of(case) in PRODUCTION_EXECUTORS]


def production_gate_cases(cases) -> list:
    return [case for case in cases if executor_of(case) in GATE_EXECUTORS]


def render_postgres_layer(by_executor: dict, gate: dict) -> str:
    """PostgreSQL 层的明细：案例数 / 唯一场景数 / 执行结果 / cleanup / mutation。"""
    row = (by_executor or {}).get("postgres_integration") or {}
    gate = gate or {}
    scenarios = gate.get("integration_scenarios") or []
    lines = ["-- PostgreSQL 集成层（声明 / 唯一场景分开看，重复案例不虚增覆盖） --"]
    lines.append(f"  postgres case count：{sum(row.values())}"
                 f"（通过 {row.get('passed', 0)} / 失败 {row.get('failed', 0)} / "
                 f"非法 {row.get('invalid', 0)} / 跳过 {row.get('skipped', 0)}）")
    lines.append(f"  postgres unique scenario count：{gate.get('integration_unique_scenarios', 0)}")
    for scenario in scenarios:
        lines.append(f"      · {scenario}")
    lines.append(f"  cleanup succeeded：{gate.get('integration_cleanup_ok', 0)} / "
                 f"cleanup failed：{gate.get('integration_cleanup_failed', 0)}")
    sentinel = gate.get("pg_sentinel") or {}
    if sentinel:
        lines.append(f"  mutation executed：{sentinel.get('executed', 0)} / "
                     f"killed：{sentinel.get('killed', 0)} / "
                     f"survived：{sentinel.get('survived', 0)}")
    return "\n".join(lines)


def route_coverage(cases, route_rows) -> dict:
    """真实路由 × 案例声明的 ``covers_routes``；未覆盖的要看得见（不静默通过）。"""
    declared = set()
    for case in cases:
        for item in covers_routes(case):
            declared.add(str(item).strip())
    reads = sorted({f"GET {row['path']}" for row in route_rows if row["method"] == "GET"})
    writes = sorted({f"{row['method']} {row['path']}" for row in route_rows
                     if row["method"] in ("POST", "PUT", "PATCH", "DELETE")})
    return {
        "declared": sorted(declared),
        "reads_total": len(reads), "reads_covered": len([r for r in reads if r in declared]),
        "uncovered_reads": [r for r in reads if r not in declared],
        "writes_total": len(writes), "writes_covered": len([r for r in writes if r in declared]),
        "uncovered_writes": [r for r in writes if r not in declared],
    }


def render_route_coverage(info: dict) -> str:
    lines = [f"-- 真实路由覆盖（读 {info.get('reads_covered', 0)}/"
             f"{info.get('reads_total', 0)}，写 {info.get('writes_covered', 0)}/"
             f"{info.get('writes_total', 0)}） --"]
    for label, key in (("未覆盖读接口", "uncovered_reads"), ("未覆盖写接口", "uncovered_writes")):
        rows = info.get(key) or []
        if rows:
            lines.append(f"  {label} {len(rows)} 条：" + ", ".join(rows[:8])
                         + (" …" if len(rows) > 8 else ""))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 项目级写路由策略表（现算，可脚本再生）
#
# 为什么要有这张表：专属业务动作白名单（`project_access.CONTRIBUTE_ROUTES`）里的路由由
# 接口自己的 `_require` 判角色，项目 ACL 只判「可见 + 未归档」；其余项目级写路由仍由
# 通用写 ACL（mode=write）裁决。两类路由的回归特征完全不同 ——
#   · 白名单被通用写 ACL 提前截断 → 「你的角色只能查看该项目，不能修改」；
#   · 通用写路由漏掉 ACL      → 越权角色直接改到数据。
# 这张表把每条真实写路由归到其中一类，并要求每类都有能打死对应回归的案例。
# --------------------------------------------------------------------------- #
POLICY_REASONS = {
    "contribute_whitelist": ("自带业务角色门禁的专属动作：项目 ACL 只判可见 + 未归档，"
                             "角色由接口 _require 判定"),
    "acl_write_gated": ("普通项目级写：由 project_access mode=write 判定，"
                        "可见但写权不足返回 403"),
}
POLICY_CASES = {
    "contribute_whitelist": ["acl.real.contribute_whitelist_excludes_generic_writes",
                             "acl.real.write_gate_matrix_whitelist_not_acl_blocked",
                             "acl.real.write_gate_matrix_route_specific_403_keeps_role_wording"],
    "acl_write_gated": ["fail.real.generic_write_still_uses_write_mode",
                        "acl.real.finance.unrelated_write_is_not_found_not_forbidden",
                        "acl.real.write_gate_matrix_generic_routes_still_acl_gated"],
}
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def write_routes(route_rows=None) -> list:
    """真实项目级写路由（AST 现算，不手抄）。"""
    if route_rows is None:
        from . import prodkit

        route_rows = prodkit.routes().project()
    return [row for row in route_rows if row["method"] in WRITE_METHODS]


def contribute_whitelist() -> set:
    """真实专属业务动作白名单（`project_access.CONTRIBUTE_ROUTES`，唯一事实源）。"""
    from . import prodkit

    project_access = prodkit.module("backend.services.project_access")
    return {(str(method).upper(), str(path)) for method, path in project_access.CONTRIBUTE_ROUTES}


def build_write_policy(cases, route_rows=None) -> dict:
    """按真实路由表 + 真实白名单 + 案例声明的 ``covers_routes`` 生成策略表。"""
    whitelist = contribute_whitelist()
    covered: dict = {}
    for case in cases:
        for item in covers_routes(case):
            covered.setdefault(str(item).strip(), []).append(case.case_id)
    rows = []
    for row in write_routes(route_rows):
        signature = f"{row['method']} {row['path']}"
        policy = "contribute_whitelist" if (row["method"], row["path"]) in whitelist \
            else "acl_write_gated"
        rows.append({
            "route": signature,
            "policy": policy,
            "reason": POLICY_REASONS[policy],
            "covered_by": sorted(covered.get(signature, [])),
            "policy_cases": list(POLICY_CASES[policy]),
        })
    return {
        "generated_from": "tech_app/backend/main.py + backend/services/project_access.CONTRIBUTE_ROUTES",
        "note": "由 scripts/cpq_eval/coverage.build_write_policy() 现算（runner --snapshot-routes 可重写）；"
                "每条项目级写路由必须有一条策略 —— 新增写路由若不在这里，覆盖测试会失败并列出它。",
        "contribute_whitelist_size": len(whitelist),
        "writes": rows,
        "delegates_to_spec": "docs/specs/tech-project-acl-visible-scope.md §18.5",
        "policy_cases": {name: list(ids) for name, ids in POLICY_CASES.items()},
    }


def project_route_rows(route_rows=None) -> list:
    if route_rows is None:
        from . import prodkit

        route_rows = prodkit.routes().project()
    return list(route_rows)


def read_routes(route_rows=None) -> list:
    return [row for row in project_route_rows(route_rows) if row["method"] == "GET"]


def declared_route_coverage(cases) -> dict:
    """案例声明的 ``covers_routes`` → ``{route: [case_id, ...]}``。"""
    table: dict = {}
    for case in cases:
        for item in covers_routes(case):
            table.setdefault(str(item).strip(), []).append(case.case_id)
    return table


def snapshot_gaps(snapshot, route_rows=None) -> dict:
    """路由快照 vs 现算路由表：**新增 / 删除的真实路由**都要看得见。"""
    live = sorted({f"{row['method']} {row['path']}"
                   for row in project_route_rows(route_rows)})
    recorded = set(snapshot.get("project_routes") or [])
    return {
        "added": sorted(set(live) - recorded),
        "removed": sorted(recorded - set(live)),
    }


def policy_gaps(cases, policy, route_rows=None) -> dict:
    """写路由策略表的漏项（新增写路由没有策略 / 白名单路由没有案例 / 策略案例不存在）。"""
    rows = write_routes(route_rows)
    live = {f"{row['method']} {row['path']}" for row in rows}
    recorded = {str(row.get("route") or "") for row in (policy.get("writes") or [])}
    whitelist = {f"{method} {path}" for method, path in contribute_whitelist()}
    covered = declared_route_coverage(cases)
    known_cases = {case.case_id for case in cases}
    classified = {str(row.get("route") or ""): str(row.get("policy") or "")
                  for row in (policy.get("writes") or [])}
    wrong_class = sorted(route for route in live & whitelist
                         if classified.get(route) != "contribute_whitelist")
    missing_policy_cases = sorted(
        {case_id for row in (policy.get("writes") or [])
         for case_id in (row.get("policy_cases") or [])} - known_cases)
    from . import prodkit

    live_all = set(prodkit.routes().signature())
    return {
        "missing_routes": sorted(live - recorded),
        "stale_routes": sorted(recorded - live),
        "uncovered_whitelist": sorted(route for route in live & whitelist
                                      if not covered.get(route)),
        "missing_policy_cases": missing_policy_cases,
        "wrong_class": wrong_class,
        "declared_unknown": sorted(route for route in covered
                                   if " " in route and route not in live_all),
    }


def sweep_case_ids(cases) -> list:
    """用 `route_sweep` 动态遍历全部项目级读路由的案例（读覆盖的兜底来源）。"""
    out = []
    for case in cases:
        steps = (case.get("input") or {}).get("steps") or []
        if any(isinstance(step, dict) and "route_sweep" in step for step in steps):
            out.append(case.case_id)
    return sorted(out)


def read_route_report(cases, route_rows=None) -> list:
    """每条项目级读路由的覆盖来源：指名案例，或 `route_sweep` 的整体覆盖理由。"""
    covered = declared_route_coverage(cases)
    sweeps = sweep_case_ids(cases)
    rows = []
    for row in read_routes(route_rows):
        signature = f"GET {row['path']}"
        entry = {
            "route": signature,
            "handler": row["handler"],
            "extra_params": [name for name in row["runtime_params"] if name != "project_id"],
            "covered_by": sorted(covered.get(signature, [])),
        }
        if not entry["covered_by"]:
            entry["reason"] = (
                "路由清单整体覆盖（" + " / ".join(sweeps) + " 动态遍历全部项目级读路由）"
                if sweeps else "尚未覆盖：需要一条指名案例或一条 route_sweep 案例")
        rows.append(entry)
    return rows


def read_gaps(cases, snapshot, route_rows=None) -> dict:
    """读路由覆盖的漏项：没有任何覆盖来源，或来源没有理由。"""
    report = {row["route"]: row for row in (snapshot.get("reads") or [])}
    live = {f"GET {row['path']}" for row in read_routes(route_rows)}
    unexplained, missing = [], []
    for signature in sorted(live):
        entry = report.get(signature)
        if entry is None:
            missing.append(signature)
        elif not (entry.get("covered_by") or entry.get("reason")):
            unexplained.append(signature)
    return {"missing_from_report": missing, "unexplained": unexplained}
