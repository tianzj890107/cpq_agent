# 技术工艺 2.2「组装工艺」页：只留一颗主按钮，确认后进入下一步（2.3 成本测算）

状态：Spec + 红测（已实现）
红测：`tests/test_integration_process_tab_single_primary_and_next_step_red.py`

## 1. 问题（实测）

左侧操作栏由看板动作快照渲染（`tech-workbench.js` 的 `boardActionEntries()` 只跳过
`visible === false`），站在 2.2「组装工艺」页签（`aiTab === 'process'`）时
`assembly-integration.js` 的 registerActions 当前会渲染：

| 动作 | label | 现状 | 问题 |
| --- | --- | --- | --- |
| `generateIntegrationProcess` | 生成组装工艺 | `visible: aiTab === 'process'`，`role: 'aux'`（order 40） | 本页第一步却是次按钮 |
| `confirmIntegrationProcess` | 确认组装工艺 | `visible: aiTab === 'process'`（order 70） | 应与「进入下一步」合并，不该再单独占一颗 |
| `sendIntegrationToFinance` | 确认工艺并发送财务 | `visible: aiTab === 'process'`（order 20） | 保留：接收人必须人工选，是真正的人工交接 |
| `integrationStep` | 运行整合环节 | 已 `visible: false`（第 28 批） | — |
| `openIntegrationDrawings` | 整合图纸 | 已 `visible: false`（第 28 批） | — |
| `runIntegration` | 开始整合分析 | 第 30 批起只在「整合图纸」页可见 | —（本页已不出现，本批加守卫） |

后果：组装工艺页的主按钮不是本页第一步，而且「确认组装工艺」之后没有任何通往
下一步（2.3 成本测算）的入口 —— 用户确认完工艺只能自己去找顶部流程条切步骤。

## 2. 目标

1. **组装工艺页左侧只剩本步动作**：未生成时主按钮是「生成组装工艺」；
   生成后主按钮变成「确认并进入下一步」，「生成组装工艺」降为次按钮（可重新生成）。
2. **确认并进入下一步**：先走既有「确认组装工艺」（`POST /integration/process/confirm`），
   确认通过后切到下一步 2.3 成本测算；确认没过就返回真实原因、不切页。
3. **不代替人工交接**：「确认工艺并发送财务」继续保留在本页（它要人工选接收人），
   本批不把它并进「确认并进入下一步」，也不改它的闸门与弹窗。
4. **能力一个不少**：被隐藏的 `confirmIntegrationProcess` 继续注册、继续可执行
   （Agent 与内部链路照旧调用），后端接口、桥协议、右侧看板一律不动。

## 3. 契约 A：组装工艺页可见动作表（`assembly-integration.js`）

| 动作 | `getState().visible` | `role` | `order` |
| --- | --- | --- | --- |
| `generateIntegrationProcess`（生成组装工艺） | `aiTab === 'process'` | `aiHasProcess() ? 'aux' : 'primary'` | 40 |
| `confirmProcessAndNext`（确认并进入下一步，新增） | `aiTab === 'process' && aiHasProcess()` | `'primary'` | 45 |
| `sendIntegrationToFinance`（确认工艺并发送财务） | `aiTab === 'process'`（不变） | `analyzed ? 'primary' : 'aux'` | 20 |
| `confirmIntegrationProcess`（确认组装工艺） | `false` | `'aux'` | 70 |
| `integrationStep` / `openIntegrationDrawings` | `false`（第 28 批） | `'aux'` | 120 / 130 |
| `runIntegration`（开始整合分析） | `aiTab === 'drawings'`（第 30 批，本页不得出现） | — | 10 |

- 复用第 30 批已加的共享判定 `aiHasProcess()`（`assembly-integration.js` 顶部），
  不再另写一份。
- 「只保留一个主按钮」由 role 保证：组装工艺页任何时刻只有 `generateIntegrationProcess`
  （未生成工艺）或 `confirmProcessAndNext`（已生成工艺）中的一个 `role === 'primary'`。
- 既有契约要求 `aiTab === 'process'` 后 200 字符内出现 `visible`，因此可见性继续写成
  `const show = aiTab === 'process'; return { visible: show, ... }` 的形状。

## 4. 契约 B：确认并进入下一步

新增 `aiConfirmProcessAndNext()`（放在 2.2 既有函数旁边，只复用既有实现）：

1. `aiBusy` 时返回 `{ ok: false, error: { code: 'busy', message: '已有任务在执行，请稍候。' } }`；
   `!aiHasProcess()` 时返回 `{ code: 'no-process', message: '请先生成组装工艺。' }`。
2. `await aiConfirmStep('process')` —— 既有 `POST /integration/process/confirm`。
3. `aiData.status.process_confirmed !== true` 时不往下走，返回
   `{ code: 'confirm-failed', message: '组装工艺确认没有通过，请查看右侧看板提示。' }`。
4. 调用新增的 `techGoNextStage('cost')` 切到下一步 2.3 成本测算。
5. 用 `aiSay()` 说明：已确认并进入 2.3；如果还没把任务交给财务，先在左侧点
   「确认工艺并发送财务」（要人工选接收人）。
6. 返回 `{ ok: true }`；动作条目 `deferred: false`，由 `runEntry` 按既有语义发布完成事件。

切换步骤复用既有嵌入通道，不新增第二套导航：

```js
function techGoNextStage(stage) {
  if (!stage) return false;
  try {
    if (window.TechEmbed && typeof window.TechEmbed.requestNavigate === 'function') {
      window.TechEmbed.requestNavigate(stage, aiPid);
      return true;
    }
  } catch (error) { /* 独立打开时没有嵌入壳，保持页内不动 */ }
  return false;
}
```

动作条目：

```js
confirmProcessAndNext: {
  label: '确认并进入下一步',
  order: 45,
  run: () => aiConfirmProcessAndNext(),
  getState: () => ({ visible: aiTab === 'process' && aiHasProcess(),
                     enabled: true, busy: aiBusy, role: 'primary', order: 45 }),
},
```

（`role: 'primary'` 只在 `getState()` 里声明一次，保持「2.2 只允许一个静态 primary」的既有契约。）

## 5. 契约 C：能力不缩水

- `confirmIntegrationProcess` 仍注册、`run()` 仍是 `aiConfirmStep('process')`，只是 `visible: false`；
- `generateIntegrationProcess` 的 `run()` 仍是 `aiGenerate('process')`，`deferred: true` 不变；
- `sendIntegrationToFinance` 的闸门（`aiFinanceBlocker()`）与「发送至财务」弹窗、`aiRunOp('send-to-finance')`
  一字不改；
- `integrationStep` / `openIntegrationDrawings` 继续 `visible: false`（Agent 工具链
  `RequestIntegrationStep` / `UploadIntegrationDrawing` 照旧经 `execute-action` 执行）；
- `registerViews` 的 `drawings` / `params` / `process`、`tech-workbench.js` 的
  `CHILD_TAB_PROXY.process.tabs`、父壳 `boardActionEntries()` / `primaryActionName()` 一律不改；
- 右侧看板按钮在嵌入态仍由 `.tech-embed` 规则隐藏（`tech-embed.js:166-167`），本批不动。

## 6. 边界（本批禁止改动）

- 不改后端路由、service 与 `oc_agent.py` 工具映射；不新增任何接口；
- 不改 `tech-board-runtime.js` / `tech-board-bridge.js` / `cpq:tech-board` 信封与事件白名单；
- 不改 `aiConfirmStep` / `aiGenerate` / `aiRunOp` / `aiOpenFinanceDialog` 的既有语义与文案；
- 不删任何动作注册与实现；不改 1.1 / 2.1 / 2.3 / 3.x 的任何动作可见性；
- 不把「发送至财务」并进本动作（交接必须由人完成）。

## 7. 验收

1. 站在「组装工艺」页：左侧主按钮是「生成组装工艺」；生成后主按钮变成「确认并进入下一步」，
   「生成组装工艺」作为次按钮保留；不再出现「确认组装工艺」「运行整合环节」「整合图纸」「开始整合分析」。
2. 点「确认并进入下一步」：确认通过 → 右侧切到 2.3 成本测算；确认失败 → 会话与看板显示真实原因且不切页。
3. 「确认工艺并发送财务」仍在本页可用，闸门与弹窗行为不变。
4. 全量回归不得新增失败点。

测试命令：

```
python3 -m unittest tests.test_integration_process_tab_single_primary_and_next_step_red -v
python3 -m unittest tests.test_integration_params_tab_single_primary_and_auto_fill_red tests.test_integration_left_toolbar_drop_agent_only_and_duplicate_entries_red tests.test_tech_board_actions_into_left_toolbar_red
node --check tech_app/frontend/assembly-integration.js
git diff --check
```
