"""红测：右栏「零件详情」的 404（没这件）与 5xx / 网络（读不到）必须分开说。

Spec：`docs/specs/packaging-part-detail-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:1298-1328 selectPackagingPart()` 把 `!res.ok`（404 与 5xx）与
    `catch`（网络异常）一起折成 `throw new Error(message || "读取零件详情失败（HTTP n）")`，
    再把这一句贴进 `#packagingPartFacts`；
  · 断网时贴的是浏览器原生英文文本（`Failed to fetch`）；
  · 后端早已分家：`main.py:8078` 零件不在文档里 → 404 + `PACKAGING_PART_NOT_FOUND`；
    未生成文档 → 200 + `built:false`（`PACKAGING_PART_READ_PATH` `:8009`）—— 前端一个都没用。

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

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

NOT_FOUND_TEXT = "这一件已经不在当前的零件文档里了（可能重跑过图纸解析）；请点左栏重新选一件"
HTTP500_TEXT = "暂时读不到这一件（HTTP 500），请稍后重试；这不代表这一件没有数据"
NETWORK_TEXT = "暂时读不到这一件（网络错误），请稍后重试；这不代表这一件没有数据"

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
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name,
                           json.dumps(cases)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少具名函数 %s()（Spec §C1）" % name)
    return str(payload.get("body") or "")


def problem_text(problem):
    payload = run_cases("packagingPartDetailReadProblemText", [[problem]])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 packagingPartDetailReadProblemText()（Spec §C1）")
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("packagingPartDetailReadProblemText() 抛异常：%s" % row.get("error"))
    return row.get("value")


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# T 组：两态文案与接线（红）
# --------------------------------------------------------------------------- #
class TPartDetailReadFailure(unittest.TestCase):
    def test_t1_no_code_means_no_read_problem(self):
        self.assertEqual("", problem_text({"status": 500}),
                         "没有码就等于没有这件事（Spec §C1）")
        self.assertEqual("", problem_text({}), "空 problem 也要给空串（Spec §C1）")

    def test_t2_not_found_says_pick_another_part(self):
        self.assertEqual(NOT_FOUND_TEXT,
                         problem_text({"code": "PACKAGING_PART_NOT_FOUND", "status": 404}),
                         "「没这件」必须说清并给出下一步（Spec §C1 第 2 条）")

    def test_t3_http_failure_says_unreadable(self):
        self.assertEqual(HTTP500_TEXT,
                         problem_text({"code": "parts_unavailable", "status": 500}),
                         "5xx 说「读不到」（Spec §C1 第 3 条）")
        self.assertEqual("暂时读不到这一件（HTTP 503），请稍后重试；这不代表这一件没有数据",
                         problem_text({"code": "parts_unavailable", "status": 503}))

    def test_t4_network_failure_says_network(self):
        self.assertEqual(NETWORK_TEXT, problem_text({"code": "parts_unavailable", "status": 0}),
                         "`status=0` 是网络错误（Spec §C1 第 4 条）")
        self.assertEqual(NETWORK_TEXT, problem_text({"code": "parts_unavailable"}),
                         "缺状态码也要说网络错误（Spec §C1 第 4 条）")

    def test_t5_status_404_with_another_code_is_not_a_missing_part(self):
        text = problem_text({"code": "parts_unavailable", "status": 404})
        self.assertEqual("暂时读不到这一件（HTTP 404），请稍后重试；这不代表这一件没有数据", text,
                         "只有 `PACKAGING_PART_NOT_FOUND` 才能说「没这件」（Spec §C1 第 2 条 / §6 边界 3）")
        self.assertNotIn("重新选一件", text, "别把「读不到」说成「没这件」")

    def test_t6_selection_no_longer_dumps_raw_text(self):
        body = function_body("selectPackagingPart")
        self.assertIn("packagingPartDetailReadProblemText(", body,
                      "失败文案要出自这一处出处（Spec §C2）")
        self.assertNotIn("String((error && error.message) || error)", body,
                         "不许把原生文本（含浏览器英文）直接贴进右栏（Spec §C2）")


# --------------------------------------------------------------------------- #
# S 组：护栏（现状即绿）
# --------------------------------------------------------------------------- #
class SGuardrails(unittest.TestCase):
    def test_s1_200_path_unchanged(self):
        body = function_body("selectPackagingPart")
        self.assertIn("renderPackagingPartPanel(payload)", body,
                      "200 路径仍原样交给面板渲染（Spec §C2）")
        self.assertIn("highlightPackagingBusinessPartSelection(code, payload)", body,
                      "选中高亮仍是既有调用（Spec §C3）")
        src = _source(APP_JS)
        self.assertIn("正在读取零件详情…", src,
                      "右栏占位文案逐字不变（Spec §C2）")

    def test_s2_backend_not_found_code_unchanged(self):
        src = _source(MAIN_PY)
        self.assertIn('PACKAGING_PART_NOT_FOUND = "PACKAGING_PART_NOT_FOUND"', src,
                      "后端稳定码逐字不变（Spec §C1/§C3）")
        self.assertIn('PACKAGING_PART_READ_PATH = "/api/projects/{pid}/requirement/packaging-parts/{part_code}"',
                      src, "读路由路径逐字不变（Spec §C3）")

    def test_s3_processability_reasons_unchanged(self):
        body = function_body("packagingPartProcessability")
        self.assertIn("未找到闭合轮廓", body,
                      "下游按钮的置灰原因不许被打回（Spec §C3）")

    def test_s4_node_check_passes(self):
        proc = subprocess.run(["node", "--check", str(APP_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "内联脚本必须仍能过 `node --check`：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
