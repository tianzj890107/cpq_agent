# 技术工艺历史记录统一为报价 Drawer Spec

状态：Spec + 红测（已实现）
红测：`tests/test_tech_history_quote_drawer_parity_red.py`

## 决策

技术工艺 Agent 的历史记录采用报价 Agent `#historyDrawer` 的同一产品形态：左侧 340px 抽屉、
半透明全屏遮罩、顶部标题与关闭按钮、第二行新建/刷新操作、下方历史卡片列表。技术侧仍读取
技术项目并按真实 workflow 恢复 stage，不改成报价历史数据。

## 1. 固定结构

技术工作台 HTML 必须静态存在以下结构，不在首次点击时临时拼 `innerHTML`：

- `#techHistoryOverlay.overlay`；
- `#techHistoryDrawer.drawer.tech-history-drawer`；
- `.drawer-header`：标题“技术项目历史”及 30×30 关闭图标按钮；
- `.drawer-actions`：蓝色 primary“新建技术项目”和刷新图标按钮；
- `#techHistoryList.drawer-body`：加载、空态、错误态与历史卡片。

抽屉使用 `role="dialog"`、`aria-modal="true"`、可访问标题关联；关闭/刷新按钮必须有
`aria-label`，不能只依赖图标或 `title`。

## 2. 与报价一致的视觉

- 遮罩：`rgba(15,16,25,.35)`，通过 opacity/visibility 过渡；
- Drawer：左侧固定、`width:340px`、`height:100vh`、白底、右侧 0.5px 分隔线；
- 关闭态：`transform:translateX(-104%)`；打开态 `.show`：`translateX(0)`；
- 阴影：报价同级 `4px 0 24px rgba(15,16,25,.12)`；
- 标题栏 `14px 16px`；操作栏 `10px 16px`；列表 `10px 12px`；
- 历史卡：白底、0.5px 边框、10px 圆角、`10px 12px`，标题单行截断、元信息/阶段 chip；
- 当前项目卡使用系统主色边框和浅蓝底；
- 主按钮使用报价式系统蓝色 primary 风格。

窄屏允许 `max-width:88vw`，但桌面宽度仍为340px。

## 3. 交互

- 点击左栏“历史记录”给 overlay 和 drawer 同时加 `.show`，立即开始加载；
- 点击遮罩、关闭按钮或按 Escape 关闭；
- 关闭后焦点回到 `#techHistory`；
- 刷新按钮只重新调用现有 `loadTechHistory()`，不关闭抽屉；
- “新建技术项目”关闭抽屉并进入统一技术主页 `/报价首页.html?assistant=tech`，不调用删除/重置接口；
- 打开时焦点进入关闭按钮或抽屉内第一个操作按钮；
- 不再使用 `hidden + setTimeout` 控制动画，也不动态创建第二份抽屉 DOM。

## 4. 技术历史数据保持

- 继续使用既有 `GET /api/projects`；
- 点击历史项目继续读取 `/workflow` 与项目详情；
- 继续通过 `techStageFromProject()` 和 `applyStage()` 在同一工作台恢复正确 stage；
- 卡片显示项目名称/编号、日期和当前阶段；当前项目增加 `.current`/`aria-current`；
- 不添加没有后端契约的删除能力，不删除、重置或迁移历史项目。

## 禁止事项

- 不把技术历史改成报价历史；
- 不新增项目历史接口或第二套存储；
- 不删除历史项目；
- 不用刷新整个页面代替 `applyStage` 恢复；
- 不复制第二份 Drawer；
- 不修改会话、任务、附件和当前项目数据；
- 不修改本 Spec/Red 测试；
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 验收标准

1. 两个 Agent 历史入口打开相同的340px左滑 Drawer形态。
2. 技术 Drawer 有标题、关闭、新建技术项目、刷新和历史列表。
3. 遮罩、动画、尺寸、间距、按钮和历史卡片与报价侧一致。
4. 遮罩/关闭/Escape均可关闭，焦点可进入并返回历史入口。
5. 刷新留在 Drawer；新建进入统一技术主页。
6. 点击技术历史仍按真实项目状态恢复正确 stage。
7. 不新增删除能力，不改变任何历史数据。

## 对应测试

`tests/test_tech_history_quote_drawer_parity_red.py`
