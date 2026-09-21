"""DWG 图纸 → 零件（展开件）提取与回填（Spec `packaging-dwg-parts-extraction.md`）。

以 CAD IR 的 `geometry.components`（连通分量）为**唯一**零件来源 —— 真图
（`酒盒.dwg`）只有 2 条闭合轮廓却有 402 个连通分量，"零件 = 闭合轮廓"在真图上不成立。
每个分量产出一件：展开长宽取分量 bbox，角色取件内实体图层角色的最高优先级，
证据逐条可回查。

本模块是**纯函数**：`extract()` 不落盘、不联网、不改入参、不写审计；
落盘只有 `save_parts()` 一处（版本化，同 `packaging_semantics` 的做法）。
BOM 回填（`bind_rows`）只做行级临时口径 —— 正式口径应是「零件 ↔ 模板行」的对应表。
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from ..storage.meta_backend import get_backend
from .cad_ir import geometry as cad_geometry

ENGINE_VERSION = "packaging-parts/1"

#: 零件文档在项目存储里的 doc key。
DOC_KEY = "packaging_parts"

#: 过滤阈值与上限：默认值**只在这里**，不许散落在判定代码里。
DEFAULT_OPTIONS = {"min_area_mm2": 2000, "max_edge_mm": 1200,
                   "max_area_mm2": 1000000, "max_parts": 64}

#: 「可制造曲线」闭集：其余（TEXT / MTEXT / DIMENSION / INSERT / ATTRIB…）不构成零件。
CURVE_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE")

#: 提不出零件时的稳定原因码（前端据此说清"为什么没有零件"）。
REASON_CODES = ("edge_over_max", "area_over_max", "area_under_min", "no_curve_entity",
                "no_components", "all_filtered", "no_unit")

PART_CODE_FORMAT = "DWG-P%02d"

#: 零件 id 命名空间（Spec `packaging-parts-downstream-process-and-cost.md` §2）：
#: 本版 `part_id` 逐字等于 `part_code`（图上一件就是一个零件号），不建额外映射表。
PART_ID_NAMESPACE = "packaging_parts/1"

#: 下游（工艺 / 成本）拒绝码闭集：路由据此给 409，前端据此说清缺什么。
PROCESS_REJECT_CODES = ("PACKAGING_PART_NOT_CLOSED", "PACKAGING_PART_MATERIAL_UNKNOWN",
                        "PACKAGING_PART_NOT_FOUND")

#: 件内角色优先级：一件里既有刀线又有压痕 → `cut`。
ROLE_PRIORITY = ("cut", "half_cut", "crease", "v_groove", "glue_flap", "print", "bleed",
                 "frame", "hole", "unknown")

#: 每个项目最多保留的零件文档版本数（旧版本仍可按 id 回看）。
MAX_VERSIONS = 20

#: 回填只碰这两类行（Spec C7.1）。
BINDABLE_CATEGORIES = ("box_part", "optional_part")

#: 行级绑定的规则号（留痕用，正式口径定下来之前一直带这个标记）。
BINDING_RULE_ID = "dwg_parts_row_pairing_v1"

UNAVAILABLE_MESSAGES = {
    "no_components": "图纸里没有可用的连通分量，请确认上传的是 2D 刀模图",
    "all_filtered": "图纸里的分量都被过滤（整版图框 / 碎线 / 无制造曲线），没有可制造的零件",
    "no_unit": "图纸单位未确认，不能给出绝对展开尺寸，请先确认图纸单位",
}

# --------------------------------------------------------------------------- #
# 真实轮廓口径（Spec `packaging-parts-true-outline.md` §3，逐字实现）
# --------------------------------------------------------------------------- #

#: 端点相接容差（mm）。**单位未确认时不得跑** —— 求出来的 "mm" 没有意义。
LOOP_TOLERANCE_MM = 1.0

#: 环至少 3 条边。
MIN_LOOP_EDGES = 3

#: 轮廓三态与尺寸来源（对外可见，下游据此说清“为什么这件的尺寸不可信”）。
OUTLINE_STATUSES = ("closed", "open", "unavailable")
SIZE_SOURCES = ("closed_outline", "component_bbox", "dwg_outline")

#: 求环只吃这几类实体；DIMENSION / HATCH / INSERT / TEXT 一律不参与（Spec §3 第 1 步）。
LOOP_TYPES = ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE")

#: CIRCLE 天然闭环，按这个点数采样成多边形（面积走鞋带公式的近似）。
CIRCLE_SAMPLES = 32

#: 求环的计算预算：真图 402 个分量，必须给硬上限（结果仍确定，只是到点就停）。
MAX_LOOP_CYCLES = 256
MAX_LOOP_STATES = 20000


# --------------------------------------------------------------------------- #
# 图纸标注里的材料 / 厚度（Spec `packaging-parts-downstream-process-and-cost.md` §3）
# --------------------------------------------------------------------------- #
# 材料与厚度**不是猜出来的**，是图纸自己写着的东西（标题栏的「包装材料说明」与引出标注）。
# 取法两档，都留痕到 row 的 material_source / thickness_source：
#   1. 件级：贴着这一件（距该件包围盒 ≤ max(25% 对角线, 50mm)）的最近一条标注；
#   2. 图级兜底：**只在这张图上该字段只有一个候选值**时才用 —— 两个值以上就是有歧义，
#      宁可不填（缺料由 processability 拒绝并说清缺什么，绝不用默认值硬算）。
MATERIAL_KEYWORDS = ("灰板", "白卡", "粉灰", "铜版纸", "双铜", "卡纸", "牛皮纸", "坑纸",
                     "瓦楞", "PET", "光银", "哑胶", "E坑", "BC坑", "B坑", "裱")

#: 板材厚度可信区间（mm）：超出这个范围的数字是外形尺寸，不是料厚。
THICKNESS_MIN_MM = 0.2
THICKNESS_MAX_MM = 20.0

#: 件级半径：贴着这一件才算这一件的标注。
NOTE_DISTANCE_RATIO = 0.25
NOTE_DISTANCE_MIN_MM = 50.0

#: 标注文本留痕长度上限。
NOTE_TEXT_MAX = 60

_THICKNESS_EXPLICIT = re.compile(r"厚(?:度)?\s*[:：]?\s*(\d+(?:\.\d+)?)")
_THICKNESS_MM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm|MM)(?![0-9A-Za-z])")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _text(value: Any) -> str:
    return str(value or "").strip()


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 3)


def _options(options: Any) -> Dict[str, float]:
    merged = dict(DEFAULT_OPTIONS)
    if isinstance(options, dict):
        for key, value in options.items():
            if key in merged and _num(value) is not None:
                merged[key] = _num(value)
    return merged


def _layer_key(name: Any) -> str:
    return _text(name).upper()


def _bbox_of(component: Dict[str, Any], entities: List[Dict[str, Any]]) -> Optional[List[float]]:
    """分量外接矩形：优先用分量自带 bbox，缺失时由件内实体 bbox 合并（仍缺失 → None）。"""
    raw = component.get("bbox")
    values = [_num(item) for item in raw] if isinstance(raw, (list, tuple)) else []
    if len(values) == 4 and all(item is not None for item in values):
        return [float(item) for item in values]
    boxes = [entity.get("bbox") for entity in entities if isinstance(entity.get("bbox"), list)]
    boxes = [[_num(item) for item in box] for box in boxes]
    boxes = [box for box in boxes if len(box) == 4 and all(item is not None for item in box)]
    if not boxes:
        return None
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _layer_roles(ir: Dict[str, Any], semantics: Any) -> Dict[str, str]:
    """图层名（大写）→ 角色。有语义文档就用它，否则现算一份（同一套规则）。"""
    doc = semantics if isinstance(semantics, dict) else None
    if doc is None:
        try:                                              # 现算：只读规则文件，不落盘
            from . import packaging_semantics
            doc = packaging_semantics.analyze(ir)
        except Exception:                                 # noqa: BLE001 - 算不出来也要出零件
            doc = None
    roles: Dict[str, str] = {}
    if isinstance(doc, dict):
        for row in doc.get("layers") or []:
            if not isinstance(row, dict):
                continue
            key = _layer_key(row.get("name"))
            if key:
                roles[key] = _text(row.get("role")) or "unknown"
    if roles:
        return roles
    # 语义层不可用时的兜底：IR 里已经带角色就用它，否则 unknown（绝不猜）。
    for row in ir.get("layers") or []:
        if not isinstance(row, dict):
            continue
        key = _layer_key(row.get("name"))
        if key:
            roles[key] = _text(row.get("role") or row.get("inferred_role")) or "unknown"
    return roles


def _role_index(role: str) -> int:
    try:
        return ROLE_PRIORITY.index(role)
    except ValueError:
        return len(ROLE_PRIORITY) - 1


def _known_evidence(ir: Dict[str, Any]) -> Any:
    return ir.get("evidence") if isinstance(ir.get("evidence"), dict) else {}


# --------------------------------------------------------------------------- #
# 0b 图纸标注 → 材料 / 厚度候选（纯函数，不读库不联网）
# --------------------------------------------------------------------------- #
def _note_thickness(text: str, has_material: bool) -> Optional[float]:
    """标注里的料厚：显式「厚度2MM」优先，其次「1.8mm 灰板」这种带材料的写法。"""
    match = _THICKNESS_EXPLICIT.search(text)
    if match:
        value = _num(match.group(1))
    elif has_material:
        match = _THICKNESS_MM.search(text)
        value = _num(match.group(1)) if match else None
    else:
        value = None
    if value is None or not (THICKNESS_MIN_MM <= value <= THICKNESS_MAX_MM):
        return None
    return value


def _note_material(text: str) -> str:
    """标注里的材料：命中材料词才认（「面纸转越南」这种备注不是材料）。"""
    return text[:NOTE_TEXT_MAX] if any(word in text for word in MATERIAL_KEYWORDS) else ""


def drawing_notes(ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    """图纸标注 → 材料/厚度候选。顺序只由 entity_id 决定（同一份图纸两次跑逐字相同）。"""
    rows: List[Dict[str, Any]] = []
    for item in (ir.get("texts") or []):
        if not isinstance(item, dict):
            continue
        text = " ".join(_text(item.get("normalized_text")).split())
        if not text:
            continue
        material = _note_material(text)
        thickness = _note_thickness(text, bool(material))
        if not material and thickness is None:
            continue
        position = item.get("position")
        position = [float(value) for value in position] \
            if isinstance(position, (list, tuple)) and len(position) >= 2 else None
        rows.append({"entity_id": _text(item.get("entity_id")),
                     "text": text[:NOTE_TEXT_MAX], "material": material,
                     "thickness_mm": thickness, "position": position,
                     "evidence_ref": _text(item.get("evidence_ref"))})
    rows.sort(key=lambda row: (row["entity_id"], row["text"]))
    return rows


def _distance_to_bbox(position: List[float], bbox: List[float]) -> float:
    """点到包围盒的欧氏距离（件内为 0）。"""
    x, y = float(position[0]), float(position[1])
    x0, x1 = sorted((float(bbox[0]), float(bbox[2])))
    y0, y1 = sorted((float(bbox[1]), float(bbox[3])))
    return math.hypot(max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1))


def _note_radius(bbox: List[float]) -> float:
    return max(NOTE_DISTANCE_RATIO * math.hypot(float(bbox[2]) - float(bbox[0]),
                                                float(bbox[3]) - float(bbox[1])),
               NOTE_DISTANCE_MIN_MM)


def _nearest_note(notes: List[Dict[str, Any]], bbox: Optional[List[float]],
                  field: str) -> Optional[Tuple[Dict[str, Any], float]]:
    """件级：贴着这一件（≤ 半径）的最近一条标注；并列取 entity_id 小的。"""
    if not bbox:
        return None
    radius = _note_radius(bbox)
    best: Optional[Tuple[Dict[str, Any], float]] = None
    for note in notes:
        if not note.get(field) or not note.get("position"):
            continue
        distance = _distance_to_bbox(note["position"], bbox)
        if distance > radius:
            continue
        if best is None or (distance, note["entity_id"]) < (best[1], best[0]["entity_id"]):
            best = (note, distance)
    return best


def _single_value(notes: List[Dict[str, Any]], field: str) -> Any:
    """图级兜底：**只有**全图该字段只有一个候选值时才用（两个值以上就是有歧义）。"""
    values = sorted({note[field] for note in notes if note.get(field)})
    return values[0] if len(values) == 1 else None


def _size_notes(notes: List[Dict[str, Any]], bbox: Optional[List[float]]) -> Dict[str, Any]:
    """给一件定材料与厚度：件级优先，图级唯一值兜底，都不成立就留空并写清为什么。"""
    out: Dict[str, Any] = {"thickness_mm": None, "material": None,
                           "thickness_source": None, "material_source": None,
                           "note_refs": []}
    for field, key in (("thickness_mm", "thickness"), ("material", "material")):
        found = _nearest_note(notes, bbox, field)
        if found is not None:
            note, distance = found
            out[field] = note[field]
            out["%s_source" % key] = {"kind": "part_note", "text": note["text"],
                                      "evidence_ref": note["evidence_ref"],
                                      "distance_mm": _round(distance)}
            if note["evidence_ref"]:
                out["note_refs"].append(note["evidence_ref"])
            continue
        value = _single_value(notes, field)
        if value is None:
            continue
        out[field] = value
        out["%s_source" % key] = {"kind": "drawing_note",
                                  "text": next(row["text"] for row in notes
                                               if row[field] == value),
                                  "evidence_ref": "", "distance_mm": None}
    return out


# --------------------------------------------------------------------------- #
# 1 提取（纯函数）
# --------------------------------------------------------------------------- #
def _quant_key(point: Tuple[float, float]) -> Tuple[int, int]:
    """端点按 LOOP_TOLERANCE_MM 量化后归并（Spec §3 第 2 步）。"""
    return (int(round(float(point[0]) / LOOP_TOLERANCE_MM)),
            int(round(float(point[1]) / LOOP_TOLERANCE_MM)))


def _entity_chains(entity: Dict[str, Any]
                   ) -> Tuple[Optional[List[List[Tuple[float, float]]]], bool, str]:
    """实体 → (chains, natural_closed, approximation)；拿不到坐标 → (None, False, "")。

    chains 是若干条点序列（每条 ≥ 2 点）；natural_closed 表示这一条实体自己就是闭环。
    弧段（ARC）与样条（SPLINE）在本版按端点直连近似，必须留痕（Spec §3 第 5 步）。
    """
    kind = _text(entity.get("type")).upper()
    if kind not in LOOP_TYPES:
        return None, False, ""
    raw = entity.get("attributes")
    attrs = raw if isinstance(raw, dict) else {}
    if kind == "LINE":
        start = cad_geometry.point_of(attrs.get("start"))
        end = cad_geometry.point_of(attrs.get("end"))
        if not start or not end:
            return None, False, ""
        return [[start, end]], False, ""
    if kind == "ARC":
        center = cad_geometry.point_of(attrs.get("center"))
        radius = _num(attrs.get("radius"))
        if not center or radius is None or radius <= 0:
            return None, False, ""
        first_angle = _num(attrs.get("start_angle")) or 0.0
        second_angle = _num(attrs.get("end_angle")) or 0.0
        first = (center[0] + radius * math.cos(math.radians(first_angle)),
                 center[1] + radius * math.sin(math.radians(first_angle)))
        second = (center[0] + radius * math.cos(math.radians(second_angle)),
                  center[1] + radius * math.sin(math.radians(second_angle)))
        if cad_geometry.distance(first, second) <= LOOP_TOLERANCE_MM:
            return None, False, ""
        return [[first, second]], False, "arc_endpoints"
    if kind == "CIRCLE":
        center = cad_geometry.point_of(attrs.get("center"))
        radius = _num(attrs.get("radius"))
        if not center or radius is None or radius <= 0:
            return None, False, ""
        ring = [(center[0] + radius * math.cos(2.0 * math.pi * index / CIRCLE_SAMPLES),
                 center[1] + radius * math.sin(2.0 * math.pi * index / CIRCLE_SAMPLES))
                for index in range(CIRCLE_SAMPLES)]
        return [ring + [ring[0]]], True, ""
    if kind in ("LWPOLYLINE", "POLYLINE"):
        points = cad_geometry.points_of(attrs.get("points") or [])
        if len(points) < 2:
            return None, False, ""
        closed = bool(entity.get("closed")) or cad_geometry.is_closed(points, LOOP_TOLERANCE_MM)
        if closed and not cad_geometry.is_closed(points, LOOP_TOLERANCE_MM):
            points = points + [points[0]]
        return [points], closed, ""
    if kind == "SPLINE":
        points = cad_geometry.points_of(attrs.get("fit_points") or [])
        if len(points) < 2:
            return None, False, ""
        return [points], cad_geometry.is_closed(points, LOOP_TOLERANCE_MM), "spline_fit"
    return None, False, ""


def _component_edges(members: List[Dict[str, Any]]
                     ) -> Tuple[List[Tuple[Any, Any, str, str]], Dict[Any, Tuple[float, float]]]:
    """件内实体 → 边与顶点表。实体顺序按 entity_id 排序，保证输出确定性。"""
    edges: List[Tuple[Any, Any, str, str]] = []
    vertices: Dict[Any, Tuple[float, float]] = {}
    for entity in sorted(members, key=lambda row: _text(row.get("entity_id"))):
        chains, _natural, approximation = _entity_chains(entity)
        if not chains:
            continue
        entity_id = _text(entity.get("entity_id"))
        for chain in chains:
            keys = []
            for point in chain:
                key = _quant_key(point)
                keys.append(key)
                vertices.setdefault(key, (float(point[0]), float(point[1])))
            for index in range(len(keys) - 1):
                if keys[index] == keys[index + 1]:
                    continue
                edges.append((keys[index], keys[index + 1], entity_id, approximation))
    return edges, vertices


def _two_core(adjacency: Dict[Any, List[Tuple[Any, int]]]) -> Dict[Any, List[Tuple[Any, int]]]:
    """剥掉度 < 2 的顶点：环只可能活在 2-core 里（真图多数分量是长开放链，省掉无用搜索）。"""
    degree = {node: len(neighbours) for node, neighbours in adjacency.items()}
    pending = sorted(node for node, value in degree.items() if value < 2)
    removed: set = set()
    while pending:
        node = pending.pop(0)
        if node in removed:
            continue
        removed.add(node)
        for neighbour, _edge in adjacency.get(node) or ():
            if neighbour in removed:
                continue
            degree[neighbour] = degree.get(neighbour, 0) - 1
            if degree[neighbour] < 2:
                pending.append(neighbour)
    return {node: [(neighbour, edge) for neighbour, edge in neighbours
                   if neighbour not in removed]
            for node, neighbours in adjacency.items() if node not in removed}


def _find_cycles(adjacency: Dict[Any, List[Tuple[Any, int]]], *,
                 min_edges: int = MIN_LOOP_EDGES, max_cycles: int = MAX_LOOP_CYCLES,
                 max_states: int = MAX_LOOP_STATES) -> List[Dict[str, Any]]:
    """找简单环：顶点不重复、边不重复、首尾相接（Spec §3 第 3 步）。

    起点固定为环里最小的节点（neighbour < start 剪枝），同一个环只报一次。
    预算是硬上限：到点就停，但结果仍确定（同一份 IR 两次跑一模一样）。
    """
    loops: List[Dict[str, Any]] = []
    states = [0]

    def walk(start: Any, node: Any, path_nodes: List[Any], path_edges: List[int],
             used: set) -> None:
        for neighbour, edge in adjacency.get(node) or ():
            states[0] += 1
            if states[0] >= max_states or len(loops) >= max_cycles:
                return
            if edge in path_edges:
                continue
            if neighbour == start:
                if len(path_edges) + 1 >= min_edges:
                    loops.append({"nodes": list(path_nodes), "edges": list(path_edges) + [edge]})
                continue
            if neighbour in used or neighbour < start:
                continue
            used.add(neighbour)
            path_nodes.append(neighbour)
            path_edges.append(edge)
            walk(start, neighbour, path_nodes, path_edges, used)
            path_edges.pop()
            path_nodes.pop()
            used.discard(neighbour)

    for start in sorted(adjacency):
        if states[0] >= max_states or len(loops) >= max_cycles:
            break
        walk(start, start, [start], [], {start})
    return loops


def _largest_loop(members: List[Dict[str, Any]], min_area: float
                  ) -> Tuple[Optional[Dict[str, Any]], bool, bool]:
    """件内求最大闭合环 → (outline, saw_loop, has_coordinates)。

    saw_loop 表示确实找到了环（哪怕面积被门槛挡掉）；has_coordinates 表示这件里至少有
    实体给出了可用坐标 —— 拿不到坐标时分量只能沿用 DWG 自己的包围盒（Spec §3）。
    """
    edges, vertices = _component_edges(members)
    if not edges:
        return None, False, False
    adjacency: Dict[Any, List[Tuple[Any, int]]] = {}
    for index, (first, second, _entity_id, _approximation) in enumerate(edges):
        adjacency.setdefault(first, []).append((second, index))
        adjacency.setdefault(second, []).append((first, index))
    best: Optional[Dict[str, Any]] = None
    best_area = 0.0
    saw_loop = False
    for loop in _find_cycles(_two_core(adjacency)):
        polygon = [vertices[key] for key in loop["nodes"]]
        if len(polygon) < MIN_LOOP_EDGES:
            continue
        area = cad_geometry.polygon_area(polygon)
        saw_loop = True
        if area < min_area or area <= best_area:
            continue
        approximations = [edges[index][3] for index in loop["edges"] if edges[index][3]]
        outline = {
            "points": [[float(x), float(y)] for x, y in polygon],
            "entity_ids": sorted({edges[index][2] for index in loop["edges"]}),
            "closed": True,
            "area_mm2": _round(area),
            "bbox": cad_geometry.bbox_of(polygon),
        }
        if approximations:
            # 一个环里混了弧段与样条时，弧段优先（近似口径只留一条）。
            outline["approximation"] = ("arc_endpoints" if "arc_endpoints" in approximations
                                       else approximations[0])
        best, best_area = outline, area
    return best, saw_loop, True


def extract(ir: Dict[str, Any], semantics: Any = None, *,
            options: Any = None) -> Dict[str, Any]:
    """CAD IR → 零件文档。同一份 IR 两次跑必须逐字相同（排序与编号全部确定性）。"""
    if not isinstance(ir, dict):
        raise TypeError("extract() 需要一份 CAD IR 文档")
    config = _options(options)
    geometry = ir.get("geometry") if isinstance(ir.get("geometry"), dict) else {}
    components = [row for row in (geometry.get("components") or []) if isinstance(row, dict)]
    entities = {str((row or {}).get("entity_id")): row
                for row in (ir.get("entities") or []) if isinstance(row, dict)}
    known = _known_evidence(ir)
    roles = _layer_roles(ir, semantics)
    notes = drawing_notes(ir)
    units = ir.get("units") if isinstance(ir.get("units"), dict) else {}
    unit_status = _text(units.get("unit_status"))
    unit_ok = unit_status == "confirmed"

    kept: List[Dict[str, Any]] = []
    filtered: List[Dict[str, Any]] = []
    for component in components:
        component_id = _text(component.get("component_id"))
        entity_ids = [_text(item) for item in (component.get("entity_ids") or [])]
        members = [entities[item] for item in entity_ids if item in entities]
        bbox = _bbox_of(component, members)
        if bbox is None:
            box_length = box_width = None
            box_area = 0.0
        else:
            box_length = abs(bbox[2] - bbox[0])
            box_width = abs(bbox[3] - bbox[1])
            box_area = box_length * box_width
        curves = [entity for entity in members
                  if _text(entity.get("type")).upper() in CURVE_TYPES]

        # —— 真实轮廓：件内求最大闭合环（Spec `packaging-parts-true-outline.md` §3）——
        # 单位未确认时求环没有意义（没有可信的 mm），直接标 unavailable。
        if unit_ok:
            outline, saw_loop, has_coordinates = _largest_loop(
                members, float(config["min_area_mm2"]))
        else:
            outline, saw_loop, has_coordinates = None, False, False
        if not unit_ok:
            outline_status, outline_reason = "unavailable", "unit_unconfirmed"
            size_source = "dwg_outline"
            length = width = None
            area = 0.0
        elif outline is not None:
            outline_status, outline_reason, size_source = "closed", "", "closed_outline"
            loop_box = outline.get("bbox") or bbox
            length = abs(loop_box[2] - loop_box[0])
            width = abs(loop_box[3] - loop_box[1])
            area = float(outline.get("area_mm2") or 0.0)
        else:
            # 求不出环 → 退回分量包围盒**并留痕**（绝不许把包围盒说成轮廓尺寸）。
            outline_status = "open"
            outline_reason = "loop_too_small" if saw_loop else "no_closed_loop"
            # 分量里一条可用坐标都没有时，尺寸来自 DWG 自己的包围盒（与今天口径一致）。
            size_source = "component_bbox" if has_coordinates else "dwg_outline"
            length, width, area = box_length, box_width, box_area
        if outline is None:
            outline = {"points": None, "entity_ids": [], "closed": False,
                       "area_mm2": None, "bbox": list(bbox) if bbox else None}

        reasons: List[str] = []
        if bbox is not None:
            if max(box_length or 0.0, box_width or 0.0) > float(config["max_edge_mm"]):
                reasons.append("edge_over_max")
            if box_area > float(config["max_area_mm2"]):
                reasons.append("area_over_max")
            # 有环的件即使环比 min_area 小也要留着（Spec §3：报 loop_too_small，不许悄悄丢掉）。
            if box_area < float(config["min_area_mm2"]) and not saw_loop:
                reasons.append("area_under_min")
        # 「没有可制造曲线」只在**看得见实体**时才敢判：分量声明了实体却一条都查不到
        # （块引用 / 代理实体 / IR 缺条），说明这一件对我们是不透明的，宁可留着让人看，
        # 也不能悄悄当碎线丢掉（真图上"零件 = 闭合轮廓"就是这么丢掉 400 件的前车之鉴）。
        if not curves and (not entity_ids or members):
            reasons.append("no_curve_entity")
        if reasons:
            filtered.append({"component_id": component_id, "reasons": reasons,
                             "reason": reasons[0], "bbox": bbox})
            continue

        part_roles = sorted({roles.get(_layer_key(entity.get("layer")), "unknown")
                             for entity in curves})
        role = min(part_roles, key=_role_index) if part_roles else "unknown"
        layer_names = sorted({_text(entity.get("layer")) for entity in curves
                              if _text(entity.get("layer"))})
        refs = [str(entity.get("evidence_ref") or "") for entity in curves]
        refs += ["ev:L:%s" % name for name in layer_names]
        size_notes = _size_notes(notes, bbox)
        refs += [ref for ref in size_notes["note_refs"] if ref in known]
        evidence_refs = sorted({ref for ref in refs if ref and ref in known})
        kept.append({
            "component_id": component_id,
            "entity_ids": sorted(set(entity_ids)),
            "entity_total": len(entity_ids),
            "bbox": bbox,
            "length": length,
            "width": width,
            "area": area,
            "role": role,
            "layers": layer_names,
            "evidence_refs": evidence_refs,
            "outline_status": outline_status,
            "outline": outline,
            "outline_reason": outline_reason,
            "size_source": size_source,
            "thickness_mm": size_notes["thickness_mm"],
            "material": size_notes["material"],
            "thickness_source": size_notes["thickness_source"],
            "material_source": size_notes["material_source"],
        })

    # 排序：面积降序、component_id 升序（input 书写顺序不影响输出）。
    kept.sort(key=lambda row: (-(row["area"] or 0.0), row["component_id"]))
    filtered.sort(key=lambda row: row["component_id"])

    parts: List[Dict[str, Any]] = []
    seen: Dict[Tuple[float, float, int], str] = {}
    for index, row in enumerate(kept, start=1):
        part_code = PART_CODE_FORMAT % index
        key = (round(row["length"] or 0.0, 3), round(row["width"] or 0.0, 3),
               row["entity_total"])
        repeat_of = seen.get(key, "")
        if not repeat_of or repeat_of == part_code:
            seen[key] = part_code
        parts.append({
            "part_code": part_code,
            "part_id": part_code,
            "name": "图纸零件 P%02d" % index,
            "unfolded_length_mm": _round(row["length"]) if unit_ok else None,
            "unfolded_width_mm": _round(row["width"]) if unit_ok else None,
            "area_mm2": _round(row["area"]) if unit_ok else None,
            "layers": row["layers"],
            "role": row["role"],
            "component_id": row["component_id"],
            "entity_ids": row["entity_ids"],
            "evidence_refs": row["evidence_refs"],
            "repeat_of": repeat_of,
            "outline_status": row["outline_status"],
            "outline": row["outline"],
            "outline_reason": row["outline_reason"],
            "size_source": row["size_source"],
            "thickness_mm": row["thickness_mm"],
            "material": row["material"],
            "thickness_source": row["thickness_source"],
            "material_source": row["material_source"],
        })

    max_parts = int(config["max_parts"])
    truncated = max(0, len(parts) - max_parts)
    parts = parts[:max_parts]
    # 三态计数只统计**最终保留**的件（与 part_total 自洽：三者之和 == part_total）。
    closed_total = sum(1 for row in parts if row["outline_status"] == "closed")
    open_total = sum(1 for row in parts if row["outline_status"] == "open")
    outline_unavailable_total = sum(1 for row in parts
                                    if row["outline_status"] == "unavailable")
    closed_ratio = (float(closed_total) / float(len(parts))) if parts else 0.0

    unavailable: List[Dict[str, Any]] = []
    if not components:
        unavailable.append({"code": "no_components",
                            "message": UNAVAILABLE_MESSAGES["no_components"]})
    elif not kept:
        unavailable.append({"code": "all_filtered",
                            "message": UNAVAILABLE_MESSAGES["all_filtered"]})
    if not unit_ok:
        unavailable.append({"code": "no_unit", "message": UNAVAILABLE_MESSAGES["no_unit"]})

    by_role: Dict[str, int] = {}
    for row in parts:
        by_role[row["role"]] = by_role.get(row["role"], 0) + 1

    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    sem = semantics if isinstance(semantics, dict) else {}
    sem_source = sem.get("source") if isinstance(sem.get("source"), dict) else {}
    doc = {
        "engine_version": ENGINE_VERSION,
        "part_id_namespace": PART_ID_NAMESPACE,
        "parts": parts,
        "filtered": filtered,
        "unavailable": unavailable,
        "stats": {"part_total": len(parts), "filtered_total": len(filtered),
                  "truncated": truncated, "by_role": by_role,
                  "closed_total": closed_total, "open_total": open_total,
                  "outline_unavailable_total": outline_unavailable_total,
                  "closed_ratio": _round(closed_ratio)},
        "source": {
            "ir_id": _text(ir.get("ir_id")),
            "ir_hash": _text(ir.get("ir_hash")),
            "semantics_id": _text(sem.get("semantics_id")),
            "semantics_hash": _text(sem.get("semantics_hash")),
            "drawing_version": source.get("drawing_version") or sem_source.get("drawing_version"),
            "unit_status": unit_status,
        },
        "reviewable": True,
    }
    doc["parts_id"] = ""
    doc["parts_hash"] = ""
    doc["parts_id"], doc["parts_hash"] = _identity(doc)
    return doc


def _identity(doc: Dict[str, Any]) -> Tuple[str, str]:
    """版本锚点：同一份内容 → 同一个 id（落库幂等）。"""
    from .packaging_semantics import model as sem_model

    body = {key: value for key, value in doc.items()
            if key not in ("parts_id", "parts_hash")}
    digest = sem_model.sha256_hex(sem_model.canonical_json(sem_model.json_safe(body)))
    return "parts:" + digest[:16], digest


def summarize(doc: Any, *, solids: Any = None) -> Dict[str, Any]:
    """摘要（不含 entity_ids / 证据明细）：给会话、看板与门禁用。

    五个指标（Spec `packaging-parts-downstream-acceptance.md` §2）一律**由零件行现算**，
    `part_total = 0` 时全部 `0.0`（不返回 null、不抛错）—— 门禁与看板据此说"这条链路
    到底做到了哪一步"。`solids` 可选：给挤出结论时 `solid_ok_ratio` 才算得出来。
    """
    payload = doc if isinstance(doc, dict) else {}
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    parts = []
    for row in payload.get("parts") or []:
        if not isinstance(row, dict):
            continue
        parts.append({"part_code": _text(row.get("part_code")),
                      "name": _text(row.get("name")),
                      "unfolded_length_mm": row.get("unfolded_length_mm"),
                      "unfolded_width_mm": row.get("unfolded_width_mm"),
                      "area_mm2": row.get("area_mm2"),
                      "layers": list(row.get("layers") or []),
                      "role": _text(row.get("role")),
                      "component_id": _text(row.get("component_id")),
                      "repeat_of": _text(row.get("repeat_of")),
                      "size_source": _text(row.get("size_source"))})
    total = _num(stats.get("part_total"))
    total = len(parts) if total is None else max(0, int(total))
    rows = [row for row in (payload.get("parts") or []) if isinstance(row, dict)]
    solid_status = {str(row.get("part_code") or ""): _text(row.get("solid_status"))
                    for row in rows if row.get("solid_status")}
    if isinstance(solids, dict):
        for item in (solids.get("parts") or []):
            if not isinstance(item, dict):
                continue
            code = _text(item.get("part_code"))
            if code:
                solid_status[code] = _text(item.get("status"))
    closed_total = sum(1 for row in rows
                       if _text(row.get("outline_status")) == "closed")
    role_known = sum(1 for row in rows if _text(row.get("role")) not in ("", "unknown"))
    processable = sum(1 for row in rows if processability(row).get("ok"))
    solid_ok = sum(1 for row in rows
                   if solid_status.get(_text(row.get("part_code"))) == "ok")
    mix = {name: 0 for name in SIZE_SOURCES}
    for row in rows:
        source = _text(row.get("size_source"))
        if source in mix:
            mix[source] += 1

    def _ratio(count: int) -> float:
        return _round(float(count) / float(total)) if total else 0.0

    return {
        "engine_version": _text(payload.get("engine_version")) or ENGINE_VERSION,
        "parts_id": _text(payload.get("parts_id")),
        "parts_hash": _text(payload.get("parts_hash")),
        "closed_ratio": _ratio(closed_total),
        "role_known_ratio": _ratio(role_known),
        "solid_ok_ratio": _ratio(solid_ok),
        "processable_ratio": _ratio(processable),
        "size_source_mix": mix,
        "parts": parts,
        "filtered": [{"component_id": _text(row.get("component_id")),
                      "reasons": list(row.get("reasons") or [])}
                     for row in (payload.get("filtered") or []) if isinstance(row, dict)],
        "unavailable": [{"code": _text(row.get("code")), "message": _text(row.get("message"))}
                        for row in (payload.get("unavailable") or []) if isinstance(row, dict)],
        "stats": stats,
        "source": payload.get("source") if isinstance(payload.get("source"), dict) else {},
        "reviewable": bool(payload.get("reviewable")),
    }


# --------------------------------------------------------------------------- #
# 1b 下游适配：图纸零件 → 既有 IR 的 Part（Spec
#    `packaging-parts-downstream-process-and-cost.md` §3）
# --------------------------------------------------------------------------- #
def _material_spec(value: Any) -> str:
    """行里的材料可能是 {"spec": ...} 或一句标注文本；取不到就空（不许猜）。"""
    if isinstance(value, dict):
        for key in ("spec", "grade", "material_spec", "name"):
            text = _text(value.get(key))
            if text:
                return text
        return ""
    return _text(value)


def as_ir_part(row: Any) -> Any:
    """零件文档的一行 → 既有 IR 的 `Part`（唯一适配点，不改既有模型）。

    特征是**门槛式**的：只有闭合轮廓 + 已知料厚才给一个 `plate` 基体 —— 缺料时给
    特征等于拿错误输入去排工艺（Spec §3）。
    """
    from ..models.ir import Feature, Part, Provenance

    payload = row if isinstance(row, dict) else {}
    part_id = _text(payload.get("part_id")) or _text(payload.get("part_code"))
    name = _text(payload.get("name")) or part_id
    closed = _text(payload.get("outline_status")) == "closed"
    material = _material_spec(payload.get("material"))
    thickness = _num(payload.get("thickness_mm"))
    length = _num(payload.get("unfolded_length_mm"))
    width = _num(payload.get("unfolded_width_mm"))
    features = []
    if closed and thickness and thickness > 0 and length and width:
        features.append(Feature(type="plate", length=length, width=width,
                                thickness=thickness, purpose="图纸闭合轮廓 × 料厚"))
    if closed and material:
        confidence = 0.7
    elif closed:
        confidence = 0.4
    else:
        confidence = 0.3
    entities = [str(item) for item in (payload.get("entity_ids") or [])]
    note = " | ".join(part for part in (
        "packaging_parts/%s" % _text(payload.get("part_code")),
        "outline_status=%s" % (_text(payload.get("outline_status")) or "unknown"),
        "size_source=%s" % (_text(payload.get("size_source")) or "unknown"),
        "component=%s" % _text(payload.get("component_id")),
        "entities=%d" % len(entities),
        "material=%s" % (_material_source_kind(payload, "material_source") or "unknown"),
        "thickness=%s" % (_material_source_kind(payload, "thickness_source") or "unknown"),
    ) if part)
    return Part(part_id=part_id, name=name, role=_text(payload.get("role")) or None,
                quantity=1, features=features,
                material={"spec": material} if material else None,
                confidence=confidence, provenance=Provenance(note=note))


def _material_source_kind(payload: Dict[str, Any], key: str) -> str:
    source = payload.get(key)
    return _text(source.get("kind")) if isinstance(source, dict) else ""


def processability(row: Any, *, options: Any = None) -> Dict[str, Any]:
    """这一件现在能不能跑工艺/成本？缺什么就说什么（路由据此 409，绝不硬算）。

    返回 `{ok, code, message, missing_variables, part}`：`ok` 时 `part` 可直接交给
    `process.outline_process()` 与成本入口。
    """
    payload = row if isinstance(row, dict) else {}
    part_code = _text(payload.get("part_code"))
    if not part_code:
        return {"ok": False, "code": "PACKAGING_PART_NOT_FOUND",
                "message": "图纸里没有这个零件，请先跑一键解析", "missing_variables": [],
                "part": None}
    if _text(payload.get("outline_status")) != "closed":
        return {"ok": False, "code": "PACKAGING_PART_NOT_CLOSED",
                "message": "这一件没有可信的闭合轮廓（%s），不能拿包围盒尺寸去排工艺"
                           % (_text(payload.get("outline_reason")) or "no_closed_loop"),
                "missing_variables": ["outline"], "part": None}
    missing: List[str] = []
    if not _material_spec(payload.get("material")):
        missing.append("material")
    if not _num(payload.get("thickness_mm")):
        missing.append("thickness_mm")
    if missing:
        return {"ok": False, "code": "PACKAGING_PART_MATERIAL_UNKNOWN",
                "message": "这一件缺材料/厚度：%s（请在需求里补全后重跑解析）"
                           % "、".join(missing),
                "missing_variables": missing, "part": None}
    return {"ok": True, "code": "", "message": "", "missing_variables": [],
            "part": as_ir_part(payload)}


# --------------------------------------------------------------------------- #
# 2 落库 / 读回（版本化）
# --------------------------------------------------------------------------- #
def _load_items(project_id: str) -> List[Dict[str, Any]]:
    doc = get_backend().get_doc(project_id, DOC_KEY) or {}
    items = doc.get("items") if isinstance(doc, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def save_parts(project_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """落一版零件文档：同一 `parts_id` 覆盖同一条，新内容追加（最多 20 版）。"""
    if not isinstance(doc, dict):
        raise ValueError("save_parts() 需要一份零件文档")
    record = copy.deepcopy(doc)
    parts_id, parts_hash = _identity(record)
    record["parts_id"] = parts_id
    record["parts_hash"] = parts_hash
    items = [item for item in _load_items(project_id)
             if _text(item.get("parts_id")) != parts_id]
    items.insert(0, record)
    get_backend().put_doc(project_id, DOC_KEY, {"items": items[:MAX_VERSIONS]})
    return record


def load_parts(project_id: str, parts_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for item in _load_items(project_id):
        if parts_id is None or _text(item.get("parts_id")) == str(parts_id):
            return item
    return None


def list_parts(project_id: str) -> List[Dict[str, Any]]:
    return _load_items(project_id)


# --------------------------------------------------------------------------- #
# 3 回填 BOM 行（Spec C7，行级临时口径）
# --------------------------------------------------------------------------- #
def _needs_binding(row: Dict[str, Any]) -> bool:
    """「这一行算不出尺寸」的判据：缺输入，或整行根本没有尺寸。

    刻意不用「长或宽任一为空」：像 `RB01001-P10`（`长度 = W/3 + 40`，只有长度一维）
    这种行，长度是表达式**真算出来的**，不能拿图纸零件的尺寸去覆盖它（Spec C7.6）。
    """
    if _text(row.get("bom_category")) not in BINDABLE_CATEGORIES:
        return False
    if int(row.get("locked") or 0) == 1:
        return False
    if _text(row.get("status")) == "needs_input":
        return True
    return _num(row.get("length_mm")) is None and _num(row.get("width_mm")) is None


def _size_source_of(row: Dict[str, Any]) -> Dict[str, Any]:
    """把行上的尺寸溯源读成 dict（`_assemble` 给的是 JSON 字符串）。"""
    raw = row.get("size_source")
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    text = _text(row.get("size_source_json"))
    if not text:
        return {}
    try:
        import json
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def bind_rows(items: Any, parts: Any, *, options: Any = None) -> Dict[str, Any]:
    """把图纸零件回填进算不出尺寸的 BOM 行；纯函数：不改入参、不落库。

    - 只碰 `box_part` / `optional_part` 且（`needs_input` 或长宽为空）的行，锁定行绝不碰；
    - 行按出现顺序（= 模板 `seq` 升序）↔ 零件按面积降序，逐行取件；
      零件比分出的行少时**循环取件**并保留 `fallback_paired=true` 留痕
      —— 口径是"先保证有数"，正式对应表是下一批的事；
    - 留痕齐备：`size_source.dwg_binding`（component_id / part_code / rule_id /
      fallback_paired / original_missing_variables）、`source="dwg_parts"`、
      `missing_variables` 清空。
    """
    rows = [copy.deepcopy(row) for row in (items or []) if isinstance(row, dict)]
    available = [row for row in ((parts or {}).get("parts") or [])
                 if isinstance(row, dict)
                 and _num(row.get("unfolded_length_mm")) is not None
                 and _num(row.get("unfolded_width_mm")) is not None]
    bound = 0
    skipped_locked = 0
    unbound: List[str] = []
    pair_index = 0
    for row in rows:
        item_key = _text(row.get("item_key"))
        if _text(row.get("bom_category")) in BINDABLE_CATEGORIES \
                and int(row.get("locked") or 0) == 1 \
                and (_text(row.get("status")) == "needs_input"
                     or (_num(row.get("length_mm")) is None
                         and _num(row.get("width_mm")) is None)):
            skipped_locked += 1
            continue
        if not _needs_binding(row):
            continue
        if not available:
            unbound.append("part_size_unbound:%s" % item_key)
            continue
        part = available[pair_index % len(available)]
        fallback = pair_index >= len(available)
        pair_index += 1
        original_missing = list(row.get("missing_variables") or [])
        source = _size_source_of(row)
        source["dwg_binding"] = {
            "component_id": _text(part.get("component_id")),
            "part_code": _text(part.get("part_code")),
            "rule_id": BINDING_RULE_ID,
            "fallback_paired": bool(fallback),
            "original_missing_variables": original_missing,
        }
        row["length_mm"] = _num(part.get("unfolded_length_mm"))
        row["width_mm"] = _num(part.get("unfolded_width_mm"))
        row["size_source"] = source
        row["size_source_json"] = copy.deepcopy(source)
        row["missing_variables"] = []
        row["source"] = "dwg_parts"
        row["status"] = "computed"
        bound += 1
    return {"items": rows, "bound": bound, "unbound": unbound,
            "skipped_locked": skipped_locked, "gaps": list(unbound),
            "rule_id": BINDING_RULE_ID, "engine_version": ENGINE_VERSION}
