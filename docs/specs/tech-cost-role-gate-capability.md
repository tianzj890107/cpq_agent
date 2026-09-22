# Spec：2.3 成本测算的角色判定改为「服务端能力位优先」

状态：Spec + 红测（已实现）
红测：`tests/test_tech_cost_role_gate_capability_red.py`

## 背景（用户反馈）

财务经理在 2.3 点业务动作（例：「回传销售经理继续报价」）报：

```
⚠ 回传销售经理继续报价失败，请查看看板提示。
⚠ 2.3 成本测算是财务经理的步骤；当前登录的是「财务经理」，这一页只能查看。请用财务经理账号登录后测算。
```

自己就是财务经理，却被判成「只能查看」——前后自相矛盾。

## 根因（源码已核实）

- 登录态有两个口径：
  - `window.cpqAuth.user()`（`/auth/me`，由 `cpq_auth.py` 提供）里的角色码是 **CPQ 口径**：
    `sales_mgr` / `process_mgr` / **`finance_mgr`**（`cpq_auth.py:33-39`）。
  - 技术工艺内部口径是 `process_manager` / **`finance_manager`**（`tech_app/backend/services/auth.py`，
    由 `cpq_sso.ROLE_MAP` 把 `finance_mgr → finance_manager` 映射过来）。
- `cost-review.js:34` 只写了内部口径的 `CR_COST_ROLES = ['finance_manager', 'admin']`，
  而 `crUser` 来自 CPQ 登录态 → `role_code === 'finance_mgr'` 永远不在白名单里，
  于是**真财务经理被前端判成只读**：动作被前端挡下、写请求也发不出去，
  会话里就看到上面那两条。
- 后端本身是对的：`auth.COST_ROLES = {"finance_manager", "admin"}`，
  `cpq_sso.ROLE_MAP` 把 CPQ 财务经理映射成 `finance_manager`，
  `/api/...` 能力位里 `can_cost = true`（`main.py` 的 sso 段）。

## 范围

- 只改 `tech_app/frontend/cost-review.js` 的只读判定（以及被本批推翻的旧断言）。
- 不动：后端权限与路由、`cpq_sso.ROLE_MAP`、`auth.COST_ROLES`、`cpq-sso.js` 的写拦截、
  `crOpAction` / `crConfirmCost` / `crRunOp` 等业务实现、看板动作名。

## R1 判定顺序：服务端能力位优先

- R1.1 首选 `window.CpqSso.state()` 的 `canCost`（服务端按技术工艺角色算好的能力位，
  唯一权威）——`enabled && checked` 时才采信。
- R1.2 拿不到能力位时退回角色码白名单，且**两套口径都收**：
  `finance_mgr`（CPQ）、`finance_manager`（技术工艺）、`admin`；角色码同时读
  `role_code` 与 `role`（两种登录态字段名）。
- R1.3 都拿不到身份时不拦（交给后端 403），保持「取不到身份不误判」的既有口径。

## R2 能力不缩水

- R2.1 真闸门仍在：非财务角色（如工艺经理 `process_mgr` / `process_manager`）在 2.3 仍是只读，
  且 `crReadOnlyWhy()` 的原因文案保留。
- R2.2 后端 `auth.COST_ROLES`、`cpq_sso.ROLE_MAP`、`cpq-sso.js` 的写拦截与 403 预判不动。
- R2.3 代价测算 / 确认 / 写入数据库 / 回传报价 / 提交工艺经理确认等既有动作与接口不动。

## 验收

- 红测：`tests/test_tech_cost_role_gate_capability_red.py`（先红后绿）。
  其中「动态」部分把 `cost-review.js` 的判定段抽出来在 node 里跑，
  用 CPQ 财务经理 / 技术工艺财务经理 / 工艺经理 / 身份未知四种登录态断言结果。
- 被推翻的旧断言更新 1 处（注明「契约更新（2.3 角色判定批次）」）：
  `tests/test_tech_business_actions_clickable_then_error_red.py` 里锁 `CR_COST_ROLES = ['finance_manager','admin']`
  的断言改为锁「服务端能力位优先 + 两套口径都认」。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败。
- 浏览器：CPQ 财务经理账号进 2.3 不再出现「只能查看」，能测算、确认、回传销售经理继续报价；
  工艺经理进 2.3 仍是只读并给出原因。
