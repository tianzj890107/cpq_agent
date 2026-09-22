# 技术工艺：Agent 主动动作的「我：…」回声 + 执行进度与助手回复合成一张卡

状态：Spec + 红测（已实现）
红测：`tests/test_tech_agent_echo_bubble_and_single_exec_card_red.py`

## 1. 用户拍板（三条）

1. **决定一 → 选项二**：只给「Agent 主动发起、并且会真的跑起来」的动作补一条用户气泡；
   往看板写字段、把确认 / 审核意见带进看板输入框、单纯刷新看板这几类**一条都不加**，
   维持现在的系统提示。
2. **决定二 → 方案 A**：气泡里那句话由**真正执行的那一方**（右侧看板动作）给出，左侧原样显示，
   左侧不得自己写「动作名 → 文案」的映射表。
3. **决定三 → 合成一张**：执行进度不再是一种独立卡片，而与助手回复合成同一个气泡
   （报价 `.message-ai` 的形态）。

用户原话：

> 一种是问 agent 输出的卡片，一种是执行工具等卡片，没有像报价里面那样执行工具也会先模拟
> 用户提问再 agent 输出

> 那就选二，67 不用管 然后二选你推荐的，三选应该合并成跟报价一样的一个气泡

## 2. 现状缺口（已排查）

- 技术工艺左侧现在只有真人打字才出用户气泡：`agent-chat.js:757`（发送）与 `agent-chat.js:262`
  （历史回放）。Agent 主动做事的路径一处都没调用 `addUser()`，只留 `noteInThread()` 系统提示
  （`agent-chat.js:815` / `:839` / `:879` / `:890` / `:895` / `:909` / `:926` / `:941` / `:959`）。
  整个 `tech_app/frontend/` 里没有一处 `addUserBubble`（报价侧有 13 处）。
- 执行进度是**另一族卡**：`ensureTaskCard()`（`agent-chat.js:1316`）建的是独立 `.oc-task-card`，
  与助手卡 `.oc-amsg` 平级；注释里写着「任务卡是与助手卡同级的一张卡…不再套一层 `.oc-amsg`」。
  而报价侧工具执行行长在同一张 `.message-ai` 里（`确认需求解析结果.html:2285`）。
- 看板运行时把 `task-progress / task-completed / task-partial / task-failed` 播给左侧
  （`tech-board-runtime.js:326-366`），载荷只有 `{action, phase, label, taskId, ...}`：
  **没有**任何「用户口吻」的字段，左侧无从显示。

## 3. 目标形态

```
我：开始解析这张图纸。                        ← .oc-ubub（本批新增的回声）
┌──────────────────────────────────────────┐
│ ● 技术工艺智能体 · 图纸解析      ◌ 运行中 │   ← 唯一一层框（.oc-amsg）
│   • 读取图纸…                             │
│   • 识别零件 5 个…                        │
│   ✓ 图纸解析完成                          │
└──────────────────────────────────────────┘
```

同一轮里 Agent 已经说过话时，进度直接长进它那张卡，不再另起一张：

```
我：成本帮我算一下。                          ← 真人输入（既有）
┌──────────────────────────────────────────┐
│ ● 技术工艺智能体                 ◌ 运行中 │
│   我先把 2.3 的单件成本跑一遍。            │   ← Agent 正文
│   • 读取零件 P-003…                       │   ← 进度并进同一张卡
└──────────────────────────────────────────┘
```

（上面这两段里，「我：…」气泡插在当前这一轮助手卡的**上方**。）

## 4. 契约

### C1 右侧看板动作声明气泡文案

- 动作条目新增可选字段 `prompt`：字符串，或 `function(payload)`（按这一次调用的入参生成，
  例如点名零件号）。它属于**执行方**的知识，不放进 `getState()`。
- `tech-board-runtime.js` 新增 `function resolveActionPrompt(entry, payload)`：
  - 字符串 → 原样（trim）；
  - 函数 → 用 `payload` 调用，抛错或返回空 → 空字符串；
  - 未声明 / 解析不出 → 空字符串。**不得**拿动作名或 label 兜底造句。
- `runEntry()` 在启动那条 `TASK_PROGRESS` 上带 `prompt` 与 `runId`
  （`publishTaskCard(EVENT.TASK_PROGRESS, ...)`）；既有字段 `action / phase / label / taskId`
  一个都不删。没声明 `prompt` 的动作，`prompt` 是空字符串。
- `runId` 是**这一次执行**的唯一标识（`nextRequestId('run')` 即可），每次 `runEntry` 都不一样。
  没有它，左侧只能按项目级 `taskId` 或动作名去重，同一动作第二次执行就不会再出「我：…」气泡。
  注意启动事件里的 `taskId` 是项目级的、**不能**当执行标识用（真实的后台任务号要等提交后才拿到，
  卡片随后会用另一个 key 建出来）。

### C2 只有这 5 个动作声明 `prompt`

| 动作名 | 位置 | 对应左侧入口 |
| --- | --- | --- |
| `parseDrawing` | `app.js` | Agent 请求开始解析（2.1 图纸） |
| `extractRequirement` | `requirement-create.js` | Agent 请求一键解析技术资料（1.1） |
| `integrationStep` | `assembly-integration.js` | Agent 请求生成参数推荐 / 组装工艺（2.2） |
| `costStep` | `cost-review.js` | Agent 请求单件 / 组装 / 全部成本测算（2.3） |
| `openIntegrationDrawings` | `assembly-integration.js` | Agent 请求打开「整合图纸」 |

- 文案必须是中文业务句，且与这次真正要做的事一致：`integrationStep` / `costStep` 用函数形式，
  按 `payload.step`（必要时 `part_id`）产出不同句子。
- 其余动作（`refreshData` / `refreshIntegration` / `refreshCostReview` / `refreshProcessReport`
  以及各种确认、写库、送审、发布、回传）**一律不声明** `prompt`。

### C3 左侧按 `prompt` 出气泡

- `agent-chat.js` 新增 `function echoTaskPrompt(detail)`：
  `detail.prompt` 非空、且**这一次执行**（`runId` 优先，回退 `taskId` / `label`）还没有出过气泡 →
  出气泡并登记；同一次执行的重复投递不再出第二条，**不同执行各出一条**。
- `renderTaskProgress(detail)` 的第一件事就是调用它，**必须在**「没有进度明细就不建卡」的提前
  return（`agent-chat.js:1424`）**之前**：任务刚启动、还没蹦出第一条进度时，气泡先出现。
- 历史回放（`replayTimelineTask`）走同一个入口：`task.prompt` 有值就同样先出气泡，顺序与当时一致。

### C4 气泡插在当前这一轮助手卡的上方

- `addUser(text, before)` 增加可选锚点：锚点在会话里 → `insertBefore`；否则照旧 append。
- `echoTaskPrompt` 在「当前这一轮助手卡还在」时把气泡插到那张卡**之前**；没有当前轮
  （用户点右侧看板按钮触发的执行）→ append，气泡后面紧跟执行卡。

### C5 一张卡：执行进度并进本轮助手卡

- `addAssistant()` 在**实时**会话里记录当前轮上下文（`activeTurnCtx = ctx`）；一轮结束
  （`send()` 的 `finally`）清空；历史回放期间不设置（回放按行渲染，不跨行合流）。
- `ensureTaskCard(taskId, label)`：
  - 当前轮存在 → **不新建卡**：进度行容器插进这轮助手卡的 `.oc-abody`，状态 chip 复用它那颗
    `.oc-alabel-state`；
  - 当前轮不存在 → 新建一张 `.oc-amsg.oc-task-card`，头行是 `.oc-alabel`：
    身份「技术工艺智能体」+ `.oc-alabel-sub`（任务中文名）+ 右侧 `.oc-alabel-state.oc-task-state`。
- 卡根节点继续带 `.oc-task-card` 与 `is-<status>` 类，`.oc-task-card.is-*.oc-task-state` 既有配色
  规则继续生效；头行那颗 chip 同时带 `oc-alabel-state` 与 `oc-task-state` 两个类名，
  并新增一条 `.oc-task-card .oc-task-state { margin-left: auto; }` 让它靠右（`.oc-alabel` 是行内 flex）；
  既有 `.oc-task-head` / `.oc-task-title` 规则可以保留但不再生成节点；`.oc-task-steps` / `.oc-task-error` / `.oc-task-note` 结构，以及
  `pushTaskStep` / `toneOf` / `setTaskStatus` / `taskStatusWord` / `sanitizeTaskDetail` /
  `taskProgressHost` / `persistTaskCard` / `replayTimelineTask` 全部保留。
- 状态 chip 词表沿用任务状态词（排队中 / 运行中 / 已完成 / 部分完成 / 失败 / 中断，中断沿用蓝色）。
  进度挂在本轮助手卡上且该任务还没结束时，本轮流结束时**不得**把 chip 改成「已完成」——
  由任务最终状态决定。
- `renderTaskRetry()` 的「仅重试失败项」按钮落在同一张卡的进度区之后，行为不变。

### C6 落库与回放

- `persistTaskCard(taskId, label, status, steps, error, prompt)`：把 `prompt` 写进任务事件的
  `task.prompt`（`kind: "task"`）。
- **回声气泡自己也要落库**：出气泡的同时 `persistSessionEvent` 一条 `kind: "user"` 的会话条目
  （`text` = 这句话，`key` 里带 `runId`，`source: "shell"`，`stage` = 当前步骤）。回放时它按
  `seq` 顺序排在同一次执行的卡之前，重进项目后气泡不会丢；不产生卡的动作（打开「整合图纸」）
  也照样能恢复。
  现有回放已经支持「用户气泡」这一类条目（`agent-chat.js:261` 的 `type === "user"` → `addUser`），
  不需要新增第二套渲染。
- 后端**不需要**新路由：`AgentEventBody.task`（`main.py:2100`）与
  `store.append_session_event` / `_merge_task_entry`（`store.py:1201`、`:1221`）已原样保留额外
  标量字段；前端 `tech-session-timeline.js` 的 `mergeTask` 同样是通用合并。
- 重进项目：时间线条目按 `seq` 顺序回放，气泡与卡片的相对顺序与当时一致。

### C7 不做的事

- 不加气泡的动作：往看板写字段（需求字段 / 整合参数与工序 / 成本说明 / 报告内容）、
  把确认意见与审核意见带进看板输入框、单纯刷新看板；这些现有系统提示照旧。
- 不动确认卡（`request_confirmation`）、权限门、写库 / 送审 / 审核 / 发布 / 回传的动作映射。
- 不新增后端路由，不改桥事件名与信封，不改 `.oc-ubub` 主色样式，不改 `tech_ui` 八个 action 行为。
- 左侧不得出现「`ui_action` → 文案」的映射表或任何写死的中文句子表。

## 5. 范围（允许修改）

- `tech_app/frontend/tech-board-runtime.js`（prompt 解析与载荷）
- `tech_app/frontend/app.js`、`requirement-create.js`、`assembly-integration.js`、`cost-review.js`
  （各自的动作条目加 `prompt`）
- `tech_app/frontend/agent-chat.js`（回声、合流、落库）
- `tech_app/frontend/agent-chat.css`（任务卡头行 / chip 对齐；既有选择器不删）
- 引用这些静态资源的页面版本号参数（`agent-chat.js?v=`、`agent-chat.css?v=`、
  `tech-board-runtime.js?v=`，以及四个看板页自身的 `?v=`）
- 当周 changelog

## 6. 禁止事项

- 不改后端路由、schema、权限角色与历史数据；
- 不删、不清空任何会话事件与已有卡片；
- 不给第 6 / 7 类动作加气泡，不把 `prompt` 兜底成动作名或 label；
- 不把 `prompt` 写进 `getState()`（它是每次调用的入参相关，不是按钮状态）；
- 不为迁就实现修改本 Spec 或红测；
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 7. 验收标准

1. 真跑 `tech-board-runtime.js`：声明 `prompt` 的动作，`task-progress` 载荷带这句话；函数形式按
   `payload` 变化；没声明的动作该字段是空串；`silent` 动作照旧一条事件都不发。
2. 上述 5 个动作都声明了中文 `prompt`；其余动作条目没有 `prompt`。
3. `renderTaskProgress` 先出气泡、同一任务只出一条，且发生在「无明细不建卡」之前。
4. 回放任务带 `task.prompt` 时同样先出气泡。
5. 有空锚点时气泡插在该节点之前（`insertBefore`）。
6. 实时轮内执行进度并进本轮助手卡、不新建卡；无实时轮时新建 `.oc-amsg.oc-task-card`，
   头行是 `.oc-alabel` + `.oc-alabel-sub` + `.oc-alabel-state.oc-task-state`。
7. 既有任务卡管线、任务卡 chip 配色、`.oc-ubub` 主色、工具轨迹结构、`tech_ui` 确认卡、
   桥事件名全部保留。
8. `agent-chat.js` 里没有 `ui_action` → 文案映射表；第 6 / 7 类动作没有新增气泡调用。
9. 连跑两次同一个动作，两次的启动载荷 `runId` 都非空且互不相同；左侧按它去重。
10. 回声气泡以 `kind: "user"` 条目落库，重进项目后按顺序恢复。
