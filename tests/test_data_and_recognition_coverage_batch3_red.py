"""红测：真实数据与识别覆盖（批次 3）—— 区间审计 + 识别质量度量 + 路线编码诊断。

Spec：docs/specs/data-and-recognition-coverage-batch3.md

现状缺口（2026-10-09 实测，代码事实）：

  · T6 匹配代码**已支持**逐轴下限（`packaging_match._dimension_size()`；`size_guard` 红测 23 OK），
    本仓种子 `da_seed_packaging.BOX_TYPES` 12 行边界齐全、无倒挂；但**没有任何工具**能对生产
    `kb_packaging_box_type` 跑同一套不变量，也分不出「固定规格 / 可调尺寸 / 只能当参考」；
  · T14 全仓**没有**任何 accuracy / precision / recall / 留出图实现（grep 为空）：
    识别质量只有「在位性门禁」没有「度量」，且「有尺寸证据的比例」与「识别正确率」从未分开；
  · T16 只有只读桥 `da_process_routing.py`（本地未提交），没有离线诊断回答
    「名称/编码命中多少、多少步骤是孤儿、多少头是草稿或工时空」；
  · T13 已实现（导入器默认 dry-run、预检含 `box_type_missing_fit_clearance`），本文件只放两条守卫。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _module(dotted: str):
    try:
        return importlib.import_module(dotted)
    except Exception as exc:                                   # noqa: BLE001 - 交回用例判定
        return exc


def _box(code, l_min, l_max, w_min, w_max, h_min, h_max):
    return {"box_type_code": code, "name": code, "size_l_min": l_min, "size_l_max": l_max,
            "size_w_min": w_min, "size_w_max": w_max, "size_h_min": h_min, "size_h_max": h_max}


class T6RangeAudit(unittest.TestCase):
    """T6：区间数据的只读审计 —— 先查数据，不许拍脑袋改匹配代码。"""

    def mod(self):
        mod = _module("tech_app.tools.packaging_box_type_range_audit")
        if isinstance(mod, Exception):
            self.fail("缺 `tech_app/tools/packaging_box_type_range_audit.py`（Spec §2.1）：%r" % mod)
        return mod

    def test_t6_a_classify_fixed_adjustable_and_reference(self):
        mod = self.mod()
        self.assertEqual("fixed_spec", mod.classify_sizes(_box("FIX", 200, 200, 100, 100, 50, 50)))
        self.assertEqual("adjustable", mod.classify_sizes(_box("ADJ", 80, 400, 80, 300, 25, 120)))
        missing = _box("REF", 80, 400, 80, 300, 25, 120)
        missing["size_h_max"] = None
        self.assertEqual("reference", mod.classify_sizes(missing))

    def test_t6_b_audit_reports_missing_inverted_and_non_positive(self):
        mod = self.mod()
        rows = [
            _box("OK", 80, 400, 80, 300, 25, 120),
            _box("INV", 400, 80, 80, 300, 25, 120),
            _box("ZERO", 0, 400, 80, 300, 25, 120),
            {**_box("MISS", 80, 400, 80, 300, 25, 120), "size_w_min": None},
        ]
        report = mod.audit_box_types(rows)
        self.assertEqual(4, report["total"])
        codes = {row["box_type_code"]: row["code"] for row in report["issues"]}
        self.assertEqual("range_inverted", codes.get("INV"))
        self.assertEqual("range_not_positive", codes.get("ZERO"))
        self.assertEqual("range_missing", codes.get("MISS"))
        self.assertNotIn("OK", codes, "干净的行不许报 issue")
        self.assertIn("MISS", report["no_lower_bound"])
        self.assertIn("INV", report["inverted"])
        self.assertIn("MISS", report["classes"]["reference"])
        self.assertIn("FIX", mod.audit_box_types(rows + [_box("FIX", 200, 200, 100, 100, 50, 50)])
                      ["classes"]["fixed_spec"])

    def test_t6_c_repository_seed_is_clean(self):
        from tech_app.backend.storage import da_seed_packaging as seed
        mod = self.mod()
        report = mod.audit_box_types(seed.BOX_TYPES)
        self.assertEqual(12, report["total"])
        self.assertEqual([], report["issues"], "本仓种子的区间数据必须是干净的")
        self.assertEqual(12, len(report["classes"]["adjustable"]))


class T14PartsAccuracy(unittest.TestCase):
    """T14：识别质量必须按维度拆开度量；证据覆盖率不等于正确率。"""

    TRUTH = [
        {"part_code": "P01", "name": "左盖面纸", "length_mm": 218.2, "width_mm": 68.25,
         "role": "面纸", "group": "左盖"},
        {"part_code": "P02", "name": "右盖面纸", "length_mm": 113.04, "width_mm": 384.67,
         "role": "面纸", "group": "右盖"},
        {"part_code": "P03", "name": "内托", "length_mm": 100.0, "width_mm": 50.0,
         "role": "内托", "group": ""},
    ]
    PREDICTED = [
        {"part_code": "P01", "name": "左盖面纸", "length_mm": 218.2, "width_mm": 68.25,
         "role": "面纸", "group": "左盖", "size_evidence": True},
        {"part_code": "P02", "name": "右盖面纸", "length_mm": 113.04, "width_mm": 390.0,
         "role": "面纸", "group": "右盖", "size_evidence": True},
        {"part_code": "P09", "name": "多余件", "length_mm": 10.0, "width_mm": 10.0,
         "role": "", "group": "", "size_evidence": False},
    ]

    def mod(self):
        mod = _module("tech_app.tools.packaging_parts_accuracy")
        if isinstance(mod, Exception):
            self.fail("缺 `tech_app/tools/packaging_parts_accuracy.py`（Spec §2.2）：%r" % mod)
        return mod

    def test_t14_a_metrics_are_split_not_fused(self):
        mod = self.mod()
        result = mod.score_parts(self.PREDICTED, self.TRUTH)
        self.assertEqual(["P03"], sorted(result["missed"]), "漏识别要单独报")
        self.assertEqual(["P09"], sorted(result["spurious"]), "误识别要单独报")
        self.assertEqual(2, result["matched_total"])
        self.assertEqual(2, result["attribution_total"])
        self.assertEqual(2, result["attribution_ok"])
        self.assertEqual(2, result["size_checked"])
        self.assertEqual(1, result["size_ok"], "P02 尺寸偏 5.33mm，必须判不通过")
        self.assertEqual(2, result["evidence_total"])
        self.assertEqual(2, result["evidence_covered"], "证据覆盖率与正确率是两个数")
        self.assertIsNone(result["manual_fix_minutes"], "人工修正耗时只能由人填，不许推算")

    def test_t14_b_no_single_accuracy_scalar_in_summary(self):
        mod = self.mod()
        result = mod.score_parts(self.PREDICTED, self.TRUTH)
        keys = {str(key).lower() for key in (result.get("summary") or {})}
        for banned in ("accuracy", "precision", "recall", "f1", "正确率", "准确率"):
            self.assertNotIn(banned, keys,
                             "summary 不得出现单一正确率标量（Spec §2.2）：%s" % sorted(keys))

    def test_t14_c_missing_holdout_folder_is_empty_not_invented(self):
        mod = self.mod()
        with tempfile.TemporaryDirectory() as folder:
            data = mod.load_holdout(folder)
        self.assertEqual([], data["drawings"], "空目录不许编造评价集")
        self.assertEqual([], mod.load_holdout(str(ROOT / "no-such-holdout-dir"))["drawings"])


class T16RoutingDiagnostic(unittest.TestCase):
    """T16：工序路线的离线只读诊断 —— 命中、孤儿、草稿、空工时各自可数。"""

    HEADERS = [
        {"product_item_code": "BP01", "name": "左盖面纸", "status": "confirmed"},
        {"product_item_code": "BP02", "name": "右盖面纸", "status": "draft"},
        {"product_item_code": "BP03", "name": "内托", "status": "confirmed"},
        {"product_item_code": "BP04", "name": "无路线件", "status": "confirmed"},
    ]
    STEPS = [
        {"product_item_code": "BP01", "seq": 1, "process_code": "CUT", "process_name": "开料",
         "standard_seconds": 30, "labor_seconds": None, "equipment_seconds": None},
        {"product_item_code": "BP02", "seq": 1, "process_code": "CUT", "process_name": "开料",
         "standard_seconds": None, "labor_seconds": None, "equipment_seconds": None},
        # BP03 只有名称能对上（product_item_code 不一致）
        {"product_item_code": "", "seq": 1, "process_code": "GLU", "process_name": "内托",
         "standard_seconds": 20, "labor_seconds": 10, "equipment_seconds": 0},
        # 孤儿步骤：头表里没有 BP09
        {"product_item_code": "BP09", "seq": 1, "process_code": "X", "process_name": "未知",
         "standard_seconds": 5, "labor_seconds": 5, "equipment_seconds": 5},
    ]

    def mod(self):
        mod = _module("tech_app.tools.packaging_routing_diagnostic")
        if isinstance(mod, Exception):
            self.fail("缺 `tech_app/tools/packaging_routing_diagnostic.py`（Spec §2.3）：%r" % mod)
        return mod

    def test_t16_a_diagnose_classifies_matches_and_gaps(self):
        mod = self.mod()
        report = mod.diagnose_routing(self.HEADERS, self.STEPS)
        self.assertEqual(4, report["headers_total"])
        self.assertIn("BP01", report["matched_by_code"])
        self.assertIn("BP03", report["matched_by_name"], "只能靠名称命中的要单独标出来")
        self.assertIn("BP04", report["unmatched"])
        self.assertEqual("attention", report["verdict"])

    def test_t16_b_detects_orphans_drafts_and_empty_times(self):
        mod = self.mod()
        report = mod.diagnose_routing(self.HEADERS, self.STEPS)
        self.assertEqual([{"product_item_code": "BP09", "seq": 1}], report["orphan_steps"])
        self.assertIn("BP02", report["draft_headers"])
        self.assertIn("BP02", report["empty_time_headers"], "三类工时全空必须点名")
        self.assertNotIn("BP01", report["empty_time_headers"], "有标准工时就不算空")

    def test_t16_c_diagnose_is_read_only_and_pure(self):
        mod = self.mod()
        headers = [dict(row) for row in self.HEADERS]
        steps = [dict(row) for row in self.STEPS]
        mod.diagnose_routing(headers, steps)
        self.assertEqual(self.HEADERS, headers, "不许改入参")
        self.assertEqual(self.STEPS, steps, "不许改入参")


class T13GuardsAlreadyImplemented(unittest.TestCase):
    """T13 守卫（今天应为绿）：把既有的只读导入与预检口径钉住，防止被改回去。"""

    def test_t13_a_importer_defaults_to_read_only(self):
        mod = _module("scripts.import_da_kb_to_pg")
        if isinstance(mod, Exception):
            self.fail("缺 `scripts/import_da_kb_to_pg.py`：%r" % mod)
        with tempfile.TemporaryDirectory() as folder:
            source = pathlib.Path(folder) / "da.db"
            source.write_bytes(b"")
            result = {"ok": True, "dry_run": True, "kb_version": 0, "rows": 0, "tables": {}}
            with mock.patch.object(mod.cpq_kb, "import_from_sqlite",
                                   return_value=dict(result)) as fake:
                code = mod.main(["--source", str(source)])
            self.assertEqual(0, code)
            self.assertFalse(fake.call_args.kwargs.get("confirm"),
                             "不给 --confirm 时必须只读（confirm=False）")

    def test_t13_b_preflight_flags_authority_box_type_without_fit_clearance(self):
        preflight = _module("tech_app.tools.kb_deploy_preflight")
        kb = _module("cpq_kb")
        if isinstance(preflight, Exception) or isinstance(kb, Exception):
            self.fail("缺预检模块：%r / %r" % (preflight, kb))
        tables = {name: [] for name in kb.KB_TABLES}
        tables[preflight.BOX_TABLE] = [
            {"box_type_code": "YT-X-1", "name": "测试盒",
             "source_type": "workbook", "fit_clearance": ""}]
        report = preflight.preflight(tables, env="production", kb_version=0)
        codes = {row.get("code") for row in report["problems"]}
        self.assertIn("box_type_missing_fit_clearance", codes)


if __name__ == "__main__":
    unittest.main()
