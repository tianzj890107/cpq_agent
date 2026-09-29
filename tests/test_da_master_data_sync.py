"""DA 主数据增量迁移的离线口径检查；不连接或修改 Postgres。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import sync_da_master_data as sync  # noqa: E402


class DaMasterDataSyncTests(unittest.TestCase):
    def test_migration_whitelist_is_defined_by_da(self):
        sync.validate_da()

    def test_mock_dictionary_comes_from_current_industry_templates(self):
        industries, catalog, links = sync.seed_rows()
        self.assertEqual([row[0] for row in industries],
                         ["semiconductor", "battery", "appliance", "packaging"])
        self.assertEqual(len(catalog), 132)
        self.assertEqual(len(links), 132)
        self.assertIn(("packaging", "box_type", ""), links)
        self.assertIn(("packaging", "face_paper_gsm", ""), links)
        self.assertEqual(len({row[0] for row in catalog}), len(catalog))

    def test_plan_is_additive_and_idempotent(self):
        before = sync.plan({})
        self.assertEqual(before["new_tables"], list(sync.NEW_TABLES))
        self.assertEqual(before["new_columns"]["product_para_value"],
                         ["para_code", "para_name", "para_value"])
        after = sync.plan({**{t: {"id"} for t in sync.NEW_TABLES},
                           **{t: set(cols) for t, cols in sync.ADDITIONAL_COLUMNS.items()}})
        self.assertEqual(after["new_tables"], [])
        self.assertEqual(after["new_columns"], {})


if __name__ == "__main__":
    unittest.main()
