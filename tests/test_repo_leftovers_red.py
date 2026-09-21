"""红测：样本目录与一次性脚本的归属说明（`.gitignore` + `scripts/README.md`）。

Spec：`docs/specs/repo-leftovers-and-sample-data.md`

现状缺口（2026-09-21 实测，不是推断）：
  · 仓库根挂着**未跟踪**的 `scripts/tmp_import_dwg_cases.py`（旧 sqlite 通道）与 **3.7 MB**
    未跟踪样本目录 `裕同包装项目-待开发/`（`酒盒.dwg` / `圆盘盒.dwg` + 三份工作簿）；
  · `.gitignore` 里没有任何对应条目（`grep 裕同 / tmp_ / 样本` 全空）；
  · `scripts/` 下没有 README，没人知道 `tmp_` 脚本与正式脚本
    `scripts/import_dwg_quick_quote_cases.py` 是什么关系、能不能删；
  · 于是每次 `git status` 都把它们当"待处理"，每次验收都要重新判断一遍。

纪律：
  · 只读：读 `.gitignore` / README / 入库脚本源码，不写、不删、不移动任何文件；
  · 明确不要求删除样本与一次性脚本（Spec §3 非目标）；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GITIGNORE = ROOT / ".gitignore"
SCRIPTS_README = ROOT / "scripts" / "README.md"
OFFICIAL_TOOL = ROOT / "scripts" / "import_dwg_quick_quote_cases.py"
SAMPLE_DIR_NAME = "裕同包装项目-待开发"


def gitignore_lines():
    if not GITIGNORE.exists():
        return []
    return [line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines()]


def code_files():
    """入库的 .py / .sh（仓库根 + scripts/，跳过虚拟环境与第三方目录）。"""
    out = []
    for pattern, base in (("*.py", ROOT), ("*.sh", ROOT),
                          ("*.py", ROOT / "scripts"), ("*.sh", ROOT / "scripts")):
        for path in sorted(base.glob(pattern)):
            out.append(path)
    return out


class TestAGitignore(unittest.TestCase):
    def test_a1_sample_dir_is_ignored(self):
        lines = gitignore_lines()
        hit = [line for line in lines
               if line and line.startswith("#") is False and SAMPLE_DIR_NAME in line]
        self.assertTrue(hit, ".gitignore 必须显式忽略样本目录 %s（Spec §1.1）；现有条目：%r"
                        % (SAMPLE_DIR_NAME, lines[-6:]))

    def test_a2_oneoff_scripts_are_ignored(self):
        lines = gitignore_lines()
        hit = [line for line in lines if line.startswith("scripts/tmp_") or line == "tmp_*.py"
               or line.startswith("tmp_")]
        self.assertTrue(hit, ".gitignore 必须忽略一次性脚本（scripts/tmp_*.py，Spec §1.1）")


class TestBScriptsReadme(unittest.TestCase):
    def setUp(self):
        if not SCRIPTS_README.exists():
            self.fail("缺 scripts/README.md（Spec §1.2）：样本与一次性脚本的归属必须有落点")

    def text(self):
        return SCRIPTS_README.read_text(encoding="utf-8")

    def test_b1_documents_oneoff_scripts(self):
        text = self.text()
        self.assertIn("tmp_", text, "必须写清 scripts/tmp_*.py 的地位（Spec §1.2 第 1 条）")
        self.assertTrue(("一次性" in text) or ("可删" in text),
                        "必须写明一次性脚本可以随时删除")
        self.assertIn("不参与部署", text, "必须写明一次性脚本不参与部署")

    def test_b2_points_at_the_official_tool(self):
        text = self.text()
        self.assertIn(OFFICIAL_TOOL.name, text, "必须点名正式脚本（Spec §1.2 第 2 条）")
        self.assertIn("--confirm", text, "正式脚本的 dry-run / --confirm 口径要写出来")

    def test_b3_documents_the_sample_dir(self):
        text = self.text()
        self.assertIn(SAMPLE_DIR_NAME, text, "必须写清样本目录的用途与归属（Spec §1.2 第 3 条）")
        self.assertIn("不入库", text, "必须写明样本不入库、不随部署分发")


class TestCNoProductionDependency(unittest.TestCase):
    def test_c1_no_tracked_script_uses_tmp_scripts(self):
        offenders = []
        pattern = re.compile(r"tmp_[A-Za-z0-9_]*\.py")
        for path in code_files():
            rel = path.relative_to(ROOT).as_posix()
            if path.name.startswith("tmp_"):
                continue                                # 脚本自己不算依赖
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:                  # pragma: no cover
                continue
            if pattern.search(text):
                offenders.append(rel)
        self.assertEqual([], offenders,
                         "入库脚本不许引用一次性脚本（Spec §1.3）：%s" % offenders)


if __name__ == "__main__":                                               # pragma: no cover
    unittest.main()
