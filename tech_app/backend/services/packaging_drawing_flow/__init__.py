"""DWG 图纸解析会话、右侧需求看板与业务流程贯通（第 5 批 Spec）。

纯编排层：七个步骤只调依赖缝（第 1 到 4 批与四个包装引擎），自己不实现转换、
不解析 DXF、不算几何、不调模型。对外接口见 Spec 第 2.1 节（冻结给第 6 批）。
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
from typing import Any, Callable, Dict, List, Optional

from tech_app.backend.storage import store

from . import anchor as anchor_mod
from . import gates as gates_mod
from . import model
from . import persistence
from . import steps as steps_mod

FLOW_VERSION = model.FLOW_VERSION
ANCHOR_VERSION = model.ANCHOR_VERSION
STALE_VERSION = model.STALE_VERSION
STEP_IDS = model.STEP_IDS
STEP_TITLES = model.STEP_TITLES
STEP_STATUSES = model.STEP_STATUSES
FIELD_BOARD_STATES = model.FIELD_BOARD_STATES
GATE_STAGES = model.GATE_STAGES
STALE_REASONS = model.STALE_REASONS
DEPENDENCIES = model.DEPENDENCIES
DrawingFlowError = model.DrawingFlowError

_MODULE_PATHS = {
    "file_preflight": "tech_app.backend.services.file_preflight",
    "cad_converter": "tech_app.backend.services.cad_converter",
    "cad_ir": "tech_app.backend.services.cad_ir",
    "packaging_semantics": "tech_app.backend.services.packaging_semantics",
    "requirement_service": "tech_app.backend.services.requirement_service",
    "packaging_match": "tech_app.backend.services.packaging_match",
    "packaging_bom": "tech_app.backend.services.packaging_bom",
    "packaging_route": "tech_app.backend.services.packaging_route",
    "packaging_cost": "tech_app.backend.services.packaging_cost",
}

_CACHE: Dict[str, Any] = {}


def _dependency(name: str) -> Optional[Any]:
    """依赖缝：解析不到就返回 None（不许把 AttributeError 抛给用户）。"""
    key = str(name or "")
    if key in _CACHE:
        return _CACHE[key]
    path = _MODULE_PATHS.get(key)
    module = None
    if path:
        try:
            module = importlib.import_module(path)
        except Exception:
            module = None
    _CACHE[key] = module
    return module


def _make_resolver(deps: Any = None) -> Callable[[str], Optional[Any]]:
    if isinstance(deps, dict):
        return lambda name: deps.get(name)
    return _dependency


def _available(name: str) -> bool:
    path = _MODULE_PATHS.get(str(name or ""))
    if not path:
        return False
    try:
        return importlib.util.find_spec(path) is not None
    except Exception:
        return False


def capability() -> Dict[str, Any]:
    dependencies = {name: _available(name) for name in model.DEPENDENCIES}
    missing = [name for name in ("file_preflight", "cad_converter", "cad_ir",
                                 "packaging_semantics") if not dependencies.get(name)]
    return {"available": not missing,
            "version": model.FLOW_VERSION,
            "steps": model.steps(),
            "dependencies": dependencies,
            "message": ("图纸解析链路依赖的能力尚未就绪：%s" % "、".join(missing)) if missing
                       else "图纸解析链路编排层已就绪"}


def steps() -> List[Dict[str, Any]]:
    return model.steps()


def run_id_for(project_id: str, *, prompt: Any = "", source_sha256: Any = "",
               drawing_version: Any = 0, requirement_snapshot_version: Any = "") -> str:
    return model.run_id_for(project_id, prompt=prompt, source_sha256=source_sha256,
                            drawing_version=drawing_version,
                            requirement_snapshot_version=requirement_snapshot_version)


def requirement_snapshot_version(project_id: str) -> str:
    return anchor_mod.requirement_snapshot_version(project_id)


def current_anchor(project_id: str) -> Dict[str, Any]:
    return anchor_mod.current_anchor(project_id)


def summarize(state: Any) -> Dict[str, Any]:
    return model.summarize(state)


def migrate(doc: Any) -> Dict[str, Any]:
    return model.migrate(doc)


def stale_view(project_id: str) -> Dict[str, Any]:
    return anchor_mod.stale_view(project_id)


def mark_downstream_stale(project_id: str, reasons: Any, *, actor: str = "system") -> Dict[str, Any]:
    return anchor_mod.mark_stale(project_id, list(reasons or []), actor=actor)


def clear_downstream_stale(project_id: str, stage: str, *, actor: str = "system") -> Dict[str, Any]:
    return anchor_mod.clear_stale(project_id, stage, actor=actor)


def inheritance(project_id: str, *, stage: str = "") -> Dict[str, Any]:
    return anchor_mod.inheritance(project_id, _dependency, stage=stage)


def gates(project_id: str, *, stage: str = "") -> Dict[str, Any]:
    return gates_mod.build(project_id, resolve=_dependency, stage=stage)


def require_gate(project_id: str, stage: str, *, actor: str = "") -> Dict[str, Any]:
    return gates_mod.require(project_id, stage, resolve=_dependency)


def _new_step(step_id: str, run_id: str) -> Dict[str, Any]:
    return {"step_id": step_id, "title": model.STEP_TITLES[step_id], "status": "pending",
            "attempt": 0, "key": "flow:%s:%s" % (run_id, step_id), "seq": 0,
            "started_at": "", "finished_at": "", "error_code": "", "error_message": "",
            "retryable": False, "detail": {}}


def _steps_index(flow: Any) -> Dict[str, Dict[str, Any]]:
    rows = (flow or {}).get("steps") if isinstance((flow or {}).get("steps"), list) else []
    return {str((row or {}).get("step_id")): dict(row) for row in rows if isinstance(row, dict)}


def _public_state(project_id: str, flow: Any) -> Dict[str, Any]:
    payload = flow if isinstance(flow, dict) else {}
    run_id = str(payload.get("run_id") or "")
    index = _steps_index(payload)
    ordered = []
    for step_id in model.STEP_IDS:
        row = dict(index.get(step_id) or _new_step(step_id, run_id))
        row["detail"] = model.jsonable(row.get("detail") or {})
        ordered.append(row)
    gates_payload = payload.get("gates")
    if not isinstance(gates_payload, dict) or not gates_payload:
        gates_payload = gates_mod.build(project_id, resolve=_dependency)["stages"]
    return {
        "flow_version": model.FLOW_VERSION,
        "project_id": project_id,
        "run_id": run_id,
        "status": str(payload.get("status") or "pending"),
        "started_at": str(payload.get("started_at") or ""),
        "finished_at": str(payload.get("finished_at") or ""),
        "inputs": model.jsonable(payload.get("inputs") or {}),
        "anchor": anchor_mod.current_anchor(project_id),
        "steps": ordered,
        "fields": model.jsonable(payload.get("fields") or {}),
        "pending": model.jsonable(payload.get("pending") or {"needs_confirmation": [],
                                                             "conflict": [], "missing": [],
                                                             "total": 0}),
        "downstream": model.jsonable(payload.get("downstream") or {}),
        "gates": model.jsonable(gates_payload),
        "stale": anchor_mod.stale_view(project_id),
    }


def flow_state(project_id: str) -> Dict[str, Any]:
    return _public_state(project_id, persistence.load_flow(project_id) or {})


def _source_bytes(project_id: str, meta: Dict[str, Any]) -> bytes:
    name = str((meta or {}).get("source_path") or "")
    if not name:
        return b""
    try:
        data = store._blob().get_bytes("%s/%s" % (project_id, name))
    except Exception:
        return b""
    return bytes(data) if isinstance(data, (bytes, bytearray)) else b""


def _reusable(previous: Any, source_sha256: str, drawing_version: int,
              prompt: Any, snapshot: str) -> bool:
    if not isinstance(previous, dict) or not str(previous.get("run_id") or ""):
        return False
    inputs = previous.get("inputs") if isinstance(previous.get("inputs"), dict) else {}
    if str(inputs.get("source_sha256") or "") != source_sha256:
        return False
    if model._as_int(inputs.get("drawing_version"), 0) != int(drawing_version):
        return False
    if model.canonical_prompt(inputs.get("prompt")) != model.canonical_prompt(prompt):
        return False
    return snapshot in {str(inputs.get("requirement_snapshot_version") or ""),
                        str(previous.get("requirement_snapshot_version_after") or "")}


def _stale_reasons(previous: Any, source_sha256: str, drawing_version: int,
                   snapshot: str) -> List[str]:
    if not isinstance(previous, dict) or not str(previous.get("run_id") or ""):
        return []
    inputs = previous.get("inputs") if isinstance(previous.get("inputs"), dict) else {}
    reasons: List[str] = []
    old_sha = str(inputs.get("source_sha256") or "")
    if old_sha and old_sha != source_sha256:
        reasons.append("source_sha256_changed")
    old_version = model._as_int(inputs.get("drawing_version"), 0)
    if old_version and old_version != int(drawing_version):
        reasons.append("drawing_version_changed")
    known = {str(inputs.get("requirement_snapshot_version") or ""),
             str(previous.get("requirement_snapshot_version_after") or "")}
    if snapshot and snapshot not in known:
        reasons.append("requirement_snapshot_changed")
    return reasons


def start(project_id: str, *, prompt: Any = "", actor: str = "system",
          run_id: str = "") -> Dict[str, Any]:
    meta = store.load_meta(project_id) or {}
    content = _source_bytes(project_id, meta)
    source_sha256 = hashlib.sha256(content).hexdigest()
    drawing_version = model._as_int(meta.get("input_revision"), 1)
    snapshot = anchor_mod.requirement_snapshot_version(project_id)
    previous = persistence.load_flow(project_id)
    wanted = str(run_id or "")
    if not wanted:
        if _reusable(previous, source_sha256, drawing_version, prompt, snapshot):
            wanted = str(previous.get("run_id") or "")
        else:
            wanted = model.run_id_for(project_id, prompt=prompt, source_sha256=source_sha256,
                                      drawing_version=drawing_version,
                                      requirement_snapshot_version=snapshot)
    current = previous if (isinstance(previous, dict)
                           and str(previous.get("run_id") or "") == wanted) else None
    if current is None:
        current = {"flow_version": model.FLOW_VERSION, "project_id": project_id,
                   "run_id": wanted, "status": "pending", "started_at": steps_mod.now_iso(),
                   "finished_at": "", "steps": [],
                   "inputs": {"source_sha256": source_sha256,
                              "drawing_version": drawing_version,
                              "prompt": model.canonical_prompt(prompt),
                              "requirement_snapshot_version": snapshot},
                   "requirement_snapshot_version_after": "",
                   "fields": {}, "pending": {"needs_confirmation": [], "conflict": [],
                                             "missing": [], "total": 0},
                   "downstream": {}, "gates": {}, "_semantics": None}
        persistence.save_flow(project_id, current)
        fresh = anchor_mod.empty_anchor(project_id, wanted)
        fresh["source_sha256"] = source_sha256
        fresh["drawing_version"] = drawing_version
        fresh["requirement_snapshot_version"] = snapshot
        fresh["updated_at"] = steps_mod.now_iso()
        fresh["updated_by"] = str(actor or "system")
        persistence.save_anchor(project_id, fresh)
        reasons = _stale_reasons(previous, source_sha256, drawing_version, snapshot)
        if reasons:
            anchor_mod.mark_stale(project_id, reasons, actor=actor)
    user_entry = store.append_session_event(project_id, {
        "kind": "user", "source": "shell", "stage": "drawing",
        "key": "flow:user:%s" % wanted,
        "text": str(prompt or "解析图纸并提取包装结构和需求字段")})
    anchor_mod.refresh(project_id, {}, actor=actor)
    return {"run_id": wanted, "user_entry": user_entry,
            "anchor": anchor_mod.current_anchor(project_id)}


def _card_status(status: str) -> str:
    if status in ("pending", "running", "completed"):
        return status
    if status in ("failed", "unavailable"):
        return "failed"
    return "completed"


def _flow_status(flow: Dict[str, Any]) -> str:
    index = _steps_index(flow)
    rows = [index.get(step_id) or {} for step_id in model.STEP_IDS]
    if any(str(row.get("status")) in model.STEP_TERMINAL_FAILURES for row in rows):
        return "failed"
    if all(str(row.get("status")) in ("completed", "skipped", "blocked") for row in rows):
        return "completed"
    if any(str(row.get("status")) == "running" for row in rows):
        return "running"
    return "pending"


def _context(project_id: str, step_id: str, flow: Dict[str, Any], *, run_id: str,
             actor: str, resolver: Callable[[str], Optional[Any]]) -> Dict[str, Any]:
    meta = store.load_meta(project_id) or {}
    context = {"project_id": project_id, "run_id": run_id, "actor": actor,
               "resolve": resolver, "flow": flow,
               "filename": str(meta.get("source_filename") or ""),
               "content": _source_bytes(project_id, meta),
               "drawing_version": model._as_int(meta.get("input_revision"), 1),
               "anchor": anchor_mod.current_anchor(project_id)}
    if step_id == "field_write":
        cached = flow.get("_semantics")
        context["semantics"] = cached if isinstance(cached, dict) else None
    return context


def _execute_step(project_id: str, step_id: str, *, run_id: str, actor: str,
                  resolver: Callable[[str], Optional[Any]],
                  retry_of: str = "") -> Dict[str, Any]:
    flow = persistence.load_flow(project_id) or {}
    record = _steps_index(flow).get(step_id) or {}
    key = str(record.get("key") or ("flow:%s:%s" % (run_id, step_id)))
    is_retry = bool(retry_of) and str(retry_of) == key
    previous_attempt = model._as_int(record.get("attempt"), 0)
    attempt = previous_attempt + 1 if is_retry else max(1, previous_attempt)
    started_at = steps_mod.now_iso()
    store.append_session_event(project_id, {
        "kind": "task", "source": "flow", "stage": "drawing", "key": key,
        "task": {"id": key, "label": model.STEP_TITLES[step_id], "status": "running",
                 "steps": [], "error": ""}})
    context = _context(project_id, step_id, flow, run_id=run_id, actor=actor,
                       resolver=resolver)
    try:
        outcome = steps_mod.run(step_id, context)
    except model.DrawingFlowError as exc:
        outcome = {"status": "failed", "error_code": exc.stable_error_code,
                   "error_message": exc.message, "retryable": exc.retryable, "detail": {}}
    except Exception as exc:
        outcome = {"status": "failed", "error_code": "PACKAGING_FLOW_STEP_FAILED",
                   "error_message": "该步骤执行失败，请重试", "retryable": True,
                   "detail": {"reason": type(exc).__name__}}
    status = str(outcome.get("status") or "failed")
    detail = model.jsonable(outcome.get("detail") or {})
    if outcome.get("progress"):
        detail["progress"] = str(outcome.get("progress"))
    message = str(outcome.get("error_message") or "")
    stored = store.append_session_event(project_id, {
        "kind": "task", "source": "flow", "stage": "drawing", "key": key,
        "task": {"id": key, "label": model.STEP_TITLES[step_id],
                 "status": _card_status(status), "error": message}})
    now = steps_mod.now_iso()
    record = {"step_id": step_id, "title": model.STEP_TITLES[step_id], "status": status,
              "attempt": attempt, "key": key, "seq": model._as_int(stored.get("seq")),
              "started_at": started_at, "finished_at": now,
              "error_code": str(outcome.get("error_code") or ""),
              "error_message": message,
              "retryable": bool(outcome.get("retryable")),
              "detail": detail}
    flow = persistence.load_flow(project_id) or {}
    index = _steps_index(flow)
    index[step_id] = record
    flow["steps"] = [index[item] for item in model.STEP_IDS if item in index]
    flow["run_id"] = run_id
    if isinstance(outcome.get("fields"), dict):
        flow["fields"] = model.jsonable(outcome["fields"])
    if isinstance(outcome.get("pending"), dict):
        flow["pending"] = model.jsonable(outcome["pending"])
    if isinstance(outcome.get("downstream"), dict):
        flow["downstream"] = model.jsonable(outcome["downstream"])
    if isinstance(outcome.get("gates"), dict):
        flow["gates"] = model.jsonable(outcome["gates"])
    if step_id == "packaging_semantics" and isinstance(outcome.get("semantics"), dict):
        flow["_semantics"] = model.jsonable(outcome["semantics"])
    updates = outcome.get("anchor_updates")
    anchor_mod.refresh(project_id, updates if isinstance(updates, dict) else {},
                       actor=actor)
    if step_id == "field_write":
        flow["requirement_snapshot_version_after"] = anchor_mod.requirement_snapshot_version(
            project_id)
    flow["status"] = _flow_status(flow)
    if flow["status"] in ("completed", "failed"):
        flow["finished_at"] = now
    persistence.save_flow(project_id, flow)
    return record


def run_step(project_id: str, step_id: str, *, actor: str = "system", run_id: str = "",
             deps: Any = None, retry_of: str = "") -> Dict[str, Any]:
    if step_id not in model.STEP_IDS:
        raise ValueError("未知的步骤：%s" % step_id)
    resolver = _make_resolver(deps)
    flow = persistence.load_flow(project_id)
    if not isinstance(flow, dict) or not str(flow.get("run_id") or ""):
        started = start(project_id, prompt="", actor=actor)
        run_id = started["run_id"]
    run_id = str(run_id or (flow or {}).get("run_id") or "")
    record = _execute_step(project_id, step_id, run_id=run_id, actor=actor,
                           resolver=resolver, retry_of=retry_of)
    if str(record.get("status")) == "completed" and retry_of:
        for later in model.iter_step_ids(step_id):
            if later == step_id:
                continue
            current = persistence.load_flow(project_id) or {}
            status = str((_steps_index(current).get(later) or {}).get("status") or "pending")
            if status != "pending":
                continue
            attempt = _execute_step(project_id, later, run_id=run_id, actor=actor,
                                    resolver=resolver)
            if str(attempt.get("status")) in model.STEP_TERMINAL_FAILURES:
                break
    final = persistence.load_flow(project_id) or {}
    return dict(_steps_index(final).get(step_id) or record)


def run_flow(project_id: str, *, prompt: Any = "", actor: str = "system",
             run_id: str = "", deps: Any = None) -> Dict[str, Any]:
    resolver = _make_resolver(deps)
    started = start(project_id, prompt=prompt, actor=actor, run_id=run_id)
    current_run = started["run_id"]
    for step_id in model.STEP_IDS:
        flow = persistence.load_flow(project_id) or {}
        status = str((_steps_index(flow).get(step_id) or {}).get("status") or "pending")
        if status in ("completed", "skipped", "blocked"):
            continue
        record = _execute_step(project_id, step_id, run_id=current_run, actor=actor,
                               resolver=resolver)
        if str(record.get("status")) in model.STEP_TERMINAL_FAILURES:
            break
    return flow_state(project_id)


__all__ = [
    "ANCHOR_VERSION", "DEPENDENCIES", "FIELD_BOARD_STATES", "FLOW_VERSION",
    "GATE_STAGES", "STALE_REASONS", "STALE_VERSION", "STEP_IDS", "STEP_STATUSES",
    "STEP_TITLES", "capability", "clear_downstream_stale", "current_anchor",
    "flow_state", "gates", "inheritance", "mark_downstream_stale", "migrate",
    "require_gate", "requirement_snapshot_version", "run_flow", "run_id_for",
    "run_step", "stale_view", "start", "steps", "summarize",
]
