"""红测：BOM 面板要说得出「部件组行是对照表来的，还是几何模板展开的」
（Spec `packaging-bom-business-rows-account-panel.md`）。

现状缺口（代码级，可指到行）：
  · `packaging_bom._business_rows_scope()`（`:689`）早就逐字给了 `business_rows`
    （`row_total` / `box_part_total` / `optional_part_total` / `needs_input_total` / `keys`，
    判据只认行上的 `source == "packaging_business_parts_authority"` 且 `bom_category` 属部件组），
    `load_bom()` 顶层键就是 `business_rows`（`:1423`）；
  · 而 `requirement-confirm.js` 里 `business_rows` **0 处引用**（`grep -c` 实测）：材料组那本账
    已经有两块落点（映射表 + 清单版本），**部件组这一半一行都没有** —— 对照表来的 28 行与
    几何模板展开的 10 行在表格里同形，用户看不出该不该去导对照表。

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

HEADLINE_READY = "部件组行：来自对照表 28 行（盒型件 26 件 · 选配件 2 件），其中缺输入 3 行。"
HEADLINE_EMPTY = "这一版 BOM 的部件组行没有一行来自对照表（部件组是按几何零件模板展开的）。"
HEADLINE_UNKNOWN = ("后端没给部件组行的来源账（老载荷）：说不清这一版 BOM 的部件组行是对照表还是模板来的。")

RECORD_READY = {"business_rows": {"row_total": 28, "box_part_total": 26, "optional_part_total": 2,
                                  "needs_input_total": 3,
                                  "keys": ["YT-RB-01001-A-01", "YT-RB-01001-A-02"]}}
RECORD_EMPTY = {"business_rows": {"row_total": 0, "box_part_total": 0, "optional_part_total": 0,
                                  "needs_input_total": 0, "keys": []}}

BLOCK_DEPS = ["pbEsc", "pbBusinessRowsAccount"]

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
# A 组：部件组行来源这本账（三态）
# --------------------------------------------------------------------------- #
class ARowsAccount(unittest.TestCase):
    def test_a1_ready_reports_scale_and_missing_input(self):
        out = value("pbBusinessRowsAccount", [RECORD_READY])
        self.assertEqual("ready", out["state"], "有来自对照表的行就是 ready（Spec §C1）")
        self.assertEqual(28, out["total"], "来自对照表几行（Spec §C1）")
        self.assertEqual(26, out["boxParts"], "其中盒型件几件（Spec §C1）")
        self.assertEqual(2, out["optionalParts"], "其中选配件几件（Spec §C1）")
        self.assertEqual(3, out["needsInput"], "其中几行缺输入（Spec §C1）")
        self.assertEqual(HEADLINE_READY, out["headline"], "那句人话逐字（Spec §C1）")

    def test_a2_zero_authority_rows_is_its_own_state(self):
        out = value("pbBusinessRowsAccount", [RECORD_EMPTY])
        self.assertEqual("empty", out["state"], "一行都没来自对照表也是一档（Spec §C1）")
        self.assertEqual(HEADLINE_EMPTY, out["headline"], "模板展开的人话逐字（Spec §C1）")
        self.assertEqual(0, out["total"], "0 行就是 0（Spec §C1）")

    def test_a3_empty_is_not_unknown_and_not_told_the_other_way(self):
        empty = value("pbBusinessRowsAccount", [RECORD_EMPTY])
        legacy = value("pbBusinessRowsAccount", [{}])
        self.assertNotEqual(empty["state"], legacy["state"], "0 行与老载荷不许同形（Spec §C1）")
        self.assertNotEqual(empty["headline"], legacy["headline"], "两种态各有各的一句话（Spec §C1）")
        self.assertNotIn("0 行", empty["headline"].replace("没有一行", ""),
                         "empty 不许说成「没有部件组行」（Spec §C3）")
        self.assertIn("没有一行来自对照表", empty["headline"], "empty 说的是来源（Spec §C1）")

    def test_a4_legacy_payload_is_unknown(self):
        cases = [[{}], [{"stats": {"total": 10}, "items": []}], [{"business_rows": None}],
                 [{"business_rows": []}], [{"business_rows": "x"}], [{"business_rows": 7}],
                 [None], ["x"], [123]]
        for out in run_cases("pbBusinessRowsAccount", cases):
            self.assertEqual("unknown", out["state"], "没有这一块 → 未知档（Spec §C1）")
            self.assertEqual(HEADLINE_UNKNOWN, out["headline"], "未知档的人话逐字（Spec §C1）")
            self.assertEqual(0, out["total"], "未知档给 0，不许编行数（Spec §C1）")

    def test_a5_counts_are_numbers_with_zero_fallback(self):
        out = value("pbBusinessRowsAccount",
                    [{"business_rows": {"row_total": "28", "box_part_total": None,
                                        "optional_part_total": -3, "needs_input_total": "x"}}])
        self.assertEqual(28, out["total"], "数字串照收（Spec §C1）")
        self.assertEqual(0, out["boxParts"], "null 归 0（Spec §C1）")
        self.assertEqual(0, out["optionalParts"], "负数归 0（Spec §C1）")
        self.assertEqual(0, out["needsInput"], "非数归 0（Spec §C1）")
        for key in ("total", "boxParts", "optionalParts", "needsInput"):
            self.assertIsInstance(out[key], (int, float), "键必须存在且是数字：%s（Spec §C1）" % key)
        got = value("pbBusinessRowsAccount",
                    [{"business_rows": {"row_total": "x", "needs_input_total": -1}}])
        self.assertEqual("empty", got["state"], "行数非数按 0 处理（Spec §C1）")
        self.assertEqual(0, got["needsInput"], "负的缺输入归 0（Spec §C1）")

    def test_a6_zero_needs_input_is_said_not_hidden(self):
        out = value("pbBusinessRowsAccount",
                    [{"business_rows": {"row_total": 28, "box_part_total": 26,
                                        "optional_part_total": 2, "needs_input_total": 0}}])
        self.assertEqual(0, out["needsInput"], "缺输入 0 行也要给数（Spec §C1）")
        self.assertIn("缺输入 0 行", out["headline"], "0 行缺输入要说出来（Spec §C1）")

    def test_a7_never_raises_and_state_is_closed_set(self):
        got = run_cases("pbBusinessRowsAccount",
                        [[None], [{}], [[]], ["x"], [{"business_rows": 7}], [RECORD_READY],
                         [{"business_rows": {"row_total": None}}]])
        for out in got:
            self.assertIn(out["state"], ("ready", "empty", "unknown"), "状态闭集（Spec §C1）")
            self.assertIsInstance(out["headline"], str, "任何输入都要有一句话（Spec §C1）")

    def test_a8_keys_exist_in_every_state(self):
        for record in (RECORD_READY, RECORD_EMPTY, {}):
            out = value("pbBusinessRowsAccount", [record])
            for key in ("state", "total", "boxParts", "optionalParts", "needsInput", "headline"):
                self.assertIn(key, out, "返回键固定（Spec §C1）：%s" % key)


# --------------------------------------------------------------------------- #
# B 组：真跑渲染串
# --------------------------------------------------------------------------- #
class BBlock(unittest.TestCase):
    def test_b1_ready_block_carries_the_numbers(self):
        html = value("pbBusinessRowsBlock", [RECORD_READY], also=BLOCK_DEPS)
        self.assertIn('<div class="pb-hint"', html, "沿用 pb-hint 样式（Spec §C1）")
        self.assertIn('data-pb-business-rows-state="ready"', html, "态要能判（Spec §C1）")
        self.assertIn('data-pb-business-rows-total="28"', html, "行数要能判（Spec §C1）")
        self.assertIn('data-pb-business-rows-needs-input="3"', html, "缺输入要能判（Spec §C1）")
        self.assertIn(HEADLINE_READY, html, "人话逐字（Spec §C1）")

    def test_b2_empty_block_is_rendered_too(self):
        html = value("pbBusinessRowsBlock", [RECORD_EMPTY], also=BLOCK_DEPS)
        self.assertIn('data-pb-business-rows-state="empty"', html, "0 行也要渲染（Spec §C1）")
        self.assertIn(HEADLINE_EMPTY, html, "模板展开的人话（Spec §C1）")
        self.assertIn('data-pb-business-rows-total="0"', html, "0 行要能判（Spec §C1）")

    def test_b3_unknown_block_is_rendered_too(self):
        html = value("pbBusinessRowsBlock", [{}], also=BLOCK_DEPS)
        self.assertIn('data-pb-business-rows-state="unknown"', html, "老载荷也要渲染（Spec §C1）")
        self.assertIn(HEADLINE_UNKNOWN, html, "老载荷的人话（Spec §C1）")
        self.assertNotIn(HEADLINE_EMPTY, html, "老载荷不许冒充 0 行（Spec §C1）")

    def test_b4_every_interpolation_is_escaped(self):
        body = function_body("pbBusinessRowsBlock")
        self.assertIn('data-pb-business-rows-state="${pbEsc(', body, "态要过 pbEsc（Spec §C1）")
        self.assertIn('data-pb-business-rows-total="${pbEsc(', body, "行数要过 pbEsc（Spec §C1）")
        self.assertIn('data-pb-business-rows-needs-input="${pbEsc(', body,
                      "缺输入要过 pbEsc（Spec §C1）")
        self.assertIn("${pbEsc(facts.headline)}", body, "那句话也要过 pbEsc（Spec §C1）")

    def test_b5_any_input_yields_a_html_string(self):
        for record in (None, {}, RECORD_READY, RECORD_EMPTY, {"business_rows": []}):
            html = value("pbBusinessRowsBlock", [record], also=BLOCK_DEPS)
            self.assertIsInstance(html, str, "任何输入都要出串（Spec §C1）")
            self.assertIn("pb-hint", html, "任何输入都要出块（Spec §C1）")


# --------------------------------------------------------------------------- #
# C 组：面板接线
# --------------------------------------------------------------------------- #
class CPanelWiring(unittest.TestCase):
    def setUp(self):
        self.body = function_body("pbPanel")

    def test_c1_panel_renders_it_once(self):
        self.assertEqual(1, self.body.count("pbBusinessRowsBlock(record)"),
                         "只准一处调用（Spec §C2）")
        self.assertIn("${pbBusinessRowsBlock(record)}", self.body)

    def test_c2_it_sits_after_the_business_parts_scope(self):
        self.assertIn("pbBusinessRowsBlock(record)", self.body, "要有这一处调用（Spec §C2）")
        self.assertLess(self.body.index("pbBusinessPartsScopeBlock(record)"),
                        self.body.index("pbBusinessRowsBlock(record)"),
                        "业务部件清单那本账之后（Spec §C2）")

    def test_c3_existing_blocks_are_untouched(self):
        for marker in ("零件文档版本", 'data-pb-material-map="1"', "data-pb-business-stale=",
                       "data-pb-material-unresolved=", "data-pb-parts-stale=",
                       "data-pb-parts-unavailable=", "data-pb-pairing-review"):
            self.assertIn(marker, self.body, "既有块一字不动（Spec §C2）：%s" % marker)
        self.assertNotIn("fetch(", self.body, "面板只渲染，不新增请求（Spec §C3）")


# --------------------------------------------------------------------------- #
# D 组：冻结面
# --------------------------------------------------------------------------- #
class DFreeze(unittest.TestCase):
    def test_d1_helpers_hold_no_dom(self):
        for name in ("pbBusinessRowsAccount", "pbBusinessRowsBlock"):
            body = function_body(name)
            for forbidden in ("document", "window.", "sessionStorage", "localStorage",
                              "fetch(", "querySelector"):
                self.assertNotIn(forbidden, body,
                                 "%s() 必须是纯函数（Spec §C1）：不许出现 %s" % (name, forbidden))

    def test_d2_rows_come_only_from_the_backend_scope(self):
        body = function_body("pbBusinessRowsAccount")
        self.assertIn("business_rows", body, "行数只读这一块（Spec §C3）")
        self.assertNotIn("business_material_rows", body, "材料组是另一本账（Spec §C3）")
        self.assertNotIn("items", body, "不许去数 BOM 行（Spec §C3）")
        self.assertNotIn(".source", body, "不许自己按 source 过滤（Spec §C3）")
        self.assertNotIn("stats", body, "不许拿 stats 顶替（Spec §C3）")

    def test_d3_index_html_is_not_touched(self):
        html = (ROOT / "tech_app" / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("data-pb-business-rows-state", html, "不改 index.html（Spec §C3）")

    def test_d4_per_row_marking_is_left_to_the_next_batch(self):
        row = function_body("pbRow")
        self.assertNotIn("pbBusinessRows", row, "逐行标记留给后面一批（Spec §6）")
        self.assertNotIn("data-pb-business-rows", row, "逐行标记留给后面一批（Spec §6）")

    def test_d5_inline_script_still_parses(self):
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "requirement-confirm.js 仍须通过 node --check：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
