# -*- coding: utf-8 -*-
"""Schema parity：隔离 PostgreSQL 上的关键表 / 列 / 索引 / 约束必须来自**生产 DDL 入口**。

核心命题（用户第六节）：临时库的表是 `cpq_auth.init()` + `cpq_wf.init()` 建出来的，
不是测试另抄一套简化 DDL 自证正确。因此这里同时做两件事：

1. 读真库 catalog（`schema.catalog_parity` 场景），断言关键对象；
2. 把每个 catalog 对象与 `cpq_wf._ddl_pg(schema)` —— 生产建表语句本身 —— 对账。

没有隔离 PostgreSQL 环境时整类 skip（不算通过）；报告里会单列 integration skipped。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cpq_eval import pg_integration  # noqa: E402

TABLES = ("cpq_wf_task", "cpq_wf_task_event", "cpq_wf_handoff")
# 表 -> 必须存在的列（新老兼容列都在内）
REQUIRED_COLUMNS = {
    "cpq_wf_task": ("task_id", "card_id", "status", "task_kind", "payload",
                    "supersedes_task_id", "replaced_by_task_id", "cancel_reason",
                    "cancelled_at", "claimed_by_user_id"),
    "cpq_wf_task_event": ("event_id", "task_id", "card_id", "action", "actor_user_id"),
    "cpq_wf_handoff": ("handoff_id", "handoff_key", "handoff_kind", "source_project_id",
                       "source_task_id", "target_card_id", "step_no", "snapshot_sections",
                       "source_task_closed", "business_case_id"),
}


def _production_ddl(schema: str = "cpq_wf") -> str:
    import cpq_wf

    return "\n".join(str(sql) for sql in cpq_wf._ddl_pg(schema))


class PgSchemaParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ok, reason = pg_integration.available()
        if not ok:
            raise unittest.SkipTest(f"隔离 PostgreSQL 不可用，schema parity 跳过：{reason}")
        payload, err = pg_integration.probe("schema.catalog_parity", {})
        if err is not None or not payload or not payload.get("ok"):
            raise unittest.SkipTest(f"schema parity 场景不可执行：{err or payload.get('error')}")
        cls.catalog = payload["result"]
        cls.ddl = _production_ddl(cls.catalog["schema"])

    def test_tables_created_by_production_init(self):
        self.assertEqual(self.catalog["schema"], "cpq_wf")
        for table in TABLES:
            self.assertIsNotNone(self.catalog["tables"].get(table),
                                 f"真库里没有表 {table}（生产 init 未建出）")
            self.assertIn(f"CREATE TABLE IF NOT EXISTS cpq_wf.{table}", self.ddl,
                          f"{table} 不在生产 DDL 里 —— catalog 可能来自测试自造 DDL")

    def test_required_columns_present(self):
        for table, columns in REQUIRED_COLUMNS.items():
            got = self.catalog["tables"][table] or {}
            for column in columns:
                self.assertIn(column, got, f"{table} 缺列 {column}")
                self.assertIn(column, self.ddl, f"{table}.{column} 不在生产 DDL 里")

    def test_partial_unique_index_on_open_task(self):
        entry = self.catalog["indexes"].get("uq_wf_task_open_kind")
        self.assertIsNotNone(entry, "缺少 uq_wf_task_open_kind 部分唯一索引")
        self.assertTrue(entry["unique"], "uq_wf_task_open_kind 必须是 UNIQUE")
        definition = entry["indexdef"].lower()
        self.assertIn("card_id", definition)
        self.assertIn("task_kind", definition)
        self.assertIn("where", definition)
        self.assertIn("'open'", definition)
        self.assertIn("uq_wf_task_open_kind", self.ddl)
        self.assertIn("WHERE status = 'open'", self.ddl)

    def test_unique_index_on_handoff_key(self):
        entry = self.catalog["indexes"].get("uq_wf_handoff_key")
        self.assertIsNotNone(entry, "缺少 uq_wf_handoff_key 唯一索引")
        self.assertTrue(entry["unique"], "uq_wf_handoff_key 必须是 UNIQUE")
        self.assertIn("handoff_key", entry["indexdef"].lower())
        self.assertIn("uq_wf_handoff_key", self.ddl)

    def test_foreign_keys_match_production_ddl(self):
        definitions = " | ".join(str(item.get("def") or "")
                                 for item in self.catalog["constraints"])
        self.assertIn("REFERENCES cpq_wf_card(card_id)", definitions)
        self.assertIn("FOREIGN KEY (supersedes_task_id) REFERENCES cpq_wf_task(task_id)", definitions)
        self.assertIn("FOREIGN KEY (replaced_by_task_id) REFERENCES cpq_wf_task(task_id)", definitions)
        self.assertIn("REFERENCES cpq_wf.cpq_wf_task(task_id)", self.ddl)

    def test_dropping_key_index_is_visible_to_parity_report(self):
        """删除关键索引后 catalog 必须看得见 —— parity 守护真的敏感。"""
        for mutation, index in (("drop_handoff_index", "uq_wf_handoff_key"),
                                ("drop_task_open_index", "uq_wf_task_open_kind")):
            payload, err = pg_integration.probe("schema.catalog_parity", {}, mutate=mutation)
            self.assertIsNone(err, err)
            self.assertTrue(payload.get("ok"), payload.get("error"))
            self.assertIsNone(payload["result"]["indexes"].get(index),
                              f"注入 {mutation} 后 {index} 仍然存在，parity 守护无效")


if __name__ == "__main__":
    unittest.main()
