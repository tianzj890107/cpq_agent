"""红测：`stage_chain` 里"这一段读不到"不许显示成"这一段还没做"。

Spec：`docs/specs/packaging-stage-chain-read-failure-disclosure.md`

现状缺口（代码级，都可指到行）：
  · `packaging_drawing_flow/anchor.py:195 _load()` 的 `if not callable(fn): return {}`（"这个部署
    没有这一段"）与 `:204 except Exception: return {}`（"这段读挂了"）**同形**；
  · `:211 _load_list()` 的 `except Exception: return []` 同上；
  · `:238-242` 的 `result_version_of(cost)` 抛异常时 `result_version = ""` —— 与"成本没算过"同形；
  · 于是 `stage_chain` 给这一段的 `value: ""` + `status: "none"`，用户读到的结论是
    **"这一段还没做"**，而真相是"读不到"（重跑不会让它变好）。

纪律：只读源码 + 假依赖（resolve 返回假模块）；不连 PG / SQLite 生产库、不发 HTTP、
不写任何文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services.packaging_drawing_flow import anchor          # noqa: E402

PID = "testpid00001"

STAGE_ORDER = ["box_match", "bom", "route", "cost"]


class _Module:
    """假依赖模块：只挂 Spec 允许调用的那几个函数。"""

    def __init__(self, **functions):
        for name, value in functions.items():
            setattr(self, name, value)


def _boom(*_args, **_kwargs):
    raise RuntimeError("store down")


def _good_engines(*, bom=None, route=None, cost=None, route_versions=None,
                  result_version_error=False):
    def box_row(project_id):
        return {"decision": "confirmed", "confirmed_box_type": "folding_carton",
                "confirmed_by": "zhang", "confirmed_at": "2026-09-20T10:00:00+08:00",
                "engine_version": "packaging_match_v1"}

    def bom_row(project_id):
        return {"built": True, "generated_at": "2026-09-20T11:00:00+08:00",
                "engine_version": "packaging_bom_v1"}

    def route_row(project_id):
        return {"built": True, "status": "confirmed",
                "engine_version": "packaging_route_v1"}

    def versions(project_id):
        return [{"version": 3, "status": "confirmed"}]

    def cost_row(project_id):
        return {"built": True, "engine_version": "packaging_cost_v1"}

    def result_version_of(cost):
        if result_version_error:
            raise RuntimeError("version boom")
        return "cost:v1"

    return {
        "packaging_match": _Module(load_box_match=box_row),
        "packaging_bom": _Module(load_bom=bom or bom_row),
        "packaging_route": _Module(load_route=route or route_row,
                                   route_versions=route_versions or versions),
        "packaging_cost": _Module(load_cost=cost or cost_row,
                                  result_version_of=result_version_of),
    }


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


def _resolver(engines):
    def resolve(name):
        return engines.get(name)
    return resolve


def _chain(engines, stage=""):
    return anchor.stage_chain(PID, _resolver(engines), stage=stage)


def _inheritance(engines, stage=""):
    with _Patch((anchor, "current_anchor",
                 lambda project_id: {"drawing_version": 1, "ir_version": "ir/1",
                                     "ir_hash": "h1", "semantics_version": "s1",
                                     "semantics_hash": "sh1"}),
                (anchor, "requirement_snapshot_version", lambda project_id: "reqsnap/1:abc"),
                (anchor, "unresolved_gaps", lambda project_id, resolve=None: [])):
        return anchor.inheritance(PID, _resolver(engines), stage=stage)


def _row(chain, stage):
    rows = [row for row in (chain or []) if str(row.get("stage")) == stage]
    return rows[0] if rows else {}


# --------------------------------------------------------------------------- #
# P 组：链条每一段的三态（读到 / 确实没做 / 读不到）必须两两可分
# --------------------------------------------------------------------------- #
class PStageChainReadFailure(unittest.TestCase):
    def test_p1_bom_read_failure_is_not_a_missing_bom(self):
        row = _row(_chain(_good_engines(bom=_boom)), "bom")
        self.assertEqual("unavailable", row.get("source"),
                         "读 BOM 抛异常时这一段必须标出来（Spec §2.1）：今天的 `status: none` "
                         "读起来就是'BOM 还没生成'，用户会去重跑一遍")
        flag = row.get("unavailable") or {}
        self.assertEqual("stage_chain_stage_unavailable", flag.get("code"),
                         "这一段的披露键必须存在且码是闭集里的那个")
        self.assertIn("RuntimeError", str(flag.get("reason") or ""), "reason 要说得出异常类名")
        self.assertEqual("", row.get("value"), "既有键逐字不变：读不到不许编一个版本")
        self.assertEqual("none", row.get("status"), "既有键逐字不变：结论不改，只留痕")

    def test_p2_route_read_failure_is_disclosed(self):
        engines = _good_engines(route_versions=_boom)
        row = _row(_chain(engines), "route")
        self.assertEqual("unavailable", row.get("source"),
                         "路线两个入口（load_route / route_versions）任一抛异常都算这一段读不到")

    def test_p3_result_version_failure_is_disclosed(self):
        row = _row(_chain(_good_engines(result_version_error=True)), "cost")
        self.assertEqual("unavailable", row.get("source"),
                         "`result_version_of(cost)` 抛异常与'成本没算过'不是一件事（Spec §2.1）")
        self.assertEqual("", row.get("value"), "读不到版本时不许编一个")
        self.assertIn("RuntimeError", str((row.get("unavailable") or {}).get("reason") or ""),
                      "reason 带异常类名")

    def test_p4_clean_chain_reports_engine_everywhere(self):
        chain = _chain(_good_engines())
        self.assertEqual(STAGE_ORDER, [row.get("stage") for row in chain],
                         "链条的形状与顺序逐字不变")
        for row in chain:
            self.assertEqual("engine", row.get("source"),
                             "%s 段读到了 → source 必须是 engine（键必须存在）" % row.get("stage"))
            self.assertEqual({}, row.get("unavailable"),
                             "%s 段正常时披露键必须是空的" % row.get("stage"))

    def test_p5_absent_dependency_is_not_a_read_failure(self):
        engines = _good_engines()
        engines.pop("packaging_bom")
        row = _row(_chain(engines), "bom")
        self.assertEqual("absent", row.get("source"),
                         "模块没装是'这个部署没有这一段'，与'读挂了'必须分家（Spec §2.1）")
        self.assertEqual({}, row.get("unavailable") or {},
                         "'没装'没有异常类名可报，披露键给空")

    def test_p6_really_missing_stage_keeps_its_existing_shape(self):
        engines = _good_engines(bom=lambda project_id: {"built": False,
                                                        "engine_version": "packaging_bom_v1"})
        row = _row(_chain(engines), "bom")
        self.assertEqual("", row.get("value"), "真的没生成时 value 仍是空串")
        self.assertEqual("none", row.get("status"), "真的没生成时 status 仍是 none")
        self.assertEqual("packaging_bom_v1", row.get("engine_version"), "既有键逐字不变")

    def test_p7_inheritance_clean_chain_reports_empty_flag(self):
        result = _inheritance(_good_engines())
        self.assertEqual({}, result.get("stage_chain_unavailable"),
                         "链条全读到 → 该键必须是空的（键必须存在）")

    def test_p8_inheritance_lists_unavailable_stages_in_chain_order(self):
        engines = _good_engines(bom=_boom)
        engines.pop("packaging_route")
        result = _inheritance(engines)
        flag = result.get("stage_chain_unavailable") or {}
        self.assertEqual("stage_chain_stage_unavailable", flag.get("code"),
                         "有读不到 / 没装的段时必须在这个键上披露")
        stages = flag.get("stages") or {}
        self.assertEqual(["bom", "route"], list(stages),
                         "只含非 engine 的段，且按链条顺序")
        self.assertEqual("unavailable", (stages.get("bom") or {}).get("source"))
        self.assertIn("RuntimeError", str((stages.get("bom") or {}).get("reason") or ""))
        self.assertEqual("absent", (stages.get("route") or {}).get("source"))


# --------------------------------------------------------------------------- #
# P 组（护栏）：链的既有形状与接口都不许被本批改掉
# --------------------------------------------------------------------------- #
class PStageChainUnchanged(unittest.TestCase):
    def test_p9_inheritance_existing_keys_are_verbatim(self):
        result = _inheritance(_good_engines())
        versions = result.get("source_versions") or {}
        self.assertEqual(1, result.get("source_drawing_version"), "锚点字段逐字不变")
        self.assertEqual("zhang", result.get("confirmed_by"), "确认人照旧从链里回溯")
        self.assertEqual(result.get("stage_chain"), versions.get("stage_chain"),
                         "source_versions.stage_chain 与顶层仍是同一份")
        self.assertEqual("reqsnap/1:abc", versions.get("requirement_snapshot_version"),
                         "既有键逐字不变")

    def test_p10_chain_never_raises_even_if_the_resolver_does(self):
        def bad_resolve(name):
            raise RuntimeError("resolver boom")

        chain = anchor.stage_chain(PID, bad_resolve, stage="")
        self.assertEqual(STAGE_ORDER, [row.get("stage") for row in chain],
                         "依赖缝出问题时链照旧返回四段、照旧不抛（GET /drawing-flow 必须 200）")


if __name__ == "__main__":
    unittest.main()
