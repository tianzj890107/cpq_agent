# Spec：零件清单里去掉「一键生成全部工艺推荐」按钮

## 背景（用户实测）

2.1 图纸解析的零件清单（右侧看板固定左栏）里还有一颗蓝色批量按钮：

- `app.js:2474` `renderPartsBoardToolbar()` 往 `.drawing-parts-column`（退回 `#secParts`）里
  追加 `#partsBulkBar`，按钮文案「一键生成全部工艺推荐」，调用 `startAllPartProcesses()`；
  调用点两处：`app.js:496`（首屏渲染后）、`app.js:1561`（每次重新渲染零件清单后）。
- `agent-chat.css:614-615` 配套两条样式 `.board-parts-toolbar` / `.board-parts-bulk`。

用户要求：**零件清单那里不要这个按钮。**

口径与既有「右侧看板业务按钮统一到左侧会话操作栏」批次一致：同一能力只在左侧会话操作栏
保留唯一入口，不在看板里重复；看板里的重复按钮删掉，能力与后端接口一个都不能少。

## 范围

- 只改「2.1 零件清单里的按钮呈现」这一层：`tech_app/frontend/app.js`、
  `tech_app/frontend/agent-chat.css`。
- 不动：`runAllPartProcesses` 看板动作注册（左侧操作栏与 Agent 分派的唯一入口）、
  `startAllPartProcesses()` 的批量实现与逐件串行 / 跳过已有工艺 / 失败继续 / 进度上报、
  单零件行上的「工艺推荐」入口、后端 `/api/projects/{id}/parts/{part_id}/process`、
  看板桥协议与视图注册。

## R1 零件清单不再有批量按钮

- R1.1 `app.js` 不再创建 `#partsBulkBar`，不再有 `renderPartsBoardToolbar()` /
  `partsBoardToolbarHost()` 及其调用点（首屏与重渲染两处都要清干净）。
- R1.2 不留「只有样式没有节点」或「只有节点没有样式」的半成品：`agent-chat.css` 里
  `.board-parts-toolbar` / `.board-parts-bulk` 两条规则一并删除。
- R1.3 2.1 页静态 HTML 里本来就没有该节点，不得为了「先占位」再把 `#partsBulkBar` 写进 HTML。

## R2 能力不缩水（本批最重要的约束）

- R2.1 `runAllPartProcesses` 动作注册保留：`label` = 「一键生成全部工艺推荐」、
  `deferred: true`、`role` 字段、`getState()` 的 visible/enabled/busy 语义不变 ——
  左侧会话操作栏仍按看板动作快照渲染出这颗按钮，Agent 也仍能按名字分派。
- R2.2 `startAllPartProcesses()` 与 `app.js` 里对 `/parts/${id}/process` 的调用不变。
- R2.3 单零件「工艺推荐」入口不变（`data.partAnalysis = mode` 与 `openPartAnalysis(part, "process")`）。
- R2.4 后端路由不变：仍只有 `/api/projects/{project_id}/parts/{part_id}/process`，
  没有新增 process-all / bulk 接口。

## 验收

- 红测：`tests/test_tech_parts_list_drop_bulk_process_button_red.py`（先红后绿）。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败。
- 浏览器：2.1 图纸解析页零件清单里不再出现蓝色批量按钮；左侧会话操作栏里那颗
  「一键生成全部工艺推荐」照旧存在且可点，点击后进度卡与逐件明细照旧。
