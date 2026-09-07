# -*- coding: utf-8 -*-
"""配置报价CPQ —— 登录与角色系统（数据访问层）。

**只用线上 Postgres，没有任何本地存储回落。** 连不上就抛 BackendUnavailable，
上层把 /auth/* 与 /wf/* 一律回 503——绝不把账号/卡片悄悄写到本机文件里。

按《报价工作流DA梳理.xlsx》实现，本模块负责其中登录相关的两张表：
  - cpq_wf_user           内置账号（含角色 role_code、口令哈希）
  - cpq_wf_login_session  登录会话令牌
其余 6 张（step_perm / card / card_step / task / task_event / message）见 cpq_wf.py，
共用本模块的连接层。

连接参数复用 cpq_db 的 CPQ_PG_* 环境变量；工作流表所在 schema 用 CPQ_WF_SCHEMA 覆盖
（默认 cpq_wf，与业务数据 schema master_data 解耦）。

口令只存 pbkdf2-sha256 加盐哈希（stdlib，无第三方依赖），绝不存明文、绝不出网。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

import cpq_db

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WF_SCHEMA = os.getenv("CPQ_WF_SCHEMA", "cpq_wf")

# 角色字典（DA 清单里角色表已按单角色模型取消，改为带取值约束的枚举）
ROLES = {
    "sales_mgr": "销售经理",
    "process_mgr": "工艺经理",
    # 成本测算归财务：工艺经理出工艺与用量，财务经理据此核算成本并对数字负责。
    # 技术工艺 2.3 是他的步骤（见 tech_app/backend/services/cpq_sso.py 的角色映射）。
    "finance_mgr": "财务经理",
}

SESSION_DAYS = int(os.getenv("CPQ_SESSION_DAYS", "7"))

_backend = None            # 'pg'；init() 成功后置位，None 表示未就绪
_backend_note = ""
_lock = threading.Lock()


class BackendUnavailable(RuntimeError):
    """连不上线上 Postgres。不回落、不静默，直接让上层报错。"""


class AuthError(Exception):
    """带用户可见文案的业务错误。"""


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
# 连接（仅 Postgres；autocommit，无需显式 commit）
# ---------------------------------------------------------------------------
def _connect():
    """连到业务库并把 search_path 指到 cpq_wf（可写事务）。"""
    try:
        import psycopg
    except ImportError as e:
        raise BackendUnavailable(
            "未安装 psycopg 驱动，无法访问线上 Postgres："
            "pip install \"psycopg[binary]==3.3.4\"") from e
    try:
        conn = psycopg.connect(
            host=cpq_db.PG_HOST, port=cpq_db.PG_PORT, user=cpq_db.PG_USER,
            password=cpq_db.PG_PASSWORD, dbname=cpq_db.PG_DATABASE,
            connect_timeout=cpq_db.PG_CONNECT_TIMEOUT, autocommit=True,
        )
    except Exception as e:
        raise BackendUnavailable(
            f"连接 Postgres {cpq_db.PG_HOST}:{cpq_db.PG_PORT}/{cpq_db.PG_DATABASE} 失败："
            f"{str(e).splitlines()[0][:160]}") from e
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('search_path', %s, false)", (f"{WF_SCHEMA}, public",))
    return conn


def _exec(conn, sql: str, args=()):
    """执行一条 SQL，返回游标（调用方按需 fetch）。"""
    cur = conn.cursor()
    cur.execute(sql, args)
    return cur


def _now():
    return datetime.now(timezone.utc)


def _ts(dt):
    """时间值直接给 timestamptz 用。"""
    return dt


def _commit(conn):
    """连接为 autocommit，这里留空以保持调用方写法统一。"""
    return None


# ---------------------------------------------------------------------------
# 建表（DDL，字段/类型/主外键严格对齐《报价工作流DA梳理.xlsx》）
# ---------------------------------------------------------------------------
_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {WF_SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS {WF_SCHEMA}.cpq_wf_user (
            user_id        bigint       PRIMARY KEY,
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
            user_id     bigint      NOT NULL
                        REFERENCES {WF_SCHEMA}.cpq_wf_user(user_id) ON DELETE CASCADE,
            issued_at   timestamptz NOT NULL DEFAULT now(),
            expires_at  timestamptz NOT NULL,
            ip          varchar(45)
        )""",
    f"CREATE INDEX IF NOT EXISTS idx_wf_session_user ON {WF_SCHEMA}.cpq_wf_login_session(user_id)",
]


def init() -> str:
    """建 schema 与登录相关两张表。连不上直接抛 BackendUnavailable。"""
    global _backend, _backend_note
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for sql in _DDL:
                cur.execute(sql)
    finally:
        conn.close()
    _backend = "pg"
    _backend_note = (f"Postgres {cpq_db.PG_HOST}:{cpq_db.PG_PORT}/"
                     f"{cpq_db.PG_DATABASE}（schema={WF_SCHEMA}）")
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
    return cpq_db.snow_next_id(conn)


# ---------------------------------------------------------------------------
# 对外能力：注册 / 登录 / 登出 / 当前用户
# ---------------------------------------------------------------------------
def register(username: str, password: str, display_name: str,
             role_code: str, email: str = "") -> dict:
    """注册写入 cpq_wf.cpq_wf_user。"""
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
            cur = _exec(conn, f"SELECT {', '.join(_USER_COLS)} FROM cpq_wf_user WHERE user_id = %s", (uid,))
            return _user_row(cur.fetchone())
        finally:
            conn.close()


def login(username: str, password: str, ip: str = "") -> dict:
    """校验口令并在 cpq_wf_login_session 里发一张会话票。"""
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
            return bool(getattr(cur, "rowcount", 0))
        finally:
            conn.close()


def whoami(token: str) -> dict:
    """按令牌取当前用户；无效/过期/后端未就绪一律返回 None（调用方按未登录处理）。"""
    if not token or _backend is None:
        return None
    try:
        conn = _connect()
    except BackendUnavailable:
        return None
    try:
        cur = _exec(conn,
                    "SELECT s.expires_at, " + ", ".join("u." + c for c in _USER_COLS) +
                    " FROM cpq_wf_login_session s JOIN cpq_wf_user u ON u.user_id = s.user_id"
                    " WHERE s.token = %s", (token,))
        row = cur.fetchone()
        if not row:
            return None
        exp = row[0]
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < _now():
            return None
        user = _user_row(row[1:])
        return user if (user or {}).get("status", "active") == "active" else None
    finally:
        conn.close()


def list_users(role_code: str = "") -> list:
    """按角色列出启用中的用户（供「按角色发送任务」使用）。"""
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
