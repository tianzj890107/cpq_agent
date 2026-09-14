"""看板长任务"提交即回执 + 事件驱动完成"的红测基线（实现前应失败）。

背景：统一父壳里点「开始解析」会在 20 秒后弹「开始解析超时未响应」，因为
`tech-board-bridge.js` 的 `executeAction` 用 20 秒默认超时，而右侧看板把
`parseDrawing` / `runIntegration` / `costStep` / `extractRequirement` 这类长任务
注册成"等整份任务跑完才回执"（`app.js` 里是 `await parseDrawing()`）。

修法固定为：动作条目声明 `deferred: true`，`run` 只启动后台链路并立即回执；
真正的完成/失败由看板用既有 `TechBoardRuntime.publish` 推 `task-completed` /
`task-failed`；父壳负责把 `task-failed` 的真实原因显示出来。

静态契约任何解释器都能跑；行为用例用 Node 真跑 `tech-board-runtime.js` 的消息分发，
没有 Node 时跳过。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
RUNTIME = FRONTEND / "tech-board-runtime.js"
PARENT = FRONTEND / "tech-workbench.js"
BRIDGE = FRONTEND / "tech-board-bridge.js"

# 会发起后端异步任务并等待其完成、因而必然超过桥的 20 秒默认超时的动作。
LONG_TASK_ACTIONS = {
    "app.js": {
        "parseDrawing": ("parseDrawing",),
    },
    "assembly-integration.js": {
        "runIntegration": ("aiRunAll",),
        "integrationStep": ("aiGenerate",),
    },
    "cost-review.js": {
        "runCostReview": ("crRunAll",),
        "costStep": ("crRunPart", "crRunAssembly", "crRunAll"),
    },
    "requirement-create.js": {
        "extractRequirement": ("rcExtractRequirementFields",),
    },
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _registry_body(source: str) -> str:
    """截出 registerActions({...}) 的动作表正文。"""
    match = re.search(r"registerActions\(\{(.*?)\n  \}\);", source, re.S)
    if not match:
        match = re.search(r"registerActions\(\{(.*?)\}\);", source, re.S)
    return match.group(1) if match else ""


def _action_block(source: str, action: str) -> str:
    """截出单个动作条目（从动作名到大括号结束前的下一个同级动作）。"""
    body = _registry_body(source)
    start = body.find("\n    " + action + ":")
    if start < 0:
        start = body.find("\n  " + action + ":")
    if start < 0:
        return ""
    tail = body[start + 1:]
    match = re.search(r"\n\s{2,6}\w+:\s*\{", tail)
    return body[start:start + 1 + (match.start() if match else len(tail))]


class RuntimeDeferredContract(unittest.TestCase):
    """R1：运行时必须认识 deferred，并且不得越权替它宣告完成。"""

    @classmethod
    def setUpClass(cls):
        cls.runtime = _read(RUNTIME)

    def test_runtime_knows_deferred_entries(self):
        match = re.search(r"function runEntry\([\s\S]*?\n  \}", self.runtime)
        self.assertIsNotNone(match, "找不到 runEntry 实现")
        body = match.group(0)
        self.assertRegex(
            body, r"entry\.deferred|\.deferred\b",
            "runEntry 未识别 deferred 条目，长任务仍会在回执时被误判为已完成",
        )
        self.assertRegex(
            body, r"deferred[\s\S]{0,600}TASK_COMPLETED|TASK_COMPLETED[\s\S]{0,600}deferred",
            "deferred 必须与 task-completed 的发布条件相邻，否则声明了也没用",
        )

    def test_non_deferred_actions_still_publish_completion(self):
        match = re.search(r"function runEntry\([\s\S]*?\n  \}", self.runtime)
        self.assertIsNotNone(match)
        # 第 5 批之后卡片事件收口到 runEntry 内的 publishTaskCard(eventName, extra)
        # 出口（silent 条目在出口里短路），完成事件因此不再以裸 publish(EVENT.*) 出现。
        self.assertRegex(
            match.group(0), r"publishTaskCard\(EVENT\.TASK_COMPLETED",
            "非 deferred 动作仍必须发布 task-completed",
        )

    def test_bridge_default_timeout_is_not_loosened(self):
        match = re.search(r"DEFAULT_TIMEOUT\s*=\s*(\d+)", _read(BRIDGE))
        self.assertIsNotNone(match, "找不到桥的默认超时")
        self.assertEqual(
            int(match.group(1)), 20000,
            "不得用放宽超时来掩盖长任务未回执；要改成提交即回执",
        )


class LongTaskActionContract(unittest.TestCase):
    """R2 / R3：长任务动作声明 deferred、不再 await 整任务、并自行发布完成/失败。"""

    def test_long_task_actions_declare_deferred(self):
        missing = []
        for filename, actions in LONG_TASK_ACTIONS.items():
            source = _read(FRONTEND / filename)
            for action in actions:
                block = _action_block(source, action)
                if not block:
                    missing.append(f"{filename}:{action}:未找到动作条目")
                    continue
                if not re.search(r"deferred\s*:\s*true", block):
                    missing.append(f"{filename}:{action}:缺少 deferred: true")
        self.assertFalse(missing, f"长任务动作未声明 deferred：{missing}")

    def test_long_task_actions_do_not_await_the_whole_task(self):
        offenders = []
        for filename, actions in LONG_TASK_ACTIONS.items():
            source = _read(FRONTEND / filename)
            for action, awaited in actions.items():
                block = _action_block(source, action)
                for call in awaited:
                    if re.search(rf"await\s+{call}\s*\(", block):
                        offenders.append(f"{filename}:{action}:await {call}(")
        self.assertFalse(
            offenders,
            "长任务动作仍同步等整份任务，20 秒桥超时必然再次触发："
            f"{offenders}",
        )

    def test_long_task_pages_publish_real_completion(self):
        problems = []
        for filename in LONG_TASK_ACTIONS:
            source = _read(FRONTEND / filename)
            if "TechBoardRuntime.publish(" not in source:
                problems.append(f"{filename}:未调用 TechBoardRuntime.publish")
                continue
            for event in ("task-completed", "task-failed"):
                if event not in source:
                    problems.append(f"{filename}:缺少 {event}")
        self.assertFalse(problems, f"长任务完成/失败必须由看板自己发布：{problems}")


class ParentFailureSurfaceContract(unittest.TestCase):
    """R4：父壳要把长任务的真实失败显示出来，而不是只认 type === 'error'。"""

    def test_parent_bindboard_shows_task_failed_message(self):
        source = _read(PARENT)
        match = re.search(r"function bindBoardBridge\([\s\S]*?\n  \}", source)
        self.assertIsNotNone(match, "找不到 bindBoardBridge")
        body = match.group(0)
        self.assertIn(
            "task-failed", body,
            "父壳订阅回调未处理 task-failed，长任务真实失败原因不会出现在提示位",
        )
        self.assertRegex(body, r"setBoardNotice\(")


_NODE = shutil.which("node")

_PROBE = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('vm');
    const runtimePath = process.argv[2];

    const outbox = [];
    const listeners = {};
    const fakeParent = { postMessage: (message, origin) => { outbox.push({ message, origin }); } };
    const windowObj = {
      parent: fakeParent,
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    };
    const context = {
      window: windowObj,
      location: { search: '?project=p1&stage=drawing', origin: 'http://localhost' },
      document: { readyState: 'complete' },
      console, setTimeout, clearTimeout, Promise, JSON, URLSearchParams,
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(runtimePath, 'utf8'), context);

    const rt = windowObj.TechBoardRuntime;
    const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
    const sendCommand = (requestId, name, payload) => {
      const event = {
        origin: 'http://localhost',
        source: fakeParent,
        data: {
          namespace: rt.namespace, version: rt.version, type: 'command',
          requestId, projectId: 'p1', stage: 'drawing', name, payload,
        },
      };
      listeners['message'].forEach((fn) => fn(event));
    };
    const results = (requestId) =>
      outbox.filter((m) => m.message.type === 'result' && m.message.requestId === requestId);
    const states = (eventName) =>
      outbox.filter((m) => m.message.type === 'state' && m.message.name === eventName);

    (async () => {
      const report = {};
      let finishBackground;
      const background = new Promise((resolve) => { finishBackground = resolve; });

      rt.registerActions({
        parseDrawing: {
          label: '开始解析',
          deferred: true,
          run: () => { background.then(() => {}, () => {}); return { ok: true, taskId: 't-1' }; },
        },
        nonDeferred: {
          label: '同步动作',
          run: async () => { await tick(); return { ok: true }; },
        },
        brokenDeferred: {
          label: '会失败的长任务',
          deferred: true,
          run: () => ({ ok: false, error: { code: 'parse-failed', message: '图纸解析未完成。' } }),
        },
      });

      outbox.length = 0;
      sendCommand('r-deferred', 'execute-action', { name: 'parseDrawing' });
      await tick();
      report.ackDeferred = results('r-deferred').map((m) => m.message.payload);
      report.completedAtAck = states('task-completed').length;

      finishBackground();
      await tick();
      rt.publish('task-completed', 'parseDrawing', { action: 'parseDrawing' });
      await tick();
      report.completedAfterBackground = states('task-completed').length;

      outbox.length = 0;
      sendCommand('r-sync', 'execute-action', { name: 'nonDeferred' });
      await tick();
      await tick();
      report.ackSync = results('r-sync').map((m) => m.message.payload);
      report.completedSync = states('task-completed').length;

      outbox.length = 0;
      sendCommand('r-fail', 'execute-action', { name: 'brokenDeferred' });
      await tick();
      report.ackFail = results('r-fail').map((m) => m.message.payload);
      report.failedStates = states('task-failed').length;

      console.log(JSON.stringify(report));
    })();
    """
)


@unittest.skipUnless(_NODE, "需要 node 才能真跑看板运行时的消息分发")
class DeferredRuntimeBehavior(unittest.TestCase):
    """真跑 tech-board-runtime.js，验证回执与完成事件的时序。"""

    @classmethod
    def setUpClass(cls):
        workdir = Path(tempfile.mkdtemp(prefix="cpq-board-deferred-"))
        probe = workdir / "probe.js"
        probe.write_text(_PROBE, encoding="utf-8")
        completed = subprocess.run(
            [_NODE, str(probe), str(RUNTIME)],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        if completed.returncode != 0:
            raise AssertionError(
                "看板运行时探针执行失败：\n" + (completed.stderr or completed.stdout)
            )
        cls.report = json.loads(completed.stdout.strip().splitlines()[-1])

    def test_deferred_action_acks_without_premature_completion(self):
        ack = self.report["ackDeferred"]
        self.assertEqual(len(ack), 1, f"deferred 动作必须立刻回执一次，实际 {ack}")
        self.assertTrue(ack[0].get("ok"), f"deferred 动作回执应为成功，实际 {ack[0]}")
        self.assertEqual(
            self.report["completedAtAck"], 0,
            "动作只是启动、后台任务还没结束时，运行时不得发布 task-completed",
        )
        self.assertEqual(
            self.report["completedAfterBackground"], 1,
            "后台任务结束后应恰好有一条 task-completed（由看板自己发布）",
        )

    def test_non_deferred_action_keeps_the_old_timing(self):
        self.assertTrue(self.report["ackSync"] and self.report["ackSync"][0].get("ok"))
        self.assertEqual(
            self.report["completedSync"], 1,
            "非 deferred 动作仍应在回执时发布 task-completed（回归保护）",
        )

    def test_deferred_failure_still_returns_structured_error(self):
        ack = self.report["ackFail"]
        self.assertEqual(len(ack), 1)
        self.assertFalse(ack[0].get("ok"))
        self.assertEqual(ack[0]["error"]["code"], "parse-failed")
        self.assertEqual(self.report["failedStates"], 1)


if __name__ == "__main__":
    unittest.main()
