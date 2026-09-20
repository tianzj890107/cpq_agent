"""模型辅助（DWG 第 4 批 Spec §6）—— 本包**唯一**访问模型客户端的地方。

硬约束：
  · 输入只能是栅格预览（image/png|jpeg|webp|bmp）；SVG/XML 一律按「不可用」处理，
    模型调用数为 **0**；
  · 只经 `claude_client.run`（模块属性形式调用，红测据此 patch 计数）；
  · 输出过 schema（`model.PackagingAssistResult`，extra="allow"），越权键只记名字，
    绝不写进 fields；模型结论恒 `inferred_by_model` + `needs_confirmation` + WEAK；
  · 同字段已有 CAD 证据时只进 alternatives（不许覆盖）；
  · 预览字节、DXF 原文、思维链一律不入库、不入响应。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .. import claude_client
from .. import llm_client
from . import model

#: Spec §6：允许送模型的栅格类型闭集。
RASTER_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/bmp")

ENV_MODEL = "PACKAGING_SEMANTICS_MODEL"
ENV_MAX_PREVIEW_BYTES = "PACKAGING_SEMANTICS_MAX_PREVIEW_BYTES"
DEFAULT_MAX_PREVIEW_BYTES = 8 * 1024 * 1024

PREVIEW_UNAVAILABLE = "PACKAGING_PREVIEW_UNAVAILABLE"
PREVIEW_TOO_LARGE = "PACKAGING_PREVIEW_TOO_LARGE"
MODEL_OUTPUT_INVALID = "PACKAGING_MODEL_OUTPUT_INVALID"

_SYSTEM_PROMPT = (
    "你在看一张包装盒图纸的缩略图（分辨率很低）。只输出结构化候选：标题栏字段、"
    "材料候选、表面工艺候选、可能的盒型、图层含义猜测与需要人工回答的问题。"
    "不要给结论性尺寸，不要推测金额、客户信息或任何与图纸无关的内容；"
    "看不清就留空并写进 open_questions。"
)


def enabled() -> bool:
    """全局开关：`PACKAGING_SEMANTICS_MODEL=off` 时锁死（Spec §10）。"""
    return str(os.environ.get(ENV_MODEL) or "auto").strip().lower() != "off"


def max_preview_bytes() -> int:
    try:
        return int(str(os.environ.get(ENV_MAX_PREVIEW_BYTES) or DEFAULT_MAX_PREVIEW_BYTES))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PREVIEW_BYTES


def _block(status: str, *, used: bool, calls: int, model_name: str = "", error: str = "",
           preview_kind: str = "none", evidence_level: str = "NONE") -> Dict[str, Any]:
    return {
        "used": bool(used), "status": status, "calls": int(calls), "model": str(model_name or ""),
        "stable_error_code": str(error or ""), "preview_kind": preview_kind,
        "evidence_level": evidence_level, "extra_fields_dropped": [],
        "title_block": {}, "material_candidates": [], "process_candidates": [],
        "box_candidates": [], "layer_interpretations": [], "open_questions": [],
    }


def _preview_payload(preview: Any) -> Tuple[Optional[str], Optional[bytes]]:
    if not isinstance(preview, dict):
        return None, None
    raw = preview.get("bytes")
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        return None, None
    media = str(preview.get("media_type") or "").strip().lower()
    if media not in RASTER_MEDIA_TYPES:
        return None, bytes(raw)
    return media, bytes(raw)


def _call_model(raw: bytes, media: str) -> Tuple[str, Any]:
    """唯一的一条模型缝：llm_client 负责路由/方言翻译，run 走 claude_client。"""
    filename = "preview." + (media.rsplit("/", 1)[-1] or "png")
    blocks: List[Dict[str, Any]] = [
        {"type": "text", "text": _SYSTEM_PROMPT},
        {"type": "image", "data": raw, "filename": filename, "detail": "low"},
    ]
    model_name = ""
    try:
        route = llm_client.resolve_route(blocks)
        module = llm_client._module_for(route)
        blocks = llm_client._translate(blocks, module)
        model_name = str(route.get("model") or "")
    except Exception:  # noqa: BLE001 - 路由不可观测时按中立块继续，绝不因此丢辅助
        model_name = ""
    return model_name, claude_client.run(_SYSTEM_PROMPT, blocks,
                                         model.PackagingAssistResult)


def _drop(bucket: List[str], text: str) -> None:
    if text and text not in bucket:
        bucket.append(text)


def _sanitize(result: Any) -> Dict[str, Any]:
    """把模型输出收敛成闭集内的内容；越权键只留名字。"""
    if hasattr(result, "model_dump"):
        data = dict(result.model_dump())
    elif isinstance(result, dict):
        data = dict(result)
    else:
        raise ValueError("模型返回不是对象")

    dropped: List[str] = []
    extras = getattr(result, "__pydantic_extra__", None)
    if isinstance(extras, dict):
        for key in extras:
            _drop(dropped, str(key))
    for key in list(data.keys()):
        if key not in model.MODEL_OUTPUT_KEYS:
            _drop(dropped, str(key))
            data.pop(key, None)

    allowed = set(model.packaging_field_keys())
    for kind in ("material_candidates", "process_candidates"):
        kept: List[Dict[str, Any]] = []
        for index, item in enumerate(data.get(kind) or []):
            if not isinstance(item, dict):
                _drop(dropped, "%s[%d]" % (kind, index))
                continue
            field_key = str(item.get("field") or "")
            if field_key not in allowed:
                _drop(dropped, "%s[%d].field=%s" % (kind, index, field_key or "-"))
                continue
            value = item.get("value")
            if value is None:
                value = item.get("text")
            kept.append({"field": field_key, "text": str(item.get("text") or ""),
                         "value": model.json_safe(value),
                         "confidence": model.cap_confidence("inferred_by_model",
                                                            item.get("confidence"))})
        data[kind] = kept

    title = data.get("title_block")
    data["title_block"] = model.json_safe(title) if isinstance(title, dict) else {}
    data["box_candidates"] = [model.json_safe(item) for item in (data.get("box_candidates") or [])
                              if isinstance(item, dict)]
    data["layer_interpretations"] = [model.json_safe(item)
                                     for item in (data.get("layer_interpretations") or [])
                                     if isinstance(item, dict)]
    data["open_questions"] = [str(item) for item in (data.get("open_questions") or [])]
    data["extra_fields_dropped"] = dropped
    return data


def apply_candidates(block: Dict[str, Any], fields: Dict[str, Any], ir: Any) -> None:
    """模型候选落进字段表：CAD 缺证据才填补，已有证据只能进 alternatives。"""
    ir_doc = ir if isinstance(ir, dict) else {}
    for key, candidate in ([(c.get("field"), c) for c in block.get("material_candidates") or []]
                           + [(c.get("field"), c) for c in block.get("process_candidates") or []]):
        entry = fields.get(str(key or ""))
        if not isinstance(entry, dict):
            continue
        alternative = {
            "origin": "inferred_by_model", "status": "needs_confirmation",
            "value": model.json_safe(candidate.get("value")),
            "confidence": model.cap_confidence("inferred_by_model", candidate.get("confidence")),
            "evidence_level": "WEAK", "evidence_refs": [], "source": "model",
            "ir_id": str(ir_doc.get("ir_id") or ""), "ir_hash": str(ir_doc.get("ir_hash") or ""),
        }
        if entry.get("origin") == "missing":
            fields[str(key)] = model.field_entry(
                ir_doc, origin="inferred_by_model", status="needs_confirmation",
                value=candidate.get("value"), confidence=candidate.get("confidence"),
                evidence_level="WEAK", evidence_refs=[])
            continue
        alternatives = list(entry.get("alternatives") or [])
        alternatives.append(alternative)
        entry["alternatives"] = alternatives


def evaluate(*, preview: Any = None, use_model: bool = False,
             fields: Optional[Dict[str, Any]] = None, ir: Any = None,
             known: Any = None) -> Dict[str, Any]:
    """返回 `model_assist` 文档块；`fields` 传入时就地写入模型候选。"""
    del known  # 模型结论没有图纸证据，保留形参只为调用方对称
    if not use_model:
        return _block("disabled", used=False, calls=0)
    if not enabled():
        return _block("disabled", used=False, calls=0)

    media, raw = _preview_payload(preview)
    if media is None:
        kind = "unsupported" if raw is not None else "none"
        return _block("unavailable", used=False, calls=0, error=PREVIEW_UNAVAILABLE,
                      preview_kind=kind)
    if len(raw) > max_preview_bytes():
        return _block("unavailable", used=False, calls=0, error=PREVIEW_TOO_LARGE,
                      preview_kind="raster")

    try:
        model_name, result = _call_model(raw, media)
        payload = _sanitize(result)
    except Exception:  # noqa: BLE001 - 模型异常绝不能拖垮确定性结果
        return _block("failed", used=True, calls=1, error=MODEL_OUTPUT_INVALID,
                      preview_kind="raster")

    block = _block("ok", used=True, calls=1, model_name=model_name, preview_kind="raster",
                   evidence_level="WEAK")
    block.update(payload)
    block.update({"used": True, "status": "ok", "calls": 1, "model": model_name,
                  "stable_error_code": "", "preview_kind": "raster",
                  "evidence_level": "WEAK"})
    if isinstance(fields, dict):
        apply_candidates(block, fields, ir)
    return block
