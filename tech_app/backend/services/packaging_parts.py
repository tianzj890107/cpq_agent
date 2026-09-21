"""DWG 图纸 → 零件（展开件）提取与回填（Spec `packaging-dwg-parts-extraction.md`）。

以 CAD IR 的 `geometry.components`（连通分量）为**唯一**零件来源 —— 真图
（`酒盒.dwg`）只有 2 条闭合轮廓却有 402 个连通分量，"零件 = 闭合轮廓"在真图上不成立。
每个分量产出一件：展开长宽取分量 bbox，角色取件内实体图层角色的最高优先级，
证据逐条可回查。

本模块是**纯函数**：`extract()` 不落盘、不联网、不改入参、不写审计；
落盘只有 `save_parts()` 一处（版本化，同 `packaging_semantics` 的做法）。
BOM 回填（`bind_rows`）只做行级临时口径 —— 正式口径应是「零件 ↔ 模板行」的对应表。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from ..storage.meta_backend import get_backend

ENGINE_VERSION = "packaging-parts/1"

#: 零件文档在项目存储里的 doc key。
DOC_KEY = "packaging_parts"

#: 过滤阈值与上限：默认值**只在这里**，不许散落在判定代码里。
DEFAULT_OPTIONS = {"min_area_mm2": 2000, "max_edge_mm": 1200,
                   "max_area_mm2": 1000000, "max_parts": 64}

#: 「可制造曲线」闭集：其余（TEXT / MTEXT / DIMENSION / INSERT / ATTRIB…）不构成零件。
CURVE_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE")

#: 提不出零件时的稳定原因码（前端据此说清"为什么没有零件"）。
REASON_CODES = ("edge_over_max", "area_over_max", "area_under_min", "no_curve_entity",
                "no_components", "all_filtered", "no_unit")

PART_CODE_FORMAT = "DWG-P%02d"

#: 件内角色优先级：一件里既有刀线又有压痕 → `cut`。
ROLE_PRIORITY = ("cut", "half_cut", "crease", "v_groove", "glue_flap", "print", "bleed",
                 "frame", "hole", "unknown")

#: 每个项目最多保留的零件文档版本数（旧版本仍可按 id 回看）。
MAX_VERSIONS = 20

#: 回填只碰这两类行（Spec C7.1）。
BINDABLE_CATEGORIES = ("box_part", "optional_part")

#: 行级绑定的规则号（留痕用，正式口径定下来之前一直带这个标记）。
BINDING_RULE_ID = "dwg_parts_row_pairing_v1"

UNAVAILABLE_MESSAGES = {
    "no_components": "图纸里没有可用的连通分量，请确认上传的是 2D 刀模图",
    "all_filtered": "图纸里的分量都被过滤（整版图框 / 碎线 / 无制造曲线），没有可制造的零件",
    "no_unit": "图纸单位未确认，不能给出绝对展开尺寸，请先确认图纸单位",
}


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _text(value: Any) -> str:
    return str(value or "").strip()


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 3)


def _options(options: Any) -> Dict[str, float]:
    merged = dict(DEFAULT_OPTIONS)
    if isinstance(options, dict):
        for key, value in options.items():
            if key in merged and _num(value) is not None:
                merged[key] = _num(value)
    return merged


def _layer_key(name: Any) -> str:
    return _text(name).upper()


def _bbox_of(component: Dict[str, Any], entities: List[Dict[str, Any]]) -> Optional[List[float]]:
    """分量外接矩形：优先用分量自带 bbox，缺失时由件内实体 bbox 合并（仍缺失 → None）。"""
    raw = component.get("bbox")
    values = [_num(item) for item in raw] if isinstance(raw, (list, tuple)) else []
    if len(values) == 4 and all(item is not None for item in values):
        return [float(item) for item in values]
    boxes = [entity.get("bbox") for entity in entities if isinstance(entity.get("bbox"), list)]
    boxes = [[_num(item) for item in box] for box in boxes]
    boxes = [box for box in boxes if len(box) == 4 and all(item is not None for item in box)]
    if not boxes:
        return None
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _layer_roles(ir: Dict[str, Any], semantics: Any) -> Dict[str, str]:
    """图层名（大写）→ 角色。有语义文档就用它，否则现算一份（同一套规则）。"""
    doc = semantics if isinstance(semantics, dict) else None
    if doc is None:
        try:                                              # 现算：只读规则文件，不落盘
            from . import packaging_semantics
            doc = packaging_semantics.analyze(ir)
        except Exception:                                 # noqa: BLE001 - 算不出来也要出零件
            doc = None
    roles: Dict[str, str] = {}
    if isinstance(doc, dict):
        for row in doc.get("layers") or []:
            if not isinstance(row, dict):
                continue
            key = _layer_key(row.get("name"))
            if key:
                roles[key] = _text(row.get("role")) or "unknown"
    if roles:
        return roles
    # 语义层不可用时的兜底：IR 里已经带角色就用它，否则 unknown（绝不猜）。
    for row in ir.get("layers") or []:
        if not isinstance(row, dict):
            continue
        key = _layer_key(row.get("name"))
        if key:
            roles[key] = _text(row.get("role") or row.get("inferred_role")) or "unknown"
    return roles


def _role_index(role: str) -> int:
    try:
        return ROLE_PRIORITY.index(role)
    except ValueError:
        return len(ROLE_PRIORITY) - 1


def _known_evidence(ir: Dict[str, Any]) -> Any:
    return ir.get("evidence") if isinstance(ir.get("evidence"), dict) else {}


# --------------------------------------------------------------------------- #
# 1 提取（纯函数）
# --------------------------------------------------------------------------- #
def extract(ir: Dict[str, Any], semantics: Any = None, *,
            options: Any = None) -> Dict[str, Any]:
    """CAD IR → 零件文档。同一份 IR 两次跑必须逐字相同（排序与编号全部确定性）。"""
    if not isinstance(ir, dict):
        raise TypeError("extract() 需要一份 CAD IR 文档")
    config = _options(options)
    geometry = ir.get("geometry") if isinstance(ir.get("geometry"), dict) else {}
    components = [row for row in (geometry.get("components") or []) if isinstance(row, dict)]
    entities = {str((row or {}).get("entity_id")): row
                for row in (ir.get("entities") or []) if isinstance(row, dict)}
    known = _known_evidence(ir)
    roles = _layer_roles(ir, semantics)
    units = ir.get("units") if isinstance(ir.get("units"), dict) else {}
    unit_status = _text(units.get("unit_status"))
    unit_ok = unit_status == "confirmed"

    kept: List[Dict[str, Any]] = []
    filtered: List[Dict[str, Any]] = []
    for component in components:
        component_id = _text(component.get("component_id"))
        entity_ids = [_text(item) for item in (component.get("entity_ids") or [])]
        members = [entities[item] for item in entity_ids if item in entities]
        bbox = _bbox_of(component, members)
        if bbox is None:
            length = width = None
            area = 0.0
        else:
            length = abs(bbox[2] - bbox[0])
            width = abs(bbox[3] - bbox[1])
            area = length * width
        curves = [entity for entity in members
                  if _text(entity.get("type")).upper() in CURVE_TYPES]
        reasons: List[str] = []
        if bbox is not None:
            if max(length or 0.0, width or 0.0) > float(config["max_edge_mm"]):
                reasons.append("edge_over_max")
            if area > float(config["max_area_mm2"]):
                reasons.append("area_over_max")
            if area < float(config["min_area_mm2"]):
                reasons.append("area_under_min")
        # 「没有可制造曲线」只在**看得见实体**时才敢判：分量声明了实体却一条都查不到
        # （块引用 / 代理实体 / IR 缺条），说明这一件对我们是不透明的，宁可留着让人看，
        # 也不能悄悄当碎线丢掉（真图上"零件 = 闭合轮廓"就是这么丢掉 400 件的前车之鉴）。
        if not curves and (not entity_ids or members):
            reasons.append("no_curve_entity")
        if reasons:
            filtered.append({"component_id": component_id, "reasons": reasons,
                             "reason": reasons[0], "bbox": bbox})
            continue

        part_roles = sorted({roles.get(_layer_key(entity.get("layer")), "unknown")
                             for entity in curves})
        role = min(part_roles, key=_role_index) if part_roles else "unknown"
        layer_names = sorted({_text(entity.get("layer")) for entity in curves
                              if _text(entity.get("layer"))})
        refs = [str(entity.get("evidence_ref") or "") for entity in curves]
        refs += ["ev:L:%s" % name for name in layer_names]
        evidence_refs = sorted({ref for ref in refs if ref and ref in known})
        kept.append({
            "component_id": component_id,
            "entity_ids": sorted(set(entity_ids)),
            "entity_total": len(entity_ids),
            "bbox": bbox,
            "length": length,
            "width": width,
            "area": area,
            "role": role,
            "layers": layer_names,
            "evidence_refs": evidence_refs,
        })

    # 排序：面积降序、component_id 升序（input 书写顺序不影响输出）。
    kept.sort(key=lambda row: (-(row["area"] or 0.0), row["component_id"]))
    filtered.sort(key=lambda row: row["component_id"])

    parts: List[Dict[str, Any]] = []
    seen: Dict[Tuple[float, float, int], str] = {}
    for index, row in enumerate(kept, start=1):
        part_code = PART_CODE_FORMAT % index
        key = (round(row["length"] or 0.0, 3), round(row["width"] or 0.0, 3),
               row["entity_total"])
        repeat_of = seen.get(key, "")
        if not repeat_of or repeat_of == part_code:
            seen[key] = part_code
        parts.append({
            "part_code": part_code,
            "name": "图纸零件 P%02d" % index,
            "unfolded_length_mm": _round(row["length"]) if unit_ok else None,
            "unfolded_width_mm": _round(row["width"]) if unit_ok else None,
            "area_mm2": _round(row["area"]) if unit_ok else None,
            "layers": row["layers"],
            "role": row["role"],
            "component_id": row["component_id"],
            "entity_ids": row["entity_ids"],
            "evidence_refs": row["evidence_refs"],
            "repeat_of": repeat_of,
            "size_source": "dwg_outline",
        })

    max_parts = int(config["max_parts"])
    truncated = max(0, len(parts) - max_parts)
    parts = parts[:max_parts]

    unavailable: List[Dict[str, Any]] = []
    if not components:
        unavailable.append({"code": "no_components",
                            "message": UNAVAILABLE_MESSAGES["no_components"]})
    elif not kept:
        unavailable.append({"code": "all_filtered",
                            "message": UNAVAILABLE_MESSAGES["all_filtered"]})
    if not unit_ok:
        unavailable.append({"code": "no_unit", "message": UNAVAILABLE_MESSAGES["no_unit"]})

    by_role: Dict[str, int] = {}
    for row in parts:
        by_role[row["role"]] = by_role.get(row["role"], 0) + 1

    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    sem = semantics if isinstance(semantics, dict) else {}
    sem_source = sem.get("source") if isinstance(sem.get("source"), dict) else {}
    doc = {
        "engine_version": ENGINE_VERSION,
        "parts": parts,
        "filtered": filtered,
        "unavailable": unavailable,
        "stats": {"part_total": len(parts), "filtered_total": len(filtered),
                  "truncated": truncated, "by_role": by_role},
        "source": {
            "ir_id": _text(ir.get("ir_id")),
            "ir_hash": _text(ir.get("ir_hash")),
            "semantics_id": _text(sem.get("semantics_id")),
            "semantics_hash": _text(sem.get("semantics_hash")),
            "drawing_version": source.get("drawing_version") or sem_source.get("drawing_version"),
            "unit_status": unit_status,
        },
        "reviewable": True,
    }
    doc["parts_id"] = ""
    doc["parts_hash"] = ""
    doc["parts_id"], doc["parts_hash"] = _identity(doc)
    return doc


def _identity(doc: Dict[str, Any]) -> Tuple[str, str]:
    """版本锚点：同一份内容 → 同一个 id（落库幂等）。"""
    from .packaging_semantics import model as sem_model

    body = {key: value for key, value in doc.items()
            if key not in ("parts_id", "parts_hash")}
    digest = sem_model.sha256_hex(sem_model.canonical_json(sem_model.json_safe(body)))
    return "parts:" + digest[:16], digest


def summarize(doc: Any) -> Dict[str, Any]:
    """摘要（不含 entity_ids / 证据明细）：给会话与看板用。"""
    payload = doc if isinstance(doc, dict) else {}
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    parts = []
    for row in payload.get("parts") or []:
        if not isinstance(row, dict):
            continue
        parts.append({"part_code": _text(row.get("part_code")),
                      "name": _text(row.get("name")),
                      "unfolded_length_mm": row.get("unfolded_length_mm"),
                      "unfolded_width_mm": row.get("unfolded_width_mm"),
                      "area_mm2": row.get("area_mm2"),
                      "layers": list(row.get("layers") or []),
                      "role": _text(row.get("role")),
                      "component_id": _text(row.get("component_id")),
                      "repeat_of": _text(row.get("repeat_of")),
                      "size_source": _text(row.get("size_source"))})
    return {
        "engine_version": _text(payload.get("engine_version")) or ENGINE_VERSION,
        "parts_id": _text(payload.get("parts_id")),
        "parts_hash": _text(payload.get("parts_hash")),
        "parts": parts,
        "filtered": [{"component_id": _text(row.get("component_id")),
                      "reasons": list(row.get("reasons") or [])}
                     for row in (payload.get("filtered") or []) if isinstance(row, dict)],
        "unavailable": [{"code": _text(row.get("code")), "message": _text(row.get("message"))}
                        for row in (payload.get("unavailable") or []) if isinstance(row, dict)],
        "stats": stats,
        "source": payload.get("source") if isinstance(payload.get("source"), dict) else {},
        "reviewable": bool(payload.get("reviewable")),
    }


# --------------------------------------------------------------------------- #
# 2 落库 / 读回（版本化）
# --------------------------------------------------------------------------- #
def _load_items(project_id: str) -> List[Dict[str, Any]]:
    doc = get_backend().get_doc(project_id, DOC_KEY) or {}
    items = doc.get("items") if isinstance(doc, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def save_parts(project_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """落一版零件文档：同一 `parts_id` 覆盖同一条，新内容追加（最多 20 版）。"""
    if not isinstance(doc, dict):
        raise ValueError("save_parts() 需要一份零件文档")
    record = copy.deepcopy(doc)
    parts_id, parts_hash = _identity(record)
    record["parts_id"] = parts_id
    record["parts_hash"] = parts_hash
    items = [item for item in _load_items(project_id)
             if _text(item.get("parts_id")) != parts_id]
    items.insert(0, record)
    get_backend().put_doc(project_id, DOC_KEY, {"items": items[:MAX_VERSIONS]})
    return record


def load_parts(project_id: str, parts_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for item in _load_items(project_id):
        if parts_id is None or _text(item.get("parts_id")) == str(parts_id):
            return item
    return None


def list_parts(project_id: str) -> List[Dict[str, Any]]:
    return _load_items(project_id)


# --------------------------------------------------------------------------- #
# 3 回填 BOM 行（Spec C7，行级临时口径）
# --------------------------------------------------------------------------- #
def _needs_binding(row: Dict[str, Any]) -> bool:
    """「这一行算不出尺寸」的判据：缺输入，或整行根本没有尺寸。

    刻意不用「长或宽任一为空」：像 `RB01001-P10`（`长度 = W/3 + 40`，只有长度一维）
    这种行，长度是表达式**真算出来的**，不能拿图纸零件的尺寸去覆盖它（Spec C7.6）。
    """
    if _text(row.get("bom_category")) not in BINDABLE_CATEGORIES:
        return False
    if int(row.get("locked") or 0) == 1:
        return False
    if _text(row.get("status")) == "needs_input":
        return True
    return _num(row.get("length_mm")) is None and _num(row.get("width_mm")) is None


def _size_source_of(row: Dict[str, Any]) -> Dict[str, Any]:
    """把行上的尺寸溯源读成 dict（`_assemble` 给的是 JSON 字符串）。"""
    raw = row.get("size_source")
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    text = _text(row.get("size_source_json"))
    if not text:
        return {}
    try:
        import json
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def bind_rows(items: Any, parts: Any, *, options: Any = None) -> Dict[str, Any]:
    """把图纸零件回填进算不出尺寸的 BOM 行；纯函数：不改入参、不落库。

    - 只碰 `box_part` / `optional_part` 且（`needs_input` 或长宽为空）的行，锁定行绝不碰；
    - 行按出现顺序（= 模板 `seq` 升序）↔ 零件按面积降序，逐行取件；
      零件比分出的行少时**循环取件**并保留 `fallback_paired=true` 留痕
      —— 口径是"先保证有数"，正式对应表是下一批的事；
    - 留痕齐备：`size_source.dwg_binding`（component_id / part_code / rule_id /
      fallback_paired / original_missing_variables）、`source="dwg_parts"`、
      `missing_variables` 清空。
    """
    rows = [copy.deepcopy(row) for row in (items or []) if isinstance(row, dict)]
    available = [row for row in ((parts or {}).get("parts") or [])
                 if isinstance(row, dict)
                 and _num(row.get("unfolded_length_mm")) is not None
                 and _num(row.get("unfolded_width_mm")) is not None]
    bound = 0
    skipped_locked = 0
    unbound: List[str] = []
    pair_index = 0
    for row in rows:
        item_key = _text(row.get("item_key"))
        if _text(row.get("bom_category")) in BINDABLE_CATEGORIES \
                and int(row.get("locked") or 0) == 1 \
                and (_text(row.get("status")) == "needs_input"
                     or (_num(row.get("length_mm")) is None
                         and _num(row.get("width_mm")) is None)):
            skipped_locked += 1
            continue
        if not _needs_binding(row):
            continue
        if not available:
            unbound.append("part_size_unbound:%s" % item_key)
            continue
        part = available[pair_index % len(available)]
        fallback = pair_index >= len(available)
        pair_index += 1
        original_missing = list(row.get("missing_variables") or [])
        source = _size_source_of(row)
        source["dwg_binding"] = {
            "component_id": _text(part.get("component_id")),
            "part_code": _text(part.get("part_code")),
            "rule_id": BINDING_RULE_ID,
            "fallback_paired": bool(fallback),
            "original_missing_variables": original_missing,
        }
        row["length_mm"] = _num(part.get("unfolded_length_mm"))
        row["width_mm"] = _num(part.get("unfolded_width_mm"))
        row["size_source"] = source
        row["size_source_json"] = copy.deepcopy(source)
        row["missing_variables"] = []
        row["source"] = "dwg_parts"
        row["status"] = "computed"
        bound += 1
    return {"items": rows, "bound": bound, "unbound": unbound,
            "skipped_locked": skipped_locked, "gaps": list(unbound),
            "rule_id": BINDING_RULE_ID, "engine_version": ENGINE_VERSION}
