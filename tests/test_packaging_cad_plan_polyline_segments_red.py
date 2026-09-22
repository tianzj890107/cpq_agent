"""红测：平面图按**实体折线**画（开口件不再只画包络框）。

Spec：`docs/specs/packaging-cad-plan-polyline-segments.md`

现状缺口（代码级，都可指到行）：
  · `packaging_parts.extract()` 的 `kept.append({...})`（`:1681`）不带任何顶点坐标；
  · `geometry_evidence_of()`（`:3251`）只透传 `drawing_bbox` 与闭合件的 `outline_points`；
  · 前端 `packagingCadPlanComponentSvg()`（`app.js:1959`）只有 `<polygon>` / `<rect>` 两支。

纪律：纯函数真跑（后端直接调、前端 `node -e` 抽具名函数）+ 合成 IR + 源码守卫 + `node --check`；
不连 PG / 34、不发 HTTP、不写文件、不写业务数据。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_parts as parts             # noqa: E402

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
PARSER_PY = ROOT / "tech_app" / "backend" / "services" / "cad_ir" / "parser.py"

SEGMENT_MAX = 24
POINTS_MAX = 64

#: 抽具名函数体交给 node 执行（一次 eval 多个依赖，渲染类函数才能在 node 里真跑）。
EXTRACT_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extract(name) {
  const at = src.indexOf("function " + name + "(");
  if (at < 0) return null;
  let i = src.indexOf("{", at);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(at, j + 1); }
  }
  return null;
}
function extractConst(name) {
  const at = src.indexOf("const " + name + " =");
  if (at < 0) return null;
  const i = src.indexOf("{", at);
  if (i < 0) return null;
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(at, j + 2); }
  }
  return null;
}
const names = process.argv[3].split(",");
const deps = ["esc", "packagingCadPlanRange", "packagingCadPlanViewBox", "packagingCadPlanComponentBox",
              "packagingCadPlanOutlinePoints", "packagingCadPlanSegmentPolylines",
              "packagingCadPlanTruncationNote", "packagingCadPlanComponentSvg",
              "packagingBusinessPartComponents", "packagingBusinessPartOutlineHtml"];
const bodies = {};
for (const name of names.concat(deps)) {
  const fn = extract(name);
  if (fn) bodies[name] = fn;
}
const palette = extractConst("PACKAGING_CAD_LAYER_COLORS");
if (palette) { eval(palette.replace(/^const /, "var ")); }
const target = process.argv[4];
if (!bodies[target]) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
if (process.argv[5] === "body") { console.log(JSON.stringify({ missing: false, body: bodies[target] })); process.exit(0); }
for (const name of deps) { if (bodies[name]) eval(bodies[name]); }
eval(bodies[target]);
const cases = JSON.parse(process.argv[5]);
const out = [];
for (const args of cases) {
  const call = target + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def run_cases(name: str, cases):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, name, json.dumps(cases)],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def value_of(name: str, *args):
    got = run_cases(name, [list(args)])
    if got.get("missing"):
        raise AssertionError("app.js 缺少顶层纯函数 %s()（Spec §C3/§C4/§C5）" % name)
    row = got["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def function_body(name: str) -> str:
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, name, "body"],
        capture_output=True, text=True, timeout=60)
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少具名函数 %s()（Spec §C3/§C4/§C5）" % name)
    return str(payload.get("body") or "")


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def _polyline(entity_id, points, layer="CUT"):
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return {"entity_id": entity_id, "handle": entity_id.split(":")[-1], "type": "LWPOLYLINE",
            "kind": "polyline", "layer": layer, "space": "model", "block_path": [],
            "closed": False, "bbox": [min(xs), min(ys), max(xs), max(ys)],
            "length": 0.0, "length_mm": 0.0, "area": None, "area_mm2": None,
            "attributes": {"vertices": len(points), "points": [list(p) for p in points]},
            "evidence_ref": "ev:E:%s" % entity_id.split(":")[-1]}


def _line(entity_id, start, end, layer="CUT"):
    x0, y0 = start
    x1, y1 = end
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    return {"entity_id": entity_id, "handle": entity_id.split(":")[-1], "type": "LINE",
            "kind": "line", "layer": layer, "space": "model", "block_path": [],
            "closed": False, "bbox": [x0, y0, x1, y1], "length": length, "length_mm": length,
            "area": None, "area_mm2": None,
            "attributes": {"start": [x0, y0], "end": [x1, y1]},
            "evidence_ref": "ev:E:%s" % entity_id.split(":")[-1]}


def _spline(entity_id, fit_points, layer="CUT"):
    return {"entity_id": entity_id, "handle": entity_id.split(":")[-1], "type": "SPLINE",
            "kind": "spline", "layer": layer, "space": "model", "block_path": [],
            "closed": False, "bbox": [0.0, 0.0, 1.0, 1.0], "length": 1.0, "length_mm": 1.0,
            "area": None, "area_mm2": None,
            "attributes": {"curve": "spline", "fit_points_count": len(fit_points),
                           "fit_points": [list(p) for p in fit_points]},
            "evidence_ref": "ev:E:%s" % entity_id.split(":")[-1]}


def _circle(entity_id, layer="CUT"):
    return {"entity_id": entity_id, "handle": entity_id.split(":")[-1], "type": "CIRCLE",
            "kind": "circle", "layer": layer, "space": "model", "block_path": [],
            "closed": True, "bbox": [0.0, 0.0, 20.0, 20.0], "length": 62.8, "length_mm": 62.8,
            "area": 314.0, "area_mm2": 314.0,
            "attributes": {"curve": "circle", "radius": 10.0, "center": [10.0, 10.0]},
            "evidence_ref": "ev:E:%s" % entity_id.split(":")[-1]}


def _component(component_id, entity_ids):
    return {"component_id": component_id, "entity_ids": list(entity_ids)}


def _ir(entities, components, *, unit_status="confirmed"):
    evidence = {}
    for entity in entities:
        evidence[entity["evidence_ref"]] = {"handle": entity["handle"], "kind": "entity",
                                            "layer": entity["layer"], "spatial": [0.0, 0.0]}
    boxes = [entity["bbox"] for entity in entities if entity.get("bbox")] or [[0, 0, 1, 1]]
    span = [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return {"ir_id": "ir:red", "ir_hash": "red", "ir_version": 1,
            "units": {"candidates": [], "drawing_units": "mm", "scale_to_mm": 1.0,
                      "unit_confidence": 1.0, "unit_status": unit_status},
            "document": {"dxf_version": "AC1027", "extents": span, "layouts": [],
                         "model_space": {"entity_count": len(entities)},
                         "paper_space": {"entity_count": 0}, "warnings": []},
            "entities": entities, "layers": [{"name": "CUT", "role": "cut"}],
            "evidence": evidence, "texts": [], "dimensions": [],
            "geometry": {"components": components, "repeated_groups": []},
            "stats": {}, "source": {"kind": "dxf_2d", "attachment_name": "red.dwg"},
            "parser": {"name": "ezdxf", "options": {}, "version": "1.4.4"}}


# --------------------------------------------------------------------------- #
# A 组：`_component_segments()`（Spec §C1）
# --------------------------------------------------------------------------- #
class AComponentSegments(unittest.TestCase):
    def segments(self, members):
        fn = getattr(parts, "_component_segments", None)
        if not callable(fn):
            self.fail("packaging_parts 必须有纯函数 _component_segments()（Spec §C1）")
        return fn(members)

    def test_a1_three_kinds_of_segments_in_a_fixed_order(self):
        members = [_spline("e:30", [(3, 3), (4, 4)]),
                   _line("e:10", (0, 0), (10, 0)),
                   _polyline("e:20", [(0, 0), (5, 5), (10, 0)])]
        got = self.segments(members)
        self.assertEqual([[0.0, 0.0], [10.0, 0.0]], got["segments"][0],
                         "按 entity_id 升序：直线在前（Spec §C1）")
        self.assertEqual([[0.0, 0.0], [5.0, 5.0], [10.0, 0.0]], got["segments"][1],
                         "折线整串一段（Spec §C1）")
        self.assertEqual([[3.0, 3.0], [4.0, 4.0]], got["segments"][2],
                         "样条拟合点整串一段（Spec §C1）")
        self.assertEqual(3, got["segments_total"], "总数按段数算（Spec §C1）")
        self.assertIs(False, got["truncated"])

    def test_a2_unusable_coordinates_drop_the_whole_segment(self):
        members = [{"entity_id": "e:1", "attributes": {"start": [0, 0]}},
                   {"entity_id": "e:2", "attributes": {"start": [0, "x"], "end": [1, 1]}},
                   _line("e:3", (0, 0), (1, 1)),
                   _line("e:4", (0, 0), (float("nan"), 1))]
        got = self.segments(members)
        self.assertEqual(1, len(got["segments"]), "坐标不齐就整段丢掉，不猜（Spec §C1）")
        self.assertEqual([[0.0, 0.0], [1.0, 1.0]], got["segments"][0])
        self.assertIs(False, got["truncated"], "丢掉没有坐标的段不是截断（Spec §C1）")

    def test_a3_long_segment_is_decimated_with_both_ends_kept(self):
        points = [[float(i), 0.0] for i in range(200)]
        got = self.segments([_polyline("e:1", points)])
        self.assertEqual(1, len(got["segments"]))
        self.assertEqual(POINTS_MAX, len(got["segments"][0]),
                         "每段最多 %d 点（Spec §C1）" % POINTS_MAX)
        self.assertEqual([0.0, 0.0], got["segments"][0][0], "首点必留（Spec §C1）")
        self.assertEqual([199.0, 0.0], got["segments"][0][-1], "尾点必留（Spec §C1）")
        self.assertIs(True, got["truncated"], "抽稀必须披露（Spec §C1）")

    def test_a4_too_many_segments_keeps_the_first_ones_and_says_so(self):
        members = [_line("e:%03d" % index, (index, 0), (index, 1)) for index in range(40)]
        got = self.segments(members)
        self.assertEqual(SEGMENT_MAX, len(got["segments"]), "最多 %d 段（Spec §C1）" % SEGMENT_MAX)
        self.assertEqual(40, got["segments_total"], "总数按截断前算（Spec §C1）")
        self.assertIs(True, got["truncated"])
        self.assertEqual([[0.0, 0.0], [0.0, 1.0]], got["segments"][0], "取前 24 段（Spec §C1）")

    def test_a5_entities_without_coordinates_yield_nothing(self):
        got = self.segments([_circle("e:1"), {"entity_id": "e:2", "attributes": {}}])
        self.assertEqual([], got["segments"], "没坐标就是没折线（Spec §C1）")
        self.assertEqual(0, got["segments_total"])
        self.assertIs(False, got["truncated"])

    def test_a6_never_throws(self):
        self.segments(None)
        self.segments("x")
        self.segments([None, 7, "y", {"entity_id": "e:1", "attributes": "nope"}])

    def test_a7_caps_are_constants(self):
        self.assertEqual(SEGMENT_MAX, getattr(parts, "PLAN_SEGMENT_MAX", None),
                         "段数上限必须是常量（Spec §C1）")
        self.assertEqual(POINTS_MAX, getattr(parts, "PLAN_SEGMENT_POINTS_MAX", None),
                         "点数上限必须是常量（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：行与证据层（Spec §C2）
# --------------------------------------------------------------------------- #
class BDocumentAndEvidence(unittest.TestCase):
    #: 200 点的折线（锯齿状，包络 199×40 mm 才过得了 min_area 那道筛）
    LONG = [(float(index), 0.0 if index % 2 else 40.0) for index in range(200)]

    def test_b1_extract_rows_carry_the_segments(self):
        entities = [_polyline("e:1", self.LONG)]
        doc = parts.extract(_ir(entities, [_component("c1", ["e:1"])]))
        rows = doc.get("parts") or []
        self.assertEqual(1, len(rows), "这一件必须过筛（Spec §C2）")
        row = rows[0]
        self.assertIn("segments", row, "行上要带段（Spec §C2）")
        self.assertEqual(POINTS_MAX, len(row["segments"][0]), "上限同 C1（Spec §C2）")
        self.assertEqual(1, row["segments_total"])
        self.assertIs(True, row["segments_truncated"])

    def test_b2_evidence_forwards_the_segments(self):
        entities = [_polyline("e:1", self.LONG)]
        doc = parts.extract(_ir(entities, [_component("c1", ["e:1"])]))
        evidence = parts.geometry_evidence_of(doc)
        component = (evidence.get("components") or [])[0]
        for key in ("segments", "segments_total", "segments_truncated"):
            self.assertIn(key, component, "证据层要透传 %s（Spec §C2）" % key)
        self.assertEqual(POINTS_MAX, len(component["segments"][0]))

    def test_b3_evidence_defaults_for_old_documents(self):
        component = (parts.geometry_evidence_of({"parts": [{"component_id": "c1"}]})
                     .get("components") or [])[0]
        self.assertEqual([], component.get("segments"), "老文档缺键 → 空段（Spec §C2）")
        self.assertEqual(0, component.get("segments_total"))
        self.assertIs(False, component.get("segments_truncated"))

    def test_b4_other_evidence_keys_are_untouched(self):
        entities = [_polyline("e:1", [(0.0, 0.0), (50.0, 0.0), (50.0, 30.0), (0.0, 30.0), (0.0, 0.0)])]
        doc = parts.extract(_ir(entities, [_component("c1", ["e:1"])]))
        component = (parts.geometry_evidence_of(doc).get("components") or [])[0]
        for key in ("component_id", "entity_ids", "bbox", "drawing_bbox", "outline_points",
                    "unfolded_length_mm", "unfolded_width_mm", "outline_status", "size_source",
                    "area_mm2", "layers", "role", "geometry_component_ref"):
            self.assertIn(key, component, "既有键一个不动（Spec §C2）：%s" % key)


# --------------------------------------------------------------------------- #
# C 组：前端两个纯函数（Spec §C3/§C4）
# --------------------------------------------------------------------------- #
class CFrontendPureFunctions(unittest.TestCase):
    COMPONENT = {"segments": [[[0.0, 0.0], [10.0, 5.0]], [[2.0, 2.0], [3.0, 3.0], [4.0, 2.0]]],
                 "segments_total": 2, "segments_truncated": False}

    def test_c1_points_are_flipped_on_y(self):
        lines = value_of("packagingCadPlanSegmentPolylines", self.COMPONENT)
        self.assertEqual(["0,0 10,-5", "2,-2 3,-3 4,-2"], lines,
                         "每段一串 `x,-y`（与轮廓环点同口径，Spec §C3）")

    def test_c2_unusable_segments_are_dropped(self):
        component = {"segments": [[[0.0, 0.0]], [[0.0, 0.0], ["x", 1.0]],
                                  [[1.0, 1.0], [2.0, 2.0]], "nonsense", None]}
        self.assertEqual(["1,-1 2,-2"], value_of("packagingCadPlanSegmentPolylines", component),
                         "不足 2 点 / 坐标不可用的段丢掉（Spec §C3）")

    def test_c3_no_segments_is_an_empty_list(self):
        for component in ({}, {"segments": []}, None, 7, "x", {"segments": "nope"}):
            self.assertEqual([], value_of("packagingCadPlanSegmentPolylines", component),
                             "一段都没有 → 空清单（Spec §C3）：%r" % (component,))

    def test_c4_truncation_note_is_exact(self):
        self.assertEqual("", value_of("packagingCadPlanTruncationNote", self.COMPONENT),
                         "没截断就一个字都不说（Spec §C4）")
        component = dict(self.COMPONENT, segments_truncated=True, segments_total=40)
        self.assertEqual("这一件的折线被截断（原 40 段，图上 2 段），形状仅供定位。",
                         value_of("packagingCadPlanTruncationNote", component),
                         "截断那句话逐字（Spec §C4）")

    def test_c5_truncation_note_without_total_uses_shown(self):
        component = {"segments": [[[0.0, 0.0], [1.0, 1.0]]], "segments_truncated": True}
        self.assertEqual("这一件的折线被截断（原 1 段，图上 1 段），形状仅供定位。",
                         value_of("packagingCadPlanTruncationNote", component),
                         "总数取不到就用已画段数，不写 0（Spec §C4）")

    def test_c6_pure_functions_never_throw_and_have_no_dom(self):
        for name in ("packagingCadPlanSegmentPolylines", "packagingCadPlanTruncationNote"):
            body = function_body(name)
            for token in ("document.", "window.", "fetch(", "localStorage"):
                self.assertNotIn(token, body, "%s 体内不许出现 %s（Spec §C3/§C4）" % (name, token))
            for payload in (None, 7, "x", [], {"segments": [None]},
                            {"segments_truncated": "yes"}, {"segments_total": {"a": 1}}):
                value_of(name, payload)     # 不抛就算过


# --------------------------------------------------------------------------- #
# D 组：接线与护栏（Spec §C5/§C6）
# --------------------------------------------------------------------------- #
class DWiringAndGuards(unittest.TestCase):
    CLOSED = {"component_id": "c1", "geometry_component_ref": "c1", "outline_status": "closed",
              "bbox": [0.0, 0.0, 10.0, 10.0],
              "drawing_bbox": [0.0, 0.0, 10.0, 10.0], "role": "cut", "layers": ["CUT"],
              "outline_points": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]}
    OPEN = {"component_id": "c2", "geometry_component_ref": "c2", "outline_status": "open",
            "bbox": [0.0, 0.0, 10.0, 10.0],
            "drawing_bbox": [0.0, 0.0, 10.0, 10.0], "role": "cut", "layers": ["CUT"],
            "segments": [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]], "segments_total": 1,
            "segments_truncated": False}
    BOXY = {"component_id": "c3", "geometry_component_ref": "c3", "outline_status": "open",
            "bbox": [0.0, 0.0, 10.0, 10.0],
            "drawing_bbox": [0.0, 0.0, 10.0, 10.0], "role": "cut", "layers": ["CUT"]}

    def test_d1_closed_still_wins_and_segments_are_drawn_for_open_parts(self):
        closed = value_of("packagingCadPlanComponentSvg", self.CLOSED)
        self.assertIn("<polygon", closed, "闭合件仍画真轮廓（Spec §C5）")
        self.assertNotIn("<polyline", closed, "有环就不画折线（Spec §C5）")
        opened = value_of("packagingCadPlanComponentSvg", self.OPEN)
        self.assertIn("<polyline", opened, "开口件有段就画折线（Spec §C5）")
        self.assertNotIn("<rect", opened, "有段就不再画包络框（Spec §C5）")
        self.assertEqual(1, opened.count("<polyline"), "一段一条折线（Spec §C5）")
        self.assertIn('data-segments-truncated="0"', opened)

    def test_d2_polylines_keep_the_same_data_attributes(self):
        opened = value_of("packagingCadPlanComponentSvg", self.OPEN)
        for token in ('data-component-id="c2"', 'data-component-ref="c2"', 'data-business-part=""',
                      'data-bbox="0,0,10,10"', 'data-layer="CUT"', 'data-role="cut"',
                      'fill="none"'):
            self.assertIn(token, opened, "折线上的 data 属性必须与方框同形（Spec §C5）：%s" % token)

    def test_d3_box_is_still_the_last_resort(self):
        boxy = value_of("packagingCadPlanComponentSvg", self.BOXY)
        self.assertIn("<rect", boxy, "真没坐标才退回包络框（Spec §C5）")
        self.assertNotIn("<polyline", boxy)

    def test_d4_panel_appends_the_truncation_note(self):
        truncated = dict(self.OPEN, segments_total=40, segments_truncated=True)
        record = {"geometry_evidence": {"components": [truncated]}}
        html = value_of("packagingBusinessPartOutlineHtml",
                        {"component_ids": ["c2"]}, record)
        self.assertIn("这一件的折线被截断（原 40 段，图上 1 段），形状仅供定位。", html,
                      "截断了就必须在面板上说一句（Spec §C5）")
        plain = {"geometry_evidence": {"components": [self.OPEN]}}
        self.assertNotIn("截断", value_of("packagingBusinessPartOutlineHtml",
                                         {"component_ids": ["c2"]}, plain),
                         "没截断就不许说（Spec §C5）")

    def test_d5_frozen_helpers_are_untouched(self):
        for name in ("packagingCadPlanOutlinePoints", "packagingCadPlanComponentBox",
                     "packagingCadPlanRange", "packagingCadPlanViewBox",
                     "fitPackagingCadPlan", "highlightPackagingBusinessPart"):
            body = function_body(name)
            self.assertNotIn("segments", body, "%s 一字不动（Spec §C6）" % name)
        self.assertNotIn("segments", _source(PARSER_PY), "cad_ir/parser.py 一行不改（Spec §C6）")

    def test_d6_node_check_passes(self):
        proc = subprocess.run(["node", "--check", str(APP_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, "app.js 语法必须通过：%s" % proc.stderr[:400])


if __name__ == "__main__":
    unittest.main()
