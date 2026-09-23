# -*- coding: utf-8 -*-
"""红测：酒盒真 DWG 拆件正确后，可信事实才能进入工艺推荐和成本测算。

Spec：docs/specs/packaging-wine-dwg-parts-and-downstream-truth.md

金标只在本测试中对答案；生产解析器仍只吃 CAD IR。禁止为转绿修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import cad_ir                       # noqa: E402
from tech_app.backend.services import packaging_business_part_resolver as resolver  # noqa: E402
from tech_app.backend.services import packaging_parts              # noqa: E402

SAMPLE = ROOT / "tech_app/data/cad-ir-realsample/conversions/47c39dc1ab6738fc48c8/converted.dxf"
GOLD = ROOT / "tests/fixtures/gold/packaging_authority_parts.json"
SHA = "0991c8b0a9646d1fea6571d2ae6155923351c05ca2aeeb6ed544ef19df93f3e0"


def _norm(value):
    return resolver.normalize_part_label(str(value or ""))


def _gold_rows():
    payload = json.loads(GOLD.read_text(encoding="utf-8"))
    return [dict(row) for row in payload["sources"][0]["parts"]]


def _out():
    ir = cad_ir.parse_dxf(SAMPLE.read_bytes(), filename="酒盒.dxf",
                          source={"source_sha256": SHA, "original_filename": "酒盒.dwg"})
    return resolver.resolve_business_parts("wine-red", ir, None)


class WineDrawingTruthRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SAMPLE.exists():
            raise unittest.SkipTest("缺酒盒真样本")
        cls.result = _out()
        cls.rows = [row for row in cls.result.get("authority_rows", []) if isinstance(row, dict)]
        cls.by_name = {_norm(row.get("name")): row for row in cls.rows}
        cls.gold = _gold_rows()
        cls.gold_by_name = {_norm(row.get("name")): row for row in cls.gold}
        authority = cls.result.get("authority") if isinstance(cls.result.get("authority"), dict) else {}
        cls.doc = packaging_parts.business_parts_document(
            authority, {"parts": []}, bindings=cls.result.get("match"))

    def test_a1_leaf_name_set_is_the_expected_28_without_parent_extras(self):
        self.assertEqual(
            set(self.gold_by_name), set(self.by_name),
            "真样本应得到28个业务叶子件；缺=%s，多=%s" % (
                sorted(set(self.gold_by_name) - set(self.by_name)),
                sorted(set(self.by_name) - set(self.gold_by_name))))
        self.assertEqual(28, len(self.rows), "不能用父组/别名凑数量")

    def test_a2_each_row_declares_observed_or_inferred_truth_state(self):
        allowed = {"observed", "inferred", "pending_confirmation"}
        bad = [(row.get("name"), row.get("truth_state")) for row in self.rows
               if row.get("truth_state") not in allowed]
        self.assertEqual([], bad, "每件必须区分直接观测、规则推断、待确认")

    def test_b1_confirmed_dimensions_are_correct_not_merely_present(self):
        wrong = []
        checked = 0
        for name, row in self.by_name.items():
            expected = self.gold_by_name.get(name)
            if not expected:
                continue
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            quality = str(row.get("size_quality") or evidence.get("size_quality") or "")
            if quality not in ("confirmed", "unfolded"):
                continue
            got = sorted((float(row["length_mm"]), float(row["width_mm"])))
            want = sorted((float(expected["length_mm"]), float(expected["width_mm"])))
            checked += 1
            if max(abs(got[0] - want[0]), abs(got[1] - want[1])) > 1.0:
                wrong.append((row.get("name"), got, want, quality))
        self.assertGreaterEqual(checked, 1, "至少应有一件得到确认尺寸")
        self.assertEqual([], wrong, "标成确认的尺寸必须经得住金标复核；错误项=%r" % wrong)

    def test_b2_gross_bbox_is_not_downstream_ready(self):
        row = self.by_name.get(_norm("右盖面纸"))
        self.assertIsNotNone(row)
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        self.assertNotIn(str(row.get("size_quality") or evidence.get("size_quality")),
                         ("confirmed", "unfolded"),
                         "约3928×968的总包围盒不能冒充右盖面纸确认尺寸")

    def test_c1_unconfirmed_size_is_rejected_by_process(self):
        row = {"business_part_code": "DWG-BP-X", "name": "测试面纸",
               "authority": {"length_mm": 3927.7, "width_mm": 967.9,
                             "material_text": "225G铜版纸", "size_quality": "bbox_only"}}
        got = packaging_parts.business_process_inputs(row)
        self.assertFalse(got.get("ok"), "bbox猜测尺寸不得进入工艺推荐")
        self.assertEqual("PACKAGING_BUSINESS_PART_SIZE_UNCONFIRMED", got.get("code"))

    def test_c2_missing_greyboard_material_cannot_use_face_paper_gsm(self):
        row = {"business_part_code": "DWG-BP-GREY", "name": "左盖外盒里层灰板1",
               "authority": {"length_mm": 217.1, "width_mm": 482.9,
                             "size_quality": "confirmed", "material_text": ""}}
        got = packaging_parts.business_cost_inputs(
            row, requirement={"data": {"face_paper_gsm": 225}}, quantity=1000)
        self.assertFalse(got.get("ok"), "灰板缺材料不能拿整盒面纸225g兜底")
        self.assertEqual("PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN", got.get("code"))

    def test_c3_real_document_does_not_claim_all_parts_cost_ready(self):
        rows = [row for row in self.doc.get("business_parts", []) if isinstance(row, dict)]
        results = [packaging_parts.business_cost_inputs(
            row, requirement={"data": {"face_paper_gsm": 225}}, quantity=1000) for row in rows]
        suspect = [row.get("name") for row, result in zip(rows, results)
                   if result.get("ok") and not str((row.get("authority") or {}).get("material_text") or "").strip()
                   and any(word in str(row.get("name") or "") for word in ("灰板", "衬板", "EVA", "磁铁"))]
        self.assertEqual([], suspect, "不同材料类别不得被face_paper_gsm错误放行：%s" % suspect)


if __name__ == "__main__":
    unittest.main(verbosity=2)
