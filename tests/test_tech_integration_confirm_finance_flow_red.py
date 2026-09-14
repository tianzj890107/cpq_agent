"""红测：2.2 / 3.3 收口链路修复（作用域泄漏、发送财务主按钮）。

用户反馈与要求：
- 点「确认并进入下一页签」报 `aiSetTab is not defined`（并行批次已修 `aiSetTab`，
  本批只管同一个闭包里**仍然漏**的 `aiAnalyzed` / `aiStatusState`，以及 3.3 的 `rpRefreshReport`）。
- 「组装工艺完成之后下一个主按钮应该是确认工艺并发给财务」。

红线：后端路由、`aiFinanceBlocker()` 的真实前置、既有接口与动作名、并行批次已落地的
`confirmParamsAndNext` deferred 实现一个都不能动。
"""
from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


def read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8", errors="replace").replace("\x00", "")


ASSEMBLY = read("assembly-integration.js")
REPORT_PUBLISH = read("report-publish-result.js")
REGISTRY_START = ASSEMBLY.find("(function aiRegisterTechBoardActions()")
REGISTRY_END = len(ASSEMBLY)


def iife_spans(src: str):
    """产出 (start, end, body) —— 每个 `(function name(...) {` 到配平右括号。"""
    for match in re.finditer(r"\(function\s+\w+\s*\([^)]*\)\s*\{", src):
        start = match.start()
        i = match.end() - 1
        depth = 0
        j = i
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        yield start, j + 1, src[i:j + 1]


def block_from(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    tail = idx + len(marker) - 1
    brace = tail if text[tail:tail + 1] == "{" else text.find("{", idx + len(marker))
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


class ScopeLeakGuard(unittest.TestCase):
    PREFIX = re.compile(r"^(ai|cr|rp|rr|cf|sr|qr|oc|tp)[A-Z_$]")

    def test_registry_iife_does_not_leak_prefixed_helpers(self):
        """注册 IIFE 内声明的页面助手，绝不能在 IIFE 外被引用（ReferenceError 的一般形态）。"""
        leaks = []
        for path in sorted(FRONTEND.glob("*.js")):
            src = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
            for start, end, body in iife_spans(src):
                names = set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)", body))
                names |= set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", body))
                outside = src[:start] + src[end:]
                for name in sorted(names):
                    if not self.PREFIX.match(name):
                        continue
                    if re.search(r"\b" + re.escape(name) + r"\b", outside):
                        leaks.append(f"{path.name}:{name}")
        self.assertEqual([], leaks, "注册闭包内的助手被闭包外引用：" + "、".join(leaks))

    def test_assembly_helpers_are_module_scope(self):
        self.assertGreater(REGISTRY_START, 0, "找不到注册闭包")
        for name in ("aiStatusState", "aiAnalyzed", "aiSetTab"):
            with self.subTest(helper=name):
                idx = ASSEMBLY.find(f"function {name}(")
                self.assertGreater(idx, 0, f"{name} 必须在模块作用域有唯一实现")
                self.assertLess(idx, REGISTRY_START, f"{name} 必须定义在注册闭包之前")
                self.assertNotIn(f"const {name} =", ASSEMBLY,
                                 f"{name} 不得在注册闭包里再声明一份 const（会造成作用域泄漏）")

    def test_report_publish_refresh_helper_is_module_scope(self):
        registry = REPORT_PUBLISH.find("(function rpRegisterTechBoardActions()")
        self.assertGreater(registry, 0, "找不到 3.3 的注册闭包")
        idx = REPORT_PUBLISH.find("function rpRefreshReport(")
        self.assertGreater(idx, 0, "rpRefreshReport 必须在模块作用域有唯一实现")
        self.assertLess(idx, registry,
                        "rpRefreshReport 被闭包外的「回传销售经理」调用，必须提升到模块作用域")
        self.assertNotIn("async function rpRefreshReport(", REPORT_PUBLISH[registry:])


class ProcessTabPrimaryIsConfirmAndSendFinance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.finance = block_from(ASSEMBLY, "sendIntegrationToFinance: {")
        cls.confirm_next = block_from(ASSEMBLY, "confirmProcessAndNext: {")
        cls.generate = block_from(ASSEMBLY, "generateIntegrationProcess: {")
        cls.chain = block_from(ASSEMBLY, "async function aiConfirmProcessAndSendToFinance(")
        cls.bg = block_from(ASSEMBLY, "async function aiSendToFinanceInBackground(")

    def order_of(self, block: str) -> int:
        match = re.search(r"order:\s*(\d+)", block)
        self.assertTrue(match, "动作块里找不到 order")
        return int(match.group(1))

    def test_finance_is_primary_after_process_is_generated(self):
        self.assertRegex(self.finance, r"role:\s*[^,}\n]*'primary'",
                         "组装工艺生成后主按钮应是「确认工艺并发送财务」")
        self.assertRegex(self.confirm_next, r"visible:\s*false",
                         "2.3 是财务经理那一步，「确认并进入下一步」不再占工艺经理的左侧栏")
        self.assertIn("aiConfirmProcessAndNext()", self.confirm_next,
                      "动作注册与实现必须保留（能力不缩水），只是不再出现在左侧栏")
        self.assertRegex(self.generate, r"role:\s*[^,}\n]*'primary'",
                         "工艺还没生成时主按钮仍是「一键生成组装工艺」")

    def test_list_order_puts_finance_after_generate(self):
        self.assertLess(self.order_of(self.generate), self.order_of(self.finance),
                        "组装工艺生成后，主按钮是紧接它的「确认工艺并发送财务」")
        self.assertEqual(45, self.order_of(self.confirm_next), "退出左侧栏的动作保留原 order")

    def test_finance_entry_is_deferred_and_only_starts_the_work(self):
        self.assertIn("deferred: true", self.finance,
                      "弹窗选接收人要人操作：deferred 才能不被桥的 20 秒超时误判")
        self.assertIn("aiSendToFinanceInBackground(", self.finance, "动作条目只负责启动后台链路")
        run_block = block_from(self.finance, "run:")
        self.assertTrue(run_block, "动作条目缺少 run")
        self.assertNotIn("await ", run_block, "run 里不能再等接口 / 弹窗")
        self.assertIn("aiFinanceBlocker(", run_block, "真实闸门留在同步回执里")

    def test_background_chain_settles_its_own_action(self):
        self.assertTrue(self.bg, "缺少 aiSendToFinanceInBackground")
        self.assertIn("aiSettleIfNeeded('task-completed', 'sendIntegrationToFinance'", self.bg)
        self.assertIn("aiSettleIfNeeded('task-failed', 'sendIntegrationToFinance'", self.bg)
        self.assertIn("aiDeferredBusy = false", self.bg)
        self.assertIn("updateActionState('sendIntegrationToFinance', { busy: false })", self.bg)

    def test_finance_chain_confirms_process_then_opens_dialog(self):
        self.assertTrue(self.chain, "缺少 aiConfirmProcessAndSendToFinance 链路")
        self.assertIn("aiConfirmStep('process')", self.chain,
                      "未确认的先按既有 /process/confirm 确认掉，不做死按钮")
        self.assertIn("aiFinanceBlocker(", self.chain)
        self.assertIn("aiOpenFinanceDialog(", self.chain)
        self.assertLess(self.chain.find("aiConfirmStep('process')"),
                        self.chain.find("aiOpenFinanceDialog("),
                        "先确认工艺，再打开发送财务弹窗")


class CapabilitiesNotReduced(unittest.TestCase):
    def test_existing_endpoints_and_helpers_stay(self):
        for token in ("/integration/params/finalize", "/integration/params/autofill",
                      "/params/confirm", "/process/confirm", "send-to-finance",
                      "aiRequiredGaps(", "aiParamsAutofill(", "aiOpenFinanceDialog(",
                      "aiSettleIfNeeded(", "aiAskProceed("):
            with self.subTest(token=token):
                self.assertIn(token, ASSEMBLY)

    def test_parallel_batch_work_is_left_alone(self):
        # 并行批次（tech-confirm-actions-no-timeout-and-no-failure-cards）的落点不得被本批改回。
        entry = block_from(ASSEMBLY, "confirmParamsAndNext: {")
        self.assertIn("deferred: true", entry, "并行批次给参数页收口加的 deferred 不能被撤掉")
        self.assertIn("order: 35", entry)
        self.assertRegex(entry, r"visible:\s*aiTab === 'params'")

    def test_hidden_actions_and_views_stay_registered(self):
        for name in ("confirmIntegrationParamsFinal", "saveIntegrationParamsFinal",
                     "autofillIntegrationParams", "confirmIntegrationParams",
                     "confirmIntegrationProcess", "saveIntegrationParams",
                     "refreshIntegration", "runIntegration", "integrationStep",
                     "openIntegrationDrawings", "confirmDrawingsAndNext",
                     "generateIntegrationParams", "generateIntegrationProcess"):
            with self.subTest(action=name):
                self.assertIn(f"{name}: {{", ASSEMBLY, f"动作注册被删除：{name}")
        for view in ("drawings:", "params:", "process:"):
            with self.subTest(view=view):
                self.assertIn(view, block_from(ASSEMBLY, "registerViews({"))

    def test_drawings_and_params_entry_points_stay(self):
        drawings = block_from(ASSEMBLY, "confirmDrawingsAndNext: {")
        self.assertRegex(drawings, r"role:\s*show \? 'primary' : 'aux'")
        self.assertIn("aiConfirmDrawingsAndNext(", drawings)
        params = block_from(ASSEMBLY, "generateIntegrationParams: {")
        self.assertRegex(params, r"role:\s*aiHasParams\(\) \? 'aux' : 'primary'")


if __name__ == "__main__":
    unittest.main()
