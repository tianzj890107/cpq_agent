"""红测：零件门禁的真样本门槛必须是「绝对分子地板」，且读数行同时给出比值与绝对分子
（Spec `packaging-parts-gate-threshold-recalibration.md`）。

现状缺口（实测，不是推断）：
  · `packaging_parts_gate.py:53 THRESHOLDS` 按**比值**判
    （`酒盒.dwg: closed_ratio 0.10`、`圆盘盒.dwg: closed_ratio 0.50 + role_known_ratio 0.10`）；
  · `--env local` 实测 `summary={ok:4, fail:1, manual:1}`、`verdict=no_go`，唯一失败项
    `parts_outline_real_sample`：「圆盘盒.dwg：role_known_ratio=0.029 < 门槛 0.10」——
    而同一批件的**绝对分子**是 `role_known_total = 9`（旧分母 64 时是 8，一分没掉）；
  · metrics 行（`:252`）只打比值，看不见「9 件 / 312 件」；
  · 同一条判据在测试侧已改成绝对分子地板（`test_packaging_parts_downstream_gate_red::F2`，
    依据 `packaging-parts-list-visibility-and-kinds.md` §6），门禁没跟着改 → 同一输入两个相反的裁决。

纪律：A 组纯离线（常量 + `sample_verdict()` 纯函数）；B 组要真样本 + 本机可用转换器，缺任一项即
`skip`（与本门禁自己的 `skip` 口径一致）；C 组为冻结面守卫。不连 PG / 34、不发 HTTP、不写业务数据。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.tools import packaging_parts_gate as gate                 # noqa: E402

GATE_PY = ROOT / "tech_app" / "tools" / "packaging_parts_gate.py"
ACCEPT_MD = ROOT / "docs" / "specs" / "packaging-parts-downstream-acceptance.md"
DEPLOY_MD = ROOT / "DEPLOYMENT.md"

#: Spec §C1 的逐字地板（数字来源：`ceil(原比值 × 原分母 64)`，`role_known_total` 取 `## 308` §6 的 8）。
FLOORS = {"酒盒.dwg": {"closed_total": 7},
          "圆盘盒.dwg": {"closed_total": 32, "role_known_total": 8}}

IDS = ("parts_outline_engine", "parts_outline_real_sample", "parts_panel_wired",
       "parts_downstream_wired", "parts_3d_wired", "parts_demo_script")


def _sample_metrics(name: str):
    """真样本指标；样本不在 / 本机没有可用转换器时返回 None（调用方 skip）。"""
    sample = gate.SAMPLES_DIR / name
    if not gate.SAMPLES_DIR.is_dir() or not sample.exists():
        return None
    if gate._converter_gap():
        return None
    try:
        return gate._sample_metrics(sample)
    except Exception:                                       # noqa: BLE001 - 真跑不动就 skip
        return None


def _verdict(name: str, metrics: dict):
    fn = getattr(gate, "sample_verdict", None)
    if fn is None:
        raise AssertionError("门禁缺少纯函数 sample_verdict()（Spec §C3）")
    return fn(name, metrics)


# --------------------------------------------------------------------------- #
# A 组：门槛形态 / 依据 / 纯函数裁决（离线）
# --------------------------------------------------------------------------- #
class AThresholdShape(unittest.TestCase):
    def test_a1_floors_are_absolute_counts_not_ratios(self):
        for name, floors in gate.THRESHOLDS.items():
            for key, floor in floors.items():
                self.assertNotIn("_ratio", key,
                                 "门槛必须是绝对分子地板，不许按比值（Spec §C1）：%s / %s"
                                 % (name, key))
                self.assertTrue(key.endswith("_total"),
                                "门槛键必须是计数键 `*_total`（Spec §C1）：%s / %s"
                                % (name, key))
                self.assertEqual(int(floor), floor,
                                 "地板必须是整数计数（Spec §C1）：%s / %s=%r"
                                 % (name, key, floor))

    def test_a2_floor_values_are_verbatim(self):
        for name, floors in FLOORS.items():
            self.assertIn(name, gate.THRESHOLDS, "两份样本都要有地板（Spec §C1）")
            self.assertEqual(floors, dict(gate.THRESHOLDS[name]),
                             "%s 的地板逐字（Spec §C1）" % name)

    def test_a3_acceptance_spec_carries_the_same_numbers(self):
        text = ACCEPT_MD.read_text(encoding="utf-8")
        for token in ("closed_total >= 7", "closed_total >= 32", "role_known_total >= 8"):
            self.assertIn(token, text,
                          "门槛写进 Spec 的规矩（改门槛必须改那份文件）：%s" % token)
        self.assertNotIn("role_known_ratio >= 0.10", text,
                         "旧比值门槛必须从 Spec §3 撤掉（Spec §C4）")

    def test_a4_high_ratio_with_low_count_still_fails(self):
        bad = _verdict("圆盘盒.dwg", {"closed_ratio": 0.999, "part_total": 10,
                                      "closed_total": 999, "role_known_total": 3,
                                      "processable_total": 5, "solid_total": 5})
        self.assertTrue(bad, "比值再高也不能把「有角色的件只有 3 件」判成合格（Spec §C1：不许双通道）")
        self.assertTrue([row for row in bad if "role_known_total" in row],
                        "失败原因要点名那一个键（Spec §C3）")
        self.assertTrue(all("地板" in row for row in bad),
                        "文案说的是「地板」而不是比值门槛（Spec §C3）：%s" % bad)

    def test_a5_low_ratio_with_enough_count_passes_and_invariants_hold(self):
        self.assertEqual([], _verdict("圆盘盒.dwg",
                                      {"role_known_ratio": 0.029, "part_total": 312,
                                       "closed_total": 255, "role_known_total": 9,
                                       "processable_total": 92, "solid_total": 255}),
                         "比值被分母稀释不是不合格的理由：绝对分子过地板就合格（Spec §C1/§C3）")
        for key in ("processable_total", "solid_total"):
            bad = _verdict("圆盘盒.dwg", {"closed_total": 999, "role_known_total": 99,
                                          "processable_total": 1, "solid_total": 1,
                                          key: 0})
            self.assertTrue(bad, "既有两条不变式还要在（Spec §C3）：%s" % key)


# --------------------------------------------------------------------------- #
# B 组：真样本真跑（没有样本 / 没有转换器 → skip，与本门禁同口径）
# --------------------------------------------------------------------------- #
class BRealSamples(unittest.TestCase):
    def test_b1_gate_passes_the_real_samples(self):
        if _sample_metrics("圆盘盒.dwg") is None:
            self.skipTest("本机没有真实样本或没有可用转换器（门禁自己也会 skip）")
        proc = subprocess.run([sys.executable, str(GATE_PY), "--env", "local", "--json"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        payload = json.loads(proc.stdout)
        item = {row["id"]: row for row in payload["items"]}["parts_outline_real_sample"]
        self.assertEqual("ok", item["status"],
                         "两份真实样本必须过门槛（Spec §C2/§C3）：%s" % item.get("message"))
        self.assertEqual(0, payload["summary"]["fail"], "fail 必须是 0（Spec §C3）")
        self.assertEqual("go", payload["verdict"], "没有 fail 且人工项未签字 → verdict 仍是 go（Spec §C5）")
        self.assertEqual(0, proc.returncode, "verdict=go → 退出码 0（Spec §C5）")

    def test_b2_metrics_line_shows_ratio_and_absolute_counts(self):
        metrics = _sample_metrics("圆盘盒.dwg")
        if metrics is None:
            self.skipTest("本机没有真实样本或没有可用转换器（门禁自己也会 skip）")
        result = gate._check_outline_real_sample("local")
        line = "；".join(result.get("metrics") or [])
        self.assertTrue(line, "门禁必须给出读数行（Spec §C2）")
        for token in ("closed_ratio=", "closed_total=", "part_total=", "role_known_total="):
            self.assertIn(token, line, "读数行缺 %s（Spec §C2）" % token)
        expected = int(round(float(metrics["role_known_ratio"]) * int(metrics["part_total"])))
        self.assertIn("role_known_total=%d" % expected, line,
                      "绝对分子逐字（Spec §C2）：%s" % expected)
        self.assertIn("part_total=%d" % int(metrics["part_total"]), line,
                      "分母逐字（Spec §C2）")

    def test_b3_absolute_counts_do_not_regress(self):
        for name, floors in FLOORS.items():
            metrics = _sample_metrics(name)
            if metrics is None:
                self.skipTest("本机没有真实样本或没有可用转换器")
            role_known = int(round(float(metrics["role_known_ratio"]) * int(metrics["part_total"])))
            got = {"closed_total": int(metrics["closed_total"]), "role_known_total": role_known}
            for key, floor in floors.items():
                self.assertGreaterEqual(got[key], floor,
                                        "%s 的 %s 不许低于地板 %d（Spec §C1/§6）：实测 %d"
                                        % (name, key, floor, got[key]))


# --------------------------------------------------------------------------- #
# C 组：冻结面
# --------------------------------------------------------------------------- #
class CFreeze(unittest.TestCase):
    def test_c1_item_ids_order_and_statuses_are_frozen(self):
        self.assertEqual(IDS, tuple(item_id for item_id, _k, _t in gate.GATE_ITEMS),
                         "六项 id 与顺序逐字不变（Spec §C5）")
        self.assertEqual(("ok", "fail", "manual_unacknowledged", "acknowledged", "skip"),
                         tuple(gate.GATE_STATUSES), "状态闭集逐字不变（Spec §C5）")
        self.assertEqual("packaging-parts-gate/1", gate.GATE_VERSION,
                         "gate_version 逐字不变（Spec §C5）")

    def test_c2_manual_item_is_never_auto_ok(self):
        plain = {row["id"]: row for row in gate.run_gate("local", {})["items"]}
        self.assertEqual("manual_unacknowledged", plain["parts_demo_script"]["status"],
                         "人工项不签字绝不许是 ok（Spec §C5）")
        acked = {row["id"]: row for row in gate.run_gate("local",
                                                         {"parts_demo_script": "tester"})["items"]}
        self.assertEqual("acknowledged", acked["parts_demo_script"]["status"],
                         "显式 --ack 才转 acknowledged（Spec §C5）")
        self.assertEqual("tester", acked["parts_demo_script"].get("acknowledged_by"),
                         "签字人逐字带出（Spec §C5）")

    def test_c3_gate_stays_read_only(self):
        source = GATE_PY.read_text(encoding="utf-8")
        for banned in ("psycopg", "postgresql://", "da_db", "requests.post",
                       "urllib.request.urlopen", "open(", "write_text"):
            self.assertNotIn(banned, source, "门禁只读纪律不变（Spec §C5）：出现 %s" % banned)

    def test_c4_unknown_ack_exit_code_is_two(self):
        proc = subprocess.run([sys.executable, str(GATE_PY), "--env", "local", "--ack", "nope=x"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(2, proc.returncode, "未知 --ack 项 → 退出码 2（Spec §C5）")
        self.assertIn("unknown_ack_item", proc.stdout, "错误体逐字不变（Spec §C5）")


if __name__ == "__main__":
    unittest.main()
