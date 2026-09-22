"""红测：BOM 的**材料组**也必须按权威清单遍历（`## 377` 只切了部件组）。

Spec：`docs/specs/packaging-bom-business-material-rows.md`
依赖：`packaging-bom-business-parts-rows.md`（切片 1）、
`packaging-business-parts-and-cad-plan-view.md` §7（BOM 只遍历 business_parts）。

现状缺口（代码级，见 Spec §1）：
  · `_assemble()` 第 3 组的材料行来自模板展开的部件材料（`source="kb_material"`），
    与 `## 377` 之后的 28 件权威部件行**自相矛盾**；
  · 真样本材料的合并单元格（"同上一组"）在导入器里只记 `merged_from`、不复制值 ——
    所以材料组必须按**去重原文**收，不能按件数收；
  · `load_bom().gaps.material_unresolved` 今天列的是模板材料，与真实待办无关。

夹具**复用**冻结模块 `tests.test_packaging_parametric_bom_red.BomCase`。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_packaging_parametric_bom_red import (  # noqa: E402
    BOX_MAIN, PID, REQ_NO, BomCase, load_bom)

PARTS_PKG = "tech_app.backend.services.packaging_parts"
BUSINESS_SOURCE = "packaging_business_parts_authority"
TEMPLATE_MATERIAL_SOURCE = "kb_material"
DISCLOSURE_KEYS = ("row_total", "resolved_total", "unresolved_total", "keys")


def parts_module():
    return importlib.import_module(PARTS_PKG)


def biz_row(code, name, material="", *, length=100.0, width=50.0, row=4):
    authority = {"sequence_no": 1, "product_size_text": "100x50mm", "length_mm": length,
                 "width_mm": width, "material_text": material, "layout_text": "",
                 "process_text": "开料-模切", "note": "", "quantity": "",
                 "source": {"sheet": "零部件排版工艺", "row": row}}
    return {"business_part_code": code, "name": name, "authority": authority,
            "geometry_binding": {"status": "unbound"}}


def biz_doc(rows):
    return {"engine_version": "packaging-business-parts/1",
            "business_parts": list(rows), "geometry_evidence": {},
            "unavailable": [], "stats": {}}


#: 真样本形状：前 3 件独立写材料，后 3 件是合并单元格（同上一组 → 没有原文）。
def real_sample_rows():
    return [biz_row("JWXR21-P01", "左盖面纸", "225G太阳铜版底PET光银", row=4),
            biz_row("JWXR21-P03", "左盖外盒里层灰板1", "1.8MM双灰裱225G太阳铜版底PET光银", row=6),
            biz_row("JWXR21-P04", "右盖外盒里层灰板1", "", row=7),          # 合并单元格
            biz_row("JWXR21-P05", "左盖外盒外层衬板", "350G玖龙粉灰", row=8),
            biz_row("JWXR21-P06", "右盖外盒外层衬板", "", row=9),            # 合并单元格
            biz_row("JWXR21-P27", "顶托EVA", "38度A级白色EVA 125×54×35MM异形", row=30)]


class ABusinessMaterialRows(BomCase):
    """纯函数也**必须**在设好 KB 快照的沙盘里跑：`_material_index()` 要读知识库
    （生产里走 `/wf/tech/kb/snapshot`，拿不到就抛 `KbUnavailable`）。"""

    def module(self):
        module = load_bom()
        self.assertIsNotNone(module, "缺少 tech_app/backend/services/packaging_bom.py")
        self.assertTrue(hasattr(module, "business_material_rows"),
                        "packaging_bom 里没有 business_material_rows()（Spec §C1）")
        return module

    def rows(self, *args, **kwargs):
        return self.module().business_material_rows(*args, **kwargs)

    def test_a1_dedup_keeps_first_seen_order_and_origin_text(self):
        rows = self.rows(biz_doc(real_sample_rows()))
        texts = [row.get("item_key") for row in rows]
        self.assertEqual(["225G太阳铜版底PET光银", "1.8MM双灰裱225G太阳铜版底PET光银",
                          "350G玖龙粉灰", "38度A级白色EVA 125×54×35MM异形"], texts,
                         "按**去重原文**收、保持首次出现顺序（合并单元格的件没有原文，跳过）")
        for row in rows:
            self.assertEqual(row.get("item_key"), row.get("item_name"))
            self.assertEqual(row.get("item_key"), row.get("material"))

    def test_a2_row_shape_matches_existing_material_rows(self):
        row = self.rows(biz_doc([biz_row("JWXR21-P01", "左盖面纸", "灰板 2.0mm")]))[0]
        self.assertEqual("material", row.get("bom_category"))
        self.assertEqual("computed", row.get("status"))
        self.assertEqual(0, int(row.get("is_optional") or 0))
        self.assertEqual(BUSINESS_SOURCE, row.get("source"))

    def test_a3_blank_and_illegal_input_is_empty_list(self):
        for payload in (None, {}, {"business_parts": []}, {"business_parts": "x"},
                        {"business_parts": [None, 3, "x"]},
                        biz_doc([biz_row("A-1", "没有材料", "")])):
            self.assertEqual([], self.rows(payload), "空/非法/无原文一律给 []（%r）" % (payload,))

    def test_a4_material_code_is_resolved_by_the_existing_matcher(self):
        module = self.module()
        materials = module._material_index()
        text = "灰板 2.0mm"
        given = self.rows(biz_doc([biz_row("JWXR21-P01", "盖面", text)]), materials=materials)
        expected = module._resolve_material_code(text, materials)
        self.assertEqual(expected, given[0].get("material_code"),
                         "材料码必须走既有唯一口径 _resolve_material_code()，不许另写一套匹配")
        without = self.rows(biz_doc([biz_row("JWXR21-P01", "盖面", text)]))
        self.assertEqual("", without[0].get("material_code"),
                         "不传 materials 时不许自己去读知识库（纯函数）")

    def test_a5_unresolvable_text_keeps_the_existing_empty_result(self):
        module = self.module()
        materials = module._material_index()
        text = "天上掉下来的材料 XYZ999"
        rows = self.rows(biz_doc([biz_row("JWXR21-P01", "盖面", text)]), materials=materials)
        self.assertFalse(rows[0].get("material_code"),
                         "解析不到就留空（不许编材料码）")
        self.assertEqual(module._resolve_material_code(text, materials),
                         rows[0].get("material_code"),
                         "留空也要与既有唯一口径逐字一致（不许自己发明一个空值形态）")

    def test_a6_pure_function_does_not_touch_repos(self):
        module = self.module()
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        body = ""
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name == "business_material_rows":
                body = ast.get_source_segment(source, node) or ""
        self.assertTrue(body, "business_material_rows() 必须是模块级函数（Spec §C1）")
        for forbidden in ("kb_repo.", "da_repo.", "store.", "get_backend("):
            self.assertNotIn(forbidden, body,
                             "business_material_rows() 体内不许出现 %s" % forbidden)


class BBuildBomWiring(BomCase):
    def build_with(self, rows):
        self.save_requirement()
        self.confirm_box()
        parts_module().save_business_parts(PID, biz_doc(rows))
        return self.bom().build_bom(PID, REQ_NO)

    def build_baseline(self):
        self.save_requirement()
        self.confirm_box()
        return self.bom().build_bom(PID, REQ_NO)

    def test_b1_authority_materials_replace_template_materials(self):
        result = self.build_with(real_sample_rows())
        rows = self.by_category(result, "material")
        self.assertEqual(4, len(rows), "材料组 = 清单里的去重原文（本夹具 4 条）")
        self.assertTrue(all(row.get("source") == BUSINESS_SOURCE for row in rows),
                        "材料行必须逐字带 %s" % BUSINESS_SOURCE)
        self.assertEqual([], [row for row in rows
                              if row.get("source") == TEMPLATE_MATERIAL_SOURCE])

    def test_b2_without_authority_list_behaviour_is_unchanged(self):
        rows = self.by_category(self.build_baseline(), "material")
        self.assertTrue(rows, "没有权威清单时材料组照旧来自模板展开")
        self.assertTrue(all(row.get("source") == TEMPLATE_MATERIAL_SOURCE for row in rows))

    def test_b3_other_groups_and_part_rows_are_untouched(self):
        baseline = self.build_baseline()
        with_biz = self.build_with(real_sample_rows())
        for category in ("finished", "process", "tooling", "packaging"):
            before = sorted(row["item_key"] for row in self.by_category(baseline, category))
            after = sorted(row["item_key"] for row in self.by_category(with_biz, category))
            self.assertEqual(before, after, "%s 组的行不许因为本批而变" % category)
        self.assertEqual(6, len([row for row in with_biz["items"]
                                 if row["bom_category"] in ("box_part", "optional_part")]),
                         "`## 377` 的部件组口径不许被打回（本夹具 6 件）")

    def test_b4_material_codes_come_from_the_existing_matcher(self):
        result = self.build_with([biz_row("JWXR21-P01", "盖面", "灰板 2.0mm")])
        module = self.bom()
        expected = module._resolve_material_code("灰板 2.0mm", module._material_index())
        row = self.by_category(result, "material")[0]
        self.assertEqual(expected, row.get("material_code"))
        self.assertEqual("灰板 2.0mm", row.get("item_key"))


class CReadBackDisclosure(BomCase):
    def build_with(self, rows):
        self.save_requirement()
        self.confirm_box()
        parts_module().save_business_parts(PID, biz_doc(rows))
        return self.bom().build_bom(PID, REQ_NO)

    def test_c1_disclosure_counts_and_sorted_keys(self):
        result = self.build_with(real_sample_rows())
        block = result.get("business_material_rows")
        self.assertIsInstance(block, dict, "load_bom() 必须给 business_material_rows（Spec §C3）")
        for key in DISCLOSURE_KEYS:
            self.assertIn(key, block, "business_material_rows 缺键 %s" % key)
        self.assertEqual(4, int(block["row_total"]))
        self.assertEqual(int(block["resolved_total"]) + int(block["unresolved_total"]),
                         int(block["row_total"]), "已解析 + 未解析必须等于总数")
        self.assertEqual(sorted(block["keys"]), list(block["keys"]), "keys 必须升序")

    def test_c2_without_authority_rows_every_counter_is_zero(self):
        self.save_requirement()
        self.confirm_box()
        result = self.bom().build_bom(PID, REQ_NO)
        block = result.get("business_material_rows") or {}
        self.assertEqual(0, int(block.get("row_total") or 0))
        self.assertEqual(0, int(block.get("resolved_total") or 0))
        self.assertEqual(0, int(block.get("unresolved_total") or 0))
        self.assertEqual([], list(block.get("keys") or []))

    def test_c3_part_rows_disclosure_is_unchanged(self):
        result = self.build_with(real_sample_rows())
        block = result.get("business_rows") or {}
        self.assertEqual(6, int(block.get("row_total") or 0),
                         "`## 377` 的 business_rows 仍只数部件组行（两把账分开）")


class DGuards(BomCase):
    def test_d1_categories_unchanged(self):
        module = self.bom()
        self.assertEqual(("finished", "box_part", "material", "process",
                          "packaging", "tooling", "optional_part"),
                         tuple(module.BOM_CATEGORIES))

    def test_d2_locked_authority_material_row_survives_rebuild(self):
        self.save_requirement()
        self.confirm_box()
        parts_module().save_business_parts(PID, biz_doc(real_sample_rows()))
        module = self.bom()
        module.build_bom(PID, REQ_NO)
        module.lock_bom_item(PID, REQ_NO, "350G玖龙粉灰", locked=True)
        again = module.build_bom(PID, REQ_NO)
        locked = [row for row in again["items"] if row["item_key"] == "350G玖龙粉灰"]
        self.assertEqual(1, len(locked), "锁定的材料行不许被重算删掉")
        self.assertEqual(1, int(locked[0].get("locked") or 0))
