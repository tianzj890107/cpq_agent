"""红测：包装成本「谁能算」只许有一个出处。

Spec：`docs/specs/packaging-cost-write-role-single-source.md`

现状缺口（2026-09-22 在 34 上真跑实测，不是推断。项目 `afe9e844f2ec` /
需求单 `REQ-AFE9E844F2EC`，`酒盒.dwg` 一键解析 8/8 completed）：

  · `FI1`（财务经理）调 `POST /api/projects/afe9e844f2ec/requirement/packaging-cost`：
      - 成本还没算过时 → **404** `{"detail": "项目不存在"}`（项目就在那儿）；
      - 工艺经理算完一次之后 → **403** `你的角色只能查看该项目，不能修改`；
      - 同一动作换 `PE1`（工艺经理）→ **200**，`total_cost=17.754993`。
  · 「财务能不能算包装成本」这件事仓里有**两个相反的答案**：
      - `services/auth.py:55` `COST_ROLES = {finance_manager, admin}`，且 `main.py:860` 的
        `can_cost` 就是它 —— 前端据此**放行**财务经理的「测算」按钮；
      - `services/packaging_cost.py:139` `COST_WRITE_ROLES = {process_manager,
        process_director, admin}` —— 服务端**没有**财务，项目 ACL 也只给财务可见性。
    于是用户看到的是「按钮点得动，点完告诉你项目不存在」。

纪律：只读源码 + 纯函数；不连 PG、不发 HTTP、不写任何文件。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import auth as auth_service  # noqa: E402
from tech_app.backend.services import packaging_cost  # noqa: E402

MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
#: 财务经理在仓里的两种写法（CPQ 角色码 / 技术工艺角色名）。
FINANCE_NAMES = ("finance_manager", "finance_mgr")
#: §2.2 要求「项目存在但还没算过成本」必须带的可判分支码（二选一）。
NOT_COMPUTED_CODES = ("cost_not_computed_yet", "project_read_only_until_cost_built")


def _source() -> str:
    return MAIN_PY.read_text(encoding="utf-8")


def _can_cost_roles(source: str):
    """从 main.py 里读出 `can_cost` 这一行引用的角色集合（不许硬编码在本测试里）。"""
    match = re.search(r'"can_cost":\s*\(user or \{\}\)\.get\("role"\)\s*in\s*([A-Za-z_.]+)',
                      source)
    if not match:
        return None
    expr = match.group(1)                       # 形如 auth.COST_ROLES
    name = expr.rsplit(".", 1)[-1]
    return getattr(auth_service, name, None)


class AOneSourceOfTruth(unittest.TestCase):
    def test_a1_auth_and_cost_service_agree_on_finance(self):
        can_cost = set(auth_service.COST_ROLES or set())
        can_write = set(packaging_cost.COST_WRITE_ROLES or set())
        bad = [name for name in FINANCE_NAMES
               if (name in can_cost) != (name in can_write)]
        self.assertEqual([], bad,
                         "财务经理能不能算包装成本，两个出处必须同口径（Spec §2.1）：\n"
                         "  auth.COST_ROLES = %s\n"
                         "  packaging_cost.COST_WRITE_ROLES = %s\n"
                         "现在前端按前者放行、服务端按后者拒绝，用户点下去只会拿到 404/403"
                         % (sorted(can_cost), sorted(can_write)))

    def test_a2_main_can_cost_agrees_with_cost_service(self):
        source = _source()
        can_cost = _can_cost_roles(source)
        self.assertIsNotNone(can_cost, "main.py 里找不到 can_cost 的角色表达式")
        can_write = set(packaging_cost.COST_WRITE_ROLES or set())
        bad = [name for name in FINANCE_NAMES
               if (name in set(can_cost or set())) != (name in can_write)]
        self.assertEqual([], bad,
                         "main.py 下发给前端的 can_cost 必须与 packaging_cost.COST_WRITE_ROLES "
                         "同口径（Spec §2.1）；现在 can_cost=%s、COST_WRITE_ROLES=%s"
                         % (sorted(can_cost or []), sorted(can_write)))


class BNotComputedIsItsOwnBranch(unittest.TestCase):
    def test_b1_not_computed_has_its_own_code(self):
        source = _source()
        found = [code for code in NOT_COMPUTED_CODES if code in source]
        self.assertTrue(found,
                        "「项目存在但还没算过成本」必须回带稳定 code 的业务错误（Spec §2.2），"
                        "取 %s 之一；现在这条路径直接复用 404「项目不存在」，"
                        "财务分辨不出『没权限』和『项目被删了』" % (NOT_COMPUTED_CODES,))

    def test_b2_real_missing_project_still_says_project_missing(self):
        source = _source()
        self.assertIn("项目不存在", source,
                      "真不存在的项目仍必须是 404「项目不存在」（Spec §2.2，一字不改）")

    def test_b3_cost_error_carries_a_code(self):
        exc = packaging_cost.CostError("x", 409, "some_code")
        self.assertEqual("some_code", getattr(exc, "code", None),
                         "CostError 必须保持 message/status_code/code 三件套（Spec §2.3）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
