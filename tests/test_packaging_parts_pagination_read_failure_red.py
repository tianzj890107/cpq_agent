"""红测：2.1 "继续加载"分页读失败不许静默 —— 点一下必须当场说清"这一页没读出来"。

Spec：`docs/specs/packaging-parts-pagination-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:2412-2436 loadMorePackagingParts()` 把 `!res.ok`、
    `res.json()` 解不出、`fetch` 抛异常三条都折成 `return null`；
  · 唯一调用点 `:3667`（`more.addEventListener("click", () => { loadMorePackagingParts(); })`）
    丢掉返回值 —— 点一下什么都不会发生：按钮还亮着、文案一字不变、左栏不重画；
  · `renderTree()`（`:3487-3698`）的"还有 N 件未列出"那一块没有任何"这一页读不到"的位置。

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

PURE_FN = "packagingPartsPageReadProblemText"
HTTP_TEXT = ('这一页零件没读出来（HTTP 503），已列出的零件不受影响；'
             '点"继续加载"重试')
NETWORK_TEXT = ('这一页零件没读出来（网络错误），已列出的零件不受影响；'
                '点"继续加载"重试')

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
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2.1）" % PURE_FN)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (PURE_FN, row.get("error")))
    return row.get("value")


# --------------------------------------------------------------------------- #
# P 组：这一页读不出来必须自己一句话（纯函数）
# --------------------------------------------------------------------------- #
class PPageReadProblemText(unittest.TestCase):
    def test_p1_http_500_says_the_page_failed(self):
        self.assertEqual(HTTP_TEXT,
                         problem_text({"code": "parts_page_unavailable",
                                       "status": 503, "message": ""}),
                         '读不到这一页必须说清，且要保住"已列出的零件不受影响"（Spec §2.1）')

    def test_p2_network_error_says_network(self):
        self.assertEqual(NETWORK_TEXT,
                         problem_text({"code": "parts_page_unavailable",
                                       "status": 0, "message": ""}),
                         "没有状态码要说'网络错误'")
        self.assertEqual(NETWORK_TEXT,
                         problem_text({"code": "parts_page_unavailable", "message": ""}),
                         "status 缺失同样按网络错误说")

    def test_p3_no_problem_means_no_sentence(self):
        for value in ({}, None, {"code": ""}):
            self.assertEqual("", problem_text(value),
                             "没有这一页的读问题就不许拼出这句话（%r）" % (value,))

    def test_p6_pure_function_has_no_dom(self):
        body = function_body(APP_JS, PURE_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.1）" % PURE_FN)
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "分页读失败文案必须是纯函数（Spec §2.1）")


# --------------------------------------------------------------------------- #
# P 组：接线（取数口 + 渲染位置）
# --------------------------------------------------------------------------- #
class PWiring(unittest.TestCase):
    def test_p4_load_more_reports_the_failed_page(self):
        body = function_body(APP_JS, "loadMorePackagingParts")
        self.assertTrue(body, "app.js 缺少 loadMorePackagingParts()")
        self.assertIn("parts_page_unavailable", body,
                      "分页读失败必须留下自己的形状，不许再只 return null（Spec §2.2）")
        self.assertIn("page_problem", body,
                      "失败要写进 currentPackagingParts.page_problem 交给渲染（Spec §2.2）")
        self.assertIn("packagingPartsPageReadProblemText", body,
                      "文案必须从这个纯函数来，不许在分页里另写一套（Spec §2.1/§2.2）")

    def test_p5_render_tree_has_the_retry_note_hook(self):
        body = function_body(APP_JS, "renderTree")
        self.assertTrue(body, "app.js 缺少 renderTree()")
        self.assertIn("qqPartsPageProblem", body,
                      "左栏必须有渲染这一页读失败的位置（Spec §2.3）")
        self.assertIn("继续加载", body, "既有'继续加载'按钮不许被删（Spec §2.3）")
        at = body.find("qqPartsPageProblem")
        self.assertLess(at, body.find("part-filtered-note"),
                        "提示块要挂在'还有 N 件未列出'那一块里（Spec §2.3）")


# --------------------------------------------------------------------------- #
# P 组（护栏）：既有前置判断与成功路径一个字不改
# --------------------------------------------------------------------------- #
class PExistingBehaviourUnchanged(unittest.TestCase):
    def test_p7_precondition_and_success_path_verbatim(self):
        body = function_body(APP_JS, "loadMorePackagingParts")
        self.assertTrue(body, "app.js 缺少 loadMorePackagingParts()")
        self.assertIn("if (!currentProject || !doc.has_more) return null;", body,
                      "既有前置判断逐字不变（Spec §2.2）")
        for needle in ("packagingPartsShown.push(row)",
                       "currentPackagingParts = Object.assign({}, doc, page,",
                       "renderTree(currentIR || {})",
                       "return page;"):
            self.assertIn(needle, body, "成功路径逐字不变：%s" % needle)

    def test_p8_failure_keeps_the_rows_already_listed(self):
        body = function_body(APP_JS, "loadMorePackagingParts")
        self.assertTrue(body, "app.js 缺少 loadMorePackagingParts()")
        for forbidden in ("packagingPartsShown = []", "packagingPartsShown.length = 0",
                          "packagingPartsShown.splice(0"):
            self.assertNotIn(forbidden, body,
                             "这一页失败不许把已列出的零件丢掉：%s" % forbidden)
        self.assertNotIn("has_more = false", body,
                         "不许把这一页失败改写成'没有更多零件'（Spec §3）")
        src = read_text(APP_JS)
        for literal in ("part-truncated-note", "packagingPartsLoadMore",
                        "继续加载（还有 ${missing} 件）"):
            self.assertIn(literal, src,
                          "既有'还有 N 件未列出 + 继续加载'出口逐字不变：%s" % literal)


if __name__ == "__main__":
    unittest.main()
