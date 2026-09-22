"""红测：BOM 的尺寸质量必须有账（包围盒行不许和真展开行一样算"已算好"）。

Spec：`docs/specs/packaging-bom-size-quality-accounting.md`

现状缺口（代码级，三处，都可指到行）：
  · `packaging_bom.py:881 _item_out()` 把 `binding_evidence` / `binding_method` / `bound_by` /
    `part_role` 提到行顶层，**不提** `size_source` / `outline_status` / `size_quality`
    —— BOM 面板要判断"这一行的数是不是包围盒"，只能自己钻 `size_source_json.dwg_binding`
    （同仓 `packaging_bom.py:657-658` 的未映射清单反而带了这两样，口径不一致）；
  · `packaging_bom.py:897 _stats()` 只数 `computed` / `needs_input` / `locked` /
    `material_unresolved` —— 整份 BOM **没有尺寸质量账**：`bbox_only` 全仓只出现在
    `packaging_parts.py:227` 的常量与 docstring 里，
    `grep -c "size_source\\|size_quality\\|outline_status" packaging_cost.py` → 0；
  · 材料费按 `cut_length × cut_width` 算，包围盒越大越贵，而"几行、哪几行是包围盒贡献的"
    在任何汇总上都没有账（承接 `packaging-bom-part-size-provenance.md` §1.3）。

纪律：只读源码 + 纯函数 / 假后端；不连 PG、不发 HTTP、不写任何文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_bom as bom            # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-QUALITY-001"

BBOX_BINDING = {"part_code": "DWG-P01", "component_id": "cmp:47",
                "size_source": "component_bbox", "outline_status": "open",
                "size_quality": "bbox_only", "binding_method": "auto_position_area",
                "bound_by": "system", "part_role": "unbound"}
UNFOLDED_BINDING = {"part_code": "DWG-P04", "component_id": "cmp:50",
                    "size_source": "closed_outline", "outline_status": "closed",
                    "size_quality": "unfolded", "binding_method": "auto_position_area",
                    "bound_by": "system", "part_role": "面纸"}
#: 有绑定留痕、但**没有** `size_source`：按 `size_quality_of()` 的口径必须算包围盒档。
NO_SOURCE_BINDING = {"part_code": "DWG-P07", "component_id": "cmp:60"}


def _db_row(item_key: str, binding=None, category: str = "box_part",
            status: str = "computed", material: str = "", material_code: str = "") -> dict:
    """落库回来的行：`size_source_json` 是 DB 里的 JSON 文本（`_item_out()` 这么读）。"""
    source = {}
    if binding is not None:
        source["dwg_binding"] = dict(binding)
    return {"item_key": item_key, "bom_category": category, "status": status, "locked": 0,
            "length_mm": 440.123, "width_mm": 482.92, "material": material,
            "material_code": material_code,
            "size_source_json": json.dumps(source, ensure_ascii=False)}


MATERIAL_ROW = _db_row("MAT-01", None, category="material", status="needs_input",
                       material="灰板")


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, value in reversed(self.saved):
            setattr(owner, name, value)
        return False


def _load(rows):
    with _Patch((bom.da_repo, "load_packaging_bom", lambda *a, **k: rows),
                (bom.da_repo, "load_box_match", lambda *a, **k: {}),
                (bom, "_load_pairing_review", lambda *a, **k: []),
                (bom, "_role_scope", lambda *a, **k: {"items": [], "unbound_total": 0})):
        return bom.load_bom(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# G 组（行顶层）：包围盒行的来源与质量必须能直接读到
# --------------------------------------------------------------------------- #
class GRowLevelSizeQuality(unittest.TestCase):
    def test_g1_row_exposes_size_source_and_quality(self):
        out = bom._item_out(_db_row("RB02001-P02", BBOX_BINDING))
        self.assertEqual("component_bbox", out.get("size_source"),
                         "行顶层必须能直接读到尺寸来源（Spec §2.1）：现在只有钻进 "
                         "`size_source_json.dwg_binding` 才看得到，BOM 面板因此一律显示成一样的数")
        self.assertEqual("open", out.get("outline_status"),
                         "行顶层必须能直接读到轮廓状态（与 `packaging_bom.py:657-658` 同一口径）")
        self.assertEqual("bbox_only", out.get("size_quality"),
                         "行顶层必须能直接读到尺寸质量档（`unfolded` / `bbox_only`）")

    def test_g2_existing_lifted_fields_are_unchanged(self):
        out = bom._item_out(_db_row("RB02001-P02", BBOX_BINDING))
        self.assertEqual("auto_position_area", out.get("binding_method"), "既有提升字段逐字不变")
        self.assertEqual("system", out.get("bound_by"), "既有提升字段逐字不变")
        self.assertEqual("unbound", out.get("part_role"), "既有提升字段逐字不变")
        self.assertEqual({}, out.get("binding_evidence") or {}, "既有提升字段逐字不变")


# --------------------------------------------------------------------------- #
# G 组（整份汇总）：`_stats()` 必须有尺寸质量账，且按行计一次
# --------------------------------------------------------------------------- #
class GStatsSizeQuality(unittest.TestCase):
    def _rows(self):
        return [_db_row("RB02001-P02", BBOX_BINDING),
                _db_row("RB02001-P08", UNFOLDED_BINDING),
                dict(MATERIAL_ROW)]

    def test_g3_stats_count_the_three_buckets(self):
        stats = bom._stats([bom._item_out(row) for row in self._rows()])
        self.assertEqual({"unfolded": 1, "bbox_only": 1, "unknown": 1},
                         stats.get("size_quality"),
                         "整份 BOM 必须有尺寸质量账（Spec §2.2）：现在一个数都没有，"
                         "33 行里 3 行是包围盒这件事在读接口上完全看不出来")

    def test_g4_existing_stats_keys_are_unchanged(self):
        stats = bom._stats([bom._item_out(row) for row in self._rows()])
        self.assertEqual(3, stats.get("total"), "既有键逐字不变")
        self.assertEqual({"box_part": 2, "material": 1}, stats.get("by_category"),
                         "既有键逐字不变")
        self.assertEqual(2, stats.get("computed"), "既有键逐字不变")
        self.assertEqual(0, stats.get("needs_input"), "既有键逐字不变")
        self.assertEqual(0, stats.get("locked"), "既有键逐字不变")
        self.assertEqual(1, stats.get("material_unresolved"), "既有键逐字不变")


# --------------------------------------------------------------------------- #
# G 组（读接口）：`gaps.bbox_only` 与"来源缺失按包围盒"的口径
# --------------------------------------------------------------------------- #
class GLoadBomGaps(unittest.TestCase):
    def test_g5_gaps_lists_the_bbox_rows(self):
        doc = _load([_db_row("RB02001-P08", UNFOLDED_BINDING),
                     _db_row("RB02001-P02", BBOX_BINDING),
                     dict(MATERIAL_ROW)])
        self.assertEqual(["RB02001-P02"], (doc.get("gaps") or {}).get("bbox_only"),
                         "读接口必须把包围盒行的 item_key 列出来（Spec §2.3，按 item_key 升序）："
                         "现在 `gaps` 只有 needs_input / missing_variables / material_unresolved")
        self.assertEqual(1, ((doc.get("stats") or {}).get("size_quality") or {}).get("bbox_only"),
                         "行数与清单必须对得上：stats 记 1 行、gaps 列 1 行")

    def test_g6_no_bbox_rows_keeps_the_account_empty(self):
        doc = _load([_db_row("RB02001-P08", UNFOLDED_BINDING),
                     _db_row("RB02001-P09", UNFOLDED_BINDING)])
        self.assertEqual([], (doc.get("gaps") or {}).get("bbox_only") or [],
                         "没有包围盒行时清单必须是 []（不许常驻非空，否则等于没披露）")
        self.assertEqual(0, (doc.get("stats") or {}).get("size_quality", {}).get("bbox_only") or 0,
                         "同口径：没有包围盒行时计数必须是 0")

    def test_g7_missing_source_counts_as_bbox_not_unfolded(self):
        out = bom._item_out(_db_row("RB02001-P10", NO_SOURCE_BINDING))
        self.assertEqual("", out.get("size_source") or "",
                         "来源缺失时行上给空串，不许编一个来源")
        stats = bom._stats([out])
        self.assertEqual(1, (stats.get("size_quality") or {}).get("bbox_only"),
                         "有绑定留痕但没有尺寸来源的行必须计进 bbox_only（Spec §2.2 末条，"
                         "与 `size_quality_of()` 的『缺失一律按包围盒』同一个口径）："
                         "不许因为这一行有数就把它当成展开尺寸")


if __name__ == "__main__":
    unittest.main()
