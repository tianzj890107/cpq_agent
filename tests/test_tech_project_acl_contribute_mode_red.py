# -*- coding: utf-8 -*-
"""红测：项目 ACL 不得提前否决专属业务动作（批次 7 回归修复 / Spec §18）。

用户口径（原文要点）：

  · 项目 ACL 回答「这个用户是否与项目有关、是否可以进入项目」；
    业务接口门禁回答「这个角色能不能执行当前业务动作」；
  · 不能再让通用的 can_write_project() 提前否决「财务确认成本、总监审核、报告发布」
    等专属业务动作，否则接口自己的角色门禁永远没有执行机会；
  · 项目存在 plan.finance_handoff：财务经理**角色池**可读该项目，不绑定具体领取人；
  · 项目存在有效来源报价关联（business_case.quote_session_id / source_task_id）：
    销售经理**角色池**可读该项目，不绑定具体领取人；
  · 角色池可读要覆盖清单、历史抽屉与项目的全部必要读接口；
  · 「有关联」只授予可见性，不自动授予写权限；通用项目修改 / 删除 / 附件管理
    仍保留原有项目级写权限；
  · 归档项目继续遵循独立的归档可见规则，不因角色池关联自动获得修改权限；
  · 五阶段口径：成本测算是第 4 阶段（4.1 零件成本 / 4.2 组装成本 / 4.3 汇总）。

现状缺口（只读实测已定位，断言都是行为与状态）：

  · `main.py:433` 的 `project_write_guard` 对**所有**非 GET 请求先跑
    `require_project_access(pid, user, "write")`，`mode` 只有 `read` / `write`；
  · `services/project_access.py` 的 `can_write` 落到 `auth.can_edit_project`
    （`services/auth.py:200`），只认 `admin` / `process_manager` / `engineer`（本人）；
  · 读侧只认参与者表里的 `quote_owner` / `cost_task_assignee`，而全仓库没有任何业务
    代码调用 `store.add_participant` —— 这两类记录从不存在。
  → 实测：财务经理 43 个读接口全部 404（批次 7 之前 40×200）；14 条成本写接口 403
    「你的角色只能查看该项目，不能修改」；总监审核 / 发布同样 403；
    销售经理读接口全 404，报价首页技术清单为空。

验证方式：子进程 + 每个用例独立的临时 `DATA_DIR` + TestClient + `CPQ_SSO=true` +
打桩 `cpq_sso.resolve`，用真 store 建三个项目（有关联 / 无关 / 已归档），逐路由断言
状态与文案。绝不连线上 PG，也不连线上 CPQ（`CPQ_AUTH_BASE_URL` 指向必然拒绝的端口）。

Spec：docs/specs/tech-project-acl-visible-scope.md 的第 18 节（修订 v2）
"""
from __future__ import annotations

import ast
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

# 父进程也要有独立的 DATA_DIR：本文件的静态用例会 import main，别写进仓库数据目录。
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="cpq-aclrole-parent-")
os.environ.setdefault("AUTH_ENABLED", "false")

SPEC = ROOT / "docs" / "specs" / "tech-project-acl-visible-scope.md"

NOT_FOUND_DETAIL = "项目不存在"
ACL_FORBIDDEN_FALLBACK = "你的角色只能查看该项目，不能修改"

# Spec §18.5 的 21 条专属业务动作白名单（逐条固定；静态用例会核对每条真实存在，
# 并且与「从 main.py 现算出来的」那份逐条相等）。
CONTRIBUTE_ROUTES = [
    ("POST", "/api/projects/{project_id}/agent/event", "FIN"),
    ("POST", "/api/projects/{project_id}/parts/{part_id}/cost", "FIN"),
    ("PUT", "/api/projects/{project_id}/parts/{part_id}/cost", "FIN"),
    ("POST", "/api/projects/{project_id}/integration/cost", "FIN"),
    ("PUT", "/api/projects/{project_id}/integration/cost", "FIN"),
    ("PUT", "/api/projects/{project_id}/cost-review", "FIN"),
    ("POST", "/api/projects/{project_id}/cost-review/parts/{part_id}", "FIN"),
    ("POST", "/api/projects/{project_id}/cost-review/assembly", "FIN"),
    ("POST", "/api/projects/{project_id}/cost-review/confirm", "FIN"),
    ("POST", "/api/projects/{project_id}/cost-review/material-write", "FIN"),
    ("POST", "/api/projects/{project_id}/cost-review/send-to-quote", "FIN"),
    ("POST", "/api/projects/{project_id}/cost-review/return-to-process", "FIN"),
    ("POST", "/api/projects/{project_id}/pricing/review", "FIN"),
    ("POST", "/api/projects/{project_id}/approval/act", "FIN"),
    ("POST", "/api/projects/{project_id}/versions/{version}/approve", "DIR"),
    ("POST", "/api/projects/{project_id}/versions/{version}/reject", "DIR"),
    ("POST", "/api/projects/{project_id}/requirement/review", "DIR"),
    ("POST", "/api/projects/{project_id}/process-report/review", "DIR"),
    ("PUT", "/api/projects/{project_id}/process-report/distribution", "DIR"),
    ("POST", "/api/projects/{project_id}/process-report/publish", "DIR"),
    ("PUT", "/api/projects/{project_id}/requirement/customer-credit", "SAL"),
]

# 通用项目级写：即使有关联，也必须仍被项目级写权挡住（可见性 ≠ 写权限）。
# 只挑收 JSON 体的路由 —— multipart 的路由放静态用例里核对，避免 422 干扰断言。
GENERIC_WRITE_ROUTES = [
    ("PATCH", "/api/projects/{project_id}/management", "FIN"),
    ("PATCH", "/api/projects/{project_id}/management", "DIR"),
    ("PATCH", "/api/projects/{project_id}/management", "SAL"),
]

# 没有自己的角色门禁、必须留在 write 模式的路由（降级会让任何可见者都能做）。
MUST_STAY_WRITE = [
    ("POST", "/api/projects/{project_id}/tasks/{task_id}/cancel"),
    ("PATCH", "/api/projects/{project_id}/management"),
    ("DELETE", "/api/projects/{project_id}/management"),
    ("POST", "/api/projects/{project_id}/attachments"),
    ("PUT", "/api/projects/{project_id}/agent/settings"),
]

CAN_WRITE_ROLES = {"admin", "process_manager", "engineer"}


# --------------------------------------------------------------------------- #
# 静态契约：白名单口径必须能从 main.py 现算出来，且实现里有一份同口径的显式常量
# --------------------------------------------------------------------------- #
def _resolve_roles(node, authmod):
    """把 `_require(user, auth.X)` 的第二个实参解析成角色集合；解析不了返回 None。"""
    if isinstance(node, ast.Name) and hasattr(authmod, node.id):
        value = getattr(authmod, node.id)
        if isinstance(value, (set, frozenset, tuple, list)):
            return set(value)
        return None
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "auth"):
        value = getattr(authmod, node.attr, None)
        if isinstance(value, (set, frozenset, tuple, list)):
            return set(value)
        return None
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        try:
            return set(ast.literal_eval(node))
        except Exception:
            return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _resolve_roles(node.left, authmod)
        right = _resolve_roles(node.right, authmod)
        if left is None or right is None:
            return None
        return left | right
    return None


def _role_var_names(func):
    """函数体里从 `X.get("role")` / `X.get("cpq_role_code")` 取值的变量名。"""
    names = set()
    for sub in ast.walk(func):
        if (isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call)
                and isinstance(sub.value.func, ast.Attribute)
                and sub.value.func.attr == "get" and sub.value.args
                and isinstance(sub.value.args[0], ast.Constant)
                and sub.value.args[0].value in ("role", "cpq_role_code")):
            for target in sub.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _inline_roles(func, names):
    """内联角色判断里出现过的角色字面量（`role != "sales_manager"` / `role in {...}`）。"""
    found = set()
    for sub in ast.walk(func):
        if not isinstance(sub, ast.Compare):
            continue
        sides = [sub.left] + list(sub.comparators)
        if not any(isinstance(s, ast.Name) and s.id in names for s in sides):
            continue
        for side in sides:
            if isinstance(side, ast.Constant) and isinstance(side.value, str):
                found.add(side.value)
            elif isinstance(side, (ast.Set, ast.Tuple, ast.List)):
                for elt in side.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        found.add(elt.value)
    return found


def derive_contribute_routes():
    """从 main.py 现算「自带门禁允许一个 can_write=False 角色的项目写路由」。"""
    from tech_app.backend.services import auth as authmod

    tree = ast.parse((ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8"))
    write = {"POST", "PUT", "PATCH", "DELETE"}
    hits = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                    and dec.args and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)):
                continue
            method, path = dec.func.attr.upper(), dec.args[0].value
            if method not in write or not path.startswith("/api/projects/{project_id}"):
                continue
            hit = False
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id == "_require" and len(sub.args) >= 2):
                    roles = _resolve_roles(sub.args[1], authmod)
                    if roles and (roles - CAN_WRITE_ROLES):
                        hit = True
            if _inline_roles(node, _role_var_names(node)) - CAN_WRITE_ROLES:
                hit = True
            if hit:
                hits.add("%s %s" % (method, path))
    return hits


class SpecPinnedTest(unittest.TestCase):
    """不吃探针的静态契约：Spec 存在、路由真实、白名单口径自洽、实现里有显式常量。"""

    def test_spec_pins_the_revision(self):
        self.assertTrue(SPEC.exists(), f"缺少 Spec：{SPEC}")
        text = SPEC.read_text(encoding="utf-8")
        for token in ("修订 v2", "contribute", "角色池", "finance_handoff",
                      "quote_session_id", "4.1 零件成本", "4.2 组装成本", "4.3 汇总"):
            self.assertTrue(token in text, f"Spec 修订缺少关键契约：{token}")
        for stale in ("2.3 成本", "2.2 发送财务"):
            self.assertTrue(stale not in text, f"Spec 还在用旧的阶段编号：{stale}")

    def test_whitelist_routes_exist(self):
        """白名单里的路由必须真实存在（否则断言会空转）。"""
        from tech_app.backend import main

        known = {(getattr(route, "path", ""), method)
                 for route in main.app.routes
                 for method in (getattr(route, "methods", set()) or set())}
        missing = [f"{method} {path}"
                   for method, path, _ in CONTRIBUTE_ROUTES + GENERIC_WRITE_ROUTES
                   if (path, method) not in known]
        self.assertEqual([], missing, f"白名单里的路由不存在，断言会空转：{missing}")
        self.assertEqual(21, len(CONTRIBUTE_ROUTES),
                         "专属业务动作白名单必须正好 21 条（Spec §18.5）")

    def test_whitelist_matches_the_derived_set(self):
        """白名单 == 从 main.py 现算出来的集合，逐条相等（不多不少）。"""
        derived = derive_contribute_routes()
        declared = {"%s %s" % (method, path) for method, path, _ in CONTRIBUTE_ROUTES}
        missing = sorted(derived - declared)
        extra = sorted(declared - derived)
        self.assertEqual([], missing, f"漏进白名单的专属业务动作：{missing}")
        self.assertEqual([], extra, f"白名单里混进了不该有的路由：{extra}")

    def test_no_gate_routes_are_not_in_whitelist(self):
        """没有自己角色门禁的路由必须留在 write 模式（降级 = 能力放大）。"""
        declared = {"%s %s" % (method, path) for method, path, _ in CONTRIBUTE_ROUTES}
        leaked = [f"{m} {p}" for m, p in MUST_STAY_WRITE if f"{m} {p}" in declared]
        self.assertEqual([], leaked, f"这些路由没有自己的门禁，不能降级为 contribute：{leaked}")

    def test_modes_has_three_values(self):
        from tech_app.backend.services import project_access

        modes = set(getattr(project_access, "MODES", ()) or ())
        self.assertEqual({"read", "contribute", "write"}, modes,
                         "require_project_access 的 mode 必须正好三个取值（Spec §18.4）")

    def test_contribute_constant_is_declared(self):
        """实现里必须有一份显式常量，不许用「路径不以 /cost 结尾就…」的推断式规则。"""
        from tech_app.backend.services import project_access

        declared = getattr(project_access, "CONTRIBUTE_ROUTES", None)
        self.assertIsNotNone(declared, "project_access 缺少显式常量 CONTRIBUTE_ROUTES")
        normalized = set()
        for row in declared:
            self.assertEqual(2, len(tuple(row)), f"CONTRIBUTE_ROUTES 每项必须是 (method, path)：{row}")
            method, path = tuple(row)
            normalized.add("%s %s" % (str(method).upper(), str(path)))
        expected = {"%s %s" % (method, path) for method, path, _ in CONTRIBUTE_ROUTES}
        self.assertEqual(expected, normalized,
                         "CONTRIBUTE_ROUTES 必须与 Spec §18.5 的 21 条逐条一致")


# --------------------------------------------------------------------------- #
# 行为契约：子进程 + 临时 DATA_DIR + TestClient，真 store 建三个项目逐路由断言
# --------------------------------------------------------------------------- #
CHILD = r'''
import json
import os
import sys
import types

data_dir, root, case = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
os.environ["CPQ_SSO"] = "true"
# 绝不连线上 CPQ：指到一个必然拒绝的本地端口，bridge 立刻 BridgeUnavailable。
os.environ["CPQ_AUTH_BASE_URL"] = "http://127.0.0.1:1"
os.environ["CPQ_KB_BASE_URL"] = "http://127.0.0.1:1"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

from fastapi.testclient import TestClient

from tech_app.backend import main
from tech_app.backend.models.integration import (FinanceHandoff, IntegrationPlan,
                                                 QuoteHandoff)
from tech_app.backend.models.ir import DesignIR
from tech_app.backend.services import cpq_sso, integration as integration_service
from tech_app.backend.services import project_access
from tech_app.backend.storage import store

ROUTES = json.loads(os.environ.get("CPQ_ACLROLE_ROUTES") or "[]")
GENERIC = json.loads(os.environ.get("CPQ_ACLROLE_GENERIC") or "[]")
ACL = project_access.FORBIDDEN_MESSAGE
NOT_FOUND = project_access.NOT_FOUND_MESSAGE
BC = "bc_5f4e3d2c1b0a"


def U(name, role, code, label, display=""):
    return {"username": name, "role": role, "display_name": display or name,
            "user_id": name, "cpq_role_code": code, "cpq_role_name": label,
            "is_system": False}


TOK = {
    "A": U("alice", "engineer", "", "", "爱丽丝"),
    "PM": U("pm", "process_manager", "process_mgr", "工艺经理", "工艺经理"),
    "FIN": U("fin1", "finance_manager", "finance_mgr", "财务经理", "财务一号"),
    "DIR": U("dir1", "process_director", "tech_director", "工艺技术总监", "总监一号"),
    "SAL": U("sales1", "viewer", "sales_mgr", "销售经理", "销售一号"),
    "GM": U("gm1", "general_manager", "", "", "总经理"),
    "V": U("looker", "viewer", "", "", "路人"),
}
cpq_sso.resolve = lambda token: TOK.get((token or "").strip())
READERS = ("A", "FIN", "SAL", "DIR", "GM", "V")


def make_project(owner, name, related=False, archive=False):
    pid = str(store.create_project(source_filename="%s.png" % name, source_bytes=b"x",
                                   note=name, owner=owner))
    store.save_ir(pid, DesignIR(device_name=name, design_intent=name,
                                parts=[{"part_id": "P-001", "name": "上壳",
                                        "quantity": 1}]).model_dump(),
                  stage="parsed", author="tester")
    if related:
        store.save_business_case(pid, {"business_case_id": BC,
                                       "quote_session_id": "S-2026-0007",
                                       "source_task_id": "T-1"}, author="tester")
        integration_service.save_plan(pid, IntegrationPlan(
            project_id=pid,
            finance_handoff=FinanceHandoff(task_id="T-FIN-1", task_no="FIN-1",
                                           target_role_name="财务经理", target_type="role",
                                           sent_at="2026-09-17 12:00:00", sent_by="工艺经理"),
            quote_handoff=QuoteHandoff(session_id="S-2026-0007", next_step_no=3,
                                       next_step_name="定价-利润加成",
                                       target_role_name="销售经理", task_id="T-Q-9",
                                       sent_at="2026-09-17 13:00:00", sent_by="pm"),
        ), author="tester")
    if archive:
        store.archive_project(pid, author="tester")
    return pid


P_OPEN = make_project("alice", "有关联项目", related=True)
P_PLAIN = make_project("bob", "无关项目")
P_ARCH = make_project("alice", "已归档有关联", related=True, archive=True)

client = TestClient(main.app)
H = {key: {"Authorization": "Bearer " + key} for key in TOK}
SUBS = {"{part_id}": "P-001", "{version}": "1", "{task_id}": "T-FIN-1"}


def materialize(template, pid):
    path = template.replace("{project_id}", pid)
    for key, value in SUBS.items():
        path = path.replace(key, value)
    return path


def single_param_gets():
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


def call(method, template, token, pid, payload=None):
    url = materialize(template, pid)
    kwargs = {"headers": H[token]}
    if method in {"POST", "PUT", "PATCH"}:
        kwargs["json"] = payload if payload is not None else {}
    try:
        resp = client.request(method, url, **kwargs)
    except Exception as exc:                      # 网络/序列化异常也要有记录，不能中断整轮
        return {"status": -1, "detail": "EXC:%s" % type(exc).__name__, "acl": False}
    try:
        body = resp.json()
    except Exception:
        body = None
    detail = str((body or {}).get("detail") or "") if isinstance(body, dict) else ""
    acl_blocked = (resp.status_code == 403 and detail == ACL) or \
                  (resp.status_code == 404 and detail == NOT_FOUND)
    return {"status": resp.status_code, "detail": detail[:120], "acl": acl_blocked}


out = {"open": P_OPEN, "plain": P_PLAIN, "arch": P_ARCH, "acl": ACL,
       "not_found": NOT_FOUND, "readers": list(READERS)}

if case == "reads":
    paths = single_param_gets()
    out["read_route_count"] = len(paths)
    out["read_routes"] = paths
    for label, pid in (("open", P_OPEN), ("plain", P_PLAIN), ("arch", P_ARCH)):
        for who in READERS:
            by_route = {}
            for path in paths:
                by_route[path] = call("GET", path, who, pid)
            out["reads_%s_%s" % (label, who)] = by_route
    for who in READERS:
        listed = client.get("/api/projects?scope=all", headers=H[who])
        rows = listed.json() if listed.status_code == 200 else []
        out["list_%s" % who] = sorted(str((row or {}).get("project_id"))
                                      for row in rows if isinstance(row, dict))
    for who in ("FIN", "SAL"):
        out["single_all_%s" % who] = call("GET", "/api/projects/{project_id}", who, P_OPEN)

elif case == "writes":
    rows = []
    for method, template, who in ROUTES:
        rows.append({"route": "%s %s" % (method, template), "who": who, "project": "open",
                     **call(method, template, who, P_OPEN)})
    for method, template, who in ROUTES:
        rows.append({"route": "%s %s" % (method, template), "who": who, "project": "plain",
                     **call(method, template, who, P_PLAIN)})
    for method, template, _ in ROUTES:
        rows.append({"route": "%s %s" % (method, template), "who": "V", "project": "open",
                     **call(method, template, "V", P_OPEN)})
    out["whitelist_rows"] = rows

    generic = []
    for method, template, who in GENERIC:
        generic.append({"route": "%s %s" % (method, template), "who": who,
                        "project": "open", **call(method, template, who, P_OPEN)})
    out["generic_rows"] = generic

    out["engineer_other"] = call("PATCH", "/api/projects/{project_id}/management", "A",
                                 P_PLAIN, {"name": "HACK-BY-ENGINEER"})
    out["engineer_other_name_after"] = (store.load_meta(P_PLAIN) or {}).get("project_name")
    out["finance_unrelated_cost"] = call(
        "PUT", "/api/projects/{project_id}/cost-review", "FIN", P_PLAIN)
    out["finance_archived_cost"] = call(
        "PUT", "/api/projects/{project_id}/cost-review", "FIN", P_ARCH)
    out["finance_archived_read"] = call(
        "GET", "/api/projects/{project_id}/cost-review", "FIN", P_ARCH)
    out["sales_archived_read"] = call(
        "GET", "/api/projects/{project_id}/cost-review", "SAL", P_ARCH)
    out["pm_generic_ok"] = call("PATCH", "/api/projects/{project_id}/management", "PM",
                                P_OPEN, {"name": "工艺经理改过的名字"})

print(json.dumps(out, ensure_ascii=False, default=str))
'''


_PROBE_CACHE = {}


def probe(case):
    """跑一次子进程探针（每个 case 一份全新 DATA_DIR），结果在本进程内缓存。"""
    if case in _PROBE_CACHE:
        return _PROBE_CACHE[case]
    data_dir = tempfile.mkdtemp(prefix="cpq-aclrole-%s-" % case)
    script_dir = tempfile.mkdtemp(prefix="cpq-aclrole-script-")
    script = pathlib.Path(script_dir) / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "DATA_DIR": data_dir,
        "AUTH_ENABLED": "false",
        "CPQ_SSO": "true",
        "CPQ_AUTH_BASE_URL": "http://127.0.0.1:1",
        "CPQ_KB_BASE_URL": "http://127.0.0.1:1",
        "PYTHONPATH": str(ROOT),
        "CPQ_ACLROLE_ROUTES": json.dumps([list(row) for row in CONTRIBUTE_ROUTES]),
        "CPQ_ACLROLE_GENERIC": json.dumps([list(row) for row in GENERIC_WRITE_ROUTES]),
    })
    proc = subprocess.run([sys.executable, str(script), data_dir, str(ROOT), case],
                          capture_output=True, text=True, env=env, cwd=str(ROOT))
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines or not lines[-1].startswith("{"):
        raise AssertionError(
            "子进程没有产出 JSON：rc=%s\nSTDERR:\n%s"
            % (proc.returncode, (proc.stderr or "")[-2500:]))
    _PROBE_CACHE[case] = json.loads(lines[-1])
    return _PROBE_CACHE[case]


def summarize(rows):
    """失败信息只留路由 + 状态 + 文案，别把整份响应吐出来。"""
    return "; ".join("%s [%s] -> %s %s"
                     % (row.get("route"), row.get("who"), row.get("status"),
                        row.get("detail")) for row in rows[:6])


class ReadScopeTest(unittest.TestCase):
    """读侧：角色池可读必须与属主同级，无关项目与归档项目一律不可见。"""

    @classmethod
    def setUpClass(cls):
        cls.out = probe("reads")

    def _routes(self):
        return list(self.out["read_routes"])

    def _by_route(self, project, who):
        return self.out["reads_%s_%s" % (project, who)]

    def _acl_blocked(self, project, who):
        return [path for path in self._routes() if self._by_route(project, who)[path]["acl"]]

    def _not_found(self, project, who):
        return [path for path in self._routes()
                if self._by_route(project, who)[path]["status"] != 200]

    def test_read_route_count_is_stable(self):
        self.assertEqual(43, self.out["read_route_count"],
                         "单 {project_id} 的 GET 路由数量变了，后面的断言基线要重算")

    def test_finance_role_pool_is_not_blocked_on_related_project(self):
        blocked = self._acl_blocked("open", "FIN")
        self.assertEqual([], blocked, f"财务经理被 ACL 挡在有关联项目之外：{blocked[:6]}")

    def test_finance_reads_at_least_what_the_owner_reads(self):
        """这批读路由本身没有任何角色门禁（静态扫描 0 条），所以逐条状态必须与
        「全部可读」的经理一致 —— 差别只可能来自 ACL。"""
        manager, finance = self._by_route("open", "DIR"), self._by_route("open", "FIN")
        diff = [path for path in self._routes()
                if manager[path]["status"] != finance[path]["status"]]
        self.assertEqual([], diff, f"财务角色池与经理读到的结果不一致：{diff[:6]}")

    def test_finance_cannot_read_unrelated_project(self):
        visible = [path for path in self._routes()
                   if self._by_route("plain", "FIN")[path]["status"] == 200]
        self.assertEqual([], visible, f"财务经理读到了没有财务交接的项目：{visible[:6]}")

    def test_sales_role_pool_is_not_blocked_on_related_project(self):
        blocked = self._acl_blocked("open", "SAL")
        self.assertEqual([], blocked, f"销售经理被 ACL 挡在有关联项目之外：{blocked[:6]}")

    def test_sales_reads_at_least_what_the_owner_reads(self):
        manager, sales = self._by_route("open", "DIR"), self._by_route("open", "SAL")
        diff = [path for path in self._routes()
                if manager[path]["status"] != sales[path]["status"]]
        self.assertEqual([], diff, f"销售角色池与经理读到的结果不一致：{diff[:6]}")

    def test_sales_cannot_read_independent_project(self):
        visible = [path for path in self._routes()
                   if self._by_route("plain", "SAL")[path]["status"] == 200]
        self.assertEqual([], visible, f"销售经理读到了没有来源报价关联的项目：{visible[:6]}")

    def test_unrelated_account_gets_not_found_on_every_read(self):
        for project in ("open", "plain", "arch"):
            visible = [path for path in self._routes()
                       if self._by_route(project, "V")[path]["status"] == 200]
            self.assertEqual([], visible, f"无关账号读到了 {project} 项目：{visible[:6]}")

    def test_director_and_general_manager_keep_broad_read(self):
        for who in ("DIR", "GM"):
            blocked = self._acl_blocked("open", who)
            self.assertEqual([], blocked, f"{who} 在有关联项目上被 ACL 挡住：{blocked[:6]}")

    def test_archived_project_is_not_readable_by_role_pools(self):
        for who in ("FIN", "SAL", "V"):
            visible = [path for path in self._routes()
                       if self._by_route("arch", who)[path]["status"] == 200]
            self.assertEqual([], visible, f"{who} 读到了已归档项目：{visible[:6]}")

    def test_owner_is_never_told_his_role_is_insufficient(self):
        """归档对属主是「按不存在处理」的读，不是「角色只能查看」的 403。"""
        owner = self._by_route("arch", "A")
        forbidden = [path for path in self._routes() if owner[path]["status"] == 403]
        self.assertEqual([], forbidden, f"属主被自己的项目判成「角色不够」：{forbidden[:6]}")

    def test_list_and_single_project_agree(self):
        """清单里出现的项目，点进去不能 404；反之也不允许。"""
        for who in ("FIN", "SAL"):
            listed = set(self.out["list_%s" % who])
            self.assertTrue(self.out["open"] in listed,
                            f"{who} 的清单里看不到有关联项目（报价首页技术清单会因此为空）")
            single = self.out["single_all_%s" % who]
            self.assertFalse(single["acl"],
                             f"{who} 在清单里看得到项目，点进去却是 ACL {single['status']}")
            for label, pid in (("open", self.out["open"]), ("plain", self.out["plain"]),
                               ("arch", self.out["arch"])):
                plain_get = self._single_get(label, who)
                in_list = pid in listed
                self.assertEqual(in_list, not plain_get["acl"],
                                 f"{who} 的清单与单项目读不一致：{label} "
                                 f"in_list={in_list} single={plain_get['status']}")

    def _single_get(self, label, who):
        by_route = self._by_route(label, who)
        return by_route["/api/projects/{project_id}"]


class ContributeWhitelistTest(unittest.TestCase):
    """写侧：21 条专属业务动作必须由接口自己的门禁说话，而不是被 ACL 提前挡掉。"""

    @classmethod
    def setUpClass(cls):
        cls.out = probe("writes")
        cls.rows = cls.out["whitelist_rows"]
        cls.acl = cls.out["acl"]

    def _rows(self, project, who):
        return [row for row in self.rows
                if row["project"] == project and row["who"] == who]

    def test_every_whitelist_route_is_present(self):
        routes = {row["route"] for row in self.rows if row["project"] == "open"}
        expected = {"%s %s" % (method, path) for method, path, _ in CONTRIBUTE_ROUTES}
        self.assertEqual(expected, routes, "探针没有覆盖白名单全部路由")

    def test_whitelist_routes_are_not_acl_blocked_for_their_role(self):
        """Spec §18.12 #3：只看**合法角色**对「有关联项目」的请求。

        探针把无关账号（V）的同行也放在 project == "open" 里，那种行是**应当**被
        404 挡住的（由 test_unrelated_account_is_locked_on_whitelist_routes 负责），
        不能混进这条断言。
        """
        blocked = [row for row in self.rows
                   if row["project"] == "open" and row["who"] != "V" and row["acl"]]
        self.assertEqual([], blocked,
                         f"专属业务动作被 ACL 提前否决：{summarize(blocked)}")

    def test_whitelist_routes_stay_locked_on_unrelated_projects(self):
        """Spec §18.12 #4：只有**角色池可读**的财务 / 销售才按「没有关联就不存在」处理。

        `process_director` / `general_manager` 是批次 7 §5 的「全部可读」角色，
        他们对任何项目都进得去，角色够不够由接口自己的 `DIRECTOR_ROLES` 说话 ——
        那是 test_read_all_roles_reach_the_business_gate 的契约。
        """
        leaked = [row for row in self.rows
                  if row["project"] == "plain" and row["who"] in ("FIN", "SAL")
                  and not row["acl"]]
        self.assertEqual([], leaked,
                         f"没有关联的项目不该被角色池放行：{summarize(leaked)}")

    def test_read_all_roles_reach_the_business_gate(self):
        """「全部可读」角色对任何项目都不该被 ACL 挡（Spec §18.6 矩阵 + §18.12 #3）。"""
        blocked = [row for row in self.rows
                   if row["project"] == "plain" and row["who"] == "DIR" and row["acl"]]
        self.assertEqual([], blocked,
                         f"总监在无关项目上仍被 ACL 挡住（应当由 DIRECTOR_ROLES 说话）："
                         f"{summarize(blocked)}")

    def test_unrelated_account_is_locked_on_whitelist_routes(self):
        leaked = [row for row in self.rows
                  if row["who"] == "V" and not row["acl"]]
        self.assertEqual([], leaked, f"无关账号被放行了专属业务动作：{summarize(leaked)}")

    def test_generic_project_write_still_needs_project_level_permission(self):
        """可见性不自动等于写权限：通用项目修改仍要 403 ACL 文案。"""
        rows = self.out["generic_rows"]
        wrong = [row for row in rows
                 if row["status"] != 403 or row["detail"] != self.acl]
        self.assertEqual([], wrong,
                         f"通用项目写没有按「只能查看、不能修改」挡住：{summarize(wrong)}")

    def test_engineer_cannot_edit_someone_elses_project(self):
        row = self.out["engineer_other"]
        self.assertNotEqual(200, row["status"], "工程师改掉了别人创建的项目")
        self.assertFalse(self.out["engineer_other_name_after"],
                         "被拒绝的改名却真的落盘了")

    def test_process_manager_can_still_write_generic_project(self):
        """收紧不能误伤：工艺经理的普通项目写必须照常成功。"""
        row = self.out["pm_generic_ok"]
        self.assertEqual(200, row["status"],
                         f"工艺经理的普通项目写被挡住了：{row['status']} {row['detail']}")

    def test_finance_cannot_write_cost_on_unrelated_project(self):
        row = self.out["finance_unrelated_cost"]
        self.assertTrue(row["acl"], f"财务经理改到了没有财务交接的项目：{row}")

    def test_archived_project_is_not_writable_or_readable_by_role_pools(self):
        for key in ("finance_archived_cost", "finance_archived_read", "sales_archived_read"):
            row = self.out[key]
            self.assertTrue(row["acl"], f"归档项目对角色池不是「按不存在处理」：{key}={row}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
