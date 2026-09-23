"""红测：批量 3D 挤出的正文读不出来，被渲染成"3D 覆盖率 0%（0/0 件可挤出）"。

Spec：`docs/specs/packaging-solids-body-unusable.md`

现状缺口（代码级，可指到行）：
  · `app.js:2145-2164`（批量）：`payload = await res.json().catch(() => ({}))` 之后
    `const stats = (payload && payload.stats) || {}`，再直接渲染
    `3D 覆盖率 ${Math.round((Number(stats.solid_ok_ratio) || 0) * 100)}%（${stats.ok_total||0}/${stats.part_total||0}）`
    → **200 但正文不可用**（网关 HTML / 空对象）会渲染成"0%（0/0 件可挤出）"并 `return {ok:true}`
    —— 与"真的 0 件可挤出"逐字同形；
  · `app.js:1463-1485`（单件）：`status` 缺失时落到
    `这一件暂时挤不出来（${payload.reason || "unknown"}）` → 正文不可用被说成"这一件挤不出来"。

纪律：`node -e` 抽具名函数体执行（纯函数）+ 源码守卫；不起服务、不发 HTTP、
不连 PG / SQLite、不写业务数据。
禁止为了让红测转绿而修改本文件。
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

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

BATCH_FN = "packagingSolidsBatchFacts"
SINGLE_FN = "packagingSolidBodyProblemText"
BATCH_MSG = ("这一次读不到批量挤出结果（正文里没有覆盖率），请稍后重试；"
             "这不代表一件都挤不出来。")
SINGLE_MSG = ("这一次读不到这一件的挤出结果（正文里没有结论），请稍后重试；"
              "这不代表这一件挤不出来。")
BATCH_CALLER = "packagingPartsSolidBatch"
SINGLE_CALLER = "packagingPartSolidPreview"

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
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
const mode = process.argv[4];
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


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def run_node(name: str, mode: str):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, mode],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    payload = run_node(name, "body")
    return "" if payload.get("missing") else str(payload.get("body") or "")


def call_pure(name: str, cases):
    payload = run_node(name, json.dumps(cases))
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2）" % name)
    out = []
    for row in payload.get("results") or []:
        if not row.get("ok"):
            raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
        out.append(row.get("value"))
    return out


def function_window(name: str) -> str:
    src = read_text(APP_JS)
    at = src.find("function " + name + "(")
    if at < 0:
        return ""
    match = re.search(r"\n(?:async )?function ", src[at + 10:])
    return src[at:at + 10 + match.start()] if match else src[at:]


# --------------------------------------------------------------------------- #
# V1–V4：批量正文可用性必须能判（纯函数）
# --------------------------------------------------------------------------- #
class VBatchFacts(unittest.TestCase):
    def test_v1_unusable_body_is_not_zero_coverage(self):
        values = call_pure(BATCH_FN, [{}, None, "nope", [], 0])
        for value, label in zip(values, [{}, None, "nope", [], 0]):
            self.assertFalse(value.get("available"),
                             "正文不可用（%r）不许算成可用：%r" % (label, value))
            self.assertEqual(value.get("message"), BATCH_MSG,
                             "不可用时必须给「读不到批量挤出结果」那句：%r" % value)
            self.assertEqual(value.get("headline"), "",
                             "不可用时不许产出「3D 覆盖率 …%」这句：%r" % value)

    def test_v2_stats_shape_must_not_be_folded_into_zero(self):
        cases = [{"stats": {}}, {"stats": []}, {"stats": {"part_total": "N/A"}},
                 {"stats": {"part_total": None}}, {"stats": {"part_total": -1}}]
        for value, label in zip(call_pure(BATCH_FN, cases), cases):
            self.assertFalse(value.get("available"),
                             "stats 形状不对（%r）不许按「0 件可挤出」渲染：%r" % (label, value))
            self.assertEqual(value.get("message"), BATCH_MSG)

    def test_v3_usable_body_keeps_the_existing_sentence(self):
        stats = {"solid_ok_ratio": 0.25, "ok_total": 16, "part_total": 64,
                 "unsupported_total": 48}
        value = call_pure(BATCH_FN, [{"stats": stats}])[0]
        self.assertTrue(value.get("available"), value)
        self.assertEqual(value.get("headline"), "3D 覆盖率 25%（16/64 件可挤出）",
                         "可用时这句必须逐字不变（Spec §2.1）：%r" % value)
        self.assertAlmostEqual(float(value.get("ratio")), 0.25, places=9)
        self.assertEqual(int(value.get("ok_total")), 16)
        self.assertEqual(int(value.get("part_total")), 64)
        self.assertEqual(value.get("message"), "")

    def test_v4_pure_functions_have_no_dom(self):
        for name in (BATCH_FN, SINGLE_FN):
            body = function_body(name)
            self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2）" % name)
            for banned in ("document.", "window.", "fetch(", "localStorage", "alert("):
                self.assertNotIn(banned, body,
                                 "%s() 不许碰 %s（纯函数纪律）" % (name, banned))


# --------------------------------------------------------------------------- #
# V5–V6：单件正文可用性必须能判（纯函数）
# --------------------------------------------------------------------------- #
class VSingleBodyProblem(unittest.TestCase):
    def test_v5_missing_status_is_a_read_problem(self):
        values = call_pure(SINGLE_FN, [{}, {"status": 0}, {"status": "  "}, None])
        for value, label in zip(values, [{}, {"status": 0}, {"status": "  "}, None]):
            self.assertEqual(value, SINGLE_MSG,
                             "正文不可用（%r）必须说「读不到这一件的挤出结果」：%r"
                             % (label, value))

    def test_v6_usable_statuses_keep_their_own_path(self):
        values = call_pure(SINGLE_FN, [{"status": "ok"}, {"status": "unsupported",
                                                          "reason": "outline_open"}])
        for value, label in zip(values, ["ok", "unsupported"]):
            self.assertEqual(value, "",
                             "有结论（status=%r）时这句读不到提示必须为空，结论照既有路径渲染：%r"
                             % (label, value))


# --------------------------------------------------------------------------- #
# V7–V8：两处调用点的接线（源码守卫；V7 今天就该绿）
# --------------------------------------------------------------------------- #
class VCallSites(unittest.TestCase):
    def test_v7_existing_rendering_paths_are_still_there(self):
        batch = function_window(BATCH_CALLER)
        self.assertTrue(batch, "app.js 里找不到 %s()" % BATCH_CALLER)
        for anchor in ("3D 覆盖率", "await refreshPackagingParts()",
                       "return { ok: true, result: payload };"):
            self.assertIn(anchor, batch, "可用正文的既有路径不许改：缺 %r" % anchor)
        single = function_window(SINGLE_CALLER)
        self.assertTrue(single, "app.js 里找不到 %s()" % SINGLE_CALLER)
        for anchor in ("packagingPartSolidReason(", "正在计算 3D 挤出体…", "loadSTL("):
            self.assertIn(anchor, single, "单件预览的既有路径不许改：缺 %r" % anchor)

    def test_v8_call_sites_use_the_new_pure_functions(self):
        batch = function_window(BATCH_CALLER)
        self.assertIn(BATCH_FN + "(", batch,
                      "批量出口必须走 %s()（Spec §2.3）" % BATCH_FN)
        self.assertNotIn("stats.solid_ok_ratio", batch,
                         "批函数体内不许再「缺就是 0」地直接渲染覆盖率（Spec §2.3）")
        single = function_window(SINGLE_CALLER)
        self.assertIn(SINGLE_FN + "(", single,
                      "单件出口必须走 %s()（Spec §2.3）" % SINGLE_FN)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main(verbosity=2)
