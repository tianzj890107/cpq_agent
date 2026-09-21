"""纯函数几何：长度 / 面积 / 包围盒 / 闭合判定 / 连通组件 / 容差（DWG 第 3 批 Spec §3.2）。

本模块**不依赖 ezdxf**：输入都是本地点列，输出都是原生 float/list/dict，
方便单独验证算法，也保证解析器的几何口径只有一处实现。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

#: 容差下限（Spec §3.2：默认 1e-9 × 最大跨度，且不得小于 1e-9）
MIN_TOLERANCE = 1e-9


def finite(value: Any) -> Optional[float]:
    """把任意输入收敛成有限 float；NaN / ±Inf / 非数值一律返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def round6(value: Any) -> Optional[float]:
    number = finite(value)
    return None if number is None else round(number, 6)


def point_of(value: Any) -> Optional[Point]:
    """从 (x, y) / [x, y] / 带 .x/.y 的对象取二维点；取不到返回 None。"""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        x, y = finite(value[0]), finite(value[1])
    else:
        x, y = finite(getattr(value, "x", None)), finite(getattr(value, "y", None))
    if x is None or y is None:
        return None
    return (x, y)


def points_of(values: Iterable[Any]) -> List[Point]:
    out: List[Point] = []
    for item in values or []:
        point = point_of(item)
        if point is not None:
            out.append(point)
    return out


def bbox_of(points: Sequence[Point]) -> Optional[List[float]]:
    if not points:
        return None
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def union_bbox(boxes: Iterable[Any]) -> Optional[List[float]]:
    clean = [box for box in boxes if isinstance(box, (list, tuple)) and len(box) == 4]
    if not clean:
        return None
    return [min(float(b[0]) for b in clean), min(float(b[1]) for b in clean),
            max(float(b[2]) for b in clean), max(float(b[3]) for b in clean)]


def distance(first: Point, second: Point) -> float:
    return math.hypot(float(second[0]) - float(first[0]), float(second[1]) - float(first[1]))


def polyline_length(points: Sequence[Point], closed: bool = False) -> float:
    if len(points) < 2:
        return 0.0
    total = sum(distance(points[index], points[index + 1]) for index in range(len(points) - 1))
    if closed and len(points) > 2:
        total += distance(points[-1], points[0])
    return float(total)


def polygon_area(points: Sequence[Point]) -> float:
    """鞋带公式取绝对值：镜像（负缩放）不改变面积，结果恒为正值（Spec §5）。"""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for index in range(len(points)):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def is_closed(points: Sequence[Point], tolerance: float) -> bool:
    if len(points) < 3:
        return False
    return distance(points[0], points[-1]) <= max(float(tolerance), MIN_TOLERANCE)


def circle_length(radius: float) -> float:
    return 2.0 * math.pi * abs(float(radius))


def arc_length(radius: float, start_degrees: float, end_degrees: float) -> float:
    """ARC 长度 = r · Δθ；跨 0° 时按逆时针补满 2π（不许用包围盒近似）。"""
    span = (float(end_degrees) - float(start_degrees)) % 360.0
    if span <= 0.0:
        span += 360.0
    return abs(float(radius)) * math.radians(span)


def ellipse_perimeter(semi_major: float, semi_minor: float) -> float:
    """Ramanujan 第二近似（相对误差 < 1e-9 量级，足够验收口径）。"""
    a, b = abs(float(semi_major)), abs(float(semi_minor))
    if a <= 0.0 and b <= 0.0:
        return 0.0
    h = ((a - b) ** 2) / ((a + b) ** 2) if (a + b) else 0.0
    return math.pi * (a + b) * (1.0 + (3.0 * h) / (10.0 + math.sqrt(4.0 - 3.0 * h)))


def tolerance_for(extents: Optional[Sequence[float]]) -> float:
    if not extents:
        return MIN_TOLERANCE
    span = max(abs(float(extents[2]) - float(extents[0])),
               abs(float(extents[3]) - float(extents[1])))
    return max(MIN_TOLERANCE, MIN_TOLERANCE * span)


def boxes_touch(first: Any, second: Any, tolerance: float) -> bool:
    if not (isinstance(first, (list, tuple)) and isinstance(second, (list, tuple))):
        return False
    gap = max(float(tolerance), MIN_TOLERANCE)
    return not (first[2] + gap < second[0] or second[2] + gap < first[0]
                or first[3] + gap < second[1] or second[3] + gap < first[1])


def components_of(rows: Sequence[Dict[str, Any]], tolerance: float) -> List[Dict[str, Any]]:
    """按包围盒相邻关系做连通分组（只做分组与计数，不做排版/拼版优化）。"""
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(rows)):
        for right in range(left + 1, len(rows)):
            if boxes_touch(rows[left].get("bbox"), rows[right].get("bbox"), tolerance):
                a, b = find(left), find(right)
                if a != b:
                    parent[b] = a

    groups: Dict[int, List[int]] = {}
    for index in range(len(rows)):
        groups.setdefault(find(index), []).append(index)

    out: List[Dict[str, Any]] = []
    for order, indexes in enumerate(sorted(groups.values(), key=lambda items: items[0]), start=1):
        members = [rows[index] for index in indexes]
        out.append({
            "component_id": "cmp:%d" % order,
            "entity_ids": [str(row.get("entity_id")) for row in members],
            "bbox": union_bbox([row.get("bbox") for row in members]),
            "closed_cycles": sum(1 for row in members if str(row.get("kind")) == "polyline"
                                 and row.get("closed")),
        })
    return out
