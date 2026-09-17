# -*- coding: utf-8 -*-
"""首页项目卡摘要（批次 10A）—— 卡片上的每个字段都由**后端**算。

为什么单独成模块：在此之前首页拿的是粗粒度 `status` 字符串，再在前端那张
`statusOf()` 本地映射表里翻成中文（`报价首页.html`）。结果同一个项目在首页与工作台
顶部可能给出两个不同的「阶段」，卡片上也没有 owner / 在等谁 / 最后一次业务事件 /
有没有异常 —— 用户必须点进去才知道。

本模块把批次 5B 的统一流程投影、批次 6 的业务实例号、批次 9 的封闭任务词表与批次
10B 的业务时间线拼成一张卡片；只读、纯派生，不写库、不新建任何业务实例号。

角色口径：投影给的 `required_role` 是**中文标签**（如「工艺工程师」），卡片层负责把它
归一化成 `auth.ROLES` 里的角色码（`scope=todo` 直接拿它比对）；认不出来就给空串，
绝不编一个。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..storage import store
from . import auth, workflow_stages

STAGE_TOTAL = len(workflow_stages.STAGES)
_STAGE_INDEX = {row["sub"]: index for index, row in enumerate(workflow_stages.STAGES, start=1)}
# 还没收口的任务状态（批次 9 词表的子集 + 老记录里出现过的写法）。
_OPEN_TASK_STATUSES = ("queued", "running", "open", "claimed", "claimed_by")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,64}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stage_row(code: str) -> Optional[dict]:
    key = _text(code)
    for row in workflow_stages.STAGES:
        if row["sub"] == key:
            return row
    return None


def _display_name(username: str) -> str:
    name = _text(username)
    if not name:
        return ""
    try:
        user = store.get_user(name) or {}
    except Exception:  # noqa: BLE001
        user = {}
    return _text(user.get("display_name")) or name


def normalize_role(label: str) -> str:
    """中文角色标签 → 角色码（与 auth.ROLES 同源）；认不出来给空串。"""
    name = _text(label)
    if not name:
        return ""
    for code, text in getattr(auth, "ROLE_LABEL", {}).items():
        if text == name:
            return code
    try:
        from . import cpq_sso
        for code, text in getattr(cpq_sso, "TECH_ROLE_LABEL", {}).items():
            if text == name:
                return code
    except Exception:  # noqa: BLE001 - 映射表缺失不该让卡片读不出来
        pass
    return name if name in getattr(auth, "ROLES", ()) else ""


def _stage_block(code: str) -> Dict[str, Any]:
    row = _stage_row(code)
    if not row:
        return {"code": _text(code), "index": 0, "total": STAGE_TOTAL, "title": "", "phase": 0}
    return {"code": row["sub"], "index": _STAGE_INDEX.get(row["sub"], 0),
            "total": STAGE_TOTAL, "title": row["sub_title"], "phase": int(row["phase"])}


def _stage_url(project_id: str, row: dict) -> str:
    params = ["project=" + project_id, "stage=" + _text(row.get("stage_id"))]
    if _text(row.get("view")):
        params.append("view=" + _text(row.get("view")))
    return (_text(row.get("page")) or "index.html") + "?" + "&".join(params)


def _primary_action(project_id: str, code: str, next_action: Optional[dict]) -> Optional[dict]:
    row = _stage_row(code)
    if not row:
        return None
    return {"id": "open-stage",
            "label": "去 " + row["sub"] + " " + row["sub_title"],
            "required_role": normalize_role(_text((next_action or {}).get("required_role"))),
            "target": _stage_url(project_id, row)}


def _waiting_for(project_id: str, next_action: Optional[dict]) -> Dict[str, Any]:
    try:
        tasks = store.list_tasks(project_id) or []
    except Exception:  # noqa: BLE001
        tasks = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if _text(task.get("status")) not in _OPEN_TASK_STATUSES:
            continue
        claimed = _text(task.get("claimed_by"))
        if claimed:
            return {"kind": "user", "role": "", "label": _display_name(claimed),
                    "username": claimed}
    if not next_action:
        return {"kind": "none", "role": "", "label": "", "username": ""}
    label = _text(next_action.get("required_role"))
    return {"kind": "role", "role": normalize_role(label), "label": label, "username": ""}


def _last_event(project_id: str, meta: dict) -> Dict[str, Any]:
    try:
        from . import timeline
        events = (timeline.build_timeline(project_id) or {}).get("events") or []
    except Exception:  # noqa: BLE001
        events = []
    if events:
        row = events[-1] or {}
        return {"action": _text(row.get("action")), "at": _text(row.get("at")),
                "actor": _text(row.get("actor"))}
    return {"action": "project_created", "at": _text((meta or {}).get("created_at")),
            "actor": _text((meta or {}).get("owner")) or "system"}


def handoff_error(project_id: str) -> str:
    """最近一次回传的失败原因（没有回传或回传成功时给空串）。"""
    try:
        raw = store.load_integration(project_id) or {}
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(raw, dict):
        return ""
    for key in ("quote_handoff_error", "handoff_error"):
        value = _text(raw.get(key))
        if value:
            return value
    return ""


def _anomaly(project_id: str, projection: dict, stages_rows: List[dict]) -> Dict[str, Any]:
    codes: List[str] = []
    try:
        tasks = store.list_tasks(project_id) or []
    except Exception:  # noqa: BLE001
        tasks = []
    statuses = {_text(row.get("status")) for row in tasks if isinstance(row, dict)}
    if "failed" in statuses:
        codes.append("task_failed")
    if "interrupted" in statuses:
        codes.append("task_interrupted")
    if projection and projection.get("refresh_ok") is False:
        codes.append("refresh_failed")
    if any(_text(row.get("status")) == "stale" for row in stages_rows or []):
        codes.append("stale")
    if handoff_error(project_id):
        codes.append("handoff_failed")
    return {"has_anomaly": bool(codes), "codes": codes}


def business_case_id(project_id: str) -> str:
    """业务实例号只从落盘文档取；没有就给空串，绝不现编（批次 6 口径）。"""
    try:
        return _text((store.load_business_case(project_id) or {}).get("business_case_id"))
    except Exception:  # noqa: BLE001
        return ""


def build_card(project_id: str, meta: Optional[dict] = None, user: Optional[dict] = None) -> Dict[str, Any]:
    """一张首页卡片；投影读不动时降级为空阶段，绝不抛。"""
    meta = meta if isinstance(meta, dict) else (store.load_meta(project_id) or {})
    projection: Dict[str, Any] = {}
    try:
        from . import workflow_projection
        projection = workflow_projection.build_projection(project_id, user or {}) or {}
    except Exception:  # noqa: BLE001 - 单项目投影损坏不该让整个列表 500
        projection = {}
    stages_rows = [row for row in (projection.get("stages") or []) if isinstance(row, dict)]
    next_action = projection.get("next_action") if isinstance(projection.get("next_action"), dict) else None
    code = _text((next_action or {}).get("sub"))
    if not code and stages_rows:
        code = _text((stages_rows[-1] or {}).get("sub"))
    return {
        "owner": _text(meta.get("owner")),
        "owner_display_name": _text(meta.get("owner_display_name")) or _text(meta.get("owner")),
        "stage": _stage_block(code),
        "waiting_for": _waiting_for(project_id, next_action),
        "last_event": _last_event(project_id, meta),
        "anomaly": _anomaly(project_id, projection, stages_rows),
        "business_case_id": business_case_id(project_id),
        "primary_action": _primary_action(project_id, code, next_action),
    }


def current_stage_actionable(project_id: str, user: Optional[dict] = None) -> bool:
    """当前该做的子步骤，此刻能不能由这个人执行（`scope=todo` 的唯一判据）。"""
    try:
        from . import workflow_projection
        projection = workflow_projection.build_projection(project_id, user or {}) or {}
    except Exception:  # noqa: BLE001
        return False
    next_action = projection.get("next_action") if isinstance(projection.get("next_action"), dict) else None
    code = _text((next_action or {}).get("sub"))
    if not code:
        return False
    for row in projection.get("stages") or []:
        if isinstance(row, dict) and _text(row.get("key")) == code:
            return bool(row.get("actionable"))
    return False


def sort_entries(rows: List[dict]) -> List[dict]:
    """last_event.at 降序 → updated_at 降序 → project_id 升序（稳定）。"""
    def at_of(row: dict) -> str:
        card = row.get("card") if isinstance(row.get("card"), dict) else {}
        event = card.get("last_event") if isinstance(card.get("last_event"), dict) else {}
        return _text(event.get("at"))
    out = list(rows)
    out.sort(key=lambda row: _text((row or {}).get("project_id")))
    out.sort(key=lambda row: _text((row or {}).get("updated_at")), reverse=True)
    out.sort(key=at_of, reverse=True)
    return out
