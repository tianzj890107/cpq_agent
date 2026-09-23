r"""红测：2.1 左栏零件行「标题一行 / 尺寸另起一行 / 不出卡片」+ 右栏零件形状可缩放拖拽 + 右栏整块可滚。

Spec：`docs/specs/packaging-2-1-parts-row-layout-and-shape-viewport.md`

用户原话（2026-09-23）：

> 现在零件清单里面文字没渲染 应该标题一行然后尺寸换行 注意文字大小不要超出来卡片
> 然后右边显示的这个零件本身也没有做缩放或者拖拽功能看起来有点奇怪
> 然后滚动应该是图片也能向上滚动，就是右侧看板应该全都能滚动，现在除开零件视图下面只剩一点点能滚动了

现状缺口（HEAD `872898b` 工作副本只读）：
  · `app.js::renderPackagingBusinessTree()`（`:3251-3255`）把 `.part-icon` / `.part-name` / `.part-meta` /
    `.part-note` **平铺**成同一层，而 `workbench.css:46` 的 `.part-item{display:flex;align-items:center;…}`
    是单行 flex ⇒ 四块挤一行；对照 `app.js::renderNode()`（`:4694-4699`）用的是
    `.part-info{flex:1;min-width:0}` 包住三行的**正确**版式；
  · **全库 0 条 `.part-meta` 规则**（`grep -rn "part-meta" tech_app/frontend/` 只有 `app.js:3253`）
    ⇒ 尺寸行落到浏览器默认 16px，比同行件名（12px）还大，最长一行
    `展开 443.523×492.62 mm · DESIGN / SAMPLE` 必然撑破行宽，再被
    `workbench.css:138` 的 `.drawing-parts-column{…;overflow-x:hidden}` 直接裁掉；
  · `app.js::openPackagingBusinessPart()`（`:3342-3350`）的 `ready` 分支只写 `innerHTML`：形状是死图，
    没有视口、没有复位；`grep -n "pointerdown\|wheel\|ctrlKey" app.js` = **0 命中**；
  · 滚动是三套：`workbench.css:139` 列 `overflow:hidden` → `:239` `#modelPanes{overflow-y:auto}` →
    `drawing-flow.css:39-44` 面板 `overflow:auto`；且 `workbench.css:312-316` 在 `[data-qq-fill]` 下让
    `#packagingPartPanel{flex:1}` → `.packaging-part-outline{flex:1}` → `svg{height:100%}`，
    形状块吃掉全部剩余高度，"图片也能向上滚"做不到。

纪律：只读源码 / CSS + `node -e` 抽纯函数真跑；不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
WORKBENCH_CSS = ROOT / "tech_app" / "frontend" / "workbench.css"
DRAWING_FLOW_CSS = ROOT / "tech_app" / "frontend" / "drawing-flow.css"

TREE_FN = "renderPackagingBusinessTree"
OPEN_FN = "openPackagingBusinessPart"
BIND_FN = "bindPackagingPartShapeInteractions"
VIEWPORT_CLASS = "packaging-part-shape-viewport"
RESET_ID = "packagingPartReset"

CLAMP_FN = "packagingPartShapeZoomClamp"
NEXT_FN = "packagingPartShapeNextState"
CSS_FN = "packagingPartShapeTransformCss"
LABEL_FN = "packagingPartShapeZoomLabel"

SHAPE_LOADING = "正在读取这一件的形状…"

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
// 非有限数（`float("nan")`）过不了 JSON：Python 那边写成哨兵串，这里还原回真 `NaN`；
// 造调用时再把它写成字面量 `NaN`（`JSON.stringify(NaN)` 会变成 `null`，那就测不到 NaN 了）。
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
    """`json.dumps` 会把 `float("nan")` 写成裸 `NaN`（不是合法 JSON，node 的 JSON.parse 直接抛），
    所以先换成哨兵串；断言一字不动，只是让这条用例跑得起来。"""
    if isinstance(value, float) and math.isnan(value):
        return "__NaN__"
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    return value


def run_cases(name: str, cases, timeout: int = 60):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, json.dumps(json_safe(cases))],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def call(name: str, args):
    payload = run_cases(name, [args])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2.2）" % name)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def css_blocks(text: str):
    """把 CSS 切成 (选择器, 声明块)。注释先摘掉，免得注释里的括号把块切坏。"""
    body = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return [(m.group(1).strip(), m.group(2)) for m in
            re.finditer(r"([^{}]+)\{([^{}]*)\}", body)]


def px_value(decls: str, prop: str):
    """取一条声明里的 px 数值；没有该声明就回 None。"""
    match = re.search(r"(?<![\w-])" + prop + r"\s*:\s*([0-9.]+)px", decls)
    return float(match.group(1)) if match else None


def blocks_matching(blocks, *selector_parts):
    return [decls for selector, decls in blocks
            if all(part in selector for part in selector_parts)]


def has_decl(decls_list, pattern: str) -> bool:
    return any(re.search(pattern, decls) for decls in decls_list)


# --------------------------------------------------------------------------- #
# A 组：左栏业务部件行的版式（标题一行 / 尺寸一行 / 不出卡片）
# --------------------------------------------------------------------------- #
class APartsRowLayout(unittest.TestCase):
    def test_a1_row_has_a_text_container(self):
        body = function_body(TREE_FN)
        self.assertTrue(body, "app.js 缺少 renderPackagingBusinessTree()")
        self.assertIn("part-body", body,
                      "行的三行文字必须装进一个文本容器（版式靠容器，不靠 flex 平铺，Spec §2.1）")
        self.assertLess(body.index("part-body"), body.index("part-name"),
                        "容器要先开、标题在里面（Spec §2.1）")

    def test_a2_meta_line_has_its_own_font_size(self):
        blocks = css_blocks(read_text(WORKBENCH_CSS) + read_text(DRAWING_FLOW_CSS))
        sizes = [px_value(d, "font-size") for d in blocks_matching(blocks, ".part-meta")]
        sizes = [s for s in sizes if s is not None]
        self.assertTrue(sizes, "`.part-meta` 一条字号规则都没有 ⇒ 尺寸行落回浏览器默认 16px（Spec §2.1）")
        self.assertLessEqual(min(sizes), 11,
                             "尺寸行字号必须 ≤ 11px，不许比同一行的件名还大（Spec §2.1）")

    def test_a3_three_lines_keep_the_font_size_closed_set(self):
        blocks = css_blocks(read_text(WORKBENCH_CSS) + read_text(DRAWING_FLOW_CSS))
        name_sizes = [px_value(d, "font-size") for d in blocks_matching(blocks, ".part-name")]
        name_sizes = [s for s in name_sizes if s is not None]
        self.assertIn(12.0, name_sizes, "标题行字号仍是 12px（Spec §2.1）")
        note_sizes = [px_value(d, "font-size") for d in blocks_matching(blocks, ".part-note")]
        note_sizes = [s for s in note_sizes if s is not None]
        self.assertTrue(note_sizes, "状态行必须有字号（Spec §2.1）")
        self.assertLessEqual(min(note_sizes), 11,
                             "状态行字号必须 ≤ 11px（Spec §2.1）")

    def test_a4_text_container_can_shrink(self):
        blocks = css_blocks(read_text(WORKBENCH_CSS) + read_text(DRAWING_FLOW_CSS))
        found = has_decl(blocks_matching(blocks, ".part-body"), r"min-width\s*:\s*0")
        self.assertTrue(found,
                        "`.part-body` 必须 `min-width:0` —— 少了它 flex 子项不收缩，照样撑破（Spec §2.1）")

    def test_a5_row_stacks_instead_of_one_line(self):
        blocks = css_blocks(read_text(WORKBENCH_CSS) + read_text(DRAWING_FLOW_CSS))
        decls_list = blocks_matching(blocks, ".packaging-business-part")
        found = has_decl(decls_list, r"flex-wrap\s*:\s*wrap") or \
            has_decl(decls_list, r"flex-direction\s*:\s*column")
        self.assertTrue(found, "业务部件行必须能纵向堆叠（`flex-wrap:wrap` 或 `flex-direction:column`，Spec §2.1）")

    def test_a6_long_text_wraps_instead_of_being_clipped(self):
        blocks = css_blocks(read_text(WORKBENCH_CSS) + read_text(DRAWING_FLOW_CSS))
        decls_list = blocks_matching(blocks, ".part-body") + blocks_matching(blocks, ".part-meta")
        found = has_decl(decls_list, r"overflow-wrap\s*:\s*(anywhere|break-word)") or \
            has_decl(decls_list, r"word-break\s*:\s*(break-word|break-all)")
        self.assertTrue(found,
                        "长文本必须换行/断词；被 `overflow-x:hidden` 裁掉不算换行（Spec §2.1）")

    def test_a7_row_carries_the_full_text_on_title(self):
        body = function_body(TREE_FN)
        self.assertIn("title=", body,
                      "行上要给完整「编号 件名 + 尺寸」的 title，裁不掉也 hover 得到（Spec §2.1）")

    def test_a8_result_caliber_is_untouched(self):
        body = function_body(TREE_FN)
        self.assertIn("业务部件 ${rows.length} 件（", body,
                      "结果口径那句计数不许被版式改动带走（Spec §2.4）")
        self.assertIn("data-qq-part-toggle", body,
                      "展开「构成」的开关不许被版式改动带走（Spec §2.4）")


# --------------------------------------------------------------------------- #
# B 组：形状视口的缩放 / 平移数学（纯函数，node 真跑）
# --------------------------------------------------------------------------- #
class BShapeViewportMath(unittest.TestCase):
    def assert_state(self, got, k, tx, ty, msg=""):
        self.assertIsInstance(got, dict, "state 必须是对象：%r %s" % (got, msg))
        for key, want in (("k", k), ("tx", tx), ("ty", ty)):
            self.assertIn(key, got, "state 缺 %s（Spec §2.2）%s" % (key, msg))
            self.assertAlmostEqual(float(got[key]), float(want), places=6,
                                   msg="%s 的 %s 不对（Spec §2.2）" % (msg, key))

    def test_b1_zoom_clamp(self):
        for raw, want in ((0.05, 0.2), (0.2, 0.2), (3, 3), (8, 8), (99, 8)):
            self.assertAlmostEqual(float(call(CLAMP_FN, [raw])), want, places=6,
                                   msg="缩放倍数夹取不准（Spec §2.2）：%r" % (raw,))
        for raw in (None, "abc", float("nan")):
            self.assertAlmostEqual(float(call(CLAMP_FN, [raw])), 1.0, places=6,
                                   msg="算不出来的倍数要回「适应窗口」=1（Spec §2.2）：%r" % (raw,))

    def test_b2_state_is_normalized(self):
        for broken in (None, {}, {"k": "x"}, {"k": 2, "tx": None}):
            self.assert_state(call(NEXT_FN, [broken, {"type": "pan", "dx": 0, "dy": 0}]),
                              1, 0, 0, "非法 state 要归一成初始态（Spec §2.2）：%r" % (broken,))
        self.assert_state(call(NEXT_FN, [None, {"type": "nope"}]), 1, 0, 0,
                          "未知动作原样返回归一化后的 state（Spec §2.2）")

    def test_b3_pan_moves_without_touching_zoom(self):
        got = call(NEXT_FN, [{"k": 2, "tx": -100, "ty": -50}, {"type": "pan", "dx": 10, "dy": -4}])
        self.assert_state(got, 2, -90, -54, "拖拽只动 tx/ty（Spec §2.2）")

    def test_b4_zoom_keeps_the_pointer_anchor(self):
        got = call(NEXT_FN, [{"k": 1, "tx": 0, "ty": 0},
                             {"type": "zoom", "factor": 2, "at": {"x": 100, "y": 50}}])
        self.assert_state(got, 2, -100, -50, "以指针为锚点放大 2 倍（Spec §2.2）")

    def test_b5_anchor_does_not_jump_when_clamped(self):
        got = call(NEXT_FN, [{"k": 7, "tx": 0, "ty": 0},
                             {"type": "zoom", "factor": 2, "at": {"x": 70, "y": 0}}])
        self.assert_state(got, 8, -10, 0,
                          "夹到上限时锚点仍不跳（按实际生效倍数 r = k2/k 算，Spec §2.2）")

    def test_b6_reset_goes_back_to_fit(self):
        got = call(NEXT_FN, [{"k": 3.5, "tx": -200, "ty": 42}, {"type": "reset"}])
        self.assert_state(got, 1, 0, 0, "复位 = 适应窗口（Spec §2.2）")

    def test_b7_transform_css_and_label(self):
        self.assertEqual(
            "translate(-100px, -50px) scale(2)",
            call(CSS_FN, [{"k": 2, "tx": -100, "ty": -50}]),
            "缩放/平移只许写成 transform（不改 viewBox，Spec §2.2）")
        self.assertEqual("translate(0px, 0px) scale(1)", call(CSS_FN, [{"k": 1, "tx": 0, "ty": 0}]),
                         "初始态就是适应窗口（Spec §2.2）")
        self.assertEqual("100%", call(LABEL_FN, [{"k": 1, "tx": 0, "ty": 0}]),
                         "倍数文案（Spec §2.2）")
        self.assertEqual("800%", call(LABEL_FN, [{"k": 8, "tx": 0, "ty": 0}]),
                         "倍数文案（Spec §2.2）")

    def test_b8_math_stays_pure(self):
        for name in (CLAMP_FN, NEXT_FN, CSS_FN, LABEL_FN):
            body = function_body(name)
            self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.2）" % name)
            for token in ("document.", "window.", "fetch(", "localStorage", "getBoundingClientRect"):
                self.assertNotIn(token, body,
                                 "%s() 必须是纯函数、不碰 DOM / 网络（Spec §2.2）：%s" % (name, token))


# --------------------------------------------------------------------------- #
# C 组：形状视口的交互接线
# --------------------------------------------------------------------------- #
class CShapeViewportWiring(unittest.TestCase):
    def test_c1_ready_state_installs_the_viewport(self):
        src = read_text(APP_JS)
        self.assertIn(VIEWPORT_CLASS, src,
                      "形状要有自己的视口容器（Spec §2.2）")
        self.assertIn(RESET_ID, src,
                      "视口里要有「适应窗口」复位按钮（Spec §2.2）")
        body = function_body(OPEN_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % OPEN_FN)
        self.assertIn(BIND_FN + "(", body,
                      "点开一件、形状画好之后必须装上缩放/拖拽交互（Spec §2.2）")

    def test_c2_pointer_and_wheel_events(self):
        body = function_body(BIND_FN)
        self.assertTrue(body, "app.js 缺少 %s()（Spec §2.2）" % BIND_FN)
        for event in ("pointerdown", "pointermove", "pointerup", "pointercancel", "wheel"):
            self.assertIn(event, body, "视口交互缺事件 %s（Spec §2.2）" % event)

    def test_c3_drag_captures_the_pointer(self):
        body = function_body(BIND_FN)
        self.assertIn("setPointerCapture", body,
                      "拖拽要抓指针，否则划出视口就丢事件（Spec §2.2）")

    def test_c4_plain_wheel_is_not_zoom(self):
        body = function_body(BIND_FN)
        self.assertIn("ctrlKey", body,
                      "滚轮缩放必须判 ctrlKey（触控板捏合也是它）；裸滚轮留给整栏滚动（Spec §2.2/§2.3）")

    def test_c5_state_is_readable_from_the_dom(self):
        src = read_text(APP_JS)
        self.assertTrue("data-qq-shape-zoom" in src or "qqShapeZoom" in src,
                        "宿主上要写当前倍数（Spec §2.2）")
        self.assertTrue("data-qq-shape-pan" in src or "qqShapePan" in src,
                        "宿主上要写当前平移量（Spec §2.2）")

    def test_c6_drag_state_is_readable_from_the_dom(self):
        src = read_text(APP_JS)
        self.assertTrue("data-qq-shape-dragging" in src or "qqShapeDragging" in src,
                        "拖拽中要有自己的钩子（Spec §2.2）")


# --------------------------------------------------------------------------- #
# D 组：右栏看板只有一个滚动容器
# --------------------------------------------------------------------------- #
class DSingleScrollContainer(unittest.TestCase):
    def setUp(self):
        self.blocks = css_blocks(read_text(WORKBENCH_CSS) + read_text(DRAWING_FLOW_CSS))

    def test_d1_column_becomes_the_scroller(self):
        decls_list = blocks_matching(self.blocks, "data-qq-fill", ".drawing-model-column")
        self.assertTrue(has_decl(decls_list, r"overflow-y\s*:\s*auto"),
                        "包装 2.1 下纵向滚动权属于列（Spec §2.3）")

    def test_d2_panel_no_longer_eats_the_height(self):
        decls_list = blocks_matching(self.blocks, "data-qq-fill", "packagingPartPanel")
        self.assertFalse(has_decl(decls_list, r"flex(-grow)?\s*:\s*1"),
                         "面板不许再 flex:1 吃掉剩余高度（Spec §2.3）")

    def test_d3_shape_block_no_longer_eats_the_height(self):
        decls_list = blocks_matching(self.blocks, "data-qq-fill", ".packaging-part-outline")
        self.assertFalse(has_decl(decls_list, r"flex(-grow)?\s*:\s*1"),
                         "形状块不许再 flex:1 —— 它就是「下面只剩一点点」的根因（Spec §2.3）")

    def test_d4_shape_svg_no_longer_fills_the_column(self):
        decls_list = blocks_matching(self.blocks, "data-qq-fill", ".packaging-part-outline", "svg")
        self.assertFalse(has_decl(decls_list, r"height\s*:\s*100%"),
                         "svg 不许 height:100% 吃掉整栏（Spec §2.3）")

    def test_d5_viewport_is_bounded_and_clipped(self):
        decls_list = blocks_matching(self.blocks, "." + VIEWPORT_CLASS)
        self.assertTrue(has_decl(decls_list, r"overflow\s*:\s*hidden") or
                        has_decl(decls_list, r"overflow-y?\s*:\s*hidden"),
                        "视口要裁剪（Spec §2.3）")
        self.assertTrue(has_decl(decls_list, r"(max-)?height\s*:[^;]*(vh|px)"),
                        "视口高度要有上限，不能吃掉剩余（Spec §2.3）")
        self.assertTrue(has_decl(decls_list, r"touch-action\s*:"),
                        "视口要 touch-action，拖拽别把页面一起拖走（Spec §2.3）")

    def test_d6_model_panes_gives_up_the_scroll(self):
        decls_list = blocks_matching(self.blocks, "data-qq-fill", "modelPanes")
        self.assertTrue(has_decl(decls_list, r"overflow(-y)?\s*:\s*visible"),
                        "fill 下 #modelPanes 不许再自己滚（双滚动 + 图片被钉住，Spec §2.3）")


# --------------------------------------------------------------------------- #
# E 组：护栏（本批不许动的东西）
# --------------------------------------------------------------------------- #
class EGuardrails(unittest.TestCase):
    def test_e1_shape_keeps_its_three_states(self):
        body = function_body(OPEN_FN)
        for state in ('"ready"', '"loading"', '"unavailable"'):
            self.assertIn(state, body, "三态口径不动（Spec §2.4）：%s" % state)

    def test_e2_loading_copy_is_verbatim(self):
        self.assertIn(SHAPE_LOADING, read_text(APP_JS),
                      "加载态文案逐字保留（Spec §2.4）")

    def test_e3_3d_base_rule_is_not_touched(self):
        blocks = css_blocks(read_text(WORKBENCH_CSS) + read_text(DRAWING_FLOW_CSS))
        for selector, decls in blocks:
            if ".view-3d-content" in selector and "data-qq" not in selector:
                self.assertNotIn("display:none", decls.replace(" ", ""),
                                 "撤 3D 只在包装巷子（靠 [data-qq-no-3d]），基础规则不动（Spec §2.4）")

    def test_e4_viewbox_still_comes_from_the_backend_range(self):
        body = function_body("packagingBusinessPartOutlineHtml")
        self.assertTrue(body, "app.js 缺少 packagingBusinessPartOutlineHtml()")
        self.assertIn("packagingCadPlanViewBox(", body,
                      "缩放只改 transform，viewBox 仍由后端坐标范围产出、前端不做几何求解（Spec §2.4）")


if __name__ == "__main__":
    unittest.main()
