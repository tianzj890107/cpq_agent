# -*- coding: utf-8 -*-
"""PostgreSQL mutation sentinel：在**真实隔离 PostgreSQL** 上注入故障并证明案例能杀死它。

"只注册 mutation" 不算完成（用户明确要求）。本模块把 `pg_scenarios.MUTATIONS` 里的每个
故障**真的注入一次**，跑对应 case，比较「正常实现结果」与「注入后结果」，输出
mutation 名称 / 对应测试 / 正常结果 / 注入后结果 / 是否 killed。

用法：

    python3 -m scripts.cpq_eval.pg_sentinel --list
    python3 -m scripts.cpq_eval.pg_sentinel                 # 人读表格
    python3 -m scripts.cpq_eval.pg_sentinel --json /tmp/x.json
    python3 -m scripts.cpq_eval.pg_sentinel --require-all-killed   # CI：有 survived 就非零

缺隔离 PostgreSQL 时：`--require-all-killed` 下**失败**（不能拿 skip 冒充通过）；
普通调用下显式打印 SKIPPED 并返回 0。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import dataset, pg_integration, pg_scenarios, production
from . import runner as _runner

# mutation 名称 -> 用来杀死它的 postgres_integration 案例（一一对应，可复核）。
MUTATION_CASES = (
    {"mutation": "drop_handoff_index", "case": "pg.handoff.concurrent_same_key_single_row",
     "expects": "uq_wf_handoff_key 消失后并发占位出现多行"},
    {"mutation": "drop_task_open_index", "case": "pg.task.db_rejects_duplicate_open",
     "expects": "部分唯一索引被删后原始 INSERT 能插进第二条 open"},
    {"mutation": "handoff_no_conflict", "case": "pg.handoff.concurrent_same_key_single_row",
     "expects": "去掉 ON CONFLICT DO NOTHING 后并发占位不再收敛"},
    {"mutation": "blind_task_lookup", "case": "pg.task.supersede_on_signature_change",
     "expects": "屏蔽复用查询后签名变化被错误复用，丢掉替代语义"},
    {"mutation": "no_rollback_tx", "case": "pg.tx.midway_failure_rolls_back_all",
     "expects": "出错仍 commit，前半段写入不回滚"},
    {"mutation": "autocommit_tx", "case": "pg.tx.midway_failure_rolls_back_all",
     "expects": "autocommit 让多写一步 = 多提交一次"},
    {"mutation": "claim_without_open_guard", "case": "pg.claim.concurrent_two_claimers_one_winner",
     "expects": "原子 UPDATE 去掉 status='open' 后出现两个赢家"},
    {"mutation": "claim_ignore_eligibility", "case": "pg.claim.role_denied_no_side_effect",
     "expects": "权限门被绕过后无权限角色也写副作用"},
)


def _comparison_errors(case, observation) -> list:
    return production.compare_production(case, observation, _runner.compare,
                                         _runner.resolve_path)


def _evaluate(entry: dict, mutate: str) -> dict:
    found = dataset.load_cases(case_id=entry["case"])
    case = found[0] if found else None
    row = {"mutation": entry["mutation"], "case": entry["case"],
           "expects": entry["expects"], "baseline_ok": False, "mutated_ok": False,
           "killed": False, "reason": "", "db": "", "cleanup": ""}
    if case is None:
        row["reason"] = f"数据集里没有案例 {entry['case']}"
        return row
    baseline = pg_integration.run_case(case)
    if baseline.get("status") != "executed":
        row["reason"] = f"基线不可执行：{baseline.get('reason')}"
        return row
    row["db"] = str((baseline.get("observation") or {}).get("steps", {})
                    .get((baseline.get("observation") or {}).get("order", [""])[0], {})
                    .get("db") or "")
    base_errors = _comparison_errors(case, baseline["observation"])
    row["baseline_ok"] = not base_errors
    if base_errors:
        row["reason"] = f"基线本应通过却是红的：{base_errors[:2]}"
        return row
    broken = pg_integration.run_case(case, mutate=mutate)
    if broken.get("status") != "executed":
        row["reason"] = f"注入后不可执行：{broken.get('reason')}"
        return row
    broken_errors = _comparison_errors(case, broken["observation"])
    row["mutated_ok"] = not broken_errors
    row["killed"] = bool(broken_errors)
    row["reason"] = ("" if row["killed"]
                     else f"注入 {mutate} 后 {entry['case']} 仍然通过 —— 守卫无效")
    obs = broken.get("observation") or {}
    step0 = (obs.get("order") or [""])[0]
    row["cleanup"] = str((obs.get("steps", {}).get(step0, {}) or {}).get("cleanup") or "")
    return row


def run_all(only_mutation: str = "") -> dict:
    ok, why = pg_integration.available()
    if not ok:
        return {"available": False, "reason": why, "rows": []}
    rows = []
    for entry in MUTATION_CASES:
        if only_mutation and entry["mutation"] != only_mutation:
            continue
        rows.append(_evaluate(entry, entry["mutation"]))
    stats = pg_integration.stats()
    return {"available": True, "reason": "", "rows": rows,
            "killed": sum(1 for r in rows if r["killed"]),
            "survived": sum(1 for r in rows if not r["killed"]),
            "cleanup": stats}


def render(report: dict) -> str:
    if not report.get("available"):
        return f"[SKIP] PostgreSQL mutation sentinel 未运行：{report.get('reason')}"
    lines = ["PostgreSQL mutation sentinel（隔离库真跑）",
             f"  mutation 执行 {len(report['rows'])} 项：killed {report['killed']} / "
             f"survived {report['survived']}"]
    for row in report["rows"]:
        flag = "KILLED  " if row["killed"] else "SURVIVED"
        lines.append(f"  [{flag}] {row['mutation']:26s} case={row['case']:48s} "
                     f"baseline_ok={row['baseline_ok']} mutated_ok={row['mutated_ok']}")
        if row["reason"]:
            lines.append(f"             ↳ {row['reason']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.cpq_eval.pg_sentinel")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--mutation", default="")
    parser.add_argument("--json", default="")
    parser.add_argument("--require-all-killed", action="store_true",
                        help="CI：缺环境或任一 mutation survived 都返回非零")
    opts = parser.parse_args(argv)
    if opts.list:
        for entry in MUTATION_CASES:
            print(json.dumps(entry, ensure_ascii=False))
        return 0
    report = run_all(opts.mutation)
    print(render(report))
    if opts.json:
        with open(opts.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    if not report.get("available"):
        return 1 if opts.require_all_killed else 0
    return 1 if (report["survived"] and opts.require_all_killed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
