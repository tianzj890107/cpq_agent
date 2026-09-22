"""红测：右栏 CAD 平面图「读不到」不许冒充「这份图纸没有可显示的 CAD 图元」。

Spec：`docs/specs/packaging-cad-plan-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:1952-1967 loadPackagingCadPlan()` 把 `!res.ok`（404 与 5xx）与
    `catch`（网络异常）一起折成同一个 `throw new Error("读取 CAD 平面图失败（HTTP n）")`，
    断网时更把浏览器原生英文文本（`Failed to fetch`）贴进中文右栏；
  · `renderPackagingCadPlan()`（`:1893`）拿不到任何「读失败」迹象，只能回落
    `PACKAGING_CAD_PLAN_EMPTY`（「这份图纸还没有可显示的 CAD 图元。」）—— 与「服务端读不到」不可分；
  · 同一条线上的左栏零件文档 / 业务部件清单早有「404 与 5xx 分家 + `read_problem` 空文档形状 +
    具名纯函数出文案」的口径（`packaging-parts-read-failure-empty-state.md` /
    `packaging-business-parts-read-failure-note.md`），只有平面图这一处没接。

纪律：`node -e` 抽具名函数体执行（纯函数）+ 源码守卫；不起服务、不发 HTTP、
不连 PG / 34、不写业务数据。
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
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

EMPTY_TEXT = "这份图纸还没有可显示的 CAD 图元。"
NO_COORDS_TEXT = "这批零件还没有图纸坐标，暂时画不出平面图（坐标要等 CAD IR 把折线顶点带进来）。"
UNBOUND_TEXT = "几何证据，尚未归属业务部件"
ALIASES = ["包装展开图", "包装刀模图"]
LABEL = "CAD 平面图"
HTTP500_TEXT = "暂时读不到 CAD 平面图（HTTP 500），请稍后重试；这不代表这份图纸没有图元"
HTTP503_TEXT = "暂时读不到 CAD 平面图（HTTP 503），请稍后重试；这不代表这份图纸没有图元"
NETWORK_TEXT = "暂时读不到 CAD 平面图（网络错误），请稍后重试；这不代表这份图纸没有图元"
GAP_TEXT = "已识别几何区域 0 个，尚未形成业务部件清单。"

# 纯函数体内的前置常量（`packagingCadPlanEmptyText()` 会引用它；逐字与 app.js 一致，S1 组核对）。
PREAMBLE = 'const PACKAGING_CAD_PLAN_EMPTY = "%s";\n' % EMPTY_TEXT

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
const preamble = process.argv[5] || "";
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
if (mode === "body") { console.log(JSON.stringify({ missing: false, body: fn })); process.exit(0); }
const cases = JSON.parse(mode);
eval(preamble + "\n" + fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def run_cases(name: str, cases, preamble: str = ""):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name,
                           json.dumps(cases), preamble],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body", ""],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少具名函数 %s()（Spec §C1/§C2）" % name)
    return str(payload.get("body") or "")


def evaluate(name: str, cases, preamble: str = ""):
    payload = run_cases(name, cases, preamble)
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §C1/§C2）" % name)
    return payload["results"]


def read_problem_text(problem):
    row = evaluate("packagingCadPlanReadProblemText", [[problem]])[0]
    if not row.get("ok"):
        raise AssertionError("packagingCadPlanReadProblemText() 抛异常：%s" % row.get("error"))
    return row.get("value")


def empty_text(doc):
    # `packagingCadPlanEmptyText()` 会调用同级的 `packagingCadPlanReadProblemText()`：
    # node 的 eval 作用域里没有别的函数，所以把那一份函数体一并带进去（两份都真跑）。
    preamble = function_body("packagingCadPlanReadProblemText") + "\n" + PREAMBLE
    row = evaluate("packagingCadPlanEmptyText", [[doc]], preamble)[0]
    if not row.get("ok"):
        raise AssertionError("packagingCadPlanEmptyText() 抛异常：%s" % row.get("error"))
    return row.get("value")


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def _problem(status=None, code="cad_plan_unavailable"):
    problem = {"code": code, "message": ""}
    if status is not None:
        problem["status"] = status
    return problem


# --------------------------------------------------------------------------- #
# T 组：读失败的三态文案与接线（红）
# --------------------------------------------------------------------------- #
class TCadPlanReadFailure(unittest.TestCase):
    def test_t1_no_code_means_no_read_problem(self):
        self.assertEqual("", read_problem_text({"code": "", "status": 500}),
                         "没有码就等于没有「读失败」这件事（Spec §C1）")
        self.assertEqual("", read_problem_text({}), "空 problem 也要给空串（Spec §C1）")

    def test_t2_http_status_says_http(self):
        self.assertEqual(HTTP500_TEXT, read_problem_text(_problem(500)),
                         "5xx 必须说 HTTP 码（Spec §C1）")
        self.assertEqual(HTTP503_TEXT, read_problem_text(_problem(503)))

    def test_t3_missing_status_says_network(self):
        self.assertEqual(NETWORK_TEXT, read_problem_text(_problem(0)),
                         "`status=0` 是网络错误（Spec §C1）")
        self.assertEqual(NETWORK_TEXT, read_problem_text({"code": "cad_plan_unavailable"}),
                         "没有状态码也要说网络错误，不许给空串（Spec §C1）")

    def test_t4_read_problem_wins_over_gap_message(self):
        text = empty_text({"geometry_evidence": {"components": []}, "business_parts": [],
                           "business_parts_gap": {"message": GAP_TEXT},
                           "read_problem": _problem(500)})
        self.assertEqual(HTTP500_TEXT, text,
                         "读失败必须优先于 gap 文案（Spec §C2）：今天回落成「%s」" % GAP_TEXT)

    def test_t5_network_read_problem_text(self):
        text = empty_text({"geometry_evidence": {"components": []}, "business_parts": [],
                           "read_problem": _problem(0)})
        self.assertEqual(NETWORK_TEXT, text, "网络错误要自己的文案（Spec §C2）")

    def test_t6_load_reads_404_and_5xx_apart(self):
        body = function_body("loadPackagingCadPlan")
        self.assertIn("read_problem", body,
                      "读失败要落成 `read_problem` 空文档形状（Spec §C4）：今天只有裸 throw")
        self.assertIn("404", body, "404（还没生成 / 路由未上线）必须与 5xx 分家（Spec §C4）")
        self.assertNotIn("String((error && error.message) || error)", body,
                         "不许把浏览器原生英文文本直接贴进右栏（Spec §C1/§C4）")

    def test_t7_render_has_read_problem_branch(self):
        body = function_body("renderPackagingCadPlan")
        self.assertIn("read_problem", body,
                      "`renderPackagingCadPlan()` 要认 `read_problem`（Spec §C3）")
        self.assertIn("packagingCadPlanEmptyText(", body,
                      "「没有分量」那一条要走 `packagingCadPlanEmptyText()` 这一处出处（Spec §C2/§C3）")

    def test_t8_both_pure_functions_are_self_contained(self):
        for name in ("packagingCadPlanReadProblemText", "packagingCadPlanEmptyText"):
            body = function_body(name)
            for forbidden in ("document.", "window.", "fetch(", "localStorage"):
                self.assertNotIn(forbidden, body,
                                 "%s() 体内不许出现 %s（Spec §C1/§C2）" % (name, forbidden))


# --------------------------------------------------------------------------- #
# S 组：护栏（现状即绿）—— 既有文案与渲染粒度不许被打回
# --------------------------------------------------------------------------- #
class SGuardrails(unittest.TestCase):
    def test_s1_existing_texts_unchanged(self):
        src = _source(APP_JS)
        self.assertIn('const PACKAGING_CAD_PLAN_EMPTY = "%s";' % EMPTY_TEXT, src,
                      "既有空态文案逐字不变（Spec §C5）")
        self.assertIn('const PACKAGING_CAD_PLAN_NO_COORDS = "%s";' % NO_COORDS_TEXT, src,
                      "「有图元没坐标」的文案逐字不变（Spec §C5）")
        self.assertIn('const PACKAGING_CAD_PLAN_UNBOUND = "%s";' % UNBOUND_TEXT, src)
        self.assertIn('const PACKAGING_CAD_PLAN_LABEL = "%s";' % LABEL, src)
        for alias in ALIASES:
            self.assertIn('"%s"' % alias, src, "别名逐字不变（Spec §C5）")

    def test_s2_component_box_still_prefers_drawing_bbox(self):
        body = function_body("packagingCadPlanComponentBox")
        self.assertIn("drawing_bbox", body)
        self.assertLess(body.index("drawing_bbox"), body.index("|| row.bbox"),
                        "画图框仍是先 `drawing_bbox`、再 `bbox` 兜底（Spec §C3/§C5）")

    def test_s3_backend_route_untouched(self):
        src = _source(MAIN_PY)
        self.assertIn('PACKAGING_GEOMETRY_READ_PATH = "/api/projects/{pid}/requirement/packaging-geometry"',
                      src, "后端读路由路径逐字不变（Spec §C5：本批不改后端）")
        self.assertIn('body["parts_built"]', src, "响应形状不变（Spec §C4/§C5）")

    def test_s4_no_coords_branch_still_exists(self):
        body = function_body("renderPackagingCadPlan")
        self.assertIn("PACKAGING_CAD_PLAN_NO_COORDS", body,
                      "「有分量、没坐标」那一档不许被删（Spec §C3/§C5）")


if __name__ == "__main__":
    unittest.main()
