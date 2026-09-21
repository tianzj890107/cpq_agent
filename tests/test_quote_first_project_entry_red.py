"""红测：项目必须从报价开始（统一入口 / 落点分种类 / 恢复通道）。

Spec：`docs/specs/quote-first-project-entry.md`（新五批 · 第 3 批）。

现场缺口（服务器实测 + 本地只读实测，不是推断）：

  · 现场 P0：独立技术项目（项目 `96a959b0264c`）报告已发布后回传销售报
    「没有找到这条业务实例对应的报价卡片。确认要新建报价卡片时，请填写新建原因后重试。」
    而 `ReportQuoteAction`（main.py:148）只有 note / target_type / target_role_code /
    target_user_id / source_task_id —— 没有 create_new / create_reason，
    发布页也没有这两个交互：用户**无处填写**，只能反复点重试。
  · 结构化冲突字段在桥接层就被丢掉：`cpq_suite_server.py:763` 已经用 409 回
    `{code, candidates, error}`，但 `tech_app/backend/services/cpq_bridge.py:44` 的 `_post`
    把它收敛成 `BridgeRejected(str(message))`；main/cost_flow/report_workflow 三处翻译
    再统一变成「400 + 纯字符串」；前端 `workflow.js:30` 的 `apiError` 只返回字符串，
    `api()` 抛 `new Error(...)` → `report-publish-result.js:54` 读的 `error.code` 恒为 undefined。
  · 技术侧独立建项无门禁：`POST /api/projects`（main.py:1208）只有 file/files/note/attachments。
  · 报价 → 技术建项不带实例号：`grep -c business_case_id tech_app/frontend/tech-task.js` → 0；
    全仓 `save_business_case` 的生产调用点为 0。
  · 全仓 `grep -rn "entry_origin|internal_test|classify_entry|quote-link/recover"` → 无恢复入口。

验证方式：

  · 纯函数行为：`entry_origin.classify_entry`（Spec §2）。
  · AST / 源码契约：后端请求模型与函数签名、前端接口形状。
  · 受控假库真跑：`tests/fixtures/wf_handoff_harness.py`（护栏 —— 带线索的
    `cost_to_process` 仍落原报价卡片，本批"补出口"不许破坏既有落点裁决）。
  · 全部离线：不连 Postgres、不调模型、不起服务、不读写生产数据。
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import importlib
import io
import json
import pathlib
import re
import sys
import unittest
import urllib.error
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (str(ROOT), str(ROOT / "tests" / "fixtures")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import wf_handoff_harness as H  # noqa: E402

SPEC = ROOT / "docs" / "specs" / "quote-first-project-entry.md"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
ENTRY_PY = ROOT / "tech_app" / "backend" / "services" / "entry_origin.py"
REQ_SERVICE_PY = ROOT / "tech_app" / "backend" / "services" / "requirement_service.py"
REPORT_WORKFLOW_PY = ROOT / "tech_app" / "backend" / "services" / "report_workflow.py"
WORKFLOW_JS = ROOT / "tech_app" / "frontend" / "workflow.js"
TECH_TASK_JS = ROOT / "tech_app" / "frontend" / "tech-task.js"
REPORT_PUBLISH_JS = ROOT / "tech_app" / "frontend" / "report-publish-result.js"

#: 入口分级返回的四个键（Spec §2）—— 一个都不能少、一个都不能多。
ENTRY_KEYS = ("origin", "internal_test", "clues", "reason")
#: 报价 → 技术建项的既有溯源键（本批只允许追加，不允许改）。
QUOTE_SOURCE_KEYS_KEPT = ("source", "source_task_id", "source_task_no",
                          "source_session_id", "customer_name")
#: 恢复接口的入参键（Spec §6）。
RECOVERY_FIELDS = ("business_case_id", "quote_session_id", "source_task_id",
                   "source_session_id", "create_new", "create_reason", "note")
BC = "bc_0f1e2d3c4b5a"
RECOVERY_SENTINEL = "quote_link:recovered"


def setUpModule():
    if H.LOAD_ERROR:
        raise AssertionError(H.LOAD_ERROR)
    if not H.SELFCHECK_OK:
        raise AssertionError(f"受控假库自检失败：{H.SELFCHECK_ERROR}")
    if not SPEC.exists():
        raise AssertionError(f"缺少本批 Spec：{SPEC.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# 只读工具：AST / 源码
# ---------------------------------------------------------------------------
_TREES: dict = {}


def read(path) -> str:
    p = pathlib.Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def tree(path):
    key = str(path)
    if key not in _TREES:
        if not pathlib.Path(path).exists():
            raise AssertionError(f"缺少文件：{pathlib.Path(path).relative_to(ROOT)}")
        _TREES[key] = ast.parse(read(path))
    return _TREES[key]


def func_node(path, name, cls=None):
    body = tree(path).body
    if cls:
        owner = class_node(path, cls)
        if owner is None:
            return None
        body = owner.body
    for item in body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
            return item
    return None


def class_node(path, name):
    for item in tree(path).body:
        if isinstance(item, ast.ClassDef) and item.name == name:
            return item
    return None


def param_names(node) -> set:
    if node is None:
        return set()
    args = node.args
    names = {a.arg for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def ann_field_names(cls) -> set:
    if cls is None:
        return set()
    return {item.target.id for item in cls.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}


def calls(node, func_name: str):
    out = []
    if node is None:
        return out
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            if name == func_name:
                out.append(sub)
    return out


def kwarg_names(call) -> set:
    return {k.arg for k in call.keywords if k.arg}


def func_source(path, name, cls=None) -> str:
    """按 AST 行号切片取函数源码（不依赖缩进/换行风格）。"""
    node = func_node(path, name, cls=cls)
    if node is None or not getattr(node, "lineno", None) or not getattr(node, "end_lineno", None):
        return ""
    return "\n".join(read(path).splitlines()[node.lineno - 1:node.end_lineno])


def routes(path) -> dict:
    out = {}
    for item in tree(path).body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in item.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            if isinstance(dec.args[0].value, str):
                out.setdefault(dec.args[0].value, item)
    return out


def js_function(path, name: str) -> str:
    text = read(path)
    match = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", text)
    if not match:
        return ""
    start = text.find("{", match.end())
    if start < 0:
        return ""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[match.start():index + 1]
    return text[match.start():]


def must(condition, message: str):
    if not condition:
        raise AssertionError(message)


def entry_module():
    """`tech_app/backend/services/entry_origin.py`（Spec §2 的唯一入口）。"""
    if not ENTRY_PY.exists():
        raise AssertionError(
            "缺少 tech_app/backend/services/entry_origin.py（Spec §2）：入口分级"
            "（quote / internal_test）必须由这一个纯函数判定，不许散落在建项代码里")
    try:
        module = importlib.import_module("tech_app.backend.services.entry_origin")
    except ImportError as exc:                              # pragma: no cover - 缺口路径
        raise AssertionError(f"entry_origin 不能导入：{exc}") from exc
    fn = getattr(module, "classify_entry", None)
    must(callable(fn),
         "entry_origin 必须定义 classify_entry(*, business_case_id='', source_task_id='', "
         "source_session_id='', source='') -> dict")
    must({"business_case_id", "source_task_id", "source_session_id", "source"} <= param_names(
        func_node(ENTRY_PY, "classify_entry")),
        "classify_entry 必须接受 business_case_id / source_task_id / source_session_id / source")
    return module


def make_conflict(code: str, candidates):
    """按 Spec §4 构造一个落点冲突错误（桥接层必须带 code / candidates / status）。"""
    bridge = importlib.import_module("tech_app.backend.services.cpq_bridge")
    try:
        return bridge.BridgeRejected("落点冲突", code=code, candidates=list(candidates),
                                     status=409)
    except TypeError as exc:
        raise AssertionError(
            "cpq_bridge.BridgeRejected 必须接受 code / candidates / status（Spec §4）："
            f"{exc}。服务器已经用 409 回了这三个字段，桥接层丢掉它们，界面就只能显示"
            "一句无法执行的文案") from exc


# ===========================================================================
# A. 入口分级（统一入口）
# ===========================================================================
class EntryOriginTest(unittest.TestCase):
    def test_a1_module_and_function_exist(self):
        module = entry_module()
        self.assertTrue(callable(module.classify_entry))

    def test_a2_clue_means_quote_entry(self):
        module = entry_module()
        out = module.classify_entry(business_case_id=BC)
        self.assertEqual("quote", str(out.get("origin") or ""),
                         "带业务实例号的建项必须判成正式报价入口")
        self.assertIs(False, out.get("internal_test"))
        self.assertIn("business_case_id", list(out.get("clues") or []))
        self.assertEqual("", str(out.get("reason") or ""))

    def test_a3_no_clue_means_internal_test_entry(self):
        module = entry_module()
        out = module.classify_entry(business_case_id="", source_task_id="",
                                    source_session_id="", source="")
        self.assertEqual("internal_test", str(out.get("origin") or ""),
                         "没有任何报价线索时必须判成内部测试入口，而不是悄悄建一个"
                         "将来回传不了的项目")
        self.assertIs(True, out.get("internal_test"))
        self.assertEqual([], list(out.get("clues") or []))
        self.assertTrue(str(out.get("reason") or "").strip(),
                        "内部测试入口必须给出非空 reason（说清这不是正式入口）")

    def test_a4_pure_function_contract(self):
        module = entry_module()
        kwargs = dict(source_session_id="qs-1", source="CPQ 报价 · 新增工艺")
        first = module.classify_entry(**kwargs)
        second = module.classify_entry(**kwargs)
        self.assertEqual(first, second, "同输入必须同输出")
        self.assertIsNot(first, second, "每次调用必须返回新的 dict，不共享可变状态")
        self.assertEqual(sorted(ENTRY_KEYS), sorted(first.keys()),
                         f"返回键必须恰好是 {ENTRY_KEYS}")
        self.assertEqual(kwargs["source_session_id"], "qs-1", "不许改入参")
        source = read(ENTRY_PY)
        self.assertEqual([], re.findall(r"^\s*(?:from|import)\s+(?:tech_app|psycopg|sqlite3|\w*store)",
                                        source, re.M),
                         "入口分级必须是纯函数：不读库、不连 Postgres")

    def test_a5_upload_project_takes_source_clues_and_reports_entry_origin(self):
        node = func_node(MAIN_PY, "upload_project")
        must(node is not None, "POST /api/projects 的处理函数 upload_project 不存在")
        missing = [name for name in ("business_case_id", "source_task_id", "source_session_id")
                   if name not in param_names(node)]
        self.assertEqual([], missing,
                         f"POST /api/projects 必须接受报价线索表单字段：缺 {missing}")
        body = func_source(MAIN_PY, "upload_project")
        self.assertIn("entry_origin", body,
                      "建项响应体必须带 entry_origin（前端与验收脚本据此判断入口来源）")
        self.assertIn("classify_entry", body,
                      "入口分级只能调用 entry_origin.classify_entry，不许另写一套判断")

    def test_a6_internal_test_entry_leaves_a_trace(self):
        body = func_source(MAIN_PY, "upload_project")
        self.assertIn("save_business_case", body,
                      "建项时必须把入口事实落进项目 meta（store.save_business_case）")
        self.assertIn("internal_test", body,
                      "内部测试入口必须显式留痕（meta / 审计里能读到 internal_test）")
        self.assertIn("store.audit", body,
                      "内部测试入口必须写审计，便于事后区分正式入口与测试入口")


# ===========================================================================
# B. 报价 → 技术建项必须携带实例号
# ===========================================================================
class QuoteToTechEntryTest(unittest.TestCase):
    def test_b1_tech_task_writes_business_case_id(self):
        body = js_function(TECH_TASK_JS, "linkProject")
        must(body, "tech-task.js 的 linkProject 不存在")
        self.assertIn("business_case_id", body,
                      "报价「新增工艺」建项时必须把 business_case_id 写进需求单/项目 meta —— "
                      "现在只写了 source_task_id / source_session_id，落点只能靠会话号兜底")

    def test_b2_quote_source_keys_keep_and_extend(self):
        module = importlib.import_module("tech_app.backend.services.requirement_service")
        keys = tuple(getattr(module, "QUOTE_SOURCE_KEYS", ()) or ())
        kept = [name for name in QUOTE_SOURCE_KEYS_KEPT if name not in keys]
        self.assertEqual([], kept, f"既有溯源键不许删：缺 {kept}")
        self.assertIn("business_case_id", keys,
                      "QUOTE_SOURCE_KEYS 必须追加 business_case_id（只追加，不改既有口径）")

    def test_b3_project_creation_persists_the_instance_id(self):
        body = func_source(MAIN_PY, "upload_project")
        self.assertIn("business_case_id", body,
                      "POST /api/projects 必须接收 business_case_id 并落进项目 meta")
        call_body = "\n".join(line for line in body.splitlines()
                              if "save_business_case" in line or "business_case_id" in line)
        self.assertIn("save_business_case", call_body,
                      "business_case_id 必须通过 store.save_business_case 落盘，"
                      "使 cost_flow.business_case_of 在第一次回传前就能读到它")


# ===========================================================================
# C. 落点冲突的结构化出口
# ===========================================================================
class ConflictExitTest(unittest.TestCase):
    def test_c1_bridge_rejected_carries_the_conflict_fields(self):
        bridge = importlib.import_module("tech_app.backend.services.cpq_bridge")
        err = make_conflict("no_candidate", [{"quote_session_id": "qs-1", "card_id": 1,
                                              "linked_by": "session", "title": "报价卡片"}])
        self.assertEqual("no_candidate", str(getattr(err, "code", "") or ""))
        self.assertEqual(409, int(getattr(err, "status", 0) or 0))
        self.assertTrue(list(getattr(err, "candidates", []) or []),
                        "候选清单必须随错误一起带走")
        plain = bridge.BridgeRejected("参数不合法")
        self.assertEqual("", str(getattr(plain, "code", "") or ""),
                         "不带 code 的普通拒绝仍然可用（向后兼容）")
        self.assertEqual(400, int(getattr(plain, "status", 0) or 0),
                         "普通拒绝默认仍是 400")

    def test_c2_post_preserves_the_409_payload(self):
        bridge = importlib.import_module("tech_app.backend.services.cpq_bridge")
        payload = {"ok": False, "code": "no_candidate",
                   "candidates": [{"quote_session_id": "qs-9", "card_id": 7,
                                   "linked_by": "task", "title": "原报价卡片"}],
                   "error": "没有找到这条业务实例对应的报价卡片。"}
        failure = urllib.error.HTTPError(
            "http://cpq.local/wf/tech/handoff", 409, "Conflict", {},
            io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        with mock.patch.object(bridge.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaises(bridge.BridgeRejected) as caught:
                bridge._post("/wf/tech/handoff", "token", {})
        err = caught.exception
        self.assertEqual("no_candidate", str(getattr(err, "code", "") or ""),
                         "409 响应里的 code 不能丢（现在被压成一句文案）")
        self.assertTrue(list(getattr(err, "candidates", []) or []),
                        "409 响应里的 candidates 不能丢 —— 界面要靠它列候选让人选")
        self.assertEqual(409, int(getattr(err, "status", 0) or 0))

    def test_c3_http_translation_is_409_with_structured_detail(self):
        from fastapi import HTTPException
        main = importlib.import_module("tech_app.backend.main")
        candidates = [{"quote_session_id": "qs-9", "card_id": 7,
                       "linked_by": "task", "title": "原报价卡片"}]
        conflict = make_conflict("no_candidate", candidates)

        def boom():
            raise conflict

        with self.assertRaises(HTTPException) as caught:
            main._bridge_call(boom)
        exc = caught.exception
        self.assertEqual(409, exc.status_code,
                         "落点冲突是业务冲突，不是「你的参数不对」：不能继续用 400")
        detail = exc.detail
        self.assertIsInstance(detail, dict,
                              f"落点冲突必须回结构化 detail（code/candidates/message）：{detail!r}")
        self.assertEqual("no_candidate", str(detail.get("code") or ""))
        self.assertTrue(list(detail.get("candidates") or []))
        self.assertIn("message", detail)

        def plain():
            bridge = importlib.import_module("tech_app.backend.services.cpq_bridge")
            raise bridge.BridgeRejected("参数不合法")

        with self.assertRaises(HTTPException) as other:
            main._bridge_call(plain)
        self.assertEqual(400, other.exception.status_code,
                         "其它业务拒绝必须保持既有 400 口径")
        self.assertIsInstance(other.exception.detail, str,
                              "其它业务拒绝的响应形状不许变（仍是字符串）")

    def test_c4_frontend_api_keeps_code_and_candidates(self):
        # 只要求"错误对象上带这三个字段"，不限定写在 api() 还是 apiError() 里。
        text = "\n".join(filter(None, (js_function(WORKFLOW_JS, "api"),
                                       js_function(WORKFLOW_JS, "apiError")))) \
            or read(WORKFLOW_JS)
        for key in ("code", "candidates", "status"):
            self.assertRegex(text, r"\.%s\s*=" % key,
                             f"api() 抛出的错误对象必须带 {key}（现在只抛 new Error(文案)，"
                             "report-publish-result.js 读的 error.code 恒为 undefined）")

    def test_c6_cost_and_report_paths_share_the_same_contract(self):
        """4.1–4.3 的成本回传与 5.3 的报告回传是两条不同的 service，出口契约必须一致。"""
        from fastapi import HTTPException
        main = importlib.import_module("tech_app.backend.main")
        cost_flow = importlib.import_module("tech_app.backend.services.cost_flow")
        report_flow = importlib.import_module("tech_app.backend.services.report_workflow")
        candidates = [{"quote_session_id": "qs-9", "card_id": 7,
                       "linked_by": "task", "title": "原报价卡片"}]

        def conflict():
            def boom():
                raise make_conflict("no_candidate", candidates)
            return boom

        for module, wrapper in ((cost_flow, cost_flow.bridge_call),
                                (report_flow, report_flow._bridge_call)):
            with self.assertRaises(Exception) as caught:
                wrapper(conflict())
            err = caught.exception
            self.assertEqual("no_candidate", str(getattr(err, "code", "") or ""),
                             f"{module.__name__} 的业务错误必须带 code（不然 FastAPI 层"
                             "拿不到落点冲突，界面弹不出恢复框）")
            self.assertTrue(list(getattr(err, "candidates", []) or []),
                            f"{module.__name__} 的业务错误必须带 candidates")

        for name in ("_cost_flow", "_report_flow"):
            with self.assertRaises(HTTPException) as caught:
                getattr(main, name)(conflict())
            exc = caught.exception
            self.assertEqual(409, exc.status_code, f"main.{name} 必须把落点冲突翻成 409")
            self.assertIsInstance(exc.detail, dict,
                                  f"main.{name} 必须给出结构化 detail：{exc.detail!r}")
            self.assertEqual("no_candidate", str(exc.detail.get("code") or ""))
            self.assertTrue(list(exc.detail.get("candidates") or []))

    def test_c5_cost_to_process_with_clue_still_lands_on_the_card(self):
        """护栏：本批只补"出口"，不许改既有落点裁决（4 成本测算 → 工艺经理）。"""
        with H.Workbench() as wb:
            out = wb.return_process()
            self.assertIsInstance(out, dict)
            self.assertEqual(1, len(wb.cards()),
                             "带线索的 cost_to_process 必须仍落原报价卡片，不许新建卡片")
            rows = wb.handoffs()
            self.assertEqual(1, len(rows))
            self.assertEqual("cost_to_process", str(rows[0].get("handoff_kind") or ""))
            tasks = wb.rows("cpq_wf_task", task_kind="tech_cost_return")
            self.assertEqual(1, len(tasks), "成本结果复核任务必须照常建出来")


# ===========================================================================
# D. 5.3 回传销售的恢复通道
# ===========================================================================
class ReportToQuoteRecoveryTest(unittest.TestCase):
    def test_d1_report_quote_action_has_the_three_fields(self):
        fields = ann_field_names(class_node(MAIN_PY, "ReportQuoteAction"))
        must(fields, "main.py 里没有 ReportQuoteAction 模型")
        missing = [name for name in ("business_case_id", "create_new", "create_reason")
                   if name not in fields]
        self.assertEqual([], missing,
                         f"3.3 回传销售的入参缺 {missing} —— 用户没有地方写「新建原因」，"
                         "服务端的 no_candidate 提示永远无法满足")

    def test_d2_workflow_accepts_and_forwards_the_recovery_fields(self):
        node = func_node(REPORT_WORKFLOW_PY, "send_to_quote")
        must(node is not None, "report_workflow.send_to_quote 不存在")
        missing = [name for name in ("business_case_id", "create_new", "create_reason")
                   if name not in param_names(node)]
        self.assertEqual([], missing, f"send_to_quote 必须接受 {missing}")
        hops = calls(node, "report_handoff")
        must(hops, "report_workflow.send_to_quote 必须调用 cpq_bridge.report_handoff")
        forwarded = set()
        for call in hops:
            forwarded |= kwarg_names(call)
        self.assertTrue({"create_new", "create_reason"} <= forwarded,
                        "create_new / create_reason 必须透传给 cpq_bridge.report_handoff，"
                        f"否则服务端永远不会走「明确新建」路径：已传 {sorted(forwarded)}")

    def test_d3_route_forwards_the_body_fields(self):
        node = func_node(MAIN_PY, "send_process_report_to_quote")
        must(node is not None, "POST .../process-report/send-to-quote 的处理函数不存在")
        hops = calls(node, "_report_flow")
        must(hops, "该路由必须通过 _report_flow 调 services.report_workflow.send_to_quote")
        forwarded = set()
        for call in hops:
            forwarded |= kwarg_names(call)
        missing = [name for name in ("business_case_id", "create_new", "create_reason")
                   if name not in forwarded]
        self.assertEqual([], missing, f"路由没有把 body 的 {missing} 转发下去")

    def test_d4_return_body_carries_candidates_and_recovery(self):
        body = func_source(REPORT_WORKFLOW_PY, "send_to_quote")
        for key in ("business_case_id", "candidates", "recovery"):
            self.assertIn(f'"{key}"', body,
                          f"回传返回体必须带 {key}（与成本侧 cost_flow 对齐）："
                          "界面要靠它渲染「已认回 / 新建了哪张卡片、由谁在何时恢复的」")

    def test_d5_publish_page_offers_the_recovery_dialog(self):
        body = js_function(REPORT_PUBLISH_JS, "rpSendReportToSales")
        must(body, "report-publish-result.js 的 rpSendReportToSales() 不存在")
        self.assertIn("business_case_id", body,
                      "回传请求必须带上项目实例号（落点由它裁决）")
        for key in ("create_new", "create_reason"):
            self.assertIn(key, body,
                          f"发布页必须提供「新建报价卡片 + 填写原因」的确认交互（缺 {key}）——"
                          "否则服务端那句「请填写新建原因后重试」在界面上根本无法执行")
        self.assertIn("candidates", body,
                      "多候选时必须把候选列给用户选，不许替他挑也不许静默新建")


# ===========================================================================
# E. 历史项目一次性恢复
# ===========================================================================
class QuoteLinkRecoveryTest(unittest.TestCase):
    def _handler(self):
        found = {path: node for path, node in routes(MAIN_PY).items()
                 if path.endswith("/quote-link/recover")}
        must(found, "缺少 POST /api/projects/{project_id}/quote-link/recover（Spec §6）："
                    "历史项目没有一次性「选择已有报价 / 明确新建报价」的恢复出口")
        path = sorted(found)[0]
        return path, found[path]

    def test_e1_route_and_body_model(self):
        path, handler = self._handler()
        self.assertIn("POST", " " + path + " ", "恢复接口必须是 POST 路由")
        model = ""
        for arg in handler.args.args[1:]:
            if isinstance(arg.annotation, ast.Name):
                model = arg.annotation.id
                break
        must(model, "恢复接口必须用显式的入参模型，不接受匿名 dict")
        fields = ann_field_names(class_node(MAIN_PY, model))
        missing = [name for name in RECOVERY_FIELDS if name not in fields]
        self.assertEqual([], missing, f"{model} 缺字段 {missing}")

    def test_e2_recovery_only_writes_tech_side_facts(self):
        _path, handler = self._handler()
        body = "\n".join(read(MAIN_PY).splitlines()[handler.lineno - 1:handler.end_lineno])
        self.assertIn("save_business_case", body,
                      "恢复动作必须把线索写进技术项目 meta（store.save_business_case）")
        self.assertIn("store.audit", body, "恢复动作必须留痕")
        self.assertIn(RECOVERY_SENTINEL, body,
                      f"恢复审计的动作名必须是 {RECOVERY_SENTINEL}（验收脚本按它取证）")
        leaked = re.findall(r"\b(cpq_case_link|cpq_wf|create_card|ensure_quote_session)\b", body)
        self.assertEqual([], leaked,
                         "恢复接口只写技术侧事实：建/改报价卡片仍只能由回传命令"
                         f"带 create_new + create_reason 完成，不许在这里动手（发现 {leaked}）")


if __name__ == "__main__":
    unittest.main()
