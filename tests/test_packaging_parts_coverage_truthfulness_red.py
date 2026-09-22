"""红测：图纸零件覆盖率的诚实性（分子/分母/证据口径 + 缺口原因账）。

Spec：`docs/specs/packaging-parts-coverage-truthfulness.md`（取代
`packaging-parts-material-attribution.md` §4 的「真实样本门槛」表）。

现状缺口（9-22 实测，本机 `酒盒.dwg` + 需求 3.3）：
  · `material_known_ratio` = `thickness_known_ratio` = `processable_ratio` = `closed_ratio` = **0.625**
    —— 四条逐字相等：归属只在 `closed` 件上生效，而层 4 整盒兜底又把每个 closed 件都填满；
  · 于是 0.75/0.75/0.70 这三条门槛测的是"闭合轮廓占比"：分量 402 → 1163（分组变诚实）后，
    `test_packaging_parts_material_attribution_red` 直接 `failures=3`；
  · 料厚 40 件里 **34 件来自 `requirement_default`**（整盒 2.5mm），而 `packaging-parts-3d-extrusion.md`
    §2 明写"绝不许默认料厚" —— 一个模块禁止的东西正在给另一个模块的 KPI 充数；
  · 24 件没有材料，但没有任何读接口能说出"是没闭合轮廓 / 图纸没写 / 只有克重"。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.packaging_parts"
MATERIAL_SPEC = ROOT / "docs" / "specs" / "packaging-parts-material-attribution.md"
THIS_SPEC = "packaging-parts-coverage-truthfulness.md"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"

COUNT_KEYS = ("part_total", "closed_total", "material_known_total", "thickness_known_total",
              "processable_total", "material_default_total", "thickness_default_total")
EVIDENCE_KEYS = ("material_evidence_ratio", "thickness_evidence_ratio")
GAP_KEYS = ("material_gap_mix", "thickness_gap_mix")
MATERIAL_GAP_REASONS = ("no_closed_outline", "no_material_note", "material_ambiguous", "unknown")
THICKNESS_GAP_REASONS = ("no_closed_outline", "no_thickness_note", "grammage_only",
                         "material_missing", "material_ambiguous", "unknown")
FROZEN_RATIOS = ("material_known_ratio", "thickness_known_ratio", "processable_ratio",
                 "closed_ratio", "material_default_ratio")
#: Spec §2.3（2026-09-22 重标定）：三条地板 + 两条证据地板，**必须同时成立**。
#: 全部改成**绝对分子地板** —— 比值分母是"文档里有几件"，会随分组口径变
#: （`## 308` 让 `酒盒.dwg` 的件数 64 → 263），分子才是"能力有没有退步"。
FLOORS = {"material_known_total": 40, "thickness_known_total": 40, "processable_total": 40,
          "material_evidence_total": 20, "thickness_evidence_total": 8}


def floor_actual(summary, key):
    """取绝对分子：有 `*_total` 键就直接用，否则用 `*_ratio × part_total` 还原。"""
    value = summary.get(key)
    if value is not None:
        return float(value)
    ratio_key = key[: -len("_total")] + "_ratio"
    return round(float(summary.get(ratio_key) or 0) * float(summary.get("part_total") or 0))
REQUIREMENT = {"grey_board": "灰板", "grey_board_thickness": 2.5,
               "face_paper": "粉灰", "face_paper_gsm": 350}


def text_entry(eid, text, xy):
    return {"entity_id": eid, "type": "MTEXT", "layer": "TEXT",
            "normalized_text": text, "raw_text": text,
            "position": [float(xy[0]), float(xy[1])], "height": 2.5,
            "evidence_ref": "ev:E:" + eid.split(":")[-1]}


def rect(tag, x0, y0, w, h, layer="DESIGN"):
    corners = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    eids, entities = [], []
    for index in range(4):
        start, end = corners[index], corners[(index + 1) % 4]
        eid = "ent:model:%s-%d" % (tag, index)
        eids.append(eid)
        entities.append({"entity_id": eid, "type": "LINE", "layer": layer,
                         "attributes": {"start": [float(start[0]), float(start[1])],
                                        "end": [float(end[0]), float(end[1])]}})
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids,
                 "bbox": [float(x0), float(y0), float(x0 + w), float(y0 + h)]}
    return component, entities


def open_rect(tag, x0, y0, w, h, layer="DESIGN"):
    """缺一条边 → 真开线（端点度数为奇数）。"""
    corners = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    eids, entities = [], []
    for index in range(3):
        start, end = corners[index], corners[(index + 1) % 4]
        eid = "ent:model:%s-%d" % (tag, index)
        eids.append(eid)
        entities.append({"entity_id": eid, "type": "LINE", "layer": layer,
                         "attributes": {"start": [float(start[0]), float(start[1])],
                                        "end": [float(end[0]), float(end[1])]}})
    component = {"component_id": "cmp:%s" % tag, "entity_ids": eids,
                 "bbox": [float(x0), float(y0), float(x0 + w), float(y0 + h)]}
    return component, entities


def make_ir(pieces, texts=()):
    components, entities = [], []
    for component, rows in pieces:
        components.append(component)
        entities.extend(rows)
    return {"ir_version": "cad-ir/1", "ir_id": "ir:test", "ir_hash": "hash",
            "parser": {"name": "test"}, "source": {"kind": "dxf_2d", "attachment_name": "t.dxf"},
            "units": {"drawing_units": "mm", "scale_to_mm": 1.0, "unit_status": "confirmed",
                      "unit_confidence": 1.0, "candidates": []},
            "layers": [], "entities": entities, "texts": list(texts),
            "dimensions": [], "geometry": {"components": components},
            "evidence": {}, "unsupported": [], "warnings": [], "stats": {}}


class CoverageCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module(PKG)

    def extract(self, pieces, texts=(), requirement=None):
        options = {"requirement": requirement} if requirement is not None else None
        return self.module.extract(make_ir(pieces, texts), options=options)

    def summary(self, doc):
        return self.module.summarize(doc)

    def part(self, doc, component_id):
        for row in doc.get("parts") or []:
            if row.get("component_id") == component_id:
                return row
        raise AssertionError("没有分量 %s（parts=%d）" % (component_id, len(doc.get("parts") or [])))


# --------------------------------------------------------------------------- #
# A 组：计数、分母与"证据口径"
# --------------------------------------------------------------------------- #
class ACounts(CoverageCase):
    def test_a1_summary_exposes_counts_evidence_and_gaps(self):
        doc = self.extract([rect("A", 0, 0, 100, 100)])
        summary = self.summary(doc)
        for key in COUNT_KEYS + EVIDENCE_KEYS + GAP_KEYS:
            self.assertIn(key, summary, "summarize() 必须带 %s（Spec §2.1 / §2.2）" % key)
        for key in FROZEN_RATIOS:
            self.assertIn(key, summary, "既有指标 %s 不许丢（Spec §2.4）" % key)

    def test_a2_evidence_ratio_is_not_the_same_as_known_ratio(self):
        pieces = [rect("NOTE", 0, 0, 100, 100), rect("DEFAULT", 400, 0, 100, 100)]
        texts = [text_entry("ent:text:1", "名称：盒背灰板 材料：2.5MM灰板", (50, 50))]
        doc = self.extract(pieces, texts, requirement=REQUIREMENT)
        summary = self.summary(doc)
        self.assertEqual(1.0, float(summary.get("material_known_ratio") or 0),
                         "两件都拿到材料（一件注记、一件整盒兜底）→ known 口径是 1.0")
        self.assertEqual(0.5, float(summary.get("material_evidence_ratio") or 0),
                         "证据口径必须把整盒兜底排除：只有 1/2 件是图纸证据（Spec §2.1）")
        self.assertEqual(1, int(summary.get("material_default_total") or 0))

    def test_a3_zero_parts_is_all_zero_not_error(self):
        summary = self.summary(self.extract([]))
        for key in COUNT_KEYS:
            self.assertEqual(0, int(summary.get(key) or 0), "%s 在空文档上必须是 0" % key)
        for key in EVIDENCE_KEYS:
            self.assertEqual(0.0, float(summary.get(key) or 0), "%s 在空文档上必须是 0.0" % key)
        for key in GAP_KEYS:
            self.assertEqual({}, {k: v for k, v in (summary.get(key) or {}).items() if v},
                             "%s 在空文档上必须是全 0" % key)


# --------------------------------------------------------------------------- #
# B 组：缺口原因账
# --------------------------------------------------------------------------- #
class BGaps(CoverageCase):
    def mixed_fixture(self):
        pieces = [open_rect("OPEN", 0, 0, 100, 100),
                  rect("BARE", 400, 0, 100, 100),
                  rect("NOTED", 800, 0, 100, 100)]
        texts = [text_entry("ent:text:1", "名称：盒背灰板 材料：2.5MM灰板", (850, 50))]
        return pieces, texts

    def test_b1_material_gap_mix_adds_up(self):
        pieces, texts = self.mixed_fixture()
        doc = self.extract(pieces, texts)
        summary = self.summary(doc)
        gap = summary.get("material_gap_mix") or {}
        self.assertEqual(int(summary.get("part_total") or 0) - int(summary.get("material_known_total") or 0),
                         sum(int(value or 0) for value in gap.values()),
                         "材料缺口账必须等于 part_total - material_known_total（Spec §2.2）")
        self.assertEqual(1, int(gap.get("no_closed_outline") or 0), "开线件必须记 no_closed_outline")
        self.assertEqual(1, int(gap.get("no_material_note") or 0), "闭合但没注记 → no_material_note")
        self.assertEqual(0, int(gap.get("unknown") or 0), "unknown 长期必须为 0（Spec §2.2）")

    def test_b2_grammage_only_is_its_own_reason(self):
        pieces = [rect("GSM", 0, 0, 100, 100)]
        texts = [text_entry("ent:text:1", "350g粉灰", (50, 50))]
        doc = self.extract(pieces, texts)
        row = self.part(doc, "cmp:GSM")
        self.assertTrue(row.get("material"), "夹具前提：这一件有材料")
        self.assertIsNone(row.get("thickness_mm"))
        gap = self.summary(doc).get("thickness_gap_mix") or {}
        self.assertGreaterEqual(int(gap.get("grammage_only") or 0), 1,
                                "只有克重、又推不出密度 → grammage_only（Spec §2.2）")
        self.assertEqual("grammage_only", (row.get("attribution") or {}).get("gap_reason"),
                         "件上的 gap_reason 必须和账一致")

    def test_b3_thickness_gap_reasons_are_a_closed_set(self):
        pieces, texts = self.mixed_fixture()
        doc = self.extract(pieces, texts)
        gap = self.summary(doc).get("thickness_gap_mix") or {}
        for reason in gap:
            self.assertIn(reason, THICKNESS_GAP_REASONS, "原因必须在闭集内（Spec §2.2）")
        self.assertEqual(int(self.summary(doc).get("part_total") or 0)
                         - int(self.summary(doc).get("thickness_known_total") or 0),
                         sum(int(value or 0) for value in gap.values()))
        self.assertEqual(0, int(gap.get("unknown") or 0))

    def test_b4_material_gap_reasons_are_a_closed_set(self):
        pieces, texts = self.mixed_fixture()
        doc = self.extract(pieces, texts)
        for reason in (self.summary(doc).get("material_gap_mix") or {}):
            self.assertIn(reason, MATERIAL_GAP_REASONS)


# --------------------------------------------------------------------------- #
# C 组：真样本门槛（默认不跑）
# --------------------------------------------------------------------------- #
class RealSampleCoverage(CoverageCase):
    doc = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.environ.get("CPQ_DWG_REAL_SAMPLES") != "1":
            raise unittest.SkipTest("未设置 CPQ_DWG_REAL_SAMPLES=1：真实样本组默认不跑（Spec §5）")
        sample = SAMPLES_DIR / "酒盒.dwg"
        if not sample.is_file():
            raise unittest.SkipTest("样本不在本机：%s" % sample)
        tool = shutil.which("dwg2dxf")
        if not tool:
            raise unittest.SkipTest("本机没有 libredwg 的 dwg2dxf")
        from tech_app.backend.services import cad_ir
        workspace = pathlib.Path(tempfile.mkdtemp(prefix="cpq-dwg-coverage-"))
        cls.workspace = workspace
        cache = workspace / "real.dxf"
        subprocess.run([tool, "-y", "-o", str(cache), str(sample)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ir = cad_ir.parse_dxf(cache.read_bytes(), filename=cache.name,
                              source={"kind": "dxf_2d", "attachment_name": sample.name})
        cls.doc = cls.module.extract(ir, options={"requirement": REQUIREMENT})

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "workspace", None) is not None:
            shutil.rmtree(cls.workspace, ignore_errors=True)

    def summary_of(self):
        return self.module.summarize(self.doc)

    def test_c1_all_floors_hold(self):
        summary = self.summary_of()
        for key, floor in FLOORS.items():
            self.assertGreaterEqual(floor_actual(summary, key), floor,
                                    "%s 地板 %d（Spec §2.3，9-22 按全量分母重标定）" % (key, floor))

    def test_c2_default_filling_is_visible(self):
        summary = self.summary_of()
        default_total = int(summary.get("thickness_default_total") or 0)
        known_total = int(summary.get("thickness_known_total") or 0)
        self.assertGreaterEqual(default_total, 1,
                                "酒盒真图上整盒兜底件数必须看得见、且远大于 0（Spec §1.2）")
        self.assertLessEqual(default_total, known_total,
                             "兜底件数不可能超过已知件数（Spec §2.1）")

    def test_c3_gap_accounts_add_up_on_the_real_drawing(self):
        summary = self.summary_of()
        total = int(summary.get("part_total") or 0)
        self.assertEqual(total - int(summary.get("material_known_total") or 0),
                         sum(int(value or 0) for value in (summary.get("material_gap_mix") or {}).values()))
        self.assertEqual(total - int(summary.get("thickness_known_total") or 0),
                         sum(int(value or 0) for value in (summary.get("thickness_gap_mix") or {}).values()))


# --------------------------------------------------------------------------- #
# D 组：两份文档不许并存两套门槛
# --------------------------------------------------------------------------- #
class DDocAgreement(unittest.TestCase):
    def test_d1_material_attribution_points_to_the_new_owner(self):
        text = MATERIAL_SPEC.read_text(encoding="utf-8")
        self.assertIn(THIS_SPEC, text,
                      "§4 的门槛必须有唯一出处：旧 Spec 要点名 %s（Spec §2.3）" % THIS_SPEC)
