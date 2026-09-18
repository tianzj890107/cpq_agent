# 规格：过程行「运行中必须是圆圈 / 有内容必须真折叠 / 状态展示行用 ·」

> 本批（## 142）是对 `quote-tech-process-row-fold-restore.md`（## 136）与
> `quote-tech-process-row-product-contract.md`（## 133）的**增补修正**：
> ## 133 的「无『详情』、无输入输出 JSON、图标 ✓/○/⚠、颜色落在文字上、报价与技术工艺统一」
> 与 ## 136 的「标题行即展开开关、折叠区与开关成对、卡片收尾该卡全行 ✓」全部保留；
> 本批只修三处用户仍然看得到的问题：**运行中提前变 ✓**、**裸子节点 = 假折叠**、
> **状态展示行没有独立行类型**。
> 红测：`tests/test_process_row_running_info_and_fold_red.py`（26 条，实现前 14 条失败）。
>
> 用户另有一条硬要求：「不只是图纸解析的输出气泡，而是**报价和技术工艺所有的**都应该是统一的」，
> 因此本批的 A/B/C 合同必须同时覆盖四个过程行入口：技术工艺左栏
> `agent-chat.js::pushTaskStep`、3 阶段页 `assembly-integration.js::aiProcessCard`、
> 4 阶段页 `cost-review.js::crCard`、报价页 `确认需求解析结果.html::addToolActivity`。

## 1. 背景与真实问题

用户现场（技术工艺智能体，3.2 参数推荐 / 3.3 组装工艺）贴出真实渲染结果，指出：

1. **「正在的时候应该是圆圈，但是实际上正在的时候就已经是 ✓ 了」** —— 卡片还在跑，
   过程行已经显示 ✓。
2. **「里面还是有多余的很奇怪的缩进和换行，而且这些并没有折叠展开而是都还是显示出来」**
   —— 有的子行缩进、换行、并且完全没有折叠，全部平铺在卡片里。
3. **「对于里面的详情，如果是一个任务步骤就 ✓，展示状态的不需要，展示状态的就用 · 就行了」**
   —— 点名 `产品族 锂离子电池包 / 模组：报价成品参数 14/25 已给出（必填 4/11）`、
   `报价必填项仍缺：产品系列、产品型号、成品编码、标称电压、标称容量` 这类**统计陈述行**，
   它们是「展示状态」而不是「任务步骤」，不应该跟着步骤行一块儿翻 ✓。

## 2. 当前行为（node 实跑真渲染器 `agent-chat.js::pushTaskStep`，非推断）

场景 S1 = 用户现场 3.2 的实时序列，实测（`tests/test_process_row_running_info_and_fold_red.py::out()`）：

```
s1_running  top=3  bare=2  folded=2  details=1
  running   ○        top    调用模型（qwen3.5-plus）
  completed ✓        BARE   汇总 2.1 零件、1.x 需求与本步整合图纸
  completed ✓        BARE   载入报价成品参数字典（46 个字段 / 5 个产品族）
  completed ✓        top    正在调用模型推荐整机参数与整合方案
  completed ✓        top    参数 25 条、连接 3 处、BOM 5 行
  completed ✓        folded 产品族 锂离子电池包 / 模组：报价成品参数 14/25 已给出（必填 4/11）
  completed ✓        folded 报价必填项仍缺：产品系列、产品型号、成品编码、标称电压、标称容量
```

三处根因（都在 `pushTaskStep`）：

- **运行中提前 ✓**（`agent-chat.js:1658-1659`）：
  `itemState = … : (detail && status === 'running') ? 'running' : 'completed'`
  —— **没有显式状态的行一律按「完成」渲染**。于是卡片还在跑时，只要这一行不是
  「带 running detail 的模型行」，就一律是 `completed` + ✓。
- **裸子节点 = 假折叠**（`agent-chat.js:1688-1695`）：`opensCall` 分支把先到的普通行
  `item.append(row)` 直接挂成调用行的**直接子节点**（没有 `tool-detail` 折叠区、
  没有开关、不是收起态）。这些行既不能折叠、又会继承父行的横向 flex 布局，用户看到的
  正是「多余缩进 / 换行 + 没有折叠、全都显示出来」。实测该场景 `bare=2`。
- **状态展示行没有独立行类型**（同上 `itemState`）：统计 / 覆盖率 / 汇总陈述行
  （`共 N 道组装工序：…`、`参数 N 条、连接 N 处、BOM N 行`、`…已给出（必填 N/M）`、
  `…仍缺：…`）只能跟着步骤行翻 ✓，没有 `·` 的落点。

同一合同在另外三个入口的实测缺口（`tests/test_process_row_running_info_and_fold_red.py`
E 组，同样 node 实跑真渲染器）：

- **3 阶段页 `aiProcessCard` / 4 阶段页 `crCard`**：`rowFor()` 把每一行硬编码成
  `data-state="completed"` + `✓`，所以卡片还在跑时每一行都已经是 ✓；
  `done(ok)` 里的「running → completed」收尾翻转因此是死代码。实测
  `asm_running` 四行全是 `completed/✓`，`cr_running` 两行全是 `completed/✓`。
- **报价页 `addToolActivity`**：`makeItem()` 运行中给的是 `data-state="running"` + `○`
  （这部分已经正确），但 `markTraceDone()` 会把**所有** `tool-item` 无条件翻成 ✓，
  包括状态展示行；报价页同样没有 `·` 行类型。实测 `quote_running` 两行都是 `running/○`，
  `quote_done` 两行都被翻成 `completed/✓`。
- 三个入口的子行都已折进 `tool-detail` 折叠区（`bare==0`、`details==toggles`），
  这部分是既有正确能力，红测 E5 作为守卫项。

## 3. 目标合同

### A. 运行中必须是圆圈，终态才翻 ✓（四个入口一致）

- 卡片未到终态（`data-status="running"` 且尚未收尾）时，**未显式标注终态**的过程行
  一律 `data-state="running"` + `○`。
- 卡片成功收尾（`setAssistantState(..., "succeeded")`）后，该卡自己的过程行
  （含折叠区里的行）才收成 `completed` + ✓；失败行保留 `⚠`。
- 卡片一开始就是成功终态（历史回放 `data-status="completed"` 的卡片）时，直接渲染 ✓，
  不留圆圈。
- 收尾翻转继续覆盖任务卡（`oc-task-steps` 挂在 `turn.body` 的形态）。

### B. 有子内容必须是真折叠，禁止裸子节点（四个入口一致）

- 有子内容的行必须带 `[data-agent-role="tool-detail"]` 折叠区，子行落在其中、**默认收起**。
- 标题行即开关（`tool-toggle` + `role="button"` + `tabindex="0"` + `aria-expanded="false"`），
  点击 / Enter / Space 切换；折叠区与开关**成对**出现，`details` 数量 == `toggles` 数量。
- **禁止**把子行裸挂成另一条 `tool-item` 的直接子节点（中间没有 `tool-detail`）；
  这是 「多余的缩进 / 换行 + 没有折叠」的直接来源。
- 行标题文本不得带前导 / 尾随空白，不得残留 `↳`。
- 无内容可展开的行：不建折叠区、也不挂开关。

### C. 行分两类：任务步骤行 vs 状态展示行（四个入口一致）

- **任务步骤行**：`○`（运行中）/ `✓`（完成）/ `⚠`（失败），参与收尾翻转。
- **状态展示行**：`·` + `data-state="info"`（统计 / 覆盖率 / 汇总陈述），
  **不参与收尾翻转**（卡片成功后仍然是 `·`）。
- 报价页 `markTraceDone()`、阶段页 `done(ok)` 收尾时只允许翻**任务步骤行**，
  状态展示行保持 `·` + `data-state="info"`。
- 判定优先级：
  1. 显式 `detail.kind === "info"` → 状态展示行；`detail.kind === "step"` → 任务步骤行。
  2. 历史文本按**封闭句式兜底**判定为状态展示行，至少覆盖：
     - `共 N 道组装工序：…`
     - `参数 N 条、连接 N 处、BOM N 行`
     - `…已给出（必填 N/M）`
     - `…仍缺：…`
- **必须明确**：新增句式不得让任务步骤行被误判成状态行。反例（必须仍是任务步骤行）：
  `检索工艺库：ASSY…`、`正在调用模型编制组装工序明细`、`查询条件：…`、`命中 …`、
  `库内无同类件，按新制评估`、`正在调用模型推荐整机参数与整合方案`。
- `·` 只能出现在 `data-state="info"` 行上，不得回退成 ## 133 之前的「点」。

### D. ## 133 / ## 136 合同不变（不得减少）

- 界面上没有「详情」字样；不渲染输入输出 JSON / `{}` / `{}`。
- 图标与标题同行；命中绿 / 未命中橙；模型 / 工具色调保留。
- 折叠区与标题行开关成对，除标题行外没有第二套开关（原生 `summary` / `button` 计数为 0）。
- 报价侧（`确认需求解析结果.html::addToolActivity` / `showStage`）、技术工艺左栏、
  3 阶段页（`assembly-integration.js::aiProcessCard`）、4 阶段页（`cost-review.js::crCard`）
  共用同一套行渲染合同。

## 4. 状态与行类型定义

| 行类型 | `data-state` | 图标 | 卡片成功后 | 判定 |
| --- | --- | --- | --- | --- |
| 任务步骤-运行中 | `running` | ○ | → `completed` ✓ | 默认（未显式终态） |
| 任务步骤-已标注失败 | `failed` | ⚠ | 保留 ⚠ | `tone==='err'` / `detail.status==='failed'` |
| 状态展示行 | `info` | · | 仍是 `·` | `detail.kind==='info'` 或封闭句式兜底 |

## 5. 数据与兼容

- 不改后端 `tasks.py::report_progress / process_event`、不改 `phase ∈ {model, tool, progress}`、
  不改 SSE 载荷；本批只在渲染层补 `info` 行类型与兜底句式。
- 历史回放走同一渲染入口：老任务 `progress_log` 里带 `↳` 的文本行，按封闭句式兜底判成
  `info`，并折进父行折叠区；不补造、不删除历史文本。
- 图标字符沿用 `✓` / `○` / `⚠`，新增 `·`（`\u00b7`，与产品侧既有状态点一致）。

## 6. 非目标

不改后端 / SSE / 工具协议 / 数据库 / Prompt / `font-family`；不做流式追加；
不改卡片外层视觉；不新增字体；不重构整体渲染框架。

## 7. 自动化验收

```
./open-claude/.venv/bin/python -m unittest tests.test_process_row_running_info_and_fold_red
```

允许修改的文件（仅这些）：`tech_app/frontend/agent-chat.js`、`tech_app/frontend/agent-chat.css`、
`tech_app/frontend/assembly-integration.js`、`tech_app/frontend/cost-review.js`、
`确认需求解析结果.html`，以及各页 `?v=` cache-buster。

红测对照（26 条）：

- **A 运行中圆圈**：A1 卡片在跑时未标注终态的行是 `running` + ○、且不出现 ✓；
  A2 卡片成功后收成 `completed` + ✓；A3 历史回放已成功卡片直接 ✓。
- **B 真折叠**：B1 没有裸子节点（`bare==0`）；B2 子行落在折叠区、默认收起、`details==toggles`；
  B3 折叠区里的行不可见；B4 标题无前导空白、无 `↳`。
- **C 行类型**：C1 `detail.kind='info'` → `·` 且卡片成功后仍是 `·`，`kind='step'` 正常翻 ✓；
  C2 统计句式兜底 → `·`；C3 步骤 / 结果行保持 ○/✓/⚠、不得用 `·`；C4 只有 `info` 行能用 `·`。
- **D 合同不减少**：D1 无「详情」、无 JSON；D2 每行都有图标与标题；D3 卡片成功后不留圆圈。
- **E 跨入口同合同**：E1 阶段页（组装整合 / 成本）卡片在跑时过程行是 `running` + ○；
  E2 阶段页成功后步骤行收成 ✓；E3 阶段页状态展示行是 `·` + `data-state="info"`
  （运行中与完成后都不得翻 ✓）；E4 报价页 `addToolActivity` 运行中 ○、
  `markTraceDone()` 后步骤行 ✓ 而状态展示行仍是 `·`/`info`；
  E5 四个入口的折叠区与开关成对、没有裸子节点（守卫项）。
- 另有 7 条脚手架自检（A–E 每类各 ≥1 条 + Harness），抽不到 `pushTaskStep` /
  `setAssistantState` / `aiProcessCard` / `crCard` / `addToolActivity` 等函数时
  失败落在脚手架，不是需求。

## 8. 人工验收

技术工艺智能体跑一次「参数推荐」或「组装工艺」、再在 3 阶段页 / 4 阶段页 / 报价页各跑一次
带过程行的动作：卡片还在跑时，每一行左边是 ○（不是 ✓）；
带子内容的行点标题行才展开，再点收起；卡片完成后**任务步骤行**变 ✓，而
`产品族 …已给出（必填 4/11）`、`报价必填项仍缺：…`、`共 N 道组装工序：…`、
`参数 N 条、连接 N 处、BOM N 行` 这类**状态展示行**保持 `·`；全卡没有「详情」二字、
点开也看不到 JSON，也没有多余的缩进 / 换行。

## 9. 不允许减少的既有能力

- ## 133：无「详情」、无输入输出 JSON、图标 ✓/○/⚠、色调落在文字上、两侧统一。
- ## 136：标题行即展开开关、折叠区与开关成对、卡片收尾该卡全行 ✓、
  报价页也要有标题行开关。
- 命中可复用 / 可改制绿、未命中 / 按新制橙、模型行 / 工具行色调与既有折叠内容
  （查询条件、命中件与匹配度、差异、库内条数）一条不少。
