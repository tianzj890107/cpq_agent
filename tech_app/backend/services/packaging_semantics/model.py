"""包装图纸语义的数据结构、枚举闭集与规范化哈希（DWG 第 4 批 Spec §3/§5/§7）。

本模块只放**数据**：闭集、置信度上界、规范排序/哈希、模型输出 schema。
判定逻辑分别在 rules / roles / geometry_semantics / fields / provenance /
model_assist 里；落盘只在 persistence 里。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

#: Spec §2：语义文档版本号（第 5 批直接依赖，不许改）。
SEMANTICS_VERSION = "packaging-semantics/1"

#: 语义文档在项目存储里的 doc key（Spec §2）。
SEMANTICS_DOC_KEY = "packaging_semantics"

#: Spec §5.1 —— 字段来源闭集。
ORIGINS = ("confirmed_from_cad", "inferred_from_geometry", "inferred_from_text",
           "inferred_by_model", "user_confirmed", "conflict", "missing")

#: Spec §5.1 —— 字段状态闭集。
STATUSES = ("confirmed", "needs_confirmation", "conflict", "missing")

#: Spec §4 —— 图层角色闭集（unknown 只能来自「没命中」，不许写进配置）。
ROLES = ("cut", "crease", "half_cut", "v_groove", "glue_flap", "print", "bleed",
         "frame", "hole", "unknown")

#: 证据强度闭集。
EVIDENCE_LEVELS = ("STRONG", "MODERATE", "WEAK", "CONTRADICTORY", "NONE")

#: Spec §7 —— 盒型候选闭集。
BOX_TYPES = ("telescope_lid_base", "drawer", "book_style", "folding_carton",
             "round_tube", "irregular", "unknown")

#: Spec §5.1 —— 每个 origin 的置信度上界（超过即截断到这个值）。
CONFIDENCE_CAPS: Dict[str, float] = {
    "confirmed_from_cad": 1.0,
    "inferred_from_geometry": 0.8,
    "inferred_from_text": 0.7,
    "inferred_by_model": 0.6,
    "user_confirmed": 1.0,
    "conflict": 0.0,
    "missing": 0.0,
}

#: Spec §6.1 —— 允许模型输出的顶层键闭集（其余一律算越权）。
MODEL_OUTPUT_KEYS = ("title_block", "material_candidates", "process_candidates",
                     "box_candidates", "layer_interpretations", "open_questions")

#: Spec §7 —— 本批允许写需求看板的字段键（键名取自 industry_templates.PACKAGING_SPEC，
#: 不许自创；白名单外的字段只进 unresolved）。
FIELD_WHITELIST = (
    "inner_length", "inner_width", "inner_height", "box_type", "box_family",
    "closure_type", "v_groove", "grey_board", "grey_board_thickness", "face_paper",
    "face_paper_gsm", "insert_type", "print_colors", "lamination", "hot_stamping",
    "uv_coating", "emboss_deboss", "silk_screen", "die_cutting", "mounting",
    "special_process", "units_per_carton", "carton_size",
)

#: 结构尺寸组：一个字段冲突时同组字段一起不得被视为已确认（Spec §5.2 第 4 条）。
DIMENSION_GROUP = ("inner_length", "inner_width", "inner_height")

DEFAULT_CONFLICT_TOLERANCE = 0.5


class PackagingAssistResult(BaseModel):
    """模型辅助输出的 schema（Spec §6.1）。

    `extra="allow"` 是硬要求：越权键必须能在 `__pydantic_extra__` 里被看见并计入
    `extra_fields_dropped`，不许用 `extra="ignore"` 悄悄吞掉。
    """

    model_config = ConfigDict(extra="allow")

    title_block: Dict[str, Any] = Field(default_factory=dict)
    material_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    process_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    box_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    layer_interpretations: List[Dict[str, Any]] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)


def canonical_json(value: Any) -> str:
    """规范化 JSON：键排序 + 紧凑分隔，保证同输入同字符串（→ 同哈希）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def sha256_hex(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
    else:
        data = str(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def json_safe(value: Any) -> Any:
    """把值收敛成 JSON 安全形式（NaN/Infinity → None，bytes/Path → str）。"""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, (bytes, bytearray)):
        return "<bytes>"
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def cap_confidence(origin: str, confidence: Any) -> float:
    """按 Spec §5.1 的上界表截断置信度，并保证是 [0,1] 内的有限数。"""
    try:
        number = float(confidence)
    except (TypeError, ValueError):
        number = 0.0
    if number != number or number in (float("inf"), float("-inf")):
        number = 0.0
    ceiling = CONFIDENCE_CAPS.get(str(origin), 0.0)
    return round(max(0.0, min(ceiling, number)), 4)


def packaging_field_keys() -> tuple:
    """Spec §7 白名单 ∩ `industry_templates.PACKAGING_SPEC`，顺序取模板声明顺序。

    取交集是刻意的：任何键都必须真实存在于包装需求模板，绝不自创字段名。
    """
    from .. import industry_templates

    allowed = set(FIELD_WHITELIST)
    keys: List[str] = []
    for block in getattr(industry_templates, "PACKAGING_SPEC", ()) or ():
        for spec_field in getattr(block, "fields", ()) or ():
            key = str(getattr(spec_field, "key", "") or "")
            if key and key in allowed and key not in keys:
                keys.append(key)
    return tuple(keys)


def evidence_refs_of(known: Any, *refs: Any) -> List[str]:
    """只保留能在输入 CAD IR 的 `evidence` 里回查到的引用（Spec §3 / 红测 A8）。"""
    pool = known if isinstance(known, (set, frozenset, dict, list, tuple)) else ()
    out: List[str] = []
    for ref in refs:
        if isinstance(ref, (list, tuple, set)):
            out.extend(evidence_refs_of(known, *ref))
            continue
        text = str(ref or "")
        if text and text in pool and text not in out:
            out.append(text)
    return out


def field_entry(ir: Dict[str, Any], *, origin: str, status: str, value: Any = None,
                confidence: Any = 0.0, evidence_level: str = "NONE",
                evidence_refs: Optional[List[str]] = None,
                conflicts: Optional[List[dict]] = None,
                alternatives: Optional[List[dict]] = None) -> Dict[str, Any]:
    """构造一条字段留痕（Spec §3 fields 结构，键集固定）。"""
    return {
        "origin": str(origin),
        "status": str(status),
        "value": json_safe(value),
        "confidence": cap_confidence(origin, confidence),
        "evidence_level": evidence_level if evidence_level in EVIDENCE_LEVELS else "NONE",
        "evidence_refs": list(evidence_refs or []),
        "conflicts": list(conflicts or []),
        "alternatives": list(alternatives or []),
        "ir_id": str(ir.get("ir_id") or ""),
        "ir_hash": str(ir.get("ir_hash") or ""),
    }


def missing_entry(ir: Dict[str, Any]) -> Dict[str, Any]:
    return field_entry(ir, origin="missing", status="missing", value=None, confidence=0.0,
                       evidence_level="NONE")


def semantics_hash(doc: Dict[str, Any]) -> str:
    """语义哈希：排除 semantics_id / semantics_hash 自身后的规范 JSON 摘要。"""
    body = {key: value for key, value in doc.items()
            if key not in ("semantics_id", "semantics_hash")}
    return sha256_hex(canonical_json(body))


def semantics_id(ir: Dict[str, Any], *, rules_version: str, template: str,
                 options: Any = None, digest: str = "") -> str:
    """版本锚点：同一份 IR + 同一份规则 + 同一模板/选项 → 同一个 id（Spec §8）。"""
    seed = "|".join([str(ir.get("ir_id") or ""), str(ir.get("ir_hash") or ""),
                     str(rules_version or ""), str(template or ""),
                     canonical_json(json_safe(options if options is not None else {}))
                     if isinstance(options, (dict, list)) else str(options or ""),
                     str(digest or "")])
    return sha256_hex(seed)[:16]


def migrate(semantics: Any) -> Dict[str, Any]:
    """Spec §8 的三分支：缺版本 / 同版本 / 未知或更高版本。"""
    if not isinstance(semantics, dict) or "semantics_version" not in semantics:
        return {"status": "needs_rebuild", "reason": "missing_semantics_version"}
    version = str(semantics.get("semantics_version") or "")
    if version == SEMANTICS_VERSION:
        return {"status": "ok", "semantics_version": version}
    raise ValueError("unsupported packaging semantics version")
