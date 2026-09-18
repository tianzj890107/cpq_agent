"""红测：技术工艺首页三页签与待办任务口径（批次 ## 139）。

用户口径（原文要点）：

  · 技术工艺首页「应该是和报价一样的三个页签而不是五个，反正后两个里面也没内容」；
  · 「待办的卡片应该自己的样式」；
  · 「待办怎么现在是 28 个，应该报价转交过来的才是待办，就是反正有人转交给这个人的
    才是这个人的待办，或者是转交到谁都能领取的池子里。报价和工艺的逻辑都应该是这样的」。

现状缺口（只读实测，均已定位，不是推断）：

  · `tech_app/frontend/tech-home-board.js:15-19` 的 `ENTRIES` 是五项
    `mine / all / todo / recent / archived`；node 实跑该模块得到
    `ids=["mine","all","todo","recent","archived"]`、`sources=[...,"local",...]`；
  · `报价首页.html:1948-1951` 把「待办任务」分流写死成
    `if (mode === 'quote' && tab.indexOf('待办任务') === 0)`：报价侧走
    `renderTaskTab()`（`/wf/tasks` 任务卡），技术工艺侧走 `scope=todo` 的**项目清单**；
  · `报价首页.html:2220-2224` 的 `techEntryId()` 逐字比对页签文案，而
    `wfBadge()`（`报价首页.html:1604`）会把文案改写成 `待办任务 (N)`：node 实跑
    `techEntryId('待办任务 (28)') === 'mine'` —— 徽标一挂，待办页签就解析成「我的项目」；
  · 技术工艺待办渲染的是项目卡 `cardHtml()`（`.request-card`，无 `wf-task`），
    报价待办渲染的是任务卡 `taskCardHtml()`（`.request-card.wf-task`）；
  · 「已归档」目前是独立页签，摘掉页签后首页没有 `include_archived` 出口。

验证方式（行为为主，静态只用于 DOM / 脚本接线 / 路由存在性）：

  · node 侧：真加载 `tech-home-board.js`（`global.window = {}` + `require`），
    用真模块断言入口与待办过滤；用 `js_block()` 从 `报价首页.html` 取真函数
    （`techEntries` / `techEntryId` / `taskCardHtml` / `cardHtml`）实跑。
  · 后端：复用批次 2 的受控假库 `Workbench`（真跑 `cpq_wf` 的 SQL），
    断言 `inbox()` 只含「转交给我 / 定向我的角色 / 公共池 / 我已领取」。

Spec：docs/specs/tech-home-three-tabs-and-todo-tasks.md
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_quote_task_coexistence_and_atomic_claim_red import (  # noqa: E402
    CARD2_ID,
    SESSION2,
    FIN1_UID,
    PROC1_UID,
    PROC2_UID,
    SALES_UID,
    Workbench,
    js_block,
    read_text,
    run_node,
)

import cpq_wf  # noqa: E402

SPEC = ROOT / "docs" / "specs" / "tech-home-three-tabs-and-todo-tasks.md"
HOME_BOARD = ROOT / "tech_app" / "frontend" / "tech-home-board.js"
HOME_HTML = ROOT / "报价首页.html"

ME = {"user_id": "200", "role_code": "process_mgr"}
OTHER = {"user_id": "100", "role_code": "sales_mgr"}


# ---------------------------------------------------------------------------
# node 脚手架：真加载模块 + 真跑页面函数
# ---------------------------------------------------------------------------
def board_sandbox(body: str) -> str:
    """加载真 tech-home-board.js（window.TechHomeBoard）后执行 body。"""
    return (
        "global.window = {};\n"
        "var mem = {};\n"
        "global.window.localStorage = {\n"
        "  getItem: function (k) { return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null; },\n"
        "  setItem: function (k, v) { mem[k] = String(v); }\n"
        "};\n"
        f"require({json.dumps(str(HOME_BOARD))});\n"
        "var board = global.window.TechHomeBoard;\n"
        + body
    )


def page_function(header: str) -> str:
    return js_block(read_text(HOME_HTML), header)


def js_call(name: str, *args) -> str:
    return name + "(" + ", ".join(json.dumps(a, ensure_ascii=False) for a in args) + ")"


def js_json(expr: str) -> str:
    return "process.stdout.write(JSON.stringify(" + expr + "));\n"


def task(**over):
    """一条任务行（字段名与 cpq_wf._task_row 的返回一致：id 类字段全是字符串）。"""
    row = {
        "task_id": "5001",
        "task_no": "TP-00005001",
        "task_kind": "handoff",
        "status": "open",
        "status_label": "待领取",
        "target_type": "public",
        "target_role_code": "",
        "target_user_id": None,
        "claimed_by_user_id": None,
        "from_user_id": "100",
        "title": "测试报价",
        "customer": "客户A",
        "from_display_name": "SM1",
        "from_role_name": "销售经理",
        "next_step_no": 1,
        "next_step_name": "确认需求配置",
        "next_role_name": "工艺经理",
        "created_at": "2026-09-17T10:00:00",
        "session_id": "sess-b138-0001",
        "note": "",
        "cancel_reason": None,
        "replaced_by_task_no": None,
    }
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# A. 首页三页签
# ---------------------------------------------------------------------------
class HomeTabsTest(unittest.TestCase):
    """Spec §5.1：首页恰好三个页签。"""

    def entries(self):
        script = board_sandbox(js_json("board ? board.entries() : null"))
        return json.loads(run_node(script))

    def test_board_exposes_exactly_three_tabs(self):
        entries = self.entries()
        self.assertIsInstance(entries, list, "TechHomeBoard.entries() 必须返回数组")
        self.assertEqual(3, len(entries),
                         f"技术工艺首页必须恰好三个页签（Spec §5.1）：{[e.get('label') for e in entries]}")
        self.assertEqual(["mine", "todo", "all"], [e.get("id") for e in entries],
                         "页签 id 与顺序固定为 我的项目 → 待办任务 → 全部项目")
        self.assertEqual(["我的项目", "待办任务", "全部项目"], [e.get("label") for e in entries],
                         "页签文案固定（与报价 我的报价 / 待办任务 / 全部报价 同构）")
        self.assertEqual([], [e.get("id") for e in entries if e.get("id") in ("recent", "archived")],
                         "「最近访问」「已归档」不再是首页页签（Spec §5.1）")
        self.assertEqual([], [e.get("id") for e in entries if str(e.get("source")) == "local"],
                         "首页不得再有本机数据源页签（Spec §5.1）")

    def test_todo_tab_is_backed_by_tasks_not_a_project_scope(self):
        entries = {e.get("id"): e for e in self.entries()}
        todo = entries.get("todo") or {}
        self.assertEqual("tasks", str(todo.get("source") or ""),
                         "待办任务必须标注数据来源为任务（/wf/tasks），不是项目清单（Spec §5.1）")
        self.assertFalse(str(todo.get("scope") or "").strip(),
                         f"待办任务不得再携带项目 scope（Spec §5.1）：{todo}")
        self.assertEqual("mine", str((entries.get("mine") or {}).get("scope") or ""))
        self.assertEqual("all", str((entries.get("all") or {}).get("scope") or ""))


# ---------------------------------------------------------------------------
# B. 待办唯一口径：isTodoTask / todoTasks / todoCount
# ---------------------------------------------------------------------------
class TodoScopeTest(unittest.TestCase):
    """Spec §4：待办 = 别人转交给我 ∪ 定向我的角色 ∪ 公共池 ∪ 我已领取未完成。"""

    def board_result(self, tasks, user):
        body = (
            "var tasks = " + json.dumps(tasks, ensure_ascii=False) + ";\n"
            "var user = " + json.dumps(user, ensure_ascii=False) + ";\n"
            "var out = {\n"
            "  has_is: typeof board.isTodoTask === 'function',\n"
            "  has_tasks: typeof board.todoTasks === 'function',\n"
            "  has_count: typeof board.todoCount === 'function'\n"
            "};\n"
            "if (out.has_is) { out.flags = tasks.map(function (t) { return !!board.isTodoTask(t, user); }); }\n"
            "if (out.has_tasks) { out.kept = (board.todoTasks(tasks, user) || []).map(function (t) { return t.task_id; }); }\n"
            "if (out.has_count) { out.count = board.todoCount(tasks, user); }\n"
            "out.source_len = tasks.length;\n"
            + js_json("out")
        )
        return json.loads(run_node(board_sandbox(body)))

    def test_is_todo_task_matrix(self):
        tasks = [
            task(task_id="t-public"),                                                  # 0 ✓ 公共池
            task(task_id="t-role", target_type="role", target_role_code="process_mgr"),  # 1 ✓ 定向我的角色
            task(task_id="t-user", target_type="user", target_user_id="200"),            # 2 ✓ 定向给我
            task(task_id="t-other-role", target_type="role", target_role_code="finance_mgr"),  # 3 ✗
            task(task_id="t-other-user", target_type="user", target_user_id="999"),       # 4 ✗
            task(task_id="t-mine-open", target_type="public", from_user_id="200"),        # 5 ✗ 我发起的
            task(task_id="t-claimed-me", status="claimed", claimed_by_user_id="200"),     # 6 ✓ 我已领取
            task(task_id="t-claimed-other", status="claimed", claimed_by_user_id="100"),  # 7 ✗
            task(task_id="t-done", status="completed"),                                   # 8 ✗
            task(task_id="t-cancelled", status="cancelled"),                              # 9 ✗
            task(task_id="t-broken", target_type=None, status=None),                      # 10 ✗ 脏数据
            task(task_id="t-no-status", status=""),                                       # 11 ✗
        ]
        out = self.board_result(tasks, ME)
        self.assertTrue(out.get("has_is"), "必须导出 TechHomeBoard.isTodoTask（Spec §5.1）")
        self.assertEqual([True, True, True, False, False, False, True, False, False, False, False, False],
                         out.get("flags"), "待办判定矩阵必须与 Spec §4 逐条一致")

    def test_role_target_does_not_match_a_user_without_role(self):
        out = self.board_result(
            [task(task_id="t-role", target_type="role", target_role_code="process_mgr")],
            {"user_id": "200", "role_code": ""})
        self.assertTrue(out.get("has_is"), "必须导出 TechHomeBoard.isTodoTask（Spec §5.1）")
        self.assertEqual([False], out.get("flags"),
                         "没有角色码的用户不得命中定向角色的任务（Spec §5.1）")

    def test_todo_tasks_and_count_agree(self):
        tasks = [
            task(task_id="a"),
            task(task_id="b", status="completed"),
            task(task_id="c", target_type="role", target_role_code="process_mgr"),
            task(task_id="d", target_type="role", target_role_code="finance_mgr"),
            task(task_id="e", status="claimed", claimed_by_user_id="200"),
            task(task_id="f", status="claimed", claimed_by_user_id="100"),
            task(task_id="g", target_type="public", from_user_id="200"),
        ]
        out = self.board_result(tasks, ME)
        self.assertTrue(out.get("has_tasks"), "必须导出 TechHomeBoard.todoTasks（Spec §5.1）")
        self.assertTrue(out.get("has_count"), "必须导出 TechHomeBoard.todoCount（Spec §5.1）")
        self.assertEqual(["a", "c", "e"], out.get("kept"),
                         "todoTasks 必须保持原顺序、只留下真实待办")
        self.assertEqual(3, out.get("count"),
                         "todoCount 必须等于 todoTasks(...).length（页签徽标与列表同源）")
        self.assertEqual(7, out.get("source_len"),
                         "过滤不得就地改动入参数组（长度必须原样）")


# ---------------------------------------------------------------------------
# C. 技术工艺页签路由
# ---------------------------------------------------------------------------
class TechTabRoutingTest(unittest.TestCase):
    """Spec §5.2：页签解析、待办分流、归档可达。"""

    def test_tech_entry_id_resolves_a_badged_todo_tab(self):
        code = page_function("function techEntries() {") + "\n" + page_function("function techEntryId(tab) {")
        body = (
            code + "\n"
            "var out = {plain: techEntryId('待办任务'), badged: techEntryId('待办任务 (28)'),\n"
            "           all: techEntryId('全部项目'), mine: techEntryId('我的项目'),\n"
            "           unknown: techEntryId('别的页签')};\n"
            + js_json("out")
        )
        out = json.loads(run_node(board_sandbox(body)))
        self.assertEqual("todo", out.get("plain"))
        self.assertEqual("todo", out.get("badged"),
                         "页签文案被徽标改写成「待办任务 (28)」后仍必须解析成 todo（Spec §5.2）")
        self.assertEqual("all", out.get("all"))
        self.assertEqual("mine", out.get("mine"))
        self.assertEqual("mine", out.get("unknown"), "未知页签沿用既有兜底：我的项目")

    def test_todo_branch_is_not_quote_only(self):
        body = page_function("function renderCards() {")
        calls = re.findall(r"if\s*\((?P<cond>[^{}]*?)\)\s*\{\s*renderTaskTab\(", body, re.S)
        self.assertTrue(calls,
                        "renderCards 必须把「待办任务」分流到任务渲染器 renderTaskTab（Spec §5.2）")
        for cond in calls:
            self.assertNotIn("'quote'", cond.replace('"', "'"),
                             "待办任务分支不得再限定 mode === 'quote'：报价与技术工艺同构（Spec §5.2）")
        guard = " ".join(calls)
        self.assertIn("待办任务", guard,
                      "待办分流必须仍然按页签文案判断，不能误吞「我的 / 全部」（Spec §5.2）")

    def test_tech_mode_no_longer_serves_a_todo_project_list(self):
        page = read_text(HOME_HTML)
        self.assertIsNone(re.search(r"TECH_SCOPE_ROWS\s*=\s*\{[^}]*\btodo\s*:", page),
                          "技术工艺不得再缓存 scope=todo 的项目清单（Spec §5.2）")
        self.assertNotIn("techLoadScope('todo')", page,
                         "技术工艺不得再为待办加载项目清单（Spec §5.2）")
        if "function techScopedRows(entryId) {" in page:
            scoped = js_block(page, "function techScopedRows(entryId) {")
            self.assertNotIn("'todo'", scoped,
                             "techScopedRows 不得再把手办的 todo 当成项目 scope（Spec §5.2）")

    def test_archived_still_reachable_from_all_tab(self):
        page = read_text(HOME_HTML)
        self.assertTrue("include_archived" in page,
                        "摘掉「已归档」页签后，全部项目仍必须能读到归档项目"
                        "（/api/projects?scope=all&include_archived=true，Spec §5.2）")


# ---------------------------------------------------------------------------
# D. 待办卡片：与项目卡可区分，且两侧同一渲染器
# ---------------------------------------------------------------------------
class TodoCardShapeTest(unittest.TestCase):
    """Spec §3-4、§5.2：待办卡是任务卡，不是项目卡。"""

    def test_task_card_and_project_card_are_distinguishable(self):
        prelude = (
            "function escH(v) { return String(v == null ? '' : v); }\n"
            "function fmtTime() { return '刚刚'; }\n"
            "function techCardStage() { return null; }\n"
            "function statusOf() { return ['处理中', 'status-draft']; }\n"
            "function techCardMeta() { return ''; }\n"
            "function quoteNo() { return 'TP-20260917-0001'; }\n"
        )
        script = (
            board_sandbox("")
            + prelude
            + page_function("function taskCardHtml(t) {") + "\n"
            + page_function("function cardHtml(mode, s) {") + "\n"
            + "var task = " + json.dumps(task(), ensure_ascii=False) + ";\n"
            + "var proj = {id: 'p1', title: '上壳组件', project: '上壳组件',"
              " updated: '2026-09-18T10:00:00', turns: 2};\n"
            + "var out = {task: taskCardHtml(task), project: cardHtml('tech', proj)};\n"
            + js_json("out")
        )
        out = json.loads(run_node(script))
        self.assertIn("wf-task", out["task"],
                      "待办任务必须渲染成任务卡（.request-card.wf-task，Spec §3-4）")
        self.assertIn("request-card", out["task"])
        self.assertNotIn("wf-task", out["project"],
                         "项目卡不得挂上任务卡标记：两种卡必须能区分（Spec §5.2）")
        self.assertIn("request-card", out["project"])
        for token in ("来自", "第 1 步", "领取并继续"):
            self.assertTrue(token in out["task"],
                            f"任务卡必须保留来源 / 步骤 / 领取入口（Spec §15）：缺 {token}")

    def test_quote_and_tech_todo_share_one_count_source(self):
        page = read_text(HOME_HTML)
        hit = re.search(r"TechHomeBoard[\s\S]{0,60}?todoCount|todoCount[\s\S]{0,60}?TechHomeBoard",
                        page)
        self.assertTrue(hit is not None,
                        "待办数字必须来自 TechHomeBoard.todoCount"
                        "（报价与技术工艺同一口径，Spec §5.2）")


# ---------------------------------------------------------------------------
# E. 后端来源：/wf/tasks（cpq_wf.inbox）就是待办
# ---------------------------------------------------------------------------
class InboxSourceTest(unittest.TestCase):
    """Spec §4：inbox 只含转交给我 / 定向我的角色 / 公共池 / 我已领取。"""

    def test_inbox_holds_exactly_what_was_handed_to_me(self):
        wb = Workbench()
        with wb:
            # 同一卡片上「同 kind 互斥、异 kind 并存」是批次 2 的既有契约，
            # 所以这里用四个 task_kind 铺开，避免用例自己互相替代（脚手架自检）。
            public = wb.send(SALES_UID, "handoff", "public")
            to_me = wb.send(SALES_UID, "tech_new_product", "user", target_user=PROC1_UID)
            to_role = wb.send(SALES_UID, "tech_cost_return", "role", role="process_mgr")
            to_other = wb.send(SALES_UID, "tech_cost", "user", target_user=PROC2_UID)
            mine_open = wb.send(PROC1_UID, "handoff", "public", session=SESSION2)
            ids = {str(r["task_id"]) for r in cpq_wf.inbox(wb.user(PROC1_UID))}
            for label, row in (("公共池", public), ("定向给我", to_me), ("定向我的角色", to_role)):
                self.assertIn(str(row["task_id"]), ids,
                              f"{label}的任务必须在收件人的待办里（Spec §4）")
            self.assertNotIn(str(to_other["task_id"]), ids,
                             "定向别人的任务不得出现在我的待办里（Spec §4）")
            self.assertNotIn(str(mine_open["task_id"]), ids,
                             "我自己发起、还没人领取的任务不是我的待办（Spec §4）")

            cpq_wf.claim_task(str(to_me["task_id"]), wb.user(PROC1_UID))
            claimed = {str(r["task_id"]) for r in cpq_wf.inbox(wb.user(PROC1_UID))}
            self.assertIn(str(to_me["task_id"]), claimed,
                          "我已领取未完成的任务仍留在我的待办里（Spec §4）")

    def test_terminal_tasks_are_not_todos(self):
        wb = Workbench()
        with wb:
            for idx, status in ((9001, "completed"), (9002, "cancelled")):
                wb.db.seed("cpq_wf_task", task_id=idx, card_id=1, from_user_id=SALES_UID,
                           from_step_no=1, target_type="public", target_role_code="",
                           status=status, task_kind="handoff", created_at="2026-01-01",
                           source_label="历史任务", note="")
            ids = {str(r["task_id"]) for r in cpq_wf.inbox(wb.user(PROC1_UID))}
            self.assertNotIn("9001", ids, "已完成的任务不是待办（Spec §4）")
            self.assertNotIn("9002", ids, "已撤回的历史任务不是待办（Spec §4）")

    def test_project_ownership_is_not_a_todo(self):
        wb = Workbench()
        with wb:
            wb.db.seed("cpq_wf_card", card_id=CARD2_ID + 1, session_id="sess-b138-only-mine",
                       assistant_type="quote", title="我自己建的项目", customer="客户B",
                       project_name="项目B", current_step=1, overall_status="in_progress",
                       creator_user_id=PROC1_UID, current_owner=PROC1_UID,
                       created_at="2026-09-17T10:00:00", updated_at="2026-09-17T10:00:00")
            rows = cpq_wf.inbox(wb.user(PROC1_UID))
            self.assertEqual([], rows,
                             "待办只认「有人转交给我的任务」：我自己名下、没有任务的项目"
                             "不得出现在待办里（Spec §4，正是 28 条那条错误口径）")


if __name__ == "__main__":
    unittest.main()
