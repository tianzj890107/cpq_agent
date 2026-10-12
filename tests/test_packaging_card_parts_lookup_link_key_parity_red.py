# -*- coding: utf-8 -*-
"""红测：报价卡片不再承担「反查技术项目读零件」（销售视角与三行业对齐）。

Spec：`docs/specs/packaging-quote-card-sales-view-parity.md`（C2）

血缘与取代：本文件原契约是 `packaging-card-parts-lookup-must-accept-recovered-link.md`
（「卡片第 6 步的零件反查必须认得认回（recover）过的技术项目」）—— 该契约已由新 Spec 的 C2
**整份取代**：卡片第 6 步不再有零件表，反查随之撤掉。`source_session_id` / `quote_session_id`
两键同义这件事在**写侧**（`recover_quote_link` 的线索键、`packaging_handoff` 的映射）继续成立，
只是卡片页不再有读方。

现状缺口（本机读码，2026-10-12，不是推断）：
  · 卡片页仍按会话反查技术项目：`resolveCardTechProject()` 读 `/api/projects?scope=all`
    （全页 4 处 `/api/projects` 都在这一段），命中后读 `.../requirement/packaging-parts` 与
    `.../packaging-business-parts`；
  · 新口径下这一段（含 `part_columns` 列定义与 `packaging-parts` 端点）必须整段撤出卡片页。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CARD_HTML = ROOT / "确认需求解析结果.html"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
HANDOFF_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_handoff.py"

RECOVER_CLUE_KEYS = {"business_case_id", "quote_session_id", "source_task_id",
                     "source_session_id"}


def html_text() -> str:
    return CARD_HTML.read_text(encoding="utf-8", errors="replace")


class CardPartsLookupRemovedTest(unittest.TestCase):
    # ---------------- A 组：卡片页不再反查技术项目读零件（今天红） ----------------

    def test_a1_card_page_has_no_project_lookup(self):
        text = html_text()
        self.assertNotIn("resolveCardTechProject", text,
                         "卡片页不许再有反查技术项目的函数（Spec C2 第 2 条）")
        count = text.count("/api/projects")
        self.assertEqual(0, count,
                         "卡片页不许再读 `/api/projects`（现在命中 %d 次，全部属于零件反查）" % count)

    def test_a2_card_page_has_no_parts_endpoints(self):
        text = html_text()
        for token in ("packaging-parts", "packaging-business-parts"):
            self.assertNotIn(token, text, "卡片页不许再读 %s（Spec C2 第 1 条）" % token)

    def test_a3_card_page_has_no_part_columns(self):
        count = html_text().count("part_columns")
        self.assertEqual(0, count,
                         "列定义不再上卡片（现在命中 %d 次）；列的唯一来源仍是后端 CARD_COLUMNS"
                         % count)

    # ---------------- B 组：写侧与技术侧护栏（今天就是绿的，不许被改红） ----------------

    def test_b1_recover_route_clue_keys_unchanged(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        match = re.search(r"def recover_quote_link\((?P<body>.*?)(?=\n@app\.|\n@_route\(|\Z)",
                          source, re.S)
        self.assertTrue(match, "POST /quote-link/recover 必须仍在 main.py 里")
        body = match.group("body")
        keys = set(re.findall(r'"([a-z_]+)":\s*str\(body\.', body))
        self.assertEqual(keys, RECOVER_CLUE_KEYS,
                         "恢复动作的线索键集合是冻结面（写侧两键同义）：%r" % (sorted(keys),))
        for banned in ("sync_card", "create_card", "cpq_wf"):
            self.assertNotIn(banned, body,
                             "恢复动作只写技术侧事实，不许顺手建/改报价卡片")

    def test_b2_handoff_still_maps_quote_session_to_source_session(self):
        source = HANDOFF_PY.read_text(encoding="utf-8")
        self.assertIn('link.get("quote_session_id")', source,
                      "回传通道仍把 quote_session_id 当来源会话（两个键同义的既有事实）")

    def test_b3_tech_board_still_reads_both_parts_surfaces(self):
        text = APP_JS.read_text(encoding="utf-8", errors="replace")
        for token in ("packaging-parts", "packaging-business-parts",
                      "business_part_code", "geometry_binding"):
            self.assertIn(token, text,
                          "技术侧 2.1 零件树不许被本批动掉：缺 %s（Spec C2 末条）" % token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
