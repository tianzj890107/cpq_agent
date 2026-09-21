"""生成 DWG 第 4 批（包装语义）红测用的 **CAD IR 夹具**。

为什么不用真实文件：
  · 真实 DWG → DXF → CAD IR 的链路属第 2/3 批，随时可能变；
  · 本批要验的是「IR 事实 → 包装语义结论」这段**纯确定性**算法，夹具必须可读、可断言、可复现。

因此本目录下每份 `*.json` 都是一份**合法的 CAD IR 文档**（结构见
`docs/specs/dxf-cad-ir.md` §3），由本脚本生成，**只在夹具需要变更时手工重跑**：
`python tests/fixtures/cad_ir/build_fixtures.py`；`--check` 只校验不写盘。

两条硬约束（写进第 4 批 Spec §11）：
  1. `ir_id` / `ir_hash` 是**不透明版本锚点**：第 4 批只透传，不重算、不校验算法，
     避免与第 3 批的规范化实现耦合；
  2. 夹具里的尺寸都是**测试用的假数据**，不得被读成「酒盒/圆盘盒的真实尺寸」。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURES = pathlib.Path(__file__).resolve().parent

REQUIRED_TOP_KEYS = ("ir_version", "ir_id", "ir_hash", "source", "parser", "units", "document",
                     "layers", "entities", "geometry", "texts", "dimensions", "unsupported",
                     "warnings", "stats", "evidence")

MM_UNITS = {"drawing_units": "mm", "unit_status": "confirmed", "unit_confidence": 1.0,
            "scale_to_mm": 1.0, "candidates": []}
UNITLESS_UNITS = {"drawing_units": "unitless", "unit_status": "needs_confirmation",
                  "unit_confidence": 0.4, "scale_to_mm": None,
                  "candidates": [{"unit": "mm", "confidence": 0.4, "reason": "标注文字含 mm"}]}


def _digest(seed: str, size: int) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:size]


def layer(name, *, color=7, line_type="CONTINUOUS", entity_count=1, visible=True, frozen=False):
    return {"name": name, "visible": visible, "frozen": frozen, "color": color,
            "line_type": line_type, "entity_count": entity_count,
            "inferred_role": None, "role_confidence": None, "evidence_refs": ["ev:L:%s" % name]}


def entity(handle, kind, layer_name, *, closed=None, bbox=None, length=None, area=None,
           space="model", attributes=None):
    row = {"entity_id": "ent:%s:%s" % (space, handle), "handle": str(handle), "type": kind,
           "layer": layer_name, "space": space, "block_path": [], "closed": closed,
           "bbox": bbox, "length": length, "area": area,
           "length_mm": length, "attributes": attributes or {},
           "evidence_ref": "ev:E:%s" % handle}
    return row


def dimension(handle, layer_name, *, declared, measured, tolerance=0.5, unit="mm",
              dim_type="linear", target=None, raw_text=None, confidence=0.8):
    delta = None if declared is None or measured is None else round(declared - measured, 9)
    return {"entity_id": "ent:model:%s" % handle, "layer": layer_name, "dim_type": dim_type,
            "raw_text": str(declared) if raw_text is None else raw_text,
            "normalized_text": str(declared) if declared is not None else "",
            "declared_value": declared, "measured_value": measured, "unit": unit,
            "delta": delta, "tolerance": tolerance, "target_entity_ids": list(target or []),
            "confidence": confidence, "evidence_ref": "ev:D:%s" % handle}


def text(handle, layer_name, raw, normalized, *, kind="MTEXT", position=(0.0, 0.0), height=2.5):
    return {"entity_id": "ent:model:%s" % handle, "type": kind, "layer": layer_name,
            "raw_text": raw, "normalized_text": normalized, "position": list(position),
            "height": height, "evidence_ref": "ev:E:%s" % handle}


def outline(outline_id, entity_id, layer_name, bbox, length, area):
    return {"outline_id": outline_id, "entity_id": entity_id, "layer": layer_name,
            "bbox": list(bbox), "length": length, "area": area, "length_mm": length,
            "area_mm2": area, "evidence_ref": "ev:E:%s" % entity_id.rsplit(":", 1)[-1]}


def hole(entity_id, kind, center, radius):
    return {"entity_id": entity_id, "kind": kind, "center": list(center), "diameter": radius * 2,
            "radius": radius, "evidence_ref": "ev:E:%s" % entity_id.rsplit(":", 1)[-1]}


def make(name, *, layers, entities, geometry=None, texts=(), dimensions=(), units=None,
         warnings=(), unsupported=(), extra_evidence=(), drawing_version=1):
    handle_of = {item["entity_id"]: item["handle"] for item in entities}
    evidence = {}
    for item in layers:
        evidence["ev:L:%s" % item["name"]] = {"kind": "layer", "handle": "", "layer": item["name"],
                                             "spatial": [0.0, 0.0]}
    for item in entities:
        evidence["ev:E:%s" % item["handle"]] = {"kind": "entity", "handle": item["handle"],
                                                "layer": item["layer"], "spatial": [0.0, 0.0]}
    for item in dimensions:
        evidence["ev:D:%s" % handle_of.get(item["entity_id"], item["entity_id"].rsplit(":", 1)[-1])] = {
            "kind": "dimension", "handle": item["entity_id"].rsplit(":", 1)[-1],
            "layer": item["layer"], "spatial": [0.0, 0.0]}
    for ref, payload in extra_evidence:
        evidence[ref] = payload
    geometry = geometry or {"closed_outlines": [], "open_outlines": [], "components": [], "holes": [],
                            "repeated_groups": [], "overlaps": [], "tolerance": 1e-6}
    stats = {"entity_total": len(entities), "layer_total": len(layers),
             "closed_outline_total": len(geometry.get("closed_outlines") or []),
             "open_outline_total": len(geometry.get("open_outlines") or []),
             "arc_total": sum(1 for e in entities if e["type"] == "ARC"),
             "circle_total": sum(1 for e in entities if e["type"] == "CIRCLE"),
             "ellipse_total": sum(1 for e in entities if e["type"] == "ELLIPSE"),
             "spline_total": sum(1 for e in entities if e["type"] == "SPLINE"),
             "dimension_total": len(dimensions), "text_total": len(texts),
             "block_ref_total": sum(1 for e in entities if e["type"] == "INSERT"),
             "unsupported_total": len(unsupported)}
    return {
        "ir_version": "cad-ir/1",
        "ir_id": _digest(name, 16),
        "ir_hash": _digest(name + ":hash", 64),
        "source": {"kind": "dwg_2d", "project_id": "", "attachment_name": "fixture.dwg",
                   "source_sha256": _digest(name + ":src", 64), "conversion_id": _digest(name + ":cv", 16),
                   "dxf_artifact": "converted.dxf", "dxf_sha256": _digest(name + ":dxf", 64),
                   "converter_name": "libredwg", "converter_version": "0.14",
                   "detected_dwg_version": "AC1027", "drawing_version": drawing_version},
        "parser": {"name": "ezdxf", "version": "1.4.4", "options": {}},
        "units": units or dict(MM_UNITS),
        "document": {"extents": [0.0, 0.0, 200.0, 300.0], "model_space": {"entity_count": len(entities)},
                     "paper_space": {"entity_count": 0},
                     "layouts": [{"name": "Model", "entity_count": len(entities)}],
                     "dxf_version": "AC1027", "warnings": []},
        "layers": list(layers),
        "entities": list(entities),
        "geometry": geometry,
        "texts": list(texts),
        "dimensions": list(dimensions),
        "unsupported": list(unsupported),
        "warnings": list(warnings),
        "stats": stats,
        "evidence": evidence,
    }


# --------------------------------------------------------------------------- #
# 夹具定义
# --------------------------------------------------------------------------- #
def cut_crease_layers():
    return make(
        "cut_crease_layers",
        layers=[layer("CUT", entity_count=1), layer("CREASE", entity_count=2),
                layer("PRINT", entity_count=1), layer("FRAME", entity_count=1),
                layer("MYSTERY_LAYER", entity_count=1)],
        entities=[entity("10", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 100, 60],
                         length=320.0, area=6000.0),
                  entity("11", "LINE", "CREASE", length=100.0, bbox=[0, 30, 100, 30]),
                  entity("12", "LINE", "CREASE", length=60.0, bbox=[50, 0, 50, 60]),
                  entity("13", "TEXT", "PRINT", bbox=[4, 4, 24, 8]),
                  entity("14", "LWPOLYLINE", "FRAME", closed=True, bbox=[-10, -10, 210, 310],
                         length=1040.0, area=64000.0),
                  entity("15", "LINE", "MYSTERY_LAYER", length=12.0, bbox=[1, 1, 13, 1])],
        geometry={"closed_outlines": [
                      outline("out:model:10", "ent:model:10", "CUT", [0, 0, 100, 60], 320.0, 6000.0),
                      outline("out:model:14", "ent:model:14", "FRAME", [-10, -10, 210, 310],
                              1040.0, 64000.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
    )


def color_only_layers():
    # 图层名无意义（LAYER1/LAYER2），角色只能靠颜色配置解释 —— 而颜色含义不许硬编码。
    return make(
        "color_only_layers",
        layers=[layer("LAYER1", color=3, entity_count=1),
                layer("LAYER2", color=5, entity_count=2),
                layer("LAYER3", color=1, entity_count=1)],
        entities=[entity("20", "LWPOLYLINE", "LAYER1", closed=True, bbox=[0, 0, 80, 40],
                         length=240.0, area=3200.0),
                  entity("21", "LINE", "LAYER2", length=80.0, bbox=[0, 20, 80, 20]),
                  entity("22", "LINE", "LAYER2", length=40.0, bbox=[40, 0, 40, 40]),
                  entity("23", "LINE", "LAYER3", length=9.0, bbox=[2, 2, 11, 2])],
        geometry={"closed_outlines": [outline("out:model:20", "ent:model:20", "LAYER1",
                                             [0, 0, 80, 40], 240.0, 3200.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
    )


def dashed_crease_layer():
    # 只有线型（DASHED）能当证据：必须只是弱证据，不许直接判成压痕。
    return make(
        "dashed_crease_layer",
        layers=[layer("LAYER_A", color=7, line_type="DASHED", entity_count=1),
                layer("LAYER_B", color=7, line_type="CONTINUOUS", entity_count=1)],
        entities=[entity("30", "LINE", "LAYER_A", length=100.0, bbox=[0, 20, 100, 20]),
                  entity("31", "LWPOLYLINE", "LAYER_B", closed=True, bbox=[0, 0, 100, 60],
                         length=320.0, area=6000.0)],
        geometry={"closed_outlines": [outline("out:model:31", "ent:model:31", "LAYER_B",
                                             [0, 0, 100, 60], 320.0, 6000.0)],
                  "open_outlines": [{"outline_id": "out:model:30", "entity_id": "ent:model:30",
                                     "layer": "LAYER_A", "bbox": [0, 20, 100, 20], "length": 100.0,
                                     "area": None, "length_mm": 100.0, "area_mm2": None,
                                     "evidence_ref": "ev:E:30"}],
                  "components": [], "holes": [], "repeated_groups": [], "overlaps": [],
                  "tolerance": 1e-6},
    )


def dim_conflict():
    # 标注写 70，几何实测 72 → 必须形成冲突，不许静默选一个。
    return make(
        "dim_conflict",
        layers=[layer("CUT", entity_count=1), layer("DIM", entity_count=1)],
        entities=[entity("40", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 72, 50],
                         length=244.0, area=3600.0),
                  entity("41", "DIMENSION", "DIM", bbox=[0, -8, 72, -6])],
        geometry={"closed_outlines": [outline("out:model:40", "ent:model:40", "CUT",
                                             [0, 0, 72, 50], 244.0, 3600.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
        dimensions=[dimension("41", "DIM", declared=70.0, measured=72.0, tolerance=0.5,
                              target=["ent:model:40"])],
    )


def dim_conflict_tolerance():
    return make(
        "dim_conflict_tolerance",
        layers=[layer("CUT", entity_count=1), layer("DIM", entity_count=1)],
        entities=[entity("50", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 70, 50],
                         length=240.0, area=3500.0),
                  entity("51", "DIMENSION", "DIM", bbox=[0, -8, 70, -6])],
        geometry={"closed_outlines": [outline("out:model:50", "ent:model:50", "CUT",
                                             [0, 0, 70, 50], 240.0, 3500.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
        dimensions=[dimension("51", "DIM", declared=70.0, measured=70.4, tolerance=0.5,
                              target=["ent:model:50"])],
    )


def unitless_dimensions():
    # 单位未确认 + 标注与几何完全一致：仍然不许确认绝对尺寸。
    return make(
        "unitless_dimensions",
        layers=[layer("CUT", entity_count=1), layer("DIM", entity_count=1)],
        entities=[entity("60", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 70, 50],
                         length=240.0, area=3500.0),
                  entity("61", "DIMENSION", "DIM", bbox=[0, -8, 70, -6])],
        geometry={"closed_outlines": [outline("out:model:60", "ent:model:60", "CUT",
                                             [0, 0, 70, 50], 240.0, 3500.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
        dimensions=[dimension("61", "DIM", declared=70.0, measured=70.0, tolerance=0.5,
                              unit="unitless", target=["ent:model:60"])],
        units=dict(UNITLESS_UNITS),
    )


def material_texts():
    return make(
        "material_texts",
        layers=[layer("CUT", entity_count=1), layer("PRINT", entity_count=2)],
        entities=[entity("70", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 100, 60],
                         length=320.0, area=6000.0),
                  entity("71", "MTEXT", "PRINT", bbox=[4, 60, 60, 66]),
                  entity("72", "MTEXT", "PRINT", bbox=[4, 52, 60, 58])],
        geometry={"closed_outlines": [outline("out:model:70", "ent:model:70", "CUT",
                                             [0, 0, 100, 60], 320.0, 6000.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
        texts=[text("71", "PRINT", "材质：白卡纸 350g", "材质：白卡纸 350g", position=[4, 63]),
               text("72", "PRINT", "工艺：烫金", "工艺：烫金", position=[4, 55])],
    )


def no_material_texts():
    return make(
        "no_material_texts",
        layers=[layer("CUT", entity_count=1), layer("DIM", entity_count=1)],
        entities=[entity("80", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 100, 60],
                         length=320.0, area=6000.0),
                  entity("81", "TEXT", "DIM", bbox=[4, 62, 40, 66])],
        geometry={"closed_outlines": [outline("out:model:80", "ent:model:80", "CUT",
                                             [0, 0, 100, 60], 320.0, 6000.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
        texts=[text("81", "DIM", "100", "100", kind="TEXT", position=[4, 64])],
    )


def hole_plate():
    return make(
        "hole_plate",
        layers=[layer("CUT", entity_count=3)],
        entities=[entity("90", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 40, 20],
                         length=120.0, area=800.0),
                  entity("91", "CIRCLE", "CUT", closed=True, bbox=[8, 8, 14, 14], length=18.8496,
                         area=28.2743),
                  entity("92", "CIRCLE", "CUT", closed=True, bbox=[26, 8, 32, 14], length=18.8496,
                         area=28.2743)],
        geometry={"closed_outlines": [outline("out:model:90", "ent:model:90", "CUT",
                                             [0, 0, 40, 20], 120.0, 800.0)],
                  "open_outlines": [], "components": [],
                  "holes": [hole("ent:model:91", "circle", [11.0, 11.0], 3.0),
                            hole("ent:model:92", "circle", [29.0, 11.0], 3.0)],
                  "repeated_groups": [], "overlaps": [], "tolerance": 1e-6},
    )


def multi_panel():
    return make(
        "multi_panel",
        layers=[layer("CUT", entity_count=2)],
        entities=[entity("A0", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 60, 40],
                         length=200.0, area=2400.0),
                  entity("A1", "LWPOLYLINE", "CUT", closed=True, bbox=[80, 0, 140, 40],
                         length=200.0, area=2400.0)],
        geometry={"closed_outlines": [outline("out:model:A0", "ent:model:A0", "CUT",
                                             [0, 0, 60, 40], 200.0, 2400.0),
                                     outline("out:model:A1", "ent:model:A1", "CUT",
                                             [80, 0, 140, 40], 200.0, 2400.0)],
                  "open_outlines": [], "components": [], "holes": [],
                  "repeated_groups": [{"signature": "LWPOLYLINE:4:closed",
                                       "entity_ids": ["ent:model:A0", "ent:model:A1"], "count": 2}],
                  "overlaps": [], "tolerance": 1e-6},
    )


def round_outline():
    # 圆形闭合外轮廓 → 只允许出 round_tube **候选**。
    return make(
        "round_outline",
        layers=[layer("CUT", entity_count=1)],
        entities=[entity("B0", "CIRCLE", "CUT", closed=True, bbox=[0, 0, 120, 120],
                         length=376.9911, area=11309.7336)],
        geometry={"closed_outlines": [], "open_outlines": [], "components": [],
                  "holes": [hole("ent:model:B0", "circle", [60.0, 60.0], 60.0)],
                  "repeated_groups": [], "overlaps": [], "tolerance": 1e-6},
    )


def ambiguous_layers():
    return make(
        "ambiguous_layers",
        layers=[layer("MYSTERY_1", color=2, entity_count=1),
                layer("MYSTERY_2", color=4, entity_count=1),
                layer("MYSTERY_3", color=6, entity_count=1)],
        entities=[entity("C0", "LINE", "MYSTERY_1", length=18.0, bbox=[0, 0, 18, 0]),
                  entity("C1", "LINE", "MYSTERY_2", length=7.5, bbox=[0, 5, 7.5, 5]),
                  entity("C2", "SPLINE", "MYSTERY_3")],
        geometry={"closed_outlines": [], "open_outlines": [], "components": [], "holes": [],
                  "repeated_groups": [], "overlaps": [], "tolerance": 1e-6},
    )


def warnings_passthrough():
    return make(
        "warnings_passthrough",
        layers=[layer("CUT", entity_count=1), layer("FRAME", entity_count=1)],
        entities=[entity("D0", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 90, 50],
                         length=280.0, area=4500.0),
                  entity("D1", "3DFACE", "FRAME", bbox=[0, 0, 10, 10])],
        geometry={"closed_outlines": [outline("out:model:D0", "ent:model:D0", "CUT",
                                             [0, 0, 90, 50], 280.0, 4500.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
        unsupported=[{"type": "3DFACE", "count": 1, "handles": ["D1"],
                      "warning": "不支持的实体已跳过"}],
        warnings=[{"code": "conversion_degraded",
                   "message": "上游转换有 252 条警告、3 条错误", "evidence_refs": []},
                  {"code": "unsupported_entity", "message": "1 个 3DFACE 未解析",
                   "evidence_refs": ["ev:E:D1"]}],
    )


def no_preview():
    return make(
        "no_preview",
        layers=[layer("CUT", entity_count=1)],
        entities=[entity("E0", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 100, 60],
                         length=320.0, area=6000.0)],
        geometry={"closed_outlines": [outline("out:model:E0", "ent:model:E0", "CUT",
                                             [0, 0, 100, 60], 320.0, 6000.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
    )


def extra_field_model():
    return make(
        "extra_field_model",
        layers=[layer("CUT", entity_count=1), layer("PRINT", entity_count=1)],
        entities=[entity("F0", "LWPOLYLINE", "CUT", closed=True, bbox=[0, 0, 100, 60],
                         length=320.0, area=6000.0),
                  entity("F1", "MTEXT", "PRINT", bbox=[4, 60, 60, 66])],
        geometry={"closed_outlines": [outline("out:model:F0", "ent:model:F0", "CUT",
                                             [0, 0, 100, 60], 320.0, 6000.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
        texts=[text("F1", "PRINT", "材质：白卡纸 350g", "材质：白卡纸 350g", position=[4, 63])],
    )


def _rect_panel(code, x0, y0, x1, y1, layer_name="CUT"):
    """一块矩形面板：4 条 LINE 拼出来（真实刀模图就是这样，零件不是闭合环）。

    返回 (entities, open_outlines, component)。分量 bbox 由生成器直接给出 —— 真实 CAD IR
    的 `geometry.components` 也是这样带着 bbox 的（第 3 批算好，第 4/本批只读）。
    """
    handles = ["%s%d" % (code, index + 1) for index in range(4)]
    boxes = [[x0, y0, x1, y0], [x1, y0, x1, y1], [x0, y1, x1, y1], [x0, y0, x0, y1]]
    lengths = [x1 - x0, y1 - y0, x1 - x0, y1 - y0]
    ents = [entity(handle, "LINE", layer_name, bbox=box, length=length)
            for handle, box, length in zip(handles, boxes, lengths)]
    outs = [outline("out:%s" % handle, "ent:model:%s" % handle, layer_name, box, length, None)
            for handle, box, length in zip(handles, boxes, lengths)]
    comp = {"component_id": "cmp:%s" % code,
            "entity_ids": ["ent:model:%s" % handle for handle in handles],
            "bbox": [x0, y0, x1, y1], "closed_cycles": 0}
    return ents, outs, comp


def parts_panels():
    """刀模/展开图形态的多零件夹具（零件由多条 LINE 拼出 + 整版框 + 标注 + 碎线）。

    形态照真实 `酒盒.dwg` 的 CAD IR 统计取（**同构，非真实尺寸**）：
    `closed_outline_total = 2` / `open_outline_total = 5598` / `components = 402`，
    所以「零件 = 闭合轮廓」在真图上不成立 —— 必须按连通分量聚合。

    7 个分量：4 件过筛（盒身展开 / 盖面 / 盖壁条 / 内托），3 件被筛
    （整版框 = 长边超限、碎线 = 面积过小、纯文字组 = 没有可制造曲线）。
    """
    body_e, body_o, body_c = _rect_panel("B", 100.0, 100.0, 331.2, 296.0)
    wall_e, wall_o, wall_c = _rect_panel("W", 100.0, 320.0, 331.2, 365.0)
    insert_e, insert_o, insert_c = _rect_panel("I", 700.0, 100.0, 760.0, 150.0,
                                               layer_name="INSERT")
    frame_e, frame_o, frame_c = _rect_panel("F", 0.0, 0.0, 1400.0, 1000.0, layer_name="0")
    tiny_e, tiny_o, tiny_c = _rect_panel("T", 900.0, 900.0, 908.0, 908.0)
    # 盖面：闭合多段线（真图里那 2 条闭合环的同构体）。
    lid_e = [entity("L1", "LWPOLYLINE", "CUT", closed=True, bbox=[400.0, 100.0, 631.2, 200.8],
                    length=2 * (231.2 + 100.8), area=231.2 * 100.8)]
    lid_o = [outline("out:L1", "ent:model:L1", "CUT", [400.0, 100.0, 631.2, 200.8],
                     2 * (231.2 + 100.8), 231.2 * 100.8)]
    lid_c = {"component_id": "cmp:L", "entity_ids": ["ent:model:L1"],
             "bbox": [400.0, 100.0, 631.2, 200.8], "closed_cycles": 1}
    # 压痕线：与盒身刀线共顶点 → 同一分量（考"件内角色优先级 cut > crease"）。
    crease = entity("B5", "LINE", "CREASE", bbox=[200.0, 296.0, 331.2, 296.0], length=131.2)
    body_c = dict(body_c, entity_ids=list(body_c["entity_ids"]) + ["ent:model:B5"])
    # 纯文字组：没有可制造曲线 → 必须被筛掉。
    text_e = [entity("X1", "MTEXT", "TEXT", bbox=[0.0, 1010.0, 40.0, 1014.0]),
              entity("X2", "MTEXT", "TEXT", bbox=[0.0, 1020.0, 40.0, 1024.0])]
    text_c = {"component_id": "cmp:X",
              "entity_ids": ["ent:model:X1", "ent:model:X2"],
              "bbox": [0.0, 1010.0, 40.0, 1024.0], "closed_cycles": 0}
    return make(
        "parts_panels",
        layers=[layer("CUT", entity_count=18), layer("CREASE", entity_count=1),
                layer("INSERT", entity_count=4), layer("TEXT", entity_count=2),
                layer("0", entity_count=4)],
        entities=body_e + [crease] + lid_e + wall_e + insert_e + frame_e + tiny_e + text_e,
        texts=[text("X1", "TEXT", "Flute/Grain", "Flute/Grain", position=[0.0, 1011.0]),
               text("X2", "TEXT", "酒盒 700ML", "酒盒 700ML", position=[0.0, 1021.0])],
        geometry={"closed_outlines": lid_o,
                  "open_outlines": body_o + wall_o + insert_o + frame_o + tiny_o
                                   + [outline("out:B5", "ent:model:B5", "CREASE",
                                              [200.0, 296.0, 331.2, 296.0], 131.2, None)],
                  "components": [body_c, lid_c, wall_c, insert_c, frame_c, tiny_c, text_c],
                  "holes": [], "repeated_groups": [], "overlaps": [], "tolerance": 1e-6},
    )


BUILDERS = (cut_crease_layers, color_only_layers, dashed_crease_layer, dim_conflict,
            dim_conflict_tolerance, unitless_dimensions, material_texts, no_material_texts,
            hole_plate, multi_panel, round_outline, ambiguous_layers, warnings_passthrough,
            no_preview, extra_field_model, parts_panels)


def name_of(fn):
    return fn.__name__


def check(doc):
    problems = []
    for key in REQUIRED_TOP_KEYS:
        if key not in doc:
            problems.append("缺少顶层键 %s" % key)
    if doc.get("ir_version") != "cad-ir/1":
        problems.append("ir_version 必须是 cad-ir/1")
    evidence = doc.get("evidence") or {}
    refs = []
    for item in doc.get("entities") or []:
        refs.append(item.get("evidence_ref"))
    for item in doc.get("texts") or []:
        refs.append(item.get("evidence_ref"))
    for item in doc.get("dimensions") or []:
        refs.append(item.get("evidence_ref"))
    for group in ("closed_outlines", "open_outlines", "holes"):
        for item in (doc.get("geometry") or {}).get(group) or []:
            refs.append(item.get("evidence_ref"))
    for item in doc.get("layers") or []:
        refs.extend(item.get("evidence_refs") or [])
    for ref in refs:
        if ref and ref not in evidence:
            problems.append("证据引用无法回查：%s" % ref)
    blob = json.dumps(doc, ensure_ascii=False, allow_nan=False)
    if "NaN" in blob or "Infinity" in blob:
        problems.append("出现 NaN/Infinity")
    return problems


def main(argv):
    write = "--check" not in argv
    failures = 0
    for fn in BUILDERS:
        name = name_of(fn)
        doc = fn()
        problems = check(doc)
        path = FIXTURES / ("%s.json" % name)
        if problems:
            failures += 1
            print("FAIL %-28s %s" % (name, "; ".join(problems)))
            continue
        if write:
            # 稳定落盘：sort_keys + 固定缩进，同一脚本两次运行字节一致。
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
        print("%-28s %s %s" % (name, "wrote" if write else "checked", path.name))
    if failures:
        print("fixtures 校验失败：%d 个" % failures)
        return 1
    print("wrote %d fixtures" % len(BUILDERS) if write else "checked %d fixtures" % len(BUILDERS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
