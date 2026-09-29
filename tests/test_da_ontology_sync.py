"""新版 DA 工作簿、运行时字段目录与前端取字段链路的回归检查。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cpq_db  # noqa: E402
import cpq_ontology  # noqa: E402
import cpq_agent_server as server  # noqa: E402


class DaOntologySyncTests(unittest.TestCase):
    def test_markdown_round_trips_current_workbook(self):
        workbook = cpq_db._load_ontology_xlsx()
        markdown = cpq_ontology.load_md()
        self.assertEqual(cpq_ontology._shape(markdown), cpq_ontology._shape(workbook))
        self.assertEqual(cpq_ontology.dump_md(workbook), cpq_ontology.MD_PATH.read_text(encoding="utf-8"))

    def test_spreadsheet_artifact_is_not_a_quote_table(self):
        quote = cpq_db._load_ontology()["quote"]["entities"]
        self.assertEqual(len(quote), 11)
        self.assertNotIn("·", {entity["table"] for entity in quote})

    def test_new_eav_definition_does_not_pollute_existing_wide_table(self):
        ontology = cpq_db._load_ontology()
        wide = next(e for e in ontology["config"]["entities"] if e["table"] == "product_para_value")
        proposal = next(e for e in ontology["pending"]["entities"] if e["table"] == "product_para_value")
        wide_codes = {a["code"] for a in wide["attrs"]}
        proposal_codes = {a["code"] for a in proposal["attrs"]}
        self.assertIn("machine_model", wide_codes)
        self.assertNotIn("para_value", wide_codes)
        self.assertTrue({"para_code", "para_name", "para_value"} <= proposal_codes)
        self.assertNotIn(", para_value)", cpq_db.schema_text("config"))
        self.assertEqual(next(a for a in proposal["attrs"] if a["code"] == "id")["pk"], "是")
        self.assertEqual(next(a for a in proposal["attrs"] if a["code"] == "para_code")["fk"],
                         "关联para_catalog.para_code")

    def test_frontend_catalog_keeps_packaging_and_existing_industry_columns_separate(self):
        bridge = server.Bridge.__new__(server.Bridge)
        bridge.conv = SimpleNamespace(model="test", profile=SimpleNamespace(name="test"))
        bridge.cwd = ""
        bridge.session_id = ""
        bridge.current_settings = lambda: {}
        packaging = {f["section_id"]: f for f in bridge.meta("packaging")["forms"]}
        battery = {f["section_id"]: f for f in bridge.meta("battery")["forms"]}
        for sid in ("s1_techparams", "s2_techparams"):
            box_labels = {c["label"] for c in packaging[sid]["techparams_columns"]}
            self.assertTrue({"盒型编码", "灰板厚度（mm）", "V槽"} <= box_labels)
            self.assertNotIn("电芯型号", box_labels)
            self.assertTrue(packaging[sid]["techparams_switched"])
            self.assertFalse(battery[sid]["techparams_switched"])
            self.assertNotIn("参数值", {c["label"] for c in battery[sid]["columns"]})
        html = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")
        self.assertIn("FORMS = meta.forms || []", html)
        self.assertIn("f.techparams_columns = got.techparams_columns", html)


if __name__ == "__main__":
    unittest.main()
