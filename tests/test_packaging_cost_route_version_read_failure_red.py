"""红测：成本的"当前路线版本读不到"不许被说成"工艺路线已重新确认"。

Spec：`docs/specs/packaging-cost-route-version-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `packaging_cost.py:2509 _upstream_route_version()` 的 `except Exception: return ""`
    把"读失败"和"确实没有确认版本"折成同一个值；
  · `packaging_cost.py:2489 _input_drift()` 拿这个空串去和存的 `route_version` 比 ——
    探测一挂就报 `route_reconfirmed`（前端 `requirement-confirm.js:840` → "工艺路线已重新确认"），
    PE1 会为一个根本没发生的"重新确认"白重算一次成本；
  · 反过来：存的那一版本来就是空串时，两边都是 "" → 一个原因都不报，`stale=false`
    —— "比较不了"在这里被伪装成"没问题"；
  · 同一函数里 BOM 那条轴有 `bom_unavailable`（`:2496-2504`）把这两件事分开，
    路线轴一个都没有（`grep -rn "route_unavailable" tech_app/` 命中数 0）。

纪律：只读源码 + 假仓库 + 打桩 `packaging_route.route_versions`；不连 PG / SQLite 生产库、
不发 HTTP、不写任何文件、不落库。
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

from tech_app.backend.services import packaging_cost as cost           # noqa: E402
from tech_app.backend.services import packaging_route as route         # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-ROUTEREAD-001"

STORED_ROUTE_VERSION = "route:v1"
LIVE_ROUTE_VERSION = "route:v2"

BOM_ROW_A = {"item_key": "RB02001-P02", "bom_category": "box_part", "length_mm": 440.123,
             "width_mm": 482.92, "material": "300g双铜哑胶", "seq": 1}
BOM_ROW_B = {"item_key": "RB02001-P08", "bom_category": "box_part", "length_mm": 120.0,
             "width_mm": 80.0, "material": "灰板", "seq": 2}

BOM_ROWS = [BOM_ROW_A, BOM_ROW_B]
# BOM 侧保持"没变"，这样 stale / stale_reasons 上的读数只可能来自路线那条轴。
STORED_BOM_HASH = cost.bom_input_hash(BOM_ROWS)


def _stored_row(route_version=STORED_ROUTE_VERSION):
    """落库回来的成本估算行（`source_versions_json` 是既有列）。"""
    return {"estimate_id": 7, "project_id": PID, "requirement_no": REQ_NO,
            "scenario_code": "default", "industry": "packaging",
            "engine_version": cost.ENGINE_VERSION, "cost_profile": cost.COST_PROFILE,
            "currency": "CNY", "quote_quantity": 1000, "tax_rate": 0.13,
            "loss_base_scope": cost.DEFAULT_LOSS_BASE_SCOPE,
            "material_total": 100.0, "process_total": 50.0, "labor_total": 20.0,
            "tooling_total": 0.0, "packaging_total": 5.0, "freight_total": 3.0,
            "other_total": 2.0, "subtotal": 180.0, "loss_amount": 1.8,
            "total_cost": 1234.5, "has_gaps": 0, "gaps_json": "[]",
            "assumptions_json": "[]", "computed_at": "2026-09-22 10:00:00",
            "computed_by": "PE1", "computed_by_role": "process_engineer",
            "source_versions_json": json.dumps(
                {"route_version": route_version, "engine_version": cost.ENGINE_VERSION,
                 "bom_hash": STORED_BOM_HASH, "bom_item_total": len(BOM_ROWS)},
                ensure_ascii=False)}


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
        for owner, name, old in reversed(self.saved):
            setattr(owner, name, old)
        return False


def _load_cost(row, *, versions=(), route_error=None, bom_error=None,
               bom_rows=BOM_ROWS):
    """读一次成本；`packaging_route.route_versions()` 是本批唯一的打桩缝（Spec §2.1）。"""
    def version_reader(*args, **kwargs):
        if route_error is not None:
            raise route_error
        return [dict(item) for item in versions]

    def bom_reader(*args, **kwargs):
        if bom_error is not None:
            raise bom_error
        return [dict(item) for item in bom_rows]

    with _Patch((cost, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                (cost.da_repo, "load_packaging_cost", lambda *a, **k: row),
                (cost.da_repo, "load_packaging_cost_items", lambda *a, **k: []),
                (cost.da_repo, "load_packaging_bom", bom_reader),
                (route, "route_versions", version_reader)):
        return cost.load_cost(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# N 组：读侧的三态（读到了 / 确实没有 / 读不到）必须两两可分
# --------------------------------------------------------------------------- #
class NCostRouteVersionReadFailure(unittest.TestCase):
    def test_n1_unreadable_route_is_not_a_reconfirmation(self):
        result = _load_cost(_stored_row(), route_error=RuntimeError("route store down"))
        self.assertNotIn("route_reconfirmed", result.get("stale_reasons") or [],
                         "读不到路线版本时说'工艺路线已重新确认'是假的（Spec §1.1）："
                         "PE1 会为一个根本没发生的重新确认白重算一次成本")
        flag = result.get("route_unavailable") or {}
        self.assertEqual("route_unavailable", flag.get("code"),
                         "读不到路线版本必须显式披露（键必须存在）")
        self.assertIn("RuntimeError", str(flag.get("reason") or ""),
                      "要说得出是哪种失败，不能只给一个布尔")

    def test_n2_real_route_change_is_still_reported(self):
        result = _load_cost(_stored_row(), versions=[{"version": LIVE_ROUTE_VERSION}])
        self.assertIn("route_reconfirmed", result.get("stale_reasons") or [],
                      "路线版本真的对不上时照旧要报（本批只把'读不到'从这条里摘出去）")
        self.assertIs(True, result.get("stale"), "真的变了就是过期")

    def test_n3_unreadable_route_is_not_silently_fine(self):
        result = _load_cost(_stored_row(route_version=""),
                            route_error=RuntimeError("route store down"))
        flag = result.get("route_unavailable") or {}
        self.assertEqual("route_unavailable", flag.get("code"),
                         "存的是空串时两边都空 —— 但那是'比较不了'，不是'没问题'（Spec §1.2）")
        self.assertNotIn("route_reconfirmed", result.get("stale_reasons") or [],
                         "读不到不许报成'变了'")
        self.assertIn("RuntimeError", str(flag.get("reason") or ""), "reason 带异常类名")

    def test_n4_clean_read_reports_empty_flag(self):
        result = _load_cost(_stored_row(), versions=[{"version": STORED_ROUTE_VERSION}])
        self.assertEqual({}, result.get("route_unavailable"),
                         "读到了、版本也对得上 → 该键必须是空的（键必须存在）")
        self.assertEqual([], result.get("stale_reasons") or [],
                         "输入没变，一个原因都不该报")

    def test_n5_no_confirmed_route_is_not_a_read_failure(self):
        result = _load_cost(_stored_row(route_version=""), versions=[])
        self.assertEqual({}, result.get("route_unavailable") or {},
                         "正常读到、只是当前确实没有确认版本 —— 那是'确实没有'，不是'读不到'")
        self.assertNotIn("route_reconfirmed", result.get("stale_reasons") or [],
                         "两边都是'没有'，什么都不用报")

    def test_n6_read_failure_does_not_mark_the_estimate_stale(self):
        result = _load_cost(_stored_row(), route_error=RuntimeError("route store down"))
        self.assertIs(False, result.get("stale"),
                      "读不到路线版本不等于这份成本过期（Spec §2.1）：现在它被标成 stale=true")

    def test_n9_stored_versions_are_never_overwritten_by_a_failed_probe(self):
        result = _load_cost(_stored_row(), route_error=RuntimeError("route store down"))
        versions = result.get("source_versions") or {}
        self.assertEqual(STORED_ROUTE_VERSION, versions.get("route_version"),
                         "`source_versions` 永远是**存的**那一份：探测失败也不许拿现场值覆盖")
        self.assertEqual(STORED_BOM_HASH, versions.get("bom_hash"),
                         "BOM 那一项同理逐字不变")


# --------------------------------------------------------------------------- #
# N 组（护栏）：既有口径不许被本批改掉
# --------------------------------------------------------------------------- #
class NExistingContractUnchanged(unittest.TestCase):
    def test_n7_existing_keys_and_bom_axis_are_verbatim(self):
        result = _load_cost(_stored_row(), versions=[{"version": STORED_ROUTE_VERSION}])
        self.assertIs(True, result.get("built"), "既有键逐字不变")
        self.assertEqual(1234.5, result.get("total_cost"), "既有键逐字不变")
        self.assertEqual([], result.get("items"), "既有键逐字不变")
        self.assertEqual("PE1", result.get("computed_by"), "既有键逐字不变")
        self.assertEqual({}, result.get("bom_unavailable") or {},
                         "BOM 侧披露口径逐字不变")

    def test_n7b_bom_axis_still_reports_its_own_defects(self):
        rebuilt = _load_cost(_stored_row(), versions=[{"version": STORED_ROUTE_VERSION}],
                             bom_rows=[BOM_ROW_A])
        self.assertIn("bom_rebuilt", rebuilt.get("stale_reasons") or [],
                      "BOM 那一条轴照旧独立报（不许被本批改动）")
        unreadable = _load_cost(_stored_row(), versions=[{"version": STORED_ROUTE_VERSION}],
                                bom_error=RuntimeError("bom store down"))
        self.assertEqual("bom_unavailable",
                         (unreadable.get("bom_unavailable") or {}).get("code"),
                         "BOM 读不到照旧给 bom_unavailable（两条轴不许合并）")
        self.assertNotIn("route_reconfirmed", unreadable.get("stale_reasons") or [],
                         "BOM 读不到时路线那条轴没有理由报'变了'")


class NFrontendDisclosure(unittest.TestCase):
    def test_n8_frontend_has_a_separate_route_unavailable_banner(self):
        source = (ROOT / "tech_app" / "frontend" / "requirement-confirm.js").read_text(
            encoding="utf-8")
        self.assertIn("data-pc-route-unavailable", source,
                      "成本面板要有独立钩子把'暂时读不到路线版本'说出来（Spec §2.2）")
        self.assertNotIn("route_unavailable:", source,
                         "'读不到'不是'变了'：不许把它塞进 PC_STALE_REASONS 那张人话表")


if __name__ == "__main__":
    unittest.main()
