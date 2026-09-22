"""红测：BOM 面板上必须看得见「配对复核 / 回填失败 / 业务清单换版」这三本账
（Spec `packaging-bom-disclosure-panel.md`）。

现状缺口（代码级，可指到行）：
  · `tech_app/frontend/app.js` 与 `tech_app/frontend/requirement-confirm.js` 里
    `pairing_review` / `pairing_review_unavailable` / `binding_error` / `business_parts_stale`
    **四个键 0 处引用**；
  · 后端 `packaging_bom.load_bom()` 早已把它们交到
    `GET …/requirement/packaging-bom` 的顶层（`:1391` / `:1394` / `:1396` / `:1404`）；
  · `pbPanel()` 只渲染了 `parts_binding_stale`（几何零件版本漂移），业务清单换版那一本没有；
  · 三家的空值语义（`{}` = 没有失败 / `[]` + `unavailable` 非空 = 读不到 / `[]` = 确实没有）
    在界面上**一处都没有区分**。

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

UNAVAILABLE_CODE = "pairing_review_unavailable"
BIND_UNAVAILABLE_CODE = "binding_error_unavailable"
UNKNOWN_LABEL = "原因未知（后端没有给出原因档）"
REIMPORTED_LABEL = "业务清单已重新导入（这一行还是上一版清单算的）"
WITHOUT_VERSION_LABEL = "这一行没记业务清单版本，判断不了是不是过期"

#: 真响应形状（`packaging_parts.bind_rows()` 的 `pairing_review` 逐字）。
PAIRING = [
    {"item_key": "item-1", "part_code": "DWG-P03", "row_material": "钕铁硼磁铁",
     "part_material": "灰板 2.0mm", "material_match": False},
    {"item_key": "item-2", "part_code": "DWG-P04", "row_material": "灰板 2.0mm",
     "part_material": "灰板 2.0mm", "material_match": True},
    {"item_key": "", "part_code": "DWG-P05", "row_material": "a", "part_material": "b",
     "material_match": False},
]
UNAVAILABLE = {"code": UNAVAILABLE_CODE, "reason": "doc_channel_unavailable",
               "message": "配对复核读不到（文档通道不可用：BomError）"}
BIND_ERROR = {"code": "part_binding_failed", "reason": "OSError", "at": "2026-09-22 11:00:00"}
BIND_UNAVAILABLE = {"code": BIND_UNAVAILABLE_CODE, "reason": "NameError",
                    "message": "回填失败留痕读不到（文档通道不可用：NameError）"}
STALE = [
    {"item_key": "item-1", "bound_business_parts_hash": "old",
     "current_business_parts_hash": "new", "reason": "business_parts_reimported"},
    {"item_key": "item-2", "bound_business_parts_hash": "",
     "current_business_parts_hash": "new", "reason": "binding_without_version"},
    {"item_key": "item-3", "reason": "something_new"},
    {"item_key": "", "reason": "business_parts_reimported"},
]

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
# A 组：不一致项清单（只收不一致、逐字透传、不造无名行）
# --------------------------------------------------------------------------- #
class APairingRows(unittest.TestCase):
    def test_a1_only_mismatches_are_listed(self):
        rows = value("pbPairingMismatchRows", [{"pairing_review": PAIRING}])
        self.assertEqual(["item-1"], [row["key"] for row in rows],
                         "`material_match === true` 的条目跳过（这一列的是不一致项）：%s"
                         % [row["key"] for row in rows])

    def test_a2_values_are_passed_through_verbatim(self):
        rows = value("pbPairingMismatchRows", [{"pairing_review": PAIRING}])
        self.assertEqual([{"key": "item-1", "part_code": "DWG-P03",
                           "row_material": "钕铁硼磁铁", "part_material": "灰板 2.0mm"}], rows,
                         "行键 / 几何件 / 两侧材料逐字透传（Spec §C1）")

    def test_a3_blank_item_key_is_skipped(self):
        rows = value("pbPairingMismatchRows",
                     [{"pairing_review": [PAIRING[2], {"item_key": "  ", "material_match": False},
                                          PAIRING[0]]}])
        self.assertEqual(["item-1"], [row["key"] for row in rows], "空行名跳过（Spec §C1）")

    def test_a4_non_array_or_non_object_is_empty(self):
        got = run_cases("pbPairingMismatchRows",
                        [[None], ["x"], [{}], [{"pairing_review": {}}], [{"pairing_review": "x"}]])
        for rows in got:
            self.assertEqual([], rows, "不是数组 → 空清单（Spec §C1）")

    def test_a5_broken_entries_do_not_crash(self):
        rows = value("pbPairingMismatchRows",
                     [{"pairing_review": [None, "x", 123, {"item_key": "item-9"}]}])
        self.assertEqual([{"key": "item-9", "part_code": "", "row_material": "",
                           "part_material": ""}], rows,
                         "缺字段给空串，不写字面量 undefined（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：配对复核那一名（读不到 ≠ 没有不一致）
# --------------------------------------------------------------------------- #
class BPairingNote(unittest.TestCase):
    def test_b1_nothing_to_say_is_an_empty_string(self):
        got = run_cases("pbPairingMismatchNote",
                        [[{}], [{"pairing_review": []}], [None],
                         [{"pairing_review": [PAIRING[1]]}]],
                        also=["pbPairingMismatchRows"])
        for text in got:
            self.assertEqual("", text, "确实没有不一致 → 一个字都不说（Spec §C1）")

    def test_b2_mismatch_count_is_stated(self):
        text = value("pbPairingMismatchNote", [{"pairing_review": PAIRING}],
                     also=["pbPairingMismatchRows"])
        self.assertEqual("配对复核：1 行材料与绑定的几何件不一致，请核对后再往下算。", text,
                         "行数逐字来自真数据（Spec §C1）")

    def test_b3_unavailable_uses_the_backend_code(self):
        text = value("pbPairingMismatchNote",
                     [{"pairing_review": [], "pairing_review_unavailable": UNAVAILABLE}],
                     also=["pbPairingMismatchRows"])
        self.assertIn(UNAVAILABLE_CODE, text, "读不到要说清是哪种读不到（Spec §C1）")
        self.assertTrue(text.startswith("配对复核读不到"), "句子形状按 Spec §C1：%s" % text)

    def test_b4_unavailable_is_not_reported_as_no_mismatch(self):
        text = value("pbPairingMismatchNote",
                     [{"pairing_review": [], "pairing_review_unavailable": UNAVAILABLE}],
                     also=["pbPairingMismatchRows"])
        self.assertNotIn("没有不一致", text.split("别当成")[-1].replace("「没有不一致」", ""),
                         "读不到不许被说成「没有不一致」（Spec §C1 / §4）")


# --------------------------------------------------------------------------- #
# C 组：回填失败那一名（{} = 没有失败；非空 = 有话说）
# --------------------------------------------------------------------------- #
class CBindingErrorNote(unittest.TestCase):
    def test_c1_no_failure_says_nothing(self):
        got = run_cases("pbBindingErrorNote", [[{}], [{"binding_error": {}}], [None],
                                               [{"binding_error": ""}]])
        for text in got:
            self.assertEqual("", text, "没有失败就不说（Spec §C1）")

    def test_c2_failure_names_code_and_reason(self):
        text = value("pbBindingErrorNote", [{"binding_error": BIND_ERROR}])
        self.assertIn("part_binding_failed", text, "稳定码逐字（Spec §C1）")
        self.assertIn("OSError", text, "原因逐字（Spec §C1）")

    def test_c3_unreadable_trace_is_not_no_failure(self):
        text = value("pbBindingErrorNote", [{"binding_error": BIND_UNAVAILABLE}])
        self.assertIn(BIND_UNAVAILABLE_CODE, text,
                      "连披露文档都读不到时不许显示成「没有失败」（Spec §C1 / §4）")

    def test_c4_blank_fields_do_not_produce_empty_parentheses(self):
        text = value("pbBindingErrorNote", [{"binding_error": {"code": "", "reason": ""}}])
        self.assertTrue(text, "有失败就得出话说（Spec §C1）")
        self.assertNotIn("（）", text, "缺字段时不许留空括号（Spec §C1）：%s" % text)


# --------------------------------------------------------------------------- #
# D 组：业务清单换版逐行（两档闭集 + 未知档）
# --------------------------------------------------------------------------- #
class DBusinessStaleRows(unittest.TestCase):
    def test_d1_two_known_reasons_are_verbatim(self):
        rows = value("pbBusinessStaleRows", [{"business_parts_stale": STALE}])
        self.assertEqual([REIMPORTED_LABEL, WITHOUT_VERSION_LABEL, UNKNOWN_LABEL],
                         [row["label"] for row in rows],
                         "两档闭集 + 未知档（Spec §C1）")

    def test_d2_item_key_is_passed_through_and_blank_skipped(self):
        rows = value("pbBusinessStaleRows", [{"business_parts_stale": STALE}])
        self.assertEqual(["item-1", "item-2", "item-3"], [row["key"] for row in rows],
                         "行键逐字，空行名跳过（Spec §C1）")

    def test_d3_unknown_reason_is_not_guessed(self):
        rows = value("pbBusinessStaleRows",
                     [{"business_parts_stale": [{"item_key": "i", "reason": "business_parts_reimported "},
                                                {"item_key": "j", "reason": "BUSINESS_PARTS_REIMPORTED"}]}])
        self.assertEqual([UNKNOWN_LABEL, UNKNOWN_LABEL], [row["label"] for row in rows],
                         "闭集外的档一律未知，不许归到已知两档（Spec §C1）")

    def test_d4_non_array_is_empty(self):
        got = run_cases("pbBusinessStaleRows",
                        [[None], ["x"], [{}], [{"business_parts_stale": {}}],
                         [{"business_parts_stale": "x"}]])
        for rows in got:
            self.assertEqual([], rows, "不是数组 → 空清单（Spec §C1）")

    def test_d5_empty_list_is_empty(self):
        self.assertEqual([], value("pbBusinessStaleRows", [{"business_parts_stale": []}]),
                         "确实没有换版行 → 空清单（Spec §C1）")


# --------------------------------------------------------------------------- #
# E 组：面板接线
# --------------------------------------------------------------------------- #
class EPanelWiring(unittest.TestCase):
    def setUp(self):
        self.body = function_body("pbPanel")

    def test_e1_pairing_review_block_is_rendered(self):
        self.assertIn('data-pb-pairing-review="', self.body, "配对复核块（Spec §C2）")
        self.assertIn('data-pb-pairing-mismatch="', self.body, "逐条不一致项（Spec §C2）")
        self.assertIn("pbPairingMismatchRows(record)", self.body, "行数来自真数据（Spec §C2）")

    def test_e2_pairing_unavailable_block_is_rendered(self):
        self.assertIn('data-pb-pairing-review-unavailable="', self.body,
                      "读不到要有落点（Spec §C2）")
        self.assertIn("pbPairingMismatchNote(record)", self.body, "句子取自纯函数（Spec §C2）")

    def test_e3_binding_error_block_is_rendered(self):
        self.assertIn('data-pb-binding-error="', self.body, "回填失败要有落点（Spec §C2）")
        self.assertIn("pbBindingErrorNote(record)", self.body, "句子取自纯函数（Spec §C2）")

    def test_e4_business_stale_block_is_rendered(self):
        self.assertIn('data-pb-business-stale="', self.body, "业务清单换版块（Spec §C2）")
        self.assertIn('data-pb-business-stale-key="', self.body, "逐行标出行键（Spec §C2）")
        self.assertIn("pbBusinessStaleRows(record)", self.body, "行数来自真数据（Spec §C2）")

    def test_e5_existing_banners_are_untouched(self):
        for marker in ('data-pb-mixed-box="', 'data-pb-box-unknown-total="',
                       'data-pb-bbox-total="', 'data-pb-parts-stale="',
                       'data-pb-parts-unavailable="'):
            self.assertIn(marker, self.body, "既有 banner 一字不动（Spec §C2）：%s" % marker)

    def test_e6_panel_adds_no_request(self):
        self.assertNotIn("fetch(", self.body, "面板只渲染，不新增接口（Spec §C3）")


# --------------------------------------------------------------------------- #
# F 组：冻结面
# --------------------------------------------------------------------------- #
class FFreeze(unittest.TestCase):
    def test_f1_pb_row_is_not_rewritten(self):
        body = function_body("pbRow")
        for forbidden in ("pairing_review", "binding_error", "business_parts_stale",
                          "data-pb-pairing", "data-pb-business-stale"):
            self.assertNotIn(forbidden, body,
                             "三本账不许塞进 pbRow()（Spec §C2 / §C3）：%s" % forbidden)

    def test_f2_frontend_does_not_re_judge_the_pairing(self):
        body = function_body("pbPairingMismatchRows")
        self.assertIn("material_match", body, "只认后端判过的那一列（Spec §C1 / §4）")
        for forbidden in ("row_material ===", "part_material ===", "toLowerCase"):
            self.assertNotIn(forbidden, body,
                             "不许在前端再判一次材料是否相等（Spec §4）：%s" % forbidden)

    def test_f3_helpers_hold_no_dom(self):
        for name in ("pbPairingMismatchRows", "pbPairingMismatchNote",
                     "pbBindingErrorNote", "pbBusinessStaleRows"):
            body = function_body(name)
            for forbidden in ("document", "window.", "sessionStorage", "localStorage",
                              "fetch(", "querySelector"):
                self.assertNotIn(forbidden, body,
                                 "%s() 必须是纯函数（Spec §C1）：不许出现 %s" % (name, forbidden))

    def test_f4_index_html_is_not_touched(self):
        self.assertNotIn("data-pb-pairing-mismatch",
                         (ROOT / "tech_app" / "frontend" / "index.html").read_text(encoding="utf-8"),
                         "不改 index.html（Spec §C3）")


# --------------------------------------------------------------------------- #
# G 组：语法护栏
# --------------------------------------------------------------------------- #
class GSyntax(unittest.TestCase):
    def test_g1_inline_script_still_parses(self):
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "requirement-confirm.js 仍须通过 node --check：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
