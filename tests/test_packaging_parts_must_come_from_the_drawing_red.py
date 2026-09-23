# -*- coding: utf-8 -*-
"""解析只吃 DWG：业务部件清单必须从图纸推导，BOM 只做最后对答案（红测）。

对应 Spec：`docs/specs/packaging-parts-must-be-derived-from-the-drawing.md`
编号（A/B/C/D/E/F）与该 Spec §2 各小节逐条对应。

本批现场结论（只读实测）：酒盒的 28 件是 `drawing_hash` 命中仓库内快照查出来的；
圆盘盒掉到 `dwg_candidate`，16 件里混着视图标题与产地备注。裁定是解析只吃 DWG。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESOLVER_PATH = ROOT / "tech_app/backend/services/packaging_business_part_resolver.py"
FLOW_PATH = ROOT / "tech_app/backend/services/packaging_drawing_flow/steps.py"
MAIN_PATH = ROOT / "tech_app/backend/main.py"
APP_PATH = ROOT / "tech_app/frontend/app.js"
SHIPPED_SNAPSHOT = ROOT / "tech_app/agent_knowledge/provenance/packaging_authority_parts.json"
GOLD_STANDARD = ROOT / "tests/fixtures/gold/packaging_authority_parts.json"
SAMPLE_ROOT = ROOT / "tech_app/data/cad-ir-realsample/conversions"

#: 真样本：conversion_id 与 DWG 的 sha-256（取自转换 manifest）。
SAMPLES = {
    "酒盒": ("47c39dc1ab6738fc48c8",
             "0991c8b0a9646d1fea6571d2ae6155923351c05ca2aeeb6ed544ef19df93f3e0"),
    "圆盘盒": ("a6140fbc4e9b8d2e9bee",
               "4c70ce7b3774c2a1803a942341a606c92758228cf982c3bf1016642be44a531b"),
}

#: 酒盒图纸名称锚点里**不是件名**的整串（Spec §2.3/§5 现场证据；标题栏那几个带全角空格）。
WINE_NON_PART_TEXTS = ("角  度", "批 准", "日 期", "单 位", "比 例", "审 核", "设 计",
                       "包装", "包装材料", "包装材料说明（包含颜色、密度、厚度等）",
                       "酒盒顶托", "700ML酒盒底托")

#: 圆盘盒候选里混进来的非零件串（Spec §0 实测）。
ROUND_NON_PART_TEXTS = ("侧视图", "后视图", "俯视图上往下", "示意图", "面纸转越南",
                        "旧款大货色位不够", "内托方案1", "内托方案2")

#: 金标里**图纸名称锚点没有**的件（派生清单不许凭空出现这几项，Spec §2.1/§5）。
GOLD_ONLY_NAMES = ("磁铁", "内卡", "底板", "底板面纸", "底托灰板")


def _resolver():
    from tech_app.backend.services import packaging_business_part_resolver as module
    return module


_IR_CACHE: dict = {}


def _real_ir(label: str):
    """真样本 → CAD IR（只读；缺样本时由调用方 skip）。"""
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


def _require_sample(test: unittest.TestCase, label: str):
    conversion_id, _ = SAMPLES[label]
    if not (SAMPLE_ROOT / conversion_id / "converted.dxf").exists():
        test.skipTest("缺少真样本 DXF（%s）" % conversion_id)


_OUT_CACHE: dict = {}


def _derive(label: str, **kwargs):
    """真样本 → `resolve_business_parts()` 结果（同一份 IR 只算一次）。"""
    key = (label,) + tuple(sorted(kwargs.items(), key=lambda item: item[0]))
    if key in _OUT_CACHE:
        return _OUT_CACHE[key]
    out = _resolver().resolve_business_parts("red-test", _real_ir(label), None, **kwargs)
    _OUT_CACHE[key] = out
    return out


def _rows(out) -> list:
    return [row for row in (out.get("authority_rows") or []) if isinstance(row, dict)]


def _names(out) -> list:
    return [str(row.get("name") or "") for row in _rows(out)]


def _texts(*items) -> list:
    return [{"entity_id": "ent:t%02d" % index, "handle": "T%02d" % index, "layer": "0",
             "raw_text": raw, "position": [float(index), 0.0]}
            for index, raw in enumerate(items, start=1)]


def _synthetic_ir(*raw_texts, sha: str = "synthetic-sha") -> dict:
    return {
        "ir_version": "cad-ir/1",
        "source": {"source_sha256": sha, "original_filename": "synthetic.dxf",
                   "conversion_id": "synthetic"},
        "units": {"unit_status": "confirmed", "unit": "mm", "scale_to_mm": 1.0},
        "layers": [{"name": "0"}],
        "entities": [],
        "texts": _texts(*raw_texts),
        "geometry": {"components": [], "repeated_groups": []},
        "document": {"extents": [0.0, 0.0, 100.0, 100.0], "extents_source": "declared"},
    }


def _derive_synthetic(*raw_texts: str, sha: str = "synthetic-sha"):
    return _resolver().resolve_business_parts("red-test", _synthetic_ir(*raw_texts, sha=sha), None)


class RuntimeSourcesAreDrawingOnlyRed(unittest.TestCase):
    """A 组：运行时来源闭集（Spec §2.1）。"""

    def test_a1_authority_sources_closed_set_is_dwg_and_missing(self):
        self.assertEqual(tuple(_resolver().AUTHORITY_SOURCES), ("dwg", "missing"),
                         "运行时来源闭集只允许 dwg / missing（Spec §2.1 第 1 条）")

    def test_a2_refused_sources_are_declared(self):
        refused = set(getattr(_resolver(), "RUNTIME_REFUSED_SOURCES", ()))
        self.assertTrue({"attachment", "knowledge_base", "drawing_hash"} <= refused,
                        "必须显式声明被拒绝的来源（Spec §2.1 第 2 条）")

    def test_a3_attachment_workbook_is_refused(self):
        module = _resolver()
        rows = [{"sequence_no": i, "business_part_code": "JWXR21-P%02d" % i, "name": "左盖面纸",
                 "length_mm": 100.0, "width_mm": 200.0} for i in range(1, 29)]

        def fake_importer(*args, **kwargs):
            return {"parts": rows, "source": {"file": "酒盒 报价资料.xlsx", "sheet": "零部件排版工艺"}}

        out = module.resolve_business_parts(
            "red-test", _synthetic_ir("名称：左盖面纸"), None,
            [{"name": "酒盒 报价资料.xlsx", "path": "/tmp/wine.xlsx"}], None,
            import_workbook=fake_importer)
        detail = out.get("detail") or {}
        self.assertEqual(out.get("authority_source"), "dwg",
                         "附件工作簿不许当清单来源（Spec §2.1 第 3 条）")
        self.assertIs(detail.get("gold_standard_used"), False)
        self.assertIn("attachment", detail.get("refused_sources") or [],
                      "被拒来源要留痕（Spec §2.1 第 2 条）")

    def test_a4_gold_standard_has_zero_effect_on_the_result(self):
        module = _resolver()
        ir = _synthetic_ir("名称：左盖面纸", "名称：内盒1面纸", sha="sha-for-gold")
        gold = {"sources": [{"reviewed": True, "authority_source": "drawing_hash",
                             "drawing_sha256": "sha-for-gold", "code_prefix": "JWXR21",
                             "business_part_total": 2,
                             "parts": [{"sequence_no": 1, "business_part_code": "JWXR21-P01",
                                        "name": "磁铁", "length_mm": 10.0, "width_mm": 20.0},
                                       {"sequence_no": 2, "business_part_code": "JWXR21-P02",
                                        "name": "内卡", "length_mm": 30.0, "width_mm": 40.0}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / "packaging_authority_parts.json"
            seed.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
            with_gold = module.resolve_business_parts("red-test", ir, None, None, None,
                                                      seed_path=str(seed))
            without = module.resolve_business_parts("red-test", ir, None, None, None,
                                                    seed_path=None)
        self.assertEqual(with_gold.get("authority_source"), "dwg",
                         "金标不许当运行时来源（Spec §2.1 第 3 条）")
        self.assertEqual(json.dumps(with_gold, ensure_ascii=False, sort_keys=True),
                         json.dumps(without, ensure_ascii=False, sort_keys=True),
                         "给了金标与不给金标，结果必须逐字相同")
        self.assertNotIn("磁铁", _names(with_gold))

    def test_a5_default_seed_path_is_empty(self):
        self.assertEqual(str(getattr(_resolver(), "DEFAULT_SEED_PATH", "x")), "",
                         "生产默认不许读任何已审核快照（Spec §2.1 第 4 条）")

    def test_a6_load_seed_without_path_reads_nothing(self):
        self.assertEqual(_resolver().load_seed(), {},
                         "不传路径时不许读到仓库里的快照（Spec §2.1 第 4 条）")

    def test_a7_flow_precedence_is_drawing_only(self):
        flow = FLOW_PATH.read_text(encoding="utf-8")
        self.assertRegex(flow, r'AUTHORITY_PRECEDENCE\s*=\s*\(\s*"dwg"\s*,\s*"missing"\s*,?\s*\)',
                         "图纸流的来源优先级只剩 dwg / missing（Spec §2.1 第 5 条）")

    def test_a8_flow_does_not_take_attachments_as_a_source(self):
        flow = FLOW_PATH.read_text(encoding="utf-8")
        start = flow.index("def _resolve_business_parts")
        end = flow.index("\ndef ", start + 10)
        body = flow[start:end]
        self.assertNotIn("load_attachments", body,
                         "附件不再是业务部件清单的来源（Spec §2.1 第 5 条）")


class DerivedRowsCarryNameSizeAndDrawingRed(unittest.TestCase):
    """B 组：产出契约 = 名称 + 尺寸 + 图纸证据（Spec §2.2）。"""

    @classmethod
    def setUpClass(cls):
        cls.out = _derive("酒盒")

    def test_b1_every_row_has_name_size_and_evidence(self):
        rows = _rows(self.out)
        self.assertTrue(rows, "必须推得出部件行")
        for row in rows:
            for key in ("business_part_code", "name", "length_mm", "width_mm",
                        "material_text", "process_text", "evidence"):
                self.assertIn(key, row, "行缺字段 %s（Spec §2.2）" % key)

    def test_b2_drawing_ref_points_somewhere(self):
        for row in _rows(self.out):
            ref = (row.get("evidence") or {}).get("drawing_ref")
            self.assertIsInstance(ref, dict, "每件必须有图纸证据块（Spec §2.2）")
            self.assertIn(ref.get("kind"), ("geometry_evidence", "part_view", "none"))
            if ref.get("kind") != "none":
                self.assertTrue(ref.get("component_ids") or ref.get("bbox"),
                                "非 none 的图纸证据必须给得出位置（Spec §2.2）")

    def test_b3_status_closed_set_and_reasons(self):
        for row in _rows(self.out):
            self.assertIn(row.get("status"), ("derived", "partial", "unbound"))
            if row.get("status") != "derived":
                self.assertTrue(row.get("reasons"), "不是 derived 就必须给稳定码原因")

    def test_b4_name_comes_from_the_drawing(self):
        for row in _rows(self.out):
            self.assertIs(row.get("name_from_drawing"), True,
                          "名称必须来自本图（Spec §2.2）")

    def test_b5_derivation_is_deterministic(self):
        first = _resolver().resolve_business_parts("red-test", _real_ir("酒盒"), None)
        second = _resolver().resolve_business_parts("red-test", _real_ir("酒盒"), None)
        self.assertEqual(json.dumps(first, ensure_ascii=False, sort_keys=True),
                         json.dumps(second, ensure_ascii=False, sort_keys=True))

    def test_b6_detail_declares_drawing_derivation(self):
        detail = self.out.get("detail") or {}
        self.assertIs(detail.get("derived_from_drawing"), True)
        self.assertIs(detail.get("gold_standard_used"), False)

    def test_b7_counters_are_truthful(self):
        rows = _rows(self.out)
        detail = self.out.get("detail") or {}
        with_size = [row for row in rows
                     if row.get("length_mm") is not None and row.get("width_mm") is not None]
        with_ref = [row for row in rows
                    if (row.get("evidence") or {}).get("drawing_ref", {}).get("kind") not in (None, "none")]
        self.assertEqual(detail.get("parts_with_size_total"), len(with_size))
        self.assertEqual(detail.get("parts_with_drawing_ref_total"), len(with_ref))

    def test_b8_derived_codes_are_distinguishable_from_customer_codes(self):
        for row in _rows(self.out):
            self.assertTrue(str(row.get("business_part_code") or "").startswith("DWG-BP"),
                            "派生编码前缀固定 DWG-BP，别和客户编码混淆（Spec §2.2）")


class NonPartTextsNeverBecomePartNamesRed(unittest.TestCase):
    """C 组：排除规则（Spec §2.3）。"""

    def test_c1_view_titles_are_not_part_names(self):
        out = _derive_synthetic("侧视图", "后视图", "俯视图上往下", "示意图", "名称：左盖面纸")
        names = _names(out)
        for junk in ("侧视图", "后视图", "俯视图上往下", "示意图"):
            self.assertNotIn(junk, names)

    def test_c2_origin_and_status_notes_are_not_part_names(self):
        out = _derive_synthetic("面纸转越南", "旧款大货色位不够", "名称：左盖面纸")
        names = _names(out)
        for junk in ("面纸转越南", "旧款大货色位不够"):
            self.assertNotIn(junk, names)

    def test_c3_option_names_are_not_part_names(self):
        out = _derive_synthetic("内托方案1", "内托方案2", "名称：左盖面纸")
        for junk in ("内托方案1", "内托方案2"):
            self.assertNotIn(junk, _names(out))

    def test_c4_title_block_fields_are_not_part_names(self):
        out = _derive_synthetic("角  度", "批 准", "日 期", "单 位", "比 例", "审 核", "设 计",
                                "名称：左盖面纸")
        for junk in ("角  度", "批 准", "日 期", "单 位", "比 例", "审 核", "设 计"):
            self.assertNotIn(junk, _names(out))
            self.assertNotIn(junk.replace("  ", ""), _names(out))

    def test_c5_material_table_header_is_not_a_part_name(self):
        out = _derive_synthetic("包装材料", "包装材料说明（包含颜色、密度、厚度等）",
                                "名称：左盖面纸")
        names = _names(out)
        self.assertNotIn("包装", names, "表头被截断后不许变合法件名（Spec §2.3 第 5 条）")
        self.assertNotIn("包装材料", names)

    def test_c6_whole_box_names_are_not_part_names(self):
        out = _derive_synthetic("酒盒顶托", "700ML酒盒底托", "名称：左盖面纸")
        names = _names(out)
        self.assertNotIn("酒盒顶托", names)
        self.assertNotIn("700ML酒盒底托", names)

    def test_c7_round_box_real_sample_has_no_non_part_names(self):
        _require_sample(self, "圆盘盒")
        names = _names(_derive("圆盘盒"))
        for junk in ROUND_NON_PART_TEXTS:
            self.assertNotIn(junk, names, "圆盘盒真样本里 %r 不是零件（Spec §0/§2.3）" % junk)


class LabelAndMaterialAreSplitRed(unittest.TestCase):
    """D 组：名称与材料拆分（Spec §2.4）。"""

    def _row_by_raw(self, raw: str):
        out = _derive_synthetic(raw)
        for row in _rows(out):
            text = json.dumps(row, ensure_ascii=False)
            if raw.split("\\P")[0][:6] in text:
                return row
        return {}

    def test_d1_colon_form_keeps_only_the_name(self):
        row = self._row_by_raw("名称：左盖面纸\\P材料：225G铜版底PET光银")
        self.assertEqual(row.get("name"), "左盖面纸")

    def test_d2_full_width_comma_form_keeps_only_the_name(self):
        row = self._row_by_raw("面卡，300G白卡/哑PP")
        self.assertEqual(row.get("name"), "面卡")

    def test_d3_full_width_comma_with_sequence_number(self):
        row = self._row_by_raw("内托灰板1，1500G双灰板")
        self.assertEqual(row.get("name"), "内托灰板1")

    def test_d4_another_full_width_comma_case(self):
        row = self._row_by_raw("底盒底板1，1100G双灰板")
        self.assertEqual(row.get("name"), "底盒底板1")

    def test_d5_multiline_name_with_trailing_material(self):
        row = self._row_by_raw("内托支撑围条灰板\\P650G灰板\\P正面图，啤面")
        self.assertEqual(row.get("name"), "内托支撑围条灰板")

    def test_d6_material_text_is_not_lost(self):
        row = self._row_by_raw("面卡，300G白卡/哑PP")
        self.assertTrue(str(row.get("material_text") or "").strip(),
                        "拆出来的材料必须留在 material_text（Spec §2.2/§2.4）")

    def test_d7_half_width_comma_same_rule(self):
        row = self._row_by_raw("面卡,300G白卡")
        self.assertEqual(row.get("name"), "面卡")


class GoldStandardLivesOnTheTestSideRed(unittest.TestCase):
    """E 组：金标只做对答案（Spec §2.5）。"""

    def test_e1_shipped_snapshot_is_gone(self):
        self.assertFalse(SHIPPED_SNAPSHOT.exists(),
                         "生产树里不许再有已审核快照：%s" % SHIPPED_SNAPSHOT)

    def test_e2_gold_standard_lives_under_tests(self):
        self.assertTrue(GOLD_STANDARD.exists(), "金标搬到 %s" % GOLD_STANDARD)
        if GOLD_STANDARD.exists():
            doc = json.loads(GOLD_STANDARD.read_text(encoding="utf-8"))
            sources = doc.get("sources") or []
            self.assertTrue(sources)
            wine = sources[0]
            self.assertEqual(wine.get("business_part_total"), 28)
            self.assertEqual(wine.get("drawing_sha256"), SAMPLES["酒盒"][1])
            self.assertEqual(len(wine.get("parts") or []), 28)

    def test_e3_backend_never_references_the_gold_standard(self):
        offenders = []
        for path in (ROOT / "tech_app").rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "packaging_authority_parts.json" in text or "tests/fixtures/gold" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "生产代码不许引用金标（Spec §2.5 第 3 条）")


class DerivedListAnswersAgainstTheGoldRed(unittest.TestCase):
    """F 组：与金标对答案（Spec §1/§2.1）。

    对答案只在测试里发生：生产运行时读不到金标。
    """

    def test_f1_wine_names_cover_the_gold_without_borrowing_it(self):
        _require_sample(self, "酒盒")
        module = _resolver()
        names = _names(_derive("酒盒"))
        gold = json.loads(GOLD_STANDARD.read_text(encoding="utf-8")) if GOLD_STANDARD.exists() else {}
        gold_names = [row.get("name") for row in ((gold.get("sources") or [{}])[0].get("parts") or [])]
        normalized = {module.normalize_part_label(name) for name in names if name}
        hit = [name for name in gold_names if module.normalize_part_label(name) in normalized]
        self.assertGreaterEqual(len(hit), 18,
                                "图纸名称证据实测能覆盖金标 18 件（Spec §0/§5）")
        for borrowed in GOLD_ONLY_NAMES:
            self.assertNotIn(borrowed, names,
                             "金标有、图纸名称锚点里没有的件不许出现（说明借了金标）")
        self.assertTrue({"右盖外盒里层灰板", "顶托灰板"} & set(names),
                        "图纸名称锚点里有、金标里没有的件必须出现在派生清单里（证明确实从图推）")

    def test_f2_every_wine_row_is_sized_or_explicitly_unbound(self):
        _require_sample(self, "酒盒")
        for row in _rows(_derive("酒盒")):
            if row.get("status") == "derived":
                self.assertIsNotNone(row.get("length_mm"))
                self.assertIsNotNone(row.get("width_mm"))
            else:
                self.assertTrue(row.get("reasons"))

    def test_f3_wine_names_have_no_non_part_texts(self):
        _require_sample(self, "酒盒")
        names = _names(_derive("酒盒"))
        for junk in WINE_NON_PART_TEXTS:
            self.assertNotIn(junk, names)
            self.assertNotIn(junk.replace("  ", ""), names)


if __name__ == "__main__":
    unittest.main()
