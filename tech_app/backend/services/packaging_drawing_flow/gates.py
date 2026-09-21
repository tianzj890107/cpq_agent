"""门禁矩阵（第 5 批 Spec §5）：哪些字段人工确认后才能做哪一段下游。"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from tech_app.backend.storage import store

from . import anchor as anchor_mod
from . import model, persistence

Resolver = Callable[[str], Optional[Any]]

#: 每段的必需字段（有序，与 Spec §5.1 逐格一致）。
GATE_REQUIRES: Dict[str, Tuple[str, ...]] = {
    "box_match": ("inner_length", "inner_width", "inner_height", "closure_type"),
    "bom": ("inner_length", "inner_width", "inner_height", "face_paper_gsm"),
    "route": ("closure_type", "v_groove", "face_paper_gsm"),
    "cost": ("quote_quantity",),
    "quote_draft": (),
    "quote_publish": ("inner_length", "inner_width", "inner_height", "closure_type"),
}

BLOCKING_CODES = ("field_missing", "field_unconfirmed", "field_conflict", "unit_unconfirmed",
                  "box_match_not_confirmed", "bom_not_built", "route_not_confirmed",
                  "cost_not_built", "cost_gaps_unresolved", "minimum_charge_policy_unresolved")

_FIELD_CODES = ("field_missing", "field_unconfirmed", "field_conflict")


def _requirement(project_id: str) -> Dict[str, Any]:
    doc = store.load_requirement(project_id) or {}
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    return {"data": data or {},
            "sources": (data or {}).get("field_sources") or {},
            "provenance": (data or {}).get("field_provenance") or {}}


def _has_value(data: Dict[str, Any], field: str) -> bool:
    return data.get(field) not in (None, "", [], {})


def _is_confirmed(data: Dict[str, Any], provenance: Dict[str, Any], sources: Dict[str, Any],
                  field: str) -> bool:
    """这个字段算不算"已确认"（Spec `packaging-manual-field-confirmation.md` §2.1）。

    两条**互相独立**的证据路径，**任一**成立即可 —— 原来写成"与"，于是两条路各自都走不通：
    人工填的字段卡在 `status`（几何看板给的是 missing），图纸已确认的字段卡在来源/来源口径。

      ① 图纸 / 模型证据已确认：`status == "confirmed"`；
      ② 人工录入 / 人工确认**且当前有值**：`data[field]` 有值 且
         （`field_sources == "manual"` 或 `origin == "user_confirmed"`）。

    拒绝口径一个字没放宽：值为空先走 `field_missing`（值判定在 `_field_blocking` 里排在前面）、
    冲突证据先走 `field_conflict`（同样排在前面）。
    """
    row = provenance.get(field) if isinstance(provenance.get(field), dict) else {}
    status = str(row.get("status") or "")
    origin = str(row.get("origin") or "")
    source = str(sources.get(field) or "")
    if status == "confirmed":                                   # ① 图纸/模型证据
        return True
    return _has_value(data, field) and (source == "manual" or origin == "user_confirmed")  # ② 人工


def _field_blocking(requires: Tuple[str, ...], requirement: Dict[str, Any],
                    boards: Dict[str, Any]) -> List[Dict[str, Any]]:
    data, provenance, sources = requirement["data"], requirement["provenance"], requirement["sources"]
    rows: List[Dict[str, Any]] = []
    for field in requires:
        board = str((boards.get(field) or {}).get("board") or "")
        row = provenance.get(field) if isinstance(provenance.get(field), dict) else {}
        if board == "conflict" or str(row.get("status") or "") == "conflict":
            rows.append({"code": "field_conflict", "field": field,
                         "message": "%s存在冲突证据，需要人工裁定后才能继续" % model.label_of(field),
                         "source": "field_provenance"})
            continue
        if not _has_value(data, field):
            rows.append({"code": "field_missing", "field": field,
                         "message": "缺少%s，请先补全后再进行该步骤" % model.label_of(field),
                         "source": "requirement"})
            continue
        if not _is_confirmed(data, provenance, sources, field):
            rows.append({"code": "field_unconfirmed", "field": field,
                         "message": "%s尚未确认，确认后才能进行该步骤" % model.label_of(field),
                         "source": "field_provenance"})
    return rows


def _engine(resolve: Resolver, name: str, function: str, *args: Any) -> Dict[str, Any]:
    module = resolve(name) if callable(resolve) else None
    fn = getattr(module, function, None) if module is not None else None
    if not callable(fn):
        return {}
    try:
        row = fn(*args)
    except Exception:
        return {}
    return dict(row) if isinstance(row, dict) else {}


def _policy(resolve: Resolver) -> Dict[str, Any]:
    module = resolve("packaging_cost") if callable(resolve) else None
    fn = getattr(module, "minimum_charge_policy", None) if module is not None else None
    if not callable(fn):
        return {}
    try:
        row = fn()
    except Exception:
        return {}
    return dict(row) if isinstance(row, dict) else {}


def _stage_entry(stage: str, project_id: str, resolve: Resolver,
                 snapshot: str) -> Dict[str, Any]:
    requirement = _requirement(project_id)
    flow = persistence.load_flow(project_id) or {}
    boards = flow.get("fields") if isinstance(flow.get("fields"), dict) else {}
    requires = GATE_REQUIRES[stage]
    blocking: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if stage != "quote_draft":
        blocking.extend(_field_blocking(requires, requirement, boards))
    unit_status = str(anchor_mod.current_anchor(project_id).get("unit_status") or "")
    if stage == "box_match" and unit_status and unit_status != "confirmed":
        blocking.append({"code": "unit_unconfirmed", "field": "inner_width",
                         "message": "图纸未声明单位，长宽高不能按毫米确认"})
    if stage in ("bom", "quote_publish"):
        box = _engine(resolve, "packaging_match", "load_box_match", project_id)
        if str(box.get("decision") or "") != "confirmed":
            blocking.append({"code": "box_match_not_confirmed",
                             "message": "盒型尚未确认，确认后才能进行该步骤",
                             "source": "packaging_match"})
    if stage == "route":
        bom = _engine(resolve, "packaging_bom", "load_bom", project_id)
        if not bom.get("built"):
            blocking.append({"code": "bom_not_built", "message": "BOM 尚未生成，生成后才能排工艺路线",
                             "source": "packaging_bom"})
    if stage == "cost":
        route = _engine(resolve, "packaging_route", "load_route", project_id)
        if str(route.get("status") or "") != "confirmed":
            blocking.append({"code": "route_not_confirmed",
                             "message": "工艺路线尚未确认，确认后才能测算成本",
                             "source": "packaging_route"})
    if stage == "quote_publish":
        cost = _engine(resolve, "packaging_cost", "load_cost", project_id)
        if not cost.get("built"):
            blocking.append({"code": "cost_not_built", "message": "成本尚未测算，测算后才能生成正式报价",
                             "source": "packaging_cost"})
        elif cost.get("has_gaps"):
            blocking.append({"code": "cost_gaps_unresolved",
                             "message": "成本仍存在缺口，缺口清零后才能生成正式报价",
                             "source": "packaging_cost"})
        policy = _policy(resolve)
        if str(policy.get("status") or "") != "chosen":
            blocking.append({"code": "minimum_charge_policy_unresolved",
                             "message": "最低收费口径尚未裁决，暂不能生成正式报价（可先存草稿）",
                             "source": "packaging_cost"})
    if stage == "quote_draft":
        cost = _engine(resolve, "packaging_cost", "load_cost", project_id)
        if not cost.get("built"):
            warnings.append({"code": "cost_not_built", "message": "成本尚未测算，草稿里暂不体现成本"})
        elif cost.get("has_gaps"):
            warnings.append({"code": "cost_gaps_unresolved",
                             "message": "成本存在缺口，草稿会随包带出这些缺口"})
    return {"stage": stage, "status": "blocked" if blocking else "open",
            "requires": list(requires), "blocking": blocking, "warnings": warnings,
            "snapshot": {"requirement_snapshot_version": snapshot}}


def build(project_id: str, *, resolve: Resolver = None, stage: str = "") -> Dict[str, Any]:
    if stage and stage not in model.GATE_STAGES:
        raise ValueError("未知的门禁段：%s" % stage)
    snapshot = anchor_mod.requirement_snapshot_version(project_id)
    stages = {name: _stage_entry(name, project_id, resolve, snapshot) for name in model.GATE_STAGES}
    if stage:
        return stages[stage]
    return {"project_id": project_id,
            "flow_version": model.FLOW_VERSION,
            "requirement_snapshot_version": snapshot,
            "stages": stages}


def blocking_message(entry: Dict[str, Any]) -> str:
    """门禁文案：只给一处结论，短且不换行（红测 G18/C15）。"""
    blocking = (entry or {}).get("blocking") or []
    fields = [str(item.get("field") or "") for item in blocking
              if str(item.get("code") or "") in _FIELD_CODES and str(item.get("field") or "")]
    if fields:
        labels = "、".join(model.label_of(field) for field in fields[:4])
        return "需求字段尚未确认（%s），请先确认后再进行该步骤" % labels
    for item in blocking:
        message = str(item.get("message") or "")
        if message:
            return message
    return "当前条件尚不满足，请先补齐前置步骤"


def require(project_id: str, stage: str, *, resolve: Resolver = None) -> Dict[str, Any]:
    if stage not in model.GATE_STAGES:
        raise ValueError("未知的门禁段：%s" % stage)
    entry = build(project_id, resolve=resolve, stage=stage)
    if entry.get("status") == "blocked":
        http_status, retryable = model.error_meta("PACKAGING_GATE_BLOCKED")
        raise model.DrawingFlowError("PACKAGING_GATE_BLOCKED", http_status, retryable,
                                     blocking_message(entry))
    return entry
