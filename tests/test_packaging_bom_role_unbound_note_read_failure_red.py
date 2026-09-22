"""红测：BOM 读不到时"候选角色暂时读不到"这句提示被擦掉 —— 一次读失败把已披露的事实抹平。

Spec：`docs/specs/packaging-bom-role-unbound-note-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:2466-2487 refreshPackagingBomRoleUnboundNote()` **从不检查 `res.ok`**，
    `res.json()` 解不出 / `fetch` 抛异常也一起并进 `flag = {}`；
  · 然后**无条件**执行 `if (old) old.remove();`（`:2477-2478`）—— 读失败时把上一次刷新已经写上的
    `[data-role-unbound-templates-unavailable]` 提示删掉，且不补任何说明，于是"读不到"与
    "这个盒型没有候选角色"完全同形。

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

PURE_FN = "packagingRoleUnboundReadProblemText"
HTTP500_TEXT = ("这一次读不到 BOM（HTTP 500），候选角色的披露也读不到；"
                "请稍后重试，这不代表这些行都有候选角色。")
NETWORK_TEXT = ("这一次读不到 BOM（网络错误），候选角色的披露也读不到；"
                "请稍后重试，这不代表这些行都有候选角色。")
BODY_TEXT = ("这一次读到的 BOM 正文里没有盒型信息，候选角色的披露读不到；"
             "请稍后重试，这不代表这些行都有候选角色。")

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
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), PURE_FN,
         json.dumps([[problem]])],
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


# --------------------------------------------------------------------------- #
# R 组：这一趟读不到 BOM 必须自己一句话（纯函数）
# --------------------------------------------------------------------------- #
class RReadProblemText(unittest.TestCase):
    def test_r1_http_500_says_the_bom_read_failed(self):
        self.assertEqual(HTTP500_TEXT,
                         problem_text({"code": "bom_unavailable", "status": 500, "message": ""}),
                         "读不到 BOM 必须说清，且不许声称'这些行都有候选角色'（Spec §2.1）")

    def test_r2_network_error_says_network(self):
        self.assertEqual(NETWORK_TEXT,
                         problem_text({"code": "bom_unavailable", "status": 0, "message": ""}),
                         "没有状态码要说'网络错误'")
        self.assertEqual(NETWORK_TEXT,
                         problem_text({"code": "bom_unavailable", "message": ""}),
                         "status 缺失同样按网络错误说")

    def test_r3_unexpected_body_has_its_own_sentence(self):
        self.assertEqual(BODY_TEXT,
                         problem_text({"code": "bom_body_unexpected", "status": 200,
                                       "message": ""}),
                         "res.ok 但正文没有盒型信息是第三种情形（Spec §2.1）")

    def test_r4_no_problem_means_no_sentence(self):
        for value in ({}, None, {"code": ""}, {"code": "something_else"}):
            self.assertEqual("", problem_text(value),
                             "没有读问题 / 表外码不许拼出这句话（%r）" % (value,))

    def test_r5_pure_function_has_no_dom(self):
        body = function_body(PURE_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.1）" % PURE_FN)
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "读失败文案必须是纯函数（Spec §2.1）")


# --------------------------------------------------------------------------- #
# R 组：接线（读失败必须排在 old.remove() 之前，并把三条失败分家）
# --------------------------------------------------------------------------- #
class RWiring(unittest.TestCase):
    def test_r6_read_problem_returns_before_the_note_is_removed(self):
        body = function_body("refreshPackagingBomRoleUnboundNote")
        self.assertTrue(body, "app.js 缺少 refreshPackagingBomRoleUnboundNote()")
        at_problem = body.find("read_problem")
        self.assertGreaterEqual(at_problem, 0,
                                "读失败必须留下自己的形状（Spec §2.2）")
        at_remove = body.find("old.remove()")
        self.assertGreaterEqual(at_remove, 0, "既有旧提示的清理不许被删")
        self.assertLess(at_problem, at_remove,
                        "读失败必须在 old.remove() **之前**返回：一次读不到不许擦掉已披露的事实")
        self.assertIn("qqRoleUnboundReadProblem", body,
                      "读失败要挂自己的 data- 钩子（Spec §2.2）")

    def test_r7_fetch_failure_is_split_into_two_codes(self):
        body = function_body("refreshPackagingBomRoleUnboundNote")
        self.assertTrue(body, "app.js 缺少 refreshPackagingBomRoleUnboundNote()")
        self.assertIn("res.ok", body,
                      "既不检查 res.ok，500 的错误体就会被当成'没有 flag'（Spec §2.2）")
        self.assertIn("bom_unavailable", body,
                      "非 2xx 与网络异常要写成 bom_unavailable（Spec §2.2）")
        self.assertIn("bom_body_unexpected", body,
                      "res.ok 但正文没有盒型信息要写成 bom_body_unexpected（Spec §2.2）")


# --------------------------------------------------------------------------- #
# R 组（护栏）：既有的成功路径与调用点一个字不改
# --------------------------------------------------------------------------- #
class RExistingBehaviourUnchanged(unittest.TestCase):
    def test_r8_existing_literals_verbatim(self):
        body = function_body("refreshPackagingBomRoleUnboundNote")
        self.assertTrue(body, "app.js 缺少 refreshPackagingBomRoleUnboundNote()")
        for literal in ("data-role-unbound-templates-unavailable",
                        "候选角色暂时读不到（",
                        "这不代表该盒型没有候选角色。",
                        "role_unbound_templates_unavailable"):
            self.assertIn(literal, body,
                          "既有'读到了、但候选角色读不到'的披露逐字不变：%s" % literal)
        self.assertIn("if (!currentProject) return null;", body,
                      "既有前置判断逐字不变")
        src = read_text(APP_JS)
        self.assertIn("await refreshPackagingBomRoleUnboundNote()", src,
                      "refreshPackagingParts() 里的调用点不许被删")


if __name__ == "__main__":
    unittest.main()
