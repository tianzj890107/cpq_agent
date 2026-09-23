# -*- coding: utf-8 -*-
"""红测：业务部件必须由 DWG 的**全部证据**推出来，而不是只从文字里抄件名（Spec §2/§3）。

Spec：`docs/specs/packaging-business-parts-must-come-from-all-drawing-evidence.md`

现状缺口（2026-09-23 本机真样本只读复跑，不是推断）：`## 456` 落地的 `_derived_rows()` 是
「一条名称锚点 = 一件」，酒盒样本实测

    27 行 / 对 28 件金标 recall 19/28、precision 19/27 / 只有 5 件带尺寸 / 22 件 unbound
    漏件 9：内卡、底板、底板面纸、底托灰板、磁铁、左/右盖外盒里层灰板1、左/右盖外盒里层灰板2
    多件 8：刀、`底板:2.5MM灰板`、`底板面纸:225G铜版底PET光银`、左/右盒外盒里层灰板、左/右盒盒背灰板、顶托灰板

几何、尺寸、引线、图块、图层、空间关系、重复/镜像都没进"推件"这一步；父名（`左盒外盒里层灰板`）
被直接当成一件。金标只在测试侧做对答案（`## 453` 的成果保留，本批不许回退）。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLD_STANDARD = ROOT / "tests/fixtures/gold/packaging_authority_parts.json"
SAMPLE_ROOT = ROOT / "tech_app/data/cad-ir-realsample/conversions"

#: 真样本：conversion_id 与 DWG 的 sha-256（取自转换 manifest）。
WINE_SAMPLE = ("47c39dc1ab6738fc48c8",
               "0991c8b0a9646d1fea6571d2ae6155923351c05ca2aeeb6ed544ef19df93f3e0")

#: 证据类别闭集（Spec §3.1 第 1 条）。
EVIDENCE_KINDS = ("text_anchor", "geometry_region", "size_dimension", "leader_callout",
                  "block_attribute", "layer_role", "spatial_relation", "repetition_mirror")

#: 非文字证据（Spec §3.1 第 3 条）：清单里必须至少有一条来自它们。
GEOMETRIC_KINDS = ("geometry_region", "size_dimension", "leader_callout", "block_attribute",
                   "layer_role", "spatial_relation", "repetition_mirror")

#: 真样本验收门槛（Spec §3.2 第 1 条）：金标 28 件的 recall / precision。
GOLD_TOTAL = 28
MIN_RECALL = 22
MIN_PRECISION = 0.85

#: 父名拆件（Spec §3.3 第 2 条）：金标里这四件，图上只有两条含该词组的名称锚点。
GROUP_SPLIT_NAMES = ("左盖外盒里层灰板1", "左盖外盒里层灰板2",
                     "右盖外盒里层灰板1", "右盖外盒里层灰板2")


def _resolver():
    from tech_app.backend.services import packaging_business_part_resolver as module
    return module


_IR_CACHE: dict = {}
_OUT_CACHE: dict = {}


def _real_ir():
    if "wine" in _IR_CACHE:
        return _IR_CACHE["wine"]
    from tech_app.backend.services import cad_ir
    conversion_id, drawing_sha = WINE_SAMPLE
    path = SAMPLE_ROOT / conversion_id / "converted.dxf"
    ir = cad_ir.parse_dxf(path.read_bytes(), filename="酒盒.dxf",
                          source={"source_sha256": drawing_sha, "original_filename": "酒盒.dwg"})
    _IR_CACHE["wine"] = ir
    return ir


def _require_sample(test: unittest.TestCase):
    conversion_id, _ = WINE_SAMPLE
    if not (SAMPLE_ROOT / conversion_id / "converted.dxf").exists():
        test.skipTest("缺少真样本 DXF（%s）" % conversion_id)


def _derive(**kwargs):
    key = tuple(sorted((str(name), json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
                       for name, value in kwargs.items()))
    if key in _OUT_CACHE:
        return _OUT_CACHE[key]
    out = _resolver().resolve_business_parts("red-test", _real_ir(), None, **kwargs)
    _OUT_CACHE[key] = out
    return out


def _rows(out) -> list:
    return [row for row in (out.get("authority_rows") or []) if isinstance(row, dict)]


def _detail(out) -> dict:
    return out.get("detail") if isinstance(out.get("detail"), dict) else {}


def _gold_names() -> list:
    if not GOLD_STANDARD.exists():
        return []
    gold = json.loads(GOLD_STANDARD.read_text(encoding="utf-8"))
    return [row.get("name") for row in ((gold.get("sources") or [{}])[0].get("parts") or [])]


def _kinds(row) -> list:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    kinds = evidence.get("kinds")
    return [str(kind) for kind in kinds] if isinstance(kinds, list) else []


def _answer_against_gold(out) -> dict:
    """测试侧对答案：金标只在这里出现（运行时读不到）。"""
    module = _resolver()
    names = [str(row.get("name") or "") for row in _rows(out)]
    normalized = {module.normalize_part_label(name) for name in names if name}
    gold = _gold_names()
    matched = [name for name in gold if module.normalize_part_label(name) in normalized]
    missing = [name for name in gold if module.normalize_part_label(name) not in normalized]
    gold_norm = {module.normalize_part_label(name) for name in gold}
    extra = [name for name in names if module.normalize_part_label(name) not in gold_norm]
    return {"matched": matched, "missing": missing, "extra": extra, "rows": names,
            "recall": len(matched), "precision": (len(matched) / len(names)) if names else 0.0}


class AllDrawingEvidenceRed(unittest.TestCase):
    """A 组：证据面（Spec §3.1）。"""

    def test_a1_every_row_declares_its_evidence_kinds(self):
        _require_sample(self)
        bad = []
        for row in _rows(_derive()):
            kinds = _kinds(row)
            if not kinds:
                bad.append("%s：没有 evidence.kinds" % row.get("business_part_code"))
                continue
            unknown = [kind for kind in kinds if kind not in EVIDENCE_KINDS]
            if unknown:
                bad.append("%s：证据类别不在闭集里 %r" % (row.get("business_part_code"), unknown))
            if len(set(kinds)) != len(kinds):
                bad.append("%s：证据类别重复 %r" % (row.get("business_part_code"), kinds))
        self.assertEqual([], bad,
                         "每一行都必须说清「这一件用了哪些证据」（闭集 %s）：\n%s" % (EVIDENCE_KINDS, "\n".join(bad)))

    def test_a2_at_least_one_row_comes_from_non_text_evidence(self):
        _require_sample(self)
        rows = _rows(_derive())
        hits = [row.get("business_part_code") for row in rows
                if set(_kinds(row)) & set(GEOMETRIC_KINDS)]
        self.assertTrue(hits,
                        "清单里必须出现由几何/尺寸等**非文字**证据推出来的件；"
                        "今天 %d 行全部只由名称文字产出（Spec §3.1 第 3 条）" % len(rows))

    def test_a3_detail_reports_the_kinds_it_actually_used(self):
        _require_sample(self)
        out = _derive()
        declared = _detail(out).get("evidence_kinds")
        self.assertIsInstance(declared, list,
                              "detail.evidence_kinds 必须给出本趟实际用上的证据类别（Spec §3.1 第 2 条）")
        self.assertTrue(set(declared) <= set(EVIDENCE_KINDS),
                        "detail.evidence_kinds 必须落在闭集里：%r" % (declared,))
        used = set()
        for row in _rows(out):
            used |= set(_kinds(row))
        self.assertEqual(used, set(declared),
                         "detail.evidence_kinds 必须是真值（等于各行 kinds 的并集）：申报 %r、实际 %r"
                         % (sorted(declared), sorted(used)))

    def test_a4_at_least_half_of_the_rows_have_sizes(self):
        _require_sample(self)
        detail = _detail(_derive())
        total = int(detail.get("business_part_total") or 0)
        sized = int(detail.get("parts_with_size_total") or 0)
        self.assertGreater(total, 0, "推件结果不能为空")
        self.assertGreaterEqual(
            sized / total, 0.5,
            "尺寸是主证据之一：至少一半的件要给出长宽（Spec §3.1 第 4 条）；今天 %d/%d" % (sized, total))

    """B 组：与金标对答案（Spec §3.2；金标只在测试侧）。"""

    def test_b1_recall_against_the_gold(self):
        _require_sample(self)
        report = _answer_against_gold(_derive())
        self.assertGreaterEqual(
            report["recall"], MIN_RECALL,
            "酒盒样本 recall 必须 ≥ %d/%d（Spec §3.2 第 1 条）；今天 %d/%d，漏件：%s"
            % (MIN_RECALL, GOLD_TOTAL, report["recall"], GOLD_TOTAL,
               "、".join(report["missing"])))

    def test_b2_precision_against_the_gold(self):
        _require_sample(self)
        report = _answer_against_gold(_derive())
        self.assertGreaterEqual(
            report["precision"], MIN_PRECISION,
            "precision 必须 ≥ %.2f（多件也要降下来，Spec §3.2 第 1 条）；今天 %.3f，多件：%s"
            % (MIN_PRECISION, report["precision"], "、".join(report["extra"])))

    def test_b3_reasons_breakdown_is_given(self):
        _require_sample(self)
        detail = _detail(_derive())
        breakdown = detail.get("reasons_breakdown")
        self.assertIsInstance(breakdown, dict,
                              "detail.reasons_breakdown（原因码 → 件数）必须给出，"
                              "让「为什么没推出来」一眼可查（Spec §3.2 第 3 条）")
        self.assertIn("unobservable_total", detail,
                      "detail 必须给出 DWG 完全观测不到、只能标「不可观测」的件数（Spec §3.2 第 2 条）")

    """C 组：分组与层级（Spec §3.3）。"""

    def test_c1_every_row_declares_its_group_judgement(self):
        _require_sample(self)
        bad = []
        for row in _rows(_derive()):
            group = ((row.get("evidence") or {}).get("group")
                     if isinstance(row.get("evidence"), dict) else None)
            if not isinstance(group, dict):
                bad.append("%s：没有 evidence.group" % row.get("business_part_code"))
                continue
            if group.get("kind") not in ("leaf", "group"):
                bad.append("%s：group.kind=%r 不在闭集 (leaf, group)"
                           % (row.get("business_part_code"), group.get("kind")))
        self.assertEqual([], bad,
                         "每一行都必须带分组判定（父名不许直接当独立件）：\n%s" % "\n".join(bad))

    def test_c2_parent_name_is_split_into_its_members(self):
        _require_sample(self)
        module = _resolver()
        normalized = {module.normalize_part_label(str(row.get("name") or ""))
                      for row in _rows(_derive())}
        missing = [name for name in GROUP_SPLIT_NAMES
                   if module.normalize_part_label(name) not in normalized]
        self.assertEqual([], missing,
                         "图上只有父名（左/右盒外盒里层灰板）时，必须拆成金标那四件（Spec §3.3 第 2 条）；"
                         "现在缺：%s" % "、".join(missing))

    """D 组：护栏（`## 453`/`## 456` 的成果，不许回退）。"""

    def test_d1_authority_sources_stay_drawing_only(self):
        self.assertEqual(tuple(_resolver().AUTHORITY_SOURCES), ("dwg", "missing"),
                         "运行时来源闭集不许放宽（`## 453` §2.1）")

    def test_d2_gold_standard_has_zero_effect(self):
        _require_sample(self)
        plain = json.dumps(_derive(), ensure_ascii=False, sort_keys=True)
        gold = json.loads(GOLD_STANDARD.read_text(encoding="utf-8")) if GOLD_STANDARD.exists() else {}
        seeded = json.dumps(_derive(attachments=gold, kb={"parts": _gold_names()},
                                    seed_path=str(GOLD_STANDARD)),
                            ensure_ascii=False, sort_keys=True)
        self.assertEqual(plain, seeded,
                         "金标 / 附件 / 知识库对结果必须零影响（`## 453` §2.1 第 3 条）")

    def test_d3_derivation_is_deterministic(self):
        _require_sample(self)
        first = json.dumps(_derive(), ensure_ascii=False, sort_keys=True)
        second = json.dumps(_resolver().resolve_business_parts("red-test", _real_ir(), None),
                            ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second, "同一份 IR 跑两次必须逐字相同")

    def test_d4_total_matches_the_row_count(self):
        _require_sample(self)
        out = _derive()
        self.assertEqual(int(_detail(out).get("business_part_total") or 0), len(_rows(out)),
                         "件数必须是真值（不许靠删件把 precision 做上去）")

    def test_d5_codes_are_unique_and_named(self):
        _require_sample(self)
        codes = [str(row.get("business_part_code") or "") for row in _rows(_derive())]
        self.assertEqual(len(set(codes)), len(codes), "业务部件编码必须唯一")
        for row in _rows(_derive()):
            self.assertTrue(str(row.get("business_part_code") or "").startswith("DWG-BP"),
                            "派生编码前缀固定 DWG-BP：%r" % row.get("business_part_code"))
            self.assertTrue(str(row.get("name") or "").strip(),
                            "%s 没有名称" % row.get("business_part_code"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
