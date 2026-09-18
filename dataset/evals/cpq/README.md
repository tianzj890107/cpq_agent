# CPQ 业务回归数据集（`dataset/evals/cpq`）

可复用、数据驱动、离线运行的 CPQ 业务回归测试集。数据与执行器分离：

- 数据：本目录（`cases/` 案例、`fixtures/` 受控假数据、`schemas/` 结构约束、`routes/` 真实路由快照）。
- 执行器：`scripts/cpq_eval/`（`dataset` 加载校验、`checks` 业务契约、`production` + `prodkit`
  真实生产代码边界、`runner` 编排、`coverage` 覆盖矩阵、`scoring` 加权与脱敏）。
- 守护测试：`tests/test_cpq_eval_*.py`。

数据集只做回归守护，**不改任何业务实现**，也不访问真实 PostgreSQL、PDT、线上服务与真实模型。

> 本数据集最重要的验收标准不是「全绿」，而是：**再次把财务 / 销售 / 总监的实际权限关掉，
> 测试能在部署前立即变红，并准确指出是哪条真实路由、哪层门禁出了问题。**
> 这套「测试真的能杀死回归」的证明在 `tests/test_cpq_eval_production_backed.py`（mutation / sentinel）。

## 业务口径（唯一事实源）

技术工艺是**五阶段 13 子步骤**，不是旧的九阶段：

| 阶段 | 阶段名 | 子步骤 |
| --- | --- | --- |
| 1 | 工艺评估需求 | 1.1 创建需求 `requirement-create`、1.2 确认需求 `requirement-confirm`、1.3 审核需求 `requirement-review` |
| 2 | 图纸解析 | 2.1 图纸解析 `drawing` |
| 3 | 组装与整合 | 3.1 整合图纸 `process/drawings`、3.2 参数推荐 `process/params`、3.3 组装工艺 `process/process` |
| 4 | 成本测算 | 4.1 零件成本 `cost/parts`、4.2 组装成本 `cost/assembly`、4.3 汇总 `cost/total` |
| 5 | 工艺评估报告 | 5.1 汇总结果 `summary`、5.2 结果审核 `report-review`、5.3 发布并回传报价 `report-publish` |

报价 Agent 保持**六步**：1 确认需求配置 / 2 工艺确认 / 3 定价－利润加成 / 4 报价－其他加价项 /
5 报价方案 / 6 输出报价单。技术工艺阶段与报价六步**不使用同一套编号**。

stage id、URL 与历史数据兼容字段保持不变；新案例、标题、期望结果与文档统一使用上面的口径。

## 目录

```
dataset/evals/cpq/
  README.md
  schemas/    case.schema.json / suite.schema.json
  cases/      quote tech cross_agent auth_acl session_history concurrency
              llm_contract failure_recovery ui_protocol
  fixtures/   quote tech handoff history documents provider
  routes/     project_routes.json（读路由覆盖来源 + 快照）
              write_route_policy.json（每条写路由的分类与案例）
  reports/    只放 .gitkeep（报告默认写临时目录，禁止提交运行产物）
```

`routes/` 两份文件由真实路由表现算，**不要手改**；重算命令：

```bash
python3 -m scripts.cpq_eval.runner --snapshot-routes
```

`tests/test_cpq_eval_route_coverage.py` 会把「现算 vs 快照」的差异报出来：真实路由增删、
白名单路由改名、新增写路由没有对应 ACL / 角色案例，都会让测试失败并列出漏测路由。

## 案例结构

每条案例至少包含 `id / version / title / domain / priority / layer / roles / preconditions /
input / actions / expected / invariants / tags / source_specs`，可另带 `stage / sub_step /
quote_step / failure_type / notes / executor / entry / covers_routes`。

约定：

1. `id` 稳定可读，禁止按运行时间生成；全库唯一。
2. `expected` 是结构化断言：`http / state（点路径）/ records（计数）/ events / messages /
   ui_protocol / concurrency / result / error / forbidden`。
3. 金额一律 decimal 字符串（`"15.5000"`），禁止浮点比较。
4. 时间只校验格式（`{"$format": "iso8601"}`）/ 顺序（`{"$order": "ascending"}`）/ 窗口，不写死当前时间。
5. 每条案例必须列明 `source_specs`，且引用必须真实存在 —— 文件路径，或 `module:<dotted.path>` /
   `route:<METHOD> /api/...` 真实入口引用；**production-backed 的 P0 必须带 `module:` / `route:`**。
6. P0 必须含反例或失败路径（禁止项 / 动作失败期望 / 4xx / 反例 tag）。
7. 写操作必须声明幂等键或重复执行期望。
8. `auth_acl` 案例的 `input.access_matrix` 必须同时含 `allow` 与 `deny`。
9. 回传案例必须校验 `source_project_id / source_task_id / business_case_id` 等关联字段，防串项目。
10. 模型输出只断言结构化工具调用与业务结果，不断言逐字文本。

断言操作符：`$present` `$absent` `$ne` `$gte` `$lte` `$gt` `$lt` `$len` `$contains`
`$contains_text`（列表里任意一条字符串包含该片段）`$any_of` `$format` `$order` `$within` `$truthy`。
**只有全部键以 `$` 开头才会按操作符解析**，否则按严格相等比较。

## 执行分层（executor / layer）

`layer` 是案例数据的**离线 / 联网属性**（历史字段）；`executor` 是**案例由谁执行** —— 发布门禁
只看 `executor`。Sim 自证的案例不计入发布门禁。

| executor | 说明 | 计入发布门禁 |
| --- | --- | --- |
| `specification_only` | 没有可执行步骤，只固化规范 / 期望；声明运行期断言即判失败 | 否（不计为执行过） |
| `simulation` | `runner.Sim` 状态机执行，验证业务规格与数据格式 | 否 |
| `production_unit` | 直接调用真实生产模块的纯函数（ACL 判定、状态推进、关联解析、幂等写入） | 是 |
| `production_http` | 真实 FastAPI app / `TestClient`，经真鉴权依赖、`project_write_guard` 与路由 `_require` | 是 |
| `recorded_provider` | 固定模型 / SSE 响应进入真实 Agent 工具分发边界 | 是 |
| `postgres_integration` | 真实隔离 PostgreSQL 的唯一约束 / 事务 / 原子领取 / 并发回传 | 默认跳过 |

`layer` 只是案例数据的**离线 / 联网属性**（历史字段，保持兼容），**不决定执行层**：

- `deterministic` → 离线，案例可以是 `simulation` / `production_unit` / `production_http`；
- `recorded_provider` → 必须用 `recorded_provider` 执行器（回放固定 provider 响应）；
- `integration` → 必须用 `postgres_integration` 执行器（隔离 PostgreSQL）。

执行层由 `executor_of()` **唯一决定**：显式 `executor` 字段优先 → 有 `actions` 则 `simulation`
→ 否则 `specification_only`。改 `layer` 字段**不会**把案例抬进生产层（有专门的声明一致性
门禁守护，见 `tests/test_cpq_eval_executor_declaration.py`）。

runner 的最终摘要会分别给出：总案例数、实际执行案例数（skipped 不算执行过）、生产入口执行
案例数（`production_unit` + `production_http` + `recorded_provider`）、数据库集成案例数、
仅规范 / 模拟案例数，并逐层列出 passed / failed / skipped / invalid。

production-backed 案例必须声明真实入口与步骤：

```json
"executor": "production_http",
"entry": {"http": {"method": "PUT", "path": "/api/projects/{project_id}/cost-review"}},
"input": {"projects": {...}, "identities": {...},
          "steps": [{"id": "save", "http": {"identity": "finance", "project": "handoff",
                                            "method": "PUT",
                                            "path": "/api/projects/{project_id}/cost-review",
                                            "json": {"note": "..."}}}]}

"executor": "production_unit",
"entry": {"module": "backend.services.project_access", "function": "can_write"},
"input": {"steps": [{"id": "ask", "call": {"args": {"user": {"$identity": "finance"},
                                                    "meta": {...}}}}]}
```

步骤类型：`call`（生产函数）/ `http`（真实路由）/ `route_sweep`（动态遍历全部项目级读路由）/
`write_matrix`（写路由 × 多角色门禁矩阵）/ `concurrent`（`threading.Barrier` 受控交错，
**禁止用 sleep 制造竞争**）。引用解析：`$identity` / `$project` / `$project_id` / `$username` /
`$files` / `$fixture`（`$pick` 可再取子对象，如 `{"$fixture": "quote/legacy_quote_case.json",
"$pick": "card"}`）。

`route_sweep` 的契约：逐条读路由**要么被真请求、要么在 `skipped` 里写明原因**（`unaccounted`
必须为 0）；命中通用 ACL 文案的 404（`acl_blocked_paths`）、403（`forbidden_paths`）、
≥500（`server_error_paths`）都必须为空 —— 读接口被误拦、报错或扫荡静默少探一条都会失败。

### 发布门禁口径

- `simulation` 通过率与 `production-backed` 通过率**分开统计**，simulation 通过不能抵消
  production-backed 失败；
- `production-backed` 的 P0 必须 100% 通过，任一 P0 `failed` / `invalid` 都返回非零退出码；
- `integration`（`postgres_integration`）未启用时单独记 `skipped`，**不算通过**；
- 声明 production-backed 却没有任何真实生产入口被命中 → 直接判 `failed`（防退化成 Sim）。

runner 输出的门禁摘要形如：

```
-- 执行层 / 发布门禁 --
  specification_only（只固化规范，未执行）：0 条（通过 0 / 失败 0 / 跳过 0 / 非法 0）
  simulation（Sim 假库，不计入发布门禁）：241 条（通过 241 / 失败 0 / 跳过 0 / 非法 0）
  production_unit（真实生产函数）：37 条（通过 37 / 失败 0 / 跳过 0 / 非法 0）
  production_http（真实路由）：32 条（通过 32 / 失败 0 / 跳过 0 / 非法 0）
  recorded_provider（回放 + 真实分发边界）：5 条（通过 5 / 失败 0 / 跳过 0 / 非法 0）
  postgres_integration（隔离 PostgreSQL，默认跳过）：13 条（通过 0 / 失败 0 / 跳过 13 / 非法 0）
  · 总案例数：<随数据集增长>
  · 实际执行案例数：<总案例 - integration 跳过>（skipped 不算执行过）
  · 生产入口执行案例数：<production_unit + production_http + recorded_provider>（不含 simulation）
  · 数据库集成案例数：<postgres_integration 实跑>
  · PostgreSQL 场景：案例 13 条，唯一场景 12 个（通过 / 失败 / 非法 / 跳过），
    cleanup 成功 / 失败
  · 仅规范 / 模拟案例数：<specification_only + simulation>
  simulation 通过率与 production-backed 通过率**分开两行**，互不抵消
  integration 跳过：<未启用时的条数>（单独一行，不算通过）
  P0 production-backed 全部通过（否则非零退出）
```

`postgres case count` 与 `postgres unique scenario count` **必须分开看**：业务案例允许引用同一
场景（例如 `acl.integration.token_probe` 与 `pg.visibility.role_scoped_tasks` 都跑
`visibility.role_scoped_tasks`），报告里显示「13 cases / 12 unique scenarios」，重复案例不会
虚增数据库能力覆盖。

### PostgreSQL 集成层安全边界

`postgres_integration`（`scripts/cpq_eval/pg_integration.py` + `pg_scenarios.py`）只读
`CPQ_EVAL_PG_*`，**绝不回退到生产 `CPQ_PG_*`**；host 必须是回环地址或 CI service container
别名，命中 `pdt / prod / production / 172.16.10.34 / 172.16.5.181 / :8010 / metabase` 立即拒绝。
每条用例自建 `cpq_eval_it_<hex>` 临时库、跑完 `DROP DATABASE ... WITH (FORCE)`，只删自己建的库。
子进程超时 / 崩溃 / 返回格式错误时，父进程用 `cleanup_orphan` 兜底清理**同一个** child_db，
且只接受 `^cpq_eval_it_[0-9a-f]{10}$`（禁止模糊匹配批量 DROP）；清理失败进报告，不静默忽略。

父层（`pg_integration.guard_config`）与子层（`pg_scenarios.guard`）调用的是
`scripts/cpq_eval/pg_guard.py` 里的**同一份** `guard()`，不会出现一边放行、一边拒绝。
GitLab CI service alias 只有在 `CPQ_EVAL_PG_CI=1` + 显式 alias 白名单下才放行。

真库层专门验证假库证明不了的东西：`uq_wf_handoff_key` 唯一约束、`uq_wf_task_open_kind` 部分
唯一索引、`WHERE status='open'` 的原子领取、非 autocommit 事务回滚、角色池任务可见性、
跨业务实例不串单、legacy 行缺列可读。缺隔离 PG 环境时整层 `skipped`（runner 单列，不算通过）；
本机可用 `CPQ_EVAL_PG_PYTHON` 指向装了 psycopg 的解释器。

PG 检查项 → 案例映射：

| 检查项 | 案例 |
| --- | --- |
| 两人并发领取只有一方成功 | `pg.claim.concurrent_two_claimers_one_winner` |
| 同一回传并发只有一次副作用 | `pg.handoff.concurrent_same_key_single_row` |
| 幂等键重复提交不产生重复任务 | `pg.task.duplicate_submit_single_open_row` |
| 事务中途失败整体回滚 | `pg.tx.midway_failure_rolls_back_all` |
| 权限拒绝零副作用 | `pg.claim.role_denied_no_side_effect` |
| 财务 / 销售角色池可读、无关角色不可读 | `pg.visibility.role_pools_isolated`、`pg.visibility.role_scoped_tasks` |
| legacy 缺列仍可读 | `pg.legacy.null_new_columns_readable` |
| 业务实例不串单 / 串回传 | `pg.cross_instance.no_cross_handoff` |
| 部分唯一索引是最终裁决者（绕过应用层原始 INSERT 必须被拒） | `pg.task.db_rejects_duplicate_open` |
| 同卡片同类型签名变化 → 旧任务取消 + 新任务替代（双向指针） | `pg.task.supersede_on_signature_change` |
| 真实初始化函数建出的表 / 列 / 索引 / 约束与生产 DDL 一致 | `pg.schema.catalog_parity` |

**项目级** ACL 可见性（`plan.finance_handoff` / 来源报价关联）落在项目元数据上，元数据走
JSON meta store 而不是 PostgreSQL，因此由 `production_http` 层的 `acl.real.finance.*` /
`acl.real.sales.*`（真实 `project_access` guard + 真鉴权依赖 + 真实路由）验证；PG 层只验证
数据库承载的角色池任务可见性，不伪造一份 meta store 的 SQL 副本。

### recorded_provider 的 `provider_replay=simulated` 例外

`llm_contract` 里确实**无法真实回放**的旧 fixture，允许 `executor=simulation`，但必须显式声明
`input.provider_replay="simulated"` 与 `input.provider_replay_reason`，否则判不合法。可真实回放
的案例必须用 `recorded_provider` 执行器、引用 `fixtures/provider/`、把 fixture 通过 `$fixture`
喂进真实工具分发边界。

### 旧口径审计

`dataset/evals/cpq/legacy_wording_audit.json` 登记仓库现有 E2E 清单里的历史旧口径文本
（`historical_provenance`）：这些是历史故障复现 / 恢复清单原文，**原样保留不改写**。
未登记为历史文本的旧口径会被 `--validate` 判成错误（真正过期，必须修）。新数据集、新测试、
新文档一律采用五阶段 13 子步骤口径。

## 优先级标准（P0 / P1 / P2）

- **P0**：会导致越权、数据泄漏、项目不可见、核心角色无法工作、错项目回传、金额错误、
  重复落库、步骤倒退、历史数据丢失、部署直接不可用。生产层 P0 必须有真实入口 + 反例。
- **P1**：重要异常恢复、UI 协议、边界兼容、多角色 / 多步骤的组合路径。
- **P2**：文案、次要展示、低风险组合。

## ACL 业务口径（真实 `project_access`）

1. 项目 ACL 只负责三件事：**项目是否与当前用户相关**、**是否允许进入 / 读取**、
   **普通项目级写操作是否允许**。
2. `plan.finance_handoff`（`sent_at` 或 `task_id` 非空）存在时，**财务角色池成员**都与项目相关并可读，
   不要求具体 `cost_task_assignee` 参与者行。
3. 项目存在有效来源报价关联（`business_case.quote_session_id` 或 `source_task_id` 非空）时，
   **销售角色池成员**都与项目相关并可读，不要求具体 `quote_owner` 参与者行。
4. 专属业务写动作（`project_access.CONTRIBUTE_ROUTES`，21 条）只判「可见 + 未归档」，
   角色由**接口自己的 `_require`** 裁决；通用写 ACL **不得**提前返回
   「你的角色只能查看该项目，不能修改」。
5. 普通项目级写路由仍由通用写 ACL（`mode=write`）裁决：可见但写权不足 → 403，无关 → 404。
6. 无关用户读项目返回不泄露存在性的 404「项目不存在」；与项目相关但无此职责 → 接口专属 403。
7. 归档项目对非 owner 一律按不存在处理，关联也不解锁。

### 角色身份字段

案例身份分开保存真实映射链路，runner **不许凭用户名猜角色**：

```json
"identities": {"finance": {"username": "dave",
                           "sso_roles": ["finance_manager"],
                           "cpq_role_code": "finance_mgr",
                           "tech_role": "finance_manager"}}
```

`cpq_role_code` 走真实 `cpq_sso.to_tech_user`（生产唯一映射表），只有 `tech_role` 时造本地用户视图，
两者都缺直接报错。未知角色 code 必须落到 `viewer`，不能静默升级成高权限。

## 运行方式

```bash
python3 -m scripts.cpq_eval.runner --list
python3 -m scripts.cpq_eval.runner --validate
python3 -m scripts.cpq_eval.runner --layer deterministic
python3 -m scripts.cpq_eval.runner --layer recorded_provider
python3 -m scripts.cpq_eval.runner --executor production_http
python3 -m scripts.cpq_eval.runner --domain quote
python3 -m scripts.cpq_eval.runner --domain tech
python3 -m scripts.cpq_eval.runner --priority P0
python3 -m scripts.cpq_eval.runner --case <case-id>
python3 -m scripts.cpq_eval.runner --report <临时目录>
python3 -m scripts.cpq_eval.runner --snapshot-routes

python3 -m unittest \
  tests.test_cpq_eval_dataset_contract \
  tests.test_cpq_eval_runner \
  tests.test_cpq_eval_coverage \
  tests.test_cpq_eval_business_cases \
  tests.test_cpq_eval_production_backed \
  tests.test_cpq_eval_route_coverage -v
```

runner 会校验 schema、id 唯一、fixture / spec 引用、五阶段 13 子步骤与报价六步口径，输出
`passed / failed / skipped / invalid` 与按 domain、priority、layer、executor、stage、sub_step、
quote_step、role、failure_type、kind 的覆盖矩阵；失败返回非零退出码；报告默认写临时目录，
输出经过敏感信息脱敏（不含 token / API key / 密码 / 完整敏感附件）。

真实路由覆盖（读 / 写接口清单、未覆盖路由、写路由策略）由 `--snapshot-routes` 维护，
`tests/test_cpq_eval_route_coverage.py` 负责「新增路由漏测」守护。

## 三条硬约束

1. **不写仓库**：production 层的 `DATA_DIR` 指向进程级临时目录，项目元数据用真实
   `JsonMetaBackend` 但落在每个案例自己的子目录；仓库里的 `tech_data/`、`cpq_data/`
   与运行中的业务数据一概不读、不写、不删。
2. **不出网**：`CPQ_SSO=1` 打开真实鉴权链路，但 `cpq_sso._fetch`（唯一回调 8010 的出口）
   被替换成固定身份表；不请求真实模型、PDT 或任何生产服务。
3. **不复制生产判断**：谁可见 / 谁能写 / 谁能做这一步，一律调用生产模块自己的函数。

## 历史数据兼容

`fixtures/` 提供三组完整历史数据，由真实 `cpq_wf` / `workflow_projection` / `store` / `tasks`
执行（见 `cases/session_history/production_legacy_and_history.json` 与
`tests/test_cpq_eval_production_backed.py`）：

- `quote/legacy_quote_case.json`：报价已到第 4 步，后到的技术成本回传只合并快照，
  真实 `cpq_wf.advance_step_no` 保证 `current_step` 绝不退回第 2/3 步。
- `tech/legacy_tech_project.json`：历史数据带旧 `page_context`（`2.2 组装与整合`），
  经真实投影路由打开后仍是 **5 phases / 13 stages**，stage id 不变；**历史消息原文与旧
  `page_context` 原样保留，不做迁移、不改写**（历史原文只在 `input` / fixture 里出现，
  `expected` 只断言结构与顺序）。
- `handoff/interrupted_cross_agent_case.json`：报价已派发技术任务、服务重启后，
  真实 `tasks.recover_interrupted_tasks()` 把在途任务置为 `interrupted`（不伪装成功），
  重复恢复不新建重复项目 / 任务 / 会话卡。

## 已知遗留（legacy）

`docs/specs/tech-agent-recovery-22-e2e-scenarios.json` 仍残留 7 处旧阶段口径文案
（`2.2 组装与整合` / `2.3 成本测算` / `3.x 报告`）。该文件不在本数据集的允许修改范围内，
**保持原文不动**，由 `runner --validate` 作为只读提示打印；历史消息 fixture 的原文同样保留
并标记为 legacy，不做改写。

## CI 门禁与 mutation sentinel

`.gitlab-ci.yml` 里有四个独立 job，互相不覆盖对方的结论：

| job | 命令 | 覆盖 | 说明 |
| --- | --- | --- | --- |
| `cpq_eval_fast` | `python -m scripts.cpq_eval.ci_gates --gate fast` | simulation + production_unit | 纯离线 |
| `cpq_eval_production_http` | `--gate production_http` | 真实 FastAPI TestClient + 真鉴权 | 进程内，不访问部署环境 |
| `cpq_eval_recorded_provider` | `--gate recorded_provider` | 固定 fixture + 真实工具分发边界 | 不访问真实模型 / 真实 API Key |
| `cpq_eval_postgres` | `--gate postgres_integration --strict` | 隔离 PostgreSQL service | 13 条 integration 案例 + mutation sentinel |

`postgres_integration` 门禁的 `--strict`：缺隔离 PG 环境、案例**全部 skip**、或任一
mutation **survived**，job 都返回非零 —— 不允许拿「全部 skip」冒充通过。普通本地环境不传
`--strict` 时缺 PG 仍记 `skipped`（runner 单列，不算通过）。

### PostgreSQL mutation sentinel（真跑，不只注册）

`scripts/cpq_eval/pg_sentinel.py` 把 `pg_scenarios.MUTATIONS` 里的每个故障**真的注入一次**，
跑对应案例，输出 mutation 名称 / 对应案例 / 正常实现结果 / 注入后结果 / 是否 killed：

```
python3 -m scripts.cpq_eval.pg_sentinel                # 人读表格
python3 -m scripts.cpq_eval.pg_sentinel --require-all-killed   # CI：任一 survived 即非零
```

| mutation | 死亡它的案例 | 故障语义 |
| --- | --- | --- |
| `drop_handoff_index` | `pg.handoff.concurrent_same_key_single_row` | 删 `uq_wf_handoff_key` |
| `handoff_no_conflict` | `pg.handoff.concurrent_same_key_single_row` | 删 `ON CONFLICT DO NOTHING` |
| `drop_task_open_index` | `pg.task.db_rejects_duplicate_open` | 删 `uq_wf_task_open_kind` 部分唯一索引 |
| `blind_task_lookup` | `pg.task.supersede_on_signature_change` | 屏蔽任务复用查询 |
| `no_rollback_tx` | `pg.tx.midway_failure_rolls_back_all` | 出错仍 commit / 不回滚 |
| `autocommit_tx` | `pg.tx.midway_failure_rolls_back_all` | 用 autocommit 破坏事务 |
| `claim_without_open_guard` | `pg.claim.concurrent_two_claimers_one_winner` | 原子领取去掉 `status='open'` |
| `claim_ignore_eligibility` | `pg.claim.role_denied_no_side_effect` | 绕过领取资格判定，无权限也写副作用 |

### 触发范围

`workflow.rules` 目前只覆盖 **Merge Request** 与 **默认分支（master）**。按仓库约定
（AGENTS.md「CI 只允许在 Merge Request 和 master 上做测试/镜像构建验证」），
开发分支 `20260909` **不自动跑 CI** —— 这一点必须明确说明，不能假称开发分支已受 CI 保护。
