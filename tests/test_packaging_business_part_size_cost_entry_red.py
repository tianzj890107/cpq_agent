"""红测：业务部件「按清单尺寸算材料费」的入口必须能点（`## 412` 的界面那一半）。

Spec：`docs/specs/packaging-business-part-size-cost-entry.md`

现状缺口（代码级，可指到行）：
  · `app.js:2571 openPackagingBusinessPart()` 的 `!ok` 分支只渲染一行原因 —— 没有几何但有清单尺寸
    的件（真样本 24 件）在界面上仍然"算不了"；
  · 后端 `## 412` 的 `.../packaging-business-parts/{code}/cost` 前端一处都没调
    （`packagingPartEndpoint()` `:1502` 只指几何零件那条路）。

纪律：`node -e` 抽 app.js 顶层具名函数真跑（纯函数）+ 源码守卫 + `node --check`；
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
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"

CODE = "JWXR21-P01"
SIZE_MISSING_TEXT = ("这一件没有清单尺寸（长度/宽度），先在平面图里确认几何映射，"
                     "或补录清单尺寸后再算。")

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


def target(row):
    got = run_cases("packagingBusinessPartSizeCostTarget", [[row]])
    if got.get("missing"):
        raise AssertionError("app.js 缺少顶层纯函数 packagingBusinessPartSizeCostTarget()（Spec §C1）")
    row_out = got["results"][0]
    if not row_out.get("ok"):
        raise AssertionError("packagingBusinessPartSizeCostTarget() 抛异常：%s" % row_out.get("error"))
    return row_out.get("value") or {}


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def _row(**authority):
    return {"business_part_code": CODE, "name": "礼盒面纸",
            "authority": authority, "geometry_binding": {"component_ids": []}}


# --------------------------------------------------------------------------- #
# A 组：纯函数 `packagingBusinessPartSizeCostTarget()`（Spec §C1）
# --------------------------------------------------------------------------- #
class ASizeCostEntry(unittest.TestCase):
    def test_a1_authority_size_gives_a_clickable_entry(self):
        got = target(_row(length_mm=300.0, width_mm=200.0, material_text="350G玖龙粉灰"))
        self.assertIs(True, got.get("ok"), "有清单尺寸就该给入口（Spec §C1）")
        self.assertEqual(CODE, got.get("part_code"), "编码带回去，点了才知道算哪一件（Spec §C1）")
        self.assertEqual("", got.get("code") or "", "能算就没有原因码")

    def test_a2_missing_size_is_not_clickable(self):
        for authority in ({}, {"length_mm": 300.0}, {"width_mm": 200.0},
                          {"length_mm": 0, "width_mm": 200.0},
                          {"length_mm": "abc", "width_mm": "x"}):
            got = target(_row(**authority))
            self.assertIs(False, got.get("ok"),
                          "没有清单尺寸不许给按钮（Spec §C1）：%r" % (authority,))
            self.assertEqual("authority_size_missing", got.get("code"))
            self.assertEqual(SIZE_MISSING_TEXT, got.get("message"),
                             "文案要说清两条出路（Spec §C1）")

    def test_a3_missing_code_is_its_own_reason(self):
        got = target({"authority": {"length_mm": 1, "width_mm": 1}})
        self.assertIs(False, got.get("ok"))
        self.assertEqual("business_part_missing", got.get("code"))
        self.assertEqual("", got.get("part_code") or "", "没有编码就不带编码")

    def test_a4_odd_payloads_never_throw(self):
        for row in (None, 7, "x", [], {}, {"business_part_code": CODE},
                    {"business_part_code": CODE, "authority": [1, 2]},
                    {"business_part_code": CODE, "authority": {"length_mm": {}, "width_mm": []}}):
            got = target(row)      # 不抛就算过
            self.assertIn(got.get("code") or "",
                          ("", "business_part_missing", "authority_size_missing"),
                          "原因码是闭集（Spec §C1）：%r" % (row,))

    def test_a5_numeric_strings_are_accepted(self):
        got = target(_row(length_mm="300", width_mm="200"))
        self.assertIs(True, got.get("ok"), "清单尺寸是数字串也要认（Spec §C1）")

    def test_a6_pure_function_has_no_dom_or_io(self):
        body = function_body("packagingBusinessPartSizeCostTarget")
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "纯函数体内不许出现 %s（Spec §C1）" % token)


# --------------------------------------------------------------------------- #
# B 组：接线（Spec §C2/§C3）
# --------------------------------------------------------------------------- #
class BWiredIntoThePanel(unittest.TestCase):
    def setUp(self):
        self.src = _source(APP_JS)

    def test_b1_panel_offers_the_entry_only_when_it_can_run(self):
        body = function_body("openPackagingBusinessPart")
        self.assertIn("data-qqBusinessDownstreamReason", body,
                      "既有那行原因必须还在（Spec §C2，`## 409` 的口径不改）")
        self.assertIn("packagingBusinessPartSizeCostTarget(", body,
                      "动作区要按 NEW 判据决定给不给这颗按钮（Spec §C2）")
        self.assertIn("data-qqBusinessSizeCost", body, "给按钮时要有那一行说明（Spec §C2）")
        self.assertIn("packagingBusinessPartCostBySize", body, "按钮 id 固定（Spec §C2）")

    def test_b2_reason_line_still_comes_first(self):
        body = function_body("openPackagingBusinessPart")
        reason_at = body.index("data-qqBusinessDownstreamReason")
        size_at = body.index("data-qqBusinessSizeCost")
        self.assertLess(reason_at, size_at,
                        "原因仍在最前面：新入口是追加，不是替换（Spec §C2）")

    def test_b3_size_cost_opens_the_business_endpoint(self):
        body = function_body("packagingBusinessPartSizeCost")
        self.assertIn("packaging-business-parts/", body,
                      "端点必须是业务部件那条（Spec §C3）")
        self.assertIn("CadInlineAnalysis.open(", body, "复用既有内嵌面板（Spec §C3）")
        self.assertIn('"cost"', body, "只做成本（Spec §C3/§6）")
        self.assertNotIn("selectPackagingPart(", body,
                         "业务编码不在零件文档里，不许走几何那条取行（Spec §C3）")

    def test_b4_click_is_bound_to_the_code(self):
        body = function_body("openPackagingBusinessPart")
        self.assertIn("packagingBusinessPartSizeCost(", body, "点了要真的发起（Spec §C2）")


# --------------------------------------------------------------------------- #
# C 组（护栏）：既有几何入口与后端一个字不改
# --------------------------------------------------------------------------- #
class CGuardrails(unittest.TestCase):
    def test_c1_geometric_entry_is_unchanged(self):
        body = function_body("packagingBusinessPartDownstreamTarget")
        for token in ("authority", "length_mm", "width_mm"):
            self.assertNotIn(token, body,
                             "几何那条判据不许被清单尺寸污染（Spec §C4）")

    def test_c2_geometric_buttons_stay(self):
        body = function_body("openPackagingBusinessPart")
        self.assertIn("packagingBusinessPartProcess", body, "几何那两个按钮仍在（Spec §C4）")
        self.assertIn("packagingBusinessPartCost", body)

    def test_c3_backend_is_untouched(self):
        for path in (MAIN_PY, PARTS_PY):
            self.assertNotIn("packagingBusinessPartSizeCost", _source(path),
                             "本批不改后端（Spec §C4）：%s" % path.name)

    def test_c4_node_check_passes(self):
        proc = subprocess.run(["node", "--check", str(APP_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, "app.js 语法必须通过：%s" % proc.stderr[:400])


if __name__ == "__main__":
    unittest.main()
