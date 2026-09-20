"""字段候选：长宽高、材料/工艺文字、拼版、盒型候选（DWG 第 4 批 Spec §5/§7）。

铁律（Spec §5.2）：
  1. 未确认不写值 —— status != confirmed 的字段绝不写进 requirement.data；
  2. 单位未确认 → 绝对尺寸最高只能是 needs_confirmation + `PACKAGING_UNIT_UNCONFIRMED`；
  3. 模型不得覆盖 CAD —— 由 model_assist 处理；
  4. 冲突不静默 —— declared/measured 超容差 → conflict + 两条证据 + delta；
  5. 缺什么就 missing —— 抄不到的字段绝不编造。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from . import model

MATERIAL_LABELS = ("材质", "材料", "material", "materials")
PROCESS_LABELS = ("工艺", "表面工艺", "表面处理", "process", "工艺要求")

#: 工艺关键词 → 容器字段键（键必须在本批白名单里）。
PROCESS_KEYWORDS: Tuple[Tuple[str, str], ...] = (
    ("烫金", "hot_stamping"),
    ("覆膜", "lamination"),
    ("亮膜", "lamination"),
    ("哑膜", "lamination"),
    ("UV", "uv_coating"),
    ("上光", "uv_coating"),
    ("丝印", "silk_screen"),
    ("压凹凸", "emboss_deboss"),
    ("凹凸", "emboss_deboss"),
    ("模切", "die_cutting"),
    ("裱", "mounting"),
    ("V槽", "v_groove"),
)

_GSM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*g\b", re.IGNORECASE)
_HEIGHT_RE = re.compile(r"(高度|高|height|h)\s*[:=：]?\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")


# --------------------------------------------------------------------------- #
# 文字 / 尺寸标注
# --------------------------------------------------------------------------- #
def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _split_label(text: str) -> Tuple[str, str]:
    for sep in ("：", ":"):
        head, found, tail = text.partition(sep)
        if found:
            return head.strip(), tail.strip()
    return "", text.strip()


def build_texts(ir: Dict[str, Any], known: Any) -> Dict[str, Any]:
    """把图纸文字整理成材料 / 工艺候选（`inferred_from_text` 的唯一来源）。"""
    material: List[Dict[str, Any]] = []
    process: List[Dict[str, Any]] = []
    height: List[Dict[str, Any]] = []

    texts = [item for item in (ir.get("texts") or []) if isinstance(item, dict)]
    texts.sort(key=lambda item: (str(item.get("handle") or ""), str(item.get("entity_id") or "")))
    for item in texts:
        raw = str(item.get("normalized_text") or item.get("raw_text") or "").strip()
        if not raw:
            continue
        refs = model.evidence_refs_of(known, item.get("evidence_ref"))
        label, value = _split_label(raw)
        upper_label = label.upper()

        if upper_label in {name.upper() for name in MATERIAL_LABELS} and value:
            material.append({"field": "face_paper", "text": value, "value": value,
                             "evidence_level": "MODERATE", "evidence_refs": refs})
            gsm = _GSM_RE.search(value)
            if gsm:
                material.append({"field": "face_paper_gsm", "text": gsm.group(1),
                                 "value": _number(gsm.group(1)),
                                 "evidence_level": "MODERATE", "evidence_refs": refs})

        if upper_label in {name.upper() for name in PROCESS_LABELS} and value:
            seen: List[str] = []
            for keyword, field_key in PROCESS_KEYWORDS:
                if keyword.upper() in value.upper() and field_key not in seen:
                    seen.append(field_key)
                    process.append({"field": field_key, "text": value, "value": value,
                                    "evidence_level": "MODERATE", "evidence_refs": refs})

        match = _HEIGHT_RE.search(raw)
        if match:
            height.append({"field": "inner_height", "text": raw, "value": _number(match.group(2)),
                           "evidence_level": "MODERATE", "evidence_refs": refs})

    return {
        "material_candidates": material,
        "process_candidates": process,
        "height_candidates": height,
        "title_block": {},
        "unit_hints": _unit_hints(ir),
    }


def _unit_hints(ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for candidate in ((ir.get("units") or {}).get("candidates") or []):
        if not isinstance(candidate, dict):
            continue
        out.append({"unit": str(candidate.get("unit") or ""),
                    "confidence": _number(candidate.get("confidence")),
                    "reason": str(candidate.get("reason") or ""),
                    "evidence_refs": []})
    return out


def _targets(dimension: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    targets = dimension.get("target_entity_ids")
    if not isinstance(targets, (list, tuple)):
        targets = [dimension.get("target_entity_id")]
    wanted = {str(item) for item in targets if item}
    return bool(wanted & {str(item) for item in candidate.get("entity_ids") or []})


def _assign_dimensions(dimensions: List[Dict[str, Any]], candidate: Dict[str, Any]):
    """把落在候选上的标注分到长边 / 短边：谁近就算量谁（Spec §5.3）。"""
    box = candidate["bbox"]
    edges = sorted([abs(box[2] - box[0]), abs(box[3] - box[1])])
    short_edge, long_edge = edges[0], edges[1]
    for_length: Optional[Dict[str, Any]] = None
    for_width: Optional[Dict[str, Any]] = None
    for dimension in dimensions:
        measured = _number(dimension.get("measured_value"))
        if measured is None:
            continue
        to_long = abs(measured - long_edge)
        to_short = abs(measured - short_edge)
        if to_long <= to_short and for_length is None:
            for_length = dimension
        elif for_width is None:
            for_width = dimension
    return for_length, for_width, long_edge, short_edge


def _size_field(ir: Dict[str, Any], candidate: Dict[str, Any], measured: float,
                dimension: Optional[Dict[str, Any]], units_confirmed: bool,
                warnings: List[Dict[str, Any]]) -> Dict[str, Any]:
    refs = list(candidate.get("evidence_refs") or [])
    if dimension is None:
        return model.field_entry(ir, origin="inferred_from_geometry",
                                 status="needs_confirmation", value=measured,
                                 confidence=0.8, evidence_level="MODERATE",
                                 evidence_refs=refs)

    declared = _number(dimension.get("declared_value"))
    tolerance = _number(dimension.get("tolerance"))
    tolerance = model.DEFAULT_CONFLICT_TOLERANCE if tolerance is None else tolerance
    refs = list(dict.fromkeys(
        [str(dimension.get("evidence_ref") or "")] + refs))
    refs = model.evidence_refs_of(set(ir.get("evidence") or {}), refs)
    if declared is None:
        return model.field_entry(ir, origin="inferred_from_geometry",
                                 status="needs_confirmation", value=measured,
                                 confidence=0.8, evidence_level="MODERATE",
                                 evidence_refs=refs)

    if not units_confirmed:
        _warn(warnings, "PACKAGING_UNIT_UNCONFIRMED",
              "图纸未标注单位，绝对尺寸只能作为候选，需要人工确认")
        return model.field_entry(ir, origin="inferred_from_geometry",
                                 status="needs_confirmation", value=measured,
                                 confidence=0.8, evidence_level="MODERATE",
                                 evidence_refs=refs)

    delta = abs(declared - measured)
    if delta <= tolerance:
        return model.field_entry(ir, origin="confirmed_from_cad", status="confirmed",
                                 value=declared, confidence=1.0, evidence_level="STRONG",
                                 evidence_refs=refs)

    conflict = {
        "field": "",
        "declared": declared,
        "measured": measured,
        "delta": delta,
        "tolerance": tolerance,
        "unit": str(dimension.get("unit") or ""),
        "status": "conflict",
        "evidence_refs": refs,
    }
    return model.field_entry(ir, origin="conflict", status="conflict", value=None,
                             confidence=0.0, evidence_level="CONTRADICTORY",
                             evidence_refs=refs, conflicts=[conflict])


def _warn(bucket: List[Dict[str, Any]], code: str, message: str,
          evidence_refs: Optional[List[str]] = None) -> None:
    entry = {"code": code, "message": message, "evidence_refs": list(evidence_refs or [])}
    if entry not in bucket:
        bucket.append(entry)


# --------------------------------------------------------------------------- #
# 盒型候选（只出候选，不锁定；Spec §7）
# --------------------------------------------------------------------------- #
def build_box_candidates(ir: Dict[str, Any], layers: List[Dict[str, Any]],
                         geometry: Dict[str, Any], known: Any) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    holes = geometry.get("holes") or []
    closed = [row for row in geometry.get("boundary_candidates") or [] if row.get("is_closed")]
    circle_areas = [row["area"] for row in holes
                    if row.get("kind") == "circle" and _number(row.get("area"))]
    biggest_circle = max(circle_areas) if circle_areas else 0.0
    biggest_outline = max([row.get("area") or 0.0 for row in closed], default=0.0)

    if biggest_circle > 0.0 and biggest_circle > biggest_outline:
        refs: List[str] = []
        for row in holes:
            if row.get("kind") == "circle" and _number(row.get("area")) == biggest_circle:
                refs.extend(row.get("evidence_refs") or [])
        candidates.append({
            "candidate_type": "round_tube",
            "confidence": model.cap_confidence("inferred_from_geometry", 0.4),
            "matched_features": ["circular_closed_boundary"],
            "missing_features": ["panel_layout"],
            "contradictory_features": [],
            "evidence_refs": model.evidence_refs_of(known, refs),
        })

    crease_rows = [row for row in layers if row.get("role") == "crease"]
    if crease_rows and closed:
        refs = list(closed[0].get("evidence_refs") or [])
        for row in crease_rows:
            refs.extend(row.get("evidence_refs") or [])
        candidates.append({
            "candidate_type": "folding_carton",
            "confidence": model.cap_confidence("inferred_from_geometry", 0.35),
            "matched_features": ["closed_outline", "crease_lines"],
            "missing_features": ["glue_flap", "panel_layout"],
            "contradictory_features": [],
            "evidence_refs": model.evidence_refs_of(known, refs),
        })

    candidates.sort(key=lambda row: (-float(row["confidence"]), row["candidate_type"]))
    return candidates


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def build_fields(ir: Dict[str, Any], layers: List[Dict[str, Any]],
                 geometry: Dict[str, Any], texts: Dict[str, Any],
                 known: Any, max_conflicts: int = 200) -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    fields = {key: model.missing_entry(ir) for key in model.packaging_field_keys()}

    units_confirmed = str((ir.get("units") or {}).get("unit_status") or "") == "confirmed"
    closed = [row for row in geometry.get("boundary_candidates") or [] if row.get("is_closed")]
    primary = closed[0] if closed else None

    conflicts: List[Dict[str, Any]] = []
    if primary:
        dimensions = [item for item in (ir.get("dimensions") or []) if isinstance(item, dict)]
        on_candidate = [item for item in dimensions if _targets(item, primary)]
        for_length, for_width, long_edge, short_edge = _assign_dimensions(on_candidate, primary)
        for key, measured, dimension in (("inner_length", long_edge, for_length),
                                        ("inner_width", short_edge, for_width)):
            if key not in fields:
                continue
            entry = _size_field(ir, primary, measured, dimension, units_confirmed, warnings)
            fields[key] = entry
            for conflict in entry.get("conflicts") or []:
                conflict = dict(conflict)
                conflict["field"] = key
                conflicts.append(conflict)

    if max_conflicts and len(conflicts) > max_conflicts:
        conflicts = conflicts[:max_conflicts]
        _warn(warnings, "PACKAGING_SEMANTICS_CONFLICTS_TRUNCATED",
              "冲突条数超过上限，已截断，请人工核对图纸标注")
    conflicts.sort(key=lambda row: (row["field"], str(row["declared"])))

    # 材料 / 工艺 / 高度：图纸文字（inferred_from_text）是唯一来源。
    for candidate in (texts.get("material_candidates") or []) + (texts.get("process_candidates") or []):
        key = str(candidate.get("field") or "")
        if key not in fields:
            continue
        fields[key] = model.field_entry(ir, origin="inferred_from_text",
                                        status="needs_confirmation",
                                        value=candidate.get("value"),
                                        confidence=0.7, evidence_level="MODERATE",
                                        evidence_refs=candidate.get("evidence_refs"))
    for candidate in texts.get("height_candidates") or []:
        key = str(candidate.get("field") or "")
        if key not in fields or candidate.get("value") is None:
            continue
        fields[key] = model.field_entry(ir, origin="inferred_from_text",
                                        status="needs_confirmation",
                                        value=candidate.get("value"), confidence=0.7,
                                        evidence_level="MODERATE",
                                        evidence_refs=candidate.get("evidence_refs"))

    box_candidates = build_box_candidates(ir, layers, geometry, known)
    if box_candidates and "box_type" in fields:
        top = box_candidates[0]
        fields["box_type"] = model.field_entry(
            ir, origin="inferred_from_geometry", status="needs_confirmation",
            value=top["candidate_type"], confidence=top["confidence"],
            evidence_level="MODERATE", evidence_refs=top.get("evidence_refs"))
    elif "box_type" in fields:
        fields["box_type"] = model.missing_entry(ir)

    dimensions_block = {
        "conflicts": conflicts,
        "measured_total": len([item for item in (ir.get("dimensions") or [])
                               if isinstance(item, dict) and _number(item.get("measured_value"))
                               is not None]),
        "declared_total": len([item for item in (ir.get("dimensions") or [])
                               if isinstance(item, dict) and _number(item.get("declared_value"))
                               is not None]),
    }
    return {"fields": fields, "box_candidates": box_candidates,
            "dimensions": dimensions_block, "warnings": warnings}
