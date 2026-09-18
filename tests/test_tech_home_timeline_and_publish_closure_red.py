"""红测：统一首页信息架构、跨流程时间线与报告发布收口（批次 10）。

用户口径（批次 10 原文要点）：

  · 首页信息架构固定为 我的项目 / 全部项目 / 待办任务 / 最近访问 / 已归档
    （**本批 ## 139 已取代**：首页改回三页签，见下方 HomeBoardModuleTest 的说明）；
  · 历史 Drawer 不能成为找回首页遗漏业务项目的补救入口；
  · 最近访问不是业务数据的事实源；待办只显示需要当前用户执行的动作；
  · 项目卡显示 owner、当前阶段、等待谁、最后业务事件、是否有异常；点击能恢复真实 project/stage/task；
  · 一条统一业务时间线，覆盖报价创建 → … → 回传报价 → 销售继续报价，每条事件带
    actor / role / at / action / business_case_id / project_id|session_id|task_id /
    from_state / to_state / version / 可跳转目标；
  · 报告发布成功后必须明确显示：报告已发布 / 分发已留痕 / 是否已回传报价 / 回传到哪张报价 /
    报价当前步骤 / 回传失败时的重试入口；
  · 主操作随来源变化（来自报价 → 返回原报价继续；独立技术项目 → 查看已发布报告）；
  · 系统内收件人（账号/角色，产生消息）与外部分发对象（自由文本，仅留痕）必须区分。

现状缺口（只读实测，均已定位；断言都是行为与状态，不是文本搜索）：

  · `GET /api/projects`（main.py:1016）的 scope 只认 mine / all / archived，**没有 todo**；
    每一行只有批次 7 的 `access` 块，**没有** `card`；
  · `报价首页.html:1558` 的标签是 ['我的清单','待办任务','全部清单']：没有「最近访问」、
    没有「已归档」；卡片状态由 `报价首页.html:1752` 的 `statusOf()` 本地映射表拼出，
    是批次 5B 流程投影之外的第二套口径；
  · `GET /api/projects/{project_id}/timeline` **不存在**；`store.audit()`（store.py:101-106）
    只写 {ts, action, detail}，没有 actor / role / 状态迁移 / 跳转目标；
    `GET /api/projects/{project_id}/agent/events` 是 Agent 会话流，不是业务时间线；
  · `report_workflow.publish_result()`（report_workflow.py:584）返回 {report, versions,
    quote_handoff}，**没有** closure：没有分发留痕、没有「回传到哪张报价第几步」、
    没有回传失败的重试入口、没有随来源变化的主操作；
  · `tech_app/frontend/tech-home-board.js` **不存在**：首页没有唯一的状态渲染口径模块。

验证方式（行为为主，绝不连线上 PG）：

  · HTTP 级：子进程 + 临时 DATA_DIR + TestClient + CPQ_SSO=true + 打桩 cpq_sso.resolve
    （三张票 = 三个账号），真 store 建项目 / IR / business_case / 失败任务 / 已发布报告 /
    整机计划，再逐接口断言结构、状态与只读性。
  · 前端级：node 跑新增的 tech-home-board.js（只桩 window / localStorage），
    断言入口集合（**## 139 起为三页签**：我的项目 / 待办任务 / 全部项目）、
    cardOf 原样透出、stageText/waitingText 不回落到本地映射表、
    rememberRecent 只写 localStorage。

Spec：docs/specs/tech-home-timeline-and-publish-closure.md
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DATA_DIR = tempfile.mkdtemp(prefix="cpq-b10-data-")
os.environ["DATA_DIR"] = _DATA_DIR
os.environ.setdefault("AUTH_ENABLED", "false")

SPEC = ROOT / "docs" / "specs" / "tech-home-timeline-and-publish-closure.md"
HOME_BOARD = ROOT / "tech_app" / "frontend" / "tech-home-board.js"
HOME_HTML = ROOT / "报价首页.html"

BC = "bc_0f1e2d3c4b5a"
NOT_FOUND_DETAIL = "项目不存在"

CARD_KEYS = ("owner", "owner_display_name", "stage", "waiting_for", "last_event",
             "anomaly", "business_case_id", "primary_action")
STAGE_KEYS = ("code", "index", "total", "title", "phase")
EVENT_KEYS = ("seq", "at", "actor", "actor_display", "role", "action", "label",
              "action_kind", "from_state", "to_state", "project_id", "session_id",
              "task_id", "version", "business_case_id", "target")
CLOSURE_KEYS = ("state", "published", "published_at", "published_by", "version",
                "distributed", "handoff", "primary_action", "anomaly")
DISTRIBUTED_KEYS = ("recorded", "scope", "cc", "internal_recipients", "external_targets")
TIMELINE_REQUIRED_ACTIONS = ("quote_created", "tech_branch_started", "drawing_parsed",
                            "task_failed", "report_published")
KNOWN_ACTION_KINDS = {"quote", "task", "tech", "cost", "report", "handoff"}
SECRET_MARKERS = ("Bearer ", "/Users/", "base64,", "sk-", "data:image")


class SpecPinnedTest(unittest.TestCase):
    def test_spec_exists_and_pins_contract(self):
        self.assertTrue(SPEC.exists(), f"缺少 Spec：{SPEC}")
        text = SPEC.read_text(encoding="utf-8")
        for token in ("scope=mine|all|todo|archived", "/timeline", "closure",
                      "internal_recipients", "external_targets", "tech-home-board.js",
                      "最近访问", "已归档", "card"):
            self.assertTrue(token in text, f"Spec 缺少关键契约：{token}")

    def test_home_html_loads_the_board_module(self):
        """静态契约：首页必须加载唯一口径模块（脚本加载顺序，Spec §7.4）。"""
        self.assertTrue(HOME_HTML.exists(), f"缺少首页：{HOME_HTML}")
        html = HOME_HTML.read_text(encoding="utf-8")
        self.assertTrue("tech-home-board.js" in html,
                        "首页必须加载 tech_app/frontend/tech-home-board.js（Spec §7.4）")

    # 这条断言会读它自己的源码，所以旧编号在这里必须拼出来（否则这行字自己就把断言判死）；
    # 断言本身一个字没变：SPEC 与本文件都不得再出现旧的成本编号写法。
    OLD_COST_NO = "2.3" + " " + "成本"

    def test_five_phase_wording_only(self):
        """口径统一：成本测算是第 4 阶段（4.1/4.2/4.3），不得再写旧的第二步成本编号。"""
        for path in (SPEC, pathlib.Path(__file__)):
            text = path.read_text(encoding="utf-8")
            self.assertTrue("4.1" in text and "4.2" in text and "4.3" in text,
                            f"{path.name} 必须使用五阶段口径（4.1 零件成本 / 4.2 组装成本 / 4.3 汇总）")
            self.assertTrue(self.OLD_COST_NO not in text,
                            f"{path.name} 不得再出现旧编号的成本写法")


CHILD = r'''
import json
import os
import sys
import types

data_dir, root, case = sys.argv[1], sys.argv[2], sys.argv[3]
variant = sys.argv[4] if len(sys.argv) > 4 else ""
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
os.environ["CPQ_SSO"] = "true"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

from fastapi.testclient import TestClient

from tech_app.backend import main
from tech_app.backend.services import cpq_sso
from tech_app.backend.storage import store
from tech_app.backend.models.ir import DesignIR


def U(name, role, code, label, display=""):
    return {"username": name, "role": role, "display_name": display or name, "user_id": name,
            "cpq_role_code": code, "cpq_role_name": label, "is_system": False}


ALICE = U("alice", "engineer", "process_mgr", "工艺经理", "爱丽丝")
PM = U("pm", "process_manager", "process_mgr", "工艺经理", "工艺经理")
LOOKER = U("looker", "viewer", "", "", "路人")
TOK = {"A": ALICE, "PM": PM, "V": LOOKER}
cpq_sso.resolve = lambda token: TOK.get((token or "").strip())

BC = "bc_0f1e2d3c4b5a"
P1 = str(store.create_project(source_filename="a.png", source_bytes=b"a",
                              note="批次10 探针", owner="alice"))
store.save_ir(P1, DesignIR(device_name="便携式锂电池 PACK", design_intent="探针",
                           parts=[{"part_id": "P-001", "name": "上壳",
                                   "quantity": 1}]).model_dump(),
              stage="parsed", author="tester")
store.save_business_case(P1, {"business_case_id": BC, "quote_session_id": "S-2026-0007",
                              "source_task_id": "T-1"}, author="tester")
store.save_task(P1, {"task_id": "t-failed-1", "label": "生成工程图",
                     "status": "failed", "progress": "失败", "error": "CAD 不可用",
                     "created_at": "2026-09-15 10:00:00",
                     "finished_at": "2026-09-15 10:01:00"})

PUBLISHED = {"project_id": P1, "report_no": "RPT-2026-0007", "status": "published",
             "version": 1, "title": "工艺评估报告", "published_by": "pm",
             "published_at": "2026-09-17 11:20:00",
             "distribution_scope": "工艺、质量、生产", "distribution_cc": "项目组",
             "recipients": [
                 {"name": "zhangzhen", "channel": "平台通知"},
                 {"name": "财务经理", "channel": "平台通知"},
                 {"name": "张三", "contact": "zhangsan@example.com", "channel": "邮件"}]}

HANDOFF_OK = {"session_id": "S-2026-0007", "next_step_no": 3,
              "next_step_name": "定价-利润加成", "target_role_name": "销售经理",
              "task_id": "T-Q-9", "sent_at": "2026-09-17 12:00:00", "sent_by": "pm"}

P2 = str(store.create_project(source_filename="b.png", source_bytes=b"b",
                              note="已归档", owner="bob"))
store.archive_project(P2, author="tester")

P3 = str(store.create_project(source_filename="c.png", source_bytes=b"c",
                              note="独立技术项目", owner="alice"))

store.save_process_report(P1, PUBLISHED, author="tester")
store.save_integration(P1, {"project_id": P1, "quote_handoff": HANDOFF_OK}, author="tester")

if variant == "unpublished":
    doc = dict(PUBLISHED)
    doc["status"] = "draft"
    doc["published_by"] = None
    doc["published_at"] = None
    store.save_process_report(P1, doc, author="tester")
    store.save_integration(P1, {"project_id": P1}, author="tester")
elif variant == "published_no_handoff":
    store.save_integration(P1, {"project_id": P1}, author="tester")
elif variant == "handoff_failed":
    plan = {"project_id": P1, "quote_handoff": dict(HANDOFF_OK)}
    plan["quote_handoff_error"] = "回传报价失败：业务数据库暂不可用"
    store.save_integration(P1, plan, author="tester")
elif variant == "independent":
    store.save_business_case(P1, {"business_case_id": BC, "quote_session_id": "",
                                  "source_task_id": ""}, author="tester")
    store.save_integration(P1, {"project_id": P1}, author="tester")

client = TestClient(main.app)
H = {key: {"Authorization": "Bearer " + key} for key in TOK}


def rows_of(scope, token):
    url = "/api/projects" if not scope else "/api/projects?scope=" + scope
    resp = client.get(url, headers=H[token])
    try:
        body = resp.json()
    except Exception:
        body = None
    return resp.status_code, body


def ids_of(body):
    if not isinstance(body, list):
        return []
    return [str((row or {}).get("project_id")) for row in body if isinstance(row, dict)]


out = {"p1": P1, "p2": P2, "p3": P3, "case": case, "variant": variant}

if case == "cards":
    for scope in ("", "mine", "all", "todo", "archived"):
        status, body = rows_of(scope, "PM")
        out["scope_" + (scope or "default") + "_status"] = status
        out["scope_" + (scope or "default") + "_ids"] = ids_of(body)
        out["scope_" + (scope or "default") + "_is_list"] = isinstance(body, list)
        out["scope_" + (scope or "default") + "_rows"] = body if isinstance(body, list) else []
    out["bad_scope_status"] = client.get("/api/projects?scope=nonsense", headers=H["PM"]).status_code
    out["no_token_status"] = client.get("/api/projects").status_code
    _, eng_all = rows_of("all", "A")
    eng_todo_status, eng_todo = rows_of("todo", "A")
    out["engineer_all_ids"] = ids_of(eng_all)
    out["engineer_todo_ids"] = ids_of(eng_todo)
    out["engineer_todo_status"] = eng_todo_status
    out["engineer_todo_rows"] = eng_todo if isinstance(eng_todo, list) else []
    proj = client.get("/api/projects/%s/workflow/projection" % P1, headers=H["PM"])
    out["projection_status"] = proj.status_code
    out["projection"] = proj.json() if proj.status_code == 200 else None
    out["detail_status"] = client.get("/api/projects/%s" % P1, headers=H["V"]).status_code
    out["project_list_status"] = client.get("/api/projects?scope=all", headers=H["PM"]).status_code

elif case == "timeline":
    resp = client.get("/api/projects/%s/timeline" % P1, headers=H["PM"])
    out["status"] = resp.status_code
    try:
        out["body"] = resp.json()
    except Exception:
        out["body"] = None
    resp2 = client.get("/api/projects/%s/timeline" % P1, headers=H["PM"])
    out["status2"] = resp2.status_code
    try:
        out["body2"] = resp2.json()
    except Exception:
        out["body2"] = None
    unseen = client.get("/api/projects/%s/timeline" % P1, headers=H["V"])
    out["unseen_status"] = unseen.status_code
    try:
        out["unseen_body"] = unseen.json()
    except Exception:
        out["unseen_body"] = None
    missing = client.get("/api/projects/%s/timeline" % ("0" * 12), headers=H["PM"])
    out["missing_status"] = missing.status_code
    try:
        out["missing_body"] = missing.json()
    except Exception:
        out["missing_body"] = None
    legacy = client.get("/api/projects/%s/timeline" % P3, headers=H["A"])
    out["legacy_status"] = legacy.status_code
    try:
        out["legacy_body"] = legacy.json()
    except Exception:
        out["legacy_body"] = None

elif case == "closure":
    resp = client.get("/api/projects/%s/process-report/publish-result" % P1, headers=H["PM"])
    out["status"] = resp.status_code
    try:
        out["body"] = resp.json()
    except Exception:
        out["body"] = None
    unseen = client.get("/api/projects/%s/process-report/publish-result" % P1, headers=H["V"])
    out["unseen_status"] = unseen.status_code
    try:
        out["unseen_body"] = unseen.json()
    except Exception:
        out["unseen_body"] = None

elif case == "readonly":
    def snapshot():
        return {
            "meta": json.dumps(store.load_meta(P1), sort_keys=True, ensure_ascii=False, default=str),
            "report": json.dumps(store.load_process_report(P1), sort_keys=True,
                                 ensure_ascii=False, default=str),
            "tasks": json.dumps(store.list_tasks(P1), sort_keys=True, ensure_ascii=False, default=str),
            "ir": json.dumps(store.load_ir(P1), sort_keys=True, ensure_ascii=False, default=str),
        }

    before = snapshot()
    for url in ("/api/projects", "/api/projects?scope=all", "/api/projects?scope=todo",
                "/api/projects?scope=archived",
                "/api/projects/%s/timeline" % P1,
                "/api/projects/%s/process-report/publish-result" % P1):
        client.get(url, headers=H["PM"])
    after = snapshot()
    out["before"] = before
    out["after"] = after

print(json.dumps(out, ensure_ascii=False, default=str))
'''


class NodeCase(unittest.TestCase):
    def run_child(self, case, variant=""):
        data_dir = tempfile.mkdtemp(prefix="cpq-b10-http-")
        script_dir = tempfile.mkdtemp(prefix="cpq-b10-script-")
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        env = dict(os.environ)
        env["DATA_DIR"] = data_dir
        env["AUTH_ENABLED"] = "false"
        env["CPQ_SSO"] = "true"
        env["PYTHONPATH"] = str(ROOT)
        args = [sys.executable, str(script), data_dir, str(ROOT), case]
        if variant:
            args.append(variant)
        completed = subprocess.run(args, capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertEqual(0, completed.returncode,
                         f"子进程失败：{completed.stderr[-1200:]}")
        return json.loads(completed.stdout.strip().splitlines()[-1])


def card_in(rows, pid):
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("project_id")) == pid:
            # 行找到了就把 card 原样交出去（可能是 None）—— 断言必须能分清
            # 「这行不在列表里」与「这行没有 card 块」。
            return row.get("card")
    return "ROW_NOT_FOUND"


class HomeCardTest(NodeCase):
    """10A（## 139 起为三页签）：首页入口与后端卡片摘要。"""

    def test_four_scopes_are_served_and_invalid_scope_still_400(self):
        out = self.run_child("cards")
        for key in ("default", "mine", "all", "todo", "archived"):
            self.assertEqual(200, out["scope_" + key + "_status"],
                             f"scope={key} 必须 200（Spec §7.1，todo 是本批新增）")
            self.assertTrue(out["scope_" + key + "_is_list"],
                            f"scope={key} 必须仍返回 list（既有消费方）")
        self.assertEqual(400, out["bad_scope_status"], "非法 scope 仍必须 400")
        self.assertEqual(401, out["no_token_status"], "没票必须 401（既有行为）")

    def test_every_row_carries_the_card_block(self):
        out = self.run_child("cards")
        seen = 0
        for key in ("mine", "all", "todo", "archived"):
            for row in out["scope_" + key + "_rows"]:
                seen += 1
                card = row.get("card")
                self.assertTrue(isinstance(card, dict),
                                f"scope={key} 的每一行都必须带 card 块（Spec §7.1）：{row}")
                for field in CARD_KEYS:
                    self.assertTrue(field in card, f"card 缺少 {field}：{card}")
                self.assertTrue(isinstance(row.get("access"), dict),
                                "既有的 access 块一个都不能删（Spec §16）")
        self.assertGreaterEqual(seen, 4, "卡片断言不能空转：至少要覆盖几个 scope 的若干行")

    def test_card_stage_is_the_same_source_as_workflow_projection(self):
        out = self.run_child("cards")
        self.assertEqual(200, out["projection_status"], "workflow/projection 必须仍可用")
        projection = out["projection"] or {}
        next_action = projection.get("next_action") or {}
        stages = projection.get("stages") or []
        expected = str(next_action.get("sub") or "")
        if not expected and stages:
            expected = str((stages[-1] or {}).get("sub") or "")
        self.assertTrue(expected, f"投影必须给出当前子步骤：{projection}")
        card = card_in(out["scope_all_rows"], out["p1"])
        self.assertTrue(isinstance(card, dict),
                        f"项目必须出现在 scope=all 里且每一行都必须带 card 块：{card}")
        stage = card.get("stage") or {}
        for field in STAGE_KEYS:
            self.assertTrue(field in stage, f"card.stage 缺少 {field}：{stage}")
        self.assertEqual(expected, str(stage.get("code")),
                         "card.stage.code 必须与工作台投影的当前子步骤逐字相同（Spec §14.3）")

    def test_mine_all_todo_exclude_archived_and_archived_only_has_archived(self):
        out = self.run_child("cards")
        self.assertIn(out["p2"], set(out["scope_archived_ids"]),
                      "已归档项目必须出现在 scope=archived")
        for key in ("default", "mine", "all", "todo"):
            self.assertNotIn(out["p2"], set(out["scope_" + key + "_ids"]),
                             f"scope={key} 不得含归档项目（Spec §14.4）")

    def test_todo_only_lists_my_actionable_work(self):
        out = self.run_child("cards")
        self.assertTrue(set(out["scope_todo_ids"]) <= set(out["scope_all_ids"]),
                        "待办必须是「全部」的子集")
        self.assertIn(out["p1"], set(out["scope_todo_ids"]),
                      "投影的当前可执行步骤 1.1「创建需求」归工艺工程师，"
                      "工艺经理也能动手 -> 该项目必须出现在他的待办里（断言不得空转）")
        self.assertTrue(out["scope_todo_rows"], "待办断言不能空转")
        for row in out["scope_todo_rows"]:
            card = row.get("card") or {}
            self.assertTrue(card.get("primary_action"),
                            f"待办行必须有可执行动作（Spec §6.2）：{card}")

    def test_engineer_todo_is_a_subset_of_engineer_all(self):
        out = self.run_child("cards")
        self.assertEqual(200, out["engineer_todo_status"], "scope=todo 对工程师也必须 200")
        self.assertTrue(set(out["engineer_todo_ids"]) <= set(out["engineer_all_ids"]),
                        "换个账号也必须成立：待办 ⊆ 全部")
        self.assertIn(out["p1"], set(out["engineer_todo_ids"]),
                      "1.1 归工艺工程师 -> 该项目必须出现在工程师的待办里（断言不得空转）")
        for row in out["engineer_todo_rows"]:
            card = row.get("card") or {}
            self.assertTrue(card.get("primary_action"), f"待办行必须有可执行动作：{card}")

    def test_rows_are_sorted_by_last_event_desc(self):
        out = self.run_child("cards")
        stamps = []
        for row in out["scope_all_rows"]:
            card = row.get("card") or {}
            event = card.get("last_event") or {}
            stamps.append(str(event.get("at") or ""))
        self.assertGreaterEqual(len(stamps), 2, "排序断言不能空转")
        self.assertTrue(any(stamps), "每一行都必须带 last_event.at，否则排序断言是空转")
        self.assertEqual(sorted(stamps, reverse=True), stamps,
                         "列表必须按 last_event.at 降序（Spec §7.1）")

    def test_anomaly_is_true_when_a_task_failed(self):
        out = self.run_child("cards")
        card = card_in(out["scope_all_rows"], out["p1"])
        self.assertTrue(isinstance(card, dict),
                        f"项目必须出现在 scope=all 里且每一行都必须带 card 块：{card}")
        anomaly = card.get("anomaly") or {}
        self.assertIs(True, anomaly.get("has_anomaly"),
                      f"存在 failed 任务时卡片必须标异常（Spec §14.7）：{anomaly}")
        self.assertTrue(list(anomaly.get("codes") or []), "异常必须给出机器码")

    def test_card_owner_matches_project_owner(self):
        out = self.run_child("cards")
        rows = out["scope_all_rows"]
        self.assertTrue(rows, "卡片断言不能空转")
        for row in rows:
            card = row.get("card") or {}
            self.assertEqual(str(row.get("owner") or ""), str(card.get("owner") or ""),
                             "卡片 owner 必须是项目 owner，不是当前登录人（Spec §14.2）")

    def test_default_scope_is_mine(self):
        out = self.run_child("cards")
        self.assertEqual(out["scope_mine_ids"], out["scope_default_ids"],
                         "不带 scope 时默认就是 mine（既有行为）")


class TimelineTest(NodeCase):
    """10B：统一业务时间线。"""

    def timeline(self):
        out = self.run_child("timeline")
        self.assertEqual(200, out["status"],
                         f"GET /api/projects/{{pid}}/timeline 必须存在（Spec §7.2）：{out.get('body')}")
        body = out["body"] or {}
        self.assertTrue(isinstance(body.get("events"), list),
                        f"timeline 必须返回 events 数组：{body}")
        return out, body

    def test_every_event_has_the_required_keys_and_values(self):
        _, body = self.timeline()
        events = body["events"]
        self.assertTrue(events, "真跑过的项目时间线不能为空")
        for event in events:
            for field in EVENT_KEYS:
                self.assertTrue(field in event, f"事件缺少 {field}：{event}")
            for field in ("actor", "role", "at", "action", "label"):
                self.assertTrue(str(event.get(field) or "").strip(),
                                f"事件的 {field} 不得为空：{event}")
            target = event.get("target") or {}
            self.assertTrue(str(target.get("url") or "").strip(),
                            f"事件必须能点进去（target.url）：{event}")
            self.assertIn(str(event.get("action_kind") or ""), KNOWN_ACTION_KINDS,
                          f"action_kind 取值不合法：{event}")

    def test_actions_cover_what_actually_happened(self):
        _, body = self.timeline()
        codes = {str(event.get("action") or "") for event in body["events"]}
        for action in TIMELINE_REQUIRED_ACTIONS:
            self.assertIn(action, codes,
                          f"真实发生过的动作必须出现在时间线里：缺 {action}（Spec §14.9）")

    def test_events_are_ordered_and_sequential(self):
        _, body = self.timeline()
        events = body["events"]
        ats = [str(event.get("at") or "") for event in events]
        self.assertEqual(sorted(ats), ats, "时间线必须按 at 升序（Spec §7.2）")
        seqs = [event.get("seq") for event in events]
        self.assertEqual(list(range(1, len(seqs) + 1)), seqs,
                         f"seq 必须是从 1 开始的连续序号：{seqs}")

    def test_timeline_is_read_only_and_idempotent(self):
        out, body = self.timeline()
        self.assertEqual(200, out["status2"], "第二次调用必须仍然 200")
        other = out["body2"] or {}

        def strip(payload):
            return json.dumps({k: v for k, v in (payload or {}).items()
                               if k not in ("generated_at", "refreshed_at")},
                              sort_keys=True, ensure_ascii=False, default=str)

        self.assertEqual(strip(body), strip(other),
                         "连续两次调用（除 generated_at）必须逐字相同（Spec §14.10）")

    def test_business_case_id_is_surfaced_and_never_invented(self):
        out, body = self.timeline()
        self.assertEqual(BC, str(body.get("business_case_id") or ""), "顶层必须透出业务实例号")
        values = {str(event.get("business_case_id") or "") for event in body["events"]}
        self.assertTrue(values <= {"", BC}, f"事件不得现编业务实例号：{values}")
        self.assertIn(BC, values, "至少有一条事件携带业务实例号")
        self.assertEqual(200, out["legacy_status"],
                         f"没有 business_case 的历史项目也必须能读时间线：{out.get('legacy_body')}")
        legacy = out["legacy_body"] or {}
        self.assertEqual("", str(legacy.get("business_case_id") or ""),
                         "历史项目不得现编业务实例号（Spec §13）")

    def test_unseen_project_is_indistinguishable_from_missing(self):
        out = self.run_child("timeline")
        self.assertEqual(404, out["unseen_status"], "无权项目必须 404（批次 7）")
        self.assertEqual(404, out["missing_status"], "不存在的项目必须 404")
        self.assertEqual(NOT_FOUND_DETAIL, str((out["unseen_body"] or {}).get("detail") or ""))
        self.assertEqual(NOT_FOUND_DETAIL, str((out["missing_body"] or {}).get("detail") or ""))
        self.assertEqual(sorted(out["unseen_body"] or {}), sorted(out["missing_body"] or {}),
                         "两种 404 的字段名集合必须相同（不可区分）")

    def test_timeline_carries_no_secrets(self):
        _, body = self.timeline()
        blob = json.dumps(body, ensure_ascii=False, default=str)
        for marker in SECRET_MARKERS:
            self.assertTrue(marker not in blob,
                            f"时间线不得携带令牌 / 绝对路径 / 图片块：命中 {marker}")


class PublishClosureTest(NodeCase):
    """10C：报告发布收口。"""

    def closure_of(self, variant=""):
        out = self.run_child("closure", variant)
        self.assertEqual(200, out["status"], f"publish-result 必须可用：{out.get('body')}")
        body = out["body"] or {}
        return out, body, (body.get("closure") or {})

    def test_existing_keys_are_kept_and_closure_is_added(self):
        _, body, closure = self.closure_of()
        for key in ("report", "versions", "quote_handoff"):
            self.assertTrue(key in body, f"既有键 {key} 不能删（Spec §16）")
        self.assertTrue(isinstance(closure, dict) and closure, "必须新增 closure 块（Spec §7.3）")
        for field in CLOSURE_KEYS:
            self.assertTrue(field in closure, f"closure 缺少 {field}：{closure}")

    def test_unpublished_state(self):
        _, _, closure = self.closure_of("unpublished")
        self.assertIs(False, closure.get("published"), "未发布时 published 必须是 false")
        self.assertIn(str(closure.get("state") or ""), {"draft", "awaiting_review", "approved"},
                      f"未发布时的 state 不合法：{closure.get('state')}")
        handoff = closure.get("handoff") or {}
        self.assertIs(False, handoff.get("sent"), "未发布时不得声称已回传")
        self.assertEqual("", str(handoff.get("error") or ""), "没试过就不算失败")
        self.assertIsNone(handoff.get("retry_action"), "没试过就没有重试入口")
        self.assertTrue(closure.get("primary_action"), "未发布也必须给下一步")

    def test_published_without_handoff_state(self):
        _, _, closure = self.closure_of("published_no_handoff")
        self.assertIs(True, closure.get("published"), "已发布时 published 必须为 true")
        self.assertEqual("published", str(closure.get("state") or ""),
                         f"已发布未回传的 state 必须是 published：{closure.get('state')}")
        handoff = closure.get("handoff") or {}
        self.assertIs(False, handoff.get("sent"), "还没回传时 sent 必须是 false")
        self.assertEqual("", str(handoff.get("error") or ""), "还没试过不叫失败")
        self.assertIsNone(handoff.get("retry_action"), "还没试过就没有重试入口")
        self.assertEqual("view-report", str((closure.get("primary_action") or {}).get("id") or ""),
                         "已发布未回传的主操作是查看已发布报告（Spec §7.3 规则 3）")

    def test_handoff_failed_state_carries_error_and_retry(self):
        _, _, closure = self.closure_of("handoff_failed")
        self.assertIs(True, closure.get("published"), "回传失败不影响「报告已发布」")
        self.assertEqual("handoff_failed", str(closure.get("state") or ""),
                         f"回传失败的 state 必须是 handoff_failed：{closure.get('state')}")
        handoff = closure.get("handoff") or {}
        self.assertIs(False, handoff.get("sent"), "失败时 sent 必须是 false")
        self.assertTrue(str(handoff.get("error") or "").strip(),
                        "失败必须给出原因（Spec §7.3 规则 4）")
        retry = handoff.get("retry_action") or {}
        self.assertTrue(retry, f"必须给出重试入口：{handoff}")
        self.assertTrue(str(retry.get("label") or "").strip(), "重试入口必须有文案")
        anomaly = closure.get("anomaly") or {}
        self.assertIs(True, anomaly.get("has_anomaly"), "回传失败必须标异常")
        self.assertIn("handoff_failed", list(anomaly.get("codes") or []),
                      f"异常码必须含 handoff_failed：{anomaly}")

    def test_handed_off_state_points_at_the_target_quote(self):
        _, _, closure = self.closure_of()
        self.assertEqual("handed_off", str(closure.get("state") or ""),
                         f"回传成功的 state 必须是 handed_off：{closure.get('state')}")
        handoff = closure.get("handoff") or {}
        self.assertIs(True, handoff.get("sent"), "成功时 sent 必须是 true")
        target = handoff.get("target_quote") or {}
        self.assertEqual("S-2026-0007", str(target.get("session_id") or ""),
                         "必须写出回传到哪张报价")
        self.assertGreaterEqual(int(target.get("current_step") or 0), 2,
                                f"必须写出报价当前步骤：{target}")
        self.assertTrue(str(target.get("current_step_label") or "").strip(),
                        f"报价步骤必须有中文名：{target}")

    def test_recipient_sets_are_disjoint(self):
        _, _, closure = self.closure_of()
        distributed = closure.get("distributed") or {}
        for field in DISTRIBUTED_KEYS:
            self.assertTrue(field in distributed, f"distributed 缺少 {field}：{distributed}")
        self.assertIs(True, distributed.get("recorded"), "已发布就必须显示分发已留痕")
        internal = distributed.get("internal_recipients") or []
        external = distributed.get("external_targets") or []
        self.assertTrue(internal, "系统内收件人必须解析出来（账号 / 角色）")
        self.assertTrue(external, "自由文本收件人必须落在 external_targets")
        internal_blob = json.dumps(internal, ensure_ascii=False)
        external_blob = json.dumps(external, ensure_ascii=False)
        self.assertNotIn("zhangsan@example.com", internal_blob,
                         "外部邮箱不得出现在系统内收件人里")
        self.assertNotIn("zhangzhen", external_blob,
                         "系统内账号不得出现在外部分发对象里")
        self.assertNotIn("财务经理", external_blob,
                         "系统内角色不得出现在外部分发对象里")
        self.assertIn("zhangsan@example.com", external_blob,
                      "自由文本收件人必须原样留痕")
        kinds = {str(row.get("kind") or "") for row in internal if isinstance(row, dict)}
        self.assertTrue(kinds <= {"user", "role"}, f"internal kind 只允许 user/role：{kinds}")

    def test_primary_action_follows_the_source(self):
        _, _, from_quote = self.closure_of()
        self.assertEqual("back-to-quote", str((from_quote.get("primary_action") or {}).get("id") or ""),
                         "来自报价的项目主操作必须是「返回原报价继续」（Spec §7.3 规则 6）")
        _, _, independent = self.closure_of("independent")
        self.assertEqual("view-report", str((independent.get("primary_action") or {}).get("id") or ""),
                         "独立技术项目主操作必须是「查看已发布报告」")

    def test_unseen_project_cannot_read_the_closure(self):
        out = self.run_child("closure")
        self.assertEqual(404, out["unseen_status"], "无权账号必须 404（批次 7）")
        self.assertEqual(NOT_FOUND_DETAIL, str((out["unseen_body"] or {}).get("detail") or ""))


class ReadOnlyTest(NodeCase):
    """三个接口全部只读：调用前后业务数据逐字不变。"""

    def test_reading_never_writes(self):
        out = self.run_child("readonly")
        for key in ("meta", "report", "tasks", "ir"):
            self.assertEqual(out["before"][key], out["after"][key],
                             f"只读接口不得改动 {key}：列表 / 时间线 / 发布结果都必须纯读")


NODE_DRIVER = r"""
const fs = require('fs');
const vm = require('vm');
const modulePath = process.argv[2];
const payload = JSON.parse(process.argv[3] || '{}');

const mem = {};
const localStorage = {
  getItem: function (key) { return Object.prototype.hasOwnProperty.call(mem, key) ? mem[key] : null; },
  setItem: function (key, value) { mem[key] = String(value); },
  removeItem: function (key) { delete mem[key]; },
  key: function (index) { return Object.keys(mem)[index] || null; },
  get length() { return Object.keys(mem).length; },
};

const documentStub = { addEventListener: function () {}, querySelector: function () { return null; } };
const sandbox = {
  window: {}, document: documentStub, localStorage: localStorage, console: console,
  setTimeout: setTimeout, clearTimeout: clearTimeout,
};
sandbox.window.localStorage = localStorage;
sandbox.window.document = documentStub;
sandbox.globalThis = sandbox;
const context = vm.createContext(sandbox);

const result = {};
try {
  const source = fs.readFileSync(modulePath, 'utf8');
  result.source = source;
  vm.runInContext(source, context);
  const board = (sandbox.window && sandbox.window.TechHomeBoard) || sandbox.TechHomeBoard || null;
  result.present = !!board;
  if (board) {
    result.has_entries = typeof board.entries === 'function';
    result.has_card_of = typeof board.cardOf === 'function';
    result.has_stage_text = typeof board.stageText === 'function';
    result.has_waiting_text = typeof board.waitingText === 'function';
    result.has_remember = typeof board.rememberRecent === 'function';
    result.has_local_recent = typeof board.localRecent === 'function';
    result.recent_key = String(board.RECENT_KEY || '');
    try { result.entries = board.entries(); } catch (e) { result.entries_err = String(e && e.message); }
    if (!result.entries) {
      try { result.entries = board.ENTRIES; } catch (e) { result.entries_const_err = String(e && e.message); }
    }
    const row = payload.row || null;
    try { result.card_of = board.cardOf(row); } catch (e) { result.card_of_err = String(e && e.message); }
    try { result.card_of_empty = board.cardOf({ project_id: 'x' }); } catch (e) {}
    const card = (row || {}).card || null;
    try { result.stage_text = board.stageText(card); } catch (e) {}
    try { result.stage_text_null = board.stageText(null); } catch (e) {}
    try { result.waiting_text = board.waitingText(card); } catch (e) {}
    try { result.waiting_text_none = board.waitingText({ waiting_for: { kind: 'none' } }); } catch (e) {}
    try {
      board.rememberRecent('p-1');
      board.rememberRecent('p-2');
      board.rememberRecent('p-1');
    } catch (e) { result.remember_err = String(e && e.message); }
    try { result.local_recent = board.localRecent(); } catch (e) {}
    result.storage_dump = Object.assign({}, mem);
  }
} catch (error) {
  result.error = String((error && error.stack) || error);
}
process.stdout.write(JSON.stringify(result));
"""


class HomeBoardModuleTest(unittest.TestCase):
    """10A 前端：首页唯一口径模块（Spec §7.4）。"""

    def card_row(self):
        return {
            "project_id": "7393f6a00ccc",
            "owner": "alice",
            "card": {
                "owner": "alice",
                "owner_display_name": "爱丽丝",
                "stage": {"code": "4.1", "index": 10, "total": 13,
                          "title": "零件成本", "phase": 4},
                "waiting_for": {"kind": "role", "role": "finance_manager",
                                "label": "财务经理", "username": ""},
                "last_event": {"action": "integration_send_to_finance",
                               "at": "2026-09-17 14:02:11", "actor": "pm"},
                "anomaly": {"has_anomaly": False, "codes": []},
                "business_case_id": BC,
                "primary_action": {"id": "open-stage", "label": "去 4.1 零件成本",
                                   "required_role": "finance_manager",
                                   "target": "tech-workbench.html?stage=cost-review&project=7393f6a00ccc"},
            },
        }

    def run_driver(self, row=None):
        self.assertTrue(HOME_BOARD.exists(),
                        f"缺少 tech_app/frontend/tech-home-board.js（Spec §7.4）：{HOME_BOARD}")
        driver_dir = tempfile.mkdtemp(prefix="cpq-b10-node-")
        driver = pathlib.Path(driver_dir) / "driver.js"
        driver.write_text(NODE_DRIVER, encoding="utf-8")
        payload = json.dumps({"row": self.card_row() if row is None else row}, ensure_ascii=False)
        completed = subprocess.run(["node", str(driver), str(HOME_BOARD), payload],
                                   capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(0, completed.returncode, f"node 失败：{completed.stderr[-800:]}")
        return json.loads(completed.stdout)

    def test_module_exports_the_contract(self):
        out = self.run_driver()
        self.assertFalse(out.get("error"), f"模块执行失败：{out.get('error')}")
        self.assertTrue(out.get("present"), "必须导出 window.TechHomeBoard（Spec §7.4）")
        for key in ("has_entries", "has_card_of", "has_stage_text", "has_waiting_text",
                    "has_remember", "has_local_recent"):
            self.assertTrue(out.get(key), f"TechHomeBoard 缺少能力：{key}")
        self.assertTrue(str(out.get("recent_key") or "").strip(),
                        "必须声明 localStorage 键 RECENT_KEY")

    def test_three_entries_and_no_local_tab(self):
        """## 139 取代了 §7.4 的五入口决定：首页固定三页签。

        用户口径：技术工艺首页「应该是和报价一样的三个页签而不是五个」，后两个
        （最近访问 / 已归档）常年为空。三页签与待办任务口径的完整契约见
        docs/specs/tech-home-three-tabs-and-todo-tasks.md 与
        tests/test_tech_home_three_tabs_and_todo_tasks_red.py。
        """
        out = self.run_driver()
        entries = out.get("entries") or []
        self.assertEqual(3, len(entries),
                         f"首页必须恰好三个入口（## 139 Spec §5.1）：{entries}")
        ids = [str(entry.get("id") or "") for entry in entries]
        self.assertEqual(["mine", "todo", "all"], ids,
                         f"入口的 id 与顺序必须固定（## 139 Spec §5.1）：{ids}")
        local = [str(entry.get("id")) for entry in entries
                 if str(entry.get("source") or "") == "local"]
        self.assertEqual([], local,
                         "首页不得再有本机数据源页签：最近访问 / 已归档 已下线（## 139 §5.1）")
        scopes = {str(entry.get("id")): str(entry.get("scope") or "") for entry in entries}
        self.assertEqual("", scopes.get("todo") or "",
                         "待办任务不再走项目 scope=todo（## 139 §5.1）")
        self.assertEqual("mine", scopes.get("mine"))
        self.assertEqual("all", scopes.get("all"))

    def test_card_of_returns_the_backend_block_verbatim(self):
        out = self.run_driver()
        row = self.card_row()
        self.assertEqual(row["card"], out.get("card_of"),
                         "cardOf 必须原样返回 row.card（Spec §7.4）")
        self.assertIsNone(out.get("card_of_empty"),
                          "行上没有 card 时必须返回 null，绝不现造")

    def test_stage_and_waiting_text_never_fall_back_to_a_local_map(self):
        out = self.run_driver()
        self.assertEqual("4.1 零件成本", str(out.get("stage_text") or ""),
                         "stageText 必须用后端 card.stage（Spec §7.4）")
        self.assertEqual("—", str(out.get("stage_text_null") or ""),
                         "card 无效时给占位符，不得回落到本地状态映射表")
        self.assertEqual("财务经理", str(out.get("waiting_text") or ""),
                         "waitingText 必须用后端 card.waiting_for.label")
        self.assertEqual("", str(out.get("waiting_text_none") or ""),
                         "waiting_for.kind=none 时必须给空串（Spec §7.4）")

    def test_source_has_no_duplicated_status_map(self):
        out = self.run_driver()
        source = str(out.get("source") or "")
        for token in ("tech_record", "pending_confirmation", "pending_review",
                      "status-reviewing", "status-confirmed", "status-draft"):
            self.assertTrue(token not in source,
                            f"首页口径模块不得再维护第二套状态映射表：命中 {token}")

    def test_remember_recent_only_writes_local_storage(self):
        out = self.run_driver()
        self.assertFalse(out.get("remember_err"), f"rememberRecent 抛错：{out.get('remember_err')}")
        recent = [str(value) for value in (out.get("local_recent") or [])]
        self.assertEqual(["p-1", "p-2"], recent,
                         f"最近访问必须最新在前且去重：{recent}")
        dump = out.get("storage_dump") or {}
        keys = list(dump.keys())
        self.assertEqual(1, len(keys), f"rememberRecent 只能写 RECENT_KEY 一个键：{keys}")
        self.assertTrue(json.loads(dump[keys[0]]), "RECENT_KEY 里必须能解析出 id 列表")


if __name__ == "__main__":
    unittest.main()
