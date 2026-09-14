# 技术工艺父壳：统一标题行高度、移除嵌入页空状态行、整理 2.1 结果按钮

状态：TDD Red，等待 DeepSeek 实现。

## 1. 目标

统一父壳五个大流程的业务卡标题行必须等高。2.1 显示“图纸解析”和当前项目状态，例如：

`已打开项目 42058acfab83（补充说明✓，佐证文件 0 个）`

状态已经进入父壳标题行后，九个 iframe 页面原来的 title/status 容器在嵌入态必须整体退出布局，
不能只隐藏文字后留下一条空白。2.1 零件清单常驻右侧两栏后，左侧“零件清单”按钮删除；“待澄清
问题 0”和“解析报告”迁入当前步骤按钮区。

## 2. 统一标题行

- `#techContextHeader` 是五个大流程唯一标题行，所有阶段共用同一个最小高度、padding 和垂直居中。
- 2.1 左侧标题固定为“图纸解析”；`#techContextNotice` 同行显示看板 `board-status` 原文。
- 有无子页签、有无状态、有无权限错误都不能改变标题行高度。
- 状态过长时单行省略，不能换行撑高；错误仍使用现有红色状态样式。
- 不写死示例项目 ID，项目号、补充说明和佐证文件数量继续来自 iframe 的真实 `board-status`。

## 3. 删除嵌入态空状态行

- `tech-embed.js` 在 `.tech-embed` 下整体隐藏阶段页 `.title-section`，使其不参与布局。
- 不再采用“只隐藏 `.form-title` / `.status-badge`，保留空 `.title-section`”的方式。
- 独立打开阶段页时仍显示自己的标题与状态；只影响 `embed=1`。
- 九阶段通用，不能只针对 `index.html` 写特例。
- `board-status` 发布与父壳接收必须保留，状态仍进入 `#techContextNotice`。

## 4. 2.1 结果按钮归位

- 删除 `#ocPartsAction` 和 `#ocPartsCount`；零件清单已经常驻右侧左栏，不再需要入口按钮。
- 删除会话线程中的 `#ocResultActions` 结果条，不能留下空容器占据消息流。
- 保留 `#ocQuestionsAction`、`#ocQuestionsCount` 和 `#ocReportAction`，把它们放入
  `#techChatActions`，与任务文件、解析视图和当前步骤业务按钮同一区域。
- “待澄清问题”即使数量为 0 也显示 `0`；是否可点击继续由真实 `result-summary` 决定。
- 两个按钮只在 drawing 阶段按现有摘要规则显示/启用；其它阶段不显示，不能残留上个阶段状态。
- 点击仍分别发送 `navigate-view: questions` 和 `navigate-view: report`，内容只在右侧看板打开。
- `result-summary.parts` 数据继续保留给其它状态/统计使用，只删除按钮，不改桥协议。

## 5. 兼容与禁止事项

- 更新 `agent-chat.js` 的节点查找、按钮映射、摘要刷新、active 状态和 reset 逻辑，不能访问已删除
  的 `ocResultActions/ocPartsAction` 后报错。
- 独立 2.1 页原有 `#btnReport` 和本页结果入口不属于统一父壳按钮，继续保留。
- 不删除待澄清问题、解析报告或零件清单业务视图，不新增 API，不读取 iframe DOM。
- 不把项目状态写成静态文本，不删除正常/错误状态通知，不改变权限。
- 不用 `visibility:hidden`、透明文字、零高度字体伪装删除空行。

## 6. 验收

1. 五个大流程的 `#techContextHeader` 高度一致，2.1 不再比其它页面窄。
2. 2.1 标题同行显示“图纸解析”和真实“已打开项目 …”状态。
3. 九阶段 iframe 嵌入态没有原 title/status 留下的空白行；独立页面不受影响。
4. 左侧不存在“零件清单”按钮和会话结果条。
5. “待澄清问题 0”“解析报告”位于 `#techChatActions`，接线与计数正常。
6. 切换阶段不会残留 2.1 按钮或上一个阶段状态。

