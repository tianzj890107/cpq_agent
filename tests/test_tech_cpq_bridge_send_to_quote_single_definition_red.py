"""红测：成本回传报价的桥函数 `send_to_quote()` 被重复定义覆盖。

现状缺口（实测）：
  · `tech_app/backend/services/cpq_bridge.py` 里 `send_to_quote()` 定义了两遍 ——
    `:131` 的一份会发 `handoff_kind: "cost_to_quote"` 与 `result_version`，
    `:159` 的一份这两个字段都没有，签名里也没有 `result_version`。Python 只保留**最后
    一个**定义，于是第一份是死代码，成本回传实际发出的 payload 里没有交接类型、也没有
    版本号。
  · 现有测试全绿，是因为它们做的是**静态文本**提取（拿到的是第一份函数体），
    从不验证运行时真正绑定的是哪一份。本批把模块真的 import 起来，把 `_post` 换成假
    实现，直接检查它实际发出的 payload —— 静态文本提取不是本批的验收方式。
  · 服务端 `cpq_tech_bridge._handoff_key(session, project, kind, version)` 是幂等键，
    命中 still-open 的既有任务时直接返回 `already_sent` 早退（不合并新结果、不派发）。
    版本号恒为 "" 时，同一项目 + 同一报价会话的所有成本版本共用一个幂等键，成本复核后
    的新版本会被前一次未关闭的任务吞掉。

本批只修「唯一实现 + 成本回传必须带 handoff_kind / result_version」：不改
`/wf/tech/handoff` 路由、不改服务端幂等算法与报价步骤单调性、不改报告回传
（`report_handoff` 仍是 `report_to_quote` + `report-v{n}`）。
"""
from __future__ import annotations

import importlib
import inspect
import os
import pathlib
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BRIDGE_PATH = ROOT / "tech_app" / "backend" / "services" / "cpq_bridge.py"
COST_FLOW_PATH = ROOT / "tech_app" / "backend" / "services" / "cost_flow.py"
SUITE_PATH = ROOT / "cpq_suite_server.py"
TECH_BRIDGE_PATH = ROOT / "cpq_tech_bridge.py"


def _load_bridge_module():
    """把客户端桥真的 import 起来（stub 掉 dotenv，DATA_DIR 指向临时目录）。"""
    if "dotenv" not in sys.modules:
        try:
            import dotenv  # noqa: F401
        except ModuleNotFoundError:  # pragma: no cover - 本地无依赖时的兜底
            stub = types.ModuleType("dotenv")
            stub.load_dotenv = lambda *args, **kwargs: None
            sys.modules["dotenv"] = stub
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cpq-bridge-test-"))
    return importlib.import_module("tech_app.backend.services.cpq_bridge")


def _load_server_bridge_module():
    """服务端落地桥（幂等键在这里），同样只 import 不连库。"""
    if "dotenv" not in sys.modules:
        try:
            import dotenv  # noqa: F401
        except ModuleNotFoundError:  # pragma: no cover
            stub = types.ModuleType("dotenv")
            stub.load_dotenv = lambda *args, **kwargs: None
            sys.modules["dotenv"] = stub
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cpq-bridge-test-"))
    return importlib.import_module("cpq_tech_bridge")


def _py_body(source: str, name: str) -> str:
    """取某个顶层函数的函数体（到下一个顶层 def 为止）。"""
    idx = source.find(f"def {name}(")
    if idx < 0:
        return ""
    rest = source[idx:]
    nxt = rest.find("\ndef ", 1)
    return rest if nxt == -1 else rest[:nxt]


def _call_from(source: str, needle: str) -> str:
    """从 needle 所在位置起，取到括号配平为止的完整调用表达式。"""
    idx = source.find(needle)
    if idx < 0:
        return ""
    start = source.find("(", idx)
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(source)):
        char = source[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[idx:i + 1]
    return source[idx:]


class CpqBridgeSendToQuoteSingleDefinitionRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = _load_bridge_module()
        cls.bridge_src = BRIDGE_PATH.read_text(encoding="utf-8", errors="replace")
        cls.cost_flow = COST_FLOW_PATH.read_text(encoding="utf-8", errors="replace")
        cls.suite = SUITE_PATH.read_text(encoding="utf-8", errors="replace")
        cls.tech_bridge = TECH_BRIDGE_PATH.read_text(encoding="utf-8", errors="replace")

    # ---------------------------------------------------- 唯一实现（静态结构）
    def test_only_one_definition_of_send_to_quote(self):
        found = len(re.findall(r"(?m)^def send_to_quote\(", self.bridge_src))
        self.assertEqual(found, 1,
                         f"cpq_bridge.py 里 send_to_quote 定义了 {found} 次；"
                         "后一个定义会静默覆盖前一个，必须只留一份")

    def test_handoff_post_literal_is_used_twice(self):
        found = self.bridge_src.count('"/wf/tech/handoff"')
        self.assertEqual(found, 2,
                         f"/wf/tech/handoff 只该被成本回传与报告回传各用一次，实际 {found} 次")

    def test_other_bridge_functions_stay_single_definition(self):
        for name in ("write_material", "send_to_finance", "return_to_process",
                     "complete_task", "report_handoff"):
            with self.subTest(name=name):
                found = len(re.findall(rf"(?m)^def {name}\(", self.bridge_src))
                self.assertEqual(found, 1, f"{name} 也必须只有一份实现，实际 {found} 份")

    # ------------------------------------------- 运行时真正绑定的是哪一份（核心）
    def test_runtime_bound_function_is_the_version_aware_one(self):
        bound = self.bridge.send_to_quote
        params = inspect.signature(bound).parameters
        self.assertIn("result_version", params,
                      "运行时绑定的 send_to_quote 必须接受 result_version —— "
                      "如果这里报错，说明生效的是那份没有版本号的重复定义")
        source = inspect.getsource(bound)
        self.assertIn("handoff_kind", source,
                      "运行时绑定的实现必须显式发 handoff_kind，不能依赖服务端默认值")
        self.assertIn('"cost_to_quote"', source,
                      "成本回传的 handoff_kind 必须是 cost_to_quote")
        self.assertIn("result_version", source)

    def test_handoff_payload_carries_cost_kind_and_version(self):
        sent = self._capture_call(
            token="tok", session_id="tech-1", title="整机",
            customer="客户", project_name="项目", note="请定价",
            source_task_id="task-9", result={"project_id": "tech-1"},
            source_session_id="quote-7", result_version="cost-v1:1:1234")
        self.assertEqual(sent["path"], "/wf/tech/handoff")
        payload = sent["payload"]
        self.assertEqual(payload.get("handoff_kind"), "cost_to_quote",
                         "成本回传必须显式带 handoff_kind=cost_to_quote")
        self.assertEqual(payload.get("result_version"), "cost-v1:1:1234",
                         "成本回传必须把结果版本号交给服务端做幂等键")

    def test_positional_legacy_call_still_marks_cost_handoff(self):
        """`cost_flow.py` 现在按位置传 9 个参数，这条路径不能断。"""
        sent = self._capture_call(
            "tok", "tech-1", "整机", "客户", "项目", "请定价",
            "task-9", {"project_id": "tech-1"}, "quote-7")
        payload = sent["payload"]
        self.assertEqual(payload.get("handoff_kind"), "cost_to_quote",
                         "位置调用同样必须带 handoff_kind")
        self.assertIn("result_version", payload,
                      "位置调用也要显式带 result_version 键（缺省空串）")
        for field in ("session_id", "title", "customer", "project_name", "note",
                      "source_task_id", "source_session_id", "result"):
            with self.subTest(field=field):
                self.assertIn(field, payload, f"交接 payload 字段 {field} 不得丢失")

    # ---------------------------------------------------- 调用点必须给版本号
    def test_cost_flow_send_call_forwards_result_version(self):
        call = _call_from(self.cost_flow, "cpq_bridge.send_to_quote")
        self.assertTrue(call, "找不到 cost_flow 里的 cpq_bridge.send_to_quote 调用")
        self.assertIn("result_version", call,
                      "2.2 / 2.3 发送报价必须把成本结果版本号一起交给桥，"
                      "否则服务端幂等键无法区分「重复点击」与「成本复核后的新版本」")

    def test_cost_result_version_exists_and_comes_from_cost(self):
        self.assertIn("def result_version(plan)", self.cost_flow,
                      "既有 cost_flow.result_version(plan) 不得被删除")
        self.assertRegex(self.cost_flow,
                         r'"result_version":\s*result_version\(plan\)',
                         "结果版本号必须由既有的 result_version(plan) 派生，不得另写一份")

    def test_server_handoff_key_discriminates_versions(self):
        server = _load_server_bridge_module()
        same = (server._handoff_key("s", "p", "cost_to_quote", "cost-v1:1:100")
                == server._handoff_key("s", "p", "cost_to_quote", "cost-v1:1:100"))
        self.assertTrue(same, "同一版本必须得到同一个幂等键（重复点击才认得出）")
        self.assertNotEqual(
            server._handoff_key("s", "p", "cost_to_quote", "cost-v1:1:100"),
            server._handoff_key("s", "p", "cost_to_quote", "cost-v1:1:260"),
            "不同成本版本必须得到不同的幂等键，否则新版本会被未关闭的任务吞掉")

    def test_server_route_still_reads_kind_and_version(self):
        self.assertIn('d.get("handoff_kind") or "cost_to_quote"', self.suite,
                      "服务端路由继续按 payload 读 handoff_kind")
        self.assertIn('d.get("result_version", "")', self.suite,
                      "服务端路由继续按 payload 读 result_version")
        self.assertRegex(
            self.tech_bridge,
            r"_handoff_key\(session_id, tech_project_id,\s*handoff_kind, result_version\)",
            "服务端幂等键继续由 (会话, 项目, 交接类型, 结果版本) 四元组构成")

    # ---------------------------------------------------- 报告回传与能力不缩水
    def test_report_handoff_keeps_its_own_kind_and_version(self):
        body = _py_body(self.bridge_src, "report_handoff")
        self.assertTrue(body, "找不到 report_handoff()")
        self.assertIn('"report_to_quote"', body,
                      "报告回传必须保持 report_to_quote，不能被合并进成本回传")
        self.assertIn("result_version", body)
        self.assertIn('"/wf/tech/handoff"', body)

    def test_bridge_helpers_kept(self):
        for name in ("_post", "BridgeUnavailable", "BridgeRejected"):
            with self.subTest(name=name):
                self.assertIn(name, self.bridge_src, f"{name} 不得被删除")

    # ---------------------------------------------------- 工具
    def _capture_call(self, *args, **kwargs) -> dict:
        sent: dict = {}

        def fake_post(path, token, payload):
            sent["path"] = path
            sent["token"] = token
            sent["payload"] = payload
            return {"ok": True}

        with mock.patch.object(self.bridge, "_post", fake_post):
            self.bridge.send_to_quote(*args, **kwargs)
        self.assertTrue(sent, "send_to_quote 没有调用 _post，等于什么都没发出去")
        return sent


if __name__ == "__main__":
    unittest.main()
