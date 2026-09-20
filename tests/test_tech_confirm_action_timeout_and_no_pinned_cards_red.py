"""红测：2.2 / 2.3 收口动作不再超时、失败不再建底部卡片。

Red 基线（实现前实测）：
  · `aiSetTab` 只在 `aiRegisterTechBoardActions()` 的 IIFE 里用 `const` 声明，
    外层 `aiConfirmDrawingsAndNext()` / `aiConfirmParamsAndNext()` 调用它就是
    ReferenceError「aiSetTab is not defined」。
  · 「确认并进入下一页签」「确认成本」仍是普通动作，`run()` 会 await 人工确认框 ——
    桥的 20s 超时把人的思考时间算成了「超时未响应」。
  · `renderTaskProgress()` 的 `keepFailure` 让「只有失败原因、没有任何执行明细」的事件
    也新建 `.oc-task-card`，而它长在 `#ocTaskProgressHost`（会话底部常驻宿主），
    建出来就永远钉在底部。

本批只改前端三个文件：`aiSetTab` 归位、两个收口动作改 deferred、失败不再单独建卡。
桥协议、后端、业务实现一律不动。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
BOARD = F / "assembly-integration.js"
COST = F / "cost-review.js"
CHAT = F / "agent-chat.js"
BRIDGE = F / "tech-board-bridge.js"
PARENT = F / "tech-workbench.js"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


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


class SetTabIsModuleScoped(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = read(BOARD)

    def test_ai_set_tab_is_a_module_level_function(self):
        self.assertRegex(self.board, r"\nfunction aiSetTab\s*\(",
                         "aiSetTab 必须声明在模块作用域（收口函数在外层调用它）")
        self.assertNotIn("const aiSetTab", self.board,
                         "IIFE 里的 const aiSetTab 会让外层调用变成 ReferenceError")

    def test_confirm_actions_still_switch_tabs(self):
        for marker, tab in (("async function aiConfirmDrawingsAndNext()", "params"),
                            ("async function aiConfirmParamsAndNext()", "process")):
            with self.subTest(tab=tab):
                body = block_from(self.board, marker)
                self.assertTrue(body, f"找不到 {marker}")
                self.assertIn(f"aiSetTab('{tab}')", body, "收口后仍要切页签")


class ConfirmActionsDoNotBlockTheBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = read(BOARD)
        cls.cost = read(COST)

    def test_confirm_params_action_is_deferred(self):
        block = block_from(self.board, "confirmParamsAndNext:")
        self.assertTrue(block, "找不到 confirmParamsAndNext 动作")
        self.assertIn("deferred: true", block,
                      "人工确认框不能算进桥的超时：动作必须 deferred")
        self.assertNotIn("run: () => aiConfirmParamsAndNext()", block,
                         "run 不得直接把需要人工确认的链路交给桥等待")
        self.assertIn("aiConfirmParamsAndNext()", block, "后台链路复用既有收口函数")
        self.assertRegex(block, r"return\s*\{\s*ok:\s*true\s*\}", "run 必须立即回执成功")

    def test_confirm_cost_action_is_deferred(self):
        block = block_from(self.cost, "confirmCostReview:")
        self.assertTrue(block, "找不到 confirmCostReview 动作")
        self.assertIn("deferred: true", block, "确认成本同样不能把人工确认算进超时")
        self.assertNotIn("run: async () =>", block, "run 不得返回需要人工确认的 Promise")
        self.assertRegex(block, r"return\s*\{\s*ok:\s*true\s*\}", "run 必须立即回执成功")

    def test_background_failures_report_in_normal_output(self):
        for text, marker, status_call, say_call in (
                (self.board, "confirmParamsAndNext:", "aiStatus(", "aiSay("),
                (self.cost, "confirmCostReview:", "crStatus(", "crSay(")):
            with self.subTest(action=marker):
                block = block_from(text, marker)
                self.assertIn(status_call, block, "失败原因要进标题行 / 本页状态位")
                self.assertIn(say_call, block, "失败原因要按普通输出进会话")
                self.assertNotIn("task-failed", block,
                                 "失败不再发布任务卡事件（那会钉在会话底部）")
                self.assertIn("catch", block, "后台链路必须自己吞掉异常并如实播报")

    def test_soft_gate_and_existing_impls_kept(self):
        body = block_from(self.board, "async function aiConfirmParamsAndNext()")
        for token in ("aiAskProceed(", "aiParamsFinalize(true)", "aiParamsFinalize(false)",
                      "aiConfirmStep('params')", "required-missing", "params_confirmed"):
            with self.subTest(token=token):
                self.assertIn(token, body, f"{token} 不能被删")
        cost = block_from(self.cost, "confirmCostReview:")
        for token in ("crConfirmBlocker()", "crAskProceed(", "crReadOnlyWhy()", "crConfirmCost()"):
            with self.subTest(token=token):
                self.assertIn(token, cost, f"{token} 不能被删")


class FailuresNeverCreatePinnedCards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = read(CHAT)

    def test_failure_alone_never_creates_a_card(self):
        body = block_from(self.chat, "function renderTaskProgress(")
        self.assertTrue(body, "找不到 renderTaskProgress()")
        self.assertNotIn("keepFailure", body,
                         "失败不再单独建卡：卡片长在会话底部宿主里，建出来就钉住")
        self.assertRegex(body, r"hasContent\s*=\s*log\.length\s*>\s*0\s*\|\|\s*existingCard",
                         "建卡闸门只认真实执行明细或已在运行的卡")

    def test_existing_card_still_shows_the_failure_reason(self):
        body = block_from(self.chat, "function renderTaskProgress(")
        self.assertIn("oc-task-error", body, "已有卡仍要就地显示真实失败原因")
        self.assertRegex(body, r'status\s*===\s*"failed"', "失败态处理保留")

    def test_progress_log_cards_kept(self):
        body = block_from(self.chat, "function renderTaskProgress(")
        self.assertIn("pushTaskStep(", body, "有执行明细的任务卡照旧渲染步骤")
        self.assertIn("taskProgressCards.has(", body, "同 taskId 不重复建卡")


class ProtocolAndSurfacesUnchanged(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = read(BRIDGE)
        cls.parent = read(PARENT)
        cls.board = read(BOARD)
        cls.cost = read(COST)

    def test_bridge_timeout_and_quiet_codes_unchanged(self):
        self.assertIn("var DEFAULT_TIMEOUT = 20000;", self.bridge, "桥超时值不得改动")
        codes = re.search(r"QUIET_FAILURE_CODES = \[([^\]]*)\]", self.bridge)
        self.assertIsNotNone(codes, "找不到 QUIET_FAILURE_CODES")
        names = re.findall(r"'([^']+)'", codes.group(1))
        self.assertEqual(names, ["detached", "note-target-missing", "missing-comment", "no-selection"],
                         "预期内失败码不得增删")

    def test_business_failures_stay_visible_in_both_places(self):
        body = block_from(self.parent, "function runBoardAction(")
        self.assertIn("setBoardNotice(message, 'error')", body, "标题行提示位必须保留")
        self.assertIn("chatNotice(message)", body, "会话里的普通输出必须保留")

    def test_action_registry_and_labels_untouched(self):
        block = block_from(self.board, "confirmParamsAndNext:")
        self.assertIn("label: '确认并进入下一页签'", block, "动作文案不得改动")
        self.assertRegex(block, r"role:\s*'primary'", "主按钮身份不得改动")
        self.assertRegex(block, r"order:\s*35", "order 不得改动")
        cost = block_from(self.cost, "confirmCostReview:")
        self.assertIn("label: '确认成本'", cost, "动作文案不得改动")
        self.assertRegex(cost, r"order:\s*20", "order 不得改动")


if __name__ == "__main__":
    unittest.main()
