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
import json
import os
import secrets
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

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
    # 工艺技术总监：技术工艺 3.2 报告审核 / 3.3 报告发布。没有它，CPQ 模式下只能把
    # 工艺经理升成全权（auth.enable_cpq_single_manager），3.1→3.2→3.3 的职责分离
    # 就不成立。角色只是本字典的一个取值（role_code 是 varchar(32)、无 CHECK 约束），
    # 所以新增它不需要任何 DDL / 迁移。
    "tech_director": "工艺技术总监",
    # 系统管理员：用户管理、角色授予、停用账号、重置口令（/auth/users 那一组）。
    # 账号数据统一到 PG 之后，"谁批角色申请"这一问必须有人回答，否则自助注册进来的
    # 只读账号永远升不了级。
    "admin": "系统管理员",
    # 自助注册的落地角色。注册只给只读，业务角色由管理员授予（见 /auth/register）。
    "viewer": "只读用户",
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
    # 增量列：老库上 CREATE TABLE IF NOT EXISTS 不会补列，必须显式 ALTER（幂等）。
    # requested_role = 注册时申请的业务角色；is_system = 历史归档账号（不可登录、
    # 不可改角色）。status 取值 active / disabled / archived，不加 CHECK 约束 ——
    # 角色与状态都是字典取值，加取值就不该改 schema。
    f"ALTER TABLE {WF_SCHEMA}.cpq_wf_user ADD COLUMN IF NOT EXISTS requested_role varchar(32)",
    f"ALTER TABLE {WF_SCHEMA}.cpq_wf_user ADD COLUMN IF NOT EXISTS is_system boolean NOT NULL DEFAULT false",
    # 账号级模型与密钥：密文存这里，明文只从内部通道/本人接口出。user_id 既是主键
    # 也是外键 —— 账号删了，设置跟着删，不留孤儿。
    f"""CREATE TABLE IF NOT EXISTS {WF_SCHEMA}.cpq_wf_user_llm_setting (
            user_id      bigint      PRIMARY KEY
                         REFERENCES {WF_SCHEMA}.cpq_wf_user(user_id) ON DELETE CASCADE,
            model_cipher text,
            keys_cipher  text,
            updated_at   timestamptz NOT NULL DEFAULT now()
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
              "email", "status", "last_login_at", "created_at",
              "requested_role", "is_system")


def _user_row(row) -> dict:
    if not row:
        return None
    d = dict(zip(_USER_COLS, row))
    d["user_id"] = str(d["user_id"])       # 雪花 ID 超 JS 安全整数，统一转字符串
    d["role_name"] = ROLES.get(d["role_code"], d["role_code"])
    d["requested_role"] = str(d.get("requested_role") or "")
    d["is_system"] = bool(d.get("is_system"))
    for k in ("last_login_at", "created_at"):
        v = d.get(k)
        d[k] = v.isoformat() if hasattr(v, "isoformat") else (v or None)
    return d


def _new_user_id(conn) -> int:
    return cpq_db.snow_next_id(conn)


def _uid(user_id) -> int:
    """出接口是字符串（雪花 ID 超 JS 安全整数），回库统一转 int。"""
    try:
        return int(str(user_id).strip())
    except (TypeError, ValueError):
        raise AuthError("用户不存在")


# ---------------------------------------------------------------------------
# 对外能力：注册 / 登录 / 登出 / 当前用户
# ---------------------------------------------------------------------------
def register(username: str, password: str, display_name: str,
             role_code: str, email: str = "", requested_role: str = "") -> dict:
    """注册写入 cpq_wf.cpq_wf_user。"""
    username = (username or "").strip()
    display_name = (display_name or "").strip() or username
    email = (email or "").strip()
    requested_role = (requested_role or "").strip()
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
                  " role_code, email, status, created_at, updated_at, requested_role, is_system)"
                  " VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,false)",
                  (uid, username, display_name, hash_password(password),
                   role_code, email or None, _ts(now), _ts(now), requested_role or None))
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


def list_users(role_code: str = "", status: str = "") -> list:
    """按角色 / 状态列出用户（用户管理页与「按角色发送任务」共用）。

    - role_code 为空 = 不按角色过滤；指定时只回该角色。
    - status 为空 = 沿用既有口径，只看启用中的账号（派发任务只发给能干活的人）；
      显式给 status（含 disabled / archived）才按它过滤 —— 停用与归档账号要能从
      用户管理里翻出来。
    """
    role_code = (role_code or "").strip()
    status = (status or "").strip()
    conn = _connect()
    try:
        sql = f"SELECT {', '.join(_USER_COLS)} FROM cpq_wf_user WHERE true"
        args: tuple = ()
        if status:
            sql += " AND status = %s"
            args = args + (status,)
        else:
            sql += " AND status = 'active'"
        if role_code:
            sql += " AND role_code = %s"
            args = args + (role_code,)
        sql += " ORDER BY created_at"
        cur = _exec(conn, sql, args)
        return [_user_row(r) for r in cur.fetchall()]
    finally:
        conn.close()


def find_user(username: str) -> Optional[dict]:
    """按登录名精确取一个账号（含停用/归档），不存在返回 None。"""
    username = (username or "").strip()
    if not username:
        return None
    conn = _connect()
    try:
        cur = _exec(conn, f"SELECT {', '.join(_USER_COLS)} FROM cpq_wf_user WHERE username = %s",
                    (username,))
        return _user_row(cur.fetchone())
    finally:
        conn.close()


def create_user(username: str, password: str, display_name: str, role_code: str,
                email: str = "", requested_role: str = "") -> dict:
    """管理员建号：角色直接授予，不走自助注册那条只读落地。"""
    username = (username or "").strip()
    if role_code not in ROLES:
        raise AuthError("请选择有效角色")
    return register(username, password, display_name, role_code, email,
                    requested_role=requested_role)


def update_user(user_id, patch: Optional[dict] = None) -> dict:
    """按 **user_id** 改一个账号（display_name / email / status / role_code / requested_role）。

    is_system=true 的历史归档账号不允许改角色（与既有"归档账号不能改角色"同一约束）。
    作用对象永远是调用方给的 user_id：路由层必须用路径里的那个，绝不能被请求体顶掉。
    """
    uid = _uid(user_id)
    patch = dict(patch or {})
    fields = ("display_name", "email", "status", "role_code", "requested_role")
    updates = {k: patch[k] for k in fields if k in patch}
    if not updates:
        raise AuthError("没有需要修改的字段")
    if "role_code" in updates and updates["role_code"] not in ROLES:
        raise AuthError("请选择有效角色")
    if "status" in updates and str(updates["status"]) not in ("active", "disabled", "archived"):
        raise AuthError("账号状态只能是 active / disabled / archived")
    with _lock:
        conn = _connect()
        try:
            cur = _exec(conn, f"SELECT {', '.join(_USER_COLS)} FROM cpq_wf_user WHERE user_id = %s", (uid,))
            row = _user_row(cur.fetchone())
            if not row:
                raise AuthError("用户不存在")
            if row.get("is_system") and "role_code" in updates \
                    and str(updates["role_code"]) != str(row.get("role_code")):
                raise AuthError("system 为历史项目归档账号，不能修改角色")
            sets, args = [], []
            for key, value in updates.items():
                value = str(value or "").strip()
                if key == "email":
                    value = value or None
                if key == "requested_role" and not value:
                    value = None
                sets.append(f"{key} = %s")
                args.append(value)
            sets.append("updated_at = %s")
            args.append(_ts(_now()))
            args.append(uid)
            _exec(conn, f"UPDATE cpq_wf_user SET {', '.join(sets)} WHERE user_id = %s", tuple(args))
            cur = _exec(conn, f"SELECT {', '.join(_USER_COLS)} FROM cpq_wf_user WHERE user_id = %s", (uid,))
            return _user_row(cur.fetchone())
        finally:
            conn.close()


def set_password(user_id, password: str) -> None:
    """管理员重置口令：不需要旧密码，但要过长度底线。"""
    uid = _uid(user_id)
    password = str(password or "")
    if len(password) < 6:
        raise AuthError("密码至少 6 位")
    with _lock:
        conn = _connect()
        try:
            cur = _exec(conn,
                        "UPDATE cpq_wf_user SET password_hash = %s, updated_at = %s WHERE user_id = %s",
                        (hash_password(password), _ts(_now()), uid))
            if not getattr(cur, "rowcount", 0):
                raise AuthError("用户不存在")
        finally:
            conn.close()


def change_password(user_id, current_password: str, new_password: str) -> None:
    """本人改口令：必须先验旧口令（错 → AuthError），新口令 ≥ 8 位。"""
    uid = _uid(user_id)
    current_password = str(current_password or "")
    new_password = str(new_password or "")
    if len(new_password) < 8:
        raise AuthError("新密码至少需要 8 位")
    with _lock:
        conn = _connect()
        try:
            cur = _exec(conn, "SELECT password_hash FROM cpq_wf_user WHERE user_id = %s", (uid,))
            row = cur.fetchone()
            if not row:
                raise AuthError("用户不存在")
            if not verify_password(current_password, row[0] or ""):
                raise AuthError("当前密码不正确")
            _exec(conn,
                  "UPDATE cpq_wf_user SET password_hash = %s, updated_at = %s WHERE user_id = %s",
                  (hash_password(new_password), _ts(_now()), uid))
        finally:
            conn.close()


def get_user_llm(user_id) -> dict:
    """账号级模型与密钥（**明文**，只供内部通道与本人接口）。

    没有记录 → {"model": "", "api_keys": {}}。密文解不开时明确抛错 —— 静默当成
    "没设置"会让人以为设置丢了，然后拿全局 Key 去跑。
    """
    import cpq_user_secrets

    uid = _uid(user_id)
    conn = _connect()
    try:
        cur = _exec(conn,
                    f"SELECT model_cipher, keys_cipher FROM {WF_SCHEMA}.cpq_wf_user_llm_setting"
                    " WHERE user_id = %s", (uid,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return {"model": "", "api_keys": {}}
    model = str(cpq_user_secrets.open(row[0]) or "").strip() if row[0] else ""
    keys: dict = {}
    if row[1]:
        try:
            raw = json.loads(cpq_user_secrets.open(row[1]) or "{}")
        except ValueError:
            raw = {}
        if isinstance(raw, dict):
            keys = {str(p): str(k) for p, k in raw.items() if str(k or "").strip()}
    return {"model": model, "api_keys": keys}


def set_user_llm(user_id, model=None, provider=None, key=None) -> dict:
    """写账号级模型 / 密钥。`None` = 该项不动；空串 = 清除；写库前一律 seal()。"""
    import cpq_user_secrets

    uid = _uid(user_id)
    with _lock:
        current = get_user_llm(uid)
        model_plain = str(current.get("model") or "")
        keys_plain = dict(current.get("api_keys") or {})
        if model is not None:
            model_plain = str(model or "").strip()
        if provider:
            name = str(provider).strip()
            value = str(key or "").strip()
            if value:
                keys_plain[name] = value
            else:
                keys_plain.pop(name, None)
        model_cipher = cpq_user_secrets.seal(model_plain) if model_plain else None
        keys_cipher = (cpq_user_secrets.seal(json.dumps(keys_plain, ensure_ascii=False))
                       if keys_plain else None)
        conn = _connect()
        try:
            if model_cipher is None and keys_cipher is None:
                _exec(conn,
                      f"DELETE FROM {WF_SCHEMA}.cpq_wf_user_llm_setting WHERE user_id = %s", (uid,))
            else:
                _exec(conn,
                      f"INSERT INTO {WF_SCHEMA}.cpq_wf_user_llm_setting"
                      " (user_id, model_cipher, keys_cipher, updated_at)"
                      " VALUES (%s,%s,%s,%s)"
                      " ON CONFLICT (user_id) DO UPDATE SET model_cipher = EXCLUDED.model_cipher,"
                      " keys_cipher = EXCLUDED.keys_cipher, updated_at = EXCLUDED.updated_at",
                      (uid, model_cipher, keys_cipher, _ts(_now())))
        finally:
            conn.close()
    return {"model": model_plain, "api_keys": keys_plain}


def delete_user_llm_key(user_id, provider: str) -> None:
    """删除账号在某个 provider 的个人 Key（provider 为空就什么都不做）。"""
    _uid(user_id)
    provider = str(provider or "").strip()
    if not provider:
        return
    set_user_llm(user_id, provider=provider, key="")


def bootstrap_admin() -> Optional[str]:
    """首个管理员引导：**只在用户表为空**且环境变量配好时才建号，否则只打印提示。

    自助注册收紧到 viewer 之后，"谁来当第一个管理员"必须有答案：先手工设
    CPQ_ADMIN_USER / CPQ_ADMIN_PASSWORD 再启动，本函数就会建出这个 admin。
    不自动建号、不让启动失败 —— 没配就打印一句可操作的提示。
    """
    conn = _connect()
    try:
        cur = _exec(conn, "SELECT 1 FROM cpq_wf_user LIMIT 1")
        empty = cur.fetchone() is None
    finally:
        conn.close()
    if not empty:
        return None
    username = (os.getenv("CPQ_ADMIN_USER") or "").strip()
    password = os.getenv("CPQ_ADMIN_PASSWORD") or ""
    weak = {"admin123", "123456", "12345678", "password", "admin", "admin1234"}
    if not username or len(password) < 8 or password in weak:
        print("[cpq-auth] 用户表为空：请在 .env 设置 CPQ_ADMIN_USER 与 CPQ_ADMIN_PASSWORD"
              "（≥8 位、非默认弱口令）后重启，即自动建出首个系统管理员；"
              "或由现有管理员在用户管理里建号。", file=sys.stderr)
        return None
    created = create_user(username, password, "系统管理员", "admin")
    print(f"[cpq-auth] 已按环境变量创建首个系统管理员：{created.get('username')}", file=sys.stderr)
    return str(created.get("username") or "")
