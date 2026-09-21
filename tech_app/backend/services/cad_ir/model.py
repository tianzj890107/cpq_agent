"""CAD IR 数据结构：版本、规范排序、规范哈希、摘要与迁移（DWG 第 3 批 Spec §3.4 / §6.3）。

哈希口径：`sha256(canonical_json(IR 去掉 ir_id/ir_hash/parser.version/时间戳))`。
因此**同一份 DXF 无论实体书写顺序、图层声明顺序如何，哈希都一致**；也正因如此，
任何随文件字节变化的东西（如 DXF 内容 sha256）都不在本模块里写。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

#: IR 的契约版本（写进每条 IR；第 4 批按它判定）
CAD_IR_VERSION = "cad-ir/1"

#: 解析器标识（写进 ir["parser"]["name"]）
PARSER_NAME = "ezdxf"

#: 不参与哈希的键：自身标识、解析器版本、时间戳
_DROPPED_KEYS = ("ir_id", "ir_hash", "ts", "created_at", "updated_at", "parsed_at", "generated_at")

#: 摘要里最多带几条明细（Spec §7：摘要必须小，不许塞实体明细）
SUMMARY_LIMIT = 20


def canonical_json(value: Any) -> str:
    """规范 JSON：键排序、无多余空白、禁止 NaN/Infinity（Spec §2.1 要求 JSON 安全）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if key in _DROPPED_KEYS:
                continue
            if key == "parser" and isinstance(item, dict):
                out[key] = {k: v for k, v in item.items() if k != "version"}
                continue
            out[key] = _hash_payload(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_hash_payload(item) for item in value]
    return value


def ir_hash(ir: Dict[str, Any]) -> str:
    payload = _hash_payload(ir if isinstance(ir, dict) else {})
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def ir_id_of(ir: Dict[str, Any]) -> str:
    """ir_id 取规范哈希前 16 位：同内容同 id，天然幂等（Spec §6.2）。"""
    return ir_hash(ir)[:16]


def entity_sort_key(row: Dict[str, Any]) -> tuple:
    """实体规范排序：`(space, layer, handle_or_canonical_index, type)`（Spec §3.4）。"""
    return (str(row.get("space") or ""), str(row.get("layer") or ""),
            str(row.get("handle") or row.get("canonical_index") or ""), str(row.get("type") or ""))


def canonical_sort(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(list(rows or []), key=entity_sort_key)


def migrate(ir: Any) -> Dict[str, Any]:
    """版本迁移判定（Spec §6.3）：缺版本 → 重建；同版本 → ok；未知 → 报错不硬读。"""
    if not isinstance(ir, dict) or not ir.get("ir_version"):
        return {"status": "needs_rebuild", "reason": "missing_ir_version"}
    version = str(ir.get("ir_version"))
    if version == CAD_IR_VERSION:
        return {"status": "ok", "ir_version": version}
    raise ValueError("unsupported cad ir version")


def summarize(ir: Dict[str, Any]) -> Dict[str, Any]:
    """给 Agent / 看板用的安全摘要：只有统计与少量关键项，**不含 entities**（Spec §7）。"""
    source = ir.get("source") or {}
    units = ir.get("units") or {}
    document = ir.get("document") or {}
    layers = [{"name": str(row.get("name") or ""),
               "entity_count": row.get("entity_count"),
               "color": row.get("color"),
               "line_type": row.get("line_type")}
              for row in (ir.get("layers") or [])[:SUMMARY_LIMIT]]
    texts = [str(row.get("normalized_text") or "")
             for row in (ir.get("texts") or [])[:SUMMARY_LIMIT]]
    return {
        "ir_version": ir.get("ir_version"),
        "ir_id": ir.get("ir_id"),
        "ir_hash": ir.get("ir_hash"),
        "source": {key: source.get(key) for key in
                   ("kind", "project_id", "attachment_name", "drawing_version",
                    "converter_name", "converter_version", "converter_role", "fallback_used",
                    "detected_dwg_version", "conversion_status", "warning_count", "error_count")},
        "units": {"drawing_units": units.get("drawing_units"),
                  "unit_status": units.get("unit_status"),
                  "scale_to_mm": units.get("scale_to_mm"),
                  "candidates": list(units.get("candidates") or [])[:MAX_SUMMARY_CANDIDATES]},
        "document": {"dxf_version": document.get("dxf_version"),
                     "extents": list(document.get("extents") or []),
                     "model_space": document.get("model_space") or {},
                     "paper_space": document.get("paper_space") or {}},
        "stats": dict(ir.get("stats") or {}),
        "layers": layers,
        "texts": texts,
        "dimensions": [{"declared_value": row.get("declared_value"),
                        "measured_value": row.get("measured_value"),
                        "delta": row.get("delta")}
                       for row in (ir.get("dimensions") or [])[:SUMMARY_LIMIT]],
        "unsupported": [{"type": row.get("type"), "count": row.get("count")}
                        for row in (ir.get("unsupported") or [])[:SUMMARY_LIMIT]],
        "warnings": [str(row.get("code") or "") for row in (ir.get("warnings") or [])[:SUMMARY_LIMIT]],
        "gaps": _gaps(ir),
    }


#: 摘要里的单位候选上限（候选本身很短，但没必要全带）
MAX_SUMMARY_CANDIDATES = 3


def _gaps(ir: Dict[str, Any]) -> List[str]:
    """给下游看的「看不出什么」清单（Spec §7 / §13：不确定项要显式列出）。"""
    gaps: List[str] = []
    units = ir.get("units") or {}
    if units.get("unit_status") != "confirmed":
        gaps.append("unit_unconfirmed")
    if (ir.get("stats") or {}).get("unsupported_total"):
        gaps.append("unsupported_entities")
    for row in ir.get("warnings") or []:
        code = str(row.get("code") or "")
        if code and code not in gaps and code != "unit_unconfirmed":
            gaps.append(code)
    return gaps[:SUMMARY_LIMIT]
