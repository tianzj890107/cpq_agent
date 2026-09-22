# Spec：项目会话时间线 —— 所有会话条目统一持久化 + 按同一顺序拼接

状态：Spec + 红测（已实现）
红测：`tests/test_tech_agent_history_project_rebind_red.py` `tests/test_tech_session_timeline_persistence_red.py`

## 背景（用户反馈）

重新进入项目后只剩 Agent 对话，看板按钮跑出来的过程与结果提示全都不见了；而且同一条
会话线程里，「有的卡片永远钉在最下面，有的按顺序从上到下」。用户要求：

> 这些所有的卡片都需要是持久化保存的，并且都要是按顺序拼接的，不能有的固定在最下面
> 有的按顺序从上到下。

## 事实依据（实测）

四类内容今天走的是四条不同的路，只有前两类会被持久化：

| 内容 | 现状链路 | 重进项目 |
| --- | --- | --- |
| 用户输入 / Agent 回复 | `/agent/send` → OpenClaude Conversation → `~/.claude/projects/.../<session>.jsonl` → `GET /agent/history` | 恢复 |
| Agent 工具调用 / 工具结果 | 同上 | 恢复 |
| `tech_ui` 确认卡 | 事件随工具轨迹进 JSONL，但回放时被主动跳过（`agent-chat.js` 的 `if (event.name === "tech_ui") return;`） | 不恢复 |
| 看板任务卡（`task-progress` / `task-completed` / `task-failed`） | 桥消息 → `renderTaskProgress()` → `ensureTaskCard()` 追加进 `#ocTaskProgressHost`，只存在 DOM | 不恢复 |
| 阶段页过程文字（`cost-review.js` 的 `crSay()`、`assembly-integration.js` 的 `aiSay()`、`summary-result.js` 的 `srSay()`…） | 直接插进各自 iframe 内的 `#crThread` / `#aiThread`，只存在 DOM | 不恢复 |
| 业务结果（成本 / 工艺 / 报告） | 写业务文件与库（`cost.json` / `cost_review.json` / `integration.json` …） | 右侧看板恢复，左侧会话卡片不恢复 |

两个具体缺陷：

1. **顺序**：`tech_app/frontend/agent-chat.js::taskProgressHost()` 返回
   `#ocTaskProgressHost`（`tech-workbench.html:78`，位于 `#ocTinner` 的**末尾**），
   所以任务卡永远排在所有消息之后（代码注释自己也写明「建出来就永远钉在底部，聊多少轮
   都不动」）；而 Agent 消息是按时间追加到线程里的。同一条线程里两套顺序规则。
2. **不持久化**：`renderTaskProgress()` 与各阶段页的 `*Say()` 都不调用任何会话持久化接口，
   也没有写入项目数据；`/agent/history` 只返回 OpenClaude 的 `messages`。

> 本批只改「卡片插到哪里、写不写库、回放什么」，**不删除** `#ocTaskProgressHost` 节点：
> 它已被 `test_tech_left_chat_controls_restore_red`、`test_tech_chat_card_noise_and_quiet_board_failures_red`、
> `test_tech_agent_history_project_rebind_red`、`test_tech_chat_drop_static_intro_bubble_red` 等
> 防缩水守卫固定引用，删节点属于扩大范围。

## 目标契约

### 一、后端：项目级会话时间线存储（append-only、有序、幂等）

新增 `store.append_session_event(project_id, event) -> dict` 与
`store.load_session_events(project_id) -> list`（`tech_app/backend/storage/store.py`，
底层走 `_meta().get_doc/put_doc(project_id, "session_events")`，JSON 与 SQL 两套后端都成立；
追加用进程内锁保证不丢记录）。

条目形状：

```
{
  "seq": 1,                       # 项目内单调递增，等于追加顺序
  "ts": "2026-09-15T10:20:30+08:00",
  "kind": "user|assistant|tool_use|tool_result|task|session-note|tech_ui",
  "source": "agent|shell|board",  # 谁产生的
  "stage": "cost",                # 属于哪个阶段（可空：全局条目）
  "text": "…",                    # 文本类条目
  "task": {"id": "…", "label": "…", "status": "running|succeeded|failed",
           "steps": ["…"], "error": "…"} | null,
  "ui": {…} | null,               # tech_ui 结构化事件
  "key": "…"                      # 幂等键，可空
}
```

规则：

- **顺序**：`seq` 由服务端分配，等于追加顺序；读取按 `seq` 升序返回，不重排。
- **幂等 + 就地更新**：带 `key` 的条目重复提交时只保留一条，返回的是**更新后**的那条，
  `seq` 与它在列表中的位置都不变（`key` 既去重、也做同一张卡的就地更新）；
  **没有 `key` 的条目一律追加新行**。进度轮询只提交新出现的进度行，不得每次追加整段。
- **任务卡**：同一个 `task.id` 在时间线里只有一条条目。调用方固定用 `key: "task:<taskId>"`
  提交：`task-progress` 把 `task.steps` **按行去重后追加**（已有行不重复、不覆盖），
  `task-completed` / `task-failed` 就地更新 `status` / `error`。前端纯函数
  `applyTaskProgress()` 用同一套合并口径，保证「左栏渲染出来的」和「落库里的」是同一张卡。

### 二、后端：读写路由（复用既有会话路由，不新建另一套会话体系）

- `POST /api/projects/{pid}/agent/event`（`auth.WRITE_ROLES`）：body 同条目形状
  （`kind` 必填，其余可选），返回 `{seq, event}`。
- `GET /api/projects/{pid}/agent/events?stage=&kinds=&source=`：按 `seq` 升序返回
  `{events: [...], count}`，供阶段页回放自己那一份（只读，不要求写权限）。
- `GET /api/projects/{pid}/agent/history`：**扩展**返回 `timeline`（见下），
  既有 `project_id` / `session_id` / `messages` / `message_count` 一个不删；
  Agent 层不可用（`available: false`）时**仍要返回持久化的本地条目**，
  即「重进项目还能看到之前跑过成本测算」。

### 三、前端：单一顺序（纯函数模块 `tech_app/frontend/tech-session-timeline.js`）

新模块（父壳与阶段页共用，唯一顺序 / 去重实现，可在 node 下直接执行）：

```
window.TechSessionTimeline = {
  normalize(raw) -> entry,                  // 统一补齐 kind（type -> kind），不改原对象
  merge({events, messages}) -> entry[],      // Agent 回放消息 + 本地事件
  append(list, entry) -> entry[],            // 返回新数组，按 (ts, seq) 稳定升序
  dedupe(list) -> entry[],                   // 同 key 去重，后者覆盖前者
  applyTaskProgress(list, payload) -> entry[],   // 同一 task.id 只留一张卡
  forShell({events, messages}) -> entry[],   // 左栏渲染集
  forStage({events, stage}) -> entry[]       // 阶段页渲染集
}
```

排序规则：有 `ts` 的比较 `ts`，相同或缺失时用 `seq` 兜底，仍相同则保持原顺序（稳定）。
合并规则：Agent 回放消息（`user`/`assistant`/`tool_use`/`tool_result`，来自 JSONL、只有 `ts`）
与本地事件按同一规则合并成**一条**有序列表，不再「消息全在前、事件全在后」。
渲染归属：`forShell` 保留 Agent 消息 + `task` + `tech_ui` + `source: "shell"` 的 note，
**排除** `source: "board"` 的过程文字（它们归阶段页，避免同一条内容两处重复显示）；
`forStage` 只保留该 `stage` 的 `source: "board"` 条目。

### 四、前端：父壳（`agent-chat.js` / `tech-workbench.html`）

- 只改「卡片插到哪里」，不删节点：`taskProgressHost()` 固定返回 `#ocTinner`，任务卡一律作为
  普通条目**按顺序插入线程**（`ensureTaskCard()` 里 `host === tinner` 的分支成为唯一分支）。
  `#ocTaskProgressHost`（`tech-workbench.html:78`）**保留不动** —— 它已经被多个批次的防缩水
  守卫引用（零件清单入口、空态区间、`setProject` 的可见状态清理），删节点会连带破坏这些保护；
  换项目时照旧 `replaceChildren()` 清空它即可。
- 所有会话条目（用户消息、AI 文本、工具卡、任务卡、`tech_ui` 卡、shell note）都经**同一个**
  追加函数进入线程，并调用 `POST /agent/event` 落库（落库失败只提示，不阻塞渲染）。
- `renderHistory()` 改为渲染 `timeline`（`forShell(...)`）而不是只渲染 `messages`，
  并且**不再跳过 `tech_ui`**。
- `detached` 不再清空已落库的历史条目（重进项目要能恢复）；`setProject` 仍清空上一个项目的
  可见内容，然后按新项目的 `timeline` 回放。

### 五、前端：阶段页（`cost-review.js` / `assembly-integration.js` / `summary-result.js` /
`report-review-result.js` / `report-publish-result.js` / `app.js`）

- 各自的会话文字出口（`crSay` / `aiSay` / `aiUserSay` / `srSay` 等）与过程卡统一改成：
  先写本地线程（保持现在的可见效果不变），同时把同一条内容以 `kind: "session-note"`、
  `source: "board"`、`stage: <本页阶段>`、`key: <阶段+文本+序号>` 追加到项目时间线（幂等）。
- 页面加载完成后，用 `GET /agent/events?stage=<本页阶段>&source=board` 取回该阶段条目，
  用 `forStage(...)` 回放进页内线程 —— 重进项目 / 重载 iframe 后，之前跑过的过程文字还在原位。
- 轮询型进度只按「新出现的进度行」追加（`applyTaskProgress` 语义），失败态就地更新同一张卡。

## 明确不在本批范围

- 结果入口条（`.oc-result-actions`）继续置底：它是入口 affordance，不是时间线条目。
- 不把每次轮询的中间态都写进历史：只写新增进度行与终态。
- 不改 OpenClaude JSONL 的既有读写、`/agent/send` 的 SSE 协议与 `/agent/new` 行为；
  不删 `/agent/history` 的任何既有字段。
- 不改业务数据（成本 / 工艺 / 报告）的存储与接口：时间线只存「会话可见内容」。
- 不做跨项目合并、不做会话删除、不做历史清理。

## 验收要求

- **R1 顺序**：`GET /agent/events` 与 `timeline` 都按 `seq` 升序；前端 `merge()` 把
  Agent 消息与本地事件按 `(ts, seq)` 交错排好（不是两段拼接）。
- **R2 持久化**：`store.append_session_event` 落库、重启后仍在；`POST /agent/event`
  返回自增 `seq`；`GET /agent/history` 在 Agent 层不可用时**仍返回本地条目**。
- **R3 幂等**：同 `key` 重复提交只保留一条；同一 `task.id` 的进度行不重复。
- **R4 归属**：`forShell` 排除 `source: "board"` 的过程文字；`forStage` 只返回本阶段条目
  （同一条内容不会同时出现在左右两侧）。
- **R5 不再钉底**：`taskProgressHost()` 固定返回 `#ocTinner`，不再回落到 `#ocTaskProgressHost`；
  任务卡按顺序进线程。DOM 宿主节点与 `setProject` 里的清空逻辑保留（既有守卫依赖它们）。
- **R6 回放完整**：`renderHistory()` 渲染 `timeline` 且不再跳过 `tech_ui`；
  阶段页加载后回放本阶段历史条目。
- **R7 不缩水**：`/agent/history` 既有字段、`/agent/send`、`/agent/new`、`/agent/meta`、
  `.oc-result-actions` 结果入口、既有动作名与看板协议全部保留；阶段页文字在本地线程里
  照旧可见。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_session_timeline_persistence_red -v`
  （后端与路由用带 fastapi/pydantic 的解释器子进程真跑；本机为
  `open-claude/.venv/bin/python`；缺解释器时自动跳过并在报告里说明）
- 相关回归：`python3 -m unittest tests.test_tech_board_state_envelope_dynamic
  tests.test_tech_board_deferred_actions_red tests.test_tech_left_chat_controls_restore_red
  tests.test_chat_collapsible_thinking_trace_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`
- 语法：`node --check tech_app/frontend/tech-session-timeline.js`、
  `node --check tech_app/frontend/agent-chat.js`
