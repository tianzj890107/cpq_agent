# 技术工艺 Agent 能力恢复第 13 步：3.1 汇总报告 Agent 动作 Spec

## 范围

把 3.1「汇总工艺评估结果」补齐为可被统一左侧会话驱动。新增六项能力：

1. 获取汇总数据
2. 准备报告（读取当前报告草稿与送审就绪缺口）
3. 生成报告草稿
4. 更新报告字段
5. 保存发布范围
6. 提交审核

硬要求：

- **必须复用现有 `GET /summary`、`GET/PUT /process-report*` 实现**，不得新建第二套汇总或报告生成逻辑。
- 不新增任何 `@app.` 路由；既有 summary / process-report 路由一个都不能少。
- 每个写入动作完成后右侧 3.1 看板必须刷新（走看板桥），报告字段随 Agent 工具调用即时更新。

## 1. 新增平台工具（`tech_app/backend/services/oc_agent.py`）

| 工具名 | 语义 | 复用 |
| --- | --- | --- |
| `GetSummaryData` | 获取汇总数据 | `services/summary.py` 的 `aggregate`（与 `GET /summary` 同一实现） |
| `GetProcessReport` | 准备报告：读当前草稿 + 就绪缺口 | `store.load_process_report` + 既有送审前置检查（`_report_prerequisite_issues` / `_report_content_issues`） |
| `GenerateProcessReportDraft` | 生成报告草稿 | 既有 `POST /process-report/prepare` 同一实现 |
| `UpdateProcessReportFields` | 更新报告字段 | 既有 `PUT /process-report` 同一实现（服务端合并字段，保留单据号 / 编制人 / 审核与发布留痕 / 版本号） |
| `SaveProcessReportDistribution` | 保存发布范围 | 既有 `PUT /process-report/distribution` 同一实现 |
| `SubmitProcessReportReview` | 提交审核 | 既有 `POST /process-report/submit-review` 同一实现，**需 `confirmed`** |

约束：

- `UpdateProcessReportFields` 只接受白名单字段：`title` / `overview` / `highlights` / `risks` / `conclusion` / `distribution_scope` / `distribution_cc`；其余字段由服务端维护，Agent 不得改写。
- `SubmitProcessReportReview` 会把报告推进到 `in_review`，属人工授权动作：`confirmed !== true` 时只返回 `{"requires_confirmation": true, "action": "submit-review", ...}` 回执，**不得改状态**。
- 状态流转与审计必须**只有一份实现**：路由与平台工具调用同一个共享函数（抽到 `services/summary.py` 或新建 `services/report_workflow.py`），
  `oc_agent.py` 不得直写 `doc.status = "in_review"`、不得 `store.save_process_report(`、不得自带 `workflow:report_*` 审计 sentinel。
- `oc_agent.py` 不得出现 `summary_svc.recommend(`（3.1 不触发模型汇总；那是既有 async 路由的职责）。

## 2. UI 动作映射（`UI_ACTION_TOOLS`）

```
"GenerateProcessReportDraft":  "refresh-report"
"UpdateProcessReportFields":   "refresh-report"
"SaveProcessReportDistribution": "refresh-report"
"SubmitProcessReportReview":   "refresh-report"
```

- 四个写入动作都只映射成**刷新动作** `refresh-report`；提交审核不得映射成会在 `tool_use` 阶段就自动执行的动作名（如 `submit-report` / `approve-report`）。
- 只读工具（`GetSummaryData` / `GetProcessReport`）不需要 UI 动作。
- 既有 `parse` / `refresh-ir` / `refresh-requirement` / `refresh-cost-review` 等映射不变。

## 3. 看板侧（`tech_app/frontend/summary-result.js`）

在既有 `srRegisterTechBoardActions` 追加：

- `refreshProcessReport`：复用既有 `api('/process-report')` + `api('/summary')` 重新拉取并 `srRender()`；
- `generateProcessReportDraft`：复用既有保存路径 POST 既有 `/process-report/prepare`，随后刷新；
- `updateProcessReportFields`：复用既有 `srSave(false)`（PUT 既有 `/process-report`）；
- `saveProcessReportDistribution`：复用既有表单值 PUT 既有 `/process-report/distribution`；
- 保留 `saveProcessReport` / `submitProcessReportReview`。

长任务必须经 `TechBoardRuntime` 上报 `task-progress` / `task-completed` / `task-failed`；看板仍只调用既有 summary / process-report 接口。

## 4. 左侧（`tech_app/frontend/agent-chat.js`）

- 收到 `ui_action === "refresh-report"` 时，经 `TechBoardBridge.executeAction("refreshProcessReport", {...})` 让右侧 3.1 看板重新拉取并渲染。
- 失败要有可见提示（复用既有 `pushSystem` 写法），不得静默。
- 左侧不得出现 `/process-report` / `/summary` 字面量 —— 内容与调用都在右侧看板。

## 5. 闭环

1. 用户在 3.1 左侧说「汇总一下当前结果」→ Agent 调 `GetSummaryData` 总结各步结论；
2. 「生成报告草稿」→ `GenerateProcessReportDraft` → `refresh-report` → 右侧 3.1 出现草稿；
3. 「把结论改成…」→ `UpdateProcessReportFields` → 看板字段即时刷新；
4. 「发布范围写成…」→ `SaveProcessReportDistribution` → 看板刷新发布设置；
5. 用户明确同意后 `SubmitProcessReportReview(confirmed=true)` → 报告进入 `in_review`，看板刷新状态。

## 6. 边界

- 不新增 / 删除路由与既有工具；`storage`、数据模型不改；
- 未 `confirmed` 不得送审；不得改写单据号、编制人、审核 / 发布签名与来源快照；
- 父壳不新增 3.1 表单 DOM（内容始终在右侧看板 iframe 内）；
- 不改九个 stage id 与 URL，不动历史会话与项目数据。

## 7. 验收标准

1. 6 个新工具都在 `PLATFORM_TOOL_SCHEMAS` 且都被 `_run_platform_tool` 分派；
2. 只读工具引用 `summary` / `store.load_process_report` 既有数据源；`oc_agent.py` 无 `store.save_process_report(`、无 `doc.status = "in_review"`、无 `workflow:report_` sentinel、无 `summary_svc.recommend(`；
3. `SubmitProcessReportReview` 分支出现 `requires_confirmation` 与 `confirmed`；
4. `UI_ACTION_TOOLS` 新增 4 条 `refresh-report` 映射，且未把送审接成自动执行；
5. `summary-result.js` 注册 `refreshProcessReport` / `generateProcessReportDraft` / `updateProcessReportFields` / `saveProcessReportDistribution`，保留 `saveProcessReport` / `submitProcessReportReview`，并经 `TechBoardRuntime` 上报进度；
6. 左侧处理 `refresh-report` 并经桥触发，且不含 `/process-report`；
7. workflow 状态流转与审计实现唯一（`workflow:report_prepared` / `workflow:report_saved` / `workflow:report_submitted` 各只存在于一个后端文件且不是 `oc_agent.py`）；
8. 既有平台工具与全部 summary / process-report 路由不减少；
9. 父壳无 3.1 表单 DOM。

## 8. 对应测试

`tests/test_tech_summary_report_agent_red.py`
