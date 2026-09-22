"""守卫：部署脚本第 4 步的健康等待必须是「可配窗口 + deadline 驱动」，且不许放宽判据。

Spec：`docs/specs/deploy-health-wait-window.md`
依赖：`scripts/deploy_34_bare.sh`（只读文本断言，不起服务、不连库、不发 HTTP）。

现状缺口（2026-09-22 10:02 实测）：窗口写死 `seq 1 40` × `sleep 2` = 80 秒，而 34 的冷启动
超过 80 秒 —— 服务其实已 `status=ok`（手工探测 `build.commit=7578b36…`），脚本却 `fail` 退出，
连带第 5 / 6b / 7 步全没跑：一次成功的部署被脚本自己的窗口判成失败。

纪律：不改本文件换绿；只读仓库内文件。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY_SH = ROOT / "scripts" / "deploy_34_bare.sh"


class HealthWaitGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DEPLOY_SH.read_text(encoding="utf-8")
        marker = cls.text.find('step "4. 健康检查与 PATH 核对"')
        assert marker >= 0, "第 4 步不见了"
        end = cls.text.find('step "5.', marker)
        cls.step4 = cls.text[marker:end if end > 0 else len(cls.text)]

    def test_c1_window_env_var(self):
        self.assertIn("CPQ_HEALTH_WINDOW_SECONDS", self.step4, "Spec C1：窗口必须可配")

    def test_c2_default_window_at_least_120(self):
        found = re.search(r"\$\{CPQ_HEALTH_WINDOW_SECONDS:-(\d+)\}", self.step4)
        self.assertIsNotNone(found, "Spec C1/C2：缺省窗口要写成 ${CPQ_HEALTH_WINDOW_SECONDS:-<秒>}")
        self.assertGreaterEqual(int(found.group(1)), 120, "Spec C2：缺省窗口必须 ≥ 120 秒")

    def test_c2_deadline_driven_not_fixed_iterations(self):
        self.assertNotIn("seq 1 40", self.step4, "Spec C2：不许再写死 40 次循环")
        self.assertRegex(self.step4, r"while\s+\[.*HEALTH_WINDOW_SECONDS", "Spec C2：必须 deadline 驱动")

    def test_c3_success_predicate_not_relaxed(self):
        self.assertIn('payload.get("status") == "ok"', self.step4, "Spec C3：判据仍是 status == ok")
        self.assertRegex(self.step4, r'\[ "\$HEALTH_OK" = "1" \] \|\| fail', "Spec C3：超时仍必须失败")

    def test_c4_timeout_message_points_at_log_and_window(self):
        line = [l for l in self.step4.splitlines() if "HEALTH_OK" in l and "fail" in l]
        self.assertTrue(line, "Spec C4：找不到超时那一行")
        self.assertIn("nohup.out", line[0], "Spec C4：超时要说去哪看日志")
        self.assertIn("HEALTH_WINDOW_SECONDS", line[0], "Spec C4：超时要说窗口可调")

    def test_c5_heartbeat_and_elapsed_reported(self):
        self.assertIn("等待 8010 就绪", self.step4, "Spec C5：等待期间要有心跳")
        self.assertIn("health：status=ok（等待", self.step4, "Spec C5：成功要报实际等待秒数")

    def test_c6_script_still_parses(self):
        proc = subprocess.run(["bash", "-n", str(DEPLOY_SH)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, "Spec C6：bash -n 必须过：%s" % proc.stderr)

    def test_c6_later_steps_untouched(self):
        for step in ('step "5. 真转两份样本', 'step "6. 下游连通自检',
                     'step "6b. 下游连通自检', 'step "7. 结论"'):
            self.assertIn(step, self.text, "Spec C6：后续步骤不许动：%s" % step)


if __name__ == "__main__":
    unittest.main()
