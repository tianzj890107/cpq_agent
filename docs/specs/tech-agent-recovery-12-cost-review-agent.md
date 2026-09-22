# 技术工艺 Agent 能力恢复第 12 步：2.3 成本测算 Agent 动作 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_tech_cost_review_agent_red.py`

## 范围

把 2.3 成本测算从“只挂了上下文名称”补齐为可被统一左侧会话驱动的步骤。接通十项能力：

1. 读取成本复核状态
2. 获取零件成本
3. 测算单个零件
4. 测算组装成本
5. 逐件测算并汇总
6. 编辑成本（说明与核算批量）
7. 确认成本
8. 写入物料
9. 返回工艺
10. 发送报价

硬要求：

- **必须复用现有 `cost-review` 接口与 `services/cost_review.py`**，不得新建第二套成本算法
  （不得出现第二处 `cost_model.normalize` / `CostAnalysis(...)` / 自算四项成本）。
- 所有成本数字只在右侧 2.3 看板渲染；左侧只发语义化动作，不直接调 `/cost-review/*`。
- 不新增任何 `@app.` 路由；既有 cost-review 路由与既有平台工具一个都不能少。
- 对外动作（写入物料 / 发送报价 / 返回工艺）与状态变更（确认成本）都必须有显式确认门。

## 1. 新增平台工具（`tech_app/backend/services/oc_agent.py`）

在 `PLATFORM_TOOL_SCHEMAS` 追加 10 个工具并接入 `_run_platform_tool`：

| 工具名 | 语义 | 类型 / 复用 |
| --- | --- | --- |
| `GetCostReviewState` | 读取成本复核状态 | 只读；复用 `cost_review.load_review` + `summarize` |
| `ListCostReviewParts` | 获取零件成本 | 只读；复用 `cost_review.summarize` 的 `parts` / `counts` |
| `RunCostReviewPart` | 测算单个零件 | 只发请求 + `cost-step`，由看板调既有 `POST /cost-review/parts/{part_id}` |
| `RunCostReviewAssembly` | 测算组装成本 | 只发请求 + `cost-step`，由看板调既有 `POST /cost-review/assembly` |
| `RunCostReviewAll` | 逐件测算并汇总 | 只发请求 + `cost-step`，由看板调既有 `crRunAll()`（逐件 + 整机） |
| `UpdateCostReviewNote` | 编辑成本（说明 / 核算批量） | 复用既有 `PUT /cost-review` 的同一份实现 |
| `ConfirmCostReview` | 确认成本 | 复用既有 `POST /cost-review/confirm` 的同一份实现，**需 `confirmed`** |
| `WriteCostReviewMaterial` | 写入物料 | 复用既有 `POST /cost-review/material-write` 正文，**需 `confirmed`** |
| `ReturnCostReviewToProcess` | 返回工艺 | 复用既有 `POST /cost-review/return-to-process` 正文，**需 `confirmed`** |
| `SendCostReviewToQuote` | 发送报价 | 复用既有 `POST /cost-review/send-to-quote` 正文，**需 `confirmed`** |

约束：

- 测算类工具**不自己执行测算**：返回 `{"requested": true, "step": "part|assembly|all", ...}`
  回执，由前端经看板桥触发看板的既有动作；左侧与 Agent 都不得另起一套测算实现。
- `ConfirmCostReview` / `WriteCostReviewMaterial` / `ReturnCostReviewToProcess` /
  `SendCostReviewToQuote` 接收 `confirmed` 参数；`confirmed !== true` 时只返回
  `{"requires_confirmation": true, "action": ..., ...}` 回执，**不得触发任何写入或对外调用**。
- 「发送报价」除显式确认外，既有闸门不变：必须 `review.confirmed` 且报价必填参数齐全。
- 「返回工艺」沿用既有口径：不要求先确认成本（财务恰恰是“这个成本我不认”才退回）。
- 确认 / 写库 / 发报价 / 退回的状态流转与审计必须**只有一份实现**：
  路由与平台工具调用同一个共享函数，`oc_agent.py` 不得直写 `review.confirmed = True`、
  不得直接拼 bridge 调用、不得自带审计 sentinel。
- `oc_agent.py` 不得出现 `CostAnalysis(` / `cost_model.` / `cpq_bridge.` / `cost_review.run_part(`。

### 1.1 SSO token 注入（对外动作复用的前提）

写入物料 / 发送报价 / 返回工艺都要带用户的 SSO token 调 `cpq_bridge`。该注入链在
`fdfd6f1` 已就绪，本步**复用即可、不得另建一套**：

- `oc_agent.py` 已有 `_TOKEN: contextvars.ContextVar("oc_agent_token")` 与 `current_token()`；
- `stream_sse(project_id, message, actor="system", token="")` 已在 worker 内 `_TOKEN.set(...)`；
- `main.py` 的 `agent_send` 已接收 `request: Request` 并把 `_sso_token(request)` 传给 `stream_sse`；
- 对外工具用 `current_token()` 取 token，拿不到 token 时返回可读错误，不得静默失败。

## 2. UI 动作映射（`UI_ACTION_TOOLS`）

```
"RunCostReviewPart":          "cost-step"
"RunCostReviewAssembly":      "cost-step"
"RunCostReviewAll":           "cost-step"
"UpdateCostReviewNote":       "refresh-cost-review"
"ConfirmCostReview":          "refresh-cost-review"
"WriteCostReviewMaterial":    "refresh-cost-review"
"ReturnCostReviewToProcess":  "refresh-cost-review"
"SendCostReviewToQuote":      "refresh-cost-review"
```

- 测算类统一映射 `cost-step`（`tool_use` 事件仅带 `ui_action`，具体 `step` / `part_id` /
  `quantity` 由前端从 `event.input` 读取后交给看板）。
- 确认与三个对外动作只映射成刷新动作，**不得**映射成会在 `tool_use` 阶段就自动执行的
  动作名（如 `write-material` / `send-cost-to-quote` / `return-to-process`），
  否则父壳会抢在确认门之前执行。
- 既有 `parse` / `refresh-ir` / `refresh-integration` / `integration-step` 等映射不变。

## 3. 看板侧（`tech_app/frontend/cost-review.js`）

在既有 `crRegisterTechBoardActions` 的 `TechBoardRuntime.registerActions({...})` 追加：

- `refreshCostReview`：复用既有 `api(crUrl(''))` 重新拉取并 `crRender()`；
- `costStep`：接收 `{step, part_id, quantity}`，分别复用既有 `crRunPart(...)` /
  `crRunAssembly()` / `crRunAll()`；
- `writeCostReviewMaterial` / `sendCostReviewToQuote` / `returnCostReviewToProcess`：
  复用既有 `crRunOp('material-write'|'send-to-quote'|'return-to-process')`；
- 保留 `runCostReview` / `confirmCostReview` 与 `parts` / `assembly` / `total` / `params` 视图。

长任务（逐件测算 / 整机成本）必须经 `TechBoardRuntime` 上报
`task-progress` / `task-completed` / `task-failed`（父壳据此在左侧显示进度），
不能只在 iframe 内发 `window` 事件。看板仍只调用既有 `/cost-review/*` 接口。

## 4. 左侧（`tech_app/frontend/agent-chat.js`）

- 收到 `ui_action === "cost-step"` 时，读 `event.input.step` / `part_id` / `quantity`，
  经 `TechBoardBridge.executeAction("costStep", {...})` 让右侧看板跑既有测算；
- 收到 `ui_action === "refresh-cost-review"` 时，经
  `TechBoardBridge.executeAction("refreshCostReview", {...})` 重新拉取并渲染；
- 失败要有可见提示（复用既有 `pushSystem` 写法），不得静默；
- 左侧不得出现 `/cost-review` 字面量 —— 内容与调用都在右侧看板。

## 5. 闭环

1. 用户在 2.3 左侧说「逐件测算成本」→ Agent 调 `RunCostReviewAll`；
2. 左侧收到 `cost-step` → 经桥让右侧看板跑既有逐件 + 整机测算；
3. 看板上报进度，完成后自动刷新；左侧用 `GetCostReviewState` / `ListCostReviewParts` 总结；
4. 「把说明改成…」→ `UpdateCostReviewNote` → `refresh-cost-review` → 看板刷新；
5. 用户明确同意后 `ConfirmCostReview(confirmed=true)` → 看板刷新为已确认；
6. 明确同意后 `SendCostReviewToQuote(confirmed=true)` → 复用既有正文发报价 → 看板刷新去向留痕。

## 6. 边界

- 不新增、删除任何路由与既有工具；`storage`、数据模型不改；
- 未 `confirmed` 绝不写库、不发送报价、不退回；
- 父壳不新增 2.3 表单 DOM（内容始终在右侧看板 iframe 内）；
- 不改九个 stage id 与 URL，不动历史会话与项目数据，不重建第二套成本 / 报价逻辑。

## 7. 验收标准

1. 10 个新工具都在 `PLATFORM_TOOL_SCHEMAS` 且都被 `_run_platform_tool` 分派；
2. 只读工具引用 `cost_review` 服务；`oc_agent.py` 无 `cost_model.` / `CostAnalysis(` /
   `cpq_bridge.` / `cost_review.run_part(`；
3. 4 个确认门工具的分支里都出现 `requires_confirmation` 与 `confirmed`；
4. `UI_ACTION_TOOLS` 新增 8 条映射且对外动作未被映射成自动执行动作；
5. SSO token 注入链完整：`agent_send` 传 `_sso_token(request)` → `stream_sse(token=...)`
   → `_SSO_TOKEN`；
6. 看板注册 `refreshCostReview` / `costStep` / 三个去向动作并保留既有动作与视图，
   经 `TechBoardRuntime` 上报进度；
7. 左侧处理 `cost-step` / `refresh-cost-review` 并经桥触发，且不含 `/cost-review`；
8. `cost_review_confirm` / `cost_review_material_write` / `cost_review_send_to_quote`
   各只存在于一个后端文件且不是 `oc_agent.py`；
9. 既有平台工具与全部 cost-review 路由不减少；
10. 父壳无 2.3 表单 DOM。

## 8. 对应测试

`tests/test_tech_cost_review_agent_red.py`
