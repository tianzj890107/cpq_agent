r"""红测：2.1 右栏只画「这一件」的图（一块图 / 整块一起滚 / 按图层着色 / 可拖拽缩放）。

Spec：`docs/specs/packaging-2-1-right-pane-single-part-figure.md`

用户原话（2026-09-23）：

> 现在右侧分了上下两栏滑动，我不是这个意思，我意思是完整的滑动，就像现在下面那栏里面那样直接滑动，
> 也就是说现在上下栏里面的所有内容一起滑动，但我没理解下栏里面的图是什么，是只有最外面一圈的零件吗，
> 我不需要这个东西，我只需要上面那个图，但是上面那个图不是让定位原图然后放大，是要只有这一个东西的图，
> 现在旁边还有别的零件这不对，而且是这个图可以拖拽缩放，而且这个图里面应该也有颜色区分，
> 就像dwg看图的里面那样

现状缺口（HEAD `89f0bc0` 工作副本只读）：
  · 右栏**三个**滚动容器：`workbench.css:325` 列、`drawing-flow.css:235-244` `.packaging-cad-plan`
    （`height:100%;overflow:auto`）、`drawing-flow.css:39-46` `.packaging-part-panel`（`overflow:auto`）
    ⇒ 上下两块各自能滚；
  · 上栏 `#packagingCadPlanViewer` 画的是**整张** CAD 图（`renderPackagingCadScene()`），选中一件后只是
    把其余图元 `is-dimmed` + `fitPackagingCadPlan()` 缩放定位 —— "旁边还有别的零件"；
  · 下栏 `#packagingPartOutline` 里 `packagingBusinessPartOutlineHtml()` 画的是**绑定分量的轮廓环**
    （闭合件只有 `outline_points` 那一圈）—— 用户说的"只有最外面一圈的零件"；
  · 「这一件的图元」能算出来（`geometry_binding` → `components[].entity_ids` → `cad_scene.entities`），
    但 `entity_ids` 是**全量成员（含标注）**，证据层现在**不带** `annotation_filtered`
    ⇒ 直接画会把上一批刚摘掉的尺寸线又画回来（所以 A1 要补这一个键）。

纪律：只读源码 / CSS / `index.html` + `node -e` 抽纯函数真跑 + 直接 import 后端纯函数；
不起服务、不发 HTTP、不连 PG / 34、不写业务数据。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
INDEX_HTML = ROOT / "tech_app" / "frontend" / "index.html"
WORKBENCH_CSS = ROOT / "tech_app" / "frontend" / "workbench.css"
DRAWING_FLOW_CSS = ROOT / "tech_app" / "frontend" / "drawing-flow.css"

ENTITIES_FN = "packagingPartSceneEntities"
FIGURE_FN = "packagingPartSceneSvg"
OPEN_FN = "openPackagingBusinessPart"
PLAN_FN = "renderPackagingCadPlan"
GEOMETRY_PART_FN = "selectPackagingPart"
VIEWPORT_CLASS = "packaging-part-shape-viewport"

ANNOTATION_FIELD = "annotation_filtered"

EXTRACT_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8").replace(/\u0000/g, "");
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
const name = process.argv[3];
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
const mode = process.argv[4];
if (mode === "body") { console.log(JSON.stringify({ missing: false, body: fn })); process.exit(0); }
const cases = JSON.parse(mode, (key, value) => (value === "__NaN__" ? NaN : value));
const argText = value => (typeof value === "number" && Number.isNaN(value)) ? "NaN" : JSON.stringify(value);
eval(fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(argText).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def json_safe(value):
    if isinstance(value, float) and math.isnan(value):
        return "__NaN__"
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    return value


def _run_node(argv, timeout=60):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-"] + argv,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def call(name: str, args):
    payload = _run_node([str(APP_JS), name, json.dumps(json_safe([args]))])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2/C2）" % name)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def function_body(name: str) -> str:
    payload = _run_node([str(APP_JS), name, "body"])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def css_blocks(text: str):
    body = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return [(m.group(1).strip(), m.group(2)) for m in
            re.finditer(r"([^{}]+)\{([^{}]*)\}", body)]


def blocks_matching(blocks, *selector_parts):
    return [decls for selector, decls in blocks
            if all(part in selector for part in selector_parts)]


def has_decl(decls_list, pattern: str) -> bool:
    return any(re.search(pattern, decls) for decls in decls_list)


# --------------------------------------------------------------------------- #
# 夹具：一件（cmp:17）四条图元（1 圈 + 3 条内线，跨 cut / crease 两个 role）
#       + 一条标注（e:5）+ 一条别件图元（e:9）
# --------------------------------------------------------------------------- #
def _entity(entity_id, layer, role, points, *, closed=False, kind="line", **extra):
    row = {"cad_entity_id": entity_id, "kind": kind, "layer": layer, "role": role,
           "closed": closed, "points": points,
           "bbox": [min(p[0] for p in points), min(p[1] for p in points),
                    max(p[0] for p in points), max(p[1] for p in points)]}
    row.update(extra)
    return row


PART_ENTITIES = [
    _entity("e:1", "全穿刀", "cut", [[0, 0], [200, 0]]),
    _entity("e:2", "压线 Crease", "crease", [[10, 20], [180, 20]]),
    _entity("e:3", "压线 Crease", "crease", [[10, 40], [180, 40]]),
    _entity("e:4", "轮廓线", "cut", [[0, 0], [200, 0], [200, 120], [0, 120]], closed=True,
            kind="lwpolyline"),
]
ANNOTATION = _entity("e:5", "全穿刀", "cut", [[0, -30], [0, 0]])
NEIGHBOUR = _entity("e:9", "全穿刀", "cut", [[900, 900], [999, 999]])

COLOR_CUT = "#1f6feb"
COLOR_CREASE = "#d29922"


def scene_doc():
    return {
        "cad_scene": {
            "entity_total": 6,
            "layer_visibility": {"全穿刀": True, "压线 Crease": True, "轮廓线": True},
            "entities": list(PART_ENTITIES) + [ANNOTATION, NEIGHBOUR],
        },
        "geometry_evidence": {
            "component_total": 2,
            "components": [
                {"component_id": "cmp:17", "role": "cut", "layers": ["全穿刀", "压线 Crease"],
                 "entity_ids": ["e:1", "e:2", "e:3", "e:4", "e:5"],
                 ANNOTATION_FIELD: [{"entity_id": "e:5", "reason": "dimension_line"}]},
                {"component_id": "cmp:18", "role": "cut", "layers": ["全穿刀"],
                 "entity_ids": ["e:9"], ANNOTATION_FIELD: []},
            ],
        },
    }


def bound_binding():
    return {"status": "bound", "component_ids": ["cmp:17"], "entity_ids": []}


def text_doc():
    doc = scene_doc()
    label = _entity("e:6", "文字层", "print", [[0, 0], [0, 0]], kind="text",
                    text="酒盒", x=20.0, y=60.0, height=5.0)
    doc["cad_scene"]["entities"].append(label)
    doc["geometry_evidence"]["components"][0]["entity_ids"].append("e:6")
    return doc


def drawn_count(svg: str) -> int:
    return len(re.findall(r"<(polyline|polygon|text)\b", svg or ""))


def strokes(svg: str):
    return re.findall(r'stroke="([^"]*)"', svg or "")


# --------------------------------------------------------------------------- #
# A 组：后端口径（证据层要带 annotation_filtered；证据本身一条不许少）
# --------------------------------------------------------------------------- #
class AEvidencePayload(unittest.TestCase):
    def _evidence(self, doc):
        from tech_app.backend.services import packaging_parts
        return packaging_parts.geometry_evidence_of(doc)

    def test_a1_components_carry_annotation_filtered(self):
        from tech_app.backend.services import packaging_parts
        parts_doc = {"parts": [{
            "component_id": "cmp:17", "entity_ids": ["e:1", "e:5"],
            ANNOTATION_FIELD: [{"entity_id": "e:5", "reason": "dimension_line"}],
            "unfolded_length_mm": 200.0, "unfolded_width_mm": 120.0,
            "outline_status": "closed", "size_source": "closed_outline",
            "role": "cut", "layers": ["全穿刀"],
        }], "stats": {"component_total": 1, "kept_total": 1}}
        rows = packaging_parts.geometry_evidence_of(parts_doc)["components"]
        self.assertTrue(rows, "证据层没回分量")
        row = rows[0]
        self.assertIn(ANNOTATION_FIELD, row,
                      "证据层分量必须带 `%s` —— 不然「画这一件」时标注线没处减（Spec §C2/§1）"
                      % ANNOTATION_FIELD)
        self.assertEqual([{"entity_id": "e:5", "reason": "dimension_line"}], row[ANNOTATION_FIELD],
                         "`%s` 要与 `packaging-parts` 文档同名同形（升序、逐条 entity_id+reason）"
                         % ANNOTATION_FIELD)

    def test_a2_annotation_entities_stay_in_the_evidence(self):
        from tech_app.backend.services import packaging_parts
        parts_doc = {"parts": [{
            "component_id": "cmp:17", "entity_ids": ["e:1", "e:5"],
            ANNOTATION_FIELD: [{"entity_id": "e:5", "reason": "dimension_line"}],
            "role": "cut", "layers": ["全穿刀"],
        }], "stats": {"component_total": 1, "kept_total": 1}}
        row = packaging_parts.geometry_evidence_of(parts_doc)["components"][0]
        self.assertEqual(["e:1", "e:5"], row["entity_ids"],
                         "证据不许被删：标注实体仍要在 `entity_ids` 里（只是画这一件时不画）")

    def test_a3_missing_annotation_is_an_empty_list(self):
        from tech_app.backend.services import packaging_parts
        parts_doc = {"parts": [{"component_id": "cmp:18", "entity_ids": ["e:9"], "role": "cut"}],
                     "stats": {"component_total": 1, "kept_total": 1}}
        row = packaging_parts.geometry_evidence_of(parts_doc)["components"][0]
        self.assertEqual([], row.get(ANNOTATION_FIELD),
                         "没有标注时必须是空清单（不是 None、不是缺失）")


# --------------------------------------------------------------------------- #
# B 组：`packagingPartSceneEntities(binding, doc)`（node 真跑）
# --------------------------------------------------------------------------- #
class BPartSceneEntities(unittest.TestCase):
    def test_b1_only_this_part(self):
        got = call(ENTITIES_FN, [bound_binding(), scene_doc()])
        self.assertEqual(["e:1", "e:2", "e:3", "e:4"], [row["cad_entity_id"] for row in got],
                         "只许画这一件绑的分量的图元，别件的（e:9）一条都不许进来（Spec §C1/C2）")

    def test_b2_annotations_are_subtracted(self):
        got = call(ENTITIES_FN, [bound_binding(), scene_doc()])
        ids = [row["cad_entity_id"] for row in got]
        self.assertNotIn("e:5", ids,
                         "这一件里被判成标注的实体不许画进件图（上一批刚把尺寸线摘掉，Spec §C2）")

    def test_b3_scene_order_and_dedupe(self):
        binding = {"component_ids": ["cmp:17"], "entity_ids": ["e:4", "e:2", "e:4"]}
        got = call(ENTITIES_FN, [binding, scene_doc()])
        self.assertEqual(["e:1", "e:2", "e:3", "e:4"], [row["cad_entity_id"] for row in got],
                         "顺序必须是场景顺序、同一 id 只回一条（Spec §C2 第 3 条）")

    def test_b4_binding_by_entity_ids_alone_works(self):
        binding = {"entity_ids": ["e:3", "e:1"]}
        got = call(ENTITIES_FN, [binding, scene_doc()])
        self.assertEqual(["e:1", "e:3"], [row["cad_entity_id"] for row in got],
                         "只按 `entity_ids` 绑定时也要按场景顺序命中（Spec §C2 第 1 条）")

    def test_b5_nothing_bound_or_nothing_found_is_empty(self):
        for binding in ({}, {"component_ids": []}, {"component_ids": ["cmp:missing"]},
                        {"entity_ids": ["e:nope"]}):
            self.assertEqual([], call(ENTITIES_FN, [binding, scene_doc()]),
                             "未绑定 / 命不中就回空清单，不猜也不回退整图（Spec §C2 第 4 条）")

    def test_b6_is_a_pure_function(self):
        body = function_body(ENTITIES_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §C2）" % ENTITIES_FN)
        for token in ("document", "window.", "fetch(", "localStorage", "sessionStorage"):
            self.assertNotIn(token, body, "%s 不许引用 %s（要被 node 直接执行）" % (ENTITIES_FN, token))


# --------------------------------------------------------------------------- #
# C 组：`packagingPartSceneSvg(binding, doc)`（node 真跑）
# --------------------------------------------------------------------------- #
class CPartSceneFigure(unittest.TestCase):
    def test_c1_figure_has_a_viewbox_and_all_entities(self):
        svg = call(FIGURE_FN, [bound_binding(), scene_doc()])
        self.assertIn("<svg", svg, "这一件的图必须是 svg（Spec §C3）")
        self.assertIn("viewBox", svg, "viewBox 由这一件的坐标范围产出（Spec §C3）")
        self.assertEqual(4, drawn_count(svg), "命中 4 条图元就要画 4 条（Spec §C3）")

    def test_c2_more_than_just_the_outer_ring(self):
        svg = call(FIGURE_FN, [bound_binding(), scene_doc()])
        self.assertIn("<polygon", svg, "闭合的那一圈要画（Spec §C3）")
        self.assertIn("<polyline", svg,
                      "件内折线也要画 —— 只画最外面一圈就是用户不要的那张图（Spec §C3）")
        self.assertGreaterEqual(drawn_count(svg), 4, "件内真实几何一条不许少（Spec §C3）")

    def test_c3_colours_come_from_the_layer_palette(self):
        svg = call(FIGURE_FN, [bound_binding(), scene_doc()])
        got = strokes(svg)
        self.assertIn(COLOR_CUT, got, "cut 用平面图那张表的颜色（Spec §C4）")
        self.assertIn(COLOR_CREASE, got, "crease 用平面图那张表的颜色（Spec §C4）")
        self.assertGreaterEqual(len(set(got)), 2,
                                "一件里跨两个 role 就要有两种颜色 —— 像 DWG 看图那样按图层区分（Spec §C4）")

    def test_c4_neighbours_are_not_drawn(self):
        svg = call(FIGURE_FN, [bound_binding(), scene_doc()])
        self.assertNotIn("e:9", svg, "别件的图元不许进这张图（Spec §C1/C2）")
        self.assertNotIn("999", svg, "别件的坐标不许进这张图（Spec §C1/C2）")

    def test_c5_text_entities_are_drawn(self):
        svg = call(FIGURE_FN, [bound_binding(), text_doc()])
        self.assertIn("<text", svg, "这一件里的文字图元也要画（Spec §C3）")

    def test_c6_nothing_to_draw_is_an_empty_string(self):
        for binding, doc in (({}, scene_doc()),
                             ({"component_ids": ["cmp:missing"]}, scene_doc()),
                             (bound_binding(), {})):
            self.assertEqual("", call(FIGURE_FN, [binding, doc]),
                             "画不出来就回空串，由调用方给状态文案（Spec §C3/C7）")

    def test_c7_is_a_pure_function(self):
        body = function_body(FIGURE_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §C3）" % FIGURE_FN)
        for token in ("document", "window.", "fetch(", "localStorage", "sessionStorage"):
            self.assertNotIn(token, body, "%s 不许引用 %s（要被 node 直接执行）" % (FIGURE_FN, token))


# --------------------------------------------------------------------------- #
# D 组：接线（`openPackagingBusinessPart()` / `renderPackagingCadPlan()` 函数体）
# --------------------------------------------------------------------------- #
class DWiring(unittest.TestCase):
    def test_d1_the_panel_no_longer_draws_its_own_figure(self):
        body = function_body(OPEN_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % OPEN_FN)
        self.assertIn(FIGURE_FN, body, "单件图必须由纯函数画出来（Spec §C1/C3）")
        self.assertNotIn("packagingBusinessPartOutlineHtml", body,
                         "下栏那块「只有外圈」的图要撤掉（Spec §C1/§3）")
        self.assertNotIn("packaging-part-svg", body,
                         "下栏那块图的样式类不许再出现（Spec §C1）")

    def test_d2_the_figure_is_drawn_in_the_upper_pane(self):
        body = function_body(OPEN_FN)
        self.assertIn("packagingCadPlanViewer", body,
                      "这块图要画在右栏上面那一块（用户：只需要上面那个图；Spec §2）")

    def test_d3_the_figure_is_draggable_and_zoomable(self):
        body = function_body(OPEN_FN)
        self.assertIn("bindPackagingPartShapeInteractions", body,
                      "拖拽/缩放要接线既有交互（Spec §C5）")
        self.assertIn("data-qq-shape-viewport", body,
                      "视口包裹与缩放标签的口径不变（Spec §C5）")

    def test_d4_unbound_still_explains_why(self):
        body = function_body(OPEN_FN)
        self.assertIn("PACKAGING_BOUND_OUTLINE_NOTE", body,
                      "「业务尺寸以权威资料为准」这句文字口径保留（Spec §3）")
        self.assertIn("PACKAGING_BINDING_COPY", body,
                      "未绑定 / 画不出来时给原因，不给空图（Spec §C7）")

    def test_d5_refresh_keeps_the_selected_part(self):
        body = function_body(PLAN_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % PLAN_FN)
        self.assertTrue(FIGURE_FN in body or "currentPackagingBusinessPartCode" in body,
                        "场景后到 / 重新拉取后，那块图仍是当前选中的这一件（Spec §C8）")


    def test_d6_three_states_and_race_are_not_lost_in_the_move(self):
        body = function_body(OPEN_FN)
        self.assertIn("data-qq-part-shape", body,
                      "三态勾子要跟着这块图走、打在承载它的宿主上（Spec §C7）")
        self.assertIn("正在读取这一件的形状…", body, "加载态文案逐字不变（Spec §C7）")
        self.assertIn("pendingPackagingShapePartCode", body, "首点竞态口径不变（Spec §C7）")


# --------------------------------------------------------------------------- #
# E 组：CSS（一个滚动条 + 图不吃满整栏）
# --------------------------------------------------------------------------- #
class ECss(unittest.TestCase):
    def setUp(self):
        self.blocks = css_blocks(read_text(WORKBENCH_CSS) + read_text(DRAWING_FLOW_CSS))

    def test_e1_figure_does_not_eat_the_column(self):
        decls = blocks_matching(self.blocks, ".packaging-cad-plan")
        self.assertTrue(decls, "`.packaging-cad-plan` 规则丢了")
        self.assertFalse(has_decl(decls, r"height\s*:\s*100\s*%"),
                         "那块图不许 `height:100%` 吃满整栏（Spec §C5）")

    def test_e2_plan_pane_has_no_scroll_of_its_own(self):
        decls = blocks_matching(self.blocks, ".packaging-cad-plan")
        self.assertFalse(has_decl(decls, r"overflow\s*:\s*auto"),
                         "`.packaging-cad-plan` 不许自持滚动条（Spec §C6）")

    def test_e3_part_panel_has_no_scroll_of_its_own(self):
        decls = blocks_matching(self.blocks, ".packaging-part-panel")
        self.assertTrue(decls, "`.packaging-part-panel` 规则丢了")
        self.assertFalse(has_decl(decls, r"overflow\s*:\s*auto"),
                         "`.packaging-part-panel` 不许自持滚动条（Spec §C6）")

    def test_e4_the_column_is_the_only_scroll_container(self):
        decls = blocks_matching(self.blocks, "data-qq-fill", "drawing-model-column")
        self.assertTrue(has_decl(decls, r"overflow-y\s*:\s*auto"),
                        "滚动权仍归 `.drawing-model-column[data-qq-fill]` 一处（Spec §C6）")

    def test_e5_viewport_still_swallows_wheel(self):
        decls = blocks_matching(self.blocks, "." + VIEWPORT_CLASS)
        self.assertTrue(has_decl(decls, r"overflow\s*:\s*hidden"),
                        "视口仍 `overflow:hidden`：裸滚轮留给整栏滚动（Spec §C5）")


# --------------------------------------------------------------------------- #
# F 组：护栏（现状即绿）
# --------------------------------------------------------------------------- #
class FGuards(unittest.TestCase):
    def test_f1_app_js_still_parses(self):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("未安装 node，跳过语法检查")
        proc = subprocess.run([node, "--check", str(APP_JS)], capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(0, proc.returncode, "app.js 语法错误：\n%s" % proc.stderr)

    def test_f2_whole_drawing_capabilities_survive(self):
        src = read_text(APP_JS)
        for token in ("packagingCadSceneEntitySvg", "renderPackagingCadScene", "fitPackagingCadPlan",
                      "highlightPackagingBusinessPart", "PACKAGING_CAD_LAYER_COLORS"):
            self.assertIn(token, src, "整张图既有能力丢了：%s" % token)

    def test_f3_geometry_part_channel_is_untouched(self):
        body = function_body(GEOMETRY_PART_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % GEOMETRY_PART_FN)
        self.assertIn("packaging-parts/", body, "几何分量通道（单件详情）不许被本批改掉")
        self.assertNotIn(FIGURE_FN, body, "几何分量通道不接本批的单件图（Spec §4 边界 1）")

    def test_f4_index_html_keeps_the_three_ids(self):
        html = read_text(INDEX_HTML)
        for node_id in ("packagingCadPlanViewer", "packagingPartOutline", "packagingShapeIdle"):
            self.assertIn(node_id, html, "右栏节点 %s 不许删（别的 Spec 依赖它）" % node_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
