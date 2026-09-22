"""红测：留痕欠条必须在包装成本面板上**看得见、能补写**（Spec `packaging-handoff-audit-pending-panel.md`）。

现状缺口（代码级，可指到行）：
  · `tech_app/frontend/requirement-confirm.js:1039` 的 `pcSendQuote()` 拿到 `payload.handoff`
    后只读 `handoff_no` / `version_no` / `already_sent`，`handoff.audit`（`## 418` 的七键披露体）
    **从未被读** —— 留痕没落下时界面上一个字都没有；
  · 面板（`pcPanel()` `:961`）与 `pcRefresh()`（`:1002`）从不请求
    `…/requirement/packaging-quote/audit-pending`，"还欠几条"在界面上无处可取；
  · 前端源码里 `audit-pending/relay` 0 处引用 —— 补写没有入口。

纪律：`node -e` 抽顶层 / 闭包内具名函数体执行（纯函数）+ 源码守卫 + `node --check`；
不起服务、不发 HTTP、不连 PG / 34、不写业务数据。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIRM_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"

PENDING_PATH = "/requirement/packaging-quote/audit-pending"
RELAY_PATH = "/requirement/packaging-quote/audit-pending/relay"
UNAVAILABLE_CODE = "PACKAGING_HANDOFF_AUDIT_UNAVAILABLE"

RECORDED = {"attempted": True, "ok": False, "action": "workflow:packaging_handoff_sent",
            "code": UNAVAILABLE_CODE, "message": "OSError: disk full",
            "attempts": 2, "pending": "recorded"}
UNAVAILABLE = dict(RECORDED, pending="unavailable")
OLD_FIVE_KEY = {"attempted": True, "ok": False, "action": "workflow:packaging_handoff_sent",
                "code": UNAVAILABLE_CODE, "message": "OSError: disk full", "attempts": 2,
                "pending": ""}
HEALTHY = {"attempted": True, "ok": True, "action": "workflow:packaging_handoff_sent",
           "code": "", "message": "", "attempts": 1, "pending": ""}

PENDING_DOC = {"count": 2, "items": [
    {"pending_id": "abc123def4567890", "code": UNAVAILABLE_CODE, "message": "OSError: disk full",
     "recorded_at": "2026-09-22 12:00:00",
     "payload": {"requirement_no": "REQ-A", "version_no": 3, "handoff_no": "h-1"}},
    {"pending_id": "9876543210fedcba", "code": UNAVAILABLE_CODE, "message": "OSError: disk full",
     "recorded_at": "2026-09-22 12:05:00",
     "payload": {"requirement_no": "REQ-B", "version_no": 2, "handoff_no": "h-2"}}]}

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
/* 被抽的函数若复用了同文件的另一个纯函数，一起 eval（Spec §C1：只在源码里出现一次，
   不为了"能被单独抽"把规则抄第二遍）。 */
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
    """`cases` 是**单入参**用例表（每项一个实参）；`also` 是被抽函数依赖的同文件纯函数。"""
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(CONFIRM_JS), name,
                           json.dumps([[item] for item in cases]), json.dumps(list(also))],
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
# A 组：告警句（四态互斥、不编次数、不冒充状态）
# --------------------------------------------------------------------------- #
class AAuditWarning(unittest.TestCase):
    def test_a1_nothing_to_say_is_an_empty_string(self):
        for argument in (None, {}, [], "x", 3, {"ok": True, "attempts": 1}):
            out = run_cases("pcAuditWarning", [argument])[0]
            self.assertEqual("", out,
                             "没有披露体 / 留痕落下了 → 空串（Spec §C1）：%r" % (argument,))

    def test_a2_recorded_state(self):
        out = run_cases("pcAuditWarning", [RECORDED])[0]
        self.assertTrue(out, "留痕没落下必须给一句告警（Spec §C1）")
        self.assertIn("留痕没落下", out, "必须说清是留痕没落下（Spec §C1）")
        self.assertIn("试了 2 次", out, "attempts 必须说出来（Spec §C1）")
        self.assertIn("OSError: disk full", out, "message 逐字进界面（Spec §C1/§4）")
        self.assertIn("已记成待补写", out, "pending=recorded 必须说记成待补写了（Spec §C1）")

    def test_a3_unavailable_state_is_not_faked(self):
        out = run_cases("pcAuditWarning", [UNAVAILABLE])[0]
        self.assertIn("留痕没落下", out)
        self.assertIn("也没能记下", out, "pending=unavailable 必须如实说（Spec §C1）")
        self.assertNotIn("已记成待补写", out, "记不成待补写时不许假装记上了")

    def test_a4_old_five_key_disclosure_does_not_claim_the_new_states(self):
        out = run_cases("pcAuditWarning", [OLD_FIVE_KEY])[0]
        self.assertIn("留痕没落下", out)
        self.assertIn("试了 2 次", out)
        self.assertNotIn("已记成待补写", out, "pending 为空不许冒充 recorded（Spec §C1）")
        self.assertNotIn("也没能记下", out, "pending 为空不许冒充 unavailable（Spec §C1）")

    def test_a5_missing_attempts_is_not_invented(self):
        audit = dict(RECORDED)
        audit.pop("attempts")
        out = run_cases("pcAuditWarning", [audit])[0]
        self.assertNotIn("试了", out, "没有 attempts 就不许编一个次数（Spec §C1）")
        audit["attempts"] = "x"
        self.assertNotIn("试了", run_cases("pcAuditWarning", [audit])[0],
                         "attempts 不是数时也不许编（Spec §C1）")

    def test_a6_empty_message_does_not_leak_undefined(self):
        out = run_cases("pcAuditWarning", [{"ok": False, "attempts": 2, "pending": "recorded",
                                            "message": ""}])[0]
        self.assertNotIn("undefined", out, "空 message 不许漏成 undefined（Spec §C1）")
        self.assertNotIn("null", out, "空 message 不许漏成 null（Spec §C1）")
        self.assertIn("留痕没落下", out)


# --------------------------------------------------------------------------- #
# B 组：欠条行 / 标题 / 补写文案
# --------------------------------------------------------------------------- #
class BPendingRows(unittest.TestCase):
    def test_b1_rows_from_items_only(self):
        rows = run_cases("pcPendingRows", [PENDING_DOC])[0]
        self.assertEqual(2, len(rows), "行数只由 items 决定（Spec §C1）")
        self.assertEqual(["abc123def4567890", "9876543210fedcba"],
                         [row.get("id") for row in rows], "id 取 pending_id（Spec §C1）")
        self.assertIn("REQ-A", rows[0].get("text") or "", "text 含需求单号（Spec §C1）")
        self.assertIn("第 3 版", rows[0].get("text") or "", "text 含有版本（Spec §C1）")
        self.assertIn(UNAVAILABLE_CODE, rows[0].get("text") or "", "text 含稳定码（Spec §C1）")
        self.assertIn("2026-09-22 12:00:00", rows[0].get("when") or "",
                      "when 取 recorded_at（Spec §C1）")

    def test_b2_rows_never_invented_from_count(self):
        for argument in (None, {}, {"count": 5}, {"count": 5, "items": None},
                         {"count": 5, "items": []}, {"items": ["x", None]}):
            rows = run_cases("pcPendingRows", [argument])[0]
            self.assertEqual([], rows,
                             "没有真行时不许拿 count 造假行（Spec §C1）：%r" % (argument,))

    def test_b3_rows_survive_a_missing_payload(self):
        rows = run_cases("pcPendingRows", [{"items": [{"pending_id": "p1"}]}])[0]
        self.assertEqual(1, len(rows), "缺 payload 也要出一行（Spec §C1）")
        for bad in ("undefined", "null"):
            self.assertNotIn(bad, rows[0].get("text") or "",
                             "缺字段不许漏成 %s（Spec §C1）" % bad)

    def test_b4_headline(self):
        self.assertEqual("", run_cases("pcPendingHeadline", [None],
                                       also=("pcPendingRows",))[0],
                         "读不到 → 空串（Spec §C1）")
        self.assertEqual("", run_cases("pcPendingHeadline", [{"items": [], "count": 0}],
                                       also=("pcPendingRows",))[0],
                         "不欠 → 空串（Spec §C1）")
        head = run_cases("pcPendingHeadline", [PENDING_DOC], also=("pcPendingRows",))[0]
        self.assertIn("还欠 2 条留痕", head, "标题要说清还欠几条（Spec §C1）")

    def test_b5_relay_text(self):
        self.assertEqual("", run_cases("pcRelayText", [None])[0],
                         "取不到结果 → 空串（Spec §C1）")
        done = run_cases("pcRelayText", [{"attempted": 2, "relayed": 2, "remaining": 0,
                                          "code": "", "message": ""}])[0]
        self.assertIn("补上 2 条", done)
        self.assertIn("还欠 0 条", done)
        self.assertNotIn("——", done, "全成功时不许带失败尾巴（Spec §C1）")
        partial = run_cases("pcRelayText", [{"attempted": 2, "relayed": 1, "remaining": 1,
                                             "code": UNAVAILABLE_CODE,
                                             "message": "OSError: disk full"}])[0]
        self.assertIn("补上 1 条", partial)
        self.assertIn("还欠 1 条", partial)
        self.assertIn("OSError: disk full", partial, "失败原因逐字带出来（Spec §C1/§4）")


# --------------------------------------------------------------------------- #
# C 组：接线（面板块、读欠条、绑按钮、回传 toast）
# --------------------------------------------------------------------------- #
class CWiring(unittest.TestCase):
    def test_c1_paths_are_literals(self):
        self.assertIn(PENDING_PATH, SOURCE, "缺读欠条路径字面量（Spec §C4）")
        self.assertIn(RELAY_PATH, SOURCE, "缺补写路径字面量（Spec §C4）")

    def test_c2_panel_renders_the_audit_block(self):
        body = function_body("pcPanel")
        self.assertIn("pcAuditBlock(handoff, pending, writable)", body,
                      "面板要渲染留痕块并收下欠条（Spec §C2）")
        self.assertIn("pending", body, "面板要收下欠条（Spec §C2）")
        block = function_body("pcAuditBlock")
        self.assertIn("data-pc-audit=", block, "留痕块的节点标记（Spec §C2）")
        self.assertIn("data-pc-audit-id=", block, "逐条标记 pending_id（Spec §C2）")
        self.assertIn("data-pc-audit-relay", block, "留痕块要有补写按钮（Spec §C2）")
        self.assertIn("writable", block, "按钮要看得到写权限（Spec §C2）")
        self.assertIn("pcAuditWarning", block, "留痕块要用告警函数（Spec §C2）")
        self.assertIn("pcPendingRows", block, "留痕块要列欠条行（Spec §C2）")

    def test_c3_refresh_reads_pending_and_null_on_failure(self):
        body = function_body("pcRefresh")
        self.assertIn("pcAuditPendingPath", body, "刷新时必须读欠条（Spec §C2）")
        self.assertIn("pending", body, "读到的欠条要交给面板（Spec §C2）")
        self.assertIn("pending = null", body,
                      "读不到时必须置 null（读不到≠不欠，Spec §C2/§4）")
        self.assertIn("pcPanel(", body)

    def test_c4_bind_wires_the_relay_button(self):
        body = function_body("pcBind")
        self.assertIn("data-pc-audit-relay", body, "补写按钮必须被绑上（Spec §C2）")
        self.assertIn("pcRelayAudits", body, "点击要走补写入口（Spec §C2）")

    def test_c5_relay_posts_to_the_relay_path(self):
        body = function_body("pcRelayAudits")
        self.assertIn("pcRelayAuditsPath", body, "补写要走 relay 路径（Spec §C4）")
        self.assertIn("POST", body, "补写必须是 POST（Spec §C4）")
        self.assertIn("pcRelayText", body, "结果要用补写文案（Spec §C4）")
        self.assertIn("pcRefresh", body, "补完要刷新（Spec §C4）")

    def test_c6_send_toast_carries_the_warning(self):
        body = function_body("pcSendQuote")
        self.assertIn("pcAuditWarning(handoff.audit)", body,
                      "回传成功后必须把留痕告警带进 toast（Spec §C3）")

    def test_c7_paths_encode_project_id(self):
        body = function_body("pcAuditPendingPath")
        self.assertIn("encodeURIComponent", body, "pid 必须被编码（Spec §C4）")
        body = function_body("pcRelayAuditsPath")
        self.assertIn("encodeURIComponent", body, "pid 必须被编码（Spec §C4）")


# --------------------------------------------------------------------------- #
# D 组：护栏（现状即绿）—— 回传恢复口径、出口、读取方式、语法
# --------------------------------------------------------------------------- #
class DGuardrails(unittest.TestCase):
    def test_d1_send_recovery_口径_unchanged(self):
        body = function_body("pcSendQuote")
        for token in ("cost_gaps_unresolved", "gap_reason_required", "no_candidate",
                      "multiple_candidates", "body.allow_gaps", "body.create_new"):
            self.assertIn(token, body, "回传的三类恢复口径一字不动（Spec §C5）：%s" % token)
        self.assertIn("pcPackagingSendPath", body, "回传仍走既有路径函数（Spec §C5）")

    def test_d2_panel_export_unchanged(self):
        for token in ("window.CfPackagingCostPanel", "pcMaybeMount", "pcRefresh"):
            self.assertIn(token, SOURCE, "面板既有出口不变（Spec §C5）：%s" % token)

    def test_d3_pure_functions_have_no_dom_or_fetch(self):
        for name in ("pcAuditWarning", "pcPendingRows", "pcPendingHeadline", "pcRelayText"):
            body = function_body(name)
            for banned in ("document.", "window.", "fetch(", "localStorage", "sessionStorage"):
                self.assertNotIn(banned, body,
                                 "%s() 必须是纯函数（Spec §C1/§4）：%s" % (name, banned))

    def test_d4_no_auto_relay_and_no_polling(self):
        for token in ("setInterval", "setTimeout(() => pcRelayAudits"):
            self.assertNotIn(token, SOURCE, "不许自动补写 / 轮询（Spec §4）")

    def test_d5_read_route_is_a_plain_get(self):
        body = function_body("pcRefresh")
        call = re.search(r"pcApi\(pcAuditPendingPath\(pid\)([^)]*)\)", body)
        self.assertIsNotNone(call, "读欠条必须走 pcApi(pcAuditPendingPath(pid))（Spec §C2）")
        self.assertNotIn("POST", call.group(1) or "",
                         "读欠条是 GET，不许带 method（Spec §C2）")

    def test_d6_node_check_passes(self):
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "requirement-confirm.js 必须仍能过 `node --check`：%s"
                         % (proc.stderr or proc.stdout)[:600])


if __name__ == "__main__":
    unittest.main()
