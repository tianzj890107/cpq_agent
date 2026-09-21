"""DXF 确定性解析与统一 CAD IR —— DWG 支持第 3 批（Spec `docs/specs/dxf-cad-ir.md`）。

对外只暴露 Spec §2.1 冻结的那组接口：`capability / parse_dxf / parse_conversion /
load_ir / list_irs / ir_hash / migrate / summarize`。

边界（Spec §11）：
  · **纯确定性解析**：不联网、不调模型、不写 Agent 上下文；
  · CAD IR 与设备设计 IR（`models/ir.py`）互不嵌套，各占自己的文档位；
  · 本包不判断刀线/压痕线/盒型（第 4 批），不算成本，不发报价。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import model, persistence
from .model import CAD_IR_VERSION
from .parser import capability, parse_dxf, resolve_limits

__all__ = ["CAD_IR_VERSION", "capability", "parse_dxf", "parse_conversion",
           "load_ir", "list_irs", "ir_hash", "migrate", "summarize", "resolve_limits"]


def ir_hash(ir: Dict[str, Any]) -> str:
    """规范哈希：实体书写顺序/图层声明顺序不同，哈希必须一致（Spec §3.4）。"""
    return model.ir_hash(ir)


def migrate(ir: Any) -> Dict[str, Any]:
    """版本迁移判定（Spec §6.3）：不猜、不按当前版本硬读旧数据。"""
    return model.migrate(ir)


def summarize(ir: Dict[str, Any]) -> Dict[str, Any]:
    """给 Agent / 看板用的安全摘要（**不含实体明细**，Spec §7）。"""
    return model.summarize(ir)


def load_ir(project_id: str, ir_id: Optional[str] = None) -> Optional[dict]:
    """按 id 回看某版 IR；不给 id 取最新一版（Spec §6.4）。"""
    return persistence.load_ir(project_id, ir_id)


def list_irs(project_id: str) -> List[dict]:
    """该项目全部 IR 的时间倒序元信息（Spec §6.4）。"""
    return persistence.list_irs(project_id)


def parse_conversion(project_id: str, *, conversion_id: Optional[str] = None,
                     drawing_version: Optional[int] = None, author: str = "system") -> Dict[str, Any]:
    """从第 2 批的转换产物解析出 CAD IR 并落盘（**失败不写 IR**，Spec §6.2）。

    产物定位只走第 2 批的三个函数（capability / latest_manifest / artifact_dir），
    绝不自己遍历数据目录猜路径（Spec §6.4）。
    """
    from .. import cad_converter

    cap = cad_converter.capability() or {}
    if not cap.get("available"):
        raise _error("DWG_CONVERTER_NOT_INSTALLED",
                     "本机没有可用的 DWG 转换器，无法产出 DXF 供解析",
                     {"converter": cap.get("adapter_name") or "",
                      "support_claim": cap.get("support_claim") or ""})

    # 可用的转换产物有两种状态（`dwg-semantics-agent-flow.md` §3 第 2 步的门槛就是
    # `status in ("ok","success_with_warnings")`）：ODA 干净转换给 `ok`，LibreDWG 有告警但
    # 图纸可用给 `success_with_warnings`。`latest_manifest()` 缺省只要 `ok`，直接调它会把
    # LibreDWG 的产物当成"没有产物"，本地链路在第三步就断（线上因为主用 ODA 才看不出来）。
    # 这里按门槛逐个状态取最近一条，仍然只走第 2 批的产物定位函数。
    acceptable = [str(code) for code in getattr(
        cad_converter, "SUCCESS_STATUSES", ("ok", "success_with_warnings"))]
    manifest = None
    if conversion_id:
        candidate = cad_converter.load_manifest(project_id, conversion_id)
        if candidate and str(candidate.get("status") or "") in acceptable:
            manifest = candidate
    else:
        for accepted in acceptable:
            manifest = cad_converter.latest_manifest(project_id, status=accepted)
            if manifest:
                break
    if not manifest:
        raise _error("CAD_IR_SOURCE_MISSING",
                     "项目里没有可用的 DXF 转换产物，请先重跑图纸转换",
                     {"project_id": project_id, "conversion_id": conversion_id or ""})

    outputs = [item for item in (manifest.get("output_files") or [])
               if str(item.get("role") or "") == "dxf"]
    if not outputs:
        raise _error("CAD_IR_SOURCE_MISSING", "转换产物里没有 DXF 文件，请先重跑图纸转换",
                     {"conversion_id": str(manifest.get("conversion_id") or ""),
                      "status": str(manifest.get("status") or "")})

    artifact = outputs[0]
    filename = str(artifact.get("filename") or "drawing.dxf")
    directory = cad_converter.persistence.artifact_dir(project_id,
                                                       str(manifest.get("conversion_id") or ""))
    path = directory / filename
    if not path.exists():
        raise _error("CAD_IR_SOURCE_MISSING", "转换产物已不存在，请先重跑图纸转换",
                     {"conversion_id": str(manifest.get("conversion_id") or ""),
                      "artifact": filename})

    content = path.read_bytes()
    source = {
        "kind": "dxf_2d",
        "project_id": project_id,
        "attachment_name": str(manifest.get("attachment_name")
                               or manifest.get("original_filename") or ""),
        "source_sha256": str(manifest.get("source_sha256") or ""),
        "conversion_id": str(manifest.get("conversion_id") or ""),
        "dxf_artifact": filename,
        "dxf_sha256": str(artifact.get("sha256") or _sha256(content)),
        "converter_name": str(manifest.get("converter_name") or ""),
        "converter_version": str(manifest.get("converter_version") or ""),
        # Spec §3.9：产出方身份与回退留痕必须透传（第 4 批据此下调字段可信度）
        "converter_role": str(manifest.get("converter_role") or ""),
        "fallback_used": bool(manifest.get("fallback_used")),
        "primary_failure_code": str(manifest.get("primary_failure_code") or ""),
        "output_version": str(manifest.get("output_version") or ""),
        "audit_enabled": bool(manifest.get("audit_enabled")),
        "detected_dwg_version": str(manifest.get("detected_dwg_version") or ""),
        "drawing_version": int(drawing_version or manifest.get("drawing_version") or 1),
        "conversion_status": str(manifest.get("status") or ""),
        "warning_count": int(manifest.get("warning_count") or 0),
        "error_count": int(manifest.get("error_count") or 0),
        "quality": dict(manifest.get("quality") or {}),
    }
    ir = parse_dxf(content, filename=filename, source=source)
    saved = persistence.save_ir(project_id, ir)
    persistence.audit(project_id, "cad_ir_parsed", {
        "ir_id": saved.get("ir_id"), "ir_hash": saved.get("ir_hash"),
        "ir_version": saved.get("ir_version"), "by": author,
        "entity_total": (saved.get("stats") or {}).get("entity_total"),
        "layer_total": (saved.get("stats") or {}).get("layer_total"),
        "conversion_id": source["conversion_id"],
        "dxf_artifact": filename,
        "dxf_sha256": source["dxf_sha256"],
        "warnings": len(saved.get("warnings") or []),
    })
    return saved


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _error(code: str, message: str, detected: Dict[str, Any]):
    from .. import file_preflight

    return file_preflight.FileCapabilityError(code, detected=dict(detected), message=message)
