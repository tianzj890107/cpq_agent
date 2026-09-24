r"""红测：2.1 看图那块（件图 + 任务文件预览图）—— 可缩放拖拽、分色、高度翻倍、画「图纸上这一件的样子」。

Spec：`docs/specs/packaging-2-1-part-figure-fidelity.md`

用户原话（2026-09-24）：
  > 任务文件里的预览 dwg 和 3D 视图的看零件能不能做成可以缩放和拖拽的，能不能做成有颜色区分的？
  > 然后另外，现在的 3D 视图里看零件的地方太矮了，应该高度是现在两倍，另外想要的是之前这里的东西
  > 而不是之前下面的只有外轮廓的东西，想要的是图纸上整个零件的样子而且是这个零件的全部在图纸上的
  > 样子还原出来而且还没有别的零件

现状缺口（本批实现前的工作副本）：
  · 预览里的 DWG 图是**死图**（只 `innerHTML` 一张 svg，没视口、没绑事件）；
  · 三处渲染都按 `row.role` 取色，真图 `role` 几乎全是 `unknown` ⇒ 满屏一个灰；
  · `.packaging-part-shape-viewport{height:min(46vh,420px)}`、`.packaging-cad-plan-svg{min-height:220px}`；
  · `packagingPartSceneSvg()` 用的 `packagingPartSceneEntities()` 把这一件的标注摘掉（`## 485` 的形状口径）
    ⇒ 件图看着只剩轮廓。

纪律：只读源码 / CSS + `node -e` 抽纯函数真跑；不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
DRAWING_FLOW_CSS = ROOT / "tech_app" / "frontend" / "drawing-flow.css"

COLOUR_FN = "packagingCadLayerColour"
ANNOT_FN = "packagingPartAnnotationEntities"
FIGURE_FN = "packagingPartSceneSvg"
ENTITIES_FN = "packagingPartSceneEntities"
MARKUP_FN = "packagingShapeViewportMarkup"
BIND_FN = "bindPackagingPartShapeInteractions"
PREVIEW_FN = "openFilePreview"
OPEN_FN = "openPackagingBusinessPart"
PANEL_FN = "renderPackagingPartPanel"

VIEWPORT_CLASS_CONST = "PACKAGING_PART_SHAPE_VIEWPORT_CLASS"
RESET_ID_CONST = "PACKAGING_PART_SHAPE_RESET_ID"

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
const cases = JSON.parse(mode);
eval(fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def read_text(path):
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def _run_node(argv, timeout=60):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-"] + argv,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def call(name, *args):
    payload = _run_node([str(APP_JS), name, json.dumps([list(args)])])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §C1/§C2/§C4）" % name)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def function_body(name):
    payload = _run_node([str(APP_JS), name, "body"])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def bound_binding():
    return {"component_ids": ["cmp:17"], "entity_ids": ["e:3"]}


def scene_doc():
    return {
        "cad_scene": {"entities": [
            {"cad_entity_id": "e:1", "layer": "全穿刀", "role": "cut", "kind": "polyline",
             "points": [[0, 0], [10, 0]], "bbox": [0, 0, 10, 0]},
            {"cad_entity_id": "e:2", "layer": "压线", "role": "crease", "kind": "polyline",
             "points": [[0, 5], [10, 5]], "bbox": [0, 5, 10, 5]},
            {"cad_entity_id": "e:3", "layer": "标注层", "role": "unknown", "kind": "polyline",
             "points": [[0, 20], [10, 20]], "bbox": [0, 20, 10, 20]},
            {"cad_entity_id": "e:5", "layer": "标注层", "role": "unknown", "kind": "polyline",
             "points": [[0, -50], [10, -50]], "bbox": [0, -50, 10, -50]},
            {"cad_entity_id": "e:9", "layer": "别件层", "role": "unknown", "kind": "polyline",
             "points": [[100, 100], [110, 100]], "bbox": [100, 100, 110, 100]},
            {"cad_entity_id": "e:10", "layer": "别件层", "role": "unknown", "kind": "polyline",
             "points": [[200, 200], [210, 200]], "bbox": [200, 200, 210, 200]},
        ]},
        "geometry_evidence": {"components": [
            {"component_id": "cmp:17", "entity_ids": ["e:1", "e:2", "e:3", "e:5"],
             "annotation_filtered": [{"entity_id": "e:5", "reason": "dimension_line"}]},
            {"component_id": "cmp:99", "entity_ids": ["e:9", "e:10"],
             "annotation_filtered": [{"entity_id": "e:10", "reason": "dimension_line"}]},
        ]},
    }


# --------------------------------------------------------------------------- #
# A 组：纯函数（node 真跑）
# --------------------------------------------------------------------------- #
class APureFunctions(unittest.TestCase):
    def test_current_rule_colours_use_entity_aci_and_vslot_role(self):
        self.assertEqual("#dc2626", call(COLOUR_FN, "DESIGN", "unknown", 1, 7))
        self.assertEqual("#16a34a", call(COLOUR_FN, "DESIGN", "unknown", 3, 7))
        self.assertEqual("#2563eb", call(COLOUR_FN, "0", "unknown", 6, 7))
        self.assertEqual("#ec4899", call(COLOUR_FN, "Vslot", "v_groove", 6, 7))
        self.assertEqual("#eab308", call(COLOUR_FN, "参考线", "unknown", 256, 7))

    def test_part_view_adds_internal_lines_without_neighbour(self):
        doc = scene_doc()
        doc["cad_scene"]["entities"].append({
            "cad_entity_id": "e:internal", "layer": "DESIGN", "role": "unknown",
            "kind": "line", "points": [[2, 2], [8, 2]], "bbox": [2, 2, 8, 2],
        })
        binding = dict(bound_binding(), bbox=[0, 0, 10, 20])
        got = call(ENTITIES_FN, binding, doc)
        ids = {item["cad_entity_id"] for item in got}
        self.assertIn("e:internal", ids)
        self.assertNotIn("e:9", ids)
        self.assertNotIn("e:5", ids)

    def test_a1_known_role_keeps_its_colour_unknown_takes_a_layer_colour(self):
        self.assertEqual("#dc2626", call(COLOUR_FN, "任意层", "cut"),
                         "刀线按当前业务图例显示为红色")
        first = call(COLOUR_FN, "全穿刀", "unknown")
        second = call(COLOUR_FN, "全穿刀", "unknown")
        self.assertEqual(first, second, "同一个图层名必须永远同一个颜色（Spec §C2）")
        self.assertTrue(re.fullmatch(r"#[0-9a-fA-F]{6}", str(first)),
                        "认不出角色的要回一个颜色，不能是 undefined / 空：%r" % (first,))

    def test_a2_layers_get_different_colours(self):
        names = ["全穿刀", "压线", "图框层", "0", "DEFPOINTS", "TEXT", "尺寸线", "内衬线"]
        colours = {call(COLOUR_FN, name, "unknown") for name in names}
        self.assertGreaterEqual(len(colours), 2,
                                "真图上认不出角色的图层必须看得出来是不同的层（Spec §C2）：%r" % (colours,))

    def test_a3_annotation_entities_are_only_this_part(self):
        got = call(ANNOT_FN, bound_binding(), scene_doc())
        self.assertEqual(["e:5"], [row["cad_entity_id"] for row in got],
                         "只回这一件的标注（e:5）；别件的标注（e:10）与非标注都不许进来（Spec §C4）")

    def test_a4_annotation_entities_empty_without_binding(self):
        for binding in ({}, {"component_ids": []}, {"component_ids": ["cmp:missing"]}):
            self.assertEqual([], call(ANNOT_FN, binding, scene_doc()),
                             "没绑定 / 命不中就回空（Spec §C4）")

    def test_a5_figure_excludes_annotations_by_default(self):
        svg = call(FIGURE_FN, bound_binding(), scene_doc())
        self.assertIn("e:1", svg, "形状口径的行照旧要画（Spec §C4）")
        self.assertNotIn("e:5", svg,
                         "默认（不带选项）仍是形状口径：标注不画（`## 485` 的红测口径不动，Spec §C4）")

    def test_a6_figure_with_annotations_draws_them_as_annotations(self):
        svg = call(FIGURE_FN, bound_binding(), scene_doc(), {"includeAnnotations": True})
        self.assertIn("e:5", svg, "带 `includeAnnotations` 时要把这一件的标注补画回来（Spec §C4）")
        self.assertIn("is-annotation", svg, "补画的标注要有 `is-annotation` 标记（Spec §C4）")
        self.assertNotIn("e:9", svg, "别件的图元一条都不许进来（Spec §C4）")
        self.assertNotIn("e:10", svg, "别件的标注也不许进来（Spec §C4）")


# --------------------------------------------------------------------------- #
# B 组：接线（源码扫描）
# --------------------------------------------------------------------------- #
class BPreviewWiring(unittest.TestCase):
    def test_b1_preview_uses_the_same_viewport(self):
        body = function_body(PREVIEW_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % PREVIEW_FN)
        self.assertIn(MARKUP_FN + "(", body,
                      "预览的 DWG 图要套同一套视口（Spec §C1）")
        self.assertIn(BIND_FN + "(", body,
                      "套上视口之后要绑同一套缩放 / 拖拽（Spec §C1）")

    def test_b2_viewport_markup_reuses_the_same_class_and_reset_id(self):
        body = function_body(MARKUP_FN)
        self.assertTrue(body, "app.js 缺少 %s()（Spec §C1）" % MARKUP_FN)
        self.assertIn(VIEWPORT_CLASS_CONST, body,
                      "视口类名取既有常量，不许另写字面量（Spec §C1）")
        self.assertIn(RESET_ID_CONST, body,
                      "复位按钮 id 取既有常量，不许另写一个（Spec §C1）")

    def test_b3_part_figure_call_site_turns_annotations_on(self):
        body = function_body(OPEN_FN)
        self.assertIn("includeAnnotations", body,
                      "2.1 件图的调用点要显式打开标注（Spec §C4）")

    def test_b4_preview_still_builds_one_object_url(self):
        text = read_text(APP_JS)
        self.assertEqual(text.count("URL.createObjectURL"), 1,
                         "预览的其它分支一个不动：全文件仍只有一处 createObjectURL（Spec §C5）")


# --------------------------------------------------------------------------- #
# C 组：CSS 数值
# --------------------------------------------------------------------------- #
class CCssNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = read_text(DRAWING_FLOW_CSS)

    def _block(self, selector):
        at = self.css.index(selector)
        return self.css[at:self.css.index("}", at) + 1]

    def test_c1_shape_viewport_is_square(self):
        block = self._block(".packaging-part-shape-viewport {")
        self.assertIn("aspect-ratio: 1 / 1", block,
                      "件图视口必须随栏宽保持正方形：%r" % block)

    def test_c2_plan_svg_min_height_is_twice(self):
        block = self._block(".packaging-cad-plan-svg {")
        self.assertIn("min-height: 440px", block,
                      "未选中态整图的高度翻倍（Spec §C3：220px → 440px）：%r" % block)

    def test_c3_preview_viewport_has_its_own_height(self):
        self.assertIn(".file-preview-drawing .packaging-part-shape-viewport", self.css,
                      "预览里的视口要自己给高度（Spec §C3）")

    def test_c4_annotation_style_is_declared(self):
        self.assertIn(".packaging-cad-scene-entity.is-annotation", self.css,
                      "补画的标注要有虚线 / 淡色样式（Spec §C4）")


# --------------------------------------------------------------------------- #
# D 组：护栏（现状即绿，不许回退）
# --------------------------------------------------------------------------- #
class DGuards(unittest.TestCase):
    def test_d1_shape_caliber_still_subtracts_annotations(self):
        body = function_body(ENTITIES_FN)
        self.assertIn("annotations[id]", body,
                      "形状口径仍要摘掉标注（`## 485`，Spec §C4/§C5）")

    def test_d2_viewport_binding_still_handles_drag_wheel_and_reset(self):
        body = function_body(BIND_FN)
        for token in ("pointerdown", "pointermove", "wheel", "ctrlKey"):
            self.assertIn(token, body, "既有视口交互不许回退：缺 %s（Spec §C1）" % token)

    def test_d3_panel_still_draws_the_outline_points(self):
        body = function_body(PANEL_FN)
        self.assertIn("outline.points", body,
                      "本批不动那块轮廓图（Spec §3.1/§5.2）：轮廓仍按后端给的点画")

    def test_d4_app_js_parses(self):
        proc = subprocess.run(["node", "--check", str(APP_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, "app.js 语法要过：%s" % (proc.stderr or "")[:400])


if __name__ == "__main__":
    unittest.main()
