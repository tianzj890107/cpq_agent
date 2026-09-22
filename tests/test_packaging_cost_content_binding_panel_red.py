"""红测：成本面板上必须看得见「包材绑定数据源缺失」
（Spec `packaging-cost-content-binding-panel.md`）。

现状缺口（代码级，可指到行）：
  · 后端每一份成本返回体都挂着 `content_binding`（`compute_project()` 结果体 /
    读侧 `_rehydrate()` 的 `_content_binding_of()`）—— `source` / `bound_total` /
    `unbound_total` / `bound_codes` / `unbound_codes`，就绪门也带 `content_binding_source`
    （`packaging_cost.py:2031`）；
  · 而 `tech_app/frontend/requirement-confirm.js` 里 `content_binding` / `unbound_codes` /
    `bound_total` **全部 0 处引用**（`grep -c` 实测）：`bound_gaps = []` 的两种含义
    （"这一单一项都不缺" / "认不出这一单用了哪几项包材，所以只披露不阻断"）在页面上同形，
    §2.4 要求"指名道姓"的 `unbound_codes` 在界面上不存在。

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

NONE_HEADLINE = "包材绑定数据源缺失：认不出这一单用了哪几项包材，包材缺口（content_formula_error）只披露、不进阻断。"
AUTHORITATIVE_HEADLINE = "包材绑定数据源：权威来源，这一单用哪几项包材是从权威数据里读的。"
UNKNOWN_HEADLINE = "后端没给包材绑定来源（content_binding.source），「包材没有缺口」这句话不成立。"

NONE_BINDING = {"source": "none", "bound_total": 0, "unbound_total": 7, "bound_codes": [],
                "unbound_codes": ["PKG-CT-DIVIDER", "PKG-CT-TRAY"]}
AUTHORITATIVE_BINDING = {"source": "authoritative", "bound_total": 4, "unbound_total": 0,
                         "bound_codes": ["PKG-CT-BAG"], "unbound_codes": []}

BLOCK_DEPS = ["pcEsc", "pcContentBinding", "pcContentBindingCodesText"]
# 取码口径只有一个来源（Spec §C1）：点名那段复用 `pcContentBinding()`，抽函数时要一起 eval。
CODES_DEPS = ["pcContentBinding"]

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
/* 被抽的函数若复用了同文件的另一个纯函数，一起 eval（Spec §C1：规则只在源码里出现一次）。 */
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
# A 组：来源三态（缺失 / 权威 / 未知档）
# --------------------------------------------------------------------------- #
class AContentBindingSource(unittest.TestCase):
    def test_a1_missing_source_is_reported_as_missing(self):
        out = value("pcContentBinding", [{"content_binding": NONE_BINDING}])
        self.assertEqual("none", out["source"], "来源逐字（Spec §C1）")
        self.assertEqual("none", out["state"], "状态三态原样（Spec §C1）")
        self.assertEqual(NONE_HEADLINE, out["headline"], "「我不知道」的人话（Spec §C1）")
        self.assertEqual(0, out["boundTotal"], "绑上的条数（Spec §C1）")
        self.assertEqual(7, out["unboundTotal"], "没绑上的条数（Spec §C1）")
        self.assertEqual(["PKG-CT-DIVIDER", "PKG-CT-TRAY"], out["unboundCodes"],
                         "被降级披露的包材项逐个点名（Spec §C1 / §2.4）")

    def test_a2_authoritative_source_is_not_called_missing(self):
        out = value("pcContentBinding", [{"content_binding": AUTHORITATIVE_BINDING}])
        self.assertEqual("authoritative", out["state"], "权威来源逐字（Spec §C1）")
        self.assertEqual(AUTHORITATIVE_HEADLINE, out["headline"], "权威那份的人话（Spec §C1）")
        self.assertNotIn("缺失", out["headline"], "权威来源不许说成缺失（Spec §C1 / §4）")

    def test_a3_unknown_source_is_not_guessed(self):
        cases = [[{}], [{"content_binding": {}}], [{"content_binding": {"source": "demo"}}],
                 [{"content_binding": {"source": ""}}], [{"content_binding": {"source": "NONE"}}],
                 [{"content_binding": "x"}], [{"readiness": {"verdict": "formal"}}],
                 [None], ["x"], [123], [[]]]
        for out in run_cases("pcContentBinding", cases):
            self.assertEqual("unknown", out["state"], "闭集外的来源一律未知档（Spec §C1）")
            self.assertEqual("", out["source"], "认不出的来源不许原样透出（Spec §C1）")
            self.assertEqual(UNKNOWN_HEADLINE, out["headline"], "未知档的人话（Spec §C1）")
            self.assertNotIn("权威", out["headline"], "未知档不许被当成权威（Spec §C1 / §4）")
            self.assertNotIn("缺失", out["headline"], "未知档也不许假装缺失（Spec §C1）")

    def test_a4_counts_are_numbers_with_zero_fallback(self):
        out = value("pcContentBinding", [{"content_binding": NONE_BINDING}])
        self.assertIsInstance(out["boundTotal"], (int, float), "条数必须是数字（Spec §C1）")
        self.assertIsInstance(out["unboundTotal"], (int, float), "条数必须是数字（Spec §C1）")
        got = run_cases("pcContentBinding",
                        [[{"content_binding": {"source": "none", "bound_total": "3",
                                               "unbound_total": None}}],
                         [{"content_binding": {"source": "none", "bound_total": "x",
                                               "unbound_total": -1}}]])
        self.assertEqual({"bound": 3, "unbound": 0},
                         {"bound": got[0]["boundTotal"], "unbound": got[0]["unboundTotal"]},
                         "数字串照收、null 归 0（Spec §C1）")
        self.assertEqual({"bound": 0, "unbound": 0},
                         {"bound": got[1]["boundTotal"], "unbound": got[1]["unboundTotal"]},
                         "非有限数 / 负数一律 0（Spec §C1）")

    def test_a5_codes_are_trimmed_verbatim_and_in_order(self):
        got = run_cases("pcContentBinding",
                        [[{"content_binding": {"unbound_codes": ["  A  ", "", "   ", None, 7,
                                                                "B"]}}],
                         [{"content_binding": {"unbound_codes": "PKG-CT-BAG"}}],
                         [{"content_binding": {"unbound_codes": None}}]])
        self.assertEqual(["A", "7", "B"], got[0]["unboundCodes"],
                         "逐项 trim + 丢空 + 保序，非字符串转字符串（Spec §C1）")
        self.assertEqual([], got[1]["unboundCodes"], "不是数组 → 空清单（Spec §C1）")
        self.assertEqual([], got[2]["unboundCodes"], "null → 空清单（Spec §C1）")

    def test_a6_non_object_never_raises(self):
        for out in run_cases("pcContentBinding", [[None], ["x"], [123], [[]]]):
            self.assertEqual("unknown", out["state"], "不是对象 → 未知档（Spec §C1）")
            self.assertEqual([], out["unboundCodes"], "不是对象 → 空清单（Spec §C1）")
            self.assertEqual(0, out["unboundTotal"], "不是对象 → 全 0（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：被降级披露的包材项必须被点名
# --------------------------------------------------------------------------- #
class BCodesText(unittest.TestCase):
    def test_b1_nothing_to_say_is_an_empty_string(self):
        cases = [[None], [{}], [{"content_binding": {}}],
                 [{"content_binding": {"unbound_codes": []}}],
                 [{"content_binding": {"unbound_codes": ["", "   "]}}]]
        for text in run_cases("pcContentBindingCodesText", cases, also=CODES_DEPS):
            self.assertEqual("", text, "没有码就不留空壳（Spec §C1）")

    def test_b2_codes_are_named_verbatim(self):
        text = value("pcContentBindingCodesText", [{"content_binding": NONE_BINDING}],
                     also=CODES_DEPS)
        self.assertEqual("被降级披露的包材项：PKG-CT-DIVIDER、PKG-CT-TRAY", text,
                         "逐字点名，不许改写 / 截断 / 加序号（Spec §C1 / §2.4）")

    def test_b3_same_trim_semantics_as_the_source(self):
        text = value("pcContentBindingCodesText",
                     [{"content_binding": {"unbound_codes": [" A ", "", "  B  ", None]}}],
                     also=CODES_DEPS)
        self.assertEqual("被降级披露的包材项：A、B", text,
                         "与 `pcContentBinding()` 同一取码口径（Spec §C1）")


# --------------------------------------------------------------------------- #
# C 组：这一段的渲染（真跑字符串）
# --------------------------------------------------------------------------- #
class CBlockRendering(unittest.TestCase):
    def test_c1_state_headline_counts_and_codes_are_rendered(self):
        html = value("pcContentBindingBlock", [{"content_binding": NONE_BINDING}], also=BLOCK_DEPS)
        self.assertIn('data-pc-content-binding="none"', html, "来源要能判（Spec §C2）")
        self.assertIn(NONE_HEADLINE, html, "人话逐字（Spec §C1）")
        self.assertIn("绑定 0 · 未绑 7", html, "条数逐字（Spec §C2）")
        self.assertIn('data-pc-content-binding-codes="2"', html, "点名清单要能判（Spec §C2）")
        self.assertIn("被降级披露的包材项：PKG-CT-DIVIDER、PKG-CT-TRAY", html,
                      "指名道姓（Spec §2.4）")
        self.assertNotIn("包材没有缺口", html, "不许说成没有缺口（Spec §4）")

    def test_c2_no_codes_means_no_extra_node(self):
        html = value("pcContentBindingBlock", [{"content_binding": AUTHORITATIVE_BINDING}],
                     also=BLOCK_DEPS)
        self.assertIn('data-pc-content-binding="authoritative"', html, "权威来源照渲（Spec §C2）")
        self.assertIn(AUTHORITATIVE_HEADLINE, html, "权威的人话（Spec §C1）")
        self.assertNotIn("data-pc-content-binding-codes", html, "没有码就一个节点都不多（Spec §C2）")
        self.assertNotIn("被降级披露", html, "没有码就不点名（Spec §C2）")

    def test_c3_unknown_is_rendered_too(self):
        for html in run_cases("pcContentBindingBlock", [[None], [{}], [{"content_binding": {}}]],
                              also=BLOCK_DEPS):
            self.assertIn('data-pc-content-binding="unknown"', html, "未知档也要说（Spec §C2）")
            self.assertIn(UNKNOWN_HEADLINE, html, "未知档的人话逐字（Spec §C1）")
            self.assertIn("绑定 0 · 未绑 0", html, "未知档给 0，不给 null（Spec §C1）")

    def test_c4_values_are_escaped(self):
        html = value("pcContentBindingBlock",
                     [{"content_binding": {"source": "none", "unbound_total": 1,
                                           "unbound_codes": ["<b>X</b>"]}}], also=BLOCK_DEPS)
        self.assertNotIn("<b>X</b>", html, "插值必须转义（Spec §C1）")
        self.assertIn("&lt;b&gt;X&lt;/b&gt;", html, "转义后逐字（Spec §C1）")

    def test_c5_counts_come_from_the_payload_not_from_the_code_list(self):
        html = value("pcContentBindingBlock",
                     [{"content_binding": {"source": "none", "bound_total": 2,
                                           "unbound_total": 9, "unbound_codes": ["A"]}}],
                     also=BLOCK_DEPS)
        self.assertIn("绑定 2 · 未绑 9", html, "条数取后端给的值，不许按码数重算（Spec §C3）")
        self.assertIn('data-pc-content-binding-codes="1"', html, "点名清单按真实条数（Spec §C2）")


# --------------------------------------------------------------------------- #
# D 组：面板接线
# --------------------------------------------------------------------------- #
class DPanelWiring(unittest.TestCase):
    def setUp(self):
        self.body = function_body("pcPanel")

    def test_d1_panel_renders_the_block(self):
        self.assertIn("pcContentBindingBlock(cost)", self.body, "面板要渲染这一段（Spec §C2）")

    def test_d2_block_is_called_once_and_unconditionally(self):
        self.assertEqual(1, self.body.count("pcContentBindingBlock(cost)"),
                         "只准一处调用（Spec §C2）")
        self.assertIn("${pcContentBindingBlock(cost)}", self.body,
                      "无条件渲染：不许按 built / gaps 藏起来（Spec §C2 / §C3）")

    def test_d3_block_sits_between_readiness_bar_and_head(self):
        at = self.body.index("pcContentBindingBlock(cost)")
        self.assertLess(self.body.index("${readinessBlock}"), at, "裁决条之后（Spec §C2）")
        self.assertLess(at, self.body.index("${head}"), "合计之前（Spec §C2）")

    def test_d4_existing_banners_are_untouched(self):
        for marker in ("pcStaleBanner(record)", "pcBomUnavailableBanner(record)",
                       "pcRouteUnavailableBanner(record)", "pcRuleSnapshotBanner(record)",
                       "pcHandoffDriftBanner(handoff)", "pcAuditBlock(handoff, pending, writable)",
                       'data-pc-readiness="'):
            self.assertIn(marker, self.body, "既有 banner / 裁决条一字不动（Spec §C2）：%s" % marker)
        self.assertNotIn("fetch(", self.body, "面板只渲染，不新增请求（Spec §C3）")


# --------------------------------------------------------------------------- #
# E 组：冻结面
# --------------------------------------------------------------------------- #
class EFreeze(unittest.TestCase):
    def test_e1_helpers_hold_no_dom(self):
        for name in ("pcContentBinding", "pcContentBindingCodesText", "pcContentBindingBlock"):
            body = function_body(name)
            for forbidden in ("document", "window.", "sessionStorage", "localStorage",
                              "fetch(", "querySelector"):
                self.assertNotIn(forbidden, body,
                                 "%s() 必须是纯函数（Spec §C1）：不许出现 %s" % (name, forbidden))

    def test_e2_source_comes_from_the_payload_and_only_from_here(self):
        body = function_body("pcContentBinding")
        self.assertIn("content_binding", body, "来源只读后端 payload（Spec §C3 / §4）")
        self.assertNotIn("readiness", body, "裁决归上一批，不许在这里重算（Spec §C3）")
        self.assertNotIn("gaps", body, "不许按缺口重算来源（Spec §C3 / §4）")
        block = function_body("pcContentBindingBlock")
        self.assertIn("pcContentBinding(", block, "这一段只消费同一个纯函数（Spec §C1）")

    def test_e3_index_html_is_not_touched(self):
        html = (ROOT / "tech_app" / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("data-pc-content-binding", html, "不改 index.html（Spec §C3）")

    def test_e4_inline_script_still_parses(self):
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "requirement-confirm.js 仍须通过 node --check：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
