# 技术工艺 Agent 会话输入框贴底

状态：已实现，等待验收。

## 1. 问题

技术工艺统一工作台的输入框虽然由 flex 固定在会话列底部，但输入框下方仍有 `.oc-disc`
说明行、9px 上间距和 composer 的 10px 底部 padding，导致输入框视觉下沿距离会话列底部
约 35px，看起来没有贴底。

## 2. 最新产品决策

仅技术工艺统一工作台改为输入框贴底：

- 删除 `.oc-composer` 内输入框下方的 `.oc-disc` 说明行；
- `#techChatPane .oc-composer` 使用 `padding: 10px 16px 0`，顶部和左右留白不变，底部为 0；
- 输入框继续位于会话列 flex 布局的最后一个固定区域；
- 报价 Agent 的说明行和布局保持不变。

本 Spec 取代 `quote-tech-unified-composer-and-binding-caption.md` 中“技术工艺必须恢复说明行”
以及“两侧输入区底边用相同说明行对齐”的条款；该旧 Spec 关于报价输入框、附件、发送和
模型入口的其余条款继续有效。

## 3. 保留边界

- 不改变 `.oc-inputbox-single` 的 76px 最小高度、24px 圆角和左右内部布局；
- 保留 `#ocChatAttachBtn`、`#ocChatFileInput`、`#ocInput`、`#ocSend` 及原事件接线；
- 保留会话消息滚动区 `flex: 1 1 auto; min-height: 0`；
- 不改变左侧业务操作栏、历史、任务进度、九阶段、右侧看板和后端接口；
- 不删除通用 `.oc-disc` CSS，因为独立页面及报价侧仍可能使用它；
- 不修改报价页面。

## 4. 验收标准

1. 技术统一工作台 composer 内不再出现 `.oc-disc` 或“会话绑定当前项目”文案。
2. `#techChatPane .oc-composer` 明确为 `padding: 10px 16px 0`。
3. 技术输入框仍是 composer 中最后的可见内容，且 composer 仍为 `flex: 0 0 auto`。
4. 输入、附件和发送控件及其行为不变。
5. 报价输入区的说明行不受影响。
