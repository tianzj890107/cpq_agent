"""账号级模型与密钥 —— 全局默认之上的一层可选覆盖。

粒度只改这一件事：模型与 API Key 从"全局一个值"变成"全局默认 + 每个登录账号
可覆盖"。**缺项一律回落**，所以它不是第二份"全局设置"：

  · 存储放在配置报价 CPQ 的 PG（`cpq_wf.cpq_wf_user_llm_setting`，**密文**），
    技术工艺只经 `cpq_auth_client` 的 HTTP 通道读写，本地不再落任何账号级设置文件；
    存量搬迁见 `scripts/migrate_users_to_pg.py`；
  · 只有 model 与 api_keys 两项；Temperature / 最大 Tokens / 深度思考仍归全局，
    避免每个账号一份推理参数分叉；
  · CPQ 不可达且没有缓存时**明确失败**（CpqAuthUnavailable），绝不静默按"这个人
    没设置"去用全局 Key —— 那等于拿别人的额度跑。

密钥安全：本模块只在 `get()` 里回明文（给解析用），`summary()` 只回
`configured` + 打码；日志/接口/审计一律用后者。
"""
from __future__ import annotations

from typing import Any

from . import cpq_auth_client


# --------------------------------------------------------------------------- #
# 白名单：只有 llm_settings 那一份，不新建第二份
# --------------------------------------------------------------------------- #
def _whitelist() -> tuple[dict[str, str], dict[str, Any]]:
    from . import llm_settings
    return llm_settings.MODEL_PROVIDERS, llm_settings.PROVIDERS


def _name(username: str) -> str:
    name = str(username or "").strip()
    if not name:
        raise ValueError("缺少账号名，账号级模型设置必须绑定到具体账号")
    return name


def _validate_model(model: str) -> None:
    models, _ = _whitelist()
    if model not in models:
        raise ValueError(f"未知的模型 {model}：账号级模型只能从「模型设置」的可选清单里选")


def _validate_provider(provider: str) -> None:
    _, providers = _whitelist()
    if provider not in providers:
        raise ValueError(f"未知的提供商 {provider or '(空)'}：账号级密钥的提供商必须与平台一致")


# --------------------------------------------------------------------------- #
# 读写（一律经 cpq_auth_client；账号为空串 = 没有账号级设置）
# --------------------------------------------------------------------------- #
def get(username: str) -> dict[str, Any]:
    """该账号的账号级设置；没有记录时返回 {}。CPQ 不可达且无缓存时抛 CpqAuthUnavailable。"""
    name = str(username or "").strip()
    if not name:
        return {}
    data = cpq_auth_client.user_overrides(name) or {}
    out: dict[str, Any] = {}
    model = str(data.get("model") or "").strip()
    if model:
        out["model"] = model
    keys = data.get("api_keys")
    if isinstance(keys, dict):
        clean = {str(p): str(k) for p, k in keys.items() if str(k or "").strip()}
        if clean:
            out["api_keys"] = clean
    return out


def set_model(username: str, model: str) -> dict[str, Any]:
    """设置该账号的个人模型；空串 = 清除（回落全局）。非法模型抛 ValueError 且不写库。"""
    name = _name(username)
    value = str(model or "").strip()
    if value:
        _validate_model(value)
    cpq_auth_client.set_model(name, value)
    return get(name)


def set_key(username: str, provider: str, key: str) -> dict[str, Any]:
    """设置该账号在某 provider 的个人密钥；空串 = 删除。非法 provider 抛 ValueError 且不写库。"""
    name = _name(username)
    target = str(provider or "").strip()
    value = str(key or "").strip()
    _validate_provider(target)
    cpq_auth_client.set_key(name, target, value)
    return get(name)


def summary(username: str) -> dict[str, Any]:
    """给接口/界面用的摘要：**只有 configured 与打码**，永不回明文。"""
    name = str(username or "").strip()
    if not name:
        return {"model": "", "has_model": False, "keys": {}}
    data = cpq_auth_client.summary(name) or {}
    model = str(data.get("model") or "")
    return {
        "model": model,
        "has_model": bool(data.get("has_model") or model),
        "keys": data.get("keys") or {},
    }


def personal_key(username: str, provider: str) -> str:
    """解析专用：取该账号在某个 provider 的个人 Key（没有则空串）。"""
    name = str(username or "").strip()
    if not name:
        return ""
    return str((get(name).get("api_keys") or {}).get(str(provider or "").strip()) or "").strip()
