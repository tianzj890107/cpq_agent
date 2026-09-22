"""红测：成本面板上必须看得见「正式 / 暂定」裁决与逐条缺口的分层
（Spec `packaging-cost-readiness-panel.md`）。

现状缺口（代码级，可指到行）：
  · 后端 `packaging_cost.load_cost()` 的每一份返回体都挂着 `readiness`
    （`_with_readiness()` `:2553` → `packaging_cost_readiness_gate()` `:1984`），
    而 `tech_app/frontend/requirement-confirm.js` 里 `readiness` / `verdict` / `severity` /
    `resolution_action` / `blocking` / `advisory` **全部 0 处引用**（`grep -c` 实测）；
  · `pcGapLine()`（`:996`）把**每一条**缺口都写成 `待询价：<detail>` —— 缺展开尺寸 /
    缺公式 / 缺分摊基数的行都被说成"待询价"；
  · `packaging-cost-readiness-severity-layering.md` §2.2 的 blocking / advisory 分层
    在界面上被摊成一条平列。

纪律：`node -e` 抽闭包内具名函数体执行（纯函数）+ 源码守卫 + `node --check`；
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

FORMAL_HEADLINE = "正式成本：缺口已清零，可用于正式报价。"
PROVISIONAL_HEADLINE = "暂定成本：有阻断缺口或还没测算，出价前必须走放行留痕（POC 豁免签字）。"
UNKNOWN_HEADLINE = "这份成本没有正式/暂定的裁决（后端没给 verdict），别当成正式成本。"
BLOCKING_LABEL = "阻断（挡住正式成本）"
ADVISORY_LABEL = "提示（不影响正式/暂定）"
SEVERITY_UNKNOWN_LABEL = "未知档（后端没给严重度）"

PRICE_GAP = {"code": "material_price_missing", "where": "RB01001-P01",
             "detail": "材料「装帧布」没有有效价格，材料行不出金额",
             "missing_variable": ["price"], "material": "装帧布",
             "resolution_action": "在物料主数据里补该材料的权威单价",
             "resolution_entry": "kb_material_price", "severity": "blocking"}
ADVISORY_GAP = {"code": "loss_rate_missing", "where": "RB01001-P02",
                "detail": "材料「装帧布」没有损耗率", "missing_variable": ["loss_rate"],
                "resolution_action": "补该材料的损耗率（或确认按 0 计并签字）",
                "resolution_entry": "kb_cost_factor", "severity": "advisory"}
SIZE_GAP = {"code": "part_size_missing", "where": "RB01001-P03",
            "detail": "部件「内盒灰板」没有展开尺寸，材料行不出金额",
            "missing_variable": ["length_mm", "width_mm"],
            "resolution_action": "补零件展开尺寸（2.1 零件提取或盒型尺寸确认）",
            "resolution_entry": "packaging-parts", "severity": "blocking"}
EVIDENCE = [PRICE_GAP, ADVISORY_GAP, SIZE_GAP]
PROVISIONAL = {"version": "packaging-cost-readiness/1", "verdict": "provisional",
               "formal_ready": False, "has_gaps": True, "gap_total": 3, "blocking_total": 2,
               "advisory_total": 1, "unbound_total": 0, "gaps": EVIDENCE,
               "reasons": ["2 项阻断缺口未清零", "1 项提示缺口（不影响正式/暂定）"]}
FORMAL = {"version": "packaging-cost-readiness/1", "verdict": "formal", "formal_ready": True,
          "has_gaps": False, "gap_total": 0, "blocking_total": 0, "advisory_total": 0,
          "unbound_total": 0, "gaps": [], "reasons": []}

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


def value(name: str, args, also=()):
    return run_cases(name, [args], also=also)[0]


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
# A 组：裁决（正式 / 暂定 / 未知档）
# --------------------------------------------------------------------------- #
class AReadinessVerdict(unittest.TestCase):
    def test_a1_formal_is_reported_as_formal(self):
        out = value("pcReadinessVerdict", [{"readiness": FORMAL}])
        self.assertEqual("formal", out["verdict"], "正式裁决逐字（Spec §C1）")
        self.assertTrue(out["formal"], "`formal` 必须是布尔真（Spec §C1）")
        self.assertEqual(FORMAL_HEADLINE, out["headline"], "正式那份的人话（Spec §C1）")

    def test_a2_provisional_is_not_dressed_up_as_formal(self):
        out = value("pcReadinessVerdict", [{"readiness": PROVISIONAL}])
        self.assertEqual("provisional", out["verdict"], "暂定裁决逐字（Spec §C1）")
        self.assertFalse(out["formal"], "暂定不许算成正式（Spec §C1）")
        self.assertEqual(PROVISIONAL_HEADLINE, out["headline"], "暂定那份的人话（Spec §C1）")

    def test_a3_unknown_verdict_is_not_guessed(self):
        got = run_cases("pcReadinessVerdict",
                        [[{"readiness": {"verdict": "pending"}}], [{"readiness": {"verdict": ""}}],
                         [{"readiness": {}}], [{}], [None]])
        for out in got:
            self.assertEqual("", out["verdict"], "闭集外的裁决一律未知档（Spec §C1）")
            self.assertFalse(out["formal"], "未知档不许被当成正式（Spec §C1）")
            self.assertEqual(UNKNOWN_HEADLINE, out["headline"], "未知档的人话（Spec §C1）")

    def test_a4_reasons_are_verbatim(self):
        out = value("pcReadinessVerdict", [{"readiness": PROVISIONAL}])
        self.assertEqual(["2 项阻断缺口未清零", "1 项提示缺口（不影响正式/暂定）"], out["reasons"],
                         "原因逐字保序（Spec §C1）")
        got = run_cases("pcReadinessVerdict",
                        [[{"readiness": {"reasons": ["  a  ", "", "   ", None, 7]}}],
                         [{"readiness": {"reasons": "x"}}]])
        self.assertEqual(["a", "7"], got[0]["reasons"], "trim + 丢空 + 非字符串转字符串（Spec §C1）")
        self.assertEqual([], got[1]["reasons"], "不是数组 → 空清单（Spec §C1）")

    def test_a5_counts_are_numbers_with_zero_fallback(self):
        out = value("pcReadinessVerdict", [{"readiness": PROVISIONAL}])
        self.assertEqual(3, out["counts"]["gap_total"], "缺口合计（Spec §C1）")
        self.assertEqual(2, out["counts"]["blocking_total"], "阻断条数（Spec §C1）")
        self.assertEqual(1, out["counts"]["advisory_total"], "提示条数（Spec §C1）")
        self.assertEqual(0, out["counts"]["unbound_total"], "键必须存在（Spec §C1）")
        got = run_cases("pcReadinessVerdict",
                        [[{"readiness": {"gap_total": "3", "blocking_total": None,
                                         "advisory_total": "x", "unbound_total": -1}}]])
        self.assertEqual({"gap_total": 3, "blocking_total": 0, "advisory_total": 0,
                          "unbound_total": 0}, got[0]["counts"],
                         "非有限数 / 负数一律 0，不许 null（Spec §C1）")

    def test_a6_non_object_never_raises(self):
        got = run_cases("pcReadinessVerdict", [[None], ["x"], [123], [[]], [{"readiness": "x"}]])
        for out in got:
            self.assertEqual("", out["verdict"], "不是对象 → 未知档（Spec §C1）")
            self.assertEqual([], out["reasons"], "不是对象 → 空原因（Spec §C1）")
            self.assertEqual(0, out["counts"]["gap_total"], "不是对象 → 全 0（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：严重度人话（闭集两档，其余未知档）
# --------------------------------------------------------------------------- #
class BSeverityLabel(unittest.TestCase):
    def test_b1_two_known_severities_are_verbatim(self):
        self.assertEqual(BLOCKING_LABEL, value("pcGapSeverityLabel", ["blocking"]))
        self.assertEqual(ADVISORY_LABEL, value("pcGapSeverityLabel", ["advisory"]))

    def test_b2_other_values_are_unknown(self):
        got = run_cases("pcGapSeverityLabel", [[""], [None], [{}], [123], ["BLOCKING"], ["warning"]])
        for text in got:
            self.assertEqual(SEVERITY_UNKNOWN_LABEL, text,
                             "闭集外的严重度一律未知档（Spec §C1）")


# --------------------------------------------------------------------------- #
# C 组：这条缺口要补什么、找谁补
# --------------------------------------------------------------------------- #
class CGapActionText(unittest.TestCase):
    def test_c1_nothing_to_say_is_an_empty_string(self):
        got = run_cases("pcGapActionText",
                        [[{}], [None], [{"missing_variable": [], "resolution_action": "",
                                         "resolution_entry": ""}],
                         [{"missing_variable": ["  ", ""]}]])
        for text in got:
            self.assertEqual("", text, "没话说就别说（Spec §C1）")

    def test_c2_action_only(self):
        self.assertEqual("；补零件展开尺寸", value("pcGapActionText",
                                              [{"resolution_action": "补零件展开尺寸"}]))

    def test_c3_variables_only(self):
        self.assertEqual("要补：price", value("pcGapActionText", [{"missing_variable": ["price"]}]))

    def test_c4_all_three_pieces(self):
        text = value("pcGapActionText", [SIZE_GAP])
        self.assertIn("要补：length_mm、width_mm", text, "变量逐字并去重顺序（Spec §C1）")
        self.assertIn("补零件展开尺寸（2.1 零件提取或盒型尺寸确认）", text, "动作逐字（Spec §C1）")
        self.assertIn("（入口：packaging-parts）", text, "补数入口逐字（Spec §C1）")

    def test_c5_empty_entry_leaves_no_empty_parentheses(self):
        text = value("pcGapActionText",
                     [{"missing_variable": ["  price  ", ""], "resolution_action": "补价",
                       "resolution_entry": "   "}])
        self.assertEqual("要补：price；补价", text, "缺的部分整段跳过（Spec §C1）")


# --------------------------------------------------------------------------- #
# D 组：前缀（待询价只留给价格 / 费率类）
# --------------------------------------------------------------------------- #
class DGapPrefix(unittest.TestCase):
    def test_d1_price_and_rate_codes_are_quoted(self):
        got = run_cases("pcGapPrefix",
                        [["material_price_missing"], ["material_price_unit_missing"],
                         ["material_price_unit_mismatch"], ["rate_missing"],
                         ["freight_rule_missing"]])
        for text in got:
            self.assertEqual("待询价", text, "价格 / 费率类才叫待询价（Spec §C1）")

    def test_d2_other_codes_are_not_quoted(self):
        got = run_cases("pcGapPrefix",
                        [["part_size_missing"], ["no_formula:print"], ["material_gsm_missing"],
                         ["loss_rate_missing"], ["tooling_basis_missing:T-PKG-DIE-REFUND"],
                         ["content_formula_error:PKG-P-BAG"], ["step_time_missing"]])
        for text in got:
            self.assertEqual("待补输入", text,
                             "缺的是尺寸 / 公式 / 口径，不是价格（Spec §C1 / §4）")

    def test_d3_empty_or_non_string_is_not_quoted(self):
        got = run_cases("pcGapPrefix", [[""], [None], [{}], [123]])
        for text in got:
            self.assertEqual("待补输入", text, "没有码 → 不许说成待询价（Spec §C1）")


# --------------------------------------------------------------------------- #
# E 组：面板接线
# --------------------------------------------------------------------------- #
class EPanelWiring(unittest.TestCase):
    def setUp(self):
        self.body = function_body("pcPanel")

    def test_e1_readiness_bar_is_rendered(self):
        self.assertIn('data-pc-readiness="', self.body, "裁决条（Spec §C2）")
        self.assertIn("pcReadinessVerdict(cost)", self.body, "裁决取自后端 payload（Spec §C3）")

    def test_e2_reasons_are_rendered_one_by_one(self):
        self.assertIn('data-pc-readiness-reason="', self.body, "原因逐条（Spec §C2）")

    def test_e3_blocking_group_is_rendered(self):
        self.assertIn('data-pc-gap-blocking="', self.body, "阻断缺口一组（Spec §C2）")

    def test_e4_advisory_group_is_rendered(self):
        self.assertIn('data-pc-gap-advisory="', self.body, "提示缺口一组（Spec §C2）")

    def test_e5_each_gap_carries_code_severity_prefix_and_action(self):
        self.assertIn('data-pc-gap="', self.body, "逐条缺口（Spec §C2）")
        self.assertIn('data-pc-gap-severity="', self.body, "严重度原文（Spec §C2）")
        self.assertIn("pcGapPrefix(", self.body, "前缀由纯函数决定（Spec §C2）")
        self.assertIn("pcGapActionText(", self.body, "动作由纯函数产出（Spec §C2）")

    def test_e6_existing_banners_are_untouched(self):
        for marker in ("pcStaleBanner(record)", "pcBomUnavailableBanner(record)",
                       "pcRouteUnavailableBanner(record)", "pcRuleSnapshotBanner(record)",
                       "pcHandoffDriftBanner(handoff)", "pcAuditBlock(handoff, pending, writable)"):
            self.assertIn(marker, self.body, "既有 banner 一字不动（Spec §C2）：%s" % marker)
        self.assertNotIn("fetch(", self.body, "面板只渲染，不新增请求（Spec §C3）")


# --------------------------------------------------------------------------- #
# F 组：冻结面
# --------------------------------------------------------------------------- #
class FFreeze(unittest.TestCase):
    def test_f1_helpers_hold_no_dom(self):
        for name in ("pcReadinessVerdict", "pcGapSeverityLabel", "pcGapActionText", "pcGapPrefix"):
            body = function_body(name)
            for forbidden in ("document", "window.", "sessionStorage", "localStorage",
                              "fetch(", "querySelector"):
                self.assertNotIn(forbidden, body,
                                 "%s() 必须是纯函数（Spec §C1）：不许出现 %s" % (name, forbidden))

    def test_f2_verdict_comes_from_the_payload(self):
        body = function_body("pcReadinessVerdict")
        self.assertIn("readiness", body, "裁决只读后端 payload（Spec §C3 / §4）")
        self.assertNotIn("gaps", body, "不许在前端按缺口重算裁决（Spec §C3 / §4）")

    def test_f3_index_html_is_not_touched(self):
        self.assertNotIn("data-pc-readiness",
                         (ROOT / "tech_app" / "frontend" / "index.html").read_text(encoding="utf-8"),
                         "不改 index.html（Spec §C3）")

    def test_f4_inline_script_still_parses(self):
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "requirement-confirm.js 仍须通过 node --check：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
