"""红测：平面图点业务部件要开业务部件面板；面板要画出**绑定分量**的形状。

Spec：`docs/specs/packaging-business-part-plan-click-and-bound-outline.md`
依赖口径：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§6.2 平面图交互、data-business-part）、
          `docs/specs/packaging-cad-plan-true-outline-polygons.md`（形状渲染与取框入口）

现状缺口（源码实测，不是推断）：

  · `renderPackagingCadPlan()` 点到图元时用 `data-business-part` 的值（**业务部件编码**）去调
    `selectPackagingPart()` —— 那是几何零件通道（`GET .../packaging-parts/{code}`），业务编码
    在那里不存在 → 必然 404，右栏只剩报错；
  · `openPackagingBusinessPart()` 的轮廓区只写一句绑定状态文案，业务部件即使绑定了闭合件
    （真轮廓点已随 `## 372` 进证据层）也永远看不到形状。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_JS_PATH = ROOT / "tech_app" / "frontend" / "app.js"
APP_JS = APP_JS_PATH.read_text(encoding="utf-8")

#: 抽具名函数体交给 node 执行（可一次 eval 多个，按依赖顺序）。
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
              "packagingCadPlanOutlinePoints", "packagingCadPlanComponentSvg",
              "packagingBusinessPartComponents",
              // `## 417` 起 `packagingCadPlanComponentSvg()` 多了一支「按实体折线画」，
              // 依赖清单跟着补两个名字（**只加依赖名，断言一字未动**）。
              "packagingCadPlanSegmentPolylines", "packagingCadPlanTruncationNote"];
const bodies = {};
for (const name of names.concat(deps)) {
  const fn = extract(name);
  if (fn) bodies[name] = fn;
}
// 角色配色是 const 字面量：抽出来按 var 求值，渲染函数才能在 node 里真跑。
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


def run_cases(name: str, cases, timeout: int = 60):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS_PATH), name, name, json.dumps(cases)],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS_PATH), name, name, "body"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def value_of(name: str, *args):
    payload = run_cases(name, [list(args)])
    if payload.get("missing"):
        raise AssertionError("app.js 里没有 %s" % name)
    result = payload["results"][0]
    if not result.get("ok"):
        raise AssertionError("%s(%r) 抛错：%s" % (name, args, result.get("error")))
    return result["value"]


# 证据层的一件闭合分量（真样本的形状：有环点、有绘图包络）
CLOSED_COMPONENT = {
    "component_id": "cmp:139", "entity_ids": ["ent:1"], "layers": ["CUT"], "role": "cut",
    "geometry_component_ref": "cmp:139", "outline_status": "closed", "size_source": "closed_outline",
    "drawing_bbox": [0.0, 0.0, 100.0, 50.0],
    "outline_points": [[0.0, 0.0], [100.0, 0.0], [100.0, 50.0], [0.0, 50.0]],
}
OPEN_COMPONENT = {
    "component_id": "cmp:17", "entity_ids": ["ent:2"], "layers": ["CREASE"], "role": "crease",
    "geometry_component_ref": "cmp:17", "outline_status": "open", "size_source": "component_bbox",
    "drawing_bbox": [200.0, 0.0, 240.0, 20.0], "outline_points": None,
}


def doc(components):
    return {"geometry_evidence": {"components": list(components),
                                  "component_total": len(components),
                                  "kept_component_total": len(components)}}


# --------------------------------------------------------------------------- #
# A 组：点选落点（纯函数，node 真跑）
# --------------------------------------------------------------------------- #
class AClickTarget(unittest.TestCase):
    def test_a1_business_code_is_a_business_target(self):
        self.assertEqual({"kind": "business", "code": "JWXR21-P03"},
                         value_of("packagingCadPlanClickTarget", "JWXR21-P03"))

    def test_a2_blank_is_unbound(self):
        for raw in ("", "   ", None):
            self.assertEqual({"kind": "unbound", "code": ""},
                             value_of("packagingCadPlanClickTarget", raw),
                             "没有业务归属的图元必须走既有未归属提示：%r" % (raw,))

    def test_a3_whitespace_is_trimmed(self):
        self.assertEqual({"kind": "business", "code": "JWXR21-P03"},
                         value_of("packagingCadPlanClickTarget", "  JWXR21-P03 "))


# --------------------------------------------------------------------------- #
# B 组：接线（函数体断言）
# --------------------------------------------------------------------------- #
class BPlanClickWiring(unittest.TestCase):
    def test_b1_plan_click_opens_the_business_panel(self):
        body = function_body("renderPackagingCadPlan")
        self.assertTrue(body, "renderPackagingCadPlan() 找不到（签名变了？）")
        self.assertIn("packagingCadPlanClickTarget", body, "点选落点必须走纯函数（Spec §C1）")
        self.assertIn("openPackagingBusinessPart", body,
                      "点到业务部件图元要开业务部件面板，不是几何零件面板")
        self.assertNotIn("selectPackagingPart(owner)", body,
                         "不许再拿业务编码去调几何零件通道（那一定 404）")

    def test_b2_unbound_click_keeps_the_existing_note(self):
        body = function_body("renderPackagingCadPlan")
        self.assertIn("PACKAGING_CAD_PLAN_UNBOUND", body, "未归属图元的提示不许丢")

    def test_b3_business_panel_renders_the_bound_outline(self):
        # 2026-09-23 按 `docs/specs/packaging-2-1-right-pane-single-part-figure.md` §3/§5.1 重指：
        # 用户要的是「一块图 = 只有这一件」（带图层颜色、可拖拽缩放），下栏那块「只有外圈」的图撤掉。
        # 断言强度不变：仍要求"这一件的图必须由具名纯函数拼出来"+ 那句文字口径保留，只是换了函数名。
        body = function_body("openPackagingBusinessPart")
        self.assertTrue(body, "openPackagingBusinessPart() 找不到（签名变了？）")
        self.assertIn("packagingPartSceneSvg", body,
                      "这一件的图必须由纯函数拼出来（Spec `packaging-2-1-right-pane-single-part-figure.md` §C3）")
        self.assertNotIn("packagingBusinessPartOutlineHtml", body,
                         "下栏那块「只有外圈」的图已按新 Spec 撤掉（§C1）")
        self.assertIn("PACKAGING_BOUND_OUTLINE_NOTE", body,
                      "「业务尺寸以权威资料为准」这句文字口径保留")

    def test_b4_geometry_part_panel_is_untouched(self):
        body = function_body("selectPackagingPart")
        self.assertNotIn("packagingBusinessPartOutlineHtml", body,
                         "几何零件通道不许被本批改掉")
        self.assertIn("packaging-parts/", body)


# --------------------------------------------------------------------------- #
# C 组：形状（纯函数，node 真跑）
# --------------------------------------------------------------------------- #
class CBoundOutline(unittest.TestCase):
    def test_c1_components_are_picked_by_component_ids(self):
        got = value_of("packagingBusinessPartComponents",
                       {"component_ids": ["cmp:17"]}, [CLOSED_COMPONENT, OPEN_COMPONENT])
        self.assertEqual(["cmp:17"], [row["component_id"] for row in got],
                         "只有绑定的分量才画，且按证据层顺序")

    def test_c2_no_binding_no_components(self):
        self.assertEqual([], value_of("packagingBusinessPartComponents", {}, [CLOSED_COMPONENT]))
        self.assertEqual([], value_of("packagingBusinessPartComponents",
                                      {"component_ids": ["cmp:missing"]}, [CLOSED_COMPONENT]))

    def test_c3_closed_component_is_drawn_as_a_polygon(self):
        html = value_of("packagingBusinessPartOutlineHtml",
                        {"component_ids": ["cmp:139"]}, doc([CLOSED_COMPONENT]))
        self.assertIn("<polygon", html, "闭合件要画真轮廓（复用平面图的渲染，Spec §C2）")
        self.assertIn("viewBox", html)

    def test_c4_open_component_is_drawn_as_a_box(self):
        html = value_of("packagingBusinessPartOutlineHtml",
                        {"component_ids": ["cmp:17"]}, doc([OPEN_COMPONENT]))
        self.assertIn("<rect", html, "开口件仍是包络矩形，不许编轮廓")
        self.assertNotIn("<polygon", html)

    def test_c5_nothing_to_draw_returns_empty(self):
        for binding, record in (({}, doc([CLOSED_COMPONENT])),
                                ({"component_ids": ["cmp:139"]}, doc([])),
                                ({"component_ids": ["cmp:139"]}, {})):
            self.assertEqual("", value_of("packagingBusinessPartOutlineHtml", binding, record),
                             "画不出来就回空串，由调用方给状态文案（Spec §C2）")


# --------------------------------------------------------------------------- #
# D 组：护栏（现状即绿）
# --------------------------------------------------------------------------- #
class DGuards(unittest.TestCase):
    def test_d1_new_functions_are_node_executable(self):
        for name in ("packagingCadPlanClickTarget", "packagingBusinessPartComponents",
                     "packagingBusinessPartOutlineHtml"):
            body = function_body(name)
            self.assertTrue(body, "缺少纯函数 %s" % name)
            for token in ("document", "sessionStorage", "localStorage", "window.", "fetch("):
                self.assertNotIn(token, body, "%s 不许引用 %s（要被 node 直接执行）" % (name, token))

    def test_d2_app_js_still_parses(self):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("未安装 node，跳过语法检查")
        completed = subprocess.run([node, "--check", str(APP_JS_PATH)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(0, completed.returncode, "app.js 语法错误：\n%s" % completed.stderr)

    def test_d3_plan_viewer_capabilities_are_untouched(self):
        for token in ("highlightPackagingBusinessPart", "fitPackagingCadPlan",
                      "packagingCadPlanComponentSvg", "data-bbox"):
            self.assertIn(token, APP_JS, "平面图既有能力丢了：%s" % token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
