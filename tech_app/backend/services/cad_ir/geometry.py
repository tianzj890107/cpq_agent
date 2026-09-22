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


def point_on_circle(center: Point, radius: float, degrees_value: float) -> Point:
    """圆心 + 半径 + 角度 → 圆周上的点（ARC 的两个端点由它算出来）。"""
    angle = math.radians(float(degrees_value))
    return (float(center[0]) + float(radius) * math.cos(angle),
            float(center[1]) + float(radius) * math.sin(angle))


def chaining_keys(row: Dict[str, Any]) -> Dict[str, Any]:
    """实体参与连通分组的键（Spec `packaging-parts-component-chaining.md` §2.1）。

    返回 `{"points": [...], "circle": <同圆键或 None>}` —— **只有端点**参与分组，
    `bbox` 一律不许当分组依据（一条斜线的 bbox 覆盖整块，会把落在里面的无关实体吞并）：

    - `LINE` → `attributes.start` / `attributes.end`；
    - `ARC` → 圆心 + 半径 + 起止角算出的两个端点；
    - `LWPOLYLINE` / `POLYLINE` → `attributes.points` 首尾；
    - `SPLINE` → `attributes.fit_points` 首尾；
    - `CIRCLE` → 没有端点，只给"同圆键"（圆心 + 半径，只与自己同圆时相接）；
    - 一个键都取不到 → 空（**不许**用 bbox 兜底，宁可是单件不可信）。
    """
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    points: List[Point] = []
    start, end = point_of(attrs.get("start")), point_of(attrs.get("end"))
    if start is not None:
        points.append(start)
    if end is not None:
        points.append(end)
    curve = str(attrs.get("curve") or "").lower()
    type_name = str(row.get("type") or "").upper()
    if not points and (curve == "arc" or type_name == "ARC"):
        center, radius = point_of(attrs.get("center")), finite(attrs.get("radius"))
        if center is not None and radius:
            points = [point_on_circle(center, radius, finite(attrs.get("start_angle")) or 0.0),
                      point_on_circle(center, radius, finite(attrs.get("end_angle")) or 0.0)]
    if not points:
        vertices = points_of(attrs.get("points") or [])
        if vertices:
            points = [vertices[0], vertices[-1]]
    if not points:
        fit_points = points_of(attrs.get("fit_points") or [])
        if fit_points:
            points = [fit_points[0], fit_points[-1]]
    circle: Optional[Tuple[str, float, float, float]] = None
    if curve == "circle" or type_name == "CIRCLE":
        center, radius = point_of(attrs.get("center")), finite(attrs.get("radius"))
        if center is not None and radius is not None:
            circle = ("circle", round6(center[0]) or 0.0, round6(center[1]) or 0.0,
                      round6(radius) or 0.0)
    return {"points": points, "circle": circle}


def is_ungroupable(row: Dict[str, Any]) -> bool:
    """没有任何端点（也没有同圆键）的实体：**不参与分组**，各自单独成件（Spec §2.1）。"""
    keys = chaining_keys(row) if isinstance(row, dict) else {"points": [], "circle": None}
    return not keys["points"] and keys["circle"] is None


def components_of(rows: Sequence[Dict[str, Any]], tolerance: float) -> List[Dict[str, Any]]:
    """按**端点相接**做连通分组（Spec `packaging-parts-component-chaining.md` §2.1）。

    只做分组与计数，不做排版/拼版优化；`bbox` 只用于输出，不再参与分组。
    """
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    gap = max(float(tolerance), MIN_TOLERANCE)
    keys = [chaining_keys(row) if isinstance(row, dict) else {"points": [], "circle": None}
            for row in rows]
    # 端点按"容差大小的格子"分桶：距离 ≤ 容差的两个点必然落在同格或相邻格（3 × 3），
    # 于是真图 6k+ 实体不需要 O(n²) 两两比包围盒。
    buckets: Dict[Tuple[int, int], List[Tuple[int, Point]]] = {}
    for index, key in enumerate(keys):
        for point in key["points"]:
            cell = (int(math.floor(point[0] / gap)), int(math.floor(point[1] / gap)))
            buckets.setdefault(cell, []).append((index, point))
    for (cell_x, cell_y), members in buckets.items():
        neighbours: List[Tuple[int, Point]] = []
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                neighbours.extend(buckets.get((cell_x + offset_x, cell_y + offset_y)) or [])
        for position, (left_index, left_point) in enumerate(members):
            for right_index, right_point in neighbours:
                if right_index <= left_index:
                    continue
                if distance(left_point, right_point) <= gap:
                    union(left_index, right_index)
    # CIRCLE：只和"同圆"（圆心 + 半径一致）的圆相接，绝不与直线/折线相并。
    same_circle: Dict[Tuple[str, float, float, float], List[int]] = {}
    for index, key in enumerate(keys):
        if key["circle"] is not None:
            same_circle.setdefault(key["circle"], []).append(index)
    for members in same_circle.values():
        for other in members[1:]:
            union(members[0], other)

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
