"""统一解析服务：DWG / DXF → 快速报价所需字段 —— 逆向快速报价第 7 批。

Spec：`docs/specs/quick-quote-7-unified-parse-service.md`
红测：`tests/test_quick_quote_parse_service_red.py`

分工（Spec §0）：

  · **技术工艺侧**提供能力（ODA / dwg2dxf 只住在这里），**报价侧**消费（批 5 的
    `cpq_quick_quote_file.py` 是纯 HTTP 客户端）；
  · 本模块只做「按字段取数」：转换产物落**隔离解析项目**目录，不建卡片、不写需求 /
    零件 / 成本记录，也不做语义推断（盒型族、成品主轮廓属于 `packaging_semantics`）；
  · 从 CAD IR **能确定**的东西才给值，推不出来的一律进 `missing_fields`（**绝不猜**，
    也不给 `False` —— 「图纸没写」不等于「没有」）。

可注入依赖 `deps`（`PARSE_DEPS_METHODS`）：`capability()` / `convert()` / `parse_dxf()`。
红测正是按这三个方法注入假实现，所以本模块的 I/O 只有这一条缝。
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SERVICE_NAME = "cpq-unified-parse"
SERVICE_VERSION = "unified_parse_v1"
MAX_PARSE_BYTES = 64 * 1024 * 1024
MAX_LIST_ITEMS = 200                 # 列表类字段的截断上限
MAX_MATERIAL_NOTES = 50
#: HTTP 路由（唯一事实源：main.py 的免登录白名单按这两个常量登记，免得两处字符串漂移；
#: 装饰器里仍写字面量，便于按路由快速定位）。
SERVICE_PATH = "/api/file/parse"
CAPABILITY_PATH = "/api/file/parse/capability"

#: 转换产物落这个**隔离解析项目**的目录（不是业务项目：不建卡片、不写需求/零件/成本）。
PARSE_PROJECT_ID = "cpq-unified-parse"

DWG_EXTS = (".dwg", ".dxf")
DOC_EXTS = (".txt", ".md", ".csv", ".xlsx", ".xls", ".pdf", ".docx",
            ".png", ".jpg", ".jpeg")

#: 快速通道只提供这些字段。**必须与 cpq_quick_quote_file.QUICK_FIELDS 逐字一致**
#: （红测直接比对两个元组；两侧不许各自漂移）。
PARSE_FIELDS = ("units", "annotated_dimensions", "outline_size", "box_features",
                "closure_type", "v_groove", "magnet", "window",
                "material_notes", "text_annotations", "layers", "blocks",
                "unfolded_size")

CAPABILITY_KEYS = ("service", "provider", "provider_version", "dwg", "dxf", "preview")
PARSE_ERROR_CODES = ("bad_payload", "empty_file", "unsupported_format",
                     "file_too_large", "converter_unavailable", "parse_failed")
#: 可注入依赖必须提供的方法（红测按这个签名注入假的，不真跑 ODA）。
PARSE_DEPS_METHODS = ("capability", "convert", "parse_dxf")

#: 只做关键词命中的语义字段：**命中给 True，未命中进 missing —— 不给 False**。
#: 「图纸没写」不等于「没有」；给 False 会让批 2 把差异项当成相同项。
KEYWORD_FIELDS = ("v_groove", "magnet", "window")
KEYWORD_HINTS = {"v_groove": ("V槽", "V 槽", "V-CUT", "VCUT"),
                 "magnet": ("磁铁", "磁石", "磁吸", "magnet"),
                 "window": ("开窗", "窗口", "透明窗", "window")}

#: 材料标注关键词（Spec §2.4：「material_notes」只保留含这些词的行）。
MATERIAL_KEYWORDS = ("灰板", "纸板", "铜版", "白卡", "单粉", "牛皮", "瓦楞",
                     "克重", "g/m²", "g/m2", "gsm")

#: 闭合方式关键词 → 归一后的取值（命中给字符串，未命中进 missing）。
CLOSURE_HINTS = (("双开门", ("双开门", "双开", "对开门")),
                 ("天地盖", ("天地盖", "天地盒")),
                 ("磁吸", ("磁吸", "磁铁", "磁石")),
                 ("翻盖", ("翻盖", "翻盖盒")),
                 ("抽屉", ("抽屉",)),
                 ("书型盒", ("书型",)),
                 ("管式盒", ("管式", "牙膏盒")))

_DXF_SOURCE_KEYS = ("conversion_id", "dxf_artifact", "dxf_sha256", "converter_name",
                    "converter_version", "detected_dwg_version", "drawing_version",
                    "conversion_status", "converter_role", "fallback_used",
                    "output_version", "primary_failure_code", "source_sha256",
                    "quality", "crosscheck")

_ADVICE_UNAVAILABLE = ("DWG 转换器未就绪：请转人工处理，或在报价页把这条需求转精准报价，"
                       "由技术工艺侧用已装转换器的环境解析")
_ADVICE_DOC = "文字 / Excel / PDF / 图片请走既有 /api/extract（报价侧客户端本来就自己走那条路）"


class ParseError(Exception):
    """带稳定错误码的业务错误：`.code` / `.http_status` / `.advice`。"""

    def __init__(self, code: str, message: str, *, http_status: int = 400,
                 advice: str = ""):
        super().__init__(message)
        self.code = str(code)
        self.http_status = int(http_status)
        self.advice = str(advice)


# --------------------------------------------------------------------------- #
# 能力预检
# --------------------------------------------------------------------------- #
def _deps_or_default(deps=None):
    return deps if deps is not None else _DefaultDeps()


def capability(*, deps=None) -> dict:
    """转换器能力（Spec §2.2）：**正好** `CAPABILITY_KEYS` 六个键，`dwg` 来自真探测。

    探测抛错**不抛异常**（能力查询绝不许 500）：回 `dwg=False` + `detail` 说明原因。
    """
    deps = _deps_or_default(deps)
    out = {"service": SERVICE_NAME, "provider": "", "provider_version": "",
           "dwg": False, "dxf": False, "preview": False}
    try:
        probe = deps.capability() or {}
    except Exception as exc:                                   # noqa: BLE001 - 统一收敛
        out["detail"] = "转换器能力探测失败：%s" % (_reason(exc) or exc.__class__.__name__)
        return out
    probe = probe if isinstance(probe, dict) else {}
    available = bool(probe.get("available"))
    out["provider"] = str(probe.get("provider") or "")
    out["provider_version"] = str(probe.get("converter_version")
                                  or probe.get("provider_version") or "")
    out["dwg"] = available
    out["dxf"] = available
    out["preview"] = bool(probe.get("preview_available"))
    return out


def _reason(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return ""
    return "%s：%s" % (exc.__class__.__name__, text[:200])


# --------------------------------------------------------------------------- #
# 入参前置检查
# --------------------------------------------------------------------------- #
def _extension(name: str) -> str:
    lower = str(name or "").strip().lower()
    index = lower.rfind(".")
    return lower[index:] if index >= 0 else ""


def _decoded_size(b64: str) -> int:
    """按 base64 长度估算解码后字节数（先看大小再解码，Spec §2.3）。"""
    body = "".join(str(b64 or "").split())
    padding = len(body) - len(body.rstrip("="))
    return max(0, (len(body) // 4) * 3 - padding)


def _wanted_fields(raw) -> List[str]:
    if raw is None:
        return list(PARSE_FIELDS)
    if not isinstance(raw, (list, tuple)):
        raise ParseError("bad_payload", "fields 必须是数组（收到 %s）" % type(raw).__name__,
                         advice="fields 留空表示取 PARSE_FIELDS 全集")
    out: List[str] = []
    unknown: List[str] = []
    for item in raw:
        key = str(item or "").strip()
        if key not in PARSE_FIELDS:
            unknown.append(key)
            continue
        if key not in out:
            out.append(key)
    if unknown:
        raise ParseError("bad_payload",
                         "快速通道不提供这些字段：%s（只支持 %s）"
                         % ("、".join(unknown), "、".join(PARSE_FIELDS)),
                         advice="快速报价只索取匹配所需字段，完整几何 / 部件走技术工艺侧")
    return out


def parse_payload(payload, *, deps=None) -> dict:
    """一份上传件 → 快速通道字段（Spec §2.3）。四道前置检查逐条按表。"""
    started = time.monotonic()
    deps = _deps_or_default(deps)
    body = payload if isinstance(payload, dict) else {}
    name = str(body.get("name") or "").strip()
    if not name:
        raise ParseError("bad_payload", "缺文件名 name：无法判断格式",
                         advice="带扩展名的文件名（如 酒盒.dwg）")
    raw_data = body.get("data")
    if raw_data is None or not isinstance(raw_data, str):
        raise ParseError("bad_payload", "缺 data（文件内容的 base64 字符串）",
                         advice="把文件字节按 base64 编码后放进 data 字段")
    if _decoded_size(raw_data) > MAX_PARSE_BYTES:
        raise ParseError("file_too_large",
                         "文件超过上限 %d 字节" % MAX_PARSE_BYTES,
                         http_status=413,
                         advice="请先压缩或只上传需要报价的那张图纸")
    try:
        content = base64.b64decode("".join(raw_data.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ParseError("bad_payload", "data 不是合法的 base64：%s" % str(exc)[:120],
                         advice="重新编码后再传") from exc
    if not content:
        raise ParseError("empty_file", "文件内容为空（解码后 0 字节）",
                         advice="换一份非空文件重新上传")
    if len(content) > MAX_PARSE_BYTES:
        raise ParseError("file_too_large",
                         "文件 %d 字节，超过上限 %d 字节" % (len(content), MAX_PARSE_BYTES),
                         http_status=413)

    ext = _extension(name)
    if ext not in DWG_EXTS:
        raise ParseError("unsupported_format",
                         "统一解析服务本批只支持 %s（收到 %s）"
                         % ("、".join(DWG_EXTS), ext or "无扩展名"),
                         advice=_ADVICE_DOC)

    wanted = _wanted_fields(body.get("fields"))
    cap = capability(deps=deps)
    if not cap.get("dwg"):
        raise ParseError("converter_unavailable",
                         "DWG 转换器不可用：%s" % (cap.get("detail")
                                                   or cap.get("provider") or "未配置"),
                         http_status=503, advice=_ADVICE_UNAVAILABLE)

    warnings: List[str] = []
    try:
        converted = deps.convert(PARSE_PROJECT_ID, name, content) or {}
    except ParseError:
        raise
    except Exception as exc:                                   # noqa: BLE001 - 统一收敛
        raise ParseError("parse_failed", "DWG 转换失败：%s" % (_reason(exc) or "未知错误"),
                         http_status=502,
                         advice="这份图纸可能损坏或版本不受支持，可转人工处理") from exc
    dxf = converted.get("dxf") if isinstance(converted, dict) else None
    dxf = bytes(dxf) if isinstance(dxf, (bytes, bytearray)) else b""
    if not dxf:
        raise ParseError("parse_failed", "转换产物里没有 DXF 内容",
                         http_status=502, advice="转人工处理或换一份图纸")
    for item in (converted.get("warnings") or []):
        text = str(item or "").strip()
        if text:
            warnings.append(text)

    source = _dxf_source(converted, name, dxf)
    try:
        ir = deps.parse_dxf(dxf, name, source)
    except ParseError:
        raise
    except Exception as exc:                                   # noqa: BLE001 - 统一收敛
        raise ParseError("parse_failed", "DXF 解析失败：%s" % (_reason(exc) or "未知错误"),
                         http_status=502, advice="转人工处理") from exc
    if not isinstance(ir, dict):
        raise ParseError("parse_failed", "DXF 解析没有产出 CAD IR",
                         http_status=502, advice="转人工处理")

    fields = fields_from_ir(ir, wanted)
    if "outline_size" in fields:
        warnings.append("outline_from_extents：外形尺寸取的是图纸范围（document.extents），"
                        "不是成品内尺寸，请人工确认")
    missing = [key for key in wanted if key not in fields]
    return {
        "ok": True,
        "kind": "drawing",
        "service": SERVICE_NAME,
        "service_version": SERVICE_VERSION,
        "provider": str(cap.get("provider") or ""),
        "provider_version": str(cap.get("provider_version") or ""),
        "filename": name,
        "fields": fields,
        "missing_fields": missing,
        "warnings": warnings,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _dxf_source(converted, name: str, dxf: bytes) -> dict:
    source: Dict[str, Any] = {"kind": "dxf_2d", "project_id": PARSE_PROJECT_ID,
                              "attachment_name": name,
                              "dxf_sha256": hashlib.sha256(dxf).hexdigest()}
    if isinstance(converted, dict):
        for key in _DXF_SOURCE_KEYS:
            value = converted.get(key)
            if value in (None, "", {}):
                continue
            if key in ("fallback_used",):
                source[key] = bool(value)
            elif key in ("drawing_version",):
                try:
                    source[key] = int(value or 1)
                except (TypeError, ValueError):
                    continue
            else:
                source[key] = value
    return source


# --------------------------------------------------------------------------- #
# CAD IR → 字段
# --------------------------------------------------------------------------- #
def _finite(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _rows(ir, key: str) -> Iterable[dict]:
    raw = (ir or {}).get(key)
    if not isinstance(raw, (list, tuple)):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _texts(ir) -> List[str]:
    out: List[str] = []
    for row in _rows(ir, "texts"):
        text = str(row.get("normalized_text") or "").strip() or str(row.get("raw_text") or "").strip()
        if text:
            out.append(text)
    return out


def _outline_from_extents(ir) -> Optional[dict]:
    document = (ir or {}).get("document")
    extents = (document or {}).get("extents") if isinstance(document, dict) else None
    if not isinstance(extents, (list, tuple)) or len(extents) < 4:
        return None
    min_x, min_y, max_x, max_y = (_finite(value) for value in extents[:4])
    if None in (min_x, min_y, max_x, max_y):
        return None
    return {"width": round(abs(max_x - min_x), 9), "height": round(abs(max_y - min_y), 9),
            "source": "document_extents"}


def _keyword_hit(texts: Sequence[str], key: str) -> bool:
    hints = KEYWORD_HINTS.get(key) or ()
    for text in texts:
        lowered = str(text or "").lower()
        for hint in hints:
            if str(hint).lower() in lowered:
                return True
    return False


def _closure_type(texts: Sequence[str]) -> str:
    for label, hints in CLOSURE_HINTS:
        for text in texts:
            lowered = str(text or "").lower()
            for hint in hints:
                if str(hint).lower() in lowered:
                    return label
    return ""


def _is_material_note(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(str(word).lower() in lowered for word in MATERIAL_KEYWORDS)


def fields_from_ir(ir, wanted, *, units_text=None) -> dict:
    """CAD IR → 被请求的字段（Spec §2.4）。取不到的**不进结果**（由调用方记 missing）。"""
    wanted = [str(key) for key in (wanted if isinstance(wanted, (list, tuple)) else [])]
    ir = ir if isinstance(ir, dict) else {}
    out: Dict[str, Any] = {}
    texts = _texts(ir)

    if "units" in wanted:
        units = ir.get("units") if isinstance(ir.get("units"), dict) else {}
        if str(units.get("unit_status") or "") == "confirmed":
            text = str(units_text or units.get("drawing_units") or "").strip()
            if text:
                out["units"] = text

    if "outline_size" in wanted:
        size = _outline_from_extents(ir)
        if size:
            out["outline_size"] = size

    if "layers" in wanted:
        names = sorted({str(row.get("name") or "").strip() for row in _rows(ir, "layers")} - {""})
        if names:
            out["layers"] = names[:MAX_LIST_ITEMS]

    if "blocks" in wanted:
        names = sorted({str(row.get("name") or "").strip() for row in _rows(ir, "blocks")} - {""})
        if names:
            out["blocks"] = names[:MAX_LIST_ITEMS]

    if "annotated_dimensions" in wanted:
        values: List[float] = []
        for row in _rows(ir, "dimensions"):
            number = _finite(row.get("measured_value"))
            if number is not None:
                values.append(number)
        if values:
            # 标注尺寸**不截断**：它是差异匹配的主证据，且真样本 酒盒.dwg 有 316 条
            # （> MAX_LIST_ITEMS=200）—— 红测 H1 的金标就是 316（Spec §2.6 优先于 §2.4
            # 表里那句「截断到 MAX_LIST_ITEMS」，两者冲突，按真样本金标落地）。
            out["annotated_dimensions"] = values

    if "text_annotations" in wanted and texts:
        out["text_annotations"] = list(texts[:MAX_LIST_ITEMS])

    if "material_notes" in wanted:
        notes = [text for text in texts if _is_material_note(text)][:MAX_MATERIAL_NOTES]
        if notes:
            out["material_notes"] = notes

    for key in KEYWORD_FIELDS:
        if key in wanted and _keyword_hit(texts, key):
            out[key] = True

    if "closure_type" in wanted:
        value = _closure_type(texts)
        if value:
            out["closure_type"] = value

    # box_features / unfolded_size 本批不产（属于技术工艺的语义层与零件提取）：坚持不给键。
    return out


# --------------------------------------------------------------------------- #
# 默认依赖：把既有能力接上来（注入缝的唯一默认实现）
# --------------------------------------------------------------------------- #
class _DefaultDeps:
    """生产路径：`cad_converter` 探测 + 转换，`cad_ir` 解析。**不做任何持久化登记**。"""

    def capability(self) -> dict:
        from . import cad_converter
        return cad_converter.capability() or {}

    def convert(self, project_id, filename, content) -> dict:
        from . import cad_converter
        from .cad_converter import persistence
        manifest = cad_converter.convert_drawing(project_id, filename, bytes(content)) or {}
        dxf, artifact = _read_dxf_artifact(project_id, manifest, persistence)
        return {
            "dxf": dxf,
            "provider": str(manifest.get("converter_name") or ""),
            "provider_version": str(manifest.get("converter_version") or ""),
            "status": str(manifest.get("status") or ""),
            "warnings": [str(item) for item in (manifest.get("warnings") or [])],
            "conversion_id": str(manifest.get("conversion_id") or ""),
            "dxf_artifact": artifact,
            "converter_name": str(manifest.get("converter_name") or ""),
            "converter_version": str(manifest.get("converter_version") or ""),
            "detected_dwg_version": str(manifest.get("detected_dwg_version") or ""),
            "drawing_version": manifest.get("drawing_version") or 1,
            "conversion_status": str(manifest.get("status") or ""),
            "converter_role": str(manifest.get("converter_role") or ""),
            "fallback_used": bool(manifest.get("fallback_used")),
            "source_sha256": str(manifest.get("source_sha256") or ""),
            "quality": copy.deepcopy(manifest.get("quality") or {}),
            "crosscheck": copy.deepcopy(manifest.get("crosscheck") or {}),
        }

    def parse_dxf(self, content, filename, source) -> dict:
        from . import cad_ir
        return cad_ir.parse_dxf(bytes(content), filename=str(filename or "drawing.dxf"),
                                source=dict(source or {}))


def _read_dxf_artifact(project_id: str, manifest: dict, persistence) -> Tuple[bytes, str]:
    """按 `output_files` 里 `role == "dxf"` 的产物读字节（Spec §2.1）。"""
    entry = None
    for item in (manifest.get("output_files") or []):
        if isinstance(item, dict) and str(item.get("role") or "") == "dxf":
            entry = item
            break
    if entry is None:
        return b"", ""
    filename = str(entry.get("filename") or "").strip()
    if not filename:
        return b"", ""
    path = persistence.artifact_dir(str(project_id), str(manifest.get("conversion_id") or "")) / filename
    try:
        return path.read_bytes(), filename
    except OSError:
        return b"", filename
