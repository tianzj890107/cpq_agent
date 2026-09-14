"""红测：2.3 成本测算的角色判定改为「服务端能力位优先、两套角色码都认」。

用户反馈：财务经理自己点 2.3 的动作，却收到
「2.3 成本测算是财务经理的步骤；当前登录的是「财务经理」，这一页只能查看。」

根因：`cost-review.js` 的 `CR_COST_ROLES` 只有技术工艺口径的 `finance_manager`，
而登录态 `window.cpqAuth.user().role_code` 是 CPQ 口径的 `finance_mgr`
（`cpq_auth.ROLES`），于是真财务经理被误判只读，动作被前端挡下。

红线：真闸门（非财务角色在 2.3 只读）、后端 COST_ROLES、ROLE_MAP、
读接口与业务动作一个都不能变。
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
COST = (FRONTEND / "cost-review.js").read_text(encoding="utf-8", errors="replace")
AUTH = (ROOT / "tech_app" / "backend" / "services" / "auth.py").read_text(encoding="utf-8")
SSO = (ROOT / "tech_app" / "backend" / "services" / "cpq_sso.py").read_text(encoding="utf-8")
CPQ_AUTH = (ROOT / "cpq_auth.py").read_text(encoding="utf-8")

START = COST.find("let crUser = null;")
END = COST.find("const $cr = id =>")


def gate_snippet() -> str:
    assert START > 0 and END > START, "找不到 cost-review.js 的角色判定段"
    return COST[START:END]


def run_gate(user, sso_state):
    """把真实的判定段放进 node 跑：user / sso 是两种登录态的形状。"""
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("没有 node，无法做动态判定")
    harness = (
        "globalThis.window = {\n"
        f"  cpqAuth: {{ user: () => ({json.dumps(user, ensure_ascii=False)}) }},\n"
        f"  CpqSso: {{ state: () => ({json.dumps(sso_state, ensure_ascii=False)}) }},\n"
        "};\n"
        + gate_snippet()
        # 判定段里 crUser 是模块级变量（真实页面里由 crStart() 从登录态填）：
        # 这里按同一条路径显式赋值，测的才是真判定，不是「crUser 恰好是 null」。
        + f"\ncrUser = ({json.dumps(user, ensure_ascii=False)});\n"
        + "console.log(JSON.stringify({ readOnly: crReadOnly(), why: crReadOnlyWhy() }));\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "gate.js"
        path.write_text(harness, encoding="utf-8")
        done = subprocess.run([node, str(path)], capture_output=True, text=True, check=False)
    assert done.returncode == 0, f"判定段在本例里跑不起来：{done.stderr.strip()[:400]}"
    return json.loads(done.stdout.strip().splitlines()[-1])


class CostRoleGateIsCapabilityFirst(unittest.TestCase):
    def test_cpq_finance_manager_is_not_read_only(self):
        # 用户遇到的场景：CPQ 角色码 finance_mgr + 服务端 can_cost=true
        out = run_gate({"role_code": "finance_mgr", "role_name": "财务经理"},
                       {"enabled": True, "checked": True, "canCost": True})
        self.assertFalse(out["readOnly"], "财务经理被误判只读：" + out["why"])

    def test_tech_finance_manager_is_not_read_only(self):
        out = run_gate({"role": "finance_manager", "role_name": "财务经理"}, None)
        self.assertFalse(out["readOnly"], "技术工艺口径的财务经理也要放行：" + out["why"])

    def test_cpq_finance_code_alone_is_enough_before_sso_ready(self):
        # 登录态已到、能力位还没就绪：退回角色码白名单也不能误拦财务经理。
        out = run_gate({"role_code": "finance_mgr", "role_name": "财务经理"},
                       {"enabled": True, "checked": False, "canCost": False})
        self.assertFalse(out["readOnly"], "能力位未就绪时误拦：" + out["why"])

    def test_process_manager_stays_read_only(self):
        out = run_gate({"role_code": "process_mgr", "role_name": "工艺经理"},
                       {"enabled": True, "checked": True, "canCost": False})
        self.assertTrue(out["readOnly"], "非财务角色在 2.3 必须保持只读")
        self.assertIn("财务经理", out["why"], "只读原因要保留，说清这一步归谁")

    def test_unknown_identity_is_not_blocked(self):
        out = run_gate(None, {"enabled": False})
        self.assertFalse(out["readOnly"], "取不到身份时不拦，交给后端 403")

    def test_server_capability_wins_over_a_strange_role_code(self):
        out = run_gate({"role_code": "something_else", "role_name": "财务经理"},
                       {"enabled": True, "checked": True, "canCost": True})
        self.assertFalse(out["readOnly"], "服务端能力位是权威，不能被本地角色码否决")


class SourceContracts(unittest.TestCase):
    def test_snippet_is_found(self):
        snippet = gate_snippet()
        self.assertIn("crReadOnly", snippet)

    def test_both_role_families_are_accepted(self):
        match = re.search(r"CR_COST_ROLES\s*=\s*\[([^\]]*)\]", COST)
        self.assertTrue(match, "找不到 CR_COST_ROLES")
        codes = re.findall(r"'([^']+)'", match.group(1))
        for code in ("finance_mgr", "finance_manager", "admin"):
            with self.subTest(code=code):
                self.assertIn(code, codes, f"角色白名单要认 {code}（两套口径）")

    def test_gate_prefers_server_capability(self):
        snippet = gate_snippet()
        self.assertIn("CpqSso", snippet, "要优先读服务端算好的能力位")
        self.assertIn("canCost", snippet, "能力位字段是 canCost")
        self.assertIn("role", snippet, "退回角色码时要同时认 role_code 与 role")

    def test_capability_and_role_gate_are_both_kept(self):
        self.assertIn("const crReadOnly = ()", COST)
        self.assertIn("const crReadOnlyWhy = ()", COST)
        self.assertIn("这一页只能查看", COST, "只读原因文案保留")


class CapabilitiesNotReduced(unittest.TestCase):
    def test_backend_ownership_unchanged(self):
        self.assertRegex(AUTH, r'COST_ROLES\s*=\s*\{"finance_manager",\s*"admin"\}')
        self.assertIn('"finance_mgr": "finance_manager"', SSO,
                      "CPQ 财务经理 → 技术工艺财务经理 的映射不能动")
        self.assertIn('"finance_mgr": "财务经理"', CPQ_AUTH,
                      "CPQ 角色码本身不变")

    def test_cost_actions_and_routes_stay(self):
        for token in ("/cost-review", "crConfirmCost", "crOpAction", "crRunOp",
                      "sendCostReviewToQuote", "writeCostReviewMaterial",
                      "returnCostReviewToProcess"):
            with self.subTest(token=token):
                self.assertIn(token, COST)


if __name__ == "__main__":
    unittest.main()
