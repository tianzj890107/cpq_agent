"""红测：包装回传报价的落点冲突必须可恢复（409 + 结构化 detail / 恢复字段真的收得到）。

Spec：`docs/specs/packaging-quote-send-recovery.md`
依赖：`packaging-quote-close-loop.md`（第 8 批）、`quote-first-project-entry.md`（报告侧同类 P0）。

现状缺口（34 线上实测，不是推断）：

  · 项目 `f1417060ae9d`（成本已算 6.412359 元/件）
    `POST /requirement/packaging-quote/send`（PE1，allow_gaps=True + reason）
    → **500**「没有找到这条业务实例对应的报价卡片。确认要新建报价卡片时，请填写新建原因后重试。」
  · 先做一次合法恢复 `POST /quote-link/recover {"create_new": true, "create_reason": "..."}` → 200
    （meta 里写下 create_new=true），再回传 → **500，逐字同一句**；
  · `PackagingQuoteSendAction`（main.py:146）没有 business_case_id / create_new / create_reason，
    而同类 `ReportQuoteAction` 有（main.py:165-167）；`cpq_bridge.send_to_quote()` 早就支持这两个参数；
  · `_packaging_handoff_flow`（main.py:6979）只捕 `HandoffError`，`BridgeRejected` 冒到全局
    `RuntimeError` 处理器 → 500；报告侧的 `_report_flow` 捕了它并走 `_bridge_http_error()`（409 + detail）。

不许放宽：`allow_gaps=False` 有缺口仍 409；放行必须写原因；多候选不许自动挑一张。

纪律：全部离线（不连 PG、不调模型、不起服务）；服务层用打桩依赖真跑一次 `send_to_quote`，
捕获交给桥的入参，不写任何业务数据。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HANDOFF = importlib.import_module("tech_app.backend.services.packaging_handoff")
BRIDGE = importlib.import_module("tech_app.backend.services.cpq_bridge")
CASE_LINK = importlib.import_module("cpq_case_link")

MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
HANDOFF_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_handoff.py"

PROJECT_ID = "f1417060ae9d"
RECOVERY_FIELDS = ("business_case_id", "create_new", "create_reason")
CREATE_REASON = "全流程演示：明确新建一张报价卡片承接包装报价回传"


def classes_of(path):
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def model_fields(node):
    fields = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields.append(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    fields.append(target.id)
    return fields


def assigned_value(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == name:
            return node.value
    return None


def function_source(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    return ""


PACKAGE = {"engine_version": "packaging_handoff_v1", "handoff_version": "pkg-quote-handoff-v1",
           "handoff_kind": "packaging_cost_to_quote", "industry": "packaging",
           "source": {"project_id": PROJECT_ID, "requirement_no": "REQ-1",
                      "scenario_code": "default", "result_version": "pkgcost-v1:5000.0:6.412359",
                      "source_task_id": "", "source_session_id": "", "business_case_id": ""},
           "cost": {"built": True, "total_cost": 6.412359, "gaps": []},
           "requirement": {}, "gaps": []}


class SendRecoveryCase(unittest.TestCase):
    maxDiff = None

    def send(self, meta=None, **kwargs):
        """真跑一次 `send_to_quote`，依赖全部打桩，返回交给桥的入参。

        `meta` 是**项目 meta 里已有的恢复留痕**（`/quote-link/recover` 写过的那次"明确新建"）：
        `## 467` 起由这个参数交给夹具。以前 C1/C2 用 `with mock.patch...` 在**外面**替换
        `load_business_case`，而本函数在**里面**又把它打桩成 `{}` —— 后启动的打桩赢，
        于是 C1 断言的那份 meta 从来没进过被测代码（Spec §2.5 记的"夹具自遮挡"）。
        """
        captured = {}

        def fake_bridge(*args, **kw):
            captured.update(kw)
            captured["_args"] = args
            return {"quote_session_id": "sess-demo", "business_case_id": "bc_demo",
                    "handoff": {"task_id": 1}}

        patches = [
            mock.patch.object(HANDOFF, "handoff_package",
                              lambda pid, req="", scenario=None: dict(PACKAGE)),
            mock.patch.object(HANDOFF, "_guard_gaps", lambda package, **kw: None),
            mock.patch.object(HANDOFF, "package_fingerprint", lambda package: "fp-demo"),
            mock.patch.object(HANDOFF.da_repo, "packaging_handoffs", lambda *a, **k: []),
            mock.patch.object(HANDOFF.da_repo, "save_packaging_handoff", lambda row: None),
            mock.patch.object(HANDOFF.da_db, "now", lambda: "2026-09-22T00:30:00+08:00"),
            mock.patch.object(HANDOFF.store, "load_business_case",
                              lambda pid: dict(meta or {})),
            mock.patch.object(HANDOFF.store, "audit", lambda *a, **k: None),
            mock.patch.object(BRIDGE, "send_to_quote", fake_bridge),
            mock.patch.object(HANDOFF.cpq_bridge, "send_to_quote", fake_bridge),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        try:
            HANDOFF.send_to_quote(PROJECT_ID, "REQ-1", allow_gaps=True, reason="演示放行",
                                  user={"username": "PE1", "role": "process_manager",
                                        "cpq_role_code": "process_mgr"},
                                  **kwargs)
        except TypeError as exc:
            self.fail("packaging_handoff.send_to_quote 还没有接收恢复字段（Spec §2.1）：%s" % exc)
        return captured


# --------------------------------------------------------------------------- #
# A 组：请求模型（Spec §2.1）
# --------------------------------------------------------------------------- #
class ARequestModel(SendRecoveryCase):

    def test_a1_packaging_model_carries_the_recovery_fields(self):
        classes = classes_of(MAIN_PY)
        node = classes.get("PackagingQuoteSendAction")
        self.assertIsNotNone(node, "main.py 必须有 PackagingQuoteSendAction")
        fields = model_fields(node)
        for name in RECOVERY_FIELDS:
            self.assertIn(name, fields,
                          "包装回传请求模型必须收得到 %s（Spec §2.1）；"
                          "现在文案让人填新建原因，接口里却没有这个字段" % name)

    def test_a2_field_names_match_the_report_side(self):
        classes = classes_of(MAIN_PY)
        report = set(model_fields(classes.get("ReportQuoteAction")))
        packaging = set(model_fields(classes.get("PackagingQuoteSendAction")))
        self.assertTrue(set(RECOVERY_FIELDS) <= report, "报告侧（已修）必须有这三个字段")
        self.assertTrue(set(RECOVERY_FIELDS) <= packaging,
                        "包装侧字段名必须与报告侧逐字一致：缺 %s"
                        % sorted(set(RECOVERY_FIELDS) - packaging))

    def test_a3_service_signature_accepts_them(self):
        fn = HANDOFF.send_to_quote
        params = fn.__code__.co_varnames[: fn.__code__.co_argcount + fn.__code__.co_kwonlyargcount]
        for name in ("create_new", "create_reason"):
            self.assertIn(name, params,
                          "packaging_handoff.send_to_quote 必须收得到 %s（Spec §2.1）" % name)


# --------------------------------------------------------------------------- #
# B 组：真的透传到桥（Spec §2.1）
# --------------------------------------------------------------------------- #
class BTransparency(SendRecoveryCase):

    def test_b1_create_new_reaches_the_bridge(self):
        captured = self.send(create_new=True, create_reason=CREATE_REASON)
        self.assertTrue(captured.get("create_new"), "create_new 必须原样交给桥（Spec §2.1）")
        self.assertEqual(captured.get("create_reason"), CREATE_REASON)

    def test_b2_default_keeps_the_old_behaviour(self):
        captured = self.send()
        self.assertFalse(captured.get("create_new"),
                         "默认不许变成「总是新建」（Spec §2.1/§2.4）")
        self.assertEqual(captured.get("create_reason"), "")

    def test_b3_business_case_id_reaches_the_bridge(self):
        captured = self.send(business_case_id="bc_83e3d41fd939")
        self.assertEqual(captured.get("business_case_id"), "bc_83e3d41fd939")


# --------------------------------------------------------------------------- #
# C 组：复用 meta 里已有的恢复留痕（Spec §2.2）
# --------------------------------------------------------------------------- #
class CMetaRecovery(SendRecoveryCase):

    def test_c1_meta_create_new_is_reused_without_asking_again(self):
        captured = self.send(meta={"create_new": True, "create_reason": CREATE_REASON})
        self.assertTrue(captured.get("create_new"),
                        "项目 meta 里已有「明确新建」留痕时必须复用，不要求用户再填一次（Spec §2.2）")
        self.assertEqual(captured.get("create_reason"), CREATE_REASON)

    def test_c2_explicit_request_wins_over_meta(self):
        captured = self.send(meta={"create_new": True, "create_reason": "旧原因"},
                             create_reason="新原因")
        self.assertEqual(captured.get("create_reason"), "新原因",
                         "本次请求里写明的原因优先（Spec §2.2）")


# --------------------------------------------------------------------------- #
# D 组：错误口径（Spec §2.3）
# --------------------------------------------------------------------------- #
class DBridgeErrorSurface(SendRecoveryCase):

    def test_d1_handoff_flow_catches_bridge_rejected(self):
        body = function_source(MAIN_PY, "_packaging_handoff_flow")
        self.assertTrue(body, "main.py 必须有 _packaging_handoff_flow")
        self.assertIn("BridgeRejected", body,
                      "_packaging_handoff_flow 必须捕 BridgeRejected，否则落点冲突冒成 500（Spec §2.3）")
        self.assertIn("_bridge_http_error", body,
                      "必须复用既有的 409 + 结构化 detail 出口，不另写一份（Spec §2.3）")

    def test_d2_conflict_detail_shape_is_reused(self):
        exc = BRIDGE.BridgeRejected("没有找到这张报价卡片", code="no_candidate",
                                   candidates=[{"quote_session_id": "s1"}], status=409)
        self.assertTrue(BRIDGE.is_conflict(exc))
        detail = BRIDGE.conflict_detail(exc)
        self.assertEqual(detail.get("code"), "no_candidate")
        self.assertEqual(len(detail.get("candidates") or []), 1)

    def test_d3_packaging_send_route_still_guards_gaps(self):
        source = HANDOFF_PY.read_text(encoding="utf-8")
        self.assertIn("cost_gaps_unresolved", source, "缺口未清这条拒绝不许被本批放宽（Spec §2.4）")
        self.assertIn("gap_reason_required", source, "放行必须写明原因（Spec §2.4）")


# --------------------------------------------------------------------------- #
# E 组：落点规则护栏（Spec §2.4）
# --------------------------------------------------------------------------- #
class ELandingRules(SendRecoveryCase):

    def test_e1_multiple_candidates_never_auto_pick(self):
        outcome = CASE_LINK.decide([{"quote_session_id": "s1"}, {"quote_session_id": "s2"}],
                                   create_new=True, create_reason=CREATE_REASON)
        self.assertEqual(outcome.get("code"), "multiple_candidates",
                         "多候选时即使带了新建原因也不许自动挑一张（Spec §2.4）")

    def test_e2_no_candidate_with_reason_is_create_new(self):
        outcome = CASE_LINK.decide([], create_new=True, create_reason=CREATE_REASON,
                                   tech_project_id=PROJECT_ID)
        self.assertEqual(outcome.get("code"), "create_new")

    def test_e3_no_candidate_without_reason_still_refuses(self):
        outcome = CASE_LINK.decide([], create_new=True, create_reason="",
                                   tech_project_id=PROJECT_ID)
        self.assertEqual(outcome.get("code"), "no_candidate",
                         "没写原因就不许新建（Spec §2.4）")


if __name__ == "__main__":
    unittest.main()
