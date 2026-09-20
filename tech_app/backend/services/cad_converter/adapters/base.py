"""转换适配器协议（Spec §2.2）。

适配器只回答「怎么把 DWG 转成中间格式」这一件事：**不读数据库、不读项目目录、
不看 Agent / 路由**。所有关联信息（项目、附件、图纸版本、来源摘要、超时、输出
目录）都由编排层构造好放进 `ConversionRequest`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 输入文件在临时目录里的**固定安全名**：转换器永远拿不到用户可控路径，
#: 用户原名只出现在 manifest 的 `original_filename`（且已清洗成 basename）。
SOURCE_FILENAME = "source.dwg"

#: 适配器必须实现且只实现这 5 个方法（Spec §2.2）。
ADAPTER_METHODS = ("capability", "inspect", "convert_to_dxf", "render_preview",
                   "convert_3d_if_supported")


@dataclass(frozen=True)
class ConversionRequest:
    """编排层构造、适配器只读。`output_dir` 是适配器**唯一允许写入**的目录。"""

    project_id: str
    attachment_name: str
    drawing_version: int
    source_path: Path
    source_sha256: str
    source_format: str
    detected_dwg_version: str
    output_dir: Path
    timeout_seconds: float
    options: Dict[str, Any] = field(default_factory=dict)
    conversion_id: str = ""

    @property
    def source_filename(self) -> str:
        """固定 `source.dwg`——与用户原始文件名无关（Spec §2.2）。"""
        return SOURCE_FILENAME


@dataclass
class ConversionResult:
    """适配器一次转换的回执；也允许适配器直接返回同形状的 dict。"""

    status: str = "ok"
    dxf_path: Optional[Path] = None
    preview_paths: List[Path] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error_code: Optional[str] = None
    stderr_digest: Optional[str] = None
    three_d: Dict[str, Any] = field(default_factory=dict)


def result_field(result: Any, name: str, default: Any = None) -> Any:
    """从 dict 或对象形状的回执里取字段（适配器两种写法都允许）。"""
    if isinstance(result, dict):
        value = result.get(name, default)
    else:
        value = getattr(result, name, default)
    return default if value is None else value
