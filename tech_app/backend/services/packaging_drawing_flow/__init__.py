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

# 需求单的「可编辑状态」只有一处事实源（`requirement_service`），这里按同一份判断报前置条件：
# 已提交的需求不许被图纸解析静默改写，重试同一个入口必然再失败（Spec
# `drawing-flow-non-editable-requirement.md` §2/§4）。
from ..requirement_service import EDITABLE_STATUSES, REQUIREMENT_NOT_EDITABLE

from . import anchor as anchor_mod
from . import gates as gates_mod
from . import model
from . import persistence
from . import steps as steps_mod

#: "这一次读不到需求单"的稳定码（Spec `packaging-preconditions-requirement-read-failure.md` §2.1）：
#: 与"这个项目真的没有需求单"（`REQUIREMENT_DRAFT_MISSING`）必须是两条码、两句文案。
REQUIREMENT_UNREADABLE = "REQUIREMENT_UNREADABLE"

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
    "packaging_parts": "tech_app.backend.services.packaging_parts",
    "packaging_route": "tech_app.backend.services.packaging_route",
    "packaging_cost": "tech_app.backend.services.packaging_cost",
}

_CACHE: Dict[str, Any] = {}


def _dependency(name: str) -> Optional[Any]:
    """依赖缝：解析不到就返回 None（不许把 AttributeError 抛给用户）。

    Spec `packaging-flow-dependency-probe-truth.md` §2.2：**失败不许进缓存**（一次失败不许被
    钉死到进程结束，装好依赖 / 热修模块后同一进程必须能恢复），失败要登记 `missing` /
    `import_failed` + 异常类名 + 原文前 200 字。对外的返回口径逐字不变。
    """
    key = str(name or "")
    if key in _CACHE:
        return _CACHE[key]
    path = _MODULE_PATHS.get(key)
    if not path:                                        # 这个部署里就没有这个缝
        model.note_dependency_state(key, "missing")
        return None
    try:
        module = importlib.import_module(path)
    except Exception as exc:                            # noqa: BLE001 - 真因要登记，不许吞
        model.note_dependency_state(key, "import_failed", reason=type(exc).__name__,
                                    message=str(exc))
        return None
    model.note_dependency_state(key, "ok")
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


#: 自检必须"真的导得进来"的四项（Spec §2.3）：文件在 ≠ 装载得成。
REQUIRED_DEPENDENCIES = ("file_preflight", "cad_converter", "cad_ir", "packaging_semantics")


def capability() -> Dict[str, Any]:
    """部署自检入口（Spec `packaging-flow-dependency-probe-truth.md` §2.3）。

    既有 `dependencies`（逐名 `bool`，`find_spec` 口径）**逐字不变**；新增
    `dependencies_state`，必需四项的 `ok` 判定改走**真的导入**（文件在但装载失败时不许说"已就绪"）。
    """
    dependencies = {name: _available(name) for name in model.DEPENDENCIES}
    # 必需四项真的导入一次（成功的进 `_CACHE`，失败的登记 import_failed 且不进缓存）。
    unresolved = [name for name in REQUIRED_DEPENDENCIES if _dependency(name) is None]
    dependencies_state = {name: model.dependency_state(name) for name in model.DEPENDENCIES}
    import_failed = [name for name in unresolved
                     if (dependencies_state.get(name) or {}).get("state") == "import_failed"]
    missing = [name for name in unresolved if name not in import_failed]
    if import_failed:
        # 装载失败与"这个部署没有它"必须分家说：前者要重启/看日志，后者要去装依赖。
        message = "依赖装载失败：%s，请查看服务日志后重启服务" % "、".join(
            "%s（%s）" % (name, (dependencies_state.get(name) or {}).get("reason") or "未知异常")
            for name in import_failed)
    elif missing:
        message = "图纸解析链路依赖的能力尚未就绪：%s" % "、".join(missing)
    else:
        message = "图纸解析链路编排层已就绪"
    return {"available": not unresolved,
            "version": model.FLOW_VERSION,
            "steps": model.steps(),
            "dependencies": dependencies,
            "dependencies_state": dependencies_state,
            "message": message}


def steps() -> List[Dict[str, Any]]:
    return model.steps()


#: 依赖状态登记体（Spec §2.1）：与其它 `model` 常量同一处方言。
dependency_state = model.dependency_state


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


#: 零件提取那一步对外承诺的 detail 键闭集（Spec `packaging-dwg-parts-extraction.md` C6）。
#: 前端只按这张表读，缺的在这儿补齐默认值 —— 旧 run 里没有这一步时也拿到同一形状，
#: 不用在每个消费点各写一遍 `or 0`。
PARTS_DETAIL_DEFAULTS = {"parts_id": "", "parts_hash": "", "parts_total": 0,
                         "filtered_total": 0, "truncated": 0, "unavailable": []}


def _step_detail(step_id: str, detail: Any) -> Dict[str, Any]:
    row = model.jsonable(detail or {})
    row = row if isinstance(row, dict) else {}
    if step_id == "parts_extract":
        for key, default in PARTS_DETAIL_DEFAULTS.items():
            row.setdefault(key, list(default) if isinstance(default, list) else default)
    return row


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
        row["detail"] = _step_detail(step_id, row.get("detail"))
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


#: 源文件这一趟到底是哪一态（Spec `packaging-drawing-source-read-failure.md` §2.1）：
#: `blob`（读到了，含内容真的是空的）/ `none`（meta 里没有 `source_path` —— 这个项目
#: 确实还没有源附件）/ `unavailable`（blob 通道读不到，`reason` 给异常类名）。
SOURCE_CONTENT_STATES = ("blob", "none", "unavailable")


def _source_bytes_detail(project_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    """读这一版的源文件字节 **+ 三态披露**（Spec §2.1）。

    为什么必须三态可分：`b""` 今天有两个来源 —— "这个项目还没有源附件"与"blob 读不到"。
    两者都会被 `start()` 折成 `sha256(b"")` 这个**看起来完全合法**的常量写进锚点，之后
    所有 stale 比对都会说"源文件没变"，而第 1 步会把"读不到"报成"上传的是空文件，请重新上传"
    （重传救不了它）。`_source_bytes()` 的返回类型仍是 `bytes`（既有调用方逐字不变），
    三态只有这一个来源。
    """
    name = str((meta or {}).get("source_path") or "")
    if not name:
        return {"content": b"", "source": "none", "reason": ""}
    try:
        data = store._blob().get_bytes("%s/%s" % (project_id, name))
    except Exception as exc:                              # noqa: BLE001 - 读不到要披露，不许抛给调用方
        return {"content": b"", "source": "unavailable", "reason": type(exc).__name__}
    content = bytes(data) if isinstance(data, (bytes, bytearray)) else b""
    return {"content": content, "source": "blob", "reason": ""}


def _source_bytes(project_id: str, meta: Dict[str, Any]) -> bytes:
    """这一版的源文件字节（返回类型仍是 `bytes`；要三态请用 `_source_bytes_detail()`）。"""
    return _source_bytes_detail(project_id, meta)["content"]


def _source_disclosure(detail: Dict[str, Any]) -> Dict[str, Any]:
    """读不到时的披露形状（Spec §2.1）：`{}` / `{"code", "reason"}`。"""
    if str((detail or {}).get("source") or "") != "unavailable":
        return {}
    return {"code": "drawing_source_unavailable",
            "reason": str((detail or {}).get("reason") or "")}


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
    source = _source_bytes_detail(project_id, meta)
    read_state = str(source.get("source") or "blob")
    # 只有**真的读到内容**才允许算哈希（Spec §2.1）：`sha256(b"")` 是一个形状合法的常量，
    # 写进锚点会让"这一次读不到"与"源文件没变"同形；读不到 / 确实没有都给 `""`。
    source_sha256 = (hashlib.sha256(source["content"]).hexdigest()
                     if read_state == "blob" else "")
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
                              "source_content": read_state,
                              "source_content_unavailable": _source_disclosure(source),
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
    source = _source_bytes_detail(project_id, meta)
    context = {"project_id": project_id, "run_id": run_id, "actor": actor,
               "resolve": resolver, "flow": flow,
               "filename": str(meta.get("source_filename") or ""),
               "content": source["content"],
               # 三态披露（Spec `packaging-drawing-source-read-failure.md` §2.1）：`content`
               # 仍是 `bytes`（该给 `b""` 仍给 `b""`），只是把它**是哪一态**一并交出去 ——
               # 否则第 1 步只能拿空字节去判格式，把"读不到"说成"文件是空的"。
               "content_source": str(source.get("source") or "blob"),
               "content_unavailable": _source_disclosure(source),
               "drawing_version": model._as_int(meta.get("input_revision"), 1),
               "anchor": anchor_mod.current_anchor(project_id)}
    if step_id in ("field_write", "parts_extract"):
        # 语义文档在 packaging_semantics 那一步算一次、缓存进 flow：字段写入与零件提取
        # 都读同一份，绝不为了拿角色再算一遍。
        cached = flow.get("_semantics")
        context["semantics"] = cached if isinstance(cached, dict) else None
        context["ir"] = flow.get("_ir") if isinstance(flow.get("_ir"), dict) else None
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
    if step_id == "cad_ir_parse" and isinstance(outcome.get("ir"), dict):
        flow["_ir"] = model.jsonable(outcome["ir"])
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


def preconditions(project_id: str) -> List[Dict[str, Any]]:
    """跑链路**之前**就能知道缺什么：`[{code, severity, message, action, unavailable}]`。

    只读、幂等、不写库、不建数据（Spec `drawing-flow-error-taxonomy.md` C3）。
    缺需求草稿时前端可以先提示"去哪建草稿"，而不是让用户跑完整条链路才看到
    「字段写入失败，请重试」——那句重试是永远不会成功的。
    """
    items: List[Dict[str, Any]] = []
    if not str(project_id or "").strip():
        return items
    try:
        requirement = store.load_requirement(str(project_id))
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不抛给调用方
        # "存储通道暂时读不到" ≠ "这个项目没有需求单"（Spec §2.1）：前者让用户重试，
        # 后者让用户去建草稿 —— 说反了就会建出一张重复的需求单。
        reason = type(exc).__name__
        items.append({"code": REQUIREMENT_UNREADABLE, "severity": "blocking",
                      # 文案红线（Spec §4 Q2）：说得出异常类名与"重试"，且**不含**"不存在"
                      # —— "不存在"只留给真的没有需求单那条码（见 Spec §5.3 记录的偏差）。
                      "message": "暂时读不到这个项目的需求单（%s），请稍后重试；"
                                 "这不代表该需求单缺失，请勿据此新建需求草稿" % reason,
                      "action": "稍后重试；若持续失败请让管理员检查存储通道",
                      "unavailable": {"code": "requirement_unreadable", "reason": reason}})
        return items
    if not requirement:
        spec = model.PRECONDITION_BLOCKERS["REQUIREMENT_DRAFT_MISSING"]
        items.append({"code": "REQUIREMENT_DRAFT_MISSING", "severity": "blocking",
                      "message": str(spec["message"]), "action": str(spec["action"]),
                      "unavailable": {}})
        return items
    status = str(requirement.get("status") or "").strip()
    if status and status not in EDITABLE_STATUSES:
        spec = model.PRECONDITION_BLOCKERS[REQUIREMENT_NOT_EDITABLE]
        items.append({"code": REQUIREMENT_NOT_EDITABLE, "severity": "blocking",
                      "message": "需求已提交（当前状态：%s），不能直接改写；请先退回草稿"
                                 "或为该项目新建一张需求草稿（缺前置条件，重试不会成功）" % status,
                      "action": str(spec["action"]), "unavailable": {}})
    return items


__all__ = [
    "ANCHOR_VERSION", "DEPENDENCIES", "FIELD_BOARD_STATES", "FLOW_VERSION",
    "GATE_STAGES", "STALE_REASONS", "STALE_VERSION", "STEP_IDS", "STEP_STATUSES",
    "STEP_TITLES", "capability", "clear_downstream_stale", "current_anchor",
    "flow_state", "gates", "inheritance", "mark_downstream_stale", "migrate",
    "preconditions",
    "require_gate", "requirement_snapshot_version", "run_flow", "run_id_for",
    "run_step", "stale_view", "start", "steps", "summarize",
]
