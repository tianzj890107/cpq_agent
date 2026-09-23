"""红测：投影里每一步的 `status` / `completed` 必须与它自己的两个原因字段一致。

Spec：`docs/specs/tech-projection-step-state-must-agree-with-its-reasons.md`

现状缺口（2026-09-23 在 34 实测 + 本机进程内逐字复现，不是推断）：
  · 34 上新项目 `4b1213500624`（`酒盒.dwg`，需求 = `draft`）的投影里：
      2.1 图纸解析  status=generated  completed=**true**   blocked_reasons=["请先完成 1.1 创建需求"]
      1.2 确认需求  status=in_progress completed=false     missing_requirements=[]
      1.3 审核需求  status=in_progress completed=false     missing_requirements=[]
    前一条就是用户看到的"顺序全乱了"：图纸解析已经 8/8，却还挂着"请先完成 1.1"；
    后两条是需求还是草稿时就并排写"进行中"，一个字都不说缺什么。
  · 根因 1：`_rows_for()` 的前置循环对**每一行**都追加「请先完成 X …」，不看本行 `completed`；
  · 根因 2：`_judge()` 的 1.2 / 1.3 兜底分支是 `in_progress` + `missing=[]`。

纪律：进程内直调投影自己的判据（`wp._rows_for` / `wp._judge` / `wp._phases`），
不起服务、不发 HTTP、不连 PG / 34、不写业务数据。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.models.integration import IntegrationPlan  # noqa: E402
from tech_app.backend.models.ir import DesignIR, Part  # noqa: E402
from tech_app.backend.services import workflow_projection as wp  # noqa: E402

PID = "4b1213500624"

#: 34 上那份包装解析产物（2.1 靠它判完成，`## 447` 的口径），截断成 2 件。
PACKING_PARTS_DOC = {
    "engine_version": "packaging-parts/1",
    "parts": [{"part_code": "JWXR21-P01", "name": "底板"},
              {"part_code": "JWXR21-P02", "name": "灰板1"}],
    "stats": {"part_total": 28},
}

#: 投影主按钮认的角色码（角色表是唯一来源，不许在测试里写死角色名）。
WRITE_ROLE = (wp._role_names("2.1")[1] or ("",))[0]

#: 前置提示的固定前缀（`_rows_for()` 生成的那一句）。
PRIOR_PREFIX = "请先完成"


def facts(req_status: str = "draft", *, pack: bool = True) -> dict:
    """34 上那份形状：包装项目（IR 为空、解析产物在 packaging_*），需求单状态可指定。"""
    return {
        "requirement": ({"status": req_status} if req_status else {}),
        "ir": None,
        "plan": IntegrationPlan(project_id=PID),
        "review": None,
        "audit": [],
        "packaging_cad_ir": ({"engine_version": "packaging-semantics/1"} if pack else None),
        "packaging_parts": (PACKING_PARTS_DOC if pack else None),
    }


def ir_facts(n_parts: int = 2) -> dict:
    """技术侧 IR 项目：零件用真模型构造（`_ir_model()` 认的是同一个模型）。"""
    ir = DesignIR(device_name="酒盒", design_intent="复跑",
                  parts=[Part(part_id="P%d" % (i + 1), name="件%d" % (i + 1), quantity=1)
                         for i in range(n_parts)])
    out = facts("", pack=False)
    out["ir"] = ir.model_dump()
    return out


def row_of(rows, key: str) -> dict:
    for row in rows:
        if row.get("key") == key:
            return row
    raise AssertionError("投影里没有 %s 这一行" % key)


def prior_texts(row: dict):
    return [b for b in (row.get("blocked_reasons") or []) if str(b).startswith(PRIOR_PREFIX)]


class StepStateAgreesWithReasonsTest(unittest.TestCase):
    # ---------------- A 组（今天都是红的） ----------------

    def test_a1_completed_stage_must_not_carry_a_prior_blocker(self):
        rows = wp._rows_for(PID, facts("draft"), WRITE_ROLE)
        row = row_of(rows, "2.1")
        self.assertTrue(row.get("completed"),
                        "前提：这份 facts 里 2.1（图纸解析）已完成：%s" % row)
        self.assertEqual([], prior_texts(row),
                         "2.1 已经是 completed=true，就跨过了「请先完成 1.1」那道门，"
                         "不许再挂前置阻塞；今天挂着 %s" % (prior_texts(row),))

    def test_a2_no_completed_row_carries_a_prior_blocker(self):
        rows = wp._rows_for(PID, facts("draft"), WRITE_ROLE)
        bad = [(row["key"], prior_texts(row)) for row in rows
               if row.get("completed") and prior_texts(row)]
        self.assertEqual([], bad,
                         "任何 completed=true 的行都不许挂前置阻塞（Spec §2.1）；今天有：%s" % (bad,))

    def test_a3_draft_requirement_does_not_put_the_confirm_step_in_progress(self):
        row = wp._judge("1.2", PID, facts("draft"))
        self.assertEqual("not_started", row.get("status"),
                         "需求还是草稿时「确认需求」一步都没开始，不许报 in_progress；"
                         "今天回的是 %r" % (row.get("status"),))
        self.assertFalse(row.get("completed"))
        self.assertTrue(row.get("missing"),
                        "不能只说「没开始」却不说缺什么（Spec §2.2）")

    def test_a4_draft_requirement_does_not_put_the_review_step_in_progress(self):
        row = wp._judge("1.3", PID, facts("draft"))
        self.assertEqual("not_started", row.get("status"),
                         "需求还是草稿时「审核需求」一步都没开始，不许报 in_progress；"
                         "今天回的是 %r" % (row.get("status"),))
        self.assertFalse(row.get("completed"))
        self.assertTrue(row.get("missing"),
                        "不能只说「没开始」却不说缺什么（Spec §2.2）")

    def test_a5_review_step_is_not_in_progress_before_it_is_sent_for_review(self):
        row = wp._judge("1.3", PID, facts("pending_confirmation"))
        self.assertEqual("not_started", row.get("status"),
                         "需求只走到「待确认」、还没送审，审核一步不许报 in_progress；"
                         "今天回的是 %r" % (row.get("status"),))
        self.assertTrue(row.get("missing"),
                        "不能只说「没开始」却不说缺什么（Spec §2.2）")

    def test_a6_draft_rows_say_what_is_missing(self):
        rows = wp._rows_for(PID, facts("draft"), WRITE_ROLE)
        for key in ("1.2", "1.3"):
            row = row_of(rows, key)
            self.assertTrue(row.get("missing_requirements"),
                            "%s 的 missing_requirements 是空，用户看不出缺什么：%s" % (key, row))

    # ---------------- B 组：护栏（今天就是绿的，不许被改红） ----------------

    def test_b1_draft_keeps_the_create_step_in_progress(self):
        row = wp._judge("1.1", PID, facts("draft"))
        self.assertEqual("in_progress", row.get("status"),
                         "草稿时 1.1 仍是 in_progress（tech-unified-workflow-projection §5 表）")
        self.assertFalse(row.get("completed"), "草稿不能算 1.1 完成")

    def test_b2_completed_states_of_the_first_three_steps_are_unchanged(self):
        pending = wp._judge("1.1", PID, facts("pending_confirmation"))
        self.assertEqual(("confirmed", True), (pending.get("status"), pending.get("completed")))
        pending_review = facts("pending_review")
        self.assertEqual(True, wp._judge("1.1", PID, pending_review).get("completed"))
        self.assertEqual(True, wp._judge("1.2", PID, pending_review).get("completed"))
        approved = facts("approved")
        self.assertEqual(("approved", True),
                         (wp._judge("1.3", PID, approved).get("status"),
                          wp._judge("1.3", PID, approved).get("completed")))

    def test_b3_incomplete_rows_keep_the_prior_text(self):
        rows = wp._rows_for(PID, facts("draft"), WRITE_ROLE)
        self.assertIn("请先完成 1.1 创建需求", row_of(rows, "1.2").get("blocked_reasons") or [],
                      "未完成的行必须逐字保留前置提示（不许整段删掉）")
        rows2 = wp._rows_for(PID, facts("pending_confirmation"), WRITE_ROLE)
        self.assertIn("请先完成 1.2 确认需求", row_of(rows2, "1.3").get("blocked_reasons") or [],
                      "未完成的行必须逐字保留前置提示（不许整段删掉）")

    def test_b4_phase_aggregation_and_2_1_judgement_unchanged(self):
        rows = [
            {"key": "3.1", "completed": False, "status": "not_started"},
            {"key": "3.2", "completed": True, "status": "confirmed"},
            {"key": "3.3", "completed": False, "status": "generated"},
        ]
        phase3 = [p for p in wp._phases(rows) if p.get("no") == 3] or []
        if not phase3:
            self.skipTest("阶段表里没有第 3 阶段")
        self.assertEqual("not_started", phase3[0].get("status"),
                         "_phases() 的聚合规则（取第一个未完成子步的状态）不变")
        empty = wp._judge("1.1", PID, facts("", pack=False))
        self.assertEqual("not_started", empty.get("status"), "没有需求单时 1.1 仍是 not_started")
        no_pack = wp._judge("2.1", PID, facts("", pack=False))
        self.assertEqual(("not_started", ["图纸还没有解析"]),
                         (no_pack.get("status"), no_pack.get("missing")),
                         "没有解析产物时 2.1 的口径逐字不变")
        ir = wp._judge("2.1", PID, ir_facts())
        self.assertEqual(("generated", True), (ir.get("status"), ir.get("completed")),
                         "技术侧 IR 项目有零件时 2.1 仍是 generated/completed")


if __name__ == "__main__":
    unittest.main()
