# 技术工艺 2.2「参数推荐」页：只留一颗主按钮，生成即补全必填，确认后进入下一步

状态：TDD Red，等待实现。

> 已更新（`tech-global-single-primary-by-state-and-nonblocking-notices.md`）：参数页收口动作
> 的文案由「确认并进入下一步」改为「确认并进入下一页签」（组装工艺页仍叫「确认并进入下一步」），
> 动作名、role 判定、order 与实现不变；下表及描述中出现的旧文案按此条理解。

## 1. 问题（实测）

统一工作台左侧操作栏由看板动作快照渲染（`tech-workbench.js` 的 `boardActionEntries()` 只跳过
`visible === false` 的条目）。当前站在 2.2「参数推荐」页签（`aiTab === 'params'`）时，
`assembly-integration.js` 的 `registerActions` 会把下面这些都渲染成左侧按钮：

| 动作 | label | 现状 | 问题 |
| --- | --- | --- | --- |
| `runIntegration` | 开始整合分析 | `visible: true`（order 10） | 参数页不需要「整条链路」入口，本页主按钮应由生成 / 确认承担 |
| `sendIntegrationToFinance` | 确认工艺并发送财务 | `visible: true`（order 20），analysis 完成后抢走主按钮 | 它的闸门是 `aiFinanceBlocker()`（要求工艺已生成并确认），在参数页必然是死按钮，还把「生成参数推荐」挤出主位 |
| `generateIntegrationParams` | 生成参数推荐 | `visible: aiTab === 'params'`，`role: 'aux'`（order 30） | 参数页真正的起点却是次按钮 |
| `saveIntegrationParams` | 保存参数 | `visible: aiTab === 'params'`（order 50） | 参数表一直可编辑，保存动作不该占一颗左栏按钮 |
| `confirmIntegrationParams` | 确认参数推荐 | `visible: aiTab === 'params'`（order 60） | 与下一步合并 |
| `autofillIntegrationParams` | 智能补全 | `visible: aiTab === 'params'`（order 80） | 应由生成流程自动做掉 |
| `saveIntegrationParamsFinal` | 保存补填 | `visible: aiTab === 'params'`（order 90） | 同上 |
| `confirmIntegrationParamsFinal` | 确认参数已齐 | `visible: aiTab === 'params'`（order 100） | 同上 |
| `integrationStep` | 运行整合环节 | `visible: false`（上一批已隐藏） | — |
| `openIntegrationDrawings` | 整合图纸 | `visible: false`（上一批已隐藏） | — |

后果：参数页左侧堆了 8 颗按钮，主按钮还不是本页的第一步；而「报价必填」的补齐要用户自己
点三次（智能补全 → 保存补填 → 确认参数已齐，`aiParamsAutofill` / `aiParamsFinalize`），
任何一步没点，`aiFinanceBlocker()` 就会在发送财务时把任务挡住（
`assembly-integration.js:846-856`）。

## 2. 目标

1. **参数推荐页左侧只剩两颗按钮**：未生成时是「生成参数推荐」（主按钮，蓝色实心）；
   生成后主按钮变成「确认并进入下一步」，「生成参数推荐」降为次按钮（可重新生成）。
2. **生成即补全必填**：点「生成参数推荐」一次，把既有三步串起来自动跑完 ——
   生成整机参数 → 有报价必填缺口就自动智能补全 → 把补上的建议值落库保存。
   用户不再需要点「智能补全 / 保存补填 / 确认参数已齐」。
3. **确认并进入下一步**：先把参数表按报价必填校验并最终确认（缺项时返回真实原因、不往下走），
   再确认本环节，然后切到下一步「组装工艺」。用户只点一次。
4. **能力一个不少**：上表全部动作继续注册、继续可执行（Agent 与内部链路照旧调用），
   只是不再出现在用户的操作栏里；后端接口与 service 一律不动。

## 3. 契约 A：2.2 左侧可见动作表（`assembly-integration.js`）

每个动作的 `getState()` 决定可见性；`run()` 实现一律复用既有函数，不新增第二套。

| 动作 | `getState().visible` | `role` | `order` |
| --- | --- | --- | --- |
| `runIntegration`（开始整合分析） | `aiTab === 'drawings'` | `analyzed ? 'aux' : 'primary'` | 10 |
| `sendIntegrationToFinance`（确认工艺并发送财务） | `aiTab === 'process'` | `analyzed ? 'primary' : 'aux'` | 20 |
| `generateIntegrationParams`（生成参数推荐） | `aiTab === 'params'` | `aiHasParams() ? 'aux' : 'primary'` | 30 |
| `confirmParamsAndNext`（确认并进入下一步，新增） | `aiTab === 'params' && aiHasParams()` | `'primary'` | 35 |
| `generateIntegrationProcess`（生成组装工艺） | `aiTab === 'process'` | `'primary'` | 40 |
| `confirmIntegrationProcess`（确认组装工艺） | `aiTab === 'process'` | `'aux'` | 70 |
| `saveIntegrationParams`（保存参数） | `false` | `'aux'` | 50 |
| `confirmIntegrationParams`（确认参数推荐） | `false` | `'aux'` | 60 |
| `autofillIntegrationParams`（智能补全） | `false` | `'aux'` | 80 |
| `saveIntegrationParamsFinal`（保存补填） | `false` | `'aux'` | 90 |
| `confirmIntegrationParamsFinal`（确认参数已齐） | `false` | `'aux'` | 100 |
| `integrationStep` / `openIntegrationDrawings` | `false`（上一批已完成） | `'aux'` | 120 / 130 |
| `refreshIntegration` | `false` | `'aux'` | 110 |

- 新增两个语义化判定，供 `getState()` 使用，避免各处各写一份：
  `const aiHasParams = () => Boolean(aiData?.status?.has_params);`
  `const aiHasProcess = () => Boolean(aiData?.status?.has_process);`
- 「只保留一个主按钮」由 role 保证：参数页任何时刻只有 `generateIntegrationParams`（未生成）
  或 `confirmParamsAndNext`（已生成）中的一个 `role === 'primary'`；父壳 `primaryActionName()`
  取可见条目里第一个 primary，其余按 order 描边渲染。
- 既有 `tech-board-actions-into-left-toolbar` 契约要求「`aiTab === 'params'` 后 200 字符内出现
  `visible`」与「`aiTab === 'process'` 同理」，因此这两个分支继续写成
  `const show = aiTab === 'params'; return { visible: show, ... }` 的形状。

## 4. 契约 B：生成参数推荐 = 既有三步链路

新增 `aiGenerateParamsFully()`（文件内既有函数旁边，复用既有实现）：

1. `await aiGenerate('params')` —— 既有 `POST /integration/params` + 既有任务轮询；
   失败（`ok === false`）直接返回该失败，不继续往下跑。
2. 生成成功但没有 `has_params` → 返回结构化失败 `{ code: 'no-params', message: '模型没有产出整机参数…' }`。
3. `aiRequiredGaps().required_missing > 0` 时 `await aiParamsAutofill()` —— 既有
   `POST /integration/params/autofill`，只给缺口出建议值并回填到参数表。
4. 自动补全真的填进去了（`applied > 0`）时 `await aiParamsFinalize(false)` —— 既有
   `POST /integration/params/finalize`（`confirm: false`），把补上的值落库。
5. 结尾用 `aiSay()` 汇报「已填 X/Y 项、还缺哪几项」；缺口为 0 时提示可以点「确认并进入下一步」。

- `aiParamsAutofill()` 需要把它已经算出来的结果返回出去：成功路径 `return { applied: applied, unresolved: unresolved };`
  （其余行为、文案、接口一字不改），否则调用方无法判断「补全是否真的填了值」。
- 动作条目保持 `deferred: true` 与 `run: () => { aiGenerateParamsFully(); return { ok: true }; }`
  的启动即回执形状；进度与失败仍由链路内部既有的 `aiPublishTask` 上报。

## 5. 契约 C：确认并进入下一步（新增动作 + 既有两步）

新增 `aiConfirmParamsAndNext()`：

1. `aiBusy` 时返回 `{ code: 'busy' }`；`!aiHasParams()` 时返回 `{ code: 'no-params', message: '请先生成参数推荐。' }`。
2. `await aiParamsFinalize(true)` —— 既有 `POST /integration/params/finalize`（`confirm: true`）：
   后端按报价必填校验，缺项返回 400 真实原因（`main.py` 的 `finalize_integration_params`）。
3. `aiData.status.params_final !== true` 时不继续，返回
   `{ code: 'required-missing', message: '还有 N 项报价必填参数没有值：…。请在表里补填后再确认。' }`。
4. `await aiConfirmStep('params')` —— 既有 `POST /integration/params/confirm`。
5. `aiData.status.params_confirmed !== true` 时返回 `{ code: 'confirm-failed', … }`。
6. `aiSetTab('process')` 切到下一步「组装工艺」，并用 `aiSay()` 说明下一步动作。
7. 返回 `{ ok: true }`；动作条目 `deferred: false`，由 `runEntry` 按既有语义发布完成事件。

动作条目：

```js
confirmParamsAndNext: {
  label: '确认并进入下一步',
  role: 'primary',
  order: 35,
  run: () => aiConfirmParamsAndNext(),
  getState: () => ({ visible: aiTab === 'params' && aiHasParams(),
                     enabled: true, busy: aiBusy, role: 'primary', order: 35 }),
},
```

## 6. 契约 D：Agent 能力与其它页签不受影响

- `integrationStep` / `openIntegrationDrawings` 继续 `visible: false`（Agent 工具
  `RequestIntegrationStep` / `UploadIntegrationDrawing` 照旧经 `execute-action` 执行）；
- 所有被隐藏的参数动作仍注册、`run()` 仍调用既有实现（`aiSaveEdits('params')` /
  `aiConfirmStep('params')` / `aiParamsAutofill()` / `aiParamsFinalize(false)` / `aiParamsFinalize(true)`）；
- `registerViews` 的 `drawings` / `params` / `process` 三个内部视图、
  `tech-workbench.js` 的 `CHILD_TAB_PROXY.process.tabs`、父壳 `boardActionEntries()` 的
  `entry.visible === false` 过滤与 `primaryActionName()` 规则一律不改；
- 右侧看板按钮在嵌入态仍由 `.tech-embed` 规则隐藏（`tech-embed.js:166-167`），本批不动。

## 7. 边界（本批禁止改动）

- 不改后端路由、service 与 `oc_agent.py` 工具映射；不新增任何接口；
- 不改 `tech-board-runtime.js` / `tech-board-bridge.js` / `cpq:tech-board` 信封与事件白名单；
- 不改 `aiConfirmStep` / `aiParamsFinalize` / `aiPost` 的既有语义与文案措辞；
- 不删任何动作注册与实现，不删右侧看板页签与表格；
- 不改 1.1 / 2.1 / 2.3 / 3.x 的任何动作可见性。

## 8. 验收

1. 站在「参数推荐」页：左侧只有一颗主按钮 —— 未生成时「生成参数推荐」，生成后「确认并进入下一步」
   （「生成参数推荐」作为次按钮保留）；左侧不再出现开始整合分析 / 保存参数 / 确认参数推荐 /
   智能补全 / 保存补填 / 确认参数已齐 / 运行整合环节 / 整合图纸。
2. 点「生成参数推荐」后，缺口由平台自动补全并落库，右侧参数表的报价必填项填满；
   真的推不出来的项仍如实列出来。
3. 点「确认并进入下一步」：必填齐 → 参数已最终确认 + 本环节已确认 → 看板切到「组装工艺」；
   必填不齐 → 会话与看板显示真实原因且不切页。
4. 「整合图纸」页只有「开始整合分析」，「组装工艺」页只有生成 / 确认 / 发送财务。
5. 全量回归不得新增失败点。

测试命令：

```
python3 -m unittest tests.test_integration_params_tab_single_primary_and_auto_fill_red -v
python3 -m unittest tests.test_integration_left_toolbar_drop_agent_only_and_duplicate_entries_red tests.test_tech_board_actions_into_left_toolbar_red tests.test_tech_board_action_registry_red tests.test_tech_board_deferred_actions_red
node --check tech_app/frontend/assembly-integration.js
git diff --check
```
