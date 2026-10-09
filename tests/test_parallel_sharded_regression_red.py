"""红测：真并行分片回归 —— 分片必须**同时**跑，不是只切不改。

Spec：docs/specs/parallel-sharded-regression.md

现状（2026-10-09 实测，可指到行）：

  · `scripts/run_tests_sharded.py::run_shards()` 在 `for index, modules in enumerate(plan)` 里
    **逐个阻塞** `subprocess.run(...)`；函数内调用集合无 `ThreadPoolExecutor` / `concurrent.futures`；
  · 所以 `--shards 8` 的墙钟 ≈ `--shards 1`（本机 8 核 / 436 模块，仍是 10 分钟级）；
  · 批 2 的 Spec/红测只要求「分片 + 隔离 + 与串行结论一致」，**从未要求并发** —— 规格漏项。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import inspect
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNNER = ROOT / "scripts" / "run_tests_sharded.py"


def _module():
    try:
        return importlib.import_module("scripts.run_tests_sharded")
    except Exception as exc:                                   # noqa: BLE001
        return exc


def _fake_run(delay):
    def fake(cmd, **kwargs):                                   # noqa: ANN001 - 交回用例
        time.sleep(delay)
        text = "Ran 1 test in 0.001s\n\nOK\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")
    return fake


class ParallelShardRunner(unittest.TestCase):
    """T1+：`run_shards` 必须支持真并发，且聚合/顺序/隔离口径不变。"""

    def mod(self):
        mod = _module()
        if isinstance(mod, Exception):
            self.fail("缺 `scripts/run_tests_sharded.py`：%r" % mod)
        return mod

    def test_r1_run_shards_accepts_jobs(self):
        mod = self.mod()
        params = inspect.signature(mod.run_shards).parameters
        self.assertIn("jobs", params,
                      "run_shards 必须支持 jobs=（Spec §2.1）：当前参数 %s" % list(params))

    def test_r2_runner_mentions_thread_pool(self):
        text = RUNNER.read_text(encoding="utf-8")
        self.assertTrue("ThreadPoolExecutor" in text or "concurrent.futures" in text,
                        "并发必须用线程池（Spec §2.1）：分片是 subprocess，线程即可真正并行")
        self.assertTrue("--jobs" in text, "CLI 必须暴露 --jobs（Spec §2.2）")

    def test_r3_parallel_wall_time_beats_serial_sum(self):
        mod = self.mod()
        if "jobs" not in inspect.signature(mod.run_shards).parameters:
            self.fail("run_shards 还不支持 jobs=（Spec §2.1）")
        plan = [["tests.a"], ["tests.b"], ["tests.c"], ["tests.d"]]
        delay = 0.4
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mod.subprocess, "run", side_effect=_fake_run(delay)):
                started = time.time()
                out = mod.run_shards(plan, tmp, jobs=4)
                elapsed = time.time() - started
        self.assertEqual(4, len(out["shards"]))
        self.assertEqual([0, 1, 2, 3], [row["index"] for row in out["shards"]],
                         "并发下 shards 仍按 index 升序")
        self.assertEqual(4, out["totals"]["ran"], "聚合口径不变（4 个分片各 Ran=1）")
        self.assertTrue(out["ok"])
        self.assertLess(elapsed, delay * len(plan) * 0.7,
                        "真并行必须显著快于串行和：实测 %.2fs vs 串行下界 %.2fs"
                        % (elapsed, delay * len(plan)))

    def test_r4_jobs_one_stays_sequential_and_isolated(self):
        mod = self.mod()
        plan = [["tests.a"], ["tests.b"], ["tests.c"]]
        delay = 0.3
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mod.subprocess, "run", side_effect=_fake_run(delay)):
                started = time.time()
                out = mod.run_shards(plan, tmp, jobs=1) if "jobs" in inspect.signature(
                    mod.run_shards).parameters else mod.run_shards(plan, tmp)
                elapsed = time.time() - started
        self.assertEqual(3, len(out["shards"]))
        self.assertEqual(3, out["totals"]["ran"])
        self.assertGreaterEqual(elapsed, delay * len(plan) * 0.8,
                                "jobs=1 必须保持串行（Spec §2.1）")
        temps = [mod.shard_env(i, tmp)["TMPDIR"] for i in range(len(plan))]
        self.assertEqual(len(temps), len(set(temps)), "分片 TMPDIR 必须互不相同（并发前提）")

    def test_r5_totals_agree_between_jobs_one_and_many(self):
        mod = self.mod()
        if "jobs" not in inspect.signature(mod.run_shards).parameters:
            self.fail("run_shards 还不支持 jobs=（Spec §2.1）")
        plan = [["tests.a"], ["tests.b"], ["tests.c"], ["tests.d"], ["tests.e"]]
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            with mock.patch.object(mod.subprocess, "run", side_effect=_fake_run(0.05)):
                serial = mod.run_shards(plan, tmp1, jobs=1)
                parallel = mod.run_shards(plan, tmp2, jobs=5)
        self.assertEqual(serial["totals"], parallel["totals"],
                         "串行与并行的 totals 必须一致（Spec §2.3）")
        self.assertEqual(serial["ok"], parallel["ok"])


if __name__ == "__main__":
    unittest.main()
