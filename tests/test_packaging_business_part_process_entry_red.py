"""红测：业务部件「按权威清单排工序」的入口必须能点（`## 414` 的界面那一半）。

Spec：`docs/specs/packaging-business-part-process-entry.md`

现状缺口（代码级，可指到行）：
  · `app.js:2624 openPackagingBusinessPart()` 的 `!ok` 分支给的是 `## 409` 那行原因 +
    `## 413` 那颗「成本测算（按权威尺寸）」—— **工序那一半一个入口都没有**；
  · 后端 `## 414` 的 `.../requirement/packaging-business-parts/{code}/process` 前端一处都没调。

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
COST_ENTRY_RED = ROOT / "tests" / "test_packaging_business_part_cost_by_authority_size_red.py"

CODE = "JWXR21-P01"
SIZE_MISSING_TEXT = ("这一件没有权威尺寸（长度/宽度），先在平面图里确认几何映射，"
                     "或补录权威尺寸后再排工艺。")
MATERIAL_MISSING_TEXT = "这一件在权威清单里没有材料原文，补上材料后再排工艺。"
REASONS = ("", "business_part_missing", "authority_size_missing", "material_missing")

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
    got = run_cases("packagingBusinessPartProcessTarget", [[row]])
    if got.get("missing"):
        raise AssertionError("app.js 缺少顶层纯函数 packagingBusinessPartProcessTarget()（Spec §C1）")
    row_out = got["results"][0]
    if not row_out.get("ok"):
        raise AssertionError("packagingBusinessPartProcessTarget() 抛异常：%s" % row_out.get("error"))
    return row_out.get("value") or {}


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def _row(**authority):
    return {"business_part_code": CODE, "name": "礼盒面纸",
            "authority": authority, "geometry_binding": {"component_ids": []}}


# --------------------------------------------------------------------------- #
# A 组：纯函数 `packagingBusinessPartProcessTarget()`（Spec §C1）
# --------------------------------------------------------------------------- #
class AProcessEntry(unittest.TestCase):
    def test_a1_authority_route_gives_a_clickable_entry(self):
        got = target(_row(length_mm=300.0, width_mm=200.0, material_text="350G玖龙粉灰"))
        self.assertIs(True, got.get("ok"), "权威尺寸与材料齐了就该给入口（Spec §C1）")
        self.assertEqual(CODE, got.get("part_code"), "编码带回去，点了才知道排哪一件（Spec §C1）")
        self.assertEqual("", got.get("code") or "", "能排就没有原因码")

    def test_a2_missing_size_is_not_clickable(self):
        for authority in ({}, {"length_mm": 300.0}, {"width_mm": 200.0},
                          {"length_mm": 0, "width_mm": 200.0},
                          {"length_mm": "abc", "width_mm": "x"}):
            row = _row(material_text="350G玖龙粉灰", **authority)
            got = target(row)
            self.assertIs(False, got.get("ok"),
                          "没有权威尺寸不许给按钮（Spec §C1）：%r" % (authority,))
            self.assertEqual("authority_size_missing", got.get("code"))
            self.assertEqual(SIZE_MISSING_TEXT, got.get("message"),
                             "文案逐字，且说清两条出路（Spec §C1）")

    def test_a3_missing_material_text_is_its_own_reason(self):
        for authority in ({}, {"material_text": "  "}):
            got = target(_row(length_mm=300.0, width_mm=200.0, **authority))
            self.assertIs(False, got.get("ok"), "没有材料原文不许给按钮（Spec §C1）")
            self.assertEqual("material_missing", got.get("code"))
            self.assertEqual(MATERIAL_MISSING_TEXT, got.get("message"))

    def test_a4_missing_code_is_its_own_reason(self):
        got = target({"authority": {"length_mm": 1, "width_mm": 1, "material_text": "x"}})
        self.assertIs(False, got.get("ok"))
        self.assertEqual("business_part_missing", got.get("code"))
        self.assertEqual("", got.get("part_code") or "", "没有编码就不带编码")

    def test_a5_row_material_is_the_documented_fallback(self):
        got = target({"business_part_code": CODE, "material": "350G灰板",
                      "authority": {"length_mm": 300.0, "width_mm": 200.0}})
        self.assertIs(True, got.get("ok"), "行上 material 那条兜底与后端同口径（Spec §C1）")

    def test_a6_odd_payloads_never_throw(self):
        for row in (None, 7, "x", [], {}, {"business_part_code": CODE},
                    {"business_part_code": CODE, "authority": [1, 2]},
                    {"business_part_code": CODE, "material": {},
                     "authority": {"length_mm": {}, "width_mm": []}}):
            got = target(row)      # 不抛就算过
            self.assertIn(got.get("code") or "", REASONS,
                          "原因码是闭集（Spec §C1）：%r" % (row,))

    def test_a7_numeric_strings_are_accepted(self):
        got = target(_row(length_mm="300", width_mm="200", material_text="350G玖龙粉灰"))
        self.assertIs(True, got.get("ok"), "权威尺寸是数字串也要认（Spec §C1）")

    def test_a8_pure_function_has_no_dom_or_io(self):
        body = function_body("packagingBusinessPartProcessTarget")
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
                      "`## 413` 那颗成本按钮的判据必须还在（Spec §C2）")
        self.assertIn("packagingBusinessPartProcessTarget(", body,
                      "动作区要按新判据决定给不给这颗按钮（Spec §C2）")
        self.assertIn("data-qqBusinessProcess", body, "给按钮时要有那一行说明（Spec §C2）")
        self.assertIn("packagingBusinessPartProcessByAuthority", body, "按钮 id 固定（Spec §C2）")
        self.assertIn("processTarget.ok", body, "不 ok 时什么都不加（Spec §C2）")

    def test_b2_reason_and_size_cost_still_come_first(self):
        body = function_body("openPackagingBusinessPart")
        reason_at = body.index("data-qqBusinessDownstreamReason")
        size_at = body.index("data-qqBusinessSizeCost")
        process_at = body.index("data-qqBusinessProcess")
        self.assertLess(reason_at, size_at,
                        "原因仍在最前面：新入口是追加，不是替换（Spec §C2）")
        self.assertLess(size_at, process_at,
                        "`## 413` 那条与那颗按钮逐字保留、相对顺序不变（Spec §C2）")

    def test_b3_process_entry_opens_the_business_endpoint(self):
        body = function_body("packagingBusinessPartProcessByAuthority")
        self.assertIn("packaging-business-parts/", body,
                      "端点必须是业务部件那条（Spec §C3）")
        self.assertIn("CadInlineAnalysis.open(", body, "复用既有内嵌面板（Spec §C3）")
        self.assertIn('"process"', body, "只做工艺（Spec §C3/§6）")
        for token in ("selectPackagingPart(", "packagingBusinessPartAnalyze("):
            self.assertNotIn(token, body,
                             "业务编码不在零件文档里，不许走几何那条取行（Spec §C3）：%s" % token)
        self.assertIn("exitBoardViewHost()", body, "与 `## 413` 逐字同形（Spec §C3）")
        self.assertIn("setRightPane(", body, "落在右栏内嵌面板里（Spec §C3）")
        self.assertIn("no-analysis-host", body, "没有 host 要给稳定原因（Spec §C3）")
        self.assertIn("no-part-code", body, "没有编码要给稳定原因（Spec §C3）")

    def test_b4_click_is_bound_to_the_code(self):
        body = function_body("openPackagingBusinessPart")
        self.assertIn("packagingBusinessPartProcessByAuthority(", body, "点了要真的发起（Spec §C2）")

    def test_b5_the_entry_does_not_compute_anything(self):
        body = function_body("packagingBusinessPartProcessByAuthority")
        for token in ("length_mm", "width_mm", "thickness", "compute", "工时"):
            self.assertNotIn(token, body, "前端不算工序 / 不碰尺寸（Spec §C4）：%s" % token)

    def test_b6_node_check_passes(self):
        proc = subprocess.run(["node", "--check", str(APP_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, "app.js 语法必须通过：%s" % proc.stderr[:400])


# --------------------------------------------------------------------------- #
# C 组（护栏）：既有几何入口、成本入口与后端一个字不改
# --------------------------------------------------------------------------- #
class CGuardrails(unittest.TestCase):
    def test_c1_geometric_entry_is_unchanged(self):
        body = function_body("packagingBusinessPartDownstreamTarget")
        for token in ("authority", "length_mm", "width_mm"):
            self.assertNotIn(token, body,
                             "几何那条判据不许被权威尺寸污染（Spec §C4）")

    def test_c2_geometric_and_cost_buttons_stay(self):
        body = function_body("openPackagingBusinessPart")
        self.assertIn("packagingBusinessPartProcess", body, "几何那两个按钮仍在（Spec §C4）")
        self.assertIn("packagingBusinessPartCost", body)
        self.assertIn("packagingBusinessPartCostBySize", body, "`## 413` 那颗按钮仍在（Spec §C4）")

    def test_c3_backend_is_untouched(self):
        for path in (MAIN_PY, PARTS_PY):
            self.assertNotIn("packagingBusinessPartProcessByAuthority", _source(path),
                             "本批不改后端（Spec §C4）：%s" % path.name)

    def test_c4_the_412_freeze_is_repointed_not_loosened(self):
        # `## 412` 的 E4 原本锁"前端只多出那一条声明的业务件成本请求"；本批按 Spec §C4 把它
        # **重指**为"成本一条 + 工艺一条"（重指≠放宽：多出来的必须逐个点名）。
        # `## 480` 按 Spec `packaging-2-1-result-parts-and-shape-only-pane.md` §2.4b C6 再**重指**
        # 一次（计数 4 → 5）：多出来的那一条是按图纸补推导（`…/packaging-business-parts/derive`）。
        # 重指不等于放宽：计数仍精确相等，多出来的一条在这里点名。
        src = _source(APP_JS)
        self.assertEqual(5, src.count("packaging-business-parts/"),
                         "前端只许多出那两条声明的业务件请求（工艺 + 补推导；Spec §C4 / 2.1-result §2.4b）")
        red = _source(COST_ENTRY_RED)
        self.assertIn("重指", red, "`## 412` 的 E4 必须写明重指而不是偷偷放宽（Spec §C4）")
        self.assertIn("4", red.split("test_e4_no_frontend_change")[1][:900],
                      "重指后的计数必须落在 E4 里（Spec §C4）")

    def test_c5_cost_entry_is_untouched(self):
        body = function_body("packagingBusinessPartSizeCost")
        self.assertIn('"cost"', body, "`## 413` 那条一字不动（Spec §C4）")
        self.assertNotIn('"process"', body, "成本入口不许顺手改成工艺（Spec §C4）")

    def test_c6_no_second_business_process_endpoint_in_the_frontend(self):
        src = _source(APP_JS)
        for token in ('id="packagingBusinessPartProcessByAuthority"',
                      '$("packagingBusinessPartProcessByAuthority")'):
            self.assertEqual(1, src.count(token), "那一条只许有一处（Spec §C2）：%s" % token)


if __name__ == "__main__":
    unittest.main()
