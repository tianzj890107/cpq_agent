"""红测：包装知识库「样例 → 权威 → 生产可用」的单一通路。

Spec：`docs/specs/kb-authoritative-promotion-and-load.md`

现状（全部实测）：

  · 样例早已固化成代码：`da_seed_packaging.py` 里 `BOX_TYPES=12`、`PART_TEMPLATES=31`、
    `PROCESS_TEMPLATES=23`、`ACCESSORIES=12`，Sheet 名与 `SOURCE_*` 常量逐字对得上；
  · 但本地 `tech_app/tech_data/da.db` 里**没有** `kb_packaging_*` 任何一张表
    （`kb_material` 等也全是 0 行）→ seed 在本机从未落库；
  · 运行时读的不是 sqlite：`kb_repo.py:70` → `cpq_kb_client.fetch_snapshot()`
    → `GET /wf/tech/kb/snapshot`，缺令牌抛 `KbUnavailable`（拒绝静默降级成空库）；
    PG `cpq_kb` 表建了但**数据未灌**（预检 7 项 `empty_required_table`）；
  · 因此 `packaging_match.match_box_types()` 一个盒型/部件都匹配不出来；
  · 就算灌进去，seed 行 `source_type="demo"`，生产预检 `demo_only` 判 no-go 死循环。

本文件同时钉住"通路是通的"：把 seed 灌进临时 sqlite 再喂给 `kb_repo` 快照缓存，
匹配必须出候选（实测 12 个、建议盒型 `YT-RB-01001-A`）—— 这条是绿护栏，
证明问题不在算法，而在"没人把数据灌进去、也没有升格机制"。

纪律：全部离线；只在临时目录建 sqlite；不连 Postgres、不调模型、不起服务。
"""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import da_db, da_seed_packaging as seed, kb_repo  # noqa: E402
from tech_app.backend.services import packaging_match as match  # noqa: E402

PROVENANCE_JSON = (ROOT / "tech_app" / "agent_knowledge" / "provenance"
                   / "packaging_sources.json")
PREFLIGHT_PY = ROOT / "tech_app" / "tools" / "kb_deploy_preflight.py"
IMPORT_PY = ROOT / "scripts" / "import_da_kb_to_pg.py"

SNAPSHOT_TABLES = ("kb_packaging_box_type", "kb_packaging_part_template",
                   "kb_packaging_process_template", "kb_packaging_insert_accessory",
                   "kb_packaging_match_weight", "kb_packaging_cost_formula",
                   "kb_packaging_logistics_rule", "kb_packaging_cost_content",
                   "kb_packaging_tooling_rule")

MATCH_INPUTS = {"inner_length": 200, "inner_width": 120, "inner_height": 80,
                "closure_type": "天地盖", "v_groove": True, "face_paper_gsm": 200,
                "fit_clearance": 2.0}

AUTHORITY = {"workbook": "礼盒盒型库_数据样例.xlsx", "sheet": "01盒型",
             "owner": "业务", "decided_at": "2026-09-21",
             "sha256": "0" * 64}


def seed_into_temp_sqlite():
    """把 seed 灌进临时 sqlite，返回 {表名: [行]}（模拟 cpq_kb 快照的 tables）。"""
    path = pathlib.Path(tempfile.mkdtemp()) / "da.db"
    with mock.patch.object(config, "DA_DB_PATH", path):
        da_db.init_db()
        seed.seed_packaging()
        tables = {}
        for name in SNAPSHOT_TABLES:
            try:
                tables[name] = [dict(row) for row in
                                da_db.query("SELECT * FROM %s" % name)]
            except sqlite3.Error:
                tables[name] = []
    return path, tables


class APromotionMechanism(unittest.TestCase):
    """A 组：demo → 权威 必须有代码路径（现在完全没有）。"""

    def test_a1_promote_rows_exists(self):
        import cpq_kb
        self.assertTrue(hasattr(cpq_kb, "promote_rows"),
                        "缺少 cpq_kb.promote_rows()：demo → workbook 没有任何代码路径")

    def test_a2_promotion_is_one_way_and_requires_authority(self):
        import cpq_kb
        rows = [{"box_type_code": "YT-RB-01001-A", "source_type": "demo", "size": "80-400"}]
        promoted = cpq_kb.promote_rows(rows, target="workbook", authority=AUTHORITY)
        self.assertEqual(promoted[0]["source_type"], "workbook", "demo 应被升格为 workbook")
        self.assertEqual(promoted[0]["size"], "80-400", "升格只改来源列，业务值逐字不变")
        self.assertIn("authority_ref", promoted[0], "升格必须留下出处 authority_ref")
        with self.assertRaises(ValueError):
            cpq_kb.promote_rows([{"source_type": "unknown"}], target="workbook",
                                authority=AUTHORITY)
        with self.assertRaises((ValueError, KeyError)):
            cpq_kb.promote_rows([{"source_type": "demo"}], target="workbook", authority={})

    def test_a3_promotion_is_idempotent(self):
        import cpq_kb
        once = cpq_kb.promote_rows([{"source_type": "demo"}], target="workbook",
                                   authority=AUTHORITY)
        twice = cpq_kb.promote_rows(once, target="workbook", authority=AUTHORITY)
        self.assertEqual(twice[0]["source_type"], "workbook", "二次升格必须幂等")


class BProvenanceDeclaration(unittest.TestCase):
    """B 组：出处必须落盘可机检（现状 `SOURCE_COST` 指向的 Sheet 名在工作簿里并不存在）。"""

    def test_b1_provenance_json_exists(self):
        self.assertTrue(PROVENANCE_JSON.exists(),
                        "缺少 %s：出处只写在 Python 常量里，无法机检、无法核对"
                        % PROVENANCE_JSON.relative_to(ROOT))

    def test_b2_every_source_constant_is_declared(self):
        if not PROVENANCE_JSON.exists():
            self.skipTest("出处文件不存在（见 B1）")
        payload = json.loads(PROVENANCE_JSON.read_text(encoding="utf-8"))
        declared = {item["source"] for item in payload.get("sources") or []}
        constants = {getattr(seed, name) for name in dir(seed)
                     if name.startswith("SOURCE_") and isinstance(getattr(seed, name), str)}
        missing = sorted(constants - declared)
        self.assertEqual(missing, [], "这些 SOURCE_* 常量没有出处登记：%s" % missing)

    def test_b3_declared_rows_match_seed_counts(self):
        if not PROVENANCE_JSON.exists():
            self.skipTest("出处文件不存在（见 B1）")
        payload = json.loads(PROVENANCE_JSON.read_text(encoding="utf-8"))
        by_source = {item["source"]: item for item in payload.get("sources") or []}
        for source, expected in ((seed.SOURCE_BOX_TYPE, len(seed.BOX_TYPES)),
                                 (seed.SOURCE_PART_TEMPLATE, len(seed.PART_TEMPLATES)),
                                 (seed.SOURCE_PROCESS_TEMPLATE, len(seed.PROCESS_TEMPLATES)),
                                 (seed.SOURCE_ACCESSORY, len(seed.ACCESSORIES))):
            item = by_source.get(source)
            self.assertIsNotNone(item, "出处表缺少 %s" % source)
            self.assertEqual(item.get("rows"), expected,
                             "%s 声明的条数与 seed 实际（%d）不一致" % (source, expected))


class CPreflightAndImporter(unittest.TestCase):
    """C 组：预检要"必须申报权威"，灌库要能一步升格且默认 dry-run。"""

    def test_c1_authority_missing_rule_exists(self):
        src = PREFLIGHT_PY.read_text(encoding="utf-8")
        self.assertIn("authority_missing", src,
                      "预检缺少 authority_missing 判定：demo 行没有出处就必须 no-go")

    def test_c2_importer_can_promote(self):
        src = IMPORT_PY.read_text(encoding="utf-8")
        self.assertTrue("--promote" in src, "灌库工具必须支持 --promote")
        self.assertTrue("--authority-file" in src,
                        "权威出处必须由文件提供（不是命令行手打）")

    def test_c3_importer_defaults_to_dry_run(self):
        src = IMPORT_PY.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"dry_run\s*=\s*not\s+args\.confirm", src) is not None,
                        "灌库工具必须默认 dry-run，只有 --confirm 才写库")


class DMatchPipelineGuards(unittest.TestCase):
    """D 组（绿护栏）：通路本身是通的 —— 数据一旦进快照，匹配就出候选。"""

    def test_d1_empty_snapshot_yields_no_candidate(self):
        saved_tables = kb_repo._CACHE.get("tables")
        saved_version = kb_repo._CACHE.get("version")
        try:
            _, tables = seed_into_temp_sqlite()
            kb_repo._CACHE["version"] = 1
            kb_repo._CACHE["tables"] = {"kb_packaging_match_weight":
                                        tables["kb_packaging_match_weight"]}
            result = match.match_box_types(dict(MATCH_INPUTS))
            self.assertEqual(len(result["candidates"]), 0,
                             "快照里没有盒型行时必须 0 候选（不得编造）")
            self.assertEqual(result["new_tooling_reason"], "no_box_type")
        finally:
            kb_repo._CACHE["version"] = saved_version
            kb_repo._CACHE["tables"] = saved_tables

    def test_d2_seeded_snapshot_yields_candidates(self):
        saved_tables = kb_repo._CACHE.get("tables")
        saved_version = kb_repo._CACHE.get("version")
        try:
            _, tables = seed_into_temp_sqlite()
            self.assertEqual(len(tables["kb_packaging_box_type"]), len(seed.BOX_TYPES),
                             "seed 必须落进临时库（否则这条护栏没有意义）")
            kb_repo._CACHE["version"] = 1
            kb_repo._CACHE["tables"] = tables
            result = match.match_box_types(dict(MATCH_INPUTS))
            matched = [c for c in result["candidates"] if c["status"] == "matched"]
            self.assertTrue(matched,
                            "灌了 %d 条盒型后必须至少 1 条 matched —— 现在一条都匹配不出来"
                            % len(seed.BOX_TYPES))
            self.assertTrue(result["suggested_box_type"],
                            "有 matched 候选时必须给出建议盒型")
            self.assertFalse(result["needs_new_tooling"],
                             "有 matched 候选时不得再要求新开刀模")
        finally:
            kb_repo._CACHE["version"] = saved_version
            kb_repo._CACHE["tables"] = saved_tables

    def test_d3_kb_unavailable_is_not_an_empty_kb(self):
        from tech_app.backend.services import cpq_kb_client
        saved_tables = kb_repo._CACHE.get("tables")
        saved_version = kb_repo._CACHE.get("version")
        try:
            kb_repo._CACHE["version"] = None
            kb_repo._CACHE["tables"] = None
            with mock.patch.dict("os.environ", {"CPQ_INTERNAL_TOKEN": ""}, clear=False):
                with self.assertRaises(cpq_kb_client.KbUnavailable):
                    match.match_box_types(dict(MATCH_INPUTS))
        finally:
            kb_repo._CACHE["version"] = saved_version
            kb_repo._CACHE["tables"] = saved_tables


class EDataLayeringGuards(unittest.TestCase):
    """E 组（绿护栏）：数据分层与行业隔离不得被顺手改掉。"""

    def test_e1_source_types_unchanged(self):
        import cpq_kb
        for value in ("demo", "workbook", "dwg_confirmed", "unknown"):
            self.assertIn(value, cpq_kb.SOURCE_TYPES, "SOURCE_TYPES 不得缩水")

    def test_e2_packaging_tables_unchanged(self):
        import cpq_kb
        self.assertEqual(len(cpq_kb.PACKAGING_TABLES), 9,
                         "包装扩展表口径是 9 张（唯一清单在 cpq_kb）")
        self.assertEqual(len(cpq_kb.PACKAGING_REQUIRED_TABLES), 7,
                         "关键主体表是 7 张（预检 empty_required_table 用它）")

    def test_e3_seed_rows_stay_demo_by_default(self):
        rows = seed._packaging_row({"x": 1}, "src")
        self.assertEqual(rows["source_type"], "demo",
                         "seed 缺省仍是 demo：升格必须显式发生，不得静默变权威")


if __name__ == "__main__":
    unittest.main(verbosity=2)
