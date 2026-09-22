"""红测：换盒型之后旧工艺路线必须报出来（读侧 `box_type_reconfirmed`，确认动作拒绝）。

Spec：`docs/specs/packaging-route-box-type-drift.md`

现状缺口（代码级，都可指到行）：
  · `packaging_route.py:454 _stale_reasons()` 只用**路线行里存的** `box_type_code` 重算工序指纹
    （`:463-470`），`load_route()`（`:481`）从头到尾不读 `da_repo.load_box_match()` ——
    盒型从 A 重新确认成 B 之后，三条既有原因一条都不命中，`stale=false`、`stale_reasons=[]`；
  · `:592 confirm_route()` 只校验工序顺序与三条指纹，从不读当前确认盒型 ——
    "照 A 排的路线"能被确认成冻结版本，而需求单上确认的是 B；
  · 读不到匹配记录、还没确认过盒型、与"盒型一致"在读回体上分不出来。

纪律：只读源码 + 假仓库 / 纯函数；不连 PG / SQLite 生产库、不发 HTTP、不写任何文件、不落库。
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

from tech_app.backend.services import packaging_route as route        # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-BOXDRIFT-001"
BOX_A = "RB02001"
BOX_B = "RB02002"

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


def _fixtures():
    out_steps = [route._step_out(dict(row)) for row in STEP_ROWS]
    return {"steps": [dict(row) for row in STEP_ROWS], "out_steps": out_steps,
            "data": dict(DATA),
            "fingerprint": route._steps_fingerprint(out_steps),
            "surface": route._surface_snapshot(DATA),
            "quantity": route._quote_quantity(DATA)}


def route_row(*, box_type_code=BOX_A, status="confirmed", fx=None):
    fx = fx or _fixtures()
    return {"project_id": PID, "requirement_no": REQ_NO, "industry": "packaging",
            "engine_version": route.ENGINE_VERSION, "generated_at": "2026-09-22 09:00:00",
            "box_type_code": box_type_code, "total_seconds": 181.0,
            "batch_seconds": 181000.0, "has_incomplete_time": 1, "status": status,
            "confirmed_by": "wangjingli", "confirmed_at": "2026-09-22 09:10:00",
            "stale": 0, "stale_reasons": "[]", "steps_fingerprint": fx["fingerprint"],
            "surface_json": fx["surface"], "quote_quantity": fx["quantity"],
            "updated_at": "2026-09-22 09:00:00"}


def route_version(*, fx=None, version=1):
    fx = fx or _fixtures()
    return {"version_id": version, "project_id": PID, "requirement_no": REQ_NO,
            "version": version, "confirmed_by": "wangjingli",
            "confirmed_at": "2026-09-22 09:10:00", "box_type_code": BOX_A,
            "steps_fingerprint": fx["fingerprint"], "surface_json": fx["surface"],
            "quote_quantity": fx["quantity"], "total_seconds": 181.0,
            "has_incomplete_time": 0,
            "steps_json": json.dumps(fx["out_steps"], ensure_ascii=False)}


def box_record(*, box_type_code=BOX_A, decision="confirmed"):
    return {"project_id": PID, "requirement_no": REQ_NO, "decision": decision,
            "confirmed_box_type": box_type_code, "confirmed_by": "wangjingli",
            "confirmed_at": "2026-09-22 08:50:00",
            "engine_version": "packaging_match_v1"}


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


def _load(row, *, box=..., box_error=None, versions=..., steps=None):
    fx = _fixtures()
    steps = fx["steps"] if steps is None else steps
    if versions is ...:
        versions = [route_version(fx=fx)]
    box = box_record() if box is ... else box

    def box_loader(*args, **kwargs):
        if box_error is not None:
            raise box_error
        return dict(box) if box else None

    with _Patch((route, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                (route, "_requirement_data", lambda *a, **k: dict(fx["data"])),
                (route, "build_route_steps", lambda *a, **k: {"steps": list(fx["out_steps"])}),
                (route.da_repo, "load_packaging_route", lambda *a, **k: row),
                (route.da_repo, "load_packaging_route_steps", lambda *a, **k: list(steps)),
                (route.da_repo, "packaging_route_versions", lambda *a, **k: list(versions)),
                (route.da_repo, "load_box_match", box_loader)):
        return route.load_route(PID, REQ_NO)


def _confirm(row, *, box=..., versions=..., hash_value=None):
    fx = _fixtures()
    versions = [] if versions is ... else versions
    box = box_record() if box is ... else box
    appended: list = []

    def appender(record):
        appended.append(dict(record))
        return len(appended)

    patches = [
        (route, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
        (route, "_requirement_data", lambda *a, **k: dict(fx["data"])),
        (route, "build_route_steps", lambda *a, **k: {"steps": list(fx["out_steps"])}),
        (route, "load_route", lambda *a, **k: {"built": True, "status": "confirmed"}),
        (route.da_repo, "load_packaging_route", lambda *a, **k: row),
        (route.da_repo, "load_packaging_route_steps", lambda *a, **k: list(fx["steps"])),
        (route.da_repo, "packaging_route_versions", lambda *a, **k: list(versions)),
        (route.da_repo, "load_box_match", lambda *a, **k: dict(box) if box else None),
        (route.da_repo, "append_packaging_route_version", appender),
        (route.store, "audit", lambda *a, **k: None),
        (route.da_db, "now", lambda: "2026-09-22 11:00:00"),
        (route.da_db, "execute", lambda *a, **k: None),
    ]
    if hash_value is not None:
        patches.append((route, "bom_input_hash", lambda rows, _v=hash_value: _v))
    with _Patch(*patches):
        try:
            record = route.confirm_route(PID, REQ_NO, actor={"username": "wangjingli"})
            return {"ok": True, "record": record, "appended": appended, "error": None}
        except route.RouteError as exc:
            return {"ok": False, "record": None, "appended": appended, "error": exc}


# --------------------------------------------------------------------------- #
# J 组：路线照的盒型 vs 当前确认的盒型
# --------------------------------------------------------------------------- #
class JRouteBoxTypeDrift(unittest.TestCase):
    def test_j1_reconfirmed_box_type_marks_the_route_stale(self):
        result = _load(route_row(), box=box_record(box_type_code=BOX_B))
        self.assertIs(True, bool(result.get("stale")),
                      "盒型改成 B 之后旧路线必须被标成过期（Spec §2.1）："
                      "现在三条既有原因一条都不命中，路线读起来完全有效")
        self.assertIn("box_type_reconfirmed", result.get("stale_reasons") or [],
                      "必须说清是哪一项变了（box_type_reconfirmed）")
        self.assertEqual(BOX_A, result.get("box_type_code"),
                         "既有键 `box_type_code` 仍是路线行里那个（这条路线照 A 排的）")
        self.assertEqual(BOX_B, result.get("current_box_type_code"),
                         "新增 `current_box_type_code` 必须报出当前确认的是哪个盒型")

    def test_j2_unconfirmed_route_also_reports_the_drift(self):
        result = _load(route_row(status="draft"), box=box_record(box_type_code=BOX_B),
                       versions=[])
        self.assertIn("box_type_reconfirmed", result.get("stale_reasons") or [],
                      "盒型轴的判定与是否确认过无关（Spec §2.2）："
                      "还没确认的路线同样要报出来")

    def test_j3_same_box_type_is_not_drift(self):
        result = _load(route_row(), box=box_record(box_type_code=BOX_A))
        self.assertNotIn("box_type_reconfirmed", result.get("stale_reasons") or [],
                         "盒型没变就不是漂移")
        self.assertFalse(bool(result.get("stale")), "其他输入也没变 → 不是 stale")

    def test_j4_unreadable_box_match_is_unknown_not_a_verdict(self):
        result = _load(route_row(), box_error=RuntimeError("box match store down"))
        flag = result.get("box_match_unavailable") or {}
        self.assertEqual("box_match_unavailable", flag.get("code"),
                         "匹配记录读不到必须显式披露（Spec §2.2）、不许把读路线接口带崩")
        self.assertNotIn("box_type_reconfirmed", result.get("stale_reasons") or [],
                         "读不到当前盒型时'比较不了'，不许断言盒型变过")
        self.assertEqual("", result.get("current_box_type_code") or "",
                         "读不到就报空串，不许现编一个盒型")

    def test_j5_confirm_is_refused_when_the_box_type_moved(self):
        result = _confirm(route_row(status="draft"), box=box_record(box_type_code=BOX_B))
        self.assertFalse(result["ok"],
                          "照 A 排的路线不许在'当前确认的是 B'时被确认（Spec §2.1）")
        self.assertEqual("box_type_reconfirmed", getattr(result["error"], "code", ""))
        self.assertEqual(409, getattr(result["error"], "status_code", 0))
        self.assertEqual([], result["appended"],
                         "被拒的确认一个版本快照都不许留下（既有口径）")

    def test_j6_repeat_confirm_with_the_same_box_type_stays_idempotent(self):
        fx = _fixtures()
        result = _confirm(route_row(), box=box_record(box_type_code=BOX_A),
                          versions=[route_version(fx=fx)], hash_value="pinned-hash")
        self.assertTrue(result["ok"], "盒型一致时重复确认照旧幂等（既有口径不变）")
        self.assertEqual([], result["appended"], "没有变化就不该追加版本")

    def test_j7_no_confirmed_box_type_is_not_drift(self):
        result = _load(route_row(), box=box_record(decision="none", box_type_code=""))
        self.assertNotIn("box_type_reconfirmed", result.get("stale_reasons") or [],
                         "还没确认过盒型不算'盒型变了'（比较不了 / 没确认 ≠ 变了）")


if __name__ == "__main__":
    unittest.main()
