"""红测：1.2 的盒型候选列表要说清「选它能不能往下走」（有没有部件模板）。

Spec：`docs/specs/packaging-box-candidate-runnability-in-panel.md`

现状缺口（代码级，都可指到行）：
  · 接口那半早已就位：`packaging_match._candidate()`（`:523-531`）给
    `part_template_available`（`True`/`False`/`None` 三态）/ `part_template_total` /
    `part_template_unavailable`，`_part_template_state()`（`:419-436`）的纪律是「读不到 →
    `None`（未知），不许折成 `False`」；
  · 前端这半没接：`tech_app/frontend/requirement-confirm.js:107-127` 的 `bmCandidate()`
    渲染了总分 / 分项 / 淘汰原因 / 无法判定 / 越界 / 适用行业，**没有** `part_template_*`；
  · 于是 `packaging-box-candidate-rank-and-runnability.md` §2.2 保证的「选之前看得见」
    在页面上等于没有。

纪律：`node -e` 抽顶层具名函数体执行（纯函数）+ 源码守卫 + `node --check`；
不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
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

CONFIRM_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"
MATCH_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_match.py"

NO_TEMPLATE_TEXT = ("这个盒型还没有部件模板（0 条），确认后 BOM / 工艺 / 成本都跑不动；"
                    "先补模板再确认")
BACKEND_MESSAGE = ("部件模板暂时查不到（知识库读失败：RuntimeError），请稍后重试；"
                   "这不代表该盒型没有模板")
FALLBACK_UNKNOWN_TEXT = "部件模板暂时查不到，请稍后重试；这不代表该盒型没有模板"

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
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
if (mode === "body") { console.log(JSON.stringify({ missing: false, body: fn })); process.exit(0); }
const cases = JSON.parse(mode);
eval(fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def run_cases(name: str, cases):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(CONFIRM_JS), name,
                           json.dumps(cases)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(CONFIRM_JS), name, "body"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("requirement-confirm.js 缺少顶层函数 %s()（Spec §C1）" % name)
    return str(payload.get("body") or "")


def note(row):
    payload = run_cases("boxCandidateRunnabilityNote", [[row]])
    if payload.get("missing"):
        raise AssertionError("requirement-confirm.js 缺少顶层纯函数 "
                             "boxCandidateRunnabilityNote()（Spec §C1）")
    row_out = payload["results"][0]
    if not row_out.get("ok"):
        raise AssertionError("boxCandidateRunnabilityNote() 抛异常：%s" % row_out.get("error"))
    return row_out.get("value")


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# T 组：四态文案与接线（红）
# --------------------------------------------------------------------------- #
class TBoxCandidateRunnability(unittest.TestCase):
    def test_t1_without_template_says_it_out_loud(self):
        self.assertEqual(NO_TEMPLATE_TEXT,
                         note({"part_template_available": False, "part_template_total": 0}),
                         "没有部件模板必须说清「确认后跑不动」（Spec §C1/§C2）")

    def test_t2_with_template_gives_the_count(self):
        self.assertEqual("部件模板 11 条",
                         note({"part_template_available": True, "part_template_total": 11}),
                         "有模板给条数（Spec §C1）")
        self.assertEqual("", note({"part_template_available": True}),
                         "条数拿不到时不许编 `0 条`（Spec §C1）")

    def test_t3_unknown_uses_backend_message_verbatim(self):
        text = note({"part_template_available": None,
                     "part_template_total": 0,
                     "part_template_unavailable": {"code": "template_lookup_failed",
                                                   "reason": "RuntimeError",
                                                   "message": BACKEND_MESSAGE}})
        self.assertEqual(BACKEND_MESSAGE, text,
                         "「查不到」这一态要用后端那句话逐字（Spec §C1）")
        self.assertNotIn("这个盒型还没有部件模板", text,
                         "查不到不许给出「没有模板」的断言句（Spec §C1/§2.4）——"
                         "后端那句「这不代表该盒型没有模板」是**正确措辞**，不算")
        self.assertIn("不代表", text, "查不到这一态必须明说「不代表没有」（Spec §C1）")

    def test_t4_unknown_without_message_gets_a_fallback_that_does_not_claim_absence(self):
        text = note({"part_template_available": None})
        self.assertEqual(FALLBACK_UNKNOWN_TEXT, text,
                         "拿不到 message 也要有自己的兜底句（Spec §C1）")
        self.assertIn("不代表", text, "兜底句必须明说「不代表没有」（Spec §C1）")

    def test_t5_old_backend_missing_the_keys_says_nothing(self):
        self.assertEqual("", note({}), "键缺失时前端不替后端编事实（Spec §C1）")
        self.assertEqual("", note({"box_type_code": "YT-RB-01001-A"}),
                         "老后端（没有这三个键）时这一句不出现（Spec §C1/§6 边界 3）")

    def test_t6_candidate_row_renders_the_note(self):
        body = function_body("bmCandidate")
        self.assertIn("boxCandidateRunnabilityNote(", body,
                      "候选行要渲染这一句（Spec §C2）")
        self.assertIn("data-bm-runnability", body,
                      "要带 `data-bm-runnability` 钩子（Spec §C2）")


# --------------------------------------------------------------------------- #
# S 组：护栏（现状即绿）—— 既有渲染、判据与前端不重排
# --------------------------------------------------------------------------- #
class SGuardrails(unittest.TestCase):
    def test_s1_note_is_self_contained(self):
        body = function_body("boxCandidateRunnabilityNote")
        for forbidden in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(forbidden, body,
                             "纯函数体内不许出现 %s（Spec §C1）" % forbidden)

    def test_s2_existing_candidate_fields_unchanged(self):
        body = function_body("bmCandidate")
        for token in ("box-match-rank", "box-match-score", "dimension_scores",
                      "reject_reasons", "undecidable_dimensions", "out_of_range",
                      "applicable_industries", "data-bm-confirm"):
            self.assertIn(token, body, "既有候选字段不许少（Spec §C2）：%s" % token)

    def test_s3_confirm_button_not_gated_by_the_note(self):
        body = function_body("bmCandidate")
        self.assertIn("row.can_confirm && bmCanDecide()", body,
                      "确认按钮的判据仍是 `row.can_confirm && bmCanDecide()`"
                      "（披露不是闸门，Spec §C2）")

    def test_s4_frontend_does_not_reorder_candidates(self):
        body = function_body("bmPanel")
        self.assertIn("record.candidates", body, "候选仍来自接口（Spec §C3）")
        self.assertNotIn("candidates.sort(", body, "前端不许重排候选（Spec §C3）")

    def test_s5_backend_three_states_unchanged(self):
        src = _source(MATCH_PY)
        self.assertIn('"part_template_available": template_state[0]', src,
                      "后端三态口径不变（Spec §C4）")
        self.assertIn('"part_template_total": template_state[1]', src)
        self.assertIn("return None, 0, {", src,
                      "读不到仍是 `None`（未知），不许折成 `False`（Spec §C4）")

    def test_s6_node_check_passes(self):
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "内联脚本必须仍能过 `node --check`：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
