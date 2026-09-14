# 技术工艺：步骤状态行去掉，文本原样并入统一工作台标题行

状态：TDD Red，等待 DeepSeek 实现。

## 1. 背景

统一工作台（`tech-workbench.html`）右侧业务卡的标题行 `.tech-workspace-context`（`#techContextHeader`）
已经承载：固定大标题 `#techContextTitle`（如「组装与整合」）、提示位 `#techContextNotice`、
子页签 `#techSubstepsBar`（如「整合图纸 / 参数推荐 / 组装工艺」）。

每个阶段页自己还渲染一条步骤状态行：
- 2.1 `index.html` 的 `.title-row > #status`（`app.js` 的 `status()` 写「已打开项目 <pid>（补充说明✓，佐证文件 0 个）」）；
- 2.2 `assembly-integration.html` 的 `.title-row > #status` 与 `.ai-status#aiPanelStatus`；
- 2.3 `cost-review.html` 的 `.title-row > #status` 与 `.ai-status#crStatus`；
- 1.1/1.2/1.3 需求页模板里的 `.title-row > .status-badge`（创建中 / 待确认 / 待审核）。

嵌入态下 `tech-embed.js` 只隐藏了 `.form-title` 与 `.ai-tabs`，状态徽标留在原位，
于是在父壳标题行下面又多出一整行只有状态文字的行；2.2/2.3 更是同一句话出现两次
（`aiStatus()` 同时写 `#status` 与 `#aiPanelStatus` / `crStatus()` 写 `#crStatus`）。

## 2. 目标

1. 阶段页不再单独占一行显示步骤状态。
2. 这段状态文字**原样**出现在统一工作台的标题行里（与「组装与整合」「整合图纸 / 参数推荐 / 组装工艺」同一行）。
3. 状态文字本身一个字不改：`已打开项目 <pid>（补充说明✓，佐证文件 N 个）`、`就绪`、`本步已确认`、`创建中`…照抄。
4. 独立打开阶段页（非嵌入）时，页面自己的状态行照旧显示，不受影响。

## 3. 协议契约（看板 → 父壳）

新增一个标准状态事件，沿用既有 `cpq:tech-board` 信封（`namespace` / `version` / `type` / `requestId` /
`projectId` / `stage` / `name` / `payload`）：

- 事件名：`board-status`，`type: 'state'`；
- `payload`：`{ text: string, level: 'info' | 'error' }`，`text` 就是页面要显示的原文；
- `tech-board-runtime.js`：`EVENT.BOARD_STATUS = 'board-status'`，并导出
  `TechBoardRuntime.publishStatus(text, level)`（内部复用既有 `publish()`，不做第二套通信）；
- `tech-board-bridge.js`：把 `board-status` 加进 `STATE_EVENTS` 白名单；
- 既有的 origin / source / projectId / stage 校验一律不变，也不放宽。

## 4. 子页面契约

- 任何在页内渲染步骤状态的阶段页，在写本地元素的同时把同一段文字上报：
  `window.TechBoardRuntime && TechBoardRuntime.publishStatus(text, level)`。
  必须上报的现有写入点：
  `app.js` 的 `status(msg, busy)`（2.1）、`assembly-integration.js` 的 `aiStatus(message, error)`（2.2）、
  `cost-review.js` 的 `crStatus(message, error)`（2.3），以及 1.1/1.2/1.3 三个需求页渲染出的状态徽标文本。
- 上报文本必须与本地显示完全一致（含 `处理中 · ` 前缀与 2.2/2.3 的 `#status` + `.ai-status` 同文）。
- 嵌入态（`document.documentElement.classList.contains('tech-embed')`）下，页内状态行隐藏：
  在 `tech-embed.js` 注入的样式里补
  `.tech-embed .title-row .status-badge { display:none !important; }`、
  `.tech-embed #status { display:none !important; }`、
  `.tech-embed .ai-status { display:none !important; }`。
  只允许 `.tech-embed` 作用域，禁止写不带前缀的全局隐藏规则。
- `.title-row` 内的其它内容（2.1 的 `#partsMetric` / `#confidenceMetric` 等度量、文件名、错误提示）
  不受影响；不隐藏整个 `.title-section`。

## 5. 父壳契约

- `tech-workbench.js` 订阅 `board-status`，把 `payload.text` 原样写进 `#techContextNotice`
  （标题行内的提示位，与 `#techContextTitle`、`#techSubstepsBar` 同一行）。
- 固定大标题 `#techContextTitle` 仍只来自 `MAJOR_STEPS[].title`，状态文本不得覆盖它；
  `#techSubstepsBar` 的页签渲染与 `data-child-tab` 逻辑不变。
- 提示位两种内容共存：看板状态是默认内容，未就绪 / 失败提示优先显示；
  提示清除后回落到最近一次收到的那一步状态文本，而不是留空。
- 切换 stage（`applyStage` / 重新挂载 iframe）时必须丢弃上一步的状态文本，
  不允许把 2.2 的「就绪」带到 2.3 显示。
- 未收到任何 `board-status` 时，提示位保持隐藏（不显示空行）。

## 6. 非目标与保护边界

- 不改任何状态文案、`status()` / `aiStatus()` / `crStatus()` 的调用点与语义。
- 不改九阶段 stage id、页面文件映射、顶部流程条、右侧业务卡结构、底栏代理与左侧会话。
- 不改 `tech:command` 方向、既有六个 state 事件（ready / action-state / task-progress /
  task-completed / task-failed / selection-changed）的名字与语义。
- 不改后端路由、Agent 工具、业务流程与数据。
- 不新增第二套通信通道（不得再用 `iframe.contentDocument` 直接读取子页面 DOM）。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 7. 验收

- `python3 -m unittest tests.test_tech_step_status_in_context_row_red -v` 全绿。
- `node --check` 覆盖 `tech-board-runtime.js`、`tech-board-bridge.js`、`tech-workbench.js`、
  `app.js`、`assembly-integration.js`、`cost-review.js`、`requirement-create.js`、
  `requirement-confirm-page.js`、`requirement-review-page.js`。
- 浏览器（统一工作台）：每一步标题行右侧显示该步状态原文，标题行下方不再有单独的状态行；
  独立打开 2.1/2.2/2.3 时页内状态行照旧。

## 8. 对应测试

`tests/test_tech_step_status_in_context_row_red.py`
