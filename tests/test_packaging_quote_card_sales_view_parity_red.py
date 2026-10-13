# -*- coding: utf-8 -*-
"""红测：报价卡片回到销售视角（技术整包/零件表撤出，定价块按步骤，第 4 步不再复制第 3 步）。

Spec：`docs/specs/packaging-quote-card-sales-view-parity.md`

现状缺口（本机读码 + 34 渲染文本，2026-10-12，不是推断）：
  · `确认需求解析结果.html:4332-4358` 把技术整包（`packaging_package`）整棵 JSON 树画进
    `#packagingPackageContent` —— `renderPackage` 是通用递归，没标签的键打英文原键、无名数组项
    标题退化成「明细 N」（`packaging-quote-panel.js:151-192`）；
  · 第 6 步触发点 `:1749` → `ensureCardPackagingParts()`（`:4623`）反查技术项目读零件端点，
    把零件/几何表画进卡片（31+ 行 `DWG-BPxx`）；
  · 这两块都没有步骤归属（host 直接插进 `#resultsContent`，没进 `sections` Map），
    `showStep()` 只隐藏 sections 里的分区（`:1756`）→ 每一步都在；
  · 第 3 步与第 4 步逐字一样：`cpq_packaging_quote.py:672-695` 里 `s3_markup` 与 `s4_markup`
    共用同一个 `markup_fields`/`markup_rows`，只有 `title`/`step_no` 不同；而卡片只传
    `pricing_mode` + `gross_margin_rate`（`:4364-4365`），加价/折扣恒为 0。

纪律：`node -e` 抽具名函数体真跑（纯函数 + 最小 DOM 桩）+ 源码/后端守卫；不发 HTTP、不连 PG / 34、
不写业务数据。禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CARD_HTML = ROOT / "确认需求解析结果.html"
PANEL_JS = ROOT / "tech_app" / "frontend" / "packaging-quote-panel.js"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
SUITE_PY = ROOT / "cpq_agent_server.py"
GATE_PY = ROOT / "cpq_quote_gate.py"

DUMP_NODE = "packagingPackageContent"

#: 零件/几何表那一段的全部标识符（Spec C2，逐个必须 0 次）。
PART_BLOCK_TOKENS = (
    "ensureCardPackagingParts", "resolveCardTechProject", "PACKAGING_PART_COLUMNS",
    "PACKAGING_PARTS_SECTION", "packagingPartCardRows", "packagingBusinessPartCardRows",
    "cardBusinessPartsAccountingText", "PACKAGING_BUSINESS_PARTS_COLUMNS",
    "PACKAGING_BUSINESS_PARTS_TITLE", "PACKAGING_BINDING_COPY",
    "PACKAGING_BUSINESS_MISSING_CODE", "packaging-business-parts", "/api/projects",
    "part_columns",
)

#: `sections()` 的输入（金额口径与 34 上实测的圆盘盒草稿同形）。
QUOTE = {
    "pricing_mode": "gross_margin", "gross_margin_rate": 0.25,
    "cost_total": 4.14, "margin_price": 5.52, "addon_total": 0.0, "subtotal_unit": 5.52,
    "discount_amount": 0.0, "net_unit_price": 5.52, "tax_amount": 0.72,
    "taxed_unit_price": 6.24, "untaxed_unit_price": 5.52,
    "untaxed_total": 5521.04, "taxed_total": 6238.78,
    "tax_rate": 0.13, "quote_quantity": 1000, "addons": [], "discount_rate": 0.0,
}
QUOTE_WITH_ADDONS = dict(QUOTE, addons=[{"code": "other_addon", "label": "其他加价",
                                        "amount": 1.0}],
                         addon_total=1.0, subtotal_unit=6.52, discount_amount=0.326,
                         discount_rate=0.05, net_unit_price=6.194)


def html_text() -> str:
    return CARD_HTML.read_text(encoding="utf-8", errors="replace")


def fn_src(name: str) -> str:
    """抽 `[async] function <name>() { ... }` 的完整文本（缩进 4 空格；取到同缩进的收尾 `}`）。"""
    html = html_text()
    match = re.search(r"\n    (?:async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", html)
    assert match, "卡片页必须仍有 %s()（Spec §4.1）" % name
    end = html.find("\n    }", match.end())
    assert end > 0, "取不到 %s() 的收尾\n    }" % name
    return html[match.start() + 5:end + 6]


def node_run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", script], capture_output=True, text=True)


def quote_module():
    return importlib.import_module("cpq_packaging_quote")


# --------------------------------------------------------------------------- #
# A 组：技术整包不再上销售卡片（今天红）
# --------------------------------------------------------------------------- #
class ANoPackageDump(unittest.TestCase):
    def test_a1_card_page_has_no_dump_node_and_never_renders_the_package(self):
        text = html_text()
        self.assertEqual(0, text.count(DUMP_NODE),
                         "卡片页不许再有 `#%s`（技术整包只许留在快照里，不上销售卡片）" % DUMP_NODE)
        self.assertEqual(0, text.count("renderPackage"),
                         "卡片页不许再调 `PackagingQuotePanel.renderPackage(...)`")

    def test_a2_executable_cached_path_never_calls_render_package(self):
        script = (
            "let dumps=0;\n"
            "const currentSessionId='same'; let LAST_PACKAGING_PACKAGE=null;\n"
            "let LAST_PACKAGING_QUOTE={gross_margin_rate:0}; let PACKAGING_RESTORED_RATE=0;\n"
            "const PACKAGING_PANEL_KEY='BOX|v1|12|0|{}';\n"
            "const window={PackagingQuotePanel:{renderPackage:()=>{dumps++;},"
            "renderCard:async()=>({quote:{}})}};\n"
            "const document={querySelectorAll:()=>[],getElementById:()=>null,createElement:()=>({style:{},"
            "setAttribute:()=>{},appendChild:()=>{}})};\n"
            "function applyPackagingQuoteRows(){}\n"
            "function restorePackagingPricing(){} function packagingMarginRate(){return 0;}\n"
            + fn_src("packagingAddonValues") + "\n"
            + fn_src("renderPackagingQuotePanel") + "\n"
            "(async()=>{await renderPackagingQuotePanel({packaging_package:{box_type:"
            "{confirmed_box_type:'BOX'},source:{result_version:'v1'},cost:{total_cost:12}}});\n"
            'if(dumps!==0)throw new Error("技术整包不得再上屏 dumps="+dumps);})()\n'
            ".catch(e=>{console.error(e.message||e);process.exitCode=1;});\n"
        )
        run = node_run(script)
        self.assertEqual(0, run.returncode, run.stderr)

    def test_a3_snapshot_still_holds_the_package(self):
        self.assertIn("LAST_PACKAGING_PACKAGE = pkg", html_text(),
                      "护栏：`packaging_package` 仍必须存进页面状态（门禁/定价/历史重建读它）")

    def test_a4_engine_api_is_kept(self):
        self.assertIn("renderPackage", PANEL_JS.read_text(encoding="utf-8"),
                      "护栏：`PackagingQuotePanel.renderPackage` 作为引擎 API 不许顺手删掉")


# --------------------------------------------------------------------------- #
# B 组：零件/几何表下线（今天红）
# --------------------------------------------------------------------------- #
class BNoPartsTable(unittest.TestCase):
    def test_b1_parts_block_identifiers_are_gone(self):
        text = html_text()
        left = ["%s×%d" % (token, text.count(token)) for token in PART_BLOCK_TOKENS
                if token in text]
        self.assertEqual([], left, "卡片页不许再出现这些零件表标识符：%s" % "、".join(left))

    def test_b2_executable_skip_rule_for_the_parts_section(self):
        script = (
            fn_src("cardSnapshotSectionSkipped") + "\n"
            "const cases=[['packaging_parts','packaging',true],"
            "['s6_quote','packaging',false],['s1_basic','packaging',false],"
            "['packaging_parts','battery',false],['packaging_parts','',false]];\n"
            "for(const [sid,ind,want] of cases){const got=cardSnapshotSectionSkipped(sid,ind);"
            "if(got!==want)throw new Error(sid+'/'+ind+' 期望 '+want+' 实得 '+got);}\n"
        )
        run = node_run(script)
        self.assertEqual(0, run.returncode,
                         run.stderr + "\n（判据必须叫 cardSnapshotSectionSkipped(sectionId, industry)）")

    def test_b3_generic_unknown_table_still_renders(self):
        body = fn_src("wfRestoreStepData")
        self.assertRegex(body, r"kind\s*===?\s*'table'",
                         "护栏：`kind:'table'` 的未知段仍必须走通用渲染（Spec C6）")
        self.assertIn("renderTableSection", body, "护栏：通用渲染必须还在")

    def test_b4_tech_board_parts_tree_is_untouched(self):
        text = APP_JS.read_text(encoding="utf-8", errors="replace")
        for token in ("packaging-parts", "packaging-business-parts", "business_part_code"):
            self.assertIn(token, text, "护栏：技术侧 2.1 零件树不许被本批动掉（缺 %s）" % token)


# --------------------------------------------------------------------------- #
# C 组：定价块并入步骤体系（今天红）
# --------------------------------------------------------------------------- #
class CPanelStepScoped(unittest.TestCase):
    def test_c1_executable_visibility_rule(self):
        script = (
            fn_src("packagingQuotePanelStepVisible") + "\n"
            "for(const [n,want] of [[1,false],[2,false],[3,true],[4,true],[5,true],[6,false]]){"
            "const got=packagingQuotePanelStepVisible(n);"
            "if(got!==want)throw new Error('第 '+n+' 步期望 '+want+' 实得 '+got);}\n"
        )
        run = node_run(script)
        self.assertEqual(0, run.returncode,
                         run.stderr + "\n（判据必须叫 packagingQuotePanelStepVisible(step)）")

    def test_c2_show_step_uses_the_rule(self):
        self.assertIn("packagingQuotePanelStepVisible", fn_src("showStep"),
                      "showStep() 必须按该判据切换 `#packagingQuotePanel` 的可见性")

    def test_c3_pricing_inputs_are_kept(self):
        text = html_text()
        for token in ("packagingMarginInput", "packagingRepriceBtn", "renderCard"):
            self.assertIn(token, text, "护栏：包装定价入口不许被撤掉（缺 %s）" % token)


# --------------------------------------------------------------------------- #
# D 组：第 4 步不再复制第 3 步（今天红）
# --------------------------------------------------------------------------- #
class DStep4IsNotACopyOfStep3(unittest.TestCase):
    def test_d1_s4_is_not_a_verbatim_copy_of_s3(self):
        sections = quote_module().sections(QUOTE)
        self.assertIn("s3_markup", sections)
        self.assertIn("s4_markup", sections)
        for block in ("fields", "rows"):
            s3 = json.dumps(sections["s3_markup"].get(block), ensure_ascii=False, sort_keys=True)
            s4 = json.dumps(sections["s4_markup"].get(block), ensure_ascii=False, sort_keys=True)
            self.assertNotEqual(s3, s4,
                                "第 4 步的 `%s` 不许再与第 3 步逐字相同"
                                "（现在两段共用同一份 markup_fields/markup_rows）" % block)

    def test_d2_s4_speaks_about_addons_and_discounts(self):
        sections = quote_module().sections(QUOTE)
        blob = json.dumps(sections["s4_markup"], ensure_ascii=False)
        self.assertIn("沿用第 3 步", blob,
                      "第 4 步必须写明定价结果沿用第 3 步（来源 §1.2）")
        for label in ("加价合计", "加价后单价", "折扣率", "折后未税单价"):
            self.assertIn(label, blob, "第 4 步的字段缺少「%s」" % label)
        self.assertIn("本版无其他加价", blob, "没有加价时必须如实写「本版无其他加价」")
        self.assertIn("本版无折扣", blob, "没有折扣时必须如实写「本版无折扣」")

    def test_d3_s4_lists_real_addons_and_discount(self):
        sections = quote_module().sections(QUOTE_WITH_ADDONS)
        blob = json.dumps(sections["s4_markup"], ensure_ascii=False)
        self.assertIn("其他加价", blob, "有加价项时必须逐条列出（label 逐字来自引擎）")
        self.assertIn("1.00", blob, "加价项的金额必须写出来")
        self.assertIn("0.33", blob, "折扣金额必须写出来（两位小数，来自 money_view）")
        self.assertNotIn("本版无其他加价", blob, "有加价时不许再说「本版无其他加价」")

    def test_d4_s3_keeps_its_own_meaning(self):
        sections = quote_module().sections(QUOTE)
        blob = json.dumps(sections["s3_markup"], ensure_ascii=False)
        self.assertIn("0.25", blob, "护栏：第 3 步仍显示毛利率")
        self.assertEqual("定价-利润加成", sections["s3_markup"].get("title"))
        self.assertEqual(3, sections["s3_markup"].get("step_no"))
        self.assertEqual("报价-加价与折扣", sections["s4_markup"].get("title"))
        self.assertEqual(4, sections["s4_markup"].get("step_no"))
        for key in ("s5_basic", "s5_detail"):
            self.assertIn(key, sections, "护栏：四段分区一个都不许少")


# --------------------------------------------------------------------------- #
# E 组：后端与快照零改动（今天就是绿的，不许被改红）
# --------------------------------------------------------------------------- #
class EBackEndUntouched(unittest.TestCase):
    def test_e1_step_gate_still_reads_the_package(self):
        self.assertIn("packaging_package", GATE_PY.read_text(encoding="utf-8"),
                      "第 6 步门禁仍读 `packaging_package`（Spec C1/C6）")

    def test_e2_bi_sections_still_expose_packaging_sections(self):
        text = SUITE_PY.read_text(encoding="utf-8", errors="replace")
        for sid in ("s2_packaging", "s2_packaging_cost"):
            self.assertIn(sid, text, "固定表单目录仍必须有 %s（Spec C4）" % sid)

    def test_e3_card_still_wires_the_pricing_panel(self):
        text = html_text()
        self.assertIn("packaging-quote-panel.js", text, "卡片仍必须引用包装定价面板")
        self.assertIn("PackagingQuotePanel", text, "卡片仍必须真的调 PackagingQuotePanel")


if __name__ == "__main__":
    unittest.main(verbosity=2)
