"""红测：包装 BOM 与零件文档的版本绑定（重解析后 BOM 不许静默指旧图）。

Spec：`docs/specs/packaging-bom-parts-version-binding.md`

现状缺口（代码级，两处，都可指到行）：
  · `packaging_parts.py:2366 bind_rows()` 的 `size_binding` **不带** `parts_id` / `parts_hash` ——
    落库那一份没有任何地方说明"这一对尺寸是照哪一版零件文档配的"；
    `grep -c "parts_hash\\|parts_id" tech_app/backend/services/packaging_bom.py` → 0。
  · `packaging_bom.py:921 load_bom()` 不比对这些版本：重解析出**新**零件文档后，
    行上的旧尺寸、旧的 `component_id` 照旧以 `status="computed"` / `missing_variables=[]`
    返回，2.1 的零件树与 BOM 行互相打架，而且没有任何地方说得出
    "这些行指着一份已经不存在的零件文档"（点进零件明细还会找不到那一件）。

纪律：只读源码 + 纯函数 / 假后端；不连 PG、不发 HTTP、不写任何文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_bom as bom            # noqa: E402
from tech_app.backend.services import packaging_parts as parts        # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-VERSION-001"

CURRENT_ID = "parts:bbbbbbbbbbbbbbbb"
CURRENT_HASH = "hash-v2-new-drawing"
OLD_HASH = "hash-v1-old-drawing"

PART_ROW = {
    "part_code": "DWG-P01", "component_id": "cmp:50", "material": "300g双铜哑胶",
    "unfolded_length_mm": 440.123, "unfolded_width_mm": 482.92,
    "size_source": "dwg_outline", "outline_status": "closed",
}

#: 入参 `parts` 就是 `save_parts()` 落的那份整文档（顶层带 parts_id / parts_hash）。
PARTS_DOC = {"parts": [dict(PART_ROW)], "parts_id": CURRENT_ID, "parts_hash": CURRENT_HASH,
             "stats": {"part_total": 1}}

BINDABLE_ROW = {
    "item_key": "WINE-P01", "bom_category": "box_part", "item_name": "左盖面纸",
    "status": "needs_input", "locked": 0, "length_mm": None, "width_mm": None,
    "material": "300g双铜哑胶", "missing_variables": ["inner_length"],
}


def _db_row(item_key: str, binding=None, category: str = "box_part") -> dict:
    """落库回来的行：`size_source_json` 是 DB 里的 JSON 文本（`_item_out()` 这么读）。"""
    source = {}
    if binding is not None:
        source["dwg_binding"] = dict(binding)
    return {"item_key": item_key, "bom_category": category, "status": "computed",
            "length_mm": 440.123, "width_mm": 482.92, "locked": 0,
            "material": "300g双铜哑胶", "material_code": "",
            "size_source": "dwg_parts",
            "size_source_json": json.dumps(source, ensure_ascii=False)}


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


# --------------------------------------------------------------------------- #
# E 组：绑出来的行必须带上是照哪一版零件文档配的
# --------------------------------------------------------------------------- #
class EBindingCarriesDocumentVersion(unittest.TestCase):
    def test_e1_binding_records_the_parts_document_version(self):
        result = parts.bind_rows([dict(BINDABLE_ROW)], dict(PARTS_DOC))
        row = result["items"][0]
        binding = (row.get("size_source") or {}).get("dwg_binding") or {}
        self.assertEqual(CURRENT_ID, binding.get("parts_id"),
                         "行上的绑定留痕必须带零件文档版本（Spec §2.1）：现在一个字都没有，"
                         "重解析之后没人能说出这一对尺寸是照哪一版图纸配的")
        self.assertEqual(CURRENT_HASH, binding.get("parts_hash"),
                         "`parts_hash` 必须逐字等于入参文档那一份（不许自己算/编）")
        self.assertEqual(CURRENT_ID, result.get("parts_id"),
                         "返回体顶层也要带一份文档身份（落库/比对用，Spec §2.1）")
        self.assertEqual(CURRENT_HASH, result.get("parts_hash"),
                         "返回体顶层的 `parts_hash` 必须逐字等于入参文档那一份")

    def test_e2_existing_binding_contract_is_unchanged(self):
        result = parts.bind_rows([dict(BINDABLE_ROW)], dict(PARTS_DOC))
        row = result["items"][0]
        self.assertEqual(1, result.get("bound"), "绑定的行数口径逐字不变")
        self.assertEqual("dwg_parts", row.get("source"), "来源标记逐字不变")
        self.assertEqual([], row.get("missing_variables"), "回填后缺变量清空的口径逐字不变")
        self.assertEqual(440.123, row.get("length_mm"), "回填的尺寸逐字不变")
        self.assertEqual("DWG-P01", ((row.get("size_source") or {}).get("dwg_binding")
                                     or {}).get("part_code"), "part_code 留痕逐字不变")


# --------------------------------------------------------------------------- #
# F 组：读接口必须比对版本并披露（比较不了 ≠ 不一致）
# --------------------------------------------------------------------------- #
class FLoadBomDisclosesVersionDrift(unittest.TestCase):
    def _load(self, rows, doc=None, error=None):
        def loader(*args, **kwargs):
            if error:
                raise error
            return doc

        with _Patch((bom.da_repo, "load_packaging_bom", lambda *a, **k: rows),
                    (bom.da_repo, "load_box_match", lambda *a, **k: {}),
                    (bom, "_load_pairing_review", lambda *a, **k: []),
                    (bom, "_role_scope", lambda *a, **k: {"items": [], "unbound_total": 0}),
                    (parts, "load_parts", loader)):
            return bom.load_bom(PID, REQ_NO)

    def test_f1_row_bound_to_an_older_document_is_reported(self):
        doc = self._load([_db_row("WINE-P01", {"part_code": "DWG-P01",
                                              "parts_hash": OLD_HASH})],
                         doc=dict(PARTS_DOC))
        stale = doc.get("parts_binding_stale") or []
        self.assertEqual(1, len(stale),
                         "行绑的是旧版零件文档、当前文档已经换版 —— 必须报出来（Spec §2.2）："
                         "现在读接口一个字都不说，旧尺寸照旧以 computed 返回")
        entry = stale[0]
        self.assertEqual("WINE-P01", entry.get("item_key"))
        self.assertEqual("parts_reparsed", entry.get("reason"),
                         "换版要给自己稳定的原因码 parts_reparsed")
        self.assertEqual(OLD_HASH, entry.get("bound_parts_hash"))
        self.assertEqual(CURRENT_HASH, entry.get("current_parts_hash"))
        versions = doc.get("source_versions") or {}
        self.assertEqual(CURRENT_HASH, versions.get("parts_hash"),
                         "`source_versions` 必须带出**当前**零件文档版本（Spec §2.2）")
        self.assertEqual(CURRENT_ID, versions.get("parts_id"))

    def test_f2_binding_without_a_version_is_not_silently_fine(self):
        doc = self._load([_db_row("WINE-P01", {"part_code": "DWG-P01"})],
                         doc=dict(PARTS_DOC))
        stale = doc.get("parts_binding_stale") or []
        self.assertEqual(1, len(stale),
                         "本批之前落库的行有 binding 但没有版本 —— 无从判断不等于没问题，"
                         "必须进清单（Spec §2.2）")
        self.assertEqual("binding_without_version", stale[0].get("reason"),
                         "历史行要单独给码，不能和 parts_reparsed 混成一个（处置话术不同）")
        self.assertEqual("", stale[0].get("bound_parts_hash"))

    def test_f3_matching_versions_are_not_flagged(self):
        doc = self._load([_db_row("WINE-P01", {"part_code": "DWG-P01",
                                              "parts_hash": CURRENT_HASH})],
                         doc=dict(PARTS_DOC))
        self.assertEqual([], doc.get("parts_binding_stale") or [],
                         "版本一致时清单必须是 []（不许常驻非空，否则等于没披露）")

    def test_f4_document_unreadable_is_unknown_not_a_verdict(self):
        rows = [_db_row("WINE-P01", {"part_code": "DWG-P01", "parts_hash": OLD_HASH})]
        broken = self._load(rows, error=RuntimeError("parts store down"))
        flag = broken.get("parts_document_unavailable") or {}
        self.assertEqual("parts_document_unavailable", flag.get("code"),
                         "当前零件文档读不到必须显式披露（Spec §2.2）："
                         "现在读接口连读都没读，旧尺寸照旧当已确认结果用")
        self.assertEqual([], broken.get("parts_binding_stale") or [],
                         "读不到文档时**比较不了**，不许逐行断言谁过期（Spec §2.2）")

        healthy = self._load(rows, doc=dict(PARTS_DOC))
        for key in ("items", "gaps", "stats"):
            self.assertEqual(healthy.get(key), broken.get(key),
                             "本批只要求披露：`%s` 不许因为读不到零件文档而变化（Spec §3）" % key)

    def test_f5_rows_without_a_dwg_binding_are_not_flagged(self):
        rows = [_db_row("WINE-P01", None),
                _db_row("MAT-01", None, category="material")]
        doc = self._load(rows, doc=dict(PARTS_DOC))
        self.assertEqual([], doc.get("parts_binding_stale") or [],
                         "模板行 / 人工行 / 材料行没有 `dwg_binding`，不许被标成过期（Spec §2.2）")


if __name__ == "__main__":
    unittest.main()
