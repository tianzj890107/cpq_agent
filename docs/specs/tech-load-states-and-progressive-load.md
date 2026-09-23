# Spec：2.1 与任务清单的「装载态 / 分阶段加载 / 慢请求反馈」——加载中不许说「没有图纸 / 没有任务」

状态：Spec + 红测（已实现）（原状：2.1 的 `#btnParse` 还在灰着时点一下，回包说「当前没有可解析的
图纸，请先上传 2D 工程图」；任务清单的读失败被折成空数组，页面说「暂时没有分派给你的任务」；
两个页面都没有首屏占位、没有分阶段加载、没有慢请求时长。2026-09-23 实测点名的红测
`tests/test_tech_load_states_and_progressive_load_red.py` 已 `Ran 34 … OK`，本行按状态守卫
`tests.test_spec_status_truth_red` 随事实更正）
红测：`tests/test_tech_load_states_and_progressive_load_red.py`
血缘：`docs/specs/packaging-business-parts-read-failure-note.md`（读失败 ≠ 还没有：同一套口径的先例）、
`docs/specs/packaging-drawing-flow-read-failure.md`（链路读失败要自己一句话）、
`docs/specs/chat-echo-must-pair-with-agent-output.md`（本批把「还在加载」与「被拒回执」分开之后，
回声气泡的成对性由那一份 Spec 收口）。
本批 changelog 条目号：`## 478`。

## 1. 实测证据（HEAD `0312614` 工作副本，代码级，都可指到行）

| 读数 | 实测 |
| --- | --- |
| `index.html:147` | `<button id="btnParse" class="start-parse-btn" disabled>▶ 开始解析</button>` —— 初始就是灰的 |
| `app.js:4160`（`openProject()` 末尾） | 只有整份项目读**回来之后**才按入口判定置位 `$("btnParse").disabled` |
| `app.js:5622-5625`（看板动作 `parseDrawing.run()`） | `if (button && button.disabled) return { ok:false, error:{ code:"not-ready", message:"当前没有可解析的图纸，请先上传 2D 工程图。" } }` —— 三种完全不同的处境（还在加载 / 项目打不开 / 3D 导入项目）共用同一句话 |
| `app.js:4161` | 3D 项目的真实原因只写在 `$("btnParse").title`（「这是 3D 模型导入项目（x.step）…」），回包一个字都不带 |
| 2.1 首屏 | `openProject()` 期间 `#tree` 是空的、`#btnParse` 灰、状态栏还没有字（`status(...)` 在 `openProject` 末尾才写） |
| `app.js:4153-4154` | 读是**串行**的：`await loadDrawingFlowPanel()` → `await refreshPackagingParts()`（后者内部再串行读几何分量与业务部件） |
| `grep data-qq-stage` | **0 处**（没有阶段可见性，也没有"哪一段还没回来"的披露） |
| `cpq-tech-inbox.js:82-85` | `catch (e) { tasks = []; }` + `loaded = true` ⇒ `renderTaskCards()` 渲染 `暂时没有分派给你的任务。`（**读失败伪装成空态**） |
| `cpq-tech-inbox.js:153/157` | 只有 `正在读取待办任务…` 与「暂时没有…」两态，没有读失败态、没有重试 |
| 链路步记录（`packaging_drawing_flow/__init__.py:_execute_step`） | 有 `started_at` / `finished_at`（`now_iso()` 秒级），**没有** `duration_ms`；`grep duration_ms tech_app/` 只命中转换器（`cad_converter`） |
| 真跑一次链路 | 酒盒.dwg 端到端 8.9 s（八步全 `completed`）—— 足够长，用户需要看得见"还在跑、跑到哪一步" |

## 2. 契约

### 2.1 C1：2.1 的装载状态只有一个来源

`app.js` 顶层新增 `pageLoadState`（闭集 `"idle" | "loading" | "ready" | "failed"`）与
`pageLoadStartedAt`（`Date.now()` 毫秒）：

- `openProject()` 一进来（**发第一个请求之前**）置 `pageLoadState = "loading"` 且
  `pageLoadStartedAt = Date.now()`；
- `openProject()` 的 `finally` 落 `"ready"`（成功）或 `"failed"`（抛异常）；
- 其它任何地方只读不改（不许各自维护第二份"我这边加载完了"）。

### 2.2 C2：`parseDrawing` 的三态判定抽成纯函数 `parseActionReadiness(input)`

入参 `{ state, startedAt, disabled, reason, now }`（`reason` = `#btnParse.title` 的原文；
`now` 默认当前毫秒），出参**要么** `null`（可以开始解析）**要么** `{ code, message }`：

| 情形 | 出参 |
| --- | --- |
| `state === "loading"` | `{ code:"loading", message:"图纸信息还在加载中（已用 8s），请稍候。" }`（秒 = `Math.floor((now-startedAt)/1000)`） |
| `state === "loading"` 且 `startedAt` 不是有限数字 | `{ code:"loading", message:"图纸信息还在加载中（已用不到 1s），请稍候。" }` |
| `state` ∈ `{"ready","failed"}` 且 `disabled` 且 `reason` 非空 | `{ code:"not-ready", message: reason }`（**逐字取按钮 title** —— 3D 项目因此说得出真正原因） |
| `state` ∈ `{"ready","failed"}` 且 `disabled` 且 `reason` 空 | `{ code:"not-ready", message:"当前没有可解析的图纸，请先上传 2D 工程图。" }`（既有兜底一字不改） |
| `state` ∈ `{"ready","failed"}` 且 `!disabled` | `null` |
| `state` 是 `undefined` / `"idle"` / 闭集外的值 | 按 `loading` 处理（**不许**把"还不知道"说成"没有图纸"） |

纯函数：体内无 `document` / `window` / `fetch(` / `localStorage` / `sessionStorage`。
`parseDrawing.run()` 的第一句判定必须走它（源码里出现 `parseActionReadiness(`），
`disabled` 仍在 `{ok:false}` 时**不进**后台任务（护栏）。

### 2.3 C3：`getState()` 要说出"在加载"

`parseDrawing` 的 `getState()` 多一个 `state` 字段：`loading` 时 `{ visible:true, enabled:false,
busy:true, state:"loading" }`；`ready` / `failed` 时给出同名 `"ready"` / `"failed"`。
`enabled` 仍以 `#btnParse.disabled` 为准（护栏：不许改成别的来源）。

### 2.4 C4：首屏占位（loading 期间不许渲染终态文案）

- 纯函数 `pageLoadElapsedText(startedAt, now)`：`< 1000ms` → `已用不到 1s`；`1000..59999ms` →
  `已用 {n}s`（整数秒）；`>= 60000ms` → `已用 {m} 分 {s} 秒`；非法入参 → `""`。
- `openProject()` **第一件事**（早于第一个 `await fetch(`）就在 `#tree` 渲染占位节点，带
  `data-qq-page-skeleton="1"`，文案含 `正在读取项目…` 与 `pageLoadElapsedText(...)` 的结果；
  占位必须在 `finally` 里被清掉/替换（成功、失败两条路都要），不许残留。
- `pageLoadState === "loading"` 期间，`packagingPartsEmptyText()` / `packagingBusinessImportNote()`
  的"没有零件 / 还没有权威清单"终态文案**不许**进 DOM（它们是终态结论，不是加载态）。

### 2.5 C5：分阶段加载（四段各自可见、并发开始、单段失败不牵连）

- 四段固定顺序与名字：`core`（项目本体）、`flow`（图纸解析链路）、`geometry_parts`（几何分量）、
  `business_parts`（业务部件）；每段状态闭集 `loading | ready | failed`。
- 渲染出 `data-qq-stage="<name>"` 与 `data-qq-stage-state="<state>"` 两个钩子（四段都要有）。
- `refreshPackagingParts()` 里几何分量与业务部件两段必须**并发**开始（源码里出现 `Promise.all(`），
  不许一段等另一段；`core` 慢不许挡住 `flow` 先画出来。
- 纯函数 `loadStagesLine(stages)`（`stages` = `{name: state}` 或行数组）：
  - 全部 `ready` → `已就绪 4/4 段`；
  - 有 `failed` 且还有 `loading` → `已就绪 2/4 段，1 段读取失败`（先报失败，再报在途）；
  - 有 `loading` 无 `failed` → `已就绪 1/4 段，3 段读取中`；
  - 非法/空入参 → `""`。

### 2.6 C6：任务清单读失败不许伪装空态

`tech_app/frontend/cpq-tech-inbox.js`：

- `loadTasks()` 的 `catch` 必须记下读失败（`inboxError`：`{code, status}` 或等价形状），**不许**
  只写 `tasks = []`；成功一次后清空；
- `renderTaskCards()` 在 `inboxError` 非空时渲染 `data-qq-inbox-error="1"` 与逐字文案
  `这一次读不到待办任务（HTTP 500），请稍后重试；这不代表没有分派给你的任务。`
  （网络错误没有状态码时写 `（网络错误）`），并给一个重试按钮 `id="cpqInboxRetry"`（点击重跑
  `loadTasks()`）；
- `暂时没有分派给你的任务。` 只在 `inboxError` 为空时出现（护栏：字面量逐字保留）；
- `正在读取待办任务…` 保持（首屏加载态，护栏）。

### 2.7 C7：慢请求要能说出"这一步花了多久"

- 链路步记录新增 `duration_ms`（整数毫秒，用单调时钟量，**不改** `started_at` / `finished_at`
  的既有 ISO 形状）；旧 run 没有这个键时按缺失处理；
- 纯函数 `drawingFlowStepDurationText(step)`：
  - `duration_ms` 为正整数 → `1.2 s`（保留 1 位小数）；`>= 60000` → `1 分 2 秒`；
  - 缺失 / `0` / 负数 / 非数字 → `""`（**不许**显示 `0.0 s` —— 那会把"没量到"说成"用了 0 秒"）；
- `renderDrawingFlowPanel()` 的步骤表必须调用它（源码里出现 `drawingFlowStepDurationText(`）。

### 2.8 C8：护栏

`renderDrawingEntry()` 的入口判定（vision / drawing_flow / blocked_3d / blocked_other）、
既有 `not-ready` 兜底文案、既有读失败文案（`packagingBusinessReadProblemText` /
`drawingFlowReadProblemText` / `packagingPartsPageReadProblemText`）、
`正在读取待办任务…`、`暂时没有分派给你的任务。` **一个字都不许改**；
不加依赖、不连 PG、前端不做几何解析。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：`pageLoadState` / `pageLoadStartedAt`；`parseActionReadiness()`；
   `pageLoadElapsedText()`；`loadStagesLine()`；`drawingFlowStepDurationText()`；`openProject()`
   的装载态与首屏占位；`parseDrawing` 动作的三态判定与 `getState()` 的 `state`；
   `refreshPackagingParts()` 的两段并发与阶段钩子；`renderDrawingFlowPanel()` 的时长列。
2. `tech_app/frontend/cpq-tech-inbox.js`：`inboxError` + 读失败态 + 重试按钮。
3. `tech_app/backend/services/packaging_drawing_flow/__init__.py`：步记录 `duration_ms`
   （以及为它需要的单调时钟取时；`now_iso()` 的形状不改）。
4. `changelog/changelog_9_21_25.md`：`## 478`。

禁止：改 `renderDrawingEntry()` 的判定；改任何既有错误码的语义；改链路步序 / 门禁 / 主管线；
改既有测试；改快速报价五批与成本/工艺公式；新增依赖；把加载态写成"没有图纸"。

## 4. 未做 / 边界（如实记）

- 本批**不**做骨架屏的视觉设计（只要求占位节点、文案与清理时机正确）；样式可留空。
- 本批**不**改任务清单的数据来源（仍旧 `/wf/tasks`），只加读失败态与重试。
- `duration_ms` 只用于**披露**（步骤表多一个时长），不进任何门禁判据。
- 未起服务、未连 PG / 34、未写业务数据。

## 5. 红测与反向对照

红测：`tests/test_tech_load_states_and_progressive_load_red.py`
（P 组 `parseActionReadiness` / E 组 `pageLoadElapsedText` / G 组阶段 / N 组 2.1 首屏与动作接线 /
D 组步级时长 / I 组任务清单读失败 / 护栏组）。

- 红基（把本批三个生产文件还原成 HEAD `0312614` 工作副本）：`Ran 34 … FAILED (failures=32)`
  —— 32 红 / 2 绿。红的正好是 P1–P8（没有 `parseActionReadiness()`）、E1–E5（没有
  `pageLoadElapsedText()`）、G1–G6（没有 `loadStagesLine()`、没有 `Promise.all(`、没有
  `data-qq-stage*`）、N1–N4（没有 `pageLoadState`、没有首屏占位、加载态没有落到渲染处）、
  D1–D5（没有 `drawingFlowStepDurationText()`、链路没有 `duration_ms`）、
  I1/I2/I3/I5（`loadTasks()` 把读失败折成空数组，没有读失败态与重试）；
  绿的 2 条是护栏：I4（`暂时没有分派给你的任务。` / `正在读取待办任务…` 字面量）、
  N5（既有 `not-ready` 兜底文案一字不改）。

### 5.1 测试侧修正授权（2026-09-23，Codex；断言与期望值一字未改）

**`EXTRACT_JS` 的 harness 有两处缺陷，P1–P7 对任何实现都不可能通过**（用 HEAD 代码 + 修好的
harness 复跑确认：P 组 8 条全部以「`app.js` 缺少纯函数 `parseActionReadiness()`」失败 ——
修正只是让调用能发出去，不是放宽断言）：

1. `readiness(**kwargs)` 传下去的是**单个对象**，而 harness 的
   `args.map(a => JSON.stringify(a))` 只认位置参数数组 —— 未进入 `try` 就抛
   `TypeError: args.map is not a function`（在任何实现被调用之前）；
2. P2 的 `float("nan")` 被 `json.dumps` 写成 `NaN` 字面量，`JSON.parse` 直接解不开。

授权把 harness 改成：单对象入参按一个位置参数调用（`Array.isArray(args) ? args : [args]`），
并把 JSON 文本里的 `NaN` 按 `null`（= "缺少"）解析。**断言、期望文案、用例数、分组一律未动**
（先例：`## 465` / `## 467` 两批同样只修 harness）。`NaN` 与 `null` 在本契约里同属"量不出来"，
P2 因此仍能判"不许把还在加载说成没有图纸"。

### 5.2 反向对照（本机实测）

```text
② 把 refreshPackagingParts() 的 Promise.all( 改回两个串行 await   ⇒ G5 单条红
④ 删掉 renderTree() 里的装载态闸门                                ⇒ N4 单条红
①' 删掉 openProject() 的首屏占位块（带 data-qq-page-skeleton）     ⇒ N2 + N3 两条红
③' 删掉 renderTaskCards() 的读失败分支                            ⇒ I2 + I3 + I5 三条红
```

**不符合预期的两条如实记**：§5 原写的 ①（把 `pageLoadState = "loading"` 改成 `"ready"`）与
③（把 `loadTasks()` 的 `catch` 里 `inboxError` 去掉）**都不转红** —— N/I 两组是**源码守卫**
（只查字面量是否出现，不执行 `openProject()` / `loadTasks()`），这两种改法仍留着那些字面量，
所以判不出来。上表的 ①' / ③' 用"删掉这一支"才真正转红。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 478`）

| 契约 | 落点 | 复跑结果 |
| --- | --- | --- |
| C1 装载态唯一来源 | `app.js`：`pageLoadState` / `pageLoadStartedAt`；`openProject()` 入口置 `loading`、`finally` 收口 | N1 绿；成功落 `ready`、抛异常落 `failed` |
| C2 三态判定 | 纯函数 `parseActionReadiness()`；`parseDrawing.run()` 第一句走它，被拒时**不进**后台任务 | P1–P8 绿；harness 缺陷修正见 §5.1 |
| C3 看板报装载态 | `getState()` 新增 `state`（`enabled` 仍以 `#btnParse.disabled` 为准） | `test_tech_drawing_agent_actions_red` 8 OK 未见回退 |
| C4 首屏占位 | `openProject()` 第一个请求**之前**画 `data-qq-page-skeleton`，`finally` 两条路清掉；`renderTree()` 装载期间只画"正在读取零件文档…" | N2/N3/N4 绿；反向对照 ①'/④ |
| C5 分阶段 | `PAGE_LOAD_STAGE_NAMES` 四段 + `data-qq-stage` / `data-qq-stage-state`；`refreshPackagingParts()` 用 `Promise.all(`；纯函数 `loadStagesLine()` | G1–G6 绿；反向对照 ②（G5 单条红） |
| C6 任务清单读失败 | `cpq-tech-inbox.js`：`inboxError` + `data-qq-inbox-error="1"` + `#cpqInboxRetry`（点击重跑 `loadTasks()`） | I1/I2/I3/I5 绿；I4 两条字面量未动；反向对照 ③' |
| C7 步级时长 | 链路 `_execute_step()` 新增 `duration_ms`（`time.monotonic()`，`started_at` / `finished_at` 的 ISO 形状不改）；纯函数 `drawingFlowStepDurationText()` 进步骤表 | D1–D5 绿 |

## 7. 红基（原文保留，`## 6` 改名前的实测）

```text
./open-claude/.venv/bin/python -m unittest tests.test_tech_load_states_and_progressive_load_red
Ran 34 tests … FAILED (failures=32)
```

- 红 32 条：P1–P8（没有 `parseActionReadiness()`）、E1–E5（没有 `pageLoadElapsedText()`）、
  G1–G6（没有 `loadStagesLine()`、没有 `Promise.all(`、没有 `data-qq-stage*`）、
  N1–N4（没有 `pageLoadState`、没有首屏占位、加载态没有落到渲染处）、
  D1–D5（没有 `drawingFlowStepDurationText()`、链路没有 `duration_ms`）、
  I1/I2/I3/I5（`loadTasks()` 把读失败折成空数组，没有读失败态与重试）；
- 绿 2 条是护栏：I4（`暂时没有分派给你的任务。` / `正在读取待办任务…` 字面量）、
  N5（既有 `not-ready` 兜底文案一字不改）。
