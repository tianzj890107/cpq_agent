"""红测：3.1「整合图纸」必须认自己产出的分析结果（参数推荐 / 组装工艺）（Spec §2/§4）。

Spec：`docs/specs/packaging-integration-stage-must-count-its-own-analysis.md`

现状缺口（2026-09-23 在 34 实测 + 本机读源码，不是推断）：
  · 34 上项目 `8131f6d29d99` 真跑完 `POST /integration/params`（参数 64 条、连接 3 处、BOM 8 行）
    与 `POST /integration/process`（组装工序 10 道）：`status = {has_params: true, param_count: 64,
    has_process: true, process_step_count: 10, ...}`、`plan.drawings = 0`；
  · 投影里 3.1 仍是 `not_started` + `missing=["整合分析还没有执行"]`，
    3.2 `awaiting_confirmation` / 3.3 `generated` 都带 `blocked_reasons=["请先完成 3.1 整合图纸"]`，
    `next_action` 永远指回 3.1 的 `runIntegration`；
  · 根因：`_judge()` 里 `3.1` 的判据是 `done = bool(plan.drawings) or bool(parts)` ——
    只看图纸与零件，**不看 `plan.params` / `plan.process`**，而包装项目两样都没有。

纪律：进程内直调投影自己的判据（纯函数 + 真模型构造的 facts），
不起服务、不发 HTTP、不连 PG / SQLite、不写业务数据。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.models.integration import (  # noqa: E402
    IntegrationParamPlan, IntegrationPlan, ProcessPlan)
from tech_app.backend.models.ir import DesignIR, Part  # noqa: E402
from tech_app.backend.services import workflow_projection as wp  # noqa: E402

PID = "8131f6d29d99"


def facts(plan: IntegrationPlan, *, ir=None, parts_doc=None):
    return {"requirement": {"status": "approved"}, "ir": ir, "plan": plan,
            "review": None, "audit": [], "packaging_cad_ir": parts_doc,
            "packaging_parts": parts_doc}


def plan_with(params=None, process=None, drawings=None):
    p = IntegrationPlan(project_id=PID)
    p.params = params
    p.process = process
    p.drawings = list(drawings or [])
    return p


#: 34 上的包装解析产物（2.1 靠它判完成；`## 447` 的口径），截断成 2 件。
PACKING_PARTS_DOC = {"engine_version": "packaging-parts/1",
                     "parts": [{"part_code": "DWG-P01", "name": "图纸零件 P01"},
                               {"part_code": "DWG-P02", "name": "图纸零件 P02"}],
                     "stats": {"part_total": 263}}

#: 34 实测的真实形态：2.1 有包装解析产物、分析跑过（params + process 都有）、
#: 没有整合图纸、IR 为空。
ANALYZED = facts(plan_with(params=IntegrationParamPlan(assembly_name="酒盒"),
                           process=ProcessPlan()), parts_doc=PACKING_PARTS_DOC)

#: 3.1 主按钮认的角色码（角色表是唯一来源，不许在测试里写死）。
ROLE_31 = (wp._role_names("3.1")[1] or ("",))[0]
ONLY_PARAMS = facts(plan_with(params=IntegrationParamPlan(assembly_name="酒盒")))
ONLY_PROCESS = facts(plan_with(process=ProcessPlan()))
NOTHING = facts(plan_with())
WITH_DRAWING = facts(plan_with(drawings=[{"filename": "整装图.png"}]))


def ir_facts(n_parts: int = 1) -> dict:
    ir = DesignIR(device_name="酒盒", design_intent="复跑",
                  parts=[Part(part_id="P%d" % (i + 1), name="件%d" % (i + 1), quantity=1)
                         for i in range(n_parts)])
    return facts(plan_with(), ir=ir.model_dump())


class IntegrationStageCountsItsOwnAnalysisTest(unittest.TestCase):
    # ---------------- A 组（今天都是红的） ----------------

    def test_a1_params_alone_completes_stage_3_1(self):
        row = wp._judge("3.1", PID, ONLY_PARAMS)
        self.assertTrue(row.get("completed"),
                        "参数推荐已生成（分析真跑过）时 3.1 必须算完成；"
                        "今天回的是 %s / %s" % (row.get("status"), row.get("missing")))

    def test_a2_process_alone_completes_stage_3_1(self):
        row = wp._judge("3.1", PID, ONLY_PROCESS)
        self.assertTrue(row.get("completed"),
                        "组装工艺已生成时 3.1 必须算完成；今天回的是 %s / %s"
                        % (row.get("status"), row.get("missing")))

    def test_a3_next_action_leaves_stage_3_1_after_the_analysis(self):
        rows = wp._rows_for(PID, ANALYZED, ROLE_31)
        nxt = wp._next_action(rows) or {}
        self.assertNotEqual(nxt.get("key"), "3.1",
                            "34 上分析已经跑过（参数 64 条 / 工序 10 道），"
                            "next_action 不许再指回 3.1；今天指回 %r" % (nxt.get("key"),))

    # ---------------- B 组：护栏（今天就是绿的，不许被改红） ----------------

    def test_b1_nothing_ran_still_says_not_started(self):
        row = wp._judge("3.1", PID, NOTHING)
        self.assertEqual(row.get("status"), "not_started")
        self.assertEqual(row.get("missing"), ["整合分析还没有执行"], "没跑过时的文案逐字不变")

    def test_b2_integration_drawing_still_completes_stage_3_1(self):
        self.assertTrue(wp._judge("3.1", PID, WITH_DRAWING).get("completed"))

    def test_b3_ir_parts_still_complete_stage_3_1(self):
        self.assertTrue(wp._judge("3.1", PID, ir_facts(3)).get("completed"))

    def test_b4_stage_3_2_and_3_3_judgements_unchanged(self):
        row32 = wp._judge("3.2", PID, ONLY_PARAMS)
        self.assertEqual(row32.get("status"), "awaiting_confirmation")
        self.assertEqual(row32.get("missing"), ["参数推荐尚未人工确认"])
        row33 = wp._judge("3.3", PID, ONLY_PROCESS)
        self.assertEqual(row33.get("status"), "generated")
        self.assertEqual(row33.get("missing"), ["组装工艺尚未确认"])

    def test_b5_phase_rule_unchanged(self):
        rows = [
            {"key": "3.1", "completed": False, "status": "not_started"},
            {"key": "3.2", "completed": True, "status": "confirmed"},
            {"key": "3.3", "completed": False, "status": "generated"},
        ]
        phases = wp._phases(rows)
        phase3 = [p for p in phases if p.get("no") == 3] or []
        if not phase3:
            self.skipTest("阶段表里没有第 3 阶段")
        self.assertFalse(phase3[0].get("completed"))


if __name__ == "__main__":
    unittest.main()
