# -*- coding: utf-8 -*-
"""`postgres_integration` 场景驱动（**子进程**入口）。

必须在真库上跑、且必须证明「跑在真库上」：本模块启动后第一件事是拿
`CPQ_EVAL_PG_*` 建一条 `cpq_eval_it_<hex>` 临时库，然后断言应用侧
（`cpq_auth.cpq_db.PG_DATABASE`）绑定的正是这条临时库、host 是回环地址。
断言不过就直接失败 —— 宁可红，也不在生产库上跑业务动作。

调用方式（由 `pg_integration._run_child` 拉起）：

    python3 -m scripts.cpq_eval.pg_scenarios --scenario <name> --args '{...}'

每个场景返回 ``{"result": {...}, "rows": {...}, "trace": [...]}``，断言写在案例的
`expected.state` 里，和其它层共用 runner 的比较引擎。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import pg_guard

RESULT_PREFIX = "@@CPQ_EVAL_PG_RESULT@@ "
ENV_PREFIX = pg_guard.ENV_PREFIX
# 父 / 子层共用同一份白名单定义，避免一边允许、一边拒绝。
PRODUCTION_MARKERS = pg_guard.PRODUCTION_MARKERS
LOOPBACK_HOSTS = pg_guard.LOOPBACK_HOSTS
TEMP_DB_RE = pg_guard.TEMP_DB_RE


class ScenarioError(Exception):
    """场景前置条件不满足（例如临时库没建成）—— 记为失败，不当成跳过。"""


# --------------------------------------------------------------------------- #
# 环境 / 临时库
# --------------------------------------------------------------------------- #
def _cfg() -> dict:
    return {
        "host": str(os.environ.get(ENV_PREFIX + "HOST") or ""),
        "port": str(os.environ.get(ENV_PREFIX + "PORT") or "5432"),
        "user": str(os.environ.get(ENV_PREFIX + "USER") or "postgres"),
        "password": str(os.environ.get(ENV_PREFIX + "PASSWORD") or ""),
        "maintenance_db": str(os.environ.get(ENV_PREFIX + "MAINTENANCE_DB") or "postgres"),
        "child_db": str(os.environ.get(ENV_PREFIX + "CHILD_DB") or ""),
    }


def guard(cfg: dict) -> dict:
    """与父进程 `pg_integration.guard_config` 调用同一份 `pg_guard.guard`。"""
    try:
        return pg_guard.guard(cfg)
    except pg_guard.PgGuardError as exc:
        raise ScenarioError(str(exc)) from exc


def _connect_admin(cfg: dict):
    import psycopg

    return psycopg.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                           password=cfg["password"], dbname=cfg["maintenance_db"],
                           connect_timeout=10, autocommit=True)


def create_temp_db(admin, name: str) -> None:
    admin.execute(f'CREATE DATABASE "{name}"')


def drop_temp_db(admin, name: str) -> None:
    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# 生产库里的 `snow_next_id()` 是**库函数**（仓库内没有它的 DDL，只有客户端回退实现
# `cpq_db._client_snow_id`）。临时库里没有它时，`SELECT snow_next_id()` 会在事务内报错
# 并把事务置为 aborted（`cpq_db.snow_next_id` 吞掉异常、回退客户端实现，但事务已经脏了）。
# 所以这里在临时库里补一个**语义等价**的库函数：与客户端回退同一套算法
# （epoch 1314220021721 / shard 5 / 12 位序列），只保证「唯一 bigint」。
# 生产库已有同名函数时不动它（只报 pre-existing）。
_SNOW_EPOCH = 1314220021721
_SNOW_SHARD = 5
_SNOW_FN_SQL = f"""
CREATE SEQUENCE IF NOT EXISTS public.cpq_eval_snow_seq;
CREATE OR REPLACE FUNCTION public.snow_next_id() RETURNS bigint AS $$
  SELECT ((floor(extract(epoch from clock_timestamp()) * 1000)::bigint - {_SNOW_EPOCH}) << 23)
         | ({_SNOW_SHARD} << 10) | (nextval('public.cpq_eval_snow_seq') % 4096);
$$ LANGUAGE sql;
"""


# 故障注入（只作用于临时库 / 子进程内的生产模块对象，绝不动仓库代码）。
# 用途：证明「数据库唯一约束 / 事务语义真的在裁决」——注入后对应用例必须变红。
MUTATIONS = {
    "autocommit_tx": "把 cpq_wf.tx_connect 换成 autocommit 连接：多写一步 = 多提交一次",
    "no_rollback_tx": "事务出错时仍然 commit：前半段写入不会被回滚",
    "handoff_no_conflict": "回传占位去掉 ON CONFLICT DO NOTHING：幂等键失去裁决力",
    "drop_handoff_index": "DROP INDEX uq_wf_handoff_key：唯一约束消失",
    "drop_task_open_index": "DROP INDEX uq_wf_task_open_kind：同卡片同类型 open 不再唯一",
    "claim_without_open_guard": "原子领取的 UPDATE 去掉 status='open' 条件：并发下两人都能领取",
    "blind_task_lookup": "任务复用的前置查询返回空：应用层不再判重，只剩数据库约束",
    "claim_ignore_eligibility": "领取资格判定被绕过（任务被当成 public）：无权限角色也能领取并写副作用",
}


def _patch_exec_filter(predicate, transform):
    """在 `cpq_auth._exec` 上装一层 SQL 改写（只作用于本子进程）。"""
    import cpq_auth

    real = cpq_auth._exec

    def wrapped(conn, sql, args=None):
        text = str(sql)
        if predicate(text):
            text = transform(text)
        return real(conn, text, args) if args is not None else real(conn, text)

    cpq_auth._exec = wrapped


def _patch_exec_row(predicate, index, value):
    """在 `cpq_auth._exec` 上改写**取回的行**：把第 index 列换成 value（只作用于本子进程）。

    用途：模拟「领取资格判定被绕过」——不复制生产 `claim_task`，只改它读到的目标类型。
    """
    import cpq_auth

    real = cpq_auth._exec

    def wrapped(conn, sql, args=None):
        cur = real(conn, sql, args) if args is not None else real(conn, sql)
        if not predicate(str(sql)):
            return cur

        class _Cursor:
            def __getattr__(self, item):
                return getattr(cur, item)

            def fetchone(self):
                row = cur.fetchone()
                if row is None:
                    return None
                row = list(row)
                row[index] = value
                return tuple(row)

        return _Cursor()

    cpq_auth._exec = wrapped


def _empty_cursor():
    class _Cursor:
        rowcount = 0

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    return _Cursor()


def apply_mutation(name: str, admin, schema: str) -> None:
    """按名字注入故障；未知名字直接报错，不静默忽略。"""
    if name not in MUTATIONS:
        raise ScenarioError(f"未注册的故障注入：{name!r}")
    if name == "drop_handoff_index":
        admin.execute(f"DROP INDEX IF EXISTS {schema}.uq_wf_handoff_key")
        return
    if name == "drop_task_open_index":
        admin.execute(f"DROP INDEX IF EXISTS {schema}.uq_wf_task_open_kind")
        return
    if name == "autocommit_tx":
        import cpq_auth
        import cpq_wf

        # `cpq_auth._connect()` 就是 autocommit=True：每写一步各自提交，事后 rollback 无效。
        cpq_wf.tx_connect = cpq_auth._connect
        return
    if name == "no_rollback_tx":
        import cpq_wf

        real_connect = cpq_wf.tx_connect

        class _CommitInsteadOfRollback:
            """包一层连接：`rollback()` 被换成 `commit()`（模拟"出错照样提交 / 不回滚"）。"""

            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, item):
                return getattr(self._conn, item)

            def rollback(self):
                self._conn.commit()

        cpq_wf.tx_connect = lambda: _CommitInsteadOfRollback(real_connect())
        return
    if name == "claim_without_open_guard":
        _patch_exec_filter(
            lambda sql: "UPDATE cpq_wf_task" in sql and "AND status = 'open'" in sql,
            lambda sql: sql.replace("AND status = 'open'", ""))
        return
    if name == "blind_task_lookup":
        import cpq_auth

        real = cpq_auth._exec

        def wrapped(conn, sql, args=None):
            if "FROM cpq_wf_task" in str(sql) and "status IN ('open', 'claimed')" in str(sql):
                return _empty_cursor()
            return real(conn, sql, args) if args is not None else real(conn, sql)

        cpq_auth._exec = wrapped
        return
    if name == "claim_ignore_eligibility":
        # `claim_task` 读任务行的 SQL 里第 3 列是 target_type；改成 'public' 后资格判定
        # `ttype == "public"` 恒真 —— 等同于删掉「你没有该任务的领取权限」这道门。
        _patch_exec_row(
            lambda sql: ("FROM cpq_wf_task WHERE task_id = %s" in sql
                         and "target_role_code" in sql),
            2, "public")
        return
    if name == "handoff_no_conflict":
        import cpq_auth
        import cpq_wf

        def insert_no_conflict(conn, handoff_id, handoff_key, handoff_kind, **kwargs):
            cpq_auth._exec(
                conn, "INSERT INTO cpq_wf_handoff (handoff_id, handoff_key, handoff_kind,"
                      " source_project_id, created_by_user_id, created_at, source_task_closed)"
                      " VALUES (%s,%s,%s,%s,%s,%s,false)",
                (handoff_id, str(handoff_key or "")[:255], handoff_kind or "",
                 kwargs.get("source_project_id") or None, kwargs.get("created_by_user_id"),
                 cpq_wf._ts(cpq_wf._now())))
            return True

        cpq_wf.insert_handoff_placeholder = insert_no_conflict


def ensure_snow_fn(cfg: dict) -> str:
    """临时库里保证 `snow_next_id()` 存在；返回 created / pre-existing。"""
    import psycopg

    conn = psycopg.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                           password=cfg["password"], dbname=cfg["child_db"],
                           connect_timeout=10, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regprocedure('public.snow_next_id()')")
            row = cur.fetchone()
            if row and row[0]:
                return "pre-existing"
            cur.execute(_SNOW_FN_SQL)
        return "created"
    finally:
        conn.close()


def child_admin(cfg: dict):
    """连到临时库自己的连接（DDL / 计数用，autocommit）。"""
    import psycopg

    return psycopg.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                           password=cfg["password"], dbname=cfg["child_db"],
                           connect_timeout=10, autocommit=True)


def bound_db() -> dict:
    """断言应用侧真的绑在**本轮创建的临时库**上；返回绑定信息（写进报告）。

    两层核对：应用常量 `cpq_db.PG_DATABASE` + 数据库自己回报的 `current_database()`。
    """
    import cpq_auth
    import cpq_wf  # noqa: F401  绑定同一套 PG_* 常量，顺带验证可导入

    cfg = _cfg()
    host = str(cpq_auth.cpq_db.PG_HOST)
    database = str(cpq_auth.cpq_db.PG_DATABASE)
    schema = str(cpq_auth.WF_SCHEMA)
    if database != cfg["child_db"]:
        raise ScenarioError(f"应用绑定的库是 {database!r}，不是临时库 {cfg['child_db']!r}，拒绝继续")
    if not TEMP_DB_RE.match(database):
        raise ScenarioError(f"应用连接的库 {database!r} 不符合 {TEMP_DB_RE.pattern}，拒绝继续")
    if host.strip().lower() not in LOOPBACK_HOSTS and not pg_guard.ci_flag():
        raise ScenarioError(f"应用绑定的 host={host!r} 不是回环地址且无 CI 标志，拒绝继续")
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(conn, "SELECT current_database()")
        row = cur.fetchone()
        live = str(row[0]) if row else ""
    finally:
        conn.close()
    if live != cfg["child_db"]:
        raise ScenarioError(f"current_database()={live!r} 与临时库 {cfg['child_db']!r} 不一致，拒绝继续")
    return {"host": host, "database": database, "schema": schema, "current_database": live,
            "temp_db_pattern": TEMP_DB_RE.pattern}


def init_schema() -> str:
    import cpq_auth
    import cpq_wf

    cpq_auth.init()
    return cpq_wf.init()


# --------------------------------------------------------------------------- #
# 场景工具
# --------------------------------------------------------------------------- #
class Ctx:
    """场景上下文：真实 `cpq_wf` 的连接与几张表的计数。"""

    def __init__(self):
        import cpq_auth
        import cpq_wf

        self.cpq_auth = cpq_auth
        self.cpq_wf = cpq_wf
        self.trace = []
        self.queries = 0

    # -- 连接 / SQL --
    def conn(self):
        self.queries += 1
        return self.cpq_auth._connect()

    def one(self, sql, args=()):
        conn = self.conn()
        try:
            cur = self.cpq_auth._exec(conn, sql, tuple(args))
            return cur.fetchone()
        finally:
            conn.close()

    def count(self, table, where="", args=()):
        sql = f"SELECT count(*) FROM {table}"
        if where:
            sql += " WHERE " + where
        row = self.one(sql, args)
        return int(row[0]) if row else 0

    # -- 造数（只造前置数据，业务动作一律走真实函数）--
    def user(self, role_code: str, username: str, display_name: str = "") -> dict:
        uid = int(self.cpq_auth.cpq_db.snow_next_id(self.conn()))
        cpq_auth = self.cpq_auth
        conn = cpq_auth._connect()
        try:
            cpq_auth._exec(
                conn, "INSERT INTO cpq_wf_user (user_id, username, display_name,"
                      " password_hash, role_code, status) VALUES (%s,%s,%s,%s,%s,'active')",
                (uid, username, display_name or username, "x", role_code))
        finally:
            conn.close()
        role_name = self.cpq_wf.ROLES.get(role_code, role_code)
        return {"user_id": uid, "username": username, "display_name": display_name or username,
                "role_code": role_code, "role_name": role_name}

    def card(self, session_id: str, user: dict, *, step: int = 1,
             business_case_id: str = "") -> dict:
        return self.cpq_wf.sync_card(session_id, user, title=f"IT {session_id}",
                                     customer="IT 客户", current_step=step,
                                     business_case_id=business_case_id)

    def send(self, session_id: str, user: dict, **kwargs) -> dict:
        return self.cpq_wf.send_task(session_id, user, **kwargs)


def _concurrent(fn, count: int = 2, timeout: float = 20.0) -> dict:
    """用 `threading.Barrier` 让 N 个独立上下文同时起跑（不许用 sleep 制造竞争）。"""
    barrier = threading.Barrier(count)
    results = [None] * count
    errors = [None] * count

    def worker(index):
        try:
            barrier.wait(timeout=timeout)
            results[index] = fn(index)
        except Exception as exc:  # noqa: BLE001 - 竞态里的异常要如实收集
            errors[index] = {"type": type(exc).__name__, "message": str(exc)[:200]}

    pool = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(count)]
    started = time.time()
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join(timeout=timeout + 10)
    return {"results": results, "errors": errors,
            "ok_count": sum(1 for r in results if isinstance(r, dict) and r.get("ok")),
            "error_count": sum(1 for e in errors if e),
            "duration_ms": int((time.time() - started) * 1000)}


# --------------------------------------------------------------------------- #
# 场景
# --------------------------------------------------------------------------- #
def claim_concurrent_two_claimers(ctx: Ctx, **_) -> dict:
    """两名人员同时领取同一任务：只有一方成功，另一方零副作用。"""
    owner = ctx.user("sales_mgr", "it_owner")
    a = ctx.user("process_mgr", "it_pm_a")
    b = ctx.user("process_mgr", "it_pm_b")
    ctx.card("it-claim", owner)
    task = ctx.send("it-claim", owner, target_type="role", target_role_code="process_mgr")
    tid = task["task_id"]

    def claim(index):
        user = a if index == 0 else b
        try:
            out = ctx.cpq_wf.claim_task(tid, user)
            return {"ok": True, "already": bool(out.get("already")),
                    "by": str(user["user_id"])}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "already": False, "by": str(user["user_id"]),
                    "error": str(exc)[:120]}

    race = _concurrent(claim, 2)
    claimed = ctx.count("cpq_wf_task", "task_id = %s AND status = 'claimed'", (tid,))
    claim_events = ctx.count("cpq_wf_task_event", "task_id = %s AND action = 'claim'", (tid,))
    claimed_msgs = ctx.count("cpq_wf_message", "task_id = %s AND msg_type = 'task_claimed'", (tid,))
    rows = ctx.one("SELECT claimed_by_user_id, status FROM cpq_wf_task WHERE task_id = %s", (tid,))
    return {
        "successes": race["ok_count"],
        "errors": race["error_count"],
        "claimed_rows": claimed,
        "claim_events": claim_events,
        "claimed_messages": claimed_msgs,
        "claimed_by": str(rows[0]) if rows and rows[0] is not None else "",
        "status": str(rows[1]) if rows else "",
        "race": race["results"],
    }


def claim_role_denied_no_side_effect(ctx: Ctx, **_) -> dict:
    """没有该任务领取权限的角色发起领取：被拒且**零副作用**。"""
    owner = ctx.user("sales_mgr", "it_owner2")
    outsider = ctx.user("finance_mgr", "it_fin")
    ctx.card("it-denied", owner)
    task = ctx.send("it-denied", owner, target_type="role", target_role_code="process_mgr")
    tid = task["task_id"]
    before_owner = ctx.one("SELECT current_owner FROM cpq_wf_card WHERE session_id = 'it-denied'")[0]
    denied, message = False, ""
    try:
        ctx.cpq_wf.claim_task(tid, outsider)
    except Exception as exc:  # noqa: BLE001
        denied = True
        message = str(exc)[:120]
    after = ctx.one("SELECT status, claimed_by_user_id, current_owner FROM cpq_wf_task t"
                    " JOIN cpq_wf_card c ON c.card_id = t.card_id WHERE t.task_id = %s", (tid,))
    return {
        "denied": denied,
        "message": message,
        "task_status": str(after[0]) if after else "",
        "claimed_by": "" if (not after or after[1] is None) else str(after[1]),
        "claim_events": ctx.count("cpq_wf_task_event", "task_id = %s AND action = 'claim'", (tid,)),
        "claimed_messages": ctx.count("cpq_wf_message", "task_id = %s" " AND msg_type = 'task_claimed'", (tid,)),
        "owner_unchanged": before_owner == (after[2] if after else None),
    }


def handoff_concurrent_same_key(ctx: Ctx, **_) -> dict:
    """同一幂等键并发占位：数据库唯一索引裁决，只留一行。"""
    key = "it-handoff-" + uuid.uuid4().hex[:8]
    holder = ctx.user("process_mgr", "it_pm_h")

    def insert(index):
        conn = ctx.cpq_auth._connect()
        try:
            hid = int(ctx.cpq_auth.cpq_db.snow_next_id(conn))
            first = ctx.cpq_wf.insert_handoff_placeholder(
                conn, hid, key, "cost_to_quote", source_project_id=f"proj-{index}",
                created_by_user_id=int(holder["user_id"]), business_case_id="bc_it")
            return {"ok": True, "first": bool(first), "handoff_id": str(hid)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "first": False, "error": str(exc)[:160]}
        finally:
            conn.close()

    race = _concurrent(insert, 2)
    rows = ctx.count("cpq_wf_handoff", "handoff_key = %s", (key,))
    winners = sum(1 for r in race["results"] if isinstance(r, dict) and r.get("first"))
    stored = ctx.one("SELECT handoff_kind, source_project_id, business_case_id"
                     " FROM cpq_wf_handoff WHERE handoff_key = %s", (key,))
    return {
        "rows": rows,
        "winners": winners,
        "errors": race["error_count"],
        "handoff_kind": str(stored[0]) if stored else "",
        "business_case_id": str(stored[2]) if stored else "",
        "race": race["results"],
    }


def task_duplicate_submit_single_open_row(ctx: Ctx, **_) -> dict:
    """同一张卡片重复发起同签名任务：复用既有任务，不产生第二行 open。"""
    owner = ctx.user("sales_mgr", "it_owner3")
    ctx.card("it-dup", owner)
    first = ctx.send("it-dup", owner, target_type="role", target_role_code="process_mgr",
                     note="同样的备注")
    second = ctx.send("it-dup", owner, target_type="role", target_role_code="process_mgr",
                      note="同样的备注")
    return {
        "first_reused": bool(first.get("reused")),
        "second_reused": bool(second.get("reused")),
        "same_task": first["task_id"] == second["task_id"],
        "open_rows": ctx.count("cpq_wf_task", "card_id = (SELECT card_id FROM cpq_wf_card"
                                              " WHERE session_id = 'it-dup') AND status = 'open'"),
        "total_rows": ctx.count("cpq_wf_task", "card_id = (SELECT card_id FROM cpq_wf_card"
                                               " WHERE session_id = 'it-dup')"),
        "send_events": ctx.count("cpq_wf_task_event", "action = 'send' AND card_id ="
                                                      " (SELECT card_id FROM cpq_wf_card"
                                                      " WHERE session_id = 'it-dup')"),
    }


def tx_midway_failure_rolls_back_all(ctx: Ctx, **_) -> dict:
    """事务后半段失败：前半段已写入的回传占位必须整体回滚。

    严格照 `cpq_tech_bridge` 回传主流程的写法：`tx_connect()` 拿一条**非 autocommit**
    连接 → 多步写入 → 成功 `commit()` / 出错 `rollback()`（不是 `with conn.transaction()`）。
    autocommit 连接上 `rollback()` 是空操作、写入早已各自提交，所以这条案例能真正杀死
    「用 autocommit 破坏事务」和「出错仍然 commit / 不回滚」两类变异。
    """
    key = "it-tx-" + uuid.uuid4().hex[:8]
    holder = ctx.user("process_mgr", "it_pm_tx")
    conn = ctx.cpq_wf.tx_connect()
    autocommit = bool(conn.autocommit)
    error = ""
    try:
        hid = int(ctx.cpq_auth.cpq_db.snow_next_id(conn))
        ctx.cpq_wf.insert_handoff_placeholder(
            conn, hid, key, "cost_to_quote", created_by_user_id=int(holder["user_id"]))
        ctx.cpq_auth._exec(
            conn, "UPDATE cpq_wf_handoff SET step_no = %s WHERE handoff_id = %s", (3, hid))
        raise RuntimeError("模拟回传中途失败（第三步写任务时炸了）")
    except RuntimeError as exc:
        error = str(exc)[:120]
        try:
            conn.rollback()
        except Exception:                       # noqa: BLE001 - autocommit 连接回滚可能报错
            pass
    finally:
        conn.close()
    return {
        "raised": bool(error),
        "autocommit": autocommit,
        "rolled_back": ctx.count("cpq_wf_handoff", "handoff_key = %s", (key,)) == 0,
        "rows_after": ctx.count("cpq_wf_handoff", "handoff_key = %s", (key,)),
        "error": error,
    }


def legacy_null_new_columns_readable(ctx: Ctx, **_) -> dict:
    """legacy 行缺新列（business_case_id / task_kind 为 NULL）时仍按兼容合同可读。"""
    owner = ctx.user("sales_mgr", "it_owner_legacy")
    card = ctx.card("it-legacy", owner, step=4)
    tid = int(ctx.cpq_auth.cpq_db.snow_next_id(ctx.conn()))
    conn = ctx.cpq_auth._connect()
    try:
        ctx.cpq_auth._exec(
            conn, "INSERT INTO cpq_wf_task (task_id, card_id, from_user_id, from_step_no,"
                  " target_type, target_role_code, status, source_label, created_at, task_kind,"
                  " payload, supersedes_task_id, cancel_reason)"
                  " VALUES (%s,%s,%s,4,'role','process_mgr','open','legacy',%s,'handoff',NULL,NULL,NULL)",
            (tid, int(card["card_id"]), int(owner["user_id"]), ctx.cpq_wf._ts(ctx.cpq_wf._now())))
        ctx.cpq_auth._exec(conn, "UPDATE cpq_wf_card SET business_case_id = NULL WHERE card_id = %s",
                           (int(card["card_id"]),))
    finally:
        conn.close()
    detail = ctx.cpq_wf.task_detail(str(tid), owner)
    reread = ctx.cpq_wf.get_card("it-legacy")
    return {
        "task_kind": str(detail.get("task_kind") or ""),
        "task_status": str(detail.get("status") or ""),
        "card_business_case_id": str(reread.get("business_case_id") or ""),
        "card_step": int(reread.get("current_step") or 0),
        "readable": bool(detail.get("task_id")),
    }


def cross_instance_no_cross_handoff(ctx: Ctx, **_) -> dict:
    """两个业务实例并存：一个实例的回传记录绝不落到另一个实例上。"""
    holder = ctx.user("process_mgr", "it_pm_case")
    owner = ctx.user("sales_mgr", "it_owner_case")
    card_a = ctx.card("it-case-a", owner, business_case_id="bc_it_a")
    card_b = ctx.card("it-case-b", owner, business_case_id="bc_it_b")
    key_a = "it-cross-" + uuid.uuid4().hex[:8]
    conn = ctx.cpq_auth._connect()
    try:
        hid = int(ctx.cpq_auth.cpq_db.snow_next_id(conn))
        ctx.cpq_wf.insert_handoff_placeholder(
            conn, hid, key_a, "cost_to_quote", source_project_id="proj-a",
            created_by_user_id=int(holder["user_id"]), business_case_id="bc_it_a")
        ctx.cpq_wf.update_handoff(conn, hid, target_card_id=int(card_a["card_id"]),
                                  target_quote_session_id="it-case-a", step_no=3)
    finally:
        conn.close()
    read_conn = ctx.cpq_auth._connect()
    try:
        found_a = ctx.cpq_wf.find_handoff(read_conn, key_a)
        missing = ctx.cpq_wf.find_handoff(read_conn, key_a + "-x")
    finally:
        read_conn.close()
    detail_b = ctx.cpq_wf.card_detail("it-case-b", owner)
    return {
        "found_on_a": bool(found_a) and found_a.get("business_case_id") == "bc_it_a",
        "found_business_case": str((found_a or {}).get("business_case_id") or ""),
        "target_session": str((found_a or {}).get("target_quote_session_id") or ""),
        "card_b_business_case_id": str(card_b.get("business_case_id") or ""),
        "card_b_step": int((detail_b.get("card") or {}).get("current_step") or 0),
        "card_b_handoff": str((detail_b.get("card") or {}).get("business_case_id") or ""),
        "other_key_missing": missing is None,
    }


def visibility_role_scoped_tasks(ctx: Ctx, **_) -> dict:
    """角色范围可见性：定向任务只有目标角色可见可领，无关角色两者皆不可。"""
    owner = ctx.user("sales_mgr", "it_owner_vis")
    pm = ctx.user("process_mgr", "it_pm_vis")
    fin = ctx.user("finance_mgr", "it_fin_vis")
    ctx.card("it-vis", owner)
    task = ctx.send("it-vis", owner, target_type="role", target_role_code="process_mgr")
    tid = task["task_id"]
    pm_inbox = [str(row.get("task_id")) for row in ctx.cpq_wf.inbox(pm)]
    fin_inbox = [str(row.get("task_id")) for row in ctx.cpq_wf.inbox(fin)]
    fin_denied = False
    try:
        ctx.cpq_wf.claim_task(tid, fin)
    except Exception:  # noqa: BLE001
        fin_denied = True
    claimed = ctx.cpq_wf.claim_task(tid, pm)
    return {
        "in_pm_inbox": tid in pm_inbox,
        "in_fin_inbox": tid in fin_inbox,
        "fin_denied": fin_denied,
        "pm_claimed": bool(claimed) and not claimed.get("already"),
        "status": str(ctx.one("SELECT status FROM cpq_wf_task WHERE task_id = %s", (tid,))[0]),
    }


def visibility_finance_and_sales_role_pools_isolated(ctx: Ctx, **_) -> dict:
    """财务角色池 / 销售角色池在真库上互不串池：各自的定向任务只进自己的收件箱。

    覆盖「财务角色池项目可读」「销售角色池项目可读」「不相关角色不可读」三条在任务层的
    等价物：项目 ACL 的可见性来自项目状态（plan.finance_handoff / 来源报价关联），对应
    到任务流就是 finance_mgr / sales_mgr 各自的角色池只收到自己的任务，跨池领取被拒且零副作用。
    """
    owner = ctx.user("sales_mgr", "it_owner_pool")
    fin = ctx.user("finance_mgr", "it_fin_pool")
    sales = ctx.user("sales_mgr", "it_sales_pool")
    ctx.card("it-pool-cost", owner, step=2)
    ctx.card("it-pool-quote", owner, step=2)
    cost = ctx.send("it-pool-cost", owner, target_type="role", target_role_code="finance_mgr")
    quote = ctx.send("it-pool-quote", owner, target_type="role", target_role_code="sales_mgr")
    cost_id, quote_id = cost["task_id"], quote["task_id"]
    fin_inbox = {str(row.get("task_id")) for row in ctx.cpq_wf.inbox(fin)}
    sales_inbox = {str(row.get("task_id")) for row in ctx.cpq_wf.inbox(sales)}
    cross_denied = 0
    for user, task in ((fin, quote_id), (sales, cost_id)):
        try:
            ctx.cpq_wf.claim_task(task, user)
        except Exception:  # noqa: BLE001
            cross_denied += 1
    fin_own = ctx.cpq_wf.claim_task(cost_id, fin)
    sales_own = ctx.cpq_wf.claim_task(quote_id, sales)
    return {
        "cost_in_fin_inbox": cost_id in fin_inbox,
        "cost_in_sales_inbox": cost_id in sales_inbox,
        "quote_in_sales_inbox": quote_id in sales_inbox,
        "quote_in_fin_inbox": quote_id in fin_inbox,
        "cross_pool_denied": cross_denied,
        "fin_claimed_own": bool(fin_own) and not fin_own.get("already"),
        "sales_claimed_own": bool(sales_own) and not sales_own.get("already"),
        "cross_claim_side_effects": ctx.count(
            "cpq_wf_task", "task_id IN (%s,%s) AND status = 'claimed' AND claimed_by_user_id IS NULL",
            (cost_id, quote_id)),
    }


SCHEMA_PARITY_TABLES = ("cpq_wf_task", "cpq_wf_task_event", "cpq_wf_handoff")
SCHEMA_PARITY_INDEXES = ("uq_wf_handoff_key", "uq_wf_task_open_kind")


def schema_catalog_parity(ctx: Ctx, **_) -> dict:
    """schema parity：读取**真实初始化函数**建出的 catalog，断言关键表 / 列 / 索引 / 约束。

    表由 `cpq_auth.init()` + `cpq_wf.init()` 建立（见 `init_schema`），不是测试另抄的一套
    DDL；这里只做只读盘点，供 `tests/test_cpq_eval_pg_schema.py` 断言与生产定义一致。
    """
    schema = str(ctx.cpq_auth.WF_SCHEMA)
    tables = {}
    for table in SCHEMA_PARITY_TABLES:
        cols = ctx.one(
            "SELECT jsonb_object_agg(column_name, jsonb_build_object("
            " 'type', data_type, 'notnull', is_nullable = 'NO',"
            " 'default', coalesce(column_default, '')))"
            " FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s", (schema, table))
        tables[table] = cols[0] if cols and cols[0] else None
    indexes = {}
    for name in SCHEMA_PARITY_INDEXES:
        row = ctx.one(
            "SELECT indexdef, indisunique FROM pg_indexes i"
            " JOIN pg_class c ON c.relname = i.indexname"
            " JOIN pg_index x ON x.indexrelid = c.oid"
            " WHERE i.schemaname = %s AND i.indexname = %s", (schema, name))
        indexes[name] = ({"indexdef": str(row[0]), "unique": bool(row[1])}
                         if row else None)
    fks = ctx.one(
        "SELECT jsonb_agg(jsonb_build_object("
        " 'name', con.conname, 'type', con.contype, 'def', pg_get_constraintdef(con.oid)))"
        " FROM pg_constraint con"
        " JOIN pg_class rel ON rel.oid = con.conrelid"
        " JOIN pg_namespace ns ON ns.oid = rel.relnamespace"
        " WHERE ns.nspname = %s AND rel.relname = ANY(%s)", (schema, list(SCHEMA_PARITY_TABLES)))
    return {
        "schema": schema,
        "tables": tables,
        "indexes": indexes,
        "constraints": fks[0] if fks and fks[0] else [],
    }


def task_db_rejects_duplicate_open(ctx: Ctx, **_) -> dict:
    """数据库是「同卡片同类型最多一条 open」的最终裁决者。

    绕过应用层复用逻辑，直接用原始 INSERT 再插一条 open：`uq_wf_task_open_kind` 必须
    把它挡下来。删除该部分唯一索引后这条案例必须变红。
    """
    owner = ctx.user("sales_mgr", "it_owner_dupdb")
    ctx.card("it-dupdb", owner)
    ctx.send("it-dupdb", owner, target_type="role", target_role_code="process_mgr")
    row = ctx.one("SELECT card_id FROM cpq_wf_card WHERE session_id = 'it-dupdb'")
    card_id = int(row[0])
    tid = int(ctx.cpq_auth.cpq_db.snow_next_id(ctx.conn()))
    conn = ctx.cpq_auth._connect()
    rejected, error = False, ""
    try:
        ctx.cpq_auth._exec(
            conn, "INSERT INTO cpq_wf_task (task_id, card_id, from_user_id, from_step_no,"
                  " target_type, target_role_code, status, source_label, created_at, task_kind)"
                  " VALUES (%s,%s,%s,1,'role','process_mgr','open','raw-dup',%s,'handoff')",
            (tid, card_id, int(owner["user_id"]), ctx.cpq_wf._ts(ctx.cpq_wf._now())))
    except Exception as exc:                    # noqa: BLE001 - 唯一冲突是预期结果
        rejected = True
        error = type(exc).__name__
    finally:
        conn.close()
    return {
        "raw_duplicate_rejected": rejected,
        "error_type": error,
        "open_rows": ctx.count("cpq_wf_task",
                               "card_id = %s AND task_kind = 'handoff' AND status = 'open'",
                               (card_id,)),
    }


def task_supersede_on_signature_change(ctx: Ctx, **_) -> dict:
    """同卡片同类型、签名不同（改了派发备注）：旧任务被取消并被新任务替代。

    应用层必须先用复用查询判重：同签名复用、签名不同替代。屏蔽该复用查询后，第二条
    会先撞唯一索引再错误地复用**旧**任务（丢掉了替代语义），这条案例变红。
    """
    owner = ctx.user("sales_mgr", "it_owner_sup")
    ctx.card("it-sup", owner)
    first = ctx.send("it-sup", owner, target_type="role", target_role_code="process_mgr", note="A")
    second = ctx.send("it-sup", owner, target_type="role", target_role_code="process_mgr", note="B")
    old = ctx.one("SELECT status, replaced_by_task_id FROM cpq_wf_task WHERE task_id = %s",
                  (int(first["task_id"]),))
    new = ctx.one("SELECT status, supersedes_task_id FROM cpq_wf_task WHERE task_id = %s",
                  (int(second["task_id"]),))
    return {
        "second_is_new": not bool(second.get("reused")),
        "same_task": first["task_id"] == second["task_id"],
        "new_supersedes_old": (str(new[1]) if new and new[1] is not None else "")
                              == first["task_id"],
        "old_status": str(old[0]) if old else "",
        "old_replaced_by_new": (str(old[1]) if old and old[1] is not None else "")
                               == second["task_id"],
        "open_rows": ctx.count("cpq_wf_task",
                               "card_id = (SELECT card_id FROM cpq_wf_card"
                               " WHERE session_id = 'it-sup') AND status = 'open'"),
    }


SCENARIOS = {
    "claim.concurrent_two_claimers_one_winner": {
        "fn": claim_concurrent_two_claimers, "kind": "concurrency",
        "modules": ["cpq_wf.claim_task"], "note": "原子 UPDATE + 部分唯一索引裁决"},
    "claim.role_denied_no_side_effect": {
        "fn": claim_role_denied_no_side_effect, "kind": "permission",
        "modules": ["cpq_wf.claim_task"], "note": "无权限不写任何行"},
    "handoff.concurrent_same_key_single_row": {
        "fn": handoff_concurrent_same_key, "kind": "concurrency",
        "modules": ["cpq_wf.insert_handoff_placeholder"], "note": "uq_wf_handoff_key 唯一约束"},
    "task.duplicate_submit_single_open_row": {
        "fn": task_duplicate_submit_single_open_row, "kind": "idempotent",
        "modules": ["cpq_wf.send_task"], "note": "同签名复用，不新增 open 行"},
    "tx.midway_failure_rolls_back_all": {
        "fn": tx_midway_failure_rolls_back_all, "kind": "transaction",
        "modules": ["cpq_wf.tx_connect", "cpq_wf.insert_handoff_placeholder"],
        "note": "非 autocommit 事务整体回滚"},
    "legacy.null_new_columns_readable": {
        "fn": legacy_null_new_columns_readable, "kind": "legacy",
        "modules": ["cpq_wf.get_card", "cpq_wf.task_detail"], "note": "缺列兼容读取"},
    "cross_instance.no_cross_handoff": {
        "fn": cross_instance_no_cross_handoff, "kind": "isolation",
        "modules": ["cpq_wf.find_handoff", "cpq_wf.card_detail"], "note": "业务实例不串单"},
    "visibility.role_scoped_tasks": {
        "fn": visibility_role_scoped_tasks, "kind": "permission",
        "modules": ["cpq_wf.inbox", "cpq_wf.claim_task"], "note": "角色范围可见性"},
    "visibility.finance_and_sales_role_pools_isolated": {
        "fn": visibility_finance_and_sales_role_pools_isolated, "kind": "permission",
        "modules": ["cpq_wf.inbox", "cpq_wf.claim_task"],
        "note": "财务/销售角色池互不串池，跨池领取被拒"},
    "task.db_rejects_duplicate_open": {
        "fn": task_db_rejects_duplicate_open, "kind": "constraint",
        "modules": ["cpq_auth.init", "cpq_wf.init"],
        "note": "uq_wf_task_open_kind 部分唯一索引是最终裁决者"},
    "task.supersede_on_signature_change": {
        "fn": task_supersede_on_signature_change, "kind": "idempotent",
        "modules": ["cpq_wf.send_task"], "note": "同签名复用 / 签名不同替代"},
    "schema.catalog_parity": {
        "fn": schema_catalog_parity, "kind": "schema",
        "modules": ["cpq_auth.init", "cpq_wf.init"],
        "note": "真实初始化函数的表 / 列 / 索引 / 约束 catalog 复盘"},
}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def run(scenario: str, args: dict, mutate: str = "") -> dict:
    cfg = _cfg()
    guard(cfg)
    payload = {"ok": False, "scenario": scenario, "db": cfg["child_db"], "trace": [],
               "queries": 0, "rows": {}, "mutate": mutate or ""}
    admin = None
    created = False
    try:
        admin = _connect_admin(cfg)
        create_temp_db(admin, cfg["child_db"])
        created = True
        payload["trace"].append(f"created temp db {cfg['child_db']}")
        payload["snow_fn"] = ensure_snow_fn(cfg)
        payload["bound"] = bound_db()
        payload["schema"] = init_schema()
        wanted = [name for name in str(mutate or "").split(",") if name.strip()]
        if wanted:
            admin_db = child_admin(cfg)
            try:
                for name in wanted:
                    apply_mutation(name.strip(), admin_db, payload["bound"]["schema"])
            finally:
                admin_db.close()
            payload["trace"].append(f"mutation applied: {mutate}")
        ctx = Ctx()
        payload["result"] = SCENARIOS[scenario]["fn"](ctx, **(args or {}))
        payload["queries"] = ctx.queries
        payload["trace"].extend(ctx.trace)
        payload["ok"] = True
    except Exception as exc:  # noqa: BLE001 - 任何异常都要如实回报
        payload["error"] = {"type": type(exc).__name__, "message": str(exc)[:400],
                            "trace": [line.strip()[:200] for line in
                                      traceback.format_exc().splitlines()[-14:]]}
    finally:
        cleanup = "not-created"
        if created and admin is not None:
            try:
                admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                              " WHERE datname = %s", (cfg["child_db"],))
                drop_temp_db(admin, cfg["child_db"])
                cleanup = "dropped"
            except Exception as exc:  # noqa: BLE001
                cleanup = f"{type(exc).__name__}: {str(exc)[:200]}"
        payload["cleanup"] = cleanup
        if admin is not None:
            try:
                admin.close()
            except Exception:  # pragma: no cover
                pass
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pg_scenarios")
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    parser.add_argument("--args", default="{}")
    parser.add_argument("--mutate", default="",
                        help="逗号分隔的故障注入名（见 MUTATIONS），留空 = 不注入")
    parser.add_argument("--list", action="store_true")
    opts = parser.parse_args(argv)
    if opts.list:
        print(json.dumps({name: {"kind": spec["kind"], "modules": spec["modules"],
                                 "note": spec["note"]} for name, spec in SCENARIOS.items()},
                         ensure_ascii=False, indent=2))
        return 0
    try:
        args = json.loads(opts.args or "{}")
    except ValueError:
        args = {}
    payload = run(opts.scenario, args if isinstance(args, dict) else {}, mutate=opts.mutate)
    sys.stdout.write(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
