# 技术工艺 0–4 修订 Spec：看板→父壳状态通道修复与 result-summary 生产者

本 Spec 是 `tech-agent-recovery-00-03-foundation.md` 第 2 步与
`tech-agent-recovery-04-left-chat-controls.md` 的修订补充。0–3 的协议契约不变，
只是当前实现存在一处把整条上行通道打通的缺陷，导致第 4 步“恢复了控件但拿不到数据”。

## 1. 已确认缺陷（实测）

`tech-board-runtime.js` 的 `emit(type, name, payload)` 把**事件名**写进了信封的
`type` 字段：

```
emit('ready','ready',...)            -> { type: 'ready',        name: 'ready' }
emit('action-state','parseDrawing')  -> { type: 'action-state', name: 'parseDrawing' }
emit('task-progress','parseDrawing') -> { type: 'task-progress',name: 'parseDrawing' }
```

而 `tech-board-bridge.js` 只接受 `type === 'state'`：

```js
if (type === 'result') { settle(data); return; }
if (type !== 'state') return;          // ← 其余全部丢弃
```

因此 `ready / action-state / task-progress / task-completed / task-failed /
selection-changed` 这些**看板 → 父壳**的状态推送在实际浏览器里全部被父壳丢弃。
`tech-workbench.js` 的 `bridge.subscribe(...)`、`agent-chat.js` 的 `bindBoardBridge()`
以及 `app.js` 里的 `updateActionState('parseDrawing', { busy })` 都不会真正生效。

下行方向（父壳 → 看板 `command` / 看板 → 父壳 `result`）本身正确：错误 requestId、
外来 origin、外来 source、错 projectId/stage/namespace/version 都会被拒绝。

第二个缺陷：全仓库没有任何页面发布 `result-summary`（只有 `agent-chat.js` 在消费），
所以第 4 步左侧的结果计数与可用态在真实链路里永远是初值。

第三个小缺陷：`tech-workbench.js` 的旧通道 `cpq:tech-workbench:navigate` 只校验
`event.origin` 与 stage 白名单，没有校验 `event.source`。

## 2. 修复后的信封契约

- 看板发出的每一条状态推送都必须是 `type: 'state'`；
- 事件名放在 `name`，且必须属于白名单：
  `ready`、`action-state`、`task-progress`、`task-completed`、`task-failed`、
  `selection-changed`、`result-summary`；
- 事件主体信息不得丢弃：动作名、视图名、`register-actions` / `register-views` /
  `context` / `view`、以及 `payload.action` / `payload.message` 都要保留（例如放进
  `payload.subject`），父壳已有的订阅与 `snapshot()` 行为不能因此改变；
- 命令（`type: 'command'`）与回复（`type: 'result'`）信封保持不变；
- 仍然只用 `location.origin` 作为 targetOrigin，禁止 `"*"`；
- 不在父壳加“兼容旧信封”的兜底分支：修的是发送方，不是接收方。

## 3. result-summary 生产者契约

2.1 看板（右侧 iframe，`index.html` + `app.js`）在业务数据变化后主动发布：

```js
TechBoardRuntime.publish('result-summary', 'result-summary', {
  summary: { stage: 'drawing', parsed: <bool>, results: {
    parts:     { count: <n>, available: <bool> },
    questions: { count: <n>, available: <bool> },
    report:    { count: <n>, available: <bool> },
    files:     { count: <n>, available: <bool> },
  } }
});
```

- 数据必须复用看板已有的 `currentIR`（`parts`、`open_questions`）、既有报告入口与既有
  `/api/projects/{id}/files` 清单，不得新建第二套状态或另起一个拉取层；
- 触发时机至少覆盖：`renderIR` 之后、解析完成之后、项目切换之后；
- 父壳 `agent-chat.js` 已按 `results.parts|questions|report|files` 的 `available`/`count`
  渲染，不做二次改造。

## 4. 旧导航通道

`tech-workbench.js` 处理 `cpq:tech-workbench:navigate` 时必须同时校验
`event.source === iframe.contentWindow`（只认当前嵌入的看板 iframe），在校验 origin
与 stage 白名单之前或之后都可，但不得缺失。

## 5. 验收标准

1. 直连宿主（真实 `tech-board-runtime.js` + `tech-board-bridge.js`）中，`attach` 后父壳
   能收到 `ready`，`updateActionState` 后能收到 `action-state` 且 `snapshot().actions`
   的 `busy` 随之为真；
2. `executeAction('parseDrawing')` 后父壳依次收到 `task-progress` 与 `task-completed`，
   且返回值仍是 `{ ok: true }`；
3. 看板所有状态推送的 `type === 'state'` 且 `name` 在白名单内；
4. `app.js` 发布 `result-summary`，四个分组 `parts/questions/report/files` 均含
   `count` 与 `available`；
5. 左侧结果按钮与计数在看板发布后按 `available` 显示、按 `count` 更新；
6. 旧导航通道校验 `event.source`；
7. 不新增、不修改任何后端路由与业务算法，不新增父层业务 Drawer/Modal。

## 6. 对应测试

- `tests/test_tech_board_state_envelope_dynamic.py`（本次新增，直连宿主 + 静态契约）
- `tests/test_tech_left_chat_controls_restore_red.py`（第 4 步，本轮补强“生产者存在”）
- `tests/test_tech_backend_capability_preservation_red.py`（0–1）
- `tests/test_tech_board_bridge_protocol_red.py`（2）
- `tests/test_tech_board_action_registry_red.py`（3）
