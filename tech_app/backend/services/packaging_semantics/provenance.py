"""字段来源 / 可信度 / 冲突 + 写需求看板（DWG 第 4 批 Spec §5.4）。

写盘纪律：
  · 双写 `requirement.data["field_provenance"]` 与既有闭集的 `field_sources`；
  · `status != confirmed` 的字段**绝不**写进 `requirement.data[field]`（未确认不写值）；
  · 用户已确认（`origin == user_confirmed` 或 `field_sources == manual`）**且当前有值** →
    看板显式写成 `origin="user_confirmed"` / `status="confirmed"` / `value=<当前值>`，
    图纸候选只追加进 `alternatives` + `PACKAGING_FIELD_USER_CONFIRMED` 警告，不改值、不降级来源
    （Spec `packaging-manual-field-confirmation.md` §2.2）；
  · 增量写入，且只经 `requirement_service.save_requirement_draft()` 落盘。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from ...models.workflow import RequirementDoc
from .. import requirement_service

#: origin → 既有 `FIELD_SOURCES` 闭集（不新增取值，Spec §5.4）。
SOURCE_MAP = {
    "confirmed_from_cad": "attachment",
    "inferred_from_geometry": "attachment",
    "inferred_from_text": "attachment",
    "inferred_by_model": "ai_recommend",
    "user_confirmed": "manual",
}

PROVENANCE_KEYS = ("origin", "status", "value", "confidence", "evidence_level",
                   "evidence_refs", "conflicts", "alternatives", "ir_id", "ir_hash")

USER_CONFIRMED_WARNING = "PACKAGING_FIELD_USER_CONFIRMED"

#: 稳定错误码：字段写入的**前置条件**缺失（这里=没有需求草稿）。它与"写库真的失败"
#: 是两回事：重试同一入口必然再失败，所以调用方要按 blocked 处理，而不是 failed。
#: 见 docs/specs/drawing-flow-error-taxonomy.md C1/C4。
REQUIREMENT_DRAFT_MISSING = "REQUIREMENT_DRAFT_MISSING"


class RequirementDraftMissing(ValueError):
    """缺需求草稿：没法写入任何字段。带上稳定码，别让调用方靠关键字猜。"""

    stable_error_code = REQUIREMENT_DRAFT_MISSING

    def __init__(self, message: str = "需求单不存在，请先创建需求草稿"):
        super().__init__(message)
        self.message = str(message)


def _entry_snapshot(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {key: candidate.get(key) for key in PROVENANCE_KEYS}


def _alternative(candidate: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = _entry_snapshot(candidate)
    snapshot["source"] = "packaging_semantics"
    return snapshot


def _has_value(value: Any) -> bool:
    """这个值算不算"有值"（Spec 批 12 §3.3）：None / 空白串 / 空容器都算没有。

    `0` 与 `False` **是**值 —— 只认"空"，不认"假"。
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _is_manual_source(previous: Any, source: Any) -> bool:
    """这个字段的来源算不算"人工"（看来源，不看值）。"""
    if isinstance(previous, dict) and str(previous.get("origin") or "") == "user_confirmed":
        return True
    return str(source or "") == "manual"


def _is_user_confirmed(previous: Any, source: Any, value: Any = None) -> bool:
    """人工确认过**且当前真的有值**才算确认（Spec 批 12 §3.3）。

    只看来源不看值会让字段永远空着：34 实测 `data.closure_type = ""` 而
    `field_provenance.closure_type.origin = "user_confirmed"` —— 图纸里读到的「磁吸」
    只进 alternatives，这个字段再也补不上。值为空时不存在"用户确认过的值"，
    必须让图纸证据按正常路径写入；值非空时一个字都不许改（冻结红测 D7）。
    """
    return _is_manual_source(previous, source) and _has_value(value)


def apply_to_requirement(project_id: str, semantics: Dict[str, Any], *,
                         accept: Iterable[str] = (), author: str = "system") -> Dict[str, Any]:
    """把语义文档里的字段候选增量写入需求看板，并返回本次写入的报告。"""
    if not isinstance(semantics, dict):
        raise ValueError("apply_to_requirement() 需要一份语义文档")
    current = requirement_service.store.load_requirement(project_id)
    if not current:
        raise RequirementDraftMissing()

    data: Dict[str, Any] = dict(current.get("data") or {})
    provenance: Dict[str, Any] = dict(data.get("field_provenance") or {})
    sources: Dict[str, str] = dict(data.get("field_sources") or {})
    accepted = {str(item) for item in (accept or ()) if str(item)}

    applied: Dict[str, Any] = {}
    kept: Dict[str, Any] = {}
    warnings: List[Dict[str, Any]] = []

    for key in sorted((semantics.get("fields") or {}).keys()):
        candidate = semantics["fields"].get(key) or {}
        if not isinstance(candidate, dict):
            continue
        previous = provenance.get(key)
        manual_source = _is_manual_source(previous, sources.get(key))
        if _is_user_confirmed(previous, sources.get(key), data.get(key)):
            entry = dict(previous) if isinstance(previous, dict) and previous else _entry_snapshot(candidate)
            alternatives = list(entry.get("alternatives") or [])
            if candidate.get("value") is not None or candidate.get("status") != "missing":
                alternatives.append(_alternative(candidate))
            entry["alternatives"] = alternatives
            # 人工事实为准（Spec `packaging-manual-field-confirmation.md` §2.2）：**显式写死**
            # origin / status / value，不用 `setdefault` —— 候选快照本身带着
            # `status="missing"` / `origin="inferred_from_geometry"`，setdefault 抢不过它，
            # 看板就永远停在 missing（34 实测：值在、来源 manual、看板 missing、门禁 unconfirmed）。
            entry["origin"] = "user_confirmed"
            entry["status"] = "confirmed"
            entry["value"] = data.get(key)
            entry.setdefault("confidence", 1.0)
            provenance[key] = entry
            kept[key] = {"reason": "user_confirmed"}
            _append_warning(warnings, USER_CONFIRMED_WARNING,
                            "字段 %s 已由人工确认，本次图纸证据只追加为备选，不改动当前值" % key)
            continue

        status = str(candidate.get("status") or "missing")
        record = _entry_snapshot(candidate)
        confirmed = status == "confirmed" or key in accepted
        if key in accepted and status != "confirmed":
            record["origin"] = "user_confirmed"
            record["status"] = "confirmed"
        provenance[key] = record

        if confirmed:
            data[key] = candidate.get("value")
            if manual_source:
                # 人工来源、但当前值是空的：图纸证据把值补上，**来源与留痕仍算人工确认**
                # （Spec 批 12 §3.3 第一行）—— 只有空值才走到这里，非空值在前面就 kept 了。
                record["origin"] = "user_confirmed"
                record["status"] = "confirmed"
                provenance[key] = record
                sources[key] = "manual"
            else:
                sources[key] = ("manual" if key in accepted and status != "confirmed"
                                else SOURCE_MAP.get(str(candidate.get("origin") or ""), "attachment"))
            applied[key] = {"origin": record.get("origin"), "value": candidate.get("value")}
        else:
            kept[key] = {"reason": status}

    data["field_provenance"] = provenance
    data["field_sources"] = sources

    doc = RequirementDoc.model_validate({**current, "data": data})
    saved = requirement_service.save_requirement_draft(
        project_id, doc, user={"username": author}, current=current)

    requirement_service.store.audit(project_id, "packaging_semantics.applied", {
        "semantics_id": str(semantics.get("semantics_id") or ""),
        "applied": len(applied), "kept": len(kept), "by": author,
    })
    return {
        "project_id": project_id,
        "semantics_id": str(semantics.get("semantics_id") or ""),
        "data": (saved or {}).get("data") or data,
        "applied": applied,
        "kept": kept,
        "warnings": warnings,
    }


def _append_warning(bucket: List[Dict[str, Any]], code: str, message: str) -> None:
    entry = {"code": code, "message": message, "evidence_refs": []}
    if entry not in bucket:
        bucket.append(entry)


def field_candidates(semantics: Dict[str, Any]) -> Dict[str, Any]:
    """只给「有话说」的字段（missing 的不算候选）。"""
    return {key: entry for key, entry in sorted((semantics.get("fields") or {}).items())
            if isinstance(entry, dict) and entry.get("status") != "missing"}


def summarize(semantics: Dict[str, Any]) -> Dict[str, Any]:
    """第 5 批 Agent 只吃这份摘要：小、无实体明细、无整份证据字典（Spec §2）。"""
    assist = semantics.get("model_assist") or {}
    fields = {key: {"origin": entry.get("origin"), "status": entry.get("status"),
                    "value": entry.get("value"), "confidence": entry.get("confidence")}
              for key, entry in sorted((semantics.get("fields") or {}).items())
              if isinstance(entry, dict)}
    return {
        "semantics_version": semantics.get("semantics_version"),
        "semantics_id": semantics.get("semantics_id"),
        "semantics_hash": semantics.get("semantics_hash"),
        "source": semantics.get("source") or {},
        "stats": semantics.get("stats") or {},
        "roles_summary": semantics.get("roles_summary") or {},
        "fields": fields,
        "unresolved": [{"field": row.get("field"), "reason": row.get("reason"),
                        "status": row.get("status")}
                       for row in (semantics.get("unresolved") or []) if isinstance(row, dict)],
        "box_candidates": [{"candidate_type": row.get("candidate_type"),
                            "confidence": row.get("confidence"),
                            "matched_features": row.get("matched_features")}
                           for row in (semantics.get("box_candidates") or [])
                           if isinstance(row, dict)],
        "model_assist": {"used": assist.get("used"), "status": assist.get("status"),
                         "calls": assist.get("calls"),
                         "stable_error_code": assist.get("stable_error_code")},
        "reviewable": bool(semantics.get("reviewable")),
    }


def group_of(field_key: str) -> Tuple[str, ...]:
    """结构尺寸组：一个字段冲突时同组字段一起不得被视为已确认（Spec §5.2 第 4 条）。"""
    from . import model

    return tuple(model.DIMENSION_GROUP) if field_key in model.DIMENSION_GROUP else (str(field_key),)
