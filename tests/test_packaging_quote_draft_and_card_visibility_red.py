"""红测：缺口包的草稿报价 + 报价卡片里看得见包装分区.

Spec：docs/specs/packaging-quote-draft-and-card-visibility.md

现状缺口（34 上真跑，不是推断）：
  · `POST /agents/quote/api/packaging-quote/price`（项目 325effd296a5 的缺口包）返回
    `{"ok": false, "error": "成本仍有缺口，只能出成本与草稿，不得生成正式报价单"}`
    —— 文案说"只能出成本与草稿"，实现却硬拒（`price()` → 409 cost_gaps_unresolved），
    而同一个包里技术侧门禁写着 `gates.quote_draft.status == "open"`；
  · `packaging-quote-panel.js` 没有任何页面引用；`cpq_agent_server._BI_SECTIONS` 没有
    `s2_packaging` / `s2_packaging_cost` → 报价卡片第 2 步看不到盒型/参数/成本/缺口；
  · 面板把定价路径写成根相对 `/api/packaging-quote/price`，34 上实测 405
    （root 的 `/api/*` 全被代理给 tech_app），而报价页其它 Agent 调用都走 `/agents/quote`；
（第 4 条——图纸字段的门禁死结与人工确认通道——归 `packaging-manual-field-confirmation.md`
与 `packaging-parse-to-downstream-seams.md`，本文件不复述、也不另立第二套接口。）

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUOTE_HTML = ROOT / "确认需求解析结果.html"
PANEL_JS = ROOT / "tech_app" / "frontend" / "packaging-quote-panel.js"
SERVER_PY = ROOT / "cpq_agent_server.py"


def quote_module():
    return importlib.import_module("cpq_packaging_quote")


def server_module():
    return importlib.import_module("cpq_agent_server")


def _module_literal(name):
    """从 cpq_agent_server.py 源码里取**模块级字面量**常量（不经 import）。

    `open-claude/open_claude/*` 随包只发 .pyc（源码保护），本机解释器的 magic 与
    .pyc 不一致时 `import cpq_agent_server` 会直接抛 `bad magic number in
    'open_claude'`。那只是运行环境差异，不该把「分区表里没有包装分区」这条需求缺口
    掩盖成含义完全不同的 ERROR，所以退化成读源码字面量 —— 测的还是同一份声明。
    """
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            target = names[0] if names else None
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        if target != name or getattr(node, "value", None) is None:
            continue
        try:
            return ast.literal_eval(node.value)
        except ValueError:                                      # 非常量表达式
            break
    raise AssertionError("cpq_agent_server.py 里取不到模块级常量 %s（Spec §3.2）" % name)


def _from_server(name, default=None):
    """优先真实 import；只在 .pyc/解释器不匹配这种环境差异上退化成读源码。"""
    try:
        module = server_module()
    except ImportError as exc:
        if "bad magic number" not in str(exc):
            raise
        return _module_literal(name)
    return getattr(module, name, default)


def gap_package():
    """34 上真实包的**形状**（数字取自 325effd296a5 的交接包预览，缺口为 24 条那版）。"""
    return {
        "industry": "packaging",
        "handoff_kind": "packaging_cost_to_quote",
        "handoff_version": "pkg-quote-handoff-v1",
        "result_version": "pkgcost-v1:1000.0:10.934768",
        "requirement": {"quote_quantity": 1000, "closure_type": "双开门/对开，磁吸",
                        "face_paper_gsm": 225, "box_type": "YT-RB-02001-A"},
        "box_type": {"confirmed_box_type": "YT-RB-02001-A", "decision": "confirmed"},
        "params": {"inner_length": 220.0, "inner_width": 89.0, "inner_height": 89.0},
        "cost": {"built": True, "has_gaps": True, "total_cost": 10.934767996439058,
                 "currency": "CNY", "cost_profile": "packaging_v1",
                 "tax_rate": 0.13, "engine_version": "packaging_cost_v1",
                 "material_total": 6.1, "process_total": 1.2, "labor_total": 2.1,
                 "tooling_total": 0.9, "packaging_total": 0.3, "freight_total": 0.2,
                 "other_total": 0.0, "subtotal": 10.8, "loss_amount": 0.13,
                 "gaps": [{"code": "material_gsm_missing", "where": "RB02001-P01",
                           "detail": "材料「灰板 2.0mm」解析不到克重，材料行不出金额"}]},
        "gaps": [{"code": "material_gsm_missing", "where": "RB02001-P01",
                  "detail": "材料「灰板 2.0mm」解析不到克重，材料行不出金额"}],
        "bom": {"items": [{"item_key": "RB02001-P01", "item_name": "面纸", "material": "灰板 2.0mm",
                           "quantity": 1, "bom_category": "box_part"}]},
        "route": {"status": "confirmed", "steps": [{"step_no": 10, "step_name": "灰板开料",
                                                    "equipment": "开料机", "standard_seconds": 20.0}]},
        "gates": {"quote_draft": {"status": "open", "blocking": []},
                  "quote_publish": {"status": "blocked",
                                    "blocking": [{"code": "cost_gaps_unresolved",
                                                  "message": "成本仍存在缺口，缺口清零后才能生成正式报价",
                                                  "source": "packaging_cost"}]}},
        "source": {"project_id": "325effd296a5", "requirement_no": "REQ-325EFFD296A5",
                   "scenario_code": "default", "result_version": "pkgcost-v1:1000.0:10.934768",
                   "business_case_id": "bc_7bcabebb1983", "source_session_id": "e2e-fullflow-a856a046",
                   "handoff_id": "h-0001"},
    }


def clean_package():
    pkg = gap_package()
    pkg["cost"] = dict(pkg["cost"], has_gaps=False, gaps=[])
    pkg["gaps"] = []
    return pkg


# --------------------------------------------------------------------------- #
# A. 缺口包必须能出草稿报价；正式报价单仍然拦住
# --------------------------------------------------------------------------- #
class ADraftQuote(unittest.TestCase):
    def test_a1_gap_package_returns_draft_quote(self):
        quote = quote_module().price(gap_package(), gross_margin_rate=0.25, tax_rate=0.13)
        self.assertIsInstance(quote, dict)
        for key in ("cost_total", "margin_price", "subtotal_unit", "net_unit_price",
                    "tax_amount", "taxed_unit_price", "quote_quantity"):
            self.assertIn(key, quote)
        self.assertTrue(quote.get("draft"), "缺口包必须能出草稿报价（Spec §3.1）")
        self.assertTrue(quote.get("publish_blocked"), "草稿必须带 publish_blocked（Spec §3.1）")
        self.assertEqual("cost_gaps_unresolved", quote.get("publish_block_reason"))
        self.assertEqual(1, quote.get("gap_count"))
        self.assertTrue(quote.get("gaps"), "草稿必须如实带出缺口（不许洗成干净值）")

    def test_a2_publish_with_gaps_is_still_refused(self):
        module = quote_module()
        with self.assertRaises(Exception) as ctx:
            module.price(gap_package(), gross_margin_rate=0.25, publish=True)
        self.assertEqual("cost_gaps_unresolved", getattr(ctx.exception, "code", ""))

    def test_a3_publish_without_gaps_is_allowed_and_not_draft(self):
        quote = quote_module().price(clean_package(), gross_margin_rate=0.25, publish=True)
        self.assertIs(quote.get("draft"), False)
        self.assertIs(quote.get("publish_blocked"), False)

    def test_a4_document_has_publish_switch(self):
        params = inspect.signature(quote_module().document).parameters
        self.assertIn("publish", params,
                      "document() 必须区分草稿与正式报价单（Spec §2.1）")
        self.assertIs(True, params["publish"].default is False or params["publish"].default is None
                      or params["publish"].default is False)

    def test_a5_draft_markdown_says_it_is_a_draft(self):
        module = quote_module()
        try:
            quote = module.price(gap_package(), gross_margin_rate=0.25)
        except Exception as exc:                                    # 实现前：price 还没有草稿路
            self.fail("缺口包还不能出草稿报价：%s（Spec §1.1 / §3.1）" % exc)
        doc = module.document(quote)
        self.assertIn("草稿", doc.get("markdown") or "",
                      "草稿报价单必须写明不得对外发布（Spec §3.1）")

    def test_a6_recompute_still_matches(self):
        module = quote_module()
        try:
            quote = module.price(gap_package(), gross_margin_rate=0.25)
        except Exception as exc:
            self.fail("缺口包还不能出草稿报价：%s（Spec §3.1）" % exc)
        again = module.recompute(quote)
        for key in ("cost_total", "margin_price", "net_unit_price", "taxed_unit_price"):
            self.assertEqual(quote.get(key), again.get(key))


# --------------------------------------------------------------------------- #
# B. 报价卡片里必须有包装分区与面板接线
# --------------------------------------------------------------------------- #
class BCardVisibility(unittest.TestCase):
    def test_b1_bi_sections_expose_packaging_sections(self):
        sections = _from_server("_BI_SECTIONS", {}) or {}
        bridge = importlib.import_module("cpq_tech_bridge")
        snapshot = bridge.packaging_snapshot(gap_package())
        for sid in ("s2_packaging", "s2_packaging_cost"):
            self.assertIn(sid, sections,
                          "报价卡片分区表必须登记 %s，否则第 2 步恢复不出包装分区"
                          "（Spec §2.2 / §3.2）" % sid)
            self.assertEqual(snapshot[sid]["title"], sections[sid][1],
                             "%s 的标题必须与后端快照分区逐字一致（Spec §3.2）" % sid)

    def test_b2_quote_page_references_the_packaging_panel(self):
        html = QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn("packaging-quote-panel.js", html,
                      "报价页必须引用包装报价面板（Spec §1.2 / §3.2）")
        self.assertIn("PackagingQuotePanel", html,
                      "报价页必须真的调 PackagingQuotePanel（Spec §3.2）")

    def test_b3_panel_price_endpoint_is_injectable(self):
        source = PANEL_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"base|AGENT_URL|agentUrl", source),
                        "面板必须支持注入定价基址（默认与该页其它 Agent 调用一致），"
                        "不许把根相对路径写死成唯一入口（Spec §1.2 / §3.2）")

    def test_b4_price_path_constant_is_unchanged(self):
        self.assertEqual("/api/packaging-quote/price",
                         _from_server("PACKAGING_QUOTE_PRICE_PATH"))


if __name__ == "__main__":
    unittest.main()
