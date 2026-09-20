"""DWG 转换的稳定错误码闭集（Spec §4.1）。

本表**派生自**第 1 批的权威表 `file_preflight.STABLE_ERROR_CODES`：两批的口径必须
逐条一致（HTTP 状态、可重试性、中文文案），所以这里不复制字面量，只声明本批用到的
8 个码，数值与文案全部现取。第 1 批 Spec §3 是唯一权威来源。
"""
from __future__ import annotations

from .. import file_preflight

#: 本批可触发的错误码（闭集，顺序即声明顺序）。
CONVERSION_ERROR_CODE_NAMES = (
    "DWG_CONVERTER_NOT_INSTALLED",
    "DWG_CONVERSION_FAILED",
    "DWG_CONVERSION_TIMEOUT",
    "DWG_CONVERTER_OUTPUT_MISSING",
    "DWG_CONVERTER_OUTPUT_INVALID",
    "DWG_CONVERTER_OUTPUT_TOO_LARGE",
    "DWG_CONVERTER_UNSAFE_PATH",
    "FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION",
)

#: 码 -> {http_status, retryable, message}；与第 1 批权威表逐条同值。
CONVERSION_ERROR_CODES = {
    code: dict(file_preflight.STABLE_ERROR_CODES[code])
    for code in CONVERSION_ERROR_CODE_NAMES
}

#: 失败一律抛这个类型（第 1 批定义，带稳定码/HTTP/可重试性/中文文案）。
FileCapabilityError = file_preflight.FileCapabilityError
