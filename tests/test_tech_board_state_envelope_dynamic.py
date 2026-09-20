"""看板 ↔ 父壳状态通道的直连回归（0–3 缺陷 + 第 4 步端到端）。

背景（本文件建立时的实测结论）：
  · tech-board-runtime.js 的 emit() 把事件名写进信封 type（"ready" / "action-state" /
    "task-progress"...），而 tech-board-bridge.js 只认 type === "state"；
  · 因此看板主动推送全部被丢弃，父壳左侧计数、可用态、进度卡在真实链路里拿不到数据；
  · 全仓库没有任何页面发布 result-summary，生产者缺失。

验证方式：在 Node 里用两个 vm 上下文分别加载真实的 runtime 与 bridge，把两边接成
双向通道，然后真跑 attach / action-state / execute-action，取回实际发生的消息。
另外用静态断言锁住“看板必须发布 result-summary”与“旧导航通道必须校验 event.source”。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
RUNTIME = FRONTEND / "tech-board-runtime.js"
BRIDGE = FRONTEND / "tech-board-bridge.js"
PARENT_JS = (FRONTEND / "tech-workbench.js").read_text(encoding="utf-8", errors="replace")
BOARD_JS = (FRONTEND / "app.js").read_text(encoding="utf-8", errors="replace")
CHAT_JS = (FRONTEND / "agent-chat.js").read_text(
    encoding="utf-8", errors="replace").replace("\x00", "")

STATE_EVENT_NAMES = {
    "ready", "action-state", "task-progress", "task-completed", "task-failed",
    "selection-changed", "result-summary",
}

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const runtimeCode = fs.readFileSync(process.argv[2], 'utf8');
const bridgeCode = fs.readFileSync(process.argv[3], 'utf8');
const ORIGIN = 'http://localhost';

function makeEnv(search, pathname) {
  const handlers = [];
  const env = {
    location: { origin: ORIGIN, search: search, pathname: pathname },
    document: { readyState: 'complete' },
    setTimeout: setTimeout, clearTimeout: clearTimeout,
    URLSearchParams: URLSearchParams, console: console,
  };
  env.window = env;
  env.addEventListener = (type, fn) => { if (type === 'message') handlers.push(fn); };
  env.__deliver = (event) => { handlers.slice().forEach((fn) => fn(event)); };
  return env;
}

const runtimeEnv = makeEnv('?project=P1&stage=drawing', '/index.html');
const bridgeEnv = makeEnv('', '/tech-workbench.html');
const runtimeCtx = vm.createContext(runtimeEnv);
const bridgeCtx = vm.createContext(bridgeEnv);
const rawBoardPosts = [];

runtimeEnv.parent = {
  postMessage: (msg) => {
    rawBoardPosts.push(JSON.parse(JSON.stringify(msg)));
    bridgeEnv.__deliver({ origin: ORIGIN, source: bridgeEnv.__frame.contentWindow, data: msg });
  },
};
bridgeEnv.__frame = { contentWindow: { postMessage: (msg) => {
  runtimeEnv.__deliver({ origin: ORIGIN, source: runtimeEnv.parent, data: msg });
} } };

vm.runInContext(runtimeCode, runtimeCtx, { filename: 'tech-board-runtime.js' });
vm.runInContext(bridgeCode, bridgeCtx, { filename: 'tech-board-bridge.js' });

const runtime = runtimeEnv.TechBoardRuntime;
const bridge = bridgeEnv.TechBoardBridge;
const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const out = { runtime_api: !!runtime, bridge_api: !!bridge };

(async () => {
  const events = [];
  bridge.subscribe((event) => events.push(String(event.type) + '|' + String(event.name)));
  let busy = false;
  let parseRuns = 0;
  runtime.registerActions({
    parseDrawing: {
      label: '开始解析',
      run: async () => { parseRuns += 1; await tick(5); return { parts: 3 }; },
      getState: () => ({ visible: true, enabled: true, busy: busy }),
    },
  });

  const attached = await bridge.attach(bridgeEnv.__frame,
    { projectId: 'P1', stage: 'drawing', taskId: '' });
  out.attach = { ready: attached.ready, actions: Object.keys(attached.actions) };
  out.events_after_attach = events.slice();

  runtime.updateActionState('parseDrawing', { busy: true });
  await tick(20);
  out.after_action_state = {
    events: events.slice(),
    snapshot_busy: !!(bridge.snapshot().actions.parseDrawing || {}).busy,
  };

  const done = bridge.executeAction('parseDrawing', { label: '开始解析' });
  const result = await done.then((r) => ({ ok: r.ok === true }),
    (e) => ({ error: String(e && e.code) }));
  await tick(30);
  out.after_action = {
    runs: parseRuns,
    result: result,
    events: events.slice(),
    snapshot_busy: !!(bridge.snapshot().actions.parseDrawing || {}).busy,
  };

  out.board_posts = rawBoardPosts.filter((m) => m && m.type !== 'result')
    .map((m) => ({ type: m.type, name: m.name }));
  process.stdout.write(JSON.stringify(out));
})().catch((error) => {
  process.stdout.write(JSON.stringify(
    Object.assign(out, { fatal: String(error && error.stack || error) })));
});
"""


class TechBoardStateEnvelopeDynamicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("需要 node 才能跑看板 ↔ 父壳直连验证")
        with tempfile.TemporaryDirectory(prefix="cpq-envelope-") as tmp:
            harness = Path(tmp) / "harness.js"
            harness.write_text(HARNESS, encoding="utf-8")
            proc = subprocess.run(
                [shutil.which("node"), str(harness), str(RUNTIME), str(BRIDGE)],
                capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            raise AssertionError(f"直连宿主执行失败：{proc.stderr}")
        cls.result = json.loads(proc.stdout)

    def test_modules_loaded_in_harness(self):
        self.assertTrue(self.result.get("runtime_api"), "运行时未加载")
        self.assertTrue(self.result.get("bridge_api"), "父壳桥未加载")
        self.assertTrue((self.result.get("attach") or {}).get("ready"), "attach 未就绪")

    def test_board_state_posts_use_state_envelope(self):
        posts = self.result.get("board_posts") or []
        self.assertTrue(posts, "看板没有发出任何状态事件")
        bad = [p for p in posts
               if p.get("type") != "state" or p.get("name") not in STATE_EVENT_NAMES]
        self.assertFalse(bad, f"状态事件必须是 type=state + name=事件名，实际发出：{bad[:6]}")

    def test_parent_bridge_receives_action_state_and_snapshot_updates(self):
        after = self.result.get("after_action_state") or {}
        self.assertTrue(
            any(item.startswith("action-state") for item in (after.get("events") or [])),
            f"父壳未收到 action-state 推送：{after.get('events')}")
        self.assertTrue(after.get("snapshot_busy"),
                        "父壳 snapshot().actions 未随 action-state 更新 busy")

    def test_parent_bridge_receives_task_progress_and_completion(self):
        after = self.result.get("after_action") or {}
        self.assertEqual(after.get("runs"), 1, "动作未执行")
        self.assertTrue((after.get("result") or {}).get("ok"), f"动作未成功：{after.get('result')}")
        names = [item.split("|")[0] for item in (after.get("events") or [])]
        for expected in ("task-progress", "task-completed"):
            self.assertIn(expected, names, f"父壳未收到 {expected}：{names}")

    def test_board_publishes_result_summary_for_left_entries(self):
        self.assertIn("result-summary", BOARD_JS,
                      "2.1 看板没有发布 result-summary，左侧结果按钮与计数永远拿不到数据")
        match = re.search(r"result-summary[\s\S]{0,1200}", BOARD_JS)
        body = match.group(0) if match else ""
        for key in ("parts", "questions", "files", "report"):
            self.assertIn(key, body, f"result-summary 缺少 {key} 分组")
        self.assertIn("available", body)
        self.assertIn("count", body)

    def test_left_chat_consumes_result_summary_payload(self):
        self.assertIn("result-summary", CHAT_JS)
        for key in ("results", "parts", "questions", "report", "files"):
            self.assertIn(key, CHAT_JS)

    def test_legacy_navigate_channel_checks_event_source(self):
        # 锚点必须落在**处理函数**里（带引号的字符串字面量），不能落在文件头注释上：
        # 注释里也写着通道名，按通道名就近取 900 字会取到 STAGES 表而不是 handler。
        anchor = PARENT_JS.find("'cpq:tech-workbench:navigate'")
        self.assertNotEqual(anchor, -1, "未找到 cpq:tech-workbench:navigate 的处理分支")
        handler = PARENT_JS[anchor:anchor + 500]
        self.assertIn("event.source", handler,
                      "旧导航通道只校验 origin，未校验 event.source")


if __name__ == "__main__":
    unittest.main()
