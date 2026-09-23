"""红测：1.1 提交确认 / 1.2 确认必须先挡住"图纸还没解析"（顺序门禁）。

Spec：`docs/specs/packaging-requirement-confirm-order-guard.md`

现状缺口（34 实测，2026-09-22，项目 `7267eff7d68a`）：
  · 先走 1.1/1.2/1.3 再跑 `drawing-flow` → 第 8 步 `field_write` 必
    `blocked / REQUIREMENT_NOT_EDITABLE`（7/8），提示"请先退回草稿"；
    退回草稿重跑才 8/8 —— 每次改完需求重跑图纸解析都要再踩一遍；
  · `requirement_service.drawing_parse_prerequisite()` 只被 1.3 `review_requirement` 用，
    `submit_requirement_confirmation()`（1.1）与 `confirm_requirement()`（1.2）里一次都没有调用。

纪律：只读源码 + 打桩 store，不连 PG、不发 HTTP、不写任何文件。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import copy
import json
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import requirement_service as RS  # noqa: E402

SRC = (ROOT / "tech_app/backend/services/requirement_service.py").read_text(encoding="utf-8")
CONFIRM_PAGE = (ROOT / "tech_app/frontend/requirement-confirm-page.js").read_text(encoding="utf-8")
REVIEW_PAGE = (ROOT / "tech_app/frontend/requirement-review-page.js").read_text(encoding="utf-8")

CODE = "REQUIREMENT_DRAWING_NOT_PARSED"
SUBMIT_WAIVED_AUDIT = "workflow:requirement_submitted_waived"
DRAFT = {"project_id": "p-order", "requirement_no": "REQ-PORDER", "title": "顺序门禁",
         "status": "draft", "data": {"industry": "packaging"}}
PENDING = {**DRAFT, "status": "pending_confirmation"}


def func_body(name: str) -> str:
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(SRC.splitlines()[node.lineno - 1:node.end_lineno])
    raise AssertionError("requirement_service.%s() 不见了（Spec §3）" % name)


def js_func_body(source: str, name: str) -> str:
    """取页面里的一个函数体：`function <name>(` 那行起，到下一个顶层 function 声明之前。"""
    marker = "function %s(" % name
    idx = source.find(marker)
    if idx < 0:
        raise AssertionError("%s() 在页面里不见了（Spec §2.2）" % name)
    head = source.rfind("\n", 0, idx) + 1
    tail = source[idx + len(marker):]
    nxt = re.search(r"\n(?:async\s+)?function\s", tail)
    end = len(source) if nxt is None else idx + len(marker) + nxt.start()
    return source[head:end]


def js_status_sets(source: str):
    """页面里所有「名字里带 RETURN 的数组常量」：(常量名, 字面量字符串)。"""
    return [(match.group(1), match.group(2))
            for match in re.finditer(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(\[[^\[\]]*\])",
                                     source)
            if re.search(r"RETURN", match.group(1), re.IGNORECASE)]


#: 后端「允许退回草稿」的同一份口径（requirement_service.RETURNABLE_TO_DRAFT_STATUSES）。
RETURNABLE = ("pending_confirmation", "pending_review", "approved")


def blocked_prerequisite(project_id=""):
    """图纸还没解析（1.3 已经在用的那份判据，在这里打桩成同一形状）。"""
    return {"required": True, "done": False, "suffix": ".dwg", "industry": "packaging",
            "code": CODE,
            "message": "图纸还没解析完（酒盒.dwg）：请先跑「一键解析图纸」再审批 —— "
                       "没有零件清单，后面 2.2/2.3 与报告都拿不到数据"}


def parsed_prerequisite(project_id=""):
    return {"required": True, "done": True, "suffix": ".dwg", "industry": "packaging",
            "code": "", "message": ""}


class FakeStore:
    def __init__(self, doc):
        self.doc = copy.deepcopy(doc)
        self.calls = []

    def load_requirement(self, project_id):
        return copy.deepcopy(self.doc)

    def save_requirement(self, project_id, doc, author=""):
        self.calls.append(("save", doc.get("status")))
        self.doc = copy.deepcopy(doc)

    def audit(self, project_id, action, payload=None):
        self.calls.append(("audit", action))

    def load_meta(self, project_id):
        return {"source_filename": "酒盒.dwg"}

    def load_ir(self, project_id):
        return {}

    def saved_statuses(self):
        return [row[1] for row in self.calls if row[0] == "save"]


class ASubmitConfirmationGuard(unittest.TestCase):
    def test_a1_submit_is_blocked_when_drawing_not_parsed(self):
        fake = FakeStore(DRAFT)
        with mock.patch.object(RS, "store", fake), \
             mock.patch.object(RS, "drawing_parse_prerequisite", blocked_prerequisite):
            with self.assertRaises(RS.RequirementSaveError) as ctx:
                RS.submit_requirement_confirmation("p-order", {"username": "PE1"})
        self.assertEqual(int(ctx.exception.status_code), 409,
                         "1.1 必须先挡在图纸解析之前（Spec §2.3）")
        self.assertEqual(ctx.exception.stable_error_code, CODE,
                         "错误码必须与 1.3 那条一致（Spec §2.3）")
        self.assertEqual(fake.saved_statuses(), [],
                         "被挡住时不许落盘（Spec §2.3）")

    def test_a2_submit_unchanged_when_drawing_is_parsed(self):
        fake = FakeStore(DRAFT)
        with mock.patch.object(RS, "store", fake), \
             mock.patch.object(RS, "drawing_parse_prerequisite", parsed_prerequisite):
            out = RS.submit_requirement_confirmation("p-order", {"username": "PE1"})
        self.assertEqual(out["status"], "pending_confirmation", "解析完成时行为逐字不变（Spec §2.2）")
        self.assertEqual(fake.saved_statuses(), ["pending_confirmation"])


class BConfirmGuard(unittest.TestCase):
    def test_b1_confirm_is_blocked_when_drawing_not_parsed(self):
        fake = FakeStore(PENDING)
        with mock.patch.object(RS, "store", fake), \
             mock.patch.object(RS, "drawing_parse_prerequisite", blocked_prerequisite):
            with self.assertRaises(RS.RequirementSaveError) as ctx:
                RS.confirm_requirement("p-order", {"username": "PE1"})
        self.assertEqual(int(ctx.exception.status_code), 409, "1.2 也必须挡住（Spec §2.3）")
        self.assertEqual(ctx.exception.stable_error_code, CODE)
        self.assertEqual(fake.saved_statuses(), [], "被挡住时不许落盘（Spec §2.3）")

    def test_b2_confirm_unchanged_when_drawing_is_parsed(self):
        fake = FakeStore(PENDING)
        with mock.patch.object(RS, "store", fake), \
             mock.patch.object(RS, "drawing_parse_prerequisite", parsed_prerequisite):
            out = RS.confirm_requirement("p-order", {"username": "PE1"})
        self.assertEqual(out["status"], "pending_review", "解析完成时行为逐字不变（Spec §2.2）")
        self.assertEqual(fake.saved_statuses(), ["pending_review"])


class CSharedGuard(unittest.TestCase):
    def test_c1_both_entry_points_use_the_same_prerequisite(self):
        for name in ("submit_requirement_confirmation", "confirm_requirement"):
            self.assertIn("drawing_parse_prerequisite(", func_body(name),
                          "%s() 必须调用同一份 drawing_parse_prerequisite()，"
                          "不许只修一处（Spec §2.1 / §2.5）" % name)

    def test_c2_waiver_releases_and_leaves_a_trace(self):
        fake = FakeStore(DRAFT)
        with mock.patch.object(RS, "store", fake), \
             mock.patch.object(RS, "drawing_parse_prerequisite", blocked_prerequisite), \
             mock.patch.object(RS, "requirement_gaps", lambda *a, **k: {"keys": [], "items": []}), \
             mock.patch.object(RS, "record_requirement_waiver", lambda *a, **k: {}), \
             mock.patch.object(RS, "_waiver_reason", lambda waiver: "带缺口继续"):
            out = RS.submit_requirement_confirmation("p-order", {"username": "PE1"},
                                                    waiver={"reason": "带缺口继续"})
        self.assertEqual(out["status"], "pending_confirmation", "带 waiver 才放行（Spec §2.4）")
        self.assertIn(("audit", SUBMIT_WAIVED_AUDIT), fake.calls,
                      "放行必须留痕 %s（Spec §2.4）" % SUBMIT_WAIVED_AUDIT)


class DNoRegression(unittest.TestCase):
    def test_d1_review_guard_still_there(self):
        body = func_body("review_requirement")
        self.assertIn("drawing_parse_prerequisite(", body, "1.3 既有拦截逐字不变（Spec §2.5）")
        self.assertIn("waiver", body, "1.3 的 waiver 放行仍在（Spec §2.5）")

    def test_d2_not_required_never_blocks(self):
        fake = FakeStore(DRAFT)
        with mock.patch.object(RS, "store", fake), \
             mock.patch.object(RS, "drawing_parse_prerequisite",
                               lambda pid: {"required": False, "done": True, "suffix": ".png",
                                            "industry": "semiconductor", "code": "", "message": ""}):
            out = RS.submit_requirement_confirmation("p-order", {"username": "PE1"})
        self.assertEqual(out["status"], "pending_confirmation", "非包装/非 dwg 一步都不拦（Spec §2.2）")


class EReturnToDraftEscape(unittest.TestCase):
    """§2.2 退路：批准之后前端还得有按钮能退回草稿（今天一个都没有）。"""

    def test_e1_returnable_statuses_are_declared_and_wide_enough(self):
        sets = js_status_sets(CONFIRM_PAGE)
        self.assertTrue(sets, "1.2 页必须把「退回可用状态」写成一个具名常量，"
                              "并且与后端 RETURNABLE_TO_DRAFT_STATUSES 同一份口径（Spec §2.2 第 7 条）")
        wide = [name for name, literal in sets
                if all(("'%s'" % status) in literal or ('"%s"' % status) in literal
                       for status in RETURNABLE)]
        self.assertTrue(wide, "退回状态集必须同时含 %s，不许比后端更窄（Spec §2.2 第 7 条）"
                        % "、".join(RETURNABLE))
        body = js_func_body(CONFIRM_PAGE, "cfAct")
        self.assertTrue(any(name in body for name in wide),
                        "cfAct() 里必须真的按这份状态集判退路（Spec §2.2 第 7/8 条）")

    def test_e2_status_guard_is_per_action_not_one_size_fits_all(self):
        body = js_func_body(CONFIRM_PAGE, "cfAct")
        guard = re.search(r"status\s*!==\s*'pending_confirmation'", body)
        if guard is None:
            return  # 已经按动作分别判前置，这条自动满足（Spec §2.2 第 8 条）
        branch = re.search(r"kind\s*===\s*'confirm'", body)
        self.assertIsNotNone(branch, "带状态前置的动作必须逐支判 kind（Spec §2.2 第 8 条）")
        self.assertLess(branch.start(), guard.start(),
                        "以 pending_confirmation 挡掉一切的那句必须落在 kind === 'confirm' "
                        "那一支之后，否则 approved 之后「驳回」可见可点却什么都不发生"
                        "（Spec §2.2 第 8 条）")

    def test_e3_review_page_also_offers_the_way_back(self):
        self.assertIn("return-to-draft", REVIEW_PAGE,
                      "1.3 审核通过之后人就在审核页，那里也必须能退回草稿，"
                      "不能只留一句「当前需求不在待审核状态」（Spec §2.2 第 9 条）")

    def test_e4_backend_still_accepts_approved_and_is_idempotent(self):
        """护栏：后端这一侧今天就是对的，实现只需要别把它改窄。"""
        self.assertEqual(tuple(RS.RETURNABLE_TO_DRAFT_STATUSES), RETURNABLE,
                         "退回放行集合不许改窄（Spec §2.2 第 10 条）")
        fake = FakeStore({**DRAFT, "status": "approved"})
        with mock.patch.object(RS, "store", fake):
            out = RS.return_requirement_to_draft("p-order", {"username": "PE1"})
        self.assertEqual(out["status"], "draft", "approved 必须退得回草稿（Spec §2.2 第 10 条）")
        self.assertEqual(fake.saved_statuses(), ["draft"])
        already = FakeStore(DRAFT)
        with mock.patch.object(RS, "store", already):
            out2 = RS.return_requirement_to_draft("p-order", {"username": "PE1"})
        self.assertEqual(out2["status"], "draft")
        self.assertEqual(already.saved_statuses(), [], "已经是草稿就幂等、不写库（Spec §2.2 第 10 条）")

    def test_e5_draft_status_is_not_added_to_editable_statuses(self):
        """不许为了修好退路把 approved 塞进可编辑闭集（Spec §4）。"""
        self.assertEqual(tuple(RS.EDITABLE_STATUSES), ("draft", "rejected"),
                         "编辑口径不许放宽：退回是动作，编辑是动作之后的状态（Spec §4）")


if __name__ == "__main__":
    unittest.main()
