"""红测：任务收尾要有终态「中断」（沿用蓝色，不改配色）。

Red 基线（实现前实测）：
  · `agent-chat.js` 的 `taskStatusWord()` 只有四态，未知状态兜底「进行中」——
    `interrupted` 会显示成「进行中」。
  · `setTaskStatus()` 只切 `is-queued / is-running / is-succeeded / is-failed`，
    没有 `is-interrupted`；`.oc-task-state` 也没有中断态配色。
  · 桥切看板（`detached`）与超时（`timeout`）后，在途任务卡没有任何收尾——永远「进行中」。
  · `aiPollTask()` / `crPollTask()` / `pollMatchTask()` 只认 succeeded / failed，
    服务重启中断的任务会被无限轮询。
  · `recover_interrupted_tasks()` 把在途任务写成 `failed`，且不写会话时间线，
    重进项目只剩「进行中」。

本批只做「中断」这一个终态：任务卡 + 阶段页过程卡 + 轮询收尾 + 后端恢复标记。
桥协议事件名、静默失败码、后端路由、助手卡三态、阶段白名单一律不动。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
CHAT = F / "agent-chat.js"
CSS = F / "agent-chat.css"
BOARD = F / "assembly-integration.js"
COST = F / "cost-review.js"
BRIDGE = F / "tech-board-bridge.js"
TASKS = ROOT / "tech_app" / "backend" / "services" / "tasks.py"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 「中断」沿用的就是进行中的那套蓝色——不新增颜色 token。
BLUE_BG = "#e0edff"
BLUE_FG = "#0050C4"
RED_TOKENS = ("#b91c1c", "#dc2626", "#fee2e2", "#ef4444")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def rule(text: str, selector: str) -> str:
    """返回单个 CSS 选择器的规则体（选择器后必须紧跟 `{`，避免前缀误命中）。"""
    match = re.search(re.escape(selector) + r"\s*\{", text)
    if not match:
        return ""
    start = text.find("{", match.start())
    end = text.find("}", start)
    return text[start:end + 1] if end > 0 else ""


def decl(body: str, name: str) -> str:
    match = re.search(rf"(?:^|[;{{\s]){re.escape(name)}\s*:\s*([^;}}]+)", body)
    return match.group(1).strip() if match else ""


def py_func(text: str, name: str) -> str:
    """按顶层 `def name(` 取函数体：Python 有文档字符串，不能用花括号配平。"""
    start = text.find(f"def {name}(")
    if start < 0:
        return ""
    nxt = text.find("\ndef ", start + 1)
    return text[start:] if nxt < 0 else text[start:nxt]


def block_from(text: str, marker: str) -> str:
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
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    break
                i += 1
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


class TestRead(unittest.TestCase):
    pass


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = read(CHAT)
        cls.css = read(CSS)
        cls.board = read(BOARD)
        cls.cost = read(COST)
        cls.bridge = read(BRIDGE)
        cls.tasks = read(TASKS)
        cls.main = read(MAIN)


# ----------------------------------------------------------------- 词汇与状态
class InterruptedVocabulary(Base):
    def test_status_word_has_interrupted(self):
        body = block_from(self.chat, "function taskStatusWord(")
        self.assertTrue(body, "找不到 taskStatusWord()")
        self.assertIn("interrupted", body, "状态词表必须登记 interrupted")
        self.assertIn("中断", body, "interrupted 的中文必须是「中断」")

    def test_set_task_status_switches_interrupted_class(self):
        body = block_from(self.chat, "function setTaskStatus(")
        self.assertTrue(body, "找不到 setTaskStatus()")
        self.assertIn("is-interrupted", body, "状态切换必须处理 is-interrupted")
        self.assertRegex(body, r"is-interrupted[\s\S]*is-\$\{|`is-\$\{",
                         "必须按同一个类模板加 is-<status>")

    def test_unknown_fallback_kept(self):
        body = block_from(self.chat, "function taskStatusWord(")
        self.assertIn("进行中", body, "未知状态的既有兜底不得被顺手删掉")

    def test_persist_and_replay_unchanged(self):
        self.assertIn("function persistTaskCard(", self.chat, "任务卡落库入口不得删除")
        self.assertIn("function replayTimelineTask(", self.chat, "任务卡回放入口不得删除")


# ----------------------------------------------------------------- 蓝色沿用
class InterruptedChipKeepsRunningBlue(Base):
    def test_task_state_interrupted_same_blue_as_running(self):
        running = rule(self.css, ".oc-task-card.is-running .oc-task-state")
        interrupted = rule(self.css, ".oc-task-card.is-interrupted .oc-task-state")
        self.assertTrue(running, "缺少进行中 chip 规则")
        self.assertTrue(interrupted, "缺少中断 chip 规则")
        self.assertEqual(decl(running, "background"), decl(interrupted, "background"),
                         "中断必须沿用进行中的背景色（不改配色）")
        self.assertEqual(decl(running, "color"), decl(interrupted, "color"),
                         "中断必须沿用进行中的文字色（不改配色）")
        self.assertIn(BLUE_BG, interrupted, "中断背景必须是既有蓝色 #e0edff")
        self.assertIn(BLUE_FG, interrupted, "中断文字必须是既有蓝色 #0050C4")

    def test_alabel_state_interrupted_same_blue_as_running(self):
        running = rule(self.css, ".oc-alabel-state.is-running")
        interrupted = rule(self.css, ".oc-alabel-state.is-interrupted")
        self.assertTrue(running, "缺少身份行进行中 chip 规则")
        self.assertTrue(interrupted, "缺少身份行中断 chip 规则")
        self.assertEqual(decl(running, "background"), decl(interrupted, "background"),
                         "阶段页过程卡的中断 chip 同样沿用蓝色")
        self.assertEqual(decl(running, "color"), decl(interrupted, "color"),
                         "阶段页过程卡的中断 chip 同样沿用蓝色")

    def test_interrupted_reason_line_is_neutral_not_red(self):
        note = rule(self.css, ".oc-task-note")
        self.assertTrue(note, "缺少中断原因行 .oc-task-note")
        for token in RED_TOKENS:
            self.assertNotIn(token, note, f"中断原因行不得用红色 {token}")

    def test_failure_red_line_kept(self):
        error = rule(self.css, ".oc-task-error")
        self.assertTrue(error, "真正的失败仍要保留红字原因行 .oc-task-error")


# ----------------------------------------------------------------- 判定入口
class SingleInterruptedCodeJudge(Base):
    def test_codes_are_collected_in_one_helper(self):
        match = re.search(r"INTERRUPTED_CODES\s*=\s*\[([^\]]*)\]", self.chat)
        self.assertIsNotNone(match, "找不到中断码集合 INTERRUPTED_CODES")
        names = re.findall(r"[\"']([^\"']+)[\"']", match.group(1))
        self.assertEqual(names, ["interrupted", "detached", "timeout"],
                         "中断码固定为服务重启 / 切看板取消 / 桥超时三个来源")
        self.assertRegex(self.chat, r"function\s+isInterruptedCode\s*\(",
                         "中断判定必须收口到一个函数")

    def test_render_task_progress_uses_the_judge(self):
        body = block_from(self.chat, "function renderTaskProgress(")
        self.assertTrue(body, "找不到 renderTaskProgress()")
        self.assertIn("isInterruptedCode(", body, "渲染必须走同一个中断判定")
        self.assertRegex(body, r"[\"']interrupted[\"']", "必须真的产出 interrupted 状态")

    def test_interrupted_reason_goes_to_neutral_line(self):
        body = block_from(self.chat, "function renderTaskProgress(")
        self.assertRegex(body, r'status\s*===\s*"interrupted"[\s\S]{0,320}oc-task-note',
                         "中断原因必须写进中性的 .oc-task-note，不能刷红字")
        self.assertRegex(body, r'status\s*===\s*"failed"[\s\S]{0,320}oc-task-error',
                         "失败的红字原因行不得被中断改动顶掉")


# ----------------------------------------------------------------- 在途收尾
class InflightCardsNeverStayRunning(Base):
    def test_interrupt_helper_skips_terminal_cards(self):
        body = block_from(self.chat, "function interruptRunningCards(")
        self.assertTrue(body, "缺少把在途卡翻成中断的 interruptRunningCards()")
        for token in ("done", "succeeded", "failed", "interrupted"):
            self.assertIn(token, body, f"中断收尾必须跳过终态（缺 {token}）")
        self.assertIn("renderTaskProgress(", body, "中断收尾必须复用同一套渲染")

    def test_detached_interrupts_inflight_cards(self):
        body = block_from(self.chat, "function bindBoardBridge(")
        self.assertTrue(body, "找不到 bindBoardBridge()")
        detached = re.search(r'name\s*===\s*"detached"[\s\S]{0,400}?\n      \}', body)
        self.assertIsNotNone(detached, "找不到 detached 分支")
        self.assertIn("interruptRunningCards(", detached.group(0),
                      "切看板后不会再有收尾事件：在途卡必须标成中断")
        self.assertNotRegex(detached.group(0), r"clear\(\)", "detached 不得清空已落库历史")

    def test_bridge_timeout_interrupts_inflight_cards(self):
        body = block_from(self.chat, "function bindBoardBridge(")
        self.assertRegex(body, r'type[\s\S]{0,40}===\s*"error"[\s\S]{0,200}isInterruptedCode\(',
                         "桥超时（type=error + code=timeout）必须进中断判定")
        self.assertIn("interruptRunningCards(", body, "桥超时后必须把在途卡标成中断")


# ----------------------------------------------------------------- 轮询收尾
class PollersTreatInterruptedAsTerminal(Base):
    def _assert_poll(self, text, marker):
        body = block_from(text, marker)
        self.assertTrue(body, f"找不到 {marker}")
        self.assertIn("interrupted", body, f"{marker} 必须把中断当终态，不能无限轮询")
        self.assertRegex(body, r"code:\s*'interrupted'", f"{marker} 必须带中断码上报")

    def test_22_poller_and_publish(self):
        self._assert_poll(self.board, "async function aiPollTask(")
        publish = re.findall(r"aiPublishTask\('task-failed'[\s\S]{0,260}?\}\)", self.board)
        self.assertTrue(publish, "2.2 找不到 task-failed 上报")
        self.assertTrue(any("interrupted" in chunk for chunk in publish),
                        "2.2 的中断必须按中断码上报，父壳才标「中断」而不是「失败」")

    def test_23_poller_and_publish(self):
        self._assert_poll(self.cost, "async function crPollTask(")
        publish = re.findall(r"crPublishTask\('task-failed'[\s\S]{0,260}?\}\)", self.cost)
        self.assertTrue(publish, "2.3 找不到 task-failed 上报")
        self.assertTrue(any("interrupted" in chunk for chunk in publish),
                        "2.3 的中断必须按中断码上报")

    def test_shell_match_poller(self):
        body = block_from(self.chat, "async function pollMatchTask(")
        self.assertTrue(body, "找不到 pollMatchTask()")
        self.assertIn("interrupted", body, "会话侧检索轮询同样要把中断当终态")

    def test_stage_process_cards_support_interrupted(self):
        for text, marker, where in ((self.board, "function aiProcessCard(", "2.2"),
                                    (self.cost, "function crCard(", "2.3")):
            with self.subTest(where=where):
                body = block_from(text, marker)
                self.assertTrue(body, f"找不到 {marker}")
                self.assertIn("is-interrupted", body, f"{where} 过程卡必须支持中断态")
                self.assertIn("中断", body, f"{where} 过程卡中断文案必须是「中断」")


# ----------------------------------------------------------------- 后端恢复
class BackendRecoveryMarksInterrupted(Base):
    def test_recovery_writes_interrupted_terminal_state(self):
        body = py_func(self.tasks, "recover_interrupted_tasks")
        self.assertTrue(body, "找不到 recover_interrupted_tasks()")
        self.assertRegex(body, r'"status":\s*"interrupted"',
                         "服务重启中断必须落成 interrupted，而不是 failed")
        self.assertNotRegex(body, r'"status":\s*"failed"',
                            "failed 不再表示中断")
        self.assertIn("服务重启中断", body, "进度文案保留可追溯的「服务重启中断」")
        self.assertIn('"error"', body, "必须保留真实原因")
        self.assertIn("finished_at", body, "中断也是终态，必须有 finished_at")

    def test_recovery_writes_the_interrupted_card_into_timeline(self):
        body = py_func(self.tasks, "recover_interrupted_tasks")
        self.assertIn("append_session_event(", body,
                      "浏览器关着时被中断的任务必须写进会话时间线，重进项目才看得到")
        self.assertIn('"kind": "task"', body, "时间线条目必须是任务卡")
        self.assertRegex(body, r'"key":\s*f"task:\{', "任务卡必须按 task:<id> 幂等落库")


# ----------------------------------------------------------------- 不越界
class ProtocolAndSurfacesUnchanged(Base):
    def test_bridge_protocol_untouched(self):
        codes = re.search(r"QUIET_FAILURE_CODES\s*=\s*\[([^\]]*)\]", self.bridge)
        self.assertIsNotNone(codes, "找不到 QUIET_FAILURE_CODES")
        names = re.findall(r"'([^']+)'", codes.group(1))
        self.assertEqual(names, ["detached", "note-target-missing", "missing-comment", "no-selection"],
                         "桥的预期内失败码不得增删")
        self.assertIn("var DEFAULT_TIMEOUT = 20000;", self.bridge, "桥超时值不得改动")
        self.assertNotIn("interrupted", self.bridge,
                         "桥协议不新增事件名 / 状态名：中断只加在会话与任务卡这一层")

    def test_assistant_chip_still_three_states(self):
        body = block_from(self.chat, "function setAssistantState(")
        for glyph in ("◌", "✓", "⚠"):
            self.assertIn(glyph, body, f"助手回复卡仍保留三态图标 {glyph}")
        self.assertNotIn("⏸", body, "助手回复卡不新增中断态（中断只加在任务卡与过程卡）")

    def test_backend_routes_untouched(self):
        self.assertEqual(self.main.count('"interrupted"'), 0,
                         "本批不新增后端路由，也不在路由层写字面状态")
        self.assertEqual(len(re.findall(r"recover_interrupted_tasks", self.main)), 1,
                         "既有恢复调用点保持唯一，不新增路由")

    def test_task_card_pipeline_kept(self):
        for token in ("function renderTaskProgress(", "function ensureTaskCard(",
                      "function pushTaskStep(", "ocTaskProgressHost"):
            self.assertIn(token, self.chat, f"{token} 不得删除")
        self.assertIn(".oc-task-steps", self.css, "任务卡步骤区样式不得删除")


if __name__ == "__main__":
    unittest.main()
