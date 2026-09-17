# 技术工艺任务卡：卡片体布局归位 + 任务框架的「过程事件」通道（## 94）

两件事一起做，因为它们是同一张卡的两个验收面：先把卡的结构改对（挤成一行是硬缺陷），
再给这张卡接上「模型调用 + 工具摘要」的过程流（现在后台任务卡只有一句进度，看不到
模型在干什么）。

## 1. 背景（已实测，非推断）

### 1.1 卡片被挤成一行：`## 89` 引入的回归

- `tech_app/frontend/agent-chat.js:1387` 把三个类放在**同一个** div 上：
  `el("div", "oc-amsg oc-task-card is-queued")`，紧接着 `:1393` 把「身份行」和「步骤列表」
  作为**两个兄弟节点**挂上去：
  ```js
  const box = el("div", "oc-amsg oc-task-card is-queued");
  const head = el("div", "oc-alabel");
  box.append(head, steps);
  ```
- `.oc-amsg` 是横向 flex（`agent-chat.css:168-172`：`display:flex; gap:11px;`），本意是
  「头像 + 正文」。于是身份行与步骤列表变成同一 flex 行的两个 item —— 用户看到的
  「所有文字都在同一行」。
- 正确的一支在同函数上面（`agent-chat.js:1375-1384`）：合并进当前轮助手卡时，
  `steps` 被塞进 `turn.body`（即 `.oc-abody`，块级容器），所以**只有"没有实时轮"这条分支是坏的**。
- `agent-chat.css:526` 的注释写着「任务卡是同级卡（**不在 `.oc-amsg` 里**）」，
  与实现相反 —— 实现把两个类加在同一节点上。
- 引入时间：`git log -S'oc-amsg oc-task-card'` → 只有 `a4bd13c`（## 89）。改之前是
  `el("div", "oc-task-card is-queued")`（默认 `display:block`，头行与步骤天然上下排列）。
- 触发条件：`activeTurnCtx` 为空（`agent-chat.js:503/842/1374`）。**凡不是从左侧输入框
  发起的一轮对话**（右侧看板点按钮、左侧回形针上传、历史回放）都走这条分支，所以
  右侧按钮触发的任务卡全是这个挤成行的样子。

### 1.2 看不到模型调用与工具执行：数据源问题，不是样式问题

- 任务卡的正文只有 `progress_log`：后端任务的进度是一句文字
  （`tech_app/backend/services/tasks.py:201-212` → `store.append_task_progress`
  `storage/store.py:236-257`）。
- 「需求资料解析」的 job 在后台线程里直接调模型（`main.py:5894-5896`
  调 `requirement_extract.extract_requirement_fields()`），**没有经过 Agent 会话循环**。
- `thinking` 帧只在 `/agent/send` 的 SSE 里产生（`services/oc_agent.py:3036-3040`），
  左侧思考折叠块（`agent-chat.js:621-635`）与工具卡（`:657/722/732`）也只从那条流渲染。
  后台任务线程永远不会产生这类事件。
- 任务卡落库也只存文字步骤（`tasks.py:251-257` 的 `task.steps`）。

因此：**后台任务卡不是"漏渲染"，而是"根本没有这类数据"**。本批不改这个事实，
但要给任务框架加一条一等的「过程事件」通道，把**模型调用与工具摘要**按序播进会话。

**边界（不改口径）**：本批不播模型的推理过程（后台任务没有推理流），只播
「调用了哪个模型 / 哪个工具、拿到什么规模的产出」。报价侧 Agent 会话的思考折叠与工具卡
（`oc-thinking` / `oc-art-detail` / `oc-tool-result`）一个字都不动。

## 2. 目标

1. 任务卡的结构与助手卡**逐字同构**：`.oc-amsg.oc-task-card > .oc-abody > (.oc-alabel + 步骤区)`，
   身份行与步骤列表不再同行；
2. 修正 `agent-chat.css` 里与实现相反的注释；
3. 任务框架新增「过程事件」通道（`model` / `tool` / `progress` 三类，同一序列、可回放）；
4. 左侧任务卡按序列渲染这三类事件，落库与回放保持同一顺序；
5. 既有行为、既有测试口径一律不放松。

## 3. 契约 A：卡片体布局

### A1 结构与助手卡同构

`ensureTaskCard()` 的「没有实时轮」分支必须产出：

```
div.oc-amsg.oc-task-card.is-queued
└── div.oc-abody
    ├── div.oc-alabel      （身份 + 任务中文名 + 右侧状态 chip）
    └── div.oc-task-steps  （进度 / 过程步骤）
```

- 可以继续把 `oc-amsg oc-task-card` 留在同一个节点上（`## 89` 的测试与报价同款口径都要求它），
  **但内容必须先包进 `.oc-abody`**：`.oc-abody` 是 `.oc-amsg` 那一行里唯一的 flex item，
  身份行与步骤区在它内部按块级上下排列。
- 「合并进当前轮助手卡」那一支（`turn.body` 已经是 `.oc-abody`）行为不变。

### A2 卡片元素不得再有第二个直接子元素

`.oc-amsg.oc-task-card` 的 `children` 必须恰好是 1 个（即 `.oc-abody`）。
身份行与步骤列表**不得**同时成为该 flex 行的直接子元素。

### A3 全文件口径一致

`agent-chat.js` 里每一个 `el("div", "oc-amsg…")` 都必须配套一个 `el("div", "oc-abody")`
承载内容 —— 不允许再出现"有框没有体"的卡。数量必须相等。

### A4 注释与实现一致

`agent-chat.css` 里描述 `.oc-task-card` 的注释不得再出现「不在 `.oc-amsg` 里」这类与实现
相反的表述；必须写清「与助手卡同款、内容在 `.oc-abody` 里」。

### A5 样式契约不变

- `.oc-abody` 保持 `flex: 1` 与 `min-width: 0`（flex 行里唯一的正文列，缺任一都会挤压/溢出）；
- `.oc-task-steps` 保持 `display:flex; flex-direction:column`（步骤默认展开、逐行）；
- `.oc-task-card .oc-task-state { margin-left: auto }` 与 `.oc-task-card.is-*` 六态配色不动；
- 卡片仍是白底 + 1px 边框 + 圆角，与 `.oc-amsg` 同款。

### A6 两个现成样板保持同构

`assembly-integration.js` 的 `aiProcessCard()`（2.2）与 `cost-review.js` 的 `crCard()`（2.3）
本来就是正确形态，本批**不得**改动它们的结构（`.oc-amsg > .oc-abody > (.oc-alabel + 步骤区)`），
它们同时是这次的对照样板。

## 4. 契约 B：任务框架的「过程事件」通道

### B1 `tasks.process_event(phase, text, detail=None)`

- 位置：`tech_app/backend/services/tasks.py`，与 `report_progress` 同族。
- `phase` 取值白名单：`model` / `tool` / `progress`；非法 phase 抛 `ValueError`。
- `text` 为空 → 静默 no-op。
- **没有当前任务上下文**（`_CURRENT_TASK.get()` 为 None）→ 静默 no-op，不写盘、不抛错
  （Agent 会话轮、HTTP 请求线程都会走到这条路径，绝不能污染它们）。
- 写入任务文档的 `process_log`（**只增不改**），每条：
  `{"seq": <int, 本任务内从 1 单调递增>, "phase": "<白名单值>", "text": "<≤240 字>", "at": "<时间>"}`。
- 上限 `PROCESS_LOG_LIMIT`（与 `PROGRESS_LOG_LIMIT` 同量级，超出保留最后 N 条）。

### B2 `report_progress` 既有一字不改，同时进同一序列

- `report_progress(text)` 仍然写 `progress_log`（只增）与 `progress`（最新一条）——旧客户端
  与既有测试不得受影响；
- **同时**向 `process_log` 追加一条 `phase="progress"` 的记录。
  这样「进度行」与「模型/工具行」在同一个序列里，顺序唯一、无歧义。

### B3 任务创建即有空日志

`submit()` 建任务时 `process_log` 初始化为 `[]`（与 `progress_log` 一致），
前端据此判断「这个任务有没有过程通道」。

### B4 轮询出口带上 `process_log`

`GET /api/projects/{project_id}/tasks/{task_id}`（`main.py:5239`）的返回体必须包含
`process_log`（已有的 `progress_log` / `progress` / `status` / `error` 一个不少）。

### B5 模型调用事件在「真正发起调用的那一层」发

- 一次**逻辑**模型调用恰好一对事件：开始 `调用模型（<实际模型名>）`、结束
  `模型返回（<产出规模>）`；异常路径也要有一条结束事件（`模型调用失败（<简短原因>）`），
  用 try/finally 语义保证成对。
- 发出位置：`services/claude_client.py:163` 的 `run()`（Anthropic 原生路径）与
  `services/qwen_client.py:686` 的 `run()`（OpenAI 兼容路径）。
- `services/llm_client.py:106` 的 `run()` 只做分派，**不得**再发一对（否则同一调用会出现
  两份模型事件）。它继续保留 `last_used_model()` 口径不变。
- 模型名取**实际跑过的那个**（`claude_client.last_used_model()` / `route["model"]`），
  不许写死 "Claude"，也不许拿配置值顶替。
- 事件文本**只允许**出现模型名与规模数字；**不得**出现 system prompt、用户输入原文、
  附件内容、API Key 或响应正文。

### B6 工具调用事件

以下四个位置必须各发一条 `tool` 事件（开始一条带目标，返回一条带摘要，摘要用现成计数即可）：

- `main.py::_process_lookup_for`（`main.py:1476`）——企业工艺库检索；
- `main.py::_cost_lookup_for`（`main.py:1486`）——企业成本库检索；
- `main.py::_refresh_component_match`（`main.py:1345`）——零部件库检索；
- `main.py` 的型号联网核验 job（`main.py:1676-1688`）——型号核验。

文本形如：`检索企业工艺库（P-001）` / `  ↳ 命中 3 条路线模板`。失败降级（工具不可用）
也要留一条 `tool` 事件的说明，不能静默。

### B7 载荷透传 `process`

任务卡载荷（`task-progress` / `task-completed` / `task-failed` 的 detail）在既有字段之上
多一个 `process` 字段，取值就是任务文档的 `process_log`（数组，未加工）。

必须透传的位置（每个都是机械的一行，字段名与形状必须一致）：

- `tech_app/frontend/app.js:1263-1272`（2.1 轮询）；
- `tech_app/frontend/assembly-integration.js:311-314`（2.2）；
- `tech_app/frontend/cost-review.js:253-256`（2.3）；
- `tech_app/frontend/inline-analysis.js:232-236`；
- `tech_app/frontend/requirement-create.js:72`（1.1，`rcTaskPayload`）；
- `tech_app/frontend/agent-chat.js:1682-1692`（左侧自己轮询的零部件库检索）。

### B8 左侧按序列渲染

- `renderTaskProgress(detail)`：**只要载荷带 `process` 键（哪怕是空数组）就以它为准**；
  没有该键时退回 `log`（`progress_log`）—— 旧任务记录必须照旧能画出来。
- 渲染顺序 = `process_log` 的 `seq` 顺序，一行一条，沿用既有的
  `.oc-process-step` / `.oc-process-dot` / `.oc-process-text` 结构。
- `phase` 决定色调：`model` / `tool` 各有专属类名（新增 `.oc-process-step.model` /
  `.oc-process-step.tool`），`progress` 沿用现有默认样式（含 `  ↳` 前缀的缩进与
  `命中` / `无同类件` 的既有色调判定，不回归）。

### B9 落库与合并按 `seq` 取并集

- 任务卡落库（`persistTaskCard`）在既有字段之上多带 `process`（只提交新增条目）。
- `storage/store.py::_merge_task_entry`（`store.py:1225`）与
  `tech_app/frontend/tech-session-timeline.js::mergeTask` 必须用**同一套口径**：
  `process` 按 `seq` 取并集、按 `seq` 升序；**不按文本去重**（同一句话在不同零件上重复
  出现是合法的，今天的 `steps` 文本去重会把它吃掉）。
- `steps` 的既有合并口径不动（旧数据继续按原样合并）。

### B10 回放同序

`agent-chat.js::replayTimelineTask()` 必须把 `task.process` 一并透传给 `renderTaskProgress`，
重进项目 / 刷新后卡片里的顺序与运行时一致。

### B11 不放松

- 任务卡的身份、状态机（queued / running / succeeded / partial / failed / interrupted）、
  `task:<id>` 幂等 key、「失败不单独建常驻卡」「预期内失败静默」等既有口径一律不动；
- 不加新的 HTTP 路由（`process_log` 走既有任务端点）；
- 不把 `thinking` / `tool_use` / `tool_result` 这类 Agent 专有事件塞进任务卡；
- 不在过程事件里落任何密钥、prompt 原文或响应正文（## 99 例外：规模与结构摘要
  （字数 / 段落数 / 图片数 / 顶层字段规模）与文件名（非路径）允许放进明细）。

## 5. 验收

| 编号 | 场景 | 通过标准 |
| --- | --- | --- |
| A1 | 右侧按钮触发的任务卡 | 结构为 `.oc-amsg.oc-task-card > .oc-abody > (.oc-alabel + .oc-task-steps)` |
| A2 | 卡片直接子元素 | `.oc-amsg.oc-task-card` 的 children 恰好 1 个（`.oc-abody`） |
| A3 | 身份行与步骤 | 步骤文字不出现在身份行的文本里 |
| A4 | 合并进当前轮 | 步骤仍落在本轮助手卡的 `.oc-abody` 里（不回归） |
| A5 | 注释 | `agent-chat.css` 不再声称任务卡不在 `.oc-amsg` 里 |
| A6 | 两个样板 | `aiProcessCard` / `crCard` 结构逐字不变 |
| B1 | `process_event` | 三类 phase 可写；非法 phase 抛 `ValueError`；无任务上下文静默 no-op |
| B2 | `report_progress` | `progress_log` 与 `process_log` 同时增长，seq 单调 |
| B3 | 任务端点 | 返回体含 `process_log`（`progress_log` 等既有字段不丢） |
| B4 | 模型事件 | 一次 `claude_client.run()` 恰好一对 `model` 事件，文本含真实模型名 |
| B5 | 无泄漏 | 事件文本不含 prompt 原文、附件内容、API Key |
| B6 | 工具事件 | 四个位置各有 `tool` 事件（源码契约 + 至少一处实跑） |
| B7 | 载荷 | 六个轮询点都透传 `process`（字段名与形状一致） |
| B8 | 渲染 | 有 `process` 按 seq 顺序渲染并带 phase 类名；无 `process` 退回 `log` |
| B9 | 合并 | `_merge_task_entry` 与 `mergeTask` 对 `process` 都按 seq 取并集，重复文本保留 |
| B10 | 回放 | `replayTimelineTask` 透传 `process`，顺序与运行时一致 |

## 6. 不在本批

- 把后台任务改造成走 Agent 会话循环（那是另一条路，代价与影响面完全不同）；
- 播模型的完整推理过程 / chain-of-thought；
- 右侧看板页面自己的过程卡（`aiProcessCard` / `crCard` / `crSay`）改用它自己的过程流 ——
  它们拿到 `process` 后如何使用由后续批次决定，本批只要求它们**结构不变**；
- 任务卡的排序、去重、失败静默等既有口径调整。
