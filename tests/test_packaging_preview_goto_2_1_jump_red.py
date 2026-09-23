r"""红测：预览里那个「去 2.1 跑图纸解析」按钮，点了不真的去 2.1（只刷了面板、没切面板）。

Spec：`docs/specs/packaging-preview-goto-2-1-jump.md`

症状（2026-09-23 工作副本只读）：
  · 任务文件里点一份还没解析过的 DWG，预览逐字给出「这份图纸还没有解析结果，请先到 2.1 跑一次图纸解析。」
    底下一个按钮「去 2.1 跑图纸解析」；
  · 点它只 `window.CadFilePreview.close(); loadDrawingFlowPanel();` —— `loadDrawingFlowPanel()` 只
    `fetchDrawingFlowState()` + `renderDrawingFlowPanel()`，**刷面板不切面板** ⇒ 用户停在原地，
    右栏还是 3D 空框；
  · 进入图纸链路的既有口径是 `enterDrawingFlowPanes()`（一键解析与 `openProject()` 都用
    `try { enterDrawingFlowPanes(); } catch (error) { /* 纯展示 */ }`），那个按钮没接上。

纪律：只读源码 / 抽函数体真跑 / `node --check`；不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
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
CHAT_JS = ROOT / "tech_app" / "frontend" / "agent-chat.js"

PREVIEW_FN = "openFilePreview"
ENTER_FN = "enterDrawingFlowPanes"
LOAD_FN = "loadDrawingFlowPanel"
GUARD = 'currentDrawingEntry === "drawing_flow"'
KIND_FN = "filePreviewKind"

GOTO_TEXT = "去 2.1 跑图纸解析"
NO_PARSE_COPY = "这份图纸还没有解析结果，请先到 2.1 跑一次图纸解析。"

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
const fn = extract(process.argv[3]);
console.log(JSON.stringify({ missing: !fn, body: fn || "" }));
"""


def read_text(path):
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def function_body(name):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 抽函数体失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少函数 %s()（Spec §1）" % name)
    return str(payload.get("body") or "")


def brace_block(text, start):
    """从 text[start] 处的 `{` 起做花括号配对，返回（含首尾括号的）整块文本。"""
    at = text.index("{", start)
    depth = 0
    for j in range(at, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[at:j + 1]
    raise AssertionError("花括号不配对：%s" % text[start:start + 80])


def click_handler(body):
    """那个按钮的 click 回调体（Spec §C1：点它时该做什么）。"""
    at = body.find('goto.addEventListener("click"')
    if at < 0:
        raise AssertionError("openFilePreview() 里没有 goto 的 click 绑定（Spec §C1）")
    return brace_block(body, at)


# --------------------------------------------------------------------------- #
# A 组：那个按钮要真的走既有跳转口径（实现前红）
# --------------------------------------------------------------------------- #
class AGoToDrawingFlow(unittest.TestCase):
    def test_a1_handler_calls_the_existing_jump_routine(self):
        handler = click_handler(function_body(PREVIEW_FN))
        self.assertIn(ENTER_FN + "()", handler,
                      "点「去 2.1」必须调既有跳转口径 %s()（Spec §C1），不许新写第二套切面板逻辑"
                      % ENTER_FN)

    def test_a2_jump_is_wrapped_in_try_catch_and_guarded(self):
        handler = click_handler(function_body(PREVIEW_FN))
        self.assertRegex(handler, r"try\s*\{[\s\S]*?" + ENTER_FN + r"\(\)",
                         "切面板是纯展示，必须包在 try 里（Spec §C2，与 openProject() 同一写法）")
        call = handler.find(ENTER_FN + "()")
        self.assertGreaterEqual(handler.find("catch", call), 0,
                                "切面板的 try 要有 catch，不许裸调（Spec §C2）")
        # 既有口径（Spec `packaging-2-1-first-paint-must-be-2d-not-3d.md` §2.3）：每一处
        # `enterDrawingFlowPanes()` 都必须在图纸链路守卫里，不许误伤视觉链路。
        self.assertIn(GUARD, handler[:call],
                      "这一处调用也要落在 %s 守卫之内（Spec §C1/§C2）" % GUARD)

    def test_a5_call_site_sits_inside_the_400_char_guard_window(self):
        # 与既有 `test_packaging_2_1_first_paint_is_2d_not_3d_red.py::a3` 同一条口径：
        # 全文件每一处调用点往前 400 字符内必须出现守卫（覆盖新加的这一处）。
        text = read_text(APP_JS)
        pattern = r"(?<!function )" + re.escape(ENTER_FN) + r"\(\);"
        matches = list(re.finditer(pattern, text))
        self.assertGreater(len(matches), 2, "应当至少三处调用点（一键解析 / 进项目 / 预览出口）")
        for match in matches:
            head = text[max(0, match.start() - 400):match.start()]
            self.assertIn(GUARD, head,
                          "调用点 %d 不在守卫内（Spec §C1/§C2）" % match.start())

    def test_a3_close_then_jump_then_refresh_order(self):
        handler = click_handler(function_body(PREVIEW_FN))
        close_at = handler.find("CadFilePreview.close()")
        jump_at = handler.find(ENTER_FN + "()")
        read_at = handler.find(LOAD_FN + "()")
        self.assertGreaterEqual(close_at, 0, "先关预览（顺序不变，Spec §C1/§C2）")
        self.assertGreaterEqual(jump_at, 0, "要切面板（Spec §C1）")
        self.assertGreaterEqual(read_at, 0, "仍然要刷新链路状态（Spec §C2）")
        self.assertLess(close_at, jump_at, "顺序：先关预览、再切面板（Spec §C1）")
        self.assertLess(jump_at, read_at, "顺序：先切、再读（Spec §C2）")

    def test_a4_handler_body_is_only_the_goto_button(self):
        body = function_body(PREVIEW_FN)
        handler = click_handler(body)
        self.assertIn('goto.className = "file-preview-goto"', body,
                      "那个按钮仍然是 file-preview-goto（Spec §C3）")
        self.assertIn('goto.textContent = "%s"' % GOTO_TEXT, body,
                      "按钮文字逐字不变：%s（Spec §C3）" % GOTO_TEXT)
        self.assertNotIn("fetch(", handler,
                          "出口只做切面板 + 读回，不许在回调里自己发请求（Spec §C2）")


# --------------------------------------------------------------------------- #
# B 组：护栏（现状即绿，不许回退）
# --------------------------------------------------------------------------- #
class BGuards(unittest.TestCase):
    def test_b1_no_parse_copy_verbatim(self):
        body = function_body(PREVIEW_FN)
        self.assertIn('"%s"' % NO_PARSE_COPY, body,
                      "「还没有解析结果」那句逐字不变（Spec §C3）")

    def test_b2_object_url_only_for_image_or_pdf(self):
        text = read_text(APP_JS)
        self.assertEqual(text.count("URL.createObjectURL"), 1,
                         "全文件只许有一处 createObjectURL（Spec §C3）")
        self.assertRegex(text, r'if \(kind === "image" \|\| kind === "pdf"\)\s*'
                               r'filePreviewUrl = URL\.createObjectURL\(blob\);',
                         "createObjectURL 必须只在 image / pdf 那一条（Spec §C3）")

    def test_b3_drawing_branch_builds_no_object_url(self):
        body = function_body(PREVIEW_FN)
        start = body.find('if (kind === "drawing")')
        end = body.find('if (kind === "image" || kind === "pdf")')
        self.assertGreaterEqual(start, 0, "drawing 分支还在（Spec §C3）")
        self.assertGreater(end, start, "drawing 分支在前、image/pdf 在后（Spec §C3）")
        self.assertNotIn("createObjectURL", body[start:end],
                         "图纸分支不许建 object URL（Spec §C3）")

    def test_b4_cad_file_preview_contract(self):
        text = read_text(APP_JS)
        self.assertIn("window.CadFilePreview = { open: openFilePreview, close: closeFilePreview };",
                      text, "预览契约不动（Spec §C3）")

    def test_b5_preview_has_single_implementation(self):
        self.assertNotIn(ENTER_FN, read_text(CHAT_JS),
                         "预览只有一份实现：agent-chat.js 里不许出现 %s（Spec §C3）" % ENTER_FN)

    def test_b6_app_js_parses(self):
        proc = subprocess.run(["node", "--check", str(APP_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, "app.js 语法要过：%s" % (proc.stderr or "")[:400])

    def test_b7_enter_drawing_flow_panes_still_defined(self):
        text = read_text(APP_JS)
        self.assertIn("function " + ENTER_FN + "()", text,
                      "既有跳转口径仍在，且本批不改它（Spec §C1/§3.1）")


# --------------------------------------------------------------------------- #
# C 组：护栏（切面板口径与预览判定不许被顺手改掉）
# --------------------------------------------------------------------------- #
class CGuards(unittest.TestCase):
    def test_c1_jump_routine_keeps_hiding_the_3d_viewer(self):
        body = function_body(ENTER_FN)
        self.assertIn('$("viewer")', body, "既有口径仍隐藏 #viewer（Spec §C1/§3.1）")
        self.assertIn("viewer.hidden = true", body, "既有口径仍隐藏 #viewer（Spec §C1/§3.1）")

    def test_c2_load_drawing_flow_panel_still_only_refreshes(self):
        body = function_body(LOAD_FN)
        self.assertIn("fetchDrawingFlowState()", body, "刷面板仍读链路状态（Spec §C1）")
        self.assertIn("renderDrawingFlowPanel(", body, "刷面板仍画面板（Spec §C1）")

    def test_c3_preview_kind_branches_unchanged(self):
        body = function_body(KIND_FN)
        for kind in ('"drawing"', '"image"', '"pdf"', '"model"', '"text"', '"other"'):
            self.assertIn("return " + kind, body,
                          "五条既有分支一个不动：%s（Spec §C3）" % kind)


if __name__ == "__main__":
    unittest.main()
