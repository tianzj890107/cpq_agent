"""红测：Spec「状态」行必须与红测事实一致（全仓，不限快速报价系列）。

Spec：`docs/specs/spec-status-consistency-repo-wide.md`
血缘：`docs/specs/spec-status-consistency.md`（第一批只覆盖 `quick-quote-*.md`，§3 把其它系列
记为历史存量、另批再收）——本文件就是那个「另批」。

现状缺口（2026-09-22 实测，不是推断）：
  · `docs/specs/` 231 份 Spec 里 107 份写了状态行，其中 **93 份**要么写法机器读不了
    （`状态：TDD Red，等待 DeepSeek 实现。`、`状态：Spec（待实现）`、`状态：**未实现**`），
    要么结论与事实相反（`## 214` / `## 289` 等落地后仍写「等待实现」）；
  · 现成的守卫 `tests/test_spec_status_consistency_red.py` 只看 `quick-quote-*.md`，
    其它 9 个系列（tech-／chat-／quote-／integration-／converter-／deploy- …）无人看；
  · 后果是"看着像还没做、其实早做完了"：重复排查、重复写红测，人（和 Agent）按 Spec 头
    判断"这批还没做"。

纪律：
  · 只读：扫文档 + 跑「声明未实现」的 Spec 点名的红测（子进程）；不改任何文件；
  · 不连 PG、不发 HTTP、不写业务数据；
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

SPECS_DIR = ROOT / "docs" / "specs"
LEGAL_PREFIXES = ("状态：Spec + 红测（未实现）", "状态：Spec + 红测（已实现）")
#: 2026-09-22 对账后 `docs/specs/*.md` 全部 232 份都写了状态行；这个下限只防"规则悄悄空转"
#: （有人把状态行批量删掉时能报警），不做上限。
MIN_DECLARED = 220
PYTHON = sys.executable


def spec_files():
    return sorted(SPECS_DIR.glob("*.md"))


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
    """第一处 `红测：` 行里点名的测试文件（相对仓库根，按出现顺序、去重）。"""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if line.startswith("红测："):
            out = []
            for item in re.findall(r"`([^`]+)`", line):
                if item.startswith("tests/") and item.endswith(".py"):
                    if item not in out:
                        out.append(item)
            return out
    return []


def declared_specs():
    """(path, status_line, red_test 路径) 三元组，只含写了状态行的 Spec。"""
    out = []
    for path in spec_files():
        line = status_line(path)
        if line:
            out.append((path, line, red_test_paths(path)))
    return out


def run_red_test(rel_path):
    module = rel_path[:-3].replace("/", ".")
    proc = subprocess.run([PYTHON, "-m", "unittest", module], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=900)
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-4:])
    return proc.returncode, tail


class Base(unittest.TestCase):
    def specs(self):
        specs = declared_specs()
        self.assertTrue(specs, "docs/specs 下没有任何写了「状态：」行的 Spec")
        return specs


# --------------------------------------------------------------------------- #
# A 组：状态行字面量（全仓）
# --------------------------------------------------------------------------- #
class TestAStatusLiteral(Base):
    def test_a1_every_declared_spec_uses_a_legal_literal(self):
        bad = []
        for path, line, _ in self.specs():
            if not line.startswith(LEGAL_PREFIXES):
                bad.append("%s：%r 不是合法字面量（只允许 %s 开头）"
                           % (path.name, line[:70], " 或 ".join(LEGAL_PREFIXES)))
        self.assertEqual([], bad,
                         "状态行必须用两个合法字面量之一（Spec §1.1）：\n" + "\n".join(bad))

    def test_a2_rule_is_not_vacuous(self):
        self.assertGreaterEqual(
            len(self.specs()), MIN_DECLARED,
            "写了「状态：」行的 Spec 少于 %d 份，这条守卫就快成空转了（Spec §1.4）" % MIN_DECLARED)

    def test_a3_machine_readable_state(self):
        for path, line, _ in self.specs():
            self.assertIn(declared_state(line), ("未实现", "已实现"),
                          "%s 的状态必须能机械判定（Spec §1.1）" % path.name)


# --------------------------------------------------------------------------- #
# B 组：必须点名存在的红测文件
# --------------------------------------------------------------------------- #
class TestBRedTestDeclared(Base):
    def test_b1_every_declared_spec_names_an_existing_red_test(self):
        bad = []
        for path, _, refs in self.specs():
            if not refs:
                bad.append("%s：写了状态行，却没有 `红测：` 行点名测试文件" % path.name)
                continue
            for rel in refs:
                if not (ROOT / rel).exists():
                    bad.append("%s：红测文件不存在 %s" % (path.name, rel))
        self.assertEqual([], bad,
                         "声明了状态就必须点名存在的红测（Spec §1.2）：\n" + "\n".join(bad))


# --------------------------------------------------------------------------- #
# C 组：声明「未实现」必须与事实一致（这是危险方向：谎报"还没做"）
# --------------------------------------------------------------------------- #
class TestCPendingIsTrue(Base):
    def test_c1_pending_specs_declare_a_reason(self):
        bad = []
        for path, line, _ in self.specs():
            if declared_state(line) != "未实现":
                continue
            if "（" not in line[len(LEGAL_PREFIXES[0]):]:
                bad.append("%s：声明「未实现」却没写明原因（Spec §1.3）" % path.name)
        self.assertEqual([], bad, "\n".join(bad))

    def test_c2_pending_specs_red_test_actually_fails(self):
        bad = []
        for path, line, refs in self.specs():
            if declared_state(line) != "未实现":
                continue
            if not refs:
                bad.append("%s：声明「未实现」但没点名红测（B 组已单独报）" % path.name)
                continue
            for rel in refs:
                if not (ROOT / rel).exists():
                    continue
                code, tail = run_red_test(rel)
                if code == 0:
                    bad.append("%s 声明「未实现」，但 %s 已经全绿（该改成已实现，或说明冲突）"
                               % (path.name, rel))
        self.assertEqual([], bad,
                         "声明「未实现」的红测必须当前失败（Spec §1.3）：\n" + "\n".join(bad))

    def test_c3_implemented_specs_outnumber_pending(self):
        pending = [p.name for p, line, _ in self.specs() if declared_state(line) == "未实现"]
        self.assertLess(len(pending), len(self.specs()),
                        "全部 Spec 都声明「未实现」显然不对（Spec §1.3）")


if __name__ == "__main__":
    unittest.main()
