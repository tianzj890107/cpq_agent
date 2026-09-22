"""红测：BOM 面板上「材料码映射表」的账要说全——空表不许静默，来源与指纹要看得见
（Spec `packaging-material-map-account-panel.md`）。

现状缺口（代码级，可指到行）：
  · `packaging_bom._business_material_scope()`（`:635`）回了 `map_hit_total`，但**没有**表本身的
    条数 —— "表是空的"与"表里没有这一条"在读接口上同形；
  · `requirement-confirm.js` 的 `pbMaterialMapNote()`（`:391`）只在"读不到"或"命中 > 0"时才有话说，
    其余情况回空串 → **空表在页面上一字不说**；
  · `map_source` / `map_fingerprint` / `reason_counts` 在前端 **0 处引用**（`grep -c` 实测）：
    这一版 BOM 用的是哪一份映射表（仓库内置 / env 覆盖）说不出来，逐档条数也看不见。

纪律：后端跑**真函数**（合成材料清单 + 真映射条目）；前端用 `node -e` 抽闭包内具名函数体真跑
（纯函数 + 渲染串）+ 源码守卫 + `node --check`；不连 PG / 34、不写业务数据、不发 HTTP。
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

from tech_app.backend.services import packaging_bom as bom                  # noqa: E402

CONFIRM_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"

MATERIALS = [
    {"material_code": "MAT-PAPER-350", "name": "350G玖龙粉灰 面纸"},
    {"material_code": "MAT-PAPER-350B", "name": "350G玖龙粉灰 底纸"},
    {"material_code": "MAT-EVA", "name": "EVA 内托"},
]

HEADLINE_UNAVAILABLE = "材料原文映射表读不到：这一版 BOM 的材料码只按既有分词规则解析。"
HEADLINE_EMPTY = ("材料原文映射表读得到，但一条映射都没有（0 条）：客户原文只能按既有分词规则解析，"
                  "解不出来的行要往表里补。")
HEADLINE_UNKNOWN = "后端没给材料码映射表的状态（老载荷），这一版说不清用了哪份映射。"

SCOPE_READY = {"row_total": 4, "resolved_total": 2, "unresolved_total": 2,
               "keys": ["350G玖龙粉灰", "EVA 内托", "磁铁", "未收录原文"],
               "reason_counts": {"map_hit": 1, "legacy_hit": 1, "map_key_missing": 2},
               "map_hit_total": 1, "map_source": "default", "map_fingerprint": "9f2c0a1b4d5e",
               "map_entry_total": 3, "map_unavailable": {}}
SCOPE_EMPTY = {"row_total": 4, "resolved_total": 1, "unresolved_total": 3,
               "keys": ["a"], "reason_counts": {"legacy_hit": 1, "map_key_missing": 3},
               "map_hit_total": 0, "map_source": "override",
               "map_fingerprint": "0011223344ff", "map_entry_total": 0, "map_unavailable": {}}
SCOPE_UNAVAILABLE = {"row_total": 4, "resolved_total": 0, "unresolved_total": 4, "keys": [],
                     "reason_counts": {"map_unknown": 4}, "map_hit_total": 0,
                     "map_source": "", "map_fingerprint": "", "map_entry_total": 0,
                     "map_unavailable": {"code": "PACKAGING_MATERIAL_CODE_MAP_INVALID",
                                         "message": "材料原文映射表缺失或无法读取，请联系系统管理员"}}
#: 老载荷：这一批之前 `map_entry_total` 这个键根本不存在。
SCOPE_LEGACY = {"row_total": 2, "resolved_total": 1, "unresolved_total": 1,
                "keys": ["x"], "reason_counts": {"map_hit": 1}, "map_hit_total": 1,
                "map_source": "default", "map_fingerprint": "abc123abc123", "map_unavailable": {}}

BLOCK_DEPS = ["pbEsc", "pbMaterialMapState", "pbMaterialMapBreakdown"]

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
/* 被抽的函数若复用了同文件的另一个纯函数，一起 eval（Spec §C2：口径只在源码里出现一次）。 */
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
        raise AssertionError("requirement-confirm.js 缺少纯函数 %s()（Spec §C2）" % name)
    for index, item in enumerate(payload["results"]):
        if not item.get("ok"):
            raise AssertionError("%s() 第 %d 个入参抛异常：%s（Spec §C2）"
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
        raise AssertionError("requirement-confirm.js 缺少具名函数 %s()（Spec §C2）" % name)
    return payload["body"]


def entry(text, code, note=""):
    return {"text": text, "material_code": code, "note": note}


def business_doc(texts):
    return {"business_parts": [{"business_part_code": "PART-P%02d" % index,
                                "name": "件%d" % index,
                                "authority": {"material_text": text}}
                               for index, text in enumerate(texts, start=1)]}


def rows(texts=("350G玖龙粉灰", "EVA 内托", "磁铁", "未收录原文"), *, entries=()):
    return bom.business_material_rows(business_doc(list(texts)), materials=MATERIALS,
                                      map_entries=[entry(t, c) for t, c in entries])


# --------------------------------------------------------------------------- #
# A 组：后端那一处加法（真函数）
# --------------------------------------------------------------------------- #
class ABackendEntryTotal(unittest.TestCase):
    def test_a1_entry_total_counts_the_map(self):
        scope = bom._business_material_scope(
            rows(entries=(("350G玖龙粉灰", "MAT-PAPER-350"),)),
            map_entries=[entry("350G玖龙粉灰", "MAT-PAPER-350")])
        self.assertIn("map_entry_total", scope, "表本身多少条要进账（Spec §C1）")
        self.assertEqual(1, scope["map_entry_total"], "映射表里几条就是几条（Spec §C1）")
        self.assertIsInstance(scope["map_entry_total"], int, "值必须是整数（Spec §C1）")
        for key in ("row_total", "resolved_total", "unresolved_total", "keys", "reason_counts",
                    "map_hit_total", "map_source", "map_fingerprint", "map_unavailable"):
            self.assertIn(key, scope, "既有键一个都不许少（Spec §C1）：%s" % key)

    def test_a2_empty_map_is_zero_not_missing(self):
        scope = bom._business_material_scope(rows(), map_entries=[])
        self.assertEqual(0, scope["map_entry_total"], "空表 = 0 条（Spec §C1）")
        self.assertEqual({}, scope["map_unavailable"], "空表不是「读不到」（Spec §C1）")

    def test_a3_unavailable_map_has_no_count(self):
        scope = bom._business_material_scope(
            rows(), map_entries=[entry("350G玖龙粉灰", "MAT-PAPER-350")], map_available=False,
            map_unavailable={"code": "PACKAGING_MATERIAL_CODE_MAP_INVALID", "message": "读不到"})
        self.assertEqual(0, scope["map_entry_total"], "读不到就没有条数可说（Spec §C1）")
        self.assertTrue(scope["map_unavailable"], "读不到仍要如实披露（Spec §C1）")
        self.assertEqual(4, scope["reason_counts"].get("map_unknown", 0),
                         "读不到时逐行归 map_unknown（Spec §C1）")

    def test_a4_key_always_present(self):
        for scope in (bom._business_material_scope(rows()), bom._business_material_scope([]),
                      bom._business_material_scope(rows(), map_entries=None)):
            self.assertIn("map_entry_total", scope, "键必须永远存在（Spec §C1）")
            self.assertIsInstance(scope["map_entry_total"], int, "值必须永远是整数（Spec §C1）")

    def test_a5_existing_account_is_untouched(self):
        scope = bom._business_material_scope(
            rows(entries=(("350G玖龙粉灰", "MAT-PAPER-350"),)),
            map_entries=[entry("350G玖龙粉灰", "MAT-PAPER-350")],
            map_source="default", map_fingerprint="9f2c0a1b4d5e")
        self.assertEqual(4, scope["row_total"], "既有四个数一字不动（Spec §C1）")
        self.assertEqual(2, scope["resolved_total"])
        self.assertEqual(2, scope["unresolved_total"])
        self.assertEqual(1, scope["map_hit_total"], "映射命中几条仍要单独说（Spec §C1）")
        self.assertEqual(1, scope["reason_counts"].get("legacy_hit", 0))
        self.assertEqual(2, scope["reason_counts"].get("map_key_missing", 0))
        self.assertEqual("default", scope["map_source"], "来源逐字透出（Spec §C1）")
        self.assertEqual("9f2c0a1b4d5e", scope["map_fingerprint"], "指纹逐字透出（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：这张表的状态（读不到 / 空表 / 有 N 条 / 老载荷）
# --------------------------------------------------------------------------- #
class BMapState(unittest.TestCase):
    def test_b1_ready_reports_scale_and_source(self):
        out = value("pbMaterialMapState", [SCOPE_READY])
        self.assertEqual("ready", out["state"], "有表就是 ready（Spec §C2）")
        self.assertEqual("材料原文映射表 3 条，本次命中 1 行。", out["headline"],
                         "有表的人话逐字（Spec §C2）")
        self.assertEqual(3, out["entries"], "表里几条（Spec §C2）")
        self.assertEqual(1, out["hits"], "这次命中几行（Spec §C2）")
        self.assertEqual("仓库内置", out["sourceLabel"], "default 的人话（Spec §C2）")
        self.assertEqual("9f2c0a1b4d5e", out["fingerprint"], "指纹逐字，不许截断（Spec §C2）")

    def test_b2_empty_map_is_a_state_of_its_own(self):
        out = value("pbMaterialMapState", [SCOPE_EMPTY])
        self.assertEqual("empty", out["state"], "空表必须自己一档（Spec §C2）")
        self.assertEqual(HEADLINE_EMPTY, out["headline"], "空表的人话逐字（Spec §C2）")
        self.assertEqual(0, out["entries"], "0 条（Spec §C2）")
        self.assertEqual("环境变量覆盖（影响所有项目）", out["sourceLabel"],
                         "override 的人话（Spec §C2）")

    def test_b3_unavailable_beats_the_count(self):
        out = value("pbMaterialMapState", [SCOPE_UNAVAILABLE])
        self.assertEqual("unavailable", out["state"], "读不到优先于条数（Spec §C2）")
        self.assertEqual(HEADLINE_UNAVAILABLE, out["headline"], "读不到的人话逐字（Spec §C2）")

    def test_b4_legacy_payload_is_not_read_as_empty(self):
        for scope in (SCOPE_LEGACY, {}, {"row_total": 2}, None, "x", 123, []):
            out = value("pbMaterialMapState", [scope])
            self.assertEqual("unknown", out["state"],
                             "没有条数这一栏 → 未知档，不许当成 0 条（Spec §C2）：%r" % (scope,))
            self.assertEqual(HEADLINE_UNKNOWN, out["headline"], "未知档的人话逐字（Spec §C2）")
            self.assertEqual(0, out["entries"], "未知档给 0（Spec §C2）")

    def test_b5_counts_and_source_are_safe(self):
        got = run_cases("pbMaterialMapState",
                        [[{"map_entry_total": "3", "map_hit_total": None,
                           "map_source": "  override  ", "map_fingerprint": " ff "}],
                         [{"map_entry_total": "x", "map_hit_total": -1}],
                         [{"map_entry_total": 2.0, "map_source": "weird", "map_hit_total": 1}]])
        self.assertEqual(3, got[0]["entries"], "数字串照收（Spec §C2）")
        self.assertEqual(0, got[0]["hits"], "null 归 0（Spec §C2）")
        self.assertEqual("override", got[0]["source"])
        self.assertEqual("ff", got[0]["fingerprint"], "trim 后逐字（Spec §C2）")
        self.assertEqual("unknown", got[1]["state"], "非数 → 未知档（Spec §C2）")
        self.assertEqual(0, got[1]["hits"], "负数归 0（Spec §C2）")
        self.assertEqual("ready", got[2]["state"], "有限数照收（Spec §C2）")
        self.assertEqual("来源未知", got[2]["sourceLabel"], "闭集外的来源不猜（Spec §C2）")


# --------------------------------------------------------------------------- #
# C 组：逐档条数（不只给命中数）
# --------------------------------------------------------------------------- #
class CBreakdown(unittest.TestCase):
    def test_c1_all_five_buckets_in_fixed_order(self):
        scope = {"reason_counts": {"map_key_missing": 2, "map_hit": 1, "map_unknown": 0,
                                   "legacy_hit": 1, "map_entry_not_applied": 3}}
        self.assertEqual("这次解析：映射命中 1 行 · 旧规则命中 1 行 · 原文没映射 2 行 · "
                         "映射写了没生效 3 行", value("pbMaterialMapBreakdown", [scope]),
                         "五档人话与顺序固定、零档不出现（Spec §C2）")

    def test_c2_nothing_to_say_is_an_empty_string(self):
        for scope in (None, {}, {"reason_counts": {}}, {"reason_counts": {"map_hit": 0}},
                      {"reason_counts": "x"}, "x", []):
            self.assertEqual("", value("pbMaterialMapBreakdown", [scope]),
                             "没有非零档就不留空壳（Spec §C2）")

    def test_c3_unknown_bucket_is_not_given_a_name(self):
        text = value("pbMaterialMapBreakdown",
                     [{"reason_counts": {"map_hit": 2, "weird_bucket": 3, "another": 1}}])
        self.assertEqual("这次解析：映射命中 2 行 · 其它档 4 行", text,
                         "闭集外的档名不猜人话，合并成其它档（Spec §C2）")
        self.assertNotIn("weird_bucket", text, "不许把后端码当人话显示（Spec §C2）")

    def test_c4_non_numeric_counts_are_ignored(self):
        text = value("pbMaterialMapBreakdown",
                     [{"reason_counts": {"map_hit": "2", "legacy_hit": None,
                                         "map_key_missing": -1}}])
        self.assertEqual("这次解析：映射命中 2 行", text, "非数 / 负数不进清单（Spec §C2）")


# --------------------------------------------------------------------------- #
# D 组：这一段渲染（真跑字符串）
# --------------------------------------------------------------------------- #
class DBlockRendering(unittest.TestCase):
    def test_d1_ready_block_carries_state_source_and_fingerprint(self):
        html = value("pbMaterialMapAccountBlock", [SCOPE_READY], also=BLOCK_DEPS)
        self.assertIn('data-pb-material-map-state="ready"', html, "状态要能判（Spec §C2）")
        self.assertIn('data-pb-material-map-source="仓库内置"', html, "来源要能判（Spec §C2）")
        self.assertIn('data-pb-material-map-fingerprint="9f2c0a1b4d5e"', html,
                      "指纹要能回查这一版用的是哪份表（Spec §C2）")
        self.assertIn("材料原文映射表 3 条，本次命中 1 行。", html, "人话逐字（Spec §C2）")
        self.assertIn('data-pb-material-map-reasons="3"', html, "非零档数（Spec §C2）")
        self.assertIn("这次解析：映射命中 1 行 · 旧规则命中 1 行 · 原文没映射 2 行", html,
                      "逐档条数逐字（Spec §C2）")

    def test_d2_empty_map_is_said_out_loud(self):
        html = value("pbMaterialMapAccountBlock", [SCOPE_EMPTY], also=BLOCK_DEPS)
        self.assertIn('data-pb-material-map-state="empty"', html, "空表要自己一档（Spec §C2）")
        self.assertIn(HEADLINE_EMPTY, html, "空表的人话必须出现（Spec §C2）")
        self.assertNotIn("都齐了", html, "不许把空表说成「映射都齐了」（Spec §C4）")

    def test_d3_other_states_stay_silent(self):
        for scope in (SCOPE_UNAVAILABLE, SCOPE_LEGACY, {}, None):
            self.assertEqual("", value("pbMaterialMapAccountBlock", [scope], also=BLOCK_DEPS),
                             "读不到 / 未知档由既有那句承担，不许重复或编话（Spec §C2）")

    def test_d4_values_are_escaped(self):
        html = value("pbMaterialMapAccountBlock",
                     [{"map_entry_total": 1, "map_hit_total": 0, "map_source": "<b>",
                       "map_fingerprint": "<b>\"x\"", "reason_counts": {}}], also=BLOCK_DEPS)
        self.assertNotIn("<b>", html, "插值必须转义（Spec §C2）")
        self.assertIn("&lt;b&gt;", html, "转义后逐字（Spec §C2）")
        self.assertIn("&quot;x&quot;", html, "指纹里的引号也要转义（Spec §C2）")
        self.assertIn('data-pb-material-map-source="来源未知"', html,
                      "闭集外的来源不许原样显示（Spec §C2）")


# --------------------------------------------------------------------------- #
# E 组：面板接线与冻结面
# --------------------------------------------------------------------------- #
class EPanelWiring(unittest.TestCase):
    def setUp(self):
        self.body = function_body("pbPanel")

    def test_e1_panel_renders_the_account_once(self):
        self.assertEqual(1, self.body.count("pbMaterialMapAccountBlock("),
                         "只准一处调用（Spec §C3）")
        self.assertIn("${pbMaterialMapAccountBlock(record.business_material_rows)}", self.body)

    def test_e2_it_sits_right_after_the_existing_map_block(self):
        at = self.body.index("pbMaterialMapAccountBlock(")
        self.assertLess(self.body.index("const mapBlock"), at, "既有块之后（Spec §C3）")
        self.assertIn("${mapBlock}", self.body, "既有块仍要拼进模板（Spec §C3）")
        self.assertLess(self.body.index("${mapBlock}"), at, "紧跟既有块（Spec §C3）")

    def test_e3_existing_blocks_are_untouched(self):
        for marker in ('data-pb-material-map="1"',
                       "pbMaterialMapNote(record.business_material_rows)",
                       "data-pb-material-unresolved=", "pbMaterialUnresolvedHint(gaps)"):
            self.assertIn(marker, self.body, "既有块一字不动（Spec §C3）：%s" % marker)
        self.assertNotIn("fetch(", self.body, "面板只渲染，不新增请求（Spec §C4）")


class FFreeze(unittest.TestCase):
    def test_f1_helpers_hold_no_dom(self):
        for name in ("pbMaterialMapState", "pbMaterialMapBreakdown", "pbMaterialMapAccountBlock"):
            body = function_body(name)
            for forbidden in ("document", "window.", "sessionStorage", "localStorage",
                              "fetch(", "querySelector"):
                self.assertNotIn(forbidden, body,
                                 "%s() 必须是纯函数（Spec §C2）：不许出现 %s" % (name, forbidden))

    def test_f2_state_does_not_come_from_the_hit_count(self):
        body = function_body("pbMaterialMapState")
        self.assertIn("map_entry_total", body, "表的状态只读条数这一栏（Spec §C4）")
        self.assertIn("map_unavailable", body, "读不到优先（Spec §C4）")
        self.assertNotIn("reason_counts", body, "逐档条数不许拿来判状态（Spec §C4）")
        # 判定是**行为**上的：命中数再大也改不了状态（今天若拿"命中 > 0"当表非空的依据，
        # 这条会红）。
        got = run_cases("pbMaterialMapState",
                        [[{"map_entry_total": 0, "map_hit_total": 99}],
                         [{"map_entry_total": 5, "map_hit_total": 0}]])
        self.assertEqual("empty", got[0]["state"], "命中数不许决定表是不是空的（Spec §C4）")
        self.assertEqual("ready", got[1]["state"], "命中为 0 也不影响「表里有 N 条」（Spec §C4）")

    def test_f3_index_html_is_not_touched(self):
        html = (ROOT / "tech_app" / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("data-pb-material-map-state", html, "不改 index.html（Spec §C4）")

    def test_f4_inline_script_still_parses(self):
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "requirement-confirm.js 仍须通过 node --check：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
