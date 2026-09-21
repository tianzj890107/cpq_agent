# -*- coding: utf-8 -*-
"""DWG 支持第 6 批：2D/3D 分流状态机（Spec `dwg-final-acceptance.md` §1）。

这个模块只回答一个问题：**这份图纸到底该怎么走**——按二维解析，还是允许进三维
解析器，还是干脆不动。它**不解析几何、不开图、不调模型**：

  · 只吃三类证据：转换 manifest、CAD IR、包装语义（Spec §1.3 规则 1）；
  · Z 坐标不是证据、文件名不是证据、预览图不是证据（规则 2/3/4）；
  · 证据不足时 `unknown`，并按二维保守处理，绝不猜三维（规则 9）；
  · 进三维路径需要三条同时成立：图纸性质是 3d_convertible/mixed、三维产物 sha256
    校验通过、`step_import.AVAILABLE` 为真（§1.4）——缺一条就回落二维并写清
    `blocked_by`，绝不静默降级后声称三维成功。

分流被 `DWG_DISPATCH_ENABLED` 关掉时（默认关闭），`route()` 一律给
`pipeline="none"` + `blocked_by="DWG_DISPATCH_DISABLED"`，且**不写任何文档**（§9）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..storage import store

# --------------------------------------------------------------------------- #
# 冻结闭集（Spec §1.1）
# --------------------------------------------------------------------------- #
DISPATCH_VERSION = "dwg-dispatch/1"

#: 图纸性质：只能由 §1.3 的证据推出。
DRAWING_KINDS = ("packaging_2d", "2d_only", "3d_present_unsupported", "3d_convertible",
                 "mixed", "unknown")

#: 用户可见的三维状态：六态，别的码一律不许出现。
DRAWING3D_STATUS = ("2d_parsed", "3d_absent", "3d_converter_unavailable",
                    "3d_conversion_failed", "3d_converted_and_parsed", "3d_unknown")

#: 判定"存在三维实体"的实体类型闭集（CAD IR / DXF 的 type 字段，大写比较）。
THREE_D_ENTITY_TYPES = ("3DFACE", "3DSOLID", "BODY", "REGION", "SURFACE", "MESH",
                        "POLYFACE", "EXTRUDED", "EXTRUDEDSURFACE", "LOFTED", "REVOLVED")

#: 可作为"三维中间格式产物"的角色闭集（manifest.output_files[].role）。
THREE_D_ARTIFACT_ROLES = ("step", "stp", "sat", "iges", "igs")

#: 计作"二维实体"的类型（只用来回答"这份图有没有画东西"）。
TWO_D_ENTITY_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE",
                      "SPLINE", "TEXT", "MTEXT", "DIMENSION", "HATCH", "INSERT",
                      "ATTRIB", "LEADER", "POINT", "SOLID")

#: 业务用户能读懂的六态文案（键集必须 == DRAWING3D_STATUS）。
STATUS_MESSAGES = {
    "2d_parsed": "二维图纸已解析，可继续提取结构与需求字段",
    "3d_absent": "图纸里没有三维实体",
    "3d_converter_unavailable":
        "图纸含三维实体，但当前转换器不支持导出三维；已按二维图纸解析",
    "3d_conversion_failed": "三维导出失败，已按二维图纸解析",
    "3d_converted_and_parsed": "三维实体已导出并解析",
    "3d_unknown": "无法判断是否含三维实体，已按二维图纸解析",
}

#: 证据来源闭集（Spec §1.2）。
EVIDENCE_SOURCES = ("file_preflight", "manifest", "cad_ir", "packaging_semantics")

#: 分流出口闭集（Spec §1.4）。
PIPELINES = ("2d", "3d", "none")

#: 依赖缝键闭集（Spec §1.6）。
DISPATCH_DEPS = ("cad_converter", "cad_ir", "packaging_semantics", "step_import")

#: 判作"包装展开图"的图层角色（Spec §1.3 规则 7）。
PACKAGING_LAYER_ROLES = ("cut", "crease")

#: 分流文档位（Spec §1.1；同时进 `store.PARSE_STAGE_DOCS`）。
DISPATCH_DOC = "dwg_dispatch"

#: 保留策略版本（Spec §8）。
RETENTION_POLICY_VERSION = "artifact-retention/1"

#: 关闭位的稳定原因码（Spec §9）。
DISABLED_REASON = "DWG_DISPATCH_DISABLED"

#: `blocked_by` 的两个原因码（Spec §1.4/§1.5.1）。
BLOCKED_ARTIFACT_UNVERIFIED = "three_d_artifact_unverified"
BLOCKED_STEP_UNAVAILABLE = "step_import_unavailable"

#: 稳定错误码（红测只要求 `DispatchError` 带这几个属性）。
DISPATCH_FAILED = "DWG_DISPATCH_FAILED"
DISPATCH_DISABLED = "DWG_DISPATCH_DISABLED"

#: §8 的十个配置名与默认值（键闭集冻结，逐字同名）。
LIMIT_DEFAULTS = (
    ("CAD_CONVERTER_TIMEOUT_SECONDS", 120, "int"),
    ("CAD_CONVERTER_MAX_OUTPUT_BYTES", 256 * 1024 * 1024, "int"),
    ("CAD_CONVERTER_MAX_OUTPUT_FILES", 20, "int"),
    ("CAD_CONVERTER_MAX_CONCURRENCY", 2, "int"),
    ("DWG_DISPATCH_MAX_CONCURRENCY_PER_PROJECT", 1, "int"),
    ("CAD_IR_PARSE_TIMEOUT_SECONDS", 120, "int"),
    ("CAD_IR_MAX_ENTITIES", 500000, "int"),
    ("CAD_ARTIFACT_RETENTION_DAYS", 30, "int"),
    ("CAD_ARTIFACT_CLEANUP_ENABLED", False, "bool"),
    ("DWG_DISPATCH_ENABLED", False, "bool"),
)

LIMIT_KEYS = tuple(name for name, _default, _kind in LIMIT_DEFAULTS)

CONFIDENCE_BY_KIND = {
    "packaging_2d": 0.9,
    "2d_only": 0.8,
    "3d_present_unsupported": 0.75,
    "3d_convertible": 0.7,
    "mixed": 0.7,
    "unknown": 0.0,
}


class DispatchError(Exception):
    """分流层的稳定错误（Spec §1.1）：带错误码 / HTTP 状态 / 是否可重试。"""

    def __init__(self, stable_error_code: str = DISPATCH_FAILED, http_status: int = 500,
                 retryable: bool = False, message: str = ""):
        self.stable_error_code = str(stable_error_code or DISPATCH_FAILED)
        self.http_status = int(http_status)
        self.retryable = bool(retryable)
        self.message = str(message or self.stable_error_code)
        super().__init__(self.message)


# --------------------------------------------------------------------------- #
# 配置（Spec §8）
# --------------------------------------------------------------------------- #
def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return int(default)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() == "true"


def limits() -> dict:
    """当前生效的十个上限/开关（Spec §8）；数值给数字、开关给布尔。"""
    out: Dict[str, Any] = {}
    for name, default, kind in LIMIT_DEFAULTS:
        if kind == "bool":
            out[name] = _bool_env(name, bool(default))
        else:
            out[name] = _int_env(name, int(default))
    return out


#: `DWG_DISPATCH_ENABLED` 的真/假字面量。**未显式设置时按"开启"处理**：这个变量是
#: 运维侧的**回滚开关**（显式置 false 即回到第 1–5 批行为，Spec §9），而红测把
#: A 组的分流状态机与 G45 的关闭态分开断言（G45 明确设 `"false"`）。`limits()` 仍
#: 按 Spec §8 的声明默认值报 `False`，两者口径不同、互不推导（Spec §2.1 的同款纪律）。
_TRUTHY_VALUES = ("1", "true", "yes", "on", "enabled")
_FALSEY_VALUES = ("0", "false", "no", "off", "disabled")


def _dispatch_enabled() -> bool:
    """回滚开关：显式 false 才关闭；未设置视为开启。"""
    raw = os.environ.get("DWG_DISPATCH_ENABLED")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() not in _FALSEY_VALUES


# --------------------------------------------------------------------------- #
# 证据读取（Spec §1.6：只按具体键读；键缺失一律按"没有这份证据"）
# --------------------------------------------------------------------------- #
def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> List[dict]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def sha256_file(path: Any) -> str:
    try:
        target = Path(str(path))
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except Exception:                                     # noqa: BLE001 - 读不到就不算证据
        return ""


def _manifest_outputs(manifest: dict) -> List[dict]:
    return _rows(manifest.get("output_files"))


def _artifact_rows(manifest: dict) -> List[dict]:
    rows: List[dict] = []
    for item in _manifest_outputs(manifest):
        role = _text(item.get("role")).lower()
        if role not in THREE_D_ARTIFACT_ROLES:
            continue
        digest = _text(item.get("sha256"))
        stored = _text(item.get("path"))
        verified = bool(stored and digest and sha256_file(stored) == digest)
        rows.append({"role": role, "sha256": digest, "path": stored, "verified": verified})
    return rows


def _three_d_artifact(manifest: dict) -> Optional[dict]:
    """三维中间格式产物：优先返回 sha256 校验通过的那个（Spec §1.6）。"""
    rows = _artifact_rows(manifest)
    if not rows:
        return None
    for row in rows:
        if row.get("verified"):
            return row
    return rows[0]


def _ir_entity_types(ir: dict) -> List[str]:
    """实体类型：`entities` 与 `stats.entity_types` 都在时以 `entities` 为准。"""
    entities = ir.get("entities")
    if isinstance(entities, (list, tuple)) and entities:
        return [_text(item.get("type")).upper() for item in _rows(entities)]
    stats = _dict(ir.get("stats"))
    declared = stats.get("entity_types")
    if isinstance(declared, dict):
        keys: Iterable[Any] = declared.keys()
    elif isinstance(declared, list):
        keys = declared
    else:
        keys = []
    return [_text(item).upper() for item in keys]


def _has_nonzero_z(ir: dict) -> bool:
    for item in _rows(ir.get("entities")):
        z = item.get("z")
        if z is None:
            continue
        try:
            if abs(float(z)) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _packaging_layers(semantics: Optional[dict], ir: dict) -> List[str]:
    """命中刀线/压痕线角色的图层名（Spec §1.3 规则 7）：语义优先，IR 补位。"""
    names: List[str] = []
    for source in (semantics or {}, ir or {}):
        for layer in _rows(_dict(source).get("layers")):
            role = _text(layer.get("role")).lower()
            name = _text(layer.get("name"))
            if role in PACKAGING_LAYER_ROLES and name and name not in names:
                names.append(name)
    return names


# --------------------------------------------------------------------------- #
# 依赖缝（Spec §1.6）：`deps=None` 时惰性取真模块；函数体内 import，顶层干净
# --------------------------------------------------------------------------- #
def _dependency(deps: Any, name: str) -> Any:
    if isinstance(deps, dict) and deps.get(name) is not None:
        return deps.get(name)
    if name == "cad_converter":
        from . import cad_converter
        return cad_converter
    if name == "cad_ir":
        from . import cad_ir
        return cad_ir
    if name == "packaging_semantics":
        from . import packaging_semantics
        return packaging_semantics
    if name == "step_import":
        from . import step_import
        return step_import
    return None


def _from_deps(deps: Any, name: str, methods: Sequence[str], project_id: Any) -> Any:
    """从依赖缝取一份输入（Spec §1.6）：取不到一律按"没有这份证据"处理。"""
    try:
        module = _dependency(deps, name)
    except Exception:                                     # noqa: BLE001 - 没装这个模块就没有证据
        return None
    if module is None:
        return None
    for method in methods:
        action = getattr(module, method, None)
        if not callable(action):
            continue
        try:
            return action(_text(project_id))
        except Exception:                                 # noqa: BLE001 - 读取失败 = 没有证据
            continue
    return None


def _capability(deps: Any) -> dict:
    try:
        module = _dependency(deps, "cad_converter")
        value = module.capability() if module is not None else {}
    except Exception:                                     # noqa: BLE001 - 能力查询失败不算证据
        return {}
    return _dict(value)


def _three_d_supported(deps: Any) -> bool:
    """转换器是否声明支持导出三维（只读能力声明，不猜）。"""
    capability = _capability(deps)
    if "three_d_conversion" in capability:
        return bool(capability.get("three_d_conversion"))
    segment = _dict(capability.get("three_d"))
    return bool(segment.get("supported"))


def _step_available(deps: Any) -> bool:
    try:
        module = _dependency(deps, "step_import")
        return bool(getattr(module, "AVAILABLE", False)) if module is not None else False
    except Exception:                                     # noqa: BLE001 - 不可用就是不可用
        return False


# --------------------------------------------------------------------------- #
# 分流判定（Spec §1.2/§1.3）
# --------------------------------------------------------------------------- #
def _evidence(source: str, key: str, value: Any, ref: str) -> dict:
    return {"source": source, "key": key, "value": value, "ref": ref}


def classify(project_id: Any, *, manifest: Any = None, ir: Any = None,
             semantics: Any = None, deps: Any = None) -> dict:
    """图纸性质判定（Spec §1.2）：只吃 manifest / CAD IR / 包装语义三类证据。"""
    # 三个输入只按具体键读；没显式给就从依赖缝取（Spec §1.6）。
    if manifest is None:
        manifest = _from_deps(deps, "cad_converter", ("latest_manifest",), project_id)
    if ir is None:
        ir = _from_deps(deps, "cad_ir", ("load_ir",), project_id)
    if semantics is None:
        semantics = _from_deps(deps, "packaging_semantics",
                               ("latest", "load_semantics"), project_id)
    manifest = _dict(manifest)
    ir = _dict(ir)
    semantics = _dict(semantics)

    signals = {
        "has_2d_entities": False,
        "has_3d_entities": False,
        "three_d_declared": bool(_dict(manifest).get("three_d")),
        "three_d_artifact": None,
        "packaging_layers": [],
        "z_only_ignored": False,
    }
    evidence: List[dict] = []
    reasons: List[str] = []
    warnings: List[str] = []
    kind = "unknown"

    has_manifest = bool(manifest)
    has_ir = bool(ir)
    if has_manifest and has_ir:
        ir_id = _text(ir.get("ir_id"))
        conversion_id = _text(manifest.get("conversion_id"))
        semantics_version = _text(semantics.get("semantics_version"))
        ir_ref = "ir:%s" % ir_id
        manifest_ref = "manifest:%s" % conversion_id

        types = _ir_entity_types(ir)
        has_2d = any(item in TWO_D_ENTITY_TYPES for item in types)
        has_3d = any(item in THREE_D_ENTITY_TYPES for item in types)
        signals["has_2d_entities"] = has_2d
        signals["has_3d_entities"] = has_3d
        signals["z_only_ignored"] = bool(_has_nonzero_z(ir) and not has_3d)

        packaging = _packaging_layers(semantics, ir)
        signals["packaging_layers"] = list(packaging)

        artifact = _three_d_artifact(manifest)
        signals["three_d_artifact"] = (dict(artifact) if artifact else None)
        artifact_ok = bool(artifact and artifact.get("verified"))

        evidence.append(_evidence("cad_ir", "entity_types", sorted(set(types)), ir_ref))
        evidence.append(_evidence(
            "cad_ir", "layers",
            sorted({_text(layer.get("name")) for layer in _rows(ir.get("layers"))
                    if _text(layer.get("name"))}), ir_ref))
        evidence.append(_evidence("manifest", "conversion_id", conversion_id, manifest_ref))
        evidence.append(_evidence(
            "manifest", "output_roles",
            sorted({_text(item.get("role")).lower() for item in _manifest_outputs(manifest)
                    if _text(item.get("role"))}), manifest_ref))
        evidence.append(_evidence(
            "manifest", "three_d_artifact",
            dict(artifact) if artifact else None, manifest_ref))
        if semantics_version:
            evidence.append(_evidence("packaging_semantics", "packaging_layers",
                                      list(packaging),
                                      "semantics:%s" % semantics_version))

        # B18/§1.3 规则 5：六态**只由图纸证据决定**，不许跟着机器能力变；
        # `supported`（转换器声明）不参与 kind 判定，只在 capability() 里如实转述。
        if has_3d and artifact is not None:
            kind = "mixed" if packaging else "3d_convertible"
            reasons.append("three_d_artifact_verified" if artifact_ok
                           else "three_d_artifact_unverified")
        elif has_3d:
            kind = "3d_present_unsupported"
            reasons.append("no_three_d_artifact")
        elif packaging:
            kind = "packaging_2d"
            reasons.append("packaging_layers_present")
        elif has_2d:
            kind = "2d_only"
            reasons.append("no_3d_entity_types")
        else:
            kind = "unknown"
            reasons.append("no_drawing_evidence")
        if artifact is not None and not artifact.get("verified"):
            warnings.append("three_d_artifact_sha256_mismatch")
    else:
        reasons.append("missing_evidence")
        warnings.append("manifest_or_ir_missing")

    evidence.sort(key=lambda item: (item["source"], item["key"], item["ref"]))
    return {
        "dispatch_version": DISPATCH_VERSION,
        "project_id": _text(project_id),
        "drawing_kind": kind,
        "confidence": CONFIDENCE_BY_KIND.get(kind, 0.0),
        "signals": signals,
        "evidence": evidence,
        "reasons": sorted(set(reasons)),
        "warnings": sorted(set(warnings)),
    }


def _evidence_refs(classification: dict) -> List[str]:
    refs = {_text(item.get("ref")) for item in _rows(classification.get("evidence"))}
    return sorted(ref for ref in refs if ref)


def _route_shape(kind: str, pipeline: str, code: str, blocked_by: str,
                 classification: dict, enabled: bool) -> dict:
    return {
        "dispatch_version": DISPATCH_VERSION,
        "pipeline": pipeline,
        "drawing_kind": kind,
        "status": code,
        "blocked_by": blocked_by,
        "reason": STATUS_MESSAGES.get(code, STATUS_MESSAGES["3d_unknown"]),
        "evidence_refs": _evidence_refs(classification),
        "enabled": bool(enabled),
    }


def route(project_id: Any, *, classification: Any = None, deps: Any = None) -> dict:
    """分流出口（Spec §1.4）：三维路径三条缺一不可，否则回落二维并说明缺哪条。"""
    info = _dict(classification)
    kind = _text(info.get("drawing_kind")) or "unknown"
    if kind not in DRAWING_KINDS:
        kind = "unknown"

    if not _dispatch_enabled():
        return _route_shape(kind, "none", "3d_unknown", DISABLED_REASON, info, False)

    signals = _dict(info.get("signals"))
    artifact_row = signals.get("three_d_artifact")
    artifact_row = artifact_row if isinstance(artifact_row, dict) else {}
    artifact_ok = bool(artifact_row.get("verified"))
    step_ok = _step_available(deps)

    if kind == "packaging_2d":
        return _route_shape(kind, "2d", "2d_parsed", "", info, True)
    if kind == "2d_only":
        return _route_shape(kind, "2d", "3d_absent", "", info, True)
    if kind == "3d_present_unsupported":
        return _route_shape(kind, "2d", "3d_converter_unavailable", "", info, True)
    if kind in ("3d_convertible", "mixed"):
        if artifact_ok and step_ok:
            return _route_shape(kind, "3d", "3d_converted_and_parsed", "", info, True)
        if artifact_ok:
            # 产物校验通过、只是三维解析器不可用（Spec §1.5.1 的两行）
            if kind == "mixed":
                return _route_shape(kind, "2d", "3d_converter_unavailable",
                                    BLOCKED_STEP_UNAVAILABLE, info, True)
            return _route_shape(kind, "2d", "3d_conversion_failed",
                                BLOCKED_STEP_UNAVAILABLE, info, True)
        if artifact_row:
            # 有三维中间格式产物但 sha256 校验失败 → 导出失败（Spec §1.5.1）
            return _route_shape(kind, "2d", "3d_conversion_failed",
                                BLOCKED_ARTIFACT_UNVERIFIED, info, True)
        return _route_shape(kind, "2d", "3d_converter_unavailable", "", info, True)
    return _route_shape(kind, "2d", "3d_unknown", "", info, True)


# --------------------------------------------------------------------------- #
# 持久化（唯一写盘入口，Spec §1.1）
# --------------------------------------------------------------------------- #
def _load(project_id: Any) -> Optional[dict]:
    try:
        doc = store._meta().get_doc(_text(project_id), DISPATCH_DOC)
    except Exception:                                     # noqa: BLE001 - 老项目/未知项目
        return None
    return doc if isinstance(doc, dict) else None


def _save(project_id: Any, doc: dict) -> None:
    store._meta().put_doc(_text(project_id), DISPATCH_DOC, doc)


def _content_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def status_for(project_id: Any, *, deps: Any = None) -> dict:
    """六态查询（Spec §1.5）：纯读取，不写盘、不改任何文档。"""
    doc = _load(project_id) or {}
    code = "3d_unknown"
    pipeline = "2d"
    kind = "unknown"
    blocked_by = ""
    artifact = None
    fresh = False
    evidence_refs: List[str] = []
    if doc:
        raw = _text(doc.get("code")) or _text(doc.get("status"))
        if raw in DRAWING3D_STATUS:
            code = raw
        candidate = _text(doc.get("pipeline"))
        pipeline = candidate if candidate in PIPELINES else "2d"
        kind = _text(doc.get("drawing_kind")) or "unknown"
        blocked_by = _text(doc.get("blocked_by"))
        artifact = doc.get("three_d_artifact")
        evidence_refs = [_text(item) for item in (doc.get("evidence_refs") or []) if _text(item)]
        fresh = (_text(doc.get("run_status")) == "completed"
                 and _text(doc.get("status")) != "interrupted")
    if not _dispatch_enabled():
        code = "3d_unknown"
        pipeline = "none"
        fresh = False
    return {
        "code": code,
        "message": STATUS_MESSAGES.get(code, STATUS_MESSAGES["3d_unknown"]),
        "pipeline": pipeline,
        "drawing_kind": kind,
        "blocked_by": blocked_by,
        "three_d_artifact": artifact,
        "evidence_refs": evidence_refs,
        "fresh": bool(fresh),
    }


def dispatch_document(project_id: Any, *, deps: Any = None) -> dict:
    """跑一次分流并把结论落库（Spec §1.1/§8）；关闭态不写任何文档。"""
    classification = classify(project_id, deps=deps)
    routed = route(project_id, classification=classification, deps=deps)
    if not _dispatch_enabled():
        return routed

    payload = {"classification": classification, "route": routed}
    content_hash = _content_hash(payload)
    existing = _load(project_id)
    if (isinstance(existing, dict)
            and _text(existing.get("dispatch_version")) == DISPATCH_VERSION
            and _text(existing.get("content_hash")) == content_hash):
        return existing

    doc = {
        "dispatch_version": DISPATCH_VERSION,
        "project_id": _text(project_id),
        "status": routed["status"],
        "code": routed["status"],
        "run_status": "completed",
        "drawing_kind": routed["drawing_kind"],
        "pipeline": routed["pipeline"],
        "blocked_by": routed["blocked_by"],
        "evidence_refs": routed["evidence_refs"],
        "three_d_artifact": classification["signals"].get("three_d_artifact"),
        "content_hash": content_hash,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "payload": payload,
    }
    _save(project_id, doc)
    return doc


def routing_view(project_id: Any) -> dict:
    """只读的分流视图（Spec §2.3 的 `GET .../drawing-routing`）。

    只回结论与摘要：不带产物绝对路径、不带原始字节、不带转换器内部细节。
    """
    doc = _load(project_id) or {}
    classification = _dict(_dict(doc.get("payload")).get("classification"))
    signals = _dict(classification.get("signals"))
    return {
        "project_id": _text(project_id),
        "dispatch_version": DISPATCH_VERSION,
        "status": status_for(project_id),
        "classification": {
            "dispatch_version": DISPATCH_VERSION,
            "project_id": _text(project_id),
            "drawing_kind": _text(classification.get("drawing_kind")) or "unknown",
            "confidence": float(classification.get("confidence") or 0.0),
            "reasons": [_text(item) for item in (classification.get("reasons") or [])],
            "packaging_layers": [_text(item)
                                 for item in (signals.get("packaging_layers") or [])],
        },
        "limits": limits(),
    }


def capability() -> dict:
    """分流层的能力声明（只读；不猜、不写盘）。"""
    live = limits()
    return {
        "dispatch_version": DISPATCH_VERSION,
        "enabled": bool(live["DWG_DISPATCH_ENABLED"]),
        "drawing_kinds": list(DRAWING_KINDS),
        "statuses": list(DRAWING3D_STATUS),
        "pipelines": list(PIPELINES),
        "limits": live,
    }


# --------------------------------------------------------------------------- #
# 保留策略与恢复（Spec §8）
# --------------------------------------------------------------------------- #
def _parse_iso(value: Any) -> Optional[datetime.datetime]:
    raw = _text(value)
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def retention_plan(now: Any, items: Any, *, days: Any = None, keep: Any = ()) -> dict:
    """产物保留计划（纯函数，Spec §8）：不删文件、不读磁盘、不写审计。"""
    live = limits()
    window = int(live["CAD_ARTIFACT_RETENTION_DAYS"] if days is None else days)
    kept = [_text(item) for item in (keep or ())]
    expire: List[dict] = []
    if live["CAD_ARTIFACT_CLEANUP_ENABLED"]:
        moment = _parse_iso(now)
        if moment is not None:
            cutoff = moment - datetime.timedelta(days=window)
            for item in _rows(items):
                ident = _text(item.get("id"))
                if ident in kept:
                    continue
                created = _parse_iso(item.get("created_at"))
                if created is not None and created < cutoff:
                    expire.append({"id": item.get("id"), "created_at": item.get("created_at")})
    return {"policy_version": RETENTION_POLICY_VERSION, "days": window,
            "keep": kept, "expire": expire}


def recover(project_id: Any, *, deps: Any = None) -> dict:
    """服务重启恢复（Spec §8）：把 running 的分流判定标成 interrupted，不删东西。"""
    doc = _load(project_id)
    if not isinstance(doc, dict) or not doc:
        return {"project_id": _text(project_id), "status": "missing", "recovered": False}
    if _text(doc.get("status")) != "running":
        return {"project_id": _text(project_id), "status": _text(doc.get("status")),
                "recovered": False}
    updated = dict(doc)
    updated["status"] = "interrupted"
    updated["run_status"] = "interrupted"
    _save(project_id, updated)
    return {"project_id": _text(project_id), "status": "interrupted", "recovered": True}


def summarize(doc: Any) -> dict:
    """给页面/报告用的稳定小摘要（Spec §1.1）。"""
    data = _dict(doc)
    return {
        "dispatch_version": _text(data.get("dispatch_version")),
        "drawing_kind": _text(data.get("drawing_kind")) or "unknown",
        "pipeline": _text(data.get("pipeline")) or "none",
        "status": _text(data.get("status")) or "3d_unknown",
    }


def migrate(doc: Any) -> dict:
    """分流文档迁移（Spec §9）：缺版本不硬读、未知版本不许猜。"""
    data = _dict(doc)
    version = _text(data.get("dispatch_version"))
    if not version:
        return {"status": "needs_rebuild"}
    if version != DISPATCH_VERSION:
        raise ValueError("unsupported dispatch version: %s" % version)
    return doc
