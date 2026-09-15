# Spec：看板动作的静态 role 必须在运行时快照里生效（1.2 / 1.3 / 3.2 主按钮被静默降级）

## 背景（用户反馈）

「通过确认」（1.2）与「提交审核意见」（1.3）在左侧统一操作栏里不是蓝色实心主按钮，
而是白底描边的次要按钮。经实测根因**不在页面样式，也不在父壳渲染**，而在看板运行时的
动作快照解析：

`tech_app/frontend/tech-board-runtime.js:114` 的 `entryState()` 只从 `getState()` 的返回值
里取 `role`：

```js
var raw = {};
if (typeof entry.getState === 'function') raw = entry.getState() || {};
...
role: raw.role === 'primary' ? 'primary' : 'aux',
```

条目外层声明的静态 `role` 从来没有进入 `raw`，因此凡是「外层写 `role: 'primary'`、`getState()`
只返回 `visible/enabled/busy`」的条目，运行时都会把 `role` 判成 `'aux'`。这同时违背了同一段
代码上方（`:126`～`:129`）的既有注释：

> role 只认白名单 'primary' / 'aux' …… **三者既可来自静态条目，也可由 getState() 动态返回**
> （2.2 的主操作反转就靠它）。

即：实现与它自己声明的契约不一致，属于运行时缺陷，不是页面写法问题。

## 事实依据（实测）

- 全仓库九个阶段页面里，**条目外层静态声明 `role: 'primary'` 的只有三处**：
  - `tech_app/frontend/requirement-confirm-page.js:162` `confirmRequirement`（1.2「✓ 通过确认」）
  - `tech_app/frontend/requirement-review-page.js:87` `submitRequirementReview`（1.3「提交审核意见」）
  - `tech_app/frontend/report-review-result.js:72` `approveProcessReport`（3.2「审核通过并进入下一步」）
  这三处的 `getState()` 都**不返回** `role`，所以三颗按钮在真实快照里全是 `aux`。
- 其余条目的 `role` 都由 `getState()` 动态返回（2.2 参数/工艺反转、2.3 测算↔确认成本、
  3.1 生成草稿↔提交审核、3.3 发布↔回传），或外层写 `'aux'` 且无 `getState` 冲突，不受影响。
  没有任何条目「外层写 primary、运行时应当降级为 aux」。
- 父壳只渲染快照里 `role === 'primary'` 的那一颗：`tech-workbench.js:648` `primaryEntries()`、
  `:722`～`:760` 主槽位、`:680` `button.classList.toggle('primary', ...)`（蓝色实心样式）。
  快照里没有 primary 时父壳只写诊断、不兜底，所以这三颗按钮在左侧操作栏里降级成了普通按钮；
  1.2、1.3 因此出现「这一页没有主按钮」。
- 现有测试只做了源码文本断言（例如 `tests/test_tech_global_single_primary_and_nonblocking_notices_red.py`、
  `tests/test_tech_confirm_review_optional_note_red.py` 断言源码里出现 `role: 'primary'`），
  没有按运行时的真实快照规则执行 `getState()`，所以这个缺陷在 136 项测试全绿的情况下漏检。

## 修复原则（单一事实来源）

1. **在运行时修，不在页面里逐颗抄 `role`。** 静态条目就是主按钮角色的单一事实来源；
   在 `getState()` 里再写一遍 `role` 会让同一颗按钮有两处声明，改一处漏一处，同样的坑会
   在下一个新页面重演。
2. **静态元数据只回退「元数据」，不回退「状态」。** 参与回退的只有 `role` / `order` / `hint`
   （与既有注释一致）；`visible` / `enabled` / `busy` / `active` / `analyzed` 继续只由
   `getState()` / `entry.state` / `updateActionState()` 决定，避免把静态声明误当成运行时状态。
3. **合并优先级不变**：静态元数据 < `entry.state` < `getState()` 返回值 < `updateActionState()` 覆盖值。
4. **`getState()` 抛错时保留静态元数据基线**（与既有 `label` 的回退语义一致），照旧不向上抛。

## 契约

```
entryState(name)
  base   = { role: entry.role, order: entry.order, hint: entry.hint }   // 缺失即缺省
  raw    = merge(base, entry.state, getState())    // 后写的覆盖先写的；getState 抛错时保留 base
  raw    = merge(raw, overrides[name])             // updateActionState 最高优先级
  role   = raw.role === 'primary' ? 'primary' : 'aux'   // 白名单，其余（含缺失、非法值）→ 'aux'
  order  = 合法数字转 Number，否则 null（既有规则不变）
  hint   = 字符串，否则 ''
  label  = raw.label ?? entry.label ?? name（既有规则不变）
```

`primaryAudit()` / `primaryDiagnostics()` / 事件信封（`type: 'state'`、`name: 'action-state'`、
`payload.subject`、`payload.actions` / `view` / `primary`）与 `NAMESPACE` / `VERSION`
一律不变。

## 范围

**允许修改**

- `tech_app/frontend/tech-board-runtime.js`：`entryState()` 里加入静态 `role` / `order` / `hint`
  基线（唯一必需修改点）。

**禁止**

- 不得在 `requirement-confirm-page.js` / `requirement-review-page.js` /
  `report-review-result.js` 的 `getState()` 里补写 `role: 'primary'`（制造第二处事实来源）；
  这三处的**外层静态 `role: 'primary'` 必须保留、不得删除或挪位**。
- 不得改动作名、`run` 实现、`silent` / `deferred` 标记、九阶段白名单、协议常量与事件名。
- 不得改父壳 `tech-workbench.js` 的主按钮判定（`role === 'primary'` 且恰好一颗）。
- 不得为「让测试变绿」而新增按钮、改文案或放宽 `primaryAudit` 的唯一主按钮约束。
- 不新增任何后端接口、不改后端服务。

## 验收要求

- **R1 静态 role 生效**：`{ role: 'primary', getState: () => ({ visible, enabled, busy }) }`
  的快照 `role === 'primary'`，并且该快照经 `publish('action-state', ...)` 发出的
  `payload.actions[name].role === 'primary'`（父壳真实拿到的就是这一份）。
- **R2 主按钮审计**：上例 `auditPrimary()` 返回 `primary_count === 1`、`ok === true`、
  `primary_actions === ['confirmRequirement']`。
- **R3 动态优先**：`getState()` 返回 `role: 'aux'` 时覆盖静态 `'primary'`；
  `updateActionState(name, { role: 'aux' })` 覆盖前两者（既有通道不被破坏）。
- **R4 不误升**：外层 `role: 'aux'` 且 `getState()` 不返回 `role` → 仍是 `'aux'`；
  完全没有 `getState()` 的静态 `primary` 条目 → `'primary'`。
- **R5 抛错兜底**：`getState()` 抛错时快照仍带静态 `role` / `order` / `hint`，且不抛异常。
- **R6 order / hint**：`getState()` 未返回时用静态值，返回时以 `getState()` 为准。
- **R7 真实页面三处条目**：三个文件仍以**外层静态 `role: 'primary'`**为唯一事实来源
  （`getState()` 里不得出现 `role:`），且动作名与标签不变。
- **R8 不缩水**：`window.TechBoardRuntime` 仍导出
  `registerActions` / `registerViews` / `updateActionState` / `refreshState` / `setContext` /
  `snapshot` / `auditPrimary` / `primaryDiagnostics` / `publish` / `emitState` / `publishStatus` /
  `setView` / `nextRequestId`；`NAMESPACE = 'cpq:tech-board'`、`VERSION = 1` 不变；
  九个阶段页面注册的动作名与数量不变。

## 明确不在本批范围（避免误判为已修复）

- **3.2 / 3.3 某些状态下「有可见动作但一颗主按钮都没有」**（例如 3.3 发布页在报告
  `draft` / `in_review` / `rejected` 时，只有 `createReportNewVersion` 可见而没有任何
  `primary`）。这类状态缺的是**业务上的下一步入口/门禁设计**，属于「下一步与未来步骤解锁」
  批次，本批不新增按钮、不改页面状态机，也不得用「随便挑一颗当主按钮」掩盖。
  本批只保证：这类状态仍由 `primaryDiagnostics()` 给出确定性诊断，不会被静默当成正常。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_board_static_action_role_snapshot_red -v`
- 相关回归：`python3 -m unittest tests.test_tech_global_single_primary_and_nonblocking_notices_red tests.test_tech_confirm_review_optional_note_red tests.test_tech_board_state_envelope_dynamic tests.test_tech_board_deferred_actions_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`
- 语法：`node --check tech_app/frontend/tech-board-runtime.js`
