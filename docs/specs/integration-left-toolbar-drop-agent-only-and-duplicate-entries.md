# 技术工艺 2.2：左侧操作栏去掉 Agent 专用单步动作与重复的「整合图纸」入口

状态：TDD Red，等待实现。

## 1. 问题（实测）

2.2「组装与整合」的左侧会话操作栏由看板动作快照渲染（`tech-workbench.js` 的
`boardActionEntries()` 只跳过 `visible === false` 的条目）。当前 2.2 把两个本不属于
用户操作栏的条目声明成了 `visible: true`：

- **`integrationStep`（运行整合环节）**（`assembly-integration.js:1623`，`order: 120`，`getState()` 返回 `visible: true`）
  只跑**单个**环节，必须由调用方给出 `payload.step = params | process`。但父壳
  `runBoardAction()`（`tech-workbench.js:602`）只发 `{ label, role }`，不带 `step`，
  所以用户点它必然落到 `bad-step`，弹出「step 只能是 params / process；成本测算请在成本步骤发起」。
  它的真实调用方是 Agent 工具 `RequestIntegrationStep → tech_ui "integration-step"`
  （`oc_agent.py:1180`、`agent-chat.js:586`、`agent-chat.js:754-763`），经
  `executeAction("integrationStep", { step })` 走 `execute-action` 命令 —— 与 `visible` 无关。
- **`openIntegrationDrawings`（整合图纸）**（`assembly-integration.js:1649`，`order: 130`，`getState()` 返回 `visible: true`）
  只把看板切到 `drawings` 页签并聚焦上传按钮。而同一界面标题行右侧已经有同一目标的子页签
  （`tech-workbench.js:61-66` 的 `CHILD_TAB_PROXY.process.tabs` → `navigateView('drawings')`），
  于是「整合图纸」在一个界面里出现两次。它的真实调用方是 Agent 工具
  `UploadIntegrationDrawing → tech_ui "open-integration-drawings"`（`oc_agent.py:1183`、`agent-chat.js:587`）。

两个条目的 `run()` 实现本身没有问题，问题只在**可见性**：它们不该出现在给用户点的左侧栏里。

## 2. 目标

1. 2.2 左侧操作栏不再出现「运行整合环节」与「整合图纸」两颗按钮；
2. Agent 的两个工具链路（`integration-step`、`open-integration-drawings`）一字不改、照常可用；
3. 2.2 真正的用户动作「开始整合分析」「确认工艺并发送财务」与各页签专属动作的可见性、主/次按钮反转一律不变；
4. 「整合图纸」在 2.2 界面上只剩标题行子页签这一个可见入口。

## 3. 契约 A：两个条目改为 `visible: false`（`assembly-integration.js`）

- `integrationStep.getState()`：`visible: false`（其余 `enabled: true` / `busy: aiBusy` 不变）。
- `openIntegrationDrawings.getState()`：`visible: false`（其余 `enabled: true` / `busy: false` /
  `active: aiTab === 'drawings' ? 'drawings' : null` 不变）。
- 两处只改这一个字段，和既有 `refreshIntegration` 的 `visible: false` 用同一条通道 ——
  父壳不渲染即等于入口消失，不需要在父壳里按动作名写黑名单。
- `label`（`运行整合环节` / `整合图纸`）、`role`（`aux`）、`order`（`120` / `130`）、
  `deferred`（`integrationStep` 为 `true`）、`run()` 实现、`getState()` 其余字段一律不动。

## 4. 契约 B：Agent 能力不缩水

- `agent-chat.js` 继续保留 `ui_action` 分发与唯一出口：
  `"integration-step"` → `integrationBoardStep()` → `bridge.executeAction("integrationStep", { step })`；
  `"open-integration-drawings"` → `openIntegrationDrawings()` → `bridge.executeAction("openIntegrationDrawings", ...)`。
- `oc_agent.py` 的工具映射 `RequestIntegrationStep: "integration-step"` 与
  `UploadIntegrationDrawing: "open-integration-drawings"` 不改。
- `runEntry()` 只按动作名执行，不读 `visible`；`execute-action` 命令因此照旧能跑到这两个实现
  （与 `silent` 刷新动作同一条既有机制），本批不新增任何「Agent 专用」分支。
- `registerViews` 的 `drawings` / `params` / `process` 三个内部视图不改。

## 5. 契约 C：可见入口不重复、用户动作不受影响

- `runIntegration`（开始整合分析）与 `sendIntegrationToFinance`（确认工艺并发送财务）
  的 `getState()` 仍返回 `visible: true`，`role` 仍按 `aiAnalyzed()` 反转，`order` 仍为 10 / 20。
- 页签专属动作（`generateIntegrationParams` / `generateIntegrationProcess` / `saveIntegrationParams` /
  `confirmIntegrationParams` / `confirmIntegrationProcess` / `autofillIntegrationParams` /
  `saveIntegrationParamsFinal` / `confirmIntegrationParamsFinal`）的 `visible: show`（按当前页签）不变。
- `tech-workbench.js` 的 `CHILD_TAB_PROXY.process.tabs`（整合图纸 / 参数推荐 / 组装工艺）不变，
  它继续是这三类视图对用户的唯一可见入口。
- 父壳 `boardActionEntries()` 继续以 `entry.visible === false` 作为唯一跳过条件，不新增名字黑名单。

## 6. 边界（本批禁止改动）

- 不改 `tech-board-runtime.js`、`tech-board-bridge.js`、`cpq:tech-board` 信封与事件白名单；
- 不改任何后端路由、service 与 `oc_agent.py` 的工具映射；
- 不改 `entryState()` 字段契约、`silent` / `deferred` 语义；
- 不删 `integrationStep` / `openIntegrationDrawings` 的注册与 `run()` 实现（Agent 仍在用）；
- 不改 2.2 的右侧看板按钮、页签结构与上传入口。

## 7. 验收

1. 进入 2.2，左侧操作栏只有：主按钮（未分析时「开始整合分析」／分析后「确认工艺并发送财务」）、
   另一个次按钮、以及当前页签的专属动作；不再出现「运行整合环节」「整合图纸」。
2. 标题行右侧「整合图纸 / 参数推荐 / 组装工艺」页签仍能切换右侧看板视图。
3. Agent 说「帮我跑参数推荐」时，`integration-step` 仍能驱动看板执行；
   Agent 说「我要上传整合图纸」时，看板仍会切到整合图纸页签并聚焦上传入口。
4. 全量回归不得新增失败点。
