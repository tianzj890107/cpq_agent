"""图纸零件 → 平板挤出体（ASCII STL）（Spec `packaging-parts-3d-extrusion.md`）。

只做**闭合轮廓 × 已知料厚**的直线挤出：底面扇形三角化 + 顶面 + 侧壁。
不做折弯成型、不做盒体装配、不做刀模重建 —— 这一版的目标是"右栏那块画布不再空着"，
而且挤不出来的件必须给出**明确原因**（`UNSUPPORTED_REASONS`），不许留一块空白画布。

三角化与法向全部自己写（`numpy` / `trimesh` / `shapely` 一律不引入）。
本模块是纯函数风格：`extrude()` 不落盘、不联网；落盘只有 `save_solids()` 一处。
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

#: 挤不出来的原因闭集（前端据此翻人话，顺序即判定顺序）。
UNSUPPORTED_REASONS = ("outline_open", "outline_unavailable", "thickness_unknown",
                       "concave_polygon", "too_few_points", "too_many_points")

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


def _triangles(points: List[List[float]], thickness: float) -> List[List[List[float]]]:
    """底 2(n-2) 个 + 侧 2n 个三角形（底面法向朝 -Z，侧面按轮廓走向）。"""
    count = len(points)
    top = [[point[0], point[1], thickness] for point in points]
    bottom = [[point[0], point[1], 0.0] for point in points]
    faces: List[List[List[float]]] = []
    for index in range(1, count - 1):          # 底面：扇形，反向保证法向朝下
        faces.append([bottom[0], bottom[index + 1], bottom[index]])
    for index in range(1, count - 1):          # 顶面：扇形
        faces.append([top[0], top[index], top[index + 1]])
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
    if not is_convex(points):
        return _unsupported(part_code, "concave_polygon")
    faces = _triangles(points, thickness)
    return {"part_code": part_code, "status": "ok", "reason": "",
            "stl": _stl(part_code, faces), "triangles": len(faces),
            "bbox_mm": _bbox_mm(points, thickness),
            "volume_mm3": _round(polygon_area_mm2(points) * thickness),
            "points": len(points)}


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
