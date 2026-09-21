"""CAD IR 落盘（**唯一写盘入口**，DWG 第 3 批 Spec §6.4）。

两处落点，职责不同：
  · `store` 文档位 `cad_ir`：当前这一版（「本次任务从头开始」按 `PARSE_STAGE_DOCS` 清掉它）；
  · blob 目录 `<project_id>/cad_ir/`：按 `ir_id` 逐版本留档，配一份索引文档供列表回看。

CAD IR **不占用** DesignIR 的文档位，也不触发它的「设计 IR 已更新」失效链（Spec §11）。
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from ...storage import store
from ...storage.blob_backend import get_blob_backend
from . import model

#: 索引文档名（与逐版本快照同目录）
INDEX_FILENAME = "index.json"

#: 逐版本快照保留上限（超出丢弃最旧的一条，只动本包自己的目录）
MAX_VERSIONS = 20

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


def _prefix(project_id: str) -> str:
    return "%s/cad_ir" % str(project_id)


def _index_key(project_id: str) -> str:
    return "%s/%s" % (_prefix(project_id), INDEX_FILENAME)


def _snapshot_key(project_id: str, ir_id: str) -> str:
    return "%s/%s.json" % (_prefix(project_id), str(ir_id))


def _read_index(project_id: str) -> List[dict]:
    raw = _blob().get_bytes(_index_key(project_id))
    if not raw:
        return []
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    items = parsed.get("items") if isinstance(parsed, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def _write_index(project_id: str, items: List[dict]) -> None:
    payload = json.dumps({"items": items}, ensure_ascii=False, indent=2)
    _blob().put_bytes(_index_key(project_id), payload.encode("utf-8"))


def _entry(ir: Dict[str, Any], ts: str) -> Dict[str, Any]:
    source = ir.get("source") or {}
    return {
        "ir_id": str(ir.get("ir_id") or ""),
        "ir_hash": str(ir.get("ir_hash") or ""),
        "ir_version": str(ir.get("ir_version") or ""),
        "ts": ts,
        "source": {key: source.get(key) for key in
                   ("kind", "project_id", "attachment_name", "conversion_id",
                    "converter_name", "converter_role", "fallback_used",
                    "detected_dwg_version", "drawing_version")},
        "stats": dict(ir.get("stats") or {}),
    }


def _stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_ir(project_id: str, ir: dict) -> dict:
    """写入/覆盖一条 IR（同一 ir_id 幂等），返回写入内容。"""
    item = dict(ir or {})
    if not item.get("ir_hash"):
        item["ir_hash"] = model.ir_hash(item)
    if not item.get("ir_id"):
        item["ir_id"] = item["ir_hash"][:16]
    ir_id = str(item["ir_id"])
    with _lock_for(project_id):
        _blob().put_bytes(_snapshot_key(project_id, ir_id),
                          json.dumps(item, ensure_ascii=False).encode("utf-8"))
        items = [row for row in _read_index(project_id) if str(row.get("ir_id")) != ir_id]
        items.append(_entry(item, _stamp()))
        _write_index(project_id, items[-MAX_VERSIONS:])
    store.save_cad_ir(project_id, item)
    return dict(item)


def load_ir(project_id: str, ir_id: Optional[str] = None) -> Optional[dict]:
    with _lock_for(project_id):
        items = _read_index(project_id)
        wanted = str(ir_id) if ir_id else (str(items[-1].get("ir_id")) if items else "")
    if not wanted:
        return store.load_cad_ir(project_id)
    raw = _blob().get_bytes(_snapshot_key(project_id, wanted))
    if not raw:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def list_irs(project_id: str) -> List[dict]:
    """该项目全部 IR 的**时间倒序**元信息（不含完整实体明细）。"""
    with _lock_for(project_id):
        items = _read_index(project_id)
    return [dict(item) for item in reversed(items)]


def audit(project_id: str, action: str, detail: Any = None) -> None:
    """审计入口（唯一）：字段由调用方保证不含密钥/堆栈/绝对路径/原始字节。"""
    store.audit(project_id, action, detail)
