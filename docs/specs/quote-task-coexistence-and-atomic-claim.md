# 报价任务并存规则、原子领取与多人并发保护（批次 2）

- 状态：Spec（待实现）
- 范围：`cpq_wf.py`（任务流转）、`cpq_suite_server.py`（`/wf/task*` 路由）、`报价首页.html`
  与 `tech_app/frontend/cpq-tech-inbox.js`（任务卡片渲染）、`cpq_msg.js`（消息图标）
- 依赖：无。**不依赖批次 1**（项目身份），也不修改批次 1 的文件；批次 3（跨系统回传事务）
  必须等本批实现并验收后再开始。

## 1. 背景与真实问题

报价卡片的任务流转是「谁把这张卡片交给谁、谁来领」的唯一通道。今天的实现有三个确定性缺陷，
且都已在线上数据里留下痕迹。

### 1.1 发起任何一条支线任务，会把卡片上其他所有待领取任务一起取消

`cpq_wf.py:819-822`：

```python
# 同一张卡片同时只保留一个待领取任务，避免重复派发
cpq_auth._exec(
    conn, "UPDATE cpq_wf_task SET status = 'cancelled' WHERE card_id = %s AND status = 'open'",
    (cid,))
```

这条 `UPDATE` **没有 `task_kind` 条件**：工艺经理把卡片「转交工艺确认」（`handoff`）之后，
再去 2.2 点「发送至财务」（`tech_cost`），第一条 `handoff` 会被**静默**取消。收件人侧只是
任务从待办里消失，没有任何解释。

线上实测（只读，未改任何数据）：

- `cpq_wf_task` 共 220 行，其中 `status='cancelled'` **45 行**；
- 这 45 行的 `cpq_wf_task_event` 里 `action IN ('cancel','supersede')` 的记录数 = **0**，
  即**每一次取消都没有审计**；
- 任务类型分布：`handoff` 170、`tech_new_product` 27、`tech_cost` 23 —— 三类任务在同一张
  卡片上本来就是并存的（报价在等新产品 / 等成本的同时，卡片本身可能还在等工艺确认）。

### 1.2 领取不是原子的

`cpq_wf.py:1002-1030`：先 `SELECT card_id, status, ... FROM cpq_wf_task WHERE task_id = %s`，
判断 `status == 'claimed'` 抛错，再 `UPDATE cpq_wf_task SET status = 'claimed', ... WHERE task_id = %s`。

- 那条 `UPDATE` 的 `WHERE` **只有 `task_id`**，没有 `AND status = 'open'`；
- 也没有检查受影响行数 / `RETURNING`。

公共任务池（`target_type='public'`）是给「谁有空谁领」用的。两个账号同时点「领取」：

1. A 与 B 的 `SELECT` 都读到 `status='open'`；
2. A 执行无条件的 `UPDATE` → 成功；
3. B 的 `UPDATE` 同样匹配到这一行 → 成功，把 `claimed_by_user_id` 覆盖成 B；
4. A、B 都收到「领取成功」，卡片 `current_owner` 变成 B，A 已经进入工作台开始干活，
   但他的任务已经被划走了。

线上公共任务池这一批仍在用（`/wf/task/send` 的 `target_type` 支持 `public`），且 App 端
领取是「点卡片即领取」，重复点击和多端同时打开都很常见。

### 1.3 被取消 / 被替代的任务在界面上没有出口

`报价首页.html:1621-1653`（`taskCardHtml`）与 `tech_app/frontend/cpq-tech-inbox.js:108-138`
（`taskCard`）只认两种状态：`claimed` → 「进行中」，其余 → 「待领取」。`cpq_wf.MSG_TYPES`
（`cpq_wf.py:690-694`）与 `cpq_msg.js:42-46` 也只有 `task_sent` / `task_received` /
`task_claimed` 三种消息。

所以任务被替代 / 被撤回之后：发起人看不到「我那条被新任务替代了」，收件人看不到「你要做的
这件事已经作废」，两边都只能靠口头确认。

## 2. 用户角色与用户故事

| 角色 | 故事 |
|------|------|
| 销售经理 | 我把卡片转交工艺确认后，还要把工艺与参数发给财务测算成本 —— 这两件事必须同时存在，前面的转交不能被后面这一步悄悄取消。 |
| 工艺经理 | 我点两次「转交」不该产生两条一模一样的待办；我改成转交别人时，原来那位应该收到「已被新任务替代」而不是任务凭空消失。 |
| 财务经理 | 我正准备领取公共任务池里的成本测算，别人先领走了，我必须收到明确的「已被他人领取」，而不是进去之后发现卡片不是我的。 |
| 任何人 | 我不该在自己还没开始做的时候，任务就被另一个人的领取动作覆盖掉。 |
| 发起人 | 我要能在自己的任务列表里看到「我这条已被 TP-xxxx 替代」，而不是只能看到新任务。 |

## 3. 当前流程

```
销售：完成第 1 步 → POST /wf/task/send {task_kind: handoff}
        → send_task：取消该卡片所有 open 任务（不分类型）→ 插入新任务 → 消息 task_sent/task_received
工艺：2.2 完成 → POST /wf/task/send {task_kind: tech_cost}
        → send_task：又取消该卡片所有 open 任务（含刚发出去的 handoff）→ 插入新任务
        ⇒ 第一位工艺经理的待办凭空消失，且无审计、无消息
财务：待办列表点卡片 → POST /wf/task/claim
        → SELECT 判状态 → 无 status 条件的 UPDATE → 卡片 current_owner 改为领取人
        ⇒ 并发点两次，两人都成功，后到者覆盖先到者
```

## 4. 目标流程

```
销售：完成第 1 步 → 转交 handoff（目标 = 工艺经理）
                    ├─ 该卡片没有同类 open 任务 → 新建（不变）
                    ├─ 已有同类 open 且签名完全一致 → 复用（不新建、不重复通知）
                    └─ 已有同类 open 但签名不同（换人或换了备注）→ 替代：
                         旧任务 status=cancelled、replaced_by_task_id=新任务、cancel_reason
                         审计 action='cancel'、原收件人 + 原发起人各收 task_superseded
                         新任务写入 supersedes_task_id=旧任务
                    ⇒ 其他类型（tech_new_product / tech_cost / tech_cost_return）一律不受影响
工艺：发送至财务 tech_cost（与 handoff 并存，占用的是 tech_cost 这一格）
财务：领取 → 一条带 status='open' 条件的原子 UPDATE
        ├─ 抢到 → 改卡片 owner（仅主线任务）、审计 'claim'、通知转交人
        ├─ 没抢到且领取人就是自己 → 幂等成功（不复写、不重复审计、不重复通知）
        └─ 没抢到且是别人 → 「该任务已被他人领取」，卡片 owner 保持不动、无任何副作用
```

## 5. 状态定义及状态转换

任务状态（`cpq_wf_task.status`，取值不变，只补语义与审计）：

| 状态 | 中文 | 含义 | 是否终态 |
|------|------|------|----------|
| `open` | 待领取 | 已派发、没人领取 | 否 |
| `claimed` | 进行中 | 已被某人领取 | 否 |
| `completed` | 已完成 | 领取人做完并交回 | 是 |
| `cancelled` | 已撤回 / 被新任务替代 | 未领取就被新的同类任务替代 | 是 |

允许的转换：

```
open  → claimed     （领取，必须原子）
open  → cancelled   （被同类新任务替代）
claimed → completed （complete_claimed_task，既有）
```

不允许：`claimed → cancelled`（本批不支持撤回别人正在做的任务）、任何终态回到 `open`/`claimed`、
`cancelled → *`。

并存矩阵（同一张卡片上，`(card_id, task_kind)` 这一格最多一条 `open` 任务；**不同 `task_kind` 之间永远并存**）：

| 已有 ↓ / 新发起 → | handoff | tech_new_product | tech_cost | tech_cost_return |
|---|---|---|---|---|
| **handoff**（open） | 同签名→复用；否则替代 | 并存 | 并存 | 并存 |
| **handoff**（claimed） | 同签名→复用；否则**拒绝** | 并存 | 并存 | 并存 |
| **tech_new_product**（open） | 并存 | 同签名→复用；否则替代 | 并存 | 并存 |
| **tech_cost**（open） | 并存 | 并存 | 同签名→复用；否则替代 | 并存 |
| **tech_cost_return**（open） | 并存 | 并存 | 并存 | 同签名→复用；否则替代 |
| 任一类型（claimed） | 并存 | 并存 | 并存 | 并存（同类型同签名→复用；其他→拒绝） |

「签名」（`dispatch_signature`）= 以下五项逐项相等：

1. `target_type`；2. `target_role_code`（`target_type != 'role'` 时为 NULL）；3. `target_user_id`；
4. `note.strip()`；5. `business_version`。

`business_version` = `payload.get('result_version') or payload.get('version') or
payload.get('handoff_key') or ''`（调用方没给就是空串，等价于「同版本」）。

同一张卡片上并存的例子（本批必须成立）：`handoff`(open) + `tech_cost`(open) +
`tech_new_product`(open) + `tech_cost_return`(open) 四条同时存在，互不影响。

## 6. 接口与数据契约

### 6.1 数据库（`_ddl_pg`，全部幂等）

```sql
ALTER TABLE <schema>.cpq_wf_task ADD COLUMN IF NOT EXISTS
    supersedes_task_id bigint REFERENCES <schema>.cpq_wf_task(task_id) ON DELETE SET NULL;
ALTER TABLE <schema>.cpq_wf_task ADD COLUMN IF NOT EXISTS
    replaced_by_task_id bigint REFERENCES <schema>.cpq_wf_task(task_id) ON DELETE SET NULL;
ALTER TABLE <schema>.cpq_wf_task ADD COLUMN IF NOT EXISTS cancel_reason varchar(200);
ALTER TABLE <schema>.cpq_wf_task ADD COLUMN IF NOT EXISTS cancelled_at timestamptz;
-- 「同一卡片同一类型最多一条 open」的硬保证：并发下由数据库裁决，不靠应用层 SELECT
CREATE UNIQUE INDEX IF NOT EXISTS uq_wf_task_open_kind
    ON <schema>.cpq_wf_task(card_id, task_kind) WHERE status = 'open';
```

字段语义：

| 列 | 语义 |
|----|------|
| `supersedes_task_id` | 只有「替代」出来的新任务写：指向被它替代的旧任务 |
| `replaced_by_task_id` | 只有被替代的旧任务写：指向替代它的新任务 |
| `cancel_reason` | 被替代的原因；本批固定为 `被新任务替代` |
| `cancelled_at` | 取消时间 |

### 6.2 `cpq_wf.send_task(...)` 返回值新增

| 键 | 类型 | 说明 |
|----|------|------|
| `reused` | bool | `True` = 命中已有同签名任务，没有新建；`False` = 新建或替代 |
| `supersedes_task_id` | str/None | 替代成功时是被替代任务 id，否则 `None` |
| `task_id` / `task_no` / `task_kind` / `source_label` | 不变 | 复用与替代两种情况都必须回**有效**的新/既有任务 id |

复用分支不得写 `cpq_wf_task`、不得写 `cpq_wf_task_event`、不得写 `cpq_wf_message`（它没有
改变任何状态）。

### 6.3 `cpq_wf.claim_task(...)` 返回值新增

| 键 | 类型 | 说明 |
|----|------|------|
| `already` | bool | `True` = 该任务已由**本人**领取，本次是重复请求；不产生任何副作用 |
| `session_id` / `task_kind` / `task_no` | 不变 | 复用分支同样要回，前端要靠它跳转 |

### 6.4 行字段（`_TASK_SELECT` / `_TASK_KEYS` / `_task_row`）

`task_detail`、`inbox`、`card_detail.pending_task` 返回的每一行新增：

| 键 | 说明 |
|----|------|
| `status_label` | 中文状态：`待领取` / `进行中` / `已完成` / `已撤回`（被替代的行显示 `已撤回`，`replaced_by_task_no` 另行给出） |
| `supersedes_task_id` | 见 6.1 |
| `replaced_by_task_id` | 见 6.1 |
| `replaced_by_task_no` | 替代它的新任务的展示编码（`TP-xxxxxxxx`），无则 `None` |
| `cancel_reason` | 见 6.1 |
| `cancelled_at` | 见 6.1（ISO 字符串，无则 `None`） |

### 6.5 消息类型（`MSG_TYPES` + `cpq_msg.js` 的 `ICON`）

| msg_type | 文案 | 何时发 |
|----------|------|--------|
| `task_superseded` | 被新任务替代 | 旧任务被同类新任务替代时，发给旧任务原收件人集合 + 旧任务发起人（去重） |
| `task_cancelled` | 已撤回 | 未带替代任务的取消（本批没有入口触发，但字典与图标必须齐备） |

消息体必须带：旧任务编码 `TP-…`、新任务编码 `TP-…`（替代时）、替代原因。

### 6.6 `inbox(user)`（`GET /wf/tasks`）

在原「待领取（定向我 / 定向我的角色 / 公共，且不是我发的）+ 我已领取未完成」之上**增加一支**：

```
OR (t.status = 'cancelled' AND t.from_user_id = <我> AND t.replaced_by_task_id IS NOT NULL)
```

即：**我自己发起的、被新任务替代掉的那一条**，留在我的列表里给一个终态出口。
被历史实现的静默取消（`replaced_by_task_id IS NULL`）**不回填、不进列表**，避免把线上 45 条
老记录一次性倒进销售经理的待办里。

## 7. 正常路径

1. 销售转交 `handoff` → 新任务 `open`、卡片 `overall_status='handoff_pending'`、审计 `send`、
   自己与收件人各一条消息（现状不变）。
2. 工艺经理 2.2 发送至财务 `tech_cost` → 只影响 `tech_cost` 这一格：`handoff` 仍 `open`，
   卡片状态与 owner 不变，消息照旧。
3. 财务经理领取 → 原子 UPDATE 命中 → 卡片 `current_owner` 改为财务（`tech_cost` 是支线，
   **不改** `current_owner`；只有主线 `handoff` 才改）→ 审计 `claim` → 转交人收到 `task_claimed`。
4. 销售重复点「转交」，目标与备注都没变 → 第二次返回 `reused=True`，任务数不变、消息数不变。
5. 销售改成转交给另一个人 → 旧任务 `cancelled` + `replaced_by_task_id` 指向新任务 + 审计
   `cancel` + 原收件人收到 `task_superseded`；新任务 `supersedes_task_id` 指向旧任务。

## 8. 异常路径

| 场景 | 期望 |
|------|------|
| 领取已被**他人**领取的任务 | `WfError("该任务已被他人领取")`；卡片 `current_owner` 不变；无审计、无消息 |
| 领取已被取消 / 已完成的任务 | `WfError("该任务已关闭")`；无副作用 |
| 领取不存在的任务 | `WfError("任务不存在")` |
| 没有领取资格（角色/指派都不匹配） | `WfError("你没有该任务的领取权限")`；**在原子更新之前**判定，无副作用 |
| 同类任务已被他人 `claimed`，又发起不同签名的新任务 | `WfError`，文案必须含领取人显示名，例如「该卡片的任务已被 张三 领取，请等他完成后再重新发起」 |
| 并发重复发起同签名任务（唯一索引冲突） | 必须捕获冲突并回落到「复用」语义返回已有任务，**不得**把唯一冲突当 500 抛给用户 |
| 未登录 | `WfError("请先登录")`（不变） |

## 9. 并发与幂等要求

1. 领取必须是**单条**带 `status = 'open'` 条件的 `UPDATE`，并且**必须**检查受影响行数
   （`rowcount`）或使用 `RETURNING`，不得依赖先前 `SELECT` 的结果决定成败。
   禁止用进程内锁 / 全局字典做唯一性保证：8010 是一体化服务，但状态唯一来源必须是 PG。
2. 两个账号同时领取同一条公共任务：**恰好一个**成功；失败方零副作用（不改卡片 owner、
   不写审计、不发消息）。
3. 同一个人重复领取（双击、多端）：第二次返回 `already=True`，审计仍只有 1 条 `claim`，
   消息仍只有 1 条 `task_claimed`（`from_user_id` 与领取人相同时本来就不发，也必须保持）。
4. 同一签名重复发起：只产生 1 条任务、1 组消息。
5. 「同一 `（card_id, task_kind）` 最多一条 `open`」由 partial unique index 兜底，
   并发下应用层即使判断错也必须被数据库挡住并优雅收敛。

## 10. 刷新、重试、重复点击、服务重启

| 动作 | 期望 |
|------|------|
| 刷新页面 | 列表完全来自 `/wf/tasks`；`cancelled` 行仍显示为终态，不会变成可领取 |
| 重复点击「转交」 | 第二次 `reused=True`，界面提示同一任务编码，不产生第二条 |
| 重复点击「领取」 | 第二次 `already=True`，照常跳转工作台，不报错 |
| 领取失败后重试 | 仍是「已被他人领取」；任务完成后再重试 → 「该任务已关闭」 |
| 服务重启 | 全部状态在 PG（autocommit），重启后列表 / 领取 / 替代行为一致；不允许出现仅存在于内存的锁或队列 |
| 重放同一次替代 | 第二次替代请求命中同签名 → `reused=True`，不再产生第三条任务 |

## 11. 权限边界

- 领取资格与 `inbox` 可见性保持完全一致（`public` / 角色匹配 / 指派到我），不新增也不放宽。
- 替代别人任务的权限 = 该业务入口自身的角色校验（例如 `send_to_finance` 仅 `process_mgr`），
  本批**不新增**任何「谁都能撤回任意任务」的能力。
- 结算/审批/发布的既有硬门禁不受本批影响；本批不碰「仍要继续 / 豁免」。
- `/wf/task*` 路由的登录校验（未登录 401）与工作流未就绪 503 保持不变。

## 12. 历史数据兼容

- 4 个新列全部 nullable + `ADD COLUMN IF NOT EXISTS`，老行读出 `None`，`_task_row` 不得因
  为 `None` 抛错。
- 线上 45 条历史 `cancelled` 任务：**不回填** `cancel_reason` / `cancelled_at`，不补审计，
  也不进任何人的列表。
- `CREATE UNIQUE INDEX` 在线上实测可安全建立：当前 `(card_id, task_kind)` 维度的 open 重复数
  为 **0**（23 条 open）。实现仍必须先建列再建索引，且索引语句必须幂等。
- 既有调用方（`cpq_tech_bridge._side_task`、`确认需求解析结果.html`）只读 `task_id` /
  `task_no` / `source_label`，新增返回值与字段是纯增量，不改调用契约。
- `msg_type` 允许旧值缺失（`MSG_TYPES.get` 有兜底），老消息不受影响。

## 13. 非目标（本批不做）

- 不做跨系统回传事务（技术工艺 → 报价的交接）——批次 3。
- 不新增「撤回任务」按钮 / 接口；只定义 `cancelled` 的语义与展示（替代是主要的取消来源）。
- 不改动 `task_kind` 取值集合、不改「转交推进步骤」的既有语义、不改卡片 6 步状态机。
- 不改权限模型、不改 Token / 鉴权、不改门禁分级（L0–L4）、不改视觉风格。
- 不清理、不迁移、不回填任何历史任务、消息、项目与会话数据。

## 14. 可自动化验收标准

红测文件 `tests/test_quote_task_coexistence_and_atomic_claim_red.py`，全部为**行为**断言
（受控假库 + 真调 `cpq_wf.send_task` / `claim_task` / `inbox`，不是文本搜索）：

| # | 验收点 | 断言 |
|---|--------|------|
| A1 | 同类不相残 | 同卡片依次发 `handoff`、`tech_cost`、`tech_new_product`、`tech_cost_return` → 4 条全 `open`，没有任何一条被取消 |
| A2 | 支线不取消主线 | 先 `handoff` 后 `tech_cost`：旧 handoff 仍 `open`、无 `replaced_by_task_id`、卡片状态不变 |
| A3 | 同签名复用 | 同签名连发两次 → 任务总数 +1、第二次 `reused=True`、消息不增加、无新增审计 |
| A4 | 换目标=替代 | 同类型换目标连发两次 → 旧任务 `cancelled` + `replaced_by_task_id`=新任务 + `cancel_reason` 非空；新任务 `supersedes_task_id`=旧任务 |
| A5 | 替代有通知有审计 | 旧任务有且仅有 1 条 `action='cancel'` 审计；原收件人与原发起人各收到 1 条 `task_superseded` |
| A6 | 并发领取只有一个赢 | 两个账号（各自连接）在受控交错下同时领取同一公共任务 → 恰好 1 个成功，失败方抛 `WfError` |
| A7 | 输家无副作用 | 失败方不得改变卡片 `current_owner`，不得写 `claim` 审计，不得发 `task_claimed` 消息 |
| A8 | 幂等重领 | 同一人连续领取两次 → 第二次 `already=True`，审计 `claim` 仅 1 条 |
| A9 | 支线不改归属 | 领取 `tech_cost` 后卡片 `current_owner` 不变；领取 `handoff` 后 `current_owner` = 领取人 |
| A10 | 终态不可复开 | `cancelled` / `completed` 任务领取一律 `WfError`，且状态不变 |
| A11 | 列表出口 | `inbox()` 里发起人看得到被替代的那条（`cancelled` + `replaced_by_task_no`），且它不计入 `open`；历史静默取消的行不出现 |
| A12 | 并发重复发起收敛 | 两线程同签名同时发起 → 只有 1 条 open，另一个走复用语义而不是抛唯一冲突 |
| C1 | SQL 契约 | `claim_task` 的 `UPDATE` 必须带 `status = 'open'` 且消费 `rowcount`/`RETURNING`；`send_task` 不得再有「取消该卡片全部 open」的无条件 UPDATE |
| C2 | DDL 契约 | 4 个新列 + `uq_wf_task_open_kind` partial unique index 必须在 `_ddl_pg` 中幂等声明 |
| C3 | 展示契约 | `MSG_TYPES` 与 `cpq_msg.js` `ICON` 含两种新消息；两个任务卡片渲染器都能渲染终态（`已撤回` / 被替代任务编码）且不显示领取 CTA |
| C4 | 计数契约 | 两个前端的待办计数只统计还能做的任务，**不得把 `cancelled` / `completed` 的终态行算进去**（终态行会留在列表里，计数必须排除它们） |

## 15. 人工验收场景

1. 两个浏览器（或两台机器）分别登录两个账号，A 在公共任务池发一条任务，A/B 同时点领取：
   只有一个进入工作台，另一个看到「该任务已被他人领取」，卡片归属与领取人一致。
2. 销售完成第 1 步 → 转交工艺经理 → 再点一次「转交」（不改任何内容）：待办仍只有 1 条。
3. 销售改为转交给另一个人：原收件人的待办里那条消失，同时消息里出现「被 TP-xxxx 替代」；
   销售自己的任务列表里能看见那条被替代的记录。
4. 工艺经理 2.2 发送至财务：先前的「工艺确认」待办仍在，工艺经理本人的待办不消失。
5. 重启 8010 后重复步骤 1–4，行为一致（无内存态残留）。

## 16. 不允许减少的既有能力

- `handoff` 仍然推进卡片到「已转交·待领取」并通知收件人；`tech_new_product` /
  `tech_cost` / `tech_cost_return` 仍然不推进步骤、不夺卡片持有人。
- 定向角色 / 指派个人 / 公共任务池三种派发方式都能用；支线任务的默认收件角色规则不变。
- `task_detail` 的可见性与 `claim_task` 的领取资格判定口径不变。
- `task_no`（`TP-` 短码）、`source_label`、`payload`、`next_step_name` 等既有字段不变。
- `/wf/cards`、`/wf/card`、`/wf/messages`、`/wf/card/step*` 的行为与返回结构不变。
- `MSG_TYPES` 既有三种消息的类型码与文案不变。
