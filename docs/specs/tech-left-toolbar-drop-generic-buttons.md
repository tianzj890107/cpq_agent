# 技术工艺：左侧会话操作栏只留当前步骤业务动作，去掉通用刷新与导航按钮

状态：Spec + 红测（已实现）
红测：`tests/test_tech_left_toolbar_drop_generic_buttons_red.py`

## 1. 问题

左侧会话操作栏（`#techChatActions` + `#techChatPrimary`）把两类**不属于业务动作**的东西也一起渲染了，
每一页都重复出现，纯噪声：

1. 四个静态通用按钮（`tech_app/frontend/tech-workbench.html:121-124`）：
   `上一步` `下一步` `转交任务` `失败重试`。
   步骤导航在顶部流程条已经有一份（`#techPrev` / `#techNext` / 大步骤按钮 / 子页签），
   这里是第二套，属于重复入口。
2. 每个 stage 页注册的刷新动作，`getState()` 都返回 `visible: true`，于是左侧多出一颗
   「刷新需求看板 / 刷新整合看板 / 刷新成本看板 / 刷新汇总报告 / 刷新审核报告 / 刷新发布报告」：
   - `app.js:2549` `refreshData`（刷新看板数据）
   - `requirement-create.js:284` / `requirement-confirm-page.js:149` / `requirement-review-page.js:76` `refreshData`
   - `assembly-integration.js:1586` `refreshIntegration`
   - `cost-review.js:783` `refreshCostReview`
   - `summary-result.js:129` / `report-review-result.js:102` / `report-publish-result.js:100` `refreshProcessReport`
   这些是看板**内部刷新通道**（`refresh-data` 命令、Agent 工具 `RefreshRequirementBoard` 等都在用），
   不是给人点的业务按钮。

## 2. 目标

1. 左侧会话操作栏只保留**当前步骤的真实业务动作**：看板快照里 `role === 'primary'` 的那一颗进
   `#techChatPrimary`，其余可见业务动作紧随其后。刷新类动作与通用导航按钮不再出现在这里。
2. 能力不减少：步骤导航继续由顶部流程条提供；附件继续由输入区 ＋ 提供；结果入口与任务进度卡照旧；
   刷新能力**动作本体保留**，Agent 工具、`refresh-data` 命令与看板内部调用一律照用。
3. 操作栏显隐改由「当前步骤是否有可见业务动作」决定，不再依赖壳里的阶段导航表。

## 3. 契约 A：删除四个通用按钮（`tech-workbench.html`）

- 删掉 `#techChatPrev`、`#techChatNext`、`#techChatTransfer`、`#techChatRetry` 四个 `<button>`。
- `#techChatPrimary` 保留，仍是唯一主按钮槽位。
- 输入区 `#ocChatAttachBtn` / `#ocChatFileInput`、结果入口 `#ocResultActions`、
  任务进度宿主 `#ocTaskProgressHost`、顶部 `#techPrev` / `#techNext` / `#techNowLabel` 全部保留。

## 4. 契约 B：父壳删除对应接线（`tech-workbench.js`）

- 删除 `setChatButton($('techChatPrev'|'techChatNext'|'techChatTransfer'|'techChatRetry')…)` 四处渲染
  与 `bindChatToolbar()` 里对应的四个事件绑定。
- 删除只服务这四个按钮的辅助：`transferCurrentTask()`、`replayLastBoardAction()`、
  以及在 `runBoardAction()` 里为失败重试维护的 `lastBoardAction` / `lastActionFailed`。
- 删除给这四个按钮提供 `prev / next / transfer` 的阶段壳导航表 `STAGE_CHAT_FLOW` 与 `chatFlow()`。
- `syncChatActions()`：显隐改为「看板快照里有没有可见业务动作」——
  先取 `boardActionEntries()`，`entries.length === 0` 时 `bar.hidden = true` 并返回；
  否则渲染主按钮槽位 + `syncChatActionList(entries, primaryName)`。
- 保留的动作出口不变：业务按钮仍只发 `TechBoardBridge.executeAction`，
  不写死业务动作名、不查 iframe DOM、不按动作名写分支。
- 上一批（Spec `tech-business-actions-clickable-then-error`）对 `syncChatActionList()` /
  `actionTooltip()` / `runBoardAction()` 的要求继续有效，本批只减按钮、不改这条出口。

## 5. 契约 C：刷新动作退出左侧栏，但动作本体保留（九个 stage 页）

- 每个刷新动作的 `getState()` 返回 `visible: false`，动作条目、`run()` 实现与注册名一律不动：

  | 文件 | 动作名 | 标签 |
  | --- | --- | --- |
  | `app.js` | `refreshData` | 刷新看板数据 |
  | `requirement-create.js` | `refreshData` | 刷新需求看板 |
  | `requirement-confirm-page.js` | `refreshData` | 刷新需求看板 |
  | `requirement-review-page.js` | `refreshData` | 刷新需求看板 |
  | `assembly-integration.js` | `refreshIntegration` | 刷新整合看板 |
  | `cost-review.js` | `refreshCostReview` | 刷新成本看板 |
  | `summary-result.js` | `refreshProcessReport` | 刷新汇总报告 |
  | `report-review-result.js` | `refreshProcessReport` | 刷新审核报告 |
  | `report-publish-result.js` | `refreshProcessReport` | 刷新发布报告 |

- `TechBoardRuntime` 的 `refresh-data` 命令分发、`TechBoardBridge.refreshData()` 与 Agent 侧的
  `requestRequirementExtract` / `refreshIntegrationBoard` / `refreshCostReview` 等调用点一字不改：
  `visible: false` 只影响左侧栏是否渲染，不影响 `executeAction` / `refresh-data` 的执行。

## 6. 非目标与保护边界

- 不改 `cpq:tech-board` 信封、事件白名单、`payload` 形状与 `entryState()` 字段契约。
- 不删除任何业务动作、Agent 工具或后端路由；不新增路由。
- 不改顶部流程条、子页签、标题行、右侧看板卡片结构与嵌入态隐藏规则。
- 不改输入区 ＋（附件）行为、结果入口与任务进度卡。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 7. 本批取代的旧断言（需由契约方按本批语义反转）

- `tests/test_tech_left_toolbar_parity_red.py`：`TOOLBAR_IDS` 里的
  `techChatPrev / techChatNext / techChatTransfer / techChatRetry`、
  `test_transfer_reuses_existing_capability_not_new_route`、`test_retry_replays_last_action`
  —— 四个按钮已按用户要求退役，转交改由会话输入区承担、失败由业务按钮本身重试。
- `tests/test_tech_board_actions_into_left_toolbar_red.py`：
  `TOOLBAR_IDS` 同上、`test_stage_flow_table_covers_nine_stages_without_action_names`
  （`STAGE_CHAT_FLOW` 已退役）、`test_existing_left_toolbar_channels_are_kept` 里
  `applyStage` / `last[A-Za-z]*Action` 两条、`test_every_button_has_tooltip_and_aria_label`
  里的 `当前不可用` token（上一批 Spec 已要求删掉该文案）。
- `tests/test_tech_primary_quick_actions_and_bulk_part_analysis_red.py:67-68` 的
  `techChatPrev` / `techChatTransfer` 断言（节点已删除）。

## 8. 验收

- `python3 -m unittest tests.test_tech_left_toolbar_drop_generic_buttons_red -v` 全绿。
- 回归：`tests.test_tech_business_actions_clickable_then_error_red`、
  `tests.test_tech_board_bridge_protocol_red`、`tests.test_tech_step_status_blue_pill_red`、
  `tests.test_tech_drawing_agent_actions_red`、`tests.test_tech_integration_agent_red`、
  `tests.test_tech_cost_review_agent_red`。
- `node --check` 覆盖 `tech-workbench.js` 与九个 stage 页脚本；`git diff --check` 通过。
- 浏览器：九阶段左侧操作栏只剩当前步骤的业务动作（主按钮 + 其余业务动作），
  没有刷新 / 上一步 / 下一步 / 转交任务 / 失败重试；顶部流程条与输入区 ＋ 照旧可用；
  Agent 触发的看板刷新（`refresh-data`）仍然生效。

## 9. 对应测试

`tests/test_tech_left_toolbar_drop_generic_buttons_red.py`
