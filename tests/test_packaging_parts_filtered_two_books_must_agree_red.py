"""红测：被过滤的分量有三套口径在打架，必须自洽且单位可机械读出。

Spec：`docs/specs/packaging-parts-filtered-two-books-must-agree.md`

现状缺口（真实跑出来的，不是推断；本机 LibreDWG 真转换两份真刀模图 + 合成夹具）：
  · `filtered[].reason` 是 `reasons[0]`（跟着原因追加顺序走），而 `filtered_reason_mix` 走的是
    `FILTER_REASON_ACCOUNT_ORDER`（`area_over_max` 优先）—— 同一个词"主因"，两处两把尺：
      `酒盒.dwg`   按 reason 归并 = {edge_over_max: 12, area_under_min: 888}
                   mix          = {area_under_min: 888, area_over_max: 6, edge_over_max: 6}（6 行不一致）
      `圆盘盒.dwg` 按 reason 归并 = {edge_over_max: 69, area_under_min: 2919}
                   mix          = {area_under_min: 2919, area_over_max: 38, edge_over_max: 31}（38 行）
      合成 `parts_panels()` 的 `cmp:F`（`["edge_over_max", "area_over_max"]`）就是那一件。
  · 四个 `filtered_*_total`（一件可进多本）与 `filtered_total`（一件一次）不可相加，
    而键名里没有一处说得出：911 ≠ 900（酒盒，11 件多因）、3029 ≠ 2988（圆盘盒，41 件多因）。
  · 没有任何键说出这两本账的**单位**，也没说出 `Σ len(reasons)` 这个合计。

纪律：纯函数（合成 CAD IR + `extract()` / `summarize()`）+ 真实样本（`CPQ_DWG_REAL_SAMPLES=1`
才真转换，样本只读、产物只写临时目录）；不连 PG / 34、不起服务、不发 HTTP、不写业务数据。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import collections
import importlib
import importlib.util
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

PARTS_MOD = "tech_app.backend.services.packaging_parts"
PARTS_SRC = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "cad_ir"
SAMPLE_TOOL = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"
HITS_KEY = "filtered_reason_hits_total"
MIX_SCOPE_KEY = "filtered_reason_mix_scope"
TOTALS_SCOPE_KEY = "filtered_reason_totals_scope"
MIX_SCOPE = "primary_reason_per_part"
TOTALS_SCOPE = "reason_per_part"
TOTAL_KEYS = {"edge_over_max": "filtered_edge_over_max_total",
              "area_over_max": "filtered_area_over_max_total",
              "area_under_min": "filtered_area_under_min_total",
              "no_curve_entity": "filtered_no_curve_entity_total"}
ACCOUNT_ORDER = ("area_over_max", "edge_over_max", "area_under_min", "no_curve_entity")
REASON_CODES_FROZEN = ("edge_over_max", "area_over_max", "area_under_min", "no_curve_entity",
                       "no_components", "all_filtered", "no_unit")


def fixture_module():
    """按路径加载夹具构造器（不 import `tests` 包，避免依赖 __init__.py）。"""
    path = FIXTURE_DIR / "build_fixtures.py"
    spec = importlib.util.spec_from_file_location("cpq_cad_ir_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parts_module():
    return importlib.import_module(PARTS_MOD)


def synth_doc():
    return parts_module().extract(fixture_module().parts_panels(), None)


def rows_of(doc):
    return [row for row in (doc.get("filtered") or []) if isinstance(row, dict)]


def mix_of(doc):
    stats = doc.get("stats") or {}
    mix = stats.get("filtered_reason_mix")
    return dict(mix) if isinstance(mix, dict) else {}


def grouped_by_reason(doc):
    counter = collections.Counter(str(row.get("reason") or "") for row in rows_of(doc))
    return {key: value for key, value in sorted(counter.items())}


def four_totals(doc):
    stats = doc.get("stats") or {}
    return {reason: int(stats.get(key) or 0) for reason, key in TOTAL_KEYS.items()}


def expected_four(doc):
    counter = {reason: 0 for reason in TOTAL_KEYS}
    for row in rows_of(doc):
        for reason in row.get("reasons") or []:
            if reason in counter:
                counter[reason] += 1
    return counter


def hits_from_rows(doc):
    return sum(len(row.get("reasons") or []) for row in rows_of(doc))


def stats_of(doc):
    return doc.get("stats") or {}


class FilteredAccountsCase(unittest.TestCase):
    maxDiff = None

    # ------------------------------------------------------------------ S1
    def test_s1_row_reason_is_the_same_primary_reason_as_the_mix(self):
        doc = synth_doc()
        self.assertEqual(grouped_by_reason(doc), mix_of(doc),
                         "按 `filtered[].reason` 归并必须逐键逐值等于 `filtered_reason_mix`"
                         "（Spec §2.1）：同一个词「主因」不许有两把尺")
        by_component = {row["component_id"]: row.get("reason") for row in rows_of(doc)}
        self.assertEqual(by_component.get("cmp:F"), "area_over_max",
                         "`cmp:F` 的原因同时命中 edge_over_max 与 area_over_max，"
                         "主因按 `FILTER_REASON_ACCOUNT_ORDER` 是 area_over_max（Spec §2.1）")

    # ------------------------------------------------------------- S2 / S3
    def test_s2_hits_total_and_scopes_are_disclosed(self):
        stats = stats_of(synth_doc())
        for key in (HITS_KEY, MIX_SCOPE_KEY, TOTALS_SCOPE_KEY):
            self.assertIn(key, stats, "`stats` 必须说出两本账的单位与合计（Spec §2.2）：缺 %r" % key)
        self.assertEqual(stats[MIX_SCOPE_KEY], MIX_SCOPE)
        self.assertEqual(stats[TOTALS_SCOPE_KEY], TOTALS_SCOPE)
        doc = synth_doc()
        self.assertEqual(stats_of(doc)["filtered_total"], 3)
        self.assertEqual(sum(mix_of(doc).values()), 3,
                         "`filtered_reason_mix` 各项之和 == `filtered_total`（Spec §2.3 第 1 条）")
        self.assertEqual(stats_of(doc)[HITS_KEY], 5)
        self.assertEqual(sum(four_totals(doc).values()), stats_of(doc)[HITS_KEY],
                         "四个 `filtered_*_total` 之和必须等于 `%s`（Spec §2.3 第 2 条）" % HITS_KEY)
        self.assertEqual(hits_from_rows(doc), stats_of(doc)[HITS_KEY],
                         "`%s` 必须等于 `Σ len(row[\"reasons\"])`（Spec §2.3 第 2 条）" % HITS_KEY)

    def test_s3_hits_minus_filtered_equals_the_extra_hits(self):
        doc = synth_doc()
        stats = stats_of(doc)
        self.assertIn(HITS_KEY, stats, "`stats` 必须先说出合计（Spec §2.2）：缺 %r" % HITS_KEY)
        extra = sum(len(row.get("reasons") or []) - 1 for row in rows_of(doc))
        self.assertEqual(extra, 2, "合成夹具里两件多因（cmp:F 与 cmp:X）")
        self.assertEqual(stats[HITS_KEY] - stats["filtered_total"], extra,
                         "`hits - filtered_total` 必须等于多因件贡献的额外命中（Spec §2.3 第 3 条）")
        self.assertGreaterEqual(stats[HITS_KEY], stats["filtered_total"])

    # ------------------------------------------------------------------ S4
    def test_s4_summarize_exposes_the_three_keys(self):
        summary = parts_module().summarize(synth_doc())
        for key in (HITS_KEY, MIX_SCOPE_KEY, TOTALS_SCOPE_KEY):
            self.assertIn(key, summary, "读侧摘要必须透出 %r（Spec §2.4）" % key)

    # ------------------------------------------------------------------ S6
    def test_s6_four_totals_are_still_per_reason_counts(self):
        for doc in (synth_doc(),):
            self.assertEqual(four_totals(doc), expected_four(doc),
                             "四个 `filtered_*_total` 的数值口径不变：仍等于用 `filtered[]` 逐原因重算"
                             "（Spec §2.5，一个字不许改）")

    # ------------------------------------------------------------------ S7
    def test_s7_source_wires_the_primary_reason_function(self):
        module = ast.parse(PARTS_SRC.read_text(encoding="utf-8"))
        wired = []
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "append"):
                continue
            for arg in node.args:
                if not isinstance(arg, ast.Dict):
                    continue
                for key, value in zip(arg.keys, arg.values):
                    if isinstance(key, ast.Constant) and key.value == "reason":
                        wired.append(value)
        self.assertTrue(wired, "找不到写 `filtered[]` 的 `append({... \"reason\": …})`（Spec §2.1）")
        ok = [value for value in wired
              if isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
              and value.func.id == "_account_reason"]
        self.assertEqual(len(ok), len(wired),
                         "`filtered[].reason` 必须由 `_account_reason(reasons)` 产生"
                         "（今天写的是 `reasons[0]`，所以它跟 `filtered_reason_mix` 不是一把尺）")
        source = PARTS_SRC.read_text(encoding="utf-8")
        for key in (HITS_KEY, MIX_SCOPE_KEY, TOTALS_SCOPE_KEY):
            self.assertIn(key, source, "`%s` 必须出现在 `packaging_parts.py` 里（Spec §2.2）" % key)

    # ------------------------------------------------------------------ S8
    def test_s8_guards_on_rows_and_frozen_constants(self):
        parts = parts_module()
        self.assertEqual(tuple(parts.FILTER_REASON_ACCOUNT_ORDER), ACCOUNT_ORDER,
                         "主因优先级顺序不变（Spec §2.5）")
        self.assertEqual(tuple(parts.REASON_CODES), REASON_CODES_FROZEN,
                         "`REASON_CODES` 闭集与顺序不变（Spec §2.5）")
        doc = synth_doc()
        self.assertEqual(len(rows_of(doc)), stats_of(doc)["filtered_total"])
        for row in rows_of(doc):
            reasons = row.get("reasons") or []
            self.assertTrue(reasons, "每一行的 `reasons` 不许为空")
            self.assertEqual(len(reasons), len(set(reasons)),
                             "同一原因一件只记一次（Spec §2.3 第 4 条）：%r" % (reasons,))
        again = synth_doc()
        self.assertEqual(again.get("parts_hash"), doc.get("parts_hash"),
                         "`extract()` 必须仍是纯函数：同一份 IR 两次跑出同一个 parts_hash")


class RealSampleFilteredAccounts(unittest.TestCase):
    """S5：两份真图的真值 —— 混账必须能当场看出来。"""

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
        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="cpq-filtered-books-"))
        try:
            cad_ir = importlib.import_module("tech_app.backend.services.cad_ir")
            sem = importlib.import_module("tech_app.backend.services.packaging_semantics")
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
                cls.docs[name] = parts_module().extract(ir, sem.analyze(ir))
        except unittest.SkipTest:
            raise
        except Exception as exc:                                     # noqa: BLE001
            raise unittest.SkipTest("真实样本转换/解析失败：%s: %s" % (type(exc).__name__, exc))

    @classmethod
    def tearDownClass(cls):
        if cls.workspace is not None:
            shutil.rmtree(cls.workspace, ignore_errors=True)

    def test_s5_real_samples_reconcile(self):
        expected_total = {WINE_BOX: 900, ROUND_BOX: 2988}
        expected_hits = {WINE_BOX: 911, ROUND_BOX: 3029}
        expected_multi = {WINE_BOX: 11, ROUND_BOX: 41}
        for name in (WINE_BOX, ROUND_BOX):
            doc = self.docs[name]
            stats = stats_of(doc)
            self.assertEqual(stats.get("filtered_total"), expected_total[name],
                             "%s：`filtered_total` 口径不变（Spec §2.5）" % name)
            self.assertEqual(grouped_by_reason(doc), mix_of(doc),
                             "%s：按 `filtered[].reason` 归并必须等于 `filtered_reason_mix`（Spec §2.1）"
                             % name)
            self.assertEqual(sum(mix_of(doc).values()), stats["filtered_total"])
            self.assertEqual(stats.get(HITS_KEY), expected_hits[name],
                             "%s：合计必须等于 `Σ len(reasons)`（Spec §2.3 第 2 条）" % name)
            self.assertEqual(sum(four_totals(doc).values()), expected_hits[name])
            self.assertEqual(hits_from_rows(doc), expected_hits[name])
            multi = sum(1 for row in rows_of(doc) if len(row.get("reasons") or []) > 1)
            self.assertEqual(multi, expected_multi[name], "%s：多因件件数是可复核的" % name)
            extra = sum(len(row.get("reasons") or []) - 1 for row in rows_of(doc))
            self.assertEqual(stats[HITS_KEY] - stats["filtered_total"], extra,
                             "%s：差额必须等于多因件贡献的额外命中（Spec §2.3 第 3 条）" % name)
            self.assertGreater(stats[HITS_KEY], stats["filtered_total"],
                               "%s：两本账本来就不是一个数，产物必须说得出" % name)
            self.assertEqual(four_totals(doc), expected_four(doc),
                             "%s：四个 `filtered_*_total` 仍是逐原因件数（Spec §2.5）" % name)


if __name__ == "__main__":
    unittest.main()
