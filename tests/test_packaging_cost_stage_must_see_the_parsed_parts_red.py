"""红测：包装项目 2.1 解析出的零件，成本阶段（4.1/4.2/4.3）必须看得见（Spec §2/§4）。

Spec：`docs/specs/packaging-cost-stage-must-see-the-parsed-parts.md`

现状缺口（2026-09-23 在 34 实测 + 本机读源码，不是推断）：
  · 34 上项目 `8131f6d29d99`（`酒盒.dwg`）2.1 = `generated/completed`，
    `GET /requirement/packaging-parts` 有 263 件（`part_total=263`）；
  · 同一份投影里 4.1 = `not_started` + `missing=["还没有可测算的零件"]`、
    4.2 = `in_progress` + `missing=["整机（组装）成本还没有测算"]`、4.3 = `not_started`；
  · 根因：`_judge_cost()` → `_cost_data()` → `cost_review.summarize(project_id, ir, plan)`
    的零件来源仍是 `ir.parts`（`store.load_ir`），包装项目的零件在 `packaging_parts`
    文档里、IR 是空的（`## 447` 只改了 2.1）。
  · 4.2 的前置判断是 `if not data:`（summarize 永远返回字典）→ 一件零件都没有时也报「进行中」。

纪律：进程内直调投影自己的判据（纯函数 + 构造好的 facts / IntegrationPlan），
不起服务、不发 HTTP、不连 PG / SQLite、不写业务数据。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import sys
import unittest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.models.integration import IntegrationPlan  # noqa: E402
from tech_app.backend.models.ir import DesignIR, Part  # noqa: E402
from tech_app.backend.services import workflow_projection as wp  # noqa: E402

PID = "8131f6d29d99"

#: 34 上那份包装零件文档的形状（截断成 3 件；`stats.part_total` 保留真实的 263）。
PACKING_PARTS = {
    "engine_version": "packaging-parts/1",
    "parts": [
        {"part_code": "DWG-P01", "name": "图纸零件 P01", "role": "panel"},
        {"part_code": "DWG-P02", "name": "图纸零件 P02", "role": "panel"},
        {"part_code": "DWG-P03", "name": "图纸零件 P03", "role": "unknown"},
    ],
    "stats": {"part_total": 263, "closed_total": 134, "open_total": 129},
}

#: 包装项目：解析产物在 `packaging_cad_ir` / `packaging_parts`，IR 为空（34 实测 `ir = {}`）。
PKG_FACTS = {
    "requirement": {"status": "approved"},
    "ir": None,
    "plan": IntegrationPlan(project_id=PID),
    "review": None,
    "audit": [],
    "packaging_cad_ir": {"engine_version": "packaging-semantics/1", "parts": PACKING_PARTS["parts"]},
    "packaging_parts": PACKING_PARTS,
}

def ir_with_one_part() -> dict:
    """技术侧 IR 项目：一件零件（字段用真模型构造，`_ir_model()` 认的是同一个模型）。"""
    return DesignIR(device_name="酒盒", design_intent="复跑",
                    parts=[Part(part_id="P1", name="件1", quantity=1)]).model_dump()


#: 真空项目：没有 IR 零件，也没有包装解析产物，成本阶段什么都没开始。
EMPTY_FACTS = {
    "requirement": {"status": "approved"},
    "ir": None,
    "plan": IntegrationPlan(project_id="empty-project"),
    "review": None,
    "audit": [],
}


def judge(key: str, facts: dict) -> dict:
    return wp._judge(key, str(facts) and PID, facts)


class PackagingCostStagePartsTest(unittest.TestCase):
    # ---------------- A 组（今天都是红的） ----------------

    def test_a1_cost_stage_sees_packaging_parts(self):
        row = judge("4.1", PKG_FACTS)
        self.assertNotEqual(row.get("status"), "not_started",
                            "2.1 明明有 %d 件零件（IR 为空是包装项目的正常形态），"
                            "4.1 不许判未开始；今天回的是 %s / %s"
                            % (PACKING_PARTS["stats"]["part_total"], row.get("status"),
                               row.get("missing")))

    def test_a2_no_parts_message_is_gone_for_packaging_project(self):
        row = judge("4.1", PKG_FACTS)
        missing = " ".join(str(x) for x in (row.get("missing") or []))
        self.assertNotIn("还没有可测算的零件", missing,
                         "有 263 件零件时不许说「还没有可测算的零件」；今天回的是 %r" % (missing,))

    def test_a3_assembly_stage_is_not_in_progress_before_anything_started(self):
        row41 = judge("4.1", EMPTY_FACTS)
        row42 = judge("4.2", EMPTY_FACTS)
        self.assertEqual(row41.get("status"), "not_started", "一件零件都没有时 4.1 是未开始（护栏）")
        self.assertEqual(row42.get("status"), "not_started",
                         "同一阶段里 4.1 说「还没有可测算的零件」时，4.2 不许报「进行中」"
                         "（「整机（组装）成本还没有测算」在什么都没算时是假话）；"
                         "今天 4.2 回的是 %s / %s" % (row42.get("status"), row42.get("missing")))

    # ---------------- B 组：护栏（今天就是绿的，不许被改红） ----------------

    def test_b1_drawing_stage_still_accepts_packaging_parse_products(self):
        row = judge("2.1", PKG_FACTS)
        self.assertTrue(row.get("completed"), "2.1 仍要认包装解析产物（## 447 的口径）")
        self.assertEqual(row.get("status"), "generated")

    def test_b2_ir_project_with_parts_but_no_cost_stays_in_progress(self):
        facts = dict(PKG_FACTS)
        facts["packaging_cad_ir"] = None
        facts["packaging_parts"] = None
        facts["ir"] = ir_with_one_part()
        row = judge("4.1", facts)
        self.assertEqual(row.get("status"), "in_progress")
        self.assertTrue(any("P1" in str(x) for x in (row.get("missing") or [])),
                        "未测算的零件号要点名；今天回的是 %r" % (row.get("missing"),))

    def test_b3_ir_project_without_parts_stays_not_started(self):
        facts = dict(PKG_FACTS)
        facts["packaging_cad_ir"] = None
        facts["packaging_parts"] = None
        facts["ir"] = {"device_name": "x", "design_intent": "y", "parts": []}
        self.assertEqual(judge("4.1", facts).get("status"), "not_started")
        self.assertEqual(judge("4.3", facts).get("status"), "not_started")

    def test_b4_phase_rule_unchanged(self):
        rows = [
            {"key": "4.1", "completed": False, "status": "not_started"},
            {"key": "4.2", "completed": True, "status": "confirmed"},
            {"key": "4.3", "completed": False, "status": "in_progress"},
        ]
        phases = wp._phases(rows)
        phase4 = [p for p in phases if p.get("no") == 4] or []
        if not phase4:
            self.skipTest("阶段表里没有第 4 阶段")
        self.assertFalse(phase4[0].get("completed"))

    def test_b5_assembly_in_progress_when_whole_machine_cost_missing(self):
        # 4.2 的「真在做」形态：零件在（成本阶段确实有东西可做）、整机成本仍缺。
        facts = dict(PKG_FACTS)
        facts["packaging_cad_ir"] = None
        facts["packaging_parts"] = None
        facts["ir"] = ir_with_one_part()
        row = judge("4.2", facts)
        self.assertEqual(row.get("status"), "in_progress")
        self.assertEqual(row.get("missing"), ["整机（组装）成本还没有测算"])


if __name__ == "__main__":
    unittest.main()
