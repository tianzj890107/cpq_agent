"""红测：路线重算不出来时不许当成"没变"。

Spec：`docs/specs/packaging-route-recompute-unavailable.md`

现状缺口（代码级，都可指到行）：
  · `packaging_route.py:454 _stale_reasons()` 里 `except RouteError: current = None` 之后
    `current_fingerprint` 被顶回**存的**指纹（`:467-469`）—— "现在排不出来"与"排出来一模一样"
    在读回体上同形，`route_changed` 永远不会命中；
  · `packaging_route.py:524 load_route()` 的 `gaps.no_process_template` 写死 `False`，
    而 `build_route()` 在同样的输入下会 `409 no_process_template`（`:557`）——
    "再点一次重排"报错、"读回来看"说没缺口；
  · `except RouteError:` 把 `RouteError.code` 吞掉，读接口说不出排不出来的原因。

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
REQ_NO = "REQ-RECOMPUTE-001"
BOX = "RB02001"

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

CHANGED_STEP_ROWS = [
    {"step_no": 10, "step_name": "灰板开料", "rank": 10, "workstation": "开料机",
     "work_content": "开料", "standard_seconds": 30.0, "needs_standard_time": 0,
     "automation": "自动", "control_point": "", "parallel_ok": 0, "depends_on": None,
     "source": "template:材料开料", "requirement_field": "", "note": ""},
    {"step_no": 20, "step_name": "组装", "rank": 160, "workstation": "裱糊线",
     "work_content": "总装", "standard_seconds": 121.0, "needs_standard_time": 0,
     "automation": "手工", "control_point": "外观", "parallel_ok": 0, "depends_on": 10,
     "source": "template:总装与检验", "requirement_field": "", "note": ""},
]


def _out(rows):
    return [route._step_out(dict(row)) for row in rows]


def _fixtures(steps=None):
    steps = [dict(row) for row in (STEP_ROWS if steps is None else steps)]
    out_steps = _out(steps)
    return {"steps": steps, "out_steps": out_steps, "data": dict(DATA),
            "fingerprint": route._steps_fingerprint(out_steps),
            "surface": route._surface_snapshot(DATA),
            "quantity": route._quote_quantity(DATA)}


def route_row(fx=None, status="confirmed"):
    fx = fx or _fixtures()
    return {"project_id": PID, "requirement_no": REQ_NO, "industry": "packaging",
            "engine_version": route.ENGINE_VERSION, "generated_at": "2026-09-22 09:00:00",
            "box_type_code": BOX, "total_seconds": 181.0, "batch_seconds": 181000.0,
            "has_incomplete_time": 1, "status": status, "confirmed_by": "wangjingli",
            "confirmed_at": "2026-09-22 09:10:00", "stale": 0, "stale_reasons": "[]",
            "steps_fingerprint": fx["fingerprint"], "surface_json": fx["surface"],
            "quote_quantity": fx["quantity"], "updated_at": "2026-09-22 09:00:00"}


def route_version(fx=None, version=1):
    fx = fx or _fixtures()
    return {"version_id": version, "project_id": PID, "requirement_no": REQ_NO,
            "version": version, "confirmed_by": "wangjingli",
            "confirmed_at": "2026-09-22 09:10:00", "box_type_code": BOX,
            "steps_fingerprint": fx["fingerprint"], "surface_json": fx["surface"],
            "quote_quantity": fx["quantity"], "total_seconds": 181.0,
            "has_incomplete_time": 0,
            "steps_json": json.dumps(fx["out_steps"], ensure_ascii=False)}


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


def _load(step_rows=None, *, build_error=None, row=None, versions=...):
    fx = _fixtures(step_rows)
    row = route_row(fx) if row is None else row
    if versions is ...:
        versions = [route_version(fx)]

    def builder(*args, **kwargs):
        if build_error is not None:
            raise build_error
        return {"steps": list(fx["out_steps"])}

    with _Patch((route, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                (route, "_requirement_data", lambda *a, **k: dict(fx["data"])),
                (route, "build_route_steps", builder),
                (route.da_repo, "load_packaging_route", lambda *a, **k: row),
                (route.da_repo, "load_packaging_route_steps", lambda *a, **k: list(fx["steps"])),
                (route.da_repo, "packaging_route_versions", lambda *a, **k: list(versions)),
                (route.da_repo, "load_box_match", lambda *a, **k: None)):
        return route.load_route(PID, REQ_NO)


def _no_template():
    return route.RouteError("盒型 %s 没有工艺模板，无法生成工艺路线" % BOX, 409,
                            "no_process_template")


# --------------------------------------------------------------------------- #
# K 组：重算不出来 vs 没变
# --------------------------------------------------------------------------- #
class KRecomputeUnavailable(unittest.TestCase):
    def test_k1_missing_templates_is_disclosed_not_silently_unchanged(self):
        result = _load(build_error=_no_template())
        self.assertIs(True, result["gaps"].get("no_process_template"),
                      "模板没了这条缺口读回时必须是真的（Spec §2.1）："
                      "现在是写死的 False —— 重排会 409、读回却说没缺口，界面两侧打架")
        flag = result.get("route_recompute_unavailable") or {}
        self.assertEqual("no_process_template", flag.get("code"),
                         "必须显式披露'按当前输入排不出来'并带原因码（Spec §2.1）")
        self.assertTrue(result.get("built"),
                        "披露归披露：已存的路线照旧返回，读接口不许因此报错")
        self.assertEqual(2, len(result.get("steps") or []), "已存的工序照旧返回")

    def test_k2_other_recompute_failures_keep_their_code(self):
        error = route.RouteError("盒型 %s 已下架" % BOX, 409, "box_type_retired")
        result = _load(build_error=error)
        flag = result.get("route_recompute_unavailable") or {}
        self.assertEqual("box_type_retired", flag.get("code"),
                         "原因码必须逐字带出来（Spec §2.1）：现在 except RouteError 把它吞了")
        self.assertIs(False, bool(result["gaps"].get("no_process_template")),
                      "不是模板问题就不许把 no_process_template 说成真的")

    def test_k3_healthy_recompute_reports_no_unavailable(self):
        result = _load()
        self.assertEqual({}, result.get("route_recompute_unavailable") or {},
                         "重算成功时该键必须是空的（不许拿它当'总是有问题'）")
        self.assertIs(False, bool(result["gaps"].get("no_process_template")),
                      "模板还在时这条缺口照旧 False")
        self.assertFalse(bool(result.get("stale")), "输入没变 → 不是 stale")

    def test_k4_successful_recompute_still_reports_real_changes(self):
        original = _fixtures()
        result = _load(CHANGED_STEP_ROWS, row=route_row(original),
                       versions=[route_version(original)])
        self.assertIn("route_changed", result.get("stale_reasons") or [],
                      "重算成功且工序确实变了，照旧要报 route_changed（既有口径不变）")
        self.assertFalse(result.get("route_recompute_unavailable"),
                         "重算成功时不许挂'排不出来'的标记")
        self.assertNotIn("route_changed", _load(build_error=_no_template())
                         .get("stale_reasons") or [],
                         "'算不出来' ≠ '变过'：不许拿重算失败充 route_changed")


if __name__ == "__main__":
    unittest.main()
