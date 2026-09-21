"""单位判定：$INSUNITS / 标注文字后缀 / 标题栏文字（DWG 第 3 批 Spec §4）。

铁律：**不许猜**。$INSUNITS=0 / 缺失 / 未知值时只能给候选与置信度，
`unit_status` 必须是 `needs_confirmation`，`scale_to_mm` 必须是 None。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: $INSUNITS → (单位名, 1 单位等于多少毫米)。0=无单位、未列入=未知（都走候选分支）。
INSUNITS_TABLE: Dict[int, Tuple[str, float]] = {
    1: ("inch", 25.4),
    2: ("ft", 304.8),
    3: ("mi", 1609344.0),
    4: ("mm", 1.0),
    5: ("cm", 10.0),
    6: ("m", 1000.0),
    7: ("km", 1000000.0),
    8: ("microinch", 0.0000254),
    9: ("mil", 0.0254),
    10: ("yd", 914.4),
    11: ("angstrom", 1e-7),
    12: ("nm", 1e-6),
    13: ("um", 0.001),
    14: ("dm", 100.0),
    15: ("dam", 10000.0),
    16: ("hm", 100000.0),
    17: ("gm", 1e12),
}

#: 候选上限（Spec：只给人工确认用，不需要穷举）
MAX_CANDIDATES = 5

_TEXT_UNITS = (
    ("mm", re.compile(r"\d\s*(?:mm|MM|毫米)")),
    ("cm", re.compile(r"\d\s*(?:cm|CM|厘米)")),
    ("m", re.compile(r"\d\s*(?:m|米)(?!\w)")),
    ("inch", re.compile(r"\d\s*(?:in|inch|\"|英寸)")),
)

_TITLE_BLOCK = re.compile(r"(?:单位|units?)\s*[:：]?\s*(mm|MM|毫米|cm|CM|厘米|m|米|inch|英寸)")


def _labeled(unit: str) -> str:
    return "mm" if unit in ("毫米",) else unit


def candidates_from_texts(texts: Iterable[str]) -> List[Dict[str, Any]]:
    """从文字里找单位候选：先看「单位：mm」这类标题栏字样，再看尺寸后缀。"""
    found: List[Dict[str, Any]] = []
    seen: set = set()

    def add(unit: str, confidence: float, reason: str) -> None:
        key = str(unit)
        if key in seen or len(found) >= MAX_CANDIDATES:
            return
        seen.add(key)
        found.append({"unit": key, "confidence": float(confidence), "reason": reason})

    for raw in texts or []:
        text = str(raw or "")
        if not text:
            continue
        for match in _TITLE_BLOCK.finditer(text):
            add(_labeled(match.group(1)), 0.45, "标题栏文字声明单位")
        for unit, pattern in _TEXT_UNITS:
            if pattern.search(text):
                add(unit, 0.4, "标注文字含 %s" % unit)
    return found


def resolve(insunits: Any, texts: Iterable[str]) -> Dict[str, Any]:
    """返回 IR 的 `units` 块（结构见 Spec §3）。"""
    raw = insunits
    try:
        value = int(raw) if raw is not None and str(raw).strip() != "" else None
    except (TypeError, ValueError):
        value = None

    entry = INSUNITS_TABLE.get(value) if value is not None else None
    if entry is not None:
        name, scale = entry
        return {
            "drawing_units": name,
            "unit_status": "confirmed",
            "unit_confidence": 1.0,
            "scale_to_mm": float(scale),
            "candidates": [],
            "source": "insunits:%d" % value,
        }

    candidates = candidates_from_texts(texts)
    return {
        "drawing_units": "unitless" if value == 0 else "unknown",
        "unit_status": "needs_confirmation",
        "unit_confidence": min(0.5, max([item["confidence"] for item in candidates] or [0.25])),
        "scale_to_mm": None,
        "candidates": candidates,
        "source": "insunits:%s" % ("0" if value == 0 else "missing"),
    }


def mm_of(value: Optional[float], scale_to_mm: Optional[float]) -> Optional[float]:
    """单位未确认（scale 为 None）时恒为 None —— 绝不用 1.0 蒙混。"""
    if value is None or scale_to_mm is None:
        return None
    return float(value) * float(scale_to_mm)


def mm2_of(value: Optional[float], scale_to_mm: Optional[float]) -> Optional[float]:
    if value is None or scale_to_mm is None:
        return None
    return float(value) * (float(scale_to_mm) ** 2)
