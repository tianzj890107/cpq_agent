"""账号级模型与密钥（全局默认兜底）：Spec / Red。

契约见 docs/specs/per-account-model-and-api-key.md。今天的事实是反的：

  · tech_app/backend/services/llm_settings.py 只有一份全局配置，"只有一个模型"；
    resolve() 从 cpq_settings.json 取 model，从 api_keys 取该 provider 的 Key，
    没有任何"发起账号"维度；
  · 后端接口只有全局 GET/PUT /api/settings，没有 /api/my/settings；
  · 结果：任一登录账号改模型/Key 都是全平台生效，想让自己的账号单独选模型、
    用自己的 Key（不污染别人的额度）做不到。

本批要建立的契约（摘要）：
  C1 resolve() 增加"发起账号"维度：显式 user → 上下文账号 → 全局兜底；
  C2 llm_settings.effective(username) 报告生效模型与来源；
  C3 账号级设置独立落 DATA_DIR/_user_llm.json（0600，原子写），不写全局文件；
  C4 模型/provider 白名单仍只有 llm_settings 那一份；
  C5 任何 GET 只回 configured + 打码；账号之间不串号；
  C6 发起账号：HTTP 由 auth_guard 设上下文，异步任务由 tasks.submit(actor=...) 传；
  C7 会话按发起账号解析路由并重建 client；
  C8 能力校验按生效模型；
  C9 全局兜底不被账号级写入改动；缺 Key 明确失败，不静默降级；
  C10 GET/PUT /api/my/settings + DELETE /api/my/settings/keys/{provider}，只作用于本人；
  C12 面板新增「我的模型与密钥」区，全局区权限不变；报价端 404 时隐藏不报错。

验证方式：源码契约（前端面板 / 后端发起账号通道 / 进度文案 / 白名单与原子写 /
会话按账号重建 client）+ 子进程真跑后端
（临时 DATA_DIR、内存里的假全局配置、不联网、不写仓库里的真实 cpq_settings.json）。
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
FRONTEND = ROOT / "tech_app" / "frontend"
MAIN_PY = ROOT / "tech_app/backend/main.py"
TASKS_PY = ROOT / "tech_app/backend/services/tasks.py"
AUTH_PY = ROOT / "tech_app/backend/services/auth.py"
LLM_SETTINGS_PY = ROOT / "tech_app/backend/services/llm_settings.py"
SPEC = ROOT / "docs/specs/per-account-model-and-api-key.md"

GLOBAL_QWEN_KEY = "GLOBAL-QWEN-KEY-0001"
GLOBAL_DS_KEY = "GLOBAL-DS-KEY-0001"
ACCOUNT_QWEN_KEY = "ACCOUNT-QWEN-KEY-0002"
ACCOUNT_MODEL = "deepseek-v4-pro"
ACCOUNT_MODEL_WITH_KEY = "qwen3.8-max"
BAD_MODEL = "no-such-model-v0"
BAD_PROVIDER = "no-such-provider"
USER_LLM_PY = ROOT / "tech_app/backend/services/user_llm.py"
ACTING_USER_PY = ROOT / "tech_app/backend/services/acting_user.py"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


# --------------------------------------------------------------------------- #
# 子进程走查：真跑服务层与 HTTP 面
# --------------------------------------------------------------------------- #
CHILD = r'''
import json
import os
import pathlib
import sys
import time
import types

data_dir, root = sys.argv[1], sys.argv[2]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

out = {"data_dir": data_dir}

# 走查脚本里的账号级取值：与文件顶部常量保持一致。CHILD 是普通字符串（不是
# f-string），顶部那几个常量不会自动进来，这里显式定义一次，否则下面一用到就是
# NameError，红测根本跑不到实现上。
ACCOUNT_QWEN_KEY = "ACCOUNT-QWEN-KEY-0002"
ACCOUNT_MODEL = "deepseek-v4-pro"
ACCOUNT_MODEL_WITH_KEY = "qwen3.8-max"

# 真实仓库里的全局设置文件：本走查只读它一次做指纹，绝不允许被写。
global_path = pathlib.Path(root) / "cpq_settings.json"
out["global_file_before"] = (global_path.read_bytes() if global_path.exists() else b"").hex()[:64]

import cpq_shared_settings

GLOBAL = {
    "model": "qwen3.5-plus",
    "temperature": 0.2,
    "max_tokens": 4096,
    "thinking": False,
    "api_keys": {"qwen": "GLOBAL-QWEN-KEY-0001", "deepseek": "GLOBAL-DS-KEY-0001"},
}
cpq_shared_settings.load = lambda legacy=(): dict(GLOBAL)
_global_saves = []
cpq_shared_settings.save = lambda data: _global_saves.append(dict(data))

from tech_app.backend.services import llm_settings
from tech_app.backend.storage import store

out["global_model"] = llm_settings.current_model_id()


def route(user=""):
    try:
        result = llm_settings.resolve(vision=False, user=user) if user else llm_settings.resolve(vision=False)
    except TypeError as exc:
        return {"error": "TypeError: %s" % exc}
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    if not isinstance(result, dict):
        return {"error": "resolve() 未返回 dict: %r" % (result,)}
    return {"model": result.get("model"), "provider": result.get("provider"),
            "api_key": result.get("api_key"), "base_url": result.get("base_url")}


def route_vision(user=""):
    try:
        result = llm_settings.resolve(vision=True, user=user) if user else llm_settings.resolve(vision=True)
    except TypeError as exc:
        return {"error": "TypeError: %s" % exc}
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    return {"model": result.get("model")}


# --- 1. 账号级存储模块 ---
try:
    from tech_app.backend.services import user_llm
    out["user_llm_module"] = True
except Exception as exc:  # noqa: BLE001
    user_llm = None
    out["user_llm_module"] = "%s: %s" % (type(exc).__name__, exc)

if user_llm is not None:
    try:
        user_llm.set_model("alice", ACCOUNT_MODEL)
        out["alice_store_after_model"] = user_llm.get("alice")
        out["store_path_exists"] = (pathlib.Path(data_dir) / "_user_llm.json").exists()
        mode = (pathlib.Path(data_dir) / "_user_llm.json").stat().st_mode & 0o777
        out["store_mode"] = oct(mode)
        out["alice_summary"] = user_llm.summary("alice")
        out["bob_summary"] = user_llm.summary("bob")
        out["saved_global_configs"] = len(_global_saves)
    except Exception as exc:  # noqa: BLE001
        out["user_llm_probe_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 2. 解析优先级 ---
out["route_no_user"] = route()
out["route_alice"] = route("alice")
out["route_bob"] = route("bob")
out["vision_alice"] = route_vision("alice")

if user_llm is not None:
    try:
        user_llm.set_key("alice", "qwen", ACCOUNT_QWEN_KEY)
        user_llm.set_model("alice", ACCOUNT_MODEL_WITH_KEY)
        out["route_alice_own_key"] = route("alice")
        out["route_bob_global_key"] = route("bob")
        out["global_keys_after_account_write"] = dict(GLOBAL.get("api_keys") or {})
        out["alice_summary_after_key"] = user_llm.summary("alice")
        # 清空 -> 回落全局
        user_llm.set_model("alice", "")
        user_llm.set_key("alice", "qwen", "")
        out["route_alice_after_clear"] = route("alice")
    except Exception as exc:  # noqa: BLE001
        out["account_probe_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 3. 选了没有 Key 的模型：明确失败，不静默降级 ---
if user_llm is not None:
    try:
        user_llm.set_model("carol", "deepseek-v4-pro")
        saved_keys = dict(GLOBAL.get("api_keys") or {})
        saved_keys.pop("deepseek", None)
        cpq_shared_settings.load = lambda legacy=(): dict(GLOBAL, api_keys=saved_keys)
        out["route_carol_missing_key"] = route("carol")
        cpq_shared_settings.load = lambda legacy=(): dict(GLOBAL)
        user_llm.set_model("carol", "")
    except Exception as exc:  # noqa: BLE001
        out["missing_key_probe_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 4. effective() 生效模型与来源 ---
try:
    out["effective_no_user"] = llm_settings.effective()
    out["effective_bob"] = llm_settings.effective("bob")
except Exception as exc:  # noqa: BLE001
    out["effective_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 5. 上下文通道 ---
try:
    from tech_app.backend.services import acting_user
    acting_user.set_acting_user("alice")
    if user_llm is not None:
        user_llm.set_model("alice", ACCOUNT_MODEL)
    out["acting_user_value"] = acting_user.current_acting_user()
    out["route_ctx_alice"] = route()
    acting_user.set_acting_user("")
    out["route_ctx_cleared"] = route()
except Exception as exc:  # noqa: BLE001
    out["acting_user_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 6. 异步任务带发起人 ---
try:
    from tech_app.backend.services import tasks

    pid = store.create_project(source_filename="probe.png", source_bytes=b"x",
                              note="per-account llm probe", owner="tester")
    seen = {}

    def job():
        try:
            from tech_app.backend.services import acting_user as au
            seen["actor"] = au.current_acting_user()
        except Exception as exc:  # noqa: BLE001
            seen["actor_error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            seen["model"] = llm_settings.resolve(vision=False).get("model")
        except Exception as exc:  # noqa: BLE001
            seen["model_error"] = "%s: %s" % (type(exc).__name__, exc)
        return {"ok": True}

    def run(actor):
        try:
            task_id = tasks.submit(pid, "probe_actor", job, actor=actor) if actor is not None \
                else tasks.submit(pid, "probe_actor", job)
        except TypeError as exc:
            return {"error": "TypeError: %s" % exc}
        deadline = time.time() + 30
        while time.time() < deadline:
            task = store.list_tasks(pid)
            for item in task:
                if str(item.get("task_id")) == str(task_id) and item.get("status") in {
                        "succeeded", "failed", "partial", "interrupted", "cancelled"}:
                    return {"task_id": task_id, "status": item.get("status")}
            time.sleep(0.1)
        return {"task_id": task_id, "status": "timeout"}

    out["task_with_actor"] = run("alice")
    out["task_with_actor_seen"] = dict(seen)
    seen.clear()
    if user_llm is not None:
        user_llm.set_model("alice", "")
    out["task_without_actor"] = run(None)
    out["task_without_actor_seen"] = dict(seen)
except Exception as exc:  # noqa: BLE001
    out["tasks_probe_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 7. HTTP 面：账号隔离 + 明文不外泄 ---
try:
    from starlette.testclient import TestClient
    import tech_app.backend.main as main
    from tech_app.backend.main import current_user

    CURRENT = {"user": {"username": "alice", "role": "viewer", "display_name": "Alice"}}
    main.app.dependency_overrides[current_user] = lambda: dict(CURRENT["user"])
    client = TestClient(main.app, raise_server_exceptions=False)

    def http(method, path, payload=None):
        response = client.request(method, path, json=payload) if payload is not None \
            else client.request(method, path)
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            body = response.text[:300]
        return {"status": response.status_code, "text": response.text[:2000], "body": body}

    out["http_alice_get"] = http("GET", "/api/my/settings")
    out["http_alice_put_model"] = http("PUT", "/api/my/settings", {"model": "deepseek-v4-pro"})
    out["http_alice_get_after"] = http("GET", "/api/my/settings")
    out["http_bob_get"] = None
    CURRENT["user"] = {"username": "bob", "role": "viewer", "display_name": "Bob"}
    out["http_bob_get"] = http("GET", "/api/my/settings")
    out["http_bob_spoof"] = http("PUT", "/api/my/settings",
                                 {"model": "qwen3.8-max", "username": "alice"})
    out["http_bob_after_spoof"] = http("GET", "/api/my/settings")
    CURRENT["user"] = {"username": "alice", "role": "viewer", "display_name": "Alice"}
    out["http_alice_after_spoof"] = http("GET", "/api/my/settings")
    out["http_alice_put_key"] = http("PUT", "/api/my/settings",
                                     {"api_key": "ACCOUNT-QWEN-KEY-0002", "api_key_provider": "qwen"})
    out["http_alice_delete_key"] = http("DELETE", "/api/my/settings/keys/qwen")
    out["http_global_settings_alice"] = http("GET", "/api/settings")
    out["http_me"] = http("GET", "/api/me")
    CURRENT["user"] = {"username": "bob", "role": "admin", "display_name": "Bob"}
    out["http_users_bob_admin"] = http("GET", "/api/users")
    # C11：账号级改动必须留审计，只记字段名与"是否改了密钥"，不记值
    out["audit_global"] = store.list_audit("_global")
except Exception as exc:  # noqa: BLE001
    out["http_probe_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 8. C4 白名单：非法模型 / provider 明确拒绝，且不静默改写 ---
def attempt(fn):
    try:
        return {"value": fn()}
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


if user_llm is not None:
    try:
        user_llm.set_model("alice", ACCOUNT_MODEL)
        out["bad_model_set"] = attempt(lambda: user_llm.set_model("alice", "no-such-model-v0"))
        out["alice_after_bad_model"] = user_llm.get("alice")
        out["bad_provider_set"] = attempt(
            lambda: user_llm.set_key("alice", "no-such-provider", "sk-should-be-rejected"))
        out["alice_after_bad_provider"] = user_llm.get("alice")
        out["clear_model_allowed"] = attempt(lambda: user_llm.set_model("alice", ""))
        out["clear_missing_key_allowed"] = attempt(lambda: user_llm.set_key("alice", "qwen", ""))
    except Exception as exc:  # noqa: BLE001
        out["whitelist_probe_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 9. C7 会话按发起账号切路由（stub 掉 oc_agent，只看是否重建 client） ---
try:
    _oc_stub = types.ModuleType("tech_app.backend.services.oc_agent")
    APPLIED = []

    def _apply_settings(params, *, rebuild_client=False):
        APPLIED.append({"rebuild": bool(rebuild_client),
                        "agent_model": params.get("agent_model")})

    _oc_stub.apply_settings = _apply_settings
    sys.modules["tech_app.backend.services.oc_agent"] = _oc_stub
    # `from . import oc_agent` 只在包属性缺失时才走 import 机器，所以两处都要占位，
    # 否则真实 oc_agent 会被 import 进来（或者被 sync_live_agents 的 except 吞掉）。
    import tech_app.backend.services as _services_pkg
    _services_pkg.oc_agent = _oc_stub

    try:
        from tech_app.backend.services import acting_user as _acting
    except Exception:                                          # noqa: BLE001
        _acting = None
    out["session_route_acting_module"] = _acting is not None

    if user_llm is not None:
        user_llm.set_model("bob", "")
        user_llm.set_model("alice", ACCOUNT_MODEL)

    def sync(username, via_kwarg):
        if _acting is not None:
            _acting.set_acting_user(username)
        before = len(APPLIED)
        try:
            if via_kwarg:
                llm_settings.sync_live_agents(user=username)
            else:
                llm_settings.sync_live_agents()
        except TypeError as exc:
            return {"error": "TypeError: %s" % exc}
        if len(APPLIED) == before:
            return {"error": "sync_live_agents 没有把路由推给任何会话"}
        return {"rebuild": APPLIED[-1]["rebuild"], "model": APPLIED[-1]["agent_model"]}

    via_kwarg = True
    first = sync("alice", via_kwarg)
    if first.get("error"):
        via_kwarg = False
        first = sync("alice", via_kwarg)
    out["session_route_mode"] = "user=" if via_kwarg else "acting-context"
    out["session_route_alice"] = first
    out["session_route_bob"] = sync("bob", via_kwarg)
    out["session_route_bob_again"] = sync("bob", via_kwarg)
    out["session_route_alice_again"] = sync("alice", via_kwarg)
except Exception as exc:  # noqa: BLE001
    out["session_route_probe_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 10. C14 账号级文件损坏时安全降级（不许 500、不许中断对话） ---
try:
    store_file = pathlib.Path(data_dir) / "_user_llm.json"
    backup = store_file.read_bytes() if store_file.exists() else None
    store_file.write_text("{ not json at all", encoding="utf-8")
    out["corrupt_get"] = attempt(lambda: user_llm.get("alice"))
    out["corrupt_summary"] = attempt(lambda: user_llm.summary("alice"))
    out["corrupt_route"] = route("alice")
    if backup is not None:
        store_file.write_bytes(backup)
    out["corrupt_restored"] = store_file.exists()
except Exception as exc:  # noqa: BLE001
    out["corrupt_probe_error"] = "%s: %s" % (type(exc).__name__, exc)

out["global_file_after"] = (global_path.read_bytes() if global_path.exists() else b"").hex()[:64]
out["saved_global_configs_total"] = len(_global_saves)
print(json.dumps(out, ensure_ascii=False, default=str))
'''


def py_body(src: str, name: str) -> str:
    """取某个顶层函数的函数体（到下一个顶层 def/class 为止）。"""
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


def backend_python() -> str:
    candidates = [sys.executable, str(ROOT / "open-claude/.venv/bin/python"),
                  shutil.which("python3"), shutil.which("python")]
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", "import fastapi, pydantic, starlette"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return ""


@functools.lru_cache(maxsize=None)
def probe() -> dict:
    python = backend_python()
    if not python:
        raise unittest.SkipTest("没有可 import fastapi/pydantic 的解释器，跳过账号级模型走查")
    data_dir = tempfile.mkdtemp(prefix="cpq-account-llm-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-account-llm-script-")
    try:
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run([python, str(script), data_dir, str(ROOT)],
                                   capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "账号级模型走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout[-2000:], completed.stderr[-4000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(script_dir, ignore_errors=True)


class AccountModelBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = probe()

    def case(self, name: str):
        value = self.data.get(name)
        if isinstance(value, dict) and value.get("error"):
            self.fail(f"{name} 报错：{value['error']}")
        return value


class AccountModelResolutionRedTest(AccountModelBase):
    """C1/C2/C9：账号级模型/密钥优先，全局兜底，缺 Key 不静默降级。"""

    def test_account_module_and_isolated_store_exist(self):
        self.assertTrue(self.data.get("user_llm_module") is True,
                        f"缺少账号级设置模块：{self.data.get('user_llm_module')}")
        self.assertNotIn("user_llm_probe_error", self.data, self.data.get("user_llm_probe_error"))
        self.assertTrue(self.data.get("store_path_exists"), "账号级设置必须落在 DATA_DIR 下的文件")
        self.assertEqual(self.data.get("store_mode"), "0o600", "账号级设置文件必须是 0600")
        self.assertEqual(self.data.get("saved_global_configs"), 0,
                         "写账号级设置不得触碰全局 cpq_settings.json")

    def test_resolve_prefers_account_model_then_global(self):
        self.assertEqual(self.case("route_no_user").get("model"), "qwen3.5-plus",
                         "没有发起账号时必须用全局默认模型")
        self.assertEqual(self.case("route_alice").get("model"), ACCOUNT_MODEL,
                         "alice 设了自己的模型就必须按自己的解析")
        self.assertEqual(self.case("route_bob").get("model"), "qwen3.5-plus",
                         "bob 没设置，必须仍然用全局默认（不能串号）")

    def test_account_key_wins_and_global_key_is_untouched(self):
        route = self.case("route_alice_own_key") or {}
        self.assertEqual(route.get("model"), ACCOUNT_MODEL_WITH_KEY)
        self.assertEqual(route.get("api_key"), ACCOUNT_QWEN_KEY,
                         "alice 配了自己的 Key 就必须用自己那把")
        self.assertEqual(self.case("route_bob_global_key").get("api_key"), GLOBAL_QWEN_KEY,
                         "bob 没有自己的 Key，必须回落到全局兜底 Key")
        self.assertEqual(self.case("global_keys_after_account_write"),
                         {"qwen": GLOBAL_QWEN_KEY, "deepseek": GLOBAL_DS_KEY},
                         "账号级写入不得改动全局 Key")

    def test_missing_key_fails_loudly_instead_of_falling_back(self):
        result = self.data.get("route_carol_missing_key") or {}
        self.assertTrue(result.get("error"), f"选了没有 Key 的模型必须报错，实际：{result}")
        self.assertIn("deepseek", str(result.get("error")).lower(),
                      f"错误必须指明缺哪个 provider 的 Key：{result.get('error')}")
        self.assertNotEqual(result.get("model"), "qwen3.5-plus",
                            "缺 Key 时不得静默改用全局模型")

    def test_summary_masks_keys(self):
        summary = json.dumps(self.case("alice_summary_after_key"), ensure_ascii=False)
        self.assertNotIn(ACCOUNT_QWEN_KEY, summary, "账号级 Key 明文不得出现在任何摘要里")
        self.assertIn("qwen", summary, "摘要应含该账号各 provider 的配置状态")

    def test_clearing_account_settings_falls_back_to_global(self):
        cleared = self.case("route_alice_after_clear") or {}
        self.assertEqual(cleared.get("model"), "qwen3.5-plus")
        self.assertEqual(cleared.get("api_key"), GLOBAL_QWEN_KEY)

    def test_effective_reports_model_and_source(self):
        self.assertNotIn("effective_error", self.data, self.data.get("effective_error"))
        self.assertEqual((self.case("effective_no_user") or {}).get("source"), "global")
        self.assertEqual((self.case("effective_no_user") or {}).get("model"), "qwen3.5-plus")

    def test_vision_capability_uses_effective_model(self):
        result = self.data.get("vision_alice") or {}
        self.assertTrue(result.get("error"), f"账号模型不支持图像时必须报错，实际：{result}")
        self.assertIn(ACCOUNT_MODEL, str(result.get("error")),
                      "报错必须带生效模型名，且不得静默换模型")


class AccountModelChannelRedTest(AccountModelBase):
    """C6：发起账号的传递（上下文 + 异步任务）。"""

    def test_context_account_drives_resolution(self):
        self.assertNotIn("acting_user_error", self.data, self.data.get("acting_user_error"))
        self.assertEqual(self.data.get("acting_user_value"), "alice")
        self.assertEqual(self.case("route_ctx_alice").get("model"), ACCOUNT_MODEL,
                         "服务层没有显式传账号时必须读上下文里的发起账号")
        self.assertEqual(self.case("route_ctx_cleared").get("model"), "qwen3.5-plus")

    def test_task_actor_is_propagated_to_worker(self):
        self.assertNotIn("tasks_probe_error", self.data, self.data.get("tasks_probe_error"))
        with_actor = self.case("task_with_actor")
        self.assertEqual(with_actor.get("status"), "succeeded", with_actor)
        seen = self.case("task_with_actor_seen")
        self.assertEqual(seen.get("actor"), "alice",
                         "worker 线程里必须能取到发起人（tasks.submit(actor=...)）")
        self.assertEqual(seen.get("model"), ACCOUNT_MODEL,
                         "异步任务必须按发起人的账号模型解析，而不是全局默认")

    def test_task_without_actor_uses_global(self):
        seen = self.case("task_without_actor_seen")
        self.assertEqual(seen.get("model"), "qwen3.5-plus",
                         "没有发起人的任务（定时 / 系统重跑）必须回落到全局默认")


class AccountModelHttpRedTest(AccountModelBase):
    """C5/C10：账号级接口只作用于本人，且不外泄明文。"""

    def test_my_settings_round_trip(self):
        self.assertEqual(self.case("http_alice_get").get("status"), 200,
                         self.case("http_alice_get"))
        put = self.case("http_alice_put_model")
        self.assertIn(put.get("status"), (200, 201), put)
        after = self.case("http_alice_get_after")
        self.assertEqual(after.get("status"), 200, after)

    def test_accounts_do_not_see_each_other(self):
        bob = self.case("http_bob_get")
        self.assertEqual(bob.get("status"), 200, bob)
        text = json.dumps(bob.get("body"), ensure_ascii=False)
        self.assertNotIn(ACCOUNT_MODEL, text, "bob 不得看到 alice 的账号级模型")

    def test_body_username_cannot_target_another_account(self):
        spoof = self.case("http_bob_spoof")
        self.assertIn(spoof.get("status"), (200, 201, 400, 403), spoof)
        alice = self.case("http_alice_after_spoof")
        self.assertNotIn("qwen3.8-max", json.dumps(alice.get("body"), ensure_ascii=False),
                         "请求体里的 username 不得把设置写到别人头上")

    def test_key_lifecycle_and_no_plaintext(self):
        put_key = self.case("http_alice_put_key")
        self.assertIn(put_key.get("status"), (200, 201), put_key)
        self.assertNotIn(ACCOUNT_QWEN_KEY, json.dumps(put_key.get("body"), ensure_ascii=False),
                         "保存接口不得回显明文 Key")
        deleted = self.case("http_alice_delete_key")
        self.assertIn(deleted.get("status"), (200, 204), deleted)

    def test_responses_never_contain_any_plaintext_key(self):
        for name in ("http_alice_get", "http_alice_get_after", "http_global_settings_alice",
                     "http_me", "http_users_bob_admin"):
            payload = self.data.get(name)
            if not isinstance(payload, dict):
                continue
            text = str(payload.get("text") or "")
            for secret in (GLOBAL_QWEN_KEY, GLOBAL_DS_KEY, ACCOUNT_QWEN_KEY):
                self.assertNotIn(secret, text, f"{name} 响应里出现了明文 Key")

    def test_global_settings_are_still_global_and_readable(self):
        payload = self.case("http_global_settings_alice")
        self.assertEqual(payload.get("status"), 200, payload)
        body = payload.get("body")
        self.assertIsInstance(body, dict, body)
        self.assertIn("mine", body, "全局设置接口必须附上当前账号的账号级摘要")
        self.assertIn("effective_model", body, "全局设置接口必须给出当前账号的生效模型")


class AccountModelWhitelistRedTest(AccountModelBase):
    """C3/C4：白名单仍只有 llm_settings 一份；非法值明确拒绝、不静默改写。"""

    def test_invalid_model_is_rejected_and_not_written(self):
        self.assertIn("bad_model_set", self.data,
                      "账号级设置模块不可用：%s" % self.data.get("user_llm_module"))
        result = self.data.get("bad_model_set") or {}
        self.assertIn("error", result,
                      f"账号选一个不存在的模型必须明确拒绝，实际：{result}")
        self.assertIn("ValueError", str(result.get("error")),
                      f"拒绝方式应是 ValueError（不是静默改写）：{result.get('error')}")
        after = self.data.get("alice_after_bad_model")
        self.assertIsInstance(after, dict, f"读不到 alice 的账号级设置：{after}")
        self.assertNotIn(BAD_MODEL, json.dumps(after, ensure_ascii=False),
                         "非法模型不得被写进账号级设置")

    def test_invalid_provider_is_rejected_and_no_key_written(self):
        self.assertIn("bad_provider_set", self.data,
                      "账号级设置模块不可用：%s" % self.data.get("user_llm_module"))
        result = self.data.get("bad_provider_set") or {}
        self.assertIn("error", result, f"未知 provider 必须明确拒绝，实际：{result}")
        self.assertIn("ValueError", str(result.get("error")))
        after = self.data.get("alice_after_bad_provider") or {}
        blob = json.dumps(after, ensure_ascii=False)
        self.assertNotIn(BAD_PROVIDER, blob, "非法 provider 不得落盘")
        self.assertNotIn("sk-should-be-rejected", blob, "被拒绝的 Key 值不得落盘")

    def test_clearing_is_still_allowed(self):
        self.assertIn("clear_model_allowed", self.data,
                      "账号级设置模块不可用：%s" % self.data.get("user_llm_module"))
        self.assertIn("value", self.data.get("clear_model_allowed") or {},
                      f"空串 = 清除个人模型，必须允许：{self.data.get('clear_model_allowed')}")
        self.assertIn("value", self.data.get("clear_missing_key_allowed") or {},
                      f"删除一个本来就没有的个人 Key 必须允许："
                      f"{self.data.get('clear_missing_key_allowed')}")

    def test_whitelist_comes_from_llm_settings(self):
        src = read(USER_LLM_PY) if USER_LLM_PY.exists() else ""
        self.assertTrue(src, f"缺少 {USER_LLM_PY.relative_to(ROOT)}")
        for marker in ("MODEL_PROVIDERS", "PROVIDERS"):
            self.assertIn(marker, src,
                          f"账号级设置必须复用 llm_settings.{marker}，不得新建第二份白名单")

    def test_store_is_written_atomically(self):
        src = read(USER_LLM_PY) if USER_LLM_PY.exists() else ""
        self.assertTrue(src, f"缺少 {USER_LLM_PY.relative_to(ROOT)}")
        self.assertIn("os.replace", src, "账号级设置必须原子写（临时文件 + os.replace）")
        self.assertIn("0o600", src, "账号级设置文件必须是 0600")


class AccountModelSessionRouteRedTest(AccountModelBase):
    """C7：同一项目里不同账号交替说话，每轮都按自己的生效模型走，且不白重建。"""

    def test_probe_ran(self):
        self.assertFalse(self.data.get("session_route_probe_error"),
                         f"会话路由走查失败：{self.data.get('session_route_probe_error')}")
        self.assertIn("session_route_mode", self.data,
                      f"会话路由走查没有产出结果：{self.data.get('session_route_alice')}")

    def test_alice_turn_uses_alice_model(self):
        first = self.data.get("session_route_alice") or {}
        self.assertFalse(first.get("error"), first)
        self.assertEqual(first.get("model"), ACCOUNT_MODEL,
                         "发起账号是 alice 时，会话必须按 alice 的模型建/重建 client")

    def test_switching_account_rebuilds_client(self):
        bob = self.data.get("session_route_bob") or {}
        self.assertFalse(bob.get("error"), bob)
        self.assertEqual(bob.get("model"), "qwen3.5-plus",
                         "bob 没有个人模型，必须回落全局模型")
        self.assertTrue(bob.get("rebuild"),
                        "从 alice 切到 bob 路由变了，必须重建 client，"
                        "否则会用上一个人的模型与 Key")

    def test_same_account_does_not_rebuild_every_turn(self):
        again = self.data.get("session_route_bob_again") or {}
        self.assertFalse(again.get("error"), again)
        self.assertFalse(again.get("rebuild"),
                         "同一账号连续两轮路由没变，不该每轮都重建 client")

    def test_route_comparison_includes_the_account(self):
        body = py_body(read(LLM_SETTINGS_PY), "_route_matches_applied")
        self.assertTrue(body, "找不到 llm_settings._route_matches_applied")
        found = (re.search(r'"\s*(user|account|actor|acting_user)\s*"', body)
                 or re.search(r"\b(account|actor|acting_user)\b", body))
        self.assertTrue(found,
                        "路由比对必须把发起账号算进去，否则换账号发现不了："
                        + body[:240].replace("\n", " "))


class AccountModelAuditRedTest(AccountModelBase):
    """C11：账号级改动留审计，只记字段名与"是否改了密钥"，不记任何值。"""

    def test_account_settings_change_is_audited(self):
        entries = self.data.get("audit_global")
        self.assertIsInstance(entries, list, f"读不到审计记录：{entries}")
        actions = [str(item.get("action") or "") for item in entries if isinstance(item, dict)]
        self.assertIn("user_llm_settings_update", actions,
                      "账号级模型/密钥改动必须写审计（_global + "
                      "user_llm_settings_update），实际动作：%s" % actions)

    def test_audit_never_records_secret_values(self):
        text = json.dumps(self.data.get("audit_global") or [], ensure_ascii=False)
        for secret in (GLOBAL_QWEN_KEY, GLOBAL_DS_KEY, ACCOUNT_QWEN_KEY,
                       "sk-should-be-rejected"):
            self.assertNotIn(secret, text, "审计里出现了明文 Key")


class AccountModelResilienceRedTest(AccountModelBase):
    """C14：账号级文件损坏时安全降级为"没有账号级设置"，不抛错、不中断对话。"""

    def test_corrupt_store_degrades_to_global(self):
        self.assertFalse(self.data.get("corrupt_probe_error"),
                         self.data.get("corrupt_probe_error"))
        self.assertIn("corrupt_get", self.data,
                      "账号级设置模块不可用：%s" % self.data.get("user_llm_module"))
        got = self.data.get("corrupt_get")
        self.assertIsInstance(got, dict, got)
        self.assertNotIn("error", got, f"账号级文件损坏不得抛错：{got}")
        self.assertEqual(got.get("value"), {}, "读不出来时应当作「没有账号级设置」")

    def test_corrupt_store_still_resolves_global(self):
        route = self.data.get("corrupt_route") or {}
        self.assertFalse(route.get("error"), route)
        self.assertEqual(route.get("model"), "qwen3.5-plus",
                         "损坏时必须回落全局模型，而不是让对话/任务报错")

    def test_corrupt_store_summary_does_not_raise(self):
        got = self.data.get("corrupt_summary")
        self.assertIsInstance(got, dict, got)
        self.assertNotIn("error", got, f"损坏时 summary 也要能返回空摘要：{got}")


class AccountModelSourceRedTest(unittest.TestCase):
    """源码契约：发起账号通道、进度文案、前端面板与边界。"""

    @classmethod
    def setUpClass(cls):
        cls.panel = read(FRONTEND / "llm-settings-panel.js")
        cls.main = read(MAIN_PY)
        cls.tasks = read(TASKS_PY)
        cls.llm_settings = read(LLM_SETTINGS_PY)
        cls.auth = read(AUTH_PY)

    def test_spec_document_exists(self):
        self.assertTrue(SPEC.exists(), "缺少 docs/specs/per-account-model-and-api-key.md")

    def test_auth_guard_sets_acting_account(self):
        self.assertTrue("acting_user" in self.main,
                        "main.py 的 auth_guard 必须把发起账号设入上下文")
        for marker in ("/api/my/settings", "api_key_provider", "current_user"):
            self.assertTrue(marker in self.main, f"main.py 缺少 {marker}")

    def test_acting_user_module_interface(self):
        src = read(ACTING_USER_PY) if ACTING_USER_PY.exists() else ""
        self.assertTrue(src, f"缺少 {ACTING_USER_PY.relative_to(ROOT)}")
        self.assertIn("def set_acting_user", src)
        self.assertIn("def current_acting_user", src)

    def test_user_llm_module_interface(self):
        src = read(USER_LLM_PY) if USER_LLM_PY.exists() else ""
        self.assertTrue(src, f"缺少 {USER_LLM_PY.relative_to(ROOT)}")
        for name in ("def get(", "def set_model(", "def set_key(", "def summary("):
            self.assertIn(name, src, f"user_llm 缺少接口 {name}（名字按 Spec §6，不得改名）")

    def test_task_actor_parameter_exists(self):
        head = self.tasks.split("def submit(", 1)[-1].split("def ", 1)[0]
        self.assertTrue("actor" in head, "tasks.submit 必须接受 actor 参数")
        self.assertTrue("actor" in self.tasks, "worker 内必须按 actor 设入发起账号")

    def test_progress_text_uses_effective_model(self):
        stale = "调用多模态模型解析图纸（{llm_settings.snapshot()['model']}）"
        self.assertTrue(stale not in self.main,
                        "图纸解析进度文案必须显示生效模型，而不是全局默认")
        effective = re.search(r"调用多模态模型解析图纸（[^）]*）", self.main)
        self.assertIsNotNone(effective, "找不到图纸解析进度文案")
        self.assertTrue("effective" in effective.group(0),
                        f"进度文案必须取生效模型：{effective.group(0)}")

    def test_panel_has_my_settings_section(self):
        self.assertTrue("/api/my/settings" in self.panel, "面板必须读写 /api/my/settings")
        self.assertTrue("我的模型" in self.panel, "面板必须区分「我的模型」")
        self.assertTrue("平台默认" in self.panel, "面板必须写清「平台默认」兜底")
        self.assertTrue(re.search(r"404|405|notFound|not_found", self.panel),
                        "报价端没有该接口时必须能优雅隐藏，不报错")

    def test_panel_shows_effective_model_and_mine_source(self):
        self.assertIn("effective_model", self.panel,
                      "面板/页头必须显示生效模型，而不是只显示全局默认")
        self.assertIn("我的", self.panel, "来源是账号设置时要标注「我的」")

    def test_panel_keeps_global_permissions(self):
        self.assertTrue("secrets_editable" in self.panel)
        self.assertTrue("editable" in self.panel)
        self.assertTrue(re.search(r'input\.value\s*=\s*""', self.panel))

    def test_no_revived_two_model_fields(self):
        # 面板里不得再出现双模型字段；服务层不得把它们当成状态位读写。
        for field in ("vision_model", "text_model"):
            self.assertTrue(field not in self.panel, f"面板里不该再有 {field}")
            self.assertTrue(f'["{field}"]' not in self.llm_settings,
                            f"llm_settings 不该再把 {field} 当状态读写")

    def test_user_views_do_not_carry_account_llm(self):
        self.assertTrue(re.search(r"def public_user", self.auth))
        public = self.auth.split("def public_user", 1)[1].split("def ", 1)[0]
        self.assertTrue("api_key" not in public, "用户视图不得带出账号级密钥字段")
        self.assertTrue("llm" not in public, "用户视图不得带出账号级 llm 字段")


if __name__ == "__main__":
    unittest.main()
