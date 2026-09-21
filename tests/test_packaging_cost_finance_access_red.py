"""红测：包装 2.3 成本测算的财务可见性与写角色口径。

Spec：`docs/specs/packaging-cost-finance-access.md`
依赖：`tech_project_acl_visible_scope.md` §18（可见性与角色池）、`packaging-cost-engine.md`（第 7 批）。

现状缺口（34 线上实测，不是推断）：

  · 项目 `f1417060ae9d`：PE1（工艺经理）跑 `POST /requirement/packaging-cost` → 200，
    `total_cost = 6.412359` 元/件；**同一个接口** FI1（CPQ 财务经理）→
    404 `{"detail": "项目不存在", "trace_id": "721685b955f94d93"}`；
  · 财务对项目的可见性只认 `participants` 里的 `cost_task_assignee` 或
    `plan.finance_handoff`，而全仓 `store.add_participant` 的生产调用点为 0、
    包装链路也没有"送财务"一步 → 包装项目的 2.3 对财务**永远 404**；
  · `packaging_cost.COST_WRITE_ROLES = packaging_match.BOX_MATCH_DECIDE_ROLES`（工艺侧）
    与 `auth.COST_ROLES = {finance_manager, admin}`（通用 2.3）两套口径并存，且成本记录里
    没有 `computed_by` / `computed_by_role`，事后分不清"谁算的"。

不许放宽的口径：可见性只加不减；归档项目仍只对 owner 可见；可见不等于可写。

纪律：全部离线（不连 PG、不调模型、不起服务），只打桩 store / packaging_cost 的读取。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import copy
import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ACCESS = importlib.import_module("tech_app.backend.services.project_access")
COST = importlib.import_module("tech_app.backend.services.packaging_cost")
AUTH = importlib.import_module("tech_app.backend.services.auth")
STORE = importlib.import_module("tech_app.backend.storage.store")
MATCH = importlib.import_module("tech_app.backend.services.packaging_match")

COST_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_cost.py"
PROJECT_ID = "60a1b2c3d4e5"

#: CPQ 侧账号形状（与 cpq_sso 映射一致：tech role 由 cpq_role_code 决定）。
FINANCE = {"username": "FI1", "role": "finance_manager", "cpq_role_code": "finance_mgr",
           "role_code": "finance_mgr", "display_name": "财务经理"}
PROCESS = {"username": "PE1", "role": "process_manager", "cpq_role_code": "process_mgr",
           "role_code": "process_mgr", "display_name": "工艺经理"}


def meta(*, owner="PE1", deleted_at=None, participants=None):
    return {"project_id": PROJECT_ID, "owner": owner, "current_holder": owner,
            "participants": list(participants or []), "deleted_at": deleted_at}


class CostAccessCase(unittest.TestCase):
    maxDiff = None

    def patch_reads(self, *, cost_record=None, meta_row=None, plan=None):
        patches = [
            mock.patch.object(STORE, "load_meta", lambda pid: copy.deepcopy(meta_row)),
            mock.patch.object(COST, "load_cost", lambda pid, req="", scenario=None:
                              copy.deepcopy(cost_record or {"built": False})),
        ]
        integration = importlib.import_module("tech_app.backend.services.integration")
        patches.append(mock.patch.object(integration, "load_plan",
                                         lambda pid: plan if plan is not None else object()))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def can_read(self, user, row):
        self.patch_reads(meta_row=row)
        return ACCESS.can_read(user, row)

    def assert_visible_to_finance(self, *, row, cost_record):
        """财务 + 已算出包装成本 → 必须可见（今天 False）。"""
        self.patch_reads(meta_row=row, cost_record=cost_record)
        self.assertTrue(ACCESS.can_read(FINANCE, row),
                        "项目已经算出包装成本，财务账号必须看得见它（Spec §2.1）；"
                        "34 实测今天是 404「项目不存在」")
        return ACCESS.require_project_access(PROJECT_ID, FINANCE, "read")


# --------------------------------------------------------------------------- #
# A 组：财务可见性（Spec §2.1）
# --------------------------------------------------------------------------- #
class AFinanceVisibility(CostAccessCase):

    def test_a1_built_cost_makes_the_project_visible_to_finance(self):
        self.assert_visible_to_finance(row=meta(),
                                       cost_record={"built": True, "total_cost": 6.412359})

    def test_a2_require_project_access_does_not_say_not_found(self):
        self.patch_reads(meta_row=meta(), cost_record={"built": True})
        try:
            returned = ACCESS.require_project_access(PROJECT_ID, FINANCE, "read")
        except ACCESS.ProjectAccessError as exc:
            self.fail("财务读一个已算出成本的项目不该被挡：code=%s message=%s" % (exc.code, exc.message))
        self.assertEqual(returned.get("project_id"), PROJECT_ID)

    def test_a3_without_any_cost_a_finance_user_is_still_invisible(self):
        self.patch_reads(meta_row=meta(), cost_record={"built": False})
        self.assertFalse(ACCESS.can_read(FINANCE, meta()),
                         "还没算过成本就不给可见性 —— 这条依据不许被放宽成「财务都能看」（Spec §2.1）")

    def test_a4_archived_project_stays_owner_only(self):
        row = meta(deleted_at="2026-09-22T00:00:00+08:00")
        self.patch_reads(meta_row=row, cost_record={"built": True})
        self.assertFalse(ACCESS.can_read(FINANCE, row),
                         "归档规则优先，不因「算过成本」解锁（Spec §2.1）")

    def test_a5_visibility_is_not_write_permission(self):
        self.patch_reads(meta_row=meta(), cost_record={"built": True})
        self.assertFalse(ACCESS.can_write(FINANCE, meta()),
                         "看得见 ≠ 能改项目（Spec §2.1）")


# --------------------------------------------------------------------------- #
# B 组：写角色口径（Spec §2.2）
# --------------------------------------------------------------------------- #
class BWriteRoles(CostAccessCase):

    def test_b1_roles_are_not_an_alias_of_the_box_match_roles(self):
        source = COST_PY.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assigned = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned[target.id] = node.value
        node = assigned.get("COST_WRITE_ROLES")
        self.assertIsNotNone(node, "packaging_cost 必须显式定义 COST_WRITE_ROLES（Spec §2.2）")
        self.assertNotIsInstance(node, ast.Attribute,
                                 "COST_WRITE_ROLES 不许直接别名到 packaging_match 的角色集（Spec §2.2）")
        if isinstance(node, ast.Call):
            self.fail("COST_WRITE_ROLES 不许由调用推导（Spec §2.2）：%s"
                      % ast.dump(node, annotate_fields=False)[:120])
        self.assertTrue(isinstance(node, (ast.Set, ast.Tuple, ast.List)),
                        "COST_WRITE_ROLES 必须是写死的字面量集合（Spec §2.2）")

    def test_b2_roles_relationship_with_generic_cost_roles_is_written_down(self):
        source = COST_PY.read_text(encoding="utf-8")
        self.assertTrue("COST_ROLES" in source,
                        "必须在同一处说明与 auth.COST_ROLES（通用 2.3 口径）的关系（Spec §2.2）")
        roles = set(getattr(COST, "COST_WRITE_ROLES") or ())
        self.assertTrue(roles, "COST_WRITE_ROLES 不能是空集")
        self.assertTrue(roles <= set(AUTH.ROLES),
                        "角色码必须是 auth.ROLES 闭集内的值：%s" % sorted(roles - set(AUTH.ROLES)))
        finance_or_traced = roles <= set(AUTH.COST_ROLES) or "computed_by_role" in source
        self.assertTrue(finance_or_traced,
                        "要么与通用 2.3 同口径（财务专属），要么留痕说明谁算的（Spec §2.2）")

    def test_b3_process_side_is_still_allowed_or_explicitly_excluded(self):
        roles = set(getattr(COST, "COST_WRITE_ROLES") or ())
        if "process_manager" in roles:
            source = COST_PY.read_text(encoding="utf-8")
            self.assertIn("computed_by_role", source,
                          "工艺侧可以代算时必须留痕（Spec §2.2/§2.3）")


# --------------------------------------------------------------------------- #
# C 组：成本记录留痕（Spec §2.3）
# --------------------------------------------------------------------------- #
class CCostRecordProvenance(CostAccessCase):

    def test_c1_record_declares_who_computed_it(self):
        source = COST_PY.read_text(encoding="utf-8")
        for key in ("computed_by", "computed_by_role"):
            self.assertTrue('"%s"' % key in source,
                            "成本记录必须带 %s（Spec §2.3）" % key)

    def test_c2_build_cost_signature_still_takes_the_actor(self):
        fn = getattr(COST, "build_cost")
        params = fn.__code__.co_varnames[: fn.__code__.co_argcount]
        self.assertIn("actor", params, "build_cost 必须拿到发起人（Spec §2.3）")


if __name__ == "__main__":
    unittest.main()
