"""红测：2.1 的两笔账（几何分量 263 / 业务部件 28）必须同屏对账，几何分量不许自称「零件」。

Spec：`docs/specs/packaging-two-ledgers-reconciliation.md`

现状缺口（2026-09-23 实测，代码级，都可指到行）：
  · 真跑一次（隔离 `DATA_DIR`，酒盒.dwg）两笔账同时存在：几何分量文档 `total=263`、
    业务部件清单 28 件（bound 26 / unbound 2）—— 差 235，是两件事；
  · 页面上**没有一处**把两个分子放在同一行（`grep` 实测 0 处）；
  · `app.js:4666` 顶层计数句 `已显示 ${rows.length} 件，共 ${total} 件（…）` 里的「件」没有
    限定词；`app.js:4673` 截断句同样；
  · 右栏几何分量面板自称「图纸零件」：`index.html:206` 默认文案、`app.js:1242` 兜底、
    `app.js:1336` 标签 —— 点诊断区任一几何分量，标题就是「图纸零件 …」。
  · 结论（写进 Spec §1）：**不需要重建项目**，缺的是对账与措辞。

纪律：`node -e` 抽具名函数体真跑（纯函数）+ 源码/字面量守卫；不起服务、不发 HTTP、
不连 PG / SQLite、不写业务数据、不读真样本。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
INDEX_HTML = ROOT / "tech_app" / "frontend" / "index.html"

PURE_FN = "packagingTwoLedgersLine"
RECON_LINE = "几何区域 263 个 → 业务部件 28 件（已定位 26 件）"
RECON_NO_BUSINESS = "几何区域 263 个 → 还没有业务部件清单"
RECON_NO_GEOMETRY = "几何区域待确认 → 业务部件 28 件（已定位 26 件）"
RECON_BUSINESS_UNREADABLE = "几何区域 263 个 → 业务部件清单读不到"

GEOMETRY_DOC = {"total": 263, "stats": {"part_total": 263}}
BUSINESS_DOC = {"built": True, "business_parts": [{"business_part_code": "QG-01"}],
                "summary": {"stats": {"business_part_total": 28, "bound_total": 26,
                                      "unbound_total": 2}}}

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


def app_source() -> str:
    return APP_JS.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


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


def ledger_line(geometry, business) -> str:
    payload = run_cases(PURE_FN, [[geometry, business]])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2.1）" % PURE_FN)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (PURE_FN, row.get("error")))
    return row.get("value")


# --------------------------------------------------------------------------- #
# L 组：对账纯函数（两笔账的分子与关系）
# --------------------------------------------------------------------------- #
class LTwoLedgersLine(unittest.TestCase):
    def test_l1_both_ledgers_reconcile_in_one_line(self):
        self.assertEqual(RECON_LINE, ledger_line(GEOMETRY_DOC, BUSINESS_DOC),
                         "两笔账必须在同一行里说清（Spec §2.1）")

    def test_l2_geometry_only(self):
        for business in (None, {}, {"business_parts": []}):
            self.assertEqual(RECON_NO_BUSINESS, ledger_line(GEOMETRY_DOC, business),
                             "业务账还没有时要说'还没有业务部件清单'（Spec §2.1）：%r" % (business,))

    def test_l3_geometry_read_problem_says_pending(self):
        geometry = {"read_problem": {"code": "parts_unavailable", "status": 500}}
        self.assertEqual(RECON_NO_GEOMETRY, ledger_line(geometry, BUSINESS_DOC),
                         "几何账读不到时不许把 0 当几何数（Spec §2.1）")

    def test_l4_business_read_problem_is_not_the_missing_sentence(self):
        business = {"read_problem": {"code": "business_parts_unavailable", "status": 500}}
        self.assertEqual(RECON_BUSINESS_UNREADABLE, ledger_line(GEOMETRY_DOC, business),
                         "读不到 ≠ 还没有（Spec §2.1）")

    def test_l5_nothing_readable_returns_empty(self):
        for geometry, business in (({}, None), (None, None), ({}, {}),
                                   ({"total": 0}, {"business_parts": []})):
            self.assertEqual("", ledger_line(geometry, business),
                             "两边都取不到数时不许拼出 0/0（Spec §2.1）：%r" % ((geometry, business),))

    def test_l6_falls_back_to_stats_keys(self):
        self.assertEqual(RECON_LINE,
                         ledger_line({"stats": {"part_total": 263}},
                                     {"stats": {"business_part_total": 28, "bound_total": 26}}),
                         "顶层缺 total 时按 stats.part_total 读（Spec §2.1）")

    def test_l7_junk_inputs_do_not_raise(self):
        self.assertEqual("", ledger_line("263", 42),
                         "非法入参不许抛异常、也不许编数（Spec §2.1）")
        self.assertEqual("", ledger_line(None, "x"))

    def test_l8_pure_function_has_no_dom(self):
        body = function_body(PURE_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.1）" % PURE_FN)
        for token in ("document.", "window.", "fetch(", "localStorage", "sessionStorage"):
            self.assertNotIn(token, body, "对账句必须是纯函数（Spec §2.1）")


# --------------------------------------------------------------------------- #
# W 组：接线（两条渲染路都要有这一行）
# --------------------------------------------------------------------------- #
class WWiring(unittest.TestCase):
    def test_w1_render_tree_renders_the_reconciliation_line(self):
        body = function_body("renderTree")
        self.assertTrue(body, "app.js 缺少 renderTree()")
        self.assertIn(PURE_FN + "(", body,
                      "左栏几何兜底路必须调用对账函数（Spec §2.2）")
        self.assertIn("qq-ledger-reconciliation", body,
                      "对账行必须有自己的 data- 钩子（Spec §2.2）")

    def test_w2_business_tree_renders_the_same_line(self):
        body = function_body("renderPackagingBusinessTree")
        self.assertTrue(body, "app.js 缺少 renderPackagingBusinessTree()")
        self.assertIn(PURE_FN + "(", body,
                      "业务部件那一路也要同一句对账（Spec §2.2）")
        self.assertIn("qq-ledger-reconciliation", body,
                      "对账行必须有自己的 data- 钩子（Spec §2.2）")

    def test_w3_reconciliation_sits_outside_the_geometry_fold(self):
        body = function_body("renderTree")
        at_line = body.find("qq-ledger-reconciliation")
        at_fold = body.find("geometry-diagnostics")
        self.assertGreaterEqual(at_line, 0, "对账行还没渲染（Spec §2.2）")
        self.assertTrue(at_fold < 0 or at_line < at_fold,
                        "对账行必须在几何诊断折叠区**之外**（Spec §2.2）")


# --------------------------------------------------------------------------- #
# S 组：措辞（几何分量不许自称零件）+ 护栏
# --------------------------------------------------------------------------- #
class SWording(unittest.TestCase):
    def test_s1_geometry_count_sentence_carries_the_caliber(self):
        src = app_source()
        self.assertIn("个几何分量，共 ", src,
                      "顶层计数句必须写'个几何分量'（Spec §2.3）")
        self.assertIn("个几何分量未列出（只显示前 ", src,
                      "截断句必须写'个几何分量'（Spec §2.3）")

    def test_s2_viewer_label_keeps_the_business_words_apart(self):
        body = function_body("enterDrawingFlowPanes")
        self.assertTrue(body, "app.js 缺少 enterDrawingFlowPanes()")
        self.assertIn("几何分量（图纸零件）· 选中后看轮廓与证据", body,
                      "右栏标签必须点明这是几何分量（Spec §2.3）")
        self.assertIn("图纸零件", body,
                      "既有契约要求 #viewerPartName 里仍有'图纸零件'字面量（Spec §2.3）")

    def test_s3_geometry_panel_title_is_not_called_a_part(self):
        body = function_body("renderPackagingPartPanel")
        self.assertTrue(body, "app.js 缺少 renderPackagingPartPanel()")
        self.assertIn("几何分量", body,
                      "几何分量面板的标题兜底必须写'几何分量'（Spec §2.3）")
        self.assertNotIn("图纸零件", body,
                         "几何分量面板标题里不许再出现'图纸零件'（Spec §2.3）")
        line = [row for row in INDEX_HTML.read_text(encoding="utf-8").splitlines()
                if 'id="packagingPartTitle"' in row]
        self.assertEqual(1, len(line), "index.html 里必须有 #packagingPartTitle")
        self.assertIn("几何分量", line[0],
                      "#packagingPartTitle 的默认文案必须是'几何分量'（Spec §2.3）")

    def test_s4_business_ledger_wording_is_untouched(self):
        src = app_source()
        self.assertIn("业务部件 ${rows.length} 件（", src,
                      "业务部件树的措辞逐字不变（Spec §2.3/§2.4）")
        self.assertIn("几何诊断 / 映射证据（几何区域 ${total} 个，默认收起）", src,
                      "几何诊断折叠区的 summary 逐字不变（Spec §2.4）")

    def test_s5_existing_geometry_row_naming_is_untouched(self):
        src = app_source()
        self.assertIn("图纸零件", src,
                      "诊断行名'图纸零件 PNN'来自解析侧，本批不改（Spec §2.4）")
        self.assertIn("qqGeometryDiagnostics", src,
                      "几何诊断区仍是同一个钩子（Spec §2.4）")

    def test_s6_no_frontend_geometry_solving_or_new_requests(self):
        body = function_body(PURE_FN)
        for token in ("Math.hypot", "bbox", "polygon"):
            self.assertNotIn(token, body,
                             "对账句只搬数字，不做几何求解（Spec §2.4）：%s" % token)
        src = app_source()
        self.assertNotRegex(src, r"packaging-two-ledgers",
                            "不许新增第二套读接口（Spec §2.4）")


if __name__ == "__main__":
    unittest.main()
