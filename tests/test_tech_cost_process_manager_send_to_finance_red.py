"""红测：2.3 成本测算页对工艺经理只留「发送给财务」主按钮（实现前应失败）。

现状缺口（实测）：
  · `cost-review.js` 注册的五颗财务动作（runCostReview / confirmCostReview /
    writeCostReviewMaterial / sendCostReviewToQuote / returnCostReviewToProcess）
    对谁都是 `visible: true`：工艺经理打开 2.3，左侧操作栏摆着五颗他点不动的按钮
    （`crReadOnly()` 全挡），而这一页他真正该做的「把工艺与整机参数交给财务」没有入口。
  · 页内 `.ai-ops` 同样只摆 `#crConfirm` / `#crWriteDb` / `#crToQuote` / `#crReturn`。

要求：
  · 财务经理（`crReadOnly()` 为假）：行为不变，仍是「一键测算全部成本 → 确认成本」。
  · 工艺经理（`crReadOnly()` 为真）：五颗财务动作不占操作栏，唯一主按钮是「发送给财务」，
    走既有 2.2 出口 `POST /api/projects/{id}/integration/send-to-finance`（不新建第二套
    发送实现、不在 2.3 重写 2.2 的派发弹窗）。
  · 动作注册、Agent 工具、看板桥协议、后端路由与权限判定都不缩水。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
COST_JS = F / "cost-review.js"
COST_HTML = F / "cost-review.html"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

FINANCE_ACTIONS = ("runCostReview:", "confirmCostReview:", "writeCostReviewMaterial:",
                   "sendCostReviewToQuote:", "returnCostReviewToProcess:")
FINANCE_BUTTON_IDS = ("crConfirm", "crWriteDb", "crToQuote", "crReturn")


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


def action_block(text: str, marker: str) -> str:
    """取注册表里一个动作自己的那一块（4 空格缩进的键 → 它自己的 "    },"）。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    end = text.find("\n    },", idx)
    return text[idx:end] if end > 0 else ""


class ProcessSideOnlySeesSendToFinance(unittest.TestCase):
    """R1 / R2：非财务身份只留「发送给财务」主按钮。"""

    @classmethod
    def setUpClass(cls):
        cls.cr = read(COST_JS)

    def test_send_to_finance_action_is_the_process_side_primary(self):
        block = action_block(self.cr, "sendCostReviewToFinance: {")
        self.assertTrue(block, "尚未注册 sendCostReviewToFinance 动作")
        self.assertIn("label: '发送给财务'", block, "主按钮文案必须是「发送给财务」")
        self.assertIn("role: 'primary'", block, "发送给财务必须是主按钮")
        self.assertIn("deferred: true", block, "对外发送是长动作，必须 deferred 先回执")
        self.assertIn("visible: crReadOnly()", block,
                      "它只在非财务身份（工艺经理）下出现")

    def test_finance_actions_hide_for_process_side(self):
        op_factory = block_from(self.cr, "const crOpAction = (")
        self.assertTrue(op_factory, "找不到 crOpAction 工厂")
        self.assertIn("visible: !crReadOnly()", op_factory,
                      "写入数据库 / 回传报价 / 提交工艺经理确认 都要按身份隐藏")
        for marker in ("runCostReview: {", "confirmCostReview: {"):
            with self.subTest(action=marker):
                block = action_block(self.cr, marker)
                self.assertTrue(block, "动作丢失：" + marker)
                self.assertIn("visible: !crReadOnly()", block,
                              marker + " 必须只在财务身份下占操作栏")

    def test_send_helper_reuses_the_existing_2_2_exit(self):
        block = block_from(self.cr, "async function crSendToFinance(")
        self.assertTrue(block, "尚未实现 crSendToFinance()")
        self.assertIn("/integration/send-to-finance", block,
                      "必须复用 2.2 既有的发送财务出口，不得新建接口")
        self.assertNotIn("/cost-review/", block, "不得绕到成本接口去发送")
        self.assertNotIn("aiOpenFinanceDialog", block,
                         "2.3 不重写 2.2 的派发弹窗（收件人选择留在 2.2）")
        self.assertIn("product_name", block, "复用既有 IntegrationPublishBody 字段")
        self.assertIn("api(", block, "走既有请求封装")

    def test_failure_surfaces_the_real_reason(self):
        block = block_from(self.cr, "async function crSendToFinance(")
        for token in ("crStatus(", "error.message"):
            with self.subTest(token=token):
                self.assertIn(token, block, "失败必须给出后端真实原因：" + token)
        self.assertIn("crToast(", block, "失败要可见")


class PageLevelButtonsFollowIdentity(unittest.TestCase):
    """R3：页内也不再摆那几颗按不动的按钮。"""

    @classmethod
    def setUpClass(cls):
        cls.cr = read(COST_JS)
        cls.html = read(COST_HTML)

    def test_page_has_hidden_send_to_finance_primary(self):
        tag = re.search(r'<button[^>]*id="crSendToFinance"[^>]*>', self.html)
        self.assertIsNotNone(tag, "cost-review.html 缺少 #crSendToFinance 按钮")
        self.assertIn("ai-op-btn primary", tag.group(0), "页内发送给财务要是主按钮样式")
        self.assertIn("hidden", tag.group(0), "默认隐藏，由身份决定是否显示")

    def test_render_ops_toggles_finance_buttons_by_identity(self):
        block = block_from(self.cr, "function crRenderOps(")
        self.assertTrue(block, "找不到 crRenderOps()")
        self.assertIn("crReadOnly()", block, "页内渲染必须按身份分流")
        for name in FINANCE_BUTTON_IDS + ("crSendToFinance",):
            with self.subTest(name=name):
                self.assertIn(name, block, "页内分流遗漏：" + name)

    def test_page_action_row_and_bulk_button_follow_identity(self):
        actions = block_from(self.cr, "function crRenderActions(")
        self.assertTrue(actions, "找不到 crRenderActions()")
        self.assertIn("crReadOnly()", actions, "零件页签的测算按钮要按身份让位")
        bind = block_from(self.cr, "function crBind()")
        self.assertIn("crSendToFinance", bind, "页内按钮必须绑定到同一发送实现")
        self.assertRegex(self.cr, r"crRunAll[\s\S]{0,200}hidden",
                         "一键测算全部成本要在非财务身份下隐藏")

    def test_identity_ready_republishes_action_snapshot(self):
        self.assertIn("cpq-sso-ready", self.cr,
                      "CpqSso 核对完身份后要翻一次主按钮")
        block = self.cr[self.cr.find("cpq-sso-ready"):]
        self.assertIn("crPublishState()", block[:600],
                      "身份就绪后必须重发动作快照，不能等用户再点一次")


class CapabilitiesNotReduced(unittest.TestCase):
    """R4：动作注册、后端路由与权限闸门都不缩水。"""

    @classmethod
    def setUpClass(cls):
        cls.cr = read(COST_JS)
        cls.main = read(MAIN_PY)

    def test_finance_actions_and_helpers_stay_registered(self):
        for name in FINANCE_ACTIONS:
            with self.subTest(action=name):
                self.assertIn(name, self.cr, "财务动作被删：" + name)
        for helper in ("function crRunAllInBackground(", "function crConfirmCost(",
                       "async function crRunOp(", "function crSendToFinance("):
            with self.subTest(helper=helper):
                self.assertIn(helper, self.cr, "既有实现被删：" + helper)

    def test_backend_routes_and_role_gates_unchanged(self):
        for route in ('"/api/projects/{project_id}/cost-review"',
                      '"/api/projects/{project_id}/cost-review/confirm"',
                      '"/api/projects/{project_id}/integration/send-to-finance"'):
            with self.subTest(route=route):
                self.assertIn(route, self.main, "后端路由被删：" + route)
        self.assertRegex(self.main, r'integration_send_to_finance[\s\S]{0,600}MANAGER_ROLES',
                         "发送财务仍必须是工艺技术经理 / 管理员权限")


if __name__ == "__main__":
    unittest.main()
