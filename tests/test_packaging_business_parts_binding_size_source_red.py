"""红测：业务部件 ↔ 几何分量的绑定必须按「件的权威尺寸」，不许读一个不存在的 `bbox` 键。

Spec：`docs/specs/packaging-business-parts-binding-size-source.md`
依赖口径：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（判据 / 状态机 / 容差）、
          `docs/specs/packaging-parts-true-outline.md`（件的尺寸只有一个定义）

现状缺口（2026-09-22 实测，真样本 `裕同包装项目-待开发/酒盒.dwg` × `酒盒 报价资料.xlsx`）：

  · `extract()` 产出的 `parts` 行**没有** `bbox` 键；件的尺寸落在
    `unfolded_length_mm / unfolded_width_mm` + `outline_status` / `size_source` 上；
  · `geometry_evidence_of()` 只透传 `row.get("bbox")` → 组件尺寸恒为 `None`；
  · `_axis_pair_score()` 只读 `component["bbox"]` → 真样本上 **0 / 28** 命中（28 件全 `unbound`），
    绑定这一层等于没接上；同一份数据改读件权威尺寸后**19 件两轴命中 + 8 件单轴命中**。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import copy
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_parts  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "cad_ir"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
DWG = SAMPLES_DIR / "酒盒.dwg"
WORKBOOK = SAMPLES_DIR / "酒盒 报价资料.xlsx"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"

SIZE_KEYS = ("unfolded_length_mm", "unfolded_width_mm", "outline_status", "size_source")
SIZE_SOURCES = ("closed_outline", "component_bbox", "dwg_outline")


def fixture_ir() -> dict:
    return json.loads((FIXTURES / "parts_panels.json").read_text(encoding="utf-8"))


def business(code: str, length, width) -> dict:
    return {"business_part_code": code, "name": "件 %s" % code,
            "length_mm": length, "width_mm": width}


def component(component_id: str, *, unfolded=None, bbox=None,
              outline_status: str = "closed", size_source: str = "closed_outline") -> dict:
    row = {"component_id": component_id, "entity_ids": ["ent:%s" % component_id],
           "layers": ["CUT"], "role": "cut", "geometry_component_ref": component_id,
           "outline_status": outline_status, "size_source": size_source}
    if unfolded is not None:
        row["unfolded_length_mm"], row["unfolded_width_mm"] = unfolded
    if bbox is not None:
        row["bbox"] = list(bbox)
    return row


def one(plan: dict, code: str) -> dict:
    for row in plan.get("bindings") or []:
        if row.get("business_part_code") == code:
            return row
    raise AssertionError("绑定结果里没有 %s：%s" % (code, plan))


# --------------------------------------------------------------------------- #
# A 组：证据层必须把件尺寸带出来（现状：只带 bbox=None）
# --------------------------------------------------------------------------- #
class AEvidenceCarriesPartSize(unittest.TestCase):
    def setUp(self):
        self.doc = packaging_parts.extract(fixture_ir())
        self.evidence = packaging_parts.geometry_evidence_of(self.doc)
        self.by_id = {row.get("component_id"): row for row in self.doc["parts"]}

    def test_a1_every_component_carries_the_same_size_as_its_part_row(self):
        components = self.evidence.get("components") or []
        self.assertTrue(components, "证据层一个分量都没有")
        for row in components:
            part = self.by_id.get(row.get("component_id"))
            self.assertIsNotNone(part, "证据层出现了零件表里没有的分量：%s" % row.get("component_id"))
            for key in SIZE_KEYS:
                self.assertIn(key, row, "证据层分量缺 %s（绑定器拿不到件尺寸）" % key)
                self.assertEqual(part.get(key), row.get(key),
                                 "%s 的 %s 与零件行不一致" % (row.get("component_id"), key))

    def test_a2_the_bbox_key_is_not_the_only_size_carrier(self):
        row = (self.evidence.get("components") or [])[0]
        self.assertIsNone(row.get("bbox"), "夹具分量本来就没有 bbox，别伪造")
        self.assertIsNotNone(row.get("unfolded_length_mm"),
                             "`parts` 行没有 bbox 键；件尺寸只在 unfolded_* 上 —— "
                             "证据层不透传它，绑定判据就永远是 size_unknown")

    def test_a3_totals_are_unchanged(self):
        self.assertEqual(4, self.evidence.get("kept_component_total"))
        self.assertGreaterEqual(self.evidence.get("component_total") or 0,
                                self.evidence.get("kept_component_total") or 0)

    def test_a4_existing_reference_keys_survive(self):
        for row in self.evidence.get("components") or []:
            for key in ("component_id", "entity_ids", "layers", "role", "geometry_component_ref"):
                self.assertIn(key, row, "证据层丢了既有键 %s" % key)


# --------------------------------------------------------------------------- #
# B 组：绑定用件尺寸（现状：全 size_unknown）
# --------------------------------------------------------------------------- #
class BBindingUsesPartSize(unittest.TestCase):
    def test_b1_two_axes_hit_on_the_part_size(self):
        plan = packaging_parts.bind_geometry(
            [business("P01", 307.07, 528.89)],
            [component("cmp:139", unfolded=(308.834, 446.32)),
             component("cmp:17", unfolded=(307.07, 528.89))])
        row = one(plan, "P01")
        self.assertEqual("bound", row["status"], row)
        self.assertEqual(["cmp:17"], row["component_ids"])
        self.assertEqual(0.9, row["confidence"])
        self.assertEqual([], row["reasons"])
        self.assertEqual(["closed_outline"], row.get("size_sources"),
                         "命中件必须留下尺寸来源（Spec C5）")

    def test_b2_one_axis_only(self):
        plan = packaging_parts.bind_geometry([business("P01", 100.0, 80.0)],
                                             [component("cmp:a", unfolded=(100.0, 50.0))])
        row = one(plan, "P01")
        self.assertEqual("partial", row["status"], row)
        self.assertEqual(0.5, row["confidence"])
        self.assertIn("one_axis_only", row["reasons"])

    def test_b3_wrong_size_is_size_mismatch_not_size_unknown(self):
        plan = packaging_parts.bind_geometry([business("P01", 300.0, 10.0)],
                                             [component("cmp:a", unfolded=(100.0, 50.0))])
        row = one(plan, "P01")
        self.assertEqual("unbound", row["status"], row)
        self.assertIn("size_mismatch", row["reasons"], row["reasons"])
        self.assertNotIn("size_unknown", row["reasons"], row["reasons"])

    def test_b4_two_same_size_components_are_ambiguous_not_merged(self):
        plan = packaging_parts.bind_geometry(
            [business("P01", 100.0, 50.0)],
            [component("cmp:a", unfolded=(100.0, 50.0)),
             component("cmp:b", unfolded=(100.0, 50.0))])
        row = one(plan, "P01")
        self.assertEqual("ambiguous", row["status"], "同尺寸左右件不许并成一件（Spec C3）")
        self.assertEqual(["cmp:a", "cmp:b"], sorted(row["component_ids"]))

    def test_b5_no_size_at_all_is_size_unknown(self):
        plan = packaging_parts.bind_geometry([business("P01", 100.0, 50.0)],
                                             [component("cmp:a")])
        row = one(plan, "P01")
        self.assertEqual("unbound", row["status"], row)
        self.assertIn("size_unknown", row["reasons"], row["reasons"])
        self.assertEqual([], row.get("size_sources"), row)

    def test_b6_empty_component_list_keeps_the_old_reason(self):
        plan = packaging_parts.bind_geometry([business("P01", 100.0, 50.0)], [])
        row = one(plan, "P01")
        self.assertEqual("unbound", row["status"], row)
        self.assertIn("no_component_size_match", row["reasons"], row["reasons"])

    def test_b7_reason_codes_are_declared(self):
        # `REASON_CODES` 是**过筛**原因码，绑定有自己那套闭集（Spec §C4）。
        for code in ("size_unknown", "size_mismatch", "one_axis_only",
                     "no_component_size_match", "component_bbox_missing",
                     "no_authority_binding"):
            self.assertIn(code, packaging_parts.BUSINESS_BINDING_REASONS,
                          "%s 必须进 BUSINESS_BINDING_REASONS 闭集，不许散在代码里" % code)


# --------------------------------------------------------------------------- #
# C 组：护栏（现状即绿，改完必须仍绿）
# --------------------------------------------------------------------------- #
class CGuards(unittest.TestCase):
    def test_c1_bbox_only_components_still_bind(self):
        plan = packaging_parts.bind_geometry([business("P01", 100.0, 50.0)],
                                             [component("cmp:a", bbox=[0, 0, 100.0, 50.0],
                                                        outline_status="unavailable",
                                                        size_source="component_bbox")])
        row = one(plan, "P01")
        self.assertEqual("bound", row["status"], row)
        self.assertEqual(["component_bbox"], row.get("size_sources"), row)

    def test_c2_swapped_axes_still_count_as_two(self):
        plan = packaging_parts.bind_geometry([business("P01", 100.0, 50.0)],
                                             [component("cmp:a", unfolded=(50.0, 100.0))])
        self.assertEqual("bound", one(plan, "P01")["status"])

    def test_c3_tolerance_is_still_two_mm_or_five_percent(self):
        inside = packaging_parts.bind_geometry([business("P01", 100.0, 50.0)],
                                               [component("cmp:a", unfolded=(104.9, 50.0))])
        self.assertEqual("bound", one(inside, "P01")["status"],
                         "5% 容差内必须仍算两轴命中（容差一个字不许动）")
        outside = packaging_parts.bind_geometry([business("P01", 100.0, 50.0)],
                                               [component("cmp:a", unfolded=(105.2, 50.0))])
        self.assertEqual("partial", one(outside, "P01")["status"],
                         "超出 ±max(2mm, 5%) 就不许算两轴")

    def test_c4_bind_geometry_does_not_mutate_its_input(self):
        components = [component("cmp:a", unfolded=(100.0, 50.0))]
        before = copy.deepcopy(components)
        packaging_parts.bind_geometry([business("P01", 100.0, 50.0)], components)
        self.assertEqual(before, components, "绑定是纯函数：不许改传入的分量")

    def test_c5_the_binding_key_set_is_unchanged(self):
        plan = packaging_parts.bind_geometry([business("P01", 100.0, 50.0)],
                                             [component("cmp:a", unfolded=(100.0, 50.0))])
        row = one(plan, "P01")
        for key in ("business_part_code", "status", "component_ids", "entity_ids", "bbox",
                    "confidence", "reasons", "bound_by", "bound_at", "rule_id",
                    "geometry_component_ref"):
            self.assertIn(key, row, "binding 既有键 %s 不许消失" % key)
        self.assertEqual("business_parts_geometry_binding_v1", row["rule_id"])


# --------------------------------------------------------------------------- #
# D 组：真样本（本机有样本 + dwg2dxf + 工作簿才跑）
# --------------------------------------------------------------------------- #
class DRealSample(unittest.TestCase):
    def setUp(self):
        if not DWG.exists() or not WORKBOOK.exists():
            self.skipTest("真实样本不在本机：%s" % SAMPLES_DIR)
        if not shutil.which("dwg2dxf"):
            self.skipTest("本机没有 libredwg 的 dwg2dxf")
        try:
            from tech_app.backend.services import packaging_reference_workbook
        except Exception as exc:                                            # noqa: BLE001
            self.skipTest("权威清单导入器不可用：%s" % exc)
        self.authority = packaging_reference_workbook

    def real_geometry(self) -> dict:
        cache = pathlib.Path(tempfile.mkdtemp()) / "real.dxf"
        subprocess.run([shutil.which("dwg2dxf"), "-y", "-o", str(cache), str(DWG)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from tech_app.backend.services import cad_ir
        ir = cad_ir.parse_dxf(cache.read_bytes(), filename=cache.name,
                              source={"kind": "dxf_2d", "attachment_name": "酒盒.dwg"})
        return packaging_parts.extract(ir)

    def test_d1_real_sample_actually_binds(self):
        evidence = packaging_parts.geometry_evidence_of(self.real_geometry())
        authority = self.authority.import_workbook(str(WORKBOOK))
        plan = packaging_parts.bind_geometry(authority["parts"], evidence["components"])
        stats = packaging_parts.business_parts_stats(
            [{"geometry_binding": row} for row in plan["bindings"]])
        self.assertEqual(28, stats["business_part_total"], stats)
        located = stats["bound_total"] + stats["partial_total"] + stats["ambiguous_total"]
        self.assertGreaterEqual(
            located, 20,
            "真样本上 28 件业务部件必须有 20 件以上找到尺寸相符的几何分量（改判据前是 0 件："
            "判据只读 `parts` 行里不存在的 bbox 键 → 每件都 size_unknown → 全 unbound）；"
            "实测：%s" % stats)
        self.assertGreaterEqual(
            stats["bound_total"] + stats["ambiguous_total"], 15,
            "找到候选的件必须落到 bound/ambiguous，而不是掉回 unbound；实测：%s" % stats)

    def test_d1b_real_sample_uses_the_loop_size_not_only_the_bbox(self):
        evidence = packaging_parts.geometry_evidence_of(self.real_geometry())
        authority = self.authority.import_workbook(str(WORKBOOK))
        plan = packaging_parts.bind_geometry(authority["parts"], evidence["components"])
        sources = {source for row in plan["bindings"] for source in (row.get("size_sources") or [])}
        self.assertIn("closed_outline", sources,
                      "真样本上有 134 件闭合件：绑定必须真的用到环尺寸（Spec C1），实测来源集：%s"
                      % sorted(sources))

    def test_d2_every_binding_reports_a_declared_reason_and_size_source(self):
        evidence = packaging_parts.geometry_evidence_of(self.real_geometry())
        authority = self.authority.import_workbook(str(WORKBOOK))
        plan = packaging_parts.bind_geometry(authority["parts"], evidence["components"])
        for row in plan["bindings"]:
            self.assertIn(row["status"], packaging_parts.BUSINESS_BINDING_STATUSES, row)
            for reason in row["reasons"]:
                self.assertIn(reason, packaging_parts.BUSINESS_BINDING_REASONS,
                              "未登记的原因码：%s" % reason)
            for source in row.get("size_sources") or []:
                self.assertIn(source, SIZE_SOURCES,
                              "命中分量的尺寸来源必须出自件尺寸口径：%s" % source)
            if row["status"] in ("bound", "partial", "ambiguous"):
                self.assertTrue(row.get("size_sources"),
                                "命中件必须留下尺寸来源：%s" % row["business_part_code"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
