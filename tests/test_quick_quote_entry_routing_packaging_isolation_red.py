"""Red tests for quick-quote-entry-routing-and-packaging-isolation.md.

离线静态/纯函数契约：不连接 PG、不调用模型、不改案例数据。
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "报价首页.html").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")
SERVER = (ROOT / "cpq_agent_server.py").read_text(encoding="utf-8")
PANEL = (ROOT / "tech_app/frontend/quick-quote-panel.js").read_text(encoding="utf-8")


def js_function(source: str, name: str) -> str:
    match = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    if not match:
        return ""
    start = match.start()
    next_fn = re.search(r"\n\s*(?:async\s+)?function\s+[A-Za-z_$]", source[match.end():])
    end = match.end() + next_fn.start() if next_fn else len(source)
    return source[start:end]


def py_function(source: str, name: str) -> str:
    start = source.find("def " + name + "(")
    if start < 0:
        return ""
    match = re.search(r"\n(?:async\s+)?def\s+[A-Za-z_]", source[start + 1:])
    end = start + 1 + match.start() if match else len(source)
    return source[start:end]


class QuoteModeRoutingRed(unittest.TestCase):
    def test_home_has_explicit_active_quote_mode(self):
        self.assertTrue("activeQuoteMode" in HOME,
                        "首页只有打开面板动作，没有可持久化的 precise/quick 当前模式")

    def test_quick_button_sets_mode_before_opening_panel(self):
        init = js_function(HOME, "initQuoteModeEntries")
        self.assertTrue("setActiveQuoteMode" in init,
                        "点击快速报价必须先保存模式，不能只 openQuickQuotePanel()")

    def test_submit_requirement_routes_by_quote_mode(self):
        submit = js_function(HOME, "submitRequirement")
        self.assertTrue("routeQuoteSubmission" in submit or "activeQuoteMode" in submit,
                        "发送/Enter 仍无条件进入精准报价 requestNavigate")
        self.assertTrue("submitQuickQuoteRequirement" in HOME,
                        "缺少文本输入直接驱动快速报价的入口")

    def test_quick_submission_stays_on_homepage(self):
        body = js_function(HOME, "submitQuickQuoteRequirement")
        self.assertTrue(body, "缺少 submitQuickQuoteRequirement()")
        self.assertNotIn("确认需求解析结果.html", body)
        for token in ("openQuickQuoteSession", "matchQuickQuoteCases"):
            self.assertIn(token, body, f"快速提交没有调用 {token}")

    def test_quote_mode_is_persisted_with_session(self):
        create = py_function(SERVER, "_handle_quick_quote_session_create")
        self.assertTrue("quote_mode" in create,
                        "快速 session/card 没有保存 quote_mode，刷新无法确定恢复哪个工作区")

    def test_quick_card_reopens_quick_workspace(self):
        self.assertTrue("openQuickQuoteCard" in HOME,
                        "首页卡片没有按 quote_mode 恢复快速工作区的独立入口")


class StructuredQuickInputRed(unittest.TestCase):
    def test_raw_requirement_text_is_not_the_match_payload(self):
        body = js_function(HOME, "quickQuoteInputs")
        self.assertTrue("structuredQuickQuoteInputs" in body,
                        "当前 quickQuoteInputs 只返回 requirement_text，匹配器拿不到盒型/尺寸")

    def test_panel_has_text_to_match_input_command(self):
        self.assertTrue("extractQuickQuoteInputs" in PANEL,
                        "面板缺少需求原文→QUICK_MATCH_INPUT_KEYS 的统一抽取命令")

    def test_example_fields_are_supported(self):
        for token in ("box_type", "box_family", "closure_type", "inner_length",
                      "inner_width", "inner_height", "face_paper_gsm", "insert_type",
                      "v_groove", "quantity"):
            self.assertTrue(token in PANEL, f"快速输入协议缺少 {token}")


class PackagingIsolationRed(unittest.TestCase):
    def test_two_phase_step1_match_branches_before_product_table_query(self):
        body = py_function(SERVER, "_handle_step1_match")
        phase = body.find('if phase == "match"')
        query = body.find("cpq_match.PRODUCT_TABLE", phase)
        packaging = body.find("cpq_packaging_match", phase)
        self.assertGreaterEqual(packaging, 0,
                                "页面实际调用的 _handle_step1_match 没有包装分流")
        self.assertLess(packaging, query,
                        "包装分流必须发生在构造/执行 product_para_value SQL 之前")

    def test_packaging_match_does_not_call_battery_scorer(self):
        self.assertTrue("match_step1_by_industry" in SERVER,
                        "两套匹配入口尚未收敛为同一个按行业分流服务")

    def test_quick_session_rejects_non_packaging_industry(self):
        create = py_function(SERVER, "_handle_quick_quote_session_create")
        self.assertTrue("quick_quote_industry_guard" in create,
                        "快速 session 只应接受 packaging，不能靠按钮隐藏保证")

    def test_packaging_workbench_refreshes_industry_schema(self):
        self.assertTrue("refreshFixedFormsForIndustry" in WORKBENCH,
                        "包装工作台仍可能沿用默认电池技术参数列")

    def test_packaging_page_has_cross_industry_candidate_guard(self):
        self.assertTrue("rejectCrossIndustryCandidates" in WORKBENCH,
                        "服务端误回电池时前端仍会把它渲染成可选产品")


class ConversationRed(unittest.TestCase):
    def test_initial_requirement_echo_is_deduplicated(self):
        self.assertTrue("dedupeInitialRequirementEcho" in WORKBENCH,
                        "一次首页提交不应在精准工作台重复输出两条相同用户消息")


if __name__ == "__main__":
    unittest.main()

