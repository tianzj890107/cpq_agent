"""红测：Spec「状态」行必须与红测的实际结果一致（快速报价系列）。

Spec：`docs/specs/spec-status-consistency.md`
范围：`docs/specs/quick-quote-*.md`（13 份）。

现状缺口（2026-09-21 实测，不是推断）：
  · `quick-quote-10-material-gsm-attribution.md` 头部写「状态：**待实现**（红测先写、先跑红）」，
    而它那 37 条红测**已经全绿**（实现者当场改绿，Spec 头没跟）；
  · 同系列另有 9 份 Spec 写着「未实现」而红测早已全绿；
  · 写法还不统一：`**待实现**`、`Spec（**未实现**）`、`Spec + 红测 + **实现**` 三种都有，
    机器读不了、人也会读错。

纪律：
  · 只读：扫文档、跑既有红测文件（子进程），不改任何文件；
  · 不连 PG、不发 HTTP；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC_GLOB = "quick-quote-*.md"
LEGAL_PREFIXES = ("状态：Spec + 红测（未实现）", "状态：Spec + 红测（已实现）")
PYTHON = sys.executable


def spec_files():
    return sorted((ROOT / "docs" / "specs").glob(SPEC_GLOB))


def status_line(path):
    """第一处以「状态：」开头的行（去前导 `- `、去 `**`、去首尾空白）。"""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if line.startswith("状态："):
            return line.replace("**", "")
    return ""


def declared_state(line):
    return "未实现" if "未实现" in line else "已实现"


def red_test_paths(path):
    """`红测：` 行里反引号给出的路径（可能有多个 / 可能是 glob）。"""
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip().startswith("红测："):
            return re.findall(r"`([^`]+)`", raw)
    return []


def run_red_test(rel_path):
    """跑一个红测文件，返回 (returncode, tail)。"""
    module = rel_path[:-3].replace("/", ".") if rel_path.endswith(".py") else rel_path
    proc = subprocess.run([PYTHON, "-m", "unittest", module], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=900)
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-4:])
    return proc.returncode, tail


class Base(unittest.TestCase):
    def specs(self):
        files = spec_files()
        self.assertTrue(files, "找不到任何 docs/specs/quick-quote-*.md")
        return files


# --------------------------------------------------------------------------- #
# A 组：状态行字面量
# --------------------------------------------------------------------------- #
class TestAStatusLiteral(Base):
    def test_a1_every_spec_declares_a_legal_status_line(self):
        bad = []
        for path in self.specs():
            line = status_line(path)
            if not line:
                bad.append("%s：没有以「状态：」开头的行" % path.name)
                continue
            if not line.startswith(LEGAL_PREFIXES):
                bad.append("%s：%r 不是合法字面量（只允许 %s）"
                           % (path.name, line[:70], " 或 ".join(LEGAL_PREFIXES)))
        self.assertEqual([], bad, "状态行必须用两个合法字面量之一（Spec §1.1）：\n" + "\n".join(bad))

    def test_a2_state_is_readable_by_machine(self):
        for path in self.specs():
            line = status_line(path)
            self.assertIn(declared_state(line), ("未实现", "已实现"),
                          "%s 的状态必须能机械判定（Spec §1.1）" % path.name)


# --------------------------------------------------------------------------- #
# B 组：红测文件可解析
# --------------------------------------------------------------------------- #
class TestBRedTestDeclared(Base):
    def test_b1_every_spec_names_an_existing_red_test(self):
        bad = []
        for path in self.specs():
            rels = red_test_paths(path)
            if not rels:
                bad.append("%s：没有 `红测：` 行" % path.name)
                continue
            for rel in rels:
                if not (ROOT / rel).exists():
                    bad.append("%s：红测文件不存在 %s" % (path.name, rel))
        self.assertEqual([], bad, "每份 Spec 都必须点名存在的红测文件（Spec §1.2）：\n" + "\n".join(bad))


# --------------------------------------------------------------------------- #
# C 组：声明与实际一致
# --------------------------------------------------------------------------- #
class TestCRealityMatches(Base):
    def test_c1_declared_state_matches_actual_result(self):
        mismatched = []
        for path in self.specs():
            state = declared_state(status_line(path))
            rels = red_test_paths(path)
            if not rels:
                continue
            for rel in rels:
                if not (ROOT / rel).exists():
                    continue
                code, tail = run_red_test(rel)
                failed = code != 0
                if state == "未实现" and not failed:
                    mismatched.append("%s 声明「未实现」，但 %s 已经全绿（该改成已实现）"
                                      % (path.name, rel))
                if state == "已实现" and failed:
                    mismatched.append("%s 声明「已实现」，但 %s 仍在失败：%s"
                                      % (path.name, rel, tail.replace("\n", " | ")))
        self.assertEqual([], mismatched,
                         "Spec 状态必须与红测实际结果一致（Spec §1.3）：\n" + "\n".join(mismatched))


if __name__ == "__main__":                                          # pragma: no cover
    unittest.main()
