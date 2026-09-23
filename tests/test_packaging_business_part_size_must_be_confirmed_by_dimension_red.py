# -*- coding: utf-8 -*-
"""红测：业务部件的"尺寸"必须分得开「有独立证据」与「只有几何包络的猜测」（红）。

Spec：`docs/specs/packaging-business-part-size-must-be-confirmed-by-dimension.md`

现状缺口（2026-09-23 本机隔离 `DATA_DIR` 真样本只读复跑，不是推断）：把 ## 461 的断链先摆平
（夹具把矩形提到行顶层）后，两份真样本的绑定都出来了，但**尺寸的两档混成一档**：

    酒盒  ：26 行全部 derived，26 件有尺寸；其中**只有 12 件**的尺寸有标注证据（同形尺寸标注），
            另外 14 件只是分量/环的**包络猜测** —— 行上 `reasons` 是空的，
            没有 `evidence.size_quality`，detail 只有一笔 parts_with_size_total = 26
    圆盘盒：39 件有尺寸；确认 8 / 猜测 31，同样一个数说不开

也就是说：`evidence.size_confirmed` 已经算出来了，却没有任何**读得出来**的出口 ——
页面、BOM、成本都只能看到"这件有尺寸"，看不出"这个数是从图纸上量出来的"还是"拿包络估的"。

夹具说明：本批测的是「确认与否」，所以夹具先把 ## 461 的断链替掉（`outline.bbox` → 行顶层
`bbox`），让绑定先跑通 —— 两条缺口各测各的，不许互相掩盖。

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

#: 未确认尺寸的稳定原因码（Spec §1.3）。
REASON_SIZE_UNCONFIRMED = "outline_size_unconfirmed"

#: 真样本门槛（Spec §1.4；本机实测 酒盒 12/14、圆盘盒 8/31，留余量）。
MIN_CONFIRMED = {"酒盒": 10, "圆盘盒": 6}
MIN_UNCONFIRMED = {"酒盒": 10, "圆盘盒": 25}

#: 护栏：本批不改"绑定/已定位"的口径（Spec §1.5）。
#:
#: 圆盘盒 39 → 38（`## 475`）：39 里那多出来的一件是**件名里 MTEXT 格式码造出来的假件** ——
#: `ent:model:8C665` / `8D60F` 是同一件的原图 + 镜像（件名与材料逐字相同，只是 MTEXT run 被切在
#: `1` 与 `0PC` 之间）。剥码后它们归并回一件（66 → 65 行），与图纸上其它镜像对的处理一致。
#: 口径变化由 `docs/specs/packaging-part-name-mtext-codes.md` §5 授权；本行是那次授权下**唯一**
#: 允许改动的数字，其它组的期望值一字未动。
DERIVED_TOTAL = {"酒盒": 26, "圆盘盒": 38}
SIZED_TOTAL = {"酒盒": 26, "圆盘盒": 38}


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


def _pinned_parts(label: str):
    """真零件文档 + 把 `outline.bbox` 提到行顶层（替 ## 461 的断链，夹具专用）。"""
    if label in _DOC_CACHE:
        return _DOC_CACHE[label]
    doc = _parts_module().extract(_real_ir(label))
    pinned = copy.deepcopy(doc)
    for row in (pinned.get("parts") or []):
        outline = row.get("outline") if isinstance(row.get("outline"), dict) else {}
        if outline.get("bbox"):
            row["bbox"] = list(outline["bbox"])
    _DOC_CACHE[label] = pinned
    return pinned


def _require_sample(test: unittest.TestCase, label: str):
    conversion_id, _ = SAMPLES[label]
    if not (SAMPLE_ROOT / conversion_id / "converted.dxf").exists():
        test.skipTest("缺少真样本 DXF（%s）" % conversion_id)


def _resolve(label: str):
    if label in _OUT_CACHE:
        return _OUT_CACHE[label]
    out = _resolver().resolve_business_parts("red-test", _real_ir(label), _pinned_parts(label))
    _OUT_CACHE[label] = out
    return out


def _rows(out) -> list:
    return [row for row in (out.get("authority_rows") or []) if isinstance(row, dict)]


def _detail(out) -> dict:
    return out.get("detail") if isinstance(out.get("detail"), dict) else {}


def _evidence(row) -> dict:
    return row.get("evidence") if isinstance(row.get("evidence"), dict) else {}


def _has_size(row) -> bool:
    return row.get("length_mm") is not None and row.get("width_mm") is not None


def _sized_rows(out) -> list:
    return [row for row in _rows(out) if _has_size(row)]


def _confirmed_rows(out) -> list:
    return [row for row in _sized_rows(out) if _evidence(row).get("size_confirmed") is True]


def _unconfirmed_rows(out) -> list:
    return [row for row in _sized_rows(out) if _evidence(row).get("size_confirmed") is not True]


# --------------------------------------------------------------------------- #
# A 组：行级必须有机器可读的质量档，且与 `size_confirmed` 一致
# --------------------------------------------------------------------------- #
class ARowCarriesSizeQuality(unittest.TestCase):
    def test_a1_every_row_carries_a_size_quality_from_the_closed_set(self):
        qualities = set(_parts_module().SIZE_QUALITIES)
        for label in SAMPLES:
            _require_sample(self, label)
            for row in _rows(_resolve(label)):
                code = str(row.get("business_part_code") or "")
                value = _evidence(row).get("size_quality")
                self.assertIsNotNone(
                    value,
                    "%s %s：行上没有 `evidence.size_quality` —— 页面 / BOM / 成本只能看到"
                    "「有尺寸」，看不出这个数是量出来的还是拿包络估的" % (label, code))
                self.assertIn(value, qualities,
                              "%s %s：质量档 %r 不在闭集 %s 里" % (label, code, value,
                                                              sorted(qualities)))

    def test_a2_confirmed_sizes_are_a_different_bucket_than_guesses(self):
        for label in SAMPLES:
            _require_sample(self, label)
            for row in _sized_rows(_resolve(label)):
                code = str(row.get("business_part_code") or "")
                quality = _evidence(row).get("size_quality")
                if _evidence(row).get("size_confirmed") is True:
                    self.assertEqual("unfolded", quality,
                                     "%s %s：尺寸有标注证据，却被归进包围盒档（与猜测同形）"
                                     % (label, code))
                else:
                    self.assertEqual("bbox_only", quality,
                                     "%s %s：尺寸只是几何包络的猜测，不许被当成有证据的一档"
                                     % (label, code))

    def test_a3_rows_without_any_size_are_bbox_only(self):
        for label in SAMPLES:
            _require_sample(self, label)
            for row in _rows(_resolve(label)):
                if _has_size(row):
                    continue
                self.assertEqual("bbox_only", _evidence(row).get("size_quality"),
                                 "%s %s：没有尺寸的行按「缺失一律包围盒」的口径必须是 "
                                 "bbox_only" % (label, row.get("business_part_code")))


# --------------------------------------------------------------------------- #
# B 组：两笔账必须分开（确认 / 未确认）
# --------------------------------------------------------------------------- #
class BTwoBooksMustBeSeparate(unittest.TestCase):
    def test_b1_detail_gives_both_totals(self):
        for label in SAMPLES:
            _require_sample(self, label)
            detail = _detail(_resolve(label))
            for key in ("size_confirmed_total", "size_unconfirmed_total"):
                self.assertIn(key, detail,
                              "%s：详情里没有 %s —— 「有尺寸」这一笔账把确认的与猜的合在一起，"
                              "审阅者分不开" % (label, key))
                self.assertIsInstance(detail[key], int, "%s：%s 必须是整数" % (label, key))

    def test_b2_the_two_totals_add_up_to_the_size_total(self):
        for label in SAMPLES:
            _require_sample(self, label)
            out = _resolve(label)
            detail = _detail(out)
            confirmed = int(detail.get("size_confirmed_total") or 0)
            unconfirmed = int(detail.get("size_unconfirmed_total") or 0)
            self.assertEqual(int(detail.get("parts_with_size_total") or 0),
                             confirmed + unconfirmed,
                             "%s：两笔账相加必须等于「有尺寸的件数」" % label)
            self.assertEqual(len(_confirmed_rows(out)), confirmed,
                             "%s：size_confirmed_total 与行上的 size_confirmed 对不上" % label)
            self.assertEqual(len(_unconfirmed_rows(out)), unconfirmed,
                             "%s：size_unconfirmed_total 与行上的 size_confirmed 对不上" % label)


# --------------------------------------------------------------------------- #
# C 组：未确认的行必须自己说出来
# --------------------------------------------------------------------------- #
class CUnconfirmedRowsMustSaySo(unittest.TestCase):
    def test_c1_the_reason_code_has_one_constant(self):
        self.assertEqual(REASON_SIZE_UNCONFIRMED,
                         getattr(_resolver(), "REASON_SIZE_UNCONFIRMED", ""),
                         "未确认尺寸的原因码必须是模块级常量")

    def test_c2_only_the_unconfirmed_rows_carry_the_code(self):
        for label in SAMPLES:
            _require_sample(self, label)
            out = _resolve(label)
            for row in _unconfirmed_rows(out):
                self.assertIn(REASON_SIZE_UNCONFIRMED, list(row.get("reasons") or []),
                              "%s %s：尺寸是猜的，行上却什么都没说"
                              % (label, row.get("business_part_code")))
            for row in _confirmed_rows(out):
                self.assertNotIn(REASON_SIZE_UNCONFIRMED, list(row.get("reasons") or []),
                                 "%s %s：尺寸有标注证据，不该背「未确认」这个码"
                                 % (label, row.get("business_part_code")))

    def test_c3_the_breakdown_counts_the_code(self):
        for label in SAMPLES:
            _require_sample(self, label)
            out = _resolve(label)
            breakdown = _detail(out).get("reasons_breakdown")
            self.assertIsInstance(breakdown, dict, "%s：没有 reasons_breakdown" % label)
            expected = len(_unconfirmed_rows(out))
            self.assertGreater(expected, 0, "%s：夹具里应当有「尺寸未确认」的件" % label)
            self.assertEqual(expected, int(breakdown.get(REASON_SIZE_UNCONFIRMED) or 0),
                             "%s：reason 明细里数不到「尺寸未确认」的件（真值 %d）—— "
                             "行上说了，账上却看不见" % (label, expected))


# --------------------------------------------------------------------------- #
# D 组：真样本门槛（确认覆盖了多少件是事实，必须披露）
# --------------------------------------------------------------------------- #
class DRealSampleThresholds(unittest.TestCase):
    def _check(self, label: str):
        _require_sample(self, label)
        out = _resolve(label)
        confirmed = len(_confirmed_rows(out))
        unconfirmed = len(_unconfirmed_rows(out))
        self.assertGreaterEqual(confirmed, MIN_CONFIRMED[label],
                                "%s：有标注证据的尺寸只有 %d 件（门槛 %d）—— 确认这一档没落出来"
                                % (label, confirmed, MIN_CONFIRMED[label]))
        self.assertGreaterEqual(unconfirmed, MIN_UNCONFIRMED[label],
                                "%s：未确认的尺寸只有 %d 件（门槛 %d）—— 猜测这一档没落出来"
                                % (label, unconfirmed, MIN_UNCONFIRMED[label]))

    def test_d1_wine_box(self):
        self._check("酒盒")

    def test_d2_round_box(self):
        self._check("圆盘盒")


# --------------------------------------------------------------------------- #
# E 组：护栏（本批只披露，不许动绑定与既有映射）
# --------------------------------------------------------------------------- #
class EGuards(unittest.TestCase):
    def test_e1_binding_and_size_counts_are_unchanged(self):
        for label in SAMPLES:
            _require_sample(self, label)
            out = _resolve(label)
            derived = [row for row in _rows(out) if str(row.get("status") or "") == "derived"]
            self.assertEqual(DERIVED_TOTAL[label], len(derived),
                             "%s：derived 件数变了（本批不许改「已定位」的口径 —— 那是另一份 "
                             "Spec / 需要签字的选项）" % label)
            self.assertEqual(SIZED_TOTAL[label], len(_sized_rows(out)),
                             "%s：有尺寸的件数变了" % label)

    def test_e2_parts_side_quality_mapping_is_untouched(self):
        module = _parts_module()
        self.assertEqual(("unfolded", "bbox_only"), tuple(module.SIZE_QUALITIES),
                         "尺寸质量档闭集不许被扩")
        self.assertEqual("unfolded", module.size_quality_of("closed_outline"))
        self.assertEqual("unfolded", module.size_quality_of("dwg_outline"))
        for source in ("component_bbox", "geometry_region", "none", ""):
            self.assertEqual("bbox_only", module.size_quality_of(source),
                             "既有口径：%r 一律按包围盒" % source)

    def test_e3_confirmed_rows_keep_the_dimension_evidence_kind(self):
        for label in SAMPLES:
            _require_sample(self, label)
            out = _resolve(label)
            for row in _confirmed_rows(out):
                kinds = list(_evidence(row).get("kinds") or [])
                self.assertIn("size_dimension", kinds,
                              "%s %s：确认了尺寸却没有 size_dimension 这条证据"
                              % (label, row.get("business_part_code")))
            for row in _unconfirmed_rows(out):
                kinds = list(_evidence(row).get("kinds") or [])
                self.assertNotIn("size_dimension", kinds,
                                 "%s %s：尺寸没标注证据，却挂着 size_dimension"
                                 % (label, row.get("business_part_code")))

    def test_e4_resolution_is_deterministic(self):
        for label in SAMPLES:
            _require_sample(self, label)
            first = _resolve(label)
            second = _resolver().resolve_business_parts("red-test", _real_ir(label),
                                                        _pinned_parts(label))
            self.assertEqual(
                json.dumps([_rows(first), _detail(first)], ensure_ascii=False, sort_keys=True,
                           default=str),
                json.dumps([_rows(second), _detail(second)], ensure_ascii=False, sort_keys=True,
                           default=str),
                "%s：同一份输入两次解析不同" % label)

    def test_e5_resolution_does_not_mutate_the_parts_document(self):
        label = "酒盒"
        _require_sample(self, label)
        doc = copy.deepcopy(_pinned_parts(label))
        before = json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str)
        _resolver().resolve_business_parts("red-test", _real_ir(label), doc)
        self.assertEqual(before, json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str),
                         "解析不许改零件文档")


if __name__ == "__main__":
    unittest.main()
