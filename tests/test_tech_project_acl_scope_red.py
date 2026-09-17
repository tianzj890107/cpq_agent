"""红测：技术项目「我的 / 全部」可见范围与项目级读写权限（批次 7）。

用户口径（批次 7 原文要点）：

  · 我的清单 = 我创建 / 我当前持有 / 我参与过 / 分派给我的业务实例；
  · 全部清单 = **当前用户权限范围内**的全部项目，不是无条件全公司公开；
  · 经理有部门/角色范围、管理员有管理范围；销售经理对其来源报价关联的技术项目可读、
    财务经理对当前成本任务项目可读；
  · 要有项目参与者模型；已归档项目要单独定义权限；
  · 附件、几何、审计、Agent 历史必须使用**同一套** ACL；
  · 「不存在」与「无权限」的响应必须一致，避免泄露项目是否存在；
  · 统一实现 require_project_access(project_id, user, mode)，红测至少两个账号两个项目，
    覆盖列表与全部敏感 GET。

现状缺口（只读实测，均已定位；断言都是行为与状态，不是文本搜索）：

  · `GET /api/projects`（main.py:920-922）就是 `store.list_projects()`，**连当前用户都没取**：
    实测（CPQ_SSO=true + 打桩 cpq_sso.resolve，两个账号两个项目）工艺工程师 alice 的
    列表里同时出现 alice 与 bob 的项目（2 条）。
  · 41 个「只有 {project_id} 一个路径参数」的 GET 路由里 **34 个**对非属主返回 200：
    attachments / files / audit / source / workflow / workflow/projection / summary /
    summary.html / summary.md / cost-review / costest / integration / material / assembly /
    manufacturing / production / verification / versions / process-report /
    process-report/versions / requirement / agent/meta / agent/settings / agent/history(未测) …
    这就意味着：知道 12 位项目 ID 的人可以读到任意项目的图纸、审计与 Agent 历史。
  · 项目级写入只有 `project_write_guard`（main.py:429-446），且**只对 role == "engineer"** 生效；
    `auth.can_edit_project`（services/auth.py:200）只被 4 处调用，读路径一处都没有。
  · 没有参与者模型：`store` 里没有 add_participant / list_participants / current_holder
    （storage/store.py 无这段），meta 里也没有 participants / current_holder 字段。
  · CPQ 登录把 `sales_mgr` 映射成技术工艺的 `viewer`（services/cpq_sso.py:35-45）：
    不读 `cpq_role_code` 就再也认不出「他是销售经理」，销售经理的来源报价关联根本无从判定。

验证方式（行为为主）：

  · 模块级：临时 DATA_DIR + 真 store 建两个账号两个项目，直接调
    `project_access.visible_projects / require_project_access / can_access`，断言
    范围、参与者、持有者、归档与「不存在 vs 无权限」的响应一致。
  · HTTP 级：子进程 + 临时 DATA_DIR + TestClient + `CPQ_SSO=true` + 打桩 `cpq_sso.resolve`
    （两张票 = 两个账号），**从 main.app.routes 现算**敏感 GET 清单，逐条断言非属主 404、
    属主不 404、响应体逐字一致。绝不连线上 PG，也不碰任何真实项目。

Spec：docs/specs/tech-project-acl-visible-scope.md
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# store 在 import 期就读 DATA_DIR，必须先指到临时目录（绝不碰线上/仓库数据）。
_DATA_DIR = tempfile.mkdtemp(prefix="cpq-acl-data-")
os.environ["DATA_DIR"] = _DATA_DIR
os.environ.setdefault("AUTH_ENABLED", "false")

from tech_app.backend.storage import store  # noqa: E402

SPEC = ROOT / "docs" / "specs" / "tech-project-acl-visible-scope.md"
NOT_FOUND_DETAIL = "项目不存在"


def access():
    """延迟导入新模块：缺失时给出明确缺口，而不是 ImportError。"""
    try:
        from tech_app.backend.services import project_access
    except ImportError as exc:                               # pragma: no cover - 缺口路径
        raise AssertionError(
            "缺少 tech_app/backend/services/project_access.py（批次 7 Spec §7）："
            "visible_projects / require_project_access / ProjectAccessError 是本批唯一判定入口"
        ) from exc
    return project_access


def need_store_api():
    missing = [name for name in ("add_participant", "remove_participant",
                                 "list_participants", "set_current_holder",
                                 "current_holder")
               if not hasattr(store, name)]
    if missing:
        raise AssertionError(f"store 缺少参与者 / 持有者模型 API：{missing}（Spec §7）")


def user(username, role, cpq_role_code="", cpq_role_name="", uid=""):
    return {"username": username, "role": role, "display_name": username,
            "user_id": uid or username, "requested_role": role,
            "cpq_role_code": cpq_role_code, "cpq_role_name": cpq_role_name or cpq_role_code,
            "is_system": False, "source": "cpq"}


ALICE = user("alice", "engineer", "process_mgr", "工艺经理", "101")
BOB = user("bob", "engineer", "process_mgr", "工艺经理", "102")
PM = user("pm", "process_manager", "process_mgr", "工艺经理", "201")
ADMIN = user("root", "admin", "", "", "1")
SALES = user("sales1", "viewer", "sales_mgr", "销售经理", "301")
SALES_DIR = user("salesd", "sales_director", "sales_director", "销售总监", "302")
FIN = user("fin1", "finance_manager", "finance_mgr", "财务经理", "401")
VIEWER = user("looker", "viewer", "", "", "501")
GM = user("gm", "general_manager", "", "", "601")


def make_project(owner, note="acl 探针"):
    return store.create_project(source_filename="acl.png", source_bytes=b"x",
                               note=note, owner=owner)


def ids(rows):
    return {str((row or {}).get("project_id") or "") for row in rows}


class SpecPinnedTest(unittest.TestCase):
    def test_spec_exists_and_pins_contract(self):
        self.assertTrue(SPEC.exists(), f"缺少 Spec：{SPEC}")
        text = SPEC.read_text(encoding="utf-8")
        for token in ("require_project_access", "visible_projects", "ProjectAccessError",
                      "participants", "current_holder", "mine_sources", "not_found",
                      "quote_owner", "cost_task_assignee", "scope"):
            self.assertIn(token, text, f"Spec 必须钉死 {token}")


class ModuleContractTest(unittest.TestCase):
    def test_module_and_error_type_exist(self):
        module = access()
        err = getattr(module, "ProjectAccessError", None)
        self.assertTrue(isinstance(err, type) and issubclass(err, Exception),
                        "project_access 必须定义 ProjectAccessError(code, message='')")
        for name in ("visible_projects", "require_project_access", "can_access",
                     "mine_sources", "effective_roles", "scope_of"):
            self.assertTrue(callable(getattr(module, name, None)),
                            f"project_access.{name} 必须存在（Spec §7）")


class MineScopeTest(unittest.TestCase):
    def test_mine_excludes_other_people_projects(self):
        module = access()
        mine = make_project("alice")
        other = make_project("bob")
        alice_mine = ids(module.visible_projects(ALICE, "mine"))
        bob_mine = ids(module.visible_projects(BOB, "mine"))
        self.assertIn(mine, alice_mine, "我创建的项目必须在我的清单里")
        self.assertNotIn(other, alice_mine, "别人的项目不得出现在我的清单里")
        self.assertIn(other, bob_mine)
        self.assertNotIn(mine, bob_mine)

    def test_all_scope_is_not_everything_for_a_plain_engineer(self):
        module = access()
        mine = make_project("alice")
        other = make_project("bob")
        alice_all = ids(module.visible_projects(ALICE, "all"))
        self.assertIn(mine, alice_all)
        self.assertNotIn(other, alice_all,
                         "工程师的「全部」不能等于全公司公开（Spec §5）")

    def test_managers_and_admin_see_everything(self):
        module = access()
        mine = make_project("alice")
        other = make_project("bob")
        for who in (PM, ADMIN, GM):
            rows = ids(module.visible_projects(who, "all"))
            self.assertIn(mine, rows, f"{who['username']} 应看到全部技术项目")
            self.assertIn(other, rows, f"{who['username']} 应看到全部技术项目")

    def test_list_items_carry_access_block(self):
        module = access()
        make_project("alice")
        rows = module.visible_projects(ALICE, "mine")
        self.assertTrue(rows, "至少要返回一条我自己的项目")
        for row in rows:
            block = row.get("access")
            self.assertIsInstance(block, dict, "列表项必须带 access（Spec §7）")
            for key in ("scope", "mine_sources", "can_read", "can_write"):
                self.assertIn(key, block, f"access 缺键 {key}")
            self.assertIsInstance(block["mine_sources"], list)

    def test_mine_sources_cover_the_four_kinds(self):
        module = access()
        need_store_api()
        pid = make_project("alice")
        store.set_current_holder(pid, "alice", author="tester")
        store.add_participant(pid, "alice", role="engineer", source="manual",
                              assignee=True, author="tester")
        row = [r for r in module.visible_projects(ALICE, "mine")
               if str(r.get("project_id")) == pid][0]
        sources = set(row["access"]["mine_sources"])
        self.assertIn("owner", sources)
        self.assertIn("holder", sources)
        self.assertIn("participant", sources)
        self.assertIn("assigned", sources)


class ParticipantTest(unittest.TestCase):
    def test_participant_grant_and_revoke(self):
        module = access()
        need_store_api()
        pid = make_project("bob")
        self.assertFalse(module.can_access(pid, ALICE, "read"),
                         "没被加进参与者之前，别人的项目对我不可读")
        store.add_participant(pid, "alice", role="engineer", source="manual", author="tester")
        self.assertTrue(module.can_access(pid, ALICE, "read"), "参与者必须可读")
        self.assertIn(pid, ids(module.visible_projects(ALICE, "mine")),
                      "参与过的项目要进我的清单")
        store.remove_participant(pid, "alice", author="tester")
        self.assertFalse(module.can_access(pid, ALICE, "read"), "移除参与者后必须立刻不可读")
        self.assertNotIn(pid, ids(module.visible_projects(ALICE, "mine")))

    def test_add_participant_is_idempotent(self):
        need_store_api()
        pid = make_project("bob")
        store.add_participant(pid, "alice", role="engineer", source="manual", author="tester")
        store.add_participant(pid, "alice", role="process_manager", source="manual",
                              author="tester")
        rows = store.list_participants(pid)
        mine = [row for row in rows if str(row.get("username")) == "alice"]
        self.assertEqual(1, len(mine), f"同一 username+source 只允许一条：{rows}")
        self.assertEqual("process_manager", mine[0].get("role"), "重复加入应更新角色而不是追加")

    def test_current_holder_defaults_to_owner_and_can_be_transferred(self):
        module = access()
        need_store_api()
        pid = make_project("bob")
        self.assertEqual("bob", store.current_holder(pid), "缺省持有人 = 创建人")
        store.set_current_holder(pid, "alice", author="tester")
        self.assertEqual("alice", store.current_holder(pid))
        self.assertTrue(module.can_access(pid, ALICE, "read"), "当前持有人必须可读")


class ArchivedTest(unittest.TestCase):
    def test_archived_projects_leave_the_normal_lists(self):
        module = access()
        pid = make_project("alice")
        store.archive_project(pid, author="tester")
        self.assertNotIn(pid, ids(module.visible_projects(ALICE, "mine")), "归档后不在我的清单")
        self.assertNotIn(pid, ids(module.visible_projects(PM, "all")), "归档后不在全部清单")
        self.assertIn(pid, ids(module.visible_projects(ALICE, "archived")), "owner 能在已归档里看到")
        self.assertIn(pid, ids(module.visible_projects(ADMIN, "archived")), "管理员能在已归档里看到")

    def test_archived_project_is_read_only_even_for_owner(self):
        module = access()
        pid = make_project("alice")
        store.archive_project(pid, author="tester")
        self.assertTrue(module.can_access(pid, ALICE, "read"), "归档项目 owner 仍可读（可追溯）")
        self.assertFalse(module.can_access(pid, ALICE, "write"), "归档项目一律只读（Spec §6）")

    def test_archived_project_is_invisible_to_unrelated_users(self):
        module = access()
        pid = make_project("bob")
        store.archive_project(pid, author="tester")
        self.assertFalse(module.can_access(pid, ALICE, "read"),
                         "别人的归档项目对我按不存在处理")
        self.assertNotIn(pid, ids(module.visible_projects(ALICE, "archived")))


class LinkedRoleTest(unittest.TestCase):
    def test_sales_manager_reads_only_its_source_quote_projects(self):
        module = access()
        need_store_api()
        linked = make_project("bob")
        unlinked = make_project("bob")
        store.add_participant(linked, "sales1", role="viewer", source="quote_owner",
                              author="tester")
        self.assertTrue(module.can_access(linked, SALES, "read"),
                        "销售经理必须能读他来源报价关联的技术项目")
        self.assertFalse(module.can_access(unlinked, SALES, "read"),
                         "没关联的项目对销售经理按不存在处理")
        # 「全部」必须**恰好**是被登记为来源报价负责人的那些项目：既不能漏（linked 丢），
        # 也不能多（unlinked 混进来）。这里按 store 现算期望集合，而不是写死成 {linked}，
        # 否则同一模块里先跑的用例只要也给 sales1 登记过，断言就会因顺序不同而假红。
        expected = {str(project.get("project_id")) for project in store.list_projects()
                    if any(str(row.get("username")) == SALES["username"]
                           and str(row.get("source")) == "quote_owner"
                           for row in store.list_participants(project.get("project_id")))}
        self.assertEqual(expected, ids(module.visible_projects(SALES, "all")),
                         "销售经理的「全部」= 他被登记为来源报价负责人的那些项目，不多不少")
        self.assertIn(linked, expected, "本用例登记的项目必须在期望集合里（否则断言空转）")
        self.assertFalse(module.can_access(linked, SALES, "write"),
                         "销售经理在技术项目里只读")

    def test_cpq_sales_role_code_is_recognized(self):
        module = access()
        need_store_api()
        pid = make_project("bob")
        store.add_participant(pid, "sales1", role="viewer", source="quote_owner", author="tester")
        self.assertIn("sales_mgr", module.effective_roles(SALES),
                      "cpq_role_code=sales_mgr 必须在有效角色里（sales_mgr→viewer 映射不能丢身份）")
        self.assertTrue(module.can_access(pid, SALES, "read"))
        self.assertFalse(module.can_access(pid, VIEWER, "read"),
                         "普通 viewer 不能被当成销售经理放行")

    def test_sales_director_follows_the_same_rule(self):
        module = access()
        need_store_api()
        pid = make_project("bob")
        store.add_participant(pid, "salesd", role="sales_director", source="quote_owner",
                              author="tester")
        self.assertTrue(module.can_access(pid, SALES_DIR, "read"))

    def test_finance_manager_reads_only_its_cost_task_projects(self):
        module = access()
        need_store_api()
        mine = make_project("bob")
        other = make_project("bob")
        store.add_participant(mine, "fin1", role="finance_manager", source="cost_task_assignee",
                              author="tester")
        self.assertTrue(module.can_access(mine, FIN, "read"),
                        "财务经理必须能读当前成本任务归他的项目")
        self.assertFalse(module.can_access(other, FIN, "read"))
        self.assertFalse(module.can_access(mine, FIN, "write"),
                         "项目级写权限不由成本角色决定（成本写仍走 COST_ROLES 的业务接口）")


class WriteModeTest(unittest.TestCase):
    def test_engineer_writes_only_own_project(self):
        module = access()
        mine = make_project("alice")
        other = make_project("bob")
        self.assertTrue(module.can_access(mine, ALICE, "write"))
        self.assertFalse(module.can_access(other, ALICE, "write"),
                         "工程师不能写别人的项目")

    def test_manager_and_admin_can_write_any_project(self):
        module = access()
        pid = make_project("alice")
        self.assertTrue(module.can_access(pid, PM, "write"))
        self.assertTrue(module.can_access(pid, ADMIN, "write"))

    def test_forbidden_write_raises_forbidden_not_not_found(self):
        module = access()
        err = module.ProjectAccessError
        pid = make_project("bob")
        store.add_participant(pid, "looker", role="viewer", source="manual", author="tester")
        try:
            module.require_project_access(pid, VIEWER, "write")
        except err as exc:
            self.assertEqual("forbidden", str(getattr(exc, "code", "")),
                             "能看见但角色不够时要 forbidden，不是 not_found")
        else:
            self.fail("只读参与者写项目必须被拒绝")


class NoLeakTest(unittest.TestCase):
    def test_missing_and_unauthorized_are_indistinguishable(self):
        module = access()
        err = module.ProjectAccessError
        other = make_project("bob")
        missing = "0" * 12

        def outcome(pid):
            try:
                module.require_project_access(pid, ALICE, "read")
            except err as exc:
                return (str(getattr(exc, "code", "")), str(exc))
            return ("ok", "")

        self.assertEqual(outcome(missing), outcome(other),
                         "「不存在」与「无权限」必须给出完全相同的错误码与文案（不泄露存在性）")
        self.assertEqual("not_found", outcome(other)[0])

    def test_require_project_access_returns_meta_for_allowed_user(self):
        module = access()
        pid = make_project("alice")
        meta = module.require_project_access(pid, ALICE, "read")
        self.assertEqual(pid, str((meta or {}).get("project_id") or ""),
                         "有权限时必须返回项目 meta（接口后面还要用）")


# ---------------------------------------------------------------------------
# HTTP 级：子进程 + TestClient + 两张票（两个账号）
# ---------------------------------------------------------------------------
CHILD = r'''
import json
import os
import re
import sys
import types

data_dir, root, case = sys.argv[1], sys.argv[2], sys.argv[3]
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

ALICE = {"username": "alice", "role": "engineer", "display_name": "Alice", "user_id": "101",
         "cpq_role_code": "process_mgr", "cpq_role_name": "工艺经理", "is_system": False}
BOB = {"username": "bob", "role": "engineer", "display_name": "Bob", "user_id": "102",
       "cpq_role_code": "process_mgr", "cpq_role_name": "工艺经理", "is_system": False}
PM = {"username": "pm", "role": "process_manager", "display_name": "PM", "user_id": "201",
      "cpq_role_code": "process_mgr", "cpq_role_name": "工艺经理", "is_system": False}
SALES = {"username": "sales1", "role": "viewer", "display_name": "销售", "user_id": "301",
         "cpq_role_code": "sales_mgr", "cpq_role_name": "销售经理", "is_system": False}
cpq_sso.resolve = lambda token: {"T-A": ALICE, "T-B": BOB, "T-PM": PM, "T-S": SALES}.get(
    (token or "").strip())

out = {"case": case}
pa = store.create_project(source_filename="a.png", source_bytes=b"a", note="A", owner="alice")
pb = store.create_project(source_filename="b.png", source_bytes=b"b", note="B", owner="bob")
from tech_app.backend.models.ir import DesignIR
store.save_ir(pb, DesignIR(device_name="B 项目", design_intent="ACL 探针",
                           parts=[{"part_id": "P-001", "name": "上壳", "quantity": 1}]
                           ).model_dump(), stage="parsed", author="tester")
client = TestClient(main.app)
A = {"Authorization": "Bearer T-A"}
B = {"Authorization": "Bearer T-B"}
PM_H = {"Authorization": "Bearer T-PM"}
S = {"Authorization": "Bearer T-S"}


def project_get_routes():
    """路由表里「只有 {project_id} 一个路径参数」的 GET 路由（现算，不写死清单）。"""
    paths = set()
    for route in main.app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        if "GET" not in methods or not path.startswith("/api/projects/{project_id}"):
            continue
        if "{" in path.replace("/api/projects/{project_id}", ""):
            continue
        paths.add(path)
    return sorted(paths)


if case == "routes":
    paths = project_get_routes()
    out["route_count"] = len(paths)
    out["non_owner_status"] = {p: client.get(p.replace("{project_id}", pb), headers=A).status_code
                               for p in paths}
    out["owner_status"] = {p: client.get(p.replace("{project_id}", pb), headers=B).status_code
                           for p in paths}
    missing = "0" * 12
    r_missing = client.get(f"/api/projects/{missing}/attachments", headers=A)
    r_forbidden = client.get(f"/api/projects/{pb}/attachments", headers=A)
    out["missing_status"] = r_missing.status_code
    out["forbidden_status"] = r_forbidden.status_code
    out["missing_body"] = r_missing.json()
    out["forbidden_body"] = r_forbidden.json()
    out["missing_trace_header"] = str(r_missing.headers.get("x-trace-id") or "")
    out["forbidden_trace_header"] = str(r_forbidden.headers.get("x-trace-id") or "")

elif case == "lists":
    out["alice_id"], out["bob_id"] = str(pa), str(pb)

    def ids_and_blocks(resp):
        rows = resp.json() if resp.status_code == 200 else []
        if not isinstance(rows, list):
            return [], False
        ids = [str(r.get("project_id")) for r in rows]
        blocks_ok = all(isinstance(r, dict) and isinstance(r.get("access"), dict)
                        and "scope" in r["access"] and "can_read" in r["access"]
                        for r in rows)
        return ids, bool(rows) and blocks_ok

    listed = client.get("/api/projects", headers=A)
    out["list_status"] = listed.status_code
    rows = listed.json()
    out["list_is_list"] = isinstance(rows, list)
    out["alice_ids"], out["alice_has_access_block"] = ids_and_blocks(listed)
    out["all_engineer_ids"], out["all_engineer_access_block"] = ids_and_blocks(
        client.get("/api/projects?scope=all", headers=A))
    out["all_pm_ids"], out["all_pm_access_block"] = ids_and_blocks(
        client.get("/api/projects?scope=all", headers=PM_H))
    out["archived_ids"] = [str(r.get("project_id"))
                           for r in client.get("/api/projects?scope=archived", headers=PM_H).json()]
    store.add_participant(pb, "sales1", role="viewer", source="quote_owner", author="tester")
    out["sales_ids"], out["sales_access_block"] = ids_and_blocks(
        client.get("/api/projects", headers=S))
    out["bad_scope_status"] = client.get("/api/projects?scope=nonsense", headers=A).status_code
    out["no_token_status"] = client.get("/api/projects").status_code

elif case == "writes":
    out["engineer_other_rename"] = client.patch(
        f"/api/projects/{pb}/management", json={"name": "改别人的"}, headers=A).status_code
    out["engineer_own_rename"] = client.patch(
        f"/api/projects/{pa}/management", json={"name": "改自己的"}, headers=A).status_code
    out["manager_rename"] = client.patch(
        f"/api/projects/{pb}/management", json={"name": "经理改名"}, headers=PM_H).status_code
    out["engineer_read_other_leak"] = client.get(
        f"/api/projects/{pb}/attachments", headers=A).status_code

print(json.dumps(out, ensure_ascii=False, default=str))
'''


class HttpAclTest(unittest.TestCase):
    """HTTP 级：敏感 GET 全部走同一 ACL，且不泄露项目是否存在。"""

    def run_child(self, case):
        data_dir = tempfile.mkdtemp(prefix="cpq-acl-http-")
        script_dir = tempfile.mkdtemp(prefix="cpq-acl-script-")
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        env = dict(os.environ)
        env["DATA_DIR"] = data_dir
        env["AUTH_ENABLED"] = "false"
        env["CPQ_SSO"] = "true"
        env["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run(
            [sys.executable, str(script), data_dir, str(ROOT), case],
            capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertEqual(0, completed.returncode,
                         f"子进程失败：{completed.stderr[-800:]}")
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_every_single_param_project_get_needs_access(self):
        out = self.run_child("routes")
        self.assertGreaterEqual(out["route_count"], 30,
                                f"路由清单太少（{out['route_count']}），断言可能空转")
        leaking = {path: code for path, code in out["non_owner_status"].items() if code != 404}
        self.assertEqual({}, leaking, f"这些 GET 对非属主没有按「不存在」处理：{leaking}")
        blocked_owner = {path: code for path, code in out["owner_status"].items() if code == 404}
        self.assertEqual([], sorted(blocked_owner),
                         f"属主自己有数据的项目不应 404：{blocked_owner}")

    def test_missing_and_unauthorized_bodies_are_identical(self):
        """「不存在」与「无权限」必须**不可区分**（不是「逐字相同」）。

        跨批次契约（批次 9）：所有 >=400 的 JSON 响应体会被统一补一个 `trace_id`
        （main.py 的 trace 中间件，`uuid4().hex[:16]`，逐请求唯一），所以「响应体逐字
        相同」在批次 9 落地后按字面已不可能成立。要守的是「不可区分」——状态码相同、
        `detail` 逐字相同、字段名集合相同、除 `trace_id` 外的取值逐字相同。这几条
        继续为真才不泄露项目是否存在；任何一条破了就是真缺口。
        """
        out = self.run_child("routes")
        self.assertEqual(404, out["missing_status"])
        self.assertEqual(404, out["forbidden_status"])
        missing, forbidden = out["missing_body"], out["forbidden_body"]
        self.assertEqual(missing.get("detail"), forbidden.get("detail"),
                         "「不存在」与「无权限」的 detail 必须逐字相同（不泄露存在性）")
        self.assertEqual(sorted(missing), sorted(forbidden),
                         "两种响应的字段名集合必须相同，否则可以从字段反推项目是否存在："
                         f"{sorted(missing)} != {sorted(forbidden)}")
        strip = lambda body: {k: v for k, v in body.items() if k != "trace_id"}  # noqa: E731
        self.assertEqual(strip(missing), strip(forbidden),
                         "除 trace_id 外，「不存在」与「无权限」的响应体必须逐字相同")
        self.assertTrue(str(missing.get("trace_id") or "").strip(),
                        "批次 9 起错误响应必须带非空 trace_id")
        self.assertTrue(str(forbidden.get("trace_id") or "").strip(),
                        "批次 9 起错误响应必须带非空 trace_id")
        self.assertNotEqual(missing["trace_id"], forbidden["trace_id"],
                            "trace_id 必须逐请求唯一；若它是常量，就必须回到「逐字相同」的老断言")

    def test_list_scope_and_access_block_over_http(self):
        out = self.run_child("lists")
        self.assertEqual(200, out["list_status"])
        self.assertTrue(out["list_is_list"], "GET /api/projects 仍必须返回 list（既有消费方）")
        for key in ("alice_has_access_block", "all_engineer_access_block",
                    "all_pm_access_block", "sales_access_block"):
            self.assertTrue(out[key], f"{key}：每一行列表项都必须带 access 块")
        # 非法 scope 与未登录都是既有行为，不能被本批改掉
        self.assertEqual(400, out["bad_scope_status"], "非法 scope 必须 400")
        self.assertEqual(401, out["no_token_status"], "没票必须 401（既有行为）")

    def test_engineer_sees_only_own_project_in_list(self):
        out = self.run_child("lists")
        bob = out["bob_id"]
        self.assertEqual(out["alice_id"], out["alice_ids"][0] if out["alice_ids"] else None,
                         f"alice 的「我的」第一条应是自己的项目：{out['alice_ids']}")
        self.assertNotIn(bob, out["alice_ids"],
                         f"alice 的「我的」不得出现 bob 的项目：{out['alice_ids']}")
        self.assertNotIn(bob, out["all_engineer_ids"],
                         f"alice 的 scope=all 也不得出现 bob 的项目：{out['all_engineer_ids']}")
        self.assertEqual(sorted(set(out["all_engineer_ids"])), sorted(set(out["alice_ids"])),
                         "工程师的 scope=all 必须等于他能看到的全部（即只有自己的项目）")
        self.assertIn(out["alice_id"], out["all_pm_ids"], "工艺经理的 scope=all 要看得见项目 A")
        self.assertIn(bob, out["all_pm_ids"], "工艺经理的 scope=all 要看得见项目 B")
        self.assertEqual([out["bob_id"]], sorted(set(out["sales_ids"])),
                         "销售经理只看得到他来源报价关联的那一个项目")

    def test_archived_scope_is_a_separate_list(self):
        out = self.run_child("lists")
        self.assertNotIn(out["alice_id"], out["archived_ids"],
                         "未归档项目不得出现在 scope=archived")
        self.assertNotIn(out["bob_id"], out["archived_ids"],
                         "未归档项目不得出现在 scope=archived")

    def test_write_paths_stay_scoped(self):
        out = self.run_child("writes")
        self.assertNotEqual(200, out["engineer_other_rename"],
                            "工程师不能改别人的项目")
        self.assertEqual(200, out["engineer_own_rename"], "工程师能改自己的项目")
        self.assertEqual(200, out["manager_rename"], "经理能改任意项目")
        self.assertEqual(404, out["engineer_read_other_leak"],
                         "读别人的项目必须按不存在处理")


if __name__ == "__main__":
    unittest.main()
