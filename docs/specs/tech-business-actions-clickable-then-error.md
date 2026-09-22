# 技术工艺：业务动作按钮一律可点，点了再给真实原因

状态：Spec + 红测（已实现）
红测：`tests/test_tech_business_actions_clickable_then_error_red.py`

## 1. 问题（实测）

统一工作台左侧会话区的业务按钮会**永久灰掉**，点不动也就拿不到任何原因，流程硬卡死：

- `确认工艺并发送财务` 亮不起来：`tech_app/frontend/assembly-integration.js:1513` 的
  `sendIntegrationToFinance.getState()` 直接读页内按钮 `enabled: Boolean(button) && !button.disabled`，
  而 `:847` 的 `financeBtn.disabled = !ready` 要求
  `params_confirmed && process_confirmed && params_final && !required_missing` 四项同时成立。
  少了任何一项，左侧那颗按钮就永远是灰的，用户看不到"差在哪"。
- `确认成本` 同理：`tech_app/frontend/cost-review.js:779` 读 `#crConfirm` 的 `disabled`，
  而 `:385` 的 `disabled = !ready` 里 `ready = crData.ready && !crBusy && !readOnly`。
  一旦 `ready` 为假，左侧按钮永久灰掉；确认不了成本，`写入数据库 / 回传销售经理继续报价 /
  提交工艺经理确认` 又都要求 `confirmed`（`:387-389`），于是「回传销售经理继续报价」整条链路走不下去。
- 灰掉之后**没有出路**：`run()` 虽然写了结构化错误（例如 `:773` 的 `not-ready`），但按钮被禁用，
  这段错误永远跑不到；父壳也没有「为什么不能点」的可见提示，只有一句 `当前不可用`。
- 父壳快照不会自动刷新：看板的 `action-state` 只在 `updateActionState()` / 注册时发布，
  而 `crConfirmCost()`（`:602`）与 `crRunOp()`（`:482`）的 `finally` 只做 `crBusy = false; crRender();`，
  **不重新发布快照** —— 父壳缓存里那颗按钮的 `enabled` 一直停在上一次的值，即使业务状态早已变好。
- 2.3 的角色白名单与后端不一致：`cost-review.js:34` 的 `CR_COST_ROLES = ['finance_mgr','admin']`
  写了一个后端不存在的角色码，后端权威白名单是
  `tech_app/backend/services/auth.py:55` 的 `COST_ROLES = {"finance_manager","admin"}`。
  真正的财务负责人登录后被前端误判成"只读"，按钮灰掉，还没点到后端就先被拦。
- 顺带的静默返回：`aiOpenFinanceDialog()`（`assembly-integration.js:907`）开头 `if (aiBusy) return;`、
  父壳 `runBoardAction` 的 `if (!state.project) return;` —— 点了没反应，用户只会当成"系统坏了"。

## 2. 目标

1. **业务动作按钮一律可点**：`enabled` 只表达"这一步根本没有这个动作"（`visible: false`），
   以及"同一个动作正在执行"（`busy: true`）。前置条件没满足不再把按钮灰掉。
2. **点了再判**：`run()` 必须自己做前置校验，不满足就返回结构化错误
   `{ ok: false, error: { code, message } }`，`message` 必须是**具体缺什么**（复用页内已有的 `why` 文案）。
3. **错误必须看得见**：父壳把失败原因写进标题行提示位（batch 18 的红色 `.is-error`），
   并同步一条左侧会话提示；禁止静默 `return`。
4. **快照自动刷新**：看板每次渲染后重新发布一次动作快照，父壳按钮的 `busy` / 可见性不会停在上一帧。
5. **修真正的前置条件错误**：2.3 角色白名单必须与后端 `auth.COST_ROLES` 一致；只读身份不再禁用左侧按钮，
   改为点击后给出明确原因（后端仍是权威，403 照旧）。
6. 后端权限、路由与页内按钮语义一律不动 —— 本批只改"不让点的判定"和"点了以后怎么报错"。

## 3. 契约 A：父壳不再按 `enabled === false` 禁用（`tech-workbench.js`）

- `syncChatActionList()`：`button.disabled = !visible || !state.project || entry.busy === true;`
  （删掉 `entry.enabled === false`）。
- `syncChatActions()` 的主按钮槽位：`enabled: Boolean(primaryEntry) && primaryEntry.busy !== true
  && Boolean(state.project)`（删掉 `primaryEntry.enabled !== false`）。
- `actionTooltip()`：删掉 `if (item.enabled === false) return ${label}（${hint || '当前不可用'}）`
  这条分支 —— 按钮还能点，就不能在 tooltip 里宣称"不可用"。`执行中` 与 `请先打开项目` 保留。
- `runBoardAction()`：`if (!state.project) return;` 改为明确提示（标题行 + 会话），不得静默返回。
- `runBoardAction()` 的失败分支：`setBoardNotice(message, 'error')`（接 batch 18 的等级参数），
  并在 `window.ocTechAgent && typeof window.ocTechAgent.notice === 'function'` 时追加一条会话提示。
- `agent-chat.js` 的 `window.ocTechAgent` 增加 `notice: pushSystem`（会话提示的唯一入口，不在父壳里新建气泡）。

## 4. 契约 B：运行时提供"重新发布快照"（`tech-board-runtime.js`）

- 新增并导出 `refreshState()`：重新发布当前 `action-state` 快照，**不写 `overrides`**
  （`updateActionState()` 是覆盖语义，不能拿来刷新）。
- `entryState()` 的字段契约（`label/visible/enabled/busy/active/analyzed/role/order/hint`）一律不变：
  `enabled` 字段继续存在并继续下发，只是它的取值不再表达前置条件。

## 5. 契约 C：2.2 组装与整合（`assembly-integration.js`）

- 注册动作的 `getState()` **不得再读页内按钮的 `disabled`**，也不再出现 `enabled: !aiBusy` / `enabled: !busy`：
  一律 `enabled: true`，忙闲只看 `busy: aiBusy`。`visible` 语义不变（页签专属动作照旧按 `aiTab` 隐藏）。
- 新增 `aiFinanceBlocker()`：把 `aiRenderOps()` 里那段 `why` 计算抽成唯一来源（只判前置条件，不含 busy），
  `aiRenderOps()` 与动作 `run()` 共用同一份，不允许两处漂移。
- `sendIntegrationToFinance.run()`：

  ```js
  run: async () => {
    const why = aiFinanceBlocker();
    if (why) { aiStatus(why, true); return { ok: false, error: { code: 'not-ready', message: why } }; }
    return aiOpenFinanceDialog();
  }
  ```

- `aiOpenFinanceDialog()`：`if (aiBusy) return;` 改为返回
  `{ ok: false, error: { code: 'busy', message: '正在处理中，请稍后再发送财务。' } }`，
  并把同一句写进页内提示（`aiStatus(msg, true)`）。
- `aiRender()` 末尾调用新增的 `aiPublishState()`；`aiPublishState()` 内部调用
  `window.TechBoardRuntime.refreshState()`（带存在性守卫，独立打开阶段页无副作用），
  同帧内多次渲染用 `setTimeout(..., 0)` 合并成一次，避免刷爆消息通道。

## 6. 契约 D：2.3 成本测算（`cost-review.js`）

- `CR_COST_ROLES` 必须与后端 `auth.COST_ROLES` 完全一致（`finance_manager` / `admin`），
  不得再出现 `finance_mgr` 这类后端不存在的角色码。
- 注册动作的 `getState()` 不得读 `#crConfirm` 等页内按钮的 `disabled`，`enabled` 一律 `true`，
  忙闲只看 `busy`。
- 新增 `crConfirmBlocker()`：抽出 `crRenderActions()` 里那段 `why`（只读身份 / 缺件数 / 整机未算 /
  0 元行 / 未确认），`crRenderActions()` 与动作共用。
- `confirmCostReview.run()`：不再看按钮 `disabled`，改为

  ```js
  run: async () => {
    const why = crConfirmBlocker();
    if (why) { crStatus(why, true); return { ok: false, error: { code: 'not-ready', message: why } }; }
    const done = await crConfirmCost();
    return done ? { ok: true }
      : { ok: false, error: { code: 'confirm-failed', message: '确认成本失败，请查看右侧看板提示。' } };
  }
  ```

- `crRender()` 末尾调用 `crPublishState()` → `TechBoardRuntime.refreshState()`，同帧合并一次。
  这样 `crConfirmCost()` / `crRunOp()` 收尾后，父壳立刻拿到新快照，
  `写入数据库 / 回传销售经理继续报价 / 提交工艺经理确认` 不用手动刷新就能点。
- 只读身份继续在页内提示（`标题 + crReadOnlyWhy()`），但**不再作为左侧按钮的禁用条件**；
  点击后由 `crConfirmBlocker()` 给出只读原因，后端 `_require(..., auth.COST_ROLES)` 照旧兜底。

## 7. 非目标与保护边界

- 不改 `cpq:tech-board` 信封、`STAGE_EVENTS` 白名单、`payload` 形状与 `entryState()` 字段契约。
- 不改后端任何路由、`_require(..., auth.COST_ROLES)` 权限判定与 403 语义。
- 不改页内按钮自身的 `disabled` 语义（例如 `#aiParamsConfirm` 仍是 `!has || aiBusy`、
  `#crConfirm` 仍按 `crData.ready`），本批只改"注册动作快照 + 左侧会话区按钮"的判定。
- 不改九阶段 stage id、页面映射、标题行结构、右侧看板卡片结构。
- 不新增第二套成本算法、第二套解析逻辑，不新增后端接口字段。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 8. 验收

- `python3 -m unittest tests.test_tech_business_actions_clickable_then_error_red -v` 全绿。
- 回归：`tests.test_tech_board_actions_into_left_toolbar_red`、
  `tests.test_tech_assembly_disabled_vs_busy_red`、`tests.test_tech_step_status_blue_pill_red`、
  `tests.test_tech_board_bridge_protocol_red`。
- `node --check` 覆盖 `tech-workbench.js`、`tech-board-runtime.js`、`assembly-integration.js`、
  `cost-review.js`、`agent-chat.js`；`git diff --check` 通过。
- 浏览器：2.2 在参数/工艺未确认时点「确认工艺并发送财务」，右侧看板与标题行同时给出**具体**缺项原因；
  补齐后按钮可直接点通，发送成功。2.3 点「确认成本」同理；确认成功后左侧
  「回传销售经理继续报价」无需手动刷新即可点击。

## 9. 对应测试

`tests/test_tech_business_actions_clickable_then_error_red.py`
