# 技术工艺 Agent 能力恢复第 9 步：1.2 确认需求 Agent 动作 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_tech_requirement_confirm_red.py`

## 范围

只打通 **1.2 确认需求**：Agent 能读取结构化完整性检查与待澄清问题、把确认意见
带入看板、并在**用户明确确认后**通过确认或退回草稿。

只新增 Agent 工具与前端接线，**不新增 HTTP 路由、不新增第二套完整性检查或流转逻辑**。

## 1. 新增平台工具（`tech_app/backend/services/oc_agent.py`）

在 `PLATFORM_TOOL_SCHEMAS` 追加 5 个工具，并在 `_run_platform_tool` 里分派。
每个工具必须复用既有实现，不得新写一套：

| 工具名 | 语义 | 复用 |
| --- | --- | --- |
| `GetRequirementPrecheck` | 获取完整性检查结果 | 既有确定性预检（`/requirement/precheck` 同一份实现，不调模型） |
| `GetRequirementClarifications` | 获取待澄清问题 | 同一份预检结果里 `status == "need_info"` 的条目，不另立规则 |
| `SaveRequirementConfirmationNote` | 补充确认内容 | 只发请求（与 `RequestParse` 同模式）：回报要填入看板 `#confirmationNote` 的文本，由看板写入表单；真正落盘仍由既有 confirm / return 动作携带 comment 完成 |
| `ConfirmRequirement` | 通过确认 | 既有 `POST /requirement/confirm` 同一份流转实现 |
| `ReturnRequirementToDraft` | 退回草稿 | 既有 `POST /requirement/return-to-draft` 同一份流转实现 |

约束：

- **必须显式确认**：`ConfirmRequirement` / `ReturnRequirementToDraft` 必须接收
  `confirmed` 参数；`confirmed !== true` 时只返回
  `{"requires_confirmation": true, ...}` 回执、**不得改动任何状态**；
  只有 `confirmed === true` 才执行流转。Agent 不得代替人工审批。
- 流转必须复用同一份实现：把 `/requirement/confirm`、`/requirement/return-to-draft`
  的流转逻辑与 `_requirement_precheck` 抽到一个 service 模块（如
  `services/requirement_flow.py`），路由与平台工具都调用它；
  `oc_agent.py` 不得自带第二份预检或状态流转。
- 写操作必须落既有审计（`workflow:requirement_confirmed` /
  `workflow:requirement_returned`）。
- 不新增任何 `@app.` 路由；`/requirement/precheck`、`/requirement/confirm`、
  `/requirement/return-to-draft` 必须保留。

## 2. UI 动作映射

`UI_ACTION_TOOLS` 追加：

```
"SaveRequirementConfirmationNote": "fill-confirmation-note"
"ConfirmRequirement":              "refresh-requirement"
"ReturnRequirementToDraft":        "refresh-requirement"
```

关键：**不得**把 `ConfirmRequirement` / `ReturnRequirementToDraft` 映射成会在
tool_use 阶段就自动执行的动作（如 `confirm-requirement` / `return-requirement`），
否则父壳会在工具跑之前绕过确认门直接审批。既有动作名保持不变。

## 3. 看板侧（`tech_app/frontend/requirement-confirm-page.js`）

- 在既有 `cfRegisterTechBoardActions` 的 `TechBoardRuntime.registerActions({...})`
  追加 `applyConfirmationNote`：把父壳传来的确认意见写入既有 `#confirmationNote`
  （复用 `#bringAi` 的「带入」写法，只追加，不覆盖人工已写内容）；保留
  `confirmRequirement` / `returnRequirementDraft`；
- `cfAct` 执行通过 / 退回时经 `TechBoardRuntime` 上报
  `task-progress` / `task-completed` / `task-failed`，不能只在 iframe 内发 `window` 事件；
- 仍然只调用既有 `/requirement/confirm` / `/requirement/return-to-draft`，
  不新增接口、不复制流转逻辑。

## 4. 左侧会话（`tech_app/frontend/agent-chat.js`）

- 收到 `ui_action === "fill-confirmation-note"` 时，经
  `TechBoardBridge.executeAction("applyConfirmationNote", { note })` 把确认意见带入看板，
  不自己写接口；
- 能把预检类工具结果渲染成「需求确认摘要」：使用 `generated_note` 概述、
  `need_info` 统计待补充项；
- 收到带 `requires_confirmation` 的工具结果时，给出「需人工明确确认后才能通过 / 退回」
  的提示，不得静默通过；
- 左侧不得直接调用 `/requirement/confirm` 或 `/requirement/return-to-draft`。

## 5. 闭环

1. 用户在 1.2 左侧问「这单还能不能确认」；
2. Agent 调 `GetRequirementPrecheck` / `GetRequirementClarifications`；
3. 左侧渲染完整性摘要与待澄清问题（哪些 `need_info`、还缺什么）；
4. Agent 调 `SaveRequirementConfirmationNote`，右侧 `#confirmationNote` 被带入确认意见；
5. 用户明确说「确认通过」后，Agent 才以 `confirmed: true` 调 `ConfirmRequirement`；
   用户说「退回」时才调 `ReturnRequirementToDraft`；
6. 右侧看板刷新状态与留痕，左侧会话保留完整工具轨迹。

## 6. 边界

- 不新增/删除路由与既有工具；`storage`、数据模型不改；
- Agent 不得代替人工审批：无 `confirmed: true` 绝不改状态；
- 父壳不新增确认表单 DOM（表单始终在右侧看板 iframe 内）；
- 不改九个 stage id 与 URL，不动历史会话与项目数据。

## 7. 验收标准

1. 5 个新工具都在 `PLATFORM_TOOL_SCHEMAS` 且都被 `_run_platform_tool` 分派；
2. `ConfirmRequirement` / `ReturnRequirementToDraft` 有显式确认门
   （`requires_confirmation` + `confirmed`），未确认不改状态；
3. 预检与流转实现唯一：`基于已保存需求字段的确定性完整性检查`、
   `workflow:requirement_confirmed`、`workflow:requirement_returned`
   各只出现在一个后端文件里，且都不是 `oc_agent.py`；既有三个需求确认路由仍在；
4. `UI_ACTION_TOOLS` 出现 `fill-confirmation-note`，且 `ConfirmRequirement` /
   `ReturnRequirementToDraft` 没有被映射成自动审批动作；
5. 看板注册 `applyConfirmationNote` 并经 `TechBoardRuntime` 上报确认进度；
6. 左侧由 `fill-confirmation-note` 触发看板动作，并能渲染确认摘要与
   `requires_confirmation` 提示；
7. `requirement-confirm-page.js` 仍只调用既有 confirm / return 接口，
   `agent-chat.js` 不直接调用这两个接口；
8. 既有平台工具与全部既有路由不减少。

## 8. 对应测试

`tests/test_tech_requirement_confirm_red.py`
