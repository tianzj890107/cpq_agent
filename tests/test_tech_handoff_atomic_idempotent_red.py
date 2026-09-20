"""红测：技术工艺 / 成本 / 报告回传报价的原子闭环与幂等（批次 3）。

用户口径（批次 3 原文要点）：

  · 回传今天被拆成"创建目标任务 / 推进报价"和"关闭来源任务"两次互相独立的请求，
    跨 HTTP 部分成功时会出现"销售已收到任务、技术来源任务仍显示未完成"，
    用户重试还会重复创建；
  · 必须**定义一个原子业务命令**，覆盖：成本直接回销售、成本回工艺经理复核、
    工艺经理确认后回销售、已发布报告回销售、关闭来源 claimed 任务、更新第 2 步快照、
    保证 current_step 单调前进、创建或复用目标报价任务、写消息与审计、返回唯一 handoff_id；
  · 必须使用业务幂等键（handoff_kind + source_task_id + source_project_id +
    source_result_version + target_quote_session_id）：相同键重复调用只返回同一结果；
    首次成功但客户端超时，重试不得产生第二条任务；来源任务已完成返回 already_completed；
    任一步失败必须整体回滚；不允许"报价任务已创建、来源任务未关闭"的半完成状态。

现状缺口（只读实测，均已定位；本文件的断言都是"行为"，不是文本搜索）：

  · `cpq_tech_bridge.send_to_quote`（cpq_tech_bridge.py:531-687）跨 5 条连接、5 次提交：
    查来源任务 / 建会话 / 补前置步骤 / complete_step / send_task；而
    `cpq_auth._connect()` 是 autocommit=True、`cpq_auth._commit`/`cpq_wf._commit` 是空函数，
    所以"多写一步"＝"多提交一次"。受控假库实测：一次成功回传的 20 条写语句**全部**不在事务里。
  · 来源任务关闭是**另一次**请求（`/wf/tech/complete-task`，见 `cost_flow.py:308-329` 与
    515/573 两处调用），失败只写一条审计、返回 `{"closed": False}`（cost_flow.py:326-329）；
    报告回传 `report_workflow.send_to_quote` 一次都不关。实测：第 4 步做报告回传后，
    来源任务仍是 claimed，且卡片上不会出现给销售的任务。
  · 幂等只认 `payload->>'handoff_key'`（cpq_tech_bridge.py:418-428），且仅当目标任务
    `status IN ('open','claimed')`；没有唯一约束、键里也没有 source_task_id。
    实测：目标任务被置为 completed 后再点一次，会**再建一条任务**。
  · 没有 `cpq_wf_handoff` 表、没有 handoff_id：实测响应里没有 `handoff_id` 字段。
  · 2.3「提交工艺经理确认」用技术项目号当报价会话号（`_side_task` → `sync_card`），
    实测会新建一张 session_id=技术项目号的**幽灵卡片**，任务挂在那张卡上，
    原报价卡片上什么都没有。

验证方式（行为为主：受控假库 + 真调 cpq_tech_bridge / cpq_wf）：

  · `tests/fixtures/wf_handoff_harness.py`：能真跑这些 SQL 的最小引擎（JOIN / 布尔 WHERE /
    `->>` / `RETURNING` / `ON CONFLICT`），带**事务与撤销日志**、
    `cpq_wf_handoff.handoff_key` 唯一约束、按"第 N 条写语句"注入故障，
    以及"这条写是否发生在事务里"的写日志。
  · 全部断言都是行为与状态：整库快照前后对比、并发放行、幂等收敛、技术侧子进程真跑。
    前端把 handoff 编号与来源待办状态展示出来属**人工验收**（Spec 15.x），
    本文件不做字符串搜索式断言。

Spec：docs/specs/tech-handoff-atomic-idempotent-close.md
"""
from __future__ import annotations

import functools
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests" / "fixtures") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import wf_handoff_harness as H  # noqa: E402

SPEC = ROOT / "docs" / "specs" / "tech-handoff-atomic-idempotent-close.md"


def setUpModule():
    if H.LOAD_ERROR:
        raise AssertionError(H.LOAD_ERROR)
    if not H.SELFCHECK_OK:
        raise AssertionError(f"受控假库自检失败：{H.SELFCHECK_ERROR}")


def frozen(rows):
    """把一批 dict 行变成可比较的、与列顺序无关的冻结值。"""
    out = []
    for row in rows:
        out.append(tuple(sorted(
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
            for key, value in row.items())))
    return tuple(sorted(out))


def state(wb):
    """整库快照：判断"这次回传到底留下了什么"。"""
    return {
        "cpq_wf_card": frozen(wb.cards()),
        "cpq_wf_card_step": frozen(wb.rows("cpq_wf_card_step")),
        "cpq_wf_task": frozen(wb.tasks()),
        "cpq_wf_task_event": frozen(wb.events()),
        "cpq_wf_message": frozen(wb.messages()),
        "cpq_wf_handoff": frozen(wb.handoffs()),
    }


def diff_state(before, after):
    return "、".join(name for name in before if before[name] != after[name])


@functools.lru_cache(maxsize=1)
def baseline_writes():
    """一次正常回传会写多少条语句（用来按"第 N 条写"注入故障）。"""
    with H.Workbench() as wb:
        wb.handoff(kind="cost_to_quote")
        return wb.db.writes_seen


class HandoffCase(unittest.TestCase):
    """共用判定：一次回传只允许两种结局 —— 全部发生，或什么都不发生。"""

    def call(self, wb, **kwargs):
        try:
            if kwargs.get("kind", "cost_to_quote") is None:      # None = cost_to_process
                kwargs.pop("kind")
                return wb.return_process(**kwargs), None
            return wb.handoff(**kwargs), None
        except Exception as exc:                        # noqa: BLE001 - 异常本身是断言对象
            return None, exc

    def assert_nothing_happened(self, before, after, message=""):
        changed = diff_state(before, after)
        self.assertEqual("", changed, f"{message}：报错后这些表被改动了 -> {changed}")

    def assert_everything_happened(self, wb, out, *, kind=None, source_task_id="9001",
                                   expect_handoffs=1):
        """成功返回 ⇒ 副作用必须齐备（"半完成状态"的总闸）。"""
        self.assertIsNotNone(out, "回传必须返回结果")
        self.assertTrue(str(out.get("handoff_id") or "").strip(),
                        f"成功返回必须带 handoff_id（spec 6.4），实际字段：{sorted(out)}")
        self.assertEqual(kind, str(out.get("handoff_kind") or ""),
                         f"返回体必须写明回传类型：{out.get('handoff_kind')}")
        rows = wb.handoffs()
        self.assertEqual(expect_handoffs, len(rows),
                         f"一次成功回传只应有 {expect_handoffs} 条回传记录：{rows}")
        row = rows[0]
        self.assertEqual(str(out["handoff_id"]), str(row.get("handoff_id")))
        self.assertEqual(kind, str(row.get("handoff_kind") or ""))
        self.assertTrue(str(row.get("handoff_key") or "").strip(), "回传记录必须落幂等键")
        if source_task_id:
            source = wb.source_task()
            self.assertEqual("completed", str((source or {}).get("status")),
                             "成功回传后来源待办必须已经关闭（不许半完成）")
            self.assertTrue(bool(row.get("source_task_closed")),
                            "回传记录必须写明来源任务已关闭")

    def assert_outcome_is_all_or_nothing(self, wb, before, out, raised, **kwargs):
        if raised is not None:
            self.assert_nothing_happened(before, state(wb), f"注入故障后（{raised}）")
            return
        self.assert_everything_happened(wb, out, **kwargs)


# ---------------------------------------------------------------------------
# 0. 假库自检：先证明观察手段可信
# ---------------------------------------------------------------------------
class HarnessSelfTest(HandoffCase):
    def test_harness_loaded_and_selfchecked(self):
        self.assertEqual("", H.LOAD_ERROR)
        self.assertTrue(H.SELFCHECK_OK, H.SELFCHECK_ERROR)
        self.assertTrue(SPEC.exists(), "批次 3 的 Spec 必须在仓库里")
        self.assertTrue((ROOT / "tests" / "fixtures" / "wf_handoff_harness.py").exists())

    def test_fake_engine_really_executes_workflow_sql(self):
        with H.Workbench() as wb:
            wb.handoff(kind="cost_to_quote")
            self.assertGreaterEqual(wb.db.writes_seen, 10,
                                    "假库必须真的跑到了工作流 SQL，而不是被 mock 掉")
            self.assertTrue(wb.tasks(), "任务表必须有行")

    def test_fake_engine_enforces_handoff_key_uniqueness(self):
        db = H.FakeDB()
        cur = H.TxConn(db).cursor()
        cur.execute("INSERT INTO cpq_wf_handoff (handoff_id, handoff_key) VALUES (%s,%s)",
                    (1, "K"))
        with self.assertRaises(H.FakeUniqueViolation):
            cur.execute("INSERT INTO cpq_wf_handoff (handoff_id, handoff_key) VALUES (%s,%s)",
                        (2, "K"))

    def test_fake_engine_rolls_back_only_its_own_writes(self):
        """事务回滚只撤销「本连接加入事务的那几条写」，别的连接已提交的行不能被顺手删掉。"""
        db = H.FakeDB()
        keep = H.TxConn(db)
        with keep.transaction():
            keep.cursor().execute(
                "INSERT INTO cpq_wf_card (card_id, session_id) VALUES (%s,%s)", (1, "keep"))
        rollback = H.TxConn(db)
        with self.assertRaises(RuntimeError):
            with rollback.transaction():
                rollback.cursor().execute(
                    "INSERT INTO cpq_wf_card (card_id, session_id) VALUES (%s,%s)", (2, "undo"))
                raise RuntimeError("事务中间异常")
        self.assertEqual([1], [row["card_id"] for row in db.table("cpq_wf_card")])

    def test_bare_autocommit_write_is_already_durable(self):
        """与 psycopg 一致：autocommit 连接上的裸写没有事务可回滚，写完就是持久化。

        这正是本批要抓的缺口来源 —— 今天 `cpq_tech_bridge` 的 20 条写都落在这里。
        """
        db = H.FakeDB()
        conn = H.TxConn(db)
        conn.cursor().execute(
            "INSERT INTO cpq_wf_card (card_id, session_id) VALUES (%s,%s)", (3, "bare"))
        conn.rollback()
        self.assertEqual([3], [row["card_id"] for row in db.table("cpq_wf_card")],
                         "裸写落在事务之外，回滚必须撤不掉它")


# ---------------------------------------------------------------------------
# 1. 原子性：一个命令、一个事务，要么全发生要么全不发生
# ---------------------------------------------------------------------------
class AtomicityTest(HandoffCase):
    def test_success_means_every_side_effect_is_present(self):
        with H.Workbench() as wb:
            out, raised = self.call(wb)
            self.assertIsNone(raised, f"正常回传不该失败：{raised}")
            self.assert_everything_happened(wb, out, kind="cost_to_quote")
            self.assertGreaterEqual(len(wb.deliverables()), 1,
                                    "成功回传必须在卡片上留下给销售的交接任务")
            self.assertIsNotNone(wb.step2_snapshot(), "第 2 步快照必须被写入")

    def test_failure_after_target_task_is_created_leaves_nothing(self):
        total = baseline_writes()
        with H.Workbench() as wb:
            before = state(wb)
            wb.db.fail_on_write(max(1, total - 1))
            out, raised = self.call(wb)
            self.assertIsNotNone(raised, "倒数第二条写失败必须让整次回传失败")
            self.assert_nothing_happened(before, state(wb), "倒数第二条写失败")

    def test_failure_on_last_write_leaves_nothing(self):
        total = baseline_writes()
        with H.Workbench() as wb:
            before = state(wb)
            wb.db.fail_on_write(total)
            out, raised = self.call(wb)
            self.assertIsNotNone(raised, "最后一条写失败必须让整次回传失败")
            self.assert_nothing_happened(before, state(wb), "最后一条写失败")

    def test_write_index_sweep_never_leaves_partial_state(self):
        total = baseline_writes()
        self.assertGreater(total, 3, "基线回传至少要有几条写语句")
        for index in (2, max(2, total // 2), max(2, total - 2), total):
            with self.subTest(write_index=index):
                with H.Workbench() as wb:
                    before = state(wb)
                    wb.db.fail_on_write(index)
                    out, raised = self.call(wb)
                    self.assert_outcome_is_all_or_nothing(
                        wb, before, out, raised, kind="cost_to_quote")

    def test_no_write_happens_outside_one_transaction(self):
        with H.Workbench() as wb:
            out, raised = self.call(wb)
            self.assertIsNone(raised, f"正常回传不该失败：{raised}")
            self.assert_everything_happened(wb, out, kind="cost_to_quote")
            outside = wb.unprotected_writes()
            self.assertEqual([], outside,
                             "回传的写必须全部发生在同一个事务连接上，"
                             f"实际有 {len(outside)} 条裸写（例如：{outside[:2]}）")


# ---------------------------------------------------------------------------
# 2. 幂等：同一把业务键只产生一次有效交接
# ---------------------------------------------------------------------------
class IdempotencyTest(HandoffCase):
    def test_second_call_returns_the_same_handoff(self):
        with H.Workbench() as wb:
            first, raised = self.call(wb)
            self.assertIsNone(raised, f"第一次回传失败：{raised}")
            second, raised2 = self.call(wb)
            self.assertIsNone(raised2, f"重复回传不该报错：{raised2}")
            self.assertEqual(first["handoff_id"], second["handoff_id"],
                             "同一把幂等键必须返回同一条回传记录")
            self.assertTrue(second.get("already_sent"), "重复调用必须标记 already_sent")
            self.assertEqual(1, len(wb.handoffs()))
            self.assertEqual(1, len(wb.deliverables()), "不得产生第二条交接任务")

    def test_retry_after_target_task_completed_creates_no_second_task(self):
        with H.Workbench() as wb:
            first = wb.handoff(kind="cost_to_quote")
            for row in wb.tasks(task_kind="handoff"):
                row["status"] = "completed"
            second, raised = self.call(wb)
            self.assertIsNone(raised, f"目标任务已完成后的重试不该失败：{raised}")
            self.assertEqual(first["handoff_id"], second["handoff_id"])
            self.assertEqual(1, len(wb.handoffs()))
            self.assertEqual(1, len(wb.deliverables()),
                             "目标任务已完成后重试不得再建一条任务")

    def test_retry_after_source_task_completed_reports_already_completed(self):
        with H.Workbench(source_task_status="completed") as wb:
            first, raised = self.call(wb)
            self.assertIsNone(raised, f"来源任务已完成时首次回传仍应成功：{raised}")
            second, raised2 = self.call(wb)
            self.assertIsNone(raised2, f"重试不该失败：{raised2}")
            self.assertTrue(second.get("already_completed"),
                            "来源任务已完成时重复调用必须返回 already_completed")
            self.assertEqual(first["handoff_id"], second["handoff_id"])
            self.assertEqual(1, len(wb.deliverables()))

    def test_new_result_version_creates_a_new_handoff(self):
        with H.Workbench() as wb:
            first = wb.handoff(kind="cost_to_quote", result_version="cost-v1:1:100")
            second = wb.handoff(kind="cost_to_quote", result_version="cost-v1:1:200")
            self.assertNotEqual(first["handoff_id"], second["handoff_id"],
                                "换版本必须是一次新的交接，不能被旧键吞掉")
            self.assertFalse(second.get("already_sent"))
            self.assertEqual(2, len(wb.handoffs()))
            versions = sorted(str(row.get("source_result_version") or "")
                              for row in wb.handoffs())
            self.assertEqual(["cost-v1:1:100", "cost-v1:1:200"], versions)

    def test_different_source_task_creates_a_new_handoff(self):
        with H.Workbench() as wb:
            first = wb.handoff(kind="cost_to_quote", source_task_id="9001")
            second = wb.handoff(kind="cost_to_quote", source_task_id="9002")
            self.assertNotEqual(first["handoff_id"], second["handoff_id"],
                                "幂等键必须包含 source_task_id")
            self.assertEqual(2, len(wb.handoffs()))

    def test_handoff_key_carries_all_five_components(self):
        with H.Workbench() as wb:
            out = wb.handoff(kind="cost_to_quote", result_version="cost-v1:1:123.45")
            rows = wb.handoffs()
            self.assertEqual(1, len(rows))
            key = str(rows[0].get("handoff_key") or "")
            for token in ("cost_to_quote", "9001", H.TECH_PROJECT, "cost-v1:1:123.45",
                          H.QUOTE_SESSION):
                self.assertIn(token, key, f"幂等键缺少组成项 {token}：{key}")
            self.assertEqual(key, str(out.get("handoff_key") or key))


# ---------------------------------------------------------------------------
# 3. 关闭来源任务：同一个事务里，且如实回报
# ---------------------------------------------------------------------------
class SourceTaskCloseTest(HandoffCase):
    def test_report_handoff_closes_the_claimed_source_task(self):
        with H.Workbench(current_step=4, source_task_kind="tech_cost") as wb:
            out, raised = self.call(wb, kind="report_to_quote",
                                    report=H.report_package(), result_version="report-v2")
            self.assertIsNone(raised, f"第 4 步的报告回传不该失败：{raised}")
            self.assertEqual("completed", str(wb.source_task().get("status")),
                             "报告回传后来源 claimed 待办必须关闭")
            self.assertEqual(1, len(wb.handoffs()))
            self.assertTrue((out.get("source_task") or {}).get("closed"),
                            f"返回体必须说明来源任务已关闭：{out.get('source_task')}")

    def test_source_task_close_result_is_reported(self):
        with H.Workbench() as wb:
            out, raised = self.call(wb)
            self.assertIsNone(raised, f"回传失败：{raised}")
            info = out.get("source_task") or {}
            self.assertEqual("9001", str(info.get("task_id") or ""))
            self.assertEqual("completed", str(info.get("status") or ""))
            self.assertIn("closed", info)
            self.assertFalse(info.get("already"))

    def test_source_task_claimed_by_someone_else_is_refused_without_writes(self):
        with H.Workbench(source_claimed_by=H.OTHER_FIN_UID) as wb:
            before = state(wb)
            out, raised = self.call(wb)
            self.assertIsNotNone(raised, "别人的待办不能被这次回传顺手关掉")
            self.assert_nothing_happened(before, state(wb), "越权关闭来源任务")

    def test_missing_source_task_is_not_an_error(self):
        with H.Workbench(with_source_task=False) as wb:
            out, raised = self.call(wb, source_task_id="")
            self.assertIsNone(raised, f"没有来源任务（技术工艺独立发起）不该失败：{raised}")
            info = out.get("source_task") or {}
            self.assertEqual("missing_task_id", str(info.get("skipped") or ""))
            self.assertFalse(info.get("closed"))
            self.assertEqual(1, len(wb.handoffs()))


# ---------------------------------------------------------------------------
# 4. 步骤单调：第 2 步快照只合并，current_step 只前进
# ---------------------------------------------------------------------------
class StepMonotonicityTest(HandoffCase):
    def test_report_handoff_at_step_four_keeps_step_and_status(self):
        with H.Workbench(current_step=4, overall_status="handoff_pending") as wb:
            out, raised = self.call(wb, kind="report_to_quote",
                                    report=H.report_package(), result_version="report-v2")
            self.assertIsNone(raised, f"报告回传失败：{raised}")
            card = wb.card()
            self.assertEqual(4, int(card["current_step"]), "回传不得把报价步骤写回去")
            self.assertEqual("handoff_pending", str(card["overall_status"]),
                             "卡片总状态不得回退")
            self.assertGreaterEqual(len(wb.deliverables()), 1,
                                    "报告回传必须给销售留下一条任务")
            self.assertEqual(1, len(wb.handoffs()))

    def test_step_two_snapshot_is_merged_not_replaced(self):
        existing = json.dumps({"s1_basic": {"数据": [{"客户": "客户A"}]}}, ensure_ascii=False)
        with H.Workbench(step2_snapshot=existing) as wb:
            out, raised = self.call(wb)
            self.assertIsNone(raised, f"回传失败：{raised}")
            snapshot = wb.step2_snapshot() or {}
            self.assertIn("s1_basic", snapshot, "第 2 步快照必须合并，不得整份覆盖")
            self.assertIn("s2_products", snapshot, "技术结果必须写进第 2 步快照")
            self.assertIn("s2_products", out.get("returned_sections") or [])

    def test_current_step_moves_to_next_stage_on_cost_handoff(self):
        with H.Workbench(current_step=1) as wb:
            out, raised = self.call(wb)
            self.assertIsNone(raised, f"回传失败：{raised}")
            self.assertEqual(3, int(wb.card()["current_step"]))
            self.assertEqual(3, int(out.get("next_step_no") or 0))
            self.assertEqual("定价-利润加成", str(out.get("next_step_name") or ""))


# ---------------------------------------------------------------------------
# 5. 四种回传类型都走同一条原子命令
# ---------------------------------------------------------------------------
class HandoffKindCoverageTest(HandoffCase):
    def test_cost_to_process_lands_on_the_original_quote_card(self):
        with H.Workbench() as wb:
            out, raised = self.call(wb, kind=None)
            self.assertIsNone(raised, f"提交工艺经理失败：{raised}")
            del out
            self.assertEqual(1, len(wb.cards()),
                             "不得用技术项目号新建一张报价卡片（幽灵卡片）")
            rows = wb.rows("cpq_wf_task", task_kind="tech_cost_return")
            self.assertEqual(1, len(rows))
            self.assertEqual(H.CARD_ID, int(rows[0]["card_id"]),
                             "成本结果复核任务必须挂在原报价卡片上")

    def test_cost_to_process_closes_the_cost_task_and_records_handoff(self):
        with H.Workbench() as wb:
            out, raised = self.call(wb, kind=None)
            self.assertIsNone(raised, f"提交工艺经理失败：{raised}")
            self.assertEqual("completed", str(wb.source_task().get("status")))
            rows = wb.handoffs()
            self.assertEqual(1, len(rows), f"必须留下回传记录：{rows}")
            self.assertEqual("cost_to_process", str(rows[0].get("handoff_kind") or ""))
            self.assertEqual(str(H.FIN_UID), str(rows[0].get("created_by_user_id") or ""))

    def test_every_kind_writes_exactly_one_handoff(self):
        cases = (
            ("cost_to_quote", dict(uid=H.FIN_UID)),
            ("process_to_quote", dict(uid=H.PROC_UID)),
            ("report_to_quote", dict(uid=H.PROC_UID, report=H.report_package(),
                                     result_version="report-v2")),
        )
        for kind, kwargs in cases:
            with self.subTest(kind=kind):
                # 来源待办必须由**发起回传的这个人**领取：越权关闭别人的待办要被拒绝
                # （见 SourceTaskCloseTest），所以这里按调用者把领取人对上。
                with H.Workbench(source_claimed_by=kwargs["uid"]) as wb:
                    out, raised = self.call(wb, kind=kind, **kwargs)
                    self.assertIsNone(raised, f"{kind} 回传失败：{raised}")
                    rows = wb.handoffs()
                    self.assertEqual(1, len(rows))
                    self.assertEqual(kind, str(rows[0].get("handoff_kind") or ""))

    def test_handoff_records_are_signed_by_the_caller(self):
        with H.Workbench() as wb:
            wb.handoff(kind="cost_to_quote", uid=H.FIN_UID)
            wb.handoff(kind="process_to_quote", uid=H.PROC_UID, result_version="cost-v1:2:1")
            signed = sorted(str(row.get("created_by_user_id") or "")
                            for row in wb.handoffs())
            self.assertEqual(sorted([str(H.FIN_UID), str(H.PROC_UID)]), signed,
                             "回传记录必须留下是谁交的")


# ---------------------------------------------------------------------------
# 6. 随任务带走的内容：技术结果 / 报告 / 消息 / 审计
# ---------------------------------------------------------------------------
class PayloadAndNotificationTest(HandoffCase):
    def payload_of(self, wb, kind="handoff"):
        rows = wb.deliverables(kind=kind)
        self.assertTrue(rows, f"卡片上应该有给销售的 {kind} 任务")
        raw = rows[0].get("payload")
        return json.loads(raw) if isinstance(raw, str) else (raw or {})

    def test_cost_handoff_carries_full_technical_result(self):
        with H.Workbench() as wb:
            out = wb.handoff(kind="cost_to_quote")
            payload = self.payload_of(wb)
            self.assertEqual(str(out["handoff_id"]), str(payload.get("handoff_id") or ""),
                             "任务 payload 必须带上 handoff_id 以便追溯")
            result = payload.get("tech_result") or {}
            for key in ("params", "process", "parts", "assembly", "cost", "final",
                        "cost_confirmation"):
                self.assertIn(key, result, f"技术结果缺少 {key}：{sorted(result)}")
            self.assertEqual("92022001", str((result.get("material") or {}).get("number")))
            self.assertEqual("cost_to_quote", str(payload.get("handoff_kind") or ""))
            self.assertEqual("cost-v1:1:123.45", str(payload.get("result_version") or ""))

    def test_report_handoff_carries_full_report_package(self):
        with H.Workbench() as wb:
            package = H.report_package()
            out = wb.handoff(kind="report_to_quote", report=package,
                             result_version="report-v2")
            payload = self.payload_of(wb)
            report = payload.get("report") or {}
            for key in ("report_no", "version", "status", "conclusion", "risks",
                        "attachments", "report_url", "pdf_url"):
                self.assertIn(key, report, f"报告包缺少 {key}：{sorted(report)}")
            self.assertEqual(package["report_no"], report["report_no"])
            self.assertEqual(2, int(report["version"]))
            self.assertEqual("report-v2", str(payload.get("result_version") or ""))
            self.assertEqual(str(out["handoff_id"]), str(payload.get("handoff_id") or ""))

    def test_report_handoff_does_not_overwrite_source_task_payload(self):
        with H.Workbench(current_step=4) as wb:
            marker = json.dumps({"tech_result": {"marker": "source"}}, ensure_ascii=False)
            wb.source_task()["payload"] = marker
            wb.handoff(kind="report_to_quote", report=H.report_package(),
                       result_version="report-v2")
            raw = wb.source_task().get("payload")
            kept = json.loads(raw) if isinstance(raw, str) else (raw or {})
            self.assertEqual("source", str((kept.get("tech_result") or {}).get("marker")),
                             "不得把来源任务的 payload 覆写成回传内容")

    def test_messages_and_audit_reference_the_handoff(self):
        with H.Workbench() as wb:
            out = wb.handoff(kind="cost_to_quote")
            rows = wb.handoffs()
            self.assertEqual(1, len(rows))
            target_task_id = str(rows[0].get("target_task_id") or "")
            deliverable_ids = {str(t["task_id"]) for t in wb.deliverables()}
            self.assertIn(target_task_id, deliverable_ids,
                          "回传记录里的目标任务必须真的存在")
            events = [row for row in wb.events()
                      if str(row.get("task_id")) == target_task_id]
            self.assertTrue(events, "目标任务必须有审计记录")
            self.assertTrue(wb.messages(user_id=H.SALES_UID), "销售必须收到「新任务」消息")
            self.assertTrue(wb.messages(user_id=H.FIN_UID), "发起人必须留下「我发出的任务」")
            self.assertTrue(str(out.get("handoff_id") or ""))


# ---------------------------------------------------------------------------
# 7. 并发：同一把键只允许一次有效交接
# ---------------------------------------------------------------------------
class ConcurrencyTest(HandoffCase):
    def race(self, wb):
        results = {}
        errors = {}

        def run(tag):
            try:
                results[tag] = wb.handoff(kind="cost_to_quote")
            except Exception as exc:                    # noqa: BLE001
                errors[tag] = exc

        threads = [threading.Thread(target=run, args=(f"race{i}",), name=f"race{i}")
                   for i in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        return results, errors

    def test_concurrent_same_version_produces_one_handoff_and_one_task(self):
        """两个线程同时交同一把键：只能落一条记录、一条任务，失败方收敛为复用。

        唯一约束要么由数据库挡下（INSERT 冲突 → 重读到同一条），要么由行锁挡下
        （先 SELECT FOR UPDATE 再写）—— 两种实现都算过，但都不许抛异常给用户，
        也不许两个线程各自返回一个空 handoff_id 假装「一样」。
        """
        gate = H.B2.Gate(parties=2, prefix="race")
        with H.Workbench(gate=gate) as wb:
            results, errors = self.race(wb)
            self.assertEqual({}, errors, f"失败方必须收敛为复用，而不是报错：{errors}")
            rows = wb.handoffs()
            self.assertEqual(1, len(rows), f"同一把键只允许一条回传记录：{rows}")
            self.assertEqual(1, len(wb.deliverables()), "只允许一条交接任务")
            ids = sorted(str(out.get("handoff_id") or "") for out in results.values())
            self.assertEqual(2, len(ids), f"两个调用都应有结果：{results}")
            self.assertTrue(all(part.strip() for part in ids),
                            f"两个调用都必须返回真实的 handoff_id：{results}")
            self.assertEqual(1, len(set(ids)), f"两个调用必须返回同一条回传记录：{ids}")
            self.assertEqual(1, sum(1 for out in results.values() if out.get("already_sent")),
                             "其中一个调用必须报告 already_sent")


# ---------------------------------------------------------------------------
# 8. 技术侧：只有拿到明确成功结果才记录「回传完成」
# ---------------------------------------------------------------------------
CHILD = r'''
import json
import os
import sys
import types

data_dir, root, case = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

from tech_app.backend.models.cost import CostAnalysis, CostItem
from tech_app.backend.models.integration import (IntegrationParam, IntegrationParamPlan,
                                                 IntegrationPlan)
from tech_app.backend.models.ir import DesignIR
from tech_app.backend.models.process import ProcessPlan, ProcessStep
from tech_app.backend.services import cost_flow, cpq_bridge, integration, product_params
from tech_app.backend.storage import store

ACTOR = {"username": "fin1", "display_name": "财务一", "role": "finance_manager",
         "user_id": "400", "role_code": "finance_mgr"}
PID = store.create_project(source_filename="handoff.dxf", source_bytes=b"x",
                           note="batch3 tech-side probe", owner="tester")
CALLS = []


def amount(value):
    return {"name": "材料费", "category": "material", "quantity": 1,
            "unit_price": value, "amount": value}


PART = "P-001"
store.save_ir(PID, DesignIR(device_name="便携式锂电池 PACK",
                            design_intent="批次 3 技术侧回传探针",
                            parts=[{"part_id": PART, "name": "上壳", "quantity": 1}]
                            ).model_dump(), stage="parsed")
store.save_cost(PID, PART, {"part_id": PART, "items": [amount(100.0)]})

plan = IntegrationPlan(project_id=PID)
plan.params = IntegrationParamPlan(product_family="other", params=[],
                                   assembly_name="便携式锂电池 PACK")
plan.cost = CostAnalysis(items=[CostItem(**amount(100.0))])
plan.process = ProcessPlan(steps=[ProcessStep(no=1, name="整机总装", hours=0.07)])
plan.quantity = 1
# 报价必填项补齐：本批验的是「回传原子性」，不该卡在参数门禁上。
for _group in product_params.checklist(plan.params)["groups"]:
    for _field in _group["fields"]:
        if _field["required"]:
            plan.params.params.append(IntegrationParam(
                param_code=_field["code"], name=_field["name"], value="X",
                unit=_field.get("unit") or None))
assert not product_params.missing_required(plan.params), "探针自身的参数表必须是齐的"
integration.save_plan(PID, plan, "tester")
cost_flow.confirm_review(PID, ACTOR)


def fake_write_material(token, product_name, unit_price, breakdown=None, spec="",
                        *extra, **extra_kw):
    # 批次 4 起 write_material 末尾多了业务幂等键（project_id / result_version）。
    # 代理桩只关心"有没有走桥调用"，参数多两个不该让本批的用例失败。
    CALLS.append("write_material")
    return {"material_id": "1", "number": "92022001", "name": product_name,
            "material_unit_price": unit_price, "tables": [], "by": "fin1"}


def fake_send_to_quote(token, session_id, title, *args, **kwargs):
    CALLS.append("send_to_quote")
    if case == "bridge_down":
        raise cpq_bridge.BridgeUnavailable("CPQ 服务返回 503：业务库暂不可用")
    return {"handoff_id": "H-1", "handoff_key": "K-1", "handoff_kind": "cost_to_quote",
            "quote_session_id": "qs-1", "linked_by": "task", "new_card": False,
            "already_sent": False, "next_step_no": 3, "next_step_name": "定价-利润加成",
            "returned_sections": ["s2_products"],
            "handoff": {"task_id": "77", "task_no": "TP-77",
                        "target_role_name": "销售经理"},
            "source_task": {"task_id": "9001", "closed": True, "status": "completed",
                            "already": False, "skipped": ""}}


def fake_complete_task(*args, **kwargs):
    CALLS.append("complete_task")
    return {"already": False}


cpq_bridge.write_material = fake_write_material
cpq_bridge.send_to_quote = fake_send_to_quote
cpq_bridge.complete_task = fake_complete_task

out = {"case": case}
try:
    data = cost_flow.send_to_quote(PID, ACTOR, token="t", source_task_id="9001")
    out["ok"] = True
    out["handoff_id"] = data.get("handoff_id")
    out["source_task"] = data.get("source_task")
    out["quote_session_id"] = data.get("quote_session_id")
except Exception as exc:                                # noqa: BLE001
    out["ok"] = False
    out["error_type"] = type(exc).__name__
    out["error"] = str(exc)[:300]
loaded = integration.load_plan(PID)
out["plan_handoff"] = (loaded.quote_handoff.model_dump()
                       if loaded and loaded.quote_handoff else None)
out["audit"] = [str(row.get("action") or "") for row in store.list_audit(PID)]
out["calls"] = CALLS
print(json.dumps(out, ensure_ascii=False, default=str))
'''


class TechSideContractTest(HandoffCase):
    """技术侧 2.3：桥接失败不许留痕；成功必须带 handoff_id，且不再自己关任务。"""

    def run_child(self, case):
        data_dir = tempfile.mkdtemp(prefix="cpq-handoff-data-")
        script_dir = tempfile.mkdtemp(prefix="cpq-handoff-script-")
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        env = dict(os.environ)
        env["DATA_DIR"] = data_dir
        env["AUTH_ENABLED"] = "false"
        env["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run(
            [sys.executable, str(script), data_dir, str(ROOT), case],
            capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertEqual(0, completed.returncode,
                         f"子进程失败：{completed.stderr[-800:]}")
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_bridge_failure_is_not_recorded_as_handoff_done(self):
        out = self.run_child("bridge_down")
        self.assertFalse(out["ok"], "桥接失败必须让这一次回传失败")
        self.assertIsNone(out["plan_handoff"], "失败时不得把项目标记为已回传")
        self.assertNotIn("cost_review_send_to_quote", out["audit"],
                         "失败时不得落下「已回传」审计")

    def test_success_records_handoff_id_from_the_server(self):
        out = self.run_child("bridge_ok")
        self.assertTrue(out["ok"], f"成功路径不该失败：{out.get('error')}")
        self.assertEqual("H-1", str(out.get("handoff_id") or ""),
                         "技术侧必须把服务端返回的 handoff_id 带出来")
        self.assertTrue((out.get("plan_handoff") or {}).get("session_id"),
                        "成功后必须把回传落的报价会话写进项目")
        self.assertEqual("qs-1", str(out.get("quote_session_id") or ""))

    def test_tech_side_does_not_issue_a_second_close_request(self):
        out = self.run_child("bridge_ok")
        self.assertNotIn("complete_task", out["calls"],
                         "来源任务的关闭必须由那一次回传命令在同一个事务里完成，"
                         "技术侧不得再发一次可以独立失败的关闭请求")


if __name__ == "__main__":
    unittest.main()
