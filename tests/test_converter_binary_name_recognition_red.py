"""守卫：AppImage 形态的转换器入口（34 的 ODA 就叫 `AppRun`）必须被认出来。

Spec：`docs/specs/converter-binary-name-recognition.md`
依赖：`tech_app/backend/services/cad_converter/adapters/local_cli.py` 的 `driver_of()` /
`provider_of_binary()`，以及 `service._resolve_chain()`。

现状缺口（34 真机实测 2026-09-22）：`DWG_CONVERTER_BINARY=/home/data/cpq-tools/
oda-file-converter-27.1/squashfs-root/AppRun` 的 basename 不含 "oda"，`provider_of_binary()` 回空 →
`_resolve_chain()` 把「argv 形状未经真机验证」塞进 warnings（用户每次解析都看到），
而同一份结果里 `argv_verified` 又是 true —— 事实自相矛盾。

纪律：不改本文件换绿；不连库、不联网、不启动服务；只在临时目录里造同名假入口（不装任何转换器）。
"""
from __future__ import annotations

import os
import pathlib
import stat
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services.cad_converter import service as converter_service      # noqa: E402
from tech_app.backend.services.cad_converter.adapters import local_cli               # noqa: E402

CONVERTER_KEYS = (
    "DWG_CONVERTER_PROVIDER", "DWG_CONVERTER_BINARY", "DWG_CONVERTER_VERSION",
    "DWG_CONVERTER_WRAPPER", "DWG_CONVERTER_PREVIEW_BINARY",
    "DWG_CONVERTER_FALLBACK_PROVIDER", "DWG_CONVERTER_FALLBACK_BINARY",
    "DWG_CONVERTER_FALLBACK_VERSION", "DWG_CONVERTER_FALLBACK_PREVIEW_BINARY",
    "DWG_CONVERTER_FALLBACK_WRAPPER",
    "CAD_CONVERTER",
)
ODA_APPIMAGE = "/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun"
FAKE_WARNING_MARK = "未经真机验证"


class Case(unittest.TestCase):
    def use_env(self, **overrides):
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

    def fake_entry(self, *parts, name="AppRun"):
        """在临时目录里造一个可执行假入口，返回它的绝对路径。"""
        holder = pathlib.Path(tempfile.mkdtemp(prefix="conv-entry-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(holder, True))
        entry = holder.joinpath(*parts, name)
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        entry.chmod(entry.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return entry


# --------------------------------------------------------------------------- #
# A. 名字判定（C1 / C2 / C5）
# --------------------------------------------------------------------------- #
class ANameRecognition(Case):
    def test_a1_oda_appimage_path_is_oda(self):
        self.assertEqual(local_cli.provider_of_binary(ODA_APPIMAGE), "oda", "Spec C2")
        self.assertEqual(local_cli.driver_of("oda", ODA_APPIMAGE), "oda_file_converter", "Spec C2")

    def test_a2_teigha_appimage_path_is_oda_driver(self):
        self.assertEqual(local_cli.provider_of_binary("/opt/teigha-2024/AppRun"), "oda", "Spec C1")
        self.assertEqual(local_cli.driver_of("", "/opt/teigha-2024/AppRun"), "oda_file_converter")

    def test_a3_libredwg_appimage_path_is_libredwg(self):
        self.assertEqual(local_cli.provider_of_binary("/opt/libredwg-0.14/x/AppRun"), "libredwg")
        self.assertEqual(local_cli.driver_of("", "/opt/libredwg-0.14/x/AppRun"), "libredwg_dwg2dxf")

    def test_a4_marker_is_prefix_of_a_path_component(self):
        self.assertEqual(local_cli.provider_of_binary("/tmp/soda/AppRun"), "",
                         "Spec C1：`soda` 不是 `oda` 安装目录，不许误认")
        self.assertEqual(local_cli.provider_of_binary("/home/x/AppRun"), "",
                         "Spec C5：无标记路径下的 AppRun 回落到显式 provider")

    def test_a5_existing_filename_rules_unchanged(self):
        cases = {
            "/usr/local/bin/ODAFileConverter": "oda",
            "/usr/local/bin/TeighaFileConverter": "oda",
            "/usr/local/bin/dwg2dxf": "libredwg",
            "/usr/local/bin/dwgread": "libredwg-cli",
            "/usr/local/bin/something-else": "",
        }
        for path, expected in cases.items():
            self.assertEqual(local_cli.provider_of_binary(path), expected, "Spec C5：%s" % path)


# --------------------------------------------------------------------------- #
# B. 转换链（C3 / C4）
# --------------------------------------------------------------------------- #
class BChainWarnings(Case):
    def entry(self):
        return self.fake_entry("oda-file-converter-27.1", "squashfs-root")

    def test_b1_appimage_entry_produces_no_false_warning(self):
        entry = pathlib.Path(ODA_APPIMAGE)
        if not entry.exists():
            entry = self.entry()
        self.use_env(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(entry),
                     DWG_CONVERTER_VERSION="27.1", DWG_CONVERTER_FALLBACK_PROVIDER="none")
        chain = converter_service._resolve_chain()
        primary = chain["primary"]
        self.assertEqual(primary["provider"], "oda", primary)
        self.assertEqual(primary["driver"], "oda_file_converter", primary)
        self.assertEqual(primary["note"], "", "Spec C3：AppImage 入口不许再报「认不出」")
        self.assertTrue(primary["argv_verified"], "Spec C3：argv 仍是已验证的那一套")
        self.assertEqual([w for w in chain["warnings"] if FAKE_WARNING_MARK in w], [],
                         "Spec C3：警告里不许再出现「未经真机验证」：%r" % chain["warnings"])

    def test_b2_unknown_binary_still_warns(self):
        entry = self.fake_entry("cpq-tools", name="mydwg")
        self.use_env(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(entry),
                     DWG_CONVERTER_VERSION="27.1", DWG_CONVERTER_FALLBACK_PROVIDER="none")
        chain = converter_service._resolve_chain()
        self.assertTrue(chain["primary"]["note"], "Spec C4：真认不出必须仍然告警（不许静默）")
        self.assertTrue([w for w in chain["warnings"] if FAKE_WARNING_MARK in w], chain["warnings"])

    def test_b3_auto_provider_appimage_still_maps_to_oda_driver(self):
        # provider 字段本身按配置原样保留（`auto`）是既有口径，本批不动；要保证的是
        # **driver 被认出、note 不再误报**（Spec C1/C3，边界 §3）。
        entry = self.fake_entry("oda-file-converter-27.1", "squashfs-root")
        self.use_env(DWG_CONVERTER_PROVIDER="auto", DWG_CONVERTER_BINARY=str(entry),
                     DWG_CONVERTER_VERSION="27.1", DWG_CONVERTER_FALLBACK_PROVIDER="none")
        chain = converter_service._resolve_chain()
        primary = chain["primary"]
        self.assertEqual(primary["driver"], "oda_file_converter", primary)
        self.assertEqual(primary["note"], "", primary)
        self.assertTrue(primary["argv_verified"], primary)

    def test_b4_capability_declares_appimage_as_oda(self):
        entry = self.fake_entry("oda-file-converter-27.1", "squashfs-root")
        self.use_env(DWG_CONVERTER_PROVIDER="oda", DWG_CONVERTER_BINARY=str(entry),
                     DWG_CONVERTER_VERSION="27.1", DWG_CONVERTER_FALLBACK_PROVIDER="none")
        cap = converter_service.capability()
        self.assertEqual(cap.get("adapter_name"), "oda", cap)
        self.assertTrue(cap.get("argv_verified"), cap)
        self.assertEqual(cap.get("binary_reason"), "", cap)


if __name__ == "__main__":
    unittest.main()
