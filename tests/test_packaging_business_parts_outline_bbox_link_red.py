# -*- coding: utf-8 -*-
"""红测：几何零件的轮廓矩形必须能被下游读到，绑定链断了必须自证（红）。

Spec：`docs/specs/packaging-business-parts-outline-bbox-broken-link.md`

现状缺口（2026-09-23 本机隔离 `DATA_DIR` 真跑，非推断）：业务部件清单的来源闭集已经落地
（`derived_from_drawing=true`），但两份真样本**一件都绑不上图**：

    酒盒  ：业务部件 26 行 → bound+partial 0 / unbound 26；`no_outline_evidence` 26 条
    圆盘盒：业务部件 66 行 → bound+partial 0 / unbound 66；`no_outline_evidence` 66 条

真因在字段路径：`packaging_parts.extract()` 的零件行把轮廓矩形放在 `outline.bbox`
（真样本 263/312 行都有），行顶层没有 `bbox` 键；而
`packaging_business_part_resolver.regions_from_geometry_parts()` 读的是 `row.get("bbox")`
→ 每条 region 的 `bbox` / `center` 全空 → `_assign_outlines()` 里
`_region_center(region) is None` → **每一件都 continue** → 必然 0 绑定。
把这一处接上（本机实测）后：酒盒 26/26 绑定、圆盘盒 39/66。

本批只钉"轮廓矩形可读 + 断链自证"这两件事，不改成本 / BOM / 工艺公式，不改既有键名与数值口径。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE_ROOT = ROOT / "tech_app/data/cad-ir-realsample/conversions"

#: 真样本：conversion_id 与 DWG 的 sha-256（取自转换 manifest）。
SAMPLES = {
    "酒盒": ("47c39dc1ab6738fc48c8",
             "0991c8b0a9646d1fea6571d2ae6155923351c05ca2aeeb6ed544ef19df93f3e0"),
    "圆盘盒": ("a6140fbc4e9b8d2e9bee",
               "4c70ce7b3774c2a1803a942341a606c92758228cf982c3bf1016642be44a531b"),
}

#: 断链码（Spec §1.3）：稳定字符串，模块上必须有一个同名常量。
OUTLINE_LINK_BROKEN = "PACKAGING_BUSINESS_PARTS_OUTLINE_LINK_BROKEN"

#: 真样本验收门槛（Spec §1.5，留 20% 余量；本机修后实测酒盒 26 / 圆盘盒 39）。
MIN_DERIVED = {"酒盒": 20, "圆盘盒": 31}

#: 不回归（Spec §1.6）：几何零件提取本身不许变。
GEOMETRY_TOTAL = {"酒盒": 263, "圆盘盒": 312}
CLOSED_RATIO = {"酒盒": 0.510, "圆盘盒": 0.817}

#: 区域有 bbox 的比例门槛（两份样本都是 100%，留一点余量）。
MIN_REGION_RECT_RATIO = 0.99


def _parts_module():
    from tech_app.backend.services import packaging_parts as module
    return module


def _resolver():
    from tech_app.backend.services import packaging_business_part_resolver as module
    return module


_IR_CACHE: dict = {}
_DOC_CACHE: dict = {}
_OUT_CACHE: dict = {}


def _real_ir(label: str):
    if label in _IR_CACHE:
        return _IR_CACHE[label]
    from tech_app.backend.services import cad_ir
    conversion_id, drawing_sha = SAMPLES[label]
    path = SAMPLE_ROOT / conversion_id / "converted.dxf"
    ir = cad_ir.parse_dxf(path.read_bytes(), filename="%s.dxf" % label,
                          source={"source_sha256": drawing_sha,
                                  "original_filename": "%s.dwg" % label})
    _IR_CACHE[label] = ir
    return ir


def _real_parts(label: str):
    if label in _DOC_CACHE:
        return _DOC_CACHE[label]
    doc = _parts_module().extract(_real_ir(label))
    _DOC_CACHE[label] = doc
    return doc


def _require_sample(test: unittest.TestCase, label: str):
    conversion_id, _ = SAMPLES[label]
    if not (SAMPLE_ROOT / conversion_id / "converted.dxf").exists():
        test.skipTest("缺少真样本 DXF（%s）" % conversion_id)


def _resolve(label: str):
    """真样本 → 业务部件解析结论（分辨率与图纸流一致：把**过滤后**的几何零件文档传进去）。"""
    if label in _OUT_CACHE:
        return _OUT_CACHE[label]
    out = _resolver().resolve_business_parts("red-test", _real_ir(label), _real_parts(label))
    _OUT_CACHE[label] = out
    return out


def _rows(out) -> list:
    return [row for row in (out.get("authority_rows") or []) if isinstance(row, dict)]


def _detail(out) -> dict:
    return out.get("detail") if isinstance(out.get("detail"), dict) else {}


def _derived_rows(out) -> list:
    return [row for row in _rows(out) if str(row.get("status") or "") == "derived"]


def _by_code(doc: dict) -> dict:
    return {str(row.get("part_code")): row for row in (doc.get("parts") or [])
            if isinstance(row, dict)}


def _regions(doc: dict) -> list:
    return list(_resolver().regions_from_geometry_parts(doc) or [])


def _region_rect(region: dict):
    bbox = region.get("bbox")
    return list(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) >= 4 else None


def _row_rect(row: dict):
    """测试侧自己算这一件**应该**有的轮廓矩形（顶层 `bbox` 或 `outline.bbox`）。"""
    if isinstance(row.get("bbox"), (list, tuple)) and len(row["bbox"]) >= 4:
        return [float(value) for value in row["bbox"][:4]]
    outline = row.get("outline") if isinstance(row.get("outline"), dict) else {}
    bbox = outline.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        return [float(value) for value in bbox[:4]]
    return None


def _fixture_doc() -> dict:
    """合成夹具：一件顶层有 bbox、一件只有 `outline.bbox`、一件两处都没有。

    三件都够 `substantial`（边长 ≥ 5mm、面积 ≥ 2000mm²、图元 ≥ 2）才是**能当候选**的轮廓 ——
    第三件的"没矩形"才是这条链断点（不是被 `substantial` 判掉的）。
    """
    closed = {"points": None, "closed": True, "area_mm2": 6000.0}
    return {
        "engine_version": "fixture",
        "stats": {"kept_total": 3},
        "parts": [
            {"part_code": "DWG-P01", "component_id": "cmp:1", "entity_ids": ["e1", "e2"],
             "bbox": [0.0, 0.0, 100.0, 60.0],
             "unfolded_length_mm": 100.0, "unfolded_width_mm": 60.0,
             "layers": ["全穿刀"], "area_mm2": 6000.0, "outline_status": "closed",
             "outline": dict(closed, bbox=[0.0, 0.0, 100.0, 60.0])},
            {"part_code": "DWG-P02", "component_id": "cmp:2", "entity_ids": ["e3", "e4"],
             "unfolded_length_mm": 80.0, "unfolded_width_mm": 40.0,
             "layers": ["压线"], "area_mm2": 3200.0, "outline_status": "closed",
             "outline": dict(closed, area_mm2=3200.0, bbox=[200.0, 10.0, 280.0, 50.0])},
            {"part_code": "DWG-P03", "component_id": "cmp:3", "entity_ids": ["e5", "e6"],
             "unfolded_length_mm": None, "unfolded_width_mm": None,
             "layers": ["0"], "area_mm2": 0.0, "outline_status": "open",
             "outline": {"points": None, "closed": False, "area_mm2": None, "bbox": None}},
        ],
    }


# --------------------------------------------------------------------------- #
# A 组：轮廓矩形必须能从**行**上取到，并且必须成为区域记录的 bbox/center
# --------------------------------------------------------------------------- #
class AOutlineRectIsReadableFromTheRow(unittest.TestCase):
    def test_a1_there_is_one_named_accessor_for_the_part_outline_rect(self):
        rect_of = getattr(_parts_module(), "part_outline_rect", None)
        self.assertTrue(callable(rect_of),
                        "`packaging_parts` 必须给一个**唯一**的轮廓矩形取法"
                        "（`part_outline_rect(row)`）—— 否则每个下游都要自己猜字段路径，"
                        "今天这种断链就没人看得出")

    def test_a2_the_accessor_sees_both_kinds_of_rows_and_says_none_when_there_is_none(self):
        rect_of = getattr(_parts_module(), "part_outline_rect", None)
        if not callable(rect_of):
            self.skipTest("没有 `part_outline_rect()`（A1 已在红）")
        by_code = _by_code(_fixture_doc())
        self.assertEqual([0.0, 0.0, 100.0, 60.0], list(rect_of(by_code["DWG-P01"])),
                         "顶层就有 bbox 的行必须原样取到")
        self.assertEqual([200.0, 10.0, 280.0, 50.0], list(rect_of(by_code["DWG-P02"])),
                         "只有 `outline.bbox` 的行必须**派生**得到（真样本 263/312 行都是这种）")
        self.assertIsNone(rect_of(by_code["DWG-P03"]), "两处都没有就回 None，不许编")

    def test_a3_every_region_with_a_rect_carries_bbox_and_center(self):
        regions = {str(region.get("region_id")): region
                   for region in _regions(_fixture_doc())}
        for part_code, expected in (("DWG-P01", [0.0, 0.0, 100.0, 60.0]),
                                    ("DWG-P02", [200.0, 10.0, 280.0, 50.0])):
            region = regions.get("region:%s" % part_code)
            self.assertIsNotNone(region, "区域记录里少了 %s" % part_code)
            self.assertEqual([float(value) for value in expected], _region_rect(region),
                             "%s 的轮廓矩形必须进区域记录（现状读的是行顶层 `bbox`，恒为 None）"
                             % part_code)
            center = region.get("center")
            self.assertTrue(isinstance(center, (list, tuple)) and len(center) >= 2,
                            "%s 的区域没有 center —— 配对判据第一行就 continue，绑定必然 0"
                            % part_code)
            self.assertAlmostEqual((expected[0] + expected[2]) / 2.0, float(center[0]), places=6)
            self.assertAlmostEqual((expected[1] + expected[3]) / 2.0, float(center[1]), places=6)
            self.assertTrue(region.get("substantial"), "%s 够 substantial，不该被当成碎片"
                            % part_code)

    def test_a4_a_region_without_any_rect_must_say_so_instead_of_silently_being_empty(self):
        regions = {str(region.get("region_id")): region
                   for region in _regions(_fixture_doc())}
        region = regions.get("region:DWG-P03")
        self.assertIsNotNone(region, "区域记录里少了 DWG-P03")
        self.assertIsNone(_region_rect(region), "两处都没有矩形的行不许编一个出来")
        self.assertTrue(str(region.get("excluded") or "").strip(),
                        "拿不到矩形的区域必须留 `excluded` 原因（静默空值就是这条链断了两天"
                        "没人发现的原因）")
        self.assertFalse(region.get("substantial"), "没有矩形的区域不能当候选件")

    def test_a5_regions_are_one_to_one_with_part_rows(self):
        for label in SAMPLES:
            _require_sample(self, label)
            doc = _real_parts(label)
            codes = sorted(str(row.get("part_code")) for row in (doc.get("parts") or []))
            regions = _regions(doc)
            self.assertEqual(len(codes), len(regions),
                             "%s：区域数与零件数不一致（口径变了吗）" % label)
            self.assertEqual(sorted("region:%s" % code for code in codes),
                             sorted(str(region.get("region_id")) for region in regions),
                             "%s：区域 id 与零件号不再一一对应" % label)

    def test_a6_real_samples_regions_all_carry_a_rect(self):
        for label in SAMPLES:
            _require_sample(self, label)
            doc = _real_parts(label)
            regions = _regions(doc)
            self.assertTrue(regions, "%s：一条区域都没有" % label)
            with_rect = [region for region in regions if _region_rect(region)]
            ratio = len(with_rect) / float(len(regions))
            self.assertGreaterEqual(
                ratio, MIN_REGION_RECT_RATIO,
                "%s：有轮廓矩形的区域只有 %d/%d（真样本零件行的 `outline.bbox` 是"
                " %d/%d 齐的）—— 区域读的字段路径与零件行不一致"
                % (label, len(with_rect), len(regions),
                   len([row for row in (doc.get("parts") or []) if _row_rect(row)]),
                   len(doc.get("parts") or [])))


# --------------------------------------------------------------------------- #
# B 组：绑定链断了必须自证（不许把整份清单标成 no_outline_evidence 了事）
# --------------------------------------------------------------------------- #
class BBrokenLinkMustSaySo(unittest.TestCase):
    def test_b1_the_broken_link_code_has_one_constant(self):
        module = _resolver()
        self.assertEqual(OUTLINE_LINK_BROKEN, getattr(module, "OUTLINE_LINK_BROKEN", ""),
                         "断链码必须是模块级常量（前端与运维按它说话）")

    def test_b2_detail_always_discloses_how_many_regions_have_a_center(self):
        for label in SAMPLES:
            _require_sample(self, label)
            detail = _detail(_resolve(label))
            self.assertIn("regions_with_center_total", detail,
                          "%s：详情里没有「有几条区域拿得到中心」这个数 —— 断链时无法自证"
                          % label)
            self.assertIsInstance(detail["regions_with_center_total"], int)
            self.assertEqual(detail["regions_with_center_total"],
                             len([region for region in _regions(_real_parts(label))
                                  if region.get("center")]),
                             "%s：这个数与区域记录里的 center 数不一致" % label)

    def test_b3_zero_binding_with_rows_must_be_reported_as_a_broken_link(self):
        for label in SAMPLES:
            _require_sample(self, label)
            out = _resolve(label)
            detail = _detail(out)
            rows = _rows(out)
            linked = int(detail.get("bound_total") or 0) + int(detail.get("partial_total") or 0)
            self.assertGreater(len(rows), 0, "%s：业务部件一行都没有，这条测不了" % label)
            if linked:
                continue                       # 已经绑上了（修好之后走这里）
            link = detail.get("outline_link") or {}
            self.assertEqual(OUTLINE_LINK_BROKEN, str(link.get("code") or ""),
                             "%s：%d 行业务部件一件都没绑上图，却既没有断链码也没有别的说明"
                             % (label, len(rows)))
            self.assertEqual(len(rows), int(link.get("business_part_total") or 0),
                             "%s：断链码没带上业务部件行数" % label)
            self.assertEqual(int(detail.get("geometry_component_total") or 0),
                             int(link.get("region_total") or 0),
                             "%s：断链码没带上区域总数" % label)
            self.assertEqual(int(detail.get("regions_with_center_total") or 0),
                             int(link.get("regions_with_center_total") or 0),
                             "%s：断链码没带上「有中心的区域数」（这一条就是真因所在）" % label)

    def test_b4_the_code_appears_if_and_only_if_the_link_is_broken(self):
        for label in SAMPLES:
            _require_sample(self, label)
            out = _resolve(label)
            detail = _detail(out)
            rows = _rows(out)
            linked = int(detail.get("bound_total") or 0) + int(detail.get("partial_total") or 0)
            broken = bool(rows) and linked == 0
            code = str((detail.get("outline_link") or {}).get("code") or "")
            if broken:
                self.assertEqual(OUTLINE_LINK_BROKEN, code)
            else:
                self.assertEqual("", code,
                                 "%s：绑定是通的，却还在报断链码" % label)

    def test_b5_all_rows_at_no_outline_evidence_is_exactly_the_broken_case(self):
        for label in SAMPLES:
            _require_sample(self, label)
            out = _resolve(label)
            rows = _rows(out)
            module = _resolver()
            all_missing = bool(rows) and all(
                list(row.get("reasons") or []) == [module.REASON_NO_OUTLINE] for row in rows)
            if not all_missing:
                continue
            code = str((_detail(out).get("outline_link") or {}).get("code") or "")
            self.assertEqual(OUTLINE_LINK_BROKEN, code,
                             "%s：%d 行**全部**是 no_outline_evidence —— 这不是「个别件没找到"
                             "轮廓」，是绑定链断了，必须按断链报" % (label, len(rows)))


# --------------------------------------------------------------------------- #
# C 组：真样本端到端（修好后绑定必须真的出来）
# --------------------------------------------------------------------------- #
class CRealSamplesMustBind(unittest.TestCase):
    def _check(self, label: str):
        _require_sample(self, label)
        out = _resolve(label)
        derived = _derived_rows(out)
        self.assertGreaterEqual(len(derived), MIN_DERIVED[label],
                                "%s：绑上图的业务部件只有 %d 件（门槛 %d）—— 轮廓矩形没接到区域上"
                                % (label, len(derived), MIN_DERIVED[label]))
        for row in derived:
            code = str(row.get("business_part_code") or "")
            self.assertIsNotNone(row.get("length_mm"), "%s 没有件长" % code)
            self.assertIsNotNone(row.get("width_mm"), "%s 没有件宽" % code)
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            ref = evidence.get("drawing_ref") if isinstance(evidence.get("drawing_ref"), dict) else {}
            self.assertNotEqual("none", str(ref.get("kind") or "none"),
                                "%s：绑上了却没有图纸证据" % code)
            self.assertTrue(list(ref.get("component_ids") or []) or list(ref.get("bbox") or []),
                            "%s：绑上了却既没有分量也没有轮廓矩形" % code)

    def test_c1_wine_box_binds(self):
        self._check("酒盒")

    def test_c2_round_box_binds(self):
        self._check("圆盘盒")


# --------------------------------------------------------------------------- #
# D 组：不回归（几何零件提取本身、区域口径、确定性）
# --------------------------------------------------------------------------- #
class DNoRegression(unittest.TestCase):
    def test_d1_geometry_parts_are_unchanged(self):
        for label in SAMPLES:
            _require_sample(self, label)
            doc = _real_parts(label)
            self.assertEqual(GEOMETRY_TOTAL[label], len(doc.get("parts") or []),
                             "%s：几何零件件数变了（本批不该动提取口径）" % label)
            stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
            self.assertAlmostEqual(CLOSED_RATIO[label], float(stats.get("closed_ratio") or 0.0),
                                   places=3,
                                   msg="%s：闭合比例变了（本批不该动提取口径）" % label)

    def test_d2_extract_is_deterministic(self):
        for label in SAMPLES:
            _require_sample(self, label)
            first = json.dumps(_real_parts(label), ensure_ascii=False, sort_keys=True,
                               default=str)
            second = json.dumps(_parts_module().extract(_real_ir(label)), ensure_ascii=False,
                                sort_keys=True, default=str)
            self.assertEqual(first, second, "%s：同一份 IR 两次提取不同" % label)

    def test_d3_reading_regions_does_not_mutate_the_document(self):
        doc = _fixture_doc()
        before = copy.deepcopy(doc)
        _regions(doc)
        self.assertEqual(json.dumps(before, ensure_ascii=False, sort_keys=True),
                         json.dumps(doc, ensure_ascii=False, sort_keys=True),
                         "区域构建不许改零件文档（它是共享事实）")


if __name__ == "__main__":
    unittest.main()
