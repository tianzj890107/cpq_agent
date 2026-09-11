# 技术工艺 Agent 能力恢复第 14 步：3.2 报告审核 Agent 动作 Spec

## 范围

把 3.2「审核工艺评估报告」补齐为可被统一左侧会话驱动。新增五项能力：

1. 读取报告及版本
2. 输出审核摘要
3. 保存审核意见
4. 审核通过
5. 退回汇总

硬要求：

- **必须复用现有 `POST /process-report/review`、`GET /process-report/versions*` 实现**，不得新建第二套审核判定逻辑。
- 所有审批操作保留既有权限校验（`DIRECTOR_ROLES`）与审计记录；Agent 不得代替人工审批。
- 审核状态与留痕必须在右侧 3.2 看板同步显示。

## 1. 新增平台工具（`tech_app/backend/services/oc_agent.py`）

| 工具名 | 语义 | 复用 |
| --- | --- | --- |
| `GetProcessReportReview` | 读取报告及版本 | `store.load_process_report` + `store.list_process_report_versions`（与 `GET /process-report/versions` 同一实现） |
| `GetReportReviewSummary` | 输出审核摘要 | 既有送审 / 内容 / 来源快照三项检查（`_report_prerequisite_issues` / `_report_content_issues` / `_report_source_is_current`）+ 报告与汇总数据 |
| `SaveReportReviewNote` | 保存审核意见 | 只回执不落盘：返回 `decision` / `note` / `note_target`，由看板带入表单 |
| `ApproveProcessReport` | 审核通过 | 既有 `POST /process-report/review`（decision=approve）同一实现，**需 `confirmed`** |
| `RejectProcessReport` | 退回汇总 | 既有 `POST /process-report/review`（decision=reject）同一实现，**需 `confirmed`** |

约束：

- `ApproveProcessReport` / `RejectProcessReport` 接收 `confirmed` 参数；`confirmed !== true` 时只回
  `{"requires_confirmation": true, "action": "approve"|"reject", ...}` 回执，**不得改状态**。审核通过还必须满足既有闸门：
  `status === "in_review"`、内容检查通过、`_report_source_is_current` 为真；退回不要求内容检查。
- 权限不变：审核仍是 `DIRECTOR_ROLES`。Agent 工具调用者由 `_actor_user()` 回查角色，服务端把门，不靠前端。
- 审核正文与审计只有一份实现（抽到 service 层，路由与工具共用）；
  `oc_agent.py` 不得直写 `doc.status = "approved" / "rejected"`、不得 `store.save_process_report(`、不得自带 `workflow:report_*` sentinel。
- `SaveReportReviewNote` 不得触发任何状态变更；只允许 `decision ∈ {approve, reject, ""}`。

## 2. UI 动作映射（`UI_ACTION_TOOLS`）

```
"SaveReportReviewNote":   "fill-report-review-note"
"ApproveProcessReport":   "refresh-report"
"RejectProcessReport":    "refresh-report"
```

- 审核意见只回填看板（不落盘）；通过 / 退回只映射刷新，**不得**映射成会在 `tool_use` 阶段自动审批的动作名
  （如 `approve-report` / `reject-report` / `review-report`）。
- 既有映射不变。

## 3. 看板侧（`tech_app/frontend/report-review-result.js`）

在既有 `rrRegisterTechBoardActions` 追加：

- `refreshProcessReport`：复用既有 `api('/process-report')` 重新拉取并 `rrRender()`；
- `applyReportReviewNote`：接收 `{decision, note}`，复用既有 `rrReviewBody(decision)` 路径把意见带入审核说明（不提交、不改状态）；
- 保留 `approveProcessReport` / `rejectProcessReport` 与既有发布范围维护（`PUT /process-report/distribution`）。

长任务必须经 `TechBoardRuntime` 上报 `task-progress` / `task-completed` / `task-failed`；看板仍只调用既有 process-report 接口。

## 4. 左侧（`tech_app/frontend/agent-chat.js`）

- `ui_action === "fill-report-review-note"` → `TechBoardBridge.executeAction("applyReportReviewNote", {decision, note})`；
- `ui_action === "refresh-report"` → `TechBoardBridge.executeAction("refreshProcessReport", {})`；
- 失败要有可见提示，不得静默；左侧不得出现 `/process-report` 字面量。

## 5. 闭环

1. 用户在 3.2 左侧说「这个报告能不能通过」→ Agent 调 `GetProcessReportReview` + `GetReportReviewSummary`，给出依据与缺口；
2. 「审核意见写：…」→ `SaveReportReviewNote` → 左侧 `fill-report-review-note` → 看板带入审核说明；
3. 用户明确同意后 `ApproveProcessReport(confirmed=true)` → 报告 `approved`，看板刷新审核状态与留痕；
4. 或 `RejectProcessReport(confirmed=true)` → 报告 `rejected`，看板刷新为已退回。

## 6. 边界

- 不新增 / 删除路由与既有工具；`storage`、数据模型不改；
- 未 `confirmed` 绝不通过 / 退回；不得绕过 `in_review`、内容检查与来源快照校验；
- 父壳不新增 3.2 表单 DOM；
- 不改九个 stage id 与 URL，不动历史会话与项目数据。

## 7. 验收标准

1. 5 个新工具都在 `PLATFORM_TOOL_SCHEMAS` 且都被 `_run_platform_tool` 分派；
2. `ApproveProcessReport` / `RejectProcessReport` 分支都有 `requires_confirmation` 与 `confirmed`；
3. `SaveReportReviewNote` 分支不触发状态变更（无 `requires_confirmation`、无 save/audit）；
4. `UI_ACTION_TOOLS` 新增 3 条映射，且通过 / 退回未接成自动审批；
5. 审核判定 / 状态流转 / 审计实现唯一（审核闸门文案 `报告送审后上游工艺数据已变化，请驳回并重新汇总后送审` 只存在于一个后端文件且不是 `oc_agent.py`），`oc_agent.py` 无状态直写；
6. 既有路由仍保留 `DIRECTOR_ROLES` 权限校验与 `workflow:report_*` 审计；
7. 看板注册 `refreshProcessReport` / `applyReportReviewNote`，保留 `approveProcessReport` / `rejectProcessReport`，并经 `TechBoardRuntime` 上报进度；
8. 左侧处理 `fill-report-review-note` / `refresh-report` 并经桥触发，且不含 `/process-report`；
9. 既有平台工具与 process-report 路由不减少；父壳无 3.2 表单 DOM。

## 8. 对应测试

`tests/test_tech_report_review_agent_red.py`
