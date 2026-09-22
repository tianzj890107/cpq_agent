"""守卫：转换缓存的 `cache_key` 必须带「引擎身份」，不然改了代码旧 manifest 会把旧话接着说。

Spec：`docs/specs/converter-cache-engine-identity.md`
依赖：`tech_app/backend/services/cad_converter/service.py`（`CHAIN_ENGINE_VERSION` /
`_chain_fingerprint()` / `convert_drawing()` / `_cached_manifest()`）。

现状缺口（34 真机实测 2026-09-22）：`## 297` 修掉「AppImage 入口认不出 → 假告警」并部署后，
34 解析真实 `酒盒.dwg` 仍回那条假告警 —— 因为 `tech_app/tech_data/cpq-unified-parse/
conversions/manifests.json` 里 09-21 写的旧 manifest 被幂等复用（`cache_key` 只认转换器身份与
options，代码变了它不变）。

纪律：不改本文件换绿；用内存 + 临时目录替换 persistence，不写真实 tech_data；不连库、不联网。
"""
from __future__ import annotations

import hashlib
import importlib
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

SERVICE = "tech_app.backend.services.cad_converter.service"
PERSISTENCE = "tech_app.backend.services.cad_converter.persistence"
FAKE = "tech_app.backend.services.cad_converter.adapters.fake"

PROJECT_ID = "cache-engine-test"
ENV = {"APP_ENV": "test", "DWG_CONVERTER_PROVIDER": "fake", "DWG_CONVERTER_VERSION": "1.0.0"}


def synthetic_dwg() -> bytes:
    """合成一份通过 `file_preflight` 结构校验的 DWG 头（R2004+ 要 0x80 处的加密哨兵）。

    只是让预检放行：真正的产物由 FakeAdapter 产出，不依赖客户样本（样本不入库）。
    """
    preflight = importlib.import_module("tech_app.backend.services.file_preflight")
    sentinel = bytes(getattr(preflight, "_DWG_SENTINEL"))
    mask = bytes(getattr(preflight, "_DWG_R2004_MASK"))
    offset = int(getattr(preflight, "_DWG_SENTINEL_OFFSET"))
    head = b"AC1018" + b"\x00" * (offset - len(b"AC1018"))
    encoded = bytes(sentinel[i] ^ mask[i] for i in range(len(sentinel))) + b"\x00\x00\x00\x00"
    return head + encoded + b"\x00" * 2048


CONTENT = synthetic_dwg()


def service_module():
    return importlib.import_module(SERVICE)


class Case(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {k: str(v) for k, v in ENV.items()})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = service_module()
        self.state = self.memory_persistence()

    def memory_persistence(self):
        persistence = importlib.import_module(PERSISTENCE)
        root = pathlib.Path(tempfile.mkdtemp(prefix="cache-engine-"))
        self.addCleanup(shutil.rmtree, root, True)
        state = {"root": root, "manifests": {}, "seq": 0, "calls": 0}
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

        for name, fn in (("artifact_dir", artifact_dir), ("save_artifact", save_artifact),
                         ("save_manifest", save_manifest), ("load_manifest", load_manifest),
                         ("list_manifests", list_manifests), ("sync", lambda *a: None)):
            if not hasattr(persistence, name):
                self.fail("cad_converter.persistence 必须提供 %s()" % name)
            patcher = mock.patch.object(persistence, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        return state

    def adapter(self):
        cls = getattr(importlib.import_module(FAKE), "FakeAdapter", None)
        self.assertTrue(callable(cls), "adapters/fake.py 必须提供 FakeAdapter")
        return cls()

    def convert(self, adapter):
        return self.service.convert_drawing(PROJECT_ID, "酒盒.dwg", CONTENT,
                                           adapter=adapter, attachment_name="酒盒.dwg")

    def with_engine(self, version):
        patcher = mock.patch.object(self.service, "CHAIN_ENGINE_VERSION", version)
        patcher.start()
        self.addCleanup(patcher.stop)


# --------------------------------------------------------------------------- #
# A. 常量与身份段（C1 / C2）
# --------------------------------------------------------------------------- #
class AEngineIdentity(Case):
    def test_a1_constant_exists_and_is_version_shaped(self):
        value = getattr(self.service, "CHAIN_ENGINE_VERSION", None)
        self.assertIsInstance(value, str, "Spec C1：CHAIN_ENGINE_VERSION 必须导出且是字符串")
        self.assertRegex(value, r"^cad-converter-chain/\d+$",
                         "Spec C1：形如 cad-converter-chain/<n>：%r" % value)

    def test_a2_fingerprint_starts_with_the_engine_identity(self):
        primary = {"provider": "oda", "converter_version": "27.1", "binary_sha256": "deadbeef"}
        fingerprint = self.service._chain_fingerprint(primary, {}, False)
        self.assertTrue(fingerprint.startswith(self.service.CHAIN_ENGINE_VERSION),
                        "Spec C2：身份段首段必须是引擎身份：%r" % fingerprint)
        self.assertIn("oda", fingerprint)
        self.assertIn("27.1", fingerprint)

    def test_a3_bumping_the_engine_changes_the_fingerprint(self):
        primary = {"provider": "oda", "converter_version": "27.1", "binary_sha256": "deadbeef"}
        before = self.service._chain_fingerprint(primary, {}, False)
        self.with_engine("cad-converter-chain/999")
        after = self.service._chain_fingerprint(primary, {}, False)
        self.assertNotEqual(before, after, "Spec C2：引擎身份必须真的进身份段")


# --------------------------------------------------------------------------- #
# B. 幂等与失效（C3 / C4 / C5）
# --------------------------------------------------------------------------- #
class BCacheBehaviour(Case):
    def test_b1_same_engine_keeps_idempotency(self):
        adapter = self.adapter()
        first = self.convert(adapter)
        second = self.convert(adapter)
        self.assertEqual(adapter.convert_calls, 1, "Spec C4：同引擎必须仍然幂等复用")
        self.assertEqual(first["cache_key"], second["cache_key"], "Spec C4")
        self.assertEqual(first["conversion_id"], second["conversion_id"], "Spec C4")

    def test_b2_engine_change_invalidates_the_cache(self):
        adapter_a = self.adapter()
        self.with_engine("cad-converter-chain/1")
        first = self.convert(adapter_a)
        adapter_b = self.adapter()
        self.with_engine("cad-converter-chain/2")
        second = self.convert(adapter_b)
        self.assertNotEqual(first["cache_key"], second["cache_key"],
                            "Spec C3：引擎身份变了 cache_key 必须变")
        self.assertEqual(adapter_b.convert_calls, 1,
                         "Spec C3：引擎身份变了必须真的重跑，不许复用旧 manifest")

    def test_b3_engine_change_keeps_the_artifact_identity(self):
        adapter_a = self.adapter()
        self.with_engine("cad-converter-chain/1")
        first = self.convert(adapter_a)
        self.with_engine("cad-converter-chain/2")
        second = self.convert(self.adapter())
        self.assertEqual(first["conversion_id"], second["conversion_id"],
                         "Spec C5：换引擎重跑仍写回同一个 conversion_id（不堆第二份产物目录）")

    def test_b4_engine_identity_is_auditable_in_the_manifest(self):
        manifest = self.convert(self.adapter())
        chain = (manifest.get("conversion_options") or {}).get("converter_chain")
        self.assertIsInstance(chain, str, "Spec C2：converter_chain 必须是字符串")
        self.assertTrue(chain.startswith(self.service.CHAIN_ENGINE_VERSION),
                        "Spec C2：manifest 里也要看得见引擎身份：%r" % chain)


if __name__ == "__main__":
    unittest.main()
