# -*- coding: utf-8 -*-
"""红测：防止用酒盒28件答案过拟合生产解析器，并守住下游可信门禁。

Spec：docs/specs/packaging-dwg-generalization-and-downstream-trust.md
金标只在测试侧评测；禁止为转绿修改本文件。
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

from tech_app.backend.services import cad_ir                       # noqa: E402
from tech_app.backend.services import packaging_business_part_resolver as resolver  # noqa: E402
from tech_app.backend.services import packaging_parts              # noqa: E402

SAMPLE = ROOT / "tech_app/data/cad-ir-realsample/conversions/47c39dc1ab6738fc48c8/converted.dxf"
GOLD = ROOT / "tests/fixtures/gold/packaging_authority_parts.json"
SHA = "0991c8b0a9646d1fea6571d2ae6155923351c05ca2aeeb6ed544ef19df93f3e0"
PRODUCTION = ROOT / "tech_app"


def _ir():
    return cad_ir.parse_dxf(SAMPLE.read_bytes(), filename="酒盒.dxf",
                            source={"source_sha256": SHA, "original_filename": "酒盒.dwg"})


def _rows(out):
    return [row for row in out.get("authority_rows", []) if isinstance(row, dict)]


def _norm(value):
    return resolver.normalize_part_label(str(value or ""))


def _semantic(out):
    """忽略顺序生成的编码，只比较业务语义。"""
    rows = []
    for row in _rows(out):
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        # 平面件旋转90°不改变业务语义，长/宽在此按无向两轴比较。
        axes = sorted((round(float(row.get("length_mm") or 0), 3),
                       round(float(row.get("width_mm") or 0), 3)))
        rows.append((_norm(row.get("name")), row.get("truth_state"),
                     axes[0], axes[1],
                     tuple(evidence.get("kinds") or ()),
                     str(row.get("size_quality") or evidence.get("size_quality") or "")))
    return sorted(rows)


class GeneralizationAndTrustRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SAMPLE.exists():
            raise unittest.SkipTest("缺酒盒真样本")
        cls.ir = _ir()
        cls.out = resolver.resolve_business_parts("generalization-red", cls.ir, None)
        cls.rows = _rows(cls.out)
        payload = json.loads(GOLD.read_text(encoding="utf-8"))
        cls.gold = [dict(row) for row in payload["sources"][0]["parts"]]
        cls.gold_by_name = {_norm(row.get("name")): row for row in cls.gold}

    def test_a1_production_has_no_sample_fingerprint_or_test_fixture_import(self):
        bad = []
        forbidden = (SHA, "47c39dc1ab6738fc48c8", "tests/fixtures/gold")
        for path in PRODUCTION.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = [token for token in forbidden if token in text]
            if hits:
                bad.append((str(path.relative_to(ROOT)), hits))
        self.assertEqual([], bad, "生产代码不得识别样本指纹或导入测试金标：%r" % bad)

    def test_a2_gold_injection_has_zero_effect(self):
        plain = json.dumps(self.out, ensure_ascii=False, sort_keys=True)
        gold = json.loads(GOLD.read_text(encoding="utf-8"))
        injected = resolver.resolve_business_parts(
            "generalization-red", self.ir, None, attachments=gold,
            kb={"parts": self.gold}, seed_path=str(GOLD))
        self.assertEqual(plain, json.dumps(injected, ensure_ascii=False, sort_keys=True),
                         "金标只能评测，不能影响生产输出")

    def test_a3_entity_iteration_order_does_not_change_semantics(self):
        shuffled = copy.deepcopy(self.ir)
        for key in ("entities", "texts", "dimensions", "layers"):
            if isinstance(shuffled.get(key), list):
                shuffled[key] = list(reversed(shuffled[key]))
        if isinstance(shuffled.get("evidence"), dict):
            shuffled["evidence"] = dict(reversed(list(shuffled["evidence"].items())))
        changed = resolver.resolve_business_parts("generalization-red", shuffled, None)
        self.assertEqual(_semantic(self.out), _semantic(changed),
                         "实体仅换遍历顺序，业务语义不应漂移")

    def test_a4_text_evidence_ablation_cannot_return_identical_answer(self):
        ablated = copy.deepcopy(self.ir)
        ablated["texts"] = []
        ablated["entities"] = [row for row in ablated.get("entities", [])
                               if str(row.get("type") or "").upper() not in ("TEXT", "MTEXT")]
        changed = resolver.resolve_business_parts("generalization-red", ablated, None)
        self.assertNotEqual(_semantic(self.out), _semantic(changed),
                            "移除名称文字后仍逐字命中，说明结果可能在背答案")

    def test_b1_gold_is_metric_not_exact_runtime_count(self):
        names = {_norm(row.get("name")) for row in self.rows}
        gold = set(self.gold_by_name)
        recall = len(names & gold) / len(gold)
        precision = len(names & gold) / len(names) if names else 0
        self.assertGreaterEqual(recall, 22 / 28, "已知样本最低recall回归线")
        self.assertGreaterEqual(precision, 0.85, "已知样本最低precision回归线")
        # 有意不写 len(rows)==28：这是防过拟合测试的核心。

    def test_b2_every_result_declares_truth_state(self):
        allowed = {"observed", "inferred", "pending_confirmation"}
        bad = [(row.get("name"), row.get("truth_state")) for row in self.rows
               if row.get("truth_state") not in allowed]
        self.assertEqual([], bad, "每件必须声明直接观测、规则推断或待确认")

    def test_b3_confirmed_dimensions_are_actually_correct(self):
        wrong = []
        for row in self.rows:
            expected = self.gold_by_name.get(_norm(row.get("name")))
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            quality = str(row.get("size_quality") or evidence.get("size_quality") or "")
            if not expected or quality not in ("confirmed", "unfolded"):
                continue
            got = sorted((float(row["length_mm"]), float(row["width_mm"])))
            want = sorted((float(expected["length_mm"]), float(expected["width_mm"])))
            if max(abs(got[0] - want[0]), abs(got[1] - want[1])) > 1.0:
                wrong.append((row.get("name"), got, want))
        self.assertEqual([], wrong, "错误尺寸必须降级，不能标成确认：%r" % wrong)

    def test_c1_bbox_size_is_rejected_by_process(self):
        row = {"business_part_code": "DWG-BP-X", "name": "测试面纸",
               "authority": {"length_mm": 3927.7, "width_mm": 967.9,
                             "material_text": "225G铜版纸", "size_quality": "bbox_only"}}
        got = packaging_parts.business_process_inputs(row)
        self.assertFalse(got.get("ok"), "bbox猜测尺寸不得进入工艺推荐")
        self.assertEqual("PACKAGING_BUSINESS_PART_SIZE_UNCONFIRMED", got.get("code"))

    def test_c2_face_paper_gsm_cannot_fill_missing_greyboard_material(self):
        row = {"business_part_code": "DWG-BP-G", "name": "外盒里层灰板",
               "authority": {"length_mm": 217.1, "width_mm": 482.9,
                             "size_quality": "confirmed", "material_text": ""}}
        got = packaging_parts.business_cost_inputs(
            row, requirement={"data": {"face_paper_gsm": 225}}, quantity=1000)
        self.assertFalse(got.get("ok"), "灰板不能拿面纸克重兜底")
        self.assertEqual("PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN", got.get("code"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
