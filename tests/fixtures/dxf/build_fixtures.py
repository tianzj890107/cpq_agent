"""生成 DXF 解析红测用的小夹具（只跑一次，产物已入库；测试直接读 .dxf，不调用本脚本）。

两类夹具：
  · 手写最小 ASCII DXF（AC1015/AC1009）——简单实体，便于人工核对坐标与句柄；
  · 用 ezdxf 生成 —— 块/嵌套/镜像/标注这些结构手写容易出错（生成期需要 ezdxf，运行期不需要）。

用法：./open-claude/.venv/bin/python tests/fixtures/dxf/build_fixtures.py [--check]
      --check 只校验已入库夹具能否被 ezdxf 读回，不重写文件。
"""
from __future__ import annotations

import pathlib
import sys
import textwrap

HERE = pathlib.Path(__file__).resolve().parent
LAYER_TABLE = [
    ("0", 7, "CONTINUOUS"), ("CUT", 1, "CONTINUOUS"), ("CREASE", 3, "DASHED"),
    ("PRINT", 5, "CONTINUOUS"), ("FRAME", 8, "CONTINUOUS"),
]


def tags(*pairs) -> str:
    """把 (组码, 值) 序列写成 DXF 组码行；值里的换行不允许出现（DXF 是逐行格式）。"""
    out = []
    for code, value in pairs:
        out.append(str(code))
        out.append(str(value))
    return "\n".join(out) + "\n"


def unicode_escape(value: str) -> str:
    """把非 ASCII 字符写成 AutoCAD 的 `\\U+XXXX` 转义（真实 DXF 就是这么存中文的）。

    夹具因此保持纯 ASCII，解析器必须自己解码 —— 这与真实图纸一致，也让"文字乱码"
    这类缺陷能被红测抓住。
    """
    out = []
    for char in value:
        out.append(char if ord(char) < 128 else "\\U+%04X" % ord(char))
    return "".join(out)


def header(insunits: int | None, version: str = "AC1015") -> str:
    pairs = [(0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, version)]
    pairs += [(9, "$DWGCODEPAGE"), (3, "ANSI_1252")]
    if insunits is not None:
        pairs += [(9, "$INSUNITS"), (70, insunits)]
    pairs += [(0, "ENDSEC")]
    return tags(*pairs)


def tables() -> str:
    out = tags((0, "SECTION"), (2, "TABLES"), (0, "TABLE"), (2, "LTYPE"), (70, 2))
    out += tags((0, "LTYPE"), (2, "CONTINUOUS"), (70, 0), (3, "Solid line"), (72, 65),
                (73, 0), (40, 0.0))
    out += tags((0, "LTYPE"), (2, "DASHED"), (70, 0), (3, "__ __ __"), (72, 65),
                (73, 2), (40, 0.75), (49, 0.5), (74, 0), (49, -0.25), (74, 0))
    out += tags((0, "ENDTAB"), (0, "TABLE"), (2, "LAYER"), (70, len(LAYER_TABLE)))
    for name, color, ltype in LAYER_TABLE:
        out += tags((0, "LAYER"), (2, name), (70, 0), (62, color), (6, ltype))
    out += tags((0, "ENDTAB"), (0, "ENDSEC"))
    return out


def body(*entities: str) -> str:
    return tags((0, "SECTION"), (2, "ENTITIES")) + "".join(entities) + tags((0, "ENDSEC"), (0, "EOF"))


def ent(kind: str, handle: str, layer: str, subclass: str, pairs) -> str:
    head = tags((0, kind), (5, handle), (8, layer), (100, "AcDbEntity"), (100, subclass))
    return head + tags(*pairs)


def lwpolyline(handle, layer, points, closed=False) -> str:
    pairs = [(90, len(points)), (70, 1 if closed else 0)]
    for x, y in points:
        pairs += [(10, float(x)), (20, float(y))]
    return ent("LWPOLYLINE", handle, layer, "AcDbPolyline", pairs)


def line(handle, layer, start, end) -> str:
    return ent("LINE", handle, layer, "AcDbLine", [
        (10, float(start[0])), (20, float(start[1])), (30, 0.0),
        (11, float(end[0])), (21, float(end[1])), (31, 0.0)])


def circle(handle, layer, center, radius) -> str:
    return ent("CIRCLE", handle, layer, "AcDbCircle", [
        (10, float(center[0])), (20, float(center[1])), (30, 0.0), (40, float(radius))])


def arc(handle, layer, center, radius, start, end) -> str:
    return ent("ARC", handle, layer, "AcDbCircle", [
        (10, float(center[0])), (20, float(center[1])), (30, 0.0), (40, float(radius)),
        (100, "AcDbArc"), (50, float(start)), (51, float(end))])


def ellipse(handle, layer, center, major, ratio) -> str:
    return ent("ELLIPSE", handle, layer, "AcDbEllipse", [
        (10, float(center[0])), (20, float(center[1])), (30, 0.0),
        (11, float(major[0])), (21, float(major[1])), (31, 0.0),
        (40, float(ratio)), (41, 0.0), (42, 6.283185307179586)])


def spline(handle, layer, points, degree=3) -> str:
    count = len(points)
    knots = [0.0] * (degree + 1) + [1.0] * (degree + 1)
    pairs = [(70, 8), (71, degree), (72, len(knots)), (73, count), (74, 0)]
    pairs += [(40, k) for k in knots]
    for x, y in points:
        pairs += [(10, float(x)), (20, float(y)), (30, 0.0)]
    return ent("SPLINE", handle, layer, "AcDbSpline", pairs)


def text(handle, layer, position, height, value) -> str:
    return ent("TEXT", handle, layer, "AcDbText", [
        (10, float(position[0])), (20, float(position[1])), (30, 0.0),
        (40, float(height)), (1, unicode_escape(value))])


def mtext(handle, layer, position, height, value) -> str:
    return ent("MTEXT", handle, layer, "AcDbMText", [
        (10, float(position[0])), (20, float(position[1])), (30, 0.0),
        (40, float(height)), (71, 1), (72, 5), (1, unicode_escape(value))])


def face3d(handle, layer, points) -> str:
    pairs = []
    for index, (x, y, z) in enumerate(points[:4]):
        code = 10 + index
        pairs += [(code, float(x)), (code + 10, float(y)), (code + 20, float(z))]
    return ent("3DFACE", handle, layer, "AcDbFace", pairs)


def write(name: str, content: str) -> pathlib.Path:
    target = HERE / name
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def build_simple() -> list[str]:
    made = []

    rect = [(-0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
    write("rect_10x5.dxf", header(4) + tables() + body(lwpolyline("10", "0", rect, True)))
    made.append("rect_10x5.dxf")

    write("hole_plate.dxf", header(4) + tables() + body(
        lwpolyline("20", "0", [(0, 0), (40, 0), (40, 20), (0, 20)], True),
        circle("21", "0", (20, 10), 3.0),
        circle("22", "0", (10, 10), 1.5),
    ))
    made.append("hole_plate.dxf")

    write("open_polyline.dxf", header(4) + tables() + body(
        lwpolyline("30", "0", [(0, 0), (10, 0), (10, 10)], False),
        line("31", "0", (0, 0), (0, 10)),
    ))
    made.append("open_polyline.dxf")

    write("curves_arc_ellipse_spline.dxf", header(4) + tables() + body(
        arc("40", "0", (0, 0), 10.0, 0.0, 90.0),
        circle("41", "0", (0, 0), 2.0),
        ellipse("42", "0", (50, 50), (20.0, 0.0), 0.5),
        spline("43", "0", [(0, 0), (5, 8), (10, 0), (15, 6)]),
    ))
    made.append("curves_arc_ellipse_spline.dxf")

    write("layers_cut_crease.dxf", header(4) + tables() + body(
        lwpolyline("50", "CUT", [(0, 0), (30, 0), (30, 20), (0, 20)], True),
        lwpolyline("51", "CREASE", [(5, 10), (25, 10)], False),
        lwpolyline("52", "CREASE", [(15, 2), (15, 18)], False),
        mtext("53", "PRINT", (2, 22), 2.5, "材质：白卡纸 350g"),
        line("54", "FRAME", (-5, -5), (35, -5)),
    ))
    made.append("layers_cut_crease.dxf")

    write("units_unitless.dxf", header(0) + tables() + body(
        lwpolyline("60", "0", [(0, 0), (10, 0), (10, 10), (0, 10)], True),
        text("61", "0", (12, 12), 2.0, "70mm"),
    ))
    made.append("units_unitless.dxf")

    write("units_inch.dxf", header(1) + tables() + body(
        lwpolyline("62", "0", [(0, 0), (10, 0), (10, 10), (0, 10)], True),
    ))
    made.append("units_inch.dxf")

    write("unsupported_entities.dxf", header(4) + tables() + body(
        lwpolyline("70", "0", [(0, 0), (20, 0), (20, 10), (0, 10)], True),
        face3d("71", "0", [(0, 0, 0), (5, 0, 0), (5, 5, 1), (0, 5, 1)]),
        tags((0, "REGION"), (5, "72"), (8, "0"), (100, "AcDbEntity"), (100, "AcDbModelerGeometry")),
    ))
    made.append("unsupported_entities.dxf")

    many = "".join(line("%X" % (0x100 + index), "0", (float(index), 0.0),
                        (float(index), 1.0)) for index in range(200))
    write("many_entities.dxf", header(4) + tables() + body(many))
    made.append("many_entities.dxf")

    shared = [line("A0", "0", (0, 0), (10, 0)),
              circle("A1", "0", (3, 3), 2.0),
              lwpolyline("A2", "CUT", [(0, 0), (4, 0), (4, 4), (0, 4)], True)]
    write("order_a.dxf", header(4) + tables() + body(*shared))
    write("order_b.dxf", header(4) + tables() + body(shared[2], shared[0], shared[1]))
    made += ["order_a.dxf", "order_b.dxf"]

    polyline_r12 = tags((0, "POLYLINE"), (5, "B0"), (8, "0"), (66, 1), (70, 1))
    for handle, (x, y) in zip(("B1", "B2", "B3"), [(0, 0), (10, 0), (10, 10)]):
        polyline_r12 += tags((0, "VERTEX"), (5, handle), (8, "0"), (10, float(x)), (20, float(y)))
    polyline_r12 += tags((0, "SEQEND"), (5, "B9"), (8, "0"))
    write("r12_polyline.dxf", header(None, "AC1009") + tables() + body(polyline_r12))
    made.append("r12_polyline.dxf")

    write("broken.dxf", "0\nSECTION\n2\nENTITIES\n0\nLWPOLYLINE\n90\n4\n10\n0.0\n")
    made.append("broken.dxf")
    return made


def build_with_ezdxf() -> list[str]:
    import ezdxf
    made = []

    def new(insunits=4):
        doc = ezdxf.new("R2000", setup=False)
        doc.header["$INSUNITS"] = insunits
        for name, color, ltype in LAYER_TABLE:
            if name != "0" and name not in doc.layers:
                doc.layers.add(name, color=color, linetype=ltype)
        return doc

    doc = new()
    block = doc.blocks.new("RECT10x5")
    block.add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)], close=True)
    msp = doc.modelspace()
    msp.add_blockref("RECT10x5", (100, 0), dxfattribs={"rotation": 90.0})
    doc.saveas(HERE / "block_rotated.dxf")
    made.append("block_rotated.dxf")

    doc = new()
    inner = doc.blocks.new("INNER")
    inner.add_lwpolyline([(0, 0), (4, 0), (4, 2), (0, 2)], close=True)
    outer = doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (10, 0))
    outer.add_blockref("INNER", (0, 20), dxfattribs={"rotation": 90.0})
    msp = doc.modelspace()
    msp.add_blockref("OUTER", (0, 0))
    msp.add_blockref("OUTER", (50, 50))
    doc.saveas(HERE / "block_nested.dxf")
    made.append("block_nested.dxf")

    doc = new()
    shape = doc.blocks.new("LSHAPE")
    shape.add_lwpolyline([(0, 0), (10, 0), (10, 3), (3, 3), (3, 10), (0, 10)], close=True)
    msp = doc.modelspace()
    msp.add_blockref("LSHAPE", (0, 0))
    msp.add_blockref("LSHAPE", (20, 0), dxfattribs={"xscale": -1.0})
    doc.saveas(HERE / "block_mirror.dxf")
    made.append("block_mirror.dxf")

    doc = new()
    shape = doc.blocks.new("LSHAPE")
    shape.add_lwpolyline([(0, 0), (10, 0), (10, 3), (3, 3), (3, 10), (0, 10)], close=True)
    msp = doc.modelspace()
    msp.add_blockref("LSHAPE", (0, 0))
    flipped = msp.add_blockref("LSHAPE", (20, 0), dxfattribs={"xscale": -1.0})
    flipped.dxf.extrusion = (0.0, 0.0, -1.0)
    doc.saveas(HERE / "block_mirror_flip.dxf")
    made.append("block_mirror_flip.dxf")

    doc = new()
    block_a = doc.blocks.new("A")
    block_a.add_lwpolyline([(0, 0), (2, 0), (2, 2), (0, 2)], close=True)
    block_b = doc.blocks.new("B")
    block_b.add_blockref("A", (0, 0))
    block_a.add_blockref("B", (5, 0))
    msp = doc.modelspace()
    msp.add_blockref("A", (0, 0))
    doc.saveas(HERE / "block_cycle.dxf")
    made.append("block_cycle.dxf")

    doc = new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (72, 0), (72, 20), (0, 20)], close=True)
    dim = msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(72, 0),
                             override={"dimtxt": 2.5})
    dim.dimension.dxf.text = "70"
    dim.render()
    msp.add_text(unicode_escape("单位：mm"),
                dxfattribs={"height": 2.5}).set_placement((0, 25))
    doc.saveas(HERE / "dims_override.dxf")
    made.append("dims_override.dxf")

    # 真实 LibreDWG 0.14 转出的 DXF 里，中文是**已经解码的明文**（不是 \U+XXXX 转义）；
    # 解析器必须两种来源都能读，且不许把明文再"解码"一次。
    doc = new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True)
    msp.add_mtext("材质：白卡纸 350g", dxfattribs={"char_height": 2.5}).set_location((0, 25))
    msp.add_text("角  度", dxfattribs={"height": 2.5}).set_placement((0, 30))
    doc.saveas(HERE / "text_predecoded.dxf")
    made.append("text_predecoded.dxf")

    # 真实图纸里的标注绝大多数**没有**文字覆盖（`text == ''`），此时 declared 必须回落到
    # 实测值、delta 为 0 —— 不许因为"没有标注文字"就写出 null/NaN。
    doc = new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (72, 0), (72, 20), (0, 20)], close=True)
    dim = msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(72, 0), override={"dimtxt": 2.5})
    dim.render()
    doc.saveas(HERE / "dim_empty_text.dxf")
    made.append("dim_empty_text.dxf")

    # HATCH 只登记边界信息，不进闭合轮廓（填充不等于刀线）；LibreDWG 对某些 HATCH 会给出
    # 「Skip HATCH common handles」，所以边界是要 best-effort + 警告的。
    doc = new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (30, 0), (30, 20), (0, 20)], close=True)
    hatch = msp.add_hatch(color=3)
    hatch.paths.add_polyline_path([(5, 5), (25, 5), (25, 15), (5, 15)], is_closed=True)
    doc.saveas(HERE / "hatch_boundary.dxf")
    made.append("hatch_boundary.dxf")

    return made


def main() -> int:
    made = []
    if "--check" not in sys.argv:
        made = build_simple() + build_with_ezdxf()
        print("wrote %d fixtures" % len(made))
    import ezdxf
    for path in sorted(HERE.glob("*.dxf")):
        if path.name == "broken.dxf":
            print("%-34s broken (expected to fail)" % path.name)
            continue
        try:
            doc = ezdxf.readfile(str(path))
        except Exception as exc:
            print("%-34s READ FAILED %s: %s" % (path.name, type(exc).__name__, exc))
            continue
        msp = doc.modelspace()
        kinds = {}
        for entity in msp:
            kinds[entity.dxftype()] = kinds.get(entity.dxftype(), 0) + 1
        print("%-34s %6d B  insunits=%s  layers=%s  ms=%s" % (
            path.name, path.stat().st_size, doc.header.get("$INSUNITS"),
            len(list(doc.layers)), kinds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
