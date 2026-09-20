# 技术工艺 Agent 能力恢复第 19 步：九阶段上下文修复 Spec

## 范围

九个内部阶段每一个都必须带**独立、正确**的 `page_context`，不得在缺上下文时默认冒充
2.1 图纸解析。当前缺陷：

- `tech_app/frontend/tech-workbench.js` 的 `STAGE_AGENT_CONTEXT` 只登记了 3 个 stage
  （`drawing` / `process` / `cost`），`requirement-create`、`requirement-confirm`、
  `requirement-review`、`summary`、`report-review`、`report-publish` 没有上下文，
  左侧会话栏在这六步不显示当前步骤。
- `tech_app/frontend/agent-chat.js` 的 `currentPageContext()` 在没有上下文时
  **硬回退到 `"2.1 图纸解析"`**，于是 1.1 / 1.2 / 1.3 / 3.* 的提问被当成 2.1 发给后端。
- `tech_app/backend/main.py` 保存会话轮次时 `body.page_context or "2.1 图纸解析"`，
  后端也在无上下文时冒充 2.1。

## 1. 九阶段 page_context 一览

| stage id | page_context |
| --- | --- |
| `requirement-create` | `1.1 创建需求` |
| `requirement-confirm` | `1.2 确认需求` |
| `requirement-review` | `1.3 审核需求` |
| `drawing` | `2.1 图纸解析` |
| `process` | `2.2 组装与整合` |
| `cost` | `2.3 成本测算` |
| `summary` | `3.1 汇总结果` |
| `report-review` | `3.2 结果审核` |
| `report-publish` | `3.3 发布并回传报价` |

## 2. 前端（`tech_app/frontend/tech-workbench.js`）

- `STAGE_AGENT_CONTEXT` 必须覆盖全部九个 stage id，每个 stage 有独立 `pageContext`
  （取值见上表），`label` / `hint` / `actions` 仍只引用既有 `STAGE_ACTIONS` 语义动作名，
  不复制业务逻辑。
- `stageAgentContext()` 只根据 `state.stage` 返回该 stage 的上下文；查不到就返回 `null`，
  **不得**回退成别的 stage。
- 切换 stage 后（`applyStage` → `syncAgentStageContext`）左侧上下文卡即时更新为当前步骤。

## 3. 前端（`tech_app/frontend/agent-chat.js`）

- `currentPageContext()` 必须返回当前 `stageContext.pageContext`；缺失时返回中性值
  （如 `""` 或 `"未指定步骤"`），**不得**写死 `"2.1 图纸解析"`。
- 请求体 `page_context` 仍只在有真实上下文时携带具体步骤；无上下文时不伪装成任何具体阶段。
- 上下文卡渲染仍复用既有 `#ocStageContext` 宿主，不新增父级面板。

## 4. 后端（`tech_app/backend/main.py`）

- 保存会话轮次时 `page_context` 的默认值不得是 `"2.1 图纸解析"`；无上下文时用中性值
  （如 `""` / `"未指定步骤"`）。
- 无上下文时不得把 `[当前页面：2.1 图纸解析]` 注入模型消息。
- 不新增 / 删除路由，不迁移或删除历史会话。

## 5. 边界

- 不改九个 stage id 与 URL；不动看板业务逻辑与既有 Agent 工具。
- 不把上下文写进父壳 DOM 之外的业务表单；不建 Drawer / Modal。
- 九个 page_context 文案一旦确定即用于会话留痕，后续不得再按 stage 冒充 2.1。

## 6. 验收标准

1. `STAGE_AGENT_CONTEXT` 覆盖九个 stage id，且九个 `pageContext` 值与上表一一对应；
2. `stageAgentContext()` / `STAGE_AGENT_CONTEXT[state.stage]` 不把缺失 stage 回退成 2.1；
3. `agent-chat.js` 的 `currentPageContext()` 无 `"2.1 图纸解析"` 硬回退；
4. `main.py` 的 `page_context` 默认值不是 `"2.1 图纸解析"`；
5. 既有 `test_tech_context_substeps_and_frame_status_red` 保持全绿。

## 7. 对应测试

`tests/test_tech_stage_context_nine_stages_red.py`
