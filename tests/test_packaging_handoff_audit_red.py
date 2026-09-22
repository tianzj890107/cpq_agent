"""红测：包装回传报价必须留项目审计（谁在什么时候把哪一版推给了报价侧）。

Spec：`docs/specs/packaging-handoff-audit-trail.md`

现状缺口（代码级，都可指到行）：
  · `packaging_handoff.py` 全文 `store.audit` 出现 **0** 次（对照 `packaging_bom.py:1393`、
    `packaging_match.py:827`、`packaging_route.py:586/637`、`packaging_cost.py:2388` 都写了）；
  · 通用行业的同一动作有：`cost_flow.py:776 store.audit(project_id, "integration_send_to_quote", …)`；
  · 于是"谁把这一版推给了报价侧"只能翻 `wip_packaging_handoff` 表；而且**同包重发**
    （`_reuse_outcome()`，`:432`）与"第一次发出"在审计上完全不可分（两边都没有记录）。

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
REQ_NO = "REQ-HANDOFFAUDIT-001"
RESULT_VERSION = "pkgcost-v1:1000:1234.500000"
ACTION = "workflow:packaging_handoff_sent"

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


def _send(*, user=None, rows=(), guard_error=None, package_error=None):
    recorded: list = []
    saved: list = []

    def pkg(*args, **kwargs):
        if package_error is not None:
            raise package_error
        return dict(PACKAGE)

    def guard(package, *, allow_gaps=False, reason="", user=None):
        if guard_error is not None:
            raise guard_error
        return None

    def bridge(*args, **kwargs):
        return {"handoff_id": "h-1", "quote_session_id": "sess-1",
                "business_case_id": "bc-1", "handoff": {"task_id": "task-1"}}

    with _Patch((handoff, "handoff_package", pkg),
                (handoff, "_guard_gaps", guard),
                (handoff, "package_fingerprint", lambda package: "fp-1"),
                (handoff, "bridge_result", lambda package: {"packaging_package": package}),
                (handoff.da_repo, "packaging_handoffs", lambda *a, **k: list(rows)),
                (handoff.da_repo, "save_packaging_handoff",
                 lambda record: saved.append(dict(record)) or "pkghandoff:saved"),
                (handoff.cpq_bridge, "send_to_quote", bridge),
                (handoff.store, "load_business_case", lambda *a, **k: {}),
                (handoff.store, "audit",
                 lambda pid, action, payload=None: recorded.append(
                     (pid, action, dict(payload or {})))),
                (handoff.da_db, "now", lambda: "2026-09-22 12:00:00")):
        try:
            out = handoff.send_to_quote(PID, REQ_NO, user=_user() if user is None else user,
                                        token="tok")
            return {"ok": True, "out": out, "recorded": recorded, "saved": saved,
                    "error": None}
        except handoff.HandoffError as exc:
            return {"ok": False, "out": None, "recorded": recorded, "saved": saved,
                    "error": exc}


# --------------------------------------------------------------------------- #
# L 组：回传动作的审计留痕
# --------------------------------------------------------------------------- #
class LHandoffAuditTrail(unittest.TestCase):
    def test_l1_first_send_writes_exactly_one_audit(self):
        result = _send()
        self.assertTrue(result["ok"], "正常回传必须成功")
        recorded = result["recorded"]
        self.assertEqual(1, len(recorded),
                         "回传必须在项目审计里留一条（Spec §2.1）："
                         "现在 `packaging_handoff` 全文一次 `store.audit` 都没有")
        pid, action, payload = recorded[0]
        self.assertEqual(PID, pid)
        self.assertEqual(ACTION, action, "动作名固定 `workflow:packaging_handoff_sent`")
        self.assertEqual(REQ_NO, payload.get("requirement_no"))
        self.assertEqual("default", payload.get("scenario_code"))
        self.assertEqual("pkghandoff:%s:%s:default:1" % (PID, REQ_NO),
                         payload.get("handoff_no"))
        self.assertEqual(1, payload.get("version_no"))
        self.assertIs(False, bool(payload.get("already_sent")))
        self.assertEqual(RESULT_VERSION, payload.get("cost_result_version"))
        self.assertEqual("sess-1", payload.get("quote_session_id"))
        self.assertEqual("CF1", payload.get("by"), "审计要能说出是谁点的（username 逐字）")
        self.assertIn("has_gaps", payload, "有缺口没缺口是回传事实的一部分，键必须存在")

    def test_l2_reused_send_is_audited_with_the_reused_row(self):
        result = _send(rows=[dict(REUSED_ROW)])
        self.assertTrue(result["ok"])
        self.assertTrue(result["out"].get("already_sent"), "同包重发应命中复用路径")
        recorded = result["recorded"]
        self.assertEqual(1, len(recorded),
                         "同包重发也是动作，必须留一条（Spec §2.1）："
                         "现在复用路径直接 return，连记录都不碰")
        payload = recorded[0][2]
        self.assertIs(True, bool(payload.get("already_sent")))
        self.assertEqual(REUSED_ROW["handoff_no"], payload.get("handoff_no"),
                         "复用路径的 handoff_no 必须是被复用那一行，不许新编一个")
        self.assertEqual(2, payload.get("version_no"))
        self.assertEqual("sess-9", payload.get("quote_session_id"))

    def test_l3_role_refusal_leaves_no_audit(self):
        result = _send(user=_user(role="sales", username="SM1"))
        self.assertFalse(result["ok"], "非回传角色必须被拒")
        self.assertEqual(403, getattr(result["error"], "status_code", 0))
        self.assertEqual([], result["recorded"],
                         "被拒的调用发生在落库之前，一条审计都不许留（既有口径）")
        self.assertEqual([], result["saved"])

    def test_l4_audit_payload_never_carries_prices(self):
        result = _send()
        recorded = result["recorded"]
        self.assertEqual(1, len(recorded),
                         "没有审计就等于没有载荷可查（Spec §2.1）")
        payload = recorded[0][2]
        for key in handoff._FORBIDDEN_COST_KEYS:
            self.assertNotIn(key, payload,
                             "审计载荷不许出现售价 / 毛利字段（第 7 批同一条禁令）")
        self.assertNotIn("package", payload, "不许把整份交接包灌进审计载荷")
        self.assertNotIn("token", payload, "不许把登录凭据灌进审计载荷")

    def test_l5_unresolved_gaps_leave_no_audit(self):
        error = handoff.HandoffError("成本仍有缺口（cost_gap_a），不能生成正式报价",
                                     409, "cost_gaps_unresolved")
        result = _send(guard_error=error)
        self.assertFalse(result["ok"])
        self.assertEqual("cost_gaps_unresolved", getattr(result["error"], "code", ""))
        self.assertEqual([], result["recorded"], "缺口未清被拒时不许留下'已回传'的痕迹")
        self.assertEqual([], result["saved"])


if __name__ == "__main__":
    unittest.main()
