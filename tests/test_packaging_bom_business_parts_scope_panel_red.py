"""红测：BOM 面板要说得出「这份 BOM 照哪一版业务部件清单配的」
（Spec `packaging-bom-business-parts-scope-panel.md`）。

现状缺口（代码级，可指到行）：
  · `packaging_bom.load_bom()`（`:1412-1420`）早就逐字给了 `business_parts_id` 与
    `business_parts`（`available` / `business_part_total` / `bound_total` / `unbound_total` /
    `business_parts_hash` / `gap`），`_business_parts_scope()`（`:1647`）还会把"读不到"
    （`business_parts_document_unavailable`）与"还没导入"（`business_parts_missing`）分成两种 gap；
  · 而 `requirement-confirm.js` 里 `business_parts_id` / `business_parts_hash` /
    `business_part_total` **全部 0 处引用**（`grep -c` 实测）：BOM 面板 head 只写了
    「零件文档版本」这一把尺子，另一把尺子（业务部件清单哪一版、共几件、几件绑了几何）没有落点。

纪律：`node -e` 抽闭包内具名函数体执行（纯函数 + 真跑渲染串）+ 源码守卫 + `node --check`；
不起服务、不发 HTTP、不连 PG / 34、不写业务数据。禁止为了让红测转绿而修改本文件。
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

CONFIRM_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"

BIZ_ID = "biz:authority0001"
BIZ_HASH = "d3f1c2a4b5e6"
READ_FAIL_MESSAGE = ("业务部件清单读不到（OperationalError）：这次判不了 BOM 是按哪一版业务部件"
                     "算的，别把这次当成「没有业务部件」")
NOT_IMPORTED_MESSAGE = "项目里还没有业务部件清单：已识别几何区域 12 个，尚未形成业务部件清单"

HEADLINE_READY = ("这版 BOM 照的业务部件清单：%s · 共 28 件（已绑几何 4 件 · 未绑 24 件）"
                  "· 版本 %s。" % (BIZ_ID, BIZ_HASH))
HEADLINE_EMPTY = "业务部件清单读到了，但一件都没有（0 件）：这版 BOM 只按几何零件配，清单要重导。"
HEADLINE_UNKNOWN = "后端没给业务部件清单这一块（老载荷）：说不清这版 BOM 照哪一版业务部件算的。"
HEADLINE_FALLBACK = ("业务部件清单读不到：这版 BOM 只按几何零件配，"
                     "别把这次当成「没有业务部件」。")

RECORD_READY = {"business_parts_id": BIZ_ID,
                "business_parts": {"available": True, "business_part_total": 28,
                                   "bound_total": 4, "unbound_total": 24,
                                   "business_parts_hash": BIZ_HASH, "gap": {}}}
RECORD_READ_FAIL = {"business_parts_id": "",
                    "business_parts": {"available": False, "business_part_total": 0,
                                       "bound_total": 0, "unbound_total": 0,
                                       "business_parts_hash": "",
                                       "gap": {"code": "business_parts_document_unavailable",
                                               "reason": "OperationalError",
                                               "message": READ_FAIL_MESSAGE}}}
RECORD_NOT_IMPORTED = {"business_parts_id": "",
                       "business_parts": {"available": False, "business_part_total": 0,
                                          "bound_total": 0, "unbound_total": 0,
                                          "business_parts_hash": "",
                                          "gap": {"code": "business_parts_missing",
                                                  "message": NOT_IMPORTED_MESSAGE}}}
RECORD_EMPTY = {"business_parts_id": BIZ_ID,
                "business_parts": {"available": True, "business_part_total": 0,
                                   "bound_total": 0, "unbound_total": 0,
                                   "business_parts_hash": BIZ_HASH, "gap": {}}}

BLOCK_DEPS = ["pbEsc", "pbBusinessPartsScope"]

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
const extras = JSON.parse(process.argv[5] || "[]");
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
if (mode === "body") { console.log(JSON.stringify({ missing: false, body: fn })); process.exit(0); }
const cases = JSON.parse(mode);
/* 被抽的函数若复用了同文件的另一个纯函数，一起 eval（Spec §C1：口径只在源码里出现一次）。 */
eval(extras.map(extract).filter(Boolean).concat([fn]).join("\n"));
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def run_cases(name: str, cases, also=()):
    """`cases` 是**实参表**（每项一次调用的实参列表）；`also` 是被抽函数依赖的同文件纯函数。"""
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(CONFIRM_JS), name,
                           json.dumps(list(cases)), json.dumps(list(also))],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("requirement-confirm.js 缺少纯函数 %s()（Spec §C1）" % name)
    for index, item in enumerate(payload["results"]):
        if not item.get("ok"):
            raise AssertionError("%s() 第 %d 个入参抛异常：%s（Spec §C1）"
                                 % (name, index, item.get("error")))
    return [item["value"] for item in payload["results"]]


def value(name: str, args, also=()):
    return run_cases(name, [args], also=also)[0]


def function_body(name: str) -> str:
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(CONFIRM_JS), name, "body"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("requirement-confirm.js 缺少具名函数 %s()（Spec §C1）" % name)
    return payload["body"]


# --------------------------------------------------------------------------- #
# A 组：这一版业务部件清单的账（四态）
# --------------------------------------------------------------------------- #
class AScope(unittest.TestCase):
    def test_a1_ready_reports_version_scale_and_binding(self):
        out = value("pbBusinessPartsScope", [RECORD_READY])
        self.assertEqual("ready", out["state"], "有清单就是 ready（Spec §C1）")
        self.assertEqual(BIZ_ID, out["id"], "清单 id 逐字（Spec §C1）")
        self.assertEqual(BIZ_HASH, out["hash"], "清单版本逐字（Spec §C1）")
        self.assertEqual(28, out["total"], "清单几件（Spec §C1）")
        self.assertEqual(4, out["bound"], "几件绑了几何（Spec §C1）")
        self.assertEqual(24, out["unbound"], "几件没绑（Spec §C1）")
        self.assertEqual(HEADLINE_READY, out["headline"], "那句人话逐字（Spec §C1）")

    def test_a2_read_failure_relays_the_backend_message(self):
        out = value("pbBusinessPartsScope", [RECORD_READ_FAIL])
        self.assertEqual("unavailable", out["state"], "读不到就是 unavailable（Spec §C1）")
        self.assertEqual("business_parts_document_unavailable", out["gapCode"],
                         "gap 码逐字（Spec §C1）")
        self.assertEqual(READ_FAIL_MESSAGE, out["headline"],
                         "后端 message 逐字转达，不许换个说法（Spec §C1）")

    def test_a3_not_imported_is_a_different_sentence(self):
        out = value("pbBusinessPartsScope", [RECORD_NOT_IMPORTED])
        self.assertEqual("unavailable", out["state"], "还没导入也归 unavailable（Spec §C1）")
        self.assertEqual("business_parts_missing", out["gapCode"], "gap 码逐字（Spec §C1）")
        self.assertEqual(NOT_IMPORTED_MESSAGE, out["headline"],
                         "两种态各有各的画面（Spec §C1）")
        self.assertNotEqual(value("pbBusinessPartsScope", [RECORD_READ_FAIL])["headline"],
                            out["headline"], "读不到与还没导入不许同一句（Spec §C1）")

    def test_a4_available_with_zero_parts_is_its_own_state(self):
        out = value("pbBusinessPartsScope", [RECORD_EMPTY])
        self.assertEqual("empty", out["state"], "清单读到了但 0 件（Spec §C1）")
        self.assertEqual(HEADLINE_EMPTY, out["headline"], "0 件的人话逐字（Spec §C1）")
        self.assertEqual(0, out["total"], "0 件就是 0（Spec §C1）")

    def test_a5_legacy_payload_is_not_read_as_empty(self):
        cases = [[{}], [{"stats": {"total": 12}, "items": []}], [{"business_parts": None}],
                 [{"business_parts": []}], [{"business_parts": "x"}], [None], ["x"], [123]]
        for out in run_cases("pbBusinessPartsScope", cases):
            self.assertEqual("unknown", out["state"], "没有这一块 → 未知档（Spec §C1）")
            self.assertEqual(HEADLINE_UNKNOWN, out["headline"], "未知档的人话逐字（Spec §C1）")
            self.assertEqual(0, out["total"], "未知档给 0，不许拿几何件数顶（Spec §C1）")

    def test_a6_counts_are_numbers_with_zero_fallback(self):
        out = value("pbBusinessPartsScope",
                    [{"business_parts": {"available": True, "business_part_total": "28",
                                         "bound_total": None, "unbound_total": -3}}])
        self.assertEqual(28, out["total"], "数字串照收（Spec §C1）")
        self.assertEqual(0, out["bound"], "null 归 0（Spec §C1）")
        self.assertEqual(0, out["unbound"], "负数归 0（Spec §C1）")
        self.assertIsInstance(out["total"], (int, float), "键必须存在且是数字（Spec §C1）")
        got = value("pbBusinessPartsScope",
                    [{"business_parts": {"available": True, "business_part_total": "x"}}])
        self.assertEqual("empty", got["state"], "非数按 0 件处理（Spec §C1）")

    def test_a7_missing_message_falls_back(self):
        out = value("pbBusinessPartsScope", [{"business_parts": {"available": False}}])
        self.assertEqual("unavailable", out["state"], "没有 message 也仍是读不到这一档（Spec §C1）")
        self.assertEqual(HEADLINE_FALLBACK, out["headline"], "兜底句逐字（Spec §C1）")
        self.assertEqual("", out["gapCode"], "没有码就给空串，不许编（Spec §C1）")

    def test_a8_never_raises(self):
        got = run_cases("pbBusinessPartsScope",
                        [[None], [{}], [[]], ["x"], [{"business_parts": 7}]])
        for out in got:
            self.assertIn(out["state"], ("ready", "empty", "unavailable", "unknown"),
                          "状态闭集（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：这一段渲染（真跑字符串）
# --------------------------------------------------------------------------- #
class BBlock(unittest.TestCase):
    def test_b1_ready_block_carries_state_id_and_total(self):
        html = value("pbBusinessPartsScopeBlock", [RECORD_READY], also=BLOCK_DEPS)
        self.assertIn('data-pb-business-parts-state="ready"', html, "状态要能判（Spec §C1）")
        self.assertIn('data-pb-business-parts-id="%s"' % BIZ_ID, html, "清单 id 要能判（Spec §C1）")
        self.assertIn('data-pb-business-parts-total="28"', html, "件数要能判（Spec §C1）")
        self.assertIn(HEADLINE_READY, html, "人话逐字（Spec §C1）")
        self.assertIn("（已绑几何 4 件 · 未绑 24 件）", html, "绑定情况逐字（Spec §C1）")

    def test_b2_unavailable_block_carries_the_gap_code(self):
        html = value("pbBusinessPartsScopeBlock", [RECORD_READ_FAIL], also=BLOCK_DEPS)
        self.assertIn('data-pb-business-parts-state="unavailable"', html, "读不到要能判（Spec §C1）")
        self.assertIn('data-pb-business-parts-gap="business_parts_document_unavailable"', html,
                      "gap 码要能判（Spec §C1）")
        self.assertIn(READ_FAIL_MESSAGE, html, "后端 message 逐字（Spec §C1）")

    def test_b3_unknown_and_empty_are_rendered_too(self):
        html = value("pbBusinessPartsScopeBlock", [RECORD_EMPTY], also=BLOCK_DEPS)
        self.assertIn('data-pb-business-parts-state="empty"', html, "0 件也要说（Spec §C1）")
        self.assertIn(HEADLINE_EMPTY, html, "0 件的人话（Spec §C1）")
        legacy = value("pbBusinessPartsScopeBlock", [{}], also=BLOCK_DEPS)
        self.assertIn('data-pb-business-parts-state="unknown"', legacy, "老载荷也要说（Spec §C1）")
        self.assertIn(HEADLINE_UNKNOWN, legacy, "老载荷的人话（Spec §C1）")

    def test_b4_values_are_escaped(self):
        html = value("pbBusinessPartsScopeBlock",
                     [{"business_parts_id": "<b>",
                       "business_parts": {"available": False,
                                          "gap": {"code": '"x"', "message": "<i>看不到</i>"}}}],
                     also=BLOCK_DEPS)
        self.assertNotIn("<b>", html, "插值必须转义（Spec §C1）")
        self.assertIn("&lt;b&gt;", html, "转义后逐字（Spec §C1）")
        self.assertIn("&lt;i&gt;看不到&lt;/i&gt;", html, "message 也要转义（Spec §C1）")
        self.assertIn("&quot;x&quot;", html, "gap 码也要转义（Spec §C1）")


# --------------------------------------------------------------------------- #
# C 组：面板接线
# --------------------------------------------------------------------------- #
class CPanelWiring(unittest.TestCase):
    def setUp(self):
        self.body = function_body("pbPanel")

    def test_c1_panel_renders_it_once(self):
        self.assertEqual(1, self.body.count("pbBusinessPartsScopeBlock(record)"),
                         "只准一处调用（Spec §C2）")
        self.assertIn("${pbBusinessPartsScopeBlock(record)}", self.body)

    def test_c2_it_sits_after_the_material_map_account(self):
        at = self.body.index("pbBusinessPartsScopeBlock(record)")
        self.assertLess(self.body.index("pbMaterialMapAccountBlock(record.business_material_rows)"), at,
                        "材料码映射表那条账之后（Spec §C2）")

    def test_c3_existing_blocks_are_untouched(self):
        for marker in ("零件文档版本", "data-pb-material-map=\"1\"", "data-pb-business-stale=",
                       "data-pb-material-unresolved=", "data-pb-parts-stale=",
                       "data-pb-parts-unavailable="):
            self.assertIn(marker, self.body, "既有块一字不动（Spec §C2）：%s" % marker)
        self.assertNotIn("fetch(", self.body, "面板只渲染，不新增请求（Spec §C3）")


# --------------------------------------------------------------------------- #
# D 组：冻结面
# --------------------------------------------------------------------------- #
class DFreeze(unittest.TestCase):
    def test_d1_helpers_hold_no_dom(self):
        for name in ("pbBusinessPartsScope", "pbBusinessPartsScopeBlock"):
            body = function_body(name)
            for forbidden in ("document", "window.", "sessionStorage", "localStorage",
                              "fetch(", "querySelector"):
                self.assertNotIn(forbidden, body,
                                 "%s() 必须是纯函数（Spec §C1）：不许出现 %s" % (name, forbidden))

    def test_d2_counts_never_come_from_geometry(self):
        body = function_body("pbBusinessPartsScope")
        self.assertIn("business_parts", body, "件数只读这一块（Spec §C3）")
        self.assertNotIn("stats", body, "不许拿几何件数冒充业务件数（Spec §C3）")
        self.assertNotIn("items", body, "不许去数 BOM 行（Spec §C3）")
        self.assertNotIn("stale", body, "可用性不许按漂移那一栏反推（Spec §C3）")

    def test_d3_index_html_is_not_touched(self):
        html = (ROOT / "tech_app" / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("data-pb-business-parts-state", html, "不改 index.html（Spec §C3）")

    def test_d4_inline_script_still_parses(self):
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "requirement-confirm.js 仍须通过 node --check：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
