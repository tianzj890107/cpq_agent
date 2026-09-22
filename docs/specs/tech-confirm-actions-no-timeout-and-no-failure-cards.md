# 2.2 / 2.3 收口动作不再超时、失败不再建底部卡片

状态：Spec + 红测（已实现）
红测：`tests/test_tech_confirm_action_timeout_and_no_pinned_cards_red.py`

## 用户反馈

「⚠ 确认并进入下一页签超时未响应」「⚠ aiSetTab is not defined」这些报错都不要再有卡片了，
因为有卡片之后会固定在底部。

## 现状缺口（源码已核实）

### A. `aiSetTab is not defined`（真实 ReferenceError）

`assembly-integration.js` 里 `aiSetTab` 只在 `aiRegisterTechBoardActions()` 这个 IIFE 内部
用 `const aiSetTab = (name) => {…}` 声明（视图注册那几行用它），而两个收口函数
`aiConfirmDrawingsAndNext()` / `aiConfirmParamsAndNext()` 定义在外层，
却直接调用 `aiSetTab('params')` / `aiSetTab('process')` —— 外层作用域根本没有这个标识符，
一点「确认并进入下一页签」就是 ReferenceError。看板运行时把异常消息发成 `task-failed`，
父壳又把它渲染成会话底部的失败卡。

### B. 「确认并进入下一页签超时未响应」

桥（`tech-board-bridge.js`，`DEFAULT_TIMEOUT = 20000`）等的是动作 `run()` 的 Promise。
上一批给「确认并进入下一页签 / 确认成本」加了**人工确认框**（缺项时等人点「仍要继续」），
而这两个动作仍是普通（非 deferred）动作 —— 人的思考时间被算进 20 秒超时，
于是桥超时并报「确认并进入下一页签超时未响应」。

### C. 失败卡钉在底部

`agent-chat.js renderTaskProgress()` 的 `keepFailure`：只要失败载荷带真实原因，
即使没有任何执行明细也会新建 `.oc-task-card`，而它长在 `#ocTaskProgressHost`
（会话底部常驻宿主）—— 建出来就永远钉在底部，聊多少轮都不动。

## 目标

1. `aiSetTab` 收口到模块作用域，收口函数不再抛 ReferenceError。
2. 「确认并进入下一页签」「确认成本」改成 deferred：`run()` 立即回执（桥不再等人工确认），
   真正的执行放到后台壳里，成败由本页播报（标题行提示位 + 普通会话输出）。
3. 失败不再单独建卡：没有执行明细的失败不建 `.oc-task-card`；已有卡（有真实 progress_log）
   照旧就地翻成失败态并显示原因。

## 允许修改范围

- `tech_app/frontend/assembly-integration.js`
- `tech_app/frontend/cost-review.js`
- `tech_app/frontend/agent-chat.js`
- 相关页面静态资源版本号、本批 spec / 红测 / changelog

## 禁止事项

- 不改 `cpq:tech-board` 信封、七个事件、`tech:command` 方向与 projectId/stage 校验；
  不改桥的 `DEFAULT_TIMEOUT` 与 `QUIET_FAILURE_CODES`。
- 不新增后端路由 / 字段 / Agent 工具；不改业务实现（`aiParamsFinalize`、`aiConfirmStep`、
  `crConfirmCost`、`aiAskProceed`、`crAskProceed` 一字不改）。
- 不删动作注册与 `run()` 语义；业务失败仍必须可见（标题行 + 普通会话输出）。
- 不提交、不推送、不建 MR/tag/Release、不部署（按当次用户指令执行）。

## 实现要点

1. `assembly-integration.js`：把 `aiSetTab` 提成模块级 `function aiSetTab(name) { aiTab = name; aiRender(); }`
   （与视图注册共用一个），删掉 IIFE 里的 `const aiSetTab`。
2. 两个收口动作加 `deferred: true`，`run` 改为「启动后台壳 + 立即 `{ok:true}`」：
   - `aiConfirmParamsAndNextFlow()` / `crConfirmCostFlow()`：调用既有函数，
     成功不额外播报，失败把 `error.message` 写进 `aiStatus(message, true)`（标题行）与 `aiSay(...)`（普通输出）。
   - `aiConfirmParamsAndNext()` / `crConfirmCostFlow()` 的判定与实现全部保留（含软闸门确认框）。
3. `agent-chat.js renderTaskProgress()`：去掉 `keepFailure`，建卡闸门改成
   `log.length > 0 || existingCard`；已有卡的失败态与 `.oc-task-error` 更新逻辑保留。

## 验收标准

- `python3 -m unittest tests.test_tech_confirm_action_timeout_and_no_pinned_cards_red -v` 全绿。
- 回归：`tests.test_integration_params_tab_single_primary_and_auto_fill_red`、
  `tests.test_tech_params_autofill_and_soft_gates_red`、
  `tests.test_cost_review_single_primary_and_drop_run_step_red`、
  `tests.test_tech_chat_card_noise_and_quiet_board_failures_red`、
  `tests.test_tech_board_bridge_protocol_red` 全绿。
- 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 0 失败；
  `node --check` 覆盖这三个 js；`git diff --check` 通过。
- 浏览器：2.2 缺项时点「确认并进入下一页签」弹确认框，等多久都不再出现「超时未响应」，
  点「仍要继续」正常切到组装工艺；任何失败都不在会话底部留下卡片。
