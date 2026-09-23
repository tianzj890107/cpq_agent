r"""红测：任务文件里点 DWG，看到的是 2.1 未选中态那张「整张平面图」。

Spec：`docs/specs/packaging-task-file-dwg-opens-the-whole-plan.md`

用户原话（2026-09-23）：

> 在这个 任务文件 / × 输入资料 2 / 酒盒.dwg / 需求说明_Codex只点按钮版.txt
> 里面显示那个「未选中时仍是整张平面图」的那个平面图

现状缺口（HEAD `89f0bc0` 工作副本只读）：
  · `/api/projects/{pid}/files` 把需求原图标成 `{"kind": "image", "url": "…/source"}`
    （`main.py:1533-1535`）—— 哪怕文件名是 `.dwg`；
  · `filePreviewKind()`（`app.js:6344-6351`）先判 `file.kind === "image"` ⇒ `酒盒.dwg` 直接进
    image 分支：`URL.createObjectURL(blob)` + `<img>`（`app.js:6420-6427`）⇒ 碎图 / 空白；
  · 全文件没有 `drawing` 这一分类，也没有任何一处把预览接到
    `/requirement/packaging-geometry` 那张整张平面图上；
  · 那张图本身是现成的：`renderPackagingCadScene()` + `packagingCadSceneEntitySvg()` +
    `packagingCadPlanViewBox()`（`app.js:2140-2220`）—— 本批只要求"复用"，不要求新写渲染。

纪律：只读源码 / Spec + `node -e` 抽纯函数真跑；不起服务、不发 HTTP、不连 PG / 34、不写业务数据；
不读真实 DWG。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
CHAT_JS = ROOT / "tech_app" / "frontend" / "agent-chat.js"

DRAWING_FN = "fileIsDrawing"
KIND_FN = "filePreviewKind"
HTML_FN = "fileDrawingPreviewHtml"
NOTE_FN = "fileDrawingOwnershipNote"
OPEN_FN = "openFilePreview"

NO_PARSE_COPY = "这份图纸还没有解析结果，请先到 2.1 跑一次图纸解析。"

COLOR_CUT = "#1f6feb"
COLOR_CREASE = "#d29922"

EXTRACT_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8").replace(/\u0000/g, "");
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
const cases = JSON.parse(mode, (key, value) => (value === "__NaN__" ? NaN : value));
const argText = value => (typeof value === "number" && Number.isNaN(value)) ? "NaN" : JSON.stringify(value);
// `filePreviewKind()` / `fileIsDrawing()` 依赖模块级正则与常量：把要用到的那几条一起带进来。
const prelude = [
  'const IMAGE_FILE_PATTERN = /\\.(png|jpe?g|gif|webp|svg|bmp)$/i;',
  'const TEXT_FILE_PATTERN = /\\.(txt|md|csv|json|log|yaml|yml)$/i;',
].join("\n");
eval(prelude);
eval(fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(argText).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def json_safe(value):
    if isinstance(value, float) and math.isnan(value):
        return "__NaN__"
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    return value


def _run_node(argv, timeout=60):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-"] + argv,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def call(name: str, args):
    payload = _run_node([str(APP_JS), name, json.dumps(json_safe([args]))])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §C1/§C2）" % name)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def function_body(name: str) -> str:
    payload = _run_node([str(APP_JS), name, "body"])
    return "" if payload.get("missing") else str(payload.get("body") or "")


# --------------------------------------------------------------------------- #
# 夹具：一张「整张图」的 packaging-geometry 响应（三图元，跨两 role）
# --------------------------------------------------------------------------- #
def _entity(entity_id, layer, role, points, *, closed=False):
    return {"cad_entity_id": entity_id, "kind": "line", "layer": layer, "role": role,
            "closed": closed, "points": points,
            "bbox": [min(p[0] for p in points), min(p[1] for p in points),
                     max(p[0] for p in points), max(p[1] for p in points)]}


def scene_doc():
    return {"cad_scene": {
        "entity_total": 3,
        "layer_visibility": {"全穿刀": True, "压线 Crease": True},
        "entities": [
            _entity("e:1", "全穿刀", "cut", [[0, 0], [200, 0]]),
            _entity("e:2", "压线 Crease", "crease", [[10, 20], [180, 20]]),
            _entity("e:3", "全穿刀", "cut", [[0, 0], [200, 0], [200, 120], [0, 120]], closed=True),
        ]}}


# --------------------------------------------------------------------------- #
# A 组：分类与渲染（node 真跑）
# --------------------------------------------------------------------------- #
class ADrawingKind(unittest.TestCase):
    def test_a1_dwg_is_a_drawing_even_when_kind_says_image(self):
        self.assertEqual("drawing", call(KIND_FN, [{"name": "酒盒.dwg", "kind": "image",
                                                    "url": "/api/projects/p/source"}]),
                         "后端把需求原图标成 `kind:\"image\"`，前端不许照着当位图（Spec §C1/§1）")

    def test_a2_dxf_and_case_are_recognised(self):
        for name in ("圆盘盒.dxf", "圆盘盒.DXF", "P01 面板 · DXF"):
            self.assertEqual("drawing", call(KIND_FN, [{"name": name, "kind": "doc"}]),
                             "%s 也该按图纸走（Spec §C1）" % name)

    def test_a3_other_kinds_are_unchanged(self):
        cases = {"a.png": "image", "a.PDF": "pdf", "a.txt": "text", "a.stl": "model",
                 "a.bin": "other", "": "other"}
        for name, want in cases.items():
            self.assertEqual(want, call(KIND_FN, [{"name": name}]),
                             "%s 的分类不许被本批改动（Spec §C7）" % (name or "（空名）"))

    def test_a4_file_is_drawing_only_looks_at_the_name(self):
        for name, want in (("酒盒.dwg", True), ("酒盒.DWG", True), ("x.dxf", True),
                           ("x.txt", False), ("x.dwg.txt", False), ("", False)):
            got = call(DRAWING_FN, [{"name": name, "kind": "image"}])
            self.assertEqual(want, got, "fileIsDrawing(%r) 只看后缀（Spec §C1）" % name)

    def test_a5_preview_html_draws_the_whole_plan(self):
        svg = call(HTML_FN, [scene_doc()])
        self.assertIn("<svg", svg, "整张平面图要是 svg（Spec §C2）")
        self.assertIn("viewBox", svg, "viewBox 由取框函数产出（Spec §C2）")
        self.assertEqual(3, len(re.findall(r"<(polyline|polygon|text)\b", svg)),
                         "整张图有几条图元就画几条（Spec §C2）")

    def test_a6_colours_are_the_same_table(self):
        svg = call(HTML_FN, [scene_doc()])
        strokes = re.findall(r'stroke="([^"]*)"', svg)
        self.assertIn(COLOR_CUT, strokes, "cut 用那张表的颜色（Spec §C2）")
        self.assertIn(COLOR_CREASE, strokes, "crease 用那张表的颜色（Spec §C2）")

    def test_a7_no_selection_state_in_the_preview(self):
        svg = call(HTML_FN, [scene_doc()])
        for token in ("is-highlighted", "is-dimmed"):
            self.assertNotIn(token, svg,
                             "预览是「未选中」那张整图，不许带选中态的高亮/淡化（Spec §C2）")

    def test_a8_nothing_to_draw_is_an_empty_string(self):
        for doc in ({}, {"cad_scene": {"entities": []}}, {"cad_scene": None}, None):
            self.assertEqual("", call(HTML_FN, [doc]),
                             "没有场景就回空串，由调用方给文案（Spec §C2/C4）")

    def test_a9_ownership_note_only_when_it_differs(self):
        self.assertEqual("", call(NOTE_FN, [{"name": "酒盒.dwg"}, "酒盒.dwg"]),
                         "就是本次解析那张时不许啰嗦（Spec §C5）")
        note = call(NOTE_FN, [{"name": "另一张.dwg"}, "酒盒.dwg"])
        self.assertIn("本次解析", note, "点的是别的图纸时要写清归属（Spec §C5）")
        self.assertIn("酒盒.dwg", note, "归属说明要点出到底是哪一张（Spec §C5）")

    def test_a10_all_four_are_pure_functions(self):
        for name in (DRAWING_FN, KIND_FN, HTML_FN, NOTE_FN):
            body = function_body(name)
            self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §C1/§C2/§C5）" % name)
            for token in ("document", "window.", "fetch(", "localStorage", "sessionStorage"):
                self.assertNotIn(token, body, "%s 不许引用 %s（要被 node 直接执行）" % (name, token))


# --------------------------------------------------------------------------- #
# B 组：接线（预览函数体）
# --------------------------------------------------------------------------- #
class BPreviewWiring(unittest.TestCase):
    def test_b1_preview_has_a_drawing_branch(self):
        body = function_body(OPEN_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % OPEN_FN)
        self.assertIn(HTML_FN, body,
                      "drawing 分支必须复用整张平面图的拼接函数（Spec §C2）")

    def test_b2_preview_reads_the_existing_geometry_endpoint(self):
        body = function_body(OPEN_FN)
        self.assertTrue("packagingCadSceneEndpoint" in body or "packaging-geometry" in body,
                        "图必须来自既有 /requirement/packaging-geometry，不许新开接口（Spec §C2）")

    def test_b3_not_parsed_yet_tells_the_next_step(self):
        body = function_body(OPEN_FN)
        self.assertIn(NO_PARSE_COPY, body,
                      "还没有解析结果时必须逐字给这句（Spec §C4）")

    def test_b4_ownership_note_is_used(self):
        body = function_body(OPEN_FN)
        self.assertIn(NOTE_FN, body, "哪一张图纸的归属说明要走纯函数（Spec §C5）")

    def test_b5_kind_switch_is_still_the_single_entry(self):
        body = function_body(OPEN_FN)
        self.assertIn(KIND_FN, body, "分类仍只由 %s() 决定（Spec §C1）" % KIND_FN)

    def test_b6_the_dock_does_not_copy_a_second_implementation(self):
        chat = read_text(CHAT_JS)
        for token in (HTML_FN, "packagingCadSceneEndpoint", "packaging-geometry"):
            self.assertNotIn(token, chat,
                             "两处入口共用一份预览：agent-chat.js 不许出现 %s（Spec §C6）" % token)


# --------------------------------------------------------------------------- #
# C 组：护栏（现状即绿）
# --------------------------------------------------------------------------- #
class CGuards(unittest.TestCase):
    def test_c1_app_js_still_parses(self):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("未安装 node，跳过语法检查")
        proc = subprocess.run([node, "--check", str(APP_JS)], capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(0, proc.returncode, "app.js 语法错误：\n%s" % proc.stderr)

    def test_c2_other_preview_branches_survive(self):
        src = read_text(APP_JS)
        for token in ("该类型暂不支持预览。", "不在卡片内预览", "TEXT_PREVIEW_LIMIT",
                      "createObjectURL", "revokeObjectURL", "登录状态已失效"):
            self.assertIn(token, src, "既有预览能力丢了：%s（Spec §C7）" % token)

    def test_c3_preview_still_fetches_with_the_ticket(self):
        body = function_body(OPEN_FN)
        self.assertIn("fetch(file.url)", body,
                      "预览仍走同源带票 fetch，不发裸链接、不把 token 拼 URL（Spec §C7）")

    def test_c4_files_endpoint_caliber_is_untouched(self):
        main_py = read_text(ROOT / "tech_app" / "backend" / "main.py")
        self.assertIn('"kind": "image"', main_py,
                      "后端 `/files` 的返回口径本批不动 —— 前端必须自己按后缀判图纸（Spec §3 边界 3）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
