# -*- coding: utf-8 -*-
"""生产代码边界执行器：`production_unit` / `production_http` / `recorded_provider` /
`postgres_integration` 四层案例的真实执行入口。

与 `runner.Sim` 的分工：

  · `simulation`       —— 案例只用来固化业务规格；由 Sim 执行，**不计入发布门禁**。
  · `production_unit`  —— 案例声明 `entry.module` + `entry.function`，直接调用生产函数。
  · `production_http`  —— 案例声明 `entry.http`，走真实 FastAPI app / TestClient 与真鉴权依赖。
  · `recorded_provider`—— 固定 provider 响应进入真实 Agent 工具分发边界。
  · `postgres_integration` —— 唯一约束 / 事务 / 原子领取；默认跳过，显式开启且拒绝生产库。

本模块**不复制任何生产判断**：`$identity` 走 `cpq_sso.to_tech_user`，项目状态走真实
`store` / `integration` 写入 API，权限判定走 `project_access` / 路由自己的 `_require`。
"""
from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path

from . import EXECUTORS, FIXTURES_DIR, GATE_EXECUTORS, PRODUCTION_EXECUTORS
from . import executor_of  # noqa: F401  （唯一口径在 __init__，这里只是转发）
from . import prodkit

# 生产层的 `expected.forbidden` 专用检查（Sim 里没有、也不该有）
PRODUCTION_FORBIDDEN = {
    "production_host_called": "不得访问 PDT / 生产地址（harness 已钉死 CPQ_AUTH_BASE_URL 且替换 _fetch）",
    "real_secret_used": "不得使用真实 API key",
    "acl_blocked_professional_write": "通用项目写 ACL 不得提前拦截专属业务动作",
    "acl_forbidden_message_leaked": "不得把「你的角色只能查看该项目，不能修改」当成专属动作的结论",
    "http_error_without_trace": "4xx/5xx 必须带 trace_id",
    "cross_project_leak": "返回体里的 project_id 必须与请求目标一致",
    "sim_fallback": "声明 production-backed 的案例不得退化成 Sim",
    "partial_write_on_failure": "步骤失败时不得留下该步骤之后才该有的状态",
}

ACL_FORBIDDEN_MESSAGE = "你的角色只能查看该项目，不能修改"
ACL_NOT_FOUND_MESSAGE = "项目不存在"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return jsonable(dump())
        except Exception:                              # pragma: no cover - 模型 dump 失败
            pass
    as_dict = getattr(value, "__dict__", None)
    if isinstance(as_dict, dict):
        return {str(k): jsonable(v) for k, v in as_dict.items() if not str(k).startswith("_")}
    return str(value)


def resolve_module(name: str):
    text = str(name or "").strip()
    if not text:
        raise ValueError("entry.module 为空")
    for prefix in ("tech_app.",):
        if text.startswith(prefix):
            text = text[len(prefix):]
    prodkit.load()
    return importlib.import_module(text)


_FIXTURE_CACHE: dict = {}


def load_fixture(ref: str):
    """读取数据集 fixture（相对 ``dataset/evals/cpq/fixtures``），只读、不出仓库。"""
    text = str(ref or "").strip()
    if not text or ".." in text:
        raise ValueError(f"非法 fixture 引用：{ref!r}")
    if text in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[text]
    path = (FIXTURES_DIR / text).resolve()
    if FIXTURES_DIR.resolve() not in path.parents or not path.is_file():
        raise ValueError(f"fixture 不存在：{text}")
    value = json.loads(path.read_text(encoding="utf-8"))
    _FIXTURE_CACHE[text] = value
    return value


class ProductionUnavailable(RuntimeError):
    """生产模块 / 依赖不可用 → 案例按 skipped 处理，不算通过也不算失败。"""


def _require_fastapi():
    try:
        import fastapi  # noqa: F401
        from fastapi.testclient import TestClient  # noqa: F401
    except Exception as exc:
        raise ProductionUnavailable(f"缺少 fastapi/httpx，production_http 层不可执行：{exc}")


# --------------------------------------------------------------------------- #
# 执行上下文
# --------------------------------------------------------------------------- #
class Ctx:
    def __init__(self, case, case_dir: str):
        self.case = case
        self.dir = case_dir
        self.input = case.get("input") or {}
        self.identities = self.input.get("identities") or {}
        self.projects = {}
        self.case_dir = case_dir
        self.trace = []
        self.records = {"unit_calls": 0, "http_calls": 0, "guard_calls": 0}
        self.client = None
        self.tokens = {}
        self.step_obs = {}
        self.order = []

    # ---- 引用解析 ----
    def project_id(self, key: str) -> str:
        if key not in self.projects:
            raise ValueError(f"未声明的项目 key：{key}")
        return self.projects[key]

    def resolve(self, value):
        if isinstance(value, dict):
            # 引用型 dict：`{"$project_id": "a"}` / `{"$fixture": "...", "$pick": "card"}`
            # 等 —— 只要有 `$` 开头的引用键就整体解析，不要求只有 0/1 个键。
            if any(str(key).startswith("$") for key in value):
                if "$identity" in value:
                    return prodkit.make_identity(self.identities.get(value["$identity"], {}))
                if "$project" in value:
                    return prodkit.project_meta(self.project_id(value["$project"]))
                if "$project_id" in value:
                    return self.project_id(value["$project_id"])
                if "$username" in value:
                    return str((self.identities.get(value["$username"]) or {}).get("username") or "")
                if "$files" in value:
                    return self.case_dir
                if "$bytes" in value:
                    # 需要 bytes 参数的真实生产函数（store.add_attachment / replace_source）
                    # 用这个引用注入一段 UTF-8 字节，不读真实文件、不带真实数据。
                    return str(value["$bytes"]).encode("utf-8")
                if "$fixture" in value:
                    data = load_fixture(value["$fixture"])
                    pick = value.get("$pick")
                    if pick is None:
                        return data
                    for part in str(pick).split("."):
                        data = data[part] if isinstance(data, dict) else data[int(part)]
                    return data
                if "$present" in value or "$absent" in value or "$len" in value:
                    return value            # 断言操作符：原样交给比较引擎
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        return value

    def path(self, template: str, spec: dict) -> str:
        text = str(template or "")
        params = dict(self.input.get("path_params") or {})
        key = str(spec.get("project") or "")
        if key:
            params["project_id"] = self.project_id(key)
            params["project"] = self.project_id(key)
        for name, value in params.items():
            text = text.replace("{" + name + "}", str(value))
        return text


def _error_obs(exc: Exception) -> dict:
    return {
        "type": type(exc).__name__,
        "code": str(getattr(exc, "code", "") or ""),
        "status": int(getattr(exc, "status_code", 0) or getattr(exc, "status", 0) or 0),
        "message": str(exc)[:400],
    }


def _http_detail(resp) -> str:
    try:
        body = resp.json()
    except Exception:
        return str(getattr(resp, "text", ""))[:200]
    return str(body.get("detail") or "")[:200] if isinstance(body, dict) else ""


def _http_trace(resp) -> str:
    try:
        body = resp.json()
    except Exception:
        return ""
    return str((body or {}).get("trace_id") or "") if isinstance(body, dict) else ""


def _http_code(resp) -> str:
    try:
        body = resp.json()
    except Exception:
        return ""
    if not isinstance(body, dict):
        return ""
    for key in ("code", "error_code", "detail_code"):
        if body.get(key):
            return str(body[key])
    return ""


# --------------------------------------------------------------------------- #
# 步骤执行
# --------------------------------------------------------------------------- #
def _run_unit_step(ctx: Ctx, step: dict) -> dict:
    call = step.get("call") or {}
    module = resolve_module(call.get("module") or (ctx.case.get("entry") or {}).get("module"))
    name = str(call.get("function") or (ctx.case.get("entry") or {}).get("function") or "")
    func = getattr(module, name, None)
    if func is None:
        raise ValueError(f"生产模块 {module.__name__} 没有 {name}")
    args = ctx.resolve(call.get("args") or {})
    ctx.records["unit_calls"] += 1
    ctx.trace.append({"module": module.__name__, "function": name})
    if name.startswith("require_") or name.startswith("can_"):
        ctx.records["guard_calls"] += 1
    try:
        value = func(**args)
    except Exception as exc:
        return {"kind": "unit", "ok": False, "error": _error_obs(exc),
                "module": module.__name__, "function": name, "tool": name}
    return {"kind": "unit", "ok": True, "result": jsonable(value),
            "module": module.__name__, "function": name, "tool": name}


def _run_http_step(ctx: Ctx, step: dict) -> dict:
    _require_fastapi()
    spec = step.get("http") or {}
    identity_key = str(spec.get("identity") or "")
    if identity_key not in ctx.identities:
        raise ValueError(f"未声明的身份 key：{identity_key}")
    if ctx.client is None:
        ctx.client, ctx.tokens = prodkit.http_client(ctx.identities)
    method = str(spec.get("method") or (ctx.case.get("entry") or {}).get("http", {}).get("method")
                 or "GET").upper()
    template = str(spec.get("path") or (ctx.case.get("entry") or {}).get("http", {}).get("path") or "")
    path = ctx.path(template, spec)
    token = ctx.tokens[identity_key]
    headers = {"Authorization": f"Bearer {token}"}
    ctx.records["http_calls"] += 1
    ctx.trace.append({"http": f"{method} {path}", "identity": identity_key,
                      "as": str(ctx.identities[identity_key].get("username") or "")})
    handler = getattr(ctx.client, method.lower())
    kwargs = {"headers": headers}
    if spec.get("json") is not None:
        kwargs["json"] = ctx.resolve(spec["json"])
    if spec.get("params"):
        kwargs["params"] = ctx.resolve(spec["params"])
    resp = handler(path, **kwargs)
    out = {
        "kind": "http", "ok": True, "status": int(resp.status_code),
        "detail": _http_detail(resp), "trace_id": _http_trace(resp), "code": _http_code(resp),
        "method": method, "path": path, "project": str(spec.get("project") or ""),
    }
    try:
        out["result"] = jsonable(resp.json())
    except Exception:
        out["result"] = None
    return out


def _run_route_sweep(ctx: Ctx, step: dict) -> dict:
    """对真实路由表里的项目级 GET 路由逐条发请求（动态收集，不手抄条数）。"""
    _require_fastapi()
    spec = step.get("route_sweep") or {}
    identity_key = str(spec.get("identity") or "")
    project_key = str(spec.get("project") or "")
    if ctx.client is None:
        ctx.client, ctx.tokens = prodkit.http_client(ctx.identities)
    token = ctx.tokens[identity_key]
    pid = ctx.project_id(project_key)
    results = prodkit.probe_read_routes(prodkit.routes(), ctx.client, token, pid,
                                        skip_extra_params=bool(spec.get("skip_extra_params", True)))
    probe = [row for row in results if row.get("status") is not None]
    ctx.records["http_calls"] += len(probe)
    ctx.trace.append({"route_sweep": "project GET", "identity": identity_key, "probed": len(probe)})
    blocked = [row for row in probe if row["status"] == 404 and row.get("detail") == ACL_NOT_FOUND_MESSAGE]
    forbidden = [row for row in probe if row["status"] == 403]
    server_errors = [row for row in probe if row["status"] >= 500]
    # 每条读路由必须要么真被请求过、要么写明为什么跳过 —— 不许静默少探一条。
    unaccounted = [row["path"] for row in results
                   if row.get("status") is None and not row.get("skipped")]
    statuses: dict = {}
    for row in probe:
        key = str(row["status"])
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "kind": "route_sweep", "ok": True, "identity": identity_key, "project": pid,
        "total": len(results), "probed": len(probe),
        "skipped": [row for row in results if row.get("status") is None],
        "unaccounted": unaccounted,
        "statuses": statuses,
        "acl_blocked": blocked,
        "acl_blocked_paths": [row["path"] for row in blocked],
        "forbidden_paths": [row["path"] for row in forbidden],
        "server_error_paths": [row["path"] for row in server_errors],
        "routes": results,
    }


def _run_concurrent_step(ctx: Ctx, step: dict) -> dict:
    """受控交错的并发步骤：`threading.Barrier` 同步起跑，不用 sleep 制造竞争。

    同一个真实生产调用被 N 个线程同时触发；裁决权在生产代码（锁 / 唯一约束 / 原子更新），
    不在测试里。返回每条线程的真实结果，供断言「只有一个 winner」。
    """
    import threading

    spec = step.get("concurrent") or {}
    threads = max(2, int(spec.get("threads") or 2))
    mode = "http" if spec.get("http") else "call"
    results = [None] * threads
    errors = [None] * threads
    barrier = threading.Barrier(threads)
    lock = threading.Lock()

    def worker(index):
        try:
            barrier.wait(timeout=10)
            if mode == "http":
                results[index] = _run_http_step(ctx, {"http": spec["http"]})
            else:
                results[index] = _run_unit_step(ctx, {"call": spec["call"]})
        except Exception as exc:                        # noqa: BLE001 - 竞态里的异常要如实收集
            errors[index] = _error_obs(exc)

    pool = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join(timeout=30)
    with lock:
        ok_results = [row for row in results if isinstance(row, dict)]
    winners = [row for row in ok_results if row.get("ok") is True]
    return {
        "kind": "concurrent", "ok": not any(errors), "threads": threads, "mode": mode,
        "results": results, "errors": errors,
        "successes": len(winners), "failures": len(ok_results) - len(winners),
    }


def _run_write_matrix(ctx: Ctx, step: dict) -> dict:
    """项目级写路由 × 多角色的**门禁矩阵**（真实路由、真实鉴权依赖、真实项目 ACL）。

    为什么不是手抄的清单：`CONTRIBUTE_ROUTES`（专属业务动作白名单）的真实条数由
    `project_access` 现算。矩阵逐条 × 逐个身份发真实请求，只记录**谁被哪一层拦下**：

      · ``acl_blocked_hits`` —— 命中通用写 ACL 文案「你的角色只能查看该项目，不能修改」。
        专属动作白名单里的路由**一条都不该**命中它（命中就是门禁顺序回归）。
      · ``not_found`` —— 无关角色必须拿到不可枚举的 404，而不是 403。
      · ``reached`` —— 请求穿过了项目 ACL（进入接口自己的业务门禁 / 校验）。
      · ``server_errors`` —— 5xx 也要如实计数，不能把服务端异常算成「被正确拒绝」。

    额外路径参数（``{part_id}`` / ``{version}``）用占位值填，并在行里标出来，不静默跳过。
    """
    _require_fastapi()
    spec = step.get("write_matrix") or {}
    project_key = str(spec.get("project") or "")
    identities = [str(name) for name in (spec.get("identities") or [])]
    for name in identities:
        if name not in ctx.identities:
            raise ValueError(f"未声明的身份 key：{name}")
    if ctx.client is None:
        ctx.client, ctx.tokens = prodkit.http_client(ctx.identities)
    pid = ctx.project_id(project_key)
    project_access = prodkit.module("backend.services.project_access")
    rows = prodkit.routes().project()
    wanted = str(spec.get("routes") or "contribute_whitelist")
    missing_from_route_table = []
    if wanted == "contribute_whitelist":
        allow = {f"{method} {path}" for method, path in project_access.CONTRIBUTE_ROUTES}
        present = {f"{row['method']} {row['path']}" for row in rows}
        missing_from_route_table = sorted(allow - present)
        routes = [row for row in rows if f"{row['method']} {row['path']}" in allow]
    elif wanted == "acl_write_gated":
        allow = {f"{method} {path}" for method, path in project_access.CONTRIBUTE_ROUTES}
        routes = [row for row in rows
                  if row["method"] in ("POST", "PUT", "PATCH", "DELETE")
                  and f"{row['method']} {row['path']}" not in allow]
    else:
        routes = [row for row in rows if row["method"] in ("POST", "PUT", "PATCH", "DELETE")]
    body = spec.get("json")
    placeholders = dict(spec.get("params") or {})

    matrix = []
    acl_hits = []
    not_found = {name: 0 for name in identities}
    reached = {name: 0 for name in identities}
    route_forbidden = {name: 0 for name in identities}
    forbidden_samples = []
    acl_blocked_routes = []
    server_errors = []
    for row in routes:
        path = row["path"].replace("{project_id}", pid)
        extra = [name for name in row["runtime_params"] if name != "project_id"]
        for name in extra:
            path = path.replace("{" + name + "}", str(placeholders.get(name) or "1"))
        per_role = {}
        for key in identities:
            token = ctx.tokens[key]
            kwargs = {"headers": {"Authorization": f"Bearer {token}"}}
            # httpx 的 delete() 不吃 json=；DELETE 路由本就不带请求体。
            if body is not None and row["method"] in ("POST", "PUT", "PATCH"):
                kwargs["json"] = ctx.resolve(body)
            ctx.records["http_calls"] += 1
            resp = getattr(ctx.client, row["method"].lower())(path, **kwargs)
            status = int(resp.status_code)
            detail = _http_detail(resp)
            blocked = ACL_FORBIDDEN_MESSAGE in str(detail)
            entry = {"status": status, "detail": detail, "acl_blocked": blocked}
            per_role[key] = entry
            route = f"{row['method']} {row['path']}"
            if blocked:
                acl_hits.append({"route": route, "identity": key, "status": status})
                if route not in acl_blocked_routes:
                    acl_blocked_routes.append(route)
            elif status == 403:
                # 相关但无此业务职责：接口自己的 _require 403（文案指向该谁做），
                # 不是通用写 ACL 的「你只能查看该项目」。
                route_forbidden[key] += 1
                if detail and detail not in forbidden_samples:
                    forbidden_samples.append(detail)
            invisible = status == 404 and detail == ACL_NOT_FOUND_MESSAGE
            if invisible:
                not_found[key] += 1
            if not blocked and not invisible and status < 500:
                reached[key] += 1
            if status >= 500:
                server_errors.append({"route": route, "identity": key, "status": status,
                                      "detail": detail[:120]})
        matrix.append({"route": f"{row['method']} {row['path']}", "method": row["method"],
                       "handler": row["handler"], "extra_params": extra, "roles": per_role})
    ctx.trace.append({"write_matrix": f"{wanted} × {len(identities)} 角色",
                      "routes": len(routes)})
    identities_covered = sorted({key for row in matrix for key in (row["roles"] or {})})
    return {
        "kind": "write_matrix", "ok": True, "mode": wanted,
        "route_count": len(matrix), "identities": identities_covered,
        "routes": [row["route"] for row in matrix],
        "missing_from_route_table": missing_from_route_table,
        "missing_route_count": len(missing_from_route_table),
        "acl_blocked_hits": acl_hits,
        "acl_blocked_hit_count": len(acl_hits),
        "acl_blocked_routes": acl_blocked_routes,
        "acl_blocked_route_count": len(acl_blocked_routes),
        "server_error_count": len(server_errors),
        "route_forbidden": route_forbidden,
        "route_forbidden_samples": forbidden_samples[:5],
        "not_found": not_found,
        "reached": reached,
        "server_errors": server_errors,
        "rows": matrix,
    }


_STEP_KINDS = ("call", "http", "route_sweep", "concurrent", "write_matrix")


def run_case(case, case_dir: str = "") -> dict:
    """执行一条 production-backed 案例；返回 ``{status, reason, observation}``。"""
    ok, why = prodkit.available()
    if not ok:
        return {"status": "skipped", "reason": why, "observation": {}}
    executor = executor_of(case)
    if executor == "postgres_integration":
        return {"status": "skipped",
                "reason": "postgres_integration 层默认跳过（CPQ_EVAL_INTEGRATION=1 才运行）",
                "observation": {}}
    own_dir = not case_dir
    work = Path(case_dir or tempfile.mkdtemp(prefix="cpq_eval_case_"))
    # 环境隔离只覆盖本条案例的执行窗口：退出时把 CPQ_SSO 等开关放回原值，
    # 不把「测试用的生产环境」泄漏给同进程的其他测试模块（含别人的红测）。
    return _run_case_inner(case, str(work), own_dir, executor)


def _run_case_inner(case, work: str, own_dir: bool, executor: str) -> dict:
    with prodkit.pinned_env():
        return _execute_case(case, work, own_dir, executor)


def _execute_case(case, case_dir: str, own_dir: bool, executor: str) -> dict:
    work = Path(case_dir)
    try:
        prodkit.fresh_store(str(work / "meta"))
        ctx = Ctx(case, str(work))
        ctx.projects = prodkit.seed_projects(ctx.resolve(ctx.input.get("projects") or {}))
        ctx.records["projects"] = len(ctx.projects)
        for step in ctx.input.get("steps") or []:
            step_id = str(step.get("id") or f"step{len(ctx.order)}")
            ctx.order.append(step_id)
            payload = next((step[key] for key in _STEP_KINDS if key in step), None)
            kind = next((key for key in _STEP_KINDS if key in step), "")
            if payload is None:
                ctx.step_obs[step_id] = {"kind": "", "ok": False,
                                         "error": {"type": "ValueError",
                                                   "message": f"步骤 {step_id} 没有可执行的 op"}}
                continue
            try:
                if kind == "call":
                    obs = _run_unit_step(ctx, step)
                elif kind == "http":
                    obs = _run_http_step(ctx, step)
                elif kind == "route_sweep":
                    obs = _run_route_sweep(ctx, step)
                elif kind == "write_matrix":
                    obs = _run_write_matrix(ctx, step)
                else:
                    obs = _run_concurrent_step(ctx, step)
            except Exception as exc:
                obs = {"kind": kind, "ok": False, "error": _error_obs(exc)}
            ctx.step_obs[step_id] = obs
        observation = {
            "executor": executor,
            "steps": ctx.step_obs,
            "order": ctx.order,
            "records": ctx.records,
            "trace": ctx.trace,
            "projects": ctx.projects,
            "identities": {key: {"username": (spec or {}).get("username"),
                                 "cpq_role_code": (spec or {}).get("cpq_role_code"),
                                 "tech_role": (spec or {}).get("tech_role"),
                                 "sso_roles": (spec or {}).get("sso_roles") or []}
                           for key, spec in ctx.identities.items()},
        }
        observation["last"] = ctx.step_obs.get(ctx.order[-1], {}) if ctx.order else {}
        try:
            observation["expected"] = ctx.resolve(case.get("expected") or {})
        except Exception as exc:                        # pragma: no cover - 数据写错
            observation["expected_error"] = str(exc)
        for name, step in (ctx.input.get("observe") or {}).items():
            try:
                observation["state_" + name] = ctx.resolve(step)
            except Exception as exc:                   # pragma: no cover - 数据写错
                observation["state_" + name] = {"error": _error_obs(exc)}
        return {"status": "executed", "reason": "", "observation": observation}
    finally:
        if own_dir:
            import shutil
            shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 断言
# --------------------------------------------------------------------------- #
def _forbidden_hits(name: str, case, observation: dict) -> list:
    steps = observation.get("steps") or {}
    hits = []
    if name in ("production_host_called", "real_secret_used", "sim_fallback"):
        return hits
    if name == "http_error_without_trace":
        for sid, obs in steps.items():
            if obs.get("kind") == "http" and int(obs.get("status") or 0) >= 400 \
                    and not obs.get("trace_id"):
                hits.append(f"{sid}: {obs.get('status')} 没有 trace_id")
    if name in ("acl_blocked_professional_write", "acl_forbidden_message_leaked"):
        for sid, obs in steps.items():
            if ACL_FORBIDDEN_MESSAGE in str(obs.get("detail") or ""):
                hits.append(f"{sid}: 通用写 ACL 提前拦截（{ACL_FORBIDDEN_MESSAGE}）")
    if name == "cross_project_leak":
        for sid, obs in steps.items():
            if obs.get("kind") != "http" or not obs.get("project"):
                continue
            body = obs.get("result")
            if isinstance(body, dict):
                inner = body.get("meta") if isinstance(body.get("meta"), dict) else body
                got = str((inner or {}).get("project_id") or "")
                want = observation.get("projects", {}).get(obs["project"], "")
                if got and want and got != want:
                    hits.append(f"{sid}: 返回 project_id={got} ≠ 请求目标 {want}")
    if name == "partial_write_on_failure":
        for sid, obs in steps.items():
            if obs.get("ok", True) is False and obs.get("side_effect_seen"):
                hits.append(f"{sid}: 失败却留下了副作用")
    return hits


def compare_production(case, observation: dict, compare, resolve_path) -> list:
    """用 runner 的操作符引擎断言生产观测；不引入第二套比较语义。"""
    errors = []
    expected = observation.get("expected") or case.get("expected") or {}
    last = observation.get("last") or {}

    if "http" in expected:
        errors.extend(compare(expected["http"], {
            "status": last.get("status"), "detail": last.get("detail"),
            "code": last.get("code"), "trace_id": last.get("trace_id"),
        }, "http"))
    if "result" in expected:
        errors.extend(compare(expected["result"], last.get("result"), "result"))
    if "error" in expected:
        errors.extend(compare(expected["error"], last.get("error") or {}, "error"))
    state = {"steps": observation.get("steps") or {}, "records": observation.get("records") or {},
             "trace": observation.get("trace") or [], "projects": observation.get("projects") or {},
             "identities": observation.get("identities") or {}}
    for name, value in observation.items():
        if name.startswith("state_"):
            state[name[len("state_"):]] = value
    for path, want in (expected.get("state") or {}).items():
        found, actual = resolve_path(state, path)
        if not found:
            if isinstance(want, dict) and want.get("$absent"):
                continue
            errors.append(f"state.{path}: 观测里没有这个字段")
        else:
            errors.extend(compare(want, actual, f"state.{path}"))
    for path, want in (expected.get("records") or {}).items():
        found, actual = resolve_path(state["records"], path)
        if not found:
            errors.append(f"records.{path}: 观测里没有这个计数")
        else:
            errors.extend(compare(want, actual, f"records.{path}"))
    for name in expected.get("forbidden") or []:
        if name in PRODUCTION_FORBIDDEN:
            for hit in _forbidden_hits(name, case, observation):
                errors.append(f"forbidden.{name}: {hit}")
    return errors


# --------------------------------------------------------------------------- #
# 质量守护：production-backed 案例必须声明真实入口
# --------------------------------------------------------------------------- #
def entry_errors(case) -> list:
    executor = executor_of(case)
    if executor not in PRODUCTION_EXECUTORS:
        return []
    entry = case.get("entry") or {}
    errors = []
    if executor == "postgres_integration":
        if not (entry.get("module") or entry.get("http") or entry.get("service")):
            errors.append("postgres_integration 案例必须声明 entry（module / service / http）")
        return errors
    http = entry.get("http") or {}
    if http:
        if not (http.get("method") and http.get("path")):
            errors.append("entry.http 必须同时有 method 与 path")
        elif not str(http.get("path")).startswith("/api/"):
            errors.append("entry.http.path 必须是真实接口路径")
    elif entry.get("module") and entry.get("function"):
        pass
    else:
        errors.append("production-backed 案例必须声明 entry.module+function 或 entry.http.method+path")
    steps = (case.get("input") or {}).get("steps") or []
    if not steps:
        errors.append("production-backed 案例必须声明 input.steps")
    kinds = {kind for step in steps for kind in _STEP_KINDS if kind in step}
    if any("call" == kind for kind in kinds) and not entry.get("module"):
        errors.append("有 call 步骤却没有 entry.module")
    if any(kind in ("http", "write_matrix") for kind in kinds) and not http:
        errors.append("有 http / write_matrix 步骤却没有 entry.http")
    if executor == "recorded_provider" and (case.get("layer") or "") not in (
            "deterministic", "recorded_provider"):
        errors.append("recorded_provider 案例的 layer 必须是 deterministic / recorded_provider")
    return errors


def touched_production(observation: dict) -> list:
    """本案例真实命中的生产模块 / 路由（质量守护与报告用）。"""
    out = []
    for item in observation.get("trace") or []:
        if item.get("module"):
            out.append(f"{item['module']}.{item.get('function')}")
        elif item.get("http"):
            out.append(item["http"])
        elif item.get("route_sweep"):
            out.append(f"route_sweep:{item['route_sweep']}")
        elif item.get("write_matrix"):
            out.append(f"write_matrix:{item['write_matrix']}")
    return out


_RUNTIME_KEYS = ("http", "state", "records", "messages", "events", "ui_protocol")


def spec_only_errors(case) -> list:
    """`specification_only` 案例的静态契约：没有执行器就不许写运行期断言。"""
    raw = case if isinstance(case, dict) else getattr(case, "raw", {}) or {}
    errors = []
    if raw.get("actions"):
        errors.append("specification_only 案例不得声明 actions（那是 Sim 执行器）")
    expected = raw.get("expected") or {}
    for key in _RUNTIME_KEYS:
        if expected.get(key):
            errors.append(f"specification_only 案例不得声明 expected.{key}"
                          f"（没有执行器，运行期断言无法被验证）")
    if not (raw.get("invariants") or raw.get("source_specs")):
        errors.append("specification_only 案例必须声明 invariants 或 source_specs")
    return errors
