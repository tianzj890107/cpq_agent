"""图纸零件 → 平板挤出体（ASCII STL）（Spec `packaging-parts-3d-extrusion.md` +
`packaging-parts-solid-coverage.md`）。

只做**闭合轮廓 × 已知料厚**的直线挤出：底面三角化 + 顶面 + 侧壁。
不做折弯成型、不做盒体装配、不做刀模重建 —— 目标是"右栏那块画布不再空着"，
而且挤不出来的件必须给出**明确原因**（`UNSUPPORTED_REASONS`），不许留一块空白画布。

三角化是**耳切（ear clipping）**（Spec `packaging-parts-solid-coverage.md` §2.1）：
真刀模零件的展开轮廓绝大多数是凹的（实测 51 件闭合件里 34 件凹），扇形三角化 + 凸性门槛
会把覆盖率天花板压到 17/64。凸多边形仍按与既有实现逐字相同的扇形切（从 0 号顶点连续切耳
就是耳切的一支），凹多边形走通用耳切；真自交（bowtie）与零面积轮廓**一律拒绝，不硬挤**。

三角化与法向全部自己写（`numpy` / `trimesh` / `shapely` 一律不引入）。
本模块是纯函数风格：`extrude()` / `extrude_all()` 不落盘、不联网；落盘只有 `save_solids()` 一处。
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..storage.meta_backend import get_backend

ENGINE_VERSION = "packaging-part-solids/1"

#: 挤出结果在项目存储里的 doc key。
DOC_KEY = "packaging_part_solids"

STL_FORMAT = "ascii"

#: 三角化方式（Spec `packaging-parts-solid-coverage.md` §2）：耳切。
TRIANGULATION = "ear_clipping"

#: 挤不出来的原因闭集（前端据此翻人话，顺序即判定顺序）。
#: 2026-09-22（Spec `packaging-parts-solid-coverage.md` §2.1）：`concave_polygon` **不再**
#: 是拒绝理由 —— 凹件必须能耳切挤出；新增 `self_intersecting` / `degenerate_polygon` 两个
#: **真缺陷**理由（真自交与零面积轮廓仍不许硬挤）。
UNSUPPORTED_REASONS = ("outline_open", "outline_unavailable", "thickness_unknown",
                       "too_few_points", "too_many_points",
                       "self_intersecting", "degenerate_polygon")

#: 轮廓聚合用的角度容差（叉积绝对值，mm²）；共线点不算拐角。
ANGLE_TOLERANCE_MM2 = 1e-6

#: 点数上限：超过就不挤（先保证确定性，不做简化）。
MAX_POINTS = 2000

#: 重合成一个点的容差（mm）。
CONVERT_TOLERANCE_MM = 1e-6

#: 每个项目最多保留的固体版本数。
MAX_VERSIONS = 20


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 4)


def _points_of(row: Dict[str, Any]) -> List[List[float]]:
    """轮廓点：去掉连续重复点与"首尾同点"的收尾重复（只做归一，不改坐标）。"""
    outline = row.get("outline") if isinstance(row.get("outline"), dict) else {}
    raw = outline.get("points")
    if not isinstance(raw, list):
        return []
    points: List[List[float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        x, y = _num(item[0]), _num(item[1])
        if x is None or y is None:
            continue
        if points and abs(points[-1][0] - x) <= CONVERT_TOLERANCE_MM \
                and abs(points[-1][1] - y) <= CONVERT_TOLERANCE_MM:
            continue
        points.append([x, y])
    if len(points) > 1 and abs(points[0][0] - points[-1][0]) <= CONVERT_TOLERANCE_MM \
            and abs(points[0][1] - points[-1][1]) <= CONVERT_TOLERANCE_MM:
        points.pop()
    return points


def polygon_area_mm2(points: List[List[float]]) -> float:
    """鞋带公式（绝对面积）。"""
    total = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index][0], points[index][1]
        x2, y2 = points[(index + 1) % count][0], points[(index + 1) % count][1]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def is_convex(points: List[List[float]]) -> bool:
    """叉积符号是否一致（共线点不算反向）。"""
    count = len(points)
    if count < 3:
        return False
    sign = 0
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        x3, y3 = points[(index + 2) % count]
        cross = (x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2)
        if abs(cross) <= CONVERT_TOLERANCE_MM:
            continue
        current = 1 if cross > 0 else -1
        if sign == 0:
            sign = current
        elif sign != current:
            return False
    return sign != 0


def _bbox_mm(points: List[List[float]], thickness: float) -> Dict[str, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {"length": _round(max(xs) - min(xs)), "width": _round(max(ys) - min(ys)),
            "thickness": _round(thickness)}


def _unsupported(part_code: str, reason: str) -> Dict[str, Any]:
    return {"part_code": part_code, "status": "unsupported", "reason": reason,
            "stl": "", "triangles": 0, "bbox_mm": {}, "volume_mm3": None, "points": 0}


def _signed_area_mm2(points: List[List[float]]) -> float:
    """有符号面积（鞋带，逆时针为正）—— 只用来定绕向，不用来报面积。"""
    total = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index][0], points[index][1]
        x2, y2 = points[(index + 1) % count][0], points[(index + 1) % count][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _orient(a: List[float], b: List[float], c: List[float]) -> float:
    """叉积（> 0 表示 a→b→c 左转）。"""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _properly_cross(a: List[float], b: List[float], c: List[float],
                    d: List[float]) -> bool:
    """两条线段是否**真相交**（各自跨过对方；共端点/共线重叠不算）。"""
    d1, d2 = _orient(a, b, c), _orient(a, b, d)
    d3, d4 = _orient(c, d, a), _orient(c, d, b)
    if not (((d1 > ANGLE_TOLERANCE_MM2) and (d2 < -ANGLE_TOLERANCE_MM2))
            or ((d1 < -ANGLE_TOLERANCE_MM2) and (d2 > ANGLE_TOLERANCE_MM2))):
        return False
    return (((d3 > ANGLE_TOLERANCE_MM2) and (d4 < -ANGLE_TOLERANCE_MM2))
            or ((d3 < -ANGLE_TOLERANCE_MM2) and (d4 > ANGLE_TOLERANCE_MM2)))


def _self_intersects(points: List[List[float]]) -> bool:
    """轮廓是否自交（bowtie 这类边相交）。相邻边共享端点，跳过。"""
    count = len(points)
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or (i + 1) % count == j:
                continue
            if _properly_cross(a, b, points[j], points[(j + 1) % count]):
                return True
    return False


def _inside_triangle(point: List[float], a: List[float], b: List[float],
                     c: List[float]) -> bool:
    """点是否落在逆时针三角形内（含边界）。"""
    return (_orient(a, b, point) >= -ANGLE_TOLERANCE_MM2
            and _orient(b, c, point) >= -ANGLE_TOLERANCE_MM2
            and _orient(c, a, point) >= -ANGLE_TOLERANCE_MM2)


def _is_ear(points: List[List[float]], order: List[int], position: int,
            *, strict: bool) -> bool:
    """`order[position]` 这个顶点是不是一个耳（凸角且三角形内没有别的顶点）。

    凸角判据：左转（叉积 > 0）；`strict=False` 的兜底趟里允许共线顶点（叉积 ≈ 0）当耳，
    否则一条边上带共线点（真图很常见）的轮廓会中途卡死切不动。
    """
    count = len(order)
    tip = order[position]
    prev = order[position - 1]
    nxt = order[(position + 1) % count]
    cross = _orient(points[prev], points[tip], points[nxt])
    if strict:
        if cross <= ANGLE_TOLERANCE_MM2:
            return False
    elif cross < -ANGLE_TOLERANCE_MM2:
        return False
    for index in order:
        if index in (prev, tip, nxt):
            continue
        if _inside_triangle(points[index], points[prev], points[tip], points[nxt]):
            return False
    return True


def _ear_clip(points: List[List[float]]) -> Optional[List[Tuple[int, int, int]]]:
    """耳切三角化（返回逆时针索引三元组）。切不动（退化）返回 None。"""
    count = len(points)
    order = list(range(count))
    if _signed_area_mm2(points) < 0:      # 统一成逆时针，法向才朝上
        order.reverse()
    caps: List[Tuple[int, int, int]] = []
    while len(order) > 3:
        cut = False
        for strict in (True, False):      # 先切严格凸耳，卡住了再允许共线顶点兜底
            for position in range(len(order)):
                if not _is_ear(points, order, position, strict=strict):
                    continue
                caps.append((order[position - 1], order[position],
                             order[(position + 1) % len(order)]))
                order.pop(position)
                cut = True
                break
            if cut:
                break
        if not cut:
            return None
    caps.append((order[0], order[1], order[2]))
    return caps


def _cap_indices(points: List[List[float]]) -> Optional[List[Tuple[int, int, int]]]:
    """底面/顶面的三角化索引（Spec §2.1）。

    凸多边形走与既有实现**逐字相同**的扇形（从 0 号顶点连续切耳就是耳切的一支，
    面数与顶点顺序都不变）；凹多边形走通用耳切。
    """
    count = len(points)
    if count == 3:
        return [(0, 1, 2)]
    if is_convex(points):
        return [(0, index, index + 1) for index in range(1, count - 1)]
    return _ear_clip(points)


def _triangles(points: List[List[float]], thickness: float,
               caps: List[Tuple[int, int, int]]) -> List[List[List[float]]]:
    """按三角化索引出底面 + 顶面 + 侧壁（底面法向朝 -Z，侧面按轮廓走向）。"""
    top = [[point[0], point[1], thickness] for point in points]
    bottom = [[point[0], point[1], 0.0] for point in points]
    faces: List[List[List[float]]] = []
    for first, second, third in caps:          # 底面：反向保证法向朝下
        faces.append([bottom[first], bottom[third], bottom[second]])
    for first, second, third in caps:          # 顶面
        faces.append([top[first], top[second], top[third]])
    count = len(points)
    for index in range(count):                 # 侧壁：每边两个三角形
        nxt = (index + 1) % count
        faces.append([bottom[index], bottom[nxt], top[nxt]])
        faces.append([bottom[index], top[nxt], top[index]])
    return faces


def _normal(face: List[List[float]]) -> Tuple[float, float, float]:
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = face
    ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
    vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= CONVERT_TOLERANCE_MM:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _stl(part_code: str, faces: List[List[List[float]]]) -> str:
    name = "packaging_part_%s" % (part_code or "part")
    lines = ["solid %s" % name]
    for face in faces:
        nx, ny, nz = _normal(face)
        lines.append("  facet normal %.6f %.6f %.6f" % (nx, ny, nz))
        lines.append("    outer loop")
        for vertex in face:
            lines.append("      vertex %.6f %.6f %.6f" % (vertex[0], vertex[1], vertex[2]))
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid %s" % name)
    return "\n".join(lines) + "\n"


def extrude(row: Any, *, options: Any = None) -> Dict[str, Any]:
    """闭合轮廓 × 料厚 → ASCII STL（板件）。门槛顺序固定，先判门槛再算。"""
    payload = row if isinstance(row, dict) else {}
    part_code = str(payload.get("part_code") or "")
    status = str(payload.get("outline_status") or "")
    if status != "closed":
        return _unsupported(part_code,
                            "outline_unavailable" if status == "unavailable"
                            else "outline_open")
    thickness = _num(payload.get("thickness_mm"))
    if not thickness or thickness <= 0:
        return _unsupported(part_code, "thickness_unknown")
    points = _points_of(payload)
    if len(points) < 3:
        return _unsupported(part_code, "too_few_points")
    if len(points) > MAX_POINTS:
        return _unsupported(part_code, "too_many_points")
    # 真自交要**先判**：bowtie 的有符号面积正好是 0，先判退化会把它误报成 degenerate_polygon。
    if _self_intersects(points):
        return _unsupported(part_code, "self_intersecting")
    if polygon_area_mm2(points) <= ANGLE_TOLERANCE_MM2:
        return _unsupported(part_code, "degenerate_polygon")
    caps = _cap_indices(points)
    if not caps:
        return _unsupported(part_code, "degenerate_polygon")
    faces = _triangles(points, thickness, caps)
    return {"part_code": part_code, "status": "ok", "reason": "",
            "triangulation": TRIANGULATION,
            "stl": _stl(part_code, faces), "triangles": len(faces),
            "bbox_mm": _bbox_mm(points, thickness),
            "volume_mm3": _round(polygon_area_mm2(points) * thickness),
            "points": len(points)}


def extrude_all(rows: Any, *, options: Any = None) -> Dict[str, Any]:
    """整份零件文档一次算完（Spec `packaging-parts-solid-coverage.md` §2.2）。

    **纯函数**：入参行一个字节都不改，逐件结论是副本并回写 `solid_status` /
    `solid_reason`；`stats` 让"3D 覆盖率"第一次成为真值（今天恒 0.0 是因为没人算）。
    """
    source = [item for item in (rows or []) if isinstance(item, dict)]
    parts: List[Dict[str, Any]] = []
    mix: Dict[str, int] = {}
    ok_total = 0
    for row in source:
        result = dict(extrude(row, options=options))
        status = "ok" if str(result.get("status") or "") == "ok" else "unsupported"
        result["solid_status"] = status
        result["solid_reason"] = "" if status == "ok" else str(result.get("reason") or "")
        if status == "ok":
            ok_total += 1
        else:
            reason = result["solid_reason"] or "unknown"
            mix[reason] = mix.get(reason, 0) + 1
        parts.append(result)
    total = len(parts)
    stats = {"part_total": total, "ok_total": ok_total,
             "unsupported_total": total - ok_total,
             "solid_ok_ratio": _round(ok_total / total) if total else 0.0,
             "unsupported_reason_mix": mix}
    return {"parts": parts, "stats": stats}


# --------------------------------------------------------------------------- #
# 落库（版本化，照 packaging_semantics/persistence.py 的范式）
# --------------------------------------------------------------------------- #
def _load_items(project_id: str) -> List[Dict[str, Any]]:
    doc = get_backend().get_doc(project_id, DOC_KEY) or {}
    items = doc.get("items") if isinstance(doc, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def save_solids(project_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """落一版固体文档。版本号只增不改：同内容重复落库也长版本号（每版可回看）。"""
    if not isinstance(doc, dict):
        raise ValueError("save_solids() 需要一份固体文档")
    items = _load_items(project_id)
    version = 1
    for item in items:
        value = _num(item.get("version"))
        if value is not None:
            version = max(version, int(value) + 1)
    record = {"version": version, "saved_at": datetime.now(timezone.utc).isoformat(),
              "engine_version": ENGINE_VERSION, "doc": copy.deepcopy(doc)}
    items.insert(0, record)
    get_backend().put_doc(project_id, DOC_KEY, {"items": items[:MAX_VERSIONS]})
    return {"version": version, "saved_at": record["saved_at"],
            "engine_version": ENGINE_VERSION, "doc": copy.deepcopy(doc)}


def load_solids(project_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """读回一版固体文档（默认最新一版）。"""
    for item in _load_items(project_id):
        if version is not None and int(_num(item.get("version")) or 0) != int(version):
            continue
        doc = item.get("doc")
        if not isinstance(doc, dict):
            continue
        record = copy.deepcopy(doc)
        record["solids_version"] = int(_num(item.get("version")) or 0)
        return record
    return None
