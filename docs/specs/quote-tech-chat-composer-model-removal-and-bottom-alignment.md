# 报价 / 技术工艺会话输入区：移除框内模型按钮并底部对齐

## 1. 背景与问题

技术工艺统一工作台把“大语言模型”按钮放在输入框内部，占用了消息输入宽度；报价工作台没有在输入框内放置该按钮。技术工艺输入框下方还存在说明文字和额外底部间距，导致输入框本体比报价输入框更靠上，两个 Agent 的会话输入区不能在同一视觉基线上。

## 2. 目标

1. 技术工艺统一工作台的输入框只保留“上传附件、文本输入、发送”三类控件。
2. 移除输入框内的大语言模型按钮、模型名同步和按钮点击绑定，不以隐藏样式保留占位。
3. 模型配置能力继续复用全局设置：技术工艺左侧“设置”入口和右上角 `techModelInfo` 均保留；报价左侧设置入口和右上角 `modelInfo` 均保留。
4. 报价与技术工艺的 composer 都由会话列 flex 布局锚定底部；技术工艺统一工作台不再在输入框下方渲染说明文字，并使用与报价一致的 `10px 16px` 外边距，使输入框底边处于相同视觉高度。

## 3. 交互契约

- 技术工艺 `.oc-inputbox-single` 的直接可交互结构为：`#ocChatAttachBtn`、隐藏文件输入、`#ocInput`、`#ocSend`；其中不得出现 `#ocModelSelect`、`#ocModelSelectLabel` 或同义模型选择按钮。
- 删除按钮后，`#ocInput` 必须继续 `flex: 1`，不得用固定宽度补位；附件上传、Enter 发送、Shift+Enter 换行及发送按钮行为保持原样。
- `agent-chat.js` 不再查询、更新或绑定 `ocModelSelect` / `ocModelSelectLabel`。
- 不能用 `display:none`、`visibility:hidden`、零宽度或移到屏幕外等方式掩盖旧按钮。
- 模型设置弹窗、全局 API Key、模型读取与保存逻辑不得删除、复制或改为局部状态。

## 4. 布局契约

- 报价 `.chat-panel` 与技术工艺 `#techChatPane` 均保持纵向 flex、全高和 `overflow:hidden`；消息线程保持 `flex: 1`、`min-height: 0`，composer 保持 `flex-shrink: 0` / `flex: 0 0 auto`，确保其被锚定在会话列底部。
- 技术工艺统一工作台 `.oc-composer` 的 padding 为 `10px 16px`，与报价 `.chat-input-area` 一致；不得保留额外 bottom 偏移。
- `tech-workbench.html` 的统一 composer 内不再出现 `.oc-disc`，避免说明行将输入框本体向上推。通用 `.oc-disc` 样式可供其他独立页面继续使用。
  — 已更新（`tech-global-single-primary-by-state-and-nonblocking-notices.md`）：说明原文回归技术工艺，
  但以 composer 内绝对定位的不占布局高度提示呈现，因此「不得把输入框本体向上推」这条要求仍然成立，
  「composer 内不出现 `.oc-disc`」这条表述不再有效。
- 本次只对齐输入区底边，不要求报价和技术工艺 textarea 的行数、输入框高度或视觉造型完全相同。

## 5. 非目标与保护边界

- 不删除全局模型设置弹窗、设置入口、模型/API Key 后端接口和已有配置。
- 不修改消息、流式响应、历史会话、附件上传、Agent 工具、九阶段流程或右侧业务看板。
- 不修改报价输入框的业务按钮和快捷操作。
- 不改独立 2.1 / 2.2 页面使用的通用输入组件行为。

## 6. 验收

- `python3 -m unittest tests.test_quote_tech_chat_composer_alignment_red -v` 全部通过。
- `node --check tech_app/frontend/agent-chat.js` 与 `node --check tech_app/frontend/tech-workbench.js` 通过。
- 浏览器分别打开报价与技术工艺工作台：技术工艺输入框中无模型按钮，可输入区域变宽；两侧输入框底边与页面底部的距离一致；全局模型设置仍可打开、读取和保存同一份配置。
