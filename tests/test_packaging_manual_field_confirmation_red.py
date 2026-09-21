"""红测：人工录入/确认的需求字段必须能让图纸链路门禁转绿。

Spec：`docs/specs/packaging-manual-field-confirmation.md`
依赖：包装语义第 4 批（`packaging_semantics`）、图纸链路第 5 批（`packaging_drawing_flow`）。

现状缺口（34 线上实测，不是推断；细节见 Spec §1）：

  · 项目 `f1417060ae9d` 的需求单里 `inner_length="200"`、`field_sources.inner_length="manual"`，
    跑完一键解析后 `field_provenance.inner_length = {origin:"missing", status:"missing", value:null}`；
  · `gates.stages.box_match` 因此 `blocked` + `field_unconfirmed`（"内长尚未确认"），
    `bom / route / cost / quote_publish` 依次全被挡住 —— 值明明在，没有任何界面动作能开这扇门；
  · `provenance.apply_to_requirement()` 的人工分支用 `setdefault` 改**已经带 status 的**快照，
    永远升不到 `confirmed`；
  · `apply_to_requirement(accept=...)` 的业务调用点恒传 `accept=()`（steps.py:405），
    "人工确认某个字段"这条通道在业务代码里不存在。

三组口径不许被放宽（Spec §2.1）：值为空仍然 missing；冲突证据仍然 conflict；
单位未确认时图纸侧绝对尺寸仍然不许 confirmed。

纪律：全部离线（不连 PG、不调模型、不起服务），用 `SemanticsCase` 的内存需求看板。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_packaging_semantics_red import SemanticsCase  # noqa: E402

PROVENANCE_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_semantics" / "provenance.py"
GATES_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "gates.py"
STEPS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "steps.py"
FLOW_PKG = "tech_app.backend.services.packaging_drawing_flow"

PROJECT_ID = "proj-manual-field"
CONFIRMED_BOARDS = ("inner_length", "inner_width", "inner_height", "closure_type")


def missing_candidate():
    return {"origin": "missing", "status": "missing", "value": None, "confidence": 0.0,
            "evidence_level": "NONE", "evidence_refs": [], "conflicts": []}


def semantics_doc(**fields):
    """一份足够小的语义文档：fields 里每个键都给一个 missing 候选。"""
    return {"semantics_id": "sem-manual-field",
            "fields": {key: missing_candidate() for key in fields or ()}}


def manual_requirement_data(fields=CONFIRMED_BOARDS, *, values=None, provenance=None,
                            board=None):
    """34 实测的需求单形状：值在 + 来源 manual + 看板仍是 missing。"""
    values = dict(values or {})
    data = {}
    for key in fields:
        data[key] = values.get(key, {"inner_length": "200", "inner_width": "150",
                                     "inner_height": "80", "closure_type": "天地盖"}
                              .get(key, "有值"))
        data.setdefault("field_sources", {})[key] = "manual"
    data["field_provenance"] = copy.deepcopy(
        provenance if provenance is not None
        else {key: dict(missing_candidate()) for key in fields})
    if board is not None:
        data["field_provenance"].update(copy.deepcopy(board))
    return data


class ManualFieldCase(SemanticsCase):
    def case(self, data):
        return self.memory_requirement(data=data, project_id=PROJECT_ID)

    def gates_module(self):
        try:
            return importlib.import_module(FLOW_PKG + ".gates")
        except ModuleNotFoundError as exc:                       # pragma: no cover
            self.fail("依赖图纸链路第 5 批（`packaging_drawing_flow.gates`）：%s" % exc)

    def gate_stage(self, data, stage):
        """在内存需求单上跑真实门禁：引擎解析器一律给空，只留字段判定。"""
        flow_pkg = importlib.import_module(FLOW_PKG)
        persistence = importlib.import_module(FLOW_PKG + ".persistence")
        anchor = importlib.import_module(FLOW_PKG + ".anchor")
        store = importlib.import_module("tech_app.backend.storage.store")
        doc = {"project_id": PROJECT_ID, "requirement_no": "REQ-1", "status": "draft",
               "title": "包装需求", "data": copy.deepcopy(data), "history": [], "waivers": []}
        patches = [
            mock.patch.object(store, "load_requirement", lambda pid: copy.deepcopy(doc)),
            mock.patch.object(persistence, "load_flow", lambda pid: {}),
            mock.patch.object(anchor, "current_anchor", lambda pid: {}),
            mock.patch.object(anchor, "requirement_snapshot_version", lambda pid: "reqsnap/1:test"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.assertTrue(hasattr(flow_pkg, "gates") or True)
        return self.gates_module().build(PROJECT_ID, resolve=lambda name: None, stage=stage)


# --------------------------------------------------------------------------- #
# A 组：provenance 分支行为（Spec §2.2）
# --------------------------------------------------------------------------- #
class AProvenanceBoard(ManualFieldCase):

    def test_a1_manual_value_is_marked_confirmed(self):
        data = manual_requirement_data(fields=("inner_length",))
        service, req, _state, project_id = self.case(data)
        self.apply(semantics_doc(inner_length=True), service, project_id)
        row = ((req.get("data") or {}).get("field_provenance") or {}).get("inner_length") or {}
        self.assertEqual(row.get("status"), "confirmed",
                         "人工填的值必须把看板写成人已确认，不能停在 missing（Spec §2.2）")
        self.assertEqual(row.get("origin"), "user_confirmed")
        self.assertEqual(row.get("value"), "200", "看板里的 value 必须是这个字段当前值")

    def test_a2_manual_value_is_never_overwritten(self):
        data = manual_requirement_data(fields=("inner_length",))
        service, req, _state, project_id = self.case(data)
        self.apply(semantics_doc(inner_length=True), service, project_id)
        self.assertEqual((req.get("data") or {}).get("inner_length"), "200",
                         "人工值一个字都不许改（Spec §2.2）")
        self.assertEqual(((req.get("data") or {}).get("field_sources") or {}).get("inner_length"),
                         "manual", "来源不许被降级")

    def test_a3_board_confirms_even_when_candidate_has_no_value(self):
        data = manual_requirement_data(fields=("closure_type",),
                                       values={"closure_type": "天地盖"})
        service, req, _state, project_id = self.case(data)
        self.apply(semantics_doc(closure_type=True), service, project_id)
        row = ((req.get("data") or {}).get("field_provenance") or {}).get("closure_type") or {}
        self.assertEqual((row.get("status"), row.get("origin")), ("confirmed", "user_confirmed"),
                         "图纸没有这个字段的证据时，人工值仍然是已确认（Spec §2.1 第 2 条）")

    def test_a4_plain_value_without_manual_source_stays_unconfirmed(self):
        data = {"inner_length": "200", "field_sources": {},
                "field_provenance": {"inner_length": dict(missing_candidate())}}
        service, req, _state, project_id = self.case(data)
        self.apply(semantics_doc(inner_length=True), service, project_id)
        row = ((req.get("data") or {}).get("field_provenance") or {}).get("inner_length") or {}
        self.assertNotEqual(row.get("status"), "confirmed",
                            "没有人工来源、图纸也没确认 → 不许凭「有值」升级（Spec §2.1）")

    def test_a5_empty_manual_value_is_not_confirmed_by_the_board(self):
        data = manual_requirement_data(fields=("inner_length",), values={"inner_length": ""})
        data["field_provenance"]["inner_length"] = {"origin": "user_confirmed",
                                                    "status": "confirmed", "value": "",
                                                    "confidence": 1.0, "evidence_level": "NONE",
                                                    "evidence_refs": [], "conflicts": [],
                                                    "alternatives": []}
        service, req, _state, project_id = self.case(data)
        self.apply(semantics_doc(inner_length=True), service, project_id)
        row = ((req.get("data") or {}).get("field_provenance") or {}).get("inner_length") or {}
        self.assertNotEqual(row.get("status"), "confirmed",
                            "值为空就不存在「人工确认过的值」，不许写成 confirmed（批 12 §3.3）")


# --------------------------------------------------------------------------- #
# B 组：真实门禁行为（Spec §2.1）
# --------------------------------------------------------------------------- #
class BGateBehaviour(ManualFieldCase):

    def test_b1_manual_fields_open_the_box_match_gate(self):
        entry = self.gate_stage(manual_requirement_data(), "box_match")
        self.assertEqual(entry.get("status"), "open",
                         "人工录入且来源 manual 的字段必须开门禁，34 实测今天是 blocked：%s"
                         % [(item.get("code"), item.get("field")) for item in entry.get("blocking") or []])

    def test_b2_blocking_codes_disappear_not_just_the_message(self):
        entry = self.gate_stage(manual_requirement_data(), "box_match")
        self.assertEqual([item.get("field") for item in (entry.get("blocking") or [])
                          if item.get("code") in ("field_missing", "field_unconfirmed")], [],
                         "开门禁必须真的没有 field_* 阻断项，不是换一句文案（Spec §2.1）")

    def test_b3_empty_value_still_blocks(self):
        data = manual_requirement_data(fields=("inner_length",), values={"inner_length": ""})
        entry = self.gate_stage(data, "box_match")
        codes = {(item.get("code"), item.get("field")) for item in entry.get("blocking") or []}
        self.assertIn(("field_missing", "inner_length"), codes,
                      "空值必须仍然是 field_missing —— 不许因为写了 manual 就放行（Spec §2.1）")

    def test_b4_conflict_still_blocks_human_source(self):
        board = {"inner_length": {"origin": "conflict", "status": "conflict", "value": None,
                                  "confidence": 0.0, "evidence_level": "CONTRADICTORY",
                                  "evidence_refs": [], "conflicts": [{"a": 1}]}}
        entry = self.gate_stage(manual_requirement_data(fields=("inner_length",), board=board),
                                "box_match")
        codes = {(item.get("code"), item.get("field")) for item in entry.get("blocking") or []}
        self.assertIn(("field_conflict", "inner_length"), codes,
                      "冲突证据必须先人工裁定，人工来源也不例外（Spec §2.1）")

    def test_b5_geometry_confirmed_fields_still_open(self):
        values = {"inner_length": 200.0, "inner_width": 150.0, "inner_height": 80.0,
                  "closure_type": "天地盖"}
        data = {"field_sources": {key: "attachment" for key in values},
                "field_provenance": {
                    key: {"origin": "confirmed_from_cad", "status": "confirmed",
                          "value": value, "confidence": 0.9, "evidence_level": "STRONG",
                          "evidence_refs": [], "conflicts": [], "alternatives": []}
                    for key, value in values.items()}}
        data.update(values)
        entry = self.gate_stage(data, "box_match")
        self.assertEqual(entry.get("status"), "open", "图纸已确认这条路径不许被本批改坏")


# --------------------------------------------------------------------------- #
# C 组：源码契约（Spec §2.2 / §2.3）
# --------------------------------------------------------------------------- #
class CSourceContract(ManualFieldCase):

    def test_c1_manual_branch_sets_status_and_origin_explicitly(self):
        body = PROVENANCE_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("user_confirmed", body, "provenance.py 必须仍然有人工确认分支")
        window = body[body.find("def apply_to_requirement"):]
        window = window[: window.find("def ", 10) if window.find("def ", 10) > 0 else len(window)]
        self.assertNotIn('setdefault("status"', window,
                         "人工确认分支不许用 setdefault 改已经带 status 的候选快照（Spec §2.2）")
        self.assertIn('"confirmed"', window, "人工分支必须显式把 status 写成 confirmed")

    def test_c2_accept_is_called_with_real_fields_somewhere(self):
        """业务侧必须真的把字段集交给确认通道 —— packaging_semantics 里的转发包装不算。"""
        pkg_dir = (ROOT / "tech_app" / "backend" / "services" / "packaging_semantics")
        found = []
        for path in sorted((ROOT / "tech_app" / "backend").rglob("*.py")):
            if pkg_dir in path.parents:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if "accept=" in line and "accept=()" not in line and "accept=( )" not in line:
                    found.append("%s: %s" % (path.name, line.strip()))
        self.assertTrue(found,
                        "必须存在一条人工确认字段的真实调用路径（accept 不能永远是空集，Spec §2.3）；"
                        "当前唯一调用点 steps.py 恒传 accept=()")

    def test_c3_gates_keeps_the_three_refusals(self):
        body = GATES_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("field_missing", body, "空值仍然要报 field_missing（Spec §2.1）")
        self.assertIn("field_conflict", body, "冲突仍然要报 field_conflict（Spec §2.1）")
        self.assertIn("field_unconfirmed", body, "图纸未确认这条路径必须保留")


if __name__ == "__main__":
    unittest.main()
