"""用户数据统一维护在 Postgres（CPQ 为唯一权威）：Spec / Red。

契约见 docs/specs/user-data-unified-in-pg.md。今天的事实是反的：

  · 用户数据分裂两处：CPQ 在 PG `cpq_wf.cpq_wf_user`（cpq_auth.py:135-146），技术工艺在
    本地 JSON `DATA_DIR/_auth_users.json`（tech_app/backend/storage/meta_backend.py:124-141）；
  · CPQ 侧没有任何用户管理能力（不能改角色 / 改显示名 / 改密码 / 停用账号），角色字典里
    也没有 admin（cpq_auth.py:29-41），而技术工艺的角色授予只有 admin 能做；
  · 账号级模型与 API Key 明文落在 `DATA_DIR/_user_llm.json`（services/user_llm.py），
    与账号主数据不在同一处；
  · 技术工艺仍能直连本地用户文件，`AUTH_ENABLED=true` + `CPQ_SSO=false` 仍是一条"看起来
    能用"的独立登录路径。

本批建立的契约（摘要）：
  C2 角色字典增 admin / viewer，既有映射不动；
  C3 `cpq_wf_user` 增 requested_role / is_system，新增 `cpq_wf_user_llm_setting` 密文表；
  C4 用户主键口径 = user_id（用户管理端点按 user_id 定位）；
  C5 admin 专用的建号 / 改角色 / 停用 / 重置口令接口；
  C6 本人接口（改资料 / 改密码 / 账号级模型与密钥），请求体里的 user_id 一律忽略；
  C7 自助注册一律落地 viewer，role_code=admin 必须 400；
  C8 账号级密钥 AEAD 加密，密钥材料来自 CPQ_USER_SECRET_KEY；
  C9 服务间内部通道（X-Internal-Token）供后台任务读账号级设置；
  C10 技术工艺新增 cpq_auth_client（只走 HTTP，不直连 PG）；
  C11 技术工艺 HTTP 面：/api/users* 409 指向 CPQ、/api/me 增 user_id、/api/my/settings 转发；
  C12 本地用户表退役 + 启动守卫；
  C13 迁移脚本（口令散列无损转换、默认 dry-run、不删原文件）；
  C14 依赖 cryptography 与新增环境变量。

验证方式：
  A. 子进程真起一体化服务的 HTTP 面（端口 0，假 cpq_auth 存储函数），打 /auth/* 的真请求；
  B. 子进程真起技术工艺 App（TestClient + 假 cpq_sso.resolve + 假 cpq_auth_client），
     读 /api/users、/api/me、/api/my/settings 的行为；
  C. 子进程真跑加密模块与迁移脚本的散列转换；
  D. 源码契约（表结构、角色字典、内部通道、退役标记、依赖）。
"""
from __future__ import annotations

import functools
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]

SPEC = ROOT / "docs" / "specs" / "user-data-unified-in-pg.md"
CPQ_AUTH_PY = ROOT / "cpq_auth.py"
CPQ_SUITE_PY = ROOT / "cpq_suite_server.py"
CPQ_SECRETS_PY = ROOT / "cpq_user_secrets.py"
CPQ_SSO_PY = ROOT / "tech_app" / "backend" / "services" / "cpq_sso.py"
CPQ_AUTH_CLIENT_PY = ROOT / "tech_app" / "backend" / "services" / "cpq_auth_client.py"
USER_LLM_PY = ROOT / "tech_app" / "backend" / "services" / "user_llm.py"
TECH_MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
META_BACKEND_PY = ROOT / "tech_app" / "backend" / "storage" / "meta_backend.py"
MIGRATE_PY = ROOT / "scripts" / "migrate_users_to_pg.py"
REQUIREMENTS = ROOT / "requirements.txt"

ADMIN_TOKEN = "admin-token-1"
MGR_TOKEN = "mgr-token-1"
VIEWER_TOKEN = "viewer-token-1"
INTERNAL_TOKEN = "internal-token-abc"
PLAIN_ACCOUNT_KEY = "ACCOUNT-QWEN-KEY-0002"
ACCOUNT_MODEL = "deepseek-v4-pro"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def body_of(src: str, name: str) -> str:
    """取某个顶层函数/方法的函数体（到下一个顶层 def/class 为止）。"""
    idx = src.find("def %s(" % name)
    if idx < 0:
        return ""
    rest = src[idx:]
    end = len(rest)
    for marker in ("\ndef ", "\nasync def ", "\n@app.", "\nclass "):
        at = rest.find(marker, 1)
        if at != -1:
            end = min(end, at)
    return rest[:end]


def child_python() -> str:
    """能同时 import fastapi/httpx/cpq_suite_server 的解释器（本机为 open-claude/.venv）。"""
    candidates = [str(ROOT / "open-claude" / ".venv" / "bin" / "python"),
                  sys.executable, shutil.which("python3"), shutil.which("python")]
    probe_code = ("import sys; sys.path.insert(0, %r); "
                  "import fastapi, httpx, cpq_suite_server" % str(ROOT))
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", probe_code], cwd=str(ROOT),
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return ""


def run_child(source: str, *args: str, timeout: int = 300) -> dict:
    """把走查脚本落到临时目录里，用带依赖的解释器在仓库根跑一次，取最后一行 JSON。"""
    python = child_python()
    if not python:
        raise unittest.SkipTest("没有能 import fastapi/httpx/cpq_suite_server 的解释器")
    script_dir = pathlib.Path(tempfile.mkdtemp(prefix="cpq-pg-users-script-"))
    script = script_dir / "child.py"
    script.write_text(source, encoding="utf-8")
    completed = subprocess.run([python, str(script), *args], cwd=str(ROOT),
                               capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise AssertionError("走查子进程失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                             % (completed.returncode, completed.stdout[-3000:],
                                completed.stderr[-4000:]))
    return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# A. 一体化服务 /auth/*：真 HTTP（端口 0，假存储函数）
# --------------------------------------------------------------------------- #
CPQ_CHILD = r'''
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

root = sys.argv[1]
sys.path.insert(0, root)
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="cpq-pg-users-")

import cpq_suite_server as suite

INTERNAL_TOKEN = "internal-token-abc"
os.environ["CPQ_INTERNAL_TOKEN"] = INTERNAL_TOKEN
suite.CPQ_INTERNAL_TOKEN = INTERNAL_TOKEN

CALLS = []

ALL_FIELDS = ("user_id", "username", "display_name", "role_code", "role_name", "email",
              "status", "requested_role", "is_system", "last_login_at", "created_at")


def _row(user_id, username, role_code, role_name, **extra):
    row = {"user_id": str(user_id), "username": username, "display_name": username,
           "role_code": role_code, "role_name": role_name, "email": username + "@x.com",
           "status": "active", "requested_role": "viewer", "is_system": False,
           "last_login_at": "2026-09-16T09:00:00+00:00", "created_at": "2026-09-01T09:00:00+00:00"}
    row.update(extra)
    return row


USERS = {
    "admin-token-1": _row(1, "root", "admin", "系统管理员"),
    "mgr-token-1": _row(7, "mgr", "process_mgr", "工艺经理"),
    "viewer-token-1": _row(9, "look", "sales_mgr", "销售经理"),
}
LLM_ROWS = {}


def whoami(token):
    return USERS.get((token or "").strip())


def list_users(role_code="", status=""):
    CALLS.append(("list_users", role_code, status))
    return [dict(row) for row in USERS.values()]


def create_user(username="", password="", display_name="", role_code="",
                email="", requested_role="", **kw):
    CALLS.append(("create_user", username, role_code, email, requested_role))
    return _row(21, username or "new", role_code or "viewer",
                {"admin": "系统管理员", "viewer": "只读用户"}.get(role_code or "viewer", role_code or "viewer"),
                requested_role=requested_role or "")


def update_user(user_id, patch=None, **kw):
    patch = dict(patch or {})
    patch.update(kw)
    CALLS.append(("update_user", str(user_id), patch))
    row = _row(user_id, patch.get("username") or "u%s" % user_id, patch.get("role_code") or "viewer", "只读用户")
    row.update({k: v for k, v in patch.items() if k in ALL_FIELDS})
    return row


def set_password(user_id, password, **kw):
    CALLS.append(("set_password", str(user_id), bool(password)))


def change_password(user_id, current_password="", new_password="", **kw):
    CALLS.append(("change_password", str(user_id), current_password, new_password))
    if current_password != "old-secret":
        raise suite.cpq_auth.AuthError("当前密码不正确")


def register(username="", password="", display_name="", role_code="", email="",
             requested_role="", **kw):
    CALLS.append(("register", username, role_code, requested_role))
    return _row(31, username or "new", role_code or "", "只读用户", requested_role=requested_role or "")


def login(username="", password="", ip="", **kw):
    CALLS.append(("login", username))
    return {"token": "fresh-token", "user": USERS["viewer-token-1"]}


def get_user_llm(user_id, **kw):
    CALLS.append(("get_user_llm", str(user_id)))
    return dict(LLM_ROWS.get(str(user_id)) or {"model": "", "api_keys": {}})


def set_user_llm(user_id, model=None, provider=None, key=None, **kw):
    CALLS.append(("set_user_llm", str(user_id), model, provider, key))
    entry = dict(LLM_ROWS.get(str(user_id)) or {"model": "", "api_keys": {}})
    if model is not None:
        entry["model"] = model
    if provider:
        keys = dict(entry.get("api_keys") or {})
        if key:
            keys[provider] = key
        else:
            keys.pop(provider, None)
        entry["api_keys"] = keys
    LLM_ROWS[str(user_id)] = entry
    return dict(entry)


def delete_user_llm_key(user_id, provider="", **kw):
    CALLS.append(("delete_user_llm_key", str(user_id), provider))


def list_roles(role_code=""):
    return list(suite.cpq_auth.ROLES.items())


suite.cpq_auth.whoami = whoami
suite.cpq_auth.list_users = list_users
suite.cpq_auth.create_user = create_user
suite.cpq_auth.update_user = update_user
suite.cpq_auth.set_password = set_password
suite.cpq_auth.change_password = change_password
suite.cpq_auth.register = register
suite.cpq_auth.login = login
suite.cpq_auth.get_user_llm = get_user_llm
suite.cpq_auth.set_user_llm = set_user_llm
suite.cpq_auth.delete_user_llm_key = delete_user_llm_key
suite.cpq_auth._backend = "pg"

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), suite.Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

out = {"port": port}


def call(method, path, token=None, payload=None, internal=None):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    if internal:
        request.add_header("X-Internal-Token", internal)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", "replace")
            return {"status": response.status, "text": raw[:1500]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return {"status": exc.code, "text": raw[:1500]}
    except Exception as exc:      # noqa: BLE001
        return {"status": -1, "text": "%s: %s" % (type(exc).__name__, exc)}


def keys_of(result):
    try:
        users = (json.loads(result.get("text") or "{}") or {}).get("users") or []
    except Exception:  # noqa: BLE001
        return []
    return sorted({k for row in users if isinstance(row, dict) for k in row})


# --- 角色字典 ---
out["roles"] = call("GET", "/auth/roles")
out["role_codes"] = sorted(suite.cpq_auth.ROLES)

# --- 列表：未登录 / 非 admin / admin ---
out["users_anon"] = call("GET", "/auth/users")
out["users_viewer"] = call("GET", "/auth/users", token="viewer-token-1")
out["users_viewer_keys"] = keys_of(out["users_viewer"])
out["users_mgr"] = call("GET", "/auth/users", token="mgr-token-1")
out["users_mgr_keys"] = keys_of(out["users_mgr"])
out["users_admin"] = call("GET", "/auth/users", token="admin-token-1")
out["users_admin_keys"] = keys_of(out["users_admin"])
out["users_role_filter"] = call("GET", "/auth/users?role=process_mgr", token="admin-token-1")

# --- 建号 / 改角色 / 停用 / 重置口令 ---
CALLS.clear()
out["create_anon"] = call("POST", "/auth/users", payload={"username": "x2", "password": "secret123"})
out["create_viewer"] = call("POST", "/auth/users", token="viewer-token-1",
                            payload={"username": "x2", "password": "secret123"})
out["create_admin"] = call("POST", "/auth/users", token="admin-token-1",
                           payload={"username": "x2", "password": "secret123",
                                    "display_name": "X2", "role_code": "process_mgr"})
out["create_calls"] = [list(c) for c in CALLS]

CALLS.clear()
out["patch_viewer"] = call("PUT", "/auth/users/9", token="viewer-token-1",
                           payload={"display_name": "hi"})
out["patch_admin"] = call("PUT", "/auth/users/9", token="admin-token-1",
                          payload={"display_name": "hi", "user_id": "1", "role_code": "viewer",
                                   "status": "disabled", "requested_role": "viewer"})
out["patch_calls"] = [list(c) for c in CALLS]

CALLS.clear()
out["password_viewer"] = call("PUT", "/auth/users/9/password", token="viewer-token-1",
                              payload={"password": "secret123"})
out["password_admin"] = call("PUT", "/auth/users/9/password", token="admin-token-1",
                             payload={"password": "secret123"})
out["password_calls"] = [list(c) for c in CALLS]

# --- 本人接口：body 里的 user_id 一律忽略 ---
CALLS.clear()
out["my_profile"] = call("PUT", "/auth/my/profile", token="mgr-token-1",
                         payload={"display_name": "新名字", "user_id": "1", "username": "root"})
out["my_password"] = call("PUT", "/auth/my/password", token="mgr-token-1",
                          payload={"current_password": "old-secret", "new_password": "new-secret-1",
                                   "user_id": "1"})
out["my_password_wrong"] = call("PUT", "/auth/my/password", token="mgr-token-1",
                                payload={"current_password": "nope", "new_password": "new-secret-1"})
out["my_calls"] = [list(c) for c in CALLS]

# --- 账号级模型与密钥（本人） ---
CALLS.clear()
out["llm_anon"] = call("GET", "/auth/my/llm")
out["llm_get"] = call("GET", "/auth/my/llm", token="mgr-token-1")
out["llm_put"] = call("PUT", "/auth/my/llm", token="mgr-token-1",
                      payload={"model": "deepseek-v4-pro", "user_id": "1"})
out["llm_put_key"] = call("PUT", "/auth/my/llm", token="mgr-token-1",
                          payload={"api_key": "ACCOUNT-QWEN-KEY-0002", "api_key_provider": "qwen",
                                   "username": "root"})
out["llm_get_after"] = call("GET", "/auth/my/llm", token="mgr-token-1")
out["llm_delete"] = call("DELETE", "/auth/my/llm/keys/qwen", token="mgr-token-1")
out["llm_calls"] = [list(c) for c in CALLS]
out["llm_other_account"] = call("GET", "/auth/my/llm", token="viewer-token-1")

# --- 内部通道 ---
CALLS.clear()
out["internal_anon"] = call("GET", "/auth/internal/user-llm?username=mgr")
out["internal_bad"] = call("GET", "/auth/internal/user-llm?username=mgr", internal="wrong-token")
out["internal_ok"] = call("GET", "/auth/internal/user-llm?username=mgr", internal=INTERNAL_TOKEN)
out["internal_put"] = call("PUT", "/auth/internal/user-llm", internal=INTERNAL_TOKEN,
                           payload={"username": "mgr", "api_key": "ACCOUNT-QWEN-KEY-0002",
                                    "api_key_provider": "qwen"})
out["internal_calls"] = [list(c) for c in CALLS]

# --- 自助注册收紧 ---
CALLS.clear()
out["register_admin"] = call("POST", "/auth/register",
                             payload={"username": "self1", "password": "secret123",
                                      "display_name": "Self", "role_code": "admin"})
out["register_requested_admin"] = call("POST", "/auth/register",
                                       payload={"username": "self2", "password": "secret123",
                                                "display_name": "Self", "role_code": "viewer",
                                                "requested_role": "admin"})
out["register_ok"] = call("POST", "/auth/register",
                          payload={"username": "self3", "password": "secret123",
                                   "display_name": "Self", "requested_role": "process_mgr"})
out["register_calls"] = [list(c) for c in CALLS]

# --- 后端不可用：不能装作能用 ---
suite.cpq_auth._backend = None
out["backend_down_users"] = call("GET", "/auth/users", token="admin-token-1")
out["backend_down_llm"] = call("GET", "/auth/my/llm", token="admin-token-1")
suite.cpq_auth._backend = "pg"

server.shutdown()
print(json.dumps(out, ensure_ascii=False, default=str))
'''


@functools.lru_cache(maxsize=None)
def cpq_probe() -> dict:
    return run_child(CPQ_CHILD, str(ROOT))


# --------------------------------------------------------------------------- #
# B. 技术工艺 HTTP 面：TestClient（假 cpq_sso + 假 cpq_auth_client）
# --------------------------------------------------------------------------- #
TECH_CHILD = r'''
import json
import os
import sys
import tempfile
import types

root = sys.argv[1]
sys.path.insert(0, root)
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tech-pg-users-")
os.environ["AUTH_ENABLED"] = "false"
os.environ["CPQ_SSO"] = "true"
os.environ["CPQ_AUTH_BASE_URL"] = "http://127.0.0.1:9"
os.environ["CPQ_INTERNAL_TOKEN"] = "internal-token-abc"

try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

CALLS = []

from tech_app.backend.services import cpq_sso as sso

ME = {
    "username": "mgr",
    "role": "admin",
    "display_name": "工艺经理",
    "requested_role": "admin",
    "is_system": False,
    "source": "cpq",
    "cpq_role_code": "admin",
    "cpq_role_name": "系统管理员",
    "cpq_user_id": "7",
}
sso.resolve = lambda token: dict(ME) if (token or "").strip() == "tech-token" else None
sso.invalidate = lambda token="": None

# 假客户端：必须在 tech_app 后端 import 之前装进 sys.modules（user_llm 会 from . import）
client = types.ModuleType("tech_app.backend.services.cpq_auth_client")


class CpqAuthUnavailable(RuntimeError):
    pass


client.CpqAuthUnavailable = CpqAuthUnavailable


def _record(name):
    def fn(*args, **kwargs):
        CALLS.append([name, *[str(a) for a in args]])
        if name == "user_overrides":
            return {"model": "deepseek-v4-pro", "api_keys": {"qwen": "ACCOUNT-QWEN-KEY-0002"}}
        if name == "summary":
            return {"model": "deepseek-v4-pro", "has_model": True,
                    "keys": {"qwen": {"configured": True, "hint": "ACCOUNT…0002"}}}
        return {"ok": True}
    return fn


for _name in ("set_request_token", "user_overrides", "summary", "set_model", "set_key",
              "update_profile", "change_password", "invalidate"):
    setattr(client, _name, _record(_name))
sys.modules["tech_app.backend.services.cpq_auth_client"] = client

from starlette.testclient import TestClient
import tech_app.backend.main as main

# user_llm 可能已经绑定了另一个对象，统一改成我们的假客户端
from tech_app.backend.services import user_llm
if hasattr(user_llm, "cpq_auth_client"):
    user_llm.cpq_auth_client = client

app_client = TestClient(main.app, raise_server_exceptions=False)
HEADERS = {"Authorization": "Bearer tech-token"}

out = {}


def http(method, path, payload=None, headers=HEADERS):
    response = app_client.request(method, path, json=payload, headers=headers) \
        if payload is not None else app_client.request(method, path, headers=headers)
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        body = response.text[:800]
    return {"status": response.status_code, "text": response.text[:1200], "body": body}


out["me"] = http("GET", "/api/me")
out["users"] = http("GET", "/api/users")
out["users_role"] = http("PUT", "/api/users/mgr/role", {"role": "viewer"})
out["login"] = http("POST", "/api/login", {"username": "mgr", "password": "x"})
out["register"] = http("POST", "/api/register",
                       {"username": "self1", "password": "secret123", "display_name": "S"})

CALLS.clear()
out["my_settings_get"] = http("GET", "/api/my/settings")
out["my_settings_get_calls"] = [list(c) for c in CALLS]

CALLS.clear()
out["my_settings_put_model"] = http("PUT", "/api/my/settings", {"model": "qwen3.8-max"})
out["my_settings_put_calls"] = [list(c) for c in CALLS]

CALLS.clear()
out["my_settings_put_key"] = http("PUT", "/api/my/settings",
                                  {"api_key": "ACCOUNT-QWEN-KEY-0002", "api_key_provider": "qwen"})
out["my_settings_key_calls"] = [list(c) for c in CALLS]

CALLS.clear()
out["my_settings_delete_key"] = http("DELETE", "/api/my/settings/keys/qwen")
out["my_settings_delete_calls"] = [list(c) for c in CALLS]

CALLS.clear()
out["my_profile"] = http("PUT", "/api/me", {"display_name": "新名字"})
out["my_profile_calls"] = [list(c) for c in CALLS]

CALLS.clear()
out["my_password"] = http("PUT", "/api/me", {"current_password": "old", "new_password": "newpass123"})
out["my_password_calls"] = [list(c) for c in CALLS]

# 没有票：任何 /api 都要 401，且不得回本地用户数据
out["users_no_token"] = http("GET", "/api/users", headers={})
out["my_settings_no_token"] = http("GET", "/api/my/settings", headers={})

print(json.dumps(out, ensure_ascii=False, default=str))
'''


@functools.lru_cache(maxsize=None)
def tech_probe() -> dict:
    return run_child(TECH_CHILD, str(ROOT))


# --------------------------------------------------------------------------- #
# C. 加密与迁移：真跑模块（含散列无损转换）
# --------------------------------------------------------------------------- #
CRYPTO_CHILD = r'''
import base64
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

root = sys.argv[1]
sys.path.insert(0, root)
data_dir = tempfile.mkdtemp(prefix="cpq-secrets-")
os.environ["DATA_DIR"] = data_dir

KEY_B64 = base64.b64encode(bytes(range(32))).decode("ascii")
os.environ["CPQ_USER_SECRET_KEY"] = KEY_B64

out = {"data_dir": data_dir}

# --- 1. seal / open ---
try:
    import cpq_user_secrets as secrets_mod
    out["module"] = True
except Exception as exc:  # noqa: BLE001
    secrets_mod = None
    out["module"] = "%s: %s" % (type(exc).__name__, exc)

if secrets_mod is not None:
    plain = "ACCOUNT-QWEN-KEY-0002"
    try:
        first = secrets_mod.seal(plain)
        second = secrets_mod.seal(plain)
        out["sealed_first"] = first
        out["sealed_second"] = second
        out["round_trip"] = secrets_mod.open(first)
        out["contains_plain"] = plain in str(first)
        out["different"] = first != second
        head, nonce, payload = str(first).split(":")
        out["prefix"] = head
        flipped = "A" if payload[0] != "A" else "B"
        broken = "%s:%s:%s%s" % (head, nonce, flipped, payload[1:])
        try:
            secrets_mod.open(broken)
            out["tamper"] = "no-error"
        except Exception as exc:  # noqa: BLE001
            out["tamper"] = type(exc).__name__
        try:
            secrets_mod.open("v1:not-base64:also-not")
            out["garbage"] = "no-error"
        except Exception as exc:  # noqa: BLE001
            out["garbage"] = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        out["seal_error"] = "%s: %s" % (type(exc).__name__, exc)

    # 未配置密钥：必须明确失败，不得静默明文落库
    os.environ.pop("CPQ_USER_SECRET_KEY", None)
    try:
        secrets_mod.seal(plain)
        out["missing_key"] = "no-error"
    except Exception as exc:  # noqa: BLE001
        out["missing_key"] = type(exc).__name__
    os.environ["CPQ_USER_SECRET_KEY"] = KEY_B64

# 写入密文：落库参数里不得出现明文（真跑 cpq_auth.set_user_llm，用一个记录型假连接）
try:
    import cpq_auth

    RECORDED = []

    class Cursor:
        rowcount = 0

        def execute(self, sql, args=()):
            RECORDED.append([str(sql), list(args or ())])
            return self

        def fetchone(self):
            return None

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    cpq_auth._connect = lambda: Conn()
    cpq_auth.set_user_llm("7", model="deepseek-v4-pro", provider="qwen",
                          key="ACCOUNT-QWEN-KEY-0002")
    out["db_refused_plaintext"] = not any(
        "ACCOUNT-QWEN-KEY-0002" in " ".join(str(a) for a in args)
        for _sql, args in RECORDED)
    out["db_statements"] = [sql for sql, _args in RECORDED]
    cpq_auth._connect = lambda: (_ for _ in ()).throw(cpq_auth.BackendUnavailable("probe"))
except Exception as exc:  # noqa: BLE001
    out["db_probe_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 2. 迁移脚本：口令散列无损转换 ---
script = pathlib.Path(root) / "scripts" / "migrate_users_to_pg.py"
out["script_exists"] = script.exists()
if script.exists():
    try:
        spec = importlib.util.spec_from_file_location("migrate_users_to_pg", script)
        migrate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migrate)
        out["imported"] = True
    except Exception as exc:  # noqa: BLE001
        migrate = None
        out["imported"] = "%s: %s" % (type(exc).__name__, exc)
    if migrate is not None:
        try:
            from tech_app.backend.services import auth as tech_auth
            legacy = tech_auth.hash_password("secret123")
            out["legacy_hash"] = legacy
            converted = migrate.convert_password_hash(legacy)
            out["converted_hash"] = converted
            out["legacy_ok"] = tech_auth.verify_password("secret123", legacy)
            out["converted_ok"] = cpq_auth.verify_password("secret123", converted)
            out["converted_wrong_pw"] = cpq_auth.verify_password("nope", converted)
            out["already_cpq"] = migrate.convert_password_hash(converted)
            try:
                migrate.convert_password_hash("not-a-hash")
                out["garbage_hash"] = "no-error"
            except Exception as exc:  # noqa: BLE001
                out["garbage_hash"] = type(exc).__name__
        except Exception as exc:  # noqa: BLE001
            out["convert_error"] = "%s: %s" % (type(exc).__name__, exc)

print(json.dumps(out, ensure_ascii=False, default=str))
'''


@functools.lru_cache(maxsize=None)
def crypto_probe() -> dict:
    return run_child(CRYPTO_CHILD, str(ROOT))


# --------------------------------------------------------------------------- #
# D. 迁移脚本的 dry-run（默认不写库、不改原文件）
# --------------------------------------------------------------------------- #
DRY_RUN_CHILD = r'''
import json
import os
import pathlib
import subprocess
import sys
import tempfile

root = sys.argv[1]
data_dir = pathlib.Path(tempfile.mkdtemp(prefix="cpq-migrate-"))
os.environ["DATA_DIR"] = str(data_dir)
sys.path.insert(0, root)

try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    import types
    stub = types.ModuleType("dotenv")
    stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = stub

from tech_app.backend.services import auth as tech_auth

LEGACY = {
    "admin": {"username": "admin", "role": "admin", "display_name": "默认管理员",
              "password_hash": tech_auth.hash_password("admin123"),
              "created_at": "2026-09-01 09:00:00"},
    "zhang": {"username": "zhang", "role": "engineer", "display_name": "张三",
              "requested_role": "engineer",
              "password_hash": tech_auth.hash_password("zhang-secret"),
              "created_at": "2026-09-02 09:00:00"},
    "system": {"username": "system", "role": "viewer", "display_name": "system",
               "password_hash": tech_auth.hash_password("archived-secret"),
               "is_system": True, "created_at": "2026-09-03 09:00:00"},
}
legacy_file = data_dir / "_auth_users.json"
legacy_file.write_text(json.dumps(LEGACY, ensure_ascii=False), encoding="utf-8")
before = legacy_file.read_bytes()

script = pathlib.Path(root) / "scripts" / "migrate_users_to_pg.py"
env = dict(os.environ)
env.update({"CPQ_PG_HOST": "127.0.0.1", "CPQ_PG_PORT": "9", "CPQ_PG_CONNECT_TIMEOUT": "2",
            "DATA_DIR": str(data_dir)})
out = {}
for name, argv in (("default", []), ("dry_run", ["--dry-run"])):
    if not script.exists():
        out[name] = {"returncode": None, "error": "脚本不存在"}
        continue
    completed = subprocess.run([sys.executable, str(script), *argv], cwd=root, env=env,
                               capture_output=True, text=True, timeout=180)
    out[name] = {"returncode": completed.returncode,
                 "stdout": completed.stdout[-3000:], "stderr": completed.stderr[-3000:]}

out["legacy_unchanged"] = legacy_file.read_bytes() == before
out["report_file_written"] = (data_dir / "_auth_users.migrated.json").exists()
print(json.dumps(out, ensure_ascii=False, default=str))
'''


@functools.lru_cache(maxsize=None)
def dry_run_probe() -> dict:
    return run_child(DRY_RUN_CHILD, str(ROOT))


# --------------------------------------------------------------------------- #
# E. 启动自检：AUTH_ENABLED=true + CPQ_SSO=false 必须当场失败
# --------------------------------------------------------------------------- #
GUARD_CHILD = r'''
import json
import os
import sys
import tempfile

root = sys.argv[1]
sys.path.insert(0, root)
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tech-guard-")
os.environ["AUTH_ENABLED"] = "true"
os.environ["CPQ_SSO"] = "false"
os.environ["AUTH_SECRET"] = "guard-probe-secret-not-the-default"
os.environ["DEFAULT_ADMIN_PASSWORD"] = "guard-probe-password"
os.environ["TASK_RECOVER_ON_START"] = "false"

try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    import types
    stub = types.ModuleType("dotenv")
    stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = stub

out = {}
try:
    import tech_app.backend.main as main
    try:
        result = main._startup_housekeeping()
        out["raised"] = False
        out["result"] = repr(result)[:200]
    except BaseException as exc:  # noqa: BLE001
        out["raised"] = type(exc).__name__
        out["message"] = str(exc)[:400]
except BaseException as exc:  # noqa: BLE001
    out["probe_error"] = "%s: %s" % (type(exc).__name__, exc)

print(json.dumps(out, ensure_ascii=False, default=str))
'''


@functools.lru_cache(maxsize=None)
def guard_probe() -> dict:
    return run_child(GUARD_CHILD, str(ROOT))


# --------------------------------------------------------------------------- #
# 1. Spec 与角色字典
# --------------------------------------------------------------------------- #
class SpecDocumentRedTest(unittest.TestCase):
    def test_spec_document_exists(self):
        self.assertTrue(SPEC.exists(),
                        "缺少 docs/specs/user-data-unified-in-pg.md（本批的验收依据）")


class RoleDictionaryRedTest(unittest.TestCase):
    """C2：CPQ 角色字典增 admin / viewer，既有映射不动。"""

    @classmethod
    def setUpClass(cls):
        cls.data = cpq_probe()
        cls.cpq_auth = read(CPQ_AUTH_PY)
        cls.sso = read(CPQ_SSO_PY)

    def test_cpq_roles_include_admin(self):
        self.assertIn("admin", self.data.get("role_codes") or [],
                      "CPQ 角色字典必须有 admin（系统管理员），现在是：%s"
                      % (self.data.get("role_codes"),))

    def test_cpq_roles_include_viewer(self):
        self.assertIn("viewer", self.data.get("role_codes") or [],
                      "CPQ 角色字典必须有 viewer（自助注册落地角色），现在是：%s"
                      % (self.data.get("role_codes"),))

    def test_admin_role_is_labelled(self):
        self.assertIn("系统管理员", self.cpq_auth, "admin 角色要有中文名，403 文案要说人话")

    def test_sso_role_map_covers_admin_and_viewer(self):
        self.assertIn('"admin": "admin"', self.sso.replace("'", '"'),
                      "cpq_sso.ROLE_MAP 必须把 CPQ 的 admin 映射到技术工艺的 admin")
        self.assertIn('"viewer": "viewer"', self.sso.replace("'", '"'),
                      "cpq_sso.ROLE_MAP 必须把 CPQ 的 viewer 映射到技术工艺的 viewer")

    def test_existing_mappings_unchanged(self):
        flat = self.sso.replace("'", '"').replace(" ", "")
        for pair in ('"sales_mgr":"viewer"', '"process_mgr":"process_manager"',
                     '"finance_mgr":"finance_manager"', '"tech_director":"process_director"'):
            self.assertIn(pair, flat, f"既有角色映射不得改动：{pair}")


class PgSchemaRedTest(unittest.TestCase):
    """C3 / C4：表结构增量与主键口径。"""

    @classmethod
    def setUpClass(cls):
        cls.src = read(CPQ_AUTH_PY)

    def test_user_table_has_requested_role_and_is_system(self):
        for column in ("requested_role", "is_system"):
            self.assertIn(column, self.src,
                          f"cpq_wf_user 必须增加 {column}（现在没有，注册申请/归档账号无处安放）")

    def test_llm_setting_table_exists(self):
        self.assertIn("cpq_wf_user_llm_setting", self.src,
                      "账号级模型与密钥要落 cpq_wf_user_llm_setting，而不是本地文件")
        body = self.src[self.src.find("cpq_wf_user_llm_setting"):]
        for column in ("model_cipher", "keys_cipher", "updated_at", "user_id"):
            self.assertIn(column, body[:1200],
                          f"cpq_wf_user_llm_setting 缺列 {column}")

    def test_user_llm_setting_is_keyed_by_user_id(self):
        body = self.src[self.src.find("cpq_wf_user_llm_setting"):]
        self.assertIn("REFERENCES", body[:1200],
                      "账号级设置必须以外键挂在 cpq_wf_user(user_id) 上（主键口径 = user_id）")

    def test_no_role_check_constraint_added(self):
        self.assertNotIn("CHECK (role_code", self.src,
                         "角色是字典取值，不得引入 DB 取值约束（加角色就不该改 schema）")

    def test_user_columns_include_new_fields(self):
        head = self.src[self.src.find("_USER_COLS"):][:400]
        for column in ("requested_role", "is_system"):
            self.assertIn(column, head, f"_USER_COLS 必须同步增加 {column}")

    def test_list_users_accepts_status_filter(self):
        self.assertTrue(re.search(r"def list_users\([^)]*status", self.src),
                        "list_users 必须支持按 status 过滤（停用账号列表要查得出来）")

    def test_user_admin_functions_exist(self):
        for name in ("def create_user(", "def update_user(", "def set_password(",
                     "def change_password(", "def find_user(",
                     "def get_user_llm(", "def set_user_llm(", "def delete_user_llm_key(",
                     "def bootstrap_admin("):
            self.assertIn(name, self.src, f"cpq_auth 缺少 {name}")


# --------------------------------------------------------------------------- #
# 2. CPQ 用户管理接口（真 HTTP）
# --------------------------------------------------------------------------- #
class UserAdminHttpRedTest(unittest.TestCase):
    """C5：admin 专用的建号 / 改角色 / 停用 / 重置口令。"""

    ADMIN_ONLY_FIELDS = {"email", "status", "requested_role", "is_system", "last_login_at",
                         "created_at"}

    @classmethod
    def setUpClass(cls):
        cls.data = cpq_probe()

    def case(self, name) -> dict:
        value = self.data.get(name)
        self.assertIsInstance(value, dict, "%s 缺失：%s" % (name, self.data))
        return value

    def test_anonymous_cannot_list_users(self):
        got = self.case("users_anon")
        self.assertEqual(got.get("status"), 401, got)

    def test_non_admin_list_hides_private_columns(self):
        self.assertEqual(self.case("users_viewer").get("status"), 200, self.case("users_viewer"))
        leaked = self.ADMIN_ONLY_FIELDS & set(self.data.get("users_viewer_keys") or [])
        self.assertFalse(leaked,
                         f"非 admin 的名单不得带出 {sorted(leaked)}（只给 user_id/username/"
                         f"display_name/role_code/role_name）")
        self.assertIn("user_id", self.data.get("users_viewer_keys") or [],
                      "名单必须带 user_id —— 前端要按 user_id 指派到人")

    def test_manager_list_hides_private_columns(self):
        self.assertEqual(self.case("users_mgr").get("status"), 200, self.case("users_mgr"))
        leaked = self.ADMIN_ONLY_FIELDS & set(self.data.get("users_mgr_keys") or [])
        self.assertFalse(leaked, f"工艺经理（非 admin）不得看到 {sorted(leaked)}")

    def test_admin_list_returns_private_columns(self):
        self.assertEqual(self.case("users_admin").get("status"), 200, self.case("users_admin"))
        missing = self.ADMIN_ONLY_FIELDS - set(self.data.get("users_admin_keys") or [])
        self.assertFalse(missing,
                         f"admin 的名单要能看到 {sorted(missing)}（用户管理页要用）")

    def test_user_id_is_a_string(self):
        src = read(CPQ_AUTH_PY)
        self.assertTrue(re.search(r'user_id"\]\s*=\s*str\(', src),
                        "user_id 是雪花 ID，出接口前必须转成字符串（超 JS 安全整数）")

    def test_role_filter_still_works(self):
        self.assertEqual(self.case("users_role_filter").get("status"), 200,
                         self.case("users_role_filter"))

    def test_anonymous_cannot_create_user(self):
        self.assertEqual(self.case("create_anon").get("status"), 401, self.case("create_anon"))

    def test_non_admin_cannot_create_user(self):
        got = self.case("create_viewer")
        self.assertEqual(got.get("status"), 403,
                         f"非 admin 建号必须 403（现在 %s）" % got.get("status"))

    def test_admin_can_create_user(self):
        got = self.case("create_admin")
        self.assertEqual(got.get("status"), 200, got)

    def test_non_admin_cannot_change_role_or_status(self):
        got = self.case("patch_viewer")
        self.assertEqual(got.get("status"), 403,
                         f"非 admin 改别人角色/状态必须 403（现在 %s）" % got.get("status"))

    def test_admin_patch_targets_the_path_user_id(self):
        got = self.case("patch_admin")
        self.assertEqual(got.get("status"), 200, got)
        # patch_calls 是列表而不是结果对象，不能走 case()（它按 dict 断言）；
        # 断言的实质不变：作用对象必须是路径里的 user_id=9。
        patch_calls = self.data.get("patch_calls") or []
        call = patch_calls[0] if patch_calls else []
        self.assertEqual(call[:2], ["update_user", "9"],
                         f"作用对象必须是路径里的 user_id=9，不能被请求体里的 user_id 顶掉：{call}")

    def test_non_admin_cannot_reset_password(self):
        got = self.case("password_viewer")
        self.assertEqual(got.get("status"), 403,
                         f"非 admin 重置他人口令必须 403（现在 %s）" % got.get("status"))

    def test_admin_can_reset_password(self):
        got = self.case("password_admin")
        self.assertEqual(got.get("status"), 200, got)
        password_calls = self.data.get("password_calls") or []
        call = password_calls[0] if password_calls else []
        self.assertEqual(call[:2], ["set_password", "9"], call)


class SelfServiceHttpRedTest(unittest.TestCase):
    """C6：本人接口只看票，请求体里的 user_id/username 一律忽略。"""

    @classmethod
    def setUpClass(cls):
        cls.data = cpq_probe()

    def case(self, name) -> dict:
        value = self.data.get(name)
        self.assertIsInstance(value, dict, "%s 缺失：%s" % (name, self.data))
        return value

    def test_profile_targets_the_token_holder(self):
        got = self.case("my_profile")
        self.assertEqual(got.get("status"), 200, got)
        calls = self.data.get("my_calls") or []
        target = [c for c in calls if c and c[0] == "update_user"]
        self.assertTrue(target, f"改资料必须落到 cpq_auth.update_user：{calls}")
        self.assertEqual(target[0][1], "7",
                         f"作用对象必须是票上的人（user_id=7），不是请求体里的 1：{target[0]}")

    def test_password_change_verifies_the_old_one(self):
        self.assertEqual(self.case("my_password").get("status"), 200, self.case("my_password"))
        calls = self.data.get("my_calls") or []
        target = [c for c in calls if c and c[0] == "change_password"]
        self.assertTrue(target, f"改密码必须走 cpq_auth.change_password（要验旧口令）：{calls}")
        self.assertEqual(target[0][1], "7", f"作用对象必须是票上的人：{target[0]}")

    def test_wrong_old_password_is_rejected(self):
        got = self.case("my_password_wrong")
        self.assertEqual(got.get("status"), 400,
                         f"旧口令错必须 400，实际 {got.get('status')} {got.get('text')}")

    def test_anonymous_cannot_read_account_settings(self):
        self.assertEqual(self.case("llm_anon").get("status"), 401, self.case("llm_anon"))

    def test_llm_get_returns_masked_summary(self):
        got = self.case("llm_get")
        self.assertEqual(got.get("status"), 200, got)
        text = got.get("text") or ""
        self.assertNotIn(PLAIN_ACCOUNT_KEY, text, "本人摘要接口不得回明文 Key")

    def test_llm_put_targets_the_token_holder(self):
        got = self.case("llm_put")
        self.assertEqual(got.get("status"), 200, got)
        calls = self.data.get("llm_calls") or []
        target = [c for c in calls if c and c[0] == "set_user_llm"]
        self.assertTrue(target, f"账号级模型写入必须落到 cpq_auth.set_user_llm：{calls}")
        self.assertEqual(target[0][1], "7",
                         f"作用对象必须是票上的人（7），不是请求体里的 1：{target[0]}")
        self.assertIn(ACCOUNT_MODEL, target[0][2:],
                      f"模型值要原样落到该账号：{target[0]}")

    def test_llm_put_key_targets_the_token_holder(self):
        got = self.case("llm_put_key")
        self.assertEqual(got.get("status"), 200, got)
        calls = self.data.get("llm_calls") or []
        target = [c for c in calls if c and c[0] == "set_user_llm" and "qwen" in c]
        self.assertTrue(target, f"个人 Key 写入必须落到 cpq_auth.set_user_llm：{calls}")
        self.assertEqual(target[0][1], "7", f"作用对象必须是票上的人：{target[0]}")

    def test_llm_delete_key_targets_the_token_holder(self):
        got = self.case("llm_delete")
        self.assertEqual(got.get("status"), 200, got)
        calls = self.data.get("llm_calls") or []
        target = [c for c in calls if c and c[0] in ("delete_user_llm_key", "set_user_llm")
                  and "qwen" in c]
        self.assertTrue(target, f"删除个人 Key 必须落到该账号：{calls}")
        self.assertEqual(target[0][1], "7", f"作用对象必须是票上的人：{target[0]}")

    def test_summary_never_leaks_between_accounts(self):
        got = self.case("llm_other_account")
        self.assertEqual(got.get("status"), 200, got)
        self.assertNotIn(ACCOUNT_MODEL, got.get("text") or "",
                         "另一个账号不得看到别人的账号级设置（连模型名都不给）")


class InternalChannelRedTest(unittest.TestCase):
    """C9：服务间内部通道（X-Internal-Token），供后台任务读账号级设置。"""

    @classmethod
    def setUpClass(cls):
        cls.data = cpq_probe()

    def case(self, name) -> dict:
        value = self.data.get(name)
        self.assertIsInstance(value, dict, "%s 缺失：%s" % (name, self.data))
        return value

    def test_internal_requires_the_token(self):
        got = self.case("internal_anon")
        self.assertIn(got.get("status"), (401, 403),
                      f"没有内部令牌不得读任何账号级设置，实际 {got.get('status')}")

    def test_internal_rejects_a_wrong_token(self):
        got = self.case("internal_bad")
        self.assertIn(got.get("status"), (401, 403),
                      f"内部令牌不对必须拒绝，实际 {got.get('status')}")
        self.assertNotIn(PLAIN_ACCOUNT_KEY, got.get("text") or "", "拒绝时不得回任何数据")

    def test_internal_returns_the_entry(self):
        got = self.case("internal_ok")
        self.assertEqual(got.get("status"), 200, got)
        self.assertIn('"user_id"', got.get("text") or "",
                      "内部通道要回 user_id（主键口径）以及该账号的账号级设置")

    def test_internal_can_write(self):
        got = self.case("internal_put")
        self.assertEqual(got.get("status"), 200, got)
        calls = self.data.get("internal_calls") or []
        target = [c for c in calls if c and c[0] == "set_user_llm"]
        self.assertTrue(target, f"内部通道写入要落到 cpq_auth.set_user_llm：{calls}")


class RegisterTighteningRedTest(unittest.TestCase):
    """C7：自助注册不能自己选 admin。"""

    @classmethod
    def setUpClass(cls):
        cls.data = cpq_probe()

    def case(self, name) -> dict:
        value = self.data.get(name)
        self.assertIsInstance(value, dict, "%s 缺失：%s" % (name, self.data))
        return value

    def test_self_register_as_admin_is_rejected(self):
        got = self.case("register_admin")
        self.assertEqual(got.get("status"), 400,
                         f"自助注册成 admin 必须 400（加了 admin 角色后这条路等于谁都能当管理员），"
                         f"实际 {got.get('status')} {got.get('text')}")

    def test_requesting_admin_is_rejected(self):
        got = self.case("register_requested_admin")
        self.assertEqual(got.get("status"), 400,
                         f"requested_role=admin 也要拒绝，实际 {got.get('status')} {got.get('text')}")

    def test_self_register_lands_as_viewer(self):
        got = self.case("register_ok")
        self.assertEqual(got.get("status"), 200, got)
        calls = self.data.get("register_calls") or []
        target = [c for c in calls if c and c[0] == "register"]
        self.assertTrue(target, f"注册必须落到 cpq_auth.register：{calls}")
        self.assertEqual(target[0][2], "viewer",
                         f"自助注册一律落地 viewer，实际 {target[0]}")
        self.assertEqual(target[0][3], "process_mgr",
                         f"申请的角色要记进 requested_role：{target[0]}")


class BackendDownRedTest(unittest.TestCase):
    """C9/C11：库或登录服务不可用时明确报错，不装作能用。"""

    @classmethod
    def setUpClass(cls):
        cls.data = cpq_probe()

    def test_users_503_when_backend_down(self):
        got = self.data.get("backend_down_users") or {}
        self.assertEqual(got.get("status"), 503, got)

    def test_llm_503_when_backend_down(self):
        got = self.data.get("backend_down_llm") or {}
        self.assertEqual(got.get("status"), 503,
                         f"库不可用时账号级设置接口必须明确 503，实际 {got.get('status')}")


# --------------------------------------------------------------------------- #
# 3. 账号级密钥加密
# --------------------------------------------------------------------------- #
class SecretEncryptionRedTest(unittest.TestCase):
    """C8：AEAD 加密，密钥材料来自环境变量，未配置时明确失败。"""

    @classmethod
    def setUpClass(cls):
        cls.data = crypto_probe()
        cls.src = read(CPQ_SECRETS_PY) if CPQ_SECRETS_PY.exists() else ""
        cls.cpq_auth_src = read(CPQ_AUTH_PY)

    def test_secret_module_exists(self):
        self.assertTrue(CPQ_SECRETS_PY.exists(),
                        "缺少 cpq_user_secrets.py：账号级密钥必须有独立的加密模块")
        for name in ("def seal(", "def open(", "class SecretKeyMissing"):
            self.assertIn(name, self.src, f"cpq_user_secrets 缺少 {name}")

    def test_uses_an_aead(self):
        self.assertTrue(re.search(r"AESGCM|ChaCha20Poly1305|Fernet", self.src),
                        "必须用带认证的加密（AES-GCM / ChaCha20-Poly1305 / Fernet），"
                        "不能自己拼流密码")

    def test_key_material_comes_from_the_environment(self):
        self.assertIn("CPQ_USER_SECRET_KEY", self.src,
                      "密钥材料必须来自环境变量 CPQ_USER_SECRET_KEY，不能写死在代码或库里")

    def test_round_trip(self):
        self.assertEqual(self.data.get("round_trip"), "ACCOUNT-QWEN-KEY-0002",
                         f"seal/open 必须往返一致：{self.data.get('seal_error') or self.data}")

    def test_ciphertext_has_no_plaintext(self):
        self.assertTrue(self.data.get("module") is True,
                        f"加密模块不可用：{self.data.get('module')}")
        self.assertFalse(self.data.get("contains_plain"),
                         "密文里不得出现明文")

    def test_every_seal_uses_a_fresh_nonce(self):
        self.assertTrue(self.data.get("different"),
                        "同一明文两次 seal 必须不同（每次新随机 nonce），"
                        "否则相同密钥在库里一眼可辨")

    def test_ciphertext_is_versioned(self):
        self.assertEqual(self.data.get("prefix"), "v1",
                         f"密文要带版本前缀，便于以后换算法：{self.data.get('sealed_first')}")

    def test_tampering_is_detected(self):
        self.assertNotIn(self.data.get("tamper"), (None, "no-error"),
                         "密文被改动后 open 必须抛错（AEAD 认证），不能静默解出垃圾")

    def test_garbage_is_rejected(self):
        self.assertNotIn(self.data.get("garbage"), (None, "no-error"),
                         "非密文输入必须抛错，不能原样当明文返回")

    def test_missing_key_fails_loudly(self):
        self.assertNotIn(self.data.get("missing_key"), (None, "no-error"),
                         "没有 CPQ_USER_SECRET_KEY 时必须明确失败，绝不静默明文落库")

    def test_no_plaintext_key_reaches_the_database(self):
        self.assertTrue(self.data.get("db_refused_plaintext"),
                        f"写库参数里出现了明文 Key（或写入失败）：涉及语句 "
                        f"{self.data.get('db_statements')} / 错误 {self.data.get('db_probe_error')}")

    def test_writes_are_sealed_before_storage(self):
        self.assertIn("seal(", self.cpq_auth_src,
                      "cpq_auth 写 cpq_wf_user_llm_setting 之前必须先 seal()")


# --------------------------------------------------------------------------- #
# 4. 迁移
# --------------------------------------------------------------------------- #
class MigrationRedTest(unittest.TestCase):
    """C13：口令散列无损转换、默认 dry-run、不改原文件。"""

    @classmethod
    def setUpClass(cls):
        cls.data = crypto_probe()
        cls.dry = dry_run_probe()
        cls.src = read(MIGRATE_PY) if MIGRATE_PY.exists() else ""

    def test_account_level_settings_are_migrated_too(self):
        self.assertIn("_user_llm.json", self.src,
                      "迁移脚本必须同一趟搬账号级模型与 Key —— 不搬的话，换存储的那一刻"
                      "现有账号的个人模型与个人 Key 就凭空消失了")
        self.assertIn("seal(", self.src,
                      "搬账号级 Key 必须先用 cpq_user_secrets.seal() 加密再入库")

    def test_script_exists(self):
        self.assertTrue(MIGRATE_PY.exists(),
                        "缺少 scripts/migrate_users_to_pg.py：存量本地用户要有一条可复跑的搬迁路径")

    def test_cli_flags(self):
        for flag in ("--dry-run", "--apply", "--promote"):
            self.assertIn(flag, self.src, f"迁移脚本缺少 {flag}")
        self.assertIn("_auth_users.migrated.json", self.src,
                      "迁移结果要落一份报告（每个账号 imported/skipped/rejected）")

    def test_migration_module_ran(self):
        self.assertTrue(self.data.get("imported") is True,
                        f"迁移脚本无法作为模块加载（convert_password_hash 是它的对外契约）："
                        f"{self.data.get('imported')}")

    def test_conversion_is_lossless(self):
        self.assertTrue(self.data.get("converted_ok"),
                        f"转换后的散列必须能用原口令通过 cpq_auth.verify_password："
                        f"{self.data.get('convert_error') or self.data.get('converted_hash')}")

    def test_conversion_rejects_wrong_password(self):
        self.assertTrue(self.data.get("converted_ok"),
                        f"转换结果还没有被验证过：{self.data.get('convert_error')}")
        self.assertFalse(self.data.get("converted_wrong_pw"),
                         "转换不得让错口令通过")

    def test_already_converted_hash_is_kept(self):
        self.assertTrue(self.data.get("converted_hash"),
                        f"没有拿到转换结果：{self.data.get('convert_error')}")
        self.assertEqual(self.data.get("already_cpq"), self.data.get("converted_hash"),
                         "已经是 CPQ 格式的散列要原样保留（幂等）")

    def test_unparsable_hash_is_refused(self):
        self.assertIn(self.data.get("garbage_hash"), ("ValueError", "TypeError", "AuthError"),
                      f"解析不了的散列必须明确报错、列入 rejected，不能静默生成空密码："
                      f"{self.data.get('garbage_hash')}")

    def test_default_run_is_dry(self):
        got = self.dry.get("default") or {}
        self.assertEqual(got.get("returncode"), 0,
                         f"默认必须是 dry-run 且不因连不上库而失败：{got}")
        self.assertIn("zhang", (got.get("stdout") or "") + (got.get("stderr") or ""),
                      "dry-run 要打印计划清单（含每个账号的处置）")

    def test_dry_run_writes_nothing(self):
        self.assertEqual((self.dry.get("default") or {}).get("returncode"), 0,
                         f"dry-run 必须能跑通：{self.dry.get('default')}")
        self.assertTrue(self.dry.get("legacy_unchanged"),
                        "迁移不得改写/删除原 _auth_users.json")
        self.assertFalse(self.dry.get("report_file_written"),
                         "dry-run 不得落任何结果文件")


# --------------------------------------------------------------------------- #
# 5. 技术工艺侧（HTTP 面 + 源码契约）
# --------------------------------------------------------------------------- #
class TechSideHttpRedTest(unittest.TestCase):
    """C10 / C11：技术工艺只走 HTTP，本地用户表下线。"""

    @classmethod
    def setUpClass(cls):
        cls.data = tech_probe()

    def case(self, name) -> dict:
        value = self.data.get(name)
        self.assertIsInstance(value, dict, "%s 缺失：%s" % (name, self.data))
        return value

    def test_users_endpoint_points_to_cpq(self):
        got = self.case("users")
        self.assertEqual(got.get("status"), 409,
                         f"SSO 模式下 /api/users 必须明确 409 并指路 CPQ，"
                         f"不能回本地用户数据；实际 {got.get('status')} {got.get('text')[:200]}")
        self.assertTrue(re.search(r"CPQ|配置报价", got.get("text") or ""),
                        f"409 文案要告诉用户去哪管账号：{got.get('text')[:200]}")

    def test_role_endpoint_points_to_cpq(self):
        got = self.case("users_role")
        self.assertEqual(got.get("status"), 409, got)

    def test_local_login_still_rejected(self):
        self.assertEqual(self.case("login").get("status"), 409, self.case("login"))

    def test_local_register_still_rejected(self):
        self.assertEqual(self.case("register").get("status"), 409, self.case("register"))

    def test_me_exposes_user_id(self):
        got = self.case("me")
        body = got.get("body") or {}
        sso = (body.get("sso") or {}) if isinstance(body, dict) else {}
        self.assertEqual(sso.get("user_id"), "7",
                         f"/api/me 的 sso 块必须带 user_id（前端要按 user_id 管账号）：{body}")

    def test_my_settings_reads_through_the_client(self):
        self.assertEqual(self.case("my_settings_get").get("status"), 200,
                         self.case("my_settings_get"))
        names = [c[0] for c in (self.data.get("my_settings_get_calls") or [])]
        self.assertIn("summary", names,
                      f"/api/my/settings 必须从 CPQ 客户端取摘要，实际调用：{names}")

    def test_my_settings_write_goes_through_the_client(self):
        self.assertEqual(self.case("my_settings_put_model").get("status"), 200,
                         self.case("my_settings_put_model"))
        calls = self.data.get("my_settings_put_calls") or []
        target = [c for c in calls if c and c[0] == "set_model"]
        self.assertTrue(target, f"保存个人模型必须走客户端：{calls}")
        self.assertEqual(target[0][1:], ["mgr", "qwen3.8-max"],
                         f"账号级设置只作用于本人：{target[0]}")

    def test_my_settings_key_write_goes_through_the_client(self):
        calls = self.data.get("my_settings_key_calls") or []
        target = [c for c in calls if c and c[0] == "set_key"]
        self.assertTrue(target, f"保存个人 Key 必须走客户端：{calls}")
        self.assertEqual(target[0][1], "mgr", f"账号级设置只作用于本人：{target[0]}")

    def test_my_settings_key_delete_goes_through_the_client(self):
        calls = self.data.get("my_settings_delete_calls") or []
        target = [c for c in calls if c and c[0] in ("set_key", "delete_key") and "qwen" in c]
        self.assertTrue(target, f"删除个人 Key 必须走客户端：{calls}")

    def test_request_token_is_propagated(self):
        seen = []
        for name in ("my_settings_get_calls", "my_settings_put_calls",
                     "my_settings_key_calls", "my_settings_delete_calls",
                     "my_profile_calls", "my_password_calls"):
            seen += [c[0] for c in (self.data.get(name) or [])]
        self.assertIn("set_request_token", seen,
                      f"网关必须把用户自己的 CPQ 票交给客户端（本人写操作要用本人身份）：{sorted(set(seen))}")

    def test_profile_update_goes_through_the_client(self):
        got = self.case("my_profile")
        self.assertEqual(got.get("status"), 200, got)
        calls = self.data.get("my_profile_calls") or []
        target = [c for c in calls if c and c[0] == "update_profile"]
        self.assertTrue(target, f"改显示名必须转发到 CPQ：{calls}")
        self.assertIn("新名字", target[0], f"值要原样转发：{target[0]}")

    def test_password_change_goes_through_the_client(self):
        got = self.case("my_password")
        self.assertEqual(got.get("status"), 200, got)
        calls = self.data.get("my_password_calls") or []
        target = [c for c in calls if c and c[0] == "change_password"]
        self.assertTrue(target, f"改密码必须转发到 CPQ（含旧口令校验）：{calls}")

    def test_no_token_is_rejected(self):
        self.assertEqual(self.case("users_no_token").get("status"), 401,
                         self.case("users_no_token"))
        self.assertEqual(self.case("my_settings_no_token").get("status"), 401,
                         self.case("my_settings_no_token"))


class TechSideSourceRedTest(unittest.TestCase):
    """C1 / C10 / C12：不直连 PG、本地用户表与本地密钥文件退役、启动守卫。"""

    @classmethod
    def setUpClass(cls):
        cls.main = read(TECH_MAIN_PY)
        cls.meta = read(META_BACKEND_PY)
        cls.user_llm = read(USER_LLM_PY)

    def test_no_direct_postgres_in_tech_app(self):
        offenders = []
        for path in sorted((ROOT / "tech_app").rglob("*.py")):
            src = read(path)
            if re.search(r"^\s*import psycopg", src, re.M) or "psycopg.connect" in src:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertFalse(offenders,
                         f"技术工艺必须只走 CPQ 的 HTTP，不得直连 PG：{offenders}")

    def test_local_user_file_is_retired(self):
        self.assertNotIn("_auth_users.json", self.meta,
                         "本地用户文件 _auth_users.json 必须下线（用户数据只剩 PG 一处）")

    def test_local_key_file_is_retired(self):
        self.assertNotIn("_user_llm.json", self.user_llm,
                         "账号级模型与密钥不再落 DATA_DIR/_user_llm.json，改存 PG 密文")
        for name in ("def get(", "def set_model(", "def set_key(", "def summary(",
                     "def personal_key("):
            self.assertIn(name, self.user_llm,
                          f"user_llm 的对外接口 {name} 不得改名/删除（既有调用点依赖）")

    def test_client_module_contract(self):
        self.assertTrue(CPQ_AUTH_CLIENT_PY.exists(), "缺少 services/cpq_auth_client.py")
        src = read(CPQ_AUTH_CLIENT_PY)
        for name in ("def user_overrides(", "def summary(", "def set_model(", "def set_key(",
                     "def update_profile(", "def change_password(", "def set_request_token(",
                     "class CpqAuthUnavailable"):
            self.assertIn(name, src, f"cpq_auth_client 缺少 {name}")
        self.assertIn("X-Internal-Token", src,
                      "后台任务没有用户票，读账号级设置要用内部令牌通道")
        self.assertIn("/auth/internal/user-llm", src,
                      "内部通道路径按 Spec C9 固定")
        self.assertTrue(re.search(r"ttl|cache", src, re.I),
                        "账号级设置要带进程内缓存（别把 CPQ 当热路径）")

    def test_startup_guard_rejects_a_second_user_source(self):
        """真跑一次启动期自检：AUTH_ENABLED=true + CPQ_SSO=false 必须当场失败。

        只断言源码里出现 RuntimeError 是不够的——现在那句 RuntimeError 是 AUTH_SECRET
        的检查，与本条无关（红测会因此假绿）。这里真调 _startup_housekeeping()。
        """
        data = guard_probe()
        self.assertFalse(data.get("probe_error"),
                         f"启动自检走查失败：{data.get('probe_error')}")
        self.assertTrue(data.get("raised"),
                         "AUTH_ENABLED=true 且 CPQ_SSO=false 时必须在启动阶段明确失败，"
                         "不能留下一条没有用户来源的本地登录路径"
                         f"（实际：{data.get('result')}）")
        self.assertIn("RuntimeError", str(data.get("raised")),
                      f"要用 RuntimeError 明确失败，实际 {data.get('raised')}")
        self.assertTrue(re.search(r"CPQ|配置报价", str(data.get("message") or "")),
                        f"失败文案要告诉运维怎么改（设 CPQ_SSO=true 或关掉 AUTH_ENABLED）："
                        f"{data.get('message')}")


class DependencyRedTest(unittest.TestCase):
    """C14：依赖增量。"""

    def test_cryptography_is_pinned(self):
        self.assertIn("cryptography", read(REQUIREMENTS),
                      "requirements.txt 要增加 cryptography（账号级密钥加密用）")


if __name__ == "__main__":
    unittest.main()
