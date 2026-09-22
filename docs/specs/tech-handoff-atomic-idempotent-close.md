# 技术工艺 / 成本 / 报告回传报价的原子闭环与幂等（批次 3）

- 状态：Spec + 红测（已实现）
- 红测：`tests/test_tech_handoff_atomic_idempotent_red.py`
- 范围：`cpq_tech_bridge.py`（回传业务命令）、`cpq_wf.py`（任务与步骤写入，改为支持外部事务连接）、
  `cpq_suite_server.py`（`/wf/tech/*` 路由与请求体）、`tech_app/backend/services/cpq_bridge.py`
  （回调客户端）、`tech_app/backend/services/cost_flow.py`、`tech_app/backend/services/report_workflow.py`
  （技术侧只在明确成功后留痕）、`tech_app/frontend/cost-review.js`、`tech_app/frontend/report-publish-result.js`
  （回传结果展示 handoff 编号与来源任务状态）
- 依赖：**必须在批次 2（`docs/specs/quote-task-coexistence-and-atomic-claim.md`）实现并验收之后再做**。
  本批要改 `cpq_wf.send_task` / `claim_task` 与 `cpq_wf_task` 的写入路径，批次 2 也要改同一批函数；
  先做批次 2、再做本批，避免两边同时改一个函数。
- 本批**不**改：步骤角色权限（`cpq_wf.can_do_step`）、`/wf/tech/material`（写主数据）、
  技术工艺内部 1.x/2.x 的页面流转、报价首页的任务卡片视觉。

## 1. 背景与真实问题

技术侧把结果交回报价，今天是一次业务动作被拆成**多条互不相干的写**与**两次 HTTP 调用**，
任何一步失败都会留下"一半成功"的记录。

### 1.1 一次回传跨了 5 个连接、至少 5 次提交（没有事务）

`cpq_tech_bridge.send_to_quote`（`cpq_tech_bridge.py:531-687`）依次做：

1. `cpq_auth._connect()` 查来源任务，`finally conn.close()`；
2. 认不回原报价时 `ensure_quote_session()` → `cpq_wf.sync_card()`（**第二条连接 + 自己 commit**）
   → `_seed_quote_history()`（**写磁盘历史文件**）；
3. `cpq_auth._connect()` 查幂等任务 + `_commit_card_steps()`（**第三条连接**）；
4. `cpq_wf.complete_step()`（**第四条连接**，内部再 commit）或 `cpq_wf.merge_step_snapshot()`；
5. `_dispatch_handoff_task()` → `cpq_wf.send_task()`（**第五条连接**）或一条裸 UPDATE。

而 `cpq_auth._connect()` 是 `autocommit=True`、`cpq_auth._commit()` / `cpq_wf._commit()` 都是空函数
（`cpq_auth.py:95-137`、`cpq_wf.py:234-236`）。psycopg3 在 autocommit 连接上**不会**为
`with conn.transaction():` 发 `BEGIN`（`_connection_base.py:537-545`：`if self._autocommit: return`），
所以"多写一步"在这里就等于"多提交一次"，事后无法整体回滚。

### 1.2 关闭来源任务是一次独立请求，失败被吞掉

`tech_app/backend/services/cost_flow.py:308-329`（`close_source_task`）在回传**已经成功之后**，
再发一次 `POST /wf/tech/complete-task`。关不掉时它只写一条审计并返回
`{"closed": False, "error": ...}`（`cost_flow.py:326-329`），不回滚、不报错。调用点见
`cost_flow.py:515`（回传销售）与 `cost_flow.py:573`（提交工艺经理）。

于是"报价那边已经收到任务、技术这边的待办还挂着"是**设计出来的**半完成状态：
`/wf/tech/complete-task` 与 `/wf/tech/handoff` 之间任何一次超时、断网、服务重启，都会落在它上面。
报告回传这条路更彻底：`report_workflow.send_to_quote`（`report_workflow.py:680-764`）
**一次都不关**来源任务。

### 1.3 幂等键只活在任务 payload 里，且只认未关闭的任务

`_find_handoff_task`（`cpq_tech_bridge.py:418-428`）靠
`payload->>'handoff_key' = %s` 找"已经交过的这一版"，而调用点
（`cpq_tech_bridge.py:609-613`）只把 `status IN ('open','claimed')` 当作命中。后果：

* 目标任务的领取人把它做完（`completed`）之后再点一次"回传销售经理"，会**再建一条任务**、
  再发一轮消息 —— 同一版结果交了两次；
* 目标卡片上的任务被后来的派发取消（批次 2 之后是"被替代"）时同理；
* `handoff_key` 没有唯一约束，两个浏览器同时点（或超时重试撞上原请求）→ 两条任务；
* 幂等键里没有 `source_task_id`：同一张报价卡片上，两条不同来源的用户操作（例如另一条
  「新增工艺」任务带来的同一版本号）会共用一把键，互相吞掉。

### 1.4 没有"这次回传是哪一次"的唯一标识

返回值里只有 `task_id` / `task_no`，没有回传记录本身。审计（`cpq_wf_task_event`）里
也没有任何一个字段能把"任务 A 的回传"与"快照合并 + 来源任务关闭 + 消息"这几件事绑在一起，
事后只能靠时间顺序猜。

### 1.5 步骤单调只做了一半

`complete_step`（`cpq_wf.py:455-576`）无条件写 `current_step = nxt` 与
`overall_status = awaiting_handoff/in_progress`；`cpq_tech_bridge.py:645-651` 只用
`current_step <= TECH_CONFIRM_STEP` 挡了"已经推进过的卡片"，一旦判断失误（并发、或先合并快照
再被别人推进）就会把 `current_step` 或 `overall_status` 写回去。
`advance_step_no()`（`cpq_wf.py:578-585`）只被用来算**返回值**，没有参与写入。

### 1.6 返回体里的技术结果与报告没有契约

`_step2_snapshot`（`cpq_tech_bridge.py:289-330`）与 `_handoff_text` 只挑了一部分字段；
报告回传（`report_workflow.report_package`，`report_workflow.py:640-670`）里明明有
编号、版本、审核 / 发布留痕、结论、风险、附件与导出入口，但没有任何验收标准保证它们
真的随任务到了销售手上。

## 2. 用户角色与用户故事

| 角色 | 故事 |
|------|------|
| 财务经理 | 我点「回传销售经理继续报价」，要么**全部发生**（报价收到任务、第 2 步快照更新、我这条待办关掉），要么**什么都没发生**。断网重试时不能再多出一条任务。 |
| 财务经理 | 我提交工艺经理确认后，原来那条「成本测算」待办必须自己消失，而不是等我再点一次"完成"。 |
| 工艺经理 | 我在成本之后点「回传销售经理」，同一版报告点两次只能有一次交接；报告从 v1 改到 v2 必须算新的一次。 |
| 销售经理 | 我收到的任务里必须带着这一版的技术结果（参数 / 工艺 / 零件 / 组装 / 成本合计）或完整报告，不是一句"已回传"。 |
| 任何人 | 报价已经走到第 4 步时，一次成本 / 报告回传不能把卡片拽回第 3 步。 |
| 运维 | 出问题时我要能在库里查到"这是哪一次回传、来源任务是谁、目标任务是谁、关没关掉"。 |

## 3. 当前流程

```
财务：2.3 点「回传销售经理继续报价」
  tech_app cost_flow.send_to_quote
    ├─ integration_send_to_quote_body → POST /wf/tech/handoff
    │     ├─ 连接①查来源任务 → 连接②建会话/写磁盘历史 → 连接③补前置步骤
    │     ├─ 连接④ complete_step / merge_step_snapshot（各自 commit）
    │     └─ 连接⑤ send_task（自己 commit，插入任务 + 消息 + 审计）
    └─ POST /wf/tech/complete-task → 连接⑥ 关来源任务（失败被吞）
  ⇒ 任一步失败 = 半完成；重复点击 = 可能第二条任务
```

## 4. 目标流程

```
财务：2.3 点「回传销售经理继续报价」
  tech_app cost_flow.send_to_quote → POST /wf/tech/handoff（一次请求，带幂等键五元组）
    服务端：一个业务命令、一个事务、一次提交
    ├─ 0. 校验调用者角色与必要入参
    ├─ 1. 解析目标报价会话（来源任务 / 会话线索 / 新建），得到 target_quote_session_id
    ├─ 2. 用五元组算 handoff_key，查 cpq_wf_handoff
    │       命中 → 直接返回同一条 handoff_id（already_sent=True），无任何副作用
    ├─ 3. 第 2 步：current_step<=2 → complete_step(第 2 步)；>2 → 只 merge 快照（步骤只进不退）
    ├─ 4. 目标报价任务：create-or-reuse（沿用批次 2 的同类复用规则），payload 带完整结果
    ├─ 5. 关闭来源 claimed 任务（同一事务内；状态、领取人、归属都在这里校验）
    ├─ 6. 写消息（发起人 / 收件人）与审计（含 handoff_id）
    ├─ 7. 插 cpq_wf_handoff 一行（唯一约束 = handoff_key），拿 handoff_id
    └─ 8. commit；异常 → rollback，什么都不留下
  事务提交成功之后（且仅此时）：技术侧才把 quote_handoff / 审计写进自己的项目
```

## 5. 状态定义及状态转换

### 5.1 回传类型（`handoff_kind`）

| kind | 中文 | 入口 | 目标 | 是否推进报价步骤 |
|------|------|------|------|------------------|
| `cost_to_quote` | 成本回传销售 | `POST /wf/tech/handoff` | 原报价卡片的销售经理 | 是（`current_step<=2` 时完成第 2 步） |
| `cost_to_process` | 成本提交工艺经理复核 | `POST /wf/tech/return-process` | 同一卡片的工艺经理（支线 `tech_cost_return`） | 否 |
| `process_to_quote` | 工艺经理确认后回传销售 | `POST /wf/tech/handoff` | 原报价卡片的销售经理 | 是（同上） |
| `report_to_quote` | 已发布报告回传销售 | `POST /wf/tech/handoff` | 原报价卡片的销售经理 | 否（报价已在第 3 步或更后时只合并快照） |

四者必须走**同一个**服务端业务命令与同一张 `cpq_wf_handoff` 表；不允许每个 kind 各写一套。

### 5.2 来源任务状态 → 回传行为

| 来源任务状态 | 行为 |
|--------------|------|
| `claimed`（领取人是调用者） | 同一事务内置 `completed` + `completed_at`，审计 action=`complete` |
| `completed` | 不重写状态；`source_task.already=true`，本次回传若已存在同 key 记录则 `already_completed=true` |
| `open`（未领取） | 置 `completed` 并审计（技术侧确实做完了这件事）；`source_task_status_before='open'` |
| `claimed`（领取人是别人） | 业务拒绝（`WfError`），整个事务回滚 |
| 不存在 / 未传 | 不算失败：`source_task.skipped='not_found'` / `'missing_task_id'`，其余照旧提交 |

### 5.3 `cpq_wf_handoff` 记录状态

本批只有一种终态：**记录存在 = 这一次回传已经完整发生**。没有"进行中"的回传记录，
因为记录本身与它的全部副作用在同一个事务里落库。

## 6. 接口与数据契约

### 6.1 请求（`POST /wf/tech/handoff`）

| 字段 | 说明 |
|------|------|
| `handoff_kind` | 见 5.1；缺省 `cost_to_quote` |
| `session_id` | **技术项目号**（沿用今天语义，不要改成报价会话号） |
| `source_task_id` / `source_task_no` | 来源任务（可为空） |
| `source_session_id` | 需求单里记着的原报价会话号（可为空） |
| `result_version` | 结果版本，参与幂等键 |
| `result` | 技术结果（成本 / 参数 / 工艺 / 零件） |
| `report` | 已发布报告包（`report_to_quote` 必带） |
| `title` / `customer` / `project_name` / `note` | 沿用今天语义 |

`POST /wf/tech/return-process` 增加同样的 `handoff_kind`（`cost_to_process`）、`result_version`、
`session_id` 语义，其余不变。

### 6.2 幂等键（业务口径，必须落库唯一）

```
handoff_key = "|".join([
    handoff_kind,                      # 见 5.1
    source_task_id 或 "",              # 原样 strip
    source_project_id 或 "",           # = 请求里的 session_id（技术项目号）
    source_result_version 或 "",       # 成本用 cost_flow.result_version(plan)，报告用 report-v{n}
    target_quote_session_id 或 "",     # 见下
])
```

`target_quote_session_id` 取**解析得到的**报价会话号：来源任务 → 它的卡片会话号；
否则请求里的 `source_session_id`（且报价里真有这张卡片）；都没有则为空串（技术工艺独立发起的项目）。
键必须在任何写操作之前算好，且**不包含本次新生成的会话号** —— 否则新建会话这条路的
重试永远算不出同一把键，超时重试会建出第二张报价卡片。

同一把键重复调用只返回同一条记录：`handoff_id`、`target_quote_session_id`、`task_id`、
`next_step_no` 全部与第一次一致；不新建任务、不重复完成第 2 步、不重复发消息、不重复写审计。

### 6.3 数据表（新增，`{CPQ_WF_SCHEMA}.cpq_wf_handoff`）

```
handoff_id             bigint PRIMARY KEY
handoff_key            varchar(255) NOT NULL   -- UNIQUE
handoff_kind           varchar(32)  NOT NULL
source_project_id      varchar(64)
source_task_id         bigint
source_result_version  varchar(64)
target_quote_session_id varchar(32)
target_card_id         bigint
target_task_id         bigint
target_task_kind       varchar(24)
step_no                int
snapshot_sections      jsonb
source_task_closed     boolean NOT NULL DEFAULT false
source_task_status     varchar(16)
created_by_user_id     bigint
created_at             timestamptz NOT NULL DEFAULT now()
```

必须建 `CREATE UNIQUE INDEX IF NOT EXISTS uq_wf_handoff_key ON ... (handoff_key)`，
DDL 走 `ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` 的幂等写法（与
`cpq_wf._ddl_pg` 同一风格），老库升级不需要人工步骤。

### 6.4 返回体（新增字段，既有字段一律保留）

```
{
  "handoff_id": "…",            # 必须；本批的核心新增
  "handoff_key": "…",
  "handoff_kind": "cost_to_quote",
  "already_sent": false,        # 命中同 key → true
  "already_completed": false,   # 来源任务已 completed 且命中同 key → true
  "quote_session_id": "…",
  "linked_by": "task|session|new_session",
  "new_card": false,
  "next_step_no": 3, "next_step_name": "定价-利润加成",
  "returned_sections": ["s2_products", "s2_techparams"],
  "handoff": { "task_id": "…", "task_no": "TP-…", "task_kind": "handoff",
               "target_role_code": "sales_mgr", "target_role_name": "销售经理",
               "target_name": "…", "returned_to_sender": true, "source_task_no": "TP-…" },
  "source_task": { "task_id": "…", "closed": true, "status": "completed",
                   "already": false, "skipped": "" }
}
```

### 6.5 目标任务 payload（销售必须看得到的东西）

`cpq_wf_task.payload` 必须含：

* 溯源：`handoff_id`、`handoff_key`、`handoff_kind`、`result_version`、
  `source_project_id`、`source_task_id`、`quote_session_id`；
* 技术结果 `tech_result`：`params`（含 `fields`/`summary`）、`process`、`parts`/`part_costs`、
  `assembly`/`assembly_cost`、`cost`/`cost_breakdown`、`final`、`cost_confirmation`；
* 报告 `report`（`report_to_quote`）：`report_no`、`version`、`status`、`reviewed_*`、
  `published_*`、`conclusion`、`risks`、`highlights`、`attachments`、`report_url`、`pdf_url`。

合并路径（报价已推进）**更新已有任务**时不得丢掉这些字段，也不得覆盖成空 payload。

## 7. 正常路径

1. 财务在 2.3 点「回传销售经理继续报价」；技术侧带 `handoff_kind=cost_to_quote`、
   `result_version`、来源 `task_id` 调 `/wf/tech/handoff`。
2. 服务端一个事务内：解析会话 → 完成第 2 步（快照写 `s2_products`/`s2_techparams`）→
   创建 / 复用销售经理任务（payload 带全套结果）→ 关闭来源 claimed 任务 →
   写消息与审计 → 插 `cpq_wf_handoff` → commit。
3. 返回 `handoff_id` + `task_no` + `next_step_no`；技术侧把 `quote_handoff`（含 `handoff_id`）
   写进项目，`cost_review` 记一条动作留痕。
4. 销售在报价首页看到任务与第 3 步卡片；技术侧来源待办消失。

## 8. 异常路径

| 场景 | 必须的行为 |
|------|------------|
| 事务中途任何一步抛错（含写消息、关来源任务、插回传记录） | **整体回滚**：没有新任务、没有消息、没有审计、第 2 步没被完成、来源任务仍是 `claimed`、没有 `cpq_wf_handoff` 行；接口返回业务错误 |
| 客户端超时后重试（首次其实已成功） | 第二次返回**同一条** `handoff_id`，`already_sent=true`；任务、消息、审计都不增加 |
| 两个请求同时到（同一把键） | 数据库唯一约束挡住其中一个；失败方**收敛**为读取已存在记录并返回同一 `handoff_id`，不得回 500 |
| 来源任务已被别人领取 | 业务拒绝，整个事务回滚，不产生任何一条半成品记录 |
| 报价已经到第 4/5/6 步 | 只合并第 2 步快照；`current_step` 与 `overall_status` 都不得回退 |
| 新建会话（技术工艺独立发起） | 报价会话与磁盘历史记录只在事务**提交之后**创建；事务回滚时不得留下空会话 / 空历史文件 |
| 数据库连不上 / 主数据不可用 | 沿用 `_db_error` 的中文可执行文案；不做本地回落，不假装成功 |

## 9. 并发与幂等

* 幂等由**数据库唯一约束**保证，禁止"先 SELECT 再 INSERT"决定是否新建（今天
  `_find_handoff_task` 就是这种写法）。
* 事务隔离：一个回传命令 = 一个非 autocommit 连接 + 一次 commit；`cpq_wf` 的写函数必须支持
  传入连接（`conn=None` 时维持现状：自己开连接、自己提交），否则"原子"无从谈起。
* 同一 `(card_id, card_id, task_kind)` 目标任务的复用遵循批次 2 的同类复用规则；本批不重复定义。
* 顺序要求：`cpq_wf_handoff` 的插入与其余写在同一事务内；不允许先提交业务副作用、再补写记录。

## 10. 刷新、重试、重复点击与服务重启

* 技术侧页面刷新后再次点击：同版本 → `already_sent`；换版本（成本重算 / 报告换版）→ 新的一条
  `handoff_id`，两张任务并存（批次 2 的同类复用规则决定是被替代还是复用）。
* 服务在事务中途重启：事务未提交 → 数据库自动回滚 → 与 8.1 同结果；技术侧因为拿不到 HTTP 成功，
  不写 `quote_handoff`，界面提示可重试。
* 技术侧写自己的项目文件失败（磁盘满等）：不影响已提交的报价侧结果；错误要如实回给界面，
  不得反过来把报价侧结果说成失败（避免用户重复点击造成二次交接）。

## 11. 权限边界

* `/wf/tech/handoff`：`finance_mgr` / `process_mgr`（沿用今天）；`report_to_quote` 允许
  `process_mgr`（报告由工艺经理发布；已发布报告回传也允许 `finance_mgr` 沿用今天语义）。
* `/wf/tech/return-process`：`finance_mgr`。
* 来源任务的关闭只允许**领取人本人**（或 `admin`/`sys_admin`），沿用
  `cpq_wf.complete_claimed_task` 的判定；不因为"回传成功了"就放宽。
* 权限判断必须在事务开始前完成；被拒绝时不得留下任何写入。

## 12. 历史数据兼容

* 老库里已存在的任务 / 卡片 / 快照一律不动；`cpq_wf_handoff` 是新增表，老数据没有记录时
  回传照常进行（只是没有 `already_sent` 可复用）。
* 老任务 payload 里已有的 `handoff_key` 允许被新命令当作**兜底线索**读一次，但新记录
  一律以 `cpq_wf_handoff` 为准。
* 今天的返回字段（`linked_by`、`new_card`、`returned_sections`、`handoff.*`、`source_task.*`）
  必须继续存在，前端与 Agent 工具不必改就能跑。

## 13. 非目标

* 不改报价 6 步的角色与步骤定义；不改 `can_do_step` 的判定。
* 不做"回传记录"的界面列表（本批只在技术侧展示 `handoff_id` / 来源任务状态）。
* 不把 2.2 的「写入数据库」（`/wf/tech/material`）并进本事务：它写的是主数据两张表，
  与本命令的成功与否互不依赖。
* 不引入消息队列 / 后台重试器；不引入分布式事务。

## 14. 可自动化验收标准

见 `tests/test_tech_handoff_atomic_idempotent_red.py`（红测）与
`tests/fixtures/wf_handoff_harness.py`（受控假库）。验收时必须满足：

1. 连续调用两次只生成一个目标任务（`cpq_wf_task` 里同类 open/claimed 任务只有 1 条）且
   `handoff_id` 相同、`already_sent=true`。
2. 首次成功但客户端没收到（重试）不产生第二条任务；目标任务已被领取 / 已完成后再重试，
   仍返回同一 `handoff_id`。
3. 事务中途注入异常：`cpq_wf_task`、`cpq_wf_message`、`cpq_wf_task_event`、`cpq_wf_handoff`、
   `cpq_wf_card_step`、来源任务状态**全部回到调用前**。
4. 原报价在第 4 步时回传：`current_step` 仍为 4、`overall_status` 不变、第 2 步快照被合并。
5. 来源任务 / 目标任务 / 快照 / 消息 / 审计 / 回传记录六者互相引用一致。
6. 报告回传 payload 带完整报告包与版本；成本回传 payload 带完整成本、参数、工艺与零件信息。
7. 同类并发重复调用只成功一次，且失败方收敛为复用（不抛 500）。
8. 技术侧只有在拿到明确成功结果（含 `handoff_id`）后才写项目留痕；桥接失败时不写。

## 15. 人工验收场景

1. 财务在 2.3 点「回传销售经理继续报价」→ 销售收到 `TP-` 任务，任务详情里带参数 / 工艺 /
   零件 / 组装 / 成本合计；技术侧那条「成本测算」待办消失。
2. 立刻再点一次 → 界面提示"本次交接已完成（沿用上一次结果）"，销售那边**不新增**任务。
3. 把报价推到第 4 步后回到 3.3 点「回传销售经理继续报价」→ 报价仍在第 4 步，
   第 2 步快照里能看到报告编号与版本。
4. 拔掉业务库网络后点回传 → 明确的中文失败提示；恢复后重试只产生一次交接。
5. 回传成功后，2.3「回传销售经理继续报价」与 3.3「回传销售经理继续报价」的结果区要显示
   这一次交接的 `handoff_id` 和来源待办状态（`source_task`：已关闭 / 无需关闭 / 关不掉的原因）；
   刷新页面后仍看得到 —— 出问题时按这个编号就能在库里查到「哪一次回传、来源任务是谁、
   目标任务是谁、关没关掉」。

## 16. 不允许减少的既有能力

* `linked_by` 三条线索（来源任务 / 会话号 / 新建真实报价会话）的语义与 `new_card` 提示；
* `_force_done` 补齐第 1 步、`complete_step(on_behalf_of=...)` 的代完成留痕；
* 第 2 步快照的 DA 列名口径（`_step2_snapshot` / `_merge_report_snapshot`）；
* 报价助手会话历史（`_seed_quote_history`）与"绝不拿技术项目号冒充报价会话号"；
* 批次 2 的任务并存 / 复用 / 替代规则与前端渲染；
* 技术侧 2.3 的四个去向按钮、3.3 的回传按钮与既有的审计动作名。
