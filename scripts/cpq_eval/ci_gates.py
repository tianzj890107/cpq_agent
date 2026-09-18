# -*- coding: utf-8 -*-
"""CPQ 回归测试集的 CI 门禁分层入口（四类门禁，互相独立、都可离线跑）。

用法：

    python3 -m scripts.cpq_eval.ci_gates --list
    python3 -m scripts.cpq_eval.ci_gates --gate fast
    python3 -m scripts.cpq_eval.ci_gates --gate production_http
    python3 -m scripts.cpq_eval.ci_gates --gate recorded_provider
    python3 -m scripts.cpq_eval.ci_gates --gate postgres_integration   # 需要隔离 PG
    python3 -m scripts.cpq_eval.ci_gates --gate offline                # fast + http + provider

四类门禁与 runner 的执行层一一对应（**不把 simulation 通过当生产通过**）：

    · fast                —— simulation + production_unit（完全离线，CI 必跑）；
    · production_http     —— 真实 FastAPI app / TestClient + 真鉴权依赖（离线，CI 必跑）；
    · recorded_provider   —— 回放固定 provider 响应，进真实工具分发边界（离线，CI 必跑）；
    · postgres_integration—— 隔离 PostgreSQL 的并发 / 唯一约束 / 事务（默认 skip，需显式 PG）。

任何门禁都不访问 PDT / 生产 / 真实模型 / 线上服务。缺隔离 PostgreSQL 时该门禁显式打印
``SKIPPED`` 并以 0 退出，绝不伪装成 ``PASSED``；报告里 integration 也单列 skipped。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 生产层（production_unit / production_http / recorded_provider）能不能装载，取决于真实
# 生产入口的 import 链（fastapi / pydantic / sqlalchemy / openai / tzdata …）。`--strict`
# 下先在**同一个解释器**里做一次 preflight：不能装载就直接判门禁失败，绝不静默 skip。
_PROD_PREFLIGHT = (
    "import sys;"
    "sys.path[:0]=[{tech!r}, {root!r}];"
    "from scripts.cpq_eval import prodkit;"
    "ok, why = prodkit.available();"
    "print('OK' if ok else 'FAIL');"
    "print(why or '')"
)

# 每个门禁 = 一组 `runner` 参数（顺序执行，全部 0 才算门禁通过）。
GATES = {
    "fast": {
        "title": "快速门禁：simulation + production_unit（离线，CI 必跑）",
        "commands": [
            ["--executor", "simulation", "--no-report", "--quiet"],
            ["--executor", "production_unit", "--no-report", "--quiet"],
        ],
        "optional": False,
    },
    "production_http": {
        "title": "真实路由门禁：production_http（离线 TestClient）",
        "commands": [["--executor", "production_http", "--no-report", "--quiet"]],
        "optional": False,
    },
    "recorded_provider": {
        "title": "回放门禁：recorded_provider（固定 SSE，不访问真实模型）",
        "commands": [["--executor", "recorded_provider", "--no-report", "--quiet"]],
        "optional": False,
    },
    "postgres_integration": {
        "title": "集成门禁：postgres_integration（隔离 PostgreSQL，缺环境 SKIP）",
        "commands": [["--executor", "postgres_integration", "--no-report", "--quiet"]],
        "optional": True,
    },
}
OFFLINE_GROUP = ("fast", "production_http", "recorded_provider")


def _pg_available() -> tuple:
    from . import pg_integration

    return pg_integration.available()


def _command_argv(args: list) -> list:
    return [sys.executable, "-m", "scripts.cpq_eval.runner", *args]


def production_preflight() -> tuple:
    """(ok, reason)：当前解释器能否装载真实生产模块（缺依赖 → 不让门禁静默 skip）。"""
    code = _PROD_PREFLIGHT.format(tech=str(ROOT / "tech_app"), root=str(ROOT))
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), text=True,
                          capture_output=True)
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    ok = bool(lines) and lines[0].strip() == "OK"
    reason = lines[1].strip() if len(lines) > 1 else ((proc.stderr or "").strip()[-200:])
    return ok, ("" if ok else f"真实生产模块不可装载：{reason}")


def _report_skips(directory: str) -> tuple:
    """读一份 runner 报告，返回 ``(passed, skipped, failed)``。"""
    path = Path(directory) / "cpq_eval_report.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0, 0
    by_executor = (report.get("summary") or {}).get("by_executor") or {}
    passed = sum(int((row or {}).get("passed", 0)) for row in by_executor.values())
    skipped = sum(int((row or {}).get("skipped", 0)) for row in by_executor.values())
    failed = sum(int((row or {}).get("failed", 0)) + int((row or {}).get("invalid", 0))
                 for row in by_executor.values())
    return passed, skipped, failed


def _pg_all_skipped(report: dict) -> bool:
    """环境已配置（有 PG）但案例一条都没真跑 —— CI 不允许把它当通过。"""
    pg = (report or {}).get("postgres") or {}
    total = int(pg.get("case_count", 0) or 0)
    skipped = int(pg.get("skipped", 0) or 0)
    return bool(total) and skipped >= total


def _run_postgres_gate(strict: bool, stream: bool) -> tuple:
    """PG 门禁：跑全部 integration 案例 + 真跑 mutation sentinel。

    ``strict``（CI job 用）：缺 PG 环境、案例全 skip、或任一 mutation survived 都返回
    非零 —— 不允许拿「全部 skip」冒充通过。普通本地环境不传 strict 时缺环境仍 skip。
    """
    import json as _json
    import tempfile

    from . import pg_integration

    ok, why = pg_integration.available()
    if not ok:
        if stream:
            print(f"[{'FAIL' if strict else 'SKIP'}] postgres_integration：{why}")
            if not strict:
                print("       （integration 记 skipped，不计入 passed）")
        return ("failed", 1) if strict else ("skipped", 0)
    with tempfile.TemporaryDirectory(prefix="cpq_eval_pg_gate_") as tmp:
        proc = subprocess.run(
            _command_argv(["--executor", "postgres_integration", "--report", tmp, "--quiet"]),
            cwd=str(ROOT), text=True, capture_output=not stream)
        report_path = __import__("pathlib").Path(tmp) / "cpq_eval_report.json"
        pg = {}
        try:
            pg = (_json.loads(report_path.read_text(encoding="utf-8")) or {}).get("postgres") or {}
        except (OSError, ValueError):
            pg = {}
        if stream:
            print(f"  postgres case count={pg.get('case_count', 0)} "
                  f"unique scenarios={pg.get('unique_scenario_count', 0)} "
                  f"executed={pg.get('executed', 0)} passed={pg.get('passed', 0)} "
                  f"failed={pg.get('failed', 0)} skipped={pg.get('skipped', 0)} "
                  f"cleanup_ok={pg.get('cleanup_succeeded', 0)} "
                  f"cleanup_failed={pg.get('cleanup_failed', 0)}")
        if proc.returncode != 0:
            if not stream:
                print(proc.stdout)
                print(proc.stderr, file=sys.stderr)
            return "failed", proc.returncode
        if strict and _pg_all_skipped({"postgres": pg}):
            print("[FAIL] postgres_integration：环境已配置但全部案例都被 skip，"
                  "不能算通过", file=sys.stderr)
            return "failed", 1
    sentinel = subprocess.run(
        [sys.executable, "-m", "scripts.cpq_eval.pg_sentinel", "--require-all-killed"],
        cwd=str(ROOT), text=True, capture_output=not stream)
    if sentinel.returncode != 0:
        if not stream:
            print(sentinel.stdout)
            print(sentinel.stderr, file=sys.stderr)
        return "failed", sentinel.returncode
    return "passed", 0


def run_gate(name: str, stream=True, strict: bool = False) -> tuple:
    """跑一个门禁；返回 ``(status, returncode)``，status ∈ passed / failed / skipped。

    ``strict``（CI job 用）：跑之前先做生产依赖 preflight；跑完检查**没有任何案例被
    静默 skip**，也没有 0 通过的空跑。缺依赖 / 全 skip 一律非零 —— 不给「缺包就跳过」
    留后门。
    """
    gate = GATES[name]
    if name == "postgres_integration":
        return _run_postgres_gate(strict, stream)
    if strict:
        ok, why = production_preflight()
        if not ok:
            print(f"[FAIL] {name}：{why}", file=sys.stderr)
            print("       （CI 必须安装 requirements.txt；缺依赖不得降级为 skip）",
                  file=sys.stderr)
            return "failed", 1
    totals = [0, 0, 0]
    for args in gate["commands"]:
        run_args = list(args)
        tmp = None
        if strict:
            tmp = tempfile.mkdtemp(prefix="cpq_eval_gate_")
            run_args = [a for a in run_args if a != "--no-report"] + ["--report", tmp]
        proc = subprocess.run(_command_argv(run_args), cwd=str(ROOT), text=True,
                              capture_output=not stream)
        if proc.returncode != 0:
            if not stream:
                print(proc.stdout)
                print(proc.stderr, file=sys.stderr)
            return "failed", proc.returncode
        if strict and tmp:
            passed, skipped, failed = _report_skips(tmp)
            totals[0] += passed
            totals[1] += skipped
            totals[2] += failed
    if strict:
        passed, skipped, failed = totals
        if stream:
            print(f"  {name}: passed={passed} skipped={skipped} failed={failed}")
        if failed:
            print(f"[FAIL] {name}：有 {failed} 条 failed / invalid", file=sys.stderr)
            return "failed", 1
        if skipped:
            print(f"[FAIL] {name}：{skipped} 条被静默 skip（缺依赖 / 环境不完整），"
                  f"strict 门禁不接受", file=sys.stderr)
            return "failed", 1
        if not passed:
            print(f"[FAIL] {name}：没有任何案例真正执行", file=sys.stderr)
            return "failed", 1
    return "passed", 0


def run_group(names: tuple, strict: bool = False) -> int:
    results = []
    for name in names:
        status, code = run_gate(name, strict=strict)
        results.append((name, status, code))
    failed = [row for row in results if row[1] == "failed"]
    skipped = [row for row in results if row[1] == "skipped"]
    print("== CPQ CI 门禁汇总 ==")
    for name, status, _code in results:
        print(f"  {name:22s} {status}")
    if skipped:
        print(f"  skipped={len(skipped)}（未执行，不算 passed）："
              + ", ".join(row[0] for row in skipped))
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.cpq_eval.ci_gates")
    parser.add_argument("--list", action="store_true", help="列出门禁与命令")
    parser.add_argument("--gate", default="", help="跑单个门禁（fast / production_http / "
                                                   "recorded_provider / postgres_integration）")
    parser.add_argument("--group", default="", help="跑一组门禁：offline（三类离线门禁）")
    parser.add_argument("--strict", action="store_true",
                        help="CI 用：生产依赖 preflight + 案例静默 skip 一律非零；"
                             "postgres_integration 的缺环境 / 全 skip / mutation survived "
                             "同样非零 —— 绝不把 skip 当通过")
    opts = parser.parse_args(argv)
    if opts.list:
        for name, gate in GATES.items():
            optional = "（可跳过）" if gate.get("optional") else ""
            print(f"{name}{optional}：{gate['title']}")
            for args in gate["commands"]:
                print("    " + " ".join(_command_argv(args)))
        return 0
    if opts.gate:
        if opts.gate not in GATES:
            print(f"未知门禁 {opts.gate}；可选：{', '.join(GATES)}", file=sys.stderr)
            return 2
        status, code = run_gate(opts.gate, strict=opts.strict)
        print(f"{opts.gate}: {status.upper()}")
        return code
    if opts.group:
        if opts.group != "offline":
            print(f"未知门禁组 {opts.group}；可选：offline", file=sys.stderr)
            return 2
        return run_group(OFFLINE_GROUP, strict=opts.strict)
    return run_group(tuple(GATES), strict=opts.strict)


if __name__ == "__main__":
    raise SystemExit(main())
