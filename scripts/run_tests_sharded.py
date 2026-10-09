#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分片全量回归运行器（发布保障批次 2，Spec `docs/specs/release-assurance-batch2.md` §2.1）。

为什么要有它：全量只能串行 `python -m unittest discover`，实测 10~11 分钟（432 模块 / 约 6970 项），
客观上鼓励「只跑相关模块」。本运行器把同一批模块切成 N 个确定性、两两不相交、并集等于全集的分片，
**每个分片一个独立进程 + 独立 TMPDIR 根**（`tests/_tmp_guard` 的临时目录闸门是「一次运行一个根」，
多进程共享同一个根会互相删对方的临时目录，所以必须隔离）。

纪律：分片之间绝不共享 `TMPDIR`；「3 分钟」是优化目标，不是验收硬指标 —— 验收的是分片正确性与隔离性。
本脚本纯本地：不联网、不改仓库、只读模块清单。

用法：

    python scripts/run_tests_sharded.py --list                 # 每行一个模块名
    python scripts/run_tests_sharded.py --shards 4 --dry-run   # 只看分片命令，不执行
    python scripts/run_tests_sharded.py --shards 4             # 独立进程跑每个分片并聚合
    python scripts/run_tests_sharded.py --shards 4 --json      # 机器可读汇总
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 默认分片临时根：放在系统临时目录下，每个分片一个子目录。
DEFAULT_BASE_TMP = os.path.join(tempfile.gettempdir(), "cpq-sharded")

_RAN_RE = re.compile(r"Ran (\d+) tests?")
_OUTCOME_RE = re.compile(r"(FAILED|OK)\s*\(([^)]*)\)")


def discover_modules(root="tests") -> list:
    """返回 `root` 下 `test_*.py` 的**排序后**点分模块名（集合等于文件集合，不多不少）。"""
    base = Path(root)
    if not base.is_absolute():
        base = REPO_ROOT / base
    prefix = base.name
    names = ["%s.%s" % (prefix, path.stem) for path in sorted(base.glob("test_*.py"))
             if path.is_file()]
    return sorted(set(names))


def plan_shards(modules, shards) -> list:
    """确定性分片：同输入同输出、两两不相交、并集等于输入；`shards <= len` 时每片非空。"""
    shards = int(shards)
    if shards <= 0:
        raise ValueError("shards 必须为正整数")
    order = sorted({str(name) for name in modules})
    plan = [[] for _ in range(shards)]
    for index, name in enumerate(order):
        plan[index % shards].append(name)
    return plan


def shard_env(index, base_tmp) -> dict:
    """第 `index` 个分片的环境变量：`TMPDIR` 指向它**独有**的根（不同分片互不相同）。"""
    root = os.path.join(str(base_tmp), "shard-%d" % int(index))
    env = dict(os.environ)
    env["TMPDIR"] = root
    env["CPQ_TEST_SHARD"] = str(int(index))
    env["CPQ_TEST_SHARD_ROOT"] = root
    return env


def _command(modules, env) -> str:
    parts = ["TMPDIR=%s" % env["TMPDIR"], sys.executable, "-m", "unittest"]
    parts.extend(modules)
    return " ".join(parts)


def _parse_totals(text: str) -> dict:
    totals = {"ran": 0, "failures": 0, "errors": 0, "skipped": 0}
    ran = _RAN_RE.search(text or "")
    if ran:
        totals["ran"] = int(ran.group(1))
    outcome = _OUTCOME_RE.search(text or "")
    if outcome:
        for chunk in outcome.group(2).split(","):
            key, _, value = chunk.strip().partition("=")
            key = key.strip()
            if key in totals:
                try:
                    totals[key] = int(value)
                except ValueError:
                    totals[key] = 0
    return totals


def _run_one_shard(index, modules, base_tmp) -> dict:
    """跑第 `index` 个分片：独立 `TMPDIR`、独立子进程，返回结构化结果 + 原始输出。"""
    env = shard_env(index, base_tmp)
    os.makedirs(env["TMPDIR"], exist_ok=True)
    command = _command(modules, env)
    proc = subprocess.run([sys.executable, "-m", "unittest"] + list(modules),
                          cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    totals = _parse_totals(output)
    ok = proc.returncode == 0
    return {
        "index": index,
        "command": command,
        "returncode": proc.returncode,
        "modules": list(modules),
        "ok": ok,
        "output": output,
        **totals,
    }


def _resolve_jobs(jobs, plan) -> int:
    """`--jobs` → 实际开发线程数。

    默认（0/负数/None）取 **CPU 数的一半**：本套件里有些用例自己会再起子进程
    （ODA 转换、xvfb、node），跑满核会超订 → 让这些用例在负载下抖动
    （Spec `parallel-sharded-regression` §2.1：`jobs=8` 实测出 2 条负载相关红，
    `jobs=4` 全绿）。上限为分片数（不为 0）；显式 `--jobs N` 仍按 N 跑。
    """
    if jobs is None or int(jobs) <= 0:
        cpus = os.cpu_count() or 1
        jobs = max(1, cpus // 2)
    return max(1, min(int(jobs), max(1, len(plan))))


def run_shards(plan, base_tmp, *, json_out=False, jobs=1) -> dict:
    """跑每个分片并聚合；任一分片非零 → `ok` 为假。

    `jobs == 1` 串行（保留可回归对照）；`jobs > 1` 用**线程池**并发跑分片 ——
    分片本体是阻塞 `subprocess`，线程即可真正并行（Spec `parallel-sharded-regression` §2.1）。
    `shards` 结果恒按 index 升序，`totals`/`ok` 与串行口径一致。
    """
    workers = _resolve_jobs(jobs, plan)
    if workers <= 1:
        results = [_run_one_shard(index, modules, base_tmp)
                   for index, modules in enumerate(plan)]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_one_shard, index, modules, base_tmp)
                       for index, modules in enumerate(plan)]
            results = [future.result() for future in futures]

    aggregates = {"ran": 0, "failures": 0, "errors": 0, "skipped": 0}
    overall_ok = True
    shards = []
    for row in results:
        for key in aggregates:
            aggregates[key] += row[key]
        overall_ok = overall_ok and row["ok"]
        if not json_out:
            print("[分片 %d/%d] 退出码=%d Ran=%d failures=%d errors=%d skipped=%d"
                  % (row["index"] + 1, len(plan), row["returncode"], row["ran"],
                     row["failures"], row["errors"], row["skipped"]))
            if not row["ok"]:
                tail = "\n".join(row["output"].strip().splitlines()[-20:])
                print(tail)
        shards.append({key: value for key, value in row.items() if key != "output"})
    return {"ok": overall_ok, "shards": shards, "totals": aggregates}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="分片全量回归运行器（纯本地，不联网）")
    parser.add_argument("--list", action="store_true", help="每行打印一个模块名后退出")
    parser.add_argument("--shards", type=int, default=0, help="分片数")
    parser.add_argument("--jobs", type=int, default=0,
                        help="并发分片数；0=按 CPU 数（默认），1=串行")
    parser.add_argument("--dry-run", action="store_true", help="只打印分片命令，不执行")
    parser.add_argument("--json", action="store_true", help="输出机器可读汇总")
    parser.add_argument("--root", default="tests", help="模块根目录（默认 tests）")
    parser.add_argument("--base-tmp", default=None, help="分片临时根（默认系统临时目录下）")
    args = parser.parse_args(argv)

    modules = discover_modules(args.root)
    if args.list:
        for name in modules:
            print(name)
        return 0

    shards = args.shards or 1
    plan = plan_shards(modules, shards)
    base_tmp = args.base_tmp or DEFAULT_BASE_TMP

    if args.dry_run:
        print("# 分片回归计划：%d 个分片 / 并发 %d —— dry-run，不执行任何用例"
          % (len(plan), _resolve_jobs(args.jobs, plan)))
        for index, part in enumerate(plan):
            print(_command(part, shard_env(index, base_tmp)))
        return 0

    summary = run_shards(plan, base_tmp, json_out=args.json, jobs=args.jobs)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        totals = summary["totals"]
        print("总计：Ran=%d failures=%d errors=%d skipped=%d —— %s"
              % (totals["ran"], totals["failures"], totals["errors"], totals["skipped"],
                 "全部通过" if summary["ok"] else "有分片失败"))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
