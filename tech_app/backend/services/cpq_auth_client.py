"""技术工艺访问 CPQ 账号级设置的 HTTP 客户端。

用户数据只有 PG 一处（`cpq_wf`），技术工艺**不直连数据库**，一律走配置报价 CPQ
一体化服务（8010）的 `/auth/*`。本模块是那条 HTTP 通道在技术工艺侧的唯一入口：

  · `user_overrides(username)`  —— 解析层要的明文（模型 + 各 provider 的 Key），
    走服务间内部通道（后台任务线程没有用户票）；
  · `summary(username)`         —— 界面要的打码摘要；有请求票就用**本人**的票打
    `/auth/my/llm`，没有票才退回内部通道；
  · `set_model / set_key`       —— 写账号级设置（内部通道 PUT）；
  · `update_profile / change_password` —— 本人改资料 / 改密码；
  · `invalidate(username="")`   —— 写成功后清缓存。

失败策略（与 Spec C10 一致，别改成"静默回落全局"）：

  · CPQ 可达 → 刷新缓存；
  · CPQ 不可达 → 用缓存（过期也用，避免正在跑的任务被外部抖动打断）；
  · 连缓存都没有 → 抛 `CpqAuthUnavailable`，由 HTTP 面转成 503。

拿别人的额度跑是最坏的结果，所以这里宁可失败，也绝不假装"这个人没有账号级设置"。

唯一的例外是**根本没有账号这件事**的本地开发模式（`AUTH_ENABLED=false` /
`AUTH_AUTO_ADMIN=true`、没配 `CPQ_SSO` 也没配内部令牌）：那时 CPQ 不是"不可达"，
而是"不存在"，账号级设置应当如实为空 —— 详见 `_configured()`。
"""
from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..config import CPQ_AUTH_BASE_URL, CPQ_AUTH_TIMEOUT_SECONDS

INTERNAL_TOKEN_HEADER = "X-Internal-Token"
INTERNAL_PATH = "/auth/internal/user-llm"
MY_PATH = "/auth/my/llm"

_DEFAULT_TTL_SECONDS = 60.0

# 发起这一次调用的用户票（HTTP 请求上下文）。后台任务线程没有它，
# 那条路径用内部令牌。
_REQUEST_TOKEN: contextvars.ContextVar[str] = contextvars.ContextVar(
    "cpq_auth_request_token", default="")

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


class CpqAuthUnavailable(RuntimeError):
    """CPQ 登录服务不可用（或返回异常），且本地没有可用缓存。"""


# --------------------------------------------------------------------------- #
# 请求上下文
# --------------------------------------------------------------------------- #
def set_request_token(token: str = "") -> None:
    """由 main.auth_guard 在 SSO 分支设入当前请求的用户票。"""
    _REQUEST_TOKEN.set(str(token or "").strip())


def current_request_token() -> str:
    try:
        return _REQUEST_TOKEN.get() or ""
    except LookupError:                                  # pragma: no cover - 正常取不到才兜底
        return ""


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
# 缓存 TTL 的环境变量名：`CPQ_USER_LLM_CACHE_SECONDS` 是 Spec 里的正式名，
# `CPQ_USER_CACHE_SECONDS` 是早期实现用的名字 —— 两个都认，前者优先（都在部署里改过的
# 话，以 Spec 的名字为准）。
_TTL_ENV_VARS = ("CPQ_USER_LLM_CACHE_SECONDS", "CPQ_USER_CACHE_SECONDS")


def _ttl_seconds() -> float:
    for env_name in _TTL_ENV_VARS:
        raw = (os.getenv(env_name) or "").strip()
        if not raw:
            continue
        try:
            return max(0.0, float(raw))
        except ValueError:
            continue                                         # 写错了就退回默认值，不炸
    return _DEFAULT_TTL_SECONDS


def _cache_get(username: str, allow_expired: bool = False) -> dict:
    with _lock:
        entry = _cache.get(username)
        if not entry:
            return None
        expires_at, value = entry
        if not allow_expired and expires_at < time.monotonic():
            return None
        return dict(value)


def _cache_put(username: str, value: dict) -> None:
    with _lock:
        if len(_cache) > 4096:                           # 简单防膨胀：满了就整体清空
            _cache.clear()
        _cache[username] = (time.monotonic() + _ttl_seconds(), dict(value or {}))


def invalidate(username: str = "") -> None:
    """写成功后清缓存；不传账号 = 全清。"""
    with _lock:
        if username:
            _cache.pop(str(username).strip(), None)
        else:
            _cache.clear()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _base() -> str:
    return (CPQ_AUTH_BASE_URL or "").rstrip("/")


def _http(method: str, url: str, *, token: str = "", payload: dict = None,
          internal: bool = False) -> dict:
    headers = {"Accept": "application/json"}
    if internal:
        secret = (os.environ.get("CPQ_INTERNAL_TOKEN") or "").strip()
        if not secret:
            raise CpqAuthUnavailable(
                "未配置 CPQ_INTERNAL_TOKEN，无法读取账号级模型与密钥（独立运行？）")
        headers[INTERNAL_TOKEN_HEADER] = secret
    elif token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=CPQ_AUTH_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = (exc.read().decode("utf-8", "replace") or "")[:200]
        except Exception:                                # pragma: no cover - 读不到就算了
            detail = ""
        raise CpqAuthUnavailable(
            f"CPQ 登录服务返回 {exc.code}：{detail or '请求被拒绝'}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CpqAuthUnavailable(f"连不上 CPQ 登录服务（{_base()}）：{exc}") from exc
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise CpqAuthUnavailable("CPQ 登录服务返回的不是 JSON") from exc
    if not isinstance(body, dict):
        raise CpqAuthUnavailable("CPQ 登录服务返回的结构不对")
    return body


def _configured() -> bool:
    """这一次读账号级设置时，CPQ 这条通道到底存不存在。

    只有"存在"才谈得上"不可达"。本地开发模式（没开 CPQ_SSO、也没配内部令牌、更没有
    用户票）里 CPQ 里没有账号，账号级设置如实为空 —— 否则"本机把后端跑起来"会变成
    "登录服务不可用"，/api/health、/api/settings 这类读接口直接 503。

    一旦有任何一条通道可用（内部令牌 / 用户票 / CPQ_SSO），就按正常失败策略来：
    读不到就抛 CpqAuthUnavailable，绝不静默改用全局模型与全局 Key。

    注意：**写**（set_model / set_key）不走这个兜底 —— 没有账号权威时不接受写，
    免得把一次失败的写入当成"已保存"。
    """
    if (os.environ.get("CPQ_INTERNAL_TOKEN") or "").strip():
        return True
    if current_request_token():
        return True
    from ..config import CPQ_SSO_ENABLED
    return bool(CPQ_SSO_ENABLED)


def _entry_from_internal(username: str) -> dict:
    payload = _http("GET", f"{_base()}{INTERNAL_PATH}?username={urllib.parse.quote(username)}",
                    internal=True)
    keys = payload.get("api_keys")
    return {"model": str(payload.get("model") or ""),
            "api_keys": {str(p): str(v) for p, v in (keys or {}).items() if str(v or "").strip()}}


def _read(username: str) -> dict:
    """账号级设置（明文形态）。命中缓存不回调；不可达时用缓存；都没有就抛错。"""
    name = str(username or "").strip()
    if not name:
        return {"model": "", "api_keys": {}}
    if not _configured():
        return {"model": "", "api_keys": {}}
    cached = _cache_get(name)
    if cached is not None:
        return cached
    try:
        value = _entry_from_internal(name)
    except CpqAuthUnavailable:
        stale = _cache_get(name, allow_expired=True)
        if stale is not None:
            return stale
        raise
    _cache_put(name, value)
    return value


# --------------------------------------------------------------------------- #
# 对外契约
# --------------------------------------------------------------------------- #
def user_overrides(username: str) -> dict:
    """该账号的账号级模型与**明文** Key（解析层专用）。"""
    entry = _read(username)
    out = {}
    model = str(entry.get("model") or "").strip()
    if model:
        out["model"] = model
    keys = entry.get("api_keys") or {}
    if keys:
        out["api_keys"] = dict(keys)
    return out


def summary(username: str) -> dict:
    """给界面用的打码摘要：只有 configured 与 hint，永不回明文。"""
    name = str(username or "").strip()
    if not name:
        return {"model": "", "has_model": False, "keys": {}}
    if not _configured():
        return {"model": "", "has_model": False, "keys": {}}
    token = current_request_token()
    if token:
        try:
            payload = _http("GET", f"{_base()}{MY_PATH}", token=token)
            keys = payload.get("keys") or {}
            clean = {str(p): {"configured": bool((v or {}).get("configured")),
                              "hint": str((v or {}).get("hint") or "")}
                     for p, v in keys.items() if isinstance(v, dict)}
            model = str(payload.get("model") or "")
            return {"model": model, "has_model": bool(payload.get("has_model") or model),
                    "keys": clean}
        except CpqAuthUnavailable:
            pass                                         # 票过期/服务抖动 → 走内部通道与缓存
    entry = _read(name)
    return _summary_of(entry)


def _summary_of(entry: dict) -> dict:
    model = str((entry or {}).get("model") or "")
    keys = {}
    for provider, value in ((entry or {}).get("api_keys") or {}).items():
        keys[str(provider)] = {"configured": True, "hint": _mask(value)}
    return {"model": model, "has_model": bool(model), "keys": keys}


def _mask(secret: str) -> str:
    value = str(secret or "").strip()
    if not value:
        return ""
    try:                                                 # 与全局设置同一套打码口径
        import cpq_shared_settings
        return cpq_shared_settings.mask(value)
    except Exception:                                    # pragma: no cover - 独立运行兜底
        return f"{value[:7]}…{value[-4:]}" if len(value) > 14 else "已配置"


def set_model(username: str, model: str) -> dict:
    """写账号级模型（空串 = 清除，回落全局默认）。"""
    name = str(username or "").strip()
    if not name:
        raise ValueError("缺少账号名，账号级模型设置必须绑定到具体账号")
    _http("PUT", f"{_base()}{INTERNAL_PATH}", internal=True,
          payload={"username": name, "model": str(model or "")})
    invalidate(name)
    return user_overrides(name)


def set_key(username: str, provider: str, key: str) -> dict:
    """写账号级 Key（空串 = 删除该 provider 的个人 Key）。"""
    name = str(username or "").strip()
    if not name:
        raise ValueError("缺少账号名，账号级密钥必须绑定到具体账号")
    _http("PUT", f"{_base()}{INTERNAL_PATH}", internal=True,
          payload={"username": name, "api_key": str(key or ""),
                   "api_key_provider": str(provider or "")})
    invalidate(name)
    return user_overrides(name)


def delete_key(username: str, provider: str) -> dict:
    """删除某 provider 的个人 Key（内部通道 PUT 空串，语义与 C6 一致）。"""
    return set_key(username, provider, "")


def update_profile(display_name: str) -> dict:
    """本人改显示名：用**本人自己的票**打 /auth/my/profile。"""
    token = current_request_token()
    if not token:
        raise CpqAuthUnavailable("缺少用户票据，无法修改账号资料")
    return _http("PUT", f"{_base()}/auth/my/profile", token=token,
                 payload={"display_name": str(display_name or "")})


def change_password(current_password: str, new_password: str) -> dict:
    """本人改密码：用**本人自己的票**打 /auth/my/password（旧口令由 CPQ 校验）。"""
    token = current_request_token()
    if not token:
        raise CpqAuthUnavailable("缺少用户票据，无法修改密码")
    return _http("PUT", f"{_base()}/auth/my/password", token=token,
                 payload={"current_password": str(current_password or ""),
                          "new_password": str(new_password or "")})
