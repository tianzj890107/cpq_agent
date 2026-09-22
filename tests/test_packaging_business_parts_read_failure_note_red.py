"""红测：业务部件（权威清单）"读不到"不许显示成"已识别的几何区域还不是业务部件清单"。

Spec：`docs/specs/packaging-business-parts-read-failure-note.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:2437-2445 fetchPackagingBusinessParts()` 把 `!res.ok`（404 与 500）
    与 `catch`（网络异常）一起折成 `null`；
  · `packagingBusinessImportNote()`（`:2067-2090`）收到 `null` 就断言
    "已识别的几何区域还不是业务部件清单：下面列的是几何分量，不是业务零件。"，并给出
    "导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本" + 导入按钮 ——
    真相可能只是这一趟读不到（重新导入会落**新的一版**业务部件文档，是错的下一步）。

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

PURE_FN = "packagingBusinessReadProblemText"
HTTP500_TEXT = ("暂时读不到业务部件清单（HTTP 500），请稍后重试；"
                "这不代表这个项目还没导入权威清单")
NETWORK_TEXT = ("暂时读不到业务部件清单（网络错误），请稍后重试；"
                "这不代表这个项目还没导入权威清单")
LEGACY_SENTENCE = "已识别的几何区域还不是业务部件清单：下面列的是几何分量，不是业务零件。"
LEGACY_ACTION = "导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本"

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


def problem_text(problem):
    payload = run_cases(APP_JS, PURE_FN, [[problem]])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2.2）" % PURE_FN)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (PURE_FN, row.get("error")))
    return row.get("value")


def rows_of(doc):
    payload = run_cases(APP_JS, "packagingBusinessPartRows", [[doc]])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少 packagingBusinessPartRows()")
    return payload["results"][0].get("value")


# --------------------------------------------------------------------------- #
# U 组：读失败必须自己一句话（纯函数）
# --------------------------------------------------------------------------- #
class UBusinessReadProblemText(unittest.TestCase):
    def test_u1_http_500_says_unreadable(self):
        self.assertEqual(HTTP500_TEXT,
                         problem_text({"code": "business_parts_unavailable",
                                       "status": 500, "message": ""}),
                         "读不到业务部件清单必须说清楚（Spec §2.2）")

    def test_u2_network_error_says_network(self):
        self.assertEqual(NETWORK_TEXT,
                         problem_text({"code": "business_parts_unavailable",
                                       "status": 0, "message": ""}),
                         "没有状态码要说'网络错误'")
        self.assertEqual(NETWORK_TEXT,
                         problem_text({"code": "business_parts_unavailable", "message": ""}),
                         "status 缺失同样按网络错误说")

    def test_u3_no_problem_means_no_sentence(self):
        for value in ({}, None, {"code": ""}):
            self.assertEqual("", problem_text(value),
                             "没有读问题时不许拼出这句话（%r）" % (value,))

    def test_u8_pure_function_has_no_dom(self):
        body = function_body(APP_JS, PURE_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.2）" % PURE_FN)
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "读失败文案必须是纯函数（Spec §2.2）")


# --------------------------------------------------------------------------- #
# U 组：接线（取数口 + 判定优先级）
# --------------------------------------------------------------------------- #
class UWiring(unittest.TestCase):
    def test_u4_fetch_distinguishes_404_from_a_read_failure(self):
        body = function_body(APP_JS, "fetchPackagingBusinessParts")
        self.assertTrue(body, "app.js 缺少 fetchPackagingBusinessParts()")
        self.assertIn("404", body,
                      "404（端点未上线）必须与 5xx（读不到）在源码上分开（Spec §2.1）")
        self.assertIn("read_problem", body,
                      "读失败时必须把 read_problem 交给面板提示（不许再折成 null）")

    def test_u5_note_checks_the_read_problem_first(self):
        body = function_body(APP_JS, "packagingBusinessImportNote")
        self.assertTrue(body, "app.js 缺少 packagingBusinessImportNote()")
        at_problem = body.find("read_problem")
        self.assertGreaterEqual(at_problem, 0,
                                "面板提示必须先认 read_problem（Spec §2.3）")
        at_legacy = body.find("packaging-business-missing")
        self.assertTrue(at_legacy < 0 or at_problem < at_legacy,
                        "read_problem 的判定必须排在既有'还不是业务部件清单'文案之前")
        window = body[at_problem:at_problem + 400]
        self.assertIn("qqBusinessUnavailable", window,
                      "读不到要用自己的 data- 钩子（与 qqBusinessMissing 分家）")


# --------------------------------------------------------------------------- #
# U 组（护栏）：既有的"确实没有清单"路径一个字不改
# --------------------------------------------------------------------------- #
class UExistingNoteUnchanged(unittest.TestCase):
    def test_u6_legacy_note_literals_are_verbatim(self):
        src = read_text(APP_JS)
        for literal in (LEGACY_SENTENCE, LEGACY_ACTION, "packaging-business-missing",
                        "dataset.qqBusinessMissing", "导入权威清单（业务部件）"):
            self.assertIn(literal, src,
                          "既有'确实没有权威清单'的提示与导入按钮逐字不变：%s" % literal)

    def test_u7_business_rows_tolerate_null_and_empty(self):
        self.assertEqual([], rows_of(None), "null 文档仍是 []（护栏）")
        self.assertEqual([], rows_of({}), "空文档仍是 []（护栏）")
        self.assertEqual(1, len(rows_of({"business_parts": [{"part_code": "QG-01"}]})),
                         "有行时照旧返回行（护栏）")


if __name__ == "__main__":
    unittest.main()
