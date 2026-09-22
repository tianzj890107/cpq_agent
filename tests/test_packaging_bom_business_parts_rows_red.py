"""红测：业务部件清单里的 28 件必须真的进 BOM 的**部件组**（不只是披露版本号）。

Spec：`docs/specs/packaging-bom-business-parts-rows.md`
依赖：`packaging-business-parts-and-cad-plan-view.md` §7（BOM 只遍历 business_parts）、
`packaging-parametric-bom.md`（七类与读接口）、`packaging-dwg-parts-extraction.md` C7。

现状缺口（代码级 + 真样本，见 Spec §1）：
  · `packaging_bom.py` 的 `_assemble()` 第 2 组部件行只来自盒型模板展开
    （`source="kb_packaging_part_template"`），全文件里 `business_part_code` 出现 **0** 次；
  · 真样本 28 件业务部件（`JWXR21-P01…P28`）每件都有权威尺寸与材料原文，
    其中 2 件是外购件（「外购，用量1个」/「外购，用量8/套」），一件都进不了 BOM；
  · 业务部件版本今天只以披露形式跟进（`source_versions.business_parts_id/hash`），行仍按模板走。

夹具**复用**冻结模块 `tests.test_packaging_parametric_bom_red.BomCase`（BOM 真链路：
种子 KB + 临时 SQLite + meta 沙盘），不复制它的常量与假库。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import ast
import importlib
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_packaging_parametric_bom_red import (  # noqa: E402
    BOX_MAIN, PID, REQ_NO, BomCase, load_bom)

PARTS_PKG = "tech_app.backend.services.packaging_parts"

#: C1 定的新取值（与既有 `kb_packaging_part_template` / `dwg_parts` 并列）。
BUSINESS_SOURCE = "packaging_business_parts_authority"
TEMPLATE_SOURCE = "kb_packaging_part_template"
#: 部件组的两个类别闭集。
PART_CATEGORIES = ("box_part", "optional_part")
#: `load_bom()` 里那一块披露必须有的键。
BUSINESS_ROWS_KEYS = ("row_total", "box_part_total", "optional_part_total",
                      "needs_input_total", "keys")


def parts_module():
    return importlib.import_module(PARTS_PKG)


def _row(code, name, *, length=307.07, width=528.89, material="225G太阳铜版底PET光银",
         process="开料-模切", quantity="", sheet="零部件排版工艺", row=4,
         size_text="307.07x528.89mm"):
    """一件业务部件：形状照真样本（`authority` 是权威原文那一块）。"""
    authority = {"sequence_no": 1, "product_size_text": size_text,
                 "length_mm": length, "width_mm": width, "material_text": material,
                 "layout_text": "787x560mm=1M", "process_text": process, "note": "",
                 "quantity": quantity, "source": {"sheet": sheet, "row": row}}
    return {"business_part_code": code, "name": name, "authority": authority,
            "geometry_binding": {"status": "unbound", "component_ids": []}}


def biz_doc(rows):
    return {"engine_version": "packaging-business-parts/1",
            "business_parts": list(rows), "geometry_evidence": {},
            "unavailable": [], "stats": {}}


def real_sample_rows():
    """真样本前几件的形状（1 纸件 + 1 灰板件 + 2 外购件）。"""
    return [_row("JWXR21-P01", "左盖面纸"),
            _row("JWXR21-P03", "左盖外盒里层灰板1", length=217.1, width=482.9,
                 size_text="217.1x482.9mm", material="1.8MM双灰裱225G太阳铜版底PET光银",
                 process="开料-印刷1专(内裱纸）-裱纸-模切", row=6),
            _row("JWXR21-P27", "顶托EVA", length=125.2, width=54.0,
                 size_text="125.2x54mm", material="38度A级白色EVA 125×54×35MM异形",
                 process="外购，用量1个", row=30),
            _row("JWXR21-P28", "磁铁", length=15.0, width=5.0, size_text="15x5x2MM",
                 material="长方形镀锌双面磁铁侧吸3500GS 15×5×2MM",
                 process="外购，用量8/套", row=31)]


# --------------------------------------------------------------------------- #
# A. 纯函数 `business_part_rows()`
# --------------------------------------------------------------------------- #
class ABusinessPartRows(unittest.TestCase):
    def module(self):
        module = load_bom()
        self.assertIsNotNone(module, "缺少 tech_app/backend/services/packaging_bom.py")
        self.assertTrue(hasattr(module, "business_part_rows"),
                        "packaging_bom 里没有 business_part_rows()（Spec §C1）")
        return module

    def rows(self, *args, **kwargs):
        return self.module().business_part_rows(*args, **kwargs)

    def test_a1_every_authority_part_becomes_one_row(self):
        rows = self.rows(biz_doc(real_sample_rows()))
        self.assertEqual(4, len(rows), "28 件业务部件就该有 28 行（本夹具 4 件）")
        first = rows[0]
        self.assertEqual("JWXR21-P01", first.get("item_key"))
        self.assertEqual("JWXR21-P01", first.get("part_code"),
                         "业务编码必须落到既有 part_code 列上（读回来认得出这件是谁）")
        self.assertEqual("左盖面纸", first.get("item_name"))
        self.assertEqual("225G太阳铜版底PET光银", first.get("material"),
                         "材料必须是权威原文（不许改写、不许猜材料码）")
        self.assertAlmostEqual(307.07, float(first.get("length_mm")), places=6)
        self.assertAlmostEqual(528.89, float(first.get("width_mm")), places=6)
        self.assertEqual("件", first.get("unit"))
        self.assertEqual("computed", first.get("status"))
        self.assertEqual([], list(first.get("missing_variables") or []))
        self.assertEqual(BUSINESS_SOURCE, first.get("source"))
        self.assertEqual("box_part", first.get("bom_category"))

    def test_a2_missing_size_is_needs_input_with_ordered_variables(self):
        rows = self.rows(biz_doc([_row("JWXR21-P09", "缺尺寸件", length=None, width=None)]))
        row = rows[0]
        self.assertEqual("needs_input", row.get("status"))
        self.assertEqual(["length_mm", "width_mm"], list(row.get("missing_variables") or []),
                         "缺什么按 (length_mm, width_mm) 顺序列，顺序不许反")
        only_width = self.rows(biz_doc([_row("JWXR21-P10", "只缺宽", length=100.0,
                                            width=None)]))[0]
        self.assertEqual(["width_mm"], list(only_width.get("missing_variables") or []))
        for bad in (0, -5, "307.07", None):
            row = self.rows(biz_doc([_row("JWXR21-P11", "脏尺寸", length=bad,
                                          width=50.0)]))[0]
            self.assertIsNone(row.get("length_mm"), "长度 %r 不许当有效尺寸" % (bad,))
            self.assertEqual("needs_input", row.get("status"))

    def test_a3_purchased_parts_are_optional_part(self):
        rows = self.rows(biz_doc(real_sample_rows()))
        by_code = {row["item_key"]: row for row in rows}
        for code in ("JWXR21-P27", "JWXR21-P28"):
            row = by_code[code]
            self.assertEqual("optional_part", row.get("bom_category"),
                             "权威原文写「外购」的件是采购件（%s）" % code)
            self.assertEqual(1, int(row.get("is_optional") or 0))
        self.assertEqual("box_part", by_code["JWXR21-P01"].get("bom_category"))
        self.assertEqual(0, int(by_code["JWXR21-P01"].get("is_optional") or 0))

    def test_a4_empty_input_is_empty_list_never_raises(self):
        for payload in (None, {}, {"business_parts": []}, {"business_parts": "x"},
                        {"business_parts": [None, "x", 3]}):
            self.assertEqual([], self.rows(payload), "空/非法入参一律给 []（%r）" % (payload,))

    def test_a5_blank_and_duplicate_codes(self):
        rows = self.rows(biz_doc([_row("", "没有编码"),
                                 _row("JWXR21-P01", "第一次"),
                                 _row("JWXR21-P01", "第二次")]))
        keys = [row["item_key"] for row in rows]
        self.assertEqual(["JWXR21-P01"], keys,
                         "空编码跳过；同编码只留第一条（BOM 行主键要求 item_key 唯一）")
        self.assertEqual("第一次", rows[0].get("item_name"))

    def test_a6_no_role_no_material_code_no_expr(self):
        row = self.rows(biz_doc([_row("JWXR21-P01", "左盖面纸")]))[0]
        self.assertNotIn("role", row, "业务角色留给人工映射，本层不许贴")
        self.assertEqual("", row.get("material_code"),
                         "权威清单没有材料码：一律空串，不许现猜")
        for key in ("size_length_expr", "size_width_expr", "size_height_expr"):
            self.assertFalse(row.get(key), "%s 不许凭空出现表达式" % key)

    def test_a7_size_source_points_at_the_workbook_row(self):
        row = self.rows(biz_doc([_row("JWXR21-P01", "左盖面纸", sheet="零部件排版工艺",
                                     row=4)]))[0]
        payload = json.loads(row.get("size_source_json") or "{}")
        self.assertEqual("authority_workbook", payload.get("kind"))
        self.assertEqual("零部件排版工艺", payload.get("sheet"))
        self.assertEqual(4, int(payload.get("row") or 0))

    def test_a7b_no_usable_source_gives_empty_object(self):
        bare = _row("JWXR21-P02", "无出处")
        bare["authority"].pop("source")
        row = self.rows(biz_doc([bare]))[0]
        self.assertEqual({}, json.loads(row.get("size_source_json") or "{}"),
                         "拼不出出处就给 {}，不许编一个")

    def test_a8_quantity_only_when_numeric(self):
        numeric = self.rows(biz_doc([_row("JWXR21-P01", "有数量", quantity="3")]))[0]
        self.assertEqual(3.0, float(numeric.get("quantity")))
        blank = self.rows(biz_doc([_row("JWXR21-P02", "空数量", quantity="")]))[0]
        self.assertIsNone(blank.get("quantity"), "空字符串/非数字一律 None，不许编数量")

    def test_a9_pure_function_does_not_touch_repos(self):
        module = self.module()
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        body = ""
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name == "business_part_rows":
                body = ast.get_source_segment(source, node) or ""
        self.assertTrue(body, "business_part_rows() 必须是模块级函数（Spec §C1）")
        for forbidden in ("kb_repo.", "da_repo.", "store.", "get_backend("):
            self.assertNotIn(forbidden, body,
                             "business_part_rows() 必须是纯函数：体内不许出现 %s" % forbidden)


# --------------------------------------------------------------------------- #
# B. `build_bom()` 接线（真链路）
# --------------------------------------------------------------------------- #
class BBuildBomUsesAuthorityRows(BomCase):
    def save_business(self, rows):
        parts_module().save_business_parts(PID, biz_doc(rows))

    def build(self):
        self.save_requirement()
        self.confirm_box()
        return self.bom().build_bom(PID, REQ_NO)

    def build_with(self, rows):
        self.save_requirement()
        self.confirm_box()
        parts_module().save_business_parts(PID, biz_doc(rows))
        return self.bom().build_bom(PID, REQ_NO)

    @staticmethod
    def part_rows(items):
        return [row for row in items if row.get("bom_category") in PART_CATEGORIES]

    def test_b1_authority_rows_replace_template_part_rows(self):
        result = self.build_with(real_sample_rows())
        rows = self.part_rows(result["items"])
        self.assertEqual(4, len(rows), "有权威清单时，部件组行 = 清单件数")
        self.assertEqual({"JWXR21-P01", "JWXR21-P03", "JWXR21-P27", "JWXR21-P28"},
                         {row["item_key"] for row in rows})
        self.assertTrue(all(row.get("source") == BUSINESS_SOURCE for row in rows),
                        "部件组行必须逐字带 %s" % BUSINESS_SOURCE)
        self.assertEqual([], [row for row in rows if row.get("source") == TEMPLATE_SOURCE],
                         "模板展开的部件行不许与新清单混在一份 BOM 里")

    def test_b2_without_authority_list_behaviour_is_unchanged(self):
        baseline = self.build()
        rows = self.part_rows(baseline["items"])
        self.assertTrue(rows, "没有权威清单时必须照旧走盒型模板展开")
        self.assertTrue(all(row.get("source") == TEMPLATE_SOURCE for row in rows),
                        "没有清单时逐字保持今天的行为（%s）" % TEMPLATE_SOURCE)

    def test_b3_empty_authority_document_falls_back_to_template(self):
        result = self.build_with([])
        rows = self.part_rows(result["items"])
        self.assertTrue(rows, "空清单不许把部件组算成 0 行")
        self.assertTrue(all(row.get("source") == TEMPLATE_SOURCE for row in rows))

    def test_b4_the_other_five_groups_are_untouched(self):
        baseline = self.build()
        with_biz = self.build_with(real_sample_rows())
        for category in ("finished", "material", "process", "tooling", "packaging"):
            before = sorted(row["item_key"] for row in self.by_category(baseline, category))
            after = sorted(row["item_key"] for row in self.by_category(with_biz, category))
            self.assertEqual(before, after,
                             "%s 组的行不许因为本批而变（Spec §C2）" % category)

    def test_b5_rows_are_stamped_with_the_confirmed_box_type(self):
        result = self.build_with(real_sample_rows())
        for row in self.part_rows(result["items"]):
            self.assertEqual(BOX_MAIN, row.get("box_type_code"),
                             "业务行也要盖盒型章（换盒型重算才分得清新旧）")
            self.assertEqual("packaging", row.get("industry"))
            self.assertTrue(row.get("engine_version"))

    def test_b6_part_code_survives_the_storage_round_trip(self):
        self.build_with(real_sample_rows())
        read_back = self.bom().load_bom(PID, REQ_NO)
        rows = self.part_rows(read_back["items"])
        self.assertEqual(4, len(rows))
        for row in rows:
            self.assertTrue(str(row.get("part_code") or "").startswith("JWXR21-P"),
                            "part_code 列必须存下业务编码（读回来仍认得出这件）")

    def test_b7_needs_input_row_survives_and_is_counted(self):
        rows = [_row("JWXR21-P01", "左盖面纸"), _row("JWXR21-P09", "缺尺寸", length=None,
                                                    width=None)]
        result = self.build_with(rows)
        target = [row for row in self.part_rows(result["items"])
                  if row["item_key"] == "JWXR21-P09"]
        self.assertEqual(1, len(target), "缺尺寸的权威件也必须是 BOM 行（needs_input）")


# --------------------------------------------------------------------------- #
# C. 读接口披露 `business_rows`
# --------------------------------------------------------------------------- #
class CReadBackDisclosure(BomCase):
    def build_with(self, rows):
        self.save_requirement()
        self.confirm_box()
        parts_module().save_business_parts(PID, biz_doc(rows))
        return self.bom().build_bom(PID, REQ_NO)

    def test_c1_disclosure_counts_and_sorted_keys(self):
        result = self.build_with(real_sample_rows())
        block = result.get("business_rows")
        self.assertIsInstance(block, dict, "load_bom() 必须给 business_rows（Spec §C3）")
        for key in BUSINESS_ROWS_KEYS:
            self.assertIn(key, block, "business_rows 缺键 %s（键必须存在）" % key)
        self.assertEqual(4, int(block["row_total"]))
        self.assertEqual(2, int(block["box_part_total"]))
        self.assertEqual(2, int(block["optional_part_total"]))
        self.assertEqual(0, int(block["needs_input_total"]))
        self.assertEqual(sorted(block["keys"]), list(block["keys"]),
                         "keys 必须升序（读接口要稳定）")

    def test_c2_without_business_rows_every_counter_is_zero(self):
        self.save_requirement()
        self.confirm_box()
        result = self.bom().build_bom(PID, REQ_NO)
        block = result.get("business_rows") or {}
        self.assertEqual(0, int(block.get("row_total") or 0))
        self.assertEqual(0, int(block.get("box_part_total") or 0))
        self.assertEqual(0, int(block.get("optional_part_total") or 0))
        self.assertEqual(0, int(block.get("needs_input_total") or 0))
        self.assertEqual([], list(block.get("keys") or []))

    def test_c3_needs_input_total_counts_authority_rows(self):
        rows = [_row("JWXR21-P01", "左盖面纸"), _row("JWXR21-P09", "缺尺寸", length=None,
                                                    width=None)]
        result = self.build_with(rows)
        block = result.get("business_rows") or {}
        self.assertEqual(2, int(block.get("row_total") or 0))
        self.assertEqual(1, int(block.get("needs_input_total") or 0))


# --------------------------------------------------------------------------- #
# D. 护栏
# --------------------------------------------------------------------------- #
class DGuards(BomCase):
    def test_d1_categories_closed_sets_unchanged(self):
        module = self.bom()
        self.assertEqual(("finished", "box_part", "material", "process",
                          "packaging", "tooling", "optional_part"),
                         tuple(module.BOM_CATEGORIES))
        self.assertEqual(("box_part", "optional_part"), tuple(module.PART_CATEGORIES))

    def test_d2_template_row_constructor_unchanged(self):
        module = self.bom()
        row = module._part_item({"part_code": "T-1", "name": "模板件", "quantity": 1,
                                 "is_optional": 0})
        self.assertEqual(TEMPLATE_SOURCE, row.get("source"),
                         "_part_item() 仍是模板行构造器（不许被权威行顶替）")

    def test_d3_locked_business_row_survives_a_rebuild(self):
        self.save_requirement()
        self.confirm_box()
        parts_module().save_business_parts(PID, biz_doc(real_sample_rows()))
        module = self.bom()
        module.build_bom(PID, REQ_NO)
        module.lock_bom_item(PID, REQ_NO, "JWXR21-P27", locked=True)
        again = module.build_bom(PID, REQ_NO)
        locked = [row for row in again["items"] if row["item_key"] == "JWXR21-P27"]
        self.assertEqual(1, len(locked), "锁定行不许被重算删掉")
        self.assertEqual(1, int(locked[0].get("locked") or 0), "锁定状态必须保留")
