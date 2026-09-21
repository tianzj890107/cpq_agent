# 规格：一键解析图纸的终态信号 + 2.1 零件可见（DWG / DXF 链路）

Spec 版本：1 · 状态：待实现（红测已就位）
红测：`tests/test_drawing_flow_parse_terminal_signal_red.py`
上游：`docs/specs/drawing-flow-frontend-wiring.md`（入口接线）、
`docs/specs/drawing-flow-error-taxonomy.md`（`blocked` 不是"这一步坏了"）、
`docs/specs/drawing-flow-non-editable-requirement.md`（需求不可编辑 → `blocked`）、
`docs/specs/packaging-dwg-parts-extraction.md`（零件文档与 2.1 零件树）

## 0. 现场（实测坐标，不是推断）

34 上两份真实 DWG 跑通链路后，用户看到的是两件事同时发生：**左栏零件清单空白**，
**看板落一张「图纸解析未完成」的失败卡**。三个缺陷叠在一起：

### D1 一键解析把"跑完了"报成"没跑成"（`app.js`）

- `tech_app/frontend/app.js:936-938`：drawing_flow 分支

  ```js
  if (currentDrawingEntry === "drawing_flow") {
    await runDrawingFlowParse();      // 真跑完，且它 return 了链路 payload
    parseDrawingError = "";
    return null;                      // ← 返回值被丢掉
  }
  ```

- `app.js:2763-2766`：后台链路只认返回值真假

  ```js
  const result = await parseDrawing();
  if (result) parseDrawingSettle("task-completed");
  else parseDrawingSettle("task-failed", parseDrawingError || "图纸解析未完成。");
  ```

  drawing_flow 模式下 `result` 恒为 `null`、`parseDrawingError` 恒为 `""`，于是**每次**都发
  `task-failed` + 逐字文案「图纸解析未完成。」—— 与链路真实结果无关。用户的原话就是这一条：
  「现在一键解析图纸出来就是图纸解析未完成」。

### D2 没有终态映射：成功 / 被阻断 / 真失败三者不分

后端每个步骤的载荷早已带齐判据（`packaging_drawing_flow/steps.py:38-52`：
`status` / `error_code` / `error_message` / `retryable` / `detail.action`），
`_public_state()`（`packaging_drawing_flow/__init__.py:160-189`）原样透出，`flow_state` 另有顶层
`status`。前端一个字段都没用上：只按 `if (result)` 二分。

后果：`REQUIREMENT_NOT_EDITABLE` 这类**前置条件缺失**（`status="blocked"`、`retryable=false`）
与真正的执行失败（`status="failed"`）在看板上长得一样，都是"失败"，而"重试"对前者必然再失败。

### D3 2.1 左栏空态不说原因（`index.html` + `renderTree()`）

- `tech_app/frontend/index.html:187`：

  ```html
  <div id="tree" class="parts-list empty-state">完成解析后显示零件清单</div>
  ```

- `app.js:1908-1913` `renderTree(ir)` 只渲染视觉模型 IR；drawing_flow 模式下 `currentIR` 永远是空，
  于是左栏永远是那句硬编码占位 —— 不说是没解析、没零件，还是缺前置条件，也没有下一步。

## 1. 目标

1. 一键解析的看板终态**必须等于链路的真实终态**：跑完 = `task-completed`，缺前置条件 = `task-blocked`
   （带缺什么 + 下一步、不可重试），只有真失败才是 `task-failed`；
2. 2.1 左栏空态必须说清"为什么没有零件"和"下一步做什么"，不许再出现通用占位；
3. 判定只有一处实现、可被 `node` 直接执行（纯函数），不散落在页面环境的 if/else 里。

## 2. 契约

### C1 终态判定只有一个纯函数

`tech_app/frontend/app.js` 顶层新增具名纯函数：

```js
drawingFlowTerminalSignal(flowState, error) -> {event, code, message, action, retryable}
```

- 声明成顶层 `function drawingFlowTerminalSignal(flowState, error) {…}`（红测按函数体抽取、
  交给 `node` **真跑**，不用文本 grep 判行为）；
- 不读 DOM、不读全局变量、不发请求、不写日志；`node -e` 取函数体直接 `eval` 即可跑；
- `event ∈ ("task-completed", "task-blocked", "task-failed")`；
- 判定按 `flowState.steps` 的**数组原序**（即步骤顺序）自上而下，第一条命中即返回：

| 序 | 条件 | `event` | `code` / `message` | `action` | `retryable` |
| --- | --- | --- | --- | --- | --- |
| 1 | 存在步骤 `status === "blocked"` | `task-blocked` | 取**最靠前**该步的 `error_code` / `error_message`（逐字） | 取该步 `detail.action`；缺则 `""` | `false` |
| 2 | 存在步骤 `status === "failed"` 或 `"unavailable"` | `task-failed` | 取最靠前该步的 `error_code` / `error_message`（逐字） | 该步 `detail.action` 或 `""` | 该步 `retryable === false` → `false`，其余 → `true` |
| 3 | `flowState` 是对象、`run_id` 非空、且至少一步 `status === "completed"` | `task-completed` | `""` / `""` | `""` | `true` |
| 4 | 其余（无 `flowState` / 非对象 / `run_id` 为空）：`error` 有值 | `task-failed` | `FLOW_RUN_REJECTED` / `error` 文案（逐字） | `""` | `true` |
| 5 | 其余且 `error` 也空 | `task-failed` | `FLOW_STATE_MISSING` / `"图纸解析链路没有返回状态，请重新解析。"` | `""` | `true` |

- 第 1 条优先于第 2 条：同一张图上既有 blocked 又有 failed 时，报缺前置条件（重试无用）而不是"失败"；
- `code` 与 `message` 一律**逐字**取后端载荷，前端不许重新措辞、不许拼接猜测。

### C2 一键解析不得再把成功报成失败

- `parseDrawing()` 的 drawing_flow 分支必须把链路终态交回后台链路（不得 `return null` 把结果丢掉）；
- `parseDrawingInBackground()` 发布的事件必须来自 `drawingFlowTerminalSignal(...)`，不得继续用
  `if (result)` 二分；
- `app.js` 源码里不得再出现字面量 `图纸解析未完成。`（"没跑成"的文案只能由 C1 第 4/5 条给）。

### C3 `blocked` 必须能看见"缺什么 + 下一步"

- `tech_app/frontend/tech-board-runtime.js` 的事件闭集 `EVENT` 新增 `TASK_BLOCKED: 'task-blocked'`，
  并纳入 `publishTaskCard()` 的白名单（与 `TASK_PARTIAL` 同级）；
- 父壳桥（`tech_app/frontend/agent-chat.js`）新增 `task-blocked` 分支，按
  `renderTaskProgress(Object.assign({}, payload, { status: "blocked" }))` 渲染；
- 状态词表 `taskStatusWord()` 新增 `blocked: "被阻断"`；`blocked` 与既有 `interrupted` 同级：
  **终态**、**不翻红**（红字只留给真失败）、不冒充"失败"；`interruptRunningCards()` 的终态集合
  也要把 `blocked` 算进去（已经是终态的卡不许再被标成"中断"）；
- 卡片正文必须同时出现载荷里的 `message` 与 `action`（`action` 为空时只出现 `message`），
  且**不得**出现"请重试"——这类问题的定义就是"重试必然再失败"；
- `app.js` 的 `parseDrawingSettle()` 载荷必须带 `code` / `message` / `action` / `retryable`
  （父壳据此区分"被阻断"与"失败"，不用猜文案）。

### C4 2.1 左栏空态必须说原因

`app.js` 顶层新增具名纯函数（同样声明成 `function packagingPartsEmptyText(partsDoc, preconditions) {…}`，
可被 `node` 直接 `eval`）：

```js
packagingPartsEmptyText(partsDoc, preconditions) -> string
```

- `partsDoc.parts` 非空 → `""`（有零件就不显示空态）；
- 否则若 `partsDoc.unavailable[].message` 有值 → 逐字拼接，多条用 `"；"` 连接；
- 再逐条追加 `preconditions`（来自 `GET /api/projects/{pid}/drawing-flow` 的 `preconditions`）：
  `"[code] message → action"`，`action` 为空时省略 `→ action` 段；
- 三者皆空 → `"零件文档还没生成，请先跑一键解析图纸。"`；
- 只要 `preconditions` 非空，返回串必须**逐字包含**每个 `code` 与每个非空 `action`；
- `index.html` 里 `#tree` 的硬编码占位「完成解析后显示零件清单」必须删掉（与零件提取 Spec §5 同一条）；
- `renderTree()` 在 `currentDrawingEntry === "drawing_flow"` 时按零件文档渲染（零件提取 Spec §5：
  行数 = `stats.part_total`、每行 `part_code` + `name` + `展开 长×宽 mm` + 图层），空态用
  `packagingPartsEmptyText`；非 drawing_flow 分支（视觉 IR）一个字都不改。

### C5 冻结面

- 不改 `parseDrawing()` 的非 drawing_flow 分支（视觉模型路径 `POST /parse` 照旧）；
- 不改既有四个看板事件（`task-progress` / `task-completed` / `task-partial` / `task-failed`）的
  信封与语义，`task-blocked` 是**新增**一个，不是改旧；
- 不改 `renderDrawingFlowPanel()` 的字段与步骤表（`error_code` / `error_message` / 状态词照旧）；
- 不改后端 `steps.py` / `gates.py` / 门禁判据，不动 `packaging_drawing_flow` 的任何语义；
- 前端不得自带 DWG 转换或几何解析，不得新增第二个解析按钮。

### C6 确定性

C1 / C4 两个纯函数同一入参两次调用结果逐字相同；不含时间戳、随机数、全局状态。

## 3. 验收标准

1. `./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red` 全绿
   （本机没装 `pytest`，用 `unittest`；`node` 必装，红测用 `node --check` 与真跑纯函数）；
2. 保护网不破：`test_drawing_flow_frontend_wiring_red`、`test_drawing_board_two_column_parts_and_3d_red`、
   `test_packaging_parts_extraction_red`、`test_drawing_flow_error_taxonomy_red` 的既有绿测一条都不许转红；
3. 34 真样本（实现后由 Codex 复跑）：一键解析跑完
   - 看板终态 = 链路终态（全 `completed` 时是"已完成"，不再出现「图纸解析未完成」）；
   - 需求已提交时出现「被阻断」+ `REQUIREMENT_NOT_EDITABLE` + 退回草稿的动作文案，且没有"请重试"；
   - 左栏零件行数 = `packaging-parts` 的 `stats.part_total`（零件提取实现落地后）。

## 4. 不在本批范围

- 零件提取本身（连通分量 → 展开尺寸 → BOM 行绑定）：`docs/specs/packaging-dwg-parts-extraction.md`；
- 真实图层名 / 产品级轮廓 / 盒型候选（`## 223`）：`docs/specs/packaging-product-outline-and-die-layer-roles.md`；
- 需求不可编辑的**后端**分类与前置条件枚举（`## 224`，实现已落地）：
  `docs/specs/drawing-flow-non-editable-requirement.md`；
- 盒型确认之后的 BOM / 工艺 / 成本 / 报价链路。
