"""红测（L4）：两份真实 DWG 的端到端验收 —— DWG 支持第 6 批。

Spec：`docs/specs/dwg-final-acceptance.md`（§5 契约 E、§10 判定）

默认**不跑**：只有同时满足下列条件才执行真实转换与金标比对

  1. `CPQ_DWG_REAL_SAMPLES=1`（显式点名要跑真实样本）；
  2. 本机/本容器有可用的真实转换器（`cad_converter.capability().available` 为真）；
  3. 两份真实样本在本机（默认 `<仓库根>/裕同包装项目-待开发/酒盒.dwg`、`圆盘盒.dwg`，
     可用 `DWG_ACCEPTANCE_SAMPLES_DIR` 覆盖）。

缺任何一条都必须 `skipTest` 并**在原因里点名缺什么**：静默跳过等于把"没跑过"包装成"通过"。
L1/L2/L3 全绿只能写"编排层通过"，不许写"真实转换能力已验收"（Spec §5）。

纪律：两份样本**只读**、不入库；产物只写临时目录；不改服务器配置、不写真实 `tech_data`。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"
SAMPLES = (WINE_BOX, ROUND_BOX)
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "dwg_acceptance"
REPORT_TOOL = ROOT / "tech_app" / "tools" / "dwg_acceptance_report.py"
SAMPLE_TOOL = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
MIN_PREVIEW_BYTES = 512
MIN_DXF_BYTES = 4096


def samples_dir() -> pathlib.Path:
    override = os.environ.get("DWG_ACCEPTANCE_SAMPLES_DIR")
    return pathlib.Path(override) if override else ROOT / "裕同包装项目-待开发"


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class RealSampleCase(unittest.TestCase):
    maxDiff = None

    def require(self, *checks):
        """`checks` 是 (条件, 缺失原因) 序列；缺什么必须在 skip 原因里点名。"""
        for ok, reason in checks:
            if not ok:
                self.skipTest(reason)

    def converter_capability(self):
        try:
            package = importlib.import_module("tech_app.backend.services.cad_converter")
        except Exception as exc:                            # noqa: BLE001
            return None, "转换器适配层不可导入：%s: %s" % (type(exc).__name__, exc)
        try:
            return package.capability(), ""
        except Exception as exc:                            # noqa: BLE001
            return None, "转换器能力查询失败：%s: %s" % (type(exc).__name__, exc)

    def preflight(self):
        self.require(
            (os.environ.get("CPQ_DWG_REAL_SAMPLES") == "1",
             "未设置 CPQ_DWG_REAL_SAMPLES=1：L4 真实样本 E2E 默认不跑（Spec §5）"),
        )
        capability, error = self.converter_capability()
        self.require(
            (bool(capability) and bool(capability.get("available")),
             "没有真实转换器：%s" % (error or "capability().available 为假")),
        )
        missing = [name for name in SAMPLES if not (samples_dir() / name).is_file()]
        self.require(
            (not missing, "样本不在本机（%s）：缺少 %s" % (samples_dir(), "、".join(missing))),
        )
        return capability

    def golden(self, filename):
        if not GOLDEN_DIR.is_dir():
            self.fail("缺少 tests/fixtures/dwg_acceptance/（Spec §4）：L4 没有金标可比")
        versions = [item for item in sorted(GOLDEN_DIR.iterdir()) if item.is_dir()]
        if not versions:
            self.fail("金标目录下没有 <golden_version>/（Spec §4.1）")
        path = versions[-1] / (filename.replace(".dwg", "") + ".json")
        if not path.is_file():
            self.fail("缺少样本金标 %s（Spec §4.1）" % path)
        return versions[-1].name, json.loads(path.read_text(encoding="utf-8"))

    def convert(self, capability):
        """用工具脚本做受控转换（只读样本、只写临时目录）。"""
        if not SAMPLE_TOOL.is_file():
            self.fail("缺少 tech_app/tools/dwg_sample_e2e.py（Spec §5/§13）")
        out = pathlib.Path(tempfile.mkdtemp(prefix="dwg-l4-"))
        self.addCleanup(shutil.rmtree, out, True)
        results = {}
        for name in SAMPLES:
            completed = subprocess.run(
                [sys.executable, str(SAMPLE_TOOL), "--sample", str(samples_dir() / name),
                 "--out", str(out / name.replace(".dwg", "")), "--json"],
                capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
            payload = {}
            text = (completed.stdout or "").strip()
            if text:
                try:
                    payload = json.loads(text[text.find("{"):])
                except ValueError:
                    payload = {}
            payload.setdefault("returncode", completed.returncode)
            payload.setdefault("stderr_tail", (completed.stderr or "")[-400:])
            results[name] = payload
        return out, results

    # ------------------------------------------------------------------ 用例
    def test_l4_1_samples_are_recognised_as_dwg(self):
        self.package_preflight = self.preflight()
        from tech_app.backend.services import file_preflight
        for name in SAMPLES:
            path = samples_dir() / name
            detected = file_preflight.detect_file_format(name, path.read_bytes())
            self.assertEqual(str(detected.get("detected_format") or "").lower(), "dwg",
                             "%s 必须被识别为 DWG（Spec §5 L1）" % name)
            self.assertTrue(str(detected.get("dwg_version") or "").startswith("AC"),
                            "%s 必须能读出 DWG 版本图标（Spec §5 L1）" % name)

    def test_l4_2_converter_is_real_and_version_pinned(self):
        capability = self.preflight()
        self.assertFalse(capability.get("simulated"),
                         "L4 必须用真实转换器，不许用 fake（Spec §5 L2）")
        self.assertTrue(str(capability.get("converter_version") or "").strip(),
                        "必须能读出转换器版本，版本固定才可复现（Spec §7）")
        self.assertTrue(bool(capability.get("version_ok")),
                        "转换器版本必须与固定版本一致（Spec §7）")

    def test_l4_3_both_samples_convert_to_non_empty_dxf(self):
        capability = self.preflight()
        out, results = self.convert(capability)
        for name in SAMPLES:
            payload = results[name]
            self.assertEqual(payload.get("returncode"), 0,
                             "%s 真实转换必须成功：%s" % (name, payload.get("stderr_tail")))
            dxf = pathlib.Path(str(payload.get("dxf_path") or ""))
            self.assertTrue(dxf.is_file(), "%s 必须产出 DXF（Spec §10）" % name)
            self.assertGreaterEqual(dxf.stat().st_size, MIN_DXF_BYTES,
                                    "%s 的 DXF 明显过小，不能当成功（Spec §10）" % name)
            self.assertIn(payload.get("status"), ("success", "success_with_warnings"),
                          "%s 的转换状态必须是成功类（Spec §10）：%r"
                          % (name, payload.get("status")))

    def test_l4_4_preview_is_produced_and_not_blank(self):
        capability = self.preflight()
        out, results = self.convert(capability)
        for name in SAMPLES:
            payload = results[name]
            previews = [pathlib.Path(str(item)) for item in
                        (payload.get("preview_paths") or [])]
            self.assertTrue(previews, "%s 必须产出预览产物（Spec §10）" % name)
            biggest = max(previews, key=lambda item: item.stat().st_size)
            self.assertGreaterEqual(biggest.stat().st_size, MIN_PREVIEW_BYTES,
                                    "%s 的预览接近空白，不能算视觉可核对（Spec §10）" % name)

    def test_l4_5_artifact_hashes_match_the_manifest(self):
        capability = self.preflight()
        out, results = self.convert(capability)
        for name in SAMPLES:
            payload = results[name]
            outputs = list(payload.get("output_files") or [])
            self.assertTrue(outputs, "%s 的 manifest 必须列出产物（Spec §2）" % name)
            for item in outputs:
                path = pathlib.Path(str(item.get("path") or ""))
                self.assertTrue(path.is_file(), "%s 的产物 %r 不存在" % (name, str(path)))
                self.assertEqual(sha256_file(path), str(item.get("sha256") or ""),
                                 "%s 的产物 sha256 必须可校验（Spec §3.1）" % name)

    def test_l4_6_baseline_comparison_covers_identity_and_3d_status(self):
        capability = self.preflight()
        out, results = self.convert(capability)
        for name in SAMPLES:
            version, golden = self.golden(name)
            payload = results[name]
            self.assertEqual(sha256_file(samples_dir() / name),
                             str(golden.get("source_sha256") or ""),
                             "%s 与金标 %s 不是同一份文件（Spec §4.1）" % (name, version))
            if golden.get("detected_dwg_version"):
                self.assertEqual(str(payload.get("detected_dwg_version") or ""),
                                 str(golden["detected_dwg_version"]),
                                 "%s 的 DWG 版本与金标不一致（Spec §4.1）" % name)
            self.assertEqual(str(payload.get("three_d_status") or ""),
                             str(golden.get("three_d_status") or ""),
                             "%s 的三维状态与金标不一致（Spec §4.1/§5）" % name)

    def test_l4_7_layer_and_entity_statistics_are_not_zero(self):
        capability = self.preflight()
        out, results = self.convert(capability)
        for name in SAMPLES:
            version, golden = self.golden(name)
            stats = dict(results[name].get("stats") or {})
            self.assertGreater(int(stats.get("entity_total") or 0), 0,
                               "%s 的实体数为 0：空白页不算转换成功（Spec §10）" % name)
            self.assertGreater(int(stats.get("layer_total") or 0), 0,
                               "%s 的图层数为 0（Spec §10）" % name)
            if int(golden.get("stats", {}).get("entity_total") or 0) > 0:
                self.assertLess(
                    abs(int(stats["entity_total"])
                        - int(golden["stats"]["entity_total"])),
                    max(50, int(golden["stats"]["entity_total"]) // 10),
                    "%s 的实体数与金标 %s 差得太多，必须人工复核（Spec §4.2）"
                    % (name, version))

    def test_l4_8_baseline_must_be_human_approved(self):
        self.preflight()
        for name in SAMPLES:
            version, golden = self.golden(name)
            approval = dict(golden.get("approval") or {})
            self.assertTrue(str(approval.get("approved_by") or "").strip(),
                            "%s 的金标 %s 没有审批人：不许用未审批金标验收（Spec §4.2）"
                            % (name, version))
            self.assertTrue(str(approval.get("approved_at") or "").strip(),
                            "%s 的金标 %s 没有审批时间（Spec §4.2）" % (name, version))

    def test_l4_9_support_claim_is_consistent_with_the_evidence(self):
        capability = self.preflight()
        if not REPORT_TOOL.is_file():
            self.fail("缺少 tech_app/tools/dwg_acceptance_report.py（Spec §4.2/§13）")
        completed = subprocess.run([sys.executable, str(REPORT_TOOL), "--verify", "--json"],
                                   capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        package = importlib.import_module("tech_app.backend.services.cad_converter")
        live = package.capability()
        self.assertEqual(completed.returncode == 0, live["support_claim"] == "supported",
                         "support_claim 必须与验收校验结果一致（Spec §3.2/§10.2）：rc=%s "
                         "claim=%r" % (completed.returncode, live["support_claim"]))
        if completed.returncode != 0:
            self.assertFalse(live["dwg_supported"],
                             "验收校验没过就绝不许 dwg_supported 为真（Spec §10.2）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
