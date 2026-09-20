"""文件能力预检：按内容识别真实格式、判定本环境可用能力、给出稳定错误码。

为什么要有这一层（Spec `docs/specs/dwg-file-capability-preflight.md`）：
  · 后端过去只按扩展名分类，DWG 落 `unsupported/none`，却仍然无条件把原始字节塞成
    图像块送模型（`qwen_client._media_type_for(".dwg")` 兜底 `image/png`），
    最终拿模型报的 "image format is illegal" 当结论；
  · 3D 入口不校验格式，DWG 先建项目、再建注定失败的 `import_step` 任务。

本模块是**纯函数**：不联网、不调模型、不读写库、不装依赖、不引入第三方包。
判定依据一律是**内容**（magic / 结构），扩展名只用来标记「扩展名与内容不一致」。

口径是全文唯一事实源：`STABLE_ERROR_CODES` 是 Spec §3 的九条稳定错误码闭集
（DWG 第 2 批的转换错误码在同一张表里扩展，见 Spec §3 后段）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Dict, Optional

#: `detected_format` 闭集（Spec §2）
DETECTED_FORMATS = (
    "dwg", "dxf", "step", "iges", "stl", "pdf", "raster_image", "text", "docx",
    "unsupported",
)

#: 稳定错误码闭集（Spec §3）：码 -> {http_status, retryable, message}
STABLE_ERROR_CODES: Dict[str, Dict[str, Any]] = {
    "FILE_EMPTY": {
        "http_status": 400, "retryable": True,
        "message": "文件为空，请重新上传",
    },
    "FILE_TOO_LARGE": {
        "http_status": 413, "retryable": True,
        "message": "文件超过大小上限",
    },
    "FILE_EXTENSION_CONTENT_MISMATCH": {
        "http_status": 422, "retryable": True,
        "message": "文件扩展名与实际内容不一致",
    },
    "FILE_CORRUPTED": {
        "http_status": 422, "retryable": True,
        "message": "文件不完整或已损坏",
    },
    "DWG_CONVERTER_NOT_INSTALLED": {
        "http_status": 415, "retryable": True,
        "message": "已识别为 DWG；当前环境尚未安装 CAD 转换服务，暂时无法解析",
    },
    "DWG_NOT_A_3D_MODEL": {
        "http_status": 415, "retryable": False,
        "message": "DWG 不是 3D 实体格式，无法直接做 3D 解析",
    },
    "FILE_FORMAT_UNSUPPORTED": {
        "http_status": 415, "retryable": False,
        "message": "该文件格式暂不支持解析",
    },
    "DWG_CONVERSION_FAILED": {
        "http_status": 502, "retryable": True,
        "message": "DWG 转换失败",
    },
    "DWG_PARSE_FAILED": {
        "http_status": 502, "retryable": True,
        "message": "DWG 转换成功但图纸解析失败",
    },
    # 以下 6 条由 DWG 第 2 批「受控转换服务」提出，Spec §3 已把它们收进**同一闭集**：
    # 本表是唯一权威来源，第 2 批的 `CONVERSION_ERROR_CODES` 必须是本表的子集且
    # 数值逐条一致。本批没有转换器，因此这些码只登记、不触发。
    "DWG_CONVERSION_TIMEOUT": {
        "http_status": 504, "retryable": True,
        "message": "DWG 转换超时，请稍后重试",
    },
    "DWG_CONVERTER_OUTPUT_MISSING": {
        "http_status": 502, "retryable": True,
        "message": "转换器没有产出可用图纸文件",
    },
    "DWG_CONVERTER_OUTPUT_INVALID": {
        "http_status": 502, "retryable": True,
        "message": "转换产出的图纸文件为空或格式无效",
    },
    "DWG_CONVERTER_OUTPUT_TOO_LARGE": {
        "http_status": 502, "retryable": True,
        "message": "转换产出超过大小上限",
    },
    "DWG_CONVERTER_UNSAFE_PATH": {
        "http_status": 500, "retryable": False,
        "message": "转换器返回了不安全的输出路径",
    },
    "FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION": {
        "http_status": 500, "retryable": False,
        "message": "测试用转换器不允许在生产环境启用",
    },
}

# --------------------------------------------------------------------------- #
# 格式指纹
# --------------------------------------------------------------------------- #
_MAGIC_BYTES = 16

#: 预检采样上限：识别 DWG/DXF 只需文件头（版本号 + 0x80 的加密哨兵）与末尾的
#: 段落标记，没有理由为一次预检把 GB 级文件整体读进内存做摘要。超过上限时
#: `file_size` / `sha256` 描述的是**样本**（前 80 MiB）；真正的上传上限由接口层
#: 的 `MAX_UPLOAD_BYTES` 单独判定，不在这里重复。
PREFLIGHT_SAMPLE_LIMIT_BYTES = 80 * 1024 * 1024

_DWG_VERSION_RE = re.compile(r"^AC10(\d{2})$")
#: R2004+（AC1018 起）文件头在 0x80 处有一段用固定掩码加密的哨兵 `AcFssFcAJMB`；
#: 能解出哨兵说明文件头**结构完整**（只有 6 字节版本号的 stub 解不出来）。
#: 来源：Open Design Alliance "DWG File Format" 的 R2004 文件头说明。
_DWG_SENTINEL = b"AcFssFcAJMB"
_DWG_SENTINEL_OFFSET = 0x80
_DWG_R2004_MASK = bytes((
    0x29, 0x23, 0xBE, 0x84, 0xE1, 0x6C, 0xD6, 0xAE, 0x52, 0x90, 0x49, 0xF1,
    0xF1, 0xBB, 0xE9, 0xEB, 0xB3, 0xA6, 0xDB, 0x3C, 0x87, 0x0C, 0x3E, 0x99,
    0x24, 0x5E, 0x0D, 0x1C, 0x06, 0xB7, 0x47, 0xDE,
))
#: R13~R2000（AC1015~AC1017）没有可离线校验的加密哨兵，只做保守的**尺寸下限**：
#: 连文件头与段定位表都装不下的文件一律判截断。
_DWG_MIN_BYTES = 128

_DXF_BINARY_MAGIC = b"AutoCAD Binary DXF"
_DXF_MARKER = b"SECTION"
_DXF_SCAN_BYTES = 4096

_PDF_MAGIC = b"%PDF-"
_STEP_MAGIC = b"ISO-10303-21"
_IGES_MAGIC = b"IGES"
_ZIP_MAGIC = b"PK\x03\x04"
_RASTER_MAGICS = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)

#: 每种格式「扩展名与内容一致」的可接受扩展名（用于 extension_content_mismatch）
_EXPECTED_EXTENSIONS: Dict[str, frozenset] = {
    "dwg": frozenset({".dwg"}),
    "dxf": frozenset({".dxf"}),
    "step": frozenset({".step", ".stp"}),
    "iges": frozenset({".iges", ".igs"}),
    "stl": frozenset({".stl"}),
    "pdf": frozenset({".pdf"}),
    "raster_image": frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}),
    "text": frozenset({".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml",
                       ".ini"}),
    "docx": frozenset({".docx"}),
}
#: 只收「内容嗅探得到的格式」的扩展名：`.sat`/`.sldprt` 这类我们无法按内容判定的
#: 扩展名不在此列，避免把「没装解析器」误报成「扩展名与内容不一致」。
_KNOWN_EXTENSIONS = frozenset(
    ext for exts in _EXPECTED_EXTENSIONS.values() for ext in exts
)

#: 给用户看的中文格式名（只用于文案，不参与判定）
_FORMAT_LABELS: Dict[str, str] = {
    "dwg": "DWG", "dxf": "DXF", "step": "STEP", "iges": "IGES", "stl": "STL",
    "pdf": "PDF", "raster_image": "图片", "text": "文本", "docx": "Word 文档",
    "unsupported": "未知二进制",
}

#: 选中管线（审计字段 `selected_pipeline`，Spec §7）
_PIPELINE_BY_FORMAT: Dict[str, str] = {
    "dwg": "dwg_converter",
    "dxf": "cad_vector_parse",
    "step": "step_import",
    "iges": "cad_import",
    "stl": "cad_import",
    "pdf": "document_text",
    "raster_image": "direct_vision",
    "text": "document_text",
    "docx": "document_text",
    "unsupported": "none",
}

#: 能力矩阵（Spec §2）。第 1 批不装转换器，`converter_available` 恒为 False。
_CAPABILITIES: Dict[str, Dict[str, bool]] = {
    "dwg": {"direct_vision": False, "cad_vector_parse": False,
            "converter_required": True, "converter_available": False,
            "step_import": False, "geometry_3d": False, "document_text": False},
    "dxf": {"direct_vision": False, "cad_vector_parse": False,
            "converter_required": True, "converter_available": False,
            "step_import": False, "geometry_3d": False, "document_text": False},
    "step": {"direct_vision": False, "cad_vector_parse": False,
             "converter_required": False, "converter_available": False,
             "step_import": True, "geometry_3d": True, "document_text": False},
    "iges": {"direct_vision": False, "cad_vector_parse": False,
             "converter_required": False, "converter_available": False,
             "step_import": False, "geometry_3d": False, "document_text": False},
    "stl": {"direct_vision": False, "cad_vector_parse": False,
            "converter_required": False, "converter_available": False,
            "step_import": False, "geometry_3d": False, "document_text": False},
    "pdf": {"direct_vision": True, "cad_vector_parse": False,
            "converter_required": False, "converter_available": False,
            "step_import": False, "geometry_3d": False, "document_text": True},
    "raster_image": {"direct_vision": True, "cad_vector_parse": False,
                     "converter_required": False, "converter_available": False,
                     "step_import": False, "geometry_3d": False,
                     "document_text": False},
    "text": {"direct_vision": False, "cad_vector_parse": False,
             "converter_required": False, "converter_available": False,
             "step_import": False, "geometry_3d": False, "document_text": True},
    "docx": {"direct_vision": False, "cad_vector_parse": False,
             "converter_required": False, "converter_available": False,
             "step_import": False, "geometry_3d": False, "document_text": True},
    "unsupported": {"direct_vision": False, "cad_vector_parse": False,
                    "converter_required": False, "converter_available": False,
                    "step_import": False, "geometry_3d": False,
                    "document_text": False},
}

#: 主文件能走「直接视觉」或「本地文本提取」以外的格式，需要额外能力才能解析。
#: 失败码是本批的**稳定口径**：DWG 固定 DWG_CONVERTER_NOT_INSTALLED，
#: 其余（DXF 待第 3 批矢量解析、3D 走 3D 入口、未知格式）走 FILE_FORMAT_UNSUPPORTED。
_GATE_ERROR_CODE: Dict[str, str] = {
    "dwg": "DWG_CONVERTER_NOT_INSTALLED",
    "dxf": "FILE_FORMAT_UNSUPPORTED",
}


class FileCapabilityError(Exception):
    """文件能力失败：结构化、稳定、可重试性明确，可直接进响应与审计。"""

    def __init__(self, stable_error_code: str, detected: Optional[dict] = None,
                 message: Optional[str] = None, *, http_status: Optional[int] = None,
                 retryable: Optional[bool] = None) -> None:
        spec = STABLE_ERROR_CODES.get(str(stable_error_code)) or {}
        self.stable_error_code = str(stable_error_code)
        self.http_status = int(http_status if http_status is not None
                               else spec.get("http_status", 500))
        self.retryable = bool(retryable if retryable is not None
                              else spec.get("retryable", False))
        self.message = str(message or spec.get("message") or "文件无法解析")
        # 预检结果照原样带出（审计与响应共用），另补三个终态字段：
        # 失败现场必须能回答「哪一步、什么码、能不能重试」（Spec §7）。
        payload = dict(detected or {})
        payload.setdefault("parse_status", "blocked")
        payload["stable_error_code"] = self.stable_error_code
        payload["retryable"] = self.retryable
        self.detected = payload
        super().__init__(self.message)

    def as_detail(self) -> dict:
        """FastAPI `HTTPException(detail=...)` 的四键响应体（Spec §5）。"""
        return {
            "stable_error_code": self.stable_error_code,
            "message": self.message,
            "detected": self.detected,
            "retryable": self.retryable,
        }


# --------------------------------------------------------------------------- #
# 预检纯函数
# --------------------------------------------------------------------------- #
def _as_bytes(content: Any) -> bytes:
    if content is None:
        return b""
    if isinstance(content, (bytes, bytearray, memoryview)):
        return bytes(content)
    raise TypeError("content 必须是 bytes")


def _magic_of(data: bytes) -> str:
    """前 16 字节的 ASCII 可打印形式，不可打印用 `.` 占位。"""
    return "".join(chr(byte) if 32 <= byte < 127 else "." for byte in data[:_MAGIC_BYTES])


def _dwg_version_of(data: bytes) -> str:
    if len(data) < 6:
        return ""
    header = data[:6]
    text = header.decode("ascii", errors="replace")
    matched = _DWG_VERSION_RE.match(text)
    if not matched:
        return ""
    number = int(matched.group(1))
    if 6 <= number <= 32:
        return text
    return ""


def _looks_like_text(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:65536]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(1 for byte in sample if 32 <= byte < 127 or byte in (9, 10, 13))
    return printable >= len(sample) * 0.95


def _is_ascii_dxf(data: bytes) -> bool:
    head = data[:_DXF_SCAN_BYTES]
    if b"\x00" in head:
        return False
    return _DXF_MARKER in head


def _is_iges(data: bytes) -> bool:
    if data.startswith(_IGES_MAGIC):
        return True
    # IGES 定长 80 列，第 73 列（下标 72）是段码 S/G/D/T/P。
    return len(data) >= 80 and data[72:73] == b"S" and data[:1] in (b"S", b" ")


def _is_stl(data: bytes) -> bool:
    if data[:6] == b"solid ":
        return True
    if len(data) >= 84:
        triangles = int.from_bytes(data[80:84], "little")
        return 84 + triangles * 50 == len(data)
    return False


def _raster_magic(data: bytes) -> str:
    for magic, _ext in _RASTER_MAGICS:
        if data.startswith(magic):
            return magic.decode("latin-1")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "RIFF.WEBP"
    return ""


def _format_of(extension: str, data: bytes) -> str:
    """内容优先的格式判定（Spec §2.1：不许只看扩展名）。"""
    if _dwg_version_of(data):
        return "dwg"
    if data.startswith(_DXF_BINARY_MAGIC) or _is_ascii_dxf(data):
        return "dxf"
    if data.startswith(_STEP_MAGIC):
        return "step"
    if _is_iges(data):
        return "iges"
    if _is_stl(data):
        return "stl"
    if data.startswith(_PDF_MAGIC):
        return "pdf"
    if _raster_magic(data) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "raster_image"
    if data.startswith(_ZIP_MAGIC):
        return "docx" if extension == ".docx" else "unsupported"
    if _looks_like_text(data):
        return "text"
    return "unsupported"


def _content_kind(data: bytes) -> str:
    if _looks_like_text(data):
        return "text"
    return "binary"


def _extension_content_mismatch(detected_format: str, extension: str,
                                is_empty: bool) -> bool:
    if is_empty or not extension:
        return False
    expected = _EXPECTED_EXTENSIONS.get(detected_format)
    if expected is None:
        # 内容判不出可用格式：扩展名却在宣称某种已知格式，就是扩展名与内容不一致
        return extension in _KNOWN_EXTENSIONS
    return extension not in expected


def _is_truncated(detected_format: str, data: bytes) -> bool:
    """只有 DWG / DXF 有可离线校验的结构完整性（Spec §2 的字段定义）。"""
    if detected_format == "dwg":
        version = _dwg_version_of(data)
        if not version:
            return True
        number = int(version[4:6])
        if number >= 18:  # R2004+：必须能解出 0x80 处的加密哨兵
            offset = _DWG_SENTINEL_OFFSET
            blob = data[offset:offset + len(_DWG_SENTINEL) + 4]
            if len(blob) < len(_DWG_SENTINEL):
                return True
            decoded = bytes(byte ^ _DWG_R2004_MASK[i] for i, byte in enumerate(blob))
            return not decoded.startswith(_DWG_SENTINEL)
        return len(data) < _DWG_MIN_BYTES
    if detected_format == "dxf":
        if data.startswith(_DXF_BINARY_MAGIC):
            return len(data) < _DXF_SCAN_BYTES
        return b"EOF" not in data[-_DXF_SCAN_BYTES:]
    return False


def detect_file_format(filename: str, content: Any) -> dict:
    """按内容识别文件真实格式与能力判定所需的事实字段（Spec §2）。"""
    data = _as_bytes(content)
    sample = data[:PREFLIGHT_SAMPLE_LIMIT_BYTES]
    name = Path(str(filename or "")).name
    extension = Path(name).suffix.lower()
    is_empty = not sample
    detected_format = "unsupported" if is_empty else _format_of(extension, sample)
    return {
        "original_filename": name,
        "extension": extension,
        "detected_format": detected_format,
        "magic": _magic_of(sample),
        "dwg_version": _dwg_version_of(sample),
        "extension_content_mismatch": _extension_content_mismatch(
            detected_format, extension, is_empty),
        "file_size": len(sample),
        "sha256": hashlib.sha256(sample).hexdigest(),
        "is_empty": is_empty,
        "is_truncated": False if is_empty else _is_truncated(detected_format, sample),
        "content_kind": _content_kind(sample),
    }


def capabilities_of(detected: Optional[dict]) -> dict:
    """按预检结果给出本环境可用能力（Spec §2）；未知格式一律按 unsupported。"""
    detected_format = str((detected or {}).get("detected_format") or "unsupported")
    caps = _CAPABILITIES.get(detected_format) or _CAPABILITIES["unsupported"]
    return dict(caps)


def selected_pipeline(detected: Optional[dict]) -> str:
    """本次该走哪条管线（审计字段；DWG 是 `dwg_converter`，本批未安装）。"""
    detected_format = str((detected or {}).get("detected_format") or "unsupported")
    return _PIPELINE_BY_FORMAT.get(detected_format, "none")


def vision_gate_error(detected: Optional[dict]) -> Optional[FileCapabilityError]:
    """图纸解析入口（视觉/文本）门禁：构造内容块**之前**判定，绝不把
    模型读不了的文件字节塞成图像块。

    可消费 = 直接视觉（图片/PDF）或本地文本提取（PDF/DOCX/TXT）；其余
    （DWG/DXF/3D 模型/未知格式）一律先失败。
    """
    caps = capabilities_of(detected)
    if caps["direct_vision"] or caps["document_text"]:
        return None
    info = detected or {}
    detected_format = str(info.get("detected_format") or "unsupported")
    version = str(info.get("dwg_version") or "")
    name = str(info.get("original_filename") or "文件")
    if info.get("is_empty"):
        return FileCapabilityError("FILE_EMPTY", detected=detected,
                                   message=f"文件 {name} 为空，请重新上传")
    # 扩展名与内容不一致要**单独报**：把 酒盒.dwg 改名成 酒盒.png 却报「DWG 缺转换器」，
    # 用户永远查不到真正的原因（Spec §10）。
    if info.get("extension_content_mismatch"):
        return FileCapabilityError(
            "FILE_EXTENSION_CONTENT_MISMATCH", detected=detected,
            message=_mismatch_message(detected_format, version))
    if info.get("is_truncated") and detected_format in {"dwg", "dxf"}:
        return FileCapabilityError("FILE_CORRUPTED", detected=detected)
    code = _GATE_ERROR_CODE.get(detected_format, "FILE_FORMAT_UNSUPPORTED")
    message = None
    if detected_format == "dwg":
        # 用户该看到的是「识别到了什么」：版本号让「已识别为 DWG」这句话有据可查。
        message = (f"已识别为 {_FORMAT_LABELS['dwg']}（{version}）；"
                   f"当前环境尚未安装 CAD 转换服务，暂时无法解析") if version else None
    return FileCapabilityError(code, detected=detected, message=message)


def import_3d_gate_error(detected: Optional[dict]) -> Optional[FileCapabilityError]:
    """3D（STEP）入口门禁：非 STEP 内容一律同步拒绝，绝不建项目/建任务。

    **DWG 不是 3D 模型**：即使文件里含三维实体，现有链路也无法判定，必须让用户
    改走 2D 图纸流程（`DWG_NOT_A_3D_MODEL`，不可重试）。
    """
    info = detected or {}
    detected_format = str(info.get("detected_format") or "unsupported")
    version = str(info.get("dwg_version") or "")
    if info.get("is_empty"):
        return FileCapabilityError("FILE_EMPTY", detected=info)
    if info.get("extension_content_mismatch"):
        return FileCapabilityError(
            "FILE_EXTENSION_CONTENT_MISMATCH", detected=info,
            message=_mismatch_message(detected_format, version))
    if detected_format == "dwg":
        message = (f"已识别为 {_FORMAT_LABELS['dwg']}（{version}）；"
                   f"DWG 不是 3D 实体格式，无法直接做 3D 解析") if version else None
        return FileCapabilityError("DWG_NOT_A_3D_MODEL", detected=info, message=message)
    if detected_format != "step":
        return FileCapabilityError("FILE_FORMAT_UNSUPPORTED", detected=info)
    return None


def _mismatch_message(detected_format: str, version: str = "") -> str:
    """扩展名与内容不一致时的用户文案：必须说清**实际内容是什么**。"""
    label = _FORMAT_LABELS.get(detected_format, detected_format)
    if detected_format == "dwg" and version:
        label = f"{label} {version}"
    return f"文件扩展名与实际内容不一致（内容实为 {label}）"


def audit_entry(detected: Optional[dict], *, selected: Optional[str] = None,
                parse_status: str = "blocked",
                error: Optional[FileCapabilityError] = None) -> dict:
    """Spec §7 的审计字段（不含原始字节/base64/密钥/堆栈/绝对路径）。"""
    info = detected or {}
    return {
        "original_filename": info.get("original_filename") or "",
        "detected_format": info.get("detected_format") or "unsupported",
        "extension": info.get("extension") or "",
        "magic": info.get("magic") or "",
        "dwg_version": info.get("dwg_version") or "",
        "file_size": int(info.get("file_size") or 0),
        "sha256": info.get("sha256") or "",
        "selected_pipeline": selected or selected_pipeline(info),
        "converter_available": bool(capabilities_of(info)["converter_available"]),
        "parse_status": str(parse_status or ""),
        "stable_error_code": error.stable_error_code if error else "",
        "retryable": bool(error.retryable) if error else False,
    }
