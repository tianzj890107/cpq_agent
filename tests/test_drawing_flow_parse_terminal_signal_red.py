"""红测：一键解析图纸的终态信号 + 2.1 零件可见（DWG / DXF 链路）。

Spec：`docs/specs/drawing-flow-parse-terminal-signal.md`

现状缺口（实测，不是推断）：

  · `tech_app/frontend/app.js:936-938` 的 drawing_flow 分支 `return null` 把
    `runDrawingFlowParse()` 的返回值丢掉；`app.js:2763-2766` 只按 `if (result)` 二分，
    于是**每次**都发 `task-failed` + 逐字文案「图纸解析未完成。」（与链路真实结果无关）；
  · 步骤载荷里的 `status` / `error_code` / `error_message` / `retryable` / `detail.action`
    前端一个都没用上 —— 「缺前置条件」与「真失败」在看板上长得一样；
  · `index.html:187` 的 `#tree` 硬编码「完成解析后显示零件清单」，drawing_flow 模式下
    `currentIR` 永远是空，左栏永远空白且不说原因。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写业务数据；
  · 两个新纯函数（`drawingFlowTerminalSignal` / `packagingPartsEmptyText`）用 `node`
    **实际执行**函数体（不是文本 grep）；接线类断言才用源码检查；
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
RUNTIME_JS = FRONTEND / "tech-board-runtime.js"
CHAT_JS = FRONTEND / "agent-chat.js"
INDEX_HTML = FRONTEND / "index.html"

RUN_ENDPOINT = "/drawing-flow/run"
LEGACY_EMPTY = "完成解析后显示零件清单"
LEGACY_FAILURE_TEXT = "图纸解析未完成。"

#: 红测直接抽函数体交给 node 执行（只认 `function name(...) {…}` 具名声明）。
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


def run_cases(source: pathlib.Path, name: str, cases, timeout: int = 60):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(source), name, json.dumps(cases)],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(source: pathlib.Path, name: str) -> str:
    """取具名函数体源码（缺失返回空串）——接线类断言的唯一取数口。"""
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(source), name, "body"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


# --------------------------------------------------------------------------- #
# 夹具：链路真实形状的状态（字段名与 packaging_drawing_flow 的透出口径一致）
# --------------------------------------------------------------------------- #
def flow(steps, run_id="run-9f1", status="completed"):
    return {"flow_version": "packaging-drawing-flow/1", "run_id": run_id,
            "status": status, "steps": list(steps)}


def step(step_id, status, code="", message="", retryable=None, action=None):
    row = {"step_id": step_id, "status": status, "error_code": code,
           "error_message": message, "detail": {}}
    if retryable is not None:
        row["retryable"] = retryable
    if action is not None:
        row["detail"]["action"] = action
    return row


ALL_DONE = [
    step("file_preflight", "completed"), step("dwg_convert", "completed"),
    step("cad_ir_parse", "completed"), step("packaging_semantics", "completed"),
    step("field_write", "completed"), step("pending_confirm", "completed"),
    step("downstream_prepare", "completed"),
]

BLOCKED_MESSAGE = "需求已提交（当前状态：approved），不能直接改写；请先退回草稿或为该项目新建一张需求草稿（缺前置条件，重试不会成功）"
BLOCKED_ACTION = "把这张需求单退回草稿，或为该项目新建一张需求草稿，再回到图纸解析重跑"

BLOCKED_STEPS = [
    step("file_preflight", "completed"), step("dwg_convert", "completed"),
    step("cad_ir_parse", "completed"), step("packaging_semantics", "completed"),
    step("field_write", "blocked", code="REQUIREMENT_NOT_EDITABLE",
         message=BLOCKED_MESSAGE, retryable=False, action=BLOCKED_ACTION),
    step("pending_confirm", "completed"), step("downstream_prepare", "completed"),
]

FAILED_STEPS = [
    step("file_preflight", "completed"),
    step("dwg_convert", "failed", code="DWG_CONVERT_FAILED",
         message="ODA 转换失败：源文件里没有可用的 2D 图元", retryable=True),
    step("cad_ir_parse", "pending"), step("packaging_semantics", "pending"),
    step("field_write", "pending"), step("pending_confirm", "pending"),
    step("downstream_prepare", "pending"),
]

UNAVAILABLE_STEPS = [
    step("file_preflight", "completed"),
    step("dwg_convert", "unavailable", code="PACKAGING_FLOW_DEPENDENCY_MISSING",
         message="图纸解析链路依赖的能力尚未就绪（cad_converter），请联系系统管理员",
         retryable=False),
]


class TerminalSignalCase(unittest.TestCase):
    """A / B 组共用的取数口：把 app.js 的纯函数交给 node 真跑。"""

    def signal(self, flow_state, error=None):
        payload = run_cases(APP_JS, "drawingFlowTerminalSignal",
                            [[flow_state, error]])
        if payload.get("missing"):
            self.fail("app.js 缺少纯函数 drawingFlowTerminalSignal()（Spec C1）")
        row = payload["results"][0]
        if not row.get("ok"):
            self.fail("drawingFlowTerminalSignal() 抛异常：%s" % row.get("error"))
        value = row.get("value")
        if not isinstance(value, dict):
            self.fail("drawingFlowTerminalSignal() 必须返回对象，实际 %r" % (value,))
        return value


# --------------------------------------------------------------------------- #
# A 组：终态判定表（C1）
# --------------------------------------------------------------------------- #
class ATerminalSignal(TerminalSignalCase):
    def test_a1_pure_function_exists_and_is_free_of_dom(self):
        body = function_body(APP_JS, "drawingFlowTerminalSignal")
        self.assertTrue(body, "app.js 缺少纯函数 drawingFlowTerminalSignal()（Spec C1）")
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body,
                             "终态判定必须是纯函数，不许碰 %s（Spec C6）" % token)

    def test_a2_all_completed_is_task_completed(self):
        value = self.signal(flow(ALL_DONE))
        self.assertEqual(value.get("event"), "task-completed",
                         "链路全完成必须报 task-completed（Spec C1 第 3 条）")

    def test_a3_blocked_reports_code_action_and_is_not_retryable(self):
        value = self.signal(flow(BLOCKED_STEPS, status="completed"))
        self.assertEqual(value.get("event"), "task-blocked",
                         "缺前置条件是 blocked，不是失败（Spec C1 第 1 条 / 分类学 C4）")
        self.assertEqual(value.get("code"), "REQUIREMENT_NOT_EDITABLE")
        self.assertEqual(value.get("message"), BLOCKED_MESSAGE,
                         "blocked 文案必须逐字取后端载荷，前端不许改口径")
        self.assertEqual(value.get("action"), BLOCKED_ACTION,
                         "blocked 必须带下一步动作（来自 detail.action）")
        self.assertIs(value.get("retryable"), False,
                      "blocked 不可重试：重试同一入口必然再失败")
        self.assertNotIn("请重试", str(value.get("message")),
                         "blocked 的文案不许出现『请重试』")

    def test_a4_failed_keeps_code_message_and_retryability(self):
        value = self.signal(flow(FAILED_STEPS, status="failed"))
        self.assertEqual(value.get("event"), "task-failed")
        self.assertEqual(value.get("code"), "DWG_CONVERT_FAILED")
        self.assertEqual(value.get("message"),
                         "ODA 转换失败：源文件里没有可用的 2D 图元")
        self.assertIs(value.get("retryable"), True)

    def test_a5_unavailable_is_failed_but_not_retryable(self):
        value = self.signal(flow(UNAVAILABLE_STEPS, status="failed"))
        self.assertEqual(value.get("event"), "task-failed")
        self.assertEqual(value.get("code"), "PACKAGING_FLOW_DEPENDENCY_MISSING")
        self.assertIs(value.get("retryable"), False,
                      "能力缺失是 retryable=false，前端不许自己改判成可重试")

    def test_a6_blocked_wins_over_failed(self):
        steps = [step("file_preflight", "completed")] + list(FAILED_STEPS[1:])
        steps[1] = step("dwg_convert", "failed", code="DWG_CONVERT_FAILED",
                        message="转换失败", retryable=True)
        steps[4] = step("field_write", "blocked", code="REQUIREMENT_NOT_EDITABLE",
                        message=BLOCKED_MESSAGE, retryable=False, action=BLOCKED_ACTION)
        value = self.signal(flow(steps, status="failed"))
        self.assertEqual(value.get("event"), "task-blocked",
                         "同时有 blocked 与 failed 时报 blocked（Spec C1 第 1 条优先级）")
        self.assertEqual(value.get("code"), "REQUIREMENT_NOT_EDITABLE")

    def test_a7_missing_flow_with_error_is_rejected(self):
        value = self.signal(None, "图纸解析请求失败（HTTP 500）")
        self.assertEqual(value.get("event"), "task-failed")
        self.assertEqual(value.get("code"), "FLOW_RUN_REJECTED")
        self.assertEqual(value.get("message"), "图纸解析请求失败（HTTP 500）")
        self.assertIs(value.get("retryable"), True)

    def test_a8_missing_flow_without_error_says_so(self):
        value = self.signal(None, None)
        self.assertEqual(value.get("event"), "task-failed")
        self.assertEqual(value.get("code"), "FLOW_STATE_MISSING")
        self.assertIn("没有返回状态", str(value.get("message")))

    def test_a9_deterministic(self):
        first = self.signal(flow(BLOCKED_STEPS))
        second = self.signal(flow(BLOCKED_STEPS))
        self.assertEqual(first, second, "同一入参两次判定必须逐字相同（Spec C6）")


# --------------------------------------------------------------------------- #
# B 组：接线（C2 / C3）
# --------------------------------------------------------------------------- #
class BWiring(unittest.TestCase):
    def test_b1_legacy_failure_literal_is_gone(self):
        src = read_text(APP_JS)
        self.assertTrue(LEGACY_FAILURE_TEXT not in src,
                        "「图纸解析未完成。」不得再出现在源码里（Spec C2）")

    def test_b2_background_uses_the_terminal_signal(self):
        body = function_body(APP_JS, "parseDrawingInBackground")
        self.assertTrue(body, "app.js 缺少 parseDrawingInBackground()")
        self.assertIn("drawingFlowTerminalSignal", body,
                      "后台链路必须经 drawingFlowTerminalSignal() 决定事件（Spec C2）")
        self.assertNotIn('if (result) parseDrawingSettle("task-completed")', body,
                         "不许再按返回值真假二分成功/失败（Spec C2）")

    def test_b3_drawing_flow_branch_returns_the_flow_terminal_state(self):
        body = function_body(APP_JS, "parseDrawing")
        self.assertTrue(body, "app.js 缺少 parseDrawing()")
        at = body.find('currentDrawingEntry === "drawing_flow"')
        self.assertGreaterEqual(at, 0, "parseDrawing() 必须有 drawing_flow 分支")
        window = body[at:at + 600]
        self.assertNotIn("return null", window,
                         "drawing_flow 分支不许把链路结果丢掉（Spec C2）")
        self.assertIn("return ", window, "drawing_flow 分支必须把终态交回后台链路")

    def test_b4_board_event_closed_set_has_task_blocked(self):
        src = read_text(RUNTIME_JS)
        self.assertTrue(re.search(r"TASK_BLOCKED\s*:\s*['\"]task-blocked['\"]", src),
                        "看板事件闭集必须新增 TASK_BLOCKED: 'task-blocked'（Spec C3）")

    def test_b5_publish_task_card_whitelist_includes_blocked(self):
        body = function_body(RUNTIME_JS, "publishTaskCard")
        self.assertTrue(body, "tech-board-runtime.js 缺少 publishTaskCard()")
        self.assertIn("TASK_BLOCKED", body,
                      "task-blocked 必须进 publishTaskCard 的白名单（Spec C3）")

    def test_b6_parent_shell_bridge_maps_blocked(self):
        src = read_text(CHAT_JS)
        self.assertTrue(re.search(r"['\"]task-blocked['\"]", src),
                        "父壳桥必须新增 task-blocked 分支（Spec C3）")
        self.assertTrue(re.search(r"status:\s*['\"]blocked['\"]", src),
                        "task-blocked 必须渲染成 status=blocked（Spec C3）")

    def test_b7_status_word_and_terminal_set_cover_blocked(self):
        src = read_text(CHAT_JS)
        self.assertTrue(re.search(r"blocked:\s*['\"]被阻断['\"]", src),
                        "状态词表必须有 blocked: \"被阻断\"（Spec C3）")
        body = function_body(CHAT_JS, "interruptRunningCards")
        self.assertTrue(body, "agent-chat.js 缺少 interruptRunningCards()")
        self.assertIn("blocked", body,
                      "已经是终态（含 blocked）的卡不许再被标成「中断」（Spec C3）")


# --------------------------------------------------------------------------- #
# C 组：2.1 零件可见（C4）
# --------------------------------------------------------------------------- #
class CPartsVisibility(unittest.TestCase):
    def empty_text(self, parts_doc, preconditions=None):
        payload = run_cases(APP_JS, "packagingPartsEmptyText",
                            [[parts_doc, preconditions or []]])
        if payload.get("missing"):
            self.fail("app.js 缺少纯函数 packagingPartsEmptyText()（Spec C4）")
        row = payload["results"][0]
        if not row.get("ok"):
            self.fail("packagingPartsEmptyText() 抛异常：%s" % row.get("error"))
        return row.get("value")

    def test_c1_pure_function_exists_and_is_free_of_dom(self):
        body = function_body(APP_JS, "packagingPartsEmptyText")
        self.assertTrue(body, "app.js 缺少纯函数 packagingPartsEmptyText()（Spec C4）")
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "空态文案必须是纯函数（Spec C6）")

    def test_c2_parts_present_means_no_empty_text(self):
        doc = {"parts": [{"part_code": "DWG-P01"}], "unavailable": [],
               "stats": {"part_total": 1}}
        self.assertEqual(self.empty_text(doc), "",
                         "有零件时不该再显示空态（Spec C4）")

    def test_c3_unavailable_reasons_are_verbatim(self):
        doc = {"parts": [], "unavailable": [
            {"code": "all_filtered", "message": "分量全部被过滤（图框 / 碎线）"},
            {"code": "no_unit", "message": "图纸单位未确认，不能给展开尺寸"}]}
        text = self.empty_text(doc)
        for fragment in ("分量全部被过滤（图框 / 碎线）",
                         "图纸单位未确认，不能给展开尺寸"):
            self.assertIn(fragment, text, "空态必须逐字显示服务端给的原因（Spec C4）")

    def test_c4_preconditions_carry_code_and_action(self):
        doc = {"parts": [], "unavailable": []}
        preconditions = [{
            "code": "REQUIREMENT_NOT_EDITABLE", "severity": "blocking",
            "message": "需求已提交（当前状态：approved），不能直接改写",
            "action": "把这张需求单退回草稿，或为该项目新建一张需求草稿，再回到图纸解析重跑"}]
        text = self.empty_text(doc, preconditions)
        self.assertIn("REQUIREMENT_NOT_EDITABLE", text,
                      "空态必须带前置条件码（Spec C4）")
        self.assertIn("把这张需求单退回草稿", text, "空态必须带下一步动作（Spec C4）")

    def test_c5_everything_empty_still_names_the_next_step(self):
        text = self.empty_text({"parts": [], "unavailable": []})
        self.assertTrue(text.strip(), "零件文档缺失时不许留空白态")
        self.assertIn("一键解析图纸", text, "空态必须说清下一步（Spec C4）")

    def test_c6_part_tree_renders_the_parts_document(self):
        body = function_body(APP_JS, "renderTree")
        self.assertTrue(body, "app.js 缺少 renderTree()")
        self.assertIn("packagingPartsEmptyText", body,
                      "零件树空态必须用 packagingPartsEmptyText()（Spec C4）")

    def test_c7_app_reads_the_packaging_parts_endpoint(self):
        src = read_text(APP_JS)
        self.assertTrue("packaging-parts" in src,
                        "2.1 零件树必须读零件文档（Spec C4 / 零件提取 Spec §5）")

    def test_c8_html_no_longer_hardcodes_the_placeholder(self):
        src = read_text(INDEX_HTML)
        self.assertTrue(LEGACY_EMPTY not in src,
                        "#tree 不许再硬编码「%s」（Spec C4）" % LEGACY_EMPTY)


# --------------------------------------------------------------------------- #
# D 组（绿护栏）：不得为了接新信号弄坏既有接线与语法
# --------------------------------------------------------------------------- #
class DRegressionGuards(unittest.TestCase):
    def test_d1_syntax_ok(self):
        for path in (APP_JS, RUNTIME_JS, CHAT_JS):
            proc = subprocess.run(["node", "--check", str(path)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, "%s 语法错误：%s" % (path, proc.stderr))

    def test_d2_vision_path_call_point_survives(self):
        src = read_text(APP_JS)
        self.assertTrue(re.search(r"`/api/projects/\$\{[^}]+\}/parse`", src),
                        "视觉路径 POST /parse 的调用点必须保留（Spec C5）")
        self.assertTrue("isImg" in src, "位图判定仍要存在（视觉路径依赖它）")

    def test_d3_step_table_fields_survive(self):
        body = function_body(APP_JS, "renderDrawingFlowPanel")
        self.assertTrue(body, "app.js 缺少 renderDrawingFlowPanel()")
        for token in ("error_code", "error_message", "DRAWING_FLOW_STATUS_LABEL"):
            self.assertIn(token, body, "步骤表必须继续显示 %s（Spec C5）" % token)

    def test_d4_existing_board_events_survive(self):
        src = read_text(RUNTIME_JS)
        for event in ("task-progress", "task-completed", "task-partial", "task-failed"):
            self.assertTrue(event in src,
                            "既有事件 %s 不许改名或删除（Spec C5）" % event)

    def test_d5_blocked_label_survives(self):
        src = read_text(APP_JS)
        self.assertTrue('blocked: "被阻断"' in src,
                        "步骤表里的 blocked 中文词不许改（Spec C5）")

    def test_d6_drawing_flow_endpoints_stay_wired(self):
        src = read_text(APP_JS)
        self.assertTrue(RUN_ENDPOINT in src, "必须继续调 POST %s" % RUN_ENDPOINT)
        self.assertTrue(re.search(r"drawing-flow[`\"')\s]", src),
                        "必须继续调 GET /api/projects/{pid}/drawing-flow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
