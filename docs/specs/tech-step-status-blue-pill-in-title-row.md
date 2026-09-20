# 技术工艺：标题行步骤状态恢复蓝底蓝字胶囊，红色只留给失败提示

状态：TDD Red，等待 DeepSeek 实现。

## 1. 问题

统一工作台标题行的提示位 `#techContextNotice` 在接收 `board-status` 时，把步骤状态原文
（如 `已打开项目 fcac7095bded（补充说明✓，佐证文件 0 个）`、`就绪`、`本步已确认`）
渲染成了**红字**，与改造前那行蓝色胶囊完全不是一回事。

根因（已核对）：

- `tech-workbench.css:531-539` 的 `.tech-context-notice` 只有 `color: var(--color-red, #d92d20)`，
  没有背景与圆角 —— 这条规则当初只服务「看板未就绪 / 失败提示」。
- `tech-workbench.js:1212-1215` 之后又把步骤状态接进同一个节点，且
  `setBoardNotice(message)`（`:289-294`）**忽略** `payload.level`，于是 info 级状态也吃到了红字。
- 对照：阶段页自己那行状态是 `<span id="status" class="status-badge">`，样式在
  `workbench.css:17`：`background: var(--color-primary-light); color: var(--color-primary); border-radius: var(--radius-full)`，
  即用户说的「蓝色背景气泡蓝字」。独立打开阶段页时它是正常的，问题只出在父壳提示位。

## 2. 目标

1. 步骤状态（`level: 'info'`，含 `board-status` 的全部正常文案）在父壳标题行恢复为
   蓝底蓝字胶囊，与页内 `.status-badge` 同一视觉。
2. 只有真正的失败 / 未就绪提示（`level: 'error'`）才是红色；红色不再出现在基础态。
3. 文案一个字不改：父壳只搬运 `payload.text`，不拼前缀、不截断、不改写。
4. 切步清空不再把上一步的状态或颜色带到下一步。

## 3. 契约 A：提示位基础态 = 蓝底蓝字胶囊（`tech-workbench.css`）

```css
.tech-context-notice {
  flex: 0 1 auto;              /* 胶囊贴文字宽度，不再拉满整条标题行 */
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 2px 10px;
  border-radius: 999px;
  /* 父壳页面只声明了 --color-primary，没有 --color-primary-light，必须带 hex 兜底 */
  background: var(--color-primary-light, #E6F0FD);
  color: var(--color-primary, #0060E6);
  font-size: 12px;
  font-weight: 500;
}
.tech-context-notice[hidden] { display: none; }
```

- 基础规则里**不得**再出现 `var(--color-red…)`、`#d92d20`、`#b91c1c`。
- 保留 `overflow: hidden` + `text-overflow: ellipsis`：长文案仍然省略号收尾。

## 4. 契约 B：失败提示单独一个修饰类（`tech-workbench.css`）

```css
.tech-context-notice.is-error {
  background: var(--color-red-light, #fee2e2);
  color: var(--color-red, #d92d20);
}
```

- 未就绪 / 失败提示（桥的 `error`、`task-failed`、`board-status` 里 `level === 'error'`）
  才加 `.is-error`；提示清除后必须移除该类，回落到蓝色胶囊。

## 5. 契约 C：父壳按 level 渲染（`tech-workbench.js`）

- `state` 增加提示位等级字段（`boardStatusLevel` / 提示位自身的 level），初值 `'info'`。
- `board-status` 分支：

```js
state.boardStatus = String((event && event.payload && event.payload.text) || '');
state.boardStatusLevel = (event && event.payload && event.payload.level) === 'error' ? 'error' : 'info';
setBoardNotice(state.boardNotice, state.boardNoticeLevel);
```

- 桥的 `error` / `task-failed` 分支：`setBoardNotice(message, 'error')`。
- `setBoardNotice(message, level)`：

```js
notice.classList.toggle('is-error', (level || 'info') === 'error');
```

  文案仍是 `state.boardNotice || state.boardStatus`（失败提示优先，清除后回落到步骤状态），
  但颜色跟随**当前真正显示的那条**的 level。
- `applyStage`（`:509-510`）切步时必须同时清空 `state.boardStatus` 与它的 level，
  不允许把 2.2 的「就绪」或 `is-error` 带到 2.3。

## 6. 非目标与保护边界

- 不改 `board-status` 事件名、`payload` 形状（仍是 `{text, level}`）与 `level` 白名单；
  不改 `TechBoardRuntime.publishStatus(text, level)` 的语义与调用点。
- 不改九阶段 stage id、页面映射、`#techContextTitle`、`#techSubstepsBar` 与子页签逻辑。
- 不改阶段页自己的 `.status-badge` / `#status` / `.ai-status` 与 `tech-embed.js` 的
  `.tech-embed` 隐藏规则；独立打开阶段页时页内蓝底状态行照旧。
- 父壳不得生成或改写业务文案（`tech-workbench.js` 内不得出现 `已打开项目` 这类文案常量）。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 7. 验收

- `python3 -m unittest tests.test_tech_step_status_blue_pill_red -v` 全绿。
- 回归：`tests.test_tech_step_status_in_context_row_red`、
  `tests.test_tech_board_bridge_protocol_red`、`tests.test_tech_board_long_task_ack_red`。
- `node --check` 覆盖 `tech-workbench.js`、`tech-board-bridge.js`、`tech-board-runtime.js`。
- 浏览器：统一工作台每一步标题行显示蓝底蓝字胶囊状态（含
  `已打开项目 …（补充说明✓，佐证文件 N 个）`）；触发一次看板失败时该位置变红，
  错误清除后回到蓝色；独立打开 2.1/2.2/2.3 时页内状态行仍为蓝底蓝字。

## 8. 对应测试

`tests/test_tech_step_status_blue_pill_red.py`
