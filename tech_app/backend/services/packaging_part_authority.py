"""业务部件权威清单导入器（Spec `packaging-business-parts-and-cad-plan-view.md` §3）。

《酒盒 报价资料.xlsx》的「零部件排版工艺」才说明业务人员说的"零部件"是什么：28 行部件 +
28 张部件图。这份资料是**唯一**的业务部件事实源，导入必须**确定性**：

- 只读工作簿结构与单元格，**不调模型、不联网**；
- 列用**表头语义**定位，不把 A/B/C 写死成业务逻辑（列序变了照样读对）；
- 只认**序号连续**的业务行：说明行、签名行、制表行、完全空行一律进 `skipped` 并写明原因；
- 空白单元格可能是合并单元格表达的"同上一组"：**保留原始空值**，只记 `merged_from`
  指向锚点 —— 不许把锚点的值复制下来伪装成这一行独立的事实；
- 尺寸支持 `x` / `×` / `X` 与可选 `mm`，原文永远保留。

本模块是**纯函数**：不改入参、不落盘、不写审计。读表依赖收在离线工具侧
（`tech_app.tools.xlsx_grid`，唯一认识 xlsx 的地方）—— 生产后端不直接依赖读表库
（Spec `packaging-cost-rule-snapshot.md` §4.3）。导入器**不**产出几何信息：几何是 CAD 的
事（`packaging_parts.py`），绑定另算。
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional, Tuple

ENGINE_VERSION = "packaging-part-authority/1"

#: 业务部件编码形状：`<标题派生前缀>-P<两位序号>`（Spec §1 的 `JWXR21-P01`）。
PART_CODE_PREFIX_FALLBACK = "PART"
BUSINESS_PART_CODE_FORMAT = "%s-P%02d"

#: 唯一业务表在工作簿里叫「零部件排版工艺」（尾部空格读取时规范化，Spec §1）。
DEFAULT_SHEET_NAME = "零部件排版工艺"

#: 表头语义 → 输出键。**顺序即匹配优先级**：先长后短，避免「材料名称」被「名称」抢走。
COLUMN_HINTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("sequence_no", ("序号", "序 号")),
    ("name", ("零件名称", "部件名称", "名称")),
    ("product_size_text", ("产品尺寸", "尺寸")),
    ("material_text", ("材料名称", "材料")),
    ("layout_text", ("报价排版", "排版")),
    ("process_text", ("工艺",)),
    ("thumbnail_ref", ("单个部件图", "部件图", "图片")),
    ("note", ("备注",)),
    ("quantity", ("数量", "用量")),
    ("purchase", ("外购",)),
)

#: 行被跳过的稳定原因码。
SKIP_REASONS = ("no_header", "blank_row", "no_sequence", "sequence_not_contiguous",
                "not_a_part_row", "duplicate_sequence")

#: 尺寸原文的写法：`307.07x528.89mm` / `125.2×54` / `15 X 5 X 2`。
SIZE_SPLIT = re.compile(r"\s*[x×X*]\s*")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value)).lower()


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def code_prefix(title: Any) -> str:
    """从标题派生业务编码前缀（`JW XR21 700ML物料清单与工艺` → `JWXR21`）。

    只取**以字母开头**、含字母的 ASCII 词元（`JW` / `XR21`），丢掉纯数字规格词
    （`700ML`）；中文部分（`物料清单与工艺`）与空白一律不算。派不出来就回 `PART`。
    """
    head = _text(title)
    head = head.split("物料清单")[0].split("报价")[0]
    tokens = re.findall(r"[A-Za-z0-9]+", head)
    kept = [token for token in tokens if token[0].isalpha()]
    prefix = "".join(kept).upper()
    return prefix or PART_CODE_PREFIX_FALLBACK


def parse_size(text: Any) -> Dict[str, Any]:
    """尺寸原文 → `{length_mm, width_mm, height_mm, raw}`（解析不出回空值，不猜）。"""
    raw = _text(text)
    result = {"length_mm": None, "width_mm": None, "height_mm": None, "raw": raw}
    if not raw:
        return result
    numbers: List[float] = []
    for item in (piece for piece in SIZE_SPLIT.split(raw) if _text(piece)):
        match = NUMBER.search(_text(item).replace("mm", ""))
        if match:
            numbers.append(float(match.group(0)))
    if len(numbers) >= 1:
        result["length_mm"] = numbers[0]
    if len(numbers) >= 2:
        result["width_mm"] = numbers[1]
    if len(numbers) >= 3:
        result["height_mm"] = numbers[2]
    return result


def _cell(sheet: Dict[str, Any], row: int, col: int) -> Any:
    from tech_app.tools import xlsx_grid

    return xlsx_grid.cell_value(sheet, row, col)


def _column_letter(col: int) -> str:
    letters = ""
    while col:
        col, remainder = divmod(col - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _columns(sheet: Dict[str, Any]) -> int:
    return max(int((sheet or {}).get("max_column") or 0), 1)


def _rows(sheet: Dict[str, Any]) -> int:
    return max(int((sheet or {}).get("max_row") or 0), 1)


def _sheet_title(sheet: Dict[str, Any]) -> str:
    for row in range(1, 4):
        for col in range(1, 4):
            value = _text(_cell(sheet, row, col))
            if value:
                return value
    return ""


def _header_row(sheet: Dict[str, Any], *, limit: int = 12) -> int:
    """表头行 = 同时出现「序号」与「名称」的行（表头不在第 2 行也读得对）。"""
    for row in range(1, min(limit, _rows(sheet)) + 1):
        joined = "".join(_text(_cell(sheet, row, col))
                         for col in range(1, _columns(sheet) + 1))
        if "序号" in joined and "名称" in joined:
            return row
    return 0


def _column_map(sheet: Dict[str, Any], header_row: int) -> Dict[str, int]:
    if not header_row:
        return {}
    taken: Dict[str, int] = {}
    for col in range(1, _columns(sheet) + 1):
        text = _norm(_cell(sheet, header_row, col))
        if not text:
            continue
        for key, hints in COLUMN_HINTS:
            if key in taken:
                continue
            if any(_norm(hint) in text for hint in hints):
                taken[key] = col
                break
    return taken


def _anchor_row(sheet: Dict[str, Any], row: int, col: int) -> int:
    """这个单元格是不是合并区的一部分；是且锚点在**上面** → 回锚点行号。"""
    from tech_app.tools import xlsx_grid

    target = "%s%d" % (_column_letter(col), row)
    for merged in (sheet or {}).get("merged") or []:
        first, _, last = str(merged).partition(":")
        if not last:
            last = first
        if (xlsx_grid.column_of(first) <= col <= xlsx_grid.column_of(last)
                and xlsx_grid.row_of(first) <= row <= xlsx_grid.row_of(last)):
            anchor = xlsx_grid.row_of(first)
            if anchor < row:
                return anchor
    _ = target
    return 0


def _merged_from(sheet: Dict[str, Any], row: int, columns: Dict[str, int]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for key, col in columns.items():
        anchor = _anchor_row(sheet, row, col)
        if anchor:
            result[key] = "%s%d" % (_column_letter(col), anchor)
    return result


def _thumbnail_index(sheet: Dict[str, Any]) -> Dict[int, List[str]]:
    """部件图按**图片锚点行**建索引：Excel 行号（1 基）→ 图片引用（可能多张）。"""
    result: Dict[int, List[str]] = {}
    title = _text((sheet or {}).get("name"))
    for image in (sheet or {}).get("images") or []:
        row = int(image.get("anchor_row") or 0)
        index = int(image.get("index") or 0)
        if not row:
            continue
        result.setdefault(row, []).append("image:%s!%d#%d" % (title, row, index))
    return result


def _part_row(sheet: Dict[str, Any], row: int, columns: Dict[str, int], *,
              thumbnails: Dict[int, List[str]]) -> Optional[Dict[str, Any]]:
    sequence = _num(_cell(sheet, row, columns["sequence_no"])) if "sequence_no" in columns else None
    name = _text(_cell(sheet, row, columns["name"])) if "name" in columns else ""
    if sequence is None or not name:
        return None
    values: Dict[str, Any] = {"sequence_no": int(sequence), "name": name}
    for key, col in columns.items():
        if key in ("sequence_no", "name", "thumbnail_ref"):
            continue
        values[key] = _cell(sheet, row, col)
    size = parse_size(values.get("product_size_text"))
    values["product_size_text"] = size["raw"]
    values["length_mm"] = size["length_mm"]
    values["width_mm"] = size["width_mm"]
    values["height_mm"] = size["height_mm"]
    for key in ("material_text", "layout_text", "process_text", "note", "quantity", "purchase"):
        values[key] = _text(values.get(key))
    # 部件图的锚点行是浮动单元格引用：真样本里 28 张图锚在 1..30 行，与 28 个部件行并非
    # 逐行对齐。这里只记**按锚点行就近**的候选；数量相等时由 `import_workbook()` 改成
    # **顺序一一对应**（见那里的注释）。
    refs = list(thumbnails.get(row, []))
    values["thumbnail_ref"] = refs[0] if refs else ""
    values["thumbnail_refs"] = refs
    values["merged_from"] = _merged_from(sheet, row, columns)
    values["group_hint"] = ("同上一行" if any(
        key in values["merged_from"] for key in ("material_text", "layout_text", "process_text"))
        else "")
    values["source"] = {"sheet": _text((sheet or {}).get("name")), "row": row}
    # 一个业务部件行至少要带**一件事实**（尺寸 / 材料 / 排版 / 工艺 / 部件图）。
    # 只有名字、什么都没有的行是说明行 —— 客户备注行常常也带一个序号（真样本第 32 行
    # 就是 `29` +「客人要求每个盒子需装配两包干燥剂…」），不能因为序号连续就当部件。
    if not any([values["length_mm"], values["width_mm"], values.get("material_text"),
                values.get("layout_text"), values.get("process_text"),
                values.get("thumbnail_ref")]):
        return None
    return values


def _pick_sheet(grid: Dict[str, Any], sheet: Optional[str] = None) -> Optional[Dict[str, Any]]:
    sheets = (grid or {}).get("sheets") or {}
    if sheet:
        wanted = _norm(sheet)
        for name in (grid.get("sheet_order") or list(sheets)):
            if _norm(name) == wanted or wanted in _norm(name):
                return sheets.get(name)
        return None
    wanted = _norm(DEFAULT_SHEET_NAME)
    for name in (grid.get("sheet_order") or list(sheets)):
        if _norm(name) == wanted:
            return sheets.get(name)
    for name in (grid.get("sheet_order") or list(sheets)):
        if _header_row(sheets.get(name) or {}):
            return sheets.get(name)
    first = (grid.get("sheet_order") or [])[:1]
    return sheets.get(first[0]) if first else None


def _file_hash(source: Any) -> str:
    digest = hashlib.sha256()
    if isinstance(source, (bytes, bytearray)):
        digest.update(bytes(source))
        return digest.hexdigest()
    path = os.fspath(source)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_workbook(source: Any, *, sheet: Optional[str] = None) -> Dict[str, Any]:
    """把权威资料工作簿导成业务部件清单（**确定性**、幂等、不改入参）。

    `source` 是路径（`str` / `Path`）或工作簿字节。返回文档见 Spec §1/§2：`parts` 是业务
    部件行，`skipped` 逐行写明为什么不是部件行，`source.code_prefix` 是派生的业务编码前缀
    （业务编码 `business_part_code` 由它 + 序号组成）。
    """
    from tech_app.tools import xlsx_grid

    digest = _file_hash(source)
    file_name = "" if isinstance(source, (bytes, bytearray)) else os.path.basename(os.fspath(source))
    grid = xlsx_grid.read_grid(source)
    current = _pick_sheet(grid, sheet)
    if current is None:
        return {"engine_version": ENGINE_VERSION, "parts": [], "skipped": [],
                "source": {"file": file_name, "sheet": "", "file_hash": digest,
                           "code_prefix": PART_CODE_PREFIX_FALLBACK},
                "stats": {"part_total": 0, "image_total": 0, "skipped_total": 0},
                "unavailable": [{"code": "authority_sheet_missing",
                                 "message": "工作簿里找不到「%s」这张业务表"
                                            % (sheet or DEFAULT_SHEET_NAME)}]}

    header_row = _header_row(current)
    columns = _column_map(current, header_row)
    prefix = code_prefix(_sheet_title(current))
    thumbnails = _thumbnail_index(current)

    parts: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    expected = 1
    seen: set = set()
    if not header_row:
        skipped.append({"row": 0, "reason": "no_header",
                        "message": "表头行（含「序号」「名称」）没找到"})
    for row in range(header_row + 1, _rows(current) + 1):
        raw_sequence = _cell(current, row, columns["sequence_no"]) if "sequence_no" in columns else None
        row_values = [_text(_cell(current, row, col)) for col in range(1, _columns(current) + 1)]
        if not any(row_values):
            skipped.append({"row": row, "reason": "blank_row", "message": "整行空白"})
            continue
        sequence = _num(raw_sequence)
        if sequence is None:
            skipped.append({"row": row, "reason": "no_sequence",
                            "message": "没有序号，按说明/签名行跳过",
                            "text": next((item for item in row_values if item), "")})
            continue
        sequence = int(sequence)
        item = _part_row(current, row, columns, thumbnails=thumbnails)
        if item is None:
            skipped.append({"row": row, "reason": "not_a_part_row",
                            "message": "有序号但没有名称，或既无尺寸也无材料/排版/工艺/部件图"
                                       "（说明行），按非部件行跳过",
                            "sequence_no": sequence,
                            "text": _text(_cell(current, row, columns["name"]))
                                    if "name" in columns else ""})
            continue
        if sequence in seen:
            skipped.append({"row": row, "reason": "duplicate_sequence",
                            "message": "序号重复", "sequence_no": sequence})
            continue
        if sequence != expected:
            # 序号不连续 = 从这里开始已经不是业务行（客户备注行常常也带一个序号）。
            skipped.append({"row": row, "reason": "sequence_not_contiguous",
                            "message": "序号 %d 不在连续序号上（期望 %d），按非部件行跳过"
                                       % (sequence, expected),
                            "sequence_no": sequence, "text": item.get("name") or ""})
            continue
        item["business_part_code"] = BUSINESS_PART_CODE_FORMAT % (prefix, sequence)
        seen.add(sequence)
        parts.append(item)
        expected = sequence + 1

    # 部件图归属（Spec §1：28 行部件 ↔ 28 张部件图）：数量相等 → 顺序一一对应（图片是浮动的，
    # 锚点行与部件行并不逐行对齐，顺序才是稳定事实）；数量不等 → 保留按锚点行就近的结果，
    # 并把归属来源记下来供人工核对。
    ordered: List[str] = []
    for anchor_row in sorted(thumbnails):
        ordered.extend(thumbnails[anchor_row])
    if ordered and len(ordered) == len(parts):
        for item, ref in zip(parts, ordered):
            item["thumbnail_ref"] = ref
            item["thumbnail_refs"] = [ref]
            item["thumbnail_source"] = "order"
    else:
        for item in parts:
            item["thumbnail_source"] = "anchor_row" if item.get("thumbnail_ref") else ""

    return {
        "engine_version": ENGINE_VERSION,
        "parts": parts,
        "skipped": skipped,
        "source": {"file": file_name, "sheet": _text(current.get("name")),
                   "file_hash": digest, "code_prefix": prefix, "header_row": header_row,
                   "data_row_first": parts[0]["source"]["row"] if parts else 0,
                   "data_row_last": parts[-1]["source"]["row"] if parts else 0},
        "stats": {"part_total": len(parts), "image_total": len(current.get("images") or []),
                  "skipped_total": len(skipped),
                  "thumbnail_bound_total": sum(1 for item in parts if item.get("thumbnail_ref"))},
        "unavailable": [] if parts else [{"code": "authority_rows_empty",
                                          "message": "这张业务表里没有连续序号的部件行"}],
    }


def part_by_code(authority: Any, code: Any) -> Dict[str, Any]:
    """按业务编码取一行权威资料（纯读）。"""
    wanted = _text(code)
    doc = authority if isinstance(authority, dict) else {}
    for row in (doc.get("parts") or []):
        if isinstance(row, dict) and _text(row.get("business_part_code")) == wanted:
            return row
    return {}


if __name__ == "__main__":  # pragma: no cover - 手工核对入口
    import json
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("用法：python -m backend.services.packaging_part_authority <xlsx>")
    print(json.dumps(import_workbook(sys.argv[1]), ensure_ascii=False, indent=2)[:4000])
