"""红测：DWG 前两批修复 —— ODA 27.1 主转换器 + LibreDWG 0.14 回退链。

Spec：`docs/specs/dwg-conversion-quality-repair.md`
前置：第 1 批 `docs/specs/dwg-file-capability-preflight.md`（错误码闭集是唯一权威）、
      第 2 批 `docs/specs/dwg-controlled-conversion-adapter.md`（转换层安全规则）。

本文件只覆盖「前两批的修复」，不重复第 2 批已有的安全/幂等/并发用例。

分组：
  A 配置契约（provider/binary/version/wrapper/回退/新错误码）· B 驱动 argv ·
  C 产物质量门槛（不许只看退出码）· D 诊断计数与状态机 · E 真实二进制（否 则 skip）·
  F 审计与用户可见文案 · G 受控回退链（只在主转换器明确失败时回退）

首版（`/1`）的缺口已实现。本版（Spec `/2`）新增的**未实现缺口**（新用例因此先失败）：
  · ODA 驱动仍写 `argv_verified=False`、argv 只有 6 个参数（缺 `'*.dwg'`）、还用 `--version` 探测版本；
  · `DWG_CONVERTER_WRAPPER` 与 `DWG_CONVERTER_FALLBACK_*` 全部不存在；
  · `capability()` 没有 version_source / binary_reason / wrapper / fallback / primary_unavailable_reason；
  · manifest 没有 fallback_used / primary_failure_code / attempts / converter_role；
  · 主转换器失败时没有回退链（`G` 组整体先失败）。

夹具策略（Spec §9）：小规模用**假 CLI 脚本**验判定逻辑（可注入警告/错误/空输出/截断/零实体），
真实样本只用真二进制验兼容性；两类不得混为一类。假 LibreDWG 只认 `-y -o <out> <src>`，
假 ODA 只认真机形状 `<inDir> <outDir> ACAD2018 DXF 0 1 *.dwg`，给错形状就退出非 0。

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
    "DWG_CONVERTER_PREVIEW_BINARY", "DWG_CONVERTER_WRAPPER",
    "DWG_CONVERTER_FALLBACK_PROVIDER", "DWG_CONVERTER_FALLBACK_BINARY",
    "DWG_CONVERTER_FALLBACK_VERSION", "DWG_CONVERTER_FALLBACK_WRAPPER",
    "DWG_CONVERTER_FALLBACK_PREVIEW_BINARY",
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
  fail) echo "ERROR: dwg2dxf aborted on this drawing" >&2; exit 6 ;;
  tidy) cp "$FAKE_DXF_PAYLOAD" "$OUT" ;;
  empty) : > "$OUT" ;;
  truncated) printf '0\\nSECTION\\n2\\nENTITIES\\n0\\nLWPOLYLINE\\n90\\n4\\n10\\n0.0\\n' > "$OUT" ;;
  no_entities) printf '__NOENT__' > "$OUT" ;;
esac
exit 0
"""

#: 假 ODA File Converter：只认真机形状 `<inDir> <outDir> ACAD2018 DXF 0 1 *.dwg`。
#: 每次调用都用 `--` 分隔追加记录 argv（误用 `--version` 探测也会留下痕迹）。
FAKE_ODA_CLI = """#!/bin/sh
SIDECAR="__SIDECAR__"
printf '%s\\n' "--" >> "$SIDECAR"
printf '%s\\n' "$0" >> "$SIDECAR"
for arg in "$@"; do printf '%s\\n' "$arg" >> "$SIDECAR"; done
if [ "$1" = "--version" ]; then
  echo "ODAFileConverter: unrecognized option '--version'" >&2
  exit 9
fi
if [ "$#" -ne 7 ]; then
  echo "ERROR: usage: ODAFileConverter inDir outDir ACAD2018 DXF 0 1 *.dwg" >&2
  exit 3
fi
if [ "$3" != "ACAD2018" ] || [ "$4" != "DXF" ] || [ "$5" != "0" ] || [ "$6" != "1" ] || [ "$7" != "*.dwg" ]; then
  echo "ERROR: unexpected ODA argument shape" >&2
  exit 4
fi
if [ ! -f "$1/source.dwg" ]; then
  echo "ERROR: input source.dwg not found in $1" >&2
  exit 5
fi
i=0
while [ $i -lt __WARN__ ]; do
  echo "Warning: DIMASSOC 2 group code out of order" >&2
  i=$((i+1))
done
i=0
while [ $i -lt __ERR__ ]; do
  echo "ERROR: audit reported 1 error" >&2
  i=$((i+1))
done
case "${FAKE_DXF_MODE:-tidy}" in
  fail) echo "ERROR: converter aborted on this drawing" >&2; exit 2 ;;
  hang) sleep 30; exit 0 ;;
  tidy) cp "$FAKE_DXF_PAYLOAD" "$2/source.dxf" ;;
  empty) : > "$2/source.dxf" ;;
  truncated) printf '0\\nSECTION\\n2\\nENTITIES\\n0\\nLWPOLYLINE\\n' > "$2/source.dxf" ;;
  no_entities) printf '__NOENT__' > "$2/source.dxf" ;;
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
    def workdir(self, prefix):
        directory = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, directory, True)
        return directory

    def sidecar(self, script):
        """每个假二进制一个侧车文件，记录它被调用时的 argv。"""
        return script.parent / (script.name + ".args")

    def write_script(self, script, text):
        script.write_text(text, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return script

    def set_fake_payload(self, mode):
        payload = FIXTURES / "rect_10x5.dxf"
        self.assertTrue(payload.exists(), "需要夹具 %s" % payload.name)
        os.environ["FAKE_DXF_PAYLOAD"] = str(payload)
        os.environ["FAKE_DXF_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE_DXF_PAYLOAD", None)
        self.addCleanup(os.environ.pop, "FAKE_DXF_MODE", None)

    def fake_cli(self, name="dwg2dxf", mode="tidy", warnings=0, errors=0, distinct=0,
                 directory=None):
        """假 LibreDWG CLI：只认 `-y -o <out> <src>`，给错形状就退出非 0。"""
        directory = pathlib.Path(directory) if directory is not None else self.workdir("dwg-fake-cli-")
        directory.mkdir(parents=True, exist_ok=True)
        sidecar = self.sidecar(directory / name)
        payload = FIXTURES / "rect_10x5.dxf"
        self.assertTrue(payload.exists(), "需要夹具 %s" % payload.name)
        text = (FAKE_CLI.replace("__SIDECAR__", str(sidecar))
                .replace("__WARN__", str(int(warnings)))
                .replace("__DISTINCT__", str(int(distinct)))
                .replace("__ERR__", str(int(errors)))
                .replace("__NOENT__", NO_ENTITIES_DXF))
        script = directory / name
        self.write_script(script, text)
        self.set_fake_payload(mode)
        return script

    def fake_oda_cli(self, mode="tidy", warnings=0, errors=0, directory=None):
        """假 ODA File Converter：只认真机形状 `<inDir> <outDir> ACAD2018 DXF 0 1 *.dwg`。"""
        name = "ODAFileConverter"
        directory = pathlib.Path(directory) if directory is not None else self.workdir("dwg-fake-oda-")
        directory.mkdir(parents=True, exist_ok=True)
        sidecar = self.sidecar(directory / name)
        text = (FAKE_ODA_CLI.replace("__SIDECAR__", str(sidecar))
                .replace("__WARN__", str(int(warnings)))
                .replace("__ERR__", str(int(errors)))
                .replace("__NOENT__", NO_ENTITIES_DXF))
        script = directory / name
        self.write_script(script, text)
        self.set_fake_payload(mode)
        return script

    def fake_wrapper(self, name="xvfb-run"):
        """假包装前缀（xvfb-run）：记录一次调用，丢掉前缀项后 exec 真正的转换器。"""
        directory = self.workdir("dwg-fake-wrapper-")
        script = directory / name
        text = ('#!/bin/sh\nprintf \'%s\\n\' "$@" > "__MARKER__"\nshift\nexec "$@"\n'
                .replace("__MARKER__", str(self.sidecar(script))))
        return self.write_script(script, text)

    def prepend_path(self, directory):
        saved = os.environ.get("PATH", "")
        self.addCleanup(os.environ.__setitem__, "PATH", saved)
        os.environ["PATH"] = "%s%s%s" % (directory, os.pathsep, saved)

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
        """扁平 argv（假 LibreDWG 每次调用都会截断侧车文件）。"""
        sidecar = self.sidecar(script)
        if not sidecar.exists():
            self.fail("假转换器没有被调用（没有 argv 记录）")
        return [line for line in sidecar.read_text(encoding="utf-8").splitlines() if line]

    def fake_calls(self, script):
        """按 `--` 分隔读回「每次调用的 argv 列表」（假 ODA 用追加 + 分隔符）。"""
        sidecar = self.sidecar(script)
        if not sidecar.exists():
            return []
        calls, current = [], []
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            if line == "--":
                if current:
                    calls.append(current)
                current = []
            elif line:
                current.append(line)
        if current:
            calls.append(current)
        return calls

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

    #: ODA 在受支持平台上的已知安装位置（本机 macOS / 34 服务器）。
    ODA_CANDIDATES = (
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun",
    )

    def real_oda_binary(self):
        found = shutil.which("ODAFileConverter")
        if found:
            return found
        for path in self.ODA_CANDIDATES:
            if os.access(path, os.X_OK):
                return path
        return ""

    def oda_env(self):
        env = {"DWG_CONVERTER_PROVIDER": "oda",
               "DWG_CONVERTER_BINARY": self.real_oda_binary(),
               "DWG_CONVERTER_VERSION": "27.1",
               "DWG_CONVERTER_FALLBACK_PROVIDER": "none"}
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

    def test_a8_auto_probes_oda_before_libredwg(self):
        directory = self.workdir("dwg-probe-")
        self.fake_cli(directory=directory)
        self.fake_oda_cli(directory=directory)
        self.prepend_path(directory)
        cap = self.capability(DWG_CONVERTER_PROVIDER="auto", DWG_CONVERTER_VERSION="27.1",
                              DWG_CONVERTER_FALLBACK_PROVIDER="none")
        self.assertTrue(cap.get("available"), cap)
        self.assertEqual(cap.get("provider"), "oda",
                         "auto 必须以 ODA 为主转换器（Spec §2.4）：%r" % cap)
        self.assertTrue(str(cap.get("binary") or "").endswith("ODAFileConverter"), cap)

    def test_a9_wrapper_is_prepended_to_the_converter_argv(self):
        self.memory_persistence()
        script = self.fake_oda_cli()
        wrapper = self.fake_wrapper()
        marker = self.sidecar(wrapper)
        self.use_env(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(script),
                     DWG_CONVERTER_VERSION="27.1",
                     DWG_CONVERTER_WRAPPER="%s -a" % wrapper,
                     DWG_CONVERTER_FALLBACK_PROVIDER="none")
        cap = self.pkg().capability()
        self.assertEqual(cap.get("wrapper"), [str(wrapper), "-a"],
                         "capability 必须如实返回生效的 wrapper（Spec §2.7）：%r" % cap)
        manifest = self.convert()
        self.assertIn(manifest.get("status"), STATUSES, manifest)
        self.assertTrue(marker.exists(), "wrapper 必须被调用（逐项排在 exe 之前）：%r" % cap)
        calls = self.fake_calls(script)
        self.assertEqual(len(calls), 1, "只应发生一次转换调用：%r" % calls)
        self.assertEqual(calls[0][0], str(script),
                         "wrapper 之后必须紧跟配置的转换器：%r" % calls[0])
        self.assertEqual(len(calls[0]), 8, calls[0])

    def test_a10_illegal_wrapper_is_rejected(self):
        script = self.fake_oda_cli()
        cap = self.capability(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(script),
                              DWG_CONVERTER_VERSION="27.1",
                              DWG_CONVERTER_WRAPPER="xvfb-run -a; rm -rf /")
        self.assertFalse(cap.get("available"), "含 shell 元字符的 wrapper 必须被拒：%r" % cap)
        self.assertEqual(cap.get("stable_error_code"), "DWG_CONVERTER_BINARY_UNUSABLE", cap)
        self.assertEqual(cap.get("binary_reason"), "wrapper_invalid", cap)
        error = self.expect_error("DWG_CONVERTER_BINARY_UNUSABLE", self.convert)
        self.assertEqual((error.detected or {}).get("binary_reason"), "wrapper_invalid")

    def test_a11_oda_version_must_be_declared_and_is_never_probed(self):
        script = self.fake_oda_cli()
        undeclared = self.capability(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(script),
                                     DWG_CONVERTER_FALLBACK_PROVIDER="none")
        self.assertTrue(undeclared.get("available"),
                        "ODA 二进制可用就不能因为「探测不到版本」说不可用：%r" % undeclared)
        self.assertFalse(undeclared.get("version_ok"), undeclared)
        self.assertEqual(undeclared.get("version_source"), "unverifiable", undeclared)
        self.assertEqual(undeclared.get("converter_version"), "", undeclared)
        self.assertIn("版本", str(undeclared.get("message") or ""))
        self.assertEqual(self.fake_calls(script), [],
                         "ODA 不支持 --version，探测不许执行子进程（Spec §2.5）")
        declared = self.capability(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(script),
                                   DWG_CONVERTER_VERSION="27.1",
                                   DWG_CONVERTER_FALLBACK_PROVIDER="none")
        self.assertTrue(declared.get("version_ok"), declared)
        self.assertEqual(declared.get("converter_version"), "27.1", declared)
        self.assertEqual(declared.get("version_source"), "config_declared", declared)
        self.assertEqual(self.fake_calls(script), [], "ODA 的版本探测不许执行子进程")


# --------------------------------------------------------------------------- #
# B. 驱动 argv（按驱动分派；ODA 已真机验证，未经真机验证的驱动不许声称已验证）
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

    def test_b2_oda_driver_is_verified_and_never_probed_for_a_version(self):
        script = self.fake_oda_cli()
        cap = self.capability(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(script),
                              DWG_CONVERTER_VERSION="27.1",
                              DWG_CONVERTER_FALLBACK_PROVIDER="none")
        self.assertEqual(cap.get("provider"), "oda")
        self.assertTrue(cap.get("argv_verified"),
                        "ODA 已按 27.1 真机实测（两份样本转换成功），argv_verified 必须为 true：%r" % cap)
        self.assertEqual(self.fake_calls(script), [],
                         "ODA 不支持 `--version`，驱动不许拿它探测版本（Spec §2.5）")

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

    def test_b5_oda_argv_shape_is_exact(self):
        self.memory_persistence()
        script = self.fake_oda_cli()
        self.use_env(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(script),
                     DWG_CONVERTER_VERSION="27.1", DWG_CONVERTER_FALLBACK_PROVIDER="none")
        manifest = self.convert()
        self.assertIn(manifest.get("status"), STATUSES, manifest)
        calls = self.fake_calls(script)
        self.assertEqual(len(calls), 1, "ODA 只许被调用一次（不许先探测版本）：%r" % calls)
        argv = calls[0]
        self.assertEqual(len(argv), 8, "argv 必须是 exe + 6 个位置参数：%r" % argv)
        self.assertEqual(argv[0], str(script), argv)
        self.assertTrue(pathlib.Path(argv[1]).is_dir(), argv)
        self.assertTrue(pathlib.Path(argv[2]).is_dir(), argv)
        self.assertEqual(argv[3:], ["ACAD2018", "DXF", "0", "1", "*.dwg"],
                         "ODA 位置参数必须逐字一致（含 '*.dwg' 过滤）：%r" % argv)
        self.assertEqual(manifest.get("converter_name"), "oda", manifest)
        self.assertEqual(manifest.get("converter_version"), "27.1", manifest)
        quality = manifest.get("quality") or {}
        self.assertEqual(quality.get("output_version"), "ACAD2018", quality)
        self.assertTrue(quality.get("audit_enabled"),
                        "ODA 开了 Audit/Repair 就必须留痕（Spec §4）：%r" % quality)


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

    def test_e5_real_oda_converts_both_samples_cleanly(self):
        binary = self.real_oda_binary()
        if not binary:
            self.skipTest("本机没有 ODA File Converter：ODA 真实转换未验证")
        state = self.memory_persistence()
        for path in (WINE_BOX, ROUND_BOX):
            self.use_env(**self.oda_env())
            manifest = self.convert(path.read_bytes(), filename=path.name)
            self.assertEqual(manifest.get("status"), "ok",
                             "ODA 实测 stderr 为空，%s 不许被判成有损：%r"
                             % (path.name, manifest.get("warning_codes")))
            self.assertEqual(manifest.get("warning_count"), 0, path.name)
            self.assertEqual(manifest.get("error_count"), 0, path.name)
            quality = manifest.get("quality") or {}
            self.assertTrue(quality.get("verified"), path.name)
            self.assertEqual(quality.get("output_version"), "ACAD2018", path.name)
            self.assertTrue(quality.get("audit_enabled"), path.name)
            self.assertGreater(int(quality.get("entity_count") or 0), 0, path.name)
            self.assertGreater(int(quality.get("layer_count") or 0), 0, path.name)
            dxf = [item for item in manifest["output_files"] if item["role"] == "dxf"]
            self.assertTrue(dxf, "%s 必须有 DXF 产物：%r" % (path.name, manifest["output_files"]))
            self.assertGreater(
                len(self.artifact_path(state, manifest, dxf[0]).read_bytes()), 0, path.name)

    def test_e6_real_fallback_chain_runs_when_the_primary_is_missing(self):
        fallback = self.real_binary()
        if not fallback:
            self.skipTest("本机没有 LibreDWG dwg2dxf：真实回退链未验证")
        self.memory_persistence()
        self.use_env(DWG_CONVERTER_PROVIDER="oda",
                     DWG_CONVERTER_BINARY="/nonexistent/cpq-tools/ODAFileConverter",
                     DWG_CONVERTER_VERSION="27.1",
                     DWG_CONVERTER_FALLBACK_PROVIDER="libredwg",
                     DWG_CONVERTER_FALLBACK_BINARY=fallback,
                     DWG_CONVERTER_FALLBACK_VERSION="0.14")
        manifest = self.convert(WINE_BOX.read_bytes(), filename=WINE_BOX.name)
        self.assertTrue(manifest.get("fallback_used"),
                        "主转换器不可用时必须走回退链（Spec §7）：%r" % manifest)
        self.assertEqual(manifest.get("primary_failure_code"), "DWG_CONVERTER_BINARY_UNUSABLE",
                         manifest)
        self.assertEqual(manifest.get("converter_role"), "fallback", manifest)
        self.assertEqual(manifest.get("converter_name"), "libredwg", manifest)
        self.assertIn(manifest.get("status"), {"ok", "success_with_warnings"}, manifest)
        quality = manifest.get("quality") or {}
        self.assertTrue(quality.get("verified"), quality)
        self.assertGreater(int(quality.get("entity_count") or 0), 0, quality)


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

# --------------------------------------------------------------------------- #
# G. 受控回退链（只在主转换器明确失败时回退，Spec §7）
# --------------------------------------------------------------------------- #
class GFallbackChain(RepairCase):
    def chain(self, *, state=None, primary_mode="tidy", fallback_mode="tidy",
              primary_binary=None, fallback_provider="libredwg", **extra):
        """建一条 ODA(主) → LibreDWG(回退) 的链；`primary_binary` 覆盖主二进制路径。"""
        state = state if state is not None else self.memory_persistence()
        fallback = self.fake_cli(name="dwg2dxf", mode=fallback_mode)
        primary = None
        if primary_binary is None:
            primary = self.fake_oda_cli(mode=primary_mode)
            primary_binary = str(primary)
        env = {"DWG_CONVERTER_PROVIDER": "oda", "DWG_CONVERTER_BINARY": primary_binary,
               "DWG_CONVERTER_VERSION": "27.1",
               "DWG_CONVERTER_FALLBACK_PROVIDER": fallback_provider,
               "DWG_CONVERTER_FALLBACK_BINARY": str(fallback),
               "DWG_CONVERTER_FALLBACK_VERSION": "0.14"}
        env.update(extra)
        self.use_env(**env)
        return state, primary, fallback

    def test_g1_primary_success_never_touches_the_fallback(self):
        _state, primary, fallback = self.chain()
        manifest = self.convert()
        self.assertEqual(manifest.get("status"), "ok", manifest)
        self.assertFalse(manifest.get("fallback_used"), manifest)
        self.assertEqual(manifest.get("primary_failure_code"), "", manifest)
        self.assertEqual(manifest.get("converter_role"), "primary", manifest)
        self.assertEqual(manifest.get("converter_name"), "oda", manifest)
        attempts = manifest.get("attempts")
        self.assertEqual(len(attempts or []), 1, "主成功不该有多余跳次：%r" % attempts)
        self.assertEqual((attempts or [{}])[0].get("role"), "primary", attempts)
        self.assertFalse(self.sidecar(fallback).exists(), "主转换器成功时不许调用回退转换器")
        self.assertEqual(len(self.fake_calls(primary)), 1, "主转换器只许被调用一次")

    def test_g2_nonzero_exit_falls_back_and_is_recorded(self):
        state, primary, fallback = self.chain(primary_mode="fail")
        manifest = self.convert()
        self.assertTrue(manifest.get("fallback_used"), manifest)
        self.assertEqual(manifest.get("primary_failure_code"), "DWG_CONVERSION_FAILED", manifest)
        self.assertEqual(manifest.get("converter_role"), "fallback", manifest)
        self.assertEqual(manifest.get("converter_name"), "libredwg", manifest)
        self.assertEqual(manifest.get("converter_version"), "0.14", manifest)
        self.assertIn(manifest.get("status"), {"ok", "success_with_warnings"}, manifest)
        attempts = manifest.get("attempts") or []
        self.assertEqual(len(attempts), 2, attempts)
        self.assertEqual(attempts[0].get("role"), "primary", attempts)
        self.assertEqual(attempts[0].get("error_code"), "DWG_CONVERSION_FAILED", attempts)
        self.assertEqual(attempts[1].get("role"), "fallback", attempts)
        names = [str(item.get("filename")) for item in manifest["output_files"]]
        self.assertIn("converted.dxf", names, names)
        self.assertEqual(len(self.fake_calls(primary)), 1, "主转换器只许被调用一次")
        self.assertTrue(self.sidecar(fallback).exists(), "回退转换器必须被调用")
        blob = json.dumps(state["audits"], ensure_ascii=False, default=str)
        self.assertIn("fallback", blob, "回退必须留审计（Spec §7.2）：%r" % state["audits"])

    def test_g3_timeout_falls_back_and_is_recorded(self):
        _state, _primary, _fallback = self.chain(primary_mode="hang",
                                                 CAD_CONVERTER_TIMEOUT_SECONDS="1")
        manifest = self.convert()
        self.assertTrue(manifest.get("fallback_used"), manifest)
        self.assertEqual(manifest.get("primary_failure_code"), "DWG_CONVERSION_TIMEOUT", manifest)
        self.assertEqual(manifest.get("converter_role"), "fallback", manifest)
        self.assertIn(manifest.get("status"), {"ok", "success_with_warnings"}, manifest)

    def test_g4_invalid_primary_output_falls_back_without_publishing_it(self):
        _state, _primary, _fallback = self.chain(primary_mode="empty")
        manifest = self.convert()
        self.assertTrue(manifest.get("fallback_used"), manifest)
        self.assertEqual(manifest.get("primary_failure_code"), "DWG_CONVERTER_OUTPUT_INVALID",
                         manifest)
        names = [str(item.get("filename")) for item in manifest["output_files"]]
        self.assertIn("converted.dxf", names, names)
        self.assertNotIn("source.dxf", names, "主转换器失败留下的半成品不许进产物：%r" % names)

    def test_g5_both_failures_surface_the_primary_code_and_both_attempts(self):
        state, _primary, _fallback = self.chain(primary_mode="fail", fallback_mode="fail")
        error = self.expect_error("DWG_CONVERSION_FAILED", self.convert)
        detected = error.detected or {}
        self.assertIn("primary", detected, "必须能看出主转换器为什么失败：%r" % detected)
        self.assertIn("fallback", detected, "必须能看出回退也失败了：%r" % detected)
        self.assertEqual((detected.get("primary") or {}).get("error_code"),
                         "DWG_CONVERSION_FAILED", detected)
        self.assertEqual((detected.get("fallback") or {}).get("error_code"),
                         "DWG_CONVERSION_FAILED", detected)
        stored = list(state["manifests"].values())
        self.assertEqual(len(stored), 1, stored)
        manifest = stored[0]
        self.assertEqual(manifest.get("status"), "failed", manifest)
        self.assertTrue(manifest.get("fallback_used"), manifest)
        self.assertEqual(manifest.get("primary_failure_code"), "DWG_CONVERSION_FAILED", manifest)
        self.assertEqual(len(manifest.get("attempts") or []), 2, manifest.get("attempts"))
        self.assertEqual(manifest.get("output_files"), [], manifest)

    def test_g6_fallback_provider_none_disables_the_chain(self):
        state, _primary, fallback = self.chain(primary_mode="fail", fallback_provider="none")
        self.expect_error("DWG_CONVERSION_FAILED", self.convert)
        self.assertFalse(self.sidecar(fallback).exists(), "回退被关闭后不许调用回退转换器")
        stored = list(state["manifests"].values())
        self.assertEqual(len(stored), 1, stored)
        self.assertFalse(stored[0].get("fallback_used"), stored[0])
        self.assertEqual(stored[0].get("primary_failure_code"), "", stored[0])
        self.assertEqual(len(stored[0].get("attempts") or []), 1, stored[0].get("attempts"))

    def test_g7_cache_never_reuses_a_fallback_result_as_the_primary(self):
        state, _primary, _fallback = self.chain(
            primary_binary="/nonexistent/cpq-tools/ODAFileConverter")
        first = self.convert()
        self.assertTrue(first.get("fallback_used"), first)
        self.assertEqual(first.get("converter_role"), "fallback", first)
        _state2, primary, _fallback2 = self.chain(state=state)
        second = self.convert()
        self.assertEqual(second.get("converter_role"), "primary",
                         "主转换器恢复后必须重新调用主转换器，不许复用回退产物：%r" % second)
        self.assertFalse(second.get("fallback_used"), second)
        self.assertEqual(len(self.fake_calls(primary)), 1, "主转换器必须真的被调用")
        self.assertEqual(first.get("conversion_id"), second.get("conversion_id"),
                         "回退不许分裂产物目录（Spec §7.3）")

    def test_g8_configuration_errors_never_fall_back(self):
        state, primary, fallback = self.chain(DWG_CONVERTER_WRAPPER="xvfb-run -a; rm -rf /")
        error = self.expect_error("DWG_CONVERTER_BINARY_UNUSABLE", self.convert)
        self.assertEqual((error.detected or {}).get("binary_reason"), "wrapper_invalid",
                         error.detected)
        self.assertFalse(self.sidecar(primary).exists(), "配置错误时不许调用主转换器")
        self.assertFalse(self.sidecar(fallback).exists(), "配置错误时不许回退（Spec §7.1）")
        stored = list(state["manifests"].values())
        self.assertEqual(len(stored), 1, stored)
        self.assertFalse(stored[0].get("fallback_used"), stored[0])
        self.assertEqual(len(stored[0].get("attempts") or []), 1, stored[0].get("attempts"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
