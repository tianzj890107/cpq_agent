# -*- coding: utf-8 -*-
"""护栏：本地测试的临时目录闸门**真的在收拾**，而不是只写在文档里的承诺。

为什么要有这条：口径写在 `tests/_tmp_guard.py` / `scripts/reclaim_test_tmpdirs.py` 的注释里，
注释不会红。这里用**子进程真跑**把三件事钉死：

  1. `import tests` 之后 `tempfile.gettempdir()` 与 `TMPDIR` 都落在 `cpq-testrun-*` 根里
     （否则子进程建的目录又散回系统 TMPDIR）；
  2. 进程退出后，本次建的临时目录与那个根**都不存在了**（这就是"不留垃圾"的定义）；
  3. `CPQ_TEST_KEEP_TMP=1` 时**必须留着**（调试红测要看现场 —— 这条不许被"顺手优化"掉）。

加一条对 `scripts/reclaim_test_tmpdirs.py` 的判据护栏：家族清单必须从源码派生（不手抄），
而且**绝不**把系统条目（`TemporaryItems` 这种）当自家垃圾 —— 误删比漏删严重得多。
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JANITOR_PATH = ROOT / "scripts" / "reclaim_test_tmpdirs.py"

#: 子进程里做的三件事：触发闸门 → 建一个无前缀临时目录 → 把事实打成 JSON 回传。
CHILD = (
    "import json, os, tempfile\n"
    "import tests  # noqa: F401 - 只为触发闸门\n"
    "from tests import _tmp_guard\n"
    "made = tempfile.mkdtemp()\n"
    "prefixed = tempfile.mkdtemp(prefix='cpq-guard-test-')\n"
    "print(json.dumps({'tmpdir': tempfile.gettempdir(), 'env': os.environ.get('TMPDIR', ''),\n"
    "                  'made': made, 'prefixed': prefixed, 'root': _tmp_guard.run_root()}))\n"
)


def load_janitor():
    """按路径加载 `scripts/reclaim_test_tmpdirs.py`：`scripts` 不是包，而且 import `tests` 会搬走 TMPDIR。"""
    spec = importlib.util.spec_from_file_location("cpq_reclaim_test_tmpdirs_under_test", JANITOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TmpGuardTest(unittest.TestCase):
    """闸门本体：在子进程里真跑，本进程的临时目录一个都不动。"""

    def run_child(self, env=None):
        environ = dict(os.environ)
        environ.pop("CPQ_TEST_KEEP_TMP", None)
        environ.pop("CPQ_TEST_TMP_QUIET", None)
        environ["CPQ_TEST_TMP_QUIET"] = "1"
        environ.update(env or {})
        proc = subprocess.run([sys.executable, "-c", CHILD], cwd=str(ROOT),
                              capture_output=True, text=True, env=environ)
        self.assertEqual(0, proc.returncode, "子进程失败：%s" % proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        return payload, proc

    def test_a1_run_root_is_the_only_tmpdir(self):
        payload, _ = self.run_child()
        root = payload["root"]
        self.assertTrue(Path(root).name.startswith("cpq-testrun-"),
                        "运行根必须叫 cpq-testrun-*，现在叫 %s" % Path(root).name)
        self.assertEqual(Path(root).resolve(), Path(payload["tmpdir"]).resolve(),
                         "tempfile.gettempdir() 必须指向运行根")
        self.assertEqual(Path(root).resolve(), Path(payload["env"]).resolve(),
                         "TMPDIR 必须指向运行根（否则子进程又散回系统 TMPDIR）")
        for key in ("made", "prefixed"):
            self.assertTrue(Path(payload[key]).resolve().is_relative_to(Path(root).resolve()),
                            "%s 必须落在运行根里：%s" % (key, payload[key]))

    def test_a2_everything_is_gone_after_exit(self):
        payload, _ = self.run_child()
        for key in ("made", "prefixed", "root"):
            self.assertFalse(Path(payload[key]).exists(),
                             "进程退出后 %s 必须已被清掉：%s" % (key, payload[key]))

    def test_a3_keep_env_keeps_the_scene(self):
        payload, proc = self.run_child({"CPQ_TEST_KEEP_TMP": "1", "CPQ_TEST_TMP_QUIET": "0"})
        root = Path(payload["root"])
        self.assertTrue(Path(payload["made"]).exists(),
                        "CPQ_TEST_KEEP_TMP=1 时必须留着现场（否则红测没法看）")
        self.assertIn("保留现场", proc.stderr)
        shutil.rmtree(root, ignore_errors=True)          # 自己收尾，不给后面留垃圾
        self.assertFalse(root.exists())

    def test_a4_install_is_idempotent(self):
        code = ("import tests\n"
                "from tests import _tmp_guard\n"
                "first = _tmp_guard.run_root()\n"
                "second = _tmp_guard.install()\n"
                "print('SAME' if first == second else 'DIFF')\n")
        environ = dict(os.environ)
        environ["CPQ_TEST_TMP_QUIET"] = "1"
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                              capture_output=True, text=True, env=environ)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("SAME", proc.stdout, "install() 必须幂等")


class JanitorRulesTest(unittest.TestCase):
    """`scripts/reclaim_test_tmpdirs.py` 的判据：纯函数级，不碰文件系统。"""

    @classmethod
    def setUpClass(cls):
        cls.jan = load_janitor()
        cls.families, cls.hits = cls.jan.families()
        cls.now = time.time()

    def verdict(self, name, **kwargs):
        kwargs.setdefault("is_dir", True)
        kwargs.setdefault("is_symlink", False)
        kwargs.setdefault("mtime", self.now - 86400)
        payload = kwargs.pop("payload", False)
        allow_empty = kwargs.pop("allow_empty", False)
        older_than = kwargs.pop("older_than", 21600)
        return self.jan.verdict(name, family_list=self.families, now=self.now,
                                older_than=older_than, payload=payload,
                                allow_empty=allow_empty, **kwargs)

    def test_b1_families_are_derived_not_handwritten(self):
        self.assertGreaterEqual(self.hits, 100, "家族清单必须从源码字面量派生")
        for expected in ("cpq-kb-script-", "cpq-testrun-", "thumb-red-"):
            self.assertIn(expected, self.families)
        for name in self.families:
            self.assertRegex(name, r"^[A-Za-z0-9._-]+$",
                             "家族前缀必须是能当目录名用的 ASCII：%r" % name)

    def test_b2_system_entries_are_never_ours(self):
        for name in ("TemporaryItems", "AudioComponentRegistrar", "SandboxHelper",
                     "randomdir", "cpq"):                       # `cpq` 不是任何家族前缀
            family, reason = self.verdict(name)
            self.assertEqual("", family, "%s 被误认成自家垃圾了" % name)
            self.assertEqual("unknown_family", reason)

    def test_b3_only_old_entries(self):
        self.assertEqual(("cpq-kb-script-", "prefixed"),
                         self.verdict("cpq-kb-script-abc123"))
        self.assertEqual(("", "too_new"),
                         self.verdict("cpq-kb-script-abc123", mtime=self.now - 60))
        self.assertEqual(("cpq-kb-script-", "prefixed"),
                         self.verdict("cpq-kb-script-abc123", older_than=30))

    def test_b4_bare_prefix_needs_payload_unless_allowed(self):
        self.assertEqual(("", "bare_without_payload"), self.verdict("tmpab12cd34"))
        self.assertEqual(("tmp########", "bare_with_payload"),
                         self.verdict("tmpab12cd34", payload=True))
        self.assertEqual(("tmp########", "bare_empty"),
                         self.verdict("tmpab12cd34", allow_empty=True))
        # 名字形状不对的不算（`tmp` 后必须是 8 位随机名）
        self.assertEqual("", self.verdict("tmp")[0])
        self.assertEqual("", self.verdict("tmpABCDEFGH")[0])
        self.assertEqual("", self.verdict("tmpab12cd34", mtime=self.now - 60)[0])

    def test_b5_symlink_and_files_are_off_limits(self):
        self.assertEqual(("", "symlink"),
                         self.verdict("cpq-kb-script-abc123", is_symlink=True))
        self.assertEqual(("", "not_a_dir"),
                         self.verdict("cpq-kb-script-abc123", is_dir=False))

    def test_b6_age_parsing(self):
        self.assertEqual(21600, self.jan.parse_age("6h"))
        self.assertEqual(1800, self.jan.parse_age("30m"))
        self.assertEqual(90, self.jan.parse_age("90s"))
        self.assertEqual(172800, self.jan.parse_age("2d"))
        self.assertEqual(21600, self.jan.parse_age("6"))
        self.assertEqual(21600, self.jan.parse_age(""))

    def test_b8_forbidden_roots_are_refused(self):
        """`AGENTS.md` 的删除纪律：仓库根 / home / 系统根及其上级都不许当清理根。"""
        self.assertTrue(self.jan.root_is_safe("/tmp"))
        self.assertTrue(self.jan.root_is_safe(tempfile.gettempdir()))
        self.assertFalse(self.jan.root_is_safe("/"))
        self.assertFalse(self.jan.root_is_safe(Path.home()))
        self.assertFalse(self.jan.root_is_safe(ROOT))
        self.assertFalse(self.jan.root_is_safe(ROOT.parent))

    def test_b7_evidence_is_what_tests_actually_leave(self):
        """证据规则要认得住实测样本，也要挡住无关目录。"""
        probe = Path(tempfile.mkdtemp(prefix="cpq-guard-evidence-"))
        self.addCleanup(shutil.rmtree, probe, True)
        self.assertFalse(self.jan.has_payload(probe), "空目录不算有载荷")
        (probe / "cost.sqlite3").write_text("", encoding="utf-8")
        self.assertTrue(self.jan.has_payload(probe))
        (probe / "cost.sqlite3").unlink()
        (probe / "pkgbom000001").mkdir()
        self.assertTrue(self.jan.has_payload(probe))
        (probe / "pkgbom000001").rmdir()
        (probe / "notes.txt").write_text("别的工具的东西", encoding="utf-8")
        self.assertFalse(self.jan.has_payload(probe), "不认得的载荷不许当证据")


if __name__ == "__main__":
    unittest.main()
