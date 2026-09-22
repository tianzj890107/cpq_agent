# 报价 / 技术工艺输入区统一为技术工艺单行样式 + 恢复「会话绑定」说明行

状态：Spec + 红测（已实现）
红测：`tests/test_quote_tech_unified_composer_and_caption_red.py`

## 1. 背景

技术工艺统一工作台（`tech_app/frontend/tech-workbench.html` + `agent-chat.css`）的会话输入区
是「单行圆角输入框 + 左侧圆形 ＋（上传附件）+ 右侧圆形主色发送」，报价工作台
（`确认需求解析结果.html`）的输入区仍是旧的多行样式（38×38 方形回形针按钮、2 行 textarea、
38×38 方形发送），两者造型完全不一致。

技术工艺的 ＋ 按钮右上角还挂着一个小回形针角标（`.oc-add-mark`），语义与 ＋ 本身重复。

另外，技术工艺输入框下方原有的说明行
`会话绑定当前项目；右侧工作台只承载业务步骤，不重复会话。`（`.oc-disc`）在当前版本被删除，
输入区底部因此空出一块、两个工作台的输入区不在同一视觉基线上。

## 2. 目标

1. 报价工作台的输入行改成与技术工艺一致的单行圆角输入框：左侧圆形 ＋、中间单行 textarea、
   右侧圆形主色发送。
2. 技术工艺 ＋ 按钮去掉右上角多余的回形针角标，并删除对应死 CSS 与过时注释。
3. 恢复输入框下方的「会话绑定」说明行，技术工艺用回原句，报价按同一造型补上同一句，
   使两侧输入区底边处于同一视觉基线。

## 3. 交互契约

### 3.1 报价输入行（`确认需求解析结果.html`）
- `.chat-input-wrapper` 变为统一单行输入框：`display: flex`、`align-items: center`、
  `gap: 12px`、`min-height: 76px`、`border-radius: 24px`、白底 `var(--bg-page)`、
  1px 描边（`var(--border-color)`），`:focus-within` 时描边变主色。
- `#chatAttachBtn` 变成 50×50 的圆形按钮（`border-radius: 50%`），图标由 `ti-paperclip`
  改为 `ti-plus`；不得再出现 `ti-paperclip` 或 `oc-add-mark`。
- `#chatSend` 变成 54×54 的圆形主色按钮（`border-radius: 50%`，底色 `var(--color-primary)`），
  图标仍是 `ti-send`。
- `#chatInput` 变单行：`rows="1"`，`font-size: 15px`、`line-height: 24px`，
  去边框、透明底、`flex: 1`、`resize: none`（保留 `max-height` 限制）。
- 既有接线一律不动：`chatAttachBtn` 点击触发 `chatFileInput.click()`；`chatFileInput` 仍是
  `type="file" multiple hidden` 且 accept 列表不变；`#chatSend` 仍调 `sendFromInput()`；
  Enter 发送 / Shift+Enter 换行监听保持。
- 报价输入区的业务内容保持原样：`#quickActions` 四个快捷按钮与 `#attachChips` 附件条不删、
  不合并、不改行为。

### 3.2 技术工艺 ＋ 按钮（`tech-workbench.html` / `agent-chat.css`）
- `#ocChatAttachBtn` 只保留 `<i class="ti ti-plus">`，不得再包含 `ti-paperclip` 或
  `oc-add-mark` 节点。
- `agent-chat.css` 删除 `.oc-add .oc-add-mark` 规则，以及「契约要求保留这个字形」这条过时注释。
- 保护项不变：`#ocChatAttachBtn` 仍是 50×50 圆形 `.oc-add`、`aria-label="上传附件"`，
  点击仍然直接打开 `#ocChatFileInput`（不弹菜单、不做坐标计算）。

### 3.3 输入框下方说明行（两侧）
- 技术工艺：`.oc-composer > .oc-cinner` 内，在 `.oc-inputbox-single` 之后恢复
  `<div class="oc-disc">会话绑定当前项目；右侧工作台只承载业务步骤，不重复会话。</div>`。
- 报价：`.chat-input-area` 内，在 `.chat-input-wrapper` 之后补上同一句
  `<div class="oc-disc">会话绑定当前项目；右侧工作台只承载业务步骤，不重复会话。</div>`，
  并在该页 `<style>` 中给出与 `agent-chat.css` 同口径的 `.oc-disc` 规则。
- 说明行必须在正常文档流中（`margin-top: 9px`），不得用 `position: absolute/fixed` 或
  负外边距把它抽离布局；`font-size: 11px`、`text-align: center`、弱化文字色。

## 4. 取代的旧契约（本批明确反转）

以下旧断言与旧 Spec 条款由本批取代，必须随本批一起反转，不作为回归失败处理：

1. `tests/test_quote_tech_chat_composer_alignment_red.py::
   test_tech_composer_has_no_caption_below_the_input_box`
   —— 由「技术工艺 composer 不得出现 `.oc-disc` / `会话绑定当前项目`」改为
   「必须恢复该说明行」。
2. `tests/test_tech_direct_attachment_and_chat_capability_actions_red.py::
   test_composer_has_quote_style_attachment_button_and_hidden_multi_file_input`
   —— 删除对 `#ocChatAttachBtn` 内 `ti-paperclip` 的必须断言。
3. `docs/specs/quote-tech-chat-composer-model-removal-and-bottom-alignment.md` §4 中
   「`tech-workbench.html` 的统一 composer 内不再出现 `.oc-disc`」一条不再生效；
   该文档其余条款（框内不得有模型按钮、padding 统一 `10px 16px`、底部 flex 锚定）继续有效。

## 5. 非目标与保护边界

- 不恢复输入框内的模型选择按钮；模型/API Key 仍走全局设置入口。
- 不改消息渲染、流式输出、历史会话、附件上传接口、Agent 工具、九阶段流程与右侧业务看板。
- 不改报价输入区的 `#quickActions` 四个快捷按钮与 `#attachChips` 行为。
- 不改技术工艺工具栏里 `#ocFilesAction`「任务文件」按钮（它的回形针图标是另一件事）。
- 不新建第二套输入组件，不复用技术工艺的 `--oc-*` 变量名到报价页（报价页用自身 token）。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 6. 验收

- `python3 -m unittest tests.test_quote_tech_unified_composer_and_caption_red -v` 全绿。
- 反转后的 `tests.test_quote_tech_chat_composer_alignment_red` 与
  `tests.test_tech_direct_attachment_and_chat_capability_actions_red` 全绿。
- `node --check tech_app/frontend/agent-chat.js` 与 `node --check tech_app/frontend/tech-workbench.js` 通过。
- 浏览器并排打开报价与技术工艺工作台：输入框造型一致、＋ 上无回形针角标、
  输入框下方同一句说明行、两侧输入区底边对齐。

## 7. 对应测试

`tests/test_quote_tech_unified_composer_and_caption_red.py`
