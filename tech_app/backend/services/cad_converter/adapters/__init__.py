"""转换适配器集合：协议、fake（CI）、本地 CLI 骨架、外部服务扩展点。"""
from __future__ import annotations

from .base import ADAPTER_METHODS, SOURCE_FILENAME, ConversionRequest, ConversionResult

__all__ = ["ADAPTER_METHODS", "SOURCE_FILENAME", "ConversionRequest",
           "ConversionResult", "fake", "local_cli", "remote"]
