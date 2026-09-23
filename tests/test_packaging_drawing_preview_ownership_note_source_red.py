r"""红测：任务文件预览里那句「哪一张图纸」的归属说明，现在永远出不来（源名字喂不进去）。

Spec：`docs/specs/packaging-drawing-preview-ownership-note-source.md`

症状（2026-09-23 工作副本只读）：
  · `## 487` 之后，任务文件里点 DWG 已经能看到那张整张平面图；
  · 但 Spec `packaging-task-file-dwg-opens-the-whole-plan.md` §C5 的归属说明**任何情况下都不出现**：
    `openFilePreview()` 的 drawing 分支读的是 `payload.source_filename || payload.source.filename`，
    而 `GET /requirement/packaging-geometry` 的 `source` 块实测只有
    `ir_id` / `ir_hash` / `authority_file_hash` / `authority_sheet` ⇒ 两个取值恒为 `""`
    ⇒ `fileDrawingOwnershipNote(file, "")` 恒回 `""`；
  · 真名前端本来就有：`openProject()` 的 `data.meta.source_filename`（= `/files` 里
    「需求原图（解析依据）」那一行的 `name`），现在只用来判链路、没留档。

纪律：只读源码 / `node -e` 抽纯函数真跑；不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
CHAT_JS = ROOT / "tech_app" / "frontend" / "agent-chat.js"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

FUNC = "requirementSourceFilename"
VAR = "currentRequirementSourceFilename"
PREVIEW_FN = "openFilePreview"
PROJECT_FN = "openProject"
NOTE_FN = "fileDrawingOwnershipNote"

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
eval(fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(argText).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def read_text(path):
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


def call(name, args):
    payload = _run_node([str(APP_JS), name, json.dumps(json_safe([args]))])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2/C1）" % name)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def function_body(name):
    payload = _run_node([str(APP_JS), name, "body"])
    return "" if payload.get("missing") else str(payload.get("body") or "")


# --------------------------------------------------------------------------- #
# A 组：`requirementSourceFilename(projectBody)`（node 真跑）
# --------------------------------------------------------------------------- #
class ASourceFilename(unittest.TestCase):
    def test_a1_reads_the_meta_source_filename(self):
        self.assertEqual("酒盒.dwg", call(FUNC, [{"meta": {"source_filename": "酒盒.dwg"}}]),
                         "项目体里的 `meta.source_filename` 就是「本次解析的图纸」（Spec §1/§C1）")

    def test_a2_trims_whitespace(self):
        self.assertEqual("酒盒.dwg", call(FUNC, [{"meta": {"source_filename": "  酒盒.dwg "}}]),
                         "去首尾空白（Spec §C1）")

    def test_a3_missing_is_empty_and_never_invented(self):
        for body in ({}, None, [], "酒盒.dwg", 3, {"meta": {}}, {"meta": None}, {"meta": "酒盒.dwg"},
                     {"meta": {"source_filename": ""}}, {"meta": {"source_filename": "   "}},
                     {"meta": {"source_filename": 123}}, {"meta": {"source_filename": True}},
                     {"source_filename": "酒盒.dwg"}):
            self.assertEqual("", call(FUNC, [body]),
                             "读不到就是空串 —— 不编名字、不回落别的字段（Spec §C1）：%r" % (body,))

    def test_a4_is_a_pure_function(self):
        body = function_body(FUNC)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §C1）" % FUNC)
        for token in ("document", "window.", "fetch(", "localStorage", "sessionStorage"):
            self.assertNotIn(token, body, "%s 不许引用 %s（要被 node 直接执行）" % (FUNC, token))


# --------------------------------------------------------------------------- #
# B 组：接线（留档 + 喂给那句归属说明）
# --------------------------------------------------------------------------- #
class BWiring(unittest.TestCase):
    def test_b1_project_open_records_it(self):
        body = function_body(PROJECT_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % PROJECT_FN)
        self.assertIn(VAR, body,
                      "打开项目时要把「本次解析的图纸名」留档（Spec §2/C2：%s）" % VAR)
        self.assertIn(FUNC, body, "留档的取值要走纯函数 %s()（Spec §C2）" % FUNC)

    def test_b2_the_variable_is_module_level(self):
        src = read_text(APP_JS)
        self.assertRegex(src, r"(?:let|var|const)\s+%s\b" % VAR,
                         "`%s` 要是模块级变量（Spec §C2）" % VAR)

    def test_b3_preview_feeds_the_note(self):
        body = function_body(PREVIEW_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % PREVIEW_FN)
        self.assertIn(NOTE_FN + "(", body,
                      "归属说明仍由纯函数决定（Spec `…-dwg-opens-the-whole-plan.md` §C5）")
        self.assertIn(VAR, body,
                      "drawing 分支要把「本次解析的图纸名」真的喂进去（Spec §2/C3）")

    def test_b4_preview_still_reads_the_payload_first(self):
        body = function_body(PREVIEW_FN)
        self.assertIn("source_filename", body,
                      "响应里将来若带上名字，仍优先用它，别只认留档（Spec §C3）")


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

    def test_c2_note_caliber_is_unchanged(self):
        self.assertEqual("", call(NOTE_FN, [{"name": "酒盒.dwg"}, "酒盒.dwg"]),
                         "就是本次解析那张时不许啰嗦（Spec §C4）")
        note = call(NOTE_FN, [{"name": "另一张.dwg"}, "酒盒.dwg"])
        self.assertIn("本次解析", note, "别的图纸要写清归属（Spec §C4）")
        self.assertIn("酒盒.dwg", note, "归属说明要点出到底是哪一张（Spec §C4）")
        self.assertEqual("", call(NOTE_FN, [{"name": "另一张.dwg"}, ""]),
                         "源名字未知时不许编、也不许出这句（Spec §C4）")

    def test_c3_the_dock_does_not_keep_a_second_copy(self):
        chat = read_text(CHAT_JS)
        for token in (FUNC, VAR):
            self.assertNotIn(token, chat,
                             "两处入口共用一份预览：agent-chat.js 不许出现 %s（Spec §C4）" % token)

    def test_c4_backend_is_untouched(self):
        src = read_text(MAIN_PY)
        self.assertIn('"kind": "image"', src,
                      "后端 `/files` 的返回口径本批不动（Spec §C4）")
        self.assertNotIn(FUNC, src, "本批不开新接口、不把名字判定搬到服务端（Spec §C4）")

    def test_c5_the_geometry_payload_has_no_such_field(self):
        """Spec §1 的读数：响应里没有 `source_filename` —— 这条钉住"别指望后端给"。"""
        src = read_text(ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py")
        self.assertIn("authority_file_hash", src, "geometry 的 source 块还是那四个键（Spec §1）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
