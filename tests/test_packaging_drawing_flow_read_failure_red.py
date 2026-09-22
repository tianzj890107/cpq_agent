"""红测：图纸解析链路面板「读不到状态」不许沉默、也不许把旧内容当本次结果。

Spec：`docs/specs/packaging-drawing-flow-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:1057-1063 fetchDrawingFlowState()` 把非 2xx（404 与 5xx）与
    正文不可用一起折成 `null`；
  · `:1067-1070 loadDrawingFlowPanel()` 把网络异常也 `.catch(() => null)`，然后
    `if (state) renderDrawingFlowPanel(state)` —— 读不到 = 什么都不做；
  · 于是「链路状态读不到」与「这个项目还没点过一键解析」在界面上长得一模一样。

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

HTTP500_TEXT = "暂时读不到图纸解析链路状态（HTTP 500），这不代表这个项目没跑过一键解析"
HTTP503_TEXT = "暂时读不到图纸解析链路状态（HTTP 503），这不代表这个项目没跑过一键解析"
NETWORK_TEXT = "暂时读不到图纸解析链路状态（网络错误），这不代表这个项目没跑过一键解析"

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
        raise AssertionError("app.js 缺少具名函数 %s()（Spec §C1/§C2）" % name)
    return str(payload.get("body") or "")


def problem_text(problem):
    payload = run_cases("drawingFlowReadProblemText", [[problem]])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 drawingFlowReadProblemText()（Spec §C1）")
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("drawingFlowReadProblemText() 抛异常：%s" % row.get("error"))
    return row.get("value")


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# T 组：读失败文案与三态接线（红）
# --------------------------------------------------------------------------- #
class TDrawingFlowReadFailure(unittest.TestCase):
    def test_t1_no_code_means_no_read_problem(self):
        self.assertEqual("", problem_text({"status": 500}), "没有码就没有这件事（Spec §C1）")
        self.assertEqual("", problem_text({}), "空 problem 给空串（Spec §C1）")

    def test_t2_http_status_says_http(self):
        self.assertEqual(HTTP500_TEXT, problem_text({"code": "drawing_flow_unavailable",
                                                     "status": 500}),
                         "5xx 要说 HTTP 码（Spec §C1）")
        self.assertEqual(HTTP503_TEXT, problem_text({"code": "drawing_flow_unavailable",
                                                     "status": 503}))

    def test_t3_missing_status_says_network(self):
        self.assertEqual(NETWORK_TEXT, problem_text({"code": "drawing_flow_unavailable",
                                                     "status": 0}),
                         "`status=0` 是网络错误（Spec §C1）")
        self.assertEqual(NETWORK_TEXT, problem_text({"code": "drawing_flow_unavailable"}),
                         "缺状态码也要说网络错误（Spec §C1）")

    def test_t4_fetch_splits_404_from_the_rest(self):
        body = function_body("fetchDrawingFlowState")
        self.assertIn("404", body, "404（端点未上线）必须与 5xx 分家（Spec §C2）")
        self.assertIn("read_problem", body,
                      "其它失败要落成 `read_problem` 形状（Spec §C2）：今天一律 `return null`")
        self.assertIn("drawing_flow_body_unexpected", body,
                      "200 但正文解不出也要有自己的码，不许折成 `null`（Spec §C2）")

    def test_t5_panel_renders_even_when_the_read_failed(self):
        body = function_body("loadDrawingFlowPanel")
        self.assertIn("renderDrawingFlowPanel(", body, "面板仍由这个函数渲染（Spec §C3）")
        self.assertNotIn("if (state) renderDrawingFlowPanel(state)", body,
                         "读到带 `read_problem` 的形状也必须渲染（Spec §C3）："
                         "今天只有 `if (state)` 才画，读不到就沉默")

    def test_t6_panel_speaks_first_when_unreadable(self):
        body = function_body("renderDrawingFlowPanel")
        self.assertIn("read_problem", body, "面板要认 `read_problem`（Spec §C4）")
        self.assertIn("drawingFlowReadProblemText(", body,
                      "读失败文案要出自这一处出处（Spec §C4）")
        self.assertLess(body.index("read_problem"), body.index("drawingFlowSteps("),
                        "读失败分支必须在步骤表之前（Spec §C4 第一优先）")

    def test_t7_pure_function_is_self_contained(self):
        body = function_body("drawingFlowReadProblemText")
        for forbidden in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(forbidden, body,
                             "纯函数体内不许出现 %s（Spec §C1）" % forbidden)


# --------------------------------------------------------------------------- #
# S 组：护栏（现状即绿）
# --------------------------------------------------------------------------- #
class SGuardrails(unittest.TestCase):
    def test_s1_existing_panel_rendering_unchanged(self):
        body = function_body("renderDrawingFlowPanel")
        for token in ("drawing-flow-title", "error_code", "error_message",
                      "CAD IR：实体", "CAD IR：尚无解析结果", "drawingFlowSteps(",
                      "cadIrSummaryOf("):
            self.assertIn(token, body, "既有面板渲染不许少（Spec §C4）：%s" % token)

    def test_s2_terminal_signal_untouched(self):
        body = function_body("drawingFlowTerminalSignal")
        self.assertIn("steps", body, "终态信号那批的口径不许回退（Spec §C5）")
        self.assertNotIn("read_problem", body,
                         "终态信号与读失败是两套东西，不许混（Spec §C5）")

    def test_s3_backend_route_unchanged(self):
        src = _source(MAIN_PY)
        self.assertIn('@app.get("/api/projects/{pid}/drawing-flow")', src,
                      "后端读路由逐字不变（Spec §C5）")

    def test_s4_node_check_passes(self):
        proc = subprocess.run(["node", "--check", str(APP_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "内联脚本必须仍能过 `node --check`：%s"
                         % (proc.stderr or proc.stdout)[:400])


if __name__ == "__main__":
    unittest.main()
