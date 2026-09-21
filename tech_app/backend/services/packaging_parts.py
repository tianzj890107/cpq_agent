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

#: 单件下游结论（工艺 / 成本）各自一份版本化文档 —— 与零件文档同一套 `get_doc/put_doc`
#: 范式，但**分开存**：它们是不同的产物，读回的时机与权限都不同
#: （Spec `packaging-parts-downstream-readback.md` §2.1 命名契约）。
DOC_KEY_PROCESS = "packaging_part_process"
DOC_KEY_COST = "packaging_part_cost"

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

#: 材料分类的关键词闭集（Spec 批 12 §2.5）：**闭集外一律 None** —— 未知不等于不匹配。
#: 顺序即判据顺序：先磁铁系再五金系（否则「钕铁硼磁铁」会被「铁」判成五金），
#: 纸系在丝带/布/绒之前（「海绵裱绒」按闭集顺序落到丝带/布/绒）。
MATERIAL_CLASS_KEYWORDS = (
    ("paper", ("纸", "板", "卡", "坑", "牛皮")),
    ("magnet", ("磁铁", "钕铁硼", "磁石")),
    ("metal", ("五金", "铁", "铝")),
    ("textile", ("丝带", "织带", "布", "绒")),
    ("plastic", ("EVA", "海绵", "PET", "PVC", "塑料")),
)


def _material_class(value: Any) -> Optional[str]:
    """材料原文 → 闭集里的类别；闭集外一律 `None`（Spec 批 12 §3.4：未知 ≠ 不同类）。"""
    text = _text(value).upper()
    if not text:
        return None
    for name, keywords in MATERIAL_CLASS_KEYWORDS:
        for keyword in keywords:
            if keyword.upper() in text:
                return name
    return None

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

# —— 重复边折叠与外轮廓重判（Spec `packaging-parts-outline-chaining.md` §2）——
#: 真刀模图里同一条边常被重复画 2～4 份。环搜索把这些重复边当成**不同的边**，分支爆炸后撞上
#: 预算提前中止，却对外报"图纸没闭合"——把"我们没算完"说成了"图纸的结论"。
#: 折叠只作用于**找环**：证据（entity_ids）仍然逐条保留。
CHAIN_RULE_ID = "part_outline_chaining_v1"
EDGE_COLLAPSE_TOLERANCE_MM = 1.0

#: rescue 准入条件：折叠后最大环的 bbox 必须覆盖分量 bbox 的这个比例才算外轮廓。
#: 依据：已验收的闭合件里有 12 件的环只覆盖分量 bbox 的 49%～95%（那是内圈/局部环），
#: 所以覆盖率只能当"救判错的件"的准入条件，不能反过来重算已闭合件。
OUTLINE_BBOX_COVER_RATIO = 0.95

#: 开线原因闭集（顺序即判定顺序，Spec §2.4）；笼统的"没找到闭合环"从代码里消失。
OUTLINE_OPEN_REASONS = ("no_curve_entity", "unit_unconfirmed", "loop_budget_exhausted",
                        "odd_endpoints", "loop_too_small")


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
                 max_states: int = MAX_LOOP_STATES
                 ) -> Tuple[List[Dict[str, Any]], bool]:
    """找简单环：顶点不重复、边不重复、首尾相接（Spec §3 第 3 步）。

    起点固定为环里最小的节点（neighbour < start 剪枝），同一个环只报一次。
    预算是硬上限：到点就停，但结果仍确定（同一份 IR 两次跑一模一样）。
    返回 `(loops, exhausted)`：`exhausted` 表示搜索**撞上预算提前中止**（结论不完整），
    调用方据此说"我们没算完"，而不是把中止当成"没有环"（Spec §2.4 序 3）。
    """
    loops: List[Dict[str, Any]] = []
    states = [0]
    exhausted = [False]
    # 环只可能活在 2-core 里（真图多数分量是长开放链）：先剥掉度 < 2 的顶点，省掉无用搜索。
    # 这一步只改**速度**：度 < 2 的顶点不可能落在任何简单环上，结果逐字不变。
    adjacency = _two_core(adjacency)

    def walk(start: Any, node: Any, path_nodes: List[Any], path_edges: List[int],
             used: set) -> None:
        for neighbour, edge in adjacency.get(node) or ():
            states[0] += 1
            if states[0] >= max_states or len(loops) >= max_cycles:
                exhausted[0] = True
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
            exhausted[0] = True
            break
        walk(start, start, [start], [], {start})
    return loops, exhausted[0]


def _adjacency_of(edges: List[Tuple[Any, Any, str, str]]
                  ) -> Dict[Any, List[Tuple[Any, int]]]:
    """边表 → 邻接表（边序号即 edges 里的下标）。"""
    adjacency: Dict[Any, List[Tuple[Any, int]]] = {}
    for index, (first, second, _entity_id, _approximation) in enumerate(edges):
        adjacency.setdefault(first, []).append((second, index))
        adjacency.setdefault(second, []).append((first, index))
    return adjacency


def _edge_key(first: Any, second: Any) -> Tuple[Any, Any]:
    """边的无向键（方向无关，量化端点已归并）。"""
    return (first, second) if first <= second else (second, first)


def _collapse_edges(edges: List[Tuple[Any, Any, str, str]]
                    ) -> List[Tuple[Any, Any, str, str]]:
    """同一对量化端点之间的重复边折成一条（代表边 = entity_id 最小那条，Spec §2.1）。

    折叠只影响**找环**：`outline.entity_ids` 仍列该件里全部相关实体，证据可回查。
    输出按边键排序 —— 同一份 IR 两次跑逐字相同。
    """
    best: Dict[Tuple[Any, Any], Tuple[Any, Any, str, str]] = {}
    for edge in edges:
        key = _edge_key(edge[0], edge[1])
        current = best.get(key)
        if current is None or edge[2] < current[2]:
            best[key] = edge
    return [best[key] for key in sorted(best)]


def _nearest_gap_mm(keys: List[Any], vertices: Dict[Any, Tuple[float, float]]) -> float:
    """奇度顶点两两**最近的配对**后，取配对间隙的最大值（没有奇度顶点 → 0.0）。

    按坐标排序后取相邻配对 —— 真图上一个分量可能有上千个奇度顶点（开放链的刀口），
    逐对求最近是 O(n^3)，会把整条链路拖死；排序配对是 O(n log n) 且结果确定。
    """
    pending = sorted((float(vertices[key][0]), float(vertices[key][1])) for key in keys
                     if key in vertices)
    gaps = [cad_geometry.distance(pending[index], pending[index + 1])
            for index in range(0, len(pending) - 1, 2)]
    return max(gaps) if gaps else 0.0


def _outline_evidence(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """一次算好三件事（Spec §2.1/§2.3）：未折叠图、折叠图、逐件诊断。

    未折叠图是**今天的口径**：已经判成 closed 的件必须逐字保持它的结果，所以两条路都要算
    （只有存在重复边时才真的各跑一次搜索；没有重复边时折叠图 == 未折叠图，复用同一个结果）。
    """
    edges, vertices = _component_edges(members)
    unique = _collapse_edges(edges)
    duplicated = len(unique) != len(edges)
    if unique:
        loops_collapsed, exhausted_collapsed = _find_cycles(_adjacency_of(unique))
    else:
        loops_collapsed, exhausted_collapsed = [], False
    if duplicated:
        loops_original, _exhausted_original = _find_cycles(_adjacency_of(edges))
    else:
        loops_original = loops_collapsed
    degree: Dict[Any, int] = {}
    for first, second, _entity_id, _approximation in unique:
        degree[first] = degree.get(first, 0) + 1
        degree[second] = degree.get(second, 0) + 1
    odd = [key for key in sorted(degree) if degree[key] % 2]
    # 预算中止**且一个环都没找到**才算"我们没算完"：找到环的件，中止不影响结论。
    budget_exhausted = bool(exhausted_collapsed) and not loops_collapsed
    diagnosis = {
        "edges_total": len(edges),
        "edges_unique": len(unique),
        "collapsed_total": len(edges) - len(unique),
        "cycles_found": len(loops_collapsed),
        "budget_exhausted": budget_exhausted,
        "odd_degree_vertices": len(odd),
        "nearest_gap_mm": _round(_nearest_gap_mm(odd, vertices)),
    }
    return {"edges": edges, "unique": unique, "vertices": vertices,
            "loops_original": loops_original, "loops_collapsed": loops_collapsed,
            "diagnosis": diagnosis}


def outline_diagnosis(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """逐件诊断（Spec §2.3）：重复边 / 环数 / 预算中止 / 奇度顶点 / 最近配对间隙。

    统计口径与 `_component_edges` 一致（端点按 `LOOP_TOLERANCE_MM` 量化）；纯函数。
    """
    return _outline_evidence(list(members or []))["diagnosis"]


def _largest_loop(loops: List[Dict[str, Any]], edges: List[Tuple[Any, Any, str, str]],
                  vertices: Dict[Any, Tuple[float, float]], min_area: float
                  ) -> Dict[str, Any]:
    """给定额度内的环集合 → 最大闭合环（`{outline, loop, saw_loop, has_coordinates}`）。

    saw_loop 表示确实找到了环（哪怕面积被门槛挡掉）；has_coordinates 表示这件里至少有
    实体给出了可用坐标 —— 拿不到坐标时分量只能沿用 DWG 自己的包围盒（Spec §3）。
    """
    if not edges:
        return {"outline": None, "loop": None, "saw_loop": False, "has_coordinates": False}
    best: Optional[Dict[str, Any]] = None
    best_loop: Optional[Dict[str, Any]] = None
    best_area = 0.0
    saw_loop = False
    for loop in loops:
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
        best, best_loop, best_area = outline, loop, area
    return {"outline": best, "loop": best_loop, "saw_loop": saw_loop, "has_coordinates": True}


def _bbox_cover(loop_box: Optional[List[float]], component_box: Optional[List[float]],
                tolerance: float = LOOP_TOLERANCE_MM) -> float:
    """环 bbox 覆盖分量 bbox 的比例（每一边按 `tolerance` 容差；Spec §2.2）。

    两个方向各算一个比例（缺多少 / 分量跨度），取较小者 —— 只有**四边都盖住**才是外轮廓。
    """
    if not loop_box or not component_box:
        return 0.0
    width = abs(float(component_box[2]) - float(component_box[0]))
    height = abs(float(component_box[3]) - float(component_box[1]))
    if width <= 0 or height <= 0:
        return 1.0

    def axis_ratio(loop_low: float, loop_high: float, low: float, high: float, span: float) -> float:
        missing = max(0.0, loop_low - low - tolerance) + max(0.0, high - loop_high - tolerance)
        return max(0.0, min(1.0, 1.0 - missing / span))

    return min(axis_ratio(float(loop_box[0]), float(loop_box[2]),
                          float(component_box[0]), float(component_box[2]), width),
               axis_ratio(float(loop_box[1]), float(loop_box[3]),
                          float(component_box[1]), float(component_box[3]), height))


def _rescue_outline(evidence: Dict[str, Any], component_box: Optional[List[float]],
                    min_area: float) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """外轮廓重判（rescue）：折叠图上重找环 → `(outline, compose)`；不满足就 `(None, None)`。

    两个条件缺一不可（Spec §2.2）：① 折叠前判成 open（调用方保证）；② 最大环 bbox 覆盖
    分量 bbox >= `OUTLINE_BBOX_COVER_RATIO`。`compose` 只在 rescue 成功的件上出现 ——
    便于核对"到底动了谁"。
    """
    found = _largest_loop(evidence["loops_collapsed"], evidence["unique"],
                          evidence["vertices"], min_area)
    outline = found["outline"]
    if outline is None or not component_box:
        return None, None
    cover = _bbox_cover(outline.get("bbox"), component_box)
    if cover < OUTLINE_BBOX_COVER_RATIO:
        return None, None
    pairs = {_edge_key(evidence["unique"][index][0], evidence["unique"][index][1])
             for index in (found["loop"] or {}).get("edges") or []}
    merged = dict(outline)
    # 折叠只作用于找环：证据（含被折叠掉的重复实体）仍然逐条列出来回查（Spec §2.1）。
    merged["entity_ids"] = sorted({edge[2] for edge in evidence["edges"]
                                   if _edge_key(edge[0], edge[1]) in pairs})
    compose = {
        "kind": "collapsed_cycle",
        "rule_id": CHAIN_RULE_ID,
        "edges_total": len(evidence["edges"]),
        "edges_unique": len(evidence["unique"]),
        "collapsed_total": len(evidence["edges"]) - len(evidence["unique"]),
        "bbox_cover": _round(cover),
    }
    return merged, compose


def _open_outline_reason(*, has_curve: bool, diagnosis: Dict[str, Any],
                         saw_loop: bool) -> str:
    """判成 open 的具体原因（闭集，按 Spec §2.4 的顺序判定）。"""
    if not has_curve:
        return "no_curve_entity"
    if diagnosis.get("budget_exhausted"):
        return "loop_budget_exhausted"
    if int(diagnosis.get("odd_degree_vertices") or 0) > 0 \
            and float(diagnosis.get("nearest_gap_mm") or 0.0) > LOOP_TOLERANCE_MM:
        return "odd_endpoints"
    if saw_loop or int(diagnosis.get("cycles_found") or 0) > 0:
        return "loop_too_small"
    # 有边却既无环也无奇度顶点在图论上不存在；真到了这里宁可说"断口"也不许笼统。
    return "odd_endpoints"


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
        # —— 重复边折叠 + 外轮廓重判（Spec `packaging-parts-outline-chaining.md` §2）——
        # 先按**今天的口径**（未折叠图）判一次：已经判成 closed 的件必须逐字保持原样；
        # 只有判成 open 的件才走 rescue（折叠图重找环 + 外轮廓覆盖率准入）。
        evidence = _outline_evidence(members)
        outline: Optional[Dict[str, Any]] = None
        compose: Optional[Dict[str, Any]] = None
        saw_loop = False
        has_coordinates = False
        if unit_ok:
            verdict = _largest_loop(evidence["loops_original"], evidence["edges"],
                                    evidence["vertices"], float(config["min_area_mm2"]))
            outline = verdict["outline"]
            saw_loop = verdict["saw_loop"]
            has_coordinates = verdict["has_coordinates"]
            if outline is None:
                outline, compose = _rescue_outline(evidence, bbox,
                                                   float(config["min_area_mm2"]))
                if outline is not None:
                    saw_loop = True
                    has_coordinates = True
        if not unit_ok:
            outline_status, outline_reason = "unavailable", "unit_unconfirmed"
            size_source = "dwg_outline"
            length = width = None
            area = 0.0
        elif outline is not None:
            outline_status, outline_reason, size_source = "closed", "", "closed_outline"
            if compose:
                outline = dict(outline)
                outline["compose"] = compose
            loop_box = outline.get("bbox") or bbox
            length = abs(loop_box[2] - loop_box[0])
            width = abs(loop_box[3] - loop_box[1])
            area = float(outline.get("area_mm2") or 0.0)
        else:
            # 求不出环 → 退回分量包围盒**并留痕**（绝不许把包围盒说成轮廓尺寸）。
            outline_status = "open"
            outline_reason = _open_outline_reason(
                has_curve=bool(curves), diagnosis=evidence["diagnosis"], saw_loop=saw_loop)
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
            "outline_diagnosis": evidence["diagnosis"],
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
            "outline_diagnosis": row["outline_diagnosis"],
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
    # 重复边折叠与外轮廓重判的账（Spec `packaging-parts-outline-chaining.md` §3）：
    # 「闭合判定」这一层到底做了多少事，必须由零件文档自己说清楚，而不是靠页面猜。
    collapsed_edge_total = sum(int((row.get("outline_diagnosis") or {}).get("collapsed_total") or 0)
                               for row in parts)
    collapsed_rescue_total = sum(1 for row in parts
                                 if (row.get("outline") or {}).get("compose"))
    budget_exhausted_total = sum(1 for row in parts
                                 if (row.get("outline_diagnosis") or {}).get("budget_exhausted"))
    open_reason_mix: Dict[str, int] = {}
    for row in parts:
        if row["outline_status"] != "open":
            continue
        reason = _text(row.get("outline_reason"))
        open_reason_mix[reason] = open_reason_mix.get(reason, 0) + 1

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
                  "closed_ratio": _round(closed_ratio),
                  "collapsed_edge_total": collapsed_edge_total,
                  "collapsed_rescue_total": collapsed_rescue_total,
                  "budget_exhausted_total": budget_exhausted_total,
                  "open_reason_mix": open_reason_mix},
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
    # 闭合判定的账（Spec `packaging-parts-outline-chaining.md` §3）：一律由零件行现算，
    # 页面/门禁据此说清"这一版到底折叠了多少重复边、救回几件、还剩几件开线、为什么"。
    collapsed_edge_total = 0
    collapsed_rescue_total = 0
    budget_exhausted_total = 0
    open_reason_mix: Dict[str, int] = {}
    for row in rows:
        diagnosis = row.get("outline_diagnosis") if isinstance(row.get("outline_diagnosis"), dict) else {}
        collapsed_total = _num(diagnosis.get("collapsed_total"))
        collapsed_edge_total += max(0, int(collapsed_total or 0))
        if diagnosis.get("budget_exhausted"):
            budget_exhausted_total += 1
        outline = row.get("outline") if isinstance(row.get("outline"), dict) else {}
        if outline.get("compose"):
            collapsed_rescue_total += 1
        if _text(row.get("outline_status")) == "open":
            reason = _text(row.get("outline_reason"))
            open_reason_mix[reason] = open_reason_mix.get(reason, 0) + 1

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
        "collapsed_edge_total": collapsed_edge_total,
        "collapsed_rescue_total": collapsed_rescue_total,
        "budget_exhausted_total": budget_exhausted_total,
        "open_reason_mix": open_reason_mix,
        "open_total": sum(1 for row in rows if _text(row.get("outline_status")) == "open"),
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
                           % (_text(payload.get("outline_reason")) or "odd_endpoints"),
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


def _part_doc_items(project_id: str, key: str) -> List[Dict[str, Any]]:
    doc = get_backend().get_doc(project_id, key) or {}
    items = doc.get("items") if isinstance(doc, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def _record_hash(payload: Dict[str, Any]) -> str:
    from .packaging_semantics import model as sem_model

    return sem_model.sha256_hex(sem_model.canonical_json(sem_model.json_safe(payload)))


def _load_part_doc(project_id: str, key: str, part_code: str) -> Dict[str, Any]:
    """读这一件的**最近一版**结论；没跑过就回空文档（不抛错、不 404，Spec §2.1）。"""
    wanted = _text(part_code)
    for item in _part_doc_items(project_id, key):
        if _text(item.get("part_code")) == wanted:
            return item
    return {}


def _save_part_doc(project_id: str, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """落一版单件结论：同一 `(part_code, parts_id, 结论内容)` **幂等**、最多 20 版。

    幂等靠内容指纹（`record_hash`）：同一份结论重复落库**不写库、不新增版本** ——
    34 上"刷新就没了"的另一面就是"重跑一次就多一版"，两边都得治。
    """
    record = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    part_code = _text(record.get("part_code"))
    parts_id = _text(record.get("parts_id"))
    if not part_code:
        raise ValueError("save_part_process()/save_part_cost() 需要 part_code")
    body = {name: value for name, value in record.items() if name != "record_hash"}
    record["record_hash"] = _record_hash(body)
    items = _part_doc_items(project_id, key)
    head = items[0] if items else None
    if (isinstance(head, dict) and _text(head.get("part_code")) == part_code
            and _text(head.get("parts_id")) == parts_id
            and _text(head.get("record_hash")) == record["record_hash"]):
        return head
    kept = [item for item in items
            if not (_text(item.get("part_code")) == part_code
                    and _text(item.get("parts_id")) == parts_id)]
    kept.insert(0, record)
    get_backend().put_doc(project_id, key, {"items": kept[:MAX_VERSIONS]})
    return record


def save_part_process(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """落一版单件工艺结论（Spec §2.1）。结论**不写技术 IR**。"""
    return _save_part_doc(project_id, DOC_KEY_PROCESS, payload)


def load_part_process(project_id: str, part_code: str) -> Dict[str, Any]:
    """读回单件工艺结论（最近一版）；没跑过 → `{}`。"""
    return _load_part_doc(project_id, DOC_KEY_PROCESS, part_code)


def save_part_cost(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """落一版单件成本结论（Spec §2.1）。"""
    return _save_part_doc(project_id, DOC_KEY_COST, payload)


def load_part_cost(project_id: str, part_code: str) -> Dict[str, Any]:
    """读回单件成本结论（最近一版）；没跑过 → `{}`。"""
    return _load_part_doc(project_id, DOC_KEY_COST, part_code)


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
      fallback_paired / original_missing_variables / pairing_basis / material_match）、
      `source="dwg_parts"`、`missing_variables` 清空；
    - **披露配对**（Spec 批 12 §3.4，加法不改拒绝口径）：每行留一句 `pairing_basis`；
      两类材料都已知且不同类时 `material_match=false` 并进 `pairing_review`
      （34 实测把 `RB02001-P08` 磁铁配到纸面板上，报告里原先没有任何地方看得出来）。
      `bound` / `unbound` / `gaps` 口径逐字不变 —— 正式对应表要业务签字后另立一批。
    """
    rows = [copy.deepcopy(row) for row in (items or []) if isinstance(row, dict)]
    available = [row for row in ((parts or {}).get("parts") or [])
                 if isinstance(row, dict)
                 and _num(row.get("unfolded_length_mm")) is not None
                 and _num(row.get("unfolded_width_mm")) is not None]
    bound = 0
    skipped_locked = 0
    unbound: List[str] = []
    pairing_review: List[Dict[str, Any]] = []
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
        order = (pair_index % len(available)) + 1
        fallback = pair_index >= len(available)
        pair_index += 1
        original_missing = list(row.get("missing_variables") or [])
        # 配对是纯位置的（行顺序 ↔ 面积降序），所以必须把"这一对是怎么配上的"写在行上，
        # 再对材料明显不同类的组合单列出来 —— 披露，不是拒绝（Spec 批 12 §3.4）。
        basis = ("位置配对：第 %d 个待绑行 ↔ 面积第 %d 大的零件%s"
                 % (pair_index, order, "（零件少于行，循环取件）" if fallback else ""))
        row_class = _material_class(row.get("material"))
        part_class = _material_class(part.get("material"))
        material_match: Optional[bool] = None
        if row_class is not None and part_class is not None:
            material_match = row_class == part_class
        source = _size_source_of(row)
        source["dwg_binding"] = {
            "component_id": _text(part.get("component_id")),
            "part_code": _text(part.get("part_code")),
            "rule_id": BINDING_RULE_ID,
            "fallback_paired": bool(fallback),
            "original_missing_variables": original_missing,
            "pairing_basis": basis,
            "material_match": material_match,
        }
        if material_match is False:
            pairing_review.append({
                "item_key": item_key,
                "part_code": _text(part.get("part_code")),
                "row_material": _text(row.get("material")),
                "part_material": _text(part.get("material")),
                "material_match": False,
            })
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
            "pairing_review": pairing_review,
            "rule_id": BINDING_RULE_ID, "engine_version": ENGINE_VERSION}
