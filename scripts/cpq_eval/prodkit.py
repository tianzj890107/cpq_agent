# -*- coding: utf-8 -*-
"""真实生产代码入口（production_unit / production_http 层的唯一装载点）。

为什么要有这一层：数据集里 200+ 条 `simulation` 案例由 runner 自建的状态机执行，
案例、规则、执行实现是同一套代码 —— 真实 FastAPI 路由 / ACL / 事务 / Agent 工具链回归了，
Sim 仍然会全绿。本模块负责把 **真实生产模块** 装进同一个进程，让 `production_unit` 与
`production_http` 两层的案例真的跑在 `tech_app/backend/**` 的代码上。

三条硬约束：

1. **不写仓库**：`DATA_DIR` 指向进程级临时目录（`tempfile.mkdtemp`），项目元数据用真实
   的 `JsonMetaBackend`，但落点是每个案例自己的子目录。仓库里的 `tech_app/tech_data/`
   与运行中的业务数据一概不读、不写、不删。
2. **不出网**：`CPQ_SSO=1` 打开真实鉴权链路，但 `cpq_sso._fetch`（唯一回调 8010 的出口）
   在 http client 里被替换成固定身份表。真实模型与真实登录服务都不会被访问。
3. **不复制生产判断**：这里只做装载、数据落库、身份构造与路由清单采集；任何「谁能看 /
   谁能写 / 谁能做这一步」的判断一律调用生产模块自己的函数。
"""
from __future__ import annotations

import ast
import atexit
import contextlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TECH_APP = ROOT / "tech_app"

PROD_ENV = {
    "CPQ_SSO": "1",
    "AUTH_ENABLED": "false",
    "AUTH_AUTO_ADMIN": "false",
    "STORAGE_BACKEND": "json",
    "CPQ_AUTH_BASE_URL": "",
    # 与生产启动一致，但**不**在测试里跑中断任务恢复（空临时库上无意义，还会起线程）。
    "TASK_RECOVER_ON_START": "false",
}
# 会被案例数据 / 环境误设、必须显式钉死的键（防止数据集的 expected 被真实环境"帮"成通过）。
PINNED_ENV_KEYS = ("DATA_DIR", "DATABASE_URL")

_MAIN_SRC = TECH_APP / "backend" / "main.py"

_state: dict = {
    "loaded": False, "modules": {}, "data_dir": "", "routemap": None,
    "local_data_dir": "",
}
_at_exit: list = []


# --------------------------------------------------------------------------- #
# 装载
# --------------------------------------------------------------------------- #
def _pinned_data_dir() -> str:
    """进程级临时 DATA_DIR：import 前必须设好，否则 config 会 mkdir 到 tech_app/data。"""
    if not _state["data_dir"]:
        base = _state.get("local_data_dir") or tempfile.mkdtemp(prefix="cpq_eval_prod_")
        _state["data_dir"] = base
        if not _state.get("local_data_dir"):
            _at_exit.append(base)
    return _state["data_dir"]


def _install_cleanup() -> None:
    def _clean():
        for path in list(_at_exit):
            if path:
                shutil.rmtree(path, ignore_errors=True)
        _at_exit.clear()

    atexit.register(_clean)


_CLEANUP_REGISTERED = False


def _pin_env() -> str:
    """把隔离用的环境变量钉死（`DATA_DIR` + `PROD_ENV`）。"""
    root = _pinned_data_dir()
    os.environ["DATA_DIR"] = root
    for key, value in PROD_ENV.items():
        os.environ[key] = value
    return root


def isolate_process(temp_root: str = "") -> str:
    """把 `DATA_DIR` 指到临时目录（必须在装载任何生产模块之前调用）。

    返回 ``true_data_dir``：注意生产 `config.py` 计算 ``DATA_DIR = Path(os.getenv(...))``，
    所以这里给的是临时目录本身。

    同时**记下这些变量的原值**：`CPQ_SSO=1` 这类开关一旦留在父进程里，会被同进程其他
    测试的子进程继承（历史上就把另一个红测的子进程探针挂死过）。用 ``pinned_env()`` /
    ``restore_process_env()`` 在真实执行窗口之外把环境放回去。
    """
    global _CLEANUP_REGISTERED
    if temp_root:
        _state["local_data_dir"] = str(temp_root)
    if "env_backup" not in _state:
        _state["env_backup"] = {key: os.environ.get(key) for key in ("DATA_DIR",) + tuple(PROD_ENV)}
    root = _pin_env()
    if not _CLEANUP_REGISTERED:
        _install_cleanup()
        _CLEANUP_REGISTERED = True
    return root


def restore_process_env() -> None:
    """把隔离时改过的环境变量放回原值（没有原值的就删掉）。

    生产执行器在每条案例结束时调用它，避免把 `CPQ_SSO` 等开关泄漏给同进程的其他测试
    （它们可能是别人的红测，会起子进程）。
    """
    backup = _state.get("env_backup") or {}
    for key, value in backup.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@contextlib.contextmanager
def pinned_env():
    """真实执行窗口内钉死隔离环境，退出时放回原值。"""
    isolate_process()
    try:
        yield
    finally:
        restore_process_env()


def load() -> dict:
    """装载真实生产模块。幂等；返回 ``{名字: 模块}``。

    已经装载过也会**重新钉死隔离环境**：同进程的其他测试模块可能把环境放回了原值
    （见 ``restore_process_env``），这里是每次进入生产层的统一入口。
    """
    if _state["loaded"]:
        _pin_env()
        return _state["modules"]
    isolate_process()
    for path in (str(TECH_APP), str(ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)
    modules = {}
    for alias, name in (
        ("config", "backend.config"),
        ("store", "backend.storage.store"),
        ("meta_backend", "backend.storage.meta_backend"),
        ("auth", "backend.services.auth"),
        ("cpq_sso", "backend.services.cpq_sso"),
        ("project_access", "backend.services.project_access"),
        ("integration", "backend.services.integration"),
        ("workflow_stages", "backend.services.workflow_stages"),
        ("llm_output", "backend.services.llm_output"),
        ("oc_agent", "backend.services.oc_agent"),
        ("tasks", "backend.services.tasks"),
        ("main", "backend.main"),
    ):
        modules[alias] = importlib.import_module(name)
    _state["modules"] = modules
    _state["loaded"] = True
    # 生产是在 lifespan 启动钩子里跑 housekeeping（CPQ 单经理模式等）。TestClient 默认
    # 不跑 lifespan，若不显式调用，测出来的是「没启动过的服务」—— 与部署形态不一致。
    main = modules["main"]
    housekeeping = getattr(main, "_startup_housekeeping", None)
    if callable(housekeeping):
        housekeeping()
    return modules


def modules() -> dict:
    return _state["modules"] if _state["loaded"] else load()


def module(name: str):
    """按需装载额外的真实生产模块（``cpq_wf`` / ``cpq_case_link`` /
    ``backend.services.workflow_projection`` …）。

    不放进 ``load()`` 的固定清单：这几支只在少数案例里用得上，放进固定清单会让
    「某一支 import 失败」把整个 production 层判成 skipped。
    """
    key = str(name or "").strip()
    if not key:
        raise ValueError("module 名字为空")
    loaded = load()
    for prefix in ("tech_app.",):
        if key.startswith(prefix):
            key = key[len(prefix):]
    if key in loaded:
        return loaded[key]
    value = importlib.import_module(key)
    loaded[key] = value
    return value


def available() -> tuple:
    """(可用?, 原因)：production 层的案例在缺依赖时必须 skip，不能假绿也不能假红。"""
    try:
        load()
    except Exception as exc:  # pragma: no cover - 本机依赖齐全时才走到 else
        return False, f"生产模块不可装载（{type(exc).__name__}：{exc}）"
    return True, ""


# --------------------------------------------------------------------------- #
# 隔离存储：真实 JsonMetaBackend，落点在每个案例自己的目录
# --------------------------------------------------------------------------- #
def fresh_dir_for(name: str) -> str:
    """一个进程内临时落点（进程退出时递归清理）；只用于测试，绝不落在仓库里。"""
    base = tempfile.mkdtemp(prefix=f"cpq_eval_{name}_")
    _at_exit.append(base)
    return base


def fresh_store(case_dir: str):
    """把 store 的元数据后端换成指向 ``case_dir`` 的真实 JsonMetaBackend。

    只用真实后端、只换落点：`store.load_meta / save_business_case / list_projects / ...`
    与 `project_access` 走的都是生产代码，判断逻辑一行都没有被替换。
    """
    mods = modules()
    meta_backend = mods["meta_backend"]
    path = Path(case_dir)
    path.mkdir(parents=True, exist_ok=True)
    backend = meta_backend.JsonMetaBackend(path)
    meta_backend._backend = backend          # 真实后端的模块级缓存，换掉即完成隔离
    return backend


# --------------------------------------------------------------------------- #
# 身份：一律过真实映射，runner 不许凭用户名猜角色
# --------------------------------------------------------------------------- #
def make_identity(spec: dict) -> dict:
    """按案例声明的 ``sso_roles / cpq_role_code / tech_role`` 造真实用户视图。

    * 声明了 ``cpq_role_code`` → 过 **真实** ``cpq_sso.to_tech_user``（角色映射唯一来源）；
    * 只声明 ``tech_role`` → 造技术工艺本地用户视图（`auth` 口径）；
    * 两者都缺 → 报错，不猜。
    """
    spec = spec or {}
    mods = modules()
    cpq_sso = mods["cpq_sso"]
    username = str(spec.get("username") or "").strip()
    if not username:
        raise ValueError("identity 缺少 username")
    role_code = str(spec.get("cpq_role_code") or "").strip()
    if role_code:
        user = cpq_sso.to_tech_user({
            "username": username,
            "role_code": role_code,
            "role_name": spec.get("cpq_role_name") or role_code,
            "user_id": spec.get("cpq_user_id") or f"uid-{username}",
            "display_name": spec.get("display_name") or username,
        })
    else:
        tech_role = str(spec.get("tech_role") or "").strip()
        if not tech_role:
            raise ValueError(f"identity {username} 既没有 cpq_role_code 也没有 tech_role")
        user = {
            "username": username, "role": tech_role,
            "display_name": spec.get("display_name") or username,
            "requested_role": tech_role, "is_system": False,
        }
    if spec.get("sso_roles"):
        user["sso_roles"] = list(spec["sso_roles"])
    return user


def identity_audit(identities: dict) -> dict:
    """真实角色映射的核对数据（反例断言用，不是豁免表）。"""
    cpq_sso = modules()["cpq_sso"]
    out = {}
    for key, spec in (identities or {}).items():
        spec = spec or {}
        code = str(spec.get("cpq_role_code") or "")
        out[key] = {
            "username": spec.get("username"),
            "cpq_role_code": code,
            "mapped_tech_role": cpq_sso.ROLE_MAP.get(code, ""),
            "effective_role": make_identity(spec).get("role"),
        }
    return out


# --------------------------------------------------------------------------- #
# 项目落库：走真实 store / integration API，不手写 JSON
# --------------------------------------------------------------------------- #
def seed_projects(specs: dict) -> dict:
    """按案例声明把项目落到隔离存储，返回 ``{key: 12 位项目号}``。

    只用真实写入 API（``store.create_project`` / ``integration.save_plan`` /
    ``store.save_business_case`` / ``store.add_participant`` / ``store.archive_project``）。
    """
    mods = modules()
    store = mods["store"]
    integration = mods["integration"]
    ids: dict = {}
    for key, spec in (specs or {}).items():
        spec = spec or {}
        owner = str(spec.get("owner") or "alice")
        display = str(spec.get("owner_display_name") or owner)
        name = str(spec.get("filename") or f"{key}.dxf")
        pid = str(spec.get("project_id") or "").strip()
        if not re.match(r"^[0-9a-f]{12}$", pid):
            pid = store.create_project(name, b"cpq-eval", str(spec.get("note") or key),
                                       owner, display)
        ids[key] = pid
        if spec.get("plan") is not None:
            current = integration.load_plan(pid)
            merged = current.model_dump()
            merged.update(dict(spec["plan"] or {}))
            # 过真实 pydantic 模型校验：嵌套字段（finance_handoff 等）建模型而不是塞 dict，
            # 否则序列化会退化成 dict 并触发 pydantic 告警。
            plan = type(current).model_validate({**merged, "project_id": pid})
            integration.save_plan(pid, plan, author="cpq-eval")
        for row in spec.get("participants") or []:
            store.add_participant(pid, str(row.get("username") or ""),
                                  role=str(row.get("role") or ""),
                                  source=str(row.get("source") or "manual"),
                                  assignee=bool(row.get("assignee")),
                                  author="cpq-eval")
        if spec.get("business_case") is not None:
            store.save_business_case(pid, dict(spec["business_case"] or {}),
                                     author=str(spec.get("business_case_author") or "cpq-eval"))
        if spec.get("hold") is not None:
            store.set_current_holder(pid, str(spec["hold"]), author="cpq-eval")
        if spec.get("archive"):
            store.archive_project(pid, author=str(spec.get("archive_author") or "cpq-eval"))
    return ids


def project_meta(pid: str) -> dict:
    return modules()["store"].load_meta(pid) or {}


# --------------------------------------------------------------------------- #
# 真实 HTTP 边界：TestClient + 真鉴权链路，只换掉「回调 8010 取身份」这一步
# --------------------------------------------------------------------------- #
def http_client(identities: dict, tokens: dict = None):
    """返回 ``(client, tokens)``：真实 TestClient，身份来自真实 `cpq_sso.to_tech_user`。

    被替换的只有 ``cpq_sso._fetch`` —— 它是唯一会访问 CPQ 登录服务（8010）的出口。
    ``cpq_sso.resolve`` / ``to_tech_user`` / ``main._cpq_sso_guard`` /
    ``current_user`` / ``project_write_guard`` / 各路由自己的 ``_require`` 全部是生产代码。
    """
    mods = modules()
    cpq_sso = mods["cpq_sso"]
    from fastapi.testclient import TestClient  # 延迟 import：缺 fastapi 时上层会 skip

    table = {}
    token_map = {}
    for index, (key, spec) in enumerate(sorted((identities or {}).items())):
        token = (tokens or {}).get(key) or f"cpq-eval-{key}-{index}"
        token_map[key] = token
        table[token] = spec
    cpq_sso._cache.clear()

    def _fetch(token: str):
        spec = table.get(str(token or "").strip())
        if not spec:
            return None
        return {
            "username": spec.get("username"),
            "role_code": spec.get("cpq_role_code"),
            "role_name": spec.get("cpq_role_name") or spec.get("cpq_role_code"),
            "user_id": spec.get("cpq_user_id") or f"uid-{spec.get('username')}",
            "display_name": spec.get("display_name") or spec.get("username"),
        }

    cpq_sso._fetch = _fetch
    cpq_sso._cache.clear()
    return TestClient(mods["main"].app), token_map


# --------------------------------------------------------------------------- #
# 真实路由清单（从 main.py 现算，不手抄）
# --------------------------------------------------------------------------- #
_PROJECT_PREFIX = "/api/projects/{project_id}"
_PARAM_RE = re.compile(r"\{[^}]+\}")


class Routes:
    """从真实 ``main.py`` 的 AST 采集路由；用于覆盖矩阵与「新增路由漏测」守卫。"""

    def __init__(self, rows: list):
        self.rows = rows

    @property
    def all(self) -> list:
        return list(self.rows)

    def project(self, method: str = "") -> list:
        want = str(method or "").upper()
        return [row for row in self.rows
                if row["path"].startswith(_PROJECT_PREFIX)
                and (not want or row["method"] == want)]

    def reads(self) -> list:
        return self.project("GET")

    def writes(self) -> list:
        return [row for row in self.project() if row["method"] in
                ("POST", "PUT", "PATCH", "DELETE")]

    def signature(self) -> list:
        return sorted({f"{row['method']} {row['path']}" for row in self.rows})

    def project_signature(self) -> list:
        return sorted({f"{row['method']} {row['path']}"
                       for row in self.project()})


def routes(reload: bool = False) -> Routes:
    """按 AST 现算真实路由表（``main.py`` 是唯一事实源，不手抄数字）。"""
    if _state["routemap"] is not None and not reload:
        return _state["routemap"]
    tree = ast.parse(_MAIN_SRC.read_text(encoding="utf-8"))
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == "app"):
                continue
            if func.attr.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD",
                                         "OPTIONS"):
                continue
            path = ""
            if dec.args and isinstance(dec.args[0], ast.Constant):
                path = str(dec.args[0].value)
            rows.append({
                "method": func.attr.upper(), "path": path, "handler": node.name,
                "line": int(node.lineno),
                "runtime_params": tuple(name.strip("{}")
                                        for name in _PARAM_RE.findall(path)),
            })
    _state["routemap"] = Routes(rows)
    return _state["routemap"]


def probe_read_routes(routes_obj: Routes, client, token: str, pid: str,
                      skip_extra_params: bool = True) -> list:
    """对每个项目级 GET 路由发一次真实请求，返回逐条结果。

    `skip_extra_params=True` 时跳过还带别的路径参数（``{filename}`` / ``{part_id}`` …）的
    路由 —— 那些需要真实业务文件，属于另外的用例；被跳过的路由会在报告里列出来，不静默吞掉。
    """
    results = []
    for row in routes_obj.reads():
        path = row["path"].replace("{project_id}", pid)
        extra = [name for name in row["runtime_params"] if name != "project_id"]
        entry = {"method": "GET", "path": row["path"], "handler": row["handler"],
                 "line": row["line"], "extra_params": extra}
        if extra and skip_extra_params:
            entry["status"] = None
            entry["skipped"] = f"需要额外路径参数 {extra}"
            results.append(entry)
            continue
        try:
            resp = client.get(path, headers={"Authorization": f"Bearer {token}"})
            entry["status"] = int(resp.status_code)
            entry["detail"] = _detail_of(resp)
        except Exception as exc:                       # pragma: no cover - 见 test 报告
            entry["status"] = 0
            entry["detail"] = f"{type(exc).__name__}: {exc}"
        results.append(entry)
    return results


def _detail_of(resp) -> str:
    try:
        body = resp.json()
    except Exception:
        return str(getattr(resp, "text", ""))[:160]
    if isinstance(body, dict):
        return str(body.get("detail") or "")[:160]
    return ""


# --------------------------------------------------------------------------- #
# 路由快照：真实路由增删要让覆盖快照报差异
# --------------------------------------------------------------------------- #
SNAPSHOT_PATH = (Path(__file__).resolve().parents[2]
                 / "dataset" / "evals" / "cpq" / "routes" / "project_routes.json")


def load_snapshot() -> dict:
    if not SNAPSHOT_PATH.exists():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def snapshot_diff() -> dict:
    """真实路由表 vs 快照：``{"added": [...], "removed": [...]}``（都为空才算一致）。"""
    snap = load_snapshot()
    old = set(snap.get("project_routes") or [])
    new = set(routes().project_signature())
    return {"added": sorted(new - old), "removed": sorted(old - new)}


def write_snapshot(extra: dict = None) -> Path:
    payload = {
        "generated_from": "tech_app/backend/main.py",
        "note": "由 scripts/cpq_eval/prodkit.py 现算；真实路由增删必须让覆盖快照报差异。",
        "project_routes": routes().project_signature(),
    }
    if extra:
        payload.update(extra)
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return SNAPSHOT_PATH
