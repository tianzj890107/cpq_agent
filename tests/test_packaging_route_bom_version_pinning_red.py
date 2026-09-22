"""红测：工艺路线要固定"排产时照的那一版 BOM"，不是读的时候现取。

Spec：`docs/specs/packaging-route-bom-version-pinning.md`

现状缺口（代码级，逐条可指到行）：
  · `packaging_route.py:504-508 load_route()` 的 `source_versions` 三项全部来自**当前** BOM
    文档（`:496-497` 现取）—— BOM 一重建，旧路线的 `bom_version` 就跟着变；
  · 路线表 `da_schema.sql:1249 wip_packaging_process_route`、版本表 `:1293`、
    `da_repo.py:795 _PACKAGING_ROUTE_COLUMNS`、`da_db.py:28 _ADDED_COLUMNS` 里都没有 BOM
    来源列 —— `build_route()`（`:541`）自己也不知道照的是哪一版，落库直接丢弃；
  · `packaging_route.py:454 _stale_reasons()` 只有工序指纹 / 表面字段 / 数量三条轴，
    BOM 重建后已确认路线照旧 `stale=false`、`stale_reasons=[]`；
  · `confirm_route()`（`:592`）的幂等判定（`:611-616`）与冻结快照（`:618-632`）都不含
    输入版本：BOM 变了、工序没变时"重复确认"原样返回旧快照，版本号都不动。

纪律：只读源码 + 纯函数 / 假仓库；不连 PG / SQLite 生产库、不发 HTTP、不写任何文件、不落库。
本文件里的假 `da_repo` / `packaging_bom.load_bom` 都只替换当前测试进程内的模块属性，退出即还原。
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
from tech_app.backend.services import packaging_route as route        # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-ROUTEPIN-001"
BOX = "RB02001"

#: 排产那一刻那一版 BOM（假仓库里的"存的那一份"）。
OLD_BOM_VERSION = "2026-09-21 09:00:00"
OLD_BOM_HASH = "bom-hash-v1-old"        # 哨兵：真 sha256 永远不会等于它
NEW_BOM_VERSION = "2026-09-22 08:00:00"

DATA = {"industry": "packaging", "quote_quantity": 1000, "lamination": "是"}

STEP_ROWS = [
    {"step_no": 10, "step_name": "面纸印刷", "rank": 40, "workstation": "印刷机",
     "work_content": "面纸印刷", "standard_seconds": 60.0, "needs_standard_time": 0,
     "automation": "自动", "control_point": "", "parallel_ok": 0, "depends_on": None,
     "source": "template:面纸印刷与覆膜", "requirement_field": "lamination", "note": ""},
    {"step_no": 20, "step_name": "组装", "rank": 160, "workstation": "裱糊线",
     "work_content": "总装", "standard_seconds": 121.0, "needs_standard_time": 0,
     "automation": "手工", "control_point": "外观", "parallel_ok": 0, "depends_on": 10,
     "source": "template:总装与检验", "requirement_field": "", "note": ""},
]


def bom_rows(*, length_mm: float = 440.123, material: str = "300g双铜哑胶",
             generated_at: str = OLD_BOM_VERSION) -> list:
    """假 BOM：三行（成品 + 部件 + 工序）——`da_repo.load_packaging_bom` 的形状。"""
    return [
        {"item_key": BOX, "bom_category": "finished", "item_name": "700ML 双开门酒盒",
         "generated_at": generated_at, "engine_version": "packaging_bom_v1", "seq": 1},
        {"item_key": BOX + "-P01", "bom_category": "box_part", "item_name": "左盖面纸",
         "length_mm": length_mm, "width_mm": 482.92, "material": material, "seq": 1,
         "generated_at": generated_at},
        {"item_key": BOX + "-S01", "bom_category": "process", "item_name": "面纸印刷",
         "standard_seconds": 60.0, "seq": 1, "generated_at": generated_at},
    ]


def bom_doc(*, generated_at: str = OLD_BOM_VERSION, rows=None) -> dict:
    """假 BOM 文档：`packaging_bom.load_bom()` 的形状（`items` 与行同源）。"""
    rows = bom_rows(generated_at=generated_at) if rows is None else list(rows)
    return {"built": True, "box_type_code": BOX, "requirement_no": REQ_NO,
            "engine_version": "packaging_bom_v1", "generated_at": generated_at,
            "items": rows}


def stored_versions(*, bom_hash: str = OLD_BOM_HASH,
                    bom_version: str = OLD_BOM_VERSION) -> dict:
    return {"bom_version": bom_version, "bom_hash": bom_hash, "bom_item_total": 3,
            "engine_version": "packaging_bom_v1", "box_type_code": BOX}


def _fixtures(steps=None, data=None):
    steps = [dict(row) for row in (STEP_ROWS if steps is None else steps)]
    data = dict(DATA if data is None else data)
    out_steps = [route._step_out(dict(row)) for row in steps]
    return {"steps": steps, "out_steps": out_steps, "data": data,
            "fingerprint": route._steps_fingerprint(out_steps),
            "surface": route._surface_snapshot(data),
            "quantity": route._quote_quantity(data),
            "result": {"steps": out_steps, "total_seconds": 181.0, "batch_seconds": 181000.0,
                       "has_incomplete_time": False}}


def route_row(*, provenance=..., status="confirmed", fx=None) -> dict:
    """落库回来的路线主表行；`provenance is None` = 历史行（没有来源列）。"""
    fx = fx or _fixtures()
    row = {"project_id": PID, "requirement_no": REQ_NO, "industry": "packaging",
           "engine_version": route.ENGINE_VERSION, "generated_at": "2026-09-21 09:10:00",
           "box_type_code": BOX, "total_seconds": 181.0, "batch_seconds": 181000.0,
           "has_incomplete_time": 1, "status": status, "confirmed_by": "wangjingli",
           "confirmed_at": "2026-09-21 09:20:00", "stale": 0, "stale_reasons": "[]",
           "steps_fingerprint": fx["fingerprint"], "surface_json": fx["surface"],
           "quote_quantity": fx["quantity"], "updated_at": "2026-09-21 09:10:00"}
    if provenance is not ...:
        row["source_versions_json"] = json.dumps(provenance, ensure_ascii=False)
    return row


def route_version(*, provenance=..., fx=None, version=1) -> dict:
    fx = fx or _fixtures()
    row = {"version_id": version, "project_id": PID, "requirement_no": REQ_NO,
           "version": version, "confirmed_by": "wangjingli",
           "confirmed_at": "2026-09-21 09:20:00", "box_type_code": BOX,
           "steps_fingerprint": fx["fingerprint"], "surface_json": fx["surface"],
           "quote_quantity": fx["quantity"], "total_seconds": 181.0,
           "has_incomplete_time": 0, "steps_json": json.dumps(fx["out_steps"],
                                                              ensure_ascii=False)}
    if provenance is not ...:
        row["source_versions_json"] = json.dumps(provenance, ensure_ascii=False)
    return row


_MISSING = object()


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原（属性本不存在则删除）。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name, _MISSING)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, old in reversed(self.saved):
            if old is _MISSING:
                try:
                    delattr(owner, name)
                except AttributeError:
                    pass
            else:
                setattr(owner, name, old)
        return False


def fake_hash(value: str):
    """把 `bom_input_hash` 固定成某个值（属性本不存在时也能装上、撤掉）。"""
    return lambda rows, _value=value: _value


def _load(row, *, rows=None, doc=None, bom_error=None, versions=..., steps=None,
          data=None, hash_value=None):
    """在假仓库上跑 `load_route()`：只比对返回值，不落任何库。"""
    fx = _fixtures(steps=steps, data=data)
    rows = bom_rows() if rows is None else list(rows)
    doc = bom_doc(rows=rows) if doc is None else dict(doc)
    if versions is ...:
        versions = [route_version(fx=fx)]

    def bom_loader(*args, **kwargs):
        if bom_error is not None:
            raise bom_error
        return dict(doc)

    def rows_loader(*args, **kwargs):
        if bom_error is not None:
            raise bom_error
        return [dict(item) for item in rows]

    patches = [
        (route, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
        (route, "_requirement_data", lambda *a, **k: dict(fx["data"])),
        (route, "build_route_steps", lambda *a, **k: {"steps": list(fx["out_steps"])}),
        (route.da_repo, "load_packaging_route", lambda *a, **k: row),
        (route.da_repo, "load_packaging_route_steps", lambda *a, **k: list(fx["steps"])),
        (route.da_repo, "packaging_route_versions", lambda *a, **k: list(versions)),
        (route.da_repo, "load_packaging_bom", rows_loader),
        (bom, "load_bom", bom_loader),
    ]
    if hash_value is not None:
        patches.append((route, "bom_input_hash", fake_hash(hash_value)))
    with _Patch(*patches):
        return route.load_route(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# E 组：算的时候记下（build 时写）
# --------------------------------------------------------------------------- #
class EBuildPinsTheBomVersion(unittest.TestCase):
    def test_e1_bom_input_hash_is_a_stable_pure_fingerprint(self):
        hasher = getattr(route, "bom_input_hash", None)
        self.assertTrue(callable(hasher),
                        "路线侧必须有一个模块级纯函数 `bom_input_hash(rows)`（Spec §2.2）："
                        "现在没有，所以路线根本算不出'照哪一版 BOM 排的'")
        rows = bom_rows()
        self.assertEqual("", hasher([]), "空输入给空串（'没有 BOM' 不是一版内容）")
        self.assertEqual(hasher(rows), hasher(list(reversed(rows))),
                         "行序无关：同一批行换个顺序必须是同一个指纹")
        self.assertNotEqual(hasher(rows), hasher(bom_rows(length_mm=120.0)),
                            "BOM 内容变了（尺寸/材料/行数）指纹必须变")
        self.assertEqual(hasher(rows), hasher(bom_rows()), "同一个输入必须稳定复现")

    def _build(self, rows=None, doc=None):
        fx = _fixtures()
        rows = bom_rows() if rows is None else list(rows)
        doc = bom_doc(rows=rows) if doc is None else dict(doc)
        captured: dict = {}

        def saver(project_id, requirement_no, payload, steps):
            captured["project_id"] = project_id
            captured["requirement_no"] = requirement_no
            captured["route"] = dict(payload)
            captured["steps"] = list(steps)

        with _Patch((route, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                    (route, "_requirement_data", lambda *a, **k: dict(DATA)),
                    (route, "build_route_steps", lambda *a, **k: dict(fx["result"])),
                    (route, "load_route", lambda *a, **k: {"built": True}),
                    (route.da_repo, "load_box_match",
                     lambda *a, **k: {"decision": "confirmed", "confirmed_box_type": BOX}),
                    (route.da_repo, "load_packaging_bom", lambda *a, **k: list(rows)),
                    (route.da_repo, "save_packaging_route", saver),
                    (route.kb_repo, "packaging_process_templates",
                     lambda *a, **k: [{"step_name": "面纸印刷", "seq": 1, "part_code": "P01"}]),
                    (bom, "load_bom", lambda *a, **k: dict(doc)),
                    (route.store, "audit", lambda *a, **k: None),
                    (route.da_db, "now", lambda: "2026-09-21 09:30:00")):
            route.build_route(PID, REQ_NO)
        return captured

    def test_e2_build_records_the_bom_it_actually_read(self):
        captured = self._build()
        payload = captured.get("route") or {}
        versions = payload.get("source_versions") or {}
        self.assertEqual(OLD_BOM_VERSION, versions.get("bom_version"),
                         "排产那一刻读到的 BOM 版本必须随路线落库（Spec §2.2）："
                         "现在 `build_route()` 一个来源键都不写，读接口只能去现取")
        self.assertEqual(route.bom_input_hash(bom_rows()), versions.get("bom_hash"),
                         "落库的 `bom_hash` 必须逐字等于排产时那批 BOM 行的指纹")
        self.assertEqual(3, versions.get("bom_item_total"), "行数口径 = 排产时读到的行数")
        self.assertEqual("packaging_bom_v1", versions.get("engine_version"))
        self.assertEqual(BOX, versions.get("box_type_code"))
        self.assertIn("source_versions_json", route.da_repo._PACKAGING_ROUTE_COLUMNS,
                      "路线表必须有列能装下这份来源（Spec §2.3）：现在多出来的键被直接丢弃")

    def test_e3_build_still_replaces_steps_and_audits(self):
        captured = self._build()
        payload = captured.get("route") or {}
        self.assertEqual("draft", payload.get("status"), "重排一律回到 draft，口径逐字不变")
        self.assertIs(False, bool(payload.get("stale")), "刚落库的路线不是 stale")
        self.assertEqual([10, 20], [row.get("step_no") for row in captured.get("steps") or []],
                         "工序整体替换落库的既有行为逐字不变")
        self.assertEqual(REQ_NO, captured.get("requirement_no"))


# --------------------------------------------------------------------------- #
# F 组：读的时候不现取 + 输入变了要报
# --------------------------------------------------------------------------- #
class FReadDoesNotReFetchAndReportsDrift(unittest.TestCase):
    def test_f1_rebuilt_bom_marks_the_route_stale(self):
        result = _load(route_row(provenance=stored_versions()),
                       rows=bom_rows(length_mm=120.0),
                       doc=bom_doc(generated_at=NEW_BOM_VERSION))
        self.assertIs(True, bool(result.get("stale")),
                      "BOM 变了之后旧路线必须被标成过期（Spec §2.2）")
        self.assertIn("bom_rebuilt", result.get("stale_reasons") or [],
                      "必须说清是哪一项变了（bom_rebuilt），不许只给一个布尔")
        self.assertEqual(stored_versions(), result.get("source_versions"),
                         "`source_versions` 必须是**存的**那一份：排产时记下哪一版，"
                         "读的时候不许现取（现在读到的是新 BOM 的版本）")

    def test_f2_same_rows_with_new_timestamp_is_not_drift(self):
        stored = stored_versions()
        result = _load(route_row(provenance=stored), rows=bom_rows(),
                       doc=bom_doc(generated_at=NEW_BOM_VERSION))
        self.assertNotIn("bom_rebuilt", result.get("stale_reasons") or [],
                         "指纹为准：BOM 行没变就不算输入变了（Spec §4 F2 口径）")
        self.assertEqual(stored, result.get("source_versions"),
                         "即使当前 BOM 的 generated_at 变了，读回的 `bom_version` 也必须是"
                         "排产那一刻那一版（现在会被当前值覆盖）")

    def test_f3_legacy_route_without_provenance_is_disclosed(self):
        result = _load(route_row(provenance=None), rows=bom_rows())
        self.assertEqual({}, result.get("source_versions") or {},
                         "历史路线没有来源就给 `{}`（Spec §2.2）："
                         "不许用当前 BOM 的值兜一个'看起来对'的版本")
        self.assertIn("provenance_missing", result.get("stale_reasons") or [],
                      "没有来源本身就要披露（provenance_missing），不许当成'没过期'")

    def test_f4_unreadable_bom_is_unknown_not_a_verdict(self):
        result = _load(route_row(provenance=stored_versions()), rows=bom_rows(),
                       bom_error=RuntimeError("bom store down"))
        flag = result.get("bom_unavailable") or {}
        self.assertEqual("bom_unavailable", flag.get("code"),
                         "当前 BOM 读不到必须显式披露（Spec §2.2），"
                         "不许把读路线的整个接口带崩、也不许悄悄当成空 BOM")
        self.assertNotIn("bom_rebuilt", result.get("stale_reasons") or [],
                         "读不到 BOM 时'比较不了'，不许断言 BOM 变过")
        self.assertEqual(stored_versions(), result.get("source_versions"),
                         "读不到当前 BOM 也不影响返回**存的**那份来源")

    def test_f5_route_never_built_keeps_its_shape(self):
        result = _load(None, versions=[])
        self.assertFalse(result.get("built"), "没排过路线：built=false 逐字不变")
        self.assertIs(False, bool(result.get("stale")), "没排过路线不算 stale")
        self.assertEqual([], result.get("stale_reasons") or [],
                         "'还没排'不是'过期'：不许给 provenance_missing")
        self.assertEqual([], result.get("steps") or [], "没有路线时 steps=[] 逐字不变")
        self.assertEqual({}, result.get("source_versions") or {},
                         "没有路线时来源给空（键必须是不说谎的值）")

    def test_f6_unconfirmed_route_also_reports_bom_drift(self):
        result = _load(route_row(provenance=stored_versions(), status="draft"),
                       rows=bom_rows(length_mm=120.0), versions=[])
        self.assertIn("bom_rebuilt", result.get("stale_reasons") or [],
                      "BOM 轴的判定与是否确认过无关（Spec §2.2）："
                      "还没确认的路线同样要报出来")


# --------------------------------------------------------------------------- #
# G 组：确认动作冻结的是"照哪一版输入"
# --------------------------------------------------------------------------- #
class GConfirmFreezesTheInputVersion(unittest.TestCase):
    def _confirm(self, row, *, rows=None, versions=..., doc=None, hash_value=None):
        fx = _fixtures()
        rows = bom_rows() if rows is None else list(rows)
        doc = bom_doc(rows=rows) if doc is None else dict(doc)
        if versions is ...:
            versions = [route_version(fx=fx)]
        appended: list = []

        def appender(record):
            appended.append(dict(record))
            return len(appended)

        def rows_loader(*args, **kwargs):
            return [dict(item) for item in rows]

        patches = [
            (route, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
            (route, "_requirement_data", lambda *a, **k: dict(fx["data"])),
            (route, "build_route_steps", lambda *a, **k: {"steps": list(fx["out_steps"])}),
            (route, "load_route", lambda *a, **k: {"built": True, "status": "confirmed"}),
            (route.da_repo, "load_packaging_route", lambda *a, **k: row),
            (route.da_repo, "load_packaging_route_steps", lambda *a, **k: list(fx["steps"])),
            (route.da_repo, "packaging_route_versions", lambda *a, **k: list(versions)),
            (route.da_repo, "load_packaging_bom", rows_loader),
            (route.da_repo, "append_packaging_route_version", appender),
            (bom, "load_bom", lambda *a, **k: dict(doc)),
            (route.store, "audit", lambda *a, **k: None),
            (route.da_db, "now", lambda: "2026-09-21 11:00:00"),
            (route.da_db, "execute", lambda *a, **k: None),
        ]
        if hash_value is not None:
            patches.append((route, "bom_input_hash", fake_hash(hash_value)))
        with _Patch(*patches):
            try:
                record = route.confirm_route(PID, REQ_NO, actor={"username": "wangjingli"})
                return {"ok": True, "record": record, "appended": appended,
                        "error": None}
            except route.RouteError as exc:
                return {"ok": False, "record": None, "appended": appended, "error": exc}

    def test_g1_repeat_confirm_without_changes_stays_idempotent(self):
        result = self._confirm(route_row(provenance=stored_versions()),
                               hash_value=OLD_BOM_HASH)
        self.assertTrue(result["ok"], "BOM 没变时重复确认照旧幂等（既有口径不变）")
        self.assertEqual([], result["appended"], "没有变化就不该追加版本")

    def test_g2_confirm_is_refused_after_the_bom_changed(self):
        result = self._confirm(route_row(provenance=stored_versions(), status="draft"),
                               rows=bom_rows(length_mm=120.0),
                               doc=bom_doc(generated_at=NEW_BOM_VERSION), versions=[])
        self.assertFalse(result["ok"],
                          "排产照的那版 BOM 已经变了，不许把旧路线确认成新版本（Spec §2.2）")
        self.assertEqual("bom_rebuilt", getattr(result["error"], "code", ""),
                         "拒绝码必须能指回原因（bom_rebuilt）")
        self.assertEqual(409, getattr(result["error"], "status_code", 0))
        self.assertEqual([], result["appended"],
                         "被拒的确认一个版本快照都不许留下（既有的'被拒不留快照'口径）")

    def test_g3_legacy_route_without_provenance_cannot_be_confirmed(self):
        result = self._confirm(route_row(provenance=None, status="draft"), versions=[])
        self.assertFalse(result["ok"],
                          "没有来源的历史路线不许直接确认（Spec §5：先重算再确认）")
        self.assertEqual("route_bom_provenance_missing",
                         getattr(result["error"], "code", ""))
        self.assertEqual([], result["appended"], "被拒的确认不许留下版本快照")


# --------------------------------------------------------------------------- #
# H 组：快照与读回都带来源
# --------------------------------------------------------------------------- #
class HSnapshotsCarryTheProvenance(unittest.TestCase):
    def _confirm(self, row, *, rows=None, versions=..., doc=None, hash_value=OLD_BOM_HASH):
        return GConfirmFreezesTheInputVersion()._confirm(
            row, rows=rows, versions=versions, doc=doc, hash_value=hash_value)

    def test_h1_frozen_snapshot_records_the_input_version(self):
        stored = stored_versions()
        result = self._confirm(route_row(provenance=stored, status="draft"), versions=[])
        self.assertTrue(result["ok"], "BOM 没变的历史路线重算后确认必须照旧成功")
        self.assertEqual(1, len(result["appended"]), "确认要追加一条版本快照")
        record = result["appended"][0]
        self.assertEqual(stored, record.get("source_versions"),
                         "冻结快照必须记下'照哪一版 BOM 排的'（Spec §2.2、§6.1）："
                         "现在快照里一个来源键都没有，事后无法回答旧版本照的是哪一版")
        self.assertEqual(1, record.get("version"))

    def test_h2_version_readback_exposes_the_provenance(self):
        stored = stored_versions()
        rows = [route_version(provenance=stored, version=1),
                route_version(provenance=None, version=2)]
        with _Patch((route, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                    (route.da_repo, "packaging_route_versions", lambda *a, **k: rows)):
            versions = route.route_versions(PID, REQ_NO)
        self.assertEqual(2, len(versions), "版本条数口径逐字不变")
        self.assertEqual(stored, versions[0].get("source_versions"),
                         "有来源的快照读回时必须带 `source_versions`（Spec §2.2）")
        self.assertEqual({}, versions[1].get("source_versions") or {},
                         "历史快照没有来源就给 `{}`，不许现取当前 BOM 兜上去")
        self.assertEqual(2, versions[1].get("version"), "既有键一个都不许丢")


if __name__ == "__main__":
    unittest.main()
