"""红测：DWG 转换器在 34 生产上线（配置口径 / 部署文档 / 上线门禁 / 验收证据链）。

Spec：`docs/specs/dwg-converter-production-rollout.md`
前置（已完成，本批不重复做）：ODA 27.1 与 LibreDWG 0.14 已在本机与 34 安装并完成转换质量
对比；业务/法务已确认 ODA 可用于本 CPQ 生产环境 → **ODA 主用、仅在主失败时回退 LibreDWG**。

现状缺口（实测，不是推断）：

  · `DEPLOYMENT.md` 全文 `grep -E "DWG|转换器|dwg_deploy_gate|dwg_conversion_smoke|dwg_sample_e2e"`
    → **0 命中**：部署文档里没有任何转换器配置段，34 上线时没人给 8010 配 `DWG_CONVERTER_*`，
    这正是「线上 2.1 说 DWG 不能解析、只能退化成看 PNG」的直接原因；
  · `dwg_deploy_gate.py` 的 18 项门禁里**没有**「部署文档已写明转换器配置」这一项，
    漏配无人拦；
  · 代码侧 `cad_converter` 与四个工具都已就绪且本地红测全绿 —— 本批缺的是配置与文档。

纪律：
  · 全部离线：不连服务器、不联网、不真跑转换、不写业务数据、不读取任何密码；
  · 34 的取值只作为**部署文档的断言目标**，不在此执行；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEPLOYMENT_MD = ROOT / "DEPLOYMENT.md"
GATE_PY = ROOT / "tech_app" / "tools" / "dwg_deploy_gate.py"
SMOKE_PY = ROOT / "tech_app" / "tools" / "dwg_conversion_smoke.py"
LOCAL_CLI_PY = (ROOT / "tech_app" / "backend" / "services" / "cad_converter"
                / "adapters" / "local_cli.py")
SAMPLE_E2E_PY = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
REPORT_PY = ROOT / "tech_app" / "tools" / "dwg_acceptance_report.py"

from tech_app.backend.services import cad_converter  # noqa: E402
from tech_app.backend.services.cad_converter import service as cc  # noqa: E402
from tech_app.backend.services.cad_converter.adapters import local_cli  # noqa: E402

# Spec §2：34 的实际取值（文档必须写到这些）。
ODA_BINARY = "/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun"
LIBREDWG_BINARY = "/home/data/cpq-tools/current/bin/dwg2dxf"
XVFB_RUN = "/home/data/cpq-tools/xvfb-user/root/usr/bin/xvfb-run"
VALUE_ENVS = (cc.PROVIDER_ENV, cc.BINARY_ENV, cc.VERSION_ENV, cc.WRAPPER_ENV,
              cc.FALLBACK_PROVIDER_ENV, cc.FALLBACK_BINARY_ENV, cc.FALLBACK_VERSION_ENV)

SECTION_TITLE = "DWG 转换器（包装图纸）"
VERDICT_LINE = "DWG 编排能力完成，真实转换能力未验收"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


class DocMixin:
    """部署文档的断言目标集合（Spec §3）：A 组与 C 组共用同一份口径。"""

    @classmethod
    def setUpClass(cls):
        cls.doc = read(DEPLOYMENT_MD)

    def missing_doc_markers(self) -> list:
        doc = self.doc
        checks = {
            "章节标题": SECTION_TITLE in doc,
            "env 名齐全": all(name in doc for name in VALUE_ENVS),
            "ODA 主用取值": all(token in doc for token in
                            ("oda", ODA_BINARY, "27.1", "ACAD2018", "DXF", "*.dwg")),
            "xvfb wrapper": all(token in doc for token in (cc.WRAPPER_ENV, XVFB_RUN, "xvfb-run")),
            "LibreDWG 回退取值": all(token in doc for token in
                                (cc.FALLBACK_PROVIDER_ENV, "libredwg", LIBREDWG_BINARY)),
            "只主失败才回退": bool(re.search(r"明确失败", doc)),
            "生效方式": all(token in doc for token in ("CPQ_ENV_FILE", "0600", "重启")),
            "运行用户权限": "/home/data/cpq-tools" in doc and bool(re.search(r"可读可执行|可执行", doc)),
            "健康检查期望": all(token in doc for token in
                          ("capability", "provider", "converter_version", "fallback", "available")),
            "上线三件套": all(token in doc for token in
                         ("dwg_conversion_smoke.py", "dwg_sample_e2e.py",
                          "dwg_deploy_gate.py", "--env production")),
            "声明口径原文": VERDICT_LINE in doc,
            "两条硬禁令": bool(re.search(r"不得.{0,12}模型", doc)) and "STEP" in doc,
        }
        return [name for name, ok in checks.items() if not ok]


# --------------------------------------------------------------------------- #
# A. 部署文档必须写清转换器配置（Spec §3）
# --------------------------------------------------------------------------- #
class ADeploymentDoc(DocMixin, unittest.TestCase):
    maxDiff = None

    def _doc_fail(self, label: str) -> None:
        self.fail("DEPLOYMENT.md 缺少「%s」（Spec §3）；缺少的项：%s"
                  % (label, "、".join(self.missing_doc_markers()) or "（无）"))

    def test_a1_doc_has_a_converter_section(self):
        if SECTION_TITLE not in self.doc:
            self._doc_fail("章节标题")

    def test_a2_doc_lists_every_converter_env_name(self):
        for name in VALUE_ENVS:
            if name not in self.doc:
                self.fail("DEPLOYMENT.md 未列出配置名 %s（Spec §2/§3）" % name)

    def test_a3_doc_gives_the_oda_primary_values(self):
        for token in ("oda", ODA_BINARY, "27.1", "ACAD2018", "DXF", "*.dwg"):
            if token not in self.doc:
                self.fail("DEPLOYMENT.md 未写出 ODA 主用取值 %r（Spec §2/§3）" % token)

    def test_a4_doc_gives_the_wrapper_and_xvfb(self):
        for token in (cc.WRAPPER_ENV, XVFB_RUN, "xvfb-run"):
            if token not in self.doc:
                self.fail("DEPLOYMENT.md 未写出 ODA 所需的 xvfb wrapper %r（Spec §2/§3）" % token)

    def test_a5_doc_gives_the_libredwg_fallback_and_its_condition(self):
        for token in (cc.FALLBACK_PROVIDER_ENV, "libredwg", LIBREDWG_BINARY):
            if token not in self.doc:
                self.fail("DEPLOYMENT.md 未写出 LibreDWG 回退取值 %r（Spec §2/§3）" % token)
        if not re.search(r"明确失败", self.doc):
            self.fail("DEPLOYMENT.md 未写明回退只在主转换器「明确失败」时启用（Spec §2/§3）")

    def test_a6_doc_says_how_the_config_takes_effect(self):
        for token in ("CPQ_ENV_FILE", "0600", "重启"):
            if token not in self.doc:
                self.fail("DEPLOYMENT.md 未写明配置生效方式 %r（仓库外 env 文件 0600 + 重启 8010）"
                          % token)

    def test_a7_doc_states_the_service_user_permission(self):
        if "/home/data/cpq-tools" not in self.doc or not re.search(r"可读可执行|可执行", self.doc):
            self.fail("DEPLOYMENT.md 未写明运行用户对 /home/data/cpq-tools 需要可读可执行（Spec §3）")

    def test_a8_doc_gives_health_check_and_expected_fields(self):
        for token in ("capability", "provider", "converter_version", "fallback", "available"):
            if token not in self.doc:
                self.fail("DEPLOYMENT.md 未写出健康检查期望字段 %r（Spec §3.4）" % token)

    def test_a9_doc_gives_the_three_rollout_commands(self):
        for token in ("dwg_conversion_smoke.py", "dwg_sample_e2e.py",
                      "dwg_deploy_gate.py", "--env production"):
            if token not in self.doc:
                self.fail("DEPLOYMENT.md 未写出上线命令 %r（Spec §3.5）" % token)

    def test_a10_doc_carries_claim_wording_and_hard_bans(self):
        if VERDICT_LINE not in self.doc:
            self.fail("DEPLOYMENT.md 未写出判定文案原文「%s」（Spec §3.6）" % VERDICT_LINE)
        if not re.search(r"不得.{0,12}模型", self.doc):
            self.fail("DEPLOYMENT.md 未写明「DWG 原始字节不得发给模型」（Spec §3.7）")
        if "STEP" not in self.doc:
            self.fail("DEPLOYMENT.md 未写明「DWG 不得交给 STEP importer」（Spec §3.7）")


# --------------------------------------------------------------------------- #
# B. 配置口径与代码常量一致（Spec §2）
# --------------------------------------------------------------------------- #
class BConfigShape(unittest.TestCase):
    maxDiff = None

    def test_b1_oda_is_the_first_auto_probe_candidate(self):
        self.assertIn("oda", local_cli.KNOWN_PROVIDERS)
        self.assertIn("libredwg", local_cli.KNOWN_PROVIDERS)
        self.assertEqual(tuple(local_cli.AUTO_PROBE)[0], "oda",
                         "主转换器未固定为 ODA（Spec §2）")

    def test_b2_oda_argv_shape_is_the_verified_seven_args(self):
        driver = local_cli.DRIVERS.get("oda_file_converter")
        self.assertIsNotNone(driver, "缺少 ODA 驱动 oda_file_converter（Spec §2.1）")
        argv = driver["argv"]("EXE", Path("/in/source.dwg"), Path("/out"), Path("/out/source.dxf"))
        self.assertEqual([str(x) for x in argv],
                         ["EXE", "/in", "/out", "ACAD2018", "DXF", "0", "1", "*.dwg"],
                         "ODA 的 7 个位置参数形状被改动（Spec §2.1）")
        self.assertEqual(driver["output_name"], "source.dxf")
        self.assertEqual(driver["output_version"], "ACAD2018")
        self.assertTrue(driver["audit_enabled"], "ODA audit/repair 必须开启（Spec §2.1）")
        self.assertTrue(driver["argv_verified"])

    def test_b3_wrapper_items_go_before_the_executable(self):
        # parse_wrapper 要求首项是存在且可执行的文件（xvb-run 不在本机）→ 用临时可执行文件
        # 验证「按空白切分 + 逐项排在 exe 之前」这条口径本身（Spec §2.2）。
        with tempfile.TemporaryDirectory() as tmp:
            wrapper_exe = pathlib.Path(tmp) / "xvfb-run"
            wrapper_exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper_exe.chmod(0o755)
            items, reason = local_cli.parse_wrapper(str(wrapper_exe) + " -a")
            self.assertEqual(reason, "", "`<wrapper> -a` 形状必须被接受（Spec §2.2）")
            self.assertEqual([str(x) for x in items], [str(wrapper_exe), "-a"])
            self.assertEqual(local_cli.parse_wrapper(str(wrapper_exe) + " | tee")[1],
                             "wrapper_invalid", "shell 元字符必须判非法（Spec §2.2）")
        src = read(LOCAL_CLI_PY)
        self.assertRegex(src, r"list\(self\.wrapper\)\s*\+\s*self\.driver\[.argv.\]",
                         "wrapper 必须逐项排在 exe 之前（Spec §2.2）")

    def test_b4_capability_fails_closed_without_a_converter(self):
        env = {cc.PROVIDER_ENV: "none", cc.FALLBACK_PROVIDER_ENV: "none"}
        with mock.patch.dict(os.environ, env, clear=False):
            cap = cad_converter.capability()
        self.assertFalse(cap["available"])
        self.assertFalse(cap["dwg_supported"])
        self.assertEqual(cap["support_claim"], "orchestration_only",
                         "没装转换器时不得声称支持 DWG（Spec §2.3）")
        self.assertTrue(cap["stable_error_code"])


# --------------------------------------------------------------------------- #
# C. 上线门禁与正确失败（Spec §4 / §6）
# --------------------------------------------------------------------------- #
class CGateAndFailClosed(DocMixin, unittest.TestCase):
    maxDiff = None

    def _gate(self):
        try:
            import importlib
            return importlib.import_module("tech_app.tools.dwg_deploy_gate")
        except Exception as exc:                        # noqa: BLE001 - 红测要原文
            self.fail("无法导入 dwg_deploy_gate：%s: %s" % (type(exc).__name__, exc))

    def test_c1_gate_has_a_documented_rollout_item(self):
        gate = self._gate()
        ids = [item[0] for item in gate.GATE_ITEMS]
        self.assertIn("converter_rollout_documented", ids,
                      "门禁缺少「部署文档已写明转换器配置」这一项（Spec §4）；"
                      "现有 %d 项：%s" % (len(ids), ids))
        self.assertEqual(ids[-1], "converter_rollout_documented",
                         "新增项只能追加在末尾，既有 18 项 id/顺序不得变动（Spec §4）")
        check = dict(gate.AUTO_CHECKS).get("converter_rollout_documented")
        self.assertIsNotNone(check, "converter_rollout_documented 必须挂在 AUTO_CHECKS 上（Spec §4）")
        params = len(inspect.signature(check).parameters)
        row = check(*(["production"] * params))
        expected = "ok" if not self.missing_doc_markers() else "fail"
        self.assertEqual(row["status"], expected,
                         "文档位判定必须与 §3 口径一致（缺段即 fail，不许 skip）：%s" % row)

    def test_c2_production_never_skips_the_converter_version_item(self):
        gate = self._gate()
        env = {cc.PROVIDER_ENV: "none", cc.FALLBACK_PROVIDER_ENV: "none"}
        with mock.patch.dict(os.environ, env, clear=False):
            row = gate._check_converter_version_pinned("production")
        self.assertEqual(row["status"], "skip", "未装转换器时该项按现状给 skip")
        out = subprocess.run([sys.executable, str(GATE_PY), "--env", "production", "--json"],
                             capture_output=True, text=True, cwd=str(ROOT))
        self.assertTrue(out.stdout.strip(), "生产门禁必须能输出 JSON：%s" % out.stderr[-400:])
        payload = json.loads(out.stdout)
        items = {item["id"]: item for item in payload["items"]}
        self.assertIn("converter_version_pinned", items)
        self.assertEqual(items["converter_version_pinned"]["status"], "fail",
                         "production 下转换器版本项不得停留在 skip（Spec §4）")
        self.assertNotEqual(payload["verdict"], "go")

    def test_c3_missing_binary_fails_closed_without_switching_adapter(self):
        env = {cc.PROVIDER_ENV: "oda",
               cc.BINARY_ENV: "/nonexistent/ODAFileConverter",
               cc.VERSION_ENV: "27.1",
               cc.FALLBACK_PROVIDER_ENV: "none"}
        with mock.patch.dict(os.environ, env, clear=False):
            cap = cad_converter.capability()
        self.assertEqual(cap["provider"], "oda", "配了 oda 就不得静默换成别的适配器（Spec §6.2）")
        self.assertFalse(cap["available"])
        self.assertTrue(cap["stable_error_code"], "二进制缺失必须给出稳定错误码（Spec §6.1）")
        self.assertNotIn("已安装", cap["message"])
        self.assertFalse(cap["fallback"]["enabled"], "未显式配置回退时不得启用回退（Spec §2.3）")


# --------------------------------------------------------------------------- #
# D. 验收证据链（Spec §5）
# --------------------------------------------------------------------------- #
class DEvidenceChain(unittest.TestCase):
    maxDiff = None

    def test_d1_simulated_output_can_never_pass_layer_b(self):
        src = read(SMOKE_PY)
        self.assertTrue(src, "缺少 tech_app/tools/dwg_conversion_smoke.py")
        self.assertIn("is_simulated", src)
        self.assertIn("acceptance_level", src)
        self.assertRegex(src, r"is_simulated[^\n]{0,80}acceptance_level",
                         "fake 产物必须被判 B 层不通过（Spec §5）")

    def test_d2_sample_e2e_reports_hash_provider_and_version(self):
        src = read(SAMPLE_E2E_PY)
        self.assertTrue(src, "缺少 tech_app/tools/dwg_sample_e2e.py")
        for token in ("sha256", "converter_name", "converter_version", "converter_role"):
            self.assertIn(token, src, "样本 E2E 证据缺少 %s（Spec §5）" % token)

    def test_d3_writing_an_acceptance_record_requires_an_approver(self):
        out = subprocess.run([sys.executable, str(REPORT_PY), "--write-record"],
                             capture_output=True, text=True, cwd=str(ROOT))
        self.assertNotEqual(out.returncode, 0, "没有审批人时不得写验收记录（Spec §5）")
        self.assertIn("拒绝写入", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
