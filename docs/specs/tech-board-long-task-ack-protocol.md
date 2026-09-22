# Spec: 看板长任务"提交即回执 + 事件驱动完成"

状态：Spec + 红测（已实现）
红测：`tests/test_tech_board_bridge_protocol_red.py`
适用分支：`20260909`
起因：统一父壳里点「开始解析」等长任务按钮，20 秒后横幅出现「开始解析超时未响应」。

## 1. 问题陈述

父壳与右侧看板用 postMessage 命令/回执通信，命令有 20 秒默认超时
（`tech_app/frontend/tech-board-bridge.js:22` `DEFAULT_TIMEOUT = 20000`，
`executeAction()` 不发自定义 timeout，见 `:236`）。

但右侧看板把长任务动作注册成"等整份任务跑完才回执"：

- `tech_app/frontend/app.js:1943` `parseDrawing.run` → `await parseDrawing()` → `runTask` →
  `tech_app/frontend/app.js:1104` `pollTask()` 一直轮询到 `succeeded/failed`，自身无超时；
- `tech_app/frontend/assembly-integration.js:1271` `runIntegration` → `await aiRunAll()`，
  `:1317` `integrationStep` → `await aiGenerate()`；
- `tech_app/frontend/cost-review.js:764` `runCostReview` → `await crRunAll()`，
  `:804` `costStep` → `await crRunPart/crRunAssembly/crRunAll`；
- `tech_app/frontend/requirement-create.js` `extractRequirement` → `await rcExtractRequirementFields()`，
  内部 `rcWaitExtractionTask` 最长等 210 秒。

解析/生成一次视觉或工艺模型调用普遍远超 20 秒（后端 `QWEN_TIMEOUT_SECONDS` 默认 300、
`OPENAI_BACKGROUND_TIMEOUT_SECONDS` 默认 600），于是父壳先判超时：
`tech-board-bridge.js:114` 产出 `opts.label + '超时未响应'`，左侧对话再提示
「开始解析失败：开始解析超时未响应。」——**业务其实还在正常跑**。

同一个机制让"看板最终失败"也失真：父壳只会在 `type === 'error'` 时显示提示
（`tech_workbench.js` `bindBoardBridge`），而看板推的是 `task-failed`，因此长任务
真正的失败原因不会出现在标题行提示位。

## 2. 目标

把"长任务动作"从**同步等待整份任务**改成**提交即回执 + 事件驱动完成**：

- `execute-action` 只确认"动作已受理/已启动"，秒级返回；
- 真正的进度与最终结果通过既有的 `task-progress` / `task-completed` / `task-failed`
  状态事件推给父壳；
- 20 秒超时保留，继续用于抓真正的"看板没响应"，不靠放宽超时掩盖问题。

## 3. 契约

### R1 运行时支持"延迟完成"动作

`tech_app/frontend/tech-board-runtime.js` 的 `registerActions` 条目新增可选字段
`deferred`，语义：**本动作只负责启动，完成/失败由本页在后台任务结束时自行发布**。

`runEntry` 在该条目上的行为：

| 情形 | 行为 |
| --- | --- |
| `run` 返回 `{ok:true}` 或普通值 | 发布 `action-state`（除非 `keepActionState === true`）→ 立即回 `ok`；**不得**发布 `task-completed` |
| `run` 返回 `{ok:false, error}` | 照旧发布 `task-failed` + 回结构化 failure（不变） |
| `run` 抛异常 / reject | 照旧发布 `task-failed` + 回结构化 failure（不变） |

非 `deferred` 条目行为**完全不变**（仍发布 `task-completed`），保证既有动作与既有测试不受影响。

### R2 长任务动作必须"提交即回执"

以下动作的 `run` 必须在**启动/提交**后台链路后立即 resolve，不得 `await` 整份任务：

| 文件 | 动作 | 当前错误写法 |
| --- | --- | --- |
| `tech_app/frontend/app.js` | `parseDrawing` | `await parseDrawing()` |
| `tech_app/frontend/assembly-integration.js` | `runIntegration` | `await aiRunAll()` |
| `tech_app/frontend/assembly-integration.js` | `integrationStep` | `await aiGenerate(...)` |
| `tech_app/frontend/cost-review.js` | `runCostReview` | `await crRunAll()` |
| `tech_app/frontend/cost-review.js` | `costStep` | `await crRunPart/crRunAssembly/crRunAll(...)` |
| `tech_app/frontend/requirement-create.js` | `extractRequirement` | `await rcExtractRequirementFields()` |

实现方式不限（后台链路 + `finally` 恢复 busy 状态即可），但：
- 这些条目必须声明 `deferred: true`；
- 原有 busy 状态切换（`updateActionState(name, {busy})`）与页面内的进度提示必须保留；
- 不得新增第二套任务轮询，也不得删除或绕过任何后端接口。

### R3 完成/失败由看板自己发布

上述页面在后台任务真正结束时，必须用既有公开 API 通知父壳：

```js
window.TechBoardRuntime.publish('task-completed', '<动作名>', { action: '<动作名>' });
window.TechBoardRuntime.publish('task-failed', '<动作名>', { action: '<动作名>', message: <可读原因> });
```

失败原因必须是真实错误文本，不得吞掉或用"操作失败"泛化。

### R4 父壳要显示长任务失败

`tech_app/frontend/tech-workbench.js` 的 `bindBoardBridge` 订阅回调必须把
`task-failed` 的 `payload.message` 显示到标题行提示位（沿用 `setBoardNotice`），
不能只处理 `type === 'error'`。

### R5 不回归

- 桥的 `DEFAULT_TIMEOUT` 仍为 20000，不放宽；`sync-state` 仍用 8000。
- `navigate-view` / `refresh-data` / `select-part` / 非长任务 `execute-action` 的
  时序与回执结构不变。
- 父壳仍然不读取 iframe 内部 DOM，不引入按钮 id / CSS selector 依赖。

## 4. 验收标准

- 红测 `tests/test_tech_board_deferred_actions_red.py` 全绿（含 Node 行为用例）。
- 统一父壳里点「开始解析」不再出现「开始解析超时未响应」；
  `execute-action` 秒级回执，进度经 `task-progress` 显示，结束经 `task-completed` /
  `task-failed` 收尾。
- 长任务真正失败时，标题行提示位显示真实原因。
- 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 无新增失败。

## 5. 不在本次范围

- 改动 `task-progress` / `task-completed` / `task-failed` 的信封结构与字段。
- 调整后端任务执行器、模型超时或轮询间隔。
- 其它阶段的视觉/交互改版。
