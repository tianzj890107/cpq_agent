# 技术工艺 Agent 能力恢复第 10 步：1.3 审核需求 Agent 动作 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_tech_requirement_review_red.py`

## 范围

只打通 **1.3 审核需求**：Agent 读取审核材料、生成审核摘要、把审核意见带入看板，
并在**用户明确确认后**审核通过或审核退回；右侧同步显示审核状态与流程留痕。

只新增 Agent 工具与前端接线，**不新增 HTTP 路由、不新增第二套审核流转逻辑**。

## 1. 新增平台工具（`tech_app/backend/services/oc_agent.py`）

在 `PLATFORM_TOOL_SCHEMAS` 追加 5 个工具，并在 `_run_platform_tool` 里分派。
每个工具必须复用既有实现，不得新写一套：

| 工具名 | 语义 | 复用 |
| --- | --- | --- |
| `GetRequirementReviewMaterials` | 读取审核材料 | 既有 `store.load_requirement` / `store.load_meta` / `store.load_attachments` + 同一份确定性预检 + 历史留痕，不调模型 |
| `GetRequirementReviewSummary` | 生成审核摘要 | 由同一份预检结果 + 需求关键字段做**确定性**汇总（`summary` / `items` / `generated_note` / `decision_options`），不调模型、不编造结论 |
| `SaveRequirementReviewNote` | 保存审核意见 | 只发请求（与 `RequestParse` 同模式）：回报 `{decision, note}` 由看板写入既有 `#reviewText` 与审核结果单选；真正落盘由既有审核动作携带完成 |
| `ApproveRequirementReview` | 审核通过 | 既有 `POST /requirement/review`（`decision="approve"`）同一份流转实现 |
| `RejectRequirementReview` | 审核退回 | 既有 `POST /requirement/review`（`decision="reject"`）同一份流转实现 |

约束：

- **必须显式确认**：`ApproveRequirementReview` / `RejectRequirementReview` 必须接收
  `confirmed` 参数；`confirmed !== true` 时只返回
  `{"requires_confirmation": true, ...}` 回执、**不得改动任何状态**；只有
  `confirmed === true` 才执行流转。Agent 不得代替人工审批。
- 审核必须复用同一份实现：把 `/requirement/review` 的状态流转抽到 service 层
  （优先放进第 8 步已建的 `services/requirement_service.py`，与第 9 步
  confirm / return 共处一处），路由与平台工具都调用它；
  `oc_agent.py` 不得自带第二份审核实现，也不得直接写
  `doc.status = "approved"` 之类的状态变更。
- 权限与审计沿用既有：仅 `DIRECTOR_ROLES` 可审核，写操作落既有审计
  （`workflow:requirement_approve` / `workflow:requirement_reject` 所在路径）。
- 不新增任何 `@app.` 路由；`/requirement/review` 必须保留。

## 2. UI 动作映射

`UI_ACTION_TOOLS` 追加：

```
"SaveRequirementReviewNote":   "fill-review-note"
"ApproveRequirementReview":    "refresh-requirement"
"RejectRequirementReview":     "refresh-requirement"
```

关键：**不得**把 `ApproveRequirementReview` / `RejectRequirementReview` 映射成会在
tool_use 阶段就自动执行的动作（如 `approve-requirement` / `reject-requirement`），
否则父壳会在工具跑之前绕过确认门直接审批。既有动作名保持不变。

## 3. 看板侧（`tech_app/frontend/requirement-review-page.js`）

- 在既有 `rrRegisterTechBoardActions` 的 `TechBoardRuntime.registerActions({...})`
  追加 `applyReviewNote`：把父壳传来的 `{decision, note}` 写入既有审核结果单选与
  `#reviewText`（复用 `rrBind` 里单选联动的既有写法）；保留 `submitRequirementReview`；
- 右侧必须显示审核状态与流程留痕：沿用 `workflow.js` 的 `renderHistory` 渲染
  `rrRequirement.history`（含 `reviewed_by` / `reviewed_at` / `review_note`）；
- 提交审核时经 `TechBoardRuntime` 上报
  `task-progress` / `task-completed` / `task-failed`，不能只在 iframe 内发 `window` 事件；
- 仍然只调用既有 `/requirement/review`，不新增接口、不复制流转逻辑。

## 4. 左侧会话（`tech_app/frontend/agent-chat.js`）

- 收到 `ui_action === "fill-review-note"` 时，经
  `TechBoardBridge.executeAction("applyReviewNote", { decision, note })` 把审核意见带入看板，
  不自己写接口；
- 能把审阅类工具结果渲染成「审核摘要」：使用 `review_materials`（审核材料清单）
  与 `review_summary`（确定性摘要 + 待补充项）；
- 收到带 `requires_confirmation` 的工具结果时，给出「需人工明确确认后才能通过 / 退回」
  的提示，不得静默通过；
- 左侧不得直接调用 `/requirement/review`。

## 5. 闭环

1. 用户在 1.3 左侧问「这份需求能不能过审」；
2. Agent 调 `GetRequirementReviewMaterials` / `GetRequirementReviewSummary`；
3. 左侧渲染审核材料与确定性摘要（`review_materials` / `review_summary`）；
4. Agent 调 `SaveRequirementReviewNote`，右侧审核结果与 `#reviewText` 被带入；
5. 用户明确说「审核通过」/「退回」后，Agent 才以 `confirmed: true` 调
   `ApproveRequirementReview` / `RejectRequirementReview`；
6. 右侧刷新审核状态与流程留痕，左侧保留完整工具轨迹。

## 6. 边界

- 不新增/删除路由与既有工具；`storage`、数据模型不改；
- Agent 不得代替人工审批：无 `confirmed: true` 绝不改状态，不越过角色权限；
- 父壳不新增审核表单 DOM（表单始终在右侧看板 iframe 内）；
- 不改九个 stage id 与 URL，不动历史会话与项目数据。

## 7. 验收标准

1. 5 个新工具都在 `PLATFORM_TOOL_SCHEMAS` 且都被 `_run_platform_tool` 分派；
2. `ApproveRequirementReview` / `RejectRequirementReview` 有显式确认门
   （`requires_confirmation` + `confirmed`），未确认不改状态；
3. 审核流转实现唯一：`当前需求不在待审核状态` 只出现在一个后端文件里，
   且 `oc_agent.py` 不含 `doc.status = "approved"` 之类的状态变更；既有审核路由仍在；
4. `UI_ACTION_TOOLS` 出现 `fill-review-note`，且审批动作没有被映射成自动审批动作；
5. 看板注册 `applyReviewNote`、渲染 `renderHistory`/`history` 留痕，并经
   `TechBoardRuntime` 上报审核进度；
6. 左侧由 `fill-review-note` 触发看板动作，并能渲染 `review_materials` / `review_summary`
   与 `requires_confirmation` 提示；
7. `requirement-review-page.js` 仍只调用既有审核接口，`agent-chat.js` 不直接调用该接口；
8. 既有平台工具与全部既有路由不减少。

## 8. 对应测试

`tests/test_tech_requirement_review_red.py`
