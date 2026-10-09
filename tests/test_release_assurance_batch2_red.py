"""红测：发布保障（批次 2）—— 分片全量回归 + 稳健共享测试件 + CI 有效门禁。

Spec：docs/specs/release-assurance-batch2.md

现状缺口（2026-10-09 实测，代码事实）：

  · T1 没有分片运行器：`scripts/` 下无任何分片/并行入口，全量只能串行（432 模块 / 约 6970 项，
    实测 10~11 分钟），客观上鼓励「只跑相关模块」；临时目录闸门 `tests/_tmp_guard.py` 是
    「一次运行一个根」，所以分片必须给每个分片独立的 TMPDIR 根，否则多进程互删临时目录；
  · T3 `tests/` 里至少 17 份各自实现的 JS 括号计数抠取（`test_home_cards_equal_height_red.py:70`、
    `test_tech_stage_context_nine_stages_red.py:39` …），不区分字符串/注释/模板串/正则，
    签名或函数体出现 `}` 字面量就抠错并让多个模块集体报红；
  · T4 冻结清单用计数断言表达（`grep` 实测 419 处 `assertEqual(len(...), <数字>)`），
    新增合法条目也会红，与「防偷加接口」的本意相反，且没有可复用的集合断言件；
  · T5 `tech_app/frontend/tech-workbench.js:19` 的 `const STAGES` 是手抄的 9 行，
    没有任何测试把它与后端 `workflow_stages.py` 逐行比对（10-9 只读比对：当前 0 漂移）；
  · T12 CI 的 `python_contract` 只 `py_compile` 4 个文件（一方 Python 共 660 个），也没有空白检查。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNNER = ROOT / "scripts" / "run_tests_sharded.py"
CI_CHECKS = ROOT / "scripts" / "ci_checks.py"
CI_PATH = ROOT / ".gitlab-ci.yml"
WORKBENCH_JS = ROOT / "tech_app" / "frontend" / "tech-workbench.js"
TESTS_DIR = ROOT / "tests"


def _module(dotted: str):
    """惰性 import：模块还不存在时返回异常对象，由用例给出可读的红点。"""
    try:
        return importlib.import_module(dotted)
    except Exception as exc:                                   # noqa: BLE001 - 交回用例判定
        return exc


def _test_modules() -> list:
    return sorted("tests." + path.stem for path in TESTS_DIR.glob("test_*.py"))


def _run(args, **kwargs):
    return subprocess.run([sys.executable] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=300, **kwargs)


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                   capture_output=True, text=True)


class T1ShardedRunner(unittest.TestCase):
    """T1：全量回归必须有确定、完整、隔离的分片执行器。"""

    def mod(self):
        mod = _module("scripts.run_tests_sharded")
        if isinstance(mod, Exception):
            self.fail("缺 `scripts/run_tests_sharded.py`（Spec §2.1）：%r" % mod)
        return mod

    def test_t1_a_discover_matches_filesystem(self):
        mod = self.mod()
        got = list(mod.discover_modules())
        self.assertEqual(_test_modules(), sorted(got),
                         "discover_modules 必须恰好覆盖 tests/test_*.py")
        self.assertEqual(sorted(got), got, "模块名必须排序输出（确定性）")

    def test_t1_b_plan_is_disjoint_complete_and_deterministic(self):
        mod = self.mod()
        mods = _test_modules()
        plan = [list(part) for part in mod.plan_shards(mods, 4)]
        self.assertEqual(4, len(plan))
        flat = [name for part in plan for name in part]
        self.assertEqual(sorted(mods), sorted(flat), "分片并集必须等于全集")
        self.assertEqual(len(flat), len(set(flat)), "分片之间必须不相交")
        self.assertTrue(all(part for part in plan), "分片数不超过模块数时不允许空分片")
        sizes = [len(part) for part in plan]
        self.assertLessEqual(max(sizes), 2 * min(sizes), "分片要基本均衡：%s" % sizes)
        self.assertEqual(plan, [list(p) for p in mod.plan_shards(mods, 4)],
                         "同一输入必须给出同一计划")

    def test_t1_c_cli_list_prints_every_module(self):
        self.assertTrue(RUNNER.exists(), "缺 `scripts/run_tests_sharded.py`（Spec §2.1）")
        proc = _run([RUNNER, "--list"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        lines = [line.strip() for line in proc.stdout.splitlines()
                 if line.strip().startswith("tests.")]
        self.assertEqual(_test_modules(), lines)

    def test_t1_d_cli_dry_run_prints_one_command_per_shard(self):
        self.assertTrue(RUNNER.exists(), "缺 `scripts/run_tests_sharded.py`（Spec §2.1）")
        proc = _run([RUNNER, "--shards", "3", "--dry-run"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        commands = [line for line in proc.stdout.splitlines() if "unittest" in line]
        self.assertEqual(3, len(commands), "dry-run 必须只打印 3 条分片命令：%r" % commands)
        for line in commands:
            self.assertIn("TMPDIR", line,
                          "每条分片命令必须带该分片独有的 TMPDIR（Spec §2.1）：%r" % line)

    def test_t1_e_shard_env_isolates_tmpdir(self):
        mod = self.mod()
        base = str(ROOT / ".tmp-shard-probe")
        envs = [mod.shard_env(index, base) for index in range(3)]
        roots = [env.get("TMPDIR") for env in envs]
        for root in roots:
            self.assertTrue(root, "shard_env 必须给出 TMPDIR（Spec §2.1）")
            self.assertTrue(str(root).startswith(base),
                            "分片根要落在给定 base 之下：%r" % root)
        self.assertEqual(len(roots), len(set(roots)), "不同分片的 TMPDIR 根不许相同")


class T3JsSourceExtractor(unittest.TestCase):
    """T3：唯一、稳健的 JS 函数体抠取（字符串/注释/模板串/正则里的括号不算）。"""

    def mod(self):
        mod = _module("tests.support.js_source")
        if isinstance(mod, Exception):
            self.fail("缺 `tests/support/js_source.py`（Spec §2.2）：%r" % mod)
        return mod

    def test_t3_a_strings_and_comments_do_not_end_the_function(self):
        mod = self.mod()
        source = (
            "function alpha() {\n"
            "  const a = \"}\";        // } 在字符串里，不是结尾\n"
            "  const b = '{';          /* { */\n"
            "  return a + b;\n"
            "}\n"
            "function beta() { return 1; }\n"
        )
        body = mod.function_body(source, "alpha")
        self.assertTrue(body, "必须抠出 alpha 的函数体")
        self.assertIn('const a = "}"', body)
        self.assertNotIn("function beta", body, "不得越界抠到下一个函数")
        self.assertIn("return a + b;", body)

    def test_t3_b_template_literals_and_regex_do_not_end_the_function(self):
        mod = self.mod()
        source = (
            "function gamma(x) {\n"
            "  const t = `a ${ {k: 1}.k } b }`;\n"
            "  const r = /}/.test(x);\n"
            "  return t + r;\n"
            "}\n"
        )
        body = mod.function_body(source, "gamma")
        self.assertIn("`a ${ {k: 1}.k } b }`", body,
                      "模板串里的 } 不得被当成函数结尾")
        self.assertIn("/}/", body, "正则字面量里的 } 不得被当成函数结尾")
        self.assertIn("return t + r;", body)

    def test_t3_c_missing_function_returns_empty(self):
        mod = self.mod()
        self.assertEqual("", mod.function_body("function only() { return 1; }", "nope"))


class T4FrozenClosedSet(unittest.TestCase):
    """T4：用集合枚举替代计数断言；失败信息必须分开列出新增与缺失。"""

    def mod(self):
        mod = _module("tests.support.frozen")
        if isinstance(mod, Exception):
            self.fail("缺 `tests/support/frozen.py`（Spec §2.3）：%r" % mod)
        return mod

    def test_t4_a_closed_set_flags_extra_and_missing(self):
        mod = self.mod()
        self.assertEqual({"extra": ["b"], "missing": ["c"]},
                         mod.frozen_diff(["a", "b"], ["a", "c"]))
        case = unittest.TestCase()
        mod.assert_closed_set(case, "routes", ["a", "b"], ["b", "a"])
        with self.assertRaises(AssertionError) as ctx:
            mod.assert_closed_set(case, "routes", ["a", "b"], ["a", "c"])
        text = str(ctx.exception)
        self.assertIn("新增", text)
        self.assertIn("b", text)
        self.assertIn("缺失", text)
        self.assertIn("c", text)


class T5StageTableParity(unittest.TestCase):
    """T5：前端手抄编号表与后端唯一事实源必须能逐行比对。"""

    def mod(self):
        mod = _module("tests.support.stage_table")
        if isinstance(mod, Exception):
            self.fail("缺 `tests/support/stage_table.py`（Spec §2.4）：%r" % mod)
        return mod

    def test_t5_a_frontend_rows_match_backend(self):
        mod = self.mod()
        rows = mod.frontend_rows(WORKBENCH_JS)
        self.assertEqual(9, len(rows), "tech-workbench.js 的 STAGES 是 9 行")
        self.assertEqual({"id", "phase", "phaseTitle", "no", "subTitle", "page"},
                         set(rows[0].keys()))
        backend = mod.backend_rows()
        self.assertEqual(9, len(backend))
        mod.assert_parity(unittest.TestCase())


class T12CiChecks(unittest.TestCase):
    """T12：CI 的语法检查要覆盖全部一方 Python，并保持「CI 不部署」。"""

    def mod(self):
        mod = _module("scripts.ci_checks")
        if isinstance(mod, Exception):
            self.fail("缺 `scripts/ci_checks.py`（Spec §2.5）：%r" % mod)
        return mod

    def test_t12_a_compile_all_detects_syntax_error(self):
        mod = self.mod()
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder)
            (path / "ok.py").write_text("x = 1\n", encoding="utf-8")
            good = mod.compile_all([folder])
            self.assertTrue(good["ok"], good)
            self.assertGreaterEqual(int(good["checked"]), 1)
            (path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
            bad = mod.compile_all([folder])
            self.assertFalse(bad["ok"])
            self.assertTrue(any("bad.py" in str(item) for item in bad["failures"]),
                            "必须点出语法错的文件：%r" % bad)

    def test_t12_b_whitespace_detects_trailing_space(self):
        mod = self.mod()
        with tempfile.TemporaryDirectory() as folder:
            repo = pathlib.Path(folder)
            _git(repo, "init", "-q", "-b", "main")
            _git(repo, "config", "user.email", "ci@local")
            _git(repo, "config", "user.name", "ci")
            (repo / "a.txt").write_text("line\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "base")
            (repo / "a.txt").write_text("line \n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "trailing")
            dirty = mod.whitespace(base="HEAD~1", cwd=str(repo))
            self.assertFalse(dirty["ok"], dirty)
            self.assertTrue(any("a.txt" in str(item) for item in dirty["offenders"]),
                            "必须点出空白错的文件：%r" % dirty)
            (repo / "a.txt").write_text("line\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "clean")
            self.assertTrue(mod.whitespace(base="HEAD~1", cwd=str(repo))["ok"])

    def test_t12_c_ci_wires_checks_and_stays_deploy_free(self):
        from scripts.cpq_eval import ci_yaml
        jobs = ci_yaml.jobs(ci_yaml.load(CI_PATH))
        self.assertIn("python_contract", jobs)
        script = "\n".join(ci_yaml.job_script(jobs["python_contract"]))
        self.assertIn("scripts/ci_checks.py", script,
                      "python_contract 必须调用 scripts/ci_checks.py（Spec §2.5）")
        self.assertIn("--compile-all", script)
        self.assertIn("--whitespace", script)
        self.assertNotIn("allow_failure", jobs["python_contract"],
                         "python_contract 失败必须拦住合入")
        for name, job in jobs.items():
            text = "\n".join(ci_yaml.job_script(job))
            for needle in ("deploy", "systemctl", "ssh", "scp", "rsync", "git pull"):
                self.assertNotIn(needle, text, "%s 不得含 %r（CI 不部署）" % (name, needle))


if __name__ == "__main__":
    unittest.main()
