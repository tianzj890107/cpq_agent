"""红测：BOM 行必须认自己的盒型（换盒型重算后，旧锁定行不许冒充新盒型的部件）。

Spec：`docs/specs/packaging-bom-box-type-provenance.md`

现状缺口（代码级，三处，都可指到行）：
  · `packaging_bom.py:406 _assemble()` 造的六组行**都没有盒型**，
    落库列清单 `da_repo.py:731 _PACKAGING_BOM_COLUMNS` 也没有 `box_type_code`；
  · `packaging_bom.py:926-929 load_bom()` 用**成品行**的 `item_key` 当整份 BOM 的盒型，
    不问其余行属于谁；而 `da_repo.py:748 save_packaging_bom()` 只删未锁定行，
    锁定行"原样保留" —— 换盒型重算后，上一版盒型的锁定行静默混进新盒型的 BOM，
    报价与人工角色映射都按新盒型对待它们；
  · 本批之前落库的行没有盒型字段：既说不上属于哪个盒型，也说不上"无从判断"。

纪律：只读源码 + 假仓库；不连 PG / SQLite 生产库、不发 HTTP、不写任何文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_bom as bom            # noqa: E402
from tech_app.backend.services import packaging_parts as parts        # noqa: E402
from tech_app.backend.storage import da_db, da_repo                   # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-BOXTYPE-001"
BOX_CODE = "YT-NEW"
OLD_BOX_CODE = "YT-OLD"


def _db_row(item_key: str, category: str = "box_part", box_type=None,
            status: str = "computed") -> dict:
    row = {"item_key": item_key, "bom_category": category, "status": status, "locked": 0,
           "length_mm": 440.123, "width_mm": 482.92, "material": "灰板",
           "material_code": "", "size_source_json": "{}"}
    if box_type is not None:
        row["box_type_code"] = box_type
    return row


BOX_MATCH = {"confirmed_box_type": BOX_CODE, "decision": "confirmed",
             "engine_version": "match-v1", "confirmed_by": "PE1",
             "confirmed_at": "2026-09-22 10:00:00"}


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


def _load(rows):
    with _Patch((bom.da_repo, "load_packaging_bom", lambda *a, **k: rows),
                (bom.da_repo, "load_box_match", lambda *a, **k: dict(BOX_MATCH)),
                (bom, "_load_pairing_review", lambda *a, **k: []),
                (bom, "_role_scope", lambda *a, **k: {"items": [], "unbound_total": 0}),
                (parts, "load_parts", lambda *a, **k: None)):
        return bom.load_bom(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# I 组：混盒型的 BOM 必须报出来（但不许删行）
# --------------------------------------------------------------------------- #
class IMixedBoxTypes(unittest.TestCase):
    def _mixed_rows(self):
        return [_db_row(BOX_CODE, category="finished", box_type=BOX_CODE),
                _db_row("RB02001-P02", box_type=OLD_BOX_CODE),
                _db_row("RB02001-P03", box_type=OLD_BOX_CODE),
                _db_row("MAT-01", category="material", box_type=BOX_CODE)]

    def test_i1_rows_from_another_box_type_are_reported(self):
        doc = _load(self._mixed_rows())
        self.assertEqual(["RB02001-P02", "RB02001-P03"],
                         doc.get("rows_from_other_box_type"),
                         "换盒型重算后，上一版盒型留下来的那几行必须被报出来（Spec §2.3）："
                         "今天它们被当成新盒型的部件，报价与人工角色映射照着错的前提走")
        codes = (doc.get("source_versions") or {}).get("box_type_codes")
        self.assertEqual([BOX_CODE, OLD_BOX_CODE], codes,
                         "整份 BOM 里出现过哪些盒型要去重升序列出来（Spec §2.3）")

    def test_i2_single_box_type_keeps_the_list_empty(self):
        rows = [_db_row(BOX_CODE, category="finished", box_type=BOX_CODE),
                _db_row("RB02001-P02", box_type=BOX_CODE),
                _db_row("MAT-01", category="material", box_type=BOX_CODE)]
        doc = _load(rows)
        self.assertEqual([], doc.get("rows_from_other_box_type") or [],
                         "没有混盒型时清单必须是 []（不许常驻非空，否则等于没披露）")

    def test_i3_rows_without_a_box_type_are_not_treated_as_current(self):
        rows = [_db_row(BOX_CODE, category="finished", box_type=BOX_CODE),
                _db_row("RB02001-P02"),
                _db_row("MAT-01", category="material")]
        doc = _load(rows)
        self.assertEqual(["MAT-01", "RB02001-P02"],
                         doc.get("rows_without_box_type"),
                         "本批之前落的历史行没有盒型字段 —— '无从判断'不许当成'同盒型'（Spec §2.3）")

    def test_i4_existing_keys_are_verbatim(self):
        doc = _load(self._mixed_rows())
        self.assertIs(True, doc.get("built"), "既有键逐字不变")
        self.assertEqual(BOX_CODE, doc.get("box_type_code"),
                         "`box_type_code` 仍是成品行那个门面值，口径不变")
        versions = doc.get("source_versions") or {}
        self.assertEqual(BOX_CODE, versions.get("box_type_code"), "既有来源四项逐字不变")
        self.assertEqual("match-v1", versions.get("engine_version"), "既有来源四项逐字不变")
        self.assertEqual("PE1", versions.get("confirmed_by"), "既有来源四项逐字不变")
        self.assertEqual("2026-09-22 10:00:00", versions.get("confirmed_at"),
                         "既有来源四项逐字不变")


# --------------------------------------------------------------------------- #
# I 组：行上的盒型必须真的写得进库
# --------------------------------------------------------------------------- #
class IBoxTypePersists(unittest.TestCase):
    def test_i5_insert_column_list_accepts_box_type(self):
        self.assertIn("box_type_code", da_repo._PACKAGING_BOM_COLUMNS,
                      "落库按 `_PACKAGING_BOM_COLUMNS` 取值（da_repo.py:762），"
                      "清单里没有这一列，行上的盒型就被丢掉（Spec §2.2）")

    def test_i6_old_databases_get_the_column(self):
        pairs = [(entry[0], entry[1]) for entry in da_db._ADDED_COLUMNS]
        self.assertIn(("wip_packaging_bom_item", "box_type_code"), pairs,
                      "老库上 `CREATE TABLE IF NOT EXISTS` 不补列，必须进 "
                      "`_ADDED_COLUMNS` 才补得上（Spec §2.1）")


if __name__ == "__main__":
    unittest.main()
