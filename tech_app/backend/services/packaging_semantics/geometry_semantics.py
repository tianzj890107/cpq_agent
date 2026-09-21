"""几何语义：外轮廓 / 出血 / 开窗 / 孔位 / 拼版候选（DWG 第 4 批 Spec §3/§7）。

只做**确定性整理**：不判盒型（fields.py 出候选）、不锁定结论、不做排版。
每个候选都带能在输入 CAD IR 的 `evidence` 里回查到的引用。

第 4b 批补充（Spec `packaging-product-outline-and-die-layer-roles.md` §2）：
「拼版 / 图框 / 整张」三类闭合候选**保留**在 `boundary_candidates` 里，但**不得**参与
成品轮廓、成品长宽与盒型候选的判定，并逐条披露到 `rejected[]`
（`reason` 闭集：frame_layer / panel_repeat / sheet_extent）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from . import model


def _int_of(value: Any) -> int:
    """脏数值（字符串/None/NaN）一律归 0，绝不向上冒泡（上游字段不一定可信）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number != number or number in (float("inf"), float("-inf")):
        return 0
    return int(number)


def _bbox(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _outline_candidate(outline: Dict[str, Any], entity: Dict[str, Any], role: str,
                       closed: bool, known: Any) -> Optional[Dict[str, Any]]:
    box = _bbox(outline.get("bbox")) or _bbox(entity.get("bbox"))
    if box is None:
        return None
    entity_id = str(outline.get("entity_id") or entity.get("entity_id") or "")
    try:
        area = float(outline.get("area") if outline.get("area") is not None
                     else (entity.get("area") or 0.0))
    except (TypeError, ValueError):
        area = 0.0
    try:
        length = float(outline.get("length") if outline.get("length") is not None
                       else (entity.get("length") or 0.0))
    except (TypeError, ValueError):
        length = 0.0
    length_mm = outline.get("length_mm")
    if length_mm is None:
        length_mm = entity.get("length_mm")
    try:
        length_mm = None if length_mm is None else float(length_mm)
    except (TypeError, ValueError):
        length_mm = None
    return {
        "outline_id": str(outline.get("outline_id") or entity_id),
        "entity_ids": [entity_id] if entity_id else [],
        "bbox": box,
        "length": length,
        "length_mm": length_mm,
        "area": area,
        "is_closed": bool(closed),
        "layer": str(outline.get("layer") or entity.get("layer") or ""),
        "role": role,
        "evidence_level": "STRONG" if closed else "WEAK",
        "evidence_refs": model.evidence_refs_of(known, outline.get("evidence_ref"),
                                                entity.get("evidence_ref")),
    }


def build_geometry(ir: Dict[str, Any], layers: List[Dict[str, Any]], known: Any,
                   max_candidates: int = 200) -> Dict[str, Any]:
    geometry = ir.get("geometry") or {}
    entities = {str(item.get("entity_id") or ""): item
                for item in (ir.get("entities") or []) if isinstance(item, dict)}

    candidates: List[Dict[str, Any]] = []
    for outline in (geometry.get("closed_outlines") or []):
        if not isinstance(outline, dict):
            continue
        entity = entities.get(str(outline.get("entity_id") or "")) or {}
        item = _outline_candidate(outline, entity, _role_of(layers, outline, entity), True, known)
        if item:
            candidates.append(item)
    for outline in (geometry.get("open_outlines") or []):
        if not isinstance(outline, dict):
            continue
        entity = entities.get(str(outline.get("entity_id") or "")) or {}
        item = _outline_candidate(outline, entity, _role_of(layers, outline, entity), False, known)
        if item:
            candidates.append(item)
    # Spec §3：候选按 (is_closed desc, -area, outline_id) 排序 → 与输入书写顺序无关。
    candidates.sort(key=lambda row: (0 if row["is_closed"] else 1, -row["area"],
                                     row["outline_id"]))
    truncated = 0
    if max_candidates and len(candidates) > max_candidates:
        truncated = len(candidates) - max_candidates
        candidates = candidates[:max_candidates]

    holes: List[Dict[str, Any]] = []
    for hole in (geometry.get("holes") or []):
        if not isinstance(hole, dict):
            continue
        entity = entities.get(str(hole.get("entity_id") or "")) or {}
        radius = _number(hole.get("radius"))
        if radius is None and hole.get("diameter") is not None:
            radius = (_number(hole.get("diameter")) or 0.0) / 2.0
        diameter = _number(hole.get("diameter"))
        if diameter is None and radius is not None:
            diameter = radius * 2.0
        area = _number(hole.get("area"))
        if area is None and radius is not None:
            area = math.pi * radius * radius
        center = hole.get("center")
        refs = model.evidence_refs_of(known, hole.get("evidence_ref"),
                                      entity.get("evidence_ref"))
        if not refs:
            # 合成夹具里的圆可以是「只有 ref、没有实体条目」的孔位：宁可比对失败，
            # 也不许把圆证据悄悄丢掉（Spec §4.1 要求 round_tube 证据指向圆实体）。
            source_ref = str(hole.get("evidence_ref") or entity.get("evidence_ref") or "")
            refs = [source_ref] if source_ref else []
        holes.append({
            "hole_id": str(hole.get("entity_id") or ""),
            "kind": str(hole.get("kind") or "unknown"),
            "center": [float(item) for item in center] if isinstance(center, (list, tuple)) else [],
            "radius": radius,
            "diameter": diameter,
            "area": area,
            "layer": str(entity.get("layer") or ""),
            "role": role_of_layer(layers, entity.get("layer")),
            "evidence_refs": refs,
        })
    holes.sort(key=lambda row: (row["hole_id"], row["kind"]))
    if max_candidates and len(holes) > max_candidates:
        truncated += len(holes) - max_candidates
        holes = holes[:max_candidates]

    bleed = [{"outline_id": row["outline_id"], "bbox": row["bbox"], "area": row["area"],
              "role": row["role"], "evidence_refs": row["evidence_refs"]}
             for row in candidates if row["is_closed"] and row["role"] == "bleed"]
    windows = [{"outline_id": row["outline_id"], "bbox": row["bbox"], "area": row["area"],
                "role": row["role"], "evidence_refs": row["evidence_refs"]}
               for row in candidates if row["role"] == "hole"]
    windows.extend({"hole_id": row["hole_id"], "kind": row["kind"], "center": row["center"],
                    "radius": row["radius"], "role": row["role"],
                    "evidence_refs": row["evidence_refs"]}
                   for row in holes if row["role"] == "hole")

    rejected, product_candidates = _split_product_candidates(ir, layers, candidates)
    return {
        "boundary_candidates": candidates,
        "product_candidates": product_candidates,
        "rejected": rejected,
        "bleed_candidates": bleed,
        "windows": windows,
        "holes": holes,
        "panel": _panel(geometry, candidates, known),
        "truncated": truncated,
    }


#: Spec §2.2 —— 被排除候选的 reason 闭集（判定优先级同顺序）。
REJECT_REASONS = ("frame_layer", "panel_repeat", "sheet_extent")

#: Spec §2.2 —— `sheet_extent` 判据的容差（任一方向相差 ≤ 1%）。
SHEET_EXTENT_TOLERANCE = 0.01


def _repeated_entity_ids(geometry: Dict[str, Any]) -> set:
    """落在 `count >= 2` 的重复组里的实体 id（拼版/展开料单元）。"""
    out: set = set()
    for group in (geometry.get("repeated_groups") or []):
        if not isinstance(group, dict) or _int_of(group.get("count")) < 2:
            continue
        out.update(str(item) for item in (group.get("entity_ids") or []) if item)
    return out


def _same_span_as_extents(bbox: Any, extents: Any) -> bool:
    """候选 bbox 与 `document.extents` 在任一方向（宽/高）相差 ≤ 1%。"""
    box = _bbox(bbox)
    ext = _bbox(extents)
    if box is None or ext is None:
        return False
    for low, high in ((0, 2), (1, 3)):
        span = box[high] - box[low]
        base = ext[high] - ext[low]
        if base > 0 and abs(span - base) <= SHEET_EXTENT_TOLERANCE * base:
            return True
    return False


def reject_reason(row: Dict[str, Any], layers: List[Dict[str, Any]],
                  extents: Any, repeated_ids: set) -> str:
    """一个闭合候选该被排除的原因；返回 "" 表示它是产品级候选（Spec §2）。"""
    if not row.get("is_closed"):
        return ""
    if role_of_layer(layers, row.get("layer")) == "frame":
        return "frame_layer"
    if set(row.get("entity_ids") or []) & repeated_ids:
        return "panel_repeat"
    if _same_span_as_extents(row.get("bbox"), extents):
        return "sheet_extent"
    return ""


def _split_product_candidates(ir: Dict[str, Any], layers: List[Dict[str, Any]],
                              candidates: List[Dict[str, Any]]) -> Any:
    """拆成 (rejected, product_candidates)。

    `rejected` 按 `(reason, -area, outline_id)` 排序（Spec §2.2/§6），
    `product_candidates` 保持 `candidates` 原顺序（调用方无需再排）。
    """
    extents = (ir.get("document") or {}).get("extents")
    repeated_ids = _repeated_entity_ids(ir.get("geometry") or {})
    rejected: List[Dict[str, Any]] = []
    product: List[Dict[str, Any]] = []
    for row in candidates:
        reason = reject_reason(row, layers, extents, repeated_ids)
        if not reason:
            product.append(row)
            continue
        rejected.append({
            "outline_id": str(row.get("outline_id") or ""),
            "reason": reason,
            "bbox": list(row.get("bbox") or []),
            "area": row.get("area") or 0.0,
            "evidence_refs": list(row.get("evidence_refs") or []),
        })
    rejected.sort(key=lambda item: (item["reason"], -(item["area"] or 0.0),
                                    item["outline_id"]))
    return rejected, product


def _role_of(layers: List[Dict[str, Any]], outline: Dict[str, Any],
             entity: Dict[str, Any]) -> str:
    return role_of_layer(layers, outline.get("layer") or entity.get("layer"))


def role_of_layer(layers: List[Dict[str, Any]], layer_name: Any) -> str:
    name = str(layer_name or "")
    for row in layers or []:
        if row.get("name") == name:
            return str(row.get("role") or "unknown")
    return "unknown"


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _panel(geometry: Dict[str, Any], candidates: List[Dict[str, Any]], known: Any) -> Dict[str, Any]:
    """拼版候选：只报 panel_count / multi_up / candidates，不决定实际排版（Spec §7）。"""
    repeated = [group for group in (geometry.get("repeated_groups") or [])
                if isinstance(group, dict) and _int_of(group.get("count")) >= 2]
    if not repeated:
        return {"panel_count": 1, "multi_up": False, "candidates": [], "evidence_refs": []}
    best = max(repeated, key=lambda group: (_int_of(group.get("count")),
                                            str(group.get("signature") or "")))
    entity_ids = [str(item) for item in (best.get("entity_ids") or [])]
    picked = [row for row in candidates if set(row["entity_ids"]) & set(entity_ids)]
    refs: List[str] = []
    for row in picked:
        for ref in row["evidence_refs"]:
            if ref not in refs:
                refs.append(ref)
    return {
        "panel_count": _int_of(best.get("count")),
        "multi_up": True,
        "candidates": picked,
        "signature": str(best.get("signature") or ""),
        "evidence_refs": model.evidence_refs_of(known, refs),
    }
