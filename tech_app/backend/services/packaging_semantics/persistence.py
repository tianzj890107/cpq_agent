"""语义文档落盘（doc key `packaging_semantics`）—— 本包唯一写盘入口。

版本只增不改：同一 `semantics_id` 覆盖同一条，新 IR 追加一条；`load_semantics`
按 id 回看历史版本，`list_semantics` 返回时间倒序。审计只在真实项目上留痕。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...storage.meta_backend import get_backend
from . import model

#: 每个项目最多保留的语义版本数（旧版本仍可按 id 回看）。
MAX_VERSIONS = 20


def _load_items(project_id: str) -> List[Dict[str, Any]]:
    doc = get_backend().get_doc(project_id, model.SEMANTICS_DOC_KEY) or {}
    items = doc.get("items") if isinstance(doc, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def save_semantics(project_id: str, semantics: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(semantics, dict):
        raise ValueError("save_semantics() 需要一份语义文档")
    semantics_id = str(semantics.get("semantics_id") or "")
    items = [item for item in _load_items(project_id)
             if str(item.get("semantics_id") or "") != semantics_id]
    items.insert(0, semantics)
    get_backend().put_doc(project_id, model.SEMANTICS_DOC_KEY,
                          {"items": items[:MAX_VERSIONS]})
    return semantics


def load_semantics(project_id: str,
                   semantics_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for item in _load_items(project_id):
        if semantics_id is None or item.get("semantics_id") == semantics_id:
            return item
    return None


def list_semantics(project_id: str) -> List[Dict[str, Any]]:
    return _load_items(project_id)


def audit(project_id: str, action: str, detail: Optional[Dict[str, Any]] = None) -> None:
    """审计动作名见 Spec §14；只对真实存在的项目留痕，且绝不因审计失败中断主流程。"""
    from ...storage import store

    try:
        if not store.load_meta(project_id):
            return
        store.audit(project_id, action, detail or {})
    except Exception:  # noqa: BLE001
        return
