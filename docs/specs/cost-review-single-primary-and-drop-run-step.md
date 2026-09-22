# 技术工艺 2.3「成本测算」：去掉「运行成本测算」，主按钮随测算完成度反转

状态：Spec + 红测（已实现）
红测：`tests/test_cost_review_single_primary_and_drop_run_step_red.py`

## 1. 问题（实测）

左侧操作栏由看板动作快照渲染（`tech-workbench.js` 的 `boardActionEntries()` 只跳过
`visible === false`，主按钮只认 `role === 'primary'`）。站在 2.3 成本测算页时
`cost-review.js` 的 `registerActions` 会往左侧栏推出 6 颗按钮：

| 动作 | label | 现状 | 问题 |
| --- | --- | --- | --- |
| `runCostReview` | 一键测算全部成本 | 条目上静态写 `role: 'primary'`（order 10） | 主按钮没生效（见下） |
| `confirmCostReview` | 确认成本 | `role: 'aux'`（order 20） | 算完之后主按钮仍不出现 |
| `writeCostReviewMaterial` | 写入数据库 | `aux`（order 30） | — |
| `sendCostReviewToQuote` | 回传销售经理继续报价 | `aux`（order 40） | — |
| `returnCostReviewToProcess` | 提交工艺经理确认 | `aux`（order 50） | — |
| `costStep` | 运行成本测算 | `visible: true`（order 70） | 与「一键测算全部成本」是同一件事的第二颗按钮 |

`refreshCostReview` 第 26 批起已 `visible: false`，不在栏内。

两点症状：

1. **「运行成本测算」是重复入口**。它要求 `payload.step` ∈ `part / assembly / all`，
   而左侧栏不会给任何 step，用户只能点出 `bad-step`。它真正的调用方是 Agent
   （`RunCostReviewPart / Assembly / All` → `cost-step` → `bridge.executeAction("costStep")`）
   与并发内部链路，本就不该出现在用户按钮栏里；旁边还有一颗同义的
   「一键测算全部成本」，多一颗必然点错。
2. **2.3 根本没有主按钮**。`tech-board-runtime.js:114` 的 `entryState()` 只从
   `getState()` 读 `role`（`role: raw.role === 'primary' ? 'primary' : 'aux'`），
   动作条目上的静态 `role: 'primary'`（`cost-review.js:778`）是死元数据。
   结果是「一键测算全部成本」与「确认成本」都渲染成描边按钮：测算前没有起点高亮，
   测算完成后也没有任何视觉收口，用户不知道这一步该点哪颗。

## 2. 目标

1. **「运行成本测算」退出左侧操作栏**：`costStep.getState()` 返回 `visible: false`；
   动作注册、参数校验与后台链路一字不改（Agent 与内部链路继续经 `executeAction` 使用）。
2. **主按钮随「成本是否算全」反转**：
   - 成本没算全 → 「一键测算全部成本」是 2.3 唯一主按钮；
   - 成本算全 → 「确认成本」是 2.3 唯一主按钮。
3. **反转只读一份判定**：复用本页唯一前置判定 `crConfirmBlocker()`，不新增第二份
   `counts` / `ready` / 后端字段判断，避免两处漂移。
4. **「确认成本」始终可见可点**：前置不满足时不置灰，点了返回真实原因（既有契约：
   `enabled: true`，闸门由 `crConfirmBlocker()` 决定），三个去向、内部视图、后端接口、
   桥协议、右侧看板一律不动。

## 3. 契约 A：左侧可见动作表（`cost-review.js`）

| 动作 | `getState().visible` | `role` | `order` |
| --- | --- | --- | --- |
| `runCostReview`（一键测算全部成本） | `true`（不变） | `!crCostsComplete() ? 'primary' : 'aux'` | 10 |
| `confirmCostReview`（确认成本） | `true`（不变） | `crCostsComplete() ? 'primary' : 'aux'` | 20 |
| `costStep`（运行成本测算） | `false`（本批改） | `'aux'` | 70 |
| `refreshCostReview` | `false`（第 26 批） | `'aux'` | 60 |
| `writeCostReviewMaterial` / `sendCostReviewToQuote` / `returnCostReviewToProcess` | `true`（不变） | `'aux'` | 30 / 40 / 50 |

- 两颗按钮的 `role` 由同一个判定反转，因此 2.3 任何时刻恰好一颗主按钮，不需要也不得
  调整 `order`：父壳 `syncChatActions()` 把 `role === 'primary'` 的那一个单独放进
  `#techChatPrimary` 槽位，其余按 `order` 紧随其后（`tech-workbench.js:694-710`）。
- 动作条目上**不再保留静态 `role: 'primary'`**（它是死元数据，且会与真实快照自相矛盾）；
  真实 role 只由 `getState()` 返回。既有契约「每个 stage 静态 primary ≤ 1 且必须有
  动态 primary」由动态写法满足。
- `runCostReview` / `confirmCostReview` 的 `label`、`deferred: true`（前者）、
  `run()` 主体（`crRunAllInBackground()` / `crConfirmBlocker()` + `crConfirmCost()`）
  一律不变；`getState()` 继续显式 `enabled: true`，忙闲交给 `busy`。

形状（`role` 必须与判定写在同一行，保持既有「每个 stage 必须有动态 primary」契约可通过；
动作条目顶层的静态 `role: 'primary'` 要删掉，改成 `role: 'aux'` 或直接省略）：

```js
runCostReview: {
  label: '一键测算全部成本',
  role: 'aux',            // 真实 role 由 getState() 返回，这里只留一个不会自相矛盾的默认值
  order: 10,
  deferred: true,
  run: () => { /* 既有实现不动 */ },
  getState: () => ({ visible: true, enabled: true, busy: Boolean(crBusy),
                     role: !crCostsComplete() ? 'primary' : 'aux' }),
},
confirmCostReview: {
  label: '确认成本',
  role: 'aux',
  order: 20,
  run: async () => { /* 既有实现不动 */ },
  getState: () => ({ visible: true, enabled: true, busy: Boolean(crBusy),
                     role: crCostsComplete() ? 'primary' : 'aux' }),
},
costStep: {
  label: '运行成本测算',
  role: 'aux',
  order: 70,
  deferred: true,
  run: (payload) => { /* 既有校验与后台链路一律不动 */ },
  getState: () => ({ visible: false, enabled: true, busy: Boolean(crBusy) }),
},
```

## 4. 契约 B：「成本是否算全」的唯一判定

新增一个薄封装（放在 `crConfirmBlocker()` 旁边），只复用既有判定，不重写条件：

```js
/* 「成本是否算全」的唯一判定：直接复用 crConfirmBlocker()（本页唯一前置判定），
   不新增第二份 counts / ready 判断。只读身份在这里按「未算全」处理 —— 它只决定
   谁是主按钮，不决定能不能点：点了照旧由后端 403 与看板提示给出真实原因。 */
function crCostsComplete() {
  return !crConfirmBlocker();
}
```

- 函数体内**不得**出现 `counts` / `missing` / `assembly_costed` / `crData.ready` 之类
  的第二份条件；`crConfirmBlocker()` 继续是 `parts / missing / assembly_costed / zero`
  与只读身份的唯一出处。
- 两个 `getState()` 都必须经 `crCostsComplete()` 取 role，不得各写一份。

## 5. 契约 C：能力不缩水

- `costStep` 仍注册、`label`/`order`/`deferred` 不变，`run(payload)` 仍校验
  `step ∈ {part, assembly, all}`（`part` 必带 `part_id`）并复用 `crRunPart` /
  `crRunAssembly` / `crRunAll` 与 `crCostStepInBackground()`；只改 `visible`；
- Agent 工具链不动：`oc_agent.py` 的 `RunCostReviewPart / RunCostReviewAssembly /
  RunCostReviewAll → "cost-step"`、`agent-chat.js` 的 `cost-step` 分发与
  `executeAction("costStep", …)` 全部保留，且左侧会话仍不得直接调 `/cost-review`；
- 后端路由一个不少：`GET/PUT /api/projects/{project_id}/cost-review`、
  `/cost-review/parts/{part_id}`、`/cost-review/assembly`、`/cost-review/confirm`、
  `/cost-review/material-write`、`/cost-review/send-to-quote`、
  `/cost-review/return-to-process`；
- 内部视图不动：`registerViews` 的 `parts` / `assembly` / `total` 与显式拒绝的
  `params`（`moved-to-integration`）照旧；
- 右侧看板不动：`#crRunAll`（一键测算全部成本）与 `#crOpsCard .ai-ops` 在 `.tech-embed`
  作用域仍隐藏，`#crActions` 里的「重算全部零件 / 测算组装成本」与逐零件按钮照旧
  （它们是页内单件 / 单环节重跑入口，不属于左侧操作栏）；
- 父壳不动：仍只按 `entry.visible === false` 过滤、只按 `role === 'primary'` 选主按钮，
  不得为 `costStep` / `cost-review` 之类动作名写特例；
- 桥协议不动：`READY / ACTION_STATE / TASK_PROGRESS / TASK_COMPLETED / TASK_FAILED /
  SELECTION_CHANGED / BOARD_STATUS` 白名单不改。

## 6. 边界（本批禁止改动）

- 不改后端路由、service、`oc_agent.py` 工具映射，不新增任何接口；
- 不改 `tech-board-runtime.js` / `tech-board-bridge.js` / `cpq:tech-board` 信封与事件白名单；
- 不改 `crConfirmBlocker()` / `crConfirmCost()` / `crRunOp()` / `crRunAll()` 的既有语义与文案；
- 不改 `runCostReview` / `confirmCostReview` 的 label、order、run 主体与错误码；
- 不把三个去向并进「确认成本」，不改它们的权限与审计链路；
- 不删任何动作注册与实现，不改 1.x / 2.1 / 2.2 / 3.x 的动作可见性；
- 不动右侧看板的页内按钮与 `.tech-embed` 隐藏规则。

## 7. 验收

1. 站在 2.3 成本测算页：左侧不再出现「运行成本测算」；成本没算全时「一键测算全部成本」
   是实心主按钮，「确认成本」是描边次按钮。
2. 点「一键测算全部成本」跑完（零件逐件 + 整机汇总都算全）后，左侧主按钮自动变成
   「确认成本」，「一键测算全部成本」降为次按钮；任何时刻只有一颗主按钮。
3. 「确认成本」在没算全时也可点：点了返回真实原因（缺零件数 / 整机未算 / 0 元行 /
   只读身份），不置灰、不静默。
4. Agent 说「测算这个零件的成本」「测算组装成本」「测算全部成本」时仍能经
   `costStep` 真正跑起来，左侧照样有进度与收尾卡片。
5. 第 3 大步（组装与整合）与 2.1、1.x、3.x 的任何按钮不因本批变化。
6. 全量回归不得新增失败点。

测试命令：

```
python3 -m unittest tests.test_cost_review_single_primary_and_drop_run_step_red -v
python3 -m unittest tests.test_tech_cost_review_agent_red tests.test_tech_business_actions_clickable_then_error_red tests.test_tech_board_deferred_actions_red tests.test_tech_board_actions_into_left_toolbar_red
node --check tech_app/frontend/cost-review.js
git diff --check
```
