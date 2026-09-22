# 技术工艺 / 报价 助手卡片统一：一层边框 + 去头像 + 几何与 token 对齐

状态：Spec + 红测（已实现）
红测：`tests/test_tech_quote_assistant_card_unification_red.py`

取代：`docs/specs/chat-fused-assistant-card-style.md` 中「技术侧卡框用 `--oc-border-2`、报价侧用
`--border-color`」以及「`.oc-art` / `.oc-intent-card` 各自是白底带框卡」这两处写法 —— 那两个 token
不是同一个颜色（`#d9d9e3` vs `#e7e7ea`），也正是两边观感分叉的源头。融合风格本身（白卡 + 内容默认展开 +
只有思考过程折叠 + 右上角状态 chip + 报价那行蓝色身份字）继续有效。

## 一、问题

一次助手回复在技术侧出现**两层边框**：外层 `.oc-amsg` 一张带框白卡，里面再套一张带框卡
（`.oc-intent-card`、`.oc-art`、`.oc-process-card`、`.oc-match-card`）。报价侧同样位置只有一层卡。
另外技术侧多一个 28px 渐变头像（`✦` / `¥`），报价侧没有；两边卡的边框色、消息间距、用户气泡几何
也各写一套。

用户口径：**技术侧去掉头像、改成报价同款蓝色身份行头部；不要两个边框卡片，只保留报价那样的一个；
间距等几何一并对齐。**

## 二、目标结构

一次助手回复 = **一张白卡 + 一行蓝色身份行 + 卡内无边框区块**：

```
┌──────────────────────────────────────────────┐
│ ● 技术工艺智能体                   ◌ 运行中   │  ← 唯一身份行（蓝字 + 右状态 chip）
├──────────────────────────────────────────────┤
│ ▸ 思考过程（默认折叠，无边框区块）             │
│ 正文 markdown                                 │
│ 工具轨迹行（无边框）／步骤列表（无边框）        │
└──────────────────────────────────────────────┘
        ↑ 整轮只有这一处 border
```

## 三、契约

### C1 技术侧去头像，头部改为报价同款蓝色身份行

- 删除 `.oc-aav` 规则，以及**所有** `oc-aav` 节点，涉及：
  `agent-chat.js`（`addAssistant()` / `pushSystem()` / 零部件库检索结果卡）、
  `assembly-integration.js`（`aiSay()` / `aiProcessCard()` / `aiAgentTurn()`）、
  `cost-review.js`（`crSay()` / `crCard()`）、
  静态页 `assembly-integration.html` / `cost-review.html` / `index.html`。
- 每张助手卡保留 `.oc-abody`，并在其中保留 `.oc-alabel`（技术工艺智能体 / 成本测算）+ `.oc-alabel-state` 状态 chip；
  报价 `.message-label` 文案不变。
- `.oc-amsg` **保持 `display: flex`**（既有契约 `tests/test_quote_tech_ai_message_white_surface_red.py`
  第 52 行要求）；去掉头像后它只有一个子节点，视觉上就是单列。

### C2 只保留一层边框（不得卡中卡）

- `.oc-amsg` 是助手回复里**唯一**带 `background` + `border` 的容器。
- 下列卡内区块**不得再有** `background` 与 `border`：
  `.oc-intent-card`、`.oc-art`、`.oc-thinking`、`.oc-process-card`、`.oc-match-card`。
  报价侧 `.thinking-block` 同步去掉 `background` 与 `border`。
- 允许保留的分隔/点缀（不是框）：
  `.oc-art-detail` 的 `border-top: .5px solid var(--oc-border-3)`、
  `.oc-atile` 的 36px 图标块底色、`.oc-atxt.rendered pre` 代码块底色。
- `.oc-task-card` 不在 `.oc-amsg` 内（它是同级卡），保留自己的框，但必须与 `.oc-amsg`
  **同一边框 token、同一圆角、同一内边距、同一白底**，让两者看起来是同一套卡。

### C3 边框 token 两边同值

- 技术侧 `.oc-amsg` / `.oc-task-card` 的边框改用 `var(--oc-border-3)`（= `#e7e7ea`），
  与报价 `--border-color`（= `#e7e7ea`）同值；不得再用 `var(--oc-border-2)`（= `#d9d9e3`）。
- 报价侧 `.message-ai` / `.tool-activity.trace` 维持 `var(--border-color)` 不变。

### C4 几何对齐

- `.oc-tinner` 的 `gap` 由 `16px` 改为 `14px`（对齐报价 `.chat-messages`）。
- `.oc-ubub`：`border-radius: 14px`、`border-bottom-right-radius: 4px`、`padding: 11px 14px`、
  `max-width: 92%`（对齐报价 `.message` / `.message-user`）。
- `.oc-atxt.rendered :not(pre) > code` 圆角 `5px` → `4px`（对齐报价 `.message-text code`）。
- `.oc-amsg` 与 `.message-ai` 必须逐项同值：白底、`1px solid`、`border-radius: 14px`、`padding: 11px 14px`。

### C5 状态 chip 收敛成一套

- 阶段页 `aiProcessCard()` / `crCard()` 不再各自渲染 `.oc-process-head` / `.oc-process-title` /
  `.oc-process-state`，改成与 `addAssistant()` 同一张卡：`.oc-amsg` + `.oc-abody` +
  `.oc-alabel`（含 `.oc-alabel-state`）+ 步骤列表 `.oc-process-steps`。
- 步骤行 `.oc-process-step` / `.oc-process-step.sub` / `.oc-process-dot` 保留，仍是无边框区块。

### C6 不放宽

- 思考过程仍是唯一默认折叠项之一，`<details class="oc-thinking">` 不带 `open`；工具「详情」
  `details.oc-art-detail` 同样默认折叠。
- 工具轨迹主行仍显示中文业务文案（`.oc-art-name`）、副标题、状态位（◌ / ✓ / ⚠）与可展开原始载荷。
- `.oc-task-card` 仍按 `taskId` 一张卡、`.oc-task-steps` 仍默认展开、`renderTaskProgress()` 的空判据不变。
- `splitReasoningSections`、`.oc-ubub` 主色实心用户气泡、桥协议与既有路由全部不动。

## 四、验收

- `python3 -m unittest tests.test_tech_quote_assistant_card_unification_red -v` 全绿；
- 被本方案取代的既有断言已同步改写：
  `tests/test_chat_fused_assistant_card_style_red.py`（内层卡不再是各自一张白框卡）、
  `tests/test_tech_chat_drop_red_error_cards_red.py`（不再要求 `oc-aav` 头像）；
- 回归 `tests.test_chat_collapsible_thinking_trace_red`、`tests.test_tech_tool_trace_business_line_detail_red`、
  `tests.test_quote_tech_ai_message_white_surface_red`、`tests.test_quote_tech_user_message_primary_bubble_red`、
  `tests.test_tech_chat_card_noise_and_quiet_board_failures_red` 全绿；
- 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 无新增失败。

## 五、不在本批范围

报价侧工具轨迹行（`.tool-activity.trace`）仍是独立同级行，不并入 `.message-ai`；
技术侧 `.oc-task-card` 仍是独立同级卡，不并入助手卡；`.oc-chip` / 结果按钮组、右侧看板与后端一律不动。
