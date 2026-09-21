"""红测：DWG 下游全流程剩余三处缝 —— 放行留痕过桥 / 需求退回草稿 / 人工来源空值。

Spec：docs/specs/packaging-downstream-blockers-close-loop.md
依赖：包装第 8 批（交接与定价）、`packaging-dwg-parts-extraction`（C7 回填）、
`packaging-product-outline-and-die-layer-roles`（第 4b 批语义）已完成。

现状缺口（三条都有线上实测证据，不是推断；细节见 Spec §1）：
  · 技术侧 `packaging_handoff._guard_gaps()` 已经接受 `allow_gaps=True + reason` 并把 `gap_waiver`
    写进交接记录，但交给报价侧桥的正文**不含**这份留痕；`cpq_tech_bridge._guard_packaging_result()`
    只看 `has_gaps`，于是 34 上最后一步 `POST .../packaging-quote/send` 必然 **500**。
  · `requirement_service.return_requirement_to_draft()` 只接受 `pending_confirmation`，
    而 `EDITABLE_STATUSES` 只有 `draft/rejected` → 需求一旦被批准就**没有任何合法路径**回到可编辑态，
    `field_write` 永久 blocked（而系统给用户的 action 里写着"请先退回草稿"）。
  · `packaging_semantics/provenance._is_user_confirmed()` 只看 `field_sources == "manual"`，
    不看值是否为空 → `data.closure_type` 恒为空、`field_provenance` 却写 `user_confirmed`。
  · `packaging_parts.bind_rows()` 的配对是纯位置（行顺序 ↔ 面积降序），34 上把 `RB02001-P08`（磁铁）
    配到 443.5×492.6 的纸面板上，且报告里**没有任何地方**能看出这个配对不可信。

本批只写 Spec + 红测（AGENTS.md：Codex 不直接编写业务实现）。
夹具复用既有冻结测试模块（`tests/` 下同一目录），**不复制**它们的常量与假库：
  · `tests.test_packaging_quote_close_loop_red` —— 报价侧受控假库（真跑 cpq_wf / cpq_tech_bridge）；
  · `tests.test_packaging_semantics_red`       —— 需求写入的内存沙盘；
  · `tests.test_packaging_parts_extraction_red` —— BOM 回填的 KB 沙盘与 CAD IR 夹具。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import importlib
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_packaging_parts_extraction_red import PartsCase  # noqa: E402
from tests.test_packaging_quote_close_loop_red import (  # noqa: E402
    FINANCE, PID, REQ, HandoffCase, QuoteStoreCase, cost_fixture, package_fixture)
from tests.test_packaging_semantics_red import SemanticsCase  # noqa: E402

GAP_CODE = "no_formula:print"
WAIVER_REASON = "客户同意按缺料价先出草稿，工艺与财务各留一次签字"

REQUIREMENT_SERVICE = "tech_app.backend.services.requirement_service"


def load_bridge():
    """报价侧桥（根目录模块）；不存在时给明确断言，不抛 ImportError。"""
    try:
        return importlib.import_module("cpq_tech_bridge")
    except Exception as exc:                                    # noqa: BLE001
        raise AssertionError("缺少 cpq_tech_bridge（报价侧桥）：%s" % exc)


def load_requirement_service():
    try:
        return importlib.import_module(REQUIREMENT_SERVICE)
    except Exception as exc:                                    # noqa: BLE001
        raise AssertionError("缺少 %s：%s" % (REQUIREMENT_SERVICE, exc))


def gap_package(waiver=None, **over):
    """一份**有缺口**的包装包（缺口逐条可列举）；waiver 非 None 时挂在正文顶层。"""
    package = package_fixture(**over)
    package["cost"] = cost_fixture(
        has_gaps=True, gaps=[{"code": GAP_CODE, "where": "BOX-MAIN/print", "detail": "手填列"}])
    package["gaps"] = package["cost"]["gaps"]
    if waiver is not None:
        package["gap_waiver"] = waiver
    return package


def good_waiver(**over):
    waiver = {"by": "fin1", "at": "2026-09-21T20:00:00+08:00", "reason": WAIVER_REASON,
              "codes": [GAP_CODE]}
    waiver.update(over)
    return waiver


# --------------------------------------------------------------------------- #
# A. 报价侧桥必须读放行留痕（真跑 cpq_tech_bridge + 受控假库）
# --------------------------------------------------------------------------- #
class ABridgeWaiver(QuoteStoreCase):
    """`_guard_packaging_result` 必须在**任何写之前**认留痕；认不出的一律照旧拒绝。"""

    def call(self, result, **kwargs):
        try:
            out = self.wb.handoff(kind="packaging_cost_to_quote", result=result, **kwargs)
        except Exception as exc:                                # noqa: BLE001
            return None, exc
        return out, None

    def handoff_tasks(self):
        import cpq_wf
        return [dict(row) for row in self.wb.rows("cpq_wf_task",
                                                 task_kind=cpq_wf.TASK_KIND_HANDOFF)]

    def test_a1_valid_waiver_lets_the_package_land(self):
        bridge = load_bridge()
        package = gap_package(waiver=good_waiver())
        package["source"]["source_session_id"] = self.session
        out, raised = self.call(package)
        self.assertIsNone(raised,
                          "写明原因的放行必须在报价侧被认下来，实际抛了 %r（Spec §3.1）" % (raised,))
        self.assertIsNotNone(out, "放行后必须真的落地")
        self.assertTrue(callable(getattr(bridge, "_guard_packaging_result", None)),
                        "报价侧桥必须有 _guard_packaging_result()（Spec §3.1）")

    def test_a2_waiver_reason_is_readable_at_the_landing(self):
        package = gap_package(waiver=good_waiver())
        package["source"]["source_session_id"] = self.session
        self.call(package)
        tasks = self.handoff_tasks()
        self.assertEqual(len(tasks), 1, "放行后必须建一条报价任务")
        blob = json.dumps(tasks[0].get("payload"), ensure_ascii=False, default=str)
        self.assertIn(WAIVER_REASON, blob,
                      "落点必须能读出放行原因：页面要显示「为什么带缺口也放行了」（Spec §3.1）")

    def test_a3_gaps_without_a_waiver_are_still_refused(self):
        bridge = load_bridge()
        package = gap_package()
        package["source"]["source_session_id"] = self.session
        before = len(self.handoff_tasks())
        out, raised = self.call(package)
        self.assertIsNone(out, "没有留痕的缺口包不许落地（Spec §3.1）")
        self.assertIsInstance(raised, bridge.BridgeError)
        self.assertIn(GAP_CODE, str(raised), "拒绝必须点名缺口：%r" % (raised,))
        self.assertEqual(len(self.handoff_tasks()), before, "被拒绝的回传不许建任务")

    def test_a4_incomplete_waiver_is_still_refused(self):
        bridge = load_bridge()
        broken = {
            "缺原因": good_waiver(reason="   "),
            "缺签字人": good_waiver(by=""),
            "缺时间": good_waiver(at=""),
            "缺缺口码": good_waiver(codes=[]),
            "码不覆盖": good_waiver(codes=["material_price_missing"]),
        }
        for label, waiver in broken.items():
            package = gap_package(waiver=waiver)
            package["source"]["source_session_id"] = self.session
            before = len(self.handoff_tasks())
            out, raised = self.call(package)
            self.assertIsNone(out, "%s：不完整的留痕不许放行" % label)
            self.assertIsInstance(raised, bridge.BridgeError,
                                  "%s：必须是 BridgeError，实际 %r" % (label, raised))
            self.assertIn(GAP_CODE, str(raised),
                          "%s：拒绝必须点名缺口，实际 %r" % (label, raised))
            self.assertEqual(len(self.handoff_tasks()), before,
                             "%s：被拒绝的回传不许建任务" % label)

    def test_a5_waiver_does_not_open_the_non_packaging_gate(self):
        bridge = load_bridge()
        package = gap_package(waiver=good_waiver(), industry="battery")
        package["source"]["source_session_id"] = self.session
        out, raised = self.call(package)
        self.assertIsNone(out, "留痕不能把非包装结果塞进包装口径（Spec §4）")
        self.assertIsInstance(raised, bridge.BridgeError)


# --------------------------------------------------------------------------- #
# B. 交接 → 桥这条缝：技术侧算出来的留痕必须真的传过去
# --------------------------------------------------------------------------- #
class BHandoffBridgeSeam(HandoffCase):
    """冻结红测 C5 把 `cpq_bridge.send_to_quote` 换掉了，所以这条缝以前没有任何约束。"""

    def spy(self, seen):
        import cpq_tech_bridge

        def fake(*args, **kwargs):
            result = kwargs.get("result")
            if result is None:
                result = next((a for a in args
                               if isinstance(a, dict) and "packaging_package" in a), None)
            seen["result"] = result
            cpq_tech_bridge._guard_packaging_result(result)     # 跑**真**守卫
            return dict(self.bridge_ok)

        return fake

    def setUp(self):
        super().setUp()
        from tests.test_packaging_quote_close_loop_red import BRIDGE_OK
        self.bridge_ok = dict(BRIDGE_OK)

    def test_b1_gaps_plus_reason_passes_the_real_guard(self):
        from tech_app.backend.services import cpq_bridge
        gaps = [{"code": GAP_CODE, "where": "BOX-MAIN/print", "detail": "手填列"}]
        self.prepare(has_gaps=True)
        self.save_cost(has_gaps=True, gaps=gaps)          # 缺口要**逐条可列举**才验得了覆盖
        module = self.handoff_mod()
        seen = {}
        with mock.patch.object(cpq_bridge, "send_to_quote", self.spy(seen)):
            module.send_to_quote(PID, "REQ-PKG-Q0001", user=FINANCE, token="tkn",
                                 allow_gaps=True, reason=WAIVER_REASON)
        result = seen.get("result") or {}
        self.assertIn("gap_waiver", result,
                      "技术侧必须在交给桥的正文里带上 gap_waiver（Spec §2.1）")
        waiver = result.get("gap_waiver") or {}
        self.assertEqual(waiver.get("reason"), WAIVER_REASON)
        self.assertEqual(waiver.get("by"), "fin1")
        self.assertIn(GAP_CODE, list(waiver.get("codes") or []),
                      "留痕必须点名这次放行覆盖的缺口码（Spec §3.1）")

    def test_b2_no_waiver_when_there_are_no_gaps(self):
        from tech_app.backend.services import cpq_bridge
        self.prepare(has_gaps=False)
        module = self.handoff_mod()
        seen = {}
        with mock.patch.object(cpq_bridge, "send_to_quote", self.spy(seen)):
            module.send_to_quote(PID, "REQ-PKG-Q0001", user=FINANCE, token="tkn")
        result = seen.get("result") or {}
        self.assertNotIn("gap_waiver", result,
                         "没有缺口的包不许造留痕：与今天的正文逐字一致（Spec §2.1）")


# --------------------------------------------------------------------------- #
# C. 需求退回草稿：approved / pending_review 必须有合法路径
# --------------------------------------------------------------------------- #
class CRequirementReturnToDraft(unittest.TestCase):
    maxDiff = None

    def sandbox(self, status):
        service = load_requirement_service()
        req = {"project_id": "proj-blockers", "requirement_no": "REQ-B1", "status": status,
               "title": "包装需求", "data": {}, "created_by": "tester",
               "history": [], "waivers": []}
        state = {"saved": 0, "audit": []}

        def load(pid):
            return copy.deepcopy(req) if pid == req["project_id"] else None

        def save(pid, doc, author="system"):
            state["saved"] += 1
            req.clear()
            req.update(copy.deepcopy(doc))

        for attr, fn in (("load_requirement", load), ("save_requirement", save)):
            patch = mock.patch.object(service.store, attr, fn)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(service.store, "audit",
                                  lambda *a, **k: state["audit"].append((a, k)))
        patch.start()
        self.addCleanup(patch.stop)
        return service, req, state

    def return_to_draft(self, status):
        service, req, state = self.sandbox(status)
        out = service.return_requirement_to_draft("proj-blockers", {"username": "sm1"}, "补料")
        return out, req, state

    def test_c1_approved_can_be_returned_to_draft(self):
        out, req, state = self.return_to_draft("approved")
        self.assertEqual(req.get("status"), "draft",
                         "已批准的需求必须有一条合法的退回路径（Spec §3.2）")
        self.assertEqual((out or {}).get("status"), "draft")
        events = [str((item or {}).get("action") if isinstance(item, dict) else item)
                  for item in (req.get("history") or [])]
        self.assertIn("confirmation_returned", events, "退回必须留一条历史事件")
        self.assertTrue(state["audit"], "退回必须写审计")

    def test_c2_pending_review_can_be_returned_to_draft(self):
        out, req, _state = self.return_to_draft("pending_review")
        self.assertEqual(req.get("status"), "draft",
                         "待审核的需求也必须能被退回补充（Spec §3.2）")

    def test_c3_pending_confirmation_keeps_working(self):
        out, req, _state = self.return_to_draft("pending_confirmation")
        self.assertEqual(req.get("status"), "draft", "既有行为不变")

    def test_c4_draft_is_idempotent(self):
        out, req, _state = self.return_to_draft("draft")
        self.assertEqual(req.get("status"), "draft", "草稿退回是幂等的，不许抛")

    def test_c5_editable_statuses_must_not_be_widened(self):
        service = load_requirement_service()
        self.assertEqual(tuple(service.EDITABLE_STATUSES), ("draft", "rejected"),
                         "不许把 approved 塞进可编辑闭集来「修好」退回路径（Spec §4）")
        self.assertNotIn("approved", tuple(service.EDITABLE_STATUSES))


# --------------------------------------------------------------------------- #
# D. 人工来源但值为空 → 图纸证据必须补上
# --------------------------------------------------------------------------- #
class DUserConfirmedEmpty(SemanticsCase):
    """`_is_user_confirmed` 只看来源不看值，导致"确认过"的字段永远空着。"""

    def semantics(self, value, status="confirmed", origin="confirmed_from_cad"):
        return {"semantics_id": "probe-1", "fields": {"closure_type": {
            "origin": origin, "status": status, "value": value, "confidence": 0.9,
            "evidence_level": "STRONG", "evidence_refs": [], "conflicts": [],
            "alternatives": []}}}

    def data_of(self, result, req):
        return (result or {}).get("data") or req.get("data") or {}

    def test_d1_empty_manual_value_is_filled(self):
        seed = {"closure_type": "", "field_sources": {"closure_type": "manual"}}
        service, req, _state, project_id = self.memory_requirement(seed)
        result = self.apply(self.semantics("磁吸"), service, project_id)
        data = self.data_of(result, req)
        self.assertEqual(data.get("closure_type"), "磁吸",
                         "人工来源但值为空时，图纸证据必须把值补上（Spec §3.3）")
        self.assertEqual((data.get("field_sources") or {}).get("closure_type"), "manual",
                         "补齐值不许把来源降级成图纸（Spec §3.3）")
        entry = (data.get("field_provenance") or {}).get("closure_type") or {}
        self.assertEqual(entry.get("origin"), "user_confirmed")

    def test_d2_missing_key_with_manual_source_is_filled(self):
        seed = {"field_sources": {"closure_type": "manual"}}
        service, req, _state, project_id = self.memory_requirement(seed)
        result = self.apply(self.semantics("磁吸"), service, project_id)
        data = self.data_of(result, req)
        self.assertEqual(data.get("closure_type"), "磁吸",
                         "键不存在也算「没有用户确认的值」（Spec §3.3）")

    def test_d3_non_empty_manual_value_is_never_overwritten(self):
        seed = {"closure_type": "天地盖", "field_sources": {"closure_type": "manual"}}
        service, req, _state, project_id = self.memory_requirement(seed)
        result = self.apply(self.semantics("磁吸"), service, project_id)
        data = self.data_of(result, req)
        self.assertEqual(data.get("closure_type"), "天地盖",
                         "人工已确认的非空值一个字都不许改（Spec §3.3）")
        entry = (data.get("field_provenance") or {}).get("closure_type") or {}
        self.assertTrue(entry.get("alternatives"),
                        "新证据要进 alternatives（冻结红测 D7 的口径）")
        self.assertIn("PACKAGING_FIELD_USER_CONFIRMED", self.warning_codes(result))

    def test_d4_unconfirmed_evidence_never_clears_a_value(self):
        seed = {"closure_type": "天地盖", "field_sources": {"closure_type": "manual"}}
        service, req, _state, project_id = self.memory_requirement(seed)
        result = self.apply(self.semantics(None, status="missing", origin="missing"),
                            service, project_id)
        data = self.data_of(result, req)
        self.assertEqual(data.get("closure_type"), "天地盖",
                         "读不到的字段不许把已有值清空（Spec §3.3）")


# --------------------------------------------------------------------------- #
# E. BOM 回填必须披露配对与材料是否一致（加法，不改成拒绝）
# --------------------------------------------------------------------------- #
class EBindingDisclosure(PartsCase):
    """位置配对可以先用，但**不许静默**：逐行留痕 + 单列不一致项。"""

    def rows(self, box_code="YT-RB-01001-A"):
        from tech_app.backend.services import packaging_bom
        inputs = dict(REQ)
        expanded = packaging_bom.expand_parts(box_code, inputs)
        return packaging_bom._assemble(expanded, packaging_bom._load_box_type(box_code),
                                       inputs, "REQ-B1")

    @staticmethod
    def with_materials(doc, material):
        out = copy.deepcopy(doc)
        for row in out.get("parts") or []:
            row["material"] = material
        return out

    def test_e1_every_bound_row_discloses_the_pairing(self):
        module = self.module()
        result = module.bind_rows(self.rows(), self.extract())
        self.assertEqual(result["bound"], 4, "披露不等于拒绝：既有的 4 行照样绑上")
        for row in result["items"]:
            binding = (row.get("size_source") or {}).get("dwg_binding") \
                if isinstance(row.get("size_source"), dict) else None
            if not row.get("length_mm") or not binding:
                continue
            self.assertTrue(str(binding.get("pairing_basis") or "").strip(),
                            "%s 必须留一句配对依据（Spec §3.4）：%r"
                            % (row.get("item_key"), binding))
            self.assertIn(binding.get("material_match"), (True, False, None),
                          "%s.material_match 只能是 True/False/None（未知不许猜）"
                          % row.get("item_key"))

    def test_e2_known_mismatch_is_disclosed_not_hidden(self):
        module = self.module()
        items = self.rows()
        for row in items:
            if row.get("item_key") == "RB01001-P08":
                row["material"] = "钕铁硼磁铁 15×3mm"
        parts = self.with_materials(self.extract(), "2mm 灰板")
        result = module.bind_rows(items, parts)
        review = list(result.get("pairing_review") or [])
        hit = [item for item in review if item.get("item_key") == "RB01001-P08"]
        self.assertTrue(hit, "磁铁行配到纸面板上必须单列出来（Spec §3.4）：%r" % (review,))
        self.assertEqual(hit[0].get("material_match"), False)
        self.assertTrue(str(hit[0].get("part_material") or "").strip(),
                        "披露必须带上零件侧的材料原文")
        bound = [row for row in result["items"] if row.get("item_key") == "RB01001-P08"][0]
        self.assertEqual(bound.get("status"), "computed",
                         "本批披露不改成拒绝：行仍然要绑上（Spec §3.4）")
        self.assertEqual(result["bound"], 4)
        self.assertEqual(result["unbound"], [])

    def test_e3_unknown_material_is_not_a_mismatch(self):
        module = self.module()
        items = self.rows()
        for row in items:
            if row.get("item_key") == "RB01001-P08":
                row["material"] = "钕铁硼磁铁 15×3mm"
        parts = self.with_materials(self.extract(), None)
        result = module.bind_rows(items, parts)
        self.assertEqual(list(result.get("pairing_review") or []), [],
                         "零件侧材料未知时不许当成不匹配（未知≠不同类，Spec §3.4）")

    def test_e4_rows_without_parts_are_untouched(self):
        module = self.module()
        tiny = {"parts": [], "stats": {}, "unavailable": [{"code": "all_filtered"}]}
        result = module.bind_rows(self.rows(), tiny)
        self.assertEqual(result["bound"], 0)
        self.assertEqual(list(result.get("pairing_review") or []), [])
        self.assertEqual(len(result["unbound"]), 4)


if __name__ == "__main__":
    unittest.main()
