"""红测：图纸零件的料厚事实（克重推导 / 防跨材料串味 / 人工补料厚）。

Spec：`docs/specs/packaging-parts-thickness-facts.md`
前置：`packaging-parts-material-attribution.md`（四层归属）已实现。

现状缺口（本机实测 `酒盒.dwg`，不是推断）：
  · 64 件里 `thickness_mm` 只有 9 件 → `processability()` 通过 9 件、`extrude_all()` ok 9 件；
  · 12 件材料写成克重（`225G铜版底PET光银` / `350g粉灰` / `235g白卡底PET光银裱A9 E坑`），
    `_note_thickness()` 只认 mm，于是永远拿不到料厚；
  · 9 件已有料厚里 **2 件是跨材料串味**（`DWG-P31` / `DWG-P47` 材料是面纸、厚度取自「内盒N灰板」
    的成组注记 2mm），来源看着齐全、数值却是别人的；
  · 没有 `set_manual_thickness` / 写路由 / 前端补录入口，55 件不可挤出也没人能补。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import json
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
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
SAMPLE_TOOL = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"

DERIVED_KIND = "derived_from_gsm_density"
MANUAL_KIND = "manual"
THICKNESS_KINDS = ("part_note", "group_note", "layer_name", "requirement_default",
                   DERIVED_KIND, MANUAL_KIND)
NEW_SUMMARY_KEYS = ("thickness_known_total", "thickness_unknown_total",
                    "thickness_conflict_total", "thickness_manual_total")
RESOLUTION_REASONS = ("density_missing", "material_ambiguous", "no_grammage", "material_unknown")
CONFLICT_REASON = "thickness_material_conflict"
THICKNESS_PATH_TOKEN = "thickness"
#: 只放一条：灰板密度 0.75 g/cm³（与 `packaging-cost-gaps-closure.md` §1.1 同一口径）。
MATERIAL_TABLE = [{"material_code": "MAT-PKG-GREYBOARD", "name": "灰板（双灰纸板）",
                   "grade": "t2.0，双灰，含水率 8%±2", "spec": "灰板", "density": 0.75}]
AMBIGUOUS_TABLE = MATERIAL_TABLE + [
    {"material_code": "MAT-PKG-GREYBOARD-2", "name": "粉灰灰板", "grade": "350g", "density": 0.8}]


def text_entry(eid, text, xy):
    return {"entity_id": eid, "type": "MTEXT", "layer": "TEXT",
            "normalized_text": text, "raw_text": text,
            "position": [float(xy[0]), float(xy[1])], "height": 2.5,
            "evidence_ref": "ev:E:" + eid.split(":")[-1]}


def rect(tag, x0, y0, w, h, layer="DESIGN"):
    """4 条 LINE 组成的闭合矩形分量。"""
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


class ThicknessCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module(PKG)

    def extract(self, pieces, texts=(), *, material_table=None, requirement=None):
        options = {}
        if requirement is not None:
            options["requirement"] = requirement
        if material_table is not None:
            options["material_table"] = material_table
        return self.module.extract(make_ir(pieces, texts), options=options or None)

    def part(self, doc, component_id):
        for row in doc.get("parts") or []:
            if row.get("component_id") == component_id:
                return row
        raise AssertionError("零件文档里没有分量 %s（parts=%d）"
                             % (component_id, len(doc.get("parts") or [])))


# --------------------------------------------------------------------------- #
# A 组：克重 → 料厚（唯一合法路径：克重 ÷ 密度）
# --------------------------------------------------------------------------- #
class ADerivation(ThicknessCase):
    def test_a1_grammage_times_density_yields_thickness_with_provenance(self):
        piece = rect("A", 0, 0, 100, 100)
        note = text_entry("ent:text:1", "名称：左盒盒背灰板 材料：灰板 1500g", (50, 50))
        doc = self.extract([piece], [note], material_table=MATERIAL_TABLE)
        row = self.part(doc, "cmp:A")
        self.assertEqual(2.0, row.get("thickness_mm"),
                         "1500 g/㎡ ÷ (0.75 g/cm³ × 1000) 必须等于 2.0mm（Spec §2.1）")
        source = row.get("thickness_source") or {}
        self.assertEqual(DERIVED_KIND, source.get("kind"), "来源必须是推导，不许写成 group_note")
        self.assertEqual(1500.0, source.get("gsm"))
        self.assertEqual(0.75, source.get("density"))
        self.assertEqual("MAT-PKG-GREYBOARD", source.get("material_code"))
        self.assertTrue(source.get("evidence_ref"), "推导必须留注记证据（Spec §2.1）")
        self.assertTrue(row.get("needs_confirmation"), "推导值必须 needs_confirmation=True")

    def test_a2_missing_density_never_guesses_and_is_visible(self):
        piece = rect("A", 0, 0, 100, 100)
        note = text_entry("ent:text:1", "350g粉灰", (50, 50))
        doc = self.extract([piece], [note], material_table=MATERIAL_TABLE)
        row = self.part(doc, "cmp:A")
        self.assertIsNone(row.get("thickness_mm"), "材料表里没有对应密度，就不许给料厚")
        self.assertIsNone(row.get("thickness_source"))
        unresolved = (row.get("attribution") or {}).get("thickness_unresolved") or []
        self.assertTrue(unresolved, "推不出来必须看得见（Spec §2.4）")
        self.assertIn(unresolved[0].get("reason"), RESOLUTION_REASONS)

    def test_a3_ambiguous_material_abstains(self):
        piece = rect("A", 0, 0, 100, 100)
        note = text_entry("ent:text:1", "名称：左盒盒背灰板 材料：灰板 1500g", (50, 50))
        doc = self.extract([piece], [note], material_table=AMBIGUOUS_TABLE)
        row = self.part(doc, "cmp:A")
        self.assertIsNone(row.get("thickness_mm"), "材料表命中 ≥2 条 → 弃权（Spec §2.3）")
        reasons = [item.get("reason")
                   for item in (row.get("attribution") or {}).get("thickness_unresolved") or []]
        self.assertIn("material_ambiguous", reasons)

    def test_a4_no_material_table_means_no_derivation(self):
        piece = rect("A", 0, 0, 100, 100)
        note = text_entry("ent:text:1", "名称：左盒盒背灰板 材料：灰板 1500g", (50, 50))
        doc = self.extract([piece], [note])
        row = self.part(doc, "cmp:A")
        self.assertIsNone(row.get("thickness_mm"), "没给材料表就不许推（Spec §2.3）")

    def test_a5_deterministic(self):
        piece = rect("A", 0, 0, 100, 100)
        note = text_entry("ent:text:1", "名称：左盒盒背灰板 材料：灰板 1500g", (50, 50))
        first = self.extract([piece], [note], material_table=MATERIAL_TABLE)
        second = self.extract([piece], [note], material_table=MATERIAL_TABLE)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


# --------------------------------------------------------------------------- #
# B 组：料厚不许跨材料串味
# --------------------------------------------------------------------------- #
def contamination_pieces():
    """面纸件 + 灰板件成组注记互相在 300mm 半径内（真图 DWG-P31 的同形夹具）。"""
    first = rect("FACE", 0, 0, 100, 100)
    second = rect("GREY", 200, 0, 100, 100)
    texts = [text_entry("ent:text:1", "名称：右盖面纸 材料：225G铜版底PET光银", (50, 50)),
             text_entry("ent:text:2", "名称：内盒2灰板 材料：2mm灰板", (250, 50))]
    return [first, second], texts


class BNoContamination(ThicknessCase):
    def test_b1_face_paper_never_takes_grey_board_thickness(self):
        pieces, texts = contamination_pieces()
        doc = self.extract(pieces, texts)
        face = self.part(doc, "cmp:FACE")
        self.assertIn("PET", face.get("material") or "", "夹具前提：这一件是 PET 面纸")
        self.assertIsNone(face.get("thickness_mm"),
                          "面纸不许吃到『内盒2灰板』的 2mm（Spec §2.2）")
        conflicts = (face.get("attribution") or {}).get("thickness_material_conflict") or []
        self.assertTrue(conflicts, "拒绝采用必须留冲突记录（Spec §2.2）")
        self.assertEqual(CONFLICT_REASON, conflicts[0].get("reason"))
        self.assertTrue(conflicts[0].get("note_ref"), "冲突必须能回查注记（note_ref）")
        self.assertTrue(conflicts[0].get("note_text"))

    def test_b2_same_material_note_still_applies(self):
        pieces, texts = contamination_pieces()
        doc = self.extract(pieces, texts)
        grey = self.part(doc, "cmp:GREY")
        self.assertEqual(2.0, grey.get("thickness_mm"), "灰板件照旧采用 2mm 灰板注记（防误伤）")
        self.assertFalse((grey.get("attribution") or {}).get("thickness_material_conflict"))

    def test_b3_explicit_thickness_without_material_word_is_not_a_conflict(self):
        piece = rect("A", 0, 0, 100, 100)
        notes = [text_entry("ent:text:1", "名称：右盖面纸 材料：225G铜版底PET光银", (50, 50)),
                 text_entry("ent:text:2", "厚度2MM", (50, 50))]
        doc = self.extract([piece], notes)
        row = self.part(doc, "cmp:A")
        self.assertEqual(2.0, row.get("thickness_mm"),
                         "不带材质词的显式厚度照旧生效（Spec §2.2）")


# --------------------------------------------------------------------------- #
# C 组：指标（新键 + 既有键不回归）
# --------------------------------------------------------------------------- #
class CMetrics(ThicknessCase):
    def test_c1_summary_carries_the_four_new_keys(self):
        piece = rect("A", 0, 0, 100, 100)
        note = text_entry("ent:text:1", "名称：左盒盒背灰板 材料：灰板 1500g", (50, 50))
        doc = self.extract([piece], [note], material_table=MATERIAL_TABLE)
        summary = self.module.summarize(doc)
        for key in NEW_SUMMARY_KEYS:
            self.assertIn(key, summary, "summary 必须带 %s（Spec §2.4）" % key)

    def test_c2_known_plus_unknown_accounts_for_every_part(self):
        pieces, texts = contamination_pieces()
        doc = self.extract(pieces, texts)
        summary = self.module.summarize(doc)
        total = len(summary.get("parts") or [])
        self.assertEqual(total,
                         (summary.get("thickness_known_total") or 0)
                         + (summary.get("thickness_unknown_total") or 0),
                         "料厚的两本账必须加起来等于零件总数（Spec §2.4）")
        self.assertGreaterEqual(summary.get("thickness_conflict_total") or 0, 1)

    def test_c3_existing_keys_survive(self):
        piece = rect("A", 0, 0, 100, 100)
        doc = self.extract([piece])
        summary = self.module.summarize(doc)
        for key in ("closed_ratio", "material_known_ratio", "thickness_known_ratio",
                    "processable_ratio", "solid_ok_ratio", "attribution_kind_mix",
                    "unprocessable_reason_mix", "size_source_mix"):
            self.assertIn(key, summary, "既有键 %s 不许丢（Spec §2.4 冻结面）" % key)


# --------------------------------------------------------------------------- #
# D 组：人工补料厚的生产入口
# --------------------------------------------------------------------------- #
class DManualThickness(ThicknessCase):
    def test_d1_pure_function_exists_and_stamps_manual_source(self):
        setter = getattr(self.module, "set_manual_thickness", None)
        self.assertTrue(callable(setter), "packaging_parts 必须提供 set_manual_thickness()（Spec §2.5）")
        piece = rect("A", 0, 0, 100, 100)
        row = self.part(self.extract([piece]), "cmp:A")
        updated = setter(row, 1.8, bound_by="PE1", reason="图纸未标料厚")
        self.assertEqual(1.8, updated.get("thickness_mm"))
        source = updated.get("thickness_source") or {}
        self.assertEqual(MANUAL_KIND, source.get("kind"))
        self.assertEqual("PE1", source.get("bound_by"))
        self.assertIsNone(row.get("thickness_mm"), "不许原地改入参（Spec §2.5）")
        with self.assertRaises(ValueError):
            setter(row, 0, bound_by="PE1")

    def test_d2_persistence_helpers_exist(self):
        for name in ("save_part_thickness", "load_part_thickness"):
            self.assertTrue(callable(getattr(self.module, name, None)),
                            "packaging_parts 必须提供 %s()（Spec §2.5）" % name)

    def test_d3_routes_and_frontend_entry_are_wired(self):
        main_text = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("PACKAGING_PART_THICKNESS_PATH", main_text,
                      "main.py 必须注册补料厚路由常量（Spec §2.5）")
        self.assertIn("BOX_MATCH_DECIDE_ROLES", main_text,
                      "写权限必须直接引用既有角色常量（Spec §2.5）")
        app_text = APP_JS.read_text(encoding="utf-8", errors="replace")
        self.assertIn("/thickness", app_text, "app.js 的零件树必须有补料厚调用（Spec §2.5）")

    def test_d4_source_kind_closed_set_is_documented_in_code(self):
        text = PARTS_PY.read_text(encoding="utf-8", errors="replace")
        for kind in THICKNESS_KINDS:
            self.assertIn(kind, text, "来源闭集必须含 %s（Spec §2.4）" % kind)


# --------------------------------------------------------------------------- #
# E 组：真样本（默认不跑）
# --------------------------------------------------------------------------- #
class RealSampleThickness(ThicknessCase):
    irs = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.environ.get("CPQ_DWG_REAL_SAMPLES") != "1":
            raise unittest.SkipTest("未设置 CPQ_DWG_REAL_SAMPLES=1：真实样本组默认不跑（Spec §5）")
        try:
            converter = importlib.import_module("tech_app.backend.services.cad_converter")
            capability = converter.capability()
        except Exception as exc:                                       # noqa: BLE001
            raise unittest.SkipTest("转换器适配层不可用：%s: %s" % (type(exc).__name__, exc))
        if not capability.get("available") or capability.get("simulated"):
            raise unittest.SkipTest("没有可用的真实转换器：%r" % capability.get("message"))
        missing = [name for name in (WINE_BOX, ROUND_BOX) if not (SAMPLES_DIR / name).is_file()]
        if missing:
            raise unittest.SkipTest("样本不在本机：缺少 %s" % "、".join(missing))
        if not SAMPLE_TOOL.is_file():
            raise unittest.SkipTest("缺少 tech_app/tools/dwg_sample_e2e.py")
        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="cpq-dwg-thickness-"))
        try:
            cad_ir = importlib.import_module("tech_app.backend.services.cad_ir")
            for name in (WINE_BOX, ROUND_BOX):
                out = cls.workspace / name.replace(".dwg", "")
                completed = subprocess.run(
                    [sys.executable, str(SAMPLE_TOOL), "--sample", str(SAMPLES_DIR / name),
                     "--out", str(out), "--json"],
                    capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
                text = (completed.stdout or "").strip()
                payload = json.loads(text[text.find("{"):]) if text else {}
                dxf = pathlib.Path(str(payload.get("dxf_path") or ""))
                if not dxf.is_file():
                    raise unittest.SkipTest("%s 转换未产出 DXF（rc=%s）"
                                            % (name, completed.returncode))
                cls.irs[name] = cad_ir.parse_dxf(dxf.read_bytes(), filename=dxf.name,
                                                 source={"kind": "dwg_2d", "attachment_name": name})
        except unittest.SkipTest:
            raise
        except Exception as exc:                                       # noqa: BLE001
            raise unittest.SkipTest("真实样本转换/解析失败：%s: %s" % (type(exc).__name__, exc))

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "workspace", None) is not None:
            shutil.rmtree(cls.workspace, ignore_errors=True)

    def doc_of(self, name):
        doc = self.module.extract(self.irs[name])
        return doc, self.module.summarize(doc)

    def test_e1_wine_box_reports_conflicts_and_never_invents_thickness(self):
        # 2026-09-22 修订（Spec `packaging-parts-component-chaining.md` 落地后，`酒盒.dwg` 的分量数
        # 402 → 1163，零件表随之变化）：原按旧分量集写死的"至少 2 件串味"变成实测 1 件。
        # 计数是**样本相关**的，契约本身是"串味必须被识别、且不许有残留串味"，所以这条改成
        # "≥1 且逐件复核没有残留"，比原来的写死数字更强。
        doc, summary = self.doc_of(WINE_BOX)
        self.assertGreaterEqual(summary.get("thickness_conflict_total") or 0, 1,
                                "酒盒真图上至少要识别出 1 件跨材料串味（Spec §2.2）")
        residual = []
        for row in doc["parts"]:
            source = row.get("thickness_source") or {}
            if row.get("thickness_mm") is None:
                continue
            self.assertIn(source.get("kind"), THICKNESS_KINDS,
                          "%s 的料厚来源必须在闭集内（Spec §2.4）" % row.get("part_code"))
            if source.get("kind") not in ("part_note", "group_note"):
                continue
            note_words = {word for word in self.module.MATERIAL_KEYWORDS
                          if word in (source.get("text") or "")}
            part_words = {word for word in self.module.MATERIAL_KEYWORDS
                          if word in (row.get("material") or "")}
            if note_words and part_words and not (note_words & part_words):
                residual.append({"part_code": row.get("part_code"), "material": row.get("material"),
                                 "note": source.get("text"), "note_words": sorted(note_words)})
        self.assertEqual([], residual[:5], "这些件的料厚仍然取自别的材料的注记（Spec §2.2）：%s"
                         % residual[:5])
        self.assertGreaterEqual(summary.get("thickness_unknown_total") or 0, 40,
                                "不许靠猜把料厚填满（Spec §3 E 组）")

    def test_e2_round_box_outline_unchanged(self):
        doc, _summary = self.doc_of(ROUND_BOX)
        self.assertGreaterEqual(len(doc.get("parts") or []), 9)
