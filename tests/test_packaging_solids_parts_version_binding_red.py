"""红测：3D 结论（挤出体 / STL）必须认零件文档版本。

Spec：`docs/specs/packaging-solids-parts-version-binding.md`

现状缺口（代码级，四处，都可指到行）：
  · `main.py:8261`（单件）与 `main.py:8310-8314`（整份）两个写入口落库体都不带 `parts_id`，
    而同一批的**单件工艺**（`main.py:8012`）与**单件成本**（`main.py:8145`）都带了；
  · `main.py:6940 _packaging_solids_index()` 只贴 `solid_status` / `solid_reason`，不比对当前零件文档
    —— 零件重解析换了 `parts_id` 之后，2.1 列表照旧显示"这一件有 3D"；
  · `main.py:8265` 的 STL 下载只要 `status == "ok"` 且有 `stl` 就 200，响应头里没有任何版本信息；
  · 整份入口是**合并写**（`main.py:8305-8314` 按 `part_code` 覆盖/保留），不区分版本，
    重解析后上一版的件与新一版的件混在同一份 3D 文档里。

纪律：只读源码 + 假后端 / 假文档；不连 PG / SQLite 生产库、不发 HTTP、不写任何文件、不落库。
（`packaging_part_solids` 是**模块级**导入 `get_backend`，所以这里补的是它自己的模块属性 ——
补 `meta_backend.get_backend` 会打到真实 JSON 数据目录上。）
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import main                                        # noqa: E402
from tech_app.backend.services import packaging_part_solids as solids    # noqa: E402
from tech_app.backend.services import packaging_parts as parts           # noqa: E402

PID = "testpid00001"
OLD_PARTS_ID = "parts:oldoldoldoldold"
NEW_PARTS_ID = "parts:newnewnewnewnew"
NEW_PARTS_HASH = "hash-new"

CURRENT_DOC = {"parts": [{"part_code": "DWG-P01"}, {"part_code": "DWG-P02"}],
               "parts_id": NEW_PARTS_ID, "parts_hash": NEW_PARTS_HASH}
STORED_SOLIDS = {"parts": [{"part_code": "DWG-P01", "status": "ok", "reason": "",
                            "solid_status": "ok", "solid_reason": "", "stl": "solid dwg-p01"}],
                 "parts_id": OLD_PARTS_ID, "parts_hash": "hash-old", "solids_version": 3}


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, value in reversed(self.saved):
            setattr(owner, name, value)
        return False


def _index(record=STORED_SOLIDS, current=CURRENT_DOC):
    with _Patch((solids, "load_solids", lambda *a, **k: record),
                (parts, "load_parts", lambda *a, **k: current)):
        return main._packaging_solids_index(PID)


# --------------------------------------------------------------------------- #
# J 组：写入口必须把"照哪一版零件算的"落下去
# --------------------------------------------------------------------------- #
class JWritePathsRecordPartsVersion(unittest.TestCase):
    def test_j1_whole_document_entry_records_the_current_parts_id(self):
        captured: dict = {}

        def fake_save(project_id, doc):
            captured.update(doc)
            return {"version": 1}

        with _Patch((main, "_require", lambda *a, **k: None),
                    (main, "_workflow_project", lambda *a, **k: None),
                    (parts, "load_parts", lambda *a, **k: dict(CURRENT_DOC)),
                    (solids, "extrude_all",
                     lambda rows: {"parts": [{"part_code": "DWG-P01", "status": "ok"}],
                                   "stats": {"part_total": 1, "ok_total": 1}}),
                    (solids, "load_solids", lambda *a, **k: None),
                    (solids, "save_solids", fake_save),
                    (main.store, "audit", lambda *a, **k: None)):
            main.packaging_parts_solids(PID, {"username": "PE1"})

        self.assertEqual(NEW_PARTS_ID, captured.get("parts_id"),
                         "整份 3D 入口落库时必须记下当前零件文档版本（Spec §2.2）："
                         "今天落库体只有 engine_version / stats / parts —— 重解析后没人说得清"
                         "这份 3D 是哪一版零件算的")
        self.assertEqual(NEW_PARTS_HASH, captured.get("parts_hash"),
                         "`parts_hash` 也要落下去（与单件工艺/成本同一口径）")
        self.assertEqual(NEW_PARTS_ID,
                         (captured.get("parts") or [{}])[0].get("parts_id"),
                         "每件结论也要带 `parts_id`：整份入口是合并写，只有逐件带版本才分得清混没混")


# --------------------------------------------------------------------------- #
# J 组：读侧必须比对（比较不了 ≠ 过期）
# --------------------------------------------------------------------------- #
class JReadSideDisclosesStale(unittest.TestCase):
    def test_j2_reparsed_parts_make_the_solid_stale(self):
        index = _index()
        entry = index.get("DWG-P01") or {}
        self.assertIs(True, entry.get("stale"),
                      "结论是旧版零件算的、当前零件文档已换版 —— 列表行必须能看出（Spec §2.3）："
                      "现在只贴 solid_status，页面照旧显示'这一件有 3D'")
        self.assertEqual("parts_reparsed", entry.get("stale_reason"),
                         "换版要给自己稳定的原因码 parts_reparsed")
        self.assertEqual(OLD_PARTS_ID, entry.get("parts_id"),
                         "索引项要带出结论那一版的 `parts_id`，人才对得上账")

    def test_j3_missing_parts_document_is_unknown_not_stale(self):
        entry = (_index(current=None).get("DWG-P01") or {})
        self.assertEqual("parts_unknown", entry.get("stale_reason"),
                         "当前零件文档读不到时'比较不了'，不许断言过期（Spec §2.3）")
        self.assertIsNot(True, entry.get("stale"),
                         "`parts_unknown` 时 `stale` 不许为 true")

    def test_j4_stl_download_still_200_but_carries_the_version(self):
        with _Patch((main, "_workflow_project", lambda *a, **k: None),
                    (solids, "load_solids", lambda *a, **k: dict(STORED_SOLIDS)),
                    (parts, "load_parts", lambda *a, **k: dict(CURRENT_DOC))):
            resp = main.get_packaging_part_solid_stl(PID, "DWG-P01")
        headers = {str(key).lower(): str(value) for key, value in resp.headers.items()}
        self.assertEqual(200, resp.status_code, "文件还在，下载照旧 200（Spec §2.4）")
        self.assertEqual(OLD_PARTS_ID, headers.get("x-packaging-parts-id"),
                         "下载必须能看出这是哪一版零件算的（Spec §2.4）")
        self.assertEqual("1", headers.get("x-packaging-parts-stale"),
                         "过期要标记（`X-Packaging-Parts-Stale`），不许悄悄给一个旧文件")


# --------------------------------------------------------------------------- #
# J 组（护栏）：现有口径不许被本批改掉
# --------------------------------------------------------------------------- #
class JExistingContractUnchanged(unittest.TestCase):
    def test_j5_storage_roundtrip_keeps_parts_id_verbatim(self):
        class _Backend:
            def __init__(self):
                self.doc = {}

            def get_doc(self, project_id, key):
                return self.doc

            def put_doc(self, project_id, key, doc):
                self.doc = doc
                return {"ok": True}

        backend = _Backend()
        with _Patch((solids, "get_backend", lambda: backend)):
            saved = solids.save_solids(PID, {"engine_version": solids.ENGINE_VERSION,
                                             "parts_id": NEW_PARTS_ID,
                                             "parts_hash": NEW_PARTS_HASH,
                                             "parts": []})
            record = solids.load_solids(PID)
        self.assertEqual(NEW_PARTS_ID, saved["doc"].get("parts_id"),
                         "存储层是整份文档原样存取，本批不改它的语义")
        self.assertEqual(NEW_PARTS_ID, (record or {}).get("parts_id"),
                         "读回也必须原样带出（护栏：证明缺口在调用方，不在存储层）")

    def test_j6_existing_index_keys_are_verbatim(self):
        entry = _index().get("DWG-P01") or {}
        self.assertEqual("ok", entry.get("solid_status"), "既有键逐字不变")
        self.assertEqual("", entry.get("solid_reason"), "既有键逐字不变")


if __name__ == "__main__":
    unittest.main()
