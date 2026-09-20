"""第 22 步红测：端到端场景清单与 runner。

覆盖：
1. 新增可机读场景清单 docs/specs/tech-agent-recovery-22-e2e-scenarios.json，
   正好覆盖 e2e-01 … e2e-10 十条场景；
2. 每条有 title / expected，automated 与 manual 至少一个非空；
3. automated 引用的测试文件真实存在；depends_on 落在 1–19；
4. 新增 scripts/tech_e2e_scenarios.py，--list 退出码 0 且列出全部十条；
5. e2e-10 明确要求左右两侧都显示真实错误且可重试。

不联网、不起服务、不读真实业务数据、不调用删除类接口。
"""
from pathlib import Path
import json
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "specs" / "tech-agent-recovery-22-e2e-scenarios.json"
RUNNER = ROOT / "scripts" / "tech_e2e_scenarios.py"
SCENARIOS = tuple(f"e2e-{n:02d}" for n in range(1, 11))


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


class TechE2eScenariosRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = {}
        if MANIFEST.exists():
            try:
                cls.data = json.loads(_read(MANIFEST))
            except json.JSONDecodeError:
                cls.data = {}
        cls.scenarios = {s.get("id"): s for s in (cls.data.get("scenarios") or [])
                         if isinstance(s, dict)}

    # ---------------------------------------------------------------- 清单覆盖
    def test_manifest_declares_ten_scenarios(self):
        self.assertTrue(MANIFEST.exists(),
                        f"缺少场景清单 {MANIFEST.relative_to(ROOT)}")
        self.assertEqual(self.data.get("step"), 22, "场景清单 step 必须是 22")
        self.assertEqual(set(self.scenarios), set(SCENARIOS),
                         f"十条场景必须齐全，实际：{sorted(self.scenarios)}")
        self.assertEqual(len(self.scenarios), 10, "十条场景不得重复")

    def test_each_scenario_has_title_and_expected(self):
        for scenario_id in SCENARIOS:
            scenario = self.scenarios.get(scenario_id)
            self.assertIsNotNone(scenario, f"缺少场景 {scenario_id}")
            self.assertTrue(str(scenario.get("title") or "").strip(),
                            f"{scenario_id} 缺少 title")
            self.assertTrue(str(scenario.get("expected") or "").strip(),
                            f"{scenario_id} 缺少 expected")

    def test_each_scenario_has_automated_or_manual(self):
        for scenario_id in SCENARIOS:
            scenario = self.scenarios.get(scenario_id, {})
            automated = scenario.get("automated") or []
            manual = scenario.get("manual") or []
            self.assertTrue(automated or manual,
                            f"{scenario_id} 必须给出 automated 或 manual 验收步骤")

    def test_automated_modules_exist(self):
        for scenario_id, scenario in self.scenarios.items():
            for rel in scenario.get("automated") or []:
                self.assertTrue((ROOT / rel).exists(),
                                f"{scenario_id} 引用的测试不存在：{rel}")

    def test_depends_on_is_within_known_steps(self):
        for scenario_id, scenario in self.scenarios.items():
            for step in scenario.get("depends_on") or []:
                self.assertIsInstance(step, int, f"{scenario_id} depends_on 必须是整数")
                self.assertTrue(1 <= step <= 19,
                                f"{scenario_id} depends_on 应在 1–19：{step}")

    # ---------------------------------------------------------------- 失败场景
    def test_failure_scenario_requires_real_error_and_retry(self):
        scenario = self.scenarios.get("e2e-10")
        self.assertIsNotNone(scenario, "缺少 e2e-10 失败场景")
        text = json.dumps(scenario, ensure_ascii=False)
        self.assertRegex(text, r"错误", "e2e-10 必须写明显示真实错误")
        self.assertRegex(text, r"重试", "e2e-10 必须写明可以重试")
        self.assertRegex(text, r"(左右|两侧|左侧[\s\S]{0,20}右侧)",
                         "e2e-10 必须要求左右两侧都可见")

    # ---------------------------------------------------------------- runner
    def test_runner_lists_all_scenarios(self):
        self.assertTrue(RUNNER.exists(),
                        f"缺少 runner {RUNNER.relative_to(ROOT)}")
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "--list"],
            cwd=str(ROOT), text=True, capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0,
                         f"runner --list 退出码非 0：{proc.stderr[-500:]}")
        for scenario_id in SCENARIOS:
            self.assertIn(scenario_id, proc.stdout,
                          f"runner --list 未列出 {scenario_id}")


if __name__ == "__main__":
    unittest.main()
