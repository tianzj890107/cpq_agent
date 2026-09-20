"""红测：第 22 步的人工验收清单必须与当前产品一致。

现状缺口（实测，`docs/specs/tech-agent-recovery-22-e2e-scenarios.json`）：
  · e2e-03 要求「左侧操作栏的上一步 / 下一步 / 主要操作可用且随 stage 变化」——
    上下步早已移到右侧底栏（`tech-workbench.html` 的 `tech-workbench-bottom`，
    `#techPrev` / `#techNext`）。
  · e2e-04 要求「点「零件清单」→ 右侧清单」—— 零件清单现在右侧看板常驻，
    不再是从左侧按钮打开的临时视图。
  · e2e-09 要求「左侧上下文卡显示各自的 page_context」—— 可见上下文卡已删除；
    page_context 仍然存在，但它是**发给 Agent 的请求上下文**（如
    `assembly-integration.js` 的 `page_context: '2.2 组装与整合'`），不是页面上的一行卡片。
  · e2e-10 要求「用左侧『失败重试』…重发」—— 常驻「失败重试」入口已下线，
    失败以普通会话输出 + 就近重试呈现。

这些不会直接造成线上故障，但会让「按第 22 步验收」得到错误结论。本批要求：清单里的
automated 引用必须真实存在，manual/expected 只能描述当前产品的交互（右侧底栏上下步、
右侧常驻零件清单、会话输出的错误与就近重试），并且不得靠删场景来变绿。
"""
from __future__ import annotations

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "specs" / "tech-agent-recovery-22-e2e-scenarios.json"

# 已被后续批次取代的要求（出现在清单里就是过期）。
STALE_PHRASES = (
    "失败重试",
    "左侧上下文卡",
    "点「零件清单」",
    "点“零件清单”",
    "左侧操作栏的上一步",
    "左侧的上一步",
)
# 当前产品的真实交互，必须在清单里被如实描述。
CURRENT_PHRASES = (
    "右侧底栏",
    "常驻",
    "会话输出",
    "重试",
)
EXPECTED_IDS = [f"e2e-{index:02d}" for index in range(1, 11)]


class E2eAcceptanceDocCurrentRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = DOC_PATH.read_text(encoding="utf-8", errors="replace")
        cls.doc = json.loads(cls.raw)
        cls.scenarios = cls.doc.get("scenarios") or []

    def _scenario(self, scenario_id: str) -> dict:
        found = next((row for row in self.scenarios if row.get("id") == scenario_id), None)
        self.assertIsNotNone(found, f"清单里缺少场景 {scenario_id}")
        return found

    def _scenario_text(self, scenario: dict) -> str:
        parts = [scenario.get("title", ""), scenario.get("expected", "")]
        parts.extend(scenario.get("manual") or [])
        return "\n".join(str(item) for item in parts)

    # ------------------------------------------- 结构完整性（防「删场景变绿」）
    def test_document_keeps_all_ten_scenarios(self):
        self.assertEqual(self.doc.get("step"), 22, "这是第 22 步的验收清单")
        self.assertEqual([row.get("id") for row in self.scenarios], EXPECTED_IDS,
                         "十条端到端场景必须全部保留，顺序不变")

    def test_every_scenario_stays_actionable(self):
        for row in self.scenarios:
            with self.subTest(scenario=row.get("id")):
                self.assertTrue(str(row.get("title") or "").strip(), "标题不能为空")
                self.assertTrue(row.get("manual"), "manual 步骤不能为空")
                self.assertTrue(str(row.get("expected") or "").strip(), "expected 不能为空")
                self.assertTrue(row.get("depends_on"), "depends_on 不能为空")

    def test_automated_references_exist(self):
        missing = []
        for row in self.scenarios:
            for path in row.get("automated") or []:
                if not (ROOT / path).exists():
                    missing.append(f"{row.get('id')} → {path}")
        self.assertEqual(missing, [], f"清单引用了不存在的自动化测试：{missing}")

    def test_note_explains_automated_and_manual(self):
        note = str(self.doc.get("note") or "")
        for token in ("automated", "manual"):
            with self.subTest(token=token):
                self.assertIn(token, note, "清单头部要说明 automated / manual 的含义")

    # ------------------------------------------- 过期要求（红）
    def test_no_scenario_requires_removed_left_toolbar_steps(self):
        for row in self.scenarios:
            with self.subTest(scenario=row.get("id")):
                text = self._scenario_text(row)
                self.assertNotIn("左侧操作栏的上一步", text,
                                 "上下步已在右侧底栏，不能再要求左侧操作栏的上下步")
                self.assertNotIn("左侧的上一步", text, "上下步已在右侧底栏")

    def test_no_scenario_requires_persistent_retry_entry(self):
        for row in self.scenarios:
            with self.subTest(scenario=row.get("id")):
                self.assertNotIn("失败重试", self._scenario_text(row),
                                 "常驻「失败重试」入口已下线：失败走普通会话输出 + 就近重试")

    def test_no_scenario_requires_visible_context_card(self):
        for row in self.scenarios:
            with self.subTest(scenario=row.get("id")):
                self.assertNotIn("上下文卡", self._scenario_text(row),
                                 "可见上下文卡已删除；page_context 是发给 Agent 的请求上下文")

    def test_no_scenario_opens_parts_list_from_left_button(self):
        for row in self.scenarios:
            with self.subTest(scenario=row.get("id")):
                text = self._scenario_text(row)
                self.assertNotIn("点「零件清单」", text,
                                 "零件清单已在右侧常驻，不再是从左侧按钮打开的视图")
                self.assertNotIn("点“零件清单”", text, "零件清单已在右侧常驻")

    def test_document_has_no_removed_ui_phrases_anywhere(self):
        for phrase in STALE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.raw,
                                 f"验收清单整体不得再出现已下线交互：{phrase}")

    # ------------------------------------------- 当前交互（红）
    def test_document_describes_current_interactions(self):
        for phrase in CURRENT_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.raw,
                              f"验收清单必须如实描述当前交互：{phrase}")

    def test_step_navigation_is_described_as_bottom_bar(self):
        text = self._scenario_text(self._scenario("e2e-03"))
        self.assertIn("右侧底栏", text,
                      "上一步 / 下一步在右侧底栏，验收步骤要按这个位置描述")
        self.assertIn("下一步", text, "上下步的能力本身仍要验收")

    def test_parts_list_is_described_as_right_side_resident(self):
        text = self._scenario_text(self._scenario("e2e-04"))
        self.assertIn("常驻", text,
                      "零件清单是右侧看板常驻区块，验收步骤要按这个形态描述")
        self.assertIn("零件", text)

    def test_nine_stage_context_is_agent_request_context(self):
        text = self._scenario_text(self._scenario("e2e-09"))
        self.assertIn("page_context", text, "九阶段 page_context 仍然要验收")
        self.assertTrue("Agent" in text or "请求" in text,
                        "要说明 page_context 是发给 Agent 的请求上下文，而不是页面上的卡片")
        self.assertNotIn("显示各自的 page_context", text,
                         "不能再要求页面「显示」page_context")

    def test_failure_scenario_uses_chat_output_and_local_retry(self):
        text = self._scenario_text(self._scenario("e2e-10"))
        self.assertIn("会话", text, "失败要以普通会话输出呈现")
        self.assertIn("重试", text, "仍然要能重发同一条命令")
        self.assertNotIn("左侧「失败重试」", text, "左侧常驻失败重试入口已下线")


if __name__ == "__main__":
    unittest.main()
