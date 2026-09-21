"""红测：零件自检要能"指着原因说话"（不可算/不可挤出按第一原因汇总，两份样本对等达标）。

Spec：`docs/specs/packaging-parts-selfcheck-diagnostics.md`

现状缺口（34 实测，部署 `0d8884d` 的第 6b 步）：
  · 酒盒.dwg：64 件（closed_ratio=0.938）、可算 9 / 可挤出 6；
  · 圆盘盒.dwg：9 件（closed_ratio=0.889）、可算 0 / 可挤出 0；
  · 自检只说"圆盘盒.dwg：没有一件能跑工艺"，**不说**是缺材料还是缺厚度，也不说挤出为什么
    unsupported —— 只能人肉逐件去翻 `GET /requirement/packaging-parts`。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_part_solids, packaging_parts  # noqa: E402

DEPLOY_SH = ROOT / "scripts" / "deploy_34_bare.sh"
SPEC_BLOCK = "6b."
PROCESSABILITY_CODES = ("PACKAGING_PART_NOT_FOUND", "PACKAGING_PART_NOT_CLOSED",
                        "PACKAGING_PART_MATERIAL_UNKNOWN")
UNPROCESSABLE_KEY = "unprocessable_reason_mix"
SOLID_REASON_KEY = "solid_reason_mix"
PERMISSIVE = {"min_area_mm2": 0.0, "max_edge_mm": 100000.0,
              "max_area_mm2": 1e12, "max_parts": 512}


def make_ir(pieces):
    components, entities = [], []
    for component, rows in pieces:
        components.append(component)
        entities.extend(rows)
    return {"ir_version": "cad-ir/1", "ir_id": "ir:selfcheck", "ir_hash": "hash",
            "parser": {"name": "test"}, "source": {"kind": "dxf_2d", "attachment_name": "t.dxf"},
            "units": {"drawing_units": "mm", "scale_to_mm": 1.0, "unit_status": "confirmed",
                      "unit_confidence": 1.0, "candidates": []},
            "layers": [], "entities": entities, "texts": [], "dimensions": [],
            "geometry": {"components": components}, "evidence": {}, "unsupported": [],
            "warnings": [], "stats": {}}


def segment(tag, index, start, end, layer="DESIGN"):
    return {"entity_id": "ent:model:%s-%04d" % (tag, index), "type": "LINE", "layer": layer,
            "attributes": {"start": [float(start[0]), float(start[1])],
                           "end": [float(end[0]), float(end[1])]}}


def bbox_of(points):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def open_lines(tag, count, spacing=200.0):
    """count 条互不相连的线段：轮廓必然 open（第一原因 = PACKAGING_PART_NOT_CLOSED）。"""
    rows, eids, points = [], [], []
    for index in range(count):
        start = (index * spacing, 0.0)
        end = (index * spacing + 60.0, 40.0)
        entity = segment(tag, index, start, end)
        rows.append(entity)
        eids.append(entity["entity_id"])
        points.extend([start, end])
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids, "bbox": bbox_of(points)}
    return component, rows


def closed_rect(tag, size=900.0):
    corners = [(0.0, 0.0), (size, 0.0), (size, size), (0.0, size)]
    rows, eids, points = [], [], []
    for index in range(4):
        start, end = corners[index], corners[(index + 1) % 4]
        entity = segment(tag, index, start, end)
        rows.append(entity)
        eids.append(entity["entity_id"])
        points.extend([start, end])
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids, "bbox": bbox_of(points)}
    return component, rows


def stats_of(summary):
    stats = summary.get("stats") if isinstance(summary.get("stats"), dict) else {}
    merged = dict(stats)
    merged.update({key: value for key, value in summary.items() if key != "stats"})
    return merged


def deploy_block():
    text = DEPLOY_SH.read_text(encoding="utf-8")
    start = text.index(SPEC_BLOCK)
    rest = text[start + len(SPEC_BLOCK):]
    stops = [rest.index(marker) for marker in ("step \"7.", "step \"7b", "\n# ----")
             if marker in rest]
    return rest[:min(stops)] if stops else rest


class UnprocessableLedger(unittest.TestCase):
    """A 组：不可算的账必须由 summarize() 出，且分母与 processable_ratio 一致。"""

    def doc(self):
        pieces = [open_lines("u", 5), closed_rect("c")]
        return packaging_parts.extract(make_ir(pieces), options=dict(PERMISSIVE))

    def test_a1_summary_has_unprocessable_reason_mix(self):
        summary = stats_of(packaging_parts.summarize(self.doc()))
        mix = summary.get(UNPROCESSABLE_KEY)
        self.assertIsInstance(mix, dict, "summarize() 必须返回 %s（Spec §2）" % UNPROCESSABLE_KEY)
        self.assertTrue(mix, "这份夹具里每一件都不可算，账不该是空的")

    def test_a2_ledger_denominator_matches_processable_ratio(self):
        doc = self.doc()
        summary = stats_of(packaging_parts.summarize(doc))
        mix = summary.get(UNPROCESSABLE_KEY) or {}
        rows = doc.get("parts") or []
        unprocessable = [row for row in rows
                         if not packaging_parts.processability(row).get("ok")]
        self.assertEqual(sum(mix.values()), len(unprocessable),
                         "不可算的账与逐件判定的分母不一致（Spec §2.1）")
        self.assertEqual(sum(mix.values()) + round(float(summary.get("processable_ratio") or 0.0)
                                                  * len(rows)), len(rows))

    def test_a3_empty_document_gives_empty_ledgers(self):
        summary = stats_of(packaging_parts.summarize({"parts": [], "stats": {"part_total": 0}}))
        self.assertEqual(summary.get(UNPROCESSABLE_KEY), {}, "空文档必须是 {}，不是 null")
        self.assertEqual(summary.get(SOLID_REASON_KEY), {}, "空文档必须是 {}，不是 null")

    def test_a4_solid_reason_ledger_counts_unsupported(self):
        doc = self.doc()
        rows = doc.get("parts") or []
        code = rows[0].get("part_code")
        solids = {"parts": [{"part_code": code, "status": "unsupported",
                             "reason": "thickness_unknown"}]}
        summary = stats_of(packaging_parts.summarize(doc, solids=solids))
        mix = summary.get(SOLID_REASON_KEY)
        self.assertIsInstance(mix, dict, "summarize() 必须返回 %s（Spec §2）" % SOLID_REASON_KEY)
        self.assertEqual(mix.get("thickness_unknown"), 1, "挤出原因必须按件数入账：%r" % (mix,))


class ReasonCodes(unittest.TestCase):
    """B 组：每一件的第一原因必须落在闭集里，且账是确定的。"""

    def test_b1_processability_codes_are_closed(self):
        doc = packaging_parts.extract(make_ir([open_lines("b", 3)]), options=dict(PERMISSIVE))
        for row in doc.get("parts") or []:
            verdict = packaging_parts.processability(row)
            if verdict.get("ok"):
                self.assertEqual(verdict.get("code"), "")
            else:
                self.assertIn(verdict.get("code"), PROCESSABILITY_CODES,
                              "不可算原因 %r 不在闭集里" % verdict.get("code"))
                self.assertTrue(verdict.get("missing_variables") is not None)

    def test_b2_solid_reasons_are_closed(self):
        self.assertIn("thickness_unknown", packaging_part_solids.UNSUPPORTED_REASONS)
        row = {"part_code": "DWG-P01", "outline_status": "closed", "thickness_mm": None,
               "outline": {"points": [[0, 0], [10, 0], [10, 10], [0, 10]]}}
        out = packaging_part_solids.extrude(row)
        self.assertEqual(out.get("status"), "unsupported")
        self.assertIn(out.get("reason"), packaging_part_solids.UNSUPPORTED_REASONS)

    def test_b3_ledger_order_is_deterministic(self):
        doc = packaging_parts.extract(make_ir([open_lines("d", 4), closed_rect("d2")]),
                                      options=dict(PERMISSIVE))
        first = stats_of(packaging_parts.summarize(doc)).get(UNPROCESSABLE_KEY)
        second = stats_of(packaging_parts.summarize(doc)).get(UNPROCESSABLE_KEY)
        self.assertEqual(first, second, "同一份零件文档两次 summarize 的账必须逐字相同")
        if isinstance(first, dict) and first:
            self.assertEqual(list(first), sorted(first, key=lambda key: (-first[key], key)),
                             "账必须按件数降序、同数按 code 字典序（Spec §3）")


class DeploySelfCheckPrintsLedgers(unittest.TestCase):
    """C 组：部署自检必须把这两把账打出来（静态钉住）。"""

    def setUp(self):
        self.block = deploy_block()

    def test_c1_prints_unprocessable_reasons(self):
        self.assertIn("不可算原因", self.block, "第 6b 步必须打印不可算原因（Spec §3）")

    def test_c2_prints_solid_reasons(self):
        self.assertIn("不可挤出原因", self.block, "第 6b 步必须打印不可挤出原因（Spec §3）")

    def test_c3_uses_the_same_ledger_from_summarize(self):
        self.assertIn(UNPROCESSABLE_KEY, self.block,
                      "自检必须用 summarize() 的同一份账，不许在脚本里重算（Spec §3）")
        self.assertIn(SOLID_REASON_KEY, self.block,
                      "自检必须用 summarize() 的同一份账，不许在脚本里重算（Spec §3）")


class SampleParityGate(unittest.TestCase):
    """D 组：两份样本对等达标（护栏，防为了发车放宽门槛）。"""

    def setUp(self):
        self.block = deploy_block()

    def test_d1_both_samples_need_processable_and_solid(self):
        self.assertIn("没有一件能跑工艺", self.block)
        self.assertIn("没有一件能挤出 3D", self.block)
        self.assertIn("fail", self.block, "自检未通过必须非零退出（Spec §4）")
        self.assertIn("酒盒.dwg", self.block)
        self.assertIn("圆盘盒.dwg", self.block)


if __name__ == "__main__":
    unittest.main()
