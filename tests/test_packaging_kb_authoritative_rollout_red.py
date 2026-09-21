"""红测：包装知识库权威数据入库与 34 上线预检。

Spec：`docs/specs/packaging-kb-authoritative-rollout.md`
前置（已完成，不在本批范围）：`cpq_kb`（PG DDL + 整包快照）、`da_schema.sql`、
`da_seed_packaging.py`、`scripts/import_da_kb_to_pg.py`、`tech_app/tools/extract_packaging_rules.py`。

现状缺口（实测，不是推断）：

  · 34 上 `relation "cpq_kb.kb_packaging_box_type" does not exist`：包装知识库检索 503、
    `4.1` 成本全部失败 —— 本地有 DDL 与 seed，但**没有任何上线预检**，缺表照样上线。
  · 三处不一致（本地实测）：`da_schema.sql` 有 29 张 `kb_*` 表，`cpq_kb.KB_TABLES` 只有 27 张 ——
    `in sqlite not in pg: ['kb_packaging_cost_content', 'kb_packaging_tooling_rule']`；
    而 `kb_repo.py:928/936` 正是从快照读这两张表，`da_seed_packaging.py` 也 seed 它们 →
    走 PG 快照的 34 上，这两张表**永远是空的**。
  · 9 张包装表没有「演示 vs 权威」的可判定字段：`grep -c "source_type" cpq_kb.py` → 0；
    演示的 12 条盒型一旦灌进生产，就会被当成真实可报价盒型推荐给客户。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写生产数据；
  · 只读源码与纯函数（预检函数按 Spec §5 是纯函数，红测直接喂 fixture）；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_SQL = ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql"
SEED_PY = ROOT / "tech_app" / "backend" / "storage" / "da_seed_packaging.py"
KB_REPO_PY = ROOT / "tech_app" / "backend" / "storage" / "kb_repo.py"
QUOTE_KB_PY = ROOT / "cpq_kb.py"
IMPORT_PY = ROOT / "scripts" / "import_da_kb_to_pg.py"
PREFLIGHT_PY = ROOT / "tech_app" / "tools" / "kb_deploy_preflight.py"
DEPLOYMENT_MD = ROOT / "DEPLOYMENT.md"

import cpq_kb  # noqa: E402

#: 9 张包装扩展表（SQLite schema 口径）。
PACKAGING_TABLES = (
    "kb_packaging_box_type", "kb_packaging_part_template", "kb_packaging_process_template",
    "kb_packaging_insert_accessory", "kb_packaging_cost_formula",
    "kb_packaging_logistics_rule", "kb_packaging_match_weight",
    "kb_packaging_cost_content", "kb_packaging_tooling_rule",
)
#: 关键主体表（预检要求非空）。
REQUIRED_TABLES = PACKAGING_TABLES[:7]
#: 既有 27 张（本批只允许追加，不允许改动相对顺序）。
EXISTING_ORDER = tuple(cpq_kb.KB_TABLES)
#: 06-成本 与 07-刀模 两张包装表：本地有、快照没有（实测）。
MISSING_FROM_PG = ("kb_packaging_cost_content", "kb_packaging_tooling_rule")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def sqlite_kb_tables() -> tuple:
    return tuple(sorted(set(re.findall(r"CREATE TABLE IF NOT EXISTS (kb_[a-z_]+)",
                                       read(SCHEMA_SQL)))))


def ddl_statements() -> tuple:
    return tuple(cpq_kb._DDL_TEMPLATE) if hasattr(cpq_kb, "_DDL_TEMPLATE") else ()


def ddl_names() -> tuple:
    return tuple(sorted({re.search(r"CREATE TABLE IF NOT EXISTS \{schema\}\.(\w+)", s).group(1)
                         for s in ddl_statements()
                         if re.search(r"CREATE TABLE IF NOT EXISTS \{schema\}\.(\w+)", s)}))


def packaging_tables_named_in(text: str) -> set:
    return {name for name in PACKAGING_TABLES if name in text}


def preflight_module():
    try:
        return importlib.import_module("tech_app.tools.kb_deploy_preflight")
    except Exception:                                  # noqa: BLE001 - 红测自己给结论
        return None


def fixture_tables(*, source_type: str = "workbook", rows: int = 1) -> dict:
    """全表都在、关键包装表各 rows 行的快照形状（Spec §5）。"""
    tables = {name: [] for name in cpq_kb.KB_TABLES}
    for name in PACKAGING_TABLES:
        tables[name] = [{"source_type": source_type} for _ in range(rows)]
    return tables


# --------------------------------------------------------------------------- #
# A. 三处一致（Spec §2）
# --------------------------------------------------------------------------- #
class AThreeWayParity(unittest.TestCase):
    maxDiff = None

    def test_a1_kb_tables_cover_every_sqlite_kb_table(self):
        missing = [t for t in sqlite_kb_tables() if t not in cpq_kb.KB_TABLES]
        self.assertEqual(missing, [],
                         "KB_TABLES 缺表，快照里永远没有它们（Spec §2.1）：%s" % missing)

    def test_a2_kb_keys_cover_every_table_and_order_is_frozen(self):
        missing = [t for t in cpq_kb.KB_TABLES if t not in cpq_kb.KB_KEYS]
        self.assertEqual(missing, [], "KB_KEYS 必须覆盖全部 KB_TABLES（Spec §2.2）：%s" % missing)
        kept = [t for t in cpq_kb.KB_TABLES if t in EXISTING_ORDER]
        self.assertEqual(kept, list(EXISTING_ORDER),
                         "既有 27 张表的相对顺序被改动（新增只能追加，Spec §2.3）")

    def test_a3_pg_ddl_covers_every_kb_table(self):
        names = set(ddl_names())
        missing = [t for t in cpq_kb.KB_TABLES if t not in names]
        self.assertEqual(missing, [], "PG DDL 缺建表语句（Spec §2.4）：%s" % missing)
        for table in MISSING_FROM_PG:
            self.assertIn(table, names, "%s 必须能建到 cpq_kb（Spec §2.1）" % table)

    def test_a4_reader_tables_are_all_in_the_snapshot(self):
        bad = sorted(t for t in packaging_tables_named_in(read(KB_REPO_PY))
                     if t not in cpq_kb.KB_TABLES)
        self.assertEqual(bad, [],
                         "kb_repo 在读快照里没有的表（本地有、快照没有，Spec §2.5）：%s" % bad)

    def test_a5_seeded_tables_are_all_in_the_snapshot(self):
        bad = sorted(t for t in packaging_tables_named_in(read(SEED_PY))
                     if t not in cpq_kb.KB_TABLES)
        self.assertEqual(bad, [],
                         "seed 写的表不在快照清单里（Spec §2.5）：%s" % bad)


# --------------------------------------------------------------------------- #
# B. 数据分层（Spec §3）
# --------------------------------------------------------------------------- #
class BProvenance(unittest.TestCase):
    maxDiff = None

    def test_b1_source_type_and_review_status_lists_exist(self):
        self.assertTrue(hasattr(cpq_kb, "SOURCE_TYPES"),
                        "cpq_kb 必须导出 SOURCE_TYPES（Spec §3）")
        self.assertEqual(tuple(cpq_kb.SOURCE_TYPES),
                         ("demo", "workbook", "dwg_confirmed", "unknown"))
        self.assertTrue(hasattr(cpq_kb, "REVIEW_STATUSES"),
                        "cpq_kb 必须导出 REVIEW_STATUSES（Spec §3）")
        self.assertEqual(tuple(cpq_kb.REVIEW_STATUSES), ("draft", "reviewed", "retired"))

    def test_b2_both_schemas_declare_the_provenance_columns(self):
        sqlite_src, pg_src = read(SCHEMA_SQL), read(QUOTE_KB_PY)
        for table in PACKAGING_TABLES:
            for src, label in ((sqlite_src, "da_schema.sql"), (pg_src, "cpq_kb.py")):
                block = _table_block(src, table)
                self.assertTrue(block, "%s 里找不到 %s 的建表语句（Spec §3）" % (label, table))
                self.assertIn("source_type", block,
                              "%s 的 %s 缺 source_type 列（Spec §3）" % (label, table))

    def test_b3_demo_seed_rows_are_marked_demo(self):
        src = read(SEED_PY)
        self.assertIn("source_type", src, "演示数据必须显式标成 demo（Spec §3）")
        m = re.search(r"def _packaging_row\(.*?\n(.*?)\n\n", src, re.S)
        self.assertIsNotNone(m, "找不到 _packaging_row()")
        self.assertIn('"demo"', m.group(1),
                      "seed 的公共行必须写 source_type='demo'（Spec §3）")

    def test_b4_rule_snapshot_rows_are_marked_workbook_with_source_ref(self):
        src = read(SEED_PY)
        m = re.search(r"def seed_packaging_cost_rules\(.*?\n(.*?)\n\n\n", src, re.S)
        self.assertIsNotNone(m, "找不到 seed_packaging_cost_rules()")
        body = m.group(1)
        self.assertIn('"workbook"', body, "权威规则行必须标 source_type='workbook'（Spec §3）")
        self.assertIn("source_ref", body)

    def test_b5_dwg_confirmed_channel_columns_exist(self):
        for src, label in ((read(SCHEMA_SQL), "da_schema.sql"), (read(QUOTE_KB_PY), "cpq_kb.py")):
            for column in ("source_sha256", "parser_version", "confirmed_by"):
                self.assertIn(column, src,
                              "%s 缺 DWG 确认样本列 %s（Spec §3）" % (label, column))

    def test_b6_cost_engine_still_only_accepts_reviewed_rows(self):
        cost = read(ROOT / "tech_app" / "backend" / "services" / "packaging_cost.py")
        self.assertIn('review_status', cost)
        self.assertRegex(cost, r"review_status.{0,24}==\s*[\"']reviewed[\"']",
                         "成本引擎只认 reviewed 的口径被改动（Spec §3/§8）")


def _table_block(src: str, table: str) -> str:
    """取某张表在建表语句里的块（SQLite 与 PG 模板两种写法都认）。"""
    for pattern in (r"CREATE TABLE IF NOT EXISTS %s \((.*?)\n\);" % re.escape(table),
                    r"CREATE TABLE IF NOT EXISTS \{\{schema\}\}\.%s \((.*?)\n\);\"\"\""
                    % re.escape(table)):
        m = re.search(pattern, src, re.S)
        if m:
            return m.group(1)
    return ""


# --------------------------------------------------------------------------- #
# C. 导入器 / 版本 / 回滚（Spec §4）
# --------------------------------------------------------------------------- #
class CImportAndRollback(unittest.TestCase):
    maxDiff = None

    def test_c1_importer_is_dry_run_by_default(self):
        src = read(IMPORT_PY)
        self.assertIn("--confirm", src)
        self.assertRegex(src, r"dry_run\s*=\s*not\s+args\.confirm",
                         "导入器必须默认 dry-run、只有 --confirm 才写库（Spec §4.1）")

    def test_c2_version_bump_only_when_something_changed(self):
        src = read(QUOTE_KB_PY)
        self.assertRegex(src, r"def _bump_version\(cur,\s*changed",
                         "kb_version 递增必须由 changed 决定（Spec §4.2）")
        self.assertRegex(src, r"changed\s*[<>]=?\s*0",
                         "changed 为 0 时不得递增 kb_version（Spec §4.2）")

    def test_c3_readonly_snapshot_export_exists(self):
        has_fn = hasattr(cpq_kb, "export_snapshot")
        has_cli = (ROOT / "scripts" / "kb_export_snapshot.py").exists()
        self.assertTrue(has_fn or has_cli,
                        "缺导入前快照/回滚入口：cpq_kb.export_snapshot() 或 "
                        "scripts/kb_export_snapshot.py（Spec §4.3）")

    def test_c4_import_report_gives_per_table_rows(self):
        src = read(IMPORT_PY)
        self.assertIn('"tables"', src, "导入报告必须逐表给行数（Spec §4.4）")
        self.assertIn('"kb_version"', src)


# --------------------------------------------------------------------------- #
# D. 上线预检与文档（Spec §5 / §6）
# --------------------------------------------------------------------------- #
class DPreflight(unittest.TestCase):
    maxDiff = None

    def _preflight(self):
        module = preflight_module()
        if module is None or not hasattr(module, "preflight"):
            self.fail("缺 tech_app/tools/kb_deploy_preflight.py 的纯函数 preflight()（Spec §5）")
        return module.preflight

    def test_d1_preflight_pure_function_exists(self):
        self._preflight()
        src = read(PREFLIGHT_PY)
        self.assertIn("--json", src)
        self.assertIn("kb_version", src)

    def test_d2_missing_table_is_no_go_and_named(self):
        preflight = self._preflight()
        tables = fixture_tables()
        for table in MISSING_FROM_PG:
            tables.pop(table)
        out = preflight(tables, env="production", kb_version=7)
        self.assertFalse(out["ok"], "缺表必须 no-go（Spec §5.3）：%s" % out)
        self.assertEqual(out["verdict"], "no-go")
        self.assertEqual(sorted(out["missing_tables"]), sorted(MISSING_FROM_PG))
        codes = {p["code"] for p in out["problems"]}
        self.assertIn("missing_tables", codes)

    def test_d3_production_refuses_demo_only_library(self):
        preflight = self._preflight()
        out = preflight(fixture_tables(source_type="demo"), env="production", kb_version=7)
        self.assertFalse(out["ok"], "生产库里全是演示数据必须 no-go（Spec §5.3）")
        codes = {p["code"] for p in out["problems"]}
        self.assertIn("demo_only", codes)
        self.assertEqual(out["provenance"]["demo"], len(PACKAGING_TABLES))

    def test_d4_kb_version_required_and_positive_path_passes(self):
        preflight = self._preflight()
        zero = preflight(fixture_tables(), env="production", kb_version=0)
        self.assertFalse(zero["ok"], "kb_version 为 0 必须 no-go（Spec §5.3）")
        self.assertIn("kb_version_missing", {p["code"] for p in zero["problems"]})
        good = preflight(fixture_tables(source_type="workbook"), env="production", kb_version=7)
        self.assertTrue(good["ok"], "权威数据齐备时必须 go（Spec §5.3）：%s" % good)
        self.assertEqual(good["verdict"], "go")

    def test_d5_deployment_doc_covers_the_kb_rollout(self):
        doc = read(DEPLOYMENT_MD)
        missing = [token for token in ("ensure_schema", "import_da_kb_to_pg.py",
                                       "kb_deploy_preflight.py", "--confirm", "回滚")
                   if token not in doc]
        self.assertEqual(missing, [],
                         "DEPLOYMENT.md 缺知识库上线小节（Spec §6）：缺 %s" % missing)


if __name__ == "__main__":
    unittest.main()
