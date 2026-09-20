"""红测：DWG 受控转换服务（转换适配器层）—— DWG 支持第 2 批。

Spec：`docs/specs/dwg-controlled-conversion-adapter.md`（前置：第 1 批
`docs/specs/dwg-file-capability-preflight.md`）。真实样本：
`裕同包装项目-待开发/酒盒.dwg`（686195 B）、`裕同包装项目-待开发/圆盘盒.dwg`（889062 B），
文件头都是 `AC1027`。样本只读，本文件不得修改它们。

首次运行时**必须失败**（实测，不是推断）：
  · `tech_app/backend/services/cad_converter/` 不存在 → 没有适配器协议、没有编排层、没有 manifest；
  · 本机 `ODAFileConverter` / `dwg2dxf` / `dwgread` / `libredwg` / `soffice` / `libreoffice` /
    `inkscape` / `teigha` 全部不存在，Python 侧 `dxfgrabber` / `libredwg` / `pyautocad` 均
    `ModuleNotFoundError`（只有 `ezdxf` 可用）→ 本机只能验收 **A 层（编排层 + fake adapter）**，
    B 层（真实转换器）要先由用户拍板，`h` 组在未安装时 skip 并如实标注「A 层已验收 / B 层未验收」。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = SAMPLES_DIR / "酒盒.dwg"
ROUND_BOX = SAMPLES_DIR / "圆盘盒.dwg"
FRONTEND = ROOT / "tech_app" / "frontend"

PKG = "tech_app.backend.services.cad_converter"
PERSISTENCE = PKG + ".persistence"
FAKE_ADAPTER = PKG + ".adapters.fake"
PREFLIGHT = "tech_app.backend.services.file_preflight"

CAPABILITY_URL = "/api/capabilities/cad-converter"

#: Spec §3.2：manifest 必含字段（缺一项即失败）
REQUIRED_MANIFEST_KEYS = {
    "conversion_id", "project_id", "attachment_name", "drawing_version", "original_filename",
    "source_sha256", "source_format", "detected_dwg_version",
    "converter_name", "converter_version", "conversion_options",
    "output_files", "output_sha256", "warnings", "started_at", "finished_at",
    "status", "error_code", "is_simulated", "acceptance_level", "cache_key",
    "converter_stderr_digest",
}

#: Spec §4.1：本批错误码 → (HTTP, retryable)，必须与第 1 批 Spec §3 闭集逐条一致
CONVERSION_ERROR_CODES = {
    "DWG_CONVERTER_NOT_INSTALLED": (415, True),
    "DWG_CONVERSION_FAILED": (502, True),
    "DWG_CONVERSION_TIMEOUT": (504, True),
    "DWG_CONVERTER_OUTPUT_MISSING": (502, True),
    "DWG_CONVERTER_OUTPUT_INVALID": (502, True),
    "DWG_CONVERTER_OUTPUT_TOO_LARGE": (502, True),
    "DWG_CONVERTER_UNSAFE_PATH": (500, False),
    "FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION": (500, False),
}

FAILURE_MODES = {"timeout", "nonzero_exit", "missing_output", "empty_output",
                 "oversized_output", "unsafe_path"}
ADAPTER_METHODS = ("capability", "inspect", "convert_to_dxf", "render_preview",
                   "convert_3d_if_supported")
MANIFEST_OUTPUT_ROLES = {"dxf", "preview"}

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
STEP_BYTES = (b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nENDSEC;\nDATA;\nENDSEC;\n"
              b"END-ISO-10303-21;\n")

#: Spec §7：确定性环境基线（每个用例显式设定，避免读到宿主环境）
DEFAULT_ENV = {
    "APP_ENV": "ci",
    "CAD_CONVERTER": "fake",
    "CAD_CONVERTER_ALLOW_SIMULATED": "true",
    "CAD_CONVERTER_TIMEOUT_SECONDS": "30",
    "CAD_CONVERTER_MAX_OUTPUT_BYTES": str(8 * 1024 * 1024),
    "CAD_CONVERTER_MAX_OUTPUT_FILES": "20",
}


class _StopCall(Exception):
    """哨兵：本批不许碰的调用真的发生了（模型 / STEP 导入器）。"""


class AdapterCase(unittest.TestCase):
    """公共脚手架：懒加载被测包（缺失时以 Spec 文案失败），并提供内存版 persistence。"""

    # ---------------------------------------------------------------- 包加载
    def pkg(self):
        try:
            return importlib.import_module(PKG)
        except ModuleNotFoundError as exc:
            if str(getattr(exc, "name", "") or "").endswith("file_preflight"):
                self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现，见第 1 批 Spec §2）")
            self.fail("缺少 tech_app/backend/services/cad_converter/（本批 Spec §2）")

    def module(self, name):
        self.pkg()
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if str(getattr(exc, "name", "") or "").endswith("file_preflight"):
                self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现，见第 1 批 Spec §2）")
            self.fail("缺少 %s（本批 Spec §2）" % name)

    def preflight(self):
        try:
            return importlib.import_module(PREFLIGHT)
        except ModuleNotFoundError:
            self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现，见第 1 批 Spec §2）")

    # ---------------------------------------------------------------- 环境
    def use_env(self, **overrides):
        merged = dict(DEFAULT_ENV)
        merged.update({k: str(v) for k, v in overrides.items()})
        patcher = mock.patch.dict(os.environ, merged)
        patcher.start()
        self.addCleanup(patcher.stop)
        return merged

    # ---------------------------------------------------------------- 适配器
    def fake(self, **kwargs):
        module = self.module(FAKE_ADAPTER)
        cls = getattr(module, "FakeAdapter", None)
        self.assertTrue(callable(cls), "adapters/fake.py 必须提供 FakeAdapter（Spec §2.4）")
        return cls(**kwargs)

    def convert(self, content, filename=WINE_BOX.name, adapter=None, project_id="dwg-conv-test",
                attachment_name=None, drawing_version=1, **kwargs):
        package = self.pkg()
        fn = getattr(package, "convert_drawing", None)
        self.assertTrue(callable(fn), "cad_converter 必须导出 convert_drawing()（Spec §2.1）")
        return fn(project_id, filename, content, adapter=adapter,
                  attachment_name=attachment_name if attachment_name is not None else filename,
                  drawing_version=drawing_version, **kwargs)

    def expect_error(self, code, fn, *args, **kwargs):
        preflight = self.preflight()
        err_type = getattr(preflight, "FileCapabilityError")
        # 权威表在第 1 批；本批的 8 条必须是它的子集（值逐条一致）。
        authoritative = getattr(preflight, "STABLE_ERROR_CODES", {})
        expected = CONVERSION_ERROR_CODES.get(code)
        if expected is None:
            entry = authoritative.get(code) or {}
            expected = (entry.get("http_status"), entry.get("retryable"))
        try:
            result = fn(*args, **kwargs)
        except err_type as exc:
            self.assertEqual(getattr(exc, "stable_error_code", None), code,
                             "错误码必须是 %s（Spec §4.1）" % code)
            status, retryable = expected
            self.assertEqual(getattr(exc, "http_status", None), status, code)
            self.assertEqual(getattr(exc, "retryable", None), retryable, code)
            self.assertTrue(getattr(exc, "message", ""), "%s 必须有中文用户文案" % code)
            return exc
        except BaseException as exc:  # noqa: BLE001 - 只做分类
            self.fail("期望 %s，实际抛出 %s: %s" % (code, type(exc).__name__, exc))
        self.fail("期望 %s，实际成功返回：%r" % (code, result))

    # ---------------------------------------------------------------- 存储
    def memory_persistence(self):
        """用内存 + 临时目录替换 persistence，测试不写真实 tech_data、不碰历史项目。"""
        persistence = self.module(PERSISTENCE)
        root = pathlib.Path(tempfile.mkdtemp(prefix="dwg-adapter-test-"))
        self.addCleanup(shutil.rmtree, root, True)
        state = {"root": root, "manifests": {}, "seq": 0}
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
                item.setdefault("saved_seq", state["seq"])
                state["manifests"][(str(project_id), str(manifest.get("conversion_id")))] = item
            return dict(item)

        def load_manifest(project_id, conversion_id):
            with lock:
                item = state["manifests"].get((str(project_id), str(conversion_id)))
            return dict(item) if item else None

        def list_manifests(project_id):
            with lock:
                items = [dict(v) for (pid, _), v in state["manifests"].items()
                         if pid == str(project_id)]
            items.sort(key=lambda m: int(m.get("saved_seq") or 0), reverse=True)
            return items

        def sync(project_id, conversion_id):
            return None

        for name, fn in (("artifact_dir", artifact_dir), ("save_artifact", save_artifact),
                         ("save_manifest", save_manifest), ("load_manifest", load_manifest),
                         ("list_manifests", list_manifests), ("sync", sync)):
            if not hasattr(persistence, name):
                self.fail("cad_converter.persistence 必须提供 %s()（Spec §3.1）" % name)
            patcher = mock.patch.object(persistence, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        return state

    def package_sources(self):
        directory = ROOT / "tech_app" / "backend" / "services" / "cad_converter"
        if not directory.is_dir():
            self.fail("缺少 tech_app/backend/services/cad_converter/（本批 Spec §2）")
        return {path: path.read_text(encoding="utf-8", errors="replace")
                for path in sorted(directory.rglob("*.py"))}

    def sample(self, path):
        if not path.exists():
            self.skipTest("客户样本不在本机（不入库）：%s" % path.name)
        return path.read_bytes()


# --------------------------------------------------------------------------- #
# A. 适配器契约与选型（本批核心骨架）
# --------------------------------------------------------------------------- #
class AAdapterContract(AdapterCase):
    def test_a1_capability_is_honest_when_no_converter_is_installed(self):
        self.use_env(CAD_CONVERTER="none")
        cap = self.pkg().capability()
        self.assertFalse(cap.get("available"), "没有转换器时 capability().available 必须为假")
        self.assertEqual(cap.get("stable_error_code"), "DWG_CONVERTER_NOT_INSTALLED")
        self.assertIn("转换", str(cap.get("message") or ""))
        self.assertFalse(cap.get("dwg_supported"), "本批不许宣称 DWG 已受支持（Spec §0）")
        self.assertEqual(cap.get("support_claim"), "orchestration_only")
        for key in ("simulated", "adapter_name", "converter_version", "dwg_conversion",
                    "preview_render", "three_d_conversion", "env"):
            self.assertIn(key, cap, "capability() 必须返回 %s（Spec §2.1）" % key)

    def test_a2_adapter_protocol_surface_is_complete(self):
        adapter = self.fake()
        for name in ADAPTER_METHODS:
            self.assertTrue(callable(getattr(adapter, name, None)),
                            "适配器协议必须提供 %s()（Spec §2.2）" % name)
        declared = adapter.capability()
        for key in ("name", "version", "dwg_conversion", "preview_render",
                    "three_d_conversion", "simulated"):
            self.assertIn(key, declared, "适配器声明缺少 %s（Spec §2.2）" % key)
        self.assertTrue(declared["simulated"], "fake 必须自报 simulated=True（Spec §2.3）")

    def test_a3_fake_is_forbidden_in_production(self):
        self.use_env(APP_ENV="production", CAD_CONVERTER="fake",
                     CAD_CONVERTER_ALLOW_SIMULATED="false")
        self.expect_error("FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION", self.pkg().get_adapter)

    def test_a4_fake_needs_explicit_enabling_outside_production(self):
        self.use_env(APP_ENV="ci", CAD_CONVERTER="fake", CAD_CONVERTER_ALLOW_SIMULATED="false")
        self.expect_error("FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION", self.pkg().get_adapter)

    def test_a5_unknown_adapter_never_falls_back_silently(self):
        self.use_env(CAD_CONVERTER="oda-not-installed", CAD_CONVERTER_ALLOW_SIMULATED="true")
        package = self.pkg()
        self.assertIsNone(package.get_adapter(), "未知适配器必须视为未安装，不得回退到 fake")
        payload = WINE_BOX.read_bytes() if WINE_BOX.exists() else b"AC1027" + b"\x00" * 4096
        self.expect_error("DWG_CONVERTER_NOT_INSTALLED", self.convert, payload, adapter=None)

    def test_a6_wine_box_sample_goes_to_the_dwg_adapter(self):
        self.use_env()
        self.memory_persistence()
        content = self.sample(WINE_BOX)
        adapter = self.fake()
        manifest = self.convert(content, filename=WINE_BOX.name, adapter=adapter)
        self.assertTrue(adapter.calls, "真实 DWG 必须先进入 DWG 转换适配器")
        self.assertEqual(adapter.calls[-1].get("source_sha256"), hashlib.sha256(content).hexdigest())
        self.assertEqual(adapter.calls[-1].get("source_filename"), "source.dwg",
                         "适配器必须拿到固定安全名，而不是用户可控路径（Spec §2.2）")
        self.assertEqual(manifest.get("detected_dwg_version"), "AC1027")
        self.assertEqual(manifest.get("source_sha256"), hashlib.sha256(content).hexdigest())

    def test_a7_round_box_sample_goes_to_the_dwg_adapter(self):
        self.use_env()
        self.memory_persistence()
        content = self.sample(ROUND_BOX)
        adapter = self.fake()
        manifest = self.convert(content, filename=ROUND_BOX.name, adapter=adapter)
        self.assertEqual(adapter.calls[-1].get("source_sha256"), hashlib.sha256(content).hexdigest())
        self.assertEqual(manifest.get("source_format"), "dwg")
        self.assertEqual(manifest.get("original_filename"), ROUND_BOX.name)

    def test_a8_conversion_never_calls_a_vision_model(self):
        self.use_env()
        self.memory_persistence()
        guards = []
        for name, attr in (("vision", "parse_drawing"), ("vision", "verify_drawing"),
                           ("claude_client", "run")):
            try:
                module = importlib.import_module("tech_app.backend.services.%s" % name)
            except Exception:  # pragma: no cover - 依赖环境
                continue
            if hasattr(module, attr):
                patcher = mock.patch.object(module, attr, mock.Mock(side_effect=_StopCall()))
                patcher.start()
                self.addCleanup(patcher.stop)
                guards.append("%s.%s" % (name, attr))
        try:
            self.convert(self.sample(WINE_BOX), filename=WINE_BOX.name, adapter=self.fake())
        except _StopCall:  # pragma: no cover - 失败信息更明确
            self.fail("转换过程调用了模型（%s）：DWG 原始字节不得再进视觉模型" % guards)

    def test_a9_conversion_never_calls_the_step_importer(self):
        self.use_env()
        self.memory_persistence()
        try:
            step_import = importlib.import_module("tech_app.backend.services.step_import")
        except Exception:  # pragma: no cover - 依赖环境
            step_import = None
        if step_import is not None and hasattr(step_import, "import_step"):
            patcher = mock.patch.object(step_import, "import_step",
                                        mock.Mock(side_effect=_StopCall()))
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            self.convert(self.sample(ROUND_BOX), filename=ROUND_BOX.name, adapter=self.fake())
        except _StopCall:  # pragma: no cover
            self.fail("转换过程调用了 STEP 导入器：DWG 不得交给 STEP 解析")

    def test_a10_conversion_error_codes_are_a_subset_of_the_authoritative_table(self):
        self.use_env()
        table = self.preflight().STABLE_ERROR_CODES
        declared = getattr(self.pkg(), "CONVERSION_ERROR_CODES", None)
        self.assertIsInstance(declared, dict,
                              "cad_converter 必须导出 CONVERSION_ERROR_CODES（Spec §4.1）")
        self.assertEqual(set(declared), set(CONVERSION_ERROR_CODES),
                         "本批错误码必须是 Spec §4.1 的 8 条闭集，不许自创或漏项")
        for code, (status, retryable) in CONVERSION_ERROR_CODES.items():
            self.assertIn(code, table, "%s 必须来自第 1 批的权威闭集（第 1 批 Spec §3）" % code)
            self.assertEqual((table[code]["http_status"], table[code]["retryable"]),
                             (status, retryable), "第 1 批与第 2 批口径不一致：%s" % code)
            self.assertEqual(declared[code].get("http_status"), status, code)
            self.assertEqual(declared[code].get("retryable"), retryable, code)
            self.assertTrue(declared[code].get("message"), "%s 必须有中文用户文案" % code)


# --------------------------------------------------------------------------- #
# B. manifest 与产物
# --------------------------------------------------------------------------- #
class BManifestAndArtifacts(AdapterCase):
    def convert_ok(self, content=None, **kwargs):
        self.use_env(**{k: v for k, v in kwargs.pop("env", {}).items()})
        state = self.memory_persistence()
        content = content if content is not None else self.sample(WINE_BOX)
        adapter = kwargs.pop("adapter", None) or self.fake()
        manifest = self.convert(content, adapter=adapter, **kwargs)
        self.assertEqual(manifest.get("status"), "ok")
        return state, manifest, adapter

    def test_b1_success_manifest_has_every_required_field(self):
        _, manifest, _ = self.convert_ok()
        missing = sorted(REQUIRED_MANIFEST_KEYS - set(manifest))
        self.assertEqual(missing, [], "manifest 缺少 Spec §3.2 必含字段：%s" % missing)
        self.assertEqual(manifest.get("source_format"), "dwg")
        self.assertIsInstance(manifest.get("conversion_options"), dict)
        self.assertIsInstance(manifest.get("output_files"), list)
        self.assertTrue(manifest.get("started_at") and manifest.get("finished_at"))
        self.assertIsNone(manifest.get("error_code"))

    def test_b2_output_sha256_can_be_recomputed_from_disk(self):
        state, manifest, _ = self.convert_ok()
        root = state["root"] / manifest["project_id"] / manifest["conversion_id"]
        self.assertTrue(manifest["output_files"], "成功转换必须产出 dxf 与预览（Spec §3.1）")
        roles = set()
        for item in manifest["output_files"]:
            self.assertIn(item.get("role"), MANIFEST_OUTPUT_ROLES, "产物角色只能是 dxf/preview")
            roles.add(item["role"])
            path = root / item["filename"]
            self.assertTrue(path.exists(), "产物文件不存在：%s" % item["filename"])
            data = path.read_bytes()
            self.assertTrue(data, "产物不得为空：%s" % item["filename"])
            self.assertEqual(item.get("sha256"), hashlib.sha256(data).hexdigest())
            self.assertEqual(item.get("bytes"), len(data))
            self.assertEqual(manifest["output_sha256"].get(item["filename"]), item["sha256"])
        self.assertEqual(roles, {"dxf", "preview"}, "必须同时产出 DXF 与预览（Spec §3.1）")

    def test_b3_artifacts_are_bound_to_project_attachment_and_version(self):
        _, manifest, adapter = self.convert_ok(drawing_version=3, attachment_name="酒盒.dwg")
        self.assertEqual(manifest["project_id"], "dwg-conv-test")
        self.assertEqual(manifest["drawing_version"], 3)
        self.assertEqual(manifest["attachment_name"], "酒盒.dwg")
        self.assertEqual(manifest["original_filename"], WINE_BOX.name)
        self.assertNotIn("/", manifest["original_filename"])
        self.assertEqual(adapter.calls[0]["conversion_id"], manifest["conversion_id"],
                         "适配器记录的转换必须与 manifest 的 conversion_id 一致")

    def test_b4_manifests_are_readable_after_the_fact(self):
        _, manifest, _ = self.convert_ok()
        package = self.pkg()
        loaded = package.load_manifest(manifest["project_id"], manifest["conversion_id"])
        self.assertIsNotNone(loaded, "load_manifest() 必须能回看已完成的转换（Spec §2.1）")
        self.assertEqual(loaded["conversion_id"], manifest["conversion_id"])
        listed = package.list_conversions(manifest["project_id"])
        self.assertEqual([m["conversion_id"] for m in listed], [manifest["conversion_id"]])
        latest = package.latest_manifest(manifest["project_id"])
        self.assertEqual(latest["conversion_id"], manifest["conversion_id"])
        self.assertIsNone(package.load_manifest(manifest["project_id"], "no-such-conversion"))

    def test_b5_simulated_results_are_never_presented_as_real(self):
        _, manifest, _ = self.convert_ok()
        self.assertTrue(manifest.get("is_simulated"),
                        "fake 产出的 manifest 必须自报 is_simulated=True（Spec §3.2）")
        self.assertEqual(manifest.get("acceptance_level"), "orchestration_only")
        cap = self.pkg().capability()
        self.assertFalse(cap.get("dwg_supported"),
                         "即使 fake 可用，也不得宣称 DWG 已受支持（Spec §0）")
        self.assertEqual(cap.get("support_claim"), "orchestration_only")


# --------------------------------------------------------------------------- #
# C. 幂等、缓存与并发
# --------------------------------------------------------------------------- #
class CIdempotencyAndCache(AdapterCase):
    def test_c1_same_source_is_converted_only_once(self):
        self.use_env()
        self.memory_persistence()
        content = self.sample(WINE_BOX)
        adapter = self.fake()
        first = self.convert(content, adapter=adapter)
        second = self.convert(content, adapter=adapter)
        self.assertEqual(adapter.convert_calls, 1, "同一源文件不得重复调用适配器（Spec §4.2）")
        self.assertEqual(first["conversion_id"], second["conversion_id"])
        self.assertEqual(len(self.pkg().list_conversions(first["project_id"])), 1)

    def test_c2_converter_version_change_invalidates_the_cache(self):
        self.use_env()
        self.memory_persistence()
        content = self.sample(WINE_BOX)
        first = self.convert(content, adapter=self.fake(version="1.0.0"))
        second = self.convert(content, adapter=self.fake(version="2.0.0"))
        self.assertNotEqual(first["conversion_id"], second["conversion_id"],
                            "转换器版本变化后不得复用旧缓存（Spec §4.2）")
        self.assertNotEqual(first["cache_key"], second["cache_key"])
        self.assertEqual(len(self.pkg().list_conversions(first["project_id"])), 2)

    def test_c3_concurrent_requests_convert_once_and_share_the_result(self):
        self.use_env()
        self.memory_persistence()
        content = self.sample(ROUND_BOX)
        adapter = self.fake()
        results, errors = [], []

        def worker():
            try:
                results.append(self.convert(content, filename=ROUND_BOX.name, adapter=adapter))
            except BaseException as exc:  # noqa: BLE001 - 并发下只做分类
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [], "并发转换不得失败：%r" % errors)
        self.assertEqual(len(results), 8)
        ids = {item["conversion_id"] for item in results}
        self.assertEqual(len(ids), 1, "并发同一源文件必须复用同一个 conversion_id")
        self.assertEqual(adapter.convert_calls, 1, "并发下只允许一次真实转换（Spec §4.2）")
        self.assertEqual(len(self.pkg().list_conversions("dwg-conv-test")), 1)

    def test_c4_failure_never_overwrites_the_last_successful_artifacts(self):
        self.use_env()
        state = self.memory_persistence()
        content = self.sample(WINE_BOX)
        good = self.convert(content, adapter=self.fake())
        root = state["root"] / good["project_id"] / good["conversion_id"]
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
        self.expect_error("DWG_CONVERSION_FAILED", self.convert, content,
                          adapter=self.fake(failure_mode="nonzero_exit"))
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
        self.assertEqual(before, after, "失败的转换不得覆盖或删除上一次成功产物（Spec §4.2）")
        latest = self.pkg().latest_manifest(good["project_id"])
        self.assertEqual(latest["conversion_id"], good["conversion_id"])
        self.assertEqual(latest["status"], "ok")


# --------------------------------------------------------------------------- #
# D. 故障矩阵与可重试
# --------------------------------------------------------------------------- #
class DFaultMatrix(AdapterCase):
    EXPECTED = {"timeout": "DWG_CONVERSION_TIMEOUT",
                "nonzero_exit": "DWG_CONVERSION_FAILED",
                "missing_output": "DWG_CONVERTER_OUTPUT_MISSING",
                "empty_output": "DWG_CONVERTER_OUTPUT_INVALID",
                "oversized_output": "DWG_CONVERTER_OUTPUT_TOO_LARGE",
                "unsafe_path": "DWG_CONVERTER_UNSAFE_PATH"}

    def convert_with_failure(self, mode, **env):
        self.use_env(**env)
        state = self.memory_persistence()
        stderr = "token=sk-deadbeef path=/Users/tester/secret/oda" if mode == "nonzero_exit" else ""
        adapter = self.fake(failure_mode=mode, stderr=stderr)
        error = self.expect_error(self.EXPECTED[mode], self.convert,
                                  self.sample(WINE_BOX), adapter=adapter)
        return state, adapter, error

    def test_d1_failure_modes_are_a_closed_set(self):
        self.assertEqual(set(self.EXPECTED), FAILURE_MODES,
                         "Spec §2.4 的故障注入模式是闭集，测试不得私自扩项")
        with self.assertRaises(ValueError):
            self.fake(failure_mode="not-a-mode")

    def test_d2_timeout_has_its_own_stable_code(self):
        self.convert_with_failure("timeout", CAD_CONVERTER_TIMEOUT_SECONDS="0.3")

    def test_d3_nonzero_exit_is_a_retryable_conversion_failure(self):
        _, _, error = self.convert_with_failure("nonzero_exit")
        self.assertTrue(error.retryable)

    def test_d4_missing_output_is_not_a_success(self):
        self.convert_with_failure("missing_output")

    def test_d5_empty_output_is_rejected(self):
        self.convert_with_failure("empty_output")

    def test_d6_oversized_output_is_rejected(self):
        self.convert_with_failure("oversized_output", CAD_CONVERTER_MAX_OUTPUT_BYTES="8192")

    def test_d7_paths_outside_the_output_dir_are_rejected(self):
        state, _, error = self.convert_with_failure("unsafe_path")
        self.assertFalse(error.retryable)
        project_dir = state["root"] / "dwg-conv-test"
        written = [p for p in project_dir.rglob("*") if p.is_file()]
        self.assertEqual(written, [], "越界产物不得被收进正式产物目录（Spec §5）")
        self.addCleanup(shutil.rmtree,
                        pathlib.Path(tempfile.gettempdir()) / "dwg-conv-escape", True)

    def test_d8_errors_never_leak_secrets_stacks_or_paths(self):
        state, _, error = self.convert_with_failure("nonzero_exit")
        self.assertNotIn("sk-deadbeef", str(error.message))
        self.assertNotIn("/Users/", str(error.message))
        manifests = [v for (pid, _), v in state["manifests"].items() if pid == "dwg-conv-test"]
        self.assertTrue(manifests, "失败也必须留一条可审计的 failed manifest（Spec §3.2）")
        failed = [m for m in manifests if m.get("status") == "failed"]
        self.assertTrue(failed, "失败必须写成 status=failed 的 manifest")
        blob = json.dumps(failed, ensure_ascii=False)
        for banned in ("sk-deadbeef", "/Users/", "Traceback", "/opt/", "bearer"):
            self.assertNotIn(banned, blob, "manifest 不得记录密钥/堆栈/绝对路径")
        digest = failed[0].get("converter_stderr_digest")
        self.assertIsInstance(digest, str)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", digest),
                        "只允许存 stderr 摘要（sha256），不得存原文（Spec §5）")

    def test_d9_temporary_directories_are_cleaned_up(self):
        self.use_env()
        self.memory_persistence()
        temp_root = pathlib.Path(tempfile.gettempdir())
        before = {p.name for p in temp_root.glob("dwg-conv-*")}
        self.convert(self.sample(WINE_BOX), adapter=self.fake())
        self.expect_error("DWG_CONVERSION_FAILED", self.convert, self.sample(WINE_BOX),
                          adapter=self.fake(failure_mode="nonzero_exit"))
        after = {p.name for p in temp_root.glob("dwg-conv-*")}
        self.assertEqual(after - before, set(), "成功与失败都必须清理临时目录（Spec §5）")

    def test_d10_source_file_survives_failure_and_can_be_retried(self):
        self.use_env()
        self.memory_persistence()
        content = self.sample(ROUND_BOX)
        holder = pathlib.Path(tempfile.mkdtemp(prefix="dwg-adapter-src-"))
        self.addCleanup(shutil.rmtree, holder, True)
        source = holder / "source.dwg"
        source.write_bytes(content)
        self.expect_error("DWG_CONVERSION_FAILED", self.convert, source.read_bytes(),
                          filename=ROUND_BOX.name,
                          adapter=self.fake(failure_mode="nonzero_exit"))
        self.assertTrue(source.exists(), "失败不得删除原附件（Spec §4.2）")
        self.assertEqual(source.read_bytes(), content)
        manifest = self.convert(source.read_bytes(), filename=ROUND_BOX.name, adapter=self.fake())
        self.assertEqual(manifest["status"], "ok", "恢复转换器后同一附件必须能重试成功")


# --------------------------------------------------------------------------- #
# E. 安全硬约束
# --------------------------------------------------------------------------- #
class ESecurity(AdapterCase):
    MALICIOUS = {"../../etc/passwd.dwg": "passwd.dwg", "..\\..\\win.dwg": "win.dwg",
                 "/tmp/abs.dwg": "abs.dwg", "a\x00b.dwg": "ab.dwg"}

    def test_e1_malicious_filenames_never_escape_the_artifact_dir(self):
        self.use_env()
        state = self.memory_persistence()
        content = self.sample(WINE_BOX)
        for evil, expected in self.MALICIOUS.items():
            adapter = self.fake()
            manifest = self.convert(content, filename=evil, adapter=adapter)
            self.assertEqual(manifest["original_filename"], expected,
                             "原始文件名必须清洗成单一安全 basename（Spec §3.2）")
            self.assertEqual(adapter.calls[-1]["source_filename"], "source.dwg")
            for path in (state["root"] / manifest["project_id"]).rglob("*"):
                self.assertNotIn("..", path.parts, "产物路径不得出现上跳段")
                if path.is_file():
                    self.assertEqual(path.parent.name, manifest["conversion_id"],
                                     "产物必须落在该项目自己的转换目录里")

    def test_e2_no_shell_string_concatenation_in_the_package(self):
        offenders = []
        for path, text in self.package_sources().items():
            for banned in ("shell=True", "os.system(", "shell = True"):
                if banned in text:
                    offenders.append("%s:%s" % (path.name, banned))
        self.assertEqual(offenders, [],
                         "转换器调用必须用 argv 列表 + shell=False（Spec §5）：%s" % offenders)

    def test_e3_only_remote_adapters_may_touch_the_network(self):
        offenders = []
        for path, text in self.package_sources().items():
            if "remote" in path.name or "aps" in path.name:
                continue
            for banned in ("import requests", "urlopen(", "http.client", "import socket"):
                if banned in text:
                    offenders.append("%s:%s" % (path.name, banned))
        self.assertEqual(offenders, [], "本地适配器与编排层不得联网（Spec §5）：%s" % offenders)

    def test_e4_non_dwg_inputs_never_reach_the_converter(self):
        self.use_env()
        self.memory_persistence()
        for name, payload in (("drawing.png", PNG_BYTES), ("spec.pdf", PDF_BYTES),
                              ("model.step", STEP_BYTES)):
            adapter = self.fake()
            self.expect_error("FILE_FORMAT_UNSUPPORTED", self.convert, payload,
                              filename=name, adapter=adapter)
            self.assertEqual(adapter.convert_calls, 0,
                             "%s 不是 DWG，不得进转换器（Spec §5）" % name)

    def test_e5_audit_records_only_safe_fields(self):
        self.use_env()
        self.memory_persistence()
        store = importlib.import_module("tech_app.backend.storage.store")
        calls = []
        with mock.patch.object(store, "audit", lambda pid, action, detail=None: calls.append(
                {"project_id": pid, "action": action, "detail": detail})):
            self.convert(self.sample(WINE_BOX), adapter=self.fake())
        matches = [c for c in calls if "dwg" in str(c["action"])]
        self.assertTrue(matches, "转换必须写审计（Spec §3.2）：%r" % [c["action"] for c in calls])
        blob = json.dumps(matches, ensure_ascii=False)
        for banned in ("sk-", "/Users/", "Traceback", "base64"):
            self.assertNotIn(banned, blob, "审计不得记录密钥/绝对路径/堆栈/base64")
        self.assertIn("conversion_id", blob, "审计要能追到 conversion_id")


# --------------------------------------------------------------------------- #
# F. 能力对外可见（接口 + 前端不得写死）
# --------------------------------------------------------------------------- #
class FCapabilitySurface(AdapterCase):
    def test_f1_capability_is_exposed_by_api_and_health(self):
        self.use_env(CAD_CONVERTER="none")
        main = importlib.import_module("tech_app.backend.main")
        paths = {route.path for route in main.app.routes}
        self.assertIn(CAPABILITY_URL, paths,
                      "必须提供 %s 让前端问出转换能力（Spec §7）" % CAPABILITY_URL)
        health = main.health()
        self.assertIn("cad_converter", health, "/api/health 必须报告转换器能力（Spec §7）")
        block = health["cad_converter"]
        self.assertFalse(block.get("available"))
        self.assertEqual(block.get("stable_error_code"), "DWG_CONVERTER_NOT_INSTALLED")

    def test_f2_frontend_reads_the_capability_instead_of_hardcoding_it(self):
        candidates = {}
        for path in sorted(FRONTEND.glob("*.js")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if CAPABILITY_URL in text or "cad-converter" in text:
                candidates[path.name] = text
        self.assertTrue(candidates,
                        "前端必须从 %s 读取转换能力，不得写死「已安装」（Spec §7）" % CAPABILITY_URL)
        self.assertTrue(any("未安装" in text for text in candidates.values()),
                        "转换器不可用时前端必须显示「未安装」而不是「解析完成」")


# --------------------------------------------------------------------------- #
# G. 批次边界
# --------------------------------------------------------------------------- #
class GBatchBoundaries(AdapterCase):
    def test_g1_package_never_imports_vision_or_step_import(self):
        offenders = []
        for path, text in self.package_sources().items():
            for banned in ("import vision", "from . import vision", "qwen_client",
                           "claude_client", "step_import"):
                if banned in text:
                    offenders.append("%s:%s" % (path.name, banned))
        self.assertEqual(offenders, [],
                         "转换层不得依赖模型或 STEP 导入（Spec §5）：%s" % offenders)

    def test_g2_package_does_not_parse_dxf(self):
        offenders = [path.name for path, text in self.package_sources().items()
                     if "ezdxf" in text or "dxfgrabber" in text]
        self.assertEqual(offenders, [],
                         "DXF 实体解析属于第 3 批，本批不得引入（Spec §10）：%s" % offenders)


# --------------------------------------------------------------------------- #
# H. 两层验收（A 层必须过；B 层未装真实转换器时如实 skip）
# --------------------------------------------------------------------------- #
class HTwoLayerAcceptance(AdapterCase):
    def test_h1_real_converter_is_required_for_the_b_level_claim(self):
        self.use_env()
        package = self.pkg()
        cap = package.capability()
        if not cap.get("available") or cap.get("simulated"):
            self.skipTest("未安装真实 DWG 转换器：本机只能验收 A 层（编排层 + fake adapter），"
                          "B 层未验收 —— 不得宣称「支持 DWG」（Spec §0）")
        try:
            import ezdxf
        except ImportError:
            self.skipTest("缺少 ezdxf，无法校验真实 DXF 可打开性（第 3 批依赖）")
        for sample in (WINE_BOX, ROUND_BOX):
            content = self.sample(sample)
            state = self.memory_persistence()
            manifest = self.convert(content, filename=sample.name)
            self.assertFalse(manifest["is_simulated"])
            self.assertEqual(manifest["acceptance_level"], "real")
            root = state["root"] / manifest["project_id"] / manifest["conversion_id"]
            dxf = [item for item in manifest["output_files"] if item["role"] == "dxf"]
            previews = [item for item in manifest["output_files"] if item["role"] == "preview"]
            self.assertTrue(dxf and previews, "真实转换必须产出 DXF 与预览")
            doc = ezdxf.readfile(str(root / dxf[0]["filename"]))
            self.assertGreater(len(list(doc.modelspace())), 0, "真实 DXF 不得是空图")
            self.assertGreater(len(list(doc.layers)), 0, "真实 DXF 必须带图层")
            for item in previews:
                self.assertTrue((root / item["filename"]).read_bytes(), "预览不得为空")

    def test_h2_this_batch_never_claims_dwg_is_supported(self):
        self.use_env()
        package = self.pkg()
        cap = package.capability()
        self.assertFalse(cap.get("dwg_supported"),
                         "第 2 批无论是否装好转换器都不得宣称「支持 DWG」（Spec §0）")
        self.assertNotEqual(cap.get("support_claim"), "real")
        adapter = package.get_adapter()
        self.assertEqual(bool(cap.get("available")), adapter is not None,
                         "capability() 与 get_adapter() 必须口径一致")


# --------------------------------------------------------------------------- #
# I. 不回归（这两条现在就是绿的：锁住既有行为，不许被本批改坏）
# --------------------------------------------------------------------------- #
class INoRegression(AdapterCase):
    def test_i1_raster_path_still_builds_an_image_block(self):
        vision = importlib.import_module("tech_app.backend.services.vision")
        blocks = vision._base_blocks(PNG_BYTES, "drawing.png")
        kinds = [block.get("type") for block in blocks]
        self.assertTrue(
            any(kind in {"image", "_neutral_image", "image_url", "input_image"} for kind in kinds),
            "PNG 仍必须走既有视觉路径（本批不得改坏）：%r" % kinds)
        self.assertTrue(
            any(block.get("data") == PNG_BYTES for block in blocks),
            "PNG 字节仍必须原样进入内容块：%r" % kinds)

    def test_i2_frontend_still_admits_dwg_is_not_parsed_yet(self):
        source = (FRONTEND / "app.js").read_text(encoding="utf-8", errors="replace")
        self.assertTrue("解析不了" in source, "app.js 的诚实说明必须保留（第 1 批 Spec §6）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
