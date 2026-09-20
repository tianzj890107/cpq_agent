# 助手卡片融合风格：技术白卡 + 报价蓝色身份行 + 状态图标（技术工艺 + 报价）

状态：TDD Red，等待 DeepSeek 实现。

取代：`docs/specs/chat-white-bubble-and-expandable-run-progress.md`（该方案把运行过程做成
默认折叠、label 为灰色，已被本方案替换）；对应红测
`tests/test_chat_white_bubble_and_expandable_run_progress_red.py` 同步替换为
`tests/test_chat_fused_assistant_card_style_red.py`。

## 1. 已确认的融合口径

用户确认：**保留技术工艺的卡片结构，内容默认展开；只有「思考过程」折叠；
卡片右上角的运行中 / 已完成状态图标保留；同时保留报价「报价单智能体」那行蓝色身份字。**

三轮拍板结果：

1. 状态 chip 放在**标题行右侧**（不再做卡片外悬浮角标）。
2. 报价侧同样加同一个状态 chip，两个 Agent 风格一致。
3. 工具卡的「详情」（原始入参 JSON，排障入口）**继续默认折叠**。

## 2. 最终结构

一轮助手回复 = 一张白底带边框的卡，卡内三段：

```
┌──────────────────────────────────────────────┐
│ ● 技术工艺智能体                   ◌ 运行中   │  标题行：左身份行（蓝字）+ 右状态 chip
├──────────────────────────────────────────────┤
│ ▸ 思考过程                                    │  唯一默认折叠项（仅有内容时出现）
│ 正文 markdown                                 │  默认展开
│ 工具轨迹卡 / 结果卡 / 表格 / 结果入口          │  默认展开
└──────────────────────────────────────────────┘
```

插入顺序固定为：**身份行 → 思考过程折叠块 → 正文文本**。

## 3. 契约 A：技术工艺卡片（`agent-chat.css` + `agent-chat.js`）

```css
.oc-amsg {
  display: flex; gap: 11px; background: #ffffff;
  border: 1px solid var(--oc-border-2); border-radius: 14px; padding: 11px 14px;
}
.oc-art, .oc-task-card, .oc-intent-card { background: #ffffff; border: 1px solid var(--oc-border-2); }
.oc-alabel {
  display: flex; align-items: center; gap: 6px; margin-bottom: 4px;
  color: #0060E6; font-size: 11px; font-weight: 500;
}
.oc-alabel::before {
  content: ''; width: 6px; height: 6px; flex-shrink: 0;
  border-radius: 50%; background: linear-gradient(135deg, #0060E6, #0050C4);
}
.oc-alabel-state {
  margin-left: auto; flex-shrink: 0; padding: 1px 8px; border-radius: 999px;
  background: var(--oc-bg-3); color: var(--oc-text-2); font-size: 11px; font-weight: 600;
}
.oc-alabel-state.is-running { background: #e0edff; color: #0050C4; }
.oc-alabel-state.is-succeeded { background: #dcfce7; color: #15803d; }
.oc-alabel-state.is-failed { background: #fee2e2; color: #b91c1c; }
```

- 蓝色身份行与报价 `.message-label` 同一视觉（报价 `--color-secondary: #0060E6`、
  `--gradient-ai: linear-gradient(135deg,#0060E6,#0050C4)`）；技术侧不依赖未定义 token，
  直接写同值字面量。
- `addAssistant()` 在 `.oc-abody` 内、文本节点之前插入
  `<div class="oc-alabel"><span>技术工艺智能体</span><span class="oc-alabel-state is-running">◌ 运行中</span></div>`（新增节点，不改原文本节点与既有 `.oc-atxt`）。
- 新增 `setAssistantState(ctx, state)`（`running` / `succeeded` / `failed`）就地翻转同一张 chip 的
  class 与文案：`◌ 运行中` / `✓ 已完成` / `⚠ 失败`；`done` 置 `succeeded`，`error` 置 `failed`。
  一轮只允许一个 chip，**不得**为状态变化新增第二行或第二张卡。
- `pushSystem()` 的告警卡不带 chip，只保留 `.oc-err-line`。
- 既有圆角、内边距、间距、字号保持现值，只换底色与边框。

## 4. 契约 B：默认展开（本批的核心口径）

- 助手正文、`.oc-art` 工具轨迹、`.oc-task-card` 步骤（`.oc-task-steps`）、结果卡、`.oc-intent-card`
  **全部默认可见**，不加任何折叠包裹。
- 唯一默认关闭的两个 `<details>`：`details.oc-thinking`（思考过程）与
  `details.oc-art-detail`（工具原始载荷，排障用）。
- **不得**引入 `oc-task-fold` / 「过程详情 · N 步」这类把运行过程默认收起的结构；
  本方案明确取消上一稿的该条款。
- 任务卡头部（`.oc-task-head` + `.oc-task-title` + `.oc-task-state`）与四态 chip
  （`is-queued` / `is-running` / `is-succeeded` / `is-failed`）保持不变。

## 5. 契约 C：任务事件去噪（沿用上一稿，未变）

- `tech-board-runtime.js::runEntry()` 的三处 publish 补 `label`（`entryState(name).label`）
  与 `taskId`（`context.taskId || ''`）。
- 看板页 `app.js::pollTask()` 与 `inline-analysis.js` 把同一份
  `{ label, taskId, status, progress, log, error }` 经
  `window.TechBoardRuntime && TechBoardRuntime.publish(事件名, 'board-task', detail)` 转给父壳；
  事件名按 `status` 取 `task-progress` / `task-completed` / `task-failed`；无运行时行为不变。
- `agent-chat.js::renderTaskProgress()`：`label` 不再兜底「处理中」；
  `label / taskId / log / progress` 全空直接 `return`，不建空卡。

## 6. 契约 D：报价侧同步（`确认需求解析结果.html`）

```css
.message-ai { background: #ffffff; border: 1px solid var(--border-color); border-radius: 14px; padding: 11px 14px; }
.tool-activity.trace {
  color: var(--text-secondary); background: #ffffff;
  border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 12px; gap: 7px;
}
.message-label-state {
  margin-left: auto; flex-shrink: 0; padding: 1px 8px; border-radius: 999px;
  background: var(--bg-tertiary); color: var(--text-secondary); font-size: 11px; font-weight: 600;
}
.message-label-state.is-running { background: #e0edff; color: #0050C4; }
.message-label-state.is-succeeded { background: #dcfce7; color: #15803d; }
.message-label-state.is-failed { background: #fee2e2; color: #b91c1c; }
```

- `.message-label` 文案「报价单智能体」保留，`justify-content: space-between`（或 chip
  `margin-left: auto`）让 chip 靠右。
- `ensureStreamBubble()` 生成的 `.message-label` 里带 `is-running` chip；
  `finishStreamBubble()` 置 `is-succeeded`（`✓ 已完成`）；`addErrorBubble()` 置 `is-failed`（`⚠ 失败`）。
- 用户气泡 `.oc-ubub` / `.message-user` 不动。
- **禁止** `.oc-amsg` / `.oc-art` / `.oc-task-card` / `.oc-intent-card` / `.message-ai` /
  `.tool-activity.trace` 的规则里继续出现 `var(--bg-secondary)`、`var(--oc-bg-2)`、`var(--oc-bg-3)`；
  卡内代码块、chip、表头等次级元素不受影响。

## 7. 非目标与保护边界

- 不改 `cpq:tech-board` 信封、六个 state 事件、`tech:command` 方向与 origin/projectId/stage 校验。
- 不改 `pollTask()` 轮询与提交逻辑、不改后端任务接口、不新增后端路由。
- 不删 `pushTaskStep` / `toneOf` / `sanitizeTaskDetail` / `setTaskStatus` / `taskProgressHost` /
  `.oc-task-card` / `.oc-task-steps` / `.oc-art-detail`，也不删报价
  `addToolActivity` / `showStage` / `showTyping` / `describeTool` 及其调用点。
- 思考过程折叠块本身见 `docs/specs/chat-collapsible-thinking-trace.md`（后端 `thinking` 帧、
  `appendThinking()` / `appendThinkingText()`、报价 `splitReasoningSections()`），本批不改其契约。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 8. 验收

- `python3 -m unittest tests.test_chat_fused_assistant_card_style_red -v` 全绿。
- 回归：`tests.test_chat_collapsible_thinking_trace_red`、
  `tests.test_tech_step_status_in_context_row_red`、`tests.test_tech_board_bridge_protocol_red`、
  `tests.test_quote_tech_chat_composer_alignment_red`。
- `node --check` 覆盖 `agent-chat.js`、`app.js`、`inline-analysis.js`、`tech-board-runtime.js`。
- 浏览器：技术侧每张助手卡白底带边框，标题行「● 技术工艺智能体 … ◌ 运行中」，
  正文/工具轨迹/步骤默认展开，只有「思考过程」和工具「详情」折叠；
  报价侧 `.message-ai` 与轨迹行白底带边框、身份行同款 chip；用户气泡保持主色实心。

## 9. 对应测试

`tests/test_chat_fused_assistant_card_style_red.py`
