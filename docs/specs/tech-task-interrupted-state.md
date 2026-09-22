# Spec：任务终态「中断」（沿用蓝色）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_task_interrupted_state_red.py`

## 背景（用户反馈）

技术工艺统一工作台左侧的任务进度卡只有四个状态：排队中 / 进行中 / 已完成 / 失败。
`taskStatusWord()` 对**任何未知状态都兜底成「进行中」**，而系统里真实存在的第三种收尾方式
没有任何名字：

| 现实发生的事 | 现在卡上显示 | 应该显示 |
| --- | --- | --- |
| 服务在任务执行期间重启（`recover_interrupted_tasks()` 把在途任务置 `failed`） | 失败（红）或重进项目后永远「进行中」 | 中断 |
| 用户在任务在途时切走看板（桥 `failPending('detached')` 取消在途命令） | 永远「进行中」（命令已取消，不会再有收尾事件） | 中断 |
| 看板 20s 没回执（桥 `code=timeout`，文案「…超时未响应」） | 永远「进行中」+ 红字提示 | 中断 + 中性原因行 |
| 2.2 / 2.3 阶段页轮询到服务重启中断的任务 | 死循环轮询（只认 succeeded / failed） | 中断并停止轮询 |

用户口径：**新增这个终态，就叫「中断」；颜色沿用现有的蓝色，不改配色**——
即中断与「进行中」用同一套蓝色 chip（`#e0edff` / `#0050C4`），既不新造颜色、也不刷红字。

## 事实依据（实测）

| 位置 | 现状 |
| --- | --- |
| `tech_app/frontend/agent-chat.js` `taskStatusWord()`（:1305） | 词表只有 `queued / running / succeeded / failed`，兜底返回「进行中」 |
| `agent-chat.js` `setTaskStatus()`（:1346） | 只切 `is-queued / is-running / is-succeeded / is-failed` 四个类 |
| `agent-chat.js` `renderTaskProgress()`（:1353） | 只处理 `failed` 的红字原因行（`.oc-task-error`）；`completed → succeeded` |
| `agent-chat.js` `bindBoardBridge()`（:~1862） | `detached` 分支只清摘要；桥 `type: "error"` 的 `code=timeout` 完全不进任务卡 |
| `agent-chat.js` `pollMatchTask()`（:1537） | 只认 `succeeded / failed`，`interrupted` 会一直轮询 |
| `assembly-integration.js` `aiPollTask()`（:297） / `cost-review.js` `crPollTask()`（:239） | 同上，`while(true)` 无上限、只认 succeeded / failed |
| `agent-chat.css`（:531-538） | `.oc-task-state` 四态；`.oc-task-error` 是红字；`is-running` 的蓝＝`#e0edff` / `#0050C4` |
| `tech_app/backend/services/tasks.py` `recover_interrupted_tasks()`（:175） | 把在途任务置 `failed` + `progress="服务重启中断"`，**没有任何收尾事件写进会话时间线** |
| `tech_app/backend/storage/store.py` `append_session_event()`（:1218） | 时间线唯一落库入口：`key` / `task.id` 幂等且就地更新 |

## 目标契约

### 一、终态「中断」的词汇与颜色

- 任务卡状态词表新增 `interrupted → 「中断」`；未知状态仍照既有兜底，不改。
- `setTaskStatus()` 在切换状态时要清掉 `is-interrupted`，再按新状态加类。
- CSS 新增 `.oc-task-card.is-interrupted .oc-task-state`，取值必须与
  `.oc-task-card.is-running .oc-task-state` **逐项同值**（`#e0edff` / `#0050C4`）——
  中断沿用蓝色，不新增颜色 token。
- 中断的原因行用**中性** `.oc-task-note`（灰色，不是红色），**不得**再用
  `.oc-task-error`（红字只留给真正的失败）。
- 中断是终态：卡建一次、状态就地翻转，落库与回放都走既有 `persistTaskCard()` /
  `replayTimelineTask()`，不新增存储字段。

### 二、中断的唯一判定入口

- 中断由**一个**判定函数收口：`isInterruptedCode(code)`，码集合固定为
  `["interrupted", "detached", "timeout"]` —— 服务重启中断、切看板取消、桥超时。
- `renderTaskProgress()` 必须：`detail.status === "interrupted"` 或
  （非 succeeded 且命中中断码）时，把这张卡渲染成 `interrupted`；

### 三、在途任务不许永远「进行中」

- 切看板（板桥 `detached`）时，把尚未收尾的任务卡翻成中断，原因写明「看板已切换，任务已中断。」
  （不新建卡、不清落库历史）。
- 桥报 `type: "error"` 且 `code` 命中中断码（20s 超时）时同样翻成中断，原因用桥给的真实文案。
- 收尾事件（`succeeded` / 后续真实失败）到达时仍可把中断卡就地翻成新状态 —— 中断不是不可逆，
  但**没有收尾事件时不得停在「进行中」**。

### 四、阶段页轮询把中断当终态

- `assembly-integration.js` `aiPollTask()`、`cost-review.js` `crPollTask()`、
  `agent-chat.js` `pollMatchTask()`：任务状态为 `interrupted` 时**停止轮询**，把真实原因带出去；
  上报失败事件时带 `code: "interrupted"`，父壳据此把卡标成「中断」而不是「失败」。
- 2.2 / 2.3 的过程卡（`aiProcessCard.done()` / `crCard.done()`）支持第三种收尾：中断。
  仍是同一张卡、同一个状态 chip（`.oc-alabel-state` 加 `is-interrupted`，蓝色同 `is-running`），
  文案 `⏸ 中断`，不新增第二行 / 第二张卡。
- CSS 新增 `.oc-alabel-state.is-interrupted`，取值与 `.oc-alabel-state.is-running` 一致。
- 业务失败（`code` 不是中断码）一律维持既有「失败」路径与红字原因，不得被中断吞掉。

### 五、后端的「服务重启中断」

- `recover_interrupted_tasks()`：在途任务的终态从 `failed` 改为 `interrupted`；
  `progress` 仍写「服务重启中断」、`error` 仍保留原文（可追溯原因）、`finished_at` 照写。
- 同时把这条中断**写进项目会话时间线**（`store.append_session_event()`，`kind="task"`、
  `key=f"task:{task_id}"`、`task.status="interrupted"`），否则浏览器关着时被中断的任务
  重进项目只剩「进行中」。
- 该函数仍返回恢复条数；启动开关 `TASK_RECOVER_ON_START` 与调用点（`main.py` 启动清理）不变。

## 不在本批范围

- 不改桥协议：事件名仍只有 `task-progress / task-completed / task-failed`，
  `QUIET_FAILURE_CODES`、`DEFAULT_TIMEOUT` 一个不动；`detached` 仍走「预期内失败」不刷提示。
- 不改后端路由、权限、`_handoff_key`、成本 / 工艺 / 报告数据与阶段白名单。
- 助手回复卡的状态 chip 仍是三态（◌ 运行中 / ✓ 已完成 / ⚠ 失败），中断只加在**任务卡**与
  **阶段页过程卡**上。
- 2.1 图纸页（`app.js` / `cost.js` / `process.js`）与 1.1 需求页（`requirement-create.js`）的轮询
  本轮不改（1.1 自带 210s 上限）；它们的分支另有归属，避免把本批摊成翻页式重构。

## 验收标准

1. `taskStatusWord()` 含 `interrupted → 中断`；`setTaskStatus()` 会清 `is-interrupted` 并按新状态加类。
2. `.oc-task-card.is-interrupted .oc-task-state` 与 `.is-running` 的 `background` / `color` 同值
   （`#e0edff` / `#0050C4`）。
3. 中断原因行进 `.oc-task-note`（非红），中断卡不产生 `.oc-task-error`；失败路径原样保留。
4. `isInterruptedCode()` 覆盖 `interrupted / detached / timeout`，并被 `renderTaskProgress()` 使用。
5. 切看板与桥超时会把在途卡翻成中断（`interruptRunningCards()` 跳过终态卡）。
6. `aiPollTask()` / `crPollTask()` / `pollMatchTask()` 对 `interrupted` 停止轮询，并带 `code: "interrupted"`。
7. `aiProcessCard.done()` / `crCard.done()` 支持中断态，`is-interrupted` 蓝色，文案 `⏸ 中断`。
8. `recover_interrupted_tasks()` 写 `interrupted`（progress 仍「服务重启中断」）并把中断卡写进会话时间线。
9. 桥协议、静默失败码、后端路由、助手卡三态、阶段白名单全部不变。

## 测试命令

```
python3 -m unittest tests.test_tech_task_interrupted_state_red -v
node --check tech_app/frontend/agent-chat.js
node --check tech_app/frontend/assembly-integration.js
node --check tech_app/frontend/cost-review.js
python3 -m py_compile tech_app/backend/services/tasks.py
python3 -m unittest discover -s tests -p 'test_*.py'
```
