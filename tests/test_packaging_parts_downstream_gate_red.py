"""红测：下游闭环的验收、门禁与能力声明（第 5 层）。

Spec：`docs/specs/packaging-parts-downstream-acceptance.md`
前置：假设第 1～4 层已实现。

**现状缺口**：这条链路已经三次出现"测试全绿但能力不成立"——
`aria-label="3D 视图"` 按名字断言（DWG 下画布其实是空的）、64 行零件实测只有 27 种尺寸且闭合数 0、
工艺推荐与左栏 64 行自相矛盾。没有门槛，"做完"永远只是感觉。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_parts  # noqa: E402

GATE_PY = ROOT / "tech_app" / "tools" / "packaging_parts_gate.py"
DEPLOYMENT_MD = ROOT / "DEPLOYMENT.md"
DEPLOY_SH = ROOT / "scripts" / "deploy_34_bare.sh"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
VENV_PY = ROOT / "open-claude" / ".venv" / "bin" / "python"

GATE_IDS = ("parts_outline_engine", "parts_outline_real_sample", "parts_panel_wired",
            "parts_downstream_wired", "parts_3d_wired", "parts_demo_script")
METRICS = ("closed_ratio", "role_known_ratio", "solid_ok_ratio", "processable_ratio",
           "size_source_mix")


def summarize(doc):
    return packaging_parts.summarize(doc)


# --------------------------------------------------------------------------- #
# A 组：指标固化
# --------------------------------------------------------------------------- #
class AMetrics(unittest.TestCase):
    def test_a1_summary_carries_all_five_metrics(self):
        doc = {"parts": [], "stats": {"part_total": 0}}
        got = summarize(doc)
        for key in METRICS:
            self.assertIn(key, got, "summarize() 缺指标 %s" % key)

    def test_a2_zero_total_is_zero_not_error(self):
        got = summarize({"parts": [], "stats": {"part_total": 0}})
        for key in METRICS:
            if key == "size_source_mix":
                continue
            self.assertEqual(float(got[key]), 0.0, "%s 在 part_total=0 时必须是 0.0" % key)

    def test_a3_ratios_match_their_definitions(self):
        doc = {"parts": [{"part_code": "DWG-P01", "outline_status": "closed", "role": "cut",
                          "size_source": "closed_outline"},
                         {"part_code": "DWG-P02", "outline_status": "open", "role": "unknown",
                          "size_source": "component_bbox"}],
               "stats": {"part_total": 2}}
        got = summarize(doc)
        self.assertAlmostEqual(float(got["closed_ratio"]), 0.5, places=6)
        self.assertAlmostEqual(float(got["role_known_ratio"]), 0.5, places=6)
        self.assertEqual(got["size_source_mix"].get("closed_outline"), 1)
        self.assertEqual(got["size_source_mix"].get("component_bbox"), 1)


# --------------------------------------------------------------------------- #
# B 组：门禁脚本
# --------------------------------------------------------------------------- #
class BGateScript(unittest.TestCase):
    def test_b1_script_exists(self):
        self.assertTrue(GATE_PY.exists(),
                        "缺少 tech_app/tools/packaging_parts_gate.py（Spec §4）")

    def test_b2_runs_and_emits_the_same_shape_as_dwg_gate(self):
        if not GATE_PY.exists():
            self.fail("门禁脚本不存在（Spec §4）")
        proc = subprocess.run([str(VENV_PY), str(GATE_PY), "--env", "local"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        self.assertIn(proc.returncode, (0, 1), "退出码只能是 0/1：stderr=%s" % proc.stderr[-400:])
        payload = json.loads(proc.stdout)
        for key in ("gate_version", "env", "items", "summary", "verdict", "reasons"):
            self.assertIn(key, payload, "门禁输出缺 %s（要与 dwg_deploy_gate 同形状）" % key)
        for key in ("ok", "fail", "manual", "skip"):
            self.assertIn(key, payload["summary"], "summary 缺 %s" % key)

    def test_b3_gate_ids_are_frozen(self):
        source = GATE_PY.read_text(encoding="utf-8", errors="replace") if GATE_PY.exists() else ""
        for item_id in GATE_IDS:
            self.assertIn(item_id, source, "门禁项 %s 必须在（id 不许改名）" % item_id)

    def test_b4_fail_means_nonzero_exit(self):
        if not GATE_PY.exists():
            self.fail("门禁脚本不存在（Spec §4）")
        proc = subprocess.run([str(VENV_PY), str(GATE_PY), "--env", "local"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        payload = json.loads(proc.stdout)
        if payload["summary"]["fail"]:
            self.assertNotEqual(proc.returncode, 0, "有 fail 就必须非零退出")
        else:
            self.assertEqual(payload["verdict"], "go", "无 fail 且无待签字项时 verdict 必须是 go")


# --------------------------------------------------------------------------- #
# C 组：门禁只读
# --------------------------------------------------------------------------- #
class CGateIsReadOnly(unittest.TestCase):
    def test_c1_no_production_access_in_gate(self):
        if not GATE_PY.exists():
            self.fail("门禁脚本不存在（Spec §4）")
        source = GATE_PY.read_text(encoding="utf-8", errors="replace")
        for banned in ("psycopg", "postgresql://", "da_db", "requests.post", "urllib.request.urlopen"):
            self.assertNotIn(banned, source,
                             "门禁必须只读：不许出现 %s（Spec §4）" % banned)

    def test_c2_no_writes(self):
        if not GATE_PY.exists():
            self.fail("门禁脚本不存在（Spec §4）")
        source = GATE_PY.read_text(encoding="utf-8", errors="replace")
        for banned in ("open(", "write_text", "save_parts", "put_doc"):
            self.assertNotIn(banned, source, "门禁不许写任何东西（出现 %s）" % banned)


# --------------------------------------------------------------------------- #
# D 组：能力声明
# --------------------------------------------------------------------------- #
class DClaimLevels(unittest.TestCase):
    def test_d1_three_levels_documented(self):
        text = DEPLOYMENT_MD.read_text(encoding="utf-8", errors="replace")
        for token in ("L1", "L2", "L3"):
            self.assertIn(token, text, "DEPLOYMENT.md 必须写清 %s 级能力声明（Spec §5）" % token)
        self.assertIn("闭环", text, "L3 的定义要写出来")
        self.assertIn("未签字", text, "未签字前不得声明 L3，必须写明")

    def test_d2_current_level_is_stated(self):
        text = DEPLOYMENT_MD.read_text(encoding="utf-8", errors="replace")
        self.assertRegex(text, r"当前(能力)?级别",
                         "DEPLOYMENT.md 必须显眼写出当前级别（Spec §5）")


# --------------------------------------------------------------------------- #
# E 组：部署自检
# --------------------------------------------------------------------------- #
class EDeploySelfCheck(unittest.TestCase):
    def test_e1_downstream_step_present(self):
        text = DEPLOY_SH.read_text(encoding="utf-8", errors="replace")
        self.assertIn("packaging-parts", text, "部署自检必须打零件文档与单件详情")
        self.assertIn("下游", text, "要有一步『下游连通自检』（Spec §6）")

    def test_e2_skip_branch_when_project_unknown(self):
        text = DEPLOY_SH.read_text(encoding="utf-8", errors="replace")
        marker = text.find("下游")
        self.assertGreater(marker, 0)
        window = text[marker:marker + 3000]
        self.assertIn("skip", window,
                      "样本项目 id 未提供时必须 skip 并打印原因，不许自己猜项目（Spec §6）")


# --------------------------------------------------------------------------- #
# F 组：真实样本门槛
# --------------------------------------------------------------------------- #
class FRealSampleThresholds(unittest.TestCase):
    def _doc(self, filename: str):
        sample = SAMPLES_DIR / filename
        if not sample.exists():
            self.skipTest("真实样本不在本机：%s" % sample)
        tool = shutil.which("dwg2dxf")
        if not tool:
            self.skipTest("本机没有 libredwg 的 dwg2dxf")
        import hashlib
        import tempfile
        from tech_app.backend.services import cad_ir
        digest = hashlib.sha256(sample.read_bytes()).hexdigest()[:16]
        cache = pathlib.Path(tempfile.mkdtemp()) / ("real_%s.dxf" % digest)
        subprocess.run([tool, "-y", "-o", str(cache), str(sample)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ir = cad_ir.parse_dxf(cache.read_bytes(), filename=cache.name,
                              source={"kind": "dxf_2d", "attachment_name": filename})
        return packaging_parts.extract(ir)

    def test_f1_wine_box_threshold(self):
        doc = self._doc("酒盒.dwg")
        summary = summarize(doc)
        self.assertGreaterEqual(float(summary["closed_ratio"]), 0.10,
                                "酒盒 closed_ratio 门槛 0.10（今天 0）——Spec §3")

    def test_f2_disc_box_threshold(self):
        doc = self._doc("圆盘盒.dwg")
        summary = summarize(doc)
        self.assertGreaterEqual(float(summary["closed_ratio"]), 0.50, "圆盘盒 closed_ratio 门槛 0.50")
        # 2026-09-22：比值分母随 `## 308`（零件文档保留全量件）由 64 变 312，按比值标定的 0.10 假性失败；
        # 分子反而涨了（8 → 9），所以改成**绝对分子地板**（`summarize()` 没有 `role_known_total` 键，
        # 由 `role_known_ratio × part_total` 还原分子，不新增实现键）。见
        # `docs/specs/packaging-parts-list-visibility-and-kinds.md` §6。
        role_known_total = round(float(summary["role_known_ratio"]) * int(summary["part_total"]))
        self.assertGreaterEqual(role_known_total, 8,
                                "圆盘盒 role_known_total 地板 8（Spec `packaging-parts-list-visibility-and-kinds.md` §6）")

    def test_f3_at_least_one_part_is_computable(self):
        for name in ("酒盒.dwg", "圆盘盒.dwg"):
            doc = self._doc(name)
            rows = doc.get("parts") or []
            ready = [row for row in rows
                     if packaging_parts.processability(row).get("ok")]
            self.assertTrue(ready, "%s 至少要有一件能跑工艺（Spec §3）" % name)

    def test_f4_at_least_one_part_can_be_extruded(self):
        solids = __import__("importlib").import_module(
            "tech_app.backend.services.packaging_part_solids")
        for name in ("酒盒.dwg", "圆盘盒.dwg"):
            doc = self._doc(name)
            ok = [row for row in (doc.get("parts") or [])
                  if solids.extrude(row).get("status") == "ok"]
            self.assertTrue(ok, "%s 至少要有一件能挤出 3D（Spec §3）" % name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
