# 长任务恢复、关键失败固定展示与统一下一步引导（批次 9）

假设批次 1–8 已实现。本批**只做三件事**，且全部围绕「任务跑得久／跑挂了以后，用户还看得见、回得来、知道下一步点哪里」：

* **9A 统一任务状态与「轮询超时 ≠ 失败」**：任务状态收成一个封闭词表，补上 `cancelled`（用户主动取消）与 `unknown`（前端拿不准），并让「取消」成为一条可审计、可幂等、可恢复的正常终态；前端轮询遇到瞬时故障不得把任务判成失败，任务必须仍然留在任务中心、刷新后按 `task_id` 复原。
* **9B 关键失败的固定展示 + 错误追踪 ID**：关键失败不再只有 2.6–4.2 秒就消失的 toast，而是一条**常驻**的失败块，含五要素：原因 / 影响 / 重试 / 返回正确步骤 / 错误追踪 ID；追踪 ID 端到端贯通（后端生成 → 响应头与错误体 → 任务记录 → 前端展示）。
* **9C 安静失败口径**：「安静」（不打扰用户）只允许用在刷新类副作用上；关键失败码与安静码必须是不相交的两个集合，且「没有码的失败」默认**不安静**。

**非目标**：不改流程门禁与阶段完成条件（批次 5）、不改项目 ACL（批次 7）、不改登录态与离开协议（批次 8）、不改业务状态机语义（报价/技术/财务回传契约，批次 3/4/6）、不改视觉基线与配色。

---

状态：Spec + 红测（已实现）
红测：`tests/test_tech_long_task_recovery_and_fixed_error_guide_red.py`

## 1. 背景与真实问题

### 1.1 任务状态词表不封闭，`cancelled` 根本不存在

`tech_app/backend/services/tasks.py` 全文出现过的任务状态只有
`queued`（:126）/ `running`（:152）/ `succeeded`（:170）/ `partial`（:170）/ `failed`（:177）/
`interrupted`（:364）。**没有 `cancelled`**：用户中途不要这个任务时，没有任何一条合法的收尾路径
——只能等它自己跑完或者跑失败，或者把它留在 `running` 直到下次服务重启被 `recover_interrupted_tasks()`
误判成「中断」。

`tech_app/frontend/agent-chat.js:1363` 的 `taskStatusWord()` 只有六个词，其余**一律兜底成「进行中」**：
一个已经不存在、已经被取消、或者状态拼错的任务，在界面上永远显示为「进行中」——这是**永不收敛的假象**，
用户会一直等一个再也不会推进的卡。

`tech_app/frontend/tech-board-bridge.js:114` 的 `QUIET_FAILURE_CODES` 只在桥内部生效，页面侧没有任何
共享口径，「哪些失败可以不出声」目前是**十来个页面各自决定**。

### 1.2 轮询遇到一次网络抖动就等于任务失败

`tech_app/frontend/assembly-integration.js:317`、`cost-review.js:254`、
`agent-chat.js:1788` 都是同一个写法：

```
while (true) { await sleep(1200); const task = await api(`.../tasks/${taskId}`); ... }
```

`api()` 在 `fetch` 抛错或响应非 2xx 时**直接 throw**；`while(true)` 之外没有重试、没有上限、
没有区分「网络抖了一下」与「服务端明确说它失败了」。于是一次 1 秒的断网就把一个还在正常跑的长任务
标成「失败」，用户重试又起了第二份模型调用（重复计费）。

同一层还缺「按 `task_id` 恢复」：任务中心没有单一读取入口，页面刷新后**只能靠内存里的 taskId 变量**
回忆自己在等谁；一旦刷新，卡就断了。

### 1.3 关键失败只有 2.6–4.2 秒的 toast，且没有追踪 ID

错误出口实测（全部是自动消失的浮层，没有任何常驻形态）：

| 位置 | 出口 | 存活 |
| --- | --- | --- |
| `tech_app/frontend/workflow.js:74` | `toast()` | 2600 ms |
| `tech_app/frontend/tech-task.js:42` | 内联 box | 2600 / 4200 ms |
| `tech_app/frontend/assembly-integration.js:176` | `aiToast()` | 3200 ms |
| `tech_app/frontend/home.js:31` | `homeToast()` | 3200 ms |
| `tech_app/frontend/report-review-result.js:7` | `rrToast()` | 3200 ms |
| `tech_app/frontend/summary-result.js:12` | `srToast()` | 3200 ms |
| `tech_app/frontend/report-publish-result.js:8` | `rpToast()` | 3600 ms |
| `tech_app/frontend/requirement-confirm-page.js:6` | `cfToast()` | 3600 ms |
| `tech_app/frontend/requirement-create.js:7` | `rcToast()` | 3600 ms |
| `tech_app/frontend/requirement-review-page.js:5` | `rrToast()` | 3600 ms |
| `tech_app/frontend/workflow-navigation.js:46` | 内联浮层 | 3600 ms |

「权限不足」「回传报价失败」「主数据写入失败」「任务中断」「结果过期」这五类**都会改变业务状态**，
用户在 toast 消失后既不知道发生了什么、也不知道该回哪一步、更没法把一次故障报给运维。

同时**全仓没有任何追踪 ID**：

* `tech_app/frontend/*.js` 搜 `trace_id` / `traceId` / `request_id` / `错误追踪` → **0 命中**；
* `tech_app/backend/main.py` 搜 `trace` → **0 命中**；
* 只有 `tech_app/backend/services/openai_client.py:78` 在读上游 SDK 自带的 `request_id`，与技术工艺任务无关。

后果：线上日志里一条 `[cpq-suite] /auth 出错` 或一条任务失败，与用户屏幕上的那句话**无法对上**，
排障只能靠时间戳猜。

---

## 2. 已实现部分（本批不重复建设）

以下能力在批次 5/7 与「中断态」批已经落地并有 143 条绿测覆盖，本批**不得重做、不得反转**：

* 服务重启把在途任务置 `interrupted`（`tasks.py:343 recover_interrupted_tasks()`），并写会话时间线
  `key = task:<task_id>`；前端蓝色「⏸ 中断」chip 与「重试」按钮（`assembly-integration.js:150-152/265-324`、
  `cost-review.js:139`）。
* `agent-chat.js` 的 `taskStatusWord()` 对**未知状态仍兜底「进行中」**（`tests/test_tech_task_interrupted_state_red.py`
  的 `test_unknown_fallback_kept` 明确钉死）。本批**不改这条兜底**，只做加法：把 `cancelled` / `unknown`
  登记成显式词条。
* 「仅重试失败项」在工艺批量（`app.js:2515 retryFailedPartProcesses()` / `:2707`）与成本批量
  （`cost-review.js:579`）已存在，且 `tasks.submit()` 的 `dedup_key` 去重（`tasks.py:113-117`）已保证
  重复提交同一种昂贵任务不会起第二份。本批只做**回归护栏**，不新增第二套重试机制。
* 同一任务不生成多张 Agent 执行卡、过程事件按序回放：已有
  `tests/test_tech_task_process_stream_red.py`、`test_tech_chat_drop_red_error_cards_red.py` 等覆盖。

---

## 3. 用户角色与用户故事

* 作为**工艺工程师**，我发起了一次要跑几分钟的批量工艺推荐，中途网断了 3 秒；我希望界面上写「连接不稳定，
  结果仍在处理中」，而不是「失败」——后者会让我重跑一遍，白花一次模型费用。
* 作为**工艺工程师**，我离开页面去开会，回来时浏览器已经刷新；我希望任务中心里那张卡还在，
  点一下就能接着看进度，而不是重新发起。
* 作为**工艺工程师**，我点错了「生成成本测算」，希望有一个「取消这次任务」，并且希望被取消是**终态**：
  不会过一会儿又自己变成「进行中」，也不会被服务重启误标成「中断」。
* 作为**工艺经理**，我看到「回传报价失败」时，希望它**一直挂在那里**，并且告诉我：原因是什么、影响哪一步、
  能不能重试、应该回到哪一步，以及一个能交给运维的**错误追踪 ID**。
* 作为**运维/开发**，用户把「错误追踪 ID」发给我时，我希望能用它一次定位到那条服务端日志。
* 作为**财务经理**，我不希望「刷新看板失败」这种无副作用的小问题弹一个大红块挡在我面前；
  但「写主数据失败」必须挡住我，因为那已经改了账。

---

## 4. 当前流程

### 4.1 长任务的生命周期（现状）

```
POST 提交 → queued ──→ running ──┬─→ succeeded / partial        （唯一正常出口）
                                  ├─→ failed                     （任务函数抛异常）
                                  └─→ interrupted                （服务重启，扫尾时判定）
前端 while(true) 轮询：
  一次 fetch 抛错 / 非 2xx  →  直接 throw  →  上层 catch  →  卡片翻「失败」
  页面刷新                  →  内存里的 taskId 丢失  →  卡断了，无人知道还在跑
  用户不想要了              →  没有出口（只能等）
```

### 4.2 关键失败的出口（现状）

```
业务失败 → xxxToast(message, true) → 3 秒后 el.remove()
                                    ↑
                        原因 / 影响 / 下一步 / 追踪 ID 全部不存在
```

---

## 5. 目标流程

### 5.1 任务状态封闭词表与取消（9A）

```
queued ─┬─→ running ─┬─→ succeeded
        │            ├─→ partial
        │            ├─→ failed
        │            ├─→ interrupted          （服务重启扫尾）
        │            └─→ cancelled            （用户主动取消，本批新增）
        └─→ cancelled                        （排队中被取消，本批新增）

终态 = succeeded / partial / failed / interrupted / cancelled
unknown 不是状态，是「前端此刻拿不准」的读取结果：只在 normalize 时出现，永不由服务端写入。
终态一律不可再被改写（幂等：第二次取消返回 already_terminal 且不改任何值）。
```

### 5.2 轮询（9A，前端）

```
poll 第 N 次
  ├─ 2xx + status ∈ 终态        → 结算（succeeded/partial 成功；failed/interrupted/cancelled 明确失败）
  ├─ 2xx + 非终态               → 播进度，继续
  ├─ 404                       → 明确定义：任务不存在（唯一「确定」的 4xx）
  ├─ 401 / 403                 → 明确失败 permission_denied
  ├─ 其他 4xx                  → 明确失败 request-rejected
  ├─ 5xx / 网络抛错 / JSON 解析失败
  │     └─→ 计一次「瞬时故障」，继续轮询（连续成功一次即清零）
  │           连续达到 transientLimit → degraded=true，放慢间隔，卡片显示「连接不稳定，结果仍在处理中」
  │           ＊＊ 绝不因此把任务判成失败 ＊＊
  └─ abort()                   → 停止轮询（不动服务端状态）
```

### 5.3 关键失败固定展示与追踪 ID（9B）

```
后端：每个请求分配 trace_id
   ├─ 响应头 X-Trace-Id（所有响应）
   └─ status >= 400 的 JSON 体额外带 trace_id（与响应头逐字相同）
后端：每个任务记录带 trace_id（提交时生成，GET /tasks/{id} 可见）
前端：拿到失败 → TechFailure.describe() → TechFailure.show() 常驻块
   五要素：原因 / 影响 / 重试 / 返回正确步骤 / 错误追踪 ID
```

### 5.4 安静失败（9C）

```
失败码 ∈ QUIET_FAILURE_CODES            → 静默（或仅一行状态字），不进失败块
失败码 ∈ CRITICAL_FAILURE_CODES         → 必须常驻失败块
没有 code / 不认识的 code                → 默认**不安静**（保守：宁可多打扰，不可漏报）
两个集合必须不相交
```

---

## 6. 状态定义与状态转换

### 6.1 任务状态（服务端）

| 状态 | 含义 | 是否终态 | 谁写入 |
| --- | --- | --- | --- |
| `queued` | 已受理，未开跑 | 否 | `tasks.submit()` |
| `running` | 正在跑 | 否 | `tasks._run()` |
| `succeeded` | 正常完成 | 是 | `tasks._run()` |
| `partial` | 部分完成（逐件容错后仍有成功件） | 是 | `tasks._run()`（任务函数自报） |
| `failed` | 任务函数抛异常 | 是 | `tasks._run()` |
| `interrupted` | 服务重启扫尾 | 是 | `tasks.recover_interrupted_tasks()` |
| `cancelled` | **用户主动取消** | 是 | `tasks.cancel_task()`（本批新增） |

`unknown` 是**读取侧**的归一化结果（`normalize_task_status`），不落库。

### 6.2 转换规则

1. `queued → cancelled`、`running → cancelled` 允许。
2. 任何终态 → 任何状态 **不允许**；`cancel_task()` 对终态任务返回 `already_terminal=true` 且**不写**。
3. `cancel_task()` 只写 `status / progress / finished_at / error`，**不得**改写 `result`、`dedup_key`、`trace_id`。
4. `recover_interrupted_tasks()` 只处理 `queued / running`，`cancelled` 必须原样保留。
5. 「取消」必须留痕：写审计 + 写会话时间线（`key = task:<task_id>`，`status = cancelled`），
   与「中断」用同一套幂等口径（同一 `key` 就地更新，不重复建卡）。
6. **取消之后再收尾不得复活**：任务函数在被取消之后才返回/抛错时，`_run()` 的收尾写入
   必须只在当前状态仍为 `queued` / `running` 时才生效；否则 `cancelled` 会被改回
   `succeeded` / `failed`，用户看到「已取消的任务自己又完成了」。取消后到达的结果一律丢弃，
   但**不**清空此前已写入的 `progress_log` / `process_log`。

---

## 7. 接口与数据契约

### 7.1 后端：`tech_app/backend/services/tasks.py`

```python
TASK_STATUSES = frozenset({"queued", "running", "succeeded", "partial",
                           "failed", "interrupted", "cancelled", "unknown"})
TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed",
                               "interrupted", "cancelled"})

def normalize_task_status(status) -> str          # 词表外 / 空 / None → "unknown"
def is_terminal_status(status) -> bool            # 词表外 → False
def cancel_task(project_id, task_id, actor="", reason="") -> dict
```

`cancel_task()` 返回契约（**字段名逐字钉死**）：

```json
{"ok": true,  "task_id": "…", "status": "cancelled", "already_terminal": false}
{"ok": true,  "task_id": "…", "status": "succeeded", "already_terminal": true}
{"ok": false, "task_id": "…", "status": "",          "already_terminal": false,
 "reason": "not_found"}
```

### 7.2 后端：任务记录新增字段

`submit()` 在**入队时**生成并写入 `trace_id`（`^[0-9a-f]{16}$`，同一次提交复用同一个值）：

```json
{"task_id": "...", "trace_id": "0f1e2d3c4b5a6978", "status": "queued", "dedup_key": "..."}
```

`GET /api/projects/{project_id}/tasks/{task_id}` 必须返回 `trace_id`（历史任务没有该字段时返回空串，
**不报错、不迁移**）。

### 7.3 后端：HTTP 层

* 新增 `POST /api/projects/{project_id}/tasks/{task_id}/cancel`
  → 200 返回 7.1 的 `cancel_task()` 结果；任务不存在时 **404**（`detail = "任务不存在"`）。
  权限沿用项目写权限（批次 7 的 `require_project_access(project_id, user, "write")`）。
* **所有**响应带 `X-Trace-Id`，值匹配 `^[0-9a-f]{16}$`。
* 所有 `status >= 400` 的 JSON 响应体额外带顶层 `trace_id`，与响应头逐字相同。
  （CORS 预检与静态资源同样带头；静态资源不带 body 的 `trace_id`。）

### 7.4 前端：`tech_app/frontend/tech-task-watch.js` → `window.TechTaskWatch`

```js
TechTaskWatch.STATUSES        // ['queued','running','succeeded','partial','failed','interrupted','cancelled','unknown']
TechTaskWatch.TERMINAL       // 终态数组
TechTaskWatch.normalizeStatus(s)      // 词表外/空/undefined → 'unknown'
TechTaskWatch.isTerminal(s)           // 词表外 → false
TechTaskWatch.statusWord(s)           // succeeded→「已完成」 partial→「部分完成」 failed→「失败」
                                      // interrupted→「中断」 cancelled→「已取消」 unknown→「状态未知」
TechTaskWatch.recover(projectId, taskId, fetchImpl)   // GET 单任务；404 → null（供刷新后复原）
TechTaskWatch.list(projectId, fetchImpl)              // GET 任务列表 → 数组（任务中心事实源）
TechTaskWatch.watch(options) -> {promise, abort, state}
```

`watch(options)`：`{projectId, taskId, fetchImpl, intervalMs=1200, degradedIntervalMs=5000,
transientLimit=3, onProgress, onDegraded, signal}`。

* `state()` → `{status, transient_failures, degraded, record}`；`status` 为归一化结果，
  首轮成功前为 `unknown`。
* `succeeded` / `partial` → `promise` **resolve** `{ok:true, status, record}`。
* `failed` → **reject** `{ok:false, status:'failed', code:'task-failed', message, trace_id}`。
* `interrupted` → reject `{ok:false, status:'interrupted', code:'interrupted', message, trace_id}`。
* `cancelled` → reject `{ok:false, status:'cancelled', code:'cancelled', message, trace_id}`。
* 瞬时故障（fetch 抛错 / 5xx / JSON 解析失败）→ **计数**，`transient_failures` 递增，
  连续成功一次清零；达到 `transientLimit` 时 `degraded = true` 并回调 `onDegraded`
  ——**promise 既不 resolve 也不 reject**，继续按 `degradedIntervalMs` 轮询。
* `404 → reject {code:'task-not-found'}`；`401/403 → reject {code:'permission_denied'}`；
  其他 4xx → `reject {code:'request-rejected', status}`。
* `abort()` → `reject {code:'aborted'}`，**不发任何写请求**（取消要显式调 cancel 接口）。

### 7.5 前端：`tech_app/frontend/tech-failure-banner.js` → `window.TechFailure`

```js
TechFailure.CRITICAL_FAILURE_CODES  // 必须固定展示
TechFailure.QUIET_FAILURE_CODES     // 允许静默
TechFailure.isQuiet(error)          // 只认 QUIET 成员 或 error.quiet === true；无码 → false
TechFailure.describe(failure)       // → {code, title, reason, impact, actions, trace_id, quiet}
TechFailure.show(failure)           // → 常驻 DOM 节点（不自动消失）
TechFailure.dismiss(node)           // 显式关闭
```

* `CRITICAL_FAILURE_CODES` 至少含
  `permission_denied` / `handoff_failed` / `db_write_failed` / `task-failed` / `interrupted` / `result_stale`。
* `QUIET_FAILURE_CODES` 只含刷新/选择类副作用码（成员一律以 `refresh-` 开头，或属于
  `detached` / `no-selection` / `note-target-missing` / `missing-comment`）。
* 两集合**不相交**（`isQuiet({code})` 对关键码必须为 `false`）。
* `describe()` 的 `actions` 至少含一个 `id === 'retry'` 与一个 `id === 'goto-step'`；
  `goto-step` 的 `label` 必须写出目标步骤名（无 `stage` 时给「返回正确步骤」）。
* `show()` 渲染出的文本必须同时出现五个标签：`原因`、`影响`、`重试`、`返回正确步骤`、`错误追踪 ID`。
* `show()` 的节点必须带 `data-trace-id`（等于 `describe().trace_id`）。
* 常驻：`show()` 之后再推进 60 秒，节点仍挂在 `document.body` 上（不会被任何定时器摘掉）。
* 关键码走 `show()` 时有节点；安静码**不得**产出节点。

---

## 8. 正常路径

1. 提交长任务 → `queued`（带 `trace_id`）→ 前端 `watch()` 轮询 → 进度按既有 `process_log` 播到卡上
   → `succeeded` → 卡翻「已完成」，`trace_id` 留档可查。
2. 用户在排队中点「取消」→ `POST …/cancel` → 200 `{status:'cancelled', already_terminal:false}`
   → 前端卡翻「已取消」→ 刷新后仍在任务中心、仍是「已取消」。
3. 任务在跑到一半时用户取消 → 同 2，且任务函数已写入的部分结果**不被清除**。
4. 回传报价失败 → 常驻失败块，含五要素；用户点「重试」→ 回调发起重试；点「返回正确步骤」
   → 跳到失败发生在的那一步。

## 9. 异常路径

1. **网络抖动**：连续 2 次 fetch 失败后第 3 次成功 → `transient_failures` 归零、`degraded` 保持 false、
   任务正常跑完（**不得**判失败）。
2. **长时间不可达**：连续失败 ≥ `transientLimit` → `degraded = true`，界面写「连接不稳定，结果仍在处理中」，
   promise 悬挂；用户刷新后 `list()` 里仍能看到该任务。
3. **任务真的失败**：`status = failed` → 明确 reject，前端出常驻失败块（不是 toast）。
4. **任务不存在**（被清理/项目不对）：404 → `task-not-found`；前端不出失败块，只写「任务已不在」。
5. **无权限**：401/403 → `permission_denied` → **常驻失败块**（权限不足不允许用任何跳过方式绕过）。
6. **服务重启**：`running → interrupted`（既有），前端照既有蓝色「中断」展示，并可作为常驻失败块的
   一种（原因 / 影响 / 重试 / 返回正确步骤 / 追踪 ID 齐全）。

## 10. 并发与幂等

* 两个浏览器标签同时对同一任务点「取消」：**只允许一个**执行转换，另一个返回 `already_terminal = true`；
  最终状态唯一为 `cancelled`，`finished_at` 只写一次。
* 取消与「服务重启扫尾」并发：`recover_interrupted_tasks()` 不得把 `cancelled` 改成 `interrupted`。
* 重复 `POST …/cancel` 任意次：结果恒等（除 `already_terminal` 由 false 变 true），**不得**产生第二条
  会话时间线事件（同一 `key` 就地更新）。
* `watch()` 的 `abort()` 可重复调用；第二次仍返回同一个 `aborted` 结果，不抛异常。

## 11. 刷新 / 重试 / 重复点击 / 服务重启

| 场景 | 期望 |
| --- | --- |
| 页面刷新 | `list()` 能列出该任务；`recover()` 能按 `task_id` 取回记录并复原卡片 |
| 轮询超时 | 任务**不算失败**，卡保持原状态并标 `degraded` |
| 重复点「重试」 | 沿用既有 `dedup_key` 去重（回归护栏，不新增机制） |
| 服务重启 | `queued/running → interrupted`；`cancelled` 与其余终态原样保留 |
| 取消后刷新 | 仍显示「已取消」，不会退回「进行中」 |
| 取消后任务函数才收尾 | 状态仍是 `cancelled`（收尾写入被终态挡住，不复活） |

## 12. 权限边界

* `POST …/cancel` 需要项目写权限；只读账号 → 403 且错误体带 `trace_id`。
* 失败块只展示**当前用户有权看到**的文案；`trace_id` 是运维凭据，不含密钥、绝对路径、base64。
* 「安静失败」不得被用来掩盖权限类失败：`permission_denied` 必须常驻展示。

## 13. 历史数据兼容

* 历史任务记录**没有** `trace_id`：`GET /tasks/{id}` 返回空串，前端失败块写「无」而不是报错。
* 历史任务出现词表外状态（如早期写法）：`normalize_task_status()` 归一成 `unknown`，
  前端显式显示「状态未知」；既有「未知兜底成进行中」的行为**不反转**（见 §2）。
* 不迁移、不清洗、不删除任何既有任务与会话记录。

## 14. 可自动化验收标准

1. `tasks.TASK_STATUSES` 含 8 个词（含 `cancelled` / `unknown`）；`TERMINAL_STATUSES` 含 5 个终态。
2. `normalize_task_status()` 对 `None` / `""` / `"nonsense"` 返回 `"unknown"`；`is_terminal_status("cancelled")` 为真。
3. `cancel_task()` 能把 `queued`/`running` 任务置 `cancelled`，写 `finished_at`，写会话时间线。
4. 同任务二次 `cancel_task()` → `already_terminal=true`，`status` 不变，会话时间线不新增条目。
5. 两个线程并发 `cancel_task()` → 恰好一个 `already_terminal=false`。
6. `recover_interrupted_tasks()` 不碰 `cancelled`。
6b. 任务被取消后才返回（或抛错）的收尾路径**不得**把 `cancelled` 改写成 `succeeded` / `failed`。
7. `submit()` 写的任务记录含 `trace_id`，匹配 `^[0-9a-f]{16}$`，两次提交不同。
8. `GET /api/projects/{pid}/tasks/{tid}` 返回 `trace_id`。
9. 路由 `POST /api/projects/{pid}/tasks/{tid}/cancel` 存在；取消后 200 且状态为 `cancelled`；
   未知任务 404。
10. 任意响应带 `X-Trace-Id`；≥400 的 JSON 体 `trace_id` 与响应头逐字相同。
11. `TechTaskWatch` 存在且 `normalizeStatus/isTerminal/statusWord` 语义与 §7.4 一致。
12. `watch()`：连续 2 次瞬时失败后成功 → resolve、`transient_failures` 归零、`degraded=false`。
13. `watch()`：连续失败 ≥ `transientLimit` → `degraded=true`、**不 reject**、`state().status` 不变。
14. `watch()`：`failed` / `interrupted` / `cancelled` 各自 reject 出正确的 `code`。
15. `watch()`：404 → `task-not-found`；403 → `permission_denied`。
16. `watch().abort()` 幂等，且不产生任何写请求。
17. `recover()` 404 返回 `null`；`list()` 返回数组（超时中的任务仍在其中）。
18. `TechFailure` 两集合不相交；关键码 `isQuiet` 为 false；无码 `isQuiet` 为 false。
19. `describe()` 的 `actions` 含 `retry` 与 `goto-step`。
20. `show()` 节点文本含五要素标签；带 `data-trace-id`；推进 60 秒后仍在 `document.body`。
21. 安静码 `show()` 不产出节点。

## 15. 人工验收场景

1. 断网 5 秒再恢复：卡上出现「连接不稳定，结果仍在处理中」，恢复后继续推进并最终完成。
2. 刷新浏览器：任务中心里任务仍在；点开可继续看进度。
3. 排队中点「取消」：立即变「已取消」；刷新后仍是「已取消」；不会被服务重启改成「中断」。
4. 制造一次回传失败：失败块常驻且含五要素；点「重试」与「返回正确步骤」都有效；把追踪 ID 报给运维可用。
5. 只读账号点「取消」：403 + 常驻失败块（不是 toast）。

## 16. 不允许减少的既有能力

* 既有 toast 仍可用于成功提示与安静失败（不得整体删除）。
* `taskStatusWord()` 对未知状态的「进行中」兜底不得删除或反转。
* 既有 `dedup_key` 去重、`interrupted` 判定与蓝色 chip、「仅重试失败项」、过程事件流、
  一次性任务卡（不重复建卡）全部保持。
* 既有 7 个任务相关红测文件必须继续全绿（143 条）。
