"""红测：第 5 步的币种必须能由界面真实数据满足（Spec §2/§4）。

Spec：`docs/specs/packaging-quote-step5-currency-must-come-from-a-real-ui-source.md`

现状缺口（2026-09-23 在 34 实测 + 本机读源码，不是推断）：
  · `POST /agents/quote/api/quote/step-gate`（step_no=5）要求**每一行**明细都有币种
    （`invalid_currency`「第 1 行币种为空。」）；
  · 而 `GET /agents/quote/api/meta` 给 `s5_detail` 的 14 列里没有「币种」：
    产品系列 / 产品型号 / 成品编码 / 成品描述 / 版本扩展 / 方案描述 / 规格 / 数量 /
    报价 / 折扣 / 折后价格 / 总金额 / 税率 / 税金；
  · 页面 `确认需求解析结果.html` 的 `S5_DEFAULTS` 也没有币种，`fillStep5()` 只把
    `'币种': '人民币'` 写进**另一个分区** `s5_basic`（报价基本信息）；
  · 34 实测：把 `"币种":"人民币"` 手工塞进明细行 → HTTP 200；不塞 → HTTP 409。
  · 同一个键在导出侧也是硬的：`assertQuoteExportable()` 逐行读 `row['币种']`，
    「生成报价单(Word)」与「导入数据库」都先过它。
  → 按前端按钮走的用户永远走不出第 5 步，报价单也生不出来。

纪律：门禁用进程内 import 直调纯函数（不起服务、不发 HTTP）；页面侧用 `node -e` 抽具名函数体
执行（纯函数 + 两个依赖桩）。不连 PG / SQLite、不写业务数据。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CARD_HTML = ROOT / "确认需求解析结果.html"

gate = importlib.import_module("cpq_agent_server").quote_step_completion_gate

# 34 上 meta 下发的 s5_detail 列（14 列，没有「币种」）。
S5_DETAIL_COLUMNS = ["产品系列", "产品型号", "成品编码", "成品描述", "版本扩展", "方案描述",
                     "规格", "数量", "报价", "折扣", "折后价格", "总金额", "税率", "税金"]


def detail_row(**over):
    row = {"成品编码": "YT-RB-02001-A", "成品描述": "700ML 双开门酒盒", "数量": "1000",
           "报价": "27.37", "折扣": "1", "税率": "0.13", "折后价格": "27.37",
           "总金额": "27370"}
    row.update(over)
    return row


def step5(rows, *, basic_currency="人民币"):
    """页面 `collectStepData(5)` 的形状：`{分区: {标题, 数据}}`（`s5_basic` 是表单、`s5_detail` 是表）。"""
    basic = {"报价单号": "QUO202609001", "报价有效天数": "14", "币种": basic_currency,
             "报价联系人": "SM1", "申请日期": "2026-09-23"}
    return {"s5_basic": {"标题": "报价基本信息", "数据": basic},
            "s5_detail": {"标题": "报价明细", "数据": rows}}


# --------------------------------------------------------------------------- #
# 页面侧：`node -e` 抽 `assertQuoteExportable()` 的函数体执行（依赖 `quoteDetailRows()` /
# `collectStepData()` 两个桩；未识别的自由名一律解析成返回 null 的桩，避免 ReferenceError）。
# --------------------------------------------------------------------------- #
EXTRACT_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function decl(name) {
  const at = src.indexOf("function " + name + "(");
  if (at < 0) return null;
  const open = src.indexOf("{", at);
  if (open < 0) return null;
  let depth = 0;
  for (let j = open; j < src.length; j += 1) {
    const ch = src[j];
    if (ch === "{") depth += 1;
    else if (ch === "}") { depth -= 1; if (depth === 0) return src.slice(at, j + 1); }
  }
  return null;
}
const wanted = ["assertQuoteExportable", "_numOf"];
const env = { quoteDetailRows: () => ROWS, collectStepData: () => PAYLOAD };
let ROWS = [];
let PAYLOAD = {};
for (const name of wanted) {
  const text = decl(name);
  if (text === null) { console.log(JSON.stringify({ missing: name })); process.exit(0); }
  with (env) { env[name] = eval("(" + text + ")"); }
}
const scoped = new Proxy(env, {
  has: () => true,
  get: (t, k) => (k in t ? t[k] : (() => null))
});
const cases = JSON.parse(process.argv[3]);
const results = cases.map((c) => {
  ROWS = c.rows || [];
  PAYLOAD = c.payload || {};
  try {
    const out = scoped.assertQuoteExportable();
    return { ok: true, value: { ok: out.ok, problems: out.problems, rows: out.rows } };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
});
console.log(JSON.stringify({ results: results }));
"""


def export_check(rows, payload):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(CARD_HTML),
                           json.dumps([{"rows": rows, "payload": payload}], ensure_ascii=False)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % ((proc.stderr or proc.stdout) or "")[:800])
    payload_out = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload_out.get("missing"):
        raise AssertionError("卡片页缺少 %s()（Spec §2.3）" % payload_out["missing"])
    item = payload_out["results"][0]
    if not item.get("ok"):
        raise AssertionError("assertQuoteExportable() 抛异常：%s" % item.get("error"))
    return item["value"]


class Step5CurrencySourceTest(unittest.TestCase):
    # ---------------- A 组：币种必须能由「同一步报价基本信息」兜底（今天红） ----------------

    def test_a1_currency_from_same_step_basic_satisfies_gate(self):
        verdict = gate(5, step5([detail_row()]))
        self.assertTrue(verdict.get("ok"),
                        "明细行没有币种列、但同一步「报价基本信息」有币种（界面上第 5 步唯一的币种）时，"
                        "第 5 步必须可以完成；今天回的是 %s / %s"
                        % (verdict.get("code"), verdict.get("message")))

    def test_a2_row_level_wins_and_rest_fall_back_to_same_step(self):
        rows = [detail_row(成品编码="A", **{"币种": "美元"}), detail_row(成品编码="B"),
                detail_row(成品编码="C")]
        verdict = gate(5, step5(rows))
        self.assertTrue(verdict.get("ok"),
                        "3 行明细里只有 1 行自带币种，其余必须按同一步报价基本信息兜底；"
                        "今天回的是 %s / %s" % (verdict.get("code"), verdict.get("message")))

    def test_a3_export_hard_check_accepts_same_step_currency(self):
        payload = step5([detail_row()])
        got = export_check(payload["s5_detail"]["数据"], payload)
        self.assertTrue(got["ok"], "导出/导入前的硬校验必须与门禁同源（同上一步报价基本信息的币种）；"
                                   "今天的问题：%s" % (got["problems"],))

    # ---------------- B 组：护栏（今天就是绿的，不许被改红） ----------------

    def test_b1_row_level_currency_still_accepted(self):
        verdict = gate(5, step5([detail_row(**{"币种": "人民币"})]))
        self.assertTrue(verdict.get("ok"), "行里自带币种仍然放行")

    def test_b2_no_currency_anywhere_still_blocked(self):
        verdict = gate(5, step5([detail_row()], basic_currency=""))
        self.assertEqual(verdict.get("code"), "invalid_currency",
                         "行里和同一步报价基本信息里都没有币种时必须继续拦")
        self.assertEqual(verdict.get("message"), "第 1 行币种为空。", "文案逐字不变")
        self.assertEqual(verdict.get("action"), "填写币种（如 人民币）。", "修复入口文案逐字不变")

    def test_b3_quantity_unit_and_recompute_judgements_unchanged(self):
        bad_qty = gate(5, step5([detail_row(数量="0", **{"币种": "人民币"})]))
        self.assertEqual(bad_qty.get("code"), "invalid_quantity")
        self.assertEqual(bad_qty.get("message"), "第 1 行数量无效。")
        bad_unit = gate(5, step5([detail_row(报价="", **{"币种": "人民币"})]))
        self.assertEqual(bad_unit.get("code"), "invalid_unit_price")
        self.assertEqual(bad_unit.get("message"), "第 1 行单价（报价）无效。")
        bad_total = gate(5, step5([detail_row(总金额="1", **{"币种": "人民币"})]))
        self.assertEqual(bad_total.get("code"), "total_not_recomputable")
        self.assertTrue(bad_total.get("message", "").startswith("第 1 行总金额 "))

    def test_b4_other_steps_judgements_unchanged(self):
        step3 = gate(3, {"s3_products": {"标题": "产品信息", "数据": [{"基础成本": "20.53"}]}})
        self.assertTrue(step3.get("ok"), "第 3 步：正数基础成本即放行")
        step4 = gate(4, {"s4_products": {"标题": "产品信息",
                                         "数据": [{"价格": "27.37", "其他加价": "1.15"}]}})
        self.assertTrue(step4.get("ok"), "第 4 步：有加价依据即放行")
        rows = [detail_row(**{"币种": "人民币"})]
        step6 = gate(6, {"s5_detail": {"标题": "报价明细", "数据": rows}},
                     quote_fingerprint="qd1-1", detail_fingerprint="qd2-2")
        self.assertEqual(step6.get("code"), "detail_changed_after_step5_confirm",
                         "第 6 步：第 5 步确认后明细被改过仍然拦")

    def test_b5_export_hard_check_still_blocks_bad_rows(self):
        payload = step5([detail_row(数量="0", 报价="", 总金额="1")], basic_currency="")
        got = export_check(payload["s5_detail"]["数据"], payload)
        self.assertFalse(got["ok"], "数量/单价/复算/币种都不合格时仍然拦")
        joined = " ".join(got["problems"])
        self.assertIn("数量无效", joined)
        self.assertIn("单价（报价）无效", joined)
        self.assertIn("币种为空", joined)
        empty = export_check([], payload)
        self.assertFalse(empty["ok"], "空明细仍然拦")
        self.assertIn("报价明细为空", " ".join(empty["problems"]))


if __name__ == "__main__":
    unittest.main()
