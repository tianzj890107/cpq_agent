"""
CPQ 单点登录 —— 技术工艺复用配置报价 CPQ 的登录与角色。

CPQ 一体化服务（cpq_suite_server.py:8010）自己有一套登录（cpq_auth.py，账号与会话落
线上 Postgres），角色只有两个：sales_mgr 销售经理 / process_mgr 工艺经理。技术工艺是
这套件里的第四个助手，不该再要用户登第二次，所以这里把 CPQ 的令牌当作身份来源。

验令牌走 **HTTP 回调**而不是直连数据库：
  技术工艺(8012) --Bearer--> CPQ 一体化服务(8010) /auth/me --> Postgres
这样 tech_app 不用引 psycopg、不用知道库在哪、也不用维护第二份会话表；会话的唯一
权威仍然是 cpq_auth。代价是每次请求多一跳，因此按令牌做短 TTL 缓存。

角色映射（见 ROLE_MAP）：
  process_mgr 工艺经理 → process_manager  技术工艺的全部操作
  sales_mgr   销售经理 → viewer           只读浏览
  其它/未知            → viewer           安全默认：认不出来的一律只读

数据库连不上时 **不当作未登录**：那会把所有人静默踢出去，看起来像"登录坏了"。
这里抛 SsoUnavailable，由 main.py 回 503 并说明原因。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from ..config import CPQ_AUTH_BASE_URL, CPQ_AUTH_TIMEOUT_SECONDS

# CPQ 角色 → 技术工艺角色。CPQ 里没有"总监"，技术工艺 3.2 审核 / 3.3 发布原本要
# process_director —— 由 auth.enable_cpq_single_manager() 在本模式下把这两档也授予
# 工艺经理，否则报告永远发布不出去。
ROLE_MAP = {
    "process_mgr": "process_manager",
    "sales_mgr": "viewer",
}
FALLBACK_ROLE = "viewer"

# 令牌缓存。命中期内不再回调 8010 —— 一次页面加载会打十几个 /api 请求，
# 每个都回调一次等于把登录服务当成了热路径。
_TTL_OK = 60.0        # 有效令牌
_TTL_BAD = 10.0       # 无效令牌也缓存一小会儿，挡住刷新风暴
_MAX_ENTRIES = 512

_cache: dict[str, tuple[float, Optional[dict]]] = {}
_lock = threading.Lock()


class SsoUnavailable(RuntimeError):
    """连不上 CPQ 登录服务（8010 没起来，或它连不上 Postgres）。"""


def _cached(token: str) -> tuple[bool, Optional[dict]]:
    with _lock:
        entry = _cache.get(token)
        if not entry:
            return False, None
        expires_at, user = entry
        if expires_at < time.monotonic():
            _cache.pop(token, None)
            return False, None
        return True, user


def _remember(token: str, user: Optional[dict]) -> None:
    with _lock:
        if len(_cache) >= _MAX_ENTRIES:
            # 简单清理：过期的先删，仍然满就整体清空。会话缓存丢了只是多回调一次。
            now = time.monotonic()
            for key in [k for k, (exp, _) in _cache.items() if exp < now]:
                _cache.pop(key, None)
            if len(_cache) >= _MAX_ENTRIES:
                _cache.clear()
        _cache[token] = (time.monotonic() + (_TTL_OK if user else _TTL_BAD), user)


def invalidate(token: str = "") -> None:
    """登出或角色变更后清缓存。不传令牌就整体清空。"""
    with _lock:
        _cache.pop(token, None) if token else _cache.clear()


def _fetch(token: str) -> Optional[dict]:
    """回调 CPQ 的 /auth/me。返回 CPQ 的用户字典；未登录返回 None。"""
    request = urllib.request.Request(
        f"{CPQ_AUTH_BASE_URL}/auth/me",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=CPQ_AUTH_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        # 401/403 是"这张票不认"，属于正常结果；5xx 才是服务本身出问题。
        if exc.code in (401, 403):
            return None
        raise SsoUnavailable(f"CPQ 登录服务返回 {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SsoUnavailable(f"连不上 CPQ 登录服务（{CPQ_AUTH_BASE_URL}）：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise SsoUnavailable("CPQ 登录服务返回的不是 JSON") from exc
    user = payload.get("user")
    return user if isinstance(user, dict) else None


def to_tech_user(cpq_user: dict) -> dict:
    """CPQ 用户 → 技术工艺的用户视图（形状与 auth.public_user 一致）。"""
    role_code = str(cpq_user.get("role_code") or "").strip()
    return {
        "username": cpq_user.get("username") or "",
        "role": ROLE_MAP.get(role_code, FALLBACK_ROLE),
        "display_name": cpq_user.get("display_name") or cpq_user.get("username") or "",
        "requested_role": ROLE_MAP.get(role_code, FALLBACK_ROLE),
        "created_at": cpq_user.get("created_at"),
        "is_system": False,
        # 留痕：出问题时要能看出这个身份是从 CPQ 哪个角色映射来的。
        "source": "cpq",
        "cpq_role_code": role_code,
        "cpq_role_name": cpq_user.get("role_name") or role_code,
        "cpq_user_id": cpq_user.get("user_id"),
    }


def resolve(token: str) -> Optional[dict]:
    """按 CPQ 令牌取技术工艺的用户视图。令牌无效返回 None；服务不可用抛 SsoUnavailable。"""
    token = (token or "").strip()
    if not token:
        return None
    hit, cached = _cached(token)
    if hit:
        return cached
    cpq_user = _fetch(token)
    user = to_tech_user(cpq_user) if cpq_user else None
    _remember(token, user)
    return user
