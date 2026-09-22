"""红测：留痕没落下时的**重试 / 补写 / 告警**（Spec `packaging-handoff-audit-relay.md`）。

现状缺口（代码级，可指到行）：
  · `packaging_handoff._audit_handoff_sent()`（`:331`）的 `try/except` 只包**一次**
    `store.audit()`：失败即丢，没有重试（`store.audit` 的调用数恒 <= 1）；
  · 披露体只有五键（`:355`），说不出"试了几次、有没有记成待补写"；
  · 没有任何**待补写**的落点 —— 披露体只活在这一次回传响应里，刷新即无；
  · `main.py` 只有 send / 读 / versions / package 四条包装报价路由（`:7673-7676`），
    没有"还欠几条留痕"的读接口，也没有补写接口。

纪律：只读源码 + 假仓库 / 假文档 / 假审计；不连 PG / SQLite 生产库、不发 HTTP、
不写文件、不落库、不联网。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import main                                        # noqa: E402
from tech_app.backend.services import packaging_handoff as handoff       # noqa: E402

HANDOFF_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_handoff.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

PID = "testpid00001"
REQ_NO = "REQ-HANDOFFAUDITRELAY-001"
RESULT_VERSION = "pkgcost-v1:1000:1234.500000"
ACTION = "workflow:packaging_handoff_sent"
RELAY_ACTION = "workflow:packaging_handoff_audit_relayed"
PENDING_DOC_KEY = "packaging_handoff_audit_pending"
PENDING_UNAVAILABLE_CODE = "PACKAGING_HANDOFF_AUDIT_PENDING_UNAVAILABLE"

AUDIT_KEYS = ("requirement_no", "scenario_code", "handoff_no", "version_no", "already_sent",
              "cost_result_version", "has_gaps", "quote_session_id", "by")
DISCLOSURE_KEYS = ("attempted", "ok", "action", "code", "message", "attempts", "pending")
RECORD_KEYS = ("ok", "code", "pending_id", "count")
RELAY_KEYS = ("attempted", "relayed", "remaining", "code", "message")

PAYLOAD = {"requirement_no": REQ_NO, "scenario_code": "default",
           "handoff_no": "pkghandoff:%s:%s:default:1" % (PID, REQ_NO), "version_no": 1,
           "already_sent": False, "cost_result_version": RESULT_VERSION,
           "has_gaps": False, "quote_session_id": "sess-1", "by": "CF1"}


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


class _FakeBackend:
    """假文档后端：内存字典 + 可注入的读写故障（不落盘、不连库）。"""

    def __init__(self, *, put_error=None, get_error=None):
        self.docs: dict = {}
        self.put_error = put_error
        self.get_error = get_error

    def get_doc(self, pid, kind):
        if self.get_error is not None:
            raise self.get_error
        return copy.deepcopy(self.docs.get((pid, kind)))

    def put_doc(self, pid, kind, data):
        if self.put_error is not None:
            raise self.put_error
        self.docs[(pid, kind)] = copy.deepcopy(data)


def _user(role="finance_manager", username="CF1"):
    return {"username": username, "role": role, "role_code": role, "role_name": "财务经理"}


def _expected_pending_id(payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _audit_call(*, outcomes=(None,), backend=None):
    """直接调 `_audit_handoff_sent()`，`outcomes` 逐次决定 audit 成功（`None`）还是抛异常。"""
    calls: list = []

    def audit(pid, action, payload=None):
        index = len(calls)
        calls.append((pid, action, copy.deepcopy(payload)))
        mode = outcomes[index] if index < len(outcomes) else None
        if mode is not None:
            raise mode

    store_backend = backend if backend is not None else _FakeBackend()
    with _Patch((handoff.store, "audit", audit),
                (handoff, "get_backend", lambda: store_backend)):
        try:
            out = handoff._audit_handoff_sent(
                PID, requirement_no=REQ_NO, scenario_code="default",
                handoff_no=PAYLOAD["handoff_no"], version_no=1, already_sent=False,
                cost_result_version=RESULT_VERSION, has_gaps=False,
                quote_session_id="sess-1", by="CF1")
        except Exception as caught:      # noqa: BLE001 — 留痕失败不许抛，抓住即判失败
            return {"raised": caught, "out": None, "calls": calls, "backend": store_backend}
    return {"raised": None, "out": out, "calls": calls, "backend": store_backend}


# --------------------------------------------------------------------------- #
# A 组：当场再试一次，并让披露体回答"试了几次、有没有待补写"
# --------------------------------------------------------------------------- #
class AHandoffAuditRelayDisclosure(unittest.TestCase):
    def test_a1_success_keeps_seven_key_disclosure(self):
        result = _audit_call()
        self.assertIsNone(result["raised"], "写审计成功时不许抛异常")
        out = result["out"]
        self.assertIsInstance(out, dict, "`_audit_handoff_sent()` 必须返回披露体（Spec §C1）")
        self.assertEqual(set(DISCLOSURE_KEYS), set(out),
                         "披露体由五键扩为七键（Spec §C1，availability.md §C1 按本批重指）")
        self.assertIs(True, bool(out.get("attempted")), "attempted 恒 True")
        self.assertIs(True, bool(out.get("ok")), "写成功时 ok 必须是 True")
        self.assertEqual(ACTION, out.get("action"), "action 逐字等于 AUDIT_SENT_ACTION")
        self.assertEqual("", out.get("code"), "写成功没有码")
        self.assertEqual("", out.get("message"), "写成功没有消息")
        self.assertEqual(1, out.get("attempts"), "一次就写成的 attempts 必须是 1（Spec §C1）")
        self.assertEqual("", out.get("pending"), "成功就没有待补写（Spec §C1）")

    def test_a2_retries_once_then_succeeds(self):
        result = _audit_call(outcomes=(RuntimeError("audit backend down"), None))
        self.assertIsNone(result["raised"], "第一次失败后重试成功，不许抛")
        out = result["out"]
        self.assertIs(True, bool(out.get("ok")), "重试成功后 ok 只回答**最终**结果")
        self.assertEqual(2, out.get("attempts"), "失败一次再成功 = 试了 2 次（Spec §C1）")
        self.assertEqual("", out.get("code"), "最终成功不给码")
        self.assertEqual("", out.get("message"), "最终成功不给消息")
        self.assertEqual("", out.get("pending"), "最终成功没有待补写")
        self.assertEqual(2, len(result["calls"]), "必须真调了 2 次 store.audit（Spec §C1）")

    def test_a3_double_failure_records_pending(self):
        result = _audit_call(outcomes=(RuntimeError("audit backend down"), RuntimeError("audit backend down")))
        out = result["out"]
        self.assertIs(False, bool(out.get("ok")), "两次都失败 ok 必须是 False")
        self.assertEqual(handoff.AUDIT_UNAVAILABLE_CODE, out.get("code"))
        message = str(out.get("message") or "")
        self.assertIn("RuntimeError", message, "message 必须含异常类名")
        self.assertEqual(2, out.get("attempts"), "两次都失败 = attempts 2（Spec §C1）")
        self.assertEqual(2, len(result["calls"]), "重试上界决定调用数（Spec §C1）")
        self.assertEqual("recorded", out.get("pending"),
                         "两次都失败必须记成待补写，并如实写 recorded（Spec §C1/C2）")
        items = (result["backend"].docs.get((PID, PENDING_DOC_KEY)) or {}).get("items") or []
        self.assertEqual(1, len(items), "待补写文档里必须真有这一条（Spec §C2）")
        self.assertEqual(_expected_pending_id(PAYLOAD), items[0].get("pending_id"),
                         "pending_id = 九键载荷规范 JSON 的 sha256 前 16 位（Spec §C2）")

    def test_a4_pending_store_failure_is_disclosed_not_raised(self):
        backend = _FakeBackend(put_error=OSError("readonly fs"))
        result = _audit_call(outcomes=(RuntimeError("audit backend down"), RuntimeError("audit backend down")), backend=backend)
        self.assertIsNone(result["raised"], "待补写也写不进去时仍然不许抛（Spec §C2/C6）")
        out = result["out"]
        self.assertIs(False, bool(out.get("ok")))
        self.assertEqual("unavailable", out.get("pending"),
                         "待补写记不上时必须如实说 unavailable，不许假装记上了（Spec §C1/C6）")

    def test_a5_retry_is_bounded(self):
        self.assertEqual(1, handoff.AUDIT_RETRY_LIMIT,
                         "重试上界是模块级常量且为 1（Spec §C1）")
        for outcomes in ((RuntimeError("x"), RuntimeError("x"), None),
                         (RuntimeError("x"), RuntimeError("x"), RuntimeError("x"))):
            result = _audit_call(outcomes=outcomes)
            self.assertLessEqual(len(result["calls"]), 1 + handoff.AUDIT_RETRY_LIMIT,
                                 "重试次数必须由 AUDIT_RETRY_LIMIT 决定（Spec §C1/§4）")

    def test_a6_no_sleep_and_no_loop_in_retry(self):
        source = HANDOFF_PY.read_text(encoding="utf-8")
        body = source.split("def _audit_handoff_sent(", 1)[1].split("\ndef ", 1)[0]
        # 只扫**代码**：文档串里写「不 sleep」是说明，不是调用（散文不该被判据误伤）。
        code = re.sub(r'"""[\s\S]*?"""', "", body)
        self.assertNotIn("sleep", code, "重试必须同步、不 sleep（Spec §C1/§4）")
        self.assertNotIn("while ", code, "不许无界循环重试（Spec §C1/§4）")
        self.assertNotIn("Thread", source, "不许后台线程补写（Spec §4）")

    def test_a7_payload_still_frozen_nine_keys(self):
        result = _audit_call()
        payload = result["calls"][0][2]
        for key in AUDIT_KEYS:
            self.assertIn(key, payload, "九键载荷逐字不变（Spec §C4）：%s" % key)
        for key in handoff._FORBIDDEN_COST_KEYS:
            self.assertNotIn(key, payload, "审计载荷不许出现售价 / 毛利字段")
        disclosure = result["out"]
        for key in handoff._FORBIDDEN_COST_KEYS:
            self.assertNotIn(key, disclosure, "披露体不许出现售价 / 毛利字段")
        self.assertNotIn("payload", disclosure, "披露体不许把九键载荷塞回去")
        self.assertNotIn("token", disclosure, "披露体不许把登录凭据塞进去")

    def test_a8_action_name_unchanged(self):
        self.assertEqual(ACTION, handoff.AUDIT_SENT_ACTION,
                         "既有动作名逐字不变（Spec §C4）")
        self.assertEqual("PACKAGING_HANDOFF_AUDIT_UNAVAILABLE", handoff.AUDIT_UNAVAILABLE_CODE,
                         "既有稳定码逐字不变（Spec §C4）")


# --------------------------------------------------------------------------- #
# B 组：待补写文档（幂等 / 上限 / 写不进去也不抛）与补写
# --------------------------------------------------------------------------- #
class BPendingAuditStore(unittest.TestCase):
    def _record(self, backend, payload, **kwargs):
        with _Patch((handoff, "get_backend", lambda: backend)):
            return handoff.record_pending_audit(PID, payload, **kwargs)

    def _load(self, backend):
        with _Patch((handoff, "get_backend", lambda: backend)):
            return handoff.load_pending_audits(PID)

    def test_b1_constants_are_named_and_frozen(self):
        self.assertEqual(PENDING_DOC_KEY, handoff.AUDIT_PENDING_DOC_KEY,
                         "文档键逐字固定（Spec §C2）")
        self.assertEqual(20, handoff.AUDIT_PENDING_MAX, "上限逐字为 20（Spec §C2）")
        self.assertEqual(PENDING_UNAVAILABLE_CODE, handoff.AUDIT_PENDING_UNAVAILABLE_CODE,
                         "稳定码逐字固定（Spec §C2）")
        self.assertEqual(RELAY_ACTION, handoff.AUDIT_RELAY_ACTION,
                         "补写动作名逐字固定（Spec §C2）")

    def test_b2_record_returns_four_keys(self):
        backend = _FakeBackend()
        out = self._record(backend, dict(PAYLOAD), code="C", message="m", by="CF1")
        self.assertIsInstance(out, dict, "record_pending_audit 必须返回四键（Spec §C2）")
        self.assertEqual(set(RECORD_KEYS), set(out), "返回键集固定四键（Spec §C2）")
        self.assertIs(True, bool(out.get("ok")))
        self.assertEqual("", out.get("code"))
        self.assertEqual(_expected_pending_id(PAYLOAD), out.get("pending_id"),
                         "pending_id = 九键载荷规范 JSON 的 sha256 前 16 位（Spec §C2）")
        self.assertEqual(1, out.get("count"))
        items = (backend.docs.get((PID, PENDING_DOC_KEY)) or {}).get("items") or []
        self.assertEqual(1, len(items))
        row = items[0]
        for key in ("pending_id", "payload", "code", "message", "recorded_at", "by"):
            self.assertIn(key, row, "每条待补写要带这六键（Spec §C2）：%s" % key)
        self.assertEqual(PAYLOAD, row.get("payload"), "payload 逐字来自那次失败的回传载荷")
        self.assertEqual("CF1", row.get("by"))

    def test_b3_same_payload_is_idempotent(self):
        backend = _FakeBackend()
        first = self._record(backend, dict(PAYLOAD))
        second = self._record(backend, dict(PAYLOAD))
        self.assertEqual(first.get("pending_id"), second.get("pending_id"),
                         "同一份留痕重复记 → 同一条（Spec §C2）")
        self.assertEqual(1, second.get("count"), "幂等：count 不涨（Spec §C2）")
        items = (backend.docs.get((PID, PENDING_DOC_KEY)) or {}).get("items") or []
        self.assertEqual(1, len(items), "重复记不许留下第二条")

    def test_b4_newest_first_and_capped(self):
        backend = _FakeBackend()
        for index in range(25):
            payload = dict(PAYLOAD, version_no=index + 1)
            self._record(backend, payload)
        items = (backend.docs.get((PID, PENDING_DOC_KEY)) or {}).get("items") or []
        self.assertEqual(handoff.AUDIT_PENDING_MAX, len(items),
                         "最多留 AUDIT_PENDING_MAX 条（Spec §C2）")
        self.assertEqual(dict(PAYLOAD, version_no=25), items[0].get("payload"),
                         "新记录插到最前（Spec §C2）")

    def test_b5_non_dict_payload_gives_code_not_exception(self):
        backend = _FakeBackend()
        out = self._record(backend, ["not", "a", "dict"])
        self.assertIsInstance(out, dict, "返回必须仍是四键（Spec §C2）")
        self.assertIs(False, bool(out.get("ok")))
        self.assertEqual(PENDING_UNAVAILABLE_CODE, out.get("code"))
        self.assertEqual(0, out.get("count"))

    def test_b6_store_failure_gives_code_not_exception(self):
        backend = _FakeBackend(put_error=RuntimeError("db down"))
        out = self._record(backend, dict(PAYLOAD))
        self.assertIsInstance(out, dict, "落库抛异常时必须返回四键（Spec §C2）")
        self.assertIs(False, bool(out.get("ok")), "写不进去不许假装成功（Spec §C6）")
        self.assertEqual(PENDING_UNAVAILABLE_CODE, out.get("code"))

    def test_b7_load_empty_and_unreadable(self):
        self.assertEqual({"items": [], "count": 0}, self._load(_FakeBackend()),
                         "没记过 → 空清单（Spec §C2）")
        broken = _FakeBackend(get_error=RuntimeError("db down"))
        self.assertEqual({"items": [], "count": 0}, self._load(broken),
                         "读不到也要返回空清单，不许抛（Spec §C2）")


class BRelayPendingAudits(unittest.TestCase):
    def _relay(self, backend, calls):
        with _Patch((handoff, "get_backend", lambda: backend),
                    (handoff.store, "audit", calls)):
            return handoff.relay_pending_audits(PID, by="CF1")

    def _seed(self, backend, count=2, exc=None):
        """先把 count 条待补写塞进假后端（失败条用不同 version_no 区分）。"""
        with _Patch((handoff, "get_backend", lambda: backend)):
            for index in range(count):
                handoff.record_pending_audit(PID, dict(PAYLOAD, version_no=index + 1),
                                             code=exc if exc else "", message="m")

    def test_b8_relay_all_success_removes_and_audits(self):
        backend = _FakeBackend()
        self._seed(backend, 2)
        calls: list = []

        def audit(pid, action, payload=None):
            calls.append((pid, action, copy.deepcopy(payload)))

        out = self._relay(backend, audit)
        self.assertEqual(set(RELAY_KEYS), set(out), "补写返回五键（Spec §C2）")
        self.assertEqual(2, out.get("attempted"), "这次试了 2 条")
        self.assertEqual(2, out.get("relayed"), "2 条都补上了")
        self.assertEqual(0, out.get("remaining"), "补完不欠了")
        self.assertEqual("", out.get("code"))
        self.assertEqual("", out.get("message"))
        with _Patch((handoff, "get_backend", lambda: backend)):
            left = handoff.load_pending_audits(PID)
        self.assertEqual(0, left["count"], "成功的条必须从待办里删掉（Spec §C2）")
        actions = [action for _pid, action, _payload in calls]
        self.assertEqual(2, actions.count(ACTION), "逐条按 AUDIT_SENT_ACTION 补写")
        self.assertIn(handoff.AUDIT_RELAY_ACTION, actions,
                      "跑过一次就要留一条补写审计（Spec §C2）")

    def test_b9_relay_failure_keeps_the_row(self):
        backend = _FakeBackend()
        self._seed(backend, 2)

        def audit(pid, action, payload=None):
            if action == ACTION:
                if int((payload or {}).get("version_no") or 0) == 1:
                    raise OSError("disk full")

        out = self._relay(backend, audit)
        self.assertEqual(2, out.get("attempted"))
        self.assertEqual(1, out.get("relayed"), "补上的只有 1 条")
        self.assertEqual(1, out.get("remaining"), "失败的条要留着（Spec §C2/§4）")
        self.assertEqual(handoff.AUDIT_UNAVAILABLE_CODE, out.get("code"))
        self.assertIn("OSError", str(out.get("message") or ""),
                      "有失败时 message 含第一条异常的类名（Spec §C2）")
        with _Patch((handoff, "get_backend", lambda: backend)):
            left = handoff.load_pending_audits(PID)
        self.assertEqual(1, left["count"], "失败的那条必须留在待办里")
        self.assertEqual(1, left["items"][0]["payload"].get("version_no"),
                         "留下的正是失败的那条")

    def test_b10_relay_empty_is_a_noop(self):
        backend = _FakeBackend()
        calls: list = []

        def audit(pid, action, payload=None):
            calls.append((pid, action, copy.deepcopy(payload)))

        out = self._relay(backend, audit)
        self.assertEqual({"attempted": 0, "relayed": 0, "remaining": 0,
                          "code": "", "message": ""}, out,
                         "一条都没有时 0/0/0 空码空消息、不抛（Spec §C2）")
        self.assertEqual([], calls, "没有待补写就不许写审计")

    def test_b11_relay_audit_failure_does_not_change_result(self):
        backend = _FakeBackend()
        self._seed(backend, 1)

        def audit(pid, action, payload=None):
            if action == RELAY_ACTION:
                raise RuntimeError("audit backend down")

        out = self._relay(backend, audit)
        self.assertEqual(1, out.get("relayed"), "补写审计写不进去不许改变补写结果（Spec §C2）")
        self.assertEqual(0, out.get("remaining"))
        self.assertEqual("", out.get("code"), "补写本身成功，码就不许非空")


# --------------------------------------------------------------------------- #
# C 组：两条路由（读欠条 / 发起补写）
# --------------------------------------------------------------------------- #
class CRoutes(unittest.TestCase):
    def test_c1_route_paths_are_literals(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        self.assertIn("/api/projects/{pid}/requirement/packaging-quote/audit-pending",
                      source, "缺读路由路径字面量（Spec §C3）")
        self.assertIn("/api/projects/{pid}/requirement/packaging-quote/audit-pending/relay",
                      source, "缺补写路由路径字面量（Spec §C3）")
        self.assertIsNotNone(getattr(main, "get_packaging_handoff_audit_pending", None),
                             "缺 GET /…/packaging-quote/audit-pending（Spec §C3）")
        self.assertIsNotNone(getattr(main, "relay_packaging_handoff_audit_pending", None),
                             "缺 POST /…/packaging-quote/audit-pending/relay（Spec §C3）")

    def test_c2_read_route_is_read_only(self):
        with _Patch((main.packaging_handoff, "load_pending_audits",
                     lambda pid: {"items": [{"pending_id": "p1", "code": "C",
                                             "message": "m", "recorded_at": "t",
                                             "payload": dict(PAYLOAD)}],
                                  "count": 1}),
                    (main, "_workflow_project", lambda pid: {"project_id": pid})):
            out = main.get_packaging_handoff_audit_pending(PID, _user())
        self.assertEqual(PID, out.get("project_id"))
        self.assertEqual(1, out.get("count"))
        self.assertEqual(1, len(out.get("items") or []))
        row = out["items"][0]
        for key in ("pending_id", "code", "message", "recorded_at", "payload"):
            self.assertIn(key, row, "读接口逐条原样带出这些键（Spec §C3）：%s" % key)

    def test_c3_relay_route_role_gate_and_result(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        block = source.split("def relay_packaging_handoff_audit_pending", 1)[1].split("\n@app.", 1)[0]
        self.assertIn("handoff.HANDOFF_WRITE_ROLES", block,
                      "补写权限必须直接引用 HANDOFF_WRITE_ROLES（Spec §C3）")
        calls: list = []

        def relay(pid, *, by=""):
            calls.append((pid, by))
            return {"attempted": 1, "relayed": 1, "remaining": 0, "code": "", "message": ""}

        with _Patch((main.packaging_handoff, "relay_pending_audits", relay),
                    (main.packaging_handoff, "load_pending_audits",
                     lambda pid: {"items": [], "count": 0}),
                    (main, "_workflow_project", lambda pid: {"project_id": pid})):
            out = main.relay_packaging_handoff_audit_pending(PID, _user())
        self.assertEqual([(PID, "CF1")], calls, "补写必须带操作人（Spec §C3）")
        for key in RELAY_KEYS:
            self.assertIn(key, out, "返回体带五键披露体（Spec §C3）：%s" % key)
        self.assertEqual(PID, out.get("project_id"))
        self.assertEqual(0, out.get("count"), "返回补写后**再读一次**的 count（Spec §C3）")
        self.assertEqual([], out.get("items"))

    def test_c4_relay_route_refuses_read_only_role(self):
        from fastapi import HTTPException
        with _Patch((main, "_workflow_project", lambda pid: {"project_id": pid})):
            with self.assertRaises(HTTPException) as ctx:
                main.relay_packaging_handoff_audit_pending(PID, _user(role="sales"))
        self.assertEqual(403, ctx.exception.status_code, "只读用户不许发起补写（Spec §C3）")


# --------------------------------------------------------------------------- #
# D 组：护栏（现状即绿）—— 回传行为、冻结面、既有四条路由原样
# --------------------------------------------------------------------------- #
class DGuardrails(unittest.TestCase):
    def test_d1_send_to_quote_keys_unchanged(self):
        import inspect
        source = inspect.getsource(handoff.send_to_quote)
        self.assertNotIn("relay_pending", source, "回传不许顺手补写（Spec §C4/§6）")
        self.assertIn('"audit"', source, "回传仍要带披露体（Spec §C4）")

    def test_d2_four_existing_routes_untouched(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        for path in ("/api/projects/{project_id}/requirement/packaging-quote/send",
                     "/api/projects/{pid}/requirement/packaging-quote",
                     "/api/projects/{pid}/requirement/packaging-quote/versions",
                     "/api/projects/{pid}/requirement/packaging-quote/package"):
            self.assertIn(path, source, "既有四条路由路径不变（Spec §C4）：%s" % path)

    def test_d3_pending_doc_is_not_the_audit_record(self):
        backend = _FakeBackend()
        with _Patch((handoff, "get_backend", lambda: backend)):
            handoff.record_pending_audit(PID, dict(PAYLOAD))
        doc = backend.docs.get((PID, PENDING_DOC_KEY)) or {}
        self.assertIn("items", doc, "待补写文档是「欠条」，不是审计记录本身（Spec §4）")
        self.assertNotIn(ACTION, json.dumps(doc, ensure_ascii=False),
                         "待补写文档里不许混进审计动作名")

    def test_d4_handoff_package_still_writes_no_audit(self):
        import inspect
        source = inspect.getsource(handoff.handoff_package)
        self.assertNotIn("store.audit", source,
                         "`handoff_package()` 仍然只组装、不写审计（Spec §C4）")

    def test_d5_no_new_dependency(self):
        source = HANDOFF_PY.read_text(encoding="utf-8")
        for banned in ("import requests", "import httpx", "import psycopg", "import urllib.request"):
            self.assertNotIn(banned, source, "不新增依赖、不联网（Spec §C4）：%s" % banned)


if __name__ == "__main__":
    unittest.main()
