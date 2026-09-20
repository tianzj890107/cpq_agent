"""图层 → 角色（DWG 第 4 批 Spec §4）。

匹配优先级：名称 → 颜色 → 线型。
  · 名称命中：沿用配置里的 evidence_level / confidence；
  · 颜色命中：只在客户模板显式配了 `colors` 时生效（role_source=color_rule）；
  · 线型命中：**只能是弱证据** —— evidence_level=WEAK、role_confidence<=0.5、
    role_source=line_type_weak，且必须进 needs_confirmation；
  · 都没命中：role=unknown、role_confidence<=0.3、role_source=none，并进 unresolved。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import model

#: 线型命中时的置信度上界（Spec §4：线型只能作弱证据）。
LINE_TYPE_CONFIDENCE = 0.5

#: 颜色命中时的置信度（客户模板显式声明，仍低于名称强规则）。
COLOR_CONFIDENCE = 0.7

#: 未命中时的置信度（Spec §4：<=0.3）。
UNKNOWN_CONFIDENCE = 0.3


def _int_of(value: Any) -> int:
    """脏数值（字符串/None/NaN）一律归 0，绝不向上冒泡（上游字段不一定可信）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number != number or number in (float("inf"), float("-inf")):
        return 0
    return int(number)


def _clamp01(value: Any) -> float:
    """图层角色的置信度用规则自带值（Spec §3 示例 CUT=0.9），只做 [0,1] 收敛。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return round(max(0.0, min(1.0, number)), 4)


def _normalized(value: Any) -> str:
    return str(value or "").strip().upper()


def _match_rule(layer: Dict[str, Any], rule: Dict[str, Any]) -> bool:
    name = _normalized(layer.get("name"))
    if not name:
        return False
    for candidate in rule.get("names") or ():
        if name == _normalized(candidate):
            return True
    for prefix in rule.get("name_prefix") or ():
        if prefix and name.startswith(_normalized(prefix)):
            return True
    return False


def resolve_layer(layer: Dict[str, Any], template: Dict[str, Any],
                  known_evidence: Any) -> Dict[str, Any]:
    name = str(layer.get("name") or "")
    refs = model.evidence_refs_of(known_evidence, "ev:L:%s" % name)

    for rule in template.get("layers") or []:
        if _match_rule(layer, rule):
            return {
                "name": name, "role": rule["role"],
                "role_confidence": _clamp01(rule.get("confidence")),
                "evidence_level": rule.get("evidence_level") or "MODERATE",
                "matched_rule_id": rule.get("rule_id") or "",
                "role_source": "rule",
                "color": layer.get("color"), "line_type": layer.get("line_type"),
                "entity_count": _int_of(layer.get("entity_count")),
                "evidence_refs": refs,
            }

    color_key = "" if layer.get("color") is None else str(layer.get("color")).strip()
    color_role = (template.get("colors") or {}).get(color_key) if color_key else None
    if color_role:
        return {
            "name": name, "role": color_role,
            "role_confidence": _clamp01(COLOR_CONFIDENCE),
            "evidence_level": "MODERATE",
            "matched_rule_id": "color:%s" % color_key,
            "role_source": "color_rule",
            "color": layer.get("color"), "line_type": layer.get("line_type"),
            "entity_count": _int_of(layer.get("entity_count")),
            "evidence_refs": refs,
        }

    line_key = _normalized(layer.get("line_type"))
    line_role = (template.get("line_types") or {}).get(line_key) if line_key else None
    if line_role:
        return {
            "name": name, "role": line_role,
            "role_confidence": _clamp01(LINE_TYPE_CONFIDENCE),
            "evidence_level": "WEAK",
            "matched_rule_id": "line_type:%s" % line_key,
            "role_source": "line_type_weak",
            "color": layer.get("color"), "line_type": layer.get("line_type"),
            "entity_count": _int_of(layer.get("entity_count")),
            "evidence_refs": refs,
        }

    return {
        "name": name, "role": "unknown",
        "role_confidence": UNKNOWN_CONFIDENCE,
        "evidence_level": "NONE",
        "matched_rule_id": "",
        "role_source": "none",
        "color": layer.get("color"), "line_type": layer.get("line_type"),
        "entity_count": _int_of(layer.get("entity_count")),
        "evidence_refs": refs,
    }


def resolve_layer_roles(ir: Dict[str, Any], template: Dict[str, Any],
                        known_evidence: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    """返回 (图层行, roles_summary, 未命中图层名)。图层行按 name 排序保证哈希稳定。"""
    layers = [layer for layer in (ir.get("layers") or []) if isinstance(layer, dict)]
    rows = [resolve_layer(layer, template, known_evidence) for layer in layers]
    rows.sort(key=lambda row: row["name"])

    summary: Dict[str, Dict[str, int]] = {}
    unknown: List[str] = []
    for row in rows:
        bucket = summary.setdefault(row["role"], {"layer_count": 0, "entity_count": 0})
        bucket["layer_count"] += 1
        bucket["entity_count"] += _int_of(row.get("entity_count"))
        if row["role"] == "unknown" and row["name"]:
            unknown.append(row["name"])
    return rows, summary, unknown


def role_of(layers: List[Dict[str, Any]], layer_name: Any) -> str:
    name = str(layer_name or "")
    for row in layers or []:
        if row.get("name") == name:
            return str(row.get("role") or "unknown")
    return "unknown"
