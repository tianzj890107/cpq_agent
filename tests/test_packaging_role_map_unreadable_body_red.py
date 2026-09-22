"""红测：角色映射正文解不出时，页面写着"每一行都有业务角色了"。

Spec：`docs/specs/packaging-role-map-unreadable-body.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:2512 const payload = await res.json().catch(() => ({}));`
    —— 正文解不出折成 `{}`；
  · `:2519 return renderPackagingRoleMap((payload && payload.role_map) || {});`
    —— 形状不对也照渲染；
  · `renderPackagingRoleMap()`（`:2539-2542`）`total === 0` 时渲染
    **"每一行都有业务角色了。"**，于是"200 但正文不可用"与"确实全映射完"完全同形。
非 2xx（`:2513-2518`）与抛异常（`:2520-2521`）两条路是对的，本批不许动它们。

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

PURE_FN = "packagingRoleMapReadProblemText"
BODY_UNREADABLE_TEXT = ("这一次读到的角色映射正文解不出，请稍后重试；"
                        "这不代表每一行都有业务角色。")

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


# --------------------------------------------------------------------------- #
# S 组：200 但正文不可用必须自己一句话（纯函数）
# --------------------------------------------------------------------------- #
class SReadProblemText(unittest.TestCase):
    def test_s1_unreadable_body_says_so(self):
        self.assertEqual(BODY_UNREADABLE_TEXT,
                         problem_text({"code": "role_map_body_unexpected",
                                       "status": 200, "message": ""}),
                         "正文解不出必须说清，且不许给出'每行都有角色'这种结论（Spec §2.1）")

    def test_s2_no_problem_means_no_sentence(self):
        for value in ({}, None, {"code": ""}, {"code": "something_else"}):
            self.assertEqual("", problem_text(value),
                             "没有读问题 / 表外码不许拼出这句话（%r）" % (value,))

    def test_s3_pure_function_has_no_dom(self):
        body = function_body(PURE_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.1）" % PURE_FN)
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "读失败文案必须是纯函数（Spec §2.1）")


# --------------------------------------------------------------------------- #
# S 组：接线（res.ok 之后要有形状检查，且交给既有的 unavailable 渲染）
# --------------------------------------------------------------------------- #
class SWiring(unittest.TestCase):
    def test_s4_shape_check_sits_after_res_ok(self):
        body = function_body("loadPackagingRoleMap")
        self.assertTrue(body, "app.js 缺少 loadPackagingRoleMap()")
        at_ok = body.find("res.ok")
        self.assertGreaterEqual(at_ok, 0, "既有非 2xx 分支不许被删")
        at_shape = body.find("role_map_body_unexpected")
        self.assertGreaterEqual(at_shape, 0,
                                "200 但正文形状不对必须有自己的形状（Spec §2.2）")
        self.assertLess(at_ok, at_shape,
                        "形状检查必须排在 res.ok 判定之后（先分非 2xx）")
        self.assertIn("renderPackagingRoleMapUnavailable", body[at_shape:at_shape + 400],
                      "形状不对要交给既有的'读不到'渲染（Spec §2.2）")

    def test_s5_panel_does_not_get_a_bogus_empty_payload(self):
        body = function_body("loadPackagingRoleMap")
        self.assertTrue(body, "app.js 缺少 loadPackagingRoleMap()")
        self.assertIn(PURE_FN, body,
                      "文案必须从这个纯函数来，不许在取数口另写一套（Spec §2.2）")
        self.assertIn("payload.role_map", body,
                      "形状正常的正文仍要按既有口径渲染（Spec §2.2）")


# --------------------------------------------------------------------------- #
# S 组（护栏）：两条既有失败路径与合法空态一个字不改
# --------------------------------------------------------------------------- #
class SExistingBehaviourUnchanged(unittest.TestCase):
    def test_s6_two_existing_failure_paths_verbatim(self):
        body = function_body("loadPackagingRoleMap")
        self.assertTrue(body, "app.js 缺少 loadPackagingRoleMap()")
        for literal in ("if (!res.ok) {", "payload.detail", "detail.message || detail",
                        "`HTTP ${res.status}`",
                        'renderPackagingRoleMapUnavailable("网络错误，请稍后重试")'):
            self.assertIn(literal, body,
                          "既有两条失败路径逐字不变：%s" % literal)

    def test_s7_role_map_panel_copy_verbatim(self):
        body = function_body("renderPackagingRoleMap")
        self.assertTrue(body, "app.js 缺少 renderPackagingRoleMap()")
        for literal in ("角色未映射 ", " 行</div>", "data-role-map-templates-unavailable",
                        "模板暂时读不到（", "这不代表该盒型没有部件模板。",
                        "每一行都有业务角色了。", "role-map-empty"):
            self.assertIn(literal, body, "既有面板文案与钩子逐字不变：%s" % literal)

    def test_s8_legal_empty_state_is_still_rendered_as_such(self):
        body = function_body("renderPackagingRoleMap")
        self.assertTrue(body, "app.js 缺少 renderPackagingRoleMap()")
        for literal in ("Array.isArray(roleMap && roleMap.items)", "unbound_total"):
            self.assertIn(literal, body, "合法空态口径逐字不变：%s" % literal)


if __name__ == "__main__":
    unittest.main()
