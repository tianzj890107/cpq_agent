"""红测：门禁段"读不到上游结果"不许说成"这一步还没做"。

Spec：`docs/specs/packaging-gate-read-failure-disclosure.md`

现状缺口（代码级，都可指到行）：
  · `packaging_drawing_flow/gates.py:88-96 _engine()` 把"模块没装"与"调用抛异常"一起吞成 `{}`；
    `:100-110 _policy()` 同理；
  · `_stage_entry()`（`:183-208`）拿这些空值当判据 → `box_match_not_confirmed` /
    `route_not_confirmed` / `cost_not_built`，文案是"尚未确认 / 尚未生成 / 尚未测算"；
  · `blocking_message()`（`:236-248`）把这条 message 当唯一结论交给用户 ——
    上游服务读不到时，用户被告知"这一步还没做"（去重新确认盒型 / 重新排路线也不会有用）。

纪律：只读源码 + 打桩 `store.load_requirement` / `persistence.load_flow` / `anchor_mod`
与假依赖模块；不连 PG / SQLite 生产库、不发 HTTP、不建项目、不写盘。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 包 `__init__` 里有一个同名函数 `gates()`，会把子模块属性遮掉 —— 走 importlib 拿真模块。
gates = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.gates")

PID = "testpid00001"
CONFIRMED_FIELDS = {"inner_length": 70.0, "inner_width": 40.0, "inner_height": 120.0,
                    "closure_type": "tuck", "face_paper_gsm": 350.0, "v_groove": False,
                    "quote_quantity": 1000.0}


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


class _Module:
    def __init__(self, **functions):
        for name, value in functions.items():
            setattr(self, name, value)


def _boom(*_args, **_kwargs):
    raise RuntimeError("upstream store down")


def _requirement(fields=None):
    data = dict(CONFIRMED_FIELDS if fields is None else fields)
    data["field_sources"] = {key: "manual" for key in (fields if fields is not None
                                                      else CONFIRMED_FIELDS)}
    data["field_provenance"] = {key: {"status": "confirmed", "origin": "user_confirmed"}
                                for key in (fields if fields is not None else CONFIRMED_FIELDS)}
    return {"project_id": PID, "status": "draft", "data": data}


def _engines(*, box=None, bom=None, route=None, cost=None, handoff=None):
    """假依赖模块；传函数（如 `_boom`）就表示这一处会抛。"""
    return {
        "packaging_match": _Module(load_box_match=box or (lambda *a: {
            "decision": "confirmed", "confirmed_box_type": "folding_carton"})),
        "packaging_bom": _Module(load_bom=bom or (lambda *a: {
            "built": True, "generated_at": "2026-09-20T11:00:00+08:00"})),
        "packaging_route": _Module(load_route=route or (lambda *a: {
            "built": True, "status": "confirmed"})),
        "packaging_cost": _Module(load_cost=cost or (lambda *a: {
            "built": True, "has_gaps": False, "items": []}),
            minimum_charge_policy=lambda: {"status": "chosen", "chosen": "sheet_labor_rate"}),
        "packaging_handoff": _Module(load_handoff=handoff or (lambda *a: {})),
    }


def _stages(engines, *, drop=(), fields=None):
    """跑一遍 `gates.build()`，返回 `{stage: entry}`。"""
    def resolve(name):
        return engines.get(name)

    for name in drop:
        engines.pop(name, None)
    with _Patch((gates.store, "load_requirement",
                 lambda project_id: _requirement(fields)),
                (gates.persistence, "load_flow", lambda project_id: {}),
                (gates.anchor_mod, "current_anchor",
                 lambda project_id: {"unit_status": "confirmed"}),
                (gates.anchor_mod, "requirement_snapshot_version",
                 lambda project_id: "reqsnap/1:abc")):
        return gates.build(PID, resolve=resolve)["stages"]


# --------------------------------------------------------------------------- #
# S 组：三态（读到 / 确实没做 / 读不到）必须两两可分
# --------------------------------------------------------------------------- #
class SGateReadFailure(unittest.TestCase):
    def test_s1_bom_gate_discloses_an_unreadable_box_match(self):
        entry = _stages(_engines(box=_boom))["bom"]
        reads = entry.get("reads") or {}
        self.assertEqual("unavailable", (reads.get("packaging_match") or {}).get("source"),
                         "读盒型结论抛异常时必须标出来（Spec §2.1）：今天的门禁只说"
                         "'盒型尚未确认'，用户会去重新确认一遍")
        flag = entry.get("reads_unavailable") or {}
        self.assertEqual("gate_read_unavailable", flag.get("code"))
        self.assertEqual(["packaging_match"], flag.get("dependencies"))
        self.assertEqual("blocked", entry.get("status"),
                         "结论不改：读不到照旧 blocked（本批只要求说出来）")
        codes = [item.get("code") for item in entry.get("blocking") or []]
        self.assertIn("box_match_not_confirmed", codes,
                      "既有 blocking 行照旧存在（只在其上补披露）")

    def test_s2_cost_gate_discloses_an_unreadable_route(self):
        entry = _stages(_engines(route=_boom))["cost"]
        reads = entry.get("reads") or {}
        self.assertEqual("unavailable", (reads.get("packaging_route") or {}).get("source"))
        self.assertIn("route_not_confirmed",
                      [item.get("code") for item in entry.get("blocking") or []],
                      "既有 blocking 行照旧")

    def test_s3_quote_publish_discloses_an_unreadable_cost(self):
        entry = _stages(_engines(cost=_boom))["quote_publish"]
        reads = entry.get("reads") or {}
        self.assertEqual("unavailable", (reads.get("packaging_cost") or {}).get("source"),
                         "成本读不到时该段必须标出来（不是'成本尚未测算'）")
        self.assertIn("cost_not_built",
                      [item.get("code") for item in entry.get("blocking") or []],
                      "既有 blocking 行照旧")

    def test_s4_clean_reads_report_engine(self):
        stages = _stages(_engines())
        expected = {"bom": "packaging_match", "route": "packaging_bom",
                    "cost": "packaging_route", "quote_draft": "packaging_cost"}
        for stage, dependency in expected.items():
            entry = stages[stage]
            reads = entry.get("reads") or {}
            self.assertEqual("engine", (reads.get(dependency) or {}).get("source"),
                             "%s 段读到了 %s → 该段要标 engine（键必须存在）" % (stage, dependency))
            self.assertEqual({}, entry.get("reads_unavailable"),
                             "%s 段没有读失败 → 披露键必须是空的" % stage)

    def test_s5_absent_dependency_is_not_a_read_failure(self):
        entry = _stages(_engines(), drop=("packaging_match",))["bom"]
        reads = entry.get("reads") or {}
        self.assertEqual("absent", (reads.get("packaging_match") or {}).get("source"),
                         "模块没装是'这个部署没有它'，与'读挂了'必须分家（Spec §2.1）")

    def test_s6_gate_message_does_not_claim_the_step_is_not_done(self):
        entry = _stages(_engines(box=_boom))["bom"]
        message = gates.blocking_message(entry)
        self.assertNotIn("尚未", message,
                         "读不到上游时不许说'尚未确认 / 尚未生成 / 尚未测算'（Spec §2.1）")
        self.assertIn("重试", message, "读不到是可重试的，文案要给出重试")


# --------------------------------------------------------------------------- #
# S 组（护栏）：门禁结论与既有口径不许被本批改掉
# --------------------------------------------------------------------------- #
class SGateContractUnchanged(unittest.TestCase):
    def test_s7_existing_message_is_verbatim_without_read_failures(self):
        entry = _stages(_engines(route=lambda *a: {}))["cost"]
        self.assertEqual("blocked", entry.get("status"), "路线确实没确认 → 照旧 blocked")
        self.assertIn("route_not_confirmed",
                      [item.get("code") for item in entry.get("blocking") or []])
        self.assertEqual("工艺路线尚未确认，确认后才能测算成本",
                         gates.blocking_message(entry),
                         "没有读失败时既有文案逐字不变")

    def test_s8_blocking_codes_stay_in_the_closed_set(self):
        for engines in (_engines(), _engines(box=_boom), _engines(route=_boom),
                        _engines(cost=_boom)):
            for stage, entry in _stages(engines).items():
                for item in entry.get("blocking") or []:
                    self.assertIn(str(item.get("code")), gates.BLOCKING_CODES,
                                  "%s 段的 blocking 码必须仍在闭集里" % stage)
                self.assertIn(entry.get("status"), ("blocked", "open"),
                              "%s 段的 status 取值不变" % stage)

    def test_s9_field_judgement_is_not_touched(self):
        entry = _stages(_engines(), fields={})["box_match"]
        self.assertEqual("blocked", entry.get("status"), "缺字段照旧 blocked")
        codes = [item.get("code") for item in entry.get("blocking") or []]
        self.assertIn("field_missing", codes, "字段判据没被本批改掉")


if __name__ == "__main__":
    unittest.main()
