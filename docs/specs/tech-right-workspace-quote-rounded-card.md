# 技术右侧工作区统一为报价圆角卡片 Spec

状态：TDD Red，等待 DeepSeek 实现。

**部分被覆盖（9-16）**：§2「报价式卡片视觉」的外边距 / 边框 / 圆角，以及 §4「响应式」的
12px / 8px 非零外间距，已被用户最新决策反转 —— 见
`docs/specs/tech-quote-workspace-flush-and-compact-stage-title-row.md`（结果区铺满右侧工作区）。
本 Spec 的 §1 / §3（同层包装、内部顺序、分隔线、iframe 满高）继续有效。

## 决策覆盖

本 Spec 以用户最新决策为准，覆盖早期“技术右侧工作区完全贴边、无外围卡片”的视觉约束。
三列父壳仍然贴合，但技术右侧的**业务结果区域**改为报价 Agent `.results-area` 的圆角卡片形态。

## 1. 信息层级

右侧从上到下保持：

1. 当前项目/流程/模型标题栏；
2. 大流程进度条；
3. 16px 留白中的业务圆角卡片 `#techResultsArea`。

业务圆角卡片内部从上到下固定为：

1. `#techContextHeader` 当前步骤标题与子步骤；
2. `#techWorkspaceOutlet` 当前步骤 iframe/加载/错误内容；
3. `.tech-workbench-bottom` 上一步、本步操作和下一步。

标题、内容和底栏必须属于同一张卡，不能各自成为三张悬浮卡。

## 2. 报价式卡片视觉

`#techResultsArea.tech-results-area`：

- `margin:16px`；
- `border:.5px solid` 统一边框 token；
- `border-radius:12px`；
- 白色系统表面；
- `overflow:hidden`，确保 iframe 和底栏不突破圆角；
- `display:flex; flex-direction:column; flex:1; min-height:0`；
- 不增加悬浮阴影，报价结果卡本身也依靠边框而非浮层阴影。

`.tech-workspace-pane` 继续作为右侧列容器：不设置 margin、圆角或阴影，避免整列和业务内容双重圆角。

## 3. 内容与底栏

- `#techWorkspaceOutlet` 继续占满卡片剩余空间；
- iframe 继续 `width/height:100%`、无边框；
- `#techContextHeader` 只保留下边分隔线，不再承担外层卡片圆角；
- `.tech-workbench-bottom` 必须在卡片内部并只保留上边分隔线；
- 下一步仍为最右唯一按钮，现有动作代理、busy/disabled和stage切换不变；
- loading、错误、空态都在圆角卡片内部呈现。

## 4. 响应式

- 桌面外间距16px；
- 中等屏可以12px；
- 窄屏可以8px，但不能恢复完全贴边，也不能取消12px圆角；
- 不改变56px导航、会话列和右侧列的所有权关系。

## 禁止事项

- 不给整个 `.tech-workspace-pane` 增加外层圆角；
- 不修改 iframe 子业务页面以伪造父卡片；
- 不给每个子步骤再套一张卡；
- 不删除项目标题、进度条、上下文标题或底栏；
- 不改变九阶段URL、iframe、postMessage、动作注册和数据接口；
- 不恢复跨整个页面宽度的全局footer；
- 不修改本Spec/Red测试；
- 不提交、推送、MR、merge、tag、Release、部署或重启服务。

## 验收标准

1. 技术右侧项目标题和进度条下方出现与报价一致的圆角业务卡片。
2. 桌面卡片16px外间距、12px圆角、0.5px边框、白底、无阴影。
3. 当前步骤标题、iframe业务内容和底部操作栏同属一张卡。
4. 内容和iframe正确裁切于圆角内，不出现双边框/双滚动。
5. 中窄屏仍保留合理间距和圆角。
6. 九阶段导航、会话、看板桥、底栏动作和业务能力不回归。

## 对应测试

`tests/test_tech_right_workspace_quote_rounded_card_red.py`
