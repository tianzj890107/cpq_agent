"""报价完成门禁的成本版本判定：只读预检必须只认落库的第 2 步交接包。

验收口径：`docs/specs/packaging-quote-gate-cost-fingerprint-authority.md` §2。
无网络、无业务库写入：`cpq_wf.step_snapshot` 一律打桩。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cpq_agent_server import _handle_quote_step_gate
from cpq_quote_workspace import cost_fingerprint

SESSION = "522ba91eaaf7"


def stored_package(*, version="pkgcost-v2:1000.0:47.352645:7d35304f"):
    return {"industry": "packaging",
            "source": {"project_id": "p1", "result_version": version},
            "cost": {"total_cost": 47.35264516949938, "material_total": 12.8336818336,
                     "quote_quantity": 1000,
                     "categories": {"die_cutting": 4.141499999999998,
                                    "anti_scratch": 0.0, "auto_mount": 0.0}},
            "gaps": [{"code": "material_price_missing"}],
            "requirement": {"quote_quantity": 1000}}


def browser_round_trip(value):
    """模拟浏览器 JSON.parse → JSON.stringify：整数取值的浮点会变成整数（0.0 → 0）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return {k: browser_round_trip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [browser_round_trip(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def packaging_quote(fingerprint, *, draft=True):
    return {"industry": "packaging", "quote_quantity": 1000,
            "net_unit_price": 63.13686022599918, "cost_total": 47.35264516949938,
            "draft": draft, "publish_blocked": draft, "gap_count": 1 if draft else 0,
            "source_cost_fingerprint": fingerprint}


def gate_body(package, quote, *, session=SESSION, step=3):
    return {"step_no": step, "session_id": session,
            "data": {"packaging_package": package, "packaging_quote": quote}}


class PackagingQuoteGateFingerprintTest(unittest.TestCase):
    def test_browser_round_trip_really_changes_the_fingerprint(self):
        """这条先把差异钉住：同一份包，浏览器往返一次指纹就不一样。"""
        pkg = stored_package()
        self.assertNotEqual(cost_fingerprint(browser_round_trip(pkg)), cost_fingerprint(pkg))

    def test_pre_check_uses_stored_package_so_round_trip_is_not_stale(self):
        """预检必须用落库包算指纹：浏览器往返版不许把正常报价判成「成本版本已变化」。"""
        pkg = stored_package()
        body = gate_body(browser_round_trip(pkg), packaging_quote(cost_fingerprint(pkg)))
        with patch("cpq_wf.step_snapshot", return_value={"packaging_package": pkg}):
            verdict = _handle_quote_step_gate(body)
        self.assertTrue(verdict["ok"], verdict)
        self.assertNotEqual("packaging_quote_stale", verdict.get("code"))

    def test_pre_check_reads_the_second_step_snapshot_for_the_session(self):
        pkg = stored_package()
        body = gate_body(browser_round_trip(pkg), packaging_quote(cost_fingerprint(pkg)))
        with patch("cpq_wf.step_snapshot", return_value={"packaging_package": pkg}) as snap:
            _handle_quote_step_gate(body)
        self.assertEqual([(SESSION, 2)], [tuple(c.args[:2]) for c in snap.call_args_list])

    def test_real_cost_change_is_still_blocked(self):
        """真换了成本（报价是拿更早那份成本算的）仍然要拦。"""
        pkg = stored_package()
        older = stored_package(version="pkgcost-v2:1000.0:46.100000:aaaabbbb")
        body = gate_body(browser_round_trip(pkg), packaging_quote(cost_fingerprint(older)))
        with patch("cpq_wf.step_snapshot", return_value={"packaging_package": pkg}):
            verdict = _handle_quote_step_gate(body)
        self.assertFalse(verdict["ok"])
        self.assertEqual("packaging_quote_stale", verdict["code"])

    def test_pre_check_falls_back_to_the_given_package_when_snapshot_unreadable(self):
        """读不到落库包时按调用方给的包判：不抛异常、不新增阻断。"""
        pkg = stored_package()
        body = gate_body(pkg, packaging_quote(cost_fingerprint(pkg)))
        with patch("cpq_wf.step_snapshot", side_effect=RuntimeError("db down")):
            verdict = _handle_quote_step_gate(body)
        self.assertTrue(verdict["ok"], verdict)

    def test_pre_check_falls_back_when_second_step_has_no_packaging_package(self):
        pkg = stored_package()
        body = gate_body(pkg, packaging_quote(cost_fingerprint(pkg)))
        with patch("cpq_wf.step_snapshot", return_value={"s1_basic": {"数据": {}}}):
            verdict = _handle_quote_step_gate(body)
        self.assertTrue(verdict["ok"], verdict)

    def test_pre_check_without_session_keeps_the_given_package(self):
        pkg = stored_package()
        body = gate_body(pkg, packaging_quote(cost_fingerprint(pkg)), session="")
        with patch("cpq_wf.step_snapshot", return_value={"packaging_package": pkg}) as snap:
            verdict = _handle_quote_step_gate(body)
        self.assertTrue(verdict["ok"], verdict)
        self.assertEqual(0, snap.call_count)

    def test_write_gate_still_reads_the_stored_second_step_package(self):
        """写闸（步骤完成 API）本来就用落库包；本批不允许把它改成读前端负载。"""
        source = (ROOT / "cpq_wf.py").read_text(encoding="utf-8")
        block = source[source.index("def complete_step"):source.index("def complete_step") + 6000]
        self.assertIn("step_snapshot(session_id, 2", block)
        self.assertIn('guard_data["packaging_package"] = package', block)

    def test_gate_module_does_not_grow_a_second_fingerprint_algorithm(self):
        """指纹只有一个算法入口（cost_fingerprint），门禁里不许就地再写一套。"""
        source = (ROOT / "cpq_agent_server.py").read_text(encoding="utf-8")
        block = source[source.index("def _handle_quote_step_gate"):]
        block = block[:block.index("\ndef ", 10)] if "\ndef " in block[10:] else block
        self.assertNotIn("sha256", block)
        self.assertNotIn("json.dumps", block)
