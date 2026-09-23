"""红测：语义层候选被上限截断时，必须说得出"上限是多少、两本书各丢了多少"。

Spec：`docs/specs/packaging-semantics-candidate-truncation-must-be-counted.md`

现状缺口（真实跑出来的，不是推断；本机 LibreDWG 真转换两份真刀模图）：
  · `boundary_candidate_total` / `hole_total` 上有上限（默认
    `PACKAGING_SEMANTICS_MAX_CANDIDATES=200`），但它们写在 `stats` 里读起来就是"一共这么多"：
    实测 `酒盒.dwg` 真值 5600 / 642，`圆盘盒.dwg` 真值 6087 / 222，两处都只列 200；
  · `geometry_semantics.build_geometry()` 明明算出了 `truncated`（两本书被丢掉的条数之和：
    5842 / 5909），`packaging_semantics/__init__.py:144` 只把它当布尔用，计数当场丢掉 ——
    `outline` / `stats` / 警告里都查不到"丢了多少"；
  · 那条 `PACKAGING_SEMANTICS_CANDIDATES_TRUNCATED` 警告的 `message` 里一个数字都没有；
  · `truncated` 还把两本书合成一个数（`酒盒.dwg` 5400 / 442 与 `圆盘盒.dwg` 5887 / 22 没法区分）。

纪律：纯函数（合成 CAD IR + `analyze()`）+ 真实样本（`CPQ_DWG_REAL_SAMPLES=1` 才真转换，样本只读、
产物只写临时目录）；不连 PG / 34、不起服务、不发 HTTP、不写业务数据。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.packaging_semantics"
ENV_CAP = "PACKAGING_SEMANTICS_MAX_CANDIDATES"
DEFAULT_CAP = 200
TRUNC_WARNING = "PACKAGING_SEMANTICS_CANDIDATES_TRUNCATED"
TRUNCATED_KEYS = ("candidate_cap", "boundary_candidates_dropped_total", "holes_dropped_total")
FROZEN_OUTLINE_KEYS = ("boundary_candidates", "holes", "rejected", "bleed_candidates", "windows",
                       "panel")
#: Spec §2.4 —— `stats` 的键集一个字不许改（本批是**加**披露，不是改这两个键的含义）。
FROZEN_STATS_KEYS = {"layer_total", "cut_layer_total", "crease_layer_total",
                     "boundary_candidate_total", "hole_total", "conflict_total",
                     "unresolved_total", "box_candidate_total"}
FROZEN_REQUIRED_KEYS = {"semantics_version", "semantics_id", "semantics_hash", "source", "layers",
                        "roles_summary", "outline", "dimensions", "texts", "box_candidates",
                        "fields", "unresolved", "model_assist", "warnings", "stats", "reviewable"}
FROZEN_SEMANTICS_VERSION = "packaging-semantics/1"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "cad_ir"
SEMANTICS_DIR = ROOT / "tech_app" / "backend" / "services" / "packaging_semantics"
SOURCE_FILES = {"__init__": SEMANTICS_DIR / "__init__.py",
                "geometry": SEMANTICS_DIR / "geometry_semantics.py"}
SAMPLE_TOOL = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"


def fixture_module():
    """按路径加载夹具构造器（不 import `tests` 包，避免依赖 __init__.py）。"""
    path = FIXTURE_DIR / "build_fixtures.py"
    spec = importlib.util.spec_from_file_location("cpq_cad_ir_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def semantics_module():
    return importlib.import_module(PKG)


def analyze(ir, *, cap=None):
    """跑一次 `analyze()`；`cap=None` 表示按环境变量默认（本机若设了就临时摘掉再还原）。"""
    saved = os.environ.pop(ENV_CAP, None)
    try:
        if cap is not None:
            os.environ[ENV_CAP] = str(cap)
        return semantics_module().analyze(ir)
    finally:
        os.environ.pop(ENV_CAP, None)
        if saved is not None:
            os.environ[ENV_CAP] = saved


def has_number(text, value):
    """`text` 里是否出现这个十进制数（前后不许再连着数字，免得 450 里"命中"50）。"""
    return re.search(r"(?<!\d)%d(?!\d)" % int(value), str(text)) is not None


def trunc_warnings(doc):
    return [row for row in (doc.get("warnings") or [])
            if isinstance(row, dict) and row.get("code") == TRUNC_WARNING]


def candidates_ir(fx, *, candidates, holes, prefix="C"):
    """合成 CAD IR：`candidates` 条闭合轮廓（面积递增）+ `holes` 个孔位。"""
    outlines, entities = [], []
    for index in range(candidates):
        handle = "%s%04X" % (prefix, index)
        width, height = 100 + index, 60 + index
        outlines.append(fx.outline("out:model:" + handle, "ent:model:" + handle, "0",
                                   [0, 0, width, height], float(2 * (width + height)),
                                   float(width * height)))
        entities.append(fx.entity(handle, "LWPOLYLINE", "0", closed=True,
                                  bbox=[0, 0, width, height], length=float(2 * (width + height)),
                                  area=float(width * height)))
    hole_rows = [fx.hole("ent:model:H%04X" % index, "circle", (index, index), 2.0 + index)
                 for index in range(holes)]
    return fx.make(
        "truncation_%d_%d" % (candidates, holes),
        layers=[fx.layer("0", entity_count=len(entities))],
        entities=entities,
        geometry={"closed_outlines": outlines, "open_outlines": [], "components": [],
                  "holes": hole_rows, "repeated_groups": [], "overlaps": [], "tolerance": 1e-6},
    )


def over_cap_ir(fx):
    """250 条候选 + 210 个孔位 → 默认上限 200 下两本书都被截断（丢 50 / 10）。"""
    return candidates_ir(fx, candidates=250, holes=210)


def under_cap_ir(fx):
    """10 条候选 + 3 个孔位 → 不触顶。"""
    return candidates_ir(fx, candidates=10, holes=3)


class CandidateTruncationCase(unittest.TestCase):
    maxDiff = None

    def fixtures(self):
        return fixture_module()

    # ------------------------------------------------------------- T1 / T2
    def test_t1_capped_run_counts_both_books(self):
        doc = analyze(over_cap_ir(self.fixtures()))
        outline = doc.get("outline") or {}
        for key in TRUNCATED_KEYS:
            self.assertIn(key, outline,
                          "截断后 `outline` 必须给出 %r（Spec §2.2）：上线就该能读出"
                          "「上限多少、两本书各丢多少」" % key)
        self.assertEqual(outline["candidate_cap"], DEFAULT_CAP)
        self.assertEqual(outline["boundary_candidates_dropped_total"], 50)
        self.assertEqual(outline["holes_dropped_total"], 10)
        self.assertEqual(doc["stats"]["boundary_candidate_total"], DEFAULT_CAP,
                         "`stats` 口径不变：仍是「列出来的条数」（Spec §2.4）")
        self.assertEqual(doc["stats"]["hole_total"], DEFAULT_CAP)

    def test_t2_uncapped_run_says_zero_and_stays_quiet(self):
        doc = analyze(under_cap_ir(self.fixtures()))
        outline = doc.get("outline") or {}
        for key in TRUNCATED_KEYS:
            self.assertIn(key, outline,
                          "没触顶时键也必须存在（Spec §2.2）：%r 读不到就分不出"
                          "「没截断」与「这一版还没接线」" % key)
        self.assertEqual(outline["candidate_cap"], DEFAULT_CAP)
        self.assertEqual(outline["boundary_candidates_dropped_total"], 0)
        self.assertEqual(outline["holes_dropped_total"], 0)
        self.assertEqual([], trunc_warnings(doc),
                         "没截断就不许出这条警告（Spec §2.3）：警告不许为披露而常驻")

    # ------------------------------------------------------------------ T3
    def test_t3_warning_carries_the_numbers(self):
        doc = analyze(over_cap_ir(self.fixtures()))
        rows = trunc_warnings(doc)
        self.assertEqual(len(rows), 1, "触顶时这条警告必须出现且只出现一次")
        row = rows[0]
        self.assertEqual(row.get("dropped_total"), 60,
                         "警告项必须带 `dropped_total`（两本书之和，Spec §2.3）")
        message = str(row.get("message") or "")
        for value in (DEFAULT_CAP, 50, 10):
            self.assertTrue(has_number(message, value),
                            "警告正文必须让人读出 %d（上限与两本书各丢多少，Spec §2.3）：%r"
                            % (value, message))

    # ------------------------------------------------------------------ T4
    def test_t4_listed_plus_dropped_equals_the_real_total(self):
        ir = over_cap_ir(self.fixtures())
        capped = analyze(ir)
        full = analyze(ir, cap=100000)
        self.assertEqual((full["outline"] or {}).get("boundary_candidates_dropped_total"), 0,
                         "上限足够大时不该有东西被丢掉")
        self.assertEqual((full["outline"] or {}).get("holes_dropped_total"), 0)
        pairs = (("boundary_candidates", "boundary_candidates_dropped_total", "轮廓候选"),
                 ("holes", "holes_dropped_total", "孔位"))
        for listed, dropped, title in pairs:
            self.assertEqual(
                len((capped["outline"] or {}).get(listed) or []) + (capped["outline"] or {})[dropped],
                len((full["outline"] or {}).get(listed) or []),
                "「列出的 + 丢掉的 == 真值」对不上（%s，Spec §2.1/§2.2）" % title)

    # ------------------------------------------------------------------ T5
    def test_t5_effective_cap_is_readable(self):
        ir = candidates_ir(self.fixtures(), candidates=20, holes=0)
        doc = analyze(ir, cap=7)
        outline = doc.get("outline") or {}
        self.assertEqual(outline.get("candidate_cap"), 7,
                         "生效上限必须披露：否则 200 与「恰好 200 条」永远分不出来（Spec §2.2）")
        self.assertEqual(outline.get("boundary_candidates_dropped_total"), 13)
        default = analyze(ir)
        self.assertEqual((default.get("outline") or {}).get("candidate_cap"), DEFAULT_CAP,
                         "没设环境变量时上限仍是 200（既有口径不变）")

    # ------------------------------------------------------------------ T7
    def test_t7_frozen_surface_is_untouched(self):
        doc = analyze(over_cap_ir(self.fixtures()))
        self.assertEqual(set(doc["stats"]), FROZEN_STATS_KEYS, "`stats` 键集不许改（Spec §2.4）")
        self.assertTrue(FROZEN_REQUIRED_KEYS.issubset(set(doc)),
                        "顶层必需键缺了：%s" % sorted(FROZEN_REQUIRED_KEYS - set(doc)))
        self.assertEqual(doc["semantics_version"], FROZEN_SEMANTICS_VERSION)
        outline = doc["outline"]
        for key in FROZEN_OUTLINE_KEYS:
            self.assertIn(key, outline, "`outline` 既有键 %r 不许消失" % key)
        self.assertEqual(doc["stats"]["boundary_candidate_total"],
                         len(outline["boundary_candidates"]),
                         "`boundary_candidate_total` 仍是「列出来的条数」（Spec §2.4）")
        self.assertEqual(doc["stats"]["hole_total"], len(outline["holes"]))
        self.assertEqual(len(outline["boundary_candidates"]), DEFAULT_CAP,
                         "截断口径不变：触顶时列表长度就是上限")
        order = [(0 if row["is_closed"] else 1, -row["area"], row["outline_id"])
                 for row in outline["boundary_candidates"]]
        self.assertEqual(order, sorted(order),
                         "候选排序口径 `(is_closed desc, -area, outline_id)` 不许改（Spec §2.4）")

    # ------------------------------------------------------------------ T8
    def test_t8_analyze_wires_the_counts_through(self):
        init_src = SOURCE_FILES["__init__"].read_text(encoding="utf-8")
        for key in TRUNCATED_KEYS:
            self.assertIn(key, init_src,
                          "`%s` 必须出现在 `packaging_semantics/__init__.py` 里（Spec §2.2/§2.3）："
                          "只留 `if geometry.get(\"truncated\")` 就是把计数丢掉" % key)
        geometry_src = SOURCE_FILES["geometry"].read_text(encoding="utf-8")
        for key in ("boundary_candidates_dropped", "holes_dropped"):
            self.assertIn(key, geometry_src,
                          "`build_geometry()` 必须交回 `%s`（Spec §2.1）" % key)


class RealSampleTruncation(unittest.TestCase):
    """T6：两份真刀模图的真值 —— 两本书都被截断，账必须对得上。"""

    workspace = None
    docs = {}

    @classmethod
    def setUpClass(cls):
        if os.environ.get("CPQ_DWG_REAL_SAMPLES") != "1":
            raise unittest.SkipTest("未设置 CPQ_DWG_REAL_SAMPLES=1：真实样本组默认不跑（Spec §5）")
        try:
            converter = importlib.import_module("tech_app.backend.services.cad_converter")
            capability = converter.capability()
        except Exception as exc:                                     # noqa: BLE001
            raise unittest.SkipTest("转换器适配层不可用：%s: %s" % (type(exc).__name__, exc))
        if not capability.get("available") or capability.get("simulated"):
            raise unittest.SkipTest("没有可用的真实转换器：%r" % capability.get("message"))
        missing = [name for name in (WINE_BOX, ROUND_BOX) if not (SAMPLES_DIR / name).is_file()]
        if missing:
            raise unittest.SkipTest("样本不在本机：缺少 %s" % "、".join(missing))
        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="cpq-truncation-"))
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
                ir = cad_ir.parse_dxf(dxf.read_bytes(), filename=dxf.name,
                                      source={"kind": "dwg_2d", "attachment_name": name})
                cls.docs[name] = (ir, analyze(ir), analyze(ir, cap=100000))
        except unittest.SkipTest:
            raise
        except Exception as exc:                                     # noqa: BLE001
            raise unittest.SkipTest("真实样本转换/解析失败：%s: %s" % (type(exc).__name__, exc))

    @classmethod
    def tearDownClass(cls):
        if cls.workspace is not None:
            shutil.rmtree(cls.workspace, ignore_errors=True)

    def test_t6_real_samples_report_what_was_dropped(self):
        for name in (WINE_BOX, ROUND_BOX):
            _ir, capped, full = self.docs[name]
            outline = capped.get("outline") or {}
            stats = capped.get("stats") or {}
            for key in TRUNCATED_KEYS:
                self.assertIn(key, outline, "%s 的 `outline` 缺 %r（Spec §2.2）" % (name, key))
            self.assertEqual(stats.get("boundary_candidate_total"), outline.get("candidate_cap"),
                             "%s：`stats` 上那个数就是上限，必须与 `candidate_cap` 对上" % name)
            self.assertGreaterEqual(outline.get("boundary_candidates_dropped_total") or 0, 1000,
                                    "%s：真值远超上限，丢掉的条数必须说出来" % name)
            self.assertGreaterEqual(outline.get("holes_dropped_total") or 0, 1,
                                    "%s：孔位也被截断过，必须说出来" % name)
            rows = trunc_warnings(capped)
            self.assertEqual(len(rows), 1, "%s：触顶必须带一条截断警告" % name)
            row = rows[0]
            self.assertEqual(row.get("dropped_total"),
                             outline["boundary_candidates_dropped_total"]
                             + outline["holes_dropped_total"],
                             "%s：警告上的合计必须等于两本书之和" % name)
            for value in (outline["candidate_cap"],
                          outline["boundary_candidates_dropped_total"],
                          outline["holes_dropped_total"]):
                self.assertTrue(has_number(row.get("message"), value),
                                "%s：警告正文里必须有 %s" % (name, value))
            pairs = (("boundary_candidates", "boundary_candidates_dropped_total", "轮廓候选"),
                     ("holes", "holes_dropped_total", "孔位"))
            for listed, dropped, title in pairs:
                self.assertEqual(
                    len((capped.get("outline") or {}).get(listed) or []) + outline[dropped],
                    len((full.get("outline") or {}).get(listed) or []),
                    "%s：%s「列出的 + 丢掉的 == 不设上限的真值」对不上" % (name, title))
            self.assertGreaterEqual(
                len((full.get("outline") or {}).get("boundary_candidates") or []), DEFAULT_CAP)


if __name__ == "__main__":
    unittest.main()
