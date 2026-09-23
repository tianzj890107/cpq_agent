"""红测：服务端自己写进第 5 步的那份报价明细，必须能被第 5/6 步的完成门禁判成「有明细」（Spec §2/§4）。

Spec：`docs/specs/packaging-quote-step5-reported-detail-must-satisfy-step-gate.md`

现状缺口（2026-09-23 在 34 实测 + 本机读源码，不是推断）：
  · 34 上 `GET /wf/card/step-data?session_id=1bef04f7dab3&step_no=5` 给的第 5 步明细是
    `{"kind":"summary","title":"报价明细","rows":[{项目,值,来源} × 13]}`（定价引擎算出来的那份，
    肉眼可见 13 行）；
  · 门禁只认 `{"标题":…,"数据":[行…]}` 这种表形状（`_gate_rows()` 只读 `数据`/`data` 是数组的那种），
    于是这份快照被实跑判成 409 `no_detail_rows`「报价明细为空：至少要有 1 行。」，
    同一份判定 `fixable_by_fill:false` → 「强行填满本步骤」被当场拒绝；
  · 第 6 步拿同一份快照同样 409；
  · 页面 `wfRestoreStepData()` 对没有 `数据`/`data` 的分区快照直接 `return` —— 汇总分区整段丢掉。

纪律：门禁用进程内 import 直调纯函数（不起服务、不发 HTTP）；卡片侧只读源码做形状守卫。
不连 PG / SQLite、不写业务数据。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CARD_HTML = ROOT / "确认需求解析结果.html"

gate = importlib.import_module("cpq_agent_server").quote_step_completion_gate

# 34 上真实返回的第 5 步快照（逐字抄自 `GET /wf/card/step-data`，2026-09-23）。
STEP5_SNAPSHOT = {
    "s5_basic": {
        "kind": "summary", "title": "报价基本信息",
        "fields": [
            {"key": "quote_session_id", "label": "报价会话", "value": "1bef04f7dab3"},
            {"key": "business_case_id", "label": "业务实例号", "value": ""},
            {"key": "requirement_no", "label": "需求单号", "value": "REQ-8131F6D29D99"},
            {"key": "box_type_code", "label": "盒型", "value": "YT-RB-02001-A"},
            {"key": "quote_quantity", "label": "报价数量", "value": 1000.0},
        ],
    },
    "s5_detail": {
        "kind": "summary", "title": "报价明细",
        "rows": [
            {"项目": "成本总额", "值": "20.53", "来源": "定价引擎"},
            {"项目": "毛利后单价", "值": "27.37", "来源": "定价引擎"},
            {"项目": "加价合计", "值": "1.15", "来源": "定价引擎"},
            {"项目": "加价后单价", "值": "28.52", "来源": "定价引擎"},
            {"项目": "折扣金额", "值": "1.43", "来源": "定价引擎"},
            {"项目": "未税单价", "值": "27.10", "来源": "定价引擎"},
            {"项目": "税金", "值": "3.52", "来源": "定价引擎"},
            {"项目": "含税单价", "值": "30.62", "来源": "定价引擎"},
            {"项目": "未税单价（定价）", "值": "27.37", "来源": "定价引擎"},
            {"项目": "未税总额", "值": "27095.08", "来源": "定价引擎"},
            {"项目": "含税总额", "值": "30617.45", "来源": "定价引擎"},
            {"项目": "税金", "值": "3.52", "公式": "未税单价 × 税率"},
            {"项目": "税率", "值": "0.13", "来源": "Spec §2.4"},
        ],
    },
}

SUMMARY_ROWS = STEP5_SNAPSHOT["s5_detail"]["rows"]


def detail_row(**over):
    row = {"成品编码": "YT-RB-02001-A", "数量": "1000", "报价": "27.37", "折扣": "1",
           "税率": "0.13", "折后价格": "27.37", "总金额": "27370"}
    row.update(over)
    return row


def _restore_body() -> str:
    html = CARD_HTML.read_text(encoding="utf-8")
    match = re.search(r"async function wfRestoreStepData\(step\)\s*\{(?P<body>.*?)\n    \}", html, re.S)
    assert match, "卡片页必须仍有 wfRestoreStepData()（Spec §2.5）"
    return match.group("body")


SUMMARY_TOKENS = ("payload.rows", "payload['rows']", 'payload["rows"]', "'summary'", '"summary"')


class Step5ReportedDetailTest(unittest.TestCase):
    # ---------------- A 组（今天都是红的） ----------------

    def test_a1_card_step5_snapshot_counts_as_detail(self):
        verdict = gate(5, json.loads(json.dumps(STEP5_SNAPSHOT, ensure_ascii=False)))
        self.assertTrue(verdict.get("ok"),
                        "服务端自己写进第 5 步的那 13 行报价明细必须算「有明细」；"
                        "今天回的是 %s / %s" % (verdict.get("code"), verdict.get("message")))

    def test_a2_empty_detail_is_fixable_by_the_step5_entry(self):
        verdict = gate(5, {})
        self.assertEqual(verdict.get("code"), "no_detail_rows", "明细真的没有时仍然拦")
        self.assertTrue(verdict.get("fixable_by_fill"),
                        "第 5 步自己有入口（「报价方案」按钮就能把明细生出来），"
                        "不许报 fixable_by_fill:false 把「强行填满本步骤」当场拒掉")

    def test_a3_empty_detail_action_points_at_step5_entry(self):
        verdict = gate(5, {})
        action = str(verdict.get("action") or "")
        self.assertTrue(("报价方案" in action) or ("重算明细" in action),
                        "修复入口必须指向第 5 步自己的入口；今天给的是：%r" % (action,))

    def test_a4_step6_snapshot_counts_as_detail(self):
        verdict = gate(6, json.loads(json.dumps(STEP5_SNAPSHOT, ensure_ascii=False)),
                       quote_fingerprint="qd111-13", detail_fingerprint="qd111-13")
        self.assertTrue(verdict.get("ok"),
                        "第 6 步不许把同一份快照判成「报价明细为空」；今天回的是 %s / %s"
                        % (verdict.get("code"), verdict.get("message")))

    def test_a5_summary_rows_inside_a_table_envelope_are_not_checked_as_detail_rows(self):
        verdict = gate(5, {"s5_detail": {"标题": "报价明细",
                                         "数据": json.loads(json.dumps(SUMMARY_ROWS, ensure_ascii=False))}})
        self.assertTrue(verdict.get("ok"),
                        "汇总行不是明细表行，不许套用「数量/单价/复算」逐行判据；"
                        "今天回的是 %s / %s" % (verdict.get("code"), verdict.get("message")))

    def test_a6_card_restore_keeps_rows_carrying_snapshot_sections(self):
        body = _restore_body()
        self.assertTrue(any(token in body for token in SUMMARY_TOKENS),
                        "wfRestoreStepData() 必须认得带 rows 的汇总形状分区快照"
                        "（`kind:'summary'`），不许整段丢掉 —— 第 5 步报价明细/报价基本信息"
                        "就是这种形状（Spec §2.5）")

    # ---------------- B 组：护栏（今天就是绿的，不许被改红） ----------------

    def test_b1_table_envelope_without_rows_is_still_empty(self):
        verdict = gate(5, {"s5_detail": {"标题": "报价明细", "数据": []}})
        self.assertEqual(verdict.get("code"), "no_detail_rows", "空表仍然算没有明细")

    def test_b2_detail_table_with_row_currency_still_ok(self):
        verdict = gate(5, {"s5_detail": {"标题": "报价明细",
                                         "数据": [detail_row(**{"币种": "人民币"})]}})
        self.assertTrue(verdict.get("ok"), "表形状 + 行级币种仍然放行")

    def test_b3_detail_table_without_any_currency_still_blocked(self):
        verdict = gate(5, {"s5_detail": {"标题": "报价明细", "数据": [detail_row()]}})
        self.assertEqual(verdict.get("code"), "invalid_currency",
                         "表形状又没有币种时仍然拦（币种口径见同批另一份 Spec）")
        self.assertEqual(verdict.get("message"), "第 1 行币种为空。")

    def test_b4_step6_fingerprint_judgement_unchanged(self):
        verdict = gate(6, {"s5_detail": {"标题": "报价明细",
                                         "数据": [detail_row(**{"币种": "人民币"})]}},
                       quote_fingerprint="qda-1", detail_fingerprint="qdb-2")
        self.assertEqual(verdict.get("code"), "detail_changed_after_step5_confirm",
                         "第 5 步确认后明细被改过仍然拦")

    def test_b5_summary_rows_with_empty_item_are_not_detail(self):
        verdict = gate(5, {"s5_detail": {"kind": "summary", "title": "报价明细",
                                         "rows": [{"项目": "", "值": "20.53"}]}})
        self.assertFalse(verdict.get("ok"), "汇总行「项目」为空不算明细")

    def test_b6_other_steps_judgements_unchanged(self):
        step3 = gate(3, {"s3_products": {"标题": "产品信息", "数据": [{"基础成本": "20.53"}]}})
        self.assertTrue(step3.get("ok"))
        step4 = gate(4, {"s4_products": {"标题": "产品信息",
                                         "数据": [{"价格": "27.37", "加价依据": "Spec §2.4"}]}})
        self.assertTrue(step4.get("ok"))
        empty3 = gate(3, {})
        self.assertEqual(empty3.get("code"), "no_product_rows")


if __name__ == "__main__":
    unittest.main()
