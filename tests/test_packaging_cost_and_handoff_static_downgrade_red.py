"""红测：成本口径 / 规则版本 / 上游路线 / 交接闸门的「静默降级」必须留痕。

Spec：`docs/specs/packaging-cost-and-handoff-static-downgrade-disclosure.md`
血缘：`docs/specs/packaging-silent-degradation-disclosure.md`（同一病症的零件 / 人工映射 /
角色候选 / 配对复核四处；本批是成本与交接侧的五处）、`packaging-cost-content-binding-source-disclosure.md`、
`packaging-cost-input-version-pinning.md`。

现状缺口（实测，不是推断；2026-09-22 冷进程复现）：
  · `packaging_cost._load_minimum_charge_policy()`：快照读不到 → `block = {}` → `status` 退成
    `pending`，与「业务还没裁决」同形；
  · `packaging_cost.rule_snapshot_version()`：`kb_repo.kb_version()` 读挂 → `""`，而
    `kb_version()` 本身在"还没拉过快照"时也返回 `None` → `""` —— 三态压成一态，
    而这个值会写进每一个成本明细行与成本估算行当审计凭据；
  · `packaging_cost._upstream_route_version()`：路线模块读挂 → `""`，与「一条路线都没有」同形；
  · `packaging_handoff._publish_gate()`：`gates()` 抛一次异常 → `inheritance()` 根本不会被调用，
    `source_versions` 一起变 `{}`，`publishable` 静默变 `False`；
  · 同函数：`minimum_charge_policy()` 抛异常 → 给 `{}`（与正常值形状都不同），随 `package_json`
    落库并回传报价侧。

纪律：
  · 只跑离线单测：临时文件 + mock，不建项目、不算成本、不连 PG、不发 HTTP、不写业务数据；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import inspect
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import (packaging_cost,  # noqa: E402
                                       packaging_drawing_flow, packaging_handoff,
                                       packaging_route)

POLICY_KEYS = ("status", "chosen", "policy", "fallback", "decided_by", "decided_at")
POLICY_SOURCES = ("snapshot", "unavailable")
VERSION_SOURCES = ("kb", "none", "unavailable")
ROUTE_SOURCES = ("route", "none", "unavailable")


def detail_fn(module, name):
    return getattr(module, name, None)


# --------------------------------------------------------------------------- #
# A. 最低收费口径快照
# --------------------------------------------------------------------------- #
class AMinimumChargePolicySnapshot(unittest.TestCase):
    def test_a1_unreadable_snapshot_must_report_unavailable(self):
        missing = pathlib.Path("/nonexistent/cpq-minimum-charge-rules.json")
        with mock.patch.object(packaging_cost, "RULES_JSON_PATH", missing):
            block = packaging_cost._load_minimum_charge_policy()
        self.assertIn("source", block,
                      "快照读不到时必须给 source（Spec §2.1）：读不到与「未裁决」不许同形")
        self.assertIn(block.get("source"), POLICY_SOURCES,
                      "source 必须是闭集 %s，实测 %r" % (list(POLICY_SOURCES), block.get("source")))
        self.assertEqual("unavailable", block.get("source"),
                         "文件不存在时必须报 unavailable（Spec §2.1）")
        self.assertEqual("pending", block.get("status"),
                         "结论口径不变：读不到仍按未裁决处理（Spec §2.1）")
        self.assertEqual("unresolved", block.get("policy"),
                         "结论口径不变：读不到仍标注 unresolved（Spec §2.1）")
        self.assertIn("unavailable_reason", block, "必须留痕（Spec §2.1）")
        self.assertIn("FileNotFoundError", str(block.get("unavailable_reason")),
                      "unavailable_reason 必须是异常类名（Spec §2.1），实测 %r"
                      % (block.get("unavailable_reason"),))

    def test_a2_public_policy_carries_the_source(self):
        missing = pathlib.Path("/nonexistent/cpq-minimum-charge-rules.json")
        with mock.patch.object(packaging_cost, "RULES_JSON_PATH", missing):
            block = packaging_cost._load_minimum_charge_policy()
        original = packaging_cost.MINIMUM_CHARGE_POLICY
        try:
            packaging_cost.MINIMUM_CHARGE_POLICY = block
            info = packaging_cost.minimum_charge_policy()
            self.assertIn("source", info,
                          "minimum_charge_policy() 必须原样带出 source（Spec §2.1）")
            self.assertEqual("unavailable", info.get("source"),
                             "快照读不到时对外必须是 unavailable（Spec §2.1）")
            self.assertEqual("unresolved", info.get("policy"),
                             "结论口径不变：仍是 unresolved（Spec §2.1）")
            self.assertIn("unavailable_reason", info,
                          "minimum_charge_policy() 必须原样带出 unavailable_reason（Spec §2.1）")
        finally:
            packaging_cost.MINIMUM_CHARGE_POLICY = original

    def test_a3_existing_six_keys_unchanged(self):
        """护栏：正常快照下六键口径逐字不变（Spec §2.1 的既有键部分）。"""
        block = packaging_cost.MINIMUM_CHARGE_POLICY
        info = packaging_cost.minimum_charge_policy()
        for key in POLICY_KEYS:
            self.assertIn(key, info, "minimum_charge_policy() 必须仍有 %s" % key)
        self.assertIn(block.get("status"), {"pending", "chosen"},
                      "快照 status 只能是 pending / chosen")
        if block.get("status") == "chosen" and block.get("chosen"):
            self.assertEqual(block.get("chosen"), info["policy"])
            self.assertEqual("", info["fallback"], "已裁决不许再标回退口径")
        else:
            self.assertEqual("unresolved", info["policy"])
            self.assertEqual("sheet_labor_rate", info["fallback"])


# --------------------------------------------------------------------------- #
# B. 规则快照版本
# --------------------------------------------------------------------------- #
class BRuleSnapshotVersion(unittest.TestCase):
    def detail(self):
        fn = detail_fn(packaging_cost, "rule_snapshot_version_detail")
        self.assertTrue(callable(fn),
                        "packaging_cost 必须提供 rule_snapshot_version_detail()（Spec §2.2）")
        return fn

    def test_b1_kb_read_failure_is_unavailable(self):
        with mock.patch.object(packaging_cost.kb_repo, "kb_version",
                               side_effect=RuntimeError("kb down")):
            info = self.detail()()
        self.assertIn(info.get("source"), VERSION_SOURCES, "source 必须是闭集（Spec §2.2）")
        self.assertEqual("unavailable", info.get("source"),
                         "读挂必须报 unavailable（Spec §2.2）")
        self.assertEqual("", info.get("version"), "读不到版本必须是空串（Spec §2.2）")
        self.assertIn("RuntimeError", str(info.get("reason")),
                      "reason 必须是异常类名（Spec §2.2）")

    def test_b2_not_pulled_is_none_not_unavailable(self):
        with mock.patch.object(packaging_cost.kb_repo, "kb_version", return_value=None):
            info = self.detail()()
        self.assertEqual("none", info.get("source"),
                         "kb_repo.kb_version() 给 None（还没拉过快照）必须是 none，"
                         "不许与「读挂了」同形（Spec §2.2）")
        self.assertEqual("", info.get("version"))
        self.assertEqual("", info.get("reason"), "none 不是失败，不许编 reason")

    def test_b3_pulled_version_is_kb(self):
        with mock.patch.object(packaging_cost.kb_repo, "kb_version", return_value=7):
            info = self.detail()()
        self.assertEqual("kb", info.get("source"))
        self.assertEqual("7", info.get("version"), "版本必须按 _text 口径带出（Spec §2.2）")
        self.assertEqual("", info.get("reason"))

    def test_b4_legacy_reader_still_returns_str(self):
        """护栏：rule_snapshot_version() 的返回口径逐字不变（Spec §2.2）。"""
        with mock.patch.object(packaging_cost.kb_repo, "kb_version", return_value=None):
            value = packaging_cost.rule_snapshot_version()
        self.assertIsInstance(value, str, "不许改成 None / 抛异常（Spec §2.2）")
        self.assertEqual("", value)
        with mock.patch.object(packaging_cost.kb_repo, "kb_version", return_value=9):
            self.assertEqual("9", packaging_cost.rule_snapshot_version())
        source = inspect.getsource(packaging_cost.rule_snapshot_version)
        self.assertIn("return", source)


# --------------------------------------------------------------------------- #
# C. 上游确认路线版本
# --------------------------------------------------------------------------- #
class CUpstreamRouteVersion(unittest.TestCase):
    def detail(self):
        fn = detail_fn(packaging_cost, "upstream_route_version_detail")
        self.assertTrue(callable(fn),
                        "packaging_cost 必须提供 upstream_route_version_detail()（Spec §2.3）")
        return fn

    def test_c1_route_read_failure_is_unavailable(self):
        with mock.patch.object(packaging_route, "route_versions",
                               side_effect=RuntimeError("route down")):
            info = self.detail()("p1", "REQ-1")
        self.assertIn(info.get("source"), ROUTE_SOURCES, "source 必须是闭集（Spec §2.3）")
        self.assertEqual("unavailable", info.get("source"),
                         "路线读挂必须报 unavailable（Spec §2.3）")
        self.assertEqual("", info.get("version"))
        self.assertIn("RuntimeError", str(info.get("reason")),
                      "reason 必须是异常类名（Spec §2.3）")

    def test_c2_no_route_is_none_and_route_is_route(self):
        with mock.patch.object(packaging_route, "route_versions", return_value=[]):
            empty = self.detail()("p1", "REQ-1")
        self.assertEqual("none", empty.get("source"),
                         "读到了但一条路线都没有必须是 none（Spec §2.3）")
        self.assertEqual("", empty.get("version"))
        with mock.patch.object(packaging_route, "route_versions",
                               return_value=[{"version": "v1"}, {"version": "v2"}]):
            found = self.detail()("p1", "REQ-1")
        self.assertEqual("route", found.get("source"))
        self.assertEqual("v2", found.get("version"),
                         "取最后一条的 version（与 _upstream_route_version 同口径，Spec §2.3）")

    def test_c3_legacy_reader_unchanged(self):
        """护栏：_upstream_route_version() 行为逐字不变（Spec §2.3）。"""
        with mock.patch.object(packaging_route, "route_versions",
                               side_effect=RuntimeError("route down")):
            self.assertEqual("", packaging_cost._upstream_route_version("p1", "REQ-1"))
        with mock.patch.object(packaging_route, "route_versions", return_value=[]):
            self.assertEqual("", packaging_cost._upstream_route_version("p1", "REQ-1"))
        with mock.patch.object(packaging_route, "route_versions",
                               return_value=[{"version": "v3"}]):
            self.assertEqual("v3", packaging_cost._upstream_route_version("p1", "REQ-1"))


# --------------------------------------------------------------------------- #
# D. 交接包 publish gate
# --------------------------------------------------------------------------- #
GATES_OPEN = {"stages": {"quote_draft": {"status": "open", "blocking": []},
                         "quote_publish": {"status": "open", "blocking": []}}}
GATES_BLOCKED = {"stages": {"quote_draft": {"status": "open", "blocking": []},
                            "quote_publish": {"status": "blocked",
                                              "blocking": [{"code": "cost_not_built"}]}}}
INHERITED = {"source_versions": {"route_version": "v9", "engine_version": "packaging_cost_v1"}}


class DPublishGate(unittest.TestCase):
    def gate(self):
        fn = detail_fn(packaging_handoff, "_publish_gate")
        self.assertTrue(callable(fn), "packaging_handoff 必须仍有 _publish_gate()")
        return fn

    def test_d1_gates_failure_must_not_swallow_versions(self):
        with mock.patch.object(packaging_drawing_flow, "gates",
                               side_effect=RuntimeError("flow down")), \
             mock.patch.object(packaging_drawing_flow, "inheritance", return_value=INHERITED):
            out = self.gate()("p1", {}, {})
        self.assertIn("gates_source", out, "闸门读失败必须留痕（Spec §2.4）")
        self.assertEqual("unavailable", out.get("gates_source"),
                         "gates() 抛异常必须报 unavailable（Spec §2.4）")
        self.assertIn("gates_unavailable", out,
                      "gates_unavailable 键必须存在（Spec §2.4）")
        self.assertEqual("packaging_flow_gates_unavailable",
                         (out.get("gates_unavailable") or {}).get("code"))
        self.assertIn("RuntimeError", str((out.get("gates_unavailable") or {}).get("reason")))
        self.assertEqual("v9", (out.get("source_versions") or {}).get("route_version"),
                         "gates() 挂了不许把 inheritance() 的版本六元组一起吞掉（Spec §2.4）")
        self.assertEqual("flow", out.get("source_versions_source"),
                         "这个 case 里 inheritance() 是读到了的（Spec §2.4）")
        self.assertIs(False, out.get("publishable"),
                      "结论口径不变：读不到闸门仍是不可发布（Spec §2.4）")

    def test_d2_versions_failure_is_disclosed(self):
        with mock.patch.object(packaging_drawing_flow, "gates", return_value=GATES_OPEN), \
             mock.patch.object(packaging_drawing_flow, "inheritance",
                               side_effect=RuntimeError("inherit down")):
            out = self.gate()("p1", {}, {})
        self.assertIn("source_versions_source", out, "版本六元组读失败必须留痕（Spec §2.4）")
        self.assertEqual("unavailable", out.get("source_versions_source"))
        self.assertEqual({}, out.get("source_versions"),
                         "读不到就给空（既有口径）—— 但必须有 unavailable 留痕")
        self.assertEqual("packaging_flow_versions_unavailable",
                         (out.get("source_versions_unavailable") or {}).get("code"))
        self.assertIn("RuntimeError",
                      str((out.get("source_versions_unavailable") or {}).get("reason")))
        self.assertEqual("flow", out.get("gates_source"),
                         "闸门读到了就必须报 flow（Spec §2.4）")
        self.assertEqual({"quote_draft": {"status": "open", "blocking": []},
                          "quote_publish": {"status": "open", "blocking": []}},
                         out.get("gates"))

    def test_d3_normal_path_unchanged(self):
        """护栏：读得到时 gates / publishable 口径逐字不变（Spec §2.4）。"""
        with mock.patch.object(packaging_drawing_flow, "gates", return_value=GATES_OPEN), \
             mock.patch.object(packaging_drawing_flow, "inheritance", return_value=INHERITED):
            open_out = self.gate()("p1", {}, {})
        with mock.patch.object(packaging_drawing_flow, "gates", return_value=GATES_BLOCKED), \
             mock.patch.object(packaging_drawing_flow, "inheritance", return_value=INHERITED):
            blocked_out = self.gate()("p1", {}, {})
        self.assertIs(True, open_out.get("publishable"), "quote_publish=open → publishable=True")
        self.assertIs(False, blocked_out.get("publishable"),
                      "quote_publish=blocked → publishable=False")
        self.assertEqual({"quote_draft": {"status": "open", "blocking": []},
                          "quote_publish": {"status": "blocked",
                                            "blocking": [{"code": "cost_not_built"}]}},
                         blocked_out.get("gates"), "两个 stage 与 blocking 逐字带出")
        self.assertEqual(INHERITED["source_versions"], open_out.get("source_versions"))

    def test_d4_policy_failure_must_not_give_empty_dict(self):
        with mock.patch.object(packaging_drawing_flow, "gates", return_value=GATES_OPEN), \
             mock.patch.object(packaging_drawing_flow, "inheritance", return_value=INHERITED), \
             mock.patch.object(packaging_cost, "minimum_charge_policy",
                               side_effect=RuntimeError("policy down")):
            out = self.gate()("p1", {}, {})
        policy = out.get("minimum_charge_policy")
        self.assertIsInstance(policy, dict)
        self.assertNotEqual({}, policy,
                            "口径读不到不许给 {}（与正常值形状都不同，Spec §2.5）")
        self.assertEqual("pending", policy.get("status"),
                         "读不到 ≠ 已裁决：必须是未裁决形状（Spec §2.5）")
        self.assertEqual("unresolved", policy.get("policy"))
        self.assertEqual("sheet_labor_rate", policy.get("fallback"))
        self.assertEqual("unavailable", policy.get("source"),
                         "必须报 source=unavailable（Spec §2.5）")
        self.assertIn("RuntimeError", str(policy.get("unavailable_reason")),
                      "unavailable_reason 必须是异常类名（Spec §2.5）")

    def test_d5_normal_policy_unchanged(self):
        """护栏：读得到时口径六键逐字带出（Spec §2.5）。"""
        with mock.patch.object(packaging_drawing_flow, "gates", return_value=GATES_OPEN), \
             mock.patch.object(packaging_drawing_flow, "inheritance", return_value=INHERITED):
            out = self.gate()("p1", {}, {})
        expected = packaging_cost.minimum_charge_policy()
        got = out.get("minimum_charge_policy") or {}
        for key in POLICY_KEYS:
            self.assertEqual(expected[key], got.get(key),
                             "读得到时必须逐字带出 %s（Spec §2.5）" % key)


if __name__ == "__main__":
    unittest.main()
