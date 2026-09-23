"""红测：报价卡片第 6 步的零件反查必须认得「认回（recover）过」的技术项目（Spec §2/§4）。

Spec：`docs/specs/packaging-card-parts-lookup-must-accept-recovered-link.md`

现状缺口（2026-09-23 在 34 实测 + 本机读源码，不是推断）：
  · 卡片页 `确认需求解析结果.html` 的反查只认 `business_case.source_session_id`；
  · 而 `/api/projects/{id}/quote-link/recover` 只把调用方给的线索原样写进 business_case，
    34 上实测那条记录是 `quote_session_id="1bef04f7dab3"` + `source_session_id=""`；
  · 于是认回过的项目卡片反查永远落空 → 兜底读件不触发 → 第 6 步看不到拆出来的零件；
  · 回传通道 `packaging_handoff` 读的时候把 `quote_session_id` 映射成 `source_session_id`
    —— 两个键说的是同一件事，读侧却只认一个。

写侧语义（只写技术侧事实、不建卡片）与零件端点口径都不许动。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CARD_HTML = ROOT / "确认需求解析结果.html"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
HANDOFF_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_handoff.py"

SESSION = "1bef04f7dab3"
PROJECT = "8131f6d29d99"

# 34 上那份真实形状的 business_case（`quote_session_id` 有值、`source_session_id` 空）。
RECOVERED_ENTRY = {
    "project_id": PROJECT,
    "business_case": {
        "entry_origin": "quote", "internal_test": False, "clues": ["source_task_id"],
        "business_case_id": "", "source_task_id": "", "source_session_id": "",
        "quote_session_id": SESSION, "create_new": False,
        "recovered_from_project_id": PROJECT, "recovered_by": "PE1",
    },
}
TASK_ENTRY = {"project_id": PROJECT,
              "business_case": {"source_session_id": SESSION, "quote_session_id": ""}}
BOTH_ENTRY = {"project_id": PROJECT,
              "business_case": {"source_session_id": SESSION, "quote_session_id": SESSION}}
NONE_ENTRY = {"project_id": PROJECT, "business_case": {}}

RECOVER_CLUE_KEYS = {"business_case_id", "quote_session_id", "source_task_id",
                     "source_session_id"}


def _matcher_body() -> str:
    html = CARD_HTML.read_text(encoding="utf-8")
    match = re.search(r"async function resolveCardTechProject\(\)\s*\{(?P<body>.*?)\n    \}",
                      html, re.S)
    assert match, "卡片页必须仍有 resolveCardTechProject()（Spec §2.4）"
    return match.group("body")


def _link_keys() -> list:
    """反查函数实际读的会话键（按出现顺序；只取含 session 的那种）。"""
    body = _matcher_body()
    seen = []
    for key in re.findall(r"\b(?:bc|p|payload)\.([A-Za-z_]+)", body):
        if "session" in key and key not in seen:
            seen.append(key)
    return seen


def _match(entry: dict, sid: str = SESSION) -> str:
    """照实模拟那条判据：business_case 上的键优先、再退回项目级同名键。"""
    business_case = entry.get("business_case") or {}
    for key in _link_keys():
        value = business_case.get(key) or entry.get(key) or ""
        if str(value) == str(sid):
            return str(entry.get("project_id") or "")
    return ""


class CardPartsLookupLinkKeyTest(unittest.TestCase):
    # ---------------- A 组：反查必须认回的项目（今天红） ----------------

    def test_a1_lookup_reads_quote_session_id_too(self):
        keys = _link_keys()
        self.assertIn("quote_session_id", keys,
                      "反查的会话键集合里必须有 quote_session_id —— 认回（recover）写的就是它；"
                      "今天读到的键只有：%r" % (keys,))

    def test_a2_recovered_project_is_found(self):
        self.assertEqual(_match(RECOVERED_ENTRY), PROJECT,
                         "34 上认回过的项目（source_session_id 空 + quote_session_id 有值）"
                         "必须能被卡片反查到，否则第 6 步永远读不到零件。")

    def test_a3_both_link_keys_are_equivalent(self):
        by_source = _match(TASK_ENTRY)
        by_quote = _match(RECOVERED_ENTRY)
        self.assertTrue(by_source, "source_session_id 这条路本来就必须命中（护栏）")
        self.assertEqual(by_quote, by_source,
                         "同一件事的两个键必须命中同一个项目：source → %r，quote → %r"
                         % (by_source, by_quote))

    # ---------------- B 组：护栏（今天就是绿的，不许被改红） ----------------

    def test_b1_source_session_id_still_accepted(self):
        self.assertIn("source_session_id", _link_keys(),
                      "任务建项那条路（写 source_session_id）不许坏")
        self.assertEqual(_match(TASK_ENTRY), PROJECT)
        self.assertEqual(_match(BOTH_ENTRY), PROJECT)
        self.assertEqual(_match(NONE_ENTRY), "",
                         "没有会话线索时照旧安静跳过，不报错")

    def test_b2_recover_route_clue_keys_unchanged(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        match = re.search(r"def recover_quote_link\((?P<body>.*?)(?=\n@app\.|\n@_route\(|\Z)",
                          source, re.S)
        self.assertTrue(match, "POST /quote-link/recover 必须仍在 main.py 里")
        body = match.group("body")
        keys = set(re.findall(r'"([a-z_]+)":\s*str\(body\.', body))
        self.assertEqual(keys, RECOVER_CLUE_KEYS,
                         "恢复动作的线索键集合是冻结面（Spec §2.3）：%r" % (sorted(keys),))
        for banned in ("sync_card", "create_card", "cpq_wf"):
            self.assertNotIn(banned, body,
                             "恢复动作只写技术侧事实，不许顺手建/改报价卡片")

    def test_b3_handoff_still_maps_quote_session_to_source_session(self):
        source = HANDOFF_PY.read_text(encoding="utf-8")
        self.assertIn('link.get("quote_session_id")', source,
                      "回传通道仍把 quote_session_id 当来源会话（两个键同义的既有事实）")

    def test_b4_parts_endpoint_and_columns_unchanged(self):
        html = CARD_HTML.read_text(encoding="utf-8")
        self.assertIn("/requirement/packaging-parts", html,
                      "命中之后读的仍是零件端点")
        self.assertIn("part_columns", html,
                      "列定义仍只取后端 CARD_COLUMNS，不许前端另写一份")


if __name__ == "__main__":
    unittest.main()
