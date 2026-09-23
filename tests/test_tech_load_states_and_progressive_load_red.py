"""红测：2.1 与任务清单的装载态 / 分阶段加载 / 慢请求反馈 —— 加载中不许说「没有图纸 / 没有任务」。

Spec：`docs/specs/tech-load-states-and-progressive-load.md`

现状缺口（2026-09-23 实测，代码级，都可指到行）：
  · `index.html:147` `#btnParse` 初始就 `disabled`；`app.js:4160` 只有整份项目读回来才置位；
  · `app.js:5622-5625` 看板动作在 `disabled` 时一律回 `not-ready：当前没有可解析的图纸，请先上传
    2D 工程图。` —— 「还在加载 / 项目打不开 / 3D 导入项目」三种处境同一句话，3D 的真实原因只写在
    `app.js:4161` 的 `#btnParse.title` 里，回包一个字都不带；
  · `openProject()` 期间 `#tree` 空、按钮灰、状态栏无字：没有首屏占位；
  · `app.js:4153-4154` 两段读是串行的；`grep data-qq-stage` = 0 处；
  · `cpq-tech-inbox.js:82-85` `catch (e) { tasks = []; }` + `loaded = true` ⇒ 页面说
    「暂时没有分派给你的任务。」（读失败伪装空态），也没有重试；
  · 链路步记录只有秒级 `started_at` / `finished_at`，没有 `duration_ms`（`grep` 只命中转换器）。

纪律：`node -e` 抽具名函数体真跑（纯函数）+ 源码守卫；不起服务、不发 HTTP、不连 PG / SQLite、
不写业务数据、不读真样本、不改业务实现。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
INBOX_JS = ROOT / "tech_app" / "frontend" / "cpq-tech-inbox.js"
FLOW_INIT = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "__init__.py"

READINESS = "parseActionReadiness"
ELAPSED = "pageLoadElapsedText"
STAGES_LINE = "loadStagesLine"
DURATION = "drawingFlowStepDurationText"

LOADING_TEXT_8S = "图纸信息还在加载中（已用 8s），请稍候。"
LOADING_TEXT_UNKNOWN = "图纸信息还在加载中（已用不到 1s），请稍候。"
NOT_READY_FALLBACK = "当前没有可解析的图纸，请先上传 2D 工程图。"
NOT_READY_3D = ("这是 3D 模型导入项目（model.step）：几何已由原始实体生成，无需也不应再按图纸重建。")
INBOX_ERROR_HTTP = "这一次读不到待办任务（HTTP 500），请稍后重试；这不代表没有分派给你的任务。"
INBOX_ERROR_NET = "这一次读不到待办任务（网络错误），请稍后重试；这不代表没有分派给你的任务。"
INBOX_EMPTY = "暂时没有分派给你的任务。"
INBOX_LOADING = "正在读取待办任务…"

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
const cases = JSON.parse(mode.replace(/\bNaN\b/g, "null"));
eval(fn);
const out = [];
for (const args of cases) {
  const list = Array.isArray(args) ? args : [args];
  const call = name + "(" + list.map(a => JSON.stringify(a) + "").join(", ") + ")";
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
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(source), name, "body"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def call(source: pathlib.Path, name: str, args) -> object:
    payload = run_cases(source, name, [args])
    if payload.get("missing"):
        raise AssertionError("%s 缺少纯函数 %s()（Spec §2）" % (source.name, name))
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def readiness(**kwargs) -> object:
    return call(APP_JS, READINESS, kwargs)


# --------------------------------------------------------------------------- #
# P 组：动作三态判定（纯函数）—— 「还在加载」不是「没有图纸」
# --------------------------------------------------------------------------- #
class PParseActionReadiness(unittest.TestCase):
    def test_p1_loading_is_not_reported_as_no_drawing(self):
        got = readiness(state="loading", startedAt=1000, disabled=True, reason="", now=9000)
        self.assertEqual({"code": "loading", "message": LOADING_TEXT_8S}, got,
                         "加载中必须回 loading，不许回 not-ready（Spec §2.2）")

    def test_p2_loading_without_a_valid_start_says_under_one_second(self):
        for started in ("x", None, float("nan")):
            got = readiness(state="loading", startedAt=started, disabled=True, reason="", now=9000)
            self.assertEqual({"code": "loading", "message": LOADING_TEXT_UNKNOWN}, got,
                             "量不出开始时间要说'不到 1s'（Spec §2.2）：%r" % (started,))

    def test_p3_disabled_after_load_speaks_the_real_reason(self):
        got = readiness(state="ready", startedAt=0, disabled=True, reason=NOT_READY_3D, now=1)
        self.assertEqual({"code": "not-ready", "message": NOT_READY_3D}, got,
                         "装完之后的拒绝必须逐字带按钮 title 的真实原因（Spec §2.2）")

    def test_p4_disabled_without_a_reason_keeps_the_legacy_sentence(self):
        got = readiness(state="ready", startedAt=0, disabled=True, reason="", now=1)
        self.assertEqual({"code": "not-ready", "message": NOT_READY_FALLBACK}, got,
                         "既有兜底文案一字不改（Spec §2.2/§2.8）")

    def test_p5_enabled_lets_the_action_start(self):
        self.assertIsNone(readiness(state="ready", startedAt=0, disabled=False, reason="", now=1),
                          "可点时必须回 null（放行）（Spec §2.2）")

    def test_p6_unknown_state_is_treated_as_loading(self):
        for state in (None, "idle", "wat", 0):
            got = readiness(state=state, startedAt=0, disabled=True, reason="", now=500)
            self.assertIsInstance(got, dict, "未知状态不许放行、也不许说成没有图纸（Spec §2.2）")
            self.assertEqual("loading", got.get("code"),
                             "未知状态按 loading 处理（Spec §2.2）：%r" % (state,))

    def test_p7_failed_load_also_speaks_the_reason(self):
        got = readiness(state="failed", startedAt=0, disabled=True, reason=NOT_READY_3D, now=1)
        self.assertEqual({"code": "not-ready", "message": NOT_READY_3D}, got,
                         "读失败之后的拒绝同样要说真实原因（Spec §2.2）")

    def test_p8_pure_function_and_action_wiring(self):
        body = function_body(APP_JS, READINESS)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.2）" % READINESS)
        for token in ("document.", "window.", "fetch(", "localStorage", "sessionStorage"):
            self.assertNotIn(token, body, "判定必须是纯函数（Spec §2.2）")
        src = read_text(APP_JS)
        at = src.find("parseDrawing: {")
        self.assertGreaterEqual(at, 0, "app.js 里找不到 parseDrawing 动作条目")
        block = src[at:at + 2600]
        self.assertIn(READINESS + "(", block,
                      "看板动作必须走同一份判定（Spec §2.2）")


# --------------------------------------------------------------------------- #
# E 组：已用时长（纯函数）
# --------------------------------------------------------------------------- #
class EPageLoadElapsed(unittest.TestCase):
    def test_e1_sub_second(self):
        self.assertEqual("已用不到 1s", call(APP_JS, ELAPSED, [1000, 1800]),
                         "不足 1 秒要说'不到 1s'（Spec §2.4）")

    def test_e2_seconds(self):
        self.assertEqual("已用 8s", call(APP_JS, ELAPSED, [1000, 9900]),
                         "秒级要说整数秒（Spec §2.4）")

    def test_e3_minutes(self):
        self.assertEqual("已用 1 分 8 秒", call(APP_JS, ELAPSED, [1000, 69000]),
                         "超过 1 分钟要说分和秒（Spec §2.4）")

    def test_e4_junk_returns_empty(self):
        for args in ([None, 1000], ["x", 1000], [1000, None], [1000, "x"], [2000, 1000]):
            self.assertEqual("", call(APP_JS, ELAPSED, args),
                             "非法/倒退的入参回空串（Spec §2.4）：%r" % (args,))

    def test_e5_pure_function(self):
        body = function_body(APP_JS, ELAPSED)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.4）" % ELAPSED)
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "已用时长必须是纯函数（Spec §2.4）")


# --------------------------------------------------------------------------- #
# G 组：分阶段加载
# --------------------------------------------------------------------------- #
class GProgressiveLoad(unittest.TestCase):
    def test_g1_all_stages_ready(self):
        stages = {"core": "ready", "flow": "ready", "geometry_parts": "ready",
                  "business_parts": "ready"}
        self.assertEqual("已就绪 4/4 段", call(APP_JS, STAGES_LINE, [stages]),
                         "全就绪的文案（Spec §2.5）")

    def test_g2_failure_is_reported_before_pending(self):
        stages = {"core": "ready", "flow": "ready", "geometry_parts": "failed",
                  "business_parts": "loading"}
        self.assertEqual("已就绪 2/4 段，1 段读取失败", call(APP_JS, STAGES_LINE, [stages]),
                         "有失败先说失败（Spec §2.5）")

    def test_g3_pending_only(self):
        stages = {"core": "ready", "flow": "loading", "geometry_parts": "loading",
                  "business_parts": "loading"}
        self.assertEqual("已就绪 1/4 段，3 段读取中", call(APP_JS, STAGES_LINE, [stages]),
                         "在途几段要说清（Spec §2.5）")

    def test_g4_junk_returns_empty(self):
        for stages in (None, {}, "x", [], [None]):
            self.assertEqual("", call(APP_JS, STAGES_LINE, [stages]),
                             "空/非法入参回空串（Spec §2.5）：%r" % (stages,))

    def test_g5_geometry_and_business_start_together(self):
        body = function_body(APP_JS, "refreshPackagingParts")
        self.assertTrue(body, "app.js 缺少 refreshPackagingParts()")
        self.assertIn("Promise.all(", body,
                      "几何分量与业务部件两段必须并发开始（Spec §2.5）")

    def test_g6_stage_hooks_exist(self):
        src = read_text(APP_JS)
        self.assertIn("data-qq-stage", src, "四段要有自己的钩子（Spec §2.5）")
        self.assertIn("data-qq-stage-state", src, "每段要说出自己的状态（Spec §2.5）")


# --------------------------------------------------------------------------- #
# N 组：2.1 首屏与装载态
# --------------------------------------------------------------------------- #
class NTwoOneFirstPaint(unittest.TestCase):
    def test_n1_open_project_marks_loading_first(self):
        body = function_body(APP_JS, "openProject")
        self.assertTrue(body, "app.js 缺少 openProject()")
        self.assertIn("pageLoadState", body,
                      "装载态的唯一来源必须在 openProject 里维护（Spec §2.1）")
        self.assertIn("loading", body, "进 openProject 就要进 loading 态（Spec §2.1）")
        self.assertIn("finally", body, "装载态必须在 finally 收口（Spec §2.1）")

    def test_n2_skeleton_is_painted_before_the_first_request(self):
        body = function_body(APP_JS, "openProject")
        at_skeleton = body.find("data-qq-page-skeleton")
        at_fetch = body.find("await fetch(")
        self.assertGreaterEqual(at_skeleton, 0,
                                "首屏必须有占位节点（Spec §2.4）")
        self.assertGreaterEqual(at_fetch, 0, "openProject() 里找不到首个请求")
        self.assertLess(at_skeleton, at_fetch,
                        "占位必须在第一个请求之前就画出来（Spec §2.4）")

    def test_n3_skeleton_carries_the_elapsed_text(self):
        src = read_text(APP_JS)
        self.assertIn("正在读取项目…", src, "占位文案必须说'正在读取项目…'（Spec §2.4）")
        self.assertIn(ELAPSED + "(", src, "占位必须带已用时长（Spec §2.4）")

    def test_n4_loading_state_does_not_paint_terminal_texts(self):
        body = function_body(APP_JS, "renderTree")
        self.assertTrue(body, "app.js 缺少 renderTree()")
        self.assertIn("pageLoadState", body,
                      "加载期间不许渲染终态文案：判定要落在渲染处（Spec §2.4）")

    def test_n5_legacy_not_ready_sentence_is_kept(self):
        src = read_text(APP_JS)
        self.assertIn(NOT_READY_FALLBACK, src,
                      "既有 not-ready 兜底文案一字不改（Spec §2.8）")


# --------------------------------------------------------------------------- #
# D 组：步级时长
# --------------------------------------------------------------------------- #
class DStepDuration(unittest.TestCase):
    def test_d1_seconds(self):
        self.assertEqual("1.2 s", call(APP_JS, DURATION, [{"duration_ms": 1200}]),
                         "毫秒要转成一位小数的秒（Spec §2.7）")

    def test_d2_minutes(self):
        self.assertEqual("1 分 2 秒", call(APP_JS, DURATION, [{"duration_ms": 62000}]),
                         "超过 1 分钟要说分和秒（Spec §2.7）")

    def test_d3_missing_or_illegal_is_empty(self):
        for step in (None, {}, {"duration_ms": 0}, {"duration_ms": -5}, {"duration_ms": "x"}):
            self.assertEqual("", call(APP_JS, DURATION, [step]),
                             "没量到时长不许显示 0.0 s（Spec §2.7）：%r" % (step,))

    def test_d4_flow_records_the_duration(self):
        text = read_text(FLOW_INIT)
        self.assertIn("duration_ms", text,
                      "链路步记录必须给出 duration_ms（Spec §2.7）")

    def test_d5_step_table_shows_it(self):
        body = function_body(APP_JS, "renderDrawingFlowPanel")
        self.assertTrue(body, "app.js 缺少 renderDrawingFlowPanel()")
        self.assertIn(DURATION + "(", body,
                      "步骤表必须调用时长函数（Spec §2.7）")


# --------------------------------------------------------------------------- #
# I 组：任务清单读失败状态
# --------------------------------------------------------------------------- #
class IInboxReadFailure(unittest.TestCase):
    def test_i1_load_records_the_read_failure(self):
        body = function_body(INBOX_JS, "loadTasks")
        self.assertTrue(body, "cpq-tech-inbox.js 缺少 loadTasks()")
        self.assertIn("catch", body, "读失败必须有自己的分支（Spec §2.6）")
        self.assertIn("inboxError", body,
                      "读失败不许静默折成空数组：要记下这一趟读不到（Spec §2.6）")

    def test_i2_failure_is_not_painted_as_empty(self):
        body = function_body(INBOX_JS, "renderTaskCards")
        self.assertTrue(body, "cpq-tech-inbox.js 缺少 renderTaskCards()")
        self.assertIn("data-qq-inbox-error", body,
                      "读失败要有自己的钩子（Spec §2.6）")
        self.assertIn("这一次读不到待办任务（HTTP ", body,
                      "读失败要逐字说清（Spec §2.6）")
        self.assertIn("这不代表没有分派给你的任务。", body,
                      "读失败不许说成'没有任务'（Spec §2.6）")
        at_error = body.find("inboxError")
        at_empty = body.find("暂时没有分派给你的任务。")
        self.assertGreaterEqual(at_error, 0, "读失败判定还没接线（Spec §2.6）")
        self.assertGreaterEqual(at_empty, 0, "既有空态文案必须保留")
        self.assertLess(at_error, at_empty,
                        "读失败判定必须排在'暂时没有…'之前（Spec §2.6）")

    def test_i3_retry_button_exists(self):
        src = read_text(INBOX_JS)
        for token in ("cpqInboxRetry", "loadTasks()"):
            self.assertIn(token, src, "读失败要能一键重试（Spec §2.6）：缺 %s" % token)

    def test_i4_loading_and_empty_literals_are_kept(self):
        src = read_text(INBOX_JS)
        self.assertIn(INBOX_EMPTY, src, "既有空态字面量逐字保留（Spec §2.8）")
        self.assertIn(INBOX_LOADING, src, "既有首屏加载字面量逐字保留（Spec §2.8）")

    def test_i5_error_texts_are_verbatim(self):
        src = read_text(INBOX_JS)
        self.assertIn("（网络错误）", src, "没有状态码要说'网络错误'（Spec §2.6）")
        self.assertIn(INBOX_ERROR_HTTP, src, "HTTP 读失败文案逐字（Spec §2.6）")
        self.assertIn(INBOX_ERROR_NET, src, "网络读失败文案逐字（Spec §2.6）")


if __name__ == "__main__":
    unittest.main()
