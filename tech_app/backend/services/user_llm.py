"""账号级模型与密钥 —— 全局默认之上的一层可选覆盖。

粒度只改这一件事：模型与 API Key 从"全局一个值"变成"全局默认 + 每个登录账号
可覆盖"。**缺项一律回落**，所以它不是第二份"全局设置"：

  · 落盘 `DATA_DIR/_user_llm.json`（0600、同目录临时文件 + os.replace 原子写），
    绝不写 `cpq_settings.json`（四个助手共用的全局文件），也绝不写
    `_auth_users.json` —— CPQ 单点登录进来的账号在本地没有用户记录，账号级设置
    不得依赖"先有本地用户"；
  · 只有 model 与 api_keys 两项；Temperature / 最大 Tokens / 深度思考仍归全局，
    避免每个账号一份推理参数分叉；
  · 文件不存在或内容损坏 = "没有账号级设置"，安全回落全局，不抛错（Spec C14）。

密钥安全：本模块只在 `get()` 里回明文（给解析用），`summary()` 只回
`configured` + `cpq_shared_settings.mask()` 打码；日志/接口/审计一律用后者。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..time_utils import now_cst_str

STORE_FILENAME = "_user_llm.json"

_lock = threading.RLock()


# --------------------------------------------------------------------------- #
# 白名单：只有 llm_settings 那一份，不新建第二份
# --------------------------------------------------------------------------- #
def _whitelist() -> tuple[dict[str, str], dict[str, Any]]:
    from . import llm_settings
    return llm_settings.MODEL_PROVIDERS, llm_settings.PROVIDERS


def _mask(secret: str) -> str:
    value = str(secret or "").strip()
    if not value:
        return ""
    try:                                                  # 与全局设置同一套打码
        import cpq_shared_settings
        return cpq_shared_settings.mask(value)
    except Exception:                                     # pragma: no cover - 独立运行时兜底
        return f"{value[:7]}…{value[-4:]}" if len(value) > 14 else "已配置"


def store_path() -> Path:
    """账号级设置文件（DATA_DIR 下，与全局 cpq_settings.json 完全无关）。"""
    return Path(DATA_DIR) / STORE_FILENAME


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
def _load() -> dict[str, Any]:
    """读盘。文件不存在 / 不是合法 JSON / 结构不对 一律当作"没有账号级设置"。"""
    try:
        raw = store_path().read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return {"users": {}}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"users": {}}
    if not isinstance(data, dict):
        return {"users": {}}
    users = data.get("users")
    if not isinstance(users, dict):
        return {"users": {}}
    clean = {str(name): entry for name, entry in users.items()
             if isinstance(entry, dict) and str(name).strip()}
    return {"users": clean}


def _save(data: dict[str, Any]) -> None:
    """原子写：同目录临时文件写完再 os.replace，权限 0600（与 cpq_shared_settings 同一手法）。"""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp",
                                        dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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


def _entry_or_new(users: dict[str, Any], name: str) -> dict[str, Any]:
    entry = users.get(name)
    return dict(entry) if isinstance(entry, dict) else {}


def _store(users: dict[str, Any], name: str, entry: dict[str, Any]) -> None:
    """写回一个账号；模型与密钥都被清空时整条记录删掉（等于"从没设置过"）。"""
    keys = {p: k for p, k in (entry.get("api_keys") or {}).items() if str(k or "").strip()}
    model = str(entry.get("model") or "").strip()
    if not model and not keys:
        users.pop(name, None)
        return
    clean = {}
    if model:
        clean["model"] = model
    if keys:
        clean["api_keys"] = keys
    clean["updated_at"] = str(entry.get("updated_at") or now_cst_str())
    users[name] = clean


def get(username: str) -> dict[str, Any]:
    """该账号的账号级设置；没有记录时返回 {}。"""
    name = str(username or "").strip()
    if not name:
        return {}
    entry = (_load().get("users") or {}).get(name) or {}
    out: dict[str, Any] = {}
    model = str(entry.get("model") or "").strip()
    if model:
        out["model"] = model
    keys = entry.get("api_keys")
    if isinstance(keys, dict):
        clean = {str(p): str(k) for p, k in keys.items() if str(k or "").strip()}
        if clean:
            out["api_keys"] = clean
    return out


def set_model(username: str, model: str) -> dict[str, Any]:
    """设置该账号的个人模型；空串 = 清除（回落全局）。非法模型抛 ValueError 且不落盘。"""
    name = _name(username)
    value = str(model or "").strip()
    if value:
        _validate_model(value)
    with _lock:
        data = _load()
        users = data["users"]
        entry = _entry_or_new(users, name)
        if value:
            entry["model"] = value
        else:
            entry.pop("model", None)
        _store(users, name, entry)
        _save(data)
    return get(name)


def set_key(username: str, provider: str, key: str) -> dict[str, Any]:
    """设置该账号在某 provider 的个人密钥；空串 = 删除。非法 provider 抛 ValueError 且不落盘。"""
    name = _name(username)
    target = str(provider or "").strip()
    value = str(key or "").strip()
    _validate_provider(target)
    with _lock:
        data = _load()
        users = data["users"]
        entry = _entry_or_new(users, name)
        keys = dict(entry.get("api_keys") or {})
        if value:
            keys[target] = value
        else:
            keys.pop(target, None)
        entry["api_keys"] = keys
        _store(users, name, entry)
        _save(data)
    return get(name)


def summary(username: str) -> dict[str, Any]:
    """给接口/界面用的摘要：**只有 configured 与打码**，永不回明文。"""
    try:
        _, providers = _whitelist()
        provider_names = sorted(providers)
    except Exception:                                     # pragma: no cover - 依赖环境
        provider_names = []
    entry = get(username)
    personal = entry.get("api_keys") or {}
    model = str(entry.get("model") or "")
    return {
        "model": model,
        "has_model": bool(model),
        "keys": {p: {"configured": bool(str(personal.get(p) or "").strip()),
                     "hint": _mask(personal.get(p) or "")}
                 for p in provider_names},
    }


def personal_key(username: str, provider: str) -> str:
    """解析专用：取该账号在某个 provider 的个人 Key（没有则空串）。"""
    name = str(username or "").strip()
    if not name:
        return ""
    return str((get(name).get("api_keys") or {}).get(str(provider or "").strip()) or "").strip()
