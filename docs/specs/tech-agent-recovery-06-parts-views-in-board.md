# 技术工艺 Agent 能力恢复第 6 步：零件清单与零件详情看板化 Spec

## 范围

本步只做 **2.1 右侧看板内部** 的零件层级视图状态机，不接 Agent 工具（第 7 步），
不新增后端接口，不改 1.1–3.3 的其它阶段。

视图链：

```
drawing-overview → parts-list → part-detail → part-process
                                          └→ part-cost
```

## 1. 硬性边界

- 所有零件业务视图都渲染在右侧 iframe 看板（`index.html` + `app.js`）内部；
- 父壳 `tech-workbench.html` / `tech-workbench.js` **不得**出现零件详情、3D/2D、参数、
  工艺、成本、任务文件的 DOM、Drawer、Modal 或覆盖整个统一工作台的面板；
- 返回只改变右侧看板视图，不关闭父壳、不覆盖左侧会话、不重载页面、不丢会话草稿；
- 不新建第二套零件渲染或数据源，必须复用既有 `selectPart` / `#partDetail` /
  `#parameterEditor` / `#analysisPanel` / `#analysisHost` / `CadInlineAnalysis.open` /
  `/versions`。

## 2. 看板视图注册

在 `app.js` 既有的 `TechBoardRuntime.registerViews({...})` 中**追加**五个视图，每个都带真实
`run`：

- `drawing-overview`：3D/2D/参数总览（`#modelPanes` 默认态）；
- `parts-list`：零件清单（复用既有 `#secParts` / `#tree`）；
- `part-detail`：单个零件详情；
- `part-process`：该零件工艺推荐；
- `part-cost`：该零件成本测算。

第 5 步已经注册的 `parts` / `questions` / `report` / `evidence` / `review` / `files` /
`upload` / `import3d` 必须原样保留（左侧入口名=视图名的契约不变）；`parts` 进入后即
`parts-list`。

## 3. 看板内部视图状态机

`app.js` 暴露 `window.TechBoardPartViews`：

- `show(name, payload)`：切到目标视图，带 `partId` 时先解析出零件；
- `back()`：回到上一层，不看浏览器历史；
- `current()`：返回当前视图名。

父/子视图映射用常量 `PART_VIEW_PARENT` 表达，至少包含：

```
"part-cost":    "part-detail"
"part-process": "part-detail"
"part-detail":  "parts-list"
"parts-list":   "drawing-overview"
```

每次切换视图都必须调用 `TechBoardRuntime.setView(name)` 上报当前视图，父壳据此高亮左侧入口。

## 4. 进入与返回

- `selectPart(part)` 必须进入 `part-detail`（当前实现只切 `setRightPane("model")`）；
- 零件子动作「工艺推荐」「成本测算」必须进入 `part-process` / `part-cost`，映射用常量
  `PART_VIEW_FOR = { process: "part-process", cost: "part-cost" }`；
- 详情与工艺/成本视图内的返回按钮文案固定为 `返回零件详情`、`返回零件清单`，点击只调用
  `TechBoardPartViews.back()`；看板不得向父壳发导航消息，也不得 `window.parent.postMessage`。

## 5. 零件详情内容

`part-detail` 复用既有渲染，必须能展示：

- 3D 几何（既有 `#viewer` + STL）；
- 2D 工程图与下载（既有 `drawingsFor` / `mediaUrl`）；
- 识别依据（既有 `part.role` / `part.model_lookup_evidence` / `part.confidence`，不新增后端字段）；
- 参数（既有 `#parameterEditor`）、材料（`part.material`）、数量（`part.quantity`）；
- 版本（复用既有 `/api/projects/{id}/versions` 与既有渲染）。

## 6. 验收标准

1. 第 5 步契约不回归：左侧入口视图仍全部注册；
2. 五个新视图都注册且带真实 `run`；
3. 点击「零件清单」→ 右侧 `parts-list`；点击零件 → `part-detail`；
4. 详情内点击「工艺推荐」/「成本测算」→ `part-process` / `part-cost`，仍在看板内；
5. 返回按钮只改右侧视图，逐步回到 `part-detail` / `parts-list`；
6. 父壳里没有任何零件详情/3D/工艺/成本 DOM、Drawer、Modal；
7. 每次切换都上报 `TechBoardRuntime.setView`；
8. 不新增后端接口、不改既有接口与数据结构。

## 7. 对应测试

`tests/test_tech_parts_views_inside_board_red.py`
