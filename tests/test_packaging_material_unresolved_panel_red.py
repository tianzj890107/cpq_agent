"""红测：BOM 面板上「解析不到材料码」必须**逐行给原因 + 逐行给补齐办法**，
并且映射表读不到时要用后端 message 显形（Spec `packaging-material-unresolved-panel.md`）。

现状缺口（代码级，可指到行）：
  · `tech_app/frontend/requirement-confirm.js:443-444` 的 `pbPanel()` 只把
    `gaps.material_unresolved` 拼成一句 `解析不到材料码（已在库外）：…`——
    "已在库外"是**猜的**（后端从没这么说过），而且没有动作；
  · 同一份响应里后端已经给了 `gaps.material_unresolved_detail`（逐条 `reason` + `action`）
    与 `business_material_rows`（`map_hit_total` / `map_source` / `map_fingerprint` /
    `map_unavailable`），前端**一个都没用**。

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

UNKNOWN_LABEL = "原因未知（后端没有给出原因档）"
KEY_MISSING_LABEL = "映射表里还没有这条原文"
NOT_APPLIED_LABEL = "映射表里写了，但这一版没生效"
MAP_UNKNOWN_LABEL = "映射表读不到，这次没能判定"

MAP_KEY_MISSING_ACTION = ("在 tech_app/agent_knowledge/rules/packaging_material_code_map.json 的 "
                          "entries 里补一条「这条材料原文 → 材料清单里的材料码」，然后重算 BOM")

# 真 BOM 响应的形状（`## 421` `packaging_bom.load_bom()` → `gaps` + `business_material_rows`）。
GAPS_DETAIL = {
    "material_unresolved": ["M-01", "M-02"],
    "material_unresolved_detail": [
        {"item_key": "M-01", "reason": "map_key_missing", "action": MAP_KEY_MISSING_ACTION},
        {"item_key": "M-02", "reason": "map_entry_not_applied", "action": ""}],
}
GAPS_OLD = {"material_unresolved": ["M-01", "M-02"]}
SCOPE_UNAVAILABLE = {
    "map_hit_total": 0, "map_source": "packaging_material_code_map.json",
    "map_fingerprint": "sha256:deadbeef",
    "map_unavailable": {"code": "PACKAGING_MATERIAL_MAP_UNAVAILABLE",
                        "message": "材料原文映射表读不到：No such file or directory"},
}
SCOPE_HIT = {"map_hit_total": 3, "map_source": "packaging_material_code_map.json",
             "map_fingerprint": "sha256:deadbeef", "map_unavailable": {}}

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
    """单次调用：`args` 是实参表。"""
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
# A 组：原因档 → 人话（闭集认三档，其余一律"原因未知"）
# --------------------------------------------------------------------------- #
class AMaterialReasonLabel(unittest.TestCase):
    def test_a1_three_known_reasons_are_verbatim(self):
        got = run_cases("pbMaterialReasonLabel",
                        [["map_key_missing"], ["map_entry_not_applied"], ["map_unknown"]])
        self.assertEqual([KEY_MISSING_LABEL, NOT_APPLIED_LABEL, MAP_UNKNOWN_LABEL], got,
                         "三档原因各自的人话（Spec §C1）")

    def test_a2_unknown_reason_is_not_guessed(self):
        got = run_cases("pbMaterialReasonLabel",
                        [["something_new"], ["map_hit"], ["legacy_hit"],
                         ["MAP_KEY_MISSING"], ["map_key_missing "]])
        for index, text in enumerate(got):
            self.assertEqual(UNKNOWN_LABEL, text,
                             "闭集外的档一律回未知档，不许归到已知三档（Spec §C1）")

    def test_a3_non_string_is_unknown(self):
        got = run_cases("pbMaterialReasonLabel", [[None], [""], [123], [True], [[]], [{}]])
        for text in got:
            self.assertEqual(UNKNOWN_LABEL, text, "空 / 非字符串一律未知档（Spec §C1）")

    def test_a4_unknown_is_not_one_of_the_known_labels(self):
        got = value("pbMaterialReasonLabel", ["nope"])
        self.assertNotIn(got, [KEY_MISSING_LABEL, NOT_APPLIED_LABEL, MAP_UNKNOWN_LABEL],
                         "未知档不许被说成已知三档（三档互斥是后端口径）")


# --------------------------------------------------------------------------- #
# B 组：逐行清单（detail 优先，空项跳过，退回不编原因）
# --------------------------------------------------------------------------- #
class BMaterialUnresolvedRows(unittest.TestCase):
    def test_b1_detail_drives_key_label_action(self):
        rows = value("pbMaterialUnresolvedRows", [GAPS_DETAIL], also=["pbMaterialReasonLabel"])
        self.assertEqual(["M-01", "M-02"], [row["key"] for row in rows], "行名取自 item_key（Spec §C1）")
        self.assertEqual([KEY_MISSING_LABEL, NOT_APPLIED_LABEL], [row["label"] for row in rows],
                         "每行原因档 → 人话（Spec §C1）")
        self.assertEqual([MAP_KEY_MISSING_ACTION, ""], [row["action"] for row in rows],
                         "动作逐字来自后端，空就留空（Spec §C1 / §C2）")

    def test_b2_empty_item_key_is_skipped(self):
        gaps = {"material_unresolved": ["M-01", "M-02"],
                "material_unresolved_detail": [
                    {"item_key": "", "reason": "map_key_missing", "action": MAP_KEY_MISSING_ACTION},
                    {"item_key": "  ", "reason": "map_unknown", "action": ""},
                    {"item_key": "M-02", "reason": "map_key_missing", "action": MAP_KEY_MISSING_ACTION}]}
        rows = value("pbMaterialUnresolvedRows", [gaps], also=["pbMaterialReasonLabel"])
        self.assertEqual(["M-02"], [row["key"] for row in rows], "空行名跳过，不许造无名行（Spec §C1）")

    def test_b3_without_detail_falls_back_to_the_key_list(self):
        rows = value("pbMaterialUnresolvedRows", [GAPS_OLD], also=["pbMaterialReasonLabel"])
        self.assertEqual(["M-01", "M-02"], [row["key"] for row in rows], "退回旧清单（Spec §C1）")
        self.assertEqual([UNKNOWN_LABEL, UNKNOWN_LABEL], [row["label"] for row in rows],
                         "退回不等于编原因 —— 一律未知档（Spec §C1）")
        self.assertEqual(["", ""], [row["action"] for row in rows], "退回时没有动作可给（Spec §C1）")

    def test_b4_non_object_gaps_is_empty(self):
        got = run_cases("pbMaterialUnresolvedRows", [[None], ["x"], [123], [[]], [True]],
                        also=["pbMaterialReasonLabel"])
        for rows in got:
            self.assertEqual([], rows, "gaps 不是对象 → 空清单（Spec §C1）")

    def test_b5_neither_key_is_an_array_is_empty(self):
        got = run_cases("pbMaterialUnresolvedRows",
                        [[{}], [{"material_unresolved": "M-01"}],
                         [{"material_unresolved_detail": {"item_key": "M-01"}}]],
                        also=["pbMaterialReasonLabel"])
        for rows in got:
            self.assertEqual([], rows, "两键都不是数组 → 空清单（Spec §C1）")

    def test_b6_row_count_comes_only_from_real_rows(self):
        gaps = {"material_unresolved": ["M-01", "M-02", "M-03"], "total": 9, "count": 7,
                "material_unresolved_detail": [
                    {"item_key": "M-01", "reason": "map_key_missing", "action": "a"}]}
        rows = value("pbMaterialUnresolvedRows", [gaps], also=["pbMaterialReasonLabel"])
        self.assertEqual(1, len(rows), "行数只由真数据决定，不许拿计数键凑行（Spec §C1）")

    def test_b7_action_is_trimmed(self):
        gaps = {"material_unresolved": [],
                "material_unresolved_detail": [
                    {"item_key": "M-01", "reason": "map_unknown", "action": "  改映射表  "}]}
        rows = value("pbMaterialUnresolvedRows", [gaps], also=["pbMaterialReasonLabel"])
        self.assertEqual("改映射表", rows[0]["action"], "动作 trim 后逐字（Spec §C1）")


# --------------------------------------------------------------------------- #
# C 组：一句提示（行数来自真数据；全文不许出现"库外"）
# --------------------------------------------------------------------------- #
class CMaterialUnresolvedHint(unittest.TestCase):
    def test_c1_no_rows_no_hint(self):
        got = run_cases("pbMaterialUnresolvedHint", [[{}], [{"material_unresolved": []}], [None]],
                        also=["pbMaterialUnresolvedRows", "pbMaterialReasonLabel"])
        for text in got:
            self.assertEqual("", text, "没有行就别说（Spec §C1）")

    def test_c2_hint_states_the_row_count(self):
        text = value("pbMaterialUnresolvedHint", [GAPS_DETAIL],
                     also=["pbMaterialUnresolvedRows", "pbMaterialReasonLabel"])
        self.assertEqual("解析不到材料码 2 行：下面逐条给出原因与补齐办法。", text,
                         "行数逐字来自真数据（Spec §C1）")

    def test_c3_hint_never_says_out_of_library(self):
        for gaps in (GAPS_DETAIL, GAPS_OLD, SCOPE_UNAVAILABLE,
                     {"material_unresolved": ["M-01"]}):
            text = value("pbMaterialUnresolvedHint", [gaps],
                         also=["pbMaterialUnresolvedRows", "pbMaterialReasonLabel"])
            self.assertNotIn("库外", text, "这句是猜的，Spec §4 明令不许再写（Spec §C1）")


# --------------------------------------------------------------------------- #
# D 组：映射表备注（读不到用后端 message 逐字；不泄漏出处细节）
# --------------------------------------------------------------------------- #
class DMaterialMapNote(unittest.TestCase):
    def test_d1_unavailable_uses_backend_message_verbatim(self):
        text = value("pbMaterialMapNote", [SCOPE_UNAVAILABLE])
        self.assertIn(SCOPE_UNAVAILABLE["map_unavailable"]["message"], text,
                      "读不到映射表要用后端 message 逐字给用户（Spec §C1）")

    def test_d2_unavailable_never_says_out_of_library(self):
        text = value("pbMaterialMapNote", [SCOPE_UNAVAILABLE])
        self.assertNotIn("库外", text, "Spec §4 明令不许再写（Spec §C1）")

    def test_d3_hit_total_is_reported(self):
        text = value("pbMaterialMapNote", [SCOPE_HIT])
        self.assertIn("材料码映射表命中 3 行", text, "命中几行要单独说（Spec §C1）")

    def test_d4_nothing_to_say_is_an_empty_string(self):
        got = run_cases("pbMaterialMapNote",
                        [[{"map_hit_total": 0, "map_unavailable": {}}], [{}], [None], ["x"]])
        for text in got:
            self.assertEqual("", text, "没话说就别说（Spec §C1）")

    def test_d5_source_and_fingerprint_are_not_shown(self):
        text = value("pbMaterialMapNote", [SCOPE_HIT])
        self.assertNotIn(SCOPE_HIT["map_fingerprint"], text, "指纹本批不上界面（Spec §6.2）")
        self.assertNotIn(SCOPE_HIT["map_source"], text, "出处本批不上界面（Spec §6.2）")


# --------------------------------------------------------------------------- #
# E 组：面板接线
# --------------------------------------------------------------------------- #
class EPanelWiring(unittest.TestCase):
    def setUp(self):
        self.body = function_body("pbPanel")

    def test_e1_block_carries_the_row_count(self):
        self.assertIn('data-pb-material-unresolved="', self.body,
                      "替换成带行数的块（Spec §C2）")
        self.assertIn("pbMaterialUnresolvedRows(gaps)", self.body,
                      "行数必须来自逐行清单（Spec §C1）")

    def test_e2_rows_carry_item_key(self):
        self.assertIn('data-pb-material-unresolved-key="', self.body,
                      "逐行标出 item_key（Spec §C2）")

    def test_e3_map_note_block_is_rendered(self):
        self.assertIn('data-pb-material-map="1"', self.body, "映射表备注块（Spec §C2）")
        self.assertIn("pbMaterialMapNote(record.business_material_rows)", self.body,
                      "备注内容取业务材料行那把账（Spec §C1）")

    def test_e4_row_label_and_action_are_rendered(self):
        self.assertIn(".label", self.body, "行内渲染原因人话（Spec §C2）")
        self.assertIn(".action", self.body, "行内渲染补齐办法（Spec §C2）")

    def test_e5_old_guess_is_gone_from_the_panel(self):
        self.assertNotIn("已在库外", self.body, "这句是猜的，Spec §4 明令不许再写（Spec §C2）")
        self.assertNotIn("material_unresolved.join", self.body,
                         "不许再自己拼那串清单（Spec §C2）")

    def test_e6_existing_banners_are_untouched(self):
        for marker in ('data-pb-mixed-box="', 'data-pb-box-unknown-total="',
                       'data-pb-bbox-total="', 'data-pb-parts-stale="',
                       'data-pb-parts-unavailable="'):
            self.assertIn(marker, self.body, "既有 banner 一字不动（Spec §C2）：%s" % marker)

    def test_e7_panel_adds_no_request(self):
        self.assertNotIn("fetch(", self.body, "面板只渲染，不新增接口（Spec §C3）")


# --------------------------------------------------------------------------- #
# F 组：冻结面（逐行渲染回填仍归 pbRow；清单只有一个依据；无新接口）
# --------------------------------------------------------------------------- #
class FFreeze(unittest.TestCase):
    def test_f1_pb_row_is_not_rewritten(self):
        body = function_body("pbRow")
        self.assertNotIn("material_unresolved", body,
                         "pbRow() 不许接材料码缺口清单（Spec §C2 / §C3）")
        self.assertNotIn("data-pb-material-unresolved", body,
                         "逐行缺口不在 pbRow() 里（Spec §C2）")

    def test_f2_guess_word_is_gone_repo_wide(self):
        self.assertNotIn("已在库外", SOURCE, "前端源码里不许再出现这句猜测（Spec §4）")

    def test_f3_detail_is_the_only_second_source(self):
        body = function_body("pbMaterialUnresolvedRows")
        self.assertIn("material_unresolved_detail", body, "优先用 detail（Spec §C1）")
        self.assertIn("material_unresolved", body, "退回清单仍读原键（Spec §C1 / §C3）")
        self.assertNotIn("material_code", body,
                         "不许自己按 material_code 重算清单（Spec §C3）")

    def test_f4_helpers_hold_no_dom(self):
        for name in ("pbMaterialReasonLabel", "pbMaterialUnresolvedRows",
                     "pbMaterialUnresolvedHint", "pbMaterialMapNote"):
            body = function_body(name)
            for forbidden in ("document", "window.", "sessionStorage", "localStorage",
                              "fetch(", "querySelector"):
                self.assertNotIn(forbidden, body,
                                 "%s() 必须是纯函数（Spec §C1）：不许出现 %s" % (name, forbidden))


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
