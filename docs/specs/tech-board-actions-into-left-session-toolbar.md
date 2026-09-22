# 技术工艺：右侧看板业务按钮统一搬到左侧会话操作栏（唯一入口、唯一主按钮、带 tooltip）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_board_actions_into_left_toolbar_red.py`

## 1. 背景与问题

统一工作台已经是「左会话 + 右看板」两层结构，但业务动作入口仍然散落在三处：

1. 右侧阶段页自己在页内渲染操作按钮：
   - 1.1 `requirement-create.js`：`#saveDraft`、`id="submitRequirement"`、`#btnAiExtract`（AI 解析并带入字段）；
   - 2.1 `index.html`：`#btnParse`（▶ 开始解析）；
   - 2.2 `assembly-integration.html`：`#aiStart`（▶ 开始整合分析）、`#aiToFinance`（确认工艺并发送财务）；
     `assembly-integration.js` 的 `aiRenderActions()` 还动态生成 `#aiUploadBtn`/`#aiGenerate`/`#aiEdit`/`#aiSave`/
     `#aiParamsConfirm`/`#aiProcessConfirm`/`#aiParamsAutofill`/`#aiParamsFinalSave`/`#aiParamsFinalConfirm`；
   - 2.3 `cost-review.html`：`#crRunAll`、`#crConfirm`、`#crWriteDb`、`#crToQuote`、`#crReturn`；
2. 右侧业务卡底栏 `#techPrimary` / `#techSecondary`（`tech-workbench.js:syncActionBar()`）又代理同一批动作；
3. 左侧会话操作栏 `#techChatActions` 里另有一套写死的入口（`techChatAttach` / `techChatAiRun` /
   `techChatSecondary` / `techChatBulk` / 5 个 `data-tech-capability`）。

后果：同一步骤主按钮出现两次（左侧 `techChatPrimary` 与 `techChatBulk` 都是 `.primary`，右侧页内还有第三个），
父壳只登记了 `STAGE_ACTIONS` / `STAGE_CHAT_ACTIONS` 里少数动作名，看板已注册的 35 个动作里大多数
在左侧没有入口；右侧一改按钮结构，左侧接线就会断。

## 2. 目标

1. **左侧会话操作栏 `#techChatActions` 成为唯一的业务动作入口**：看板注册的每个业务动作都能在左侧点到。
2. **不重复**：同一动作在嵌入态只保留左侧一个入口；右侧阶段页与业务卡底栏不再渲染同语义按钮。
3. **每个 stage 恰好一个主按钮**：`#techChatActions` 内可见的 `.primary` 按钮数量恒为 0 或 1。
4. **删除左侧「附件」按钮** `#techChatAttach`（输入区左侧圆形 ＋ `#ocChatAttachBtn` 已是唯一上传入口）。
5. **每个按钮都有悬浮 tooltip**：`title` + `aria-label`，禁用时说明原因。
6. 独立打开阶段页（非嵌入）时页内按钮照旧，旧 2.1 能力基线不回退。

## 3. 契约 A：看板动作表携带角色与排序（`tech-board-runtime.js`）

`registerActions` 的动作条目在既有 `label` / `run` / `getState` / `deferred` 之外，可声明：

- `role`: `'primary'` | `'aux'`（缺省 `'aux'`）；
- `order`: number，同一步骤内左侧渲染顺序（缺省按注册顺序）；
- `hint`: string，tooltip 补充说明。

`entryState(name)` 必须把这三个字段归一化后透传给父壳，并且只透传白名单值：

```js
return {
  label: ..., visible: ..., enabled: ..., busy: ..., active: ..., analyzed: ...,
  role: rawRole === 'primary' ? 'primary' : 'aux',   // 任何其它值一律降级为 'aux'
  order: Number.isFinite(Number(...)) ? Number(...) : null,
  hint: String(...),
};
```

- `role` / `order` / `hint` 允许来自静态条目，也允许由 `getState()` 动态返回（2.2 的主操作反转需要）。
- 既有 6 个 state 事件名与 `cpq:tech-board` 信封不变；`ready` / `action-state` 已经全量下发 `actions`，不加新事件。

## 4. 契约 B：左侧操作栏由动作快照驱动（`tech-workbench.js`）

### 4.1 事实源

- 删除 `STAGE_ACTIONS` 与 `STAGE_CHAT_ACTIONS` 两张写死动作名的表；
- 新增 `STAGE_CHAT_FLOW`：九阶段各一条，只保留壳结构导航的布尔标记
  `{ prev, next, transfer }`（例如 `'requirement-create': { prev: false, next: true, transfer: true }`），
  **不得**再出现任何业务动作名；
- 业务按钮的 label 一律来自看板快照 `boardSnapshot().actions[name].label`，父壳不再维护兜底文案表。

### 4.2 渲染

必须实现并按下述语义使用四个函数（名字固定，父壳只有这一条渲染路径）：

```js
function boardActionEntries()          // 从快照取「已注册且 visible !== false」的动作，附带 name
function primaryActionName(entries)    // entries 里 role === 'primary' 按 (order, 注册顺序) 取第一个；没有则返回 ''
function actionTooltip(entry, opts)    // 生成 tooltip 文本（见 4.4）
function syncChatActionList(entries, primaryName)  // 渲染动态按钮
```

- 动态按钮直接渲染成 `#techChatActions` 这个 `<nav>` 的子元素（沿用
  `.tech-chat-actions > button` 既有描边胶囊样式），并带 `data-tech-action="<动作名>"`；
  每次同步先清掉上一批 `[data-tech-action]` 节点再重建，不允许堆叠。
- 新的动态按钮插到 `#techChatPrimary` 之后（`insertAdjacentElement('afterend')` 或等价写法），
  保持「主按钮在前、其余动作紧随其后、导航按钮在后」的固定顺序。
- 快照里 `name === primaryName` 的条目渲染进 `#techChatPrimary` 槽位（保留 id、保留 `variant: 'primary'`）；
  其余全部渲染成白底描边（不带 `primary`）。**全文件 `variant: 'primary'` 只允许出现一次。**
- 若某 stage 没有任何 `role === 'primary'` 的可见动作，`#techChatPrimary` 隐藏（不猜、不兜底）。
- 点击一律走既有 `TechBoardBridge.executeAction(name, { label, role })`；不再有按动作名写死的分支。
- 切 stage / 重挂 iframe 时必须丢弃上一步快照按钮（沿用桥重建后的 `state.actions = {}`），
  不允许把 2.2 的按钮带到 2.3。

### 4.3 删除与保留

- 删除：`#techChatAttach`、`openChatFilePicker()`、`#techChatAiRun`、`#techChatSecondary`、`#techChatBulk`，
  以及 `tech-workbench.html` 里对应的静态 `<button>`。
- 删除：右侧业务卡底栏的业务动作按钮 `#techPrimary` / `#techSecondary` 及其 `syncActionBar()`；
  `#techPrev` / `#techNext` / `#techNowLabel` 保留（壳导航，不属业务动作）。
- 保留：`#techChatPrimary`（唯一主按钮槽位）、`#techChatPrev`、`#techChatNext`、`#techChatTransfer`、
  `#techChatRetry`、`#ocFilesAction`、5 个 `data-tech-capability`、`#ocResultActions`、`#ocTaskProgressHost`、
  `#ocChatAttachBtn`、`#ocChatFileInput`。

### 4.4 tooltip

- 每个按钮（含 `#techChatPrev` / `#techChatNext` / `#techChatTransfer` / `#techChatRetry` 与所有动态按钮）
  必须同时有非空 `title` 与 `aria-label`，由 `actionTooltip()` 统一生成：
  - 正常：`<label>`；有 `hint` 时 `<label>：<hint>`；
  - 禁用：`<label>（<原因>）`，原因按优先级 `busy → '执行中'`、`enabled === false → hint || '当前不可用'`、
    无项目 `→ '请先打开项目'`。
- 动态按钮必须用 `setAttribute('aria-label', ...)` 写入；不得只写 `title`。
- `#techChatPrimary` 的 tooltip 额外标注这是当前步骤主操作。

## 5. 契约 C：看板侧角色声明与右侧去重

### 5.1 动作元数据（每页 `registerActions`）

| stage | 动作 | role | order | 备注 |
| --- | --- | --- | --- | --- |
| 1.1 | `submitRequirement` | primary | 10 | 主按钮「提交确认」 |
| 1.1 | `saveRequirementDraft` | aux | 20 | 保存草稿 |
| 1.1 | `extractRequirement` | aux | 30 | AI 解析并带入字段（复用 `/requirement/extract-documents`） |
| 1.2 | `confirmRequirement` | primary | 10 | ✓ 通过确认 |
| 1.2 | `returnRequirementDraft` | aux | 20 | × 驳回 |
| 1.2 | `applyConfirmationNote` | aux | 30 | 带入确认意见 |
| 1.3 | `submitRequirementReview` | primary | 10 | 提交审核意见 |
| 1.3 | `applyReviewNote` | aux | 20 | 带入审核意见 |
| 2.1 | `parseDrawing` | primary | 10 | ▶ 开始解析 |
| 2.1 | `runAllPartProcesses` | aux | 20 | 一键生成全部工艺推荐（不再占主按钮） |
| 2.1 | `modelLookup` / `verify` / `searchComponents` | aux | 30/40/50 | 联网核验 / 校验修正 / 重新检索零部件库 |
| 2.2 | `runIntegration` | 动态 | 10 | 未分析时 `primary`，已分析时 `aux` |
| 2.2 | `sendIntegrationToFinance` | 动态 | 20 | `aiAnalyzed()` 为真时 `primary`，否则 `aux` |
| 2.2 | `generateIntegrationParams` | aux | 30 | 生成/重新生成参数推荐，复用 `aiGenerate('params')` |
| 2.2 | `generateIntegrationProcess` | aux | 40 | 生成/重新生成组装工艺，复用 `aiGenerate('process')` |
| 2.2 | `saveIntegrationParams` | aux | 50 | 保存参数，复用 `aiSaveEdits('params')` |
| 2.2 | `confirmIntegrationParams` | aux | 60 | 确认参数推荐，复用 `aiConfirmStep('params')` |
| 2.2 | `confirmIntegrationProcess` | aux | 70 | 确认组装工艺，复用 `aiConfirmStep('process')` |
| 2.2 | `autofillIntegrationParams` | aux | 80 | ✦ 智能补全，复用 `aiParamsAutofill()` |
| 2.2 | `saveIntegrationParamsFinal` | aux | 90 | 保存补填，复用 `aiParamsFinalize(false)` |
| 2.2 | `confirmIntegrationParamsFinal` | aux | 100 | 确认参数已齐，复用 `aiParamsFinalize(true)` |
| 2.2 | `refreshIntegration` / `integrationStep` / `openIntegrationDrawings` | aux | 110/120/130 | 既有动作，只补元数据 |
| 2.3 | `runCostReview` | primary | 10 | 一键测算全部成本 |
| 2.3 | `confirmCostReview` | aux | 20 | ✓ 确认成本 |
| 2.3 | `writeCostReviewMaterial` / `sendCostReviewToQuote` / `returnCostReviewToProcess` | aux | 30/40/50 | 写入数据库 / 回传销售经理 / 提交工艺经理确认 |
| 2.3 | `refreshCostReview` / `costStep` | aux | 60/70 | 既有动作 |
| 3.1 | `submitProcessReportReview` | primary | 10 | 提交审核 |
| 3.1 | `saveProcessReport` / `generateProcessReportDraft` / `updateProcessReportFields` / `saveProcessReportDistribution` | aux | 20/30/40/50 | 既有动作 |
| 3.2 | `approveProcessReport` | primary | 10 | 审核通过并发布 |
| 3.2 | `rejectProcessReport` / `applyReportReviewNote` | aux | 20/30 | 退回汇总 / 带入审核意见 |
| 3.3 | `publishProcessReport` | primary | 10 | 发布报告 |
| 3.3 | `sendReportToQuote` / `createReportNewVersion` | aux | 20/30 | 回传销售经理继续报价 / 新建报告版本 |

- 每个 stage 静态 `role: 'primary'` 至多 1 个；2.2 用 `getState()` 动态返回 role，保持任意时刻恰好一个 primary。
- 动态可见性由看板决定：2.2 参数推荐专属按钮（`confirmIntegrationParams`、`autofillIntegrationParams`、
  `saveIntegrationParamsFinal`、`confirmIntegrationParamsFinal`、`saveIntegrationParams`）只在 `aiTab === 'params'` 时
  `visible: true`，组装工艺专属按钮（`confirmIntegrationProcess`）只在 `aiTab === 'process'` 时可见；2.3 的
  确认/写入/回传/返回按既有 `crBusy` / 已确认 / 只读条件决定 `enabled`。

### 5.2 右侧页内去重（嵌入态不再显示第二份入口）

嵌入态已经用 `.tech-embed` 隐藏了 `.oc-agent-pane`、`.footer-bar` 等整壳 chrome；本批把剩下这些
「与左侧工具栏同语义」的按钮也按同一模式隐藏（**只隐藏按钮，不删节点、不删绑定**，独立打开阶段页照旧）：

- 2.1 `#btnParse`（▶ 开始解析，嵌在 `.oc-agent-pane` 内）；
- 2.2 `#aiStart`（▶ 开始整合分析，嵌在 `.oc-agent-pane` 内）、`#aiActions`（参数推荐 / 组装工艺两个页签的
  操作行宿主）、`#aiOpsCard .ai-ops`（`#aiToFinance`「确认工艺并发送财务」所在按钮行）；
- 2.3 `#crRunAll`（一键测算全部成本，嵌在 `.oc-agent-pane` 内）、`#crOpsCard .ai-ops`
  （`#crConfirm` / `#crWriteDb` / `#crToQuote` / `#crReturn` 所在按钮行）；
- 1.1 `#btnAiExtract`（AI 解析并带入字段；`#aiExtractStatus` 解析进度文字保留可见）。

要求：

- 规则全部加在 `tech-embed.js` 注入的样式里，且**每条都必须带 `.tech-embed` 前缀**，禁止写不带前缀的全局隐藏规则；
- `.ai-ops` 只隐藏按钮行，卡片里的 `#aiProductName` / `#crProductName` / `#crQuantity` 等输入与
  `#aiOpsHint` / `#crHint` 说明保留；
- 页内按钮的 `onclick` / `disabled` 读写与既有实现函数保持不动（独立打开阶段页仍走它们），
  嵌入态只是不再显示，避免把功能删掉。

3.x 页脚里与已注册动作重复的按钮（`#srSave` / `#srSubmit` / 审核通过 / 退回 / `#rpPrimary` /
`#rpSendToSales`）已经整块在 `.tech-embed .footer-bar` 规则内，本批不重复新增规则。

### 5.3 必须保留（功能不缩水）

- 上传类入口不能搬到父壳：`#aiUploadBtn` 与 `#aiDrawingInput` 留在 2.2 看板内（跨文档 `input.click()`
  会丢 user activation，浏览器会拦截文件选择框），`openIntegrationDrawings` 仍只做
  「切到整合图纸页签 + 聚焦上传入口」。
- 底层实现函数一个都不能删：`parseDrawing()`、`startAllPartProcesses()`、`aiRunIntegration*`、`aiGenerate()`、
  `aiSaveEdits()`、`aiConfirmStep()`、`aiParamsAutofill()`、`aiParamsFinalize()`、`crRunAll()`、`crConfirmCost()`、
  `crRunOp()`、`rcExtractRequirementFields()`、`/requirement/extract-documents` 调用、`srSave()`、
  `/process-report/*` 审核发布调用。
- 独立打开（无 `embed=1`）时页内按钮、页脚与 3.x 操作按钮全部照旧。

## 6. 非目标与保护边界

- 不新增后端路由、Agent 工具、接口字段；不改动作语义名与任何业务算法。
- 不改 `cpq:tech-board` 信封、六个 state 事件、`tech:command` 方向与 `projectId` / `stage` 校验。
- 父壳依旧禁止 `contentDocument` / `contentWindow.document`。
- 不改九阶段 id、页面映射、顶部流程条、标题行、结果入口与任务进度宿主。
- 独立打开阶段页（未嵌入）时页内按钮与页脚按钮照旧渲染。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

### 反转的旧契约（本批取代，不作为回归失败）

- `tests/test_tech_left_toolbar_parity_red.py`：`TOOLBAR_IDS` 去掉 `techChatAttach` / `techChatAiRun` /
  `techChatSecondary`（动态渲染取代），`KEPT_IDS` 去掉 `techPrimary` / `techSecondary`（底栏业务按钮退役），
  `STAGE_CHAT_ACTIONS` 断言改为 `STAGE_CHAT_FLOW`，`test_primary_secondary_ai_go_through_board_bridge` 不再要求
  `STAGE_ACTIONS` 常量。
- `tests/test_tech_primary_quick_actions_and_bulk_part_analysis_red.py`：`runAllPartProcesses` 不再要求出现在父壳
  `tech-workbench.js`，改为要求它仍注册在 2.1 看板并带 `role: 'aux'` / `order`。
- `docs/specs/tech-primary-quick-actions-and-bulk-part-analysis.md` 中「批量入口在左侧也是 primary」一条不再生效，
  2.1 唯一主按钮为「开始解析」。

## 7. 验收

- `python3 -m unittest tests.test_tech_board_actions_into_left_toolbar_red -v` 全绿。
- 反转后的 `tests.test_tech_left_toolbar_parity_red`、`tests.test_tech_primary_quick_actions_and_bulk_part_analysis_red`
  全绿；`tests.test_tech_result_entries_board_views_red`、`tests.test_tech_outcome_entries_navigate_board_views_red`
  等「结果入口导航右侧看板」用例不回退。
- `node --check` 覆盖 `tech-board-runtime.js`、`tech-workbench.js`、`app.js`、`assembly-integration.js`、
  `cost-review.js`、`requirement-create.js`、`summary-result.js`、`report-review-result.js`、
  `report-publish-result.js`。
- 浏览器（统一工作台）：每一步左侧只出现一枚蓝色主按钮，其余动作描边且与右侧页内不再重复；
  鼠标悬浮每个按钮都能看到文案；切到 2.2/2.3 时上一步按钮不残留。

## 8. 对应测试

`tests/test_tech_board_actions_into_left_toolbar_red.py`
