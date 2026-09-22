# 成品主数据写入幂等、编码取号并发与重复点击保护（批次 4）

- 状态：Spec + 红测（已实现）
- 红测：`tests/test_tech_material_write_idempotency_red.py`
- 范围：`cpq_tech_bridge.py`（写主数据命令：一个事务 + 业务幂等键 + 取号互斥）、
  `cpq_db.py`（`connect` 暴露显式事务连接）、`cpq_suite_server.py`（`/wf/tech/material` 请求体）、
  `tech_app/backend/services/cpq_bridge.py`（回调参数）、
  `tech_app/backend/services/cost_flow.py`（业务版本 + 业务结果与审计）、
  `tech_app/backend/models/integration.py`（`MaterialWrite` 增字段）、
  `tech_app/frontend/cost-review.js`（幂等命中的界面口径）
- 依赖：与批次 3（`docs/specs/tech-handoff-atomic-idempotent-close.md`）**改同一批文件的**
  **不同函数**——批次 3 改 `send_to_quote` / `return_to_process` / `cpq_wf.send_task`，
  本批改 `write_material` / `cpq_db.connect` / `cost_flow.write_material`。
  批次 3 先落地再落本批，避免两批同时改一个函数；本批不碰任何回传语义。
- 本批**不**改：主数据表与成本表的既有列与口径、`/wf/tech/handoff` 与 `/wf/tech/return-process`、
  步骤角色权限（`cpq_wf.can_do_step`）、2.2 → 2.3 的既有去向按钮与文案。

## 1. 背景与真实问题

2.3（成本测算）右侧看板只有一个「写入数据库」动作：它调技术侧 `cost_flow.write_material`，
再回调一体化服务的 `POST /wf/tech/material` → `cpq_tech_bridge.write_material`，
把**成品编码 / 名称 / 材料单价**写进 `md_clm_material_base_info` 与 `md_clm_material_cost_cnf`。
今天这条路上有三个确定性缺陷，双击、超时重试、两个人同时点都会踩到。

### 1.1 每次调用都新建一个成品编码（没有任何幂等）

`write_material`（`cpq_tech_bridge.py:104-171`）的 docstring 明说「每次调用都**新建**一个成品编码
—— 业务要的就是"每次产生一个新的成品编码"，不做按名称去重」。受控假库实测：

```
第 1 次写入：number=92022001  material_id=…001
第 2 次写入：number=92022002  material_id=…003   ← 同一份成本、同一个名称
```

于是 2.3 上「写入数据库」被双击一次（或响应超时后用户重按一次），主数据里就多出一个成品，
报价那边的定价规则按编码匹配，两个编码意味着两条互不相干的定价链路。

### 1.2 服务端入口根本没有 project / 版本，返回体没有命中状态

`POST /wf/tech/material`（`cpq_suite_server.py:702-708`）只接受
`product_name / unit_price / breakdown / spec`；`cpq_bridge.write_material`
（`tech_app/backend/services/cpq_bridge.py:60-68`）也只发这四个字段，
返回体只有 `material_id / number / name / material_unit_price / breakdown / tables / by`。
**服务端不可能知道"这是哪一次业务动作"**，所以幂等无从谈起。

### 1.3 主数据与成本表各自独立提交（没有事务）

`write_material` 用 `cpq_db.connect(readonly=False)`，而 `connect`
（`cpq_db.py:47-70`）是 `psycopg.connect(..., autocommit=True)`：主数据 INSERT 与成本表
INSERT 各自提交一次。受控假库实测（注入成本表 INSERT 失败）：

```
raised: BridgeError 写入主数据失败：RuntimeError: 假库注入故障（命中 'md_clm_material_cost_cnf'）
主数据表： ['92022001']      ← 孤儿行：编码有、成本没有
成本表：   []
```

这条孤儿行在报价侧看起来"成品存在"，成本却永远是空的。

### 1.4 取号是「先查再用」，撞号只靠运气

`_next_code`（`cpq_tech_bridge.py:75-92`）SELECT 出已用编码取 max + 1；
`_code_taken`（`cpq_tech_bridge.py:96-101`）回查一次；`_MAX_CODE_RETRY = 5` 重试。
注释（`cpq_tech_bridge.py:26-29`）自己承认「number 上有没有唯一索引未知，所以这里不依赖数据库约束」。
受控假库实测（两个线程同时写同一把业务版本，对齐点在插入主数据那句之前）：

```
线程 A：ok   number=92022001
线程 B：BridgeError 写入主数据失败：duplicate key value violates unique constraint
```

即：**第二个点击的人看到的是"写入主数据失败"**，而且这是单进程内的时序都被抓到的情况；
两个服务进程、两个浏览器标签同样会撞。反过来，如果不强制这个交错，两边各自回查一次也能"侥幸"
都成功——这正是"不能完全消除竞争"的意思：它取决于时序，不取决于规则。

### 1.5 前端已经在防重复点击，但不能作为幂等的依据

`crRunOp`（`tech_app/frontend/cost-review.js:613-614`）有 `if (crBusy) return false;`，
看板按钮也按 `crBusy` 置灰 —— 这一层要保留，但它挡不住：刷新页面后再点、两个标签同时打开、
响应超时后用户手动重试、Agent 走 `oc_agent.py:2560` 调同一个服务。
所以**幂等必须在后端**。

## 2. 用户角色与用户故事

| 角色 | 故事 |
|------|------|
| 财务经理 | 我点「写入数据库」，看到的成品编码就是这单成本对应的那一个；我不小心点了两次，第二次必须告诉我"沿用已有编码"，而不是悄悄又建一个成品。 |
| 财务经理 | 成本重算过（数量或四项合计变了），或者我把成品名称、规格改了，再写入时我**需要**一个新的编码 —— 这是"确实要新建"。 |
| 财务经理 | 如果写库中途失败（成本表写不进去 / 网络断），主数据里不能留下一条没有成本的孤儿成品；我要看到明确的中文失败原因。 |
| 工艺经理 / 销售 | 我在报价里按成品编码匹配定价规则；同一个底稿被重复写入不该产生第二个编码，否则我的定价链路会分叉。 |
| 运维 | 同一份成本被点了 5 次，我要能在库里查到"哪一次是真正写入的、哪几次是沿用的"，以及每一次属于哪一版成本结果。 |

## 3. 当前流程

```
财务：2.3 点「写入数据库」
  tech_app cost_flow.write_material
    ├─ _ready(project_id)                     # 必须有已确认的成本
    ├─ integration_material_write_record
    │    ├─ bridge_call(cpq_bridge.write_material, token, name, total, breakdown, spec)
    │    │      → POST /wf/tech/material {product_name, unit_price, breakdown, spec}
    │    │           → cpq_tech_bridge.write_material
    │    │                ├─ connect(readonly=False)        # autocommit=True
    │    │                ├─ _next_code → _code_taken        # 先查再用
    │    │                ├─ INSERT 主数据                    # 独立提交
    │    │                └─ INSERT 成本表                    # 独立提交
    │    └─ plan.material_writes.append(record)             # 每次调用都追加一条
    └─ _record_action("material-write") + store.audit("cost_review_material_write")
  ⇒ 双击 = 两个编码；超时重试 = 两个编码；成本表失败 = 孤儿主数据行
```

## 4. 目标流程

```
财务：2.3 点「写入数据库」
  tech_app cost_flow.write_material
    ├─ _ready(project_id)
    ├─ 业务版本 result_version = mat-v1:{cost_flow.result_version(plan)}:{sha1(名称+规格)[:10]}
    ├─ POST /wf/tech/material {product_name, unit_price, breakdown, spec, project_id, result_version}
    │    服务端：一个业务命令、一个非 autocommit 连接、一次 commit
    │    ├─ 0. 校验名称与单价（沿用今天的口径与文案）
    │    ├─ 1. 建写入记录表（幂等 DDL）
    │    ├─ 2. 取数据库 advisory 锁（pg_advisory_xact_lock，跨进程；事务结束自动释放）
    │    ├─ 3. 有业务幂等键 → 查写入记录
    │    │        命中 → 直接返回原来那一行（already_written=true），不写任何表
    │    ├─ 4. 取下一个编码 → INSERT 主数据 → INSERT 成本表 → INSERT 写入记录
    │    └─ 5. commit；异常 → rollback（三张表一起回滚，不留孤儿）
    ├─ 幂等命中 → 不追加第二条 material_writes
    └─ 动作留痕 + 审计（含 result_version / idempotency_key / already_written）
```

## 5. 状态定义及状态转换

### 5.1 写入结果状态（对用户可见）

| 状态 | 触发 | 界面必须说什么 |
|------|------|----------------|
| 新建成功 | 新版本，或没有幂等键 | 「已写入数据库：成品编码 9202200X「名称」」 |
| 幂等命中 | 同一把业务幂等键重复调用 | 「沿用已有成品编码 9202200X，本次没有新建」 |
| 失败 | 校验不过 / 库不可用 / 写入出错 | 沿用今天的中文可执行文案；不得说"已写入" |

### 5.2 写入记录（`cpq_wf_material_write`）

只有一种终态：**记录存在 = 这一版业务动作已经完整写入过**。没有"进行中"的记录，
因为写入记录与两张主数据表在同一个事务里落库。

### 5.3 result_version 的构成（业务版本 = 内容版本，不是计数器）

```
result_version = "mat-v1:" + cost_flow.result_version(plan) + ":" + sha1(f"{name}\n{spec}")[:10]
                 └ 成本结果版本 cost-v1:{数量}:{材料+人工+制费+加工 合计}
```

| 变化 | 是否新版本 | 结果 |
|------|-----------|------|
| 双击 / 刷新后再点 / 超时重试（内容没变） | 否 | 沿用原编码 |
| 数量变化（`plan.quantity`） | 是 | 新编码 |
| 四项成本合计变化（成本重算） | 是 | 新编码 |
| 成品名称变化 | 是 | 新编码（否则"改名后重写"会静默沿用旧行，用户以为改生效了） |
| 规格说明（`spec`）变化 | 是 | 新编码 |
| 换了项目 | 是（键里含 project_id） | 新编码，两个项目互不影响 |

## 6. 接口与数据契约

### 6.1 请求（`POST /wf/tech/material`，新增两个字段，旧字段一律不动）

| 字段 | 说明 |
|------|------|
| `product_name` / `unit_price` / `breakdown` / `spec` | 沿用今天语义 |
| `project_id` | **新增**：技术项目号；参与业务幂等键 |
| `result_version` | **新增**：见 5.3；参与业务幂等键 |

服务端签名（位置参数顺序不能变，新参数追加在 `spec` 之后、都必须有默认值）：
`cpq_tech_bridge.write_material(user, product_name, unit_price, breakdown=None, spec="", project_id="", result_version="")`

### 6.2 业务幂等键（业务口径，必须落库唯一）

```
idempotency_key = "|".join([project_id, result_version, action_type])     # action_type = "material-write"
```

* 键必须在**任何写操作之前**算好；
* `project_id` 或 `result_version` 任一为空 → `idempotency_key = ""` → **不做幂等**
  （老调用方行为不变：每次新建一个编码）。绝不"用简称/名称兜底"凑一把键。

### 6.3 数据表（新增，`{CPQ_WF_SCHEMA}.cpq_wf_material_write`）

```
write_id             bigint PRIMARY KEY
idempotency_key      varchar(255) NOT NULL    -- UNIQUE
project_id           varchar(64)  NOT NULL
result_version       varchar(128) NOT NULL
action_type          varchar(32)  NOT NULL    -- material-write
material_id          varchar(64)
number               varchar(32)
name                 varchar(255)
unit_price           numeric(18,2)
status               varchar(16)  NOT NULL DEFAULT 'done'
created_by_user_id   bigint
created_at           timestamptz  NOT NULL DEFAULT now()
```

* 唯一约束必须同时覆盖 `(project_id, result_version, action_type)`（业务口径）与
  `idempotency_key`；
* DDL 一律幂等：`CREATE TABLE IF NOT EXISTS` / `CREATE UNIQUE INDEX IF NOT EXISTS`
  （与 `cpq_wf._ddl_pg` 同一风格），老库升级不需要人工步骤；
* **建表失败必须抛错**（没有这张表就没有幂等与留痕）；**唯一索引建不上只记不抛** ——
  `md_clm_material_base_info.number` 上是否已有重复历史数据不由此命令决定，
  取号本身的原子性由 6.4 的锁保证。

### 6.4 取号的原子性

* 取号 + INSERT 主数据 + INSERT 成本表 + INSERT 写入记录整段在**一个事务**里，
  并在事务内先取 `pg_advisory_xact_lock(<固定 key>)`：跨进程互斥，提交/回滚自动释放。
* `cpq_db.connect` 增加 `autocommit: bool = True` 参数：默认值不变（导入数据库等既有调用方
  行为不变），写入命令显式要 `autocommit=False`。
* `number` 上的唯一索引属于**第二道防线**；第一道是锁。实现不得只靠"先 SELECT 回查"。

### 6.5 返回体（新增字段，既有字段一律保留）

```json
{
  "material_id": "…", "number": "92022007", "name": "…",
  "material_unit_price": 123.45, "breakdown": {…},
  "tables": ["md_clm_material_base_info", "md_clm_material_cost_cnf"],
  "by": "财务一",
  "already_written": false,
  "idempotency_key": "proj-a|mat-v1:cost-v1:1:123.45:0f1e2d3c4b|material-write",
  "result_version": "mat-v1:cost-v1:1:123.45:0f1e2d3c4b"
}
```

`already_written=true` 时 `material_id` / `number` / `name` 必须是**原来那一行**的值（不是本次请求里的名称）。

### 6.6 技术侧契约（`MaterialWrite` 增字段，全部带默认值）

```
result_version:    str   = ""       # 6.2/5.3 的业务版本
idempotency_key:   str   = ""
already_written:   bool  = False
```

* 幂等命中 → **不追加** `plan.material_writes`（同一个成品编码在业务结果里只留一条）；
* 幂等命中 → 动作留痕文案必须写「沿用已有成品编码 9202200X……（本次没有新建）」；
* `store.audit("cost_review_material_write")` 的 detail 必须带
  `number / already_written / result_version / idempotency_key`。

## 7. 正常路径

1. 财务确认成本后在 2.3 点「写入数据库」；技术侧算出 `result_version`，连同 `project_id` 一起发出去。
2. 服务端一个事务：取锁 → 查写入记录（未命中）→ 取下一个编码 → 主数据行 → 成本行 → 写入记录 → commit。
3. 返回 `number` / `material_id` / `already_written=false`；技术侧把记录追加进 `plan.material_writes`，
   把编码回填整机参数（`integration.apply_material_code`），写动作留痕与审计。
4. 界面显示成品编码与单价，数据库无需人工干预。

## 8. 异常路径

| 场景 | 必须的行为 |
|------|------------|
| 成本表 INSERT 失败 | 整个事务回滚：主数据表**不得**留下孤儿行；返回 `_db_error` 的中文文案 |
| 写入记录 INSERT 失败 | 同上：主数据与成本一起回滚 |
| 幂等命中 | 不写任何表、不新增业务结果条目，返回原来那一行 + `already_written=true` |
| 名称 / 单价校验不过 | 沿用今天的文案（`缺少产品名称` / `材料单价不是数值` / `材料单价为 0…`），不产生任何写入 |
| 流水用尽（`92022xxx` 到 999） | 沿用今天的中文报错，不污染主数据、不插入越界号 |
| 连不上业务库 | 沿用 `_db_error` 的中文可执行文案；不做本地回落、不假装成功 |
| 老调用方（没有 project / 版本） | 不报错、不假装命中：每次新建一个编码（与今天一致） |

## 9. 并发与幂等

* 幂等由**数据库唯一约束 + 数据库锁**保证；禁止依赖前端按钮禁用，也禁止把"先 SELECT 再 INSERT"
  当作取号的唯一保障。
* 两个进程 / 两个标签同时写**同一把键**：一个真正写入，另一个收敛为"读到已有记录并返回同一编码"，
  都不得抛错、都不得出现两个编码。
* 两个**不同版本**并发：两个编码都要成功，谁都不许因为撞号而失败。
* 事务隔离：一个写入命令 = 一个 `autocommit=False` 连接 + 一次 commit；
  写入记录、主数据、成本三者的可见性必须同时发生。
* advisory 锁的 key 是固定常量：这把锁保护的是"编码序列"，与业务幂等键无关；
  业务幂等由唯一约束裁决（两层缺一不可）。

## 10. 刷新、重试、重复点击与服务重启

| 场景 | 必须的行为 |
|------|------------|
| 执行中重复点击 | 前端 `crBusy` 守门不许发第二个请求（既有能力，保持）；后端仍必须幂等 |
| 首次成功但响应丢失（客户端超时） | 重试返回同一编码 + `already_written=true`，不新增主数据行 |
| 刷新页面后再点 | 同一版本 → 沿用已有编码（服务端判定，与页面状态无关） |
| 两个标签同时打开同一项目 | 同上；不得因为"另一个标签已经在写"而报错或写出两个编码 |
| 服务在事务中途重启 | 事务未提交 → 数据库自动回滚 → 与"没有点过"一致；界面可重试 |
| 写入成功但技术侧保存项目失败 | 不重做写入（服务端已幂等）；界面按既有错误路径提示，重试会命中幂等 |

## 11. 权限边界

* `POST /wf/tech/material` 沿用今天的调用方校验（一体化服务侧 `user` 上下文）；
* `POST /api/projects/{id}/cost-review/material-write` 沿用 `auth.COST_ROLES`，
  `POST /api/projects/{id}/integration/material-write` 沿用 `auth.WRITE_ROLES`；
* **幂等不是权限**：命中已有记录时也必须先过权限（不能因为"已经写过"就绕过角色校验）；
* 本批不新增角色、不放松任何既有闸门。

## 12. 历史数据兼容

* 主数据表与成本表**不迁移、不改写、不删除**：已经写进去的成品编码原样保留，
  本批只保证"以后不再重复产生"。
* `cpq_wf_material_write` 是新增表：老项目没有记录时写入照常进行（只是没有 `already_written` 可复用）。
* `MaterialWrite` 的新字段带默认值：老项目文件里的历史记录反序列化后
  `result_version=""` / `already_written=False`，不得报错、不得需要迁移脚本。
* 老调用方（不带 `project_id` / `result_version`）的返回体形状不变（新增字段是附加的）。
* 取号仍然从现有 `92022` + 3 位流水的最大值继续，不重排历史编码。

## 13. 非目标

* 不做主数据 / 成本表的字段或口径调整，不做"按名称去重"以外的新业务规则；
* 不做写入记录的界面列表 / 历史查询页（本批只在 2.3 的结果区展示这一次的结果）；
* 不把 `number` 上的唯一索引作为**前置条件**（老库里可能有重复历史数据）；
* 不改批次 3 的回传闭环、不改 `cpq_wf` 的任务语义、不改 2.2 → 2.3 的页面流转；
* 不引入分布式锁 / 消息队列 / 后台重试器。

## 14. 可自动化验收标准

见 `tests/test_tech_material_write_idempotency_red.py`（红测）与
`tests/fixtures/material_write_harness.py`（受控假库）。验收时必须满足：

1. 同一把业务幂等键连续调用两次：一个成品编码、一行主数据、一行成本、一条写入记录，
   第二次 `already_written=true` 且返回同一 `material_id`。
2. 不同 `result_version` / 不同 `project_id` → 不同编码。
3. 成本表 INSERT 失败：主数据表回到调用前（无孤儿行），接口返回业务错误。
4. 写入记录 INSERT 失败：主数据与成本一起回滚。
5. 一次写入的所有语句都在同一个事务里（没有 autocommit 裸写）。
6. 两个线程并发写同一把键：两次都成功、只有一个编码、至少一次报告幂等命中。
7. 四个线程并发写不同版本：四个不同编码、零失败。
8. 返回体带 `already_written` / `idempotency_key` / `result_version`；写入记录表里有对应列。
9. DDL 幂等（`IF NOT EXISTS`）且业务幂等键有唯一约束。
10. 技术侧把 `project_id` / `result_version` 发出去；幂等命中不追加业务结果留痕，
    审计里记下 `already_written=true`。
11. 前端连点两次只发一个请求；幂等命中时界面说「沿用已有编码」。
12. 历史主数据行原样保留，任何路径都不出现 DELETE。

## 15. 人工验收场景

1. 财务在 2.3 点「写入数据库」→ 结果区显示成品编码 9202200X；再点一次 →
   显示「沿用已有成品编码 9202200X，本次没有新建」，主数据里仍然只有一行。
2. 把核算批量（数量）从 1 改成 2，重算成本后保存，再点「写入数据库」→ 出现第二个编码，
   两行主数据的名称/单价各自正确。
3. 把成品名称改一个字再点「写入数据库」→ 出现新编码（改名确实生效），旧行仍在。
4. 断掉业务库网络（或让成本表写权限失效）后点「写入数据库」→ 明确的中文失败提示；
   恢复后重试只产生一个编码，且第一次失败没有在主数据里留下孤儿行。
5. 打开两个标签页、同时点「写入数据库」→ 两个标签看到的成品编码相同，库里只多一行。

## 16. 不允许减少的既有能力

* 2.3「写入数据库」的既有结果区文案（产品名称 / 材料单价四项合计 / 已写入哪些表）与
  成品编码回填整机参数（`integration.apply_material_code`）；
* 「发送至报价」时主数据写不进去的临时编码回落（`code_fallback` / `next_local_code`）与
  现有 `integration_material_write` / `integration_material_write_fallback` 审计；
* `plan.material_writes` 语义（成品编码留痕）与 `has_material_code` / `payload()["material"]`；
* `_db_error` 的四类中文可执行文案与"绝不本地回落假装成功"；
* 前端 `crBusy` 守门、三个去向按钮的禁用态与 Agent（`oc_agent.py:2560`）复用同一个服务；
* 批次 3 的回传闭环、批次 2 的任务并存规则。
