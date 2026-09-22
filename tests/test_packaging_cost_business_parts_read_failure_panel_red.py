"""红测：成本面板上说得出「读不到业务部件清单」——它与「清单没换版」不许同形
（Spec `packaging-cost-business-parts-read-failure-panel.md`）。

现状缺口（代码级，可指到行）：
  · `packaging_cost.load_cost()` 的两条分支都给 `business_parts_unavailable`
    （早返回 `:2689` 给 `{}`；主路径 `:2720` 由 `_business_parts_drift()` 填）；
  · 而 `tech_app/frontend/requirement-confirm.js` 里 `business_parts_unavailable` **0 处引用**
    （`grep -c` 实测）：清单读不到时面板上只看得到 `stale=false` / `stale_reasons=[]`，
    与"清单没换版"**一模一样**；
  · 已有的 `pcBomUnavailableBanner()`（`data-pc-bom-unavailable`）与
    `pcRouteUnavailableBanner()`（`data-pc-route-unavailable`）都各自有一条 banner，
    第三条轴（业务部件清单）没有落点。

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

HEADLINE = ("暂时读不到业务部件清单：这一版成本只按几何零件算，而且判不了清单有没有换版 —— "
            "这不代表没换版。")
CODE = "business_parts_unavailable"
FLAG = {"code": CODE, "reason": "OperationalError"}

BANNER_DEPS = ["pcEsc", "pcBusinessPartsUnavailable"]

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


SOURCE = CONFIRM_JS.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# A 组：三态（读不到 / 读得到 / 老载荷）
# --------------------------------------------------------------------------- #
class AReadFailure(unittest.TestCase):
    def test_a1_unavailable_is_reported_with_code_and_reason(self):
        out = value("pcBusinessPartsUnavailable", [{"business_parts_unavailable": FLAG}])
        self.assertEqual("unavailable", out["state"], "读不到就是一档（Spec §C1）")
        self.assertEqual(CODE, out["code"], "稳定码逐字（Spec §C1）")
        self.assertEqual("OperationalError", out["reason"], "原因逐字（Spec §C1）")
        self.assertEqual(HEADLINE, out["headline"], "那句人话逐字（Spec §C1）")
        self.assertIn("这不代表没换版", out["headline"], "不许与「没换版」同形（Spec §C1）")

    def test_a2_ok_is_silent(self):
        for record in ({"business_parts_unavailable": {}},):
            out = value("pcBusinessPartsUnavailable", [record])
            self.assertEqual("ok", out["state"], "读得到就是 ok（Spec §C1）")
            self.assertEqual("", out["headline"], "读得到不编话（Spec §C1）")
            self.assertEqual("", out["code"], "读得到没有码（Spec §C1）")

    def test_a3_legacy_payload_is_not_read_as_ok_nor_as_missing(self):
        cases = [[{}], [{"stale": False, "stale_reasons": []}], [{"business_parts_unavailable": None}],
                 [{"business_parts_unavailable": "x"}], [{"business_parts_unavailable": []}],
                 [None], ["x"], [123]]
        for out in run_cases("pcBusinessPartsUnavailable", cases):
            self.assertEqual("unknown", out["state"], "没有这一栏 → 未知档（Spec §C1）")
            self.assertEqual("", out["headline"], "未知档不许说成「没换版」也不许说成「读不到」（Spec §C1）")
            self.assertEqual("", out["code"], "未知档没有码（Spec §C1）")

    def test_a4_non_empty_object_without_code_falls_back(self):
        got = run_cases("pcBusinessPartsUnavailable",
                        [[{"business_parts_unavailable": {"reason": "OperationalError"}}],
                         [{"business_parts_unavailable": {"code": "  "}}]])
        for out in got:
            self.assertEqual("unavailable", out["state"], "非空对象就是读失败（Spec §C1）")
            self.assertEqual(CODE, out["code"], "码缺失时兜底，不许留空（Spec §C1）")

    def test_a5_never_raises(self):
        got = run_cases("pcBusinessPartsUnavailable",
                        [[None], [{}], [[]], ["x"], [{"business_parts_unavailable": 7}]])
        for out in got:
            self.assertIn(out["state"], ("ok", "unknown", "unavailable"),
                          "状态闭集（Spec §C1）")
        self.assertEqual("unknown", got[4]["state"], "那一栏不是对象 → 未知档（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：这一条 banner 的渲染
# --------------------------------------------------------------------------- #
class BBanner(unittest.TestCase):
    def test_b1_banner_carries_code_and_reason(self):
        html = value("pcBusinessPartsBanner", [{"business_parts_unavailable": FLAG}],
                     also=BANNER_DEPS)
        self.assertIn('data-pc-business-parts-unavailable="%s"' % CODE, html, "码要能判（Spec §C1）")
        self.assertIn(HEADLINE, html, "人话逐字（Spec §C1）")
        self.assertIn("（OperationalError）", html, "原因逐字附在后面（Spec §C1）")

    def test_b2_no_empty_parentheses(self):
        html = value("pcBusinessPartsBanner", [{"business_parts_unavailable": {"code": CODE}}],
                     also=BANNER_DEPS)
        self.assertIn(HEADLINE, html, "没有原因也要说清（Spec §C1）")
        self.assertNotIn("（）", html, "不许留空括号（Spec §C1）")

    def test_b3_other_states_render_nothing(self):
        for record in ({"business_parts_unavailable": {}}, {}, {"stale": False}, None,
                       {"business_parts_unavailable": None}):
            self.assertEqual("", value("pcBusinessPartsBanner", [record], also=BANNER_DEPS),
                             "读得到 / 老载荷都不许多出节点（Spec §C1）")

    def test_b4_values_are_escaped(self):
        html = value("pcBusinessPartsBanner",
                     [{"business_parts_unavailable": {"code": "<b>", "reason": '"x"'}}],
                     also=BANNER_DEPS)
        self.assertNotIn("<b>", html, "插值必须转义（Spec §C1）")
        self.assertIn("&lt;b&gt;", html, "转义后逐字（Spec §C1）")
        self.assertIn("&quot;x&quot;", html, "原因也要转义（Spec §C1）")


# --------------------------------------------------------------------------- #
# C 组：面板接线
# --------------------------------------------------------------------------- #
class CPanelWiring(unittest.TestCase):
    def setUp(self):
        self.body = function_body("pcPanel")

    def test_c1_panel_renders_the_banner_once(self):
        self.assertEqual(1, self.body.count("pcBusinessPartsBanner(record)"),
                         "只准一处调用（Spec §C2）")
        self.assertIn("${pcBusinessPartsBanner(record)}", self.body)

    def test_c2_it_sits_with_the_other_read_failure_banners(self):
        at = self.body.index("pcBusinessPartsBanner(record)")
        self.assertLess(self.body.index("${pcRouteUnavailableBanner(record)}"), at,
                        "路线那条轴之后（Spec §C2）")
        self.assertLess(at, self.body.index("${pcRuleSnapshotBanner(record)}"),
                        "规则快照那条轴之前（Spec §C2）")

    def test_c3_existing_banners_are_untouched(self):
        for marker in ("pcStaleBanner(record)", "pcBomUnavailableBanner(record)",
                       "pcRouteUnavailableBanner(record)", "pcRuleSnapshotBanner(record)",
                       "pcHandoffDriftBanner(handoff)", "pcAuditBlock(handoff, pending, writable)",
                       'data-pc-readiness="', "pcContentBindingBlock(cost)"):
            self.assertIn(marker, self.body, "既有 banner 一字不动（Spec §C2）：%s" % marker)
        self.assertNotIn("fetch(", self.body, "面板只渲染，不新增请求（Spec §C3）")


# --------------------------------------------------------------------------- #
# D 组：冻结面
# --------------------------------------------------------------------------- #
class DFreeze(unittest.TestCase):
    def test_d1_helpers_hold_no_dom(self):
        for name in ("pcBusinessPartsUnavailable", "pcBusinessPartsBanner"):
            body = function_body(name)
            for forbidden in ("document", "window.", "sessionStorage", "localStorage",
                              "fetch(", "querySelector"):
                self.assertNotIn(forbidden, body,
                                 "%s() 必须是纯函数（Spec §C1）：不许出现 %s" % (name, forbidden))

    def test_d2_read_failure_is_not_mixed_into_the_changed_copy(self):
        body = function_body("pcBusinessPartsUnavailable")
        self.assertIn("business_parts_unavailable", body, "只读后端那一栏（Spec §C3）")
        self.assertNotIn("stale_reasons", body, "读不到不是「变了」（Spec §C3）")
        self.assertNotIn("stale", body, "不许按 stale 反推（Spec §C3）")

    def test_d3_stale_copy_table_is_untouched(self):
        block = SOURCE[SOURCE.index("const PC_STALE_REASONS"):SOURCE.index("function pcStaleBanner")]
        self.assertNotIn("business_parts_unavailable", block,
                         "稳定码不许进「变了」的人话表（Spec §C3）")
        self.assertIn("business_parts_reimported", block, "既有那句人话一字不动（Spec §C3）")

    def test_d4_index_html_is_not_touched(self):
        html = (ROOT / "tech_app" / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("data-pc-business-parts-unavailable", html, "不改 index.html（Spec §C3）")

    def test_d5_inline_script_still_parses(self):
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "requirement-confirm.js 仍须通过 node --check：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
