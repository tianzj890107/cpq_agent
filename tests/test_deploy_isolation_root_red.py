"""守卫：第 6b 步的隔离断言必须盯**运行目录**（`tech_app/tech_data`），不是 `tech_app/data`。

Spec：`docs/specs/packaging-parts-downstream-acceptance.md` §6.1 + §12（更正）
依赖：`scripts/deploy_34_bare.sh` 第 6b 步（只读文本断言，不起服务、不连库、不跑自检）。

现状缺口（34 实测 2026-09-22）：`tech_app_launch.py:71` 把 `DATA_DIR` 缺省成
`tech_app/tech_data`（34 上 60 个项目全在那儿），而脚本数的是 `tech_app/data/*/meta.json`
（0 个项目）→ 断言恒等于 `0 → 0`，看着通过却抓不到"少写 DATA_DIR 前缀就在生产目录里建项目"。

纪律：不改本文件换绿；只读仓库内文件。
"""
from __future__ import annotations

import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY_SH = ROOT / "scripts" / "deploy_34_bare.sh"
LAUNCHER = ROOT / "tech_app_launch.py"


class IsolationRootGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DEPLOY_SH.read_text(encoding="utf-8")
        start = cls.text.find('step "6b.')
        end = cls.text.find('step "7. 结论"')
        assert start >= 0 and end > start, "找不到第 6b 步"
        cls.step = cls.text[start:end]

    def test_a1_launcher_default_is_the_live_root(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('os.environ.setdefault("DATA_DIR"', launcher,
                      "前提变了：启动器不再把 DATA_DIR 缺省成 tech_data，Spec §12 要重新核对")
        self.assertIn('"tech_data"', launcher, "启动器缺省目录应是 tech_data")

    def test_a2_script_resolves_the_live_root_like_the_launcher(self):
        self.assertIn("LIVE_DATA_DIR=", self.step, "Spec §12.1：必须先解析运行目录")
        self.assertIn("tech_app/tech_data", self.step,
                      "Spec §12.1：缺省运行目录必须是 tech_app/tech_data")
        self.assertIn('${DATA_DIR:-', self.step, "Spec §12.1：DATA_DIR 优先（与启动器同口径）")

    def test_a3_the_old_vacuous_path_is_gone(self):
        self.assertNotIn('ls -1 tech_app/data/*/meta.json', self.step,
                         "Spec §12：不许再直接数 tech_app/data（那是最初的错误）")
        self.assertNotIn('META_BEFORE="$(ls -1 tech_app/data', self.step)

    def test_a4_both_roots_are_counted_before_and_after(self):
        for token in ("count_projects", "META_BEFORE=", "META_AFTER=",
                      "META_INCIDENTAL_BEFORE=", "META_INCIDENTAL_AFTER="):
            self.assertIn(token, self.step, "Spec §12.2：缺少 %s" % token)

    def test_a5_output_names_the_checked_paths(self):
        self.assertIn("$LIVE_DATA_DIR 项目数", self.step, "Spec §12.3：输出要写被检查的路径")
        self.assertIn("tech_app/data $META_INCIDENTAL_BEFORE", self.step,
                      "Spec §12.3：第二个根也要报数")

    def test_a6_zero_projects_says_it_proves_nothing(self):
        self.assertIn('"$META_BEFORE" -gt 0', self.step, "Spec §12.4：0 个项目要显式说出来")
        self.assertIn("证明不了什么", self.step, "Spec §12.4：文案要说清这条断言此刻无意义")

    def test_a7_failure_messages_name_both_roots(self):
        self.assertIn("隔离自检动了运行目录", self.step, "Spec §12.2：运行目录变化必须失败")
        self.assertIn("隔离自检动了 tech_app/data", self.step, "Spec §12.2：杂项目录变化也必须失败")

    def test_a8_script_still_parses(self):
        proc = subprocess.run(["bash", "-n", str(DEPLOY_SH)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, "bash -n 必须过：%s" % proc.stderr)


if __name__ == "__main__":
    unittest.main()
