"""四助手与技术工艺共用的唯一模型配置读写（cpq_settings.json）。

配置报价 CPQ 只保留一套模型设置：模型、推理参数和按 provider 保存的 API Key
全部落在仓库根目录的 cpq_settings.json。报价 / 配置 / 规则三个助手本来就写这个
文件（此前各自有一份重复的 load/save），技术工艺此前另存
`tech_app/tech_data/llm_settings.json` —— 于是"全局模型设置"只对一半进程生效。

这里把「读写 + 打码」收敛成一份实现，四个模块与技术工艺适配层共用：

  · load()      读唯一配置；文件缺失时可按调用方给的旧文件名只读兜底；
  · save()      原子写回（同目录临时文件 + os.replace），不产生半个文件；
  · api_keys()  按 provider 取全局 Key；
  · set_api_key 只增补指定 provider，绝不覆盖其它 provider 的既有值；
  · mask()      只产出打码提示，明文永远不离开本模块。

密钥安全：本模块不打印、不记录、不返回明文以外的任何形态 Key。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any, Iterable

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "cpq_settings.json")

_lock = threading.RLock()


def path_of(name: str) -> str:
    """旧配置文件在仓库根目录的同级路径（只读写兜底用）。"""
    return os.path.join(SCRIPT_DIR, os.path.basename(str(name or "")))


def load(legacy: Iterable[str] = ()) -> dict[str, Any]:
    """读唯一配置 cpq_settings.json。

    legacy 里的旧文件名只在唯一配置不存在（或不可读）时按顺序兜底 —— 用于兼容
    升级前留下的 xbom_settings.json / rule_settings.json，绝不新建第二份文件。
    """
    for path in (SETTINGS_PATH, *(path_of(name) for name in legacy)):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def save(data: dict[str, Any]) -> None:
    """原子写回唯一配置。写失败时抛出，由调用方决定是否回给用户。"""
    payload = data if isinstance(data, dict) else {}
    with _lock:
        directory = os.path.dirname(SETTINGS_PATH) or "."
        handle_fd, tmp_path = tempfile.mkstemp(
            prefix=".cpq_settings-", suffix=".json", dir=directory)
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, SETTINGS_PATH)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:                                  # pragma: no cover - 清理失败不掩盖原错
                pass
            raise


def merge_into(data: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """把 patch 合进配置：api_keys 按 provider 逐项合并，绝不整块覆盖。"""
    merged = dict(data or {})
    for key, value in (patch or {}).items():
        if key == "api_keys" and isinstance(value, dict):
            current = merged.get("api_keys")
            current = dict(current) if isinstance(current, dict) else {}
            current.update({k: v for k, v in value.items() if v})
            merged["api_keys"] = current
        else:
            merged[key] = value
    return merged


def api_keys(data: dict[str, Any] | None = None) -> dict[str, str]:
    """按 provider 取全局 Key（不返回给任何外部接口）。"""
    source = load() if data is None else data
    keys = (source or {}).get("api_keys")
    if not isinstance(keys, dict):
        return {}
    return {str(name): str(value) for name, value in keys.items() if value}


def set_api_key(provider: str, key: str) -> dict[str, Any]:
    """只增补指定 provider 的 Key；其它 provider 与其它字段保持原样。"""
    name = str(provider or "").strip()
    secret = str(key or "").strip()
    if not name or not secret:
        return load()
    with _lock:
        data = load()
        keys = dict(api_keys(data))
        keys[name] = secret
        data["api_keys"] = keys
        save(data)
        return data


def mask(secret: str) -> str:
    """只回打码提示：接口、页面和日志都只用它，明文不出本模块。"""
    value = str(secret or "").strip()
    if not value:
        return ""
    return f"{value[:7]}…{value[-4:]}" if len(value) > 14 else "已配置"
