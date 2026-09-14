"""红测：2.2 参数推荐一键生成即补全 + 缺项软闸门 + 这一步归工艺经理。

Red 基线（实现前实测）：
  · `aiGenerateParamsFully()` 只在 `required_missing > 0` 时才补全，整合分析
    （`aiRunAll()`）生成参数之后完全没有补全步骤 —— 从「开始整合分析」进来的用户
    看到的是满屏「必填未给出」。
  · `aiConfirmParamsAndNext()` 在必填不齐时直接返回失败，没有「说明缺什么 → 人确认 →
    带着缺口继续」这一步；`cost-review.js` 的 `confirmCostReview.run()` 同理。
  · `cpq-sso.js` 的 `COST_URL_PATTERNS` 仍把 `/integration/params/(autofill|finalize)`
    当成成本接口（整合参数搬回 2.2 之前的历史写法），只读横幅也没把「参数推荐」算进
    工艺经理的步骤。

本批只改前端这三个文件的补全链路、软闸门与权限归属；后端路由与 `_require` 一律不动。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
BOARD = F / "assembly-integration.js"
COST = F / "cost-review.js"
SSO = F / "cpq-sso.js"
RUNTIME = F / "tech-board-runtime.js"
MAIN = ROOT / "tech_app" / "backend" / "main.py"


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


def py_function(source: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?def\s+{re.escape(name)}\s*\([^)]*\)[^:]*:\s*([\s\S]*?)"
        rf"(?=\n(?:async\s+)?def\s+|\n@app\.|\Z)",
        source,
    )
    if not match:
        raise AssertionError(f"找不到 Python 函数 {name}")
    return match.group(1)


class GenerateParamsFillsEveryGap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = read(BOARD)

    def test_generate_params_autofills_all_dictionary_gaps(self):
        body = block_from(self.board, "async function aiGenerateParamsFully()")
        self.assertTrue(body, "找不到 aiGenerateParamsFully()")
        self.assertIn("aiAutoFillParams()", body,
                      "补全要复用 aiAutoFillParams()（补全 + 落库一份实现）")
        self.assertNotIn("required_missing > 0", body,
                         "生成之后的补全不能再只看必填缺口")
        helper = block_from(self.board, "async function aiAutoFillParams()")
        self.assertIn("aiMissingParamFields()", helper,
                      "补全按整张参数表的空格子判断，不只必填")

    def test_integration_analysis_autofills_after_generating_params(self):
        body = block_from(self.board, "async function aiRunAll()")
        self.assertTrue(body, "找不到 aiRunAll()")
        self.assertIn("aiAutoFillParams()", body,
                      "「开始整合分析」生成参数之后必须跟着补全，否则用户看到满屏未给出")
        idx_generate = body.find("aiGenerate(step)")
        idx_fill = body.find("aiAutoFillParams()")
        self.assertGreater(idx_fill, idx_generate,
                           "补全要排在参数生成之后（生成完才有空格子可补）")

    def test_missing_helper_counts_non_required_fields(self):
        body = block_from(self.board, "function aiMissingParamFields()")
        self.assertTrue(body, "找不到 aiMissingParamFields()")
        self.assertIn("!field.filled", body, "按字典字段的 filled 判空")
        self.assertIn("!field.generated", body, "平台生成项（成品编码）不算缺口")
        self.assertNotRegex(body, r"filter\(\s*field\s*=>\s*field\.required\s*\)",
                            "不能只统计必填：字典里的非必填空格子也要补")

    def test_autofill_helper_reuses_existing_pipeline(self):
        body = block_from(self.board, "async function aiAutoFillParams()")
        self.assertTrue(body, "找不到 aiAutoFillParams()")
        self.assertIn("aiParamsAutofill()", body, "补全必须复用既有智能补全")
        self.assertIn("aiParamsFinalize(false)", body, "补上的值要复用既有 finalize 落库")
        self.assertIn("applied", body, "只有真的填进了值才值得写一次 finalize")
        for token in ("fetch(", "/api/projects/"):
            with self.subTest(token=token):
                self.assertNotIn(token, body, f"补全链路不得自己发请求（{token}）")


class MissingItemsAreSoftGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = read(BOARD)
        cls.cost = read(COST)

    def test_params_confirm_asks_before_continuing_with_gaps(self):
        body = block_from(self.board, "async function aiConfirmParamsAndNext()")
        self.assertTrue(body)
        self.assertIn("aiAskProceed(", body,
                      "必填不齐时要先问「确定带着缺口继续吗」，不能直接硬阻断")
        self.assertIn("aiParamsFinalize(true)", body, "既有「确认参数已齐」保留")
        self.assertIn("aiParamsFinalize(false)", body,
                      "人确认继续后要把已填的值落库（finalize 的非校验分支）")
        self.assertIn("aiConfirmStep('params')", body, "再走既有「确认参数推荐」")
        self.assertIn("required-missing", body, "取消时仍要如实回结构化失败")
        self.assertIn("params_confirmed", body)
        self.assertGreater(body.find("aiSetTab('process')"), body.find("params_confirmed"),
                           "只有确认通过之后才允许切到组装工艺")

    def test_ask_proceed_is_a_real_dialog_not_window_confirm(self):
        body = block_from(self.board, "function aiAskProceed(why, options = {})")
        self.assertTrue(body, "找不到 aiAskProceed()")
        self.assertIn("Promise", body, "确认框要用 Promise 返回人的选择")
        self.assertIn("resolve", body)
        self.assertIn("取消", body, "必须给「取消」这一步，人不能被迫继续")
        self.assertIn("继续", body, "必须给「仍要继续」这一步")
        self.assertNotIn("window.confirm", body, "不用浏览器原生 confirm 挡住整页")
        self.assertNotIn("fetch(", body, "确认框不发任何请求")

    def test_cost_confirm_is_soft_but_permission_stays_hard(self):
        block = block_from(self.cost, "confirmCostReview:")
        self.assertTrue(block, "找不到 confirmCostReview 动作")
        self.assertIn("crConfirmBlocker()", block, "既有前置判定保留")
        self.assertIn("crAskProceed(", block, "缺项要改成提示 + 确认后继续")
        self.assertIn("crReadOnlyWhy()", block,
                      "权限类仍是硬阻断：只读身份不给「继续」的选项")
        ask = block_from(self.cost, "function crAskProceed(why, options = {})")
        self.assertTrue(ask, "找不到 crAskProceed()")
        self.assertIn("Promise", ask)
        self.assertIn("取消", ask)
        self.assertIn("crConfirmCost()", block, "继续之后仍走既有确认实现")

    def test_cost_blocker_keeps_readonly_first(self):
        body = block_from(self.cost, "function crConfirmBlocker()")
        self.assertTrue(body, "找不到 crConfirmBlocker()")
        self.assertGreaterEqual(body.find("crReadOnly()"), 0, "只读判断不能删")
        self.assertLess(body.find("crReadOnly()"), body.find("counts.parts"),
                        "只读判断要排在最前面：权限问题不是「没完成」，不给继续的选项")
        self.assertIn("crReadOnlyWhy()", body, "只读原因沿用既有文案")


class ParamsStepBelongsToProcessManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sso = read(SSO)
        cls.main = read(MAIN)

    def test_params_urls_are_not_cost_urls(self):
        start = self.sso.find("var COST_URL_PATTERNS = [")
        end = self.sso.find("];", start)
        self.assertGreater(start, 0, "找不到 COST_URL_PATTERNS")
        self.assertGreater(end, start, "COST_URL_PATTERNS 数组没有收尾")
        body = self.sso[start:end]
        self.assertNotRegex(body, r"integration/params",
                            "参数推荐（整合参数）是 2.2 工艺经理的步骤，不能留在成本接口里")
        self.assertIn(r"\/integration\/cost", body, "成本接口仍要放行给财务经理")

    def test_readonly_bar_names_params_as_process_step(self):
        body = block_from(self.sso, "function showReadonlyBar()")
        self.assertTrue(body)
        self.assertIn("参数推荐", body,
                      "只读横幅要说清参数推荐归工艺经理，不能让财务以为自己能做这一步")

    def test_frontend_gate_still_defers_to_backend(self):
        self.assertIn("state.canWrite || (state.canCost && isCostUrl(url))", self.sso,
                      "能力分流不能删；前端只是提前告知，判定仍在后端")

    def test_backend_role_ownership_unchanged(self):
        for name in ("generate_integration_params", "autofill_integration_params",
                     "finalize_integration_params", "confirm_integration_params",
                     "generate_integration_process"):
            with self.subTest(func=name):
                body = py_function(self.main, name)
                self.assertIn("auth.WRITE_ROLES", body, f"{name} 必须归工艺侧写权限")
                self.assertNotIn("auth.COST_ROLES", body, f"{name} 不得归成本权限")
        cost = py_function(self.main, "generate_integration_cost")
        self.assertIn("auth.COST_ROLES", cost, "成本测算仍归财务")


class NoScopeCreep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = read(BOARD)
        cls.runtime = read(RUNTIME)

    def test_hidden_params_actions_stay_registered(self):
        for name, call in (("autofillIntegrationParams", "aiParamsAutofill()"),
                           ("saveIntegrationParamsFinal", "aiParamsFinalize(false)"),
                           ("confirmIntegrationParamsFinal", "aiParamsFinalize(true)")):
            with self.subTest(action=name):
                block = block_from(self.board, f"{name}:")
                self.assertTrue(block, f"{name} 动作注册不能删")
                self.assertIn(call, block, f"{name} 的既有实现不能删")

    def test_protocol_events_unchanged(self):
        events = re.search(r"var EVENT = \{([\s\S]*?)\};", self.runtime)
        self.assertIsNotNone(events, "找不到 EVENT 常量表")
        names = re.findall(r"\b[A-Z_]+:\s*'([^']+)'", events.group(1))
        self.assertEqual(sorted(names),
                         sorted(["ready", "action-state", "task-progress", "task-completed",
                                 "task-failed", "selection-changed", "board-status"]),
                         "既有事件一个都不能增删")


if __name__ == "__main__":
    unittest.main()
