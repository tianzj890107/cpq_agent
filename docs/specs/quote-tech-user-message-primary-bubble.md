# 报价与技术工艺 Agent 用户消息主色气泡统一 Spec

状态：TDD Red，等待 DeepSeek 实现。

## 背景

当前品牌规范已经把系统主色定为 `#0060E6`，但仍允许品牌蓝渐变。报价 Agent 的用户消息使用
`--gradient-ai`，技术工艺 Agent 的用户消息 `.oc-ubub` 则仍是浅灰底深色字。用户在两个 Agent
之间切换时，会直接感知为两套聊天产品。

本批只统一“用户本人发送的消息气泡”，不对 Logo、主按钮、步骤节点等其它渐变做全局机械替换。

## 产品契约

报价 Agent 与技术工艺 Agent 的用户消息必须具有同一视觉语义：

- 背景使用当前系统主色 token，对应 `#0060E6`；
- 必须是实心主色，不得使用 `linear-gradient`、`radial-gradient` 或图片渐变；
- 消息正文使用白色，确保蓝底上的识别度；
- 消息位于会话右侧；
- 气泡保持圆角，并以较小的右下角圆角表达“用户消息尾部”；
- Markdown、换行、长文本折行和消息内容不得改变；
- hover、选中或发送状态不得把普通用户消息改成另一套品牌蓝。

## 系统主色引用规则

- 报价侧 `.message-user` 必须通过 `var(--color-primary)` 使用系统主色，不得直接写十六进制色值；
- 技术侧 `.oc-ubub` 必须引用同一个系统主色语义 token；允许 `--oc-accent` 作为兼容别名，但
  `--oc-accent` 必须解析到 `var(--color-primary)`，不得独立写死另一份蓝色；
- 技术工作台必须在主题入口提供 `--color-primary: #0060E6`，不能依赖未定义变量后静默退回灰色；
- 不新增 `--quote-user-color`、`--tech-user-color` 等组件私有颜色源。

## 范围

允许 DeepSeek 修改：

- `确认需求解析结果.html` 中用户消息相关 CSS；
- `tech_app/frontend/agent-chat.css` 中主题 token 与 `.oc-ubub`；
- `tech_app/frontend/tech-workbench.css` 中必要的系统主题 token；
- 必要的前端静态资源版本查询参数；
- 当周 changelog。

## 禁止事项

- 不修改 AI 消息、工具卡、Logo、主按钮和步骤节点的既有形态；
- 不全局删除所有渐变；
- 不把成功绿、警告橙、错误红改为主色；
- 不修改消息 DOM、发送接口、流式响应、会话持久化或后端逻辑；
- 不修改本 Spec 和 Red 测试以迁就实现；
- 不执行 push、MR、merge、tag、Release、部署或服务重启。

## 验收标准

1. 报价和技术工艺用户消息均为右对齐的 `#0060E6` 实心蓝色气泡、白色文字。
2. 两侧用户消息都从系统主色 token 取色，组件规则内没有硬编码品牌色。
3. 两侧气泡都有圆角和更小的右下角圆角。
4. 报价侧用户消息不再引用 `--gradient-ai`；技术侧不再使用浅灰用户气泡。
5. 其它组件渐变、语义色、消息行为和业务接口保持不变。
6. 新增 Red 测试转绿，既有品牌色及相关前端测试保持通过。

## 对应测试

`tests/test_quote_tech_user_message_primary_bubble_red.py`
