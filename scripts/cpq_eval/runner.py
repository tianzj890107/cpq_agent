# -*- coding: utf-8 -*-
"""CPQ 业务回归数据集 runner（离线、分层、数据驱动）。

用法（见 `--help`）：

    python3 -m scripts.cpq_eval.runner --list
    python3 -m scripts.cpq_eval.runner --validate
    python3 -m scripts.cpq_eval.runner --layer deterministic
    python3 -m scripts.cpq_eval.runner --layer recorded_provider
    python3 -m scripts.cpq_eval.runner --domain quote
    python3 -m scripts.cpq_eval.runner --priority P0
    python3 -m scripts.cpq_eval.runner --case quote.step1.parse.basic
    python3 -m scripts.cpq_eval.runner --report /tmp/cpq_eval_report

执行模型（分层）：
  · deterministic      —— 完全离线。案例在一个**受控假库 + 状态机**上执行；状态机的规则逐条
                          来自仓库现有 spec 与源码事实源，并**真的调用**仓库里的纯函数：
                          `cpq_wf.QUOTE_STEPS` / `advance_step_no`、`cpq_case_link.decide` /
                          `dedupe_candidates`、`tech_app/backend/services/workflow_stages.py` 的
                          5 阶段 × 13 子步骤表。表里没有真实 Postgres / PDT / 外网 / 真实模型。
  · recorded_provider   —— 回放 ``dataset/evals/cpq/fixtures/provider/*.json`` 里录制的 SSE / 工具
                          调用；api key 只允许 ``test-key``。
  · integration         —— 默认跳过；只有显式 ``CPQ_EVAL_INTEGRATION=1`` 且显式给出
                          ``--target-url`` 才运行，且拒绝 pdt / prod / 172.16.10.34 / :8010。

本模块不改任何业务实现、不写仓库（报告默认写临时目录）、不调用删除 / 重置接口。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

if __package__ in (None, ""):  # 允许 python3 scripts/cpq_eval/runner.py 直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cpq_eval import (  # noqa: E402
    CASES_DIR, DOMAINS, EXECUTORS, FAILURE_TYPES, GATE_EXECUTORS, LAYERS, PRIORITIES,
    PRODUCTION_EXECUTORS, QUOTE_ROLE_BY_STEP, QUOTE_STEPS, ROLES, ROOT, STAGE_BY_SUB, STAGES,
    find_legacy_wording, stage_title,
)
from scripts.cpq_eval import checks as checks_mod  # noqa: E402
from scripts.cpq_eval import coverage as coverage_mod  # noqa: E402
from scripts.cpq_eval import prodkit  # noqa: E402
from scripts.cpq_eval import production as production_mod  # noqa: E402
from scripts.cpq_eval import scoring as scoring_mod  # noqa: E402
from scripts.cpq_eval.dataset import (  # noqa: E402
    DatasetError, fixture_exists, fixture_refs, load_cases, load_fixtures, load_schema,
    load_suites, validate,
)

PROD_HOST_RE = re.compile(r"(172\.16\.10\.34|:8010\b|\bpdt\b|\bprod\b|prod\.)", re.I)
MONEY_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

# UI 协议在引擎里的默认值（由 ui_* 动作按状态机数据算出，不是写死的常量断言）
UI_DEFAULTS = {
    "board_left_right_sync": True,
    "field_source_badge": ["ai_recommended"],
    "part_detail_in_board": True,
    "params_tab_phase": 3,
    "single_primary_action": True,
    "error_surface": "board",
    "brand_color": "#0060e6",
    "model_button_in_composer": False,
    "composer_bottom_aligned": True,
    "critical_failure_persistent": False,
    "quiet_failure_no_card": False,
    "error_code_with_trace": True,
    "refresh_failure_keeps_state": True,
    "monotonic_ui_stage": True,
}

PRIMARY_ACTIONS = {
    "1.1": "submitRequirement", "1.2": "confirmRequirement", "1.3": "submitRequirementReview",
    "2.1": "parseDrawing", "3.1": "runIntegration", "3.2": "confirmParamsAndNext",
    "3.3": "confirmProcessAndNext", "4.1": "runCostReview", "4.2": "runCostReview",
    "4.3": "confirmCostReview", "5.1": "saveProcessReport", "5.2": "approveProcessReport",
    "5.3": "publishProcessReport",
}
SUB_ROLES = {
    "1.1": ("process_engineer", "process_mgr"), "1.2": ("process_engineer", "process_mgr"),
    "1.3": ("reviewer", "process_mgr"), "2.1": ("process_engineer", "process_mgr"),
    "3.1": ("process_engineer", "process_mgr"), "3.2": ("process_engineer", "process_mgr"),
    "3.3": ("process_engineer", "process_mgr"), "4.1": ("finance_mgr",),
    "4.2": ("finance_mgr",), "4.3": ("finance_mgr",),
    "5.1": ("process_engineer", "process_mgr"), "5.2": ("reviewer", "process_mgr"),
    "5.3": ("process_mgr", "reviewer", "admin"),
}
DEFAULT_ACTORS = {
    "alice": ("process_engineer",), "bob": ("process_engineer",), "carol": ("process_mgr",),
    "dave": ("finance_mgr",), "erin": ("sales_mgr",), "frank": ("reviewer",),
    "grace": ("admin",), "heidi": ("viewer",),
}
ERROR_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    409: "conflict", 422: "unprocessable_entity", 500: "internal_error",
    503: "service_unavailable",
}
COUNTER_NAMES = (
    "logins", "cards", "events", "messages", "tasks_sent", "tasks_reused", "tasks_superseded",
    "tasks_claimed", "tasks_completed", "tasks_cancelled", "handoffs_created", "handoff_replays",
    "cards_created_by_handoff", "requirement_parsed", "quote_steps_completed", "quote_recomputes",
    "plans", "documents", "tech_projects", "drawing_parses", "params_recommend", "cost_parts",
    "session_events", "projections", "fixture_loads", "restarts", "case_link_decisions",
    "material_duplicates", "material_writes", "material_code_conflicts", "retries", "rollbacks",
    "acl_access", "acl_list_mine", "acl_list_all", "acl_list_archived", "http_calls", "llm_calls",
    "open_home_card", "open_history_drawer", "open_task", "restore_home_card", "restore_history_drawer",
    "restore_refresh", "restore_back_forward", "tech_projects",
)
SUB_PREREQUISITES = {
    "1.2": ("1.1",), "1.3": ("1.2",), "2.1": ("1.3",), "3.1": ("2.1",),
    "3.2": ("3.1",), "3.3": ("3.2",), "4.1": ("3.3",), "4.2": ("4.1",),
    "4.3": ("4.2",), "5.1": ("4.3",), "5.2": ("5.1",), "5.3": ("5.2",),
}


class SimError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code} {message}".strip())
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)


class CaseAssertionError(Exception):
    pass


def _dec(value, default: str = "0.0000") -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal(default)
    if isinstance(value, bool):
        raise SimError(422, "bad_money", "布尔不是金额")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip() or default
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise SimError(422, "bad_money", f"非法金额 {value!r}") from exc


def money(value) -> str:
    return f"{_dec(value):.4f}"


def _diff(a, b) -> dict:
    """只保留 b 与 a 不同的叶子（用于「重复提交不产生新版本」判定）。"""
    if isinstance(a, dict) and isinstance(b, dict):
        out = {}
        for key, value in b.items():
            if key not in a:
                out[key] = value
                continue
            delta = _diff(a[key], value)
            if delta:
                out[key] = delta
        return out
    if isinstance(a, list) and isinstance(b, list):
        return b if a != b else {}
    return {} if a == b else b


class Sim:
    """受控假库 + CPQ 状态机。所有动作都是纯内存操作，可重复、无外网。"""

    def __init__(self, case, fixtures: dict):
        self.case = case
        self.fixtures = fixtures
        self.lock = threading.RLock()
        self.clock = datetime(2026, 9, 17, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        self.actors = {}
        self.cards = {}
        self.projects = {}
        self.tasks = {}
        self.handoffs = {}
        self.messages = []
        self.events = []
        self.responses = []
        self.counter = Counter()
        self.signals = {}
        self.concurrency = {}
        self.llm = {"calls": [], "persisted": 0}
        self.ui = dict(UI_DEFAULTS)
        self.acl = {"tokens": {}, "expired": set(), "bodies": {}}
        self.session = {"token": "", "actor": ""}
        self.material = {"rows": [], "codes": [], "used_codes": []}
        self.idempotency = {}
        self.projection = {}
        self.restore = {}
        self.integration = {"enabled": False, "reachable": False, "status": 0, "probes": []}
        self.focus = {"card": "", "project": "", "step": "", "stage": ""}
        self._seeded = False
        self._trace_seq = 0
        self._task_seq = 0
        self._handoff_seq = 0
        self._card_seq = 0
        self._project_seq = 0

    # ---------------- 基础设施 ----------------
    def now(self) -> str:
        self.clock = self.clock + timedelta(seconds=1)
        return self.clock.isoformat(timespec="seconds")

    def trace(self) -> str:
        self._trace_seq += 1
        return f"tr_{self._trace_seq:06d}"

    def ok(self, code: str = "OK", **extra) -> dict:
        resp = {"status": 200, "ok": True, "code": code, "trace_id": self.trace()}
        resp.update(extra)
        return resp

    def error(self, status: int, code: str = "", message: str = "", **extra) -> dict:
        resp = {"status": int(status), "ok": False, "code": code or ERROR_CODES.get(int(status), "error"),
                "trace_id": self.trace()}
        if message:
            resp["message"] = message
        resp.update(extra)
        return resp

    def signal(self, name: str, value=True):
        self.signals[name] = value

    def ensure_defaults(self):
        if self._seeded:
            return
        self._seeded = True
        self.actors = {}
        self.acl["expired"] = set()
        self.cards = {}
        self.projects = {}
        self.tasks = {}
        self.handoffs = {}
        self.messages = []
        self.events = []
        self.counter = Counter()
        self.signals = {}
        self.concurrency = {}
        self.material = {"rows": [], "codes": [], "used_codes": []}
        self.llm = {"calls": [], "persisted": 0}
        pre = (self.case.get("input") or {}).get("actors") or {}
        for name, item in pre.items():
            self._seed_actor(name, item)
        for name, roles in DEFAULT_ACTORS.items():
            if name not in self.actors:
                self._seed_actor(name, {"roles": list(roles)})

    def _seed_actor(self, name: str, item: dict):
        roles = item.get("roles") or list(DEFAULT_ACTORS.get(name, ("viewer",)))
        actor = {
            "username": name,
            "roles": [str(role) for role in roles],
            "token": str(item.get("token") or f"tok_{name}"),
            "api_key": str(item.get("api_key") or "test-key"),
            "model": str(item.get("model") or "recorded-test"),
            "settings": dict(item.get("settings") or {}),
            "projects": list(item.get("projects") or []),
        }
        self.actors[name] = actor
        if actor["token"]:
            self.acl["tokens"][actor["token"]] = name
        for token in item.get("expired_tokens") or []:
            self.acl["expired"].add(str(token))
        for token in item.get("tokens") or []:
            self.acl["tokens"][str(token)] = name
        if actor["api_key"] != "test-key":
            self.signal("real_secret_used", True)
        return actor

    def actor(self, name: str) -> dict:
        self.ensure_defaults()
        if name in self.actors:
            return self.actors[name]
        return self._seed_actor(name, {"roles": list(DEFAULT_ACTORS.get(name, ("viewer",)))})

    def actor_roles(self, name: str) -> list:
        return list(self.actor(name)["roles"]) if name else []

    def require_actor(self, name: str) -> dict:
        if not name:
            raise SimError(401, "missing_token", "缺少登录凭证")
        return self.actor(name)

    def require_role(self, name: str, roles, code: str = "forbidden") -> dict:
        actor = self.require_actor(name)
        if not set(actor["roles"]) & set(roles):
            raise SimError(403, code, f"需要角色 {'/'.join(roles)}")
        return actor

    def add_event(self, kind: str, **fields):
        item = {"type": kind, "seq": len(self.events) + 1, "at": self.now()}
        item.update(fields)
        self.events.append(item)
        self.counter["events"] += 1
        return item

    def add_message(self, to: str, msg_type: str, title: str = "", **fields):
        item = {"to": to, "msg_type": msg_type, "title": title, "at": self.now()}
        item.update(fields)
        self.messages.append(item)
        self.counter["messages"] += 1
        return item

    # ---------------- 状态快照与回滚 ----------------
    def _dump(self) -> dict:
        return {
            "cards": self.cards, "projects": self.projects, "tasks": self.tasks,
            "handoffs": self.handoffs, "messages": self.messages, "events": self.events,
            "counter": self.counter, "material": self.material, "idempotency": self.idempotency,
            "projection": self.projection, "restore": self.restore, "llm": self.llm,
        }

    def _restore_dump(self, dump: dict):
        self.cards = dump["cards"]
        self.projects = dump["projects"]
        self.tasks = dump["tasks"]
        self.handoffs = dump["handoffs"]
        self.messages = dump["messages"]
        self.events = dump["events"]
        self.counter = dump["counter"]
        self.material = dump["material"]
        self.idempotency = dump["idempotency"]
        self.projection = dump["projection"]
        self.restore = dump["restore"]
        self.llm = dump["llm"]

    def atomic(self, fn, *, fail_code: str = "internal_error"):
        """一次业务命令 = 一个事务：任何异常都整体回滚（含消息 / 审计 / 任务 / 快照）。"""
        dump = copy.deepcopy(self._dump())
        try:
            return fn()
        except SimError as exc:
            self._restore_dump(dump)
            self.counter["rollbacks"] += 1
            self.signal("rolled_back", True)
            self.signal("partial_write", False)
            raise SimError(exc.status, exc.code, exc.message) from exc
        except Exception as exc:  # pragma: no cover - 未预期异常也要回滚
            self._restore_dump(dump)
            self.counter["rollbacks"] += 1
            self.signal("rolled_back", True)
            raise SimError(500, fail_code, f"{type(exc).__name__}: {exc}") from exc

    # ---------------- 快照：expected.state / records 的取值空间 ----------------
    def snapshot(self) -> dict:
        tasks = list(self.tasks.values())
        records = {
            "cards": len(self.cards),
            "projects": len(self.projects),
            "actors": len(self.actors),
            "tasks_total": len(tasks),
            "tasks_open": sum(1 for t in tasks if t["status"] == "open"),
            "tasks_claimed": sum(1 for t in tasks if t["status"] == "claimed"),
            "tasks_completed": sum(1 for t in tasks if t["status"] == "completed"),
            "tasks_cancelled": sum(1 for t in tasks if t["status"] == "cancelled"),
            "messages_total": len(self.messages),
            "events_total": len(self.events),
            "handoff_rows": len(self.handoffs),
            "material_rows": len(self.material["rows"]),
            "material_codes": len(self.material["used_codes"]),
            "llm_calls": len(self.llm["calls"]),
            "llm_persisted": self.llm["persisted"],
            "quote_versions": sum(len(card["versions"]) for card in self.cards.values()),
            "project_timeline_events": sum(len(p.get("timeline") or []) for p in self.projects.values()),
        }
        for name in COUNTER_NAMES:
            records.setdefault(name, int(self.counter.get(name, 0)))
        records.update(self.counter)
        return {
            "records": records,
            "signals": dict(self.signals),
            "concurrency": dict(self.concurrency),
            "ui": dict(self.ui),
            "llm": {"calls": self.llm["calls"], "persisted": self.llm["persisted"],
                    "tool_calls": [{"name": call["name"], "args": call["args"],
                                    "fixture": call["fixture"], "recovered": call["recovered"]}
                                   for call in self.llm["calls"]]},
            "quote": self.cards,
            "tech": self.projects,
            "tasks": self.tasks,
            "handoffs": self.handoffs,
            "messages": self.messages,
            "events": self.events,
            "actors": {name: {"roles": a["roles"], "api_key": a["api_key"], "model": a["model"]}
                       for name, a in self.actors.items()},
            "session": dict(self.session),
            "acl": {"bodies": self.acl["bodies"], "tokens": sorted(self.acl["tokens"])},
            "projection": self.projection,
            "restore": self.restore,
            "integration": copy.deepcopy(self.integration),
            "focus": dict(self.focus),
            "material": {"codes": self.material["used_codes"], "rows": self.material["rows"]},
        }

    def last_response(self) -> dict:
        return self.responses[-1] if self.responses else {}

    # ---------------- 动作调度 ----------------
    def run(self, action: dict) -> dict:
        self.ensure_defaults()
        op = str(action.get("op") or "").strip()
        handler = OPS.get(op)
        if handler is None:
            raise CaseAssertionError(f"未实现的动作 op={op}（必须在 runner.OPS 注册）")
        try:
            resp = handler(self, action)
            if resp is None:
                resp = self.ok()
        except SimError as exc:
            resp = self.error(exc.status, exc.code, exc.message)
        self.responses.append(resp)
        if isinstance(action.get("expect"), dict):
            problems = compare(action["expect"], resp, f"动作 {op} 的 expect")
            if problems:
                raise CaseAssertionError("；".join(problems))
        return resp


# --------------------------------------------------------------------------- #
# 动作实现
# --------------------------------------------------------------------------- #
LEGACY_SUB_TO_STAGE = {
    "1.1": "requirement-create", "1.2": "requirement-confirm", "1.3": "requirement-review",
    "2.1": "drawing", "2.2": "process", "2.3": "cost", "3.1": "summary",
    "3.2": "report-review", "3.3": "report-publish",
}


def _join(*parts) -> str:
    return "|".join(str(part or "") for part in parts)


def _fixture(sim: Sim, ref: str):
    if not ref:
        raise SimError(500, "fixture_missing", "案例未给出 fixture")
    if ref not in sim.fixtures:
        raise SimError(500, "fixture_missing", f"fixture 不存在：{ref}")
    return sim.fixtures[ref]


def _card(sim: Sim, sid: str) -> dict:
    if not sid or sid not in sim.cards:
        raise SimError(404, "card_not_found", f"报价卡片不存在：{sid}")
    return sim.cards[sid]


def _project(sim: Sim, pid: str) -> dict:
    if not pid or pid not in sim.projects:
        raise SimError(404, "project_not_found", f"项目不存在：{pid}")
    return sim.projects[pid]


def _task(sim: Sim, tid: str) -> dict:
    if not tid or tid not in sim.tasks:
        raise SimError(404, "task_not_found", "任务不存在")
    return sim.tasks[tid]


def _new_card(sim: Sim, session_id: str, item: dict) -> dict:
    import cpq_case_link

    sim._card_seq += 1
    business_case_id = str(item.get("business_case_id") or
                           cpq_case_link.new_business_case_id(session_id or str(sim._card_seq)))
    card = {
        "session_id": session_id, "card_id": str(item.get("card_id") or f"c_{sim._card_seq:03d}"),
        "project_id": str(item.get("project_id") or item.get("quote_project_id") or ""),
        "business_case_id": business_case_id,
        "title": str(item.get("title") or "报价卡片"),
        "customer": str(item.get("customer") or ""),
        "owner": str(item.get("owner") or ""),
        "current_owner": str(item.get("owner") or ""),
        "current_step": int(item.get("current_step") or 1),
        "overall_status": str(item.get("overall_status") or "in_progress"),
        "steps": {}, "fields": {}, "products": [], "plans": [], "selected_plan": "",
        "versions": [], "documents": [], "totals": {}, "handoff_notes": {},
        "idempotency": {}, "left_summary": {}, "product_match": {"score": "", "matched": False},
    }
    for no in range(1, min(card["current_step"], len(QUOTE_STEPS))):
        card["steps"][str(no)] = {"status": "done", "completed_by": card["owner"],
                                  "snapshot": {}, "stale": False, "at": sim.now()}
    for name, value in (item.get("fields") or {}).items():
        if isinstance(value, dict):
            card["fields"][name] = {"value": value.get("value"), "source": value.get("source") or "system"}
        else:
            card["fields"][name] = {"value": value, "source": "system"}
    card["totals"].update({key: money(value) for key, value in (item.get("totals") or {}).items()})
    sim.cards[session_id] = card
    if card["owner"]:
        sim.actor(card["owner"])
    sim.counter["cards"] += 1
    sim.add_event("card_created", card_id=card["card_id"], session_id=session_id,
                  business_case_id=business_case_id)
    sim.focus["card"] = session_id
    return card


def _new_project(sim: Sim, project_id: str, item: dict) -> dict:
    sim._project_seq += 1
    project = {
        "project_id": project_id,
        "title": str(item.get("title") or project_id),
        "owner": str(item.get("owner") or ""),
        "current_holder": str(item.get("current_holder") or item.get("owner") or ""),
        "participants": list(item.get("participants") or []),
        "archived": bool(item.get("archived") or False),
        "business_case": dict(item.get("business_case") or {}),
        "page_context": str(item.get("page_context") or ""),
        "history_page_contexts": list(item.get("history_page_contexts") or []),
        "requirement": dict(item.get("requirement") or {"status": "draft", "gaps": [],
                                                        "confirmed": False, "review": ""}),
        "drawing": dict(item.get("drawing") or {"parsed": False, "ir": False, "parts": 0,
                                                "empty_confirmed": False}),
        "integration": dict(item.get("integration") or {
            "drawings_done": False, "params": [], "params_confirmed": False,
            "params_waiver": False, "process_steps": [], "process_confirmed": False}),
        "cost": dict(item.get("cost") or {"parts": [], "excluded_parts": [], "assembly": {},
                                          "total": {}, "confirmed": False, "confirmed_by": ""}),
        "report": dict(item.get("report") or {"draft": {}, "status": "", "published": False,
                                              "handed_back": False, "versions": []}),
        "stale": dict(item.get("stale") or {"integration": False, "cost": False, "report": False}),
        "timeline": list(item.get("timeline") or []),
    }
    if project["owner"]:
        sim.actor(project["owner"])
    sim.projects[project_id] = project
    sim.add_event("project_created", project_id=project_id)
    sim.focus["project"] = project_id
    return project


def _task_send_internal(sim: Sim, card: dict, kind: str, target_type: str,
                        target_role: str, target_user: str, note: str, payload: dict,
                        actor: str, idem_key: str = "") -> dict:
    signature = _join(kind, target_type, target_role, target_user, note)
    version = str(payload.get("result_version") or payload.get("version") or "")
    for task in card_tasks(sim, card["card_id"], kind, statuses=("open", "claimed")):
        if task["signature"] == signature:
            sim.counter["tasks_reused"] += 1
            return {"task": task, "code": "reused", "reused": True}
        old = task
        sim._task_seq += 1
        new_task = {
            "task_id": f"t_{sim._task_seq:03d}", "task_no": f"TP-{sim._task_seq:04d}",
            "card_id": card["card_id"], "session_id": card["session_id"],
            "project_id": card["project_id"], "task_kind": kind, "target_type": target_type,
            "target_role_code": target_role, "target_user_id": target_user, "status": "open",
            "claimed_by_user_id": "", "payload": dict(payload), "signature": signature,
            "supersedes_task_id": old["task_id"], "replaced_by_task_id": "",
            "cancel_reason": "被新任务替代", "note": note, "idempotency_key": idem_key,
            "created_at": sim.now(),
        }
        old["status"] = "cancelled"
        old["replaced_by_task_id"] = new_task["task_id"]
        old["cancelled_at"] = sim.now()
        sim.tasks[new_task["task_id"]] = new_task
        sim.counter["tasks_sent"] += 1
        sim.counter["tasks_superseded"] += 1
        sim.add_event("task_superseded", task_id=old["task_id"],
                      replaced_by=new_task["task_id"], actor=actor)
        sim.add_message(_recipient_of(old), "task_superseded", "你的任务已被新任务替代",
                        task_id=old["task_id"], replaced_by=new_task["task_id"])
        sim.add_message(card["owner"], "task_superseded", "你派发的任务已被替代",
                        task_id=old["task_id"], replaced_by=new_task["task_id"])
        _notify_task(sim, new_task)
        return {"task": new_task, "code": "superseded", "superseded_task_id": old["task_id"]}

    sim._task_seq += 1
    task = {
        "task_id": f"t_{sim._task_seq:03d}", "task_no": f"TP-{sim._task_seq:04d}",
        "card_id": card["card_id"], "session_id": card["session_id"],
        "project_id": card["project_id"], "task_kind": kind, "target_type": target_type,
        "target_role_code": target_role, "target_user_id": target_user, "status": "open",
        "claimed_by_user_id": "", "payload": dict(payload), "signature": signature,
        "supersedes_task_id": "", "replaced_by_task_id": "", "cancel_reason": "",
        "note": note, "idempotency_key": idem_key, "created_at": sim.now(),
    }
    sim.tasks[task["task_id"]] = task
    sim.counter["tasks_sent"] += 1
    sim.add_event("task_sent", task_id=task["task_id"], task_kind=kind, target_role=target_role)
    _notify_task(sim, task)
    return {"task": task, "code": "sent", "reused": False}


def _recipient_of(task: dict) -> str:
    return task.get("target_user_id") or task.get("target_role_code") or "public_pool"


def _notify_task(sim: Sim, task: dict):
    sim.add_message(_recipient_of(task), "task_sent", "有新任务待领取",
                    task_id=task["task_id"], task_kind=task["task_kind"])
    sim.add_message(task.get("payload", {}).get("initiator") or "", "task_received",
                    "任务已派发", task_id=task["task_id"], task_kind=task["task_kind"])


def card_tasks(sim: Sim, card_id: str, kind: str = "", statuses=()) -> list:
    out = []
    for task in sim.tasks.values():
        if task["card_id"] != card_id:
            continue
        if kind and task["task_kind"] != kind:
            continue
        if statuses and task["status"] not in statuses:
            continue
        out.append(task)
    return sorted(out, key=lambda t: t["task_id"])


def _merge_snapshot(target: dict, snapshot: dict) -> dict:
    delta = _diff(target, snapshot)
    if delta:
        target.update(copy.deepcopy(snapshot))
    return delta


def _recompute_totals(card: dict):
    totals = card["totals"]
    base = _dec(totals.get("base_cost"))
    premium = _dec(totals.get("tech_premium"))
    adjust = _dec(totals.get("market_adjust"))
    margin = base + premium + adjust
    totals["profit_margin"] = money(margin)
    extra = sum((_dec(item.get("amount")) for item in (totals.get("charges") or [])), Decimal("0"))
    totals["extra_charges"] = money(extra)
    expected = margin + extra
    totals["expected_price"] = money(expected)
    discount = _dec(totals.get("discount"))
    totals["quote_price"] = money(expected * (Decimal("1") - discount))
    return totals


def _stage_done(project: dict, sub: str) -> bool:
    if sub == "1.1":
        return project["requirement"].get("status") in ("pending_review", "approved")
    if sub == "1.2":
        return bool(project["requirement"].get("completeness_checked")) and \
            bool(project["requirement"].get("confirmed"))
    if sub == "1.3":
        return project["requirement"].get("review") == "approved"
    if sub == "2.1":
        drawing = project["drawing"]
        return bool(drawing.get("parsed")) and bool(drawing.get("ir")) and (
            int(drawing.get("parts") or 0) > 0 or bool(drawing.get("empty_confirmed")))
    if sub == "3.1":
        return bool(project["integration"].get("drawings_done"))
    if sub == "3.2":
        return bool(project["integration"].get("params_confirmed"))
    if sub == "3.3":
        return bool(project["integration"].get("process_confirmed"))
    if sub == "4.1":
        return bool(project["cost"].get("parts"))
    if sub == "4.2":
        return bool(project["cost"].get("assembly"))
    if sub == "4.3":
        return bool(project["cost"].get("confirmed"))
    if sub == "5.1":
        return bool(project["report"].get("status"))
    if sub == "5.2":
        return project["report"].get("status") in ("approved", "published")
    if sub == "5.3":
        return bool(project["report"].get("published")) and bool(project["report"].get("handed_back"))
    return False


def _stage_stale(project: dict, sub: str) -> bool:
    stale = project.get("stale") or {}
    if sub.startswith("3."):
        return bool(stale.get("integration"))
    if sub.startswith("4."):
        return bool(stale.get("cost"))
    if sub.startswith("5."):
        return bool(stale.get("report"))
    return False


def _projection(sim: Sim, project: dict, actor: str) -> dict:
    roles = sim.actor_roles(actor)
    phases = {row["no"]: {"no": row["no"], "title": row["title"]} for row in
              [{"no": s["phase"], "title": s["phase_title"]} for s in STAGES]}
    stages = {}
    for row in STAGES:
        sub, sid = row["sub"], row["stage_id"]
        completed = _stage_done(project, sub) and not _stage_stale(project, sub)
        stale = _stage_stale(project, sub)
        blocked = []
        missing = []
        required_role = SUB_ROLES.get(sub, ("process_engineer",))[0]
        if stale:
            blocked.append("上游已修改，本步结果需重算")
        for prereq in SUB_PREREQUISITES.get(sub, ()):
            if not _stage_done(project, prereq):
                blocked.append(f"前置子步骤未完成：{prereq}")
                missing.append(prereq)
        if not completed and not stale and not (set(roles) & set(SUB_ROLES.get(sub, ()))):
            blocked.append(f"需要 {'/'.join(SUB_ROLES.get(sub, ('process_engineer',)))} 角色")
        stages[sub] = {
            "key": f"{sub} {row['sub_title']}", "sub_step": sub, "stage_id": sid,
            "phase_no": row["phase"], "phase_title": row["phase_title"],
            "sub_title": row["sub_title"], "view": row["view"], "required_role": required_role,
            "completed": completed, "stale": stale, "viewable": True,
            "actionable": (not completed) and (not stale) and not missing
                          and bool(set(roles) & set(SUB_ROLES.get(sub, ("process_engineer",)))),
            "blocked_reasons": blocked, "missing_requirements": missing,
            "status": "confirmed" if completed else ("stale" if stale else "not_started"),
            "primary_action": PRIMARY_ACTIONS.get(sub, ""),
        }
    for row in phases.values():
        subs = [stage for stage in STAGES if stage["phase"] == row["no"]]
        row["completed"] = all(stages[s["sub"]]["completed"] for s in subs)
        row["status"] = "confirmed" if row["completed"] else "in_progress"
    return {"project_id": project["project_id"], "phases": phases, "stages": stages,
            "phases_total": len(phases), "stages_total": len(stages)}


def _acl_mine_sources(sim: Sim, project: dict, username: str) -> list:
    sources = []
    if project.get("owner") == username:
        sources.append("owner")
    holder = project.get("current_holder") or project.get("owner")
    if holder == username:
        sources.append("holder")
    for item in project.get("participants") or []:
        if item.get("username") == username:
            if item.get("assignee") or (holder == username and project.get("owner") != username):
                if "assigned" not in sources:
                    sources.append("assigned")
            if "participant" not in sources:
                sources.append("participant")
    return sources


def _acl_can_read(sim: Sim, project: dict, username: str) -> bool:
    roles = set(sim.actor_roles(username))
    if roles & {"admin", "process_mgr", "reviewer"}:
        return True
    sources = _acl_mine_sources(sim, project, username)
    if roles & {"engineer", "process_engineer", "viewer"}:
        return bool(sources)
    if "sales_mgr" in roles:
        return any(item.get("username") == username and item.get("source") == "quote_owner"
                   for item in project.get("participants") or [])
    if "finance_mgr" in roles:
        return any(item.get("username") == username and item.get("source") == "cost_task_assignee"
                   for item in project.get("participants") or [])
    return bool(sources)


def _acl_can_write(sim: Sim, project: dict, username: str) -> bool:
    if project.get("archived"):
        return False
    roles = set(sim.actor_roles(username))
    if roles & {"admin", "process_mgr"}:
        return True
    if roles & {"engineer", "process_engineer"}:
        return project.get("owner") == username
    return False


def _acl_require(sim: Sim, project_id: str, username: str, mode: str = "read") -> dict:
    if not username:
        raise SimError(401, "missing_token", "缺少登录凭证")
    project = sim.projects.get(project_id)
    if project is None or not _acl_can_read(sim, project, username):
        body = {"code": "project_not_found", "message": "项目不存在"}
        sim.acl["bodies"]["missing"] = dict(body)
        raise SimError(404, "project_not_found", "项目不存在")
    if mode == "write" and not _acl_can_write(sim, project, username):
        sim.acl["bodies"]["forbidden"] = {"code": "forbidden", "message": "无权修改该项目"}
        raise SimError(403, "forbidden", "无权修改该项目")
    return project


def _require_sub_role(sim: Sim, sub: str, actor: str):
    if not set(sim.actor_roles(actor)) & set(SUB_ROLES.get(sub, ())):
        raise SimError(403, "forbidden", f"本步需要角色 {'/'.join(SUB_ROLES.get(sub, ()))}")


def _sub_require(sim: Sim, project_id: str, actor: str, sub: str) -> dict:
    """子步骤门禁：项目**读**权限 + 本步角色。

    与批次 7 spec 一致：项目级 ACL 只管「看不看得到 / 能不能改项目文档」，成本（4.x）、
    报告审核（5.2）这类有自己职责角色的接口在项目级读权限之上叠加本步角色门禁；
    归档项目一律只读。
    """
    project = _acl_require(sim, project_id, actor, "read")
    if project.get("archived"):
        raise SimError(403, "forbidden", "归档项目只读")
    _require_sub_role(sim, sub, actor)
    return project


# ---------------- 环境 / 登录 ----------------
def op_seed_defaults(sim: Sim, action: dict) -> dict:
    sim.ensure_defaults()
    for name, item in (action.get("actors") or {}).items():
        sim._seed_actor(name, item)
    return sim.ok(actors=sorted(sim.actors))


def op_seed_actor(sim: Sim, action: dict) -> dict:
    actor = sim._seed_actor(str(action.get("name")), action)
    return sim.ok(actor=actor["username"], roles=actor["roles"])


def op_login(sim: Sim, action: dict) -> dict:
    actor = sim.actor(str(action.get("actor")))
    sim.session = {"token": actor["token"], "actor": actor["username"]}
    sim.counter["logins"] += 1
    sim.add_event("login", actor=actor["username"])
    return sim.ok(token=actor["token"], actor=actor["username"], roles=actor["roles"])


def op_login_with_token(sim: Sim, action: dict) -> dict:
    token = str(action.get("token") or "")
    if not token:
        return sim.error(401, "missing_token", "缺少 token")
    if token in sim.acl["expired"]:
        return sim.error(401, "token_expired", "token 已过期")
    username = sim.acl["tokens"].get(token)
    if not username:
        return sim.error(401, "invalid_token", "token 无效")
    sim.session = {"token": token, "actor": username}
    sim.counter["logins"] += 1
    return sim.ok(actor=username, roles=sim.actor_roles(username))


def op_set_api_key(sim: Sim, action: dict) -> dict:
    actor = sim.actor(str(action.get("actor")))
    actor["api_key"] = str(action.get("api_key") or "test-key")
    actor["model"] = str(action.get("model") or actor["model"])
    actor["settings"] = dict(action.get("settings") or {})
    if actor["api_key"] != "test-key":
        sim.signal("real_secret_used", True)
    return sim.ok(actor=actor["username"], model=actor["model"])


def op_read_models(sim: Sim, action: dict) -> dict:
    actor = sim.actor(str(action.get("actor")))
    return sim.ok(models=[actor["model"]], api_key=actor["api_key"])


# ---------------- 报价六步 ----------------
def op_create_quote_card(sim: Sim, action: dict) -> dict:
    sid = str(action.get("session_id") or f"qs_{len(sim.cards) + 1:03d}")
    card = _new_card(sim, sid, action)
    sim.focus["card"] = sid
    return sim.ok(session_id=sid, card_id=card["card_id"],
                  business_case_id=card["business_case_id"], current_step=card["current_step"])


def op_quote_parse_requirement(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    actor = sim.require_actor(str(action.get("actor")))
    if not set(actor["roles"]) & {"sales_mgr", "admin"}:
        return sim.error(403, "step_role_forbidden", "本步需要销售经理")
    ref = str(action.get("document") or action.get("fixture") or "")
    data = _fixture(sim, ref)
    idem = str(action.get("idempotency_key") or "")
    if idem and idem in sim.idempotency.setdefault("requirement_parse", {}):
        return sim.ok(code="already_parsed", idempotent=True,
                      filled=sim.idempotency["requirement_parse"][idem])
    if action.get("malformed"):
        sim.signal("no_partial_write", not card["fields"])
        return sim.error(422, "parse_failed", "需求文档解析失败，未写入任何字段")
    fields = data.get("fields") if isinstance(data, dict) else None
    if not fields:
        sim.signal("no_partial_write", True)
        return sim.error(422, "empty_requirement", "文档没有可解析字段")
    attachments = [str(item) for item in (action.get("attachments") or [])]
    if attachments:
        card["attachments"] = attachments
    skipped = []
    for name, value in fields.items():
        if card["fields"].get(name, {}).get("source") == "manual":
            skipped.append(name)
            continue
        card["fields"][name] = {"value": value, "source": "ai_recommended"}
    missing = [name for name, value in fields.items() if value in ("", None)]
    card["left_summary"] = {"filled": len(fields) - len(missing), "missing": missing}
    sim.ui["board_left_right_sync"] = bool(card["fields"]) and not missing
    sim.ui["field_source_badge"] = sorted({item["source"] for item in card["fields"].values()})
    sim.counter["requirement_parsed"] += 1
    if idem:
        sim.idempotency["requirement_parse"][idem] = len(fields) - len(missing)
    sim.add_event("requirement_parsed", card_id=card["card_id"], fields=sorted(fields))
    return sim.ok(code="parsed", filled=len(fields) - len(missing), missing=missing,
                  attachments=attachments, skipped_manual=skipped,
                  sources=sim.ui["field_source_badge"], board_synced=sim.ui["board_left_right_sync"])


def op_quote_edit_field(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    actor = str(action.get("actor") or "")
    if actor:
        roles = set(sim.actor_roles(actor))
        if not (roles & {"sales_mgr", "admin"} or actor == card["owner"]):
            return sim.error(403, "step_role_forbidden", "只有销售经理/卡片归属人可以改字段")
    name = str(action.get("field"))
    card["fields"][name] = {"value": action.get("value"),
                            "source": str(action.get("source") or "manual")}
    sim.ui["field_source_badge"] = sorted({item["source"] for item in card["fields"].values()})
    sim.add_event("field_edited", card_id=card["card_id"], field=name,
                  source=card["fields"][name]["source"])
    return sim.ok(field=name, source=card["fields"][name]["source"])


def op_quote_match_product(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    params = list(action.get("params") or [])
    weights = sum(float(item.get("weight") or 1) for item in params) or 1.0
    matched = sum(float(item.get("weight") or 1) for item in params if item.get("matched"))
    score = Decimal(str(round(100 * matched / weights, 1)))
    threshold = Decimal(str(action.get("threshold") or 70))
    card["product_match"] = {"score": f"{score:.1f}", "matched": score >= threshold}
    sim.add_event("product_matched", card_id=card["card_id"], score=f"{score:.1f}")
    if score < threshold:
        card["products"] = []
        return sim.ok(code="no_std_product", score=f"{score:.1f}", matched=False,
                      next_action="send_tech_new_product")
    product = dict(action.get("product") or {"code": "MD-STD-0001", "name": "标准产品"})
    product["match_score"] = f"{score:.1f}"
    card["products"] = [product]
    return sim.ok(code="matched", score=f"{score:.1f}", matched=True, product=product)


def op_quote_complete_step(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    step = int(action.get("step") or 0)
    actor = sim.require_actor(str(action.get("actor")))
    snapshot = dict(action.get("snapshot") or {})
    if step < 1 or step > len(QUOTE_STEPS):
        raise SimError(422, "bad_step", "步骤号非法")
    required_role = QUOTE_ROLE_BY_STEP.get(step, "")
    if required_role not in actor["roles"]:
        return sim.error(403, "step_role_forbidden", f"第 {step} 步需要 {required_role}")
    if step > card["current_step"]:
        return sim.error(409, "step_not_ready", f"第 {step} 步还没轮到（当前 {card['current_step']}）")
    if step == 1 and not card["fields"]:
        return sim.error(409, "required_fields_missing", "第 1 步还没有任何需求字段，不能完成")

    def _do():
        entry = card["steps"].setdefault(str(step), {"status": "", "snapshot": {}, "stale": False})
        idem = str(action.get("idempotency_key") or f"{card['session_id']}:{step}")
        if idem in card["idempotency"]:
            return sim.ok(code="already_completed", step=step, idempotent=True,
                          version=card["idempotency"][idem])
        delta = _merge_snapshot(entry["snapshot"], snapshot)
        if snapshot:
            _apply_step_snapshot(sim, card, step, snapshot)
        entry.update({"status": "done", "completed_by": actor["username"], "at": sim.now(),
                      "stale": False})
        if not delta and step < card["current_step"]:
            card["idempotency"][idem] = card["versions"][-1]["version"] if card["versions"] else ""
            return sim.ok(code="no_change", step=step)
        if step == card["current_step"]:
            card["current_step"] = min(step + 1, len(QUOTE_STEPS))
            card["current_owner"] = _next_role(card)
            card["overall_status"] = "awaiting_handoff" if card["current_step"] <= len(QUOTE_STEPS) \
                else "completed"
        version = f"v{len(card['versions']) + 1}"
        card["versions"].append({"version": version, "step": step, "at": sim.now(),
                                 "snapshot": copy.deepcopy(snapshot)})
        card["idempotency"][idem] = version
        sim.counter["quote_steps_completed"] += 1
        sim.add_event("step_completed", card_id=card["card_id"], step=step,
                      actor=actor["username"], version=version)
        sim.add_message(card["owner"], "step_completed", f"第 {step} 步已完成",
                        card_id=card["card_id"], step=step)
        return sim.ok(code="completed", step=step, version=version,
                      current_step=card["current_step"])

    return sim.atomic(_do)


def _next_role(card: dict) -> str:
    step = card["current_step"]
    if step > len(QUOTE_STEPS):
        return card["current_owner"]
    return QUOTE_ROLE_BY_STEP.get(step, card["current_owner"])


def _apply_step_snapshot(sim: Sim, card: dict, step: int, snapshot: dict):
    totals = card["totals"]
    if step == 1:
        for key in ("base_cost", "customer", "project_name"):
            if key in snapshot:
                card["fields"][key] = {"value": snapshot[key], "source": "ai_recommended"}
    if step == 3:
        for key in ("base_cost", "tech_premium", "market_adjust"):
            if key in snapshot:
                totals[key] = money(snapshot[key])
        _recompute_totals(card)
    if step == 4:
        charges = snapshot.get("charges") or []
        names = [str(item.get("name")) for item in charges]
        if len(names) != len(set(names)):
            raise SimError(409, "duplicate_charge", "加价项重复")
        for item in charges:
            if _dec(item.get("amount")) < 0:
                raise SimError(422, "negative_charge", "加价项不允许负值")
        totals["charges"] = [{"name": str(item.get("name")), "amount": money(item.get("amount"))}
                             for item in charges]
        _recompute_totals(card)
    if step == 5:
        if "discount" in snapshot:
            totals["discount"] = money(snapshot["discount"])
        if "selected_plan" in snapshot:
            card["selected_plan"] = str(snapshot["selected_plan"])
        _recompute_totals(card)
    if step == 6:
        if "document" in snapshot:
            card["documents"].append({"document": str(snapshot["document"]), "at": sim.now()})


def op_quote_edit_step(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    step = int(action.get("step") or 0)
    actor = sim.require_actor(str(action.get("actor")))
    if required_role := QUOTE_ROLE_BY_STEP.get(step, ""):
        if required_role not in actor["roles"]:
            return sim.error(403, "step_role_forbidden", f"第 {step} 步需要 {required_role}")
    entry = card["steps"].get(str(step))
    if not entry or entry.get("status") != "done":
        return sim.error(409, "step_not_completed", "还没完成这一步，不能回改")
    snapshot = dict(action.get("snapshot") or {})

    def _do():
        idem = str(action.get("idempotency_key") or f"{card['session_id']}:{step}:edit")
        if idem in card["idempotency"]:
            return sim.ok(code="already_applied", version=card["idempotency"][idem])
        delta = _merge_snapshot(entry["snapshot"], snapshot)
        if not delta:
            card["idempotency"][idem] = card["versions"][-1]["version"] if card["versions"] else ""
            return sim.ok(code="no_change", version=card["idempotency"][idem])
        _apply_step_snapshot(sim, card, step, snapshot)
        entry["snapshot"].update(copy.deepcopy(snapshot))
        entry["stale"] = False
        current = card["current_step"]
        for later in range(step + 1, len(QUOTE_STEPS) + 1):
            if str(later) in card["steps"]:
                card["steps"][str(later)]["stale"] = True
        card["current_step"] = max(current, step)
        version = f"v{len(card['versions']) + 1}"
        card["versions"].append({"version": version, "step": step, "at": sim.now(),
                                 "snapshot": copy.deepcopy(snapshot)})
        card["idempotency"][idem] = version
        sim.counter["quote_recomputes"] += 1
        sim.signal("stale_downstream", True)
        sim.add_event("step_recomputed", card_id=card["card_id"], step=step, version=version)
        return sim.ok(code="recomputed", version=version, current_step=card["current_step"],
                      stale_steps=[n for n in range(step + 1, len(QUOTE_STEPS) + 1)
                                   if str(n) in card["steps"]])

    return sim.atomic(_do)


def op_quote_generate_plans(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    if card["current_step"] < 5:
        return sim.error(409, "step_not_ready", "第 4 步还没完成，不能生成方案")
    _recompute_totals(card)
    plans = []
    for item in action.get("plans") or []:
        discount = _dec(item.get("discount"))
        expected = _dec(card["totals"].get("expected_price"))
        plans.append({"plan_id": str(item.get("plan_id")), "name": str(item.get("name") or ""),
                      "discount": money(discount),
                      "quote_price": money(expected * (Decimal("1") - discount))})
    card["plans"] = plans
    sim.counter["plans"] += len(plans)
    sim.add_event("plans_generated", card_id=card["card_id"], count=len(plans))
    return sim.ok(plans=plans, count=len(plans))


def op_quote_select_plan(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    plan_id = str(action.get("plan_id"))
    plan = next((item for item in card["plans"] if item["plan_id"] == plan_id), None)
    if plan is None:
        return sim.error(404, "plan_not_found", "方案不存在")
    if plan_id in card["idempotency"].get("selected_plan", []):
        return sim.ok(code="already_selected", plan_id=plan_id)
    card.setdefault("idempotency", {}).setdefault("selected_plan", []).append(plan_id)
    card["selected_plan"] = plan_id
    card["totals"]["discount"] = plan["discount"]
    _recompute_totals(card)
    sim.add_event("plan_selected", card_id=card["card_id"], plan_id=plan_id)
    return sim.ok(plan_id=plan_id, quote_price=card["totals"]["quote_price"])


def op_quote_output_document(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    actor = sim.require_actor(str(action.get("actor")))
    if card["current_step"] < 6:
        return sim.error(409, "step_not_ready", "报价方案未确认")
    if not card["selected_plan"]:
        return sim.error(409, "plan_not_selected", "还没有选中报价方案")
    digest = str(action.get("content_hash") or _join(card["selected_plan"], card["totals"].get("quote_price")))
    seen = card["idempotency"].setdefault("documents", [])
    if digest in seen:
        return sim.ok(code="already_output", version=card["documents"][-1]["version"] if card["documents"] else "")
    seen.append(digest)
    version = f"V{len(card['documents']) + 1}"
    card["documents"].append({"version": version, "document": str(action.get("document") or "报价单.docx"),
                              "at": sim.now(), "by": actor["username"]})
    if str(6) in card["steps"] or card["current_step"] == 6:
        card["steps"].setdefault("6", {"snapshot": {}, "stale": False}).update(
            {"status": "done", "completed_by": actor["username"], "at": sim.now()})
        card["current_step"] = len(QUOTE_STEPS)
        card["overall_status"] = "completed"
    sim.counter["documents"] += 1
    sim.add_event("document_output", card_id=card["card_id"], version=version)
    return sim.ok(code="output", version=version, download=True)


def op_quote_handoff_step(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    actor = sim.require_actor(str(action.get("actor")))
    if str(1) not in card["steps"]:
        return sim.error(409, "step_not_ready", "第 1 步还没完成")
    result = _task_send_internal(sim, card, "handoff", "role",
                                 str(action.get("target_role_code") or "process_mgr"), "",
                                 str(action.get("note") or ""),
                                 {"initiator": actor["username"],
                                  "business_case_id": card["business_case_id"],
                                  "project_id": card["project_id"]},
                                 actor["username"], str(action.get("idempotency_key") or ""))
    return sim.ok(code=result["code"], task_id=result["task"]["task_id"],
                  reused=result["reused"], current_step=card["current_step"])


def op_quote_send_tech_task(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    actor = sim.require_actor(str(action.get("actor")))
    if card["product_match"].get("matched"):
        return sim.error(409, "std_product_matched", "已有匹配标品，不需要新增工艺")
    result = _task_send_internal(sim, card, "tech_new_product", "role", "process_mgr", "",
                                 str(action.get("note") or ""),
                                 {"initiator": actor["username"],
                                  "business_case_id": card["business_case_id"],
                                  "quote_session_id": card["session_id"],
                                  "project_id": card["project_id"],
                                  "match_score": card["product_match"].get("score", "")},
                                 actor["username"], str(action.get("idempotency_key") or ""))
    return sim.ok(code=result["code"], task_id=result["task"]["task_id"], reused=result["reused"],
                  current_step=card["current_step"])


def op_quote_snapshot(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id")))
    return sim.ok(current_step=card["current_step"], overall_status=card["overall_status"],
                  totals=card["totals"], business_case_id=card["business_case_id"],
                  versions=len(card["versions"]))


# ---------------- 技术工艺 5 阶段 × 13 子步骤 ----------------
def op_tech_create_project(sim: Sim, action: dict) -> dict:
    pid = str(action.get("project_id") or f"tp_{sim._project_seq + 1:03d}")
    actor = sim.require_actor(str(action.get("actor")))
    item = dict(action)
    item.setdefault("owner", actor["username"])
    item.setdefault("current_holder", actor["username"])
    business_case = dict(action.get("business_case") or {})
    if action.get("from_task"):
        task = _task(sim, str(action.get("from_task")))
        payload = task.get("payload") or {}
        business_case.update({
            "business_case_id": payload.get("business_case_id", ""),
            "quote_session_id": payload.get("quote_session_id", task.get("session_id", "")),
            "source_task_id": task["task_id"], "linked_by": "task",
        })
        item["business_case"] = business_case
        item.setdefault("page_context", stage_title("requirement-create"))
    project = _new_project(sim, pid, item)
    sim.counter["tech_projects"] += 1
    return sim.ok(project_id=pid, page_context=project["page_context"],
                  business_case=project["business_case"])


def op_tech_open_project(sim: Sim, action: dict) -> dict:
    pid = str(action.get("project_id"))
    actor = str(action.get("actor"))
    project = _acl_require(sim, pid, actor, "read")
    entry = str(action.get("entry") or "home_card")
    sim.counter[f"open_{entry}"] += 1
    sim.focus["project"] = pid
    sim.focus["stage"] = project["page_context"] or stage_title("requirement-create")
    return sim.ok(project_id=pid, entry=entry, page_context=sim.focus["stage"],
                  timeline_events=len(project["timeline"]))


def op_tech_requirement_create(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    _require_sub_role(sim, "1.1", str(action.get("actor")))
    idem = str(action.get("idempotency_key") or "")
    if idem and idem in sim.idempotency.setdefault("requirement_create", {}):
        return sim.ok(code="already_submitted", requirement=project["requirement"])
    requirement = project["requirement"]
    requirement.update(dict(action.get("form") or {}))
    requirement["status"] = "pending_review" if action.get("submit") else "draft"
    requirement["created_by"] = str(action.get("actor"))
    requirement["created_at"] = sim.now()
    if idem:
        sim.idempotency["requirement_create"][idem] = requirement["status"]
    sim.add_event("requirement_created", project_id=project["project_id"],
                  status=requirement["status"])
    sim.add_message(project["owner"], "requirement_created", "需求单已保存",
                    project_id=project["project_id"])
    return sim.ok(code="submitted" if action.get("submit") else "draft",
                  status=requirement["status"])


def op_tech_requirement_confirm(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    _require_sub_role(sim, "1.2", str(action.get("actor")))
    requirement = project["requirement"]
    if requirement.get("status") not in ("pending_review", "approved"):
        return sim.error(409, "missing_prerequisite", "需求单还没提交确认")
    gaps = list(action.get("gaps") or requirement.get("gaps") or [])
    waiver = bool(action.get("waiver"))
    requirement["completeness_checked"] = True
    requirement["gaps"] = gaps
    if gaps and not waiver:
        requirement["confirmed"] = False
        return sim.error(409, "gaps_not_waived", f"还有缺口未处理或豁免：{gaps}")
    requirement["confirmed"] = True
    requirement["waiver"] = waiver
    requirement["confirmed_by"] = str(action.get("actor"))
    sim.add_event("requirement_confirmed", project_id=project["project_id"], waiver=waiver, gaps=gaps)
    return sim.ok(code="confirmed", gaps=gaps, waiver=waiver)


def op_tech_requirement_review(sim: Sim, action: dict) -> dict:
    project = _sub_require(sim, str(action.get("project_id")), str(action.get("actor")), "1.3")
    requirement = project["requirement"]
    if not requirement.get("confirmed"):
        return sim.error(409, "missing_prerequisite", "需求还没确认，不能审核")
    decision = str(action.get("decision") or "approve")
    requirement["review_note"] = str(action.get("note") or "")
    if decision == "approve":
        requirement["review"] = "approved"
        requirement["status"] = "approved"
        sim.add_event("requirement_reviewed", project_id=project["project_id"], decision="approve")
        return sim.ok(code="approved")
    requirement["review"] = "rejected"
    requirement["status"] = "rejected"
    sim.add_event("requirement_reviewed", project_id=project["project_id"], decision="reject")
    sim.add_message(project["owner"], "requirement_rejected", "需求被退回",
                    project_id=project["project_id"], note=requirement["review_note"])
    return sim.ok(code="rejected", note=requirement["review_note"])


def op_tech_drawing_parse(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    _require_sub_role(sim, "2.1", str(action.get("actor")))
    if project["requirement"].get("review") != "approved":
        return sim.error(409, "missing_prerequisite", "1.3 审核还没通过")
    data = None
    if action.get("fixture"):
        data = _fixture(sim, str(action["fixture"]))
    parts = int(action.get("parts") if action.get("parts") is not None
                else len((data or {}).get("parts") or []))
    drawing = project["drawing"]
    drawing.update({"parsed": True, "ir": True, "parts": parts, "parsed_at": sim.now()})
    sim.counter["drawing_parses"] += 1
    sim.add_event("drawing_parsed", project_id=project["project_id"], parts=parts)
    if parts == 0:
        return sim.ok(code="empty_result", parts=0, needs_manual_confirm=True)
    return sim.ok(code="parsed", parts=parts, needs_manual_confirm=False)


def op_tech_drawing_confirm_empty(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    drawing = project["drawing"]
    if int(drawing.get("parts") or 0) != 0:
        return sim.error(409, "not_empty_result", "解析结果不是 0 个零件")
    drawing["empty_confirmed"] = True
    drawing["empty_confirmed_by"] = str(action.get("actor"))
    sim.add_event("drawing_empty_confirmed", project_id=project["project_id"])
    return sim.ok(code="confirmed_empty", parts=0)


def op_tech_part_open(sim: Sim, action: dict) -> dict:
    container = str(action.get("container") or "board")
    if container != "board":
        return sim.error(422, "container_not_supported", "零件详情必须在看板内部展开")
    sim.ui["part_detail_in_board"] = True
    sim.add_event("part_opened", part_id=str(action.get("part_id")), container=container)
    return sim.ok(container=container, view=str(action.get("view") or "3d"))


def op_tech_integration_run(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    _require_sub_role(sim, "3.1", str(action.get("actor")))
    if not _stage_done(project, "2.1"):
        return sim.error(409, "missing_prerequisite", "2.1 图纸解析还没完成")
    project["integration"]["drawings_done"] = True
    project["integration"]["drawings_at"] = sim.now()
    project["stale"]["integration"] = False
    sim.add_event("integration_done", project_id=project["project_id"])
    return sim.ok(code="integrated")


def op_tech_params_recommend(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    stage = str(action.get("stage") or "process")
    if stage != "process":
        return sim.error(409, "params_not_in_cost", "参数推荐属于「3 组装与整合」")
    if not project["integration"].get("drawings_done"):
        return sim.error(409, "missing_prerequisite", "3.1 整合图纸还没完成")
    items = []
    for item in action.get("items") or []:
        items.append({"name": str(item.get("name")), "value": item.get("value"),
                      "required": bool(item.get("required")), "source": "ai_recommended"})
    project["integration"]["params"] = items
    sim.ui["params_tab_phase"] = 3
    sim.counter["params_recommend"] += 1
    sim.add_event("params_recommended", project_id=project["project_id"], count=len(items))
    gaps = [item["name"] for item in items if item["required"] and item["value"] in (None, "")]
    return sim.ok(code="recommended", count=len(items), gaps=gaps)


def op_tech_params_confirm(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    _require_sub_role(sim, "3.2", str(action.get("actor")))
    items = project["integration"]["params"]
    if not items:
        return sim.error(409, "missing_prerequisite", "还没有参数推荐结果")
    gaps = [item["name"] for item in items if item["required"] and item["value"] in (None, "")]
    waiver = bool(action.get("waiver"))
    if gaps and not waiver:
        project["integration"]["params_confirmed"] = False
        return sim.error(409, "params_gap", f"必填参数缺失：{gaps}")
    project["integration"]["params_confirmed"] = True
    project["integration"]["params_waiver"] = waiver
    sim.add_event("params_confirmed", project_id=project["project_id"], waiver=waiver, gaps=gaps)
    return sim.ok(code="confirmed", gaps=gaps, waiver=waiver)


def op_tech_process_assemble(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    _require_sub_role(sim, "3.3", str(action.get("actor")))
    if not project["integration"].get("params_confirmed"):
        return sim.error(409, "missing_prerequisite", "3.2 参数还没确认")
    steps = [dict(item) for item in (action.get("steps") or [])]
    project["integration"]["process_steps"] = steps
    sim.add_event("process_generated", project_id=project["project_id"], count=len(steps))
    return sim.ok(code="generated", count=len(steps))


def op_tech_process_confirm(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    _require_sub_role(sim, "3.3", str(action.get("actor")))
    steps = project["integration"]["process_steps"]
    if not steps:
        return sim.error(409, "no_process", "还没有生成工序")
    project["integration"]["process_confirmed"] = True
    project["integration"]["process_confirmed_by"] = str(action.get("actor"))
    sim.add_event("process_confirmed", project_id=project["project_id"])
    return sim.ok(code="confirmed", count=len(steps))


def op_tech_cost_parts(sim: Sim, action: dict) -> dict:
    project = _sub_require(sim, str(action.get("project_id")), str(action.get("actor")), "4.1")
    if not _stage_done(project, "3.3"):
        return sim.error(409, "missing_prerequisite", "3.3 组装工艺还没确认")
    excluded = [str(item) for item in (action.get("excluded_parts") or [])]
    included, excluded_rows = [], []
    for item in action.get("parts") or []:
        row = {"part_id": str(item.get("part_id")), "amount": money(item.get("amount")),
               "excluded": bool(item.get("excluded"))}
        if row["excluded"]:
            if row["part_id"] not in excluded:
                return sim.error(409, "excluded_part_not_declared",
                                 f"排除零件必须在 excluded_parts 里声明：{row['part_id']}")
            excluded_rows.append(row)
        elif row["part_id"] in excluded:
            excluded_rows.append({**row, "excluded": True})
        else:
            included.append(row)
    project["cost"]["parts"] = included
    project["cost"]["excluded_parts"] = excluded_rows
    sim.counter["cost_parts"] += len(included)
    sim.add_event("cost_parts", project_id=project["project_id"], included=len(included),
                  excluded=len(excluded_rows))
    total = sum((_dec(row["amount"]) for row in included), Decimal("0"))
    return sim.ok(code="costed", included=len(included), excluded=len(excluded_rows),
                  parts_total=money(total))


def op_tech_cost_assembly(sim: Sim, action: dict) -> dict:
    project = _sub_require(sim, str(action.get("project_id")), str(action.get("actor")), "4.2")
    if not project["cost"]["parts"]:
        return sim.error(409, "missing_prerequisite", "4.1 零件成本还没算")
    assembly = dict(action.get("assembly") or {})
    assembly["amount"] = money(assembly.get("amount"))
    project["cost"]["assembly"] = assembly
    sim.add_event("cost_assembly", project_id=project["project_id"], amount=assembly["amount"])
    return sim.ok(code="costed", assembly=assembly)


def op_tech_cost_total(sim: Sim, action: dict) -> dict:
    project = _sub_require(sim, str(action.get("project_id")), str(action.get("actor")), "4.3")
    cost = project["cost"]
    if not cost["parts"] or not cost["assembly"]:
        return sim.error(409, "missing_prerequisite", "零件成本或组装成本还没算完")
    parts_total = sum((_dec(row["amount"]) for row in cost["parts"]), Decimal("0"))
    assembly_total = _dec((cost["assembly"] or {}).get("amount"))
    cost["total"] = {"amount": money(parts_total + assembly_total),
                     "parts_total": money(parts_total),
                     "assembly_total": money(assembly_total),
                     "excluded_parts": [row["part_id"] for row in cost["excluded_parts"]]}
    sim.add_event("cost_total", project_id=project["project_id"], amount=cost["total"]["amount"])
    return sim.ok(code="totaled", total=cost["total"])


def op_tech_cost_confirm(sim: Sim, action: dict) -> dict:
    project = _sub_require(sim, str(action.get("project_id")), str(action.get("actor")), "4.3")
    if not project["cost"].get("total"):
        return sim.error(409, "missing_prerequisite", "成本还没汇总，不能确认")
    project["cost"]["confirmed"] = True
    project["cost"]["confirmed_by"] = str(action.get("actor"))
    project["cost"]["confirmed_at"] = sim.now()
    sim.add_event("cost_confirmed", project_id=project["project_id"],
                  actor=str(action.get("actor")))
    return sim.ok(code="confirmed", confirmed_by=project["cost"]["confirmed_by"])


def op_tech_summary_save(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    _require_sub_role(sim, "5.1", str(action.get("actor")))
    if not project["cost"].get("confirmed"):
        return sim.error(409, "missing_prerequisite", "4.3 成本还没正式确认")
    project["report"]["draft"] = dict(action.get("draft") or {})
    project["report"]["status"] = "generated"
    project["report"]["draft_at"] = sim.now()
    sim.add_event("report_draft", project_id=project["project_id"])
    return sim.ok(code="drafted", status="generated")


def op_tech_report_review(sim: Sim, action: dict) -> dict:
    project = _sub_require(sim, str(action.get("project_id")), str(action.get("actor")), "5.2")
    report = project["report"]
    if not report.get("status"):
        return sim.error(409, "missing_prerequisite", "还没有报告草稿")
    decision = str(action.get("decision") or "approve")
    if decision == "approve":
        report["status"] = "approved"
        report["review_note"] = str(action.get("note") or "")
        sim.add_event("report_reviewed", project_id=project["project_id"], decision="approve")
        return sim.ok(code="approved")
    report["status"] = "in_review"
    report["review_note"] = str(action.get("note") or "")
    sim.add_message(project["owner"], "report_rejected", "报告被退回",
                    project_id=project["project_id"], note=report["review_note"])
    sim.add_event("report_reviewed", project_id=project["project_id"], decision="reject")
    return sim.ok(code="rejected", note=report["review_note"])


def op_tech_report_publish(sim: Sim, action: dict) -> dict:
    project = _sub_require(sim, str(action.get("project_id")), str(action.get("actor")), "5.3")
    report = project["report"]
    if report.get("status") != "approved":
        return sim.error(409, "missing_prerequisite", "报告还没审核通过")
    version = f"R{len(report['versions']) + 1}"
    report["published"] = True
    report["published_at"] = sim.now()
    report["versions"].append({"version": version, "at": sim.now(), "by": str(action.get("actor"))})
    sim.signal("published_counted_complete", False)
    sim.add_event("report_published", project_id=project["project_id"], version=version)
    return sim.ok(code="published", version=version, handed_back=bool(report.get("handed_back")))


def op_tech_report_handback(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    report = project["report"]
    if not report.get("published"):
        return sim.error(409, "missing_prerequisite", "报告还没发布")
    version = report["versions"][-1]["version"] if report["versions"] else "R1"
    payload = {
        "source_project_id": project["project_id"],
        "source_task_id": str(action.get("source_task_id")
                              or (project["business_case"] or {}).get("source_task_id") or ""),
        "source_result_version": str(action.get("source_result_version") or version),
        "handoff_kind": "report",
        "business_case_id": str(action.get("business_case_id")
                                or (project["business_case"] or {}).get("business_case_id") or ""),
        "target_quote_session_id": str(action.get("target_quote_session_id")
                                       or (project["business_case"] or {}).get("quote_session_id")
                                       or ""),
        "snapshot": dict(action.get("snapshot") or {
            "report": {"status": report["status"], "version": version}}),
        "actor": str(action.get("actor")),
        "create_new": bool(action.get("create_new")),
        "create_reason": str(action.get("create_reason") or ""),
        "fail_at": str(action.get("fail_at") or ""),
    }
    resp = op_handoff_send(sim, payload)
    if resp.get("ok"):
        report["handed_back"] = True
        report["handed_back_at"] = sim.now()
        resp["handed_back"] = True
    return resp


def op_tech_edit_upstream(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    field = str(action.get("field") or "params")
    if field in ("drawing", "params"):
        project["stale"]["integration"] = True
    if field in ("drawing", "params", "process", "assembly", "parts"):
        project["stale"]["cost"] = True
    project["stale"]["report"] = True
    sim.signal("stale_upstream", True)
    sim.add_event("upstream_edited", project_id=project["project_id"], field=field)
    return sim.ok(code="edited", stale=dict(project["stale"]))


def op_tech_refresh(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "read")
    if action.get("fail"):
        sim.ui["refresh_failure_keeps_state"] = True
        sim.signal("refresh_kept_state", True)
        sim.signal("refresh_cleared_state", False)
        return sim.error(503, "refresh_failed", "刷新失败，以下为上次状态")
    if action.get("attempt_clear"):
        sim.signal("blocked_clear", True)
    projection = _projection(sim, project, str(action.get("actor")))
    sim.projection = projection
    sim.signal("refresh_kept_state", True)
    return sim.ok(code="refreshed", stages_total=projection["stages_total"],
                  phases_total=projection["phases_total"], **_substep_lists(projection))


def _substep_lists(projection: dict) -> dict:
    """按子步骤号给出完成 / stale / 可做清单，供案例断言（子步骤号带点，不能直接当 state 路径）。"""
    stages = projection.get("stages") or {}
    return {
        "completed_substeps": sorted(sub for sub, row in stages.items() if row["completed"]),
        "stale_substeps": sorted(sub for sub, row in stages.items() if row["stale"]),
        "actionable_substeps": sorted(sub for sub, row in stages.items() if row["actionable"]),
    }


def op_tech_projection(sim: Sim, action: dict) -> dict:
    pid = str(action.get("project_id"))
    actor = str(action.get("actor"))
    project = sim.projects.get(pid)
    if project is None or not _acl_can_read(sim, project, actor):
        return sim.error(404, "project_not_found", "项目不存在")
    projection = _projection(sim, project, actor)
    sim.projection = projection
    sim.counter["projections"] += 1
    next_action = next((sub for sub in sorted(projection["stages"])
                        if projection["stages"][sub]["actionable"]), "")
    return sim.ok(phases=projection["phases_total"], stages=projection["stages_total"],
                  next_action=next_action, refresh_ok=True,
                  **_substep_lists(projection))


def op_tech_timeline_read(sim: Sim, action: dict) -> dict:
    pid = str(action.get("project_id"))
    actor = str(action.get("actor"))
    project = _acl_require(sim, pid, actor, "read")
    return sim.ok(events=project["timeline"], count=len(project["timeline"]))


# ---------------- ACL ----------------
def op_acl_list(sim: Sim, action: dict) -> dict:
    actor = str(action.get("actor"))
    scope = str(action.get("scope") or "mine")
    mode = str(action.get("mode") or "read")
    sim.require_actor(actor)
    visible = []
    for pid, project in sorted(sim.projects.items()):
        mine = _acl_mine_sources(sim, project, actor)
        readable = _acl_can_read(sim, project, actor)
        keep = False
        if scope == "archived":
            keep = readable and bool(project.get("archived"))
        elif scope == "all":
            keep = readable
        else:
            keep = bool(mine) and readable
        if keep and mode == "write" and not _acl_can_write(sim, project, actor):
            keep = False
        if keep:
            visible.append(pid)
    sim.counter[f"acl_list_{scope}"] += 1
    return sim.ok(scope=scope, projects=visible, count=len(visible))


def op_acl_access(sim: Sim, action: dict) -> dict:
    pid = str(action.get("project_id"))
    actor = str(action.get("actor"))
    mode = str(action.get("mode") or "read")
    project = _acl_require(sim, pid, actor, mode)
    sim.counter["acl_access"] += 1
    return sim.ok(project_id=pid, archived=bool(project.get("archived")),
                  access={"scope": "mine" if _acl_mine_sources(sim, project, actor) else "all",
                          "mine_sources": _acl_mine_sources(sim, project, actor),
                          "can_read": True, "can_write": _acl_can_write(sim, project, actor)})


def op_acl_matrix_check(sim: Sim, action: dict) -> dict:
    pid = str(action.get("project_id"))
    mode = str(action.get("mode") or "read")
    matrix = dict(action.get("matrix") or {})
    project = sim.projects.get(pid)
    results, mismatch, denied_allowed = {}, [], False
    for name, expect in matrix.items():
        allowed = False
        if project is not None:
            allowed = _acl_can_read(sim, project, name) if mode == "read" \
                else _acl_can_write(sim, project, name)
        results[name] = "allow" if allowed else "deny"
        if str(expect) != results[name]:
            mismatch.append(name)
            if str(expect) == "deny" and allowed:
                denied_allowed = True
    sim.signal("denied_actor_allowed", denied_allowed)
    sim.acl["bodies"].setdefault("missing", {"code": "project_not_found", "message": "项目不存在"})
    sim.acl["bodies"].setdefault("forbidden", {"code": "project_not_found", "message": "项目不存在"})
    code = "matrix_ok" if not mismatch else "matrix_mismatch"
    return sim.ok(code=code, results=results, mismatch=mismatch,
                  bodies_equal=sim.acl["bodies"].get("missing") == sim.acl["bodies"].get("forbidden"))


# ---------------- 会话 / 历史 ----------------
def op_load_fixture_state(sim: Sim, action: dict) -> dict:
    data = _fixture(sim, str(action.get("fixture")))
    created = {"cards": [], "projects": [], "tasks": []}
    card_item = data.get("card")
    if card_item:
        card = _new_card(sim, str(card_item.get("session_id")), card_item)
        created["cards"].append(card["session_id"])
    project_item = data.get("project")
    if project_item:
        project = _new_project(sim, str(project_item.get("project_id")), project_item)
        project["timeline"] = list(project_item.get("timeline") or [])
        created["projects"].append(project["project_id"])
    for task_item in data.get("tasks") or []:
        task = dict(task_item)
        task.setdefault("task_id", f"t_fixture{len(sim.tasks) + 1}")
        task.setdefault("task_no", f"TP-F{len(sim.tasks) + 1}")
        task.setdefault("payload", {})
        sim.tasks[task["task_id"]] = task
        created["tasks"].append(task["task_id"])
    sim.counter["fixture_loads"] += 1
    sim.add_event("fixture_loaded", fixture=str(action.get("fixture")))
    return sim.ok(loaded=created)


def op_service_restart_recover(sim: Sim, action: dict) -> dict:
    if action.get("fixture"):
        op_load_fixture_state(sim, action)
    before = {"cards": len(sim.cards), "projects": len(sim.projects), "tasks": len(sim.tasks)}
    sim.counter["restarts"] += 1
    resumed = []
    for task in sim.tasks.values():
        if task.get("status") == "interrupted":
            task["status"] = "claimed"
            task["resumed_at"] = sim.now()
            resumed.append(task["task_id"])
            sim.add_event("task_resumed", task_id=task["task_id"])
    after = {"cards": len(sim.cards), "projects": len(sim.projects), "tasks": len(sim.tasks)}
    sim.signal("recovered_without_duplicates", before == after)
    return sim.ok(code="recovered", resumed=resumed, before=before, after=after)


def op_session_append_event(sim: Sim, action: dict) -> dict:
    project = _acl_require(sim, str(action.get("project_id")), str(action.get("actor")), "write")
    dedup = str(action.get("dedup_key") or "")
    if dedup:
        existing = [event for event in project["timeline"] if event.get("dedup_key") == dedup]
        if existing:
            return sim.ok(code="already_appended", seq=existing[0]["seq"], count=len(project["timeline"]))
    item = {"seq": len(project["timeline"]) + 1, "kind": str(action.get("kind") or "message"),
            "role": str(action.get("role") or "user"), "text": str(action.get("text") or ""),
            "payload": dict(action.get("payload") or {}), "at": sim.now(),
            "page_context": str(action.get("page_context") or project["page_context"] or "")}
    if dedup:
        item["dedup_key"] = dedup
    project["timeline"].append(item)
    sim.counter["session_events"] += 1
    return sim.ok(code="appended", seq=item["seq"], count=len(project["timeline"]))


def op_session_restore(sim: Sim, action: dict) -> dict:
    pid = str(action.get("project_id"))
    actor = str(action.get("actor"))
    project = _acl_require(sim, pid, actor, "read")
    entry = str(action.get("entry") or "history_drawer")
    messages = [event for event in project["timeline"] if event["kind"] == "message"]
    tool_trace = [event for event in project["timeline"] if event["kind"] == "tool"]
    board = [event for event in project["timeline"] if event["kind"] == "board"]
    ordered = sorted(project["timeline"], key=lambda event: event["seq"])
    sim.restore = {"entry": entry, "project_id": pid, "messages": messages,
                   "tool_trace": tool_trace, "board": board,
                   "order": [event["seq"] for event in ordered],
                   "page_context": project["page_context"],
                   "stage": project["page_context"] or stage_title("requirement-create")}
    sim.counter[f"restore_{entry}"] += 1
    sim.add_event("session_restored", project_id=pid, entry=entry)
    return sim.ok(code="restored", entry=entry, messages=len(messages),
                  tool_trace=len(tool_trace), board=len(board), stage=sim.restore["stage"],
                  order=sim.restore["order"])


def op_session_rebind_project(sim: Sim, action: dict) -> dict:
    pid = str(action.get("project_id"))
    other = str(action.get("other_project_id"))
    actor = str(action.get("actor"))
    project = _acl_require(sim, pid, actor, "read")
    other_project = _acl_require(sim, other, actor, "read")
    a_texts = {event.get("text") for event in project["timeline"] if event.get("text")}
    b_texts = {event.get("text") for event in other_project["timeline"] if event.get("text")}
    leak = bool(a_texts & b_texts)
    sim.signal("cross_project_leak", leak)
    sim.add_event("session_rebound", project_id=pid, other_project_id=other)
    return sim.ok(code="rebound", isolated=not leak,
                  a_events=len(project["timeline"]), b_events=len(other_project["timeline"]))


def op_page_context_open(sim: Sim, action: dict) -> dict:
    legacy = str(action.get("page_context") or "")
    prefix = legacy.split()[0] if legacy else ""
    stage_id = LEGACY_SUB_TO_STAGE.get(prefix, str(action.get("stage_id") or ""))
    if not stage_id:
        return sim.error(404, "unknown_page_context", "未知的 page_context")
    display = stage_title(stage_id)
    pid = str(action.get("project_id") or "")
    if pid and pid in sim.projects:
        project = sim.projects[pid]
        project["history_page_contexts"].append(legacy)
        project["page_context"] = display
        sim.signal("legacy_stage_ids_preserved", True)
    sim.focus["stage"] = display
    sim.add_event("page_context_opened", legacy=legacy, display=display, stage_id=stage_id)
    return sim.ok(code="opened", stage_id=stage_id, display=display, legacy=legacy)


# ---------------- 任务流 ----------------
def op_task_send(sim: Sim, action: dict) -> dict:
    card = _card(sim, str(action.get("session_id") or action.get("card_session_id")))
    actor = sim.require_actor(str(action.get("actor")))
    idem = str(action.get("idempotency_key") or "")
    if idem and idem in sim.idempotency.setdefault("task_send", {}):
        return sim.ok(code="replayed", task_id=sim.idempotency["task_send"][idem], replayed=True)
    result = _task_send_internal(sim, card, str(action.get("task_kind") or "handoff"),
                                 str(action.get("target_type") or "role"),
                                 str(action.get("target_role_code") or ""),
                                 str(action.get("target_user_id") or ""),
                                 str(action.get("note") or ""),
                                 dict(action.get("payload") or {"initiator": actor["username"],
                                                                "business_case_id": card["business_case_id"]}),
                                 actor["username"], idem)
    if idem:
        sim.idempotency["task_send"][idem] = result["task"]["task_id"]
    return sim.ok(code=result["code"], task_id=result["task"]["task_id"],
                  reused=result.get("reused", False),
                  superseded_task_id=result.get("superseded_task_id", ""))


def _claim_once(sim: Sim, task_id: str, actor_name: str) -> dict:
    with sim.lock:
        task = sim.tasks.get(task_id)
        if task is None:
            return sim.error(404, "task_not_found", "任务不存在")
        if task["status"] == "claimed":
            if task["claimed_by_user_id"] == actor_name:
                return sim.ok(code="already_claimed_by_you", task_id=task_id, idempotent=True)
            return sim.error(409, "task_already_claimed", "任务已被他人领取",
                             claimed_by=task["claimed_by_user_id"])
        if task["status"] != "open":
            return sim.error(409, "task_not_claimable", f"任务状态是 {task['status']}")
        roles = set(sim.actor_roles(actor_name))
        if task["target_type"] == "user" and task["target_user_id"] != actor_name:
            return sim.error(403, "task_not_assigned_to_you", "任务没有派给你")
        if task["target_type"] == "role" and task["target_role_code"] not in roles:
            return sim.error(403, "task_not_assigned_to_you",
                             f"任务属于 {task['target_role_code']}")
        task["status"] = "claimed"
        task["claimed_by_user_id"] = actor_name
        task["claimed_at"] = sim.now()
        card = sim.tasks and sim.cards.get(task["session_id"])
        if card:
            card["current_owner"] = actor_name
        sim.counter["tasks_claimed"] += 1
        sim.add_message(actor_name, "task_claimed", "任务已领取", task_id=task_id)
        sim.add_event("task_claimed", task_id=task_id, actor=actor_name)
        return sim.ok(code="claimed", task_id=task_id, claimed_by=actor_name,
                      arbiter="atomic_update")


def op_task_claim(sim: Sim, action: dict) -> dict:
    return _claim_once(sim, str(action.get("task_id")), str(action.get("actor")))


def op_task_claim_concurrent(sim: Sim, action: dict) -> dict:
    task_id = str(action.get("task_id"))
    actors = list(action.get("actors") or ["alice", "bob"])
    barrier = threading.Barrier(len(actors))
    results = {}
    lock = threading.Lock()

    def _worker(name):
        barrier.wait()
        resp = _claim_once(sim, task_id, name)
        with lock:
            results[name] = resp

    threads = [threading.Thread(target=_worker, args=(name,), daemon=True) for name in actors]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    winners = [name for name, resp in results.items() if resp.get("ok")]
    losers = [name for name, resp in results.items() if not resp.get("ok")]
    sim.concurrency = {"threads": len(actors), "used_barrier": True, "arbiter": "atomic_update",
                       "winners": winners, "losers": losers,
                       "values": {name: resp.get("code") for name, resp in results.items()}}
    if len(winners) != 1:
        sim.signal("duplicate_side_effect", True)
        sim.signal("unsafe_claim", True)
    task = sim.tasks.get(task_id, {})
    return sim.ok(code="concurrent_claim_done", winners=winners, losers=losers,
                  claimed_by=task.get("claimed_by_user_id", ""),
                  threads=len(actors), used_barrier=True, arbiter="atomic_update")


def op_task_complete(sim: Sim, action: dict) -> dict:
    task_id = str(action.get("task_id"))
    actor = str(action.get("actor"))
    version = str(action.get("result_version") or "")

    def _do():
        task = sim.tasks.get(task_id)
        if task is None:
            return sim.error(404, "task_not_found", "任务不存在")
        if task["status"] == "completed":
            if task.get("result_version") == version:
                return sim.ok(code="already_completed", task_id=task_id, idempotent=True)
            return sim.error(409, "task_already_completed", "任务已完成")
        if task["status"] == "cancelled":
            return sim.error(409, "task_cancelled", "任务已取消，不能完成")
        if task["status"] == "open":
            return sim.error(409, "task_not_claimed", "任务还没被领取")
        if task["claimed_by_user_id"] != actor:
            return sim.error(403, "task_not_owned_by_you", "任务不是你领取的")
        task["status"] = "completed"
        task["completed_at"] = sim.now()
        task["completed_by"] = actor
        task["result_version"] = version
        sim.counter["tasks_completed"] += 1
        card = sim.cards.get(task["session_id"])
        step = action.get("merge_step")
        if card and step:
            entry = card["steps"].setdefault(str(int(step)), {"snapshot": {}, "status": "",
                                                              "stale": False})
            delta = _merge_snapshot(entry["snapshot"], dict(action.get("snapshot") or {}))
            if delta:
                sim.add_event("snapshot_merged", card_id=card["card_id"], step=int(step))
        sim.add_message(task.get("payload", {}).get("initiator", ""), "task_completed",
                        "任务已完成", task_id=task_id)
        sim.add_event("task_completed", task_id=task_id, actor=actor)
        return sim.ok(code="completed", task_id=task_id)

    return sim.atomic(_do)


def op_task_cancel(sim: Sim, action: dict) -> dict:
    task_id = str(action.get("task_id"))
    actor = str(action.get("actor"))
    task = sim.tasks.get(task_id)
    if task is None:
        return sim.error(404, "task_not_found", "任务不存在")
    if task["status"] == "cancelled":
        return sim.ok(code="already_cancelled", task_id=task_id, idempotent=True)
    if task["status"] == "completed":
        return sim.error(409, "task_already_completed", "已完成的任务不能取消")
    task["status"] = "cancelled"
    task["cancel_reason"] = str(action.get("reason") or "撤回")
    task["cancelled_at"] = sim.now()
    sim.counter["tasks_cancelled"] += 1
    sim.add_event("task_cancelled", task_id=task_id, actor=actor, reason=task["cancel_reason"])
    sim.add_message(_recipient_of(task), "task_cancelled", "任务已取消", task_id=task_id)
    return sim.ok(code="cancelled", task_id=task_id)


def op_task_cancel_vs_complete_concurrent(sim: Sim, action: dict) -> dict:
    task_id = str(action.get("task_id"))
    completer = str(action.get("completer") or "dave")
    canceller = str(action.get("canceller") or "carol")
    barrier = threading.Barrier(2)
    results = {}

    def _do_complete():
        barrier.wait()
        with sim.lock:
            results["complete"] = op_task_complete(
                sim, {"task_id": task_id, "actor": completer,
                      "result_version": str(action.get("result_version") or "R1")})

    def _do_cancel():
        barrier.wait()
        with sim.lock:
            results["cancel"] = op_task_cancel(sim, {"task_id": task_id, "actor": canceller,
                                                     "reason": "并发撤回"})

    threads = [threading.Thread(target=_do_complete, daemon=True),
               threading.Thread(target=_do_cancel, daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    winners = [key for key, resp in results.items() if resp.get("ok")]
    losers = [key for key, resp in results.items() if not resp.get("ok")]
    task = sim.tasks.get(task_id, {})
    sim.concurrency = {"threads": 2, "used_barrier": True, "arbiter": "atomic_update",
                       "winners": winners, "losers": losers,
                       "values": {key: resp.get("code") for key, resp in results.items()},
                       "final_status": task.get("status")}
    if len(winners) != 1:
        sim.signal("duplicate_side_effect", True)
    return sim.ok(code="race_done", winners=winners, losers=losers,
                  final_status=task.get("status"), threads=2, used_barrier=True,
                  arbiter="atomic_update")


def op_task_list(sim: Sim, action: dict) -> dict:
    actor = str(action.get("actor"))
    roles = set(sim.actor_roles(actor))
    items = []
    for task in sim.tasks.values():
        if task["status"] not in ("open", "claimed"):
            continue
        if task["target_type"] == "user" and task["target_user_id"] != actor:
            continue
        if task["target_type"] == "role" and task["target_role_code"] not in roles:
            continue
        if task["status"] == "claimed" and task["claimed_by_user_id"] != actor:
            continue
        items.append(task["task_id"])
    return sim.ok(tasks=sorted(items), count=len(items))


# ---------------- 跨 Agent 回传 ----------------
def _handoff_key(action: dict) -> str:
    return _join(action.get("source_project_id"), action.get("source_task_id"),
                 action.get("source_result_version"), action.get("handoff_kind"),
                 action.get("business_case_id"))


def _resolve_target_card(sim: Sim, action: dict) -> dict:
    import cpq_case_link

    bcid = str(action.get("business_case_id") or "").strip()
    target_session = str(action.get("target_quote_session_id") or "").strip()
    source_task_id = str(action.get("source_task_id") or "")
    candidates = []
    for sid, card in sorted(sim.cards.items()):
        linked = ""
        if bcid and card["business_case_id"] == bcid:
            linked = "case"
        elif target_session and sid == target_session:
            linked = "session"
        elif source_task_id and any(task["task_id"] == source_task_id
                                    for task in card_tasks(sim, card["card_id"])):
            linked = "task"
        if linked:
            candidates.append({"quote_session_id": sid, "card_id": card["card_id"],
                               "linked_by": linked, "business_case_id": card["business_case_id"],
                               "title": card["title"]})
    decided = cpq_case_link.decide(candidates, business_case_id=bcid,
                                   tech_project_id=str(action.get("source_project_id") or ""),
                                   create_new=bool(action.get("create_new")),
                                   create_reason=str(action.get("create_reason") or ""))
    if decided["code"] == "linked":
        card = sim.cards.get(decided["quote_session_id"])
        if card is None:
            raise SimError(404, "card_not_found", "候选报价卡片不存在")
        return {"outcome": "linked", "card": card, "decided": decided}
    if decided["code"] == "multiple_candidates":
        raise SimError(409, "multiple_candidates", "多个候选报价卡片，需要人工选择")
    if decided["code"] == "no_candidate":
        raise SimError(409, "no_candidate", "认不回原报价卡片，且未明确要求新建")
    sid = target_session or f"qs_new_{len(sim.cards) + 1:03d}"
    card = _new_card(sim, sid, {"owner": "erin", "title": "技术回传新建卡片",
                                "business_case_id": bcid, "customer": ""})
    sim.counter["cards_created_by_handoff"] += 1
    sim.signal("silent_new_card", not bool(action.get("create_new")))
    return {"outcome": "create_new", "card": card, "decided": decided}


def op_handoff_send(sim: Sim, action: dict) -> dict:
    with sim.lock:
        return _handoff_send_locked(sim, action)


def _handoff_send_locked(sim: Sim, action: dict) -> dict:
    key = _handoff_key(action)
    if not str(action.get("business_case_id") or "").strip():
        return sim.error(422, "business_case_missing", "缺少 business_case_id")
    if key in sim.handoffs:
        sim.counter["handoff_replays"] += 1
        row = sim.handoffs[key]
        return sim.ok(code="already_sent", already_sent=True, handoff_id=row["handoff_id"],
                      reused=True, target_quote_session_id=row["target_quote_session_id"],
                      source_task_id=row["source_task_id"],
                      business_case_id=row["business_case_id"])
    actor = str(action.get("actor"))

    def _do():
        resolved = _resolve_target_card(sim, action)
        card = resolved["card"]
        snapshot = dict(action.get("snapshot") or {})
        row_key = key
        step = 2
        entry = card["steps"].setdefault(str(step), {"snapshot": {}, "status": "", "stale": False})
        delta = _merge_snapshot(entry["snapshot"], snapshot)
        current_step = card["current_step"]
        if current_step <= step:
            entry.update({"status": "done", "completed_by": actor, "at": sim.now()})
            card["current_step"] = max(current_step, step + 1)
            card["current_owner"] = _next_role(card)
        elif delta:
            card["steps"][str(step)]["stale"] = False
        if card["current_step"] < current_step:
            sim.signal("step_rollback", True)
        result = _task_send_internal(sim, card, str(action.get("target_task_kind") or "handoff"),
                                     "role", str(action.get("target_role_code") or "sales_mgr"), "",
                                     str(action.get("note") or "技术结果回传"),
                                     {"initiator": actor, "handoff_key": row_key,
                                      "business_case_id": card["business_case_id"],
                                      "source_project_id": str(action.get("source_project_id")),
                                      "source_task_id": str(action.get("source_task_id")),
                                      "source_result_version": str(action.get("source_result_version")),
                                      "snapshot": snapshot},
                                     actor, "")
        source_task_id = str(action.get("source_task_id") or "")
        source_task = sim.tasks.get(source_task_id)
        if source_task is None:
            raise SimError(404, "source_task_not_found", "来源任务不存在")
        if source_task["status"] != "claimed" or source_task["claimed_by_user_id"] != actor:
            raise SimError(409, "source_task_not_owned", "来源任务不是你的或还没领取")
        source_task["status"] = "completed"
        source_task["closed_by_handoff"] = row_key
        source_task["completed_at"] = sim.now()
        sim.counter["tasks_completed"] += 1
        sim._handoff_seq += 1
        handoff_id = f"h_{sim._handoff_seq:03d}"
        if str(action.get("fail_at") or "") == "insert_handoff":
            raise SimError(500, "handoff_failed", "回传失败，整体回滚")
        sim.handoffs[row_key] = {
            "handoff_id": handoff_id, "handoff_key": row_key,
            "handoff_kind": str(action.get("handoff_kind") or ""),
            "source_project_id": str(action.get("source_project_id") or ""),
            "source_task_id": source_task_id,
            "source_result_version": str(action.get("source_result_version") or ""),
            "target_quote_session_id": card["session_id"], "target_card_id": card["card_id"],
            "business_case_id": card["business_case_id"],
            "created_at": sim.now(), "actor": actor,
        }
        sim.counter["handoffs_created"] += 1
        sim.add_message(card["owner"], "handoff_received", "收到技术回传结果",
                        handoff_id=handoff_id, task_id=result["task"]["task_id"],
                        business_case_id=card["business_case_id"])
        sim.add_message(actor, "handoff_sent", "已回传报价", handoff_id=handoff_id)
        sim.add_event("handoff_created", handoff_id=handoff_id, source_task_id=source_task_id,
                      target_card_id=card["card_id"], business_case_id=card["business_case_id"])
        return sim.ok(code="sent", already_sent=False, handoff_id=handoff_id,
                      target_quote_session_id=card["session_id"], target_card_id=card["card_id"],
                      target_task_id=result["task"]["task_id"],
                      source_task_id=source_task_id, source_project_id=action.get("source_project_id"),
                      business_case_id=card["business_case_id"],
                      current_step=card["current_step"], outcome=resolved["outcome"],
                      linked_by=resolved["decided"]["linked_by"])

    return sim.atomic(_do)


def op_handoff_send_concurrent(sim: Sim, action: dict) -> dict:
    barrier = threading.Barrier(2)
    results = {}

    def _worker(name):
        barrier.wait()
        payload = dict(action)
        payload["actor"] = name
        results[name] = op_handoff_send(sim, payload)

    threads = [threading.Thread(target=_worker, args=(name,), daemon=True)
               for name in list(action.get("actors") or ["carol", "carol"])]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    winners = [name for name, resp in results.items() if resp.get("code") == "sent"]
    replays = [name for name, resp in results.items() if resp.get("code") == "already_sent"]
    sim.concurrency = {"threads": 2, "used_barrier": True, "arbiter": "unique_constraint",
                       "winners": winners, "replays": replays,
                       "rows": len(sim.handoffs),
                       "values": {name: resp.get("code") for name, resp in results.items()}}
    if len(sim.handoffs) != 1:
        sim.signal("duplicate_side_effect", True)
    return sim.ok(code="concurrent_handoff_done", rows=len(sim.handoffs),
                  winners=winners, replays=replays, threads=2, used_barrier=True,
                  arbiter="unique_constraint")


def op_handoff_send_failing(sim: Sim, action: dict) -> dict:
    payload = dict(action)
    payload["fail_at"] = str(action.get("fail_at") or "insert_handoff")
    before = {"messages": len(sim.messages), "tasks": len(sim.tasks),
              "handoffs": len(sim.handoffs), "events": len(sim.events)}
    try:
        resp = op_handoff_send(sim, payload)
    except SimError as exc:  # 失败也要能看见「整体回滚」的证据
        resp = sim.error(exc.status, exc.code, exc.message)
    after = {"messages": len(sim.messages), "tasks": len(sim.tasks),
             "handoffs": len(sim.handoffs), "events": len(sim.events)}
    source_task = sim.tasks.get(str(action.get("source_task_id")), {})
    resp["rolled_back"] = bool(sim.signals.get("rolled_back"))
    resp["source_task_status"] = source_task.get("status", "")
    resp["before"] = before
    resp["after"] = after
    if before != after:
        sim.signal("partial_write", True)
    return resp


def op_case_link_decide(sim: Sim, action: dict) -> dict:
    import cpq_case_link

    decided = cpq_case_link.decide(list(action.get("candidates") or []),
                                   business_case_id=str(action.get("business_case_id") or ""),
                                   tech_project_id=str(action.get("tech_project_id") or ""),
                                   create_new=bool(action.get("create_new")),
                                   create_reason=str(action.get("create_reason") or ""))
    sim.counter["case_link_decisions"] += 1
    return sim.ok(code=decided["code"], decided=decided,
                  candidates=decided["candidates"], linked_by=decided["linked_by"],
                  recovered_from_project_id=decided["recovered_from_project_id"])


def op_material_write(sim: Sim, action: dict) -> dict:
    return _material_write_once(sim, action)


def _material_write_once(sim: Sim, action: dict) -> dict:
    project_id = str(action.get("project_id"))
    result_version = str(action.get("result_version"))
    action_type = str(action.get("action_type") or "material")
    key = _join(project_id, result_version, action_type)
    rows = list(action.get("rows") or [])
    with sim.lock:
        existing = sim.idempotency.setdefault("material", {})
        if key in existing:
            sim.counter["material_duplicates"] += 1
            return sim.ok(code="replayed", codes=existing[key], replayed=True,
                          rows=len(rows), unique=True)
        prefix = str(action.get("prefix") or "92022")
        codes = []
        for index in range(len(rows)):
            code = None
            for attempt in range(5):
                seq = sim.counter[f"material_seq_{prefix}"] + 1
                candidate = f"{prefix}{seq:04d}"
                try:
                    if candidate in sim.material["used_codes"]:
                        raise _UniqueViolation(candidate)
                    sim.material["used_codes"].append(candidate)
                    sim.counter[f"material_seq_{prefix}"] += 1
                    code = candidate
                    break
                except _UniqueViolation:
                    sim.counter["material_code_conflicts"] += 1
                    sim.signal("unique_violation_observed", True)
                    continue
            if code is None:
                return sim.error(409, "material_code_conflict", "物料编码冲突")
            codes.append(code)
            sim.material["rows"].append({"project_id": project_id, "result_version": result_version,
                                         "action_type": action_type, "number": code,
                                         "seq": index})
        existing[key] = codes
        sim.counter["material_writes"] += 1
        return sim.ok(code="written", codes=codes, rows=len(rows), unique=True)


class _UniqueViolation(Exception):
    pass


def op_material_write_concurrent(sim: Sim, action: dict) -> dict:
    barrier = threading.Barrier(2)
    results = {}
    lock = threading.Lock()

    def _worker(name):
        payload = dict(action)
        payload["actor"] = name
        if action.get("split_version"):
            payload["result_version"] = f"{action.get('result_version') or 'R1'}-{name}"
        barrier.wait()
        resp = _material_write_once(sim, payload)
        with lock:
            results[name] = resp

    threads = [threading.Thread(target=_worker, args=(name,), daemon=True)
               for name in list(action.get("actors") or ["alice", "bob"])]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    codes = [code for resp in results.values() for code in (resp.get("codes") or [])]
    sim.concurrency = {"threads": 2, "used_barrier": True, "arbiter": "unique_constraint",
                       "codes": codes, "unique_codes": len(set(codes)),
                       "values": {name: resp.get("code") for name, resp in results.items()}}
    if len(set(codes)) != len(codes):
        sim.signal("duplicate_side_effect", True)
    return sim.ok(code="concurrent_write_done", codes=codes, unique_codes=len(set(codes)),
                  rows=len(sim.material["rows"]), threads=2, used_barrier=True,
                  arbiter="unique_constraint",
                  violations=sim.counter["material_code_conflicts"])


# ---------------- 模型 / 工具调用契约（recorded provider） ----------------
ALLOWED_TOOLS = ("tech_ui", "quote_ui", "sql_query")


def op_llm_call(sim: Sim, action: dict) -> dict:
    provider = str(action.get("provider") or "")
    fixture = _fixture(sim, provider)
    actor_name = str(action.get("actor") or "")
    if actor_name:
        actor = sim.actor(actor_name)
        if actor["api_key"] != "test-key":
            sim.signal("real_secret_used", True)
            return sim.error(400, "real_key_forbidden", "只允许 test-key 回放")
    request = fixture.get("request") or {}
    if str(request.get("api_key") or "test-key") != "test-key":
        sim.signal("real_secret_used", True)
        return sim.error(400, "real_key_forbidden", "fixture 里出现了非 test-key 的密钥")
    sim.counter["llm_calls"] += 1
    stream = list(fixture.get("stream") or [])
    kinds = {str(event.get("type")) for event in stream}
    if "refusal" in kinds:
        sim.add_event("llm_refused", fixture=provider)
        return sim.ok(code="model_refused", persisted=sim.llm["persisted"])
    if "invalid_json" in kinds or fixture.get("finish_reason") == "invalid_json":
        sim.add_event("llm_invalid_json", fixture=provider)
        return sim.error(422, "invalid_json", "模型返回的不是合法 JSON")
    tool_uses = [event for event in stream if str(event.get("type")) == "tool_use"]
    if not tool_uses:
        return sim.ok(code="text_only", tool_calls=0, persisted=sim.llm["persisted"])
    truncated = fixture.get("finish_reason") == "length"
    recovered = False
    if truncated:
        if not action.get("allow_truncated_recovery") or not fixture.get("continuation"):
            sim.add_event("llm_truncated", fixture=provider)
            return sim.error(422, "output_truncated", "模型输出被截断且无法恢复")
        continuation = fixture["continuation"]
        tool_uses = _merge_tool_uses(tool_uses, continuation.get("tool_use"))
        recovered = True
    calls = []
    for event in tool_uses:
        name = str(event.get("name") or "")
        args = dict(event.get("input") or {})
        if name not in ALLOWED_TOOLS:
            return sim.error(422, "unknown_tool", f"未注册的工具：{name}")
        missing = [key for key in (action.get("required_args") or []) if key not in args]
        if missing:
            return sim.error(422, "tool_args_missing", f"工具参数缺失：{missing}")
        project = sim.projects.get(str(action.get("project_id") or ""))
        context = args.get("page_context") or args.get("stage")
        if context and action.get("stage") and str(context) != str(action["stage"]):
            return sim.error(409, "output_context_mismatch",
                             "模型输出的 page_context 与当前步骤不一致")
        if args.get("project_id") and action.get("project_id") \
                and str(args["project_id"]) != str(action["project_id"]):
            return sim.error(409, "output_context_mismatch", "模型输出的 project_id 与当前项目不一致")
        calls.append({"name": name, "args": args, "truncated": truncated, "recovered": recovered,
                      "fixture": provider})
        if project is not None and action.get("persist") is not False:
            values = args.get("values") or args.get("fields") or {}
            if isinstance(values, dict):
                project.setdefault("requirement", {}).setdefault("form_values", {}).update(values)
    sim.llm["calls"].extend(calls)
    sim.signal("structured_tool_call", all(call["name"] and isinstance(call["args"], dict)
                                           for call in calls))
    if action.get("persist") is not False:
        sim.llm["persisted"] += 1
    sim.add_event("llm_call", fixture=provider, tool_names=[call["name"] for call in calls],
                  recovered=recovered)
    return sim.ok(code="recovered" if recovered else "tool_call",
                  tool_calls=len(calls), recovered=recovered,
                  persisted=sim.llm["persisted"],
                  names=[call["name"] for call in calls])


def _merge_tool_uses(tool_uses, continuation):
    if not continuation:
        return tool_uses
    merged = [dict(event) for event in tool_uses]
    first = dict(merged[0])
    args = dict(first.get("input") or {})
    args.update(continuation.get("input") or {})
    first["input"] = args
    merged[0] = first
    return merged


# ---------------- 故障注入 / UI 协议 ----------------
def op_http_call(sim: Sim, action: dict) -> dict:
    route = str(action.get("route") or "")
    actor_name = str(action.get("actor") or "")
    sim.counter["http_calls"] += 1
    if action.get("status"):
        status = int(action["status"])
        code = str(action.get("code") or ERROR_CODES.get(status, "error"))
        resp = sim.error(status, code, str(action.get("message") or ""))
    elif not actor_name:
        resp = sim.error(401, "missing_token", "缺少登录凭证")
    elif "missing_token" in route:
        resp = sim.error(401, "missing_token", "缺少登录凭证")
    elif "unknown" in route or "nonexistent" in route:
        resp = sim.error(404, "project_not_found", "项目不存在")
    elif action.get("mode") == "write" and not (_acl_can_write(
            sim, sim.projects.get(str(action.get("project_id")) or "", {}), actor_name)):
        resp = sim.error(403, "forbidden", "无权修改")
    elif action.get("conflict"):
        resp = sim.error(409, "task_already_claimed", "已被他人领取")
    elif action.get("invalid_payload"):
        resp = sim.error(422, "invalid_requirement", "需求字段不合法")
    else:
        resp = sim.error(500, "internal_error", "服务内部错误")
    resp["header_trace_id"] = resp["trace_id"]
    resp["route"] = route
    sim.signal("error_code_with_trace", bool(resp.get("code") and resp.get("trace_id")))
    sim.ui["error_code_with_trace"] = bool(resp.get("code") and resp.get("trace_id"))
    sim.signal("last_error_code", resp["code"])
    return resp


def op_board_action(sim: Sim, action: dict) -> dict:
    actor = str(action.get("actor"))
    role_required = action.get("role_required")
    code = str(action.get("code") or "board_action_failed")
    if role_required and not (set(role_required) & set(sim.actor_roles(actor))):
        resp = sim.error(403, "forbidden", f"需要角色 {'/'.join(role_required)}")
        sim.ui["error_surface"] = "board"
        sim.signal("role_hint_right_only", True)
        sim.signal("top_toast_created", False)
        sim.ui["board_error"] = resp["code"]
        sim.ui["session_error"] = ""
        return resp
    if action.get("fail"):
        resp = sim.error(500, code, "看板动作失败")
        critical = bool(action.get("critical"))
        sim.ui["board_error"] = resp["code"]
        sim.ui["session_error"] = resp["code"]
        sim.ui["critical_failure_persistent"] = critical
        sim.ui["quiet_failure_no_card"] = not critical
        sim.signal("left_right_error_consistent",
                   sim.ui["board_error"] == sim.ui["session_error"])
        return resp
    sim.add_event("board_action", action=str(action.get("action") or ""), actor=actor)
    return sim.ok(code="board_ok")


def op_retry_action(sim: Sim, action: dict) -> dict:
    target = str(action.get("target_op") or "")
    if not target:
        legacy = str(action.get("op") or "")
        target = "" if legacy in ("", "retry_action") else legacy
    handler = OPS.get(target)
    if handler is None:
        return sim.error(500, "unknown_retry_target", f"不能重试未实现的动作：{target}")
    params = {key: value for key, value in action.items()
              if key not in ("op", "target_op", "fail_first", "expect", "label")}
    attempts = []
    if action.get("fail_first", True):
        transient = sim.error(503, "service_unavailable", "临时故障，可重试")
        attempts.append(transient)
        sim.signal("transient_failure", True)
    first = handler(sim, params)
    attempts.append(first)
    sim.responses.append(first)
    counters_before = dict(sim.counter)
    second = handler(sim, params)
    attempts.append(second)
    sim.responses.append(second)
    counters_after = dict(sim.counter)
    duplicated = counters_before != counters_after
    sim.signal("duplicate_side_effect", duplicated)
    sim.signal("retry_reused_key", True)
    sim.counter["retries"] += 1
    return sim.ok(code="retry_done", attempts=len(attempts),
                  no_duplicate=not duplicated, key=str(action.get("idempotency_key") or ""))


def op_ui_board_sync(sim: Sim, action: dict) -> dict:
    sid = str(action.get("session_id") or "")
    card = sim.cards.get(sid)
    if card:
        sim.ui["field_source_badge"] = sorted({item["source"] for item in card["fields"].values()})
        sim.ui["board_left_right_sync"] = bool(card["fields"]) and not card["left_summary"].get("missing")
        sim.ui["monotonic_ui_stage"] = True
    if action.get("part_container"):
        sim.ui["part_detail_in_board"] = str(action["part_container"]) == "board"
    if action.get("params_phase"):
        sim.ui["params_tab_phase"] = int(action["params_phase"])
    if action.get("primary_actions") is not None:
        sim.ui["single_primary_action"] = int(action["primary_actions"]) == 1
    sim.add_event("board_synced", session_id=sid)
    return sim.ok(code="board_synced", sources=sim.ui["field_source_badge"])


def op_ui_error_banner(sim: Sim, action: dict) -> dict:
    critical = bool(action.get("critical"))
    role_mismatch = bool(action.get("role_mismatch"))
    sim.ui["critical_failure_persistent"] = critical
    sim.ui["quiet_failure_no_card"] = not critical
    sim.ui["error_surface"] = "board" if role_mismatch else str(action.get("surface") or "board")
    sim.ui["brand_color"] = str(action.get("brand_color") or "#0060e6")
    sim.signal("top_toast_created", bool(action.get("top_toast")))
    sim.signal("role_hint_right_only", not action.get("top_toast"))
    if action.get("code"):
        sim.ui["session_error"] = str(action["code"])
        sim.ui["board_error"] = str(action["code"])
        sim.signal("left_right_error_consistent", True)
    return sim.ok(code="banner_ready", critical=critical, role_mismatch=role_mismatch)


def op_ui_composer(sim: Sim, action: dict) -> dict:
    composer = dict(action.get("composer") or {})

    def _offset(padding, extra=0, label=""):
        numbers = [int(value) for value in re.findall(r"(\d+)px", str(padding or ""))]
        if not numbers:
            raise SimError(422, "bad_padding", f"{label} padding 不是 px：{padding!r}")
        return numbers[0] + int(extra or 0)

    quote_offset = _offset(composer.get("quote_padding", "10px 16px"),
                           composer.get("quote_extra", 0), "quote")
    tech_offset = _offset(composer.get("tech_padding", "10px 16px"),
                          composer.get("tech_extra", 0), "tech")
    sim.ui["model_button_in_composer"] = bool(composer.get("model_button_in_composer"))
    sim.ui["composer_bottom_aligned"] = quote_offset == tech_offset
    sim.ui["quote_bottom_offset"] = quote_offset
    sim.ui["tech_bottom_offset"] = tech_offset
    return sim.ok(code="composer_ready", quote_offset=quote_offset, tech_offset=tech_offset,
                  aligned=sim.ui["composer_bottom_aligned"],
                  model_button_in_composer=sim.ui["model_button_in_composer"])


OPS = {
    "seed_defaults": op_seed_defaults, "seed_actor": op_seed_actor, "login": op_login,
    "login_with_token": op_login_with_token, "set_api_key": op_set_api_key,
    "read_models": op_read_models, "create_quote_card": op_create_quote_card,
    "quote_parse_requirement": op_quote_parse_requirement, "quote_edit_field": op_quote_edit_field,
    "quote_match_product": op_quote_match_product, "quote_complete_step": op_quote_complete_step,
    "quote_edit_step": op_quote_edit_step, "quote_generate_plans": op_quote_generate_plans,
    "quote_select_plan": op_quote_select_plan, "quote_output_document": op_quote_output_document,
    "quote_handoff_step": op_quote_handoff_step, "quote_send_tech_task": op_quote_send_tech_task,
    "quote_snapshot": op_quote_snapshot, "tech_create_project": op_tech_create_project,
    "tech_open_project": op_tech_open_project,
    "tech_requirement_create": op_tech_requirement_create,
    "tech_requirement_confirm": op_tech_requirement_confirm,
    "tech_requirement_review": op_tech_requirement_review,
    "tech_drawing_parse": op_tech_drawing_parse,
    "tech_drawing_confirm_empty": op_tech_drawing_confirm_empty,
    "tech_part_open": op_tech_part_open, "tech_integration_run": op_tech_integration_run,
    "tech_params_recommend": op_tech_params_recommend,
    "tech_params_confirm": op_tech_params_confirm,
    "tech_process_assemble": op_tech_process_assemble,
    "tech_process_confirm": op_tech_process_confirm, "tech_cost_parts": op_tech_cost_parts,
    "tech_cost_assembly": op_tech_cost_assembly, "tech_cost_total": op_tech_cost_total,
    "tech_cost_confirm": op_tech_cost_confirm, "tech_summary_save": op_tech_summary_save,
    "tech_report_review": op_tech_report_review, "tech_report_publish": op_tech_report_publish,
    "tech_report_handback": op_tech_report_handback, "tech_edit_upstream": op_tech_edit_upstream,
    "tech_refresh": op_tech_refresh, "tech_projection": op_tech_projection,
    "tech_timeline_read": op_tech_timeline_read, "acl_list": op_acl_list,
    "acl_access": op_acl_access, "acl_matrix_check": op_acl_matrix_check,
    "load_fixture_state": op_load_fixture_state,
    "service_restart_recover": op_service_restart_recover,
    "session_append_event": op_session_append_event, "session_restore": op_session_restore,
    "session_rebind_project": op_session_rebind_project,
    "page_context_open": op_page_context_open, "task_send": op_task_send,
    "task_claim": op_task_claim, "task_claim_concurrent": op_task_claim_concurrent,
    "task_complete": op_task_complete, "task_cancel": op_task_cancel,
    "task_cancel_vs_complete_concurrent": op_task_cancel_vs_complete_concurrent,
    "task_list": op_task_list, "handoff_send": op_handoff_send,
    "handoff_send_concurrent": op_handoff_send_concurrent,
    "handoff_send_failing": op_handoff_send_failing, "case_link_decide": op_case_link_decide,
    "material_write": op_material_write, "material_write_concurrent": op_material_write_concurrent,
    "llm_call": op_llm_call, "http_call": op_http_call, "board_action": op_board_action,
    "retry_action": op_retry_action, "ui_board_sync": op_ui_board_sync,
    "ui_error_banner": op_ui_error_banner, "ui_composer": op_ui_composer,
}


def op_registry_report() -> dict:
    registered = set(checks_mod.ACTION_OPS)
    implemented = set(OPS)
    return {"missing": sorted(registered - implemented),
            "extra": sorted(implemented - registered)}


# --------------------------------------------------------------------------- #
# 断言求值
# --------------------------------------------------------------------------- #
MONEY_FIELDS = ("base_cost", "tech_premium", "market_adjust", "profit_margin",
                "extra_charges", "expected_price", "quote_price", "amount",
                "parts_total", "assembly_total", "discount")


def resolve_path(data, path: str):
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, current


def _is_operator_dict(value) -> bool:
    return isinstance(value, dict) and value and all(str(key).startswith("$") for key in value)


def compare(spec, actual, label: str) -> list:
    errors = []
    if _is_operator_dict(spec):
        for op, operand in spec.items():
            if op == "$present":
                ok = (actual is not None) == bool(operand)
            elif op == "$absent":
                ok = (actual is None) == bool(operand)
            elif op == "$ne":
                ok = actual != operand
            elif op == "$gte":
                ok = actual is not None and actual >= operand
            elif op == "$lte":
                ok = actual is not None and actual <= operand
            elif op == "$gt":
                ok = actual is not None and actual > operand
            elif op == "$lt":
                ok = actual is not None and actual < operand
            elif op == "$len":
                ok = hasattr(actual, "__len__") and len(actual) == operand
            elif op == "$contains":
                ok = actual is not None and operand in actual
            elif op == "$contains_text":
                # 列表里的任意一条字符串包含该片段（`$contains` 对列表是成员判断，不是子串）
                items = actual if isinstance(actual, (list, tuple)) else [actual]
                ok = any(isinstance(item, str) and operand in item for item in items)
            elif op == "$any_of":
                ok = actual in list(operand)
            elif op == "$format":
                if operand == "iso8601":
                    ok = isinstance(actual, str) and bool(ISO_RE.match(actual))
                elif operand == "decimal":
                    ok = isinstance(actual, str) and bool(MONEY_RE.match(actual))
                else:
                    ok = False
            elif op == "$order":
                values = list(actual or [])
                ok = operand == "ascending" and values == sorted(values) or \
                    operand == "descending" and values == sorted(values, reverse=True)
            elif op == "$within":
                low, high = list(operand)[:2]
                ok = actual is not None and low <= actual <= high
            elif op == "$truthy":
                ok = bool(actual) == bool(operand)
            else:
                errors.append(f"{label}: 未知断言操作符 {op}")
                continue
            if not ok:
                errors.append(f"{label}: 断言 {op}={operand!r} 不成立（实际 {actual!r}）")
        return errors
    if isinstance(spec, dict):
        if not isinstance(actual, dict):
            return [f"{label}: 期望对象，实际 {type(actual).__name__}"]
        for key, value in spec.items():
            if key not in actual:
                errors.append(f"{label}.{key}: 实际结果里没有这个字段")
                continue
            errors.extend(compare(value, actual[key], f"{label}.{key}"))
        return errors
    if isinstance(spec, list):
        if not isinstance(actual, list):
            return [f"{label}: 期望列表，实际 {type(actual).__name__}"]
        if len(spec) != len(actual):
            return [f"{label}: 期望 {len(spec)} 项，实际 {len(actual)} 项"]
        for index, (want, got) in enumerate(zip(spec, actual)):
            errors.extend(compare(want, got, f"{label}[{index}]"))
        return errors
    if spec != actual:
        errors.append(f"{label}: 期望 {spec!r}，实际 {actual!r}")
    return errors


def _match_subsequence(items, expected_items, label: str) -> list:
    errors = []
    cursor = 0
    for want in expected_items:
        found_at = None
        for index in range(cursor, len(items)):
            if not compare(want, items[index], label):
                found_at = index
                break
        if found_at is None:
            errors.append(f"{label}: 找不到期望的条目 {want!r}（顺序需保持）")
        else:
            cursor = found_at + 1
    return errors


def _focus_card(sim: Sim):
    sid = sim.focus.get("card") or (sorted(sim.cards)[0] if sim.cards else "")
    return sim.cards.get(sid)


def ui_value(name: str, sim: Sim):
    """UI 协议值一律从状态机当前数据算出来，不是写死的常量。"""
    if name == "board_left_right_sync":
        card = _focus_card(sim)
        if card is None:
            return False
        return bool(card["fields"]) and not (card.get("left_summary") or {}).get("missing")
    if name == "field_source_badge":
        card = _focus_card(sim)
        if card is None:
            return []
        return sorted({item["source"] for item in card["fields"].values()})
    if name == "monotonic_ui_stage":
        card = _focus_card(sim)
        if card is None:
            return not sim.signals.get("step_rollback", False)
        return card["current_step"] >= 1 and not sim.signals.get("step_rollback", False)
    if name == "refresh_failure_keeps_state":
        return bool(sim.ui.get("refresh_failure_keeps_state")) and \
            not sim.signals.get("refresh_cleared_state", False)
    if name == "monotonic_ui_stage":
        return bool(sim.ui.get("monotonic_ui_stage", True)) and \
            not sim.signals.get("step_rollback", False)
    return sim.ui.get(name)


def _scan_money_types(sim: Sim) -> bool:
    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in MONEY_FIELDS and isinstance(value, (int, float)) \
                        and not isinstance(value, bool):
                    return True
                if _walk(value):
                    return True
        elif isinstance(node, list):
            for item in node:
                if _walk(item):
                    return True
        return False

    return _walk({"cards": sim.cards, "projects": sim.projects})


_HISTORICAL_FIELDS = ("timeline", "history_page_contexts")


def _strip_history(node):
    """去掉纯历史字段：历史消息原文 / 旧 page_context 必须原样保留，不参与旧口径巡检。"""
    if isinstance(node, dict):
        return {key: _strip_history(value) for key, value in node.items()
                if key not in _HISTORICAL_FIELDS}
    if isinstance(node, list):
        return [_strip_history(item) for item in node]
    return node


def _scan_legacy(sim: Sim) -> bool:
    state = _strip_history({"cards": sim.cards, "projects": sim.projects})
    for _key, text in _walk_strings(state):
        if find_legacy_wording(text):
            return True
    return False


def _walk_strings(node, key=""):
    if isinstance(node, dict):
        for sub_key, value in node.items():
            yield from _walk_strings(value, sub_key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item, key)
    elif isinstance(node, str):
        yield key, node


def _scan_long_text(sim: Sim) -> bool:
    for message in sim.messages:
        if len(str(message.get("title") or "")) > 200:
            return True
    for call in sim.llm["calls"]:
        for _key, text in _walk_strings(call.get("args") or {}):
            if len(text) > 400:
                return True
    return False


def violation(name: str, sim: Sim, case=None) -> bool:
    signals = sim.signals
    direct = {
        "duplicate_side_effect": signals.get("duplicate_side_effect", False),
        "unsafe_claim": signals.get("unsafe_claim", False),
        "step_rollback": signals.get("step_rollback", False),
        "stale_written_as_fresh": signals.get("stale_written_as_fresh", False),
        "cross_project_leak": signals.get("cross_project_leak", False),
        "denied_actor_allowed": signals.get("denied_actor_allowed", False),
        "silent_new_card": signals.get("silent_new_card", False),
        "unsaved_on_failure": signals.get("partial_write", False),
        "timing_race_based": signals.get("used_sleep", False),
        "zero_for_excluded_part": signals.get("zero_for_excluded_part", False),
        "refresh_cleared_state": signals.get("refresh_cleared_state", False),
        "real_secret_used": signals.get("real_secret_used", False),
        "production_host_called": signals.get("production_host_called", False),
        "verbatim_model_text": _scan_long_text(sim),
        "float_money": _scan_money_types(sim),
        "legacy_wording": _scan_legacy(sim),
    }
    if name in direct:
        return bool(direct[name])
    if name == "generic_error_body":
        for resp in sim.responses:
            if int(resp.get("status", 200)) >= 400 and (not resp.get("code")
                                                       or not resp.get("trace_id")):
                return True
        return False
    if name == "completed_without_confirm":
        for project in sim.projects.values():
            for row in STAGES:
                sub = row["sub"]
                if _stage_done(project, sub) and not _confirm_flag(project, sub):
                    return True
        return False
    if name == "published_without_handback_counted_complete":
        for project in sim.projects.values():
            report = project.get("report") or {}
            if report.get("published") and not report.get("handed_back"):
                if _stage_done(project, "5.3"):
                    return True
        return False
    return False


def _confirm_flag(project: dict, sub: str) -> bool:
    checks = {"1.2": ("requirement", "confirmed"), "2.1": ("drawing", "ir"),
              "3.1": ("integration", "drawings_done"), "3.2": ("integration", "params_confirmed"),
              "3.3": ("integration", "process_confirmed"), "4.3": ("cost", "confirmed"),
              "5.2": ("report", "status"), "5.3": ("report", "handed_back")}
    if sub not in checks:
        return True
    section, field = checks[sub]
    return bool((project.get(section) or {}).get(field))


def invariant_ok(name: str, sim: Sim) -> bool:
    if name == "no_cross_project_leak":
        return not violation("cross_project_leak", sim)
    if name == "step_monotonic":
        return not violation("step_rollback", sim)
    if name == "single_source_of_truth":
        return not sim.signals.get("multi_source_conflict", False)
    if name == "no_duplicate_side_effect":
        return not violation("duplicate_side_effect", sim)
    if name == "atomic_claim":
        if not sim.concurrency:
            return True
        return sim.concurrency.get("arbiter") in ("atomic_update", "unique_constraint") and \
            bool(sim.concurrency.get("used_barrier"))
    if name == "transaction_all_or_nothing":
        return not violation("unsaved_on_failure", sim)
    if name == "money_is_decimal":
        return not violation("float_money", sim)
    if name == "time_is_relative":
        for _key, text in _walk_strings({"cards": sim.cards, "projects": sim.projects}):
            if ISO_RE.match(text) is None and re.search(r"\d{4}-\d{2}-\d{2}", text):
                return False
        return True
    if name == "left_right_consistent":
        return not sim.signals.get("left_right_error_inconsistent", False)
    if name == "structured_tool_call":
        return all(call.get("name") and isinstance(call.get("args"), dict)
                   for call in sim.llm["calls"])
    if name == "deny_is_not_enumerable":
        bodies = sim.acl["bodies"]
        if "missing" in bodies and "forbidden" in bodies:
            return bodies["missing"] == bodies["forbidden"]
        return True
    if name == "legacy_compatible":
        return not violation("legacy_wording", sim)
    return False


def evaluate_case(case, sim: Sim) -> list:
    errors = []
    expected = case.get("expected") or {}
    snapshot = sim.snapshot()
    if "http" in expected:
        errors.extend(compare(expected["http"], sim.last_response(), "http"))
    for path, want in (expected.get("state") or {}).items():
        found, actual = resolve_path(snapshot, path)
        if not found:
            if isinstance(want, dict) and want.get("$absent"):
                continue
            errors.append(f"state.{path}: 实际结果里没有这个字段")
        else:
            errors.extend(compare(want, actual, f"state.{path}"))
    for path, want in (expected.get("records") or {}).items():
        found, actual = resolve_path(snapshot.get("records") or {}, path)
        if not found:
            errors.append(f"records.{path}: 实际结果里没有这个计数")
        else:
            errors.extend(compare(want, actual, f"records.{path}"))
    if expected.get("events"):
        errors.extend(_match_subsequence(sim.events, expected["events"], "events"))
    if expected.get("messages"):
        errors.extend(_match_subsequence(sim.messages, expected["messages"], "messages"))
    for name, want in (expected.get("ui_protocol") or {}).items():
        if name not in checks_mod.UI_PROTOCOL:
            errors.append(f"ui_protocol.{name}: 未注册的 UI 协议项")
            continue
        errors.extend(compare(want, ui_value(name, sim), f"ui_protocol.{name}"))
    if "concurrency" in expected:
        errors.extend(compare(expected["concurrency"], sim.concurrency, "concurrency"))
    for name in expected.get("forbidden") or []:
        if name in checks_mod.FORBIDDEN_CHECKS:
            if violation(name, sim, case):
                errors.append(f"forbidden.{name}: 违反了禁止项")
        else:
            found, actual = resolve_path(snapshot, name)
            if found and actual:
                errors.append(f"forbidden.{name}: 实际为 {actual!r}")
    for name in case.get("invariants") or []:
        if name not in checks_mod.INVARIANTS:
            errors.append(f"invariant.{name}: 未注册的不变量")
        elif not invariant_ok(name, sim):
            errors.append(f"invariant.{name}: 不成立")
    return errors


# --------------------------------------------------------------------------- #
# 分层执行
# --------------------------------------------------------------------------- #
def guard_target_url(url: str) -> str:
    """integration 层目标地址守卫：只允许显式给出的非生产地址。"""
    url = str(url or "").strip()
    if not url:
        raise ValueError("integration 层必须显式传入 --target-url")
    if PROD_HOST_RE.search(url):
        raise ValueError(f"拒绝生产 / 准生产地址：{url}")
    if not re.match(r"^https?://", url):
        raise ValueError(f"target-url 必须是 http(s) 地址：{url}")
    return url


def probe_integration(sim: Sim, url: str):
    import urllib.error
    import urllib.request

    base = url.rstrip("/")
    for path in ("/api/health", "/api/projects"):
        probe = {"path": path, "status": 0, "reachable": False}
        try:
            request = urllib.request.Request(base + path, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as resp:  # noqa: S310 - 显式授权的只读探测
                probe["status"] = int(getattr(resp, "status", 200))
                probe["reachable"] = True
                probe["bytes"] = len(resp.read(4096))
        except urllib.error.HTTPError as exc:
            probe["status"] = int(exc.code)
            probe["reachable"] = True
        except Exception as exc:  # pragma: no cover - 网络问题在本机 CI 不出现
            probe["error"] = f"{type(exc).__name__}"
        sim.integration["probes"].append(probe)
    first = sim.integration["probes"][0] if sim.integration["probes"] else {}
    sim.integration.update({"enabled": True, "reachable": bool(first.get("reachable")),
                            "status": int(first.get("status") or 0)})


def _run_pg_case(case, opts, start: float) -> dict:
    """postgres_integration：真跑隔离 PostgreSQL（默认跳过，必须显式开启）。"""
    executor = "postgres_integration"
    if not opts.integration_enabled:
        return {"case": case, "executor": executor, "status": "skipped",
                "reason": "postgres_integration 默认跳过（CPQ_EVAL_INTEGRATION=1 且配置 "
                          "CPQ_EVAL_PG_HOST 才运行）",
                "duration_ms": 0}
    from . import pg_integration
    try:
        outcome = pg_integration.run_case(case)
    except Exception as exc:                              # pragma: no cover - 执行器缺陷
        return {"case": case, "executor": executor, "status": "invalid",
                "reason": f"PG 执行器异常：{type(exc).__name__}: {exc}",
                "duration_ms": int((time.time() - start) * 1000)}
    if outcome.get("status") == "skipped":
        return {"case": case, "executor": executor, "status": "skipped",
                "reason": outcome.get("reason") or "PG 环境未提供",
                "duration_ms": int((time.time() - start) * 1000)}
    observation = outcome.get("observation") or {}
    errors = []
    touched = pg_integration.touched_production(observation)
    if not touched:
        errors.append("声明 postgres_integration 但没有真的连过隔离库")
    errors.extend(production_mod.compare_production(case, observation, compare, resolve_path))
    status = "passed" if not errors else "failed"
    return {"case": case, "executor": executor, "status": status,
            "reason": "；".join(errors)[:800],
            "duration_ms": int((time.time() - start) * 1000),
            "trace": touched, "observation": observation}


def _run_production_case(case, opts, start: float) -> dict:
    """production_unit / production_http / recorded_provider 三层的执行入口。"""
    executor = production_mod.executor_of(case)
    try:
        outcome = production_mod.run_case(case)
    except Exception as exc:                              # pragma: no cover - 执行器缺陷
        return {"case": case, "executor": executor, "status": "invalid",
                "reason": f"执行器异常：{type(exc).__name__}: {exc}",
                "duration_ms": int((time.time() - start) * 1000)}
    if outcome.get("status") == "skipped":
        return {"case": case, "executor": executor, "status": "skipped",
                "reason": outcome.get("reason") or "生产层不可执行",
                "duration_ms": int((time.time() - start) * 1000)}
    observation = outcome.get("observation") or {}
    errors = []
    touched = production_mod.touched_production(observation)
    if not touched:
        errors.append("声明 production-backed 但没有任何生产入口被命中（疑似退化成 Sim）")
    expected = case.get("expected") or {}
    last = observation.get("last") or {}
    if last.get("ok") is False and "error" not in expected:
        errors.append(f"最后一步失败但没有断言：{(last.get('error') or {}).get('message')}")
    if last.get("ok") is True and "error" in expected:
        errors.append("期望报错，但最后一步成功")
    errors.extend(production_mod.compare_production(case, observation, compare, resolve_path))
    status = "passed" if not errors else "failed"
    return {"case": case, "executor": executor, "status": status,
            "reason": "；".join(errors)[:800],
            "duration_ms": int((time.time() - start) * 1000),
            "trace": touched, "observation": observation}


def run_case(case, fixtures: dict, opts) -> dict:
    start = time.time()
    layer = case.get("layer") or "deterministic"
    executor = production_mod.executor_of(case)
    if executor == "specification_only":
        # 没有可执行步骤：只校验声明（schema / 契约）本身，不谎称"跑过了"。
        errors = production_mod.spec_only_errors(case)
        return {"case": case, "executor": executor,
                "status": "passed" if not errors else "failed",
                "reason": "；".join(errors)[:800] if errors
                          else "静态契约校验通过（本条没有可执行步骤，未执行）",
                "duration_ms": 0}
    if executor in production_mod.GATE_EXECUTORS:
        return _run_production_case(case, opts, start)
    if executor == "postgres_integration":
        return _run_pg_case(case, opts, start)
    sim = Sim(case, fixtures)
    try:
        if layer == "integration":
            try:
                url = guard_target_url(opts.target_url)
            except ValueError as exc:
                return {"case": case, "status": "invalid", "reason": str(exc), "duration_ms": 0}
            probe_integration(sim, url)
        for action in case.get("actions") or []:
            try:
                sim.run(action)
            except CaseAssertionError as exc:
                return {"case": case, "status": "failed", "reason": str(exc),
                        "duration_ms": int((time.time() - start) * 1000)}
        errors = evaluate_case(case, sim)
    except SimError as exc:
        errors = [f"执行异常：{exc}"]
    except Exception as exc:  # pragma: no cover - 引擎缺陷也要被看见
        errors = [f"引擎异常：{type(exc).__name__}: {exc}"]
    status = "passed" if not errors else "failed"
    return {"case": case, "status": status, "reason": "；".join(errors)[:800],
            "duration_ms": int((time.time() - start) * 1000)}


def run_cases(cases, fixtures: dict, opts) -> list:
    return [run_case(case, fixtures, opts) for case in cases]


# --------------------------------------------------------------------------- #
# 校验与报告
# --------------------------------------------------------------------------- #
def validate_dataset() -> dict:
    errors, warnings = [], []
    registry = op_registry_report()
    if registry["missing"]:
        errors.append("有已注册但未实现的动作：" + ", ".join(registry["missing"]))
    if registry["extra"]:
        warnings.append("实现了未注册的动作：" + ", ".join(registry["extra"]))
    try:
        suites = load_suites()
        cases = [case for suite in suites for case in suite.cases()]
    except DatasetError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": [], "counts": {}, "coverage": [],
                "op_registry": registry, "legacy_audit": []}
    fixtures = load_fixtures()
    errors.extend(checks_mod.check_dataset(cases))
    errors.extend(checks_mod.check_fixtures(cases, fixtures, fixture_exists))
    errors.extend(checks_mod.check_dataset_assets())
    coverage = coverage_mod.required_coverage(cases)
    for item in coverage:
        if not item["ok"]:
            errors.append(f"覆盖不足：{item['requirement']} —— {item['detail']}")
    audit = checks_mod.legacy_wording_audit()
    stale = checks_mod.unclassified_legacy_hits()
    if stale:
        # 未登记为历史文本的旧口径 = 真正过期，必须处理（不是警告）。
        errors.append("出现未登记的旧口径文案（真正过期，必须改）："
                      + ", ".join(f"{item['file']}:{item['line']} {item['legacy']}"
                                  for item in stale[:5]))
    # 已登记为历史文本的旧口径只是**信息**（原样保留、不改写），不再当警告刷屏；
    # 真正过期的旧口径走上面的 errors。分类表见 dataset/evals/cpq/legacy_wording_audit.json。
    return {
        "ok": not errors, "errors": errors, "warnings": warnings,
        "counts": {"suites": len(suites), "cases": len(cases), "fixtures": len(fixtures)},
        "coverage": coverage, "op_registry": registry, "legacy_audit": audit,
        "legacy_audit_classified": len(audit) - len(stale),
    }


def _pg_case_details(results) -> list:
    """每条 postgres_integration 案例的执行明细（不含任何密码 / 连接串）。"""
    out = []
    for item in results:
        case = item.get("case")
        if production_mod.executor_of(case) != "postgres_integration":
            continue
        entry = case.get("entry") or {}
        observation = item.get("observation") or {}
        steps = observation.get("steps") or {}
        for step_id, step in steps.items():
            step = step or {}
            out.append({
                "case_id": case.case_id,
                "status": item.get("status"),
                "scenario": step.get("scenario"),
                "temp_db": step.get("db"),
                "production_entry": (f"{entry.get('module')}.{entry.get('function')}"
                                     if entry.get("function") else entry.get("module", "")),
                "queries": observation.get("records", {}).get("pg_queries", 0),
                "result": step.get("result"),
                "cleanup": step.get("cleanup"),
            })
    return out


def build_report(results, options) -> dict:
    summary = scoring_mod.summarize(results)
    cases = [item["case"] for item in results]
    gate = summary["gate"]
    pg_row = summary["by_executor"].get("postgres_integration") or {}
    report = {
        "generated_by": "scripts/cpq_eval/runner.py",
        "options": options,
        "summary": summary,
        "matrix": coverage_mod.matrix(cases),
        "substeps": coverage_mod.substep_table(cases),
        "quote_steps": coverage_mod.quote_step_table(cases),
        "roles": coverage_mod.role_table(cases),
        "postgres": {
            "case_count": gate.get("integration_total", 0),
            "unique_scenario_count": gate.get("integration_unique_scenarios", 0),
            "scenarios": gate.get("integration_scenarios", []),
            "executed": sum(int(v) for k, v in pg_row.items()
                            if k in ("passed", "failed", "invalid")),
            "passed": int(pg_row.get("passed", 0)),
            "failed": int(pg_row.get("failed", 0)),
            "invalid": int(pg_row.get("invalid", 0)),
            "skipped": int(pg_row.get("skipped", 0)),
            "cleanup_succeeded": gate.get("integration_cleanup_ok", 0),
            "cleanup_failed": gate.get("integration_cleanup_failed", 0),
            "cases": _pg_case_details(results),
        },
        "failures": summary["failures"],
    }
    if not options.get("pg_sentinel"):
        return report
    from . import pg_sentinel

    sentinel = pg_sentinel.run_all()
    report["pg_sentinel"] = sentinel
    summary["gate"]["pg_sentinel"] = {
        "executed": len(sentinel.get("rows") or []),
        "killed": sentinel.get("killed", 0),
        "survived": sentinel.get("survived", 0),
        "available": sentinel.get("available", False),
    }
    report["postgres"]["mutation_executed"] = len(sentinel.get("rows") or [])
    report["postgres"]["mutation_killed"] = sentinel.get("killed", 0)
    report["postgres"]["mutation_survived"] = sentinel.get("survived", 0)
    return report


def write_report(directory, report: dict):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    json_path = path / "cpq_eval_report.json"
    md_path = path / "cpq_eval_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                         encoding="utf-8")
    md = [f"# CPQ 业务回归数据集执行报告", "",
          f"- 通过 {report['summary']['totals']['passed']} / 失败 "
          f"{report['summary']['totals']['failed']} / 跳过 {report['summary']['totals']['skipped']}"
          f" / 非法 {report['summary']['totals']['invalid']}",
          f"- 加权得分 {report['summary']['score']}（P0=3 / P1=2 / P2=1）", "",
          "```", coverage_mod.render_matrix(report["matrix"]), "```", "",
          "```", coverage_mod.render_executors(report["summary"]["by_executor"],
                                               report["summary"]["gate"]), "```", "",
          "```", coverage_mod.render_postgres_layer(report["summary"]["by_executor"],
                                                    report["summary"]["gate"]), "```", ""]
    pg = report.get("postgres") or {}
    if pg.get("cases"):
        md.append("## PostgreSQL 案例明细")
        for row in pg["cases"]:
            md.append(f"- `{row['case_id']}` [{row['status']}] scenario={row['scenario']} "
                      f"entry={row['production_entry']} temp_db={row['temp_db']} "
                      f"queries={row['queries']} cleanup={row['cleanup']}")
        md.append("")
    sentinel = report.get("pg_sentinel") or {}
    if sentinel.get("rows"):
        md.append("## PostgreSQL mutation sentinel")
        md.append(f"- executed={len(sentinel['rows'])} killed={sentinel.get('killed', 0)} "
                  f"survived={sentinel.get('survived', 0)}")
        for row in sentinel["rows"]:
            md.append(f"- [{'KILLED' if row.get('killed') else 'SURVIVED'}] {row['mutation']} "
                      f"case={row['case']} baseline_ok={row.get('baseline_ok')} "
                      f"mutated_ok={row.get('mutated_ok')} {row.get('reason', '')}")
        md.append("")
    md.extend(f"- 失败：{item['id']}（{item['status']}）{item['reason']}"
              for item in report["failures"][:40])
    md_path.write_text(scoring_mod.redact("\n".join(md)), encoding="utf-8")
    return json_path, md_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
class Options:
    def __init__(self, **kwargs):
        self.layer = kwargs.get("layer") or ""
        self.domain = kwargs.get("domain") or ""
        self.priority = kwargs.get("priority") or ""
        self.case_id = kwargs.get("case") or ""
        self.executor = kwargs.get("executor") or ""
        self.report_dir = kwargs.get("report") or ""
        self.target_url = kwargs.get("target_url") or ""
        self.integration_enabled = bool(kwargs.get("integration_enabled"))
        self.pg_sentinel = bool(kwargs.get("pg_sentinel"))
        self.quiet = bool(kwargs.get("quiet"))
        self.limit = int(kwargs.get("limit") or 0)
        self.no_report = bool(kwargs.get("no_report"))
        self.baseline_log = kwargs.get("baseline_log") or ""

    def as_dict(self):
        return {"layer": self.layer, "executor": self.executor, "domain": self.domain,
                "priority": self.priority,
                "case": self.case_id, "target_url": self.target_url,
                "integration_enabled": self.integration_enabled, "limit": self.limit,
                "pg_sentinel": self.pg_sentinel}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m scripts.cpq_eval.runner",
        description="CPQ 业务回归数据集 runner（离线分层：deterministic / recorded_provider / integration）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="列出全部案例（id / domain / priority / 覆盖轴）")
    group.add_argument("--validate", action="store_true", help="只做 schema、契约与覆盖校验，不执行案例")
    group.add_argument("--snapshot-routes", action="store_true",
                       help="重算真实路由快照与写路由策略表（维护用，会写 dataset/evals/cpq/routes/）")
    parser.add_argument("--layer", choices=list(LAYERS), default="", help="只跑某一层（数据属性）")
    parser.add_argument("--executor", choices=list(EXECUTORS), default="",
                        help="只跑某一执行层（specification_only / simulation / production_unit / production_http / "
                             "recorded_provider / postgres_integration）")
    parser.add_argument("--domain", choices=list(DOMAINS), default="", help="只跑某个 domain")
    parser.add_argument("--priority", choices=list(PRIORITIES), default="", help="只跑某个优先级")
    parser.add_argument("--case", default="", help="只跑某条案例 id")
    parser.add_argument("--report", default="", help="报告输出目录（默认临时目录，不污染仓库）")
    parser.add_argument("--target-url", default="", help="integration 层目标地址（必须显式给出，禁生产地址）")
    parser.add_argument("--baseline-log", default="", help="可选：把一次 unittest 全量日志分类写进报告")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    parser.add_argument("--no-report", action="store_true", help="不写报告文件")
    parser.add_argument("--pg-sentinel", action="store_true",
                        help="跑完 postgres_integration 案例后再真跑 PostgreSQL mutation sentinel 并写进报告")
    parser.add_argument("--quiet", action="store_true", help="只输出结果摘要")
    return parser


def cmd_list(cases, quiet=False) -> int:
    for case in cases:
        axis = f"stage={case.get('stage')} sub={case.get('sub_step')}" if case.get("domain") == "tech" \
            else (f"quote_step={case.get('quote_step')}" if case.get("domain") == "quote" else "")
        print(f"{case.case_id}\t{case.get('domain')}\t{case.get('priority')}\t{case.get('layer')}\t"
              f"{axis}\t{case.get('title')}")
    if not quiet:
        print(f"# 共 {len(cases)} 条案例")
    return 0


def cmd_validate() -> int:
    report = validate_dataset()
    print("== 数据集校验 ==")
    print(json.dumps(report["counts"], ensure_ascii=False))
    print(job := scoring_mod.redact(coverage_mod.render_required(
        load_cases() if report["counts"] else [])))
    del job
    for item in report["coverage"]:
        if not item["ok"]:
            print(f"  缺 {item['requirement']} —— {item['detail']}")
    for warning in report["warnings"]:
        print(f"  警告：{warning}")
    if report.get("legacy_audit_classified"):
        print(f"  信息：旧口径审计 {report['legacy_audit_classified']} 处均为已登记历史文本，"
              f"原样保留不改写（dataset/evals/cpq/legacy_wording_audit.json）")
    for error in report["errors"]:
        print(f"  错误：{scoring_mod.redact(error)}")
    print("结果：" + ("通过" if report["ok"] else f"不通过（{len(report['errors'])} 个问题）"))
    return 0 if report["ok"] else 1


def cmd_snapshot_routes() -> int:
    """重算并写回 `dataset/evals/cpq/routes/` 两份快照（真实路由 + 写路由策略）。

    只有真实路由表 / 白名单 / 案例 `covers_routes` 发生变化时才需要手动重跑；
    覆盖测试会先把「快照 vs 现算」的差异报出来，不静默通过。
    """
    cases = load_cases()
    routes_path = prodkit.write_snapshot({
        "reads": coverage_mod.read_route_report(cases),
        "uncovered_reads_with_reason": {
            row["route"]: row["reason"] for row in coverage_mod.read_route_report(cases)
            if not row.get("covered_by") and row.get("reason")
        },
    })
    policy = coverage_mod.build_write_policy(cases)
    policy_path = routes_path.parent / "write_route_policy.json"
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"已重算路由快照：{routes_path}")
    print(f"已重算写路由策略：{policy_path}（{len(policy['writes'])} 条写路由，"
          f"白名单 {policy['contribute_whitelist_size']} 条）")
    return 0


def cmd_run(options: Options) -> int:
    try:
        cases = load_cases(domain=options.domain, priority=options.priority,
                           case_id=options.case_id)
    except DatasetError as exc:
        print(f"数据集不可用：{exc}", file=sys.stderr)
        return 2
    if options.layer:
        cases = [case for case in cases if case.get("layer") == options.layer]
    if options.executor:
        cases = [case for case in cases
                 if production_mod.executor_of(case) == options.executor]
    # 默认跑全部案例：postgres_integration 由 run_case 标成 skipped（必须单独可见，
    # 不能因为「没启用」就直接从统计里消失）。
    if options.limit:
        cases = cases[:options.limit]
    if not cases:
        print("没有匹配的案例（检查 --layer / --domain / --priority / --case）", file=sys.stderr)
        return 2
    fixtures = load_fixtures()
    results = run_cases(cases, fixtures, options)
    summary = scoring_mod.summarize(results)
    report = build_report(results, options.as_dict())
    if options.baseline_log:
        try:
            report["baseline"] = scoring_mod.classify_baseline_log(
                Path(options.baseline_log).read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            report["baseline"] = {"error": str(exc)}
    totals = summary["totals"]
    print(f"== CPQ 业务回归数据集（{len(cases)} 条）==")
    print(f"通过 {totals['passed']} / 失败 {totals['failed']} / 跳过 {totals['skipped']} / "
          f"非法 {totals['invalid']}；加权得分 {summary['score']}")
    print(coverage_mod.render_executors(summary["by_executor"], summary["gate"]))
    print(coverage_mod.render_postgres_layer(summary["by_executor"], summary["gate"]))
    if not options.quiet:
        print(coverage_mod.render_matrix(report["matrix"]))
        print(coverage_mod.render_substeps([item["case"] for item in results]))
        print(coverage_mod.render_quote_steps([item["case"] for item in results]))
        print(coverage_mod.render_required([item["case"] for item in results]))
    for item in summary["failures"]:
        print(f"  [{item['status']}] {item['id']} {item['domain']}/{item['priority']}：{item['reason']}")
    if not options.no_report:
        directory = options.report_dir or tempfile.mkdtemp(prefix="cpq_eval_")
        json_path, md_path = write_report(directory, report)
        print(f"报告：{json_path}")
        print(f"报告：{md_path}")
    return int(summary["exit_code"])


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list:
        try:
            return cmd_list(load_cases(domain=args.domain, priority=args.priority,
                                       layer=args.layer, case_id=args.case), args.quiet)
        except DatasetError as exc:
            print(f"数据集不可用：{exc}", file=sys.stderr)
            return 2
    if args.validate:
        return cmd_validate()
    if args.snapshot_routes:
        return cmd_snapshot_routes()
    options = Options(layer=args.layer, executor=args.executor, domain=args.domain,
                      priority=args.priority, case=args.case,
                      report=args.report, target_url=args.target_url,
                      integration_enabled=str(os.environ.get("CPQ_EVAL_INTEGRATION") or "") == "1",
                      quiet=args.quiet, limit=args.limit, no_report=args.no_report,
                      pg_sentinel=args.pg_sentinel, baseline_log=args.baseline_log)
    if options.target_url:
        # 无论是否命中案例，只要显式传了 --target-url 就先做生产地址守卫，避免误连线上。
        try:
            guard_target_url(options.target_url)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if (options.integration_enabled and not options.target_url and not options.layer
            and not options.quiet):
        print("提示：CPQ_EVAL_INTEGRATION=1 但没有 --target-url；integration 层将按 "
              "postgres_integration 处理（需要 CPQ_EVAL_PG_HOST）。")
    return cmd_run(options)


if __name__ == "__main__":
    raise SystemExit(main())
