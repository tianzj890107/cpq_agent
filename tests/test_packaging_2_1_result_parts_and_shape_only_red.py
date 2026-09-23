"""红测：2.1 的结果只能是那二十多件（真件名）、点开一件能看到它的两百多个分量、点一件直接看样子、
右栏 3D 区块整块撤掉并占满。

Spec：`docs/specs/packaging-2-1-result-parts-and-shape-only-pane.md`

用户原话（2026-09-23）：

> 解析出来的结果必须要是二十多个零件，然后点击之后在右边直接看样子，就像 bom 里面那样，
> 然后那个 3D 的区域整个就不要了 实际展示的内容直接占满右边看板就行了
> 不是一定要 28 个 但是应该是那二十多个 然后每个零件都是正确的名字，点开之后可以是别的更多的两百多个

现状缺口（HEAD `3902b4a` 工作副本只读；酒盒.dwg 隔离真跑读数）：
  · 业务部件文档缺失时 `renderTree()` 退回几何分量：顶层 `已显示 64 件，共 263 件（k 种形状）`、
    折叠区 263 行 `DWG-Pxx 图纸零件 Pxx` —— 名字是占位名（`packaging_parts.py:1863`）；
  · 业务部件行点不开：看不到这一件由哪几个几何分量构成（`263` 与 `28` 之间只有右栏图，没有归属入口）；
  · `openPackagingBusinessPart()`（`app.js:3171`）画形状时若 `currentPackagingCadPlan` 还是 `null`
    （坐标没回来）就直接落进状态文案，之后再也不会重画；没有加载态、没有 `data-qq-part-shape`；
  · `enterDrawingFlowPanes()`（`app.js:1329`）只隐藏 `#viewer`，`.part-details`（零件信息）与
    `details.parameter-panel`（专家参数）在没选中零件时照旧摆着（`index.html:220 / :222`）；
  · `main.py` 业务部件只有 GET / PUT binding / import（要 Excel）/ thumbnail，**没有**按图纸补推导的端点。

纪律：`node -e` 抽具名函数体真跑（纯函数）+ 源码/CSS 守卫；不起服务、不发 HTTP、不连 PG / 34、
不写业务数据、不跑真样本（只引用 Spec §1 里已量到的读数）。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
INDEX_HTML = ROOT / "tech_app" / "frontend" / "index.html"
WORKBENCH_CSS = ROOT / "tech_app" / "frontend" / "workbench.css"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

NAME_FN = "packagingBusinessPartName"
COMPONENTS_FN = "packagingBusinessPartComponentsLine"
UNNAMED = "未命名业务部件"
UNBOUND_LINE = "这一件还没定位到几何分量"
DIM_LINE = "共 3 个几何分量：DWG-C001 / DWG-C007 / DWG-C012"
SHAPE_LOADING = "正在读取这一件的形状…"
PICK_HINT = "从左栏选一件零件，这里直接看它的形状。"

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
  const call = name + "(" + args.map(a => JSON.stringify(a) + "").join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def run_cases(name: str, cases, timeout: int = 60):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, json.dumps(cases)],
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
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2）" % name)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


# --------------------------------------------------------------------------- #
# A 组：结果口径与件名
# --------------------------------------------------------------------------- #
class AResultIsTheParts(unittest.TestCase):
    def test_a1_result_branch_declares_its_caliber(self):
        body = function_body("renderTree")
        self.assertTrue(body, "app.js 缺少 renderTree()")
        self.assertIn("data-qq-parts-result", body,
                      "左栏结果必须说清自己的口径（Spec §2.1）")

    def test_a2_every_row_has_a_real_name(self):
        self.assertEqual("内盒1灰板", call(NAME_FN, [{"name": "内盒1灰板"}]),
                         "有件名就原样用（Spec §2.1b）")
        self.assertEqual("内盒1灰板", call(NAME_FN, [{"name": "  内盒1灰板  "}]),
                         "件名要去掉首尾空白（Spec §2.1b）")

    def test_a3_missing_name_falls_back_to_one_word_only(self):
        for row in ({"name": ""}, {"name": "   "}, {"business_part_code": "QG-01"},
                    {"name": None, "business_part_code": "QG-01"}, {}):
            self.assertEqual(UNNAMED, call(NAME_FN, [row]),
                             "没有件名时只许给唯一的兜底名，不许回落成分量占位名或编码（Spec §2.1b）：%r"
                             % (row,))
        self.assertEqual(UNNAMED, call(NAME_FN, [None]),
                         "空行也给同一个兜底（Spec §2.1b）")

    def test_a4_tree_uses_the_name_helper(self):
        body = function_body("renderPackagingBusinessTree")
        self.assertTrue(body, "app.js 缺少 renderPackagingBusinessTree()")
        self.assertIn(NAME_FN + "(", body,
                      "业务部件树的行名必须走同一个取名字的口径（Spec §2.1b）")
        self.assertIn("data-qq-name-missing", body,
                      "兜底名要给一个自己的钩子（Spec §2.1b）")

    def test_a5_geometry_placeholder_names_stay_out_of_the_result(self):
        src = read_text(APP_JS)
        body = function_body("renderPackagingBusinessTree")
        self.assertNotIn("图纸零件 P", body,
                         "几何分量的占位名不许出现在业务部件结果里（Spec §2.1b）")
        self.assertIn("qqGeometryDiagnostics", src,
                      "几何分量仍留在折叠诊断区（护栏，Spec §2.7）")

    def test_a6_source_disclosure_is_untouched(self):
        src = read_text(APP_JS)
        self.assertIn("从图纸推导（待人工确认）", src,
                      "业务部件的来源披露逐字保留（Spec §2.1b/§2.7）")


# --------------------------------------------------------------------------- #
# B 组：点开一件看它的构成（两百多个分量按件归属）
# --------------------------------------------------------------------------- #
class BExpandToComponents(unittest.TestCase):
    def test_b1_rows_can_expand(self):
        body = function_body("renderPackagingBusinessTree")
        self.assertIn("data-qq-part-toggle", body,
                      "业务部件行要有展开开关（Spec §2.1c）")
        self.assertIn("data-qq-part-components", body,
                      "展开容器要有自己的钩子（Spec §2.1c）")

    def test_b2_components_line_reads_the_binding(self):
        row = {"business_part_code": "QG-03",
               "geometry_binding": {"component_ids": ["DWG-C001", "DWG-C007", "DWG-C012"]}}
        components = [{"component_id": "DWG-C001"}, {"component_id": "DWG-C007"},
                      {"component_id": "DWG-C012"}]
        self.assertEqual(DIM_LINE, call(COMPONENTS_FN, [row, components]),
                         "展开了就要逐件点名这一件的分量（Spec §2.1c）")
        self.assertEqual(UNBOUND_LINE, call(COMPONENTS_FN, [{"geometry_binding": {}}, []]),
                         "没定位到分量时要说清（Spec §2.1c）")
        self.assertEqual("", call(COMPONENTS_FN, [None, []]),
                         "空行不说话（Spec §2.1c）")

    def test_b3_expansion_does_not_change_the_result_count(self):
        body = function_body("renderPackagingBusinessTree")
        self.assertIn("业务部件 ${rows.length} 件（", body,
                      "结果区那句计数仍按业务部件行数算（Spec §2.1c/§2.7）")

    def test_b4_components_come_from_the_evidence_binding(self):
        body = function_body(COMPONENTS_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.1c）" % COMPONENTS_FN)
        self.assertIn("component_ids", body,
                      "分量清单只能按证据层的 component_ids 取，前端不另算（Spec §2.1c）")
        for token in ("document.", "window.", "fetch(", "localStorage", "Math.hypot"):
            self.assertNotIn(token, body,
                             "构成行必须是纯函数、不做几何求解（Spec §2.1c）：%s" % token)


# --------------------------------------------------------------------------- #
# C 组：点一件直接看样子（三态 + 坐标竞态）
# --------------------------------------------------------------------------- #
class CShapeOnClick(unittest.TestCase):
    def test_c1_shape_has_three_states(self):
        body = function_body("openPackagingBusinessPart")
        self.assertTrue(body, "app.js 缺少 openPackagingBusinessPart()")
        self.assertIn("data-qq-part-shape", body,
                      "形状区要有三态钩子（Spec §2.2）")
        self.assertIn(SHAPE_LOADING, body,
                      "点开就先给加载态，不许留白（Spec §2.2）")

    def test_c2_pending_part_is_remembered(self):
        body = function_body("openPackagingBusinessPart")
        self.assertIn("pendingPackagingShapePartCode", body,
                      "坐标还没回来时要记住这一件（Spec §2.2）")

    def test_c3_coordinates_repaint_the_pending_part(self):
        body = function_body("renderPackagingCadPlan")
        self.assertTrue(body, "app.js 缺少 renderPackagingCadPlan()")
        self.assertIn("pendingPackagingShapePartCode", body,
                      "坐标到位后必须重画同一件（Spec §2.2）")

    def test_c4_shape_still_comes_from_the_backend(self):
        src = read_text(APP_JS)
        self.assertIn("packagingBusinessPartOutlineHtml(", src,
                      "形状仍由后端坐标池拼（护栏，Spec §2.7）")
        self.assertIn("PACKAGING_BINDING_COPY", src,
                      "画不出来时的原因文案仍取既有词表（护栏，Spec §2.7）")


# --------------------------------------------------------------------------- #
# D 组：撤 3D + 占满右栏
# --------------------------------------------------------------------------- #
class DNoThreeDAndFill(unittest.TestCase):
    def test_d1_entering_the_lane_applies_the_shape_only_panes(self):
        body = function_body("enterDrawingFlowPanes")
        self.assertTrue(body, "app.js 缺少 enterDrawingFlowPanes()")
        self.assertIn("applyPackagingShapeOnlyPanes(", body,
                      "一进包装 2.1 就要撤掉 3D 区块（Spec §2.3）")

    def test_d2_all_three_3d_blocks_are_hidden(self):
        body = function_body("applyPackagingShapeOnlyPanes")
        self.assertTrue(body, "app.js 缺少 applyPackagingShapeOnlyPanes()（Spec §2.3）")
        for token in ("viewer", "partDetail", "parameterEditor", "parameter-panel"):
            self.assertIn(token, body,
                          "3D 口径的三块都要收起（Spec §2.3）：缺 %s" % token)
        self.assertIn("data-qq-no-3d", body,
                      "撤 3D 要有自己的钩子（Spec §2.3）")
        self.assertIn("qqFill", body,
                      "占满右栏要有自己的钩子（Spec §2.4）")

    def test_d3_css_rules_exist(self):
        css = read_text(WORKBENCH_CSS)
        self.assertIn("[data-qq-no-3d", css,
                      "包装 2.1 的 3D 区不占位要有 CSS 规则（Spec §2.3）")
        self.assertIn("[data-qq-fill", css,
                      "形状区占满要有 CSS 规则（Spec §2.4）")

    def test_d4_idle_state_has_one_hint(self):
        src = read_text(APP_JS) + read_text(INDEX_HTML)
        self.assertIn(PICK_HINT, src,
                      "没选零件时只留一句引导（Spec §2.4）")

    def test_d5_non_packaging_3d_is_untouched(self):
        page = read_text(INDEX_HTML)
        self.assertIn('id="viewer"', page, "3D 画布节点仍要在（非包装流程用）（Spec §2.7）")
        self.assertIn('id="actionSheet"', page, "既有动作表逐字保留（Spec §2.7）")
        src = read_text(APP_JS)
        self.assertIn("function renderDrawingEntry(", src,
                      "入口判定仍是唯一来源（Spec §2.7）")
        self.assertIn("function showPackagingPartPane(", src,
                      "既有'选中零件才切面板'的函数保留（Spec §2.7）")


# --------------------------------------------------------------------------- #
# E 组：老项目就地补推导（不许让人重建项目）
# --------------------------------------------------------------------------- #
class EEnsureBusinessParts(unittest.TestCase):
    def test_e1_refresh_triggers_the_derive(self):
        body = function_body("refreshPackagingParts")
        self.assertTrue(body, "app.js 缺少 refreshPackagingParts()")
        self.assertIn("ensurePackagingBusinessParts(", body,
                      "读不到业务部件清单时必须先补推导（Spec §2.6）")

    def test_e2_frontend_calls_the_derive_endpoint(self):
        src = read_text(APP_JS)
        self.assertIn("packaging-business-parts/derive", src,
                      "补推导要有自己的端点（Spec §2.6）")

    def test_e3_backend_declares_the_path(self):
        text = read_text(MAIN_PY)
        self.assertIn("PACKAGING_BUSINESS_PARTS_DERIVE_PATH", text,
                      "后端要有唯一的路径常量（Spec §2.6）")
        self.assertIn("/requirement/packaging-business-parts/derive", text,
                      "端点路径逐字（Spec §2.6）")

    def test_e4_backend_reuses_the_resolver_and_persistence(self):
        text = read_text(MAIN_PY)
        at = text.find("PACKAGING_BUSINESS_PARTS_DERIVE_PATH")
        self.assertGreaterEqual(at, 0, "还没有补推导端点（Spec §2.6）")
        window = text[at:at + 6000]
        self.assertIn("resolve_business_parts", window,
                      "补推导必须复用同一个解析器（Spec §2.6）")
        self.assertIn("save_business_parts", window,
                      "补推导必须走同一份落库（Spec §2.6）")

    def test_e5_derive_is_not_destructive(self):
        text = read_text(MAIN_PY)
        at = text.find("PACKAGING_BUSINESS_PARTS_DERIVE_PATH")
        window = text[at:at + 6000]
        for token in ("shutil.rmtree", ".unlink(", "os.remove"):
            self.assertNotIn(token, window,
                             "补推导不许删任何既有产物（Spec §2.6）：%s" % token)
        self.assertIn("PACKAGING_BUSINESS_PARTS_IMPORT_PATH", text,
                      "既有导入端点逐字保留（护栏，Spec §2.7）")


# --------------------------------------------------------------------------- #
# F 组：护栏
# --------------------------------------------------------------------------- #
class FGuards(unittest.TestCase):
    def test_f1_geometry_ledger_and_paging_are_kept(self):
        src = read_text(APP_JS)
        for token in ("qqGeometryDiagnostics", "has_more", "继续加载（还有 "):
            self.assertIn(token, src, "既有几何账/翻页逐字保留（Spec §2.7）：%s" % token)

    def test_f2_left_column_nodes_are_kept(self):
        page = read_text(INDEX_HTML)
        for token in ('id="tree"', "drawing-parts-column", "零件清单"):
            self.assertIn(token, page, "左栏节点逐字保留（Spec §2.7）：%s" % token)

    def test_f3_ledger_wording_from_the_previous_batch_is_kept(self):
        src = read_text(APP_JS)
        for token in ("个几何分量，共 ", "个几何分量未列出（只显示前 ", "几何分量"):
            self.assertIn(token, src, "上一批的两笔账措辞逐字保留（Spec §2.7）：%s" % token)


if __name__ == "__main__":
    unittest.main()
