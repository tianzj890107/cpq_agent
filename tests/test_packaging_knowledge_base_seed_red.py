"""红测：包装知识库扩展表、行业维度与演示数据导入 —— 包装第 3 批。

Spec：docs/specs/packaging-knowledge-base-mock-seed.md
依赖：第 1 批（四行业注册表）、第 2 批（包装需求模板）已完成。

现状缺口（实测，不是推断）：
  · `da_schema.sql` / `cpq_kb.py` 的 20 张 kb_* 表**没有一张带行业列**，
    `kb_repo.list_materials/current_price/effective_rate/effective_factor/
    recommend_components/recommend_routes` 全部在全库上过滤 —— 加包装数据后三行业会串味；
  · 7 张包装扩展表（盒型 / 部件模板 / 工艺模板 / 内托配件 / 成本公式 / 物流规则 / 匹配权重）
    在 SQLite 与 cpq_kb 两侧都不存在；
  · `tech_app/backend/storage/da_seed_packaging.py` 不存在，包装这条线在库里一条也命中不上；
  · `cpq_kb.KB_TABLES` 不含包装表，快照与导入器都看不见它们。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_SQL = ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql"
IMPORTER_PY = ROOT / "scripts" / "import_da_kb_to_pg.py"
SEED_PACKAGING_PY = ROOT / "tech_app" / "backend" / "storage" / "da_seed_packaging.py"

import cpq_kb  # noqa: E402
from tech_app.backend import config  # noqa: E402
from tech_app.backend.services import cpq_kb_client  # noqa: E402
from tech_app.backend.storage import da_db  # noqa: E402
from tech_app.backend.storage import kb_repo  # noqa: E402

# 2.1 行业主体表（必须带 industry 列）
SUBJECT_TABLES = (
    "kb_component", "kb_standard_part", "kb_equipment_class", "kb_equipment",
    "kb_process_step", "kb_process_route", "kb_inspection_item", "kb_material",
    "kb_supplier", "kb_cost_rate", "kb_cost_factor",
)
# 2.2 包装扩展表 -> 主键
PACKAGING_TABLES = {
    "kb_packaging_box_type": ("box_type_code",),
    "kb_packaging_part_template": ("part_code",),
    "kb_packaging_process_template": ("box_type_code", "part_code", "seq"),
    "kb_packaging_insert_accessory": ("accessory_code",),
    "kb_packaging_cost_formula": ("formula_code",),
    "kb_packaging_logistics_rule": ("rule_code",),
    "kb_packaging_match_weight": ("dimension",),
}
COMMON_COLS = ("industry", "source", "version", "effective_from", "status")
SEED_COUNTS = {
    "kb_packaging_box_type": 12,
    "kb_packaging_part_template": 31,
    "kb_packaging_process_template": 23,
    "kb_packaging_insert_accessory": 12,
}
MATCH_DIMENSIONS = ("size_range", "fit_clearance", "face_paper_gsm", "closure_type", "v_groove")


def _create_statements(text: str) -> dict:
    """把一份 DDL 文本解析成 {表名: 该表 CREATE 语句}。"""
    out: dict = {}
    for match in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(", text):
        name = match.group(1)
        start = match.end() - 1
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    out[name] = text[match.start():index + 1]
                    break
    return out


def _cpq_kb_statements() -> dict:
    """cpq_kb._DDL_TEMPLATE -> {表名: 语句}（含 {schema} 占位符）。"""
    out: dict = {}
    for statement in cpq_kb._DDL_TEMPLATE:
        try:
            out[cpq_kb._statement_name(statement)] = statement
        except AttributeError:                                       # pragma: no cover
            continue
    return out


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_seed_module():
    try:
        return importlib.import_module("tech_app.backend.storage.da_seed_packaging")
    except ImportError:
        return None


class _TempSqliteMixin:
    """把 DA_DB_PATH 指到临时库，绝不碰仓库里的 tech_data/da.db。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_file = pathlib.Path(self._tmpdir.name) / "da.db"
        self._patch = mock.patch.object(config, "DA_DB_PATH", self.db_file)
        self._patch.start()
        da_db.close_conn()

    def tearDown(self):
        da_db.close_conn()
        self._patch.stop()
        self._tmpdir.cleanup()

    def _seed_module(self):
        module = _load_seed_module()
        self.assertIsNotNone(module, "缺少 tech_app/backend/storage/da_seed_packaging.py")
        return module

    def _sqlite(self):
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn

    def _count(self, table: str) -> int:
        with self._sqlite() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])

    def _rows(self, table: str, order: str = "") -> list:
        with self._sqlite() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM %s %s" % (table, order)).fetchall()]


# --------------------------------------------------------------------------- #
# A. 表结构与行业列
# --------------------------------------------------------------------------- #
class ASchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sqlite_stmts = _create_statements(SCHEMA_SQL.read_text(encoding="utf-8"))
        cls.pg_stmts = _cpq_kb_statements()

    def test_a1_cpq_kb_lists_seven_packaging_tables(self):
        missing = [t for t in PACKAGING_TABLES if t not in cpq_kb.KB_TABLES]
        self.assertFalse(missing, f"cpq_kb.KB_TABLES 缺包装表：{missing}")

    def test_a2_cpq_kb_keys_match_sqlite_primary_keys(self):
        for table, keys in PACKAGING_TABLES.items():
            self.assertEqual(tuple(cpq_kb.KB_KEYS.get(table) or ()), keys,
                             f"KB_KEYS['{table}'] 与 SQLite 主键不一致（导入器幂等会失效）")

    def test_a3_sqlite_has_seven_packaging_tables(self):
        missing = [t for t in PACKAGING_TABLES if t not in self.sqlite_stmts]
        self.assertFalse(missing, f"da_schema.sql 缺包装表：{missing}")

    def test_a4_packaging_tables_exist_in_cpq_kb_ddl(self):
        missing = [t for t in PACKAGING_TABLES if t not in self.pg_stmts]
        self.assertFalse(missing, f"cpq_kb DDL 缺包装表：{missing}")

    def test_a5_sqlite_subject_tables_have_industry_column(self):
        missing = [t for t in SUBJECT_TABLES
                   if t not in self.sqlite_stmts or "industry" not in self.sqlite_stmts[t]]
        self.assertFalse(missing, f"da_schema.sql 这些主体表没有 industry 列：{missing}")

    def test_a6_pg_ddl_subject_tables_have_industry_column(self):
        missing = [t for t in SUBJECT_TABLES
                   if t not in self.pg_stmts or "industry" not in self.pg_stmts[t]]
        self.assertFalse(missing, f"cpq_kb DDL 这些主体表没有 industry 列：{missing}")

    def test_a7_packaging_tables_carry_metadata_columns(self):
        missing = [t for t in PACKAGING_TABLES if t not in self.sqlite_stmts]
        self.assertFalse(missing, f"da_schema.sql 缺包装表：{missing}")
        for table in PACKAGING_TABLES:
            statement = self.sqlite_stmts[table]
            for column in COMMON_COLS + ("created_at", "updated_at"):
                self.assertIn(column, statement, f"{table} 缺公共列 {column}")

    def test_a8_added_columns_cover_industry_for_legacy_dbs(self):
        rendered = " ".join(str(entry) for entry in cpq_kb._ADDED_COLUMNS)
        for table in SUBJECT_TABLES:
            self.assertIn(table, rendered, f"_ADDED_COLUMNS 没有给 {table} 补 industry 的增量列")
        self.assertIn("industry", rendered, "_ADDED_COLUMNS 必须包含 industry 增量列")

    def test_a9_match_weight_dimension_closed_set(self):
        statement = self.sqlite_stmts.get("kb_packaging_match_weight", "")
        for dimension in MATCH_DIMENSIONS:
            self.assertIn(dimension, statement,
                          f"kb_packaging_match_weight 必须覆盖五维匹配的 {dimension}")

    def test_a10_existing_twenty_tables_keep_their_order(self):
        self.assertEqual(tuple(cpq_kb.KB_TABLES[:20]), (
            "kb_component", "kb_component_param", "kb_component_feature",
            "kb_component_drawing", "kb_component_embedding", "kb_standard_part",
            "kb_equipment_class", "kb_equipment", "kb_process_step",
            "kb_process_param_template", "kb_process_route", "kb_process_route_step",
            "kb_inspection_item", "kb_material", "kb_material_property",
            "kb_material_price", "kb_supplier", "kb_supplier_capability",
            "kb_cost_rate", "kb_cost_factor"),
            "既有 20 张表的名字与顺序不得变动（快照契约）")


# --------------------------------------------------------------------------- #
# B. 演示数据与幂等
# --------------------------------------------------------------------------- #
class BSeedDataTest(_TempSqliteMixin, unittest.TestCase):
    def _seed(self, overwrite: bool = False):
        module = self._seed_module()
        count_fn = getattr(module, "seed_packaging", None)
        if not callable(count_fn):
            self.fail("da_seed_packaging 必须提供 seed_packaging(*, overwrite=False)")
        return count_fn(overwrite=overwrite)

    def test_b1_seed_packaging_entrypoint_and_idempotent_by_design(self):
        module = self._seed_module()
        self.assertTrue(callable(getattr(module, "seed_all", None)),
                        "da_seed_packaging 必须提供 seed_all()")

    def test_b2_box_type_count_is_twelve(self):
        self._seed()
        self.assertEqual(self._count("kb_packaging_box_type"), SEED_COUNTS["kb_packaging_box_type"])

    def test_b3_part_template_count_is_thirty_one(self):
        self._seed()
        self.assertEqual(self._count("kb_packaging_part_template"),
                         SEED_COUNTS["kb_packaging_part_template"])

    def test_b4_process_template_count_is_twenty_three(self):
        self._seed()
        self.assertEqual(self._count("kb_packaging_process_template"),
                         SEED_COUNTS["kb_packaging_process_template"])

    def test_b5_insert_accessory_count_is_twelve(self):
        self._seed()
        self.assertEqual(self._count("kb_packaging_insert_accessory"),
                         SEED_COUNTS["kb_packaging_insert_accessory"])

    def test_b6_every_row_is_tagged_packaging_with_source_and_status(self):
        self._seed()
        for table in PACKAGING_TABLES:
            rows = self._rows(table)
            self.assertTrue(rows, f"{table} 没有数据")
            for row in rows:
                self.assertEqual(row.get("industry"), "packaging", f"{table} 行业标记错误")
                self.assertTrue(str(row.get("source") or "").strip(), f"{table} 缺 source")
                self.assertTrue(str(row.get("version") or "").strip(), f"{table} 缺 version")
                self.assertTrue(str(row.get("effective_from") or "").strip(),
                                f"{table} 缺 effective_from")
                self.assertTrue(str(row.get("status") or "").strip(), f"{table} 缺 status")

    def test_b7_box_type_rows_keep_full_business_fields(self):
        self._seed()
        row = self._rows("kb_packaging_box_type", "ORDER BY box_type_code")[0]
        for column in ("name", "family", "fit_clearance", "grey_board_thickness",
                       "face_paper_gsm", "closure_type", "part_count", "v_groove",
                       "standard_seconds", "automation_level"):
            self.assertIsNotNone(row.get(column), f"盒型缺字段 {column}")
            self.assertNotEqual(str(row.get(column)).strip(), "", f"盒型字段 {column} 为空")

    def test_b8_part_templates_keep_parseable_size_formula(self):
        self._seed()
        rows = self._rows("kb_packaging_part_template", "ORDER BY part_code")
        self.assertTrue(any(str(r.get("size_expr") or "").strip() for r in rows),
                        "部件模板必须保留尺寸公式 size_expr")
        self.assertTrue(any(str(r.get("size_length_expr") or "").strip() for r in rows),
                        "部件模板必须拆出 size_length_expr")
        sample = rows[0]
        self.assertRegex(str(sample.get("size_expr")), r"[LWtHc]",
                         "size_expr 必须是参数表达式（如 L+4t+2c），不能只有示例数值")

    def test_b9_process_templates_keep_route_metadata(self):
        self._seed()
        row = self._rows("kb_packaging_process_template", "ORDER BY box_type_code, part_code, seq")[0]
        for column in ("step_name", "workstation", "work_content", "standard_seconds",
                       "automation", "control_point", "parallel_ok"):
            self.assertIsNotNone(row.get(column), f"工艺模板缺字段 {column}")

    def test_b10_accessories_keep_cost_and_eco_fields(self):
        self._seed()
        row = self._rows("kb_packaging_insert_accessory", "ORDER BY accessory_code")[0]
        for column in ("name", "material", "forming", "tooling_cost",
                       "unit_cost_min", "eco_attr", "moq"):
            self.assertIsNotNone(row.get(column), f"内托/配件缺字段 {column}")

    def test_b11_costing_rows_keep_minimum_charge_and_gap_metadata(self):
        self._seed()
        formulas = self._rows("kb_packaging_cost_formula")
        self.assertTrue(formulas, "kb_packaging_cost_formula 必须有数据")
        for row in formulas:
            self.assertIsNotNone(row.get("minimum_charge"),
                                 "成本公式必须显式写 minimum_charge（没有就写 0）")
            self.assertIsNotNone(row.get("loss_scope"), "成本公式必须写 loss_scope")
            self.assertIsNotNone(row.get("review_status"), "成本公式必须写 review_status")
        weights = self._rows("kb_packaging_match_weight")
        self.assertEqual({r["dimension"] for r in weights}, set(MATCH_DIMENSIONS),
                         "匹配权重必须正好覆盖五维")

    def test_b12_seed_is_idempotent(self):
        self._seed()
        before = {t: self._count(t) for t in PACKAGING_TABLES}
        self._seed()
        after = {t: self._count(t) for t in PACKAGING_TABLES}
        self.assertEqual(before, after, "重复 seed 不得新增行")

    def test_b13_seed_does_not_overwrite_user_edits(self):
        self._seed()
        with self._sqlite() as conn:
            conn.execute("UPDATE kb_packaging_box_type SET name = ? WHERE box_type_code = ?",
                         ("人工改过的盒型名", "YT-RB-01001-A"))
            conn.commit()
        self._seed()
        row = [r for r in self._rows("kb_packaging_box_type")
               if r["box_type_code"] == "YT-RB-01001-A"][0]
        self.assertEqual(row["name"], "人工改过的盒型名",
                         "默认 seed 不得覆盖用户改过的行")

    def test_b14_force_overwrite_replaces_existing_rows(self):
        self._seed()
        with self._sqlite() as conn:
            conn.execute("UPDATE kb_packaging_box_type SET name = ? WHERE box_type_code = ?",
                         ("人工改过的盒型名", "YT-RB-01001-A"))
            conn.commit()
        self._seed(overwrite=True)
        row = [r for r in self._rows("kb_packaging_box_type")
               if r["box_type_code"] == "YT-RB-01001-A"][0]
        self.assertNotEqual(row["name"], "人工改过的盒型名",
                            "overwrite=True 必须恢复样例数据")

    def test_b15_seed_never_touches_pg_or_snapshot(self):
        module = self._seed_module()
        source = pathlib.Path(SEED_PACKAGING_PY).read_text(encoding="utf-8")
        self.assertNotIn("psycopg", source, "种子脚本不得连 Postgres")
        self.assertNotIn("cpq_kb_client", source, "种子脚本不得拉知识库快照")
        with mock.patch.object(cpq_kb_client, "fetch_snapshot",
                               side_effect=AssertionError("种子脚本不允许拉快照")):
            counts = module.seed_packaging()
        self.assertIsInstance(counts, dict)

    def test_b16_packaging_materials_and_rates_are_seeded(self):
        self._seed()
        materials = self._rows("kb_material")
        packaging_materials = [r for r in materials if r.get("industry") == "packaging"]
        self.assertGreaterEqual(len(packaging_materials), 3,
                                "至少要有 3 条包装物料（灰板/面纸/内衬纸）")
        rates = [r for r in self._rows("kb_cost_rate") if r.get("industry") == "packaging"]
        self.assertGreaterEqual(len(rates), 5, "至少要有 5 条包装费率（含最低收费）")
        with_floor = [r for r in rates if r.get("minimum_charge")]
        self.assertGreaterEqual(len(with_floor), 2, "至少 2 条费率要带最低收费")
        factors = [r for r in self._rows("kb_cost_factor") if r.get("industry") == "packaging"]
        self.assertTrue(factors, "包装必须有损耗率/税率系数")

    def test_b17_seed_writes_only_local_sqlite(self):
        self._seed()
        self.assertTrue(self.db_file.exists(), "种子必须写本地 SQLite 源库")
        self.assertEqual(self._count("kb_packaging_box_type"), 12)


# --------------------------------------------------------------------------- #
# C. 行业隔离检索
# --------------------------------------------------------------------------- #
class CIndustryScopeTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(kb_repo._CACHE)
        self._tables = {
            "kb_material": [
                {"material_code": "MAT-PKG-GREY", "name": "灰板 2.0mm", "grade": "2.0mm",
                 "category": "包材", "status": "active", "industry": "packaging"},
                {"material_code": "MAT-STL-Q235", "name": "Q235 钢板", "grade": "Q235",
                 "category": "金属", "status": "active", "industry": "semiconductor"},
                {"material_code": "MAT-LEGACY-SAND", "name": "通用砂纸", "grade": "-",
                 "category": "耗材辅料", "status": "active"},
            ],
            "kb_material_price": [
                {"price_id": 1, "material_code": "MAT-PKG-GREY", "price": 6.5,
                 "valid_from": "2026-01-01 00:00:00", "confidence": 1.0},
            ],
            "kb_cost_rate": [
                {"rate_code": "RATE-PKG-HANDMOUNT", "name": "手裱工时", "rate_type": "labor",
                 "scope_type": "global", "value": 42.0, "unit": "元/小时",
                 "effective_from": "2026-01-01 00:00:00", "industry": "packaging"},
                {"rate_code": "RATE-CNC", "name": "机加工工时", "rate_type": "labor",
                 "scope_type": "global", "value": 85.0, "unit": "元/小时",
                 "effective_from": "2026-01-01 00:00:00", "industry": "semiconductor"},
                {"rate_code": "RATE-COMMON", "name": "通用管理费", "rate_type": "overhead",
                 "scope_type": "global", "value": 12.0, "unit": "元/小时",
                 "effective_from": "2026-01-01 00:00:00"},
            ],
            "kb_cost_factor": [
                {"factor_code": "F-PKG-LOSS", "factor_type": "scrap", "value": 0.05,
                 "effective_from": "2026-01-01 00:00:00", "industry": "packaging"},
                {"factor_code": "F-SEMI-LOSS", "factor_type": "scrap", "value": 0.02,
                 "effective_from": "2026-01-01 00:00:00", "industry": "semiconductor"},
            ],
            "kb_component": [
                {"component_id": "C-PKG-1", "component_code": "CMP-PKG-0001",
                 "name": "天地盖灰板", "category": "包材件", "lifecycle": "active",
                 "envelope_l": 207.6, "envelope_w": 157.6, "envelope_h": 2.0,
                 "reuse_count": 3, "industry": "packaging"},
                {"component_id": "C-SEMI-1", "component_code": "CMP-SEMI-0001",
                 "name": "静电吸盘", "category": "结构件", "lifecycle": "active",
                 "envelope_l": 207.6, "envelope_w": 157.6, "envelope_h": 2.0,
                 "reuse_count": 3, "industry": "semiconductor"},
            ],
            "kb_component_param": [],
            "kb_component_feature": [],
            "kb_process_route": [
                {"route_code": "RT-PKG-BOX", "name": "礼盒成型路线", "status": "active",
                 "applicable_category": "包材件", "industry": "packaging"},
                {"route_code": "RT-CNC", "name": "CNC 路线", "status": "active",
                 "applicable_category": "结构件", "industry": "semiconductor"},
            ],
            "kb_process_route_step": [],
            "kb_process_step": [],
            "kb_packaging_box_type": [
                {"box_type_code": "YT-RB-01001-A", "name": "天地盖盒（全盖）",
                 "industry": "packaging"},
                {"box_type_code": "YT-RB-02001-A", "name": "书型盒", "industry": "packaging"},
            ],
            "kb_packaging_part_template": [
                {"part_code": "RB01001-P01", "box_type_code": "YT-RB-01001-A",
                 "name": "盖面", "industry": "packaging"},
            ],
            "kb_packaging_process_template": [
                {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P01", "seq": 10,
                 "step_name": "灰板开料", "industry": "packaging"},
            ],
            "kb_packaging_insert_accessory": [
                {"accessory_code": "YT-IN-001", "name": "EVA 植绒内托",
                 "industry": "packaging"},
            ],
        }
        kb_repo._CACHE["version"] = 4242
        kb_repo._CACHE["tables"] = self._tables

    def tearDown(self):
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._saved)

    def _require_industry(self, fn, label):
        parameters = inspect.signature(fn).parameters
        self.assertIn("industry", parameters,
                      f"{label} 必须接受 industry 关键字参数（行业隔离的唯一入口）")

    def test_c1_list_materials_excludes_other_industry(self):
        self._require_industry(kb_repo.list_materials, "kb_repo.list_materials")
        codes = {r["material_code"] for r in kb_repo.list_materials(industry="semiconductor")}
        self.assertNotIn("MAT-PKG-GREY", codes, "半导体检索不得命中包装物料")
        self.assertIn("MAT-STL-Q235", codes)
        self.assertIn("MAT-LEGACY-SAND", codes, "空行业的既有行是通用，必须继续可见")

    def test_c2_list_materials_packaging_sees_own_and_universal(self):
        self._require_industry(kb_repo.list_materials, "kb_repo.list_materials")
        codes = {r["material_code"] for r in kb_repo.list_materials(industry="packaging")}
        self.assertIn("MAT-PKG-GREY", codes)
        self.assertIn("MAT-LEGACY-SAND", codes)
        self.assertNotIn("MAT-STL-Q235", codes)

    def test_c3_no_industry_keeps_legacy_behaviour(self):
        codes = {r["material_code"] for r in kb_repo.list_materials()}
        self.assertEqual(codes, {"MAT-PKG-GREY", "MAT-STL-Q235", "MAT-LEGACY-SAND"},
                         "不传 industry 时必须不过滤（三行业默认路径逐字不变）")

    def test_c4_current_price_never_falls_back_across_industries(self):
        self._require_industry(kb_repo.current_price, "kb_repo.current_price")
        hit = kb_repo.current_price("MAT-PKG-GREY", industry="packaging")
        self.assertIsNotNone(hit, "包装物料必须能取到自己的价格")
        self.assertIsNone(kb_repo.current_price("MAT-PKG-GREY", industry="semiconductor"),
                          "不得把包装价格给到半导体")

    def test_c5_effective_rate_is_scoped(self):
        self._require_industry(kb_repo.effective_rate, "kb_repo.effective_rate")
        pkg = kb_repo.effective_rate("labor", industry="packaging")
        self.assertEqual((pkg or {}).get("rate_code"), "RATE-PKG-HANDMOUNT")
        semi = kb_repo.effective_rate("labor", industry="semiconductor")
        self.assertEqual((semi or {}).get("rate_code"), "RATE-CNC")
        self.assertNotEqual((semi or {}).get("rate_code"), "RATE-PKG-HANDMOUNT")

    def test_c6_effective_rate_keeps_universal_rows_visible(self):
        self._require_industry(kb_repo.effective_rate, "kb_repo.effective_rate")
        common = kb_repo.effective_rate("overhead", industry="packaging")
        self.assertEqual((common or {}).get("rate_code"), "RATE-COMMON",
                         "空行业的通用费率对任何行业都可见")

    def test_c7_effective_factor_is_scoped(self):
        self._require_industry(kb_repo.effective_factor, "kb_repo.effective_factor")
        pkg = kb_repo.effective_factor("scrap", industry="packaging")
        self.assertEqual((pkg or {}).get("factor_code"), "F-PKG-LOSS")
        semi = kb_repo.effective_factor("scrap", industry="semiconductor")
        self.assertEqual((semi or {}).get("factor_code"), "F-SEMI-LOSS")

    def test_c8_recommend_components_is_scoped(self):
        self._require_industry(kb_repo.recommend_components, "kb_repo.recommend_components")
        # 两个候选包络完全一致，唯一差别是行业 —— 不按行业收口就会同时命中。
        part = {"features": [{"type": "box", "length": 207.6, "width": 157.6, "height": 2.0}]}
        unfiltered = {r["component_code"] for r in kb_repo.recommend_components(part)}
        self.assertEqual(unfiltered, {"CMP-PKG-0001", "CMP-SEMI-0001"},
                         "前置条件：不过滤时两个候选都该命中")
        pkg = {r["component_code"] for r in
               kb_repo.recommend_components(part, industry="packaging")}
        self.assertEqual(pkg, {"CMP-PKG-0001"}, "包装检索只能命中包装候选")
        semi = {r["component_code"] for r in
                kb_repo.recommend_components(part, industry="semiconductor")}
        self.assertEqual(semi, {"CMP-SEMI-0001"}, "半导体检索只能命中半导体候选")

    def test_c9_recommend_routes_is_scoped(self):
        self._require_industry(kb_repo.recommend_routes, "kb_repo.recommend_routes")
        own = {r["route_code"] for r in
               kb_repo.recommend_routes(category="包材件", industry="packaging")}
        self.assertIn("RT-PKG-BOX", own)
        cross = {r["route_code"] for r in
                 kb_repo.recommend_routes(category="结构件", industry="packaging")}
        self.assertNotIn("RT-CNC", cross, "包装检索不得命中半导体路线")

    def test_c10_packaging_box_type_query_exists(self):
        fn = getattr(kb_repo, "packaging_box_types", None)
        self.assertTrue(callable(fn), "kb_repo 必须提供 packaging_box_types()")
        rows = fn()
        self.assertEqual({r["box_type_code"] for r in rows},
                         {"YT-RB-01001-A", "YT-RB-02001-A"})

    def test_c11_packaging_part_template_query_filters_by_box(self):
        fn = getattr(kb_repo, "packaging_part_templates", None)
        self.assertTrue(callable(fn), "kb_repo 必须提供 packaging_part_templates(box_type_code)")
        self.assertEqual([r["part_code"] for r in fn("YT-RB-01001-A")], ["RB01001-P01"])
        self.assertEqual(fn("YT-RB-02001-A"), [])

    def test_c12_packaging_process_template_query_filters(self):
        fn = getattr(kb_repo, "packaging_process_templates", None)
        self.assertTrue(callable(fn), "kb_repo 必须提供 packaging_process_templates(...)")
        self.assertEqual([r["step_name"] for r in fn(box_type_code="YT-RB-01001-A")],
                         ["灰板开料"])
        self.assertEqual(fn(part_code="RB01001-P01")[0]["seq"], 10)

    def test_c13_packaging_insert_accessory_query_exists(self):
        fn = getattr(kb_repo, "packaging_insert_accessories", None)
        self.assertTrue(callable(fn), "kb_repo 必须提供 packaging_insert_accessories()")
        self.assertEqual([r["accessory_code"] for r in fn()], ["YT-IN-001"])

    def test_c14_rows_without_industry_key_are_treated_as_universal(self):
        self._require_industry(kb_repo.list_materials, "kb_repo.list_materials")
        kb_repo._CACHE["tables"]["kb_material"].append(
            {"material_code": "MAT-NO-COL", "name": "老行", "status": "active"})
        codes = {r["material_code"] for r in kb_repo.list_materials(industry="battery")}
        self.assertIn("MAT-NO-COL", codes, "快照缺 industry 键的老行必须按通用处理，不得抛错")


# --------------------------------------------------------------------------- #
# D. 导入器与快照
# --------------------------------------------------------------------------- #
class DImporterTest(_TempSqliteMixin, unittest.TestCase):
    def _seed_once(self):
        module = self._seed_module()
        seed = getattr(module, "seed_packaging", None)
        if not callable(seed):
            self.fail("da_seed_packaging 必须提供 seed_packaging(*, overwrite=False)")
        seed()

    def test_d1_dry_run_reports_packaging_tables_and_keeps_source_intact(self):
        self._seed_once()
        before = _sha256(self.db_file)
        proc = subprocess.run(
            [sys.executable, str(IMPORTER_PY), "--source", str(self.db_file),
             "--dry-run", "--json"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        report = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(report["dry_run"])
        for table in PACKAGING_TABLES:
            self.assertIn(table, report["tables"], f"导入器统计里没有 {table}")
            self.assertGreater(report["tables"][table], 0, f"{table} 应统计到行")
        self.assertEqual(_sha256(self.db_file), before, "导入器不得改动源库")

    def test_d2_kb_tables_cover_packaging_for_snapshot(self):
        snapshot_tables = set(cpq_kb.KB_TABLES)
        self.assertTrue(set(PACKAGING_TABLES).issubset(snapshot_tables),
                        "快照表清单必须包含包装 7 张表")


# --------------------------------------------------------------------------- #
# E. 非回归
# --------------------------------------------------------------------------- #
class ENonRegressionTest(unittest.TestCase):
    def test_e1_legacy_seeds_still_importable(self):
        from tech_app.backend.storage import da_seed
        from tech_app.backend.storage import da_seed_battery
        self.assertTrue(callable(da_seed.seed_all))
        self.assertTrue(callable(da_seed_battery.seed_all))

    def test_e2_industry_scoping_defaults_to_none(self):
        parameters = inspect.signature(kb_repo.list_materials).parameters
        industry = parameters.get("industry")
        self.assertIsNotNone(industry, "list_materials 必须支持 industry=")
        self.assertIsNone(industry.default,
                          "industry 默认必须是 None（不传时保持既有全库行为）")

    def test_e3_schema_sql_still_has_twenty_existing_kb_tables(self):
        statements = _create_statements(SCHEMA_SQL.read_text(encoding="utf-8"))
        for table in cpq_kb.KB_TABLES[:20]:
            self.assertIn(table, statements, f"da_schema.sql 丢了既有表 {table}")


if __name__ == "__main__":
    unittest.main()
