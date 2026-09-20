"""红测：1.2 确认 / 1.3 审核的主按钮命名与「意见非必填」。

用户要求：
- 确认需求的主按钮是「通过确认」，并且不强制必须要有意见。
- 审核的主按钮是「提交审核意见」，也不强制必须要有审核意见。

现状缺口：两个页面都用「意见为空」当硬闸门（confirm-page 的 cfAct、
review-page 的 rrSubmit 与看板动作 submitRequirementReview），页内提示写「必填」，
页内主按钮文案是「✓ 通过」/「➤ 提交」，与左侧主按钮不一致。

红线：后端接口、状态机闸门、角色校验、审计留痕、看板动作注册名一个都不能少。
"""
from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8", errors="replace").replace("\x00", "")


CONFIRM = read("tech_app/frontend/requirement-confirm-page.js")
REVIEW = read("tech_app/frontend/requirement-review-page.js")
MAIN = read("tech_app/backend/main.py")
SERVICE = read("tech_app/backend/services/requirement_service.py")
MODELS = read("tech_app/backend/models/workflow.py")


def block_from(text: str, marker: str) -> str:
    """从 marker 起按大括号配平取块；跳过注释与字符串，避免模板字面量里的花括号干扰。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    tail = idx + len(marker) - 1
    if text[tail:tail + 1] == "{":
        brace = tail
    else:
        brace = text.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
        i += 1
    return ""


class ConfirmPrimaryAndOptionalNoteContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cf_act = block_from(CONFIRM, "async function cfAct(")
        cls.confirm_action = block_from(CONFIRM, "confirmRequirement: {")
        cls.return_action = block_from(CONFIRM, "returnRequirementDraft: {")

    def test_blocks_are_found(self):
        for name, block in (("cfAct", self.cf_act), ("confirmRequirement", self.confirm_action),
                            ("returnRequirementDraft", self.return_action)):
            with self.subTest(name=name):
                self.assertTrue(block, f"找不到 {name} 的实现块")

    # ------------------------------------------------ R1 确认需求
    def test_empty_note_is_no_longer_a_blocking_gate(self):
        self.assertNotIn("missing-comment", self.cf_act,
                         "空意见不得再被当成提交失败（1.2 意见为选填）")
        self.assertNotIn("请填写提交意见", CONFIRM, "「请填写提交意见」的硬拦已删除")
        self.assertIn("comment", self.cf_act, "仍要把 comment 字段原样交给后端（可为空串）")

    def test_state_gate_and_既有接口_are_kept(self):
        self.assertIn("pending_confirmation", self.cf_act,
                      "确认动作仍必须先校验需求处于待确认状态")
        self.assertIn("/requirement/confirm", self.cf_act)
        self.assertIn("return-to-draft", self.cf_act)
        self.assertIn("cfPublishTaskEvent", self.cf_act, "任务进度上报不得丢失")

    def test_hint_and_primary_button_name_align_with_left_toolbar(self):
        self.assertNotIn("必填", CONFIRM, "确认意见已改为选填，提示不得再写「必填」")
        self.assertIn("选填", CONFIRM, "提示需明确说明意见为选填")
        self.assertRegex(CONFIRM, r'id="confirmPass"[^>]*>\s*[^<]*通过确认\s*<',
                         "页内主按钮文案要和左侧主按钮一致：「通过确认」")

    def test_left_toolbar_primary_stays_the_confirm_action(self):
        self.assertRegex(self.confirm_action, r"label:\s*'[^']*通过确认'")
        self.assertRegex(self.confirm_action, r"role:\s*'primary'")
        self.assertIn("order: 10", self.confirm_action)
        self.assertRegex(self.return_action, r"role:\s*'aux'")
        self.assertIn("order: 20", self.return_action)


class ReviewPrimaryAndOptionalNoteContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rr_submit = block_from(REVIEW, "async function rrSubmit(")
        cls.review_action = block_from(REVIEW, "submitRequirementReview: {")
        cls.rr_bind = block_from(REVIEW, "function rrBind(")

    def test_blocks_are_found(self):
        for name, block in (("rrSubmit", self.rr_submit), ("submitRequirementReview", self.review_action),
                            ("rrBind", self.rr_bind)):
            with self.subTest(name=name):
                self.assertTrue(block, f"找不到 {name} 的实现块")

    def test_empty_review_note_is_no_longer_a_blocking_gate(self):
        self.assertNotIn("missing-comment", REVIEW, "空审核意见不得再被当成提交失败")
        self.assertIn("comment", self.rr_submit, "仍要把 comment 原样交给后端（可为空串）")
        self.assertIn("decision", self.rr_submit, "审核结论仍由单选项决定")

    def test_review_keeps_decision_and_status_gates(self):
        self.assertIn("pending_review", self.rr_submit,
                      "审核动作仍必须先校验需求处于待审核状态")
        self.assertIn("/requirement/review", self.rr_submit)
        self.assertIn("invalid-status", self.review_action)
        self.assertIn("no-selection", self.review_action,
                      "未选择审核结果仍要给出可读提示，不能默认通过")
        self.assertIn("busy", self.review_action)

    def test_hint_and_primary_button_name_align_with_left_toolbar(self):
        self.assertNotIn("必填", REVIEW, "审核意见已改为选填，提示不得再写「必填」")
        self.assertRegex(REVIEW, r'id="submitReview"[^>]*>\s*[^<]*提交审核意见\s*<',
                         "页内主按钮文案要和左侧主按钮一致：「提交审核意见」")

    def test_left_toolbar_primary_stays_the_review_action(self):
        self.assertRegex(self.review_action, r"label:\s*'提交审核意见'")
        self.assertRegex(self.review_action, r"role:\s*'primary'")
        self.assertIn("order: 10", self.review_action)

    def test_apply_review_note_and_history_are_kept(self):
        self.assertIn("applyReviewNote", REVIEW)
        self.assertIn("rrHistoryHtml", REVIEW)
        self.assertIn("rrPublishTaskEvent", REVIEW)


class BackendApprovalBoundariesUnchanged(unittest.TestCase):
    def test_routes_still_exist(self):
        for route in ("/requirement/confirm", "/requirement/return-to-draft", "/requirement/review"):
            with self.subTest(route=route):
                self.assertIn(route, MAIN)

    def test_service_still_validates_role_and_decision(self):
        self.assertIn("def confirm_requirement", SERVICE)
        self.assertIn("def review_requirement", SERVICE)
        review = SERVICE[SERVICE.find("def review_requirement"):]
        self.assertIn("approve", review)
        self.assertIn("reject", review)

    def test_comment_stays_optional_on_the_model(self):
        self.assertRegex(MODELS, r"comment:\s*str\s*=\s*[\"']{2}")


if __name__ == "__main__":
    unittest.main()
