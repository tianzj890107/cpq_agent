# 技术工艺左侧会话：删掉常驻的「技术工艺评估助手」开场气泡

状态：TDD Red，等待实现。

## 1. 问题（实测）

`tech_app/frontend/tech-workbench.html:71-77` 在唯一会话宿主 `#techChatPane` 的
`#ocTinner` 顶部静态写了一张卡：

```html
<div class="oc-amsg">
  <div class="oc-abody">
    <div class="oc-intent-card">
      <h4>技术工艺评估助手</h4>
      <div class="oc-intent-meta">左侧会话在整个评估流程中持续存在：可以描述零件与工艺要求、追问图纸解析结果、发起解析，或直接说「开始解析」。</div>
    </div>
  </div>
</div>
```

它不是一条消息，也没有任何 JS 在用户发言后移除它：

- `agent-chat.js` 只在**第一条消息**时删 `#ocEmpty`（`clearEmpty()` → `$("ocEmpty")?.remove()`），
  从不处理这张卡；
- 唯一会删它的路径是「新对话」的 `resetTaskFlow()`
  （`tinner.querySelectorAll(".oc-amsg, .oc-ubub").forEach(node => node.remove())`）。

后果：整个评估流程里，这张「技术工艺评估助手」气泡**永久钉在最新消息上方**，和栏头
「技术工艺智能体 + AI 徽标」重复，也把真正的对话起点挤到下面。左侧栏本来就有
`#ocEmpty`（「工艺评估助手」+「它能读取本项目图纸与技术资料、查询解析结果，并按你的
要求发起解析。切换右侧步骤不会清空这里的会话。」）承担引导，气泡是多余的第二份说明。

## 2. 目标

1. **删掉这张常驻气泡**：`#ocTinner` 里不再有静态 `.oc-amsg` / `.oc-intent-card`。
2. **引导能力不缩水**：`#ocEmpty` 保留并在首条消息后照旧消失；结果入口
   (`#ocResultActions`) 与任务进度宿主 (`#ocTaskProgressHost`) 仍是 `#ocTinner` 的子节点。
3. **不许换一个替代物补位**：不新增第二个引导卡、占位气泡或父壳注入节点；
   开场内容仍只由真实回复（`addAssistant()`）产生。
4. **只动父壳这一处**：`index.html` 的 2.1 设计意图卡（`#intent` / `#btnParse`）、
   `assembly-integration.html` / `cost-review.html` 的说明卡、`agent-chat.css` 的
   `.oc-intent-card` / `.oc-intent-meta` 规则一律保留；JS 逻辑、后端、桥协议不动。

## 3. 契约 A：气泡删除

`tech_app/frontend/tech-workbench.html`：

- `#ocTinner` 与 `#ocEmpty` 之间不再出现 `oc-amsg`；
- 整份父壳 HTML 不再出现 `oc-intent-card` / `oc-intent-meta`；
- 文案「技术工艺评估助手」与「左侧会话在整个评估流程中持续存在」在
  `tech_app/frontend/` 下的任何 `.html` / `.js` / `.css` 里都不再出现。

删除的是整块 `oc-amsg` 包裹（它只有这一张卡，没有头像、没有标题行）。

## 4. 契约 B：不许用替代物补位

- `#ocEmpty` 仍在 `#ocTinner` 内，仍保留 `id="ocEmpty"`、`✦` 图标、「工艺评估助手」标题与
  两行能力说明（含「切换右侧步骤不会清空这里的会话」），**不得**改成常驻元素；
- `#ocResultActions`（零件清单 / 待澄清问题 / 解析报告三颗入口）与 `#ocTaskProgressHost`
  仍是 `#ocTinner` 的子节点，`#ocResultActions` 默认 `hidden` 不变；
- `tech-workbench.js` 不得往会话栏写节点（不允许出现 `ocTinner` / `ocThread` /
  `oc-intent-card` 的创建或插入）；
- `agent-chat.js` 不得新增静态开场卡（不允许出现 `oc-intent-card` 或该段文案），
  头部身份行 `技术工艺智能体` + `.tech-ai-badge` 保留。

## 5. 契约 C：其它页面的同类卡必须保留

- `index.html`：`oc-intent-card`、`id="intent"`、`id="btnParse"` 全在（2.1 的功能卡，
  `app.js` 依赖这两个 id）；
- `assembly-integration.html`、`cost-review.html`：各自的 `oc-intent-card` 保留；
- `agent-chat.css`：`.oc-intent-card {` 与 `.oc-intent-meta {` 规则保留（上面的页面仍在用）。

## 6. 契约 D：行为不变

- `clearEmpty()` 仍是 `$("ocEmpty")?.remove()`，`addUser()` / `addAssistant()` 仍调用它；
- `resetTaskFlow()` 仍按节点清理会话
  （`tinner.querySelectorAll(".oc-amsg, .oc-ubub")`），不得改成 `replaceChildren()` 或
  连带清掉 `#ocResultActions`；
- 桥事件白名单（`READY / ACTION_STATE / TASK_PROGRESS / TASK_COMPLETED / TASK_FAILED /
  SELECTION_CHANGED / BOARD_STATUS`）与后端一律不动；
- 输入区（`ocChatAttachBtn` / `ocChatFileInput` / `ocInput` / `ocSend`）、操作栏
  （`techChatActions` / `ocFilesAction` / `techChatPrimary`）与滚动宿主
  （`oc-thread` / `oc-tinner`）结构不变。

## 7. 边界（本批禁止改动）

- 只允许改 `tech_app/frontend/tech-workbench.html`；唯一可选的附带改动是
  `agent-chat.js` 里那条已过期的注释（「设计意图卡是页面结构的一部分，必须保留」），
  且只能是注释文字，不得改任何行为；
- 不改 `agent-chat.css` / `tech-workbench.css` / `tech-embed.js` / `tech-board-*`；
- 不改 `index.html` / `assembly-integration.html` / `cost-review.html`；
- 不改后端路由与 service，不新增接口；
- 不删 `#ocEmpty`，不把引导文案搬进 JS 动态渲染。

## 8. 验收

1. 打开统一工作台：左侧会话栏顶部不再有「技术工艺评估助手」气泡；
   未发言时只见 `#ocEmpty` 的「工艺评估助手」引导，发言后它照旧消失。
2. 结果入口三颗按钮与任务进度卡仍出现在会话里，行为不变。
3. 2.1 独立页与嵌入页的设计意图卡、2.2 / 2.3 的说明卡显示正常。
4. 全量回归不得新增失败点。

测试命令：

```
python3 -m unittest tests.test_tech_chat_drop_static_intro_bubble_red -v
python3 -m unittest tests.test_tech_left_chat_controls_restore_red tests.test_tech_full_width_board_and_single_agent_pane_red tests.test_tech_chat_composer_compact_autogrow_red
git diff --check
```
