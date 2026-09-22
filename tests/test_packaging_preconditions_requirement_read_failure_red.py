"""红测：`preconditions()` 里"读不到需求单"不许说成"需求单不存在"。

Spec：`docs/specs/packaging-preconditions-requirement-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `packaging_drawing_flow/__init__.py:474-476` 把 `store.load_requirement()` 的异常吞成
    `requirement = None`；
  · `:478-482` 于是给 `REQUIREMENT_DRAFT_MISSING`（`severity="blocking"`），文案是
    `model.py:42` 的"需求单不存在，请先创建需求草稿（缺前置条件，重试不会成功）"
    —— 用户被劝去建一张重复的需求草稿；
  · 这条前置条件会挂在 `GET /api/projects/{pid}/drawing-flow` 上，并被 2.1 左栏
    `app.js:1720 packagingPartsEmptyText()` 原样渲染成 `[code] message → action`。

纪律：只读源码 + 打桩 `store.load_requirement`；不连 PG / SQLite 生产库、不发 HTTP、
不写任何文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_drawing_flow as flow          # noqa: E402
from tech_app.backend.services.packaging_drawing_flow import model           # noqa: E402

PID = "testpid00001"


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, old in reversed(self.saved):
            setattr(owner, name, old)
        return False


def _preconditions(requirement, *, error=None):
    def loader(project_id):
        if error is not None:
            raise error
        return requirement

    with _Patch((flow.store, "load_requirement", loader)):
        return flow.preconditions(PID)


def _entry(items, code):
    rows = [item for item in (items or []) if str(item.get("code")) == code]
    return rows[0] if rows else {}


# --------------------------------------------------------------------------- #
# Q 组：三态（读到了 / 真的没有 / 读不到）必须两两可分
# --------------------------------------------------------------------------- #
class QRequirementReadFailure(unittest.TestCase):
    def test_q1_unreadable_requirement_is_not_a_missing_draft(self):
        items = _preconditions(None, error=RuntimeError("meta store down"))
        codes = [str(item.get("code")) for item in items]
        self.assertIn("REQUIREMENT_UNREADABLE", codes,
                      "存储通道读不到时必须给一条**新**码（Spec §2.1）：今天给的是"
                      "REQUIREMENT_DRAFT_MISSING —— 用户会去建一张重复的需求草稿")
        self.assertNotIn("REQUIREMENT_DRAFT_MISSING", codes,
                         "'读不到'与'真的没有'不许给同一条码")

    def test_q2_unreadable_message_must_not_claim_absence(self):
        items = _preconditions(None, error=RuntimeError("meta store down"))
        entry = _entry(items, "REQUIREMENT_UNREADABLE")
        message = str(entry.get("message") or "")
        self.assertIn("RuntimeError", message, "文案要说得出是哪一类失败（异常类名）")
        self.assertIn("重试", message, "读不到是可重试的，文案要给出重试")
        self.assertNotIn("不存在", message,
                         "'暂时读不到'不许说成'不存在'（那是给真的没有需求单的说法）")
        self.assertTrue(str(entry.get("action") or ""), "仍要给下一步动作")

    def test_q3_every_entry_discloses_whether_it_was_readable(self):
        unreadable = _preconditions(None, error=RuntimeError("meta store down"))
        self.assertEqual("requirement_unreadable",
                         (_entry(unreadable, "REQUIREMENT_UNREADABLE").get("unavailable") or {})
                         .get("code"),
                         "读失败那条要把披露码写进 `unavailable`")
        really_missing = _preconditions(None)
        self.assertEqual({}, _entry(really_missing, "REQUIREMENT_DRAFT_MISSING").get("unavailable"),
                         "真的没有需求单时该键必须是空的（键必须存在）")
        not_editable = _preconditions({"status": "approved", "data": {}})
        self.assertEqual({}, _entry(not_editable, "REQUIREMENT_NOT_EDITABLE").get("unavailable"),
                         "不可编辑那条同理（键必须存在）")


# --------------------------------------------------------------------------- #
# Q 组（护栏）：既有前置条件口径不许被本批改掉
# --------------------------------------------------------------------------- #
class QExistingPreconditionsUnchanged(unittest.TestCase):
    def test_q4_really_missing_draft_keeps_its_exact_wording(self):
        entry = _entry(_preconditions(None), "REQUIREMENT_DRAFT_MISSING")
        spec = model.PRECONDITION_BLOCKERS["REQUIREMENT_DRAFT_MISSING"]
        self.assertEqual("blocking", entry.get("severity"))
        self.assertEqual(str(spec["message"]), str(entry.get("message")),
                         "既有四键里 message 逐字不变")
        self.assertEqual(str(spec["action"]), str(entry.get("action")),
                         "既有四键里 action 逐字不变")

    def test_q5_editable_requirement_has_no_precondition(self):
        self.assertEqual([], _preconditions({"status": "draft", "data": {}}),
                         "可编辑的需求单不该产生任何前置条件")

    def test_q6_non_editable_requirement_is_still_reported(self):
        items = _preconditions({"status": "approved", "data": {}})
        entry = _entry(items, "REQUIREMENT_NOT_EDITABLE")
        self.assertTrue(entry, "已提交的需求单照旧要报不可改写")
        self.assertEqual("blocking", entry.get("severity"))
        self.assertIn("approved", str(entry.get("message") or ""),
                      "文案照旧带当前状态")

    def test_q7_blank_project_id_returns_empty(self):
        with _Patch((flow.store, "load_requirement", lambda project_id: None)):
            self.assertEqual([], flow.preconditions(""),
                             "没有 project_id 时照旧直接给 []，不许去读存储")

    def test_q8_read_failure_never_raises(self):
        items = _preconditions(None, error=RuntimeError("meta store down"))
        self.assertIsInstance(items, list,
                              "读需求单抛异常时 preconditions() 照旧返回一份可判的清单")

    def test_q9_drawing_flow_route_still_carries_preconditions(self):
        source = (ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"preconditions": packaging_drawing_flow.preconditions(pid)', source,
                      "读接口照旧带 preconditions（本批不改路由形状）")


if __name__ == "__main__":
    unittest.main()
