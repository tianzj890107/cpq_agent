# 技术工艺会话输入框紧凑按钮与按行自增高

状态：已实现，等待验收。

## 1. 目标

技术工艺统一工作台的会话输入框改为紧凑的单行初始形态：

- 附件按钮外框由 50×50 缩到约 2/3，即 34×34；
- 发送按钮外框由 54×54 缩到 36×36；
- 两个按钮内部图标保持现有 18px（加号）和 22px（发送）不变；
- textarea 字号与上方操作按钮一致，为 12px，行高 20px；
- 外框不再固定 `min-height: 76px`，空输入时只呈现一行高度；
- 输入换行后根据 `scrollHeight` 自动增高，最高仍为 120px，超过后 textarea 内部滚动；
- 发送并清空后恢复单行高度。

## 2. 范围

仅修改技术工艺的 `agent-chat.css` 和既有 `autoSize()` 行为；报价输入框保持现状。本 Spec
取代旧统一输入框 Spec 中“报价与技术按钮尺寸、输入字号、外框最小高度必须相同”的技术侧
条款，不改变报价侧自身契约。

## 3. 保留边界

- `#ocChatAttachBtn`、`#ocChatFileInput`、`#ocInput`、`#ocSend` DOM 与无障碍标签不变；
- 附件直传、点击发送、Enter 发送、Shift+Enter 换行和输入法保护不变；
- 最大高度仍为 120px；
- 输入框仍贴底，左右与顶部 composer 间距不变；
- 不改消息、历史、任务、左侧业务动作、右侧看板或后端。

## 4. 验收

1. `.oc-add` 为 34×34，flex basis 34px，图标仍为 18px。
2. `.oc-inputbox-single .oc-send` 为 36×36，flex basis 36px，图标仍为 22px。
3. `.oc-inputbox-single textarea` 为 12px / 20px。
4. `.oc-inputbox-single` 无 76px 固定最小高度，采用内容驱动高度。
5. `autoSize()` 在 input、初始化和发送清空后生效，120px 封顶并切换 overflow-y。
