"""红测：包装成本既有红测收口（把"7 条红"拆成"环境类"与"等裁决"两类）。

Spec：`docs/specs/packaging-cost-red-closure.md`

本机实测（`python3 -m unittest` 跑 5 个 `packaging_cost_*_red.py`）：

    Ran 226 tests  FAILED (failures=6, errors=8)     # 14 条红，不是上一份报告写的 7 条

两类性质完全不同：

  · **8 条 ERROR = 本机没装 openpyxl**（`test_packaging_cost_minimum_charge_red.py:265`
    直接 `import openpyxl`，`test_packaging_cost_rule_snapshot_red.py` 的工作簿类同理），
    而 `tech_app/requirements.txt:14` 已声明 `openpyxl==3.1.5` —— 纯环境问题，
    却和业务红混在同一个数字里；
  · **6 条 FAIL = 最低收费口径未裁决**：`minimum_charge_policy()` 实测
    `{"status": "pending", "policy": "unresolved", "fallback": "sheet_labor_rate"}`；
    `PKG-C-V-GROOVE` 的 `minimum_charge` 实测 150，第 1 批冻结值 120；
    `lamination` 低表达式档实测 `amount=0.233916788093`（最低收费 200/1000=0.2），
    即表达式本身高于最低收费 —— 这 3 条是**口径分歧**，不是"少收钱"。

纪律：全部离线；不连 Postgres、不调模型、不写业务数据、不改任何既有红测。
"""
from __future__ import annotations

import importlib
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

cost = importlib.import_module("tech_app.backend.services.packaging_cost")

RULES_JSON = ROOT / "tech_app" / "agent_knowledge" / "rules" / "packaging_cost_rules.json"
REQUIREMENTS = ROOT / "tech_app" / "requirements.txt"
COST_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_cost.py"
WORKBOOK_TESTS = (ROOT / "tests" / "test_packaging_cost_minimum_charge_red.py",
                  ROOT / "tests" / "test_packaging_cost_rule_snapshot_red.py")

V_GROOVE_FROZEN = 120


class AEnvironmentVsDecision(unittest.TestCase):
    """A 组：环境类必须表现为 skip；业务类必须有裁决落档。"""

    def test_a1_workbook_tests_skip_without_openpyxl(self):
        for path in WORKBOOK_TESTS:
            src = path.read_text(encoding="utf-8")
            if "openpyxl" not in src:
                continue
            guarded = ("skipUnless" in src or "SkipTest" in src
                       or "except ImportError" in src or "importlib.util.find_spec" in src)
            self.assertTrue(guarded,
                            "%s 缺 openpyxl 时必须 skip，不能报 ERROR"
                            "（否则『N 条红』里混进环境问题，谁也看不出风险）"
                            % path.relative_to(ROOT))

    def test_a2_backend_requirements_declares_openpyxl(self):
        # 根 requirements.txt:14 有 openpyxl==3.1.5，但 tech_app/requirements.txt（后端依赖清单）
        # 没有 —— 按后端清单装环境就会缺依赖，这正是 8 条 ERROR 的来源。
        text = REQUIREMENTS.read_text(encoding="utf-8")
        self.assertRegex(text, r"openpyxl",
                         "tech_app/requirements.txt 必须声明 openpyxl："
                         "根清单有、后端清单没有，照后端清单装环境必然缺依赖")

    def test_a2b_root_requirements_still_declares_openpyxl(self):
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(text, r"openpyxl", "根 requirements.txt 的声明不得被删掉")

    def test_a3_minimum_charge_policy_is_decided(self):
        policy = cost.minimum_charge_policy()
        self.assertEqual(policy["status"], "chosen",
                         "最低收费口径必须由业务裁决（现在是 pending：数字无出处）")

    def test_a4_policy_is_not_unresolved(self):
        policy = cost.minimum_charge_policy()
        self.assertNotEqual(policy["policy"], "unresolved",
                            "未裁决期间运行时只能标 unresolved + 回退 sheet_labor_rate，"
                            "不许装作已裁决")
        self.assertTrue(policy["decided_by"], "裁决必须留人")
        self.assertTrue(policy["decided_at"], "裁决必须留日期")

    def test_a5_frozen_value_conflicts_are_registered(self):
        self.assertTrue(hasattr(cost, "_MIN_CHARGE_DECISIONS"),
                        "缺少冲突登记表：PKG-C-V-GROOVE 冻结值 %d 与运行时 %s 的差异无处可查"
                        % (V_GROOVE_FROZEN,
                           cost.FORMULA_CATALOG.get("PKG-C-V-GROOVE", {}).get("minimum_charge")))
        registry = cost._MIN_CHARGE_DECISIONS
        self.assertIsInstance(registry, dict)
        codes = set(registry)
        for code, entry in cost.FORMULA_CATALOG.items():
            current = entry.get("minimum_charge")
            frozen = entry.get("frozen_minimum_charge")
            if frozen is None or current is None or float(current) == float(frozen):
                continue
            self.assertIn(code, codes,
                          "%s 的最低收费与冻结值不一致，必须登记（owner/日期/原因）" % code)
        for code, entry in registry.items():
            for field in ("frozen", "current", "owner", "decided_at", "reason"):
                self.assertIn(field, entry, "%s 的登记缺少 %s" % (code, field))

    def test_a6_v_groove_conflict_is_visible(self):
        entry = cost.FORMULA_CATALOG.get("PKG-C-V-GROOVE")
        self.assertIsNotNone(entry, "PKG-C-V-GROOVE 必须在公式目录里")
        current = float(entry.get("minimum_charge") or 0)
        if current != V_GROOVE_FROZEN:
            registry = getattr(cost, "_MIN_CHARGE_DECISIONS", {})
            self.assertIn("PKG-C-V-GROOVE", registry,
                          "V 槽最低收费 冻结 %s / 现 %s 的差异必须在登记表里说清"
                          % (V_GROOVE_FROZEN, current))


class BDecisionFileShape(unittest.TestCase):
    """B 组：裁决必须落在规则快照里，且与运行时同源。"""

    def test_b1_rules_json_has_chosen_policy_block(self):
        payload = json.loads(RULES_JSON.read_text(encoding="utf-8"))
        block = payload.get("minimum_charge_policy") or {}
        self.assertEqual(str(block.get("status") or ""), "chosen",
                         "规则快照的 minimum_charge_policy.status 必须为 chosen")
        self.assertTrue(str(block.get("decided_by") or ""), "必须记录裁决人")

    def test_b2_policy_block_matches_runtime(self):
        payload = json.loads(RULES_JSON.read_text(encoding="utf-8"))
        block = payload.get("minimum_charge_policy") or {}
        decisions = block.get("decisions") or []
        self.assertTrue(decisions, "裁决必须逐条列出（formula_code / minimum_charge / reason）")
        for item in decisions:
            code = str(item.get("formula_code") or "")
            entry = cost.FORMULA_CATALOG.get(code)
            if entry is None:
                continue
            self.assertEqual(float(entry.get("minimum_charge") or 0),
                             float(item.get("minimum_charge") or 0),
                             "%s 的裁决值与运行时逐字不一致" % code)


class CRegressionGuards(unittest.TestCase):
    """C 组（绿护栏）：本批只收口，不得顺手改公式、口径标注与依赖边界。"""

    def test_c1_line_carries_policy_marker(self):
        line = cost.compute_line("lamination", {"loss_rate": 0.0}, amount=5.0)
        self.assertIn("policy", line, "每行成本必须带 policy 标注（未裁决不许静默）")
        self.assertIn("formula_source", line, "每行成本必须带来源，供追溯")

    def test_c2_engine_never_imports_openpyxl(self):
        text = COST_PY.read_text(encoding="utf-8")
        self.assertFalse(re.search(r"^\s*(import|from)\s+openpyxl", text, re.M),
                         "生产成本引擎不得依赖 openpyxl")

    def test_c3_formula_catalog_not_shrunk(self):
        self.assertGreaterEqual(len(cost.FORMULA_CATALOG), 20,
                                "公式目录不得因收口而缩水（快照声明 20 条）")

    def test_c4_policy_is_read_live(self):
        saved = cost.MINIMUM_CHARGE_POLICY
        try:
            cost.MINIMUM_CHARGE_POLICY = {"status": "chosen", "chosen": "sheet_labor_rate"}
            live = cost.minimum_charge_policy()
            self.assertEqual(live["status"], "chosen")
            self.assertEqual(live["policy"], "sheet_labor_rate")
            self.assertEqual(live["fallback"], "")
        finally:
            cost.MINIMUM_CHARGE_POLICY = saved

    def test_c5_red_tests_are_not_loosened(self):
        src = (ROOT / "tests" / "test_packaging_cost_engine_red.py").read_text(encoding="utf-8")
        self.assertIn("must call", src.replace("必须命中最低收费", "must call"),
                      "引擎红测的断言文本必须保持原样（禁止靠放宽断言转绿）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
