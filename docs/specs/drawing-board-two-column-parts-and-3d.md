# 2.1 图纸解析右侧看板：零件清单 / 3D 视图固定两栏

状态：TDD Red，等待 DeepSeek 实现。

## 1. 产品目标

2.1 图纸解析页的右侧业务看板改为同时可见的两栏：左栏是零件清单，右栏是 3D 视图。
用户解析完成后无需先打开抽屉或把整块看板切成“零件清单”视图；可以一边浏览清单，一边
点击零件并立即在右栏查看对应 3D、零件信息和参数。

## 2. 桌面布局

- `.center-panel` 内新增语义明确的两栏容器 `.drawing-board-split`。
- 左栏 `.drawing-parts-column` 固定承载唯一一份 `#secParts` / `#tree`，标题为“零件清单”。
- 右栏 `.drawing-model-column` 承载现有 `.center-header`、`#modelPanes`、`#viewer`、
  `#partDetail`、`#parameterEditor` 和 `#analysisPanel`。
- 桌面宽度下使用 CSS Grid，两栏建议比例为 `minmax(260px, 34%) minmax(0, 1fr)`；左栏应有
  自己的纵向滚动，右栏不能被长零件名或参数表撑出看板。
- 两栏同高、边界清晰；3D canvas 必须在右栏尺寸变化后正确 resize，不能拉伸、裁切或覆盖标题。

## 3. 唯一 DOM 与既有能力

- 全页只能有一个 `id="secParts"` 和一个 `id="tree"`，必须移动/复用现有节点，禁止复制一份清单。
- `#secParts` 不再属于 `#ocDrawerBody`，也不再带 `data-drawer-section`；抽屉仍保留上传、解析视图、
  待澄清问题、核验、校正、3D 导入和版本审查。
- `renderTree()`、`selectPart()`、`togglePartSubActions()`、批量工艺推荐和现有事件监听继续复用原
  `#tree`，不得新增第二套清单状态或 API。
- 点击某零件后：左栏保持可见并高亮唯一选中项，右栏切回模型模式并加载该零件 3D/详情。
- 点击工艺推荐时，左栏仍保持可见，右栏可从 3D 切到现有 `#analysisPanel`；返回后回到右栏 3D。

## 4. 看板视图兼容

- `navigate-view: parts` / `parts-list` 不再把 `#secParts` 搬到覆盖式 `#boardViewHost`；它只负责
  聚焦/滚动到左栏并保持两栏可见，返回成功结果 `view: "parts-list"`。
- `drawing-overview` 恢复两栏总览，不隐藏零件清单。
- `part-detail` 继续复用 `selectPart(part)`，但不能隐藏左栏或要求“返回零件清单”才能重新选零件。
- `part-process` / `part-cost` 继续只替换右栏内容，不在父壳创建 Drawer/Modal。
- 保留 `TechBoardRuntime.registerViews`、既有视图名和 `result-summary`，不得改变父壳协议。

## 5. 空态、加载和错误

- 未解析：左栏显示现有“完成解析后显示零件清单”，右栏显示现有 3D 选择提示。
- 已解析但尚未选择：清单可滚动，右栏提示“选择零件后查看”。
- 3D 不可用：错误只出现在右栏；左栏、解析、参数编辑和导出能力仍可用。
- 解析或重新检索刷新清单时，不得销毁当前 3D canvas；当前零件仍存在则保持选择，不存在才清空。

## 6. 响应式与可访问性

- `> 900px`：两栏并排。
- `<= 900px`：改为单列，零件清单在上、3D 在下；不能横向溢出。
- 两栏分别具有可访问名称“零件清单”和“3D 视图”。
- 键盘聚焦 `navigate-view: parts` 时应落到清单区域；不能只用颜色表达选中状态。

## 7. 禁止事项

- 不复制 `#tree`、不新建第二份零件数据、不新增后端路由或算法。
- 不删除零件详情、参数编辑、3D/2D、版本、工艺推荐、批量工艺推荐和更多功能。
- 不把清单搬到父壳、聊天消息、Drawer 或 Modal。
- 不修改三栏父壳宽度；本需求只改变 2.1 iframe 内右侧看板结构。
- 不以固定像素高度破坏嵌入态滚动，不让清单滚动带动 3D 标题消失。

## 8. 验收

1. 桌面 2.1 右侧同时看到零件清单与 3D 视图，左清单、右 3D。
2. `#secParts` / `#tree` 各只有一个，且不在抽屉中。
3. 点击不同零件时左栏不消失，右栏更新 3D、零件信息和参数。
4. `parts`、`drawing-overview`、`part-detail`、`part-process` 视图链和父壳桥继续工作。
5. 工艺推荐只替换右栏，返回后恢复对应零件 3D。
6. 900px 以下按清单在上、3D 在下堆叠。
7. 3D 降级不影响左栏清单及其它业务能力。

