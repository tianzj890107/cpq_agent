# 技术工艺会话：报错按普通输出走会话流、refresh 类动作不再产生进度卡

状态：TDD Red，等待 DeepSeek 实现。

## 1. 问题（实测）

### 1.1 报错被钉在会话最底部，一直看得到

`tech_app/frontend/agent-chat.js:1559-1580` 的 `showBoardNavFailure(view, payload, error)`：

```js
noteInThread(`打开「${label}」失败：${reason}。`);   // 流内一条，没问题
const retry = el("button", "oc-chip oc-chip-retry", "重试");
const row = el("div", "oc-retry-row");
row.append(retry);
(tinner || thread).append(row);                       // 问题在这里
```

而 `tech_app/frontend/agent-chat.css:624`：

```css
.oc-retry-row { display: flex; order: 1; padding: 2px 0 4px 38px; }
```

`.oc-tinner` 是 flex column（`agent-chat.css:156`），`order: 1` 把这一行**永久钉在会话列最底部**，
而且这一个节点从不被移除 —— 于是一次失败之后，底部那行「重试」一直挂在会话最下面，
即使后面又聊了很多轮也不动。用户要的是：报错就按普通输出一样在会话流里继续往下走。

（同一个 `order: 1` 机制在 `.oc-result-actions`（`:276`）上是**有意为之**：结果入口要常驻底部，
本批不动它。）

### 1.2 每一页都刷出一张「已完成」进度卡

会话里反复出现：

```
刷新看板数据 已完成
刷新需求看板 已完成
刷新需求确认页 已完成
刷新需求审核页 已完成
```

来源是 `tech_app/frontend/tech-board-runtime.js` 的 `runEntry()`：它对**每个**动作都发
`TASK_PROGRESS(phase:'start')` 与 `TASK_COMPLETED`（失败再发 `TASK_FAILED`），
父壳 `agent-chat.js:1717-1724` 把它们交给 `renderTaskProgress()` 渲染成 `.oc-task-card`
（标题 + 「已完成」状态 chip），按 taskId / label 去重后**永不删除**。

刷新动作（2.1/1.1/1.2/1.3 的 `refreshData`、2.2 的 `refreshIntegration`、2.3 的 `refreshCostReview`、
3.1/3.2/3.3 的 `refreshProcessReport`）是看板内部同步，不是用户发起的长任务，
它们出卡没有任何信息量，只会把会话塞满。

### 1.3 refresh 失败还会被静默吞掉

`agent-chat.js:1504`：`Promise.resolve(bridge.refreshData({...})).catch(() => {});` —— 失败无声。
和第 1.1 条是同一个诉求：失败必须以普通输出出现在会话里。

## 2. 目标

1. 失败提示与它的重试控件一起作为**会话流里的一条普通消息**输出，跟着对话往下走；
   会话里不再有「永远钉在底部」的提示行。
2. `silent` 动作（本批是九处刷新动作）不再产生任务进度卡；
   普通长任务（解析、整合、测算、报告…）的进度卡机制原样保留。
3. refresh 失败以普通输出呈现，不允许静默 catch。

## 3. 契约 A：失败提示回到会话流（`agent-chat.js` + `agent-chat.css`）

- `showBoardNavFailure()` 不得再新建独立行节点、不得直接 `tinner.append(...)`；
  失败原因仍用既有 `noteInThread(...)`（会话流内的普通消息）输出。
- 「重试」控件保留（复用既有 `boardNavigateView(view, payload)`），但必须**内联在同一条失败消息里**
  —— 做法可以是给 `noteInThread(text, extras)` 增加第二个参数（把节点追加到同一条消息的 `oc-abody` 内），
  或等价地把重试按钮放进这条消息；不允许再有第二个独立节点。
- `agent-chat.css` 删除 `.oc-retry-row` 规则（含它的 `order: 1`）。
  全仓 CSS 里带 `order: 1` 的规则只允许剩 `.oc-result-actions` 一条。
- `pushSystem()`（流内消息）与 `.oc-err-line` 视觉保持不变。

## 4. 契约 B：`silent` 动作不出任务卡（`tech-board-runtime.js` + 九处刷新动作）

- 动作条目支持可选 `silent: true`（默认 false，不影响既有条目）。
- `runEntry()` 内部新增一个只负责卡片事件的本地helper（建议 `publishTaskCard(eventName, payload)`）：

  ```js
  function publishTaskCard(eventName, payload) {
    if (entry.silent === true) return;      // 刷新类动作不产生进度 / 完成 / 失败卡
    publish(eventName, name, payload);
  }
  ```

  并把 `EVENT.TASK_PROGRESS` / `EVENT.TASK_COMPLETED` / `EVENT.TASK_FAILED` 三处发布
  **全部**改走它；`runEntry` 里不得再出现对这三个事件的直接 `publish(...)`。
  `ACTION_STATE`、`SELECTION_CHANGED`、`READY` 与 `deferred` 语义一律不变。
- 九处刷新动作的条目加 `silent: true`（其余字段不动，`visible: false` 保留）：

  | 文件 | 动作名 | 标签 |
  | --- | --- | --- |
  | `app.js` | `refreshData` | 刷新看板数据 |
  | `requirement-create.js` | `refreshData` | 刷新需求看板 |
  | `requirement-confirm-page.js` | `refreshData` | 刷新需求确认页 |
  | `requirement-review-page.js` | `refreshData` | 刷新需求审核页 |
  | `assembly-integration.js` | `refreshIntegration` | 刷新整合看板 |
  | `cost-review.js` | `refreshCostReview` | 刷新成本看板 |
  | `summary-result.js` | `refreshProcessReport` | 刷新汇总报告 |
  | `report-review-result.js` | `refreshProcessReport` | 刷新审核报告 |
  | `report-publish-result.js` | `refreshProcessReport` | 刷新发布报告 |

- `entryState()` 的字段契约（`label/visible/enabled/busy/active/analyzed/role/order/hint`）不变，
  快照形状（`payload.actions`）不变；`silent` 只是条目上的执行期标记。
- 普通长任务（`parseDrawing` / `runIntegration` / `runCostReview` / `saveProcessReport` …）
  继续照旧出「运行中 → 已完成」卡，`renderTaskProgress()` 与 `.oc-task-card` 一律不动。
- 刷新失败不能因此消失：失败仍要可见，但走普通输出通道
  （命令回执被调用方 catch → `noteInThread` / `pushSystem`，或父壳标题行提示），
  不允许出现「既没有卡也没有任何提示」的静默失败。

## 5. 契约 C：refresh 调用方不得静默吞错（`agent-chat.js`）

- `refreshBoardAfterUpload()`（`agent-chat.js:1500-1510`）里
  `.catch(() => {})` 改成把真实原因作为普通输出写进会话（`pushSystem(...)` / `noteInThread(...)`）。
- `boardNavigateView()` / `dispatchBoardAction()` 既有的失败输出保持。

## 6. 非目标与保护边界

- 不改 `.oc-result-actions { order: 1 }`（结果入口常驻底部是有意设计）与 `#ocTaskProgressHost` 宿主。
- 不改任务卡机制、`renderTaskProgress()`、`.oc-task-card` 四态样式与按 taskId 去重规则。
- 不改 `cpq:tech-board` 信封、事件白名单、`payload` 形状、`entryState()` 字段契约；
  不改 `deferred` / `keepActionState` / `visible` 语义。
- 不改后端任何路由、任务模型与 progress_log 语义；不新增前端轮询。
- 不改标题行提示位（`#techContextNotice` 与 `.is-error`）—— 那是另一条通道，本批不动。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 7. 验收

- `python3 -m unittest tests.test_chat_errors_inflow_and_drop_refresh_task_cards_red -v` 全绿。
- 回归：`tests.test_tech_business_actions_clickable_then_error_red`、
  `tests.test_tech_step_primary_and_drawing_entry_cleanup_red`、
  `tests.test_chat_fused_assistant_card_style_red`、`tests.test_tech_board_bridge_protocol_red`。
- `node --check` 覆盖 `tech-board-runtime.js`、`agent-chat.js` 与九个 stage 页脚本；`git diff --check` 通过。
- 浏览器：① 制造一次看板导航失败，失败原因与「重试」一起出现在会话流里，
  继续聊天后它跟着往上滚，不会钉在底部；② Agent 触发一次看板刷新（`refresh-data`）后，
  会话里不再出现「刷新需求看板 / 刷新需求确认页 / … 已完成」卡片；
  ③ 点「开始解析」仍然出现正常的任务进度卡。

## 8. 对应测试

`tests/test_chat_errors_inflow_and_drop_refresh_task_cards_red.py`
