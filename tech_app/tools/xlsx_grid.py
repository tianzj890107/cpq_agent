"""工作簿 → 纯数据网格（离线工具侧的唯一入口）。

为什么单独放这里：`packaging-cost-rule-snapshot.md` §4.3 明令**生产后端不得依赖
openpyxl**（`test_d1_backend_never_imports_openpyxl` 逐文件扫 `tech_app/backend/**`）。
业务部件导入器（`tech_app/backend/services/packaging_part_authority.py`）必须能读客户
工作簿，但自己不许出现那个依赖 —— 于是把"读 xlsx"这件事收在这一个工具模块里，后端只拿
**纯 dict 网格**（格子值 / 合并区 / 图片锚点），既不起 openpyxl，也不认识 xlsx 格式。

只读：不改工作簿、不写文件、不联网。`openpyxl` 在函数内部才导入 —— 后端导入本模块
时不会把它拉进 `sys.modules`。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def _column_letter(col: int) -> str:
    letters = ""
    while col:
        col, remainder = divmod(col - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _sheet_grid(ws: Any) -> Dict[str, Any]:
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
        images.append({"index": index,
                       "anchor_row": int(getattr(start, "row", 0) or 0) + 1,
                       "anchor_col": int(getattr(start, "col", 0) or 0) + 1})
    return {"name": ws.title, "state": getattr(ws, "sheet_state", "visible"),
            "max_row": int(ws.max_row or 0), "max_column": int(ws.max_column or 0),
            "cells": cells, "merged": merged, "images": images}


def read_grid(source: Any, *, sheet: Optional[str] = None) -> Dict[str, Any]:
    """读工作簿 → `{sheet_order, sheets, source}`（格子值是原始类型，不做任何解释）。

    `source` 是路径（`str` / `Path`）或工作簿字节；`sheet` 只影响 `source["picked"]`
    （哪张表被选中由调用方按表头语义决定，这里不认识业务）。
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
    sheets = {name: _sheet_grid(book[name]) for name in book.sheetnames}
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
