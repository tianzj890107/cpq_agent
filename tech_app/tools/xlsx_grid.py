"""工作簿 → 纯数据网格（离线工具侧的唯一入口）。

为什么单独放这里：`packaging-cost-rule-snapshot.md` §4.3 明令**生产后端不得依赖
openpyxl**（`test_d1_backend_never_imports_openpyxl` 逐文件扫 `tech_app/backend/**`）。
业务部件导入器（`tech_app/backend/services/packaging_reference_workbook.py`）必须能读客户
工作簿，但自己不许出现那个依赖 —— 于是把"读 xlsx"这件事收在这一个工具模块里，后端只拿
**纯 dict 网格**（格子值 / 合并区 / 图片锚点），既不起 openpyxl，也不认识 xlsx 格式。

只读：不改工作簿、不写文件、不联网。`openpyxl` 在函数内部才导入 —— 后端导入本模块
时不会把它拉进 `sys.modules`。
"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Any, Dict, List, Optional

#: 图片字节的魔数 → media_type（Spec 「部件图本体落地」 §C1）。
#: 只认这几种；认不出一律 `application/octet-stream`（宁可保守，不猜格式）。
IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))

IMAGE_UNAVAILABLE = "image_bytes_unreadable"


def media_type_of(data: Any) -> str:
    """字节魔数 → media_type（Spec §C1）。空 / 认不出 → `application/octet-stream`。"""
    raw = bytes(data) if isinstance(data, (bytes, bytearray)) else b""
    for magic, media_type in IMAGE_MAGIC:
        if raw.startswith(magic):
            return media_type
    return "application/octet-stream"


def image_payload(image: Any) -> Dict[str, Any]:
    """一张工作簿图片 → 纯数据（Spec §C1）：

    关键约束：openpyxl 的 `Image._data()` 会把图片流读干，**同一张图只能读一次**
    （第二次 `ValueError: I/O operation on closed file`，本机实测）。所以字节在这里
    一次取完并缓存成 base64 字符串；之后任何调用方都从这份缓存走，不许再摸流。

    取不到（流异常 / 空）**不抛异常**：按 `unavailable` 记，导入不能因为一张坏图整份失败。
    """
    blank = {"media_type": "application/octet-stream", "bytes": 0, "sha256": "",
             "content_base64": "", "unavailable": IMAGE_UNAVAILABLE}
    try:
        raw = image._data()                     # noqa: SLF001 - 唯一的取字节入口
    except Exception:                           # noqa: BLE001 - 坏图不许拦住整份导入
        return blank
    if isinstance(raw, (bytes, bytearray)):
        payload = bytes(raw)
    else:
        payload = b""
    if not payload:
        return blank
    return {"media_type": media_type_of(payload), "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "content_base64": base64.b64encode(payload).decode("ascii"), "unavailable": ""}


def _column_letter(col: int) -> str:
    letters = ""
    while col:
        col, remainder = divmod(col - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _sheet_grid(ws: Any, *, with_images: bool = False) -> Dict[str, Any]:
    cells: Dict[str, Any] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                cells[cell.coordinate] = cell.value
    merged: List[str] = []
    for merged_range in getattr(ws, "merged_cells", None).ranges if getattr(
            ws, "merged_cells", None) else []:
        merged.append(str(merged_range))
    images: List[Dict[str, int]] = []
    for index, image in enumerate(getattr(ws, "_images", []) or [], start=1):
        anchor = getattr(image, "anchor", None)
        start = getattr(anchor, "_from", None)
        item: Dict[str, Any] = {"index": index,
                                "anchor_row": int(getattr(start, "row", 0) or 0) + 1,
                                "anchor_col": int(getattr(start, "col", 0) or 0) + 1}
        if with_images:
            # 只有显式要字节时才取（既有调用方拿到的 images 逐字不变，Spec §C1）。
            item.update(image_payload(image))
        images.append(item)
    return {"name": ws.title, "state": getattr(ws, "sheet_state", "visible"),
            "max_row": int(ws.max_row or 0), "max_column": int(ws.max_column or 0),
            "cells": cells, "merged": merged, "images": images}


def read_grid(source: Any, *, sheet: Optional[str] = None,
              with_images: bool = False) -> Dict[str, Any]:
    """读工作簿 → `{sheet_order, sheets, source}`（格子值是原始类型，不做任何解释）。

    `source` 是路径（`str` / `Path`）或工作簿字节；`sheet` 只影响 `source["picked"]`
    （哪张表被选中由调用方按表头语义决定，这里不认识业务）。

    `with_images=True` 时每张图片额外带字节（`media_type` / `bytes` / `sha256` /
    `content_base64` / `unavailable`，见 `image_payload()`）；缺省仍是只给锚点 ——
    既有调用方（成本规则快照等）拿到的形状逐字不变。
    """
    import io as _io

    import openpyxl

    if isinstance(source, (bytes, bytearray)):
        book = openpyxl.load_workbook(_io.BytesIO(bytes(source)), data_only=False)
        file_name = ""
    else:
        path = os.fspath(source)
        book = openpyxl.load_workbook(path, data_only=False)
        file_name = os.path.basename(path)
    sheets = {name: _sheet_grid(book[name], with_images=with_images)
              for name in book.sheetnames}
    return {"sheet_order": list(book.sheetnames), "sheets": sheets,
            "source": {"file": file_name, "sheet": sheet or ""}}


def cell_value(sheet: Dict[str, Any], row: int, col: int) -> Any:
    """网格里取一个格子（坐标 1 基，与 Excel 一致）。"""
    return (sheet or {}).get("cells", {}).get("%s%d" % (_column_letter(col), row))


def column_of(coord: Any) -> int:
    """`"AB12"` → `28`（列号）。"""
    letters = "".join(char for char in str(coord or "") if char.isalpha())
    number = 0
    for char in letters.upper():
        number = number * 26 + (ord(char) - ord("A") + 1)
    return number


def row_of(coord: Any) -> int:
    digits = "".join(char for char in str(coord or "") if char.isdigit())
    return int(digits) if digits else 0
