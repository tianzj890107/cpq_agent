# 报价 / 技术工艺 Agent 会话统一：先用户消息、统一执行卡与 Tool List、详情与思考可折叠、Agent 消息白底（用户气泡保持蓝色）

状态：TDD Red，等待实现（DS2）。

对应红测：`tests/test_quote_tech_unified_tool_list_conversation_red.py`

取代关系（本批明确宣告，实现时必须遵守）：

- **用户气泡配色不在本批范围内**：`docs/specs/quote-tech-user-message-primary-bubble.md`
  （用户消息 = 系统主色实心蓝底 + 白字）**继续有效**，本批只统一 **Agent 侧**消息与业务卡的
  白底，不改用户气泡。用户已明确纠正：用户气泡必须是原来的蓝色背景。
- 本 Spec §11「详情折叠」与 §12「思考折叠」是
  `docs/specs/chat-fused-assistant-card-style.md`、`docs/specs/tech-quote-assistant-card-unification.md`、
  `docs/specs/chat-collapsible-thinking-trace.md`、`docs/specs/tech-agent-tool-trace-business-line-detail.md`
  的**叠加**，不是取代：融合白卡、单层边框、`.oc-amsg` 唯一边框容器、状态 chip、
  既有 `pushTaskStep` / `addToolCard` / `setToolResult` 管线全部继续有效。
- 本 Spec 不新增第三套卡片。技术侧 root 仍是 `.oc-amsg`（任务卡 `.oc-amsg.oc-task-card` 是其 modifier），
  报价侧 root 仍是 `.message.message-ai`。统一的是**结构角色（`data-agent-role`）与 Tool List 语义**，
  不是重命名现有 class。

---

## 1. 背景与真实问题

用户原话（本批）：

> 这个卡片现在所有文字都在同一行，而且没有输出大模型的思考过程和工具执行过程
> …〔另外〕我提问的时候就有很多很好的展示工具调用之类的，但是直接执行那些的时候就没有
> 这些很好看的思维链啊工具啊之类的

三个可复现的产品问题：

1. **同一件事有两种卡片**：用户自己在输入框提问，走的是「助手回复卡」；用户点右侧/左侧按钮
   触发（一键解析、生成推荐、跑成本、诊断、确认、审核、发布），走的是「执行过程卡」
   （技术侧 `.oc-process-steps` 裸行；看板页 `aiProcessCard` / `crCard`；报价侧
   `.tool-activity.trace`）。两种卡外观、信息层级、可展开性都不同。
2. **按钮触发时没有「我：…」**：用户点按钮 → 会话区直接冒出一张 Agent 卡，看不出是"谁让做的"。
   技术侧已有 `echoTaskPrompt()`（`agent-chat.js:510`）覆盖 5 个看板动作，但报价侧
   kickoff / `runStep1` / 转交任务仍无用户气泡；技术侧也不覆盖「一键解析」之外的按钮。
3. **过程行是拼好的中文 bullet，不能展开**：查询条件、库内条数、回退数量、待补项这些
   结构化事实被拼成一行行中文，收不进可展开的详情里；思考过程只在提问路径有
   `.oc-thinking`，执行路径没有。

## 2. 用户角色与用户故事

- **销售经理（报价）**：点「一键解析需求」「强行填满本步骤」「确认，进入下一步」「转交任务」后，
  我要先看到我这句话，再看 Agent 的卡。
- **工艺技术员 / 工艺经理（技术工艺）**：点「一键解析图纸」「重新生成工艺推荐」「跑成本测算」
  「确认」「审核」「发布」后，同上。
- **两者共同**：卡里的每个业务步骤仍是一张卡；卡里的过程是一份 Tool List，能收起、能展开看
  查询条件与明细；思考过程能折叠展开；Agent 消息统一白底，用户气泡保持原来的蓝色。
- **回看历史的人**：重进项目后，顺序、折叠态、Tool List 与当时一致；历史里没有的用户话术
  不许凭空补。

## 3. 入口盘点（务必逐个核对，不得只改 `sendMessage()`）

> **盘点时点说明**：下面两张表建于 `## 125` 之前。`## 125` 已经落地了统一执行卡 root 与
> `data-agent-role`、Tool Item 四态、看板页 `aiUserSay` / `crUserSay`、「先用户消息后出卡」
> （含 kickoff / `runStep1`）与思考折叠。**本修正批只剩两件事要看**：
> ① 用户气泡必须保持原来的蓝色（`## 125` 误改成白底）；
> ② 过程明细必须去掉独立「详情」、改成点标题行展开，缩进子项折进父行（`## 125` 未做）。
> 表中「当前是否违反新合同」列已按此口径回填为修正批的真实状态。

### 3.1 技术工艺侧（`tech_app/frontend/`）

| # | 触发元素 | 调用函数 | 先生成用户消息？ | 用户文案来源 | Agent 卡构造 | 持久化 | 刷新恢复 | 当前 DOM | 违反新合同 |
|---|---|---|---|---|---|---|---|---|---|
| T1 | 底部输入框 `#ocInput` + `#ocSend` | `send()` → `addUser` → `addAssistant` | 是 | 用户输入 | `addAssistant` | `kind:user` + `assistant` | 是 | `.oc-ubub` / `.oc-amsg` | 否 |
| T2 | 快捷能力 `#ocQuestionsAction` / `#ocReportAction` 等 `oc-chip` | `refreshResultChips` → `agentResultChip` | 否 | — | 打开看板视图 | 否 | 否 | `.oc-chip` | 否（不产卡） |
| T3 | 主按钮 `#techChatPrimary` | `dispatchBoardAction` / `techChatPrimary` 绑定 | 否（看板动作自带 `prompt` 时才有） | 看板动作 `prompt` | 看板页 `aiProcessCard` / `crCard` | `session-note` / `task` | 是 | `.oc-amsg`（看板页 iframe 内） | **部分**：只有 5 个动作声明 `prompt` |
| T4 | 看板页按钮（`aiStart` / `aiConfirm` / `crRunAll` / `crRetryFailed` / `aiConfirm`） | 看板页函数 | **否** | — | `aiProcessCard` / `crCard` / `aiSay` | `session-note`（board） | 是 | `.oc-amsg` + `.oc-process-steps` | **是** |
| T5 | 「重新生成工艺推荐 / 重新生成」 | `aiProcessCard` 调用点 | **否** | — | 同上 | 同上 | 是 | 同上 | **是** |
| T6 | Agent 主动 `ui_action`（parse / extract-requirement / refresh-*） | `handleEvent` → `requestParse` 等 | 由 `echoTaskPrompt` 覆盖 5 例 | 看板动作 `prompt` | 看板页 | `kind:user` echo | 是 | 同上 | **部分** |
| T7 | 历史回放 | `renderHistory` / `replayTimelineTask` | 否（不得伪造） | 落库的 `kind:user` | `addAssistant` / `replayTimelineTask` | — | 是 | 同上 | 否 |
| T8 | 系统提示（`pushSystem`） | `noteInThread` / `techShellConn` | 否（不得伪造） | — | `.oc-amsg` + `.oc-alabel` | `session-note` | 是 | 同上 | 否 |
| T9 | 错误消息 | `handleEvent(error)` / `boardFailureNotice` | 否 | — | 同一张卡改 `is-failed` | 同一轮 | 是 | 同上 | 否 |
| T10 | 任务进度 / tool trace | `renderTaskProgress` / `persistTaskCard` | echo（有 `prompt` 时） | 看板动作 `prompt` | `.oc-amsg.oc-task-card` | `kind:task` | 是 | 同上 | **部分** |
| T11 | 需求/流程摘要卡 | `renderRequirementSummary` / `renderRequirementFlowSummary` | 否（由 T6 决定） | — | **裸 `.oc-amsg` 手写 innerHTML**（`agent-chat.js:1058` / `:1160`） | 无 | 否 | `.oc-req-summary` / `.oc-req-flow` | **是**（绕过统一构造器） |
| T12 | 零部件库检索结果卡 | `rematchButton` / 检索结果卡（`agent-chat.js:1719`） | 否 | — | **裸 `.oc-amsg`** | 无 | 否 | `.oc-match-card` | **是** |
| T13 | 确认卡 `techUiRequestConfirmation` | `runTechUi` | 否（弹确认卡，不入会话流） | — | **裸 `.oc-amsg` + `.oc-confirm-card`** | 否 | 否 | `.oc-confirm-card` | 否（确认卡不算业务步骤卡，但需带 root 标记） |

技术侧需收敛的**绕过点**（`agent-chat.js` 内手写 `oc-amsg` 字符串的位置）：
`renderRequirementSummary`、`renderRequirementFlowSummary`、`noteInThread`、零部件库检索结果卡、
`techUiRequestConfirmation`，以及阶段页 `assembly-integration.js:aiSay/aiUserSay/aiProcessCard/aiAgentTurn`、
`cost-review.js:crSay/crCard`。

### 3.2 报价侧（`确认需求解析结果.html` 内联脚本）

| # | 触发元素 | 调用函数 | 先生成用户消息？ | 用户文案来源 | Agent 卡构造 | 持久化 | 刷新恢复 | 当前 DOM | 违反新合同 |
|---|---|---|---|---|---|---|---|---|---|
| Q1 | 输入框 `#chatSend` / Enter | `sendFromInput` → `addUserBubble` → `sendToAgent` | 是 | 输入 / 附件名 | `ensureStreamBubble` | `/api/send` + 事件 | 是 | `.message-user` / `.message-ai` | 否 |
| Q2 | 快捷「强行填满本步骤」 | `fillStepRecommend` | 是 | 固定中文句 | `ensureStreamBubble` | 同上 | 是 | 同上 | 否 |
| Q3 | 「确认，进入下一步」 | `confirmStep` | 是 | `确认第 N 步「…」…` | `ensureStreamBubble` | 同上 | 是 | 同上 | 否 |
| Q4 | 「返回上一步」 | `prev` 绑定 | 是 | 固定中文句 | `ensureStreamBubble` | 同上 | 是 | 同上 | 否 |
| Q5 | 「修改第 N 步并保存」 | 表单保存 | 是 | 固定中文句 | `ensureStreamBubble` | 同上 | 是 | 同上 | 否 |
| Q6 | 新建报价「开始」（kickoff） | `wfStartStep` → `PENDING_KICKOFF` → `runStep1` | **否** | — | `ensureStreamBubble` | 同上 | 是 | 同上 | **是** |
| Q7 | 第 1 步表单填写通道 | `runStep1` → `sendToAgent(task,{echo:false,display:''})` | **否** | — | 同上 | 同上 | 是 | 同上 | **是** |
| Q8 | 「转交任务」成功后说明 | `$('wfSend').onclick` → `addAiBubble` | **否** | — | `addAiBubble` | 无 | 否 | 同上 | **是** |
| Q9 | 切换模型后的系统说明 | 设置弹窗保存 | 否（系统通知，不得伪造） | — | `addAiBubble` | 无 | 否 | 同上 | 否 |
| Q10 | 导入数据库（本地直连） | 导入处理 | 是（`addUserBubble('导入数据库')`） | 固定中文句 | `addAiBubble` | 无 | 否 | 同上 | 否（非 Agent） |
| Q11 | 历史回放 | `ev.type==='user'` / `'text'` | 否（不得伪造） | 落库 | `addAiBubble` | — | 是 | 同上 | 否 |
| Q12 | 错误气泡 | `addErrorBubble` | 否 | — | `.message-ai.message-error` | 同一轮 | 是 | 同上 | 否 |
| Q13 | 候选产品块 | `renderCandidates` | 否（由 Q1/Q3 决定） | — | `.message-ai.cand-block` | 无 | 否 | 同上 | **是**（绕过统一构造器） |
| Q14 | 工具轨迹 / 阶段行 | `addToolActivity` / `showStage` | 否 | — | `.tool-activity.trace` | 无 | 否 | `.tool-activity.trace` | **是**（第二套过程样式） |

> 报价侧**没有** Task Card。技术侧的「执行过程」= `.oc-process-steps`；报价侧的「执行过程」=
> `.tool-activity.trace`。本批要求两端都能映射成 Tool List，但**不要求**两端合并成一个文件或一个函数。

## 4. 当前流程 → 目标流程

当前：

```
[按钮] → (可能) 无用户气泡 → Agent 卡 / 过程行（裸 bullet）
```

目标：

```
[按钮]
  → 用户气泡（.oc-ubub / .message-user，系统主色实心蓝底 + 白字，自然语言）
  → 统一执行卡 root（.oc-amsg / .message.message-ai）
       ├─ header：身份 + 业务步骤标题 + 状态
       └─ body
            ├─ Tool List（每条 = 状态 icon + 标题 + 次级信息 + 可选「详情」折叠）
            └─ 思考过程 折叠（仅有内容时）
```

## 5. 状态定义、状态映射与状态转换

统一**归一化状态**（写进 root 的 `data-status`，与既有 class modifier 并存，不替换）：

| `data-status` | 技术 class | 报价 class | icon | 文案 | 说明 |
|---|---|---|---|---|---|
| `pending` | `is-queued` | `is-pending`（可缺省） | `○` | 排队中 / 待处理 | 后端真有该状态才用 |
| `running` | `is-running` | `is-running` | `◌` | 运行中 | 可旋转 spinner |
| `completed` | `is-succeeded` | `is-succeeded` | `✓` | 已完成 | 标题可弱化 / 删除线，不得低到看不清 |
| `failed` | `is-failed` | `is-failed` | `⚠` | 失败 | 错误详情完整保留，可展开 |
| `interrupted` | `is-interrupted` | `is-interrupted` | `⏸` | 中断 | 技术侧真实存在，沿用 |

转换：`running → completed | failed | interrupted`，就地翻转同一张 chip，**不得**为状态变化新增
第二行、第二张卡。Tool Item 的 `data-state` 用同一套词表（pending/running/completed/failed）。

## 6. 接口 / 数据契约（仅前端，不改后端）

本批**不新增、不修改**任何后端接口、SSE 帧名、数据库结构。前端内部新增的唯一契约是：

### 6.1 统一执行卡构造器（每端各一个）

- 技术侧：`agent-chat.js` 内新增唯一构造器（建议名 `execCard(opts)`），返回
  `{ root, header, body, tools, statusChip }`；`addAssistant` / `pushSystem` / 任务卡 /
  需求摘要卡 / 流程摘要卡 / 检索结果卡 / 确认卡 / 看板页 `aiProcessCard` · `crCard` **都必须**经它建 root。
- 报价侧：`确认需求解析结果.html` 内新增同名构造器（建议名 `execCard(opts)`），
  `ensureStreamBubble` / `addAiBubble` / `addErrorBubble` / 候选块 / 轨迹行 **都必须**经它建 root。

### 6.2 统一 DOM 角色标记（additive，不重命名既有 class）

| `data-agent-role` | 位置 | 语义 | 必有 |
|---|---|---|---|
| （root）`data-agent-card` | root 元素 | 一个业务步骤 = 一张卡 | 是 |
| （root）`data-status` | root 元素 | 归一化状态 | 是 |
| `header` | root 内 | 头条区（身份 / 标题 / 状态） | 是 |
| `identity` | header 内 | Agent 身份（技术「技术工艺智能体」/ 报价「报价单智能体」） | 是 |
| `title` | header 内 | 业务步骤标题（无标题时可缺省） | 否 |
| `status` | header 内 | 状态 chip | 是（running/failed 一定有） |
| `body` | root 内 | 正文区 | 是 |
| `tools` | body 内 | Tool List 容器 | 有过程时必有 |
| `tool-item` | tools 内 | 一条业务过程 | 是 |
| `tool-title` | tool-item 内 | 过程主文案 | 是 |
| `tool-subtitle` | tool-item 内 | 次级信息（查询条件摘要、命中件名等） | 否 |
| `tool-detail` | tool-item 内 | `details`，结构化明细 | 有结构化明细时必有 |
| `thinking` | body 内 | `details`，思考过程 | 有思考内容时必有 |

### 6.3 Tool Item 数据适配

`pushTaskStep(card, text, tone, phase, detail)`（技术）与过程行渲染（报价）必须在保留
**完整原文**的前提下，把一条过程适配成 `{state, title, subtitle, detail}`：

- `title`：原文主体（去掉 `↳` / `·` 前缀与用于缩进的前导空格后原样保留，**不得**改成"查询数据"
  这类概括词）；
- `subtitle`：`detail` 里的可读摘要（如 `length=70、width=18、thickness=1.6、hole_diameter=2，材料 FR-4`），
  有则填，无则省略；
- `detail`：现有 `oc-process-detail` / `oc-art-detail` 的完整输入输出，**一字不减**；
- `state`：`hit` → `completed`、`miss` → `completed`（带 miss 语义色）、`err` → `failed`、
  `model`/`tool` 运行中 → `running`，其余默认 `completed`。

## 7. 正常路径

1. 用户点按钮 → 立即插入用户气泡（DOM 先于 Agent 节点）。
2. 同一 turn 的 `correlationId`/`turn` 记在该轮 ctx 上，Agent 卡与用户气泡成对。
3. 用户气泡进现有持久化链（技术 `persistSessionEvent({kind:'user'})`；报价走既有会话事件），
   刷新后按 `seq` 恢复在同位置。
4. 执行卡按 §6.2 渲染；过程进 Tool List；有结构化明细的 Tool Item 长出默认折叠的「详情」。
5. 有思考内容时，卡底部出现默认折叠的「思考过程」，可展开可收起。
6. 状态就地翻转。

## 8. 异常路径

- Agent 执行失败：**用户气泡必须已经在**，失败原因落在同一张卡的 `data-status="failed"` 上，
  不新起第二张基础卡（modifier 允许）。
- 用户气泡落库失败：本地可见性不受影响（沿用既有"落库失败不阻塞会话"口径）。
- Tool Item 明细缺失：不建空 `details`（沿用既有「无明细不建详情」口径）。
- 思考内容为空：不渲染空折叠栏。
- 卡片渲染报错：不得吞掉整个 turn；已插入的用户气泡不回收。

## 9. 并发与幂等

- 连续点击 A、B 两个按钮：DOM 顺序必须是 `user A, card A, user B, card B`，
  **禁止** `user A, user B, card A, card B`。实现方式由 turn 队列 / `activeTurnCtx` /
  `busy` 门任一保证，但必须可测。
- 同一个 Agent 主动动作重复触发：沿用 `echoTaskPrompt` 的 `runId` 去重（同一 run 只出一条气泡），
  用户**手点**的每一次是新 run，**必须**都出一条气泡。
- 重放（历史恢复）幂等：同一份事件回放两次产生同样顺序，不重复插气泡、不补造气泡。

## 10. 刷新 / 重试 / 重复点击 / 服务重启后行为

- 刷新：按落库 `seq` 恢复 user / assistant / task / session-note 的相对顺序；恢复期间
  `replayingHistory = true`，**不得**补 echo 气泡。
- 重试：重试用新的 run，出新气泡 + 新卡，旧卡保留。
- 服务重启：在途任务转 interrupted（既有口径），卡片状态就地翻 `is-interrupted`，
  用户气泡不回收。
- 重复点击：见 §9。

## 11. Tool List 与详情折叠合同

### 11.1 结构

一条业务过程 = 一个 `[data-agent-role="tool-item"]`，内部只有两段：

```
<div class="oc-process-step" data-agent-role="tool-item" data-state="completed">
  <div class="oc-process-row" data-agent-role="tool-toggle"      ← 标题行本身，整行可点
       role="button" tabindex="0" aria-expanded="false">
    <span data-agent-role="tool-state-icon">✓</span>
    <span data-agent-role="tool-title">检索零部件库（1/4）：P-001 上壳</span>
    <span data-agent-role="tool-subtitle">…</span>               ← 可选
  </div>
  <div class="oc-process-body" data-agent-role="tool-detail"      ← 折叠区，默认收起
       hidden aria-hidden="true">
    …该行的结构化明细（查询条件 / 命中 / 差异 / 输入输出 JSON）…
    …以及所有缩进子项…
  </div>
</div>
```

### 11.2 硬性规则

1. **界面上不再有「详情」这个东西**。展开 / 收起的唯一点击目标就是**这一行的标题行本身**
   （`[data-agent-role="tool-toggle"]`，内含 `tool-title`）。
   - **用户可见判定（验收口径）**：行内不得出现**看得见**的「详情」标签 —— 即 `summary` / `button`
     自身文字恰为「详情」，且该节点没有被 `hidden`、`aria-hidden="true"` 或 CSS
     `display: none` / `visibility: hidden` 藏起来（伪元素规则如 `::-webkit-details-marker`、
     `summary::before` 不算隐藏标签本身）。红测 `D23 / D23b` 按这个口径判定。
   - **允许保留不可见的兼容节点**：两份既有绿测仍钉着这个节点 ——
     `tests/test_task_process_detail_red.py`（渲染出的 `summary` 文案必须是「详情」）与
     `tests/test_quote_btn_radius_and_tech_board_render_red.py`（源码里必须仍有
     `el("summary", null, "详情")`）。**最小改法**是让该 `summary` 在 CSS 里 `display: none`，
     展开仍由标题行驱动 `<details>.open`；用户看不到它，旧合同也不破。
   - 若决定**彻底删掉**该节点，必须同时退役上面两条旧断言并在 changelog 写明"合同退役"，
     不允许用其它绕行方式（例如"没有订阅者就不落盘"）迁就旧断言。
2. **缩进内容必须折进上一级父项**，不得成为与父项平级的兄弟节点。
   后端用前导 2+ 空格 / `↳` / `·` 表示"这条是上一条的结果或依据"：
   这样的行**不新建 `tool-item`**，而是追加进**上一条** `tool-item` 的 `tool-detail` 里。
   用户口径原话：「这里面所有的缩进的内容都折叠在不缩进的内容里」。
3. 没有明细、也没有缩进子项的普通行：`tool-toggle` 仍然存在（保证排版一致），
   但不渲染 `tool-detail`（不造空折叠）。
4. `tool-toggle` 必须整行可点：`hover` 浅蓝背景、鼠标指针为 pointer；
   **不得**只有右边一个小三角/小字可点。
5. 键盘与语义：`aria-expanded` 必须与展开状态同步（`"false"` ↔ `"true"`）；
   可达性用原生 `<button>` / `<summary>`，或 `role="button"` + `tabindex="0"` + Enter/Space。
6. 默认折叠；展开后内容完整，不得截断；`tool-title` 必须是原句，不得概括成
   「查询数据」「生成分析」这类空话。
7. 状态 icon：`completed`→`✓`、`running`→`◌`/`✳`、`failed`→`⚠`、`pending`→`○`。
8. Tool Item 不得成为卡片：`.oc-process-step` 等规则里不得出现 `background`（transparent 除外）
   与 `box-shadow`，也不得再套一层边框。
9. 有结构化明细的行必须保留完整输入与输出；`费率 0 条 / 回退 global 0 条 / 系数 0 条 /
   待补 10 项`、查询条件、命中件、差异、取价时间、回退数量一条都不能少。

## 12. 思考过程折叠合同

- 每张有思考内容的 Agent 卡，底部一个 `details[data-agent-role="thinking"]`（现有
  `.oc-thinking` / `.thinking-block`），默认折叠。
- 位于当前 Agent Card 内，不新建第二张消息卡；空思考不渲染空折叠栏。
- 历史恢复后同样可展开（回放允许 `assistant` 事件带 `thinking` 字段时渲染出折叠栏）。
- 不修改后端思考协议，不把内部敏感推理或 provider debug 信息新增到 UI。

## 13. 可访问性合同

- 折叠入口用原生 `<details>` + `<summary>`，天然支持 Enter/Space、焦点与 `aria-expanded` 语义；
  若坚持自绘，则必须显式维护 `tabindex="0"`、`role="button"`、`aria-expanded` 与键盘事件。
- `summary:focus-visible` 必须有可识别焦点样式（`outline`）。
- 标题行不得依赖 hover 才可见；无 hover 设备（触屏）仍然可点。
- 状态 icon 是装饰，语义由文本表达。

## 14. 白底视觉合同

- **Agent 消息**（`.oc-amsg` / `.message-ai`）：白色背景、浅灰边框、深色正文、保留圆角；
  不使用明显灰底。这是本批要统一的部分。
- **用户消息**（`.oc-ubub` / `.message-user`）：**保持原来系统主色实心蓝底 + 白字 + 右下小圆角**，
  由 `docs/specs/quote-tech-user-message-primary-bubble.md` 继续约束，本批**不改**。
  用户原话：「用户气泡应该是之前的蓝色背景，改回去。」
- 页面背景可以是浅色，但 Agent 消息与业务卡本体必须是白色。
- 工具行标题（`tool-toggle`）hover 用浅蓝色，颜色必须由现有系统主色 token 推导
  （例如 `color-mix(in srgb, var(--oc-accent) …)`），不得写死一份新的蓝。
- **禁止新增或修改 `font-family`**；既有字体继承关系不变。

## 15. 权限边界

不动。所有写请求、门禁、角色判定沿用既有实现。本批新增的只是渲染与 DOM。

## 16. 历史数据兼容

- 历史里只有 `assistant` 没有 `user` 的回合：**照原样渲染**，不得凭空补用户气泡。
- 历史里存在 `kind: "user"` 的（技术侧 echo 气泡已落库）：按原顺序恢复。
- 旧任务的 progress 行没有 `detail`：Tool Item 仍然渲染（title + state），只是没有「详情」折叠。
- 不迁移、不删除任何会话事件与数据库记录。

## 17. 非目标与明确不修改的后端边界

真正的 token 流式输出、SSE 协议重构、后端 Agent 推理、Prompt 内容、工具调用协议、数据库结构、
看板业务字段、权限与工作流、Agent 输出内容压缩、字体系统。

## 18. 非流式与后续流式批次边界

> 折叠能力不依赖流式；本批只验证**完整内容返回后**的折叠展示。
> 流式追加（逐字 `text` 帧）与 Tool 状态实时迁移（`running → completed` 的 SSE 驱动）
> 另立后续 Spec。本批的 `data-status` / `data-state` 只需支持"一次性返回后正确映射"和
> "就地翻转"，**不需要**新增真实流式状态机。

## 19. 可自动化验收标准

见 §16 对照表。全部通过 `python3 -m unittest tests.test_quote_tech_unified_tool_list_conversation_red -v`。

## 20. Red Tests 对照表

| 红测 | 覆盖条款 |
|---|---|
| A1–A6 | 两侧统一卡片结构、header/body/status、一步一卡、Tool Item 不再是卡、摘要类不得绕过构造器、错误态是 modifier |
| B7–B14 | 用户消息先出现、按钮触发、重新生成、失败仍保留、turn 顺序、非用户触发不伪造、历史不补造 |
| C15–C21 | Tool List 存在、四态映射、复杂详情不压缩、查询条件/缺失项/回退/时间保留、父子层级、Tool Item 无独立卡片 |
| D22–D31、D23b | 详情默认折叠、**标题行本身就是开关**、**界面上不得出现看得见的「详情」标签（D23b；允许保留不可见兼容节点）**、展开可见、可收起、hover 浅蓝、token 派生、键盘与焦点、aria-expanded 语义、两端同合同、**缩进子项折进父项** |
| E31–E37 | 思考折叠栏位置/默认态/展开/空不渲染/历史支持/不依赖流式/不额外成卡 |
| F38–F43 | **用户气泡保持系统主色实心蓝底 + 白字**、Agent 卡白底、无明显灰底、无新增/修改 font-family、字体继承不变 |
| G44–G50 | 输出不删减、后端协议不变、SSE 名不变、DB 不变、持久化可恢复完整 turn、失败态仍显示、既有聊天测试继续运行 |

## 21. 风险与兼容策略

1. **用户气泡配色**：本批**不碰** `docs/specs/quote-tech-user-message-primary-bubble.md`
   （主色实心蓝底 + 白字）与 `tests/test_quote_tech_user_message_primary_bubble_red.py`；
   DS2 实施后该文件必须仍然全绿，若转红说明误改了用户气泡。
2. **`agent-chat.js` 含 4 个 NUL 字节**（`\x00` 哨兵，位于第 360 / 366 行的 markdown 代码块占位符）。
   `node --check` 通过，非损坏。读取时必须 `.replace("\x00","")`；**不要**顺手做无关的大范围重写。
3. **`.oc-amsg` 变成 `<article>` 或加 `data-*`** 可能影响既有 flex/几何断言。策略：允许保持
   `<div class="oc-amsg">`，只 **加** `data-agent-card` / `data-status` 与 role 属性。
4. **技术侧 root 判定**：`.oc-amsg.oc-task-card` 是同一 root 的 modifier，`[data-agent-card]` 数量
   等于"业务步骤数 + 任务卡数"，不得因为统一而合并。
5. **「详情」标签与两份既有合同的冲突**（本批唯一需要用户拍板的取舍）：
   `agent-chat.js::pushTaskStep` 里那个 `<summary>详情</summary>` 被
   `test_task_process_detail_red.test_31`（渲染文案）与
   `test_quote_btn_radius_and_tech_board_render_red.test_43`（源码断言）钉死；
   `## 130` / `## 131` 已把它作为遗留项如实记录。本批红测 `D23 / D23b` 按"用户不能看见"判定，
   给出两条合法路径：**(A) 保留节点 + CSS `display: none`**（零测试改动，推荐）；
   **(B) 删节点 + 退役两条旧断言**（DOM 更干净，但要用户批准改这两份既有测试）。

## 22. 验收清单（人工）

1. 技术工艺：点「一键解析图纸」→ 先出现「我：开始解析这张图纸。」→ 再出现执行卡，
   卡内是 Tool List（`✓ 查询同类件 / P-001 主体外壳`），查询条件默认折叠，hover 浅蓝。
2. 技术工艺：直接点「检索零部件库（1/4）：P-001 上壳」这一行 → 展开看到查询条件、命中件、
   差异与输入输出；界面上**没有**独立的「详情」小标题；再点这一行收起。
   缩进子项（查询条件 / 命中 / 差异）应出现在父行展开后的内容里，而不是与父行平级。
3. 技术工艺：卡底展开「思考过程」。
4. 报价：点 kickoff「开始」与「转交任务」→ 同样先出用户气泡。
5. Agent 消息为白底 + 浅灰边框 + 深色正文；**用户气泡仍是原来的蓝色实心底 + 白字**。
6. 刷新页面 → 顺序、折叠态、Tool List 与刷新前一致。
7. 历史里本来没有用户气泡的旧回合 → 刷新后仍然没有。

## 23. 不允许减少的既有能力

- 不得删除：业务步骤标题、Agent 身份、状态、详细过程、最终结论、缺失项、警告、查询条件、
  取价时间、回退信息、数量、人工确认信息、子级信息。
- 不得删除：`pushTaskStep` / `toneOf` / `setStatus` 系列 / `sanitizeTaskDetail` / `taskProgressHost` /
  `persistTaskCard` / `renderTaskRetry` / `replayTimelineTask` / `addToolCard` / `setToolResult` /
  `describeTool` / `addToolActivity` / `showStage` / `showTyping` / `.oc-req-summary` / `.oc-match-card` /
  `.oc-confirm-card` / `.cand-block`。
- 不得把"一个业务步骤一张卡"改成"整个会话只有一张卡"。

## 24. 本批禁止事项

- 直接修改生产 UI 实现（由 DS2 做）；删除已有复杂内容；把业务过程缩成简单 Todo；
  让每个 Tool Item 再成为卡片；只改 CSS 不检查消息创建入口；为历史消息伪造用户气泡；
  修改 Agent Prompt / 工具协议 / SSE 协议 / 数据库；顺手做流式重构；新增字体；
  修改红测让错误实现通过；提交、Push、Merge、Tag 或部署。

## 25. 统一 turn helper（实现契约，必须存在）

> 注：`## 125` 已按本节的建议补上 `echoTaskPrompt` 之外的回声路径与看板页 `aiUserSay` / `crUserSay`；
> 本修正批只需保证这些入口在改过程卡片时不被打断。

- **技术侧左栏** `tech_app/frontend/agent-chat.js`：必须有唯一一个函数，负责"用户主动触发的
  新回合"——先插用户气泡、再返回本轮 Agent 卡 ctx（建议名 `beginUserTurn(text)`）。真人打字、
  `echoTaskPrompt` 回声、以及未来所有"用户点按钮 → 左侧出卡"的入口，都必须经它。
  `addUser()` 仍是气泡的底层原语，不允许被业务入口直接调用后再自行 `addAssistant()`。
- **报价侧** `确认需求解析结果.html`：同样必须有唯一一个 turn helper（建议名 `beginUserTurn(text)`），
  `sendFromInput` / `fillStepRecommend` / `confirmStep` / kickoff / `runStep1` / 转交任务说明
  都必须经它；`sendToAgent()` 只允许在"本轮已经有用户气泡"或显式 `silent`（纯恢复 / 系统通知）
  时被调用。
- **看板页** `assembly-integration.js` / `cost-review.js`：`aiProcessCard()` / `crCard()` 的**每一个**
  调用点所在函数，必须先有一条 `aiUserSay()` / `crUserSay()`（文案由该调用点按本次动作给出，
  不得暴露函数名、`tool_call_id` 或 JSON）；纯后台恢复（`aiReplayTimeline`）与连接状态提示不加。
- 报价页（`确认需求解析结果.html`）的用户气泡原语就是既有 `addUserBubble`，不新增第二套。
