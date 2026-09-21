"""DWG 受控转换服务（转换适配器层）—— DWG 支持第 2 批。

对外稳定接口（Spec §2.1）。本包只做「DWG → 中间格式」的受控转换：不解析 DXF 实体、
不识别盒型、不调模型、不联网（外部服务适配器留作扩展点，本批不实现）。

能力声明口径：只完成编排层时只能写「**DWG 编排能力完成，真实转换能力未验收**」，
`capability().support_claim == "conversion_available"` 且 `dwg_supported is False`；
「支持 DWG」要等第 6 批金标通过。
"""
from __future__ import annotations

from .errors import CONVERSION_ERROR_CODES, CONVERSION_ERROR_CODE_NAMES, FileCapabilityError
from .service import (
    SIMULATED_ADAPTER_NAME,
    capability,
    convert_drawing,
    get_adapter,
    STATUS_OK,
    STATUS_WITH_WARNINGS,
    SUCCESS_STATUSES,
    latest_manifest,
    list_adapters,
    list_conversions,
    load_manifest,
)

__all__ = [
    "CONVERSION_ERROR_CODES",
    "CONVERSION_ERROR_CODE_NAMES",
    "FileCapabilityError",
    "SIMULATED_ADAPTER_NAME",
    "capability",
    "convert_drawing",
    "get_adapter",
    "latest_manifest",
    "STATUS_OK",
    "STATUS_WITH_WARNINGS",
    "SUCCESS_STATUSES",
    "list_adapters",
    "list_conversions",
    "load_manifest",
]
