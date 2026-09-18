# -*- coding: utf-8 -*-
"""`postgres_integration` 层：在**隔离 PostgreSQL** 上验证唯一约束、事务与原子领取。

为什么单独一层：`cpq_wf` 的并发正确性最终由数据库裁决（`uq_wf_handoff_key` 唯一索引、
`WHERE status='open'` 的原子 UPDATE、`uq_wf_task_open_kind` 部分唯一索引、非 autocommit
的 `tx_connect()` 事务）。受控假库能验证调用顺序，**验证不了**这些约束真的存在、真的
生效。本层把它们跑在真库上。

安全边界（写死在代码里，不靠约定）：
  · 只认 `CPQ_EVAL_PG_*` 环境变量；**绝不**回退到生产用的 `CPQ_PG_*`；
  · host 必须是回环地址（127.0.0.1 / localhost / ::1），CI 用 service container；
  · dsn / host / database 命中 pdt / prod / 生产 IP / metabase 等特征立即拒绝；
  · 每条案例自建 `cpq_eval_it_<hex>` 临时库，跑完在 finally 里 `DROP DATABASE ... WITH (FORCE)`；
  · 只删自己建的库，不做任何 DROP SCHEMA / DELETE 之外的数据清理。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from . import pg_guard

ROOT = Path(__file__).resolve().parents[2]

ENV_PREFIX = pg_guard.ENV_PREFIX
# 兼容旧引用：常量统一由 `pg_guard` 定义一次，父 / 子层共用同一套白名单。
PRODUCTION_MARKERS = pg_guard.PRODUCTION_MARKERS
LOOPBACK_HOSTS = pg_guard.LOOPBACK_HOSTS
DEFAULT_PORT = "5432"
DEFAULT_USER = "postgres"
DEFAULT_MAINTENANCE_DB = "postgres"
RESULT_PREFIX = "@@CPQ_EVAL_PG_RESULT@@ "
TEMP_DB_PREFIX = pg_guard.TEMP_DB_PREFIX
TEMP_DB_RE = pg_guard.TEMP_DB_RE
PgGuardError = pg_guard.PgGuardError


def integration_enabled() -> bool:
    return pg_guard.integration_enabled()


def config() -> dict:
    """只读 `CPQ_EVAL_PG_*`；没有配置就明确返回空，不去猜生产参数。"""
    host = str(os.environ.get(ENV_PREFIX + "HOST") or "").strip()
    return {
        "host": host,
        "port": str(os.environ.get(ENV_PREFIX + "PORT") or DEFAULT_PORT).strip(),
        "user": str(os.environ.get(ENV_PREFIX + "USER") or DEFAULT_USER).strip(),
        "password": str(os.environ.get(ENV_PREFIX + "PASSWORD") or ""),
        "maintenance_db": str(os.environ.get(ENV_PREFIX + "MAINTENANCE_DB")
                              or DEFAULT_MAINTENANCE_DB).strip(),
        "configured": bool(host),
    }


def guard_config(cfg: dict) -> dict:
    """连之前先把连接参数检查一遍；命中生产特征或未开开关直接抛 PgGuardError。

    与子进程 `pg_scenarios.guard` 调用的是 `pg_guard.guard` **同一份定义**，
    不再各写一套 host 白名单。
    """
    if not cfg.get("configured"):
        raise PgGuardError(f"未配置 {ENV_PREFIX}HOST（本层只认 {ENV_PREFIX}* 变量，"
                           f"不读生产 CPQ_PG_*）")
    info = pg_guard.guard(cfg)
    cfg["mode"] = info.get("mode")
    return info


def python_executable() -> str:
    """跑子进程的解释器：CI 默认当前解释器，本地可指向装了 psycopg 的 venv。"""
    return str(os.environ.get(ENV_PREFIX + "PYTHON") or sys.executable)


_DRIVER_CACHE: dict = {}


def driver_available() -> tuple:
    """子进程解释器里能不能 import psycopg（缺驱动只能 skip，不能假装通过）。"""
    exe = python_executable()
    if exe in _DRIVER_CACHE:
        return _DRIVER_CACHE[exe]
    try:
        proc = subprocess.run([exe, "-c", "import psycopg, sys; print(psycopg.__version__)"],
                              capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    except (OSError, subprocess.SubprocessError) as exc:
        out = (False, f"无法启动 {exe}：{type(exc).__name__}: {exc}")
    else:
        if proc.returncode == 0:
            out = (True, f"psycopg {proc.stdout.strip()}")
        else:
            tail = ((proc.stderr or "").strip().splitlines() or [""])[-1][:160]
            out = (False, f"{exe} 缺 psycopg（pip install 'psycopg[binary]'）：{tail}")
    _DRIVER_CACHE[exe] = out
    return out


def available() -> tuple:
    """(ok, reason)：环境齐不齐；不齐就如实跳过。"""
    cfg = config()
    try:
        guard_config(cfg)
    except PgGuardError as exc:
        return False, str(exc)
    ok, why = driver_available()
    if not ok:
        return False, why
    return True, f"{cfg['host']}:{cfg['port']}（{why}）"


def scenarios() -> dict:
    from . import pg_scenarios

    return dict(pg_scenarios.SCENARIOS)


def step_executor(case) -> list:
    """案例里声明的 `pg` 步骤 [(step_id, scenario, args)]。"""
    out = []
    for step in (case.get("input") or {}).get("steps") or []:
        spec = step.get("pg")
        if spec:
            out.append((str(step.get("id") or ""), str(spec.get("scenario") or ""),
                        dict(spec.get("args") or {})))
    return out


def run_case(case, mutate: str = "") -> dict:
    """执行一条 postgres_integration 案例；返回与 production 层一致的 observation 形状。

    ``mutate`` 只给 sentinel 测试用：把 MUTATIONS 里的故障注入子进程，证明「去掉唯一约束 /
    事务回滚 / 幂等判断」后对应案例会变红。正常执行一律不传。
    """
    ok, why = available()
    if not ok:
        return {"status": "skipped", "reason": why, "observation": {}}
    steps = step_executor(case)
    if not steps:
        return {"status": "failed",
                "reason": "postgres_integration 案例必须声明 input.steps[*].pg.scenario",
                "observation": {}}
    known = scenarios()
    observations, order, records, trace = {}, [], {}, []
    for step_id, scenario, args in steps:
        order.append(step_id)
        if scenario not in known:
            observations[step_id] = {"kind": "pg", "ok": False,
                                     "error": {"type": "unknown_scenario",
                                               "message": f"未注册的 PG 场景：{scenario}"}}
            continue
        payload, err = _run_child(scenario, args, mutate=mutate)
        if err is not None:
            observations[step_id] = {"kind": "pg", "ok": False,
                                     "error": {"type": "pg_child_error", "message": err}}
            continue
        observations[step_id] = {"kind": "pg", "ok": bool(payload.get("ok", True)),
                                 "scenario": scenario, "result": payload.get("result"),
                                 "rows": payload.get("rows"), "db": payload.get("db"),
                                 "cleanup": payload.get("cleanup"),
                                 "error": payload.get("error")}
        records["pg_queries"] = records.get("pg_queries", 0) + int(payload.get("queries") or 0)
        for item in payload.get("trace") or []:
            trace.append({"pg": item})
    observation = {"executor": "postgres_integration", "steps": observations, "order": order,
                   "records": records, "trace": trace, "projects": {}, "identities": {}}
    observation["last"] = observations.get(order[-1], {}) if order else {}
    try:
        observation["expected"] = case.get("expected") or {}
    except Exception:  # pragma: no cover
        pass
    return {"status": "executed", "reason": "", "observation": observation}


def touched_production(observation: dict) -> list:
    """本案例真实跑过的 PG 场景（质量守护：声明了 PG 层就必须真连过隔离库）。"""
    out = []
    for _sid, obs in (observation.get("steps") or {}).items():
        if obs.get("kind") == "pg" and obs.get("scenario"):
            out.append(f"pg:{obs['scenario']}")
    return out


def mutations() -> dict:
    from . import pg_scenarios

    return dict(pg_scenarios.MUTATIONS)


def probe(scenario: str, args: dict = None, mutate: str = "") -> tuple:
    """给 sentinel/守护测试用：跑一个场景（可注入故障），返回 (payload, error)。"""
    ok, why = available()
    if not ok:
        return None, why
    return _run_child(scenario, dict(args or {}), mutate=mutate)


# 父进程兜底清理统计（写进报告：cleanup succeeded / failed，不静默忽略）。
_STATS: dict = {
    "child_runs": 0,
    "parent_cleanup_attempted": 0,
    "parent_cleanup_dropped": 0,
    "parent_cleanup_failed": 0,
}


def stats() -> dict:
    return dict(_STATS)


def reset_stats() -> None:
    for key in _STATS:
        _STATS[key] = 0


def _connect_admin_cfg(cfg: dict):
    import psycopg

    return psycopg.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                           password=cfg["password"], dbname=cfg["maintenance_db"],
                           connect_timeout=10, autocommit=True)


def cleanup_orphan(cfg: dict, child_db: str, connect=None) -> dict:
    """父进程兜底清理：只 DROP 本轮生成、严格匹配 `cpq_eval_it_<10 hex>` 的临时库。

    超时 / 子进程崩溃 / 返回格式错误时调用。清理前再用**同一份** `pg_guard.guard`
    校验 host 与 maintenance database；禁止模糊匹配批量 DROP。
    """
    result = {"attempted": False, "dropped": False, "reason": "", "database": child_db}
    name = str(child_db or "").strip()
    if not TEMP_DB_RE.match(name):
        result["reason"] = f"数据库名 {name!r} 不匹配 {pg_guard.TEMP_DB_RE.pattern}，拒绝清理"
        return result
    try:
        pg_guard.guard(cfg)
    except PgGuardError as exc:
        # 注意：这里不再重复计 integration 开关，父进程本来就只在开关打开时才调用。
        result["reason"] = f"连接参数不安全，拒绝清理：{exc}"
        return result
    connect = connect or _connect_admin_cfg
    _STATS["parent_cleanup_attempted"] += 1
    result["attempted"] = True
    admin = None
    try:
        admin = connect(cfg)
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        result["dropped"] = True
        _STATS["parent_cleanup_dropped"] += 1
    except Exception as exc:                              # noqa: BLE001 - 清理失败要如实上报
        result["reason"] = f"{type(exc).__name__}: {exc}"
        _STATS["parent_cleanup_failed"] += 1
    finally:
        if admin is not None:
            try:
                admin.close()
            except Exception:                             # pragma: no cover
                pass
    return result


def _run_child(scenario: str, args: dict, mutate: str = "") -> tuple:
    """在子进程里跑一个 PG 场景；子进程自己建临时库、跑完自己删。

    超时 / 崩溃 / 输出格式错误时，父进程用 `cleanup_orphan` 兜底清理本轮临时库。
    """
    cfg = config()
    child_db = TEMP_DB_PREFIX + uuid.uuid4().hex[:10]
    _STATS["child_runs"] += 1
    env = dict(os.environ)
    env.update({
        ENV_PREFIX + "HOST": cfg["host"], ENV_PREFIX + "PORT": cfg["port"],
        ENV_PREFIX + "USER": cfg["user"], ENV_PREFIX + "PASSWORD": cfg["password"],
        ENV_PREFIX + "MAINTENANCE_DB": cfg["maintenance_db"],
        ENV_PREFIX + "CHILD_DB": child_db,
        # 应用侧连接参数在**子进程启动前**就指向临时库：任何 import 顺序都不会
        # 绑到生产 CPQ_PG_*（子进程里还会再断言一次，见 pg_scenarios.bound_db）。
        "CPQ_PG_HOST": cfg["host"], "CPQ_PG_PORT": cfg["port"],
        "CPQ_PG_USER": cfg["user"], "CPQ_PG_PASSWORD": cfg["password"],
        "CPQ_PG_DATABASE": child_db,
        "PYTHONPATH": str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    cmd = [python_executable(), "-m", "scripts.cpq_eval.pg_scenarios",
           "--scenario", scenario, "--args", json.dumps(args, ensure_ascii=False)]
    if mutate:
        cmd += ["--mutate", mutate]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env,
                              cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        cleaned = cleanup_orphan(cfg, child_db)
        return None, (f"PG 场景 {scenario} 超时（300s）；临时库 {child_db} 兜底清理："
                      f"attempted={cleaned['attempted']}, dropped={cleaned['dropped']}, "
                      f"reason={cleaned['reason'] or '-'}")
    except Exception as exc:                              # noqa: BLE001 - 启动失败也要兜底
        cleaned = cleanup_orphan(cfg, child_db)
        return None, (f"PG 场景 {scenario} 子进程启动失败（{type(exc).__name__}: {exc}）；"
                      f"兜底清理 attempted={cleaned['attempted']}, dropped={cleaned['dropped']}")
    payload = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith(RESULT_PREFIX):
            try:
                payload = json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                payload = None
    if payload is None:
        cleaned = cleanup_orphan(cfg, child_db)
        tail = ((proc.stderr or "").strip().splitlines() or [""])[-1][:200]
        return None, (f"PG 场景 {scenario} 没有返回结果（exit={proc.returncode}）：{tail}；"
                      f"兜底清理 attempted={cleaned['attempted']}, dropped={cleaned['dropped']}")
    payload.setdefault("parent_cleanup", {"attempted": False, "dropped": False, "reason": ""})
    return payload, None
