"""红测：卡片步进不许倒回（补做 / 乱序完成 / 重放已完成的步）。

Spec：`docs/specs/quote-card-step-order-and-replay.md`

现状缺口（34 上真跑实测，会话 a001739dec31 / 项目 bc0d1aeb4547）：
  · 1、3、4、5、6 步先做完 → 卡片 `current_step=6` / `completed`；
  · 工艺经理补做第 2 步之后 → 变成 `current_step=3` / `handoff_pending`（六步全 done 的卡片反而
    退回"待转交 3"），必须把 3–6 步再确认一遍才回到 completed；
  · 根因：`cpq_wf.complete_step()` 一律 `current_step = step_no + 1`，从不读 `cpq_wf_card_step`
    里其它行的状态。

验证方式：**受控假连接**驱动真的 `cpq_wf.complete_step`（本地不需要 PG、不连库、不写文件）；
假连接只认那条路径真正会发的 SQL，遇到不认识的语句直接报错，免得静默放过。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cpq_wf  # noqa: E402

LAST = cpq_wf.LAST_STEP
SESSION = "red-card-order"
CARD_ID = 9001
STEP_ROLE = {1: "sales_mgr", 2: "process_mgr", 3: "sales_mgr",
             4: "sales_mgr", 5: "sales_mgr", 6: "sales_mgr"}


class _Cursor:
    def __init__(self, db):
        self.db = db
        self.rows = []
        self.rowcount = 0

    def execute(self, sql, args=()):
        self.db.run(sql, tuple(args or ()), self)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class _Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _Cursor(self.db)

    def close(self):
        pass


class FakeDB:
    """只认 complete_step 会发的 SQL；沙盘里只有一张卡片与它的六步。"""

    def __init__(self, done=(), current_step=1, status="in_progress"):
        done = set(done)
        self.card = {"card_id": CARD_ID, "session_id": SESSION, "assistant_type": "quote",
                     "title": "红测卡片", "customer": "", "project_name": "",
                     "current_step": current_step, "overall_status": status,
                     "creator_user_id": None, "current_owner": None, "created_at": None,
                     "updated_at": None, "business_case_id": "", "industry": "packaging"}
        self.steps = [{"card_id": CARD_ID, "step_no": n, "status": "done" if n in done else "pending",
                       "owner_user_id": None, "data_snapshot": None, "started_at": None,
                       "completed_at": None} for n in range(1, LAST + 1)]
        self.events = []
        self._seq = 7000000

    # -- SQL 分发 -------------------------------------------------------
    def run(self, sql, args, cur):
        text = " ".join(str(sql).split())
        up = text.upper()
        if up.startswith("SELECT") and "SNOW_NEXT_ID()" in up.replace(" ", ""):
            self._seq += 1
            cur.rows, cur.rowcount = [(self._seq,)], 1
            return
        if up.startswith("SELECT"):
            table = text.split(" FROM ", 1)[1].split()[0] if " FROM " in up else ""
            cols = [c.strip().split(".")[-1] for c in text[len("SELECT"):text.upper().index(" FROM ")].split(",")]
            rows = self._rows_of(table)
            cur.rows = [tuple(row.get(c) for c in cols) for row in rows]
            cur.rowcount = len(cur.rows)
            return
        if up.startswith("UPDATE"):
            cur.rowcount = self._update(text, up, args)
            return
        if up.startswith("INSERT"):
            cur.rowcount = self._insert(text, up, args)
            return
        raise AssertionError("受控假连接不认这条 SQL：%s" % text[:140])

    def _rows_of(self, table):
        if table == "cpq_wf_card":
            return [self.card]
        if table == "cpq_wf_card_step":
            return self.steps
        return []                                   # cpq_wf_user 等：没有行 = 不触发自动推送

    def _update(self, text, up, args):
        if "CPQ_WF_CARD_STEP" in up:
            step_no = int(args[-1])
            row = next((s for s in self.steps if s["step_no"] == step_no), None)
            if row is None:
                raise AssertionError("沙盘里没有第 %s 步" % step_no)
            if "STATUS = 'DONE'" in up:
                row["status"] = "done"
                if len(args) > 2:
                    row["completed_at"] = args[2]
            if "started_at" in text:
                row["started_at"] = row.get("started_at") or args[-3] if len(args) >= 3 else None
            return 1
        if "CPQ_WF_CARD " in up + " ":
            self.card["current_step"], self.card["overall_status"] = args[0], args[1]
            if len(args) > 2:
                self.card["updated_at"] = args[2]
            return 1
        if "CPQ_WF_TASK" in up:
            return 0
        raise AssertionError("受控假连接不认这条 UPDATE：%s" % text[:140])

    def _insert(self, text, up, args):
        if "CPQ_WF_TASK_EVENT" in up:
            self.events.append({"action": args[4] if len(args) > 4 else "",
                                "comment": args[7] if len(args) > 7 else ""})
            return 1
        raise AssertionError("受控假连接不认这条 INSERT：%s" % text[:140])


def user_for(step_no):
    role = STEP_ROLE[step_no]
    return {"user_id": 101, "role_code": role, "role_name": role, "display_name": "红测用户"}


def complete(db, step_no):
    return cpq_wf.complete_step(SESSION, step_no, user_for(step_no),
                                snapshot='{"red": true}', conn=_Conn(db))


class ABackfill(unittest.TestCase):
    def test_a1_backfilling_a_lower_step_does_not_regress(self):
        db = FakeDB(done=(1, 3, 4, 5, 6), current_step=LAST, status="completed")
        complete(db, 2)
        self.assertEqual(LAST, db.card["current_step"],
                         "六步全做完的卡片补做第 2 步后不许退回第 3 步（Spec §3.2）")
        self.assertEqual("completed", db.card["overall_status"],
                         "补做已完成步骤不许把 completed 打回 handoff_pending（Spec §3.2）")

    def test_a2_out_of_order_lands_on_the_first_pending_step(self):
        db = FakeDB(done=(2,), current_step=3, status="handoff_pending")
        complete(db, 2)
        self.assertEqual(1, db.card["current_step"],
                         "只做了第 2 步时，第一个没做完的步是第 1 步（Spec §3.3）")
        self.assertEqual("awaiting_handoff", db.card["overall_status"],
                         "第 1 步归别的角色，状态应为待转交（Spec §3.3）")


class BReplay(unittest.TestCase):
    def test_b1_replaying_a_done_step_keeps_the_card_completed(self):
        db = FakeDB(done=range(1, LAST + 1), current_step=LAST, status="completed")
        complete(db, 5)
        self.assertEqual(LAST, db.card["current_step"], "重放不许让卡片倒回（Spec §3.4）")
        self.assertEqual("completed", db.card["overall_status"], "重放不许改变已完成状态（Spec §3.4）")

    def test_b2_replay_still_writes_a_step_done_event(self):
        db = FakeDB(done=range(1, LAST + 1), current_step=LAST, status="completed")
        complete(db, 5)
        self.assertTrue([e for e in db.events if e["action"] == "step_done"],
                        "补做/重放照样要留痕（Spec §3.6）")


class CSequential(unittest.TestCase):
    def test_c1_sequential_completion_is_unchanged(self):
        db = FakeDB()
        for n in range(1, LAST + 1):
            out = complete(db, n)
            expect = LAST if n == LAST else n + 1
            self.assertEqual(expect, db.card["current_step"],
                             "顺序推进时 current_step 仍是下一步（Spec §3.1）")
            self.assertEqual(expect, (out.get("card") or {}).get("current_step"),
                             "返回体里的卡片要与库里一致（Spec §3.5）")
        self.assertEqual("completed", db.card["overall_status"], "六步做完即完成（Spec §3.1）")

    def test_c2_payload_points_at_the_same_step(self):
        db = FakeDB(done=(1, 3, 4, 5, 6), current_step=LAST, status="completed")
        out = complete(db, 2)
        self.assertIn(out.get("next_step_no"), (None, ""),
                      "全做完时 next_step_no 必须为空，不能指向已做完的第 3 步（Spec §3.5）")
        self.assertIn(out.get("next_role_code"), (None, ""), "全做完时 next_role_code 必须为空（Spec §3.5）")
        self.assertEqual(db.card["current_step"], (out.get("card") or {}).get("current_step"),
                         "返回体里的卡片必须与库里的卡片一致（Spec §3.5）")


if __name__ == "__main__":
    unittest.main()
