"""红测：包装成本规则快照固化 —— 包装修复第 2 批。

Spec：`docs/specs/packaging-cost-rule-snapshot.md`。依赖修复第 1 批
（`docs/specs/packaging-cost-rule-routing.md`）已实现。

现状缺口（首次运行时**必须失败**，是实测不是推断）：
  · `tech_app/agent_knowledge/rules/packaging_cost_rules.json` 不存在；
  · `da_seed_packaging` 没有 `seed_packaging_cost_rules`，库里没有任何 reviewed 规则行；
  · `tech_app/tools/extract_packaging_rules.py` 不存在，没有离线对账能力；
  · 公式因此仍被人工抄在 `FORMULA_CATALOG` / `COST_FORMULAS`（7 条中文散文） / 库表 / 红测常量四处。

黄金值取自 `报价逻辑-0903.xlsx` 的可见 Sheet（`报价-工费率` 缓存值已逐条复算核对）；本文件
**不读工作簿**，只有 sha256 对账那一条在工作簿存在时才跑。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RULES_JSON = ROOT / "tech_app" / "agent_knowledge" / "rules" / "packaging_cost_rules.json"
TOOL_PY = ROOT / "tech_app" / "tools" / "extract_packaging_rules.py"
SEED_MODULE = "tech_app.backend.storage.da_seed_packaging"
WORKBOOK = ROOT / "裕同包装项目-待开发" / "报价逻辑-0903.xlsx"


#: 工作簿证据类测试的依赖：缺它必须 **skip**，不能报 ERROR。
#: 为什么：ERROR 会混进"N 条红"的数字里，让真正的业务红失去可读性（Spec
#: `packaging-cost-red-closure.md` C1）。装依赖：pip install -r tech_app/requirements.txt
OPENPYXL_SKIP_REASON = "缺 openpyxl（工作簿证据类测试）：pip install -r tech_app/requirements.txt"
WORKBOOK_SHA256 = "974d9484414824d0bbfd2c83fb0c6044e4348c7a407e1ae0f58699bb60d2acc0"

VISIBLE_SHEETS = ("报价-工费率", "包装运输", "包装运输 (2)", "成本细分", "报价-行业标准",
                  "报价表", "报价表 (2)", "成本细分（2）", "问题点")
HIDDEN_SHEETS = ("大货最终定价", "大货价核算1", "首批试产毛利核算")

REQUIRED_FORMULA_FIELDS = ("formula_code", "cost_category", "expression", "minimum_charge",
                           "rounding", "rate_code", "loss_scope", "source_sheet", "source_cell",
                           "verify_inputs", "expected_result", "formula_version")

#: Spec §2.3：必须逐字照抄的黄金值
GOLDEN_RESULTS = {
    "PKG-C-MATERIAL": (0.7954641993584073, {"cut_length": 889, "cut_width": 705, "gsm": 157,
                                            "ton_price": 6300, "imposition_count": 1,
                                            "tax_factor": 1.13, "proof_base": 450,
                                            "quote_quantity": 1000}),
    "PKG-C-PRINT-UV": (0.9865756637168142, {"machine_length": 889, "machine_width": 700,
                                            "imposition_count": 1, "setup_minutes": 30,
                                            "capacity_per_hour": 12000, "equipment_rate": 591,
                                            "labor_rate": 666, "ink_thickness_mm": 4,
                                            "ink_unit_price": 115, "tax_factor": 1.13,
                                            "quote_quantity": 1000}),
    "PKG-C-LAMINATION": (1.3766112580048271, {"machine_length": 889, "machine_width": 700,
                                              "imposition_count": 1, "setup_minutes": 30,
                                              "capacity_per_hour": 5500, "equipment_rate": 197,
                                              "labor_rate": 145, "film_price": 1.7,
                                              "film_thickness_um": 18, "film_kg_price": 18.5,
                                              "tax_factor": 1.13, "quote_quantity": 1000}),
    "PKG-C-HOT-STAMP-FLAT": (1.3432666666666671, {"hot_area_mm2": 30000, "imposition_count": 1,
                                                  "setup_minutes": 200, "capacity_per_hour": 5000,
                                                  "equipment_rate": 193, "labor_rate": 115,
                                                  "foil_price": 8.5, "quote_quantity": 1000}),
    "PKG-C-DIE-CUT": (0.8347876923076923, {"imposition_count": 1, "setup_minutes": 120,
                                           "capacity_per_hour": 6500, "equipment_rate": 197.52,
                                           "labor_rate": 190.06, "quote_quantity": 1000}),
    "PKG-C-V-GROOVE": (0.816, {"quote_quantity": 1000, "setup_minutes": 60,
                               "capacity_per_hour": 3000, "equipment_rate": 195,
                               "labor_rate": 111, "times": 2}),
    "PKG-C-GLUE": (0.46050199999999997, {"machine_length": 889, "machine_width": 700,
                                         "imposition_count": 1, "glue_unit_price": 0.74}),
    "PKG-P-CARTON": (2.1108074127397023, {"length_mm": 520, "width_mm": 420, "height_mm": 425,
                                          "usage_qty": 1, "material_price": 3, "units_per_pack": 4,
                                          "tax_factor": 1.13, "loss_uplift": 1.03,
                                          "yield_divisor": 0.9}),
    "PKG-P-PAD": (0.28404101945273708, {"length_mm": 510, "width_mm": 410, "usage_qty": 2,
                                        "material_price": 1.55, "units_per_pack": 4,
                                        "tax_factor": 1.13, "loss_uplift": 1.03,
                                        "yield_divisor": 0.9}),
    "PKG-P-PALLET": (0.51622418879056053, {"usage_qty": 1, "material_price": 70,
                                           "units_per_pack": 120, "tax_factor": 1.13}),
}


def load_cost_module():
    return importlib.import_module("tech_app.backend.services.packaging_cost")


def load_tool():
    if not TOOL_PY.exists():
        return None
    spec = importlib.util.spec_from_file_location("pkg_extract_packaging_rules", TOOL_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_seed_module():
    return importlib.import_module(SEED_MODULE)


def read_rules(path=None):
    path = pathlib.Path(path or RULES_JSON)
    return json.loads(path.read_text(encoding="utf-8"))


def rules_by_code(rules=None):
    rules = rules or read_rules()
    return {item["formula_code"]: item for item in rules["formulas"]}


def synthetic_cells(**sheets):
    """构造 `load_workbook_cells` 的返回结构：sheet -> (state, formulas, cached)。"""
    out = {}
    for name, payload in sheets.items():
        state, formulas, cached = payload
        out[name] = {"state": state, "formulas": dict(formulas), "cached": dict(cached)}
    return out


def synthetic_rules(**over):
    rule = {"formula_code": "PKG-C-GLUE", "cost_category": "glue",
            "expression": "machine_length*machine_width/1000000*glue_unit_price/imposition_count",
            "minimum_charge": 0, "rounding": 4, "rate_code": "", "loss_scope": "胶水",
            "source_sheet": "报价-工费率", "source_cell": "AN2",
            "verify_inputs": {"machine_length": 889, "machine_width": 700, "imposition_count": 1,
                              "glue_unit_price": 0.74},
            "expected_result": 0.46050199999999997, "formula_version": "packaging_cost_v1"}
    rule.update(over)
    rules = {"rule_set": "packaging_cost_v1", "source_file": "synthetic.xlsx",
             "source_sha256": "synthetic-sha", "source_sheets": ["报价-工费率"],
             "review_status": "reviewed", "generated_at": "2026-09-20", "formulas": [rule]}
    return rules


def synthetic_workbook_cells(expected=0.46050199999999997):
    return synthetic_cells(**{"报价-工费率": (
        "visible", {"AN2": "=H2*I2/1000000*0.74/J2"}, {"AN2": expected})})


class SnapshotCase(unittest.TestCase):
    """带临时 SQLite 的用例基类（seed 导入相关用）。"""

    def setUp(self):
        from tech_app.backend import config
        from tech_app.backend.storage import da_db, kb_repo
        self.config = config
        self.da_db = da_db
        self.kb_repo = kb_repo
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "rules.sqlite3"
        self._patch_da = mock.patch.object(config, "DA_DB_PATH", self.db_file)
        self._patch_da.start()
        da_db.init_db(self.db_file)

    def tearDown(self):
        self._patch_da.stop()
        self.da_db.close_conn()
        self.kb_repo._CACHE.clear()
        self.kb_repo._CACHE.update(self._cache)

    def rows(self, table):
        with self.da_db.get_conn() as conn:
            conn.row_factory = None
            cur = conn.execute("SELECT * FROM %s" % table)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def count(self, table):
        with self.da_db.get_conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])

    def seed_rules(self, **kwargs):
        module = load_seed_module()
        fn = getattr(module, "seed_packaging_cost_rules", None)
        if not callable(fn):
            self.fail("da_seed_packaging 必须提供 seed_packaging_cost_rules(*, rules_path=None, "
                      "overwrite=False)（Spec §3）")
        return fn(**kwargs)


# --------------------------------------------------------------------------- #
# A. 规则快照文件
# --------------------------------------------------------------------------- #
class ARulesSnapshot(SnapshotCase):
    def test_a1_snapshot_file_exists(self):
        self.assertTrue(RULES_JSON.exists(),
                        "缺少 tech_app/agent_knowledge/rules/packaging_cost_rules.json（Spec §2）")

    def test_a2_top_level_fields(self):
        rules = read_rules()
        for field in ("rule_set", "source_file", "source_sha256", "source_sheets",
                      "review_status", "formulas"):
            self.assertIn(field, rules, "快照缺顶层字段 %s" % field)
        self.assertTrue(rules["rule_set"].strip())
        self.assertIsInstance(rules["formulas"], list)

    def test_a3_snapshot_is_reviewed_and_points_at_the_workbook(self):
        rules = read_rules()
        self.assertEqual(rules["review_status"], "reviewed",
                         "随代码发布的快照必须是 reviewed（Spec §2.1）")
        self.assertEqual(rules["source_file"], "报价逻辑-0903.xlsx")
        self.assertEqual(rules["source_sha256"], WORKBOOK_SHA256)

    def test_a4_formulas_cover_the_runtime_catalog_exactly(self):
        module = load_cost_module()
        codes = {item["formula_code"] for item in read_rules()["formulas"]}
        self.assertEqual(len(codes), len(read_rules()["formulas"]), "快照里 formula_code 不得重复")
        self.assertEqual(codes, set(module.FORMULA_CATALOG),
                         "快照必须恰好覆盖 FORMULA_CATALOG 的 20 条公式（不多不少）")

    def test_a5_every_formula_has_required_fields(self):
        for item in read_rules()["formulas"]:
            for field in REQUIRED_FORMULA_FIELDS:
                self.assertIn(field, item, "%s 缺字段 %s" % (item.get("formula_code"), field))
            self.assertTrue(str(item["loss_scope"]).strip(),
                            "%s 的 loss_scope 不得为空（第 3 批 b11 会逐行断言）"
                            % item["formula_code"])
            self.assertIsInstance(item["verify_inputs"], dict)
            self.assertTrue(item["verify_inputs"], "%s 的 verify_inputs 不得为空" % item["formula_code"])

    def test_a6_snapshot_matches_the_frozen_runtime_catalog(self):
        module = load_cost_module()
        for code, item in rules_by_code().items():
            entry = module.FORMULA_CATALOG[code]
            self.assertEqual(item["expression"], entry["expression"],
                             "%s 的表达式与运行时目录不一致（本批不许改口径）" % code)
            self.assertEqual(item["minimum_charge"], entry["minimum_charge"])
            self.assertEqual(item["rounding"], entry["rounding"])
            self.assertEqual(item["rate_code"], entry["rate_code"])
            self.assertEqual(item["cost_category"], entry["cost_category"])
            self.assertEqual(item.get("defaults") or {}, entry.get("defaults") or {},
                             "%s 的默认参数与运行时目录不一致" % code)

    def test_a7_source_cells_are_addressable_and_visible(self):
        for item in read_rules()["formulas"]:
            self.assertIn(item["source_sheet"], VISIBLE_SHEETS,
                          "%s 的来源必须是可见 Sheet" % item["formula_code"])
            self.assertNotIn(item["source_sheet"], HIDDEN_SHEETS)
            self.assertRegex(str(item["source_cell"]), r"^[A-Z]{1,3}[0-9]{1,5}$",
                             "%s 的 source_cell 形状不合法" % item["formula_code"])
            self.assertNotIn("#", str(item["source_cell"]))

    def test_a8_hidden_sheets_never_appear(self):
        text = RULES_JSON.read_text(encoding="utf-8")
        for name in HIDDEN_SHEETS:
            self.assertNotIn(name, text, "隐藏 Sheet %s 不得进入快照（Spec §2.2）" % name)

    def test_a9_expected_results_recompute(self):
        module = load_cost_module()
        for code, item in rules_by_code().items():
            line = module.compute_line(item["cost_category"], dict(item["verify_inputs"]),
                                       formula_code=code)
            self.assertIsNotNone(line.get("amount"),
                                 "%s 在 verify_inputs 下算不出金额（Spec §2.2）" % code)
            self.assertAlmostEqual(line["amount"], item["expected_result"], places=6,
                                   msg="%s 的 expected_result 与表达式复算不符" % code)

    def test_a10_golden_results_are_transcribed(self):
        items = rules_by_code()
        for code, (expected, inputs) in GOLDEN_RESULTS.items():
            self.assertIn(code, items, "快照缺黄金样例 %s" % code)
            item = items[code]
            self.assertAlmostEqual(item["expected_result"], expected, places=9,
                                   msg="%s 的 expected_result 必须逐字照抄 0903 缓存值" % code)
            # 金标输入**逐键**核对。旧写法只核对 `quote_quantity`，而
            # `PKG-C-GLUE` / `PKG-P-CARTON` / `PKG-P-PAD` / `PKG-P-PALLET` 这 4 条金标本来就
            # 没有这个键，于是旧写法对这 4 条抛 KeyError —— 是测试自己写坏了（只覆盖了
            # 6/10 条），不是快照缺数据。改成逐键核对后覆盖面从 6 条变成 10 条全量，
            # 断言强度只增不减。
            for key, value in inputs.items():
                self.assertIn(key, item["verify_inputs"],
                              "%s 的 verify_inputs 缺金标输入 %s" % (code, key))
                self.assertAlmostEqual(float(item["verify_inputs"][key]), float(value), places=6,
                                       msg="%s 的 verify_inputs.%s 必须取 0903 同一行的输入"
                                           % (code, key))

    def test_a11_formula_version_is_the_rule_set(self):
        rules = read_rules()
        for item in rules["formulas"]:
            self.assertEqual(item["formula_version"], rules["rule_set"],
                             "%s 的 formula_version 必须等于 rule_set" % item["formula_code"])

    def test_a12_workbook_sha256_matches_when_present(self):
        if not WORKBOOK.exists():
            self.skipTest("客户工作簿不在本机（不入库），跳过 SHA-256 对账")
        digest = hashlib.sha256(WORKBOOK.read_bytes()).hexdigest()
        self.assertEqual(read_rules()["source_sha256"], digest,
                         "工作簿换了内容就必须人工确认新 rule_set，工具不许自动改 sha256")


# --------------------------------------------------------------------------- #
# B. 幂等导入
# --------------------------------------------------------------------------- #
class BSeedImport(SnapshotCase):
    def test_b1_rules_are_imported(self):
        result = self.seed_rules()
        self.assertGreaterEqual(result.get("inserted", 0), len(read_rules()["formulas"]))
        rows = {row["formula_code"]: row for row in self.rows("kb_packaging_cost_formula")}
        for item in read_rules()["formulas"]:
            self.assertIn(item["formula_code"], rows,
                          "%s 必须随 seed 导入（Spec §3）" % item["formula_code"])

    def test_b2_imported_rows_are_reviewed_and_executable(self):
        self.seed_rules()
        rows = {row["formula_code"]: row for row in self.rows("kb_packaging_cost_formula")}
        item = read_rules()["formulas"][0]
        row = rows[item["formula_code"]]
        self.assertEqual(row["review_status"], "reviewed")
        self.assertEqual(row["formula_version"], read_rules()["rule_set"])
        self.assertEqual(row["expression"], item["expression"])
        for column in ("loss_scope", "minimum_charge", "source"):
            self.assertIsNotNone(row.get(column), "导入行缺 %s" % column)

    def test_b3_import_is_idempotent(self):
        self.seed_rules()
        before = self.count("kb_packaging_cost_formula")
        again = self.seed_rules()
        self.assertEqual(self.count("kb_packaging_cost_formula"), before,
                         "重复导入不得新增行（Spec §3）")
        self.assertEqual(again.get("inserted", 0), 0)

    def test_b4_user_modified_row_is_never_overwritten(self):
        self.seed_rules()
        module = load_seed_module()
        with self.da_db.get_conn() as conn:
            conn.execute("UPDATE kb_packaging_cost_formula SET expression = ?, source = ? "
                         "WHERE formula_code = ?", ("quote_quantity*0+7", "manual", "PKG-C-GLUE"))
            conn.commit()
        result = self.seed_rules()
        rows = {row["formula_code"]: row for row in self.rows("kb_packaging_cost_formula")}
        self.assertEqual(rows["PKG-C-GLUE"]["expression"], "quote_quantity*0+7",
                         "业务人工维护的 reviewed 行不得被 seed 覆盖（Spec §3）")
        self.assertGreaterEqual(result.get("skipped_user_modified", 0), 1)
        self.assertIsNotNone(module)

    def test_b5_overwrite_does_not_break_user_rows_either(self):
        self.seed_rules()
        with self.da_db.get_conn() as conn:
            conn.execute("UPDATE kb_packaging_cost_formula SET expression = ?, source = ? "
                         "WHERE formula_code = ?", ("quote_quantity*0+9", "manual", "PKG-C-GLUE"))
            conn.commit()
        self.seed_rules(overwrite=True)
        rows = {row["formula_code"]: row for row in self.rows("kb_packaging_cost_formula")}
        self.assertEqual(rows["PKG-C-GLUE"]["expression"], "quote_quantity*0+9",
                         "overwrite=True 也不许覆盖人工行（安全优先）")

    def test_b6_retired_row_is_not_revived(self):
        self.seed_rules()
        with self.da_db.get_conn() as conn:
            conn.execute("UPDATE kb_packaging_cost_formula SET review_status = 'retired' "
                         "WHERE formula_code = ?", ("PKG-C-GLUE",))
            conn.commit()
        self.seed_rules()
        rows = {row["formula_code"]: row for row in self.rows("kb_packaging_cost_formula")}
        self.assertEqual(rows["PKG-C-GLUE"]["review_status"], "retired",
                         "业务已下线的公式不得被 seed 复活")

    def test_b7_imported_rules_take_effect_at_runtime(self):
        self.seed_rules()
        module = load_cost_module()
        item = rules_by_code()["PKG-C-GLUE"]
        line = module.compute_line(item["cost_category"], dict(item["verify_inputs"]),
                                  formula_code=item["formula_code"])
        self.assertAlmostEqual(line["amount"], item["expected_result"], places=6,
                               msg="导入的 reviewed 规则必须在计算里生效（第 1 批 §3.1）")

    def test_b8_prose_drafts_are_kept(self):
        module = load_seed_module()
        self.assertEqual(len(module.COST_FORMULAS), 7,
                         "第 3 批的 7 条中文散文公式必须原样保留（第 7 批红测逐条断言）")
        for row in module.COST_FORMULAS:
            self.assertEqual(row["review_status"], "draft")
            self.assertTrue(row["formula_code"].startswith("PKG-F-"))


# --------------------------------------------------------------------------- #
# C. 离线校验工具
# --------------------------------------------------------------------------- #
class COfflineTool(SnapshotCase):
    def tool(self):
        module = load_tool()
        self.assertIsNotNone(module,
                             "缺少 tech_app/tools/extract_packaging_rules.py（Spec §4）")
        for name in ("load_workbook_cells", "audit_rules", "main"):
            self.assertTrue(callable(getattr(module, name, None)),
                            "工具必须提供 %s（Spec §4.1）" % name)
        return module

    def audit(self, cells, rules, **over):
        module = self.tool()
        kwargs = {"workbook_sha256": rules["source_sha256"], "workbook_name": "synthetic.xlsx",
                  "catalog_codes": {"PKG-C-GLUE"}}
        kwargs.update(over)
        return module.audit_rules(cells, rules, **kwargs)

    def codes(self, report):
        return {item["code"] for item in report.get("problems") or []}

    def test_c1_cli_runs(self):
        self.tool()
        done = subprocess.run([sys.executable, str(TOOL_PY), "--help"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_c2_clean_audit_passes(self):
        report = self.audit(synthetic_workbook_cells(), synthetic_rules())
        self.assertTrue(report["ok"], report.get("problems"))
        self.assertEqual(report["exit_code"], 0)

    def test_c3_hidden_sheet_formulas_are_reported_not_fatal(self):
        cells = synthetic_workbook_cells()
        cells.update(synthetic_cells(**{"大货价核算1": (
            "hidden", {"A1": "=SUM(B1:B9)"}, {"A1": 1.0})}))
        report = self.audit(cells, synthetic_rules())
        self.assertIn("大货价核算1", report.get("skipped_hidden_sheets") or [],
                      "隐藏 Sheet 必须被明确跳过并列出（Spec §4.2）")
        self.assertTrue(report["ok"], "隐藏 Sheet 只记录，不影响结论")

    def test_c4_broken_reference_fails(self):
        cells = synthetic_workbook_cells()
        cells["报价-工费率"]["cached"]["Z9"] = "#REF!"
        report = self.audit(cells, synthetic_rules())
        self.assertIn("broken_reference", self.codes(report))
        self.assertEqual(report["exit_code"], 2)

    def test_c5_cached_value_without_formula_fails(self):
        cells = synthetic_workbook_cells()
        cells["报价-工费率"]["cached"]["Z9"] = 12.5
        report = self.audit(cells, synthetic_rules())
        self.assertIn("cached_without_formula", self.codes(report))
        self.assertEqual(report["exit_code"], 2)

    def test_c6_sha256_mismatch_fails(self):
        report = self.audit(synthetic_workbook_cells(), synthetic_rules(),
                            workbook_sha256="another-sha")
        self.assertIn("source_sha256_mismatch", self.codes(report))
        self.assertEqual(report["exit_code"], 3)

    def test_c7_cached_value_mismatch_is_a_mismatch(self):
        report = self.audit(synthetic_workbook_cells(expected=9.99), synthetic_rules())
        self.assertIn("cached_value_mismatch", self.codes(report))
        self.assertEqual(report["exit_code"], 1)
        self.assertTrue(report.get("mismatches"))

    def test_c8_recompute_mismatch_is_detected(self):
        rules = synthetic_rules(expected_result=9.99)
        report = self.audit(synthetic_workbook_cells(expected=9.99), rules)
        self.assertIn("recompute_mismatch", self.codes(report),
                      "缓存值与 expected_result 都对不上引擎复算时必须报出来")
        self.assertEqual(report["exit_code"], 1)

    def test_c9_source_cell_without_formula_fails(self):
        cells = synthetic_cells(**{"报价-工费率": ("visible", {}, {"AN2": 0.4605})})
        report = self.audit(cells, synthetic_rules())
        self.assertIn("source_cell_has_no_formula", self.codes(report))

    def test_c10_formula_set_mismatch_is_detected(self):
        report = self.audit(synthetic_workbook_cells(), synthetic_rules(),
                            catalog_codes={"PKG-C-GLUE", "PKG-C-LAMINATION"})
        self.assertIn("formula_set_mismatch", self.codes(report))
        self.assertEqual(report["exit_code"], 1)

    def test_c11_exit_code_is_the_worst_problem(self):
        cells = synthetic_workbook_cells(expected=9.99)
        report = self.audit(cells, synthetic_rules(), workbook_sha256="another-sha")
        self.assertEqual(report["exit_code"], 3,
                         "多个问题时退出码取最大值（3 > 2 > 1）")

    def test_c12_check_never_writes_and_write_refuses_reviewed(self):
        module = self.tool()
        workdir = pathlib.Path(tempfile.mkdtemp())
        rules_path = workdir / "rules.json"
        rules_path.write_text(json.dumps(synthetic_rules(), ensure_ascii=False), encoding="utf-8")
        before = rules_path.read_text(encoding="utf-8")
        code = module.main(["--workbook", str(workdir / "missing.xlsx"),
                            "--rules", str(rules_path)])
        self.assertGreater(code, 0, "工作簿缺失时不得返回 0")
        self.assertEqual(rules_path.read_text(encoding="utf-8"), before,
                         "--check（默认）绝不写文件")
        refused = module.main(["--workbook", str(workdir / "missing.xlsx"),
                               "--rules", str(rules_path), "--write"])
        self.assertEqual(refused, 4,
                         "目标 JSON 是 reviewed 时必须拒绝覆盖（Spec §4.2 write_refused）")
        self.assertEqual(rules_path.read_text(encoding="utf-8"), before)

    @unittest.skipUnless(importlib.util.find_spec("openpyxl") is not None,
                         OPENPYXL_SKIP_REASON)
    def test_c13_real_workbook_sheets_are_readable(self):
        if not WORKBOOK.exists():
            self.skipTest("客户工作簿不在本机（不入库），跳过真实工作簿读取")
        module = self.tool()
        cells = module.load_workbook_cells(WORKBOOK)
        self.assertEqual(cells["大货价核算1"]["state"], "hidden")
        self.assertEqual(cells["报价-工费率"]["state"], "visible")
        self.assertIn("V2", cells["报价-工费率"]["formulas"])


# --------------------------------------------------------------------------- #
# D. 运行时约束
# --------------------------------------------------------------------------- #
class DRuntimeBoundaries(SnapshotCase):
    def test_d1_backend_never_imports_openpyxl(self):
        offenders = []
        for path in (ROOT / "tech_app" / "backend").rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "openpyxl" in text or "load_workbook" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "生产后端不得依赖 openpyxl（Spec §4.3）")

    def test_d2_importing_the_engine_does_not_pull_openpyxl(self):
        code = ("import sys; import tech_app.backend.services.packaging_cost as pc; "
                "print('openpyxl' in sys.modules)")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              cwd=str(ROOT), timeout=180)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "False",
                         "导入成本引擎不得拉起 openpyxl（Spec §4.3）")

    def test_d3_backend_does_not_import_the_offline_tool(self):
        offenders = []
        for path in (ROOT / "tech_app" / "backend").rglob("*.py"):
            if "extract_packaging_rules" in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "离线校验工具不得进入生产路径（Spec §4.3）")


# --------------------------------------------------------------------------- #
# E. 本批不许动的既有契约
# --------------------------------------------------------------------------- #
class EUnchangedContracts(SnapshotCase):
    def test_e1_batch1_frozen_numbers_still_hold(self):
        module = load_cost_module()
        for code, (expected, inputs) in GOLDEN_RESULTS.items():
            item = module.FORMULA_CATALOG[code]
            line = module.compute_line(item["cost_category"], dict(inputs), formula_code=code)
            self.assertAlmostEqual(line["amount"], expected, places=6,
                                   msg="%s 的本批不允许改口径（第 1 批 f3 已冻结）" % code)
        # 2026-09-21 口径裁决取 ② 报价-工费率（主行无门限）：minimum_charge 归零，
        # 第 1 批冻结值 200/150/100/120 留在 frozen_minimum_charge 作证据，不再参与命中判定
        # （见 docs/specs/packaging-cost-minimum-charge-decision.md §4）。
        for code, frozen in (("PKG-C-LAMINATION", 200), ("PKG-C-HOT-STAMP-FLAT", 150),
                             ("PKG-C-DIE-CUT", 100), ("PKG-C-V-GROOVE", 120)):
            self.assertEqual(module.FORMULA_CATALOG[code]["minimum_charge"], 0,
                             "%s 的最低收费已按 ② 裁决归零" % code)
            self.assertEqual(module.FORMULA_CATALOG[code]["frozen_minimum_charge"], frozen,
                             "%s 的第 1 批冻结值必须留证" % code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
