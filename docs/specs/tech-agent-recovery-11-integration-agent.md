# 技术工艺 Agent 能力恢复第 11 步：2.2 组装与整合 Agent 动作 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_tech_integration_agent_red.py`

## 范围

1. **先修既有缺口**：`UI_ACTION_TOOLS` 早已把 2.2 的 `refresh-integration` /
   `integration-step` 发给父壳，但统一左侧会话 `agent-chat.js` 从未处理这两个事件，
   导致 Agent 改完参数/工序后右侧 2.2 看板不刷新、请求重跑环节也不执行。
2. **再接通九项能力**：整合图纸上传、参数推荐、参数修改、组装工艺生成、工序修改、
   成本生成、参数确认、工序确认、发送财务。
3. **硬要求**：所有参数、工序、成本必须落到右侧看板刷新（走看板桥），
   不能只在聊天工具结果里出现。

不新增 HTTP 路由、不新增第二套生成或流转实现。

## 1. 修复：左侧处理 2.2 的两个既有动作（`tech_app/frontend/agent-chat.js`）

- 收到 `ui_action === "refresh-integration"` 时，经
  `TechBoardBridge.executeAction("refreshIntegration", {...})` 让右侧 2.2 看板
  重新拉取并渲染整合结果（复用看板既有 `aiStart()` 读取路径），**不**只在聊天里回一句。
- 收到 `ui_action === "integration-step"` 时，读 `event.input.step`
  （`params` / `process` / `cost`），经桥
  `TechBoardBridge.executeAction("integrationStep", { step })`，由看板调用既有
  `aiGenerate(step)` 真正跑流水线；左侧不自己调生成接口。
- 收到 `ui_action === "open-integration-drawings"` 时，经桥
  `TechBoardBridge.executeAction("openIntegrationDrawings", {})` 让看板切到「整合图纸」
  页签并聚焦上传入口（Agent 不能代替用户上传二进制文件）。
- 失败要有可见提示（复用既有 `pushSystem` 写法），不得静默。

## 2. 新增平台工具（`tech_app/backend/services/oc_agent.py`）

在 `PLATFORM_TOOL_SCHEMAS` 追加 4 个工具并接入 `_run_platform_tool`：

| 工具名 | 语义 | 复用 |
| --- | --- | --- |
| `UploadIntegrationDrawing` | 整合图纸上传（只发请求） | 与 `RequestParse` 同模式：回执请用户在看板上传，不接收二进制、不落盘 |
| `ConfirmIntegrationParams` | 参数确认 | 既有 `POST /integration/params/confirm` 同一份实现 |
| `ConfirmIntegrationProcess` | 工序确认 | 既有 `POST /integration/process/confirm` 同一份实现 |
| `SendIntegrationToFinance` | 发送财务 | 既有 `POST /integration/send-to-finance` 同一份实现 |

约束：

- **发送财务必须显式确认**：`SendIntegrationToFinance` 接收 `confirmed` 参数，
  `confirmed !== true` 时只返回 `{"requires_confirmation": true, ...}` 回执、
  **不得触发任何对外调用**；只有 `confirmed === true` 才执行。
  同时既有闸门不变：`params_confirmed` 与 `process_confirmed` 都必须为真才能发送。
- 参数推荐 / 组装工艺生成 / 成本生成继续复用既有 `RequestIntegrationStep`（step =
  `params` / `process` / `cost`），**不新增重复工具**；参数修改 / 工序修改继续复用
  `UpdateIntegrationParams` / `UpdateIntegrationProcess`。
- 确认与发送必须复用同一份实现：把 `/integration/params/confirm`、
  `/integration/process/confirm`、`/integration/send-to-finance` 的状态变更抽到
  service 层（如 `services/integration.py` 或既有服务），路由与平台工具都调用它；
  `oc_agent.py` 不得直写 `plan.params_confirmed = True` / `plan.process_confirmed = True`。
- 不新增任何 `@app.` 路由；既有 integration 路由必须保留。

## 3. UI 动作映射（`UI_ACTION_TOOLS`）

追加：

```
"UploadIntegrationDrawing":     "open-integration-drawings"
"ConfirmIntegrationParams":     "refresh-integration"
"ConfirmIntegrationProcess":    "refresh-integration"
"SendIntegrationToFinance":     "refresh-integration"
```

既有 `UpdateIntegrationParams` / `UpdateIntegrationProcess` → `refresh-integration`、
`RequestIntegrationStep` → `integration-step` 保持不变。发送财务**不得**映射成会在
tool_use 阶段就自动执行的动作。

## 4. 看板侧（`tech_app/frontend/assembly-integration.js`）

- 在既有 `aiRegisterTechBoardActions` 的 `TechBoardRuntime.registerActions({...})`
  追加：
  - `refreshIntegration`：复用既有 `aiStart()` / `api(aiUrl(''))` 重新拉取并 `aiRender()`；
  - `integrationStep`：接收 `{step}`，复用既有 `aiGenerate(step)`；
  - `openIntegrationDrawings`：把页签切到 `drawings` 并聚焦上传入口（复用既有 `aiSetTab`）；
  - 保留 `runIntegration` / `sendIntegrationToFinance` 与三个视图。
- 长任务（生成 params / process / cost）必须经 `TechBoardRuntime` 上报
  `task-progress` / `task-completed` / `task-failed`，不能只在 iframe 内发 `window` 事件。
- 仍然只调用既有 `/integration/*` 接口，不新增接口、不复制生成逻辑。

## 5. 闭环

1. 用户在 2.2 左侧说「重新推荐参数」；
2. Agent 调 `RequestIntegrationStep(step=params)`；
3. 左侧收到 `integration-step` → 经桥让右侧看板跑既有参数推荐流水线；
4. 看板上报进度并在「参数推荐」页签渲染结果（不是只在聊天里贴一份）；
5. Agent 用 `UpdateIntegrationParams` 改值 → 左侧 `refresh-integration` → 看板刷新；
6. 参数与工序分别 `ConfirmIntegrationParams` / `ConfirmIntegrationProcess` 确认，
   每次确认后看板刷新确认状态；
7. 用户明确同意后 `SendIntegrationToFinance(confirmed=true)`，看板刷新到「已发送财务」。

## 6. 边界

- 不新增/删除路由与既有工具；`storage`、数据模型不改；
- 发送财务无 `confirmed: true` 绝不触发对外调用；
- Agent 不接收、不转存二进制附件，上传仍由用户在看板完成；
- 父壳不新增 2.2 表单 DOM（内容始终在右侧看板 iframe 内）；
- 不改九个 stage id 与 URL，不动历史会话与项目数据。

## 7. 验收标准

1. 4 个新工具都在 `PLATFORM_TOOL_SCHEMAS` 且都被 `_run_platform_tool` 分派；
2. `SendIntegrationToFinance` 有显式确认门（`requires_confirmation` + `confirmed`）；
3. `integration_params_confirm` / `integration_process_confirm` 审计各只出现在一个后端
   文件里且不是 `oc_agent.py`，且 `oc_agent.py` 不含 `plan.params_confirmed = True`
   / `plan.process_confirmed = True` 这类直写；
4. `UI_ACTION_TOOLS` 出现 `open-integration-drawings`，发送财务未被映射成自动执行动作；
5. 左侧 `agent-chat.js` 处理 `refresh-integration` / `integration-step` /
   `open-integration-drawings` 并经看板桥触发（不再只停留在聊天）；
6. 看板注册 `refreshIntegration` / `integrationStep` / `openIntegrationDrawings`，
   保留 `runIntegration` / `sendIntegrationToFinance`，并经 `TechBoardRuntime` 上报进度；
7. 2.2 的生成 / 修改 / 确认接口只在看板侧调用，左侧不直接调 `/integration/*`；
8. 既有平台工具与全部既有路由不减少。

## 8. 对应测试

`tests/test_tech_integration_agent_red.py`
