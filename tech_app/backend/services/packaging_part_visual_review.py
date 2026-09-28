"""Evidence-gated visual review of DWG business-part/geometry associations.

The model sees only CAD-derived candidates and a raster made from the same CAD IR.
It may reject or suggest a candidate; it cannot create geometry or dimensions.
"""
from __future__ import annotations

from io import BytesIO
import math
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, model_validator


class _Decision(BaseModel):
    selected_candidate_id: str
    insufficient_evidence: bool
    reason: str

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_keys(cls, value: Any) -> Any:
        """Accept a provider's `decision: none` without trusting extra claims."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "selected_candidate_id" not in data:
            choice = str(data.get("decision") or data.get("choice") or "none").strip()
            data["selected_candidate_id"] = (
                "none" if choice.lower() in ("none", "abstain", "reject", "unknown")
                else choice)
        if "insufficient_evidence" not in data:
            # Missing sufficiency is never interpreted as positive evidence.
            data["insufficient_evidence"] = True
        data.setdefault("reason", str(data.get("explanation") or ""))
        return data


def _point(value: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        point = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(number) for number in point) else None


def _bbox(value: Any) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        box = tuple(float(number) for number in value[:4])
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(number) for number in box):
        return None
    x0, y0, x1, y1 = box
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _center(box: Tuple[float, float, float, float]) -> Tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _spatial_dimension(region: Dict[str, Any], rects: Any) -> bool:
    """Equal numbers elsewhere on a sheet are not a dimension of this candidate."""
    box = _bbox(region.get("bbox"))
    if box is None:
        return False
    sides = sorted((box[2] - box[0], box[3] - box[1]))
    for rect in rects or []:
        if not isinstance(rect, dict):
            continue
        point = _point(rect.get("center"))
        if point is None:
            continue
        try:
            dimensions = sorted((float(rect.get("length_mm")), float(rect.get("width_mm"))))
        except (TypeError, ValueError):
            continue
        if any(abs(a - b) > 2.0 for a, b in zip(sides, dimensions)):
            continue
        # A nearby, same-sized annotation is supporting evidence, but not a
        # model-generated measurement. It still needs the name/shape relation.
        margin = max(5.0, min(sides) * 0.1)
        if box[0] - margin <= point[0] <= box[2] + margin \
                and box[1] - margin <= point[1] <= box[3] + margin:
            return True
    return False


def _anchor_in_region(anchor: Tuple[float, float], region: Dict[str, Any]) -> bool:
    box = _bbox(region.get("bbox"))
    if box is None:
        return False
    margin = max(3.0, min(box[2] - box[0], box[3] - box[1]) * 0.05)
    return (box[0] - margin <= anchor[0] <= box[2] + margin
            and box[1] - margin <= anchor[1] <= box[3] + margin)


def _candidates(anchor: Tuple[float, float], regions: Any,
                current_id: str, rects: Any,
                groups: Any = None) -> List[Dict[str, Any]]:
    """这个名字锚点给出的候选表（单片 + 有证据的**整件**候选，Spec §2）。

    发给模型的永远是这一张表：只给最近几个单片的话，模型不可能选出"由两片互补刀线
    拼成的一件"（那正是它要判的事）。
    """
    records: List[Dict[str, Any]] = []
    for region in regions or []:
        if not isinstance(region, dict) or not region.get("substantial"):
            continue
        box = _bbox(region.get("bbox"))
        region_id = str(region.get("region_id") or "")
        if box is None or not region_id:
            continue
        records.append({
            "id": region_id, "candidate_id": region_id, "region_id": region_id,
            "component_ids": list(region.get("component_ids") or []),
            "entity_ids": list(region.get("entity_ids") or []),
            "bbox": list(box), "layers": list(region.get("layers") or []),
            "member_total": 1, "evidence_reasons": [], "geometry_status": "supported",
        })
    records.sort(key=lambda row: (_distance(anchor, _center(_bbox(row["bbox"]))), row["id"]))
    whole = [dict(row, id=str(row.get("candidate_id") or "")) for row in (groups or [])]
    whole.sort(key=lambda row: row["id"])
    chosen_groups = [row for row in whole if current_id and current_id in (row.get("region_ids") or [])]
    current = [row for row in records if row["id"] == current_id]
    ordered = (chosen_groups + current
               + [row for row in records if row["id"] != current_id]
               + [row for row in whole if row not in chosen_groups])
    out: List[Dict[str, Any]] = []
    for record in ordered[:8]:
        box = _bbox(record.get("bbox"))
        if box is None:
            continue
        out.append({
            "id": str(record.get("id") or record.get("candidate_id") or ""),
            "candidate_id": str(record.get("candidate_id") or record.get("id") or ""),
            "component_ids": list(record.get("component_ids") or []),
            "entity_ids": list(record.get("entity_ids") or []),
            "bbox": list(box),
            "layers": list(record.get("layers") or []),
            "distance_mm": round(_distance(anchor, _center(box)), 3),
            "dimension_spatial": _spatial_dimension(record, rects),
            "anchor_in_region": _anchor_in_region(anchor, record),
            "member_total": int(record.get("member_total") or 1),
            "evidence_reasons": list(record.get("evidence_reasons") or []),
            "geometry_status": str(record.get("geometry_status") or ""),
            "view_id": str(record.get("view_id") or ""),
        })
    return out


def _color(entity: Dict[str, Any]) -> str:
    layer = str(entity.get("layer") or "").upper()
    aci = entity.get("aci_color")
    if "V槽" in layer or "V-SLOT" in layer or "VSLOT" in layer or aci == 6:
        return "#d946ef"
    if "HALF" in layer or "PARTIAL" in layer or aci == 5:
        return "#1f6feb"
    if "CREASE" in layer or "压" in layer or aci == 3:
        return "#16a34a"
    if "CUT" in layer or "刀" in layer or aci == 1:
        return "#dc2626"
    if "SAMPLE" in layer or "REF" in layer or aci == 2:
        return "#d29922"
    return "#475569"


def _entity_points(entity: Dict[str, Any]) -> List[Tuple[float, float]]:
    attr = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
    kind = str(entity.get("kind") or "").lower()
    if kind == "line":
        points = [_point(attr.get("start")), _point(attr.get("end"))]
        return [point for point in points if point is not None]
    if kind in ("polyline", "spline"):
        source = attr.get("points") or attr.get("fit_points") or []
        return [point for item in source if (point := _point(item)) is not None]
    if kind in ("arc", "circle"):
        center = _point(attr.get("center"))
        try:
            radius = float(attr.get("radius"))
            start = float(attr.get("start_angle", 0))
            end = float(attr.get("end_angle", 360))
        except (TypeError, ValueError):
            return []
        if center is None or radius <= 0:
            return []
        if end <= start:
            end += 360
        return [(center[0] + radius * math.cos(math.radians(start + (end - start) * i / 24)),
                 center[1] + radius * math.sin(math.radians(start + (end - start) * i / 24)))
                for i in range(25)]
    return []


def _draw_panel(draw: Any, ir_entities: Dict[str, Dict[str, Any]],
                candidate: Dict[str, Any], rectangle: Tuple[int, int, int, int]) -> None:
    box = _bbox(candidate.get("bbox"))
    if box is None:
        return
    left, top, right, bottom = rectangle
    draw.rectangle(rectangle, outline="#cbd5e1", width=2)
    width, height = max(box[2] - box[0], 1.0), max(box[3] - box[1], 1.0)
    padding = 20
    scale = min((right - left - padding * 2) / width,
                (bottom - top - padding * 2) / height)
    center = _center(box)
    px, py = (left + right) / 2, (top + bottom) / 2

    def project(point: Tuple[float, float]) -> Tuple[int, int]:
        return round(px + (point[0] - center[0]) * scale), round(py - (point[1] - center[1]) * scale)

    for entity_id in candidate.get("entity_ids") or []:
        entity = ir_entities.get(str(entity_id))
        if not entity:
            continue
        points = _entity_points(entity)
        if len(points) >= 2:
            pixels = [project(point) for point in points]
            if entity.get("closed") and len(pixels) > 2:
                pixels.append(pixels[0])
            draw.line(pixels, fill=_color(entity), width=2)
    draw.text((left + 8, top + 7), str(candidate.get("id") or "")[:28], fill="#0f172a")


def _draw_overview(draw: Any, ir_entities: Dict[str, Dict[str, Any]],
                   anchor: Tuple[float, float], candidates: List[Dict[str, Any]],
                   rectangle: Tuple[int, int, int, int], texts: Any = None,
                   target_name: str = "", label_font: Any = None) -> None:
    """Show where the name anchor and all candidate views sit on the sheet."""
    boxes = [_bbox(item.get("bbox")) for item in candidates]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return
    left, top, right, bottom = rectangle
    draw.rectangle(rectangle, outline="#cbd5e1", width=2)
    x0 = min([anchor[0]] + [box[0] for box in boxes])
    y0 = min([anchor[1]] + [box[1] for box in boxes])
    x1 = max([anchor[0]] + [box[2] for box in boxes])
    y1 = max([anchor[1]] + [box[3] for box in boxes])
    width, height = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    scale = min((right - left - 36) / width, (bottom - top - 36) / height)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    px, py = (left + right) / 2, (top + bottom) / 2

    def project(point: Tuple[float, float]) -> Tuple[int, int]:
        return round(px + (point[0] - cx) * scale), round(py - (point[1] - cy) * scale)

    for entity in ir_entities.values():
        box = _bbox(entity.get("bbox"))
        if box is None or box[2] < x0 or box[0] > x1 or box[3] < y0 or box[1] > y1:
            continue
        points = _entity_points(entity)
        if len(points) >= 2:
            draw.line([project(point) for point in points], fill="#d1d5db", width=1)
    for index, candidate in enumerate(candidates, 1):
        box = _bbox(candidate.get("bbox"))
        if box is None:
            continue
        a, b = project((box[0], box[1])), project((box[2], box[3]))
        frame = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        draw.rectangle(frame, outline="#2563eb", width=2)
        draw.text((frame[0] + 2, frame[1] + 2), str(index), fill="#1d4ed8")
    nearby = []
    for item in texts or []:
        if not isinstance(item, dict):
            continue
        point = _point(item.get("position"))
        raw = str(item.get("normalized_text") or item.get("raw_text") or "")
        if point is None or not raw or not (x0 <= point[0] <= x1 and y0 <= point[1] <= y1):
            continue
        # Only labels from the drawing; no workbook or reference answer.
        label = raw.replace("\\P", " ").replace("\n", " ").strip()[:18]
        nearby.append((_distance(point, anchor), point, label))
    for _distance_mm, point, label in sorted(nearby, key=lambda row: row[0])[:14]:
        x, y = project(point)
        try:
            draw.text((x + 3, y + 2), label, fill="#64748b", font=label_font)
        except UnicodeError:
            pass
    x, y = project(anchor)
    draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#dc2626")
    try:
        draw.text((x + 6, y - 12), "A " + target_name[:12], fill="#dc2626",
                  font=label_font)
    except UnicodeError:
        draw.text((x + 6, y - 12), "A", fill="#dc2626")
    draw.text((left + 8, top + 7), "Overview: A=name anchor, 1-8=candidates", fill="#0f172a")


# --------------------------------------------------------------------------- #
# 整件视图候选（Spec `packaging-whole-part-candidate-geometry-first.md` §2）
#
# 连通分量是**底层图元组**，不是业务件：一件可以由多片互不相连的刀线组成。这里先把
# 有 CAD 证据的组法算出来（纯几何、可回查、对平移/遍历顺序/改名等变），再交给模型判语义。
# --------------------------------------------------------------------------- #

#: 刀线互补判据：两片的**悬空端点**相距 ≤ 这个值，**且并起来悬空端点必须变少**。
#: 只有"接得上、接上以后更像一条完整轮廓"的两片才算一件（防止沿邻近关系一路连成一片）。
DANGLING_JOIN_TOLERANCE_MM = 1.0
#: 视图/排版格判据：两块矩形在两个轴上的间隙都不超过这个值 → 同一格。
VIEW_GAP_MM = 50.0
#: 一件最多由多少片拼成；超过这个数说明不是"一件的碎片"，不当整件候选发出去。
MAX_GROUP_MEMBERS = 24
#: 组的证据原因闭集（写进 `evidence_reasons`）。
GROUP_REASON_COMPLEMENTARY = "cut_lines_complementary"
GROUP_REASON_SAME_VIEW = "same_view"
GROUP_REASON_SAME_BOUNDARY = "same_boundary_role"
#: 图层 → 边界角色的兜底提示（IR 自带 `role` 时以 IR 为准）。
_ROLE_HINTS = (("v_groove", ("V槽", "V-SLOT", "VSLOT", "V-GROOVE", "VGROOVE")),
               ("partial_cut", ("HALF", "半穿", "半断", "半切", "SEMI")),
               ("crease", ("CREASE", "压线", "压痕", "折线", "折痕")),
               ("cut", ("CUT", "全穿", "刀线", "刀模", "轮廓", "OUTLINE")))
SUGGESTION_STATUSES = ("needs_confirmation", "rejected")
#: 模型可以说的文字类别；图例/标题栏不是件名，材料文字也不是。
SUGGESTION_TEXT_CLASSES = ("name", "material", "unknown")
#: 模型**不许**碰的 CAD 事实（Spec §4 第 1 条）——出现任何一个就整条拒收。
FORBIDDEN_SUGGESTION_KEYS = ("entity_ids", "component_ids", "component_id", "region_id", "bbox",
                             "length_mm", "width_mm", "size_confirmed", "size_source", "bound",
                             "geometry_binding", "outline", "polygon", "area_mm2", "status",
                             "reference", "candidates")
MERGE_ACTIONS = ("merge", "combine", "group", "join")


def _layer_role(ir_layers: Any, layer_name: Any) -> str:
    name = str(layer_name or "").strip().upper()
    if not name:
        return "unknown"
    for row in ir_layers or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("name") or "").strip().upper() == name:
            role = str(row.get("role") or "").strip()
            if role:
                return role
    for role, hints in _ROLE_HINTS:
        if any(hint.upper() in name for hint in hints):
            return role
    return "unknown"


def _boundary_role(ir: Dict[str, Any], region: Dict[str, Any]) -> str:
    roles: List[str] = []
    for name in (region.get("layers") or []):
        role = _layer_role(ir.get("layers"), name)
        if role != "unknown" and role not in roles:
            roles.append(role)
    if not roles:
        return "unknown"
    return roles[0] if len(roles) == 1 else "mixed"


def _entity_vertices(entity: Dict[str, Any]) -> List[Tuple[float, float]]:
    """一条图元的顶点序列（LINE 的 start/end、折线/样条的坐标序列）。"""
    attrs = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
    for key in ("points", "fit_points"):
        values = attrs.get(key)
        if isinstance(values, list) and values:
            points = [point for point in (_point(value) for value in values)
                      if point is not None]
            if len(points) >= 2:
                return points
    return [point for point in (_point(attrs.get("start")), _point(attrs.get("end")))
            if point is not None]


def _region_entities(entities_by_id: Dict[str, Dict[str, Any]], regions: Any
                     ) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for region in regions or []:
        for entity_id in (region.get("entity_ids") or []):
            entity = entities_by_id.get(str(entity_id))
            if entity is not None:
                out.append(entity)
    return out


def _endpoint_census(entities: Any) -> List[List[Any]]:
    """图元端点 → 出现次数；同一位置（≤ 容差）的端点合并计数。"""
    census: List[List[Any]] = []
    for entity in entities or []:
        if str(entity.get("kind") or "").lower() in ("circle",) or entity.get("closed") is True:
            continue
        points = _entity_vertices(entity)
        if len(points) < 2:
            continue
        for endpoint in (points[0], points[-1]):
            for entry in census:
                if _distance(entry[0], endpoint) <= DANGLING_JOIN_TOLERANCE_MM:
                    entry[1] += 1
                    break
            else:
                census.append([endpoint, 1])
    return census


def _dangling_points(entities: Any) -> List[Tuple[float, float]]:
    """悬空端点：只被**奇数**条边用到的位置（一条开放刀线的两个头）。

    闭合轮廓（真样本圆盘盒整圈、上面的矩形示例）一个悬空端点都没有 —— 它本身就不需要
    跟谁"接上"，因此也不会被卷进任何整件组。
    """
    return [entry[0] for entry in _endpoint_census(entities) if entry[1] % 2 == 1]


def _join_complementary(left: Any, right: Any,
                        entities_by_id: Dict[str, Dict[str, Any]]) -> bool:
    """两片刀线是否互补：有接得上的悬空端点，且并起来悬空端点**变少**（Spec §2）。"""
    left_dangling = _dangling_points(_region_entities(entities_by_id, left))
    right_dangling = _dangling_points(_region_entities(entities_by_id, right))
    if not left_dangling or not right_dangling:
        return False
    closest = min(_distance(one, other)
                  for one in left_dangling for other in right_dangling)
    if closest > DANGLING_JOIN_TOLERANCE_MM:
        return False
    merged = _dangling_points(_region_entities(entities_by_id, list(left) + list(right)))
    return len(merged) < len(left_dangling) + len(right_dangling)


def _boxes_close(left: Any, right: Any, tolerance: float = VIEW_GAP_MM) -> bool:
    gap_x = max(0.0, left[0] - right[2], right[0] - left[2])
    gap_y = max(0.0, left[1] - right[3], right[1] - left[3])
    return gap_x <= tolerance and gap_y <= tolerance


def _view_ids(regions: Any) -> Dict[str, str]:
    """region_id → `view:N`（同一排版格/视图）。

    判据只有"两块矩形挨得近"：编号按格的几何位置排序，与输入顺序、绝对坐标无关
    （整体平移后编号一一对应），Spec §2 的等变性要求。
    """
    entries: List[Tuple[str, Tuple[float, float, float, float]]] = []
    for region in regions or []:
        if not isinstance(region, dict) or not region.get("substantial"):
            continue
        box = _bbox(region.get("bbox"))
        region_id = str(region.get("region_id") or "")
        if box is None or not region_id:
            continue
        entries.append((region_id, box))
    parent = list(range(len(entries)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for first in range(len(entries)):
        for second in range(first + 1, len(entries)):
            if find(first) == find(second):
                continue
            if _boxes_close(entries[first][1], entries[second][1]):
                one, other = find(first), find(second)
                parent[max(one, other)] = min(one, other)
    clusters: Dict[int, List[Tuple[str, Tuple[float, float, float, float]]]] = {}
    for index, entry in enumerate(entries):
        clusters.setdefault(find(index), []).append(entry)
    ordered = sorted(clusters.values(), key=lambda members: (
        min(box[0] for _rid, box in members), min(box[1] for _rid, box in members),
        min(box[2] for _rid, box in members), min(box[3] for _rid, box in members)))
    out: Dict[str, str] = {}
    for index, members in enumerate(ordered, start=1):
        for region_id, _box in members:
            out[region_id] = "view:%d" % index
    return out


def _group_record(members: List[Dict[str, Any]], ir: Dict[str, Any],
                  views: Dict[str, str]) -> Dict[str, Any]:
    boxes = [_bbox(member.get("bbox")) for member in members]
    boxes = [box for box in boxes if box is not None]
    component_ids = sorted({str(value) for member in members
                            for value in (member.get("component_ids") or []) if str(value)})
    entity_ids = sorted({str(value) for member in members
                         for value in (member.get("entity_ids") or []) if str(value)})
    region_ids = sorted({str(member.get("region_id") or "") for member in members
                         if str(member.get("region_id") or "")})
    roles = [_boundary_role(ir, member) for member in members]
    reasons = [GROUP_REASON_COMPLEMENTARY]
    views_of_members = {views.get(region_id, "") for region_id in region_ids}
    if len(views_of_members) == 1 and "" not in views_of_members:
        reasons.append(GROUP_REASON_SAME_VIEW)
    if len(set(roles)) == 1:
        reasons.append(GROUP_REASON_SAME_BOUNDARY)
    return {
        "candidate_id": "group:" + "|".join(component_ids),
        "region_ids": region_ids,
        "component_ids": component_ids,
        "entity_ids": entity_ids,
        "bbox": [min(box[0] for box in boxes), min(box[1] for box in boxes),
                 max(box[2] for box in boxes), max(box[3] for box in boxes)],
        "boundary_role": roles[0] if len(set(roles)) == 1 else "mixed",
        "layers": sorted({str(value) for member in members
                          for value in (member.get("layers") or []) if str(value)}),
        "member_total": len(members),
        "evidence_reasons": reasons,
        "geometry_status": "supported" if entity_ids else "insufficient",
        "view_id": sorted(views_of_members)[0] if len(views_of_members) == 1 else "",
    }


def _whole_part_groups(ir: Any, regions: Any, views: Any = None) -> List[Dict[str, Any]]:
    """由**互补刀线**拼出来的整件候选（多片，Spec §2 第 1 条）。"""
    doc = ir if isinstance(ir, dict) else {}
    entities_by_id = {str(row.get("entity_id") or ""): row
                      for row in (doc.get("entities") or []) if isinstance(row, dict)}
    rows = [region for region in (regions or [])
            if isinstance(region, dict) and region.get("substantial")
            and _bbox(region.get("bbox")) is not None]
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for first in range(len(rows)):
        for second in range(first + 1, len(rows)):
            if find(first) == find(second):
                continue
            if _join_complementary([rows[first]], [rows[second]], entities_by_id):
                one, other = find(first), find(second)
                parent[max(one, other)] = min(one, other)
    clusters: Dict[int, List[Dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        clusters.setdefault(find(index), []).append(row)
    view_map = views if views is not None else _view_ids(rows)
    out = [_group_record(members, doc, view_map) for members in clusters.values()
           if 2 <= len(members) <= MAX_GROUP_MEMBERS]
    out.sort(key=lambda row: row["candidate_id"])
    return out


def _single_view_candidate(ir: Dict[str, Any], region: Dict[str, Any], views: Any,
                           anchors: Any, rects: Any) -> Dict[str, Any]:
    box = _bbox(region.get("bbox"))
    entity_ids = [str(value) for value in (region.get("entity_ids") or []) if str(value)]
    known = {str(row.get("entity_id") or "") for row in (ir.get("entities") or [])
             if isinstance(row, dict)}
    anchor_ids = [str(row.get("entity_id") or "") for row in (anchors or [])
                  if isinstance(row, dict) and _point(row.get("position")) is not None
                  and _point_in_box(_point(row.get("position")), box)]
    refs = [str(rect.get("dimension_ref") or "") for rect in (rects or [])
            if isinstance(rect, dict) and rect.get("dimension_ref")
            and box is not None
            and all(_point_in_box(point, box, DANGLING_JOIN_TOLERANCE_MM)
                    for point in _unique_points(rect.get("witness_points")))]
    return {
        "candidate_id": str(region.get("region_id") or ""),
        "region_ids": [str(region.get("region_id") or "")],
        "component_ids": [str(value) for value in (region.get("component_ids") or []) if str(value)],
        "entity_ids": entity_ids,
        "bbox": list(box) if box is not None else None,
        "boundary_role": _boundary_role(ir, region),
        "layers": list(region.get("layers") or []),
        "member_total": 1,
        "name_anchor_ids": sorted(set(anchor_ids)),
        "dimension_refs": sorted(set(refs)),
        "evidence_reasons": ["single_connected_fragment"],
        "geometry_status": "supported" if entity_ids and set(entity_ids) <= known
        else "insufficient",
        "view_id": str((views or {}).get(str(region.get("region_id") or "")) or ""),
    }


def _point_in_box(point: Any, box: Any, tolerance: float = 0.0) -> bool:
    return bool(point is not None and box is not None
                and box[0] - tolerance <= point[0] <= box[2] + tolerance
                and box[1] - tolerance <= point[1] <= box[3] + tolerance)


def _unique_points(values: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for value in values or []:
        point = _point(value)
        if point is not None and point not in out:
            out.append(point)
    return out


def _part_view_candidates(ir: Any, regions: Any, anchors: Any = None,
                          rects: Any = None) -> List[Dict[str, Any]]:
    """CAD 确定性层的整件视图候选（Spec §2 的键集合，稳定排序）。"""
    doc = ir if isinstance(ir, dict) else {}
    rows = [region for region in (regions or [])
            if isinstance(region, dict) and _bbox(region.get("bbox")) is not None]
    views = _view_ids(rows)
    out = [_single_view_candidate(doc, region, views, anchors, rects) for region in rows]
    out.extend(_whole_part_groups(doc, rows, views))
    out.sort(key=lambda row: (str(row.get("candidate_id") or ""),
                              ",".join(row.get("component_ids") or [])))
    return out


def build_part_view_candidates(cad_ir: Any, geometry_parts: Any) -> List[Dict[str, Any]]:
    """`cad_ir + 几何零件文档` → 整件视图候选（不联网、不读 BOM/金标，纯函数）。

    调用方（一键解析、补推导、人工确认面板）拿到的是**同一份**候选：程序只负责把
    "哪些图元、哪个排版格、什么边界角色、哪条标注指向它"摆出来，语义归属仍由模型建议 +
    人工/CAD 证据确认。
    """
    from . import packaging_business_part_resolver as resolver_agent

    doc = cad_ir if isinstance(cad_ir, dict) else {}
    regions = resolver_agent.regions_from_geometry_parts(geometry_parts)
    if not regions:
        regions = resolver_agent.build_geometry_regions(doc)
    try:
        anchors = resolver_agent.extract_text_anchors(doc)
    except Exception:  # pragma: no cover - 锚点提取是只读的，失败不能拖垮候选
        anchors = []
    try:
        rects = resolver_agent.dimension_rects(doc)
    except Exception:  # pragma: no cover
        rects = []
    return _part_view_candidates(doc, regions, anchors=anchors, rects=rects)


def validate_model_suggestion(request: Any, suggestion: Any) -> Dict[str, Any]:
    """模型建议的**纯校验器**（Spec §4）：只能引用程序给的候选，不能造 CAD 事实。

    输出闭集只有 `needs_confirmation` 与 `rejected`；被接受的建议也只是弱证据
    （`evidence_level=WEAK`），绝不改正式字段。
    """
    req = request if isinstance(request, dict) else {}
    by_id = {str(row.get("candidate_id")): row for row in (req.get("candidates") or [])
             if isinstance(row, dict) and row.get("candidate_id")}
    out: Dict[str, Any] = {"status": "rejected", "evidence_level": "WEAK",
                           "candidate_id": "", "reason": "", "text_class": "",
                           "alternatives": [], "over_reach_keys": []}
    if not isinstance(suggestion, dict):
        out["reason"] = "suggestion_must_be_an_object"
        return out
    over_reach = sorted(str(key) for key in suggestion if key in FORBIDDEN_SUGGESTION_KEYS)
    if over_reach:
        out["over_reach_keys"] = over_reach
        out["reason"] = "suggestion_must_not_claim_cad_facts:" + ",".join(over_reach)
        return out
    merged = [str(value) for value in (suggestion.get("candidate_ids") or [])
              if str(value).strip()] if isinstance(suggestion.get("candidate_ids"), (list, tuple)) else []
    action = str(suggestion.get("action") or "").strip().lower()
    if len(merged) > 1 or action in MERGE_ACTIONS:
        out["reason"] = "cross_candidate_merge_needs_human_confirmation"
        return out
    text_class = str(suggestion.get("text_class") or "").strip().lower()
    out["text_class"] = text_class
    if text_class and text_class not in SUGGESTION_TEXT_CLASSES:
        out["reason"] = "text_class_is_not_a_business_part:%s" % text_class
        return out
    if text_class == "material" and str(suggestion.get("suggested_name") or "").strip():
        out["reason"] = "material_text_is_not_a_part_name"
        return out
    candidate_id = str(suggestion.get("candidate_id") or "")
    candidate = by_id.get(candidate_id)
    if candidate is None:
        out["reason"] = "candidate_id_not_in_request"
        return out
    entity_ids = [str(value) for value in (candidate.get("entity_ids") or []) if str(value)]
    if not entity_ids:
        out["reason"] = "candidate_has_no_real_cad_entity"
        return out
    out.update({
        "status": "needs_confirmation",
        "candidate_id": candidate_id,
        "reason": str(suggestion.get("reason") or "")[:320],
        "suggested_name": str(suggestion.get("suggested_name") or "")[:120],
        "suggested_material": str(suggestion.get("suggested_material") or "")[:120],
        "model_confidence": _confidence(suggestion.get("confidence")),
        "alternatives": sorted(other for other in by_id if other != candidate_id),
        "view_id": str(candidate.get("view_id") or ""),
        "component_ids": list(candidate.get("component_ids") or []),
    })
    return out


def _confidence(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return min(1.0, max(0.0, number))


def render_candidate_preview(cad_ir: Any, anchor: Tuple[float, float],
                             candidates: List[Dict[str, Any]],
                             target_name: str = "") -> Optional[bytes]:
    """Small in-memory PNG montage; no raw DXF, workbook or gold data is used."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    chosen = candidates[:8]
    if not chosen:
        return None
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    entities = {str(row.get("entity_id") or ""): row for row in (ir.get("entities") or [])
                if isinstance(row, dict)}
    label_font = None
    for font_path in ("/System/Library/Fonts/PingFang.ttc",
                      "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"):
        try:
            label_font = ImageFont.truetype(font_path, 12)
            break
        except OSError:
            continue
    image = Image.new("RGB", (1056, 1056), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((8, 3), "CAD candidate views / cut red / crease green / partial blue / V-slot pink",
              fill="#334155")
    draw.text((8, 18), "Name anchor: (%.1f, %.1f). Panel IDs match candidate list." % anchor,
              fill="#334155")
    _draw_overview(draw, entities, anchor, chosen, (8, 38, 344, 368),
                   ir.get("texts"), target_name, label_font)
    for index, candidate in enumerate(chosen, 1):
        col, row = index % 3, index // 3
        _draw_panel(draw, entities, candidate,
                    (col * 352 + 8, row * 338 + 38,
                     col * 352 + 344, row * 338 + 368))
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _default_reviewer(request: Dict[str, Any]) -> Dict[str, Any]:
    from . import llm_client

    preview = request.get("preview") or {}
    raw = preview.get("bytes")
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("candidate_preview_unavailable")
    candidates = request.get("candidates") or []
    concise = [{key: item.get(key) for key in ("id", "bbox", "layers", "distance_mm",
                                                "dimension_spatial", "anchor_in_region")}
               for item in candidates]
    prompt = (
        "你只是在 CAD 图纸上复核件名与候选图形的归属，不是在算尺寸。"
        "图片左上格是总览：红点 A 是件名位置，蓝框 1-8 对应后面 8 个候选格，"
        "候选格左上角 ID 与候选表对应。必须看候选图形与名称/视图证据；"
        "仅凭最近距离、相似尺寸或想象不能选。若图片不能证明归属，返回 none。"
        "只能选择候选表内已有的一个 id，绝不可发明 id、尺寸、材料或件名。"
        "只输出 JSON，键必须是 selected_candidate_id、insufficient_evidence、reason；"
        "拒答时 selected_candidate_id='none' 且 insufficient_evidence=true。"
        "件名：%s。名称插入点：%s。候选：%s"
        % (request.get("name"), request.get("anchor_position"), concise)
    )
    result = llm_client.run(
        "你是 CAD 图纸归属审查员。证据不足时必须拒答；回答仅作弱证据建议。",
        [llm_client.image_block(raw, "cad-candidates.png", detail="high"),
         llm_client.text_block(prompt)], _Decision, max_tokens=500,
    )
    return result.model_dump() if hasattr(result, "model_dump") else dict(result)


def _demote(binding: Dict[str, Any], candidates: List[Dict[str, Any]],
            status: str, reason: str, selected: str = "",
            explanation: str = "") -> None:
    binding.update({
        "status": "ambiguous" if candidates else "unbound",
        "region_id": "", "component_ids": [], "entity_ids": [], "bbox": None,
        "length_mm": None, "width_mm": None, "size_source": "none",
        "size_confirmed": False, "confidence": 0.0,
        "region_area_mm2": None, "region_layers": [], "instances": [],
        "evidence_kinds": ["text_anchor"],
        "candidates": [{key: item.get(key) for key in
                        ("id", "component_ids", "bbox", "distance_mm", "layers",
                         "dimension_spatial", "anchor_in_region")}
                       for item in candidates],
        "attribution": {"status": status, "reasons": [reason],
                        "selected_candidate_id": selected,
                        "explanation": explanation[:320]},
    })
    binding["reasons"] = list(dict.fromkeys(list(binding.get("reasons") or []) + [reason]))


def review_matches(cad_ir: Any, rows: Any, anchors: Any, regions: Any,
                   rects: Any, match: Dict[str, Any],
                   *, reviewer: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
                   progress: Optional[Callable[[int, int, str], None]] = None
                   ) -> Dict[str, Any]:
    """Review all names; only a bounded, risk-ranked subset calls the model."""
    anchor_by_id = {str(item.get("entity_id") or ""): item for item in (anchors or [])
                    if isinstance(item, dict)}
    by_code = {str(item.get("business_part_code") or ""): item for item in (rows or [])
               if isinstance(item, dict)}
    by_region = {str(item.get("region_id") or ""): item for item in (regions or [])
                 if isinstance(item, dict)}
    # 整件候选（多片互补刀线）先算一次：每件拿到的候选表里必须含"它属于的那一组"，
    # 否则模型只能看到单片（Spec `packaging-whole-part-candidate-geometry-first.md` §2/§4）。
    groups = _whole_part_groups(cad_ir, regions)
    work: List[Tuple[float, str, Dict[str, Any], Dict[str, Any], Tuple[float, float],
                     List[Dict[str, Any]]]] = []
    for binding in match.get("bindings") or []:
        code = str(binding.get("business_part_code") or "")
        row = by_code.get(code) or {}
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        anchor = next((anchor_by_id.get(str(eid)) for eid in
                       (evidence.get("anchor_entity_ids") or [])
                       if anchor_by_id.get(str(eid))), None)
        point = _point((anchor or {}).get("position"))
        if point is None:
            _demote(binding, [], "review_needed", "name_anchor_position_missing")
            continue
        choices = _candidates(point, regions, str(binding.get("region_id") or ""), rects, groups)
        current = by_region.get(str(binding.get("region_id") or ""))
        if current and _anchor_in_region(point, current) and _spatial_dimension(current, rects):
            binding["attribution"] = {"status": "verified_by_cad", "reasons": [
                "name_inside_region_and_spatial_dimension"]}
            continue
        # A distance-only assignment is a proposal, never a verified binding.
        distance = next((item["distance_mm"] for item in choices
                         if item["id"] == binding.get("region_id")), float("inf"))
        box = _bbox((current or {}).get("bbox"))
        diagonal = math.hypot(box[2] - box[0], box[3] - box[1]) if box else 1.0
        risk = (distance / max(diagonal, 1.0)) + (0.2 if not binding.get("size_confirmed") else 0)
        work.append((risk, code, binding, row, point, choices))
    work.sort(key=lambda item: (-item[0], item[1]))
    try:
        max_calls = max(0, min(64, int(os.environ.get("PACKAGING_PART_MODEL_MAX_CALLS", "8"))))
    except ValueError:
        max_calls = 8
    if reviewer is not None:
        max_calls = 64  # A test-supplied reviewer has no network/cost budget.
    elif str(os.environ.get("PACKAGING_PART_MODEL_REVIEW") or "auto").lower() == "off":
        max_calls = 0
    else:
        try:
            from . import llm_settings
            if not llm_settings.resolve(vision=True).get("api_key"):
                max_calls = 0
        except Exception:
            max_calls = 0
    used = 0
    planned = min(max_calls, len(work))
    for _risk, _code, binding, row, point, choices in work:
        if used >= max_calls:
            _demote(binding, choices, "review_needed", "model_review_budget_exhausted")
            continue
        preview = render_candidate_preview(cad_ir, point, choices, str(row.get("name") or ""))
        if preview is None:
            _demote(binding, choices, "model_unavailable", "candidate_preview_unavailable")
            continue
        request = {
            "name": str(row.get("name") or ""),
            "anchor_entity_id": str(next(iter(
                (row.get("evidence") or {}).get("anchor_entity_ids") or []), "")),
            "anchor_position": list(point),
            "candidates": choices,
            "preview": {"media_type": "image/png", "bytes": preview},
        }
        try:
            used += 1
            if progress is not None:
                try:
                    progress(used, planned, str(row.get("name") or ""))
                except Exception:
                    pass  # Progress telemetry cannot change the recognition result.
            decision = (reviewer or _default_reviewer)(request)
        except Exception:
            _demote(binding, choices, "model_unavailable", "model_review_failed")
            continue
        if not isinstance(decision, dict):
            _demote(binding, choices, "model_unavailable", "model_output_invalid")
            continue
        selected = str(decision.get("selected_candidate_id") or "none")
        if selected == "none" or decision.get("insufficient_evidence") is True:
            _demote(binding, choices, "review_needed", "model_abstained",
                    explanation=str(decision.get("reason") or ""))
            continue
        candidate = next((item for item in choices if item["id"] == selected), None)
        if candidate is None:
            _demote(binding, choices, "review_needed", "model_candidate_id_invalid")
            continue
        # 模型答复走**生产同一校验器**（Spec §4）：越权字段、跨候选乱并、候选无真实图元
        # 一律拒收；被接受的也只是弱建议，不改正式字段。
        verdict = validate_model_suggestion(
            {"candidates": [{"candidate_id": item["id"],
                             "view_id": item.get("view_id", ""),
                             "component_ids": list(item.get("component_ids") or []),
                             "entity_ids": list(item.get("entity_ids") or []),
                             "bbox": item.get("bbox"),
                             "geometry_status": item.get("geometry_status", "")}
                            for item in choices]},
            {"candidate_id": selected, "reason": str(decision.get("reason") or "")})
        if verdict.get("status") != "needs_confirmation":
            _demote(binding, choices, "review_needed", "model_suggestion_rejected_by_cad_gate",
                    selected, str(verdict.get("reason") or ""))
            continue
        region = by_region.get(selected)
        if region and candidate["anchor_in_region"] and candidate["dimension_spatial"]:
            box = _bbox(region.get("bbox"))
            if box is not None:
                binding.update({"status": "bound", "region_id": selected,
                                "component_ids": list(region.get("component_ids") or []),
                                "entity_ids": list(region.get("entity_ids") or []),
                                "bbox": list(box), "length_mm": box[2] - box[0],
                                "width_mm": box[3] - box[1], "size_source": "size_dimension",
                                "size_confirmed": True,
                                "attribution": {"status": "verified_by_cad", "reasons": [
                                    "model_selected_existing_candidate_with_cad_evidence"],
                                    "selected_candidate_id": selected}})
                continue
        _demote(binding, choices, "model_suggested", "model_suggestion_needs_cad_confirmation",
                selected, str(decision.get("reason") or ""))
    counts = {key: 0 for key in ("bound", "partial", "ambiguous", "unbound")}
    for binding in match.get("bindings") or []:
        counts[str(binding.get("status") or "unbound")] += 1
    for key, value in counts.items():
        match[key + "_total"] = value
    match["assigned_regions"] = {str(binding["region_id"]): str(binding["business_part_code"])
                                 for binding in match.get("bindings") or []
                                 if binding.get("region_id") and binding.get("status") == "bound"}
    active_codes = set(match["assigned_regions"].values())
    match["instances"] = [item for item in (match.get("instances") or [])
                          if str(item.get("business_part_code") or "") in active_codes]
    model_name = ""
    if used and reviewer is None:
        try:
            from . import llm_client
            model_name = str(llm_client.last_used_model() or "")
        except Exception:
            pass
    match["model_review"] = {"enabled": True, "calls": used, "model": model_name,
                             "unavailable_total": sum(1 for binding in match.get("bindings") or []
                                                      if (binding.get("attribution") or {}).get("status")
                                                      == "model_unavailable"),
                             "review_needed_total": sum(1 for binding in match.get("bindings") or []
                                                       if binding.get("status") in ("ambiguous", "unbound"))}
    return match
