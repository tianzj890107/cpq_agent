"""红测：2.1 解析出的「待确认」需求字段，必须有一条人工采纳的通道，采纳后盒型匹配才看得到。

Spec：`docs/specs/packaging-2-1-candidate-fields-into-downstream.md`

现状缺口（34 线上实测，不是推断；细节见 Spec §1）：

  · 项目 `9ab4fe6ce416`（圆盘盒-方案1-上半.dwg）一键解析 `status=completed`、八步全绿，
    语义文档已经算出成品尺寸候选 `inner_length = inner_width = 396.6`（`status=needs_confirmation`、
    `board=pending`、`confidence=0.8`、圆直径带内，符合 `## 223`）；
  · 但同一次运行的 `field_write` 返回 `written = []`、`pending = [box_type, inner_length,
    inner_width, insert_type]` —— 候选只躺在语义文档里，**没有进需求单**；
  · 下游 `downstream_prepare` 于是把 `box_match / bom / route / cost` 全判成 `field_missing`
    （"缺少内长/内宽/内高/闭合方式，source=requirement"）；
  · 实测 `POST /requirement/box-match/decision` → 409
    `box_type_not_confirmable：YT-DWG-ROUND-10PC（needs_input）`，`POST packaging-bom/route/cost`
    → 409 `box_type_not_confirmed`，`POST /integration/send-to-finance` → 400「请先完成参数推荐」。
  · 对照项目 `6beeeebb19a1`（酒盒）能一路走到 2.3，是因为**人在需求文本里把
    「成品内尺寸220.5×90×90mm；面纸225g；…」又写了一遍**（`field_sources=user_text`），
    不是图纸读出来的 —— 只要不给文本答案（圆盘盒就是），这一段就断。

根因：`packaging_drawing_flow/steps.py:668` 的 `_manual_accept_fields()` 只收
`field_sources[key] == "manual"` 且已有值的字段，而图纸候选的 `field_sources` 从来不是
`manual`；`packaging_semantics.apply_to_requirement(accept=...)` 本身支持按字段确认
（`provenance.py:170-186`），但**没有任何路由/界面**把"采纳这批候选"送进来，
`field_write` 也不会（也不该）自动采纳。

本批只补「采纳」这一条通道与它的守卫：候选值必须由人逐个确认才写进需求单，
写入后来源记为人工确认、留痕确认人与时间，盒型匹配随即读得到；解析本身仍然不许自动采纳，
单位未确认时绝对尺寸仍然不许确认，人工值与候选冲突时不许静默覆盖。

纪律：全部离线（不连 PG、不调模型、不起服务），用内存需求看板 + 内存语义文档。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_packaging_semantics_red import SemanticsCase  # noqa: E402

MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
FLOW_PKG = "tech_app.backend.services.packaging_drawing_flow"
MATCH_PKG = "tech_app.backend.services.packaging_match"

PROJECT_ID = "proj-candidate-adopt"
#: 34 实测的圆盘盒候选：产品级圆直径带内的成形尺寸。
ROUND_DIAMETER = 396.6
SIZE_KEYS = ("inner_length", "inner_width", "inner_height")

#: 采纳被拒时的稳定码（Spec §2.2）。
CODE_CANDIDATE_MISSING = "candidate_missing"
CODE_CANDIDATE_UNKNOWN = "candidate_unknown"
CODE_MANUAL_CONFLICT = "manual_conflict"
CODE_UNIT_UNCONFIRMED = "unit_unconfirmed"


def candidate(value, *, status="needs_confirmation", origin="inferred_from_geometry",
              confidence=0.8, unit="mm", refs=("ev:E:7F02D",)):
    return {"origin": origin, "status": status, "value": value, "unit": unit,
            "confidence": confidence, "evidence_level": "CAD",
            "evidence_refs": list(refs), "conflicts": []}


def missing():
    return {"origin": "missing", "status": "missing", "value": None, "unit": "",
            "confidence": 0.0, "evidence_level": "NONE", "evidence_refs": [], "conflicts": []}


def round_semantics():
    """34 实测的圆盘盒语义字段看板（只保留本批用到的键）。"""
    return {"semantics_id": "sem-round-adopt", "semantics_version": "packaging-semantics/1",
            "fields": {"inner_length": candidate(ROUND_DIAMETER),
                       "inner_width": candidate(ROUND_DIAMETER),
                       "inner_height": missing(),
                       "closure_type": missing(),
                       "box_type": candidate("round_tube", confidence=0.4,
                                             refs=("ev:E:7F02D",))}}


def empty_requirement_data():
    return {"industry": "packaging", "quote_quantity": 1000,
            "field_provenance": {"inner_length": dict(missing()),
                                 "inner_width": dict(missing())},
            "field_sources": {}}


class AdoptCase(SemanticsCase):
    def case(self, data=None, *, unit_status="confirmed"):
        service, req, state, project_id = self.memory_requirement(
            data=data if data is not None else empty_requirement_data(),
            project_id=PROJECT_ID)
        anchor = importlib.import_module(FLOW_PKG + ".anchor")
        patch = mock.patch.object(anchor, "current_anchor",
                                  lambda pid: {"unit_status": unit_status, "ir_id": "ir-1",
                                               "ir_hash": "hash-1"})
        patch.start()
        self.addCleanup(patch.stop)
        return service, req, state, project_id

    def adopt_fn(self):
        package = self.module()
        fn = getattr(package, "adopt_requirement_candidates", None)
        self.assertTrue(callable(fn),
                        "packaging_semantics 必须导出 adopt_requirement_candidates()（Spec §2.1）")
        return fn

    def adopt(self, project_id, keys, **kwargs):
        kwargs.setdefault("semantics", round_semantics())
        kwargs.setdefault("actor", "PE1")
        return self.adopt_fn()(project_id, keys, **kwargs)

    def data(self, req):
        return req.get("data") or {}

    def codes(self, result, bucket):
        rows = result.get(bucket) or []
        out = []
        for row in rows:
            out.append(row.get("code") if isinstance(row, dict) else row)
        return sorted(str(code) for code in out if code)


# --------------------------------------------------------------------------- #
# A 组：采纳本身（Spec §2.1/§2.3）
# --------------------------------------------------------------------------- #
class ACandidateAdoption(AdoptCase):

    def test_a1_drawing_candidates_are_not_written_by_parsing(self):
        """反例守卫：解析自己仍然不许把 needs_confirmation 候选写进需求单。"""
        service, req, _state, project_id = self.case()
        flow_steps = importlib.import_module(FLOW_PKG + ".steps")
        resolve = lambda name: importlib.import_module(  # noqa: E731
            "tech_app.backend.services." + name)
        ctx = {"project_id": project_id, "run_id": "run-1",
               "semantics": round_semantics(), "resolve": resolve, "actor": "PE1",
               "anchor": {"unit_status": "confirmed", "ir_id": "ir-1", "ir_hash": "hash-1"}}
        step = flow_steps.field_write(ctx)
        detail = step.get("detail") or {}
        self.assertEqual("completed", step.get("status"))
        self.assertEqual([], list(detail.get("written") or []),
                         "解析不许自动采纳候选（Spec §2.3）")
        self.assertIn("inner_length", list(detail.get("pending") or []))
        self.assertIsNone(self.data(req).get("inner_length"),
                          "没采纳之前需求单上不许有值")

    def test_a2_adopting_writes_value_source_and_provenance(self):
        service, req, state, project_id = self.case()
        result = self.adopt(project_id, ["inner_length", "inner_width"])
        self.assertEqual(["inner_length", "inner_width"], sorted(result.get("adopted") or []))
        data = self.data(req)
        self.assertAlmostEqual(float(data["inner_length"]), ROUND_DIAMETER, places=6)
        self.assertAlmostEqual(float(data["inner_width"]), ROUND_DIAMETER, places=6)
        self.assertEqual("manual", (data.get("field_sources") or {}).get("inner_length"),
                         "采纳是人确认的动作，来源必须记成人工确认")
        row = (data.get("field_provenance") or {}).get("inner_length") or {}
        self.assertEqual("confirmed", row.get("status"))
        self.assertEqual("user_confirmed", row.get("origin"))
        self.assertAlmostEqual(float(row.get("value")), ROUND_DIAMETER, places=6)
        self.assertEqual("PE1", row.get("adopted_by"), "采纳必须留痕是谁确认的")
        self.assertTrue(row.get("adopted_at"), "采纳必须留痕确认时间")
        self.assertTrue(state.get("saved"), "采纳必须落盘（save_requirement 至少调用一次）")

    def test_a3_adoption_is_recorded_in_audit(self):
        service, _req, state, project_id = self.case()
        self.adopt(project_id, ["inner_length"])
        actions = [str((row[0] or [""])[0]) for row in (state.get("audit") or [])]
        self.assertTrue(any("candidate" in action and "adopt" in action for action in actions),
                        "采纳要有审计动作：%r" % actions)

    def test_a4_unknown_candidate_never_becomes_a_value(self):
        service, req, _state, project_id = self.case()
        result = self.adopt(project_id, ["inner_length", "closure_type"])
        self.assertEqual(["inner_length"], sorted(result.get("adopted") or []))
        self.assertIn(CODE_CANDIDATE_MISSING, self.codes(result, "rejected"))
        self.assertIsNone(self.data(req).get("closure_type"),
                          "候选没值就不许凭「采纳」造一个出来")

    def test_a6_only_pending_or_confirmed_candidates_are_adoptable(self):
        """冲突候选必须先由人裁定，不许借"采纳"这个动作翻成 confirmed（Spec §2.2）。"""
        sem = round_semantics()
        sem["fields"]["box_type"] = candidate("round_tube", status="conflict", confidence=0.3)
        service, req, _state, project_id = self.case()
        result = self.adopt_fn()(project_id, ["box_type"], semantics=sem, actor="PE1")
        self.assertEqual([], sorted(result.get("adopted") or []))
        self.assertNotIn("box_type", self.data(req))

    def test_a7_adopting_an_already_confirmed_candidate_still_records_manual_source(self):
        """采纳本身就是人确认动作：候选此前已是 confirmed，来源同样记成 manual（Spec §2.2）。"""
        sem = round_semantics()
        sem["fields"]["box_type"] = candidate("round_tube", status="confirmed",
                                             origin="confirmed_from_cad")
        service, req, _state, project_id = self.case()
        result = self.adopt_fn()(project_id, ["box_type"], semantics=sem, actor="PE1")
        self.assertEqual(["box_type"], sorted(result.get("adopted") or []))
        self.assertEqual("manual", (self.data(req).get("field_sources") or {}).get("box_type"))

    def test_a5_unknown_field_is_rejected(self):
        service, req, _state, project_id = self.case()
        result = self.adopt(project_id, ["not_a_real_field"])
        self.assertEqual([], sorted(result.get("adopted") or []))
        self.assertIn(CODE_CANDIDATE_UNKNOWN, self.codes(result, "rejected"))
        self.assertNotIn("not_a_real_field", self.data(req))


# --------------------------------------------------------------------------- #
# B 组：三条不许放宽的口径（Spec §2.2）
# --------------------------------------------------------------------------- #
class BGuards(AdoptCase):

    def test_b1_manual_value_is_never_overwritten(self):
        data = empty_requirement_data()
        data["inner_length"] = 500
        data["field_sources"]["inner_length"] = "manual"
        service, req, _state, project_id = self.case(data)
        result = self.adopt(project_id, ["inner_length"])
        self.assertEqual([], sorted(result.get("adopted") or []))
        self.assertIn(CODE_MANUAL_CONFLICT, self.codes(result, "conflicts"))
        self.assertEqual(500, self.data(req).get("inner_length"),
                         "人工值与候选冲突时必须保留人工值并请人裁定（Spec §2.2）")

    def test_b2_agreeing_manual_value_is_not_re_asked(self):
        data = empty_requirement_data()
        data["inner_length"] = ROUND_DIAMETER
        data["field_sources"]["inner_length"] = "manual"
        service, req, _state, project_id = self.case(data)
        result = self.adopt(project_id, ["inner_length"])
        self.assertIn("inner_length", sorted(result.get("adopted") or []))
        self.assertEqual([], self.codes(result, "conflicts"))

    def test_b3_unit_unconfirmed_never_confirms_absolute_size(self):
        data = empty_requirement_data()
        service, req, _state, project_id = self.case(data, unit_status="unconfirmed")
        result = self.adopt(project_id, ["inner_length", "inner_width"])
        self.assertEqual([], sorted(result.get("adopted") or []))
        self.assertIn(CODE_UNIT_UNCONFIRMED, self.codes(result, "rejected"))
        self.assertIsNone(self.data(req).get("inner_length"),
                          "单位未确认时绝对尺寸不许 confirmed（既有口径不许放宽）")

    def test_b4_non_size_field_can_still_be_adopted_without_units(self):
        sem = round_semantics()
        sem["fields"]["box_type"] = candidate("round_tube", unit="", confidence=0.4,
                                             refs=("ev:E:7F02D",))
        service, req, _state, project_id = self.case(unit_status="unconfirmed")
        result = self.adopt_fn()(project_id, ["box_type"], semantics=sem, actor="PE1")
        self.assertIn("box_type", sorted(result.get("adopted") or []),
                      "非绝对尺寸字段不受单位门禁影响")


# --------------------------------------------------------------------------- #
# C 组：采纳之后下游读得到（Spec §2.3）
# --------------------------------------------------------------------------- #
class CDownstreamSeesIt(AdoptCase):

    def test_c1_box_match_inputs_become_available(self):
        match = importlib.import_module(MATCH_PKG)
        service, req, _state, project_id = self.case()
        _doc, _data, before = match._requirement_inputs(project_id)
        self.assertIsNone(before.get("inner_length"),
                          "没采纳之前盒型匹配必须如实说读不到内长")
        self.adopt(project_id, ["inner_length", "inner_width"])
        _doc, _data, after = match._requirement_inputs(project_id)
        self.assertAlmostEqual(float(after["inner_length"]), ROUND_DIAMETER, places=6)
        self.assertAlmostEqual(float(after["inner_width"]), ROUND_DIAMETER, places=6)

    def test_c2_main_exposes_the_adoption_route(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("PACKAGING_FIELD_CANDIDATE_ADOPT_PATH", source,
                      "必须有一条具名常量路由（Spec §2.4），便于契约测试钉住")
        self.assertIn("@app.post(PACKAGING_FIELD_CANDIDATE_ADOPT_PATH)", source,
                      "采纳入口必须把具名常量用在 POST 装饰器上")
        self.assertIn("adopt_requirement_candidates", source,
                      "路由必须调用同一个服务入口，不许另写一套写入逻辑")


if __name__ == "__main__":
    unittest.main()
