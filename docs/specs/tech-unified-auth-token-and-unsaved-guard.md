# 统一认证客户端、Token 状态传播与未保存修改保护（批次 8）

假设批次 1–7 已实现。本批**只做两件事**：把登录态（Token 与身份）收成一份事实源并让它在
标签页 / iframe 之间正确地传播；给「有未保存修改」的看板加一条能拦住五个导航出口的离开协议。
不改流程门禁、不改项目 ACL（批次 7）、不改业务状态机（批次 5）、不改视觉。

---

状态：Spec + 红测（已实现）
红测：`tests/test_tech_unified_auth_token_and_unsaved_guard_red.py`

## 1. 背景与真实问题

### 1.1 三份 Token，各自为政（8A）

* 配置报价 CPQ 的令牌只存 `localStorage.cpq_auth_token`（`cpq_auth.js:17`），这是唯一事实源。
* 技术工艺十几个页面在**脚本求值时**就读的却是 `localStorage.authToken` / `cad_engine_token`
  （`tech_app/frontend/auth.js`、`account.js`、`session-guard.js`、`app.js` 等）。
* `tech_app/frontend/cpq-sso.js` 里的 `mirrorToken()` 每次身份变化时把 `cpq_auth_token`
  抄成那两份键 —— 抄写**没有单一入口**，任何一处漏抄就是「页面看着能点、每个请求都不带票」。
* 身份变化后的收敛手段是 `onIdentityChanged()` → `location.reload()`（`cpq-sso.js:117`）。
  代价是：**用户正在填的表单、会话滚动位置、当前 project/stage 全部丢掉**；
  而且 iframe 内的一次 401 也会触发整壳重载。
* `cpq_auth.js` 已经派发了两个事件（`cpq-auth-ready` / `cpq-auth-change`），
  也提供了 `cpqAuth.ready()`（同步布尔）；但技术工艺侧**没有订阅方**，只能靠重载。
* `session-guard.js` 自己再发一次 `/api/me` 探测：**同一页面三次登录态查询**
  （`session-guard.js`、`cpq-sso.js:check()`、`cpq_auth.js:refresh()`）。

### 1.2 有未保存修改时，五个出口全部无声放行（8B）

`tech_app/frontend/tech-board-bridge.js`（326 行）是父壳与看板之间**唯一**的通道，
今天的状态事件只有 `ready / action-state / task-progress / task-completed / task-failed /
selection-changed / board-status`，命令只有 `execute-action / navigate-view / refresh-data /
select-part / sync-state`：**没有任何一条与「有没有没保存的东西」有关。**

而父壳切步有五个出口，全部**直接**调 `applyStage()`（`tech-app/frontend/tech-workbench.js`）：

1. 顶部大步骤按钮（`renderTop()` 里 `data-major-step` 的 click）；
2. `#techPrev` 上一步；
3. `#techNext` 下一步；
4. `window` 的 `popstate`（浏览器前进 / 后退）；
5. 看板 iframe 发来的 `cpq:tech-workbench:exit` 消息 → `window.location.href = target`。

看板里正在填的参数 / 工序 / 成本说明**没有任何保存保护**：切步即丢，且不会提示。
`beforeunload` 也没有兜底。

---

## 2. 用户角色与用户故事

* 作为工艺工程师，我在「参数推荐」里改了尺寸忘了保存，点「下一步」时应该有人问我一句，
  而不是让我回来发现改动没了。
* 作为工艺工程师，我在 CPQ 重新登录（或换个账号）后，**应该停在原来的项目和步骤上**继续做完，
  而不是被整页重载打回第一步。
* 作为同时开着两个标签页的用户，我在 A 标签登出后，B 标签也要立刻变成未登录，
  不能一边显示着「xx 已登录」一边每个请求都 401。
* 作为嵌在 iframe 里的阶段页，我不该维护第二份登录状态，也不该由我自己重载整个工作台。
* 作为只读账号，我打开纯查看的页面时，不该被「有未保存修改」拦住 —— 因为我根本没有可改的东西。

---

## 3. 当前流程

### 3.1 登录态

```
CPQ 登录框（cpq_auth.js）  →  localStorage.cpq_auth_token
                              ↓ mirrorToken()（cpq-sso.js，无单一入口）
                          localStorage.authToken / cad_engine_token
                              ↓ 十几个页面在脚本求值时各读一次
                          局部变量（刷新前不会再读）
身份变化（storage / cpq-auth-change）  →  cpq-sso.js: onIdentityChanged()
                                       →  location.reload()   ← 唯一收敛手段
```

### 3.2 离开一个有改动的看板

```
点「下一步」/ 顶部步骤 / 浏览器后退 / 看板要求退出
        ↓  （什么都不问）
applyStage(next)  或  location.href = target
        ↓
未保存的编辑丢失，无提示、无留痕
```

---

## 4. 目标流程

### 4.1 登录态（8A）

```
                       ┌────────────────────────────────────────┐
localStorage           │ tech-auth-session.js（新，每页第一个）  │
  cpq_auth_token  ◀──▶ │  TechAuth.token()      唯一事实源读取   │
  authToken       ◀──▶ │  TechAuth.setToken(t)  唯一写入口        │
  cad_engine_token ◀──▶│  TechAuth.clear()      登出清全部兼容键  │
                       │  TechAuth.subscribe(fn) 唯一订阅入口     │
                       │  TechAuth.bindContext() 记住 project/stage│
                       │  TechAuth.ready()       首次登录态就绪   │
                       └────────────────────────────────────────┘
                                    ↓ 订阅
                    各页面（不再在脚本求值时各抄一份）
                                    ↓ storage / cpq-auth-change
                    TechAuth 广播 {token, previous, source}
                                    ↓
        cpq-sso.js 就地重取身份（CpqSso.refresh()）—— **不整页重载**
```

### 4.2 离开一个有改动的看板（8B）

```
看板有未保存修改  →  发 state dirty-state {dirty:true, reason, source}
                      父壳 state.dirty = true（snapshot().dirty 可读）

用户触发五个出口之一
        ↓
父壳 guardLeave(reason, {target})
        ↓ not attached → 直接放行（纯查看页面不误拦）
        ↓ !dirty     → 直接放行
        ↓ dirty      → 发命令 request-leave {reason, target}，等看板答复
                        ├─ cancel  → 停在原地（quiet，不进会话流，不弹错误）
                        ├─ save    → 发 save-draft，看板回 dirty-state{false} → 放行
                        ├─ discard → 发 discard，看板回 dirty-state{false} → 放行
                        ├─ 看板自行 leave-approved → 一次性放行
                        ├─ ok:false / 超时 → **不放行**，并给一条非 quiet 的失败（不能静默丢改动）
                        └─ 握手期间看板被切走(detach) → 不放行，且不再补发 save-draft/discard
        ↓ 放行
applyStage(target)  或  window.location.href = target
```

---

## 5. 状态定义与状态转换

### 5.1 登录态（TechAuth）

| 名称 | 取值 | 说明 |
| --- | --- | --- |
| `token` | string | 唯一事实源的值；空串 = 未登录 |
| `isReady()` | boolean | 首次「迁移 + 广播」是否已完成 |
| `ready()` | Promise<{token,user}> | 首次就绪即可 resolve；**无票也必须 resolve（不能挂起）** |
| `embedded()` | boolean | `window.parent !== window` |
| `context()` | `{project, stage}` | 与登录态解耦，身份变化不得清空 |

`token` 的取值顺序：`cpq_auth_token` → （无票时）`authToken` → `cad_engine_token`。
一旦读出兼容键的值，`migrate()` 必须把它**写进唯一事实源并删掉兼容键**（一次性迁移）。
`setToken()` / `clear()` 之后，三份键必须**同时**是「新值 / 新值」或「全空」，不允许只写一份。

### 5.2 未保存修改（TechBoardBridge）

| 名称 | 取值 | 说明 |
| --- | --- | --- |
| `state.dirty` | boolean | 看板送上来的未保存标记（`snapshot().dirty` 可读） |
| `markDirty(reason)` | — | 父壳侧主动标记（典型来源：Agent 自动回填），`source='agent'` |
| `shouldWarnOnUnload()` | boolean | `attached && dirty`；页面用它挂 `beforeunload` |
| `guardLeave(reason, opts)` | Promise<boolean> | `true` = 可以离开，`false` = 用户取消 / 放行不了 |

`dirty-state` 的 `source` 取值：`board`（用户在看板里改的）/ `agent`（Agent 回填的）/ `server`（保存成功）。
**任何一种 source 都要把 `state.dirty` 设成 payload 里的值** —— 来源不同不代表可以不拦。

---

## 6. 接口与数据契约

### 6.1 新增模块 `tech_app/frontend/tech-auth-session.js`

必须由**每个技术工艺页面在最前面加载**（在 `cpq-sso.js` 与任何读 token 的页面脚本之前），
并暴露 `window.TechAuth`：

```js
window.TechAuth = {
  TOKEN_KEY: 'cpq_auth_token',
  LEGACY_KEYS: ['authToken', 'cad_engine_token'],
  token(): string,                       // 唯一事实源（含兼容读取）
  setToken(token, opts?): void,          // 唯一写入口：写三份键 + 广播
  clear(): void,                         // 登出：清三份键 + 广播
  migrate(): string,                     // 兼容键 → 唯一事实源；返回生效值
  subscribe(fn): function,               // fn({token, previous, source})；返回退订函数
  bindContext(ctx): void,                // {project, stage}
  context(): {project, stage},
  embedded(): boolean,
  broadcast(type, detail): void,         // iframe → 父壳；namespace 'cpq:tech-auth'
  isReady(): boolean,
  ready(): Promise,
}
```

约束：

* `setToken` / `clear` **绝不调用 `location.reload`**，也**绝不写 `sessionStorage`**
  （iframe 与父壳共用同一份 `localStorage`，不额外维护每帧一份）。
* `subscribe` 的回调必须**同步**收到通知（先改值再广播），页面据此就地重取身份。
* `setToken` / `clear` **不得改动** `context()`。

### 6.2 `tech_app/frontend/cpq-sso.js`（改造）

* `mirrorToken()` 的抄写职责整体移入 `TechAuth`；本文件**不得再自己写兼容键**
  （只能 `TechAuth.setToken` / `TechAuth.clear`）。
* `onIdentityChanged()` **删掉 `location.reload()`**，改为：`TechAuth.migrate()` →
  重新拉一次 `/api/me`（`CpqSso.refresh()`）→ 用新身份 `apply()`。
  过程中 `TechAuth.context()` 的 `project` / `stage` 必须原样保留。
* `CpqSso.token()` 必须**等于** `TechAuth.token()`（不允许出现第二份事实源）。
* `CpqSso.refresh()` 必须用**当前** `TechAuth.token()` 作为 `Authorization: Bearer`，
  这样「CPQ 里换了账号 → 订阅者就地刷新」才成立。
* `TechAuth` 不存在时（旧壳 / 脚本未加载）必须安全降级：不抛错、不重载、只打一条 console 提示。

### 6.3 `auth.js` / `account.js` / `session-guard.js`（改造）

* 一律通过 `TechAuth` 读写令牌；**删除**自己 `localStorage.setItem('authToken', ...)` /
  `('cad_engine_token', ...)` 的写法。
* `session-guard.js` 在 `TechAuth.isReady()` 之前不得自作主张发 `/api/me`；
  同一页面只允许一次「首次登录态查询」（由 `TechAuth.ready()` 统一）。

### 6.4 `tech-board-bridge.js` 新增协议（版本仍为 1，向后兼容）

父壳 → 看板（命令）：

| 命令 | payload | 看板回复 payload |
| --- | --- | --- |
| `request-leave` | `{reason, target}` | `{ok:true, result:{decision:'save'\|'discard'\|'cancel'}}` |
| `save-draft` | `{reason}` | `{ok:true, result:{saved:true}}` |
| `discard` | `{reason}` | `{ok:true, result:{discarded:true}}` |

看板 → 父壳（状态事件）：

| 事件 | payload |
| --- | --- |
| `dirty-state` | `{dirty: boolean, reason, source: 'board'\|'agent'\|'server'}` |
| `leave-approved` | `{reason, target, decision}` |

父壳新增 API：`markDirty(reason)`、`guardLeave(reason, opts)`、`shouldWarnOnUnload()`，
以及 `snapshot().dirty`。`reason` 白名单：

`'stage-switch'`（顶部大步骤）、`'prev'`、`'next'`、`'popstate'`、`'exit-project'`。

`guardLeave(reason, opts)` 的契约（逐条可测）：

* `opts` 支持 `{target, timeout}`（`timeout` 默认沿用 `DEFAULT_TIMEOUT`）。
* **未 attach** → 立即 resolve `true`（纯查看页面不误拦）。
* **未 dirty** → 立即 resolve `true`，**且不发 `request-leave`**。
* dirty → 发一条 `request-leave`，按下表 resolve：

  | 看板答复 | 父壳动作 | resolve |
  | --- | --- | --- |
  | `{decision:'cancel'}` | 无 | `false`（quiet） |
  | `{decision:'save'}` | 发 `save-draft` | 等看板广播 `dirty-state{false}` 后 `true` |
  | `{decision:'discard'}` | 发 `discard` | 等看板广播 `dirty-state{false}` 后 `true` |
  | `leave-approved` 事件 | 无 | `true`（一次性，用掉即失效） |
  | `ok:false` | 无 | `false` + **非 quiet** 失败 |
  | 超时 | 无 | `false` + **非 quiet** 失败 |

* **放行的唯一判据是「此刻 `state.dirty === false`」**：`save-draft` 回了
  `{ok:true, result:{saved:true}}` **不足以**放行，仍须等看板广播
  `dirty-state{dirty:false}`（或 `leave-approved`）。任何在握手期间到达的
  `markDirty` / `dirty-state{dirty:true}` 都会让本次放行作废、回到 `false`。
* 同一 `reason` 的握手**在途去重**：第二次调用复用同一 Promise，只发一条 `request-leave`。
* 握手期间看板被 `detach` → `false`，错误码 `detached`（quiet），
  **不再**补发 `save-draft` / `discard`，也不再发出导航命令。

父壳侧还必须导出可探查的协议清单，供页面与测试共用：

```js
TechBoardBridge.PROTOCOL = {
  commands: [...],   // 既有 5 条 + request-leave / save-draft / discard
  events:   [...],   // 既有 7 条 + dirty-state / leave-approved
};
```

### 6.5 `tech-workbench.js`：五个出口共用一条通道

`tech_app/frontend/tech-workbench.js` 必须新增唯一的导航闸门：

```js
async function guardedStage(stageId, reason) {
  const bridge = window.TechBoardBridge;
  if (bridge && typeof bridge.guardLeave === 'function') {
    const ok = await bridge.guardLeave(reason, { target: stageId });
    if (!ok) return;                       // 用户取消 / 放行不了 → 停在原地
  }
  applyStage(stageId, { project: state.project });
}
```

五个出口（顶部大步骤、`#techPrev`、`#techNext`、`popstate`、`cpq:tech-workbench:exit`）
必须全部改为走 `guardedStage`（退出项目那条在放行后才 `window.location.href = target`）；
页面还必须用 `TechBoardBridge.shouldWarnOnUnload()` 挂 `beforeunload` 兜底。
桥缺失时 `guardedStage` 必须**直接导航**（降级，不产生死路）。

必须保留：`namespace='cpq:tech-board'`、`version=1`、现有全部命令与事件名、
`QUIET_FAILURE_CODES`、`accepts()` 的 origin / source / projectId / stage 四重校验、
`requestId` 关联、`DEFAULT_TIMEOUT`。（批次 9 依赖本协议，不得改名。）

---

## 7. 正常路径

1. 用户首次打开技术工艺页面：`TechAuth` 加载 → 若有兼容键则迁移 → `isReady()` 变真 →
   `ready()` resolve → `cpq-sso.js` 拉到身份、无遮罩。
2. 用户在 CPQ 里登录：`cpq_auth.js` 写 `cpq_auth_token` 并派发 `cpq-auth-change` →
   `TechAuth` 订阅者收到（`source='local'`）→ 三份键一致 → 各页面**就地**重取身份 →
   页面**不重载**，`project` / `stage` 不变。
3. 用户在另一个标签页登出：`storage` 事件 → `TechAuth` 广播（`source='storage'`）→
   本标签页清掉兼容键、遮罩出现、`context()` 不变。
4. 看板改了一个尺寸但没保存 → `dirty-state{dirty:true, source:'board'}` →
   点「下一步」→ 弹看板的保存 / 放弃 / 取消 → 选「保存」→ `save-draft` →
   `dirty-state{false}` → 切步成功。
5. Agent 自动回填了一批参数 → 父壳 `markDirty('agent-autofill')` → 之后再点出口照样被拦。

## 8. 异常路径

| 场景 | 期望 |
| --- | --- |
| `request-leave` 超时 | 不放行；给一条**非 quiet** 的失败（写明「未确认是否已保存」），看板仍在原步 |
| 看板答复 `ok:false` | 不放行；失败原因来自看板，非 quiet |
| 握手期间看板被切走（`detach`） | 不放行；**不再**补发 `save-draft` / `discard`；失败码 `detached`（quiet，因为用户自己切走了） |
| 用户选「取消」 | 停在原地；**quiet**：不写左侧会话流、不弹错误 toast、`state.error` 不变 |
| 看板未 attach（纯查看 / 未加载） | `guardLeave` 直接放行；`shouldWarnOnUnload()` 为 false → 不误拦 |
| `TechAuth` 脚本缺失 | `cpq-sso.js` / `session-guard.js` 安全降级，不抛错、不重载 |
| `/api/me` 返回 401 | 清掉三份键 + 遮罩；**不整页重载**；`context()` 不变（重新登录后回到原步骤） |
| `dirty-state` payload 缺失 `dirty` | 视为 `dirty:true`（宁可多问一句，不可静默丢改动） |
| `localStorage` 抛错（隐私模式） | 全部读写被 try/catch 包住，`token()` 退化为空串，不抛到调用方 |

## 9. 并发与幂等要求

* 同一 `reason` 的 `guardLeave` 在握手未结束时再次调用，必须**复用同一个 Promise**，
  且只发出**一条** `request-leave`（去重，防止连点「下一步」发一串命令）。
* `guardLeave` 与 `markDirty` 并发：`markDirty` 在握手期间把 `dirty` 置回 true 时，
  这次放行作废，必须回到「被拦」。
* `leave-approved` 是**一次性**的：用掉即失效，不得让后续第二次导航无审放行。
* `subscribe` 可多次注册；单个订阅者抛错不得影响其它订阅者（沿用 `notify()` 现有语义）。
* `migrate()` 幂等：重复调用不改变结果；已迁移过的键不会被再次写回。

## 10. 刷新、重试、重复点击与服务重启

* 刷新页面：`TechAuth` 重新迁移 + `ready()`；`project` / `stage` 来自 URL（批次 1），
  登录态变化不得把它清掉。
* 五个出口全部走同一个 `guardLeave`；连点「下一步」不会发出第二条 `request-leave`。
* 服务端保存成功（看板回 `{saved:true}` 或广播 `dirty-state{false, source:'server'}`）后
  立即清 dirty，之后的导航不再被拦。
* 服务重启导致 `/api/me` 网络失败：`TechAuth.ready()` 仍必须 resolve（拿不到就按未登录），
  不能挂住页面；`cpq-sso.js` 保留「登录服务暂不可用」的既有遮罩文案。
* 旧壳（未加载 `TechAuth`）：行为不得回归 —— 不得出现「既不重载也没有人收到通知」的死状态。

## 11. 权限边界

* 本批不改任何后端权限判定：技术工艺的真实权限仍在后端（`services/auth.py` / `WRITE_ROLES` 等）。
* `guardLeave` 只保证「不静默丢用户输入」，**不是**权限门禁；
  只读账号（`can_write=false`）的页面本来就没有可改内容，因此不该被拦
  （看板本就不会发 `dirty-state{true}`）。
* 未登录（无票）仍然由既有的登录遮罩处理，不由本批新增拦截。

## 12. 历史数据兼容

* 已有 `localStorage.authToken` / `cad_engine_token`（现在还活着的会话）必须被
  `migrate()` 读出来并迁移；迁移后**删除**兼容键，避免长期两份。
* 已有 `cpq_auth_token` 不变。
* 看板仍未升级（不发 `dirty-state`）时，父壳 `state.dirty` 恒为 false，
  行为与今天完全一致 —— 不得因为协议存在就默认 dirty。
* 不迁移、不清空、不重写任何项目 / 会话 / 任务 / 模型设置数据。

## 13. 非目标

* 不做流程门禁、项目 ACL、状态机（批次 5 / 7）。
* 不改后端 `/auth/*`、`cpq_auth.js` 的登录 / 注册 / 登出接口与 `cpq_auth_token` 的键名。
* 不做 `business_case_id`（批次 6）、不做首页信息架构与跨流程时间线（批次 10）。
* 不改看板内部各页面的保存按钮与业务语义；本批只提供协议与父壳的拦截。
* 不引入新的第三方库，不引入打包器 / 模块系统（继续用 `<script src>`）。

## 14. 可自动化验收标准

8A：

1. `tech-auth-session.js` 存在且暴露 `window.TechAuth`，`TOKEN_KEY='cpq_auth_token'`、
   `LEGACY_KEYS=['authToken','cad_engine_token']`。
2. 只有兼容键有值时，`token()` 能读出来；`migrate()` 之后唯一事实源有值、兼容键被删除。
3. 只有 `cpq_auth_token` 有值时，`token()` 返回它，且**不**被兼容键的空值覆盖。
4. `setToken('x')` 后三份键全部为 `'x'`，订阅者同步收到 `{token:'x', previous}`。
5. `clear()` 后三份键全空，订阅者收到 `{token:''}`。
6. `setToken` / `clear` 全程不调用 `location.reload`，不写 `sessionStorage`。
7. `bindContext({project,stage})` 后 `setToken` / `clear` 都不改变 `context()`。
8. `embedded()` 在 iframe 内为 true；`broadcast()` 用 `location.origin` 发给 `window.parent`，
   且消息带 `namespace='cpq:tech-auth'`。
9. `ready()` 无票也 resolve，且 `isReady()` 之后为 true。
10. `cpq-sso.js`：`CpqSso.token() === TechAuth.token()`；身份变化后**不重载**，
    且用新票重新请求了 `/api/me`。
11. `auth.js` / `account.js` / `session-guard.js` 不再各自写 `authToken` / `cad_engine_token`；
    `session-guard.js` 复用 `TechAuth` 的唯一首次查询。

8B：

12. `tech-board-bridge.js` 的 `PROTOCOL.commands` 含既有 5 条 + `request-leave` /
    `save-draft` / `discard`，`PROTOCOL.events` 含既有 7 条 + `dirty-state` /
    `leave-approved`，且 `namespace` / `version` / 既有名字一个不缺。
13. 未 attach 时 `guardLeave()` resolve `true`（纯查看页面不误拦），
    `shouldWarnOnUnload()` 为 false。
14. 干净状态下 `guardLeave()` 立即 resolve true，**不发** `request-leave`。
15. dirty → 先发 `request-leave`；答复 `save` 时发 `save-draft`，且在
    `dirty-state{false}` 到达前**不** resolve true。
16. 答复 `cancel` → resolve false，**没有** `save-draft` / `discard`，**没有** error 通知（quiet）。
17. 答复 `discard` → 发 `discard`；`dirty-state{false}` 后 resolve true。
18. 看板自行发 `leave-approved` → resolve true；该批准一次用完即失效（第二次重新被拦）。
19. 握手期间重复调用 → 只发一条 `request-leave`，两次调用共用同一结果。
20. 握手期间 `detach` → resolve false、错误码 `detached`（quiet），之后没有
    `save-draft` / `discard`。
21. 答复 `ok:false` 或超时 → resolve false，且给出**非 quiet** 失败。
22. `markDirty('agent-autofill')` → `snapshot().dirty === true`、`shouldWarnOnUnload()` true。
23. `dirty-state` 缺 `dirty` 字段 → 视为 true。
24. 握手期间 `markDirty` → 本次放行作废（resolve false）。
25. `snapshot()` / `actionState()` / `subscribe()` / `executeAction()` /
    `QUIET_FAILURE_CODES` 的既有行为不回归。
26. `tech-workbench.js` 有且只有一个 `guardedStage(` 定义、恰好一处
    `TechBoardBridge.guardLeave(` 调用，且五个出口都走 `guardedStage`
    （静态契约：脚本路由存在性），并挂了 `beforeunload` 兜底。

## 15. 人工验收场景

1. 在「参数推荐」改一个尺寸不保存 → 点「下一步」→ 出现保存 / 放弃 / 取消；选「取消」停在原步、
   无错误提示；再点一次选「保存」→ 切到下一步，回来尺寸还在。
2. 「组装工艺」改工序描述不保存 → 浏览器后退 → 同样被拦。
3. 在 CPQ 里登出再登录另一个账号 → 技术工艺页面**不整页重载**、停在原项目原步骤，
   左上角账号名变成新账号，随后的请求带新票（Network 里 `Authorization` 是新的）。
4. 两个标签页：A 登出 → B 立刻出现登录遮罩，B 的 project/stage 不变。
5. 嵌在 iframe 里的阶段页触发 401 → 外层工作台**不整页重载**。
6. 打开一个只看不写的页面（如已发布报告页），任何导航都不弹保存询问。

## 16. 不允许减少的既有能力

* `cpq_auth_token` 键名与 `Authorization: Bearer` 的 header 方式不变（不改成 Cookie）。
* 既有 `cpq-auth-ready` / `cpq-auth-change` 事件仍然派发；`cpqAuth.ready()` 语义不变。
* `CpqSso.state()` / `CpqSso.canWrite()` / `CpqSso.login()` / `CpqSso.refresh()` 继续可用，
  四档能力（`can_write` / `can_cost` / `can_review` / `can_publish`）与写请求拦截语义不变。
* 看板桥的 origin / source / projectId / stage 四重校验、`requestId` 关联、
  `QUIET_FAILURE_CODES`、`not-attached` / `detached` / `timeout` 错误码不回退。
* 「登录服务暂不可用」「请先登录」两条遮罩文案与触发条件不变。
* 未处于 dirty 状态时的导航、`execute-action` / `select-part` / `refresh-data` 行为完全不变
  （**本批只拦离开，不拦操作**）。
