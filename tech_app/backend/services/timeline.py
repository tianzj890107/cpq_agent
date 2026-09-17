# -*- coding: utf-8 -*-
"""跨流程业务时间线（批次 10B）—— 只读、幂等、每条都能点进去。

为什么单独成模块：今天能读到的两块都不是业务时间线 —— `store.audit()` 只写
`{ts, action, detail}`（没有 actor / 角色 / 状态迁移 / 跳转目标），
`/api/projects/{pid}/agent/events` 回放的是 Agent 会话流（模型这段时间说了什么），
不是「这单业务上发生了什么」。于是「从报价创建到回传报价」只能跨三个页面手工拼。

本模块把已经落盘的业务事实（项目 meta、业务实例关联、IR、任务、整机计划、成本评审、
报告）按**时间**拼成一条事件流，每条事件回答：谁（actor / role）、什么时候（at）、
做了什么（action / label）、从哪个状态到哪个状态（from_state / to_state）、点它能
去哪（target.url）。

只读、纯派生：不写库、不迁移、不新建任何业务实例号；历史项目缺业务实例号时，顶层与
每条事件都给空串（批次 6 口径：绝不现编）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..storage import store
from ..time_utils import now_cst_str
from . import workflow_stages

_TECH_PAGE = "tech-workbench.html"
_FALLBACK_AT = "1970-01-01 00:00:00"
_FALLBACK_ROLE = "engineer"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _identity(username: str, fallback_role: str = "") -> tuple:
    """(actor, actor_display, role)：用户档案查不到时用 fallback_role，role 永远非空。"""
    name = _text(username) or "system"
    user: Dict[str, Any] = {}
    try:
        user = store.get_user(name) or {}
    except Exception:  # noqa: BLE001 - 身份档案缺失不该让整条时间线读不出来
        user = {}
    display = _text(user.get("display_name")) or name
    role = _text(user.get("role")) or _text(fallback_role) or _FALLBACK_ROLE
    return name, display, role


def _stage_target(project_id: str, stage_id: str, view: str = "", label: str = "") -> dict:
    row = workflow_stages.default_row(stage_id) or {}
    page = _text(row.get("page")) or "index.html"
    params = ["project=" + project_id, "stage=" + stage_id]
    if view or row.get("view"):
        params.append("view=" + (view or _text(row.get("view"))))
    return {"kind": "tech",
            "label": label or workflow_stages.stage_title(stage_id) or "打开步骤",
            "url": page + "?" + "&".join(params)}


def _project_target(project_id: str, label: str = "打开项目") -> dict:
    return {"kind": "tech", "label": label, "url": _TECH_PAGE + "?project=" + project_id}


def _quote_target(session_id: str, label: str = "打开原报价") -> dict:
    return {"kind": "quote", "label": label,
            "url": "/?assistant=quote&session=" + session_id}


def _report_target(project_id: str, label: str = "打开工艺评估报告") -> dict:
    return {"kind": "report", "label": label,
            "url": "report-publish.html?project=" + project_id}


def _task_target(project_id: str, task_id: str, label: str = "查看任务") -> dict:
    return {"kind": "task", "label": label,
            "url": _TECH_PAGE + "?project=" + project_id + "&task=" + task_id}


def build_timeline(project_id: str) -> Dict[str, Any]:
    """一条项目的业务时间线：events 按 at 升序、同 at 按 seq 升序。"""
    pid = _text(project_id)
    meta = store.load_meta(pid) or {}
    link = store.load_business_case(pid) or {}
    bc_id = _text(link.get("business_case_id"))
    created_at = _text(meta.get("created_at")) or _FALLBACK_AT
    owner = _text(meta.get("owner")) or "system"
    stages = meta.get("stages") if isinstance(meta.get("stages"), dict) else {}
    session_id = _text(link.get("quote_session_id"))
    source_task_id = _text(link.get("source_task_id"))
    rows: List[Dict[str, Any]] = []

    def add(at, actor, role, action, label, kind, *, frm="", to="", session="",
            task="", version="", target=None, display=""):
        who, disp, real_role = _identity(actor, role)
        rows.append({
            "at": _text(at) or created_at,
            "actor": who,
            "actor_display": _text(display) or disp,
            "role": real_role,
            "action": action,
            "label": label,
            "action_kind": kind,
            "from_state": frm,
            "to_state": to,
            "session_id": _text(session),
            "task_id": _text(task),
            "version": _text(version),
            "target": target or _project_target(pid),
        })

    # ---- 报价 → 技术支线：项目起源 -------------------------------------- #
    add(created_at, owner, _FALLBACK_ROLE, "project_created", "项目创建", "tech",
        to="created")
    if bc_id or session_id:
        add(_text(link.get("linked_at")) or created_at, owner, "sales_manager",
            "quote_created", "报价创建", "quote", session=session_id, to="in_progress",
            target=_quote_target(session_id) if session_id else _project_target(pid))
    if session_id or source_task_id:
        add(created_at, owner, "process_manager", "tech_branch_started", "技术支线开始",
            "tech", frm="quote", to="tech", session=session_id, task=source_task_id,
            target=_stage_target(pid, "requirement-create"))

    # ---- 1.x 需求 / 2.1 图纸解析 --------------------------------------- #
    requirement = store.load_requirement(pid)
    if isinstance(requirement, dict) and requirement:
        req_status = _text(requirement.get("status"))
        req_by = _text(requirement.get("created_by")) or _text(requirement.get("updated_by")) or owner
        add(_text(requirement.get("created_at")) or created_at, req_by, _FALLBACK_ROLE,
            "requirement_created", "需求单创建", "tech", to=req_status or "draft",
            target=_stage_target(pid, "requirement-create"))
        if req_status in ("pending_confirmation", "confirmed", "pending_review", "approved"):
            add(_text(requirement.get("confirmed_at")) or _text(requirement.get("updated_at")) or created_at,
                req_by, _FALLBACK_ROLE, "requirement_confirmed", "需求确认", "tech",
                frm="draft", to=req_status, target=_stage_target(pid, "requirement-confirm"))
    try:
        ir = store.load_ir(pid)
    except Exception:  # noqa: BLE001
        ir = None
    if ir:
        add(_text(stages.get("parsed")) or created_at, owner, _FALLBACK_ROLE,
            "drawing_parsed", "图纸解析完成", "tech", frm="uploaded", to="parsed",
            target=_stage_target(pid, "drawing"))

    # ---- 3.x 整合 / 参数 / 工艺 + 4.x 成本 ------------------------------ #
    plan = None
    try:
        from . import integration
        plan = integration.load_plan(pid)
    except Exception:  # noqa: BLE001 - 整机计划缺失只影响这几条事件
        plan = None
    if plan is not None:
        if bool(getattr(plan, "params_confirmed", False)):
            add(_text(getattr(plan, "params_confirmed_at", "")) or created_at,
                _text(getattr(plan, "params_confirmed_by", "")) or owner, _FALLBACK_ROLE,
                "params_confirmed", "参数推荐已确认", "tech", frm="generated", to="confirmed",
                target=_stage_target(pid, "process", view="params"))
        if bool(getattr(plan, "process_confirmed", False)):
            add(_text(getattr(plan, "process_confirmed_at", "")) or created_at,
                _text(getattr(plan, "process_confirmed_by", "")) or owner, _FALLBACK_ROLE,
                "process_confirmed", "组装工艺已确认", "tech", frm="generated", to="confirmed",
                target=_stage_target(pid, "process", view="process"))
        if getattr(plan, "cost", None) is not None:
            add(_text(getattr(plan, "updated_at", "")) or created_at, owner, "finance_manager",
                "cost_calculated", "整机成本测算完成", "cost", to="generated",
                target=_stage_target(pid, "cost", view="total"))
        finance_handoff = getattr(plan, "finance_handoff", None)
        if finance_handoff is not None and (_text(getattr(finance_handoff, "sent_at", ""))
                                            or _text(getattr(finance_handoff, "task_id", ""))):
            fh_task = _text(getattr(finance_handoff, "task_id", ""))
            add(_text(getattr(finance_handoff, "sent_at", "")) or created_at,
                _text(getattr(finance_handoff, "sent_by", "")) or owner, "process_manager",
                "cost_returned", "成本测算交财务复核", "cost", task=fh_task,
                to="awaiting_confirmation",
                target=_task_target(pid, fh_task) if fh_task else _stage_target(pid, "cost", view="total"))
    try:
        from . import cost_review
        review = cost_review.load_review(pid)
    except Exception:  # noqa: BLE001
        review = None
    if review is not None and bool(getattr(review, "confirmed", False)):
        add(_text(getattr(review, "confirmed_at", "")) or created_at,
            _text(getattr(review, "confirmed_by", "")) or owner, "finance_manager",
            "cost_confirmed", "成本已正式确认", "cost", frm="awaiting_confirmation",
            to="confirmed", target=_stage_target(pid, "cost", view="total"))

    # ---- 任务：派发 / 领取 / 终态 -------------------------------------- #
    for task in store.list_tasks(pid) or []:
        if not isinstance(task, dict):
            continue
        tid = _text(task.get("task_id"))
        at = _text(task.get("created_at")) or created_at
        actor = _text(task.get("created_by")) or _text(task.get("claimed_by")) or owner
        task_role = _text(task.get("role")) or _FALLBACK_ROLE
        title = _text(task.get("label")) or tid or "任务"
        add(at, actor, task_role, "task_sent", "任务派发：" + title, "task", task=tid,
            to="queued", target=_task_target(pid, tid))
        claimed_by = _text(task.get("claimed_by"))
        if claimed_by:
            add(_text(task.get("claimed_at")) or at, claimed_by, task_role, "task_claimed",
                "任务已领取：" + title, "task", task=tid, frm="queued", to="running",
                target=_task_target(pid, tid))
        status = _text(task.get("status"))
        finished = _text(task.get("finished_at")) or at
        terminal = {"failed": ("task_failed", "任务失败：" + title, "failed"),
                    "interrupted": ("task_interrupted", "任务中断：" + title, "interrupted"),
                    "cancelled": ("task_cancelled", "任务已取消：" + title, "cancelled"),
                    "canceled": ("task_cancelled", "任务已取消：" + title, "cancelled"),
                    "succeeded": ("task_completed", "任务完成：" + title, "succeeded"),
                    "partial": ("task_completed", "任务部分完成：" + title, "partial")}
        if status in terminal:
            action, label, to_state = terminal[status]
            add(finished, claimed_by or actor, task_role, action, label, "task", task=tid,
                frm="running", to=to_state, target=_task_target(pid, tid))

    # ---- 5.x 报告：送审 / 审核 / 发布 / 回传 ---------------------------- #
    report = store.load_process_report(pid) or {}
    if isinstance(report, dict) and report:
        report_status = _text(report.get("status"))
        version = report.get("version")
        if report_status in ("in_review", "approved", "published"):
            add(_text(report.get("submitted_at")) or _text(report.get("created_at")) or created_at,
                _text(report.get("created_by")) or owner, "process_manager",
                "report_submitted", "报告送审", "report", version=version, to="in_review",
                target=_report_target(pid))
        if _text(report.get("reviewed_at")):
            add(report.get("reviewed_at"), _text(report.get("reviewed_by")), "process_director",
                "report_reviewed", "报告审核完成", "report", version=version,
                frm="in_review", to="approved", target=_report_target(pid))
        if report_status == "published":
            add(_text(report.get("published_at")) or created_at,
                _text(report.get("published_by")) or owner, "process_director",
                "report_published", "报告已发布", "report", version=version, to="published",
                target=_report_target(pid))
    if plan is not None:
        handoff = getattr(plan, "quote_handoff", None)
        hs = _text(getattr(handoff, "session_id", "")) if handoff is not None else ""
        if handoff is not None and (hs or _text(getattr(handoff, "sent_at", ""))):
            at = _text(getattr(handoff, "sent_at", "")) or created_at
            by = _text(getattr(handoff, "sent_by", "")) or owner
            add(at, by, "process_manager", "report_sent_to_quote", "回传销售经理继续报价",
                "handoff", session=hs, task=_text(getattr(handoff, "task_id", "")),
                to="handed_off", target=_quote_target(hs) if hs else _report_target(pid))
            step_no = getattr(handoff, "next_step_no", None)
            if step_no:
                add(at, by, "sales_manager", "quote_resumed",
                    "报价继续至第 " + _text(step_no) + " 步", "quote", session=hs,
                    frm="handed_off", to="in_progress",
                    target=_quote_target(hs) if hs else _project_target(pid))

    rows.sort(key=lambda row: row["at"])
    events: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        events.append({
            "seq": index,
            "at": row["at"],
            "actor": row["actor"],
            "actor_display": row["actor_display"],
            "role": row["role"],
            "action": row["action"],
            "label": row["label"],
            "action_kind": row["action_kind"],
            "from_state": row["from_state"],
            "to_state": row["to_state"],
            "project_id": pid,
            "session_id": row["session_id"],
            "task_id": row["task_id"],
            "version": row["version"],
            "business_case_id": bc_id,
            "target": row["target"],
        })
    return {"project_id": pid, "business_case_id": bc_id,
            "generated_at": now_cst_str(), "events": events}
