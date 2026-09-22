# Spec：移除技术工艺 Agent 会话阶段上下文卡

状态：Spec + 红测（已实现）
红测：`tests/test_tech_agent_remove_stage_context_card_red.py`

## 1. 问题

技术工艺统一工作台会在左侧会话标题下动态插入一张阶段上下文卡。以 1.1 为例，它重复展示“创建需求”“1.1 创建需求”、说明文字以及“提交确认 / 保存草稿”按钮。报价 Agent 没有这层重复介绍；技术侧真实操作已经存在于左侧统一操作栏和右侧业务看板，因此该卡增加噪声并重复入口。

## 2. 产品决策

从技术工艺 Agent 左侧会话中完整移除所有阶段的这类动态上下文卡，而不只隐藏 1.1 文案：

- 不再创建或渲染 `#ocStageContext` / `.oc-stage-context`；
- 不再展示阶段标题、阶段编号、提示语及卡内操作按钮；
- 不再保留卡内按钮专用的 `cpq:tech-agent:stage-action` 转发链路；
- 不使用 CSS 隐藏来伪装删除，相关渲染器和专用样式应清理。

## 3. 必须保留

- `STAGES`、九阶段路由、顶部大流程和右侧子步骤导航；
- `STAGE_AGENT_CONTEXT` 中供模型请求使用的九阶段 `pageContext`，以及 `currentPageContext()` 的请求注入能力；
- 左侧固定的 `#techChatActions` 操作栏及其附件、AI 执行、主次操作、批量操作、前后步骤、转交和重试能力；
- `STAGE_ACTIONS` 与右侧看板 `TechBoardBridge.executeAction`；
- 右侧 1.1 表单自己的“保存草稿 / 提交”按钮及原业务接口；
- 会话历史、消息、草稿、项目绑定、任务进度、结果入口和 `tech_ui` 协议。

## 4. 实现边界

`pageContext` 是发给模型的不可见语义上下文，不等于用户可见卡片。可以继续由父壳把当前阶段上下文传给 `agent-chat.js`，但接收端只能更新请求上下文，不能生成 UI。阶段操作只保留统一操作栏一条入口。

不修改后端、不删除 Agent 的需求工具、不删除右侧真实业务按钮、不改变审批权限或阶段流转。

## 5. 验收标准

1. 技术会话 DOM 运行路径不再创建 `ocStageContext`。
2. `agent-chat.js` 不再包含阶段卡渲染器、卡内按钮或 `stage-action` 事件发送。
3. `tech-workbench.js` 不再为阶段上下文构造卡内 actions，也不再监听 `stage-action`。
4. `agent-chat.css` 不再包含 `.oc-stage-context*` 专用样式。
5. 九阶段 `pageContext` 仍完整且进入 Agent 请求。
6. `#techChatActions`、`STAGE_ACTIONS`、右侧 1.1 保存与提交按钮均保留。
7. 会话、看板桥、九阶段导航和后端接口不变。

