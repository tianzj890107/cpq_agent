"""红测：2.1 图纸解析入口接线 drawing-flow（DWG / DXF 项目）。

Spec：`docs/specs/drawing-flow-frontend-wiring.md`

现状缺口（实测，不是推断）：

  · `tech_app/frontend/app.js:1436` 用 `isImg = /\\.(png|jpe?g|webp|gif|bmp)$/i`
    把 DWG/DXF 判成"不是位图"，占位文案是「请上传该图纸的 PNG 或 JPG 后再解析」；
  · `app.js:1457` `$("btnParse").disabled = !isImg;` —— DWG 用户看到的解析按钮是灰的；
  · 前端全文 0 处 `drawing-flow`（grep 实测），`app.js:916` 打的是
    `POST /api/projects/{id}/parse`（视觉模型路径）；
  · 服务端入口早已存在：`tech_app/backend/main.py:6831` / `:6844`。

业务后果：客户手里的 DWG 被系统收下，却要求他回 CAD 另存 PNG，服务端已实测打通的
链路在界面上等于零。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写业务数据；
  · 入口判定用 `node` **实际执行** `renderDrawingEntry()` 纯函数（不是文本 grep）；
  · 禁止为了让红测转绿而修改本文件。
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

FRONTEND = ROOT / "tech_app" / "frontend"
APP_JS = FRONTEND / "app.js"
INDEX_HTML = FRONTEND / "index.html"

DRAWING_FLOW_RUN = "/drawing-flow/run"
PNG_HINT = "请上传该图纸的 PNG"

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
const fn = extract(process.argv[3]);
if (!fn) { console.log(JSON.stringify({missing: true})); process.exit(0); }
const cases = JSON.parse(process.argv[4]);
const out = {missing: false, results: []};
eval(fn);
for (const c of cases) { try { out.results.push(eval(process.argv[3] + "(" + JSON.stringify(c) + ")")); }
  catch (e) { out.results.push("ERR:" + e.message); } }
console.log(JSON.stringify(out));
"""


def run_entry_cases(cases):
    """把 app.js 里的 renderDrawingEntry() 抽出来交给 node 真跑。"""
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), "renderDrawingEntry",
         json.dumps(cases)],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


class AEntryRouting(unittest.TestCase):
    """A 组：入口判定必须可测、且把 DWG/DXF 指到 drawing-flow。"""

    def test_a1_pure_function_exists_and_is_free_of_dom(self):
        src = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("function renderDrawingEntry(" in src,
                        "缺少纯函数 renderDrawingEntry()：入口判定必须可被 node 直接执行")
        body = src.split("function renderDrawingEntry(", 1)[1].split("\nfunction ", 1)[0]
        for forbidden in ("document", "window", "sessionStorage", "localStorage", "$("):
            self.assertFalse(forbidden in body,
                             "renderDrawingEntry() 不得引用 %s（要能被 node 直接跑）" % forbidden)

    def test_a2_dwg_dxf_route_to_drawing_flow(self):
        out = run_entry_cases(["酒盒.dwg", "平面图.DXF", "ROUND.Dwg"])
        self.assertFalse(out["missing"], "renderDrawingEntry() 不存在，无法判定入口")
        self.assertEqual(out["results"], ["drawing_flow", "drawing_flow", "drawing_flow"],
                         "DWG/DXF 必须走 drawing_flow（大小写不敏感）")

    def test_a3_bitmap_keeps_vision_route(self):
        out = run_entry_cases(["a.png", "b.JPG", "c.jpeg", "d.webp", "e.gif", "f.bmp"])
        self.assertFalse(out.get("missing"), "renderDrawingEntry() 不存在，无法判定入口")
        self.assertEqual(out["results"], ["vision"] * 6,
                         "位图必须继续走视觉路径（本批不改变 2.1 既有能力）")

    def test_a4_3d_and_unknown_stay_blocked(self):
        out = run_entry_cases(["m.step", "m.STP", "m.iges", "m.stl", "x.pdf", "x.docx", ""])
        self.assertFalse(out.get("missing"), "renderDrawingEntry() 不存在，无法判定入口")
        self.assertEqual(out["results"], ["blocked_3d"] * 4 + ["blocked_other"] * 3,
                         "3D 导入项目与未知格式仍必须被明确阻断（不得误入 drawing_flow）")


class BButtonAndCalls(unittest.TestCase):
    """B 组：按钮可用性、两个调用点。"""

    def test_b1_grey_rule_no_longer_keys_on_isimg(self):
        src = APP_JS.read_text(encoding="utf-8")
        self.assertFalse('$("btnParse").disabled = !isImg;' in src,
                         "按钮置灰规则必须改由 renderDrawingEntry() 决定，不能继续用 isImg")

    def test_b2_run_and_read_endpoints_are_wired(self):
        src = APP_JS.read_text(encoding="utf-8")
        self.assertTrue(DRAWING_FLOW_RUN in src,
                        "必须调用 POST /api/projects/{pid}/drawing-flow/run")
        self.assertTrue(re.search(r"drawing-flow[`\"')\s]", src) is not None,
                        "必须调用 GET /api/projects/{pid}/drawing-flow 读取链路状态")
        self.assertTrue(re.search(r"ODAFileConverter|oda_converter|\bxvfb-run\b", src) is None,
                        "前端不得自带第二套 DWG 转换器，只能调统一服务")

    def test_b3_result_panel_and_cad_ir_summary(self):
        src = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("drawingFlowPanel" in src,
                        "必须新增 #drawingFlowPanel 容器承载步骤表与 cad_ir 摘要")
        for token in ("cad_ir", "entities", "layers"):
            self.assertTrue(token in src,
                            "cad_ir 摘要必须渲染 %s（用户要看到 6569/8 这类事实）" % token)

    def test_b4_failure_surfaces_stable_code_and_message(self):
        src = APP_JS.read_text(encoding="utf-8")
        for token in ("error_code", "error_message"):
            self.assertTrue(token in src,
                            "步骤失败必须展示 %s，不得只显示『解析失败，请重试』" % token)


class CRegressionGuards(unittest.TestCase):
    """C 组（绿护栏）：不得为了接新链路弄坏既有入口与语法。"""

    def test_c1_syntax_ok(self):
        for path in (APP_JS,):
            proc = subprocess.run(["node", "--check", str(path)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, "%s 语法错误：%s" % (path, proc.stderr))

    def test_c2_vision_path_call_point_survives(self):
        src = APP_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"`/api/projects/\$\{[^}]+\}/parse`", src) is not None,
                        "视觉路径 POST /parse 的调用点必须保留（PNG/PDF 仍走它）")

    def test_c3_isimg_and_3d_classification_survive(self):
        src = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("isImg" in src, "位图判定仍要存在（视觉路径依赖它）")
        self.assertTrue("is3d" in src, "3D 导入项目判定仍要存在")

    def test_c4_png_hint_no_longer_targets_dwg(self):
        src = APP_JS.read_text(encoding="utf-8")
        # 现状：非位图（含 DWG）一律提示换 PNG。接线后该提示只能留给 blocked_other/blocked_3d，
        # 因此源码里不得再出现"把 DWG 当成需要转 PNG"的判定组合。
        self.assertTrue(re.search(r"isImg\s*\?\s*\"\"\s*:.*" + re.escape(PNG_HINT), src) is None,
                        "非位图占位文案不得继续覆盖 DWG（DWG 不再需要 PNG）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
