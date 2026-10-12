# -*- coding: utf-8 -*-
"""红测：报价卡片第 6 步不许再出现零件/几何/业务部件表（销售视角与三行业对齐）。

Spec：`docs/specs/packaging-quote-card-sales-view-parity.md`（C2）

血缘与取代：本文件原契约是 `packaging-card-step6-parts-must-be-the-business-parts-list.md`
（「卡片第 6 步的零件必须是业务部件清单」）—— 该契约已由上面那份新 Spec 的 C2 **整份取代**：
卡片第 6 步不再有零件表，因此「业务部件 vs 几何分量」的取舍不再发生在卡片上。
业务部件与几何证据仍在**技术工艺侧**（`tech_app/frontend/app.js` 的 2.1 看板、
`.../requirement/packaging-business-parts` 与 `.../requirement/packaging-parts` 两个端点），
本文件只断言它们**不再**出现在报价卡片页。

现状缺口（本机读码，2026-10-12，不是推断）：
  · 卡片页 `packaging-business-parts` 命中 2 次、`ensureCardPackagingParts()` 在 `showStep(6)`
    里被调用（`确认需求解析结果.html:1749`）、几何端点 1 次 —— 卡片刻意反查技术项目读零件；
  · 业务行字段 `business_part_code` / `geometry_binding`、几何兜底标题、`geometry_component_total`
    与后端缺口文案都在卡片页里。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CARD_HTML = ROOT / "确认需求解析结果.html"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
HANDOFF_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_handoff.py"

BUSINESS_READ_TOKEN = "packaging-business-parts"
GEOMETRY_READ_TOKEN = "/requirement/packaging-parts"
GEOMETRY_FALLBACK_TITLE = "图纸拆出来的零件"

#: 技术侧 2.1 仍必须读得到的东西（本批不动，护栏用）。
TECH_BOARD_TOKENS = (BUSINESS_READ_TOKEN, GEOMETRY_READ_TOKEN, "business_part_code",
                     "geometry_binding")

#: 卡片页必须一并消失的零件面标识符。
CARD_FORBIDDEN_TOKENS = ("ensureCardPackagingParts", "resolveCardTechProject",
                         "cardBusinessPartsAccountingText", "geometry_component_total",
                         "PACKAGING_PARTS_SECTION", "PACKAGING_PART_COLUMNS",
                         "part_columns", BUSINESS_READ_TOKEN, GEOMETRY_READ_TOKEN,
                         "business_part_code", "geometry_binding")


def html_text() -> str:
    return CARD_HTML.read_text(encoding="utf-8", errors="replace")


def fn_body(name: str) -> str:
    """取 `[async] function <name>() { ... }` 的函数体（卡片页缩进是 4 空格）。"""
    pattern = (r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{(?P<body>.*?)\n    \}")
    match = re.search(pattern, html_text(), re.S)
    assert match, "卡片页必须仍有 %s()（Spec §4.1）" % name
    return match.group("body")


class CardStep6NoPartsTableRed(unittest.TestCase):
    # ---------------- A 组：卡片不再去技术项目读零件（今天红） ----------------

    def test_a1_card_page_does_not_read_the_business_parts_endpoint(self):
        count = html_text().count(BUSINESS_READ_TOKEN)
        self.assertEqual(0, count,
                         "卡片页不许再读 `.../requirement/packaging-business-parts`；"
                         "现在命中 %d 次（技术侧 app.js 才是它的读方）" % count)

    def test_a2_card_page_has_no_lookup_and_no_part_table_renderer(self):
        text = html_text()
        left = [name for name in ("ensureCardPackagingParts", "resolveCardTechProject",
                                  "cardBusinessPartsAccountingText") if name in text]
        self.assertEqual([], left, "卡片页不许再出现：%s" % "、".join(left))

    def test_a3_business_row_fields_are_not_rendered_on_the_card(self):
        text = html_text()
        left = [token for token in ("business_part_code", "geometry_binding") if token in text]
        self.assertEqual([], left, "卡片页不许再渲染业务部件字段：%s" % "、".join(left))

    # ---------------- B 组：几何兜底与缺口文案也不再上卡片（今天红） ----------------

    def test_b1_missing_list_wording_is_not_on_the_card(self):
        text = html_text()
        left = [token for token in ("尚未形成业务部件清单", "business_parts_missing")
                if token in text]
        self.assertEqual([], left, "卡片页不许再出现业务部件缺口文案：%s" % "、".join(left))

    def test_b2_geometry_fallback_title_is_not_on_the_card(self):
        count = html_text().count(GEOMETRY_FALLBACK_TITLE)
        self.assertEqual(0, count,
                         "「%s」只许留在技术侧；卡片页现在命中 %d 次"
                         % (GEOMETRY_FALLBACK_TITLE, count))

    # ---------------- C 组：两笔账的字段也不再上卡片（今天红） ----------------

    def test_c1_geometry_accounting_field_is_not_on_the_card(self):
        count = html_text().count("geometry_component_total")
        self.assertEqual(0, count,
                         "几何区域计数只在技术侧给（Spec C2）；卡片页现在命中 %d 次" % count)

    # ---------------- D 组：护栏（今天就是绿的，不许被改红） ----------------

    def test_d1_write_side_keeps_both_session_keys_equivalent(self):
        source = HANDOFF_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn('link.get("quote_session_id")', source,
                      "回传通道仍把 quote_session_id 当来源会话（写侧两键同义，Spec C6）")

    def test_d2_generic_unknown_table_section_still_renders(self):
        body = fn_body("wfRestoreStepData")
        self.assertRegex(body, r"kind\s*===?\s*'table'",
                         "`kind:'table'` 的未知段仍必须走通用渲染（Spec C6）")
        self.assertIn("renderTableSection", body, "通用渲染必须还在")

    def test_d3_tech_board_still_reads_both_endpoints(self):
        text = APP_JS.read_text(encoding="utf-8", errors="replace")
        for token in TECH_BOARD_TOKENS:
            self.assertIn(token, text, "技术侧 2.1 零件树不许被本批动掉：缺 %s（Spec C2 末条）" % token)

    def test_d4_backend_read_is_still_business_side(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        match = re.search(r"def _business_parts_body\((?P<body>.*?)(?=\n@app\.|\n@_route\(|\ndef )",
                          source, re.S)
        self.assertTrue(match, "`_business_parts_body()` 必须仍在 main.py 里")
        body = match.group("body")
        self.assertIn("business_parts_missing", body,
                      "后端没有清单时必须给 `business_parts_missing` 缺口（不回退成几何件）")
        self.assertNotRegex(body, r"business_parts\"\]\s*=\s*geometry",
                            "后端不许把几何分量塞进 `business_parts`")
        parts = PARTS_PY.read_text(encoding="utf-8")
        self.assertIn("尚未形成业务部件清单", parts,
                      "缺口文案的唯一来源仍是 `packaging_parts.BUSINESS_PARTS_MISSING_MESSAGE`")

    def test_d5_card_page_is_free_of_the_whole_parts_surface(self):
        text = html_text()
        left = [token for token in CARD_FORBIDDEN_TOKENS if token in text]
        self.assertEqual([], left, "卡片页仍残留零件/几何标识符：%s" % "、".join(left))


if __name__ == "__main__":
    unittest.main(verbosity=2)
