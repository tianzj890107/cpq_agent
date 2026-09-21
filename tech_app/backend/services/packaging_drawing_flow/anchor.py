"""版本锚点、需求快照版本与 stale 传播（第 5 批 Spec §6）。

这里只读需求单与依赖缝的既有读接口，绝不改写下游正文：stale 只做标注。
"""
from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Optional

from tech_app.backend.storage import store

from . import model, persistence

Resolver = Callable[[str], Optional[Any]]

#: 需求快照里要排除的内部键（含 `*_json` / `quote_source_*` 前缀）。
_INTERNAL_KEYS = {"field_sources", "field_provenance", "history"}
_INTERNAL_SUFFIXES = ("_json",)
_INTERNAL_PREFIXES = ("quote_source_", "_")

#: 锚点里参与 stale 比对的版本位 → 原因码。
_STALE_KEYS = (("drawing_version", "drawing_version_changed"),
               ("source_sha256", "source_sha256_changed"),
               ("ir_hash", "ir_hash_changed"),
               ("semantics_hash", "semantics_hash_changed"),
               ("converter_version", "converter_version_changed"))


def empty_anchor(project_id: str, run_id: str = "") -> Dict[str, Any]:
    return {
        "anchor_version": model.ANCHOR_VERSION,
        "project_id": project_id,
        "run_id": run_id,
        "drawing_version": 0,
        "source_sha256": "",
        "conversion_id": "",
        "converter_version": "",
        "ir_id": "", "ir_hash": "", "ir_version": "",
        "semantics_id": "", "semantics_hash": "", "semantics_version": "",
        "unit_status": "",
        "requirement_snapshot_version": "",
        "updated_at": "", "updated_by": "",
    }


def _is_internal(key: str) -> bool:
    name = str(key or "")
    if name in _INTERNAL_KEYS or not name:
        return True
    if name.endswith(_INTERNAL_SUFFIXES):
        return True
    return name.startswith(_INTERNAL_PREFIXES)


def requirement_snapshot_version(project_id: str) -> str:
    """只含业务口径的需求快照版本（时间戳 / history 一律不进）。"""
    doc = store.load_requirement(project_id) or {}
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    fields: Dict[str, Any] = {}
    for key, value in (data or {}).items():
        if _is_internal(key) or value in (None, "", [], {}):
            continue
        fields[str(key)] = value
    provenance: Dict[str, Any] = {}
    for key, entry in (data.get("field_provenance") or {}).items():
        row = entry if isinstance(entry, dict) else {}
        provenance[str(key)] = {"origin": str(row.get("origin") or ""),
                                "status": str(row.get("status") or "")}
    waivers = []
    for item in doc.get("waivers") or []:
        row = item if isinstance(item, dict) else {}
        waivers.append({"field": str(row.get("field") or ""),
                        "reason": str(row.get("reason") or "")})
    waivers.sort(key=lambda row: row["field"])
    payload = {
        "status": str(doc.get("status") or ""),
        "fields": fields,
        "provenance": provenance,
        "waivers": waivers,
        "confirmed_by": str(doc.get("confirmed_by") or ""),
        "confirmed_at": str(doc.get("confirmed_at") or ""),
    }
    return "reqsnap/1:" + model.digest16(payload)


def current_anchor(project_id: str) -> Dict[str, Any]:
    anchor = persistence.load_anchor(project_id)
    if not isinstance(anchor, dict):
        return empty_anchor(project_id)
    merged = empty_anchor(project_id, str(anchor.get("run_id") or ""))
    merged.update(anchor)
    return merged


def refresh(project_id: str, updates: Dict[str, Any], *, actor: str = "system") -> Dict[str, Any]:
    """合并版本位；版本真的变了（旧值非空且不同）才标 stale（Spec §6.3）。"""
    previous = current_anchor(project_id)
    merged = dict(previous)
    for key, value in (updates or {}).items():
        if value is None:
            continue
        merged[key] = value
    merged["anchor_version"] = model.ANCHOR_VERSION
    merged["project_id"] = project_id
    merged["requirement_snapshot_version"] = requirement_snapshot_version(project_id)
    merged["updated_at"] = _now()
    merged["updated_by"] = str(actor or "system")
    persistence.save_anchor(project_id, merged)
    reasons = [reason for key, reason in _STALE_KEYS if _changed(previous, merged, key)]
    if reasons:
        mark_stale(project_id, reasons, actor=actor)
    return merged


def _changed(previous: Dict[str, Any], merged: Dict[str, Any], key: str) -> bool:
    old = str(previous.get(key) or "")
    new = str(merged.get(key) or "")
    return bool(old) and old != new


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def _downstream_defaults(flag: bool = False) -> Dict[str, bool]:
    return {stage: flag for stage in ("box_match", "bom", "route", "cost", "quote_draft")}


def stale_document(reasons: List[str], *, anchor: Optional[dict] = None) -> Dict[str, Any]:
    return {"stale_version": model.STALE_VERSION,
            "stale": True,
            "reasons": sorted({str(item) for item in reasons if str(item)}),
            "since": _now(),
            "anchor": dict(anchor or {}),
            "downstream": _downstream_defaults(True)}


def mark_stale(project_id: str, reasons: List[str], *, actor: str = "system") -> Dict[str, Any]:
    wanted = [str(item) for item in reasons or []]
    unknown = [item for item in wanted if item not in model.STALE_REASONS]
    if unknown:
        raise ValueError("未知的 stale 原因：%s" % "、".join(sorted(set(unknown))))
    previous = stale_view(project_id)
    merged_reasons = sorted(set((previous.get("reasons") or [])) | set(wanted))
    document = stale_document(merged_reasons, anchor=current_anchor(project_id))
    document["downstream"] = dict(previous.get("downstream") or _downstream_defaults(False))
    for stage in document["downstream"]:
        document["downstream"][stage] = True
    persistence.save_stale(project_id, document)
    store.audit(project_id, "packaging_flow.downstream_stale",
                {"by": str(actor or "system"), "reasons": merged_reasons})
    return stale_view(project_id)


def clear_stale(project_id: str, stage: str, *, actor: str = "system") -> Dict[str, Any]:
    if stage not in _downstream_defaults(False):
        raise ValueError("未知的下游段：%s" % stage)
    previous = stale_view(project_id)
    downstream = dict(previous.get("downstream") or _downstream_defaults(False))
    downstream[stage] = False
    document = {"stale_version": model.STALE_VERSION,
                "stale": any(downstream.values()),
                "reasons": list(previous.get("reasons") or []),
                "since": str(previous.get("since") or ""),
                "anchor": current_anchor(project_id),
                "downstream": downstream}
    persistence.save_stale(project_id, document)
    store.audit(project_id, "packaging_flow.downstream_stale_cleared",
                {"by": str(actor or "system"), "stage": stage})
    return stale_view(project_id)


def stale_view(project_id: str) -> Dict[str, Any]:
    document = persistence.load_stale(project_id)
    if not isinstance(document, dict):
        return {"stale_version": model.STALE_VERSION, "stale": False, "reasons": [],
                "since": "", "anchor": current_anchor(project_id),
                "downstream": _downstream_defaults(False)}
    return {"stale_version": str(document.get("stale_version") or model.STALE_VERSION),
            "stale": bool(document.get("stale")),
            "reasons": list(document.get("reasons") or []),
            "since": str(document.get("since") or ""),
            "anchor": dict(document.get("anchor") or current_anchor(project_id)),
            "downstream": dict(_downstream_defaults(False),
                               **(document.get("downstream") or {}))}


def _engine(resolve: Resolver, name: str) -> Optional[Any]:
    try:
        return resolve(name) if callable(resolve) else None
    except Exception:
        return None


def _load(resolve: Resolver, name: str, function: str, project_id: str, requirement_no: str = "") -> Dict[str, Any]:
    module = _engine(resolve, name)
    fn = getattr(module, function, None) if module is not None else None
    if not callable(fn):
        return {}
    try:
        row = fn(project_id, requirement_no) if requirement_no else fn(project_id)
    except TypeError:
        row = fn(project_id)
    except Exception:
        return {}
    return dict(row) if isinstance(row, dict) else {}


def _load_list(resolve: Resolver, name: str, function: str, project_id: str) -> List[Any]:
    module = _engine(resolve, name)
    fn = getattr(module, function, None) if module is not None else None
    if not callable(fn):
        return []
    try:
        rows = fn(project_id)
    except Exception:
        return []
    return list(rows) if isinstance(rows, (list, tuple)) else []


def stage_chain(project_id: str, resolve: Resolver = None, *, stage: str = "") -> List[Dict[str, Any]]:
    """上一段的版本必须传进下一段（Spec §6.1）。"""
    box = _load(resolve, "packaging_match", "load_box_match", project_id)
    bom = _load(resolve, "packaging_bom", "load_bom", project_id)
    route = _load(resolve, "packaging_route", "load_route", project_id)
    versions = _load_list(resolve, "packaging_route", "route_versions", project_id)
    cost = _load(resolve, "packaging_cost", "load_cost", project_id)
    cost_module = _engine(resolve, "packaging_cost")
    result_version = ""
    result_version_of = getattr(cost_module, "result_version_of", None) if cost_module else None
    if callable(result_version_of) and cost:
        try:
            result_version = str(result_version_of(cost))
        except Exception:
            result_version = ""
    version_row = {}
    if isinstance(versions, dict):
        version_row = versions
    elif isinstance(versions, list) and versions:
        version_row = versions[-1] if isinstance(versions[-1], dict) else {}
    rows: List[Dict[str, Any]] = []
    if str(stage or "") in ("bom", "route", "cost", "quote_draft", "quote_publish", ""):
        rows.append({"stage": "box_match",
                     "value": str(box.get("confirmed_box_type") or ""),
                     "status": str(box.get("decision") or "none"),
                     "confirmed_by": str(box.get("confirmed_by") or ""),
                     "confirmed_at": str(box.get("confirmed_at") or ""),
                     "engine_version": str(box.get("engine_version") or "")})
    if str(stage or "") in ("route", "cost", "quote_draft", "quote_publish", ""):
        rows.append({"stage": "bom",
                     "value": str(bom.get("generated_at") or ""),
                     "status": "built" if bom.get("built") else "none",
                     "engine_version": str(bom.get("engine_version") or "")})
    if str(stage or "") in ("cost", "quote_draft", "quote_publish", ""):
        rows.append({"stage": "route",
                     "value": str(version_row.get("version") or ""),
                     "status": str(version_row.get("status") or route.get("status") or ""),
                     "engine_version": str(route.get("engine_version") or "")})
    if str(stage or "") in ("quote_draft", "quote_publish", ""):
        rows.append({"stage": "cost",
                     "value": result_version,
                     "status": "built" if cost.get("built") else "none",
                     "engine_version": str(cost.get("engine_version") or "")})
    return rows


def unresolved_gaps(project_id: str, resolve: Resolver = None) -> List[Dict[str, Any]]:
    flow = persistence.load_flow(project_id) or {}
    gaps: List[Dict[str, Any]] = []
    for key, entry in sorted((flow.get("fields") or {}).items()):
        row = entry if isinstance(entry, dict) else {}
        board = str(row.get("board") or "")
        if board in ("pending", "missing", "conflict"):
            gaps.append({"field": str(key), "reason": board,
                         "status": str(row.get("status") or board),
                         "message": "%s尚未就绪" % model.label_of(key)})
    cost = _load(resolve, "packaging_cost", "load_cost", project_id)
    for item in cost.get("gaps") or []:
        row = item if isinstance(item, dict) else {}
        gaps.append({"field": str(row.get("field") or ""), "reason": "cost_gap",
                     "status": "gap", "code": str(row.get("code") or ""),
                     "message": str(row.get("message") or "成本存在缺口")})
    return gaps


def inheritance(project_id: str, resolve: Resolver = None, *, stage: str = "") -> Dict[str, Any]:
    anchor = current_anchor(project_id)
    drawing_version = model._as_int(anchor.get("drawing_version"), 0)
    chain = stage_chain(project_id, resolve, stage=stage)
    confirmed_by = ""
    confirmed_at = ""
    for row in reversed(chain[:-1] if chain else []):
        if str(row.get("confirmed_by") or ""):
            confirmed_by = str(row.get("confirmed_by") or "")
            confirmed_at = str(row.get("confirmed_at") or "")
            break
    gaps = unresolved_gaps(project_id, resolve)
    payload = {
        "source_drawing_version": drawing_version,
        "source_ir_version": str(anchor.get("ir_version") or ""),
        "requirement_snapshot_version": requirement_snapshot_version(project_id),
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at,
        "unresolved_gaps": gaps,
        "source_ir_hash": str(anchor.get("ir_hash") or ""),
        "source_semantics_version": str(anchor.get("semantics_version") or ""),
        "source_semantics_hash": str(anchor.get("semantics_hash") or ""),
        "stage": str(stage or ""),
        "stage_chain": chain,
    }
    payload["source_versions"] = {
        "drawing_version": drawing_version,
        "ir_version": payload["source_ir_version"],
        "ir_hash": payload["source_ir_hash"],
        "semantics_version": payload["source_semantics_version"],
        "semantics_hash": payload["source_semantics_hash"],
        "requirement_snapshot_version": payload["requirement_snapshot_version"],
        "stage_chain": chain,
    }
    return payload
