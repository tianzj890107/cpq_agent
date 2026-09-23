"""红测/护栏：门禁的「角色已知件数」分子必须**只有一处来源** —— 引擎 `summarize()` 给的键，
不是拿三位小数的比值乘回分母。

Spec：`docs/specs/packaging-parts-role-known-numerator-must-not-be-reconstructed.md`
（§2.1 引擎给键、§2.2 门禁不许反推、§6.2 自己记下的覆盖缺口：
「R5 是源码守卫；它不证明门禁在**所有**调用路径上都拿到了分子」）。
血缘：`packaging-parts-gate-threshold-recalibration.md` §C1/§C2（读数行与绝对分子地板的口径）。

为什么还要这一份（既有红测 `test_packaging_parts_role_known_numerator_red.py` 的覆盖缺口）：

  · R5 是 **ast 源码守卫**（函数体里不许出现 `role_known_ratio` 取数）——它一次都不执行那段代码；
    真把 `_sample_metrics()` 跑起来的只有 R4 与门禁的 B 组，两组都要求**真样本 + 转换器**，
    默认 `skip`（本机实测：`test_packaging_parts_role_known_numerator_red` 默认跑 `skipped=1`）；
  · 门禁自己的 `_check_outline_real_sample()` 更甚：样本目录或转换器缺一件就整项 `skip`
    （`tech_app/tools/packaging_parts_gate.py` 的前两个分支）；
  · 于是「分子到底从哪来」这条判据，在**默认跑**里没有任何一条用例真的执行过 —— 这正是 §6.2 自述的缺口。

本文件把三段执行路径**离线**打通（转换 / 解析 / 零件 / 挤出全部打桩，`summarize()` 的输出按样例给定）：

  P1  `_sample_metrics()` 拿到的 `role_known_total` 必须是 `summarize()` 给的那个键（不是反推值）；
  P2  `_check_outline_real_sample()` 的读数行打出来的必须是同一个数（源与读数不许两本账）；
  P3  同一份 metrics 交给 `sample_verdict()`：地板按**分子**判 —— 样例是
      「引擎分子 7 < 地板 8、而 `round(ratio × 1500)` 是 8」，反推口径会把这条判成 pass（假 go），
      直读口径必须给 fail；
  P4  自检：样例里两个口径**必须不相等**，否则本文件会退化成空转（改夹具即红）。

纪律：纯离网（转换 / 解析 / 零件 / 挤出全打桩，样本目录用临时目录造占位文件）；
不连 PG / 34、不发 HTTP、不写业务数据、不改业务实现、不读真实样本。
"""
from __future__ import annotations

import contextlib
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.tools import packaging_parts_gate as gate                 # noqa: E402

RATIO_KEY = "role_known_ratio"
TOTAL_KEY = "role_known_total"
ROUND_BOX = "圆盘盒.dwg"

#: 样例一（Spec §1 的表里那一行）：3000 件 / 2 件角色已知 → 比值 `0.001` → 反推 **3**（多报 1）。
SMALL_SAMPLE = {"part_total": 3000, "engine_total": 2, "ratio": 0.001, "closed_total": 3}
#: 样例二（地板级判别）：1500 件 / 7 件角色已知 → 比值 `0.005` → 反推 **8** = 地板（会被放行）。
FLOOR_SAMPLE = {"part_total": 1500, "engine_total": 7, "ratio": 0.005, "closed_total": 255}


def reconstructed(metrics: dict) -> int:
    """旧口径的反推值（`int(round(ratio × part_total))`）—— 用来证明样例有判别力。"""
    return int(round(float(metrics[RATIO_KEY]) * int(metrics["part_total"])))


def rows(count: int) -> list:
    return [{"part_code": "DWG-P%02d" % (i + 1)} for i in range(count)]


@contextlib.contextmanager
def offline_pipeline(summary: dict, count: int):
    """把 `_sample_metrics()` 的重活全部换成打桩，只留门禁自己的取数 / 组装 / 读数逻辑真跑。"""
    from tech_app.backend.services import cad_ir, packaging_part_solids, packaging_parts

    with mock.patch.object(gate, "_sample_dxf", lambda path: (b"stub-dxf", "打桩转换器")), \
            mock.patch.object(cad_ir, "parse_dxf", lambda *a, **k: {"engine": "stub"}), \
            mock.patch.object(packaging_parts, "extract", lambda ir: {"parts": rows(count)}), \
            mock.patch.object(packaging_parts, "summarize", lambda doc: dict(summary)), \
            mock.patch.object(packaging_parts, "processability", lambda row: {"ok": True}), \
            mock.patch.object(packaging_part_solids, "extrude", lambda row, **k: {"status": "ok"}):
        yield


def metrics_of(sample: dict) -> dict:
    """真跑一遍 `_sample_metrics()`（重活打桩），返回门禁自己组装出来的那份 metrics。"""
    summary = {RATIO_KEY: sample["ratio"], TOTAL_KEY: sample["engine_total"],
               "closed_ratio": 0.8, "closed_total": sample["closed_total"]}
    with offline_pipeline(summary, sample["part_total"]):
        return gate._sample_metrics(pathlib.Path("合成占位.dxf"))


class SampleMetricsProvenance(unittest.TestCase):
    """P1 / P4：分子直读引擎键；样例必须真的有判别力。"""

    def test_p1_numerator_comes_from_the_engine_key(self):
        metrics = metrics_of(SMALL_SAMPLE)
        self.assertEqual(int(metrics[TOTAL_KEY]), SMALL_SAMPLE["engine_total"],
                         "`_sample_metrics()` 的分子必须是引擎给的 `%s`（Spec §2.2）：%r"
                         % (TOTAL_KEY, metrics))
        self.assertNotEqual(int(metrics[TOTAL_KEY]), reconstructed(metrics),
                            "分子不许由比值反推（Spec §2.2）：反推是 %d，引擎给的是 %d"
                            % (reconstructed(metrics), SMALL_SAMPLE["engine_total"]))
        self.assertEqual(int(metrics["part_total"]), SMALL_SAMPLE["part_total"],
                         "分母照旧是零件行数（Spec §2.2 只换分子的来源）")

    def test_p4_the_sample_actually_discriminates(self):
        for name, sample in (("小样本", SMALL_SAMPLE), ("地板级样本", FLOOR_SAMPLE)):
            metrics = metrics_of(sample)
            with self.subTest(sample=name):
                self.assertNotEqual(reconstructed(metrics), int(metrics[TOTAL_KEY]),
                                    "%s 的两个口径相等 ⇒ 本文件的断言会退化成空转：%r"
                                    % (name, metrics))


class ReadoutLineProvenance(unittest.TestCase):
    """P2：读数行与地板比较用的是同一个数（引擎分子），不是另一份反推。"""

    def _line(self, sample: dict) -> str:
        placeholder = tempfile.mkdtemp(prefix="cpq-gate-numerator-")
        directory = pathlib.Path(placeholder)
        for name in gate.THRESHOLDS:
            (directory / name).write_bytes(b"")                     # 占位，只为过 exists() 判定
        metrics = dict(metrics_of(sample))
        with mock.patch.object(gate, "SAMPLES_DIR", directory), \
                mock.patch.object(gate, "_converter_gap", lambda: ""), \
                mock.patch.object(gate, "_sample_metrics", lambda path: dict(metrics)):
            result = gate._check_outline_real_sample("local")
        return "；".join(result.get("metrics") or [])

    def test_p2_readout_line_prints_the_engine_numerator(self):
        line = self._line(SMALL_SAMPLE)
        self.assertTrue(line, "门禁必须给出读数行（Spec §C2）")
        self.assertIn("%s=%d" % (TOTAL_KEY, SMALL_SAMPLE["engine_total"]), line,
                      "读数行必须是引擎给的分子（Spec §2.2/§C2）：%s" % line)
        self.assertNotIn("%s=%d" % (TOTAL_KEY, 3), line,
                         "读数行不许出现反推值 3（`round(0.001 × 3000)`，Spec §2.2）：%s" % line)
        self.assertIn("part_total=%d" % SMALL_SAMPLE["part_total"], line,
                      "分母照旧逐字（Spec §C2）：%s" % line)


class FloorUsesTheEngineNumerator(unittest.TestCase):
    """P3：地板按分子判 —— 反推会假 go，直读必须 fail。"""

    def test_p3_floor_is_judged_by_the_engine_numerator(self):
        metrics = metrics_of(FLOOR_SAMPLE)
        floors = gate.THRESHOLDS[ROUND_BOX]
        self.assertEqual(8, int(floors[TOTAL_KEY]), "圆盘盒的角色已知地板是 8（Spec §C1 冻结）")
        self.assertLess(int(metrics[TOTAL_KEY]), int(floors[TOTAL_KEY]),
                        "样例的引擎分子必须低于地板，这条才有判别力：%r" % metrics)
        self.assertGreaterEqual(reconstructed(metrics), int(floors[TOTAL_KEY]),
                                "样例的反推值必须**够**地板 —— 否则证明不了「反推会假 go」：%r" % metrics)
        bad = gate.sample_verdict(ROUND_BOX, metrics)
        self.assertTrue([row for row in bad if TOTAL_KEY in row],
                        "分子低于地板必须 fail、且失败原因点名 %s（Spec §C1/§C3）：%r"
                        % (TOTAL_KEY, bad))


if __name__ == "__main__":
    unittest.main()
