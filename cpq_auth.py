# -*- coding: utf-8 -*-
"""配置报价CPQ —— 登录与角色系统（数据访问层）。

按《报价工作流DA梳理.xlsx》实现，本阶段只落地登录相关的两张表：
  - cpq_wf_user           内置账号（含角色 role_code、口令哈希）
  - cpq_wf_login_session  登录会话令牌

存储后端（双后端，自动选择）：
  1) **Postgres**（首选，按 DA 决策放在业务库的独立 schema `cpq_wf`，与业务数据解耦）
     连接参数复用 cpq_db 的 CPQ_PG_* 环境变量；schema 名可用 CPQ_WF_SCHEMA 覆盖。
  2) **SQLite**（回落）：本机连不上 Postgres / 未装 psycopg 时自动使用 cpq_auth.db，
     表结构与 Postgres 等价，保证本地开发与离线演示可用。启动时会打印实际后端。

口令只存 pbkdf2-sha256 加盐哈希（stdlib，无第三方依赖），绝不存明文、绝不出网。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import cpq_db

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(SCRIPT_DIR, "cpq_auth.db")
WF_SCHEMA = os.getenv("CPQ_WF_SCHEMA", "cpq_wf")

# 角色字典（DA 清单里角色表已按单角色模型取消，改为带取值约束的枚举）
ROLES = {
    "sales_mgr": "销售经理",
    "process_mgr": "工艺经理",
}

SESSION_DAYS = int(os.getenv("CPQ_SESSION_DAYS", "7"))

_backend = None            # 'pg' | 'sqlite'，init() 时确定
_backend_note = ""
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 口令哈希（pbkdf2-sha256）
# ---------------------------------------------------------------------------
_PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = (stored or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 连接（两种后端）
# ---------------------------------------------------------------------------
def _pg_connect():
    """连到业务库并把 search_path 指到 cpq_wf（可写事务）。"""
    import psycopg

    conn = psycopg.connect(
        host=cpq_db.PG_HOST, port=cpq_db.PG_PORT, user=cpq_db.PG_USER,
        password=cpq_db.PG_PASSWORD, dbname=cpq_db.PG_DATABASE,
        connect_timeout=cpq_db.PG_CONNECT_TIMEOUT, autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('search_path', %s, false)", (f"{WF_SCHEMA}, public",))
    return conn


def _sqlite_connect():
    conn = sqlite3.connect(SQLITE_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _connect():
    return _pg_connect() if _backend == "pg" else _sqlite_connect()


def _ph(sql: str) -> str:
    """占位符适配：内部统一写 %s，SQLite 下换成 ?。"""
    return sql if _backend == "pg" else sql.replace("%s", "?")


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 建表（DDL）
# ---------------------------------------------------------------------------
_DDL_PG = [
    f"CREATE SCHEMA IF NOT EXISTS {WF_SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS {WF_SCHEMA}.cpq_wf_user (
            user_id        bigint PRIMARY KEY,
            username       varchar(64)  NOT NULL UNIQUE,
            display_name   varchar(64)  NOT NULL,
            password_hash  varchar(255) NOT NULL,
            role_code      varchar(32)  NOT NULL,
            email          varchar(128),
            status         varchar(16)  NOT NULL DEFAULT 'active',
            last_login_at  timestamptz,
            created_at     timestamptz  NOT NULL DEFAULT now(),
            updated_at     timestamptz  NOT NULL DEFAULT now()
        )""",
    f"""CREATE TABLE IF NOT EXISTS {WF_SCHEMA}.cpq_wf_login_session (
            token       varchar(64) PRIMARY KEY,
            user_id     bigint      NOT NULL REFERENCES {WF_SCHEMA}.cpq_wf_user(user_id) ON DELETE CASCADE,
            issued_at   timestamptz NOT NULL DEFAULT now(),
            expires_at  timestamptz NOT NULL,
            ip          varchar(45)
        )""",
    f"CREATE INDEX IF NOT EXISTS idx_wf_session_user ON {WF_SCHEMA}.cpq_wf_login_session(user_id)",
]

_DDL_SQLITE = [
    """CREATE TABLE IF NOT EXISTS cpq_wf_user (
            user_id        INTEGER PRIMARY KEY,
            username       TEXT NOT NULL UNIQUE,
            display_name   TEXT NOT NULL,
            password_hash  TEXT NOT NULL,
            role_code      TEXT NOT NULL,
            email          TEXT,
            status         TEXT NOT NULL DEFAULT 'active',
            last_login_at  TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        )""",
    """CREATE TABLE IF NOT EXISTS cpq_wf_login_session (
            token       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            issued_at   TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            ip          TEXT
        )""",
    "CREATE INDEX IF NOT EXISTS idx_wf_session_user ON cpq_wf_login_session(user_id)",
]


def init() -> str:
    """确定后端并建表。返回后端说明（用于启动日志）。失败不抛异常，返回错误说明。"""
    global _backend, _backend_note
    # 先试 Postgres（按 DA 决策的首选存储）
    try:
        import psycopg  # noqa: F401
        _backend = "pg"
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                for sql in _DDL_PG:
                    cur.execute(sql)
        finally:
            conn.close()
        _backend_note = (f"Postgres {cpq_db.PG_HOST}:{cpq_db.PG_PORT}/"
                         f"{cpq_db.PG_DATABASE}（schema={WF_SCHEMA}）")
        return _backend_note
    except Exception as e:
        pg_err = str(e).splitlines()[0][:120] if str(e) else e.__class__.__name__

    # 回落 SQLite（本机无驱动/连不上库时仍可登录，表结构等价）
    _backend = "sqlite"
    conn = _sqlite_connect()
    try:
        for sql in _DDL_SQLITE:
            conn.execute(sql)
        conn.commit()
    finally:
        conn.close()
    _backend_note = f"SQLite {os.path.basename(SQLITE_PATH)}（Postgres 不可用：{pg_err}）"
    return _backend_note


def backend_info() -> dict:
    return {"backend": _backend, "detail": _backend_note}


# ---------------------------------------------------------------------------
# 内部：行 -> dict
# ---------------------------------------------------------------------------
_USER_COLS = ("user_id", "username", "display_name", "role_code",
              "email", "status", "last_login_at", "created_at")


def _user_row(row) -> dict:
    if not row:
        return None
    d = dict(zip(_USER_COLS, row))
    d["user_id"] = str(d["user_id"])       # 雪花 ID 超 JS 安全整数，统一转字符串
    d["role_name"] = ROLES.get(d["role_code"], d["role_code"])
    for k in ("last_login_at", "created_at"):
        v = d.get(k)
        d[k] = v.isoformat() if hasattr(v, "isoformat") else (v or None)
    return d


def _new_user_id(conn) -> int:
    if _backend == "pg":
        return cpq_db.snow_next_id(conn)
    return cpq_db._client_snow_id()


def _ts(dt):
    """时间值按后端存：PG 存 datetime，SQLite 存 ISO 文本。"""
    return dt if _backend == "pg" else dt.isoformat()


def _exec(conn, sql, args=()):
    if _backend == "pg":
        cur = conn.cursor()
        cur.execute(_ph(sql), args)
        return cur
    cur = conn.execute(_ph(sql), args)
    return cur


# ---------------------------------------------------------------------------
# 对外能力：注册 / 登录 / 登出 / 当前用户
# ---------------------------------------------------------------------------
class AuthError(Exception):
    """带用户可见文案的业务错误。"""


def register(username: str, password: str, display_name: str,
             role_code: str, email: str = "") -> dict:
    username = (username or "").strip()
    display_name = (display_name or "").strip() or username
    email = (email or "").strip()
    if not username or len(username) < 2:
        raise AuthError("登录名至少 2 个字符")
    if not password or len(password) < 6:
        raise AuthError("密码至少 6 位")
    if role_code not in ROLES:
        raise AuthError("请选择有效角色")

    with _lock:
        conn = _connect()
        try:
            cur = _exec(conn, "SELECT 1 FROM cpq_wf_user WHERE username = %s", (username,))
            if cur.fetchone():
                raise AuthError(f"登录名「{username}」已被占用")
            uid = _new_user_id(conn)
            now = _now()
            _exec(conn,
                  "INSERT INTO cpq_wf_user (user_id, username, display_name, password_hash,"
                  " role_code, email, status, created_at, updated_at)"
                  " VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s)",
                  (uid, username, display_name, hash_password(password),
                   role_code, email or None, _ts(now), _ts(now)))
            if _backend == "sqlite":
                conn.commit()
            cur = _exec(conn, f"SELECT {', '.join(_USER_COLS)} FROM cpq_wf_user WHERE user_id = %s", (uid,))
            return _user_row(cur.fetchone())
        finally:
            conn.close()


def login(username: str, password: str, ip: str = "") -> dict:
    username = (username or "").strip()
    with _lock:
        conn = _connect()
        try:
            cur = _exec(conn,
                        "SELECT user_id, password_hash, status FROM cpq_wf_user WHERE username = %s",
                        (username,))
            row = cur.fetchone()
            # 用户不存在与口令错误返回同一文案，避免枚举账号
            if not row or not verify_password(password, row[1]):
                raise AuthError("登录名或密码不正确")
            if (row[2] or "active") != "active":
                raise AuthError("该账号已停用，请联系管理员")
            uid = row[0]
            now = _now()
            token = secrets.token_urlsafe(32)[:64]
            _exec(conn,
                  "INSERT INTO cpq_wf_login_session (token, user_id, issued_at, expires_at, ip)"
                  " VALUES (%s,%s,%s,%s,%s)",
                  (token, uid, _ts(now), _ts(now + timedelta(days=SESSION_DAYS)), ip or None))
            _exec(conn, "UPDATE cpq_wf_user SET last_login_at = %s, updated_at = %s WHERE user_id = %s",
                  (_ts(now), _ts(now), uid))
            # 顺手清理该用户已过期的会话，避免表无限增长
            _exec(conn, "DELETE FROM cpq_wf_login_session WHERE user_id = %s AND expires_at < %s",
                  (uid, _ts(now)))
            if _backend == "sqlite":
                conn.commit()
            cur = _exec(conn, f"SELECT {', '.join(_USER_COLS)} FROM cpq_wf_user WHERE user_id = %s", (uid,))
            return {"token": token, "user": _user_row(cur.fetchone())}
        finally:
            conn.close()


def logout(token: str) -> bool:
    if not token:
        return False
    with _lock:
        conn = _connect()
        try:
            cur = _exec(conn, "DELETE FROM cpq_wf_login_session WHERE token = %s", (token,))
            if _backend == "sqlite":
                conn.commit()
            return bool(getattr(cur, "rowcount", 0))
        finally:
            conn.close()


def whoami(token: str) -> dict:
    """按令牌取当前用户；无效/过期返回 None。"""
    if not token:
        return None
    conn = _connect()
    try:
        cur = _exec(conn,
                    "SELECT s.expires_at, " + ", ".join("u." + c for c in _USER_COLS) +
                    " FROM cpq_wf_login_session s JOIN cpq_wf_user u ON u.user_id = s.user_id"
                    " WHERE s.token = %s", (token,))
        row = cur.fetchone()
        if not row:
            return None
        exp = row[0]
        if not hasattr(exp, "isoformat"):  # SQLite 存的是 ISO 文本
            try:
                exp = datetime.fromisoformat(str(exp))
            except ValueError:
                return None
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < _now():
            return None
        user = _user_row(row[1:])
        return user if (user or {}).get("status", "active") == "active" else None
    finally:
        conn.close()


def list_users(role_code: str = "") -> list:
    """按角色列出启用中的用户（供后续「按角色发送任务」使用）。"""
    conn = _connect()
    try:
        sql = f"SELECT {', '.join(_USER_COLS)} FROM cpq_wf_user WHERE status = 'active'"
        args = ()
        if role_code:
            sql += " AND role_code = %s"
            args = (role_code,)
        sql += " ORDER BY created_at"
        cur = _exec(conn, sql, args)
        return [_user_row(r) for r in cur.fetchall()]
    finally:
        conn.close()
