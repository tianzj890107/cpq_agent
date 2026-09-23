# -*- coding: utf-8 -*-
"""红测：报价卡片第 6 步的「零件」必须是业务部件清单，263 个几何分量不许整表冒充（Spec §2）。

Spec：`docs/specs/packaging-card-step6-parts-must-be-the-business-parts-list.md`

现状缺口（2026-09-23 在 34 只读实测 + 本机读源码，不是推断）：
  · 卡片页 `确认需求解析结果.html` 里 `/requirement/packaging-parts`（几何口径）出现 4 次，
    `packaging-business-parts` **0 次** —— 业务部件清单在卡片上根本读不到；
  · 34 上项目 `8131f6d29d99`：几何文档 `total=263`；业务部件文档 `built=false`
    （`gap.message = 已识别几何区域 263 个，尚未形成业务部件清单`）；
  · 卡片第 6 步照旧渲染成「图纸拆出来的零件（263 件）」8 列（`DWG-P01`…`DWG-P263`），
    与 `packaging-business-parts-and-cad-plan-view.md` §7「不能产生 263 行任务」相反；
  · 后端 `_business_parts_body()` 早就不回退成几何件 —— 缺口只在卡片页这一侧。

本文件只读源码 + 真文档形状（不发 HTTP、不连 PG、不写业务数据）。
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
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"

BUSINESS_READ_TOKEN = "packaging-business-parts"
GEOMETRY_READ_TOKEN = "/requirement/packaging-parts"
GEOMETRY_FALLBACK_TITLE = "图纸拆出来的零件"

#: 34 上真实返回的缺口文案（`packaging_parts.BUSINESS_PARTS_MISSING_MESSAGE`）。
MISSING_MESSAGE = "已识别几何区域 %d 个，尚未形成业务部件清单"

#: `## 315` 落地的业务列（几何口径），卡片页照抄后端 `part_columns`。
GEOMETRY_CARD_LABELS = ["零件号", "名称", "角色", "材料", "厚度(mm)", "展开长(mm)", "展开宽(mm)",
                        "轮廓状态", "可算", "不可算原因"]


def html_text() -> str:
    return CARD_HTML.read_text(encoding="utf-8")


def fn_body(name: str) -> str:
    """取 `[async] function <name>() { ... }` 的函数体（卡片页缩进是 4 空格）。"""
    pattern = (r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{(?P<body>.*?)\n    \}")
    match = re.search(pattern, html_text(), re.S)
    assert match, "卡片页必须仍有 %s()（Spec §2.1/§3 的既有路径）" % name
    return match.group("body")


def business_read_index_in(body: str) -> int:
    return body.find(BUSINESS_READ_TOKEN)


def geometry_read_index_in(body: str) -> int:
    return body.find(GEOMETRY_READ_TOKEN)


class CardStep6BusinessPartsRed(unittest.TestCase):
    # ---------------- A 组：卡片第 6 步优先按业务部件口径出表（今天红） ----------------

    def test_a1_card_page_reads_the_business_parts_endpoint(self):
        """业务清单在卡片上必须读得到 —— 今天整个卡片页 0 处引用（Spec §1/§2.1 第 1 条）。"""
        text = html_text()
        self.assertTrue(BUSINESS_READ_TOKEN in text,
                        "卡片页必须读 `.../requirement/packaging-business-parts`；"
                        "现在 `packaging-business-parts` 出现 %d 次（几何端点 4 次）"
                        % text.count(BUSINESS_READ_TOKEN))

    def test_a2_business_read_happens_before_the_geometry_read(self):
        """同一条兜底链里，业务端点的读必须早于几何端点（Spec §2.1 第 1 条）。"""
        body = fn_body("ensureCardPackagingParts")
        business = business_read_index_in(body)
        geometry = geometry_read_index_in(body)
        self.assertGreaterEqual(business, 0,
                                "ensureCardPackagingParts() 里必须出现业务部件端点的读")
        self.assertGreaterEqual(geometry, 0,
                                "几何端点的读必须还在（§3：几何证据仍要能读）")
        self.assertLess(business, geometry,
                        "业务部件端点必须**先**读，几何端点只能做兜底；"
                        "现在业务端点位置 %d、几何端点位置 %d" % (business, geometry))

    def test_a3_business_rows_carry_business_identity_and_binding(self):
        """业务行要带业务件号与几何绑定状态，不能用 `DWG-Pxx` 那套几何列冒充（Spec §2.1 第 2 条）。"""
        text = html_text()
        for token in ("business_part_code", "geometry_binding"):
            self.assertTrue(token in text,
                            "卡片页渲染业务部件行时必须用到 `%s`（今天 0 处）" % token)

    # ---------------- B 组：没有业务清单时，说清是几何区域（今天红） ----------------

    def test_b1_missing_business_list_is_disclosed_with_the_backend_wording(self):
        """空清单必须把后端那条缺口文案说出来，不许拿几何行顶上（Spec §2.2 第 1 条）。"""
        text = html_text()
        disclosed = ("尚未形成业务部件清单" in text) or ("business_parts_missing" in text)
        self.assertTrue(disclosed,
                        "卡片第 6 步在 `built=false` 时必须说「%s」（或引用 `business_parts_missing`）；"
                        "今天卡片页里既没有这句话、也没有这个码" % (MISSING_MESSAGE % 263))

    def test_b2_geometry_fallback_is_not_titled_as_the_parts_list(self):
        """几何兜底的表标题必须自带「几何」字样，`图纸拆出来的零件` 只给业务表（Spec §2.2 第 2 条）。"""
        body = fn_body("ensureCardPackagingParts")
        titles = re.findall(r"title:\s*'([^']*)'", body) + re.findall(r'title:\s*"([^"]*)"', body)
        self.assertTrue(titles, "兜底渲染必须仍有显式 title（否则测试前提失效）")
        for title in titles:
            self.assertIn("几何", title,
                          "几何兜底那条路的标题必须说明这是几何区域，不许继续叫「零件」；"
                          "现在写的是 %r" % (title,))

    # ---------------- C 组：两笔账分开说（今天红） ----------------

    def test_c1_geometry_and_business_counts_are_kept_apart(self):
        """263（几何区域）与业务部件数必须在卡片上分别给得出（Spec §2.3 第 1 条）。"""
        text = html_text()
        self.assertTrue("geometry_component_total" in text,
                        "卡片页必须读得出几何区域数（`geometry_component_total`），"
                        "并与业务部件数分开说；今天 0 处引用")

    # ---------------- D 组：护栏（今天就是绿的，不许被改红） ----------------

    def test_d1_project_lookup_keeps_both_session_keys(self):
        body = fn_body("resolveCardTechProject")
        for key in ("source_session_id", "quote_session_id"):
            self.assertIn(key, body,
                          "反查两键同义（`## 446`）不许回退：缺 %s" % key)

    def test_d2_historical_snapshot_section_still_renders(self):
        text = html_text()
        self.assertIn("PACKAGING_PARTS_SECTION", text,
                      "老卡片快照里那份段的渲染路径不许删（只追加不迁移）")
        self.assertRegex(text, r"kind\s*===?\s*'table'",
                         "`kind:'table'` 的未知段仍必须渲染（`## 315`）")

    def test_d3_geometry_endpoint_stays_reachable(self):
        self.assertIn(GEOMETRY_READ_TOKEN, html_text(),
                      "几何证据端点仍要能读（右栏 CAD 平面图 / 绑定都依赖它）")

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
        self.assertIn(MISSING_MESSAGE, parts,
                      "缺口文案的唯一来源仍是 `packaging_parts.BUSINESS_PARTS_MISSING_MESSAGE`")

    def test_d5_card_columns_still_come_from_the_backend(self):
        text = html_text()
        self.assertIn("part_columns", text,
                      "卡片列定义仍必须照抄后端 `part_columns`（唯一来源，不许前端另写一套）")
        parts = PARTS_PY.read_text(encoding="utf-8")
        for label in GEOMETRY_CARD_LABELS:
            self.assertIn('"%s"' % label, parts,
                          "几何口径的卡片列定义不许被本批动掉：缺 %s" % label)


if __name__ == "__main__":
    unittest.main(verbosity=2)
