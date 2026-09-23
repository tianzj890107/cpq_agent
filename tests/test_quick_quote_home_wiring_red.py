"""红测：快速报价「能点完」—— 首页接线 + 工作区渲染 + 读路径不许假装正常。

Spec：docs/specs/quick-quote-home-wiring-and-read-diagnostics.md
血缘：`e2e-quick-quote-executable-path.md`（本层把它的 §3/§4/§6 验收从「源码里出现过」
升到「页面上真点得到」）、`quick-quote-6-case-library-readiness.md`（「不许假装空库」
的读路径版本）、`quick-quote-12-case-maintenance.md`（补字段/审核入口）。

现状缺口（实测，不是推断）：
  · `报价首页.html` 对面板只调用 `open` / `fillCaseFields` / `reviewCase`；
    七个工作区命令（建实例 → 匹配 → 选基准 → 改参数 → 重算 → 出价 → 转精准）**0 次调用**；
  · `quick-quote-panel.js` 的 `render()` 只画了解析入口、五步、案例库、readiness ——
    `renderDiffTable` / `renderQuote` 在模块内**零调用点**，字段工作区与报价段在人眼里不存在；
  · 首页没有 `#quickQuoteWorkspace` 容器（`确认需求解析结果.html` 有）；
  · 首页 `onAction` 没接 `save_quote`，「出价（落版本）」点了没反应；
  · `selectQuickQuoteBaseline()` 在 session 为空时照样发请求，URL 拼成
    `/api/quick-quote/sessions//baseline`，服务端正则 `[^/]+` 匹配不上 → 落到 404；
  · `_handle_quick_quote_read()` 把 `find_quote` 的异常吞成 `saved={}` ——
    实测把 `find_quote` 换成抛 `NameError` 的桩，返回体是 `ok=True` + `quote={}`
    + `saved_quote={}`，**没有任何诊断键**：「确实没落过卡」与「报价存储这一路坏了」
    在响应里长得一模一样。

上一版 `tests/test_e2e_quick_quote_executable_red.py` 的 UI 组只断言源码里出现过这些
token（`assertTrue("selectQuickQuoteBaseline" in PANEL)`），函数写在面板里、页面不调用
它一样绿 —— 本层把验收换成**调用点与行为**。

禁止为了让红测转绿而修改本文件。
（唯一的例外记在 Spec `quick-quote-home-wiring-and-read-diagnostics.md` §6.6：`## 470` 只给
D1/D2 两条探针补了「会话必须先存在」这一步夹具，断言与期望值一字未改。）
"""
from __future__ import annotations

import ast
import contextlib
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOME_NAME = "报价首页.html"
PANEL_NAME = "tech_app/frontend/quick-quote-panel.js"
SERVER_NAME = "cpq_agent_server.py"

HOME = (ROOT / HOME_NAME).read_text(encoding="utf-8")
PANEL = (ROOT / PANEL_NAME).read_text(encoding="utf-8")

# 七个工作区命令（Spec `e2e-quick-quote-executable-path.md` §3 的命令闭集）。
COMMANDS = (
    "openQuickQuoteSession",        # 建实例
    "matchQuickQuoteCases",         # 匹配
    "selectQuickQuoteBaseline",     # 选基准
    "saveQuickQuoteWorkspace",      # 改参数（PUT）
    "repriceQuickQuote",            # 重算
    "confirmQuickQuote",            # 出价（落版本）
    "transferQuickQuoteToPrecise",  # 转精准
)


def js_function_body(source: str, signature: str) -> str:
    """截出 `signature` 这个函数体（以下一个 `/**` 或 `function` 声明为界）。"""
    start = source.find(signature)
    if start < 0:
        return ""
    rest = source[start + len(signature):]
    cut = len(rest)
    for marker in ("\n  /**", "\n  function "):
        idx = rest.find(marker)
        if idx >= 0:
            cut = min(cut, idx)
    return source[start:start + len(signature) + cut]


def called_in(source: str, name: str) -> bool:
    """`面板.函数名(` / `panel.函数名(` 这种真实调用点（不是定义、不是字符串）。"""
    return re.search(r"\.\s*%s\s*\(" % re.escape(name), source) is not None


class AHomeWiringRed(unittest.TestCase):
    """C1：首页必须真的接线七个命令。"""

    def test_a1_home_calls_every_workspace_command(self):
        missing = [name for name in COMMANDS if not called_in(HOME, name)]
        self.assertEqual(
            [], missing,
            "%s 里这些工作区命令一个调用点都没有（函数躺在面板里没人调）：%s —— "
            "Spec §C1 要求首页把整条链接上，人才能点到出价" % (HOME_NAME, missing))

    def test_a2_home_handles_the_save_quote_action(self):
        # 用布尔断言而不是 assertIn：后者失败时会把整份 HTML 打进日志。
        self.assertTrue(
            re.search(r"\bsave_quote\b", HOME) is not None,
            "%s 的 onAction 没接 save_quote：「出价（落版本）」点了没反应（Spec §C3）" % HOME_NAME)


class BWorkspaceVisibleRed(unittest.TestCase):
    """C2：字段工作区与报价段必须被画出来，且首页要给它们容器。"""

    def test_b1_diff_table_and_quote_section_are_rendered(self):
        body = js_function_body(PANEL, "function render(target, payload, options)")
        self.assertTrue(body, "面板 render() 找不到（文件名或签名变了？）")
        in_panel = ("renderDiffTable" in body) and ("renderQuote" in body)
        in_home = called_in(HOME, "renderDiffTable") and called_in(HOME, "renderQuote")
        self.assertTrue(
            in_panel or in_home,
            "字段工作区（renderDiffTable）与报价段（renderQuote）都没有渲染调用点："
            "「改差异项」「出价」在页面上根本不存在（Spec §C2）")

    def test_b2_home_has_a_workspace_container(self):
        self.assertTrue(
            "quickQuoteWorkspace" in HOME,
            "%s 没有工作区容器（`确认需求解析结果.html:875` 有 `#quickQuoteWorkspace`），"
            "面板接上也无处渲染（Spec §C2）" % HOME_NAME)


class CSessionPrerequisiteRed(unittest.TestCase):
    """C4：session 未建立时不许发注定 404 的请求。"""

    def test_c1_baseline_refuses_to_fire_without_a_session(self):
        body = js_function_body(PANEL, "function selectQuickQuoteBaseline(caseCode, options)")
        self.assertTrue(body, "面板 selectQuickQuoteBaseline() 找不到（签名变了？）")
        self.assertRegex(
            body, r"if\s*\(\s*!\s*quickQuoteSessionId\s*\(\s*\)\s*\)",
            "selectQuickQuoteBaseline 没有「session 为空就先建实例 / 直接报错」的前置；"
            "现在会拼出 `/api/quick-quote/sessions//baseline`（服务端 `[^/]+` 匹配不上 → 404）"
            "（Spec §C4）")


class DReadPathHonestyRed(unittest.TestCase):
    """C5：读路径不许把「处理器坏了」伪装成「还没落过卡」。"""

    def setUp(self):
        import cpq_agent_server  # noqa: PLC0415 - 按本仓既有测试的写法，用前才 import
        self.server = cpq_agent_server
        self.price = getattr(self.server, "cpq_quick_quote_price", None)
        if self.price is None:
            self.fail("cpq_agent_server 没有模块级绑定 cpq_quick_quote_price"
                      "（`## 287` 已收口过，属回退；Spec §C7）")

    @contextlib.contextmanager
    def _existing_session(self, session_id: str):
        """让读路径看到一个**只活在内存里**的实例（不落盘、不写业务数据）。

        Spec `quick-quote-home-wiring-and-read-diagnostics.md` §6.6（`## 470` 处置）：C5 的后半句
        「正常读到空时不许出现诊断键」说的是**存在的实例里还没落过卡**。`_qq_state()` 早已不许
        `setdefault` 造幽灵实例（`quick-quote-full-flow-state-and-recovery.md` §6：不存在的 session 一律
        404 `session_not_found`，错误体必然带 `error`），所以探针必须先给出「这个实例存在」这件事；
        而会话仓储是**落盘**的（`_JsonDocRepository`），真去建实例会把探针 id 写进数据目录 ——
        于是这里只替换进程内的存在性判定，一个字都不落盘。
        """
        state = {"quote_mode": "quick", "industry": "packaging", "owner_user_id": "",
                 "participants": [], "inputs": {}, "match": {}, "baseline": {},
                 "workspace": {}, "diff": [], "revision": 0, "card": {}, "transfer": {},
                 "quote": {}, "updated_at": "2026-09-23T00:00:00+08:00"}
        with mock.patch.object(self.server, "_qq_existing",
                               lambda sid: dict(state) if str(sid) == session_id else None):
            yield

    def test_d1_broken_store_is_surfaced_not_swallowed(self):
        original = self.price.find_quote

        def boom(*args, **kwargs):
            raise NameError("simulated: name 'cpq_quick_quote_price' is not defined")

        self.price.find_quote = boom
        try:
            with self._existing_session("wiring-probe-xyz"):
                out = self.server._handle_quick_quote_read("wiring-probe-xyz")
        finally:
            self.price.find_quote = original
        self.assertTrue(
            any(("error" in key) or ("diagnostic" in key) for key in out),
            "报价存储这一路坏了（find_quote 抛 NameError），响应却只有 %s —— "
            "与「这个会话确实还没落过卡」长得一模一样，现场没法对账（Spec §C5）"
            % sorted(out.keys()))

    def test_d2_a_genuinely_empty_read_is_not_dressed_up_as_an_error(self):
        original = self.price.find_quote
        self.price.find_quote = lambda *args, **kwargs: {}
        try:
            with self._existing_session("wiring-probe-empty"):
                out = self.server._handle_quick_quote_read("wiring-probe-empty")
        finally:
            self.price.find_quote = original
        self.assertEqual(
            [], [key for key in out if "error" in key or "diagnostic" in key],
            "确实没落过卡是正常空值，不许加诊断键（Spec §C5 的另一半）")


class EGuards(unittest.TestCase):
    """C7 / C6：不许回退的护栏与资格门禁（现在应当是绿的）。"""

    def test_e1_every_quick_quote_module_is_bound_at_module_level(self):
        source = (ROOT / SERVER_NAME).read_text(encoding="utf-8")
        tree = ast.parse(source)
        bound = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound.add(alias.asname or alias.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
        reads = {node.id for node in ast.walk(tree)
                 if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                 and node.id.startswith("cpq_quick_quote_")}
        self.assertEqual(
            [], sorted(reads - bound),
            "%s 里按全局名读了从未绑定的模块（真实请求会 NameError → 500；`## 287` 已收口）"
            % SERVER_NAME)

    def test_e2_quote_action_closed_set_is_unchanged(self):
        match = re.search(r"QUOTE_ACTIONS\s*=\s*\[([^\]]*)\]", PANEL)
        self.assertIsNotNone(match, "面板 QUOTE_ACTIONS 闭集找不到")
        actions = re.findall(r'"([^"]+)"', match.group(1))
        self.assertEqual(["save_quote", "transfer_precise"], actions,
                         "出价段出口只有这两个，不许新增第三个（Spec §C3）")

    def test_e3_quick_entry_stays_packaging_only(self):
        self.assertRegex(
            HOME, r'data-quote-mode="quick"\s*\n?\s*data-industries="packaging"',
            "首页「快速报价」入口必须继续只对包装行业可见（Spec §C7）")

    def test_e4_no_eligibility_wording_is_invented_in_the_frontend(self):
        for token in ("缺必需字段", "已审核"):
            self.assertTrue(token in PANEL,
                            "资格话术走 REASON_LABELS；面板不许自己造第二份词表（Spec §C6 血缘）")

    def test_e5_fill_and_transfer_exits_exist_for_zero_eligible(self):
        module = self._case_module()
        out = module.library_readiness([{"case_code": "QQ-T-9001", "review_status": "draft",
                                         "standard_price": None, "source_type": "workbook"}])
        self.assertEqual("no_eligible", out["verdict"])
        actions = {row.get("action") for row in (out.get("next_actions") or [])}
        self.assertIn("transfer_to_precise", actions, "0 条可用时必须给精准报价出口（Spec §C6）")
        self.assertTrue(actions & {"fill_case_fields", "review_case"},
                        "0 条可用时必须给补数据 / 审核入口（Spec §C6）")

    @staticmethod
    def _case_module():
        import importlib
        return importlib.import_module("cpq_quick_quote_case")


if __name__ == "__main__":
    unittest.main()
