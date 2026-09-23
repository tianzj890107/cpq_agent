"""红测：技术工艺 Agent 主动动作的「我：…」回声 + 执行进度与助手回复合成一张卡。

用户口径（三条，已拍板）：
  1) 只给「Agent 主动发起、并且会真的跑起来」的动作补一条用户气泡；往看板写字段、
     把确认 / 审核意见带进看板输入框、单纯刷新看板这几类一条都不加。
  2) 气泡文案由执行方（右侧看板动作）给出，左侧不写死「动作名 → 文案」映射表。
  3) 执行进度不再是一种独立卡片，与助手回复合成同一个气泡（报价 .message-ai 的形态）。

现状缺口（已排查）：
  · 技术工艺左侧只有真人打字才出用户气泡（agent-chat.js:757 发送、:262 历史回放）；
    Agent 主动做事的路径只留 noteInThread 系统提示，整个 tech_app/frontend/ 没有
    addUserBubble。
  · 执行进度是另一族卡：ensureTaskCard() 建的是独立 .oc-task-card，与助手卡 .oc-amsg
    平级（agent-chat.js:1316 注释原文「任务卡是与助手卡同级的一张卡」）。
  · 看板运行时的 task-progress 载荷只有 {action, phase, label, taskId}，没有用户口吻字段。

Spec：docs/specs/tech-agent-echo-bubble-and-single-exec-card.md
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
CHAT_JS = FRONTEND / "agent-chat.js"
CHAT_CSS = FRONTEND / "agent-chat.css"
RUNTIME = FRONTEND / "tech-board-runtime.js"
BRIDGE = FRONTEND / "tech-board-bridge.js"
APP_JS = FRONTEND / "app.js"
REQ_CREATE = FRONTEND / "requirement-create.js"
ASSEMBLY = FRONTEND / "assembly-integration.js"
COST = FRONTEND / "cost-review.js"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 只有这 5 个动作声明气泡文案（动作名 → 声明它的文件）。
ECHO_ACTIONS = {
    "parseDrawing": APP_JS,
    "extractRequirement": REQ_CREATE,
    "integrationStep": ASSEMBLY,
    "openIntegrationDrawings": ASSEMBLY,
    "costStep": COST,
}
# 每个文件里 prompt 声明的总数：多一处就是在给别的动作开口子。
PROMPT_COUNT_PER_FILE = {APP_JS: 1, REQ_CREATE: 1, ASSEMBLY: 2, COST: 1}

# 明确不加气泡的动作（第 6 / 7 类）。
NO_ECHO_FUNCTIONS = (
    "applyConfirmationNoteAction",
    "applyReviewNoteAction",
    "refreshRequirementBoard",
    "refreshIntegrationBoard",
    "refreshCostReviewBoard",
    "refreshProcessReportBoard",
)

TASK_PIPELINE_TOKENS = (
    "renderTaskProgress", "ensureTaskCard", "pushTaskStep", "toneOf", "sanitizeTaskDetail",
    "setTaskStatus", "taskStatusWord", "taskProgressHost", "persistTaskCard",
    "replayTimelineTask",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
    """从 marker 之后第一个 `{` 起做括号配对，返回该块（含首尾花括号）。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i)
            i = len(text) if j < 0 else j + 2
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
        i += 1
    return ""


def function_body(text: str, name: str) -> str:
    return block_from(text, f"function {name}(")


def entry_block(text: str, action: str) -> str:
    return block_from(text, f"{action}:")


# --------------------------------------------------------------------- 右侧：运行时
class RuntimePromptStaticContract(unittest.TestCase):
    """C1：运行时解析动作声明的气泡文案，并把它放进 task-progress 载荷。"""

    @classmethod
    def setUpClass(cls):
        cls.runtime = read(RUNTIME)

    def test_runtime_has_a_prompt_resolver(self):
        body = function_body(self.runtime, "resolveActionPrompt")
        self.assertTrue(body, "tech-board-runtime.js 缺少 resolveActionPrompt()")
        self.assertIn("payload", body, "文案解析必须能用这一次调用的入参")
        self.assertIn("prompt", body, "resolveActionPrompt() 里没有读动作声明的 prompt")

    def test_resolver_never_falls_back_to_action_name_or_label(self):
        body = function_body(self.runtime, "resolveActionPrompt")
        for banned in ("entry.label", "entryState(", "entry.name", "name ||"):
            with self.subTest(banned=banned):
                self.assertNotIn(
                    banned, body,
                    "解析不出文案时必须留空，不能拿动作名 / label 兜底造句",
                )

    def test_task_progress_payload_carries_the_prompt(self):
        run = function_body(self.runtime, "runEntry")
        self.assertIn("resolveActionPrompt", run, "runEntry 没有解析动作声明的气泡文案")
        start = block_from(run, "publishTaskCard(EVENT.TASK_PROGRESS")
        self.assertTrue(start, "找不到 runEntry 里启动那条 publishTaskCard")
        self.assertIn("prompt", start, "启动载荷没有带 prompt，左侧拿不到用户口吻的那句话")

    def test_existing_payload_fields_are_kept(self):
        run = function_body(self.runtime, "runEntry")
        start = block_from(run, "publishTaskCard(EVENT.TASK_PROGRESS")
        for field in ("action", "phase", "label", "taskId"):
            with self.subTest(field=field):
                self.assertIn(field, start, f"启动载荷的既有字段 {field} 被删了")


# --------------------------------------------------------------------- 右侧：真跑
_NODE = shutil.which("node")

_PROBE = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('vm');
    const runtimePath = process.argv[2];

    const outbox = [];
    const listeners = {};
    const fakeParent = { postMessage: (message) => { outbox.push({ message }); } };
    const windowObj = {
      parent: fakeParent,
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    };
    const context = {
      window: windowObj,
      location: { search: '?project=p1&stage=cost', origin: 'http://localhost' },
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
          requestId, projectId: 'p1', stage: 'cost', name, payload,
        },
      };
      listeners['message'].forEach((fn) => fn(event));
    };
    const progressPayloads = () =>
      outbox.filter((m) => m.message.type === 'state' && m.message.name === 'task-progress')
            .map((m) => m.message.payload);

    (async () => {
      const report = {};

      rt.registerActions({
        parseDrawing: {
          label: '一键解析图纸', deferred: true,
          prompt: '开始解析这张图纸。',
          run: () => ({ ok: true }),
        },
        costStep: {
          label: '运行成本测算', deferred: true,
          prompt: (payload) => '跑一次成本测算：' + String((payload && payload.part_id) || '全部') + '。',
          run: () => ({ ok: true }),
        },
        plainAction: { label: '普通动作', run: () => ({ ok: true }) },
        silentAction: { label: '刷新看板', silent: true, run: () => ({ ok: true }) },
        brokenPrompt: {
          label: '文案抛错', deferred: true,
          prompt: () => { throw new Error('boom'); },
          run: () => ({ ok: true }),
        },
      });

      const runOne = async (requestId, payload) => {
        outbox.length = 0;
        sendCommand(requestId, 'execute-action', payload);
        await tick();
        await tick();
        return progressPayloads();
      };

      report.parse = (await runOne('r-parse', { name: 'parseDrawing', label: '开始解析' }))[0] || null;
      report.part = (await runOne('r-part', {
        name: 'costStep', step: 'part', part_id: 'P-003', label: '单件成本测算',
      }))[0] || null;
      report.plain = (await runOne('r-plain', { name: 'plainAction' }))[0] || null;
      report.silentCount = (await runOne('r-silent', { name: 'silentAction' })).length;
      report.broken = (await runOne('r-broken', { name: 'brokenPrompt' }))[0] || null;

      console.log(JSON.stringify(report));
    })();
    """
)


@unittest.skipUnless(_NODE, "需要 node 才能真跑看板运行时的消息分发")
class RuntimePromptBehavior(unittest.TestCase):
    """C1：真跑 tech-board-runtime.js，验证载荷里那句话。"""

    @classmethod
    def setUpClass(cls):
        workdir = Path(tempfile.mkdtemp(prefix="cpq-echo-bubble-"))
        probe = workdir / "probe.js"
        probe.write_text(_PROBE, encoding="utf-8")
        completed = subprocess.run(
            [_NODE, str(probe), str(RUNTIME)],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        if completed.returncode != 0:
            raise AssertionError("看板运行时探针执行失败：\n" + (completed.stderr or completed.stdout))
        cls.report = json.loads(completed.stdout.strip().splitlines()[-1])

    def test_declared_prompt_reaches_the_left_side(self):
        payload = self.report["parse"]
        self.assertIsNotNone(payload, "声明了 prompt 的动作没有发出 task-progress")
        self.assertEqual(payload.get("prompt"), "开始解析这张图纸。",
                         f"载荷没有带动作声明的文案：{payload}")

    def test_prompt_can_depend_on_this_call(self):
        payload = self.report["part"]
        self.assertIsNotNone(payload, "函数式 prompt 的动作没有发出 task-progress")
        self.assertEqual(payload.get("prompt"), "跑一次成本测算：P-003。",
                         f"函数式 prompt 没有按这一次的入参生成：{payload}")

    def test_action_without_prompt_reports_empty(self):
        payload = self.report["plain"]
        self.assertIsNotNone(payload, "普通动作没有发出 task-progress")
        self.assertIn("prompt", payload, "没声明 prompt 的动作也必须给出这个字段（空串）")
        self.assertEqual(payload["prompt"], "", f"没声明 prompt 的动作不得被兜底造句：{payload}")

    def test_broken_prompt_resolver_is_swallowed(self):
        payload = self.report["broken"]
        self.assertIsNotNone(payload, "文案函数抛错的动作没有发出 task-progress")
        self.assertEqual(payload.get("prompt"), "", f"文案解析失败必须留空，不能向上抛：{payload}")

    def test_silent_action_still_publishes_nothing(self):
        self.assertEqual(self.report["silentCount"], 0,
                         "silent 动作照旧不得发任务事件")


# --------------------------------------------------------------------- 右侧：声明范围
class OnlyFiveActionsDeclarePrompt(unittest.TestCase):
    """C2：只有 5 个动作声明 prompt，文案是中文业务句。"""

    def test_the_five_actions_declare_a_prompt(self):
        for action, path in ECHO_ACTIONS.items():
            with self.subTest(action=action):
                block = entry_block(read(path), action)
                self.assertTrue(block, f"{path.name} 里找不到动作条目 {action}")
                self.assertIn("prompt", block, f"动作 {action} 没有声明气泡文案")

    def test_prompt_text_is_chinese(self):
        for action, path in ECHO_ACTIONS.items():
            with self.subTest(action=action):
                block = entry_block(read(path), action)
                at = block.find("prompt")
                window = block[at:at + 400]
                self.assertRegex(window, r"[\u4e00-\u9fff]",
                                 f"动作 {action} 的气泡文案不是中文业务句：{window[:80]}")
                self.assertNotRegex(window, r"prompt\s*:\s*(?!['\"`(]|function|\(|\w)",
                                    "prompt 只能是字符串或 function(payload)")

    def test_no_other_action_declares_a_prompt(self):
        for path, expected in PROMPT_COUNT_PER_FILE.items():
            with self.subTest(path=path.name):
                found = len(self.bubble_prompt_declarations(read(path)))
                self.assertEqual(
                    found, expected,
                    f"{path.name} 里 prompt 声明数量是 {found}，应为 {expected}："
                    "第 6 / 7 类动作不加气泡文案",
                )

    @staticmethod
    def bubble_prompt_declarations(text):
        """只数"动作声明的气泡文案"，不数别处的同名键。

        `prompt:` 这个写法还会出现在 HTTP 请求体里（例如图纸解析链路提交
        `JSON.stringify({ prompt: "" })`）——那是给服务端的入参，不是动作气泡文案，
        计入会让本断言假红。声明所在的整行必然同时出现 `prompt` 与引号/函数，
        且不属于请求体；这条排除规则只放行"请求体"这一种来源。
        """
        hits = []
        for line in text.splitlines():
            if not re.search(r"\bprompt\s*:", line):
                continue
            if "JSON.stringify" in line or "body:" in line:
                continue
            hits.append(line)
        return hits

    def test_prompt_is_not_smuggled_into_get_state(self):
        for action, path in ECHO_ACTIONS.items():
            with self.subTest(action=action):
                block = entry_block(read(path), action)
                for state in re.findall(r"getState[\s\S]*?\n\s{0,8}\}", block):
                    self.assertNotIn("prompt", state,
                                     f"{action} 把 prompt 放进 getState 了：它是每次调用的入参，不是按钮状态")


# --------------------------------------------------------------------- 左侧：回声
class LeftEchoBubbleContract(unittest.TestCase):
    """C3 / C4：按载荷里的 prompt 出气泡，且只出一条、插在本轮卡上方。"""

    @classmethod
    def setUpClass(cls):
        cls.js = read(CHAT_JS)

    def test_echo_helper_exists_and_reads_the_payload(self):
        body = function_body(self.js, "echoTaskPrompt")
        self.assertTrue(body, "agent-chat.js 缺少 echoTaskPrompt()")
        self.assertIn("prompt", body, "回声实现没有读载荷里的 prompt")
        self.assertIn("addUser(", body, "回声实现没有出用户气泡")
        self.assertNotIn("ui_action", body, "回声不得按 ui_action 走写死文案")

    def test_no_prompt_text_table_in_the_left_pane(self):
        for token in ("PROMPT_TEXTS", "UI_ACTION_PROMPT", "ECHO_TEXTS"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.js, "左侧不得写「动作名 → 文案」的映射表")

    def test_prompt_is_echoed_only_once_per_task(self):
        self.assertRegex(
            self.js, r"(?:const|let|var)\s+\w*[Pp]rompt\w*\s*=\s*new (Set|Map)\(",
            "没有记录已出过气泡的任务，同一任务会重复出气泡",
        )

    def test_bubble_comes_before_the_no_content_early_return(self):
        # ## 479 重指（`docs/specs/chat-echo-must-pair-with-agent-output.md` §5.2 书面授权）：
        # 原断言钉「echoTaskPrompt 的字面位置在 hasContent 之前」，那是"回声先落、卡后建"的
        # 旧口径；479 改成"先判定后回声 + 回声挂在这一张卡上方"（回声要有 Agent 输出成对），
        # 落位必然排在建卡之后。本断言保留原意图 —— 回声不被「没有明细就不建卡」吃掉 ——
        # 改钉「回声判定排在 hasContent 之前」（判定结果参与建卡判定），并补上成对性。
        # 未放宽：既要求判定在前，也要求回声落在 ensureTaskCard() 之后。
        body = function_body(self.js, "renderTaskProgress")
        self.assertTrue(body, "找不到 renderTaskProgress()")
        decide_at = body.find("taskEchoAllowed(")
        guard = re.search(r"hasContent\s*=", body)
        echo_at = body.find("echoTaskPrompt")
        card_at = body.find("ensureTaskCard(")
        self.assertGreaterEqual(decide_at, 0, "renderTaskProgress 没有回声判定")
        self.assertIsNotNone(guard, "找不到「没有明细就不建卡」的判断")
        guard_at = guard.start()
        self.assertLess(decide_at, guard_at,
                        "回声判定必须发生在「没有明细就不建卡」之前：判定结果要参与建卡判定")
        self.assertGreaterEqual(echo_at, 0, "renderTaskProgress 没有调用回声实现")
        self.assertGreaterEqual(card_at, 0, "找不到 ensureTaskCard()")
        self.assertLess(card_at, echo_at,
                        "回声要挂在这一张卡上方（回声与 Agent 输出成对，Spec 479 §2.3）")

    def test_replay_also_echoes_the_prompt(self):
        body = function_body(self.js, "replayTimelineTask")
        self.assertTrue(body, "找不到 replayTimelineTask()")
        self.assertIn("prompt", body, "历史回放没有把 task.prompt 交给渲染入口")

    def test_user_bubble_can_be_anchored_above_this_turn(self):
        body = function_body(self.js, "addUser")
        self.assertTrue(body, "找不到 addUser()")
        self.assertIn("insertBefore", body, "用户气泡必须能插到本轮助手卡之前")
        self.assertIn("tinner.append", body, "没有锚点时仍要照旧追加到会话流末尾")

    def test_prompt_is_persisted_with_the_task_card(self):
        match = re.search(r"function persistTaskCard\(([^)]*)\)", self.js)
        self.assertIsNotNone(match, "找不到 persistTaskCard()")
        self.assertIn("prompt", match.group(1),
                      "任务事件没有落库 prompt：重进项目后气泡会丢")


# --------------------------------------------------------------------- 左侧：一张卡
class SingleExecutionCardContract(unittest.TestCase):
    """C5：执行进度并进本轮助手卡；没有本轮时才新建同款卡。"""

    @classmethod
    def setUpClass(cls):
        cls.js = read(CHAT_JS)
        cls.css = read(CHAT_CSS)

    def test_assistant_turn_context_is_tracked(self):
        assistant = function_body(self.js, "addAssistant")
        self.assertTrue(assistant, "找不到 addAssistant()")
        self.assertIn("activeTurnCtx", assistant, "新增助手卡时没有记下当前轮")

    def test_turn_context_is_cleared_when_the_turn_ends(self):
        send = block_from(self.js, "async function send(")
        self.assertTrue(send, "找不到 send()")
        self.assertRegex(send, r"activeTurnCtx\s*=\s*null",
                         "一轮结束后必须清空当前轮，否则下次执行会并进上一轮的卡")

    def test_replay_does_not_merge_rows(self):
        assistant = function_body(self.js, "addAssistant")
        self.assertIn("replayingHistory", assistant,
                      "历史回放不是实时轮，不得跨行合流")

    def test_task_card_merges_into_the_current_turn(self):
        body = function_body(self.js, "ensureTaskCard")
        self.assertTrue(body, "找不到 ensureTaskCard()")
        self.assertIn("activeTurnCtx", body, "任务卡没有并入当前轮助手卡")

    def test_standalone_task_card_is_the_assistant_card(self):
        body = function_body(self.js, "ensureTaskCard")
        self.assertRegex(body, r"oc-amsg[^\"'`]*oc-task-card",
                         "没有当前轮时新建的卡必须就是助手卡同款（.oc-amsg.oc-task-card）")
        self.assertIn("oc-alabel", body, "任务卡缺少蓝色身份行")
        self.assertIn("oc-task-steps", body, "任务卡缺少进度区")
        self.assertIn("oc-alabel-state", body, "任务卡缺少状态 chip")

    def test_task_chip_keeps_both_class_names(self):
        body = function_body(self.js, "ensureTaskCard")
        self.assertRegex(body, r"oc-alabel-state[^\"'`]*oc-task-state|oc-task-state[^\"'`]*oc-alabel-state",
                         "chip 必须同时带 oc-alabel-state 与 oc-task-state")

    def test_css_keeps_the_chip_on_the_right(self):
        self.assertRegex(
            self.css, r"\.oc-task-card[^{]*\.oc-task-state\s*\{[^}]*margin-left",
            "缺少让任务卡 chip 靠右的规则（.oc-alabel 是行内 flex）",
        )

    def test_task_pipeline_is_kept(self):
        for token in TASK_PIPELINE_TOKENS:
            with self.subTest(token=token):
                self.assertIn(token, self.js, f"任务卡管线 {token} 被删除")

    def test_task_card_states_keep_their_colors(self):
        for status in ("running", "interrupted", "partial", "succeeded", "failed"):
            with self.subTest(status=status):
                self.assertRegex(
                    self.css, rf"\.oc-task-card\.is-{status}\b[^{{]*\.oc-task-state\s*\{{",
                    f"任务卡 {status} 态配色规则被删除",
                )

    def test_bridge_events_are_untouched(self):
        # 桥只登记这三个名字，其余 state 事件按 payload 透传（task-partial 就走这条通路）。
        for event in ("task-progress", "task-completed", "task-failed"):
            with self.subTest(event=event):
                self.assertIn(event, read(BRIDGE), f"桥事件 {event} 被改动")
        self.assertIn("task-partial", self.js, "会话卡不认识 partial 状态了")
        self.assertIn('window.addEventListener("agent:task-progress"', self.js,
                      "后端任务进度 → 会话卡的既有入口被删了")


# ------------------------------------------------- 回声粒度与持久化（修正轮）
_NODE2 = shutil.which("node")

_REPEAT_PROBE = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('vm');
    const runtimePath = process.argv[2];

    const outbox = [];
    const listeners = {};
    const fakeParent = { postMessage: (message) => { outbox.push({ message }); } };
    const windowObj = {
      parent: fakeParent,
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    };
    const context = {
      window: windowObj,
      location: { search: '?project=p1&stage=cost&task_id=t-eval-1', origin: 'http://localhost' },
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
          requestId, projectId: 'p1', stage: 'cost', name, payload,
        },
      };
      listeners['message'].forEach((fn) => fn(event));
    };
    const startPayloads = () =>
      outbox.filter((m) => m.message.type === 'state' && m.message.name === 'task-progress')
            .map((m) => m.message.payload);

    (async () => {
      rt.registerActions({
        costStep: {
          label: '运行成本测算', deferred: true,
          prompt: '把所有零件的成本都算一遍。',
          run: () => ({ ok: true }),
        },
      });
      outbox.length = 0;
      sendCommand('run-1', 'execute-action', { name: 'costStep', step: 'all' });
      await tick();
      const first = startPayloads()[0] || null;
      outbox.length = 0;
      sendCommand('run-2', 'execute-action', { name: 'costStep', step: 'all' });
      await tick();
      const second = startPayloads()[0] || null;
      console.log(JSON.stringify({ first, second }));
    })();
    """
)


@unittest.skipUnless(_NODE2, "需要 node 才能真跑看板运行时的消息分发")
class EchoPerExecutionBehavior(unittest.TestCase):
    """C3（修正）：同一次执行只出一条气泡，两次执行各自都要出一条。"""

    @classmethod
    def setUpClass(cls):
        workdir = Path(tempfile.mkdtemp(prefix="cpq-echo-run-"))
        probe = workdir / "probe.js"
        probe.write_text(_REPEAT_PROBE, encoding="utf-8")
        completed = subprocess.run(
            [_NODE2, str(probe), str(RUNTIME)],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        if completed.returncode != 0:
            raise AssertionError("看板运行时探针执行失败：\n" + (completed.stderr or completed.stdout))
        cls.report = json.loads(completed.stdout.strip().splitlines()[-1])

    def test_each_run_carries_its_own_id(self):
        first, second = self.report["first"], self.report["second"]
        self.assertIsNotNone(first, "第一次执行没有发出启动事件")
        self.assertIsNotNone(second, "第二次执行没有发出启动事件")
        self.assertTrue(str(first.get("runId") or "").strip(),
                        f"启动事件没有带「这一次执行」的唯一标识，左侧只能按项目 / 动作名去重：{first}")
        self.assertTrue(str(second.get("runId") or "").strip(),
                        f"第二次执行的启动事件同样要有自己的标识：{second}")
        self.assertNotEqual(first.get("runId"), second.get("runId"),
                            "两次执行的标识相同，第二次就不会再出「我：…」气泡")

    def test_left_dedupes_by_that_id_not_by_action_name(self):
        body = function_body(read(CHAT_JS), "echoTaskPrompt")
        self.assertTrue(body, "找不到 echoTaskPrompt()")
        self.assertIn("runId", body,
                      "回声没有按「这一次执行」去重：同一动作第二次执行不会再出气泡")


class EchoPersistenceContract(unittest.TestCase):
    """C6（修正）：回声气泡本身也要能重进项目后恢复，并按顺序排在执行卡前面。"""

    @classmethod
    def setUpClass(cls):
        cls.js = read(CHAT_JS)

    def test_echo_is_persisted_as_a_user_entry(self):
        body = function_body(self.js, "echoTaskPrompt")
        self.assertTrue(body, "找不到 echoTaskPrompt()")
        self.assertIn("persistSessionEvent(", body,
                      "回声只留在当前页面 DOM，重进项目就没了")
        self.assertRegex(body, r"kind\s*:\s*[\"']user[\"']",
                         "回声必须以「用户气泡」这一种条目落库，回放才是用户气泡")
        self.assertIn("runId", body, "落库的 key 必须带这一次执行的标识，避免与其它执行串台")

    def test_replay_still_renders_user_entries_as_bubbles(self):
        self.assertRegex(self.js, r"type\s*===\s*[\"']user[\"'][\s\S]{0,80}addUser\(",
                         "回放不认识「用户气泡」条目，落库了也恢复不出来")


# --------------------------------------------------------------------- 保护边界
class ProtectedBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read(CHAT_JS)
        cls.css = read(CHAT_CSS)
        cls.main = read(MAIN)

    def test_user_bubble_stays_primary_filled(self):
        block = re.search(r"\.oc-ubub\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(block, "用户气泡规则被删除")
        self.assertIn("var(--oc-accent)", block.group(1), "用户气泡仍是主色实心")
        self.assertNotIn("#ffffff", block.group(1), "用户气泡不得变白底")

    def test_the_six_and_seven_kinds_gain_no_bubble(self):
        for name in NO_ECHO_FUNCTIONS:
            with self.subTest(name=name):
                body = function_body(self.js, name)
                self.assertTrue(body, f"找不到 {name}()")
                self.assertNotIn("addUser(", body, f"{name} 不该出用户气泡")
                self.assertNotIn("echoTaskPrompt", body, f"{name} 不该出用户气泡")

    def test_confirmation_card_is_untouched(self):
        body = function_body(self.js, "techUiRequestConfirmation")
        self.assertTrue(body, "找不到 techUiRequestConfirmation()")
        self.assertIn("oc-confirm-card", body, "确认卡结构被改动")
        self.assertNotIn("addUser(", body, "确认卡不得变成用户气泡")
        self.assertNotIn("echoTaskPrompt", body, "确认卡不得触发回声")

    def test_no_backend_route_or_business_branch_is_added(self):
        for token in ("echoTaskPrompt", "resolveActionPrompt", "oc-task-card", "oc-amsg"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.main, "不得为这次改动新增后端路由或业务分支")

    def test_tool_trace_and_thinking_stay(self):
        for token in ("oc-art-detail", "oc-thinking", "oc-tool-result"):
            with self.subTest(token=token):
                self.assertIn(token, self.css + self.js, f"{token} 被删除")


if __name__ == "__main__":
    unittest.main()
