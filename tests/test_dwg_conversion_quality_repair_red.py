"""红测：DWG 前两批修复 —— LibreDWG 0.14 接入后的配置、质量门槛与转换状态。

Spec：`docs/specs/dwg-conversion-quality-repair.md`
前置：第 1 批 `docs/specs/dwg-file-capability-preflight.md`（错误码闭集是唯一权威）、
      第 2 批 `docs/specs/dwg-controlled-conversion-adapter.md`（转换层安全规则）。

本文件只覆盖「前两批的修复」，不重复第 2 批已有的安全/幂等/并发用例。

分组：
  A 配置契约（provider/binary/version/预览二进制/新错误码）· B 驱动 argv ·
  C 产物质量门槛（不许只看退出码）· D 诊断计数与状态机 · E 真实二进制（否 则 skip）·
  F 审计与用户可见文案

首次运行时**必须失败**（当前实现的实际缺口）：
  · `DWG_CONVERTER_PROVIDER/BINARY/VERSION/PREVIEW_BINARY` 四个配置项都不存在（只认 `CAD_CONVERTER`）；
  · `capability()` 没有 provider/binary/expected_version/version_ok/argv_verified/preview_available；
  · manifest 没有 `quality` / `warning_count` / `error_count` / `warning_codes` / `diagnostics_*`；
  · 状态只有 `ok` / `failed`，没有 `success_with_warnings`——所以「圆盘盒有 3 条位流错误」也报 ok；
  · 第 1 批闭集里还没有 `DWG_CONVERTER_BINARY_UNUSABLE`。

夹具策略（Spec §9）：小规模用**假 CLI 脚本**验判定逻辑（可注入警告/错误/空输出/截断/零实体），
真实样本只用真二进制验兼容性；两类不得混为一类。假脚本模拟 LibreDWG 0.14 的 argv 形状
（`-y -o <out> <src>`），给错形状（例如 ODA 的 `<src> <dir>`）就退出非 0。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.cad_converter"
PERSISTENCE = PKG + ".persistence"
PREFLIGHT = "tech_app.backend.services.file_preflight"
STORE = "tech_app.backend.storage.store"
FIXTURES = ROOT / "tests" / "fixtures" / "dxf"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = SAMPLES_DIR / "酒盒.dwg"
ROUND_BOX = SAMPLES_DIR / "圆盘盒.dwg"

#: 本批新增的稳定错误码（必须并入第 1 批 Spec §3 的权威闭集）
NEW_CODES = {"DWG_CONVERTER_BINARY_UNUSABLE": (500, False)}
#: 状态闭集（Spec §5）
STATUSES = {"ok", "success_with_warnings", "failed"}

#: 合成 DWG：`AC1015` 头 + 足够长度，能被第 1 批预检识别成 dwg（真样本不入库）
SYNTHETIC_DWG = b"AC1015" + b"\x00" * 200

#: 确定性环境：先清掉所有转换器相关变量，再按用例注入（绝不读宿主环境）
CONVERTER_KEYS = (
    "CAD_CONVERTER", "CAD_CONVERTER_ALLOW_SIMULATED", "CAD_CONVERTER_TIMEOUT_SECONDS",
    "CAD_CONVERTER_MAX_OUTPUT_BYTES", "CAD_CONVERTER_MAX_OUTPUT_FILES",
    "DWG_CONVERTER_PROVIDER", "DWG_CONVERTER_BINARY", "DWG_CONVERTER_VERSION",
    "DWG_CONVERTER_PREVIEW_BINARY",
)

#: 假转换器脚本：形状与诊断都可注入。占位符用 replace 填充，避免 shell 转义地狱。
FAKE_CLI = """#!/bin/sh
# 假转换器（CI 用）：只认 LibreDWG 0.14 的形状 `-y -o <out> <src>`，其余一律退出非 0。
if [ "$1" = "--version" ]; then echo "dwg2dxf 0.14"; exit 0; fi
SIDECAR="__SIDECAR__"
: > "$SIDECAR"
for arg in "$@"; do printf '%s\\n' "$arg" >> "$SIDECAR"; done
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    -y) shift ;;
    -o) OUT="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -z "$OUT" ]; then
  echo "ERROR: usage: dwg2dxf -y -o out.dxf source.dwg" >&2
  exit 1
fi
i=0
while [ $i -lt __WARN__ ]; do
  echo "Warning: Object handle not found 29691/0x73FB in 15802 objects of max 0x7C71 handles" >&2
  i=$((i+1))
done
i=0
while [ $i -lt __DISTINCT__ ]; do
  echo "Warning: issue K$i detected" >&2
  i=$((i+1))
done
i=0
while [ $i -lt __ERR__ ]; do
  echo "ERROR: bit_read_BD: unexpected 2-bit code: '11'" >&2
  i=$((i+1))
done
case "${FAKE_DXF_MODE:-tidy}" in
  tidy) cp "$FAKE_DXF_PAYLOAD" "$OUT" ;;
  empty) : > "$OUT" ;;
  truncated) printf '0\\nSECTION\\n2\\nENTITIES\\n0\\nLWPOLYLINE\\n90\\n4\\n10\\n0.0\\n' > "$OUT" ;;
  no_entities) printf '__NOENT__' > "$OUT" ;;
esac
exit 0
"""

#: 合法但 0 实体的 DXF（用于「结构完整但没实体」这一档）
NO_ENTITIES_DXF = (
    "0\\nSECTION\\n2\\nHEADER\\n9\\n$ACADVER\\n1\\nAC1015\\n0\\nENDSEC\\n"
    "0\\nSECTION\\n2\\nTABLES\\n0\\nTABLE\\n2\\nLAYER\\n70\\n1\\n0\\nLAYER\\n2\\n0\\n70\\n0\\n62\\n7\\n"
    "6\\nCONTINUOUS\\n0\\nENDTAB\\n0\\nENDSEC\\n0\\nSECTION\\n2\\nENTITIES\\n0\\nENDSEC\\n0\\nEOF\\n"
)


class RepairCase(unittest.TestCase):
    """公共脚手架：懒加载被测包、确定性环境、假 CLI、内存版 persistence。"""

    # ---------------------------------------------------------------- 包加载
    def pkg(self):
        try:
            return importlib.import_module(PKG)
        except ModuleNotFoundError as exc:
            if str(getattr(exc, "name", "") or "").endswith("file_preflight"):
                self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现）")
            self.fail("缺少 tech_app/backend/services/cad_converter/（第 2 批 Spec §2）")

    def module(self, name):
        self.pkg()
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            self.fail("缺少 %s（第 2 批 Spec §2）" % name)

    def preflight(self):
        try:
            return importlib.import_module(PREFLIGHT)
        except ModuleNotFoundError:
            self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现）")

    # ---------------------------------------------------------------- 环境
    def use_env(self, **overrides):
        """先清掉所有转换器相关变量，再注入本次的值（绝不读宿主环境）。"""
        saved = {key: os.environ.get(key) for key in CONVERTER_KEYS}

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)
        for key in CONVERTER_KEYS:
            os.environ.pop(key, None)
        for key, value in overrides.items():
            os.environ[key] = str(value)
        return dict(overrides)

    def capability(self, **overrides):
        self.use_env(**overrides)
        fn = getattr(self.pkg(), "capability", None)
        self.assertTrue(callable(fn), "cad_converter 必须导出 capability()")
        return fn()

    def convert(self, content=SYNTHETIC_DWG, filename="酒盒.dwg", **kwargs):
        fn = getattr(self.pkg(), "convert_drawing", None)
        self.assertTrue(callable(fn), "cad_converter 必须导出 convert_drawing()")
        return fn("repair-project", filename, content, **kwargs)

    def expect_error(self, code, fn, *args, **kwargs):
        preflight = self.preflight()
        table = getattr(preflight, "STABLE_ERROR_CODES", {})
        spec = table.get(code) or {}
        try:
            result = fn(*args, **kwargs)
        except preflight.FileCapabilityError as exc:
            self.assertEqual(exc.stable_error_code, code, "期望 %s，实际 %s" % (code, exc.stable_error_code))
            self.assertIn(code, table, "%s 必须已在第 1 批 Spec §3 闭集里（本批新增的码要并入）" % code)
            self.assertEqual(exc.http_status, spec.get("http_status"), code)
            self.assertEqual(exc.retryable, spec.get("retryable"), code)
            self.assertTrue(exc.message, "%s 必须有中文文案" % code)
            return exc
        except BaseException as exc:  # noqa: BLE001 - 只做分类
            self.fail("期望 %s，实际抛出 %s: %s" % (code, type(exc).__name__, exc))
        self.fail("期望 %s，实际成功返回：%r" % (code, result))

    # ---------------------------------------------------------------- persistence
    def memory_persistence(self):
        """内存 + 临时目录替换 persistence：不写真实 tech_data、不碰历史项目。"""
        persistence = self.module(PERSISTENCE)
        root = pathlib.Path(tempfile.mkdtemp(prefix="dwg-repair-test-"))
        self.addCleanup(shutil.rmtree, root, True)
        state = {"root": root, "manifests": {}, "audits": [], "seq": 0}
        lock = threading.Lock()

        def artifact_dir(project_id, conversion_id):
            target = root / str(project_id) / str(conversion_id)
            target.mkdir(parents=True, exist_ok=True)
            return target

        def save_artifact(project_id, conversion_id, filename, data):
            target = artifact_dir(project_id, conversion_id) / pathlib.Path(str(filename)).name
            target.write_bytes(data)
            return {"filename": target.name, "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data)}

        def save_manifest(project_id, manifest):
            with lock:
                state["seq"] += 1
                item = dict(manifest)
                state["manifests"][(str(project_id), str(manifest.get("conversion_id")))] = item
            return dict(item)

        def load_manifest(project_id, conversion_id):
            with lock:
                item = state["manifests"].get((str(project_id), str(conversion_id)))
            return dict(item) if item else None

        def list_manifests(project_id):
            with lock:
                return [dict(v) for (pid, _cid), v in state["manifests"].items()
                        if pid == str(project_id)]

        def audit(project_id, action, detail=None):
            state["audits"].append({"project_id": project_id, "action": action, "detail": detail})

        replacements = {"artifact_dir": artifact_dir, "save_artifact": save_artifact,
                        "save_manifest": save_manifest, "load_manifest": load_manifest,
                        "list_manifests": list_manifests, "audit": audit,
                        "sync": lambda *a, **k: None}
        for name, fn in replacements.items():
            if hasattr(persistence, name):
                original = getattr(persistence, name)
                setattr(persistence, name, fn)
                self.addCleanup(setattr, persistence, name, original)
        return state

    # ---------------------------------------------------------------- 假转换器
    def fake_cli(self, name="dwg2dxf", mode="tidy", warnings=0, errors=0, distinct=0):
        directory = pathlib.Path(tempfile.mkdtemp(prefix="dwg-fake-cli-"))
        self.addCleanup(shutil.rmtree, directory, True)
        sidecar = directory / "argv.txt"
        payload = FIXTURES / "rect_10x5.dxf"
        self.assertTrue(payload.exists(), "需要夹具 %s" % payload.name)
        text = (FAKE_CLI.replace("__SIDECAR__", str(sidecar))
                .replace("__WARN__", str(int(warnings)))
                .replace("__DISTINCT__", str(int(distinct)))
                .replace("__ERR__", str(int(errors)))
                .replace("__NOENT__", NO_ENTITIES_DXF))
        script = directory / name
        script.write_text(text, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.environ["FAKE_DXF_PAYLOAD"] = str(payload)
        os.environ["FAKE_DXF_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE_DXF_PAYLOAD", None)
        self.addCleanup(os.environ.pop, "FAKE_DXF_MODE", None)
        return script

    def fake_svg(self):
        """假 dwg2SVG：只把 SVG 写到 stdout（真实工具就是这么用的，没有 -o）。"""
        directory = pathlib.Path(tempfile.mkdtemp(prefix="dwg-fake-svg-"))
        self.addCleanup(shutil.rmtree, directory, True)
        script = directory / "dwg2SVG"
        script.write_text('#!/bin/sh\necho \'<svg xmlns="http://www.w3.org/2000/svg">'
                          '<path d="M0 0 L10 10"/></svg>\'\n', encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return script

    def fake_argv(self, script):
        sidecar = script.parent / "argv.txt"
        if not sidecar.exists():
            self.fail("假转换器没有被调用（没有 argv 记录）")
        return [line for line in sidecar.read_text(encoding="utf-8").splitlines() if line]

    def artifact_path(self, state, manifest, item):
        return (state["root"] / str(manifest["project_id"])
                / str(manifest["conversion_id"]) / str(item["filename"]))

    # ---------------------------------------------------------------- 真二进制
    def real_binary(self):
        return os.environ.get("DWG_CONVERTER_BINARY") or shutil.which("dwg2dxf") or ""

    def real_preview(self):
        return os.environ.get("DWG_CONVERTER_PREVIEW_BINARY") or shutil.which("dwg2SVG") or ""

    def libredwg_env(self):
        env = {"DWG_CONVERTER_PROVIDER": "libredwg",
               "DWG_CONVERTER_BINARY": self.real_binary(),
               "DWG_CONVERTER_VERSION": "0.14"}
        preview = self.real_preview()
        if preview:
            env["DWG_CONVERTER_PREVIEW_BINARY"] = preview
        return env


# --------------------------------------------------------------------------- #
# A. 配置契约（provider / binary / version / 预览 / 新错误码）
# --------------------------------------------------------------------------- #
class AConfiguration(RepairCase):
    def test_a1_provider_binary_and_version_are_honoured(self):
        script = self.fake_cli()
        cap = self.capability(DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY=str(script),
                              DWG_CONVERTER_VERSION="0.14")
        self.assertTrue(cap.get("available"), cap)
        self.assertEqual(cap.get("provider"), "libredwg")
        self.assertEqual(cap.get("binary"), str(script))
        self.assertEqual(cap.get("converter_version"), "0.14")
        self.assertEqual(cap.get("expected_version"), "0.14")
        self.assertTrue(cap.get("version_ok"))
        self.assertTrue(cap.get("argv_verified"), "libredwg 驱动已按 0.14 实测，必须为 true")
        self.assertFalse(cap.get("simulated"))
        self.assertFalse(cap.get("dwg_supported"),
                         "接入真转换器不等于「支持 DWG」（Spec §2，第 6 批才判）")

    def test_a2_absolute_binary_does_not_need_to_be_on_path(self):
        self.memory_persistence()
        script = self.fake_cli()
        self.assertNotIn(str(script.parent), os.environ.get("PATH", ""))
        self.use_env(DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY=str(script),
                     DWG_CONVERTER_VERSION="0.14")
        manifest = self.convert()
        self.assertIn(manifest.get("status"), STATUSES)
        self.assertEqual(len(self.fake_argv(script)), 4,
                         "必须直接调用配置的绝对路径（不在 PATH 里也要能用）")

    def test_a3_missing_binary_uses_the_new_code(self):
        self.memory_persistence()
        missing = "/nonexistent/cpq-tools/bin/dwg2dxf"
        self.use_env(DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY=missing,
                     DWG_CONVERTER_VERSION="0.14")
        cap = self.pkg().capability()
        self.assertFalse(cap.get("available"), "二进制不存在时不许说可用：%r" % cap)
        self.assertEqual(cap.get("stable_error_code"), "DWG_CONVERTER_BINARY_UNUSABLE", cap)
        self.assertEqual(cap.get("binary"), missing)
        error = self.expect_error("DWG_CONVERTER_BINARY_UNUSABLE", self.convert)
        self.assertEqual((error.http_status, error.retryable), (500, False))
        self.assertEqual((error.detected or {}).get("provider"), "libredwg")
        self.assertEqual((error.detected or {}).get("binary"), missing)

    def test_a4_version_mismatch_uses_the_new_code(self):
        self.memory_persistence()
        script = self.fake_cli()
        cap = self.capability(DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY=str(script),
                              DWG_CONVERTER_VERSION="9.99")
        self.assertFalse(cap.get("version_ok"), cap)
        self.assertFalse(cap.get("available"), "版本不匹配不许当可用：%r" % cap)
        self.assertEqual(cap.get("stable_error_code"), "DWG_CONVERTER_BINARY_UNUSABLE")
        self.assertEqual(cap.get("expected_version"), "9.99")
        self.assertEqual(cap.get("converter_version"), "0.14")
        error = self.expect_error("DWG_CONVERTER_BINARY_UNUSABLE", self.convert)
        detected = error.detected or {}
        self.assertEqual(detected.get("expected_version"), "9.99")
        self.assertEqual(detected.get("actual_version"), "0.14")

    def test_a5_none_locks_the_capability_even_when_a_binary_exists(self):
        script = self.fake_cli()
        cap = self.capability(DWG_CONVERTER_PROVIDER="none", DWG_CONVERTER_BINARY=str(script))
        self.assertFalse(cap.get("available"), cap)
        self.assertEqual(cap.get("stable_error_code"), "DWG_CONVERTER_NOT_INSTALLED")
        self.assertIn("未安装", str(cap.get("message") or ""))

    def test_a6_legacy_names_still_work_and_the_new_names_win(self):
        self.memory_persistence()
        legacy = self.capability(CAD_CONVERTER="fake", CAD_CONVERTER_ALLOW_SIMULATED="true")
        self.assertTrue(legacy.get("available"), "旧名 CAD_CONVERTER 必须继续可用：%r" % legacy)
        self.assertTrue(legacy.get("simulated"))
        script = self.fake_cli()
        both = self.capability(CAD_CONVERTER="fake", CAD_CONVERTER_ALLOW_SIMULATED="true",
                               DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY=str(script),
                               DWG_CONVERTER_VERSION="0.14")
        self.assertEqual(both.get("provider"), "libredwg", "DWG_CONVERTER_* 必须优先：%r" % both)
        self.assertFalse(both.get("simulated"))
        manifest = self.convert()
        blob = json.dumps([both, manifest], ensure_ascii=False, default=str)
        self.assertIn("converter_config_shadowed", blob,
                      "新旧配置同时给出时必须留下被遮蔽的告警（Spec §2）")

    def test_a7_new_code_is_in_the_authoritative_closed_set(self):
        table = getattr(self.preflight(), "STABLE_ERROR_CODES", {})
        for code, (status, retryable) in NEW_CODES.items():
            self.assertIn(code, table, "本批新码必须并入第 1 批 Spec §3 闭集")
            self.assertEqual(table[code].get("http_status"), status, code)
            self.assertEqual(table[code].get("retryable"), retryable, code)
            self.assertTrue(table[code].get("message"), "%s 必须有中文文案" % code)


# --------------------------------------------------------------------------- #
# B. 驱动 argv（按驱动分派，未验证的驱动不许声称已验证）
# --------------------------------------------------------------------------- #
class BDriverArgv(RepairCase):
    def test_b1_libredwg_argv_shape_is_exact(self):
        self.memory_persistence()
        script = self.fake_cli()
        self.use_env(DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY=str(script),
                     DWG_CONVERTER_VERSION="0.14")
        manifest = self.convert()
        self.assertIn(manifest.get("status"), STATUSES, manifest)
        argv = self.fake_argv(script)
        self.assertEqual(argv[0:2], ["-y", "-o"], "LibreDWG 形状必须是 `-y -o <out> <src>`：%r" % argv)
        self.assertEqual(len(argv), 4, "多余的参数就说明形状猜错了：%r" % argv)
        self.assertTrue(argv[2].endswith("converted.dxf"), argv)
        self.assertTrue(argv[3].endswith("source.dwg"), "输入必须用固定名 source.dwg：%r" % argv)

    def test_b2_oda_driver_is_never_claimed_verified(self):
        script = self.fake_cli(name="ODAFileConverter")
        cap = self.capability(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(script))
        self.assertEqual(cap.get("provider"), "oda")
        self.assertFalse(cap.get("argv_verified"),
                         "ODA/Teigha 本批没有真机验证，argv_verified 必须为 false：%r" % cap)
        self.assertNotIn("已验证", str(cap.get("message") or ""))

    def test_b3_preview_uses_the_svg_tool(self):
        state = self.memory_persistence()
        convert = self.fake_cli()
        svg = self.fake_svg()
        self.use_env(DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY=str(convert),
                     DWG_CONVERTER_VERSION="0.14", DWG_CONVERTER_PREVIEW_BINARY=str(svg))
        manifest = self.convert()
        previews = [item for item in manifest["output_files"] if item["role"] == "preview"]
        self.assertTrue(previews, "配置了预览工具就必须产出预览：%r" % manifest["output_files"])
        for item in previews:
            self.assertTrue(str(item["filename"]).endswith(".svg"), item)
            data = self.artifact_path(state, manifest, item).read_bytes()
            self.assertIn(b"<svg", data, "预览产物必须是真的 SVG")
        self.assertTrue(self.pkg().capability().get("preview_available"))

    def test_b4_interpreter_binary_is_rejected(self):
        cap = self.capability(DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY="/bin/sh")
        self.assertFalse(cap.get("available"), "不许把解释器当转换器：%r" % cap)
        self.assertEqual(cap.get("stable_error_code"), "DWG_CONVERTER_BINARY_UNUSABLE")


# --------------------------------------------------------------------------- #
# C. 产物质量门槛（不许只看退出码）
# --------------------------------------------------------------------------- #
class CQualityGate(RepairCase):
    def prepare(self, mode, **extra):
        self.memory_persistence()
        script = self.fake_cli(mode=mode)
        env = {"DWG_CONVERTER_PROVIDER": "libredwg", "DWG_CONVERTER_BINARY": str(script),
               "DWG_CONVERTER_VERSION": "0.14"}
        env.update(extra)
        self.use_env(**env)
        return script

    def test_c1_empty_output_is_not_success(self):
        self.prepare("empty")
        self.expect_error("DWG_CONVERTER_OUTPUT_INVALID", self.convert)

    def test_c2_truncated_dxf_is_not_success(self):
        self.prepare("truncated")
        self.expect_error("DWG_CONVERTER_OUTPUT_INVALID", self.convert)

    def test_c3_zero_entities_is_not_success(self):
        self.prepare("no_entities")
        self.expect_error("DWG_CONVERTER_OUTPUT_INVALID", self.convert)

    def test_c4_tidy_output_is_ok_and_carries_a_quality_block(self):
        self.prepare("tidy")
        manifest = self.convert()
        self.assertEqual(manifest.get("status"), "ok", "无诊断且质量达标 → ok：%r" % manifest.get("status"))
        quality = manifest.get("quality") or {}
        self.assertTrue(quality.get("verified"), quality)
        self.assertEqual(quality.get("entity_count"), 1)
        self.assertGreaterEqual(int(quality.get("layer_count") or 0), 1)
        self.assertEqual(quality.get("dxf_version"), "AC1015")
        self.assertFalse(quality.get("degraded"))
        self.assertEqual(manifest.get("warning_count"), 0)
        self.assertEqual(manifest.get("error_count"), 0)


# --------------------------------------------------------------------------- #
# D. 诊断计数与状态机
# --------------------------------------------------------------------------- #
class DDiagnosticsAndStatus(RepairCase):
    def prepare(self, mode="tidy", warnings=0, errors=0, distinct=0):
        self.memory_persistence()
        script = self.fake_cli(mode=mode, warnings=warnings, errors=errors, distinct=distinct)
        self.use_env(DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY=str(script),
                     DWG_CONVERTER_VERSION="0.14")
        return script

    def test_d1_warnings_are_counted_and_grouped(self):
        self.prepare(warnings=3)
        manifest = self.convert()
        self.assertEqual(manifest.get("warning_count"), 3)
        self.assertEqual(manifest.get("error_count"), 0)
        self.assertEqual(manifest.get("status"), "success_with_warnings",
                         "退出码 0 但有警告，不许报 ok")
        codes = manifest.get("warning_codes") or {}
        self.assertTrue(codes, "必须给出归一化后的告警模板计数")
        self.assertEqual(sum(int(v) for v in codes.values()), 3, codes)
        self.assertTrue((manifest.get("quality") or {}).get("degraded"))

    def test_d2_errors_are_counted_like_the_round_box(self):
        self.prepare(warnings=1, errors=2)
        manifest = self.convert()
        self.assertEqual(manifest.get("error_count"), 2, "圆盘盒式错误必须逐条计数")
        self.assertEqual(manifest.get("warning_count"), 1)
        self.assertEqual(manifest.get("status"), "success_with_warnings")

    def test_d3_degraded_conversion_never_claims_lossless(self):
        self.prepare(warnings=1, errors=3)
        manifest = self.convert()
        blob = json.dumps(manifest, ensure_ascii=False, default=str)
        for banned in ("lossless", "无损", "完全一致"):
            self.assertNotIn(banned, blob, "有损转换不许出现「%s」声称" % banned)

    def test_d4_raw_diagnostics_never_reach_the_manifest(self):
        self.prepare(warnings=1, errors=1)
        manifest = self.convert()
        blob = json.dumps(manifest, ensure_ascii=False, default=str)
        self.assertNotIn("bit_read_BD", blob, "stderr 原文不许入库")
        self.assertNotIn("Object handle not found", blob)
        self.assertGreater(int(manifest.get("diagnostics_bytes") or 0), 0)
        self.assertRegex(str(manifest.get("diagnostics_sha256") or ""), r"^[0-9a-f]{64}$")

    def test_d5_status_is_a_closed_set(self):
        for warnings, errors, expected in ((0, 0, "ok"), (1, 0, "success_with_warnings"),
                                           (0, 1, "success_with_warnings")):
            self.prepare(warnings=warnings, errors=errors)
            manifest = self.convert()
            self.assertIn(manifest.get("status"), STATUSES)
            self.assertEqual(manifest.get("status"), expected)
        self.prepare(mode="empty")
        error = self.expect_error("DWG_CONVERTER_OUTPUT_INVALID", self.convert)
        self.assertIn(error.stable_error_code,
                      getattr(self.preflight(), "STABLE_ERROR_CODES", {}))

    def test_d6_warning_codes_are_capped_but_counts_are_complete(self):
        self.prepare(distinct=30)
        manifest = self.convert()
        codes = manifest.get("warning_codes") or {}
        self.assertLessEqual(len(codes), 20, "告警模板最多保留 20 条：%d" % len(codes))
        self.assertTrue(manifest.get("warning_codes_truncated"),
                        "被截断必须显式标记 warning_codes_truncated")
        self.assertEqual(manifest.get("warning_count"), 30, "计数必须是完整数量，不是截断后的条数")


# --------------------------------------------------------------------------- #
# E. 真实二进制（没有就 skip：B 层未验收）
# --------------------------------------------------------------------------- #
class ERealBinary(RepairCase):
    def setUp(self):
        if not self.real_binary():
            self.skipTest("真实转换器未安装：B 层未验收（Spec §8）")

    def run_sample(self, path):
        if not path.exists():
            self.skipTest("客户样本不在本机（不入库）：%s" % path.name)
        self.memory_persistence()
        self.use_env(**self.libredwg_env())
        return self.convert(path.read_bytes(), filename=path.name)

    def test_e1_both_samples_convert_with_quality(self):
        for path in (WINE_BOX, ROUND_BOX):
            manifest = self.run_sample(path)
            self.assertIn(manifest.get("status"), {"ok", "success_with_warnings"}, path.name)
            quality = manifest.get("quality") or {}
            self.assertTrue(quality.get("verified"), path.name)
            self.assertGreater(int(quality.get("entity_count") or 0), 0, "%s 实体数为 0" % path.name)
            self.assertGreater(int(quality.get("layer_count") or 0), 0, "%s 图层数为 0" % path.name)

    def test_e2_round_box_is_degraded_not_clean(self):
        manifest = self.run_sample(ROUND_BOX)
        self.assertGreaterEqual(int(manifest.get("error_count") or 0), 1,
                                "圆盘盒实测有 3 条位流错误，必须如实计数")
        self.assertEqual(manifest.get("status"), "success_with_warnings",
                         "有错误的转换不许报 ok")
        self.assertTrue((manifest.get("quality") or {}).get("degraded"))

    def test_e3_wine_box_warnings_are_not_reported_as_clean(self):
        manifest = self.run_sample(WINE_BOX)
        self.assertGreaterEqual(int(manifest.get("warning_count") or 0), 1,
                                "酒盒实测约 1520 条警告，不许当成干净转换")
        self.assertEqual(manifest.get("status"), "success_with_warnings")

    def test_e4_preview_can_be_rendered(self):
        if not self.real_preview():
            self.skipTest("本机没有 dwg2SVG：预览能力未验证")
        state = self.memory_persistence()
        self.use_env(**self.libredwg_env())
        manifest = self.convert(WINE_BOX.read_bytes(), filename=WINE_BOX.name)
        previews = [item for item in manifest["output_files"] if item["role"] == "preview"]
        self.assertTrue(previews, "配置了 dwg2SVG 就必须产出预览")
        for item in previews:
            data = self.artifact_path(state, manifest, item).read_bytes()
            self.assertGreater(len(data), 0, "预览不得为空")


# --------------------------------------------------------------------------- #
# F. 审计与用户可见文案
# --------------------------------------------------------------------------- #
class FAuditAndCopy(RepairCase):
    def prepare(self, mode="tidy", warnings=0, errors=0):
        state = self.memory_persistence()
        script = self.fake_cli(mode=mode, warnings=warnings, errors=errors)
        self.use_env(DWG_CONVERTER_PROVIDER="libredwg", DWG_CONVERTER_BINARY=str(script),
                     DWG_CONVERTER_VERSION="0.14")
        return state, script

    def test_f1_audit_records_quality_without_raw_diagnostics(self):
        state, script = self.prepare(warnings=2, errors=1)
        self.convert()
        audits = [item for item in state["audits"] if "convert" in str(item["action"])]
        self.assertTrue(audits, "必须有转换审计：%r" % state["audits"])
        blob = json.dumps(audits, ensure_ascii=False, default=str)
        for key in ("provider", "converter_version", "status", "warning_count", "error_count"):
            self.assertIn(key, blob, "审计必须记 %s" % key)
        self.assertNotIn("bit_read_BD", blob, "审计不许写 stderr 原文")
        self.assertNotIn(str(script.parent), blob, "审计不许出现绝对路径（二进制只记 basename）")

    def test_f2_degraded_copy_is_honest(self):
        self.prepare(warnings=1)
        manifest = self.convert()
        blob = json.dumps(manifest, ensure_ascii=False, default=str)
        self.assertIn("警告", blob, "有告警时 manifest 必须如实说明")
        self.assertNotIn("无损", blob)

    def test_f3_failed_conversion_never_claims_success(self):
        state, _script = self.prepare(mode="empty")
        with self.assertRaises(self.preflight().FileCapabilityError):
            self.convert()
        stored = list(state["manifests"].values())
        self.assertTrue(stored, "失败也要留一条 manifest（第 2 批 §4.2）")
        latest = stored[-1]
        self.assertEqual(latest.get("status"), "failed")
        blob = json.dumps(latest, ensure_ascii=False, default=str)
        self.assertNotIn("无损", blob)
        self.assertNotIn("成功", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
