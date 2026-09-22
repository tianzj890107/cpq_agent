"""红测：交接留痕的「可用性」必须从返回值上读得出来（Spec `packaging-handoff-audit-availability.md`）。

现状缺口（代码级，可指到行）：
  · `packaging_handoff.py:327` 的 `_audit_handoff_sent()` 签名是 `-> None`，尾部
    `:351-353` 把 `store.audit(...)` 的异常 `except Exception: pass` 吞掉 —— 审计没落下时
    调用方**看不出来**；
  · `send_to_quote()` 的两条路径（复用 `:451` / 首次 `:507-524`）都把返回值丢掉，
    返回体里既没有 `audit` 键、也没有稳定码 —— 与「审计写成功」逐字相同；
  · `packaging-handoff-audit-trail.md` §5.3 已把这条记为遗留（「留作后续批次的『审计可用性』话题」）。

纪律：只读源码 + 假仓库 / 假业务桥 / 假审计；不连 PG / SQLite 生产库、不发 HTTP、不写文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_handoff as handoff        # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-HANDOFFAUDITAVAIL-001"
RESULT_VERSION = "pkgcost-v1:1000:1234.500000"
ACTION = "workflow:packaging_handoff_sent"
AUDIT_KEYS = ("requirement_no", "scenario_code", "handoff_no", "version_no", "already_sent",
              "cost_result_version", "has_gaps", "quote_session_id", "by")
DISCLOSURE_KEYS = ("attempted", "ok", "action", "code", "message")

PACKAGE = {
    "engine_version": handoff.ENGINE_VERSION,
    "handoff_version": handoff.HANDOFF_VERSION,
    "result_version": RESULT_VERSION,
    "requirement": {}, "params": {},
    "box_type": {"confirmed_box_type": "RB02001", "decision": "confirmed"},
    "bom": {}, "route": {}, "cost": {"has_gaps": False, "total_cost": 1234.5},
    "gaps": [], "formulas": [],
    "source": {"project_id": PID, "requirement_no": REQ_NO, "scenario_code": "default",
               "result_version": RESULT_VERSION, "source_task_id": "",
               "source_session_id": "", "business_case_id": ""},
}

REUSED_ROW = {"handoff_no": "pkghandoff:%s:%s:default:2" % (PID, REQ_NO),
              "project_id": PID, "requirement_no": REQ_NO, "scenario_code": "default",
              "version_no": 2, "package_fingerprint": "fp-1",
              "cost_result_version": RESULT_VERSION,
              "target_task_id": "task-9", "target_quote_session_id": "sess-9",
              "has_gaps": False, "gap_codes": []}


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原（属性本不存在则删除）。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []
        self._missing = object()

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name, self._missing)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, old in reversed(self.saved):
            if old is self._missing:
                try:
                    delattr(owner, name)
                except AttributeError:
                    pass
            else:
                setattr(owner, name, old)
        return False


def _user(role="finance_manager", username="CF1"):
    return {"username": username, "role_code": role, "role_name": "财务经理"}


def _audit_call(audit, *, exc=None):
    """直接调 `_audit_handoff_sent()`（唯一入口），返回 (返回值, 收到的载荷)。"""
    recorded: list = []

    def fake(pid, action, payload=None):
        if exc is not None:
            raise exc
        recorded.append((pid, action, dict(payload or {})))

    with _Patch((handoff.store, "audit", fake)):
        try:
            out = handoff._audit_handoff_sent(
                PID, requirement_no=REQ_NO, scenario_code="default",
                handoff_no="pkghandoff:%s:%s:default:1" % (PID, REQ_NO), version_no=1,
                already_sent=False, cost_result_version=RESULT_VERSION, has_gaps=False,
                quote_session_id="sess-1", by="CF1")
        except Exception as caught:      # noqa: BLE001 — 留痕失败不许抛，这里抓住即判失败
            return {"raised": caught, "out": None, "recorded": recorded}
    return {"raised": None, "out": out, "recorded": recorded}


def _send(*, user=None, rows=(), audit_error=None):
    recorded: list = []
    saved: list = []

    def pkg(*args, **kwargs):
        return dict(PACKAGE)

    def guard(package, *, allow_gaps=False, reason="", user=None):
        return None

    def bridge(*args, **kwargs):
        return {"handoff_id": "h-1", "quote_session_id": "sess-1",
                "business_case_id": "bc-1", "handoff": {"task_id": "task-1"}}

    def audit(pid, action, payload=None):
        if audit_error is not None:
            raise audit_error
        recorded.append((pid, action, dict(payload or {})))

    with _Patch((handoff, "handoff_package", pkg),
                (handoff, "_guard_gaps", guard),
                (handoff, "package_fingerprint", lambda package: "fp-1"),
                (handoff, "bridge_result", lambda package: {"packaging_package": package}),
                (handoff.da_repo, "packaging_handoffs", lambda *a, **k: list(rows)),
                (handoff.da_repo, "save_packaging_handoff",
                 lambda record: saved.append(dict(record)) or "pkghandoff:saved"),
                (handoff.cpq_bridge, "send_to_quote", bridge),
                (handoff.store, "load_business_case", lambda *a, **k: {}),
                (handoff.store, "audit", audit),
                (handoff.da_db, "now", lambda: "2026-09-22 12:00:00")):
        try:
            out = handoff.send_to_quote(PID, REQ_NO, user=_user() if user is None else user,
                                        token="tok")
            return {"ok": True, "out": out, "recorded": recorded, "saved": saved, "error": None}
        except handoff.HandoffError as exc:
            return {"ok": False, "out": None, "recorded": recorded, "saved": saved, "error": exc}


# --------------------------------------------------------------------------- #
# A 组：`_audit_handoff_sent()` 自己要把可用性说出来
# --------------------------------------------------------------------------- #
class AHandoffAuditAvailability(unittest.TestCase):
    def test_a1_success_returns_ok_disclosure(self):
        result = _audit_call(None)
        self.assertIsNone(result["raised"], "写审计成功时不许抛异常")
        out = result["out"]
        self.assertIsInstance(
            out, dict,
            "`_audit_handoff_sent()` 必须返回披露体（Spec §C1）：现在是 `-> None`，"
            "调用方拿不到任何事实")
        self.assertEqual(set(DISCLOSURE_KEYS), set(out),
                         "披露体键集固定五个（Spec §C1）：attempted/ok/action/code/message")
        self.assertIs(True, bool(out.get("attempted")), "attempted 恒 True（Spec §C1）")
        self.assertIs(True, bool(out.get("ok")), "写成功时 ok 必须是 True")
        self.assertEqual(ACTION, out.get("action"), "action 逐字等于 AUDIT_SENT_ACTION")
        self.assertEqual("", out.get("code"), "写成功没有码（Spec §C1）")
        self.assertEqual("", out.get("message"), "写成功没有消息（Spec §C1）")

    def test_a2_failure_returns_stable_code_and_reason(self):
        result = _audit_call(None, exc=RuntimeError("audit backend down"))
        self.assertIsNone(result["raised"],
                          "留痕失败不许抛出去（Spec §C1：留痕不是闸门）")
        out = result["out"]
        self.assertIsInstance(out, dict, "失败也要给披露体（Spec §C1）")
        self.assertIs(False, bool(out.get("ok")), "写失败必须 ok=False")
        self.assertEqual(handoff.AUDIT_UNAVAILABLE_CODE, out.get("code"),
                         "稳定码必须是模块级常量 AUDIT_UNAVAILABLE_CODE（Spec §C2）")
        message = str(out.get("message") or "")
        self.assertIn("RuntimeError", message, "message 必须含异常类名（Spec §C1）")
        self.assertIn("audit backend down", message, "message 必须含原异常文本（Spec §C1）")

    def test_a3_failure_keeps_payload_discipline(self):
        result = _audit_call(None, exc=OSError("disk full"))
        out = result["out"]
        self.assertIsInstance(out, dict, "失败也要给披露体（Spec §C1）")
        self.assertEqual(set(DISCLOSURE_KEYS), set(out),
                         "失败时的披露体键集也必须只有这五个（Spec §C1/C4）")
        for key in handoff._FORBIDDEN_COST_KEYS:
            self.assertNotIn(key, out, "披露体不许出现售价 / 毛利字段")
        self.assertNotIn("package", out, "披露体不许把整份交接包塞进去")
        self.assertNotIn("token", out, "披露体不许把登录凭据塞进去")

    def test_a4_constant_is_module_level_and_action_unchanged(self):
        self.assertEqual("PACKAGING_HANDOFF_AUDIT_UNAVAILABLE", handoff.AUDIT_UNAVAILABLE_CODE,
                         "稳定码逐字固定（Spec §C2）")
        self.assertEqual("workflow:packaging_handoff_sent", handoff.AUDIT_SENT_ACTION,
                         "既有动作名逐字不变（Spec §C5）")

    def test_a5_empty_exception_text_still_names_the_class(self):
        result = _audit_call(None, exc=ValueError())
        out = result["out"]
        self.assertIsInstance(out, dict, "失败也要给披露体（Spec §C1）")
        message = str(out.get("message") or "")
        self.assertTrue(message, "异常没有文本时也不许给空串（Spec §C1）")
        self.assertIn("ValueError", message, "至少要有异常类名（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：`send_to_quote()` 两条路径都要把披露带给调用方
# --------------------------------------------------------------------------- #
class BSendToQuoteDisclosesAudit(unittest.TestCase):
    def test_b1_first_send_carries_ok_disclosure(self):
        result = _send()
        self.assertTrue(result["ok"], "正常回传必须成功")
        out = result["out"]
        self.assertIn("audit", out, "首次回传的返回体必须带 `audit`（Spec §C3）")
        audit = out["audit"]
        self.assertIsInstance(audit, dict, "`audit` 必须是披露体（Spec §C1）")
        self.assertIs(True, bool(audit.get("ok")), "审计写成功时 audit.ok=True")
        self.assertEqual(ACTION, audit.get("action"))
        self.assertEqual("", audit.get("code"))
        self.assertEqual(1, len(result["recorded"]), "既有口径：成功回传恰好一条审计")

    def test_b2_first_send_audit_failure_is_disclosed_but_send_still_succeeds(self):
        result = _send(audit_error=RuntimeError("no such table"))
        self.assertTrue(result["ok"], "留痕失败不许把回传判成失败（Spec §C3）")
        out = result["out"]
        self.assertIn("audit", out, "审计失败时更要说出来（Spec §C3）")
        audit = out["audit"]
        self.assertIs(False, bool(audit.get("ok")), "audit.ok 必须是 False")
        self.assertEqual(handoff.AUDIT_UNAVAILABLE_CODE, audit.get("code"))
        self.assertIn("no such table", str(audit.get("message") or ""))
        self.assertIs(False, bool(out.get("already_sent")), "首次回传的既有键不变")
        self.assertEqual("pkghandoff:%s:%s:default:1" % (PID, REQ_NO), out.get("handoff_no"))
        self.assertEqual(1, out.get("version_no"))
        self.assertEqual(1, len(result["saved"]),
                         "审计失败不许阻止落库（Spec §C3/C4）")

    def test_b3_reused_send_carries_disclosure_too(self):
        result = _send(rows=[dict(REUSED_ROW)])
        self.assertTrue(result["ok"])
        out = result["out"]
        self.assertIs(True, bool(out.get("already_sent")), "同包重发应命中复用路径")
        self.assertIn("audit", out, "复用路径的返回体也必须带 `audit`（Spec §C3）")
        audit = out["audit"]
        self.assertIsInstance(audit, dict, "`audit` 必须是披露体（Spec §C1）")
        self.assertIs(True, bool(audit.get("ok")))
        self.assertIs(True, bool(audit.get("attempted")), "复用也是动作，留痕必须尝试过")

    def test_b4_reused_send_discloses_audit_failure(self):
        result = _send(rows=[dict(REUSED_ROW)], audit_error=OSError("readonly fs"))
        self.assertTrue(result["ok"], "留痕失败不许把复用判成失败")
        out = result["out"]
        self.assertIs(True, bool(out.get("already_sent")))
        audit = out.get("audit") or {}
        self.assertIs(False, bool(audit.get("ok")), "复用路径的审计失败同样要披露")
        self.assertEqual(handoff.AUDIT_UNAVAILABLE_CODE, audit.get("code"))
        self.assertIn("readonly fs", str(audit.get("message") or ""))

    def test_b5_audit_failure_does_not_change_reuse_identity_keys(self):
        broken = _send(rows=[dict(REUSED_ROW)], audit_error=RuntimeError("x"))
        healthy = _send(rows=[dict(REUSED_ROW)])
        self.assertTrue(broken["ok"] and healthy["ok"])
        for key in ("handoff_no", "version_no", "already_sent", "quote_session_id",
                    "business_case_id", "package_fingerprint"):
            self.assertEqual(healthy["out"].get(key), broken["out"].get(key),
                             "审计写不写，回传的身份键必须逐字相同（Spec §C3/C4）：%s" % key)


# --------------------------------------------------------------------------- #
# C 组：护栏（现状即绿）—— 拒绝路径零审计、留痕不是闸门、路由原样透传
# --------------------------------------------------------------------------- #
class CGuardrails(unittest.TestCase):
    def test_c1_role_refusal_leaves_no_audit_and_no_handoff(self):
        result = _send(user=_user(role="sales", username="SM1"))
        self.assertFalse(result["ok"], "非回传角色必须被拒")
        self.assertEqual(403, getattr(result["error"], "status_code", 0))
        self.assertEqual([], result["recorded"], "被拒的调用发生在落库之前，一条审计都不许留")
        self.assertEqual([], result["saved"])

    def test_c2_unresolved_gaps_leave_no_audit(self):
        recorded: list = []
        error = handoff.HandoffError("成本仍有缺口（cost_gap_a），不能生成正式报价",
                                     409, "cost_gaps_unresolved")

        def guard(package, *, allow_gaps=False, reason="", user=None):
            raise error

        with _Patch((handoff, "handoff_package", lambda *a, **k: dict(PACKAGE)),
                    (handoff, "_guard_gaps", guard),
                    (handoff.store, "audit",
                     lambda pid, action, payload=None: recorded.append(action))):
            with self.assertRaises(handoff.HandoffError):
                handoff.send_to_quote(PID, REQ_NO, user=_user(), token="tok")
        self.assertEqual([], recorded, "缺口未清被拒时不许留下「已回传」的痕迹")

    def test_c3_handoff_package_still_writes_no_audit(self):
        import inspect
        source = inspect.getsource(handoff.handoff_package)
        self.assertNotIn("store.audit", source,
                         "`handoff_package()` 仍然只组装、不写审计（Spec §C5）")

    def test_c4_route_returns_the_service_result_verbatim(self):
        main_source = (ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn('return {"handoff": result}', main_source,
                      "路由必须原样透传服务结果（`audit` 才能到接口，Spec §C5）")

    def test_c5_payload_keys_unchanged(self):
        result = _audit_call(None)
        recorded = result["recorded"]
        self.assertEqual(1, len(recorded), "写成功必须恰好一条审计")
        payload = recorded[0][2]
        for key in AUDIT_KEYS:
            self.assertIn(key, payload, "九键载荷逐字不变（Spec §C5）：%s" % key)
        for key in handoff._FORBIDDEN_COST_KEYS:
            self.assertNotIn(key, payload, "审计载荷不许出现售价 / 毛利字段")


if __name__ == "__main__":
    unittest.main()
