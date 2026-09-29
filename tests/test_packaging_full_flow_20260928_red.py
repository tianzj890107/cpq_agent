"""2026-09-28 包装全流程实测回归：先红后绿。"""
from __future__ import annotations

import inspect
import json
import pathlib
import unittest
from unittest import mock

import cpq_suite_server
import cpq_wf
import cpq_quick_quote_price
import cpq_quick_quote_match
import cpq_tech_bridge
import cpq_agent_server
from tech_app.backend.services import packaging_business_part_resolver as part_resolver


class TransportAndSnapshot(unittest.TestCase):
    def test_quote_put_is_dispatched_before_other_routes(self):
        source = inspect.getsource(cpq_suite_server.Handler.do_PUT)
        self.assertIn('self._dispatch_agent("do_PUT")', source)
        self.assertLess(source.index('self._dispatch_agent("do_PUT")'),
                        source.index('self._dispatch_auth()'))

    def test_large_valid_packaging_snapshot_is_not_truncated(self):
        self.assertTrue(hasattr(cpq_wf, "parse_step_snapshot"))
        package = {"quick_quote_price": {"unit_price": 15.18},
                   "packaging_package": {"bom": {"parts": ["x" * 256] * 1100}}}
        raw = json.dumps(package, ensure_ascii=False)
        self.assertGreater(len(raw), 200000)
        self.assertEqual(cpq_wf.parse_step_snapshot(raw), package)

    def test_oversize_snapshot_is_explicit_error(self):
        self.assertTrue(hasattr(cpq_wf, "parse_step_snapshot"))
        limit = cpq_wf.MAX_STEP_SNAPSHOT_BYTES
        with self.assertRaises(cpq_wf.WfError):
            cpq_wf.parse_step_snapshot(json.dumps({"payload": "x" * limit}))

    def test_merge_path_also_rejects_oversize_snapshot(self):
        self.assertTrue(hasattr(cpq_wf, "serialize_step_snapshot"))
        with self.assertRaises(cpq_wf.WfError):
            cpq_wf.serialize_step_snapshot({"payload": "x" * cpq_wf.MAX_STEP_SNAPSHOT_BYTES})
        source = inspect.getsource(cpq_wf.merge_step_snapshot)
        self.assertIn("serialize_step_snapshot(merged)", source)

    def test_complete_step_does_not_slice_json(self):
        source = inspect.getsource(cpq_wf.complete_step)
        self.assertNotIn('[:200000]', source)
        self.assertIn('parse_step_snapshot(', source)

    def test_suite_health_identifies_runtime_build(self):
        self.assertTrue(hasattr(cpq_suite_server, "runtime_identity"))
        identity = cpq_suite_server.runtime_identity()
        self.assertTrue(identity.get("started_at"))
        self.assertTrue(identity.get("source_digest"))

    def test_bridge_verifies_snapshot_before_task_dispatch(self):
        source = inspect.getsource(cpq_tech_bridge.send_to_quote)
        self.assertIn("verify_handoff_snapshot(", source)
        self.assertLess(source.index("verify_handoff_snapshot("), source.index("cpq_wf.send_task("))

    def test_bridge_refuses_missing_snapshot_keys(self):
        self.assertTrue(hasattr(cpq_tech_bridge, "verify_handoff_snapshot"))
        with mock.patch.object(cpq_wf, "step_snapshot", return_value={"quick_quote_price": {}}):
            with self.assertRaises(cpq_tech_bridge.BridgeError):
                cpq_tech_bridge.verify_handoff_snapshot(
                    "test-session", {"packaging_package": {"industry": "packaging"}}, conn=object())

    def test_bridge_refuses_stale_value_under_same_snapshot_key(self):
        with mock.patch.object(cpq_wf, "step_snapshot", return_value={
                "packaging_package": {"bom": {"parts": []}}}):
            with self.assertRaises(cpq_tech_bridge.BridgeError):
                cpq_tech_bridge.verify_handoff_snapshot(
                    "test-session", {"packaging_package": {"bom": {"parts": ["P-001"]}}},
                    conn=object())


class PackagingCandidateAndMoney(unittest.TestCase):
    def test_reviewed_case_without_quantity_tiers_uses_standard_price_as_baseline(self):
        """准入既然允许缺档位，选基准不能在下一步突然拒绝。"""
        from tests.test_quick_quote_case_retrieval_red import case_row, inputs, SALES, DEFAULT_WEIGHTS, TODAY
        case = case_row("QQ-NO-TIERS", quantity_tiers=[], standard_price=12.5)
        baseline = cpq_quick_quote_match.build_baseline(
            inputs(quantity=1000), "QQ-NO-TIERS", cases=[case], user=SALES,
            weights=DEFAULT_WEIGHTS, today=TODAY)
        self.assertEqual(baseline["base_unit_price"], 12.5)
        self.assertIsNone(baseline["base_tier_qty"])
        self.assertFalse(baseline["base_tier_matched"])

    def test_drawing_annotations_never_become_part_names(self):
        for label in ("纸张纹路", "8PCS/箱", "30659003-10PC 装柜图纸",
                      "1、内径", "402X50.5MM高/厚度2MM"):
            with self.subTest(label=label):
                self.assertTrue(part_resolver._exclusion_reason(label, label), label)

    def test_real_part_names_are_not_filtered_by_annotation_guard(self):
        for label in ("左盖面纸", "地盒底板", "内托支撑围条", "盖内圈纸管"):
            with self.subTest(label=label):
                self.assertFalse(part_resolver._exclusion_reason(label, label), label)

    def test_money_rounding_is_a_single_documented_boundary(self):
        self.assertTrue(hasattr(cpq_quick_quote_price, "_money"))
        self.assertEqual(cpq_quick_quote_price._money(39.8 - 1.99), 37.81)

    def test_quote_panel_marks_incomplete_cost(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "tech_app/frontend/packaging-quote-panel.js").read_text()
        self.assertIn("非完整成本", source)
        self.assertIn("publish_blocked", source)
        styles = (pathlib.Path(__file__).resolve().parents[1] / "报价首页.html").read_text()
        self.assertIn(".pkg-quote-warning", styles)
        self.assertIn("var(--color-primary-light)", styles)

    def test_requirement_form_tracks_actual_manual_edits(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "tech_app/frontend/requirement-create.js").read_text()
        self.assertIn("rcManuallyEditedFields", source)
        self.assertIn("out.field_sources", source)
        self.assertIn("'manual'", source)


class QuickQuoteRestartPersistence(unittest.TestCase):
    def test_state_changes_survive_repository_reload(self):
        from tech_app.backend.storage import meta_backend

        class FakeBackend:
            def __init__(self):
                self.docs = {}

            def get_doc(self, project_id, key):
                return json.loads(json.dumps(self.docs.get((project_id, key)))) if (
                    project_id, key) in self.docs else None

            def put_doc(self, project_id, key, value):
                self.docs[(project_id, key)] = json.loads(json.dumps(value))

        backend = FakeBackend()
        with mock.patch.object(meta_backend, "get_backend", return_value=backend):
            repository = cpq_agent_server._JsonDocRepository("quick_quote_sessions", "test")
            with mock.patch.object(cpq_agent_server, "QUICK_QUOTE_SESSIONS", repository):
                state = cpq_agent_server._qq_state("restart-proof", create=True)
                state["inputs"] = {"box_type": "YT-DWG-WINE-700ML"}
                cpq_agent_server._qq_touch(
                    state, session_id="restart-proof", industry="packaging",
                    quote_mode="quick", workflow_state="matched")
            reloaded = cpq_agent_server._JsonDocRepository("quick_quote_sessions", "test")
            saved = reloaded["restart-proof"]
        self.assertEqual(saved["inputs"]["box_type"], "YT-DWG-WINE-700ML")
        self.assertEqual(saved["quote_mode"], "quick")
        self.assertEqual(saved["workflow_state"], "matched")


if __name__ == "__main__":
    unittest.main()
