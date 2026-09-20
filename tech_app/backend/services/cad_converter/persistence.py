"""转换产物与 manifest 的**唯一写盘入口**（Spec §3.1 / §3.2）。

落盘沿用既有的 blob 后端约定（`<project_id>/conversions/<conversion_id>/`），
不另起第二套目录：本地后端就是磁盘目录，远端后端由 `sync_dir` 同步。

manifest 走一份**按项目**的索引文档（`<project_id>/conversions/manifests.json`）：
既能按 conversion_id 回看，也能列出该项目全部转换（含失败的），不需要依赖
对象存储的列举能力。索引的「读-改-写」在同一把锁里完成，避免并发丢版本。
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...storage.blob_backend import get_blob_backend
from ...storage import store

#: manifest 一律放同一份索引文档，便于 list_manifests 不做目录遍历。
INDEX_FILENAME = "manifests.json"

_locks_guard = threading.Lock()
_project_locks: Dict[str, threading.Lock] = {}


def _lock_for(project_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _project_locks.get(str(project_id))
        if lock is None:
            lock = threading.Lock()
            _project_locks[str(project_id)] = lock
        return lock


def _blob():
    return get_blob_backend()


def _safe_name(filename: str) -> str:
    return Path(str(filename or "")).name


def _conversion_prefix(project_id: str, conversion_id: str) -> str:
    return f"{project_id}/conversions/{conversion_id}"


def _index_key(project_id: str) -> str:
    return f"{project_id}/conversions/{INDEX_FILENAME}"


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
def artifact_dir(project_id: str, conversion_id: str) -> Path:
    """该次转换的正式产物目录（本地工作目录，结束前由 sync 同步到对象存储）。"""
    return _blob().ensure_local_dir(_conversion_prefix(project_id, conversion_id))


def save_artifact(project_id: str, conversion_id: str, filename: str, data: bytes) -> dict:
    """写入一个产物文件；返回 {"filename","sha256","bytes"}（只认 basename）。"""
    name = _safe_name(filename)
    payload = bytes(data)
    target = artifact_dir(project_id, conversion_id) / name
    target.write_bytes(payload)
    return {"filename": name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload)}


def sync(project_id: str, conversion_id: str) -> None:
    """把本地工作目录同步到对象存储（本地后端为空操作）。"""
    _blob().sync_dir(_conversion_prefix(project_id, conversion_id))


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def _read_index(project_id: str) -> List[dict]:
    raw = _blob().get_bytes(_index_key(project_id))
    if not raw:
        return []
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    items = parsed.get("manifests") if isinstance(parsed, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def _write_index(project_id: str, items: List[dict]) -> None:
    payload = json.dumps({"manifests": items}, ensure_ascii=False, indent=2)
    _blob().put_bytes(_index_key(project_id), payload.encode("utf-8"))


def save_manifest(project_id: str, manifest: dict) -> dict:
    """写入/覆盖一条 manifest（同一 conversion_id 幂等），返回写入内容。"""
    item = dict(manifest)
    with _lock_for(project_id):
        items = [m for m in _read_index(project_id)
                 if str(m.get("conversion_id")) != str(item.get("conversion_id"))]
        items.append(item)
        _write_index(project_id, items)
    return dict(item)


def load_manifest(project_id: str, conversion_id: str) -> Optional[dict]:
    with _lock_for(project_id):
        items = _read_index(project_id)
    for item in items:
        if str(item.get("conversion_id")) == str(conversion_id):
            return dict(item)
    return None


def list_manifests(project_id: str) -> List[dict]:
    """该项目全部 manifest（含失败），时间倒序（最后写入的在最前）。"""
    with _lock_for(project_id):
        items = _read_index(project_id)
    return [dict(item) for item in reversed(items)]


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #
def audit(project_id: str, action: str, detail: Any = None) -> None:
    """审计入口（唯一）：字段由调用方保证不含密钥/堆栈/绝对路径/原始字节。"""
    store.audit(project_id, action, detail)
