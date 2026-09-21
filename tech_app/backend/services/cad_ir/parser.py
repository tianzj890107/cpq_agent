"""ezdxf → CAD IR：实体、图层、文字、标注、上限、错误（DWG 第 3 批 Spec §3 / §6 / §8）。

`parse_dxf()` 是**纯函数式确定性**的：不读库、不写盘、不联网、不调模型；
同样输入两次调用得到同样的 `ir_hash`。只有 `parse_conversion()` 才落盘。
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .. import file_preflight
from . import blocks, geometry, model, units

# --------------------------------------------------------------------------- #
# 上限（Spec §8，全部可用环境变量覆盖）
# --------------------------------------------------------------------------- #
ENV_MAX_ENTITIES = "CAD_IR_MAX_ENTITIES"
ENV_MAX_LAYERS = "CAD_IR_MAX_LAYERS"
ENV_MAX_BLOCK_DEPTH = "CAD_IR_MAX_BLOCK_DEPTH"
ENV_MAX_DXF_BYTES = "CAD_IR_MAX_DXF_BYTES"
ENV_PARSE_TIMEOUT = "CAD_IR_PARSE_TIMEOUT_SECONDS"
ENV_MAX_EVIDENCE = "CAD_IR_MAX_EVIDENCE"

DEFAULT_LIMITS: Dict[str, Any] = {
    "max_entities": 200000,
    "max_layers": 5000,
    "max_block_depth": 8,
    "max_dxf_bytes": 256 * 1024 * 1024,
    "parse_timeout_seconds": 120,
    "max_evidence": 50000,
    "include_paper_space": False,
    "include_blocks": True,
}

#: 本批支持并归类的实体类型；其余一律进 `unsupported`（图不消失）
POLYLINE_TYPES = ("LWPOLYLINE", "POLYLINE")
SUPPORTED_TYPES = frozenset(POLYLINE_TYPES + (
    "LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "INSERT", "HATCH",
    "TEXT", "MTEXT", "DIMENSION", "LEADER", "MLEADER",
))

#: 标注类型位（dxf.dimtype & 7）→ 可读名
DIMENSION_KINDS = {0: "linear", 1: "aligned", 2: "angular", 3: "diameter",
                   4: "radius", 5: "angular3p", 6: "ordinate"}

#: 文字里的 `\\U+XXXX` 转义（AutoCAD 与 LibreDWG 两种大小写都出现）
_UNICODE_ESCAPE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")
#: MTEXT 格式码：`\\H1.5x;` 这类带分号的、以及 `\\P` 折行与花括号
_MTEXT_CODES = re.compile(r"\\[A-Za-z][^;\\]*;")

#: 标注文字里的「没有覆盖」占位符（AutoCAD 默认值）
_DIM_PLACEHOLDERS = ("", "<>", "< >")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


# --------------------------------------------------------------------------- #
# 上限与能力
# --------------------------------------------------------------------------- #
def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)
    return value if value > 0 else int(default)


def resolve_limits(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    limits = dict(DEFAULT_LIMITS)
    limits["max_entities"] = _env_int(ENV_MAX_ENTITIES, limits["max_entities"])
    limits["max_layers"] = _env_int(ENV_MAX_LAYERS, limits["max_layers"])
    limits["max_block_depth"] = _env_int(ENV_MAX_BLOCK_DEPTH, limits["max_block_depth"])
    limits["max_dxf_bytes"] = _env_int(ENV_MAX_DXF_BYTES, limits["max_dxf_bytes"])
    limits["parse_timeout_seconds"] = _env_int(ENV_PARSE_TIMEOUT, limits["parse_timeout_seconds"])
    limits["max_evidence"] = _env_int(ENV_MAX_EVIDENCE, limits["max_evidence"])
    for key, value in (overrides or {}).items():
        if key in limits and value is not None:
            limits[key] = value
    return limits


def capability() -> Dict[str, Any]:
    """解析能力自报（Spec §2.1）：本机没有 ezdxf 时如实给出 available=False。"""
    try:
        import ezdxf
        version = str(getattr(ezdxf, "__version__", "") or "")
        available = bool(version)
        message = "DXF 解析器就绪（本地确定性解析，不联网、不调模型）"
        error = ""
    except Exception as exc:  # pragma: no cover - 依赖环境
        version = ""
        available = False
        message = "缺少 ezdxf，DXF 解析不可用"
        error = type(exc).__name__
    limits = resolve_limits()
    return {
        "available": available,
        "parser": model.PARSER_NAME,
        "parser_version": version,
        "max_entities": limits["max_entities"],
        "max_layers": limits["max_layers"],
        "max_block_depth": limits["max_block_depth"],
        "max_dxf_bytes": limits["max_dxf_bytes"],
        "max_evidence": limits["max_evidence"],
        "stable_error_code": error,
        "message": message,
        "ir_version": model.CAD_IR_VERSION,
    }


# --------------------------------------------------------------------------- #
# 文字归一化（Spec §3.6）
# --------------------------------------------------------------------------- #
def normalize_text(raw: Any) -> str:
    """`\\U+XXXX` 解码 → MTEXT 格式码清理 → strip；已是明文的字符串**原样保留**。"""
    text = str(raw if raw is not None else "")
    if not text:
        return ""
    if "\\U+" in text or "\\u+" in text:
        text = _UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)
    text = text.replace("\\P", "\n").replace("\\p", "\n")
    text = _MTEXT_CODES.sub("", text)
    text = text.replace("{", "").replace("}", "")
    return text.strip()


def _number_of(raw: Any) -> Optional[float]:
    match = _NUMBER.search(str(raw or ""))
    if not match:
        return None
    return geometry.finite(match.group(0))


# --------------------------------------------------------------------------- #
# 错误
# --------------------------------------------------------------------------- #
def _fail(code: str, message: str, detected: Optional[Dict[str, Any]] = None) -> None:
    raise file_preflight.FileCapabilityError(code, detected=dict(detected or {}), message=message)


def _read_document(text: str) -> Any:
    try:
        import ezdxf
    except Exception as exc:  # pragma: no cover - 依赖环境
        _fail("DWG_PARSE_FAILED", "本机缺少 DXF 解析依赖，无法解析图纸",
              {"missing": type(exc).__name__})
    try:
        return ezdxf.read(io.StringIO(text))
    except Exception as exc:
        name = type(exc).__name__
        if name in ("DXFStructureError", "DXFVersionError", "UnicodeDecodeError"):
            _fail("FILE_CORRUPTED", "DXF 文件结构损坏或被截断，无法解析",
                  {"error_type": name})
        _fail("DWG_PARSE_FAILED", "图纸解析失败，请确认文件是完整的 DXF", {"error_type": name})


def _decode_text(content: bytes) -> str:
    """字节 → 文本。DXF 的换行按标准是 CRLF（LibreDWG 0.14 转出的就是 CRLF），
    必须先归一化成 LF 再交给解析器，否则每行会带上一个 `\r` 把组码读歪。"""
    text = ""
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if not text:  # pragma: no cover - 兜底
        text = content.decode("latin-1", errors="replace")
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


# --------------------------------------------------------------------------- #
# 实体 → IR 行
# --------------------------------------------------------------------------- #
class _Builder:
    """一次解析的上下文：容差、单位、证据、警告、上限。"""

    def __init__(self, limits: Dict[str, Any]) -> None:
        self.limits = limits
        self.tolerance = geometry.MIN_TOLERANCE
        self.scale_to_mm: Optional[float] = None
        self.drawing_units: str = "unitless"
        self.evidence: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[Dict[str, Any]] = []
        self.truncated_evidence = False

    def warn(self, code: str, message: str, refs: Optional[Sequence[str]] = None) -> None:
        for item in self.warnings:
            if item.get("code") == code and item.get("message") == message:
                return
        self.warnings.append({"code": code, "message": message, "evidence_refs": list(refs or [])})

    def add_evidence(self, ref: str, entry: Dict[str, Any]) -> str:
        if len(self.evidence) >= int(self.limits["max_evidence"]):
            self.truncated_evidence = True
            return ref
        if ref not in self.evidence:
            self.evidence[ref] = entry
        return ref

    def spatial(self, bbox: Optional[Sequence[float]]) -> List[float]:
        if not bbox:
            return []
        return [round((float(bbox[0]) + float(bbox[2])) / 2.0, 6),
                round((float(bbox[1]) + float(bbox[3])) / 2.0, 6)]


def _points_of_entity(entity: Any, kind: str) -> List[geometry.Point]:
    if kind == "LWPOLYLINE":
        try:
            return geometry.points_of(list(entity.get_points("xy")))
        except Exception:  # pragma: no cover - 个别文件写出非法顶点
            return []
    if kind == "POLYLINE":
        out: List[geometry.Point] = []
        try:
            for vertex in entity.vertices:
                point = geometry.point_of(vertex.dxf.location)
                if point is not None:
                    out.append(point)
        except Exception:  # pragma: no cover
            return []
        return out
    return []


def _closed_flag(entity: Any, kind: str, points: Sequence[geometry.Point],
                 tolerance: float) -> bool:
    if kind in ("CIRCLE", "ELLIPSE"):
        return True
    if kind == "ARC":
        return True
    if kind in POLYLINE_TYPES:
        try:
            if bool(entity.closed) if kind == "LWPOLYLINE" else bool(entity.is_closed):
                return True
        except Exception:  # pragma: no cover
            pass
        return geometry.is_closed(points, tolerance)
    if kind == "SPLINE":
        try:
            return bool(entity.closed)
        except Exception:  # pragma: no cover
            return False
    return False


def _curve_metrics(entity: Any, kind: str) -> Tuple[Optional[float], Optional[float],
                                                    Optional[List[float]], Dict[str, Any]]:
    """返回 (length, area, bbox, attributes)，只对曲线类使用。"""
    attrs: Dict[str, Any] = {}
    if kind == "CIRCLE":
        radius = geometry.finite(entity.dxf.get("radius", 0.0)) or 0.0
        center = geometry.point_of(entity.dxf.get("center"))
        attrs.update({"radius": radius, "diameter": radius * 2.0,
                      "center": list(center) if center else None, "curve": "circle"})
        box = None
        if center:
            box = [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius]
        return geometry.circle_length(radius), None, box, attrs
    if kind == "ARC":
        radius = geometry.finite(entity.dxf.get("radius", 0.0)) or 0.0
        center = geometry.point_of(entity.dxf.get("center"))
        start = geometry.finite(entity.dxf.get("start_angle", 0.0)) or 0.0
        end = geometry.finite(entity.dxf.get("end_angle", 0.0)) or 0.0
        attrs.update({"radius": radius, "diameter": radius * 2.0,
                      "center": list(center) if center else None,
                      "start_angle": start, "end_angle": end, "curve": "arc"})
        boxes = []
        try:
            boxes = [list(point) for point in
                     geometry.points_of([(v.x, v.y) for v in entity.flattening(0.01)])]
        except Exception:  # pragma: no cover
            boxes = []
        return (geometry.arc_length(radius, start, end), None,
                geometry.bbox_of(boxes), attrs)
    if kind == "ELLIPSE":
        center = geometry.point_of(entity.dxf.get("center"))
        major = entity.dxf.get("major_axis")
        major_len = geometry.finite(getattr(major, "magnitude", None))
        if major_len is None:
            major_len = geometry.finite(math_hypot(getattr(major, "x", None), getattr(major, "y", None)))
        ratio = geometry.finite(entity.dxf.get("ratio", 1.0)) or 1.0
        semi_major = major_len or 0.0
        semi_minor = abs(semi_major * ratio)
        pts: List[geometry.Point] = []
        try:
            pts = [(float(v.x), float(v.y)) for v in entity.flattening(0.01)]
        except Exception:  # pragma: no cover
            pts = []
        attrs.update({"center": list(center) if center else None, "ratio": ratio,
                      "semi_major": semi_major, "semi_minor": semi_minor, "curve": "ellipse"})
        return (geometry.ellipse_perimeter(semi_major, semi_minor), None,
                geometry.bbox_of(pts), attrs)
    if kind == "SPLINE":
        pts = []
        try:
            pts = [(float(v.x), float(v.y)) for v in entity.flattening(0.01)]
        except Exception:  # pragma: no cover
            pts = []
        length = geometry.polyline_length(pts, closed=False)
        control = 0
        try:
            control = len(entity.control_points)
        except Exception:  # pragma: no cover
            control = 0
        # 折线/样条的**顶点坐标必须落盘**（Spec `packaging-parts-true-outline.md` §2）：
        # 只留数量的话，下游（零件真实轮廓）永远拿不到端点，链式闭合无从谈起。
        # `fit_points` 由「数量」改为坐标序列，数量挪到 `fit_points_count`（新增键，不删旧信息）。
        attrs.update({"control_points": control, "fit_points_count": len(pts),
                      "fit_points": geometry.points_of(pts), "curve": "spline",
                      "sampled": True, "sample_step": 0.01})
        return length, None, geometry.bbox_of(pts), attrs
    return None, None, None, attrs


def math_hypot(first: Any, second: Any) -> Optional[float]:
    x, y = geometry.finite(first), geometry.finite(second)
    if x is None or y is None:
        return None
    return (x * x + y * y) ** 0.5


def _polyline_row(entity: Any, kind: str, item: Dict[str, Any], builder: _Builder
                  ) -> Optional[Dict[str, Any]]:
    points = _points_of_entity(entity, kind)
    if not points:
        return None
    closed = _closed_flag(entity, kind, points, builder.tolerance)
    length = geometry.polyline_length(points, closed=closed)
    area = geometry.polygon_area(points) if closed else None
    return {"closed": closed, "length": length, "area": area,
            "bbox": geometry.bbox_of(points),
            # 顶点坐标落盘（Spec §2）：`vertices` 仍是数量，`points` 是坐标（与它一致）。
            "attributes": {"vertices": len(points), "points": geometry.points_of(points)}}


def _row_for(entity: Any, item: Dict[str, Any], builder: _Builder,
             handle: str) -> Optional[Dict[str, Any]]:
    """把一条叶子实体转成 IR 行；返回 None 表示该类型本批不支持（交由调用方登记）。"""
    kind = str(entity.dxftype())
    if kind not in SUPPORTED_TYPES:
        return None
    layer = str(getattr(entity.dxf, "layer", "0") or "0")
    block_path = [dict(step) for step in item.get("block_path") or []]
    entity_id = blocks.entity_id_of(item, handle)
    ref = blocks.evidence_ref_of(item, handle)
    row: Dict[str, Any] = {
        "entity_id": entity_id, "handle": handle, "type": kind, "kind": kind.lower(),
        "layer": layer, "space": str(item.get("space") or "model"),
        "block_path": block_path, "closed": False, "bbox": None,
        "length": None, "area": None, "length_mm": None, "area_mm2": None,
        "attributes": {}, "evidence_ref": ref,
    }
    if block_path:
        row["block_path"] = block_path

    if kind in POLYLINE_TYPES:
        measured = _polyline_row(entity, kind, item, builder)
        if measured is None:
            builder.warn("polyline_without_vertices",
                         "折线没有可用顶点，已跳过几何计算", [ref])
            return row
        row.update(measured)
        row["kind"] = "polyline"
    elif kind == "LINE":
        start = geometry.point_of(entity.dxf.get("start"))
        end = geometry.point_of(entity.dxf.get("end"))
        points = [p for p in (start, end) if p is not None]
        row["kind"] = "line"
        row["closed"] = False
        row["bbox"] = geometry.bbox_of(points)
        row["length"] = geometry.distance(start, end) if len(points) == 2 else None
        row["area"] = None
        row["attributes"] = {"start": list(start) if start else None,
                             "end": list(end) if end else None}
    elif kind in ("CIRCLE", "ARC", "ELLIPSE", "SPLINE"):
        length, area, box, attrs = _curve_metrics(entity, kind)
        row["kind"] = kind.lower()
        row["closed"] = _closed_flag(entity, kind, [], builder.tolerance)
        row["length"] = length
        row["area"] = area
        row["bbox"] = box
        row["attributes"] = attrs
    elif kind == "INSERT":
        name = str(entity.dxf.get("name", "") or "")
        row["kind"] = "insert"
        row["attributes"] = {"block_name": name,
                             "insert": list(geometry.point_of(entity.dxf.get("insert")) or [])}
    elif kind == "HATCH":
        paths = list(getattr(entity, "paths", []) or [])
        points: List[geometry.Point] = []
        closed_paths = 0
        for path in paths:
            vertices = getattr(path, "vertices", None)
            if vertices is None:
                continue
            try:
                path_points = [(float(v[0]), float(v[1])) for v in vertices]
            except Exception:  # pragma: no cover
                path_points = []
            if len(path_points) >= 3:
                closed_paths += 1
            points.extend(path_points)
        row["kind"] = "hatch"
        row["bbox"] = geometry.bbox_of(points)
        row["attributes"] = {"boundary_paths": len(paths), "boundary_loops": closed_paths,
                             "solid_fill": bool(entity.dxf.get("solid_fill", 0))}
        if not paths:
            builder.warn("hatch_without_boundary", "填充缺少边界路径，只登记实体本身", [ref])
    elif kind == "DIMENSION":
        row["kind"] = "dimension"
        row["bbox"] = _dimension_bbox(entity)
        row["attributes"] = {"dim_type": DIMENSION_KINDS.get(
            int(entity.dxf.get("dimtype", 0) or 0) & 7, "unknown")}
    else:  # LEADER / MLEADER：解析库给得出就登记，给不出引用关系只警告
        row["kind"] = kind.lower()
        try:
            row["bbox"] = _dimension_bbox(entity)
        except Exception:  # pragma: no cover
            builder.warn("leader_reference_unavailable", "%s 的引用关系读不出来" % kind, [ref])
    return row


def _dimension_bbox(entity: Any) -> Optional[List[float]]:
    points: List[geometry.Point] = []
    for attribute in ("defpoint", "text_midpoint", "defpoint2", "defpoint3", "defpoint4"):
        point = geometry.point_of(entity.dxf.get(attribute))
        if point is not None:
            points.append(point)
    return geometry.bbox_of(points)


def _finish_row(row: Dict[str, Any], builder: _Builder) -> Dict[str, Any]:
    scale = builder.scale_to_mm
    if row.get("length") is not None:
        row["length"] = round(float(row["length"]), 9)
        row["length_mm"] = units.mm_of(row["length"], scale)
        if row["length_mm"] is not None:
            row["length_mm"] = round(float(row["length_mm"]), 9)
    if row.get("area") is not None:
        row["area"] = round(float(row["area"]), 9)
        row["area_mm2"] = units.mm2_of(row["area"], scale)
        if row["area_mm2"] is not None:
            row["area_mm2"] = round(float(row["area_mm2"]), 9)
    if row.get("bbox"):
        row["bbox"] = [round(float(value), 9) for value in row["bbox"]]
    return row


# --------------------------------------------------------------------------- #
# 文字 / 标注 / 图层
# --------------------------------------------------------------------------- #
def _text_row(entity: Any, item: Dict[str, Any], handle: str) -> Dict[str, Any]:
    kind = str(entity.dxftype())
    raw = entity.dxf.get("text", "")
    if kind == "MTEXT":
        height = geometry.finite(entity.dxf.get("char_height"))
        position = geometry.point_of(entity.dxf.get("insert"))
    else:
        height = geometry.finite(entity.dxf.get("height"))
        position = geometry.point_of(entity.dxf.get("insert"))
    return {
        "entity_id": blocks.entity_id_of(item, handle),
        "handle": handle,
        "type": kind,
        "layer": str(entity.dxf.get("layer", "0") or "0"),
        "raw_text": str(raw if raw is not None else ""),
        "normalized_text": normalize_text(raw),
        "position": list(position) if position else None,
        "height": height,
        "evidence_ref": blocks.evidence_ref_of(item, handle),
    }


def _dimension_row(entity: Any, item: Dict[str, Any], handle: str, builder: _Builder
                   ) -> Dict[str, Any]:
    raw = entity.dxf.get("text", "")
    raw_text = "" if str(raw if raw is not None else "").strip() in _DIM_PLACEHOLDERS \
        else str(raw)
    try:
        measured = geometry.finite(entity.get_measurement())
    except Exception:  # pragma: no cover - 个别标注算不出测量值
        measured = None
    declared = _number_of(raw_text) if raw_text else None
    fallback = declared is None
    if declared is None:
        declared = measured
    delta = None
    if declared is not None and measured is not None:
        # Spec §3 的示例是 declared=70 / measured=72 / delta=2.0 → 取「实测 − 标注」；
        # 没有标注文字时 declared 回落到实测，delta 恒为 0（Spec §3.7）。
        delta = round(float(measured) - float(declared), 9)
    elif declared is not None:
        delta = 0.0
    unit = builder.drawing_units if builder.drawing_units else "unitless"
    try:
        tolerance = geometry.finite(entity.dxf.get("dimtol", 0.0)) or 0.0
    except Exception:  # pragma: no cover - 部分标注实体没有该属性
        tolerance = 0.0
    return {
        "entity_id": blocks.entity_id_of(item, handle),
        "handle": handle,
        "layer": str(entity.dxf.get("layer", "0") or "0"),
        "dim_type": DIMENSION_KINDS.get(int(entity.dxf.get("dimtype", 0) or 0) & 7, "unknown"),
        "raw_text": raw_text,
        "normalized_text": normalize_text(raw_text),
        "declared_value": declared,
        "measured_value": measured,
        "unit": unit,
        "delta": delta,
        "tolerance": tolerance,
        "target_entity_ids": _dimension_targets(entity),
        "confidence": 0.5 if fallback else 0.8,
        "evidence_ref": blocks.evidence_ref_of(item, handle),
    }


def _dimension_targets(entity: Any) -> List[str]:
    """把标注的两个定义点尽量关联到实体（关联不到就留空，不猜）。"""
    out: List[str] = []
    try:
        for attribute in ("defpoint2", "defpoint3"):
            point = geometry.point_of(entity.dxf.get(attribute))
            if point is not None:
                out.append("point:%.6f,%.6f" % (point[0], point[1]))
    except Exception:  # pragma: no cover
        return []
    return out


def _layer_rows(doc: Any, counts: Dict[str, int]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for layer in doc.layers:
        name = str(layer.dxf.get("name", "") or "")
        rows.append({
            "name": name,
            "visible": not bool(layer.is_off()),
            "frozen": bool(layer.is_frozen()),
            "color": int(layer.dxf.get("color", 7) or 7),
            "line_type": str(layer.dxf.get("linetype", "CONTINUOUS") or "CONTINUOUS"),
            "entity_count": int(counts.get(name, 0)),
            "inferred_role": None,
            "role_confidence": None,
            "evidence_refs": ["ev:L:%s" % name],
        })
    rows.sort(key=lambda row: row["name"])
    return rows


# --------------------------------------------------------------------------- #
# 主解析
# --------------------------------------------------------------------------- #
def _expansion_for(doc: Any, layout: Any, limits: Dict[str, Any], space: str) -> blocks.Expansion:
    state = blocks.Expansion(max_depth=limits["max_block_depth"],
                             max_entities=limits["max_entities"])
    if limits.get("include_blocks", True):
        blocks.walk(layout, doc, state, space=space)
    else:
        for entity in layout:
            state.push({"entity": entity, "matrix": None, "block_path": [],
                        "kind": entity.dxftype(), "handle": "", "block_name": "", "space": space})
    return state


def _transformed_leaves(state: blocks.Expansion) -> List[Dict[str, Any]]:
    leaves: List[Dict[str, Any]] = []
    for item in state.leaves:
        item = dict(item)
        item["entity"] = blocks.apply_matrix(item)
        leaves.append(item)
    return leaves


def _leaf_points(leaves: Sequence[Dict[str, Any]]) -> List[geometry.Point]:
    points: List[geometry.Point] = []
    for item in leaves:
        kind = str(item["entity"].dxftype())
        if kind in POLYLINE_TYPES:
            points.extend(_points_of_entity(item["entity"], kind))
        elif kind == "LINE":
            for attribute in ("start", "end"):
                point = geometry.point_of(item["entity"].dxf.get(attribute))
                if point is not None:
                    points.append(point)
        elif kind in ("CIRCLE", "ARC", "ELLIPSE", "SPLINE"):
            _, _, box, _ = _curve_metrics(item["entity"], kind)
            if box:
                points.extend([(box[0], box[1]), (box[2], box[3])])
    return points


def parse_dxf(content: bytes, *, filename: str = "drawing.dxf",
              source: Optional[Dict[str, Any]] = None,
              limits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """字节 → CAD IR。**不做任何 I/O**（Spec §2.1）。"""
    resolved = resolve_limits(limits)
    data = bytes(content)
    if len(data) > int(resolved["max_dxf_bytes"]):
        _fail("FILE_TOO_LARGE", "DXF 文件超过解析大小上限",
              {"filename": filename, "bytes": len(data),
               "max_bytes": int(resolved["max_dxf_bytes"])})

    doc = _read_document(_decode_text(data))
    builder = _Builder(resolved)

    model_layout = doc.modelspace()
    if len(list(model_layout)) > int(resolved["max_entities"]):
        _fail("CAD_IR_ENTITY_LIMIT_EXCEEDED", "图纸实体数超过解析上限",
              {"filename": filename, "max_entities": int(resolved["max_entities"])})

    model_state = _expansion_for(doc, model_layout, resolved, "model")
    for warning in model_state.warnings:
        builder.warn(str(warning.get("code") or ""), str(warning.get("message") or ""),
                     warning.get("evidence_refs") or [])
    leaves = _transformed_leaves(model_state)
    if model_state.overflow or len(leaves) > int(resolved["max_entities"]):
        _fail("CAD_IR_ENTITY_LIMIT_EXCEEDED", "图纸实体数超过解析上限（含块展开）",
              {"filename": filename, "max_entities": int(resolved["max_entities"]),
               "expanded": len(leaves)})

    builder.tolerance = geometry.tolerance_for(geometry.bbox_of(_leaf_points(leaves)))

    text_rows: List[Dict[str, Any]] = []
    raw_texts: List[str] = []
    for item in leaves:
        if str(item["entity"].dxftype()) in ("TEXT", "MTEXT"):
            row = _text_row(item["entity"], item, item.get("handle") or "")
            text_rows.append(row)
            raw_texts.append(row["normalized_text"])
    text_rows.sort(key=lambda row: row["entity_id"])

    drawing_units = units.resolve(doc.header.get("$INSUNITS"), raw_texts)
    builder.scale_to_mm = drawing_units.get("scale_to_mm")
    builder.drawing_units = drawing_units.get("drawing_units")
    if drawing_units.get("unit_status") != "confirmed":
        builder.warn("unit_unconfirmed",
                     "图纸未声明单位（$INSUNITS=0/缺失），绝对尺寸待人工确认（Spec §4）")

    entity_rows: List[Dict[str, Any]] = []
    dimension_rows: List[Dict[str, Any]] = []
    unsupported: Dict[str, Dict[str, Any]] = {}
    block_children: Dict[str, List[List[float]]] = {}

    for item in leaves:
        entity = item["entity"]
        kind = str(entity.dxftype())
        handle = str(item.get("handle") or "")
        if kind in ("TEXT", "MTEXT"):
            continue
        if kind not in SUPPORTED_TYPES:
            bucket = unsupported.setdefault(kind, {"type": kind, "count": 0, "handles": [],
                                                   "warning": "本批未支持该实体类型，已登记未丢弃"})
            bucket["count"] += 1
            if handle and len(bucket["handles"]) < 50:
                bucket["handles"].append(handle)
            continue
        try:
            row = _row_for(entity, item, builder, handle)
        except Exception as exc:  # pragma: no cover - 单条实体坏掉不许拖垮整图
            builder.warn("entity_parse_failed", "%s 解析失败，已登记未丢弃（%s）"
                         % (kind, type(exc).__name__), [])
            row = None
        if row is None:
            continue
        _finish_row(row, builder)
        if kind == "DIMENSION":
            dimension_rows.append(_dimension_row(entity, item, handle, builder))
        entity_rows.append(row)
        if row.get("bbox"):
            for step in row.get("block_path") or []:
                ref = str(step.get("insert_handle") or "")
                if ref:
                    block_children.setdefault(ref, []).append(list(row["bbox"]))

    entity_rows.sort(key=model.entity_sort_key)
    for index, row in enumerate(entity_rows, start=1):
        row["canonical_index"] = index
    dimension_rows.sort(key=lambda row: str(row["entity_id"]))

    # INSERT 引用本身的包围盒 = 其展开子实体的并集（引用有范围才方便人工定位）
    for row in entity_rows:
        if row.get("kind") == "insert" and not row.get("bbox"):
            boxes = block_children.get(str(row.get("handle") or "")) or []
            row["bbox"] = [round(float(v), 9) for v in (geometry.union_bbox(boxes) or [])] or None

    for row in entity_rows:
        builder.add_evidence(row["evidence_ref"], {
            "kind": "entity", "handle": row.get("handle") or "",
            "layer": row.get("layer") or "", "spatial": builder.spatial(row.get("bbox")),
            "type": row.get("type") or "",
            **({"block_path": row["block_path"]} if row.get("block_path") else {}),
        })
    for row in text_rows:
        builder.add_evidence(row["evidence_ref"], {
            "kind": "text", "handle": row.get("handle") or "",
            "layer": row.get("layer") or "", "spatial": list(row.get("position") or []),
            "type": row.get("type") or "",
        })
    for row in dimension_rows:
        builder.add_evidence(row["evidence_ref"], {
            "kind": "dimension", "handle": row.get("handle") or "",
            "layer": row.get("layer") or "", "spatial": [],
        })
        builder.add_evidence("ev:D:%s" % (row.get("handle") or ""), {
            "kind": "dimension", "handle": row.get("handle") or "",
            "layer": row.get("layer") or "", "spatial": [],
        })

    # 图层与**顶层**实体计数：模型空间顶层（含文字/标注/未支持类型、不含块展开）
    # —— 与第 2 批 manifest 的 quality 计数同口径，交叉核对才有意义（修复 Spec §7）。
    counts: Dict[str, int] = {}
    top_level = {"entity": 0, "text": 0, "dimension": 0, "block_ref": 0}
    for entity in model_layout:
        layer = str(entity.dxf.get("layer", "0") or "0")
        counts[layer] = counts.get(layer, 0) + 1
        top_level["entity"] += 1
        kind_top = str(entity.dxftype())
        if kind_top in ("TEXT", "MTEXT"):
            top_level["text"] += 1
        elif kind_top == "DIMENSION":
            top_level["dimension"] += 1
        elif kind_top == "INSERT":
            top_level["block_ref"] += 1
    layer_rows = _layer_rows(doc, counts)
    if len(layer_rows) > int(resolved["max_layers"]):
        builder.warn("layer_limit_exceeded",
                     "图层数超过上限 %d，已按上限登记（图层不影响几何正确性）"
                     % int(resolved["max_layers"]))
        layer_rows = layer_rows[:int(resolved["max_layers"])]
    for row in layer_rows:
        builder.add_evidence("ev:L:%s" % row["name"], {
            "kind": "layer", "handle": "", "layer": row["name"], "spatial": [],
            "entity_count": row["entity_count"],
        })

    paper_layouts: List[Dict[str, Any]] = []
    paper_count = 0
    for layout in doc.layouts:
        name = str(getattr(layout, "name", "") or "")
        count = len(list(layout))
        paper_layouts.append({"name": name, "entity_count": count})
        if name != "Model":
            paper_count += count

    if unsupported:
        detail = "、".join("%s×%d" % (key, unsupported[key]["count"]) for key in sorted(unsupported))
        builder.warn("unsupported_entity",
                     "图纸含本批未支持的实体类型（%s），已登记未丢弃（Spec §3.5）" % detail,
                     [])

    stats = _stats(entity_rows, layer_rows, dimension_rows, text_rows, unsupported)
    stats["top_level_entity_total"] = int(top_level["entity"])
    stats["top_level_text_total"] = int(top_level["text"])
    stats["top_level_dimension_total"] = int(top_level["dimension"])
    stats["top_level_block_ref_total"] = int(top_level["block_ref"])

    if builder.truncated_evidence:
        builder.warn("evidence_truncated", "证据条目达到上限，后续节点不再新增证据引用")

    source_block = _source_block(source, model_state, builder)
    _apply_conversion_crosscheck(source_block, stats, builder)

    ir: Dict[str, Any] = {
        "ir_version": model.CAD_IR_VERSION,
        "ir_id": "",
        "ir_hash": "",
        "source": source_block,
        "parser": {"name": model.PARSER_NAME,
                   "version": _parser_version(),
                   "options": {"max_entities": int(resolved["max_entities"]),
                               "max_block_depth": int(resolved["max_block_depth"]),
                               "include_paper_space": bool(resolved["include_paper_space"])}},
        "units": dict(drawing_units, source=drawing_units.get("source", "")),
        "document": {
            "extents": [round(float(value), 9) for value in
                        (geometry.bbox_of(_leaf_points(leaves)) or [0.0, 0.0, 0.0, 0.0])],
            "model_space": {"entity_count": len(entity_rows)},
            "paper_space": {"entity_count": paper_count, "layouts": paper_layouts},
            "layouts": paper_layouts,
            "dxf_version": str(getattr(doc, "dxfversion", "") or ""),
            "warnings": [],
        },
        "layers": layer_rows,
        "entities": entity_rows,
        "geometry": _geometry_block(entity_rows, builder.tolerance),
        "texts": text_rows,
        "dimensions": dimension_rows,
        "unsupported": [unsupported[key] for key in sorted(unsupported)],
        "warnings": sorted(builder.warnings, key=lambda row: (str(row.get("code")),
                                                              str(row.get("message")))),
        "stats": stats,
        "evidence": builder.evidence,
    }
    ir["ir_hash"] = model.ir_hash(ir)
    ir["ir_id"] = ir["ir_hash"][:16]
    return ir


def _parser_version() -> str:
    try:
        import ezdxf
        return str(getattr(ezdxf, "__version__", "") or "")
    except Exception:  # pragma: no cover
        return ""


def _stats(entities: Sequence[Dict[str, Any]], layers: Sequence[Dict[str, Any]],
           dimensions: Sequence[Dict[str, Any]], texts: Sequence[Dict[str, Any]],
           unsupported: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    def count(kind: str) -> int:
        return sum(1 for row in entities if row.get("kind") == kind)
    return {
        "entity_total": len(entities),
        "layer_total": len(layers),
        "closed_outline_total": sum(1 for row in entities
                                    if row.get("kind") == "polyline" and row.get("closed")),
        "open_outline_total": sum(1 for row in entities
                                  if row.get("kind") == "line"
                                  or (row.get("kind") == "polyline" and not row.get("closed"))),
        "arc_total": count("arc"),
        "circle_total": count("circle"),
        "ellipse_total": count("ellipse"),
        "spline_total": count("spline"),
        "dimension_total": len(dimensions),
        "text_total": len(texts),
        "block_ref_total": count("insert"),
        "unsupported_total": sum(int(row.get("count") or 0) for row in unsupported.values()),
    }


def _geometry_block(entities: Sequence[Dict[str, Any]], tolerance: float) -> Dict[str, Any]:
    closed = [row for row in entities if row.get("kind") == "polyline" and row.get("closed")]
    open_rows = [row for row in entities
                 if row.get("kind") == "line"
                 or (row.get("kind") == "polyline" and not row.get("closed"))]
    holes = [row for row in entities
             if row.get("kind") in ("circle", "arc", "ellipse", "spline")]

    def outline(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "outline_id": "out:%s" % str(row.get("entity_id") or "").split(":", 2)[-1],
            "entity_id": row.get("entity_id"), "layer": row.get("layer"),
            "bbox": row.get("bbox"), "length": row.get("length"),
            "area": row.get("area") if row in closed else None,
            "length_mm": row.get("length_mm"),
            "area_mm2": row.get("area_mm2") if row in closed else None,
            "evidence_ref": row.get("evidence_ref"),
        }

    hole_rows: List[Dict[str, Any]] = []
    for row in holes:
        attributes = row.get("attributes") or {}
        hole_rows.append({
            "entity_id": row.get("entity_id"),
            "kind": str(attributes.get("curve") or row.get("kind") or ""),
            "center": attributes.get("center"),
            "diameter": attributes.get("diameter"),
            "radius": attributes.get("radius"),
            "bbox": row.get("bbox"),
            "length": row.get("length"),
            "length_mm": row.get("length_mm"),
            "evidence_ref": row.get("evidence_ref"),
        })

    repeated: Dict[str, List[str]] = {}
    for row in entities:
        if row.get("kind") != "polyline":
            continue
        attributes = row.get("attributes") or {}
        signature = "%s:%s:%s" % (row.get("type"), attributes.get("vertices"),
                                  "closed" if row.get("closed") else "open")
        repeated.setdefault(signature, []).append(str(row.get("entity_id")))
    repeated_groups = [{"signature": key, "entity_ids": value, "count": len(value)}
                       for key, value in sorted(repeated.items()) if len(value) >= 2]

    overlaps: List[Dict[str, Any]] = []
    boxes = [row for row in entities if row.get("bbox")]
    for index in range(len(boxes)):
        for other in range(index + 1, len(boxes)):
            if len(overlaps) >= 200:
                break
            if geometry.boxes_touch(boxes[index]["bbox"], boxes[other]["bbox"], tolerance):
                overlaps.append({"entity_ids": [boxes[index]["entity_id"], boxes[other]["entity_id"]],
                                 "bbox": geometry.union_bbox([boxes[index]["bbox"],
                                                              boxes[other]["bbox"]]),
                                 "kind": "bbox_overlap"})

    return {
        "closed_outlines": [outline(row) for row in closed],
        "open_outlines": [outline(row) for row in open_rows],
        "components": geometry.components_of(closed + open_rows, tolerance),
        "holes": hole_rows,
        "repeated_groups": repeated_groups,
        "overlaps": overlaps,
        "tolerance": float(tolerance),
    }


def _source_block(source: Optional[Dict[str, Any]], state: blocks.Expansion,
                  builder: _Builder) -> Dict[str, Any]:
    given = dict(source or {})
    block: Dict[str, Any] = {
        "kind": str(given.get("kind") or "dxf_2d"),
        "project_id": str(given.get("project_id") or ""),
        "attachment_name": str(given.get("attachment_name") or ""),
        "source_sha256": str(given.get("source_sha256") or ""),
        "conversion_id": str(given.get("conversion_id") or ""),
        "dxf_artifact": str(given.get("dxf_artifact") or ""),
        "dxf_sha256": str(given.get("dxf_sha256") or ""),
        "converter_name": str(given.get("converter_name") or ""),
        "converter_version": str(given.get("converter_version") or ""),
        "detected_dwg_version": str(given.get("detected_dwg_version") or ""),
        "drawing_version": int(given.get("drawing_version") or 1),
    }
    for key in ("conversion_status", "quality", "crosscheck"):
        if given.get(key) not in (None, "", {}):
            block[key] = given[key]
    for key in ("converter_role", "output_version", "primary_failure_code"):
        if given.get(key) not in (None, ""):
            block[key] = str(given[key])
    for key in ("fallback_used", "audit_enabled"):
        if given.get(key) is not None:
            block[key] = bool(given.get(key))
    if given.get("warning_count") is not None:
        block["warning_count"] = int(given.get("warning_count") or 0)
    if given.get("error_count") is not None:
        block["error_count"] = int(given.get("error_count") or 0)
    return block


def _apply_conversion_crosscheck(source_block: Dict[str, Any], stats: Dict[str, int],
                                 builder: _Builder) -> None:
    """与第 2 批 manifest 的交叉核对（Spec §3.9）：计数、降级、回退、缺证据都留痕。"""
    quality = source_block.get("quality") if isinstance(source_block.get("quality"), dict) else {}
    role = str(source_block.get("converter_role") or "")
    if not quality or not role:
        # 老版本产物 / 没给质量证据：**不许**伪造 match=true
        source_block["crosscheck"] = {"match": None, "deltas": {}, "reason": "quality_missing"}
        builder.warn("conversion_quality_missing",
                     "转换 manifest 缺少质量证据（quality / converter_role），本次不做计数核对")
    if quality:
        # 口径必须同源：manifest 的 quality 数的是**模型空间顶层**实体（不展开块引用），
        # 所以这里比对 top_level_* 而不是 `entity_total`（后者含块展开，天然更大）。
        mapping = {"entity_count": "top_level_entity_total", "layer_count": "layer_total",
                   "text_count": "top_level_text_total",
                   "dimension_count": "top_level_dimension_total",
                   "block_ref_count": "top_level_block_ref_total"}
        deltas: Dict[str, int] = {}
        for qkey, skey in mapping.items():
            value = quality.get(qkey)
            if value is None:
                continue
            number = geometry.finite(value)
            if number is None:
                continue
            deltas[qkey] = int(stats.get(skey, 0)) - int(number)
        crosscheck = {"match": all(value == 0 for value in deltas.values()), "deltas": deltas}
        source_block["crosscheck"] = crosscheck
        if not crosscheck["match"]:
            builder.warn("ir_manifest_mismatch",
                         "IR 统计与转换 manifest 的质量计数不一致，已标记待人工核对")
    degraded = (str(source_block.get("conversion_status") or "ok") not in ("", "ok")
                or int(source_block.get("warning_count") or 0) > 0
                or int(source_block.get("error_count") or 0) > 0)
    if degraded:
        builder.warn("conversion_degraded",
                     "转换产物带告警/错误（有损转换，状态 %s / 告警 %s / 错误 %s），字段可信度以质量块为准"
                     % (source_block.get("conversion_status") or "ok",
                        source_block.get("warning_count"), source_block.get("error_count")))
    if bool(source_block.get("fallback_used")):
        builder.warn("conversion_fallback_used",
                     "这份图纸由回退转换器产出（生效 provider=%s，主转换器失败码=%s），相关字段可信度下调"
                     % (source_block.get("converter_name") or "unknown",
                        source_block.get("primary_failure_code") or "unknown"))
