"""红测：补料厚 / 补材料 / 轮廓放行成功之后，列表读不回来就把整栏零件换成一句"读不到"。

Spec：`docs/specs/packaging-parts-reread-failure-after-write.md`

现状缺口（代码级，都可指到行）：
  · 三处"写后重读"（`app.js:1541-1548` 补料厚 / `:1588-1595` 补材料 / `:1641-1642` 轮廓出路）
    都写成 `if (reread) { currentPackagingParts = reread; } else { <回显补丁> }` ——
    这是 `## 381` 之前的旧契约（读失败给 `null`）；
  · `## 381` 落地后 `fetchPackagingParts()` 读失败给**带 `read_problem` 的真值空文档**
    （`:2980-3010`，`parts: []`），于是 `else` 分支成死代码、`currentPackagingParts` 被整份换成空文档
    —— 刚提交成功的一笔把左栏零件全清了。

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

PURE_FN = "packagingPartsRereadProblemText"
HTTP500_TEXT = ("刚才这一笔已经提交成功，但列表没能重新读回来（HTTP 500），"
                "下面显示的是本地回显；刷新页面即可核对服务端的值")
NETWORK_TEXT = ("刚才这一笔已经提交成功，但列表没能重新读回来（网络错误），"
                "下面显示的是本地回显；刷新页面即可核对服务端的值")
REREAD_CALL = "const reread = await fetchPackagingParts();"

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


def function_body(name: str) -> str:
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def problem_text(problem):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), PURE_FN, json.dumps([[problem]])],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2.1）" % PURE_FN)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (PURE_FN, row.get("error")))
    return row.get("value")


def reread_windows(size: int = 420):
    """三处"写后重读"的源码窗口（每个窗口从 `const reread = …` 起）。"""
    source = read_text(APP_JS)
    out = []
    at = source.find(REREAD_CALL)
    while at >= 0:
        out.append(source[at:at + size])
        at = source.find(REREAD_CALL, at + 1)
    return out


# --------------------------------------------------------------------------- #
# T 组：这一笔成功了但列表读不回来，必须自己一句话（纯函数）
# --------------------------------------------------------------------------- #
class TRereadProblemText(unittest.TestCase):
    def test_t1_http_500_says_the_write_succeeded_but_reread_failed(self):
        self.assertEqual(HTTP500_TEXT,
                         problem_text({"code": "parts_unavailable", "status": 500,
                                       "message": ""}),
                         "既要说清写成功了，也要说清列表没读回来（Spec §2.1）")

    def test_t2_network_error_says_network(self):
        self.assertEqual(NETWORK_TEXT,
                         problem_text({"code": "parts_unavailable", "status": 0, "message": ""}),
                         "没有状态码要说'网络错误'")
        self.assertEqual(NETWORK_TEXT,
                         problem_text({"code": "parts_unavailable", "message": ""}),
                         "status 缺失同样按网络错误说")

    def test_t3_no_problem_means_no_sentence(self):
        for value in ({}, None, {"code": ""}, {"code": "something_else"}):
            self.assertEqual("", problem_text(value),
                             "没有读问题 / 表外码不许拼出这句话（%r）" % (value,))

    def test_t4_pure_function_has_no_dom(self):
        body = function_body(PURE_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.1）" % PURE_FN)
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "这句话必须是纯函数（Spec §2.1）")


# --------------------------------------------------------------------------- #
# T 组：三处写后重读都要按三态处置
# --------------------------------------------------------------------------- #
class TRereadCallSites(unittest.TestCase):
    def test_t5_every_reread_site_checks_read_problem(self):
        windows = reread_windows()
        self.assertEqual(3, len(windows),
                         "三处'写后重读'（补料厚 / 补材料 / 轮廓出路）都要在（Spec §1）")
        for index, window in enumerate(windows, start=1):
            self.assertIn("read_problem", window,
                          "第 %d 处仍按旧契约判真值：读失败会被当成'读到了空文档'（Spec §2.2）"
                          % index)

    def test_t6_render_tree_has_the_reread_note_hook(self):
        body = function_body("renderTree")
        self.assertTrue(body, "app.js 缺少 renderTree()")
        self.assertIn("qqPartsRereadProblem", body,
                      "左栏必须有渲染这条披露的位置（Spec §2.3）")


# --------------------------------------------------------------------------- #
# T 组（护栏）：读到 / 404 两条既有路径一个字不改
# --------------------------------------------------------------------------- #
class TExistingPathsUnchanged(unittest.TestCase):
    def test_t7_normal_and_404_paths_still_there(self):
        source = read_text(APP_JS)
        self.assertIn("currentPackagingParts = reread", source,
                      "读到时的替换逐字不变（护栏）")
        self.assertIn("patchPackagingPartRows(partCode, patch)", source,
                      "404（端点未上线）的回显补丁路径不许被删（护栏）")
        body = function_body("patchPackagingPartRows")
        self.assertIn("currentPackagingParts", body,
                      "回显补丁的既有语义（整份行 + 当前页 + 已累加行）不许改（护栏）")

    def test_t8_no_site_clears_the_rows_or_swallows_the_problem(self):
        for index, window in enumerate(reread_windows(), start=1):
            self.assertNotIn("packagingPartsShown = []", window,
                             "第 %d 处不许清空已列出的零件（护栏）" % index)
        source = read_text(APP_JS)
        declaration = source.count("let packagingPartsShown = []")
        in_fetch = function_body("fetchPackagingParts").count("packagingPartsShown = []")
        self.assertEqual(1, declaration, "累加行的声明只有一处（护栏）")
        self.assertEqual(source.count("packagingPartsShown = []"),
                         declaration + in_fetch,
                         "清空只允许出现在 fetchPackagingParts() 自己的失败路径里（护栏）")


if __name__ == "__main__":
    unittest.main()
