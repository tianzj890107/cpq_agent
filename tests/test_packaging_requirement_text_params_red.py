"""红测：包装需求原文里明明写着「包装品类酒盒」，抽取器却没认这两个键。

Spec：`docs/specs/packaging-requirement-text-derived-params.md`

现状缺口（34 线上实测，项目 `6beeeebb19a1`，2026-10-12）：

  · 需求 `description` 原文写着「700ML双开门酒盒；包装品类酒盒；盒型编码…」；
  · 但 1.1 的报文字段抽取只认盒型 / 盒族 / 闭合 / 尺寸 / 克重 / 内托 / V 槽 / 数量，
    `packaging_product_name`、`packaging_category` 两个必填键从来没被写进需求单；
  · 于是 4.x 回传销售被拒：「报价必填的成品参数还缺：包装产品名称、包装品类」；
  · 一次已经算齐、账已对平的包装成本卡在回传这一步，报价侧收不到技术结果。

本批只补这一条：**原文抄录 + 通用品类词表**，不针对任何一张图纸、任何客户。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import cost_flow, integration, product_params  # noqa: E402
from tech_app.backend.services.requirement_service import (  # noqa: E402
    extract_explicit_packaging_quote_fields, merge_explicit_packaging_quote_fields)
from tests.test_packaging_cost_engine_red import CostCase, PID  # noqa: E402


class A原文抽取(unittest.TestCase):
    """C1–C3：标签逐字抄录、通用词表认品类、含品类词的片段当产品名称。"""

    def test_a1_labeled_category_without_colon(self):
        result = extract_explicit_packaging_quote_fields("包装品类酒盒")
        self.assertEqual("酒盒", result.get("packaging_category"))

    def test_a2_labeled_product_name_and_category_with_colon(self):
        result = extract_explicit_packaging_quote_fields(
            "包装产品名称：700ML双开门酒盒；包装品类：酒盒")
        self.assertEqual("700ML双开门酒盒", result.get("packaging_product_name"))
        self.assertEqual("酒盒", result.get("packaging_category"))

    def test_a3_vocabulary_category_and_clause_product_name(self):
        result = extract_explicit_packaging_quote_fields("700ML双开门酒盒；面纸225g")
        self.assertEqual("酒盒", result.get("packaging_category"),
                         "原文没有「包装品类」标签时也要按通用词表认得出品类")
        self.assertEqual("700ML双开门酒盒", result.get("packaging_product_name"),
                         "产品名称取同时命中品类词的那一片段")

    def test_a4_unrelated_text_yields_nothing(self):
        result = extract_explicit_packaging_quote_fields("面纸225g；双开门/对开")
        self.assertNotIn("packaging_category", result)
        self.assertNotIn("packaging_product_name", result)

    def test_a5_ambiguous_category_is_left_missing(self):
        result = extract_explicit_packaging_quote_fields("同时要礼盒；也要酒盒")
        self.assertNotIn("packaging_category", result,
                         "两种品类同时出现、长度相当时不许猜一个出来")

    def test_a6_labeled_value_stops_at_punctuation(self):
        result = extract_explicit_packaging_quote_fields("包装品类：酒盒；面纸225g")
        self.assertEqual("酒盒", result.get("packaging_category"),
                         "标签值只取到标点为止，不许把后面整句吞进来")


class B只填空位(unittest.TestCase):
    """C4：已有值的来源不是 user_text 时一个字不许改。"""

    def test_b1_existing_manual_value_is_never_overwritten(self):
        merged = merge_explicit_packaging_quote_fields(
            {"packaging_category": "礼盒", "field_sources": {"packaging_category": "manual"}},
            "包装品类酒盒")
        self.assertEqual("礼盒", merged.get("packaging_category"))

    def test_b2_empty_slot_is_filled_with_provenance(self):
        merged = merge_explicit_packaging_quote_fields({}, "包装品类酒盒")
        self.assertEqual("酒盒", merged.get("packaging_category"))
        self.assertEqual("user_text",
                         (merged.get("field_sources") or {}).get("packaging_category"))
        evidence = (merged.get("field_provenance") or {}).get("packaging_category") or {}
        self.assertEqual(["quote_requirement_text"], evidence.get("evidence_refs"))


class C回传投影(CostCase):
    """C5/C6：需求单没有这两个键、但原文认得出时，投影补齐；认不出仍如实缺口。"""

    def test_c1_missing_required_is_backfilled_from_quote_text(self):
        self.save_requirement(description="700ML双开门酒盒；包装品类酒盒；面纸225g",
                              packaging_product_name=None, packaging_category=None)
        rows = cost_flow.packaging_param_projection(PID) or []
        codes = {row.param_code: row.value for row in rows}
        self.assertEqual("酒盒", codes.get("packaging_category"),
                         "原文认得出的必填，投影里必须补上，历史项目不用重建需求单")
        self.assertEqual("700ML双开门酒盒", codes.get("packaging_product_name"))

    def test_c2_requirement_value_wins_over_text(self):
        self.save_requirement(packaging_category="礼盒",
                              description="700ML双开门酒盒；包装品类酒盒")
        rows = cost_flow.packaging_param_projection(PID) or []
        codes = {row.param_code: row.value for row in rows}
        self.assertEqual("礼盒", codes.get("packaging_category"),
                         "需求单里已有值时，原文抽取不许盖掉它")

    def test_c3_unresolvable_still_reported_not_invented(self):
        self.save_requirement(packaging_product_name=None, packaging_category=None)
        cost_flow.ensure_packaging_params(PID)
        params = integration.load_plan(PID).params
        self.assertIsNotNone(params, "包装项目必须有一份参数推荐结果（含需求单投影）")
        projected = {row.param_code: row.value for row in (params.params or [])}
        codes = ("packaging_product_name", "packaging_category")
        for code in codes:
            self.assertNotIn(code, projected, "原文认不出 %s，不许编一个值出来" % code)
        missing = [row["name"] for row in product_params.missing_required(params)]
        self.assertIn("包装产品名称", missing, "认不出的必填必须如实留在缺口里")
        self.assertIn("包装品类", missing)


if __name__ == "__main__":
    unittest.main()
