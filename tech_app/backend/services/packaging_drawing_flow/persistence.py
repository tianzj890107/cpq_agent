"""落盘（唯一写盘入口，第 5 批 Spec §2.4）。

三个文档位都走 `store` 的元数据后端；包级函数一律**转调**这里，测试在
persistence 层注入内存实现即可整体替换。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from tech_app.backend.storage import store

FLOW_DOC = "packaging_drawing_flow"
ANCHOR_DOC = "packaging_flow_anchor"
STALE_DOC = "packaging_downstream_stale"

#: 「本次任务从头开始」要能清掉的文档位。
PARSE_STAGE_DOCS = (FLOW_DOC, ANCHOR_DOC, STALE_DOC)


def _put(project_id: str, doc_key: str, payload: Any) -> Dict[str, Any]:
    data = dict(payload or {})
    store._meta().put_doc(project_id, doc_key, data)
    return data


def _get(project_id: str, doc_key: str) -> Optional[Dict[str, Any]]:
    row = store._meta().get_doc(project_id, doc_key)
    return dict(row) if isinstance(row, dict) else None


def save_flow(project_id: str, flow: dict) -> dict:
    return _put(project_id, FLOW_DOC, flow)


def load_flow(project_id: str) -> Optional[dict]:
    return _get(project_id, FLOW_DOC)


def save_anchor(project_id: str, anchor: dict) -> dict:
    return _put(project_id, ANCHOR_DOC, anchor)


def load_anchor(project_id: str) -> Optional[dict]:
    return _get(project_id, ANCHOR_DOC)


def save_stale(project_id: str, stale: dict) -> dict:
    return _put(project_id, STALE_DOC, stale)


def load_stale(project_id: str) -> Optional[dict]:
    return _get(project_id, STALE_DOC)
