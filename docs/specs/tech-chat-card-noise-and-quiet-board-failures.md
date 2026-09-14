# Spec：技术工艺会话卡片降噪 + 看板「预期内失败」不进会话

## 背景（用户实测）

技术工艺会话里出现两类无效内容：

1. 一片 ⚠ 提示（avatar `!`）：
   - `⚠ 看板已切换，命令已取消。`（`tech-board-bridge.js` 切看板时 `failPending('detached')`，
     父壳 `tech-workbench.js` 的 `runBoardAction` catch → `chatNotice` → 原样进会话，出现 3 次）
   - `⚠ 请填写提交意见。`（`requirement-confirm-page.js` 的 `cfAct` 门禁，看板自己已 toast）
   - `⚠ 看板未找到确认意见输入框或意见为空。`（同页 `applyConfirmationNote` 的
     `note-target-missing`，属于「当前视图没有这个输入框」，不是故障）
2. 一批只有标题 / 纯状态的看板卡片：`提交审核意见·已完成`、`✓ 通过确认·进行中`、
   `• 正在通过确认…`、`• 正在提交审核通过…`。它们由看板运行时对**每个**动作
   （含秒级同步动作）自动发的 `task-progress(phase:start)` + `task-completed` 产生，
   卡里没有任何真实执行明细。

用户要求：**报错卡不要，只有标题的卡不要，只保留有实际执行内容的卡。**

## 范围

- 只改「失败/进度的呈现」这一层：`tech-board-bridge.js`、`tech-board-runtime.js`、
  `tech-workbench.js`、`agent-chat.js`、`requirement-confirm-page.js`、`app.js`（2.1 批量工艺
  推荐的进度上报补 log 明细）。
- 不动：桥的信封 / 命名空间 / 版本 / `tech:command` 白名单 / `STATE_EVENTS`、看板动作注册与
  业务实现、后端路由与 Agent 工具、`.oc-task-card` 样式、结果入口常驻、九阶段流程。

## R1 会话任务卡只承载真实执行内容

- R1.1 `agent-chat.js renderTaskProgress()` 建卡判据改为「有真实执行明细」：
  - `log`（`progress_log` 明细行）非空 → 建卡 / 追加；
  - 已存在同 `taskId` 的卡 → 就地更新状态、追加后续明细；
  - `failed` 且带真实原因、且失败码不是预期内失败（见 R2）→ 建卡并显示原因；
  - 其余（只有 `label`、只有状态、只有一条通用 `progress` 文本、什么都没带）→ **直接 return，不建卡**。
- R1.2 `task-completed` 不再为「从来没有内容」的任务补一张空卡。
- R1.3 2.1「一键生成全部工艺推荐」是真实长任务但只发 `progress`：`allPartsProcessPublish()`
  同时上报逐件明细 `log`，保证它的卡继续存在（内容来自真实执行）。
- R1.4 解析 / 需求解析 / 2.2 / 2.3 的卡本来就带 `progress_log` 明细，行为不变。

## R2 预期内的看板失败不进会话

- R2.1 `tech-board-bridge.js` 定义预期内失败码集合 `QUIET_FAILURE_CODES`：
  - `detached`：看板切换导致的在途命令取消（用户主动切走）；
  - `note-target-missing`：带入意见时当前视图没有对应输入框 / 意见为空；
  - `missing-comment`、`no-selection`：必填意见未填 / 未选择，看板自己已 toast。
  桥在 `settle()` 与 `failPending()` 里给这些错误打 `quiet = true`，并暴露
  `TechBoardBridge.isQuietFailure(error)`。
- R2.2 `tech-workbench.js runBoardAction()`：quiet 失败不再 `chatNotice`、不再占标题行提示位
  （理由已由看板自己呈现）；非 quiet 失败照旧两处可见。
- R2.3 `agent-chat.js` 新增唯一出口 `boardFailureNotice(prefix, error)`：quiet 失败直接忽略，
  非 quiet 照旧输出真实原因；所有看板动作 / 导航 / 刷新失败改走它。
- R2.4 `tech-board-runtime.js` 在 `task-failed` 载荷里带上 `code`（只转发不判定），父壳据此
  跳过预期内失败的卡；非预期失败仍显示真实原因。

## R3 带入确认意见不再产生错误卡

- `requirement-confirm-page.js` 的 `applyConfirmationNote`：目标输入框不存在或意见为空时
  不再返回 `ok:false`（与 1.3 `applyReviewNote` 的写法一致），改为
  `{ ok: true, result: { applied: false, reason: 'target-missing' } }`。
- `#confirmationNote` 写入、`confirmRequirement` / `returnRequirementDraft` 与全部后端路由不变。

## 验收

- 红测：`tests/test_tech_chat_card_noise_and_quiet_board_failures_red.py`（实现前失败）。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败。
- 需要人工确认的边界：quiet 只覆盖上面 4 个码；真实失败（超时 / 未就绪 / 后端 5xx /
  `action-failed`）仍必须左右可见。
