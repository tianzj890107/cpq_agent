"""红测：业务部件行必须真能发起「单件工艺 / 成本」（绑不到就说清为什么）。

Spec：`docs/specs/packaging-business-part-downstream-entry.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:2574-2577` 的 `openPackagingBusinessPart()` 写死了动作区文案
    （「单件工艺 / 成本按业务部件版本另跑；…」），**一个能点的按钮都没有**；
  · 几何件那套入口（`packagingPartActionsHtml()` `:1413` → `packagingPartAnalyze()`）
    只认 `DWG-Pxx` 编码，业务部件编码（真样本 `JWXR21-P01…`）没有入口；
  · `packaging-business-parts-and-cad-plan-view.md` §12 边界 2 已把这条记为「剩下的那一半」。

纪律：`node -e` 抽顶层具名函数体执行（纯函数）+ 源码守卫 + `node --check`；
不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
禁止为了让红测转绿而修改本文件。
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

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

UNBOUND_TEXT = ("这一件还没在 CAD 图中定位到几何件；先在平面图里确认几何映射，"
                "或按清单尺寸补录后再算。")
OPEN_TEXT = "这一件绑定的几何件还没有闭合轮廓（尺寸来自包围盒），先把轮廓补出来再算。"
MISSING_TEXT = "这一件没有业务部件编码，不能发起下游。"

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
const name = process.argv[3];
const mode = process.argv[4];
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
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


def run_cases(name: str, cases):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name,
                           json.dumps(cases)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少具名函数 %s()（Spec §C1/§C3）" % name)
    return str(payload.get("body") or "")


def target(row, parts_doc):
    payload = run_cases("packagingBusinessPartDownstreamTarget", [[row, parts_doc]])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少顶层纯函数 packagingBusinessPartDownstreamTarget()（Spec §C1）")
    out = payload["results"][0]
    if not out.get("ok"):
        raise AssertionError("packagingBusinessPartDownstreamTarget() 抛异常：%s" % out.get("error"))
    return out.get("value")


def _part(code, component_id, status="closed"):
    return {"part_code": code, "component_id": component_id,
            "outline_status": status, "unfolded_length_mm": 100.0, "unfolded_width_mm": 50.0}


def _business(code, component_ids):
    return {"business_part_code": code,
            "geometry_binding": {"status": "bound" if component_ids else "unbound",
                                 "component_ids": list(component_ids)}}


PARTS_DOC = {"parts": [_part("DWG-P01", "geometry:comp:1"),
                       _part("DWG-P02", "geometry:comp:2", status="open"),
                       _part("DWG-P03", "geometry:comp:3")]}


# --------------------------------------------------------------------------- #
# T 组：只读映射（红）
# --------------------------------------------------------------------------- #
class TBusinessPartDownstreamTarget(unittest.TestCase):
    def test_t1_bound_to_closed_part_gives_the_geometric_code(self):
        out = target(_business("JWXR21-P01", ["geometry:comp:1"]), PARTS_DOC)
        self.assertTrue(out.get("ok"), "绑到闭合几何件必须能发起下游（Spec §C1 第 4 条）")
        self.assertEqual("DWG-P01", out.get("part_code"))
        self.assertEqual("", out.get("code"), "能算时不给码")

    def test_t2_no_binding_says_unbound_with_next_step(self):
        out = target(_business("JWXR21-P02", []), PARTS_DOC)
        self.assertFalse(out.get("ok"), "没绑定时不许猜一个几何件（Spec §C1 第 2 条）")
        self.assertEqual("geometry_unbound", out.get("code"))
        self.assertEqual(UNBOUND_TEXT, out.get("message"), "要说清下一步（Spec §C1 第 2 条）")

    def test_t3_binding_pointing_nowhere_is_treated_as_unbound(self):
        out = target(_business("JWXR21-P03", ["geometry:comp:999"]), PARTS_DOC)
        self.assertFalse(out.get("ok"), "命不中任何几何件 = 没定位（Spec §C1 第 2 条）")
        self.assertEqual("geometry_unbound", out.get("code"))
        self.assertEqual("", out.get("part_code"))

    def test_t4_bound_but_open_outline_says_why(self):
        out = target(_business("JWXR21-P04", ["geometry:comp:2"]), PARTS_DOC)
        self.assertFalse(out.get("ok"), "绑到的件没闭合时不许硬算（Spec §C1 第 3 条）")
        self.assertEqual("outline_open", out.get("code"))
        self.assertEqual(OPEN_TEXT, out.get("message"))

    def test_t5_missing_code_is_its_own_code(self):
        self.assertEqual("business_part_missing", target({}, PARTS_DOC).get("code"),
                         "没有业务部件编码要说自己的码（Spec §C1 第 1 条）")
        self.assertEqual(MISSING_TEXT, target({}, PARTS_DOC).get("message"))
        self.assertEqual("business_part_missing",
                         target({"business_part_code": "  "}, PARTS_DOC).get("code"))

    def test_t6_multiple_hits_pick_the_lowest_part_code(self):
        doc = {"parts": [_part("DWG-P07", "geometry:comp:5"),
                         _part("DWG-P02", "geometry:comp:5"),
                         _part("DWG-P09", "geometry:comp:5", status="open")]}
        out = target(_business("JWXR21-P05", ["geometry:comp:5"]), doc)
        self.assertTrue(out.get("ok"), "命中多件时仍应能算（Spec §C1 第 4 条）")
        self.assertEqual("DWG-P02", out.get("part_code"),
                         "多件命中取 part_code 升序第一个（确定性）")
        self.assertEqual("", out.get("code"), "能算时不给码")
        self.assertEqual("", out.get("message"), "能算时不给原因")

    def test_t7_no_geometry_document_means_unbound(self):
        self.assertEqual("geometry_unbound",
                         target(_business("JWXR21-P06", ["geometry:comp:1"]), {}).get("code"),
                         "零件文档读不到时不许凭空算（Spec §C1 第 2 条）")

    def test_t8_pure_function_is_self_contained(self):
        body = function_body("packagingBusinessPartDownstreamTarget")
        for forbidden in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(forbidden, body,
                             "纯函数体内不许出现 %s（Spec §C1）" % forbidden)


# --------------------------------------------------------------------------- #
# T 组（接线）：面板要给按钮或给原因（红）
# --------------------------------------------------------------------------- #
class TBusinessPanelWiring(unittest.TestCase):
    def test_t9_panel_calls_the_target_function(self):
        body = function_body("openPackagingBusinessPart")
        self.assertIn("packagingBusinessPartDownstreamTarget(", body,
                      "面板要算出这一件的下游目标（Spec §C2）")
        self.assertIn("data-qqBusinessDownstream", body,
                      "能算时要渲染可点的按钮组（Spec §C2）")
        self.assertIn("data-qqBusinessDownstreamReason", body,
                      "不能算时要渲染原因（Spec §C2）")

    def test_t10_analyze_reuses_the_geometric_entry(self):
        body = function_body("packagingBusinessPartAnalyze")
        self.assertIn("selectPackagingPart(", body,
                      "先按解析出的几何件编码取行（Spec §C3）")
        self.assertIn("packagingPartAnalyze(", body,
                      "复用既有单件分析入口，不新写第二套（Spec §C3）")


# --------------------------------------------------------------------------- #
# S 组：护栏（现状即绿）
# --------------------------------------------------------------------------- #
class SGuardrails(unittest.TestCase):
    def test_s1_existing_note_stays(self):
        src = APP_JS.read_text(encoding="utf-8", errors="replace")
        self.assertIn("单件工艺 / 成本按业务部件版本另跑；", src,
                      "既有口径说明逐字保留（Spec §C2）")
        self.assertIn("几何没绑定只影响依赖几何的尺寸，不影响有清单尺寸的材料与采购项。", src)

    def test_s2_geometric_panel_untouched(self):
        body = function_body("packagingPartActionsHtml")
        self.assertNotIn("packagingBusinessPartProcess", body,
                         "几何零件面板不许混进业务部件的按钮（Spec §C4）")
        self.assertIn("packagingPartProcess", body, "几何件那边的按钮仍在（Spec §C4）")

    def test_s3_geometric_guard_slice_has_no_forbidden_tokens(self):
        src = APP_JS.read_text(encoding="utf-8", errors="replace")
        start = src.index("function packagingPartActionsHtml")
        end = src.index("function packagingPartSolidReason", start)
        sliced = src[start:end]
        self.assertNotIn("3D 预览", sliced, "既有守卫：这一段不许出现 3D 预览（Spec §C4）")
        self.assertNotIn("packagingPartSolid", sliced, "既有守卫：这一段不许出现 packagingPartSolid")

    def test_s4_node_check_passes(self):
        proc = subprocess.run(["node", "--check", str(APP_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "内联脚本必须仍能过 `node --check`：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
