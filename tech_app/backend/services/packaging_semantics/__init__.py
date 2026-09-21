"""包装图纸语义、刀线压痕线与需求字段证据（DWG 支持第 4 批）。

Spec：`docs/specs/packaging-drawing-semantics.md`。对外接口**冻结**（第 5 批直接调用）：

    SEMANTICS_VERSION = "packaging-semantics/1"
    capability() / analyze() / analyze_conversion() / load_semantics() / list_semantics()
    field_candidates() / apply_to_requirement() / summarize() / migrate()

两条铁律：
  · `analyze()` 是纯函数——不落盘、不调模型（模型只在 `use_model=True` 且给了栅格预览时经
    `model_assist` 调用一次）；
  · 写盘只经 `persistence.py`（包级 `load_semantics` / `list_semantics` / `apply_to_requirement`
    一律转调）。
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from ..file_preflight import FileCapabilityError
from . import fields as fields_mod
from . import geometry_semantics
from . import model
from . import model_assist
from . import persistence as persistence_mod
from . import provenance
from . import roles as roles_mod
from . import rules as rules_mod

#: Spec §2：语义文档版本号（冻结）。
SEMANTICS_VERSION = model.SEMANTICS_VERSION

#: 审计动作名（Spec §14，第 5 批要接）。
ACTION_ANALYZED = "packaging_semantics.analyzed"
ACTION_APPLIED = "packaging_semantics.applied"
ACTION_FAILED = "packaging_semantics.failed"

_REASON_OF_STATUS = {"conflict": "conflict", "missing": "missing",
                     "needs_confirmation": "needs_confirmation"}

#: Spec §2.3 / §3.3 —— 第 4b 批新增的两个**警告**码（不是 HTTP 错误码）。
WARNING_OUTLINE_FRAME_REJECTED = "PACKAGING_OUTLINE_SHEET_FRAME_REJECTED"
WARNING_PRODUCT_OUTLINE_UNCERTAIN = "PACKAGING_PRODUCT_OUTLINE_UNCERTAIN"

#: Spec §3.3 —— 拿不到产品级轮廓时 `unresolved[].reason` 的专属取值。
REASON_PRODUCT_OUTLINE_UNCERTAIN = "product_outline_uncertain"


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default


def capability() -> Dict[str, Any]:
    """Spec §2：配置与模型辅助的可观测状态（绝不抛异常）。"""
    cap = rules_mod.capability()
    cap["model_assist"] = ("off" if not model_assist.enabled()
                           else str(os.environ.get(model_assist.ENV_MODEL) or "auto").strip() or "auto")
    cap["semantics_version"] = SEMANTICS_VERSION
    return cap


# --------------------------------------------------------------------------- #
# 主体：CAD IR → 包装语义
# --------------------------------------------------------------------------- #
def _unresolved(fields_out: Dict[str, Any], unknown_layers: List[str],
                known: Any, box_status: str,
                reason_overrides: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    overrides = dict(reason_overrides or {})
    rows: List[Dict[str, Any]] = []
    for key in sorted(fields_out):
        entry = fields_out.get(key) or {}
        if entry.get("status") == "confirmed":
            continue
        rows.append({
            "field": key,
            "reason": overrides.get(key) or _REASON_OF_STATUS.get(str(entry.get("status")),
                                                                  "missing"),
            "status": str(entry.get("status") or "missing"),
            "evidence_refs": list(entry.get("evidence_refs") or [])[:4],
        })
    if unknown_layers:
        refs: List[str] = []
        for name in sorted(set(unknown_layers)):
            for ref in model.evidence_refs_of(known, "ev:L:%s" % name):
                if ref not in refs:
                    refs.append(ref)
        rows.append({
            "field": "box_type",
            "reason": "no_rule_matched",
            "status": str(box_status or "missing"),
            "layers": sorted(set(unknown_layers)),
            "evidence_refs": refs,
        })
    # 去重（同 field + reason）后按 (field, reason) 排序，保证哈希稳定。
    unique: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        unique.setdefault((row["field"], row["reason"]), row)
    return [unique[key] for key in sorted(unique)]


def _merge_warnings(known: Any, upstream: Any, extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: Dict[tuple, Dict[str, Any]] = {}
    for item in list(upstream or []) + list(extra or []):
        if not isinstance(item, dict):
            continue
        row = {
            "code": str(item.get("code") or ""),
            "message": str(item.get("message") or ""),
            "evidence_refs": model.evidence_refs_of(known, item.get("evidence_refs")
                                                    or item.get("evidence_ref")),
        }
        rows.setdefault((row["code"], row["message"]), row)
    return [rows[key] for key in sorted(rows)]


def analyze(ir: Dict[str, Any], *, template: Optional[str] = None, rules: Any = None,
            preview: Any = None, use_model: bool = False,
            options: Any = None) -> Dict[str, Any]:
    """把通用 CAD IR 转成包装语义 + 带证据的字段候选。**纯函数**：不落盘、不调模型（默认）。"""
    started = time.monotonic()
    if not isinstance(ir, dict):
        raise TypeError("analyze() 需要一份 CAD IR 文档")

    resolved = rules_mod.resolve_rule_set(rules)
    template_name, template_conf = rules_mod.resolve_template(resolved["rule_set"], template)

    known = {str(key) for key in (ir.get("evidence") or {})}
    layers, roles_summary, unknown_layers = roles_mod.resolve_layer_roles(
        ir, template_conf, known)

    max_candidates = _env_int("PACKAGING_SEMANTICS_MAX_CANDIDATES", 200)
    max_conflicts = _env_int("PACKAGING_SEMANTICS_MAX_CONFLICTS", 200)
    geometry = geometry_semantics.build_geometry(ir, layers, known, max_candidates)
    texts = fields_mod.build_texts(ir, known)
    built = fields_mod.build_fields(ir, layers, geometry, texts, known, max_conflicts)

    fields_out = built["fields"]
    box_candidates = built["box_candidates"]
    warnings = list(built["warnings"])
    if geometry.get("truncated"):
        warnings.append({"code": "PACKAGING_SEMANTICS_CANDIDATES_TRUNCATED",
                         "message": "轮廓/孔位候选超过上限，已截断，请人工核对图纸",
                         "evidence_refs": []})
    # Spec §2.3：有候选被排除（拼版/图框/整张）才出这条警告。
    rejected = list(geometry.get("rejected") or [])
    if rejected:
        rejected_refs: List[str] = []
        for row in rejected:
            for ref in row.get("evidence_refs") or []:
                if ref not in rejected_refs:
                    rejected_refs.append(ref)
        warnings.append({
            "code": WARNING_OUTLINE_FRAME_REJECTED,
            "message": "图纸里有拼版/图框/整张外框候选被排除，不计入成品尺寸与盒型候选，"
                       "请人工核对成品轮廓",
            "evidence_refs": rejected_refs,
        })
    uncertain_fields = list(built.get("outline_uncertain_fields") or [])
    if uncertain_fields:
        warnings.append({
            "code": WARNING_PRODUCT_OUTLINE_UNCERTAIN,
            "message": "拿不到产品级成品轮廓，成品长宽只能待人工确认（数量级可能缺失）",
            "evidence_refs": [],
        })

    assist = model_assist.evaluate(preview=preview, use_model=use_model, fields=fields_out,
                                   ir=ir, known=known)

    outline = {
        "boundary_candidates": geometry["boundary_candidates"],
        "rejected": rejected,
        "bleed_candidates": geometry["bleed_candidates"],
        "windows": geometry["windows"],
        "holes": geometry["holes"],
        "panel": geometry["panel"],
    }
    reason_overrides = {str(key): REASON_PRODUCT_OUTLINE_UNCERTAIN for key in uncertain_fields}
    unresolved = _unresolved(fields_out, unknown_layers, known,
                             (fields_out.get("box_type") or {}).get("status"),
                             reason_overrides)
    src = ir.get("source") or {}
    source = {
        "project_id": str(src.get("project_id") or ""),
        "ir_id": str(ir.get("ir_id") or ""),
        "ir_hash": str(ir.get("ir_hash") or ""),
        "ir_version": str(ir.get("ir_version") or ""),
        "drawing_version": src.get("drawing_version"),
        "conversion_status": str(src.get("conversion_status") or ""),
        "warning_count": int(src.get("warning_count") or 0),
        "error_count": int(src.get("error_count") or 0),
        "template": template_name,
        "rules_version": resolved["rules_version"],
        "rules_path_kind": resolved["path_kind"],
    }
    stats = {
        "layer_total": len(layers),
        "cut_layer_total": sum(1 for row in layers if row.get("role") == "cut"),
        "crease_layer_total": sum(1 for row in layers if row.get("role") == "crease"),
        "boundary_candidate_total": len(outline["boundary_candidates"]),
        "hole_total": len(outline["holes"]),
        "conflict_total": len(built["dimensions"]["conflicts"]),
        "unresolved_total": len(unresolved),
        "box_candidate_total": len(box_candidates),
    }
    doc: Dict[str, Any] = {
        "semantics_version": SEMANTICS_VERSION,
        "semantics_id": "",
        "semantics_hash": "",
        "source": source,
        "layers": layers,
        "roles_summary": roles_summary,
        "outline": outline,
        "dimensions": built["dimensions"],
        "texts": {
            "material_candidates": texts["material_candidates"],
            "process_candidates": texts["process_candidates"],
            "title_block": {},
            "unit_hints": texts["unit_hints"],
        },
        "box_candidates": box_candidates,
        "fields": fields_out,
        "unresolved": unresolved,
        "model_assist": model_assist_block(assist),
        "warnings": _merge_warnings(known, ir.get("warnings"), warnings),
        "stats": stats,
        "reviewable": True,
    }
    digest = model.semantics_hash(doc)
    doc["semantics_hash"] = digest
    doc["semantics_id"] = model.semantics_id(ir, rules_version=resolved["rules_version"],
                                             template=template_name, options=options,
                                             digest=digest)
    _check_timeout(started, ir)
    return doc


def model_assist_block(assist: Dict[str, Any]) -> Dict[str, Any]:
    """只把可审计的模型元数据写进文档；预览字节 / 提示词 / 思维链一律不落盘。"""
    return {
        "used": bool(assist.get("used")),
        "status": str(assist.get("status") or "unavailable"),
        "calls": int(assist.get("calls") or 0),
        "model": str(assist.get("model") or ""),
        "stable_error_code": str(assist.get("stable_error_code") or ""),
        "preview_kind": str(assist.get("preview_kind") or "none"),
        "evidence_level": str(assist.get("evidence_level") or "NONE"),
        "extra_fields_dropped": list(assist.get("extra_fields_dropped") or []),
        "title_block": assist.get("title_block") or {},
        "material_candidates": list(assist.get("material_candidates") or []),
        "process_candidates": list(assist.get("process_candidates") or []),
        "box_candidates": list(assist.get("box_candidates") or []),
        "layer_interpretations": list(assist.get("layer_interpretations") or []),
        "open_questions": list(assist.get("open_questions") or []),
    }


def _check_timeout(started: float, ir: Dict[str, Any]) -> None:
    limit = _env_int("PACKAGING_SEMANTICS_TIMEOUT_SECONDS", 60)
    if limit and (time.monotonic() - started) > limit:
        raise FileCapabilityError("DWG_PARSE_FAILED",
                                  detected={"stage": "packaging_semantics",
                                            "ir_id": str(ir.get("ir_id") or "")},
                                  message="包装图纸语义分析超时，请重试")


def analyze_conversion(project_id: str, *, ir: Any = None, ir_id: Optional[str] = None,
                       template: Optional[str] = None, rules: Any = None,
                       preview: Any = None, use_model: bool = False,
                       author: str = "system") -> Dict[str, Any]:
    """项目级入口：给了 `ir` 就直接用（不查 cad_ir）；否则从第 3 批产物加载。"""
    if ir is None:
        from .. import cad_ir  # 延迟导入：只在真的要从项目里取 IR 时依赖第 3 批

        ir = cad_ir.load_ir(project_id, ir_id) if ir_id else cad_ir.load_ir(project_id)
    if not isinstance(ir, dict):
        persistence_mod.audit(project_id, ACTION_FAILED,
                              {"reason": "source_missing", "by": author})
        raise FileCapabilityError("PACKAGING_SEMANTICS_SOURCE_MISSING",
                                  detected={"project_id": str(project_id or ""),
                                            "ir_id": str(ir_id or "")},
                                  message="项目里没有可用的 CAD 图纸解析结果，请先重跑图纸解析")

    doc = analyze(ir, template=template, rules=rules, preview=preview, use_model=use_model)
    doc["source"]["project_id"] = str(project_id or "")

    for existing in persistence_mod.list_semantics(project_id) or []:
        if existing.get("semantics_id") == doc.get("semantics_id"):
            return existing
    persistence_mod.save_semantics(project_id, doc)
    persistence_mod.audit(project_id, ACTION_ANALYZED, {
        "semantics_id": doc.get("semantics_id"),
        "ir_id": doc["source"].get("ir_id"),
        "rules_version": doc["source"].get("rules_version"),
        "by": author,
    })
    return doc


# --------------------------------------------------------------------------- #
# 落盘 / 摘要 / 迁移（一律转调，方便第 5 批与红测在 persistence 层打桩）
# --------------------------------------------------------------------------- #
def load_semantics(project_id: str,
                   semantics_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return persistence_mod.load_semantics(project_id, semantics_id)


def list_semantics(project_id: str) -> List[Dict[str, Any]]:
    return persistence_mod.list_semantics(project_id)


def field_candidates(semantics: Dict[str, Any]) -> Dict[str, Any]:
    return provenance.field_candidates(semantics)


def apply_to_requirement(project_id: str, semantics: Dict[str, Any], *,
                         accept: Any = (), author: str = "system") -> Dict[str, Any]:
    return provenance.apply_to_requirement(project_id, semantics, accept=accept, author=author)


def summarize(semantics: Dict[str, Any]) -> Dict[str, Any]:
    return provenance.summarize(semantics)


def migrate(semantics: Any) -> Dict[str, Any]:
    return model.migrate(semantics)


__all__ = [
    "SEMANTICS_VERSION", "capability", "analyze", "analyze_conversion", "load_semantics",
    "list_semantics", "field_candidates", "apply_to_requirement", "summarize", "migrate",
]
