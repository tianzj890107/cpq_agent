# -*- coding: utf-8 -*-
"""DXF 结构检查（DWG 前两批修复「转换质量门槛」用，Spec `dwg-conversion-quality-repair.md` §4）。

只回答一个问题：**这份 DXF 是不是一份结构完整、非空的图纸**。不计算几何、不猜单位、
不判图层角色、不识别盒型（那是第 3/4 批）。计数口径全部是**模型空间顶层实体**（不展开
块引用）；`layer_count` 是图层表条目数（含未被引用的图层）。

优先真解析一遍（`ezdxf`）；解析器不可用时退化为字节级结构检查，并把 `verified` 标成
`False` —— **不许**假装验过。

位置说明：转换层（`tech_app/backend/services/cad_converter/`）自己**不许**解析 DXF
（第 2 批 Spec §10 的 g2 护栏），所以这一步单独放在本模块，由编排层在产物落盘前调用。
"""
from __future__ import annotations

import importlib
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

#: 首选解析器：能导入就用它真解析（红测按 `parser == "ezdxf"` 断言）。
PARSER_MODULE = "ezdxf"
#: 解析器不可用时的降级标记（`verified` 必须为假）。
FALLBACK_PARSER = "bytes"

_ACADVER = re.compile(rb"\$ACADVER")
_ENTITY_TYPE = re.compile(rb"^[A-Z][A-Z0-9_]*$")


def parser_available() -> bool:
    """本环境能不能真解析 DXF（决定 `quality.verified` 的口径）。"""
    return _load_parser() is not None


def parser_version() -> str:
    module = _load_parser()
    return str(getattr(module, "__version__", "") or "") if module is not None else ""


def _load_parser():
    try:
        return importlib.import_module(PARSER_MODULE)
    except ImportError:
        return None


def inspect_dxf(path) -> Dict[str, Any]:
    """检查一份 DXF 产物；返回稳定的质量字典（缺项一律给安全默认值，不抛异常）。

    返回：`verified` / `parser` / `parser_version` / `dxf_version` / `insunits` /
    `entity_count` / `layer_count` / `text_count` / `dimension_count` /
    `block_ref_count` / `structure_ok` / `problem`。
    `problem` 是稳定短码：`""` / `structure_incomplete` / `no_entities` / `no_layers`
    / `unreadable`。
    """
    report: Dict[str, Any] = {
        "verified": False, "parser": "", "parser_version": "",
        "dxf_version": "", "insunits": None,
        "entity_count": 0, "layer_count": 0, "text_count": 0,
        "dimension_count": 0, "block_ref_count": 0,
        "structure_ok": False, "problem": "",
    }
    try:
        data = Path(str(path)).read_bytes()
    except OSError:
        report["problem"] = "unreadable"
        return report

    module = _load_parser()
    if module is not None:
        try:
            return _finish(_inspect_with_parser(module, path), report)
        except Exception:
            # 有解析器却读不开：这份产物就是结构不完整，**不**降级成「字节看着像」。
            report["parser"] = PARSER_MODULE
            report["parser_version"] = str(getattr(module, "__version__", "") or "")
            report["problem"] = "unreadable"
            return report

    return _finish(_inspect_bytes(data), report)


def _inspect_with_parser(module, path) -> Dict[str, Any]:
    document = module.readfile(str(path))
    types = Counter(entity.dxftype() for entity in document.modelspace())
    header = document.header
    return {
        "verified": True,
        "parser": PARSER_MODULE,
        "parser_version": str(getattr(module, "__version__", "") or ""),
        "dxf_version": str(header.get("$ACADVER") or ""),
        "insunits": _as_int(header.get("$INSUNITS")),
        "entity_count": sum(types.values()),
        "layer_count": len(list(document.layers)),
        "text_count": types.get("TEXT", 0) + types.get("MTEXT", 0),
        "dimension_count": types.get("DIMENSION", 0),
        "block_ref_count": types.get("INSERT", 0),
        "structure_ok": bool(str(header.get("$ACADVER") or "")),
    }


def _inspect_bytes(data: bytes) -> Dict[str, Any]:
    """没有解析器时的降级检查：只看结构标记与实体/图层条目数，`verified` 保持假。"""
    upper = data.upper()
    sections = upper.count(b"SECTION")
    entities_at = upper.find(b"ENTITIES")
    structure_ok = bool(sections and upper.count(b"ENDSEC") >= sections
                        and entities_at >= 0 and _acadver(data))
    entity_count = _count_entities(data)
    layer_count = _count_block(data, b"LAYER", b"TABLES")
    return {
        "verified": False,
        "parser": FALLBACK_PARSER,
        "parser_version": "",
        "dxf_version": _acadver(data),
        "insunits": None,
        "entity_count": entity_count,
        "layer_count": layer_count,
        "text_count": _count_block(data, b"MTEXT", b"ENTITIES") + _count_block(data, b"TEXT", b"ENTITIES"),
        "dimension_count": _count_block(data, b"DIMENSION", b"ENTITIES"),
        "block_ref_count": _count_block(data, b"INSERT", b"ENTITIES"),
        "structure_ok": structure_ok,
    }


def _finish(payload: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    report.update({key: value for key, value in payload.items() if key in report})
    if not report["structure_ok"]:
        report["problem"] = "structure_incomplete"
    elif int(report["entity_count"]) <= 0:
        report["problem"] = "no_entities"
    elif int(report["layer_count"]) <= 0:
        report["problem"] = "no_layers"
    return report


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _acadver(data: bytes) -> str:
    """从字节里读 `$ACADVER` 的取值（降级路径专用）。"""
    match = _ACADVER.search(data)
    if not match:
        return ""
    lines = data[match.end():].splitlines()[:6]
    values = [line.strip() for line in lines if line.strip()]
    return values[1].decode("ascii", "replace") if len(values) >= 2 else ""


def _section(data: bytes, name: bytes) -> bytes:
    upper = data.upper()
    start = upper.find(b"2\n" + name)
    if start < 0:
        return b""
    end = upper.find(b"ENDSEC", start)
    return data[start:end if end >= 0 else len(data)]


def _count_entities(data: bytes) -> int:
    """数 ENTITIES 段里的顶层实体（组码 `0` + 实体类型），不展开块引用。"""
    body = _section(data, b"ENTITIES")
    if not body:
        return 0
    lines = [line.strip() for line in body.splitlines()]
    count = 0
    for index, line in enumerate(lines[:-1]):
        if line != b"0":
            continue
        candidate = lines[index + 1]
        if _ENTITY_TYPE.match(candidate) and candidate not in (b"ENDSEC", b"EOF", b"SEQEND"):
            count += 1
    return count


def _count_block(data: bytes, entry: bytes, section_name: bytes) -> int:
    """数某段里组码 `0` + 指定条目的次数（降级路径的图层/文字/标注近似计数）。"""
    body = _section(data, section_name)
    if not body:
        return 0
    lines = [line.strip() for line in body.splitlines()]
    count = 0
    for index, line in enumerate(lines[:-1]):
        if line == b"0" and lines[index + 1] == entry.upper():
            count += 1
    return count
