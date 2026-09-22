"""红测：2.1 左栏"零件文档读不到"不许显示成"还没生成，请先跑一键解析"。

Spec：`docs/specs/packaging-parts-read-failure-empty-state.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:2399-2413 fetchPackagingParts()` 把 `!res.ok`（404 与 500）
    与 `catch`（网络异常）一起折成 `null`；
  · `packagingPartsEmptyText()`（`:2017-2042`）拿不到任何"读失败"迹象，于是回落
    "零件文档还没生成，请先跑一键解析图纸。" —— 服务端 500 时用户被告知去重跑一键解析；
  · 404（端点未上线）与 500（读不到）在源码上是同一个 `return null`，空态文案不可能分开说。

纪律：`node -e` 抽具名函数体执行（纯函数）+ 源码守卫；不起服务、不发 HTTP、
不连 PG / SQLite、不写业务数据。
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

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

MISSING_TEXT = "零件文档还没生成，请先跑一键解析图纸。"
NO_PARTS_TEXT = "这份图纸没有可用的零件。"
HTTP500_TEXT = "暂时读不到零件文档（HTTP 500），请稍后重试；这不代表这份图纸没有零件"
NETWORK_TEXT = "暂时读不到零件文档（网络错误），请稍后重试；这不代表这份图纸没有零件"

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


def run_cases(source: pathlib.Path, name: str, cases, timeout: int = 60):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(source), name, json.dumps(cases)],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(source: pathlib.Path, name: str) -> str:
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(source), name, "body"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def empty_text(parts_doc, preconditions=None):
    payload = run_cases(APP_JS, "packagingPartsEmptyText", [[parts_doc, preconditions or []]])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 packagingPartsEmptyText()")
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("packagingPartsEmptyText() 抛异常：%s" % row.get("error"))
    return row.get("value")


def _problem(status=None):
    problem = {"code": "parts_unavailable", "message": ""}
    if status is not None:
        problem["status"] = status
    return problem


# --------------------------------------------------------------------------- #
# T 组：读失败必须自己一句话，且优先于其它所有空态
# --------------------------------------------------------------------------- #
class TPartsReadFailureEmptyState(unittest.TestCase):
    def test_t1_http_500_says_unreadable(self):
        text = empty_text({"parts": [], "filtered": [], "unavailable": [], "stats": {},
                           "source": {}, "reviewable": False, "built": False,
                           "read_problem": _problem(500)})
        self.assertEqual(HTTP500_TEXT, text,
                         "读不到零件文档必须说清楚（Spec §2.2）：今天回落成"
                         "「%s」，用户会去重跑一键解析" % MISSING_TEXT)

    def test_t2_network_error_says_network(self):
        text = empty_text({"parts": [], "built": False, "read_problem": _problem(0)})
        self.assertEqual(NETWORK_TEXT, text, "没有状态码要说'网络错误'")
        text_absent = empty_text({"parts": [], "built": False, "read_problem": _problem()})
        self.assertEqual(NETWORK_TEXT, text_absent, "status 缺失同样按网络错误说")

    def test_t3_read_failure_outranks_other_empty_states(self):
        doc = {"parts": [], "built": True, "total": 0,
               "unavailable": [{"code": "no_unit", "message": "图纸单位未确认"}],
               "read_problem": _problem(503)}
        text = empty_text(doc)
        self.assertNotIn(NO_PARTS_TEXT, text,
                         "读不到时不许说'这份图纸没有可用的零件。'（Spec §2.2）")
        self.assertNotIn(MISSING_TEXT, text, "读不到时也不许说'还没生成，请先跑一键解析'")
        self.assertIn("503", text, "要说得出是哪一个状态码")


# --------------------------------------------------------------------------- #
# T 组（护栏）：没有读失败时的既有空态与文案一个字不改
# --------------------------------------------------------------------------- #
class TExistingEmptyStatesUnchanged(unittest.TestCase):
    def test_t4_plain_empty_doc_keeps_the_old_sentence(self):
        self.assertEqual(MISSING_TEXT, empty_text({"parts": [], "built": False}),
                         "普通空文档（没算过）的文案逐字不变")

    def test_t5_parts_present_means_no_empty_text(self):
        self.assertEqual("", empty_text({"parts": [{"part_code": "DWG-P01"}], "built": True,
                                         "total": 1}),
                         "有零件时仍返回空串（不新增任何提示位）")

    def test_t6_built_empty_keeps_the_no_parts_sentence(self):
        self.assertEqual(NO_PARTS_TEXT, empty_text({"parts": [], "built": True, "total": 0}),
                         "确实没有零件时仍是既有那句（Spec §2.5）")

    def test_t7b_unavailable_reasons_are_verbatim(self):
        doc = {"parts": [], "built": True,
               "unavailable": [{"code": "all_filtered", "message": "分量全部被过滤（图框 / 碎线）"}]}
        self.assertEqual("分量全部被过滤（图框 / 碎线）", empty_text(doc),
                         "服务端给的不可用原因照旧原样渲染")


class TWiring(unittest.TestCase):
    def test_t7_fetch_distinguishes_404_from_a_read_failure(self):
        body = function_body(APP_JS, "fetchPackagingParts")
        self.assertTrue(body, "app.js 缺少 fetchPackagingParts()")
        self.assertIn("404", body,
                      "404（端点未上线）必须与 5xx（读不到）在源码上分开（Spec §2.1）")
        self.assertIn("read_problem", body,
                      "读失败时必须把 read_problem 交给左栏空态（不许再折成 null）")

    def test_t8_empty_text_is_still_a_pure_function(self):
        body = function_body(APP_JS, "packagingPartsEmptyText")
        self.assertTrue(body, "app.js 缺少 packagingPartsEmptyText()")
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "空态文案必须仍是纯函数")


if __name__ == "__main__":
    unittest.main()
