# 技术工艺 Agent 能力恢复第 15 步：3.3 发布并回传报价 Agent 动作 Spec

## 范围

把 3.3「正式发布报告」补齐为可被统一左侧会话驱动。新增七项能力：

1. 读取发布状态
2. 读取发布对象
3. 更新发布范围
4. 正式发布
5. 回传报价
6. 新建报告版本
7. 获取发布结果

硬要求：

- **必须复用现有 `POST /process-report/publish`、`PUT /process-report/distribution`、
  `GET /process-report/versions*`、`POST /process-report/new-version` 实现**，不得新建第二套发布或版本逻辑。
- **Agent 不能绕过审核状态直接发布**：`publish` 必须 `status === "approved"`、发布对象非空、内容与来源快照校验通过；
  `回传报价` 必须 `status === "published"`。
- 不新增任何 `@app.` 路由；既有 process-report 路由一个都不能少。
- 所有审批 / 对外操作保留权限校验（`DIRECTOR_ROLES`）与审计记录。

## 1. 新增平台工具（`tech_app/backend/services/oc_agent.py`）

| 工具名 | 语义 | 复用 |
| --- | --- | --- |
| `GetReportPublishState` | 读取发布状态 | `store.load_process_report` + 既有发布前置检查（`_report_prerequisite_issues` / `_report_content_issues` / `_report_source_is_current`） |
| `ListReportPublishRecipients` | 读取发布对象 | 报告 `recipients` / `distribution_scope` / `distribution_cc` |
| `UpdateReportDistribution` | 更新发布范围 | 既有 `PUT /process-report/distribution` 同一实现 |
| `PublishProcessReport` | 正式发布 | 既有 `POST /process-report/publish` 同一实现，**需 `confirmed`** |
| `SendReportToQuote` | 回传报价 | 既有 CPQ 回传正文（`_integration_do_send_to_quote` / `cpq_bridge.send_to_quote`）同一实现，**需 `confirmed`** |
| `CreateReportNewVersion` | 新建报告版本 | 既有 `POST /process-report/new-version` 同一实现，**需 `confirmed`** |
| `GetReportPublishResult` | 获取发布结果 | `store.load_process_report` + `store.list_process_report_versions` + 整机回传结果（`integration.load_plan` 的 `quote_handoff`） |

约束：

- `PublishProcessReport` / `SendReportToQuote` / `CreateReportNewVersion` 接收 `confirmed` 参数；
  `confirmed !== true` 时只回 `{"requires_confirmation": true, "action": "publish"|"send-to-quote"|"new-version", ...}` 回执，
  **不得发布、不得外呼、不得新建版本**。
- 发布闸门不可放宽：`status === "approved"`、`_report_source_is_current` 为真、内容检查通过、`recipients` 非空。
  回传报价闸门：`status === "published"`，复用既有回传正文（不要求退回重审，也不得跳过发布）。
- 发布 / 回传 / 新版本的状态变更、审计与桥接调用只有一份实现（抽到 service 层，路由与工具共用）；
  `oc_agent.py` 不得直写 `doc.status = "published"`、不得 `store.save_process_report(`、
  不得直接 `cpq_bridge.send_to_quote(`、不得自带 `workflow:report_*` sentinel。
- 回传报价使用本步已有的 SSO 令牌注入链（`current_token()`），不另建。

## 2. UI 动作映射（`UI_ACTION_TOOLS`）

```
"UpdateReportDistribution":   "refresh-report"
"PublishProcessReport":       "refresh-report"
"SendReportToQuote":          "refresh-report"
"CreateReportNewVersion":     "refresh-report"
```

- 四个写入动作都只映射成刷新动作 `refresh-report`；发布 / 回传 / 新版本**不得**映射成会在
  `tool_use` 阶段自动执行的动作名（如 `publish-report` / `send-report-to-quote` / `new-report-version`）。
- 只读工具（`GetReportPublishState` / `ListReportPublishRecipients` / `GetReportPublishResult`）不需要 UI 动作。
- 既有映射不变。

## 3. 看板侧（`tech_app/frontend/report-publish-result.js`）

在既有 `rpRegisterTechBoardActions` 追加：

- `refreshProcessReport`：复用既有 `api('/process-report')` 重新拉取并 `rpRender()`；
- `sendReportToQuote`：复用既有回传正文（POST 既有 `/integration/send-to-quote`，不新增路由）；
- `createReportNewVersion`：复用既有 `rpPrimaryAction()` 的「新建报告」分支（POST 既有 `/process-report/new-version`）；
- 保留 `publishProcessReport`。

长任务必须经 `TechBoardRuntime` 上报 `task-progress` / `task-completed` / `task-failed`；看板仍只调用既有 process-report / integration 接口。

## 4. 左侧（`tech_app/frontend/agent-chat.js`）

- 收到 `ui_action === "refresh-report"` 时，经 `TechBoardBridge.executeAction("refreshProcessReport", {...})` 让右侧 3.3 看板重新拉取并渲染。
- 失败要有可见提示，不得静默；左侧不得出现 `/process-report` / `/integration` 字面量。

## 5. 闭环

1. 用户在 3.3 左侧说「现在能发布了吗」→ Agent 调 `GetReportPublishState`，指出未满足的发布条件；
2. 「发布范围加上质量部」→ `UpdateReportDistribution` → `refresh-report` → 看板发布范围即时更新；
3. 用户明确同意后 `PublishProcessReport(confirmed=true)` → 报告 `published`，看板刷新发布状态与留痕；
4. 「回传报价」→ 明确同意后 `SendReportToQuote(confirmed=true)` → 复用既有回传正文 → 看板刷新发布结果；
5. 「基于这版做个新版本」→ 明确同意后 `CreateReportNewVersion(confirmed=true)` → 新草稿版本，看板刷新。

## 6. 边界

- 不新增 / 删除路由与既有工具；`storage`、数据模型不改；
- 未 `confirmed` 不得发布 / 回传 / 新建版本；不得绕过 `approved` / `published` 状态闸门；
- 父壳不新增 3.3 表单 DOM；
- 不改九个 stage id 与 URL，不动历史会话与项目数据，不重建第二套回传 / 桥接逻辑。

## 7. 验收标准

1. 7 个新工具都在 `PLATFORM_TOOL_SCHEMAS` 且都被 `_run_platform_tool` 分派；
2. 3 个确认门工具分支都有 `requires_confirmation` 与 `confirmed`；
3. `PublishProcessReport` 分支保留 `approved` 状态闸门；`SendReportToQuote` 分支保留 `published` 状态闸门；
4. `UI_ACTION_TOOLS` 新增 4 条 `refresh-report` 映射，且发布 / 回传 / 新版本未接成自动执行；
5. 发布 / 回传 / 新版本 / 发布范围实现唯一（`workflow:report_published`、`workflow:report_new_version`、`workflow:report_distribution_updated`、`报告须审核通过后才能发布`、`仅已发布报告可创建新版本` 各只存在于一个后端文件且不是 `oc_agent.py`），`oc_agent.py` 无状态直写、无直接桥接调用；
6. 看板注册 `refreshProcessReport` / `sendReportToQuote` / `createReportNewVersion`，保留 `publishProcessReport`，并经 `TechBoardRuntime` 上报进度；
7. 左侧处理 `refresh-report` 并经桥触发，且不含 `/process-report` / `/integration`；
8. 既有平台工具与全部 process-report 路由不减少；父壳无 3.3 表单 DOM。

## 8. 对应测试

`tests/test_tech_report_publish_agent_red.py`
