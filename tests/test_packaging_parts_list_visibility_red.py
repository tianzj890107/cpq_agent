"""红测：图纸零件列表的可见性与"种类"。

Spec：`docs/specs/packaging-parts-list-visibility-and-kinds.md`

现状缺口（9-22 实测，本机 `酒盒.dwg`）：
  · 端点相接分组后 1163 分量 → 过滤 900 → **263 件真零件** → 但 `parts[:max_parts]` 只留 **64 件**，
    另外 **199 件在文档里根本不存在**（`stats.part_total=64` / `truncated=199`，两者相加才推得出 263）；
  · `kind_total` / `kind_key` / `kept_total` 三个键都不存在 —— 用户看不到"这 64 件其实是 22 种形状"；
  · `repeat_of` 的键是 `(长, 宽, entity_total)`，**不含形状**：同长宽的矩形与 L 形会被算成同一件；
  · 面板只有一句"还有 199 件未列出（只显示前 64 件）"，既翻不到下一页，也不能按种类看。

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
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"

KIND_KEYS = ("kind_key", "kind_index")
STATS_KEYS = ("kept_total", "kind_total")
SUMMARY_KEYS = ("kept_total", "listed_total", "kind_total", "repeat_total")
ROUTE_TOKENS = ("has_more", "kind_total")
APP_TOKENS = ("kind_index", "has_more", "offset")
FROZEN_ROW_KEYS = ("part_code", "name", "role", "repeat_of", "outline_status",
                   "unfolded_length_mm", "unfolded_width_mm", "material", "thickness_mm")


def rect(tag, x0, y0, w=60.0, h=60.0, layer="DESIGN"):
    corners = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    eids, entities = [], []
    for index in range(4):
        start, end = corners[index], corners[(index + 1) % 4]
        eid = "ent:model:%s-%d" % (tag, index)
        eids.append(eid)
        entities.append({"entity_id": eid, "type": "LINE", "layer": layer,
                         "attributes": {"start": [float(start[0]), float(start[1])],
                                        "end": [float(end[0]), float(end[1])]}})
    return ({"component_id": "cmp:%s" % tag, "entity_ids": eids,
             "bbox": [float(x0), float(y0), float(x0 + w), float(y0 + h)]}, entities)


def ell(tag, x0, y0, w=100.0, h=100.0, layer="DESIGN"):
    """L 形（6 条线闭环），bbox 与同尺寸矩形**完全一样**，轮廓不同。"""
    corners = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h / 2), (x0 + w / 2, y0 + h / 2),
               (x0 + w / 2, y0 + h), (x0, y0 + h)]
    eids, entities = [], []
    for index in range(len(corners)):
        start, end = corners[index], corners[(index + 1) % len(corners)]
        eid = "ent:model:%s-%d" % (tag, index)
        eids.append(eid)
        entities.append({"entity_id": eid, "type": "LINE", "layer": layer,
                         "attributes": {"start": [float(start[0]), float(start[1])],
                                        "end": [float(end[0]), float(end[1])]}})
    return ({"component_id": "cmp:%s" % tag, "entity_ids": eids,
             "bbox": [float(x0), float(y0), float(x0 + w), float(y0 + h)]}, entities)


def make_ir(pieces):
    components, entities = [], []
    for component, rows in pieces:
        components.append(component)
        entities.extend(rows)
    return {"ir_version": "cad-ir/1", "ir_id": "ir:test", "ir_hash": "hash",
            "parser": {"name": "test"}, "source": {"kind": "dxf_2d", "attachment_name": "t.dxf"},
            "units": {"drawing_units": "mm", "scale_to_mm": 1.0, "unit_status": "confirmed",
                      "unit_confidence": 1.0, "candidates": []},
            "layers": [], "entities": entities, "texts": [],
            "dimensions": [], "geometry": {"components": components},
            "evidence": {}, "unsupported": [], "warnings": [], "stats": {}}


def grid(count, *, size=60.0, columns=10):
    pieces = []
    for index in range(count):
        row, column = divmod(index, columns)
        pieces.append(rect("G%03d" % index, column * (size + 20.0), row * (size + 20.0),
                           size, size))
    return pieces


class ListCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module(PKG)

    def extract(self, pieces, options=None):
        return self.module.extract(make_ir(pieces), options=options)


# --------------------------------------------------------------------------- #
# A 组：不许因为页大小丢件
# --------------------------------------------------------------------------- #
class ANoTruncation(ListCase):
    def test_a1_every_kept_part_stays_in_the_document(self):
        doc = self.extract(grid(70))
        stats = doc.get("stats") or {}
        self.assertEqual(70, len(doc.get("parts") or []),
                         "70 件真零件必须全部留在文档里（Spec §2.1）")
        self.assertEqual(70, int(stats.get("part_total") or 0))
        self.assertEqual(0, int(stats.get("truncated") or 0),
                         "未显式传 max_parts 时不许截断（Spec §2.1）")
        self.assertEqual(70, int(stats.get("kept_total") or 0),
                         "kept_total 必须把「过滤后剩多少」显式化（Spec §2.1）")

    def test_a2_explicit_max_parts_still_truncates(self):
        doc = self.extract(grid(70), options={"max_parts": 10})
        self.assertEqual(10, len(doc.get("parts") or []), "显式传 max_parts 时旧行为必须保留（Spec §2.1）")
        self.assertEqual(60, int((doc.get("stats") or {}).get("truncated") or 0))

    def test_a3_part_codes_are_continuous_over_the_whole_document(self):
        doc = self.extract(grid(70))
        codes = [row.get("part_code") for row in doc.get("parts") or []]
        self.assertEqual(len(set(codes)), len(codes), "part_code 不许重复")
        self.assertEqual(codes, sorted(codes), "part_code 必须保持全量顺序（Spec §2.1）")


# --------------------------------------------------------------------------- #
# B 组：种类（kind_key / kind_index / kind_total）
# --------------------------------------------------------------------------- #
class BKinds(ListCase):
    def fixture(self):
        pieces = [rect("S1", 0, 0), rect("S2", 500, 0), rect("S3", 1000, 0),
                  rect("D1", 1500, 0, 60.0, 80.0)]
        return pieces

    def test_b1_rows_carry_kind_key_and_index(self):
        doc = self.extract(self.fixture())
        for row in doc.get("parts") or []:
            for key in KIND_KEYS:
                self.assertIn(key, row, "每件必须带 %s（Spec §2.2）" % key)
                self.assertTrue(row.get(key), "%s 不许为空" % key)

    def test_b2_identical_parts_share_one_kind(self):
        doc = self.extract(self.fixture())
        stats = doc.get("stats") or {}
        self.assertEqual(2, int(stats.get("kind_total") or 0),
                         "3 件同形同尺寸 + 1 件不同 = 2 种（Spec §2.2）")
        by_code = {row.get("component_id"): row for row in doc.get("parts") or []}
        same = [by_code["cmp:S%d" % index] for index in (1, 2, 3)]
        self.assertEqual(1, len({row["kind_key"] for row in same}), "同种必须同 kind_key")
        self.assertEqual(1, len({row["kind_index"] for row in same}), "同种必须同 kind_index")
        self.assertNotEqual(same[0]["kind_key"], by_code["cmp:D1"]["kind_key"])
        self.assertNotEqual(same[0]["kind_index"], by_code["cmp:D1"]["kind_index"])

    def test_b3_shape_participates_in_the_fingerprint(self):
        doc = self.extract([rect("R", 0, 0, 100.0, 100.0), ell("L", 500, 0, 100.0, 100.0)])
        lengths = {row.get("unfolded_length_mm") for row in doc.get("parts") or []}
        widths = {row.get("unfolded_width_mm") for row in doc.get("parts") or []}
        self.assertEqual(1, len(lengths), "夹具前提：两件外接长宽必须一样")
        self.assertEqual(1, len(widths), "夹具前提：两件外接长宽必须一样")
        self.assertEqual(2, int((doc.get("stats") or {}).get("kind_total") or 0),
                         "同长宽、不同轮廓必须是两种（Spec §2.2）")

    def test_b4_kinds_are_deterministic_and_repeat_stays_inside_a_kind(self):
        first = self.extract(self.fixture())
        second = self.extract(self.fixture())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        by_code = {row["part_code"]: row for row in first.get("parts") or []}
        for row in first.get("parts") or []:
            repeat = row.get("repeat_of")
            if not repeat:
                continue
            self.assertIn(repeat, by_code, "repeat_of 必须指向文档里的件（Spec §2.2）")
            self.assertEqual(by_code[repeat].get("kind_key"), row.get("kind_key"),
                             "repeat_of 只允许出现在同 kind_key 之间（Spec §2.2）")


# --------------------------------------------------------------------------- #
# C 组：指标
# --------------------------------------------------------------------------- #
class CMetrics(ListCase):
    def test_c1_summary_exposes_list_and_kind_metrics(self):
        doc = self.extract(grid(70))
        summary = self.module.summarize(doc)
        for key in SUMMARY_KEYS:
            self.assertIn(key, summary, "summarize() 必须带 %s（Spec §2.3）" % key)
        self.assertEqual(len(doc.get("parts") or []), int(summary.get("listed_total") or 0))
        self.assertGreaterEqual(int(summary.get("repeat_total") or 0), 60,
                                "70 件里 69 件是同种重复（Spec §2.3）")

    def test_c2_frozen_row_keys_survive(self):
        row = (self.extract(grid(1)).get("parts") or [{}])[0]
        for key in FROZEN_ROW_KEYS:
            self.assertIn(key, row, "既有行键 %s 不许丢（Spec §4）" % key)


# --------------------------------------------------------------------------- #
# D 组：读接口与面板
# --------------------------------------------------------------------------- #
class DWiring(unittest.TestCase):
    def test_d1_route_supports_paging_and_kind_totals(self):
        text = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for token in ROUTE_TOKENS:
            self.assertIn(token, text, "读接口必须支持分页/全量真值：缺 %s（Spec §2.4）" % token)
        self.assertIn("offset", text)
        self.assertIn("limit", text)

    def test_d2_part_tree_folds_by_kind_and_can_load_more(self):
        text = APP_JS.read_text(encoding="utf-8", errors="replace")
        for token in APP_TOKENS:
            self.assertIn(token, text, "2.1 零件树必须能按种类折叠 + 继续加载：缺 %s（Spec §2.5）" % token)


# --------------------------------------------------------------------------- #
# E 组：真样本（默认不跑）
# --------------------------------------------------------------------------- #
class RealSampleList(ListCase):
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
        workspace = pathlib.Path(tempfile.mkdtemp(prefix="cpq-dwg-list-"))
        cls.workspace = workspace
        cache = workspace / "real.dxf"
        subprocess.run([tool, "-y", "-o", str(cache), str(sample)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.doc = cls.module.extract(cad_ir.parse_dxf(
            cache.read_bytes(), filename=cache.name,
            source={"kind": "dxf_2d", "attachment_name": sample.name}))

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "workspace", None) is not None:
            shutil.rmtree(cls.workspace, ignore_errors=True)

    def test_e1_all_real_parts_are_readable(self):
        stats = self.doc.get("stats") or {}
        total = int(stats.get("part_total") or 0)
        self.assertGreaterEqual(total, 200, "酒盒真零件实测 263 件（Spec §1）")
        self.assertEqual(total, len(self.doc.get("parts") or []),
                         "文档里的件数必须等于 part_total（Spec §2.1）")
        self.assertEqual(0, int(stats.get("truncated") or 0))

    def test_e2_real_kind_total_is_meaningful(self):
        stats = self.doc.get("stats") or {}
        kinds = int(stats.get("kind_total") or 0)
        self.assertGreaterEqual(kinds, 20, "酒盒实测至少 22 种形状（Spec §1）")
        self.assertLessEqual(kinds, int(stats.get("part_total") or 0))
