"""红测：会话里「我：…」回声必须与 Agent 输出成对 —— 被拒的动作连回声都不发。

Spec：`docs/specs/chat-echo-must-pair-with-agent-output.md`

用户原话（2026-09-23）：

> 点击解析图纸的时候只有用户气泡没有 agent 输出气泡 要有就都要有 要没有就都没有
> 不能一连串用户气泡没有 agent 气泡

现状缺口（代码级，都可指到行）：
  · `tech-board-runtime.js::runEntry()` 在 `entry.run()` **之前**就发 `TASK_PROGRESS`（带 `prompt`），
    动作回 `{ok:false, code:"not-ready"|"busy"}` 时再发 `TASK_FAILED`（同 runId）；
  · `agent-chat.js:1781 renderTaskProgress()` 第一句就是 `echoTaskPrompt(detail)`：不论后面会不会有
    Agent 输出，先把用户气泡落下去；
  · 同函数 `:1825` `const hasContent = log.length > 0 || existingCard || Boolean(blockedReason);`
    + `if (!hasContent && !streamLength) return;` ⇒ 没有进度行的被拒回执**不建卡、也没有别的 Agent 行**；
  · 结果：会话里留下「我：帮我解析这张图纸。」，下面空空如也；连点几次就是一串用户气泡。

纪律：`node -e` 抽具名函数体真跑（纯函数）+ 源码守卫；不起服务、不发 HTTP、不连 PG / SQLite、
不写业务数据、不改业务实现（本文件只读源码）。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHAT_JS = ROOT / "tech_app" / "frontend" / "agent-chat.js"
RUNTIME_JS = ROOT / "tech_app" / "frontend" / "tech-board-runtime.js"

ECHO_ALLOWED = "taskEchoAllowed"
REJECTED = "isRejectedBoardCode"
REJECTED_CODES = ["not-ready", "loading", "busy", "no-project", "no-part",
                  "no-navigation", "not-parsed"]
QUIET_LITERAL = 'const QUIET_BOARD_CODES = ["detached", "note-target-missing", "missing-comment", "no-selection"];'
INTERRUPTED_LITERAL = 'const INTERRUPTED_CODES = ["interrupted", "detached", "timeout"];'
STANDALONE_SYSTEM_LINE = ("当前还不能解析：请先在 ＋ →「补充需求图纸」里上传需求原图并创建评估任务。")

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
const cases = JSON.parse(mode);
eval(fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a) + "").join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def run_cases(name: str, cases, timeout: int = 60):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(CHAT_JS), name, json.dumps(cases)],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(CHAT_JS), name, "body"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def call(name: str, args):
    payload = run_cases(name, [args])
    if payload.get("missing"):
        raise AssertionError("agent-chat.js 缺少纯函数 %s()（Spec §2）" % name)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def echo_allowed(detail):
    return call(ECHO_ALLOWED, [detail])


# --------------------------------------------------------------------------- #
# E 组：回声判定（纯函数）
# --------------------------------------------------------------------------- #
class EEchoAllowed(unittest.TestCase):
    def test_e1_a_real_run_echoes(self):
        for detail in ({"prompt": "帮我解析这张图纸。"},
                       {"prompt": "跑一次成本测算：全部。", "code": ""},
                       {"prompt": "刷新一下。", "code": "some-other-code"}):
            self.assertTrue(echo_allowed(detail),
                            "真的开始执行的动作要有回声（Spec §2.2）：%r" % (detail,))

    def test_e2_rejected_callbacks_do_not_echo(self):
        for code in REJECTED_CODES:
            self.assertFalse(echo_allowed({"prompt": "帮我解析这张图纸。", "code": code}),
                             "被拒回执（%s）不许回声（Spec §2.1/§2.2）" % code)

    def test_e3_without_a_prompt_there_is_no_echo(self):
        for detail in ({}, {"prompt": ""}, {"prompt": "   "}, {"prompt": None},
                       {"prompt": "\t\n"}, {"code": "not-ready"}):
            self.assertFalse(echo_allowed(detail),
                             "没有那句话就没有回声（Spec §2.2）：%r" % (detail,))

    def test_e4_junk_inputs_do_not_raise(self):
        for detail in (None, "帮我解析", [], 42, True):
            self.assertFalse(echo_allowed(detail),
                             "非法入参回 false 且不抛异常（Spec §2.2）：%r" % (detail,))

    def test_e5_rejected_code_helper(self):
        for code in REJECTED_CODES:
            self.assertTrue(call(REJECTED, [code]), "闭集里的 %s 必须判为被拒" % code)
        for code in ("", None, "action-failed", "timeout", "detached", "NOT-READY"):
            self.assertFalse(call(REJECTED, [code]),
                             "闭集外的 %r 不许被判成被拒（Spec §2.1）" % (code,))

    def test_e6_closed_set_is_declared_once(self):
        src = read_text(CHAT_JS)
        self.assertIn("REJECTED_BOARD_CODES", src, "被拒闭集必须有唯一的常量（Spec §2.1）")
        for code in REJECTED_CODES:
            self.assertIn('"%s"' % code, src, "闭集里缺 %s（Spec §2.1）" % code)
        self.assertEqual(1, src.count("REJECTED_BOARD_CODES = ["),
                         "被拒闭集只许声明一处（Spec §2.1）")

    def test_e7_pure_function_has_no_dom(self):
        body = function_body(ECHO_ALLOWED)
        self.assertTrue(body, "agent-chat.js 缺少纯函数 %s()（Spec §2.2）" % ECHO_ALLOWED)
        for token in ("document.", "window.", "fetch(", "localStorage", "sessionStorage"):
            self.assertNotIn(token, body, "回声判定必须是纯函数（Spec §2.2）")


# --------------------------------------------------------------------------- #
# R 组：接线与成对性
# --------------------------------------------------------------------------- #
class REchoPairsWithOutput(unittest.TestCase):
    def test_r1_render_task_progress_uses_the_decision(self):
        body = function_body("renderTaskProgress")
        self.assertTrue(body, "agent-chat.js 缺少 renderTaskProgress()")
        self.assertIn(ECHO_ALLOWED + "(", body,
                      "回声前必须走同一份判定（Spec §2.3）")

    def test_r2_echo_counts_as_content(self):
        body = function_body("renderTaskProgress")
        match = re.search(r"hasContent\s*=([^;]*);", body)
        self.assertIsNotNone(match, "renderTaskProgress() 里找不到 hasContent 判定")
        self.assertIn("echo", match.group(1),
                      "发过回声就必须算作有内容（否则回声留下、Agent 出去：Spec §2.3）")

    def test_r3_echo_is_placed_above_this_card(self):
        body = function_body("renderTaskProgress")
        self.assertIn("echoTaskPrompt(detail, card)", body,
                      "回声必须带着这一张卡调（Spec §2.3）")

    def test_r4_decision_precedes_the_echo(self):
        body = function_body("renderTaskProgress")
        at_decide = body.find(ECHO_ALLOWED + "(")
        at_echo = body.find("echoTaskPrompt(")
        self.assertGreaterEqual(at_decide, 0, "还没有回声判定（Spec §2.3）")
        self.assertGreaterEqual(at_echo, 0, "找不到回声调用")
        self.assertLess(at_decide, at_echo,
                        "判定必须排在回声之前（Spec §2.3）")

    def test_r5_echo_helper_takes_the_anchor(self):
        body = function_body("echoTaskPrompt")
        self.assertTrue(body, "agent-chat.js 缺少 echoTaskPrompt()")
        self.assertIn("before", body,
                      "第二形参必须是这条会话流里的落位锚点（Spec §2.3）")
        self.assertTrue("addUser(text, before)" in body or "beginUserTurn(text, before)" in body,
                        "回声要用 before 落位（插在这张卡的上方）（Spec §2.3）")

    def test_r6_empty_card_rule_is_kept(self):
        body = function_body("renderTaskProgress")
        self.assertIn("log.length", body,
                      "既有'没有真实执行内容不建卡'的纪律不许被本批弄丢（Spec §2.4）")
        self.assertIn("existingCard", body, "既有卡去重逻辑不许被本批弄丢（Spec §2.4）")


# --------------------------------------------------------------------------- #
# G 组：护栏
# --------------------------------------------------------------------------- #
class GGuards(unittest.TestCase):
    def test_g1_board_still_carries_the_prompt(self):
        text = read_text(RUNTIME_JS)
        self.assertIn("prompt: resolveActionPrompt(entry, payload)", text,
                      "看板载荷照旧带 prompt（既有红测的载荷契约，Spec §2.4）")

    def test_g2_quiet_and_interrupted_sets_are_verbatim(self):
        src = read_text(CHAT_JS)
        self.assertIn(QUIET_LITERAL, src, "QUIET_BOARD_CODES 逐字不改（Spec §2.4）")
        self.assertIn(INTERRUPTED_LITERAL, src, "INTERRUPTED_CODES 逐字不改（Spec §2.4）")

    def test_g3_replay_still_skips_the_echo(self):
        body = function_body("echoTaskPrompt")
        self.assertIn("replayingHistory", body,
                      "回放期间不补回声（既有纪律，Spec §2.4）")

    def test_g4_standalone_page_system_line_is_kept(self):
        src = read_text(CHAT_JS)
        self.assertIn(STANDALONE_SYSTEM_LINE, src,
                      "独立 2.1 页的系统行逐字保留（Spec §2.4）")

    def test_g5_human_input_still_gets_a_bubble_primitive(self):
        src = read_text(CHAT_JS)
        self.assertIn("function addUser(text, before)", src,
                      "真人输入 / 回声共用的气泡原语不许改名或删掉（Spec §2.4）")


if __name__ == "__main__":
    unittest.main()
