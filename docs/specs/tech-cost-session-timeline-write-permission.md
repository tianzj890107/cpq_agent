# Spec：2.3 成本测算的会话时间线写入权限（财务经理不再被前端伪 403 拦下）

## 背景（用户反馈）

> 我在成本测算为什么会显示这一步归工艺经理办理；财务经理没有这一步的操作权限
> 但是执行是可以正常执行的

「执行可以正常执行」和「弹出没有权限」同时出现，说明**被拦的不是那笔业务动作**，
而是同一个页面上另一类写请求。这条提示只由 `tech_app/frontend/cpq-sso.js` 的写请求
拦截器发出，它命中时**不调用 `nativeFetch`**、直接在前端伪造一个 403 —— 所以后端日志
里什么也看不到，用户却已经被提示「没有权限」。

## 事实依据（实测）

1. 拦截器（`tech_app/frontend/cpq-sso.js:167`）：

   ```js
   var allowed = state.canWrite || (state.canCost && isCostUrl(url));
   ```

   财务经理（CPQ `finance_mgr` → 技术工艺 `finance_manager`）在 `/api/me` 的能力位是
   `can_write=false` / `can_cost=true`（`tech_app/backend/main.py:597-598`，
   `auth.WRITE_ROLES = {engineer, process_manager, admin}`、
   `auth.COST_ROLES = {finance_manager, admin}`），所以只有 `isCostUrl(url)` 为真的
   写请求能过。

2. `isCostUrl()` 的白名单（`cpq-sso.js:34-41`）只有三类路径：

   ```
   /cost-review(/|$|?)        → 2.3 本体（含 /cost-review/parts/{id}、/assembly、/confirm）
   /parts/{id}/cost(/|$|?)    → 零件成本
   /integration/cost(/|$|?)   → 整机成本
   ```

   2.3 页面上的真业务动作全部落在里面，所以「执行可以正常执行」。

3. **漏掉的**是 2.3 页面同一批动作伴随写的会话时间线：`cost-review.js:92` 的
   `crPersistNote()` 每条过程文字都要
   `POST /api/projects/{id}/agent/event`（## 69 引入）。这条路径不在白名单里，于是：
   弹「这一步归工艺经理办理；财务经理没有这一步的操作权限」→ 请求根本没发出去 →
   过程文字不落库（重进项目少几条），而真正的成本动作已经成功。用户看到的就是
   「提示没权限，但执行又成功了」。

4. 同一个缺口的另一半在后端：`main.py:1902` 的 `/agent/event` 用
   `_require(user, auth.WRITE_ROLES, ...)`，`finance_manager` 不在其中 —— 即使前端放行，
   服务端仍会真 403。

5. 路由自己的 docstring 已经写清定位：「会话内容属于项目数据，不是业务产出」。
   既然不是业务产出，就不该按「谁有权改工艺参数」的门槛来管；2.3 这一步的操作者
   （财务经理）当然要能写自己那几条过程文字。

## 目标契约

### 一、前端能力分流（`tech_app/frontend/cpq-sso.js`）

- 拦截表达式形状不变：`state.canWrite || (state.canCost && isCostUrl(url))`（已被
  `test_tech_drop_readonly_bar_red` 固定）。
- `isCostUrl()` 增加「2.3 阶段页会写的会话时间线接口」：`POST /agent/event`
  （`/\/agent\/event(\?|$)/`）。会话时间线是项目数据，不是业务产出。
- **不**放行 Agent 对话：`/agent/send`、`/agent/new` 仍在白名单外 —— 「Agent 对话仅限
  工艺侧」的既有语义不变；`GET /agent/events` 是只读，本来就不经过写拦截。
- 被拦时的提示文案（`var detail = state.canCost` 那两句）不改：那说的是真业务动作，
  仍然归工艺经理。本次修的是**误拦**，不是文案。

### 二、后端角色集合（`tech_app/backend/services/auth.py` + `main.py`）

- `auth.py` 新增 `SESSION_WRITE_ROLES = set(WRITE_ROLES) | set(COST_ROLES)`：会话时间线
  （`/agent/event`）的写权限 = 工艺侧写角色 + 成本侧角色。
- `main.py` 的 `agent_event()` 把 `auth.WRITE_ROLES` 换成 `auth.SESSION_WRITE_ROLES`，
  并保留原提示语作为兜底。
- 只动这一处：`/agent/send`、`/agent/new`、`/integration/params/*` 的角色集合全部不变；
  `auth.COST_ROLES`、`auth.WRITE_ROLES`、`cpq_sso.ROLE_MAP` 的值一个不动
  （`test_tech_cost_role_gate_capability_red` 固定了它们）。
- 会话时间线不做阶段限制：前端只能看到 URL、看不到请求体，URL 级白名单与阶段级后端
  规则一旦不一致，就会重新出现「前端放行、后端 403」这类只在某一步偶发的伪权限提示。
  条目本身只是会话文字（显示用），不是业务产出，放宽到「项目内能动手的人」即可。

## 禁止事项

- 不改 `/agent/send`（Agent 对话）与 `/agent/new` 的权限，不让财务经理越界到工艺侧。
- 不改 `#ocTaskProgressHost`、`tech-session-timeline.js` 的排序 / 去重口径、
  `POST /agent/event` / `GET /agent/events` 的请求与响应结构。
- 不删 `state.canWrite || (state.canCost && isCostUrl(url))`、`status: 403`、`toast(detail)`。
- 不放宽后端任何业务路由（`/cost-review/*` 仍 `COST_ROLES`、`/integration/params/*` 仍
  `WRITE_ROLES`、写库 / 审核 / 发布仍各自的门）。

## 验收

1. Node 真跑 `cpq-sso.js`（打桩 `/api/me` 返回 `can_write=false` / `can_cost=true`）：
   - `POST /api/projects/{id}/agent/event` → 放行（native 被调用、状态 200）；
   - `POST /api/projects/{id}/cost-review/confirm` → 放行；
   - `POST /api/projects/{id}/agent/send`、`POST /integration/params/finalize` → 仍被拦（伪 403）。
2. 真跑 `isCostUrl()`：`/agent/event` 为真，`/agent/send`、`/agent/new`、`/agent/meta`、
   `/integration/params/finalize`、`/requirements/{id}/confirm`、`/parse` 为假；三类既有
   成本路径仍为真。
3. 后端 TestClient：`finance_manager` `POST /agent/event` → 200 且落库；
   `process_manager` 同样 200（既有能力不变）；`sales_manager` → 403；
   `finance_manager` `POST /agent/send` → 403。
4. 回归：`test_tech_drop_readonly_bar_red`、`test_tech_cost_role_gate_capability_red`、
   `test_tech_session_timeline_persistence_red`、`test_tech_params_autofill_and_soft_gates_red`、
   `test_tech_integration_params_step_ownership_red` 全绿；全量不得新增失败。
